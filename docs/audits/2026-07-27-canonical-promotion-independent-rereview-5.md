# Canonical promotion independent re-review 5

Date: 2026-07-27  
Reviewer role: sixth independent disposable-PostgreSQL adversarial review  
Migration author: no  
Implementation or test changes: none  
Live PostgreSQL apply: **not performed**  
Disposition: **BLOCKED — one reproducible High data-integrity finding**

## Scope and exact reviewed state

This review re-read the four current canonical/promotion migration artifacts,
`2026-07-27-canonical-promotion-independent-rereview-4.md`, and the declared
test suite after the latest remediation. All executable checks used
`initdb`/`pg_ctl` clusters in Python `TemporaryDirectory` paths, random local
Unix sockets, and disposable databases. No configured or live PostgreSQL
endpoint was contacted.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `48ea72b02b2f2c60d741e32db4c9cf2b867ff5d745465ab0ea29130efddab0aa` | 2,754 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `395b77f185445c27c66a7df0278094b36211a0d75abe8f362e3d4f69f7ff2918` | 232 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `8cc8de07b11105add7e5b4dd15629b12840e79bfb8dca0b339198d915cc661b7` | 3,682 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `49f6467083012e38bc13e8c6a0c118da80bba018019a79d92de1d39bbaea970f` | 3,719 |

Hashes and line counts were obtained with:

```text
shasum -a 256 \
  pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql \
  pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql \
  pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql \
  pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql \
  tests/test_canonical_promotion_migrations.py

wc -l \
  pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql \
  pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql \
  pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql \
  pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql \
  tests/test_canonical_promotion_migrations.py
```

## Commands and results

Exact declared-suite command:

```text
PYTHONPATH=src python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 39 tests in 18.746s
OK
```

The bounded regression subset was then rerun explicitly:

```text
PYTHONPATH=src python3 -m unittest -v \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_odf_container_contract_rejects_invalid_zip_variants \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_byte_derived_format_receipts_fail_closed \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_detector_executable_identity_is_bound_at_promotion \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_owner_and_capability_memberships_fail_closed \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_executor_session_identity_must_be_independent \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_roc_year_zero_is_rejected_for_every_date_role \
  tests.test_canonical_promotion_migrations.CanonicalPromotionLiveTests.test_target_multi_event_anchor_replay_promotes
Ran 7 tests in 4.290s
OK
```

Two further exact bounded invocations used inline Python:

```text
PYTHONPATH=src python3 - <<'PY'
# Independently construct the exact rereview-4 90-byte artifact, require
# Python zipfile.ZipFile to reject it, build an otherwise-valid ODT-only case
# in a disposable database, call both SQL inspectors, attempt detector
# registration and promotion, and directly count canonical receipts.
PY
```

Result:

```json
{"artifact_length":90,"artifact_sha256":"b0654966ca98b93046201a56d269a4675b6045604a051dde10502434cb8596ae","independent_oracle":"BadZipFile","promotion_rc":1,"promotion_receipt_count":0,"register_error_class":true,"register_rc":1,"sql_inspect_is_null":"true|true"}
```

The exact adversarial bytes were unchanged from rereview 4:

```text
hex 504b03044e4f542d412d5a49507c6170706c69636174696f6e2f766e642e6f617369732e6f70656e646f63756d656e742e746578747c6e65773a3833303763353364336432303466633938323639316164366431636534653362
```

The independent payload-integrity check used a valid deterministic ODT,
located `content.xml` from its local header, flipped one byte inside its
deflated payload without changing either local or central-directory metadata,
required Python `zipfile` to read the actual entry, and then submitted those
same bytes to both SQL classifiers and the promotion path:

```text
PYTHONPATH=src python3 - <<'PY'
# valid = deterministic ODT with stored mimetype and deflated content.xml
# locate content.xml compressed-data offset from the ZIP local header
# corrupt[payload_offset + compress_size // 2] ^= 0x80
# independently read mimetype and content.xml with Python zipfile
# build otherwise-valid ODT-only disposable-PG case with the corrupted bytes
# query both SQL inspectors, the two receipts, promote, and count receipts
PY
```

Result:

```json
{"artifact_length":477,"artifact_sha256":"bb0a2180d1977496ff49644fa61b752556bc2780e0ff84f921e535fe4b33afb7","compressed_payload_offset":118,"corrupted_entry":"content.xml","oracle_content_read":"BadZipFile:Bad CRC-32 for file 'content.xml'","oracle_zip_open":"opened","promotion_rc":0,"promotion_receipt_count":1,"promotion_stdout":"SET\npromotion:6df72ef5-d24f-4d67-a2b2-82f69da4899b|f|snapshot:6df72ef5-d24f-4d67-a2b2-82f69da4899b|2","sql_inspect_and_receipts":"true|true|1|1"}
```

The existing fixture builder was used only to populate otherwise-valid
promotion evidence. Artifact mutation, ZIP-local-header location, the
full-entry read/CRC oracle, SHA-256, and post-promotion receipt assertion were
performed by the review invocation.

## Bounded regression matrix

| Required case | Result |
|---|---|
| Rereview-4 exact 90-byte non-ZIP ODT false positive | **Closed for the exact case.** Both SQL inspectors returned null; registration and promotion failed; receipt count 0. |
| Malformed central/local directory, truncated EOCD, decoy MIME, compressed mimetype, unsafe path, or missing manifest | Declared regression subset passed. |
| Forged `opaque` PDF/ODT first insert and missing review | Declared regression subset passed fail-closed checks. |
| Detector/reviewer capability and session separation | Declared regression subset passed. |
| Incorrect/replaced verifier evidence or executable identity | Declared regression subset passed the byte-derived and executable-binding checks. |
| ROC year zero in effective/document/publication roles | No regression; all declared roles rejected. |
| Existing canonical A→B followed by candidate B→C | No regression; positive target case promoted. |
| Corrupt deflated ODF payload with intact local/central metadata | **Failed.** Both inspectors accepted it, both receipts were created, and canonical promotion wrote one receipt. |

## Severity summary

- Critical: **0**
- High: **1**
- Medium: **1**
- Low: **2**

## High finding

### H1 — corrupt compressed ODF payload is accepted and promoted

The new detector and reviewer now parse EOCD, central-directory, and local
header structure and require an uncompressed first `mimetype` entry. This
closes rereview 4's exact header-substring false positive.

However, neither implementation inflates compressed entries, computes their
actual CRC-32, verifies the decompressed byte count, or parses the required XML.
The detector only compares CRC and size declarations copied from the local and
central headers (`canonical_v1.sql:1120-1154`) and treats a named
`content.xml` or `META-INF/manifest.xml` with a declared nonzero uncompressed
size as present (`1193-1197`). The reviewer independently traverses the ZIP
layout, but makes the same metadata-only decision (`1373-1458`, `1495-1559`).

The 477-byte artifact above is therefore not readable as an ODF: Python
`zipfile` opens its directory but raises `Bad CRC-32 for file 'content.xml'`
when it reads the required payload. Both database classifiers nevertheless
returned non-null, both byte-bound receipts were inserted, and the otherwise
valid ODT-only case received a canonical promotion receipt.

This is a source-integrity failure at the same promotion boundary as rereview
4 H1. Matching headers prove only that two metadata copies agree; they do not
prove that the referenced compressed bytes are a valid ODF payload.

Required remediation before live apply:

1. boundedly decompress every ZIP entry admitted by the ODF contract;
2. verify actual CRC-32 and decompressed size, not only local/central declared
   values;
3. require well-formed `content.xml` and `META-INF/manifest.xml`, and validate
   the manifest's root media-type relationship needed by the declared ODF
   profile;
4. keep a genuinely independent verifier/library path and bind its
   executable/library identity plus the decompressed-entry manifest to the
   review receipt; and
5. add the exact corrupted-deflate case and corrupt stored-entry CRC/size/XML
   variants to the declared suite, proving zero detector/review/promotion
   receipts.

## Medium finding

### M1 — the declared ZIP regressions do not read entry payloads

The new regression rejects the prior 90-byte input and seven malformed
container/layout variants (`tests/test_canonical_promotion_migrations.py:
2787-2972`). It does not corrupt a compressed payload while leaving header
metadata intact, call `ZipFile.read()`/`testzip()`, or require XML
well-formedness. Consequently all 39 tests passed while the independent corrupt
`content.xml` case promoted.

## Low findings and remaining documented boundaries

### L1 — absent-schema rollback remains non-idempotent

Both rollbacks return only from an inner `DO` block when the managed schema is
absent, then proceed to unconditional schema-qualified locks
(`canonical_v1.rollback.sql:21-25,53-68`;
`promotion_v1.rollback.sql:21-25,46-58`). This fails safely but is not an
absent-schema no-op.

### L2 — allowed capability-login identities remain outside the structural hash

The migration-time membership and session guards remain strict, but the
structural contract still does not bind an immutable enumeration of the
otherwise allowed LOGIN identities. Membership changes can preserve the
structural hash when all members remain allowlisted. Preserve a separate
immutable authorization receipt if this remains outside the migration
contract.

## Gate decision

**BLOCKED.** The exact rereview-4 false-positive fixture is closed, and the
documented role, review, temporal, and A→B→C cases show no bounded regression.
Nevertheless, a reproducibly corrupt required ODF payload passed both current
classifiers and received a canonical promotion receipt.

Do not apply either migration to live PostgreSQL. The packet **may not proceed
to GPT Pro** while this High finding remains open. Remediate H1 and repeat an
independent disposable-PostgreSQL review.
