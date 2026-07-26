#!/usr/bin/env python3
"""Focused synthetic ODT tests for occurrence extraction.

Synthetic ODT ZIP fixtures are built at test runtime. Official binaries and
official prose are never copied into the test tree or Git.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import corpus_profile as cp  # noqa: E402
import occurrence_extract as oe  # noqa: E402


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _odt_bytes_from_body(body_xml: str) -> bytes:
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
        "<office:body><office:text>"
        + body_xml
        + "</office:text></office:body></office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content_xml.encode("utf-8"))
        zf.writestr(
            "META-INF/manifest.xml",
            b'<?xml version="1.0"?><manifest:manifest '
            b'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>',
        )
    return buf.getvalue()


def _simple_odt(
    *,
    paragraphs: list[str] | None = None,
    headings: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
    repeated_rows: list[tuple[int, list[tuple[int, str]]]] | None = None,
    lists: list[list[str]] | None = None,
    span_paragraphs: list[list[str]] | None = None,
) -> bytes:
    """Build a minimal ODT with synthetic text only."""
    parts: list[str] = []
    for h in headings or []:
        parts.append(f'<text:h text:style-name="Heading">{_xml_escape(h)}</text:h>')
    for p in paragraphs or []:
        parts.append(f'<text:p text:style-name="P1">{_xml_escape(p)}</text:p>')
    for spans in span_paragraphs or []:
        inner = "".join(f"<text:span>{_xml_escape(s)}</text:span>" for s in spans)
        parts.append(f'<text:p text:style-name="P1">{inner}</text:p>')
    for items in lists or []:
        parts.append("<text:list>")
        for item in items:
            parts.append(
                f"<text:list-item><text:p>{_xml_escape(item)}</text:p></text:list-item>"
            )
        parts.append("</text:list>")
    if repeated_rows is not None:
        parts.append("<table:table>")
        for row_rep, cells in repeated_rows:
            parts.append(
                f'<table:table-row table:number-rows-repeated="{row_rep}">'
            )
            for col_rep, cell_text in cells:
                parts.append(
                    f'<table:table-cell table:number-columns-repeated="{col_rep}">'
                    f"<text:p>{_xml_escape(cell_text)}</text:p>"
                    "</table:table-cell>"
                )
            parts.append("</table:table-row>")
        parts.append("</table:table>")
    else:
        for table in tables or []:
            parts.append("<table:table>")
            for row in table:
                parts.append("<table:table-row>")
                for cell in row:
                    parts.append(
                        "<table:table-cell>"
                        f"<text:p>{_xml_escape(cell)}</text:p>"
                        "</table:table-cell>"
                    )
                parts.append("</table:table-row>")
            parts.append("</table:table>")
    return _odt_bytes_from_body("".join(parts))


def _write_history_fixture(
    root: Path,
    files: dict[str, bytes],
    *,
    lane_prefix: bool = True,
) -> Path:
    """Write synthetic history ODTs and a matching accepted-style manifest."""
    hist = root / "history"
    hist.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for name, data in sorted(files.items(), key=lambda kv: kv[0].encode("utf-8")):
        (hist / name).write_bytes(data)
        rel = f"history/{name}"
        manifest_rows.append(
            {
                "basename": name,
                "byte_length": len(data),
                "lane": "history",
                "relative_path": rel,
                "sha256": cp.sha256_bytes(data),
                "extension": "odt",
            }
        )
    man_path = root / "manifest.jsonl"
    cp.write_jsonl(man_path, manifest_rows)
    return hist


def _fourteen_names() -> list[str]:
    labels = [
        "109年版",
        "108年版",
        "107年版",
        "106年版",
        "105年版",
        "104年版",
        "103年版",
        "102年版",
        "101年版",
        "100年版",
        "99年版",
        "98年版",
        "97年9月版",
        "96年7月版",
    ]
    ids = [
        7811,
        7813,
        7815,
        7817,
        7822,
        7824,
        7828,
        7833,
        7838,
        7840,
        7843,
        7849,
        7851,
        7853,
    ]
    return [f"{fid}_{lab}.odt" for fid, lab in zip(ids, labels)]


class DesignationDetectionTests(unittest.TestCase):
    def test_accept_beginning_dotted_designation(self) -> None:
        for text, want in [
            ("9.24 gefitinib synthetic", "9.24"),
            ("  3.3.13. Fabry synthetic", "3.3.13"),
            ("（8.2.4.7） risankizumab synthetic", "8.2.4.7"),
            ("1.3.5.ADHD synthetic", "1.3.5"),
            ("9.24.1 nested synthetic", "9.24.1"),
            ("9.24.Gefitinib synthetic body", "9.24"),
        ]:
            det = oe.detect_designation(text)
            self.assertIsNotNone(det, msg=text)
            assert det is not None
            self.assertEqual(det["designation_text"], want)

    def test_reject_non_boundary_and_non_start(self) -> None:
        self.assertIsNone(oe.detect_designation("prefix 9.24 not at start"))
        det = oe.detect_designation("9.240 synthetic long code")
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det["designation_text"], "9.240")
        self.assertNotEqual(det["designation_text"], "9.24")
        det = oe.detect_designation("19.24 synthetic")
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det["designation_text"], "19.24")
        self.assertIsNone(oe.detect_designation("88/9/1 synthetic date"))
        self.assertIsNone(oe.detect_designation("9 alone"))
        self.assertIsNone(oe.detect_designation(""))
        self.assertIsNone(oe.detect_designation("   "))

    def test_numeric_quantity_rejection_policy(self) -> None:
        # Zero-leading
        for text in ("0.2 synthetic", "0.23 x", "0.3", "0.5 mL dose"):
            det = oe.detect_designation(text)
            self.assertIsNotNone(det)
            assert det is not None
            rej = oe.classify_non_rule_numeric_head(
                det["designation_text"], det["remainder_after_designation"]
            )
            self.assertIsNotNone(rej)
            assert rej is not None
            self.assertEqual(rej["rejection_code"], "zero_leading_first_segment")

        # Out of chapter range
        det = oe.detect_designation("563.741 synthetic quantity")
        self.assertIsNotNone(det)
        assert det is not None
        rej = oe.classify_non_rule_numeric_head(
            det["designation_text"], det["remainder_after_designation"]
        )
        self.assertIsNotNone(rej)
        assert rej is not None
        self.assertEqual(rej["rejection_code"], "first_segment_exceeds_chapter_bound")

        # Unit-suffixed (non-zero first segment)
        det = oe.detect_designation("12.5 mg synthetic")
        self.assertIsNotNone(det)
        assert det is not None
        rej = oe.classify_non_rule_numeric_head(
            det["designation_text"], det["remainder_after_designation"]
        )
        self.assertIsNotNone(rej)
        assert rej is not None
        self.assertEqual(rej["rejection_code"], "unit_suffixed_numeric_quantity")

        # Real headings remain admitted
        for text in (
            "9.24.Gefitinib synthetic heading",
            "8.2.4.7 risankizumab synthetic",
            "3.3.13 Fabry synthetic",
        ):
            det = oe.detect_designation(text)
            self.assertIsNotNone(det)
            assert det is not None
            self.assertIsNone(
                oe.classify_non_rule_numeric_head(
                    det["designation_text"], det["remainder_after_designation"]
                )
            )


class StructuralParseTests(unittest.TestCase):
    def test_document_order_headings_paragraphs_spans_lists_tables(self) -> None:
        data = _simple_odt(
            headings=["H synthetic top"],
            paragraphs=["flow para A", "9.24 designation flow"],
            span_paragraphs=[["3.3.13", ". span-joined synthetic"]],
            lists=[["list item without desig", "1.3.5 list designation"]],
            tables=[
                [
                    ["left cell 8.2.4.7 left", "right cell other"],
                    ["row2c1", "row2c2"],
                ]
            ],
        )
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/synthetic.odt"
        )
        texts = [b["raw_text"] for b in state.blocks]
        self.assertEqual(texts[0], "H synthetic top")
        self.assertEqual(texts[1], "flow para A")
        self.assertEqual(texts[2], "9.24 designation flow")
        self.assertEqual(texts[3], "3.3.13. span-joined synthetic")
        self.assertIn("1.3.5 list designation", texts)
        self.assertIn("left cell 8.2.4.7 left", texts)
        self.assertIn("right cell other", texts)
        left = [b for b in state.blocks if b["raw_text"] == "left cell 8.2.4.7 left"]
        right = [b for b in state.blocks if b["raw_text"] == "right cell other"]
        self.assertEqual(len(left), 1)
        self.assertEqual(len(right), 1)
        self.assertNotEqual(left[0]["locator_key"], right[0]["locator_key"])
        self.assertEqual(left[0]["locator"]["cell_logical_index"], 0)
        self.assertEqual(right[0]["locator"]["cell_logical_index"], 1)

    def test_stable_locators_and_byte_identical_rerun(self) -> None:
        data = _simple_odt(
            paragraphs=["9.24 a", "body", "3.3.13 b"],
            tables=[[["c1", "c2"]]],
        )
        sha = cp.sha256_bytes(data)
        s1 = oe.parse_odt_blocks(data, artifact_sha=sha, relative_path="history/x.odt")
        s2 = oe.parse_odt_blocks(data, artifact_sha=sha, relative_path="history/x.odt")
        keys1 = [b["locator_key"] for b in s1.blocks]
        keys2 = [b["locator_key"] for b in s2.blocks]
        self.assertEqual(keys1, keys2)
        self.assertEqual(len(keys1), len(set(keys1)))
        self.assertEqual(
            [cp.stable_json_dumps(b) for b in s1.blocks],
            [cp.stable_json_dumps(b) for b in s2.blocks],
        )

    def test_repeated_rows_cells_and_fail_closed_cap(self) -> None:
        data = _simple_odt(
            repeated_rows=[
                (2, [(3, "9.24 repeated synthetic"), (1, "other col")]),
            ]
        )
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/rep.odt"
        )
        self.assertEqual(state.row_count_logical, 2)
        self.assertEqual(state.cell_count_logical, 8)
        desig_blocks = [
            b for b in state.blocks if b["raw_text"].startswith("9.24")
        ]
        self.assertEqual(len(desig_blocks), 2 * 3)
        keys = {b["locator_key"] for b in desig_blocks}
        self.assertEqual(len(keys), 6)
        # Shared xml_element_index across expanded instances of same XML p
        xml_idxs = {b["locator"]["xml_element_index"] for b in desig_blocks}
        self.assertEqual(len(xml_idxs), 1)

        bad = _simple_odt(
            repeated_rows=[
                (cp.ODT_REPEAT_EXPANSION_CAP + 1, [(1, "x")]),
            ]
        )
        with self.assertRaises(oe.OccurrenceExtractError) as ctx:
            oe.parse_odt_blocks(
                bad, artifact_sha=cp.sha256_bytes(bad), relative_path="history/bad.odt"
            )
        self.assertEqual(ctx.exception.code, "invalid_odt_repeat")

        bad2 = _simple_odt(
            repeated_rows=[
                (1, [(-1, "x")]),
            ]
        )
        with self.assertRaises(oe.OccurrenceExtractError):
            oe.parse_odt_blocks(
                bad2,
                artifact_sha=cp.sha256_bytes(bad2),
                relative_path="history/bad2.odt",
            )

    def test_two_column_table_not_joined(self) -> None:
        data = _simple_odt(
            tables=[
                [
                    ["9.24 left version synthetic", "9.24 right version synthetic"],
                ]
            ]
        )
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/twocol.odt"
        )
        occs, _, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/twocol.odt"
        )
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0]["designation_text"], "9.24")
        self.assertEqual(occs[1]["designation_text"], "9.24")
        self.assertNotEqual(occs[0]["locator_key"], occs[1]["locator_key"])
        self.assertNotEqual(occs[0]["raw_text"], occs[1]["raw_text"])
        self.assertNotIn(
            "9.24 left version synthetic 9.24 right version synthetic",
            state.blocks[0]["raw_text"],
        )
        self.assertTrue(all(o["container"] == "table_cell" for o in occs))

    def test_duplicate_designations_retained(self) -> None:
        data = _simple_odt(
            paragraphs=[
                "9.24 first synthetic occurrence",
                "body text synthetic",
                "9.24 second synthetic occurrence",
            ]
        )
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/dup.odt"
        )
        occs, issues, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/dup.odt"
        )
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0]["designation_text"], "9.24")
        self.assertEqual(occs[1]["designation_text"], "9.24")
        self.assertNotEqual(occs[0]["occurrence_id"], occs[1]["occurrence_id"])
        self.assertTrue(
            any(i["issue_code"] == "duplicate_designation_within_release" for i in issues)
        )
        for o in occs:
            self.assertIn("duplicate_designation_in_release", o["ambiguity_flags"])

    def test_no_truncation_long_block(self) -> None:
        long_body = "9.24 " + ("synthetic-token-" * 5000)
        self.assertGreater(len(long_body), 20000)
        data = _simple_odt(paragraphs=[long_body])
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/long.odt"
        )
        self.assertEqual(len(state.blocks), 1)
        self.assertEqual(state.blocks[0]["raw_text"], long_body)
        self.assertEqual(
            state.blocks[0]["raw_text_char_length"], len(long_body)
        )
        self.assertEqual(
            state.blocks[0]["raw_text_byte_length"],
            len(long_body.encode("utf-8")),
        )
        self.assertEqual(
            state.blocks[0]["raw_text_sha256"],
            cp.sha256_text(long_body),
        )
        occs, _, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/long.odt"
        )
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]["raw_text"], long_body)

    def test_source_sha_and_raw_text_hash_reconciliation(self) -> None:
        data = _simple_odt(paragraphs=["3.3.31 givosiran synthetic"])
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/hash.odt"
        )
        for b in state.blocks:
            raw_b = b["raw_text"].encode("utf-8")
            self.assertEqual(cp.sha256_bytes(raw_b), b["raw_text_sha256"])
            self.assertEqual(len(raw_b), b["raw_text_byte_length"])
            self.assertEqual(len(b["raw_text"]), b["raw_text_char_length"])
        occs, _, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/hash.odt"
        )
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]["raw_text_sha256"], state.blocks[0]["raw_text_sha256"])

    def test_invalid_odt_and_missing_content_xml_fail_closed(self) -> None:
        with self.assertRaises(oe.OccurrenceExtractError) as ctx:
            oe.parse_odt_blocks(
                b"not-a-zip",
                artifact_sha="0" * 64,
                relative_path="history/bad.odt",
            )
        self.assertEqual(ctx.exception.code, "invalid_odt_zip")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
            zf.writestr("other.xml", b"<a/>")
        with self.assertRaises(oe.OccurrenceExtractError) as ctx2:
            oe.parse_odt_blocks(
                buf.getvalue(),
                artifact_sha="0" * 64,
                relative_path="history/nocontent.odt",
            )
        self.assertEqual(ctx2.exception.code, "missing_content_xml")

    def test_nested_frame_paragraph_not_merged_into_parent(self) -> None:
        body = (
            "<text:p>outer shell synthetic "
            "<draw:frame><draw:text-box>"
            "<text:p>9.24.Gefitinib frame-only synthetic</text:p>"
            "</draw:text-box></draw:frame>"
            " trailing outer</text:p>"
        )
        data = _odt_bytes_from_body(body)
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/frame.odt",
        )
        self.assertEqual(state.xml_ph_element_count, 2)
        self.assertEqual(state.xml_ph_nested_count, 1)
        self.assertEqual(len(state.emitted_ph_xml_ids), 2)
        outer = [b for b in state.blocks if "outer shell" in b["raw_text"]]
        inner = [
            b
            for b in state.blocks
            if b["raw_text"].startswith("9.24.Gefitinib frame-only")
        ]
        self.assertEqual(len(outer), 1)
        self.assertEqual(len(inner), 1)
        # Parent must not absorb nested designation text.
        self.assertNotIn("9.24.Gefitinib", outer[0]["raw_text"])
        self.assertEqual(len(inner[0]["raw_text"]), len("9.24.Gefitinib frame-only synthetic"))
        self.assertIn(inner[0]["locator"]["in_frame"], (1, True))
        occs, _, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/frame.odt"
        )
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]["designation_text"], "9.24")
        self.assertEqual(occs[0]["raw_text_char_length"], inner[0]["raw_text_char_length"])

    def test_toc_index_blocks_preserved_with_flag(self) -> None:
        body = (
            "<text:table-of-content>"
            "<text:index-body>"
            "<text:p>9.24 index entry synthetic</text:p>"
            "<text:p>plain toc line synthetic</text:p>"
            "</text:index-body>"
            "</text:table-of-content>"
            "<text:p>9.24 body heading synthetic</text:p>"
        )
        data = _odt_bytes_from_body(body)
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/toc.odt",
        )
        self.assertEqual(state.xml_ph_element_count, 3)
        self.assertEqual(len(state.emitted_ph_xml_ids), 3)
        index_blocks = [b for b in state.blocks if b.get("in_index_context")]
        body_blocks = [
            b
            for b in state.blocks
            if not b.get("in_index_context") and "body heading" in b["raw_text"]
        ]
        self.assertEqual(len(index_blocks), 2)
        self.assertEqual(len(body_blocks), 1)
        occs, _, _ = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/toc.odt"
        )
        self.assertEqual(len(occs), 2)
        idx_occ = [o for o in occs if o.get("in_index_context")]
        body_occ = [o for o in occs if not o.get("in_index_context")]
        self.assertEqual(len(idx_occ), 1)
        self.assertEqual(len(body_occ), 1)
        self.assertIn("in_index_context", idx_occ[0]["ambiguity_flags"])

    def test_unknown_container_with_ph_not_silently_dropped(self) -> None:
        body = (
            "<text:section>"
            "<text:p>section child synthetic</text:p>"
            "</text:section>"
            # Custom/unknown producer shell with nested p/h.
            '<office:xyz-unknown xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
            "<text:p>9.24 unknown-shell synthetic</text:p>"
            "</office:xyz-unknown>"
        )
        data = _odt_bytes_from_body(body)
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/unknown.odt",
        )
        self.assertEqual(state.xml_ph_element_count, 2)
        self.assertEqual(len(state.emitted_ph_xml_ids), 2)
        texts = [b["raw_text"] for b in state.blocks]
        self.assertIn("section child synthetic", texts)
        self.assertIn("9.24 unknown-shell synthetic", texts)

    def test_xml_element_index_matches_independent_enumeration(self) -> None:
        body = (
            "<text:h>H synthetic</text:h>"
            "<text:p>P0 synthetic</text:p>"
            "<table:table><table:table-row>"
            "<table:table-cell><text:p>cell synthetic</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
        data = _odt_bytes_from_body(body)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            root = ET.fromstring(zf.read("content.xml"))
        independent = oe.assign_xml_element_indices(root)
        # Map each p/h to independent index
        expected = {}
        for el in root.iter():
            if cp.local_name(el.tag) in ("p", "h"):
                expected[oe.extract_odt_text(el)] = independent[id(el)]
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/idx.odt",
        )
        for b in state.blocks:
            if b["block_kind"] == "empty_table_cell":
                continue
            self.assertIn("xml_element_index", b["locator"])
            self.assertEqual(
                b["locator"]["xml_element_index"],
                expected[b["raw_text"]],
            )
            self.assertEqual(b["xml_element_index"], expected[b["raw_text"]])

    def test_empty_covered_and_merged_cells(self) -> None:
        body = (
            "<table:table>"
            "<table:table-row>"
            '<table:table-cell table:number-columns-spanned="2" '
            'table:number-rows-spanned="2">'
            "<text:p>merged synthetic</text:p>"
            "</table:table-cell>"
            "<table:covered-table-cell/>"
            "</table:table-row>"
            "<table:table-row>"
            "<table:covered-table-cell/>"
            "<table:table-cell/>"
            "</table:table-row>"
            '<table:table-row table:number-rows-repeated="2">'
            '<table:table-cell table:number-columns-repeated="2" '
            'table:number-columns-spanned="1">'
            "</table:table-cell>"
            "</table:table-row>"
            "</table:table>"
        )
        data = _odt_bytes_from_body(body)
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/cells.odt",
        )
        # One real paragraph + empty/covered cells as empty_table_cell blocks
        empty_blocks = [
            b for b in state.blocks if b["block_kind"] == "empty_table_cell"
        ]
        self.assertGreaterEqual(len(empty_blocks), 1)
        for b in empty_blocks:
            self.assertEqual(b["raw_text"], "")
            self.assertEqual(b["raw_text_byte_length"], 0)
            self.assertEqual(b["raw_text_char_length"], 0)
            self.assertEqual(
                b["raw_text_sha256"], cp.sha256_bytes(b"")
            )
            self.assertIn("table_index", b["locator"])
            self.assertIn("cell_logical_index", b["locator"])
            self.assertIn("number_columns_spanned", b["locator"])
            self.assertIn("number_rows_spanned", b["locator"])
        merged = [b for b in state.blocks if b["raw_text"] == "merged synthetic"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["locator"]["number_columns_spanned"], 2)
        self.assertEqual(merged[0]["locator"]["number_rows_spanned"], 2)
        # Empty cells with row/col repeat expand logically
        self.assertGreaterEqual(state.empty_table_cell_block_count, 1)
        self.assertEqual(state.empty_cell_count, state.empty_table_cell_block_count)

        # Invalid span fails closed
        bad = _odt_bytes_from_body(
            "<table:table><table:table-row>"
            '<table:table-cell table:number-columns-spanned="0">'
            "<text:p>x</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
        with self.assertRaises(oe.OccurrenceExtractError) as ctx:
            oe.parse_odt_blocks(
                bad,
                artifact_sha=cp.sha256_bytes(bad),
                relative_path="history/badspan.odt",
            )
        self.assertEqual(ctx.exception.code, "invalid_odt_repeat")

    def test_quantity_not_emitted_as_occurrence_but_block_kept(self) -> None:
        data = _simple_odt(
            paragraphs=[
                "0.5 mL synthetic quantity",
                "9.24.Gefitinib synthetic heading",
                "563.741 synthetic big",
            ]
        )
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/qty.odt",
        )
        self.assertEqual(len(state.blocks), 3)
        occs, issues, rej = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/qty.odt"
        )
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]["designation_text"], "9.24")
        self.assertGreaterEqual(sum(rej.values()), 2)
        self.assertTrue(
            any(
                i["issue_code"] == "numeric_quantity_rejected_from_occurrence"
                for i in issues
            )
        )


class PathAndInventoryTests(unittest.TestCase):
    def test_default_stage_root_follows_checkout_not_literal_tmp(self) -> None:
        root = oe.repository_root()
        stage = oe.default_stage_root()
        self.assertEqual(stage, root / ".work" / "nhi-rule-history-stage" / "grok-occurrences")
        # Source must not hard-code the temporary worktree path string.
        src = Path(oe.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/tmp/nhi-rule-history-rebuild.IYmPnX", src)
        self.assertEqual(oe.DEFAULT_STAGE_ROOT, stage)

    def test_cli_parser_has_no_unrestricted_stage_flag(self) -> None:
        p = oe.build_arg_parser()
        # Parse known-good minimal args shape
        with self.assertRaises(SystemExit):
            # missing required → SystemExit
            p.parse_args([])
        # Flag must not exist
        with self.assertRaises(SystemExit):
            p.parse_args(
                [
                    "--history-dir",
                    "h",
                    "--accepted-manifest",
                    "m",
                    "--stage-dir",
                    "s",
                    "--receipt-dir",
                    "r",
                    "--allow-unrestricted-stage",
                ]
            )
        actions = [a.dest for a in p._actions]
        self.assertNotIn("allow_unrestricted_stage", actions)

    def test_list_history_rejects_extra_docx_txt_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hist = root / "history"
            hist.mkdir()
            odt = _simple_odt(paragraphs=["x"])
            (hist / "a.odt").write_bytes(odt)
            (hist / "extra.docx").write_bytes(b"PK fake")
            with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                oe.list_history_odt(hist, accepted_basenames={"a.odt"})
            self.assertEqual(ctx.exception.code, "history_unsupported_artifact")

            # clean and test txt
            (hist / "extra.docx").unlink()
            (hist / "notes.txt").write_text("nope", encoding="utf-8")
            with self.assertRaises(oe.OccurrenceExtractError) as ctx2:
                oe.list_history_odt(hist, accepted_basenames={"a.odt"})
            self.assertEqual(ctx2.exception.code, "history_unsupported_artifact")
            (hist / "notes.txt").unlink()

            # symlink
            target = root / "target.odt"
            target.write_bytes(odt)
            link = hist / "link.odt"
            link.symlink_to(target)
            with self.assertRaises(oe.OccurrenceExtractError) as ctx3:
                oe.list_history_odt(hist, accepted_basenames={"a.odt", "link.odt"})
            self.assertEqual(ctx3.exception.code, "history_symlink_forbidden")

    def test_duplicate_manifest_relative_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            man = root / "manifest.jsonl"
            row = {
                "basename": "a.odt",
                "byte_length": 1,
                "lane": "history",
                "relative_path": "history/a.odt",
                "sha256": "0" * 64,
                "extension": "odt",
            }
            man.write_text(
                cp.stable_json_dumps(row) + "\n" + cp.stable_json_dumps(row) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                oe.load_accepted_history_manifest(man)
            self.assertEqual(ctx.exception.code, "duplicate_manifest_relative_path")


class TrackedSchemaTests(unittest.TestCase):
    def test_release_has_no_effective_date_keys(self) -> None:
        data = _simple_odt(paragraphs=["9.24 synthetic"])
        sha = cp.sha256_bytes(data)
        state = oe.parse_odt_blocks(
            data, artifact_sha=sha, relative_path="history/r.odt"
        )
        occs, _, rej = oe.blocks_to_occurrences(
            state.blocks, relative_path="history/r.odt"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.odt"
            path.write_bytes(data)
            row = oe.build_release_row(
                path=path,
                data=data,
                source_order_index=0,
                state=state,
                occurrence_count=len(occs),
                accepted={"sha256": sha},
                numeric_rejection_count=sum(rej.values()),
            )
        for k in row:
            self.assertNotIn("effective_date", k)
        tracked = oe.strip_for_tracked_release(row)
        for k in tracked:
            self.assertNotIn("effective_date", k)

    def test_allowlist_rejects_excerpt_rule_text_and_prose(self) -> None:
        base = {
            "schema": oe.SCHEMA_OCCURRENCE,
            "occurrence_id": "a" * 64,
            "artifact_sha256": "b" * 64,
            "relative_path": "history/x.odt",
            "designation_text": "9.24",
            "block_id": "c" * 64,
            "locator": {"doc_order": 0, "xml_element_index": 1},
            "locator_key": "doc_order=0|xml_element_index=1",
            "raw_text_sha256": "d" * 64,
            "raw_text_byte_length": 10,
            "raw_text_char_length": 10,
            "parser_version": oe.PARSER_VERSION,
            "ambiguity_flags": ["source_local_candidate_only"],
            "container": "flow",
            "match_start_in_raw": 0,
            "match_end_in_raw": 4,
            "statement": "source-local only",
            "in_index_context": False,
        }
        with self.assertRaises(oe.OccurrenceExtractError) as ctx:
            oe.strip_for_tracked_occurrence({**base, "excerpt": "leaked"})
        self.assertEqual(ctx.exception.code, "tracked_schema_unknown_field")

        with self.assertRaises(oe.OccurrenceExtractError) as ctx2:
            oe.strip_for_tracked_occurrence({**base, "rule_text": "leaked"})
        self.assertEqual(ctx2.exception.code, "tracked_schema_unknown_field")

        # Long English prose injected into a non-disclaimer allowed string field
        long_en = (
            "This is a long English synthetic source paragraph that should not "
            "appear inside a tracked receipt field because it looks like body prose "
            "copied from a document and exceeds the safety threshold for leakage."
        )
        bad = {**base, "designation_text": long_en}
        tracked = oe.strip_for_tracked_occurrence(bad)
        with self.assertRaises(oe.OccurrenceExtractError) as ctx3:
            oe.assert_no_tracked_leakage(tracked, path="occurrence")
        self.assertEqual(ctx3.exception.code, "tracked_receipt_prose_sample")

        # Sub-500 CJK source-like string in a non-disclaimer field
        cjk = "健保給付規定測試字串" * 20  # 200 chars CJK-heavy, under 500
        self.assertLess(len(cjk), 500)
        bad2 = {**base, "locator_key": cjk}
        tracked2 = oe.strip_for_tracked_occurrence(bad2)
        with self.assertRaises(oe.OccurrenceExtractError) as ctx4:
            oe.assert_no_tracked_leakage(tracked2, path="occurrence")
        self.assertEqual(ctx4.exception.code, "tracked_receipt_prose_sample")

    def test_markdown_report_rejects_metadata_and_credentials(self) -> None:
        for marker in ("author: synthetic", "password=synthetic", "host=synthetic"):
            with self.subTest(marker=marker):
                with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                    oe.assert_markdown_report_safe(marker, path="report.md")
                self.assertEqual(
                    ctx.exception.code,
                    "tracked_report_meta_leakage",
                )


class PipelineReceiptTests(unittest.TestCase):
    def _fourteen_synthetic(self) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for i, name in enumerate(_fourteen_names()):
            paras = [
                f"9.24 synthetic release {i}",
                f"body {i} " + ("x" * (10 + i)),
            ]
            if i % 3 == 0:
                paras.append(f"3.3.13 synthetic {i}")
            if i % 5 == 0:
                paras.append(f"9.24 duplicate synthetic {i}")
            if i % 7 == 0:
                paras.append("0.5 mL synthetic qty")
            tables = None
            if i % 4 == 0:
                tables = [[["left col synthetic", "right col synthetic"]]]
            files[name] = _simple_odt(paragraphs=paras, tables=tables)
        return files

    def test_full_pipeline_tracked_receipts_clean_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = self._fourteen_synthetic()
            hist = _write_history_fixture(root, files)
            man = root / "manifest.jsonl"
            stage1 = root / "stage1"
            stage2 = root / "stage2"
            rec1 = root / "rec1"
            rec2 = root / "rec2"

            r1 = oe.extract_history_corpus(
                history_dir=hist,
                accepted_manifest=man,
                stage_dir=stage1,
                receipt_dir=rec1,
                allow_unrestricted_stage=True,
            )
            r2 = oe.extract_history_corpus(
                history_dir=hist,
                accepted_manifest=man,
                stage_dir=stage2,
                receipt_dir=rec2,
                allow_unrestricted_stage=True,
            )
            self.assertEqual(r1["digests"], r2["digests"])
            self.assertEqual(r1["summary"]["release_count"], 14)
            self.assertTrue(r1["summary"]["all_source_sha_match_accepted_manifest"])
            self.assertEqual(r1["summary"]["xml_ph_unaccounted_total"], 0)

            for name in (
                "release-index.jsonl",
                "occurrence-index.jsonl",
                "issues.jsonl",
                "canary-occurrences.jsonl",
            ):
                text = (rec1 / name).read_text(encoding="utf-8")
                self.assertNotIn("/Users/", text)
                self.assertNotIn("/tmp/nhi-rule-history-rebuild", text)
                for line in text.splitlines():
                    if not line:
                        continue
                    row = json.loads(line)
                    self.assertNotIn("raw_text", row)
                    self.assertNotIn("normalized_search_text", row)
                    self.assertNotIn("excerpt", row)
                    self.assertNotIn("rule_text", row)
                    for k in row:
                        self.assertNotIn("effective_date", k)
                    oe.assert_no_tracked_leakage(row, path=name)

            summary = json.loads((rec1 / "summary.json").read_text(encoding="utf-8"))
            oe.assert_no_tracked_leakage(summary, path="summary")
            self.assertNotIn("raw_text", summary)

            stage_occ = (stage1 / "occurrences.jsonl").read_text(encoding="utf-8")
            self.assertIn("raw_text", stage_occ)

            bad_hist = root / "bad_history"
            bad_hist.mkdir()
            first = sorted(files.keys())[0]
            for name, data in files.items():
                if name == first:
                    (bad_hist / name).write_bytes(
                        _simple_odt(paragraphs=["totally different synthetic"])
                    )
                else:
                    (bad_hist / name).write_bytes(data)
            with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                oe.extract_history_corpus(
                    history_dir=bad_hist,
                    accepted_manifest=man,
                    stage_dir=root / "stage_bad",
                    receipt_dir=root / "rec_bad",
                    allow_unrestricted_stage=True,
                )
            self.assertEqual(ctx.exception.code, "source_sha_mismatch")

    def test_unsafe_stage_dir_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = self._fourteen_synthetic()
            hist = _write_history_fixture(root, files)
            man = root / "manifest.jsonl"
            with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                oe.extract_history_corpus(
                    history_dir=hist,
                    accepted_manifest=man,
                    stage_dir=root / "not-allowed-stage",
                    receipt_dir=root / "rec",
                    allow_unrestricted_stage=False,
                )
            self.assertEqual(ctx.exception.code, "unsafe_stage_dir")

    def test_wrong_artifact_count_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = dict(list(self._fourteen_synthetic().items())[:13])
            hist = _write_history_fixture(root, files)
            man = root / "manifest.jsonl"
            with self.assertRaises(oe.OccurrenceExtractError) as ctx:
                oe.extract_history_corpus(
                    history_dir=hist,
                    accepted_manifest=man,
                    stage_dir=root / "stage",
                    receipt_dir=root / "rec",
                    allow_unrestricted_stage=True,
                )
            self.assertIn(
                ctx.exception.code,
                {
                    "history_artifact_count",
                    "accepted_manifest_history_count",
                    "history_artifact_set_mismatch",
                },
            )


class SpaceExpansionTests(unittest.TestCase):
    def test_text_s_and_tab_expansion(self) -> None:
        body = (
            "<text:p>9.24"
            '<text:s text:c="3"/>'
            "between"
            "<text:tab/>"
            "end</text:p>"
        )
        data = _odt_bytes_from_body(body)
        state = oe.parse_odt_blocks(
            data,
            artifact_sha=cp.sha256_bytes(data),
            relative_path="history/spaces.odt",
        )
        self.assertEqual(state.blocks[0]["raw_text"], "9.24   between\tend")


if __name__ == "__main__":
    unittest.main()
