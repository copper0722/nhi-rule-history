#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.source_transcript import load_source_transcript


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and load one immutable source-transcript evidence bundle"
        )
    )
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    result = load_source_transcript(args.bundle_dir, conninfo=args.dsn)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
