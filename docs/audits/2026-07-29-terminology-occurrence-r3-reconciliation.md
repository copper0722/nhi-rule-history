# R3 terminology decision reconciliation

Decision: `REPAIR`, then implement.

Accepted:

- separate immutable terminology projection;
- alias policy A: only reviewed `source_observed` aliases may become admitted
  in v1;
- all Gemini aliases and their matches remain queryable candidates;
- longest-match plus no-overlap, with every same-span cross-concept sense
  blocked in v1;
- candidate/admitted/blocked occurrences share one table; API reads an
  admitted-only view;
- existing `semantic_tags` remains frozen and backward compatible;
- new API field is explicitly versioned and returns run ID plus fingerprint;
- public ICD-11 output is code and relation type only.

Repairs incorporated into the implementation contract:

1. Add a global concept registry separate from per-run projections.
2. Add `tagging_run_block_input` so the 13,874-block scan denominator is
   stored and sealed.
3. Add `concept_seed_tag_link` so 82/82 legacy-tag conservation is enforced.
4. Add append-only activation events; never update an `active` flag on the
   sealed run.
5. Store both Unicode-scalar and UTF-8-byte offsets and verify each against the
   exact block text.
6. Add a PostgreSQL exclusion constraint for admitted ranges.
7. Reject INSERT/UPDATE/DELETE/TRUNCATE against every row owned by a sealed
   run.

Clarification:

- `alias_admitted` is a vocabulary-level authorization based on reviewed seed
  lineage. The exact source span and hash are enforced on each admitted
  `clause_occurrence`; an alias row itself does not claim a clause span.
- Rollback of an activation is a new append-only activation event pointing to
  the previous sealed run. It does not mutate, delete, or recompute either run.
