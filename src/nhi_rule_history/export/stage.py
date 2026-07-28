"""Read-only export and verification for one exact sealed v1 staging run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .canonical import (
    CanonicalError,
    canonical_json_bytes,
    canonicalize_row,
    logical_digest,
    read_jsonl,
    redaction_scan_jsonl,
    row_sort_key,
    write_jsonl,
)
from .contract import (
    DATASET_KIND,
    EXPORT_CONTRACT_VERSION,
    NON_CLAIM,
    SQLITE_SCHEMA_VERSION,
    TABLES,
    Table,
)


class ExportError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _materialize_rows(
    raw_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    run_id: str,
    fingerprint: str,
) -> dict[str, list[dict[str, Any]]]:
    materialized: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        if table.name not in raw_rows:
            raise ExportError(f"missing table rows: {table.name}")
        rows = [
            canonicalize_row(dict(row), table)
            for row in raw_rows[table.name]
        ]
        for row in rows:
            if row["run_id"] != run_id:
                raise ExportError(
                    f"{table.name}: row belongs to unexpected run {row['run_id']}"
                )
        rows.sort(key=lambda row: row_sort_key(row, table))
        if len({row_sort_key(row, table) for row in rows}) != len(rows):
            raise ExportError(f"{table.name}: duplicate primary key")
        materialized[table.name] = rows

    run_rows = materialized["rebuild_run"]
    if len(run_rows) != 1:
        raise ExportError(f"expected one rebuild_run row, got {len(run_rows)}")
    run = run_rows[0]
    if run["state"] != "sealed":
        raise ExportError(f"run is not sealed: {run['state']}")
    if run["sealed_fingerprint"] != fingerprint:
        raise ExportError("sealed fingerprint does not match requested fingerprint")
    return materialized


def _fetch_table_rows(cursor: Any, table: Table, run_id: str) -> list[dict[str, Any]]:
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    order = ", ".join(f'"{column}"' for column in table.primary_key)
    cursor.execute(
        f'SELECT {columns} FROM tw_drug_history_stage."{table.name}" '
        f'WHERE run_id = %s ORDER BY {order}',
        (run_id,),
    )
    return [
        dict(zip((column.name for column in table.columns), values, strict=True))
        for values in cursor.fetchall()
    ]


def rows_from_connection(
    connection: Any,
    *,
    run_id: str,
    fingerprint: str,
) -> dict[str, list[dict[str, Any]]]:
    """Take one repeatable, read-only snapshot of the exact sealed run."""
    cursor = connection.cursor()
    try:
        cursor.execute(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        cursor.execute(
            """
            SELECT 1
            FROM tw_drug_history_stage.rebuild_run
            WHERE run_id = %s
              AND sealed_fingerprint = %s
              AND state = 'sealed'
            """,
            (run_id, fingerprint),
        )
        if cursor.fetchone() is None:
            raise ExportError(
                "no sealed staging run matches the exact run_id and fingerprint"
            )
        rows: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            rows[table.name] = _fetch_table_rows(cursor, table, run_id)
        materialized = _materialize_rows(
            rows,
            run_id=run_id,
            fingerprint=fingerprint,
        )
        cursor.execute("COMMIT")
        return materialized
    except Exception:
        try:
            cursor.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        cursor.close()


def _sqlite_value(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "boolean":
        return int(value)
    if kind == "json":
        return canonical_json_bytes(value).decode("utf-8")
    return value


def _build_sqlite(
    *,
    rows: Mapping[str, list[dict[str, Any]]],
    output: Path,
    schema_path: Path,
    run_id: str,
    fingerprint: str,
    row_digest: str,
    counts: Mapping[str, int],
) -> None:
    conn = sqlite3.connect(output)
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        for table in TABLES:
            column_names = [column.name for column in table.columns]
            quoted = ", ".join(f'"{name}"' for name in column_names)
            placeholders = ", ".join("?" for _ in column_names)
            sql = (
                f'INSERT INTO "{table.name}" ({quoted}) '
                f"VALUES ({placeholders})"
            )
            for row in rows[table.name]:
                conn.execute(
                    sql,
                    [
                        _sqlite_value(row[column.name], column.kind)
                        for column in table.columns
                    ],
                )
        conn.execute(
            """
            INSERT INTO dataset_metadata (
              dataset_id, schema_version, export_contract_version, dataset_kind,
              run_id, sealed_fingerprint, logical_row_digest,
              legal_history_claim, scope_statement, table_counts_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"stage-v1:{run_id}",
                SQLITE_SCHEMA_VERSION,
                EXPORT_CONTRACT_VERSION,
                DATASET_KIND,
                run_id,
                fingerprint,
                row_digest,
                NON_CLAIM,
                canonical_json_bytes(dict(counts)).decode("utf-8"),
            ),
        )
        conn.commit()
        fk_errors = list(conn.execute("PRAGMA foreign_key_check"))
        if fk_errors:
            raise ExportError(
                f"SQLite foreign_key_check returned {len(fk_errors)} row(s)"
            )
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ExportError(f"SQLite integrity_check failed: {integrity}")
    except Exception:
        conn.close()
        output.unlink(missing_ok=True)
        raise
    finally:
        if output.exists():
            conn.close()


def _sqlite_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            order = ", ".join(f'"{column}"' for column in table.primary_key)
            selected = ", ".join(f'"{column.name}"' for column in table.columns)
            raw_rows = conn.execute(
                f'SELECT {selected} FROM "{table.name}" ORDER BY {order}'
            )
            rows: list[dict[str, Any]] = []
            for raw in raw_rows:
                row: dict[str, Any] = {}
                for column in table.columns:
                    value = raw[column.name]
                    if value is not None and column.kind == "boolean":
                        value = bool(value)
                    elif value is not None and column.kind == "json":
                        value = json.loads(value)
                    row[column.name] = value
                rows.append(canonicalize_row(row, table))
            result[table.name] = rows
        return result
    finally:
        conn.close()


def export_stage_from_rows(
    raw_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    run_id: str,
    fingerprint: str,
    output_dir: Path,
    schema_path: Path,
) -> dict[str, Any]:
    """Export an already-read snapshot; intended for adapters and tests."""
    if output_dir.exists():
        raise ExportError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    try:
        rows = _materialize_rows(
            raw_rows,
            run_id=run_id,
            fingerprint=fingerprint,
        )
        counts: dict[str, int] = {}
        files: dict[str, dict[str, Any]] = {}
        for table in TABLES:
            path = output_dir / f"{table.name}.jsonl"
            count, file_digest = write_jsonl(path, rows[table.name])
            counts[table.name] = count
            files[path.name] = {
                "bytes": path.stat().st_size,
                "row_count": count,
                "sha256": file_digest,
            }

        redaction_scan_jsonl(
            output_dir / f"{table.name}.jsonl" for table in TABLES
        )
        row_digest = logical_digest(rows)
        sqlite_path = output_dir / "nhi-rule-history-stage-v1.sqlite"
        _build_sqlite(
            rows=rows,
            output=sqlite_path,
            schema_path=schema_path,
            run_id=run_id,
            fingerprint=fingerprint,
            row_digest=row_digest,
            counts=counts,
        )
        sqlite_rows = _sqlite_rows(sqlite_path)
        sqlite_digest = logical_digest(sqlite_rows)
        if sqlite_digest != row_digest:
            raise ExportError(
                "storage-independent logical digest differs between JSONL and SQLite"
            )
        files[sqlite_path.name] = {
            "bytes": sqlite_path.stat().st_size,
            "sha256": _sha256_file(sqlite_path),
        }
        manifest = {
            "schema": "nhi-rule-history-stage-export-manifest/v1",
            "export_contract_version": EXPORT_CONTRACT_VERSION,
            "dataset_kind": DATASET_KIND,
            "legal_history_claim": False,
            "scope_statement": NON_CLAIM,
            "run_id": run_id,
            "sealed_fingerprint": fingerprint,
            "logical_row_digest": row_digest,
            "table_counts": counts,
            "files": files,
            "checks": {
                "allowlisted_columns": "passed",
                "full_primary_key_sort": "passed",
                "canonical_utf8_lf_jsonl": "passed",
                "redaction_scan": "passed",
                "sqlite_foreign_key_check": "passed",
                "sqlite_integrity_check": "passed",
                "jsonl_sqlite_typed_row_parity": "passed",
            },
        }
        manifest_path = output_dir / "export-manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        verify_export_directory(
            output_dir,
            run_id=run_id,
            fingerprint=fingerprint,
        )
        return manifest
    except Exception:
        # Keep no partial directory which could be mistaken for a release input.
        for path in sorted(output_dir.glob("*")):
            if path.is_file():
                path.unlink()
        output_dir.rmdir()
        raise


def export_stage_from_connection(
    connection: Any,
    *,
    run_id: str,
    fingerprint: str,
    output_dir: Path,
    schema_path: Path,
) -> dict[str, Any]:
    rows = rows_from_connection(
        connection,
        run_id=run_id,
        fingerprint=fingerprint,
    )
    return export_stage_from_rows(
        rows,
        run_id=run_id,
        fingerprint=fingerprint,
        output_dir=output_dir,
        schema_path=schema_path,
    )


def verify_export_directory(
    directory: Path,
    *,
    run_id: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    manifest_path = directory / "export-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError("missing or invalid export-manifest.json") from exc
    if manifest.get("schema") != "nhi-rule-history-stage-export-manifest/v1":
        raise ExportError("unexpected export manifest schema")
    if manifest.get("legal_history_claim") is not False:
        raise ExportError("export manifest must explicitly reject legal-history claim")
    if run_id is not None and manifest.get("run_id") != run_id:
        raise ExportError("export run_id mismatch")
    if fingerprint is not None and manifest.get("sealed_fingerprint") != fingerprint:
        raise ExportError("export sealed_fingerprint mismatch")

    table_rows: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        path = directory / f"{table.name}.jsonl"
        if not path.is_file():
            raise ExportError(f"missing JSONL file: {path.name}")
        rows = [canonicalize_row(row, table) for row in read_jsonl(path)]
        if rows != sorted(rows, key=lambda row: row_sort_key(row, table)):
            raise ExportError(f"{table.name}: rows not sorted by full primary key")
        if len({row_sort_key(row, table) for row in rows}) != len(rows):
            raise ExportError(f"{table.name}: duplicate primary key")
        expected_file = manifest["files"].get(path.name)
        if not expected_file or expected_file["sha256"] != _sha256_file(path):
            raise ExportError(f"{path.name}: checksum mismatch")
        if expected_file["row_count"] != len(rows):
            raise ExportError(f"{path.name}: row count mismatch")
        table_rows[table.name] = rows
    redaction_scan_jsonl(
        directory / f"{table.name}.jsonl" for table in TABLES
    )
    jsonl_digest = logical_digest(table_rows)
    if jsonl_digest != manifest.get("logical_row_digest"):
        raise ExportError("JSONL logical row digest mismatch")

    sqlite_path = directory / "nhi-rule-history-stage-v1.sqlite"
    if _sha256_file(sqlite_path) != manifest["files"][sqlite_path.name]["sha256"]:
        raise ExportError("SQLite file checksum mismatch")
    sqlite_rows = _sqlite_rows(sqlite_path)
    sqlite_digest = logical_digest(sqlite_rows)
    if sqlite_digest != jsonl_digest:
        raise ExportError("JSONL/SQLite typed-row digest parity failed")
    conn = sqlite3.connect(sqlite_path)
    try:
        if str(conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            raise ExportError("SQLite integrity_check failed")
        if list(conn.execute("PRAGMA foreign_key_check")):
            raise ExportError("SQLite foreign_key_check failed")
        metadata = conn.execute(
            """
            SELECT run_id, sealed_fingerprint, logical_row_digest,
                   legal_history_claim, dataset_kind
            FROM dataset_metadata
            """
        ).fetchone()
        if metadata != (
            manifest["run_id"],
            manifest["sealed_fingerprint"],
            jsonl_digest,
            0,
            DATASET_KIND,
        ):
            raise ExportError("SQLite dataset_metadata mismatch")
    finally:
        conn.close()
    return manifest
