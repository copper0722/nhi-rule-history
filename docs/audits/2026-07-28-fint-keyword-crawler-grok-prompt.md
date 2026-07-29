# Independent audit: MOHW FINT history crawler and PostgreSQL receipt model

You are the independent read-only critic. Work only in the current
`nhi-rule-history` repository. Do not edit files, invoke subagents, use web
search, access live PostgreSQL, or promote/release anything.

## Work-unit contract

```yaml
work_unit_id: grok-fint-crawler-independent-audit
provider: grok
model: grok-4.5
execution_mode: repo_read
cwd: current nhi-rule-history repository
write_scope: none
input_manifest:
  aggregate_fingerprint: f2c24afee3f10a14e825df9f68214d74dba7a3c0ea420c3e707676793cdf3999
allowed_paths:
  - src/nhi_rule_history/discovery/fint_keyword_crawler.py
  - src/nhi_rule_history/pg/fint_crawl.py
  - pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.sql
  - pg/migrations/2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.rollback.sql
  - tools/crawl_fint_keywords.py
  - tools/load_fint_crawl_pg.py
  - tests/test_fint_keyword_crawler.py
  - tests/test_fint_crawl_pg.py
  - docs/fint-keyword-crawler.md
  - sources/fint-keyword-canary.jsonl
  - sources/fint-keyword-baseline-v1.jsonl
forbidden_paths:
  - credentials
  - unrelated private repositories
canonical_services:
  - PostgreSQL vault_main on hmj
canonical_write_authorized: false
source_claim_boundary: >
  This system may prove exact coverage of a declared query frontier at a
  captured time. It may not prove that every historical notice that ever
  existed remains searchable or that a keyword match establishes a legal
  clause transition.
independent_acceptance:
  reviewer: codex plus a separate verifier
  real_surface_required: true
```

## Observed controller receipts

- CAPD canary: one query, 6 result rows, 6 document snapshots, 6 matches,
  1 declared attachment, 8 HTTP observations, 0 crawler issues.
- One official result declares an apparently unrelated OLE Word attachment.
  The model intentionally preserves the declared edge while leaving relevance
  undecided.
- The broad query `藥品給付規定` reported 1,309 result rows. Its crawl is
  still in progress, so do not treat partial row files as a completed run.
- A known normalized formal-number collision means a formal document number is
  modeled as a grouping key, not as guaranteed one-to-one document identity.
- Disposable PostgreSQL drills already exercised forward migration, rollback,
  idempotent reload, manifest/hash verification, count-bound sealing, and
  rejection of post-seal mutation. These are controller claims to audit
  against the implementation, not facts you may assume.

## Questions to answer

Adversarially inspect the actual files and identify every critical or major
defect you can substantiate, with exact file and line evidence. In particular:

1. Does `FINTQRY03` total-count parsing plus `RowNo=1..N` enumeration fail
   closed on missing, duplicate, reordered, or changed result rows?
2. Are HTTP redirects, TLS validation, size limits, resumability, raw hashes,
   and replay semantics sufficient to prevent silent source substitution or
   partial-run acceptance?
3. Do formal-number groups, document snapshots, query matches, observations,
   and attachment snapshots preserve distinct identities without falsely
   collapsing records?
4. Can a declared but irrelevant attachment be mistaken for legal evidence?
5. Does the PostgreSQL loader verify every material manifest/raw invariant
   before sealing, and does sealing protect every child table and count?
6. Are forward and rollback migrations safe around external dependencies,
   concurrent loaders, already-loaded runs, and trigger/function ownership?
7. Do the tests miss a concrete negative case that could permit a false
   completeness claim?
8. Does the documentation clearly separate:
   declared-frontier acquisition completeness, official-document relevance,
   clause-event linkage, and complete legal history?

## Required output

Return:

1. `VERDICT`: one of `BLOCK`, `REPAIR_THEN_REAUDIT`, or
   `NO_MATERIAL_FINDING`.
2. Findings ordered by severity. Each must contain:
   - severity;
   - exact file and line(s);
   - reproduction or counterexample;
   - violated invariant;
   - minimal repair;
   - required negative test or receipt.
3. False alarms considered and rejected.
4. The narrowest claim that could be accepted now.
5. Remaining gates before live PostgreSQL application and before any claim of
   complete legal history.

Do not praise the design. If no material defect is found, explain which
adversarial cases you actually checked.
