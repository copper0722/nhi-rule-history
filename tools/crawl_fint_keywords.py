#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nhi_rule_history.discovery.fint_keyword_crawler import (
    FintKeywordCrawler,
    load_seeds,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a declared FINTQRY03 query frontier, every FINTQRY04 "
            "detail record, and attachment declarations into a raw store."
        )
    )
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--min-interval-seconds", type=float, default=0.8)
    parser.add_argument("--max-results-per-query", type=int, default=5000)
    parser.add_argument("--start-date", default="00000000")
    parser.add_argument("--end-date", default="99991231")
    parser.add_argument(
        "--attachment-policy",
        choices=("all", "nhi_candidate", "none"),
        default="all",
    )
    args = parser.parse_args()

    crawler = FintKeywordCrawler(
        args.run_dir,
        min_interval_seconds=args.min_interval_seconds,
        max_results_per_query=args.max_results_per_query,
        start_date=args.start_date,
        end_date=args.end_date,
        attachment_policy=args.attachment_policy,
    )
    try:
        manifest = crawler.crawl(load_seeds(args.seeds))
    finally:
        crawler.close()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return (
        0
        if manifest.get("status") == "complete_declared_keyword_set"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
