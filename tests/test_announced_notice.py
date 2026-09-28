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
    clause_heading_code,
    exact_in_rendering,
    is_omission_marker,
    libreoffice_text_export,
    parse_comparison_document,
    parse_effective_statement,
    projection_block_reason,
    read_odt_document,
    roc_date,
    sha256_text,
)
from nhi_rule_history.pg.common import object_fingerprint
from tools.build_announced_notice_fixture import fixture_odt


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

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice is unavailable")
    def test_independent_office_rendering(self) -> None:
        for notice in MANIFEST["notices"]:
            payload, parsed = self._parse(notice)
            rendering = libreoffice_text_export(payload)
            self.assertIsNotNone(rendering)
            for clause in parsed.clauses:
                with self.subTest(clause=clause.clause_code):
                    self.assertEqual(exact_in_rendering(clause, rendering), "exact")
            # Confusable negative: one changed character must not match.
            clause = parsed.clauses[0]
            tampered = rendering.replace(clause.revised[-1].text, clause.revised[-1].text[:-1] + "X")
            self.assertEqual(exact_in_rendering(clause, tampered), "mismatch")


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
        return _parse_payload(_document(body, automatic_styles=levels)).clauses[0]

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
        clause = self._listed_clause(
            "L1",
            '<text:list-style style:name="L1">'
            '<text:list-level-style-number text:level="1" style:num-prefix="(" '
            'style:num-suffix=")" style:num-format="1" text:start-value="5">'
            f"{alignment}</text:list-level-style-number></text:list-style>",
        )
        self.assertEqual(clause.revised[1].printed_text, "(5)\t單獨用於")
        head = "修訂後給付規定\t原給付規定\n9.139.Mogamulizumab：(115/10/1)\n"
        exact = head + "    (5) 單獨用於\n       續行段落\t無\n"
        self.assertEqual(exact_in_rendering(clause, exact), "exact")
        for tampered in (
            exact.replace("(5)", "(6)"),
            exact.replace("    (5) 單獨用於", "(5)單獨用於"),
            exact.replace("    (5) 單獨用於", "單獨用於"),
            exact.replace("       續行段落", "續行段落"),
        ):
            with self.subTest(tampered=tampered):
                self.assertEqual(exact_in_rendering(clause, tampered), "mismatch")
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

        clause = _parse_payload(row(nested=True)).clauses[0]
        self.assertTrue(any(item.nested for item in clause.revised))
        self.assertEqual(clause.blocked_reason, "nested_table")
        self.assertEqual(exact_in_rendering(clause, "x"), "not_applicable_nested_table")
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

    def test_rendering_check_accepts_cell_boundaries_only(self) -> None:
        parsed = _parse_payload(
            _comparison([(["9.2.Carboplatin：", "限"], ["9.2.Carboplatin："])])
        )
        clause = parsed.clauses[0]
        self.assertEqual(
            exact_in_rendering(clause, "前格\t9.2.Carboplatin：\n限\t9.2.Carboplatin："),
            "exact",
        )
        self.assertEqual(
            exact_in_rendering(clause, "x9.2.Carboplatin：\n限\n"), "mismatch"
        )
        self.assertEqual(
            exact_in_rendering(clause, "    1. 9.2.Carboplatin：\n限\n"), "mismatch"
        )
        self.assertEqual(exact_in_rendering(clause, None), "unavailable")


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

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice is unavailable")
    def test_independent_office_rendering(self) -> None:
        for notice in NUMBERING_MANIFEST["notices"]:
            payload, _, parsed = self._parse(notice)
            rendering = libreoffice_text_export(payload)
            self.assertIsNotNone(rendering)
            for clause, pinned in zip(
                parsed.clauses, notice["expected"]["clauses"]
            ):
                with self.subTest(clause=clause.clause_code):
                    self.assertEqual(
                        exact_in_rendering(clause, rendering),
                        pinned["official_rendering_check"],
                    )
                labelled = [item for item in clause.revised if item.generated_label]
                if not labelled or pinned["official_rendering_check"] != "exact":
                    continue
                # Confusable negative: the same text under a different label.
                item = labelled[0]
                wrong = item.export_prefix.replace(
                    item.generated_label, "(" + item.generated_label + ")"
                )
                tampered = rendering.replace(
                    item.export_prefix + item.text, wrong + item.text
                )
                self.assertNotEqual(tampered, rendering)
                self.assertEqual(exact_in_rendering(clause, tampered), "mismatch")


if __name__ == "__main__":
    unittest.main()
