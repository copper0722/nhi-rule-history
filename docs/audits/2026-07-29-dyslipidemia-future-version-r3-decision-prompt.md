# GPT Pro R3 decision packet — future-effective notice publication and 2.6.1 decision aid

## Decision requested

Review the proposed data contract before implementation. Return exactly one
verdict (`GO`, `REPAIR`, or `NO-GO`) followed by numbered findings and a
concrete repair list when applicable.

The system publishes Taiwan NHI drug reimbursement rules to a paid reader.
PostgreSQL is canonical. This change is legal/clinical, automated, public
facing, and deployed, so it is R3.

## Owner intent

1. A new official reimbursement notice should appear mechanically as soon as
   it is published and acquired.
2. If its effective date is in the future, the UI must visibly say so and must
   not mislabel it as the current effective rule.
3. Start with dyslipidemia clause 2.6.1.
4. Add structured checkbox/number/select inputs that can be projected from PG
   to the website. User inputs yield a deterministic reimbursement-condition
   assessment.

## Verified official facts

- Official notice:
  `https://www.nhi.gov.tw/ch/cp-20300-7968a-3258-1.html`
- Reference number: `健保審字第1150671962號`
- Publication date: `2026-07-28`
- Effective date stated by the official notice/attachment:
  `2026-09-01`
- Official amendment ODT SHA-256:
  `207dde0b40e9ed0238b6b40746f2450d98205f6d39d5e167ec2b41c9ec8f9e44`
- A deterministic ODT structural walk and corpus bundle already preserve the
  exact source bytes and locators. The amendment replaces 2.6.1 with ASCVD risk
  categories and LDL-C thresholds:
  - extreme risk: 55 mg/dL
  - very high risk: 70 mg/dL
  - high risk: 100 mg/dL
  - moderate risk: 115 mg/dL
  - low risk: 130 mg/dL
  - zero cardiovascular risk factors: 160 mg/dL
- The amendment also declares 116 specific statin product codes that use
  Table 2 rather than the new Table 1.
- Current active PG publication is still the official chapter ODT updated
  2026-05-22. Its 2.6.1 text SHA-256 is
  `5c6cbaaae104aaed9427080168c38ff25afc38667063c29eb04981fbdee56e3a`.

## Proposed publication contract

Keep two distinct lanes:

1. `effective/current`: existing active sealed chapter publication.
2. `announced`: immutable clause versions derived from an exact official
   notice attachment, with announcement date, effective date, exact source
   spans, content hash, and lifecycle computed at query time.

An announced version does not overwrite the active current chapter
publication. API and UI return:

- the currently effective clause;
- zero or more announced future versions;
- a computed lifecycle: `future`, `effective`, or `superseded`;
- explicit source type and effective date.

On the effective date, the query-time legal selector may treat the exact
sealed notice version as effective even if the consolidated chapter page has
not yet been refreshed. A later official chapter refresh is reconciled by
exact normalized-text/hash parity; a conflict fails closed and remains visibly
unresolved. No model performs promotion.

Import gates:

- official NHI URL and exact notice metadata;
- verified immutable attachment bytes;
- exact clause designation;
- new-column extraction with exact source locators;
- effective date from official page/attachment, never RSS time;
- old-column semantic match against the current clause or an explicit
  partial/omitted-old-text state;
- no omitted text in the proposed new clause;
- idempotent sealed run, append-only children, activation/promotion receipts;
- deterministic parser version and table fingerprints.

## Proposed decision-aid contract

Store a sealed, version-bound decision model in PG:

- `decision_model`: clause code, exact clause-version hash, effective interval,
  source notice and source spans, status, version, fingerprints.
- `decision_input`: stable key, label, control type (`checkbox`, `number`,
  `select`), unit, required state, options, display order, source span.
- `decision_rule`: normalized outcome and ordered rule priority.
- `decision_predicate`: input key, comparator, value/unit, group identity, and
  exact source span.
- `decision_outcome`: `meets_threshold`, `does_not_meet_threshold`, or
  `insufficient_information`, plus a deterministic explanation template.

The first model classifies the highest applicable ASCVD risk category from
declared facts, selects its LDL-C starting-treatment threshold, and compares a
numeric LDL-C value. It also asks whether the selected product is one of the
116 Table-2-only products; if yes, this Table-1 assessment is inapplicable and
the UI says so.

Safety boundaries:

- tri-state booleans (`yes`, `no`, `unknown`) rather than unchecked = no;
- missing or contradictory facts produce `insufficient_information`;
- highest-risk-wins priority is explicit and testable;
- user values stay browser-local and are not logged or persisted;
- result wording is “mechanical reading of the selected official rule
  version,” not clinical advice, claim approval, or reimbursement guarantee;
- the result exposes matched, missing, and conflicting predicates and links to
  the exact full clause;
- a future model is labeled with its future effective date and is never
  presented as current.

## Alternatives rejected

1. Overwrite current 2.6.1 immediately: rejected because the new rule is not
   effective until 2026-09-01.
2. Wait for the chapter compilation page: rejected because the official notice
   and amendment attachment are themselves the timely canonical amendment
   source.
3. Put rule logic only in frontend JavaScript: rejected because it would create
   a second unversioned master outside PG.
4. Use an LLM at assessment time: rejected because the rule is deterministic
   and must be reproducible.

## Questions for Pro

1. Is the two-lane current/announced model legally and operationally sound?
2. May the sealed official notice version become effective by date before the
   consolidated chapter page catches up, provided conflicts fail closed?
3. Are the decision states and safety wording adequate?
4. What exact additional invariants are mandatory before live deployment?
