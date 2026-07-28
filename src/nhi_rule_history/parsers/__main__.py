from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.parsers.odt import parse_verified_odt_run


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nhi_rule_history.parsers",
        description=(
            "Parse verified official ODT attachments into source-local structural "
            "blocks and occurrence candidates; no legal-history semantics."
        ),
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path, required=True)
    parser.add_argument("--parse-run-id", required=True)
    args = parser.parse_args()
    result = parse_verified_odt_run(
        args.run_dir,
        args.stage_dir,
        parse_run_id=args.parse_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
