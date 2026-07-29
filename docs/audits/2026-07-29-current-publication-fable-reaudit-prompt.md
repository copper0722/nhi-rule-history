# Narrow rereview: current publication v18 repairs

Read only these public repo files:

- `src/nhi_rule_history/current_publication.py`
- `pg/migrations/2026-07-29_nhi_rule_history_current_publication_v18.sql`
- `pg/migrations/2026-07-29_nhi_rule_history_current_publication_v18.rollback.sql`
- `docs/audits/2026-07-29-current-publication-fable-audit-response.md`

Do not edit, browse the web, call subagents, or access PostgreSQL.

Confirm whether the first audit's findings 1, 2, 3, 4 and 6 are correctly
repaired:

- every loader INSERT has a target-column list;
- activation is an append-only event log that permits reactivating an older
  sealed run, while identical replay does not add an activation;
- loader verifies whether its run is actually active;
- whole/split parity status is persisted and fingerprint-bound;
- reconstructed versions count only rows owned by sealed clause imports;
- publication runs cannot be inserted directly as sealed.

New disposable evidence after repairs:

- migration and exact 639/13,874/3,487 load passed;
- same run replay kept activation count at 1;
- direct sealed-run INSERT was rejected;
- activation of a loading run was rejected;
- a second sealed run could become active, then the original sealed run could
  be reactivated;
- focused tests and syntax checks passed.

Return at most 900 Chinese characters with exactly:

1. `VERDICT: ACCEPT_FOR_LIVE_STAGE` or `VERDICT: BLOCK`
2. `blocking findings`
3. `remaining limitations`
4. `minimum live checks`
