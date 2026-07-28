"""Load one verified WP2 run directory into immutable PostgreSQL staging.

Validation performs no database import.  Apply reuses the exact materialized
rows validated in memory, writes and seals in one transaction, then opens a
fresh connection and recomputes every count and order-independent row hash.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from nhi_rule_history.contracts import (
    ARTIFACT_URL_OBSERVATION_SCHEMA,
    DISCOVERED_RESOURCE_SCHEMA,
    DISCOVERY_MANIFEST_SCHEMA,
    DISCOVERY_OBSERVATION_SCHEMA,
    FETCH_ATTEMPT_SCHEMA,
    ISSUE_SCHEMA,
    JSONL_FILES,
    RAW_ARTIFACT_SCHEMA,
    RAW_MANIFEST_SCHEMA,
    RESOURCE_ARTIFACT_LINK_SCHEMA,
    file_sha256,
    iter_jsonl,
)
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
from nhi_rule_history.raw.verify import verify_raw

SCHEMA = "tw_drug_history_acq_stage"
GLOBAL_LOCK_KEY = "tw_drug_history_acq_stage-global"
LOADER_VERSION = "nhi-rule-history-acquisition-pg-loader/2.0.0"
CONTRACT_VERSION = "nhi-rule-history/wp2-acquisition-jsonl/v2"
DSN_ENV = "NHI_RULE_HISTORY_DSN"
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_acquisition_v2.sql"
)

FILE_SCHEMAS = {
    "discovery-observations.jsonl": DISCOVERY_OBSERVATION_SCHEMA,
    "discovered-resources.jsonl": DISCOVERED_RESOURCE_SCHEMA,
    "fetch-attempts.jsonl": FETCH_ATTEMPT_SCHEMA,
    "raw-artifacts.jsonl": RAW_ARTIFACT_SCHEMA,
    "resource-artifact-links.jsonl": RESOURCE_ARTIFACT_LINK_SCHEMA,
    "artifact-url-observations.jsonl": ARTIFACT_URL_OBSERVATION_SCHEMA,
    "issues.jsonl": ISSUE_SCHEMA,
}

TABLE_FILE = {
    "discovery_observation": "discovery-observations.jsonl",
    "discovered_resource": "discovered-resources.jsonl",
    "fetch_attempt": "fetch-attempts.jsonl",
    "raw_artifact": "raw-artifacts.jsonl",
    "resource_artifact_link": "resource-artifact-links.jsonl",
    "artifact_url_observation": "artifact-url-observations.jsonl",
    "acquisition_issue": "issues.jsonl",
}


class AcquisitionLoadError(PgLoadError):
    pass


@dataclass(frozen=True)
class AcquisitionMaterial:
    run_id: str
    source_plan_sha256: str
    capture_cut: str
    discovery_manifest_sha256: str
    raw_manifest_sha256: str
    migration_sha256: str
    code_sha256: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    input_files: tuple[dict[str, Any], ...]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    input_fingerprint: str
    output_fingerprint: str
    sealed_fingerprint: str


def _manifest_entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise AcquisitionLoadError("raw manifest files is not an array")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise AcquisitionLoadError("raw manifest contains an invalid file entry")
        name = entry["filename"]
        if name in result:
            raise AcquisitionLoadError("raw manifest repeats a file entry")
        result[name] = dict(entry)
    return result


def validate_acquisition_run(run_dir: Path) -> AcquisitionMaterial:
    run_dir = Path(run_dir)
    try:
        run_id = str(uuid.UUID(run_dir.name))
    except ValueError as exc:
        raise AcquisitionLoadError("run directory name must be a UUID") from exc

    raw_manifest_path = run_dir / "raw-manifest.json"
    discovery_manifest_path = run_dir / "discovery-manifest.json"
    raw_manifest = read_object(raw_manifest_path)
    discovery_manifest = read_object(discovery_manifest_path)
    if raw_manifest.get("schema") != RAW_MANIFEST_SCHEMA:
        raise AcquisitionLoadError("raw manifest schema mismatch")
    if discovery_manifest.get("schema") != DISCOVERY_MANIFEST_SCHEMA:
        raise AcquisitionLoadError("discovery manifest schema mismatch")
    if raw_manifest.get("status") != "success":
        raise AcquisitionLoadError("raw manifest is not successful")
    if discovery_manifest.get("status") != "success":
        raise AcquisitionLoadError("discovery manifest is not successful")
    for key in ("source_plan_schema", "source_plan_sha256", "capture_cut"):
        if raw_manifest.get(key) != discovery_manifest.get(key):
            raise AcquisitionLoadError(f"manifest {key} mismatch")

    verification = verify_raw(run_dir)
    if verification.get("status") != "passed":
        raise AcquisitionLoadError("raw verification did not pass")
    entries = _manifest_entries(raw_manifest)
    required = set(JSONL_FILES) | {"discovery-manifest.json"}
    if set(entries) != required:
        raise AcquisitionLoadError("raw manifest file set is not the exact contract set")

    rows: dict[str, tuple[dict[str, Any], ...]] = {}
    input_files: list[dict[str, Any]] = []
    for filename in JSONL_FILES:
        path = run_dir / filename
        entry = entries[filename]
        if path.stat().st_size != entry.get("bytes") or file_sha256(path) != entry.get(
            "sha256"
        ):
            raise AcquisitionLoadError(f"manifested input changed: {filename}")
        materialized = tuple(iter_jsonl(path))
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

    discovery_entry = entries["discovery-manifest.json"]
    discovery_sha = file_sha256(discovery_manifest_path)
    if (
        discovery_manifest_path.stat().st_size != discovery_entry.get("bytes")
        or discovery_sha != discovery_entry.get("sha256")
    ):
        raise AcquisitionLoadError("manifested discovery manifest changed")
    input_files.append(
        {
            "logical_name": "discovery-manifest.json",
            "schema_id": DISCOVERY_MANIFEST_SCHEMA,
            "content_sha256": discovery_sha,
            "byte_size": discovery_manifest_path.stat().st_size,
            "row_count": 1,
        }
    )
    raw_sha = file_sha256(raw_manifest_path)
    input_files.append(
        {
            "logical_name": "raw-manifest.json",
            "schema_id": RAW_MANIFEST_SCHEMA,
            "content_sha256": raw_sha,
            "byte_size": raw_manifest_path.stat().st_size,
            "row_count": 1,
        }
    )
    input_files.sort(key=lambda row: row["logical_name"])

    table_fingerprints: dict[str, str] = {}
    expected_counts: dict[str, int] = {}
    for table, filename in TABLE_FILE.items():
        hashes = [row_sha256(row) for row in rows[filename]]
        table_fingerprints[table] = row_set_fingerprint(hashes)
        expected_counts[table] = len(hashes)
    expected_counts["input_file"] = len(input_files)
    expected_counts["artifact_bytes"] = sum(
        int(row["byte_size"]) for row in rows["raw-artifacts.jsonl"]
    )

    migration_sha = migration_fingerprint(MIGRATION)
    code_sha = code_fingerprint(Path(__file__), Path(__file__).with_name("common.py"))
    input_fingerprint = object_fingerprint(
        {
            "contract_version": CONTRACT_VERSION,
            "source_plan_sha256": raw_manifest["source_plan_sha256"],
            "capture_cut": raw_manifest["capture_cut"],
            "discovery_manifest_sha256": discovery_sha,
            "raw_manifest_sha256": raw_sha,
            "files": input_files,
        }
    )
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "table_fingerprints": table_fingerprints,
        }
    )
    sealed_fingerprint = object_fingerprint(
        {
            "loader_version": LOADER_VERSION,
            "contract_version": CONTRACT_VERSION,
            "migration_sha256": migration_sha,
            "code_sha256": code_sha,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
        }
    )
    return AcquisitionMaterial(
        run_id=run_id,
        source_plan_sha256=str(raw_manifest["source_plan_sha256"]),
        capture_cut=str(raw_manifest["capture_cut"]),
        discovery_manifest_sha256=discovery_sha,
        raw_manifest_sha256=raw_sha,
        migration_sha256=migration_sha,
        code_sha256=code_sha,
        rows=rows,
        input_files=tuple(input_files),
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        sealed_fingerprint=sealed_fingerprint,
    )


def _default_connect(conninfo: str):
    try:
        import psycopg
    except ImportError as exc:
        raise AcquisitionLoadError("psycopg is required for apply") from exc
    return psycopg.connect(conninfo)


def _params(material: AcquisitionMaterial, filename: str) -> list[tuple[Any, ...]]:
    result: list[tuple[Any, ...]] = []
    for row in material.rows[filename]:
        payload = json_text(row)
        digest = row_sha256(row)
        if filename == "discovery-observations.jsonl":
            result.append(
                (
                    material.run_id,
                    row["observation_id"],
                    row["adapter_id"],
                    row["request_url"],
                    row.get("final_url"),
                    json_text(row["locator"]),
                    row["status"],
                    row["observed_at"],
                    row.get("http_status"),
                    json_text(row["response_headers"])
                    if "response_headers" in row
                    else None,
                    row.get("content_sha256"),
                    row.get("byte_size"),
                    row.get("content_path"),
                    row.get("error_code"),
                    payload,
                    digest,
                )
            )
        elif filename == "discovered-resources.jsonl":
            result.append(
                (
                    material.run_id,
                    row["resource_id"],
                    row["adapter_id"],
                    row["resource_kind"],
                    row["source_url"],
                    row.get("parent_resource_id"),
                    json_text(row["discovery_locator"]),
                    row["source_label"],
                    row["fetch_state"],
                    payload,
                    digest,
                )
            )
        elif filename == "fetch-attempts.jsonl":
            result.append(
                (
                    material.run_id,
                    row["attempt_id"],
                    row["resource_id"],
                    row["source_url"],
                    row["started_at"],
                    row["completed_at"],
                    row["status"],
                    row["acquisition_mode"],
                    row.get("http_status"),
                    row.get("final_url"),
                    json_text(row["response_headers"])
                    if "response_headers" in row
                    else None,
                    row.get("artifact_sha256"),
                    row.get("byte_size"),
                    row.get("error_code"),
                    payload,
                    digest,
                )
            )
        elif filename == "raw-artifacts.jsonl":
            result.append(
                (
                    material.run_id,
                    row["artifact_sha256"],
                    row["byte_size"],
                    row["content_path"],
                    row["media_type"],
                    row["first_observed_at"],
                    payload,
                    digest,
                )
            )
        elif filename == "resource-artifact-links.jsonl":
            result.append(
                (
                    material.run_id,
                    row["link_id"],
                    row["resource_id"],
                    row["artifact_sha256"],
                    row["relation"],
                    row["observed_at"],
                    payload,
                    digest,
                )
            )
        elif filename == "artifact-url-observations.jsonl":
            result.append(
                (
                    material.run_id,
                    row["url_observation_id"],
                    row["resource_id"],
                    row["source_url"],
                    row["artifact_sha256"],
                    row["relation_to_previous"],
                    row["observed_at"],
                    row.get("previous_artifact_sha256"),
                    payload,
                    digest,
                )
            )
        elif filename == "issues.jsonl":
            result.append(
                (
                    material.run_id,
                    row["issue_id"],
                    row["stage"],
                    row["severity"],
                    row["adapter_id"],
                    row.get("resource_id"),
                    row["source_url"],
                    row["code"],
                    json_text(row["locator"]) if "locator" in row else None,
                    row["recorded_at"],
                    payload,
                    digest,
                )
            )
    return result


INSERTS = {
    "discovery-observations.jsonl": f"""
        INSERT INTO {SCHEMA}.discovery_observation
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)
    """,
    "discovered-resources.jsonl": f"""
        INSERT INTO {SCHEMA}.discovered_resource
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s)
    """,
    "fetch-attempts.jsonl": f"""
        INSERT INTO {SCHEMA}.fetch_attempt
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s)
    """,
    "raw-artifacts.jsonl": f"""
        INSERT INTO {SCHEMA}.raw_artifact
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
    """,
    "resource-artifact-links.jsonl": f"""
        INSERT INTO {SCHEMA}.resource_artifact_link
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
    """,
    "artifact-url-observations.jsonl": f"""
        INSERT INTO {SCHEMA}.artifact_url_observation
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
    """,
    "issues.jsonl": f"""
        INSERT INTO {SCHEMA}.acquisition_issue
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)
    """,
}


def verify_loaded_acquisition_run(
    run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: AcquisitionMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, verified_counts,
                       table_fingerprints, input_fingerprint,
                       output_fingerprint, sealed_fingerprint
                FROM {SCHEMA}.acquisition_run WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise AcquisitionLoadError("fresh verification found no sealed run")
            stored_expected = run[1]
            stored_verified = run[2]
            stored_fingerprints = run[3]
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in TABLE_FILE:
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} WHERE run_id = %s ORDER BY source_row_sha256",
                    (run_id,),
                )
                hashes = [row[0] for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"SELECT count(*) FROM {SCHEMA}.input_file WHERE run_id = %s",
                (run_id,),
            )
            counts["input_file"] = int(cursor.fetchone()[0])
            cursor.execute(
                f"SELECT COALESCE(sum(byte_size), 0) FROM {SCHEMA}.raw_artifact WHERE run_id = %s",
                (run_id,),
            )
            counts["artifact_bytes"] = int(cursor.fetchone()[0])
    if counts != stored_expected or counts != stored_verified:
        raise AcquisitionLoadError("fresh verification count mismatch")
    if fingerprints != stored_fingerprints:
        raise AcquisitionLoadError("fresh verification row fingerprint mismatch")
    output = object_fingerprint(
        {"counts": counts, "table_fingerprints": fingerprints}
    )
    if output != run[5]:
        raise AcquisitionLoadError("fresh verification output fingerprint mismatch")
    if expected is not None:
        if (
            counts != expected.expected_counts
            or fingerprints != expected.table_fingerprints
            or run[4] != expected.input_fingerprint
            or run[5] != expected.output_fingerprint
            or run[6] != expected.sealed_fingerprint
        ):
            raise AcquisitionLoadError("fresh verification differs from validated input")
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "input_fingerprint": run[4],
        "output_fingerprint": run[5],
        "sealed_fingerprint": run[6],
    }


def load_acquisition_run(
    run_dir: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    material = validate_acquisition_run(run_dir)
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
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{SCHEMA}:{material.input_fingerprint}",),
            )
            cursor.execute(
                f"""
                SELECT run_id, sealed_fingerprint
                FROM {SCHEMA}.acquisition_run
                WHERE input_fingerprint = %s
                """,
                (material.input_fingerprint,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing[0]) != material.run_id or existing[1] != material.sealed_fingerprint:
                    raise AcquisitionLoadError("input fingerprint collision or loader drift")
                already_loaded = True
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.acquisition_run (
                      run_id, state, loader_version, contract_version,
                      migration_sha256, code_sha256, source_plan_sha256,
                      capture_cut, discovery_manifest_sha256, raw_manifest_sha256,
                      input_fingerprint, expected_counts
                    ) VALUES (
                      %s, 'loading', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        material.run_id,
                        LOADER_VERSION,
                        CONTRACT_VERSION,
                        material.migration_sha256,
                        material.code_sha256,
                        material.source_plan_sha256,
                        material.capture_cut,
                        material.discovery_manifest_sha256,
                        material.raw_manifest_sha256,
                        material.input_fingerprint,
                        json_text(material.expected_counts),
                    ),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.input_file
                      (run_id, logical_name, schema_id, content_sha256, byte_size, row_count)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    [
                        (
                            material.run_id,
                            row["logical_name"],
                            row["schema_id"],
                            row["content_sha256"],
                            row["byte_size"],
                            row["row_count"],
                        )
                        for row in material.input_files
                    ],
                )
                for filename in (
                    "discovery-observations.jsonl",
                    "discovered-resources.jsonl",
                    "fetch-attempts.jsonl",
                    "raw-artifacts.jsonl",
                    "resource-artifact-links.jsonl",
                    "artifact-url-observations.jsonl",
                    "issues.jsonl",
                ):
                    values = _params(material, filename)
                    if values:
                        cursor.executemany(INSERTS[filename], values)
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.acquisition_run
                    SET state = 'sealed',
                        output_fingerprint = %s,
                        sealed_fingerprint = %s,
                        verified_counts = %s::jsonb,
                        table_fingerprints = %s::jsonb,
                        sealed_at = now()
                    WHERE run_id = %s AND state = 'loading'
                    """,
                    (
                        material.output_fingerprint,
                        material.sealed_fingerprint,
                        json_text(material.expected_counts),
                        json_text(material.table_fingerprints),
                        material.run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AcquisitionLoadError("acquisition seal transition failed")
    result = verify_loaded_acquisition_run(
        material.run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result["already_loaded"] = already_loaded
    return result
