#!/usr/bin/env python3
"""Rebuild the `通則` official-edition history in PostgreSQL and export it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.edition_history import (
    build_sqlite,
    connect,
    export_jsonl,
    rebuild,
    write_reader_projection,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dsn",
        required=True,
        help="PostgreSQL connection string; keep private host details out of Git.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "data" / "manifests" / "nhi-history-odt-v1.jsonl",
    )
    parser.add_argument(
        "--jsonl-dir",
        type=Path,
        default=root / "data" / "templates" / "chapter-00",
    )
    parser.add_argument(
        "--reader-json",
        type=Path,
        default=(
            root
            / "prototype"
            / "reader"
            / "data"
            / "chapter-00-reader.json"
        ),
    )
    parser.add_argument("--sqlite-output", type=Path)
    parser.add_argument(
        "--sqlite-schema",
        type=Path,
        default=root / "database" / "edition-sqlite-schema.sql",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="Export an already-sealed PostgreSQL build without writing PG.",
    )
    args = parser.parse_args()

    with connect(args.dsn) as conn:
        rebuild_result = (
            {"status": "skipped"}
            if args.skip_rebuild
            else rebuild(conn, manifest_path=args.manifest)
        )
        export_result = export_jsonl(conn, output_dir=args.jsonl_dir)
        reader_result = write_reader_projection(
            conn,
            output=args.reader_json,
        )

    result = {
        "postgresql": rebuild_result,
        "jsonl": export_result,
        "reader_projection": reader_result,
    }
    if args.sqlite_output is not None:
        result["sqlite"] = build_sqlite(
            jsonl_dir=args.jsonl_dir,
            schema_path=args.sqlite_schema,
            output=args.sqlite_output,
            force=True,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
