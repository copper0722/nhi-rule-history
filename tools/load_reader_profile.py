#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.reader_profile import load_reader_profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load a source-bound specialized clause reader profile"
    )
    parser.add_argument("profile_path", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    result = load_reader_profile(
        args.profile_path,
        conninfo=args.dsn,
        activate=not args.no_activate,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
