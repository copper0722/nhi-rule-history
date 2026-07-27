# Database structure and portability

## One logical model, three formats

```text
normalized JSONL  <->  PostgreSQL build model
        |
        +----------->  SQLite portable snapshot
```

- PostgreSQL is suitable for crawling, curation, replay, validation, and
  concurrent builds.
- JSONL is the canonical public interchange format for a released dataset.
- SQLite is an immutable, searchable projection built from that JSONL.

No SQLite-only edit is accepted upstream. A correction changes source evidence
or normalized JSONL, then rebuilds both databases.

## Staging schemas now in use

The future legal-history model below is **not** populated yet. Current PG has
three isolated evidence stages:

| PG schema | Purpose | Current sealed scope |
|---|---|---|
| `tw_drug_history_stage` | v1 annual ODT source occurrences | 14 releases, 213,512 blocks |
| `tw_drug_history_acq_stage` | v2 discovery/fetch/raw evidence | 1,719 resources, 1,712 artifacts |
| `tw_drug_history_structural_stage` | v2 ODT blocks/occurrences/issues | 31,377 / 1,228 / 547 |

None of these schemas contains a promoted legal effective date, stable rule
identity, current status, predecessor edge, or diff. Their migrations and
rollbacks are under [../pg/migrations](../pg/migrations); loaders are under
[../src/nhi_rule_history/pg](../src/nhi_rule_history/pg).

The v1 eight-table public exporter has already generated a 1,102,200,832-byte
SQLite snapshot and proved storage-independent JSONL↔SQLite typed-row parity.
The v2 raw/structural release is presently JSONL.zst plus checksummed raw
tar.zst; its equivalent SQLite exporter remains an explicit gap.

## Core groups

| Group | Tables |
|---|---|
| Source | `dataset_release`, `source_artifact`, `release_artifact` |
| Events | `official_event`, `official_event_effect` |
| Identity | `rule_identity`, `rule_designation`, `rule_lineage_edge` |
| Text | `rule_snapshot`, `snapshot_evidence`, `rule_block` |
| Diff | `comparison_edge`, `diff_hunk` |
| Drugs | `drug_concept`, `drug_identifier`, `rule_drug_link`, `drug_atc_link` |
| Indications | `indication`, `rule_indication_link`, `external_concept_link` |
| Audit | `build_run`, `build_issue` |
| Search | `search_document`, optional SQLite FTS5 projection |

## Files

- [postgresql-schema.sql](postgresql-schema.sql): canonical build schema.
- [sqlite-schema.sql](sqlite-schema.sql): portable logical projection.
- [sqlite-fts.sql](sqlite-fts.sql): optional reader-search index.
- [../tools/build_sqlite.py](../tools/build_sqlite.py): JSONL-to-SQLite builder.

## Conversion

Given a release directory containing `<table>.jsonl`:

```bash
python3 tools/build_sqlite.py \
  --input-dir path/to/release-jsonl \
  --output nhi-rule-history.sqlite
```

The builder rejects unknown columns, enables foreign keys, loads tables in
dependency order, and runs `foreign_key_check` plus `integrity_check`.

## Portability rules

- IDs are strings in the public contract; PostgreSQL may validate them as UUIDs
  in later migrations without changing serialized values.
- Dates and timestamps use ISO 8601 strings in JSONL/SQLite.
- PostgreSQL `jsonb` fields become canonical JSON text in SQLite.
- Booleans become `0`/`1` in SQLite.
- Every data release includes schema version, row counts, SQLite version, and
  SHA-256 checksums.
- FTS is a derived index and may be rebuilt; it is never evidence authority.
