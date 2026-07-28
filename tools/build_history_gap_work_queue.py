#!/usr/bin/env python3
"""Build a reproducible clause-date reconstruction work queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nhi_rule_history.history_gap_work_queue import (  # noqa: E402
    write_gap_work_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cross-format-ledger", type=Path, required=True)
    parser.add_argument("--document-candidate-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--declared-cut", required=True)
    parser.add_argument("--expected-row-count", type=int)
    args = parser.parse_args()
    manifest = write_gap_work_queue(
        args.cross_format_ledger,
        args.document_candidate_ledger,
        args.output,
        args.manifest,
        declared_cut=args.declared_cut,
        expected_row_count=args.expected_row_count,
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "row_count": manifest["row_count"],
                "counts_by_priority_lane": manifest[
                    "counts_by_priority_lane"
                ],
                "queue_sha256": manifest["queue"]["sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
