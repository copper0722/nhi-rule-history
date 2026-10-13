"""Statute amendment (條/項/款) → comparison-card blocks from an explicit row spec.

Statute cards use a hand-reviewed row spec instead of automatic alignment: each row names
the before/after paragraph index, and ``{..}`` marks the changed span. Every marked text must
strip back to the source text verbatim and every paragraph of every amended article must be
covered exactly once across all documents.
"""

from __future__ import annotations

import re
from typing import Any

from .core import Block, Row, para_range

ITEM_RE = re.compile(r"^[一二三四五六七八九十]+、")
MARK_RE = re.compile(r"\{([^{}]*)\}")


def law_lines(texts: list[str]) -> list[dict[str, Any]]:
    out, para = [], 0
    for t in texts:
        if ITEM_RE.match(t):
            out.append({"text": t, "item": True, "para": para})
        else:
            para += 1
            out.append({"text": t, "item": False, "para": para})
    return out


def parse_markup(markup: str) -> tuple[str, list[tuple[int, int]]]:
    text, spans, pos = [], [], 0
    for m in MARK_RE.finditer(markup):
        text.append(markup[pos:m.start()])
        start = sum(len(x) for x in text)
        text.append(m.group(1))
        spans.append((start, start + len(m.group(1))))
        pos = m.end()
    text.append(markup[pos:])
    return "".join(text), spans


def build_documents(source: dict, editorial: dict) -> list[dict[str, Any]]:
    """Return [{'doc': editorial doc, 'blocks': [Block]}] after verifying coverage."""
    old = {a: law_lines([t for _, t in (v or [])]) for a, v in source["old"].items()}
    new = {a: law_lines(v or []) for a, v in source["new"].items()}
    added = {a for a, v in source["old"].items() if not v}
    seen_old = {a: [0] * len(v) for a, v in old.items()}
    seen_new = {a: [0] * len(v) for a, v in new.items()}
    adds = {a: 0 for a in new}
    dels = {a: 0 for a in new}
    built = []
    for doc in editorial["documents"]:
        blocks = []
        for spec in doc["blocks"]:
            a = spec["art"]
            rows, old_paras, new_paras = [], set(), set()
            for r in spec["rows"]:
                kind = r[0]
                if kind in ("ctx", "ctxc"):
                    o, n = old[a][r[1]], new[a][r[2]]
                    if o["text"] != n["text"]:
                        raise ValueError(f"{a}: context row differs")
                    seen_old[a][r[1]] += 1
                    seen_new[a][r[2]] += 1
                    old_paras.add(o["para"])
                    new_paras.add(n["para"])
                    rows.append(Row(kind, n["text"], "" if o["item"] else str(o["para"]),
                                    "" if n["item"] else str(n["para"]), indent_em=2.0 if n["item"] else 0.0))
                elif kind == "del":
                    o = old[a][r[1]]
                    text, spans = parse_markup(r[2]) if r[2] else (o["text"], [])
                    if text != o["text"]:
                        raise ValueError(f"{a}: deleted markup is not verbatim")
                    seen_old[a][r[1]] += 1
                    dels[a] += 1
                    old_paras.add(o["para"])
                    rows.append(Row("del", text, "" if o["item"] else str(o["para"]), "", spans,
                                    2.0 if o["item"] else 0.0))
                elif kind == "add":
                    n = new[a][r[1]]
                    text, spans = parse_markup(r[2]) if r[2] else (n["text"], [])
                    if text != n["text"]:
                        raise ValueError(f"{a}: added markup is not verbatim")
                    seen_new[a][r[1]] += 1
                    adds[a] += 1
                    new_paras.add(n["para"])
                    rows.append(Row("add", text, "", "" if n["item"] else str(n["para"]), spans,
                                    2.0 if n["item"] else 0.0))
                elif kind == "fold":
                    (i0, i1), (j0, j1) = r[2], r[3]
                    if [x["text"] for x in old[a][i0:i1 + 1]] != [x["text"] for x in new[a][j0:j1 + 1]]:
                        raise ValueError(f"{a}: folded paragraphs differ")
                    for i in range(i0, i1 + 1):
                        seen_old[a][i] += 1
                    for j in range(j0, j1 + 1):
                        seen_new[a][j] += 1
                    rows.append(Row("fold", r[1]))
                elif kind == "xref":
                    rows.append(Row("xref", r[1]))
            blocks.append(Block(path=f"{editorial['law']}/第{a}條", badge="增訂" if a in added else "修正",
                                badge_kind="a" if a in added else "m", fn=spec["fn"], rows=rows,
                                hunk_old=para_range(sorted(old_paras), "項"),
                                hunk_new=para_range(sorted(new_paras), "項"), clause_id=a))
        built.append({"doc": doc, "blocks": blocks})
    for a in new:
        if any(c != 1 for c in seen_old[a]) or any(c != 1 for c in seen_new[a]):
            raise ValueError(f"第{a}條 coverage old={seen_old[a]} new={seen_new[a]}")
    for item in built:
        for b in item["blocks"]:
            b.adds, b.dels = adds[b.clause_id], dels[b.clause_id]
    return built
