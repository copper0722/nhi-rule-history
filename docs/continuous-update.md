# Continuous NHI Rule Update Lane

## Status and non-claim

This document defines the stage-only continuous-update methodology implemented
under `src/nhi_rule_history/update/` and the two PostgreSQL stage migrations
dated 2026-07-27.

The lane currently ends at immutable source evidence and nonauthoritative
candidate state. It does not write legal history, close a prior version, decide
stable rule identity, or publish a reader-facing diff.

As of 2026-07-27, multiple official notices with a stated 2026-08-01 effective
date have been captured with their exact RSS observations, detail pages, and
complete declared attachment inventories. They are not canonical history. The
effective date is still in the future relative to the capture date, and the
required post-effective-date anchor replay is not yet available.

A private PostgreSQL-registered recurring deployment has now passed real
scheduled fires for both source acquisition/corpus registration and proposal
staging. It remains stage-only and enforces
`AUTO_PROMOTION_ENABLED=false`. The deployment has also exercised a primary
worker timeout or contract failure followed by exactly one successful fallback.
No scheduler or worker credential is stored in this public repository.

## Layer contract

The continuous lane preserves these boundaries:

```text
exact RSS observation
  -> immutable notice source bundle
  -> deterministic corpus source bundle
  -> bounded model source proposal
  -> deterministic proposal validation
  -> append-only PostgreSQL candidate stage
  -> independent temporal and anchor review
  -> future canonical promotion
```

Success in one layer grants no authority in the next layer. In particular:

- a feed timestamp is not a legal effective date;
- a notice comparison table is not a stable rule identity decision;
- old/new columns do not prove direct predecessor adjacency;
- a model proposal is evidence triage, not an executable database operation;
- `promotion_ready_pending_anchor` is still a candidate state, not canonical
  history.

## 1. Exact official RSS acquisition

The intake endpoint is the official NHI RSS feed:

`https://www.nhi.gov.tw/ch/rss-3258-1.xml`

The request profile is versioned and hashed. The current profile requires:

- HTTPS GET to the allowlisted official host only;
- HTTP/1.1;
- a fixed user agent, `Accept`, `Accept-Language`, and `Cache-Control` profile;
- default TLS verification;
- no redirects;
- HTTP 200 only;
- ephemeral cookies held in memory or a mode-0600 temporary file and never
  logged;
- bounded response size and timeout.

The client fails closed on a non-XML response, malformed XML, entity or doctype
declarations, an unexpected root or channel, duplicate item identity, a
non-official detail URL, or a zero-item feed. A poll also fails if the item
count collapses below the configured fraction of the preceding observation.

Each poll package contains the exact `feed.xml`, safe response headers, byte
length, SHA-256, the ordered parsed item projection, an item-sequence SHA-256,
the prior observed-GUID set hash, and the exact set selected as new likely drug
rule notices. The package is written through a temporary directory, fsynced,
verified, and atomically renamed.

Keyword matching is an intake triage rule, not source-universe closure. The
poll retains every parsed feed item even when only likely drug-rule items are
selected automatically. New nonmatching items require a discrepancy or manual
review lane; they must not disappear from the observation record.

## 2. Immutable notice source bundle

Acquisition of one selected item starts again from the exact current RSS
response. The requested detail URL must identify exactly one item in that
response. The client then captures:

1. the exact RSS response;
2. the official detail page;
3. every unique attachment URL declared by that detail page, in declared
   order.

The source bundle refuses partial attachment coverage. Attachments that return
HTML or XML instead of document bytes are rejected. Media type detection uses
the bytes as well as HTTP metadata.

Every resource record preserves:

- request URL and final URL;
- HTTP status and allowlisted safe headers;
- observed time;
- SHA-256 and byte size;
- detected media type;
- content-addressed relative path;
- attachment sequence and label where applicable.

The bundle fingerprint is computed from the RSS item, versioned HTTP profile,
resource identities and hashes, and complete attachment counts. Observation
times are retained but excluded from content identity. Therefore:

```text
same notice identity + same bytes -> verified replay of the sealed bundle
same URL + different bytes        -> new artifact and new bundle fingerprint
```

A same-URL/new-bytes observation is evidence of changed delivery bytes only. It
does not establish correction, supersession, or legal replacement and must
enter `needs_review`.

## 3. Deterministic corpus source bundle

After the notice bundle verifies, the corpus adapter deterministically extracts
the official subject, reference number, document date, publication date,
update date, and announcement text from the captured detail HTML. Missing or
ambiguous fields fail closed.

The corpus lane requires at least one parseable ODT and preserves every
declared attachment in source order. It accepts multiple PDF, ODT, ODS, XLSX,
and DOCX attachments; an unknown media type is retained with a `.bin`
extension rather than silently dropped. It writes a conventional source bundle
containing:

- `source.html`;
- `source-rss.xml`;
- deterministic `raw.md`;
- `attachment-NNN.<ext>` for every declared attachment;
- `manifest.json`.

Each attachment row retains declared sequence, label, detected media type,
origin artifact hash, byte count, and bundle hash. ODT text from every declared
ODT is extracted directly from `content.xml` in attachment order. Paragraph
and table-cell blocks retain document order, table/row/cell/paragraph locators,
original text, text hash, artifact hash, attachment identity, and deterministic
block identity. These blocks are source locators, not canonical clauses.

Legacy single-ODT/single-PDF corpus bundles remain replay-compatible. Manifest
v1.2 introduced provenance-preserving normalization for official reference
numbers that omit the terminal `號`; its rule remains frozen at
`nhi-reference-number-normalization/1.0.0`. Manifest v1.3 adds a separate
`1.1.0` rule for official table cells that append exactly one U+3002 `。`:
the parser removes whitespace from a fixed ASCII/NBSP/ideographic-space set,
removes at most one terminal full stop, then appends one missing `號`. It
re-full-matches the result after each bounded operation. Other punctuation,
two full stops, embedded notes, and multiple values fail closed.

Both versions preserve the exact `ref_number_raw`, canonical value,
normalization reason, and rule version. V1.3 repeats those fields in `raw.md`
frontmatter, and the registrar recomputes and cross-checks them. The v1.2
parser never gains v1.3 normalization behavior, so existing manifests retain
their original normalization semantics and hashes. The current adapter may
invoke that frozen parser only to locate and verify an already existing
v1.0–v1.2 target. If no such target exists, the original v1.3 parse error is
returned; legacy parsing can never create a new bundle. Metadata extraction reads
only structurally paired cells: `th`/`td` for the notice table and sequential
`dt`/`dd` cells within the same `dl` for publication metadata. `公告事項`
therefore preserves ordered paragraphs, list items, `div` blocks, line breaks,
and intervening bare text from its own value cell without consuming a later row
or treating label-like text inside the announcement as a boundary. Every
non-ignored text node is admitted exactly once. Unknown layouts fail closed.

The corpus bundle is written to a temporary sibling directory, fsynced, and
atomically renamed. An existing identity is accepted only after its source UID,
origin bundle fingerprint, complete source bindings, on-disk inventory, and
top-level metadata agree. `raw.md` is regenerated from the sealed source with
the frozen renderer for that manifest version and must be byte-identical;
changing the prose and its declared hashes together is therefore rejected.
The target, manifest, and payload files must be real in-tree files rather than
symlinks. Corpus registration must follow that filesystem publication.
Separately, the update candidate loader records a
durable, fsync-verified receipt for the immutable notice source bundle; the two
receipts must not be conflated.

## 4. Model authority and failover

Models receive only a self-contained source packet made from the immutable
attachment inventory and ordered ODT source blocks. Worker contract v2
deliberately withholds notice title, date, URL, reference number, feed
classification, rule identity, and every database identifier. Those fields
remain controller facts and are bound only after worker output has passed the
source-only contract. The worker's only permitted role is to propose:

- exact source spans using `[start,end)` character offsets and hashes;
- raw temporal expressions plus a date interpretation candidate;
- old/new comparison spans;
- source designation text;
- explicit uncertainty and review flags.

Models may not emit stable or canonical rule IDs, predecessor IDs, snapshot
IDs, interval end fields, head generations, or proposed/executable database
operations. Unknown fields and forbidden keys are rejected recursively.
Every quoted span must resolve exactly to one supplied block and its hash.
The controller then binds the proposal to the independently extracted notice
metadata and rejects any mismatch; the worker cannot supply or override that
binding.

The first worker is invoked once in an isolated, source-only runtime. A fallback
worker is invoked once only after the primary attempt has a recorded execution,
timeout, transport, or output-contract failure. A fallback is availability
recovery, not a second legal review. It must link to the failed primary attempt
and record the failure reason. A successful primary suppresses fallback. If
both attempts fail, the job ends with a failure receipt and no candidate.

Before either call, the deterministic controller enforces packet budgets and
source shape. A structurally complex packet becomes `partition_required` with a
replayable reason and **zero model calls**. This is a terminal operator-review
outcome, not a failed model attempt. General multi-rule partitioning remains a
separate open implementation item.

Before contract validation, the runner preserves exact stdout and stderr bytes
for every attempted worker. It also preserves the exact prompt, append-only
attempt JSONL, stream hashes, provider/runtime/model labels, timing, exit or
failure state, the selected raw JSON output, and a final receipt. Invalid output
is retained as evidence; it is never silently replaced.

## 5. Deterministic candidate validation

Only the following shape may reach
`promotion_ready_pending_anchor` for possible future promotion:

- the model explicitly assessed a single full replacement;
- exactly one effect candidate exists;
- comparison kind is `full_replacement`;
- exactly one clause and one comparison row are involved;
- both complete old and new source spans are present;
- no omitted text marker is present;
- no merged-cell or cross-row dependency exists;
- no partial patch, multiple-rule scope, correction, identity uncertainty,
  same-URL/new-bytes condition, or ODT/PDF discrepancy exists;
- every document-level and effect-level review flag is false.

All other shapes become `needs_review`, including a model report that finds no
relevant rule. Split, merge, move, restore, deletion, creation, numbering reuse,
correction, multiple clauses, incomplete old/new columns, and ambiguous
identity are intentionally outside the first automatic lane.

ODT/PDF agreement is not inferred. When a PDF is present, the current source
packet marks parity as unverified and requires that review flag in the model
output. Consequently, such a candidate cannot enter the future-promotion lane
until an independent deterministic or source-capable parity check has been
recorded.

The validated receipt always contains `auto_promotion_enabled: false`.

## 6. PostgreSQL stage boundary

The 2026-07-27 migrations create two isolated append-only schemas:

- `nhi_rule_history_update_ops` for jobs, bounded leases, worker attempts,
  content artifacts, URL and feed observations, feed items, and durable bundle
  receipts;
- `nhi_rule_history_candidate_stage` for immutable proposals, exact source
  spans, validator evidence, and candidate state transitions.

The capability roles are NOLOGIN, non-superuser, no-inherit roles with only the
minimum `SELECT` and `INSERT` privileges for their stage. They receive no
privilege on canonical legal-history or publication schemas.

Database guards enforce:

- at most one primary and one fallback per job;
- fallback only after the linked primary is recorded as failed;
- nonoverlapping leases and lease ownership for attempts and observations;
- candidates only from a received durable bundle and a matching successful
  worker output;
- at least one exact source span and evidence row before a state transition;
- gap-free append-only state transitions;
- terminal `needs_review` and `rejected` states;
- no update, delete, or truncate of operational or candidate evidence;
- no forbidden canonical or executable-operation keys in evidence JSON.

`promotion_ready_pending_anchor` cannot transition to canonical state in these
schemas. It can only be demoted to `needs_review` or rejected.

## 7. Idempotent load and replay

The stage loader independently re-verifies the source bundle, canonical JSON
receipt, append-only attempt stream, raw stdout/stderr hashes, selected output,
source packet, exact spans, controller-owned notice binding, and proposal
contract before opening a transaction.

Job, lease, receipt, attempt, and candidate UUIDs are derived
deterministically from immutable fingerprints. Loading is serialized by a
transaction advisory lock:

- a new fingerprint inserts all operational and candidate rows in one
  transaction;
- an existing fingerprint is a replay and inserts no duplicate logical job;
- a content hash already seen elsewhere is reused only if byte size and media
  type agree;
- a same URL with new bytes is linked as a new URL observation, not overwritten.

After commit, a fresh read-only connection recomputes per-table row identities,
counts, and an aggregate fingerprint. A mismatch fails the load receipt.
Identical input must therefore replay to the same database state; changed bytes
must produce new evidence.

## 8. Preactivation and canonical temporal model

The operational job records an `activation_cut`, but the current stage loader
does not promote any candidate on either side of that cut. This is deliberate:
the stage is safe even when an RSS item announces a future effective date.

Items observed before their stated effective date are preactivation
candidates. They may be acquired, bundled, proposed, validated, and staged, but
must not become active canonical text. Older items first seen after their
effective date are backfill candidates and must pass the same predecessor and
anchor requirements; discovery time never substitutes for legal time.

Canonical version validity must use half-open intervals:

```text
[effective_from, effective_until_exclusive)
```

If a verified replacement becomes effective at date `B`, the prior snapshot
eventually closes at `B`, and the new snapshot starts at `B`:

```text
prior: [A, B)
new:   [B, ...)
```

No day is subtracted from `B`, and no overlap is permitted. A deletion closes
the prior interval at `B` without creating an active successor. Same-day
multiple events, corrections, and unresolved ordering require review rather
than an invented sequence.

## 9. Required anchor replay before future promotion

A future promoter must be a separate capability and migration from the stage
loader. It may promote only after all of the following are proven:

1. the effective date has arrived;
2. the date is supported by an exact official source locator and its legal role
   is resolved;
3. stable rule identity is independently resolved, without designation reuse,
   split, merge, move, restore, or correction ambiguity;
4. exactly one current predecessor exists at the effective instant;
5. the comparison old side agrees with that predecessor and a pre-event
   cumulative anchor;
6. the comparison new side agrees with the first applicable
   post-effective-date cumulative anchor;
7. replay from the preceding official cumulative anchor through all intervening
   accepted events reproduces the next whole/chapter anchor rule set and text
   hashes;
8. ODT/PDF parity is verified when both official formats exist;
9. the canonical head and generation checked before the transaction are still
   unchanged at commit time.

Only then may one atomic canonical transaction create the accepted event/effect
and new snapshot, close the prior snapshot at the new
`effective_from`, and attach exact source evidence. The transaction must abort
on any stale head, overlap, replay mismatch, missing anchor, or identity
conflict. A post-commit replay and fresh read must then reproduce the accepted
anchors.

This design prevents a preactivation notice from prematurely rewriting the
current version and prevents an announcement table from being treated as proof
of direct legal adjacency.

## 10. Verified stage-only scheduling profile

The registered recurring deployment performs only:

```text
poll -> acquire -> bundle -> propose -> validate -> stage -> needs_review
```

It must set and enforce:

```text
AUTO_PROMOTION_ENABLED=false
```

The public runner already emits false in every validated candidate. The
deployment wrapper must fail closed if its configuration is missing or differs.
It must use a bounded lease and runtime, durable logs, a single registered job
owner, and one primary/one-fallback maximum. It must not possess canonical
writer credentials.

Scheduler activation is accepted only after one real scheduled fire has
evidence for the registry entry, poll artifact, job and lease, attempt lineage,
bundle receipt, candidate state, and terminal result log. Until that evidence
exists, the truthful status is "schedule not verified active."

That activation gate passed on 2026-07-27. A real scheduled poll acquired and
registered a notice containing eight declared attachments. A subsequent real
scheduled proposal fire recorded a primary timeout, invoked exactly one
failure-only fallback, validated the returned exact source spans, and ended in
`staged_needs_review`. The notice covered multiple clauses and contained
omitted-text markers, so the terminal review state is the expected safe result.

## 11. Operator runbook

The public CLI exposes five stage operations:

```text
update-poll     capture and verify one exact RSS observation
update-acquire  acquire one RSS-listed notice and all declared attachments
update-corpus   prepare one deterministic atomic corpus source bundle
update-propose  run primary once and failure-only fallback once
update-stage    transactionally load one bundle/candidate pair into stage
```

The operator should execute them in order:

1. Export the already observed feed GUIDs from the stage database.
2. Run `update-poll` with the prior item count.
3. Review any feed-collapse failure and the complete new-item delta.
4. For each selected official detail URL, run `update-acquire`.
5. Verify the sealed source bundle and prepare the deterministic corpus source
   bundle.
6. Run `update-propose` with private worker specifications.
7. Inspect the attempt receipt and candidate controller reasons.
8. Run `update-stage` with a relative bundle locator, activation cut, lease
   owner, and notification window.
9. Verify the fresh-connection counts and fingerprint returned by the loader.
10. Route `needs_review`, failures, same-URL/new-bytes observations, and
    preactivation candidates to durable review queues.

Re-running identical poll, bundle, worker, or stage input must return a replay
receipt. Operators must not delete a failed attempt and retry under the same
identity.

## 12. Observability and recovery

Minimum operational signals are:

- feed HTTP outcome, artifact hash, item count, item-sequence hash, collapse
  result, and new-item count;
- per-URL prior/current artifact relation;
- declared versus acquired attachment count;
- bundle fingerprint, manifest hash, fsync state, atomic-publication receipt,
  and PostgreSQL receipt;
- primary status, linked fallback reason, prompt/output/stderr hashes, and
  selected attempt;
- candidate state, controller reason codes, source-span count, and evidence
  outcomes;
- stage replay flag, per-table counts, and fresh-connection fingerprint;
- queue age for preactivation, `needs_review`, and failed jobs.

Recovery is append-only:

- an acquisition transport retry creates a new observation and never edits
  prior evidence; a worker job still permits only its one primary and one
  failure-only fallback;
- identical bytes replay the sealed artifact;
- changed bytes create a new artifact and review condition;
- one failed primary permits one linked fallback; two failures require operator
  review;
- corrupted or incomplete bundles are quarantined and reacquired, never
  repaired in place;
- a terminal candidate is not rewritten; corrected evidence produces a new
  immutable proposal;
- database rollback uses the guarded, object-specific rollback migrations and
  never `CASCADE`;
- canonical history is unaffected because this lane has no canonical write
  path.

Terminal worker recovery uses an explicit generation state machine. A recovery
request must name a new method version and semantic prompt fingerprint; changing
only an attempt identifier or timestamp is rejected. Duplicate or concurrent
delivery can authorize at most one next generation. Within each generation the
same one-primary/one-fallback limit applies, and a second terminal failure is
never automatically requeued.

Two 2026-07-27 terminal receipts predate the PostgreSQL `worker_attempt` ledger
and contain 64-hex attempt identities rather than UUID rows. They are not
rewritten or fabricated into modern attempts. The recovery-v2 bridge first
admits their exact immutable receipt, attempt-stream paths, bytes, hashes,
primary/fallback lineage, terminal transition, and terminal evidence into
append-only legacy-evidence tables. Only that hash-bound admission may
authorize generation 2. Later generations must use native PostgreSQL attempt
rows. A structurally complex recovered work item may therefore end in
`partition_required` with zero calls, while preserving the original terminal
receipt byte-for-byte.

Each admitted legacy attempt declares
`attempt_id_scheme=sha256_hex_v1` and
`attempt_id_origin=immutable_worker_attempt_jsonl`; these identifiers are never
represented as UUIDs. The admission row also retains the byte-verifier contract
version, the reviewed code/diff SHA-256, verifier output schema, and canonical
admission-payload SHA-256. This is an audit identity, not a legal signature or
a claim that PostgreSQL directly read the operator filesystem.

## 13. Public/private boundary

The public repository should contain:

- acquisition, parsing, validation, and stage-loader code;
- PostgreSQL and SQLite-compatible data contracts;
- migrations and rollback migrations;
- tests, small fixtures, methodology, manifests, checksums, and audit receipts;
- normalized public releases and official binary release assets when their
  release gates pass.

The public repository must not contain:

- database connection strings, credentials, or tokens;
- operator hostnames, private paths, scheduler service identifiers, or runtime
  account IDs;
- provider command specifications or authentication state;
- private model conversation links;
- mutable production PostgreSQL state or operator-local corpus locations.

Worker specifications stay outside Git. Raw attempt streams are retained in the
operator evidence store; only explicitly reviewed public receipts or release
artifacts may be published. Official source content remains attributed to the
National Health Insurance Administration, and large binaries belong in
checksum-addressed release assets rather than repeated Git history.
