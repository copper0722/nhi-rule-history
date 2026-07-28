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
| `coverage_assessment` | per-clause denominator and claim limits |

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

Edition labels and in-text slash dates do not automatically become legal
effective dates. Per-clause coverage therefore remains:

```text
official_source_universe_closed = false
legal_history_complete = false
```

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

Replaying the same source set returns the same sealed import and byte-identical
SQLite projection. A new official source edition creates a new source-set
identity; it cannot overwrite the earlier sealed receipt.

## Public artifacts

- [Clause JSONL manifest](../data/templates/chapter-00-clauses/manifest.json)
- [Portable SQLite schema](../database/clause-sqlite-schema.sql)
- [Reader clause index](../prototype/reader/data/clauses/index.json)
- [Example `0.4` reader projection](../prototype/reader/data/clauses/0.4.json)

The earlier [`chapter-00`](../data/templates/chapter-00/) export remains useful
as source-edition provenance. It is not the canonical clause-version dataset.
