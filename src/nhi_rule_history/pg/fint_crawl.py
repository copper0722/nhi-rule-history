from __future__ import annotations

import hashlib
import json
import uuid
from math import ceil
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.discovery.fint_keyword_crawler import CRAWLER_VERSION
from nhi_rule_history.discovery.fint_keyword_crawler import FintKeywordCrawler
from nhi_rule_history.discovery.fint_keyword_crawler import detect_media_type
from nhi_rule_history.discovery.fint_keyword_crawler import parse_fint_search
from nhi_rule_history.raw import RawStore


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise ContractError(
                    f"{path.name}:{line_number}: row must be an object"
                )
            result.append(row)
    return result


def _sha256_uuid_v8(namespace: str, value: str) -> uuid.UUID:
    digest = bytearray(
        hashlib.sha256(f"{namespace}\x1f{value}".encode()).digest()[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x80
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))


def _verify_manifest(run_dir: Path) -> tuple[dict[str, Any], str]:
    manifest_path = run_dir / "fint-crawl-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete_declared_keyword_set":
        raise ContractError("only complete FINT crawl manifests may enter PG")
    if manifest.get("crawler_version") != CRAWLER_VERSION:
        raise ContractError("unsupported FINT crawler version")
    counts = manifest.get("counts", {})
    if counts.get("issues") != 0:
        raise ContractError("FINT crawl has blocking issues")
    entries = manifest.get("files", [])
    if not isinstance(entries, list):
        raise ContractError("FINT manifest files must be an array")
    expected_files = set(FintKeywordCrawler.FILES.values())
    listed_files = [entry.get("filename") for entry in entries]
    if len(listed_files) != len(set(listed_files)):
        raise ContractError("FINT manifest contains duplicate file entries")
    if set(listed_files) != expected_files:
        raise ContractError("FINT manifest file set is incomplete or unknown")
    for entry in entries:
        path = run_dir / entry["filename"]
        if (
            not path.is_file()
            or path.stat().st_size != entry["byte_size"]
            or file_sha256(path) != entry["sha256"]
        ):
            raise ContractError(
                f"FINT manifest file verification failed: {path.name}"
            )
    return manifest, file_sha256(manifest_path)


def _validate_projection(
    *,
    manifest: dict[str, Any],
    observations: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    seeds: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    declarations: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    store: RawStore,
) -> None:
    collections = {
        "observations": observations,
        "queries": queries,
        "seeds": seeds,
        "documents": documents,
        "snapshots": snapshots,
        "matches": matches,
        "attachment_declarations": declarations,
        "attachments": attachments,
        "issues": issues,
    }
    counts = manifest["counts"]
    if set(counts) != set(collections):
        raise ContractError("FINT manifest count keys are incomplete or unknown")
    for name, rows in collections.items():
        if counts[name] != len(rows):
            raise ContractError(f"FINT manifest count mismatch: {name}")

    def unique(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            value = str(row.get(key, ""))
            if not value or value in result:
                raise ContractError(f"FINT projection duplicate/missing {key}")
            result[value] = row
        return result

    observation_by_id = unique(observations, "observation_id")
    if len({row["request_url"] for row in observations}) != len(observations):
        raise ContractError("FINT projection repeats an observation URL")
    for row in observations:
        expected_id = stable_id(
            "fint-crawl-observation",
            row["request_url"],
            row["content_sha256"],
        )
        if row["observation_id"] != expected_id:
            raise ContractError("FINT observation identity mismatch")
        if row["request_url"] != row["final_url"] or row["http_status"] != 200:
            raise ContractError("FINT observation transport contract mismatch")

    query_by_id = unique(queries, "query_id")
    document_by_id = unique(documents, "document_id")
    snapshot_by_id = unique(snapshots, "snapshot_id")
    match_by_id = unique(matches, "match_id")
    declaration_by_id = unique(
        declarations,
        "attachment_declaration_id",
    )
    attachment_by_id = unique(attachments, "attachment_snapshot_id")
    unique(seeds, "seed_id")
    unique(issues, "issue_id")

    for seed in seeds:
        if seed["query_id"] not in query_by_id:
            raise ContractError("FINT seed references an absent query")

    row_numbers: dict[str, set[int]] = {
        query_id: set() for query_id in query_by_id
    }
    matched_document_ids: set[str] = set()
    matched_snapshot_ids: set[str] = set()
    for match in matches:
        query = query_by_id.get(match["query_id"])
        snapshot = snapshot_by_id.get(match["snapshot_id"])
        observation = observation_by_id.get(match["detail_observation_id"])
        if query is None or snapshot is None or observation is None:
            raise ContractError("FINT match has an absent parent")
        if (
            snapshot["document_id"] != match["document_id"]
            or snapshot["detail_url"] != match["detail_url"]
            or snapshot["detail_observation_id"]
            != match["detail_observation_id"]
            or observation["resource_kind"] != "fint_detail_page"
            or observation["request_url"] != match["detail_url"]
        ):
            raise ContractError("FINT match/detail binding mismatch")
        number = int(match["row_number"])
        if number in row_numbers[match["query_id"]]:
            raise ContractError("FINT query repeats a RowNo")
        row_numbers[match["query_id"]].add(number)
        matched_document_ids.add(match["document_id"])
        matched_snapshot_ids.add(match["snapshot_id"])

    if set(document_by_id) != matched_document_ids:
        raise ContractError("FINT document groups and matches disagree")
    if set(snapshot_by_id) != matched_snapshot_ids:
        raise ContractError("FINT snapshots and matches disagree")

    for query_id, query in query_by_id.items():
        expected = int(query["expected_rows"])
        if int(query["fetched_rows"]) != expected:
            raise ContractError("FINT query fetched/expected mismatch")
        if row_numbers[query_id] != set(range(1, expected + 1)):
            raise ContractError("FINT query RowNo set is not contiguous 1..N")
        page_observations = query.get("search_page_observation_ids")
        if not isinstance(page_observations, list) or not page_observations:
            raise ContractError("FINT query search-page receipt is absent")
        if len(page_observations) != max(1, ceil(expected / 10)):
            raise ContractError("FINT query search-page count mismatch")
        if page_observations[0] != query["search_observation_id"]:
            raise ContractError("FINT query first search observation mismatch")
        page_fingerprints: list[dict[str, Any]] = []
        for page, observation_id in enumerate(page_observations, 1):
            observation = observation_by_id.get(observation_id)
            if (
                observation is None
                or observation["resource_kind"] != "fint_search_page"
            ):
                raise ContractError(
                    "FINT query search-page observation binding failed"
                )
            if (
                page == 1
                and observation["request_url"] != query["search_url"]
            ):
                raise ContractError("FINT query first search URL mismatch")
            if not store.verify(
                observation["content_path"],
                observation["content_sha256"],
                observation["byte_size"],
            ):
                raise ContractError(
                    "FINT query search-page raw verification failed"
                )
            parser = parse_fint_search(
                store.read(
                    observation["content_path"],
                    observation["content_sha256"],
                    observation["byte_size"],
                ),
                observation["response_headers"].get("content-type"),
            )
            if parser.result_count() != expected:
                raise ContractError(
                    "FINT query stored search-page total mismatch"
                )
            parser.validate_page(page=page, result_count=expected)
            page_fingerprints.append(
                {
                    "page": page,
                    "result_fingerprint": parser.result_fingerprint,
                }
            )
        if sha256_bytes(
            canonical_json_bytes(page_fingerprints)
        ) != query["search_index_sha256"]:
            raise ContractError("FINT query search-index hash mismatch")

    declarations_by_match: dict[str, list[dict[str, Any]]] = {}
    for declaration in declarations:
        match = match_by_id.get(declaration["match_id"])
        if match is None:
            raise ContractError("FINT attachment declaration has no match")
        if (
            declaration["snapshot_id"] != match["snapshot_id"]
            or declaration["document_id"] != match["document_id"]
        ):
            raise ContractError("FINT attachment declaration binding mismatch")
        declarations_by_match.setdefault(match["match_id"], []).append(
            declaration
        )

    attachment_by_declaration: dict[str, dict[str, Any]] = {}
    for attachment in attachments:
        declaration = declaration_by_id.get(
            attachment["attachment_declaration_id"]
        )
        observation = observation_by_id.get(attachment["observation_id"])
        if declaration is None or observation is None:
            raise ContractError("FINT attachment snapshot has an absent parent")
        if declaration["attachment_declaration_id"] in attachment_by_declaration:
            raise ContractError("FINT declaration has multiple byte snapshots")
        if (
            observation["resource_kind"] != "fint_attachment"
            or observation["request_url"] != declaration["source_url"]
        ):
            raise ContractError("FINT attachment observation binding mismatch")
        attachment_by_declaration[
            declaration["attachment_declaration_id"]
        ] = attachment

    for declaration_id, declaration in declaration_by_id.items():
        if declaration["fetch_state"] == "fetch_failed":
            raise ContractError(
                "complete FINT projection contains a failed attachment fetch"
            )
        fetched = declaration_id in attachment_by_declaration
        if fetched != (declaration["fetch_state"] == "fetched"):
            raise ContractError("FINT attachment fetch-state parity failed")

    if attachment_by_id and not declarations:
        raise ContractError("FINT attachment snapshots lack declarations")


def load_fint_crawl(
    connection: psycopg.Connection[Any],
    run_dir: Path,
) -> dict[str, Any]:
    manifest, manifest_sha256 = _verify_manifest(run_dir)
    counts = manifest["counts"]
    seed_input_sha256 = next(
        entry["sha256"]
        for entry in manifest["files"]
        if entry["filename"] == "fint-query-seeds.jsonl"
    )
    run_id = _sha256_uuid_v8("fint-crawl-run", manifest_sha256)
    store = RawStore(run_dir)

    observations = _rows(run_dir / "fint-crawl-observations.jsonl")
    queries = _rows(run_dir / "fint-queries.jsonl")
    seeds = _rows(run_dir / "fint-query-seeds.jsonl")
    documents = _rows(run_dir / "fint-documents.jsonl")
    snapshots = _rows(run_dir / "fint-document-snapshots.jsonl")
    matches = _rows(run_dir / "fint-query-document-matches.jsonl")
    declarations = _rows(run_dir / "fint-attachment-declarations.jsonl")
    attachments = _rows(run_dir / "fint-attachment-snapshots.jsonl")
    issues = _rows(run_dir / "fint-crawl-issues.jsonl")
    _validate_projection(
        manifest=manifest,
        observations=observations,
        queries=queries,
        seeds=seeds,
        documents=documents,
        snapshots=snapshots,
        matches=matches,
        declarations=declarations,
        attachments=attachments,
        issues=issues,
        store=store,
    )
    started_at = min(
        (row["observed_at"] for row in observations),
        default=manifest["completed_at"],
    )
    query_contract = manifest["query_contract"]

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"nhi-rule-history-fint-crawl:{run_id}",),
            )
            cursor.execute(
                """
SELECT state, input_sha256, output_sha256
FROM nhi_rule_history_edition.fint_crawl_run
WHERE run_id = %s
""",
                (run_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    existing[0] == "sealed"
                    and existing[1] == seed_input_sha256
                    and existing[2] == manifest_sha256
                ):
                    return {
                        "run_id": str(run_id),
                        "status": "already_sealed",
                        "counts": counts,
                    }
                raise ContractError("conflicting existing FINT crawl run")

            cursor.execute(
                """
INSERT INTO nhi_rule_history_edition.fint_crawl_run (
  run_id, crawler_version, input_sha256, start_date, end_date,
  valid_code, record_type, state, started_at
) VALUES (%s,%s,%s,%s,%s,%s,%s,'loading',%s)
""",
                (
                    run_id,
                    manifest.get("crawler_version", CRAWLER_VERSION),
                    seed_input_sha256,
                    query_contract["start_date"],
                    query_contract["end_date"],
                    query_contract["valid"],
                    query_contract["record_type"],
                    started_at,
                ),
            )

            for row in observations:
                if not store.verify(
                    row["content_path"],
                    row["content_sha256"],
                    row["byte_size"],
                ):
                    raise ContractError(
                        f"raw observation failed verification: "
                        f"{row['observation_id']}"
                    )
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_crawl_observation
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["observation_id"],
                        row["request_url"],
                        row["final_url"],
                        row["resource_kind"],
                        row["observed_at"],
                        row["http_status"],
                        Jsonb(row["response_headers"]),
                        row["content_sha256"],
                        row["byte_size"],
                        row["content_path"],
                    ),
                )

            for row in queries:
                keywords = (
                    row["keywords"]
                    if "keywords" in row
                    else [row["keyword"]]
                )
                normalized = (
                    row["normalized_keywords"]
                    if "normalized_keywords" in row
                    else [row["normalized_keyword"]]
                )
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_query
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["query_id"],
                        Jsonb(keywords),
                        Jsonb(normalized),
                        row["search_url"],
                        row["search_observation_id"],
                        Jsonb(row["search_page_observation_ids"]),
                        row["search_index_sha256"],
                        row["index_verified_at"],
                        row["expected_rows"],
                        row["fetched_rows"],
                        row["completed_at"],
                    ),
                )

            for row in seeds:
                keywords = (
                    row["keywords"]
                    if "keywords" in row
                    else [row["keyword"]]
                )
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_query_seed
VALUES (%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["seed_id"],
                        row["query_id"],
                        Jsonb(keywords),
                        row["origin_kind"],
                        row["origin_locator"],
                    ),
                )

            for row in documents:
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_document_number_identity
VALUES (%s,%s,%s,%s)
ON CONFLICT (document_id) DO NOTHING
""",
                    (
                        row["document_id"],
                        row["normalized_document_number"],
                        row["first_observed_raw_document_number"],
                        row["first_observed_at"],
                    ),
                )
                cursor.execute(
                    """
SELECT normalized_document_number
FROM nhi_rule_history_edition.fint_document_number_identity
WHERE document_id = %s
""",
                    (row["document_id"],),
                )
                stored = cursor.fetchone()
                if (
                    stored is None
                    or stored[0] != row["normalized_document_number"]
                ):
                    raise ContractError(
                        f"FINT document-number grouping collision: "
                        f"{row['document_id']}"
                    )

            for row in snapshots:
                payload = store.read(
                    row["record_text_path"],
                    row["record_text_sha256"],
                    row["record_text_byte_size"],
                )
                record_text = payload.decode("utf-8", errors="strict")
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_document_snapshot
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["snapshot_id"],
                        row["document_id"],
                        row["detail_url"],
                        row["detail_observation_id"],
                        record_text,
                        row["record_text_sha256"],
                        row["record_text_byte_size"],
                        row["record_text_path"],
                        row.get("document_date_raw"),
                        row["observed_at"],
                    ),
                )

            for row in matches:
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_query_document_match
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["match_id"],
                        row["query_id"],
                        row["document_id"],
                        row["snapshot_id"],
                        row["row_number"],
                        row["detail_url"],
                        row["detail_observation_id"],
                        row["observed_at"],
                    ),
                )

            for row in declarations:
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_attachment_declaration
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["attachment_declaration_id"],
                        row["document_id"],
                        row["snapshot_id"],
                        row["match_id"],
                        row["source_url"],
                        row["source_label"],
                        row["source_label_missing"],
                        row["attachment_ordinal"],
                        row["fetch_state"],
                    ),
                )

            for row in attachments:
                if not store.verify(
                    row["content_path"],
                    row["content_sha256"],
                    row["byte_size"],
                ):
                    raise ContractError(
                        f"raw attachment failed verification: "
                        f"{row['attachment_snapshot_id']}"
                    )
                attachment_payload = store.read(
                    row["content_path"],
                    row["content_sha256"],
                    row["byte_size"],
                )
                source_media_type = (
                    row.get("source_media_type") or row.get("media_type")
                )
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_attachment_snapshot
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["attachment_snapshot_id"],
                        row["attachment_declaration_id"],
                        row["observation_id"],
                        row["content_sha256"],
                        row["byte_size"],
                        row["content_path"],
                        source_media_type,
                        row.get("detected_media_type")
                        or detect_media_type(
                            attachment_payload,
                            source_media_type,
                        ),
                        row["observed_at"],
                    ),
                )

            for row in issues:
                cursor.execute(
                    """
INSERT INTO nhi_rule_history_edition.fint_crawl_issue
VALUES (%s,%s,%s,%s,%s,%s,%s)
""",
                    (
                        run_id,
                        row["issue_id"],
                        row["request_url"],
                        row["resource_kind"],
                        row["error_code"],
                        row["blocking"],
                        row["recorded_at"],
                    ),
                )

            cursor.execute(
                """
UPDATE nhi_rule_history_edition.fint_crawl_run
SET state = 'sealed',
    seed_count = %s,
    query_count = %s,
    document_count = %s,
    snapshot_count = %s,
    match_count = %s,
    attachment_count = %s,
    attachment_declaration_count = %s,
    observation_count = %s,
    issue_count = %s,
    output_sha256 = %s,
    sealed_at = %s
WHERE run_id = %s
""",
                (
                    counts["seeds"],
                    counts["queries"],
                    counts["documents"],
                    counts["snapshots"],
                    counts["matches"],
                    counts["attachments"],
                    counts["attachment_declarations"],
                    counts["observations"],
                    counts["issues"],
                    manifest_sha256,
                    manifest["completed_at"],
                    run_id,
                ),
            )

    return {
        "run_id": str(run_id),
        "status": "sealed",
        "counts": counts,
        "manifest_sha256": manifest_sha256,
    }
