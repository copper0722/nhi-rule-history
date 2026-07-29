# Independent gate: current clause publication v18

Work read-only in the `nhi-rule-history` repository root. Do not edit files,
use the web, call subagents, or access live PostgreSQL.

Review:

- `src/nhi_rule_history/current_publication.py`
- `src/nhi_rule_history/annotation_stage.py`
- `src/nhi_rule_history/current_anchor_clause_parity.py`
- `pg/migrations/2026-07-29_nhi_rule_history_current_publication_v18.sql`
- `pg/migrations/2026-07-29_nhi_rule_history_current_publication_v18.rollback.sql`
- `tests/test_current_publication.py`
- `docs/audits/2026-07-29-current-clause-history-inventory.json`

Contract:

1. Official chapter ODTs are the sole current-text authority.
2. One PostgreSQL row represents one current clause. Source structural blocks,
   dates, exact text, hashes and source locators remain queryable.
3. Owner-approved history metric:
   `expected=max(1, count(distinct valid ROC dates in current clause))`;
   `missing=max(0, expected-reconstructed full-text states)`.
4. The metric means missing reconstructed full-text states under this policy;
   it is not a claim that the announcement universe is closed.
5. A sealed run is immutable. JSON/API/site outputs will be read-only
   projections of the active sealed run.

Disposable PostgreSQL evidence already obtained:

- migration forward applied successfully;
- 639 clauses, 13,874 clause blocks and 3,487 distinct clause-date rows loaded;
- inventory = expected 3,512; reconstructed 656; missing 2,861;
  440 clauses with missing states; 199 without; 5 annotation-underflow flags;
- identical replay returned the same run and `already_loaded=true`;
- attempted sealed child UPDATE was rejected;
- active views returned 639 clauses and the same aggregate inventory;
- rollback removed the projection schema cleanly.

Audit for SQL/loader safety and semantic integrity before applying to
`hmj/vault_main`. In particular check placeholder/column order, source FKs,
load/seal/activate transaction boundaries, idempotency, trigger coverage,
fingerprint verification, active-run selection, and whether the inventory
formula is represented without overclaiming.

Return at most 1,500 Chinese characters with exactly these headings:

1. `VERDICT: ACCEPT_FOR_LIVE_STAGE` or `VERDICT: BLOCK`
2. `blocking findings` with exact file/line locators
3. `non-blocking limitations`
4. `minimum live verification`
