#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

from nhi_rule_history.pg.fint_crawl import load_fint_crawl


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and seal one complete FINT crawler run in PG."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--dsn-env",
        default="NHI_RULE_HISTORY_PG_DSN",
        help="Environment variable containing the PostgreSQL DSN.",
    )
    args = parser.parse_args()
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        parser.error(f"missing DSN environment variable: {args.dsn_env}")
    with psycopg.connect(dsn) as connection:
        result = load_fint_crawl(connection, args.run_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
