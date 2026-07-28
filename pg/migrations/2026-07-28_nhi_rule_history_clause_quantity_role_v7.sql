BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-reader-enrichment-v7', 0)
);

ALTER TABLE nhi_rule_history_clause.clause_condition_marker
  DROP CONSTRAINT clause_condition_marker_semantic_role_check;

ALTER TABLE nhi_rule_history_clause.clause_condition_marker
  ADD CONSTRAINT clause_condition_marker_semantic_role_check CHECK (
    semantic_role IN (
      'restriction', 'maximum', 'prohibition', 'requirement',
      'conjunction', 'logical', 'duration', 'quantity', 'exception',
      'prior_authorization'
    )
  );

COMMIT;
