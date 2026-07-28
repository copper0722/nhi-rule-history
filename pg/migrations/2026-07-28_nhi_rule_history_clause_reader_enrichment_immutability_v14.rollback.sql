-- Exact rollback for reader-enrichment immutability v14.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended(
    'nhi-rule-history-clause-reader-enrichment-immutability-v14',
    0
  )
);

DROP TRIGGER reader_enrichment_run_update_guard
  ON nhi_rule_history_clause.reader_enrichment_run;
DROP TRIGGER reader_enrichment_run_delete_guard
  ON nhi_rule_history_clause.reader_enrichment_run;
DROP TRIGGER reader_enrichment_run_truncate_guard
  ON nhi_rule_history_clause.reader_enrichment_run;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'clause_semantic_tag',
    'clause_semantic_tag_atc',
    'clause_semantic_tag_icd11_lookup',
    'clause_semantic_tag_icd11_code',
    'clause_semantic_tag_icd11_private',
    'clause_semantic_tag_nhi_treatment',
    'clause_condition_marker',
    'clause_condition_expression',
    'agent_history_summary'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER reader_enrichment_child_dml_guard '
      'ON nhi_rule_history_clause.%I',
      table_name
    );
    EXECUTE format(
      'DROP TRIGGER reader_enrichment_child_truncate_guard '
      'ON nhi_rule_history_clause.%I',
      table_name
    );
  END LOOP;
END;
$$;

DROP FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_child_dml();
DROP FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_truncate();
DROP FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_run_delete();
DROP FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_run_update();

COMMIT;
