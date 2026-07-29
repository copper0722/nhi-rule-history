# GPT Pro post-release audit prompt — terminology occurrence v20

You are performing a final R3 methodological audit of a public-data system
already deployed after an earlier GPT Pro `REPAIR` decision. Do not rewrite
prose. Decide `GO`, `REPAIR`, or `STOP`, and identify only concrete release
blockers.

## Intended claim

The release is **v1 of a reviewed-seed terminology occurrence layer**, not a
complete medical terminology inventory. It has completely scanned the selected
current 639-clause publication using the current 82-tag reviewed seed. Full-book
vocabulary coverage remains open and is explicitly documented.

## Implemented architecture

- PostgreSQL is the sole writable authority.
- Stable `concept_registry` is separate from append-only run-scoped
  `run_concept`, `concept_seed_tag_link`, `concept_alias`,
  `concept_external_code`, `tagging_run_block_input`, `clause_occurrence`, and
  `tagging_run_activation`.
- Every alias and every occurrence independently records
  admitted/candidate/blocked.
- Every source block receives a scan receipt, including no-match blocks.
- Matcher: longest-match, Latin token-boundary guard, deterministic tie-break,
  source-offset recovery, and no admitted overlap.
- Offsets: Unicode-scalar half-open plus UTF-8-byte half-open; verification
  re-slices the immutable source block.
- The load transaction writes, seals, fresh-verifies, and optionally activates.
  Exact replay returns the existing run.
- Sealed run/registry/children and prior activation mutation probes are rejected.
- Public output includes public codes only. Private ICD-11 title, URI,
  definition, and reference snapshots do not leave PostgreSQL.
- SQLite has an isomorphic portable schema; it is projection, not authority.

## Live receipt

- tagging run: `8d5b7f1d-01cf-5af6-932b-8bb2378f35ff`
- publication: `a707d13a-0b06-5dfe-96b7-6d107ab8793f`
- seed enrichment: `44640535-2f19-51d2-afcf-1572fea9be63`
- 79 concepts, 371 aliases, 92 external-code rows, 82 seed links
- 639/639 clauses and 13,874/13,874 source blocks scanned
- 1,916 occurrences: 1,294 admitted, 192 candidate, 430 blocked
- 0 offset mismatches; 0 admitted-overlap pairs
- API contract:
  `nhi-reimbursement-rules/terminology-occurrences/v1`
- paid-site exporter verifies the active run and fingerprints fail-closed
- browser: clause 0.4 shows linked names, no inline codes, and a
  keyboard-focus code popover
- negative canary: clause 2.6.3 has zero terminology links because ezetimibe,
  statin, gemfibrozil, and 高膽固醇血症 are not yet in the reviewed seed

## Tests

- public repo: 479 run, 472 passed, 7 skipped
- API: 153/153
- paid site: 135/135
- PostgreSQL forward/load/replay/mutation/rollback disposable verification
  passed before live load

## Audit questions

1. Is this `GO` as a reviewed-seed v1 occurrence layer with full-book vocabulary
   explicitly open?
2. Is any schema, immutability, offset, admission, privacy, API, UI, or
   reproducibility defect still a release blocker?
3. Are the completeness claims correctly bounded, especially the distinction
   between complete corpus scanning and incomplete vocabulary enumeration?
4. If `REPAIR`, give the smallest concrete patch and the exact evidence needed
   to close it.
