-- 2026-07-28 — coded treatment tags and structured compound conditions

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended(
    'nhi-rule-history-clause-coded-treatment-compound-condition-v13',
    0
  )
);

ALTER TABLE nhi_rule_history_clause.reader_enrichment_run
  ADD COLUMN tag_nhi_treatment_count integer CHECK (
    tag_nhi_treatment_count IS NULL OR tag_nhi_treatment_count >= 0
  ),
  ADD COLUMN condition_expression_count integer CHECK (
    condition_expression_count IS NULL OR condition_expression_count >= 0
  );

UPDATE nhi_rule_history_clause.reader_enrichment_run
SET tag_nhi_treatment_count = 0,
    condition_expression_count = 0
WHERE state = 'sealed';

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
      AND tag_nhi_treatment_count IS NOT NULL
      AND condition_marker_count IS NOT NULL
      AND condition_expression_count IS NOT NULL
      AND summary_count IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  );

ALTER TABLE nhi_rule_history_clause.clause_semantic_tag
  DROP CONSTRAINT clause_semantic_tag_tag_type_check,
  DROP CONSTRAINT clause_semantic_tag_entity_type_check,
  DROP CONSTRAINT clause_semantic_tag_resolution_status_check;

ALTER TABLE nhi_rule_history_clause.clause_semantic_tag
  ADD CONSTRAINT clause_semantic_tag_tag_type_check CHECK (
    tag_type IN ('drug', 'disease', 'treatment')
  ),
  ADD CONSTRAINT clause_semantic_tag_entity_type_check CHECK (
    entity_type IN (
      'ingredient', 'brand', 'drug_class', 'abbreviation',
      'disease', 'clinical_condition', 'treatment_modality'
    )
  ),
  ADD CONSTRAINT clause_semantic_tag_resolution_status_check CHECK (
    resolution_status IN (
      'resolved_atc', 'multiple_atc',
      'official_lookup_only', 'terminology_pending',
      'resolved_nhi_treatment', 'multiple_nhi_treatment'
    )
  );

CREATE TABLE
  nhi_rule_history_clause.clause_semantic_tag_nhi_treatment (
    enrichment_run_id uuid NOT NULL,
    tag_id text NOT NULL,
    treatment_code text NOT NULL CHECK (
      treatment_code ~ '^[0-9A-Z]{5,8}$'
    ),
    is_primary boolean NOT NULL,
    mapping_role text NOT NULL CHECK (
      mapping_role IN ('core_service', 'related_service')
    ),
    mapping_basis text NOT NULL CHECK (
      mapping_basis = 'nhi_payment_standard_exact_name_and_clause_context'
    ),
    name_zh text NOT NULL CHECK (name_zh <> ''),
    name_en text,
    effective_date date NOT NULL,
    source_resource_modified date NOT NULL,
    source_dataset_identifier text NOT NULL CHECK (
      source_dataset_identifier = 'A21030000I-D20020'
    ),
    source_url text NOT NULL CHECK (
      source_url ~ '^https://info[.]nhi[.]gov[.]tw/'
    ),
    review_status text NOT NULL CHECK (
      review_status = 'agent_verified'
    ),
    PRIMARY KEY (enrichment_run_id, tag_id, treatment_code),
    FOREIGN KEY (enrichment_run_id, tag_id)
      REFERENCES nhi_rule_history_clause.clause_semantic_tag (
        enrichment_run_id, tag_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE
  nhi_rule_history_clause.clause_condition_expression (
    enrichment_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
      ON DELETE RESTRICT,
    expression_id text NOT NULL,
    clause_id text NOT NULL
      REFERENCES nhi_rule_history_clause.clause (clause_id)
      ON DELETE RESTRICT,
    expression_text text NOT NULL CHECK (expression_text <> ''),
    normalized_expression text NOT NULL CHECK (
      normalized_expression <> ''
    ),
    expression_type text NOT NULL CHECK (
      expression_type IN (
        'numeric_upper_bound',
        'duration_upper_bound',
        'recurring_action'
      )
    ),
    comparator text NOT NULL CHECK (
      comparator IN (
        'less_than_or_equal',
        'duration_cap',
        'recurs_every'
      )
    ),
    operator_text text NOT NULL CHECK (operator_text <> ''),
    value_numeric numeric NOT NULL CHECK (value_numeric >= 0),
    unit_code text NOT NULL CHECK (
      unit_code IN (
        'hour', 'day', 'week', 'month', 'year',
        'unit_u', 'microgram', 'item'
      )
    ),
    value_text text NOT NULL CHECK (value_text <> ''),
    action_text text,
    action_count numeric CHECK (
      action_count IS NULL OR action_count >= 0
    ),
    severity_level text NOT NULL CHECK (
      severity_level = 'critical'
    ),
    parser_pattern_id text NOT NULL CHECK (parser_pattern_id <> ''),
    match_mode text NOT NULL CHECK (
      match_mode = 'exact_longest_first'
    ),
    provenance jsonb NOT NULL CHECK (
      jsonb_typeof(provenance) = 'object'
      AND provenance <> '{}'::jsonb
    ),
    PRIMARY KEY (enrichment_run_id, expression_id),
    UNIQUE (
      enrichment_run_id, clause_id, normalized_expression
    )
  );

CREATE INDEX clause_condition_expression_lookup_idx
  ON nhi_rule_history_clause.clause_condition_expression (
    enrichment_run_id, clause_id, normalized_expression
  );

COMMIT;
