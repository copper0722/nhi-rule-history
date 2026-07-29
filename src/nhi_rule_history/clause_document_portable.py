"""Lossless portable projection for sealed normalized clause documents.

PostgreSQL remains the only writable authority.  JSONL preserves one canonical
JSON object per logical PostgreSQL row.  SQLite stores the same canonical row
JSON in one table per PostgreSQL relation, which keeps the export lossless and
queryable through SQLite JSON functions without inventing a second schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg

SCHEMA = "nhi_rule_history_announced"
EXPORT_SCHEMA = "nhi-rule-history/clause-document-portable/v1"
SQLITE_SCHEMA = "nhi-rule-history/clause-document-sqlite/v1"
TABLES = (
    "clause_document_work",
    "clause_document_node_work",
    "clause_document_normalization_run",
    "clause_document_expression",
    "clause_document_expression_relation",
    "clause_document_source_block",
    "clause_document_node",
    "clause_document_node_identity",
    "clause_document_table",
    "clause_document_table_row",
    "clause_document_table_cell",
    "clause_document_table_cell_content",
    "clause_document_source_span",
    "clause_document_normalization_receipt",
    "clause_document_diff_run",
    "clause_document_node_lineage",
    "clause_document_diff_hunk",
    "clause_document_inline_diff_segment",
    "clause_document_normalization_control_event",
    "clause_document_diff_control_event",
)
DIFF_SCOPED_TABLES = frozenset(
    {
        "clause_document_diff_run",
        "clause_document_node_lineage",
        "clause_document_diff_hunk",
        "clause_document_inline_diff_segment",
        "clause_document_diff_control_event",
    }
)
PRIVATE_PATTERN = re.compile(
    r"(?:/Users/|postgresql://|password=|api[_-]?key|bearer\s)",
    re.IGNORECASE,
)


class PortableClauseDocumentError(RuntimeError):
    """The portable projection failed a losslessness or safety gate."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _row_set_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    hashes = sorted(_sha256_text(_canonical_json(row)) for row in rows)
    return _sha256_text("\n".join(hashes))


def _table_rows(
    connection: psycopg.Connection[Any],
    table: str,
    *,
    normalization_run_id: str,
    diff_run_id: str,
    clause_work_id: str,
) -> list[dict[str, Any]]:
    if table == "clause_document_work":
        predicate = "clause_work_id=%s"
        params = (clause_work_id,)
    elif table == "clause_document_node_work":
        predicate = "clause_work_id=%s"
        params = (clause_work_id,)
    elif table in DIFF_SCOPED_TABLES:
        predicate = "diff_run_id=%s"
        params = (diff_run_id,)
    elif table == "clause_document_normalization_control_event":
        predicate = "normalization_run_id=%s"
        params = (normalization_run_id,)
    elif table == "clause_document_node_identity":
        predicate = "normalization_run_id=%s"
        params = (normalization_run_id,)
    else:
        predicate = "normalization_run_id=%s"
        params = (normalization_run_id,)
    query = (
        f"SELECT to_jsonb(row_value) FROM {SCHEMA}.{table} row_value "
        f"WHERE {predicate}"
    )
    rows = [
        dict(row[0])
        for row in connection.execute(query, params).fetchall()
    ]
    rows.sort(key=_canonical_json)
    return rows


def fetch_active_rows(
    connection: psycopg.Connection[Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    normalization_row = connection.execute(
        f"""
        SELECT normalization_run_id
        FROM {SCHEMA}.v_active_clause_document_normalization_run
        """
    ).fetchone()
    diff_row = connection.execute(
        f"""
        SELECT diff_run_id
        FROM {SCHEMA}.v_active_clause_document_diff_run
        """
    ).fetchone()
    if normalization_row is None or diff_row is None:
        raise PortableClauseDocumentError(
            "sealed active normalization and diff runs are required"
        )
    normalization_run_id = str(normalization_row[0])
    diff_run_id = str(diff_row[0])
    clause_work_rows = connection.execute(
        f"""
        SELECT DISTINCT clause_work_id
        FROM {SCHEMA}.clause_document_expression
        WHERE normalization_run_id=%s
        """,
        (normalization_run_id,),
    ).fetchall()
    if len(clause_work_rows) != 1:
        raise PortableClauseDocumentError(
            "active normalization does not resolve one clause Work"
        )
    clause_work_id = str(clause_work_rows[0][0])
    rows = {
        table: _table_rows(
            connection,
            table,
            normalization_run_id=normalization_run_id,
            diff_run_id=diff_run_id,
            clause_work_id=clause_work_id,
        )
        for table in TABLES
    }
    return rows, {
        "normalization_run_id": normalization_run_id,
        "diff_run_id": diff_run_id,
        "clause_work_id": clause_work_id,
    }


def export_jsonl(
    connection: psycopg.Connection[Any],
    output_dir: Path,
) -> dict[str, Any]:
    rows_by_table, identity = fetch_active_rows(connection)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    table_receipts: dict[str, dict[str, Any]] = {}
    for table in TABLES:
        rows = rows_by_table[table]
        text = "".join(f"{_canonical_json(row)}\n" for row in rows)
        if PRIVATE_PATTERN.search(text):
            raise PortableClauseDocumentError(
                f"{table} contains a private path or credential-shaped value"
            )
        path = output_dir / f"{table}.jsonl"
        path.write_text(text, encoding="utf-8", newline="\n")
        table_receipts[table] = {
            "row_count": len(rows),
            "jsonl_sha256": _sha256_bytes(path.read_bytes()),
            "logical_row_set_fingerprint": _row_set_fingerprint(rows),
        }
    manifest = {
        "schema": EXPORT_SCHEMA,
        **identity,
        "table_order": list(TABLES),
        "tables": table_receipts,
    }
    manifest["manifest_fingerprint"] = _sha256_text(
        _canonical_json(manifest)
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PortableClauseDocumentError(
                f"{path.name}:{line_number} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise PortableClauseDocumentError(
                f"{path.name}:{line_number} is not an object"
            )
        if _canonical_json(row) != line:
            raise PortableClauseDocumentError(
                f"{path.name}:{line_number} is not canonical JSON"
            )
        rows.append(row)
    return rows


def build_sqlite(
    jsonl_dir: Path,
    output: Path,
) -> dict[str, Any]:
    jsonl_dir = Path(jsonl_dir)
    manifest = json.loads(
        (jsonl_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schema") != EXPORT_SCHEMA:
        raise PortableClauseDocumentError("portable manifest schema drifted")
    if manifest.get("table_order") != list(TABLES):
        raise PortableClauseDocumentError("portable table order drifted")
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        path = jsonl_dir / f"{table}.jsonl"
        rows = _read_jsonl(path)
        receipt = manifest["tables"][table]
        if (
            receipt["row_count"] != len(rows)
            or receipt["jsonl_sha256"] != _sha256_bytes(path.read_bytes())
            or receipt["logical_row_set_fingerprint"]
            != _row_set_fingerprint(rows)
        ):
            raise PortableClauseDocumentError(
                f"{table} JSONL receipt drifted"
            )
        rows_by_table[table] = rows
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE portable_manifest (
              table_name TEXT PRIMARY KEY,
              row_count INTEGER NOT NULL,
              jsonl_sha256 TEXT NOT NULL,
              logical_row_set_fingerprint TEXT NOT NULL
            )
            """
        )
        for table in TABLES:
            connection.execute(
                f"""
                CREATE TABLE "{table}" (
                  row_sha256 TEXT PRIMARY KEY,
                  row_json TEXT NOT NULL CHECK (json_valid(row_json))
                )
                """
            )
            rows = rows_by_table[table]
            connection.executemany(
                f'INSERT INTO "{table}" (row_sha256,row_json) VALUES (?,?)',
                [
                    (
                        _sha256_text(_canonical_json(row)),
                        _canonical_json(row),
                    )
                    for row in rows
                ],
            )
            receipt = manifest["tables"][table]
            connection.execute(
                """
                INSERT INTO portable_manifest
                (table_name,row_count,jsonl_sha256,
                 logical_row_set_fingerprint)
                VALUES (?,?,?,?)
                """,
                (
                    table,
                    receipt["row_count"],
                    receipt["jsonl_sha256"],
                    receipt["logical_row_set_fingerprint"],
                ),
            )
        connection.commit()
        connection.execute("VACUUM")
        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()
        if integrity != ("ok",):
            raise PortableClauseDocumentError(
                "SQLite integrity check failed"
            )
        for table in TABLES:
            sqlite_rows = [
                json.loads(row[0])
                for row in connection.execute(
                    f'SELECT row_json FROM "{table}" ORDER BY row_sha256'
                )
            ]
            if (
                len(sqlite_rows) != len(rows_by_table[table])
                or _row_set_fingerprint(sqlite_rows)
                != _row_set_fingerprint(rows_by_table[table])
            ):
                raise PortableClauseDocumentError(
                    f"{table} PostgreSQL/JSONL/SQLite parity failed"
                )
    finally:
        connection.close()
    sqlite_sha256 = _sha256_bytes(output.read_bytes())
    return {
        "schema": SQLITE_SCHEMA,
        "source_manifest_fingerprint": manifest["manifest_fingerprint"],
        "normalization_run_id": manifest["normalization_run_id"],
        "diff_run_id": manifest["diff_run_id"],
        "table_count": len(TABLES),
        "logical_row_count": sum(
            receipt["row_count"]
            for receipt in manifest["tables"].values()
        ),
        "sqlite_sha256": sqlite_sha256,
        "sqlite_integrity_check": "ok",
        "logical_row_parity": "passed",
        "builder_sqlite_version": sqlite3.sqlite_version,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--dsn", required=True)
    export_parser.add_argument("--output-dir", type=Path, required=True)
    sqlite_parser = subparsers.add_parser("build-sqlite")
    sqlite_parser.add_argument("--jsonl-dir", type=Path, required=True)
    sqlite_parser.add_argument("--output", type=Path, required=True)
    sqlite_parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.command == "export":
        with psycopg.connect(args.dsn) as connection:
            result = export_jsonl(connection, args.output_dir)
    else:
        result = build_sqlite(args.jsonl_dir, args.output)
        if args.receipt:
            args.receipt.write_text(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
