"""One-primary/one-fallback worker orchestration for bounded source proposals."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from nhi_rule_history.contracts import (
    ContractError,
    append_jsonl,
    canonical_json_bytes,
    sha256_bytes,
    stable_id,
    utc_now,
    write_json,
)
from nhi_rule_history.update.bundle import verify_bundle
from nhi_rule_history.update.notice import extract_notice_metadata
from nhi_rule_history.update.odt import extract_odt_blocks
from nhi_rule_history.update.proposal import (
    PROPOSAL_SCHEMA,
    ProposalError,
    parse_and_validate_proposal,
)


WORKER_PROMPT_VERSION = "nhi-rule-history-source-proposal/1.0.0"
WORKER_ATTEMPT_SCHEMA = "nhi-rule-history/worker-attempt/v1"
WORKER_RUN_SCHEMA = "nhi-rule-history/worker-run/v1"


class WorkerFailure(RuntimeError):
    """Both bounded worker attempts failed or violated the proposal contract."""


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


def source_packet(bundle_path: Path) -> dict[str, Any]:
    verification = verify_bundle(bundle_path)
    manifest = json.loads(
        (bundle_path / "manifest.json").read_text(encoding="utf-8")
    )
    blocks: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    has_pdf = False
    has_odt = False
    notice_metadata: dict[str, Any] | None = None
    for resource in manifest["resources"]:
        if resource["relation"] == "detail_page":
            payload = (bundle_path / resource["content_path"]).read_bytes()
            notice_metadata = extract_notice_metadata(
                payload, resource["artifact_sha256"]
            )
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
            blocks.extend(
                extract_odt_blocks(payload, resource["artifact_sha256"])
            )
    if not has_odt or not blocks:
        raise ContractError(
            "first worker lane requires at least one parseable ODT attachment"
        )
    if notice_metadata is None:
        raise ContractError("source packet is missing deterministic notice metadata")
    if len({row["block_id"] for row in blocks}) != len(blocks):
        raise ContractError("source packet contains duplicate block identities")
    return {
        "schema": "nhi-rule-history/worker-source-packet/v1",
        "bundle_id": verification["bundle_id"],
        "bundle_fingerprint": verification["bundle_fingerprint"],
        "rss_item": manifest["rss_item"],
        "notice_metadata": notice_metadata,
        "attachment_inventory": inventory,
        "controller_facts": {
            "declared_attachment_coverage_complete": True,
            "contains_pdf": has_pdf,
            "contains_odt": has_odt,
            "odt_pdf_parity_verified": False,
            "canonical_promotion_enabled": False,
        },
        "source_blocks": blocks,
    }


def build_worker_prompt(packet: Mapping[str, Any]) -> str:
    """Build a self-contained public-source packet with an exact JSON contract."""

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
            "notice.reference_number_raw and notice.subject_raw must exactly "
            "equal source_packet.notice_metadata values.",
        ],
        "schema": {
            "schema": PROPOSAL_SCHEMA,
            "notice": {
                "reference_number_raw": "string, empty if absent",
                "subject_raw": "string, empty if absent",
            },
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
            "instructions": instructions,
            "source_packet": packet,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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
        packet_notice: Mapping[str, Any],
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
                input=prompt,
                text=True,
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
                    stdout,
                    source_blocks=source_blocks,
                    bundle_id=bundle_id,
                    bundle_fingerprint=bundle_fingerprint,
                    required_true_document_flags=required_true_document_flags,
                    expected_notice={
                        "reference_number_raw": packet_notice[
                            "reference_number_raw"
                        ],
                        "subject_raw": packet_notice["subject_raw"],
                    },
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
        prompt_sha = sha256_bytes(prompt.encode("utf-8"))
        job_fingerprint = stable_id(
            "nhi-worker-job",
            packet["bundle_id"],
            packet["bundle_fingerprint"],
            WORKER_PROMPT_VERSION,
            prompt_sha,
        )
        run_dir = candidate_root / job_fingerprint
        receipt_path = run_dir / "candidate-receipt.json"
        failure_path = run_dir / "failure-receipt.json"
        if receipt_path.is_file():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                existing.get("schema") != WORKER_RUN_SCHEMA
                or existing.get("job_fingerprint") != job_fingerprint
                or existing.get("bundle_id") != packet["bundle_id"]
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
        required_flags = (
            {"odt_pdf_parity_unverified"}
            if packet["controller_facts"]["contains_pdf"]
            else set()
        )
        packet_notice = packet["notice_metadata"]
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
            packet_notice=packet_notice,
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
                packet_notice=packet_notice,
            )
            append_jsonl(run_dir / "attempts.jsonl", fallback_record)
            selected_record = fallback_record
        if validated is None:
            failure = {
                "schema": WORKER_RUN_SCHEMA,
                "job_fingerprint": job_fingerprint,
                "bundle_id": packet["bundle_id"],
                "bundle_fingerprint": packet["bundle_fingerprint"],
                "prompt_sha256": prompt_sha,
                "status": "failed",
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
            "prompt_sha256": prompt_sha,
            "status": "staged",
            "attempt_count": 1 if primary_record["status"] == "validated" else 2,
            "selected_attempt_id": selected_record["attempt_id"],
            "selected_role": selected_record["role"],
            "candidate": validated,
            "replayed": False,
        }
        write_json(receipt_path, receipt)
        return receipt
