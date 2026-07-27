"""Load verified source-local structural JSONL into immutable PG staging."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from nhi_rule_history.contracts import file_sha256
from nhi_rule_history.parsers.odt import (
    NON_CLAIM,
    OCCURRENCE_CANDIDATE_SCHEMA,
    PARSE_ISSUE_SCHEMA,
    STRUCTURAL_BLOCK_SCHEMA,
    STRUCTURAL_FILES,
    STRUCTURAL_MANIFEST_SCHEMA,
)
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    read_object,
    row_set_fingerprint,
    row_sha256,
)

SCHEMA = "tw_drug_history_structural_stage"
ACQ_SCHEMA = "tw_drug_history_acq_stage"
GLOBAL_LOCK_KEY = "tw_drug_history_structural_stage-global"
LOADER_VERSION = "nhi-rule-history-structural-pg-loader/2.0.0"
CONTRACT_VERSION = "nhi-rule-history/wp2-structural-jsonl/v2"
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_structural_v2.sql"
)
FILE_SCHEMAS = {
    "structural-blocks.jsonl": STRUCTURAL_BLOCK_SCHEMA,
    "occurrence-candidates.jsonl": OCCURRENCE_CANDIDATE_SCHEMA,
    "parse-issues.jsonl": PARSE_ISSUE_SCHEMA,
}
TABLE_FILE = {
    "structural_block": "structural-blocks.jsonl",
    "occurrence_candidate": "occurrence-candidates.jsonl",
    "parse_issue": "parse-issues.jsonl",
}


class StructuralLoadError(PgLoadError):
    pass


@dataclass(frozen=True)
class StructuralMaterial:
    parse_run_id: str
    manifest: Mapping[str, Any]
    structural_manifest_sha256: str
    migration_sha256: str
    code_sha256: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    input_files: tuple[dict[str, Any], ...]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    output_fingerprint: str


def _read_rows(path: Path, schema: str, parse_run_id: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    id_key = {
        STRUCTURAL_BLOCK_SCHEMA: "block_id",
        OCCURRENCE_CANDIDATE_SCHEMA: "occurrence_id",
        PARSE_ISSUE_SCHEMA: "issue_id",
    }[schema]
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StructuralLoadError(f"{path.name}:{line_no}: invalid JSON") from exc
            if not isinstance(row, dict) or row.get("schema") != schema:
                raise StructuralLoadError(f"{path.name}:{line_no}: schema mismatch")
            if row.get("parse_run_id") != parse_run_id:
                raise StructuralLoadError(f"{path.name}:{line_no}: parse run mismatch")
            if row.get("statement") != NON_CLAIM:
                raise StructuralLoadError(f"{path.name}:{line_no}: non-claim mismatch")
            claimed = row.get("source_row_sha256")
            if claimed != row_sha256(row, derived_key="source_row_sha256"):
                raise StructuralLoadError(f"{path.name}:{line_no}: source row hash mismatch")
            identity = row.get(id_key)
            if not isinstance(identity, str) or identity in seen:
                raise StructuralLoadError(f"{path.name}:{line_no}: invalid duplicate identity")
            seen.add(identity)
            rows.append(row)
    return tuple(rows)


def validate_structural_run(stage_dir: Path) -> StructuralMaterial:
    stage_dir = Path(stage_dir)
    manifest_path = stage_dir / "structural-manifest.json"
    manifest = read_object(manifest_path)
    if manifest.get("schema") != STRUCTURAL_MANIFEST_SCHEMA:
        raise StructuralLoadError("structural manifest schema mismatch")
    if manifest.get("status") != "passed":
        raise StructuralLoadError("structural manifest did not pass")
    try:
        parse_run_id = str(uuid.UUID(str(manifest["parse_run_id"])))
    except (KeyError, ValueError) as exc:
        raise StructuralLoadError("invalid parse run UUID") from exc
    if stage_dir.name != parse_run_id:
        raise StructuralLoadError("structural directory name differs from parse run UUID")
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or counts.get("blocking_issues") != 0:
        raise StructuralLoadError("structural manifest has blocking issues")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise StructuralLoadError("structural manifest files is not an array")
    by_name = {
        entry.get("filename"): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
    }
    if set(by_name) != set(STRUCTURAL_FILES) or len(entries) != len(by_name):
        raise StructuralLoadError("structural manifest file set mismatch")
    rows: dict[str, tuple[dict[str, Any], ...]] = {}
    input_files: list[dict[str, Any]] = []
    for filename in STRUCTURAL_FILES:
        path = stage_dir / filename
        entry = by_name[filename]
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            raise StructuralLoadError(f"structural manifested file changed: {filename}")
        materialized = _read_rows(path, FILE_SCHEMAS[filename], parse_run_id)
        rows[filename] = materialized
        input_files.append(
            {
                "logical_name": filename,
                "schema_id": FILE_SCHEMAS[filename],
                "content_sha256": entry["sha256"],
                "byte_size": entry["bytes"],
                "row_count": len(materialized),
            }
        )
    expected_counts = {
        "structural_block": len(rows["structural-blocks.jsonl"]),
        "occurrence_candidate": len(rows["occurrence-candidates.jsonl"]),
        "parse_issue": len(rows["parse-issues.jsonl"]),
    }
    manifest_count_map = {
        "structural_block": "structural_blocks",
        "occurrence_candidate": "occurrence_candidates",
        "parse_issue": "parse_issues",
    }
    if any(
        expected_counts[table] != counts[source]
        for table, source in manifest_count_map.items()
    ):
        raise StructuralLoadError("structural manifest row counts mismatch")
    fingerprints = {
        table: row_set_fingerprint(
            row["source_row_sha256"] for row in rows[filename]
        )
        for table, filename in TABLE_FILE.items()
    }
    output = object_fingerprint(
        {"counts": expected_counts, "table_fingerprints": fingerprints}
    )
    return StructuralMaterial(
        parse_run_id=parse_run_id,
        manifest=manifest,
        structural_manifest_sha256=file_sha256(manifest_path),
        migration_sha256=migration_fingerprint(MIGRATION),
        code_sha256=code_fingerprint(Path(__file__), Path(__file__).with_name("common.py")),
        rows=rows,
        input_files=tuple(sorted(input_files, key=lambda row: row["logical_name"])),
        expected_counts=expected_counts,
        table_fingerprints=fingerprints,
        output_fingerprint=output,
    )


def _block_params(m: StructuralMaterial) -> list[tuple[Any, ...]]:
    return [
        (
            m.parse_run_id,
            row["block_id"],
            None,
            row["artifact_sha256"],
            row["relative_path"],
            json_text(row["locator"]),
            row["locator_key"],
            row["block_kind"],
            row["element_name"],
            row.get("style_name"),
            row["container"],
            row["in_table"],
            row["in_index_context"],
            row["xml_element_index"],
            row["raw_text"],
            row["normalized_search_text"],
            row["raw_text_sha256"],
            row["raw_text_byte_length"],
            row["raw_text_char_length"],
            row["parser_version"],
            json_text(row["source_resource_ids"]),
            json_text(row["source_labels"]),
            row["statement"],
            json_text(row),
            row["source_row_sha256"],
        )
        for row in m.rows["structural-blocks.jsonl"]
    ]


def _occurrence_params(m: StructuralMaterial) -> list[tuple[Any, ...]]:
    return [
        (
            m.parse_run_id,
            row["occurrence_id"],
            None,
            row["artifact_sha256"],
            row["block_id"],
            row["relative_path"],
            row["designation_text"],
            json_text(row["locator"]),
            row["locator_key"],
            row["raw_text"],
            row["normalized_search_text"],
            row["raw_text_sha256"],
            row["raw_text_byte_length"],
            row["raw_text_char_length"],
            row["parser_version"],
            json_text(row["ambiguity_flags"]),
            row["container"],
            row["match_start_in_raw"],
            row["match_end_in_raw"],
            row["in_index_context"],
            json_text(row["source_resource_ids"]),
            json_text(row["source_labels"]),
            row["statement"],
            json_text(row),
            row["source_row_sha256"],
        )
        for row in m.rows["occurrence-candidates.jsonl"]
    ]


def _issue_params(m: StructuralMaterial) -> list[tuple[Any, ...]]:
    return [
        (
            m.parse_run_id,
            row["issue_id"],
            None,
            row["artifact_sha256"],
            row["issue_code"],
            row["severity"],
            row["blocking"],
            json_text(row["message_parameters"]),
            row["statement"],
            json_text(row),
            row["source_row_sha256"],
        )
        for row in m.rows["parse-issues.jsonl"]
    ]


def _with_acq(rows: list[tuple[Any, ...]], acquisition_run_id: str) -> list[tuple[Any, ...]]:
    return [row[:2] + (acquisition_run_id,) + row[3:] for row in rows]


def verify_loaded_structural_run(
    parse_run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: StructuralMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, acquisition_run_id, expected_counts,
                       verified_counts, table_fingerprints, output_fingerprint,
                       sealed_fingerprint
                FROM {SCHEMA}.parse_run WHERE parse_run_id = %s
                """,
                (parse_run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise StructuralLoadError("fresh verification found no sealed parse run")
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in TABLE_FILE:
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} WHERE parse_run_id = %s ORDER BY source_row_sha256",
                    (parse_run_id,),
                )
                hashes = [row[0] for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
    if counts != run[2] or counts != run[3]:
        raise StructuralLoadError("fresh structural count mismatch")
    if fingerprints != run[4]:
        raise StructuralLoadError("fresh structural row fingerprint mismatch")
    output = object_fingerprint({"counts": counts, "table_fingerprints": fingerprints})
    if output != run[5]:
        raise StructuralLoadError("fresh structural output fingerprint mismatch")
    if expected is not None and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or output != expected.output_fingerprint
    ):
        raise StructuralLoadError("fresh structural verification differs from input")
    return {
        "parse_run_id": parse_run_id,
        "acquisition_run_id": str(run[1]),
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "output_fingerprint": run[5],
        "sealed_fingerprint": run[6],
    }


def load_structural_run(
    stage_dir: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    material = validate_structural_run(stage_dir)
    manifest = material.manifest
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    already_loaded = False
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (GLOBAL_LOCK_KEY,),
            )
            cursor.execute(
                f"""
                SELECT run_id FROM {ACQ_SCHEMA}.acquisition_run
                WHERE state = 'sealed' AND raw_manifest_sha256 = %s
                """,
                (manifest["raw_manifest_sha256"],),
            )
            acq = cursor.fetchone()
            if acq is None:
                raise StructuralLoadError("no sealed acquisition run matches raw manifest")
            acquisition_run_id = str(acq[0])
            sealed_fingerprint = object_fingerprint(
                {
                    "loader_version": LOADER_VERSION,
                    "contract_version": CONTRACT_VERSION,
                    "migration_sha256": material.migration_sha256,
                    "code_sha256": material.code_sha256,
                    "acquisition_run_id": acquisition_run_id,
                    "input_fingerprint": manifest["input_fingerprint"],
                    "output_fingerprint": material.output_fingerprint,
                }
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{SCHEMA}:{manifest['input_fingerprint']}",),
            )
            cursor.execute(
                f"""
                SELECT parse_run_id, sealed_fingerprint
                FROM {SCHEMA}.parse_run WHERE input_fingerprint = %s
                """,
                (manifest["input_fingerprint"],),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != material.parse_run_id
                    or existing[1] != sealed_fingerprint
                ):
                    raise StructuralLoadError("structural input collision or loader drift")
                already_loaded = True
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.parse_run (
                      parse_run_id, acquisition_run_id, state, loader_version,
                      contract_version, migration_sha256, code_sha256,
                      raw_manifest_sha256, structural_manifest_sha256,
                      parser_adapter_version, legacy_parser_version,
                      parser_bundle_sha256, input_fingerprint, fidelity_class,
                      expected_counts, input_files, parser_started_at,
                      parser_completed_at
                    ) VALUES (
                      %s,%s,'loading',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s::jsonb,%s::jsonb,%s,%s
                    )
                    """,
                    (
                        material.parse_run_id,
                        acquisition_run_id,
                        LOADER_VERSION,
                        CONTRACT_VERSION,
                        material.migration_sha256,
                        material.code_sha256,
                        manifest["raw_manifest_sha256"],
                        material.structural_manifest_sha256,
                        manifest["parser_adapter_version"],
                        manifest["legacy_parser_version"],
                        manifest["parser_bundle_sha256"],
                        manifest["input_fingerprint"],
                        manifest["fidelity_class"],
                        json_text(material.expected_counts),
                        json_text(material.input_files),
                        manifest["started_at"],
                        manifest["completed_at"],
                    ),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.structural_block VALUES (
                      %s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s
                    )
                    """,
                    _with_acq(_block_params(material), acquisition_run_id),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.occurrence_candidate VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,
                      %s::jsonb,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s
                    )
                    """,
                    _with_acq(_occurrence_params(material), acquisition_run_id),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.parse_issue VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s
                    )
                    """,
                    _with_acq(_issue_params(material), acquisition_run_id),
                )
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.parse_run
                    SET state = 'sealed', output_fingerprint = %s,
                        sealed_fingerprint = %s, verified_counts = %s::jsonb,
                        table_fingerprints = %s::jsonb, sealed_at = now()
                    WHERE parse_run_id = %s AND state = 'loading'
                    """,
                    (
                        material.output_fingerprint,
                        sealed_fingerprint,
                        json_text(material.expected_counts),
                        json_text(material.table_fingerprints),
                        material.parse_run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StructuralLoadError("parse run seal transition failed")
    result = verify_loaded_structural_run(
        material.parse_run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result["already_loaded"] = already_loaded
    return result
