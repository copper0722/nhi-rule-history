"""Deterministic checks for the comparison-card diff (tools/diff_cards)."""

import json

import pytest

from tests.test_announced_notice import ALHEMO, _comparison, _odt
from tools.diff_cards import sources_rule
from tools.diff_cards.build import (
    main,
    make_rule_docs,
    paginate,
    parse_ids,
    rule_card_rows,
    rule_cover,
)
from tools.diff_cards.core import (
    Block,
    Row,
    align,
    elide,
    inline_changes,
    inline_spans,
    key,
    verify_block,
)
from tools.diff_cards.html import Doc, hunk_html
from tools.diff_cards.sources_law import parse_markup
from tools.diff_cards.sources_nhi import (
    build_blocks,
    card_view,
    general_principle_row,
    none_cell,
    read_table,
    whitespace_only_changes,
)
from tools.diff_cards.sources_rule import (
    attachment_block,
    attachment_card_rows,
    build_snapshot,
    check_coverage,
    check_summaries,
    cn2int,
    parse_gazette,
)


def test_leading_marker_and_whitespace_do_not_count():
    assert key("(3)病人之生物標記表現") == key("病人之生物標記表現")
    assert key("4.登錄與結案作業(略)") == key("登錄與結案作業(略)")
    assert key("INCAT (Inflammatory") == key("INCAT(Inflammatory")


def test_compatibility_ideographs_are_equal():
    # U+F9C1 is a compatibility ideograph for U+7642 (療); NFKC makes them equal
    assert inline_spans("治療", "治療") == ([], [])


def test_inline_spans_mark_the_changed_words():
    a, b = "限用於成人患者：", "限用於成人病人："
    da, db = inline_spans(a, b)
    assert [a[x:y] for x, y in da] == ["患者"]
    assert [b[x:y] for x, y in db] == ["病人"]


def test_pure_insertion_leaves_an_anchor_on_the_old_side():
    a, b = "(110/7/1)", "(110/7/1、115/10/1)"
    da, db, aa, _ = inline_changes(a, b)
    assert da == [] and [b[x:y] for x, y in db] == ["、115/10/1"]
    assert aa and a[aa[0]] == ")"


def test_align_pairs_edits_and_keeps_insertions():
    old = ["1.甲條件", "2.乙條件"]
    new = ["1.甲條件修訂", "新增段落", "2.乙條件"]
    assert align(old, new) == [("pair", 0, 0), ("add", 1), ("ctx", 1, 2)]


def test_elision_snaps_to_punctuation():
    text = "9.69.免疫檢查點抑制劑(如atezolizumab；nivolumab；pembrolizumab製劑)：(108/4/1、109/6/1、110/5/1、111/4/1、115/8/1、115/10/1)"
    start = text.index("、115/10/1")
    row = Row("add", text, spans=[(start, start + len("、115/10/1"))])
    assert elide(row)
    for a, b in row.keep:
        assert a == 0 or text[a - 1] in "、，；：()（） " or text[a] in "、，；：()（） "
        assert b == len(text) or text[b] in "、，；：()（） "
    assert row.keep[0][0] == 0 and row.keep[-1][1] == len(text)


def test_statute_markup_round_trip():
    text, spans = parse_markup("法人{不}得為醫療社團法人之社員。")
    assert text == "法人不得為醫療社團法人之社員。"
    assert [text[a:b] for a, b in spans] == ["不"]


def test_nhi_blocks_cover_every_paragraph_once():
    table = {"clauses": [
        {"new": ["9.1.甲藥：(114/1/1、115/10/1)", "1.限用於成人病人。", "2.(略)"],
         "old": ["9.1.甲藥：(114/1/1)", "1.限用於成人患者。", "2.(略)"]},
        {"new": ["9.2.乙藥：(115/10/1)", "1.新適應症。"], "old": ["無"]},
    ]}
    modified, added = build_blocks(table, {})
    assert (modified.badge, modified.adds, modified.dels) == ("修訂", 2, 2)
    assert (added.badge, added.adds, added.dels) == ("新增", 2, 0)
    assert [r.kind for r in card_view(modified.rows)] == ["del", "add", "del", "add", "ctx"]


GENERAL = "藥品給付規定通則"


def alhemo_table() -> dict:
    return {"clauses": [{"new": list(row["new"]), "old": list(row["old"])} for row in ALHEMO["rows"]]}


def test_a_general_principle_row_is_one_clause_block_ahead_of_the_numbered_clauses():
    blocks = build_blocks(alhemo_table(), {})
    assert [b.clause_id for b in blocks] == ["通則", "4.2", "4.2.9", "4.2.7", "4.2.8"]
    general = blocks[0]
    assert (general.path, general.badge, general.badge_kind) == ("藥品給付規定/通則", "修訂", "m")
    assert (general.adds, general.dels, general.hunk_old, general.hunk_new) == (1, 1, "第1～10段", "第1～10段")
    assert general.sort_key == (0,) and min(blocks, key=lambda b: b.sort_key) is general
    # the replay check ran over the whole row: every paragraph of both columns is covered once
    assert sorted(i for r in general.rows for i in r.covers_old) == list(range(10))
    assert sorted(j for r in general.rows for j in r.covers_new) == list(range(10))
    # 4.2.9's original cell reads （無）, with full-width parentheses: the clause is new
    new_clause = blocks[2]
    assert (new_clause.badge, new_clause.adds, new_clause.dels, new_clause.hunk_old) == ("新增", 8, 0, "（無）")


def test_a_general_principle_row_needs_the_heading_in_both_columns():
    new = [GENERAL, "四、甲：", "（一）乙"]
    assert general_principle_row(new, [GENERAL, "四、甲：", "（一）丙"])
    assert general_principle_row([" " + GENERAL + " "], ["藥品給付規定　通則"])  # width and spacing do not matter
    assert not general_principle_row(new, [])
    assert not general_principle_row(new, ["4.2.血液代用製劑"])
    assert not general_principle_row([], [GENERAL])
    with pytest.raises(ValueError, match="clause number not found"):
        build_blocks({"clauses": [{"new": new, "old": ["4.2.血液代用製劑"]}]}, {})
    with pytest.raises(ValueError, match="clause number not found"):
        build_blocks({"clauses": [{"new": new, "old": ["（無）"]}]}, {})


@pytest.mark.parametrize("cell,expected", [
    (["無"], True), (["(無)"], True), (["（無）"], True), ([" 無。 "], True),
    ([], False), (["無", "限用於甲"], False), (["（無）限用於甲"], False), (["無法給付"], False), (["9.1.甲藥："], False),
])
def test_none_cell_forms(cell, expected):
    assert none_cell(cell) is expected


def test_a_general_principle_row_does_not_take_the_place_of_the_first_clause_in_sorting():
    table = alhemo_table()
    table["extra_comparisons"] = [{"index": 1, "kind": "附表", "title": "附表十八之五 測試", "rows": [
        {"old": ["甲"], "new": ["乙"]}]}]
    *_, attachment = build_blocks(table, {})
    # an attachment sorts under the first numbered clause of the table, as it does without a 通則 row
    assert attachment.clause_id == "附表十八之五" and attachment.sort_key == (4, 2, 9999, 0)


def test_whitespace_only_changes_reads_the_none_cell_like_the_blocks_do():
    table = alhemo_table()
    assert whitespace_only_changes(table) == 0
    table["clauses"][2]["old"] = ["無"]
    assert whitespace_only_changes(table) == 0


# --- administrative rules from the gazette (tools/diff_cards sources_rule) -------------------------------

ORDER_NEW, ORDER_OLD = "測授字第2號", "測授字第1號"


def para(level: int, num: str, text: str) -> str:
    return f'<p class="level_{level}"><span class="num">{num}</span><span class="p_content">{text}</span></p>'


CHAPTER_PLAIN = '<p class="level_0"><span class="p_content">　　第二章　甲章</span></p>'
CHAPTER_IN_NUM = '<p class="level_0"><span class="num">　　第四章</span><span class="p_content">　乙章</span></p>'
LONG = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥甲乙丙丁戊己庚"  # longer than the 28-character context rule


def gazette(order_no: str, *paras: str, pre: str = "", date: str = "中華民國115年1月2日") -> bytes:
    """Tiny 網頁文字版: announcement, order text, bold title, provisions, attachment line."""
    return (f'<html><body><div class="announcement"><span>{date}</span> <span>{order_no}</span></div>'
            f'<p class="line2_indent">令文</p>{pre}<p class="bold">測試注意事項修正規定</p>{"".join(paras)}'
            '<p class="attachment">附件</p></body></html>').encode()


NEW = gazette(ORDER_NEW, CHAPTER_PLAIN, para(0, "三、", LONG), para(1, "（一）", "新版第一款。"),
              para(1, "（二）", "第二款不變。"), para(0, "　　", "第二項新增的一段。"), CHAPTER_IN_NUM,
              para(0, "十一、", "第十一點的新內容。"), pre=para(0, "一、", "令文第一點"))
OLD = gazette(ORDER_OLD, CHAPTER_PLAIN, para(0, "三、", LONG), para(1, "（一）", "舊版第一款。"),
              para(1, "（二）", "第二款不變。"), CHAPTER_IN_NUM, para(0, "十一、", "第十一點的舊內容。"),
              date="中華民國114年1月2日")


def line(num: str, text: str, level: int = 0) -> dict:
    return {"num": num, "text": text, "level": level}


def unit(uid: str, old: list, new: list, para_unit: str = "項") -> dict:
    status = "增訂" if not old else "刪除" if not new else "修正"
    return {"id": uid, "label": f"第{uid}點", "para_unit": para_unit, "status": status, "old": old, "new": new}


def snap_of(*units: dict, attachments: tuple = ()) -> dict:
    return {"schema": sources_rule.SCHEMA, "rule": "r", "units": list(units), "attachments": list(attachments)}


def editorial(*documents: dict) -> dict:
    return {"path_root": "規則", "kicker": "k", "legend_old": "前", "legend_new": "後", "number_note": "n",
            "source_lines": ["s"], "disclaimer": "d",
            "cover": {"cmd": "c", "big": "b", "sub": "s", "dates_html": "d", "hash": "h", "hash_note": "n"},
            "cover_labels": {"3": "甲", "5": "乙", "9": "丙", "14": "丁"}, "cover_foot": ["f"],
            "index": {"title": "t", "subtitle": "s", "foot": ["f"]}, "documents": list(documents)}


def doc_of(*unit_ids: str, **extra) -> dict:
    return {"slug": "d", "title": "t", "summary": [], "blocks": [{"unit": u} for u in unit_ids], **extra}


def test_gazette_points_numerals_and_chapter_lines():
    g = parse_gazette(NEW)
    assert g["announcement"] == ["中華民國115年1月2日", ORDER_NEW]
    assert g["title"] == "測試注意事項修正規定"
    assert g["order"] == ["3", "11"]  # 一、 of the order text before the bold title is not a provision
    assert [g["points"][p]["label"] for p in g["order"]] == ["三、", "十一、"]
    assert [cn2int(x) for x in ("九", "十", "十一", "二十", "二十一", "一百二十三")] == [9, 10, 11, 20, 21, 123]
    # a chapter line is recorded for the following points in both markups, never kept as provision text
    assert g["points"]["3"]["chapter_printed"] == "第二章　甲章"
    assert g["points"]["11"]["chapter_printed"] == "第四章　乙章"
    assert not any("章" in ln["text"] + ln["num"] for p in g["points"].values() for ln in p["lines"])


def test_gazette_line_citing_a_chapter_is_provision_text_not_a_chapter_line():
    g = parse_gazette(gazette(ORDER_NEW, CHAPTER_PLAIN, para(0, "三、", "甲"), para(0, "　　", "第三章所定事項準用之。")))
    assert g["points"]["3"]["lines"] == [line("", "甲"), line("", "第三章所定事項準用之。")]
    assert g["points"]["3"]["chapter_printed"] == "第二章　甲章"


def test_gazette_point_label_is_not_part_of_the_first_line():
    lines = parse_gazette(NEW)["points"]["3"]["lines"]
    assert lines == [line("", LONG), line("（一）", "新版第一款。", 1), line("（二）", "第二款不變。", 1),
                     line("", "第二項新增的一段。")]
    nbsp = parse_gazette(gazette(ORDER_NEW, para(0, "\xa0三、", "\xa0甲\u3000")))["points"]["3"]
    assert (nbsp["label"], nbsp["lines"]) == ("三、", [line("", "甲")])


@pytest.mark.parametrize(("html", "message"), [
    pytest.param(gazette(ORDER_NEW, para(0, "三、", "甲"), para(0, "三、", "乙")), "point 3 appears twice",
                 id="repeated-point"),
    pytest.param(gazette(ORDER_NEW, para(1, "（一）", "甲")), "paragraph before any point", id="before-any-point"),
    pytest.param(gazette(ORDER_NEW, CHAPTER_PLAIN, para(0, "　　", "章後的續行")), "paragraph before any point",
                 id="continuation-after-chapter-line"),
    pytest.param(gazette(ORDER_NEW, '<p class="level_0">雜<span class="num">三、</span><span class="p_content">甲</span></p>'),
                 "outside the num/p_content spans", id="text-outside-spans"),
    pytest.param(gazette(ORDER_NEW, para(0, "三、", "甲"), '<p class="bold">第二個標題</p>'), "second bold title",
                 id="second-bold-title"),
    pytest.param(b'<html><p class="bold">t</p></html>', "announcement", id="no-announcement"),
    pytest.param(b'<div class="announcement"><span>d</span><span>n</span></div>', "no bold title", id="no-title"),
])
def test_gazette_fails_closed(html, message):
    with pytest.raises(ValueError, match=message):
        parse_gazette(html)


def test_snapshot_gate_on_the_order_number(tmp_path):
    (tmp_path / "new.htm").write_bytes(NEW)
    (tmp_path / "old.htm").write_bytes(OLD)
    out = tmp_path / "pub" / "source" / "provisions.json"
    args = ["snapshot-gazette", "--out", str(out), "--rule", "測試注意事項", "--new", str(tmp_path / "new.htm"),
            "--old", f"3,11={tmp_path / 'old.htm'}"]
    with pytest.raises(SystemExit, match=f"測授字第9號.*{ORDER_NEW}"):  # the message names both numbers
        main([*args, "--expect-order", "測授字第9號"])
    with pytest.raises(SystemExit, match="date mismatch"):
        main([*args, "--expect-order", ORDER_NEW, "--expect-date", "中華民國1年1月1日"])
    assert not out.exists()
    assert main([*args, "--expect-order", ORDER_NEW, "--expect-date", "中華民國115年1月2日"]) == 0
    snap = json.loads(out.read_text(encoding="utf-8"))
    assert snap["schema"] == sources_rule.SCHEMA and snap["rule"] == "測試注意事項"
    assert snap["new"]["file"] == "new.htm" and snap["new"]["announcement"][1] == ORDER_NEW
    assert snap["old"][0]["file"] == "old.htm" and snap["old"][0]["points"] == ["3", "11"]
    assert [(u["id"], u["label"], u["para_unit"], u["status"], len(u["old"]), len(u["new"])) for u in snap["units"]] == [
        ("3", "第3點", "項", "修正", 3, 4), ("11", "第11點", "項", "修正", 1, 1)]
    assert snap["units"][0]["chapter_printed"] == {"old": "第二章　甲章", "new": "第二章　甲章"}
    assert snap["attachments"] == []


def test_snapshot_cli_merges_extra_files_and_rejects_bad_arguments(tmp_path):
    (tmp_path / "new.htm").write_bytes(NEW)
    (tmp_path / "old.htm").write_bytes(OLD)
    extra = {**unit("附表一之三", [line("", "舊")], [line("", "新")], "段"), "label": "附表一之三"}
    att = {"id": "附表一之四", "heading": "某表", "pages": [3, 3], "status": "修正"}
    (tmp_path / "extra.json").write_text(json.dumps([extra]), encoding="utf-8")
    (tmp_path / "att.json").write_text(json.dumps([att]), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "s.json"
    base = ["snapshot-gazette", "--out", str(out), "--rule", "r", "--new", str(tmp_path / "new.htm"),
            "--expect-order", ORDER_NEW]
    old = ["--old", f"3,11={tmp_path / 'old.htm'}"]
    assert main([*base, *old, "--extra-units", str(tmp_path / "extra.json"),
                 "--attachments", str(tmp_path / "att.json")]) == 0
    snap = json.loads(out.read_text(encoding="utf-8"))
    assert [u["id"] for u in snap["units"]] == ["3", "11", "附表一之三"] and snap["attachments"] == [att]
    out.unlink()
    for args, message in [([*base, "--old", "3,11"], "expects IDS=FILE"),
                          ([*base, "--old", f"3,3={tmp_path / 'old.htm'}"], "without repeats"),
                          ([*base, *old, "--extra-units", str(tmp_path / "bad.json")], "expected a JSON array"),
                          ([*base, "--old", f"3={tmp_path / 'old.htm'}"], "no OLD source.*11"),
                          ([*base, "--old", f"3,11={tmp_path / 'missing.htm'}"], "missing.htm")]:
        with pytest.raises(SystemExit, match=message):
            main(args)
    assert not out.exists()


def test_snapshot_old_mapping_rules():
    new, old = parse_gazette(NEW), parse_gazette(OLD)

    def build(ids, **kw):
        return build_snapshot("r", "n.htm", new, ORDER_NEW, [("o.htm", old, ids)], **kw)

    with pytest.raises(ValueError, match="no OLD source.*11"):
        build(["3"])
    snap = build(["3"], added=["11"])
    assert [(u["id"], u["status"], len(u["old"])) for u in snap["units"]] == [("3", "修正", 3), ("11", "增訂", 0)]
    assert snap["units"][1]["chapter_printed"] == {"old": None, "new": "第四章　乙章"}
    with pytest.raises(ValueError, match="point 5 is named for o.htm but absent from it"):
        build(["3", "5"], added=["11"])
    with pytest.raises(ValueError, match="both --added and"):
        build(["3", "11"], added=["11"])
    with pytest.raises(ValueError, match="--added names point 7"):
        build(["3", "11"], added=["7"])
    with pytest.raises(ValueError, match="OLD file is the NEW file"):
        build_snapshot("r", "n.htm", new, ORDER_NEW, [("n.htm", new, ["3", "11"])])
    with pytest.raises(ValueError, match="named by two"):
        build_snapshot("r", "n.htm", new, ORDER_NEW, [("o.htm", old, ["3", "11"]), ("p.htm", old, ["3"])])
    assert parse_ids("3,5,11", "--old") == ["3", "5", "11"]
    for bad in ("3,3", "a", "3,,5"):
        with pytest.raises(SystemExit):
            parse_ids(bad, "--old")


def test_snapshot_merges_extra_units_and_attachments_after_shape_validation():
    new, old = parse_gazette(NEW), parse_gazette(OLD)
    extra = {**unit("附表一之三", [line("", "舊")], [line("", "新")], "段"), "label": "附表一之三",
             "chapter_printed": {"old": None, "new": None}}
    att = {"id": "附表一之四", "heading": "某表", "pages": [3, 4], "status": "增訂"}

    def build(**kw):
        return build_snapshot("r", "n.htm", new, ORDER_NEW, [("o.htm", old, ["3", "11"])], **kw)

    snap = build(extra_units=[extra], attachments=[att])
    assert snap["units"][-1] == extra and snap["attachments"] == [att]
    with pytest.raises(ValueError, match="repeated: 3"):
        build(extra_units=[{**extra, "id": "3"}])
    with pytest.raises(ValueError, match="status 增訂 does not fit"):
        build(extra_units=[{**extra, "status": "增訂"}])
    with pytest.raises(ValueError, match="must be {num, text, level}"):
        build(extra_units=[{**extra, "new": [{"text": "新"}]}])
    with pytest.raises(ValueError, match="backwards"):
        build(attachments=[{**att, "pages": [4, 3]}])
    with pytest.raises(ValueError, match="must be {id, heading, pages, status}"):
        build(attachments=[{"id": "x"}])


def test_item_unit_numbers_level_zero_lines_segment_unit_numbers_every_line():
    old = [line("", "甲" * 30), line("（一）", "乙" * 30, 1)]
    new = [*old, line("", "丙" * 30)]
    assert sources_rule.numbers(new, "項") == [1, 0, 2]
    assert sources_rule.numbers(new, "段") == [1, 2, 3]
    items, segs = sources_rule.build_blocks(snap_of(unit("1", old, new), unit("2", old, new, "段")),
                                            doc_of("1", "2"))
    assert [(r.old_no, r.new_no) for r in items.rows] == [("1", "1"), ("", ""), ("", "2")]
    assert [(r.old_no, r.new_no) for r in segs.rows] == [("1", "1"), ("2", "2"), ("", "3")]
    assert (items.hunk_old, items.hunk_new) == ("第1項", "第1～2項")
    assert (segs.hunk_old, segs.hunk_new) == ("第1～2段", "第1～3段")


def test_rule_block_covers_every_line_once_and_marks_edits():
    old = [line("", "第一項甲乙丙丁戊己庚辛"), line("（一）", "舊款內容一二三四五六七八九十甲乙丙丁", 1),
           line("（二）", "不變", 1)]
    new = [line("", "第一項甲乙丙丁戊己庚辛"), line("（一）", "新款內容一二三四五六七八九十甲乙丙丁", 1),
           line("（二）", "不變", 1), line("", "全新的一項")]
    spec = {**doc_of("3"), "blocks": [{"unit": "3", "fn": "x"}]}
    block, = sources_rule.build_blocks(snap_of(unit("3", old, new)), spec, "規則")
    verify_block(block, [sources_rule.display(x) for x in old], [sources_rule.display(x) for x in new])
    assert (block.path, block.badge, block.badge_kind, block.fn, block.clause_id) == ("規則/第3點", "修正", "m", "x", "3")
    assert (block.adds, block.dels) == (2, 1)
    (gone,) = [r for r in block.rows if r.kind == "del"]
    edited = next(r for r in block.rows if r.kind == "add" and r.pair == gone.pair)
    (pure,) = [r for r in block.rows if r.kind == "add" and r.pair < 0]
    assert [gone.text[a:b] for a, b in gone.spans] == ["舊"] and [edited.text[a:b] for a, b in edited.spans] == ["新"]
    assert pure.text == "全新的一項" and pure.spans == [] and pure.new_no == "2"
    # the printed number is part of the displayed text and sets the hanging indent
    assert edited.text.startswith("（一）新款") and edited.indent_em == 3.0
    assert block.rows[0].indent_em == 0.0 and block.rows[0].text == "第一項甲乙丙丁戊己庚辛"
    assert [r.kind for r in block.rows] == ["ctx", "del", "add", "ctx", "add"]


def test_added_unit_has_no_old_side_and_unknown_unit_fails():
    snap = snap_of(unit("14", [], [line("", "新增的一點")]))
    block, = sources_rule.build_blocks(snap, doc_of("14"))
    assert (block.badge, block.badge_kind, block.hunk_old, block.adds, block.dels) == ("增訂", "a", "（無）", 1, 0)
    with pytest.raises(ValueError, match="unknown unit '99'"):
        sources_rule.build_blocks(snap, doc_of("99"))


def gate_docs(*summaries: tuple[str, str]):
    snap = snap_of(
        unit("3", [line("", "第一項：甲乙丙丁戊己庚壬")], [line("", "第一項：甲乙丙丁戊己庚辛")]),  # edited pair
        unit("5", [line("", "甲乙丙丁戊己庚辛壬癸")], [line("", "甲乙丙丁戊己庚辛癸")]),  # pair, only a deletion
        unit("9", [], [line("", "全新的一點，期限30日")]),  # pure addition
        unit("14", [line("", "子丑寅卯辰巳午未酉")], [line("", "子丑寅卯辰巳午未戌酉")]))  # pair, only an insertion
    ed = editorial({**doc_of("3", "5", "9", "14"), "summary": [list(s) for s in summaries]})
    docs, specs = make_rule_docs(snap, ed)
    return list(zip(docs, specs, strict=True))


def test_summary_gate_passes_verbatim_changed_phrases():
    check_summaries(gate_docs(("改字", "~~壬~~ → **辛**"), ("新增", "**全新的一點**，期限 **30日**，共 30"),
                              ("刪除", "~~壬~~"), ("前後", "~~第一項：甲乙丙丁戊己庚壬~~ → **庚辛**"),
                              ("插入", "**戌**")))


@pytest.mark.parametrize(("summary", "message"), [
    pytest.param(("無此詞", "**不存在的詞**"), r"\*\*不存在的詞\*\* is not new text", id="phrase-in-no-row"),
    pytest.param(("只在未改文字", "**甲乙丙丁**"), r"\*\*甲乙丙丁\*\* is not new text",
                 id="phrase-only-in-unchanged-part-of-an-edited-pair"),
    pytest.param(("舊詞在新側", "**壬**"), r"\*\*壬\*\* is not new text", id="old-phrase-marked-as-new"),
    pytest.param(("新詞在舊側", "~~辛~~"), "~~辛~~ is not deleted text", id="new-phrase-marked-as-old"),
    pytest.param(("舊文未改", "~~第一項~~"), "~~第一項~~ is not deleted text", id="old-phrase-unchanged-part"),
    pytest.param(("數字不在列", "共 7 點"), "number 7 is in no added or deleted line", id="digit-run-in-text"),
    pytest.param(("標籤數字 45", "~~壬~~"), "number 45 is in no added", id="digit-run-in-label"),
])
def test_summary_gate_fails_closed_and_names_the_offender(summary, message):
    with pytest.raises(ValueError, match=message):
        check_summaries(gate_docs(summary))


def test_summary_gate_pair_row_without_spans_is_not_a_pure_addition():
    # unit 5 / 14 are edited pairs whose added / deleted side carries no span: that side holds no new / old text,
    # so a phrase found only there must not pass (a pure addition without spans does accept any occurrence)
    with pytest.raises(ValueError, match=r"\*\*甲乙丙丁戊己庚辛癸\*\* is not new text"):
        check_summaries(gate_docs(("只刪未增", "**甲乙丙丁戊己庚辛癸**")))
    with pytest.raises(ValueError, match="~~子丑寅卯辰巳午未酉~~ is not deleted text"):
        check_summaries(gate_docs(("只增未刪", "~~子丑寅卯辰巳午未酉~~")))
    check_summaries(gate_docs(("真正新增", "**全新的一點，期限30日**")))


def test_summary_gate_compares_like_the_rest_of_the_tool():
    # the source line holds a compatibility ideograph (U+FA01 for 度) and spaces from a PDF text layer; the phrase
    # is typed with the standard ideograph and no spaces. Only the changed span counts, as before.
    snap = snap_of(unit("3", [line("", "甲乙丙丁戊己庚辛壬癸，對象已為收案 A B 計畫之個案。")],
                        [line("", "甲乙丙丁戊己庚辛壬癸，對象前一年\ufa01已為收案 A B 計畫之個案。")]))

    def run(text):
        docs, specs = make_rule_docs(snap, editorial(doc_of("3", summary=[["基準", text]])))
        check_summaries(list(zip(docs, specs, strict=True)))

    run("**前一年度**")
    run("**前 一 年 度**")
    with pytest.raises(ValueError, match="AB計畫"):
        run("**AB計畫**")  # present in the line (spaces aside) but untouched by the change


def test_summary_gate_lists_every_offender():
    with pytest.raises(ValueError) as exc:
        check_summaries(gate_docs(("一", "**甲**"), ("二", "~~乙~~ 共 7")))
    assert str(exc.value).count("\n  ") == 3 and "d / 一" in str(exc.value) and "d / 二" in str(exc.value)


ATTACHMENTS = [{"id": "附表一之一", "heading": "甲表", "pages": [5, 5], "status": "修正"},
               {"id": "附表一之二", "heading": "乙表", "pages": [6, 8], "status": "增訂"},
               {"id": "附件三", "heading": "丙附件", "pages": [9, 9], "status": "刪除"}]


def att_doc(*groups: dict, **extra) -> dict:
    return {"slug": "a", "title": "附件", "summary": [], **extra,
            "attachments": {"label": "附表及附件", "hunk_label": "不逐字比對的附表", "groups": list(groups)}}


GROUP1 = {"label": "附表一（兒童）", "strip": "附表一", "ids": ["附表一之一", "附表一之二"]}
GROUP2 = {"label": "附件", "ids": ["附件三"]}


def test_attachment_documents_cover_the_snapshot_attachments_exactly_once():
    snap = snap_of(unit("3", [line("", "舊")], [line("", "新")]), attachments=ATTACHMENTS)

    def run(*groups, blocks=("3",)):
        check_coverage(snap, editorial(doc_of(*blocks), att_doc(*groups)))

    run(GROUP1, GROUP2)
    with pytest.raises(ValueError, match="attachments missing from the documents: 附件三"):
        run(GROUP1)
    with pytest.raises(ValueError, match="attachments not in the snapshot: 附表九"):
        run(GROUP1, GROUP2, {"label": "x", "ids": ["附表九"]})
    with pytest.raises(ValueError, match="attachments used more than once: 附件三"):
        run(GROUP1, GROUP2, GROUP2)
    with pytest.raises(ValueError, match="units missing from the documents: 3"):
        run(GROUP1, GROUP2, blocks=())
    with pytest.raises(ValueError, match="units used more than once: 3"):
        check_coverage(snap, editorial(doc_of("3"), doc_of("3"), att_doc(GROUP1, GROUP2)))


def test_attachment_list_rows_for_cards_and_for_the_lookup_page():
    snap = snap_of(unit("3", [line("", "舊")], [line("", "新")]), attachments=ATTACHMENTS)
    att = att_doc(GROUP1, GROUP2)["attachments"]
    card = attachment_card_rows(att)
    assert [(r.kind, r.text) for r in card] == [("ctx", "附表一（兒童）：之一、之二"), ("ctx", "附件：附件三")]
    assert {r.indent_em for r in card} == {2.0}  # a wrapped list entry hangs under its first line
    block = attachment_block(snap, att, "規則")
    assert [r.text for r in block.rows] == ["附表一之一　甲表（公報 PDF 第 5 頁）", "附表一之二　乙表（公報 PDF 第 6～8 頁）（新增）",
                                           "附件三　丙附件（公報 PDF 第 9 頁）（刪除）"]
    assert (block.path, block.badge, block.hunk_label, block.adds, block.dels) == ("規則/附表及附件", "清單",
                                                                                 "不逐字比對的附表", 0, 0)
    with pytest.raises(ValueError, match="do not start with '附表二'"):
        attachment_card_rows({"label": "x", "groups": [{"label": "x", "strip": "附表二", "ids": ["附表一之一"]}]})


def test_summary_gate_checks_an_attachment_document_against_its_card_rows():
    snap = snap_of(unit("3", [line("", "舊")], [line("", "新")]), attachments=ATTACHMENTS)

    def run(summary):
        ed = editorial(doc_of("3"), att_doc(GROUP1, GROUP2, summary=[summary]))
        docs, specs = make_rule_docs(snap, ed)
        check_summaries(list(zip(docs, specs, strict=True)))

    run(["新增", "**附表一（兒童）** 之 **之二**"])
    with pytest.raises(ValueError, match=r"\*\*附表九\*\* is not new text"):
        run(["新增", "**附表九**"])
    with pytest.raises(ValueError, match="number 12 is in no added"):
        run(["共 12 項", "之二"])


def test_hunk_label_replaces_the_old_new_ranges_and_default_is_unchanged():
    plain = Block("p", "修正", "m", "fn", [], hunk_old="第1段", hunk_new="第1～2段")
    listing = Block("p", "清單", "m", "", [], hunk_label="附表一之一至之三")
    assert hunk_html(plain, "u") == ('<div class="r hunk" data-u="u"><span class="g">@@</span><span class="t">'
                                     '-第1段 +第1～2段 @@<span class="fn">fn</span></span></div>')
    assert hunk_html(listing) == ('<div class="r hunk"><span class="g">@@</span><span class="t">'
                                  '附表一之一至之三 @@</span></div>')


def test_a_list_block_continues_on_the_next_card_instead_of_folding_its_tail():
    heights = {"head1": 100.0, "head2": 40.0, "foot": 60.0, "b0-fhead": 40.0, "b0-hunk": 40.0, "b0-xref": 40.0,
               **{f"b0-r{i}": 100.0 for i in range(12)}}

    def paginate_block(label):
        rows = [Row("ctx", f"附表{i}") for i in range(12)]
        doc = Doc(slug="d", title="t", kicker="k", summary=[], blocks=[Block("p", "x", "m", "", rows, hunk_label=label)],
                  legend_old="o", legend_new="n", number_note="x", source_lines=[])
        return rows, paginate(doc, {0: rows}, dict(heights))

    rows, pages = paginate_block("清單")
    assert len(pages) == 2 and all(r.kind == "ctx" for r in rows)
    assert [(s["start"], s["end"], s["xref_bottom"], s["xref_top"]) for pg in pages for s in pg] == [
        (0, 9, "接下一張", ""), (9, 12, "", "接上一張")]
    rows, pages = paginate_block("")  # an ordinary block folds the same unchanged tail into one row
    assert len(pages) == 1 and rows[-1].kind == "fold" and rows[-1].text == "未修訂 3 段"


def test_rule_cover_counts_by_status_lists_units_and_honours_total_prefix():
    snap = snap_of(unit("3", [line("", "舊")], [line("", "新")]), unit("14", [], [line("", "增")]),
                   unit("5", [line("", "刪")], []), attachments=ATTACHMENTS)
    ed = editorial(doc_of("3", "14", "5"), att_doc(GROUP1, GROUP2))
    docs, specs = make_rule_docs(snap, ed)
    meta, stat_rows, toc = rule_cover(snap, ed, docs, specs)
    assert meta["total_prefix"] == "<b>3</b> 項異動（修正 1、增訂 1、刪除 1）" and meta["cmd"] == "c"
    assert stat_rows == [{"id": "第3點", "label": "甲", "adds": 1, "dels": 1},
                         {"id": "第14點", "label": "丁", "adds": 1, "dels": 0},
                         {"id": "第5點", "label": "乙", "adds": 0, "dels": 1}]
    assert toc == [{"meta": "第3點、第14點、第5點"}, {"meta": "3 項"}]
    only = snap_of(unit("3", [line("", "舊")], [line("", "新")]))
    ed = editorial(doc_of("3"))
    docs, specs = make_rule_docs(only, ed)
    assert rule_cover(only, ed, docs, specs)[0]["total_prefix"] == "<b>1</b> 項異動（修正 1）"
    ed["cover"]["total_prefix"] = "自訂"
    assert rule_cover(only, ed, docs, specs)[0]["total_prefix"] == "自訂"
    ed["cover_labels"].pop("3")
    with pytest.raises(ValueError, match="cover_labels lacks unit '3'"):
        rule_cover(only, ed, docs, specs)


def test_rule_documents_are_gated_before_anything_is_built():
    snap = snap_of(unit("3", [line("", "舊")], [line("", "新")]), unit("14", [], [line("", "增")]))
    with pytest.raises(ValueError, match="units missing from the documents: 14"):
        make_rule_docs(snap, editorial(doc_of("3")))
    with pytest.raises(ValueError, match="either blocks or attachments"):
        make_rule_docs(snap_of(), editorial(att_doc(GROUP1, blocks=[])))
    with pytest.raises(ValueError, match="slugs must be unique"):
        make_rule_docs(snap, editorial(doc_of("3"), doc_of("14")))
    ed = editorial(doc_of("3", "14"))
    del ed["kicker"], ed["cover"]["hash_note"], ed["index"]["foot"]
    with pytest.raises(ValueError, match="editorial.json lacks: kicker, cover.hash_note, index.foot"):
        make_rule_docs(snap, ed)


def test_rule_card_rows_elision_switch_and_unfolded_attachment_rows():
    old = [line("", "甲" * 60 + "舊" + "乙" * 60)]
    new = [line("", "甲" * 60 + "新" + "乙" * 60)]
    groups = [{"label": f"附表{i}", "ids": [f"附表{i}之一"]} for i in range(5)]
    snap = snap_of(unit("3", old, new), attachments=[
        {"id": f"附表{i}之一", "heading": "h", "pages": [1, 1], "status": "修正"} for i in range(5)])

    def rows_for(elide_on):
        docs, specs = make_rule_docs(snap, editorial({**doc_of("3"), "elide": elide_on}, att_doc(*groups)))
        return docs, rule_card_rows(specs)

    docs, hook = rows_for(True)
    assert any(r.keep for r in hook(docs[0], 0)[0])
    docs, hook = rows_for(False)
    assert not any(r.keep for r in hook(docs[0], 0)[0]) and len(hook(docs[0], 1)[0]) == 2
    for level in (0, 1):  # card_view at fold level 1 would fold two unchanged rows or more: the list never is
        assert [r.kind for r in hook(docs[1], level)[0]] == ["ctx"] * 5


def test_render_rule_stops_at_the_gates_before_writing_anything(tmp_path):
    pub = tmp_path / "pub"
    (pub / "source").mkdir(parents=True)
    snap = snap_of(unit("3", [line("", "第一項：甲乙丙丁戊己庚壬")], [line("", "第一項：甲乙丙丁戊己庚辛")]))
    (pub / "source" / "provisions.json").write_text(json.dumps(snap), encoding="utf-8")

    def attempt(ed, message):
        (pub / "editorial.json").write_text(json.dumps(ed), encoding="utf-8")
        with pytest.raises(SystemExit, match=message):
            main(["render-rule", str(pub), "--png", str(tmp_path / "png")])
        assert not (pub / "cards").exists() and not (pub / "index.html").exists() and not (tmp_path / "png").exists()

    good = {**doc_of("3"), "summary": [["改字", "~~壬~~ → **辛**"]]}
    attempt(editorial({**good, "summary": [["改字", "~~壬~~ → **庚**，共 9 處"]]}), r"render-rule: summary gate:\n.*\n")
    attempt(editorial(doc_of("3"), doc_of("3")), "render-rule: .*slugs must be unique")
    attempt(editorial({**good, "blocks": [{"unit": "7"}]}), "render-rule: coverage:")
    snap["schema"] = "other"
    (pub / "source" / "provisions.json").write_text(json.dumps(snap), encoding="utf-8")
    attempt(editorial(good), "render-rule: not a .* snapshot")


def test_read_table_takes_the_proposed_revised_header_and_keeps_the_kind():
    # the 2.6.1 notice prints 建議修訂後給付規定 over the revised column
    rows = [(["9.4.Fixture：(115/9/1)", "限用於甲"], ["9.4.Fixture：", "限用於乙"])]
    plain = read_table(_odt(_comparison(rows)))
    proposed = read_table(_odt(_comparison(rows, header=("建議修訂後給付規定", "原給付規定"))))
    assert proposed["clauses"] == plain["clauses"] and len(plain["clauses"]) == 1
    assert proposed["extra_comparisons"] == plain["extra_comparisons"] == [] and proposed["extra_tables"] == []
    # the kind is still the one the original header names: 給付 in the first table, nothing else
    for header in (("建議修訂後給付規定", "原附表規定"), ("建議修訂後給付規定", "建議原給付規定"),
                   ("修正後給付規定", "原給付規定"), ("建議建議修訂後給付規定", "原給付規定"),
                   ("建議後給付規定", "原給付規定")):
        with pytest.raises(ValueError, match="unexpected comparison-table header"):
            read_table(_odt(_comparison(rows, header=header)))
    with pytest.raises(ValueError, match="table 0 is not a 給付規定 comparison"):
        read_table(_odt(_comparison(rows, header=("建議修訂後附表規定", "原附表規定"))))
