# Claude Fable independent live-application gate

You are the independent database and acquisition-contract reviewer. Read the
current repository files directly:

- `src/nhi_rule_history/discovery/fint_keyword_crawler.py`
- `src/nhi_rule_history/pg/fint_crawl.py`
- `pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.sql`
- `pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.rollback.sql`
- `tests/test_fint_keyword_crawler.py`
- `tests/test_fint_crawl_pg.py`
- `docs/audits/2026-07-28-fint-frontier-grok-reconciliation.md`

The owner now asks to preserve and load the official 84-06-20 FINT record and
its scanned `全民健康保險藥品使用規範.PDF`. A sealed crawler v2.2 run contains
one declared title query, two matching formal documents, two attachment
declarations, both attachment byte snapshots, and zero issues. It will be
loaded only as append-only acquisition evidence; no clause identity, amendment
event, OCR text, or complete-history claim will be promoted.

Audit whether the repaired migration and loader are safe to apply to live
`vault_main` for this bounded run. Concentrate on the prior Grok findings:
query `1..N` parity, repeat runs, RowNo drift, declaration-to-detail binding,
graph/hash verification, migration reapply, rollback safety, and sealed-row
immutability. Also check whether a zero-result title+document-number probe and
the successful title-only probe can coexist without identity corruption.

Return no more than 1,500 Chinese characters with exactly:

1. `VERDICT: ACCEPT_FOR_BOUNDED_LIVE_STAGE` or `VERDICT: BLOCK`
2. blocking findings, if any, with exact file/line locators
3. non-blocking limitations
4. the minimum live verification queries after load

Do not edit files, use the web, call subagents, or infer legal effect.
