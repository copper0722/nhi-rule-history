"""Deterministic checks for the cards of a stored comparison (tools/diff_cards/pg_render): no database, no browser."""

import copy
import re
from datetime import date

import pytest

from tests.test_announced_notice import ALHEMO
from tests.test_diff_cards import alhemo_table
from tools.diff_cards import pg_render
from tools.diff_cards.build import main
from tools.diff_cards.pg_store import BLOCK_COLUMNS, CardError, comparison_from_table, comparison_sha256

SHA = ALHEMO["attachment_sha256"]
TITLE = ("公告暫予支付含concizumab成分藥品Alhemo solution for injection in pre-filled pen 15mg/1.5mL、60mg/1.5mL、"
         "150mg/1.5mL及300mg/3mL共4項目及其藥品給付規定，並修訂藥品給付規定通則等相關給付規定。")
NOTICE = {"reference_number": ALHEMO["reference_number"], "title": TITLE,
          "official_url": "https://www.nhi.gov.tw/ch/cp-20484-a6ca2-3258-1.html",
          "published_on": date(2026, 10, 14), "effective_on": date(2026, 11, 1), "source_artifact_sha256": SHA}


def comparison(table: dict | None = None, sha: str = SHA, extra: int = 1) -> dict:
    """The stored comparison of a table, as the public view serves it."""

    table = table or alhemo_table()
    built = comparison_from_table(table | {"sha256": sha, "extra_tables": list(range(extra))})
    return built | {"generator_version": "test/1"}


def stored_block(new: list[str], old: list[str], order: int = 0) -> dict:
    return comparison({"clauses": [{"new": new, "old": old}]})["blocks"][0] | {"block_order": order}


@pytest.mark.parametrize(("paragraph", "name"), [
    ("4.3.8.rADAMTS13 (如Adzynma)：(115/11/1)", "rADAMTS13 (如Adzynma)"),
    ("四、注射藥品之使用原則：", "注射藥品之使用原則"),
    ("4.2.血液代用製劑及血液成分製劑 blood substituents and blood components",
     "血液代用製劑及血液成分製劑 blood substituents and blood components"),
    ("4.2.9.Concizumab（如 Alhemo）：（115/11/1）", "Concizumab（如 Alhemo）"),
    ("2.6.1.全民健康保險降血脂藥物給付規定表（86/1/1、87/4/1、115/9/1）", "全民健康保險降血脂藥物給付規定表"),
    ("2.1.4.2.Rivaroxaban(如Xarelto)（101/1/1、115/10/1）", "Rivaroxaban(如Xarelto)"),
    ("9.20.Rituximab注射劑(如Mabthera，不同劑型之適用範圍需符合藥品許可證登載之適應症)：用於抗癌瘤部分(91/4/1)",
     "Rituximab注射劑(如Mabthera，不同劑型之適用範圍需符合藥品許可證登載之適應症)"),
    ("9.9.甲藥(如乙：丙)：(100/1/1)", "甲藥(如乙：丙)"),  # a colon inside a bracket is no cut
    ("9.9.甲　藥  丁：", "甲 藥 丁"),  # whitespace is collapsed, nothing is added
    ("9.9.(100/1/1)", ""),  # a heading that holds no name gives none
])
def test_the_clause_name_is_cut_from_the_stored_heading(paragraph, name):
    assert pg_render.heading_text(paragraph) == name


def test_the_name_of_a_deleted_clause_comes_from_the_original_heading_and_an_attachment_has_none():
    deleted = {"block_kind": "clause", "new_paragraphs": ["無"], "old_paragraphs": ["9.9.甲藥：(100/1/1)", "1.限用"]}
    assert pg_render.clause_name(deleted) == "甲藥"
    new_clause = {"block_kind": "clause", "new_paragraphs": ["9.9.乙藥：(115/1/1)"], "old_paragraphs": []}
    assert pg_render.clause_name(new_clause) == "乙藥"
    assert pg_render.clause_name({"block_kind": "attachment", "new_paragraphs": ["甲"], "old_paragraphs": []}) == ""


def test_fit_applies_its_two_fixed_rules_and_marks_every_cut():
    assert pg_render.fit("Fitusiran(如Qfitlia)", 28) == "Fitusiran(如Qfitlia)"
    # rule 1: a bracketed remark goes first
    assert pg_render.fit("雙特異性單株抗體藥物(如emicizumab)", 28) == "雙特異性單株抗體藥物⋯"
    assert pg_render.fit("Rituximab注射劑(如Mabthera，不同劑型之適用範圍需符合藥品許可證登載之適應症)", 28) == "Rituximab注射劑⋯"
    # rule 2: the last separator in the second half of the allowance, else the allowance itself
    assert pg_render.fit("血液代用製劑及血液成分製劑 blood substituents and blood components", 28) == "血液代用製劑及血液成分製劑⋯"
    assert pg_render.fit("甲" * 40, 10) == "甲甲甲甲⋯"
    assert pg_render.width(pg_render.fit("甲乙丙丁戊己庚辛壬癸 子丑寅卯辰巳午未申酉戌亥", 30)) <= 30


def test_the_notice_title_loses_only_its_frame_and_is_cut_by_width():
    assert pg_render.short_title("公告修訂8.1.3.高單位免疫球蛋白之藥品給付規定。") == "修訂8.1.3.高單位免疫球蛋白之藥品給付規定"
    short = pg_render.short_title(TITLE)
    assert short == "暫予支付含concizumab成分藥品Alhemo solution for injection in pre-filled pen 15mg/1.5mL⋯"
    assert pg_render.width(short) <= pg_render.TITLE_CAP and TITLE.removeprefix("公告").startswith(short[:-1])


def test_every_stored_block_rebuilds_from_its_paragraphs_to_its_stored_rows():
    stored = comparison()
    blocks = [pg_render.block_of(block) for block in stored["blocks"]]
    assert [block.clause_id for block in blocks] == ["0.4", "4.2", "4.2.9", "4.2.7", "4.2.8"]
    assert [(block.badge, block.adds, block.dels) for block in blocks] == [
        ("修訂", 1, 1), ("修訂", 2, 1), ("新增", 8, 0), ("修訂", 2, 2), ("修訂", 2, 2)]
    assert all(block.fn == "" and block.hunk_label == "" for block in blocks)


def test_a_stored_block_this_generator_would_lay_out_differently_is_refused():
    stored = copy.deepcopy(comparison()["blocks"][3])
    row = next(r for r in stored["rows"] if r["kind"] != "ctx" and any(piece["mark"] for piece in r["segments"]))
    row["segments"] = [{"text": "".join(piece["text"] for piece in row["segments"]), "mark": False}]
    with pytest.raises(CardError, match="stored rows differ"):
        pg_render.block_of(stored)
    short = copy.deepcopy(comparison()["blocks"][3])
    short["adds"] += 1
    with pytest.raises(CardError):  # the replay invariant names the count
        pg_render.block_of(short)
    odd = copy.deepcopy(comparison()["blocks"][3])
    odd["badge"] = "刪除"
    with pytest.raises(CardError, match="unknown badge"):
        pg_render.block_of(odd)


def test_the_card_set_of_a_notice_is_one_group_per_stored_block_in_block_order():
    cards = pg_render.card_set(NOTICE, comparison())
    assert [(d.slug, d.title) for d in cards.docs] == [
        ("general-4", "藥品給付規定/通則/0.4　注射藥品之使用原則"),
        ("clause-4-2", "藥品給付規定/4.2　血液代用製劑及血液成分製劑 blood substituents and blood components"),
        ("clause-4-2-9", "藥品給付規定/4.2.9　Concizumab（如 Alhemo）"),
        ("clause-4-2-7", "藥品給付規定/4.2.7　雙特異性單株抗體藥物(如emicizumab)"),
        ("clause-4-2-8", "藥品給付規定/4.2.8　Fitusiran(如Qfitlia)")]
    assert all(d.summary == [] and len(d.blocks) == 1 for d in cards.docs)
    first = cards.docs[0]
    assert first.kicker == "<b>健保審字第1150057388號</b>　115/10/14 公告　115/11/1 生效"
    assert first.source_lines == [
        "來源：健保署 健保審字第1150057388號 修訂對照表（115/10/14 公告）｜ODT sha256 ca54efe70f9b",
        "空白、全半形與自動編號差異不標示｜" + pg_render.DISCLAIMER]
    assert (first.legend_old, first.legend_new) == ("原給付規定", "修訂後給付規定")
    # the tables the comparison did not cover are noted once, on the last group
    assert [d.notes for d in cards.docs] == [[], [], [], [], ["本公告另有 1 個表格未逐字比對，見公告原文"]]
    assert cards.stat_rows == [
        {"id": "通則第4點", "label": "注射藥品之使用原則", "adds": 1, "dels": 1},
        {"id": "4.2", "label": "血液代用製劑及血液成分製劑⋯", "adds": 2, "dels": 1},
        {"id": "4.2.9", "label": "Concizumab（如 Alhemo）", "adds": 8, "dels": 0},
        {"id": "4.2.7", "label": "雙特異性單株抗體藥物⋯", "adds": 2, "dels": 2},
        {"id": "4.2.8", "label": "Fitusiran(如Qfitlia)", "adds": 2, "dels": 2}]
    assert cards.toc == [{"meta": row["id"]} for row in cards.stat_rows]


def test_the_cover_names_the_notice_by_the_record_and_escapes_what_it_inserts():
    cover = pg_render.card_set(NOTICE | {"reference_number": "字<號>&"}, comparison()).cover
    assert cover["cmd"] == "git show --stat 字<號>&" and cover["big"] == "健保藥品給付規定"
    assert cover["sub"] == "115/11/1 生效" and cover["hash"] == SHA[:12]
    assert cover["total_prefix"] == "<b>5</b> 項異動（新增 1、修訂 4）"
    assert "<b>字&lt;號&gt;&amp;</b>" in cover["dates_html"] and "字<號>" not in cover["dates_html"]
    assert "<b>2026-10-14</b>" in cover["dates_html"] and "<b>2026-11-01</b>" in cover["dates_html"]
    assert pg_render.short_title(TITLE) in cover["dates_html"]
    foot = pg_render.card_set(NOTICE, comparison()).cover_foot
    assert foot[-1] == pg_render.DISCLAIMER and "本公告另有 1 個表格未逐字比對，見公告原文" in foot
    assert "本公告另有" not in "".join(pg_render.card_set(NOTICE, comparison(extra=0)).cover_foot)


def test_the_lookup_page_meta_carries_the_full_title_and_no_unbreakable_token_wider_than_a_phone():
    meta = pg_render.card_set(NOTICE, comparison()).index_meta
    assert meta["title"] == "健保藥品給付規定 115/11/1 生效 diff（健保審字第1150057388號）"
    assert " ".join(TITLE.split()) in meta["subtitle"]
    assert meta["foot"][1] == "來源：健保署 健保審字第1150057388號 修訂對照表（115/10/14 公告），ODT sha256 ca54efe70f9b"
    # the page's CSS cannot wrap a run of ASCII: a full digest or an address would run past a 390 px screen
    runs = [run for line in meta["foot"] + [meta["subtitle"]] for run in re.findall(r"[\x21-\x7e]+", line)]
    assert max(len(run) for run in runs) <= 40


def test_a_row_that_holds_two_clauses_and_a_heading_only_original_column_carry_the_sites_notes():
    nested = stored_block(["9.5.Paclitaxel：(100/1/1)", "1.甲", "9.5.1.Paclitaxel乙：(100/1/1)", "1.乙"],
                          ["9.5.Paclitaxel：", "1.甲", "9.5.1.Paclitaxel乙：", "1.丙"])
    assert nested["clause_codes"] == ["9.5", "9.5.1"]
    assert pg_render.contained_in_note(nested) == "官方對照表此列為 9.5（含 9.5.1）"
    assert not pg_render.heading_only_old(nested)
    heading_only = stored_block(["4.2.血液代用製劑", "相關表單如下：", "◎附表四"], ["4.2.血液代用製劑"])
    assert pg_render.heading_only_old(heading_only) and pg_render.contained_in_note(heading_only) is None
    both = comparison({"clauses": [
        {"new": ["9.5.Paclitaxel：(100/1/1)", "1.甲", "9.5.1.Paclitaxel乙：(100/1/1)", "1.乙"],
         "old": ["9.5.Paclitaxel：", "1.甲", "9.5.1.Paclitaxel乙：", "1.丙"]},
        {"new": ["4.2.血液代用製劑", "相關表單如下：", "◎附表四"], "old": ["4.2.血液代用製劑"]}]}, extra=0)
    cards = pg_render.card_set(NOTICE, both)
    assert cards.docs[0].notes == ["官方對照表此列為 9.5（含 9.5.1）"]
    assert cards.docs[1].notes == [pg_render.HEADING_ONLY_NOTE]
    # a new clause (nothing in the original column) is not a heading-only original
    assert not pg_render.heading_only_old(comparison()["blocks"][2])


def test_an_attachment_block_is_a_group_of_its_own_named_by_its_position():
    table = alhemo_table() | {"extra_comparisons": [{"index": 1, "kind": "附表", "title": "附表十八之五 測試", "rows": [
        {"old": ["甲"], "new": ["乙"]}]}]}
    stored = comparison(table)
    assert [b["block_kind"] for b in stored["blocks"]][-1] == "attachment"
    cards = pg_render.card_set(NOTICE, stored)
    last = cards.docs[-1]
    assert (last.slug, last.title) == ("attachment-5", "藥品給付規定/附表十八之五")
    assert cards.stat_rows[-1]["id"] == "附表十八之五" and cards.stat_rows[-1]["label"] == ""
    assert pg_render.contained_in_note(stored["blocks"][-1]) is None


def test_the_caption_is_third_person_text_from_the_record_with_the_official_url_and_the_disclaimer():
    caption = pg_render.card_set(NOTICE, comparison()).caption
    assert caption.splitlines()[:2] == ["健保署公告 健保審字第1150057388號（115/10/14 公告，115/11/1 生效）", TITLE]
    assert "- 通則第4點 注射藥品之使用原則（修訂 +1 -1）" in caption
    assert "- 4.2.9 Concizumab（如 Alhemo）（新增 +8）" in caption
    assert "藥品給付規定異動 5 項（新增 1、修訂 4）：" in caption and "本公告另有 1 個表格未逐字比對，見公告原文。" in caption
    assert caption.splitlines()[-2:] == [f"官方公告：{NOTICE['official_url']}", "以健保署公告為準。"]
    assert caption.endswith("\n") and not any(word in caption for word in ("我", "—", "不是"))


def test_a_comparison_without_a_block_is_refused():
    with pytest.raises(CardError, match="no block"):
        pg_render.card_set(NOTICE, comparison() | {"blocks": []})


def test_two_blocks_may_not_share_a_file_name():
    stored = comparison()
    stored["blocks"][2] = stored["blocks"][1] | {"block_order": 2}
    with pytest.raises(CardError, match="share the file name"):
        pg_render.card_set(NOTICE, stored)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    """The two statements fetch_notice sends, answered from memory."""

    def __init__(self, notices, served):
        self.notices, self.served, self.sent = notices, served, []

    def execute(self, sql, params=()):
        self.sent.append(sql)
        if "notice_event" in sql:
            return Result(self.notices)
        return Result([{"source_artifact_sha256": SHA, "generator_version": "test/1",
                        "comparison_sha256": self.served["comparison_sha256"],
                        "extra_table_count": self.served["extra_table_count"],
                        **{column: block[column] for column in BLOCK_COLUMNS}} for block in self.served["blocks"]])


def test_the_notice_and_its_served_comparison_are_read_and_verified():
    stored = comparison()
    connection = FakeConnection([NOTICE], stored)
    notice, served = pg_render.fetch_notice(connection, SHA)
    assert notice == NOTICE and served["comparison_sha256"] == comparison_sha256(served["blocks"])
    assert "v_active_run" in connection.sent[0]  # only a notice of the active announced run
    with pytest.raises(CardError, match="no notice of the active announced run"):
        pg_render.fetch_notice(FakeConnection([], stored), SHA)
    with pytest.raises(CardError, match="2 notices"):
        pg_render.fetch_notice(FakeConnection([NOTICE, NOTICE], stored), SHA)
    with pytest.raises(CardError, match="serves no comparison"):
        pg_render.fetch_notice(FakeConnection([NOTICE], stored | {"blocks": []}), SHA)
    with pytest.raises(CardError, match="hash differs"):
        pg_render.fetch_notice(FakeConnection([NOTICE], stored | {"comparison_sha256": "0" * 64}), SHA)


def test_render_notice_writes_pngs_page_and_caption_and_leaves_nothing_behind_on_a_failed_check(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    seen = {}

    def fake_render_cards(pub, png_dir, docs, cover, stats, foot, index_meta, toc, rows_for):
        seen["pub"] = pub
        (pub / "cards").mkdir()
        (png_dir / "01-cover.png").write_bytes(b"png")
        (pub / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(pg_render, "render_cards", fake_render_cards)
    monkeypatch.setattr(pg_render, "check_geometry", lambda cards: [])
    connection = FakeConnection([NOTICE], comparison())
    out = tmp_path / "out"
    summary = pg_render.render_notice(connection, SHA, out)
    assert summary == {"reference_number": NOTICE["reference_number"], "source_artifact_sha256": SHA, "blocks": 5,
                       "pngs": ["01-cover.png"]}
    assert sorted(path.name for path in out.iterdir()) == ["01-cover.png", "caption.txt", "index.html"]
    assert not seen["pub"].exists()  # the scratch pages are removed

    monkeypatch.setattr(pg_render, "check_geometry", lambda cards: ["01-cover.html: the card overflows by 9px"])
    with pytest.raises(SystemExit, match="overflows by 9px"):
        pg_render.render_notice(connection, SHA, out)
    assert list(out.iterdir()) == []  # a half-rendered folder is never left for a consumer to take
    assert not seen["pub"].exists()


def test_the_commands_need_a_dsn_and_the_digest_of_one_official_file(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NHI_RULE_HISTORY_DSN", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    assert main(["render-pg", "--sha", SHA, "--out", str(tmp_path / "x")]) == 2
    assert main(["list-notices"]) == 2
    assert main(["render-pg", "--sha", "ABC", "--out", str(tmp_path / "x"), "--dsn", "service=none"]) == 2
    assert not (tmp_path / "x").exists()
    assert "64 lower-case hex digits" in capsys.readouterr().err
