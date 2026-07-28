---
summary: Public repository authority for reproducible Taiwan NHI drug reimbursement-rule history.
status: active
owner: copper
privacy: public
audience: machine
---

# nhi-rule-history

This repository is a public data-engineering project. It owns acquisition and
update workflows, parser and database code, portable schemas, data manifests,
project state, gap tracking, and acceptance evidence.

## Canonical boundaries

- PostgreSQL is the sole writable authority for normalized rule content,
  source-observed dates, version relationships, diff annotations, linkage, and
  build state.
- Git owns public code, contracts, source manifests, deterministic PostgreSQL
  migrations/importers/exporters, read-only JSONL releases, tests, project
  state, and audit evidence.
- Released JSONL and SQLite files are immutable portable projections of a
  sealed PostgreSQL import. They support public reuse without requiring
  PostgreSQL, but are never accepted as upstream edit surfaces.
- Official source binaries are distributed as checksum-addressed GitHub Release
  assets. They are not committed repeatedly into Git history.
- Historical `tw_drug.rule_*` installations are legacy discovery inputs only.
  They are not source authority and are not part of the public dataset contract.

## Resume contract

At the start of every project turn:

1. Read `project.yaml`, `STATUS.md`, `WORKLOG.md`, and the newest relevant
   evidence file.
2. Verify the real database or release surface before changing a gate.
3. Update `project.yaml`, `STATUS.md`, and `WORKLOG.md` together whenever a gate
   changes.
4. Keep public files free of credentials, private hostnames, private paths,
   private conversation URLs, and private service identifiers.

## Hard truth rules

- The accepted 14-ODT run is source-occurrence staging, not complete legal
  history.
- `9,303` means dotted-designation occurrence candidates, not unique rules,
  versions, or amendments.
- Capture, post, file-modification, or database timestamps are never legal
  effective dates.
- Article number is a historical designation, not stable identity.
- Split, merge, move, restore, correction, and number reuse fail closed.
- Published diffs compare direct predecessors only and retain source locators.
- Never publish a complete-history claim while a required gate in
  `project.yaml` is not `passed`.

## External terminology boundaries

- NHI source data and this project's derived data must retain source
  attribution.
- Do not mirror or sublicense the complete ATC/DDD index. Public mappings may
  include ATC codes already present in NHI/TFDA open data, mapping provenance,
  source version, and links to the official index.
- Do not publish populated ICD-11 crosswalks without the separate written
  agreement required by WHO. The public repo may contain linkage schema,
  indication spans, integration code, and unpopulated examples.
- Third-party terminology content is excluded from this repository's data
  licence unless explicitly stated otherwise.

## Acceptance authority

The producer of extraction or database code never self-accepts its own
promotion. Real migration, rollback, immutable sealing, exact round trips, and
release fingerprints require independent verification.

The definitive gates are in `docs/completion-contract.md`; active gaps are in
`docs/gap-register.md`; `project.yaml` is the machine-readable state authority.
