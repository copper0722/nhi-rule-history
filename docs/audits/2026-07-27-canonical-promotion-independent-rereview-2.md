# Canonical promotion independent re-review 2

Date: 2026-07-27  
Reviewer role: third independent adversarial review  
Live PostgreSQL apply: **not performed**  
Implementation changes: **none**  
Disposition: **BLOCKED FOR LIVE DEPLOYMENT**

## Scope and reviewed artifacts

This review re-read the current forward migrations, guarded rollbacks, and
promotion test suite. It then ran the declared suite and separate disposable
PostgreSQL adversaries. A green author suite was treated as a lead, not as
independent evidence.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `e0a45a537bea891aec6f203b4de019e9a90388173ab2cee6d666d3f71054c947` | 1,192 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `3bdad50498387e9ff4726dc05566cfc9081c332056b30103ae4b770a261ac3a6` | 187 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `92ced5f087cc60b5c6ecd9523b98d092cabe8c85b5b8be2b7179a7be4509609d` | 3,411 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `fa1be6ab2cfb7b013068ee301b7bff4d1d43f8e738f7b572e1b890d8fdd5c341` | 2,610 |

The declared disposable-PostgreSQL suite passed:

```text
python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 34 tests in 12.565s
OK
```

## Independent adversarial results

### Exact rerun of the three former successful bypasses

Each case was created independently in a disposable database and promoted
without relying on the test method's assertions.

| Former bypass | Promotion result | Canonical receipts | Disposition |
|---|---:|---:|---|
| Quarantined supporting PDF in exhaustive inventory | rejected | 0 | closed |
| Changed companion clause with no accepted event | rejected | 0 | closed |
| Future `effective_date_raw` normalized to a past date | rejected | 0 | closed |

The respective failure gates were the declared-format/parity check, cumulative
replay check, and parsed-future-date check. The prior three High findings are
therefore closed for the reviewed SQL.

### Role-to-MIME and exact-cardinality matrix

For retrofit adversaries, append-only triggers were disabled only while
constructing the reusable fixture state and were restored before independent
review and promotion. Promotion's structural trigger seal was therefore active
at the decision boundary.

| Case | Promotion result | Canonical receipts |
|---|---:|---:|
| Valid one-ODT plus one-PDF policy | accepted | 1 |
| Valid ODT-only policy | accepted | 1 |
| `official_pdf` with `application/octet-stream` | rejected | 0 |
| `official_odt` with `application/octet-stream` | rejected | 0 |
| `official_ods` with `application/octet-stream` | rejected | 0 |
| ODT/PDF policy with a second exact ODT MIME | rejected | 0 |
| ODT/PDF policy with a second exact PDF MIME | rejected | 0 |
| ODT-only policy with a second exact ODT MIME | rejected | 0 |
| ODT-only policy with an exact PDF MIME under `supporting` | rejected | 0 |
| ODT-only policy with a `.pdf` supporting artifact recorded as `application/octet-stream` | **accepted** | **1** |

The new stored-value invariants work as written
(`promotion_v1.sql:2544-2575`, `2653-2745`). The last row exposes the
remaining byte-provenance boundary described in H1.

### Date parser, raw/locator equality, and temporal gates

| Case | Promotion result | Canonical receipts |
|---|---:|---:|
| Exact valid Gregorian source text | accepted | 1 |
| Exact valid ROC source text | accepted | 1 |
| Gregorian impossible day | rejected | 0 |
| Gregorian raw/normalized mismatch | rejected | 0 |
| ROC impossible month | rejected | 0 |
| Effective-date locator mismatch | rejected | 0 |
| Future document-date raw normalized to a past date | rejected | 0 |
| `中華民國0年1月1日` normalized as `1911-01-01` | **accepted** | **1** |

The artifact hash, exact locator, raw text, calendar, parser version, and
normalized date are now tied by the parse fingerprint
(`promotion_v1.sql:1753-1780`, `1824-1875`). M1 remains because the SQL ROC
parser admits year zero.

### Anchor-wide change coverage

The following independent or declared-live cases now fail closed:

- changed non-target anchor member with no accepted event;
- verified canonical change inside the anchor interval but omitted from replay;
- arbitrary event count or stream fingerprint;
- broken replay chain;
- target-only anchor and partial rule-set replay.

The changed-member guard at `promotion_v1.sql:2374-2399` closes the former
eventless-companion bypass. M2 is a separate false-negative limitation for a
target rule that already has an accepted event within the anchor interval.

## Severity summary

- Critical: **0**
- High: **1**
- Medium: **3**
- Low: **2**

## High finding

### H1 — format policy is bound to a supplied MIME string, not to artifact bytes

The promotion function fingerprints the complete linked inventory, including
verification status (`promotion_v1.sql:2499-2527`), rejects quarantined rows
(`2544-2551`), enforces official-role-to-MIME mappings (`2552-2575`), and
counts exact ODT/PDF MIME values (`2657-2673`, `2709-2745`). These are material
improvements.

The canonical `source_artifact` row nevertheless stores only a supplied
`media_type`, SHA-256, and metadata. It has no immutable detector receipt that
binds:

```text
artifact bytes + detector code/version -> detected media type
```

Consequently, an ODT-only case containing a release-linked `.pdf` artifact
under `supporting`, but recorded as `application/octet-stream`, promoted
successfully after the inventory count and fingerprint were made exact. The
database has no artifact bytes or byte-derived format receipt with which to
disprove the supplied MIME value.

The acquisition implementation is magic-first for the normal leading
signatures (`src/nhi_rule_history/fetch/runner.py:30-69`), which reduces the
production likelihood. It does not seal that derivation into the canonical or
promotion contract, and the canonical bootstrap owner can insert an internally
consistent but wrongly typed row.

Before live deployment, either:

1. bind every canonical source artifact to an immutable, versioned,
   byte-derived detector receipt and make promotion verify it; or
2. perform and preserve an independently audited bootstrap receipt proving
   every linked artifact's SHA, bytes, detector version, detected MIME, role,
   and exhaustive release membership, with no alternate loader path.

Add a regression in which a real PDF byte fixture is presented with a false
header/declared MIME. A filename-only assertion is insufficient; the oracle
must inspect bytes independently.

## Medium findings

### M1 — ROC year zero is accepted as a legal calendar date

All three SQL ROC branches accept `[0-9]{1,3}` and add 1911 without requiring
the ROC year to be at least one
(`promotion_v1.sql:1273-1289`, `1299-1315`, `1325-1341`).

Observed adversary:

```text
raw source text: 中華民國0年1月1日
calendar:        roc
normalized date: 1911-01-01
result:          promotion committed
```

This disagrees with the repository's existing deterministic source parser,
which explicitly rejects `roc_year < 1`
(`src/nhi_rule_history/annotation_stage.py:87-96`). Apply the same lower bound
to effective, document, and publication dates and add zero-year negative tests
for all three roles.

### M2 — whole-anchor replay cannot represent a target with a prior accepted event in the interval

For the target rule, the anchor check requires the pre-anchor text/hash to equal
the immediate canonical predecessor
(`promotion_v1.sql:2036-2038`). The all-member replay result simultaneously
must begin at that pre-anchor hash (`2424-2448`).

The accepted-event chain can include earlier canonical events for the target,
ending with the candidate (`2101-2245`). If such an earlier event changes
`A -> B` and the candidate changes `B -> C`, whole-anchor replay must record
`before=A, after=C`. The candidate-specific result check instead requires that
same single result row to record `before=B, after=C`
(`2477-2488`). Both cannot be true when `A <> B`.

This fails closed rather than corrupting canonical state, but it means the
claimed cumulative replay cannot promote a legitimate target whose pre/post
anchor interval contains an earlier accepted event. Resolve the model by
separating:

- whole-anchor start/end result (`A -> C`); and
- candidate-edge predecessor/result (`B -> C`).

Add a positive two-event target test. The current same-order collision test is
not evidence that a valid multi-event target chain can promote.

### M3 — the test oracle still mirrors the implementation at key semantic gates

The suite is materially stronger than the prior version, but it remains
implementation-coupled:

- PostgreSQL JSONB fingerprint rendering is reproduced by the Python helper
  (`tests/test_canonical_promotion_migrations.py:62-77`);
- date parse fingerprints are built from the caller-supplied normalized date,
  not from an independent parser
  (`tests/test_canonical_promotion_migrations.py:673-701`);
- the inventory “byte-derived” expected value is manually reconstructed in the
  same seven-field shape used by SQL
  (`tests/test_canonical_promotion_migrations.py:1946-1987`);
- there are no declared negative tests for all three role-to-MIME mappings,
  duplicate exact ODT/PDF cardinality, a MIME-disguised PDF, ROC year zero, or
  a positive target chain with an earlier accepted event.

The third-party adversaries above found two cases absent from the 34 passing
tests. Add an independently implemented calendar oracle, a real byte manifest
oracle, and a positive/negative cumulative replay matrix.

## Low findings

### L1 — absent-schema rollback guards still fall through to missing-table locks

Both rollback scripts return from their schema guard when the managed schema is
absent:

- `canonical_v1.rollback.sql:21-25`
- `promotion_v1.rollback.sql:21-25`

Execution then continues to locks that name the absent schema:

- `canonical_v1.rollback.sql:53-66`
- `promotion_v1.rollback.sql:46-58`

Independent execution against an empty disposable database returned exit code
3 for both scripts with `schema ... does not exist`. This is fail-safe, not
destructive, but the apparent idempotent path is not real.

### L2 — allowed capability-login identities are still absent from the structural hash

The role guard rejects unsafe membership and requires explicit constrained
LOGIN allowlists (`canonical_v1.sql:345-416`). The structural seal records only
the count of forbidden inbound memberships and outgoing membership counts, not
the identities of otherwise allowed login members
(`canonical_v1.sql:73-114`, `986-1027`; equivalent promotion seal).

Adding or removing an otherwise allowlisted login can therefore preserve the
contract hash. This remains acceptable only if membership identity and grant
changes have a separate immutable authorization/audit receipt.

## Prior finding disposition

| Prior finding | Current disposition |
|---|---|
| Quarantined supporting PDF could promote | **Closed.** Every linked quarantined artifact now blocks promotion. |
| Changed eventless companion could promote | **Closed.** Changed non-target members require an accepted canonical event in the anchor interval. |
| Future effective raw text could normalize to past | **Closed.** Raw text is parsed inside the function, future parsed dates fail, and the parse receipt is source-bound. |
| Official role-to-MIME mismatch | **Closed for stored MIME values.** H1 remains the unsealed byte-to-MIME boundary. |
| Exact ODT/PDF cardinality | **Closed for exact stored MIME values.** Independent duplicate ODT/PDF cases fail. |
| Forced ISO raw text | **Closed.** Exact ROC source text is preserved and valid ROC dates promote. M1 is the year-zero edge. |
| Changed-member anchor coverage | **Closed for the former bypass.** M2 is a separate multi-event target usability defect. |

## Live-deployment gate

**Do not apply these migrations to the live canonical schema yet.**

The gate remains blocked by H1 and the unresolved M1/M2 correctness contract.
In addition, this migration review does not establish source-universe closure,
per-clause historical completeness, or current-source parity. The repository's
existing history-marker preflight explicitly records
`official_source_universe_closed=false`,
`per_clause_history_complete=false`, and
`canonical_history_written=false`; none of those content gates was changed by
this SQL re-review.

Required before a live apply:

1. seal and independently audit byte-derived artifact format/type provenance;
2. reject ROC year zero consistently in every date lane;
3. define and test a promotable target chain containing an earlier accepted
   event, or explicitly narrow and document the supported anchor contract;
4. add independent MIME/cardinality/date/replay oracles;
5. rerun source-universe, current-source, attachment-denominator, rule-identity,
   effective-date, adjacency, and per-clause history closure audits against the
   exact bootstrap payload;
6. obtain a final independent review of the exact migration and bootstrap
   hashes immediately before any atomic live apply.

Author-reported `34/34` remains useful regression evidence, but it is not a
live-deployment authorization or a history-completeness claim.
