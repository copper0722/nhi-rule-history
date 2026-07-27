"""Transactional loader for continuous-update operational/candidate stages.

The loader has no SQL path to canonical legal history.  It revalidates one
sealed source bundle and one immutable worker receipt, writes only the two
stage schemas, and verifies committed identities through a fresh connection.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    sha256_bytes,
    stable_id,
    utc_now,
)
from nhi_rule_history.pg.common import json_text
from nhi_rule_history.update.bundle import BUNDLE_SCHEMA, verify_bundle
from nhi_rule_history.update.proposal import VALIDATED_SCHEMA, validate_proposal
from nhi_rule_history.update.rss import HTTP_PROFILE_ID, parse_rss
from nhi_rule_history.update.workers import (
    WORKER_PROMPT_VERSION,
    WORKER_RUN_SCHEMA,
    source_packet,
)


OPS_SCHEMA = "nhi_rule_history_update_ops"
CANDIDATE_SCHEMA = "nhi_rule_history_candidate_stage"
LOADER_VERSION = "nhi-rule-history-update-pg-stage-loader/1.0.0"
CONTRACT_VERSION = "nhi-rule-history/continuous-update-pg-stage/v1"
SOURCE_SPAN_STATEMENT = (
    "Source-grounded candidate evidence only; no legal-history identity, "
    "adjacency, interval closure, or executable mutation authority."
)
_UUID_NAMESPACE = uuid.UUID("68e98490-472a-58ec-8075-2f18269a1d51")
_MAX_LEASE_SECONDS = 21600


class UpdateStageLoadError(RuntimeError):
    """Sanitized failure while validating, loading, or replaying stage data."""


@dataclass(frozen=True)
class PreparedUpdateLoad:
    job_fingerprint: str
    job_id: str
    lease_id: str
    receipt_id: str
    proposal_id: str
    bundle_id: str
    manifest_sha256: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    expected_tokens: Mapping[str, tuple[str, ...]]
    expected_counts: Mapping[str, int]
    expected_fingerprint: str
    final_state: str


def _deterministic_uuid(label: str, *fingerprints: str) -> str:
    return str(
        uuid.uuid5(_UUID_NAMESPACE, "\x1f".join((label, *fingerprints)))
    )


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise UpdateStageLoadError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateStageLoadError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise UpdateStageLoadError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _relative_path(value: str, label: str) -> str:
    path = Path(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise UpdateStageLoadError(f"{label} must be a safe relative path")
    return path.as_posix()


def _read_canonical_object(path: Path, expected_schema: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateStageLoadError(f"{path.name} is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != expected_schema:
        raise UpdateStageLoadError(f"{path.name} has the wrong schema")
    if payload != canonical_json_bytes(value):
        raise UpdateStageLoadError(f"{path.name} is not canonical immutable JSON")
    return value


def _read_attempts(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        rows = tuple(iter_jsonl(path))
        payload = path.read_bytes()
    except (OSError, ContractError) as exc:
        raise UpdateStageLoadError("attempts.jsonl is invalid") from exc
    if not rows or payload != b"".join(canonical_json_bytes(row) for row in rows):
        raise UpdateStageLoadError("attempts.jsonl is not canonical immutable JSONL")
    return rows


def _validate_attempts(
    receipt: Mapping[str, Any],
    attempts: tuple[dict[str, Any], ...],
    run_dir: Path,
) -> dict[str, Any]:
    if receipt.get("attempt_count") not in {1, 2} or receipt.get(
        "attempt_count"
    ) != len(attempts):
        raise UpdateStageLoadError("worker attempt count is inconsistent")
    primary = attempts[0]
    if primary.get("role") != "primary":
        raise UpdateStageLoadError("first worker attempt is not primary")
    if len(attempts) == 2:
        fallback = attempts[1]
        if (
            primary.get("status") == "validated"
            or fallback.get("role") != "fallback"
            or fallback.get("primary_attempt_id") != primary.get("attempt_id")
            or fallback.get("fallback_reason") != primary.get("status")
        ):
            raise UpdateStageLoadError("fallback is not linked to primary failure")
    selected = next(
        (
            row
            for row in attempts
            if row.get("attempt_id") == receipt.get("selected_attempt_id")
        ),
        None,
    )
    if (
        selected is None
        or selected.get("status") != "validated"
        or selected is not attempts[-1]
        or receipt.get("selected_role") != selected.get("role")
        or sum(row.get("status") == "validated" for row in attempts) != 1
    ):
        raise UpdateStageLoadError("selected worker attempt is not uniquely validated")
    if any(
        row.get("prompt_sha256") != receipt.get("prompt_sha256")
        for row in attempts
    ):
        raise UpdateStageLoadError("worker attempt prompt hashes differ")
    if any(
        row.get("prompt_version") != WORKER_PROMPT_VERSION
        for row in attempts
    ):
        raise UpdateStageLoadError("worker attempt prompt version differs")
    for row in attempts:
        role = str(row["role"])
        for field, stream_name in (
            ("output_sha256", "stdout"),
            ("stderr_sha256", "stderr"),
        ):
            stream_path = run_dir / f"{role}-{stream_name}.bin"
            expected_sha = row.get(field)
            if expected_sha is None:
                if stream_path.exists():
                    raise UpdateStageLoadError(
                        f"{role} {stream_name} bytes lack a recorded hash"
                    )
                continue
            if (
                not stream_path.is_file()
                or file_sha256(stream_path) != expected_sha
            ):
                raise UpdateStageLoadError(
                    f"{role} {stream_name} bytes are missing or changed"
                )
    output_path = run_dir / f"{selected['role']}-output.json"
    stdout_path = run_dir / f"{selected['role']}-stdout.bin"
    if (
        not output_path.is_file()
        or file_sha256(output_path) != selected.get("output_sha256")
        or output_path.read_bytes() != stdout_path.read_bytes()
    ):
        raise UpdateStageLoadError("selected worker output bytes are missing or changed")
    return selected


def _candidate_shape(
    candidate: Mapping[str, Any], *, contains_pdf: bool
) -> dict[str, Any]:
    proposal = candidate["proposal"]
    effects = proposal["effect_candidates"]
    temporal = proposal["temporal_evidence"]
    document_flags = proposal["document_flags"]
    effect_flags = [effect["review_flags"] for effect in effects]
    first_effect = effects[0] if effects else None
    first_temporal = temporal[0] if temporal else None
    multiple = (
        len(effects) > 1
        or any(row["multi_rule"] for row in effect_flags)
        or any(effect["scope_count"] != 1 for effect in effects)
    )
    correction = document_flags["correction_notice"] or any(
        row["correction"] for row in effect_flags
    )
    partial = any(
        row["partial_patch"] or row["omitted_text"] for row in effect_flags
    )
    if multiple:
        replacement_scope = "multiple_clauses"
    elif correction:
        replacement_scope = "correction"
    elif (
        first_effect is not None
        and first_effect["comparison_kind_hint"] == "full_replacement"
        and not partial
    ):
        replacement_scope = "full_single_clause"
    elif first_effect is not None:
        replacement_scope = "partial_patch"
    else:
        replacement_scope = "unresolved"
    if document_flags["odt_pdf_disagreement"] or any(
        row["odt_pdf_disagreement"] for row in effect_flags
    ):
        agreement = "disagree"
    elif contains_pdf and document_flags["odt_pdf_parity_unverified"]:
        agreement = "unresolved"
    elif contains_pdf:
        agreement = "agree"
    else:
        agreement = "not_available"
    if multiple:
        identity = "split_or_merge_possible"
    elif any(row["identity_uncertainty"] for row in effect_flags):
        identity = "ambiguous"
    else:
        identity = "source_designation_only"
    calendar_map = {
        "ROC": "roc",
        "gregorian": "gregorian",
        "unknown": "unresolved",
    }
    role_map = {
        "effective_from": "effective_date",
        "document_date": "document_date",
        "publication_date": "announcement_date",
        "unknown": "unresolved",
    }
    semantic_role = (
        role_map[first_temporal["semantic_role"]]
        if first_temporal
        else "unresolved"
    )
    effective_from = (
        first_temporal["iso_date_candidate"]
        if first_temporal
        and semantic_role == "effective_date"
        and first_temporal["iso_date_candidate"] is not None
        else None
    )
    if semantic_role == "effective_date" and effective_from is None:
        semantic_role = "unresolved"
    designations = [
        effect["designation_raw"]
        for effect in effects
        if effect["designation_raw"]
    ]
    return {
        "source_designation_text": " | ".join(designations) or "(unresolved)",
        "raw_effective_expression": (
            first_temporal["expression_raw"] if first_temporal else None
        ),
        "calendar_system": (
            calendar_map[first_temporal["calendar"]]
            if first_temporal
            else "unresolved"
        ),
        "effective_from": effective_from,
        "date_precision": (
            "unresolved"
            if first_temporal and first_temporal["precision"] == "unknown"
            else first_temporal["precision"] if first_temporal else "unresolved"
        ),
        "date_role": semantic_role,
        "date_scope": (
            "single_clause"
            if len(effects) == 1
            else "document" if effects else "unresolved"
        ),
        "conditionality": (
            "unresolved"
            if first_temporal and first_temporal["conditionality"] == "unknown"
            else first_temporal["conditionality"]
            if first_temporal
            else "unresolved"
        ),
        "replacement_scope": replacement_scope,
        "omitted_text_present": any(
            row["omitted_text"] for row in effect_flags
        ),
        "merged_cells_present": any(
            row["merged_cells"] for row in effect_flags
        ),
        "cross_row_dependency": any(
            row["cross_row_dependency"] for row in effect_flags
        )
        or any(effect["comparison_row_count"] != 1 for effect in effects),
        "multiple_designations_present": multiple,
        "odt_pdf_agreement": agreement,
        "identity_resolution": identity,
        "confidence": 1.0 if candidate["first_lane_shape"] else 0.5,
        "candidate_note": " ".join(candidate["controller_reason_codes"]),
    }


def _source_rows(
    candidate: Mapping[str, Any],
    *,
    proposal_id: str,
    blocks: Mapping[str, Mapping[str, Any]],
    artifact_observed_at: Mapping[str, str],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    proposal = candidate["proposal"]
    span_rows: dict[str, dict[str, Any]] = {}
    evidence_rows: dict[str, dict[str, Any]] = {}

    def add_span(
        source_span: Mapping[str, Any],
        source_role: str,
        evidence_code: str,
        outcome: str,
        details: Mapping[str, Any],
    ) -> None:
        block = blocks.get(str(source_span["block_id"]))
        if block is None:
            raise UpdateStageLoadError("validated span lost its source block")
        span_id = stable_id(
            "nhi-pg-candidate-span",
            proposal_id,
            source_role,
            str(source_span["block_id"]),
            str(source_span["start"]),
            str(source_span["end"]),
            str(source_span["exact_text_sha256"]),
        )
        observed_at = artifact_observed_at.get(
            str(source_span["artifact_sha256"])
        )
        if observed_at is None:
            raise UpdateStageLoadError("candidate span artifact is absent from bundle")
        span_rows.setdefault(
            span_id,
            {
                "proposal_id": proposal_id,
                "span_id": span_id,
                "artifact_sha256": source_span["artifact_sha256"],
                "source_role": source_role,
                "locator": block["locator"],
                "locator_key": str(source_span["block_id"]),
                "char_start": source_span["start"],
                "char_end": source_span["end"],
                "raw_text": source_span["exact_text"],
                "raw_text_sha256": source_span["exact_text_sha256"],
                "raw_text_char_length": len(source_span["exact_text"]),
                "observed_at": observed_at,
                "statement": SOURCE_SPAN_STATEMENT,
            },
        )
        evidence_id = stable_id(
            "nhi-pg-candidate-evidence",
            proposal_id,
            span_id,
            evidence_code,
            outcome,
            sha256_bytes(canonical_json_bytes(details)),
        )
        evidence_rows.setdefault(
            evidence_id,
            {
                "proposal_id": proposal_id,
                "evidence_id": evidence_id,
                "span_id": span_id,
                "evidence_code": evidence_code,
                "outcome": outcome,
                "assertion_text": (
                    f"Validated {source_role} span resolves exactly to "
                    "captured official bytes."
                ),
                "evidence_details": dict(details),
                "validator_version": LOADER_VERSION,
                "recorded_at": observed_at,
            },
        )

    for index, temporal in enumerate(proposal["temporal_evidence"]):
        resolved = (
            temporal["calendar"] != "unknown"
            and temporal["precision"] != "unknown"
            and temporal["semantic_role"] != "unknown"
        )
        add_span(
            temporal["source_span"],
            "effective_expression",
            f"temporal_evidence_{index}",
            "pass" if resolved else "unresolved",
            {
                "calendar": temporal["calendar"],
                "precision": temporal["precision"],
                "semantic_role": temporal["semantic_role"],
                "scope_raw": temporal["scope_raw"],
                "conditionality": temporal["conditionality"],
            },
        )
    for effect_index, effect in enumerate(proposal["effect_candidates"]):
        for side, role in (
            ("old_text_spans", "comparison_old"),
            ("new_text_spans", "comparison_new"),
        ):
            for span_index, source_span in enumerate(effect[side]):
                add_span(
                    source_span,
                    role,
                    f"effect_{effect_index}_{side}_{span_index}",
                    "pass",
                    {
                        "comparison_kind_hint": effect[
                            "comparison_kind_hint"
                        ],
                        "designation_raw": effect["designation_raw"],
                        "source_side": side,
                    },
                )
    if not span_rows or not evidence_rows:
        raise UpdateStageLoadError(
            "candidate has no exact source span and cannot enter PG staging"
        )
    return (
        tuple(span_rows[key] for key in sorted(span_rows)),
        tuple(evidence_rows[key] for key in sorted(evidence_rows)),
    )


def _tokens(
    rows: Mapping[str, tuple[dict[str, Any], ...]]
) -> dict[str, tuple[str, ...]]:
    token_fields = {
        "update_job": (
            "job_id",
            "job_fingerprint",
            "contract_version",
            "runner_version",
            "feed_url",
            "request_profile_sha256",
            "activation_cut",
        ),
        "job_lease": (
            "lease_id",
            "owner_key",
            "max_runtime_seconds",
        ),
        "worker_attempt": (
            "attempt_id",
            "attempt_no",
            "lane",
            "primary_attempt_id",
            "provider",
            "runtime",
            "model",
            "prompt_sha256",
            "output_sha256",
            "status",
            "failure_code",
            "fallback_reason",
        ),
        "content_artifact": (
            "artifact_sha256",
            "byte_size",
            "media_type",
        ),
        "url_observation": (
            "url_observation_id",
            "requested_url",
            "final_url",
            "outcome",
            "http_status",
            "response_headers_sha256",
            "artifact_sha256",
            "error_code",
        ),
        "feed_observation": (
            "feed_observation_id",
            "response_artifact_sha256",
            "parser_version",
            "parse_status",
            "item_count",
            "item_sequence_sha256",
            "parse_error_code",
        ),
        "feed_item_observation": (
            "item_index",
            "item_fingerprint",
            "guid_raw",
            "title_raw",
            "link_raw",
            "published_raw",
            "description_raw",
            "raw_item_sha256",
        ),
        "bundle_receipt": (
            "receipt_id",
            "bundle_uid",
            "manifest_sha256",
            "bundle_relative_path",
            "artifact_count",
            "total_bytes",
            "fsync_verified",
            "receipt_status",
            "rejection_code",
        ),
        "candidate_proposal": (
            "proposal_id",
            "proposal_fingerprint",
            "contract_version",
            "producer_attempt_id",
            "producer_output_sha256",
            "source_designation_text",
            "raw_effective_expression",
            "calendar_system",
            "effective_from",
            "date_precision",
            "date_role",
            "date_scope",
            "conditionality",
            "replacement_scope",
            "omitted_text_present",
            "merged_cells_present",
            "cross_row_dependency",
            "multiple_designations_present",
            "odt_pdf_agreement",
            "identity_resolution",
            "confidence",
            "candidate_note",
        ),
        "candidate_source_span": (
            "span_id",
            "artifact_sha256",
            "source_role",
            "locator_key",
            "char_start",
            "char_end",
            "raw_text_sha256",
            "raw_text_char_length",
            "statement",
        ),
        "candidate_evidence": (
            "evidence_id",
            "span_id",
            "evidence_code",
            "outcome",
            "assertion_text",
            "validator_version",
        ),
        "candidate_state_transition": (
            "transition_id",
            "state",
            "actor_kind",
            "decision_basis_sha256",
        ),
    }
    result: dict[str, tuple[str, ...]] = {}
    for table, fields in token_fields.items():
        result[table] = tuple(
            sorted(
                sha256_bytes(
                    canonical_json_bytes(
                        [row.get(field) for field in fields]
                    )
                )
                for row in rows[table]
            )
        )
    return result


def _prepare_update_load(
    *,
    bundle_path: Path,
    candidate_receipt_path: Path,
    bundle_relative_path: str,
    activation_cut: str | date,
    owner_key: str,
    notification_window_start: str,
    notification_window_end: str,
) -> PreparedUpdateLoad:
    bundle_path = Path(bundle_path)
    candidate_receipt_path = Path(candidate_receipt_path)
    bundle_relative_path = _relative_path(
        bundle_relative_path, "bundle_relative_path"
    )
    if not owner_key.strip():
        raise UpdateStageLoadError("owner_key must be non-empty")
    try:
        activation_date = (
            activation_cut
            if isinstance(activation_cut, date)
            else date.fromisoformat(str(activation_cut))
        )
    except ValueError as exc:
        raise UpdateStageLoadError("activation_cut must be an ISO date") from exc
    window_start = _timestamp(
        notification_window_start, "notification_window_start"
    )
    window_end = _timestamp(
        notification_window_end, "notification_window_end"
    )
    if window_end <= window_start:
        raise UpdateStageLoadError("notification window must be positive")
    try:
        verification = verify_bundle(bundle_path)
        manifest_path = bundle_path / "manifest.json"
        manifest = _read_canonical_object(manifest_path, BUNDLE_SCHEMA)
        receipt = _read_canonical_object(
            candidate_receipt_path, WORKER_RUN_SCHEMA
        )
    except ContractError as exc:
        raise UpdateStageLoadError("source bundle verification failed") from exc
    if (
        receipt.get("status") != "staged"
        or receipt.get("bundle_id") != verification["bundle_id"]
        or receipt.get("bundle_fingerprint")
        != verification["bundle_fingerprint"]
    ):
        raise UpdateStageLoadError("worker receipt does not match the source bundle")
    attempts = _read_attempts(candidate_receipt_path.parent / "attempts.jsonl")
    selected_attempt = _validate_attempts(
        receipt, attempts, candidate_receipt_path.parent
    )
    try:
        packet = source_packet(bundle_path)
    except ContractError as exc:
        raise UpdateStageLoadError("source packet verification failed") from exc
    required_flags = (
        {"odt_pdf_parity_unverified"}
        if packet["controller_facts"]["contains_pdf"]
        else set()
    )
    candidate = receipt.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("schema") != VALIDATED_SCHEMA:
        raise UpdateStageLoadError("worker receipt lacks a validated candidate")
    try:
        selected_output_path = (
            candidate_receipt_path.parent
            / f"{selected_attempt['role']}-output.json"
        )
        selected_proposal = json.loads(
            selected_output_path.read_text(encoding="utf-8")
        )
        if not isinstance(selected_proposal, dict):
            raise ValueError("selected output is not one JSON object")
        revalidated = validate_proposal(
            selected_proposal,
            source_blocks=packet["source_blocks"],
            bundle_id=verification["bundle_id"],
            bundle_fingerprint=verification["bundle_fingerprint"],
            required_true_document_flags=required_flags,
            expected_notice={
                "reference_number_raw": packet["notice_metadata"][
                    "reference_number_raw"
                ],
                "subject_raw": packet["notice_metadata"]["subject_raw"],
            },
        )
    except (KeyError, OSError, UnicodeError, ContractError, ValueError) as exc:
        raise UpdateStageLoadError("worker candidate revalidation failed") from exc
    if canonical_json_bytes(revalidated) != canonical_json_bytes(candidate):
        raise UpdateStageLoadError("worker candidate receipt changed after validation")
    if selected_attempt.get("candidate_id") != candidate.get("candidate_id"):
        raise UpdateStageLoadError("selected attempt candidate identity differs")
    job_fingerprint = str(receipt.get("job_fingerprint", ""))
    if len(job_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in job_fingerprint
    ):
        raise UpdateStageLoadError("job fingerprint is invalid")
    expected_job_fingerprint = stable_id(
        "nhi-worker-job",
        verification["bundle_id"],
        verification["bundle_fingerprint"],
        WORKER_PROMPT_VERSION,
        str(receipt["prompt_sha256"]),
    )
    if job_fingerprint != expected_job_fingerprint:
        raise UpdateStageLoadError("job fingerprint does not match worker inputs")
    job_id = _deterministic_uuid("job", job_fingerprint)
    lease_id = _deterministic_uuid("lease", job_fingerprint)
    receipt_id = _deterministic_uuid(
        "bundle-receipt", job_fingerprint, verification["bundle_fingerprint"]
    )
    proposal_id = _deterministic_uuid(
        "candidate-proposal",
        job_fingerprint,
        str(candidate["proposal_sha256"]),
    )
    manifest_sha = file_sha256(manifest_path)
    sealed_at = _timestamp(manifest["sealed_at"], "manifest.sealed_at")
    loaded_at = _timestamp(utc_now(), "loaded_at")
    resources = manifest["resources"]
    artifact_rows: dict[str, dict[str, Any]] = {
        manifest_sha: {
            "artifact_sha256": manifest_sha,
            "byte_size": manifest_path.stat().st_size,
            "media_type": "application/json",
            "bundle_relative_path": f"{bundle_relative_path}/manifest.json",
            "first_observed_at": _iso(sealed_at),
        }
    }
    artifact_observed: dict[str, str] = {}
    url_rows: list[dict[str, Any]] = []
    feed_resource: Mapping[str, Any] | None = None
    for index, resource in enumerate(resources):
        content_path = _relative_path(
            str(resource["content_path"]), "resource.content_path"
        )
        digest = str(resource["artifact_sha256"])
        row = {
            "artifact_sha256": digest,
            "byte_size": resource["byte_size"],
            "media_type": resource["media_type"],
            "bundle_relative_path": f"{bundle_relative_path}/{content_path}",
            "first_observed_at": resource["observed_at"],
        }
        prior_artifact = artifact_rows.get(digest)
        if prior_artifact is not None and (
            prior_artifact["byte_size"] != row["byte_size"]
            or prior_artifact["media_type"] != row["media_type"]
        ):
            raise UpdateStageLoadError("one artifact hash has conflicting metadata")
        artifact_rows.setdefault(digest, row)
        artifact_observed.setdefault(digest, resource["observed_at"])
        url_rows.append(
            {
                "url_observation_id": _deterministic_uuid(
                    "url-observation",
                    job_fingerprint,
                    str(index),
                    str(resource["request_url"]),
                    digest,
                ),
                "job_id": job_id,
                "lease_id": lease_id,
                "owner_key": owner_key,
                "requested_url": resource["request_url"],
                "final_url": resource["final_url"],
                "observed_at": resource["observed_at"],
                "outcome": "response",
                "http_status": resource["http_status"],
                "response_headers": resource["response_headers"],
                "response_headers_sha256": sha256_bytes(
                    canonical_json_bytes(resource["response_headers"])
                ),
                "artifact_sha256": digest,
                "previous_artifact_sha256": None,
                "relation_to_previous": "not_comparable",
                "error_code": None,
            }
        )
        if resource["relation"] == "rss_feed":
            if feed_resource is not None:
                raise UpdateStageLoadError("bundle repeats the RSS feed resource")
            feed_resource = resource
    if feed_resource is None:
        raise UpdateStageLoadError("bundle has no exact RSS feed resource")
    try:
        feed_items = parse_rss(
            (bundle_path / str(feed_resource["content_path"])).read_bytes()
        )
    except (OSError, ContractError) as exc:
        raise UpdateStageLoadError("captured RSS artifact failed reparse") from exc
    feed_observation_id = _deterministic_uuid(
        "feed-observation",
        job_fingerprint,
        str(feed_resource["artifact_sha256"]),
    )
    feed_item_rows = tuple(
        {
            "feed_observation_id": feed_observation_id,
            "item_index": item.sequence,
            "item_fingerprint": sha256_bytes(
                canonical_json_bytes(item.as_dict())
            ),
            "guid_raw": item.guid,
            "title_raw": item.title,
            "link_raw": item.link,
            "published_raw": item.published_at,
            "description_raw": item.description,
            "raw_item_sha256": sha256_bytes(
                canonical_json_bytes(item.as_dict())
            ),
        }
        for item in feed_items
    )
    feed_url_row = next(
        row
        for row in url_rows
        if row["artifact_sha256"] == feed_resource["artifact_sha256"]
        and row["requested_url"] == feed_resource["request_url"]
    )
    feed_rows = (
        {
            "feed_observation_id": feed_observation_id,
            "job_id": job_id,
            "url_observation_id": feed_url_row["url_observation_id"],
            "response_artifact_sha256": feed_resource["artifact_sha256"],
            "parser_version": "nhi-rule-history-rss-parser/v1",
            "parse_status": "parsed",
            "channel_title_raw": None,
            "item_count": len(feed_items),
            "item_sequence_sha256": sha256_bytes(
                canonical_json_bytes([item.as_dict() for item in feed_items])
            ),
            "parsed_at": feed_resource["observed_at"],
            "parse_error_code": None,
        },
    )
    raw_attempt_id_to_uuid = {
        str(attempt["attempt_id"]): _deterministic_uuid(
            "worker-attempt", job_fingerprint, str(attempt["attempt_id"])
        )
        for attempt in attempts
    }
    attempt_rows: list[dict[str, Any]] = []
    lease_points = [window_start, window_end]
    for number, attempt in enumerate(attempts, 1):
        started = _timestamp(attempt["started_at"], "attempt.started_at")
        completed = _timestamp(attempt["completed_at"], "attempt.completed_at")
        lease_points.extend((started, completed))
        validated = attempt["status"] == "validated"
        failure_code = None
        if not validated:
            failure_code = str(attempt["status"])
            if attempt.get("validation_error_code"):
                failure_code += f":{attempt['validation_error_code']}"
        attempt_rows.append(
            {
                "attempt_id": raw_attempt_id_to_uuid[str(attempt["attempt_id"])],
                "job_id": job_id,
                "lease_id": lease_id,
                "owner_key": owner_key,
                "attempt_no": number,
                "lane": attempt["role"],
                "primary_attempt_id": (
                    raw_attempt_id_to_uuid[str(attempt["primary_attempt_id"])]
                    if attempt.get("primary_attempt_id")
                    else None
                ),
                "provider": attempt["provider"],
                "runtime": attempt["runtime_id"],
                "model": attempt["model"],
                "prompt_sha256": attempt["prompt_sha256"],
                "output_sha256": attempt.get("output_sha256"),
                "started_at": _iso(started),
                "completed_at": _iso(completed),
                "status": "success" if validated else "failed",
                "failure_code": failure_code,
                "fallback_reason": attempt.get("fallback_reason"),
            }
        )
    for resource in resources:
        lease_points.append(
            _timestamp(resource["observed_at"], "resource.observed_at")
        )
    lease_start = min(lease_points)
    lease_end = max(lease_points) + timedelta(seconds=1)
    runtime_seconds = math.ceil((lease_end - lease_start).total_seconds())
    if runtime_seconds > _MAX_LEASE_SECONDS:
        raise UpdateStageLoadError("material exceeds the six-hour lease bound")
    block_map = {
        str(block["block_id"]): block for block in packet["source_blocks"]
    }
    span_rows, evidence_rows = _source_rows(
        candidate,
        proposal_id=proposal_id,
        blocks=block_map,
        artifact_observed_at=artifact_observed,
    )
    shape = _candidate_shape(
        candidate, contains_pdf=packet["controller_facts"]["contains_pdf"]
    )
    proposal_rows = (
        {
            "proposal_id": proposal_id,
            "proposal_fingerprint": candidate["proposal_sha256"],
            "contract_version": candidate["schema"],
            "job_id": job_id,
            "bundle_receipt_id": receipt_id,
            "producer_attempt_id": raw_attempt_id_to_uuid[
                str(selected_attempt["attempt_id"])
            ],
            "producer_output_sha256": selected_attempt["output_sha256"],
            **shape,
        },
    )
    final_state = str(candidate["state"])
    transition_states = (
        ("validated_candidate", "promotion_ready_pending_anchor")
        if final_state == "promotion_ready_pending_anchor"
        else (final_state,)
    )
    allowed_states = {
        "validated_candidate",
        "promotion_ready_pending_anchor",
        "needs_review",
        "rejected",
    }
    if any(state not in allowed_states for state in transition_states):
        raise UpdateStageLoadError("candidate state is outside stage authority")
    transition_rows = tuple(
        {
            "proposal_id": proposal_id,
            "transition_seq": sequence,
            "transition_id": _deterministic_uuid(
                "candidate-transition", proposal_id, str(sequence), state
            ),
            "state": state,
            "actor_kind": (
                "deterministic_validator" if sequence == 1 else "system_gate"
            ),
            "decision_basis_sha256": sha256_bytes(
                canonical_json_bytes(
                    {
                        "state": state,
                        "proposal_sha256": candidate["proposal_sha256"],
                        "controller_reason_codes": candidate[
                            "controller_reason_codes"
                        ],
                    }
                )
            ),
            "recorded_at": selected_attempt["completed_at"],
        }
        for sequence, state in enumerate(transition_states, 1)
    )
    rows: dict[str, tuple[dict[str, Any], ...]] = {
        "update_job": (
            {
                "job_id": job_id,
                "job_fingerprint": job_fingerprint,
                "contract_version": CONTRACT_VERSION,
                "runner_version": LOADER_VERSION,
                "feed_url": feed_resource["request_url"],
                "request_profile_sha256": sha256_bytes(
                    HTTP_PROFILE_ID.encode("utf-8")
                ),
                "notification_window_start": _iso(window_start),
                "notification_window_end": _iso(window_end),
                "activation_cut": activation_date.isoformat(),
                "scheduled_at": _iso(window_end),
            },
        ),
        "job_lease": (
            {
                "lease_id": lease_id,
                "job_id": job_id,
                "owner_key": owner_key,
                "acquired_at": _iso(lease_start),
                "expires_at": _iso(lease_end),
                "max_runtime_seconds": runtime_seconds,
            },
        ),
        "worker_attempt": tuple(attempt_rows),
        "content_artifact": tuple(
            artifact_rows[key] for key in sorted(artifact_rows)
        ),
        "url_observation": tuple(url_rows),
        "feed_observation": feed_rows,
        "feed_item_observation": feed_item_rows,
        "bundle_receipt": (
            {
                "receipt_id": receipt_id,
                "job_id": job_id,
                "bundle_uid": verification["bundle_id"],
                "manifest_sha256": manifest_sha,
                "bundle_relative_path": bundle_relative_path,
                "artifact_count": len(artifact_rows),
                "total_bytes": sum(
                    int(row["byte_size"]) for row in artifact_rows.values()
                ),
                "prepared_at": _iso(sealed_at),
                "atomically_published_at": _iso(sealed_at),
                "pg_received_at": _iso(max(loaded_at, sealed_at)),
                "fsync_verified": True,
                "receipt_status": "received",
                "rejection_code": None,
            },
        ),
        "candidate_proposal": proposal_rows,
        "candidate_source_span": span_rows,
        "candidate_evidence": evidence_rows,
        "candidate_state_transition": transition_rows,
    }
    expected_tokens = _tokens(rows)
    expected_counts = {
        table: len(values) for table, values in expected_tokens.items()
    }
    return PreparedUpdateLoad(
        job_fingerprint=job_fingerprint,
        job_id=job_id,
        lease_id=lease_id,
        receipt_id=receipt_id,
        proposal_id=proposal_id,
        bundle_id=verification["bundle_id"],
        manifest_sha256=manifest_sha,
        rows=rows,
        expected_tokens=expected_tokens,
        expected_counts=expected_counts,
        expected_fingerprint=sha256_bytes(
            canonical_json_bytes(expected_tokens)
        ),
        final_state=final_state,
    )


def _connect(conninfo: str):
    try:
        import psycopg
    except ImportError as exc:
        raise UpdateStageLoadError("psycopg is required for PG staging") from exc
    return psycopg.connect(conninfo)


def _insert_artifact(cursor: Any, row: Mapping[str, Any]) -> None:
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
        raise UpdateStageLoadError("content artifact metadata collision")


def _apply_material(material: PreparedUpdateLoad, conninfo: str) -> bool:
    replayed = False
    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"nhi-rule-history-update-load:{material.job_fingerprint}",),
            )
            cursor.execute(
                f"""
                SELECT job_id::text FROM {OPS_SCHEMA}.update_job
                WHERE job_fingerprint = %s
                """,
                (material.job_fingerprint,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing[0]) != material.job_id:
                    raise UpdateStageLoadError("job fingerprint UUID collision")
                replayed = True
            else:
                _insert_new_material(cursor, material)
    return replayed


def _insert_new_material(cursor: Any, material: PreparedUpdateLoad) -> None:
    job = material.rows["update_job"][0]
    cursor.execute(
        f"""
        INSERT INTO {OPS_SCHEMA}.update_job (
          job_id, job_fingerprint, contract_version, runner_version, feed_url,
          request_profile_sha256, notification_window_start,
          notification_window_end, activation_cut, scheduled_at
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
    for row in material.rows["worker_attempt"]:
        cursor.execute(
            f"""
            INSERT INTO {OPS_SCHEMA}.worker_attempt (
              attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
              primary_attempt_id, provider, runtime, model, prompt_sha256,
              output_sha256, started_at, completed_at, status, failure_code,
              fallback_reason
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            tuple(row.values()),
        )
    for row in material.rows["content_artifact"]:
        _insert_artifact(cursor, row)
    for source in material.rows["url_observation"]:
        cursor.execute(
            f"""
            SELECT artifact_sha256, final_url
            FROM {OPS_SCHEMA}.url_observation
            WHERE requested_url = %s
              AND outcome = 'response'
              AND artifact_sha256 IS NOT NULL
            ORDER BY observed_at DESC, url_observation_id DESC
            LIMIT 1
            """,
            (source["requested_url"],),
        )
        prior = cursor.fetchone()
        row = dict(source)
        if prior is None:
            relation, previous_sha = "first_observation", None
        else:
            previous_sha = str(prior[0])
            if previous_sha == row["artifact_sha256"]:
                relation = "same_bytes"
            elif str(prior[1]) != row["final_url"]:
                relation = "redirect_changed"
            else:
                relation = "same_url_new_bytes"
        row["previous_artifact_sha256"] = previous_sha
        row["relation_to_previous"] = relation
        cursor.execute(
            f"""
            INSERT INTO {OPS_SCHEMA}.url_observation (
              url_observation_id, job_id, lease_id, owner_key, requested_url,
              final_url, observed_at, outcome, http_status, response_headers,
              response_headers_sha256, artifact_sha256,
              previous_artifact_sha256, relation_to_previous, error_code
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s
            )
            """,
            (
                row["url_observation_id"],
                row["job_id"],
                row["lease_id"],
                row["owner_key"],
                row["requested_url"],
                row["final_url"],
                row["observed_at"],
                row["outcome"],
                row["http_status"],
                json_text(row["response_headers"]),
                row["response_headers_sha256"],
                row["artifact_sha256"],
                row["previous_artifact_sha256"],
                row["relation_to_previous"],
                row["error_code"],
            ),
        )
    feed = material.rows["feed_observation"][0]
    cursor.execute(
        f"""
        INSERT INTO {OPS_SCHEMA}.feed_observation (
          feed_observation_id, job_id, url_observation_id,
          response_artifact_sha256, parser_version, parse_status,
          channel_title_raw, item_count, item_sequence_sha256, parsed_at,
          parse_error_code
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        tuple(feed.values()),
    )
    for row in material.rows["feed_item_observation"]:
        cursor.execute(
            f"""
            INSERT INTO {OPS_SCHEMA}.feed_item_observation (
              feed_observation_id, item_index, item_fingerprint, guid_raw,
              title_raw, link_raw, published_raw, description_raw,
              raw_item_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            tuple(row.values()),
        )
    receipt = material.rows["bundle_receipt"][0]
    cursor.execute(
        f"""
        INSERT INTO {OPS_SCHEMA}.bundle_receipt (
          receipt_id, job_id, bundle_uid, manifest_sha256,
          bundle_relative_path, artifact_count, total_bytes, prepared_at,
          atomically_published_at, pg_received_at, fsync_verified,
          receipt_status, rejection_code
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        tuple(receipt.values()),
    )
    proposal = material.rows["candidate_proposal"][0]
    cursor.execute(
        f"""
        INSERT INTO {CANDIDATE_SCHEMA}.candidate_proposal (
          proposal_id, proposal_fingerprint, contract_version, job_id,
          bundle_receipt_id, producer_attempt_id, producer_output_sha256,
          source_designation_text, raw_effective_expression, calendar_system,
          effective_from, date_precision, date_role, date_scope,
          conditionality, replacement_scope, omitted_text_present,
          merged_cells_present, cross_row_dependency,
          multiple_designations_present, odt_pdf_agreement,
          identity_resolution, confidence, candidate_note
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        """,
        tuple(proposal.values()),
    )
    for row in material.rows["candidate_source_span"]:
        cursor.execute(
            f"""
            INSERT INTO {CANDIDATE_SCHEMA}.candidate_source_span (
              proposal_id, span_id, artifact_sha256, source_role, locator,
              locator_key, char_start, char_end, raw_text, raw_text_sha256,
              raw_text_char_length, observed_at, statement
            ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["proposal_id"],
                row["span_id"],
                row["artifact_sha256"],
                row["source_role"],
                json_text(row["locator"]),
                row["locator_key"],
                row["char_start"],
                row["char_end"],
                row["raw_text"],
                row["raw_text_sha256"],
                row["raw_text_char_length"],
                row["observed_at"],
                row["statement"],
            ),
        )
    for row in material.rows["candidate_evidence"]:
        cursor.execute(
            f"""
            INSERT INTO {CANDIDATE_SCHEMA}.candidate_evidence (
              proposal_id, evidence_id, span_id, evidence_code, outcome,
              assertion_text, evidence_details, validator_version, recorded_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
            """,
            (
                row["proposal_id"],
                row["evidence_id"],
                row["span_id"],
                row["evidence_code"],
                row["outcome"],
                row["assertion_text"],
                json_text(row["evidence_details"]),
                row["validator_version"],
                row["recorded_at"],
            ),
        )
    for row in material.rows["candidate_state_transition"]:
        cursor.execute(
            f"""
            INSERT INTO {CANDIDATE_SCHEMA}.candidate_state_transition (
              proposal_id, transition_seq, transition_id, state, actor_kind,
              decision_basis_sha256, recorded_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            tuple(row.values()),
        )


_VERIFY_SQL = {
    "update_job": f"""
        SELECT job_id::text, job_fingerprint, contract_version,
               runner_version, feed_url, request_profile_sha256,
               activation_cut::text
        FROM {OPS_SCHEMA}.update_job WHERE job_id = %s
    """,
    "job_lease": f"""
        SELECT lease_id::text, owner_key, max_runtime_seconds
        FROM {OPS_SCHEMA}.job_lease WHERE job_id = %s
    """,
    "worker_attempt": f"""
        SELECT attempt_id::text, attempt_no, lane,
               primary_attempt_id::text, provider, runtime, model,
               prompt_sha256, output_sha256, status, failure_code,
               fallback_reason
        FROM {OPS_SCHEMA}.worker_attempt WHERE job_id = %s
    """,
    "content_artifact": f"""
        SELECT artifact.artifact_sha256, artifact.byte_size,
               artifact.media_type
        FROM {OPS_SCHEMA}.content_artifact artifact
        JOIN (
          SELECT artifact_sha256
          FROM {OPS_SCHEMA}.url_observation WHERE job_id = %s
          UNION
          SELECT manifest_sha256
          FROM {OPS_SCHEMA}.bundle_receipt WHERE job_id = %s
        ) scoped
          ON scoped.artifact_sha256 = artifact.artifact_sha256
    """,
    "url_observation": f"""
        SELECT url_observation_id::text, requested_url, final_url, outcome,
               http_status, response_headers_sha256, artifact_sha256,
               error_code
        FROM {OPS_SCHEMA}.url_observation WHERE job_id = %s
    """,
    "feed_observation": f"""
        SELECT feed_observation_id::text, response_artifact_sha256,
               parser_version, parse_status, item_count,
               item_sequence_sha256, parse_error_code
        FROM {OPS_SCHEMA}.feed_observation WHERE job_id = %s
    """,
    "feed_item_observation": f"""
        SELECT item.item_index, item.item_fingerprint, item.guid_raw,
               item.title_raw, item.link_raw, item.published_raw,
               item.description_raw, item.raw_item_sha256
        FROM {OPS_SCHEMA}.feed_item_observation item
        JOIN {OPS_SCHEMA}.feed_observation feed
          ON feed.feed_observation_id = item.feed_observation_id
        WHERE feed.job_id = %s
    """,
    "bundle_receipt": f"""
        SELECT receipt_id::text, bundle_uid, manifest_sha256,
               bundle_relative_path, artifact_count, total_bytes,
               fsync_verified, receipt_status, rejection_code
        FROM {OPS_SCHEMA}.bundle_receipt WHERE job_id = %s
    """,
    "candidate_proposal": f"""
        SELECT proposal_id::text, proposal_fingerprint, contract_version,
               producer_attempt_id::text, producer_output_sha256,
               source_designation_text, raw_effective_expression,
               calendar_system, effective_from::text, date_precision,
               date_role, date_scope, conditionality, replacement_scope,
               omitted_text_present, merged_cells_present,
               cross_row_dependency, multiple_designations_present,
               odt_pdf_agreement, identity_resolution, confidence::float8,
               candidate_note
        FROM {CANDIDATE_SCHEMA}.candidate_proposal WHERE job_id = %s
    """,
    "candidate_source_span": f"""
        SELECT span.span_id, span.artifact_sha256, span.source_role,
               span.locator_key, span.char_start, span.char_end,
               span.raw_text_sha256, span.raw_text_char_length, span.statement
        FROM {CANDIDATE_SCHEMA}.candidate_source_span span
        JOIN {CANDIDATE_SCHEMA}.candidate_proposal proposal
          ON proposal.proposal_id = span.proposal_id
        WHERE proposal.job_id = %s
    """,
    "candidate_evidence": f"""
        SELECT evidence.evidence_id, evidence.span_id,
               evidence.evidence_code, evidence.outcome,
               evidence.assertion_text, evidence.validator_version
        FROM {CANDIDATE_SCHEMA}.candidate_evidence evidence
        JOIN {CANDIDATE_SCHEMA}.candidate_proposal proposal
          ON proposal.proposal_id = evidence.proposal_id
        WHERE proposal.job_id = %s
    """,
    "candidate_state_transition": f"""
        SELECT transition.transition_id::text, transition.state,
               transition.actor_kind, transition.decision_basis_sha256
        FROM {CANDIDATE_SCHEMA}.candidate_state_transition transition
        JOIN {CANDIDATE_SCHEMA}.candidate_proposal proposal
          ON proposal.proposal_id = transition.proposal_id
        WHERE proposal.job_id = %s
    """,
}


def _verify_loaded(
    material: PreparedUpdateLoad, conninfo: str
) -> dict[str, Any]:
    actual_tokens: dict[str, tuple[str, ...]] = {}
    with _connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            for table, sql in _VERIFY_SQL.items():
                count = 2 if table == "content_artifact" else 1
                cursor.execute(sql, (material.job_id,) * count)
                actual_tokens[table] = tuple(
                    sorted(
                        sha256_bytes(
                            canonical_json_bytes(list(row))
                        )
                        for row in cursor.fetchall()
                    )
                )
    actual_counts = {
        table: len(values) for table, values in actual_tokens.items()
    }
    actual_fingerprint = sha256_bytes(canonical_json_bytes(actual_tokens))
    if (
        actual_counts != dict(material.expected_counts)
        or actual_tokens != dict(material.expected_tokens)
        or actual_fingerprint != material.expected_fingerprint
    ):
        raise UpdateStageLoadError(
            "fresh-connection stage count/fingerprint verification failed"
        )
    return {"counts": actual_counts, "fingerprint": actual_fingerprint}


def load_update_candidate(
    conninfo: str,
    bundle_path: Path,
    candidate_receipt_path: Path,
    bundle_relative_path: str,
    activation_cut: str | date,
    owner_key: str,
    notification_window_start: str,
    notification_window_end: str,
) -> dict[str, Any]:
    """Load or replay one verified bundle/candidate pair into stage schemas."""

    material = _prepare_update_load(
        bundle_path=Path(bundle_path),
        candidate_receipt_path=Path(candidate_receipt_path),
        bundle_relative_path=bundle_relative_path,
        activation_cut=activation_cut,
        owner_key=owner_key,
        notification_window_start=notification_window_start,
        notification_window_end=notification_window_end,
    )
    replayed = _apply_material(material, conninfo)
    verification = _verify_loaded(material, conninfo)
    return {
        "schema": "nhi-rule-history/update-pg-stage-receipt/v1",
        "job_id": material.job_id,
        "job_fingerprint": material.job_fingerprint,
        "bundle_id": material.bundle_id,
        "bundle_receipt_id": material.receipt_id,
        "candidate_proposal_id": material.proposal_id,
        "candidate_state": material.final_state,
        "replayed": replayed,
        "verification": verification,
    }
