# GPT Pro R3 decision — future-effective notice publication and 2.6.1 decision aid

Date: 2026-07-29

Verdict: **REPAIR**

## Findings

1. The two-lane direction is correct, but a date-derived lifecycle is not
   sufficient to decide legal selectability. Keep `future`, `effective`, and
   `superseded` as display states and add a separate resolution state, including
   at least `verified_scheduled`, `effective_unconsolidated`, `reconciled`,
   `corrected`, `withdrawn`, and `conflicted`.
2. The official notice is a multi-effect event. It amends 2.6.1, 2.6.2, and
   2.6.3 and also changes reimbursed drug items. Model one official notice/event
   with many clause effects. A 2.6.1-first projection must declare
   `partial_event_projection=true` and enumerate the unprocessed scope.
3. The amendment attachment is not a source-exact complete 2.6.1 clause because
   Table 2 is marked “以下略”. Either publish the source-exact amendment patch,
   or construct an explicitly reviewed `composed_clause_version` whose source
   components, hashes, spans, composition rule, and review receipt are all
   preserved. Never label deterministic composition as single-source exact text.
4. Reaching the effective date is necessary but not sufficient for automatic
   selection. The selector must also verify Asia/Taipei date precision, absence
   of correction/withdrawal/competing notices, predecessor and composition
   lineage, no unresolved old-text mismatch, and freshness of the official
   acquisition lane. Failure must resolve to `conflicted` or `unresolved`.
5. The decision outcome vocabulary must include:
   `table1_threshold_met`, `table1_threshold_not_met`,
   `requires_table2_assessment`, and `insufficient_information`.
6. Highest-risk-wins must use open-world tri-state semantics. An unknown
   higher-risk path blocks assignment to a lower-risk category. All AND/OR
   paths, imaging stenosis, CKD duration, metabolic-syndrome criteria, sex and
   age thresholds, and HDL thresholds require source-span coverage.
7. The first tool must be named and described narrowly as a
   **Table 1 LDL-C starting-treatment threshold check**, not a complete
   reimbursement or claim-approval assessment. Alternatively, every prerequisite
   period, treatment phase, and follow-up condition would have to be modeled.
8. The 116 Table-2-only products must be a source-bound code set. Only an exact
   NHI product-code match may decide membership. Unknown codes fail closed.
9. PostgreSQL invariants must make notice, effect, clause projection, and
   decision model independently immutable; keep activation/selection receipts
   separate; allow at most one selectable version per clause/as-of date; use
   half-open effective intervals; keep exact, comparison, and composite hashes
   distinct; bind the model to the composite manifest, evaluator version, and
   predicate-set fingerprint.
10. Live release gates must cover:
    - current version remains the default;
    - a future model requires explicit opt-in and repeats the effective date at
      every result;
    - browser evaluator and PostgreSQL reference evaluator parity;
    - threshold boundaries 54/55, 69/70, 99/100, 114/115, 129/130, and
      159/160 mg/dL;
    - higher-risk unknown, contradictory input, metabolic syndrome 2/5 and 3/5,
      CKD under three months, Table-2 code, and same-day conflict fail-closed
      tests;
    - no input persistence in URL, analytics, server logs, localStorage, error
      telemetry, or session replay;
    - rollback changes only the activation receipt and never deletes evidence.

## Disposition

The future official amendment may be published immediately with its effective
date. Automatic legal selection and any complete-reimbursement claim remain
blocked until the gates above are closed.

