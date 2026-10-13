"""Row model and deterministic diff for regulation comparison cards.

A card compares an official "before" text with an official "after" text. The display unit
is one paragraph (a law 項/款 or one paragraph of an NHI 修訂對照表 cell). Rules:

- Comparison uses NFKC (the owner's `unicode-nfkc-remove-all-whitespace` equality), so
  compatibility ideographs and full/half-width punctuation are not reported as changes;
  displayed text is always the source text.
- Paragraph alignment ignores whitespace and a leading item marker such as ``(3)``,
  ``4.`` or ``Ⅰ.``: ODT auto-numbering is not part of the extracted text, so marker-only
  differences are numbering representation, not wording changes.
- Edited paragraphs are paired greedily in order by token similarity; the rest are pure
  deletions or additions.
- Inline highlights come from a token diff (ASCII word/number runs or single characters);
  whitespace and the leading marker are never highlighted; a one-token equal gap between
  two changes is absorbed so highlights read as phrases.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"[A-Za-z0-9]+|\S")
MARKER_RE = re.compile(
    r"^\s*(?:\(\d+\)|（\d+）|\d+(?:\.\d+)*\.|[IVX]+\.|[ivx]+\.|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|"
    r"[一二三四五六七八九十]+、|[A-Za-z]\.)"
)
PAIR_MIN = 0.45


@dataclass
class Row:
    kind: str  # ctx | ctxc | del | add | fold | xref
    text: str = ""
    old_no: str = ""
    new_no: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)
    indent_em: float = 0.0  # hanging indent width (item marker), 0 = none
    anchors: list[int] = field(default_factory=list)  # zero-width change points (pure insert/delete opposite)
    keep: list[tuple[int, int]] = field(default_factory=list)  # card view: shown ranges, rest elided
    pair: int = -1  # del/add rows produced from one edited paragraph share an id
    covers_old: list[int] = field(default_factory=list)
    covers_new: list[int] = field(default_factory=list)


@dataclass
class Block:
    """One article / clause: a file in code-diff terms."""

    path: str  # e.g. 醫療法/第24條 or 藥品給付規定/8.1.3
    badge: str  # 修正 / 增訂 / 修訂 / 新增
    badge_kind: str  # m | a
    fn: str  # hunk function context label
    rows: list[Row]
    adds: int = 0
    dels: int = 0
    hunk_old: str = ""
    hunk_new: str = ""
    clause_id: str = ""
    hunk_label: str = ""  # a list block (not compared): the hunk row reads "@@ label @@", rows are never folded


def marker_end(text: str) -> int:
    m = MARKER_RE.match(text)
    return m.end() if m else 0


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def key(text: str) -> str:
    return re.sub(r"\s+", "", nfkc(text[marker_end(text):]))


def _tokens(text: str, offset: int) -> list[tuple[str, int, int]]:
    return [(nfkc(m.group(0)), m.start() + offset, m.end() + offset)
            for m in TOKEN_RE.finditer(text[offset:])]


def similarity(a: str, b: str) -> float:
    ta = [t[0] for t in _tokens(a, marker_end(a))]
    tb = [t[0] for t in _tokens(b, marker_end(b))]
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def inline_spans(a: str, b: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Changed character ranges in a (deleted) and b (inserted)."""
    da, db, _, _ = inline_changes(a, b)
    return da, db


def inline_changes(a: str, b: str):
    """(del spans in a, ins spans in b, anchors in a, anchors in b); an anchor marks where the
    other side inserted or deleted text, so card elision keeps the same neighbourhood."""
    ta, tb = _tokens(a, marker_end(a)), _tokens(b, marker_end(b))
    ops = difflib.SequenceMatcher(None, [t[0] for t in ta], [t[0] for t in tb],
                                  autojunk=False).get_opcodes()
    merged: list[tuple] = []
    for op in ops:
        if op[0] != "equal" and merged and merged[-1][0] != "equal":
            prev = merged.pop()
            merged.append(("replace", prev[1], op[2], prev[3], op[4]))
            continue
        if (op[0] != "equal" and len(merged) >= 2 and merged[-1][0] == "equal"
                and merged[-2][0] != "equal" and merged[-1][2] - merged[-1][1] <= 1):
            merged.pop()
            prev = merged.pop()
            merged.append(("replace", prev[1], op[2], prev[3], op[4]))
            continue
        merged.append(op)
    da, db, aa, ab = [], [], [], []
    for tag, i1, i2, j1, j2 in merged:
        if tag == "equal":
            continue
        if i2 > i1:
            da.append((ta[i1][1], ta[i2 - 1][2]))
        else:
            aa.append(ta[i1][1] if i1 < len(ta) else len(a))
        if j2 > j1:
            db.append((tb[j1][1], tb[j2 - 1][2]))
        else:
            ab.append(tb[j1][1] if j1 < len(tb) else len(b))
    return da, db, aa, ab


def align(old: list[str], new: list[str]) -> list[tuple]:
    """('ctx', i, j) | ('del', i) | ('add', j) | ('pair', i, j) in display order."""
    sm = difflib.SequenceMatcher(None, [key(x) for x in old], [key(x) for x in new],
                                 autojunk=False)
    rows: list[tuple] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            rows += [("ctx", i1 + k, j1 + k) for k in range(i2 - i1)]
            continue
        pairs, jstart = [], j1
        for i in range(i1, i2):
            best, bestj = 0.0, None
            for j in range(jstart, j2):
                s = similarity(old[i], new[j])
                if s > best:
                    best, bestj = s, j
            if bestj is not None and best >= PAIR_MIN:
                pairs.append((i, bestj))
                jstart = bestj + 1
        pi, pj = i1, j1
        for i, j in pairs + [(i2, j2)]:
            rows += [("del", k) for k in range(pi, i)]
            rows += [("add", k) for k in range(pj, j)]
            if i < i2:
                rows.append(("pair", i, j))
            pi, pj = i + 1, j + 1
    return rows


def hanging_indent_em(text: str, max_em: float = 3.0) -> float:
    """Width of the leading item marker in em (monospace CJK: wide/ambiguous = 1em)."""
    end = marker_end(text)
    em = sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 0.5 for ch in text[:end])
    return em if em <= max_em else 0.0


BOUNDARY = set("、，,；;：:。()（）[]【】「」 ")


def _snap(text: str, a: int, b: int, reach: int = 12) -> tuple[int, int]:
    """Widen [a, b) to the nearest punctuation/space so words and dates are not cut."""
    n, a0, b0 = len(text), a, b
    while a > 0 and text[a - 1] not in BOUNDARY and a0 - a < reach:
        a -= 1
    while b < n and text[b] not in BOUNDARY and b - b0 < reach:
        b += 1
    return a, b


def elide(row: Row, head: int = 12, window: int = 10, min_len: int = 60, join_gap: int = 6) -> bool:
    """Card view: keep the paragraph head and a window around every change; mark the rest elided."""
    if len(row.text) < min_len or not (row.spans or row.anchors):
        return False
    n = len(row.text)
    ranges = [_snap(row.text, 0, min(head, n))]
    ranges += [_snap(row.text, max(0, a - window), min(n, b + window)) for a, b in row.spans]
    ranges += [_snap(row.text, max(0, p - window), min(n, p + window)) for p in row.anchors]
    ranges.sort()
    merged = [list(ranges[0])]
    for a, b in ranges[1:]:
        if a <= merged[-1][1] + join_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if n - merged[-1][1] <= join_gap:
        merged[-1][1] = n
    kept = sum(b - a for a, b in merged)
    if kept >= 0.8 * n:
        return False
    row.keep = [(a, b) for a, b in merged]
    return True


def para_range(nums: list[int], unit: str) -> str:
    if not nums:
        return "（無）"
    a, b = min(nums), max(nums)
    return f"第{a}{unit}" if a == b else f"第{a}～{b}{unit}"


def verify_block(block: Block, old: list[str], new: list[str]) -> None:
    """Every source paragraph appears exactly once; highlighted text is verbatim."""
    seen_old = [0] * len(old)
    seen_new = [0] * len(new)
    for row in block.rows:
        for i in row.covers_old:
            seen_old[i] += 1
        for j in row.covers_new:
            seen_new[j] += 1
        if row.kind == "del" and row.text != old[row.covers_old[0]]:
            raise ValueError(f"{block.path}: deleted text is not verbatim")
        if row.kind == "add" and row.text != new[row.covers_new[0]]:
            raise ValueError(f"{block.path}: added text is not verbatim")
        for a, b in row.spans:
            if not (0 <= a < b <= len(row.text)):
                raise ValueError(f"{block.path}: highlight outside text")
    if any(c != 1 for c in seen_old) or any(c != 1 for c in seen_new):
        raise ValueError(f"{block.path}: coverage old={seen_old} new={seen_new}")
