\set ON_ERROR_STOP on

-- Transactional production-schema probe. It clones the latest sealed child
-- rows into a temporary loading run, seals that run through the live count
-- gate, reports success, and rolls the entire probe back.
BEGIN;

DO $$
DECLARE
  source_run_id uuid;
  probe_run_id uuid := gen_random_uuid();
  table_name text;
  probe_state text;
BEGIN
  SELECT run_id
  INTO source_run_id
  FROM nhi_rule_history_clause.reader_enrichment_run
  WHERE state = 'sealed'
  ORDER BY sealed_at DESC
  LIMIT 1;

  IF source_run_id IS NULL THEN
    RAISE EXCEPTION 'no sealed reader enrichment run is available';
  END IF;

  INSERT INTO nhi_rule_history_clause.reader_enrichment_run (
    run_id,
    clause_import_run_id,
    diff_run_id,
    generator_version,
    input_sha256,
    state,
    started_at
  )
  SELECT
    probe_run_id,
    clause_import_run_id,
    diff_run_id,
    'immutability-live-probe/' || probe_run_id::text,
    input_sha256,
    'loading',
    clock_timestamp()
  FROM nhi_rule_history_clause.reader_enrichment_run
  WHERE run_id = source_run_id;

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
      'INSERT INTO nhi_rule_history_clause.%1$I '
      'SELECT (jsonb_populate_record('
      'NULL::nhi_rule_history_clause.%1$I, '
      'to_jsonb(source_row) || '
      'jsonb_build_object(''enrichment_run_id'', $1)'
      ')).* '
      'FROM nhi_rule_history_clause.%1$I AS source_row '
      'WHERE source_row.enrichment_run_id = $2',
      table_name
    )
    USING probe_run_id, source_run_id;
  END LOOP;

  UPDATE nhi_rule_history_clause.reader_enrichment_run AS probe
  SET state = 'sealed',
      output_sha256 = source.output_sha256,
      semantic_tag_count = source.semantic_tag_count,
      tag_atc_count = source.tag_atc_count,
      tag_icd11_lookup_count = source.tag_icd11_lookup_count,
      tag_icd11_code_count = source.tag_icd11_code_count,
      tag_icd11_private_count = source.tag_icd11_private_count,
      tag_nhi_treatment_count = source.tag_nhi_treatment_count,
      condition_marker_count = source.condition_marker_count,
      condition_expression_count = source.condition_expression_count,
      summary_count = source.summary_count,
      sealed_at = clock_timestamp()
  FROM nhi_rule_history_clause.reader_enrichment_run AS source
  WHERE probe.run_id = probe_run_id
    AND source.run_id = source_run_id;

  SELECT state
  INTO probe_state
  FROM nhi_rule_history_clause.reader_enrichment_run
  WHERE run_id = probe_run_id;

  IF probe_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'fresh reader enrichment probe did not seal';
  END IF;
END;
$$;

SELECT 'reader_enrichment_fresh_loading_to_sealed=passed';

ROLLBACK;
