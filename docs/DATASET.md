# Public dataset: bounded source-occurrence stage

## What this release is

The `stage-v1` structured release is a lossless, portable projection of one
exact sealed PostgreSQL run:

- run ID and sealed fingerprint are required together;
- all eight `tw_drug_history_stage` tables are exported;
- only reviewed, allowlisted columns are public;
- rows are canonical UTF-8/LF JSONL sorted by the complete primary key;
- the SQLite file contains the same logical rows and enforced foreign keys;
- a storage-independent typed-row SHA-256 proves JSONL/SQLite parity.

This release makes the already accepted 14-ODT source-occurrence stage reusable.
It does **not** turn those rows into normalized legal clauses.

The accepted v1 identity is:

- run ID: `33ce4d34-ab19-40be-bbe6-f7838a97ead5`;
- sealed fingerprint:
  `23d45033ec3601d9b7762b433580c5a6fa62445043eaddfed28f55b7983484ef`;
- typed logical-row digest:
  `eff96dccb7c7f1bcfffeaebe5c499fed6a0b225e49512ff8fcebf26dfaaaecd3`;
- rows: 1 run, 10 input files, 14 releases, 14 artifacts, 14 release/artifact
  links, 213,512 structural blocks, 9,303 occurrence candidates, and 1,667
  stage issues.

## What this release is not

`stage-v1` is not a complete legal history. In particular:

- `occurrence_candidate` is a dotted-designation occurrence, not a stable rule;
- filename chronology is not a legal effective date;
- an article number is not stable identity;
- split, merge, move, restore, correction, and number reuse are unresolved;
- no predecessor edge or reader-facing diff is asserted.

Both `export-manifest.json` and SQLite `dataset_metadata` set
`legal_history_claim` to false. The SQLite constraint refuses any other value.
The exact v1 scope statement is:

> Bounded source-occurrence staging from 14 historical ODT files; not a
> complete legal history and not evidence of legal effective dates.

The separate v2 raw/structural evidence release uses this exact statement:

> Source-local structural observation only; not stable rule identity, legal
> effective date, legal event, current version, predecessor/successor, or diff.

v2 does not yet have a SQLite projection. Until it does, no v2 SQLite parity or
metadata claim is permitted.

## Files

An export work directory contains:

| File | Meaning |
|---|---|
| `rebuild_run.jsonl` | Exact sealed run and verification metadata |
| `run_input_file.jsonl` | Immutable parser/loader input identities |
| `source_release.jsonl` | Source-local release observations |
| `source_artifact.jsonl` | Official ODT artifact identities |
| `release_artifact.jsonl` | Release-to-primary-artifact associations |
| `structural_block.jsonl` | Source text blocks and source locators |
| `occurrence_candidate.jsonl` | Dotted-designation candidates |
| `stage_issue.jsonl` | Deterministic, nonblocking/blocking issue evidence |
| `nhi-rule-history-stage-v1.sqlite` | Portable projection of the same rows |
| `export-manifest.json` | Counts, checksums, non-claim, and parity receipt |

Release preparation compresses each JSONL file with Zstandard when a Python
`zstandard` module or `zstd` command is available. Otherwise the canonical
uncompressed JSONL is retained and the release manifest records
`compression: none`. SQLite remains uncompressed.

## Determinism and parity

Canonical JSON uses sorted object keys, no insignificant whitespace, UTF-8, and
one LF after every row. Timestamps are normalized to UTC ISO 8601 with `Z`.
Booleans remain JSON booleans and become constrained `0`/`1` values in SQLite;
JSON objects/arrays become canonical JSON text in SQLite.

The logical digest is computed table-by-table in the documented table order,
then row-by-row in complete-primary-key order, after restoring these logical
types. It is therefore independent of JSONL versus SQLite storage.

Every preparation run checks:

1. exact run ID plus sealed fingerprint and `state = sealed`;
2. allowlisted columns and unique complete primary keys;
3. canonical UTF-8/LF JSONL;
4. public redaction patterns;
5. per-file checksums and row counts;
6. SQLite `foreign_key_check` and `integrity_check`;
7. typed-row digest parity;
8. explicit non-claim metadata.

## Commands

Use an isolated work directory outside Git. The PostgreSQL command is read-only
and opens a repeatable-read snapshot:

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli export \
  --dsn "$NHI_RULE_HISTORY_DSN" \
  --run-id 33ce4d34-ab19-40be-bbe6-f7838a97ead5 \
  --fingerprint 23d45033ec3601d9b7762b433580c5a6fa62445043eaddfed28f55b7983484ef \
  --output-dir build/stage-v1-export
```

Verify without PostgreSQL:

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-export \
  --input-dir build/stage-v1-export
```

Prepare local assets, but do not publish them:

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli release \
  --export-dir build/stage-v1-export \
  --output-dir build/stage-v1-release
```

The last command has no GitHub or network publication code. Its terminal state
is `prepared_not_published`.
