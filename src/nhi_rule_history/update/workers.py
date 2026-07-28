"""Bounded, fail-closed worker orchestration for source-only proposals."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from nhi_rule_history.contracts import (
    ContractError,
    append_jsonl,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
    utc_now,
    write_json,
)
from nhi_rule_history.update.bundle import verify_bundle
from nhi_rule_history.update.notice import extract_notice_metadata
from nhi_rule_history.update.odt import (
    ODT_STRUCTURAL_FACTS_SCHEMA,
    inspect_odt_document,
)
from nhi_rule_history.update.proposal import (
    NOTICE_BINDING_SCHEMA,
    PROPOSAL_SCHEMA,
    WORKER_JSON_MAX_DEPTH,
    WORKER_OUTPUT_MAX_BYTES,
    ProposalError,
    controller_notice_binding,
    parse_and_validate_proposal,
)


WORKER_PROMPT_VERSION = "nhi-rule-history-source-proposal/2.0.0"
WORKER_ATTEMPT_SCHEMA = "nhi-rule-history/worker-attempt/v1"
WORKER_RUN_SCHEMA = "nhi-rule-history/worker-run/v3"
WORKER_JOB_FINGERPRINT_DOMAIN = "nhi-rule-history/worker-job-fingerprint/v4"
WORKER_SOURCE_PACKET_SCHEMA = "nhi-rule-history/worker-source-packet/v3"
WORKER_SUITABILITY_SCHEMA = "nhi-rule-history/worker-suitability/v2"
WORKER_EXTRACTION_VERSION = ODT_STRUCTURAL_FACTS_SCHEMA
WORKER_BLOCK_DIGEST_VERSION = "nhi-rule-history/source-block-digest/v1"
WORKER_RUNTIME_CONTRACT_VERSION = (
    "nhi-rule-history/primary-once-fallback-once-runtime/v2"
)
WORKER_BUDGET_VERSION = "bounded-document-v1"
WORKER_BUDGET = {
    "prompt_bytes": 65_536,
    "source_text_bytes": 32_768,
    "source_blocks": 96,
    "single_block_bytes": 8_192,
    "output_bytes": WORKER_OUTPUT_MAX_BYTES,
    "json_depth": WORKER_JSON_MAX_DEPTH,
}
_DESIGNATION_RE = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+){1,4})(?=\s|[、.)）])"
)
_OMISSION_RE = re.compile(
    r"(?:[（(]略[）)]|(?:其餘(?:文字)?|以下|前|後)\s*略|"
    r"(?:^|[\s：:；;])略(?:$|[\s。；;]))"
)
_CROSS_ROW_RE = re.compile(
    r"(?:同上|續前|接續|承前|跨列|跨欄)"
)
_NEW_SIDE_HEADER_RE = re.compile(
    r"(?:建議)?(?:修訂|修正)後(?:給付)?規定|新(?:給付)?規定"
)
_OLD_SIDE_HEADER_RE = re.compile(
    r"(?:原|修訂前|修正前)(?:給付)?規定"
)


class WorkerFailure(RuntimeError):
    """Both bounded worker attempts failed or violated the proposal contract."""


def worker_job_fingerprint(
    *,
    manifest_sha256: str,
    prompt_sha256: str,
) -> str:
    """Bind an immutable worker job to every persisted worker contract."""

    return stable_id(
        WORKER_JOB_FINGERPRINT_DOMAIN,
        manifest_sha256,
        WORKER_EXTRACTION_VERSION,
        WORKER_BLOCK_DIGEST_VERSION,
        WORKER_PROMPT_VERSION,
        prompt_sha256,
        WORKER_RUNTIME_CONTRACT_VERSION,
        WORKER_SUITABILITY_SCHEMA,
        WORKER_BUDGET_VERSION,
        canonical_json_bytes(WORKER_BUDGET).decode("utf-8"),
        WORKER_ATTEMPT_SCHEMA,
        WORKER_RUN_SCHEMA,
        PROPOSAL_SCHEMA,
        NOTICE_BINDING_SCHEMA,
    )


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    runtime_id: str
    provider: str
    model: str
    command: tuple[str, ...]
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.worker_id or not self.runtime_id or not self.provider or not self.model:
            raise ValueError("worker metadata must be explicit")
        if not self.command or any(not value for value in self.command):
            raise ValueError("worker command must be a non-empty argv tuple")
        if self.timeout_seconds < 1:
            raise ValueError("worker timeout must be positive")


def source_blocks_digest(
    blocks: Iterable[Mapping[str, Any]],
) -> str:
    """Hash the exact ordered block projection consumed by the worker."""

    projection = []
    for block in blocks:
        raw_text = block.get("raw_text")
        if not isinstance(raw_text, str):
            raise ContractError("source block raw_text must be a string")
        raw_text_sha = sha256_bytes(raw_text.encode("utf-8"))
        if block.get("raw_text_sha256") != raw_text_sha:
            raise ContractError("source block raw-text hash is inconsistent")
        projection.append(
            {
                "block_id": block["block_id"],
                "artifact_sha256": block["artifact_sha256"],
                "locator": block["locator"],
                "raw_text_sha256": raw_text_sha,
                "raw_text_byte_length": len(raw_text.encode("utf-8")),
            }
        )
    return sha256_bytes(canonical_json_bytes(projection))


def source_packet(bundle_path: Path) -> dict[str, Any]:
    verification = verify_bundle(bundle_path)
    manifest = json.loads(
        (bundle_path / "manifest.json").read_text(encoding="utf-8")
    )
    blocks: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    structural_facts: list[dict[str, Any]] = []
    has_pdf = False
    has_odt = False
    notice_metadata: dict[str, Any] | None = None
    notice_binding_source: dict[str, Any] | None = None
    for resource in manifest["resources"]:
        if resource["relation"] == "detail_page":
            if notice_metadata is not None:
                raise ContractError(
                    "source packet contains multiple detail-page authorities"
                )
            payload = (bundle_path / resource["content_path"]).read_bytes()
            notice_metadata = extract_notice_metadata(
                payload, resource["artifact_sha256"]
            )
            notice_binding_source = {
                "bundle_id": verification["bundle_id"],
                "bundle_fingerprint": verification[
                    "bundle_fingerprint"
                ],
                "detail_artifact_sha256": resource["artifact_sha256"],
                "request_url": resource["request_url"],
                "final_url": resource["final_url"],
                "metadata": notice_metadata,
            }
            continue
        if resource["relation"] != "declared_attachment":
            continue
        inventory.append(
            {
                "artifact_sha256": resource["artifact_sha256"],
                "media_type": resource["media_type"],
                "declared_sequence": resource["declared_sequence"],
                "declared_label": resource["declared_label"],
            }
        )
        if resource["media_type"] == "application/pdf":
            has_pdf = True
        if (
            resource["media_type"]
            == "application/vnd.oasis.opendocument.text"
        ):
            has_odt = True
            payload = (bundle_path / resource["content_path"]).read_bytes()
            inspected = inspect_odt_document(
                payload,
                resource["artifact_sha256"],
            )
            blocks.extend(inspected["blocks"])
            structural_facts.append(inspected["structural_facts"])
    if not has_odt or not blocks:
        raise ContractError(
            "first worker lane requires at least one parseable ODT attachment"
        )
    if notice_metadata is None or notice_binding_source is None:
        raise ContractError("source packet is missing deterministic notice metadata")
    if len({row["block_id"] for row in blocks}) != len(blocks):
        raise ContractError("source packet contains duplicate block identities")
    block_digest = source_blocks_digest(blocks)
    notice_binding = controller_notice_binding(notice_binding_source)
    return {
        "schema": WORKER_SOURCE_PACKET_SCHEMA,
        "bundle_id": verification["bundle_id"],
        "bundle_fingerprint": verification["bundle_fingerprint"],
        "manifest_sha256": verification["manifest_sha256"],
        "rss_item": manifest["rss_item"],
        # Controller-only.  build_worker_prompt deliberately excludes both
        # fields so the worker cannot echo or mutate notice authority.
        "notice_metadata": notice_metadata,
        "notice_binding_source": notice_binding_source,
        "notice_binding": notice_binding,
        "attachment_inventory": inventory,
        "structural_facts": structural_facts,
        "source_blocks_sha256": block_digest,
        "semantic_contract": {
            "extraction_version": WORKER_EXTRACTION_VERSION,
            "block_digest_version": WORKER_BLOCK_DIGEST_VERSION,
            "prompt_template_version": WORKER_PROMPT_VERSION,
            "proposal_schema": PROPOSAL_SCHEMA,
            "runtime_contract_version": WORKER_RUNTIME_CONTRACT_VERSION,
            "budget_version": WORKER_BUDGET_VERSION,
        },
        "controller_facts": {
            "declared_attachment_coverage_complete": True,
            "contains_pdf": has_pdf,
            "contains_odt": has_odt,
            "odt_pdf_parity_verified": False,
            "canonical_promotion_enabled": False,
        },
        "source_blocks": blocks,
    }


def _worker_source_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return the source-only projection allowed to cross the worker boundary."""

    allowed = (
        "schema",
        "bundle_id",
        "bundle_fingerprint",
        "manifest_sha256",
        "attachment_inventory",
        "structural_facts",
        "source_blocks_sha256",
        "semantic_contract",
        "controller_facts",
        "source_blocks",
    )
    try:
        return {key: packet[key] for key in allowed}
    except KeyError as exc:
        raise ContractError(
            f"worker source packet is missing {exc.args[0]}"
        ) from exc


def _validate_semantic_contract(packet: Mapping[str, Any]) -> None:
    expected = {
        "extraction_version": WORKER_EXTRACTION_VERSION,
        "block_digest_version": WORKER_BLOCK_DIGEST_VERSION,
        "prompt_template_version": WORKER_PROMPT_VERSION,
        "proposal_schema": PROPOSAL_SCHEMA,
        "runtime_contract_version": WORKER_RUNTIME_CONTRACT_VERSION,
        "budget_version": WORKER_BUDGET_VERSION,
    }
    if packet.get("semantic_contract") != expected:
        raise ContractError("worker semantic contract does not match runtime")


def build_worker_prompt(packet: Mapping[str, Any]) -> str:
    """Build a self-contained public-source packet with an exact JSON contract."""

    _validate_semantic_contract(packet)
    instructions = {
        "role": (
            "You are a non-authoritative source-reading worker. Extract bounded "
            "evidence candidates only. Do not decide stable identity, legal "
            "adjacency, canonical history, or database operations."
        ),
        "output": (
            "Return exactly one JSON object and no Markdown, prose, or code fence."
        ),
        "authority_boundary": [
            "Never emit rule_id, stable_rule_id, canonical_slug, predecessor_id, "
            "old_snapshot_id, new_snapshot_id, close_snapshot_id, effective_to, "
            "effective_until, head_generation, proposed_operation, or "
            "proposed_operations.",
            "Never emit notice metadata or notice bindings, including "
            "reference_number_raw, subject_raw, document/publication/update "
            "dates, or announcement_text_raw. The controller binds the exact "
            "official notice after validation.",
            "Every quoted source span must resolve exactly to one supplied block "
            "using Python character offsets [start,end) and SHA-256 of exact_text.",
            "An ISO date is a candidate attached to the raw expression, not an "
            "authoritative legal date.",
            "If text is omitted with 略, crosses cells/rows, covers multiple "
            "targets, or cannot be mapped exactly, set the corresponding review "
            "flag and model_assessment=needs_review.",
            "Because the controller has not verified ODT/PDF parity, "
            "document_flags.odt_pdf_parity_unverified must be true whenever "
            "controller_facts.contains_pdf is true.",
        ],
        "schema": {
            "schema": PROPOSAL_SCHEMA,
            "temporal_evidence": [
                {
                    "source_span": {
                        "artifact_sha256": "64 lowercase hex",
                        "block_id": "supplied block_id",
                        "start": "integer",
                        "end": "integer",
                        "exact_text": "exact block substring",
                        "exact_text_sha256": "SHA-256 of UTF-8 exact_text",
                    },
                    "expression_raw": "exact temporal expression inside exact_text",
                    "calendar": "ROC|gregorian|unknown",
                    "precision": "day|month|year|unknown",
                    "semantic_role": (
                        "effective_from|document_date|publication_date|unknown"
                    ),
                    "scope_raw": "what the expression appears to govern",
                    "conditionality": "unconditional|conditional|unknown",
                    "iso_date_candidate": "YYYY-MM-DD or null",
                }
            ],
            "effect_candidates": [
                {
                    "designation_raw": "source designation, empty if absent",
                    "parent_chapter_raw": "source chapter, empty if absent",
                    "comparison_kind_hint": (
                        "full_replacement|partial_replacement|creation|"
                        "deletion|unknown"
                    ),
                    "old_text_spans": ["same source-span object"],
                    "new_text_spans": ["same source-span object"],
                    "scope_count": "positive integer",
                    "comparison_row_count": "positive integer",
                    "review_flags": {
                        "omitted_text": "boolean",
                        "merged_cells": "boolean",
                        "cross_row_dependency": "boolean",
                        "partial_patch": "boolean",
                        "multi_rule": "boolean",
                        "correction": "boolean",
                        "same_url_different_bytes": "boolean",
                        "odt_pdf_disagreement": "boolean",
                        "identity_uncertainty": "boolean",
                    },
                }
            ],
            "document_flags": {
                "correction_notice": "boolean",
                "same_url_different_bytes": "boolean",
                "odt_pdf_disagreement": "boolean",
                "odt_pdf_parity_unverified": "boolean",
                "declared_attachment_coverage_uncertain": "boolean",
            },
            "model_assessment": (
                "single_full_replacement_candidate|needs_review|no_relevant_rule"
            ),
            "reason_codes": ["unique machine-readable strings"],
        },
    }
    return json.dumps(
        {
            "prompt_version": WORKER_PROMPT_VERSION,
            "budget_version": WORKER_BUDGET_VERSION,
            "budget": WORKER_BUDGET,
            "instructions": instructions,
            "source_packet": _worker_source_packet(packet),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def assess_worker_suitability(
    packet: Mapping[str, Any],
    *,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Deterministically gate the single-document worker lane.

    This is deliberately conservative.  Complex documents are routed to a
    later partitioner before either the primary or fallback model is invoked.
    """

    blocks = list(packet.get("source_blocks", ()))
    if not all(isinstance(block, Mapping) for block in blocks):
        raise ContractError("source_blocks must be an array of objects")
    expected_digest = source_blocks_digest(blocks)
    if packet.get("source_blocks_sha256") != expected_digest:
        raise ContractError("source block digest does not match packet")
    _validate_semantic_contract(packet)
    prompt_value = prompt if prompt is not None else build_worker_prompt(packet)
    prompt_bytes = len(prompt_value.encode("utf-8"))
    block_byte_lengths = [
        len(str(block.get("raw_text", "")).encode("utf-8"))
        for block in blocks
    ]
    source_text_bytes = sum(block_byte_lengths)
    structural = list(packet.get("structural_facts", ()))
    if not all(isinstance(row, Mapping) for row in structural):
        raise ContractError("structural_facts must be an array of objects")
    reasons: set[str] = set()

    if prompt_bytes > WORKER_BUDGET["prompt_bytes"]:
        reasons.add("PROMPT_BUDGET_EXCEEDED")
    if source_text_bytes > WORKER_BUDGET["source_text_bytes"]:
        reasons.add("SOURCE_TEXT_BUDGET_EXCEEDED")
    if len(blocks) > WORKER_BUDGET["source_blocks"]:
        reasons.add("SOURCE_BLOCK_COUNT_EXCEEDED")
    if max(block_byte_lengths, default=0) > WORKER_BUDGET[
        "single_block_bytes"
    ]:
        reasons.add("SINGLE_BLOCK_BUDGET_EXCEEDED")

    if len(structural) != 1:
        reasons.add("MULTIPLE_ODT_DOCUMENTS")
    if any(not bool(row.get("exact_once_verified")) for row in structural):
        reasons.add("SOURCE_EXACT_ONCE_UNVERIFIED")
    if any(
        int(row.get("covered_cell_count", 0))
        + int(row.get("column_span_cell_count", 0))
        + int(row.get("row_span_cell_count", 0))
        > 0
        for row in structural
    ):
        reasons.add("MERGED_TABLE_STRUCTURE")
    if any(
        int(row.get("repeated_cell_count", 0))
        + int(row.get("repeated_row_count", 0))
        > 0
        for row in structural
    ):
        reasons.add("REPEATED_TABLE_STRUCTURE")
    if any(int(row.get("nested_table_count", 0)) > 0 for row in structural):
        reasons.add("NESTED_TABLE_STRUCTURE")

    designation_rows: dict[str, set[tuple[str, int, int]]] = {}
    all_text = []
    row_cells: dict[
        tuple[str, int, int],
        dict[int, list[str]],
    ] = {}
    for block in blocks:
        raw_text = str(block.get("raw_text", ""))
        all_text.append(raw_text)
        locator = block.get("locator")
        if not isinstance(locator, Mapping):
            continue
        artifact = str(block.get("artifact_sha256", ""))
        table_index = locator.get("table_index")
        row_index = locator.get("row_index")
        cell_index = locator.get("cell_index")
        table_depth = locator.get("table_depth")
        if (
            table_depth == 0
            and isinstance(table_index, int)
            and isinstance(row_index, int)
            and isinstance(cell_index, int)
        ):
            row_cells.setdefault(
                (artifact, table_index, row_index),
                {},
            ).setdefault(cell_index, []).append(raw_text)
        for match in _DESIGNATION_RE.finditer(raw_text):
            if (
                table_depth == 0
                and isinstance(table_index, int)
                and isinstance(row_index, int)
            ):
                designation_rows.setdefault(match.group(1), set()).add(
                    (artifact, table_index, row_index)
                )
            else:
                designation_rows.setdefault(match.group(1), set())
    effective_designations = set(designation_rows)
    collapsed_parent_designations: list[str] = []
    if len(designation_rows) > 1:
        maximal_designations = [
            designation
            for designation in designation_rows
            if not any(
                other.startswith(designation + ".")
                for other in designation_rows
                if other != designation
            )
        ]
        if len(maximal_designations) == 1:
            leaf = maximal_designations[0]
            leaf_rows = designation_rows[leaf]
            parent_designations = [
                designation
                for designation in designation_rows
                if designation != leaf
                and leaf.startswith(designation + ".")
            ]
            if (
                leaf_rows
                and len(parent_designations)
                == len(designation_rows) - 1
                and all(
                    not designation_rows[parent]
                    or designation_rows[parent].issubset(leaf_rows)
                    for parent in parent_designations
                )
            ):
                effective_designations = {leaf}
                collapsed_parent_designations = sorted(
                    parent_designations
                )
    if len(effective_designations) > 1:
        reasons.add("MULTI_RULE_DOCUMENT")
    elif len(effective_designations) != 1:
        reasons.add("SINGLE_TARGET_UNRESOLVED")
    if _OMISSION_RE.search("\n".join(all_text)):
        reasons.add("OMITTED_TEXT_PRESENT")
    comparison_headers: list[tuple[str, int, int]] = []
    for row_key, cells in row_cells.items():
        new_cells = {
            cell
            for cell, texts in cells.items()
            if _NEW_SIDE_HEADER_RE.search("\n".join(texts))
        }
        old_cells = {
            cell
            for cell, texts in cells.items()
            if _OLD_SIDE_HEADER_RE.search("\n".join(texts))
        }
        if any(
            new_cell != old_cell
            for new_cell in new_cells
            for old_cell in old_cells
        ):
            comparison_headers.append(row_key)
    if len(comparison_headers) != 1:
        reasons.add("COMPARISON_SIDES_UNRESOLVED")
    else:
        artifact, table_index, header_row = comparison_headers[0]
        substantive_rows = {
            row_index
            for (
                row_artifact,
                row_table,
                row_index,
            ), cells in row_cells.items()
            if row_artifact == artifact
            and row_table == table_index
            and row_index != header_row
            and any(
                text.strip()
                for texts in cells.values()
                for text in texts
            )
        }
        if len(substantive_rows) != 1:
            reasons.add("CROSS_ROW_DEPENDENCY")
    if any(
        int(row.get("table_count", 0))
        - int(row.get("nested_table_count", 0))
        > 1
        for row in structural
    ):
        reasons.add("MULTIPLE_TOP_LEVEL_TABLES")
    if _CROSS_ROW_RE.search("\n".join(all_text)) or any(
        len(rows) > 1 for rows in designation_rows.values()
    ):
        reasons.add("CROSS_ROW_DEPENDENCY")

    return {
        "schema": WORKER_SUITABILITY_SCHEMA,
        "budget_version": WORKER_BUDGET_VERSION,
        "decision": "partition_required" if reasons else "suitable",
        "reason_codes": sorted(reasons),
        "worker_calls": 0,
        "source_blocks_sha256": expected_digest,
        "observed": {
            "prompt_bytes": prompt_bytes,
            "source_text_bytes": source_text_bytes,
            "source_blocks": len(blocks),
            "max_single_block_bytes": max(block_byte_lengths, default=0),
            "odt_documents": len(structural),
            "designation_candidates": sorted(designation_rows),
            "effective_designation_candidates": sorted(
                effective_designations
            ),
            "collapsed_parent_designations": (
                collapsed_parent_designations
            ),
        },
        "budget": dict(WORKER_BUDGET),
    }


class WorkerOrchestrator:
    """Invoke primary once; invoke fallback once only after a recorded failure."""

    def __init__(
        self,
        *,
        primary: WorkerSpec,
        fallback: WorkerSpec,
        runner: Any = subprocess.run,
    ):
        if primary.worker_id == fallback.worker_id:
            raise ValueError("primary and fallback worker ids must differ")
        self.primary = primary
        self.fallback = fallback
        self._runner = runner

    @staticmethod
    def _archive_attempt_streams(
        run_dir: Path,
        role: str,
        stdout: bytes | None,
        stderr: bytes | None,
    ) -> None:
        """Persist exact process streams before output-contract validation."""

        for stream_name, payload in (("stdout", stdout), ("stderr", stderr)):
            if payload is None:
                continue
            destination = run_dir / f"{role}-{stream_name}.bin"
            temporary = destination.with_suffix(".bin.tmp")
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        directory_fd = os.open(run_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _stream_bytes(value: str | bytes | None) -> bytes | None:
        if value is None:
            return None
        return value if isinstance(value, bytes) else value.encode("utf-8")

    def _invoke(
        self,
        spec: WorkerSpec,
        *,
        role: str,
        prompt: str,
        prompt_sha256: str,
        source_blocks: list[Mapping[str, Any]],
        bundle_id: str,
        bundle_fingerprint: str,
        run_dir: Path,
        primary_attempt_id: str | None,
        fallback_reason: str | None,
        required_true_document_flags: Iterable[str],
        controller_notice: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        started_at = utc_now()
        attempt_id = stable_id(
            "nhi-worker-attempt",
            bundle_id,
            prompt_sha256,
            role,
            spec.worker_id,
        )
        base = {
            "schema": WORKER_ATTEMPT_SCHEMA,
            "attempt_id": attempt_id,
            "role": role,
            "worker_id": spec.worker_id,
            "runtime_id": spec.runtime_id,
            "provider": spec.provider,
            "model": spec.model,
            "prompt_version": WORKER_PROMPT_VERSION,
            "prompt_sha256": prompt_sha256,
            "started_at": started_at,
            "primary_attempt_id": primary_attempt_id,
            "fallback_reason": fallback_reason,
        }
        try:
            completed = self._runner(
                list(spec.command),
                input=prompt.encode("utf-8"),
                text=False,
                capture_output=True,
                timeout=spec.timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
            completed_at = utc_now()
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            stdout_bytes = self._stream_bytes(stdout)
            stderr_bytes = self._stream_bytes(stderr)
            assert stdout_bytes is not None
            assert stderr_bytes is not None
            self._archive_attempt_streams(
                run_dir, role, stdout_bytes, stderr_bytes
            )
            output_sha = sha256_bytes(stdout_bytes)
            stderr_sha = sha256_bytes(stderr_bytes)
            if completed.returncode != 0:
                record = {
                    **base,
                    "completed_at": completed_at,
                    "status": "execution_failed",
                    "exit_code": completed.returncode,
                    "output_sha256": output_sha,
                    "stderr_sha256": stderr_sha,
                    "validation_error_code": None,
                }
                return None, record
            try:
                validated = parse_and_validate_proposal(
                    stdout_bytes,
                    source_blocks=source_blocks,
                    bundle_id=bundle_id,
                    bundle_fingerprint=bundle_fingerprint,
                    required_true_document_flags=required_true_document_flags,
                    expected_notice=controller_notice,
                )
            except ProposalError as exc:
                record = {
                    **base,
                    "completed_at": completed_at,
                    "status": "contract_failed",
                    "exit_code": completed.returncode,
                    "output_sha256": output_sha,
                    "stderr_sha256": stderr_sha,
                    "validation_error_code": type(exc).__name__,
                }
                return None, record
            record = {
                **base,
                "completed_at": completed_at,
                "status": "validated",
                "exit_code": completed.returncode,
                "output_sha256": output_sha,
                "stderr_sha256": stderr_sha,
                "validation_error_code": None,
                "candidate_id": validated["candidate_id"],
            }
            output_path = run_dir / f"{role}-output.json"
            temporary_output = output_path.with_suffix(".json.tmp")
            with temporary_output.open("wb") as stream:
                stream.write(stdout_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_output, output_path)
            return validated, record
        except subprocess.TimeoutExpired as exc:
            stdout_bytes = self._stream_bytes(exc.stdout)
            stderr_bytes = self._stream_bytes(exc.stderr)
            self._archive_attempt_streams(
                run_dir, role, stdout_bytes, stderr_bytes
            )
            record = {
                **base,
                "completed_at": utc_now(),
                "status": "timeout",
                "exit_code": None,
                "output_sha256": (
                    sha256_bytes(stdout_bytes)
                    if stdout_bytes is not None
                    else None
                ),
                "stderr_sha256": (
                    sha256_bytes(stderr_bytes)
                    if stderr_bytes is not None
                    else None
                ),
                "validation_error_code": None,
            }
            return None, record
        except OSError:
            record = {
                **base,
                "completed_at": utc_now(),
                "status": "transport_failed",
                "exit_code": None,
                "output_sha256": None,
                "stderr_sha256": None,
                "validation_error_code": None,
            }
            return None, record

    def run(
        self,
        *,
        bundle_path: Path,
        candidate_root: Path,
    ) -> dict[str, Any]:
        packet = source_packet(bundle_path)
        prompt = build_worker_prompt(packet)
        suitability = assess_worker_suitability(packet, prompt=prompt)
        prompt_sha = sha256_bytes(prompt.encode("utf-8"))
        job_fingerprint = worker_job_fingerprint(
            manifest_sha256=packet["manifest_sha256"],
            prompt_sha256=prompt_sha,
        )
        run_dir = candidate_root / job_fingerprint
        receipt_path = run_dir / "candidate-receipt.json"
        failure_path = run_dir / "failure-receipt.json"
        partition_path = run_dir / "partition-receipt.json"
        prompt_path = run_dir / "prompt.json"
        suitability_path = run_dir / "suitability.json"
        if partition_path.is_file():
            existing_partition = json.loads(
                partition_path.read_text(encoding="utf-8")
            )
            if (
                existing_partition.get("schema") != WORKER_RUN_SCHEMA
                or existing_partition.get("job_fingerprint")
                != job_fingerprint
                or existing_partition.get("bundle_id") != packet["bundle_id"]
                or existing_partition.get("manifest_sha256")
                != packet["manifest_sha256"]
                or existing_partition.get("prompt_sha256") != prompt_sha
                or existing_partition.get("status")
                != "partition_required"
                or existing_partition.get("worker_calls") != 0
                or existing_partition.get("attempt_count") != 0
                or not prompt_path.is_file()
                or prompt_path.read_text(encoding="utf-8") != prompt
                or not suitability_path.is_file()
                or existing_partition.get("suitability_sha256")
                != file_sha256(suitability_path)
                or json.loads(
                    suitability_path.read_text(encoding="utf-8")
                )
                != suitability
            ):
                raise WorkerFailure(
                    "worker partition replay receipt is inconsistent"
                )
            return {**existing_partition, "replayed": True}
        if receipt_path.is_file():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            attempts_path = run_dir / "attempts.jsonl"
            if (
                existing.get("schema") != WORKER_RUN_SCHEMA
                or existing.get("job_fingerprint") != job_fingerprint
                or existing.get("bundle_id") != packet["bundle_id"]
                or existing.get("manifest_sha256")
                != packet["manifest_sha256"]
                or not attempts_path.is_file()
                or existing.get("attempts_sha256")
                != file_sha256(attempts_path)
                or not prompt_path.is_file()
                or prompt_path.read_text(encoding="utf-8") != prompt
                or not suitability_path.is_file()
                or existing.get("suitability_sha256")
                != file_sha256(suitability_path)
            ):
                raise WorkerFailure("worker replay receipt is inconsistent")
            return {**existing, "replayed": True}
        if failure_path.is_file():
            existing_failure = json.loads(
                failure_path.read_text(encoding="utf-8")
            )
            attempts_path = run_dir / "attempts.jsonl"
            attempt_count = (
                sum(1 for line in attempts_path.read_bytes().splitlines() if line)
                if attempts_path.is_file()
                else 0
            )
            if (
                existing_failure.get("schema") != WORKER_RUN_SCHEMA
                or existing_failure.get("job_fingerprint") != job_fingerprint
                or existing_failure.get("bundle_id") != packet["bundle_id"]
                or existing_failure.get("manifest_sha256")
                != packet["manifest_sha256"]
                or not attempts_path.is_file()
                or existing_failure.get("attempts_sha256")
                != file_sha256(attempts_path)
                or not prompt_path.is_file()
                or prompt_path.read_text(encoding="utf-8") != prompt
                or not suitability_path.is_file()
                or existing_failure.get("suitability_sha256")
                != file_sha256(suitability_path)
                or existing_failure.get("status") != "failed"
                or existing_failure.get("attempt_count") != 2
                or existing_failure.get("selected_attempt_id") is not None
                or attempt_count != 2
            ):
                raise WorkerFailure("worker failure replay receipt is inconsistent")
            raise WorkerFailure(
                "primary and fallback already failed for this immutable job"
            )
        if run_dir.exists():
            raise WorkerFailure(
                "worker run is incomplete and requires operator recovery"
            )

        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "prompt.json").write_text(prompt, encoding="utf-8")
        write_json(suitability_path, suitability)
        suitability_sha = file_sha256(suitability_path)
        if suitability["decision"] == "partition_required":
            partition = {
                "schema": WORKER_RUN_SCHEMA,
                "job_fingerprint": job_fingerprint,
                "bundle_id": packet["bundle_id"],
                "bundle_fingerprint": packet["bundle_fingerprint"],
                "manifest_sha256": packet["manifest_sha256"],
                "prompt_sha256": prompt_sha,
                "suitability_sha256": suitability_sha,
                "status": "partition_required",
                "worker_calls": 0,
                "attempt_count": 0,
                "selected_attempt_id": None,
                "selected_role": None,
                "partition_reason_codes": suitability["reason_codes"],
                "candidate": None,
                "replayed": False,
            }
            write_json(partition_path, partition)
            return partition
        required_flags = (
            {"odt_pdf_parity_unverified"}
            if packet["controller_facts"]["contains_pdf"]
            else set()
        )
        controller_notice = packet["notice_binding_source"]
        validated, primary_record = self._invoke(
            self.primary,
            role="primary",
            prompt=prompt,
            prompt_sha256=prompt_sha,
            source_blocks=packet["source_blocks"],
            bundle_id=packet["bundle_id"],
            bundle_fingerprint=packet["bundle_fingerprint"],
            run_dir=run_dir,
            primary_attempt_id=None,
            fallback_reason=None,
            required_true_document_flags=required_flags,
            controller_notice=controller_notice,
        )
        append_jsonl(run_dir / "attempts.jsonl", primary_record)
        selected_record = primary_record
        if validated is None:
            fallback_reason = primary_record["status"]
            validated, fallback_record = self._invoke(
                self.fallback,
                role="fallback",
                prompt=prompt,
                prompt_sha256=prompt_sha,
                source_blocks=packet["source_blocks"],
                bundle_id=packet["bundle_id"],
                bundle_fingerprint=packet["bundle_fingerprint"],
                run_dir=run_dir,
                primary_attempt_id=primary_record["attempt_id"],
                fallback_reason=fallback_reason,
                required_true_document_flags=required_flags,
                controller_notice=controller_notice,
            )
            append_jsonl(run_dir / "attempts.jsonl", fallback_record)
            selected_record = fallback_record
        if validated is None:
            failure = {
                "schema": WORKER_RUN_SCHEMA,
                "job_fingerprint": job_fingerprint,
                "bundle_id": packet["bundle_id"],
                "bundle_fingerprint": packet["bundle_fingerprint"],
                "manifest_sha256": packet["manifest_sha256"],
                "attempts_sha256": file_sha256(run_dir / "attempts.jsonl"),
                "prompt_sha256": prompt_sha,
                "suitability_sha256": suitability_sha,
                "status": "failed",
                "worker_calls": 2,
                "attempt_count": 2,
                "selected_attempt_id": None,
            }
            write_json(run_dir / "failure-receipt.json", failure)
            raise WorkerFailure("primary and fallback worker attempts failed")

        receipt = {
            "schema": WORKER_RUN_SCHEMA,
            "job_fingerprint": job_fingerprint,
            "bundle_id": packet["bundle_id"],
            "bundle_fingerprint": packet["bundle_fingerprint"],
            "manifest_sha256": packet["manifest_sha256"],
            "attempts_sha256": file_sha256(run_dir / "attempts.jsonl"),
            "prompt_sha256": prompt_sha,
            "suitability_sha256": suitability_sha,
            "status": "staged",
            "attempt_count": 1 if primary_record["status"] == "validated" else 2,
            "worker_calls": (
                1 if primary_record["status"] == "validated" else 2
            ),
            "selected_attempt_id": selected_record["attempt_id"],
            "selected_role": selected_record["role"],
            "candidate": validated,
            "replayed": False,
        }
        write_json(receipt_path, receipt)
        return receipt
