"""Compose, load, activate and roll back announced-overlay release runs.

``nhi_rule_history_announced.v_active_run`` exposes exactly one sealed release
run, chosen by the latest release-control event.  A newly announced notice can
therefore become visible only through a new run that also carries everything
the active run serves.  This module builds that composite run:

* every run-scoped row of the active (base) run is carried forward unchanged
  except for ``run_id`` and the row hash that covers it; each stored row hash
  must first replay exactly, so a carried row is provably the same row;
* each new notice contributes ``notice_event``/``notice_effect`` rows and one
  ``patch_only`` ``clause_patch`` per projectable clause, built
  deterministically by :mod:`nhi_rule_history.announced_notice` without any
  model call; a notice whose every clause is held back still contributes its
  event, with each held-back clause as a pending effect;
* a notice the base run already carries can be superseded: its carried rows
  are replaced by its fresh projection, but only when every clause patch it
  serves is re-projected byte-identically;
* the carried 2.6.1 clause document normalization and exact diff are rebuilt
  for the new run by the unchanged 2.6.1 loader code, and the active reader
  profile is re-bound to them with byte-identical content.

Composition isolates failures per notice.  A notice that cannot be rendered,
bound or superseded is reported and left out while the rest of the batch
composes; only run-level invariants (base chain, publication, schema, seal,
2.6.1 re-binding) abort the batch.  Every composition reports its failures,
dropped notices and held-back clauses.

Loading seals everything in one transaction and changes nothing that is
served.  Activation is a separate, precondition-checked transaction; rollback
re-activates the recorded previous chain.  Neither step writes canonical legal
history.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from nhi_rule_history import announced_dyslipidemia as dyslipidemia
from nhi_rule_history import reader_profile
from nhi_rule_history.announced_notice import (
    PARSER_VERSION,
    PATCH_TEXT_JOIN,
    TEXT_RULE_VERSION,
    AnnouncedClause,
    AnnouncedNoticeError,
    ParsedNotice,
    exact_in_rendering,
    sha256_text,
    stable_uuid,
)
from nhi_rule_history.current_publication import semantic_comparison_text
from nhi_rule_history.pg.common import (
    code_fingerprint,
    json_text,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


SCHEMA = "nhi_rule_history_announced"
LOADER_VERSION = "nhi-rule-history/announced-overlay-loader/1.0.0"
GLOBAL_LOCK_KEY = "nhi-rule-history-announced-global"
CIVIL_TIMEZONE = "Asia/Taipei"
EMPTY_TEXT_SHA256 = sha256_text("")
# The seal guard counts exactly these tables; any other run-scoped table is a
# carried projection verified by its own receipt.
SEALED_COUNT_TABLES = tuple(dyslipidemia._TABLE_COLUMNS)
EVENT_TABLES = frozenset(
    {
        "release_run",
        "release_activation",
        "release_control_event",
        "patch_resolution_event",
    }
)
LEGACY_DOCUMENT_RECEIPT = "composed_clause_document_receipt"
LEGACY_DOCUMENT_TABLES = (
    "composed_clause_component",
    "composed_clause_component_block",
    "composed_clause_table",
    "composed_clause_table_row",
    "composed_clause_table_cell",
    "composed_clause_table_cell_block",
)
DYSLIPIDEMIA_CLAUSE = "2.6.1"
NOTICE_TABLES = ("notice_event", "notice_effect", "clause_patch")
PLACEHOLDER_RUN_ID = "00000000-0000-0000-0000-000000000000"
# Receipt status.  Only a receipt that holds nothing back is green.
STATUS_PASSED = "passed"
STATUS_PASSED_WITH_HOLDS = "passed_with_holds"
STATUS_NO_CHANGE = "no_change"
STATUS_NO_CHANGE_WITH_HOLDS = "no_change_with_holds"
_BLOCK_NOTES = {
    "generated_list_label": (
        "ODF list numbering draws labels that are not in the document "
        "character data"
    ),
    "official_rendering_mismatch": (
        "parsed text differs from the independent office rendering"
    ),
}


class AnnouncedReleaseError(AnnouncedNoticeError):
    """A release composition, load or activation invariant failed."""


def receipt_status(
    *,
    composed: bool,
    failures: Sequence[Any] | None,
    dropped_notices: Sequence[Any] | None,
    blocked_clauses: Sequence[Any] | None,
) -> str:
    """Overall receipt status; any failure, drop or held-back clause holds."""

    held = any(
        bool(value)
        for value in (failures, dropped_notices, blocked_clauses)
    )
    if composed:
        return STATUS_PASSED_WITH_HOLDS if held else STATUS_PASSED
    return STATUS_NO_CHANGE_WITH_HOLDS if held else STATUS_NO_CHANGE


def _jsonable(value: Any) -> Any:
    """Return the loader-time JSON value of one PostgreSQL column value."""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        raise AnnouncedReleaseError("run-scoped rows must not hold timestamps")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _hashed(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["source_row_sha256"] = row_sha256(out, derived_key="source_row_sha256")
    return out


def _connect(dsn: str, *, read_only: bool) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    options = "-c default_transaction_read_only=on" if read_only else None
    connection = psycopg.connect(dsn, row_factory=dict_row, options=options)
    connection.execute("SET TIME ZONE 'UTC'")
    return connection


# ---------------------------------------------------------------------------
# Schema discovery


@dataclass(frozen=True)
class TableShape:
    name: str
    columns: tuple[str, ...]
    json_columns: frozenset[str]


def _run_scoped_tables(connection: Any) -> list[TableShape]:
    """Return run-scoped announced tables in foreign-key insertion order."""

    rows = connection.execute(
        """
        SELECT c.table_name, c.column_name, c.data_type
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
        """,
        (SCHEMA,),
    ).fetchall()
    columns: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        columns.setdefault(row["table_name"], []).append(
            (row["column_name"], row["data_type"])
        )
    names = sorted(
        name
        for name, cols in columns.items()
        if name not in EVENT_TABLES and any(col == "run_id" for col, _ in cols)
    )
    edges = connection.execute(
        """
        SELECT child.relname AS child, parent.relname AS parent
        FROM pg_constraint con
        JOIN pg_class child ON child.oid = con.conrelid
        JOIN pg_class parent ON parent.oid = con.confrelid
        JOIN pg_namespace ns ON ns.oid = child.relnamespace
        WHERE con.contype = 'f' AND ns.nspname = %s
        """,
        (SCHEMA,),
    ).fetchall()
    parents: dict[str, set[str]] = {name: set() for name in names}
    for edge in edges:
        if edge["child"] in parents and edge["parent"] in parents:
            if edge["child"] != edge["parent"]:
                parents[edge["child"]].add(edge["parent"])
    ordered: list[str] = []
    remaining = set(names)
    while remaining:
        ready = sorted(
            name for name in remaining if not (parents[name] & remaining)
        )
        if not ready:
            raise AnnouncedReleaseError("run-scoped foreign keys form a cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    missing = set(SEALED_COUNT_TABLES) - set(ordered)
    if missing:
        raise AnnouncedReleaseError(
            f"announced schema lacks sealed tables: {sorted(missing)}"
        )
    return [
        TableShape(
            name=name,
            columns=tuple(col for col, _ in columns[name]),
            json_columns=frozenset(
                col for col, kind in columns[name] if kind == "jsonb"
            ),
        )
        for name in ordered
    ]


# ---------------------------------------------------------------------------
# Base run


@dataclass(frozen=True)
class BaseChain:
    """The active run and everything that must follow it to a new run."""

    run: Mapping[str, Any]
    tables: tuple[TableShape, ...]
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    resolutions: Mapping[str, Mapping[str, Any]]
    normalization_run: Mapping[str, Any] | None
    diff_run: Mapping[str, Any] | None
    reader_profile: Mapping[str, Any] | None


def read_base_chain(connection: Any, run_id: str | None = None) -> BaseChain:
    active = connection.execute(
        f"SELECT * FROM {SCHEMA}.v_active_run"
    ).fetchone()
    if active is None:
        raise AnnouncedReleaseError("no announced release run is active")
    if run_id is not None and str(active["run_id"]) != str(run_id):
        raise AnnouncedReleaseError(
            "the active announced run differs from the expected base run"
        )
    base_id = str(active["run_id"])
    if set(active["expected_counts"]) != set(SEALED_COUNT_TABLES):
        raise AnnouncedReleaseError(
            "base run was sealed under a different count contract"
        )
    tables = _run_scoped_tables(connection)
    rows: dict[str, tuple[dict[str, Any], ...]] = {}
    for shape in tables:
        fetched = connection.execute(
            f"SELECT * FROM {SCHEMA}.{shape.name} WHERE run_id=%s",
            (base_id,),
        ).fetchall()
        converted = []
        for row in fetched:
            value = {key: _jsonable(item) for key, item in row.items()}
            if "source_row_sha256" in value:
                replay = row_sha256(value, derived_key="source_row_sha256")
                if replay != value["source_row_sha256"]:
                    raise AnnouncedReleaseError(
                        f"carried row hash does not replay in {shape.name}"
                    )
            converted.append(value)
        rows[shape.name] = tuple(
            sorted(converted, key=lambda item: json_text(item))
        )
    counts = {name: len(rows[name]) for name in SEALED_COUNT_TABLES}
    if counts != dict(active["verified_counts"]):
        raise AnnouncedReleaseError("base run counts do not replay")
    resolutions = {
        str(row["patch_id"]): dict(row)
        for row in connection.execute(
            f"""
            SELECT DISTINCT ON (patch_id) *
            FROM {SCHEMA}.patch_resolution_event
            WHERE run_id=%s
            ORDER BY patch_id, resolution_id DESC
            """,
            (base_id,),
        ).fetchall()
    }
    patch_ids = {str(row["patch_id"]) for row in rows["clause_patch"]}
    if set(resolutions) != patch_ids:
        raise AnnouncedReleaseError("a base patch has no resolution event")
    normalization = connection.execute(
        f"""
        SELECT * FROM {SCHEMA}.v_active_clause_document_normalization_run
        WHERE source_release_run_id=%s
        """,
        (base_id,),
    ).fetchone()
    diff = None
    profile = None
    if normalization is not None:
        diff = connection.execute(
            f"""
            SELECT * FROM {SCHEMA}.v_active_clause_document_diff_run
            WHERE normalization_run_id=%s
            """,
            (normalization["normalization_run_id"],),
        ).fetchone()
        if diff is not None:
            profile = connection.execute(
                f"""
                SELECT pub.*, profile.profile_run_id,
                       profile.source_row_sha256
                FROM {SCHEMA}.v_public_clause_reader_profile pub
                JOIN {SCHEMA}.clause_reader_profile profile
                  USING (profile_id)
                WHERE pub.source_release_run_id=%s
                  AND pub.source_diff_run_id=%s
                  AND pub.source_diff_output_fingerprint=%s
                ORDER BY pub.activated_at DESC
                LIMIT 1
                """,
                (
                    base_id,
                    diff["diff_run_id"],
                    diff["output_fingerprint"],
                ),
            ).fetchone()
    composed = rows.get("composed_clause_version", ())
    if composed and (normalization is None or diff is None):
        raise AnnouncedReleaseError(
            "base composed clause lacks its active normalization or diff"
        )
    return BaseChain(
        run=dict(active),
        tables=tuple(tables),
        rows=rows,
        resolutions=resolutions,
        normalization_run=dict(normalization) if normalization else None,
        diff_run=dict(diff) if diff else None,
        reader_profile=dict(profile) if profile else None,
    )


# ---------------------------------------------------------------------------
# Notice discovery


@dataclass(frozen=True)
class QueuedBundle:
    work_item_id: str
    source_uid: str
    bundle_dir: Path
    corpus_manifest_sha256: str
    first_title_raw: str
    problem: str | None = None


def queued_bundles(
    dsn: str, *, corpus_root: Path, state: str = "corpus_registered"
) -> list[QueuedBundle]:
    """Registered notice bundles waiting in the update queue at ``state``.

    The queue receipt names the bundle by a corpus-relative path and pins its
    manifest hash; a bundle whose manifest bytes differ carries a ``problem``
    and must not be parsed.
    """

    with _connect(dsn, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT backlog.work_item_id, backlog.first_title_raw,
                   backlog.evidence_json
            FROM nhi_rule_history_update_queue.v_work_backlog backlog
            WHERE backlog.current_state = %s
            ORDER BY backlog.first_observed_at, backlog.work_item_id
            """,
            (state,),
        ).fetchall()
    found: list[QueuedBundle] = []
    root = Path(corpus_root)
    for row in rows:
        evidence = row["evidence_json"] or {}
        relative = str(evidence.get("corpus_bundle_relative_path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(
            relative
        ).parts:
            raise AnnouncedReleaseError("queue receipt has no safe bundle path")
        bundle_dir = root / relative
        manifest = bundle_dir / "manifest.json"
        digest = (
            hashlib.sha256(manifest.read_bytes()).hexdigest()
            if manifest.is_file()
            else ""
        )
        problem = None
        if digest != evidence.get("corpus_manifest_sha256"):
            problem = "corpus manifest differs from its queue receipt"
        found.append(
            QueuedBundle(
                work_item_id=str(row["work_item_id"]),
                source_uid=str(evidence.get("source_uid") or ""),
                bundle_dir=bundle_dir,
                corpus_manifest_sha256=digest,
                first_title_raw=str(row["first_title_raw"]),
                problem=problem,
            )
        )
    return found


# ---------------------------------------------------------------------------
# New notice rows


def served_clauses(
    connection: Any, codes: Iterable[str]
) -> tuple[str, dict[str, Mapping[str, Any]]]:
    run = connection.execute(
        "SELECT run_id FROM nhi_rule_history_publication.v_active_publication_run"
    ).fetchone()
    if run is None:
        raise AnnouncedReleaseError("no current publication run is active")
    rows = connection.execute(
        """
        SELECT clause_code, raw_text, raw_text_sha256
        FROM nhi_rule_history_publication.v_current_clause
        WHERE clause_code = ANY(%s)
        """,
        (sorted(set(codes)),),
    ).fetchall()
    return str(run["run_id"]), {row["clause_code"]: dict(row) for row in rows}


@dataclass(frozen=True)
class NoticeRows:
    notice: ParsedNotice
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    patch_evidence: Mapping[str, Mapping[str, Any]]
    patch_effective: Mapping[str, str]


def _public_note(clause: AnnouncedClause, header: str) -> str:
    source = f"健保署修訂對照表「{header}」欄逐字文字"
    if clause.original_is_none:
        if clause.omission_orders:
            return f"{source}；原給付規定欄為「無」（新增條文），表內以「略」省略部分段落。"
        return f"{source}；原給付規定欄為「無」（新增條文），本段為對照表所列全文。"
    if clause.omission_orders:
        return f"{source}；表內以「略」省略未修訂段落，本段非合併後完整條文。"
    return f"{source}；對照表只列本次修訂所涉段落，本段非合併後完整條文。"


def clause_block_reason(
    clause: AnnouncedClause, rendering_check: str | None
) -> str | None:
    """Fail-closed projection gate for one parsed clause."""

    if clause.blocked_reason:
        return clause.blocked_reason
    if rendering_check == "mismatch":
        return "official_rendering_mismatch"
    return None


def notice_rows(
    notice: ParsedNotice,
    *,
    run_id: str,
    served_run_id: str,
    served: Mapping[str, Mapping[str, Any]],
    rendering_checks: Mapping[str, str] | None = None,
) -> NoticeRows:
    notice_id = notice.notice_id
    header = notice.tables[0].revised_header
    checks = dict(rendering_checks or {})
    blocked = {
        clause.clause_code: reason
        for clause in notice.clauses
        if (reason := clause_block_reason(clause, checks.get(clause.clause_code)))
    }
    unresolved = [
        *(
            {"effect_type": "clause_amendment", "clause_code": code,
             "blocked_reason": reason}
            for code, reason in sorted(blocked.items())
        ),
        *(
            {
                "effect_type": effect.effect_type,
                "clause_code": None,
                **(
                    {"designation": effect.designation}
                    if effect.designation
                    else {}
                ),
            }
            for effect in notice.other_effects
        ),
    ]
    effects: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    evidence: dict[str, Mapping[str, Any]] = {}
    effective: dict[str, str] = {}
    for clause in notice.clauses:
        if clause.clause_code in blocked:
            effects.append(
                _hashed(
                    {
                        "run_id": run_id,
                        "effect_id": stable_uuid(
                            "effect", [notice_id, clause.clause_code]
                        ),
                        "notice_id": notice_id,
                        "effect_type": "clause_amendment",
                        "clause_code": clause.clause_code,
                        "projection_status": "pending_projection",
                        "scope_note": (
                            f"{clause.clause_code} revised column is not "
                            "projected: "
                            + _BLOCK_NOTES.get(
                                blocked[clause.clause_code],
                                "the parser holds it back ("
                                + blocked[clause.clause_code]
                                + ")",
                            )
                        ),
                    }
                )
            )
            continue
        effect_id = stable_uuid("effect", [notice_id, clause.clause_code])
        effects.append(
            _hashed(
                {
                    "run_id": run_id,
                    "effect_id": effect_id,
                    "notice_id": notice_id,
                    "effect_type": "clause_amendment",
                    "clause_code": clause.clause_code,
                    "projection_status": "projected_source_exact_patch",
                    "scope_note": (
                        f"{clause.clause_code} official comparison-table "
                        "revised column projected as patch-only announced text"
                    ),
                }
            )
        )
        current = served.get(clause.clause_code)
        if clause.original_is_none:
            if current is not None:
                raise AnnouncedReleaseError(
                    f"{clause.clause_code} is marked new but is already served"
                )
            predecessor = EMPTY_TEXT_SHA256
        else:
            if current is None:
                raise AnnouncedReleaseError(
                    f"{clause.clause_code} has an original column but no "
                    "served clause to bind"
                )
            predecessor = str(current["raw_text_sha256"])
        text = clause.patch_text
        manifest = clause.component_manifest()
        manifest_sha = object_fingerprint(manifest)
        text_sha = sha256_text(text)
        patch_id = stable_uuid(
            "patch", [clause.clause_code, clause.effective_on, text_sha]
        )
        patches.append(
            _hashed(
                {
                    "run_id": run_id,
                    "patch_id": patch_id,
                    "effect_id": effect_id,
                    "clause_code": clause.clause_code,
                    "predecessor_text_sha256": predecessor,
                    "effective_from": clause.effective_on,
                    "effective_until": None,
                    "resolution_state": "verified_scheduled",
                    "source_exact_patch_text": text,
                    "source_exact_patch_sha256": text_sha,
                    "omitted_text_present": bool(clause.omission_orders),
                    "composition_status": "patch_only",
                    "comparison_sha256": sha256_text(
                        semantic_comparison_text(text)
                    ),
                    "component_manifest_sha256": manifest_sha,
                    "partial_event_projection": bool(unresolved),
                    "unprocessed_event_scope": unresolved,
                    "public_note": _public_note(clause, header),
                }
            )
        )
        table = next(
            item for item in notice.tables
            if item.table_index == clause.table_index
        )
        evidence[patch_id] = {
            "official_rendering_check": checks.get(
                clause.clause_code, "unavailable"
            ),
            "loader_version": LOADER_VERSION,
            "parser_version": PARSER_VERSION,
            "text_rule_version": TEXT_RULE_VERSION,
            "patch_text_join": PATCH_TEXT_JOIN,
            "source_uid": notice.bundle.source_uid,
            "reference_number": notice.bundle.reference_number,
            "corpus_manifest_sha256": notice.bundle.manifest_sha256,
            "source_artifact_sha256": notice.attachment.sha256,
            "source_artifact_filename": notice.attachment.file_name,
            "comparison_table": {
                "table_index": table.table_index,
                "rows": [list(row) for row in clause.rows],
                "revised_header": table.revised_header,
                "original_header": table.original_header,
            },
            "effective_statement": {
                "source_block_id": table.effective_statement.block_id,
                "document_order": table.effective_statement.document_order,
                "text": table.effective_statement.text,
                "effective_on": clause.effective_on,
            },
            "component_manifest_sha256": manifest_sha,
            "component_manifest": manifest,
            "omission_marker_document_orders": list(clause.omission_orders),
            "original_column": {
                "is_none": clause.original_is_none,
                "text_sha256": sha256_text(clause.original_text),
                "source_block_ids": [
                    item.block_id for item in clause.original
                ],
            },
            "served_publication_run_id": served_run_id,
            "served_clause_text_sha256": (
                None if current is None else str(current["raw_text_sha256"])
            ),
        }
        effective[patch_id] = clause.effective_on
    for effect in notice.other_effects:
        effects.append(
            _hashed(
                {
                    "run_id": run_id,
                    "effect_id": stable_uuid("effect", [notice_id, effect.key]),
                    "notice_id": notice_id,
                    "effect_type": effect.effect_type,
                    "clause_code": None,
                    "projection_status": "pending_projection",
                    "scope_note": effect.scope_note,
                }
            )
        )
    event = _hashed(
        {
            "run_id": run_id,
            "notice_id": notice_id,
            "reference_number": notice.bundle.reference_number,
            "title": notice.bundle.title,
            "official_url": notice.bundle.official_url,
            "published_on": notice.bundle.published_on,
            "effective_on": notice.effective_on,
            "civil_timezone": CIVIL_TIMEZONE,
            "source_artifact_sha256": notice.attachment.sha256,
            "source_artifact_filename": notice.attachment.file_name,
            "source_exact": True,
            "event_scope_complete": not unresolved,
            "unresolved_scope": unresolved,
        }
    )
    return NoticeRows(
        notice=notice,
        rows={
            "notice_event": (event,),
            "notice_effect": tuple(effects),
            "clause_patch": tuple(patches),
        },
        patch_evidence=evidence,
        patch_effective=effective,
    )


# ---------------------------------------------------------------------------
# Composite release


@dataclass(frozen=True)
class ResolutionRow:
    patch_id: str
    resolution_state: str
    reason: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class ReaderProfileRebind:
    profile_run_id: str
    profile_id: str
    input_fingerprint: str
    row: Mapping[str, Any]
    output_fingerprint: str
    sealed_fingerprint: str
    base_profile_run_id: str
    base_profile_id: str


@dataclass(frozen=True)
class OverlayRelease:
    run_id: str
    base: BaseChain
    notices: tuple[NoticeRows, ...]
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    expected_counts: Mapping[str, int]
    all_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    all_table_fingerprints: Mapping[str, str]
    input_fingerprint: str
    output_fingerprint: str
    sealed_fingerprint: str
    source_set_sha256: str
    evaluator_version: str
    resolutions: tuple[ResolutionRow, ...]
    rebinding: Any | None
    profile: ReaderProfileRebind | None
    rebinding_control: Mapping[str, Any] = field(default_factory=dict)

    @property
    def normalization_run_id(self) -> str | None:
        return self.rebinding.normalization_run_id if self.rebinding else None

    @property
    def diff_run_id(self) -> str | None:
        return self.rebinding.diff_run_id if self.rebinding else None


def _code_sha256() -> str:
    here = Path(__file__).resolve()
    return code_fingerprint(here, here.with_name("announced_notice.py"))


def _carry(
    base: BaseChain, run_id: str
) -> dict[str, list[dict[str, Any]]]:
    carried: dict[str, list[dict[str, Any]]] = {}
    for shape in base.tables:
        out = []
        for row in base.rows[shape.name]:
            value = dict(row)
            value["run_id"] = run_id
            if "source_row_sha256" in value:
                value = _hashed(value)
            out.append(value)
        carried[shape.name] = out
    receipts = carried.get(LEGACY_DOCUMENT_RECEIPT)
    if receipts:
        # Carried clause-document rows get new row hashes, so their receipt
        # is recomputed with the frozen v24 formula after proving that the
        # same formula replays the base receipt.
        for index, receipt in enumerate(receipts):
            for label, source in (("base", base.rows), ("new", carried)):
                counts: dict[str, int] = {}
                fingerprints: dict[str, str] = {}
                for table in LEGACY_DOCUMENT_TABLES:
                    selected = [
                        row["source_row_sha256"]
                        for row in source[table]
                        if row["version_id"] == receipt["version_id"]
                    ]
                    counts[table] = len(selected)
                    fingerprints[table] = row_set_fingerprint(selected)
                output = object_fingerprint(
                    {
                        "counts": counts,
                        "table_fingerprints": fingerprints,
                        "structure_manifest_sha256": receipt[
                            "structure_manifest_sha256"
                        ],
                    }
                )
                if label == "base":
                    original = next(
                        row
                        for row in base.rows[LEGACY_DOCUMENT_RECEIPT]
                        if row["version_id"] == receipt["version_id"]
                    )
                    if (
                        original["expected_counts"] != counts
                        or original["table_fingerprints"] != fingerprints
                        or original["document_output_fingerprint"] != output
                    ):
                        raise AnnouncedReleaseError(
                            "legacy clause document receipt does not replay"
                        )
                else:
                    updated = dict(receipt)
                    updated.update(
                        expected_counts=counts,
                        table_fingerprints=fingerprints,
                        document_output_fingerprint=output,
                    )
                    receipts[index] = _hashed(updated)
    return carried


def _pinned_rebinding_inputs(
    connection: Any, base: BaseChain
) -> dict[str, Any]:
    """Pin every 2.6.1 loader input to what the base run was built from."""

    version = base.rows["composed_clause_version"][0]
    publication_run = version["predecessor_publication_run_id"]
    clause = connection.execute(
        """
        SELECT run_id, clause_code, raw_text, raw_text_sha256,
               source_artifact_sha256, source_url, source_label
        FROM nhi_rule_history_publication.current_clause
        WHERE run_id=%s AND clause_code=%s
        """,
        (publication_run, DYSLIPIDEMIA_CLAUSE),
    ).fetchone()
    blocks = connection.execute(
        """
        SELECT block_order, source_block_id, block_kind, container,
               raw_text, raw_text_sha256, source_locator
        FROM nhi_rule_history_publication.current_clause_block
        WHERE run_id=%s AND clause_code=%s
        ORDER BY block_order
        """,
        (publication_run, DYSLIPIDEMIA_CLAUSE),
    ).fetchall()
    predecessor = {
        "run_id": str(clause["run_id"]),
        "clause_code": clause["clause_code"],
        "raw_text": clause["raw_text"],
        "raw_text_sha256": clause["raw_text_sha256"],
        "source_artifact_sha256": clause["source_artifact_sha256"],
        "source_url": clause["source_url"],
        "source_label": clause["source_label"],
        "blocks": [dict(row) for row in blocks],
    }
    tagging_runs = {
        row["terminology_tagging_run_id"]
        for row in base.rows["composed_clause_tagging_block_input"]
    }
    if len(tagging_runs) != 1:
        raise AnnouncedReleaseError("base clause mixes terminology runs")
    tagging_run_id = next(iter(tagging_runs))
    run = connection.execute(
        """
        SELECT tagging_run_id, output_fingerprint, sealed_fingerprint,
               matcher_version, offset_contract, alias_admission_policy
        FROM nhi_rule_history_terminology.tagging_run
        WHERE tagging_run_id=%s AND state='sealed'
        """,
        (tagging_run_id,),
    ).fetchone()
    if run is None:
        raise AnnouncedReleaseError("base terminology run is not sealed")
    aliases = connection.execute(
        """
        SELECT alias_id, concept_id, normalized_alias,
               production_status, match_rule
        FROM nhi_rule_history_terminology.concept_alias
        WHERE tagging_run_id=%s
        ORDER BY length(normalized_alias) DESC,
                 normalized_alias, concept_id, alias_id
        """,
        (tagging_run_id,),
    ).fetchall()
    terminology = {
        "tagging_run_id": str(run["tagging_run_id"]),
        "output_fingerprint": str(run["output_fingerprint"]),
        "sealed_fingerprint": str(run["sealed_fingerprint"]),
        "matcher_version": str(run["matcher_version"]),
        "offset_contract": str(run["offset_contract"]),
        "alias_admission_policy": str(run["alias_admission_policy"]),
        "aliases": [
            {key: str(value) for key, value in row.items()} for row in aliases
        ],
    }
    known = [
        {
            "drug_code": row["nhi_code"],
            "name_zh": row["product_name"],
            "name_en": None,
            "atc_code": row["atc_code"],
        }
        for row in base.rows["reimbursement_product_snapshot"]
    ]
    return {
        "predecessor": predecessor,
        "terminology_projection": terminology,
        "known_products": known,
        "version_id": str(version["version_id"]),
        "composed_text_sha256": str(version["composed_text_sha256"]),
    }


def _rebinding(
    connection: Any,
    base: BaseChain,
    *,
    run_id: str,
    dyslipidemia_odt: Path | None,
) -> tuple[Any | None, dict[str, Any]]:
    composed = base.rows.get("composed_clause_version", ())
    if not composed:
        return None, {}
    codes = {row["clause_code"] for row in composed}
    if codes != {DYSLIPIDEMIA_CLAUSE}:
        raise AnnouncedReleaseError(
            "only the 2.6.1 composed clause has a registered re-binder"
        )
    if dyslipidemia_odt is None:
        raise AnnouncedReleaseError(
            "the carried 2.6.1 clause needs its official amendment ODT"
        )
    inputs = _pinned_rebinding_inputs(connection, base)
    base_id = str(base.run["run_id"])
    control = dyslipidemia.prepare_announced_material(
        dyslipidemia_odt,
        known_products=inputs["known_products"],
        predecessor=inputs["predecessor"],
        terminology_projection=inputs["terminology_projection"],
        source_release_run_id=base_id,
        source_version_id=inputs["version_id"],
    )
    # Positive control: the unchanged loader must rebuild the active chain
    # of the base run exactly before it is allowed to build the new one.
    if (
        control.normalization_run_id
        != str(base.normalization_run["normalization_run_id"])
        or control.normalization_sealed_fingerprint
        != base.normalization_run["sealed_fingerprint"]
        or control.diff_run_id != str(base.diff_run["diff_run_id"])
        or control.diff_sealed_fingerprint != base.diff_run["sealed_fingerprint"]
    ):
        raise AnnouncedReleaseError(
            "2.6.1 loader no longer reproduces the served normalization/diff"
        )
    material = dyslipidemia.prepare_announced_material(
        dyslipidemia_odt,
        known_products=inputs["known_products"],
        predecessor=inputs["predecessor"],
        terminology_projection=inputs["terminology_projection"],
        source_release_run_id=run_id,
        source_version_id=inputs["version_id"],
    )
    version = material.rows["composed_clause_version"][0]
    if (
        material.version_id != inputs["version_id"]
        or version["composed_text_sha256"] != inputs["composed_text_sha256"]
    ):
        raise AnnouncedReleaseError("re-bound 2.6.1 version drifted")
    receipt = {
        "positive_control": {
            "base_run_id": base_id,
            "normalization_run_id": control.normalization_run_id,
            "normalization_sealed_fingerprint": (
                control.normalization_sealed_fingerprint
            ),
            "diff_run_id": control.diff_run_id,
            "diff_sealed_fingerprint": control.diff_sealed_fingerprint,
            "diff_output_fingerprint": control.diff_output_fingerprint,
        },
        "content_equal_tables": _content_equal(control, material),
    }
    return material, receipt


_ID_FREE_EXCLUDE = frozenset(
    {
        "normalization_run_id",
        "diff_run_id",
        "source_row_sha256",
        "expression_id",
        "older_expression_id",
        "newer_expression_id",
        "relation_id",
        "node_id",
        "parent_node_id",
        "older_node_id",
        "newer_node_id",
        "table_id",
        "span_id",
        "hunk_id",
        "lineage_id",
        "source_run_id",
        "evidence_receipt",
        "evidence_receipt_sha256",
        "completeness_receipt_sha256",
        "output_fingerprint",
        "table_fingerprints",
    }
)


_UUID_TEXT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _id_free(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        _UUID_TEXT.sub(
            "<uuid>",
            json_text(
                {
                    key: value
                    for key, value in row.items()
                    if key not in _ID_FREE_EXCLUDE
                }
            ),
        )
        for row in rows
    )


def _content_equal(control: Any, material: Any) -> dict[str, bool]:
    """Compare two 2.6.1 projections with run-bound identifiers masked.

    Every run-bound identifier is a UUID and every row hash covers them, so
    both are masked; texts, orders, markers, states, spans, table grids and
    diff segments are compared exactly.
    """

    result: dict[str, bool] = {}
    for label, left, right in (
        ("normalization", control.normalization_rows, material.normalization_rows),
        ("diff", control.diff_rows, material.diff_rows),
    ):
        for table in left:
            result[f"{label}.{table}"] = _id_free(left[table]) == _id_free(
                right[table]
            )
    if not all(result.values()):
        failed = sorted(key for key, value in result.items() if not value)
        raise AnnouncedReleaseError(
            f"re-bound 2.6.1 projection content differs: {failed}"
        )
    return result


def _profile_rebind(
    base: BaseChain, *, run_id: str, material: Any
) -> ReaderProfileRebind | None:
    profile = base.reader_profile
    if profile is None:
        return None
    binding = {
        "source_release_run_id": run_id,
        "source_version_id": str(profile["source_version_id"]),
        "source_composed_text_sha256": profile["source_composed_text_sha256"],
        "source_diff_run_id": material.diff_run_id,
        "source_diff_output_fingerprint": material.diff_output_fingerprint,
    }
    payload = reader_profile.validate_reader_profile(
        {
            "contract": profile["profile_contract"],
            "clause_code": profile["clause_code"],
            "effective_on": base.rows["composed_clause_version"][0][
                "effective_from"
            ],
            "presentation_mode": profile["presentation_mode"],
            "template_key": profile["template_key"],
            "authoring_method": profile["authoring_method"],
            "review_status": profile["review_status"],
            "disclosure_text": profile["disclosure_text"],
            "source_binding": binding,
            "content": profile["content_payload"],
        }
    )
    input_fingerprint = hashlib.sha256(
        json_text(
            {
                "rebinding_of_profile_id": str(profile["profile_id"]),
                "rebinding_of_content_sha256": profile["content_sha256"],
                "payload": payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    profile_run_id = reader_profile._stable_uuid(
        "reader-profile-run", input_fingerprint
    )
    profile_id = reader_profile._stable_uuid(
        "reader-profile",
        [payload["clause_code"], binding["source_version_id"], input_fingerprint],
    )
    row = {
        "profile_run_id": profile_run_id,
        "profile_id": profile_id,
        "clause_code": payload["clause_code"],
        "source_release_run_id": run_id,
        "source_version_id": binding["source_version_id"],
        "source_composed_text_sha256": binding["source_composed_text_sha256"],
        "source_diff_run_id": binding["source_diff_run_id"],
        "source_diff_output_fingerprint": binding[
            "source_diff_output_fingerprint"
        ],
        "presentation_mode": payload["presentation_mode"],
        "template_key": payload["template_key"],
        "profile_contract": payload["contract"],
        "authoring_method": payload["authoring_method"],
        "review_status": payload["review_status"],
        "disclosure_text": payload["disclosure_text"],
        "content_payload": payload["content"],
        "content_sha256": profile["content_sha256"],
    }
    source_row_sha = row_sha256(row, derived_key="source_row_sha256")
    output = hashlib.sha256(source_row_sha.encode("utf-8")).hexdigest()
    sealed = hashlib.sha256(
        "|".join([profile_run_id, input_fingerprint, output]).encode("utf-8")
    ).hexdigest()
    return ReaderProfileRebind(
        profile_run_id=profile_run_id,
        profile_id=profile_id,
        input_fingerprint=input_fingerprint,
        row={**row, "source_row_sha256": source_row_sha},
        output_fingerprint=output,
        sealed_fingerprint=sealed,
        base_profile_run_id=str(profile["profile_run_id"]),
        base_profile_id=str(profile["profile_id"]),
    )


@dataclass(frozen=True)
class CarriedNotice:
    """One notice of the base run with the rows that serve it."""

    event: Mapping[str, Any]
    effects: tuple[Mapping[str, Any], ...]
    patches: tuple[Mapping[str, Any], ...]
    dependent_tables: tuple[str, ...]

    @property
    def notice_id(self) -> str:
        return str(self.event["notice_id"])


def _carried_notices(base: BaseChain) -> dict[str, CarriedNotice]:
    """Index the base run's notices by reference number."""

    effects: dict[str, list[Mapping[str, Any]]] = {}
    for row in base.rows["notice_effect"]:
        effects.setdefault(str(row["notice_id"]), []).append(row)
    patches: dict[str, list[Mapping[str, Any]]] = {}
    for row in base.rows["clause_patch"]:
        patches.setdefault(str(row["effect_id"]), []).append(row)
    carried: dict[str, CarriedNotice] = {}
    for event in base.rows["notice_event"]:
        notice_id = str(event["notice_id"])
        own_effects = tuple(effects.get(notice_id, ()))
        effect_ids = {str(row["effect_id"]) for row in own_effects}
        own_patches = tuple(
            row
            for effect_id in sorted(effect_ids)
            for row in patches.get(effect_id, ())
        )
        patch_ids = {str(row["patch_id"]) for row in own_patches}
        # Any other carried row that names this notice (patch components,
        # composed versions, decision models) would be orphaned by a
        # supersede, so it pins the notice to its carried rows.
        dependent = sorted(
            shape.name
            for shape in base.tables
            if shape.name not in NOTICE_TABLES
            and any(
                str(row.get("patch_id")) in patch_ids
                or str(row.get("effect_id")) in effect_ids
                or str(row.get("notice_id")) == notice_id
                for row in base.rows[shape.name]
            )
        )
        carried[str(event["reference_number"])] = CarriedNotice(
            event=event,
            effects=own_effects,
            patches=own_patches,
            dependent_tables=tuple(dependent),
        )
    return carried


def _notice_label(notice: ParsedNotice) -> dict[str, Any]:
    return {
        "reference_number": notice.bundle.reference_number,
        "bundle": notice.bundle.bundle_dir.name,
        "effective_on": notice.effective_on,
    }


def blocked_clauses(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    origins: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Dotted clauses that a run's notices hold back, with a reason each.

    A held-back clause is a ``clause_amendment`` effect with a clause code in
    ``pending_projection``.  Appendix-table and listed-item effects are
    pending by design, not held back, and are not listed.  ``origins`` maps a
    reference number to ``new`` or ``superseded``; other notices are
    ``carried``.
    """

    events = {
        str(row["notice_id"]): row for row in rows.get("notice_event", ())
    }
    found: list[dict[str, Any]] = []
    for effect in rows.get("notice_effect", ()):
        if (
            effect["effect_type"] != "clause_amendment"
            or effect["projection_status"] != "pending_projection"
            or not effect["clause_code"]
        ):
            continue
        event = events[str(effect["notice_id"])]
        reference = str(event["reference_number"])
        reasons = [
            str(item["blocked_reason"])
            for item in event["unresolved_scope"]
            if isinstance(item, Mapping)
            and item.get("clause_code") == effect["clause_code"]
            and item.get("blocked_reason")
        ]
        found.append(
            {
                "reference_number": reference,
                "clause_code": effect["clause_code"],
                "effective_on": str(event["effective_on"]),
                "reason": reasons[0] if reasons else "pending_projection",
                "scope_note": effect["scope_note"],
                **(
                    {"origin": origins.get(reference, "carried")}
                    if origins is not None
                    else {}
                ),
            }
        )
    return sorted(
        found,
        key=lambda item: (
            item["effective_on"], item["reference_number"], item["clause_code"]
        ),
    )


def _supersede_refusal(
    previous: CarriedNotice, fresh: NoticeRows, base: BaseChain
) -> str | None:
    """Why ``fresh`` must not replace a carried notice's rows, if it must not.

    Superseding changes no served clause text: every clause patch the notice
    serves must come back with the same patch id, text hash and effective
    date, and must still be ``verified_scheduled`` so that no later
    resolution is reset.
    """

    if fresh.notice.notice_id != previous.notice_id:
        return "the notice's source artifact differs from the carried notice"
    if previous.dependent_tables:
        return "the carried notice has dependent rows in " + ", ".join(
            previous.dependent_tables
        )
    again = {row["clause_code"]: row for row in fresh.rows["clause_patch"]}
    for row in sorted(previous.patches, key=lambda item: item["clause_code"]):
        code = row["clause_code"]
        if row["composition_status"] != "patch_only":
            return f"{code} is served as a {row['composition_status']} patch"
        fresh_row = again.get(code)
        if fresh_row is None or any(
            str(fresh_row[key]) != str(row[key])
            for key in (
                "patch_id", "source_exact_patch_sha256", "effective_from"
            )
        ):
            return f"served clause {code} is not re-projected byte-identically"
        state = base.resolutions[str(row["patch_id"])]["resolution_state"]
        if state != "verified_scheduled":
            return (
                f"served clause {code} is {state}; superseding would reset "
                "its resolution"
            )
    return None


def _drop_carried(
    rows: dict[str, list[dict[str, Any]]], notices: Iterable[CarriedNotice]
) -> set[str]:
    """Remove superseded notices' carried rows; return their patch ids."""

    notice_ids: set[str] = set()
    effect_ids: set[str] = set()
    patch_ids: set[str] = set()
    for notice in notices:
        notice_ids.add(notice.notice_id)
        effect_ids.update(str(row["effect_id"]) for row in notice.effects)
        patch_ids.update(str(row["patch_id"]) for row in notice.patches)
    for table in ("notice_event", "notice_effect"):
        rows[table] = [
            row
            for row in rows[table]
            if str(row["notice_id"]) not in notice_ids
        ]
    rows["clause_patch"] = [
        row
        for row in rows["clause_patch"]
        if str(row["effect_id"]) not in effect_ids
    ]
    return patch_ids


@dataclass(frozen=True)
class Composition:
    """One compose decision: the new run, if any, and what it holds back."""

    base_run_id: str
    base_sealed_fingerprint: str
    release: OverlayRelease | None
    carried_notices: tuple[Mapping[str, Any], ...]
    superseded_notices: tuple[Mapping[str, Any], ...]
    failures: tuple[Mapping[str, Any], ...]
    dropped_notices: tuple[Mapping[str, Any], ...]
    blocked_clauses: tuple[Mapping[str, Any], ...]

    @property
    def status(self) -> str:
        return receipt_status(
            composed=self.release is not None,
            failures=self.failures,
            dropped_notices=self.dropped_notices,
            blocked_clauses=self.blocked_clauses,
        )


def compose_overlay_release(
    dsn: str,
    notices: Sequence[ParsedNotice],
    *,
    base_run_id: str | None = None,
    dyslipidemia_odt: Path | None = None,
    today: date | None = None,
    official_renderings: Mapping[str, str | None] | None = None,
    require_official_rendering: bool = False,
    supersede: bool = False,
) -> Composition:
    """Compose a new release run from the active run plus ``notices``.

    ``official_renderings`` maps a reference number to an independent office
    rendering of its comparison-table attachment.  A clause whose parsed text
    is not found verbatim there, or whose revised column draws generated list
    labels, is kept as a pending effect instead of a patch.  A notice whose
    every clause is held back still enters the run with pending effects only.

    Failures are isolated per notice: a notice that is listed twice, lacks a
    required rendering, cannot be bound to the served clauses, would give a
    clause a second patch for one effective date, or cannot be superseded is
    reported in ``failures`` and left out; the rest of the batch composes.

    A notice the base run already carries stays carried unless its fresh
    parse projects a clause the run holds back.  Such a notice is reported in
    ``dropped_notices`` unless ``supersede`` is set; then its carried rows are
    replaced by its fresh projection when :func:`_supersede_refusal` allows
    it, and it fails closed otherwise.  ``release`` is ``None`` when no
    notice is new or superseded.
    """

    renderings = dict(official_renderings or {})
    failures: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    def fail(notice: ParsedNotice, stage: str, error: str) -> None:
        failures.append(
            {**_notice_label(notice), "stage": stage, "error": error}
        )

    listed: dict[str, list[ParsedNotice]] = {}
    for notice in notices:
        listed.setdefault(notice.bundle.reference_number, []).append(notice)
    rendering_checks: dict[str, dict[str, str]] = {}
    candidates: list[ParsedNotice] = []
    for reference, group in sorted(listed.items()):
        if len(group) > 1:
            for notice in group:
                fail(
                    notice, "input", "the notice is listed twice in one batch"
                )
            continue
        notice = group[0]
        rendering = renderings.get(reference)
        if rendering is None and require_official_rendering:
            fail(
                notice,
                "rendering",
                "an independent office rendering is required but unavailable",
            )
            continue
        if not notice.clauses and not notice.other_effects:
            fail(
                notice,
                "parse",
                "the notice states no clause amendment and no other effect",
            )
            continue
        rendering_checks[reference] = {
            clause.clause_code: exact_in_rendering(clause, rendering)
            for clause in notice.clauses
        }
        candidates.append(notice)
    with _connect(dsn, read_only=True) as connection:
        base = read_base_chain(connection, base_run_id)
        carried = _carried_notices(base)
        served_run_id, served = served_clauses(
            connection,
            [
                clause.clause_code
                for notice in candidates
                for clause in notice.clauses
            ],
        )
        preliminary: list[NoticeRows] = []
        superseding: dict[str, CarriedNotice] = {}
        superseded: dict[str, dict[str, Any]] = {}
        carried_notices: list[dict[str, Any]] = []
        for notice in candidates:
            reference = notice.bundle.reference_number
            checks = rendering_checks[reference]
            previous = carried.get(reference)
            if previous is not None:
                served_codes = sorted(
                    {str(row["clause_code"]) for row in previous.patches}
                )
                added = sorted(
                    clause.clause_code
                    for clause in notice.clauses
                    if clause.clause_code not in served_codes
                    and clause_block_reason(
                        clause, checks.get(clause.clause_code)
                    )
                    is None
                )
                if not added:
                    carried_notices.append(
                        {
                            **_notice_label(notice),
                            "notice_id": previous.notice_id,
                        }
                    )
                    continue
                if not supersede:
                    dropped.append(
                        {
                            **_notice_label(notice),
                            "reason": (
                                "carried_notice_has_newly_projectable_clauses"
                            ),
                            "clause_codes": added,
                        }
                    )
                    continue
            try:
                item = notice_rows(
                    notice,
                    run_id=PLACEHOLDER_RUN_ID,
                    served_run_id=served_run_id,
                    served=served,
                    rendering_checks=checks,
                )
            except AnnouncedNoticeError as exc:
                stage = "bind" if previous is None else "supersede"
                fail(notice, stage, str(exc))
                continue
            if previous is not None:
                refusal = _supersede_refusal(previous, item, base)
                if refusal is not None:
                    fail(notice, "supersede", refusal)
                    continue
                superseding[reference] = previous
                superseded[reference] = {
                    **_notice_label(notice),
                    "notice_id": previous.notice_id,
                    "served_clauses": served_codes,
                    "added_clauses": added,
                }
            preliminary.append(item)
        # A run holds one patch per clause and effective date.  A notice that
        # would add a second one is left out rather than guessed between; a
        # superseding notice re-projects its own served keys, so counting
        # them once among the wanted keys keeps them out of the clash.
        kept = {
            (row["clause_code"], row["effective_from"])
            for reference, item in carried.items()
            if reference not in superseding
            for row in item.patches
        }
        wanted = Counter(
            (row["clause_code"], row["effective_from"])
            for item in preliminary
            for row in item.rows["clause_patch"]
        )
        admitted: list[NoticeRows] = []
        for item in preliminary:
            clashes = sorted(
                f"{row['clause_code']} effective {row['effective_from']}"
                for row in item.rows["clause_patch"]
                if (row["clause_code"], row["effective_from"]) in kept
                or wanted[(row["clause_code"], row["effective_from"])] > 1
            )
            if clashes:
                reference = item.notice.bundle.reference_number
                superseding.pop(reference, None)
                superseded.pop(reference, None)
                fail(
                    item.notice,
                    "bind",
                    "another patch amends the same clause on the same date: "
                    + ", ".join(clashes),
                )
                continue
            admitted.append(item)
        if not admitted:
            return Composition(
                base_run_id=str(base.run["run_id"]),
                base_sealed_fingerprint=str(base.run["sealed_fingerprint"]),
                release=None,
                carried_notices=tuple(carried_notices),
                superseded_notices=(),
                failures=tuple(failures),
                dropped_notices=tuple(dropped),
                blocked_clauses=tuple(blocked_clauses(base.rows, {})),
            )
        # The run identity must not depend on the order notices were listed in.
        admitted.sort(key=lambda item: item.notice.bundle.reference_number)
        notice_fingerprints = [
            {
                "reference_number": item.notice.bundle.reference_number,
                "corpus_manifest_sha256": item.notice.bundle.manifest_sha256,
                "source_artifact_sha256": item.notice.attachment.sha256,
                "rows": {
                    table: sorted(
                        row_sha256(
                            {k: v for k, v in row.items() if k != "run_id"},
                            derived_key="source_row_sha256",
                        )
                        for row in rows
                    )
                    for table, rows in item.rows.items()
                },
            }
            for item in admitted
        ]
        input_fingerprint = object_fingerprint(
            {
                "loader_version": LOADER_VERSION,
                "parser_version": PARSER_VERSION,
                "text_rule_version": TEXT_RULE_VERSION,
                "base_run_id": str(base.run["run_id"]),
                "base_sealed_fingerprint": base.run["sealed_fingerprint"],
                "served_publication_run_id": served_run_id,
                "notices": notice_fingerprints,
                "superseded_references": sorted(superseding),
                "code_sha256": _code_sha256(),
            }
        )
        run_id = stable_uuid("overlay-release-run", input_fingerprint)
        rows = _carry(base, run_id)
        superseded_patch_ids = _drop_carried(rows, superseding.values())
        built = [
            notice_rows(
                item.notice,
                run_id=run_id,
                served_run_id=served_run_id,
                served=served,
                rendering_checks=rendering_checks[
                    item.notice.bundle.reference_number
                ],
            )
            for item in admitted
        ]
        for item in built:
            for table, table_rows in item.rows.items():
                rows[table].extend(table_rows)
        patch_keys = [
            (row["clause_code"], row["effective_from"])
            for row in rows["clause_patch"]
        ]
        if len(patch_keys) != len(set(patch_keys)):
            raise AnnouncedReleaseError(
                "two patches share a clause code and effective date"
            )
        material, rebinding_receipt = _rebinding(
            connection, base, run_id=run_id, dyslipidemia_odt=dyslipidemia_odt
        )
    source_set = object_fingerprint(
        sorted(
            (
                {
                    "reference_number": row["reference_number"],
                    "source_artifact_sha256": row["source_artifact_sha256"],
                }
                for row in rows["notice_event"]
            ),
            key=lambda row: row["reference_number"],
        )
    )
    frozen = {
        name: tuple(sorted(value, key=lambda row: json_text(row)))
        for name, value in rows.items()
    }
    all_counts = {name: len(value) for name, value in frozen.items()}
    all_table_fingerprints = {
        name: row_set_fingerprint(row["source_row_sha256"] for row in value)
        for name, value in frozen.items()
        if value and "source_row_sha256" in value[0]
    }
    # The stored seal follows the 2.6.1 loader's convention exactly: counts,
    # table fingerprints and output cover SEALED_COUNT_TABLES only.  The
    # subscriber sync re-runs announced_dyslipidemia.verify_announced_material
    # on the active run every tick and requires dict equality with these keys;
    # the legacy document tables stay proven by their own carried receipt, and
    # verify_overlay_release still checks every carried table at load time.
    expected_counts = {name: all_counts[name] for name in SEALED_COUNT_TABLES}
    table_fingerprints = {
        name: row_set_fingerprint(
            row["source_row_sha256"] for row in frozen.get(name, ())
        )
        for name in SEALED_COUNT_TABLES
    }
    output_fingerprint = object_fingerprint(
        {"counts": expected_counts, "table_fingerprints": table_fingerprints}
    )
    evaluator_version = str(base.run["evaluator_version"])
    sealed_fingerprint = object_fingerprint(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "loader_version": LOADER_VERSION,
            "evaluator_version": evaluator_version,
        }
    )
    today = today or datetime.now(ZoneInfo(CIVIL_TIMEZONE)).date()
    resolutions: list[ResolutionRow] = []
    for patch_id, prior in sorted(base.resolutions.items()):
        if patch_id in superseded_patch_ids:
            continue
        # The served evidence keys stay verbatim; the carry is recorded
        # beside them, chaining any earlier carry.
        prior_evidence = _jsonable(prior["evidence"])
        carried_from = {
            "run_id": str(base.run["run_id"]),
            "resolution_id": int(prior["resolution_id"]),
            "recorded_at": prior["recorded_at"].isoformat(),
            "loader_version": LOADER_VERSION,
            "carried_row_rule": (
                "run_id replaced; every other column identical; stored row "
                "hash replayed before carrying"
            ),
        }
        if "carried_forward" in prior_evidence:
            carried_from["previous"] = prior_evidence["carried_forward"]
        resolutions.append(
            ResolutionRow(
                patch_id=patch_id,
                resolution_state=str(prior["resolution_state"]),
                reason=str(prior["reason"]),
                evidence={**prior_evidence, "carried_forward": carried_from},
            )
        )
    for item in built:
        for patch_id, evidence in sorted(item.patch_evidence.items()):
            effective_on = date.fromisoformat(item.patch_effective[patch_id])
            if today < effective_on:
                reason = (
                    "official comparison table verified before its stated "
                    "effective date"
                )
            else:
                reason = (
                    "official comparison table verified after its stated "
                    "effective date; correction, withdrawal and "
                    "consolidation reconciliation are pending"
                )
            if patch_id in superseded_patch_ids:
                # Same patch id, text and effective date as the served patch;
                # the fresh evidence describes the fresh row, and the served
                # resolution it replaces is named beside it.
                prior = base.resolutions[patch_id]
                evidence = {
                    **evidence,
                    "superseded_projection": {
                        "run_id": str(base.run["run_id"]),
                        "resolution_id": int(prior["resolution_id"]),
                        "resolution_state": str(prior["resolution_state"]),
                        "recorded_at": prior["recorded_at"].isoformat(),
                        "loader_version": LOADER_VERSION,
                        "superseded_row_rule": (
                            "notice re-projected; patch id, text and "
                            "effective date unchanged"
                        ),
                    },
                }
            resolutions.append(
                ResolutionRow(
                    patch_id=patch_id,
                    resolution_state="verified_scheduled",
                    reason=reason,
                    evidence={**evidence, "verified_on": today.isoformat()},
                )
            )
    profile = (
        _profile_rebind(base, run_id=run_id, material=material)
        if material is not None
        else None
    )
    release = OverlayRelease(
        run_id=run_id,
        base=base,
        notices=tuple(built),
        rows=frozen,
        expected_counts=expected_counts,
        all_counts=all_counts,
        table_fingerprints=table_fingerprints,
        all_table_fingerprints=all_table_fingerprints,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        sealed_fingerprint=sealed_fingerprint,
        source_set_sha256=source_set,
        evaluator_version=evaluator_version,
        resolutions=tuple(resolutions),
        rebinding=material,
        profile=profile,
        rebinding_control=rebinding_receipt,
    )
    origins = {
        item.notice.bundle.reference_number: (
            "superseded"
            if item.notice.bundle.reference_number in superseding
            else "new"
        )
        for item in built
    }
    return Composition(
        base_run_id=str(base.run["run_id"]),
        base_sealed_fingerprint=str(base.run["sealed_fingerprint"]),
        release=release,
        carried_notices=tuple(carried_notices),
        superseded_notices=tuple(
            superseded[reference] for reference in sorted(superseding)
        ),
        failures=tuple(failures),
        dropped_notices=tuple(dropped),
        blocked_clauses=tuple(blocked_clauses(frozen, origins)),
    )


def prepare_overlay_release(
    dsn: str,
    notices: Sequence[ParsedNotice],
    *,
    base_run_id: str | None = None,
    dyslipidemia_odt: Path | None = None,
    today: date | None = None,
    official_renderings: Mapping[str, str | None] | None = None,
    require_official_rendering: bool = False,
    supersede: bool = False,
) -> OverlayRelease:
    """Compose strictly: any notice failure, or nothing to compose, raises."""

    composition = compose_overlay_release(
        dsn,
        notices,
        base_run_id=base_run_id,
        dyslipidemia_odt=dyslipidemia_odt,
        today=today,
        official_renderings=official_renderings,
        require_official_rendering=require_official_rendering,
        supersede=supersede,
    )
    if composition.failures:
        raise AnnouncedReleaseError(
            "notice failures: " + json_text(list(composition.failures))
        )
    if composition.release is None:
        raise AnnouncedReleaseError("no notice to compose")
    return composition.release


# ---------------------------------------------------------------------------
# Load


def _self_references(
    connection: Any, table: str
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    rows = connection.execute(
        """
        SELECT con.oid,
               array(
                 SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY
                   AS key(attnum, ord)
                 JOIN pg_attribute att
                   ON att.attrelid = con.conrelid AND att.attnum = key.attnum
                 ORDER BY key.ord
               ) AS child_columns,
               array(
                 SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY
                   AS key(attnum, ord)
                 JOIN pg_attribute att
                   ON att.attrelid = con.confrelid AND att.attnum = key.attnum
                 ORDER BY key.ord
               ) AS parent_columns
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE con.contype = 'f' AND con.conrelid = con.confrelid
          AND ns.nspname = %s AND rel.relname = %s
        """,
        (SCHEMA, table),
    ).fetchall()
    return [
        (tuple(row["child_columns"]), tuple(row["parent_columns"]))
        for row in rows
    ]


def _parent_first(
    rows: Sequence[Mapping[str, Any]],
    references: Sequence[tuple[tuple[str, ...], tuple[str, ...]]],
) -> list[Mapping[str, Any]]:
    """Order rows so a self-referenced row precedes the rows naming it."""

    if not references:
        return list(rows)
    placed: list[Mapping[str, Any]] = []
    keys: list[set[tuple[Any, ...]]] = [set() for _ in references]
    pending = list(rows)
    while pending:
        remaining = []
        for row in pending:
            ready = True
            for index, (child, parent) in enumerate(references):
                wanted = tuple(row.get(column) for column in child)
                own = tuple(row.get(column) for column in parent)
                if any(value is None for value in wanted) or wanted == own:
                    continue
                if wanted not in keys[index]:
                    ready = False
                    break
            if ready:
                placed.append(row)
                for index, (_, parent) in enumerate(references):
                    keys[index].add(tuple(row.get(column) for column in parent))
            else:
                remaining.append(row)
        if len(remaining) == len(pending):
            raise AnnouncedReleaseError("self-referencing rows form a cycle")
        pending = remaining
    return placed


def _insert(
    connection: Any, shape: TableShape, rows: Sequence[Mapping[str, Any]]
) -> None:
    if not rows:
        return
    rows = _parent_first(rows, _self_references(connection, shape.name))
    placeholders = ",".join(
        "%s::jsonb" if column in shape.json_columns else "%s"
        for column in shape.columns
    )
    sql = (
        f"INSERT INTO {SCHEMA}.{shape.name} ({','.join(shape.columns)}) "
        f"VALUES ({placeholders})"
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            sql,
            [
                tuple(
                    (
                        None
                        if row.get(column) is None
                        else json_text(row[column])
                    )
                    if column in shape.json_columns
                    else row.get(column)
                    for column in shape.columns
                )
                for row in rows
            ],
        )


def load_overlay_release(dsn: str, release: OverlayRelease) -> dict[str, Any]:
    """Seal the composite run and its re-bound projections; do not activate."""

    with _connect(dsn, read_only=False) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (GLOBAL_LOCK_KEY,),
        )
        existing = connection.execute(
            f"""
            SELECT run_id, state, sealed_fingerprint
            FROM {SCHEMA}.release_run WHERE input_fingerprint=%s
            """,
            (release.input_fingerprint,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["run_id"]) != release.run_id
                or existing["sealed_fingerprint"] != release.sealed_fingerprint
            ):
                raise AnnouncedReleaseError(
                    "overlay release input collision or loader drift"
                )
            connection.rollback()
            return verify_overlay_release(dsn, release) | {"replayed": True}
        connection.execute(
            f"""
            INSERT INTO {SCHEMA}.release_run (
              run_id, state, loader_version, evaluator_version,
              source_artifact_sha256, input_fingerprint, expected_counts,
              started_at
            ) VALUES (%s,'loading',%s,%s,%s,%s,%s::jsonb,now())
            """,
            (
                release.run_id,
                LOADER_VERSION,
                release.evaluator_version,
                release.source_set_sha256,
                release.input_fingerprint,
                json_text(release.expected_counts),
            ),
        )
        shapes = {shape.name: shape for shape in _run_scoped_tables(connection)}
        if set(shapes) != set(release.rows):
            raise AnnouncedReleaseError(
                "target announced schema differs from the composed release"
            )
        for shape in _run_scoped_tables(connection):
            _insert(connection, shape, release.rows[shape.name])
        sealed = connection.execute(
            f"""
            UPDATE {SCHEMA}.release_run
            SET state='sealed', verified_counts=%s::jsonb,
                table_fingerprints=%s::jsonb, output_fingerprint=%s,
                sealed_fingerprint=%s, sealed_at=now()
            WHERE run_id=%s AND state='loading'
            """,
            (
                json_text(release.expected_counts),
                json_text(release.table_fingerprints),
                release.output_fingerprint,
                release.sealed_fingerprint,
                release.run_id,
            ),
        )
        if sealed.rowcount != 1:
            raise AnnouncedReleaseError("overlay release seal failed")
        for resolution in release.resolutions:
            connection.execute(
                f"SELECT {SCHEMA}.set_patch_resolution(%s,%s,%s,%s,%s::jsonb)",
                (
                    release.run_id,
                    resolution.patch_id,
                    resolution.resolution_state,
                    resolution.reason,
                    json_text(resolution.evidence),
                ),
            )
        if release.rebinding is not None:
            # Same code path and SQL as the 2.6.1 loader; it re-checks the
            # sealed source run and composed text before inserting.
            dyslipidemia._insert_material(
                _CursorAdapter(connection), release.rebinding
            )
        if release.profile is not None:
            _insert_profile(connection, release.profile)
        connection.commit()
    return verify_overlay_release(dsn, release) | {"replayed": False}


class _CursorAdapter:
    """Give the 2.6.1 loader a tuple-row cursor inside our transaction."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def cursor(self) -> Any:
        return self._connection.cursor(row_factory=_tuple_rows())


def _tuple_rows() -> Any:
    from psycopg.rows import tuple_row

    return tuple_row


def _insert_profile(connection: Any, profile: ReaderProfileRebind) -> None:
    row = profile.row
    connection.execute(
        f"""
        INSERT INTO {SCHEMA}.clause_reader_profile_run (
          profile_run_id, state, schema_version, loader_version,
          source_release_run_id, input_fingerprint,
          expected_profile_count, started_at
        ) VALUES (%s,'loading',%s,%s,%s,%s,1,now())
        """,
        (
            profile.profile_run_id,
            reader_profile.PROFILE_CONTRACT,
            LOADER_VERSION,
            row["source_release_run_id"],
            profile.input_fingerprint,
        ),
    )
    content_json = json_text(row["content_payload"])
    actual_sha = connection.execute(
        "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS sha",
        (content_json,),
    ).fetchone()["sha"]
    if actual_sha != row["content_sha256"]:
        raise AnnouncedReleaseError("re-bound profile content hash changed")
    connection.execute(
        f"""
        INSERT INTO {SCHEMA}.clause_reader_profile (
          profile_run_id, profile_id, clause_code, source_release_run_id,
          source_version_id, source_composed_text_sha256, source_diff_run_id,
          source_diff_output_fingerprint, presentation_mode, template_key,
          profile_contract, authoring_method, review_status, disclosure_text,
          content_payload, content_sha256, source_row_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s
        )
        """,
        (
            row["profile_run_id"],
            row["profile_id"],
            row["clause_code"],
            row["source_release_run_id"],
            row["source_version_id"],
            row["source_composed_text_sha256"],
            row["source_diff_run_id"],
            row["source_diff_output_fingerprint"],
            row["presentation_mode"],
            row["template_key"],
            row["profile_contract"],
            row["authoring_method"],
            row["review_status"],
            row["disclosure_text"],
            content_json,
            row["content_sha256"],
            row["source_row_sha256"],
        ),
    )
    sealed = connection.execute(
        f"""
        UPDATE {SCHEMA}.clause_reader_profile_run
        SET state='sealed', verified_profile_count=1, output_fingerprint=%s,
            sealed_fingerprint=%s, sealed_at=now()
        WHERE profile_run_id=%s AND state='loading'
        """,
        (
            profile.output_fingerprint,
            profile.sealed_fingerprint,
            profile.profile_run_id,
        ),
    )
    if sealed.rowcount != 1:
        raise AnnouncedReleaseError("re-bound reader profile seal failed")


# ---------------------------------------------------------------------------
# Fresh-connection verification


def verify_overlay_release(dsn: str, release: OverlayRelease) -> dict[str, Any]:
    with _connect(dsn, read_only=True) as connection:
        run = connection.execute(
            f"SELECT * FROM {SCHEMA}.release_run WHERE run_id=%s",
            (release.run_id,),
        ).fetchone()
        if run is None or run["state"] != "sealed":
            raise AnnouncedReleaseError("fresh read found no sealed overlay run")
        counts: dict[str, int] = {}
        fingerprints: dict[str, str] = {}
        table_hashes: dict[str, list[str]] = {}
        for shape in _run_scoped_tables(connection):
            hashes = [
                row.get("source_row_sha256")
                for row in connection.execute(
                    f"SELECT * FROM {SCHEMA}.{shape.name} WHERE run_id=%s",
                    (release.run_id,),
                ).fetchall()
            ]
            counts[shape.name] = len(hashes)
            if hashes and hashes[0] is not None:
                fingerprints[shape.name] = row_set_fingerprint(hashes)
                table_hashes[shape.name] = hashes
        sealed_counts = {name: counts[name] for name in SEALED_COUNT_TABLES}
        sealed_fingerprints = {
            name: row_set_fingerprint(table_hashes.get(name, ()))
            for name in SEALED_COUNT_TABLES
        }
        output = object_fingerprint(
            {"counts": sealed_counts, "table_fingerprints": sealed_fingerprints}
        )
        if (
            counts != dict(release.all_counts)
            or fingerprints != dict(release.all_table_fingerprints)
            or sealed_fingerprints != dict(release.table_fingerprints)
            or sealed_fingerprints != dict(run["table_fingerprints"])
            or sealed_counts != dict(run["expected_counts"])
            or sealed_counts != dict(run["verified_counts"])
            or output != run["output_fingerprint"]
            or run["sealed_fingerprint"] != release.sealed_fingerprint
        ):
            raise AnnouncedReleaseError("sealed overlay run does not replay")
        resolved = connection.execute(
            f"""
            SELECT count(*) AS n FROM {SCHEMA}.v_current_patch_resolution
            WHERE run_id=%s
            """,
            (release.run_id,),
        ).fetchone()["n"]
        if resolved != counts["clause_patch"]:
            raise AnnouncedReleaseError("overlay patch lacks a resolution")
        receipt: dict[str, Any] = {
            "run_id": release.run_id,
            "input_fingerprint": release.input_fingerprint,
            "output_fingerprint": run["output_fingerprint"],
            "sealed_fingerprint": run["sealed_fingerprint"],
            "source_set_sha256": run["source_artifact_sha256"],
            "counts": counts,
            "resolved_patch_count": int(resolved),
        }
        if release.rebinding is not None:
            normalization = connection.execute(
                f"""
                SELECT state, sealed_fingerprint, source_release_run_id
                FROM {SCHEMA}.clause_document_normalization_run
                WHERE normalization_run_id=%s
                """,
                (release.normalization_run_id,),
            ).fetchone()
            diff = connection.execute(
                f"""
                SELECT state, sealed_fingerprint, output_fingerprint
                FROM {SCHEMA}.clause_document_diff_run WHERE diff_run_id=%s
                """,
                (release.diff_run_id,),
            ).fetchone()
            if (
                normalization is None
                or normalization["state"] != "sealed"
                or str(normalization["source_release_run_id"]) != release.run_id
                or normalization["sealed_fingerprint"]
                != release.rebinding.normalization_sealed_fingerprint
                or diff is None
                or diff["state"] != "sealed"
                or diff["sealed_fingerprint"]
                != release.rebinding.diff_sealed_fingerprint
            ):
                raise AnnouncedReleaseError("re-bound 2.6.1 projections differ")
            receipt["normalization_run_id"] = release.normalization_run_id
            receipt["diff_run_id"] = release.diff_run_id
            receipt["diff_output_fingerprint"] = diff["output_fingerprint"]
        if release.profile is not None:
            profile = connection.execute(
                f"""
                SELECT state, sealed_fingerprint
                FROM {SCHEMA}.clause_reader_profile_run
                WHERE profile_run_id=%s
                """,
                (release.profile.profile_run_id,),
            ).fetchone()
            if (
                profile is None
                or profile["state"] != "sealed"
                or profile["sealed_fingerprint"]
                != release.profile.sealed_fingerprint
            ):
                raise AnnouncedReleaseError("re-bound reader profile differs")
            receipt["reader_profile_run_id"] = release.profile.profile_run_id
            receipt["reader_profile_id"] = release.profile.profile_id
    return receipt


# ---------------------------------------------------------------------------
# Activation and rollback


def activate_overlay_release(
    dsn: str,
    *,
    run_id: str,
    expected_sealed_fingerprint: str,
    expected_base_run_id: str,
    reason: str = "announced overlay loader activation",
    compose_holds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a loaded overlay run the served run, with its re-bound chain.

    ``compose_holds`` (the failures and dropped notices of the load that
    sealed this run) is recorded in the activation evidence when supplied.
    """

    with _connect(dsn, read_only=False) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (GLOBAL_LOCK_KEY,),
        )
        run = connection.execute(
            f"SELECT * FROM {SCHEMA}.release_run WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or run["state"] != "sealed"
            or run["sealed_fingerprint"] != expected_sealed_fingerprint
            or run["loader_version"] != LOADER_VERSION
        ):
            raise AnnouncedReleaseError("overlay run is not the expected sealed run")
        active = connection.execute(
            f"SELECT run_id FROM {SCHEMA}.v_active_run"
        ).fetchone()
        if active is None or str(active["run_id"]) != expected_base_run_id:
            raise AnnouncedReleaseError(
                "the served run is not the expected base run"
            )
        _require_served_rows_carried(
            connection, served_run_id=expected_base_run_id, run_id=run_id
        )
        carries_dyslipidemia = connection.execute(
            f"""
            SELECT 1 FROM {SCHEMA}.composed_clause_version
            WHERE run_id=%s AND clause_code=%s
            LIMIT 1
            """,
            (run_id, DYSLIPIDEMIA_CLAUSE),
        ).fetchone()
        if carries_dyslipidemia is not None:
            # The subscriber sync runs this exact read-only check on the
            # active run every tick; a run it rejects must never be served.
            try:
                dyslipidemia.verify_announced_material(run_id, conninfo=dsn)
            except dyslipidemia.AnnouncedDyslipidemiaError as exc:
                raise AnnouncedReleaseError(
                    "the 2.6.1 receipt check that the subscriber sync runs "
                    f"rejects this run: {exc}"
                ) from exc
        previous = _served_chain(connection)
        patches = connection.execute(
            f"""
            SELECT patch.patch_id, resolution.resolution_state
            FROM {SCHEMA}.clause_patch patch
            LEFT JOIN {SCHEMA}.v_current_patch_resolution resolution
              USING (run_id, patch_id)
            WHERE patch.run_id=%s
            """,
            (run_id,),
        ).fetchall()
        if not patches or any(row["resolution_state"] is None for row in patches):
            raise AnnouncedReleaseError("overlay patch lacks a resolution")
        new_chain = _loaded_chain(connection, run_id)
        if previous["normalization_run_id"] and not new_chain["normalization_run_id"]:
            raise AnnouncedReleaseError(
                "served clause normalization has no re-bound successor"
            )
        evidence = {
            "loader_version": LOADER_VERSION,
            "sealed_fingerprint": expected_sealed_fingerprint,
            "previous": previous,
            "activated": new_chain,
            **(
                {"compose_holds": dict(compose_holds)}
                if compose_holds is not None
                else {}
            ),
        }
        connection.execute(
            f"SELECT {SCHEMA}.set_release_control(%s,'activate',%s,%s::jsonb)",
            (run_id, reason, json_text(evidence)),
        )
        _activate_chain(connection, new_chain, reason=reason, evidence=evidence)
        connection.commit()
    return verify_served_chain(dsn, expected=new_chain) | {"previous": previous}


def rollback_overlay_release(
    dsn: str,
    *,
    from_run_id: str,
    reason: str = "announced overlay rollback",
) -> dict[str, Any]:
    """Re-activate the chain recorded when ``from_run_id`` was activated."""

    with _connect(dsn, read_only=False) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (GLOBAL_LOCK_KEY,),
        )
        active = connection.execute(
            f"SELECT run_id FROM {SCHEMA}.v_active_run"
        ).fetchone()
        if active is None or str(active["run_id"]) != from_run_id:
            raise AnnouncedReleaseError("the served run is not the rollback source")
        activation = connection.execute(
            f"""
            SELECT evidence FROM {SCHEMA}.release_control_event
            WHERE run_id=%s AND action='activate'
              AND evidence ? 'previous'
            ORDER BY control_id DESC LIMIT 1
            """,
            (from_run_id,),
        ).fetchone()
        if activation is None:
            raise AnnouncedReleaseError("no recorded previous chain to restore")
        previous = activation["evidence"]["previous"]
        evidence = {
            "loader_version": LOADER_VERSION,
            "rolled_back_run_id": from_run_id,
            "restored": previous,
        }
        connection.execute(
            f"SELECT {SCHEMA}.set_release_control(%s,'activate',%s,%s::jsonb)",
            (previous["release_run_id"], reason, json_text(evidence)),
        )
        _activate_chain(connection, previous, reason=reason, evidence=evidence)
        connection.commit()
    return verify_served_chain(dsn, expected=previous)


def _require_served_rows_carried(
    connection: Any, *, served_run_id: str, run_id: str
) -> None:
    """Refuse a run that would stop serving a served notice or patch text.

    A composite carries every row of its base and a supersede re-projects
    served patches byte-identically, so each served notice and each served
    clause patch (id, text hash, effective date) must exist in the new run.
    """

    def notices(run: str) -> set[str]:
        return {
            str(row["reference_number"])
            for row in connection.execute(
                f"SELECT reference_number FROM {SCHEMA}.notice_event "
                "WHERE run_id=%s",
                (run,),
            ).fetchall()
        }

    def patches(run: str) -> set[tuple[str, str, str, str]]:
        return {
            (
                str(row["clause_code"]),
                str(row["patch_id"]),
                str(row["source_exact_patch_sha256"]),
                row["effective_from"].isoformat(),
            )
            for row in connection.execute(
                f"""
                SELECT clause_code, patch_id, source_exact_patch_sha256,
                       effective_from
                FROM {SCHEMA}.clause_patch WHERE run_id=%s
                """,
                (run,),
            ).fetchall()
        }

    lost_notices = sorted(notices(served_run_id) - notices(run_id))
    lost_patches = sorted(
        {row[0] for row in patches(served_run_id) - patches(run_id)}
    )
    if lost_notices or lost_patches:
        raise AnnouncedReleaseError(
            "the run does not carry everything the served run serves: "
            + json_text(
                {"notices": lost_notices, "clause_patches": lost_patches}
            )
        )


def _run_blocked_clauses(connection: Any, run_id: str) -> list[dict[str, Any]]:
    rows = {
        table: [
            {key: _jsonable(value) for key, value in row.items()}
            for row in connection.execute(
                f"SELECT * FROM {SCHEMA}.{table} WHERE run_id=%s", (run_id,)
            ).fetchall()
        ]
        for table in ("notice_event", "notice_effect")
    }
    return blocked_clauses(rows)


def _served_chain(connection: Any) -> dict[str, Any]:
    release = connection.execute(
        f"SELECT run_id FROM {SCHEMA}.v_active_run"
    ).fetchone()
    normalization = connection.execute(
        f"""
        SELECT normalization_run_id
        FROM {SCHEMA}.v_active_clause_document_normalization_run
        """
    ).fetchone()
    diff = connection.execute(
        f"SELECT diff_run_id FROM {SCHEMA}.v_active_clause_document_diff_run"
    ).fetchone()
    profiles = connection.execute(
        f"""
        SELECT profile.profile_run_id, pub.profile_id
        FROM {SCHEMA}.v_public_clause_reader_profile pub
        JOIN {SCHEMA}.clause_reader_profile profile USING (profile_id)
        WHERE pub.source_release_run_id=%s
        ORDER BY pub.profile_id
        """,
        (release["run_id"] if release else None,),
    ).fetchall()
    return {
        "release_run_id": str(release["run_id"]) if release else None,
        "normalization_run_id": (
            str(normalization["normalization_run_id"]) if normalization else None
        ),
        "diff_run_id": str(diff["diff_run_id"]) if diff else None,
        "reader_profiles": [
            {
                "profile_run_id": str(row["profile_run_id"]),
                "profile_id": str(row["profile_id"]),
            }
            for row in profiles
        ],
    }


def _loaded_chain(connection: Any, run_id: str) -> dict[str, Any]:
    normalization = connection.execute(
        f"""
        SELECT normalization_run_id FROM {SCHEMA}.clause_document_normalization_run
        WHERE source_release_run_id=%s AND state='sealed'
        """,
        (run_id,),
    ).fetchall()
    if len(normalization) > 1:
        raise AnnouncedReleaseError("several normalization runs bind one release")
    normalization_run_id = (
        str(normalization[0]["normalization_run_id"]) if normalization else None
    )
    diff_run_id = None
    if normalization_run_id:
        diff = connection.execute(
            f"""
            SELECT diff_run_id FROM {SCHEMA}.clause_document_diff_run
            WHERE normalization_run_id=%s AND state='sealed'
            """,
            (normalization_run_id,),
        ).fetchall()
        if len(diff) != 1:
            raise AnnouncedReleaseError("re-bound diff run is missing or ambiguous")
        diff_run_id = str(diff[0]["diff_run_id"])
    profiles = connection.execute(
        f"""
        SELECT profile.profile_run_id, profile.profile_id
        FROM {SCHEMA}.clause_reader_profile profile
        JOIN {SCHEMA}.clause_reader_profile_run run USING (profile_run_id)
        WHERE profile.source_release_run_id=%s AND run.state='sealed'
          AND profile.source_diff_run_id=%s
        ORDER BY profile.profile_id
        """,
        (run_id, diff_run_id),
    ).fetchall()
    return {
        "release_run_id": run_id,
        "normalization_run_id": normalization_run_id,
        "diff_run_id": diff_run_id,
        "reader_profiles": [
            {
                "profile_run_id": str(row["profile_run_id"]),
                "profile_id": str(row["profile_id"]),
            }
            for row in profiles
        ],
    }


def _activate_chain(
    connection: Any,
    chain: Mapping[str, Any],
    *,
    reason: str,
    evidence: Mapping[str, Any],
) -> None:
    receipt = json_text(evidence)
    if chain.get("normalization_run_id"):
        connection.execute(
            f"""
            SELECT {SCHEMA}.set_clause_document_normalization_control(
              %s,'activate',%s,%s::jsonb)
            """,
            (chain["normalization_run_id"], reason, receipt),
        )
    if chain.get("diff_run_id"):
        connection.execute(
            f"""
            SELECT {SCHEMA}.set_clause_document_diff_control(
              %s,'activate',%s,%s::jsonb)
            """,
            (chain["diff_run_id"], reason, receipt),
        )
    recorded_at = datetime.now(timezone.utc)
    for profile in chain.get("reader_profiles") or ():
        control_row = {
            "control_event_id": reader_profile._stable_uuid(
                "reader-profile-control",
                [
                    profile["profile_id"],
                    "activate",
                    chain["release_run_id"],
                    recorded_at.isoformat(),
                ],
            ),
            "profile_run_id": profile["profile_run_id"],
            "profile_id": profile["profile_id"],
            "action": "activate",
            "reason": reason,
            "recorded_at": recorded_at.isoformat(),
        }
        connection.execute(
            f"""
            INSERT INTO {SCHEMA}.clause_reader_profile_control_event (
              control_event_id, profile_run_id, profile_id, action, reason,
              recorded_at, source_row_sha256
            ) VALUES (%s,%s,%s,'activate',%s,%s,%s)
            """,
            (
                control_row["control_event_id"],
                profile["profile_run_id"],
                profile["profile_id"],
                reason,
                recorded_at,
                row_sha256(control_row, derived_key="source_row_sha256"),
            ),
        )


def verify_served_chain(
    dsn: str, *, expected: Mapping[str, Any]
) -> dict[str, Any]:
    """Read the served views through a fresh connection."""

    run_id = str(expected["release_run_id"])
    with _connect(dsn, read_only=True) as connection:
        chain = _served_chain(connection)
        if chain != {
            "release_run_id": run_id,
            "normalization_run_id": expected.get("normalization_run_id"),
            "diff_run_id": expected.get("diff_run_id"),
            "reader_profiles": list(expected.get("reader_profiles") or ()),
        }:
            raise AnnouncedReleaseError(
                "served announced chain differs from the requested chain"
            )
        patches = connection.execute(
            f"""
            SELECT clause_code, effective_from, display_lifecycle,
                   composition_status, current_resolution_state,
                   decision_aid_available
            FROM {SCHEMA}.v_public_clause_patch
            ORDER BY effective_from, clause_code
            """
        ).fetchall()
        stored = connection.execute(
            f"SELECT count(*) AS n FROM {SCHEMA}.clause_patch WHERE run_id=%s",
            (run_id,),
        ).fetchone()["n"]
        if len(patches) != stored:
            raise AnnouncedReleaseError("a sealed patch is not served")
        blocked = _run_blocked_clauses(connection, run_id)
    return {
        "served": chain,
        "blocked_clauses": blocked,
        "public_patch_count": len(patches),
        "public_patches": [
            {
                "clause_code": row["clause_code"],
                "effective_from": row["effective_from"].isoformat(),
                "display_lifecycle": row["display_lifecycle"],
                "composition_status": row["composition_status"],
                "current_resolution_state": row["current_resolution_state"],
                "decision_aid_available": row["decision_aid_available"],
            }
            for row in patches
        ],
    }
