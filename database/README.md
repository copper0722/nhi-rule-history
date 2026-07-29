# Database structure and portability

## One authority, three usable formats

```text
official sources -> PostgreSQL (sole writable authority)
                              |
                              +-> JSONL public interchange
                              +-> SQLite portable snapshot
                              +-> reader/API projections
```

- PostgreSQL owns every normalized fact and relationship: source documents,
  rule identity, versions, date roles, blocks, predecessor comparisons, diff
  annotations, linkages, and sealed import receipts.
- JSONL is the canonical **public interchange** for a released PostgreSQL
  projection. It is convenient for GitHub, language-independent tooling, and
  users who do not operate PostgreSQL.
- SQLite is an immutable, searchable portable projection generated from the
  same JSONL export.

No JSONL-, SQLite-, or frontend-only edit is accepted upstream. A correction
changes source evidence or normalized rows in PostgreSQL through a
deterministic importer/migration, seals a new import, and regenerates every
projection.

## Staging schemas now in use

The verified legal-history promotion model remains **unpopulated**. PostgreSQL
currently has isolated evidence stages plus a two-layer normalized `通則`
model:

| PG schema | Purpose | Current sealed scope |
|---|---|---|
| `tw_drug_history_stage` | v1 annual ODT source occurrences | 14 releases, 213,512 blocks |
| `tw_drug_history_acq_stage` | v2 discovery/fetch/raw evidence | 1,719 resources, 1,712 artifacts |
| `tw_drug_history_structural_stage` | v2 ODT blocks/occurrences/issues | 31,377 / 1,228 / 547 |
| `nhi_rule_history_edition` | complete source-edition containers | `通則`: 15 cumulative editions |
| `nhi_rule_history_clause` | canonical single-clause source-observed version chains | 12 clauses, 152 observations, 29 text states, 17 edges, 26 hunks |
| `nhi_rule_history_terminology` | append-only concepts, aliases, external-code links, block scan receipts and exact clause occurrences | v1 reviewed seed: 79 concepts, 371 aliases, 13,874 blocks, 1,916 occurrences |

`nhi_rule_history_edition` is upstream provenance, not the canonical version
unit shown to readers. `nhi_rule_history_clause` gives every top-level clause
its own text states, observations, blocks, dates, edges and diffs. It
intentionally does **not** claim that an edition or in-text date is a legal
effective date, or that adjacent captured text states are direct legal
predecessors. Every edge records `legal_predecessor_status=not_claimed`, and
coverage records that the official source universe is open.

Migrations and rollbacks are under
[../pg/migrations](../pg/migrations). The single-clause `通則` import/export
entry point is
[../tools/rebuild_chapter00_clauses.py](../tools/rebuild_chapter00_clauses.py),
backed by
[../src/nhi_rule_history/clause_history.py](../src/nhi_rule_history/clause_history.py).

Continuous-update migrations are applied in filename order. The additive
`2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.sql` migration
must follow `2026-07-27_nhi_rule_history_update_ops.sql`: it keeps immutable
historical URL observation time separate from the later bounded worker lease,
rejects observations that postdate the worker, and exposes chronological URL
predecessors through `v_url_response_chronology`. Append-time predecessor
columns are compatibility evidence only; consumers must use the view.
Likewise, `content_artifact.first_observed_at` is first row-insertion evidence,
not necessarily the minimum source observation after delayed backfill; derive
the latter from `url_observation`.

The v1 eight-table public exporter has already generated a 1,102,200,832-byte
SQLite snapshot and proved storage-independent JSONL↔SQLite typed-row parity.
The v2 raw/structural release is presently JSONL.zst plus checksummed raw
tar.zst; its equivalent SQLite exporter remains an explicit gap.

The `通則` reader template separately exports the single-clause normalized
tables as JSONL. Its manifest contains row counts, byte sizes and SHA-256
checksums. Replaying those files into the clause SQLite schema has passed
foreign-key and integrity checks with exact table-count parity.

## Core groups

| Group | Tables |
|---|---|
| Source | `dataset_release`, `source_artifact`, `release_artifact` |
| Events | `official_event`, `official_event_effect` |
| Identity | `rule_identity`, `rule_designation`, `rule_lineage_edge` |
| Text | `rule_snapshot`, `snapshot_evidence`, `rule_block` |
| Diff | `comparison_edge`, `diff_hunk` |
| Drug source | `linkage_import_run`, `nhi_drug_item_observation`, `nhi_drug_rule_reference` |
| Drugs | `drug_concept`, `drug_identifier`, `rule_drug_link`, `drug_atc_link` |
| Indications | `indication`, `rule_indication_link`, `external_concept_link` |
| Audit | `build_run`, `build_issue` |
| Search | `search_document`, optional SQLite FTS5 projection |

The reader-facing single-clause template uses this normalized group:

| Group | Tables |
|---|---|
| Import | `import_run`, `coverage_assessment` |
| Source | `source_edition` in public projection; PG FK to edition `rule_version` |
| Identity | `chapter`, `clause` |
| Version/observation | `clause_version`, `clause_version_observation` |
| Dates | `clause_version_date` |
| Text | `clause_version_block` |
| Comparison | `clause_version_edge`, `clause_diff_hunk` |

The terminology projection uses a separate append-only group:

| Group | Tables |
|---|---|
| Stable registry | `concept_registry` |
| Run and activation | `tagging_run`, `tagging_run_activation` |
| Run-scoped concepts | `run_concept`, `concept_seed_tag_link` |
| Match vocabulary | `concept_alias` |
| Public code links | `concept_external_code` |
| Complete scan denominator | `tagging_run_block_input` |
| Exact text locations | `clause_occurrence` |

`clause_occurrence` stores both Unicode-scalar and UTF-8-byte half-open offsets.
The visible substring is therefore reproducible without rewriting official
text. Each alias and occurrence has its own admission status; candidate and
blocked matches remain auditable. A sealed run and all children are immutable,
and activation appends a new pointer instead of mutating the earlier run.

## Files

- [postgresql-schema.sql](postgresql-schema.sql): canonical build schema.
- [sqlite-schema.sql](sqlite-schema.sql): portable logical projection.
- [sqlite-fts.sql](sqlite-fts.sql): optional reader-search index.
- [../tools/build_sqlite.py](../tools/build_sqlite.py): JSONL-to-SQLite builder.
- [../pg/migrations/2026-07-28_nhi_rule_history_edition_v1.sql](../pg/migrations/2026-07-28_nhi_rule_history_edition_v1.sql):
  source-edition container schema.
- [edition-sqlite-schema.sql](edition-sqlite-schema.sql): portable schema for
  the source-edition JSONL export.
- [../pg/migrations/2026-07-28_nhi_rule_history_clause_v1.sql](../pg/migrations/2026-07-28_nhi_rule_history_clause_v1.sql):
  additive PostgreSQL single-clause version schema.
- [clause-sqlite-schema.sql](clause-sqlite-schema.sql): portable schema for the
  canonical single-clause JSONL projection.
- [../pg/migrations/2026-07-29_nhi_rule_history_terminology_v20.sql](../pg/migrations/2026-07-29_nhi_rule_history_terminology_v20.sql):
  append-only PostgreSQL terminology and occurrence schema.
- [terminology-sqlite-schema.sql](terminology-sqlite-schema.sql): portable
  SQLite schema for a released terminology run.
- [terminology-disposable-source-fixture.sql](terminology-disposable-source-fixture.sql):
  minimal source-publication fixture for forward/load/replay/rollback tests.
- [queries/terminology-release-gates.sql](queries/terminology-release-gates.sql):
  read-only reviewed-seed admission and 95→92 external-code reconciliation
  receipt for any sealed tagging run.

## Conversion

Given a release directory containing `<table>.jsonl`:

```bash
python3 tools/build_sqlite.py \
  --input-dir path/to/release-jsonl \
  --output nhi-rule-history.sqlite
```

The builder rejects unknown columns, enables foreign keys, loads tables in
dependency order, and runs `foreign_key_check` plus `integrity_check`.
Its receipt reports both the SQLite-file SHA and a logical SHA over the ordered
JSONL inputs. The file header includes the SQLite writer version, so byte-level
reproducibility is scoped to the same SQLite library version; cross-version
verification uses the logical SHA, exact table counts, foreign keys and
integrity result.

For the `通則` template, the full PG-first rebuild is:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00_clauses.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00-clauses \
  --reader-dir prototype/reader/data/clauses \
  --sqlite-output /tmp/nhi-rule-history-chapter-00-clauses.sqlite
```

The importer verifies the sealed edition source set, segments each edition into
top-level clauses, collapses only consecutive equivalent text observations,
writes one normalized PostgreSQL transaction and seals it with table counts
and an output hash. It then reads the sealed rows back to produce JSONL,
SQLite, and one reader JSON per clause. A replay of the same source set returns
the existing sealed import instead of creating a competing history.

Reader-enrichment immutability can be rechecked against a PostgreSQL instance
after the v14 migration:

```bash
psql -X "$DATABASE_URL" \
  -f database/verify-reader-enrichment-immutability.sql
psql -X "$DATABASE_URL" \
  -f database/verify-reader-enrichment-fresh-seal.sql
```

The first script proves that a sealed parent and all nine child tables reject
inserts, updates, deletes and truncates as applicable. The second clones the
latest sealed rows into a loading probe, seals it through exact count checks,
then rolls the complete transaction back.

## Portability rules

- IDs are strings in the public contract; PostgreSQL may validate them as UUIDs
  in later migrations without changing serialized values.
- Dates and timestamps use ISO 8601 strings in JSONL/SQLite.
- PostgreSQL `jsonb` fields become canonical JSON text in SQLite.
- Booleans become `0`/`1` in SQLite.
- Every data release includes schema version, row counts, SQLite version, and
  SHA-256 checksums.
- FTS is a derived index and may be rebuilt; it is never evidence authority.
- NHI IODE/INAE product rows are source observations. A rule-to-ATC search facet
  is derived through resolved product evidence and never asserts that the rule
  covers the whole ATC class.
