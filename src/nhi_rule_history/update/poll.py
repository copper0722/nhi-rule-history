"""Immutable RSS poll observations and deterministic new-item selection."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.update.rss import (
    RSS_CLASSIFIER_VERSION,
    RSS_LEGACY_CLASSIFIER_VERSION,
    OfficialResponse,
    RssItem,
    parse_rss,
)


POLL_SCHEMA = "nhi-rule-history/rss-poll-observation/v1"
RSS_PARSER_VERSION = "nhi-rule-history-rss/1.1.0"
RSS_LEGACY_PARSER_VERSION = "nhi-rule-history-rss/1.0.0"


@dataclass(frozen=True)
class PollObservation:
    poll_id: str
    path: Path
    manifest: dict[str, Any]
    items: tuple[RssItem, ...]
    new_items: tuple[RssItem, ...]
    replayed: bool


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def observe_feed(
    poll_root: Path,
    *,
    response: OfficialResponse,
    observed_guids: Iterable[str],
    previous_item_count: int | None,
    collapse_ratio: float = 0.5,
) -> PollObservation:
    items = parse_rss(response.body)
    if (
        previous_item_count is not None
        and previous_item_count >= 4
        and len(items) < math.ceil(previous_item_count * collapse_ratio)
    ):
        raise ContractError("official RSS item count collapsed unexpectedly")
    observed = set(observed_guids)
    observed_guid_set_sha = sha256_bytes(
        canonical_json_bytes(sorted(observed))
    )
    new_items = tuple(
        item
        for item in items
        if item.guid not in observed and item.is_likely_drug_rule
    )
    feed_sha = sha256_bytes(response.body)
    item_payloads = [item.as_dict() for item in items]
    item_sequence_sha = sha256_bytes(canonical_json_bytes(item_payloads))
    poll_id = stable_id(
        "nhi-rss-poll",
        response.request_url,
        response.observed_at,
        feed_sha,
        item_sequence_sha,
        observed_guid_set_sha,
        str(previous_item_count),
        format(collapse_ratio, ".12g"),
    )
    manifest = {
        "schema": POLL_SCHEMA,
        "poll_id": poll_id,
        "feed_url": response.request_url,
        "final_url": response.final_url,
        "observed_at": response.observed_at,
        "http_status": response.status_code,
        "response_headers": response.headers,
        "feed_artifact_sha256": feed_sha,
        "feed_byte_size": len(response.body),
        "feed_content_path": "feed.xml",
        "parser_version": RSS_PARSER_VERSION,
        "item_count": len(items),
        "item_sequence_sha256": item_sequence_sha,
        "previous_item_count": previous_item_count,
        "collapse_ratio": collapse_ratio,
        "observed_guid_set_sha256": observed_guid_set_sha,
        "items": item_payloads,
        "new_likely_drug_rule_guids": [item.guid for item in new_items],
    }
    poll_root.mkdir(parents=True, exist_ok=True)
    destination = poll_root / poll_id
    if destination.exists():
        verify_poll(destination)
        existing = json.loads(
            (destination / "manifest.json").read_text(encoding="utf-8")
        )
        return PollObservation(
            poll_id,
            destination,
            existing,
            tuple(items),
            new_items,
            True,
        )
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{poll_id}.", dir=poll_root)
    )
    try:
        with (temporary / "feed.xml").open("xb") as stream:
            stream.write(response.body)
            stream.flush()
            os.fsync(stream.fileno())
        with (temporary / "manifest.json").open("xb") as stream:
            stream.write(canonical_json_bytes(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(temporary)
        verify_poll(temporary)
        os.replace(temporary, destination)
        _fsync_directory(poll_root)
    except Exception:
        for path in sorted(temporary.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temporary.rmdir()
        raise
    return PollObservation(
        poll_id,
        destination,
        manifest,
        tuple(items),
        new_items,
        False,
    )


def verify_poll(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    feed_path = path / "feed.xml"
    if not manifest_path.is_file() or not feed_path.is_file():
        raise ContractError("RSS poll package is incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("RSS poll manifest is malformed") from exc
    if manifest.get("schema") != POLL_SCHEMA:
        raise ContractError("unexpected RSS poll schema")
    if (
        manifest.get("feed_byte_size") != feed_path.stat().st_size
        or manifest.get("feed_artifact_sha256") != file_sha256(feed_path)
    ):
        raise ContractError("RSS poll feed artifact mismatch")
    items = parse_rss(feed_path.read_bytes())
    parser_version = manifest.get("parser_version")
    classifier_version = {
        RSS_LEGACY_PARSER_VERSION: RSS_LEGACY_CLASSIFIER_VERSION,
        RSS_PARSER_VERSION: RSS_CLASSIFIER_VERSION,
    }.get(parser_version)
    if classifier_version is None:
        raise ContractError("unsupported RSS poll parser version")
    item_payloads = [
        item.as_dict(classifier_version=classifier_version) for item in items
    ]
    sequence_sha = sha256_bytes(canonical_json_bytes(item_payloads))
    if (
        manifest.get("item_count") != len(items)
        or manifest.get("items") != item_payloads
        or manifest.get("item_sequence_sha256") != sequence_sha
    ):
        raise ContractError("RSS poll item projection mismatch")
    expected_id = stable_id(
        "nhi-rss-poll",
        manifest["feed_url"],
        manifest["observed_at"],
        manifest["feed_artifact_sha256"],
        sequence_sha,
        manifest["observed_guid_set_sha256"],
        str(manifest.get("previous_item_count")),
        format(float(manifest.get("collapse_ratio")), ".12g"),
    )
    if manifest.get("poll_id") != expected_id:
        raise ContractError("RSS poll identity mismatch")
    return {
        "status": "passed",
        "poll_id": expected_id,
        "item_count": len(items),
        "new_item_count": len(
            manifest.get("new_likely_drug_rule_guids", [])
        ),
    }
