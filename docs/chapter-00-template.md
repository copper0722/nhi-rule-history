# `通則` cumulative-edition template

## What this template establishes

`通則` is the first end-to-end example in which PostgreSQL owns the complete
normalized source-edition sequence and every downstream artifact is generated.
The reader label is `通則`. `chapter:00` is a project-assigned navigation code,
not an official chapter number.

The sealed import contains:

| Object | Count |
|---|---:|
| Rules | 1 |
| Official source documents | 16 |
| Cumulative rule versions | 15 |
| Version-source links | 16 |
| Typed date facts | 523 |
| Logical text blocks | 743 |
| Adjacent-edition comparison edges | 14 |
| Substantive diff hunks | 26 |
| Coverage assessments | 1 |

The 16 documents are 14 historical whole-document ODT files, the current
official `通則` ODT, and the current whole-document ODT used as a cross-check.
The current chapter and whole-document observations have
`format_only_difference` parity.

The declared edition sequence is complete at 15 versions and 14 edges. This is
not a claim of complete legal amendment history:

- `official_source_universe_closed=false`;
- `legal_history_complete=false`;
- every edge uses `adjacency_basis=adjacent_official_edition`;
- every edge uses `legal_predecessor_status=not_claimed`;
- every edge records `crosses_known_gap=true`.

## PostgreSQL authority

Schema `nhi_rule_history_edition` is the sole writable authority for this
template:

| Table | Responsibility |
|---|---|
| `import_run` | immutable source-set identity, algorithm versions, counts, seal |
| `source_document` | official URL, artifact hash, byte size, source-stage locator |
| `rule` | stable project identity and navigation provenance |
| `rule_version` | ordered full raw and normalized snapshots |
| `version_source` | primary-source and whole-document cross-check relationships |
| `rule_version_date` | typed source dates without silent legal promotion |
| `rule_block` | ordered logical blocks with raw text and exact locators |
| `version_edge` | explicitly selected adjacent-edition comparisons |
| `diff_hunk` | stored human-readable changes and inline segments |
| `coverage_assessment` | declared denominator and non-completeness claims |

JSONL, SQLite, and browser JSON are read-only projections. Corrections enter
through a PostgreSQL migration or importer and are then re-exported; no
projection is edited as a second master.

## Extraction contract

The importer reads only sealed source-stage rows. For each historical whole
document it:

1. finds the exact normalized heading `藥品給付規定通則` or
   `全民健康保險藥品給付規定通則`;
2. begins after that heading;
3. ends immediately before `第1章` or `第1節`;
4. preserves each source block's raw text, ID, order, and locator;
5. normalizes Unicode and horizontal whitespace for display;
6. joins apparent soft-wrapped lines only when the preceding block lacks
   terminal punctuation and the next line is not a new list item.

For the current chapter-only document, end-of-file is an allowed boundary. The
same text is independently extracted from the current whole-document ODT and
stored as a cross-check. A content mismatch would be visible in
`version_source.parity_status`; it cannot be silently discarded.

## Date-role contract

Each date observation has:

- exact `raw_value`;
- calendar system;
- normalized date when valid;
- precision (`year`, `month`, or `day`);
- evidence basis and source locator;
- explicit legal-effect status.

Historical labels such as `98年版` and `96年7月版` are
`official_edition_label`. The current `113.05.28更新` value is
`official_update_date`. None is promoted to a legal effective date:
`legal_effective_status=not_claimed`.

Slash dates observed inside the text are stored as
`text_amendment_annotation` with
`legal_effective_status=candidate_unresolved`. A malformed or non-date token is
rejected rather than coerced. These annotations can drive later completeness
work, but they do not themselves create versions or legal events.

## Diff contract

Every diff compares one captured edition only with the next captured edition.
The browser does not calculate or reinterpret the comparison.

The deterministic pipeline:

1. aligns logical blocks using normalized comparison keys;
2. groups changed blocks under the nearest structural heading;
3. classifies the change as added, removed, or replaced;
4. tokenizes Chinese characters, Latin drug terms, numbers, punctuation, and
   whitespace for phrase-level emphasis;
5. stores old/new block IDs, text, hashes, inline segments, ordinal, input
   hashes, output hash, and algorithm version in PostgreSQL.

An edition transition with no substantive hunk remains a real edge and appears
in the reader as “未觀察到實質文字變更”.

### Identity-collision lesson

The first dry run derived `edge_id` only from the old and new normalized text
hashes. Three unchanged-text transitions collided, so 14 intended edges became
11 PostgreSQL rows. The new schema was rolled back exactly, reapplied, and the
identity contract was corrected to include both version IDs as well as content
hashes.

The loader now verifies the exact version, edge, and hunk identity sets and
counts before sealing. Diff algorithm version
`chapter-00-reader-diff/v1.1` records this correction. This is why stable
relationship IDs must identify the relationship, not just its possibly
repeated payload.

## Rebuild and update

Apply the idempotent schema migration, then run:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00 \
  --reader-json prototype/reader/data/chapter-00-reader.json \
  --sqlite-output /tmp/nhi-rule-history-chapter-00.sqlite
```

The command:

1. resolves the accepted annual source-stage run and current official capture;
2. verifies the exact source set and computes `source_set_sha256`;
3. extracts and validates the 15 full snapshots;
4. writes normalized rows in one PostgreSQL transaction;
5. verifies row identities, foreign relationships, chain cardinality, and
   source cross-checks;
6. seals `import_run` with exact counts and `output_sha256`;
7. reads the sealed rows back into JSONL;
8. builds and verifies SQLite when requested;
9. generates the static reader projection.

Replaying the same source set is idempotent: it returns the same sealed import
and regenerates the projections. A different source set creates a different
run identity and cannot overwrite the earlier seal.

For a future official update, acquisition and structural parsing therefore run
first. The edition importer sees the new sealed source set, appends the new
normalized version and comparison edge in PostgreSQL, and regenerates public
artifacts. The frontend itself contains no update logic.

## Public exchange

[`../data/templates/chapter-00/manifest.json`](../data/templates/chapter-00/manifest.json)
describes ten JSONL files with per-file counts, byte sizes, and SHA-256 hashes.
This is the GitHub-friendly exchange format and can be consumed without
PostgreSQL.

[`../database/edition-sqlite-schema.sql`](../database/edition-sqlite-schema.sql)
allows the same rows to be loaded into a portable SQLite file. The tested
projection passes `foreign_key_check`, `integrity_check`, and exact table-count
parity. SQLite is a distribution format, not an upstream write path.

The browser payload at
[`../prototype/reader/data/chapter-00-reader.json`](../prototype/reader/data/chapter-00-reader.json)
is smaller and purpose-specific. It includes the latest full text and the
historical transition hunks needed by the page, but it is not the complete
database export.
