"""Offline release-preparation command line adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .prepare import prepare_release


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare verified stage release assets without publishing."
    )
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_release(
        export_dir=args.export_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
