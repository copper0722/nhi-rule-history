-- 2026-07-28 — complete sealed count receipt for all enrichment children

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended(
    'nhi-rule-history-clause-reader-enrichment-full-count-receipt-v15',
    0
  )
);

DROP TRIGGER reader_enrichment_run_update_guard
  ON nhi_rule_history_clause.reader_enrichment_run;

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  ADD COLUMN tag_icd11_code_count integer CHECK (
    tag_icd11_code_count IS NULL OR tag_icd11_code_count >= 0
  ),
  ADD COLUMN tag_icd11_private_count integer CHECK (
    tag_icd11_private_count IS NULL OR tag_icd11_private_count >= 0
  );

UPDATE nhi_rule_history_clause.reader_enrichment_run AS run
SET tag_icd11_code_count = (
      SELECT count(*)
      FROM nhi_rule_history_clause.clause_semantic_tag_icd11_code AS child
      WHERE child.enrichment_run_id = run.run_id
    ),
    tag_icd11_private_count = (
      SELECT count(*)
      FROM nhi_rule_history_clause.clause_semantic_tag_icd11_private AS child
      WHERE child.enrichment_run_id = run.run_id
    )
WHERE run.state = 'sealed';

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  DROP CONSTRAINT reader_enrichment_run_check;

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  ADD CONSTRAINT reader_enrichment_run_check CHECK (
    (
      state = 'loading'
      AND output_sha256 IS NULL
      AND semantic_tag_count IS NULL
      AND tag_atc_count IS NULL
      AND tag_icd11_lookup_count IS NULL
      AND tag_icd11_code_count IS NULL
      AND tag_icd11_private_count IS NULL
      AND tag_nhi_treatment_count IS NULL
      AND condition_marker_count IS NULL
      AND condition_expression_count IS NULL
      AND summary_count IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND output_sha256 IS NOT NULL
      AND semantic_tag_count IS NOT NULL
      AND tag_atc_count IS NOT NULL
      AND tag_icd11_lookup_count IS NOT NULL
      AND tag_icd11_code_count IS NOT NULL
      AND tag_icd11_private_count IS NOT NULL
      AND tag_nhi_treatment_count IS NOT NULL
      AND condition_marker_count IS NOT NULL
      AND condition_expression_count IS NOT NULL
      AND summary_count IS NOT NULL
      AND sealed_at IS NOT NULL
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
     OR NEW.tag_icd11_code_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag_icd11_code
       WHERE enrichment_run_id = OLD.run_id
     )
     OR NEW.tag_icd11_private_count <> (
       SELECT count(*)
       FROM nhi_rule_history_clause.clause_semantic_tag_icd11_private
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

CREATE TRIGGER reader_enrichment_run_update_guard
BEFORE UPDATE ON nhi_rule_history_clause.reader_enrichment_run
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_clause.guard_reader_enrichment_run_update();

COMMIT;
