-- Roll back only the additive semantic diff-presentation layer.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-diff-policy-v2', 0)
);

DROP VIEW IF EXISTS
  nhi_rule_history_clause.v_current_diff_hunk_presentation;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.diff_hunk_presentation;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.diff_run;

COMMIT;
