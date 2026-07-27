# Historical closure canary batch — 2026-07-28

Status: official effective-date wording located for 5/5; **not legally
closed**.

This is the smallest current batch whose official comparison-table evidence
and 2026 endpoint can be replayed mechanically. Direct inspection of the
owning ODT documents confirms an explicit `自…生效` statement for every row.
That closes the source-local date-role question for this bounded batch, but the
evidence has not yet been admitted to the PostgreSQL event/effect ledger. It
does not change any immutable annotation from `no_match`, does not prove direct
predecessor adjacency, and does not count toward clause-history completeness.

## Selection result

At the declared 2026-07-27 cut:

- 3,080 clause/date audit units exist;
- 490 have one heuristic owning-document candidate;
- 57 of those have two unmerged old/new cells, explicit comparison headings,
  no omission marker, and a date added only on the new side;
- 17 also end at text equal to both current whole-document and chapter
  observations;
- 13 remain after excluding creation, deletion, and endpoint disagreement;
- the earliest five form this canary batch.

Ordered selection fingerprint:

`1caa343a81dc4532b7dbcb35f02b3fb0b8b76c0d26fd2b593c507f124ab4123f`

| Designation | Candidate date | Official document | Work-unit ID |
|---|---:|---|---|
| 2.1.1.5 | 2016-05-01 | 健保審字第1050035187號 | `a9dc19cfba9834170079bb5e1a24ddb917323adf55798e4eec05c118b9844287` |
| 9.1.1 | 2016-08-01 | 健保審字第1050035940號 | `3dd843893c3b23903afc980b711952a5b387e6e2f9235781e71b279b11702f59` |
| 2.12 | 2017-10-01 | 健保審字第1060036007號 | `670fe42094fe567d947006abfb252817d1f466a2d7643f1256e3e60f7fc82b90` |
| 9.9 | 2017-11-01 | 健保審字第1060036080號 | `d8df8af2600b68a351e614401b743e1d40d68d70414d6bae18b02e0d78a7e123` |
| 9.21 | 2017-11-01 | 健保審字第1060036080號 | `463676912fb2aaf1bab01fa731636ada366bd902e0f75eae5efc8280fe365bf9` |

## Effective-date source verification

| Designation | Exact official wording | ODT document order | Source row ID prefix |
|---|---|---:|---|
| 2.1.1.5 | 自105年5月1日生效 | 2 | `324e74e9` |
| 9.1.1 | 自105年8月1日生效 | 3 | `24c08da0` |
| 2.12 | 自106年10月1日生效 | 2 | `70f847b0` |
| 9.9 | 自106年11月1日生效 | 2 | `96bc6c7d` |
| 9.21 | 自106年11月1日生效 | 2 | `96bc6c7d` |

These are source observations in the official ODT comparison documents, not
dates inferred from the current clause annotations. The source rows retain the
exact wording and owning artifact identity. PostgreSQL admission and an
event/effect adjudication receipt are still pending.

The five rows use four official ODT documents. The ODT artifacts and normalized
old/new clause hashes are:

| Designation | ODT SHA-256 prefix | Old text hash prefix | New/current text hash prefix |
|---|---|---|---|
| 2.1.1.5 | `d6966f` | `26c694` | `968ff5` |
| 9.1.1 | `f3d560` | `73c7ea` | `d3f588` |
| 2.12 | `06b42d` | `b7c938` | `d2d357` |
| 9.9 | `723b1d` | `0d2759` | `1b731a` |
| 9.21 | `723b1d` | `c1171c` | `1f3756` |

The normalization used only for endpoint comparison is NFKC followed by
removal of whitespace. Equality under that normalization is endpoint evidence,
not legal adjacency.

## Evidence still required

All five remain open until every item has:

1. append-only admission of the now-located effective-date observation and its
   exact locator to the event/effect ledger, followed by adjudication;
2. a verified stable rule identity that addresses number reuse, move, split,
   merge, restore, and correction;
3. official pre- and post-event release anchors with complete member
   inventories and rule-set fingerprints;
4. an accepted official event/effect order proving the old cell is the direct
   predecessor rather than an earlier excerpt;
5. a governed full-document ODT integrity receipt; and
6. a live canonical schema and separately reviewed admission path.

The official ODT documents themselves state the effective dates. The listing
and detail pages identify the document and amendment subject but are not being
used as substitutes for the ODT date wording. A parenthetical clause
annotation by itself remains only a completeness lead.

## Allowed claim

These five rows are the first bounded batch with an unambiguous owning official
document, explicit official effective-date wording, complete old/new cells, and
a new-text endpoint equal to the 2026 whole-document and chapter observations.
The remaining identity, adjacency, anchor, integrity, and admission evidence
can therefore be pursued without another broad source search.

## Forbidden claims

- `resolved_count = 5`
- `verified_event_count = 5`
- `five histories complete`
- `old and new are proven adjacent`
- `the parenthetical date is the legal effective date`

Current source-date-role delta from this audit: **5 located, 0 admitted**.

Current legal closure delta from this audit: **0**.
