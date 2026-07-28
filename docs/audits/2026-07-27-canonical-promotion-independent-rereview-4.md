# Canonical promotion independent re-review 4

Date: 2026-07-27  
Reviewer role: fifth independent disposable-PostgreSQL adversarial review  
Migration author: no  
Implementation or test changes: none  
Live PostgreSQL apply: **not performed**  
Disposition: **BLOCKED — one reproducible High data-integrity finding**

## Scope and exact reviewed state

This review re-read the four current canonical/promotion migration artifacts
and the declared test suite after the remediation recorded in
`2026-07-27-canonical-promotion-independent-rereview-3.md`. All executable
checks used `initdb`/`pg_ctl` clusters in Python `TemporaryDirectory` paths
with random local Unix sockets and databases. No configured or live
PostgreSQL endpoint was contacted.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `4966368264411202cebd12bb9cab6f37251a6e607f36a740b8536ce68fb5e9b5` | 2,028 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `61af5b0a50b373cdafbd22cb12c545b352111feefa1dba7f62c1ab3fa6a6b3ae` | 228 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `1cd4fde00f9b1e2785724785a6a4e38765f9fa8cf3bcc7977f6f3b11b36a3945` | 3,662 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `5757ae9aedd675268f0a09bb26027bc34ad2f42a394e34df9fd7c9f2e1a45386` | 3,482 |

## Commands and results

Exact bounded declared-suite command:

```text
PYTHONPATH=src python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 38 tests in 18.099s
OK
```

The suite included and passed the three ROC-year-zero roles and the positive
A→B→C target case:

```text
test_roc_year_zero_is_rejected_for_every_date_role ... ok
test_target_multi_event_anchor_replay_promotes ... ok
test_executor_session_identity_must_be_independent ... ok
test_byte_derived_format_receipts_fail_closed ... ok
test_detector_executable_identity_is_bound_at_promotion ... ok
```

Two further exact bounded invocations used inline Python over the same test
module's disposable-cluster and otherwise-valid fixture builder:

```text
PYTHONPATH=src python3 - <<'PY'
# Create valid cases, then independently mutate only the review boundary:
# (1) overlapped detector capability login;
# (2) missing review;
# (3) self-consistent incorrect review plus recomputed dataset/parity
#     inventory fingerprints;
# (4) CREATE OR REPLACE the independent verifier function.
# Call promote_case and directly count canonical promotion receipts.
PY
```

Result:

```json
{
  "overlap_session_register": {"rc": 1},
  "writer_session_attest": {"rc": 1},
  "missing_review": {"rc": 1, "receipt": "0"},
  "incorrect_review_rehashed": {"rc": 1, "receipt": "0"},
  "replaced_verifier": {"rc": 1, "receipt": "0"}
}
```

For the incorrect-review case, the review row was changed under temporarily
disabled append-only triggers, its evidence and receipt hashes were recomputed,
both stored inventory fingerprints were recomputed, and all guards were
restored before `promote_case`. This prevents a stale fingerprint from being
the only rejection reason.

The final independent classification check replaced the fixture's ODF
constructor with bytes that begin with ZIP local-file magic and contain the ODT
MIME string in the first 512 bytes, but are not a ZIP archive:

```text
PYTHONPATH=src python3 - <<'PY'
fake = (
  b"PK\x03\x04NOT-A-ZIP|"
  b"application/vnd.oasis.opendocument.text|"
  + ("new:" + token).encode()
)
# The independent oracle is zipfile.ZipFile(...), followed by detector,
# reviewer, promotion, and direct receipt queries.
PY
```

Exact 90-byte adversarial artifact:

```text
SHA-256 b0654966ca98b93046201a56d269a4675b6045604a051dde10502434cb8596ae
hex 504b03044e4f542d412d5a49507c6170706c69636174696f6e2f766e642e6f617369732e6f70656e646f63756d656e742e746578747c6e65773a3833303763353364336432303466633938323639316164366431636534653362
```

Result:

```json
{
  "independent_oracle": "BadZipFile",
  "detectors": "application/vnd.oasis.opendocument.text|application/vnd.oasis.opendocument.text",
  "promotion_rc": 0,
  "promotion_stdout": "SET\npromotion:8307c53d-3d20-4fc9-8269-1ad6d1ce4e3b|f|snapshot:8307c53d-3d20-4fc9-8269-1ad6d1ce4e3b|2",
  "receipt": "1"
}
```

## Required-case disposition

| Required case | Result |
|---|---|
| Prior forged `opaque` PDF/ODT first insert | **Closed for that exact path.** Capability logins have no table INSERT; known PDF/ZIP magic cannot satisfy the `opaque` check; registration binds supplied bytes to artifact SHA/length and derives the receipt. |
| Detector/reviewer role and session separation | **Closed.** Functions test `SESSION_USER`, reject capability overlap, require distinct producer/reviewer identities, and expose EXECUTE only to the corresponding capability. Independent overlap/same-lane attempts failed. |
| Missing format review | **Closed.** Promotion failed; canonical receipt count 0. |
| Incorrect, internally rehashed format review | **Closed.** Promotion failed even after both inventory hashes were updated; canonical receipt count 0. |
| Replaced review implementation | **Closed.** Stored executable identity no longer matched `pg_get_functiondef`; promotion failed; canonical receipt count 0. |
| ROC year zero in effective/document/publication roles | **No regression.** All three declared live cases rejected with zero receipts. |
| Existing A→B then candidate B→C | **No regression.** Declared disposable-PG case promoted and asserted the A→B→C snapshot/effect chain. |

## Severity summary

- Critical: **0**
- High: **1**
- Medium: **1**
- Low: **2**

## High finding

### H1 — non-ZIP bytes are accepted and promoted as ODT

Both supposedly independent classifiers implement the same weak ODF test:
the bytes must start `PK\x03\x04`, and the ODT MIME string merely needs to
occur somewhere in the first 512 bytes
(`canonical_v1.sql:928-947` and `1113-1129`). Neither classifier parses a ZIP
central directory, reads the `mimetype` member, verifies that it is the first
uncompressed entry, or validates an ODF container. The table checks then accept
the two matching declarations (`canonical_v1.sql:635-642`).

The independent oracle rejected the 90-byte fixture as `BadZipFile`, while
both database functions classified it as
`application/vnd.oasis.opendocument.text`. An otherwise-valid ODT-only case
then promoted successfully and wrote one canonical receipt. Promotion's
inventory and executable-hash checks (`promotion_v1.sql:2632-2781`) correctly
prove that both recorded functions agreed; they do not prove the shared format
predicate is correct.

This is the same data-integrity class as the prior H1 at a narrower predicate:
the false first insert is now closed, but two byte-bound receipts can still
jointly attest a non-ODT artifact as ODT and satisfy the canonical gate.

Required remediation before live apply:

1. parse the actual ZIP container in a bounded, deterministic detector;
2. require the ODF `mimetype` entry and exact media-type value under a documented
   ODF container contract, including relevant placement/compression rules;
3. make the independent verifier use a genuinely independent implementation or
   library, not the same substring predicate expressed with different SQL
   functions;
4. bind both executable/library identities and raw parse manifests to the
   receipts; and
5. add this invalid-ZIP false-positive case, plus malformed ZIP and decoy MIME
   string cases, to the declared regression suite and prove receipt count 0.

## Medium finding

### M1 — the regression suite does not exercise classifier false positives

The expanded 38-test suite now covers direct forged-row rejection, known-magic
`opaque` rejection, incorrect input bytes, distinct capabilities, independent
review, and executable replacement. Those are real improvements.

Its positive ODF fixtures are valid ZIP/ODF containers, however, and its
adversarial cases ask whether known PDF/ODF bytes can be called `opaque`. It
never asks the reverse question: whether arbitrary or malformed bytes can be
called ODF by both implementations. The entire suite passed while the
independent `BadZipFile` case promoted.

## Low findings

### L1 — absent-schema rollback remains non-idempotent

Both rollbacks return only from an inner `DO` block when the managed schema is
absent, then continue to unconditional schema-qualified `LOCK TABLE`
statements (`canonical_v1.rollback.sql:21-25,53-68`;
`promotion_v1.rollback.sql:21-25,46-58`). This fails safely but is not a no-op
absent-schema rollback.

### L2 — allowed capability-login identities remain outside the structural hash

The migration-time membership guards are strict, but the structural contract
lines still store only forbidden-member and outgoing-membership counts, not the
identities of allowed LOGIN members (`canonical_v1.sql:73-118`;
`promotion_v1.sql:103-144`). Membership changes can therefore preserve the
structural hash when every member remains allowed. This is bounded by the
session checks and allowlist comments, but an immutable authorization receipt
remains advisable.

## Gate decision

**BLOCKED.** The exact prior forged-`opaque` path is closed, role/session
separation is enforced, missing/incorrect/replaced reviews fail closed, and
ROC-year-zero plus A→B→C behavior have not regressed. Nevertheless, a
reproducible non-ZIP artifact passed both current classifiers and received a
canonical promotion receipt.

Because any Critical or High finding blocks the gate, do not apply these
migrations to live PostgreSQL and do not proceed to GPT Pro. Remediate H1 and
repeat an independent disposable-PG review.
