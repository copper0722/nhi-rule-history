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
currently has isolated evidence stages plus one normalized cumulative-edition
model used by the `通則` reader template:

| PG schema | Purpose | Current sealed scope |
|---|---|---|
| `tw_drug_history_stage` | v1 annual ODT source occurrences | 14 releases, 213,512 blocks |
| `tw_drug_history_acq_stage` | v2 discovery/fetch/raw evidence | 1,719 resources, 1,712 artifacts |
| `tw_drug_history_structural_stage` | v2 ODT blocks/occurrences/issues | 31,377 / 1,228 / 547 |
| `nhi_rule_history_edition` | normalized source-observed cumulative editions | `通則`: 15 versions, 14 adjacent-edition edges, 26 change hunks |

`nhi_rule_history_edition` does contain a stable project rule identity,
source-observed edition sequence, full snapshots, typed date observations,
adjacent-edition comparison edges, and deterministic diffs. It intentionally
does **not** claim that an edition date is a legal effective date or that two
adjacent captured editions are direct legal predecessors. Every edge records
`legal_predecessor_status=not_claimed`, and coverage records that the official
source universe is open.

Migrations and rollbacks are under
[../pg/migrations](../pg/migrations). The `通則` import/export entry point is
[../tools/rebuild_chapter00.py](../tools/rebuild_chapter00.py), backed by
[../src/nhi_rule_history/edition_history.py](../src/nhi_rule_history/edition_history.py).

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

The `通則` template separately exports ten normalized tables as JSONL. Its
manifest contains row counts, byte sizes, and SHA-256 checksums. Replaying those
files into the edition SQLite schema has passed foreign-key and integrity
checks with exact table-count parity.

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

The cumulative-edition template uses a narrower normalized group:

| Group | Tables |
|---|---|
| Import | `import_run`, `coverage_assessment` |
| Source | `source_document` |
| Identity/version | `rule`, `rule_version`, `version_source` |
| Dates | `rule_version_date` |
| Text | `rule_block` |
| Comparison | `version_edge`, `diff_hunk` |

## Files

- [postgresql-schema.sql](postgresql-schema.sql): canonical build schema.
- [sqlite-schema.sql](sqlite-schema.sql): portable logical projection.
- [sqlite-fts.sql](sqlite-fts.sql): optional reader-search index.
- [../tools/build_sqlite.py](../tools/build_sqlite.py): JSONL-to-SQLite builder.
- [../pg/migrations/2026-07-28_nhi_rule_history_edition_v1.sql](../pg/migrations/2026-07-28_nhi_rule_history_edition_v1.sql):
  normalized PostgreSQL cumulative-edition schema.
- [edition-sqlite-schema.sql](edition-sqlite-schema.sql): portable schema for
  the cumulative-edition JSONL export.

## Conversion

Given a release directory containing `<table>.jsonl`:

```bash
python3 tools/build_sqlite.py \
  --input-dir path/to/release-jsonl \
  --output nhi-rule-history.sqlite
```

The builder rejects unknown columns, enables foreign keys, loads tables in
dependency order, and runs `foreign_key_check` plus `integrity_check`.

For the `通則` template, the full PG-first rebuild is:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00 \
  --reader-json prototype/reader/data/chapter-00-reader.json \
  --sqlite-output /tmp/nhi-rule-history-chapter-00.sqlite
```

The importer first verifies source-stage fingerprints, writes one normalized
PostgreSQL transaction, seals the import with table counts and an output hash,
then reads the sealed rows back to produce JSONL, SQLite, and reader JSON. A
replay of the same source set returns the existing sealed import instead of
creating a competing history.

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
