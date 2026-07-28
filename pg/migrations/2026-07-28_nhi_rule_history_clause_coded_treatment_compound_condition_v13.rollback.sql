-- Exact rollback for coded treatment tags and compound conditions v13.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended(
    'nhi-rule-history-clause-coded-treatment-compound-condition-v13',
    0
  )
);

CREATE TEMP TABLE rollback_v13_runs ON COMMIT DROP AS
SELECT run_id
FROM nhi_rule_history_clause.reader_enrichment_run
WHERE generator_version = 'chapter-00-reader-enrichment/v13';

DELETE FROM nhi_rule_history_clause.agent_history_summary
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_condition_expression
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_condition_marker
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_private
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_code
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_icd11_lookup
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_nhi_treatment
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag_atc
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.clause_semantic_tag
WHERE enrichment_run_id IN (SELECT run_id FROM rollback_v13_runs);
DELETE FROM nhi_rule_history_clause.reader_enrichment_run
WHERE run_id IN (SELECT run_id FROM rollback_v13_runs);

DROP TABLE nhi_rule_history_clause.clause_condition_expression;
DROP TABLE nhi_rule_history_clause.clause_semantic_tag_nhi_treatment;

ALTER TABLE nhi_rule_history_clause.clause_semantic_tag
  DROP CONSTRAINT clause_semantic_tag_tag_type_check,
  DROP CONSTRAINT clause_semantic_tag_entity_type_check,
  DROP CONSTRAINT clause_semantic_tag_resolution_status_check;

ALTER TABLE nhi_rule_history_clause.clause_semantic_tag
  ADD CONSTRAINT clause_semantic_tag_tag_type_check CHECK (
    tag_type IN ('drug', 'disease')
  ),
  ADD CONSTRAINT clause_semantic_tag_entity_type_check CHECK (
    entity_type IN (
      'ingredient', 'brand', 'drug_class', 'abbreviation',
      'disease', 'clinical_condition'
    )
  ),
  ADD CONSTRAINT clause_semantic_tag_resolution_status_check CHECK (
    resolution_status IN (
      'resolved_atc', 'multiple_atc',
      'official_lookup_only', 'terminology_pending'
    )
  );

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  DROP CONSTRAINT reader_enrichment_run_check;

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  DROP COLUMN tag_nhi_treatment_count,
  DROP COLUMN condition_expression_count;

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  ADD CONSTRAINT reader_enrichment_run_check CHECK (
    (
      state = 'loading'
      AND output_sha256 IS NULL
      AND semantic_tag_count IS NULL
      AND tag_atc_count IS NULL
      AND tag_icd11_lookup_count IS NULL
      AND condition_marker_count IS NULL
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
      AND condition_marker_count IS NOT NULL
      AND summary_count IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  );

COMMIT;
