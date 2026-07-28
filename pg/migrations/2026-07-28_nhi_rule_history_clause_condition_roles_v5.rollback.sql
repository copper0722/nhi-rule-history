-- Exact rollback for the reader-enrichment v5 semantic roles.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-reader-enrichment-v5', 0)
);

CREATE TEMP TABLE rollback_v5_runs ON COMMIT DROP AS
SELECT run_id
FROM nhi_rule_history_clause.reader_enrichment_run
WHERE generator_version = 'chapter-00-reader-enrichment/v5';

DELETE FROM nhi_rule_history_clause.agent_history_summary
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_condition_marker
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_private
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_code
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_lookup
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_atc
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v5_runs);
DELETE FROM nhi_rule_history_clause.reader_enrichment_run
WHERE run_id IN (SELECT run_id FROM rollback_v5_runs);

ALTER TABLE nhi_rule_history_clause.clause_condition_marker
  DROP CONSTRAINT clause_condition_marker_semantic_role_check;

ALTER TABLE nhi_rule_history_clause.clause_condition_marker
  ADD CONSTRAINT clause_condition_marker_semantic_role_check CHECK (
    semantic_role IN (
      'restriction', 'maximum', 'prohibition', 'requirement',
      'conjunction', 'exception', 'prior_authorization'
    )
  );

COMMIT;
