#!/usr/bin/env python3
"""Fetch one exact NHI drug-item/ATC/rule-link CSV snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from nhi_rule_history.nhi_drug_linkage import acquire_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--allow-insecure-tls", action="store_true")
    parser.add_argument(
        "--retrieved-at",
        help="ISO-8601 UTC timestamp override for deterministic tests",
    )
    args = parser.parse_args()

    retrieved_at = None
    if args.retrieved_at:
        retrieved_at = dt.datetime.fromisoformat(
            args.retrieved_at.replace("Z", "+00:00")
        )
    result = acquire_snapshot(
        output_dir=args.output_dir,
        retrieved_at=retrieved_at,
        ca_file=args.ca_file,
        allow_insecure_tls=args.allow_insecure_tls,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
