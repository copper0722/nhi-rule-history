from __future__ import annotations

import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nhi_rule_history.contracts import file_sha256
from nhi_rule_history.parsers.ods import (
    ODS_MEDIA_TYPE,
    OdsExtractionError,
    build_public_ods_receipt,
    parse_verified_ods_run,
)


CONTENT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">
 <office:body>
  <office:spreadsheet>
   <table:table table:name="測試表">
    {rows}
   </table:table>
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""


SUCCESS_ROWS = """
<table:table-row table:number-rows-repeated="2" table:style-name="r1">
 <table:table-cell table:number-columns-repeated="3"
   table:number-columns-spanned="2" office:value-type="string">
  <text:p>A<text:s text:c="2"/>B<text:tab/>C<text:line-break/>D</text:p>
 </table:table-cell>
 <table:table-cell office:value-type="float" office:value="1.50"
   table:formula="of:=1+0.5"><text:p>1.50</text:p></table:table-cell>
 <table:table-cell table:number-columns-repeated="4"/>
 <table:covered-table-cell/>
</table:table-row>
"""


def _write_ods(
    path: Path,
    content: str,
    *,
    mimetype: str = ODS_MEDIA_TYPE,
    duplicate_content: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype",
            mimetype.encode("ascii"),
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("content.xml", content.encode("utf-8"))
        if duplicate_content:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(
                    "content.xml", content.encode("utf-8")
                )


def _fixture(
    root: Path,
    content: str,
    *,
    mimetype: str = ODS_MEDIA_TYPE,
    linked: bool = True,
    duplicate_content: bool = False,
) -> tuple[Path, SimpleNamespace]:
    run_dir = root / "run"
    relative = "raw/sha256/aa/artifact"
    blob = run_dir / relative
    _write_ods(
        blob,
        content,
        mimetype=mimetype,
        duplicate_content=duplicate_content,
    )
    artifact = {
        "artifact_sha256": "a" * 64,
        "byte_size": blob.stat().st_size,
        "content_path": relative,
        "media_type": ODS_MEDIA_TYPE,
    }
    acquisition = SimpleNamespace(
        run_id="11111111-1111-4111-8111-111111111111",
        raw_manifest_sha256="b" * 64,
        sealed_fingerprint="c" * 64,
        rows={
            "raw-artifacts.jsonl": (artifact,),
            "discovered-resources.jsonl": (
                {
                    "resource_id": "resource-1",
                    "source_label": "fixture.ods",
                },
            ),
            "resource-artifact-links.jsonl": (
                (
                    {
                        "resource_id": "resource-1",
                        "artifact_sha256": artifact[
                            "artifact_sha256"
                        ],
                    },
                )
                if linked
                else ()
            ),
        },
    )
    return run_dir, acquisition


def _capture_receipt(acquisition: SimpleNamespace) -> dict:
    return {
        "schema": (
            "nhi-rule-history/"
            "historical-events-exact-phrase-capture-public-receipt/v1"
        ),
        "scope": {
            "query_start": "1996-01-01",
            "query_end": "2020-12-31",
            "capture_cut": "2026-07-27",
            "query": "藥品給付規定",
            "query_mode": "exact_phrase_bounded_baseline",
            "source_plan_sha256": "d" * 64,
        },
        "accepted_acquisition": {
            "run_id": acquisition.run_id,
            "state": "sealed",
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "sealed_fingerprint": acquisition.sealed_fingerprint,
            "media_type_counts": {ODS_MEDIA_TYPE: 1},
        },
    }


class OdsParserTests(unittest.TestCase):
    def test_preserves_repeat_span_text_formula_value_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.ods."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                manifest = parse_verified_ods_run(
                    run_dir,
                    stage,
                    expected_ods_artifact_count=1,
                )
            cells = [
                json.loads(line)
                for line in (stage / "ods-cells.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(cells), 4)
            first, second, blank, covered = cells
            self.assertEqual(first["locator"]["sheet_name"], "測試表")
            self.assertEqual(first["locator"]["row_logical_start"], 1)
            self.assertEqual(
                first["locator"]["row_logical_end_inclusive"], 2
            )
            self.assertEqual(first["repeat"]["row_repeat"], 2)
            self.assertEqual(first["repeat"]["cell_repeat"], 3)
            self.assertEqual(
                first["repeat"]["logical_cell_multiplicity"], 6
            )
            self.assertEqual(
                first["locator"]["column_logical_end_inclusive"], 3
            )
            self.assertEqual(
                first["span"]["number_columns_spanned"], 2
            )
            self.assertEqual(first["text"]["rendered"], "A  B\tC\nD")
            self.assertEqual(first["declared_value_type_raw"], "string")
            self.assertEqual(
                first["typed_payload"]["primary_value_raw"],
                "A  B\tC\nD",
            )
            self.assertEqual(second["locator"]["column_logical_start"], 4)
            self.assertEqual(second["formula_raw"], "of:=1+0.5")
            self.assertEqual(second["declared_value_type_raw"], "float")
            self.assertEqual(
                second["typed_payload"]["primary_value_raw"], "1.50"
            )
            self.assertEqual(blank["repeat"]["cell_repeat"], 4)
            self.assertTrue(blank["zero_payload"])
            self.assertEqual(covered["cell_kind"], "covered_table_cell")
            self.assertTrue(covered["zero_payload"])
            self.assertEqual(
                manifest["counts"],
                {
                    "artifacts_with_zero_payload_cells": 1,
                    "declared_ods_artifacts": 1,
                    "logical_cells": 18,
                    "logical_covered_cells": 2,
                    "logical_formula_cells": 2,
                    "logical_nonempty_text_cells": 8,
                    "logical_rows": 2,
                    "logical_zero_payload_cells": 10,
                    "parsed_ods_artifacts": 1,
                    "physical_cells": 4,
                    "physical_covered_cells": 1,
                    "physical_formula_cells": 1,
                    "physical_nonempty_text_cells": 2,
                    "physical_rows": 1,
                    "physical_zero_payload_cells": 2,
                    "rendered_text_bytes": 12,
                    "rendered_text_characters": 12,
                    "sheets": 1,
                },
            )
            self.assertTrue(
                manifest["closure_claims"][
                    "repeat_ranges_preserved_without_expansion"
                ]
            )
            self.assertFalse(
                manifest["closure_claims"]["legal_semantics_inferred"]
            )

    def test_output_is_byte_deterministic_across_fresh_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
            )
            first = root / "first"
            second = root / "second"
            with mock.patch(
                "nhi_rule_history.parsers.ods."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                first_manifest = parse_verified_ods_run(
                    run_dir,
                    first,
                    expected_ods_artifact_count=1,
                )
                second_manifest = parse_verified_ods_run(
                    run_dir,
                    second,
                    expected_ods_artifact_count=1,
                )
            self.assertEqual(first_manifest, second_manifest)
            for filename in (
                "ods-artifacts.jsonl",
                "ods-cells.jsonl",
                "ods-manifest.json",
            ):
                self.assertEqual(
                    (first / filename).read_bytes(),
                    (second / filename).read_bytes(),
                )

    def test_unsupported_cell_is_preserved_with_exact_locator(self) -> None:
        rows = """
<table:table-row>
 <table:table-cell office:value-type="mystery"
   office:string-value="raw"><draw:frame draw:name="x"/></table:table-cell>
</table:table-row>
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=rows),
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.ods."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                manifest = parse_verified_ods_run(
                    run_dir,
                    stage,
                    expected_ods_artifact_count=1,
                )
            self.assertEqual(
                manifest["counts"]["physical_unsupported_cells"], 1
            )
            self.assertEqual(
                len(manifest["unsupported_cell_locators"]), 1
            )
            codes = manifest["unsupported_cell_locators"][0][
                "unsupported_codes"
            ]
            self.assertIn("unsupported_value_type:mystery", codes)
            self.assertTrue(
                any(code.startswith("unsupported_cell_child:") for code in codes)
            )
            cell = json.loads(
                (stage / "ods-cells.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(
                cell["typed_payload"]["raw_value_attributes"],
                {
                    (
                        "{urn:oasis:names:tc:opendocument:"
                        "xmlns:office:1.0}string-value"
                    ): "raw"
                },
            )
            self.assertIn("locator", manifest["unsupported_cell_locators"][0])

    def test_public_receipt_binds_files_and_reports_zero_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.ods."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                parse_verified_ods_run(
                    run_dir,
                    stage,
                    expected_ods_artifact_count=1,
                )
            receipt = build_public_ods_receipt(
                stage_dir=stage,
                historical_capture_receipt=_capture_receipt(acquisition),
            )
            self.assertEqual(receipt["zero_content"]["physical_cells"], 2)
            self.assertEqual(
                receipt["zero_content"]["logical_cells_after_repeat"], 10
            )
            self.assertEqual(
                receipt["unsupported_cells"]["physical_cells"], 0
            )
            self.assertEqual(
                receipt["extraction"]["files"][1]["sha256"],
                file_sha256(stage / "ods-cells.jsonl"),
            )
            self.assertFalse(receipt["claims"]["history_complete"])

    def test_public_receipt_rejects_tampered_stage_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.ods."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                parse_verified_ods_run(
                    run_dir,
                    stage,
                    expected_ods_artifact_count=1,
                )
            with (stage / "ods-cells.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(" ")
            with self.assertRaisesRegex(
                OdsExtractionError,
                "changed",
            ):
                build_public_ods_receipt(
                    stage_dir=stage,
                    historical_capture_receipt=_capture_receipt(
                        acquisition
                    ),
                )

    def test_denominator_mismatch_fails_without_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
            )
            stage = root / "stage"
            with (
                mock.patch(
                    "nhi_rule_history.parsers.ods."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                self.assertRaisesRegex(
                    OdsExtractionError, "denominator"
                ),
            ):
                parse_verified_ods_run(
                    run_dir,
                    stage,
                    expected_ods_artifact_count=2,
                )
            self.assertFalse(stage.exists())

    def test_format_mismatch_and_malformed_xml_fail_closed(self) -> None:
        cases = (
            ("wrong mimetype", "text/plain", "<x/>"),
            ("malformed XML", ODS_MEDIA_TYPE, "<broken"),
        )
        for label, mimetype, content in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    run_dir, acquisition = _fixture(
                        root,
                        content,
                        mimetype=mimetype,
                    )
                    stage = root / "stage"
                    with (
                        mock.patch(
                            "nhi_rule_history.parsers.ods."
                            "validate_acquisition_run",
                            return_value=acquisition,
                        ),
                        self.assertRaises(OdsExtractionError),
                    ):
                        parse_verified_ods_run(
                            run_dir,
                            stage,
                            expected_ods_artifact_count=1,
                        )
                    self.assertFalse(stage.exists())

    def test_invalid_repeat_and_non_cell_row_child_fail_closed(self) -> None:
        cases = (
            """
<table:table-row table:number-rows-repeated="0">
 <table:table-cell/>
</table:table-row>
""",
            """
<table:table-row><text:p>not a cell</text:p></table:table-row>
""",
        )
        for rows in cases:
            with self.subTest(rows=rows):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    run_dir, acquisition = _fixture(
                        root,
                        CONTENT_TEMPLATE.format(rows=rows),
                    )
                    stage = root / "stage"
                    with (
                        mock.patch(
                            "nhi_rule_history.parsers.ods."
                            "validate_acquisition_run",
                            return_value=acquisition,
                        ),
                        self.assertRaises(OdsExtractionError),
                    ):
                        parse_verified_ods_run(
                            run_dir,
                            stage,
                            expected_ods_artifact_count=1,
                        )
                    self.assertFalse(stage.exists())

    def test_duplicate_zip_member_and_missing_source_link_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
                duplicate_content=True,
            )
            with (
                mock.patch(
                    "nhi_rule_history.parsers.ods."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                self.assertRaisesRegex(
                    OdsExtractionError, "duplicate member"
                ),
            ):
                parse_verified_ods_run(
                    run_dir,
                    root / "stage",
                    expected_ods_artifact_count=1,
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition = _fixture(
                root,
                CONTENT_TEMPLATE.format(rows=SUCCESS_ROWS),
                linked=False,
            )
            with (
                mock.patch(
                    "nhi_rule_history.parsers.ods."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                self.assertRaisesRegex(
                    OdsExtractionError, "source-resource link"
                ),
            ):
                parse_verified_ods_run(
                    run_dir,
                    root / "stage",
                    expected_ods_artifact_count=1,
                )


if __name__ == "__main__":
    unittest.main()
