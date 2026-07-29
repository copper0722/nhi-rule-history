-- DISPOSABLE / NEVER-POPULATED DATABASES ONLY.
--
-- Production recovery is append-only deactivate/reactivate plus code rollback.
-- Once any v25 evidence row exists, do not run this destructive schema file.

BEGIN;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM
      nhi_rule_history_announced.clause_document_normalization_run
  ) OR EXISTS (
    SELECT 1 FROM
      nhi_rule_history_announced.clause_document_diff_run
  ) THEN
    RAISE EXCEPTION
      'v25 contains evidence rows; use append-only deactivation';
  END IF;
END;
$$;

DROP VIEW IF EXISTS
  nhi_rule_history_announced.v_public_clause_document_diff_hunk;
DROP VIEW IF EXISTS
  nhi_rule_history_announced.v_public_clause_document_expression;
DROP VIEW IF EXISTS
  nhi_rule_history_announced.v_active_clause_document_diff_run;
DROP VIEW IF EXISTS
  nhi_rule_history_announced.v_active_clause_document_normalization_run;

DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.set_clause_document_diff_control(
    uuid, text, text, jsonb
  );
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.set_clause_document_normalization_control(
    uuid, text, text, jsonb
  );

DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_diff_control_event;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_normalization_control_event;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_inline_diff_segment;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_diff_hunk;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_node_lineage;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_diff_run;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_normalization_receipt;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_source_span;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_table_cell_content;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_table_cell;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_table_row;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_table;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_node_identity;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_node;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_source_block;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_expression_relation;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_expression;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_normalization_run;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_node_work;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.clause_document_work;

DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.guard_clause_document_diff_seal();
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.guard_clause_document_normalization_seal();
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.guard_clause_document_diff_child();
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.guard_clause_document_normalization_child();
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.guard_clause_document_identity_mutation();

COMMIT;
