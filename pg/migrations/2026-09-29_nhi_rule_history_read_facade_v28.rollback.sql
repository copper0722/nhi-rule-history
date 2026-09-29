-- Rollback of v28: drop the read-only facade schema nhi_rules.
-- The schema holds views only, so no row is written or removed anywhere.
-- RESTRICT, not CASCADE: if anything outside the schema depends on a facade
-- view, or a view was added to the schema later, this fails and changes
-- nothing instead of dropping it silently.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-read-facade-v28', 0)
);

DROP VIEW IF EXISTS
  nhi_rules.release_state,
  nhi_rules.view_catalog,
  nhi_rules.notice,
  nhi_rules.general_principle_edition,
  nhi_rules.general_principle_change,
  nhi_rules.general_principle_version,
  nhi_rules.announced_notice_effect,
  nhi_rules.announced_composed_clause,
  nhi_rules.announced_patch,
  nhi_rules.current_source_file,
  nhi_rules.current_clause_date,
  nhi_rules.current_clause_block,
  nhi_rules.current_clause;

DROP SCHEMA IF EXISTS nhi_rules;

COMMIT;
