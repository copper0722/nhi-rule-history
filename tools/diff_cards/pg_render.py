"""Share cards straight from the stored comparison (schema ``nhi_rule_history_diff_card``).

``render-pg`` turns the stored comparison of ONE notice, found by the SHA-256 of its official ODT, into
the card pages, the 1080x1350 PNGs, the lookup page and a caption.  There is no hand-made folder and no
written text: the editorial is a deterministic default built from the notice record and the stored blocks.

  cover     ``$ git show --stat <reference number>``, the series name, the effective date, the dates row
            (reference number, published, effective, the notice title cut by a fixed width rule), the first
            12 hex digits of the ODT SHA-256 and one stat row per block
  cards     one card group per stored block (clause or attachment, in ``block_order``): headline = path plus
            the clause's own name, cut from the stored heading paragraph by fixed rules; the file header
            with ``+N -M``, the ``@@`` hunk, rows, folds, legend, the source line and the disclaimer; no summary
  notes     the three the website prints for a block (a row that holds more than one clause, an original
            column that lists only the heading, tables the comparison did not cover)

Every block is rebuilt from its stored paragraphs by ``sources_nhi.clause_rows`` and must equal the stored
rows, so a generator that drifted without a version bump fails closed instead of drawing other rows.  Only
read-only PostgreSQL sessions are opened.  No model is involved.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .build import CARD_H, CARD_W, iso_to_roc, nhi_card_rows, render_cards
from .core import Block, verify_block
from .html import Doc
from .pg_store import (
    CardError,
    StoreError,
    _connect,
    check_replay,
    comparison_sha256,
    read_served,
    resolve_dsn,
    row_object,
)
from .sources_nhi import GENERAL_PRINCIPLE_PATH, clause_rows, none_cell

SERIES = "健保藥品給付規定"
LEGEND_OLD = "原給付規定"
LEGEND_NEW = "修訂後給付規定"
NUMBER_NOTE = "⋯ 省略未改動　數字為段落序"
DISCLAIMER = "以健保署公告為準；摘要為編者整理，非給付或醫療建議"
HEADING_ONLY_NOTE = "官方對照表「原給付規定」欄僅列標題；綠色段落為修訂後欄全文，未必都是新增文字"
ROW_FOOT = "行數以對照表段落計，修改一段記為 -1 +1；「(略)」為公告省略的未修訂部分。"
SHA_PREFIX = 12

# fixed width rules, in display units (a wide character counts 2, any other 1)
HEADLINE_CAP = 70  # the clause name after the path, on a card (two lines of the headline)
LABEL_CAP = 28  # the clause name on a cover stat row
TITLE_CAP = 96  # the notice title on the cover

NUMBER_PREFIX = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.|[一二三四五六七八九十]+、)\s*")
DATE_LIST = re.compile(r"[（(]\s*\d{2,3}\s*/\s*\d{1,2}\s*/\s*\d{1,2}")
OPENERS, CLOSERS = "（([【「", "）)]】」"
COLONS = "：:"
CUT_AT = "，、；;, 　"
STRIP = " 　，、；;,：:。"
CLAUSE_ID = re.compile(r"\d+(?:\.\d+)*")
SHA_RE = re.compile(r"[0-9a-f]{64}")

NOTICE_SQL = (
    "SELECT n.reference_number, n.title, n.official_url, n.published_on, n.effective_on, "
    "n.source_artifact_sha256 FROM nhi_rule_history_announced.notice_event AS n "
    "JOIN nhi_rule_history_announced.v_active_run AS r ON r.run_id = n.run_id"
)


# ---------------------------------------------------------------------------
# Pure part: fixed text rules


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def heading_text(paragraph: str) -> str:
    """The clause's own name: the heading paragraph without its number, cut at the first colon or date list
    that stands outside every bracket.  Nothing is added; a paragraph that holds no name gives ''."""

    text = NUMBER_PREFIX.sub("", " ".join(paragraph.split()), count=1)
    depth = 0
    for index, ch in enumerate(text):
        if ch in OPENERS:
            if depth == 0 and DATE_LIST.match(text, index):
                return text[:index].strip(STRIP)
            depth += 1
        elif ch in CLOSERS:
            depth = max(0, depth - 1)
        elif depth == 0 and ch in COLONS:
            return text[:index].strip(STRIP)
    return text.strip(STRIP)


def fit(text: str, cap: int) -> str:
    """``text`` within ``cap`` display units by two fixed rules, the cut marked with ⋯: drop a bracketed
    remark, else cut at the last separator in the second half of the allowance (or at the allowance)."""

    if width(text) <= cap:
        return text
    head = re.split(r"[（(]", text, maxsplit=1)[0].strip(STRIP)
    if head and head != text and width(head) + 1 <= cap:
        return head + "⋯"
    used, end = 0, 0
    for index, ch in enumerate(text):
        step = width(ch)
        if used + step > cap - 1:
            break
        used, end = used + step, index + 1
    cut = max((i for i in range(end // 2, end) if text[i] in CUT_AT), default=-1)
    return text[: cut if cut > 0 else end].strip(STRIP) + "⋯"


def short_title(title: str) -> str:
    """The notice title for the cover: without the leading 公告 and the closing 。, then ``fit``."""

    text = " ".join(title.split())
    text = text.removeprefix("公告").removesuffix("。")
    return fit(text, TITLE_CAP)


def clause_name(stored: Mapping[str, Any]) -> str:
    """The clause's own name from the stored heading paragraph (the revised column, the original one when
    the revised cell reads 無), '' for an attachment block, which has no heading."""

    if stored["block_kind"] != "clause":
        return ""
    new, old = list(stored["new_paragraphs"]), list(stored["old_paragraphs"])
    if not new or none_cell(new):
        heading = old[0] if old else ""
    else:
        heading = new[0]
    return heading_text(heading)


def is_general_principle(stored: Mapping[str, Any]) -> bool:
    return str(stored["path"]).startswith(f"{GENERAL_PRINCIPLE_PATH}/")


def slug_of(stored: Mapping[str, Any]) -> str:
    """A file and anchor name that is safe for any clause id (an attachment id is Chinese text)."""

    cid = stored["clause_id"]
    if stored["block_kind"] == "clause" and CLAUSE_ID.fullmatch(cid):
        if is_general_principle(stored):
            return "general-" + cid.split(".", 1)[1]
        return "clause-" + cid.replace(".", "-")
    return f"attachment-{stored['block_order']}"


def stat_id(stored: Mapping[str, Any]) -> str:
    """The cover's id column: a 通則 point reads 通則第N點, every other block its own id."""

    if is_general_principle(stored):
        return f"通則第{stored['clause_id'].split('.', 1)[1]}點"
    return str(stored["clause_id"])


def headline(stored: Mapping[str, Any]) -> str:
    name = fit(clause_name(stored), HEADLINE_CAP)
    return f"{stored['path']}　{name}" if name else str(stored["path"])


def contained_in_note(stored: Mapping[str, Any]) -> str | None:
    """A clause row that holds more than one clause (the 9.5 row holds 9.5.1); never on an attachment."""

    codes = list(stored["clause_codes"])
    others = [code for code in codes if code != stored["clause_id"]]
    if stored["block_kind"] != "clause" or len(codes) < 2 or not others:
        return None
    return f"官方對照表此列為 {stored['clause_id']}（含 {'、'.join(others)}）"


def heading_only_old(stored: Mapping[str, Any]) -> bool:
    """The original column lists only the heading, so the green paragraphs are the revised column's text."""

    return (stored["badge"] == "修訂" and len(stored["old_paragraphs"]) == 1 and stored["dels"] == 0
            and stored["adds"] > 0)


def extra_tables_note(count: int) -> str:
    return f"本公告另有 {count} 個表格未逐字比對，見公告原文"


# ---------------------------------------------------------------------------
# Pure part: stored blocks -> card documents


@dataclass
class CardSet:
    docs: list[Doc]
    cover: dict[str, str]
    stat_rows: list[dict[str, Any]]
    cover_foot: list[str]
    index_meta: dict[str, Any]
    toc: list[dict[str, str]]
    caption: str
    blocks: list[Block] = field(default_factory=list)


def block_of(stored: Mapping[str, Any]) -> Block:
    """The card block of one stored block object, rebuilt from its paragraphs.

    The rows must equal the stored rows and the stored counts and hunk labels must agree: a stored block
    this generator would lay out differently is refused, never drawn."""

    path = str(stored.get("path", "?"))
    check_replay(stored)
    old, new = list(stored["old_paragraphs"]), list(stored["new_paragraphs"])
    rows = clause_rows(old, new)
    if [row_object(row) for row in rows] != list(stored["rows"]):
        raise CardError(f"{path}: the stored rows differ from the rows this generator lays out "
                        "(generator changed without a version bump)")
    if stored["badge"] not in ("修訂", "新增"):
        raise CardError(f"{path}: unknown badge {stored['badge']!r}")
    block = Block(
        path=path, badge=stored["badge"], badge_kind="a" if stored["badge"] == "新增" else "m", fn="", rows=rows,
        adds=sum(1 for row in rows if row.kind == "add"), dels=sum(1 for row in rows if row.kind == "del"),
        hunk_old=stored["hunk_old"], hunk_new=stored["hunk_new"], clause_id=stored["clause_id"])
    if (block.adds, block.dels) != (stored["adds"], stored["dels"]):
        raise CardError(f"{path}: the stored counts differ from the rebuilt rows")
    try:
        verify_block(block, old, new)
    except ValueError as exc:
        raise CardError(str(exc)) from None
    return block


def iso(value: Any) -> str:
    return date.fromisoformat(str(value)).isoformat()


def card_set(notice: Mapping[str, Any], comparison: Mapping[str, Any]) -> CardSet:
    """The default editorial of one notice over its stored comparison."""

    ref, sha = notice["reference_number"], notice["source_artifact_sha256"]
    published, effective = iso(notice["published_on"]), iso(notice["effective_on"])
    roc_published, roc_effective = iso_to_roc(published), iso_to_roc(effective)
    stored_blocks = sorted(comparison["blocks"], key=lambda block: block["block_order"])
    if not stored_blocks:
        raise CardError("the comparison holds no block")
    extra = int(comparison["extra_table_count"])
    prefix = sha[:SHA_PREFIX]
    kicker = f"<b>{html.escape(ref, quote=False)}</b>　{roc_published} 公告　{roc_effective} 生效"
    source = f"來源：健保署 {ref} 修訂對照表（{roc_published} 公告）｜ODT sha256 {prefix}"
    blocks, docs, stats, toc, slugs = [], [], [], [], set()
    for position, stored in enumerate(stored_blocks):
        block = block_of(stored)
        slug = slug_of(stored)
        if slug in slugs:
            raise CardError(f"{block.path}: two blocks share the file name {slug!r}")
        slugs.add(slug)
        notes = [note for note in (contained_in_note(stored),) if note]
        if heading_only_old(stored):
            notes.append(HEADING_ONLY_NOTE)
        if extra and position == len(stored_blocks) - 1:
            notes.append(extra_tables_note(extra))
        docs.append(Doc(
            slug=slug, title=headline(stored), kicker=kicker, summary=[], blocks=[block],
            legend_old=LEGEND_OLD, legend_new=LEGEND_NEW, number_note=NUMBER_NOTE,
            source_lines=[source, "空白、全半形與自動編號差異不標示｜" + DISCLAIMER],
            notes=notes))
        blocks.append(block)
        stats.append({"id": stat_id(stored), "label": fit(clause_name(stored), LABEL_CAP),
                      "adds": block.adds, "dels": block.dels})
        toc.append({"meta": stat_id(stored)})
    n_new = sum(1 for block in blocks if block.badge_kind == "a")
    n_mod = len(blocks) - n_new
    esc = html.escape
    cover = {
        "cmd": f"git show --stat {ref}",
        "big": SERIES,
        "sub": f"{roc_effective} 生效",
        "dates_html": (f"<span>公告 <b>{published}</b></span><span>生效 <b>{effective}</b></span>"
                       f"<span>字號 <b>{esc(ref, quote=False)}</b></span>"
                       f'<span style="flex-basis:100%">{esc(short_title(notice["title"]), quote=False)}</span>'),
        "hash": prefix,
        "hash_note": "修訂對照表 ODT sha256 前 12 碼",
        "total_prefix": f"<b>{len(blocks)}</b> 項異動（新增 {n_new}、修訂 {n_mod}）",
    }
    foot = [ROW_FOOT, source]
    if extra:
        foot.append(extra_tables_note(extra))
    foot.append(DISCLAIMER)
    index_meta = {
        "title": f"{SERIES} {roc_effective} 生效 diff（{ref}）",
        "subtitle": f"{' '.join(notice['title'].split())}｜{roc_published} 公告｜官方修訂對照表逐段比對。"
                    "點灰色段落可展開全文。",
        "foot": ["紅底為原給付規定刪改文字，綠底為修訂後新增文字；空白與自動編號差異不標示。",
                 f"來源：健保署 {ref} 修訂對照表（{roc_published} 公告），ODT sha256 {prefix}",
                 DISCLAIMER],
    }
    return CardSet(docs, cover, stats, foot, index_meta, toc, caption_of(notice, stored_blocks, blocks, extra), blocks)


def caption_of(notice: Mapping[str, Any], stored_blocks: Sequence[Mapping[str, Any]], blocks: Sequence[Block],
               extra: int) -> str:
    """The share caption: deterministic zh-TW text from the record, third person, no model."""

    published, effective = iso_to_roc(iso(notice["published_on"])), iso_to_roc(iso(notice["effective_on"]))
    n_new = sum(1 for block in blocks if block.badge_kind == "a")
    lines = [f"健保署公告 {notice['reference_number']}（{published} 公告，{effective} 生效）",
             " ".join(notice["title"].split()), "",
             f"藥品給付規定異動 {len(blocks)} 項（新增 {n_new}、修訂 {len(blocks) - n_new}）："]
    for stored, block in zip(stored_blocks, blocks, strict=True):
        name = fit(clause_name(stored), HEADLINE_CAP)
        label = f"{stat_id(stored)} {name}".strip()
        size = f"+{block.adds} -{block.dels}" if block.dels else f"+{block.adds}"
        lines.append(f"- {label}（{block.badge} {size}）")
    if extra:
        lines.append(extra_tables_note(extra) + "。")
    lines += ["", "圖卡依官方修訂對照表逐段比對：紅色為原給付規定，綠色為修訂後給付規定。",
              f"官方公告：{notice['official_url']}", "以健保署公告為準。"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Rendered pages: the checks the shared tail does not make


GEOMETRY_JS = """() => {
  const q = s => document.querySelector(s);
  const card = q('.card'), foot = q('.foot'), h1 = q('h1');
  const line = h1 ? parseFloat(getComputedStyle(h1).lineHeight) : 0;
  return {cover: !!q('.cover'),
          over: card ? card.scrollHeight - card.clientHeight : 0,
          footBottom: foot ? foot.getBoundingClientRect().bottom : 0,
          h1Lines: h1 && line ? Math.round(h1.getBoundingClientRect().height / line) : 0};
}"""
FOOT_LIMIT = CARD_H - 30 + 1  # the card's bottom padding is 30px
CARD_H1_LINES = 2


def check_geometry(cards_dir: Path) -> list[str]:
    """Measure every card page in the browser: the card must not overflow (the cover has no ``.main``, so
    the shared overflow check never sees it), the footer must end inside the card, the cover title must be
    one line and a card headline at most two."""

    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
        for path in sorted(cards_dir.glob("*.html")):
            page.goto(path.resolve().as_uri())
            page.wait_for_timeout(300)
            got = page.evaluate(GEOMETRY_JS)
            if got["over"] > 1:
                problems.append(f"{path.name}: the card overflows by {got['over']}px")
            if got["footBottom"] > FOOT_LIMIT:
                problems.append(f"{path.name}: the footer ends at {got['footBottom']:.0f}px, past the card")
            if got["h1Lines"] > (1 if got["cover"] else CARD_H1_LINES):
                problems.append(f"{path.name}: the title takes {got['h1Lines']} lines")
        browser.close()
    return problems


# ---------------------------------------------------------------------------
# I/O part: read the notice, render, list


def fetch_notice(connection: Any, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The notice of the active announced run that carries this ODT and the comparison the public view
    serves for it.  The served rows are verified as the loader's read-back does: replay invariant of every
    block and the hash recomputed from what PostgreSQL returned."""

    notices = connection.execute(NOTICE_SQL + " WHERE n.source_artifact_sha256 = %s", (sha,)).fetchall()
    if not notices:
        raise CardError(f"no notice of the active announced run carries ODT {sha[:SHA_PREFIX]}")
    if len(notices) > 1:
        raise CardError(f"{len(notices)} notices of the active announced run carry ODT {sha[:SHA_PREFIX]}")
    served = read_served(connection, [sha]).get(sha)
    if served is None:
        raise CardError(f"the public view serves no comparison for ODT {sha[:SHA_PREFIX]}")
    if comparison_sha256(served["blocks"]) != served["comparison_sha256"]:
        raise CardError("the stored comparison hash differs from the hash of its own rows")
    for block in served["blocks"]:
        check_replay(block)
    return dict(notices[0]), served


def render_notice(connection: Any, sha: str, out: Path) -> dict[str, Any]:
    """Render one notice into ``out``: PNGs, ``index.html`` and ``caption.txt``.  Card pages are built in a
    scratch directory (under TMPDIR) that is removed afterwards; a failed run leaves nothing in ``out``."""

    notice, comparison = fetch_notice(connection, sha)
    cards = card_set(notice, comparison)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="nhi-diff-card-", dir=os.environ.get("TMPDIR") or None))
    generated = [out / "index.html", out / "caption.txt"]
    try:
        render_cards(work, out, cards.docs, cards.cover, cards.stat_rows, cards.cover_foot, cards.index_meta,
                     cards.toc, nhi_card_rows)
        problems = check_geometry(work / "cards")
        if problems:
            raise SystemExit("geometry:\n" + "\n".join(problems))
        shutil.copyfile(work / "index.html", out / "index.html")
        (out / "caption.txt").write_text(cards.caption, encoding="utf-8")
    except BaseException:
        for leftover in [*out.glob("*.png"), *generated]:
            leftover.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"reference_number": notice["reference_number"], "source_artifact_sha256": sha,
            "blocks": len(cards.blocks), "pngs": sorted(path.name for path in out.glob("*.png"))}


def list_notices(connection: Any) -> list[dict[str, Any]]:
    """Notices of the active announced run that have a comparison in the public view, newest first."""

    rows = connection.execute(
        "SELECT n.reference_number, n.title, n.official_url, n.published_on, n.effective_on, "
        "n.source_artifact_sha256, v.generator_version, v.comparison_sha256, count(*) AS block_count "
        "FROM nhi_rule_history_announced.notice_event AS n "
        "JOIN nhi_rule_history_announced.v_active_run AS r ON r.run_id = n.run_id "
        "JOIN nhi_rule_history_diff_card.v_public_comparison_block AS v "
        "ON v.source_artifact_sha256 = n.source_artifact_sha256 "
        "GROUP BY n.reference_number, n.title, n.official_url, n.published_on, n.effective_on, "
        "n.source_artifact_sha256, v.generator_version, v.comparison_sha256 "
        "ORDER BY n.published_on DESC, n.reference_number").fetchall()
    return [dict(row, published_on=iso(row["published_on"]), effective_on=iso(row["effective_on"]))
            for row in rows]


# ---------------------------------------------------------------------------
# CLI entries


def _dsn_or_none(explicit: str | None, command: str) -> str | None:
    dsn = resolve_dsn(explicit)
    if not dsn:
        print(f"{command}: no DSN: pass --dsn or set NHI_RULE_HISTORY_DSN (or PGSERVICE)", file=sys.stderr)
    return dsn


def render_pg(sha: str, out: Path, dsn: str | None) -> int:
    """``build.py render-pg``: 0 = rendered, 1 = refused or failed, 2 = no DSN or a bad SHA."""

    if not SHA_RE.fullmatch(sha):
        print("render-pg: --sha must be 64 lower-case hex digits", file=sys.stderr)
        return 2
    dsn = _dsn_or_none(dsn, "render-pg")
    if not dsn:
        return 2
    try:
        connection = _connect(dsn, read_only=True)
        try:
            summary = render_notice(connection, sha, out)
        finally:
            connection.close()
    except (CardError, StoreError) as exc:
        print(f"render-pg: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any other failure is reported as one line
        print(f"render-pg: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def list_pg(dsn: str | None) -> int:
    """``build.py list-notices``: the renderable notices as one JSON array on stdout."""

    dsn = _dsn_or_none(dsn, "list-notices")
    if not dsn:
        return 2
    try:
        connection = _connect(dsn, read_only=True)
        try:
            notices = list_notices(connection)
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001 - CLI boundary, as in render_pg
        print(f"list-notices: {exc}" if isinstance(exc, StoreError) else f"list-notices: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    print(json.dumps(notices, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
