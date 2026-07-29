# Grok independent audit reconciliation — FINT frontier crawler

## Receipt

- Date: 2026-07-28
- Requested provider/model: Grok `grok-4.5`
- Reported provider/model: Grok `grok-4.5`
- Execution mode: answer/repository read
- Web search: disabled
- Subagents: disabled
- Canonical writes: forbidden
- Elapsed: 309.965 seconds
- Verdict: `REPAIR_THEN_REAUDIT`

The audited aggregate input fingerprint was
`f2c24afee3f10a14e825df9f68214d74dba7a3c0ea420c3e707676793cdf3999`.
This audit applies to the pre-repair v17 draft. It is not acceptance of the
subsequent 2.1 implementation.

## Material findings and disposition

| Finding | Severity | Disposition |
|---|---|---|
| `expected_rows` was not reconciled to contiguous match RowNos | critical | fixed in loader, PG UNIQUE, and seal trigger; negative tamper test added |
| `input_sha256 UNIQUE` blocked later observations of the same frontier | critical | removed; same seed with distinct output now tested |
| first-page link count did not protect against reorder/drift | critical | all result pages now preserve ordered RowNos and row fingerprints; every page is re-fetched before completion |
| empty attachment labels were silently dropped | major | all `GetFile.ashx` anchors retained; missing label is explicit |
| attachment rows were bound only to a formal-number group | major | split declaration from byte snapshot; declaration binds exact match and detail snapshot |
| loader checked hashes/counts but not the projection graph | major | exact manifest file set and query/detail/attachment graph validation added |
| negative tests were insufficient | major | RowNo, drift, label, parity, repeated-frontier, reapply, rollback, truncate tests added |
| rollback and migration reapply were unsafe | major | forward reapply receipt added; rollback refuses when any crawl receipt exists |

## Controller findings beyond the Grok report

The controller found two additional issues while reconciling the audit:

1. The source supports an empty-keyword query. On 2026-07-28 it enumerated
   17,497 records for `1900-01-01..2026-07-28`. This is stronger than a
   keyword-frontier-only acquisition strategy. The main plan therefore changed
   to non-overlapping unfiltered yearly partitions; keyword queries remain a
   discrepancy lane.
2. A content-derived snapshot identity that retained only its first detail URL
   would lose later query occurrences with identical record text. Snapshot
   identity now includes the exact detail URL, while formal number remains only
   a grouping key.

## False lead rejected

The audit suggested that duplicate formal numbers across RowNos might make a
query incomplete. That is not an accepted rule: the official source already
contains legitimate formal-number collisions. Completeness is checked on
ordered RowNo occurrences and exact detail snapshots, not on unique formal
numbers.

## Current acceptance boundary

Accepted after controller tests:

- repository-level crawler/loader prototype;
- real 1954 unfiltered one-record acquisition receipt;
- disposable PostgreSQL load/seal for that receipt;
- 14 focused crawler/PG tests.

Not accepted:

- live v17 migration;
- 1900–2026 batch completeness;
- official-document relevance;
- clause-transition linkage;
- complete legal history.

A new independent audit must inspect the repaired code and the yearly
enumeration contract before any live PostgreSQL application.
