"""The string-level grammar shared by the announced overlay and the comparison cards."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from nhi_rule_history.comparison_grammar import (
    GENERAL_PRINCIPLE_HEADING,
    cjk_ordinal,
    is_general_principle_heading,
    is_none_cell,
    read_point_marker,
    split_general_principle,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "announced_notices" / "1150057388.rows.json").read_text(
        encoding="utf-8"
    )
)
# Row 1 of the official table is the 通則 row: heading, 一、~三、(略), the 四 point (7 paragraphs),
# 五、~十二、(略).
GENERAL_ROW = FIXTURE["rows"][0]


class NoneCellTest(unittest.TestCase):
    def test_the_forms_of_a_none_cell(self) -> None:
        for text in ("無", "(無)", "（無）", " （無） ", "無。", "（無）。", "(　無　)"):
            with self.subTest(text=text):
                self.assertTrue(is_none_cell(text))

    def test_confusable_negatives(self) -> None:
        for text in ("無法給付", "（無）限用於", "有", "", "無無", "（無）（無）", "不得無故使用"):
            with self.subTest(text=text):
                self.assertFalse(is_none_cell(text))


class HeadingTest(unittest.TestCase):
    def test_heading_ignores_width_and_spacing(self) -> None:
        for text in (
            GENERAL_PRINCIPLE_HEADING,
            " 藥品給付規定通則 ",
            "藥品給付規定　通則",
            "藥品 給付規定 通則",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_general_principle_heading(text))

    def test_other_headings_are_not_the_chapter_heading(self) -> None:
        for text in ("通則", "藥品給付規定通則：", "藥品給付規定", "4.2.血液代用製劑", "", "一、藥品給付規定通則"):
            with self.subTest(text=text):
                self.assertFalse(is_general_principle_heading(text))


class OrdinalTest(unittest.TestCase):
    def test_plain_positional_numerals(self) -> None:
        expected = {
            "一": 1, "四": 4, "九": 9, "十": 10, "十一": 11, "十二": 12, "十九": 19, "二十": 20,
            "二十一": 21, "九十": 90, "九十九": 99,
        }
        for text, value in expected.items():
            with self.subTest(text=text):
                self.assertEqual(cjk_ordinal(text), value)

    def test_anything_else_is_unreadable(self) -> None:
        for text in ("", "十十", "一二", "二十十", "百", "一百", "零", "十一一", "4", "四、"):
            with self.subTest(text=text):
                self.assertIsNone(cjk_ordinal(text))


class MarkerTest(unittest.TestCase):
    def test_points_and_omissions(self) -> None:
        cases = {
            "四、注射藥品之使用原則：": ("point", 4, 4),
            "十二、藥物給付規定所列需檢附生物標記檢測結果報告者": ("point", 12, 12),
            "四、": ("point", 4, 4),
            "一、~三、(略)": ("omission", 1, 3),
            "五、~十二、(略)": ("omission", 5, 12),
            "五、～十二、（略）": ("omission", 5, 12),  # full-width tilde and parentheses
            "五、〜十二、(略)": ("omission", 5, 12),  # wave dash
            "一、 ~ 三、 (略) ": ("omission", 1, 3),
            "十、(略)": ("omission", 10, 10),
            "十、略": ("omission", 10, 10),
            "三、~三、(略)": ("omission", 3, 3),
            "一、~三、(以下略)": ("omission", 1, 3),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(read_point_marker(text), expected)

    def test_heading_text_is_a_point_even_when_it_mentions_an_omission_inside(self) -> None:
        # the heading text is real; only a line that is all omission, or shows what only a range shows, is not
        for text in ("四、(略)注射藥品", "四、策略", "四、說明略", "四、注射藥品之使用原則：", "十一、給付規定中年齡用詞",
                     "四、依(略)規定辦理，詳見附表"):
            with self.subTest(text=text):
                self.assertEqual(read_point_marker(text)[0:1], ("point",))

    def test_a_range_written_with_a_connector_the_grammar_does_not_read_is_never_a_point(self) -> None:
        # Verifier finding (2026-10-19): ∼ ﹏ － — 至 after 一、 read as point 一 and invented a spurious 0.1.
        # Only ~ ～ 〜 are range connectors; every other one fails closed, with or without the second 、,
        # with or without an omission, and so does a marker line that ends in an omission.
        connectors = ("∼", "﹏", "－", "—", "至", "–", "―", "-", "－－", "到", "～～", "_", "…")
        for connector in connectors:
            for text in (
                f"一、{connector}三、(略)", f"一、{connector}三、（略）", f"一、 {connector} 三、 (略)",
                f"一、{connector}三(略)", f"一、{connector}三、", f"一、{connector}三、同上", f"一、{connector}(略)",
            ):
                with self.subTest(text=text):
                    self.assertEqual(read_point_marker(text), "range_marker_unreadable")
        for text in ("一、∼", "一、﹏三", "一、－", "一、—三", "一、至三(略)", "五、至十二、(略)", "十、至十二、（略）",
                     "四、注射藥品之使用原則：(略)", "四、：略", "四、(以下略)注射用藥(略)"):
            with self.subTest(text=text):
                self.assertEqual(read_point_marker(text), "range_marker_unreadable")
        # the three recognised connectors are still ranges
        for connector in ("~", "～", "〜"):
            with self.subTest(connector=connector):
                self.assertEqual(read_point_marker(f"一、{connector}三、(略)"), ("omission", 1, 3))

    def test_not_a_marker(self) -> None:
        for text in (
            "（一）注射藥品使用時機", "1.~8.(略)", "9.門診之血友病人", "藥品給付規定通則", "", "  ",
            "注射藥品之使用原則：", "四注射藥品", "四，注射藥品",
        ):
            with self.subTest(text=text):
                self.assertIsNone(read_point_marker(text))

    def test_a_marker_that_cannot_be_read_names_why(self) -> None:
        cases = {
            "十十、注射": "ordinal_unreadable",
            "一、~十十、(略)": "ordinal_unreadable",
            "一、~(略)": "range_marker_unreadable",
            "一、~": "range_marker_unreadable",
            "三、~一、(略)": "range_marker_reversed",
            "一、~三、同上": "range_marker_is_not_an_omission",
            "一、~三、注射藥品": "range_marker_is_not_an_omission",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                self.assertEqual(read_point_marker(text), reason)


class SplitTest(unittest.TestCase):
    def test_the_official_row_has_one_point_and_two_omission_markers(self) -> None:
        for column in ("new", "old"):
            texts = GENERAL_ROW[column]
            with self.subTest(column=column):
                split = split_general_principle(texts)
                self.assertIsNone(split.refusal)
                self.assertEqual(len(texts), 10)
                # heading 0, 一、~三、(略) 1, the 四 point 2..8, 五、~十二、(略) 9
                self.assertEqual(split.points, ((4, 2, 9),))
                self.assertEqual(split.omissions, ((1, 1, 3), (9, 5, 12)))
                self.assertEqual(split.codes, ("0.4",))
                self.assertEqual(texts[2][:4], "四、注射")
                # the in-point (略) lines are the point's own text
                self.assertIn("1.~8.(略)", texts[2:9])
                self.assertIn("（三）~（四）(略)", texts[2:9])

    def test_several_points_and_a_single_omitted_point(self) -> None:
        texts = [
            GENERAL_PRINCIPLE_HEADING, "一、~三、(略)", "四、甲：", "（一）乙", "五、(略)", "六、丙：", "七、~十二、(略)",
        ]
        split = split_general_principle(texts)
        self.assertEqual(split.points, ((4, 2, 4), (6, 5, 6)))
        self.assertEqual(split.codes, ("0.4", "0.6"))
        self.assertEqual(split.omissions, ((1, 1, 3), (4, 5, 5), (6, 7, 12)))

    def test_a_column_that_starts_at_a_later_point_is_fine(self) -> None:
        split = split_general_principle([GENERAL_PRINCIPLE_HEADING, "四、甲：", "（一）乙"])
        self.assertEqual((split.points, split.omissions, split.refusal), (((4, 1, 3),), (), None))

    def test_the_trailing_point_runs_to_the_end_of_the_column(self) -> None:
        split = split_general_principle([GENERAL_PRINCIPLE_HEADING, "一、~三、(略)", "四、甲：", "（一）乙", " "])
        self.assertEqual(split.points, ((4, 2, 5),))

    def test_blank_paragraphs_outside_a_point_are_ignored(self) -> None:
        split = split_general_principle([GENERAL_PRINCIPLE_HEADING, " ", "一、~三、(略)", "　", "四、甲："])
        self.assertEqual((split.points, split.omissions), (((4, 4, 5),), ((2, 1, 3),)))

    def test_the_cut_fails_closed_with_a_named_reason(self) -> None:
        cases = {
            "heading_missing": [],
            "heading_missing ": ["四、甲：", "（一）乙"],
            "text_outside_a_point": [GENERAL_PRINCIPLE_HEADING, "附註", "四、甲："],
            "text_outside_a_point after an omission": [GENERAL_PRINCIPLE_HEADING, "一、~三、(略)", "（一）乙"],
            "ordinals_not_increasing": [GENERAL_PRINCIPLE_HEADING, "四、甲：", "三、乙："],
            "ordinals_not_increasing (a repeated point)": [GENERAL_PRINCIPLE_HEADING, "四、甲：", "四、乙："],
            "ordinals_not_increasing (inside a range)": [GENERAL_PRINCIPLE_HEADING, "一、~五、(略)", "四、甲："],
            "no_point": [GENERAL_PRINCIPLE_HEADING],
            "no_point only omissions": [GENERAL_PRINCIPLE_HEADING, "一、~十二、(略)"],
            "ordinal_unreadable": [GENERAL_PRINCIPLE_HEADING, "十十、甲："],
            "range_marker_unreadable": [GENERAL_PRINCIPLE_HEADING, "一、~(略)", "四、甲："],
            "range_marker_unreadable (another connector)": [GENERAL_PRINCIPLE_HEADING, "一、∼三、(略)", "四、甲："],
            "range_marker_unreadable (a connector, no second 、)": [GENERAL_PRINCIPLE_HEADING, "一、至三(略)", "四、甲："],
        }
        for name, texts in cases.items():
            with self.subTest(name=name):
                split = split_general_principle(texts)
                self.assertEqual(split.refusal, name.split(" ")[0])
                self.assertEqual((split.points, split.omissions), ((), ()))

    def test_a_paragraph_in_a_nested_table_is_never_a_marker(self) -> None:
        texts = [GENERAL_PRINCIPLE_HEADING, "四、甲：", "五、表內文字", "六、表內文字"]
        self.assertEqual(split_general_principle(texts).points, ((4, 1, 2), (5, 2, 3), (6, 3, 4)))
        split = split_general_principle(texts, plain={2, 3})
        self.assertEqual(split.points, ((4, 1, 4),))


if __name__ == "__main__":
    unittest.main()
