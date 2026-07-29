#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    utc_now,
)
from nhi_rule_history.discovery.fint_keyword_crawler import (
    FintCurlClient,
    parse_fint_search,
)
from nhi_rule_history.raw import RawStore


def rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def index_fingerprint(
    *,
    page_rows: list[dict[str, object]],
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            [
                {
                    "page": row["page"],
                    "result_fingerprint": row["result_fingerprint"],
                }
                for row in page_rows
            ]
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-fetch every yearly FINT search-index page after a batch and "
            "prove it still matches the index used for detail enumeration."
        )
    )
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--min-interval-seconds", type=float, default=0.8)
    args = parser.parse_args()
    if args.min_interval_seconds < 0:
        raise ContractError("minimum interval must not be negative")

    batch_manifest_path = (
        args.batch_dir / "fint-yearly-enumeration-manifest.json"
    )
    batch_manifest = json.loads(
        batch_manifest_path.read_text(encoding="utf-8")
    )
    if (
        batch_manifest.get("status")
        != "yearly_enumeration_complete_pending_postcrawl_verification"
    ):
        raise ContractError(
            "FINT yearly batch is not ready for postcrawl verification"
        )

    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    store = RawStore(args.receipt_dir)
    client = FintCurlClient()
    last_network_at: float | None = None
    receipts: list[dict[str, object]] = []
    try:
        for partition in batch_manifest["year_partitions"]:
            run_dir = args.batch_dir / partition["manifest_path"]
            run_dir = run_dir.parent
            manifest_path = run_dir / "fint-crawl-manifest.json"
            if file_sha256(manifest_path) != partition["manifest_sha256"]:
                raise ContractError("year manifest changed after batch seal")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["status"] != "complete_declared_keyword_set":
                raise ContractError("year partition is not complete")

            query_rows = rows(run_dir / "fint-queries.jsonl")
            if len(query_rows) != 1 or query_rows[0]["keywords"] != []:
                raise ContractError(
                    "year verification requires one unfiltered query"
                )
            query = query_rows[0]
            observations = {
                row["observation_id"]: row
                for row in rows(run_dir / "fint-crawl-observations.jsonl")
            }
            original_pages: list[dict[str, object]] = []
            live_pages: list[dict[str, object]] = []
            expected_rows = int(query["expected_rows"])
            for page, observation_id in enumerate(
                query["search_page_observation_ids"],
                1,
            ):
                observation = observations.get(observation_id)
                if observation is None:
                    raise ContractError(
                        "year query references an absent search observation"
                    )
                raw_path = run_dir / str(observation["content_path"])
                if (
                    not raw_path.is_file()
                    or raw_path.stat().st_size != observation["byte_size"]
                    or file_sha256(raw_path) != observation["content_sha256"]
                ):
                    raise ContractError(
                        "stored year search observation failed verification"
                    )
                original_parser = parse_fint_search(
                    raw_path.read_bytes(),
                    observation["response_headers"].get("content-type"),
                )
                if original_parser.result_count() != expected_rows:
                    raise ContractError(
                        "stored year search total disagrees with query"
                    )
                original_parser.validate_page(
                    page=page,
                    result_count=expected_rows,
                )
                original_pages.append(
                    {
                        "page": page,
                        "result_fingerprint": (
                            original_parser.result_fingerprint
                        ),
                    }
                )

                if last_network_at is not None:
                    remaining = args.min_interval_seconds - (
                        time.monotonic() - last_network_at
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                response = client.get(str(observation["request_url"]))
                last_network_at = time.monotonic()
                live_parser = parse_fint_search(
                    response.body,
                    response.headers.get("content-type"),
                )
                if live_parser.result_count() != expected_rows:
                    raise ContractError(
                        "live year search total changed after detail crawl"
                    )
                live_parser.validate_page(
                    page=page,
                    result_count=expected_rows,
                )
                blob = store.put(response.body)
                live_pages.append(
                    {
                        "page": page,
                        "request_url": observation["request_url"],
                        "result_fingerprint": live_parser.result_fingerprint,
                        "content_sha256": blob.sha256,
                        "byte_size": blob.byte_size,
                        "content_path": blob.relative_path,
                    }
                )

            original_sha = index_fingerprint(page_rows=original_pages)
            live_sha = index_fingerprint(page_rows=live_pages)
            if original_sha != query["search_index_sha256"]:
                raise ContractError(
                    "stored year index fingerprint disagrees with query"
                )
            if live_sha != original_sha:
                raise ContractError(
                    "year search index changed after detail crawl"
                )
            receipts.append(
                {
                    "year": partition["year"],
                    "expected_rows": expected_rows,
                    "search_page_count": len(live_pages),
                    "search_index_sha256": live_sha,
                    "live_pages": live_pages,
                }
            )
    finally:
        client.close()

    receipt = {
        "schema": "nhi-rule-history/fint-yearly-postcrawl-verification/v1",
        "status": "passed_all_year_search_indexes_unchanged",
        "verified_at": utc_now(),
        "batch_manifest_sha256": file_sha256(batch_manifest_path),
        "year_count": len(receipts),
        "match_total": sum(
            int(row["expected_rows"]) for row in receipts
        ),
        "year_receipts": receipts,
        "year_receipts_sha256": sha256_bytes(
            canonical_json_bytes(receipts)
        ),
        "non_claim": (
            "This verifies that every currently published yearly search index "
            "still matched the index used for its detail crawl. It does not "
            "prove that withdrawn or never-indexed records exist."
        ),
    }
    destination = args.receipt_dir / "fint-yearly-verification.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(canonical_json_bytes(receipt))
    temporary.replace(destination)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
