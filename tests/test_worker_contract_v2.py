from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import nhi_rule_history.update.workers as worker_contract
from nhi_rule_history.contracts import sha256_bytes
from nhi_rule_history.update.proposal import (
    PROPOSAL_SCHEMA,
    WORKER_JSON_MAX_DEPTH,
    ProposalError,
    controller_notice_binding,
    parse_and_validate_proposal,
    validate_proposal,
)
from nhi_rule_history.update.workers import (
    WORKER_BUDGET,
    WorkerOrchestrator,
    WorkerSpec,
    assess_worker_suitability,
    build_worker_prompt,
    source_blocks_digest,
    worker_job_fingerprint,
)


ARTIFACT_SHA = "a" * 64
NOTICE = {
    "reference_number_raw": "健保審字第1150000000號",
    "subject_raw": "修訂藥品給付規定",
}
NOTICE_METADATA = {
    **NOTICE,
    "reference_number_normalized": "健保審字第1150000000號",
    "reference_number_normalization": "exact",
    "reference_number_normalization_rule": (
        "nhi-reference-number-normalization/1.0.0"
    ),
    "document_date_roc_raw": "115-07-20",
    "publication_date_roc_raw": "115-07-20",
    "update_date_roc_raw": "115-07-20",
    "announcement_text_raw": "修訂對照表如附件。",
}


def notice_source() -> dict[str, object]:
    return {
        "bundle_id": "bundle-1",
        "bundle_fingerprint": "b" * 64,
        "detail_artifact_sha256": "d" * 64,
        "request_url": "https://www.nhi.gov.tw/ch/cp-test-3258-1.html",
        "final_url": "https://www.nhi.gov.tw/ch/cp-test-3258-1.html",
        "metadata": dict(NOTICE_METADATA),
    }


def _block(
    index: int,
    text: str,
    *,
    table_index: int | None,
    row_index: int | None,
    cell_index: int | None,
) -> dict[str, object]:
    locator = {
        "kind": "paragraph" if table_index is None else "table_cell",
        "document_order": index,
        "table_index": table_index,
        "table_depth": None if table_index is None else 0,
        "parent_table_index": None,
        "row_index": row_index,
        "row_kind": None if table_index is None else "body",
        "cell_index": cell_index,
        "paragraph_index": None if table_index is None else 0,
    }
    return {
        "block_id": sha256_bytes(f"block:{index}:{text}".encode()),
        "artifact_sha256": ARTIFACT_SHA,
        "locator": locator,
        "raw_text": text,
        "raw_text_sha256": sha256_bytes(text.encode()),
    }


def packet() -> dict[str, object]:
    blocks = [
        _block(
            0,
            "(自115年8月1日生效)",
            table_index=None,
            row_index=None,
            cell_index=None,
        ),
        _block(
            1,
            "建議修訂後給付規定",
            table_index=0,
            row_index=0,
            cell_index=0,
        ),
        _block(
            2,
            "原給付規定",
            table_index=0,
            row_index=0,
            cell_index=1,
        ),
        _block(
            3,
            "9.4 新條文完整文字",
            table_index=0,
            row_index=1,
            cell_index=0,
        ),
        _block(
            4,
            "9.4 舊條文完整文字",
            table_index=0,
            row_index=1,
            cell_index=1,
        ),
    ]
    return {
        "schema": worker_contract.WORKER_SOURCE_PACKET_SCHEMA,
        "bundle_id": "bundle-1",
        "bundle_fingerprint": "b" * 64,
        "manifest_sha256": "c" * 64,
        "rss_item": {"title": "controller-only"},
        "notice_metadata": dict(NOTICE_METADATA),
        "notice_binding_source": notice_source(),
        "notice_binding": controller_notice_binding(notice_source()),
        "attachment_inventory": [
            {
                "artifact_sha256": ARTIFACT_SHA,
                "media_type": (
                    "application/vnd.oasis.opendocument.text"
                ),
                "declared_sequence": 0,
                "declared_label": "修訂對照表",
            }
        ],
        "structural_facts": [
            {
                "schema": worker_contract.WORKER_EXTRACTION_VERSION,
                "artifact_sha256": ARTIFACT_SHA,
                "table_count": 1,
                "nested_table_count": 0,
                "max_table_depth": 0,
                "row_count": 2,
                "cell_count": 4,
                "covered_cell_count": 0,
                "column_span_cell_count": 0,
                "row_span_cell_count": 0,
                "repeated_cell_count": 0,
                "repeated_row_count": 0,
                "source_paragraph_count": 5,
                "emitted_block_count": 5,
                "exact_once_verified": True,
            }
        ],
        "source_blocks_sha256": source_blocks_digest(blocks),
        "semantic_contract": {
            "extraction_version": worker_contract.WORKER_EXTRACTION_VERSION,
            "block_digest_version": (
                worker_contract.WORKER_BLOCK_DIGEST_VERSION
            ),
            "prompt_template_version": (
                worker_contract.WORKER_PROMPT_VERSION
            ),
            "proposal_schema": PROPOSAL_SCHEMA,
            "runtime_contract_version": (
                worker_contract.WORKER_RUNTIME_CONTRACT_VERSION
            ),
            "budget_version": worker_contract.WORKER_BUDGET_VERSION,
        },
        "controller_facts": {
            "declared_attachment_coverage_complete": True,
            "contains_pdf": False,
            "contains_odt": True,
            "odt_pdf_parity_verified": False,
            "canonical_promotion_enabled": False,
        },
        "source_blocks": blocks,
    }


def _span(block: dict[str, object]) -> dict[str, object]:
    text = str(block["raw_text"])
    return {
        "artifact_sha256": block["artifact_sha256"],
        "block_id": block["block_id"],
        "start": 0,
        "end": len(text),
        "exact_text": text,
        "exact_text_sha256": sha256_bytes(text.encode()),
    }


def proposal(source_packet: dict[str, object]) -> dict[str, object]:
    blocks = source_packet["source_blocks"]
    assert isinstance(blocks, list)
    return {
        "schema": PROPOSAL_SCHEMA,
        "temporal_evidence": [
            {
                "source_span": _span(blocks[0]),
                "expression_raw": "115年8月1日",
                "calendar": "ROC",
                "precision": "day",
                "semantic_role": "effective_from",
                "scope_raw": "本比較表",
                "conditionality": "unconditional",
                "iso_date_candidate": "2026-08-01",
            }
        ],
        "effect_candidates": [
            {
                "designation_raw": "9.4",
                "parent_chapter_raw": "第9章",
                "comparison_kind_hint": "full_replacement",
                "old_text_spans": [_span(blocks[4])],
                "new_text_spans": [_span(blocks[3])],
                "scope_count": 1,
                "comparison_row_count": 1,
                "review_flags": {
                    "omitted_text": False,
                    "merged_cells": False,
                    "cross_row_dependency": False,
                    "partial_patch": False,
                    "multi_rule": False,
                    "correction": False,
                    "same_url_different_bytes": False,
                    "odt_pdf_disagreement": False,
                    "identity_uncertainty": False,
                },
            }
        ],
        "document_flags": {
            "correction_notice": False,
            "same_url_different_bytes": False,
            "odt_pdf_disagreement": False,
            "odt_pdf_parity_unverified": False,
            "declared_attachment_coverage_uncertain": False,
        },
        "model_assessment": "single_full_replacement_candidate",
        "reason_codes": ["FULL_SINGLE_CLAUSE_SHAPE"],
    }


class WorkerContractV2Tests(unittest.TestCase):
    def test_worker_prompt_excludes_controller_notice_values(self) -> None:
        value = packet()
        prompt = build_worker_prompt(value)
        self.assertNotIn(NOTICE["reference_number_raw"], prompt)
        self.assertNotIn(NOTICE["subject_raw"], prompt)
        self.assertNotIn("controller-only", prompt)
        parsed = json.loads(prompt)
        self.assertNotIn("notice_metadata", parsed["source_packet"])
        self.assertNotIn("notice_binding_source", parsed["source_packet"])
        self.assertNotIn("notice_binding", parsed["source_packet"])
        self.assertEqual(
            parsed["budget"],
            {
                "prompt_bytes": 65_536,
                "source_text_bytes": 32_768,
                "source_blocks": 96,
                "single_block_bytes": 8_192,
                "output_bytes": 131_072,
                "json_depth": 16,
            },
        )

    def test_controller_adds_notice_binding_after_worker_validation(self) -> None:
        source_packet = packet()
        worker_output = proposal(source_packet)
        self.assertNotIn("notice", worker_output)
        candidate = validate_proposal(
            worker_output,
            source_blocks=source_packet["source_blocks"],
            bundle_id=str(source_packet["bundle_id"]),
            bundle_fingerprint=str(
                source_packet["bundle_fingerprint"]
            ),
            expected_notice=notice_source(),
        )
        self.assertEqual(
            candidate["notice_binding"],
            controller_notice_binding(notice_source()),
        )
        changed_source = notice_source()
        changed_source["metadata"]["update_date_roc_raw"] = "115-07-21"
        self.assertNotEqual(
            candidate["notice_binding"]["binding_sha256"],
            controller_notice_binding(changed_source)["binding_sha256"],
        )
        self.assertNotIn("notice", candidate["proposal"])

    def test_recursive_forbidden_key_scan_catches_nested_smuggling(self) -> None:
        source_packet = packet()
        worker_output = proposal(source_packet)
        worker_output["reason_codes"] = [
            {
                "harmless_wrapper": {
                    "notice_binding": {"subject_raw": "forged"}
                }
            }
        ]
        with self.assertRaisesRegex(
            ProposalError,
            "authority fields.*notice_binding.*subject_raw",
        ):
            validate_proposal(
                worker_output,
                source_blocks=source_packet["source_blocks"],
                bundle_id=str(source_packet["bundle_id"]),
                bundle_fingerprint=str(
                    source_packet["bundle_fingerprint"]
                ),
                expected_notice=notice_source(),
            )

    def test_output_byte_and_json_depth_budgets_fail_closed(self) -> None:
        source_packet = packet()
        oversized = b"{" + b" " * WORKER_BUDGET["output_bytes"] + b"}"
        with self.assertRaisesRegex(ProposalError, "exceeds"):
            parse_and_validate_proposal(
                oversized,
                source_blocks=source_packet["source_blocks"],
                bundle_id="bundle-1",
                bundle_fingerprint="b" * 64,
                expected_notice=notice_source(),
            )
        nested: object = 0
        for _ in range(WORKER_JSON_MAX_DEPTH + 1):
            nested = [nested]
        with self.assertRaisesRegex(ProposalError, "maximum depth"):
            parse_and_validate_proposal(
                json.dumps({"nested": nested}).encode(),
                source_blocks=source_packet["source_blocks"],
                bundle_id="bundle-1",
                bundle_fingerprint="b" * 64,
                expected_notice=notice_source(),
            )

    def test_preflight_partitions_each_forbidden_document_shape(self) -> None:
        cases = {}

        multi_rule = packet()
        multi_rule["source_blocks"][3]["raw_text"] = (
            "9.4 新條文完整文字\n9.5 另一條新條文"
        )
        multi_rule["source_blocks"][3]["raw_text_sha256"] = sha256_bytes(
            multi_rule["source_blocks"][3]["raw_text"].encode()
        )
        multi_rule["source_blocks_sha256"] = source_blocks_digest(
            multi_rule["source_blocks"]
        )
        cases["MULTI_RULE_DOCUMENT"] = multi_rule

        merged = packet()
        merged["structural_facts"][0]["column_span_cell_count"] = 1
        cases["MERGED_TABLE_STRUCTURE"] = merged

        omitted = packet()
        omitted["source_blocks"][3]["raw_text"] = "9.4 新條文（略）"
        omitted["source_blocks"][3]["raw_text_sha256"] = sha256_bytes(
            omitted["source_blocks"][3]["raw_text"].encode()
        )
        omitted["source_blocks_sha256"] = source_blocks_digest(
            omitted["source_blocks"]
        )
        cases["OMITTED_TEXT_PRESENT"] = omitted

        cross_row = packet()
        extra = _block(
            5,
            "9.4 續前列",
            table_index=0,
            row_index=2,
            cell_index=0,
        )
        cross_row["source_blocks"].append(extra)
        cross_row["source_blocks_sha256"] = source_blocks_digest(
            cross_row["source_blocks"]
        )
        cases["CROSS_ROW_DEPENDENCY"] = cross_row

        oversized = packet()
        oversized["source_blocks"][3]["raw_text"] = (
            "9.4 " + "文" * (WORKER_BUDGET["single_block_bytes"] + 1)
        )
        oversized["source_blocks"][3]["raw_text_sha256"] = sha256_bytes(
            oversized["source_blocks"][3]["raw_text"].encode()
        )
        oversized["source_blocks_sha256"] = source_blocks_digest(
            oversized["source_blocks"]
        )
        cases["SINGLE_BLOCK_BUDGET_EXCEEDED"] = oversized

        for reason, source_packet in cases.items():
            with self.subTest(reason=reason):
                result = assess_worker_suitability(source_packet)
                self.assertEqual(result["decision"], "partition_required")
                self.assertEqual(result["worker_calls"], 0)
                self.assertIn(reason, result["reason_codes"])

    def test_partition_receipt_is_replayed_without_worker_calls(self) -> None:
        source_packet = packet()
        source_packet["structural_facts"][0]["covered_cell_count"] = 1
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("worker must not be called")

        orchestrator = WorkerOrchestrator(
            primary=WorkerSpec(
                "primary",
                "primary-v1",
                "provider-a",
                "model-a",
                ("primary",),
            ),
            fallback=WorkerSpec(
                "fallback",
                "fallback-v1",
                "provider-b",
                "model-b",
                ("fallback",),
            ),
            runner=runner,
        )
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            worker_contract,
            "source_packet",
            return_value=source_packet,
        ):
            root = Path(temporary)
            first = orchestrator.run(
                bundle_path=root / "unused",
                candidate_root=root / "candidates",
            )
            second = orchestrator.run(
                bundle_path=root / "unused",
                candidate_root=root / "candidates",
            )
            self.assertEqual(first["status"], "partition_required")
            self.assertEqual(first["worker_calls"], 0)
            self.assertTrue(second["replayed"])
            self.assertEqual(calls, [])

    def test_raw_worker_bytes_survive_contract_failure(self) -> None:
        source_packet = packet()
        valid_bytes = json.dumps(
            proposal(source_packet),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        raw_invalid = b"\xff\xfe-not-utf8"
        calls = 0

        def runner(_argv, **_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                stdout=raw_invalid if calls == 1 else valid_bytes,
                stderr=b"\x80stderr" if calls == 1 else b"",
                returncode=0,
            )

        orchestrator = WorkerOrchestrator(
            primary=WorkerSpec(
                "primary",
                "primary-v1",
                "provider-a",
                "model-a",
                ("primary",),
            ),
            fallback=WorkerSpec(
                "fallback",
                "fallback-v1",
                "provider-b",
                "model-b",
                ("fallback",),
            ),
            runner=runner,
        )
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            worker_contract,
            "source_packet",
            return_value=source_packet,
        ):
            root = Path(temporary)
            receipt = orchestrator.run(
                bundle_path=root / "unused",
                candidate_root=root / "candidates",
            )
            run_dir = (
                root / "candidates" / receipt["job_fingerprint"]
            )
            self.assertEqual(
                (run_dir / "primary-stdout.bin").read_bytes(),
                raw_invalid,
            )
            self.assertEqual(
                (run_dir / "primary-stderr.bin").read_bytes(),
                b"\x80stderr",
            )
            self.assertEqual(
                (run_dir / "fallback-stdout.bin").read_bytes(),
                valid_bytes,
            )
            self.assertEqual(receipt["worker_calls"], 2)

    def test_semantic_fingerprint_binds_contract_versions_and_blocks(self) -> None:
        source_packet = packet()
        prompt_sha = sha256_bytes(
            build_worker_prompt(source_packet).encode()
        )
        baseline = worker_job_fingerprint(
            manifest_sha256="c" * 64,
            prompt_sha256=prompt_sha,
        )
        for name in (
            "WORKER_EXTRACTION_VERSION",
            "WORKER_BLOCK_DIGEST_VERSION",
            "WORKER_PROMPT_VERSION",
            "WORKER_RUNTIME_CONTRACT_VERSION",
            "WORKER_BUDGET_VERSION",
            "PROPOSAL_SCHEMA",
        ):
            with self.subTest(name=name), mock.patch.object(
                worker_contract,
                name,
                getattr(worker_contract, name) + "-next",
            ):
                self.assertNotEqual(
                    baseline,
                    worker_job_fingerprint(
                        manifest_sha256="c" * 64,
                        prompt_sha256=prompt_sha,
                    ),
                )
        changed = deepcopy(source_packet)
        changed["source_blocks"][3]["raw_text"] += "修"
        changed["source_blocks"][3]["raw_text_sha256"] = sha256_bytes(
            changed["source_blocks"][3]["raw_text"].encode()
        )
        changed["source_blocks_sha256"] = source_blocks_digest(
            changed["source_blocks"]
        )
        changed_prompt_sha = sha256_bytes(
            build_worker_prompt(changed).encode()
        )
        self.assertNotEqual(prompt_sha, changed_prompt_sha)
        self.assertNotEqual(
            baseline,
            worker_job_fingerprint(
                manifest_sha256="c" * 64,
                prompt_sha256=changed_prompt_sha,
            ),
        )


if __name__ == "__main__":
    unittest.main()
