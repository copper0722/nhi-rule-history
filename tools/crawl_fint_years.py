#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date
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
    FintKeywordCrawler,
    Seed,
    build_search_url,
    parse_fint_search,
)
from nhi_rule_history.raw import RawStore


COMPLETE_YEAR_STATUS = "complete_declared_keyword_set"


def partition_bounds(year: int, capture_cut: date) -> tuple[str, str]:
    if year < 1900 or year > capture_cut.year:
        raise ContractError("invalid FINT partition year")
    return (
        f"{year:04d}0101",
        (
            capture_cut.strftime("%Y%m%d")
            if year == capture_cut.year
            else f"{year:04d}1231"
        ),
    )


def require_complete_year_manifest(
    manifest: dict[str, object],
    *,
    start_date: str,
    end_date: str,
    attachment_policy: str,
) -> None:
    if manifest.get("status") != COMPLETE_YEAR_STATUS:
        raise ContractError("FINT year partition is not complete")
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or counts.get("issues") != 0:
        raise ContractError("FINT year partition has blocking issues")
    query_contract = manifest.get("query_contract")
    if not isinstance(query_contract, dict):
        raise ContractError("FINT year partition lacks a query contract")
    expected = {
        "start_date": start_date,
        "end_date": end_date,
        "valid": "3",
        "record_type": "etype_",
        "attachment_policy": attachment_policy,
    }
    for key, value in expected.items():
        if query_contract.get(key) != value:
            raise ContractError(
                f"FINT year partition contract mismatch: {key}"
            )


def _broad_receipt(
    client: FintCurlClient,
    *,
    start_date: str,
    end_date: str,
    store: RawStore,
) -> dict[str, object]:
    url = build_search_url(
        None,
        start_date=start_date,
        end_date=end_date,
        page=1,
    )
    response = client.get(url)
    parser = parse_fint_search(
        response.body,
        response.headers.get("content-type"),
    )
    count = parser.result_count()
    parser.validate_page(page=1, result_count=count)
    blob = store.put(response.body)
    return {
        "url": url,
        "observed_at": utc_now(),
        "result_count": count,
        "first_page_result_fingerprint": parser.result_fingerprint,
        "content_sha256": blob.sha256,
        "byte_size": blob.byte_size,
        "content_path": blob.relative_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate the currently published FINT surface in non-overlapping "
            "Gregorian-year partitions, with an exact broad-count receipt."
        )
    )
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--capture-cut", type=date.fromisoformat, required=True)
    parser.add_argument("--start-year", type=int, default=1900)
    parser.add_argument("--min-interval-seconds", type=float, default=0.8)
    parser.add_argument("--max-results-per-year", type=int, default=5000)
    parser.add_argument(
        "--attachment-policy",
        choices=("all", "nhi_candidate", "none"),
        default="none",
    )
    args = parser.parse_args()

    if args.start_year < 1900 or args.start_year > args.capture_cut.year:
        raise ContractError("invalid FINT start year")

    batch_dir = args.batch_dir
    batch_dir.mkdir(parents=True, exist_ok=True)
    store = RawStore(batch_dir)
    client = FintCurlClient()
    broad_start = f"{args.start_year:04d}0101"
    broad_end = args.capture_cut.strftime("%Y%m%d")
    try:
        before = _broad_receipt(
            client,
            start_date=broad_start,
            end_date=broad_end,
            store=store,
        )
    finally:
        client.close()

    year_receipts: list[dict[str, object]] = []
    for year in range(args.start_year, args.capture_cut.year + 1):
        partition_start, partition_end = partition_bounds(
            year,
            args.capture_cut,
        )
        run_dir = batch_dir / "years" / str(year)
        crawler = FintKeywordCrawler(
            run_dir,
            min_interval_seconds=args.min_interval_seconds,
            max_results_per_query=args.max_results_per_year,
            start_date=partition_start,
            end_date=partition_end,
            attachment_policy=args.attachment_policy,
        )
        try:
            manifest = crawler.crawl(
                [
                    Seed(
                        None,
                        "fint_unfiltered_year_partition",
                        f"{partition_start}__{partition_end}",
                    )
                ]
            )
        finally:
            crawler.close()
        require_complete_year_manifest(
            manifest,
            start_date=partition_start,
            end_date=partition_end,
            attachment_policy=args.attachment_policy,
        )
        manifest_path = run_dir / "fint-crawl-manifest.json"
        year_receipts.append(
            {
                "year": year,
                "start_date": partition_start,
                "end_date": partition_end,
                "match_count": manifest["counts"]["matches"],
                "document_number_group_count": manifest["counts"]["documents"],
                "attachment_declaration_count": manifest["counts"][
                    "attachment_declarations"
                ],
                "attachment_snapshot_count": manifest["counts"]["attachments"],
                "crawler_version": manifest["crawler_version"],
                "status": manifest["status"],
                "manifest_sha256": file_sha256(manifest_path),
                "manifest_path": str(manifest_path.relative_to(batch_dir)),
            }
        )

    client = FintCurlClient()
    try:
        after = _broad_receipt(
            client,
            start_date=broad_start,
            end_date=broad_end,
            store=store,
        )
    finally:
        client.close()

    partition_total = sum(
        int(receipt["match_count"]) for receipt in year_receipts
    )
    if before["result_count"] != after["result_count"]:
        raise ContractError("FINT broad result count changed during batch crawl")
    if (
        before["first_page_result_fingerprint"]
        != after["first_page_result_fingerprint"]
    ):
        raise ContractError("FINT broad first page changed during batch crawl")
    if partition_total != before["result_count"]:
        raise ContractError(
            "FINT yearly partition parity failed: "
            f"{partition_total} != {before['result_count']}"
        )

    manifest = {
        "schema": "nhi-rule-history/fint-yearly-enumeration-batch/v1",
        "status": (
            "yearly_enumeration_complete_pending_postcrawl_verification"
        ),
        "completed_at": utc_now(),
        "capture_contract": {
            "start_date": broad_start,
            "end_date": broad_end,
            "valid": "3",
            "record_type": "etype_",
            "keywords": [],
            "attachment_policy": args.attachment_policy,
        },
        "broad_before": before,
        "broad_after": after,
        "partition_match_total": partition_total,
        "year_partition_count": len(year_receipts),
        "year_partitions": year_receipts,
        "year_partition_fingerprint": sha256_bytes(
            canonical_json_bytes(year_receipts)
        ),
        "non_claim": (
            "This proves annual-partition parity against the broad official "
            "FINT count. The currently published date-bounded surface is not "
            "accepted until the separate postcrawl verifier re-fetches every "
            "annual search-index page. Neither receipt can prove that deleted, "
            "withdrawn, or never-indexed historical notices remain available."
        ),
    }
    destination = batch_dir / "fint-yearly-enumeration-manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(canonical_json_bytes(manifest))
    temporary.replace(destination)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
