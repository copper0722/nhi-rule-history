"""Administrative-rule amendment (行政規則: numbered 點 with 項/款) from the Executive Yuan Gazette -> cards.

The NEW text is the gazette 網頁文字版 of the amending order (every point in it is an amended point); each
point's OLD wording comes from the earlier gazette text that last printed it. Source identity is the order
number printed in the gazette (``--expect-order`` gate), never a file name. Chapter lines (第N章 ...) are
recorded, not compared; multi-column tables and forms are not compared either, they are listed.
Everything here fails closed with a message naming the offender.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any

from .core import Block, Row, para_range, verify_block
from .html import Doc
from .sources_nhi import clause_rows

SCHEMA = "nhi-rule-history/diff-card-rule-source/v1"
CN = "一二三四五六七八九十百"
POINT_RE = re.compile(rf"^([{CN}]+)、$")
CHAPTER_RE = re.compile(rf"^第[{CN}]+章(?:[　 ]|$)")  # a chapter number, then a separator or nothing
LEVEL_RE = re.compile(r"level_(\d+)")
WS = "　 \t\r\n\xa0"  # U+3000, space, NBSP: stripped at both ends of every field
STATUSES = ("修正", "增訂", "刪除")
OLD_PHRASE = re.compile(r"~~(.+?)~~")
NEW_PHRASE = re.compile(r"\*\*(.+?)\*\*")
DIGIT_RUN = re.compile(r"[0-9][0-9./:%-]*")
LIST_INDENT_EM = 2.0  # hanging indent of a list row, so a wrapped entry stays visibly one entry


def cn2int(s: str) -> int:
    n, cur = 0, 0
    for ch in s:
        if ch in "十百":
            n += (cur or 1) * (10 if ch == "十" else 100)
            cur = 0
        else:
            cur = "一二三四五六七八九".index(ch) + 1
    return n + cur


class _Gazette(HTMLParser):
    """Collects every <p> (class, num / p_content / other text) and the span texts of the announcement div."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paras: list[dict[str, str]] = []
        self.announcements: list[list[str]] = []
        self._para: dict[str, str] | None = None
        self._part: str | None = None  # open span inside a paragraph: num | content | other
        self._ann: list[str] | None = None  # span texts of the open announcement div
        self._buf: list[str] | None = None  # open span inside the announcement div

    def handle_starttag(self, tag: str, attrs: list) -> None:
        cls = " ".join((dict(attrs).get("class") or "").split())
        if self._ann is not None:
            if tag == "div" or (tag == "span" and self._buf is not None):
                raise ValueError("announcement block holds nested markup")
            if tag == "span":
                self._buf = []
        elif tag == "div" and cls == "announcement":
            self._ann = []
        elif tag == "p":
            if self._para is not None:
                raise ValueError(f"unclosed paragraph: {self._para['full'][:30]!r}")
            self._para = {"cls": cls, "num": "", "content": "", "other": "", "full": ""}
        elif tag == "span" and self._para is not None:
            if self._part is not None:
                raise ValueError(f"nested span in paragraph: {self._para['full'][:30]!r}")
            self._part = {"num": "num", "p_content": "content"}.get(cls, "other")

    def handle_endtag(self, tag: str) -> None:
        if self._ann is not None:
            if tag == "span" and self._buf is not None:
                self._ann.append("".join(self._buf).strip(WS))
                self._buf = None
            elif tag == "div":
                self.announcements.append(self._ann)
                self._ann = self._buf = None
        elif tag == "span":
            self._part = None
        elif tag == "p" and self._para is not None:
            self.paras.append(self._para)
            self._para = self._part = None

    def handle_data(self, data: str) -> None:
        if self._ann is not None:
            if self._buf is not None:
                self._buf.append(data)
            elif data.strip(WS):
                raise ValueError("text outside the spans of the announcement block")
        elif self._para is not None:
            self._para["full"] += data
            self._para[self._part or "other"] += data


def parse_gazette(payload: bytes) -> dict[str, Any]:
    """Gazette 網頁文字版 -> announcement [date, order number], bold title and the numbered points of the text."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"gazette file is not UTF-8: {e}") from None
    gz = _Gazette()
    gz.feed(text)
    gz.close()
    if len(gz.announcements) != 1 or len(gz.announcements[0]) != 2 or not all(gz.announcements[0]):
        raise ValueError(f"expected one announcement block with a date and an order number, found {gz.announcements}")
    title: str | None = None
    points: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    cur: str | None = None
    chapter: str | None = None
    for para in gz.paras:
        if para["cls"] == "bold":
            if title is not None:
                raise ValueError(f"a second bold title paragraph: {para['full'].strip(WS)[:30]!r}")
            title = para["full"].strip(WS)
            continue
        if title is None:
            continue  # paragraphs before the bold title are the order text, not provisions
        m = LEVEL_RE.fullmatch(para["cls"])
        if not m:
            continue
        level = int(m.group(1))
        num, body = para["num"].strip(WS), para["content"].strip(WS)
        if para["other"].strip(WS):
            raise ValueError(f"text outside the num/p_content spans: {para['full'].strip(WS)[:30]!r}")
        pm = POINT_RE.match(num) if level == 0 else None
        heading = "　".join(x for x in (num, body) if x)
        if level == 0 and not pm and CHAPTER_RE.match(heading):
            chapter = heading  # recorded, never provision text; "第三章所定…" (no separator) stays a provision line
            cur = None
            continue
        if pm:
            cur = str(cn2int(pm.group(1)))
            if cur in points:
                raise ValueError(f"point {cur} appears twice")
            points[cur] = {"label": num, "chapter_printed": chapter,
                           "lines": [{"num": "", "text": body, "level": 0}]}
            order.append(cur)
        elif cur is None:
            raise ValueError(f"paragraph before any point: {para['full'].strip(WS)[:30]!r}")
        else:
            points[cur]["lines"].append({"num": num, "text": body, "level": level})
    if title is None:
        raise ValueError("no bold title paragraph (the amended text starts after it)")
    return {"sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload),
            "announcement": gz.announcements[0], "title": title, "order": order, "points": points}


def unit_problem(u: Any) -> str | None:
    """First shape problem of a snapshot unit (hand-prepared --extra-units get the same check), or None."""
    if not isinstance(u, dict):
        return "unit: not an object"
    where = f"unit {u.get('id')!r}"
    for k in ("id", "label", "para_unit", "status", "old", "new"):
        if k not in u:
            return f"{where}: missing {k!r}"
    for k in ("id", "label", "para_unit"):
        if not isinstance(u[k], str) or not u[k]:
            return f"{where}: {k!r} must be a non-empty string"
    if u["status"] not in STATUSES:
        return f"{where}: status must be one of {'/'.join(STATUSES)}"
    for side in ("old", "new"):
        if not isinstance(u[side], list):
            return f"{where}: {side!r} must be a list of lines"
        for ln in u[side]:
            if not (isinstance(ln, dict) and set(ln) == {"num", "text", "level"} and isinstance(ln["num"], str)
                    and isinstance(ln["text"], str) and isinstance(ln["level"], int) and ln["level"] >= 0):
                return f"{where}: every {side} line must be {{num, text, level}}"
    fits = {"修正": (True, True), "增訂": (False, True), "刪除": (True, False)}[u["status"]]
    if (bool(u["old"]), bool(u["new"])) != fits:
        return f"{where}: status {u['status']} does not fit its old/new lines"
    cp = u.get("chapter_printed")
    if cp is not None and not (isinstance(cp, dict) and set(cp) <= {"old", "new"}
                               and all(v is None or isinstance(v, str) for v in cp.values())):
        return f"{where}: chapter_printed must be {{old, new}} strings"
    return None


def attachment_problem(a: Any) -> str | None:
    if not (isinstance(a, dict) and set(a) == {"id", "heading", "pages", "status"}):
        return "attachment: must be {id, heading, pages, status}"
    where = f"attachment {a['id']!r}"
    if not (isinstance(a["id"], str) and a["id"] and isinstance(a["heading"], str)):
        return f"{where}: id and heading must be strings"
    pg = a["pages"]
    if not (isinstance(pg, list) and len(pg) == 2 and all(isinstance(x, int) and x >= 1 for x in pg)):
        return f"{where}: pages must be [first, last] gazette pages"
    if pg[0] > pg[1]:
        return f"{where}: pages run backwards"
    if a["status"] not in STATUSES:
        return f"{where}: status must be one of {'/'.join(STATUSES)}"
    return None


def snapshot_problem(snap: Any) -> str | None:
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        return f"not a {SCHEMA} snapshot"
    for name, problem in (("units", unit_problem), ("attachments", attachment_problem)):
        items = snap.get(name)
        if not isinstance(items, list):
            return f"snapshot lacks a {name!r} list"
        for item in items:
            if msg := problem(item):
                return msg
        dup = [k for k, n in Counter(i["id"] for i in items).items() if n > 1]
        if dup:
            return f"{name} ids must be unique, repeated: {', '.join(dup)}"
    return None


def check_snapshot(snap: Any) -> None:
    if msg := snapshot_problem(snap):
        raise ValueError(msg)


def source_entry(name: str, g: dict) -> dict[str, Any]:
    return {"file": name, "sha256": g["sha256"], "byte_size": g["byte_size"],
            "announcement": g["announcement"], "title": g["title"]}


def build_snapshot(rule: str, new_file: str, new: dict, expect_order: str, olds: list[tuple[str, dict, list[str]]],
                   added: Sequence[str] = (), expect_date: str = "", extra_units: Sequence[dict] = (),
                   attachments: Sequence[dict] = ()) -> dict[str, Any]:
    """Public source snapshot. olds = [(file name, parse_gazette result, point ids it supplies)]; every NEW point
    needs exactly one OLD source or a place in `added`. Fails before anything is written."""
    date, order_no = new["announcement"]
    if order_no != expect_order:
        raise ValueError(f"order number mismatch: expected {expect_order!r}, the NEW file announces {order_no!r}")
    if expect_date and date != expect_date:
        raise ValueError(f"announcement date mismatch: expected {expect_date!r}, the NEW file announces {date!r}")
    supplier: dict[str, int] = {}
    for k, (name, g, ids) in enumerate(olds):
        if g["sha256"] == new["sha256"]:
            raise ValueError(f"{name}: the OLD file is the NEW file")
        for pid in ids:
            if pid in supplier:
                raise ValueError(f"point {pid} is named by two --old mappings")
            if pid not in g["points"]:
                raise ValueError(f"point {pid} is named for {name} but absent from it")
            if pid not in new["points"]:
                raise ValueError(f"point {pid} is named for {name} but absent from the NEW file "
                                 "(a deleted point comes through --extra-units)")
            supplier[pid] = k
    for pid in added:
        if pid not in new["points"]:
            raise ValueError(f"--added names point {pid}, absent from the NEW file")
        if pid in supplier:
            raise ValueError(f"point {pid} is both --added and named by an --old mapping")
    missing = [pid for pid in new["order"] if pid not in supplier and pid not in added]
    if missing:
        raise ValueError(f"NEW points with no OLD source (list new points in --added): {', '.join(missing)}")
    units = []
    for pid in new["order"]:
        cur = new["points"][pid]
        old_p = olds[supplier[pid]][1]["points"][pid] if pid in supplier else None
        units.append({"id": pid, "label": f"第{pid}點", "para_unit": "項", "status": "修正" if old_p else "增訂",
                      "chapter_printed": {"old": old_p["chapter_printed"] if old_p else None,
                                          "new": cur["chapter_printed"]},
                      "old": old_p["lines"] if old_p else [], "new": cur["lines"]})
    snap = {"schema": SCHEMA, "rule": rule, "new": source_entry(new_file, new),
            "old": [{**source_entry(name, g), "points": sorted(ids, key=int)} for name, g, ids in olds],
            "units": units + [dict(u) for u in extra_units], "attachments": [dict(a) for a in attachments]}
    check_snapshot(snap)
    return snap


def display(line: dict) -> str:
    return line["num"] + line["text"]


def num_em(num: str) -> float:
    """Width of a line's printed number in em (monospace CJK: wide/ambiguous = 1em); the hanging indent."""
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 0.5 for ch in num)


def numbers(lines: list[dict], para_unit: str) -> list[int]:
    """Gutter number per line, 0 = none: 項 counts level-0 lines only, any other unit counts every line."""
    out, n = [], 0
    for ln in lines:
        if para_unit == "項" and ln["level"] != 0:
            out.append(0)
        else:
            n += 1
            out.append(n)
    return out


def line_rows(old: list[dict], new: list[dict], para_unit: str) -> list[Row]:
    """Rows exactly as the NHI lane builds them (align, pairs, spans, anchors, 28-char context rule); only the
    gutter numbers and the hanging indent follow the printed 點/項/款 structure."""
    rows = clause_rows([display(x) for x in old], [display(x) for x in new])
    onum, nnum = numbers(old, para_unit), numbers(new, para_unit)
    for r in rows:
        if r.covers_old:
            r.old_no = str(onum[r.covers_old[0]] or "")
        if r.covers_new:
            r.new_no = str(nnum[r.covers_new[0]] or "")
        line = old[r.covers_old[0]] if r.kind == "del" else new[r.covers_new[0]]
        r.indent_em = num_em(line["num"])
    return rows


def join_path(path_root: str, leaf: str) -> str:
    return f"{path_root}/{leaf}" if path_root else leaf


def build_blocks(snapshot: dict, doc_spec: dict, path_root: str = "") -> list[Block]:
    """Blocks of one editorial document (doc_spec["blocks"] = [{"unit": id, "fn": label}])."""
    units = {u["id"]: u for u in snapshot["units"]}
    blocks = []
    for spec in doc_spec["blocks"]:
        if spec["unit"] not in units:
            raise ValueError(f"{doc_spec['slug']}: unknown unit {spec['unit']!r}")
        u = units[spec["unit"]]
        old, new, pu = u["old"], u["new"], u["para_unit"]
        rows = line_rows(old, new, pu)
        block = Block(
            path=join_path(path_root, u["label"]), badge=u["status"], badge_kind="a" if u["status"] == "增訂" else "m",
            fn=spec.get("fn", ""), rows=rows,
            adds=sum(1 for r in rows if r.kind == "add"), dels=sum(1 for r in rows if r.kind == "del"),
            hunk_old=para_range([n for n in numbers(old, pu) if n], pu),
            hunk_new=para_range([n for n in numbers(new, pu) if n], pu), clause_id=u["id"])
        verify_block(block, [display(x) for x in old], [display(x) for x in new])
        blocks.append(block)
    return blocks


def attachment_card_rows(att: dict) -> list[Row]:
    """Card rows of an attachment-list document: one unchanged row per group, never folded or elided."""
    rows = []
    for g in att["groups"]:
        strip = g.get("strip", "")
        bad = [i for i in g["ids"] if not i.startswith(strip)]
        if bad:
            raise ValueError(f"attachment group {g['label']!r}: {', '.join(bad)} do not start with {strip!r}")
        rows.append(Row("ctx", f"{g['label']}：" + "、".join(i[len(strip):] for i in g["ids"]), indent_em=LIST_INDENT_EM))
    return rows


def attachment_block(snapshot: dict, att: dict, path_root: str = "") -> Block:
    """Lookup-page block of an attachment-list document: one row per attachment with its gazette pages."""
    by_id = {a["id"]: a for a in snapshot["attachments"]}
    rows = []
    for g in att["groups"]:
        for i in g["ids"]:
            a = by_id[i]
            lo, hi = a["pages"]
            where = f"第 {lo} 頁" if lo == hi else f"第 {lo}～{hi} 頁"
            tag = {"增訂": "（新增）", "刪除": "（刪除）"}.get(a["status"], "")
            rows.append(Row("ctx", f"{i}　{a['heading']}（公報 PDF {where}）{tag}", indent_em=LIST_INDENT_EM))
    return Block(path=join_path(path_root, att["label"]), badge="清單", badge_kind="m", fn=att.get("fn", ""),
                 rows=rows, hunk_label=att["hunk_label"])


def check_coverage(snapshot: dict, editorial: dict) -> None:
    """Every snapshot unit is used by exactly one block; the attachment documents list every snapshot
    attachment exactly once (multiset equality)."""
    docs = editorial["documents"]
    problems = []
    for what, want, got in (
            ("unit", Counter(u["id"] for u in snapshot["units"]),
             Counter(b["unit"] for d in docs for b in d.get("blocks", []))),
            ("attachment", Counter(a["id"] for a in snapshot["attachments"]),
             Counter(i for d in docs if "attachments" in d for g in d["attachments"]["groups"] for i in g["ids"]))):
        for label, ids in (("missing from the documents", [k for k in want if k not in got]),
                           ("not in the snapshot", [k for k in got if k not in want]),
                           ("used more than once", [k for k, n in got.items() if k in want and n > 1])):
            if ids:
                problems.append(f"{what}s {label}: {', '.join(ids)}")
    if problems:
        raise ValueError("coverage:\n  " + "\n  ".join(problems))


def fold(text: str) -> tuple[str, list[int]]:
    """Comparison form of the whole tool (NFKC, whitespace dropped) plus, per folded character, the index of
    the source character it came from: a phrase typed with the standard ideograph matches a source line that
    holds the compatibility ideograph, and a hit maps back onto the source text for the span test."""
    out, src = [], []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if not c.isspace():
                out.append(c)
                src.append(i)
    return "".join(out), src


def phrase_hit(row: Row, phrase: str) -> bool:
    """phrase occurs in the row; an edited row (it has spans) needs an occurrence touching a changed span.
    A row without spans accepts any occurrence only when it is a pure addition/deletion, not half of a pair."""
    hay, src = fold(row.text)
    needle = fold(phrase)[0]
    if not needle:
        return False
    hits = [(src[m.start()], src[m.start() + len(needle) - 1] + 1) for m in re.finditer(f"(?={re.escape(needle)})", hay)]
    if not row.spans:
        return bool(hits) and row.pair < 0
    return any(s < b and a < e for s, e in hits for a, b in row.spans)


def check_summaries(docs_with_specs: list[tuple[Doc, dict]]) -> None:
    """Summary gate. `**Y**` must be new text of its document, `~~X~~` deleted text, and every ASCII digit run
    must occur in some added/deleted line (all compared as `fold` does: NFKC, whitespace ignored).
    An attachment-list document is checked against its card rows."""
    problems = []
    for doc, spec in docs_with_specs:
        if "attachments" in spec:
            adds = dels = attachment_card_rows(spec["attachments"])
        else:
            rows = [r for b in doc.blocks for r in b.rows]
            adds, dels = [r for r in rows if r.kind == "add"], [r for r in rows if r.kind == "del"]
        for label, text in doc.summary:
            where = f"{doc.slug} / {label}"
            problems += [f"{where}: **{p}** is not new text of this document"
                         for p in NEW_PHRASE.findall(text) if not any(phrase_hit(r, p) for r in adds)]
            problems += [f"{where}: ~~{p}~~ is not deleted text of this document"
                         for p in OLD_PHRASE.findall(text) if not any(phrase_hit(r, p) for r in dels)]
            problems += [f"{where}: number {d} is in no added or deleted line of this document"
                         for d in dict.fromkeys(DIGIT_RUN.findall(label) + DIGIT_RUN.findall(text))
                         if not any(d in fold(r.text)[0] for r in adds + dels)]
    if problems:
        raise ValueError("summary gate:\n  " + "\n  ".join(problems))
