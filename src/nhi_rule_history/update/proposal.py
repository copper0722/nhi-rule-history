"""Strict contract for non-authoritative, evidence-only worker proposals."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import canonical_json_bytes, sha256_bytes, stable_id


PROPOSAL_SCHEMA = "nhi-rule-history/agent-source-proposal/v2"
VALIDATED_SCHEMA = "nhi-rule-history/validated-source-candidate/v2"
NOTICE_BINDING_SCHEMA = "nhi-rule-history/controller-notice-binding/v1"
WORKER_OUTPUT_MAX_BYTES = 131_072
WORKER_JSON_MAX_DEPTH = 16

_TOP_LEVEL_FIELDS = {
    "schema",
    "temporal_evidence",
    "effect_candidates",
    "document_flags",
    "model_assessment",
    "reason_codes",
}
_NOTICE_BINDING_SOURCE_FIELDS = {
    "bundle_id",
    "bundle_fingerprint",
    "detail_artifact_sha256",
    "request_url",
    "final_url",
    "metadata",
}
_NOTICE_METADATA_FIELDS = {
    "subject_raw",
    "reference_number_raw",
    "reference_number_normalized",
    "reference_number_normalization",
    "reference_number_normalization_rule",
    "document_date_roc_raw",
    "publication_date_roc_raw",
    "update_date_roc_raw",
    "announcement_text_raw",
}
_SPAN_FIELDS = {
    "artifact_sha256",
    "block_id",
    "start",
    "end",
    "exact_text",
    "exact_text_sha256",
}
_TEMPORAL_FIELDS = {
    "source_span",
    "expression_raw",
    "calendar",
    "precision",
    "semantic_role",
    "scope_raw",
    "conditionality",
    "iso_date_candidate",
}
_EFFECT_FIELDS = {
    "designation_raw",
    "parent_chapter_raw",
    "comparison_kind_hint",
    "old_text_spans",
    "new_text_spans",
    "scope_count",
    "comparison_row_count",
    "review_flags",
}
_REVIEW_FLAGS = {
    "omitted_text",
    "merged_cells",
    "cross_row_dependency",
    "partial_patch",
    "multi_rule",
    "correction",
    "same_url_different_bytes",
    "odt_pdf_disagreement",
    "identity_uncertainty",
}
_DOCUMENT_FLAGS = {
    "correction_notice",
    "same_url_different_bytes",
    "odt_pdf_disagreement",
    "odt_pdf_parity_unverified",
    "declared_attachment_coverage_uncertain",
}
_FORBIDDEN_KEYS = {
    "notice",
    "notice_metadata",
    "notice_binding",
    "notice_binding_source",
    "binding_sha256",
    "detail_artifact_sha256",
    "request_url",
    "final_url",
    "bundle_id",
    "bundle_fingerprint",
    "official_document_number",
    "official_document_number_raw",
    "document_number",
    "document_number_raw",
    "ref_number",
    "ref_number_raw",
    "source_uid",
    "reference_number_raw",
    "reference_number_normalized",
    "reference_number_normalization",
    "reference_number_normalization_rule",
    "subject_raw",
    "document_date_roc_raw",
    "publication_date_roc_raw",
    "update_date_roc_raw",
    "announcement_text_raw",
    "rule_id",
    "stable_rule_id",
    "canonical_slug",
    "predecessor_id",
    "old_snapshot_id",
    "new_snapshot_id",
    "close_snapshot_id",
    "effective_to",
    "effective_until",
    "effective_until_exclusive",
    "head_generation",
    "proposed_operation",
    "proposed_operations",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProposalError(ValueError):
    """Raised when an agent output crosses its authority or evidence boundary."""


def _ensure_bounded_json_shape(
    value: Any,
    *,
    depth: int = 1,
    active: set[int] | None = None,
) -> None:
    """Reject excessive nesting and cyclic direct-Python inputs.

    Parsed JSON cannot contain cycles, but :func:`validate_proposal` is also a
    public Python API.  Apply the same depth contract to both entry points.
    """

    if depth > WORKER_JSON_MAX_DEPTH:
        raise ProposalError(
            f"worker JSON exceeds maximum depth {WORKER_JSON_MAX_DEPTH}"
        )
    if not isinstance(value, (Mapping, list)):
        return
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        raise ProposalError("worker JSON contains a cyclic container")
    active.add(identity)
    try:
        children = value.values() if isinstance(value, Mapping) else value
        for child in children:
            _ensure_bounded_json_shape(
                child,
                depth=depth + 1,
                active=active,
            )
    finally:
        active.remove(identity)


def _strict_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ProposalError(f"{label} contains unknown fields: {sorted(extra)}")


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key).casefold()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _source_span(
    value: Any,
    blocks: Mapping[str, Mapping[str, Any]],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProposalError(f"{label} must be an object")
    _strict_keys(value, _SPAN_FIELDS, label)
    if set(value) != _SPAN_FIELDS:
        raise ProposalError(f"{label} is missing required source-span fields")
    block_id = value["block_id"]
    block = blocks.get(block_id)
    if block is None:
        raise ProposalError(f"{label} references an unknown block")
    if value["artifact_sha256"] != block.get("artifact_sha256"):
        raise ProposalError(f"{label} artifact does not match source block")
    start = value["start"]
    end = value["end"]
    raw_text = block.get("raw_text")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(raw_text, str)
        or start < 0
        or end <= start
        or end > len(raw_text)
    ):
        raise ProposalError(f"{label} offsets are invalid")
    exact = value["exact_text"]
    if not isinstance(exact, str) or raw_text[start:end] != exact:
        raise ProposalError(f"{label} exact text does not resolve")
    digest = sha256_bytes(exact.encode("utf-8"))
    if value["exact_text_sha256"] != digest:
        raise ProposalError(f"{label} exact-text hash is invalid")
    return dict(value)


def _boolean_map(
    value: Any,
    fields: set[str],
    label: str,
) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise ProposalError(f"{label} must be an object")
    _strict_keys(value, fields, label)
    if set(value) != fields or any(type(item) is not bool for item in value.values()):
        raise ProposalError(f"{label} must contain every boolean flag")
    return dict(value)


def controller_notice_binding(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the exact notice binding that only the controller may author."""

    if not isinstance(value, Mapping):
        raise ProposalError("controller notice metadata must be an object")
    _strict_keys(
        value,
        _NOTICE_BINDING_SOURCE_FIELDS,
        "controller notice metadata",
    )
    if set(value) != _NOTICE_BINDING_SOURCE_FIELDS:
        raise ProposalError("controller notice metadata is incomplete")
    for field in _NOTICE_BINDING_SOURCE_FIELDS - {"metadata"}:
        if not isinstance(value[field], str) or not value[field]:
            raise ProposalError(
                "controller notice identity values must be non-empty strings"
            )
    for field in (
        "bundle_fingerprint",
        "detail_artifact_sha256",
    ):
        if not _SHA256.fullmatch(value[field]):
            raise ProposalError(
                f"controller notice {field} must be a SHA-256 digest"
            )
    metadata = value["metadata"]
    if not isinstance(metadata, Mapping):
        raise ProposalError("controller notice metadata payload must be an object")
    _strict_keys(metadata, _NOTICE_METADATA_FIELDS, "controller notice metadata")
    if set(metadata) != _NOTICE_METADATA_FIELDS:
        raise ProposalError("controller notice metadata payload is incomplete")
    if any(not isinstance(metadata[field], str) for field in metadata):
        raise ProposalError(
            "controller notice metadata payload values must be strings"
        )
    basis = {
        "schema": NOTICE_BINDING_SCHEMA,
        "bundle_id": value["bundle_id"],
        "bundle_fingerprint": value["bundle_fingerprint"],
        "detail_artifact_sha256": value["detail_artifact_sha256"],
        "request_url": value["request_url"],
        "final_url": value["final_url"],
        "metadata": dict(metadata),
    }
    return {
        **basis,
        "binding_sha256": sha256_bytes(canonical_json_bytes(basis)),
    }


def validate_proposal(
    proposal: Mapping[str, Any],
    *,
    source_blocks: Iterable[Mapping[str, Any]],
    bundle_id: str,
    bundle_fingerprint: str,
    required_true_document_flags: Iterable[str] = (),
    expected_notice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate evidence binding and return a controller-owned candidate receipt."""

    if not isinstance(proposal, Mapping):
        raise ProposalError("proposal must be a JSON object")
    _ensure_bounded_json_shape(proposal)
    forbidden = _FORBIDDEN_KEYS.intersection(_walk_keys(proposal))
    if forbidden:
        raise ProposalError(
            f"agent proposal contains authority fields: {sorted(forbidden)}"
        )
    _strict_keys(proposal, _TOP_LEVEL_FIELDS, "proposal")
    if set(proposal) != _TOP_LEVEL_FIELDS:
        raise ProposalError("proposal is missing required top-level fields")
    if proposal["schema"] != PROPOSAL_SCHEMA:
        raise ProposalError("unexpected proposal schema")
    if expected_notice is None:
        raise ProposalError("controller notice metadata is required")
    notice_binding = controller_notice_binding(expected_notice)
    if (
        notice_binding["bundle_id"] != bundle_id
        or notice_binding["bundle_fingerprint"] != bundle_fingerprint
    ):
        raise ProposalError(
            "controller notice binding does not match candidate bundle"
        )

    blocks: dict[str, Mapping[str, Any]] = {}
    for block in source_blocks:
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or block_id in blocks:
            raise ProposalError("source block identities must be unique strings")
        if not _SHA256.fullmatch(str(block.get("artifact_sha256", ""))):
            raise ProposalError("source block artifact hash is invalid")
        if not isinstance(block.get("raw_text"), str):
            raise ProposalError("source block text is invalid")
        blocks[block_id] = block

    temporal_rows = proposal["temporal_evidence"]
    if not isinstance(temporal_rows, list):
        raise ProposalError("temporal_evidence must be an array")
    validated_temporal: list[dict[str, Any]] = []
    for index, row in enumerate(temporal_rows):
        if not isinstance(row, Mapping):
            raise ProposalError("temporal evidence row must be an object")
        _strict_keys(row, _TEMPORAL_FIELDS, f"temporal_evidence[{index}]")
        if set(row) != _TEMPORAL_FIELDS:
            raise ProposalError("temporal evidence fields are incomplete")
        span = _source_span(
            row["source_span"], blocks, f"temporal_evidence[{index}].source_span"
        )
        expression = row["expression_raw"]
        if not isinstance(expression, str) or not expression.strip():
            raise ProposalError("temporal expression is empty")
        if expression not in span["exact_text"]:
            raise ProposalError("temporal expression is not inside its source span")
        if row["calendar"] not in {"ROC", "gregorian", "unknown"}:
            raise ProposalError("temporal calendar is invalid")
        if row["precision"] not in {"day", "month", "year", "unknown"}:
            raise ProposalError("temporal precision is invalid")
        if row["semantic_role"] not in {
            "effective_from",
            "document_date",
            "publication_date",
            "unknown",
        }:
            raise ProposalError("temporal semantic role is invalid")
        if row["conditionality"] not in {
            "unconditional",
            "conditional",
            "unknown",
        }:
            raise ProposalError("temporal conditionality is invalid")
        if not isinstance(row["scope_raw"], str):
            raise ProposalError("temporal scope_raw must be a string")
        iso_candidate = row["iso_date_candidate"]
        if iso_candidate is not None:
            try:
                date.fromisoformat(iso_candidate)
            except (TypeError, ValueError) as exc:
                raise ProposalError("temporal ISO date candidate is invalid") from exc
        validated_temporal.append({**dict(row), "source_span": span})

    effects = proposal["effect_candidates"]
    if not isinstance(effects, list):
        raise ProposalError("effect_candidates must be an array")
    validated_effects: list[dict[str, Any]] = []
    for effect_index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            raise ProposalError("effect candidate must be an object")
        _strict_keys(effect, _EFFECT_FIELDS, f"effect_candidates[{effect_index}]")
        if set(effect) != _EFFECT_FIELDS:
            raise ProposalError("effect candidate fields are incomplete")
        if not isinstance(effect["designation_raw"], str):
            raise ProposalError("designation_raw must be a string")
        if not isinstance(effect["parent_chapter_raw"], str):
            raise ProposalError("parent_chapter_raw must be a string")
        if effect["comparison_kind_hint"] not in {
            "full_replacement",
            "partial_replacement",
            "creation",
            "deletion",
            "unknown",
        }:
            raise ProposalError("comparison_kind_hint is invalid")
        for count_field in ("scope_count", "comparison_row_count"):
            count = effect[count_field]
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
            ):
                raise ProposalError(f"{count_field} must be a positive integer")
        review_flags = _boolean_map(
            effect["review_flags"],
            _REVIEW_FLAGS,
            f"effect_candidates[{effect_index}].review_flags",
        )
        validated_spans: dict[str, list[dict[str, Any]]] = {}
        for side in ("old_text_spans", "new_text_spans"):
            rows = effect[side]
            if not isinstance(rows, list):
                raise ProposalError(f"{side} must be an array")
            validated_spans[side] = [
                _source_span(
                    span,
                    blocks,
                    f"effect_candidates[{effect_index}].{side}[{span_index}]",
                )
                for span_index, span in enumerate(rows)
            ]
        validated_effects.append(
            {
                **dict(effect),
                **validated_spans,
                "review_flags": review_flags,
            }
        )

    document_flags = _boolean_map(
        proposal["document_flags"], _DOCUMENT_FLAGS, "document_flags"
    )
    for flag in required_true_document_flags:
        if flag not in _DOCUMENT_FLAGS:
            raise ProposalError("controller required an unknown document flag")
        if not document_flags[flag]:
            raise ProposalError(
                f"controller requires document flag {flag}=true"
            )
    if proposal["model_assessment"] not in {
        "single_full_replacement_candidate",
        "needs_review",
        "no_relevant_rule",
    }:
        raise ProposalError("model_assessment is invalid")
    reason_codes = proposal["reason_codes"]
    if (
        not isinstance(reason_codes, list)
        or any(not isinstance(value, str) or not value for value in reason_codes)
        or len(set(reason_codes)) != len(reason_codes)
    ):
        raise ProposalError("reason_codes must be unique non-empty strings")

    all_review_flags = [
        value
        for effect in validated_effects
        for value in effect["review_flags"].values()
    ] + list(document_flags.values())
    first_lane_shape = (
        proposal["model_assessment"] == "single_full_replacement_candidate"
        and len(validated_effects) == 1
        and validated_effects[0]["comparison_kind_hint"] == "full_replacement"
        and validated_effects[0]["scope_count"] == 1
        and validated_effects[0]["comparison_row_count"] == 1
        and bool(validated_effects[0]["old_text_spans"])
        and bool(validated_effects[0]["new_text_spans"])
        and not any(all_review_flags)
    )
    if proposal["model_assessment"] == "no_relevant_rule":
        state = "needs_review"
        controller_reasons = ["MODEL_FOUND_NO_RELEVANT_RULE"]
    elif first_lane_shape:
        state = "promotion_ready_pending_anchor"
        controller_reasons = ["CANONICAL_PROMOTION_DISABLED_PENDING_ANCHOR_REPLAY"]
    else:
        state = "needs_review"
        controller_reasons = ["OUTSIDE_FIRST_FULL_SINGLE_CLAUSE_LANE"]

    normalized_proposal = {
        **dict(proposal),
        "temporal_evidence": validated_temporal,
        "effect_candidates": validated_effects,
        "document_flags": document_flags,
        "reason_codes": list(reason_codes),
    }
    proposal_sha = sha256_bytes(canonical_json_bytes(normalized_proposal))
    candidate_id = stable_id(
        "nhi-validated-source-candidate",
        bundle_id,
        bundle_fingerprint,
        proposal_sha,
        notice_binding["binding_sha256"],
    )
    return {
        "schema": VALIDATED_SCHEMA,
        "candidate_id": candidate_id,
        "bundle_id": bundle_id,
        "bundle_fingerprint": bundle_fingerprint,
        "proposal_sha256": proposal_sha,
        "notice_binding": notice_binding,
        "state": state,
        "auto_promotion_enabled": False,
        "first_lane_shape": first_lane_shape,
        "controller_reason_codes": controller_reasons,
        "proposal": normalized_proposal,
    }


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ProposalError(f"worker JSON repeats key {key!r}")
        value[key] = child
    return value


def parse_and_validate_proposal(
    payload: str | bytes,
    *,
    source_blocks: Iterable[Mapping[str, Any]],
    bundle_id: str,
    bundle_fingerprint: str,
    required_true_document_flags: Iterable[str] = (),
    expected_notice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload_bytes = (
        payload.encode("utf-8") if isinstance(payload, str) else payload
    )
    if len(payload_bytes) > WORKER_OUTPUT_MAX_BYTES:
        raise ProposalError(
            f"worker output exceeds {WORKER_OUTPUT_MAX_BYTES} bytes"
        )
    try:
        payload_text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalError("worker output is not UTF-8 JSON") from exc
    try:
        value = json.loads(
            payload_text,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as exc:
        raise ProposalError("worker output is not one JSON object") from exc
    return validate_proposal(
        value,
        source_blocks=source_blocks,
        bundle_id=bundle_id,
        bundle_fingerprint=bundle_fingerprint,
        required_true_document_flags=required_true_document_flags,
        expected_notice=expected_notice,
    )
