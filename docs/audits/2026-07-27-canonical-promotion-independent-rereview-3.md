# Canonical promotion independent re-review 3

Date: 2026-07-27  
Reviewer role: fourth independent disposable-PostgreSQL adversarial review  
Migration author: no  
Implementation or test changes: none  
Live PostgreSQL apply: **not performed**  
Disposition: **BLOCKED — local security gate is not clean enough for GPT Pro**

## Scope and exact reviewed state

This review re-read the latest forward migrations, guarded rollbacks, and
declared test suite. It used two disposable PostgreSQL clusters. The existing
fixture builder was used only to create otherwise-valid promotion cases; byte
classification, PDF/ZIP construction, hashes, magic/container inspection, and
post-promotion SQL-state assertions were implemented separately for this
review. A green author suite was not treated as independent evidence.

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.sql` | `a03958210bb2ed2b052c8025760c04e6a5d7b3d3e55b7fb322a694777abf7d35` | 1,350 |
| `pg/migrations/2026-07-28_nhi_rule_history_canonical_v1.rollback.sql` | `43181cbcd5204e1cc47599d35d5b218fe9c5a0b61b9756f34d47b033664a8ea5` | 193 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.sql` | `eed8465ad85c4eb8cf5f4fd5fe9561b24c00384b1e734392e3d067e5c795e87a` | 3,530 |
| `pg/migrations/2026-07-28_nhi_rule_history_promotion_v1.rollback.sql` | `a724dadbf172beba2ed34b8ef4256e1dc443ff342c957ec478678dbba0db8187` | 166 |
| `tests/test_canonical_promotion_migrations.py` | `b921e6198530c747296c174ff4579b2807ecbf4f1157978858de75e8bfe0a851` | 3,244 |

The declared suite passed, but is reported separately from the adversarial
evidence:

```text
PYTHONPATH=src python3 -m unittest -v tests.test_canonical_promotion_migrations
Ran 37 tests in 14.653s
OK
```

## Independent adversarial matrix

| Case | Independent oracle | Promotion / write result | Canonical receipt |
|---|---|---:|---:|
| Real PDF, `supporting.bin`, stored MIME `application/octet-stream`, honest detector says PDF | `%PDF-` magic, terminal `%%EOF`, SHA/length matched the DB row | rejected | 0 |
| Same real PDF, but a self-consistent supplied `opaque` detector receipt | `%PDF-` magic and `%%EOF`; SHA `f4920929c5d4db03cce1e089531c0854db0702ab0c3c601f292ecbb047e5e0ec`, 286 bytes, exactly matched the DB row | **accepted** | **1** |
| Required ODT detector receipt absent | exact release/link denominator still present | rejected | 0 |
| PDF bytes represented by an ODT detected type with `pdf-magic` evidence | independent `%PDF-`/SHA/length oracle | insert rejected; transaction left 0 detection rows | n/a |
| ODT ZIP represented by PDF detected type with `zip-mimetype` evidence | independent ZIP open, first entry/mimetype read as `application/vnd.oasis.opendocument.text` | insert rejected | n/a |
| The same actual ODT ZIP represented by a self-consistent `opaque` receipt | ZIP magic `504b0304`, ODF mimetype read independently; SHA `2140b4f1b365f8f097d6ea980bc4bb432ea3923939904bb48b121d68f53facd6` | **insert accepted** as `application/octet-stream|opaque` | n/a |
| Update an existing detector receipt | before/after receipt hash queried directly | rejected; row unchanged | n/a |
| ROC year zero in effective-date role | raw source text `中華民國0年1月1日` | rejected | 0 |
| ROC year zero in document-date role | same independent invalid-year oracle | rejected | 0 |
| ROC year zero in publication-date role | same independent invalid-year oracle | rejected | 0 |
| Existing canonical event A→B followed by candidate B→C | independent SHA chains and direct snapshots/effects/head queries | accepted | 1 |

The successful multi-event case had this exact state transition:

```text
before:
  head generation 2 at B
  1 canonical effect
  2 replay events
  whole-anchor replay result A -> C

after:
  head generation 3 at C
  effects A -> B | B -> C
  1 promotion receipt
```

The independent hashes were:

```text
A 0dc9e306db2ee94d218b7faa87e0f1c90bd3b9cf3405180a7fc308af1695c000
B 22fa143a235f3f02dff446c1dea0f6c5f19b88a4ee654b34f2a7a86c5495691c
C 66d53bece661200ea6e1a17b29f1f7c9ee6479af4ed318f708d9118c67c1c1dc
```

For the forged-PDF promotion, the append-only triggers were restored before
the promotion decision; PostgreSQL reported both detector triggers enabled.
The retrofit used trigger disablement only because the reusable fixture first
inserts an honest receipt. The same forged `opaque` receipt can be inserted
directly at bootstrap without disabling a trigger: the table guards
update/delete/truncate, not insert. The independent ODT-ZIP direct-insert case
demonstrated that admission path without any trigger bypass.

## Severity summary

- Critical: **0**
- High: **1**
- Medium: **1**
- Low: **2**

## High finding

### H1 — the “byte-derived” detector receipt is still a self-attested row

The new schema correctly ties a detector row to artifact SHA and byte length,
hashes the JSON receipt, restricts the named detector/version, and makes the
row immutable after insertion
(`canonical_v1.sql:502-600`, `922-975`). Promotion also requires one verified
detection per linked artifact and includes the receipt in the exhaustive
inventory fingerprint
(`promotion_v1.sql:2080-2147`, `2582-2678`).

Those controls prove only that the stored declarations are internally
self-consistent. PostgreSQL never sees the artifact bytes and the receipt has
no signature, independently trusted attestation, detector executable hash, or
privileged one-shot loader proof. In particular, the fallback branch accepts:

```text
detected_media_type = application/octet-stream
detector_evidence.basis = opaque
detector_evidence.magic_hex = 255044462d312e37...
```

even though those exact bytes begin `%PDF-1.7`. The same branch accepted
`opaque` for an ODT ZIP whose mimetype entry was independently read as
`application/vnd.oasis.opendocument.text`
(`canonical_v1.sql:588-598`).

With a supplied self-consistent inventory fingerprint, the ODT-only promotion
therefore saw one detected ODT and no detected PDF, then promoted the real PDF
hidden under `supporting.bin`. The canonical receipt count was one. This
reproduces the prior H1 at the new trust boundary rather than closing it.

Immutability does not help against a false first insert. The author regression
uses the honest detector helper, so its `supporting.bin` receipt says
`application/pdf` and is correctly rejected; it does not test a self-consistent
false `opaque` first insert.

Before any live apply:

1. define the actual bootstrap/ongoing artifact-loader authority and make it
   the only path that can insert detector receipts;
2. bind the receipt to independently verifiable bytes, for example a signed
   detector attestation covering artifact SHA, byte length, detected type,
   exact detector executable/container hash, and raw manifest identity;
3. independently verify the attestation/raw bytes before loading, and preserve
   that audit receipt outside the database row being attested;
4. at minimum, reject `opaque` receipts carrying known PDF/ZIP signatures, but
   do not mistake that blacklist for full byte provenance; and
5. add a regression whose oracle constructs real PDF/ODF bytes independently,
   inserts a self-consistent false first receipt, recomputes the exhaustive
   inventory, and proves promotion remains blocked.

## Medium finding

### M1 — the declared detector regression still mirrors the trusted producer

The 37-test suite now covers an honest detected PDF, missing detector receipt,
an internally contradictory receipt, mutation rejection, ROC year zero, and
the positive A→B→C chain. This is materially better.

However, its byte-receipt helper is also the producer of the receipt the SQL
trusts. The relevant regression
(`tests/test_canonical_promotion_migrations.py:2498-2609`) never supplies a
wrong but internally self-consistent detector result. The entire declared
suite passed while the independent forged `opaque` promotion succeeded.

Keep the regression oracle separate from the receipt producer and test the
first-insert/bootstrap boundary, not only post-insert mutation.

## Low findings

### L1 — absent-schema rollback remains non-idempotent

Both rollback scripts return only from an inner `DO` block when the managed
schema is absent, then continue to unconditional schema-qualified locks:

- `canonical_v1.rollback.sql:21-25`, followed by `53-66`
- `promotion_v1.rollback.sql:21-25`, followed by `46-58`

This fails safely rather than deleting data, but it is not the apparent
no-op rollback path.

### L2 — allowed capability-login identities remain outside the structural hash

Capability membership validation is strict, but the structural contract line
still records only forbidden-member and outgoing-membership counts, not the
identities of otherwise allowed LOGIN members
(`canonical_v1.sql:1144-1185`, with the equivalent preflight calculation).
Adding or removing an allowed identity can preserve the structural hash.
Retain a separate immutable authorization receipt if this remains outside the
schema contract.

## Prior finding disposition

| Prior finding | Current disposition |
|---|---|
| Quarantined supporting artifact could promote | Closed by exhaustive inventory status checks. |
| Changed eventless companion could promote | Closed by accepted-event replay checks. |
| Future source date could normalize to a past value | Closed by in-function raw parsing and temporal gates. |
| ROC year zero accepted | **Closed.** All three roles rejected independently; SQL now requires ROC year ≥1 at lines 1274-1355. |
| Legitimate A→B canonical then B→C candidate could not promote | **Closed.** Whole-anchor A→C and candidate-edge B→C are now separate and the positive case promoted with exact interval/effect/head parity. |
| Real PDF hidden as octet-stream in ODT-only release | **Open (H1).** Honest receipt rejects it; a self-consistent false first receipt still promotes it. |

## Gate decision

The local blocking security gate is **not clean**. Do not apply either
canonical migration to live PostgreSQL, do not load canonical bootstrap rows,
and do not send the packet to GPT Pro yet. GPT Pro is an advisory post-local
verification gate and cannot close a reproducible High finding.

Required next step: remediate H1 at the first-insert/bootstrap trust boundary,
add the independent false-receipt regression, and run another disposable-PG
independent review. Medium/Low items may remain explicitly bounded, but no
Critical or High may remain before the Pro audit.
