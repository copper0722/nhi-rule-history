# GPT Pro post-release audit response — preserved

`DECISION=REPAIR`

The architecture, immutable-run model, full-block scan accounting, dual offset
verification, non-overlap rule, PostgreSQL authority, ICD-11 privacy boundary,
and bounded completeness claim are methodologically sound. No basis exists for
`STOP`.

Three prior release gates are not yet evidenced strongly enough for `GO`.

## Concrete release blockers

### 1. Reviewed-seed admission lineage is not fully proven

The negative canary is useful but does not prove that all 1,294 admitted
occurrences came exclusively from policy-A aliases and reviewed concepts.

Produce one sealed, machine-readable admission receipt proving:

```text
reviewed_seed_tags_conserved = 82/82
admitted_alias_without_reviewed_seed_link = 0
admitted_alias_from_model_only_source = 0
admitted_context_required_alias = 0
admitted_collision_involved_alias = 0
admitted_occurrence_via_nonadmitted_alias = 0
admitted_occurrence_via_unreviewed_concept = 0
```

Because 82 seed tags became 79 concepts, the receipt must enumerate the reviewed
82→79 consolidation decisions. A Gemini-proposed concept merge cannot become
production truth merely because its aliases match deterministically.

### 2. External-code conservation is unresolved

The reviewed input contains:

```text
70 ATC links
21 ICD-11 links
4 NHI treatment links
= 95 source links
```

The live run contains 92 normalized external-code rows. This is not necessarily
wrong—three rows may have collapsed as duplicate concept/code relations—but the
packet does not prove that.

Required reconciliation:

```text
source_external_code_links = 95
normalized_external_code_rows = 92
explicit_duplicate_collapses = 3
unmapped_source_links = 0
conflicting_collapses = 0
```

Each of the 95 source links must map to an output row, including provenance for
many-to-one collapses. Every admitted output must resolve against the pinned
master snapshot and have the correct public-safety state.

### 3. Prior API compatibility and public-filter gates are not explicitly closed

The earlier decision required additive compatibility. Supply a golden API
receipt proving:

```text
existing semantic_tags field remains present and unchanged
terminology_occurrences_v1 exports admitted occurrences only
public candidate occurrences = 0
public blocked occurrences = 0
private ICD-11 titles/URIs/definitions/snapshot payloads = 0
active tagging_run_id and fingerprint are returned or otherwise bound
```

API and paid-site aggregate test counts do not substitute for these exact
assertions.

## Completeness claim

The stated completeness boundary is correct:

> All 639 clauses and 13,874 source blocks in the selected current publication
> were scanned against the admitted reviewed-seed terminology.

It must not be shortened to “complete terminology coverage.” The zero-link
clause 2.6.3 canary correctly demonstrates that corpus-scan completeness and
vocabulary-enumeration completeness are separate claims.

## Smallest repair

No schema migration is required if the existing provenance columns and link
tables can establish the invariants above. Add:

- one immutable `reviewed-seed-admission` reconciliation receipt bound to the
  active run and its fingerprints; and
- one API compatibility/privacy golden fixture in the release gate.

If every required count is zero or exact as specified, the disposition becomes
`GO` without rescanning. If any admitted alias or occurrence lacks reviewed-seed
lineage, any of the 95 external-code links is lost, or public payloads expose
candidate/private data, deactivate the run and rebuild it before restoring
activation.
