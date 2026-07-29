# Gemini semantic-alias proposal — 2026-07-29

This directory is a public, candidate-only proposal. It is not a terminology
master, a reviewed crosswalk, or an admitted PostgreSQL enrichment run.

## Scope

The input denominator was the 82 semantic tags already used by the current
reader enrichment. `Gemini 3.6 Flash (High)` grouped identical concepts and
proposed Traditional Chinese, English, abbreviation, spelling, and historical
aliases.

The controller preserved the model output only after proving:

- 82/82 input tag IDs appeared exactly once;
- no source tag ID was missing or duplicated;
- no input code was changed or invented;
- all rows were valid JSONL;
- begin/end completion markers were present in every accepted partition.

The result contains 79 candidate concepts and 371 aliases. Eight normalized
aliases collide across concepts. Those collisions remain blocked from
automatic matching.

## Files

- `candidates.jsonl` — model-proposed concepts and aliases, with stable
  candidate IDs and source-tag references.
- `validation.json` — denominator, completion, conservation, and collision
  checks.

Official ATC, ICD-11, and NHI treatment/payment master rows are deliberately
not copied here. Private ICD-11 titles, URIs, definitions, and reference
snapshots are also excluded. PostgreSQL remains the only writable authority
for those tables.

## Admission rule

The `auto_match` value records Gemini's proposal, not the production decision.
Before an alias can drive deterministic clause rendering it must pass:

1. normalized cross-concept collision review;
2. token-boundary and longest-match tests;
3. code/master-table resolution;
4. exact clause occurrence generation with offsets;
5. independent review and a sealed PostgreSQL enrichment run.

For example, this proposal groups `糖尿病`, `Diabetes mellitus`, `Diabetes`,
and `DM` under one concept. The first three are proposed for deterministic
matching. `DM` is retained for search but marked `context_required`, because it
can also mean dermatomyositis and other concepts.
