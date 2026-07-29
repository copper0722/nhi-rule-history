# GPT Pro post-verification audit: clause normalization and exact diff v25

Please independently audit the implemented and production-verified design for
normalizing one NHI reimbursement clause and comparing its versions. This is
the required second review after your earlier `REPAIR` decision.

You are advisory. You cannot inspect the local PostgreSQL database, repositories
or production browser unless the evidence is stated below. Codex owns those
checks and Copper owns release authority.

## Decision basis

- Decision basis ID:
  `nhi-clause-normalization-v25-post-verification-2026-07-29`
- Earlier response SHA-256:
  `5ce55d8a9f6c5f250ec59e75880c2e86cc3dbcd69f092bf746a0b1717473f582`
- Earlier verdict: `REPAIR`
- `nhi-rule-history` commit:
  `4569203b61aa70be4078eea31b5561f39037911d`
- `copper-panel` commit:
  `f5ffd5bef5d2`
- paid-reader commits:
  - `eade2ee` — consume normalized expressions and exact diff;
  - `3a4eb25` — resolve table-cell content through exact source spans.
- Final subscriber deployment:
  `bafa486d-0854-4796-b833-27ac94f87381`
- Authenticated live JSON SHA-256:
  `e00acb98c25b96ac5791c92ee4cab4616c63522601673fed083806c6f9c4b9d4`
- Risk class: R3, because this changes canonical PostgreSQL structure and a
  subscriber-facing clinical-policy reader.

The implementation basis has not changed after the deterministic evidence
below was collected.

## What was implemented

### Work, Expression, Manifestation and completeness

- A single clause is the canonical Work.
- Each complete effective or announced wording is an Expression.
- Source artifacts and blocks retain their Manifestation provenance.
- An Expression records `source_complete`, `verified_composite`, `patch_only`,
  `partial` or `unresolved`.
- Only complete states may enter the “latest complete clause” reader.
- Reader state is explicit and activation-based; load order and largest version
  number are not selectors.
- Expression adjacency is separately asserted. Only
  `direct_predecessor_verified` may be labeled “與上一版本差異”; otherwise the UI
  must say “與舊版本差異”.

### Exact source and document tree

- The official text remains immutable.
- Each Expression stores ordered exact source blocks and a complete text hash.
- Expression-local nodes form a tree with marker, role and structure status.
- Persistent node Work identity is separate and optional.
- Source ownership uses scalar and UTF-8 byte spans. Primary leaf spans must be
  complete, ordered and non-overlapping, and replay the exact source.
- An agent may propose candidates but cannot assign verified Work identity,
  verified adjacency, verified composite completeness or formatting
  equivalence.

### Tables

- A table is a first-class component attached to the clause.
- Cells separately store physical source state and logical grid-value state.
- Covered or policy-carried values must point to a valid origin; they do not own
  fabricated source text or source hashes.
- Table-cell paragraphs have their own exact source-span owner key:
  `(table_id, row_index, cell_index, content_order)`.
- The paid reader resolves rendered table content through that key and validates
  the source block and render-plan text before display.

### Version comparison

- The canonical diff compares the two complete Expression texts at Work level.
- It is exact and replayable; no quote, width, whitespace or punctuation change
  is removed from stored data.
- Each segment stores both-side scalar and UTF-8 byte offsets.
- Display classification is a separate versioned projection.
- Node lineage is independent. Unresolved node alignment does not emit
  fabricated deletion/insertion/move hunks.
- The current 2.6.1 comparison contains seven exact segments:
  `unchanged, inserted, unchanged, inserted, unchanged, inserted, unchanged`.
  Therefore the display classification is `本版新增`. The old pane is labeled
  “舊版本”, not “本版刪除”.

## Production evidence

### Active sealed state

- release run:
  `ad59feb1-1891-5b92-b221-0cb15d3efbac`
- normalization run:
  `16d5abd5-a8aa-5d35-8a4d-3e3edabb7598`
- exact diff run:
  `cc4acbaf-559d-5148-9773-f5f023e36561`
- 2 complete Expressions;
- 478 exact source blocks;
- 87 normalized nodes;
- 6 normalized tables;
- 486 normalized table cells;
- 85 node-lineage rows, of which 83 explicitly remain
  `alignment_unresolved`;
- 1 Work-level exact diff hunk and 7 exact segments.

The active output and sealed fingerprints were recomputed and matched.

### Immutability and recovery

The production mutation matrix attempted:

- `UPDATE`;
- `DELETE`;
- direct `INSERT`;
- `UPSERT`;
- `COPY`;
- `TRUNCATE`;
- mutation of the activation/control event.

Every attempt was rejected by PostgreSQL with `P0001` and wrote no state.

A separate two-transaction drill then:

1. committed deactivation;
2. observed the fresh API return `503` because no sealed normalization was
   active;
3. committed reactivation in another transaction;
4. observed API `200`;
5. proved the complete API payload before and after was byte-identical
   (`f14b6e32bb32e887e1fb19c00c7eb678f4f373342ab37fe62a4af585ff69f104`).

Recovery is non-destructive activation of the prior sealed run, not rebuilding
or mutating historical rows.

### Portable data

- 20 PostgreSQL relations exported as canonical JSONL;
- 2,238 logical rows;
- a fresh SQLite 3.50.4 database was built only from the tracked JSONL;
- SQLite integrity check passed;
- PostgreSQL → JSONL → SQLite per-table counts and canonical row fingerprints
  matched exactly;
- the binary SQLite file is reproducible output, while JSONL and the builder are
  tracked.

### Deterministic tests and live reader

- `nhi-rule-history`: 495 tests run; 488 passed, 7 environment-gated skips,
  0 failures.
- paid site: 145/145 tests passed; 69 routes built.
- subscriber projection: 639 current clause rows and 14 latest notices.
- anonymous paid page and JSON are denied by the existing subscriber gateway;
  authenticated page and JSON return 200.
- In a real authenticated browser, clause 2.6.1 rendered:
  - 2 complete versions;
  - 4 displayed table components and 130 row cards;
  - latest heading `最新條文 2026.09.01` with a not-yet-effective warning;
  - collapsed history by default;
  - desktop exact diff as two columns;
  - mobile exact diff as one column;
  - 390 px viewport had `scrollWidth=390`, `clientWidth=390`.

The adversarial browser pass found one genuine integration defect: the first
frontend implementation expected `block_order` to be duplicated inside each
table-cell content row, although v25 correctly linked those rows through source
spans. The production reader failed closed. The renderer was repaired to resolve
the exact source span key above, reject duplicate/missing mappings, and require
the mapped block text to equal `content.exact_text`. The regression fixture was
changed to the real v25 shape (no duplicate `block_order`). The full tests,
build, deployment and live browser audit were then repeated and passed.

## Known scope boundary

- v25 is a production canary for clause 2.6.1, not a claim that all historical
  clauses are normalized.
- Historical corpus reconstruction remains incomplete; the public project
  reports those gaps separately.
- 83 subnode alignments are unresolved. Exact whole-Expression comparison is
  still valid and complete; node-level semantic lineage is intentionally not
  claimed.
- This audit is about correctness of the storage and comparison method, not
  about whether every historical NHI source has already been found.

## Audit request

Please challenge the implementation, not merely restate it.

1. Did the implementation adequately repair every material finding from your
   earlier response?
2. Is the source-span ownership model sufficient to reconstruct and prove the
   relation between raw text, nodes and table cells?
3. Is separating physical table state from logical value state correct and
   safe for both exact reconstruction and reader cards?
4. Is the Work-level exact diff valid when most subnode lineage remains
   unresolved, provided the Expression relation is verified?
5. Do the exact segment and display-classification rules prevent the
   `ABC → ABCD` error of inventing a deletion?
6. Are the sealing, mutation and two-transaction recovery tests sufficient for
   this release?
7. Does JSONL/SQLite parity preserve enough information for a non-PostgreSQL
   user?
8. What counterexample, invariant or live-browser edge remains untested?

End with exactly one release verdict:

- `PASS` — no Critical or Major blocker remains for this v25 canary and method;
  or
- `REPAIR` — list only concrete Critical/Major blockers, each with a falsifiable
  acceptance test.

Minor improvements should be listed separately and must not be mislabeled as
release blockers.
