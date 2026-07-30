# Reader experience for one clause history page

## Reader promise

One page answers:

1. What does this one reimbursement clause say now?
2. What changed in this clause at each observed text transition?

Readers enter by drug, ingredient, indication, disease or test. A clause code
is a secondary navigation aid; people are not expected to memorize it.

`通則` is the official source label. `chapter:00` and `0.x` are
project-assigned navigation codes and must retain `code_origin=project_assigned`
in API data.

## One clause, not one chapter

The page's canonical version unit is a single clause:

| Data layer | Role |
|---|---|
| Complete `通則` edition | source container and provenance |
| `0.1`, `0.2`, … | independent clause identity and version chain |
| Nested paragraphs/list items | blocks and diff anchors inside the clause |

The frontend must never load a complete chapter and present it as one rule
version. Search may span many clauses, but choosing a result opens exactly one
clause page.

## Page composition

The newest observed text appears first in full. Historical rows then run from
newest transition to oldest:

| Left column | Right column |
|---|---|
| Source-edition range for the older text state | Only the diff from that state to its next distinct text state |

An unchanged annual edition remains a PostgreSQL observation but does not
create an empty historical row. Thus a clause observed in 15 annual editions
may have one text version and no diff.

## Human diff semantics

Every historical row compares a clause only with its own next text state.

- Red with a deletion label: text the next state removes.
- Green with an insertion label: text the next state adds.
- A replacement displays the bounded old and new text together.
- Unchanged context is included only when it helps locate the change.
- Both source observations have official links.

If the edge is only supported by source chronology, labels use
「前一來源觀察／後一來源觀察」 and the neutral terms
「出現／文字改變／消失」. 「本版新增／本版刪除」 is reserved for an
adjudicated direct-predecessor legal edge.

Useful code-diff ideas are adjacent-version comparison, stable block identity,
collapsed unchanged context and intra-line emphasis. The reader does not inherit
line-number primacy, unlabeled `+`/`−`, merge terminology or a desktop-only
side-by-side pane.

Color is never the only signal. On narrow screens, the two conceptual columns
stack into one reading sequence.

## Chronology and claim limits

The interface has two possible evidence lanes.

### Source-observed lane

This is the current `通則` template. The left column shows source edition labels
or a range of editions that share the same text. Edges use:

```text
adjacency_basis =
  adjacent_distinct_text_state_across_official_editions
legal_predecessor_status = not_claimed
official_source_universe_closed = false
legal_history_complete = false
```

The page must say that it is comparing observed source text states, not verified
legal predecessor events.

### Verified legal-history lane

Only after legal effective dates, complete accepted snapshots and direct
predecessor evidence pass promotion gates may the left column show a legal
effective interval. That future lane must be visibly distinguishable from the
source-observed lane.

## Search behavior

Search uses a lightweight clause index generated from PostgreSQL. Each entry
contains:

- project clause code;
- reader title;
- latest excerpt;
- all observed clause text normalized for search;
- observation and text-version counts;
- the reader query for that clause.

Search results navigate to `?rule=0.x`. Searching does not merge several clause
histories onto one page and does not mutate the stored diff.

## PostgreSQL-first rendering

PostgreSQL stores:

- clause identities;
- every source-edition observation;
- distinct clause text states;
- complete structured blocks;
- in-text date candidates;
- explicit same-clause comparison edges;
- deterministic diff hunks.

The browser reads stored projections. It does not infer version identity,
collapse annual observations or calculate published diffs.

The browser also does not infer tables or legal-list hierarchy from visible
text. It first consumes deterministic ODT structure retained in PostgreSQL. If
the parser failed to retain structure available in the official artifact, the
artifact returns to parser development and presentation work is blocked. Only
when the official source itself is insufficient for a readable layout may a
sealed, independently reviewed, strictly character-equivalent presentation
revision replace the raw block layout. See
[`presentation-proofread.md`](presentation-proofread.md).

## 專屬閱讀編排（opt-out presentation）

通用 renderer 是預設，不是目的本身。若一條正式條文同時交織多個表格、
分流維度與判斷順序，通用 renderer 即使逐字正確，仍可能迫使讀者自行重建
臨床路徑。這時可以讓「該條文的該一版本」退出通用呈現，但不能退出資料
正規化、版本、diff 或來源稽核。

可啟用專屬編排的條件：

1. 完整 Expression、文件樹、表格、健保碼連結與 exact diff 已封存；
2. 問題是認知負荷，不是漏字、錯表或 parser 尚未完成；
3. Copper 明確授權 agentic presentation；
4. profile 綁定該版 `source_version_id`、完整文字 SHA-256、
   `source_diff_run_id` 與 diff output fingerprint；
5. profile 只保存閱讀順序、結構層級變更解說與介面模板 key，不保存另一份
   可競爭的官方條文。

PG 的 `clause_reader_profile_run` 與 `clause_reader_profile` 是 append-only
發行 lane。前端只能使用 allowlisted template，所有文字以 `textContent`
輸出，不能從 PG 注入 HTML。當 API 取得的新版本、全文 hash 或 diff
fingerprint 與 profile 不同時，profile 查詢必須回空值；網站退回通用
renderer，不得沿用舊解說。

專屬頁的閱讀順序可以不同於附件順序。例如 2.6.1 依臨床實務先呈現：

1. 病人的疾病、病史與心血管風險；
2. LDL-C 數值；
3. 健保藥品代碼與表一／表二給付軌；
4. 對應門檻、處方與追蹤條件；
5. 結構層級的變更總覽；
6. 收合的完整正規化條文、左右逐字 diff、公告附件原文與完整合成原文。

其中第 1–4 項的藥品、門檻與判斷資料仍從同一個 sealed PG version 讀取。
agent 只決定怎麼說明與排序，不能另造藥品名單或數值。

Index projection:

```json
{
  "schema": "nhi-rule-history/single-clause-index/v1",
  "generated_from": "PostgreSQL nhi_rule_history_clause",
  "canonical_version_unit": "single_clause",
  "chapter": {
    "display_label": "通則",
    "navigation_code": "chapter:00",
    "navigation_code_origin": "project_assigned"
  },
  "default_clause_code": "0.4",
  "clauses": [
    {
      "canonical_code": "0.4",
      "reader_query": "?rule=0.4",
      "observed_edition_count": 15,
      "version_state_count": 10
    }
  ]
}
```

Single-clause projection:

```json
{
  "schema": "nhi-rule-history/single-clause-reader/v1",
  "generated_from": "PostgreSQL nhi_rule_history_clause",
  "canonical_version_unit": "single_clause",
  "clause": {
    "canonical_code": "0.4",
    "code_origin": "project_assigned",
    "display_title": "注射藥品之使用原則"
  },
  "coverage": {
    "observed_edition_count": 15,
    "version_state_count": 10,
    "version_edge_count": 9,
    "legal_history_complete": false
  },
  "latest": {
    "full_text_blocks": []
  },
  "transitions": [
    {
      "older": {"observed_editions": []},
      "newer": {"observed_editions": []},
      "hunks": []
    }
  ]
}
```

## Acceptance check

A prototype passes only if a reader can:

- find a clause by ordinary medical wording;
- tell which single clause is open;
- read that clause's newest full text without opening another tab;
- scan only meaningful historical changes;
- distinguish deletion from addition without relying on color;
- identify both source observations for a diff;
- understand that source chronology is not yet complete legal history;
- use the page on a phone without horizontal diff scrolling.

The same reader-wording validator must run for PostgreSQL exports, JSONL,
SQLite, API responses and the static reader. A release fails if any surface
turns an observation date into an effective date, an observed disappearance
into a verified deletion, or a bounded search miss into non-existence.
