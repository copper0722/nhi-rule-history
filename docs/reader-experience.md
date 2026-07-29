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
