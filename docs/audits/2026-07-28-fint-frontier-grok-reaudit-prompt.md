# Re-audit repaired FINT frontier crawler and yearly enumeration

You are a new independent read-only verifier. Inspect the current repository
files; do not rely on the earlier audit's verdict. Do not edit, use web search,
access live PostgreSQL, or delegate.

## Work-unit contract

```yaml
work_unit_id: grok-fint-frontier-repair-reaudit
execution_mode: repo_read
write_scope: none
input_fingerprint: fc18efe5548f10268054091b4dafee57334ea57e565967d2ade90d06594570d7
canonical_write_authorized: false
```

Read these files:

- `src/nhi_rule_history/discovery/fint.py`
- `src/nhi_rule_history/discovery/fint_keyword_crawler.py`
- `src/nhi_rule_history/pg/fint_crawl.py`
- `pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.sql`
- `pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.rollback.sql`
- `tools/crawl_fint_keywords.py`
- `tools/crawl_fint_years.py`
- `tests/test_fint_keyword_crawler.py`
- `tests/test_fint_crawl_pg.py`
- `docs/fint-keyword-crawler.md`
- `docs/audits/2026-07-28-fint-frontier-grok-reconciliation.md`

## Real controller receipts to treat as claims requiring code support

- Empty keyword slots enumerate an official result denominator.
- 2026 date partition: expected 221, fetched 221, document groups 221,
  attachment declarations 428, attachment byte snapshots 0 by explicit
  `none` policy, search/detail observations 244, issues 0.
- Its manifest SHA-256 is
  `496cce733d56323ac5ff6a5720086d50eaf5ddc5cf19e40ec8bdb23ce45a3856`.
- Disposable PG loaded and sealed it with counts 221/428/0.
- The 1900–2026 yearly batch is still running and is not an accepted receipt.

## Audit questions

1. Recheck every previous material finding. Mark each `fixed`, `partially
   fixed`, or `open` with exact evidence.
2. Is unfiltered yearly partitioning truly non-overlapping and does the root
   batch prove sum(year matches) = broad count before/after?
3. Can an old/wrong crawler-version run directory be silently promoted?
4. Can query result pages drift, duplicate, omit, or reorder RowNos without
   failure? Distinguish official RowNo occurrence completeness from unique
   formal-number identity; formal-number duplicates are allowed.
5. Are identical record texts returned by different queries preserved as
   distinct source occurrences?
6. Are every attachment declaration, optional byte snapshot, and later
   relevance distinct? Can `attachment_policy=none` honestly seal?
7. Does the loader verify the exact file set, raw hashes, all graph bindings,
   per-query contiguous `1..N`, and fetch-state parity?
8. Are repeated same-frontier observations, concurrent loaders, sealed
   INSERT/UPDATE/DELETE/TRUNCATE, forward reapply, and rollback with data safe?
9. Find any newly introduced critical or major bug, especially exception
   handling, partial manifests, annual batch resume, date boundaries, SQL
   trigger behavior, and misleading completion wording.

## Required output

Return:

1. `VERDICT`: `BLOCK`, `REPAIR_THEN_REAUDIT`, or
   `ACCEPT_REPOSITORY_STAGE_ONLY`.
2. Previous-finding reconciliation table.
3. New findings ordered by severity, each with exact file/lines,
   counterexample, invariant, minimal repair, and negative receipt.
4. Tests/receipts still missing.
5. The narrow claim accepted now.
6. Explicit remaining gates before live PG and before complete legal history.

Do not call the 1900–2026 source surface complete while its batch is still
running. Do not equate an official attachment declaration with relevant legal
evidence.
