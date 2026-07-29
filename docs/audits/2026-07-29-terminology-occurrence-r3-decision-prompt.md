# NHI clause terminology / alias / occurrence — R3 design decision

You are the independent pre-implementation decision reviewer. This is an R3
PostgreSQL and reader-API change for the public Taiwan NHI drug reimbursement
rule project. Do not write implementation code. Decide the data model,
admission boundary, matching semantics, and release gates.

## Frozen state fingerprint

- Public repository commit: `a714580`
- Active current-publication run:
  `a707d13a-0b06-5dfe-96b7-6d107ab8793f`
- Current canonical denominator: 639 clauses / 13,874 source blocks
- Existing reviewed reader-enrichment run:
  `44640535-2f19-51d2-afcf-1572fea9be63`
- Existing reviewed seed data:
  - 82 clause-local semantic tags
  - 70 tag-to-ATC rows (55 distinct codes; all resolve in `tw_drug.ref_atc`)
  - 21 tag-to-ICD-11 rows (18 distinct codes; all resolve in the private
    `medical_knowledge.icd11_who` release `2024-01`)
  - 4 tag-to-NHI-treatment rows (all resolve in the current
    `tw_health_open.nhi_payment_standard`)
- Public Gemini proposal:
  - 79 candidate concepts
  - 371 aliases
  - 82/82 source tag IDs conserved exactly once
  - 336 model-proposed `auto_match`
  - 35 `context_required`
  - 8 normalized cross-concept collisions
- Relevant official master counts:
  - ATC: 6,812 rows
  - ICD-11: 34,663 rows, private runtime content
  - NHI treatment/payment: 6,151 rows

The Gemini proposal is candidate-only. Its `auto_match` flag is a model
recommendation, not a production decision.

## Product requirement

PostgreSQL must own normalized concepts, aliases, external-code links, and
exact clause occurrences. A deterministic scanner must process all 639 current
clauses and all 13,874 source blocks. Reader rendering must consume admitted
occurrences, not rerun terminology inference in the browser.

Each occurrence must preserve:

- active publication run and source block identity;
- concept and alias identity;
- exact original-text half-open offsets;
- exact matched text and source-block hash;
- matching rule and admission state;
- no overlapping admitted spans.

Public output may show ATC, NHI treatment/payment, and ICD-11 codes. It must not
export private ICD-11 titles, URIs, definitions, or private terminology
snapshots.

## Proposed normalized PostgreSQL model

Create a separate immutable `nhi_rule_history_terminology` projection:

1. `tagging_run`
   - ties one terminology build to one sealed current-publication run and the
     legacy reviewed enrichment source;
   - records proposal/input hashes, matcher version, counts, fingerprints,
     `loading|sealed`, and activation.
2. `concept`
   - stable concept ID, canonical public labels, concept type, link family,
     provenance, review state.
3. `concept_alias`
   - alias ID, concept ID, original and normalized text, language, alias type,
     source status, match rule, model proposal, production admission,
     ambiguity note, review state.
4. `concept_external_code`
   - concept ID, code system (`ATC|ICD11|NHI_TREATMENT`), code, relation type,
     review state, public-safety flag, source/master snapshot metadata.
5. `clause_occurrence`
   - active publication run, clause code, source block order/ID, concept ID,
     alias ID, exact offsets and matched text, occurrence status
     (`candidate|admitted|blocked`), reason, hashes.

All rows belong to one immutable tagging run. Only a sealed and explicitly
activated run is visible to the API.

## Proposed matching algorithm

For each source block:

1. Build an NFKC + case-folded matching string while retaining a mapping back
   to exact original offsets.
2. Match only aliases whose production match rule permits deterministic
   scanning. `context_required` never auto-admits an occurrence.
3. Require ASCII-alphanumeric token boundaries for Latin/abbreviation aliases.
   CJK phrases use exact substring matching.
4. Sort candidate matches by start offset, then descending normalized alias
   length, then stable alias ID.
5. At the same or overlapping span, longest match wins. Same-span duplicate
   aliases for the same concept collapse to one occurrence. A same-length tie
   across different concepts fails closed as `blocked`.
6. Persist exact original offsets. Assert
   `raw_text[start_offset:end_offset] = matched_text`.
7. Enforce no overlap among `admitted` occurrences in the same source block.

## Proposed two-lane admission policy

- Preserve all 371 Gemini aliases in PostgreSQL as candidate terminology.
- Immediately production-admit only collision-free `source_observed` aliases
  that came from the existing reviewed 82-tag run and whose external codes
  resolve in the applicable master table.
- Scan all 371 aliases across all source blocks. Matches from non-admitted
  model aliases are retained as `candidate` occurrences for later review but
  are not rendered.
- `context_required`, unresolved, collision-involved, or cross-concept-tied
  aliases remain blocked from admitted rendering.
- Do not bulk-match the complete ATC, ICD-11, or NHI master vocabulary in this
  first release. Those masters remain independent authorities and may seed a
  future candidate-discovery lane.

This intentionally distinguishes “full corpus scan” from “complete medical
ontology coverage.”

## Requested decisions

Return a concise but decisive answer with:

1. **GO / REPAIR / STOP** for this model and migration boundary.
2. Whether the proposed five-table separation is correct; list any required
   additional invariant or table.
3. Choose exactly one v1 alias admission policy:
   - A: source-observed aliases only (the proposal above);
   - B: additionally admit collision-free model aliases that actually occur in
     the current corpus and resolve to a reviewed concept/code;
   - C: admit all collision-free Gemini `auto_match` aliases.
4. Decide whether longest-match/no-overlap is correct for phrases that can
   represent both a class and an ingredient/brand. State any exception.
5. Decide whether candidate occurrences belong in the same occurrence table
   or a separate table/view.
6. State minimum pre-live checks, rollback proof, sealed-run mutation probes,
   and post-release API checks.
7. State API compatibility guidance: whether the existing `semantic_tags`
   field should remain while adding a new occurrence contract.
8. Confirm the ICD-11 public/private boundary or correct it.

The controlling priority is reader accuracy and reversible, auditable
PostgreSQL state—not maximizing highlight count.
