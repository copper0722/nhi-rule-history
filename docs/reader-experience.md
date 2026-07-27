# Reader Experience for One Rule History Page

## Reader promise

One page must answer two questions without making the reader reconstruct the
history:

1. What is the reimbursement rule now?
2. What changed at each prior transition?

The primary search surface is drug name, brand name, ingredient, indication,
and disease concept. A rule number is a secondary filter, not the expected
entry point.

The official label `通則` may carry the project-assigned navigation code
`chapter:00` in API data. The reader surface displays `通則`, never “第 0 章”,
and explains the code in metadata or advanced filters when it is exposed.

## Default page composition

The page uses two conceptual columns:

| Left column | Right column |
|---|---|
| Effective interval | Rule content or transition diff |

The newest effective version is first and shows its complete text. Every older
row shows only the diff from that older version to its immediate successor.
The older full text remains available behind an explicit “show this version in
full” control.

This avoids asking a reader to compare several nearly identical long documents.
It also preserves temporal meaning: every diff describes one adjacent
transition, never an accumulated comparison against the latest version.

## Historical row semantics

An older row is labeled with its actual half-open validity interval:

```text
2024-06-01 to before 2026-08-01
```

Its right column is titled:

```text
What the next version changed on 2026-08-01
```

Each change hunk presents:

- the nearest section, item, and subitem breadcrumb;
- one or two unchanged context sentences when needed;
- “removed in the next version” text;
- “added in the next version” text;
- a replacement arrow when a deletion and addition are one logical rewrite;
- source links for both adjacent versions;
- a control that opens either complete version at the same location.

The date therefore always belongs to the version in the left column, while the
diff language explicitly names the successor. This prevents the common
ambiguity in which a reader cannot tell whether red text was removed on the
displayed date or on the following date.

## Borrow from code diff, not code-review UI

Useful ideas from source-code frontends are:

- adjacent-version comparison;
- stable line or block identity;
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
mark plus “removed in the next version”; added text uses an insertion mark plus
“added in the next version”. Search highlighting must use a third visual
channel so it cannot be confused with legal change.

## Diff production

The canonical database keeps complete accepted snapshots, their effective
intervals, and verified direct-adjacency edges. The reader projection derives
`adjacent_diff_hunks`; it does not ask the browser to infer legal adjacency.

Diffing is hierarchical:

1. match stable clauses and structural blocks;
2. align items and sentences within the matched clause;
3. run phrase or token diff only inside changed sentences;
4. attach bounded unchanged context;
5. classify the hunk as add, remove, replace, move, or unresolved.

Chinese text must not be diffed as an undifferentiated character stream.
Punctuation, numbering, Latin drug terms, dose expressions, dates, disease
names, and parenthetical qualifiers are protected tokens. A patience-style
block alignment may be followed by a Myers-style token diff, but algorithm
choice is subordinate to stable, readable hunks.

Split, merge, move, restore, correction, or uncertain adjacency must not be
rendered as an ordinary replacement. The row receives a visible review label
and links to the complete source versions.

## Reader projection

A future API response for one accepted rule should provide at least:

```json
{
  "rule": {
    "public_id": "stable-public-id",
    "display_designation": "9.99 Gilteritinib",
    "navigation": {
      "source_designation_raw": "通則",
      "reader_display_label": "通則",
      "navigation_code": "chapter:00",
      "code_origin": "project_assigned"
    },
    "search_terms": {
      "ingredients": [],
      "brands": [],
      "indications": [],
      "atc_codes": [],
      "disease_concepts": []
    }
  },
  "latest": {
    "effective_from": "YYYY-MM-DD",
    "effective_until_exclusive": null,
    "full_text": "complete accepted text",
    "source_refs": []
  },
  "history": [
    {
      "effective_from": "YYYY-MM-DD",
      "effective_until_exclusive": "YYYY-MM-DD",
      "successor_effective_from": "YYYY-MM-DD",
      "diff_algorithm_version": "reader-diff/v1",
      "old_snapshot_sha256": "sha256",
      "new_snapshot_sha256": "sha256",
      "adjacent_diff_hunks": [],
      "full_text_available": true,
      "source_refs": []
    }
  ]
}
```

`navigation_code` is never emitted as `official_chapter_number`,
`source_chapter_code`, or a stable-identity basis. A client may use it for
routing or sorting, but its schema description and UI copy must retain the
non-claim that `chapter:00` is project-assigned and renders as `通則`.

The API must fail closed when the direct edge, accepted snapshots, or source
mapping is unresolved. It must not create a plausible-looking diff from two
versions that merely share a designation.

## Search and result ranking

Search accepts:

- generic ingredient and brand names;
- spelling and language aliases;
- indication language used by clinicians or patients;
- ATC codes and source-backed ATC descendants;
- licensed or otherwise permitted disease-concept mappings;
- rule designation as an advanced route.

Results should explain why they matched, for example “ingredient match” or
“indication match”. A query for a disease should rank rules whose accepted
indication text directly supports that disease above broad chapter or ATC
matches. ICD-11 labels or codes appear only when the mapping and display rights
permit them.

## Mobile behavior

On narrow screens the two columns become one timeline:

1. effective interval;
2. row label;
3. complete current text or adjacent diff hunks;
4. source and full-version controls.

The current full text remains first. Historical hunks are collapsed by
transition, not by arbitrary character length. Keyboard focus, screen-reader
labels, reduced-motion preferences, and non-color change indicators are
required acceptance checks.

## Acceptance examples

A prototype passes only if a reader can:

- find a rule without knowing its number;
- read the complete current rule without opening another tab;
- identify one added sentence and one removed sentence in the next historical
  row without reading both full documents;
- state which version owned the removed text and when its successor took
  effect;
- open either complete adjacent version at the changed location;
- understand the same information in monochrome and on a phone.
