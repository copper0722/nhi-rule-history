"""PostgreSQL store for the website's git-diff cards (schema ``nhi_rule_history_diff_card``, v29).

The announced layer keeps only the revised column of a notice's 修訂對照表, so the original
column is not in PostgreSQL.  ``load-pg`` reads every registered notice bundle, takes the
notice's comparison-table ODT, builds the blocks the card generator renders
(``sources_nhi.read_table`` + ``build_blocks``: no editorial text, no exclusions), checks each
block against the replay invariant and stores it once per (ODT SHA-256, generator version).  The
rows are then read back through a fresh connection and the hash is recomputed from what
PostgreSQL returns.  No model is involved.

Pure part: ``build_comparison`` (ODT bytes -> blocks and ``comparison_sha256``), ``check_replay``.
I/O part: ``run`` (store, read back) and ``load_pg`` (the CLI entry used by ``build.py load-pg``).

Row object (one per diff row) and block object (the columns of
``v_public_comparison_block`` after the four comparison-level ones) are documented in
``docs/diff-cards.md``.  ``comparison_sha256`` is the SHA-256 of the canonical JSON (sorted keys,
compact separators, UTF-8, one trailing newline) of the list of block objects in block order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nhi_rule_history.announced_notice import (
    ODT_MEDIA_TYPE,
    NoticeAttachment,
    NoticeBundle,
    _comparison_tables,
    clause_heading_code,
    read_notice_bundle,
    read_odt_document,
    stable_uuid,
)
from nhi_rule_history.announced_release import (
    QueuedBundle,
    describe_error,
    registered_bundles,
)
from nhi_rule_history.pg.common import json_text, object_fingerprint

from .core import Block, Row, para_range, verify_block
from .sources_nhi import build_blocks, clause_rows, read_table

GENERATOR_VERSION = "nhi-rule-history/diff-cards-pg/1.0.0"
RECEIPT_SCHEMA = "nhi-rule-history/diff-card-load-receipt/v1"
STORE_SCHEMA = "nhi_rule_history_diff_card"
DSN_ENV = "NHI_RULE_HISTORY_DSN"
CORPUS_ROOT_ENV = "NHI_RULE_HISTORY_CORPUS_ROOT"
LOCK_KEY = "nhi-rule-history-diff-card-load"
PARAGRAPH_NUMBER = re.compile(r"[1-9][0-9]*")


class CardError(ValueError):
    """A notice that cannot become a card: reported for that bundle, never aborts the run."""


class StoreError(RuntimeError):
    """The store or its connection is unusable: an infrastructure error that aborts the run."""


# ---------------------------------------------------------------------------
# Pure part: ODT bytes -> blocks


def comparison_attachment(bundle: NoticeBundle) -> tuple[NoticeAttachment, bytes]:
    """The ODT attachment the announced overlay parses for this notice, with its bytes.

    This is the candidate loop of ``announced_notice.parse_notice``: among the ODT attachments,
    the one whose tables include a 修訂後／原 comparison table, and it must be the only one.  Its
    SHA-256 is what the overlay records as ``source_artifact_sha256``.  Only the selection is
    shared: a notice the overlay cannot parse in full (for example because its raw.md receipts
    differ) still has a comparison table, and the card is built from the ODT alone.
    """

    candidates: list[tuple[NoticeAttachment, bytes]] = []
    for attachment in bundle.attachments:
        if attachment.media_type != ODT_MEDIA_TYPE:
            continue
        payload = attachment.path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != attachment.sha256:
            raise CardError("ODT bytes changed after verification")
        document = read_odt_document(payload, artifact_sha256=attachment.sha256)
        if _comparison_tables(document)[0]:
            candidates.append((attachment, payload))
    if not candidates:
        raise CardError("notice has no official comparison table")
    if len(candidates) != 1:
        raise CardError("comparison tables span several ODT attachments")
    return candidates[0]


def further_clause_code(row: Mapping[str, Any]) -> str | None:
    """The clause code of a row of a further 給付規定 table, or None when the row is not a clause row.

    A row is a clause row when its first revised paragraph starts with a clause heading (the
    announced overlay's grammar).  When the revised cell reads 無, the clause is deleted and the
    first original paragraph decides.
    """

    revised, original = row["new"], row["old"]
    if revised == ["無"]:
        first = original[0] if original and original != ["無"] else ""
    else:
        first = revised[0] if revised else ""
    return clause_heading_code(first) if first else None


def deleted_clause_block(row: Mapping[str, Any], code: str) -> Block:
    """The block of a clause row whose revised cell reads 無, built like ``build_blocks`` builds a clause."""

    old, new = list(row["old"]), list(row["new"])
    rows = clause_rows(old, new)
    block = Block(
        path=f"藥品給付規定/{code}", badge="修訂", badge_kind="m", fn="", rows=rows,
        adds=sum(1 for r in rows if r.kind == "add"), dels=sum(1 for r in rows if r.kind == "del"),
        hunk_old=para_range(list(range(1, len(old) + 1)), "段"),
        hunk_new=para_range(list(range(1, len(new) + 1)), "段"), clause_id=code,
    )
    verify_block(block, old, new)
    return block


def clause_paragraphs(cell: Sequence[str]) -> list[str]:
    """A cell's paragraphs; 無 in the original column means the clause is new, so there are none."""

    return [] if list(cell) == ["無"] else list(cell)


def plan_blocks(table: Mapping[str, Any]) -> list[tuple[str, Block, list[str], list[str]]]:
    """(block kind, card block, old paragraphs, new paragraphs) in block order.

    ``build_blocks`` turns the rows of the first table into clause blocks and every row of every
    further comparison table into an attachment block.  Here a row of a further 給付規定 table that
    starts with a clause heading (the 2.6.2 and 2.6.3 rows of the 2.6.1 notice) is a clause block as
    well, built exactly like a row of the first table, and the remaining rows stay attachment blocks.
    Clause blocks come first (first table, then the further tables in order), attachment blocks after.
    ``sources_nhi`` is untouched, so the card publications render as before.
    """

    plain = build_blocks(dict(table), {}, ())
    first_table = table["clauses"]
    further = [(extra["kind"], row) for extra in table.get("extra_comparisons", []) for row in extra["rows"]]
    if len(plain) != len(first_table) + len(further):
        raise CardError("card blocks and source rows differ in number")
    clauses = [("clause", block, clause_paragraphs(row["old"]), list(row["new"]))
               for block, row in zip(plain[:len(first_table)], first_table, strict=True)]
    attachments = []
    for block, (kind, row) in zip(plain[len(first_table):], further, strict=True):
        code = further_clause_code(row) if kind == "給付" else None
        if code is None:
            attachments.append(("attachment", block, list(row["old"]), list(row["new"])))
            continue
        if row["new"] == ["無"]:
            clause = deleted_clause_block(row, code)
        else:
            (clause,) = build_blocks({"clauses": [row], "extra_comparisons": []}, {}, ())
        clauses.append(("clause", clause, clause_paragraphs(row["old"]), list(row["new"])))
    return clauses + attachments


def clause_codes_of(clause_id: str, old: Sequence[str], new: Sequence[str]) -> list[str]:
    """The clause codes a clause block serves: its own id, then every clause heading in its row.

    A heading is a paragraph that starts with a clause designation, as the announced overlay's
    grammar reads it (NFKC, strict stop).  The announced layer splits a row at its headings into one
    patch per code, so the block of the row serves each of them: a heading nested under the row's
    clause (the row of 9.5 holds 9.5.1) and a sibling clause that shares the row (a 9.2 row that
    also holds 9.26 and 9.69) alike.  Headings come from the revised paragraphs in column order,
    then those only the original paragraphs have (a sub-clause this amendment deletes); each code
    appears once.
    """

    codes = [clause_id]
    for text in (*new, *old):
        code = clause_heading_code(text)
        if code and code not in codes:
            codes.append(code)
    return codes


def segments(text: str, spans: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    """Text pieces whose concatenation is ``text``; ``mark`` is true on the changed spans.

    Pieces replace character offsets, so no consumer depends on how it counts characters.
    """

    pieces: list[dict[str, Any]] = []
    cursor = 0
    for start, end in sorted(spans):
        if not (cursor <= start < end <= len(text)):
            raise CardError("highlighted spans overlap or leave the text")
        if start > cursor:
            pieces.append({"text": text[cursor:start], "mark": False})
        pieces.append({"text": text[start:end], "mark": True})
        cursor = end
    if cursor < len(text) or not pieces:
        pieces.append({"text": text[cursor:], "mark": False})
    return pieces


def row_object(row: Row) -> dict[str, Any]:
    """The stored form of a diff row: ``ctxc`` is ``ctx``; only del and add rows carry marks."""

    kind = "ctx" if row.kind == "ctxc" else row.kind
    if kind not in ("ctx", "del", "add"):
        raise CardError(f"unexpected row kind {row.kind!r}")
    return {
        "kind": kind,
        "old_no": row.old_no,
        "new_no": row.new_no,
        "pair": row.pair,
        "indent_em": float(row.indent_em),
        "segments": segments(row.text, row.spans if kind != "ctx" else ()),
    }


def block_object(order: int, kind: str, block: Block, old: Sequence[str], new: Sequence[str],
                 clause_codes: Sequence[str]) -> dict[str, Any]:
    return {
        "block_order": order,
        "block_kind": kind,
        "clause_id": block.clause_id,
        "clause_codes": list(clause_codes),
        "path": block.path,
        "badge": block.badge,
        "hunk_old": block.hunk_old,
        "hunk_new": block.hunk_new,
        "adds": block.adds,
        "dels": block.dels,
        "old_paragraphs": list(old),
        "new_paragraphs": list(new),
        "rows": [row_object(row) for row in block.rows],
    }


def _fail(block: Mapping[str, Any], message: str) -> CardError:
    return CardError(f"{block.get('path', '?')}: {message}")


def _paragraph_number(value: Any, limit: int, block: Mapping[str, Any]) -> int:
    if not (isinstance(value, str) and PARAGRAPH_NUMBER.fullmatch(value) and int(value) <= limit):
        raise _fail(block, f"paragraph number {value!r} is not 1..{limit}")
    return int(value)


def check_replay(block: Mapping[str, Any]) -> None:
    """The replay invariant of a block object; raises ``CardError`` on the first violation.

    Old numbers over ctx and del rows are 1..len(old) in order; new numbers over ctx and add rows
    are 1..len(new) in order; a del row's text is its old paragraph, an add or ctx row's text is its
    new paragraph; segments concatenate to the row text and only del and add rows carry marks;
    the two rows of one edited paragraph share a pair id; adds and dels equal the row counts.
    """

    try:
        _check_replay(block)
    except (KeyError, IndexError, TypeError) as exc:
        raise _fail(block, f"malformed block object ({type(exc).__name__}: {exc})") from None


def _check_replay(block: Mapping[str, Any]) -> None:
    old, new, rows = block["old_paragraphs"], block["new_paragraphs"], block["rows"]
    old_numbers: list[int] = []
    new_numbers: list[int] = []
    kinds: Counter[str] = Counter()
    pair_kinds: dict[int, list[str]] = defaultdict(list)
    for index, row in enumerate(rows):
        kind = row["kind"]
        if kind not in ("ctx", "del", "add"):
            raise _fail(block, f"row {index}: unexpected kind {kind!r}")
        kinds[kind] += 1
        pieces = row["segments"]
        if not pieces or not all(isinstance(p["text"], str) and isinstance(p["mark"], bool) for p in pieces):
            raise _fail(block, f"row {index}: malformed segments")
        text = "".join(piece["text"] for piece in pieces)
        if kind == "ctx" and any(piece["mark"] for piece in pieces):
            raise _fail(block, f"row {index}: a context row carries marked text")
        indent = row["indent_em"]
        if isinstance(indent, bool) or not isinstance(indent, (int, float)) or indent < 0:
            raise _fail(block, f"row {index}: indent_em {indent!r}")
        if kind in ("ctx", "del"):
            number = _paragraph_number(row["old_no"], len(old), block)
            old_numbers.append(number)
            if kind == "del" and text != old[number - 1]:
                raise _fail(block, f"row {index}: deleted text is not old paragraph {number}")
        elif row["old_no"] != "":
            raise _fail(block, f"row {index}: an add row has an old number")
        if kind in ("ctx", "add"):
            number = _paragraph_number(row["new_no"], len(new), block)
            new_numbers.append(number)
            if text != new[number - 1]:
                raise _fail(block, f"row {index}: text is not new paragraph {number}")
        elif row["new_no"] != "":
            raise _fail(block, f"row {index}: a del row has a new number")
        pair = row["pair"]
        if isinstance(pair, bool) or not isinstance(pair, int) or pair < -1 or (kind == "ctx" and pair != -1):
            raise _fail(block, f"row {index}: pair {pair!r}")
        if pair >= 0:
            pair_kinds[pair].append(kind)
    if old_numbers != list(range(1, len(old) + 1)):
        raise _fail(block, f"old numbers {old_numbers} do not replay the {len(old)} old paragraphs")
    if new_numbers != list(range(1, len(new) + 1)):
        raise _fail(block, f"new numbers {new_numbers} do not replay the {len(new)} new paragraphs")
    if kinds["add"] != block["adds"] or kinds["del"] != block["dels"]:
        raise _fail(block, f"adds/dels {block['adds']}/{block['dels']} differ from rows {kinds['add']}/{kinds['del']}")
    if any(sorted(found) != ["add", "del"] for found in pair_kinds.values()):
        raise _fail(block, "a pair id does not name exactly one del row and one add row")


def comparison_sha256(blocks: Sequence[Mapping[str, Any]]) -> str:
    return object_fingerprint(list(blocks))


def rows_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return object_fingerprint(list(rows))


def build_comparison(payload: bytes) -> dict[str, Any]:
    """Blocks, ``comparison_sha256`` and ``extra_table_count`` of one comparison-table ODT."""

    return comparison_from_table(read_table(payload))


def comparison_from_table(table: Mapping[str, Any]) -> dict[str, Any]:
    """The same from a table read by ``sources_nhi.read_table``.

    Every block is checked against the replay invariant before it is returned.
    """

    plan = plan_blocks(table)
    clause_codes = []
    for kind, block, old, new in plan:
        if kind != "clause":
            clause_codes.append(None)
            continue
        if clause_heading_code(old[0] if new == ["無"] else new[0]) != block.clause_id:
            raise CardError(f"{block.path}: the clause heading differs between the card parser and the overlay grammar")
        clause_codes.append(clause_codes_of(block.clause_id, old, new))
    # an attachment table belongs to the whole notice: it serves every code its clause blocks serve
    notice_codes = list(dict.fromkeys(code for codes in clause_codes if codes for code in codes))
    if not notice_codes:
        raise CardError("the comparison table holds no clause row")
    objects = []
    for order, (kind, block, old, new) in enumerate(plan):
        obj = block_object(order, kind, block, old, new, clause_codes[order] or notice_codes)
        check_replay(obj)
        objects.append(obj)
    return {
        "source_artifact_sha256": table["sha256"],
        "extra_table_count": len(table["extra_tables"]),
        "blocks": objects,
        "comparison_sha256": comparison_sha256(objects),
    }


def comparison_for_bundle(item: QueuedBundle) -> dict[str, Any]:
    """The verified comparison of one registered bundle (reads the corpus, never the database)."""

    bundle = read_notice_bundle(item.bundle_dir, registered_manifest_sha256=item.corpus_manifest_sha256)
    attachment, payload = comparison_attachment(bundle)
    comparison = build_comparison(payload)
    if comparison["source_artifact_sha256"] != attachment.sha256:
        raise CardError("the parsed ODT is not the selected attachment")
    return comparison | {
        "work_item_id": item.work_item_id,
        "corpus_manifest_sha256": bundle.manifest_sha256,
    }


# ---------------------------------------------------------------------------
# I/O part: store and read back


def _connect(dsn: str, *, read_only: bool) -> Any:
    """Autocommit connection with dict rows; a read-only one is asserted, not assumed."""

    import psycopg
    from psycopg.rows import dict_row

    try:
        connection = psycopg.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on" if read_only else None,
            application_name="nhi-diff-card-load",
        )
    except psycopg.OperationalError as exc:
        raise StoreError(f"cannot connect to PostgreSQL: {str(exc).splitlines()[0]}") from None
    if read_only:
        state = connection.execute("SHOW default_transaction_read_only").fetchone()
        if state is None or state["default_transaction_read_only"] != "on":
            connection.close()
            raise StoreError("a read-only session could not be established")
    return connection


def _store_present(connection: Any) -> bool:
    row = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS present", (f"{STORE_SCHEMA}.comparison",)
    ).fetchone()
    return bool(row["present"])


def _stored_hash(connection: Any, sha: str, generator_version: str) -> str | None:
    row = connection.execute(
        f"SELECT comparison_sha256 FROM {STORE_SCHEMA}.comparison "
        "WHERE source_artifact_sha256 = %s AND generator_version = %s",
        (sha, generator_version),
    ).fetchone()
    return None if row is None else row["comparison_sha256"]


def comparison_id(sha: str, generator_version: str) -> str:
    return stable_uuid("diff-card-comparison", [sha, generator_version])


def _insert(connection: Any, comparison: Mapping[str, Any], generator_version: str) -> bool:
    """Insert one comparison with all its blocks in one transaction; False if it is already stored."""

    sha = comparison["source_artifact_sha256"]
    cid = comparison_id(sha, generator_version)
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '30s'")  # a stuck loader is an error, not a hang
        connection.execute("SET LOCAL statement_timeout = '120s'")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_KEY,))
        if _stored_hash(connection, sha, generator_version) is not None:
            return False
        connection.execute(
            f"INSERT INTO {STORE_SCHEMA}.comparison (comparison_id, source_artifact_sha256, generator_version, "
            "comparison_sha256, block_count, extra_table_count, work_item_id, corpus_manifest_sha256) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (cid, sha, generator_version, comparison["comparison_sha256"], len(comparison["blocks"]),
             comparison["extra_table_count"], comparison["work_item_id"], comparison["corpus_manifest_sha256"]),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                f"INSERT INTO {STORE_SCHEMA}.comparison_block (comparison_id, block_order, block_kind, clause_id, "
                "clause_codes, path, badge, hunk_old, hunk_new, adds, dels, old_paragraphs, new_paragraphs, rows, "
                "rows_sha256) VALUES (%s, %s, %s, %s, %s::text[], %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, "
                "%s::jsonb, %s)",
                [
                    (cid, b["block_order"], b["block_kind"], b["clause_id"], b["clause_codes"], b["path"],
                     b["badge"], b["hunk_old"], b["hunk_new"], b["adds"], b["dels"], json_text(b["old_paragraphs"]),
                     json_text(b["new_paragraphs"]), json_text(b["rows"]), rows_sha256(b["rows"]))
                    for b in comparison["blocks"]
                ],
            )
    return True


BLOCK_COLUMNS = ("block_order", "block_kind", "clause_id", "clause_codes", "path", "badge", "hunk_old", "hunk_new",
                 "adds", "dels", "old_paragraphs", "new_paragraphs", "rows")


def read_served(connection: Any, shas: Sequence[str]) -> dict[str, dict[str, Any]]:
    """What the public view serves for these source files, as the consumer reads it."""

    served: dict[str, dict[str, Any]] = {}
    rows = connection.execute(
        "SELECT source_artifact_sha256, generator_version, comparison_sha256, extra_table_count, "
        f"{', '.join(BLOCK_COLUMNS)} FROM {STORE_SCHEMA}.v_public_comparison_block "
        "WHERE source_artifact_sha256 = ANY(%s) ORDER BY source_artifact_sha256, block_order",
        (list(shas),),
    ).fetchall()
    for row in rows:
        entry = served.setdefault(row["source_artifact_sha256"], {
            "generator_version": row["generator_version"],
            "comparison_sha256": row["comparison_sha256"],
            "extra_table_count": row["extra_table_count"],
            "blocks": [],
        })
        entry["blocks"].append({column: row[column] for column in BLOCK_COLUMNS})
    return served


def verify_served(connection: Any, built: Mapping[str, Mapping[str, Any]],
                  generator_version: str) -> dict[str, Any]:
    """Recompute each comparison's hash from the rows PostgreSQL returns and compare.

    A mismatch is a stored value that differs from its own rows, from the comparison just built
    (the generator changed without a version bump, and the write-once store cannot follow), or from
    the replay invariant.  A source file whose newest comparison belongs to another generator
    version is listed as superseded, not as a mismatch.
    """

    mismatches: list[dict[str, str]] = []
    superseded: list[str] = []
    served = read_served(connection, list(built))
    block_hashes = {
        (row["source_artifact_sha256"], row["block_order"]): row["rows_sha256"]
        for row in connection.execute(
            f"SELECT c.source_artifact_sha256, b.block_order, b.rows_sha256 FROM {STORE_SCHEMA}.comparison AS c "
            f"JOIN {STORE_SCHEMA}.comparison_block AS b ON b.comparison_id = c.comparison_id "
            "WHERE c.generator_version = %s AND c.source_artifact_sha256 = ANY(%s)",
            (generator_version, list(built)),
        ).fetchall()
    }
    for sha, comparison in built.items():
        got = served.get(sha)
        if got is None:
            mismatches.append({"source_artifact_sha256": sha, "error": "the public view serves no row for it"})
            continue
        if got["generator_version"] != generator_version:
            superseded.append(sha)
            continue
        found = []
        recomputed = comparison_sha256(got["blocks"])
        if recomputed != got["comparison_sha256"]:
            found.append("the stored hash differs from the hash of its own rows")
        if recomputed != comparison["comparison_sha256"]:
            found.append("the stored rows differ from the comparison just built "
                         "(generator changed without a version bump)")
        if got["extra_table_count"] != comparison["extra_table_count"]:
            found.append("extra_table_count differs")
        found.extend(
            f"block {block['block_order']}: the stored rows_sha256 differs from the hash of its rows"
            for block in got["blocks"]
            if block_hashes.get((sha, block["block_order"])) != rows_sha256(block["rows"])
        )
        try:
            for block in got["blocks"]:
                check_replay(block)
        except CardError as exc:
            found.append(f"replay invariant: {exc}")
        mismatches.extend({"source_artifact_sha256": sha, "error": text} for text in found)
    return {"checked": len(built), "mismatches": mismatches, "superseded": sorted(superseded)}


def _store_one(connection: Any, comparison: Mapping[str, Any], generator_version: str, *, dry_run: bool,
               store_present: bool) -> str:
    """inserted | present | would_insert; a dry run only looks the comparison up."""

    if dry_run:
        held = store_present and _stored_hash(
            connection, comparison["source_artifact_sha256"], generator_version) is not None
        return "present" if held else "would_insert"
    return "inserted" if _insert(connection, comparison, generator_version) else "present"


def _card_listing(distinct: Mapping[str, Mapping[str, Any]], outcomes: Mapping[str, str]) -> list[dict[str, Any]]:
    """Per source file: the work item, what happened to it and each block's clause_id and clause_codes."""

    return [
        {
            "source_artifact_sha256": sha,
            "work_item_id": comparison["work_item_id"],
            "outcome": outcomes[sha],
            "blocks": [
                {key: block[key] for key in ("block_order", "block_kind", "clause_id", "clause_codes")}
                for block in comparison["blocks"]
            ],
        }
        for sha, comparison in sorted(distinct.items())
    ]


def _item_error(exc: Exception, item: QueuedBundle) -> str:
    return describe_error(exc).replace(str(item.bundle_dir), "<bundle>")


def run(dsn: str, bundles: Sequence[QueuedBundle], *, dry_run: bool = False,
        generator_version: str = GENERATOR_VERSION) -> dict[str, Any]:
    """Load the comparison of every registered bundle; returns the receipt.

    A bundle that cannot become a card is listed in ``failed`` and never stops the others.
    ``dry_run`` builds and verifies everything but opens read-only sessions only and writes nothing;
    its ``inserted`` stays 0 and ``would_insert`` counts what a real run would store.  An
    infrastructure error (no connection, no store schema in a real run) raises ``StoreError``.
    """

    import psycopg

    problems = [{"work_item_id": item.work_item_id, "error": item.problem} for item in bundles if item.problem]
    failed: list[dict[str, str]] = []
    parsed = 0
    ordered: list[dict[str, Any]] = []
    for item in bundles:
        if item.problem:
            continue
        try:
            comparison = comparison_for_bundle(item)
        except Exception as exc:  # noqa: BLE001 - one bundle never aborts the batch
            failed.append({"work_item_id": item.work_item_id, "error": _item_error(exc, item)})
            continue
        parsed += 1
        ordered.append(comparison)

    counts = {"registered": len(bundles), "problem": len(problems), "parsed": parsed, "inserted": 0,
              "present": 0, "failed": 0}
    if dry_run:
        counts["would_insert"] = 0
    store_present = False
    distinct: dict[str, dict[str, Any]] = {}  # one comparison per official file, first registered item wins
    outcomes: dict[str, str] = {}
    stored: dict[str, dict[str, Any]] = {}  # comparisons that are in the store after this run
    connection = _connect(dsn, read_only=dry_run)
    try:
        store_present = _store_present(connection)
        if not store_present and not dry_run:
            raise StoreError(f"schema {STORE_SCHEMA} is missing: apply migration v29 first")
        for comparison in ordered:
            sha = comparison["source_artifact_sha256"]
            if sha in distinct:
                counts["present"] += 1  # two registered items carry one official file
                continue
            distinct[sha] = comparison
            try:
                outcomes[sha] = _store_one(connection, comparison, generator_version, dry_run=dry_run,
                                           store_present=store_present)
            except (psycopg.errors.DataError, psycopg.errors.IntegrityError, psycopg.errors.RaiseException) as exc:
                outcomes[sha] = "failed"
                failed.append({"work_item_id": comparison["work_item_id"], "error": f"{type(exc).__name__}: {exc}"})
                continue
            counts[outcomes[sha]] += 1
            if outcomes[sha] != "would_insert":
                stored[sha] = comparison
    finally:
        connection.close()

    readback: dict[str, Any] = {"checked": 0, "mismatches": [], "superseded": []}
    if stored:
        reader = _connect(dsn, read_only=True)  # a fresh connection: nothing from the writing session
        try:
            readback = verify_served(reader, stored, generator_version)
        finally:
            reader.close()
    counts["failed"] = len(failed)
    return {
        "schema": RECEIPT_SCHEMA,
        "generator_version": generator_version,
        "dry_run": dry_run,
        "store_schema": "present" if store_present else "missing",
        "counts": counts,
        "problems": problems,
        "failed": failed,
        "comparisons": _card_listing(distinct, outcomes),
        "readback": readback,
    }


# ---------------------------------------------------------------------------
# CLI entry


def resolve_dsn(explicit: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if explicit:
        return explicit
    if env.get(DSN_ENV):
        return env[DSN_ENV]
    if env.get("PGSERVICE"):
        return f"service={env['PGSERVICE']}"
    return None


def resolve_corpus_root(explicit: Path | None, environ: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    if explicit:
        return explicit
    return Path(env[CORPUS_ROOT_ENV]) if env.get(CORPUS_ROOT_ENV) else None


def load_pg(dsn: str | None, corpus_root: Path | None, *, dry_run: bool = False,
            bundle_source: Callable[..., Sequence[QueuedBundle]] = registered_bundles) -> int:
    """``build.py load-pg``: the receipt goes to stdout; the exit status is 0 when the run completed.

    1 = infrastructure error or a read-back mismatch; 2 = missing DSN or corpus root.
    """

    dsn = resolve_dsn(dsn)
    corpus_root = resolve_corpus_root(corpus_root)
    if not dsn:
        print(f"load-pg: no DSN: pass --dsn or set {DSN_ENV} (or PGSERVICE)", file=sys.stderr)
        return 2
    if corpus_root is None:
        print(f"load-pg: no bundle root: pass --corpus-root or set {CORPUS_ROOT_ENV}", file=sys.stderr)
        return 2
    try:
        bundles = bundle_source(dsn, corpus_root=corpus_root)
        receipt = run(dsn, bundles, dry_run=dry_run)
    except StoreError as exc:
        print(f"load-pg: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any other failure is an infrastructure error
        print(f"load-pg: {describe_error(exc)}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if receipt["readback"]["mismatches"] else 0
