# GPT Pro R3 architecture review — clause normalization and version diff

Please audit this proposed architecture before any canonical PostgreSQL
migration or production deployment. Return `GO` or `REPAIR`, followed by
precise, release-relevant reasons and the smallest required repairs.

## Control record

- decision basis: `nhi-clause-normalization-v25-predecision-20260729`
- risk: R3 (canonical PostgreSQL schema + paid production reader)
- sent from: hm4 Codex controller
- public-data scope only: Taiwan NHI reimbursement-rule text and its source
  locators; no credentials, private ICD-11 content, PHI, or user-entered facts
- current production remains unchanged
- staged repo bases:
  - `nhi-rule-history`:
    `7a352e9c1fb6abc625d0a7283d7ea715c0fe2a84`
  - `copper-panel`: `d884e092bd0e8c694fa5947b3e3ecc5400d4b281`
  - `personal-website-s`:
    `a2ff58d3726399fbb8b2fc2a38ad50f12645b8d2`
- staged migration SHA-256:
  `24d1526e95fbb08ddd0e2acb15650a6c3e37a449fcec3b61abfab5fbe111c180`

## Reader and governance goal

The latest complete clause is shown first. Historical versions are an optional
lower-page add-on. When one long clause changes only one sentence, the history
view should emphasize only the change, while still allowing a Git-style
side-by-side comparison.

New official amendments follow this pipeline:

1. acquire and hash official artifacts;
2. parse exact source blocks and locators;
3. produce a normalized, version-bound legal-document tree;
4. move the prior complete version into history;
5. align the new version only to its direct predecessor;
6. generate structural and inline diffs;
7. seal PostgreSQL rows and receipts;
8. expose the same structure through the API;
9. render the paid page mechanically;
10. let an audit agent inspect only anomalies and propose parser-rule changes.

The agent may not silently edit official wording, numbering, dates, table
values, node identity, or diff output.

## Proposed legal-document model

Use an **Akoma Ntoso-inspired relational projection**, not a claim of complete
Akoma Ntoso XML conformance.

- `clause_version`: one expression of one canonical clause.
- `source_block`: exact text, exact order, artifact hash and source locator.
- `document_node`: an ordered hierarchy using the controlled vocabulary
  `clause`, `hcontainer`, `paragraph`, `point`, `subparagraph`, `table`,
  and later `authorialNote`.
- Each node stores:
  - expression-local `node_id`;
  - nullable parent node;
  - hierarchy depth;
  - exact raw marker (`1.`, `（一）`, `一、`, etc.);
  - marker scheme and numeric ordinal when deterministically available;
  - marker-stripped content plus its hash;
  - exact source-block mapping;
  - `work_node_key` intended for cross-version alignment;
  - `identity_status`: exact clause code, exact structural role, exact marker
    path, version-local, or unresolved.
- A clause with ten numbered items therefore has ten addressable `point`
  nodes. The exact unsegmented source text is still retained independently.
- Parser uncertainty is stored as `unresolved_structure`; no model guess is
  admitted as canonical structure.

Akoma Ntoso references:

- OASIS standard:
  https://www.oasis-open.org/standard/akn-v1-0/
- vocabulary and hierarchy:
  https://docs.oasis-open.org/legaldocml/akn-core/v1.0/akn-core-v1.0-part1-vocabulary.html
- naming and stable/evolving identifiers:
  https://docs.oasis-open.org/legaldocml/akn-nc/v1.0/csd02/akn-nc-v1.0-csd02.html

## Proposed table model

A table is one document node with first-class relational children:

- table metadata: role, renderer profile, row/column/header counts, structure
  hash;
- rows;
- a complete rectangular cell grid;
- cell-to-source-paragraph mappings.

Cell state is explicit:

- `source`: text is physically present in the official source;
- `covered`: source identifies a covered cell belonging to a rowspan/colspan
  origin;
- `implicit_carry`: the official ODT omits a repeated value but does not expose
  span metadata; the deterministic table-role policy carries the prior source
  origin;
- `empty`: no source or carry value.

Every carried or covered cell points to one earlier `source` cell and copies
its exact normalized text/hash. Source mappings remain separate, so the system
never claims repeated text was physically present. Paragraph/list markers
inside a table cell are normalized as cell paragraphs, not promoted to
top-level clause nodes.

`has_table` is derived from table-node count, never entered separately.

## Conservation and sealing invariants

Before a loading run can become sealed:

- every source block maps to exactly one document node;
- node order and block order are contiguous;
- every non-root node has one parent with a shallower hierarchy;
- table nodes own exactly one normalized table; non-table nodes own none;
- every table has exactly `row_count × column_count` cells;
- source paragraph counts equal their exact mappings;
- `covered`/`implicit_carry` cells resolve to one `source` origin and have the
  same text/hash;
- raw text, marker-stripped text, rows, cells, mappings and receipts all have
  replayable hashes;
- sealed rows are immutable;
- a derived public view exposes `has_table`;
- API and static subscriber export fail closed on any mismatch.

Recovery is append-only activation/deactivation plus code rollback. Sealed
source and normalized rows are retained. Destructive schema rollback is only
for never-populated disposable databases.

## Proposed diff model

Comparison is always between one version and its direct predecessor.

1. Align legal-document nodes by stable work identity where exact; otherwise
   use bounded structural/content candidates and retain unresolved alignment.
2. Compare table structure by row/column/cell coordinates and source-origin
   identity, not by flattened PDF lines.
3. Within aligned text nodes/cells, produce semantic inline segments.
4. Ignore only the published policy classes: whitespace, single-quote variant,
   and full-/half-width variants.
5. Addition-only changes must not invent a deletion side; removal-only changes
   must not invent an addition side.
6. Preserve old/new text, ranges, algorithm version, ignored-change policy and
   hashes in PostgreSQL. The frontend never recomputes a diff.

Git algorithms (Myers/minimal/patience/histogram) are possible sequence
alignment tools, but the legal-document tree is the comparison unit before
word-level diff:
https://git-scm.com/docs/diff-algorithm-option.html

## Proposed reader behavior

- Latest complete clause: normal full-text reader, mechanically rendered from
  normalized nodes.
- History: collapsed by default.
- Desktop history: default split view, old on the left and new on the right,
  aligned by node/cell; unchanged context collapsed but expandable.
- Mobile history: unified stacked view because two narrow columns damage
  readability.
- A visible toggle switches `左右比較` / `單欄比較`, analogous to GitHub:
  https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request
- Tables use role-specific responsive renderers, but all consume the same
  normalized table data. No frontend inference of rowspans, carried values,
  list hierarchy, or table membership is allowed.

## Deterministic versus agentic boundary

Deterministic:

- retrieval, hashes, parsing, normalization under versioned rules;
- list-marker recognition under admitted patterns;
- table construction and carry policy;
- source/node/cell conservation checks;
- version activation, direct-predecessor selection and diff generation;
- API projection, subscriber export and responsive rendering;
- test fixtures, negative tests and release receipts.

Agentic audit:

- inspect rows explicitly marked unresolved or anomalous;
- identify new marker/table/layout forms not represented by current rules;
- propose a new versioned deterministic rule and regression fixture;
- adversarially inspect source-to-render parity and diff readability.

Agentic audit is forbidden to:

- edit canonical official text;
- create a legal date or source event;
- choose a node identity silently;
- patch individual production rows instead of changing a tested rule;
- certify its own work without deterministic replay.

Human gate:

- official-source conflict;
- ambiguous node lineage that affects legal history;
- new parser policy with materially different legal meaning;
- release acceptance after all deterministic and Pro audit gates pass.

## Current staged verification

- normalization/unit tests pass for:
  - exact block conservation;
  - missing-cell carry;
  - source rowspan/covered-cell origin;
  - table renderer profile;
  - API rejection of incomplete/non-rectangular structures.
- No v25 schema has been applied to production.
- No API or subscriber deployment has occurred.
- A full data replay, live PG seal, exporter parity, desktop/mobile visual
  audit, and post-verification Pro audit remain mandatory.

## Audit questions

1. Is this faithful enough to established legal-document modeling, or is any
   Akoma Ntoso concept being misused in a way that will harm version identity?
2. Should list items and tables be first-class nodes exactly as proposed?
3. Is `work_node_key + identity_status` sufficient, or must persistent Work
   identity be separated further from Expression-local nodes before release?
4. Are `source/covered/implicit_carry/empty` adequate and honest table states?
5. Is the deterministic/agentic boundary strict enough?
6. Is tree-first alignment plus PG-stored inline diff appropriate?
7. Does split desktop + unified mobile match the human-reading goal?
8. Identify missing invariants, adversarial fixtures, or migration hazards
   that must be repaired before canonical PG deployment.
