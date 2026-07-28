from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nhi_rule_history.annotation_stage import prepare_annotation_stage
from nhi_rule_history.history_marker_preflight import (
    STRUCTURAL_CONTRACT_VERSION,
    STRUCTURAL_LOADER_VERSION,
    HistoryMarkerPreflightError,
    analyze_history_marker_preflight,
    compact_public_report,
    write_evidence_ledger,
)
from nhi_rule_history.pg.common import object_fingerprint
from nhi_rule_history.pg.structural import StructuralLoadError


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _annotation_inputs(
    *,
    official_designation: str = "1.1",
) -> tuple[dict, list[dict], list[dict], dict]:
    material = prepare_annotation_stage(
        [
            {
                "article_id": "1",
                "article_num": official_designation,
                "full_text": "沿革（112/1/1、112/1/1、113/13/1）",
                "source_identity": "fixture:official",
            },
            {
                "article_id": "2",
                "article_num": "0.1",
                "full_text": "通則沿革（113/2/1）",
                "source_identity": "fixture:general",
            },
        ]
    )
    run = {
        "run_id": material.run_id,
        "contract_version": (
            "nhi-rule-history/legacy-annotation-stage/v1"
        ),
        "extractor_version": (
            "nhi-rule-history/roc-date-marker-extractor/1.0.0"
        ),
        "migration_sha256": material.migration_sha256,
        "code_sha256": material.code_sha256,
        "input_fingerprint": material.input_fingerprint,
        "output_fingerprint": material.output_fingerprint,
        "sealed_fingerprint": material.sealed_fingerprint,
        "expected_counts": dict(material.expected_counts),
        "table_fingerprints": dict(material.table_fingerprints),
        "state": "sealed",
    }
    articles = [
        {"run_id": material.run_id, **row} for row in material.articles
    ]
    annotations = [
        {"run_id": material.run_id, **row}
        for row in material.annotations
    ]
    valid_dates = sorted(
        {
            row["normalized_iso_candidate"]
            for row in material.annotations
            if row["normalized_iso_candidate"] is not None
        }
    )
    receipt = {
        "schema": (
            "nhi-rule-history/"
            "legacy-date-annotation-stage-public-receipt/v1"
        ),
        "run_id": material.run_id,
        "extractor": {
            "version": (
                "nhi-rule-history/roc-date-marker-extractor/1.0.0"
            )
        },
        "counts": {
            "article_observations": len(material.articles),
            "articles_with_markers": sum(
                row["annotation_count"] > 0
                for row in material.articles
            ),
            "date_annotations": len(material.annotations),
            "normalized_iso_candidates": sum(
                row["normalized_iso_candidate"] is not None
                for row in material.annotations
            ),
            "unique_normalized_iso_dates": len(valid_dates),
            "earliest_normalized_iso_date": valid_dates[0],
            "latest_normalized_iso_date": valid_dates[-1],
            "invalid_calendar_candidates": sum(
                row["normalization_status"]
                == "invalid_calendar_date"
                for row in material.annotations
            ),
            "unresolved_annotations": len(material.annotations),
            "coverage_projection_rows": len(material.articles),
        },
        "verification": {
            "state": "sealed",
            "sealed_fingerprint": material.sealed_fingerprint,
        },
    }
    return run, articles, annotations, receipt


def _block(
    artifact: str,
    order: int,
    text: str,
) -> dict:
    markers = []
    return {
        "artifact_sha256": artifact,
        "block_id": _sha(f"{artifact}:block:{order}"),
        "locator_key": f"container=flow|doc_order={order}",
        "raw_text": text,
        "source_row_sha256": _sha(f"{artifact}:block-row:{order}"),
        "_unused": markers,
    }


def _occurrence(
    block: dict,
    designation: str,
) -> dict:
    start = block["raw_text"].index(designation)
    return {
        "artifact_sha256": block["artifact_sha256"],
        "block_id": block["block_id"],
        "occurrence_id": _sha(
            f"{block['block_id']}:{designation}:occurrence"
        ),
        "locator_key": block["locator_key"],
        "raw_text": block["raw_text"],
        "designation_text": designation,
        "match_start_in_raw": start,
        "match_end_in_raw": start + len(designation),
        "source_row_sha256": _sha(
            f"{block['block_id']}:{designation}:row"
        ),
    }


def _historical_inputs(
    *,
    occurrence_designation: str = "1.1",
) -> tuple[SimpleNamespace, SimpleNamespace, dict]:
    acquisition_run_id = "11111111-1111-4111-8111-111111111111"
    parse_run_id = "22222222-2222-4222-8222-222222222222"
    raw_manifest_sha256 = _sha("raw-manifest")
    acquisition = SimpleNamespace(
        run_id=acquisition_run_id,
        raw_manifest_sha256=raw_manifest_sha256,
        sealed_fingerprint=_sha("acquisition-seal"),
        source_plan_sha256=_sha("source-plan"),
        rows={
            "discovered-resources.jsonl": ({"resource_id": "r1"},),
            "raw-artifacts.jsonl": (
                {
                    "artifact_sha256": _sha("artifact"),
                    "byte_size": 10,
                    "media_type": (
                        "application/vnd.oasis.opendocument.text"
                    ),
                },
            ),
            "issues.jsonl": (),
            "artifact-url-observations.jsonl": (),
        },
    )
    artifact_a = _sha("historical-a")
    artifact_b = _sha("historical-b")
    artifact_c = _sha("historical-c")
    block_a = _block(artifact_a, 1, "1.1 測試（112/1/1）")
    block_b = _block(artifact_b, 2, "通則日期（113/2/1）")
    block_c = _block(artifact_c, 3, "誤植（112/55/22）")
    counts = {
        "declared_odt_artifacts": 3,
        "parsed_odt_artifacts": 3,
        "structural_blocks": 3,
        "occurrence_candidates": 1,
        "parse_issues": 0,
        "blocking_issues": 0,
    }
    structural = SimpleNamespace(
        parse_run_id=parse_run_id,
        structural_manifest_sha256=_sha("structural-manifest"),
        migration_sha256=_sha("structural-migration"),
        code_sha256=_sha("structural-code"),
        output_fingerprint=_sha("structural-output"),
        table_fingerprints={
            "structural_block": _sha("blocks"),
            "occurrence_candidate": _sha("occurrences"),
            "parse_issue": _sha("issues"),
        },
        manifest={
            "raw_manifest_sha256": raw_manifest_sha256,
            "input_fingerprint": _sha("structural-input"),
            "counts": counts,
        },
        rows={
            "structural-blocks.jsonl": (
                block_a,
                block_b,
                block_c,
            ),
            "occurrence-candidates.jsonl": (
                _occurrence(block_a, occurrence_designation),
            ),
            "parse-issues.jsonl": (),
        },
    )
    structural_seal = object_fingerprint(
        {
            "loader_version": STRUCTURAL_LOADER_VERSION,
            "contract_version": STRUCTURAL_CONTRACT_VERSION,
            "migration_sha256": structural.migration_sha256,
            "code_sha256": structural.code_sha256,
            "acquisition_run_id": acquisition.run_id,
            "input_fingerprint": structural.manifest[
                "input_fingerprint"
            ],
            "output_fingerprint": structural.output_fingerprint,
        }
    )
    receipt = {
        "schema": (
            "nhi-rule-history/"
            "historical-events-exact-phrase-capture-public-receipt/v1"
        ),
        "scope": {
            "source_plan_sha256": acquisition.source_plan_sha256,
        },
        "accepted_acquisition": {
            "run_id": acquisition.run_id,
            "state": "sealed",
            "resources": 1,
            "artifacts": 1,
            "artifact_bytes": 10,
            "issues": 0,
            "same_url_different_bytes": 0,
            "raw_manifest_sha256": raw_manifest_sha256,
            "sealed_fingerprint": acquisition.sealed_fingerprint,
            "media_type_counts": {
                "application/vnd.oasis.opendocument.text": 1,
            },
        },
        "accepted_structural_stage": {
            "parse_run_id": structural.parse_run_id,
            "state": "sealed",
            **counts,
            "structural_manifest_sha256": (
                structural.structural_manifest_sha256
            ),
            "sealed_fingerprint": structural_seal,
        },
    }
    return acquisition, structural, receipt


def _analyze(
    *,
    official_designation: str = "1.1",
    occurrence_designation: str = "1.1",
    mutate_annotation_receipt=None,
    mutate_historical_receipt=None,
):
    run, articles, annotations, annotation_receipt = (
        _annotation_inputs(
            official_designation=official_designation
        )
    )
    acquisition, structural, historical_receipt = _historical_inputs(
        occurrence_designation=occurrence_designation
    )
    if mutate_annotation_receipt is not None:
        mutate_annotation_receipt(annotation_receipt)
    if mutate_historical_receipt is not None:
        mutate_historical_receipt(historical_receipt)
    with (
        mock.patch(
            "nhi_rule_history.history_marker_preflight."
            "validate_acquisition_run",
            return_value=acquisition,
        ),
        mock.patch(
            "nhi_rule_history.history_marker_preflight."
            "validate_structural_run",
            return_value=structural,
        ),
    ):
        return analyze_history_marker_preflight(
            annotation_run=run,
            articles=articles,
            annotations=annotations,
            annotation_receipt=annotation_receipt,
            historical_receipt=historical_receipt,
            raw_dir=Path("fixture-raw"),
            structural_dir=Path("fixture-structural"),
        )


class HistoryMarkerPreflightTests(unittest.TestCase):
    def test_candidate_coverage_preserves_exact_locators(self) -> None:
        result = _analyze()
        self.assertEqual(result["status"], "candidate_coverage_only")
        self.assertEqual(
            result["coverage"]["valid_marker_occurrences"], 3
        )
        self.assertEqual(
            result["coverage"]["unique_article_date_pairs"], 2
        )
        self.assertEqual(
            result["coverage"]["date_present_marker_occurrences"], 3
        )
        self.assertEqual(
            result["coverage"][
                "date_and_designation_same_artifact_marker_occurrences"
            ],
            2,
        )
        self.assertEqual(
            result["coverage"][
                "date_and_designation_same_artifact_article_date_pairs"
            ],
            1,
        )
        self.assertEqual(
            result["coverage"][
                "date_and_designation_not_evaluable_project_navigation_pairs"
            ],
            1,
        )
        self.assertEqual(
            result["coverage"][
                "invalid_annotation_date_candidates_rejected"
            ],
            1,
        )
        self.assertEqual(
            result["coverage"][
                "historical_odt_invalid_date_candidates_rejected"
            ],
            1,
        )

        annotation_locator = result["evidence_rows"][
            "valid_annotation_marker_locators"
        ][0]
        self.assertIn("annotation_id", annotation_locator)
        self.assertEqual(
            annotation_locator["raw_expression"], "112/1/1"
        )
        self.assertEqual(
            annotation_locator["char_end"]
            - annotation_locator["char_start"],
            len("112/1/1"),
        )
        historical_locator = result["evidence_rows"][
            "historical_date_marker_locators"
        ][0]
        self.assertIn("artifact_sha256", historical_locator)
        self.assertIn("block_id", historical_locator)
        self.assertIn("locator_key", historical_locator)
        self.assertEqual(
            historical_locator["raw_expression"], "112/1/1"
        )
        pair_rows = result["evidence_rows"]["article_date_pairs"]
        project_pair = next(
            row
            for row in pair_rows
            if row["article_num"] == "0.1"
        )
        self.assertIsNone(
            project_pair[
                "date_and_designation_in_same_artifact"
            ]
        )
        self.assertEqual(
            project_pair["joint_evaluation_status"],
            "not_evaluable_project_navigation",
        )
        self.assertFalse(
            result["claims"]["per_clause_history_complete"]
        )
        self.assertFalse(result["claims"]["legal_effective_date_resolved"])

    def test_replay_is_deterministic(self) -> None:
        first = _analyze()
        second = _analyze()
        self.assertEqual(
            first["input_fingerprint"], second["input_fingerprint"]
        )
        self.assertEqual(
            first["output_fingerprint"], second["output_fingerprint"]
        )
        self.assertEqual(
            first["evidence_fingerprints"],
            second["evidence_fingerprints"],
        )

    def test_tampered_annotation_receipt_fails_closed(self) -> None:
        def mutate(receipt):
            receipt["verification"]["sealed_fingerprint"] = "0" * 64

        with self.assertRaisesRegex(
            HistoryMarkerPreflightError,
            "not bound",
        ):
            _analyze(mutate_annotation_receipt=mutate)

    def test_tampered_historical_receipt_fails_closed(self) -> None:
        def mutate(receipt):
            receipt["accepted_structural_stage"][
                "structural_manifest_sha256"
            ] = "0" * 64

        with self.assertRaisesRegex(
            HistoryMarkerPreflightError,
            "binding mismatch",
        ):
            _analyze(mutate_historical_receipt=mutate)

    def test_missing_or_tampered_structural_manifest_fails_closed(
        self,
    ) -> None:
        run, articles, annotations, annotation_receipt = (
            _annotation_inputs()
        )
        acquisition, _structural, historical_receipt = (
            _historical_inputs()
        )
        with (
            mock.patch(
                "nhi_rule_history.history_marker_preflight."
                "validate_acquisition_run",
                return_value=acquisition,
            ),
            mock.patch(
                "nhi_rule_history.history_marker_preflight."
                "validate_structural_run",
                side_effect=StructuralLoadError(
                    "manifested file changed"
                ),
            ),
            self.assertRaisesRegex(
                HistoryMarkerPreflightError,
                "failed verification",
            ),
        ):
            analyze_history_marker_preflight(
                annotation_run=run,
                articles=articles,
                annotations=annotations,
                annotation_receipt=annotation_receipt,
                historical_receipt=historical_receipt,
                raw_dir=Path("fixture-raw"),
                structural_dir=Path("fixture-structural"),
            )

    def test_malformed_legacy_designation_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            HistoryMarkerPreflightError,
            "legacy article designation is malformed",
        ):
            _analyze(official_designation="9..1")

    def test_malformed_historical_designation_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            HistoryMarkerPreflightError,
            "historical designation occurrence is malformed",
        ):
            _analyze(occurrence_designation="1")

    def test_tampered_annotation_row_fails_closed(self) -> None:
        run, articles, annotations, annotation_receipt = (
            _annotation_inputs()
        )
        acquisition, structural, historical_receipt = (
            _historical_inputs()
        )
        annotations = copy.deepcopy(annotations)
        annotations[0]["raw_expression"] = "111/1/1"
        with (
            mock.patch(
                "nhi_rule_history.history_marker_preflight."
                "validate_acquisition_run",
                return_value=acquisition,
            ),
            mock.patch(
                "nhi_rule_history.history_marker_preflight."
                "validate_structural_run",
                return_value=structural,
            ),
            self.assertRaisesRegex(
                HistoryMarkerPreflightError,
                "deterministic replay",
            ),
        ):
            analyze_history_marker_preflight(
                annotation_run=run,
                articles=articles,
                annotations=annotations,
                annotation_receipt=annotation_receipt,
                historical_receipt=historical_receipt,
                raw_dir=Path("fixture-raw"),
                structural_dir=Path("fixture-structural"),
            )

    def test_missing_receipt_fails_closed_before_analysis(self) -> None:
        run, articles, annotations, _receipt = _annotation_inputs()
        _acquisition, _structural, historical_receipt = (
            _historical_inputs()
        )
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.json"
            with self.assertRaisesRegex(
                HistoryMarkerPreflightError,
                "missing or invalid",
            ):
                analyze_history_marker_preflight(
                    annotation_run=run,
                    articles=articles,
                    annotations=annotations,
                    annotation_receipt=missing,
                    historical_receipt=historical_receipt,
                    raw_dir=Path("fixture-raw"),
                    structural_dir=Path("fixture-structural"),
                )

    def test_evidence_ledger_and_compact_report_are_deterministic(
        self,
    ) -> None:
        result = _analyze()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.jsonl"
            first = write_evidence_ledger(result, path)
            first_bytes = path.read_bytes()
            second = write_evidence_ledger(result, path)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, path.read_bytes())
            compact = compact_public_report(
                result,
                evidence_ledger=second,
            )
        self.assertNotIn("evidence_rows", compact)
        self.assertEqual(
            compact["evidence_ledger"]["sha256"], first["sha256"]
        )
        self.assertEqual(
            compact["rejected_date_locators"],
            result["rejected_date_locators"],
        )


if __name__ == "__main__":
    unittest.main()
