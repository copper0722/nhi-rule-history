"""The string-level grammar shared by the announced overlay and the comparison cards."""

from __future__ import annotations

import json
import unicodedata
import unittest
from pathlib import Path

from nhi_rule_history.comparison_grammar import (
    GENERAL_PRINCIPLE_HEADING,
    cjk_ordinal,
    is_general_principle_heading,
    is_none_cell,
    read_point_marker,
    reads_like_an_omission_marker,
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


def _visible(text: str) -> str:
    """``text`` without its invisible control and format characters (white space kept)."""

    return "".join(char for char in text if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf"})


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

    def test_a_connector_after_white_space_is_looked_for_after_the_white_space(self) -> None:
        # Verifier mutant B8: with the leading symbol read from the first character, a space before it hid it.
        # These lines show nothing else a range shows (no second ordinal 、, no omission), so only the leading
        # dash, math or connector symbol, found after the white space, tells them from a point's heading text.
        for connector in ("∼", "﹏", "－", "—", "–", "―", "-", "_", "+", "="):
            for gap in (" ", "  ", "\t", "\u3000", " \u3000"):
                for text in (f"一、{gap}{connector}三", f"一、{gap}{connector}"):
                    with self.subTest(text=text):
                        self.assertEqual(read_point_marker(text), "range_marker_unreadable")
        # controls: the same white space before ordinary heading text is still a point
        for text in ("一、 注射藥品", "一、\t注射藥品", "一、\u3000注射藥品", "一、 三"):
            with self.subTest(text=text):
                self.assertEqual(read_point_marker(text), ("point", 1, 1))

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


class LookalikeTest(unittest.TestCase):
    """A line that only looks like an omission marker is not part of the point above it (re-verification note 4)."""

    def test_a_bare_or_第_head_that_ends_in_an_omission_is_a_lookalike(self) -> None:
        for text in (
            "五至十二、(略)", "五~十二、(略)", "第五、~十二、(略)", "五至十二、略", "五至十二、(以下略)", "五至十二、（略）。",
            "第十二、(略)", "十(略)", "五起至十二(略)", " 五至十二、(略)", "\u3000五至十二、(略)",
        ):
            for latest in (0, 4, 11):
                with self.subTest(text=text, latest=latest):
                    self.assertTrue(reads_like_an_omission_marker(text, latest))
        # whatever stands between the two ordinals
        for connector in ("至", "到", "~", "～", "〜", "∼", "﹏", "－", "—", "_", "…", "x", "起", "及", " ", "。", "，"):
            with self.subTest(connector=connector):
                self.assertTrue(reads_like_an_omission_marker(f"五{connector}十二、(略)", 4))
        # a sentence that starts with a numeral and ends in an omission is held too: the cut cannot tell it from
        # a range (none of the 252 omission lines of the 28 registered documents is such a line)
        self.assertTrue(reads_like_an_omission_marker("十二指腸潰瘍之使用(略)", 4))

    def test_a_bracketed_head_is_a_lookalike_only_at_the_next_top_level_ordinal(self) -> None:
        # In this document sub-items are numbered （一）: the official row carries （三）~（四）(略) inside point 四.
        # （五）～（十二）(略) after point 四 is equally the omitted points that follow and the point's own sub-item
        # omission, so it is a lookalike; the same shape starting anywhere else is sub-item text.
        for text in ("（五）～（十二）(略)", "(五)~(十二)(略)", "[五]~[十二](略)", "【五】～【十二】(略)", "〔五〕～〔十二〕(略)",
                     "（五）(略)", "（五）～（十二）（以下略）", "（五）－（十二）略", " （五）至（十二）(略)"):
            with self.subTest(text=text):
                self.assertTrue(reads_like_an_omission_marker(text, 4))
                self.assertFalse(reads_like_an_omission_marker(text, 3))
                self.assertFalse(reads_like_an_omission_marker(text, 5))
        for latest in range(12):
            with self.subTest(latest=latest):
                self.assertEqual(reads_like_an_omission_marker("（三）~（四）(略)", latest), latest == 2)

    def test_what_is_not_a_lookalike(self) -> None:
        for text in (
            "1.~8.(略)", "10.~26.(略)", "（三）~（四）(略)", "（一）注射藥品使用時機", "（五）注射藥品使用時機",
            "一般注射劑之使用", "三個月內不得重複給付", "十二歲以上", "(略)", "附註：(略)", "備註：策略", "",
        ):
            with self.subTest(text=text):
                self.assertFalse(reads_like_an_omission_marker(text, 4))

    def test_a_point_marker_or_a_recognised_omission_is_never_a_lookalike(self) -> None:
        # a line the marker grammar reads (or names unreadable) is that grammar's, whatever it ends in
        for text in ("五、~十二、(略)", "五、~ 十二、(略)", "五、~十二、( 略 )", "五、~十二、(略) 。", "十、(略)",
                     "四、注射藥品之使用原則：", "五、～十二(略)", "一、－三、(略)"):
            with self.subTest(text=text):
                self.assertFalse(reads_like_an_omission_marker(text, 4))


class HiddenMarkerTest(unittest.TestCase):
    """A marker an invisible character hides: the character stream has none, a reader sees one."""

    INVISIBLE = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u200e", "\u200f", "\u180e", "\x00", "\x1b")
    HIDDEN = ("五、~十二、(略)", "五至十二、(略)", "（五）～（十二）(略)", "五、丙：", "十、(略)", "第五、~十二、(略)")

    @staticmethod
    def column(line: str) -> list[str]:
        return [GENERAL_PRINCIPLE_HEADING, "四、甲：", "（一）乙", line]

    def test_an_invisible_character_in_front_of_a_marker_refuses_the_cut(self) -> None:
        # without the refusal such a line is no marker, so it runs on as text of the point above it (a hidden point
        # merges into the previous point, a hidden omission marker into its patch)
        for char in self.INVISIBLE:
            for line in self.HIDDEN:
                with self.subTest(char=hex(ord(char)), line=line):
                    split = split_general_principle(self.column(char + line))
                    self.assertEqual((split.points, split.refusal), ((), "marker_behind_invisible_characters"))

    def test_an_invisible_character_inside_a_marker_refuses_the_cut_too(self) -> None:
        for char in self.INVISIBLE:
            for line in self.HIDDEN:
                with self.subTest(char=hex(ord(char)), line=line):
                    split = split_general_principle(self.column(line[:1] + char + line[1:]))
                    # a line that is a bare-head lookalike as it stands is refused as that one
                    self.assertIn(split.refusal, {"marker_behind_invisible_characters", "omission_marker_lookalike"})
                    self.assertEqual(split.points, ())

    def test_what_an_invisible_character_does_not_change(self) -> None:
        # a line that does not read as a marker either way stays the text of the point, wherever the character stands
        for char in self.INVISIBLE:
            for line in ("注射藥品使用時機", "（二）乙：", "注射藥品(略)", "備註：策略", "1.~8.(略)", "（三）~（四）(略)"):
                for text in (char + line, line[:2] + char + line[2:], line + char):
                    with self.subTest(char=hex(ord(char)), text=text):
                        split = split_general_principle(self.column(text))
                        self.assertEqual((split.points, split.refusal), (((4, 1, 4),), None))
        # white space is visible: it is not removed, so a space or a tab between the ordinal and 、 is read as before
        # (no marker; a tab is a control character, which is why the check has to keep white space)
        for gap in (" ", "\t", "\u3000", "\u00a0"):
            with self.subTest(gap=repr(gap)):
                split = split_general_principle(self.column(f"五{gap}、丙："))
                self.assertEqual((split.points, split.refusal), (((4, 1, 4),), None))
        # ordinary white space in front of a marker is read as before
        split = split_general_principle(self.column("\u3000五、丙："))
        self.assertEqual((split.points, split.refusal), (((4, 1, 3), (5, 3, 4)), None))
        split = split_general_principle(self.column("\t五、~十二、(略)"))
        self.assertEqual((split.points, split.omissions, split.refusal), (((4, 1, 3),), ((3, 5, 12),), None))

    def test_a_hidden_marker_in_a_nested_table_is_the_points_own_text(self) -> None:
        texts = self.column("\u200b五、丙：")
        self.assertEqual(split_general_principle(texts).refusal, "marker_behind_invisible_characters")
        split = split_general_principle(texts, plain={3})
        self.assertEqual((split.points, split.refusal), (((4, 1, 4),), None))

    def test_the_official_row_has_no_invisible_character(self) -> None:
        for column in ("new", "old"):
            for text in GENERAL_ROW[column]:
                self.assertEqual(_visible(text), text)


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
            "omission_marker_lookalike": [GENERAL_PRINCIPLE_HEADING, "四、甲：", "（一）乙", "五至十二、(略)"],
            "omission_marker_lookalike (a ~ without the first 、)": [GENERAL_PRINCIPLE_HEADING, "四、甲：", "五~十二、(略)"],
            "omission_marker_lookalike (第)": [GENERAL_PRINCIPLE_HEADING, "四、甲：", "第五、~十二、(略)"],
            "omission_marker_lookalike (brackets at the next point)": [
                GENERAL_PRINCIPLE_HEADING, "四、甲：", "（一）乙", "（三）~（四）(略)", "（五）～（十二）(略)"],
            "omission_marker_lookalike (between two points)": [
                GENERAL_PRINCIPLE_HEADING, "一、~三、(略)", "四、甲：", "五至十二、(略)", "十三、乙："],
        }
        for name, texts in cases.items():
            with self.subTest(name=name):
                split = split_general_principle(texts)
                self.assertEqual(split.refusal, name.split(" ")[0])
                self.assertEqual((split.points, split.omissions), ((), ()))

    def test_a_lookalike_of_the_official_final_line_is_never_absorbed_into_the_point(self) -> None:
        # Re-verification note 4: replace the official row's last paragraph (五、~十二、(略)) by lookalikes.  They used
        # to run on as the last paragraph of 0.4's patch; now the column is refused, and the forms that are read
        # (or that are sub-item text) cut exactly as the official row does.
        for column in ("new", "old"):
            official = split_general_principle(GENERAL_ROW[column])
            for variant in ("五至十二、(略)", "五~十二、(略)", "第五、~十二、(略)", "（五）～（十二）(略)"):
                with self.subTest(column=column, variant=variant):
                    texts = [*GENERAL_ROW[column][:9], variant]
                    split = split_general_principle(texts)
                    self.assertEqual(
                        (split.points, split.omissions, split.refusal), ((), (), "omission_marker_lookalike"))
            for variant in ("五、~十二、(略) 。", "五、~ 十二、(略)", "五、~十二、( 略 )", "五、~十二、（略）", "五、～十二、(略)"):
                with self.subTest(column=column, variant=variant):
                    texts = [*GENERAL_ROW[column][:9], variant]
                    split = split_general_principle(texts)
                    self.assertEqual((split.points, split.omissions, split.refusal), (official.points, official.omissions, None))
            # sub-item text: a bracketed range that does not start at the next point's number
            for variant in ("（六）～（十二）(略)", "（四）～（五）(略)", "（五）注射藥品使用時機"):
                with self.subTest(column=column, variant=variant):
                    texts = [*GENERAL_ROW[column][:9], variant]
                    split = split_general_principle(texts)
                    self.assertEqual((split.points, split.omissions, split.refusal), (((4, 2, 10),), ((1, 1, 3),), None))
            # a lookalike with no open point stands outside any point
            split = split_general_principle([GENERAL_ROW[column][0], "五至十二、(略)", *GENERAL_ROW[column][2:9]])
            self.assertEqual(split.refusal, "text_outside_a_point")

    def test_a_lookalike_in_a_nested_table_is_the_points_own_text(self) -> None:
        texts = [GENERAL_PRINCIPLE_HEADING, "四、甲：", "五至十二、(略)", "（五）～（十二）(略)"]
        self.assertEqual(split_general_principle(texts).refusal, "omission_marker_lookalike")
        split = split_general_principle(texts, plain={2, 3})
        self.assertEqual((split.points, split.refusal), (((4, 1, 4),), None))

    def test_a_paragraph_in_a_nested_table_is_never_a_marker(self) -> None:
        texts = [GENERAL_PRINCIPLE_HEADING, "四、甲：", "五、表內文字", "六、表內文字"]
        self.assertEqual(split_general_principle(texts).points, ((4, 1, 2), (5, 2, 3), (6, 3, 4)))
        split = split_general_principle(texts, plain={2, 3})
        self.assertEqual(split.points, ((4, 1, 4),))


if __name__ == "__main__":
    unittest.main()
