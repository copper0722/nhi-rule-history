\set ON_ERROR_STOP on

-- Read-only-by-outcome adversarial verification for the latest sealed reader
-- enrichment run. Every attempted mutation must be rejected by PostgreSQL.
DO $$
DECLARE
  sealed_run_id uuid;
  table_name text;
  rejection_seen boolean;
BEGIN
  SELECT run_id
  INTO sealed_run_id
  FROM nhi_rule_history_clause.reader_enrichment_run
  WHERE state = 'sealed'
  ORDER BY sealed_at DESC
  LIMIT 1;

  IF sealed_run_id IS NULL THEN
    RAISE EXCEPTION 'no sealed reader enrichment run is available';
  END IF;

  rejection_seen := false;
  BEGIN
    UPDATE nhi_rule_history_clause.reader_enrichment_run
    SET output_sha256 = output_sha256
    WHERE run_id = sealed_run_id;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLERRM NOT LIKE '%sealed reader enrichment runs are immutable%' THEN
        RAISE;
      END IF;
      rejection_seen := true;
  END;
  IF NOT rejection_seen THEN
    RAISE EXCEPTION 'sealed parent UPDATE was not rejected';
  END IF;

  rejection_seen := false;
  BEGIN
    DELETE FROM nhi_rule_history_clause.reader_enrichment_run
    WHERE run_id = sealed_run_id;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLERRM NOT LIKE '%reader enrichment runs cannot be deleted%' THEN
        RAISE;
      END IF;
      rejection_seen := true;
  END;
  IF NOT rejection_seen THEN
    RAISE EXCEPTION 'sealed parent DELETE was not rejected';
  END IF;

  rejection_seen := false;
  BEGIN
    TRUNCATE nhi_rule_history_clause.reader_enrichment_run;
  EXCEPTION
    WHEN OTHERS THEN
      rejection_seen := true;
  END;
  IF NOT rejection_seen THEN
    RAISE EXCEPTION 'sealed parent TRUNCATE was not rejected';
  END IF;

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
    rejection_seen := false;
    BEGIN
      EXECUTE format(
        'INSERT INTO nhi_rule_history_clause.%I '
        'SELECT * FROM nhi_rule_history_clause.%I '
        'WHERE enrichment_run_id = $1 LIMIT 1',
        table_name,
        table_name
      )
      USING sealed_run_id;
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLERRM NOT LIKE
          '%reader enrichment child insert requires a loading parent run%'
        THEN
          RAISE;
        END IF;
        rejection_seen := true;
    END;
    IF NOT rejection_seen THEN
      RAISE EXCEPTION
        'sealed child INSERT was not rejected for %',
        table_name;
    END IF;

    rejection_seen := false;
    BEGIN
      EXECUTE format(
        'UPDATE nhi_rule_history_clause.%I '
        'SET enrichment_run_id = enrichment_run_id '
        'WHERE enrichment_run_id = $1',
        table_name
      )
      USING sealed_run_id;
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLERRM NOT LIKE
          '%reader enrichment child rows are append-only%'
        THEN
          RAISE;
        END IF;
        rejection_seen := true;
    END;
    IF NOT rejection_seen THEN
      RAISE EXCEPTION
        'sealed child UPDATE was not rejected for %',
        table_name;
    END IF;

    rejection_seen := false;
    BEGIN
      EXECUTE format(
        'DELETE FROM nhi_rule_history_clause.%I '
        'WHERE enrichment_run_id = $1',
        table_name
      )
      USING sealed_run_id;
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLERRM NOT LIKE
          '%reader enrichment child rows are append-only%'
        THEN
          RAISE;
        END IF;
        rejection_seen := true;
    END;
    IF NOT rejection_seen THEN
      RAISE EXCEPTION
        'sealed child DELETE was not rejected for %',
        table_name;
    END IF;

    rejection_seen := false;
    BEGIN
      EXECUTE format(
        'TRUNCATE nhi_rule_history_clause.%I',
        table_name
      );
    EXCEPTION
      WHEN OTHERS THEN
        -- PostgreSQL may reject a referenced parent table at the foreign-key
        -- layer before its explicit immutability trigger can fire. Both paths
        -- fail closed; leaf tables reach the trigger message.
        rejection_seen := true;
    END;
    IF NOT rejection_seen THEN
      RAISE EXCEPTION
        'sealed child TRUNCATE was not rejected for %',
        table_name;
    END IF;
  END LOOP;
END;
$$;

SELECT 'reader_enrichment_immutability_attack_test=passed';
