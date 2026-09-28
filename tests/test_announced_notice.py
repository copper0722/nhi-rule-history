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
    read_odt_document,
    roc_date,
    sha256_text,
)
from nhi_rule_history.pg.common import object_fingerprint
from tools.build_announced_notice_fixture import fixture_odt


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "announced_notices"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
)


def _odt(content_xml: bytes) -> bytes:
    return fixture_odt(content_xml)


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
        raw_md_blocks=raw_md_blocks or {},
    )


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

    def test_generated_list_labels_block_projection(self) -> None:
        styles = (
            '<text:list-style style:name="L1">'
            '<text:list-level-style-number text:level="1" style:num-suffix="." '
            'style:num-format="1"/></text:list-style>'
            '<text:list-style style:name="L2">'
            '<text:list-level-style-number text:level="1" style:num-format=""/>'
            "</text:list-style>"
        )

        def listed(style: str) -> str:
            return (
                "<table:table-cell><text:p>9.139.Mogamulizumab：(115/10/1)</text:p>"
                f'<text:list text:style-name="{style}"><text:list-item>'
                "<text:p>單獨用於</text:p><text:p>續行段落</text:p>"
                "</text:list-item></text:list></table:table-cell>"
            )

        for style, blocked in (("L1", True), ("L2", False)):
            body = (
                "<text:p>（自115年10月1日生效）</text:p><table:table>"
                "<table:table-row>" + _cell("修訂後給付規定") + _cell("原給付規定")
                + "</table:table-row><table:table-row>" + listed(style)
                + _cell("無") + "</table:table-row></table:table>"
            )
            parsed = _parse_payload(_document(body, automatic_styles=styles))
            clause = parsed.clauses[0]
            with self.subTest(style=style):
                labels = [item.generated_label for item in clause.revised]
                if blocked:
                    self.assertEqual(labels, [None, "list_label", None])
                    self.assertEqual(clause.blocked_reason, "generated_list_label")
                else:
                    self.assertEqual(labels, [None, None, None])
                    self.assertIsNone(clause.blocked_reason)

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


if __name__ == "__main__":
    unittest.main()
