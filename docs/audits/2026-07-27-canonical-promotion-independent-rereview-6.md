# Canonical promotion independent re-review 6

Date: 2026-07-27  
Reviewer role: seventh independent disposable-PostgreSQL adversarial review  
Migration author: no  
Implementation or test changes: none  
Live PostgreSQL apply: **not performed**  
Disposition: **BLOCKED — one reproducible High data-integrity finding**

## Exact reviewed state

All executable checks used new `initdb`/`pg_ctl` clusters under Python
`TemporaryDirectory`, random local Unix sockets, and disposable databases. No
configured or live PostgreSQL endpoint was contacted.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `25c425394b859b275cf35d290b30ddb11e63a358ac98b84c087eff9acaf464f2` | 2,808 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `395b77f185445c27c66a7df0278094b36211a0d75abe8f362e3d4f69f7ff2918` | 232 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `89d6edc63d489c48a2ff678604299d797574c546b7e08bf4e6d46f4b09285876` | 3,835 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `ef7d4e3f6c348abad11a133f3fd7b041db4b16bdea19efba4df9fae75dc16d95` | 4,011 |

## Full and bounded suites

```text
PYTHONPATH=src python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 40 tests in 20.021s
OK
```

```text
PYTHONPATH=src python3 -m unittest -v \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_source_declared_deflated_odt_is_blocked_pending_external_verifier \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_every_structural_odt_or_ods_is_observation_only \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_odf_container_contract_rejects_invalid_zip_variants \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_byte_derived_format_receipts_fail_closed \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_detector_executable_identity_is_bound_at_promotion \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_owner_and_capability_memberships_fail_closed \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_executor_session_identity_must_be_independent \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_roc_year_zero_is_rejected_for_every_date_role \
 tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_target_multi_event_anchor_replay_promotes
Ran 9 tests in 5.832s
OK
```

## Prior failing-case replay

The rereview-5 artifact was reconstructed exactly: a valid deterministic ODT
with one byte flipped inside deflated `content.xml`, without changing either
local or central-directory metadata.

```json
{"artifact_length":477,"artifact_sha256":"bb0a2180d1977496ff49644fa61b752556bc2780e0ff84f921e535fe4b33afb7","blocked_reason_present":true,"compressed_payload_offset":118,"corrupted_entry":"content.xml","oracle_content_read":"BadZipFile:Bad CRC-32 for file 'content.xml'","oracle_zip_open":"opened","promotion_rc":1,"promotion_receipt_count":0,"sql_inspect_receipts_eligible":"true|true|1|1|false"}
```

Disposition: **rereview-5 H1 is closed at the promotion boundary.** The two
structural inspectors still admit observation receipts, but every ODT/ODS is
explicitly marked non-promotion-eligible and promotion rejects it with
`blocked_pending_external_archive_integrity_verifier`. The exact adversarial
case wrote no canonical receipt.

The earlier 90-byte non-ZIP fixture, role/session separation, missing/forged
review, replaced executable, ROC-year-zero roles, and A→B→C positive case also
passed the bounded suite without regression.

## Independent PDF classification case

The current default positive promotion fixture now uses the new
`pdf_verified` policy. Both format functions classify PDF solely from the first
five bytes `%PDF-` (`canonical_v1.sql:1734-1744` and `1898-1904`).
Promotion rejects every ODT/ODS but does not perform an equivalent PDF
structure/integrity gate (`promotion_v1.sql:2778-2852`).

An independent inline harness replaced only the otherwise-valid fixture's PDF
bytes with:

```text
hex 255044462d4e4f542d412d5044467c6e65773a3433663262623138363639663461643861323164613462393935653463653631
SHA-256 84bcb02e1928fef8c2b1e04a0584051f8af75c5cc8a13cdc98dfed7f18d421ab
length 51
```

It then used `pypdf.PdfReader(..., strict=True)` as an independent parser,
submitted the same bytes to the two database functions, promoted the
otherwise-valid case, and directly counted canonical receipts.

```json
{"artifact_length":51,"artifact_sha256":"84bcb02e1928fef8c2b1e04a0584051f8af75c5cc8a13cdc98dfed7f18d421ab","detectors":"application/pdf|application/pdf|pdf-magic","independent_oracle":"PdfReadError:EOF marker not found","promotion_rc":0,"promotion_receipt_count":1,"promotion_stdout":"SET\npromotion:43f2bb18-669f-4ad8-a21d-a4b995e4ce61|f|snapshot:43f2bb18-669f-4ad8-a21d-a4b995e4ce61|2"}
```

## C/H/M/L gate

- Critical: **0**
- High: **1**
- Medium: **2**
- Low: **2**

### H1 — `%PDF-` prefix alone can receive a canonical promotion receipt

The 51-byte artifact has no valid PDF version header, cross-reference data,
trailer, catalogue, pages tree, or EOF marker. The independent parser rejects
it, while both database classifiers record matching verified PDF receipts and
the `pdf_verified` path promotes it.

This is the same source-integrity class as the prior ODF findings. Byte binding,
independent identities, executable hashes, and matching receipt hashes do not
establish PDF validity when both implementations share the same five-byte
predicate.

Required remediation:

1. parse the complete PDF with a bounded deterministic implementation;
2. require a valid header/version, readable cross-reference/trailer chain,
   catalogue/pages structure, and terminal EOF according to the declared PDF
   profile;
3. use a genuinely independent parser for review;
4. bind parser/library identity and a structural parse manifest to both
   receipts; and
5. add this exact 51-byte fixture plus truncated, bad-xref, missing-trailer,
   missing-catalogue, and appended-decoy cases, proving zero canonical receipts.

### M1 — the declared suite has no invalid-PDF positive-classification oracle

All 40 declared tests pass because every `pdf_verified` positive case uses the
same deterministic valid-PDF helper. Existing adversaries test PDF hidden as
`opaque`, not the reverse question: whether arbitrary `%PDF-` bytes can be
declared PDF. The suite therefore remained green while the malformed artifact
promoted.

### M2 — ODF promotion is a documented fail-closed dead end

The current migration safely closes rereview-5 by making every detected ODT or
ODS observation-only, including stored-entry containers. No external archive
integrity receipt schema or promotion-admission path exists in this packet.
This is not an integrity bypass, but `source_declared_odt_only` cannot progress
to canonical promotion until that separately reviewed path is implemented.

### L1 — absent-schema rollback remains non-idempotent

Both rollbacks return only from an inner `DO` block and then reach
schema-qualified `LOCK TABLE` statements when the schema is absent. They fail
safely but are not absent-schema no-ops.

### L2 — allowed capability-login identities remain outside the structural hash

The session and membership checks remain strict, but the structural contract
does not bind an immutable enumeration of otherwise allowed LOGIN members.

## Gate decision

**BLOCKED.** C/H/M/L = **0/1/2/2**. The rereview-5 corrupt-ODF case is closed
and the bounded/full suites pass, but the independent malformed-PDF case
received a canonical promotion receipt. Do not apply the migrations to live
PostgreSQL and do not proceed to GPT Pro until H1 is remediated and independently
re-reviewed.
