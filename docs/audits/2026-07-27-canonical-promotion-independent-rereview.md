# Canonical promotion independent re-review

Date: 2026-07-27  
Reviewer role: independent adversarial review  
Live PostgreSQL apply: **not performed**  
Disposition: **BLOCKED FOR LIVE DEPLOYMENT**

## Reviewed artifacts

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `e0a45a537bea891aec6f203b4de019e9a90388173ab2cee6d666d3f71054c947` | 1,192 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `3bdad50498387e9ff4726dc05566cfc9081c332056b30103ae4b770a261ac3a6` | 187 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `f231dd9195390bf7d1795999c127e86081030686239b993b9edb7fb6204ea6ba` | 3,147 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `7558ab9960d43c285226fc1402a4e831ba4897e25ae1eef57e50b76032d2efaa` | 2,371 |

The disposable-PostgreSQL suite passed **32/32** tests:

```text
python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 32 tests in 12.512s
OK
```

That green suite does not close the gate. Three independently constructed
states that meet the current stored contracts still promoted successfully.
Trigger disabling in the one-off harness was used only to retrofit the reusable
fixture; all triggers were restored before the independent ready decision and
promotion. Each final state can be inserted in that form by the normal
bootstrap/evidence producers because the stated missing constraints do not
exist.

## Severity summary

- Critical: **0**
- High: **3**
- Medium: **2**
- Low: **2**

## High findings

### H1 — `exhaustive_verified` inventory can contain an unresolved official PDF

The remediation now counts and fingerprints every `release_artifact`, including
`supporting` rows (`promotion_v1.sql:2294-2321`), and reconciles both the
release and parity receipt to that count and fingerprint
(`promotion_v1.sql:2323-2337`). It also rejects any PDF role or exact
`application/pdf` media type in the ODT-only path
(`promotion_v1.sql:2449-2481`). This closes the original hidden-PDF ODT-only
test case.

It does **not** require every inventoried artifact to be non-quarantined. The
canonical artifact contract explicitly permits `verification_status =
'quarantined'` (`canonical_v1.sql:492-497`), while the inventory aggregate does
not include or filter `verification_status` (`promotion_v1.sql:2294-2321`).
Only the selected declaration, ODT, and one selected PDF are required to be
full-text verified (`promotion_v1.sql:2360-2447`).

Disposable adversary:

1. Start from a valid `odt_pdf_verified` case.
2. Link a second `application/pdf` artifact as `supporting` with
   `verification_status='quarantined'`.
3. Recompute the declared count and current all-row inventory fingerprint in
   both the release and parity receipt.
4. Restore all triggers, obtain the independent ready transition, and execute
   promotion.

Observed result: return code `0`; a canonical promotion receipt and new
snapshot were committed.

This makes `exhaustive_verified` a self-asserted label even when a known
official attachment is unresolved. Before deployment, promotion must reject
every release-linked quarantined artifact and define, then enforce, which of
multiple ODT/PDF artifacts contain the promoted clause and therefore require
full-text/parity verification. The byte-derived inventory receipt remains an
external prerequisite.

### H2 — a changed no-event rule bypasses running replay

The new chain check is real for rules that have accepted events. It partitions
accepted events by `rule_id`, orders them by `authoritative_order`, checks every
`before` against the prior `after` or pre-anchor hash, and checks the last
`after` against the post anchor (`promotion_v1.sql:2097-2165`). Duplicate
per-rule authoritative order is also rejected
(`promotion_v1.sql:2166-2194`).

However, that CTE contains only rules present in the accepted event set. For a
rule with no accepted event, the function checks only a producer-supplied
`replay_rule_result` against the pre and post endpoint rows
(`promotion_v1.sql:2220-2243`). The table-level equality is itself only
`expected_after_raw_sha256 = actual_after_raw_sha256`
(`promotion_v1.sql:636-658`). There is no rule requiring:

```text
no accepted event for rule => pre-anchor hash = post-anchor hash
```

Disposable adversary:

1. Keep the valid candidate event for the target rule.
2. Change the companion rule's post-anchor text/hash.
3. Add no canonical or candidate event for that companion rule.
4. Set its replay result to `before=pre`, `expected_after=post`,
   `actual_after=post`.
5. Recompute the post-anchor rule-set fingerprints, restore all triggers,
   obtain ready, and promote.

Observed result: return code `0`; promotion committed.

This is not cumulative replay. Every anchor member needs a derived execution
path: an exact ordered chain for eventful rules, and a proved no-op for
eventless rules. The function should derive results from the pre anchor and
accepted event rows rather than accept endpoint assertions as execution
evidence.

### H3 — future effective source text can be normalized to a past date and promoted

The function gates only the stored normalized `case_row.effective_from`
against Taiwan today (`promotion_v1.sql:1400-1404`). The effective-date source
check proves that the selected raw span and locator match
`resolution_row.effective_date_raw` and
`resolution_row.effective_date_locator`
(`promotion_v1.sql:1627-1635`), but it never proves that the normalized date was
deterministically parsed from that raw source text. Matching the candidate
proposal compares only the already-normalized date
(`promotion_v1.sql:1344-1349`).

Disposable adversary:

1. Use source-bound effective-date raw text `2026-08-26`.
2. Store `effective_from=2026-07-26`.
3. Keep the raw span, locator, hashes, proposal raw expression, resolution raw
   field, and normalized candidate/case dates mutually consistent under the
   current schema.
4. Restore all triggers, obtain ready, and promote on 2026-07-27.

Observed result: return code `0`; the future raw effective date was promoted
as a past effective date.

This reopens the future-effective gate despite the exact-span remediation. A
promotion-grade date needs a deterministic, versioned parse receipt binding:

```text
artifact hash + exact locator + raw text + calendar system + parser version
    -> normalized Gregorian date
```

The derived date from that receipt, not a separately supplied date, must drive
the Taiwan-today gate and canonical `effective_from`.

## Medium findings

### M1 — document/publication “raw” fields are forced to ISO rather than preserving official text

Document and publication dates now have exact spans and the correct ordering
gate: `document_date <= publication_date <= Taiwan today`
(`promotion_v1.sql:1406-1414`), and each date has a unique source-bound span
(`promotion_v1.sql:1681-1696`). This closes the original arbitrary future
document/publication-date case when the official source literally contains an
ISO date.

The same code requires `*_date_raw = date::text`, however
(`promotion_v1.sql:1408-1411`, `1685-1696`). An actual NHI source date such as
`中華民國115年7月27日` cannot be preserved as raw evidence and satisfy that
contract. Keep official raw text losslessly and bind it to a separate
calendar/parser receipt, as required for H3.

### M2 — the declared tests do not independently exercise the semantic gates

The suite's fingerprint helper intentionally mirrors PostgreSQL JSON rendering
(`tests/test_canonical_promotion_migrations.py:62-68`). The fixture uses
pre-normalized ISO date text and a free-standing literal `1` as the purported
authoritative order (`tests/test_canonical_promotion_migrations.py:626-694`).
Inventory tests cover a hidden PDF only in the ODT-only branch
(`tests/test_canonical_promotion_migrations.py:1771-1813`), and replay tests do
not cover a changed anchor member with zero accepted events.

Add independent negative oracles for all three High findings. At least one date
test should use real ROC source text and a separately implemented expected
normalization. At least one inventory oracle should derive its expected
attachment/status set from a byte-level fixture manifest rather than copying
the SQL aggregate shape.

## Low findings

### L1 — the nominal absent-schema rollback path is not idempotent

Both rollback schema guards return when their schema is absent
(`canonical_v1.rollback.sql:21-25`;
`promotion_v1.rollback.sql:21-25`), but execution then continues to `LOCK
TABLE` statements that name the absent schema
(`canonical_v1.rollback.sql:53-66`;
`promotion_v1.rollback.sql:46-58`). A second rollback therefore fails rather
than behaving as the guard suggests. This is fail-safe, not data-destructive,
but should be made explicit or made idempotent.

### L2 — allowed capability-login membership identities are not in the contract hash

The structural seal records only a count of forbidden memberships
(`canonical_v1.sql:73-114`, `986-1027`;
`promotion_v1.sql:103-144`, `2932-2973`). The role guard correctly rejects all
owner memberships and validates capability members as explicit, constrained
LOGIN allowlists (`canonical_v1.sql:345-416`), so the original direct-owner-DML
path is closed. Adding or removing an otherwise allowlisted capability login
does not change the structural fingerprint. Preserve that operational choice
only if actor grants have a separate immutable authorization receipt and audit
lane.

## Prior five-High disposition

| Prior High | Re-review disposition |
|---|---|
| All release artifacts and declaration reconciliation | **Partially remediated, still High (H1).** All rows are now counted/fingerprinted and ODT-only rejects a hidden exact PDF, but an inventoried quarantined PDF can still promote. |
| True per-rule ordered replay, including same-day order | **Partially remediated, still High (H2).** Eventful rule chains and per-rule authoritative-order uniqueness are enforced; changed eventless rules bypass execution. |
| Relation durability/`relpersistence` seal | **Closed for the reviewed schema.** Both migrations reject non-permanent managed relations and fingerprint persistence, access method, tablespace, and relation options (`canonical_v1.sql:51-63`, `117-135`, `964-1048`; `promotion_v1.sql:79-92`, `146-166`, `2908-2995`). The UNLOGGED and reloptions tests pass. |
| Owner/membership containment | **Closed for the direct canonical-DML threat.** The owner has zero inbound/outbound membership, capability roles cannot inherit other roles, and capability members must be constrained, explicitly commented LOGIN roles (`canonical_v1.sql:345-416`). L2 remains as an auditability issue, not an owner bypass. |
| Exact publication/document/effective evidence and temporal gates | **Partially remediated, still High (H3).** Publication/document spans and ordering are enforced, and the effective locator must match exactly; effective raw text is not deterministically bound to the normalized date. |

## Other reviewed controls

- **Rollback races:** closed for the reviewed paths. Both rollbacks acquire
  `ACCESS EXCLUSIVE` locks before emptiness checks; promotion rollback also
  locks the canonical receipt table
  (`promotion_v1.rollback.sql:46-70`). The authenticated-writer race tests
  pass.
- **Trigger and RLS drift:** closed for the reviewed objects. Trigger enabled
  state, policies, `relrowsecurity`, and `relforcerowsecurity` are in both
  structural seals; disabled-trigger and RLS adversaries fail reapplication.
- **Executor separation:** closed. Producer, reviewer, and executor are
  authenticated `SESSION_USER` identities; the security-definer transaction
  records the executor and rejects identity reuse
  (`promotion_v1.sql:1210-1252`, `2723-2761`).
- **Privileges:** current tables, domains, and functions revoke PUBLIC access;
  owner default privileges cover the object classes currently created
  (`canonical_v1.sql:907-931`;
  `promotion_v1.sql:2807-2851`). No sequence exists in these schemas.
- **Source-bound before/after text:** closed within the stored evidence
  boundary. Both complete clause spans must exactly match the predecessor and
  candidate text/hashes, artifact, release, candidate span, and locator
  (`promotion_v1.sql:1472-1585`).
- **Mapping/publication state:** mapping coverage must equal exactly one, and
  `publishable` remains unrepresentable.

## External bootstrap and source-byte boundary

Even after H1-H3 are fixed, these migrations cannot establish that acquisition
collected every official source or that stored spans actually occur in the
immutable bytes. The repository currently declares `official_source_universe`
open, legal history incomplete, and current whole/chapter parity failed.

Live deployment therefore also requires an external deterministic receipt that
binds:

1. source plan and declared cut;
2. every official detail page and attachment;
3. byte length, media identification, and SHA-256;
4. the all-artifact inventory and per-format clause membership;
5. exact source spans and deterministic date parsing;
6. pre/post anchor manifests and rule-set fingerprints; and
7. source-universe coverage with explicit unresolved gaps.

PostgreSQL may verify that receipt; it cannot manufacture evidence for omitted
bytes.

## Gate verdict

**Do not apply these migrations to live canonical PostgreSQL and do not enable
canonical promotion.** The 32/32 declared tests are real but insufficient.
H1-H3 each have a successful disposable-PostgreSQL promotion counterexample.
The gate may be re-reviewed only after the SQL constraints and independent
tests close all three, followed by a clean-room bootstrap/source-byte audit.
