"""Build a deterministic, non-authoritative queue from clause-date evidence.

One row represents one already-observed clause-date pair.  The queue orders
stronger source-search candidates first, but never converts co-occurrence into
an official event, legal effective date, stable identity, predecessor, or
canonical history assertion.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
)


QUEUE_ROW_SCHEMA = "nhi-rule-history/history-gap-work-unit/v1"
QUEUE_MANIFEST_SCHEMA = "nhi-rule-history/history-gap-work-queue/v1"
CROSS_LEDGER_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-evidence-ledger/v1"
)
CROSS_PAIR_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-article-date-pair/v1"
)
DOCUMENT_CANDIDATE_SCHEMA = (
    "nhi-rule-history/history-marker-document-candidate/v1"
)
NON_CLAIM = (
    "Prioritized source-review work only. A date marker, same-date artifact, "
    "designation co-occurrence, or owning-document candidate does not establish "
    "an official amendment event, legal effective date, stable clause identity, "
    "direct predecessor, exact historical text, or history completeness."
)
REQUIRED_RESULTS = (
    "official_event_identity",
    "legal_date_role_and_exact_locator",
    "stable_clause_identity",
    "exact_pre_event_text_spans",
    "exact_post_event_text_spans",
    "direct_predecessor_adjacency",
    "pre_and_post_anchor_replay",
)
BASE_BLOCKERS = (
    "official_event_unresolved",
    "legal_date_role_unresolved",
    "stable_clause_identity_unresolved",
    "exact_historical_text_unresolved",
    "direct_predecessor_unresolved",
    "anchor_replay_unresolved",
)
LANES = {
    "unique_document_candidate": 1,
    "ambiguous_document_candidates": 2,
    "native_date_without_joint_document_candidate": 3,
    "marker_without_native_document_date_match": 4,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
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
                f"{path.name}:{line_number}: row is not an object"
            )
        rows.append(row)
    return rows


def _cross_pairs(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], str, str]:
    pairs: dict[str, dict[str, Any]] = {}
    input_fingerprints: set[str] = set()
    output_fingerprints: set[str] = set()
    for wrapper in _read_jsonl(path):
        if wrapper.get("schema") != CROSS_LEDGER_SCHEMA:
            raise ContractError("cross-format ledger schema is unsupported")
        if wrapper.get("evidence_group") != "article_date_pairs":
            continue
        evidence = wrapper.get("evidence")
        if (
            not isinstance(evidence, dict)
            or evidence.get("schema") != CROSS_PAIR_SCHEMA
        ):
            raise ContractError("cross-format clause-date evidence is malformed")
        pair_id = evidence.get("pair_id")
        if not isinstance(pair_id, str) or len(pair_id) != 64:
            raise ContractError("cross-format pair identity is invalid")
        if pair_id in pairs:
            raise ContractError("cross-format pair identity is duplicated")
        pairs[pair_id] = evidence
        input_fingerprints.add(str(wrapper.get("input_fingerprint", "")))
        output_fingerprints.add(str(wrapper.get("output_fingerprint", "")))
    if len(input_fingerprints) != 1 or len(output_fingerprints) != 1:
        raise ContractError("cross-format ledger fingerprints are inconsistent")
    if not pairs:
        raise ContractError("cross-format ledger contains no clause-date pairs")
    return (
        pairs,
        next(iter(input_fingerprints)),
        next(iter(output_fingerprints)),
    )


def _document_candidates(path: Path) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        if row.get("schema") != DOCUMENT_CANDIDATE_SCHEMA:
            raise ContractError("document-candidate schema is unsupported")
        pair_id = row.get("pair_id")
        if not isinstance(pair_id, str) or len(pair_id) != 64:
            raise ContractError("document-candidate pair identity is invalid")
        if pair_id in candidates:
            raise ContractError("document-candidate pair identity is duplicated")
        documents = row.get("official_document_candidates")
        count = row.get("candidate_count")
        if (
            not isinstance(documents, list)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count != len(documents)
            or count < 1
        ):
            raise ContractError("document-candidate count is inconsistent")
        expected_status = (
            "unique_document_candidate"
            if count == 1
            else "ambiguous_document_candidates"
        )
        if row.get("candidate_status") != expected_status:
            raise ContractError("document-candidate status is inconsistent")
        candidates[pair_id] = row
    return candidates


def _lane(
    pair: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
) -> tuple[str, int, list[str]]:
    if candidate is not None:
        name = str(candidate["candidate_status"])
        extra = (
            ["document_candidate_requires_legal_confirmation"]
            if name == "unique_document_candidate"
            else ["multiple_document_candidates_require_adjudication"]
        )
        return name, LANES[name], extra
    if pair.get("native_cross_format_date_candidate") is True:
        name = "native_date_without_joint_document_candidate"
        return (
            name,
            LANES[name],
            ["no_same_artifact_date_and_official_designation_candidate"],
        )
    name = "marker_without_native_document_date_match"
    return (
        name,
        LANES[name],
        ["no_native_typed_historical_artifact_date_candidate"],
    )


def build_gap_work_units(
    cross_format_ledger: Path,
    document_candidate_ledger: Path,
    *,
    declared_cut: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        date.fromisoformat(declared_cut)
    except ValueError as exc:
        raise ContractError("declared cut must be an ISO date") from exc
    pairs, cross_input, cross_output = _cross_pairs(cross_format_ledger)
    candidates = _document_candidates(document_candidate_ledger)
    unknown_pairs = set(candidates) - set(pairs)
    if unknown_pairs:
        raise ContractError("document candidates reference unknown pairs")

    units: list[dict[str, Any]] = []
    for pair_id, pair in pairs.items():
        candidate = candidates.get(pair_id)
        if candidate is not None:
            for field in (
                "article_id",
                "article_num",
                "designation_kind",
                "normalized_iso_candidate",
            ):
                if candidate.get(field) != pair.get(field):
                    raise ContractError(
                        f"document candidate disagrees on {field}"
                    )
        lane, priority, extra_blockers = _lane(pair, candidate)
        cross_row_sha = sha256_bytes(canonical_json_bytes(dict(pair)))
        candidate_row_sha = (
            sha256_bytes(canonical_json_bytes(dict(candidate)))
            if candidate is not None
            else None
        )
        unit = {
            "schema": QUEUE_ROW_SCHEMA,
            "work_unit_id": stable_id(
                "nhi-history-gap-work-unit-v1", pair_id
            ),
            "pair_id": pair_id,
            "declared_cut": declared_cut,
            "state": "pending_source_review",
            "priority": priority,
            "priority_lane": lane,
            "article": {
                "article_id": pair["article_id"],
                "source_designation_raw": pair["article_num"],
                "designation_kind": pair["designation_kind"],
            },
            "date_marker": {
                "normalized_iso_candidate": pair[
                    "normalized_iso_candidate"
                ],
                "marker_occurrence_count": pair[
                    "marker_occurrence_count"
                ],
                "legal_date_role_resolved": False,
            },
            "source_search_evidence": {
                "native_cross_format_date_candidate": pair[
                    "native_cross_format_date_candidate"
                ],
                "native_cross_format_joint_candidate": pair[
                    "native_cross_format_joint_candidate"
                ],
                "native_typed_date_artifact_sha256s": pair[
                    "native_typed_date_artifact_sha256s"
                ],
                "native_typed_joint_artifact_sha256s": pair[
                    "native_typed_joint_artifact_sha256s"
                ],
                "official_document_candidates": (
                    candidate["official_document_candidates"]
                    if candidate is not None
                    else []
                ),
            },
            "evidence_binding": {
                "cross_format_input_fingerprint": cross_input,
                "cross_format_output_fingerprint": cross_output,
                "cross_format_pair_sha256": cross_row_sha,
                "document_candidate_row_sha256": candidate_row_sha,
            },
            "blockers": [*BASE_BLOCKERS, *extra_blockers],
            "required_results": list(REQUIRED_RESULTS),
            "worker_authority": "candidate_extraction_only",
            "canonical_write_authorized": False,
            "non_claim": NON_CLAIM,
        }
        unit["work_unit_fingerprint"] = sha256_bytes(
            canonical_json_bytes(unit)
        )
        units.append(unit)

    units.sort(
        key=lambda row: (
            row["priority"],
            row["date_marker"]["normalized_iso_candidate"],
            int(row["article"]["article_id"]),
            row["pair_id"],
        )
    )
    counts = Counter(row["priority_lane"] for row in units)
    manifest = {
        "schema": QUEUE_MANIFEST_SCHEMA,
        "status": "prepared_pending_source_review",
        "declared_cut": declared_cut,
        "row_count": len(units),
        "counts_by_priority_lane": {
            lane: counts.get(lane, 0)
            for lane, _priority in sorted(
                LANES.items(), key=lambda item: item[1]
            )
        },
        "inputs": {
            "cross_format_ledger": {
                "file_name": cross_format_ledger.name,
                "sha256": file_sha256(cross_format_ledger),
                "input_fingerprint": cross_input,
                "output_fingerprint": cross_output,
            },
            "document_candidate_ledger": {
                "file_name": document_candidate_ledger.name,
                "sha256": file_sha256(document_candidate_ledger),
            },
        },
        "invariants": {
            "one_work_unit_per_clause_date_pair": True,
            "work_unit_identity_stable_from_pair_id": True,
            "source_rows_hash_bound": True,
            "priority_is_not_legal_resolution": True,
            "model_output_candidate_only": True,
            "canonical_write_authorized": False,
        },
        "required_results": list(REQUIRED_RESULTS),
        "non_claim": NON_CLAIM,
    }
    return units, manifest


def _payload(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_gap_work_queue(
    cross_format_ledger: Path,
    document_candidate_ledger: Path,
    output_path: Path,
    manifest_path: Path,
    *,
    declared_cut: str,
    expected_row_count: int | None = None,
) -> dict[str, Any]:
    units, manifest = build_gap_work_units(
        cross_format_ledger,
        document_candidate_ledger,
        declared_cut=declared_cut,
    )
    if expected_row_count is not None and len(units) != expected_row_count:
        raise ContractError(
            "gap work-unit row count does not match the declared denominator"
        )
    queue_payload = _payload(units)
    manifest = {
        **manifest,
        "queue": {
            "file_name": output_path.name,
            "sha256": sha256_bytes(queue_payload),
            "byte_size": len(queue_payload),
        },
    }
    _write_atomic(output_path, queue_payload)
    _write_atomic(manifest_path, canonical_json_bytes(manifest))
    return manifest
