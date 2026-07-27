"""PostgreSQL poll loader and per-RSS-identity stage-work transitions.

This module deliberately stops at the candidate staging boundary.  It can
record exact RSS observations, normalize durable work identities, and append
allowed stage transitions.  It has no canonical legal-history write path.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
    utc_now,
    validate_jsonl_row,
)
from nhi_rule_history.pg.common import json_text
from nhi_rule_history.fetch.runner import media_type
from nhi_rule_history.update.poll import (
    POLL_SCHEMA,
    RSS_LEGACY_PARSER_VERSION,
    RSS_PARSER_VERSION,
    verify_poll,
)
from nhi_rule_history.update.rss import (
    NHI_RSS_URL,
    RSS_CLASSIFIER_VERSION,
    RSS_LEGACY_CLASSIFIER_VERSION,
    RssItem,
    http_profile_sha256,
    parse_rss,
)


OPS_SCHEMA = "nhi_rule_history_update_ops"
QUEUE_SCHEMA = "nhi_rule_history_update_queue"
LOADER_VERSION = "nhi-rule-history-update-pg-queue/1.0.0"
CONTRACT_VERSION = "nhi-rule-history/rss-poll-pg-queue/v1"
RECEIPT_SCHEMA = "nhi-rule-history/rss-poll-pg-queue-receipt/v1"
TRANSITION_RECEIPT_SCHEMA = (
    "nhi-rule-history/work-item-transition-receipt/v1"
)
ATTEMPT_RECEIPT_SCHEMA = "nhi-rule-history/work-item-attempt-receipt/v1"
RECOVERY_AUTHORIZATION_RECEIPT_SCHEMA = (
    "nhi-rule-history/work-recovery-authorization-receipt/v2"
)
RECOVERY_TRANSITION_RECEIPT_SCHEMA = (
    "nhi-rule-history/work-recovery-transition-receipt/v2"
)
RECOVERY_ROUTE_ATTEMPT_RECEIPT_SCHEMA = (
    "nhi-rule-history/work-recovery-route-attempt-receipt/v2"
)
LEGACY_FAILURE_ADMISSION_RECEIPT_SCHEMA = (
    "nhi-rule-history/legacy-worker-failure-admission-receipt/v2"
)
LEGACY_WORKER_RUN_SCHEMA = "nhi-rule-history/worker-run/v2"
LEGACY_WORKER_ATTEMPT_SCHEMA = "nhi-rule-history/worker-attempt/v1"
LEGACY_FAILURE_BYTE_VERIFIER_CONTRACT = (
    "nhi-rule-history/legacy-failure-byte-verifier/v1"
)
LEGACY_FAILURE_ADMISSION_PAYLOAD_SCHEMA = (
    "nhi-rule-history/legacy-failure-admission-payload/v1"
)
LEGACY_ATTEMPT_ID_SCHEME = "sha256_hex_v1"
LEGACY_ATTEMPT_ID_ORIGIN = "immutable_worker_attempt_jsonl"
ATTEMPT_SANITIZATION_PROFILE = (
    "nhi-rule-history/attempt-evidence-sanitization/v1"
)
_UUID_NAMESPACE = uuid.UUID("9b541831-50a7-5867-8c52-5b7ac7ea272c")

_STATES = {
    "observed",
    "selected",
    "acquired",
    "corpus_registered",
    "proposal_running",
    "staged_needs_review",
    "staged_pending_anchor",
    "failed_terminal",
    "partition_required",
    "ignored_non_rule",
}
_TERMINAL_STATES = {
    "staged_needs_review",
    "staged_pending_anchor",
    "failed_terminal",
    "partition_required",
    "ignored_non_rule",
}
_ALLOWED_EDGES = {
    "observed": {"selected", "ignored_non_rule", "failed_terminal"},
    "selected": {"acquired", "failed_terminal", "ignored_non_rule"},
    "acquired": {"corpus_registered", "failed_terminal"},
    "corpus_registered": {"proposal_running", "failed_terminal"},
    "proposal_running": {
        "staged_needs_review",
        "staged_pending_anchor",
        "failed_terminal",
        "partition_required",
    },
}
_ATTEMPT_STATES = {
    "acquisition": {"selected"},
    "corpus_registration": {"acquired"},
    "proposal": {"proposal_running"},
}
_RECOVERY_ROUTE = "primary_then_fallback"
_RECOVERY_ATTEMPT_ROUTES = {"primary", "fallback"}
_LEGACY_FAILURE_STATUSES = {
    "execution_failed",
    "contract_failed",
    "timeout",
    "transport_failed",
}
_RECOVERY_TRANSITION_STATES = {
    "proposal_running",
    "staged_needs_review",
    "staged_pending_anchor",
    "failed_terminal",
    "partition_required",
}
_SENSITIVE_EVIDENCE_KEY = re.compile(
    r"(?i)(?:authorization|cookie|credential|password|passwd|secret|token|"
    r"api[_-]?key|dsn|conninfo)"
)
_SENSITIVE_EVIDENCE_VALUES = (
    (
        re.compile(r"(?i)\bpostgres(?:ql)?://[^,;\s]+"),
        "[REDACTED_DATABASE_URI]",
    ),
    (
        re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
        "[REDACTED_AUTH]",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)"
            r"(\s*[=:]\s*)[^,;\s]+"
        ),
        r"\1\2[REDACTED]",
    ),
)


class UpdateQueueError(RuntimeError):
    """Sanitized poll-queue validation, load, or transition failure."""


@dataclass(frozen=True)
class PreparedPollLoad:
    poll_id: str
    job_fingerprint: str
    job_id: str
    lease_id: str
    feed_observation_id: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    new_likely_guids: frozenset[str]


@dataclass(frozen=True)
class AppliedPollLoad:
    replayed: bool
    created_work_item_count: int
    selected_work_item_count: int
    ignored_work_item_count: int
    expected_projection: Mapping[str, Any]
    expected_fingerprint: str


def _deterministic_uuid(label: str, *parts: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, "\x1f".join((label, *parts))))


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise UpdateQueueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateQueueError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise UpdateQueueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _sanitize_attempt_evidence(value: Any) -> Any:
    """Return a deterministic JSON-safe evidence object with secrets removed."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        redacted_keys: list[str] = []
        for raw_key, raw_value in sorted(
            value.items(), key=lambda pair: str(pair[0])
        ):
            key = str(raw_key)
            if key == "_redacted_sensitive_keys":
                raise UpdateQueueError(
                    "attempt evidence uses a reserved sanitization key"
                )
            if _SENSITIVE_EVIDENCE_KEY.search(key):
                redacted_keys.append(key)
                continue
            sanitized[key] = _sanitize_attempt_evidence(raw_value)
        if redacted_keys:
            sanitized["_redacted_sensitive_keys"] = sorted(redacted_keys)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_attempt_evidence(item) for item in value]
    if isinstance(value, str):
        sanitized_text = value
        for pattern, replacement in _SENSITIVE_EVIDENCE_VALUES:
            sanitized_text = pattern.sub(replacement, sanitized_text)
        return sanitized_text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise UpdateQueueError(
        "attempt evidence must contain only JSON-compatible values"
    )


def _read_canonical_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateQueueError("poll manifest is not valid JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != POLL_SCHEMA
        or payload != canonical_json_bytes(manifest)
    ):
        raise UpdateQueueError("poll manifest is not canonical immutable JSON")
    return manifest


def _item_identities(
    payload: bytes,
    parsed: list[RssItem],
) -> list[dict[str, str | None]]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise UpdateQueueError("poll RSS XML is malformed") from exc
    channel = root.find("channel")
    raw_items = [] if channel is None else channel.findall("item")
    if len(raw_items) != len(parsed):
        raise UpdateQueueError("poll RSS item projection changed")
    identities: list[dict[str, str | None]] = []
    for raw_item, item in zip(raw_items, parsed, strict=True):
        guid_node = raw_item.find("guid")
        if guid_node is not None:
            explicit_guid = "".join(guid_node.itertext()).strip()
            if not explicit_guid:
                raise UpdateQueueError(
                    "RSS item contains an empty explicit GUID"
                )
            identity_kind = "rss_guid"
            identity_value = explicit_guid
        else:
            explicit_guid = None
            identity_kind = "official_detail_url"
            identity_value = item.link
        if identity_value != item.guid:
            raise UpdateQueueError(
                "RSS parser identity differs from the source identity"
            )
        identities.append(
            {
                "item_identity_kind": identity_kind,
                "item_identity_value": identity_value,
                "guid_raw": explicit_guid,
            }
        )
    return identities


def _prepare_poll_load(
    poll_path: Path,
    *,
    owner_key: str,
    poll_relative_root: str | None = None,
) -> PreparedPollLoad:
    poll_path = Path(poll_path)
    if not owner_key.strip():
        raise UpdateQueueError("owner_key must be non-empty")
    try:
        verify_poll(poll_path)
        manifest = _read_canonical_manifest(poll_path)
        if manifest.get("feed_content_path") != "feed.xml":
            raise UpdateQueueError(
                "poll feed content path is outside the immutable contract"
            )
        feed_path = poll_path / "feed.xml"
        feed_bytes = feed_path.read_bytes()
        items = parse_rss(feed_bytes)
    except (ContractError, KeyError, OSError) as exc:
        raise UpdateQueueError("immutable poll package verification failed") from exc
    item_identities = _item_identities(feed_bytes, items)

    if manifest.get("http_status") != 200:
        raise UpdateQueueError("poll package does not contain an HTTP 200 response")
    if manifest.get("feed_url") != NHI_RSS_URL:
        raise UpdateQueueError("poll package is not for the official NHI RSS feed")
    if manifest.get("feed_url") != manifest.get("final_url"):
        raise UpdateQueueError("poll package violates the no-redirect feed contract")
    parser_version = manifest.get("parser_version")
    classifier_version = {
        RSS_LEGACY_PARSER_VERSION: RSS_LEGACY_CLASSIFIER_VERSION,
        RSS_PARSER_VERSION: RSS_CLASSIFIER_VERSION,
    }.get(parser_version)
    if classifier_version is None:
        raise UpdateQueueError("poll package parser version is unsupported")
    response_headers = manifest.get("response_headers")
    if not isinstance(response_headers, dict):
        raise UpdateQueueError("poll response headers must be one JSON object")
    observed_at = _timestamp(manifest.get("observed_at"), "observed_at")
    feed_url = str(manifest["feed_url"])
    poll_id = str(manifest["poll_id"])
    feed_sha = file_sha256(feed_path)

    new_likely_raw = manifest.get("new_likely_drug_rule_guids")
    if (
        not isinstance(new_likely_raw, list)
        or any(not isinstance(value, str) or not value for value in new_likely_raw)
        or len(set(new_likely_raw)) != len(new_likely_raw)
    ):
        raise UpdateQueueError("poll selected-GUID projection is invalid")
    by_guid = {item.guid: item for item in items}
    new_likely = frozenset(new_likely_raw)
    if any(
        guid not in by_guid
        or not by_guid[guid].is_likely_drug_rule_for(classifier_version)
        for guid in new_likely
    ):
        raise UpdateQueueError(
            "poll selected-GUID projection contains a missing or non-rule item"
        )

    job_fingerprint = stable_id(
        "nhi-rss-poll-pg-queue",
        poll_id,
        feed_sha,
        CONTRACT_VERSION,
        LOADER_VERSION,
    )
    job_id = _deterministic_uuid("poll-job", job_fingerprint)
    lease_id = _deterministic_uuid("poll-lease", job_fingerprint)
    url_observation_id = _deterministic_uuid(
        "poll-url-observation", job_fingerprint, feed_url, feed_sha
    )
    feed_observation_id = _deterministic_uuid(
        "poll-feed-observation", job_fingerprint, feed_sha
    )
    relative_root = poll_relative_root or f"polls/{poll_id}"
    relative_path = Path(relative_root)
    if (
        not relative_root
        or "\\" in relative_root
        or relative_path.is_absolute()
        or ".." in relative_path.parts
    ):
        raise UpdateQueueError("poll_relative_root must be a safe relative path")

    channel_title = None
    try:
        root = ElementTree.fromstring(feed_bytes)
        channel = root.find("channel")
        if channel is not None:
            raw_title = channel.findtext("title")
            channel_title = raw_title.strip() if raw_title else None
    except ElementTree.ParseError:
        pass

    item_rows: list[dict[str, Any]] = []
    work_rows: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    for item, identity in zip(items, item_identities, strict=True):
        item_payload = item.as_dict(classifier_version=classifier_version)
        item_fingerprint = sha256_bytes(canonical_json_bytes(item_payload))
        identity_fingerprint = stable_id(
            "nhi-rss-work-identity",
            feed_url,
            str(identity["item_identity_kind"]),
            str(identity["item_identity_value"]),
        )
        work_item_id = _deterministic_uuid(
            "rss-work-item", identity_fingerprint
        )
        item_rows.append(
            {
                "feed_observation_id": feed_observation_id,
                "item_index": item.sequence,
                "item_fingerprint": item_fingerprint,
                "guid_raw": item.guid,
                "title_raw": item.title,
                "link_raw": item.link,
                "published_raw": item.published_at,
                "description_raw": item.description,
                "raw_item_sha256": item_fingerprint,
            }
        )
        work_rows.append(
            {
                "work_item_id": work_item_id,
                "rss_identity_fingerprint": identity_fingerprint,
                "item_identity_kind": identity["item_identity_kind"],
                "item_identity_value": identity["item_identity_value"],
                "source_feed_url": feed_url,
                "guid_raw": identity["guid_raw"],
                "first_feed_observation_id": feed_observation_id,
                "first_item_index": item.sequence,
                "first_item_fingerprint": item_fingerprint,
                "first_title_raw": item.title,
                "first_link_raw": item.link,
                "first_observed_at": _iso(observed_at),
                "_is_likely_drug_rule": item.is_likely_drug_rule_for(
                    classifier_version
                ),
                "_selected_by_poll": item.guid in new_likely,
            }
        )
        observation_rows.append(
            {
                "work_item_id": work_item_id,
                "feed_observation_id": feed_observation_id,
                "item_index": item.sequence,
                "observed_at": _iso(observed_at),
                "item_fingerprint": item_fingerprint,
            }
        )

    lease_end = observed_at + timedelta(seconds=1)
    rows: dict[str, tuple[dict[str, Any], ...]] = {
        "update_job": (
            {
                "job_id": job_id,
                "job_fingerprint": job_fingerprint,
                "contract_version": CONTRACT_VERSION,
                "runner_version": LOADER_VERSION,
                "feed_url": feed_url,
                "request_profile_sha256": http_profile_sha256(),
                "notification_window_start": _iso(
                    observed_at - timedelta(seconds=1)
                ),
                "notification_window_end": _iso(lease_end),
                "activation_cut": observed_at.date().isoformat(),
                "scheduled_at": _iso(observed_at),
            },
        ),
        "job_lease": (
            {
                "lease_id": lease_id,
                "job_id": job_id,
                "owner_key": owner_key,
                "acquired_at": _iso(observed_at),
                "expires_at": _iso(lease_end),
                "max_runtime_seconds": 1,
            },
        ),
        "content_artifact": (
            {
                "artifact_sha256": feed_sha,
                "byte_size": len(feed_bytes),
                "media_type": media_type(response_headers, feed_bytes),
                "bundle_relative_path": (
                    relative_path / str(manifest["feed_content_path"])
                ).as_posix(),
                "first_observed_at": _iso(observed_at),
            },
        ),
        "url_observation": (
            {
                "url_observation_id": url_observation_id,
                "job_id": job_id,
                "lease_id": lease_id,
                "owner_key": owner_key,
                "requested_url": feed_url,
                "final_url": str(manifest["final_url"]),
                "observed_at": _iso(observed_at),
                "outcome": "response",
                "http_status": 200,
                "response_headers": response_headers,
                "response_headers_sha256": sha256_bytes(
                    canonical_json_bytes(response_headers)
                ),
                "artifact_sha256": feed_sha,
                "previous_artifact_sha256": None,
                "relation_to_previous": "not_comparable",
                "error_code": None,
            },
        ),
        "feed_observation": (
            {
                "feed_observation_id": feed_observation_id,
                "job_id": job_id,
                "url_observation_id": url_observation_id,
                "response_artifact_sha256": feed_sha,
                "parser_version": str(manifest["parser_version"]),
                "parse_status": "parsed",
                "channel_title_raw": channel_title,
                "item_count": len(items),
                "item_sequence_sha256": str(
                    manifest["item_sequence_sha256"]
                ),
                "parsed_at": _iso(observed_at),
                "parse_error_code": None,
            },
        ),
        "feed_item_observation": tuple(item_rows),
        "rss_work_item": tuple(work_rows),
        "rss_work_observation": tuple(observation_rows),
    }
    return PreparedPollLoad(
        poll_id=poll_id,
        job_fingerprint=job_fingerprint,
        job_id=job_id,
        lease_id=lease_id,
        feed_observation_id=feed_observation_id,
        rows=rows,
        new_likely_guids=new_likely,
    )


def _connect(conninfo: str):
    try:
        import psycopg
    except ImportError as exc:
        raise UpdateQueueError("psycopg is required for PG queue loading") from exc
    return psycopg.connect(conninfo)


def _insert_content_artifact(cursor: Any, row: Mapping[str, Any]) -> None:
    cursor.execute(
        f"""
        INSERT INTO {OPS_SCHEMA}.content_artifact (
          artifact_sha256, byte_size, media_type, bundle_relative_path,
          first_observed_at
        ) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (artifact_sha256) DO NOTHING
        """,
        tuple(row.values()),
    )
    cursor.execute(
        f"""
        SELECT byte_size, media_type
        FROM {OPS_SCHEMA}.content_artifact
        WHERE artifact_sha256 = %s
        """,
        (row["artifact_sha256"],),
    )
    existing = cursor.fetchone()
    if existing is None or (
        int(existing[0]),
        str(existing[1]),
    ) != (int(row["byte_size"]), str(row["media_type"])):
        raise UpdateQueueError("feed artifact metadata collision")


def _transition_evidence(
    material: PreparedPollLoad,
    work_row: Mapping[str, Any],
    event: str,
) -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "event": event,
        "poll_id": material.poll_id,
        "feed_observation_id": material.feed_observation_id,
        "item_identity_kind": work_row["item_identity_kind"],
        "item_identity_value": work_row["item_identity_value"],
        "guid_raw": work_row["guid_raw"],
        "item_fingerprint": work_row["first_item_fingerprint"],
    }


def _insert_transition(
    cursor: Any,
    *,
    work_item_id: str,
    transition_seq: int,
    from_state: str | None,
    to_state: str,
    actor_kind: str,
    evidence: Mapping[str, Any],
    source_job_id: str,
    recorded_at: str,
    bundle_receipt_id: str | None = None,
    candidate_proposal_id: str | None = None,
) -> str:
    evidence_sha = sha256_bytes(canonical_json_bytes(evidence))
    transition_id = _deterministic_uuid(
        "work-transition",
        work_item_id,
        str(transition_seq),
        str(from_state),
        to_state,
        actor_kind,
        evidence_sha,
        source_job_id,
        str(bundle_receipt_id),
        str(candidate_proposal_id),
    )
    cursor.execute(
        f"""
        INSERT INTO {QUEUE_SCHEMA}.work_item_transition (
          work_item_id, transition_seq, transition_id, from_state, to_state,
          actor_kind, evidence_sha256, evidence_json, source_job_id,
          bundle_receipt_id, candidate_proposal_id, recorded_at
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s
        )
        """,
        (
            work_item_id,
            transition_seq,
            transition_id,
            from_state,
            to_state,
            actor_kind,
            evidence_sha,
            json_text(evidence),
            source_job_id,
            bundle_receipt_id,
            candidate_proposal_id,
            recorded_at,
        ),
    )
    return transition_id


def _read_job_projection(cursor: Any, job_id: str) -> dict[str, Any]:
    queries: dict[str, tuple[str, tuple[Any, ...]]] = {
        "update_job": (
            f"""
            SELECT job_id::text, job_fingerprint, contract_version,
                   runner_version, feed_url, request_profile_sha256,
                   notification_window_start, notification_window_end,
                   activation_cut::text, scheduled_at
            FROM {OPS_SCHEMA}.update_job WHERE job_id = %s
            """,
            (job_id,),
        ),
        "job_lease": (
            f"""
            SELECT lease_id::text, job_id::text, owner_key, acquired_at,
                   expires_at, max_runtime_seconds
            FROM {OPS_SCHEMA}.job_lease WHERE job_id = %s
            """,
            (job_id,),
        ),
        "content_artifact": (
            f"""
            SELECT artifact.artifact_sha256, artifact.byte_size,
                   artifact.media_type
            FROM {OPS_SCHEMA}.content_artifact artifact
            JOIN {OPS_SCHEMA}.url_observation observed
              ON observed.artifact_sha256 = artifact.artifact_sha256
            WHERE observed.job_id = %s
            """,
            (job_id,),
        ),
        "url_observation": (
            f"""
            SELECT url_observation_id::text, requested_url, final_url,
                   observed_at, outcome, http_status,
                   response_headers_sha256, artifact_sha256,
                   previous_artifact_sha256, relation_to_previous, error_code
            FROM {OPS_SCHEMA}.url_observation WHERE job_id = %s
            """,
            (job_id,),
        ),
        "feed_observation": (
            f"""
            SELECT feed_observation_id::text, response_artifact_sha256,
                   parser_version, parse_status, channel_title_raw,
                   item_count, item_sequence_sha256, parsed_at,
                   parse_error_code
            FROM {OPS_SCHEMA}.feed_observation WHERE job_id = %s
            """,
            (job_id,),
        ),
        "feed_item_observation": (
            f"""
            SELECT item.item_index, item.item_fingerprint, item.guid_raw,
                   item.title_raw, item.link_raw, item.published_raw,
                   item.description_raw, item.raw_item_sha256
            FROM {OPS_SCHEMA}.feed_item_observation item
            JOIN {OPS_SCHEMA}.feed_observation feed
              ON feed.feed_observation_id = item.feed_observation_id
            WHERE feed.job_id = %s
            ORDER BY item.item_index
            """,
            (job_id,),
        ),
        "rss_work_observation": (
            f"""
            SELECT observation.work_item_id::text,
                   item.rss_identity_fingerprint,
                   item.item_identity_kind, item.item_identity_value,
                   item.source_feed_url, item.guid_raw,
                   item.first_feed_observation_id::text,
                   item.first_item_index, item.first_item_fingerprint,
                   item.first_title_raw, item.first_link_raw,
                   item.first_observed_at,
                   observation.feed_observation_id::text,
                   observation.item_index, observation.observed_at,
                   observation.item_fingerprint
            FROM {QUEUE_SCHEMA}.rss_work_observation observation
            JOIN {QUEUE_SCHEMA}.rss_work_item item
              ON item.work_item_id = observation.work_item_id
            JOIN {OPS_SCHEMA}.feed_observation feed
              ON feed.feed_observation_id =
                 observation.feed_observation_id
            WHERE feed.job_id = %s
            ORDER BY observation.item_index
            """,
            (job_id,),
        ),
        "work_item_transition": (
            f"""
            SELECT work_item_id::text, transition_seq, transition_id::text,
                   from_state, to_state, actor_kind, evidence_sha256,
                   evidence_json, source_job_id::text,
                   bundle_receipt_id::text,
                   candidate_proposal_id::text, recorded_at
            FROM {QUEUE_SCHEMA}.work_item_transition
            WHERE source_job_id = %s
            ORDER BY work_item_id, transition_seq
            """,
            (job_id,),
        ),
    }
    projection: dict[str, Any] = {}
    for name, (query, parameters) in queries.items():
        cursor.execute(query, parameters)
        projection[name] = [
            _normalize(list(row)) for row in cursor.fetchall()
        ]
    return projection


def _apply_poll(material: PreparedPollLoad, conninfo: str) -> AppliedPollLoad:
    replayed = False
    created_count = 0
    selected_count = 0
    ignored_count = 0
    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"nhi-rule-history-poll-load:{material.job_fingerprint}",),
            )
            cursor.execute(
                f"""
                SELECT job_id::text
                FROM {OPS_SCHEMA}.update_job
                WHERE job_fingerprint = %s
                """,
                (material.job_fingerprint,),
            )
            existing_job = cursor.fetchone()
            if existing_job is not None:
                if str(existing_job[0]) != material.job_id:
                    raise UpdateQueueError("poll job fingerprint UUID collision")
                replayed = True
            else:
                job = material.rows["update_job"][0]
                cursor.execute(
                    f"""
                    INSERT INTO {OPS_SCHEMA}.update_job (
                      job_id, job_fingerprint, contract_version,
                      runner_version, feed_url, request_profile_sha256,
                      notification_window_start, notification_window_end,
                      activation_cut, scheduled_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    tuple(job.values()),
                )
                lease = material.rows["job_lease"][0]
                cursor.execute(
                    f"""
                    INSERT INTO {OPS_SCHEMA}.job_lease (
                      lease_id, job_id, owner_key, acquired_at, expires_at,
                      max_runtime_seconds
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    tuple(lease.values()),
                )
                _insert_content_artifact(
                    cursor, material.rows["content_artifact"][0]
                )
                url_row = dict(material.rows["url_observation"][0])
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (
                        "nhi-rule-history-url:"
                        f"{url_row['requested_url']}",
                    ),
                )
                url_row["relation_to_previous"] = "not_comparable"
                url_row["previous_artifact_sha256"] = None
                cursor.execute(
                    f"""
                    INSERT INTO {OPS_SCHEMA}.url_observation (
                      url_observation_id, job_id, lease_id, owner_key,
                      requested_url, final_url, observed_at, outcome,
                      http_status, response_headers, response_headers_sha256,
                      artifact_sha256, previous_artifact_sha256,
                      relation_to_previous, error_code
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s
                    )
                    """,
                    (
                        url_row["url_observation_id"],
                        url_row["job_id"],
                        url_row["lease_id"],
                        url_row["owner_key"],
                        url_row["requested_url"],
                        url_row["final_url"],
                        url_row["observed_at"],
                        url_row["outcome"],
                        url_row["http_status"],
                        json_text(url_row["response_headers"]),
                        url_row["response_headers_sha256"],
                        url_row["artifact_sha256"],
                        url_row["previous_artifact_sha256"],
                        url_row["relation_to_previous"],
                        url_row["error_code"],
                    ),
                )
                feed = material.rows["feed_observation"][0]
                cursor.execute(
                    f"""
                    INSERT INTO {OPS_SCHEMA}.feed_observation (
                      feed_observation_id, job_id, url_observation_id,
                      response_artifact_sha256, parser_version, parse_status,
                      channel_title_raw, item_count, item_sequence_sha256,
                      parsed_at, parse_error_code
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    tuple(feed.values()),
                )
                for row in material.rows["feed_item_observation"]:
                    cursor.execute(
                        f"""
                        INSERT INTO {OPS_SCHEMA}.feed_item_observation (
                          feed_observation_id, item_index, item_fingerprint,
                          guid_raw, title_raw, link_raw, published_raw,
                          description_raw, raw_item_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        tuple(row.values()),
                    )

                work_by_id = {
                    str(row["work_item_id"]): row
                    for row in material.rows["rss_work_item"]
                }
                selected_seen: set[str] = set()
                for observation in material.rows["rss_work_observation"]:
                    work = work_by_id[str(observation["work_item_id"])]
                    insert_values = {
                        key: value
                        for key, value in work.items()
                        if not key.startswith("_")
                    }
                    cursor.execute(
                        f"""
                        INSERT INTO {QUEUE_SCHEMA}.rss_work_item (
                          work_item_id, rss_identity_fingerprint,
                          item_identity_kind, item_identity_value,
                          source_feed_url, guid_raw,
                          first_feed_observation_id, first_item_index,
                          first_item_fingerprint, first_title_raw,
                          first_link_raw, first_observed_at
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        ON CONFLICT (
                          source_feed_url,
                          item_identity_kind,
                          item_identity_value
                        ) DO NOTHING
                        RETURNING work_item_id::text
                        """,
                        tuple(insert_values.values()),
                    )
                    inserted = cursor.fetchone()
                    if inserted is None:
                        cursor.execute(
                            f"""
                            SELECT work_item_id::text,
                                   rss_identity_fingerprint
                            FROM {QUEUE_SCHEMA}.rss_work_item
                            WHERE source_feed_url = %s
                              AND item_identity_kind = %s
                              AND item_identity_value = %s
                            """,
                            (
                                work["source_feed_url"],
                                work["item_identity_kind"],
                                work["item_identity_value"],
                            ),
                        )
                        existing = cursor.fetchone()
                        if existing is None or (
                            str(existing[0]),
                            str(existing[1]),
                        ) != (
                            str(work["work_item_id"]),
                            str(work["rss_identity_fingerprint"]),
                        ):
                            raise UpdateQueueError(
                                "RSS work identity collision"
                            )
                        if work["_selected_by_poll"]:
                            raise UpdateQueueError(
                                "poll calls an already durable item identity new"
                            )
                    else:
                        created_count += 1

                    cursor.execute(
                        f"""
                        INSERT INTO {QUEUE_SCHEMA}.rss_work_observation (
                          work_item_id, feed_observation_id, item_index,
                          observed_at, item_fingerprint
                        ) VALUES (%s,%s,%s,%s,%s)
                        """,
                        tuple(observation.values()),
                    )
                    if inserted is None:
                        continue

                    observed_evidence = _transition_evidence(
                        material, work, "observed"
                    )
                    _insert_transition(
                        cursor,
                        work_item_id=str(work["work_item_id"]),
                        transition_seq=1,
                        from_state=None,
                        to_state="observed",
                        actor_kind="deterministic_poll_loader",
                        evidence=observed_evidence,
                        source_job_id=material.job_id,
                        recorded_at=str(observation["observed_at"]),
                    )
                    if work["_selected_by_poll"]:
                        selected_seen.add(
                            str(work["item_identity_value"])
                        )
                        selected_count += 1
                        final_state = "selected"
                    elif not work["_is_likely_drug_rule"]:
                        ignored_count += 1
                        final_state = "ignored_non_rule"
                    else:
                        raise UpdateQueueError(
                            "new likely-rule GUID is absent from poll selection"
                        )
                    disposition_evidence = _transition_evidence(
                        material, work, final_state
                    )
                    disposition_evidence["classifier"] = (
                        "rss-item-keywords/v1"
                    )
                    _insert_transition(
                        cursor,
                        work_item_id=str(work["work_item_id"]),
                        transition_seq=2,
                        from_state="observed",
                        to_state=final_state,
                        actor_kind="deterministic_poll_classifier",
                        evidence=disposition_evidence,
                        source_job_id=material.job_id,
                        recorded_at=str(observation["observed_at"]),
                    )
                if selected_seen != set(material.new_likely_guids):
                    raise UpdateQueueError(
                        "poll selection and newly created queue identities differ"
                    )

            expected_projection = _read_job_projection(
                cursor, material.job_id
            )
            expected_fingerprint = sha256_bytes(
                canonical_json_bytes(expected_projection)
            )
    return AppliedPollLoad(
        replayed=replayed,
        created_work_item_count=created_count,
        selected_work_item_count=selected_count,
        ignored_work_item_count=ignored_count,
        expected_projection=expected_projection,
        expected_fingerprint=expected_fingerprint,
    )


def _verify_poll_load(
    material: PreparedPollLoad,
    applied: AppliedPollLoad,
    conninfo: str,
) -> dict[str, Any]:
    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            actual_projection = _read_job_projection(
                cursor, material.job_id
            )
    actual_fingerprint = sha256_bytes(
        canonical_json_bytes(actual_projection)
    )
    if (
        actual_projection != dict(applied.expected_projection)
        or actual_fingerprint != applied.expected_fingerprint
    ):
        raise UpdateQueueError(
            "fresh-connection poll queue fingerprint verification failed"
        )
    counts = {
        name: len(rows)
        for name, rows in actual_projection.items()
    }
    if counts["update_job"] != 1 or counts["feed_observation"] != 1:
        raise UpdateQueueError("poll queue committed cardinality is invalid")
    if counts["feed_item_observation"] != counts["rss_work_observation"]:
        raise UpdateQueueError(
            "not every feed item has an independent work observation"
        )
    return {
        "counts": counts,
        "fingerprint": actual_fingerprint,
    }


def load_poll_package(
    conninfo: str,
    poll_path: Path,
    *,
    owner_key: str,
    poll_relative_root: str | None = None,
) -> dict[str, Any]:
    """Load or replay one verified immutable ``update.poll`` package."""

    material = _prepare_poll_load(
        Path(poll_path),
        owner_key=owner_key,
        poll_relative_root=poll_relative_root,
    )
    applied = _apply_poll(material, conninfo)
    verification = _verify_poll_load(material, applied, conninfo)
    return {
        "schema": RECEIPT_SCHEMA,
        "poll_id": material.poll_id,
        "job_id": material.job_id,
        "job_fingerprint": material.job_fingerprint,
        "feed_observation_id": material.feed_observation_id,
        "replayed": applied.replayed,
        "created_work_item_count": applied.created_work_item_count,
        "selected_work_item_count": applied.selected_work_item_count,
        "ignored_work_item_count": applied.ignored_work_item_count,
        "verification": verification,
    }


def append_work_transition(
    conninfo: str,
    *,
    work_item_id: str,
    to_state: str,
    actor_kind: str,
    evidence: Mapping[str, Any],
    source_job_id: str,
    bundle_receipt_id: str | None = None,
    candidate_proposal_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Append one allowed transition; exact replay returns the prior receipt.

    ``bundle_receipt_id`` is the update-stage bundle receipt created together
    with a validated candidate.  Earlier corpus-registration evidence belongs
    in ``evidence`` and must not claim that later identifier.
    """

    if to_state not in _STATES or to_state == "observed":
        raise UpdateQueueError("requested transition target is invalid")
    if not actor_kind.strip():
        raise UpdateQueueError("actor_kind must be non-empty")
    if not isinstance(evidence, Mapping) or not evidence:
        raise UpdateQueueError("transition evidence must be a non-empty object")
    try:
        work_item_id = str(uuid.UUID(work_item_id))
        source_job_id = str(uuid.UUID(source_job_id))
        if bundle_receipt_id is not None:
            bundle_receipt_id = str(uuid.UUID(bundle_receipt_id))
        if candidate_proposal_id is not None:
            candidate_proposal_id = str(uuid.UUID(candidate_proposal_id))
    except ValueError as exc:
        raise UpdateQueueError("transition identifiers must be UUIDs") from exc
    transition_time = _timestamp(
        recorded_at or utc_now(), "recorded_at"
    )
    evidence_object = dict(evidence)
    evidence_sha = sha256_bytes(canonical_json_bytes(evidence_object))
    replayed = False

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"nhi-rule-history-work-item:{work_item_id}",),
            )
            cursor.execute(
                f"""
                SELECT transition_seq, transition_id::text, to_state,
                       actor_kind, evidence_sha256, source_job_id::text,
                       bundle_receipt_id::text,
                       candidate_proposal_id::text, recorded_at
                FROM {QUEUE_SCHEMA}.work_item_transition
                WHERE work_item_id = %s
                ORDER BY transition_seq DESC
                LIMIT 1
                """,
                (work_item_id,),
            )
            prior = cursor.fetchone()
            if prior is None:
                raise UpdateQueueError(
                    "work item lacks its required observed transition"
                )
            prior_seq = int(prior[0])
            prior_state = str(prior[2])
            exact_replay = (
                prior_state == to_state
                and str(prior[3]) == actor_kind
                and str(prior[4]) == evidence_sha
                and str(prior[5]) == source_job_id
                and (str(prior[6]) if prior[6] is not None else None)
                == bundle_receipt_id
                and (str(prior[7]) if prior[7] is not None else None)
                == candidate_proposal_id
            )
            if exact_replay:
                transition_id = str(prior[1])
                replayed = True
            elif prior_state in _TERMINAL_STATES:
                raise UpdateQueueError(
                    "terminal work-item state prevents silent retry"
                )
            else:
                if to_state not in _ALLOWED_EDGES.get(prior_state, set()):
                    raise UpdateQueueError(
                        "requested work-item transition edge is not allowed"
                    )
                prior_recorded_at = (
                    _iso(prior[8])
                    if isinstance(prior[8], datetime)
                    else str(prior[8])
                )
                if transition_time < _timestamp(
                    prior_recorded_at,
                    "prior.recorded_at",
                ):
                    raise UpdateQueueError(
                        "transition time precedes current work state"
                    )
                transition_id = _insert_transition(
                    cursor,
                    work_item_id=work_item_id,
                    transition_seq=prior_seq + 1,
                    from_state=prior_state,
                    to_state=to_state,
                    actor_kind=actor_kind,
                    evidence=evidence_object,
                    source_job_id=source_job_id,
                    bundle_receipt_id=bundle_receipt_id,
                    candidate_proposal_id=candidate_proposal_id,
                    recorded_at=_iso(transition_time),
                )

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT transition_id::text, work_item_id::text,
                       transition_seq, from_state, to_state, actor_kind,
                       evidence_sha256, evidence_json, source_job_id::text,
                       bundle_receipt_id::text,
                       candidate_proposal_id::text, recorded_at
                FROM {QUEUE_SCHEMA}.work_item_transition
                WHERE transition_id = %s
                """,
                (transition_id,),
            )
            row = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT current_state, transition_seq, is_terminal
                FROM {QUEUE_SCHEMA}.v_work_item_current
                WHERE work_item_id = %s
                """,
                (work_item_id,),
            )
            current = cursor.fetchone()
    if row is None or current is None or str(current[0]) != to_state:
        raise UpdateQueueError(
            "fresh-connection transition verification failed"
        )
    normalized_row = _normalize(list(row))
    return {
        "schema": TRANSITION_RECEIPT_SCHEMA,
        "transition_id": transition_id,
        "work_item_id": work_item_id,
        "to_state": to_state,
        "transition_seq": int(current[1]),
        "is_terminal": bool(current[2]),
        "replayed": replayed,
        "fingerprint": sha256_bytes(
            canonical_json_bytes(normalized_row)
        ),
    }


def append_work_attempt(
    conninfo: str,
    *,
    work_item_id: str,
    attempt_kind: str,
    idempotency_key: str,
    outcome: str,
    actor_kind: str,
    evidence: Mapping[str, Any],
    source_job_id: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Append a non-state-changing operational attempt.

    The caller's idempotency key is hashed and never stored. Exact replay
    returns the original receipt; reuse with different material fails closed.
    Evidence is sanitized before both persistence and hashing.
    """

    if attempt_kind not in _ATTEMPT_STATES:
        raise UpdateQueueError("attempt_kind is invalid")
    if outcome not in {"success", "transient_failure"}:
        raise UpdateQueueError("attempt outcome is invalid")
    if not idempotency_key.strip():
        raise UpdateQueueError("idempotency_key must be non-empty")
    if not actor_kind.strip():
        raise UpdateQueueError("actor_kind must be non-empty")
    if not isinstance(evidence, Mapping) or not evidence:
        raise UpdateQueueError("attempt evidence must be a non-empty object")
    try:
        work_item_id = str(uuid.UUID(work_item_id))
        source_job_id = str(uuid.UUID(source_job_id))
    except ValueError as exc:
        raise UpdateQueueError("attempt identifiers must be UUIDs") from exc

    sanitized_evidence = _sanitize_attempt_evidence(dict(evidence))
    if not isinstance(sanitized_evidence, dict) or not sanitized_evidence:
        raise UpdateQueueError(
            "sanitized attempt evidence must be a non-empty object"
        )
    evidence_sha = sha256_bytes(canonical_json_bytes(sanitized_evidence))
    attempt_fingerprint = stable_id(
        "nhi-work-item-attempt",
        work_item_id,
        idempotency_key,
    )
    attempt_id = _deterministic_uuid(
        "work-item-attempt", attempt_fingerprint
    )
    attempt_time = (
        _timestamp(recorded_at, "recorded_at")
        if recorded_at is not None
        else None
    )
    replayed = False
    state_at_attempt: str | None = None
    state_before_call: str | None = None

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"nhi-rule-history-work-attempt:{attempt_fingerprint}",),
            )
            cursor.execute(
                f"""
                SELECT attempt_id::text, attempt_kind, outcome, actor_kind,
                       evidence_sha256, source_job_id::text,
                       work_state_at_attempt, recorded_at
                FROM {QUEUE_SCHEMA}.work_item_attempt
                WHERE attempt_fingerprint = %s
                """,
                (attempt_fingerprint,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != attempt_id
                    or str(existing[1]) != attempt_kind
                    or str(existing[2]) != outcome
                    or str(existing[3]) != actor_kind
                    or str(existing[4]) != evidence_sha
                    or str(existing[5]) != source_job_id
                ):
                    raise UpdateQueueError(
                        "attempt idempotency key was reused with different material"
                    )
                state_at_attempt = str(existing[6])
                cursor.execute(
                    f"""
                    SELECT current_state
                    FROM {QUEUE_SCHEMA}.v_work_item_current
                    WHERE work_item_id = %s
                    """,
                    (work_item_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise UpdateQueueError("work item does not exist")
                state_before_call = str(current[0])
                replayed = True
            else:
                cursor.execute(
                    f"""
                    SELECT current_state, state_recorded_at, is_terminal
                    FROM {QUEUE_SCHEMA}.v_work_item_current
                    WHERE work_item_id = %s
                    """,
                    (work_item_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise UpdateQueueError("work item does not exist")
                state_at_attempt = str(current[0])
                state_before_call = state_at_attempt
                if bool(current[2]):
                    raise UpdateQueueError(
                        "terminal work-item state prevents new attempts"
                    )
                if state_at_attempt not in _ATTEMPT_STATES[attempt_kind]:
                    raise UpdateQueueError(
                        "attempt kind is incompatible with current work state"
                    )
                current_recorded_at = (
                    _iso(current[1])
                    if isinstance(current[1], datetime)
                    else str(current[1])
                )
                effective_time = attempt_time or datetime.now(timezone.utc)
                if effective_time < _timestamp(
                    current_recorded_at,
                    "current.state_recorded_at",
                ):
                    raise UpdateQueueError(
                        "attempt time precedes current work state"
                    )
                cursor.execute(
                    f"""
                    INSERT INTO {QUEUE_SCHEMA}.work_item_attempt (
                      attempt_id, work_item_id, attempt_fingerprint,
                      attempt_kind, outcome, work_state_at_attempt,
                      actor_kind, sanitization_profile, evidence_sha256,
                      evidence_json, source_job_id, recorded_at
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s
                    )
                    """,
                    (
                        attempt_id,
                        work_item_id,
                        attempt_fingerprint,
                        attempt_kind,
                        outcome,
                        state_at_attempt,
                        actor_kind,
                        ATTEMPT_SANITIZATION_PROFILE,
                        evidence_sha,
                        json_text(sanitized_evidence),
                        source_job_id,
                        _iso(effective_time),
                    ),
                )

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT attempt_id::text, work_item_id::text,
                       attempt_fingerprint, attempt_kind, outcome,
                       work_state_at_attempt, actor_kind,
                       sanitization_profile, evidence_sha256, evidence_json,
                       source_job_id::text, recorded_at
                FROM {QUEUE_SCHEMA}.work_item_attempt
                WHERE attempt_id = %s
                """,
                (attempt_id,),
            )
            row = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT current_state
                FROM {QUEUE_SCHEMA}.v_work_item_current
                WHERE work_item_id = %s
                """,
                (work_item_id,),
            )
            current = cursor.fetchone()
    if (
        row is None
        or current is None
        or state_at_attempt is None
        or state_before_call is None
        or str(current[0]) != state_before_call
    ):
        raise UpdateQueueError(
            "fresh-connection attempt verification failed or changed work state"
        )
    normalized_row = _normalize(list(row))
    return {
        "schema": ATTEMPT_RECEIPT_SCHEMA,
        "attempt_id": attempt_id,
        "work_item_id": work_item_id,
        "attempt_kind": attempt_kind,
        "outcome": outcome,
        "work_state_at_attempt": state_at_attempt,
        "current_state": state_before_call,
        "replayed": replayed,
        "evidence_sha256": evidence_sha,
        "sanitization_profile": ATTEMPT_SANITIZATION_PROFILE,
        "fingerprint": sha256_bytes(canonical_json_bytes(normalized_row)),
    }


def _recovery_uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise UpdateQueueError(f"{label} must be a UUID") from exc


def _recovery_sha256(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise UpdateQueueError(f"{label} must be a lowercase SHA-256")
    return value


def _recovery_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpdateQueueError(f"{label} must be non-empty")
    return value.strip()


def _recovery_relative_path(value: str, label: str) -> str:
    normalized = _recovery_text(value, label)
    path = Path(normalized)
    if (
        path.is_absolute()
        or "\\" in normalized
        or normalized != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise UpdateQueueError(
            f"{label} must be a normalized repository-relative POSIX path"
        )
    return normalized


def _load_legacy_failure_files(
    *,
    failure_receipt_path: Path,
    attempts_path: Path,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], bytes, bytes]:
    try:
        receipt_bytes = failure_receipt_path.read_bytes()
        attempts_bytes = attempts_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        raw_lines = attempts_bytes.splitlines(keepends=True)
        attempts = tuple(json.loads(line) for line in raw_lines)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateQueueError(
            "legacy failure receipt or attempts ledger is unreadable"
        ) from exc
    if (
        not isinstance(receipt, dict)
        or receipt_bytes != canonical_json_bytes(receipt)
        or len(raw_lines) != 2
        or any(
            not isinstance(attempt, dict)
            or line != canonical_json_bytes(attempt)
            for line, attempt in zip(raw_lines, attempts, strict=True)
        )
    ):
        raise UpdateQueueError(
            "legacy failure evidence must be canonical immutable JSON"
        )
    try:
        for attempt in attempts:
            validate_jsonl_row(attempt)
    except ContractError as exc:
        raise UpdateQueueError(
            "legacy attempt row violates worker-attempt/v1"
        ) from exc
    return receipt, attempts, receipt_bytes, attempts_bytes


def admit_legacy_failure_evidence(
    conninfo: str,
    *,
    work_item_id: str,
    terminal_transition_id: str,
    failure_receipt_path: str | Path,
    attempts_path: str | Path,
    failure_receipt_relative_path: str,
    attempts_relative_path: str,
    verifier_code_identity: str,
    actor_kind: str,
    admitted_at: str | None = None,
) -> dict[str, Any]:
    """Admit an immutable pre-PG two-route worker failure for recovery.

    The file attempt identifiers remain their original 64-hex values.  This
    bridge does not fabricate UUID rows in ``worker_attempt`` and does not
    change the legacy terminal transition.
    """

    work_item_id = _recovery_uuid(work_item_id, "work_item_id")
    terminal_transition_id = _recovery_uuid(
        terminal_transition_id, "terminal_transition_id"
    )
    failure_receipt_relative_path = _recovery_relative_path(
        failure_receipt_relative_path, "failure_receipt_relative_path"
    )
    attempts_relative_path = _recovery_relative_path(
        attempts_relative_path, "attempts_relative_path"
    )
    relative_receipt = Path(failure_receipt_relative_path)
    relative_attempts = Path(attempts_relative_path)
    if (
        relative_receipt.name != "failure-receipt.json"
        or relative_attempts.name != "attempts.jsonl"
        or relative_receipt.parent != relative_attempts.parent
    ):
        raise UpdateQueueError(
            "legacy receipt and attempts paths must share one run directory"
        )
    actor_kind = _recovery_text(actor_kind, "actor_kind")
    verifier_code_identity = _recovery_sha256(
        verifier_code_identity, "verifier_code_identity"
    )
    admission_time = _timestamp(admitted_at or utc_now(), "admitted_at")
    receipt, attempts, receipt_bytes, attempts_bytes = (
        _load_legacy_failure_files(
            failure_receipt_path=Path(failure_receipt_path),
            attempts_path=Path(attempts_path),
        )
    )
    if (
        receipt.get("schema") != LEGACY_WORKER_RUN_SCHEMA
        or receipt.get("status") != "failed"
        or receipt.get("attempt_count") != 2
        or receipt.get("selected_attempt_id") is not None
    ):
        raise UpdateQueueError(
            "legacy failure receipt is not a terminal two-attempt v2 receipt"
        )
    source_bundle_uid = _recovery_text(
        receipt.get("bundle_id"), "receipt.bundle_id"
    )
    source_bundle_fingerprint = _recovery_sha256(
        receipt.get("bundle_fingerprint"),
        "receipt.bundle_fingerprint",
    )
    source_manifest_sha256 = _recovery_sha256(
        receipt.get("manifest_sha256"), "receipt.manifest_sha256"
    )
    worker_job_fingerprint = _recovery_sha256(
        receipt.get("job_fingerprint"), "receipt.job_fingerprint"
    )
    if relative_receipt.parent.name != worker_job_fingerprint:
        raise UpdateQueueError(
            "legacy receipt path does not bind its worker job fingerprint"
        )
    prompt_sha256 = _recovery_sha256(
        receipt.get("prompt_sha256"), "receipt.prompt_sha256"
    )
    attempts_sha256 = sha256_bytes(attempts_bytes)
    if receipt.get("attempts_sha256") != attempts_sha256:
        raise UpdateQueueError(
            "legacy failure receipt does not bind the exact attempts stream"
        )

    primary, fallback = attempts
    for expected_role, attempt in zip(
        ("primary", "fallback"), attempts, strict=True
    ):
        if (
            attempt.get("schema") != LEGACY_WORKER_ATTEMPT_SCHEMA
            or attempt.get("role") != expected_role
            or attempt.get("status") not in _LEGACY_FAILURE_STATUSES
            or attempt.get("prompt_sha256") != prompt_sha256
            or not isinstance(attempt.get("worker_id"), str)
            or not attempt["worker_id"].strip()
        ):
            raise UpdateQueueError(
                "legacy attempts must be failed primary/fallback records "
                "for one semantic prompt"
            )
        _recovery_sha256(
            attempt.get("attempt_id"), f"{expected_role}.attempt_id"
        )
    if (
        primary.get("primary_attempt_id") is not None
        or fallback.get("primary_attempt_id") != primary.get("attempt_id")
        or primary.get("attempt_id") == fallback.get("attempt_id")
        or not isinstance(fallback.get("fallback_reason"), str)
        or not fallback["fallback_reason"].strip()
    ):
        raise UpdateQueueError(
            "legacy fallback must point to one distinct failed primary"
        )
    method_versions = {
        attempt.get("prompt_version") for attempt in attempts
    }
    if (
        len(method_versions) != 1
        or not isinstance(next(iter(method_versions)), str)
        or not next(iter(method_versions)).strip()
    ):
        raise UpdateQueueError(
            "legacy attempts must share one non-empty prompt version"
        )
    prior_method_version = next(iter(method_versions)).strip()
    expected_attempt_ids = [
        stable_id(
            "nhi-worker-attempt",
            source_bundle_uid,
            prompt_sha256,
            str(attempt["role"]),
            str(attempt["worker_id"]),
        )
        for attempt in attempts
    ]
    if expected_attempt_ids != [
        str(attempt["attempt_id"]) for attempt in attempts
    ]:
        raise UpdateQueueError(
            "legacy attempt IDs do not match their immutable lineage"
        )

    failure_receipt_sha256 = sha256_bytes(receipt_bytes)
    attempt_record_sha256s = [
        sha256_bytes(canonical_json_bytes(attempt)) for attempt in attempts
    ]
    material = {
        "work_item_id": work_item_id,
        "terminal_transition_id": terminal_transition_id,
        "failure_receipt_relative_path": failure_receipt_relative_path,
        "failure_receipt_sha256": failure_receipt_sha256,
        "attempts_relative_path": attempts_relative_path,
        "attempts_sha256": attempts_sha256,
        "source_bundle_uid": source_bundle_uid,
        "source_manifest_sha256": source_manifest_sha256,
        "worker_job_fingerprint": worker_job_fingerprint,
        "attempt_ids": [str(attempt["attempt_id"]) for attempt in attempts],
        "attempt_record_sha256s": attempt_record_sha256s,
        "verifier_contract_version": (
            LEGACY_FAILURE_BYTE_VERIFIER_CONTRACT
        ),
        "verifier_code_identity": verifier_code_identity,
        "verifier_output_schema_version": (
            LEGACY_FAILURE_ADMISSION_PAYLOAD_SCHEMA
        ),
    }
    admission_payload_sha256 = sha256_bytes(canonical_json_bytes(material))
    admission_id = _deterministic_uuid(
        "legacy-worker-failure-admission",
        admission_payload_sha256,
    )

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT admission_id::text, replayed
                FROM {QUEUE_SCHEMA}.admit_legacy_failure_evidence(
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                  %s,%s,%s::jsonb,%s::text[],%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    admission_id,
                    work_item_id,
                    terminal_transition_id,
                    source_bundle_uid,
                    source_bundle_fingerprint,
                    source_manifest_sha256,
                    worker_job_fingerprint,
                    prior_method_version,
                    prompt_sha256,
                    failure_receipt_relative_path,
                    failure_receipt_sha256,
                    json_text(receipt),
                    attempts_relative_path,
                    attempts_sha256,
                    json_text(list(attempts)),
                    attempt_record_sha256s,
                    LEGACY_FAILURE_BYTE_VERIFIER_CONTRACT,
                    verifier_code_identity,
                    LEGACY_FAILURE_ADMISSION_PAYLOAD_SCHEMA,
                    admission_payload_sha256,
                    actor_kind,
                    _iso(admission_time),
                ),
            )
            result = cursor.fetchone()
    if result is None:
        raise UpdateQueueError("legacy failure admission returned no receipt")

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT evidence.admission_id::text,
                       evidence.terminal_transition_id::text,
                       evidence.source_bundle_uid,
                       evidence.source_manifest_sha256::text,
                       evidence.worker_job_fingerprint::text,
                       evidence.failure_receipt_sha256::text,
                       evidence.attempts_sha256::text,
                       evidence.verifier_contract_version,
                       evidence.verifier_code_identity::text,
                       evidence.verifier_output_schema_version,
                       evidence.admission_payload_sha256::text,
                       array_agg(attempt.attempt_id::text ORDER BY attempt.route),
                       array_agg(attempt.attempt_id_scheme ORDER BY attempt.route),
                       array_agg(attempt.attempt_id_origin ORDER BY attempt.route)
                FROM {QUEUE_SCHEMA}.legacy_failure_evidence evidence
                JOIN {QUEUE_SCHEMA}.legacy_failure_attempt_evidence attempt
                  ON attempt.admission_id = evidence.admission_id
                WHERE evidence.admission_id = %s
                GROUP BY evidence.admission_id
                """,
                (admission_id,),
            )
            current = cursor.fetchone()
    expected_ids = sorted(str(attempt["attempt_id"]) for attempt in attempts)
    if (
        current is None
        or str(current[0]) != admission_id
        or str(current[1]) != terminal_transition_id
        or str(current[2]) != source_bundle_uid
        or str(current[3]) != source_manifest_sha256
        or str(current[4]) != worker_job_fingerprint
        or str(current[5]) != failure_receipt_sha256
        or str(current[6]) != attempts_sha256
        or str(current[7]) != LEGACY_FAILURE_BYTE_VERIFIER_CONTRACT
        or str(current[8]) != verifier_code_identity
        or str(current[9]) != LEGACY_FAILURE_ADMISSION_PAYLOAD_SCHEMA
        or str(current[10]) != admission_payload_sha256
        or sorted(str(value) for value in current[11]) != expected_ids
        or set(str(value) for value in current[12])
        != {LEGACY_ATTEMPT_ID_SCHEME}
        or set(str(value) for value in current[13])
        != {LEGACY_ATTEMPT_ID_ORIGIN}
    ):
        raise UpdateQueueError(
            "fresh-connection legacy failure admission verification failed"
        )
    return {
        "schema": LEGACY_FAILURE_ADMISSION_RECEIPT_SCHEMA,
        "admission_id": admission_id,
        "work_item_id": work_item_id,
        "terminal_transition_id": terminal_transition_id,
        "source_bundle_uid": source_bundle_uid,
        "source_bundle_fingerprint": source_bundle_fingerprint,
        "source_manifest_sha256": source_manifest_sha256,
        "worker_job_fingerprint": worker_job_fingerprint,
        "prior_method_version": prior_method_version,
        "prior_semantic_prompt_fingerprint": prompt_sha256,
        "failure_receipt_relative_path": failure_receipt_relative_path,
        "failure_receipt_sha256": failure_receipt_sha256,
        "attempts_relative_path": attempts_relative_path,
        "attempts_sha256": attempts_sha256,
        "attempt_ids": expected_ids,
        "attempt_id_scheme": LEGACY_ATTEMPT_ID_SCHEME,
        "attempt_id_origin": LEGACY_ATTEMPT_ID_ORIGIN,
        "verifier_contract_version": (
            LEGACY_FAILURE_BYTE_VERIFIER_CONTRACT
        ),
        "verifier_code_identity": verifier_code_identity,
        "verifier_output_schema_version": (
            LEGACY_FAILURE_ADMISSION_PAYLOAD_SCHEMA
        ),
        "admission_payload_sha256": admission_payload_sha256,
        "replayed": bool(result[1]),
        "fingerprint": sha256_bytes(canonical_json_bytes(_normalize(current))),
    }


def authorize_work_recovery(
    conninfo: str,
    *,
    work_item_id: str,
    prior_generation: int,
    source_bundle_uid: str,
    source_manifest_sha256: str,
    prior_method_version: str,
    new_method_version: str,
    prior_semantic_prompt_fingerprint: str,
    new_semantic_prompt_fingerprint: str,
    decision_basis_id: str,
    reason: str,
    actor_kind: str,
    superseded_attempt_ids: list[str] | tuple[str, ...] = (),
    legacy_failure_admission_id: str | None = None,
    route: str = _RECOVERY_ROUTE,
    authorized_at: str | None = None,
) -> dict[str, Any]:
    """Authorize immutable generation ``G+1`` for failed stage work.

    The legacy ``failed_terminal`` transition and its worker attempts are
    never changed.  The deterministic authorization creates only a generation
    in ``retry_pending``.  Starting model work remains a separate call.
    """

    work_item_id = _recovery_uuid(work_item_id, "work_item_id")
    if not isinstance(prior_generation, int) or prior_generation < 1:
        raise UpdateQueueError("prior_generation must be a positive integer")
    new_generation = prior_generation + 1
    source_bundle_uid = _recovery_text(
        source_bundle_uid, "source_bundle_uid"
    )
    source_manifest_sha256 = _recovery_sha256(
        source_manifest_sha256, "source_manifest_sha256"
    )
    prior_method_version = _recovery_text(
        prior_method_version, "prior_method_version"
    )
    new_method_version = _recovery_text(
        new_method_version, "new_method_version"
    )
    prior_semantic_prompt_fingerprint = _recovery_sha256(
        prior_semantic_prompt_fingerprint,
        "prior_semantic_prompt_fingerprint",
    )
    new_semantic_prompt_fingerprint = _recovery_sha256(
        new_semantic_prompt_fingerprint,
        "new_semantic_prompt_fingerprint",
    )
    if (
        prior_method_version == new_method_version
        and prior_semantic_prompt_fingerprint
        == new_semantic_prompt_fingerprint
    ):
        raise UpdateQueueError(
            "recovery must change the method or semantic prompt"
        )
    decision_basis_id = _recovery_text(
        decision_basis_id, "decision_basis_id"
    )
    sanitized_reason = _sanitize_attempt_evidence(reason)
    if not isinstance(sanitized_reason, str) or not sanitized_reason.strip():
        raise UpdateQueueError("reason must remain non-empty after sanitization")
    actor_kind = _recovery_text(actor_kind, "actor_kind")
    if route != _RECOVERY_ROUTE:
        raise UpdateQueueError(
            "route must be the primary_then_fallback contract"
        )
    if not isinstance(superseded_attempt_ids, (list, tuple)):
        raise UpdateQueueError(
            "superseded_attempt_ids must be a sequence of UUIDs"
        )
    attempts = tuple(
        sorted(
            _recovery_uuid(value, "superseded_attempt_id")
            for value in superseded_attempt_ids
        )
    )
    if len(set(attempts)) != len(attempts) or len(attempts) > 2:
        raise UpdateQueueError(
            "at most two distinct superseded attempt IDs are allowed"
        )
    if legacy_failure_admission_id is not None:
        legacy_failure_admission_id = _recovery_uuid(
            legacy_failure_admission_id, "legacy_failure_admission_id"
        )
    if bool(attempts) == bool(legacy_failure_admission_id):
        raise UpdateQueueError(
            "distinct superseded attempt IDs or one legacy admission "
            "are required, but not both"
        )
    if legacy_failure_admission_id is not None and prior_generation != 1:
        raise UpdateQueueError(
            "legacy failure evidence may authorize only generation 1 to 2"
        )
    authorization_time = _timestamp(
        authorized_at or utc_now(), "authorized_at"
    )
    material = {
        "work_item_id": work_item_id,
        "prior_generation": prior_generation,
        "new_generation": new_generation,
        "source_bundle_uid": source_bundle_uid,
        "source_manifest_sha256": source_manifest_sha256,
        "prior_method_version": prior_method_version,
        "new_method_version": new_method_version,
        "prior_semantic_prompt_fingerprint": (
            prior_semantic_prompt_fingerprint
        ),
        "new_semantic_prompt_fingerprint": new_semantic_prompt_fingerprint,
        "superseded_attempt_ids": list(attempts),
        "legacy_failure_admission_id": legacy_failure_admission_id,
        "decision_basis_id": decision_basis_id,
        "reason": sanitized_reason,
        "route": route,
        "actor_kind": actor_kind,
    }
    material_sha = sha256_bytes(canonical_json_bytes(material))
    authorization_id = _deterministic_uuid(
        "work-recovery-authorization", material_sha
    )
    initial_transition_id = _deterministic_uuid(
        "work-recovery-transition",
        authorization_id,
        str(new_generation),
        "1",
        "retry_pending",
    )

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            if legacy_failure_admission_id is None:
                cursor.execute(
                    f"""
                    SELECT authorization_id::text, generation,
                           transition_id::text, replayed
                    FROM {QUEUE_SCHEMA}.authorize_failed_work_recovery(
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],
                      %s,%s,%s,%s,%s
                    )
                    """,
                    (
                        authorization_id,
                        initial_transition_id,
                        work_item_id,
                        prior_generation,
                        new_generation,
                        source_bundle_uid,
                        source_manifest_sha256,
                        prior_method_version,
                        new_method_version,
                        prior_semantic_prompt_fingerprint,
                        new_semantic_prompt_fingerprint,
                        list(attempts),
                        decision_basis_id,
                        sanitized_reason,
                        route,
                        actor_kind,
                        _iso(authorization_time),
                    ),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT authorization_id::text, generation,
                           transition_id::text, replayed
                    FROM {QUEUE_SCHEMA}.
                      authorize_failed_work_recovery_from_legacy(
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s
                      )
                    """,
                    (
                        authorization_id,
                        initial_transition_id,
                        work_item_id,
                        prior_generation,
                        new_generation,
                        legacy_failure_admission_id,
                        source_bundle_uid,
                        source_manifest_sha256,
                        prior_method_version,
                        new_method_version,
                        prior_semantic_prompt_fingerprint,
                        new_semantic_prompt_fingerprint,
                        decision_basis_id,
                        sanitized_reason,
                        route,
                        actor_kind,
                        _iso(authorization_time),
                    ),
                )
            result = cursor.fetchone()
    if result is None:
        raise UpdateQueueError("recovery authorization returned no receipt")

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT authorization_id::text, generation, current_state,
                       is_terminal, source_bundle_uid,
                       source_manifest_sha256::text, decision_basis_id, route,
                       legacy_failure_admission_id::text
                FROM {QUEUE_SCHEMA}.v_recovery_generation_current
                WHERE work_item_id = %s AND generation = %s
                """,
                (work_item_id, new_generation),
            )
            current = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT attempt_id::text
                FROM {QUEUE_SCHEMA}.recovery_superseded_attempt
                WHERE authorization_id = %s
                ORDER BY attempt_id
                """,
                (authorization_id,),
            )
            persisted_attempts = tuple(str(row[0]) for row in cursor.fetchall())
    if (
        current is None
        or str(current[0]) != authorization_id
        or int(current[1]) != new_generation
        or str(current[2]) != "retry_pending"
        or bool(current[3])
        or str(current[4]) != source_bundle_uid
        or str(current[5]) != source_manifest_sha256
        or str(current[6]) != decision_basis_id
        or str(current[7]) != route
        or (
            None if current[8] is None else str(current[8])
        ) != legacy_failure_admission_id
        or persisted_attempts != attempts
    ):
        raise UpdateQueueError(
            "fresh-connection recovery authorization verification failed"
        )
    normalized = _normalize(list(current)) + [list(persisted_attempts)]
    return {
        "schema": RECOVERY_AUTHORIZATION_RECEIPT_SCHEMA,
        "authorization_id": authorization_id,
        "work_item_id": work_item_id,
        "prior_generation": prior_generation,
        "generation": new_generation,
        "transition_id": initial_transition_id,
        "current_state": "retry_pending",
        "is_terminal": False,
        "replayed": bool(result[3]),
        "source_bundle_uid": source_bundle_uid,
        "source_manifest_sha256": source_manifest_sha256,
        "prior_method_version": prior_method_version,
        "new_method_version": new_method_version,
        "prior_semantic_prompt_fingerprint": (
            prior_semantic_prompt_fingerprint
        ),
        "new_semantic_prompt_fingerprint": new_semantic_prompt_fingerprint,
        "superseded_attempt_ids": list(attempts),
        "legacy_failure_admission_id": legacy_failure_admission_id,
        "decision_basis_id": decision_basis_id,
        "route": route,
        "fingerprint": sha256_bytes(canonical_json_bytes(normalized)),
    }


def advance_work_recovery(
    conninfo: str,
    *,
    work_item_id: str,
    generation: int,
    to_state: str,
    actor_kind: str,
    source_job_id: str | None = None,
    bundle_receipt_id: str | None = None,
    candidate_proposal_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Advance one authorized generation; terminal states never requeue it."""

    work_item_id = _recovery_uuid(work_item_id, "work_item_id")
    if not isinstance(generation, int) or generation < 2:
        raise UpdateQueueError("generation must be an integer of at least 2")
    if to_state not in _RECOVERY_TRANSITION_STATES:
        raise UpdateQueueError("recovery transition target is invalid")
    actor_kind = _recovery_text(actor_kind, "actor_kind")
    if source_job_id is not None:
        source_job_id = _recovery_uuid(source_job_id, "source_job_id")
    if bundle_receipt_id is not None:
        bundle_receipt_id = _recovery_uuid(
            bundle_receipt_id, "bundle_receipt_id"
        )
    if candidate_proposal_id is not None:
        candidate_proposal_id = _recovery_uuid(
            candidate_proposal_id, "candidate_proposal_id"
        )
    if to_state == "proposal_running" and source_job_id is None:
        raise UpdateQueueError(
            "proposal_running recovery requires source_job_id"
        )
    transition_time = _timestamp(recorded_at or utc_now(), "recorded_at")
    material = {
        "work_item_id": work_item_id,
        "generation": generation,
        "to_state": to_state,
        "actor_kind": actor_kind,
        "source_job_id": source_job_id,
        "bundle_receipt_id": bundle_receipt_id,
        "candidate_proposal_id": candidate_proposal_id,
    }
    transition_id = _deterministic_uuid(
        "work-recovery-transition",
        sha256_bytes(canonical_json_bytes(material)),
    )

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT transition_id::text, transition_seq, to_state, replayed
                FROM {QUEUE_SCHEMA}.advance_recovery_generation(
                  %s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    transition_id,
                    work_item_id,
                    generation,
                    to_state,
                    actor_kind,
                    source_job_id,
                    bundle_receipt_id,
                    candidate_proposal_id,
                    _iso(transition_time),
                ),
            )
            result = cursor.fetchone()
    if result is None:
        raise UpdateQueueError("recovery transition returned no receipt")

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT transition_seq, current_state, is_terminal,
                       source_job_id::text, bundle_receipt_id::text,
                       candidate_proposal_id::text
                FROM {QUEUE_SCHEMA}.v_recovery_generation_current
                WHERE work_item_id = %s AND generation = %s
                """,
                (work_item_id, generation),
            )
            current = cursor.fetchone()
    expected_terminal = to_state in {
        "staged_needs_review",
        "staged_pending_anchor",
        "failed_terminal",
        "partition_required",
    }
    if (
        current is None
        or int(current[0]) != int(result[1])
        or str(current[1]) != to_state
        or bool(current[2]) != expected_terminal
        or (str(current[3]) if current[3] is not None else None)
        != source_job_id
        or (str(current[4]) if current[4] is not None else None)
        != bundle_receipt_id
        or (str(current[5]) if current[5] is not None else None)
        != candidate_proposal_id
    ):
        raise UpdateQueueError(
            "fresh-connection recovery transition verification failed"
        )
    normalized = [
        work_item_id,
        generation,
        transition_id,
        *_normalize(list(current)),
    ]
    return {
        "schema": RECOVERY_TRANSITION_RECEIPT_SCHEMA,
        "transition_id": transition_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "transition_seq": int(result[1]),
        "to_state": to_state,
        "is_terminal": expected_terminal,
        "replayed": bool(result[3]),
        "fingerprint": sha256_bytes(canonical_json_bytes(normalized)),
    }


def register_work_recovery_attempt(
    conninfo: str,
    *,
    work_item_id: str,
    generation: int,
    route: str,
    attempt_id: str,
    source_job_id: str,
    method_version: str,
    semantic_prompt_fingerprint: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Link one existing worker attempt to one recovery generation route."""

    work_item_id = _recovery_uuid(work_item_id, "work_item_id")
    attempt_id = _recovery_uuid(attempt_id, "attempt_id")
    source_job_id = _recovery_uuid(source_job_id, "source_job_id")
    if not isinstance(generation, int) or generation < 2:
        raise UpdateQueueError("generation must be an integer of at least 2")
    if route not in _RECOVERY_ATTEMPT_ROUTES:
        raise UpdateQueueError("recovery attempt route is invalid")
    method_version = _recovery_text(method_version, "method_version")
    semantic_prompt_fingerprint = _recovery_sha256(
        semantic_prompt_fingerprint, "semantic_prompt_fingerprint"
    )
    attempt_time = _timestamp(recorded_at or utc_now(), "recorded_at")

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT attempt_id::text, route, replayed
                FROM {QUEUE_SCHEMA}.register_recovery_route_attempt(
                  %s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    work_item_id,
                    generation,
                    route,
                    attempt_id,
                    source_job_id,
                    method_version,
                    semantic_prompt_fingerprint,
                    _iso(attempt_time),
                ),
            )
            result = cursor.fetchone()
    if result is None:
        raise UpdateQueueError("recovery route attempt returned no receipt")

    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cursor.execute(
                f"""
                SELECT linked.attempt_id::text, linked.route,
                       linked.source_job_id::text, linked.method_version,
                       linked.semantic_prompt_fingerprint::text,
                       attempt.status
                FROM {QUEUE_SCHEMA}.recovery_route_attempt linked
                JOIN {OPS_SCHEMA}.worker_attempt attempt
                  ON attempt.attempt_id = linked.attempt_id
                WHERE linked.work_item_id = %s
                  AND linked.generation = %s
                  AND linked.route = %s
                """,
                (work_item_id, generation, route),
            )
            current = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT current_state
                FROM {QUEUE_SCHEMA}.v_recovery_generation_current
                WHERE work_item_id = %s AND generation = %s
                """,
                (work_item_id, generation),
            )
            generation_state = cursor.fetchone()
    if (
        current is None
        or generation_state is None
        or str(current[0]) != attempt_id
        or str(current[1]) != route
        or str(current[2]) != source_job_id
        or str(current[3]) != method_version
        or str(current[4]) != semantic_prompt_fingerprint
        or str(generation_state[0]) != "proposal_running"
    ):
        raise UpdateQueueError(
            "fresh-connection recovery route verification failed"
        )
    normalized = _normalize(list(current))
    return {
        "schema": RECOVERY_ROUTE_ATTEMPT_RECEIPT_SCHEMA,
        "attempt_id": attempt_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "route": route,
        "outcome": str(current[5]),
        "current_state": "proposal_running",
        "replayed": bool(result[2]),
        "fingerprint": sha256_bytes(canonical_json_bytes(normalized)),
    }
