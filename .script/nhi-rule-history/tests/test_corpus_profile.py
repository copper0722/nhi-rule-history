#!/usr/bin/env python3
"""Focused tests for the NHI rule-history corpus profiler.

Synthetic ODT/DOCX ZIP fixtures are built at test runtime. Official binaries
are never copied into the test tree.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Load sibling modules without requiring a hyphen-free import name.
_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import corpus_profile as cp  # noqa: E402


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _odt_bytes(
    *,
    paragraphs: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
    meta_dates: dict[str, str] | None = None,
    title: str = "test",
    creator: str = "Synthetic Creator",
    generator: str = "TestGenerator/1.0",
    corrupt: bool = False,
    repeated_rows: list[tuple[int, list[tuple[int, str]]]] | None = None,
) -> bytes:
    """Build a minimal ODT (ZIP) in memory using hand-written XML.

    ``repeated_rows``: list of (row_repeat, [(col_repeat, cell_text), ...]).
    When provided, overrides ``tables`` for body construction.
    """
    paragraphs = paragraphs or ["hello"]
    tables = tables or []
    meta_dates = meta_dates or {"creation-date": "2020-01-01T00:00:00Z"}

    body_parts: list[str] = []
    for p in paragraphs:
        body_parts.append(f"<text:p>{_xml_escape(p)}</text:p>")

    if repeated_rows is not None:
        body_parts.append("<table:table>")
        for row_rep, cells in repeated_rows:
            body_parts.append(
                f'<table:table-row table:number-rows-repeated="{row_rep}">'
            )
            for col_rep, cell_text in cells:
                body_parts.append(
                    f'<table:table-cell table:number-columns-repeated="{col_rep}">'
                    f"<text:p>{_xml_escape(cell_text)}</text:p>"
                    "</table:table-cell>"
                )
            body_parts.append("</table:table-row>")
        body_parts.append("</table:table>")
    else:
        for table in tables:
            body_parts.append("<table:table>")
            for row in table:
                body_parts.append("<table:table-row>")
                for cell in row:
                    body_parts.append(
                        "<table:table-cell>"
                        f"<text:p>{_xml_escape(cell)}</text:p>"
                        "</table:table-cell>"
                    )
                body_parts.append("</table:table-row>")
            body_parts.append("</table:table>")

    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        "<office:body><office:text>"
        + "".join(body_parts)
        + "</office:text></office:body></office:document-content>"
    )

    meta_parts = [
        f"<dc:title>{_xml_escape(title)}</dc:title>",
        f"<dc:creator>{_xml_escape(creator)}</dc:creator>",
        f"<meta:generator>{_xml_escape(generator)}</meta:generator>",
        f"<meta:initial-creator>{_xml_escape(creator)}</meta:initial-creator>",
    ]
    for k, v in meta_dates.items():
        if k == "date":
            meta_parts.append(f"<dc:date>{_xml_escape(v)}</dc:date>")
        else:
            meta_parts.append(f"<meta:{k}>{_xml_escape(v)}</meta:{k}>")
    meta_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<office:meta>"
        + "".join(meta_parts)
        + "</office:meta></office:document-meta>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content_xml.encode("utf-8"))
        zf.writestr("meta.xml", meta_xml.encode("utf-8"))
        zf.writestr(
            "META-INF/manifest.xml",
            b'<?xml version="1.0"?><manifest:manifest '
            b'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>',
        )
    data = buf.getvalue()
    if corrupt:
        return data[:20] + b"\x00\x01\x02\x03" + data[40:60]
    return data


def _docx_bytes(
    *,
    paragraphs: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
    core_dates: dict[str, str] | None = None,
    core_extra: dict[str, str] | None = None,
    corrupt: bool = False,
) -> bytes:
    paragraphs = paragraphs or ["docx hello"]
    tables = tables or []
    core_dates = core_dates or {
        "created": "2021-06-15T12:00:00Z",
        "modified": "2021-06-16T12:00:00Z",
    }
    core_extra = core_extra or {}

    body_parts: list[str] = []
    for p in paragraphs:
        body_parts.append(
            f"<w:p><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>"
        )
    for table in tables:
        body_parts.append("<w:tbl>")
        for row in table:
            body_parts.append("<w:tr>")
            for cell in row:
                body_parts.append(
                    "<w:tc><w:p><w:r>"
                    f"<w:t>{_xml_escape(cell)}</w:t>"
                    "</w:r></w:p></w:tc>"
                )
            body_parts.append("</w:tr>")
        body_parts.append("</w:tbl>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_parts)
        + "</w:body></w:document>"
    )

    core_parts: list[str] = []
    for k, v in core_dates.items():
        if k in ("created", "modified"):
            core_parts.append(
                f'<dcterms:{k} xsi:type="dcterms:W3CDTF">{_xml_escape(v)}</dcterms:{k}>'
            )
        elif k == "lastPrinted":
            core_parts.append(
                f'<cp:lastPrinted>{_xml_escape(v)}</cp:lastPrinted>'
            )
        else:
            core_parts.append(f"<cp:{k}>{_xml_escape(v)}</cp:{k}>")
    for k, v in core_extra.items():
        if k in ("title", "creator", "subject"):
            core_parts.append(f"<dc:{k}>{_xml_escape(v)}</dc:{k}>")
        else:
            core_parts.append(f"<cp:{k}>{_xml_escape(v)}</cp:{k}>")
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<cp:coreProperties "
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + "".join(core_parts)
        + "</cp:coreProperties>"
    )

    app_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        "<Application>TestApp</Application>"
        "<Company>TestCompany</Company>"
        "</Properties>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            b'<?xml version="1.0"?><Types '
            b'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("word/document.xml", document_xml.encode("utf-8"))
        zf.writestr("docProps/core.xml", core_xml.encode("utf-8"))
        zf.writestr("docProps/app.xml", app_xml.encode("utf-8"))
    data = buf.getvalue()
    if corrupt:
        return data[:15] + b"CORRUPT" + data[30:50]
    return data


class CorpusProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nhi-profile-test-"))
        self.corpus = self.tmp / "corpus"
        (self.corpus / "history").mkdir(parents=True)
        (self.corpus / "current").mkdir(parents=True)
        self.out = self.tmp / "out"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, data: bytes) -> Path:
        path = self.corpus / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_stable_ordering_and_byte_identical_reruns(self) -> None:
        self._write(
            "history/7853_96年7月版.odt",
            _odt_bytes(paragraphs=["rule 1.3.5 ADHD"], title="96年7月版"),
        )
        self._write(
            "history/7811_109年版.odt",
            _odt_bytes(paragraphs=["later"], title="109"),
        )
        self._write(
            "current/42495_通則(113.05.28更新).odt",
            _odt_bytes(paragraphs=["current body 9.24"]),
        )
        self._write(
            "current/42509_第五節(114.05.23更新).docx",
            _docx_bytes(paragraphs=["docx body"]),
        )

        r1 = cp.profile_corpus(self.corpus)
        d1 = cp.write_profile_outputs(r1, self.out / "a")
        r2 = cp.profile_corpus(self.corpus)
        d2 = cp.write_profile_outputs(r2, self.out / "b")
        self.assertEqual(d1, d2)
        paths = [row["relative_path"] for row in r1["manifest_rows"]]
        self.assertEqual(paths, sorted(paths, key=lambda p: p.encode("utf-8")))
        blob = (self.out / "a" / "manifest.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(str(self.corpus), blob)
        self.assertNotIn("hostname", blob.lower())

    def test_valid_and_corrupt_containers(self) -> None:
        self._write("history/7853_96年7月版.odt", _odt_bytes())
        ok = cp.profile_corpus(self.corpus)
        self.assertEqual(ok["summary"]["observed_total"], 1)
        self.assertTrue(ok["manifest_rows"][0]["zip_valid"])

        self._write("history/7811_109年版.odt", _odt_bytes(corrupt=True))
        with self.assertRaises(cp.CorpusProfileError) as ctx:
            cp.profile_corpus(self.corpus)
        self.assertEqual(ctx.exception.code, "invalid_container")

        shutil.rmtree(self.corpus / "history")
        (self.corpus / "history").mkdir()
        self._write("current/x_bad(113.01.01更新).docx", _docx_bytes(corrupt=True))
        with self.assertRaises(cp.CorpusProfileError) as ctx2:
            cp.profile_corpus(self.corpus)
        self.assertEqual(ctx2.exception.code, "invalid_container")

    def test_filename_label_not_promoted_to_legal_date(self) -> None:
        info = cp.parse_filename_label("7853_96年7月版.odt")
        self.assertEqual(info["filename_label_raw"], "96年7月版")
        self.assertIn("96年7月", info["filename_date_fragments_raw"])
        self.assertEqual(
            info["filename_date_parse_status"], "fragments_captured_raw_only"
        )
        note_blob = " ".join(info["filename_date_parse_notes"])
        self.assertIn("not legal", note_blob)

        info2 = cp.parse_filename_label("42495_通則(113.05.28更新).odt")
        self.assertEqual(info2["filename_date_fragments_raw"], ["113.05.28"])

        info3 = cp.parse_filename_label("42528_第十一節 解毒劑.odt")
        self.assertEqual(
            info3["filename_date_parse_status"], "label_without_date_fragment"
        )

    def test_core_property_metadata_labelled_nonlegal(self) -> None:
        self._write(
            "current/a_x(113.05.28更新).docx",
            _docx_bytes(
                core_dates={
                    "created": "2024-05-28T00:00:00Z",
                    "modified": "2024-05-28T00:00:00Z",
                },
                core_extra={"title": "embedded title", "creator": "Alice"},
            ),
        )
        result = cp.profile_corpus(self.corpus)
        row = result["manifest_rows"][0]
        self.assertIn("NOT legal effective dates", row["file_metadata_note"])
        self.assertIn("core:created", row["file_metadata_dates"])
        self.assertEqual(
            row["file_metadata_dates"]["core:created"], "2024-05-28T00:00:00Z"
        )

    def test_metadata_dates_exclude_title_creator_company_generator(self) -> None:
        """Regression: file_metadata_dates must be date/time fields only."""
        self._write(
            "history/7853_96年7月版.odt",
            _odt_bytes(
                title="ShouldNotAppear",
                creator="CreatorShouldNotAppear",
                generator="GeneratorShouldNotAppear",
                meta_dates={
                    "creation-date": "2010-01-01T00:00:00Z",
                    "date": "2010-02-01T00:00:00Z",
                    "print-date": "2010-03-01T00:00:00Z",
                },
            ),
        )
        self._write(
            "current/a_x(113.05.28更新).docx",
            _docx_bytes(
                core_dates={
                    "created": "2024-01-01T00:00:00Z",
                    "modified": "2024-02-01T00:00:00Z",
                    "lastPrinted": "2024-03-01T00:00:00Z",
                },
                core_extra={
                    "title": "DocxTitle",
                    "creator": "DocxCreator",
                    "revision": "99",
                },
            ),
        )
        result = cp.profile_corpus(self.corpus)
        blob = json.dumps(result["manifest_rows"], ensure_ascii=False)
        for banned in (
            "ShouldNotAppear",
            "CreatorShouldNotAppear",
            "GeneratorShouldNotAppear",
            "DocxTitle",
            "DocxCreator",
            "TestCompany",
            "TestApp",
            "meta:generator",
            "meta:title",
            "meta:creator",
            "core:title",
            "core:creator",
            "core:revision",
            "app:Company",
            "app:Application",
        ):
            self.assertNotIn(banned, blob)
        by_path = {r["relative_path"]: r for r in result["manifest_rows"]}
        odt_keys = set(by_path["history/7853_96年7月版.odt"]["file_metadata_dates"])
        self.assertEqual(
            odt_keys, {"meta:creation-date", "meta:date", "meta:print-date"}
        )
        docx_keys = set(
            by_path["current/a_x(113.05.28更新).docx"]["file_metadata_dates"]
        )
        self.assertEqual(
            docx_keys, {"core:created", "core:lastPrinted", "core:modified"}
        )

    def test_tables_preserved_as_structural_counts(self) -> None:
        table = [[["old text", "new text"], ["a", "b"]]]
        self._write(
            "history/7853_96年7月版.odt",
            _odt_bytes(
                paragraphs=["intro"],
                tables=[[["c1", "c2"], ["c3", "c4"], ["c5", "c6"]]],
            ),
        )
        self._write(
            "current/b_y(114.01.01更新).docx",
            _docx_bytes(paragraphs=["p"], tables=table),
        )
        result = cp.profile_corpus(self.corpus)
        by_path = {r["relative_path"]: r for r in result["manifest_rows"]}
        odt = by_path["history/7853_96年7月版.odt"]
        self.assertEqual(odt["table_count"], 1)
        self.assertEqual(odt["table_row_count"], 3)
        self.assertEqual(odt["table_cell_count"], 6)
        self.assertEqual(odt["table_row_count_xml"], 3)
        self.assertEqual(odt["table_cell_count_xml"], 6)
        self.assertIn(
            "tables_present_structural_counts_only_not_flattened", odt["warnings"]
        )
        self.assertNotIn("canonical_rule_text", odt)
        self.assertNotIn("flattened_table_text", odt)

    def test_odt_repeat_attributes_expand_logical_counts(self) -> None:
        """Regression: ODT number-rows/columns-repeated expand with cap."""
        self._write(
            "history/7853_96年7月版.odt",
            _odt_bytes(
                paragraphs=["body"],
                # 1 XML row with row_rep=3, two cells: col_rep 2 and col_rep 1
                # → rows_xml=1, cells_xml=2, rows_logical=3,
                #   cells_logical = (2*3) + (1*3) = 9
                repeated_rows=[
                    (3, [(2, "A"), (1, "B")]),
                ],
            ),
        )
        result = cp.profile_corpus(self.corpus)
        row = result["manifest_rows"][0]
        self.assertTrue(row["odt_repeat_attrs_present"])
        self.assertEqual(row["table_row_count_xml"], 1)
        self.assertEqual(row["table_cell_count_xml"], 2)
        self.assertEqual(row["table_row_count"], 3)
        self.assertEqual(row["table_cell_count"], 9)
        self.assertEqual(row["odt_rows_repeated_attr_count"], 1)
        self.assertEqual(row["odt_columns_repeated_attr_count"], 2)
        codes = {i["issue_code"] for i in result["issues"]}
        self.assertIn("odt_table_repeat_attributes_present", codes)

    def test_odt_invalid_repeat_fail_closed(self) -> None:
        # Build manually with negative repeat.
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
            "<office:body><office:text>"
            "<table:table>"
            '<table:table-row table:number-rows-repeated="-1">'
            "<table:table-cell><text:p>x</text:p></table:table-cell>"
            "</table:table-row>"
            "</table:table>"
            "</office:text></office:body></office:document-content>"
        )
        meta_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-meta '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<office:meta>"
            "<meta:creation-date>2020-01-01T00:00:00Z</meta:creation-date>"
            "</office:meta></office:document-meta>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", content_xml.encode("utf-8"))
            zf.writestr("meta.xml", meta_xml.encode("utf-8"))
        self._write("history/7853_96年7月版.odt", buf.getvalue())
        with self.assertRaises(cp.CorpusProfileError) as ctx:
            cp.profile_corpus(self.corpus)
        self.assertEqual(ctx.exception.code, "invalid_odt_repeat")

    def test_no_symlink_traversal(self) -> None:
        self._write("history/7853_96年7月版.odt", _odt_bytes(paragraphs=["in-scope"]))
        outside = self.tmp / "outside.odt"
        outside.write_bytes(_odt_bytes(paragraphs=["secret outside canary-XYZ"]))
        link = self.corpus / "history" / "linked.odt"
        link.symlink_to(outside)

        result = cp.profile_corpus(self.corpus)
        paths = [r["relative_path"] for r in result["manifest_rows"]]
        self.assertEqual(paths, ["history/7853_96年7月版.odt"])
        text_blob = json.dumps(result["canary_hits"], ensure_ascii=False)
        self.assertNotIn("canary-XYZ", text_blob)

    def test_unsupported_extension_fail_closed(self) -> None:
        self._write("history/7853_96年7月版.odt", _odt_bytes())
        bad = self.corpus / "history" / "notes.txt"
        bad.write_text("nope", encoding="utf-8")
        with self.assertRaises(cp.CorpusProfileError) as ctx:
            cp.profile_corpus(self.corpus)
        self.assertEqual(ctx.exception.code, "unsupported_extension")

    def test_manifest_count_mismatch_fail_closed(self) -> None:
        self._write("history/7853_96年7月版.odt", _odt_bytes())
        result = cp.profile_corpus(self.corpus)
        result = dict(result)
        result["summary"] = dict(result["summary"])
        result["summary"]["observed_total"] = 999
        with self.assertRaises(cp.CorpusProfileError) as ctx:
            cp.write_profile_outputs(result, self.out / "mismatch")
        self.assertEqual(ctx.exception.code, "manifest_count_mismatch")

    def test_duplicate_relative_path_impossible_via_walk(self) -> None:
        self._write("current/a_x(113.01.01更新).odt", _odt_bytes())
        result = cp.profile_corpus(self.corpus)
        self.assertEqual(len(result["manifest_rows"]), 1)

    def test_canary_search_locator_fields(self) -> None:
        self._write(
            "current/c_z(115.04.23更新).odt",
            _odt_bytes(
                paragraphs=["intro", "Article 9.138 aumolertinib birth", "tail"]
            ),
        )
        result = cp.profile_corpus(self.corpus)
        hits = [
            h
            for h in result["canary_hits"]
            if h["canary"] in ("9.138", "aumolertinib")
        ]
        self.assertGreaterEqual(len(hits), 2)
        for h in hits:
            self.assertFalse(h["identity_claim"])
            self.assertFalse(h["legal_date_claim"])
            self.assertEqual(h["relative_path"], "current/c_z(115.04.23更新).odt")
            self.assertIn("paragraph_index_approx", h)

    def test_dotted_canary_token_boundaries(self) -> None:
        """Regression: 9.24 must not match 9.240 / 19.24 / 9.24.1."""
        # Positive: exact 9.24 and case-insensitive drug name.
        self._write(
            "current/pos(115.01.01更新).odt",
            _odt_bytes(
                paragraphs=[
                    "rule 9.24 applies here",
                    "Gilteritinib is listed",
                ]
            ),
        )
        # Negative neighbors only.
        self._write(
            "current/neg(115.01.01更新).odt",
            _odt_bytes(
                paragraphs=[
                    "neighbor values only: 9.240 and 19.24 and 9.24.1",
                ]
            ),
        )
        result = cp.profile_corpus(
            self.corpus, canaries=("9.24", "gilteritinib")
        )
        hits_924 = [h for h in result["canary_hits"] if h["canary"] == "9.24"]
        paths_924 = {h["relative_path"] for h in hits_924}
        self.assertIn("current/pos(115.01.01更新).odt", paths_924)
        self.assertNotIn("current/neg(115.01.01更新).odt", paths_924)
        self.assertEqual(len(hits_924), 1)

        hits_drug = [
            h for h in result["canary_hits"] if h["canary"] == "gilteritinib"
        ]
        self.assertEqual(len(hits_drug), 1)
        self.assertEqual(hits_drug[0]["match_text"], "Gilteritinib")

        # Direct pattern unit checks.
        pat = cp.compile_canary_pattern("9.24")
        self.assertIsNotNone(pat.search("x 9.24 y"))
        self.assertIsNone(pat.search("9.240"))
        self.assertIsNone(pat.search("19.24"))
        self.assertIsNone(pat.search("9.24.1"))
        self.assertIsNotNone(pat.search("(9.24)"))
        # Trailing '.' + letter is not a longer numeric suffix.
        self.assertIsNotNone(pat.search("9.24.Agalsidase"))
        pat2 = cp.compile_canary_pattern("3.3.13")
        self.assertIsNotNone(pat2.search("3.3.13.Agalsidase"))
        self.assertIsNone(pat2.search("3.3.130"))
        self.assertIsNone(pat2.search("3.3.13.1"))

    def test_historical_release_chronology_96_to_109(self) -> None:
        """Regression: chronology candidates sort 96.7 → … → 109."""
        # Write in reverse path order so bytewise path order ≠ chronology.
        labels = [
            ("7811", "109年版"),
            ("7813", "108年版"),
            ("7849", "98年版"),
            ("7851", "97年9月版"),
            ("7853", "96年7月版"),
        ]
        for fid, label in labels:
            self._write(
                f"history/{fid}_{label}.odt",
                _odt_bytes(paragraphs=[label]),
            )
        result = cp.profile_corpus(self.corpus)
        chrono = result["summary"]["historical_release_chronology_candidates"]
        keys = [c["analysis_sort_key"] for c in chrono]
        self.assertEqual(
            keys,
            ["096.07", "097.09", "098.01", "108.01", "109.01"],
        )
        self.assertEqual(
            [c["filename_label_raw"] for c in chrono],
            ["96年7月版", "97年9月版", "98年版", "108年版", "109年版"],
        )
        for c in chrono:
            self.assertTrue(c["not_legal_effective_date"])
            self.assertIn("NOT a legal effective date", c["statement"])
        # Path-order sequence still present and different ordering.
        path_seq = result["summary"]["historical_release_label_sequence"]
        path_labels = [x["filename_label_raw"] for x in path_seq]
        self.assertEqual(path_labels[0], "109年版")  # bytewise path first

    def test_historical_label_unparseable_issues(self) -> None:
        self._write(
            "history/9999_weirdlabel.odt",
            _odt_bytes(paragraphs=["x"]),
        )
        result = cp.profile_corpus(self.corpus)
        codes = [i for i in result["issues"] if i["issue_code"] == (
            "historical_release_label_chronology_unparseable"
        )]
        self.assertEqual(len(codes), 1)
        self.assertEqual(codes[0]["severity"], "error")
        self.assertEqual(codes[0]["relative_path"], "history/9999_weirdlabel.odt")

    def test_issue_schema_severity_enum_not_path(self) -> None:
        """Regression: severity is info|warning|error; path is relative_path."""
        self._write(
            "current/42528_第十一節 解毒劑.odt",
            _odt_bytes(paragraphs=["no date in name"]),
        )
        result = cp.profile_corpus(self.corpus)
        self.assertTrue(result["issues"])
        for issue in result["issues"]:
            self.assertIn(issue["severity"], ("info", "warning", "error"))
            self.assertIn("relative_path", issue)
            self.assertIn("issue_code", issue)
            self.assertIn("detail", issue)
            self.assertIn("issue_class", issue)
            # Must not put a path into severity.
            self.assertNotIn("/", issue["severity"] or "")
            self.assertNotIn("\\", issue["severity"] or "")
        # Report must render severity enum, not a path as severity.
        report = result["quality_report_md"]
        self.assertIn("[info]", report)
        digests = cp.write_profile_outputs(result, self.out / "schema")
        issues_blob = (self.out / "schema" / "issues.jsonl").read_text(
            encoding="utf-8"
        )
        for line in issues_blob.splitlines():
            obj = json.loads(line)
            self.assertIn(obj["severity"], ("info", "warning", "error"))
        self.assertTrue(digests)

    def test_freshness_gap_uses_cited_anchor_only(self) -> None:
        self._write(
            "current/42535_第十四節(115.04.23更新).odt",
            _odt_bytes(paragraphs=["x"]),
        )
        result = cp.profile_corpus(self.corpus)
        fg = result["summary"]["freshness_gap"]
        self.assertEqual(fg["cited_official_whole_file_anchor_label"], "115.07.23")
        self.assertEqual(
            fg["freshness_assessment"],
            "local_current_filename_labels_older_than_cited_anchor",
        )

    def test_entrypoint_docs_do_not_claim_package_module(self) -> None:
        """Regression: docs must not claim hyphenated dir is a runnable package."""
        main_py = (_PKG_DIR / "__main__.py").read_text(encoding="utf-8")
        run_py = (_PKG_DIR / "run_profile.py").read_text(encoding="utf-8")
        corp_py = (_PKG_DIR / "corpus_profile.py").read_text(encoding="utf-8")
        # Must not present package -m form as a working recommended command.
        for blob in (main_py, run_py, corp_py):
            self.assertNotIn("PYTHONPATH=.script python3 -m nhi_rule_history", blob)
            self.assertNotIn("Preferred CLI entry remains", blob)
        self.assertIn("run_profile.py", main_py)
        self.assertIn("not a python package", (main_py + run_py).lower())
        # __main__ must refuse rather than import corpus_profile as a package.
        self.assertIn("SystemExit(2)", main_py)
        self.assertNotIn("from .corpus_profile import main", main_py)
        self.assertNotIn("from corpus_profile import main", main_py)


if __name__ == "__main__":
    unittest.main()
