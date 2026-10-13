"""HTML for comparison cards: fixed 1080x1350 card pages and a responsive lookup page."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .core import Block, Row

TOKENS = """
:root {
  --bg: #0d1117; --head: #161b22; --border: #30363d;
  --fg: #e6edf3; --strong: #f0f6fc; --muted: #9198a1; --subtle: #6e7681;
  --del-line: rgba(248,81,73,.13); --del-gut: rgba(248,81,73,.24); --del-word: rgba(248,81,73,.46); --del-fg: #ff7b72;
  --add-line: rgba(46,160,67,.15); --add-gut: rgba(46,160,67,.30); --add-word: rgba(46,160,67,.50); --add-fg: #56d364;
  --hunk-line: rgba(56,139,253,.10); --hunk-gut: rgba(56,139,253,.20);
  --amber: #d29922; --r: 12px;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
  font-family: "Noto Sans Mono CJK TC", "Noto Sans CJK TC", "PingFang TC", "Menlo", monospace; }
.old { color: var(--del-fg); text-decoration: line-through; text-decoration-thickness: 2px; }
.new { color: var(--add-fg); font-weight: 700; }
.p { color: var(--add-fg); } .d { color: var(--del-fg); }
.file { border: 1px solid var(--border); border-radius: var(--r); overflow: hidden; }
.fhead { background: var(--head); border-bottom: 1px solid var(--border); display: flex; align-items: center; }
.fhead .path { font-weight: 700; color: var(--strong); }
.fhead .cont { color: var(--muted); font-weight: 400; }
.badge { line-height: 1; border-radius: var(--r); border: 1px solid; }
.badge.m { color: var(--amber); border-color: rgba(210,153,34,.55); }
.badge.a { color: var(--add-fg); border-color: rgba(63,185,80,.55); }
.fhead .stat { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.boxes { display: inline-flex; gap: 3px; margin-left: 6px; }
.boxes i { border-radius: 2px; background: var(--border); }
.boxes i.p { background: #3fb950; } .boxes i.d { background: #f85149; }
.r { display: grid; }
.r .n { color: var(--subtle); text-align: right; }
.r .s { text-align: center; }
.r .t { line-break: strict; overflow-wrap: anywhere; min-width: 0; }
.r.del { background: var(--del-line); } .r.del .n { background: var(--del-gut); } .r.del .s { color: var(--del-fg); }
.r.add { background: var(--add-line); } .r.add .n { background: var(--add-gut); } .r.add .s { color: var(--add-fg); }
.r.ctx .t, .r.ctxc .t { color: var(--muted); }
.r.ctxc .t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.r.del mark, .r.add mark { color: #fff; border-radius: 3px; -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.r.del mark { background: var(--del-word); } .r.add mark { background: var(--add-word); }
.r.hunk, .r.fold, .r.xref { background: var(--hunk-line); }
.r.hunk .g, .r.fold .g, .r.xref .g { grid-column: 1 / span 3; background: var(--hunk-gut); color: var(--muted); text-align: center; }
.r.hunk .t, .r.fold .t, .r.xref .t { color: var(--muted); }
.r.hunk .fn { color: var(--fg); margin-left: 10px; }
.r .el { color: var(--subtle); margin: 0 .15em; }
.summary { display: grid; grid-template-columns: max-content 1fr; }
.summary .k { color: var(--muted); }
.foot { border-top: 1px solid var(--border); color: var(--muted); }
.foot .sw { display: inline-block; padding: 0 5px; border-radius: 3px; color: #fff; }
.foot .sw.d { background: var(--del-word); } .foot .sw.p { background: var(--add-word); }
.notes { color: var(--muted); }
.notes b { color: var(--amber); font-weight: 400; }
"""

CARD_CSS = """
@page { size: 1080px 1350px; margin: 0; }
body { width: 1080px; height: 1350px; overflow: hidden; }
.card { width: 1080px; height: 1350px; padding: 48px 32px 30px; display: flex; flex-direction: column; }
.kicker { font-size: 22px; color: var(--muted); margin-bottom: 10px; }
.kicker b { color: var(--amber); font-weight: 400; }
h1 { margin: 0; font-size: 46px; line-height: 1.25; font-weight: 700; color: var(--strong); }
.contline { font-size: 26px; color: var(--muted); }
.contline b { color: var(--strong); }
.summary { margin-top: 16px; column-gap: 22px; row-gap: 2px; font-size: 27px; line-height: 1.5; }
.main { flex: 1; min-height: 0; margin-top: 22px; display: flex; flex-direction: column; gap: 18px; overflow: hidden; }
.file { flex: none; }
.fhead { padding: 10px 18px; gap: 14px; font-size: 25px; }
.badge { font-size: 19px; padding: 5px 10px; }
.fhead .stat { font-size: 22px; }
.boxes i { width: 12px; height: 12px; }
.rows { font-size: 28px; line-height: 40px; }
.r { grid-template-columns: 38px 38px 26px 1fr; }
.r .n { font-size: 20px; line-height: 40px; padding-right: 6px; }
.r .t { padding: 0 14px 0 4px; }
.r.hunk .g, .r.fold .g, .r.xref .g { font-size: 22px; line-height: 40px; }
.r.hunk .t, .r.fold .t, .r.xref .t { font-size: 22px; }
.notes { margin-top: 14px; font-size: 21px; line-height: 1.5; }
.foot { margin-top: 18px; padding-top: 12px; font-size: 20px; line-height: 1.5; }
/* cover */
.cover .cmd { font-size: 24px; color: var(--muted); }
.cover .cmd .ps { color: var(--add-fg); }
.cover h1.big { margin: 24px 0 0; font-size: 84px; line-height: 1.1; letter-spacing: .02em; }
.cover .sub { margin-top: 12px; font-size: 42px; font-weight: 700; color: var(--strong); }
.cover .sub .tag { color: var(--add-fg); }
.cover .dates { margin-top: 18px; font-size: 27px; color: var(--muted); display: flex; gap: 26px; flex-wrap: wrap; }
.cover .dates b { color: var(--fg); font-weight: 700; }
.cover .file { margin-top: 26px; }
.cover .fhead .hash { color: var(--amber); font-weight: 700; }
.cover .fhead .note { color: var(--muted); font-size: 21px; }
.cover .cbody { padding: 16px 22px 18px; }
.cover .stats { display: grid; grid-template-columns: max-content max-content max-content max-content 1fr;
  column-gap: 14px; font-size: 26px; line-height: 46px; }
.cover .stats .a { color: var(--strong); }
.cover .stats .sep { color: var(--subtle); }
.cover .stats .cnt { color: var(--muted); text-align: right; }
.cover .stats .bar { white-space: pre; }
.cover .total { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border); font-size: 25px; color: var(--muted); }
.cover .total b { color: var(--fg); }
"""

INDEX_CSS = """
body { font-size: 15px; line-height: 1.55; }
main { max-width: 1080px; margin: 0 auto; padding: 24px 16px 64px; }
header.top h1 { font-size: 28px; margin: 0 0 6px; color: var(--strong); }
header.top .sub { color: var(--muted); }
nav.toc { margin: 18px 0 8px; display: grid; gap: 4px; }
nav.toc a { color: #58a6ff; text-decoration: none; }
nav.toc .cnt { color: var(--muted); }
section.doc { margin-top: 36px; }
section.doc h2 { font-size: 21px; margin: 0 0 4px; color: var(--strong); }
.kicker { color: var(--muted); font-size: 13px; }
.kicker b { color: var(--amber); font-weight: 400; }
.summary { margin: 10px 0 14px; column-gap: 14px; row-gap: 2px; }
.file { margin-top: 14px; }
.fhead { padding: 8px 12px; gap: 10px; font-size: 14px; flex-wrap: wrap; }
.badge { font-size: 12px; padding: 3px 7px; }
.boxes i { width: 9px; height: 9px; }
.rows { font-size: 14.5px; line-height: 1.6; }
.r { grid-template-columns: 30px 30px 18px 1fr; }
.r .n { font-size: 12px; padding: 2px 4px 0 0; }
.r .t { padding: 0 10px 0 2px; }
.r.ctxc { cursor: pointer; }
.r.ctxc.open .t { white-space: normal; }
.r.hunk .g, .r.fold .g, .r.xref .g { font-size: 12px; }
.r.hunk .t { font-size: 13px; }
.notes { font-size: 13px; margin-top: 8px; }
.foot { margin-top: 28px; padding-top: 12px; font-size: 13px; }
@media (max-width: 560px) { .r { grid-template-columns: 22px 22px 14px 1fr; } .rows { font-size: 14px; } }
"""

INDEX_JS = """document.addEventListener('click', e => { const r = e.target.closest('.r.ctxc'); if (r) r.classList.toggle('open'); });"""


@dataclass
class Doc:
    slug: str
    title: str
    kicker: str
    summary: list[tuple[str, str]]
    blocks: list[Block]
    legend_old: str
    legend_new: str
    number_note: str
    source_lines: list[str]
    notes: list[str] = field(default_factory=list)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def summary_html(s: str) -> str:
    s = esc(s)
    s = re.sub(r"~~(.+?)~~", r'<span class="old">\1</span>', s)
    return re.sub(r"\*\*(.+?)\*\*", r'<span class="new">\1</span>', s)


def marked(text: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for a, b in sorted(spans):
        out.append(esc(text[pos:a]))
        out.append(f"<mark>{esc(text[a:b])}</mark>")
        pos = b
    out.append(esc(text[pos:]))
    return "".join(out)


def boxes(add: int, dele: int) -> str:
    tot = add + dele
    g = max(1, round(5 * add / tot)) if add else 0
    r = max(1, round(5 * dele / tot)) if dele else 0
    while g + r > 5:
        if g >= r:
            g -= 1
        else:
            r -= 1
    return "".join(['<i class="p"></i>'] * g + ['<i class="d"></i>'] * r + ["<i></i>"] * (5 - g - r))


def row_html(row: Row, uid: str = "") -> str:
    attr = f' data-u="{uid}"' if uid else ""
    if row.kind in ("fold", "xref"):
        return f'<div class="r {row.kind}"{attr}><span class="g">⋯</span><span class="t">{esc(row.text)}</span></div>'
    sign = {"del": "-", "add": "+"}.get(row.kind, "")
    style = ""
    if row.indent_em:
        style = f' style="padding-left:calc(4px + {row.indent_em:g}em);text-indent:-{row.indent_em:g}em"'
    if row.keep:
        pieces = []
        if row.keep[0][0] > 0:
            pieces.append('<span class="el">⋯</span>')
        for k, (a, b) in enumerate(row.keep):
            if k:
                pieces.append('<span class="el">⋯</span>')
            local = [(max(x, a) - a, min(y, b) - a) for x, y in row.spans if x < b and y > a]
            pieces.append(marked(row.text[a:b], local))
        if row.keep[-1][1] < len(row.text):
            pieces.append('<span class="el">⋯</span>')
        body = "".join(pieces)
    else:
        body = marked(row.text, row.spans) if row.spans else esc(row.text)
    return (f'<div class="r {row.kind}"{attr}><span class="n">{row.old_no}</span><span class="n">{row.new_no}</span>'
            f'<span class="s">{sign}</span><span class="t"{style}>{body}</span></div>')


def hunk_html(block: Block, uid: str = "") -> str:
    attr = f' data-u="{uid}"' if uid else ""
    fn = f'<span class="fn">{esc(block.fn)}</span>' if block.fn else ""
    span = esc(block.hunk_label) if block.hunk_label else f"-{esc(block.hunk_old)} +{esc(block.hunk_new)}"
    return (f'<div class="r hunk"{attr}><span class="g">@@</span><span class="t">'
            f'{span} @@{fn}</span></div>')


def fhead_html(block: Block, cont: bool = False, uid: str = "") -> str:
    attr = f' data-u="{uid}"' if uid else ""
    c = '<span class="cont">（續）</span>' if cont else ""
    return (f'<div class="fhead"{attr}><span class="path">{esc(block.path)}</span>{c}'
            f'<span class="badge {block.badge_kind}">{esc(block.badge)}</span>'
            f'<span class="stat"><span class="p">+{block.adds}</span><span class="d">-{block.dels}</span>'
            f'<span class="boxes">{boxes(block.adds, block.dels)}</span></span></div>')


def foot_html(doc: Doc) -> str:
    legend = (f'<div><b class="d">-</b> {esc(doc.legend_old)}　<b class="p">+</b> {esc(doc.legend_new)}　'
              f'<span class="sw d">紅</span><span class="sw p">綠</span> 改動文字　{esc(doc.number_note)}</div>')
    return '<div class="foot">' + legend + "".join(f"<div>{esc(x)}</div>" for x in doc.source_lines) + "</div>"


def page(title: str, css: str, inner: str, js: str = "") -> str:
    script = f"<script>{js}</script>" if js else ""
    return (f'<!doctype html><html lang="zh-Hant-TW"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)}</title><style>{TOKENS}{css}</style></head><body>{inner}{script}</body></html>")


def head_first_html(doc: Doc) -> str:
    summ = "".join(f'<div class="k">{esc(k)}</div><div>{summary_html(v)}</div>' for k, v in doc.summary)
    return (f'<div class="kicker">{doc.kicker}</div><h1>{esc(doc.title)}</h1>'
            f'<div class="summary">{summ}</div>')


def head_cont_html(doc: Doc) -> str:
    return f'<div class="contline"><b>{esc(doc.title)}</b>（續）</div>'


def notes_html(doc: Doc) -> str:
    if not doc.notes:
        return ""
    return '<div class="notes">' + "".join(f"<div><b>※</b> {esc(n)}</div>" for n in doc.notes) + "</div>"


def card_page_html(doc: Doc, segments: list[dict], first: bool, last: bool) -> str:
    """segments: [{'block': Block, 'rows': [Row], 'cont': bool, 'hunk': bool}]"""
    parts = []
    for seg in segments:
        rows = []
        if seg.get("xref_top"):
            rows.append(row_html(Row("xref", seg["xref_top"])))
        if seg["hunk"]:
            rows.append(hunk_html(seg["block"]))
        rows += [row_html(r) for r in seg["rows"]]
        if seg.get("xref_bottom"):
            rows.append(row_html(Row("xref", seg["xref_bottom"])))
        parts.append(f'<section class="file">{fhead_html(seg["block"], seg["cont"])}<div class="rows">{"".join(rows)}</div></section>')
    head = head_first_html(doc) if first else head_cont_html(doc)
    notes = notes_html(doc) if last else ""
    inner = f'<div class="card">{head}<div class="main">{"".join(parts)}{notes}</div>{foot_html(doc)}</div>'
    return page(doc.title, CARD_CSS, inner)


def measure_html(doc: Doc, card_rows: dict[int, list[Row]]) -> str:
    """All measurable units at card width: heads, per-block fhead/hunk/rows, xref, notes, foot."""
    parts = ['<div class="card" style="height:auto">',
             f'<div data-u="head1">{head_first_html(doc)}</div>',
             f'<div data-u="head2">{head_cont_html(doc)}</div>',
             '<div class="main" style="overflow:visible">']
    for bi, block in enumerate(doc.blocks):
        rows = [hunk_html(block, f"b{bi}-hunk"), row_html(Row("xref", "接下一張"), f"b{bi}-xref")]
        rows += [row_html(r, f"b{bi}-r{ri}") for ri, r in enumerate(card_rows[bi])]
        parts.append(f'<section class="file">{fhead_html(block, False, f"b{bi}-fhead")}'
                     f'<div class="rows">{"".join(rows)}</div></section>')
    parts.append(f'<div data-u="notes">{notes_html(doc)}</div></div>')
    parts.append(f'<div data-u="foot">{foot_html(doc)}</div></div>')
    css = CARD_CSS.replace("body { width: 1080px; height: 1350px; overflow: hidden; }", "body { width: 1080px; }")
    return page(doc.title, css, "".join(parts))


def cover_html(meta: dict, stat_rows: list[dict], foot_lines: list[str], bar_max: int = 22) -> str:
    rows = []
    top = max((s["adds"] + s["dels"]) for s in stat_rows)
    scale = min(1.0, bar_max / top) if top else 1.0
    for s in stat_rows:
        plus = max(1, round(s["adds"] * scale)) if s["adds"] else 0
        minus = max(1, round(s["dels"] * scale)) if s["dels"] else 0
        bar = f'<span class="p">{"+" * plus}</span><span class="d">{"-" * minus}</span>'
        rows.append(f'<span class="a">{esc(s["id"])}</span><span class="lab">{esc(s["label"])}</span>'
                    f'<span class="sep">|</span><span class="cnt">{s["adds"] + s["dels"]}</span><span class="bar">{bar}</span>')
    total_add = sum(s["adds"] for s in stat_rows)
    total_del = sum(s["dels"] for s in stat_rows)
    inner = f"""<div class="card cover">
<div class="cmd"><span class="ps">$</span> {esc(meta['cmd'])}</div>
<h1 class="big">{esc(meta['big'])}</h1>
<div class="sub">{esc(meta['sub'])} <span class="tag">diff</span></div>
<div class="dates">{meta['dates_html']}</div>
<section class="file"><div class="fhead" style="padding:10px 18px;gap:14px;font-size:25px"><span class="hash">{esc(meta['hash'])}</span><span class="note">{esc(meta['hash_note'])}</span></div>
<div class="cbody"><div class="stats">{"".join(rows)}</div>
<div class="total">{meta['total_prefix']}，新增 <b class="p">{total_add}</b> 行(+)，刪除 <b class="d">{total_del}</b> 行(-)</div>
</div></section>
<div style="flex:1"></div>
<div class="foot">{"".join(f"<div>{esc(x)}</div>" for x in foot_lines)}</div>
</div>"""
    return page(meta["big"], CARD_CSS, inner)


def index_html(meta: dict, docs: list[Doc], toc_rows: list[dict]) -> str:
    toc = "".join(
        f'<div><a href="#{d.slug}">{esc(d.title)}</a> <span class="cnt">{esc(t)}</span></div>'
        for d, t in zip(docs, [r["meta"] for r in toc_rows]))
    sections = []
    for doc in docs:
        blocks = []
        for block in doc.blocks:
            rows = [hunk_html(block)] + [row_html(r) for r in block.rows]
            blocks.append(f'<section class="file">{fhead_html(block)}<div class="rows">{"".join(rows)}</div></section>')
        summ = "".join(f'<div class="k">{esc(k)}</div><div>{summary_html(v)}</div>' for k, v in doc.summary)
        sections.append(
            f'<section class="doc" id="{doc.slug}"><div class="kicker">{doc.kicker}</div><h2>{esc(doc.title)}</h2>'
            f'<div class="summary">{summ}</div>{"".join(blocks)}{notes_html(doc)}'
            f'<div class="kicker" style="margin-top:8px">{" ｜ ".join(esc(x) for x in doc.source_lines)}</div></section>')
    inner = (f'<main><header class="top"><h1>{esc(meta["title"])}</h1><div class="sub">{esc(meta["subtitle"])}</div></header>'
             f'<nav class="toc">{toc}</nav>{"".join(sections)}'
             f'<div class="foot">{"".join(f"<div>{esc(x)}</div>" for x in meta["foot"])}</div></main>')
    return page(meta["title"], INDEX_CSS, inner, INDEX_JS)
