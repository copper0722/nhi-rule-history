from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nhi_rule_history.contracts import canonical_json_bytes, file_sha256
from nhi_rule_history.history_marker_cross_format_preflight import (
    HistoryMarkerCrossFormatPreflightError,
    _extract_unit_evidence,
    _text_unit,
    _verify_manifested_files,
    analyze_history_marker_cross_format_preflight,
    compact_cross_format_public_report,
    write_cross_format_evidence_ledger,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_row(**values):
    row = dict(values)
    row["source_row_sha256"] = _sha(
        canonical_json_bytes(values).decode("utf-8")
    )
    return row


def _odt_result() -> dict:
    return {
        "input_fingerprint": _sha("odt-input"),
        "output_fingerprint": _sha("odt-output"),
        "coverage": {
            "valid_marker_occurrences": 3,
            "invalid_annotation_date_candidates_rejected": 1,
            "date_present_article_date_pairs": 0,
            "date_and_designation_same_artifact_article_date_pairs": 0,
        },
        "evidence_rows": {
            "article_date_pairs": [
                {
                    "pair_id": _sha("pair-1"),
                    "article_id": "a1",
                    "article_num": "1.1",
                    "normalized_iso_candidate": "2023-01-01",
                    "designation_kind": (
                        "official_dotted_numeric_candidate"
                    ),
                    "marker_occurrence_count": 2,
                    "date_in_historical_odt_artifact": False,
                    "date_and_designation_in_same_artifact": False,
                },
                {
                    "pair_id": _sha("pair-2"),
                    "article_id": "a2",
                    "article_num": "1.2",
                    "normalized_iso_candidate": "2024-02-01",
                    "designation_kind": (
                        "official_dotted_numeric_candidate"
                    ),
                    "marker_occurrence_count": 1,
                    "date_in_historical_odt_artifact": False,
                    "date_and_designation_in_same_artifact": False,
                },
                {
                    "pair_id": _sha("pair-3"),
                    "article_id": "a3",
                    "article_num": "0.1",
                    "normalized_iso_candidate": "2023-01-01",
                    "designation_kind": (
                        "project_navigation_general_rules"
                    ),
                    "marker_occurrence_count": 1,
                    "date_in_historical_odt_artifact": False,
                    "date_and_designation_in_same_artifact": None,
                },
            ]
        },
    }


def _verified_inputs():
    pdf_artifact = _sha("pdf")
    ole_artifact = _sha("ole")
    ods_artifact = _sha("ods")
    image_artifact = _sha("image")
    acquisition = SimpleNamespace(
        run_id="raw-run",
        raw_manifest_sha256=_sha("raw-manifest"),
        sealed_fingerprint=_sha("raw-seal"),
        rows={
            "raw-artifacts.jsonl": (
                {
                    "artifact_sha256": pdf_artifact,
                    "media_type": "application/pdf",
                },
                {
                    "artifact_sha256": ole_artifact,
                    "media_type": "application/x-ole-storage",
                },
                {
                    "artifact_sha256": ods_artifact,
                    "media_type": (
                        "application/vnd.oasis.opendocument.spreadsheet"
                    ),
                },
                {
                    "artifact_sha256": image_artifact,
                    "media_type": "image/jpeg",
                },
            )
        },
    )
    pdf_page = _source_row(
        artifact_sha256=pdf_artifact,
        page_number=1,
        text="原生日 112/1/1",
        bbox={"x_min": "0", "y_min": "0"},
    )
    pdf = {
        "manifest": {
            "parse_run_id": "pdf-run",
            "output_fingerprint": _sha("pdf-output"),
            "counts": {"pages_without_words": 0},
        },
        "manifest_sha256": _sha("pdf-manifest"),
        "artifacts": [
            {
                "artifact_sha256": pdf_artifact,
                "resource_bindings": [
                    {
                        "resource_id": _sha("pdf-resource"),
                        "resource_kind": "official_attachment",
                        "source_label": "source.pdf",
                    }
                ],
            }
        ],
        "pages": [pdf_page],
    }
    ole_paragraph = _source_row(
        artifact_sha256=ole_artifact,
        text="條號 1.1",
        block_index=1,
        paragraph_ordinal=1,
        locator={
            "artifact_sha256": ole_artifact,
            "block_index": 1,
            "paragraph_ordinal": 1,
        },
    )
    ole = {
        "manifest": {
            "parse_run_id": "ole-run",
            "output_fingerprint": _sha("ole-output"),
            "counts": {
                "needs_review_artifacts": 0,
                "word_visual_pages": 0,
            },
        },
        "manifest_sha256": _sha("ole-manifest"),
        "rows": {
            "ole-artifacts.jsonl": [
                {
                    "artifact_sha256": ole_artifact,
                    "resource_bindings": [
                        {
                            "resource_id": _sha("ole-resource"),
                            "resource_kind": "official_attachment",
                            "source_label": "source.doc",
                        }
                    ],
                }
            ],
            "ole-word-paragraphs.jsonl": [ole_paragraph],
            "ole-word-cells.jsonl": [],
            "ole-excel-cells.jsonl": [],
        },
    }
    ods_date = _source_row(
        artifact_sha256=ods_artifact,
        cell_id=_sha("ods-date"),
        text={"rendered": "113/2/1"},
        locator={"sheet_name": "S", "row": 1, "column": 1},
        source_resource_ids=[_sha("ods-resource")],
        source_labels=["source.ods"],
    )
    ods_designation = _source_row(
        artifact_sha256=ods_artifact,
        cell_id=_sha("ods-designation"),
        text={"rendered": "1.2"},
        locator={"sheet_name": "S", "row": 1, "column": 2},
        source_resource_ids=[_sha("ods-resource")],
        source_labels=["source.ods"],
    )
    ods = {
        "manifest": {"extraction_id": "ods-run"},
        "manifest_sha256": _sha("ods-manifest"),
        "output_fingerprint": _sha("ods-output"),
        "cell_rows": [ods_date, ods_designation],
        "unsupported_code_counts": {},
    }
    ocr_row = _source_row(
        artifact_sha256=image_artifact,
        frame_id=_sha("frame"),
        ocr_observation_id=_sha("ocr"),
        text="1.1（112/1/1）",
        frame_locator={"frame_index": 0, "frame_kind": "single_image"},
    )
    image = {
        "manifest": {
            "extraction_id": "image-run",
            "ocr_runtime": {"runtime_fingerprint": _sha("ocr-runtime")},
            "counts": {"needs_visual_review_frames": 1},
        },
        "manifest_sha256": _sha("image-manifest"),
        "output_fingerprint": _sha("image-output"),
        "rows": {
            "image-frames.jsonl": [
                {
                    "frame_id": _sha("frame"),
                    "source_resource_ids": [_sha("image-resource")],
                    "source_labels": ["source.jpg"],
                }
            ],
            "image-ocr.jsonl": [ocr_row],
        },
    }
    return acquisition, pdf, ole, ods, image


def _analyze():
    acquisition, pdf, ole, ods, image = _verified_inputs()
    with (
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "analyze_history_marker_preflight",
            return_value=_odt_result(),
        ),
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "validate_acquisition_run",
            return_value=acquisition,
        ),
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "_verify_pdf_stage",
            return_value=pdf,
        ),
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "_verify_ole_stage",
            return_value=ole,
        ),
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "_verify_ods_bound_stage",
            return_value=ods,
        ),
        mock.patch(
            "nhi_rule_history.history_marker_cross_format_preflight."
            "_verify_image_bound_stage",
            return_value=image,
        ),
    ):
        return analyze_history_marker_cross_format_preflight(
            annotation_run={},
            articles=[],
            annotations=[],
            annotation_receipt={},
            historical_receipt={},
            raw_dir=Path("raw"),
            structural_dir=Path("structural"),
            pdf_stage_dir=Path("pdf"),
            ole_stage_dir=Path("ole"),
            ods_stage_dir=Path("ods"),
            image_stage_dir=Path("image"),
        )


class CrossFormatMarkerPreflightTests(unittest.TestCase):
    def test_manifest_file_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            target = stage / "rows.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            entry = {
                "filename": target.name,
                "bytes": target.stat().st_size,
                "sha256": file_sha256(target),
            }
            target.write_text('{"tampered":true}\n', encoding="utf-8")
            with self.assertRaises(
                HistoryMarkerCrossFormatPreflightError
            ):
                _verify_manifested_files(
                    stage_dir=stage,
                    entries=[entry],
                    expected_files=[target.name],
                    label="fixture",
                )

    def test_ambiguous_locator_fails_closed(self):
        row = {
            "artifact_sha256": _sha("artifact"),
            "source_row_sha256": _sha("source-row"),
        }
        with self.assertRaises(
            HistoryMarkerCrossFormatPreflightError
        ):
            _text_unit(
                source_format="pdf",
                confidence_lane="native_text_candidate",
                row=row,
                text="112/1/1",
                unit_type="page",
                unit_id={"page_number": 1},
                unit_locator={},
                source_resource_bindings=[
                    {
                        "resource_id": _sha("resource"),
                        "source_label": "source.pdf",
                    }
                ],
            )

    def test_missing_resource_binding_fails_closed(self):
        row = {
            "artifact_sha256": _sha("artifact"),
            "source_row_sha256": _sha("source-row"),
        }
        with self.assertRaises(
            HistoryMarkerCrossFormatPreflightError
        ):
            _text_unit(
                source_format="pdf",
                confidence_lane="native_text_candidate",
                row=row,
                text="112/1/1",
                unit_type="page",
                unit_id={"page_number": 1},
                unit_locator={"page_number": 1},
                source_resource_bindings=[],
            )

    def test_invalid_dates_are_preserved_but_not_indexed(self):
        row = {
            "artifact_sha256": _sha("artifact"),
            "source_row_sha256": _sha("source-row"),
        }
        unit = _text_unit(
            source_format="pdf",
            confidence_lane="native_text_candidate",
            row=row,
            text="112/1/1、113/13/1",
            unit_type="page",
            unit_id={"page_number": 1},
            unit_locator={"page_number": 1},
            source_resource_bindings=[
                {
                    "resource_id": _sha("resource"),
                    "source_label": "source.pdf",
                }
            ],
        )
        result = _extract_unit_evidence(
            {"pdf": [unit]},
            official_designations={"1.1"},
        )
        self.assertEqual(len(result["date_rows"]), 1)
        self.assertEqual(len(result["rejected_rows"]), 1)
        self.assertEqual(
            set(result["date_artifacts"]["pdf"]),
            {"2023-01-01"},
        )

    def test_distinct_source_occurrences_are_not_collapsed(self):
        row = {
            "artifact_sha256": _sha("artifact"),
            "source_row_sha256": _sha("source-row"),
        }
        units = [
            _text_unit(
                source_format="pdf",
                confidence_lane="native_text_candidate",
                row=row,
                text="112/1/1",
                unit_type="page",
                unit_id={"page_number": page},
                unit_locator={"page_number": page},
                source_resource_bindings=[
                    {
                        "resource_id": _sha("resource"),
                        "source_label": "source.pdf",
                    }
                ],
            )
            for page in (1, 2)
        ]
        result = _extract_unit_evidence(
            {"pdf": units},
            official_designations=set(),
        )
        self.assertEqual(len(result["date_rows"]), 2)
        self.assertEqual(
            result["exact_duplicate_rows_removed"]["valid_date"],
            0,
        )

    def test_ocr_is_separate_and_never_mixed_into_native_coverage(self):
        result = _analyze()
        coverage = result["coverage"]
        self.assertEqual(
            coverage["native_cross_format"][
                "date_present_article_date_pairs"
            ],
            3,
        )
        self.assertEqual(
            coverage["native_cross_format"][
                "joint_present_official_article_date_pairs"
            ],
            1,
        )
        self.assertEqual(
            coverage["unreviewed_ocr_separate_non_authoritative"][
                "joint_candidate_official_article_date_pairs"
            ],
            1,
        )
        self.assertEqual(
            coverage["unreviewed_ocr_separate_non_authoritative"][
                "joint_only_beyond_native_official_article_date_pairs"
            ],
            1,
        )
        self.assertEqual(
            coverage["unreviewed_ocr_separate_non_authoritative"][
                "authoritative_text_observations"
            ],
            0,
        )
        first = next(
            row
            for row in result["evidence_rows"]["article_date_pairs"]
            if row["article_num"] == "1.1"
        )
        self.assertFalse(first["native_cross_format_joint_candidate"])
        self.assertTrue(first["ocr_only_joint_candidate_beyond_native"])

    def test_same_artifact_not_same_corpus_is_joint_boundary(self):
        result = _analyze()
        by_article = {
            row["article_num"]: row
            for row in result["evidence_rows"]["article_date_pairs"]
        }
        self.assertFalse(
            by_article["1.1"]["native_cross_format_joint_candidate"]
        )
        self.assertTrue(
            by_article["1.2"]["native_cross_format_joint_candidate"]
        )

    def test_project_navigation_is_never_designation_evaluable(self):
        result = _analyze()
        row = next(
            row
            for row in result["evidence_rows"]["article_date_pairs"]
            if row["article_num"] == "0.1"
        )
        self.assertIsNone(
            row["native_cross_format_joint_candidate"]
        )
        self.assertFalse(row["native_joint_incremental_over_odt"])
        self.assertFalse(row["ocr_only_joint_candidate_beyond_native"])

    def test_deterministic_replay_and_ledger(self):
        first = _analyze()
        second = _analyze()
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as temporary:
            first_path = Path(temporary) / "first.jsonl"
            second_path = Path(temporary) / "second.jsonl"
            first_receipt = write_cross_format_evidence_ledger(
                first, first_path
            )
            second_receipt = write_cross_format_evidence_ledger(
                second, second_path
            )
            self.assertEqual(
                first_path.read_bytes(), second_path.read_bytes()
            )
            self.assertEqual(
                first_receipt["sha256"], second_receipt["sha256"]
            )
            compact = compact_cross_format_public_report(
                first, evidence_ledger=first_receipt
            )
            self.assertNotIn("evidence_rows", compact)
            self.assertEqual(
                compact["evidence_ledger"]["sha256"],
                first_receipt["sha256"],
            )

    def test_ocr_rows_are_never_marked_authoritative(self):
        result = _analyze()
        for row in result["evidence_rows"][
            "unreviewed_ocr_date_locators"
        ]:
            self.assertEqual(
                row["confidence_lane"],
                "unreviewed_ocr_candidate_non_authoritative",
            )
        self.assertFalse(result["claims"]["ocr_authoritative_text"])
        self.assertTrue(
            result["evidence_rows"]["unreviewed_ocr_date_locators"][0][
                "source_resource_bindings"
            ]
        )

    def test_tampered_verified_result_does_not_change_input_fixture(self):
        acquisition, pdf, _, _, _ = _verified_inputs()
        original = copy.deepcopy(pdf)
        pdf["pages"][0]["text"] = "tampered"
        self.assertNotEqual(pdf, original)
        self.assertEqual(
            acquisition.raw_manifest_sha256,
            _sha("raw-manifest"),
        )


if __name__ == "__main__":
    unittest.main()
