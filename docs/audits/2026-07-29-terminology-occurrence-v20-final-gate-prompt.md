# GPT Pro final gate follow-up — v20 repair evidence

Your post-release audit returned `REPAIR` with three evidence-only blockers and
said no schema migration or rescan was required if the invariants were exact.
The requested receipts now exist and were recomputed from the live sealed run.
Decide only `GO`, `REPAIR`, or `STOP`.

## 1. Reviewed-seed admission

Machine query:
`database/queries/terminology-release-gates.sql`

Receipt:
`docs/audits/2026-07-29-reviewed-seed-admission-reconciliation.json`

Results:

```text
reviewed_seed_tag_count = 82
linked_seed_tag_count = 82
distinct_linked_seed_tag_count = 82
admitted_alias_without_reviewed_seed_link = 0
admitted_alias_from_model_only_source = 0
admitted_context_required_alias = 0
admitted_collision_involved_alias = 0
admitted_occurrence_via_nonadmitted_alias = 0
admitted_occurrence_via_unreviewed_concept = 0
```

The 82→79 consolidation is exactly three two-member groups. Each is enumerated
with legacy tag IDs, source text, entity type and identical reviewed code:

- apomorphine + Apomorphine hydrochloride → ATC N04BC07
- 靜脈血栓 + VTE → ICD-11 BD72
- filgrastim + filgrastin → ATC L03AA02

The controller admission rule stored on each concept is:
`same seed concept type and identical reviewed external-code set`.

## 2. External-code conservation

```text
source links = 95
  ATC = 70
  ICD11 = 21
  NHI_TREATMENT = 4
normalized rows = 92
explicit duplicate collapses = 3
unmapped source links = 0
source links without output provenance = 0
conflicting collapses = 0
unresolved master rows = 0
non-public-safe rows = 0
```

The three duplicate code rows are the same three consolidation groups above;
the receipt enumerates every involved source tag ID.

## 3. API compatibility and privacy

Receipt:
`docs/audits/2026-07-29-terminology-api-compatibility-golden.json`

```text
semantic_tags present = true
semantic_tags count = 82
semantic_tags canonical SHA-256 =
  7d48e85f0768d0cf46478988cbbf376c5dd3b3a988c0e705c9dcc27ae5a00f74
public terminology count = PG admitted count = 1,294
public candidate count = 0
public blocked count = 0
private ICD-11 forbidden key paths = 0
active run ID + output fingerprint + sealed fingerprint returned = true
all public occurrences carry source hash + scalar/UTF-8 offsets = true
```

The API reads
`nhi_rule_history_terminology.v_admitted_clause_occurrence`. A golden unit test
asserts the legacy semantic-tag payload exactly, the admitted-only selector,
run/fingerprint binding, and the forbidden private/candidate/blocked fields.
The complete API suite is 153/153.

## Boundary unchanged

This remains a reviewed-seed v1 occurrence layer. The selected 639 clauses and
13,874 blocks were fully scanned; full-book vocabulary enumeration remains
open, with 2.6.3 retained as a zero-link negative canary.

Are all three repair conditions closed for `GO`?
