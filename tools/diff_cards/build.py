"""Build comparison cards for an NHI 藥品給付規定 announcement batch.

  python -m tools.diff_cards.build snapshot --out PUB/source/clauses.json BUNDLE_DIR...
      Read each announcement bundle's manifest + ODT 修訂對照表 with the owner block
      extractor and write a public source snapshot (official text + SHA-256 locators).
  python -m tools.diff_cards.build render PUB [--png BUILD_DIR]
      PUB/source/clauses.json + PUB/editorial.json -> PUB/index.html (lookup page) and
      PUB/cards/*.html (1080x1350 card pages); PNGs go to BUILD_DIR (default build/...).
  python -m tools.diff_cards.build snapshot-gazette --out PUB/source/provisions.json --rule NAME \
      --new NEW.htm --expect-order NO --old "3,5=OLD.htm" [--added IDS] [--expect-date D] [--extra-units F] [--attachments F]
      Administrative-rule points from the Executive Yuan Gazette text version -> public source snapshot;
      the NEW file must announce the expected order number.
  python -m tools.diff_cards.build render-rule PUB [--png BUILD_DIR]
      PUB/source/provisions.json + PUB/editorial.json -> lookup page, card pages and PNGs (as `render`).
  python -m tools.diff_cards.build load-pg [--dsn DSN] [--corpus-root DIR] [--dry-run]
      Store the website's git-diff card data: every registered notice's comparison-table ODT ->
      blocks -> schema nhi_rule_history_diff_card (write-once, read back through a fresh connection).
      Receipt JSON on stdout; see tools/diff_cards/pg_store.py.
  python -m tools.diff_cards.build render-pg --sha SHA --out DIR [--dsn DSN]
      One notice's stored comparison (found by the SHA-256 of its official ODT) -> PNG cards, lookup page
      and caption in DIR, with a deterministic default editorial; read-only PostgreSQL session.
      See tools/diff_cards/pg_render.py.
  python -m tools.diff_cards.build list-notices [--dsn DSN]
      Notices of the active announced run that have a stored comparison, as JSON on stdout.

Cards are presentation only: official text is copied verbatim from the snapshot and the
diff is recomputed deterministically; editorial.json holds the human-written summaries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from . import sources_rule
from .core import Row
from .html import Doc, card_page_html, cover_html, index_html, measure_html
from .sources_nhi import FOLD_LEVELS, build_blocks, card_view, load_odt

CARD_W, CARD_H = 1080, 1350
PAD_TOP, PAD_BOTTOM, MAIN_MT, FOOT_MT, GAP, BORDER = 48, 30, 22, 18, 18, 2
MIN_FIRST_ROWS = 3


def snapshot(bundle_dirs: list[Path], out: Path) -> None:
    items = []
    for bdir in bundle_dirs:
        man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        tables = []
        for f in man["files"]:
            if f.get("role") != "declared_attachment" or not f["file_name"].endswith(".odt"):
                continue
            table = None
            try:
                table = load_odt(bdir / f["file_name"])
            except ValueError:
                continue  # not a 修訂對照表 ODT
            if table["sha256"] != f["sha256"]:
                raise SystemExit(f"{bdir.name}: attachment hash differs from manifest")
            tables.append({"declared_sequence": f.get("declared_sequence"), "media_type": f.get("media_type"),
                           "sha256": table["sha256"], "byte_size": table["byte_size"],
                           "effective_roc": table["effective_roc"], "paragraphs": table["paragraphs"],
                           "extra_tables": table["extra_tables"], "clauses": table["clauses"],
                           "extra_comparisons": table["extra_comparisons"]})
        if len(tables) != 1:
            raise SystemExit(f"{bdir.name}: expected exactly one comparison-table ODT, found {len(tables)}")
        items.append({"ref_number": man["ref_number"], "title": man["title_zh"], "publish_date": man["publish_date"],
                      "source_url": man["source_url"], "table": tables[0]})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "nhi-rule-history/diff-card-source/v1", "announcements": items},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(items)} announcements)")


def roc_to_iso(roc: str) -> str:
    y, m, d = (int(x) for x in roc.split("/"))
    return f"{y + 1911:04d}-{m:02d}-{d:02d}"


def iso_to_roc(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{y - 1911}/{m}/{d}"


def make_docs(src: dict, ed: dict) -> list[Doc]:
    docs = []
    by_ref = {a["ref_number"]: a for a in src["announcements"]}
    for e in ed["documents"]:
        a = by_ref[e["ref_number"]]
        table = a["table"]
        blocks = build_blocks(table, e.get("fn", {}), tuple(e.get("exclude_clauses", ())))
        effective = table["effective_roc"]
        notes = list(e.get("notes", []))
        if table["extra_tables"] and not e.get("notes"):
            notes.append("本公告另附表格修訂，未列入 diff，請見原公告附件。")
        source_lines = [f"來源：健保署 {a['ref_number']} 修訂對照表（{iso_to_roc(a['publish_date'])} 公告）｜全文見 HTML 版",
                        "空白、全半形與自動編號差異不標示｜" + ed["disclaimer"]]
        docs.append(Doc(
            slug=e["slug"], title=e["title"],
            kicker=f"<b>{a['ref_number']}</b>　{iso_to_roc(a['publish_date'])} 公告　{effective} 生效",
            summary=[tuple(x) for x in e["summary"]], blocks=blocks,
            legend_old="原給付規定", legend_new="修訂後給付規定", number_note="⋯ 省略未改動　數字為段落序",
            source_lines=source_lines, notes=notes))
    return docs


def measure(doc: Doc, card_rows: dict[int, list], browser) -> dict[str, float]:
    page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
    page.set_content(measure_html(doc, card_rows), wait_until="load")
    page.wait_for_timeout(300)
    heights = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('[data-u]')]"
                            ".map(e => [e.dataset.u, e.getBoundingClientRect().height]))")
    page.close()
    return heights


def paginate(doc: Doc, card_rows: dict[int, list], h: dict[str, float]) -> list[list[dict]]:
    foot = FOOT_MT + h["foot"]
    cap_first = CARD_H - PAD_TOP - PAD_BOTTOM - h["head1"] - MAIN_MT - foot
    cap_cont = CARD_H - PAD_TOP - PAD_BOTTOM - h["head2"] - MAIN_MT - foot
    pages: list[list[dict]] = [[]]
    avail, used = cap_first, 0.0
    for bi, block in enumerate(doc.blocks):
        rows = card_rows[bi]
        ri, first_seg = 0, True
        while ri < len(rows):
            gap = GAP if pages[-1] else 0
            base = h[f"b{bi}-fhead"] + BORDER + (h[f"b{bi}-hunk"] if first_seg else h[f"b{bi}-xref"])
            room = avail - used - gap - base
            n, height = 0, 0.0
            while ri + n < len(rows):
                rh = h[f"b{bi}-r{ri + n}"]
                reserve = h[f"b{bi}-xref"] if ri + n + 1 < len(rows) else 0
                if height + rh + reserve > room:
                    break
                height += rh
                n += 1
            tail = rows[ri + n:]
            if n and tail and not block.hunk_label and all(r.kind in ("ctx", "ctxc", "fold") for r in tail):
                # only unchanged paragraphs would spill: fold them into one row on this page
                # (a list block is never folded: it continues on the next card)
                folded = Row("fold", f"未修訂 {sum(max(1, len(r.covers_new)) for r in tail)} 段",
                             covers_old=[i for r in tail for i in r.covers_old],
                             covers_new=[j for r in tail for j in r.covers_new])
                fold_h = h[f"b{bi}-xref"]
                if height + fold_h <= room:
                    rows[ri + n:] = [folded]
                    h[f"b{bi}-r{ri + n}"] = fold_h
                    height += fold_h
                    n += 1
            too_few = first_seg and n < min(MIN_FIRST_ROWS, len(rows))
            if (n == 0 or too_few) and pages[-1]:
                pages.append([])
                avail, used = cap_cont, 0.0
                continue
            if n == 0:
                raise SystemExit(f"{doc.slug}: a single row does not fit on a card")
            more = ri + n < len(rows)
            pages[-1].append({"block": bi, "start": ri, "end": ri + n, "hunk": first_seg, "cont": not first_seg,
                              "xref_top": "接上一張" if not first_seg else "",
                              "xref_bottom": "接下一張" if more else ""})
            used += gap + base + height + (h[f"b{bi}-xref"] if more else 0)
            ri += n
            first_seg = False
            if more:
                pages.append([])
                avail, used = cap_cont, 0.0
    notes_h = h.get("notes", 0)
    if notes_h and used + GAP + notes_h > avail:
        pages.append([])
    return pages


def render_cards(pub: Path, png_dir: Path, docs: list[Doc], cover_meta: dict, stat_rows: list[dict],
                 cover_foot: list[str], index_meta: dict, toc: list[dict],
                 card_rows_for: Callable[[Doc, int], dict[int, list[Row]]]) -> None:
    """Lane-independent tail: cover, measured + paginated card pages, PNGs, overflow check, lookup page.
    card_rows_for(doc, fold level) -> {block index: card rows}."""
    from playwright.sync_api import sync_playwright

    cards_dir = pub / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    for old in cards_dir.glob("*.html"):
        old.unlink()
    png_dir.mkdir(parents=True, exist_ok=True)
    for old in png_dir.glob("*.png"):
        old.unlink()  # generated output of this command only
    outputs: list[tuple[str, str]] = []
    outputs.append(("01-cover.html", cover_html(cover_meta, stat_rows, cover_foot)))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        seq = 2
        for doc in docs:
            best = None
            for level in range(len(FOLD_LEVELS)):
                card_rows = card_rows_for(doc, level)
                h = measure(doc, card_rows, browser)
                pages = paginate(doc, card_rows, h)
                if best is None or len(pages) < len(best[1]):
                    best = (card_rows, pages)
                if len(pages) == 1:
                    break
            card_rows, pages = best
            for k, pg in enumerate(pages):
                segs = []
                for s in pg:
                    block = doc.blocks[s["block"]]
                    segs.append({"block": block, "rows": card_rows[s["block"]][s["start"]:s["end"]], "hunk": s["hunk"],
                                 "cont": s["cont"], "xref_top": s["xref_top"], "xref_bottom": s["xref_bottom"]})
                suffix = f"-{k + 1}" if len(pages) > 1 else ""
                outputs.append((f"{seq:02d}-{doc.slug}{suffix}.html",
                                card_page_html(doc, segs, first=(k == 0), last=(k == len(pages) - 1))))
                seq += 1
        for name, doc_html in outputs:
            (cards_dir / name).write_text(doc_html, encoding="utf-8")
        page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H}, device_scale_factor=2)
        problems = []
        for name, _ in outputs:
            page.goto((cards_dir / name).resolve().as_uri())
            page.wait_for_timeout(400)
            over = page.evaluate("() => { const m = document.querySelector('.main');"
                                 " return m ? m.scrollHeight - m.clientHeight : 0; }")
            if over > 1:
                problems.append(f"{name}: main overflows by {over}px")
            page.screenshot(path=str(png_dir / name.replace(".html", ".png")),
                            clip={"x": 0, "y": 0, "width": CARD_W, "height": CARD_H})
        browser.close()
    (pub / "index.html").write_text(index_html(index_meta, docs, toc), encoding="utf-8")
    print(f"cards: {len(outputs)} pages -> {cards_dir}; png -> {png_dir}")
    if problems:
        raise SystemExit("overflow:\n" + "\n".join(problems))


def nhi_card_rows(doc: Doc, level: int) -> dict[int, list[Row]]:
    return {bi: card_view(b.rows, level) for bi, b in enumerate(doc.blocks)}


def render(pub: Path, png_dir: Path) -> None:
    src = json.loads((pub / "source" / "clauses.json").read_text(encoding="utf-8"))
    ed = json.loads((pub / "editorial.json").read_text(encoding="utf-8"))
    docs = make_docs(src, ed)
    # cover: one stat row per clause, natural clause order
    stat_rows = []
    for doc in docs:
        for block in doc.blocks:
            stat_rows.append({"id": block.clause_id, "label": ed["cover_labels"][block.clause_id],
                              "adds": block.adds, "dels": block.dels, "key": block.sort_key})
    stat_rows.sort(key=lambda s: s["key"])
    cov = ed["cover"]
    n_new = sum(1 for d in docs for b in d.blocks if b.badge_kind == "a")
    n_mod = sum(1 for d in docs for b in d.blocks if b.badge_kind == "m")
    meta = dict(cov, total_prefix=f"<b>{len(stat_rows)}</b> 項異動（新增 {n_new}、修訂 {n_mod}）")
    toc = [{"meta": f"{d.blocks[0].path.split('/')[-1]}" + (f" 等 {len(d.blocks)} 條" if len(d.blocks) > 1 else "")}
           for d in docs]
    render_cards(pub, png_dir, docs, meta, stat_rows, ed["cover_foot"], ed["index"], toc, nhi_card_rows)


def render_law(pub: Path) -> None:
    """Statute publication: PUB/source/articles.json + PUB/editorial.json -> PUB/index.html."""
    from .sources_law import build_documents

    src = json.loads((pub / "source" / "articles.json").read_text(encoding="utf-8"))
    ed = json.loads((pub / "editorial.json").read_text(encoding="utf-8"))
    docs = []
    for item in build_documents(src, ed):
        d = item["doc"]
        for block in item["blocks"]:
            for r in block.rows:
                if r.kind == "xref":  # card wording -> page wording
                    r.text = r.text.replace("見下一張", "見下一節").replace("見上一張", "見上一節")
        docs.append(Doc(
            slug=d["slug"], title=d["title"], kicker=f"<b>{ed['law']}</b>　{ed['promulgated']} 公布　{ed['order']}",
            summary=[tuple(x) for x in d["summary"]], blocks=item["blocks"],
            legend_old="修正前", legend_new="修正後", number_note="數字為項次",
            source_lines=[f"來源：全國法規資料庫（修正後 {ed['promulgated']}；修正前 {ed['old_version']} 版）", ed["disclaimer"]]))
    toc = [{"meta": "、".join(f"第{b.clause_id}條" for b in d.blocks)} for d in docs]
    (pub / "index.html").write_text(index_html(ed["index"], docs, toc), encoding="utf-8")
    print(f"index: {pub / 'index.html'} ({len(docs)} sections)")


RULE_KEYS = ("path_root", "kicker", "legend_old", "legend_new", "number_note", "source_lines", "disclaimer",
             "cover", "cover_labels", "cover_foot", "index", "documents")
SUB_KEYS = {"cover": ("cmd", "big", "sub", "dates_html", "hash", "hash_note"), "index": ("title", "subtitle", "foot")}


def parse_ids(arg: str, flag: str) -> list[str]:
    """'3,5,11' -> ['3', '5', '11']: point numbers without repeats."""
    ids = arg.split(",")
    if not all(re.fullmatch(r"[0-9]+", x) for x in ids) or len({int(x) for x in ids}) != len(ids):
        raise SystemExit(f"{flag}: expected comma-separated point numbers without repeats, got {arg!r}")
    return [str(int(x)) for x in ids]


def read_gazette(path: Path) -> dict:
    try:
        return sources_rule.parse_gazette(path.read_bytes())
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path.name}: {e}") from None


def read_list(path: Path | None, flag: str) -> list:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{flag}: expected a JSON array")
    return data


def snapshot_gazette(out: Path, rule: str, new: Path, expect_order: str, olds: list[str], added: str = "",
                     expect_date: str = "", extra_units: Path | None = None, attachments: Path | None = None) -> None:
    old_files = []
    for spec in olds:
        ids, sep, name = spec.partition("=")
        if not sep or not name:
            raise SystemExit(f"--old expects IDS=FILE, got {spec!r}")
        old_files.append((Path(name).name, read_gazette(Path(name)), parse_ids(ids, "--old")))
    try:
        snap = sources_rule.build_snapshot(
            rule, new.name, read_gazette(new), expect_order, old_files, parse_ids(added, "--added") if added else [],
            expect_date, read_list(extra_units, "--extra-units"), read_list(attachments, "--attachments"))
    except ValueError as e:
        raise SystemExit(f"snapshot-gazette: {e}") from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(snap['units'])} units, {len(snap['attachments'])} attachments)")


def make_rule_docs(src: dict, ed: dict) -> tuple[list[Doc], list[dict]]:
    """Editorial documents -> Doc objects (compared units, attachment lists) after the coverage gate."""
    lacking = [k for k in RULE_KEYS if k not in ed]
    lacking += [f"{name}.{k}" for name, keys in SUB_KEYS.items() if name in ed for k in keys if k not in ed[name]]
    if lacking:
        raise ValueError(f"editorial.json lacks: {', '.join(lacking)}")
    specs = ed["documents"]
    for spec in specs:
        lacking = [k for k in ("slug", "title", "summary") if k not in spec]
        if lacking or ("blocks" in spec) == ("attachments" in spec):
            raise ValueError(f"document {spec.get('slug')!r}: needs slug, title, summary and either blocks or "
                             f"attachments{' (lacks ' + ', '.join(lacking) + ')' if lacking else ''}")
    slugs = [s["slug"] for s in specs]
    if len(set(slugs)) != len(slugs):
        raise ValueError("document slugs must be unique")
    sources_rule.check_coverage(src, ed)
    docs = []
    for spec in specs:
        blocks = ([sources_rule.attachment_block(src, spec["attachments"], ed["path_root"])] if "attachments" in spec
                  else sources_rule.build_blocks(src, spec, ed["path_root"]))
        docs.append(Doc(
            slug=spec["slug"], title=spec["title"], kicker=ed["kicker"], summary=[tuple(x) for x in spec["summary"]],
            blocks=blocks, legend_old=ed["legend_old"], legend_new=ed["legend_new"], number_note=ed["number_note"],
            source_lines=[*ed["source_lines"], ed["disclaimer"]], notes=list(spec.get("notes", []))))
    return docs, specs


def rule_cover(src: dict, ed: dict, docs: list[Doc], specs: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Cover meta, one stat row per compared unit (document order) and the lookup page's TOC meta."""
    units = {u["id"]: u for u in src["units"]}
    stat_rows, toc, count = [], [], Counter()
    for doc, spec in zip(docs, specs, strict=True):
        if "attachments" in spec:
            toc.append({"meta": f"{len(doc.blocks[0].rows)} 項"})
            continue
        for block in doc.blocks:
            u = units[block.clause_id]
            if u["id"] not in ed["cover_labels"]:
                raise ValueError(f"cover_labels lacks unit {u['id']!r}")
            stat_rows.append({"id": u["label"], "label": ed["cover_labels"][u["id"]],
                              "adds": block.adds, "dels": block.dels})
            count[u["status"]] += 1
        toc.append({"meta": "、".join(units[b.clause_id]["label"] for b in doc.blocks)})
    if not stat_rows:
        raise ValueError("no compared unit: the cover needs at least one")
    parts = "、".join(f"{k} {count[k]}" for k in sources_rule.STATUSES if count[k])
    return {"total_prefix": f"<b>{len(stat_rows)}</b> 項異動（{parts}）", **ed["cover"]}, stat_rows, toc


def rule_card_rows(specs: list[dict]) -> Callable[[Doc, int], dict[int, list[Row]]]:
    """Card rows of the rule lane: NHI card view, in-paragraph elision off per document on request; an
    attachment list bypasses the card view (its rows are never folded or elided)."""
    by_slug = {s["slug"]: s for s in specs}

    def rows_for(doc: Doc, level: int) -> dict[int, list[Row]]:
        spec = by_slug[doc.slug]
        if "attachments" in spec:
            return {0: sources_rule.attachment_card_rows(spec["attachments"])}
        out = {}
        for bi, b in enumerate(doc.blocks):
            out[bi] = card_view(b.rows, level)
            if spec.get("elide", True) is False:
                for r in out[bi]:
                    r.keep = []
        return out

    return rows_for


def render_rule(pub: Path, png_dir: Path) -> None:
    """Administrative-rule publication: PUB/source/provisions.json + PUB/editorial.json -> index, cards, PNGs."""
    src = json.loads((pub / "source" / "provisions.json").read_text(encoding="utf-8"))
    ed = json.loads((pub / "editorial.json").read_text(encoding="utf-8"))
    try:
        sources_rule.check_snapshot(src)
        docs, specs = make_rule_docs(src, ed)
        sources_rule.check_summaries(list(zip(docs, specs, strict=True)))
        meta, stat_rows, toc = rule_cover(src, ed, docs, specs)
    except ValueError as e:
        raise SystemExit(f"render-rule: {e}") from None
    render_cards(pub, png_dir, docs, meta, stat_rows, ed["cover_foot"], ed["index"], toc, rule_card_rows(specs))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="diff_cards")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("bundles", nargs="+", type=Path)
    r = sub.add_parser("render")
    r.add_argument("pub", type=Path)
    r.add_argument("--png", type=Path)
    lw = sub.add_parser("render-law")
    lw.add_argument("pub", type=Path)
    sg = sub.add_parser("snapshot-gazette")
    sg.add_argument("--out", required=True, type=Path)
    sg.add_argument("--rule", required=True)
    sg.add_argument("--new", required=True, type=Path)
    sg.add_argument("--expect-order", required=True)
    sg.add_argument("--expect-date", default="")
    sg.add_argument("--old", action="append", default=[], metavar="IDS=FILE")
    sg.add_argument("--added", default="")
    sg.add_argument("--extra-units", type=Path)
    sg.add_argument("--attachments", type=Path)
    rr = sub.add_parser("render-rule")
    rr.add_argument("pub", type=Path)
    rr.add_argument("--png", type=Path)
    lp = sub.add_parser("load-pg", help="store the website's git-diff card data in PostgreSQL")
    lp.add_argument("--dsn", help="PostgreSQL connection string; default $NHI_RULE_HISTORY_DSN, else service=$PGSERVICE")
    lp.add_argument("--corpus-root", type=Path, help="NHI notice bundle root; default $NHI_RULE_HISTORY_CORPUS_ROOT")
    lp.add_argument("--dry-run", action="store_true", help="build and verify everything, write nothing")
    rp = sub.add_parser("render-pg", help="share cards of one stored comparison: PNGs, lookup page, caption")
    rp.add_argument("--sha", required=True, help="SHA-256 of the notice's official ODT (64 lower-case hex digits)")
    rp.add_argument("--out", required=True, type=Path, help="folder for the PNGs, index.html and caption.txt")
    rp.add_argument("--dsn", help="PostgreSQL connection string; default $NHI_RULE_HISTORY_DSN, else service=$PGSERVICE")
    ln = sub.add_parser("list-notices", help="notices of the active announced run that have a stored comparison")
    ln.add_argument("--dsn", help="PostgreSQL connection string; default $NHI_RULE_HISTORY_DSN, else service=$PGSERVICE")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        snapshot(a.bundles, a.out)
    elif a.cmd == "snapshot-gazette":
        snapshot_gazette(a.out, a.rule, a.new, a.expect_order, a.old, a.added, a.expect_date,
                         a.extra_units, a.attachments)
    elif a.cmd == "render-law":
        render_law(a.pub)
    elif a.cmd == "render-rule":
        render_rule(a.pub, a.png or Path("build/diff-cards") / a.pub.name / "png")
    elif a.cmd == "load-pg":
        # imported here: it needs psycopg and the loader modules, the render commands do not
        from .pg_store import load_pg

        return load_pg(a.dsn, a.corpus_root, dry_run=a.dry_run)
    elif a.cmd == "render-pg":
        # imported here: it needs psycopg and a browser, the other render commands need no database
        from .pg_render import render_pg

        return render_pg(a.sha, a.out, a.dsn)
    elif a.cmd == "list-notices":
        from .pg_render import list_pg

        return list_pg(a.dsn)
    else:
        render(a.pub, a.png or Path("build/diff-cards") / a.pub.name / "png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
