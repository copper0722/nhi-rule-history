# Reader Experience for One Rule History Page

## Reader promise

One page must answer two questions without asking the reader to reconstruct a
long document history:

1. What does the newest observed reimbursement rule say?
2. What changed at each captured transition?

The primary search surface is drug name, brand name, ingredient, indication,
and disease concept. A rule number is a secondary filter, not the expected
entry point.

The official label `通則` may carry the project-assigned navigation code
`chapter:00` in API data. The reader surface displays `通則`, never “第 0 章”,
and retains `code_origin=project_assigned` wherever the code is exposed.

## Two evidence lanes

The interface must say what kind of chronology it is showing.

### Verified legal-history lane

When legal effect dates, complete accepted snapshots, and direct predecessor
edges have passed the promotion gates, the left column may display a legal
effective interval. A historical row then describes what its verified direct
successor changed.

### Source-observed cumulative-edition lane

When only official cumulative editions are available, the left column displays
the source's edition or update label. The row compares two adjacent captured
official editions. It must not relabel an edition date as a legal effective
date, and it must not call the later edition a direct legal predecessor.

The `通則` template currently uses this second lane. Its declared 15-edition
sequence is complete, while both `official_source_universe_closed` and
`legal_history_complete` remain false.

## Default page composition

The page uses two conceptual columns:

| Left column | Right column |
|---|---|
| Legal effective interval **or** official edition/update label | Complete text or transition diff |

The newest version is first and shows its complete text. Every older row shows
only the diff from that version to the next item in the declared sequence. An
older complete snapshot remains stored in PostgreSQL and may be exposed behind
an explicit “show this version in full” control.

This avoids forcing a reader to compare several nearly identical long
documents. It also preserves the meaning of a diff: each row describes one
adjacent comparison, never an accumulated comparison against the latest
version.

## Historical row semantics

For a verified legal edge, a row may say:

```text
Effective 2024-06-01 to before 2026-08-01
What the direct successor changed on 2026-08-01
```

For a cumulative-edition edge, a row instead says:

```text
109 年版
Compared with the next captured official edition: 通則（113.05.28 更新）
```

Each change hunk presents:

- the nearest structural breadcrumb;
- bounded unchanged context only when needed;
- “removed in the next edition/version” text;
- “added in the next edition/version” text;
- a replacement relationship when deletion and addition form one rewrite;
- source links for both compared snapshots;
- a control that opens either complete snapshot at the same location.

The date or edition label therefore belongs to the snapshot in the left
column, while the comparison label explicitly identifies the later snapshot.
This prevents the reader from interpreting removed text as if it disappeared
on the older snapshot's date.

An edition with zero substantive hunks remains in the sequence and says
“未觀察到實質文字變更”. Silence must not make a captured edition disappear.

## Borrow from code diff, not code-review UI

Useful ideas from source-code frontends are:

- adjacent-version comparison;
- stable block identity;
- collapsed unchanged context;
- intra-line highlighting inside a changed block;
- anchors that reopen the full document at the same location;
- deterministic diff output tied to exact input hashes.

The reader surface should not inherit:

- line numbers as the primary locator;
- `+` and `-` without human labels;
- red/green as the only distinction;
- character-by-character noise;
- merge-conflict terminology;
- side-by-side panes that become unreadable on a phone.

Use visible labels and icons in addition to color. Removed text uses a deletion
mark plus “前一版移除”; added text uses an insertion mark plus “本版新增”.
Search highlighting must use a third visual channel so it cannot be confused
with historical change.

## Diff production

PostgreSQL keeps complete normalized snapshots, typed date observations, and
explicit comparison edges. The reader projection reads stored `diff_hunk`
rows; it does not ask the browser to infer adjacency or compute the published
diff.

Diffing is hierarchical:

1. split the exact extracted rule text into logical structural blocks;
2. align blocks between two explicitly selected adjacent editions;
3. align sentences or list items within changed blocks;
4. run phrase/token diff only inside the changed span;
5. attach bounded unchanged context;
6. store the hunk, input hashes, algorithm version, ordinal, and comparison
   edge in PostgreSQL.

Chinese text must not be diffed as one undifferentiated character stream.
Punctuation, numbering, Latin drug terms, dose expressions, dates, disease
names, and parenthetical qualifiers are protected tokens. Algorithm choice is
subordinate to stable, readable hunks.

Split, merge, move, restore, correction, or uncertain legal adjacency must not
be rendered as an ordinary verified legal replacement. A cumulative-edition
comparison may still show the observed textual change, but its stored edge
must retain `legal_predecessor_status=not_claimed`.

## Reader projection

A public API must preserve this navigation provenance even if a smaller static
projection flattens the field names:

```json
{
  "source_designation_raw": "通則",
  "reader_display_label": "通則",
  "navigation_code": "chapter:00",
  "code_origin": "project_assigned"
}
```

A source-edition response for one rule provides at least:

```json
{
  "schema": "nhi-rule-history/reader-projection/v1",
  "generated_from": "PostgreSQL nhi_rule_history_edition",
  "rule": {
    "public_id": "stable-public-id",
    "display_label": "通則",
    "navigation_code": "chapter:00",
    "navigation_code_origin": "project_assigned"
  },
  "coverage": {
    "declared_edition_count": 15,
    "observed_edition_count": 15,
    "sequence_edge_count": 14,
    "official_source_universe_closed": false,
    "legal_history_complete": false
  },
  "latest": {
    "edition_label": "通則（113.05.28 更新）",
    "date_role": "official_update_label",
    "legal_effective_status": "not_claimed",
    "full_text": "complete normalized snapshot",
    "source_refs": []
  },
  "transitions": [
    {
      "older_edition_label": "109 年版",
      "newer_edition_label": "通則（113.05.28 更新）",
      "adjacency_basis": "adjacent_official_edition",
      "legal_predecessor_status": "not_claimed",
      "diff_algorithm_version": "chapter-00-reader-diff/v1.1",
      "old_snapshot_sha256": "sha256",
      "new_snapshot_sha256": "sha256",
      "diff_hunks": []
    }
  ]
}
```

`navigation_code` is never emitted as `official_chapter_number`,
`source_chapter_code`, or a stable-identity basis. A client may use it for
routing or sorting, but the schema and UI must preserve the non-claim that
`chapter:00` is project-assigned and renders as `通則`.

The projection refuses to publish unsealed imports or a broken declared
sequence. It may publish a clearly labeled source-edition comparison while
legal-history promotion remains blocked.

## Search and result ranking

Search accepts:

- generic ingredient and brand names;
- spelling and language aliases;
- indication language used by clinicians or patients;
- ATC codes and source-backed ATC descendants;
- licensed or otherwise permitted disease-concept mappings;
- rule designation as an advanced route.

Results should explain why they matched, for example “ingredient match” or
“indication match”. A disease query ranks rules whose accepted indication text
directly supports the disease above broad chapter or ATC matches. ICD-11 labels
or codes appear only when mapping and display rights permit them.

## Mobile and accessibility

On narrow screens the two columns become one timeline:

1. edition/effective label;
2. comparison scope;
3. complete current text or change hunks;
4. sources and full-version controls.

The current full text remains first. Historical hunks are grouped by
transition, not arbitrary character length. Keyboard focus, screen-reader
labels, reduced-motion preferences, print behavior, and non-color change
indicators are required acceptance checks.

## Acceptance examples

A prototype passes only if a reader can:

- read the complete newest rule without opening another tab;
- identify added and removed text without reading both full documents;
- state which two snapshots are being compared;
- tell whether the displayed date is a legal effective date or only a source
  edition/update label;
- retain zero-change editions in the sequence;
- search without confusing yellow hits with red/green diffs;
- understand the same information in monochrome and on a phone.
