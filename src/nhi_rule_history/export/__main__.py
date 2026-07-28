"""Command line adapter for exact sealed-stage export."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .stage import export_stage_from_connection, verify_export_directory


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Export or verify the bounded v1 source-occurrence stage."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    export = subcommands.add_parser("export")
    export.add_argument("--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN"))
    export.add_argument("--run-id", required=True)
    export.add_argument("--fingerprint", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument(
        "--schema",
        type=Path,
        default=root / "database" / "stage-sqlite-schema.sql",
    )

    verify = subcommands.add_parser("verify")
    verify.add_argument("--input-dir", type=Path, required=True)
    verify.add_argument("--run-id")
    verify.add_argument("--fingerprint")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "verify":
        result = verify_export_directory(
            args.input_dir,
            run_id=args.run_id,
            fingerprint=args.fingerprint,
        )
    else:
        if not args.dsn:
            raise SystemExit("--dsn or NHI_RULE_HISTORY_DSN is required")
        try:
            import psycopg
        except ImportError as exc:
            raise SystemExit("psycopg is required for PostgreSQL export") from exc
        with psycopg.connect(args.dsn) as connection:
            result = export_stage_from_connection(
                connection,
                run_id=args.run_id,
                fingerprint=args.fingerprint,
                output_dir=args.output_dir,
                schema_path=args.schema,
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
