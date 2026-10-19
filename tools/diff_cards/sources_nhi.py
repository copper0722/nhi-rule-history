"""NHI 藥品給付規定 修訂對照表 → comparison-card documents.

Input: one official ODT 修訂對照表 per announcement (table 0: row 0 = 修訂後給付規定 |
原給付規定 header, the revised one optionally printed 建議修訂後給付規定; each later row = one
clause; cell 0 = after, cell 1 = before, "無" = new clause; a 通則 row, whose two cells open with the
藥品給付規定通則 heading, is one clause block of the points it amends). Later tables with a 修訂後X規定 |
原X規定 header (e.g. a revised 附表 form) are compared the same way; other tables are
multi-column forms and are only noted. Paragraphs come from the owner block extractor
(`nhi_rule_history.update.odt.extract_odt_blocks`), so the text is the same deterministic
source layer used for corpus admission.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from nhi_rule_history.comparison_grammar import (
    GENERAL_PRINCIPLE_LABEL,
    is_general_principle_heading,
    is_none_cell,
)
from nhi_rule_history.update.odt import extract_odt_blocks

from .core import (
    Block,
    Row,
    align,
    elide,
    hanging_indent_em,
    inline_changes,
    key,
    nfkc,
    para_range,
    verify_block,
)

CTX_FULL_MAX = 28  # unchanged paragraphs up to this length are shown in full on cards
EFFECTIVE_RE = re.compile(r"\(自(\d+)年(\d+)月(\d+)日生效\)")
CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)*)\.")
HEADER_RE = re.compile(r"^(?:建議)?修訂後(.+)規定$")  # the 2.6.1 notice prints 建議修訂後給付規定


def read_table(payload: bytes) -> dict[str, Any]:
    sha = hashlib.sha256(payload).hexdigest()
    blocks = extract_odt_blocks(payload, sha)
    paragraphs = [b["raw_text"] for b in blocks if b["locator"]["kind"] != "table_cell"]
    cells: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    for b in blocks:
        loc = b["locator"]
        if loc["kind"] == "table_cell":
            cells[(loc["table_index"], loc["row_index"], loc["cell_index"])].append(b["raw_text"])
    first_order: dict[int, int] = {}
    for b in blocks:
        loc = b["locator"]
        if loc["kind"] == "table_cell":
            ti = loc["table_index"]
            first_order[ti] = min(first_order.get(ti, loc["document_order"]), loc["document_order"])
    comparisons, extra_tables = [], []
    for ti in sorted(first_order):
        h0, h1 = cells.get((ti, 0, 0), []), cells.get((ti, 0, 1), [])
        m = HEADER_RE.match(h0[0]) if len(h0) == 1 else None
        if not (m and h1 == [f"原{m.group(1)}規定"]):
            if ti == 0:
                raise ValueError(f"unexpected comparison-table header {(h0, h1)}")
            extra_tables.append(ti)
            continue
        title = None
        if ti:
            before = [b["raw_text"] for b in blocks if b["locator"]["kind"] != "table_cell"
                      and b["locator"]["document_order"] < first_order[ti] and b["raw_text"].startswith("附表")]
            title = before[-1] if before else None
        rows = sorted({k[1] for k in cells if k[0] == ti and k[1] > 0})
        comparisons.append({"index": ti, "kind": m.group(1), "title": title,
                            "rows": [{"new": cells.get((ti, r, 0), []), "old": cells.get((ti, r, 1), [])} for r in rows]})
    if comparisons[0]["kind"] != "給付":
        raise ValueError("table 0 is not a 給付規定 comparison")
    clauses = comparisons[0]["rows"]
    effective = None
    for p in paragraphs:
        m = EFFECTIVE_RE.search(nfkc(p))
        if m:
            effective = f"{m.group(1)}/{int(m.group(2))}/{int(m.group(3))}"
    return {
        "sha256": sha,
        "byte_size": len(payload),
        "paragraphs": paragraphs,
        "clauses": clauses,
        "extra_comparisons": comparisons[1:],
        "extra_tables": extra_tables,
        "effective_roc": effective,
    }


def none_cell(cell: list[str]) -> bool:
    """A cell that only reads 無, (無) or （無）: a new clause in the original column, a deleted one in the revised.

    The announced overlay reads the same cell the same way (``comparison_grammar.is_none_cell``).
    """
    return len(cell) == 1 and is_none_cell(cell[0])


def general_principle_row(new: list[str], old: list[str]) -> bool:
    """A 通則 row: each column opens with the 藥品給付規定通則 heading (the overlay's grammar)."""
    return bool(new) and bool(old) and is_general_principle_heading(new[0]) and is_general_principle_heading(old[0])


def clause_id(first_paragraph: str) -> str:
    m = CLAUSE_RE.match(first_paragraph)
    if not m:
        raise ValueError(f"clause number not found: {first_paragraph[:40]}")
    return m.group(1)


def clause_rows(old: list[str], new: list[str]) -> list[Row]:
    rows: list[Row] = []
    for op in align(old, new):
        if op[0] == "ctx":
            i, j = op[1], op[2]
            text = new[j]
            rows.append(Row("ctx" if len(text) <= CTX_FULL_MAX else "ctxc", text, str(i + 1), str(j + 1),
                            indent_em=hanging_indent_em(text), covers_old=[i], covers_new=[j]))
        elif op[0] == "del":
            i = op[1]
            rows.append(Row("del", old[i], str(i + 1), "", indent_em=hanging_indent_em(old[i]), covers_old=[i]))
        elif op[0] == "add":
            j = op[1]
            rows.append(Row("add", new[j], "", str(j + 1), indent_em=hanging_indent_em(new[j]), covers_new=[j]))
        else:
            i, j = op[1], op[2]
            da, db, aa, ab = inline_changes(old[i], new[j])
            rows.append(Row("del", old[i], str(i + 1), "", da, hanging_indent_em(old[i]), aa, covers_old=[i], pair=i))
            rows.append(Row("add", new[j], "", str(j + 1), db, hanging_indent_em(new[j]), ab, covers_new=[j], pair=i))
    return rows


def build_blocks(table: dict[str, Any], fn_labels: dict[str, str], exclude: tuple = ()) -> list[Block]:
    """Blocks for table-0 clauses (minus editorially excluded ids) and extra comparison tables.

    A 通則 row is one clause block of the whole row: clause_id 通則, sorted ahead of the numbered clauses on a cover."""
    blocks = []
    parent_key: tuple = ()
    for clause in table["clauses"]:
        new = clause["new"]
        old = [] if none_cell(clause["old"]) else clause["old"]
        general = general_principle_row(new, old)
        cid = GENERAL_PRINCIPLE_LABEL if general else clause_id(new[0])
        if not general:
            parent_key = parent_key or tuple(int(x) for x in cid.split("."))
        if cid in exclude:
            continue
        if old and not general and clause_id(old[0]) != cid:
            raise ValueError(f"clause number differs between columns: {cid} vs {old[0][:20]}")
        rows = clause_rows(old, new)
        block = Block(
            path=f"藥品給付規定/{cid}",
            badge="新增" if not old else "修訂",
            badge_kind="a" if not old else "m",
            fn=fn_labels.get(cid, ""),
            rows=rows,
            adds=sum(1 for r in rows if r.kind == "add"),
            dels=sum(1 for r in rows if r.kind == "del"),
            hunk_old=para_range(list(range(1, len(old) + 1)), "段"),
            hunk_new=para_range(list(range(1, len(new) + 1)), "段"),
            clause_id=cid,
        )
        block.sort_key = (0,) if general else tuple(int(x) for x in cid.split("."))
        verify_block(block, old, new)
        blocks.append(block)
    for extra in table.get("extra_comparisons", []):
        title = extra["title"] or f"附表（表{extra['index']}）"
        short, _, rest = title.partition(" ")
        for k, pair in enumerate(extra["rows"]):
            old, new = pair["old"], pair["new"]
            rows = clause_rows(old, new)
            block = Block(path=f"藥品給付規定/{short}", badge="修訂", badge_kind="m", fn=fn_labels.get(short, rest),
                          rows=rows, adds=sum(1 for r in rows if r.kind == "add"),
                          dels=sum(1 for r in rows if r.kind == "del"),
                          hunk_old=para_range(list(range(1, len(old) + 1)), "段"),
                          hunk_new=para_range(list(range(1, len(new) + 1)), "段"), clause_id=short)
            block.sort_key = parent_key + (9999, k)
            verify_block(block, old, new)
            blocks.append(block)
    return blocks


FOLD_LEVELS = ({"keep": 1, "min_run": 4}, {"keep": 0, "min_run": 2})


def card_view(rows: list[Row], level: int = 0) -> list[Row]:
    """Card rows: folded context plus in-paragraph elision (copies; source rows stay whole).
    Level 1 folds every run of 2+ unchanged paragraphs (like `diff -U0`)."""
    import copy
    out = [copy.copy(r) for r in fold_context(rows, **FOLD_LEVELS[level])]
    elided_pairs = {r.pair for r in out if r.kind in ("del", "add") and elide(r) and r.pair >= 0}
    for r in out:  # an edited paragraph is elided on both sides or neither
        if r.pair in elided_pairs and not r.keep:
            elide(r, min_len=0)
    return out


def fold_context(rows: list[Row], keep: int = 1, min_run: int = 4) -> list[Row]:
    """Card view: collapse long runs of unchanged paragraphs to edge rows + one fold row."""
    out: list[Row] = []
    run: list[Row] = []

    def flush():
        if len(run) >= min_run:
            hidden = run[keep:len(run) - keep] if keep else list(run)
            out.extend(run[:keep])
            out.append(Row("fold", f"未修訂 {len(hidden)} 段",
                           covers_old=[i for r in hidden for i in r.covers_old],
                           covers_new=[j for r in hidden for j in r.covers_new]))
            out.extend(run[len(run) - keep:] if keep else [])
        else:
            out.extend(run)
        run.clear()

    for r in rows:
        if r.kind in ("ctx", "ctxc"):
            run.append(r)
        else:
            flush()
            out.append(r)
    flush()
    return out


def whitespace_only_changes(table: dict[str, Any]) -> int:
    n = 0
    for clause in table["clauses"]:
        old = [] if none_cell(clause["old"]) else clause["old"]
        for op in align(old, clause["new"]):
            if op[0] == "ctx" and old[op[1]] != clause["new"][op[2]]:
                n += 1
    return n


def load_odt(path: Path) -> dict[str, Any]:
    return read_table(path.read_bytes())


__all__ = ["build_blocks", "card_view", "fold_context", "general_principle_row", "key", "load_odt", "none_cell",
           "read_table", "whitespace_only_changes"]
