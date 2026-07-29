# GPT Pro R3 remediation re-audit

Please perform the one permitted post-remediation re-audit of the 2.6.1
dyslipidemia release. Return `GO` or `REPAIR`, followed by precise reasons.

## Prior blockers and implemented repairs

### 1. Production rollback is now append-only

- Migration:
  `2026-07-29_nhi_rule_history_announced_release_gate_v22.sql`
- New append-only tables:
  `release_control_event` and `patch_resolution_event`.
- `v_active_run` reads the newest activation/deactivation event; no production
  rollback drops a schema.
- The destructive schema rollback files are explicitly labeled for disposable
  or never-populated databases only.
- The production drill committed control event 4 (`deactivate`) followed by
  event 5 (`activate`) in one transaction.
- While deactivated, `v_active_run` and `v_public_clause_patch` returned zero.
  All sealed row counts and table fingerprints remained identical. Reactivation
  restored run `12484a94-7275-5199-97d1-d1876c45715f` and sealed fingerprint
  `3e3c299f0561cae600612a74d08e7a7cc5248b3945692ef03d55ce5e964e0cc8`
  without reloading.
- Machine receipt:
  `docs/audits/2026-07-29-dyslipidemia-r3-model-release-receipt.json`.

### 2. Effective-date transition now fails closed

- Every patch has a newest append-only resolution event:
  `verified_scheduled`, `effective_unconsolidated`, `reconciled`, `corrected`,
  `withdrawn`, or `conflicted`.
- Before the stated date, `verified_scheduled` may expose the explicitly
  future-opt-in tool.
- On or after the stated date, `verified_scheduled` becomes
  `effective_date_reached_unresolved`; the decision model disappears from the
  public view and API.
- Only a new `effective_unconsolidated` or `reconciled` receipt can restore the
  tool after the date. `corrected`, `withdrawn`, `conflicted`, superseded, and
  unresolved states suppress it.
- The 15-minute projection notices the lifecycle/output change but does not
  write a resolution receipt. A human/reviewed evidence process must first bind
  correction/withdrawal/replacement/competing-effect/freshness checks.
- In any non-future state, the lower full-text panel changes from “現行全文” to
  “分章全文／既有官方分章檔”; it no longer claims the old chapter is the
  currently effective complete clause.
- Live API commit: `5cf97d9797b2`; current live state is `future`,
  `verified_scheduled`, `decision_aid_available=true`,
  `legally_auto_selectable=false`.

### 3. Executable model coverage is sealed and replayed

The live PG receipt proves:

- 34/34 predicates join the exact notice artifact, patch component, block
  locator and component hash; every raw component hash replays.
- 21/21 branches pass direct `true`, `false`, and `unknown` fixtures using the
  PostgreSQL predicate evaluator.
- All six priorities have unknown coverage. Five can be the first full-evaluator
  blocker; the zero-risk branch also returns unknown directly, with a receipt
  noting that the same aggregate makes the one-risk category unknown first.
- Contradictory facts return `insufficient_information /
  contradictory_inputs`.
- Separate receipts cover sex-specific age, HDL, CKD duration 2 vs 3 months,
  metabolic syndrome 2/5 vs 3/5, and all six LDL boundaries:
  54/55, 69/70, 99/100, 114/115, 129/130, 159/160.
- The output scope receipt rejects eligibility, coverage approval,
  recommendation, lifestyle-completion, or similar output keys. The declared
  scope remains exactly “表一 LDL-C 起始治療門檻檢查”.
- PostgreSQL and browser evaluator tests pass.

### 4. Deployed privacy canary

- Authenticated production page:
  `https://s.copper0722.com/member/tools/nhi-rules/?clause=2.6.1`
- Sentinel product/LDL values: `ZZ99999999`, `987.6`.
- Request recording began after the protected page and its JSON finished
  loading. Form evaluation produced zero requests, zero console errors, no URL
  change, and no localStorage/sessionStorage entries. Neither sentinel appeared
  in any recorded request, error, or storage.
- No app analytics, error telemetry, or session-replay signal was present.
  Cloudflare injects its ordinary page-view beacon at load, but there was no
  post-interaction request to it or any other origin.
- Receipt:
  `docs/audits/2026-07-29-subscriber-nhi-rules-browser-privacy-canary.json`.

### 5. Vertical-card semantic parity

- Production deployment:
  `1ed7f4a7-bf50-4fbd-8d0f-5daac9199321`.
- At 390 × 844: clientWidth=scrollWidth=390, zero overflowing elements, eight
  visible cards and zero visible wide tables.
- Both five-column source tables were independently reconstructed from physical
  cells and rowspans. Each logical row, column label and value was compared in
  DOM order after removing layout-only whitespace:
  - table 0: 6 source rows → 5 cards, exact hashes equal;
  - table 1: 4 source rows → 3 cards, exact hashes equal.
- Receipt:
  `docs/audits/2026-07-29-subscriber-nhi-rules-mobile-card-parity.json`.
- Unit tests also fail closed on overlapping rowspans.

## Current verification

- Public data repo commits: `4c9a45b`, `49470ab`, `d408457`.
- API repo commit: `5cf97d9`.
- Paid-site commit: `b0be75c`.
- Paid-site production release passed 141/141 tests, 69 routes, final artifact
  audit, access-boundary probes, and old-deployment cleanup.
- The live API publishes one future patch with 337 components, 609 products,
  116 Table-2 products, and the explicit release gate.
- The implementation still makes only the bounded claim: official source-exact
  amendment patch plus Table-1 numeric-threshold helper. It does not claim a
  complete future clause, complete reimbursement eligibility adjudication, or
  universal automatic parsing of future notices.

Questions:

1. Are all four blockers in the prior post-deployment `REPAIR` now closed?
2. Does the implementation merit `GO` for the bounded 2.6.1 future-patch
   release?
3. If not, identify only concrete release-blocking defects still present.
