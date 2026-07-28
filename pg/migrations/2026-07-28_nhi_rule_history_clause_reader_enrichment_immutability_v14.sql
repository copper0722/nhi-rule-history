-- 2026-07-28 — database-owned immutability for sealed reader enrichment

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended(
    'nhi-rule-history-clause-reader-enrichment-immutability-v14',
    0
  )
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION
      'sealed reader enrichment runs are immutable';
  END IF;

  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION
      'reader enrichment run permits only loading to sealed';
  END IF;

  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.clause_import_run_id IS DISTINCT FROM OLD.clause_import_run_id
     OR NEW.diff_run_id IS DISTINCT FROM OLD.diff_run_id
     OR NEW.generator_version IS DISTINCT FROM OLD.generator_version
     OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION
      'reader enrichment run identity and input fields are immutable';
  END IF;

  IF NEW.semantic_tag_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.tag_atc_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag_atc
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.tag_icd11_lookup_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag_icd11_lookup
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.tag_nhi_treatment_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag_nhi_treatment
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.condition_marker_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_condition_marker
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.condition_expression_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_condition_expression
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.summary_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.agent_history_summary
       WHERE enrichment_run_id = OLD.run_id
     ) THEN
    RAISE EXCEPTION
      'reader enrichment seal counts do not match child rows';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_run_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'reader enrichment runs cannot be deleted';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'reader enrichment evidence cannot be truncated: %',
    TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_child_dml()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION
      'reader enrichment child rows are append-only: %.%',
      TG_TABLE_SCHEMA,
      TG_TABLE_NAME;
  END IF;

  SELECT state
  INTO parent_state
  FROM nhi_rule_history_clause.reader_enrichment_run
  WHERE run_id = NEW.enrichment_run_id
  FOR SHARE;

  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION
      'reader enrichment child insert requires a loading parent run';
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER reader_enrichment_run_update_guard
BEFORE UPDATE ON nhi_rule_history_clause.reader_enrichment_run
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_run_update();

CREATE TRIGGER reader_enrichment_run_delete_guard
BEFORE DELETE ON nhi_rule_history_clause.reader_enrichment_run
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_run_delete();

CREATE TRIGGER reader_enrichment_run_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_clause.reader_enrichment_run
FOR EACH STATEMENT
EXECUTE FUNCTION
  nhi_rule_history_clause.reject_reader_enrichment_truncate();

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
      'CREATE TRIGGER reader_enrichment_child_dml_guard '
      'BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_clause.%I FOR EACH ROW '
      'EXECUTE FUNCTION '
      'nhi_rule_history_clause.guard_reader_enrichment_child_dml()',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER reader_enrichment_child_truncate_guard '
      'BEFORE TRUNCATE ON nhi_rule_history_clause.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_clause.reject_reader_enrichment_truncate()',
      table_name
    );
  END LOOP;
END;
$$;

COMMIT;
