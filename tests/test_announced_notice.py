from __future__ import annotations

import hashlib
import io
import json
import shutil
import unittest
import zipfile
from datetime import date
from pathlib import Path

from nhi_rule_history.announced_notice import (
    AnnouncedNoticeError,
    NoticeAttachment,
    NoticeBundle,
    ODT_MEDIA_TYPE,
    PATCH_TEXT_JOIN,
    _cell_items,
    cell_rendering_check,
    clause_heading_code,
    is_omission_marker,
    parse_comparison_document,
    parse_effective_statement,
    projection_block_reason,
    read_odt_document,
    roc_date,
    sha256_text,
)
from nhi_rule_history.office_rendering import render_table_cells, uno_python
from nhi_rule_history.pg.common import object_fingerprint
from tools.build_announced_notice_fixture import fixture_odt


def _office_available() -> bool:
    binary = shutil.which("soffice")
    return bool(binary and uno_python(binary))


def _render(payload: bytes) -> dict:
    rendering = render_table_cells({"notice": payload})["notice"]
    assert rendering is not None, "LibreOffice did not render the fixture"
    return rendering


def _drawn(notice, *, text=None, label=None, separator=None) -> dict:
    """A cell rendering that draws every cell as the parser reads it.

    ``text``/``label``/``separator`` map a paragraph's document order to what
    is drawn instead, to model an office suite that disagrees.
    """

    text, label, separator = text or {}, label or {}, separator or {}

    def paragraph(item) -> dict:
        order = item.document_order
        own_label = item.generated_label or ""
        return {
            "text": text.get(order, item.text),
            "label": label.get(order, own_label),
            "separator": separator.get(
                order, item.numbering.separator if own_label else None
            ),
            "hidden": False,
            "bullet": None,
            "transform": False,
        }

    return {
        "rendering_version": "test",
        "tables": [
            [
                [
                    [
                        paragraph(item)
                        for item in _cell_items(
                            notice.document.paragraphs,
                            table_index,
                            cell.row_index,
                            cell.cell_index,
                        )
                        if not item.nested
                    ]
                    for cell in row
                ]
                for row in grid
            ]
            for table_index, grid in sorted(notice.document.top_tables.items())
        ],
    }


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "announced_notices"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
NUMBERING = FIXTURES / "numbering"
NUMBERING_MANIFEST = json.loads(
    (NUMBERING / "manifest.json").read_text(encoding="utf-8")
)

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
)


def _odt(
    content_xml: bytes, styles_xml: bytes | None = None, meta_xml: bytes | None = None
) -> bytes:
    return fixture_odt(content_xml, styles_xml, meta_xml)


def _document(body: str, *, automatic_styles: str = "") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-content {_NS}>"
        f"<office:automatic-styles>{automatic_styles}</office:automatic-styles>"
        f"<office:body><office:text>{body}</office:text></office:body>"
        f"</office:document-content>"
    ).encode("utf-8")


def _cell(*paragraphs: str) -> str:
    return (
        "<table:table-cell>"
        + "".join(f"<text:p>{text}</text:p>" for text in paragraphs)
        + "</table:table-cell>"
    )


def _comparison(
    rows: list[tuple[list[str], list[str]]],
    *,
    header: tuple[str, str] = ("修訂後給付規定", "原給付規定"),
    effective: str = "（自115年10月1日生效）",
) -> bytes:
    body = (
        "<text:p>「藥品給付規定」修訂對照表</text:p>"
        f"<text:p>{effective}</text:p>"
        "<table:table><table:table-header-rows><table:table-row>"
        + _cell(header[0])
        + _cell(header[1])
        + "</table:table-row></table:table-header-rows>"
        + "".join(
            "<table:table-row>" + _cell(*new) + _cell(*old) + "</table:table-row>"
            for new, old in rows
        )
        + "</table:table>"
    )
    return _document(body)


def _bundle(
    attachment: NoticeAttachment,
    *,
    announcement_items: tuple[str, ...] = (),
    raw_md_blocks: dict | None = None,
    title: str = "公告修訂給付規定。",
) -> NoticeBundle:
    return NoticeBundle(
        bundle_dir=FIXTURES,
        source_uid="gov_fixture",
        reference_number="健保審字第0000000000號",
        title=title,
        official_url="https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
        published_on="2026-09-15",
        manifest_sha256="0" * 64,
        attachments=(attachment,),
        announcement_items=announcement_items,
        raw_md_blocks={} if raw_md_blocks is None else raw_md_blocks,
    )


def _receipts(attachment: NoticeAttachment, document) -> dict:
    """raw.md source-block receipts as corpus registration writes them."""

    return {
        attachment.file_name: tuple(
            (item.block_id, item.block_text_sha256) for item in document.paragraphs
        )
    }


def _parse_payload(content_xml: bytes, **bundle_args: object):
    payload = _odt(content_xml)
    sha = hashlib.sha256(payload).hexdigest()
    attachment = NoticeAttachment(
        declared_sequence=0,
        file_name="attachment-000.odt",
        media_type=ODT_MEDIA_TYPE,
        sha256=sha,
        byte_size=len(payload),
        path=FIXTURES / "unused.odt",
    )
    document = read_odt_document(payload)
    if "raw_md_blocks" not in bundle_args:
        bundle_args["raw_md_blocks"] = _receipts(attachment, document)
    return parse_comparison_document(_bundle(attachment, **bundle_args), attachment, document)


class GrammarTest(unittest.TestCase):
    def test_effective_statement_grammar(self) -> None:
        for text in (
            "（自115年10月1日生效）",
            "(自115年10月1日生效)",
            "(自115年10月1日生效）",
            "（自 115 年 10 月 1 日起生效） ",
            # Official files use the CJK compatibility ideograph U+F98E for 年.
            "（自115年10月1日生效）",
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_effective_statement(text), date(2026, 10, 1))
        for text in (
            "自115年10月1日生效",
            "（自115年10月1日生效），並公告",
            "（自115年13月1日生效）",
        ):
            with self.subTest(text=text):
                if "13月" in text:
                    with self.assertRaises(AnnouncedNoticeError):
                        parse_effective_statement(text)
                else:
                    self.assertIsNone(parse_effective_statement(text))
        self.assertEqual(roc_date(115, 9, 1), date(2026, 9, 1))

    def test_clause_heading_requires_a_terminal_stop(self) -> None:
        self.assertEqual(
            clause_heading_code("2.1.4.2.Rivaroxaban(如Xarelto)"), "2.1.4.2"
        )
        self.assertEqual(clause_heading_code("4.2.血液代用製劑"), "4.2")
        self.assertEqual(clause_heading_code("８.１.３.高單位免疫球蛋白"), "8.1.3")
        # A numbered list item is not clause 2.18.
        self.assertIsNone(clause_heading_code("2.18歲以上非瓣膜性心房纖維顫動病患"))
        self.assertIsNone(clause_heading_code("1.限用於"))
        self.assertIsNone(clause_heading_code("(1)~(9)略。"))

    def test_omission_markers_with_confusable_negatives(self) -> None:
        for text in (
            "(1)~(9)略。",
            "(1)~(8)(略)",
            "2.皮下注射劑：(略)",
            "(餘略)",
            "(以下略)",
            "…最多2週：(112/3/1) 略",
            "3.治療深部靜脈血栓(103/5/1、104/12/1)：略",
            "一、~四、（略）",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_omission_marker(text))
        for text in (
            "治療策略。",
            "應省略之檢查不得申報。",
            "侵略性淋巴瘤",
            "限用於",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_omission_marker(text))


class OfficialFixtureTest(unittest.TestCase):
    """Fixtures cut from the four official ODTs effective 2026-10-01."""

    def test_fixture_bytes_are_pinned(self) -> None:
        self.assertEqual(len(MANIFEST["notices"]), 4)
        for notice in MANIFEST["notices"]:
            fixture = notice["fixture"]
            payload = (FIXTURES / fixture["file_name"]).read_bytes()
            with self.subTest(fixture=fixture["file_name"]):
                self.assertEqual(len(payload), fixture["byte_size"])
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(), fixture["sha256"]
                )

    def _parse(self, notice: dict) -> tuple:
        payload = _odt((FIXTURES / notice["fixture"]["file_name"]).read_bytes())
        official = notice["attachment"]
        attachment = NoticeAttachment(
            declared_sequence=official["declared_sequence"],
            file_name=official["file_name"],
            media_type=official["media_type"],
            sha256=official["official_sha256"],
            byte_size=official["official_byte_size"],
            path=FIXTURES / notice["fixture"]["file_name"],
        )
        bundle = NoticeBundle(
            bundle_dir=FIXTURES,
            source_uid=notice["source_uid"],
            reference_number=notice["reference_number"],
            title=notice["title"],
            official_url=notice["official_url"],
            published_on=notice["published_on"],
            manifest_sha256="0" * 64,
            attachments=(attachment,),
            announcement_items=tuple(notice["announcement_items"]),
            raw_md_blocks={
                official["file_name"]: tuple(
                    tuple(item) for item in notice["raw_md_block_receipts"]
                )
            },
        )
        document = read_odt_document(
            payload, artifact_sha256=official["official_sha256"]
        )
        return payload, parse_comparison_document(bundle, attachment, document)

    def test_parsed_clauses_match_pinned_expectations(self) -> None:
        seen = []
        for notice in MANIFEST["notices"]:
            expected = notice["expected"]
            _, parsed = self._parse(notice)
            with self.subTest(reference=notice["reference_number"]):
                self.assertEqual(parsed.effective_on, expected["effective_on"])
                self.assertEqual(
                    len(parsed.document.paragraphs), expected["block_count"]
                )
                self.assertEqual(
                    [t.table_index for t in parsed.tables],
                    [t["table_index"] for t in expected["comparison_tables"]],
                )
                self.assertEqual(
                    [e.key for e in parsed.other_effects],
                    expected["other_effects"],
                )
                self.assertEqual(
                    [c.clause_code for c in parsed.clauses],
                    [c["clause_code"] for c in expected["clauses"]],
                )
                for clause, pinned in zip(parsed.clauses, expected["clauses"]):
                    seen.append(clause.clause_code)
                    self.assertEqual(clause.effective_on, "2026-10-01")
                    self.assertEqual(
                        [list(row) for row in clause.rows], pinned["rows"]
                    )
                    self.assertEqual(
                        sha256_text(clause.patch_text),
                        pinned["revised_text_sha256"],
                    )
                    self.assertEqual(
                        sha256_text(clause.original_text),
                        pinned["original_text_sha256"],
                    )
                    self.assertEqual(
                        object_fingerprint(clause.component_manifest()),
                        pinned["component_manifest_sha256"],
                    )
                    self.assertEqual(
                        bool(clause.omission_orders),
                        pinned["omitted_text_present"],
                    )
                    self.assertIsNone(clause.blocked_reason)
                    # Pinned when cut: LibreOffice rendered the official file
                    # with exactly this revised text.
                    self.assertEqual(pinned["official_rendering_check"], "exact")
        self.assertEqual(seen, ["2.1.4.2", "3.3.28", "8.1.3", "4.2"])

    def test_revised_text_is_the_revised_column_only(self) -> None:
        by_code = {}
        for notice in MANIFEST["notices"]:
            _, parsed = self._parse(notice)
            for clause in parsed.clauses:
                by_code[clause.clause_code] = clause
        rivaroxaban = by_code["2.1.4.2"]
        self.assertTrue(rivaroxaban.patch_text.startswith("2.1.4.2.Rivaroxaban"))
        self.assertIn("115/10/1", rivaroxaban.revised[0].text)
        self.assertNotIn("115/10/1", rivaroxaban.original[0].text)
        self.assertEqual(
            rivaroxaban.patch_text,
            PATCH_TEXT_JOIN.join(item.text for item in rivaroxaban.revised),
        )
        # Whitespace elements are rendered: text:tab in item 1.
        self.assertIn("1.\t靜脈血栓", rivaroxaban.patch_text)
        blood = by_code["4.2"]
        self.assertFalse(blood.omission_orders)
        self.assertTrue(
            blood.patch_text.endswith("在家治療紀錄表(109/8/1)")
        )
        appendix = [
            effect
            for notice in MANIFEST["notices"]
            for effect in self._parse(notice)[1].other_effects
            if effect.key.startswith("appendix:")
        ]
        self.assertEqual([effect.designation for effect in appendix], ["附表十八之五"])

    def test_component_spans_rebuild_the_patch_text(self) -> None:
        for notice in MANIFEST["notices"]:
            _, parsed = self._parse(notice)
            for clause in parsed.clauses:
                text = clause.patch_text
                encoded = text.encode("utf-8")
                rebuilt = []
                previous_end = 0
                for entry, item in zip(clause.component_manifest(), clause.revised):
                    start, end = entry["patch_text_scalar_span"]
                    byte_start, byte_end = entry["patch_text_utf8_span"]
                    with self.subTest(clause=clause.clause_code, block=item.block_id):
                        self.assertEqual(text[start:end], item.text)
                        self.assertEqual(
                            encoded[byte_start:byte_end].decode("utf-8"), item.text
                        )
                        self.assertEqual(
                            sha256_text(text[start:end]),
                            entry["rendered_text_sha256"],
                        )
                    if rebuilt:
                        self.assertEqual(text[previous_end:start], PATCH_TEXT_JOIN)
                    rebuilt.append(text[start:end])
                    previous_end = end
                self.assertEqual(PATCH_TEXT_JOIN.join(rebuilt), text)
                self.assertEqual(previous_end, len(text))

    def test_block_identity_matches_corpus_registration(self) -> None:
        for notice in MANIFEST["notices"]:
            _, parsed = self._parse(notice)
            with self.subTest(reference=notice["reference_number"]):
                self.assertEqual(
                    [
                        [item.block_id, item.block_text_sha256]
                        for item in parsed.document.paragraphs
                    ],
                    notice["raw_md_block_receipts"],
                )

    def test_registered_block_drift_fails_closed(self) -> None:
        notice = MANIFEST["notices"][0]
        broken = json.loads(json.dumps(notice))
        broken["raw_md_block_receipts"][5][1] = "0" * 64
        with self.assertRaisesRegex(AnnouncedNoticeError, "raw.md"):
            self._parse(broken)

    @unittest.skipUnless(_office_available(), "LibreOffice/UNO is unavailable")
    def test_independent_office_rendering(self) -> None:
        for notice in MANIFEST["notices"]:
            payload, parsed = self._parse(notice)
            rendering = _render(payload)
            for clause in parsed.clauses:
                with self.subTest(clause=clause.clause_code):
                    self.assertEqual(
                        cell_rendering_check(parsed, clause, rendering), "exact"
                    )
            # Confusable negative: one changed character must not match.
            clause = parsed.clauses[0]
            last = clause.revised[-1]
            tampered = _drawn(
                parsed, text={last.document_order: last.text[:-1] + "X"}
            )
            self.assertEqual(
                cell_rendering_check(parsed, clause, _drawn(parsed)), "exact"
            )
            self.assertEqual(
                cell_rendering_check(parsed, clause, tampered), "mismatch"
            )


class ComparisonGrammarTest(unittest.TestCase):
    def test_new_clause_and_multiple_clauses_in_one_cell(self) -> None:
        payload = _comparison(
            [
                (["9.140.Axicabtagene(如Yescarta)：(115/10/1)", "1.限用於"], ["無"]),
                (
                    ["9.5.Paclitaxel成分劑：(115/10/1)", "9.5.1.Paclitaxel注射劑：", "~2.(略)"],
                    ["9.5.Paclitaxel成分劑：", "9.5.1.Paclitaxel注射劑：", "~2.(略)"],
                ),
            ]
        )
        parsed = _parse_payload(payload)
        self.assertEqual([c.clause_code for c in parsed.clauses], ["9.140", "9.5", "9.5.1"])
        self.assertTrue(parsed.clauses[0].original_is_none)
        self.assertEqual(parsed.clauses[0].original, ())
        self.assertEqual(len(parsed.clauses[1].revised), 1)
        self.assertTrue(parsed.clauses[2].omission_orders)

    def test_designation_mismatch_fails_closed(self) -> None:
        payload = _comparison(
            [(["9.27.Drug：(115/10/1)"], ["9.26.Drug："])]
        )
        with self.assertRaisesRegex(AnnouncedNoticeError, "designations differ"):
            _parse_payload(payload)

    def test_missing_or_unknown_effective_statement_fails_closed(self) -> None:
        with self.assertRaisesRegex(AnnouncedNoticeError, "no effective date"):
            _parse_payload(
                _comparison([(["2.1.1.X："], ["2.1.1.X："])], effective="備註")
            )
        with self.assertRaisesRegex(AnnouncedNoticeError, "known grammar"):
            _parse_payload(
                _comparison(
                    [(["2.1.1.X："], ["2.1.1.X："])],
                    effective="自公告日起生效",
                )
            )

    def test_unknown_header_is_not_guessed(self) -> None:
        payload = _comparison(
            [(["2.1.1.X："], ["2.1.1.X："])], header=("修訂後給付規定", "說明")
        )
        with self.assertRaisesRegex(AnnouncedNoticeError, "header grammar"):
            _parse_payload(payload)

    def test_continuation_row_joins_the_previous_clause(self) -> None:
        payload = _comparison(
            [
                (["2.6.1.給付規定表：(115/9/1)", "表一"], ["2.6.1.給付規定表：", "表一"]),
                (["表二", "(以下略)"], ["表二", "(以下略)"]),
            ]
        )
        parsed = _parse_payload(payload)
        self.assertEqual(len(parsed.clauses), 1)
        self.assertTrue(parsed.clauses[0].continued)
        self.assertEqual(len(parsed.clauses[0].revised), 4)

    def test_leading_row_without_designation_fails_closed(self) -> None:
        payload = _comparison([(["表二"], ["表二"])])
        with self.assertRaisesRegex(AnnouncedNoticeError, "clause designation"):
            _parse_payload(payload)

    def _listed_clause(self, style: str, levels: str):
        return self._listed_notice(style, levels).clauses[0]

    def _listed_notice(self, style: str, levels: str):
        body = (
            "<text:p>（自115年10月1日生效）</text:p><table:table>"
            "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
            + "</table:table-row><table:table-row>"
            "<table:table-cell><text:p>9.139.Mogamulizumab：(115/10/1)</text:p>"
            f'<text:list text:style-name="{style}"><text:list-item>'
            "<text:p>單獨用於</text:p><text:p>續行段落</text:p>"
            "</text:list-item></text:list></table:table-cell>"
            + _cell("無") + "</table:table-row></table:table>"
        )
        return _parse_payload(_document(body, automatic_styles=levels))

    def test_list_labels_are_printed_before_their_text(self) -> None:
        alignment = (
            '<style:list-level-properties text:list-level-position-and-space-'
            'mode="label-alignment"><style:list-level-label-alignment '
            'text:label-followed-by="listtab"/></style:list-level-properties>'
        )
        clause = self._listed_clause(
            "L1",
            '<text:list-style style:name="L1">'
            '<text:list-level-style-number text:level="1" style:num-suffix="." '
            f'style:num-format="1">{alignment}</text:list-level-style-number>'
            "</text:list-style>",
        )
        self.assertEqual(
            [item.generated_label for item in clause.revised], [None, "1.", None]
        )
        self.assertEqual(
            [item.printed_text for item in clause.revised],
            ["9.139.Mogamulizumab：(115/10/1)", "1.\t單獨用於", "續行段落"],
        )
        self.assertEqual(
            [item.export_prefix for item in clause.revised],
            ["", "    1. ", "       "],
        )
        self.assertIsNone(clause.blocked_reason)
        self.assertTrue(clause.requires_rendering_check)
        self.assertIn("\n\n1.\t單獨用於\n\n續行段落", clause.patch_text)
        manifest = clause.component_manifest()
        self.assertIsNone(manifest[0]["generated_label"])
        self.assertEqual(manifest[1]["generated_label"]["label"], "1.")
        self.assertEqual(manifest[1]["generated_label"]["separator"], "\t")
        self.assertEqual(manifest[1]["generated_label"]["prefix_scalar_length"], 3)
        # The block identity stays the registered character data.
        self.assertEqual(
            manifest[1]["raw_text_sha256"], sha256_text("單獨用於")
        )
        start, end = manifest[1]["patch_text_scalar_span"]
        self.assertEqual(clause.patch_text[start:end], "1.\t單獨用於")
        # An unnumbered level draws nothing and is still a list paragraph.
        plain = self._listed_clause(
            "L2",
            '<text:list-style style:name="L2">'
            '<text:list-level-style-number text:level="1" style:num-format="">'
            f"{alignment}</text:list-level-style-number></text:list-style>",
        )
        self.assertEqual(
            [item.generated_label for item in plain.revised], [None, None, None]
        )
        self.assertEqual(plain.revised[1].export_prefix, "       ")
        self.assertIsNone(plain.blocked_reason)
        # Confusable negative: a level without label-alignment properties is
        # in label-width mode, whose printed gap is not a character.
        blocked = self._listed_clause(
            "L3",
            '<text:list-style style:name="L3">'
            '<text:list-level-style-number text:level="1" style:num-suffix="." '
            'style:num-format="1"/></text:list-style>',
        )
        self.assertEqual(blocked.blocked_reason, "unsupported_list_numbering")
        self.assertEqual(
            blocked.numbering_blocked_reasons, ("label_width_and_position_mode",)
        )

    def test_rendering_check_compares_reconstructed_labels(self) -> None:
        alignment = (
            '<style:list-level-properties text:list-level-position-and-space-'
            'mode="label-alignment"/>'
        )
        notice = self._listed_notice(
            "L1",
            '<text:list-style style:name="L1">'
            '<text:list-level-style-number text:level="1" style:num-prefix="(" '
            'style:num-suffix=")" style:num-format="1" text:start-value="5">'
            f"{alignment}</text:list-level-style-number></text:list-style>",
        )
        clause = notice.clauses[0]
        self.assertEqual(clause.revised[1].printed_text, "(5)\t單獨用於")
        labelled, continued = (item.document_order for item in clause.revised[1:])
        self.assertEqual(cell_rendering_check(notice, clause, _drawn(notice)), "exact")
        for name, tampered in (
            ("label", _drawn(notice, label={labelled: "(6)"})),
            ("no label", _drawn(notice, label={labelled: ""})),
            ("space", _drawn(notice, separator={labelled: " "})),
            ("nothing", _drawn(notice, separator={labelled: ""})),
            ("line break", _drawn(notice, separator={labelled: "\n"})),
            ("continuation label", _drawn(notice, label={continued: "(6)"})),
            ("text", _drawn(notice, text={continued: "續行"})),
        ):
            with self.subTest(tampered=name):
                self.assertEqual(
                    cell_rendering_check(notice, clause, tampered), "mismatch"
                )
        self.assertIsNone(projection_block_reason(clause, "exact"))
        for check, reason in (
            ("mismatch", "official_rendering_mismatch"),
            ("unavailable", "official_rendering_unverified"),
            ("not_applicable_nested_table", "official_rendering_unverified"),
        ):
            with self.subTest(check=check):
                self.assertEqual(projection_block_reason(clause, check), reason)

    def test_nested_table_is_never_projected(self) -> None:
        # 2026-09-28 finding H2: a nested table was flattened into paragraph
        # text and exempt from the rendering check.
        def row(nested: bool) -> bytes:
            table = (
                "<table:table><table:table-row>" + _cell("體重") + _cell("劑量")
                + "</table:table-row><table:table-row>" + _cell("&lt;50kg")
                + _cell("1mg/kg") + "</table:table-row></table:table>"
            )
            body = (
                "<text:p>（自115年10月1日生效）</text:p><table:table>"
                "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
                + "</table:table-row><table:table-row><table:table-cell>"
                "<text:p>9.56.Foo(115/10/1)</text:p><text:p>劑量表如下：</text:p>"
                + (table if nested else "")
                + "<text:p>其餘不變。</text:p></table:table-cell>"
                + _cell("9.56.Foo")
                + "</table:table-row></table:table>"
            )
            return _document(body)

        notice = _parse_payload(row(nested=True))
        clause = notice.clauses[0]
        self.assertTrue(any(item.nested for item in clause.revised))
        self.assertEqual(clause.blocked_reason, "nested_table")
        self.assertEqual(
            cell_rendering_check(notice, clause, _drawn(notice)),
            "not_applicable_nested_table",
        )
        for check in ("exact", "not_applicable_nested_table", "unavailable"):
            self.assertEqual(projection_block_reason(clause, check), "nested_table")
        # Confusable negative: the same clause without the nested table.
        plain = _parse_payload(row(nested=False)).clauses[0]
        self.assertIsNone(plain.blocked_reason)
        self.assertIsNone(projection_block_reason(plain, "unavailable"))

    def test_continuation_needs_positive_evidence(self) -> None:
        # 2026-09-28 finding H1: any row without a strict heading continued
        # the clause above.  Each probe must now fail closed.
        first = (["9.139.Mogamulizumab：(115/10/1)", "單獨用於"], ["無"])
        for revised, original in (
            (["9.140 Bar(115/10/1)"], ["無"]),
            (["9.57:Bar"], ["9.57:Bar"]),
            (["◎9.141.Baz"], ["無"]),
            (["0.5.藥品給付通則"], ["0.5.藥品給付通則"]),
            (["五、藥品給付通則"], ["五、藥品給付通則"]),
            (["表二", "2.18歲以上"], ["表二"]),
            (["表二"], ["無"]),
        ):
            with self.subTest(row=revised[0]), self.assertRaisesRegex(
                AnnouncedNoticeError, "does not start with a clause designation"
            ):
                _parse_payload(_comparison([first, (revised, original)]))
        # A continuation must follow its clause directly, in the same table.
        body = (
            "<text:p>（自115年9月1日生效）</text:p>"
            + "".join(
                "<table:table><table:table-row>" + _cell("修訂後給付規定")
                + _cell("原給付規定") + "</table:table-row><table:table-row>"
                + _cell(*new) + _cell(*old) + "</table:table-row></table:table>"
                for new, old in (
                    (["2.6.1.給付規定表：(115/9/1)", "表一"], ["2.6.1.給付規定表：", "表一"]),
                    (["表二"], ["表二"]),
                )
            )
        )
        with self.assertRaisesRegex(AnnouncedNoticeError, "previous row"):
            _parse_payload(_document(body))

    def test_designation_inside_a_clause_cell_fails_closed(self) -> None:
        # 2026-09-28 findings R2-H3 and HIGH-B: a designation-like paragraph
        # inside a clause's cell was merged into the clause (the verifier's
        # 9.139/9.140 probe), and the first fix looked only at the paragraph
        # start.  Any position counts: after a line break, a tab, a run of
        # spaces, a sentence or a word.
        head = ["9.139.Foo：(115/10/1)", "限用於A"]
        for paragraph in (
            "9.140 Bar(115/10/1)",
            "◎9.141.Baz(115/10/1)",
            "9.57:Bar(115/10/1)",
            "0.5.藥品給付通則：",
            "９．１４０ Bar",
            "　9.140 Bar",
            "9.140：Bar",
            "新增9.140 Bar",
            "9·140 Bar",
            "9-140.Bar",
            "A9.140 Bar",
            "9.140Bar(115/10/1)",
            "9.140、Bar",
            "9.140(Bar)",
            "9.140，Bar",
            "限用於A。9.140 Bar",
            "9.140",
            "限用於A<text:line-break/>9.140 Bar(115/10/1)",
            "限用於A<text:tab/>9.140.Bar(115/10/1)",
            '限用於A<text:s text:c="3"/>9.140.Bar(115/10/1)',
            # Finding B5: a CJK name, and the forms around a Latin name the
            # first version let through.
            "9.140 抗癌藥物",
            "9.140　高單位免疫球蛋白",
            "9.140、高單位免疫球蛋白",
            "9.140（高單位免疫球蛋白）",
            "9.140高單位免疫球蛋白",
            "9.140 - Bar",
            "9.140—Bar",
            "9.140「Bar」",
            "9.140‧Bar",
            "9-140 Bar",
            "限用於A<text:line-break/>9.140 高單位",
            # Finding B5 (round 5): after a break a number is a designation
            # whatever follows it unless a whole unit word does, so names that
            # start with a unit-like character, mid-line designations, a
            # letter before a two-stop number and a word that is no code system
            # all count.
            "限用於A，9.140 抗癌藥物",
            "限用於A 9.140 抗癌藥物",
            "限用於A、9.141.高單位免疫球蛋白",
            "9.140 分子標靶藥物",
            "9.140 支氣管擴張劑",
            "9.140 百日咳疫苗",
            "9.140 日本腦炎疫苗",
            "9.140 點眼劑",
            "9.140 G-CSF",
            "9.140 L-asparaginase",
            "9.140 H2-blocker",
            "9.140 U-500 insulin",
            "A10.3.9 Bar",
            "Add 9.141 Baz",
            "限用於A—9.140 抗癌藥物",
            "限用於A.9.140 Bar",
            "9.140 L型鈣離子阻斷劑",
            # The cost: a sub-item numbered like a designation and followed by
            # a word that is not a unit is refused too (the notice is held,
            # never merged).
            "1.1 限用於成人病患",
            "1.0 版",
            "2.5 倍數",
        ):
            with self.subTest(paragraph=paragraph), self.assertRaisesRegex(
                AnnouncedNoticeError, "inside clause 9.139 reads like a designation"
            ):
                _parse_payload(_comparison([(head + [paragraph, "限用於B"], ["無"])]))
        # The heading paragraph is read after its own code, and the original
        # column is read the same way.
        with self.assertRaisesRegex(
            AnnouncedNoticeError, r"inside clause 9\.139 reads like a designation \(9\.140\)"
        ):
            _parse_payload(
                _comparison(
                    [(["9.139.Foo：(115/10/1)<text:line-break/>9.140 Bar", "限用於B"],
                      ["無"])]
                )
            )
        with self.assertRaisesRegex(
            AnnouncedNoticeError, r"inside clause 9\.2 reads like a designation \(9\.140\)"
        ):
            _parse_payload(
                _comparison(
                    [(["9.2.Foo：(115/10/1)", "限用於A"], ["9.2.Foo：", "9.140 Bar"])]
                )
            )
        # A row that would continue the clause above is read the same way.
        with self.assertRaisesRegex(
            AnnouncedNoticeError, "does not start with a clause designation"
        ):
            _parse_payload(
                _comparison(
                    [(head, ["9.139.Foo：", "限用於"]),
                     (["限用於A。9.140 Bar"], ["限用於"])]
                )
            )
        # Confusable negatives: quantities, list items run into their word,
        # single-level items, omission markers and cross-references stay text.
        revised = [
            "2.1.4.2.Rivaroxaban：(115/10/1)",
            "2.18歲以上非瓣膜性心房纖維顫動病患",
            "0.5 mg/kg以下",
            "1.5倍以上",
            "2.5 mg",
            "1.5 mg",
            "2.5mg每日一次",
            "每日2.5 mg",
            "Hb≧8.0 g/dL; BMD之-2.5 SD",
            "eGFR≦30 mL/min/1.73 m2",
            "bilirubin ≤ 3.0 x ULN",
            "1.限用於",
            "~2.(略)",
            "(詳見9.24.1)",
            "依2.6.1規定辦理",
            # Cross-references, units, versions, codes of other systems and
            # dates that the first version refused (finding B5, low).
            "依9.69.之規定辦理",
            "詳見2.1.4.2.規定",
            "TSH 4.5 mU/L以上",
            "每次4.5 MIU",
            "2.5 cc",
            "1.5 times ULN",
            "QTc延長0.5 sec",
            "肝硬度12.5 kPa以上",
            "放射劑量1.8 Gy",
            "Child-Pugh score 6.5 points",
            "RECIST version 1.1 標準",
            "AJCC 8.0 staging",
            "自115.10.1起",
            "Serum albumin在3.5 gm/dl以上",
            "限用於第2型糖尿病(ICD-10-CM：E11.9、E11.65)",
            "ICD-10-CM: C50.911, C50.912",
            "1.5倍以上",
            "2.5以上",
            # Quantities from the official comparison tables in the corpus
            # (round 6), after a sign, an attached minus, a Latin word or a
            # break, with a whole unit word or a range after them (＜ is
            # read as < after NFKC).
            "Rivaroxaban 2.5mg限與aspirin併用",
            "DXA檢測BMD之-2.5SD ＜T score ＜-1.0SD)",
            "Atropine sulfate  0.1 mg/mL- 0.5 mg/mL眼用製劑",
            "規格量≧ 3.5 mL項目",
            "(platelet count) ＜0.5x109/L。",
            "HER2/CEP17比值 2.0 以上",
            "0.5 公絲以下",
            "TLS 1.2(或以上等級)加密",
            "pH:7.30 - 7.45",
        ]
        parsed = _parse_payload(
            _comparison([(revised, ["2.1.4.2.Rivaroxaban：", revised[1]])])
        )
        self.assertEqual([c.clause_code for c in parsed.clauses], ["2.1.4.2"])
        self.assertEqual(
            [item.text for item in parsed.clauses[0].revised], revised
        )

    def test_designation_drawn_by_a_list_label_fails_closed(self) -> None:
        # 2026-09-28 finding B1: the label "9.141" of a list item inside
        # clause 9.140's cell was not read, so its text was merged into 9.140.
        def clause_cell(label_level: str, text: str) -> str:
            styles = (
                '<text:list-style style:name="GL">' + label_level
                + "</text:list-style>"
            )
            body = (
                "<text:p>（自115年10月1日生效）</text:p><table:table>"
                "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
                + "</table:table-row><table:table-row><table:table-cell>"
                "<text:p>9.140.Foo：(115/10/1)</text:p><text:p>說明甲</text:p>"
                '<text:list text:style-name="GL"><text:list-item>'
                f"<text:p>{text}</text:p></text:list-item></text:list>"
                "</table:table-cell>" + _cell("無")
                + "</table:table-row></table:table>"
            )
            return _document(body, automatic_styles=styles)

        def level(prefix: str, suffix: str, start: int, followed: str = "listtab") -> str:
            return (
                '<text:list-level-style-number text:level="1" style:num-format="1" '
                f'style:num-prefix="{prefix}" style:num-suffix="{suffix}" '
                f'text:start-value="{start}"><style:list-level-properties '
                'text:list-level-position-and-space-mode="label-alignment">'
                f'<style:list-level-label-alignment text:label-followed-by="{followed}"/>'
                "</style:list-level-properties></text:list-level-style-number>"
            )

        for name, content in (
            ("label 9.141 and a tab", clause_cell(level("9.", "", 141), "Bar：新增給付")),
            ("label 9.141:", clause_cell(level("9.", ":", 141), "Bar：新增給付")),
            ("label 9.141 and a space", clause_cell(level("9.", "", 141, "space"), "Bar：新增給付")),
            ("label 9.141 and a CJK name", clause_cell(level("9.", "", 141), "新藥甲（如Bar）：新增給付")),
            # Finding B5 (round 5): names that start like a unit.
            ("label 9.141 and 分子", clause_cell(level("9.", "", 141), "分子標靶藥物：新增給付")),
            ("label 9.141 and 克流感", clause_cell(level("9.", "", 141), "克流感膠囊：新增給付")),
            ("label 9.141 and G-CSF", clause_cell(level("9.", "", 141), "G-CSF（filgrastim）：新增給付")),
            ("label 9.141 and H2", clause_cell(level("9.", "", 141, "space"), "H2 receptor antagonists")),
        ):
            with self.subTest(label=name), self.assertRaisesRegex(
                AnnouncedNoticeError, r"inside clause 9\.140 reads like a designation \(9\.141\)"
            ):
                _parse_payload(content)
        # Confusable negatives: ordinary labels before the same text.
        for prefix, suffix in (("(", ")"), ("", ".")):
            parsed = _parse_payload(clause_cell(level(prefix, suffix, 5), "Bar：新增給付"))
            self.assertEqual([c.clause_code for c in parsed.clauses], ["9.140"])

    def test_missing_registration_receipts_fail_closed(self) -> None:
        # 2026-09-28 finding L5: without raw.md receipts the block identity
        # check was skipped.
        payload = _comparison([(["2.1.1.X：(115/10/1)"], ["2.1.1.X："])])
        self.assertEqual(len(_parse_payload(payload).clauses), 1)
        for receipts in ({}, {"attachment-001.odt": (("block", "0" * 64),)}):
            with self.subTest(receipts=receipts), self.assertRaisesRegex(
                AnnouncedNoticeError, "no source-block receipts"
            ):
                _parse_payload(payload, raw_md_blocks=receipts)

    def test_proposal_is_not_announced_text(self) -> None:
        # 2026-09-28 finding L7.  NHI announcements may keep the drafting
        # label 建議修訂後 on the revised column (1150671962 does, for 2.6.1);
        # only a notice that is not an announcement makes it a proposal.
        rows = [(["2.1.1.X：(115/10/1)"], ["2.1.1.X："])]
        proposal = ("建議修訂後給付規定", "原給付規定")
        for header in (proposal, ("修訂後給付規定", "原給付規定"),
                       ("修正後給付規定", "原給付規定")):
            with self.subTest(header=header[0]):
                parsed = _parse_payload(
                    _comparison(rows, header=header), title="公告修訂給付規定。"
                )
                self.assertEqual(parsed.tables[0].revised_header, header[0])
        for title, header, message in (
            ("修正給付規定。", proposal, "proposal"),
            ("預告修正給付規定草案。", proposal, "pre-announcement"),
            ("預告修正給付規定草案。", ("修訂後給付規定", "原給付規定"), "pre-announcement"),
        ):
            with self.subTest(title=title, header=header[0]), self.assertRaisesRegex(
                AnnouncedNoticeError, message
            ):
                _parse_payload(_comparison(rows, header=header), title=title)

    def test_prior_pre_announcement_phrase_is_not_a_pre_announcement(self) -> None:
        # 2026-09-28 finding R2-L5: any title containing 預告 was refused,
        # including an announcement that says it was pre-announced before.
        rows = [(["2.1.1.X：(115/10/1)"], ["2.1.1.X："])]
        proposal = ("建議修訂後給付規定", "原給付規定")
        for title in (
            "公告修正「藥品給付規定」部分規定（前經預告）",
            "公告修正「藥品給付規定」部分規定(業經本署於115年8月1日預告)",
        ):
            with self.subTest(title=title):
                parsed = _parse_payload(_comparison(rows, header=proposal), title=title)
                self.assertEqual([c.clause_code for c in parsed.clauses], ["2.1.1"])
        for title in (
            "預告修正「藥品給付規定」部分規定草案",
            "公告修正「藥品給付規定」部分規定（前經預告），並預告修正附表",
            "有關預告修正「藥品給付規定」一案",
        ):
            with self.subTest(title=title), self.assertRaisesRegex(
                AnnouncedNoticeError, "pre-announcement"
            ):
                _parse_payload(_comparison(rows), title=title)

    def test_hidden_or_conditional_structure_fails_closed(self) -> None:
        # Finding B2: a hidden section, a hidden-paragraph or hidden-text field
        # and conditional text decide by condition what is drawn.
        head = "9.139.Foo：(115/10/1)"
        for cell in (
            '<text:section text:name="S1" text:display="none">'
            "<text:p>隱藏段落</text:p></text:section><text:p>單獨用於</text:p>",
            '<text:section text:name="S2" text:condition="ooow:1">'
            "<text:p>條件段落</text:p></text:section><text:p>單獨用於</text:p>",
            '<text:p>單獨用於<text:hidden-paragraph text:condition="ooow:1" '
            'text:is-hidden="true"/></text:p>',
            '<text:p>單獨用於<text:hidden-text text:condition="ooow:1" '
            'text:string-value="（隱藏）" text:is-hidden="true">（隱藏）'
            "</text:hidden-text></text:p>",
            '<text:p>單獨用於<text:conditional-text text:condition="ooow:1" '
            'text:string-value-if-true="甲" text:string-value-if-false="乙">甲'
            "</text:conditional-text></text:p>",
        ):
            body = (
                "<text:p>（自115年10月1日生效）</text:p><table:table>"
                "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
                + f"</table:table-row><table:table-row><table:table-cell><text:p>{head}"
                "</text:p>" + cell + "</table:table-cell>" + _cell("無")
                + "</table:table-row></table:table>"
            )
            with self.subTest(cell=cell[:40]), self.assertRaisesRegex(
                AnnouncedNoticeError, "hidden or conditional content"
            ):
                _parse_payload(_document(body))
        # Confusable negative: a visible section parses.
        body = (
            "<text:p>（自115年10月1日生效）</text:p><table:table>"
            "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
            + f"</table:table-row><table:table-row><table:table-cell><text:p>{head}"
            '</text:p><text:section text:name="S3"><text:p>單獨用於</text:p>'
            "</text:section></table:table-cell>" + _cell("無")
            + "</table:table-row></table:table>"
        )
        self.assertEqual(
            [c.clause_code for c in _parse_payload(_document(body)).clauses], ["9.139"]
        )

    def test_rendering_without_presentation_cannot_confirm(self) -> None:
        notice = _parse_payload(
            _comparison([(["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌。"],
                          ["9.2.Carboplatin：", "限用於卵巢癌第一線。"])])
        )
        clause = notice.clauses[0]
        self.assertEqual(cell_rendering_check(notice, clause, _drawn(notice)), "exact")
        for key, value in (("hidden", True), ("bullet", "●"), ("transform", True)):
            drawn = _drawn(notice)
            drawn["tables"][0][1][0][1][key] = value
            with self.subTest(key=key):
                self.assertEqual(cell_rendering_check(notice, clause, drawn), "mismatch")
        # A rendering that does not report presentation (an older renderer)
        # cannot confirm the cell.
        bare = _drawn(notice)
        for paragraph in bare["tables"][0][1][0]:
            del paragraph["transform"]
        self.assertEqual(cell_rendering_check(notice, clause, bare), "mismatch")

    def test_rendering_check_reads_the_clause_cell_only(self) -> None:
        # 2026-09-28 finding R2-H1: the check searched the whole document's
        # text export, so revised text shown only in another cell (typically
        # the unchanged original column) passed as exact.
        notice = _parse_payload(
            _comparison(
                [(["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌。"],
                  ["9.2.Carboplatin：", "限用於卵巢癌第一線。"])]
            )
        )
        clause = notice.clauses[0]
        revised = clause.revised[1].document_order
        self.assertEqual(cell_rendering_check(notice, clause, _drawn(notice)), "exact")
        # Every revised paragraph stands in the document, but in the other
        # column of the row.
        swapped = _drawn(notice)
        swapped["tables"][0][1].reverse()
        self.assertEqual(cell_rendering_check(notice, clause, swapped), "mismatch")
        self.assertEqual(
            cell_rendering_check(
                notice, clause, _drawn(notice, text={revised: "限用於肺癌。"})
            ),
            "mismatch",
        )
        extra = _drawn(notice)
        extra["tables"][0][1][0].append(
            {"text": "", "label": "2.", "separator": "\t", "hidden": False,
             "bullet": None, "transform": False}
        )
        self.assertEqual(cell_rendering_check(notice, clause, extra), "mismatch")
        # Confusable negative: a blank unlabelled paragraph is not drawn text.
        blank = _drawn(notice)
        blank["tables"][0][1][0].append(
            {"text": " ", "label": "", "separator": None, "hidden": False,
             "bullet": None, "transform": False}
        )
        self.assertEqual(cell_rendering_check(notice, clause, blank), "exact")
        self.assertEqual(
            cell_rendering_check(notice, clause, {"tables": []}), "mismatch"
        )
        self.assertEqual(cell_rendering_check(notice, clause, None), "unavailable")


def _probe_notice(parts: tuple[bytes, bytes, bytes]):
    payload = fixture_odt(*parts)
    sha = hashlib.sha256(payload).hexdigest()
    attachment = NoticeAttachment(
        declared_sequence=0,
        file_name="attachment-000.odt",
        media_type=ODT_MEDIA_TYPE,
        sha256=sha,
        byte_size=len(payload),
        path=FIXTURES / "unused.odt",
    )
    document = read_odt_document(payload)
    bundle = _bundle(attachment, raw_md_blocks=_receipts(attachment, document))
    return payload, parse_comparison_document(bundle, attachment, document)


def _probe_table(revised: str, original: str) -> str:
    def cell(content: str) -> str:
        return f"<table:table-cell>{content}</table:table-cell>"

    return (
        "<text:p>「藥品給付規定」修訂對照表</text:p>"
        "<text:p>（自115年10月1日生效）</text:p>"
        '<table:table table:name="CMP"><table:table-column '
        'table:number-columns-repeated="2"/><table:table-row>'
        + cell("<text:p>修訂後給付規定</text:p>")
        + cell("<text:p>原給付規定</text:p>")
        + "</table:table-row><table:table-row>"
        + cell(revised)
        + cell(original)
        + "</table:table-row></table:table>"
    )


@unittest.skipUnless(_office_available(), "LibreOffice/UNO is unavailable")
class CellRenderingGateTest(unittest.TestCase):
    """2026-09-28 finding R2-H1, end to end against LibreOffice itself.

    Each probe is the verifier's: a revised cell whose reconstructed label or
    separator is not what LibreOffice draws.  The whole-document text export
    passed the first two as exact, because the same paragraph with that label
    stands in the original column, and it cannot see a separator at all.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from tests import test_odf_list_numbering as odf

        numbered = odf.list_style("A1", odf.level(1, "1", None, "."))
        other = odf.list_style("B2", odf.level(1, "1", None, "."))
        continued = 'text:continue-numbering="true"'
        word = odf.list_style(
            "LFO1", *(odf.level(n, "1", None, ".") for n in range(1, 10))
        ) + odf.list_style(
            "LFO2",
            *(
                odf.level(n, "1", None, ".", start=3 if n == 1 else None)
                for n in range(1, 10)
            ),
        )
        newline = (
            '<text:list-style style:name="NL"><text:list-level-style-number '
            'text:level="1" style:num-format="1" style:num-suffix=".">'
            "<style:list-level-properties text:list-level-position-and-space-"
            'mode="label-alignment"><style:list-level-label-alignment '
            'text:label-followed-by="nothing" loext:label-followed-by="newline"/>'
            "</style:list-level-properties></text:list-level-style-number>"
            "</text:list-style>"
        )
        heading = odf.para("9.139.Foo：")
        cls.probes = {
            # A level the style does not define: LibreOffice counts a phantom.
            "phantom_level": odf.document(
                odf.lst(
                    "A1",
                    odf.item(odf.lst(None, odf.item(odf.para("前言段落")))),
                    attributes='xml:id="L1"',
                )
                + _probe_table(
                    heading
                    + odf.lst(
                        "A1",
                        odf.item(odf.para("單獨用於")),
                        attributes='text:continue-list="L1"',
                    ),
                    heading + odf.lst("B2", odf.item(odf.para("單獨用於"))),
                ),
                automatic=numbered + other,
                generator=odf.OTHER,
            ),
            # Word's merged form cell whose covered part holds a list item.
            "covered_cell": odf.document(
                '<table:table table:name="FORM"><table:table-column '
                'table:number-columns-repeated="2"/><table:table-row>'
                '<table:table-cell table:number-rows-spanned="2">'
                + odf.lst("LFO1", odf.item(odf.para("檢附資料甲")), attributes=continued)
                + "</table:table-cell><table:table-cell>" + odf.para("說明一")
                + "</table:table-cell></table:table-row><table:table-row>"
                "<table:covered-table-cell>"
                + odf.lst("LFO1", odf.item(odf.para("")), attributes=continued)
                + "</table:covered-table-cell><table:table-cell>"
                + odf.para("說明二")
                + "</table:table-cell></table:table-row></table:table>"
                + _probe_table(
                    heading
                    + odf.lst("LFO1", odf.item(odf.para("單獨用於")), attributes=continued),
                    heading
                    + odf.lst("LFO2", odf.item(odf.para("單獨用於")), attributes=continued),
                ),
                automatic=word,
                generator=odf.MSO,
            ),
            # LibreOffice's own extension: the label is followed by a line break.
            "newline_separator": odf.document(
                _probe_table(
                    heading + odf.lst("NL", odf.item(odf.para("單獨用於"))),
                    heading + odf.para("無"),
                ),
                automatic=newline,
                generator=odf.OTHER,
            ),
            # Positive control: whitespace elements and a label, all as drawn.
            "whitespace_positive": odf.document(
                _probe_table(
                    heading
                    + odf.lst(
                        "A1",
                        odf.item(odf.para("限<text:s text:c=\"2\"/>用於<text:tab/>成人")),
                        odf.item(odf.para("每日<text:line-break/>一次")),
                    ),
                    heading + odf.para("限用於成人"),
                ),
                automatic=numbered,
                generator=odf.OTHER,
            ),
        }
        # 2026-09-28 findings B2-B4: presentation the paragraph string does
        # not show.  Each draws differently from its string in LibreOffice.
        hidden_text = (
            '<style:style style:name="HID" style:family="text">'
            '<style:text-properties text:display="none"/></style:style>'
        )
        hidden_paragraph = (
            '<style:style style:name="HP" style:family="paragraph">'
            '<style:text-properties text:display="none"/></style:style>'
        )
        upper = (
            '<style:style style:name="UC" style:family="text">'
            '<style:text-properties fo:text-transform="uppercase"/></style:style>'
        )
        small_caps = (
            '<style:style style:name="SC" style:family="text">'
            '<style:text-properties fo:font-variant="small-caps"/></style:style>'
        )
        bullet = (
            '<text:list-level-style-bullet text:level="1" text:bullet-char="●">'
            "<style:list-level-properties text:list-level-position-and-space-"
            'mode="label-alignment"><style:list-level-label-alignment '
            'text:label-followed-by="listtab"/></style:list-level-properties>'
            "</text:list-level-style-bullet>"
        )

        def presented(cell: str, automatic: str = "", common: str = "") -> tuple:
            return odf.document(
                _probe_table(heading + cell, heading + odf.para("無")),
                automatic=automatic,
                common=common,
                generator=odf.OTHER,
            )

        cls.probes.update(
            {
                "hidden_span": presented(
                    odf.para('單獨用於<text:span text:style-name="HID">（隱藏）</text:span>'),
                    automatic=hidden_text,
                ),
                "hidden_paragraph": presented(
                    odf.para("單獨用於", "HP") + odf.para("其次"),
                    automatic=hidden_paragraph,
                ),
                "hidden_label": presented(
                    odf.lst("HL", odf.item(odf.para("單獨用於"))),
                    automatic=odf.list_style(
                        "HL", odf.level(1, "1", None, ".", extra='text:style-name="HID"')
                    ),
                    common=hidden_text,
                ),
                "uppercase_label": presented(
                    odf.lst("UL", odf.item(odf.para("單獨用於"))),
                    automatic=odf.list_style(
                        "UL", odf.level(1, "i", None, ".", extra='text:style-name="UC"')
                    ),
                    common=upper,
                ),
                "small_caps_text": presented(
                    odf.para('限用於<text:span text:style-name="SC">abc</text:span>'),
                    automatic=small_caps,
                ),
                "bullet_instead_of_none": presented(
                    odf.lst("DNB", odf.item(odf.para("甲一")), odf.item(odf.para("乙二"))),
                    automatic='<text:list-style style:name="DNB">'
                    + odf.level(1, "", None, None)
                    + bullet
                    + "</text:list-style>",
                ),
            }
        )
        cls.notices = {}
        payloads = {}
        for name, parts in cls.probes.items():
            payloads[name], cls.notices[name] = _probe_notice(parts)
        cls.renderings = render_table_cells(payloads)

    def _check(self, name: str) -> tuple:
        notice = self.notices[name]
        clause = notice.clauses[0]
        rendering = self.renderings[name]
        self.assertIsNotNone(rendering)
        check = cell_rendering_check(notice, clause, rendering)
        return clause, check, projection_block_reason(clause, check)

    def test_label_found_only_in_another_cell_is_a_mismatch(self) -> None:
        clause, check, reason = self._check("phantom_level")
        self.assertEqual(clause.revised[1].generated_label, "1.")
        self.assertEqual(
            (check, reason), ("mismatch", "official_rendering_mismatch")
        )

    def test_list_in_a_covered_cell_is_not_counted(self) -> None:
        # Finding R2-H2: LibreOffice discards the covered cell, so the revised
        # item is its list's second.  Counting the covered item made it 3.,
        # which the original column (started at 3) showed with the same text.
        clause, check, reason = self._check("covered_cell")
        self.assertEqual(clause.revised[1].printed_text, "2.\t單獨用於")
        self.assertEqual((check, reason), ("exact", None))

    def test_separator_the_suite_draws_differently_is_a_mismatch(self) -> None:
        clause, check, reason = self._check("newline_separator")
        self.assertEqual(clause.revised[1].printed_text, "1.單獨用於")
        self.assertEqual(
            (check, reason), ("mismatch", "official_rendering_mismatch")
        )

    def test_whitespace_elements_and_labels_render_as_parsed(self) -> None:
        clause, check, reason = self._check("whitespace_positive")
        self.assertEqual(
            [item.printed_text for item in clause.revised[1:]],
            ["1.\t限  用於\t成人", "2.\t每日\n一次"],
        )
        self.assertEqual((check, reason), ("exact", None))

    def test_what_the_string_does_not_show_is_a_mismatch(self) -> None:
        # Findings B2-B4: the string of a hidden span, a hidden paragraph or
        # a hidden label, a bullet drawn instead of no label, and a case map
        # all looked exact.  The rendering now reports them.
        for name, key in (
            ("hidden_span", "hidden"),
            ("hidden_paragraph", "hidden"),
            ("hidden_label", "hidden"),
            ("uppercase_label", "transform"),
            ("small_caps_text", "transform"),
            ("bullet_instead_of_none", "bullet"),
        ):
            with self.subTest(probe=name):
                clause, check, reason = self._check(name)
                cell = self.renderings[name]["tables"][0][1][0]
                self.assertTrue(any(paragraph.get(key) for paragraph in cell))
                self.assertEqual(check, "mismatch")
                self.assertEqual(reason, "official_rendering_mismatch")
        # Confusable negative: the controls report none of them.
        for name in ("whitespace_positive", "covered_cell"):
            cell = self.renderings[name]["tables"][-1][1][0]
            self.assertFalse(
                any(p.get(key) for p in cell for key in ("hidden", "bullet", "transform"))
            )


def _tampered(rendering: dict, notice, item, **change) -> dict:
    """``rendering`` with ``item``'s paragraph in its own cell changed."""

    copy = json.loads(json.dumps(rendering))
    table = sorted(notice.document.top_tables).index(item.top_table_index)
    cell = copy["tables"][table][item.top_row_index][item.top_cell_index]
    matches = [
        paragraph
        for paragraph in cell
        if paragraph.get("text") == item.text
        and paragraph.get("label") == item.generated_label
    ]
    assert len(matches) == 1, "the paragraph is not unique in its cell"
    matches[0].update(change)
    return copy


class NumberingFixtureTest(unittest.TestCase):
    """Official notices whose clauses carry automatic list numbering.

    Each fixture keeps the exact ``office:body`` bytes of the official ODT and
    the styles its list labels depend on; LibreOffice renders it identically
    to the official file.
    """

    def _parts(self, notice: dict) -> tuple[bytes, bytes, bytes]:
        files = notice["fixture"]["files"]
        return tuple(
            (NUMBERING / files[part]["file_name"]).read_bytes()
            for part in ("content", "styles", "meta")
        )

    def _parse(self, notice: dict):
        payload = _odt(*self._parts(notice))
        official = notice["attachment"]
        attachment = NoticeAttachment(
            declared_sequence=official["declared_sequence"],
            file_name=official["file_name"],
            media_type=official["media_type"],
            sha256=official["official_sha256"],
            byte_size=official["official_byte_size"],
            path=NUMBERING / notice["fixture"]["files"]["content"]["file_name"],
        )
        document = read_odt_document(
            payload, artifact_sha256=official["official_sha256"]
        )
        receipts = tuple(
            (item.block_id, item.block_text_sha256) for item in document.paragraphs
        )
        bundle = NoticeBundle(
            bundle_dir=NUMBERING,
            source_uid=notice["source_uid"],
            reference_number=notice["reference_number"],
            title=notice["title"],
            official_url=notice["official_url"],
            published_on=notice["published_on"],
            manifest_sha256="0" * 64,
            attachments=(attachment,),
            announcement_items=tuple(notice["announcement_items"]),
            raw_md_blocks={official["file_name"]: receipts},
        )
        return payload, receipts, parse_comparison_document(
            bundle, attachment, document
        )

    def test_fixture_bytes_are_pinned(self) -> None:
        self.assertEqual(len(NUMBERING_MANIFEST["notices"]), 5)
        for notice in NUMBERING_MANIFEST["notices"]:
            for part in notice["fixture"]["files"].values():
                payload = (NUMBERING / part["file_name"]).read_bytes()
                with self.subTest(fixture=part["file_name"]):
                    self.assertEqual(len(payload), part["byte_size"])
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(), part["sha256"]
                    )

    def test_labels_and_patch_texts_match_pinned_expectations(self) -> None:
        for notice in NUMBERING_MANIFEST["notices"]:
            expected = notice["expected"]
            _, receipts, parsed = self._parse(notice)
            with self.subTest(reference=notice["reference_number"]):
                # Block identity equals the corpus registration receipts.
                self.assertEqual(
                    object_fingerprint([list(pair) for pair in receipts]),
                    notice["raw_md_block_receipts_sha256"],
                )
                self.assertEqual(parsed.effective_on, expected["effective_on"])
                self.assertEqual(
                    sum(1 for item in parsed.document.paragraphs if item.generated_label),
                    expected["labelled_block_count"],
                )
                self.assertEqual(
                    [clause.clause_code for clause in parsed.clauses],
                    [clause["clause_code"] for clause in expected["clauses"]],
                )
            for clause, pinned in zip(parsed.clauses, expected["clauses"]):
                with self.subTest(clause=clause.clause_code):
                    self.assertEqual(
                        sha256_text(clause.patch_text), pinned["revised_text_sha256"]
                    )
                    self.assertEqual(
                        sha256_text(clause.original_text),
                        pinned["original_text_sha256"],
                    )
                    self.assertEqual(
                        object_fingerprint(clause.component_manifest()),
                        pinned["component_manifest_sha256"],
                    )
                    self.assertEqual(
                        [item.generated_label for item in clause.revised
                         if item.generated_label],
                        pinned["generated_labels"],
                    )
                    self.assertEqual(clause.numbering_blocked_reasons, ())
                    self.assertEqual(
                        projection_block_reason(
                            clause, pinned["official_rendering_check"]
                        ),
                        pinned["projection_block_reason"],
                    )

    def test_new_clause_labels_follow_the_official_print(self) -> None:
        by_code = {}
        for notice in NUMBERING_MANIFEST["notices"]:
            for clause in self._parse(notice)[2].clauses:
                by_code[(notice["reference_number"], clause.clause_code)] = clause
        rivaroxaban_free = by_code[("健保審字第1150057031號", "8.2.19")]
        self.assertTrue(
            rivaroxaban_free.revised[1].printed_text.startswith("1.\t限用於經")
        )
        pemetrexed = by_code[("健保審字第1150672522號", "9.26")]
        # (5) continues a list whose style starts at 5, as printed.
        self.assertEqual(
            [item.generated_label for item in pemetrexed.revised
             if item.generated_label],
            ["(5)", "(6)"],
        )
        paclitaxel = by_code[("健保審字第1150672522號", "9.5.1")]
        self.assertEqual(paclitaxel.revised[1].printed_text, "1.\t~2.(略)")
        self.assertTrue(paclitaxel.omission_orders)
        maralixibat = by_code[("健保審字第1150055691號", "3.3.32")]
        self.assertIn("II.\t符合下列任一診斷條件：", maralixibat.patch_text)
        self.assertIn("(3)\t續用申請時", maralixibat.patch_text)
        immunotherapy = by_code[("健保審字第1150672522號", "9.69")]
        self.assertTrue(any(item.nested for item in immunotherapy.revised))

    @unittest.skipUnless(_office_available(), "LibreOffice/UNO is unavailable")
    def test_independent_office_rendering(self) -> None:
        parsed_notices = {}
        for notice in NUMBERING_MANIFEST["notices"]:
            payload, _, parsed = self._parse(notice)
            parsed_notices[notice["reference_number"]] = (payload, notice, parsed)
        renderings = render_table_cells(
            {reference: payload for reference, (payload, _, _) in parsed_notices.items()}
        )
        for reference, (_, notice, parsed) in parsed_notices.items():
            rendering = renderings[reference]
            self.assertIsNotNone(rendering)
            for clause, pinned in zip(
                parsed.clauses, notice["expected"]["clauses"]
            ):
                with self.subTest(clause=clause.clause_code):
                    self.assertEqual(
                        cell_rendering_check(parsed, clause, rendering),
                        pinned["official_rendering_check"],
                    )
                labelled = [item for item in clause.revised if item.generated_label]
                if not labelled or pinned["official_rendering_check"] != "exact":
                    continue
                # Confusable negatives: LibreOffice's own cell with the same
                # text under another label, or with another gap.
                item = labelled[0]
                for change in (
                    {"label": "(" + item.generated_label + ")"},
                    {"separator": " "},
                ):
                    tampered = _tampered(rendering, parsed, item, **change)
                    self.assertEqual(
                        cell_rendering_check(parsed, clause, tampered), "mismatch"
                    )


if __name__ == "__main__":
    unittest.main()
