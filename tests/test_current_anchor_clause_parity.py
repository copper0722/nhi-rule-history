from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import file_sha256
from nhi_rule_history.current_anchor_clause_parity import (
    NON_CLAIM_STATEMENT,
    analyze_current_anchor_clause_parity,
)


RUN_ID = "22222222-2222-4222-8222-222222222222"
WHOLE_LABEL = "最新版藥品給付規定內容(整份帶走)-115.7.23更新"
GENERAL_LABEL = "通則(113.05.28更新)"
SECTION_LABEL = "第一節 測試藥物"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _block(
    artifact: str,
    label: str,
    resource: str,
    order: int,
    raw_text: str,
    *,
    container: str = "flow",
    block_kind: str = "paragraph",
) -> dict[str, Any]:
    locator = {
        "container": container,
        "doc_order": order,
        "element": "p",
        "in_frame": 0,
        "in_index_context": 0,
        "list_depth": 1 if container == "list" else 0,
        "nested_table_depth": 1 if container == "table_cell" else 0,
        "style_name": "P1",
        "xml_element_index": order + 100,
    }
    locator_key = "|".join(f"{key}={value}" for key, value in locator.items())
    return {
        "schema": "nhi-rule-history/structural-block/v2",
        "parse_run_id": RUN_ID,
        "artifact_sha256": artifact,
        "block_id": _sha(f"{artifact}:block:{order}"),
        "block_kind": block_kind,
        "container": container,
        "element_name": "p",
        "in_index_context": False,
        "in_table": container == "table_cell",
        "locator": locator,
        "locator_key": locator_key,
        "normalized_search_text": raw_text,
        "raw_text": raw_text,
        "raw_text_byte_length": len(raw_text.encode("utf-8")),
        "raw_text_char_length": len(raw_text),
        "raw_text_sha256": _sha(raw_text),
        "source_labels": [label],
        "source_resource_ids": [resource],
        "source_row_sha256": _sha(f"{artifact}:source-row:{order}"),
    }


def _occurrence(
    block: dict[str, Any],
    designation: str,
) -> dict[str, Any]:
    raw = block["raw_text"]
    start = raw.index(designation)
    end = start + len(designation)
    return {
        "schema": "nhi-rule-history/occurrence-candidate/v2",
        "parse_run_id": RUN_ID,
        "artifact_sha256": block["artifact_sha256"],
        "block_id": block["block_id"],
        "container": block["container"],
        "designation_text": designation,
        "in_index_context": False,
        "locator": block["locator"],
        "locator_key": block["locator_key"],
        "match_start_in_raw": start,
        "match_end_in_raw": end,
        "normalized_search_text": raw,
        "occurrence_id": _sha(f"{block['block_id']}:{designation}"),
        "raw_text": raw,
        "raw_text_sha256": block["raw_text_sha256"],
        "source_labels": block["source_labels"],
        "source_resource_ids": block["source_resource_ids"],
        "source_row_sha256": _sha(
            f"{block['block_id']}:{designation}:source-row"
        ),
    }


def _issue(
    artifact: str,
    code: str,
    *,
    blocking: bool = False,
    severity: str = "info",
    block_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "nhi-rule-history/parse-issue/v2",
        "parse_run_id": RUN_ID,
        "artifact_sha256": artifact,
        "blocking": blocking,
        "severity": severity,
        "issue_code": code,
        "issue_id": _sha(f"{artifact}:{code}"),
        "message_parameters": {
            "detail": "test fixture",
            **({"block_id": block_id} if block_id is not None else {}),
        },
        "source_row_sha256": _sha(f"{artifact}:{code}:source-row"),
    }


def _artifact(
    name: str,
    label: str,
    texts: list[tuple[str, str, str]],
) -> dict[str, Any]:
    artifact = _sha(f"artifact:{name}")
    resource = _sha(f"resource:{name}")
    blocks = [
        _block(
            artifact,
            label,
            resource,
            order,
            raw_text,
            container=container,
            block_kind=block_kind,
        )
        for order, (raw_text, container, block_kind) in enumerate(texts)
    ]
    return {
        "sha": artifact,
        "resource": resource,
        "label": label,
        "blocks": blocks,
        "occurrences": [],
        "issues": [],
    }


def _base_artifacts() -> list[dict[str, Any]]:
    whole = _artifact(
        "whole",
        WHOLE_LABEL,
        [
            ("藥品給付規定通則", "flow", "paragraph"),
            ("一、甲", "flow", "paragraph"),
            ("甲內容", "flow", "paragraph"),
            ("二、乙", "flow", "paragraph"),
            ("乙 內 容", "flow", "paragraph"),
            ("第1節 測試藥物", "flow", "paragraph"),
            ("1.1. A", "flow", "paragraph"),
            ("parent intro", "flow", "paragraph"),
            ("表格值", "table_cell", "table_cell"),
            ("清單", "list", "list_item"),
            ("1.1.1. B", "flow", "paragraph"),
            ("child body", "flow", "paragraph"),
            ("1.2 C", "flow", "paragraph"),
            ("next body", "flow", "paragraph"),
            ("附表一", "flow", "paragraph"),
        ],
    )
    general = _artifact(
        "general",
        GENERAL_LABEL,
        [
            ("通則", "flow", "paragraph"),
            ("一、甲", "flow", "paragraph"),
            ("甲內容", "flow", "paragraph"),
            ("二、乙", "flow", "paragraph"),
            ("乙", "flow", "paragraph"),
            (" 內 容", "flow", "paragraph"),
        ],
    )
    section = _artifact(
        "section-1",
        SECTION_LABEL,
        [
            ("第1節 測試藥物", "flow", "paragraph"),
            ("1.1. A", "flow", "paragraph"),
            ("parent intro", "flow", "paragraph"),
            ("表格值", "table_cell", "table_cell"),
            ("清單", "list", "list_item"),
            ("1.1.1. B", "flow", "paragraph"),
            ("child body", "flow", "paragraph"),
            ("1.2 C", "flow", "paragraph"),
            ("next body", "flow", "paragraph"),
        ],
    )
    for item, orders in (
        (whole, (6, 10, 12)),
        (section, (1, 5, 7)),
    ):
        item["occurrences"] = [
            _occurrence(item["blocks"][order], designation)
            for order, designation in zip(
                orders, ("1.1", "1.1.1", "1.2"), strict=True
            )
        ]
    return [whole, general, section]


def _write_stage(
    root: Path,
    artifacts: list[dict[str, Any]],
) -> Path:
    blocks = [
        row for artifact in artifacts for row in artifact["blocks"]
    ]
    occurrences = [
        row for artifact in artifacts for row in artifact["occurrences"]
    ]
    issues = [
        row for artifact in artifacts for row in artifact["issues"]
    ]
    rows_by_file = {
        "structural-blocks.jsonl": blocks,
        "occurrence-candidates.jsonl": occurrences,
        "parse-issues.jsonl": issues,
    }
    file_entries = []
    for filename, rows in rows_by_file.items():
        path = root / filename
        path.write_text(
            "".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        file_entries.append(
            {
                "filename": filename,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "status": "passed",
        "parse_run_id": RUN_ID,
        "fidelity_class": "lossless_structural",
        "closure_claims": {
            "declared_odt_artifacts_exhausted": True,
            "structural_text_coverage_checked_per_odt": True,
            "semantic_history_complete": False,
        },
        "counts": {
            "blocking_issues": sum(
                1 for row in issues if row["blocking"]
            ),
            "declared_odt_artifacts": len(artifacts),
            "declared_odt_resources": len(artifacts),
            "parsed_odt_artifacts": len(artifacts),
            "structural_blocks": len(blocks),
            "occurrence_candidates": len(occurrences),
            "parse_issues": len(issues),
        },
        "parsed_artifacts": [
            {
                "artifact_sha256": artifact["sha"],
                "block_count": len(artifact["blocks"]),
                "occurrence_count": len(artifact["occurrences"]),
                "parse_issue_count": len(artifact["issues"]),
                "resource_ids": [artifact["resource"]],
                "source_labels": [artifact["label"]],
            }
            for artifact in artifacts
        ],
        "files": file_entries,
    }
    (root / "structural-manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return root


class CurrentAnchorClauseParityTests(unittest.TestCase):
    def _report(
        self,
        mutate: Any | None = None,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = _base_artifacts()
            if mutate is not None:
                mutate(artifacts)
            stage = _write_stage(Path(temporary), artifacts)
            return analyze_current_anchor_clause_parity(stage)

    def test_full_clause_parity_preserves_table_and_list_blocks(self) -> None:
        report = self._report()
        self.assertEqual(report["status"], "parity_passed")
        self.assertEqual(report["whole"]["clause_count"], 5)
        self.assertEqual(report["split"]["clause_count"], 5)
        parent = next(
            clause
            for clause in report["whole"]["clauses"]
            if clause["designation"] == "1.1"
        )
        self.assertEqual(
            parent["source_span"]["container_counts"]["table_cell"], 1
        )
        self.assertEqual(
            parent["source_span"]["container_counts"]["list"], 1
        )
        self.assertTrue(report["claims"]["full_clause_text_compared"])
        self.assertFalse(report["claims"]["historical_completeness"])
        self.assertEqual(report["non_claim_statement"], NON_CLAIM_STATEMENT)

    def test_safe_reconstruction_can_report_real_text_mismatch(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            block = artifacts[2]["blocks"][8]
            block["raw_text"] = "changed body"
            block["raw_text_sha256"] = _sha("changed body")

        report = self._report(mutate)
        self.assertEqual(report["status"], "parity_failed")
        self.assertTrue(report["claims"]["full_clause_text_compared"])
        self.assertFalse(report["claims"]["current_anchor_parity"])
        self.assertEqual(report["comparison"]["whole_only_count"], 1)
        self.assertEqual(report["comparison"]["split_only_count"], 1)
        self.assertEqual(
            report["comparison"]["whole_only"][0]["designation"],
            "1.2",
        )
        self.assertEqual(
            report["comparison"]["split_only"][0]["designation"],
            "1.2",
        )
        self.assertEqual(
            report["comparison"]["leafmost_mismatch_designations"],
            [{"membership": "section:1", "designation": "1.2"}],
        )

    def test_duplicate_strict_designation_blocks(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            section = artifacts[2]
            block = section["blocks"][8]
            block["raw_text"] = "1.1. Duplicate"
            block["raw_text_sha256"] = _sha(block["raw_text"])
            section["occurrences"].append(_occurrence(block, "1.1"))

        report = self._report(mutate)
        self.assertEqual(report["status"], "blocked_preflight")
        self.assertEqual(report["blockers"][0]["code"], "DUPLICATE_DESIGNATION")

    def test_missing_source_membership_blocks(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            artifacts[2]["blocks"][3]["source_resource_ids"] = []

        report = self._report(mutate)
        self.assertEqual(report["status"], "blocked_preflight")
        self.assertEqual(
            report["blockers"][0]["code"], "SOURCE_MEMBERSHIP_MISSING"
        )

    def test_occurrence_without_structural_block_blocks(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            artifacts[2]["occurrences"][0]["block_id"] = _sha("missing")

        report = self._report(mutate)
        self.assertEqual(report["status"], "blocked_preflight")
        self.assertEqual(
            report["blockers"][0]["code"], "OCCURRENCE_BLOCK_MISSING"
        )

    def test_unknown_text_altering_parse_issue_blocks(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            section = artifacts[2]
            section["issues"].append(
                _issue(section["sha"], "text_truncated", severity="warning")
            )

        report = self._report(mutate)
        self.assertEqual(report["status"], "blocked_preflight")
        self.assertEqual(
            report["blockers"][0]["code"],
            "TEXT_ALTERING_PARSE_ISSUE_UNRESOLVED",
        )

    def test_retained_numeric_rejection_is_reviewed_not_silently_ignored(
        self,
    ) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            section = artifacts[2]
            section["issues"].append(
                _issue(
                    section["sha"],
                    "numeric_quantity_rejected_from_occurrence",
                    block_id=section["blocks"][2]["block_id"],
                )
            )

        report = self._report(mutate)
        self.assertEqual(report["status"], "parity_passed")
        self.assertEqual(
            report["reviewed_nonblocking_parse_issues"][0]["disposition"],
            "structural_block_retained",
        )

    def test_duplicate_split_membership_blocks(self) -> None:
        def mutate(artifacts: list[dict[str, Any]]) -> None:
            duplicate = _artifact(
                "section-1-duplicate",
                "第一節 另一份",
                [("第1節 另一份", "flow", "paragraph")],
            )
            artifacts.append(duplicate)

        report = self._report(mutate)
        self.assertEqual(report["status"], "blocked_preflight")
        self.assertEqual(
            report["blockers"][0]["code"], "SOURCE_MEMBERSHIP_DUPLICATE"
        )


if __name__ == "__main__":
    unittest.main()
