"""Build and load the current single-clause publication read model.

The official chapter ODTs are the sole current-text authority.  This module
materializes their sealed structural parse into immutable PostgreSQL rows for
websites and APIs.  History gaps follow the project-owner policy: the number of
distinct valid ROC dates in the current clause, with a minimum of one, is the
expected version count.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from nhi_rule_history.annotation_stage import extract_roc_date_markers
from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.current_anchor_clause_parity import (
    analyze_current_anchor_clause_parity,
)
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


SCHEMA = "nhi_rule_history_publication"
STRUCTURAL_SCHEMA = "tw_drug_history_structural_stage"
ACQUISITION_SCHEMA = "tw_drug_history_acq_stage"
CLAUSE_SCHEMA = "nhi_rule_history_clause"
EDITION_SCHEMA = "nhi_rule_history_edition"
GLOBAL_LOCK_KEY = "nhi-rule-history-current-publication-global"
AUTHORITY_PAGE_URL = (
    "https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html"
)
SOURCE_POLICY_ID = "nhi-current-chapter-page-v1"
VERSION_COUNT_POLICY = (
    "distinct_valid_current_text_roc_dates_minimum_one/v1"
)
LOADER_VERSION = "nhi-rule-history/current-publication-loader/1.0.0"
EXTRACTOR_VERSION = "nhi-rule-history/current-single-clause/1.0.0"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_current_publication_v18.sql"
)
_UUID_NAMESPACE = uuid.UUID("8f94898d-7933-4c6a-940e-794f5946bba3")
_SINGLE_QUOTES = frozenset({"'", "’", "‘", "＇", "ʼ"})
_GENERAL_DESIGNATION_RE = re.compile(r"^通則:(?P<ordinal>[1-9][0-9]*)$")
_DOTTED_CODE_RE = re.compile(r"^(?P<chapter>[1-9][0-9]*)(?:[.][0-9]+)+$")
_GENERAL_HEADING_RE = re.compile(
    r"^[一二三四五六七八九十百]+\s*、\s*(?P<body>.*)$"
)


class CurrentPublicationError(PgLoadError):
    """Unsafe current-clause projection or PostgreSQL load."""


@dataclass(frozen=True)
class CurrentPublicationMaterial:
    run_id: str
    source_parse_run_id: str
    source_acquisition_run_id: str
    whole_split_parity_status: str
    clauses: tuple[dict[str, Any], ...]
    blocks: tuple[dict[str, Any], ...]
    dates: tuple[dict[str, Any], ...]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    input_fingerprint: str
    output_fingerprint: str
    migration_sha256: str
    code_sha256: str
    sealed_fingerprint: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_uuid(label: str, value: object) -> str:
    material = canonical_json_bytes([label, value]).decode("utf-8")
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def semantic_comparison_text(value: str) -> str:
    """Normalize the three reader-ignored change classes for state matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and char not in _SINGLE_QUOTES
    )


def version_inventory(
    valid_distinct_roc_dates: Iterable[str],
    reconstructed_version_count: int,
) -> dict[str, Any]:
    """Apply the owner-approved current-text annotation count policy."""

    date_values = sorted(set(valid_distinct_roc_dates))
    if reconstructed_version_count < 1:
        raise CurrentPublicationError(
            "reconstructed_version_count must be at least one"
        )
    expected = max(1, len(date_values))
    missing = max(0, expected - reconstructed_version_count)
    underflow = expected < reconstructed_version_count
    if underflow:
        status = "annotation_count_underflows_reconstructed_evidence"
    elif missing:
        status = "history_full_text_missing"
    else:
        status = "complete_under_annotation_policy"
    return {
        "valid_distinct_roc_date_count": len(date_values),
        "expected_version_count": expected,
        "reconstructed_version_count": reconstructed_version_count,
        "missing_version_count": missing,
        "annotation_count_underflows_reconstructed": underflow,
        "inventory_status": status,
    }


def _canonical_code(designation: str) -> tuple[str, int, str]:
    general = _GENERAL_DESIGNATION_RE.fullmatch(designation)
    if general:
        return (
            f"0.{int(general.group('ordinal'))}",
            0,
            "project_assigned",
        )
    dotted = _DOTTED_CODE_RE.fullmatch(designation)
    if dotted:
        return designation, int(dotted.group("chapter")), "official_source"
    raise CurrentPublicationError(
        f"unsupported current clause designation: {designation}"
    )


def _display_title(clause_code: str, first_block_text: str) -> str:
    text = unicodedata.normalize("NFKC", first_block_text).strip()
    general = _GENERAL_HEADING_RE.match(text)
    if general:
        text = general.group("body")
    else:
        text = re.sub(
            rf"^{re.escape(clause_code)}[.]?\s*",
            "",
            text,
            count=1,
        )
    text = text.strip().rstrip("：:。；;")
    if not text:
        return clause_code
    for delimiter in ("：", ":"):
        if delimiter in text and text.index(delimiter) <= 48:
            text = text.split(delimiter, 1)[0]
    return f"{text[:63]}…" if len(text) > 64 else text


def _read_blocks(stage_dir: Path) -> dict[str, dict[int, dict[str, Any]]]:
    path = stage_dir / "structural-blocks.jsonl"
    by_artifact: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise CurrentPublicationError(
            "structural-blocks.jsonl is missing"
        ) from exc
    with stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CurrentPublicationError(
                    f"structural-blocks.jsonl:{line_number} is invalid"
                ) from exc
            artifact = row.get("artifact_sha256")
            locator = row.get("locator")
            order = (
                locator.get("doc_order")
                if isinstance(locator, dict)
                else None
            )
            if not isinstance(artifact, str) or not isinstance(order, int):
                raise CurrentPublicationError(
                    "structural block has no artifact/order identity"
                )
            if order in by_artifact[artifact]:
                raise CurrentPublicationError(
                    "structural block doc_order is duplicated"
                )
            by_artifact[artifact][order] = row
    return by_artifact


def prepare_current_publication(
    stage_dir: Path,
    *,
    source_acquisition_run_id: str,
    source_urls: Mapping[str, str],
    reconstructed_version_counts: Mapping[str, int] | None = None,
) -> CurrentPublicationMaterial:
    """Materialize exact current clauses from one sealed structural stage."""

    stage_dir = Path(stage_dir)
    try:
        manifest = json.loads(
            (stage_dir / "structural-manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurrentPublicationError(
            "structural manifest is unreadable"
        ) from exc
    source_parse_run_id = str(manifest.get("parse_run_id", ""))
    try:
        uuid.UUID(source_parse_run_id)
        uuid.UUID(source_acquisition_run_id)
    except ValueError as exc:
        raise CurrentPublicationError(
            "source parse/acquisition run id is invalid"
        ) from exc

    report = analyze_current_anchor_clause_parity(stage_dir)
    if (
        report.get("status") not in {"parity_passed", "parity_failed"}
        or report.get("blockers")
        or report.get("claims", {}).get("full_clause_text_compared")
        is not True
    ):
        raise CurrentPublicationError(
            "current split clauses did not pass structural reconstruction"
        )
    split_clauses = report.get("split", {}).get("clauses")
    if not isinstance(split_clauses, list) or not split_clauses:
        raise CurrentPublicationError(
            "current split clause receipts are missing"
        )

    block_index = _read_blocks(stage_dir)
    reconstructed = dict(reconstructed_version_counts or {})
    clause_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    date_rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    for receipt in split_clauses:
        designation = str(receipt["designation"])
        clause_code, chapter_number, code_origin = _canonical_code(
            designation
        )
        if clause_code in seen_codes:
            raise CurrentPublicationError(
                f"duplicate current clause code: {clause_code}"
            )
        seen_codes.add(clause_code)
        artifact_sha256 = str(receipt["artifact_sha256"])
        span = receipt["source_span"]
        start = int(span["start_doc_order"])
        end = int(span["end_doc_order_exclusive"])
        artifact_blocks = block_index.get(artifact_sha256, {})
        try:
            selected_blocks = [
                artifact_blocks[order] for order in range(start, end)
            ]
        except KeyError as exc:
            raise CurrentPublicationError(
                f"current clause {clause_code} has a broken source span"
            ) from exc
        if len(selected_blocks) != int(span["block_count"]):
            raise CurrentPublicationError(
                f"current clause {clause_code} block count differs"
            )

        raw_text = "\n".join(
            str(block.get("raw_text", "")) for block in selected_blocks
        )
        normalized_text = unicodedata.normalize("NFKC", raw_text)
        comparison_text = semantic_comparison_text(raw_text)
        if not raw_text or not normalized_text or not comparison_text:
            raise CurrentPublicationError(
                f"current clause {clause_code} has empty materialized text"
            )

        markers_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for marker in extract_roc_date_markers(raw_text):
            date_value = marker["normalized_iso_candidate"]
            if marker["normalization_status"] == "normalized" and date_value:
                markers_by_date[str(date_value)].append(marker)
        inventory = version_inventory(
            markers_by_date,
            max(1, int(reconstructed.get(clause_code, 1))),
        )
        source_resource_id = str(receipt["source_resource_id"])
        source_url = source_urls.get(source_resource_id)
        if not isinstance(source_url, str) or not source_url.startswith(
            "https://"
        ):
            raise CurrentPublicationError(
                f"no official source URL for {source_resource_id}"
            )

        clause_row = {
            "clause_code": clause_code,
            "chapter_number": chapter_number,
            "source_designation": designation,
            "code_origin": code_origin,
            "display_title": _display_title(
                clause_code, str(selected_blocks[0]["raw_text"])
            ),
            "source_acquisition_run_id": source_acquisition_run_id,
            "source_resource_id": source_resource_id,
            "source_url": source_url,
            "source_label": str(receipt["source_label"]),
            "source_artifact_sha256": artifact_sha256,
            "source_span": span,
            "raw_text": raw_text,
            "raw_text_sha256": _sha256_text(raw_text),
            "normalized_text": normalized_text,
            "normalized_text_sha256": _sha256_text(normalized_text),
            "comparison_text": comparison_text,
            "comparison_sha256": _sha256_text(comparison_text),
            **inventory,
        }
        clause_rows.append(clause_row)

        for block_order, block in enumerate(selected_blocks):
            block_row = {
                "clause_code": clause_code,
                "block_order": block_order,
                "source_block_id": str(block["block_id"]),
                "block_kind": str(block["block_kind"]),
                "container": str(block["container"]),
                "raw_text": str(block["raw_text"]),
                "raw_text_sha256": str(block["raw_text_sha256"]),
                "source_locator": block["locator"],
            }
            block_rows.append(block_row)

        for date_value, markers in sorted(markers_by_date.items()):
            date_rows.append(
                {
                    "clause_code": clause_code,
                    "date_value": date_value,
                    "raw_expressions": [
                        marker["raw_expression"] for marker in markers
                    ],
                    "occurrence_count": len(markers),
                }
            )

    clause_rows.sort(key=lambda row: (row["chapter_number"], row["clause_code"]))
    block_rows.sort(
        key=lambda row: (row["clause_code"], row["block_order"])
    )
    date_rows.sort(key=lambda row: (row["clause_code"], row["date_value"]))

    input_fingerprint = object_fingerprint(
        {
            "source_parse_run_id": source_parse_run_id,
            "source_acquisition_run_id": source_acquisition_run_id,
            "source_policy_id": SOURCE_POLICY_ID,
            "authority_page_url": AUTHORITY_PAGE_URL,
            "version_count_policy": VERSION_COUNT_POLICY,
            "whole_split_parity_status": report["status"],
            "reconstructed_version_counts": dict(sorted(reconstructed.items())),
            "clause_source_receipts": [
                {
                    "clause_code": row["clause_code"],
                    "source_artifact_sha256": row[
                        "source_artifact_sha256"
                    ],
                    "source_span": row["source_span"],
                    "raw_text_sha256": row["raw_text_sha256"],
                }
                for row in clause_rows
            ],
        }
    )
    run_id = _stable_uuid("current-publication", input_fingerprint)

    for rows, schema_name in (
        (clause_rows, "current-clause/v1"),
        (block_rows, "current-clause-block/v1"),
        (date_rows, "current-clause-date/v1"),
    ):
        for row in rows:
            row["run_id"] = run_id
            row["source_row_sha256"] = row_sha256(
                {"schema": schema_name, **row}
            )

    expected_counts = {
        "current_clause": len(clause_rows),
        "current_clause_block": len(block_rows),
        "current_clause_date": len(date_rows),
    }
    table_fingerprints = {
        "current_clause": row_set_fingerprint(
            row["source_row_sha256"] for row in clause_rows
        ),
        "current_clause_block": row_set_fingerprint(
            row["source_row_sha256"] for row in block_rows
        ),
        "current_clause_date": row_set_fingerprint(
            row["source_row_sha256"] for row in date_rows
        ),
    }
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "table_fingerprints": table_fingerprints,
        }
    )
    migration_sha256 = migration_fingerprint(MIGRATION)
    code_sha256 = code_fingerprint(
        Path(__file__),
        Path(__file__).with_name("annotation_stage.py"),
        Path(__file__).with_name("current_anchor_clause_parity.py"),
    )
    sealed_fingerprint = object_fingerprint(
        {
            "loader_version": LOADER_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
        }
    )
    return CurrentPublicationMaterial(
        run_id=run_id,
        source_parse_run_id=source_parse_run_id,
        source_acquisition_run_id=source_acquisition_run_id,
        whole_split_parity_status=str(report["status"]),
        clauses=tuple(clause_rows),
        blocks=tuple(block_rows),
        dates=tuple(date_rows),
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        migration_sha256=migration_sha256,
        code_sha256=code_sha256,
        sealed_fingerprint=sealed_fingerprint,
    )


def _source_context(
    connection: Any,
    source_parse_run_id: str,
) -> tuple[str, dict[str, str], dict[str, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT acquisition_run_id, state
            FROM {STRUCTURAL_SCHEMA}.parse_run
            WHERE parse_run_id = %s
            """,
            (source_parse_run_id,),
        )
        parse_run = cursor.fetchone()
        if parse_run is None or parse_run[1] != "sealed":
            raise CurrentPublicationError(
                "source structural parse is not sealed in PostgreSQL"
            )
        acquisition_run_id = str(parse_run[0])
        cursor.execute(
            f"""
            SELECT resource_id, source_url
            FROM {ACQUISITION_SCHEMA}.discovered_resource
            WHERE run_id = %s
            """,
            (acquisition_run_id,),
        )
        source_urls = {
            str(resource_id): str(source_url)
            for resource_id, source_url in cursor.fetchall()
        }
        cursor.execute(
            f"""
            SELECT clause.canonical_code, count(*)::integer
            FROM {CLAUSE_SCHEMA}.clause clause
            JOIN {CLAUSE_SCHEMA}.clause_version version
              ON version.clause_id = clause.clause_id
            JOIN {CLAUSE_SCHEMA}.import_run import_run
              ON import_run.run_id = version.first_import_run_id
             AND import_run.state = 'sealed'
            GROUP BY clause.canonical_code
            """
        )
        reconstructed = {
            str(code): int(count) for code, count in cursor.fetchall()
        }
    return acquisition_run_id, source_urls, reconstructed


def _insert_material(connection: Any, material: CurrentPublicationMaterial) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (GLOBAL_LOCK_KEY,),
        )
        cursor.execute(
            f"""
            SELECT run_id, sealed_fingerprint
            FROM {SCHEMA}.publication_run
            WHERE input_fingerprint = %s
            """,
            (material.input_fingerprint,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if (
                str(existing[0]) != material.run_id
                or existing[1] != material.sealed_fingerprint
            ):
                raise CurrentPublicationError(
                    "publication input collision or loader drift"
                )
            return True

        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.publication_run (
              run_id, source_parse_run_id, source_acquisition_run_id,
              source_policy_id, state, loader_version, extractor_version,
              version_count_policy, authority_page_url, input_fingerprint,
              whole_split_parity_status, expected_counts, started_at
            ) VALUES (
              %s,%s,%s,%s,'loading',%s,%s,%s,%s,%s,%s,%s::jsonb,now()
            )
            """,
            (
                material.run_id,
                material.source_parse_run_id,
                material.source_acquisition_run_id,
                SOURCE_POLICY_ID,
                LOADER_VERSION,
                EXTRACTOR_VERSION,
                VERSION_COUNT_POLICY,
                AUTHORITY_PAGE_URL,
                material.input_fingerprint,
                material.whole_split_parity_status,
                json_text(material.expected_counts),
            ),
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.current_clause (
              run_id, clause_code, chapter_number, source_designation,
              code_origin, display_title, source_acquisition_run_id,
              source_resource_id, source_url, source_label,
              source_artifact_sha256, source_span, raw_text, raw_text_sha256,
              normalized_text, normalized_text_sha256, comparison_text,
              comparison_sha256, valid_distinct_roc_date_count,
              expected_version_count, reconstructed_version_count,
              missing_version_count,
              annotation_count_underflows_reconstructed, inventory_status,
              source_row_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,
              %s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            [
                (
                    row["run_id"],
                    row["clause_code"],
                    row["chapter_number"],
                    row["source_designation"],
                    row["code_origin"],
                    row["display_title"],
                    row["source_acquisition_run_id"],
                    row["source_resource_id"],
                    row["source_url"],
                    row["source_label"],
                    row["source_artifact_sha256"],
                    json_text(row["source_span"]),
                    row["raw_text"],
                    row["raw_text_sha256"],
                    row["normalized_text"],
                    row["normalized_text_sha256"],
                    row["comparison_text"],
                    row["comparison_sha256"],
                    row["valid_distinct_roc_date_count"],
                    row["expected_version_count"],
                    row["reconstructed_version_count"],
                    row["missing_version_count"],
                    row["annotation_count_underflows_reconstructed"],
                    row["inventory_status"],
                    row["source_row_sha256"],
                )
                for row in material.clauses
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.current_clause_block (
              run_id, clause_code, block_order, source_block_id, block_kind,
              container, raw_text, raw_text_sha256, source_locator,
              source_row_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
            )
            """,
            [
                (
                    row["run_id"],
                    row["clause_code"],
                    row["block_order"],
                    row["source_block_id"],
                    row["block_kind"],
                    row["container"],
                    row["raw_text"],
                    row["raw_text_sha256"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.blocks
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.current_clause_date (
              run_id, clause_code, date_value, raw_expressions,
              occurrence_count, source_row_sha256
            ) VALUES (
              %s,%s,%s,%s::jsonb,%s,%s
            )
            """,
            [
                (
                    row["run_id"],
                    row["clause_code"],
                    row["date_value"],
                    json_text(row["raw_expressions"]),
                    row["occurrence_count"],
                    row["source_row_sha256"],
                )
                for row in material.dates
            ],
        )
        cursor.execute(
            f"""
            UPDATE {SCHEMA}.publication_run
            SET state = 'sealed',
                verified_counts = %s::jsonb,
                table_fingerprints = %s::jsonb,
                output_fingerprint = %s,
                sealed_fingerprint = %s,
                sealed_at = now()
            WHERE run_id = %s AND state = 'loading'
            """,
            (
                json_text(material.expected_counts),
                json_text(material.table_fingerprints),
                material.output_fingerprint,
                material.sealed_fingerprint,
                material.run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CurrentPublicationError(
                "publication seal transition failed"
            )
    return False


def verify_current_publication(
    run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: CurrentPublicationMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, verified_counts,
                       table_fingerprints, output_fingerprint,
                       sealed_fingerprint
                FROM {SCHEMA}.publication_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise CurrentPublicationError(
                    "fresh verification found no sealed publication"
                )
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in (
                "current_clause",
                "current_clause_block",
                "current_clause_date",
            ):
                cursor.execute(
                    f"""
                    SELECT source_row_sha256
                    FROM {SCHEMA}.{table}
                    WHERE run_id = %s
                    ORDER BY source_row_sha256
                    """,
                    (run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT
                  count(*)::integer,
                  sum(expected_version_count)::bigint,
                  sum(reconstructed_version_count)::bigint,
                  sum(missing_version_count)::bigint,
                  count(*) FILTER (
                    WHERE missing_version_count > 0
                  )::integer,
                  count(*) FILTER (
                    WHERE missing_version_count = 0
                  )::integer,
                  count(*) FILTER (
                    WHERE annotation_count_underflows_reconstructed
                  )::integer
                FROM {SCHEMA}.current_clause
                WHERE run_id = %s
                """,
                (run_id,),
            )
            summary = cursor.fetchone()
    output_fingerprint = object_fingerprint(
        {"counts": counts, "table_fingerprints": fingerprints}
    )
    if counts != run[1] or counts != run[2]:
        raise CurrentPublicationError(
            "fresh publication counts differ from the sealed receipt"
        )
    if fingerprints != run[3] or output_fingerprint != run[4]:
        raise CurrentPublicationError(
            "fresh publication fingerprint differs from the sealed receipt"
        )
    if expected is not None and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or output_fingerprint != expected.output_fingerprint
        or run[5] != expected.sealed_fingerprint
    ):
        raise CurrentPublicationError(
            "fresh publication differs from prepared material"
        )
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "output_fingerprint": output_fingerprint,
        "sealed_fingerprint": run[5],
        "inventory": {
            "current_clause_count": int(summary[0]),
            "expected_version_count": int(summary[1]),
            "reconstructed_version_count": int(summary[2]),
            "missing_version_count": int(summary[3]),
            "clauses_with_missing_versions": int(summary[4]),
            "clauses_without_missing_versions": int(summary[5]),
            "annotation_underflow_clause_count": int(summary[6]),
        },
    }


def load_current_publication(
    stage_dir: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    """Load, seal, verify, and optionally activate one current projection."""

    stage_dir = Path(stage_dir)
    try:
        manifest = json.loads(
            (stage_dir / "structural-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        source_parse_run_id = str(uuid.UUID(str(manifest["parse_run_id"])))
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise CurrentPublicationError(
            "structural manifest has no valid parse run id"
        ) from exc
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        acquisition_run_id, source_urls, reconstructed = _source_context(
            connection, source_parse_run_id
        )
    material = prepare_current_publication(
        stage_dir,
        source_acquisition_run_id=acquisition_run_id,
        source_urls=source_urls,
        reconstructed_version_counts=reconstructed,
    )
    with connector(dsn) as connection:
        already_loaded = _insert_material(connection, material)
        if activate:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT run_id
                    FROM {SCHEMA}.v_active_publication_run
                    """
                )
                active_row = cursor.fetchone()
                already_active = (
                    active_row is not None
                    and str(active_row[0]) == material.run_id
                )
                if not already_active:
                    cursor.execute(
                        f"""
                        INSERT INTO {SCHEMA}.publication_activation (run_id)
                        VALUES (%s)
                        """,
                        (material.run_id,),
                    )
    result = verify_current_publication(
        material.run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result["already_loaded"] = already_loaded
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT run_id FROM {SCHEMA}.v_active_publication_run"
            )
            active_row = cursor.fetchone()
    result["active"] = (
        active_row is not None
        and str(active_row[0]) == material.run_id
    )
    return result


def material_inventory_report(
    material: CurrentPublicationMaterial,
) -> dict[str, Any]:
    """Return a public, aggregate and clause-level inventory report."""

    clauses = [
        {
            "clause_code": row["clause_code"],
            "chapter_number": row["chapter_number"],
            "display_title": row["display_title"],
            "valid_distinct_roc_date_count": row[
                "valid_distinct_roc_date_count"
            ],
            "expected_version_count": row["expected_version_count"],
            "reconstructed_version_count": row[
                "reconstructed_version_count"
            ],
            "missing_version_count": row["missing_version_count"],
            "inventory_status": row["inventory_status"],
        }
        for row in material.clauses
    ]
    return {
        "schema": "nhi-rule-history/current-history-inventory/v1",
        "run_id": material.run_id,
        "source_parse_run_id": material.source_parse_run_id,
        "whole_split_parity_status": (
            material.whole_split_parity_status
        ),
        "authority_page_url": AUTHORITY_PAGE_URL,
        "version_count_policy": VERSION_COUNT_POLICY,
        "definition": (
            "Expected versions equal distinct valid ROC dates in the current "
            "clause, minimum one. Missing versions are expected full-text "
            "states not yet reconstructed under this owner-approved policy."
        ),
        "summary": {
            "current_clause_count": len(clauses),
            "expected_version_count": sum(
                row["expected_version_count"] for row in clauses
            ),
            "reconstructed_version_count": sum(
                row["reconstructed_version_count"] for row in clauses
            ),
            "missing_version_count": sum(
                row["missing_version_count"] for row in clauses
            ),
            "clauses_with_missing_versions": sum(
                row["missing_version_count"] > 0 for row in clauses
            ),
            "clauses_without_missing_versions": sum(
                row["missing_version_count"] == 0 for row in clauses
            ),
            "annotation_underflow_clause_count": sum(
                row["inventory_status"]
                == "annotation_count_underflows_reconstructed_evidence"
                for row in clauses
            ),
        },
        "clauses": clauses,
    }
