---
audit: clause-normalization-v25-predecision
model: GPT Pro
mode: Pro
conversation_url: https://chatgpt.com/g/g-p-6a565eaa0c1881919cbb74e5a346f1b4-agentic-workflow/c/6a65f423-bb1c-83ee-8800-c8883cf9dbb7?tab=chats
prompt_sha256: 74ffed6339548ed5ff5efb9bb49740d514cc7be8be0e272dc025c71fa2619e0f
response_sha256: 5ce55d8a9f6c5f250ec59e75880c2e86cc3dbcd69f092bf746a0b1717473f582
captured_at: 2026-07-29T22:13:16+08:00
verdict: REPAIR
---

REPAIR

The overall direction is sound, but the current schema still conflates persistent legal identity, expression-local structure, source manifestation, and comparison alignment. The proposed model can become releaseable without redesigning the entire system, but canonical PostgreSQL deployment should wait for the identity, source-conservation, table-provenance, version-completeness, and exact-diff repairs below.

Akoma Ntoso is being used appropriately as inspiration, but work_node_key is not sufficient as a persistent Work-level identity. OASIS distinguishes the abstract legal Work, a content-specific Expression, and its physical/electronic Manifestation; Work-level references are intended to remain independent of a particular expression or markup instance. It also treats Work components as abstract entities that can correspond to different expression components over time. 
OASIS 文件组织

Required minimum separation:

clause_work
  persistent clause identity

clause_expression
  one adjudicated complete or explicitly composed version of that work

expression_node
  expression-local tree node

node_work
  optional persistent identity for a sub-clause structural object

node_identity_assertion
  expression_node → node_work, with status, basis, evidence and receipt

node_lineage_relation
  continues_as | moved_to | split_into | merged_from |
  replaced_by | number_reused

work_node_key may remain as a derived, human-readable key, but it must not itself be the authority. A marker path such as 2.6.1/（一）/1. is not invariant under renumbering, insertion, split, merge, or move.

Replace the proposed mixed identity_status values with two independent fields:

identity_resolution_status =
  unassigned | candidate | verified | version_local | conflicted

identity_basis =
  explicit_source_mapping | exact_predecessor_mapping |
  marker_path | structural_role | reviewed_adjudication

Cross-version alignment must be relational and many-to-many, not a single key copied onto each expression node. A single work_node_key cannot honestly represent:

one old point splitting into two new points;

two points merging;

text moving while retaining identity;

number reuse creating a distinct legal object;

unresolved alignment with several candidates.

Only identity_resolution_status=verified may drive move labeling or stable cross-version alignment. Candidate similarity may order a review queue but must not create a Work identity or a “moved” diff.

The invariant “every source block maps to exactly one document node” is too rigid and will either lose structure or fabricate one-to-one mappings. Containers may have no direct source text; one point may span several source blocks; one physical paragraph may contain more than one marker; tables map text into cells rather than directly into the table node.

Replace the one-block/one-node rule with a span-level mapping:

node_source_span(
  expression_node_id,
  source_block_id,
  scalar_start,
  scalar_end,
  byte_start,
  byte_end,
  mapping_role,
  ordinal
)

Required conservation semantics:

every source-text scalar belonging to the clause is owned exactly once by a primary leaf mapping;

structural containers may own zero primary spans;

one node may consume multiple ordered spans;

one block may be divided into multiple non-overlapping spans;

table-cell text is owned by the cell-content layer, not simultaneously by a top-level point;

concatenating primary spans in source order must reconstruct the exact unsegmented clause text byte-for-byte;

marker spans and marker-stripped content remain separate derived fields.

“Node order contiguous” should also be replaced by:

unique sibling ordinal within parent
deterministic tree preorder
contiguous primary leaf-source order

List items and tables should be first-class, with one qualification. A point or subparagraph is first-class only when its boundary is source-explicit or deterministically admitted by a versioned marker rule. Otherwise the containing text remains a paragraph with unresolved_structure.

A table should be a first-class node, and rows/cells should be first-class relational children. Paragraphs and lists inside a cell should be represented as ordered cell_content records or nodes owned by that cell; they must not enter the clause-level hierarchy as siblings of the table.

The four proposed table-cell states are not sufficient, and implicit_carry currently overstates source evidence. Copying the prior cell’s normalized text/hash into an omitted cell risks making derived meaning look physically present.

Use two orthogonal dimensions instead of one overloaded enum:

physical_state =
  present_text
  | present_empty
  | explicit_covered
  | source_repeated
  | physically_omitted
  | unresolved

logical_value_state =
  own_source_value
  | covered_from_origin
  | policy_carried_from_origin
  | none
  | unresolved

For covered_from_origin and policy_carried_from_origin:

value_origin_cell_id is mandatory;

the origin must be in the same table and geometrically valid;

origin chains and cycles are forbidden;

only the origin cell has physical source-text mappings;

a carried cell has no source-text hash of its own;

it may have a separate logical_value_hash;

policy_carried_from_origin must record a versioned table-role policy and receipt;

source-exact text reconstruction must ignore carried display values.

source_repeated is necessary because an ODT repetition attribute is different evidence from an omitted value inferred by policy.

clause_version needs an explicit completeness contract before it can become the reader’s complete version. An official amendment may be a patch, may omit unchanged text, or may contain “以下略.” Such material must not close the prior complete expression or become the latest complete clause merely because parsing succeeded.

Add:

expression_completeness =
  source_complete
  | verified_composite
  | patch_only
  | partial
  | unresolved

A verified_composite additionally requires an immutable composition manifest binding every component, source artifact, span, assembly rule, and review receipt.

Only source_complete or verified_composite may enter the complete-clause reader. patch_only remains an announced amendment/effect object.

“Latest complete clause” must not mean greatest load time or greatest expression number. The reader selector must distinguish:

current_effective_complete
future_announced_complete
prior_effective
conflicted
unresolved

The currently effective complete clause remains first. A future complete expression belongs in an announced section. “Move the prior complete version into history” must be implemented as an append-only activation/effective-state event or relation—not a physical row move or mutation of its text.

Direct-predecessor status must be explicit and must gate diff generation. Comparing two stored expressions does not prove that no intermediate legal state existed.

Add a version relation such as:

clause_expression_relation(
  older_expression_id,
  newer_expression_id,
  relation_status,
  evidence_receipt_id
)

relation_status =
  direct_predecessor_verified
  | previous_available_expression_only
  | unresolved
  | conflicted

A direct-predecessor diff may be published only for direct_predecessor_verified. Otherwise the reader must say:

Compared with the previous available expression; direct legal adjacency is not established.

No predecessor edge may be inferred from edition order, load order, source-block similarity, or designation equality.

Tree-first alignment and PostgreSQL-stored inline diff are correct, but the proposed ignored-change policy is unsafe. Git’s Myers, minimal, patience, and histogram algorithms are sequence-alignment choices; none establishes legal identity or substantive equivalence. 
Git

Required repairs:

rename “semantic inline segments” to deterministic inline_diff_segment unless actual semantic adjudication exists;

store both an exact diff and a display classification;

never remove a source difference from the exact diff;

full-/half-width, quote, punctuation, number, comparator, unit, and code changes must not be globally ignored;

formatting-only suppression may occur only through a node-type-specific, versioned policy;

every suppressed difference remains inspectable and counted;

algorithm, tokenizer, tie-break rules, Unicode profile, normalization policy, and implementation version must be fingerprinted.

Required reconstruction invariants:

concatenate(old-side unchanged + deleted/replaced segments)
  = exact old node text

concatenate(new-side unchanged + inserted/replaced segments)
  = exact new node text

Both scalar and UTF-8 byte ranges should round-trip to the stored exact text.

Table comparison cannot rely on coordinates alone. If one row is inserted near the top, coordinate-only comparison can make every subsequent row appear changed.

Minimum table alignment sequence:

verified row/cell Work identity, when available;

exact unique row signature under the same table role;

bounded deterministic candidate alignment;

otherwise alignment_unresolved.

Coordinates remain source-location evidence, not persistent identity. Header changes, inserted columns, reordered rows, and changed rowspan geometry must be represented separately from cell-text changes.

Unresolved alignment must not be converted into a misleading inline rewrite. When two nodes cannot be safely aligned:

do not pair them merely because their text is similar;

show explicit old-only/new-only blocks or an unresolved-alignment notice;

do not label the event a move or rewrite;

do not collapse the uncertainty in the reader.

The deterministic/agentic boundary is otherwise adequate. Keep the agent limited to anomaly detection and rule proposals. Add one explicit prohibition:

An agent proposal may not assign node_work_id,
relation_status=direct_predecessor_verified,
expression_completeness=verified_composite,
or formatting-equivalence status.

Those values must arise from an admitted deterministic rule or an independent human authorization followed by full replay.

Split desktop and unified mobile are appropriate. GitHub’s split and unified views support the intended interaction analogy, but the legal reader must use its own provenance and uncertainty wording. 
GitHub Docs

Release requirements:

identical normalized source data in both layouts;

unchanged sections may be collapsed but must show an explicit omitted-context count and “expand all”;

additions/removals must use icon/text/style, not color alone;

keyboard-accessible toggle and expansion controls;

screen-reader labels such as “added in newer expression” and “not present in newer expression”;

mobile DOM order must remain old-context then new-context, not visual CSS reordering;

any unresolved adjacency or alignment warning must remain visible in both modes.

The migration must be shadow/additive and activation-based. The staged migration should not be applied to production until it supports:

a separate normalization run bound to exact source-publication run, parser, rules, migration, and input fingerprints;

a separate diff run bound to exact old/new expression hashes and alignment policy;

transactionally sealed rows;

fresh-connection recount and per-table fingerprints;

append-only activation/deactivation;

no destructive rollback after any evidence rows exist;

API and static exporter reading only an explicitly activated sealed run;

PostgreSQL↔JSONL↔SQLite logical-row parity;

no modification of existing production clause text or reader during backfill.

Sealed-run mutation probes must cover child and parent INSERT, UPDATE, DELETE, TRUNCATE, ON CONFLICT DO UPDATE, nonempty COPY FROM, and activation-history mutation.

Additional relational invariants required before sealing:

exactly one clause root per expression
parent and child belong to the same expression
no tree cycles
child depth = parent depth + 1
unique sibling ordinal
work identity assignment is append-only
source-span primary coverage is total and non-overlapping
exact clause reconstruction succeeds
table dimensions and origin geometry are valid
no policy-carried value is represented as physical source text
complete expressions contain no unresolved primary structure
selectable effective expressions do not overlap
direct-predecessor diff requires a verified relation
every diff reconstructs both exact sides
API/export fingerprints bind normalization and diff runs

Minimum adversarial fixture set before canonical deployment:

one physical paragraph containing two list markers;

one point spanning multiple source blocks;

inserted list point shifting all later marker ordinals;

same marker reused for a different legal object;

exact text moved to another parent;

one-to-two split and two-to-one merge;

A → B → A, proving equal text hash does not imply one episode;

patch-only amendment with omitted unchanged text;

table row insertion without cascading false changes;

explicit rowspan/colspan, ODT repeated cells, physical empty cells, omitted carry cells, and malformed spans;

full-width digit/comparator or quote change inside a drug code, unit, or threshold;

actual serialization-only whitespace change;

duplicate identical rows producing ambiguous alignment;

same URL with changed source bytes;

missing direct predecessor or known historical gap;

addition-only and removal-only changes;

unresolved source structure requiring source-exact fallback;

desktop/mobile render parity, accessibility, and exact source-text round-trip.

Smallest required repair set

Replace work_node_key authority with persistent node_work, expression-local nodes, identity assertions, and many-to-many lineage relations.

Replace one-block/one-node conservation with exact source-span ownership and byte/scalar round-trip.

Split table physical state from logical carried value; add repeated, omitted, and unresolved states.

Add expression completeness and prevent patch-only material from becoming a complete canonical version.

Add explicit direct-predecessor status and prohibit history diff publication without verified adjacency.

Store exact diff separately from display suppression; remove global quote/full-width ignoring and add exact reconstruction invariants.

Make migration shadow-loaded, run-scoped, sealed, append-only activated, parity-verified, and non-destructively reversible.

After those repairs, the remaining full replay, live PostgreSQL seal, exporter parity, visual audit, and post-verification Pro review are sufficient gates; no broader architecture replacement is required.
