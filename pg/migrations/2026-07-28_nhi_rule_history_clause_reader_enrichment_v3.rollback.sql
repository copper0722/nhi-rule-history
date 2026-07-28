-- Exact rollback for clause reader enrichment v3.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-reader-enrichment-v3', 0)
);

DROP TABLE IF EXISTS
  nhi_rule_history_clause.agent_history_summary;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_condition_marker;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_private;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_lookup;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_semantic_tag_atc;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_semantic_tag;
DROP TABLE IF EXISTS
  nhi_rule_history_clause.reader_enrichment_run;

COMMIT;
