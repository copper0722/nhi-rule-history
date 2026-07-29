#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.announced_dyslipidemia import (
    load_announced_dyslipidemia,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load the sealed 2026-09-01 dyslipidemia amendment patch"
    )
    parser.add_argument("odt_path", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    result = load_announced_dyslipidemia(
        args.odt_path,
        conninfo=args.dsn,
        activate=not args.no_activate,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

