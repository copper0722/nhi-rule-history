# `通則` single-clause history template

## Canonical unit

The canonical source-observed version unit is **one top-level clause**, not the
whole `通則` chapter.

- `通則` is the official source designation.
- `chapter:00` and `0.1`–`0.12` are project-assigned navigation codes.
- `nhi_rule_history_edition` preserves each complete source edition as an
  upstream container.
- `nhi_rule_history_clause` preserves one independent version chain per
  top-level clause.

Nested paragraphs and numbered list items remain structured blocks inside their
parent top-level clause. They can be used as diff anchors, but this template
does not give them independent reader pages or version identities.

## Sealed result

The single-clause import is:

| Object | Count |
|---|---:|
| Source editions | 15 |
| Chapters | 1 |
| Clauses | 12 |
| Source-edition observations | 152 |
| Distinct consecutive clause text states | 29 |
| Clause-version blocks | 318 |
| In-text date annotations | 261 |
| Same-clause version edges | 17 |
| Diff hunks | 26 |
| Semantic diff presentations | 26 |
| Reader semantic tags | 81 |
| ATC code relations | 70 |
| ICD-11 code-only relations | 21 |
| Condition marker rules | 23 |
| Agent history summaries | 1 |
| Per-clause coverage assessments | 12 |

Run ID:
`3873fcbc-a2e1-5ac4-9b2c-64ab3f1da9b9`.

Source-set SHA-256:
`537a2aaf4e47987d053da35e10c2cfd58e4b36c760c9dc1ba41ca878b6156034`.

Output SHA-256:
`4eb242934844c106d4bc40ae21ab870990530b42b0f609c50b2217d7c55265e8`.

The result demonstrates why edition and clause versions must be separate.
Clause `0.2` appears in all 15 source editions but has one normalized text
state and no diff edge. Clause `0.4` appears in all 15 editions and has ten
text states with nine adjacent-state edges.

## PostgreSQL normalization

The additive `nhi_rule_history_clause` schema contains:

| Table | Responsibility |
|---|---|
| `import_run` | immutable source-set, algorithms, counts and seal |
| `chapter` | official source label plus project navigation provenance |
| `clause` | stable single-clause identity within the declared edition set |
| `clause_version` | one consecutive normalized text state |
| `clause_version_observation` | every source edition in which that state appears |
| `clause_version_block` | ordered blocks within the representative clause text |
| `clause_version_date` | in-text date candidates without legal promotion |
| `clause_version_edge` | adjacent distinct text states of the same clause |
| `clause_diff_hunk` | stored removed/added/replaced segments |
| `diff_run` / `diff_hunk_presentation` | sealed reader-facing semantic diff |
| `reader_enrichment_run` | sealed semantic-tag and summary generator receipt |
| `clause_semantic_tag` | exact drug, class, brand, disease and treatment terms |
| `clause_semantic_tag_atc` | code-only ATC relations used by this clause |
| `clause_semantic_tag_icd11_code` | public code-only relation and review state |
| `clause_semantic_tag_icd11_private` | private title/URI/full mapping evidence |
| `clause_semantic_tag_nhi_treatment` | current NHI medical-service payment codes |
| `clause_condition_marker` | longest-first restriction/requirement lexicon |
| `clause_condition_expression` | parsed comparator/value/unit/action formulas |
| `agent_history_summary` | agent summary bound to the source diff hash |
| `coverage_assessment` | per-clause denominator and claim limits |

Sealed enrichment is database-owned evidence, not a mutable cache. PostgreSQL
allows child inserts only while the parent run is `loading`, verifies all nine
child-table counts on the single `loading → sealed` transition, and rejects all
later parent or child updates, deletes and truncates. Nine enrichment child
tables carry both row-DML and truncate guards; public ICD code rows and private
ICD mapping rows have independent receipt counts. The forward/rollback
migrations are exercised on disposable PostgreSQL; the live schema has a
transactional fresh-seal probe and a separate adversarial mutation probe. Both
probes leave no test rows behind.

PostgreSQL is the only writable authority. JSONL, SQLite and reader JSON are
generated read-only projections.

## Segmentation and identity

For every sealed source edition, the importer:

1. reads the already verified `通則` source container;
2. splits at contiguous top-level Chinese ordinals such as `一、`, `二、`;
3. assigns project code `0.<ordinal>`;
4. preserves every source block, order and locator;
5. validates that each code occurs at most once per source edition;
6. records every successful edition occurrence separately.

Identity status is
`verified_within_declared_edition_set`. This does not prove that a clause was
never split, moved, restored or renumbered outside the 15-edition source set.

## Text-state collapse

An annual source observation is not automatically a new clause version.
Consecutive observations are the same text state when their NFKC-normalized,
case-folded, whitespace-insensitive comparison text is equal.

Every annual observation remains queryable through
`clause_version_observation`; only duplicate display versions are collapsed.
If identical text returns after a different state, it becomes a new later
state because version identity includes its position in the clause chain.

## Diff and chronology contract

Each edge:

- stays within one `clause_id`;
- connects adjacent **distinct text states**;
- has
  `adjacency_basis=adjacent_distinct_text_state_across_official_editions`;
- has `legal_predecessor_status=not_claimed`;
- has `crosses_known_gap=true`;
- contains at least one stored diff hunk.

The browser never computes adjacency or a published diff. It reads the stored
edge and hunk rows from the PG projection. The newest selected clause appears
in full; each older row shows only what that older state loses and what the
next state gains.

### Semantic diff presentation

The stored source hunk is never overwritten. A second sealed presentation
layer normalizes only for comparison:

- Unicode whitespace is ignored;
- straight, curly and full-width single quotes are equivalent;
- NFKC-equivalent full-width and half-width characters are equivalent.

Display text always comes from the exact stored clause text. History rows are
ordered newest first and use the displayed newer version as the grammatical
subject. If `ABC` becomes `ABCD`, the reader emits only `本版新增 D`; it
does not invent a red `本版刪除 ABC` block. A true replacement may show both
sides. A format-only difference remains auditable in PostgreSQL but is not
shown as a substantive reader change.

### Reader date label

For one adjacent clause-version edge, the reader compares the structured
date-fact sets and selects the latest date annotation that first appears in
the newer text. It renders only ROC year/month (`99/11`) as the primary label.
The source-edition range remains visible as provenance. This is a reader
label for the text annotation, not a promoted legal effective date.

Edition labels and in-text slash dates do not automatically become legal
effective dates. Per-clause coverage therefore remains:

```text
official_source_universe_closed = false
legal_history_complete = false
```

## Reader enrichment boundary

The `0.4` prototype performs longest-match-first conditional rendering:

- drug ingredients, brands and classes link to a local tag page and show the
  term only in the clause text; ATC codes appear after the reader opens the tag
  page;
- disease terms link to a local tag page, while ICD-11 codes likewise remain
  off the clause text and appear only on that tag page;
- coded treatment terms link to the same local tag system. `CAPD` uses the
  current NHI medical-service payment standard: `58011C` is the core service
  relation and the directly related instruction, material and catheter-service
  codes remain separate rows;
- `agent_selected` codes are visually distinct from `candidate` codes;
- broad terms with no defensible single code remain `待判讀`;
- phrases such as `不得` and prior-authorization expressions are highlighted
  by semantic role; `且` and `或` share one logical-word style, while the
  subjective term `需要` and low-information `至多`／`應` are deliberately
  not emphasized;
- atomic duration expressions are programmatically extracted as quantity plus time
  unit or a recurring time unit across every stored version of the clause,
  then rendered with one `duration` style (for example `二週`, `六天`,
  `一個月`, `每週`);
- atomic count expressions relevant to a changed limit are extracted into the same
  marker schema with role `quantity` (currently `15支` and `20支`) and share
  the value-emphasis style with durations;
- detected contiguous compound-condition expressions take precedence over
  atomic markers. The
  normalized row stores comparator, numeric value, unit, action, action count
  and severity. `不超過20,000U`, `不得超過15支`,
  `不得超過20支`, `一個月為限` and `每三個月應追蹤一次`
  therefore each render as one critical-red expression. This does not yet
  group an implicit alternative such as the later `或100mcg` into the same
  parent expression;
- parenthesized ROC dates use a smaller typographic level.

Latin-script terms are matched at token boundaries, not as substrings inside a
different drug name. The colored inline term is the entire link; terminology
codes and mapping status belong to the tag detail page rather than the clause
reading surface.

Semantic-link admission is `coding-able`, not merely “medical-looking”. A term
must have a project-adjudicated ATC, ICD-11 or NHI treatment-code relation
before it becomes a link. The latest clause and every stored transition hunk
are scanned, so historical-only ingredients and brands can be admitted.
`CAPD` is linked through the NHI payment standard; `透析液`, `抗生素`,
`抗凝血劑`, coagulation-factor classes and other ATC-addressable terms are
linked. Broad `癌症` is not linked because no single defensible ICD-11 relation
was selected.

User-configurable condition visibility and colors are a future presentation
feature. The browser may hide a parsed type or map it to a user-selected color,
and may restore the project default palette. Settings must never rewrite PG
expressions, severity, source text or diffs. The default remains all detected
compound conditions visible in critical red.

For long clauses, a frozen reader dock keeps the selected clause identity and
global clause search visible while scrolling on desktop. At phone widths, the
dock is replaced by a bottom floating island so the article keeps its vertical
reading space. `目錄` opens a slide-in section list with scroll-position
tracking; `搜尋` opens the same clause search in a bottom sheet. Desktop search
results remain an anchored dropdown, and neither mode reflows the article.

The public ICD projection contains only the project-authored term→code
relation, rank, confidence and review state. WHO titles, URIs, definitions and
the ICD reference snapshot remain in private PostgreSQL and are absent from
JSONL, SQLite and the static site. Code-only rows are not represented as WHO
endorsement or as completed clinical coding advice.

The `agent_history_summary` is generated from the sealed same-clause diff,
stores the exact source edge IDs and diff hash, and appears immediately above
the history under the exact heading
`歷史變更總覽（本節由生成式AI輸出）`. Its
`agent_generated_unreviewed` state stays visible; it never replaces official
clause text.

## Rebuild

Apply the additive migration, then run:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00_clauses.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00-clauses \
  --reader-dir prototype/reader/data/clauses \
  --sqlite-output /tmp/nhi-rule-history-chapter-00-clauses.sqlite
```

The command validates the sealed edition import, builds the single-clause rows
inside one PostgreSQL transaction, verifies exact identities and cardinalities,
seals the import, then reads it back to generate:

- public JSONL with per-file hashes and counts;
- a portable SQLite snapshot with foreign-key and integrity checks;
- `index.json` plus one reader JSON file per clause.

Replaying the same source set returns the same sealed import. SQLite is
byte-identical when rebuilt with the same SQLite library version; its header
records the writer version, so a different library can change the file SHA
without changing any row. The builder therefore also emits a
runtime-independent logical SHA over the ordered JSONL table inputs plus the
builder Python/SQLite versions. A new official source edition creates a new
source-set identity; it cannot overwrite the earlier sealed receipt.

## Public artifacts

- [Clause JSONL manifest](../data/templates/chapter-00-clauses/manifest.json)
- [Portable SQLite schema](../database/clause-sqlite-schema.sql)
- [Reader clause index](../prototype/reader/data/clauses/index.json)
- [Example `0.4` reader projection](../prototype/reader/data/clauses/0.4.json)
- [Public `0.4` prototype](https://copper0722.github.io/nhi-rule-history/?rule=0.4)

The earlier [`chapter-00`](../data/templates/chapter-00/) export remains useful
as source-edition provenance. It is not the canonical clause-version dataset.
