# GPT Pro R3 post-verification audit — 2.6.1 future patch and paid reader

Return exactly `GO`, `REPAIR`, or `STOP`, followed by numbered findings. Audit
reasoning, coverage, counterexamples, semantic consequences, and remaining
release risk. Do not claim access to local files, PostgreSQL, tests, or the live
site beyond the evidence in this packet. Codex ran the local verification and
Copper retains release authority.

## Decision basis

- `decision_basis_id`: `nhi-2.6.1-announced-v21-live-20260729`
- Prior Pro decision: `REPAIR`; every accepted repair is mapped below.
- Risk: R3 (legal/clinical public presentation, PostgreSQL migration, API and
  paid-site deployment).
- Official notice: `健保審字第1150671962號`, published 2026-07-28, effective
  2026-09-01.
- Official ODT SHA-256:
  `207dde0b40e9ed0238b6b40746f2450d98205f6d39d5e167ec2b41c9ec8f9e44`.
- Repositories and release commits:
  - `nhi-rule-history`: `be81839`
  - `copper-panel`: `914d56e`
  - `personal-website-s`: `daacfdb`
  - scheduler controller: `_admin-private` `9432c8ce`
- Production Pages deployment:
  `cb91c1ba-ee45-402e-a0e9-c3601a0af4e0`.

## Change manifest

1. PostgreSQL schema `nhi_rule_history_announced` separates immutable
   `notice_event`, its four `notice_effect` rows, the 2.6.1 source-exact
   `clause_patch`, 337 source components, the version-bound decision model,
   its 29 inputs, six categories, 21 DNF branches, 34 predicates, 609 product
   memberships, and append-only activation receipts.
2. The notice is marked `partial_event_projection=true`. Its unprocessed
   effects are 2.6.2, 2.6.3 and reimbursed-item changes.
3. The ODT's omitted Table 2 body is not reconstructed or called source-exact.
   The public object is explicitly a source-exact amendment patch. It has
   `omitted_text_present=true` and `composition_status=patch_only`.
4. `display_lifecycle=future` is date-derived. The API continues to return
   `legally_auto_selectable=false`; no automatic legal selector was enabled.
5. Outcomes are restricted to `table1_threshold_met`,
   `table1_threshold_not_met`, `requires_table2_assessment`, and
   `insufficient_information`. The product is named only
   「表一 LDL-C 起始治療門檻檢查」.
6. The 116 Table-2 codes are an exact source-bound set. All other 493 current
   NHI C10 reimbursement product rows are the Table-1 lane. Unknown codes fail
   closed.
7. The reference evaluator uses open-world tri-state logic. A higher-risk
   unknown blocks assignment to a lower category.
8. The paid page shows the future amendment above the still-current full
   clause, requires explicit opt-in to use the future decision aid, and repeats
   `115/9/1` beside each result.
9. Wide source tables no longer require horizontal swiping in the paid reader.
   The same physical table is rendered as one vertical patient-condition card
   per row whenever the reader column is below 900 px. Short values use a
   two-column card grid; long prescription text uses native `<details>`.
10. The 15-minute subscriber sync now idempotently loads this supported,
    hash-locked official patch before fingerprinting all five panel contracts.
    Any patch payload or date-derived lifecycle change rebuilds and verifies the
    paid projection. The generic RSS lane still acquires all notices; a future
    notice whose structure has no reviewed deterministic loader will appear in
    the notice feed but will not be misrepresented as a structured clause
    patch.

## Deterministic verification

- Exact ODT parse: 397/397 blocks accounted for exactly once.
- Live PG active sealed run:
  - one notice, four effects, one patch, 337 components;
  - one model, 29 inputs, six categories, 21 branches, 34 predicates;
  - 609 products = 493 Table 1 + exactly 116 Table 2.
- Loader is idempotent; its second run returned `already_loaded=true`.
- Attempted mutation of a sealed row was rejected.
- PostgreSQL golden evaluator:
  - `AC46402100` → `requires_table2_assessment`;
  - extreme risk + LDL-C 55 → `table1_threshold_met`;
  - extreme risk + LDL-C 54 → `table1_threshold_not_met`;
  - unknown higher-risk path → `insufficient_information`.
- Browser evaluator matches those PostgreSQL fixtures; metabolic-syndrome 2/5
  and 3/5 boundary tests pass.
- Copper Panel route
  `/api/drugs/reimbursement-rules/announced?clause=2.6.1` returned:
  contract v1, one patch, effective 2026-09-01, lifecycle future, 337
  components, 609 products and 116 Table-2 products.
- Copper Panel test file: 31/31 pass; deployed `/healthz.commit` equals
  `914d56e59ca3`.
- Paid-site suite: 138/138 pass; Astro production build: 69 routes pass; final
  artifact audit passes.
- Authenticated live JSON SHA-256 parity:
  `a4fbef1bd8f3c56c3868e906944b6043d2bc8c8b3573d1640a5797fa412873cf`.
- Authenticated production UI at 390 px:
  - `clientWidth=390`, `scrollWidth=390`;
  - future amendment visible;
  - eight row cards;
  - zero visible wide tables;
  - zero elements with horizontal overflow.
- Live Table-2 decision probe:
  `AC46402100` returned `requires_table2_assessment`, repeated `115/9/1`, did
  not alter the URL, and left both localStorage and sessionStorage empty.
- The 15-minute controller returned `status=up_to_date`, one announced patch,
  next effective date 2026-09-01, and the sealed fingerprint
  `61b62829eb83db03bd5eaad45a7610f3e70a7ebbea131b9cd7295f75cc0ba1de`.

## Recovery

- Database rollback deactivates the release and drops only the new announced
  schema; the source bundle and official notice evidence remain preserved.
- API and paid-site rollback are ordinary Git/Cloudflare release rollbacks.
- The current effective chapter publication was never overwritten.

## Residual boundaries

- This release publishes the official 2.6.1 amendment patch, not a falsely
  complete future full clause; the unchanged Table 2 body remains omitted by
  the notice attachment.
- The tool is not a complete reimbursement eligibility adjudicator.
- 2.6.2, 2.6.3 and reimbursed-item effects from the same notice remain
  unprocessed.
- A new notice without a reviewed deterministic loader is acquired and shown
  in the notice feed but not automatically promoted into a structured clause
  patch.

Questions:

1. Did the implementation satisfy every material condition in the prior
   `REPAIR`?
2. Does the new vertical-card presentation remove the sideways-scroll failure
   without changing source meaning?
3. Is the limited automation boundary honest enough for `GO` of this 2.6.1
   release, or does any residual issue require repair now?
