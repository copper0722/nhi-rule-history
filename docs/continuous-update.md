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

The title is the selection surface. Classifier 3.0.0 (parser 1.2.0) selects a
title that carries a reimbursement-rule term (給付規定, 給付條件, 給付範圍)
together with either a drug noun (藥品, 藥物) or a drug-rule clause code written
directly after 修訂, such as `公告修訂4.2.…之給付規定`; titles naming special
materials (特殊材料, 特材) stay unselected. A sealed poll package is re-verified
with the classifier of its own parser version, so packages written under
parser 1.1.0 keep their 2.0.0 selection.

An item that an older classifier closed as `ignored_non_rule` stays in the
ledger. When a newer classifier selects its stored title, the review lane
`update-queue-reselect` (plan by default, `--apply` for one item) appends
`ignored_non_rule -> selected` with actor `deterministic_classifier_reselection`;
the evidence names both classifier versions and the SHA-256 of the item's
first title. The database guard (migration
`2026-09-24_nhi_rule_history_update_queue_reselection_v3`) accepts that exit
once per classifier version and no other exit from a terminal state.
Acquisition then rebuilds the item from its first stored RSS response.

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

The 2026-07-28 cefiderocol canary exposed a narrower hierarchy case before any
worker call. Suitability v1 observed both section designation `10.3` and leaf
`10.3.8`, classified them as multiple rules, and returned
`partition_required` with zero attempts. This was a safe terminal outcome, but
it did not prove that the notice contains multiple independent rule effects.

Suitability v2 may collapse such candidates only when there is exactly one
maximal dotted-numeric leaf, all other candidates are its dot-boundary
ancestors, the leaf occurs in a top-level comparison row, and no ancestor has
an independent comparison row. Parallel leaves and independent ancestor rows
still require partitioning. The worker-job fingerprint binds the suitability
schema, so a v2 decision cannot replay the immutable v1 terminal receipt.
Replaying the sealed cefiderocol packet under v2 yields effective designation
`10.3.8` and `suitable`, but the old terminal row remains unchanged and no new
recovery generation or worker attempt has been made. General multi-rule
partitioning remains open. Consequently, this canary still provides no evidence
about Claude, Codex, Grok, or any other model's document-understanding ability.

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
   accepted transitions reproduces the next whole/chapter anchor rule set and text
   hashes;
8. ODT/PDF parity is verified when both official formats exist;
9. the canonical head and generation checked before the transaction are still
   unchanged at commit time.

Only then may one atomic canonical transaction create the accepted transition,
its accepted official-source evidence, and the new snapshot; official-notice
links are nullable enrichment. The transaction closes the prior snapshot at the new
`effective_from` and attaches exact source evidence. The transaction must abort
on any stale head, overlap, replay mismatch, missing anchor, or identity
conflict. A post-commit replay and fresh read must then reproduce the accepted
anchors.

This design prevents a preactivation notice from prematurely rewriting the
current version and prevents an announcement table from being treated as proof
of direct legal adjacency.

## 10. Verified stage-only scheduling profile

Owner override, 2026-07-28: the proposal/agent-dispatch portion is paused while
the v3 transition-evidence methodology, queue converter, validator, and pilot
are built. Deterministic poll/acquisition may continue. No Claude or fallback
worker may be called until Copper explicitly resumes the lane and
`NHI_RULE_HISTORY_AGENT_DISPATCH_ENABLED=true`.

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

## 14. Deterministic announced-notice overlay

### Why this lane exists

Until this lane, a notice that reached `corpus_registered` could become
structured data only through the model proposal stage (sections 4-7) or a
clause-specific loader. The only loaded amendment, 2.6.1, came from the
hash-locked `announced_dyslipidemia` loader. With the proposal stage not
running since 2026-07-28, every later notice stopped at `corpus_registered`,
including the amendments effective 2026-09-01 and 2026-10-01.

This lane needs no model. It reads the official comparison table
(`修訂對照表`) that every drug-rule amendment notice attaches, and serves its
revised column as an announced overlay. It sits beside the stage lane:

```text
corpus source bundle (section 3)
  -> announced_notice: parse the comparison-table ODT (no model)
  -> announced_release: compose one sealed release run (active run + notices)
  -> load (sealed, not served) -> activate (served) -> rollback (append-only)
```

It is not canonical legal history. The layer contract above still holds: a
comparison table is not a rule-identity decision and its columns do not prove
predecessor adjacency. Every projected clause is a `patch_only` clause patch,
labelled as the revised column of the table and never as a complete clause.

### Parsing contract (`nhi_rule_history.announced_notice`)

- Input is a registered corpus bundle. Every manifest file is re-hashed. Blocks
  come from the project ODT parser and must equal the `raw.md` source-block
  receipts written at registration. Queue mode also requires the manifest bytes
  to match the queue receipt.
- A comparison table has exactly two columns: a revised header
  (`修訂後給付規定`, `建議修訂後給付規定`, `修訂後附表規定`) and the
  matching original header (`原給付規定`, `原附表規定`). Any other header,
  merged cell, repeated cell, note or annotation in a comparison cell, or tracked
  change fails closed. Other tables, such as application forms, are ignored.
- The effective date is the stand-alone statement `（自115年10月1日生效）` in
  the same attachment, converted from the ROC calendar. Missing, unparseable, or
  multiple differing dates fail closed. Feed, publication and capture times are
  never used.
- A clause starts at a paragraph whose dotted code ends in its own full stop
  (`2.1.4.2.`). `2.18歲` is a list item, not a code. Both columns must name the
  same codes, or the original column must read `無` (a new clause). A row
  without a code continues the previous clause.
- An appendix table (`附表…`) becomes a pending effect because no dotted
  clause code exists for it. Listing and price changes named by the notice are
  also recorded as pending effects.
- Grammar is matched on NFKC text, because official files mix full-width forms
  and CJK compatibility ideographs (U+F98E for 年). Stored text is never
  normalized. The rule
  `odt-paragraph-text-with-whitespace-elements/1.0.0` renders `text:s`,
  `text:tab` and `text:line-break`, which the block contract drops. Each
  manifest entry keeps both hashes.
- Omission markers (`略`, `(略)`, `(以下略)`, `(餘略)`) set
  `omitted_text_present`. The same regex rejects confusable words such as
  `策略`.
- ODF list numbering draws labels such as `1.` or `(5)` that are not
  character data. `nhi_rule_history.odf_list_numbering` rebuilds them with the
  LibreOffice Writer rules (rule `odf-list-label/1.0.0`): list identity by
  `text:continue-list`, `xml:id` and `text:continue-numbering` (for documents
  generated by Microsoft Office, the last list of the same style), counting in
  a per-list number tree (list headers and later item paragraphs uncounted,
  counted phantoms, continuation of the previous subtree), level and item
  start values, the second-sub-list restart, prefix/suffix, `num-list-format`
  strings and `display-levels`, and the formats `1`, `a`, `A` (with letter
  sync), `i`, `I` and the single-character CJK values of `甲, 乙, 丙, ...`,
  `子, 丑, 寅, ...`, `壹, 貳, 參, ...` and `一, 二, 三, ...`. The printed text
  of a labelled paragraph is the label, then a tab, space or nothing as its
  `text:label-followed-by` says, then the paragraph text; the manifest entry
  records the label, its separator and provenance, and `raw_text_sha256`
  stays the registered character-data hash.
- For documents generated by Microsoft Office the label must also be the one
  that suite prints: the plain per-level counter model must give the same
  numbers and the ODF 1.4 `number:num-list-format` string must equal the
  prefix/suffix form. Bullets, images, number formats LibreOffice does not
  know (it falls back to Arabic, e.g. Word's `一, 十, 一百(繁), ...`),
  locale-dependent formats, label-width positioning, consecutive numbering,
  style overrides, differing nested list styles, headings in lists, outline
  numbering, paragraph-style numbering outside a list, lists in frames or
  notes, and numbered paragraphs without a list id and style are not
  reproduced. A clause with such a paragraph is a pending effect with reason
  `unsupported_list_numbering`.
- A notice whose every clause is held back still enters the run. Its event
  lists each held-back clause in `unresolved_scope` with its `blocked_reason`,
  and each clause is a `pending_projection` effect. Served data therefore
  shows that the clause changes on the stated date, although no text is
  served.
- Verification renders the attachment with LibreOffice as an independent
  engine. The revised text must appear verbatim between cell or line
  boundaries; a list paragraph is compared with LibreOffice's text-export
  prefix (four spaces per level, the reconstructed label or two spaces, one
  space), so a wrong label cannot match. A mismatch blocks the clause
  (`official_rendering_mismatch`). A clause with list paragraphs is projected
  only when the check is `exact`; otherwise, including a nested table in the
  revised column, it stays pending (`official_rendering_unverified`). `load`
  requires this rendering unless the operator waives it explicitly.

### Release composition (`nhi_rule_history.announced_release`)

`v_active_run` serves one run, so a new notice is served only through a new run
that keeps everything the active run serves:

- Every run-scoped row of the active run is carried. Only `run_id` and the row
  hash that covers it change. Each stored row hash must first replay from the
  database values. The clause-document receipt in older installations is
  recomputed with its frozen formula after the same formula replays the base
  receipt.
- The 2.6.1 normalization and exact diff are rebuilt for the new run by the
  unchanged 2.6.1 loader, with inputs pinned to the base run: its predecessor
  publication run, terminology run and product snapshot. As a positive control,
  the same call bound to the base run must reproduce the served normalization
  and diff run ids and sealed fingerprints exactly. Re-bound rows are also
  compared with run-bound UUIDs masked.
- The served reader profile is re-bound with byte-identical content. The latest
  resolution of each carried patch is carried. Its evidence keys stay verbatim,
  and a `carried_forward` key names the source event.
- Each new clause patch binds `predecessor_text_sha256` to the served text of
  the same code. A new clause binds the empty-text hash. The
  `verified_scheduled` resolution evidence holds the full component manifest,
  the effective-date span and the rendering-check result.
- `release_run.source_artifact_sha256` holds the fingerprint of the ordered
  (reference number, artifact hash) set.

`load` seals the run, the resolutions and the re-bound projections in one
transaction and changes nothing served. `activate` requires the expected
sealed fingerprint and the expected served base. It also refuses a run that
does not carry every notice and every clause patch (patch id, text hash,
effective date) of the served run, so a stale run composed from an older base
cannot unserve anything. The subscriber sync runs the 2.6.1 loader with
activation on every tick, and that loader re-activates its own run whenever
the served run has no 2.6.1 composed version for the 2.6.1 notice artifact.
So while the served run carries that version, `activate` refuses a run
without it: the same version, composed text, reviewed composite patch and
notice artifact. Before committing, it also runs the loader's own
active-source query on the activated state. It appends release,
normalization, diff and reader-profile control events in one transaction and
records the previous chain. `rollback` re-activates that recorded chain.

### Failure isolation and receipts

Composition isolates failures per notice. Each of these leaves one notice out
and is reported, while the rest of the batch still composes:

- the notice is listed twice in one batch;
- a required office rendering is unavailable;
- a clause cannot be bound to the served publication: it has an original
  column but no served clause, or it is marked new but is already served;
- a clause would get a second patch for the same effective date, within the
  batch or beside a carried patch;
- the notice states no clause amendment and no other effect;
- a supersede is refused (next subsection).

Only run-level invariants abort the batch: the base chain (active run,
counts, row hashes, resolutions), the current publication, the target schema,
the seal, and the 2.6.1 re-binding.

`compose`, `load` and `activate` print one JSON receipt on stdout. Errors go to
stderr. Every receipt has these lists:

- `failures`: bundles that failed at `queue`, `parse`, `input`, `rendering`,
  `bind` or `supersede`, with the error.
- `dropped_notices`: parsed notices left out, with the reason:
  `effective_on_not_selected`, or
  `carried_notice_has_newly_projectable_clauses` when a carried notice could
  serve more clauses but superseding was not requested.
- `blocked_clauses`: dotted clauses the run holds back (pending
  `clause_amendment` effects with a code), with the reason and whether the
  notice is `new`, `superseded` or `carried` in this run.
- `superseded_notices`: notices whose carried rows were replaced, with their
  served and added clauses.
- `carried_notices`: selected notices that the run already carries unchanged.

Appendix-table and listed-item effects are pending by design. They appear per
notice under `pending_effects`, not as held-back clauses.

- `status` is `passed` (a run was composed, loaded or activated) or
  `no_change` (nothing new to compose) only when `failures`,
  `dropped_notices` and `blocked_clauses` are all empty. Otherwise it is
  `passed_with_holds` or `no_change_with_holds`.
- Exit status: 0 green, 3 completed with holds, 1 error. Exit status 3 means
  the step took effect: a `load` sealed its run, and an `activate` served it.
  A scheduler must read `status` and must not treat 3 as a failed step.
- Without `--skip-failed`, any failure refuses the whole command: exit 1,
  nothing loaded.
- `activate --load-receipt <file>` copies the failures, dropped notices and
  superseded notices of the load that sealed the run into the activation
  receipt and the activation control event. Without it those lists are
  `null` (unknown, not empty), and only the held-back clauses, read from the
  served run, set the status.

### Superseding a carried notice

A notice that the served run already carries is normally left as carried. A
later compose may project more of its clauses, for example after a parser fix
admits clauses held back for generated list labels. Without
`--supersede-carried` the notice is reported in `dropped_notices`. With it,
the new composite replaces the notice's carried event, effects and patches by
its fresh projection. The served run is not rolled back.

The supersede is refused, and the notice keeps its carried rows and is
reported in `failures`, unless all of these hold:

- the fresh parse has the same notice id (same reference number and source
  artifact hash);
- no other carried row depends on the notice (patch components, composed
  versions, decision models), so the 2.6.1 reviewed composite is never
  superseded;
- every clause patch the notice serves is `patch_only` and comes back with
  the same patch id, text hash and effective date;
- each of those patches is still `verified_scheduled`, so no later resolution
  is reset.

Every patch of a superseded notice gets a fresh `verified_scheduled`
resolution. For a patch that was already served, the evidence names the
replaced resolution under `superseded_projection`. The input fingerprint
includes the superseded reference numbers.

### Consumer contract

A patch-only patch has no composed clause, decision model or reader profile.
The view's `decision_aid_available` has no meaning without a model. Consumers
must render `source_exact_patch_text` as the announced revised column and must
not present it as a complete clause. A consumer that accepts only
`reviewed_composite` patches must be changed before a run containing
patch-only patches is activated.

A notice whose every clause is held back has no clause patch, so
`v_public_clause_patch` does not list it. A consumer that shows announced
changes must also read the active run's `notice_event` and `notice_effect`
rows; otherwise that notice's held-back clauses stay invisible.

### Schema limits (design note; no DDL in this lane)

The existing tables represent a notice at patch level. Richer projection
needs reviewed migrations:

1. `patch_component.component_role` accepts only the 2.6.1 roles, so per-block
   rows are not written. The block manifest lives in the sealed
   `component_manifest_sha256` and the resolution evidence. A generic role
   such as `amendment_block` would allow per-block rows.
2. `composed_clause_version` requires `inherited_block_count > 0`, and the seal
   guard requires exactly 116 Table-2 codes per composed version. A
   full-replacement clause cannot be composed until both rules are
   generalized.
3. Only one clause-document diff run can be active, so the exact diff for a
   second clause needs multi-run activation.
4. Appendix designations (`附表十八之五`) have no clause key.
5. Generated list labels are rebuilt by `odf_list_numbering` and verified
   against LibreOffice. The rendering check does not model LibreOffice's text
   export of a nested table, so a revised column that contains one stays
   pending.
