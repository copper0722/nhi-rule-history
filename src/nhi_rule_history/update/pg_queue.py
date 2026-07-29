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
PARTITION_RECOVERY_SCHEMA = "nhi_rule_history_partition_recovery"
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
PARTITION_RECOVERY_ADMISSION_SCHEMA = (
    "nhi-rule-history/partition-recovery-admission/v1"
)
PARTITION_RECOVERY_ADMISSION_VERIFICATION_SCHEMA = (
    "nhi-rule-history/partition-recovery-admission-verification/v1"
)
PARTITION_RECOVERY_ADMISSION_RECEIPT_SCHEMA = (
    "nhi-rule-history/partition-recovery-admission-receipt/v1"
)
PARTITION_RECOVERY_AUTHORIZATION_RECEIPT_SCHEMA = (
    "nhi-rule-history/partition-recovery-authorization-receipt/v1"
)
PARTITION_RECOVERY_STATUS_SCHEMA = (
    "nhi-rule-history/partition-recovery-status/v1"
)
PARTITION_RECOVERY_DISPATCH_CONTRACT = (
    "nhi-rule-history/partition-recovery-dispatch/v1"
)
PARTITION_RECOVERY_DISPATCH_RECEIPT_SCHEMA = (
    "nhi-rule-history/partition-recovery-dispatch-receipt/v1"
)
PARTITION_RECOVERY_ROUTE_RESERVATION_SCHEMA = (
    "nhi-rule-history/partition-recovery-route-reservation/v1"
)
PARTITION_RECOVERY_ROUTE_RESULT_SCHEMA = (
    "nhi-rule-history/partition-recovery-route-result/v1"
)
PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA = (
    "nhi-rule-history/partition-recovery-terminal-evidence/v1"
)
PARTITION_RECOVERY_CANONICAL_ENCODING = (
    "nhi-rule-history/canonical-json-bytes/no-float/v1"
)
PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT = (
    "nhi-rule-history/partition-recovery-output-namespace/v1"
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
_PARTITION_ROUTE_BY_ORDINAL = {1: "primary", 2: "fallback"}
_PARTITION_ROUTE_FAILURE_CLASSES = {
    "transport_failure",
    "execution_failure",
    "timeout",
    "process_exit_failure",
    "invalid_json",
    "output_schema_invalid",
    "unknown_enum",
    "missing_locator",
    "locator_mismatch",
    "source_text_mismatch",
    "output_contract_inconsistent",
}
_PARTITION_ROUTE_RESULT_STATUSES = {
    "succeeded",
    "failed",
    "execution_unknown",
}
_PARTITION_FAILED_TERMINAL_PRECALL_REASONS = {
    "preflight_replay_mismatch",
    "preflight_nondeterminism",
    "packet_or_contract_tamper",
    "restart_before_model_reservation",
}
_PARTITION_FAILED_TERMINAL_EXECUTION_REASONS = {
    "primary_and_fallback_failed",
    "execution_unknown",
    "restart_after_model_result",
    "restart_open_route_execution_unknown",
}
_PARTITION_FAILED_TERMINAL_REASONS = (
    _PARTITION_FAILED_TERMINAL_PRECALL_REASONS
    | _PARTITION_FAILED_TERMINAL_EXECUTION_REASONS
)
_PARTITION_TERMINAL_FAILURE_CODE_BY_REASON = {
    "preflight_replay_mismatch": "ADMITTED_SUITABILITY_REPLAY_MISMATCH",
    "preflight_nondeterminism": (
        "ADMITTED_SUITABILITY_CHANGED_DURING_RUN"
    ),
    "packet_or_contract_tamper": (
        "PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE"
    ),
    "restart_before_model_reservation": (
        "RECOVERY_RESTART_BEFORE_MODEL_RESERVATION"
    ),
    "restart_after_model_result": "RECOVERY_RESTART_AFTER_MODEL_RESULT",
    "restart_open_route_execution_unknown": (
        "RECOVERY_OPEN_ROUTE_EXECUTION_UNKNOWN"
    ),
    "execution_unknown": "WORKER_EXECUTION_UNKNOWN",
    "primary_and_fallback_failed": "PRIMARY_AND_FALLBACK_FAILED",
}
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


def _partition_recovery_sha256_uuid(label: str, *parts: str) -> str:
    digest = bytearray(
        bytes.fromhex(
            sha256_bytes(
                "\x1f".join((label, *parts)).encode("utf-8")
            )
        )[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x80
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


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


def _recovery_exact_fields(
    value: Any,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        missing = sorted(fields - set(value) if isinstance(value, dict) else fields)
        extra = sorted(set(value) - fields if isinstance(value, dict) else ())
        raise UpdateQueueError(
            f"{label} fields are invalid; missing={missing}, extra={extra}"
        )
    return value


def _recovery_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UpdateQueueError(f"{label} must be a nonnegative integer")
    return value


def _recovery_positive_int(value: Any, label: str) -> int:
    value = _recovery_nonnegative_int(value, label)
    if value == 0:
        raise UpdateQueueError(f"{label} must be a positive integer")
    return value


def _recovery_git_identity(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None
    ):
        raise UpdateQueueError(
            f"{label} must be a lowercase 40- or 64-character Git identity"
        )
    return value


def _recovery_canonical_uuid(value: Any, label: str) -> str:
    normalized = _recovery_uuid(value, label)
    if value != normalized:
        raise UpdateQueueError(f"{label} must use canonical lowercase UUID text")
    return normalized


def _recovery_canonical_text(value: Any, label: str) -> str:
    normalized = _recovery_text(value, label)
    if value != normalized:
        raise UpdateQueueError(
            f"{label} must not contain surrounding whitespace"
        )
    return normalized


def _reject_floats(value: Any, label: str = "payload") -> None:
    if isinstance(value, float):
        raise UpdateQueueError(
            f"{label} must not contain floating-point values"
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_floats(item, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_floats(item, f"{label}[{index}]")


def _verify_partition_terminal_evidence(
    evidence: Mapping[str, Any],
    *,
    dispatch_claim_id: str,
    work_item_id: str,
    generation: int,
    authorization_id: str,
    admission_id: str,
    to_state: str,
) -> dict[str, Any]:
    common = {
        "schema",
        "dispatch_claim_id",
        "work_item_id",
        "generation",
        "authorization_id",
        "admission_id",
        "to_state",
        "auto_promotion_enabled",
    }
    if not isinstance(evidence, dict):
        raise UpdateQueueError("terminal evidence must be one JSON object")
    reason_code = evidence.get("reason_code")
    if to_state == "staged_needs_review":
        extras = {
            "candidate_receipt_sha256",
            "candidate_state",
            "selected_worker_role",
            "worker_calls",
            "finished_routes",
            "canonical_history_writes",
        }
    elif reason_code in {
        "restart_before_model_reservation",
        "restart_after_model_result",
        "restart_open_route_execution_unknown",
        "packet_or_contract_tamper",
    }:
        extras = {
            "reason_code",
            "failure_code",
            "preexisting_output_namespace",
            "generation_state",
            "finished_route_statuses",
            "open_route_reconciled_as_execution_unknown",
            "worker_reinvocation",
            "automatic_retry",
            "automatic_fallback",
        }
    elif reason_code in {
        "preflight_replay_mismatch",
    }:
        extras = {
            "reason_code",
            "failure_code",
            "admitted",
            "replayed",
            "worker_calls",
            "automatic_retry",
        }
    elif reason_code == "execution_unknown":
        extras = {
            "reason_code",
            "failure_code",
            "execution_unknown_routes",
            "automatic_retry",
            "automatic_fallback",
        }
    elif reason_code == "primary_and_fallback_failed":
        extras = {
            "reason_code",
            "failure_code",
            "failure_receipt_sha256",
            "finished_routes",
            "automatic_retry",
        }
    elif reason_code == "preflight_nondeterminism":
        extras = {
            "reason_code",
            "failure_code",
            "worker_status",
            "worker_calls",
            "automatic_retry",
        }
    else:
        raise UpdateQueueError(
            "terminal evidence reason/state variant is invalid"
        )
    payload = _recovery_exact_fields(
        evidence, common | extras, "terminal evidence"
    )
    expected_core = {
        "schema": PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
        "dispatch_claim_id": dispatch_claim_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "to_state": to_state,
        "auto_promotion_enabled": False,
    }
    if any(payload[key] != value for key, value in expected_core.items()):
        raise UpdateQueueError(
            "terminal evidence core tuple/schema is invalid"
        )
    if to_state == "failed_terminal" and payload["failure_code"] != (
        _PARTITION_TERMINAL_FAILURE_CODE_BY_REASON.get(reason_code)
    ):
        raise UpdateQueueError(
            "terminal evidence reason_code/failure_code pair is invalid"
        )
    if to_state == "staged_needs_review":
        _recovery_sha256(
            payload["candidate_receipt_sha256"],
            "terminal evidence.candidate_receipt_sha256",
        )
        if payload["candidate_state"] != "needs_review":
            raise UpdateQueueError(
                "staged terminal evidence must force needs_review"
            )
        if payload["selected_worker_role"] not in {"primary", "fallback"}:
            raise UpdateQueueError(
                "staged terminal evidence worker role is invalid"
            )
        if payload["canonical_history_writes"] != 0:
            raise UpdateQueueError(
                "staged terminal evidence cannot claim canonical writes"
            )
    for field in ("worker_calls", "canonical_history_writes"):
        if field in payload:
            _recovery_nonnegative_int(
                payload[field], f"terminal evidence.{field}"
            )
    for field in ("preexisting_output_namespace",):
        if field in payload and not isinstance(payload[field], bool):
            raise UpdateQueueError(
                f"terminal evidence.{field} must be boolean"
            )
    for field in ("admitted", "replayed"):
        if field in payload and (
            not isinstance(payload[field], dict) or not payload[field]
        ):
            raise UpdateQueueError(
                f"terminal evidence.{field} must be a nonempty object"
            )
    if "automatic_retry" in payload and payload["automatic_retry"] is not False:
        raise UpdateQueueError("terminal evidence cannot enable automatic retry")
    if (
        "automatic_fallback" in payload
        and payload["automatic_fallback"] is not False
    ):
        raise UpdateQueueError(
            "terminal evidence cannot enable automatic fallback"
        )
    if (
        "worker_reinvocation" in payload
        and payload["worker_reinvocation"] is not False
    ):
        raise UpdateQueueError(
            "terminal evidence cannot enable worker reinvocation"
        )
    for field in ("finished_routes", "execution_unknown_routes"):
        if field not in payload:
            continue
        route_map = payload[field]
        if (
            not isinstance(route_map, dict)
            or not route_map
            or not set(route_map).issubset({"primary", "fallback"})
        ):
            raise UpdateQueueError(
                f"terminal evidence.{field} must be a nonempty route map"
            )
        for role, receipt_sha256 in route_map.items():
            _recovery_sha256(
                receipt_sha256,
                f"terminal evidence.{field}.{role}",
            )
        if (
            field == "finished_routes"
            and to_state == "staged_needs_review"
        ):
            expected_roles = (
                {"primary"}
                if payload["selected_worker_role"] == "primary"
                else {"primary", "fallback"}
            )
            if set(route_map) != expected_roles:
                raise UpdateQueueError(
                    "staged finished_routes conflicts with selected role"
                )
            if payload["worker_calls"] != len(route_map):
                raise UpdateQueueError(
                    "staged worker_calls conflicts with finished routes"
                )
        if (
            field == "finished_routes"
            and reason_code == "primary_and_fallback_failed"
            and set(route_map) != {"primary", "fallback"}
        ):
            raise UpdateQueueError(
                "dual route failure requires both finished route receipts"
            )
    if "failure_receipt_sha256" in payload:
        _recovery_sha256(
            payload["failure_receipt_sha256"],
            "terminal evidence.failure_receipt_sha256",
        )
    if "finished_route_statuses" in payload:
        statuses = payload["finished_route_statuses"]
        if not isinstance(statuses, list) or len(statuses) > 2:
            raise UpdateQueueError(
                "terminal evidence.finished_route_statuses is invalid"
            )
        for index, status in enumerate(statuses):
            status = _recovery_exact_fields(
                status,
                {
                    "reservation_id",
                    "route_ordinal",
                    "route",
                    "status",
                    "failure_class",
                },
                f"terminal evidence.finished_route_statuses[{index}]",
            )
            ordinal = status["route_ordinal"]
            if ordinal not in {1, 2} or status["route"] != {
                1: "primary",
                2: "fallback",
            }[ordinal]:
                raise UpdateQueueError(
                    "terminal evidence finished route ordinal is invalid"
                )
            _recovery_canonical_uuid(
                status["reservation_id"],
                "terminal evidence finished route reservation_id",
            )
            if status["status"] not in _PARTITION_ROUTE_RESULT_STATUSES:
                raise UpdateQueueError(
                    "terminal evidence finished route status is invalid"
                )
            failure_valid = (
                (
                    status["status"] == "failed"
                    and status["failure_class"]
                    in _PARTITION_ROUTE_FAILURE_CLASSES
                )
                or (
                    status["status"] == "succeeded"
                    and status["failure_class"] is None
                )
                or (
                    status["status"] == "execution_unknown"
                    and status["failure_class"] == "execution_unknown"
                )
            )
            if not failure_valid:
                raise UpdateQueueError(
                    "terminal evidence finished route failure is invalid"
                )
    if "generation_state" in payload and payload["generation_state"] not in {
        "retry_pending",
        "proposal_running",
    }:
        raise UpdateQueueError(
            "terminal evidence generation_state is invalid"
        )
    if "open_route_reconciled_as_execution_unknown" in payload:
        reconciled = payload["open_route_reconciled_as_execution_unknown"]
        if reconciled is not None:
            _recovery_sha256(
                reconciled,
                "terminal evidence.open_route_reconciled_as_execution_unknown",
            )
    if reason_code == "restart_before_model_reservation" and (
        payload["preexisting_output_namespace"] is not False
        or payload["generation_state"] != "retry_pending"
        or payload["finished_route_statuses"]
        or payload["open_route_reconciled_as_execution_unknown"] is not None
    ):
        raise UpdateQueueError(
            "restart-before-model terminal evidence is contradictory"
        )
    if reason_code == "restart_after_model_result" and (
        payload["generation_state"] != "proposal_running"
        or not payload["finished_route_statuses"]
        or payload["open_route_reconciled_as_execution_unknown"] is not None
    ):
        raise UpdateQueueError(
            "restart-after-result terminal evidence is contradictory"
        )
    if reason_code == "restart_open_route_execution_unknown" and (
        payload["generation_state"] != "proposal_running"
        or payload["open_route_reconciled_as_execution_unknown"] is None
    ):
        raise UpdateQueueError(
            "open-route reconciliation terminal evidence is contradictory"
        )
    if reason_code == "packet_or_contract_tamper" and (
        payload["preexisting_output_namespace"] is not True
        or payload["generation_state"] != "retry_pending"
        or payload["finished_route_statuses"]
        or payload["open_route_reconciled_as_execution_unknown"] is not None
    ):
        raise UpdateQueueError(
            "packet/contract-tamper terminal evidence is contradictory"
        )
    if reason_code == "preflight_nondeterminism":
        _recovery_canonical_text(
            payload["worker_status"],
            "terminal evidence.worker_status",
        )
    return payload


def _verify_partition_locator(value: Any, label: str) -> dict[str, Any]:
    locator = _recovery_exact_fields(
        value,
        {
            "logical_object_id",
            "logical_locator",
            "byte_count",
            "sha256",
            "media_type",
        },
        label,
    )
    _recovery_canonical_text(
        locator["logical_object_id"], f"{label}.logical_object_id"
    )
    _recovery_relative_path(
        locator["logical_locator"], f"{label}.logical_locator"
    )
    _recovery_nonnegative_int(locator["byte_count"], f"{label}.byte_count")
    _recovery_sha256(locator["sha256"], f"{label}.sha256")
    _recovery_canonical_text(locator["media_type"], f"{label}.media_type")
    return locator


def partition_recovery_output_namespace(
    *,
    work_item_id: str,
    generation: int,
    job_fingerprint: str,
) -> str:
    """Return the only allowed relative output namespace for one generation."""

    normalized_work_item_id = _recovery_canonical_uuid(
        work_item_id, "work_item_id"
    )
    if not isinstance(generation, int) or generation < 2:
        raise UpdateQueueError("generation must be an integer of at least 2")
    normalized_job_fingerprint = _recovery_sha256(
        job_fingerprint, "job_fingerprint"
    )
    return (
        "partition-recovery/"
        f"{normalized_work_item_id}/generation-{generation}/"
        f"{normalized_job_fingerprint}"
    )


def verify_partition_recovery_evidence(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and hash one complete zero-call partition-recovery preimage.

    This is a pure operator-side gate.  It has no PostgreSQL, network,
    acquisition, corpus-registration, candidate, or canonical-history write
    path.
    """

    _reject_floats(evidence)
    payload = _recovery_exact_fields(
        dict(evidence),
        {
            "schema",
            "canonical_encoding_contract",
            "generation_1",
            "source_evidence",
            "execution_delta",
            "worker_semantics",
            "governance",
            "output_namespace",
        },
        "partition recovery evidence",
    )
    if payload["schema"] != PARTITION_RECOVERY_ADMISSION_SCHEMA:
        raise UpdateQueueError("partition recovery evidence schema is invalid")
    if (
        payload["canonical_encoding_contract"]
        != PARTITION_RECOVERY_CANONICAL_ENCODING
    ):
        raise UpdateQueueError(
            "partition recovery canonical encoding is invalid"
        )

    generation = _recovery_exact_fields(
        payload["generation_1"],
        {
            "work_item_id",
            "prior_generation",
            "terminal_state",
            "terminal_transition_sequence",
            "terminal_transition_id",
            "terminal_evidence_sha256",
            "old_job_fingerprint",
            "old_partition_receipt",
            "old_suitability_receipt",
            "worker_call_count",
            "worker_attempt_count",
            "candidate_count",
            "route_attempt_count",
            "transition_count",
            "ordered_chain_sha256",
            "rowset_fingerprint",
            "transitions",
        },
        "generation_1",
    )
    work_item_id = _recovery_canonical_uuid(
        generation["work_item_id"], "generation_1.work_item_id"
    )
    if generation["prior_generation"] != 1:
        raise UpdateQueueError("partition recovery requires prior generation 1")
    if generation["terminal_state"] != "partition_required":
        raise UpdateQueueError(
            "generation 1 terminal state must be partition_required"
        )
    terminal_sequence = _recovery_positive_int(
        generation["terminal_transition_sequence"],
        "generation_1.terminal_transition_sequence",
    )
    terminal_transition_id = _recovery_canonical_uuid(
        generation["terminal_transition_id"],
        "generation_1.terminal_transition_id",
    )
    terminal_evidence_sha256 = _recovery_sha256(
        generation["terminal_evidence_sha256"],
        "generation_1.terminal_evidence_sha256",
    )
    _recovery_sha256(
        generation["old_job_fingerprint"],
        "generation_1.old_job_fingerprint",
    )
    old_partition_receipt = _verify_partition_locator(
        generation["old_partition_receipt"],
        "generation_1.old_partition_receipt",
    )
    old_suitability_receipt = _verify_partition_locator(
        generation["old_suitability_receipt"],
        "generation_1.old_suitability_receipt",
    )
    if old_partition_receipt["logical_object_id"] == (
        old_suitability_receipt["logical_object_id"]
    ):
        raise UpdateQueueError(
            "old partition and suitability receipts must be distinct objects"
        )
    for field in (
        "worker_call_count",
        "worker_attempt_count",
        "candidate_count",
        "route_attempt_count",
    ):
        if _recovery_nonnegative_int(
            generation[field], f"generation_1.{field}"
        ) != 0:
            raise UpdateQueueError(
                "partition recovery requires zero prior worker calls, "
                "worker attempts, candidates, and route attempts"
            )
    transitions = generation["transitions"]
    if not isinstance(transitions, list) or not transitions:
        raise UpdateQueueError(
            "generation_1.transitions must be a non-empty ordered array"
        )
    normalized_transitions: list[dict[str, Any]] = []
    prior_state: str | None = None
    seen_transition_ids: set[str] = set()
    for index, raw_transition in enumerate(transitions, start=1):
        transition = _recovery_exact_fields(
            raw_transition,
            {
                "work_item_id",
                "transition_seq",
                "transition_id",
                "from_state",
                "to_state",
                "actor_kind",
                "evidence_sha256",
                "evidence_json",
                "source_job_id",
                "bundle_receipt_id",
                "candidate_proposal_id",
                "recorded_at",
            },
            f"generation_1.transitions[{index - 1}]",
        )
        if (
            _recovery_canonical_uuid(
                transition["work_item_id"],
                f"generation_1.transitions[{index - 1}].work_item_id",
            )
            != work_item_id
        ):
            raise UpdateQueueError(
                "generation 1 transition work item does not match its chain"
            )
        if transition["transition_seq"] != index:
            raise UpdateQueueError(
                "generation 1 transition sequence must be contiguous from 1"
            )
        transition_id = _recovery_canonical_uuid(
            transition["transition_id"],
            f"generation_1.transitions[{index - 1}].transition_id",
        )
        if transition_id in seen_transition_ids:
            raise UpdateQueueError(
                "generation 1 transition IDs must be distinct"
            )
        seen_transition_ids.add(transition_id)
        from_state = transition["from_state"]
        if index == 1:
            if from_state is not None:
                raise UpdateQueueError(
                    "generation 1 first transition must have null from_state"
                )
        elif from_state != prior_state:
            raise UpdateQueueError(
                "generation 1 transition chain is discontinuous"
            )
        to_state = _recovery_canonical_text(
            transition["to_state"],
            f"generation_1.transitions[{index - 1}].to_state",
        )
        _recovery_canonical_text(
            transition["actor_kind"],
            f"generation_1.transitions[{index - 1}].actor_kind",
        )
        _recovery_sha256(
            transition["evidence_sha256"],
            f"generation_1.transitions[{index - 1}].evidence_sha256",
        )
        if (
            not isinstance(transition["evidence_json"], dict)
            or not transition["evidence_json"]
        ):
            raise UpdateQueueError(
                "generation 1 transition evidence_json must be a "
                "non-empty object"
            )
        _recovery_canonical_uuid(
            transition["source_job_id"],
            f"generation_1.transitions[{index - 1}].source_job_id",
        )
        for optional_id in (
            "bundle_receipt_id",
            "candidate_proposal_id",
        ):
            if transition[optional_id] is not None:
                _recovery_canonical_uuid(
                    transition[optional_id],
                    "generation_1.transitions"
                    f"[{index - 1}].{optional_id}",
                )
        _timestamp(
            transition["recorded_at"],
            f"generation_1.transitions[{index - 1}].recorded_at",
        )
        prior_state = to_state
        normalized_transitions.append(transition)
    transition_count = _recovery_positive_int(
        generation["transition_count"], "generation_1.transition_count"
    )
    if (
        transition_count != len(normalized_transitions)
        or terminal_sequence != transition_count
        or normalized_transitions[-1]["transition_id"]
        != terminal_transition_id
        or normalized_transitions[-1]["to_state"] != "partition_required"
        or normalized_transitions[-1]["evidence_sha256"]
        != terminal_evidence_sha256
    ):
        raise UpdateQueueError(
            "generation 1 terminal row does not match the complete chain"
        )
    expected_ordered_chain_sha256 = sha256_bytes(
        canonical_json_bytes(normalized_transitions)
    )
    if _recovery_sha256(
        generation["ordered_chain_sha256"],
        "generation_1.ordered_chain_sha256",
    ) != expected_ordered_chain_sha256:
        raise UpdateQueueError(
            "generation 1 ordered chain hash does not match its canonical "
            "full-row array"
        )
    expected_rowset_fingerprint = sha256_bytes(
        canonical_json_bytes(
            sorted(
                normalized_transitions,
                key=lambda row: row["transition_id"],
            )
        )
    )
    if _recovery_sha256(
        generation["rowset_fingerprint"],
        "generation_1.rowset_fingerprint",
    ) != expected_rowset_fingerprint:
        raise UpdateQueueError(
            "generation 1 rowset fingerprint does not match its canonical "
            "full-row set"
        )

    source = _recovery_exact_fields(
        payload["source_evidence"],
        {
            "source_bundle",
            "corpus_bundle",
            "sealed_packet",
            "reuse_existing_bundle",
            "repoll_allowed",
            "reacquire_allowed",
            "new_corpus_registration_allowed",
        },
        "source_evidence",
    )
    source_bundle = _recovery_exact_fields(
        source["source_bundle"],
        {
            "bundle_id",
            "manifest_sha256",
            "byte_count",
            "logical_locator",
        },
        "source_evidence.source_bundle",
    )
    _recovery_canonical_text(
        source_bundle["bundle_id"], "source_evidence.source_bundle.bundle_id"
    )
    _recovery_sha256(
        source_bundle["manifest_sha256"],
        "source_evidence.source_bundle.manifest_sha256",
    )
    _recovery_positive_int(
        source_bundle["byte_count"],
        "source_evidence.source_bundle.byte_count",
    )
    _recovery_relative_path(
        source_bundle["logical_locator"],
        "source_evidence.source_bundle.logical_locator",
    )
    corpus_bundle = _recovery_exact_fields(
        source["corpus_bundle"],
        {
            "bundle_id",
            "manifest_sha256",
            "byte_count",
            "logical_locator",
        },
        "source_evidence.corpus_bundle",
    )
    _recovery_canonical_text(
        corpus_bundle["bundle_id"], "source_evidence.corpus_bundle.bundle_id"
    )
    _recovery_sha256(
        corpus_bundle["manifest_sha256"],
        "source_evidence.corpus_bundle.manifest_sha256",
    )
    _recovery_positive_int(
        corpus_bundle["byte_count"],
        "source_evidence.corpus_bundle.byte_count",
    )
    _recovery_relative_path(
        corpus_bundle["logical_locator"],
        "source_evidence.corpus_bundle.logical_locator",
    )
    sealed_packet = _recovery_exact_fields(
        source["sealed_packet"],
        {
            "manifest_sha256",
            "byte_count",
            "logical_locator",
            "ordered_artifact_sha256_set_digest",
            "artifacts",
        },
        "source_evidence.sealed_packet",
    )
    _recovery_sha256(
        sealed_packet["manifest_sha256"],
        "source_evidence.sealed_packet.manifest_sha256",
    )
    _recovery_positive_int(
        sealed_packet["byte_count"],
        "source_evidence.sealed_packet.byte_count",
    )
    _recovery_relative_path(
        sealed_packet["logical_locator"],
        "source_evidence.sealed_packet.logical_locator",
    )
    artifacts = sealed_packet["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise UpdateQueueError(
            "source_evidence.sealed_packet.artifacts must be non-empty"
        )
    artifact_ids: set[str] = set()
    artifact_shas: set[str] = set()
    for index, artifact in enumerate(artifacts):
        verified_artifact = _verify_partition_locator(
            artifact, f"source_evidence.sealed_packet.artifacts[{index}]"
        )
        if (
            verified_artifact["logical_object_id"] in artifact_ids
            or verified_artifact["sha256"] in artifact_shas
        ):
            raise UpdateQueueError(
                "sealed packet artifact identities and SHA-256 values "
                "must be distinct"
            )
        artifact_ids.add(verified_artifact["logical_object_id"])
        artifact_shas.add(verified_artifact["sha256"])
    artifact_digest = sha256_bytes(
        canonical_json_bytes(
            [
                {
                    "ordinal": index,
                    "sha256": artifact["sha256"],
                }
                for index, artifact in enumerate(artifacts, start=1)
            ]
        )
    )
    if (
        sealed_packet["ordered_artifact_sha256_set_digest"]
        != artifact_digest
    ):
        raise UpdateQueueError(
            "sealed packet ordered artifact digest is inconsistent"
        )
    if (
        source["reuse_existing_bundle"] is not True
        or source["repoll_allowed"] is not False
        or source["reacquire_allowed"] is not False
        or source["new_corpus_registration_allowed"] is not False
    ):
        raise UpdateQueueError(
            "partition recovery must reuse sealed evidence and forbid "
            "poll, acquisition, and corpus registration"
        )

    delta = _recovery_exact_fields(
        payload["execution_delta"],
        {
            "old_suitability_contract",
            "new_suitability_contract",
            "old_fingerprint_domain",
            "new_fingerprint_domain",
            "new_job_fingerprint",
            "suitability_preflight",
            "suitability_v2_schema_sha256",
            "suitability_v2_receipt_sha256",
            "verifier_contract_version",
            "verifier_code_commit",
            "verifier_config_sha256",
            "verifier_executable_sha256",
            "execution_contract_version",
            "execution_contract_sha256",
            "dispatch_contract_version",
            "route_policy_sha256",
        },
        "execution_delta",
    )
    exact_contracts = {
        "old_suitability_contract": (
            "nhi-rule-history/worker-suitability/v1"
        ),
        "new_suitability_contract": (
            "nhi-rule-history/worker-suitability/v2"
        ),
        "old_fingerprint_domain": (
            "nhi-rule-history/worker-job-fingerprint/v3"
        ),
        "new_fingerprint_domain": (
            "nhi-rule-history/worker-job-fingerprint/v4"
        ),
        "dispatch_contract_version": PARTITION_RECOVERY_DISPATCH_CONTRACT,
    }
    for field, expected in exact_contracts.items():
        if delta[field] != expected:
            raise UpdateQueueError(
                f"execution_delta.{field} must be {expected}"
            )
    new_job_fingerprint = _recovery_sha256(
        delta["new_job_fingerprint"],
        "execution_delta.new_job_fingerprint",
    )
    suitability = _recovery_exact_fields(
        delta["suitability_preflight"],
        {
            "designation_candidates",
            "effective_designation_candidates",
            "collapsed_parent_designations",
            "decision",
            "reason_codes",
        },
        "execution_delta.suitability_preflight",
    )
    for field in (
        "designation_candidates",
        "effective_designation_candidates",
        "collapsed_parent_designations",
        "reason_codes",
    ):
        values = suitability[field]
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise UpdateQueueError(
                f"execution_delta.suitability_preflight.{field} "
                "must be a distinct string array"
            )
    if (
        not suitability["designation_candidates"]
        or not suitability["effective_designation_candidates"]
        or suitability["decision"] != "suitable"
        or suitability["reason_codes"]
    ):
        raise UpdateQueueError(
            "partition recovery admission must bind one reviewed suitable "
            "v2 preflight with no reason codes"
        )
    for field in (
        "suitability_v2_schema_sha256",
        "suitability_v2_receipt_sha256",
        "verifier_config_sha256",
        "verifier_executable_sha256",
        "execution_contract_sha256",
        "route_policy_sha256",
    ):
        _recovery_sha256(delta[field], f"execution_delta.{field}")
    _recovery_canonical_text(
        delta["verifier_contract_version"],
        "execution_delta.verifier_contract_version",
    )
    _recovery_git_identity(
        delta["verifier_code_commit"],
        "execution_delta.verifier_code_commit",
    )
    _recovery_canonical_text(
        delta["execution_contract_version"],
        "execution_delta.execution_contract_version",
    )

    worker = _recovery_exact_fields(
        payload["worker_semantics"],
        {
            "prompt_version",
            "prompt_sha256",
            "semantic_prompt_changed",
            "execution_contract_changed",
        },
        "worker_semantics",
    )
    if (
        worker["prompt_version"]
        != "nhi-rule-history-source-proposal/2.0.0"
    ):
        raise UpdateQueueError("worker semantic prompt version changed")
    _recovery_sha256(
        worker["prompt_sha256"], "worker_semantics.prompt_sha256"
    )
    if (
        worker["semantic_prompt_changed"] is not False
        or worker["execution_contract_changed"] is not True
    ):
        raise UpdateQueueError(
            "partition recovery requires unchanged semantic prompt and "
            "a changed execution contract"
        )

    governance = _recovery_exact_fields(
        payload["governance"],
        {
            "decision_basis_id",
            "public_repo_commit",
            "private_controller_commit",
            "migration_sha256",
            "admission_contract_version",
            "review_decision_receipt_sha256",
        },
        "governance",
    )
    _recovery_canonical_text(
        governance["decision_basis_id"], "governance.decision_basis_id"
    )
    _recovery_git_identity(
        governance["public_repo_commit"], "governance.public_repo_commit"
    )
    _recovery_git_identity(
        governance["private_controller_commit"],
        "governance.private_controller_commit",
    )
    _recovery_sha256(
        governance["migration_sha256"], "governance.migration_sha256"
    )
    if (
        governance["admission_contract_version"]
        != PARTITION_RECOVERY_ADMISSION_SCHEMA
    ):
        raise UpdateQueueError(
            "governance.admission_contract_version is invalid"
        )
    _recovery_sha256(
        governance["review_decision_receipt_sha256"],
        "governance.review_decision_receipt_sha256",
    )

    output = _recovery_exact_fields(
        payload["output_namespace"],
        {"contract", "generation", "relative_path"},
        "output_namespace",
    )
    if (
        output["contract"]
        != PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT
        or output["generation"] != 2
    ):
        raise UpdateQueueError(
            "partition recovery output namespace contract is invalid"
        )
    expected_output_namespace = partition_recovery_output_namespace(
        work_item_id=work_item_id,
        generation=2,
        job_fingerprint=new_job_fingerprint,
    )
    if output["relative_path"] != expected_output_namespace:
        raise UpdateQueueError(
            "partition recovery output must use the exact generation-2 "
            "namespace"
        )

    payload_bytes = canonical_json_bytes(payload)
    payload_sha256 = sha256_bytes(payload_bytes)
    admission_id = _deterministic_uuid(
        "partition-recovery-admission", payload_sha256
    )
    return {
        "schema": PARTITION_RECOVERY_ADMISSION_VERIFICATION_SCHEMA,
        "admission_id": admission_id,
        "work_item_id": work_item_id,
        "prior_generation": 1,
        "new_generation": 2,
        "terminal_transition_id": terminal_transition_id,
        "admission_payload_sha256": payload_sha256,
        "new_execution_contract_sha256": delta[
            "execution_contract_sha256"
        ],
        "sealed_packet_manifest_sha256": sealed_packet["manifest_sha256"],
        "suitability_v2_receipt_sha256": delta[
            "suitability_v2_receipt_sha256"
        ],
        "new_job_fingerprint": new_job_fingerprint,
        "prompt_sha256": worker["prompt_sha256"],
        "route_policy_sha256": delta["route_policy_sha256"],
        "output_namespace": expected_output_namespace,
        "payload": payload,
    }


def load_partition_recovery_evidence(path: str | Path) -> dict[str, Any]:
    """Read one canonical admission payload and run the complete verifier."""

    try:
        payload_bytes = Path(path).read_bytes()
        payload = json.loads(payload_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateQueueError(
            "partition recovery evidence is unreadable"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload_bytes != canonical_json_bytes(payload)
    ):
        raise UpdateQueueError(
            "partition recovery evidence must be canonical immutable JSON"
        )
    return verify_partition_recovery_evidence(payload)


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


def _partition_function_result(
    conninfo: str,
    *,
    function_name: str,
    casts: tuple[str, ...],
    values: tuple[Any, ...],
) -> dict[str, Any]:
    if len(casts) != len(values):
        raise AssertionError("partition recovery SQL arity mismatch")
    placeholders = ",".join(f"%s::{cast}" for cast in casts)
    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {PARTITION_RECOVERY_SCHEMA}.{function_name}(
                  {placeholders}
                )
                """,
                values,
            )
            row = cursor.fetchone()
            description = cursor.description
    if row is None or description is None:
        raise UpdateQueueError(
            f"partition recovery {function_name} returned no receipt"
        )
    columns = [
        str(getattr(column, "name", column[0])) for column in description
    ]
    if len(columns) != len(row) or len(set(columns)) != len(columns):
        raise UpdateQueueError(
            f"partition recovery {function_name} returned invalid columns"
        )
    return {
        key: _normalize(value)
        for key, value in zip(columns, row, strict=True)
    }


def admit_partition_recovery(
    conninfo: str,
    *,
    evidence: Mapping[str, Any],
    actor_kind: str,
    admitted_at: str | None = None,
) -> dict[str, Any]:
    """Admit one verified, immutable zero-call partition preimage."""

    verified = verify_partition_recovery_evidence(evidence)
    actor = _recovery_canonical_text(actor_kind, "actor_kind")
    admission_time = _timestamp(admitted_at or utc_now(), "admitted_at")
    payload = verified["payload"]
    payload_bytes = canonical_json_bytes(payload)
    persisted = _partition_function_result(
        conninfo,
        function_name="admit_partition_recovery",
        casts=(
            "uuid",
            f"{OPS_SCHEMA}.sha256_hex",
            "jsonb",
            "bytea",
            "text",
            "timestamptz",
        ),
        values=(
            verified["admission_id"],
            verified["admission_payload_sha256"],
            json_text(payload),
            payload_bytes,
            actor,
            _iso(admission_time),
        ),
    )
    if (
        "admission_id" in persisted
        and str(persisted["admission_id"]) != verified["admission_id"]
    ):
        raise UpdateQueueError(
            "partition recovery admission returned a different admission ID"
        )
    return {
        "schema": PARTITION_RECOVERY_ADMISSION_RECEIPT_SCHEMA,
        **{
            key: value
            for key, value in verified.items()
            if key != "payload"
        },
        "admission_payload_byte_count": len(payload_bytes),
        "database": persisted,
    }


def verify_partition_recovery_admission(
    conninfo: str,
    *,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare one typed payload with the live immutable generation-1 chain."""

    verified = verify_partition_recovery_evidence(evidence)
    persisted = _partition_function_result(
        conninfo,
        function_name="verify_partition_recovery_admission",
        casts=("jsonb",),
        values=(json_text(verified["payload"]),),
    )
    if (
        "work_item_id" in persisted
        and str(persisted["work_item_id"]) != verified["work_item_id"]
    ):
        raise UpdateQueueError(
            "database verification returned a different work item"
        )
    if "verified" in persisted and persisted["verified"] is not True:
        raise UpdateQueueError(
            "database did not verify the partition recovery admission"
        )
    return {
        **verified,
        "database": persisted,
    }


def authorize_partition_recovery(
    conninfo: str,
    *,
    admission_id: str,
    work_item_id: str,
    generation: int,
    admission_payload_sha256: str,
    expires_at: str,
    actor_kind: str,
    authorized_at: str | None = None,
    dispatch_contract_version: str = PARTITION_RECOVERY_DISPATCH_CONTRACT,
) -> dict[str, Any]:
    """Create one expiring operator authorization for exact generation 2."""

    admission_id = _recovery_canonical_uuid(admission_id, "admission_id")
    work_item_id = _recovery_canonical_uuid(work_item_id, "work_item_id")
    if generation != 2:
        raise UpdateQueueError(
            "partition recovery authorization requires exact generation 2"
        )
    admission_payload_sha256 = _recovery_sha256(
        admission_payload_sha256, "admission_payload_sha256"
    )
    if dispatch_contract_version != PARTITION_RECOVERY_DISPATCH_CONTRACT:
        raise UpdateQueueError("dispatch contract version is invalid")
    actor = _recovery_canonical_text(actor_kind, "actor_kind")
    authorization_time = _timestamp(
        authorized_at or utc_now(), "authorized_at"
    )
    expiry = _timestamp(expires_at, "expires_at")
    if expiry <= authorization_time:
        raise UpdateQueueError("authorization expiry must be in the future")
    material = {
        "admission_id": admission_id,
        "work_item_id": work_item_id,
        "prior_generation": 1,
        "generation": generation,
        "admission_payload_sha256": admission_payload_sha256,
        "dispatch_contract_version": dispatch_contract_version,
        "expires_at": _iso(expiry),
        "actor_kind": actor,
    }
    material_sha256 = sha256_bytes(canonical_json_bytes(material))
    authorization_id = _deterministic_uuid(
        "partition-recovery-authorization", material_sha256
    )
    initial_transition_id = _deterministic_uuid(
        "partition-recovery-transition",
        authorization_id,
        str(generation),
        "1",
        "retry_pending",
    )
    persisted = _partition_function_result(
        conninfo,
        function_name="authorize_partition_recovery",
        casts=(
            "uuid",
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "integer",
            "text",
            "timestamptz",
            "text",
            "timestamptz",
        ),
        values=(
            authorization_id,
            initial_transition_id,
            admission_id,
            work_item_id,
            1,
            generation,
            dispatch_contract_version,
            _iso(expiry),
            actor,
            _iso(authorization_time),
        ),
    )
    if (
        "authorization_id" in persisted
        and str(persisted["authorization_id"]) != authorization_id
    ):
        raise UpdateQueueError(
            "partition recovery authorizer returned a different ID"
        )
    return {
        "schema": PARTITION_RECOVERY_AUTHORIZATION_RECEIPT_SCHEMA,
        "authorization_id": authorization_id,
        "initial_transition_id": initial_transition_id,
        **material,
        "authorization_payload_sha256": material_sha256,
        "database": persisted,
    }


def show_partition_recovery(
    conninfo: str,
    *,
    admission_id: str | None = None,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """Show one exact admission or authorization; no latest/next lookup."""

    if (admission_id is None) == (authorization_id is None):
        raise UpdateQueueError(
            "show requires exactly one admission_id or authorization_id"
        )
    if admission_id is not None:
        admission_id = _recovery_canonical_uuid(
            admission_id, "admission_id"
        )
    if authorization_id is not None:
        authorization_id = _recovery_canonical_uuid(
            authorization_id, "authorization_id"
        )
    persisted = _partition_function_result(
        conninfo,
        function_name="show_partition_recovery",
        casts=("uuid", "uuid"),
        values=(admission_id, authorization_id),
    )
    return {
        "schema": PARTITION_RECOVERY_STATUS_SCHEMA,
        "selector": {
            "admission_id": admission_id,
            "authorization_id": authorization_id,
        },
        "database": persisted,
    }


def revoke_partition_recovery(
    conninfo: str,
    *,
    authorization_id: str,
    reason: str,
    actor_kind: str,
    revoked_at: str | None = None,
) -> dict[str, Any]:
    """Revoke one exact unconsumed authorization without deleting evidence."""

    authorization_id = _recovery_canonical_uuid(
        authorization_id, "authorization_id"
    )
    sanitized_reason = _sanitize_attempt_evidence(reason)
    if (
        not isinstance(sanitized_reason, str)
        or not sanitized_reason.strip()
        or sanitized_reason != sanitized_reason.strip()
    ):
        raise UpdateQueueError(
            "revocation reason must be canonical non-empty text"
        )
    actor = _recovery_canonical_text(actor_kind, "actor_kind")
    revoke_time = _timestamp(revoked_at or utc_now(), "revoked_at")
    persisted = _partition_function_result(
        conninfo,
        function_name="revoke_partition_recovery",
        casts=("uuid", "text", "text", "timestamptz"),
        values=(
            authorization_id,
            sanitized_reason,
            actor,
            _iso(revoke_time),
        ),
    )
    return {
        "schema": PARTITION_RECOVERY_AUTHORIZATION_RECEIPT_SCHEMA,
        "authorization_id": authorization_id,
        "state": "revoked",
        "reason": sanitized_reason,
        "actor_kind": actor,
        "revoked_at": _iso(revoke_time),
        "database": persisted,
    }


def consume_partition_recovery_dispatch(
    conninfo: str,
    *,
    work_item_id: str,
    generation: int,
    authorization_id: str,
    admission_id: str,
    admission_payload_sha256: str,
    sealed_packet_manifest_sha256: str,
    suitability_v2_receipt_sha256: str,
    job_fingerprint: str,
    prompt_sha256: str,
    route_policy_sha256: str,
    owner_key: str,
    max_runtime_seconds: int,
    consumed_at: str | None = None,
    dispatch_contract_version: str = PARTITION_RECOVERY_DISPATCH_CONTRACT,
) -> dict[str, Any]:
    """Consume one exact authorization and bind its real recovery job lease."""

    work_item_id = _recovery_canonical_uuid(work_item_id, "work_item_id")
    authorization_id = _recovery_canonical_uuid(
        authorization_id, "authorization_id"
    )
    admission_id = _recovery_canonical_uuid(admission_id, "admission_id")
    if generation != 2:
        raise UpdateQueueError(
            "partition recovery dispatch requires exact generation 2"
        )
    if dispatch_contract_version != PARTITION_RECOVERY_DISPATCH_CONTRACT:
        raise UpdateQueueError("dispatch contract version is invalid")
    owner_key = _recovery_canonical_text(owner_key, "owner_key")
    if (
        isinstance(max_runtime_seconds, bool)
        or not isinstance(max_runtime_seconds, int)
        or not 1 <= max_runtime_seconds <= 21_600
    ):
        raise UpdateQueueError(
            "max_runtime_seconds must be an integer from 1 through 21600"
        )
    expected_hashes = {
        "admission_payload_sha256": _recovery_sha256(
            admission_payload_sha256, "admission_payload_sha256"
        ),
        "sealed_packet_manifest_sha256": _recovery_sha256(
            sealed_packet_manifest_sha256,
            "sealed_packet_manifest_sha256",
        ),
        "suitability_v2_receipt_sha256": _recovery_sha256(
            suitability_v2_receipt_sha256,
            "suitability_v2_receipt_sha256",
        ),
        "job_fingerprint": _recovery_sha256(
            job_fingerprint, "job_fingerprint"
        ),
        "prompt_sha256": _recovery_sha256(
            prompt_sha256, "prompt_sha256"
        ),
        "route_policy_sha256": _recovery_sha256(
            route_policy_sha256, "route_policy_sha256"
        ),
    }
    claim_material = {
        "work_item_id": work_item_id,
        "generation": generation,
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "dispatch_contract_version": dispatch_contract_version,
        **expected_hashes,
    }
    dispatch_claim_id = _deterministic_uuid(
        "partition-recovery-dispatch-claim",
        sha256_bytes(canonical_json_bytes(claim_material)),
    )
    consume_time = _timestamp(consumed_at or utc_now(), "consumed_at")
    recovery_job_id = _deterministic_uuid(
        "partition-recovery-update-job", dispatch_claim_id
    )
    lease_id = _deterministic_uuid(
        "partition-recovery-job-lease",
        recovery_job_id,
        owner_key,
        str(max_runtime_seconds),
    )
    lease_expires_at = consume_time + timedelta(
        seconds=max_runtime_seconds
    )
    persisted = _partition_function_result(
        conninfo,
        function_name="consume_partition_recovery_dispatch",
        casts=(
            "uuid",
            "uuid",
            "integer",
            "uuid",
            "uuid",
            "text",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            "uuid",
            "uuid",
            "text",
            "integer",
            "timestamptz",
            "timestamptz",
        ),
        values=(
            dispatch_claim_id,
            work_item_id,
            generation,
            authorization_id,
            admission_id,
            dispatch_contract_version,
            expected_hashes["admission_payload_sha256"],
            expected_hashes["sealed_packet_manifest_sha256"],
            expected_hashes["suitability_v2_receipt_sha256"],
            expected_hashes["job_fingerprint"],
            expected_hashes["prompt_sha256"],
            expected_hashes["route_policy_sha256"],
            recovery_job_id,
            lease_id,
            owner_key,
            max_runtime_seconds,
            _iso(lease_expires_at),
            _iso(consume_time),
        ),
    )
    required_receipt_fields = {
        "source_job_id",
        "lease_id",
        "owner_key",
        "max_runtime_seconds",
        "lease_expires_at",
        "replayed",
        "generation_state",
        "open_reservation_id",
        "open_route_ordinal",
        "open_attempt_namespace",
        "finished_route_count",
        "finished_route_statuses",
        "terminal_state",
        "terminal_receipt_id",
    }
    missing_receipt_fields = required_receipt_fields - set(persisted)
    if missing_receipt_fields:
        raise UpdateQueueError(
            "dispatch consume receipt is missing reconciliation fields: "
            + ", ".join(sorted(missing_receipt_fields))
        )
    for field, expected in (
        ("claim_id", dispatch_claim_id),
        ("source_job_id", recovery_job_id),
        ("lease_id", lease_id),
    ):
        if field in persisted and str(persisted[field]) != expected:
            raise UpdateQueueError(
                f"dispatch consume returned a different {field}"
            )
    if (
        "owner_key" in persisted
        and str(persisted["owner_key"]) != owner_key
    ):
        raise UpdateQueueError(
            "dispatch consume returned a different owner_key"
        )
    if (
        "max_runtime_seconds" in persisted
        and int(persisted["max_runtime_seconds"]) != max_runtime_seconds
    ):
        raise UpdateQueueError(
            "dispatch consume returned a different max_runtime_seconds"
        )
    replayed = persisted.get("replayed", False)
    if not isinstance(replayed, bool):
        raise UpdateQueueError("database.replayed must be boolean")
    output_namespace = partition_recovery_output_namespace(
        work_item_id=work_item_id,
        generation=generation,
        job_fingerprint=expected_hashes["job_fingerprint"],
    )
    persisted_lease_expiry = persisted.get(
        "lease_expires_at", _iso(lease_expires_at)
    )
    effective_lease_expiry = _timestamp(
        persisted_lease_expiry, "database.lease_expires_at"
    )
    if not replayed and _iso(effective_lease_expiry) != _iso(
        lease_expires_at
    ):
        raise UpdateQueueError(
            "dispatch consume returned a different lease expiry"
        )
    open_reservation = None
    if persisted.get("open_reservation_id") is not None:
        open_reservation = {
            "reservation_id": _recovery_canonical_uuid(
                persisted["open_reservation_id"],
                "database.open_reservation_id",
            ),
            "route_ordinal": _recovery_positive_int(
                persisted.get("open_route_ordinal"),
                "database.open_route_ordinal",
            ),
            "attempt_namespace": _recovery_canonical_text(
                persisted.get("open_attempt_namespace"),
                "database.open_attempt_namespace",
            ),
            "recovery_job_id": recovery_job_id,
            "lease_id": lease_id,
            "owner_key": owner_key,
        }
        if open_reservation["route_ordinal"] not in _PARTITION_ROUTE_BY_ORDINAL:
            raise UpdateQueueError(
                "database.open_route_ordinal must be 1 or 2"
            )
        if open_reservation["attempt_namespace"] != output_namespace:
            raise UpdateQueueError(
                "open reservation namespace conflicts with dispatch"
            )
    elif any(
        persisted.get(field) is not None
        for field in ("open_route_ordinal", "open_attempt_namespace")
    ):
        raise UpdateQueueError(
            "dispatch consume returned a partial open reservation tuple"
        )
    generation_state = persisted.get("generation_state")
    generation_state = _recovery_canonical_text(
        generation_state, "database.generation_state"
    )
    if generation_state not in {
        "retry_pending",
        "proposal_running",
        "staged_needs_review",
        "failed_terminal",
    }:
        raise UpdateQueueError(
            "database.generation_state is invalid"
        )
    finished_route_count = _recovery_nonnegative_int(
        persisted.get("finished_route_count", 0),
        "database.finished_route_count",
    )
    if finished_route_count > 2:
        raise UpdateQueueError(
            "database.finished_route_count must not exceed two"
        )
    finished_route_statuses = persisted.get("finished_route_statuses", [])
    if not isinstance(finished_route_statuses, list):
        raise UpdateQueueError(
            "database.finished_route_statuses must be an array"
        )
    if len(finished_route_statuses) != finished_route_count:
        raise UpdateQueueError(
            "database finished route count and statuses disagree"
        )
    seen_finished_ordinals: set[int] = set()
    for index, raw_status in enumerate(finished_route_statuses):
        status_row = _recovery_exact_fields(
            raw_status,
            {
                "reservation_id",
                "route_ordinal",
                "route",
                "status",
                "failure_class",
            },
            f"database.finished_route_statuses[{index}]",
        )
        _recovery_canonical_uuid(
            status_row["reservation_id"],
            f"database.finished_route_statuses[{index}].reservation_id",
        )
        ordinal = _recovery_positive_int(
            status_row["route_ordinal"],
            f"database.finished_route_statuses[{index}].route_ordinal",
        )
        if (
            ordinal not in _PARTITION_ROUTE_BY_ORDINAL
            or ordinal in seen_finished_ordinals
            or status_row["route"] != _PARTITION_ROUTE_BY_ORDINAL[ordinal]
        ):
            raise UpdateQueueError(
                "database finished route identity is invalid"
            )
        seen_finished_ordinals.add(ordinal)
        status = status_row["status"]
        failure_class = status_row["failure_class"]
        if status == "succeeded":
            if failure_class is not None:
                raise UpdateQueueError(
                    "succeeded database route cannot carry a failure class"
                )
        elif status == "failed":
            if failure_class not in _PARTITION_ROUTE_FAILURE_CLASSES:
                raise UpdateQueueError(
                    "failed database route has a non-allowlisted class"
                )
        elif status == "execution_unknown":
            if failure_class != "execution_unknown":
                raise UpdateQueueError(
                    "execution_unknown database route is not typed"
                )
        else:
            raise UpdateQueueError(
                "database finished route status is invalid"
            )
    terminal_receipt_id = persisted.get("terminal_receipt_id")
    if terminal_receipt_id is not None:
        terminal_receipt_id = _recovery_canonical_uuid(
            terminal_receipt_id, "database.terminal_receipt_id"
        )
    terminal_state = persisted.get("terminal_state")
    if (terminal_state is None) != (terminal_receipt_id is None):
        raise UpdateQueueError(
            "database terminal state and receipt must appear together"
        )
    if terminal_state is not None:
        terminal_state = _recovery_canonical_text(
            terminal_state, "database.terminal_state"
        )
        if terminal_state not in {
            "staged_needs_review",
            "failed_terminal",
        }:
            raise UpdateQueueError("database.terminal_state is invalid")
    return {
        "schema": PARTITION_RECOVERY_DISPATCH_RECEIPT_SCHEMA,
        "dispatch_claim_id": dispatch_claim_id,
        **claim_material,
        "recovery_job_id": recovery_job_id,
        "source_job_id": recovery_job_id,
        "lease_id": lease_id,
        "owner_key": owner_key,
        "max_runtime_seconds": max_runtime_seconds,
        "lease_expires_at": _iso(effective_lease_expiry),
        "output_namespace": output_namespace,
        "replayed": replayed,
        "generation_state": generation_state,
        "open_reservation": open_reservation,
        "finished_route_count": finished_route_count,
        "finished_route_statuses": finished_route_statuses,
        "terminal_state": terminal_state,
        "terminal_receipt_id": terminal_receipt_id,
        "database": persisted,
    }


def reserve_partition_recovery_route(
    conninfo: str,
    *,
    dispatch_claim_id: str,
    work_item_id: str,
    generation: int,
    authorization_id: str,
    admission_id: str,
    route_ordinal: int,
    packet_sha256: str,
    prompt_sha256: str,
    recovery_job_id: str,
    lease_id: str,
    owner_key: str,
    runtime_id: str,
    provider: str,
    model: str,
    controller_commit_sha256: str,
    job_fingerprint: str,
    reserved_at: str | None = None,
) -> dict[str, Any]:
    """Durably reserve primary/fallback before any external worker call."""

    dispatch_claim_id = _recovery_canonical_uuid(
        dispatch_claim_id, "dispatch_claim_id"
    )
    work_item_id = _recovery_canonical_uuid(work_item_id, "work_item_id")
    authorization_id = _recovery_canonical_uuid(
        authorization_id, "authorization_id"
    )
    admission_id = _recovery_canonical_uuid(admission_id, "admission_id")
    if generation != 2:
        raise UpdateQueueError(
            "partition recovery route requires exact generation 2"
        )
    if route_ordinal not in _PARTITION_ROUTE_BY_ORDINAL:
        raise UpdateQueueError("route_ordinal must be 1 or 2")
    packet_sha256 = _recovery_sha256(packet_sha256, "packet_sha256")
    prompt_sha256 = _recovery_sha256(prompt_sha256, "prompt_sha256")
    controller_commit_sha256 = _recovery_sha256(
        controller_commit_sha256, "controller_commit_sha256"
    )
    job_fingerprint = _recovery_sha256(
        job_fingerprint, "job_fingerprint"
    )
    recovery_job_id = _recovery_canonical_uuid(
        recovery_job_id, "recovery_job_id"
    )
    expected_recovery_job_id = _deterministic_uuid(
        "partition-recovery-update-job", dispatch_claim_id
    )
    if recovery_job_id != expected_recovery_job_id:
        raise UpdateQueueError(
            "recovery_job_id is not bound to the dispatch claim"
        )
    lease_id = _recovery_canonical_uuid(
        lease_id, "lease_id"
    )
    owner_key = _recovery_canonical_text(owner_key, "owner_key")
    runtime_id = _recovery_canonical_text(runtime_id, "runtime_id")
    provider = _recovery_canonical_text(provider, "provider")
    model = _recovery_canonical_text(model, "model")
    attempt_namespace = partition_recovery_output_namespace(
        work_item_id=work_item_id,
        generation=generation,
        job_fingerprint=job_fingerprint,
    )
    reservation_material = {
        "dispatch_claim_id": dispatch_claim_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "route_ordinal": route_ordinal,
        "packet_sha256": packet_sha256,
        "prompt_sha256": prompt_sha256,
        "attempt_namespace": attempt_namespace,
        "recovery_job_id": recovery_job_id,
        "lease_id": lease_id,
        "owner_key": owner_key,
        "runtime_id": runtime_id,
        "provider": provider,
        "model": model,
        "controller_commit_sha256": controller_commit_sha256,
    }
    reservation_id = _deterministic_uuid(
        "partition-recovery-route-reservation",
        sha256_bytes(canonical_json_bytes(reservation_material)),
    )
    generation_bound_attempt_id = _deterministic_uuid(
        "partition-recovery-worker-attempt", reservation_id
    )
    reservation_time = _timestamp(reserved_at or utc_now(), "reserved_at")
    persisted = _partition_function_result(
        conninfo,
        function_name="reserve_partition_recovery_route",
        casts=(
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "uuid",
            "uuid",
            "smallint",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            "text",
            "uuid",
            "uuid",
            "text",
            "text",
            "text",
            "text",
            f"{OPS_SCHEMA}.sha256_hex",
            "timestamptz",
        ),
        values=(
            reservation_id,
            dispatch_claim_id,
            work_item_id,
            generation,
            authorization_id,
            admission_id,
            route_ordinal,
            packet_sha256,
            prompt_sha256,
            attempt_namespace,
            recovery_job_id,
            lease_id,
            owner_key,
            runtime_id,
            provider,
            model,
            controller_commit_sha256,
            _iso(reservation_time),
        ),
    )
    for field, expected in (
        ("reservation_id", reservation_id),
        ("source_job_id", recovery_job_id),
        ("lease_id", lease_id),
    ):
        if field in persisted and str(persisted[field]) != expected:
            raise UpdateQueueError(
                f"route reservation returned a different {field}"
            )
    if (
        "owner_key" in persisted
        and persisted["owner_key"] != owner_key
    ):
        raise UpdateQueueError(
            "route reservation returned a different owner_key"
        )
    replayed = persisted.get("replayed", False)
    if not isinstance(replayed, bool):
        raise UpdateQueueError(
            "route reservation replayed receipt must be boolean"
        )
    return {
        "schema": PARTITION_RECOVERY_ROUTE_RESERVATION_SCHEMA,
        "reservation_id": reservation_id,
        "generation_bound_attempt_id": generation_bound_attempt_id,
        "attempt_namespace": attempt_namespace,
        "route": _PARTITION_ROUTE_BY_ORDINAL[route_ordinal],
        **reservation_material,
        "source_job_id": recovery_job_id,
        "output_namespace": attempt_namespace,
        "replayed": replayed,
        "database": persisted,
    }


def finish_partition_recovery_route(
    conninfo: str,
    *,
    reservation_id: str,
    dispatch_claim_id: str,
    work_item_id: str,
    generation: int,
    authorization_id: str,
    admission_id: str,
    route_ordinal: int,
    status: str,
    result_receipt_sha256: str,
    evidence: Mapping[str, Any],
    attempt_namespace: str,
    job_fingerprint: str,
    recovery_job_id: str,
    lease_id: str,
    owner_key: str,
    failure_class: str | None = None,
    worker_attempt_id: str | None = None,
    stdout_sha256: str | None = None,
    stderr_sha256: str | None = None,
    output_sha256: str | None = None,
    process_exit_code: int | None = None,
    timed_out: bool = False,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Finish one reserved route, preserving ambiguous execution as terminal."""

    reservation_id = _recovery_canonical_uuid(
        reservation_id, "reservation_id"
    )
    dispatch_claim_id = _recovery_canonical_uuid(
        dispatch_claim_id, "dispatch_claim_id"
    )
    work_item_id = _recovery_canonical_uuid(work_item_id, "work_item_id")
    authorization_id = _recovery_canonical_uuid(
        authorization_id, "authorization_id"
    )
    admission_id = _recovery_canonical_uuid(admission_id, "admission_id")
    recovery_job_id = _recovery_canonical_uuid(
        recovery_job_id, "recovery_job_id"
    )
    expected_recovery_job_id = _deterministic_uuid(
        "partition-recovery-update-job", dispatch_claim_id
    )
    if recovery_job_id != expected_recovery_job_id:
        raise UpdateQueueError(
            "recovery_job_id is not bound to the dispatch claim"
        )
    lease_id = _recovery_canonical_uuid(lease_id, "lease_id")
    owner_key = _recovery_canonical_text(owner_key, "owner_key")
    if generation != 2:
        raise UpdateQueueError(
            "partition recovery route result requires exact generation 2"
        )
    if route_ordinal not in _PARTITION_ROUTE_BY_ORDINAL:
        raise UpdateQueueError("route_ordinal must be 1 or 2")
    job_fingerprint = _recovery_sha256(
        job_fingerprint, "job_fingerprint"
    )
    expected_attempt_namespace = partition_recovery_output_namespace(
        work_item_id=work_item_id,
        generation=generation,
        job_fingerprint=job_fingerprint,
    )
    if attempt_namespace != expected_attempt_namespace:
        raise UpdateQueueError(
            "worker attempt namespace is not bound to the exact generation"
        )
    if status not in _PARTITION_ROUTE_RESULT_STATUSES:
        raise UpdateQueueError("partition recovery route status is invalid")
    if status == "failed":
        if failure_class not in _PARTITION_ROUTE_FAILURE_CLASSES:
            raise UpdateQueueError(
                "failed route requires an allowlisted failure class"
            )
    elif failure_class is not None:
        raise UpdateQueueError(
            "only a failed route may carry a failure class"
        )
    if status == "execution_unknown" and worker_attempt_id is not None:
        raise UpdateQueueError(
            "execution_unknown cannot claim a worker attempt"
        )
    expected_worker_attempt_id = _deterministic_uuid(
        "partition-recovery-worker-attempt", reservation_id
    )
    if worker_attempt_id is not None:
        worker_attempt_id = _recovery_canonical_uuid(
            worker_attempt_id, "worker_attempt_id"
        )
        if worker_attempt_id != expected_worker_attempt_id:
            raise UpdateQueueError(
                "worker_attempt_id must be generation-bound to the reservation"
            )
    if status in {"succeeded", "failed"} and worker_attempt_id is None:
        raise UpdateQueueError(
            "known worker execution requires its generation-bound attempt ID"
        )
    hashes: dict[str, str | None] = {}
    for label, value in (
        ("stdout_sha256", stdout_sha256),
        ("stderr_sha256", stderr_sha256),
        ("output_sha256", output_sha256),
    ):
        hashes[label] = (
            None if value is None else _recovery_sha256(value, label)
        )
    result_receipt_sha256 = _recovery_sha256(
        result_receipt_sha256, "result_receipt_sha256"
    )
    if process_exit_code is not None and (
        isinstance(process_exit_code, bool)
        or not isinstance(process_exit_code, int)
    ):
        raise UpdateQueueError("process_exit_code must be an integer or null")
    if not isinstance(timed_out, bool):
        raise UpdateQueueError("timed_out must be boolean")
    completion_time = _timestamp(completed_at or utc_now(), "completed_at")
    _reject_floats(evidence, "route result evidence")
    sanitized_evidence = _sanitize_attempt_evidence(evidence)
    if not isinstance(sanitized_evidence, dict) or not sanitized_evidence:
        raise UpdateQueueError(
            "route result evidence must be a non-empty JSON object"
        )
    if status != "execution_unknown":
        receipt = _recovery_exact_fields(
            sanitized_evidence,
            {
                "lease_id",
                "owner_key",
                "started_at",
                "completed_at",
                "raw_worker_attempt_id",
                "attempt_namespace",
            },
            "route result evidence",
        )
        if (
            _recovery_canonical_uuid(
                receipt["lease_id"], "evidence.lease_id"
            )
            != lease_id
        ):
            raise UpdateQueueError(
                "route receipt lease_id conflicts with dispatch"
            )
        if (
            _recovery_canonical_text(
                receipt["owner_key"], "evidence.owner_key"
            )
            != owner_key
        ):
            raise UpdateQueueError(
                "route receipt owner_key conflicts with dispatch"
            )
        started_at = _timestamp(receipt["started_at"], "evidence.started_at")
        receipt_completed_at = _timestamp(
            receipt["completed_at"], "evidence.completed_at"
        )
        if (
            receipt_completed_at != completion_time
            or receipt_completed_at < started_at
        ):
            raise UpdateQueueError(
                "route receipt timestamps do not bind the exact completion"
            )
        _recovery_sha256(
            receipt["raw_worker_attempt_id"],
            "evidence.raw_worker_attempt_id",
        )
        if receipt["attempt_namespace"] != expected_attempt_namespace:
            raise UpdateQueueError(
                "route receipt attempt namespace conflicts with reservation"
            )
    persisted = _partition_function_result(
        conninfo,
        function_name="finish_partition_recovery_route",
        casts=(
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "uuid",
            "uuid",
            "text",
            "text",
            "uuid",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            f"{OPS_SCHEMA}.sha256_hex",
            "integer",
            "boolean",
            f"{OPS_SCHEMA}.sha256_hex",
            "jsonb",
            "timestamptz",
        ),
        values=(
            reservation_id,
            dispatch_claim_id,
            work_item_id,
            generation,
            authorization_id,
            admission_id,
            status,
            failure_class,
            worker_attempt_id,
            hashes["stdout_sha256"],
            hashes["stderr_sha256"],
            hashes["output_sha256"],
            process_exit_code,
            timed_out,
            result_receipt_sha256,
            json_text(sanitized_evidence),
            _iso(completion_time),
        ),
    )
    required_persisted_fields = {
        "reservation_id",
        "route_ordinal",
        "route",
        "status",
        "failure_class",
        "worker_attempt_id",
        "fallback_eligible",
        "replayed",
    }
    missing_persisted_fields = required_persisted_fields - set(persisted)
    if missing_persisted_fields:
        raise UpdateQueueError(
            "route result database receipt is missing fields: "
            + ", ".join(sorted(missing_persisted_fields))
        )
    persisted_reservation_id = _recovery_canonical_uuid(
        persisted["reservation_id"], "database.reservation_id"
    )
    persisted_route_ordinal = _recovery_positive_int(
        persisted["route_ordinal"], "database.route_ordinal"
    )
    persisted_route = _recovery_canonical_text(
        persisted["route"], "database.route"
    )
    persisted_status = _recovery_canonical_text(
        persisted["status"], "database.status"
    )
    persisted_failure_class = persisted["failure_class"]
    if persisted_failure_class is not None:
        persisted_failure_class = _recovery_canonical_text(
            persisted_failure_class, "database.failure_class"
        )
    persisted_worker_attempt_id = persisted["worker_attempt_id"]
    if persisted_worker_attempt_id is not None:
        persisted_worker_attempt_id = _recovery_canonical_uuid(
            persisted_worker_attempt_id, "database.worker_attempt_id"
        )
    persisted_fallback_eligible = persisted["fallback_eligible"]
    if not isinstance(persisted_fallback_eligible, bool):
        raise UpdateQueueError(
            "database.fallback_eligible must be boolean"
        )
    replayed = persisted.get("replayed", False)
    if not isinstance(replayed, bool):
        raise UpdateQueueError(
            "route result replayed receipt must be boolean"
        )
    expected_route = _PARTITION_ROUTE_BY_ORDINAL[route_ordinal]
    expected_failure_class = (
        "execution_unknown"
        if status == "execution_unknown"
        else failure_class
    )
    expected_fallback_eligible = (
        route_ordinal == 1
        and status == "failed"
        and failure_class in _PARTITION_ROUTE_FAILURE_CLASSES
    )
    if (
        persisted_reservation_id != reservation_id
        or persisted_route_ordinal != route_ordinal
        or persisted_route != expected_route
        or persisted_status != status
        or persisted_failure_class != expected_failure_class
        or persisted_worker_attempt_id != worker_attempt_id
        or persisted_fallback_eligible != expected_fallback_eligible
    ):
        raise UpdateQueueError(
            "route result database receipt conflicts with the exact "
            "reservation or outcome contract"
        )
    for field in (
        "automatic_fallback_allowed",
        "primary_failure_fallback_eligible",
    ):
        if (
            field in persisted
            and persisted[field] is not expected_fallback_eligible
        ):
            raise UpdateQueueError(
                f"database.{field} conflicts with fallback eligibility"
            )
    for field in ("automatic_retry_allowed", "retry_allowed"):
        if field in persisted and persisted[field] is not False:
            raise UpdateQueueError(
                f"database.{field} conflicts with the no-retry contract"
            )
    return {
        "schema": PARTITION_RECOVERY_ROUTE_RESULT_SCHEMA,
        "reservation_id": reservation_id,
        "dispatch_claim_id": dispatch_claim_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "route_ordinal": persisted_route_ordinal,
        "route": persisted_route,
        "status": persisted_status,
        "failure_class": persisted_failure_class,
        "worker_attempt_id": persisted_worker_attempt_id,
        "recovery_job_id": recovery_job_id,
        "source_job_id": recovery_job_id,
        "lease_id": lease_id,
        "owner_key": owner_key,
        **hashes,
        "process_exit_code": process_exit_code,
        "timed_out": timed_out,
        "result_receipt_sha256": result_receipt_sha256,
        "attempt_namespace": attempt_namespace,
        "job_fingerprint": job_fingerprint,
        "completed_at": _iso(completion_time),
        "automatic_retry_allowed": False,
        "automatic_fallback_allowed": persisted_fallback_eligible,
        "primary_failure_fallback_eligible": persisted_fallback_eligible,
        "replayed": replayed,
        "database": persisted,
    }


def close_partition_recovery_generation(
    conninfo: str,
    *,
    dispatch_claim_id: str,
    work_item_id: str,
    generation: int,
    authorization_id: str,
    admission_id: str,
    to_state: str,
    evidence_contract: str,
    evidence_sha256: str,
    evidence: Mapping[str, Any],
    terminal_receipt_id: str,
    source_job_id: str | None = None,
    bundle_receipt_id: str | None = None,
    candidate_proposal_id: str | None = None,
    closed_at: str | None = None,
) -> dict[str, Any]:
    """Close exact generation 2 with typed transition evidence only."""

    dispatch_claim_id = _recovery_canonical_uuid(
        dispatch_claim_id, "dispatch_claim_id"
    )
    work_item_id = _recovery_canonical_uuid(work_item_id, "work_item_id")
    authorization_id = _recovery_canonical_uuid(
        authorization_id, "authorization_id"
    )
    admission_id = _recovery_canonical_uuid(admission_id, "admission_id")
    terminal_receipt_id = _recovery_canonical_uuid(
        terminal_receipt_id, "terminal_receipt_id"
    )
    if generation != 2:
        raise UpdateQueueError(
            "partition recovery closure requires exact generation 2"
        )
    if to_state not in {
        "staged_needs_review",
        "failed_terminal",
    }:
        raise UpdateQueueError(
            "reviewed-suitable partition recovery may close only as "
            "staged_needs_review or failed_terminal"
        )
    evidence_contract = _recovery_canonical_text(
        evidence_contract, "evidence_contract"
    )
    if evidence_contract != PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA:
        raise UpdateQueueError(
            "evidence_contract is not the terminal evidence contract"
        )
    evidence_sha256 = _recovery_sha256(
        evidence_sha256, "evidence_sha256"
    )
    _reject_floats(evidence, "terminal evidence")
    sanitized_evidence = dict(evidence)
    sanitized_evidence = _verify_partition_terminal_evidence(
        sanitized_evidence,
        dispatch_claim_id=dispatch_claim_id,
        work_item_id=work_item_id,
        generation=generation,
        authorization_id=authorization_id,
        admission_id=admission_id,
        to_state=to_state,
    )
    expected_evidence_sha256 = sha256_bytes(
        canonical_json_bytes(sanitized_evidence)
    )
    if evidence_sha256 != expected_evidence_sha256:
        raise UpdateQueueError(
            "evidence_sha256 does not match canonical terminal evidence"
        )
    optional_ids: dict[str, str | None] = {}
    for label, value in (
        ("source_job_id", source_job_id),
        ("bundle_receipt_id", bundle_receipt_id),
        ("candidate_proposal_id", candidate_proposal_id),
    ):
        optional_ids[label] = (
            None if value is None else _recovery_canonical_uuid(value, label)
        )
    if to_state == "staged_needs_review":
        if (
            optional_ids["source_job_id"] is None
            or optional_ids["bundle_receipt_id"] is None
            or optional_ids["candidate_proposal_id"] is None
        ):
            raise UpdateQueueError(
                "staged_needs_review closure requires its recovery job, "
                "bundle receipt, and canonical PG candidate proposal"
            )
    else:
        reason_code = _recovery_canonical_text(
            sanitized_evidence.get("reason_code"),
            "terminal evidence.reason_code",
        )
        if reason_code not in _PARTITION_FAILED_TERMINAL_REASONS:
            raise UpdateQueueError(
                "failed_terminal closure requires an allowlisted "
                "reason_code"
            )
        if (
            optional_ids["bundle_receipt_id"] is not None
            or optional_ids["candidate_proposal_id"] is not None
        ):
            raise UpdateQueueError(
                "failed_terminal closure cannot carry a bundle receipt "
                "or candidate proposal"
            )
        if (
            reason_code in _PARTITION_FAILED_TERMINAL_PRECALL_REASONS
            and optional_ids["source_job_id"] is not None
        ):
            raise UpdateQueueError(
                "pre-call failed_terminal reason cannot claim a source job"
            )
        if (
            reason_code in _PARTITION_FAILED_TERMINAL_EXECUTION_REASONS
            and optional_ids["source_job_id"] is None
        ):
            raise UpdateQueueError(
                "post-reservation failed_terminal reason requires its "
                "recovery source job"
            )
    expected_terminal_receipt_id = _partition_recovery_sha256_uuid(
        "partition-recovery-terminal-receipt",
        work_item_id,
        str(generation),
        to_state,
        evidence_sha256,
    )
    if terminal_receipt_id != expected_terminal_receipt_id:
        raise UpdateQueueError(
            "terminal_receipt_id is not bound to the exact terminal evidence"
        )
    close_time = _timestamp(closed_at or utc_now(), "closed_at")
    transition_material = {
        "dispatch_claim_id": dispatch_claim_id,
        "work_item_id": work_item_id,
        "generation": generation,
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "to_state": to_state,
        "evidence_contract": evidence_contract,
        "evidence_sha256": evidence_sha256,
        "terminal_receipt_id": terminal_receipt_id,
        **optional_ids,
    }
    transition_id = _partition_recovery_sha256_uuid(
        "partition-recovery-transition",
        sha256_bytes(canonical_json_bytes(transition_material)),
    )
    transition_evidence_id = _partition_recovery_sha256_uuid(
        "partition-recovery-transition-evidence",
        transition_id,
        terminal_receipt_id,
        evidence_sha256,
    )
    persisted = _partition_function_result(
        conninfo,
        function_name="close_partition_recovery_generation",
        casts=(
            "uuid",
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "uuid",
            "uuid",
            "text",
            "text",
            f"{OPS_SCHEMA}.sha256_hex",
            "jsonb",
            "uuid",
            "uuid",
            "uuid",
            "timestamptz",
        ),
        values=(
            transition_id,
            transition_evidence_id,
            terminal_receipt_id,
            work_item_id,
            generation,
            authorization_id,
            admission_id,
            to_state,
            evidence_contract,
            evidence_sha256,
            json_text(sanitized_evidence),
            optional_ids["source_job_id"],
            optional_ids["bundle_receipt_id"],
            optional_ids["candidate_proposal_id"],
            _iso(close_time),
        ),
    )
    required_persisted = {
        "transition_id",
        "transition_seq",
        "to_state",
        "terminal_receipt_id",
        "replayed",
        "recorded_at",
        "transition_evidence_id",
        "evidence_sha256",
    }
    if not required_persisted.issubset(persisted):
        raise UpdateQueueError(
            "database returned an incomplete terminal close receipt"
        )
    for field, expected in (
        ("transition_id", transition_id),
        ("to_state", to_state),
        ("terminal_receipt_id", terminal_receipt_id),
    ):
        if str(persisted[field]) != expected:
            raise UpdateQueueError(
                f"database terminal close returned a different {field}"
            )
    transition_seq = persisted["transition_seq"]
    if (
        isinstance(transition_seq, bool)
        or not isinstance(transition_seq, int)
        or transition_seq < 1
    ):
        raise UpdateQueueError(
            "database terminal close transition_seq is invalid"
        )
    replayed = persisted["replayed"]
    if not isinstance(replayed, bool):
        raise UpdateQueueError(
            "database terminal close replayed receipt must be boolean"
        )
    persisted_close_time = _timestamp(
        persisted["recorded_at"],
        "database terminal close recorded_at",
    )
    if str(persisted["transition_evidence_id"]) != transition_evidence_id:
        raise UpdateQueueError(
            "database terminal close returned different evidence identity"
        )
    if persisted["evidence_sha256"] != evidence_sha256:
        raise UpdateQueueError(
            "database terminal close returned a different evidence hash"
        )
    return {
        "schema": RECOVERY_TRANSITION_RECEIPT_SCHEMA,
        "transition_id": str(persisted["transition_id"]),
        "transition_evidence_id": transition_evidence_id,
        **transition_material,
        "transition_seq": transition_seq,
        "replayed": replayed,
        "closed_at": _iso(persisted_close_time),
        "database": persisted,
    }
