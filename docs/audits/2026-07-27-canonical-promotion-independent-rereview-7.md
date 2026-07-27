# Canonical promotion independent re-review 7

Date: 2026-07-27  
Reviewer role: eighth independent disposable-PostgreSQL adversarial review  
Migration author: no  
Implementation or test changes: none  
Live PostgreSQL apply: **not performed**

## Exact reviewed state

All executable checks used new `initdb`/`pg_ctl` clusters under Python
`TemporaryDirectory`, random local Unix sockets, and disposable databases. No
configured or live PostgreSQL endpoint was contacted.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `ad74d170dcf4f869d86e9d0b15330cce22d7205a5abcd96b8bc452d6848ce027` | 2,824 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `395b77f185445c27c66a7df0278094b36211a0d75abe8f362e3d4f69f7ff2918` | 232 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `63c68160da7c27717eca6440af0c2f3fc3c61184f216a635ec4cdf4e1c0aa41e` | 3,858 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `61664b4f7e13f2e2a485fe092fd11baa173be62c9e45c3e6a2a86a8b6b871117` | 4,161 |

## Full and bounded suites

```text
PYTHONPATH=src python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 42 tests in 20.777s
OK
```

```text
PYTHONPATH=src python3 -m unittest -v \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_every_pdf_is_observation_only \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_all_format_policies_have_no_promotion_lane \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_integrity_blocker_prevents_every_canonical_change \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_source_declared_deflated_odt_is_blocked_pending_external_verifier \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_every_structural_odt_or_ods_is_observation_only \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_odf_container_contract_rejects_invalid_zip_variants \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_byte_derived_format_receipts_fail_closed \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_detector_executable_identity_is_bound_at_promotion \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_owner_and_capability_memberships_fail_closed \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_executor_session_identity_must_be_independent \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_roc_year_zero_is_rejected_for_every_date_role \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_target_multi_event_anchor_is_staged_but_blocked
Ran 12 tests in 7.624s
OK
```

## Rereview-6 51-byte replay

The exact rereview-6 artifact was reconstructed and independently rejected by
`pypdf.PdfReader(..., strict=True)`:

```text
hex 255044462d4e4f542d412d5044467c6e65773a3433663262623138363639663461643861323164613462393935653463653631
SHA-256 84bcb02e1928fef8c2b1e04a0584051f8af75c5cc8a13cdc98dfed7f18d421ab
length 51
```

The same bytes were then supplied to a separately created otherwise-valid
`pdf_verified` case in disposable PostgreSQL:

```json
{"artifact_length":51,"artifact_sha256":"84bcb02e1928fef8c2b1e04a0584051f8af75c5cc8a13cdc98dfed7f18d421ab","blocker_present":true,"independent_oracle":"PdfReadError:EOF marker not found","observation":"application/pdf|application/pdf|false|false|false|blocked_pending_external_pdf_integrity_verifier","promotion_rc":1,"promotion_receipt_count":0}
```

Disposition: **rereview-6 H1 is closed at the promotion boundary.** Both
classifiers still preserve an observation that the bytes have PDF magic, but
both evidence objects state `pdf_integrity_verified=false` and
`promotion_eligible=false`. Promotion fails with the exact external-PDF
integrity blocker and writes no canonical receipt.

## Independent three-policy no-lane check

An independent disposable-database invocation constructed otherwise-valid
cases for each admitted format policy, invoked `promote_case`, and directly
counted canonical receipts:

```json
{"odt_pdf_verified":{"blocker":"archive","promotion_rc":1,"promotion_receipt_count":0},"pdf_verified":{"blocker":"pdf","promotion_rc":1,"promotion_receipt_count":0},"source_declared_odt_only":{"blocker":"archive","promotion_rc":1,"promotion_receipt_count":0}}
```

The three policy results agree with the migration's explicit fail-closed
boundary:

| Format policy | Result | Canonical receipt |
|---|---|---:|
| `odt_pdf_verified` | blocked pending external archive-integrity verifier | 0 |
| `source_declared_odt_only` | blocked pending external archive-integrity verifier | 0 |
| `pdf_verified` | blocked pending external PDF-integrity verifier | 0 |

The prior 90-byte non-ZIP, corrupt deflated ODT, role/session separation,
missing/forged review, replaced executable, ROC-year-zero, and multi-event
chain cases showed no bounded regression. The multi-event chain is now staged
but intentionally cannot cross the format-integrity blocker.

## C/H/M/L gate

- Critical: **0**
- High: **0**
- Medium: **1**
- Low: **2**

### M1 — the packet has no positive canonical-promotion lane

All three admitted format policies are intentionally fail closed. ODT/ODS
requires a future external archive-integrity verifier; PDF requires a future
external full-document PDF-integrity verifier. The packet contains neither
external receipt schema nor an admission path for such receipts.

This safely closes the prior data-integrity findings, but the migration cannot
yet demonstrate a successful real promotion, idempotent successful replay, or
positive multi-event cutover. `docs/gap-register.md` now documents the same
boundary. Do not treat the green suite as proof that canonical promotion is
operational.

### L1 — absent-schema rollback remains non-idempotent

Both rollbacks return only from an inner `DO` block and then reach
schema-qualified `LOCK TABLE` statements when the schema is absent. They fail
safely but are not absent-schema no-ops.

### L2 — allowed capability-login identities remain outside the structural hash

The session and membership checks remain strict, but the structural contract
does not bind an immutable enumeration of otherwise allowed LOGIN members.

## Gate decision

C/H/M/L = **0/0/1/2**. The local Critical/High security gate is clean for this
exact packet, including the rereview-6 51-byte fixture and all three format
policies. The packet may proceed to GPT Pro for the bounded external-verifier
architecture review.

Live apply remains **blocked**: no format policy has a positive promotion lane,
and successful migration/promotion/rollback acceptance cannot be completed
until an independently reviewed external full-document verifier receipt path
exists.
