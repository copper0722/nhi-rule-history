-- Rollback of v29: drop the diff-card store nhi_rule_history_diff_card.
-- The tables hold derived card rows only (the loader rebuilds them from the
-- registered corpus bundles), so no source data is lost; rows loaded since the
-- migration are removed with their tables.
-- RESTRICT, not CASCADE: if anything outside the schema depends on one of these
-- objects, or an object was added to the schema later, this fails and changes
-- nothing instead of dropping it silently.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-diff-card-v29', 0)
);

DROP VIEW IF EXISTS nhi_rule_history_diff_card.v_public_comparison_block;
DROP TABLE IF EXISTS nhi_rule_history_diff_card.comparison_block;
DROP TABLE IF EXISTS nhi_rule_history_diff_card.comparison;
DROP FUNCTION IF EXISTS nhi_rule_history_diff_card.check_comparison_complete();
DROP FUNCTION IF EXISTS nhi_rule_history_diff_card.reject_write_once_change();

DROP SCHEMA IF EXISTS nhi_rule_history_diff_card;

COMMIT;
