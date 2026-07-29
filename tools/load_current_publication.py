#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.current_publication import load_current_publication


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load and activate the sealed current-clause publication"
    )
    parser.add_argument("stage_dir", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    result = load_current_publication(
        args.stage_dir,
        conninfo=args.dsn,
        activate=not args.no_activate,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
