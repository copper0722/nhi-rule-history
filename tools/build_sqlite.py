#!/usr/bin/env python3
"""Build the portable SQLite projection from normalized table JSONL files."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


TABLE_ORDER = (
    "dataset_release",
    "source_artifact",
    "release_artifact",
    "official_event",
    "rule_identity",
    "rule_designation",
    "rule_snapshot",
    "official_event_effect",
    "rule_lineage_edge",
    "snapshot_evidence",
    "rule_block",
    "comparison_edge",
    "diff_hunk",
    "drug_concept",
    "drug_identifier",
    "rule_drug_link",
    "drug_atc_link",
    "indication",
    "rule_indication_link",
    "external_concept_link",
    "build_run",
    "build_issue",
    "search_document",
)


class BuildError(RuntimeError):
    pass


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BuildError(f"{path.name}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise BuildError(f"{path.name}:{line_number}: row is not an object")
            yield row


def sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def load_table(conn: sqlite3.Connection, table: str, path: Path) -> int:
    allowed = table_columns(conn, table)
    if not allowed:
        raise BuildError(f"schema missing table: {table}")

    inserted = 0
    for row in read_jsonl(path):
        unknown = set(row) - allowed
        if unknown:
            raise BuildError(
                f"{path.name}: unknown columns for {table}: {sorted(unknown)}"
            )
        if not row:
            raise BuildError(f"{path.name}: empty row")
        columns = sorted(row)
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        values = [sqlite_value(row[column]) for column in columns]
        conn.execute(
            f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
            values,
        )
        inserted += 1
    return inserted


def build(
    *,
    input_dir: Path,
    output: Path,
    schema: Path,
    fts_schema: Path | None,
    force: bool,
) -> dict[str, Any]:
    if output.exists():
        if not force:
            raise BuildError(f"output exists: {output}")
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    conn = sqlite3.connect(output)
    try:
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        for table in TABLE_ORDER:
            path = input_dir / f"{table}.jsonl"
            if not path.exists():
                counts[table] = 0
                continue
            counts[table] = load_table(conn, table, path)
        conn.commit()

        foreign_key_errors = list(conn.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise BuildError(
                f"foreign_key_check failed with {len(foreign_key_errors)} rows"
            )
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise BuildError(f"integrity_check failed: {integrity}")

        if fts_schema is not None:
            conn.executescript(fts_schema.read_text(encoding="utf-8"))
            conn.commit()

        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("sqlite_version", sqlite3.sqlite_version),
        )
        conn.commit()
    except Exception:
        conn.close()
        if output.exists():
            output.unlink()
        raise
    finally:
        if output.exists():
            conn.close()

    return {
        "status": "ok",
        "output": output.name,
        "sqlite_version": sqlite3.sqlite_version,
        "counts": counts,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=root / "database" / "sqlite-schema.sql",
    )
    parser.add_argument(
        "--with-fts",
        action="store_true",
        help="Build the optional FTS5 projection after loading data",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    fts_schema = root / "database" / "sqlite-fts.sql" if args.with_fts else None
    result = build(
        input_dir=args.input_dir,
        output=args.output,
        schema=args.schema,
        fts_schema=fts_schema,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
