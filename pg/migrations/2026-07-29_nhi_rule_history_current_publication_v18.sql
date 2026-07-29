-- 2026-07-29 — immutable current-clause publication and history inventory
--
-- This schema is the read model for websites and APIs.  The sealed structural
-- parse remains source evidence; this projection stores one exact current
-- clause per row plus the owner-approved date-annotation history inventory.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-current-publication-v18', 0)
);

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_publication;

CREATE DOMAIN nhi_rule_history_publication.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE nhi_rule_history_publication.publication_run (
  run_id uuid PRIMARY KEY,
  source_parse_run_id uuid NOT NULL
    REFERENCES tw_drug_history_structural_stage.parse_run (parse_run_id)
    ON DELETE RESTRICT,
  source_acquisition_run_id uuid NOT NULL
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  source_policy_id text NOT NULL
    REFERENCES nhi_rule_history_edition.current_source_authority_policy
      (policy_id)
    ON DELETE RESTRICT,
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  loader_version text NOT NULL,
  extractor_version text NOT NULL,
  version_count_policy text NOT NULL CHECK (
    version_count_policy =
      'distinct_valid_current_text_roc_dates_minimum_one/v1'
  ),
  authority_page_url text NOT NULL CHECK (authority_page_url ~ '^https://'),
  whole_split_parity_status text NOT NULL CHECK (
    whole_split_parity_status IN ('parity_passed', 'parity_failed')
  ),
  input_fingerprint nhi_rule_history_publication.sha256_hex NOT NULL UNIQUE,
  expected_counts jsonb NOT NULL CHECK (
    jsonb_typeof(expected_counts) = 'object'
  ),
  verified_counts jsonb CHECK (
    verified_counts IS NULL OR jsonb_typeof(verified_counts) = 'object'
  ),
  table_fingerprints jsonb CHECK (
    table_fingerprints IS NULL
    OR jsonb_typeof(table_fingerprints) = 'object'
  ),
  output_fingerprint nhi_rule_history_publication.sha256_hex,
  sealed_fingerprint nhi_rule_history_publication.sha256_hex UNIQUE,
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  CHECK (
    (
      state = 'loading'
      AND verified_counts IS NULL
      AND table_fingerprints IS NULL
      AND output_fingerprint IS NULL
      AND sealed_fingerprint IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND verified_counts IS NOT NULL
      AND table_fingerprints IS NOT NULL
      AND output_fingerprint IS NOT NULL
      AND sealed_fingerprint IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  )
);

CREATE TABLE nhi_rule_history_publication.current_clause (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_publication.publication_run (run_id)
    ON DELETE RESTRICT,
  clause_code text NOT NULL CHECK (
    clause_code ~ '^(0[.][1-9][0-9]*|[1-9][0-9]*(?:[.][0-9]+)+)$'
  ),
  chapter_number integer NOT NULL CHECK (chapter_number BETWEEN 0 AND 99),
  source_designation text NOT NULL CHECK (source_designation <> ''),
  code_origin text NOT NULL CHECK (
    code_origin IN ('official_source', 'project_assigned')
  ),
  display_title text NOT NULL CHECK (display_title <> ''),
  source_acquisition_run_id uuid NOT NULL,
  source_resource_id text NOT NULL,
  source_url text NOT NULL CHECK (source_url ~ '^https://'),
  source_label text NOT NULL CHECK (source_label <> ''),
  source_artifact_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  source_span jsonb NOT NULL CHECK (
    jsonb_typeof(source_span) = 'object' AND source_span <> '{}'::jsonb
  ),
  raw_text text NOT NULL CHECK (raw_text <> ''),
  raw_text_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  normalized_text_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  comparison_text text NOT NULL CHECK (comparison_text <> ''),
  comparison_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  valid_distinct_roc_date_count integer NOT NULL CHECK (
    valid_distinct_roc_date_count >= 0
  ),
  expected_version_count integer NOT NULL CHECK (
    expected_version_count = greatest(1, valid_distinct_roc_date_count)
  ),
  reconstructed_version_count integer NOT NULL CHECK (
    reconstructed_version_count >= 1
  ),
  missing_version_count integer NOT NULL CHECK (
    missing_version_count =
      greatest(0, expected_version_count - reconstructed_version_count)
  ),
  annotation_count_underflows_reconstructed boolean NOT NULL CHECK (
    annotation_count_underflows_reconstructed =
      (expected_version_count < reconstructed_version_count)
  ),
  inventory_status text NOT NULL CHECK (
    inventory_status IN (
      'history_full_text_missing',
      'complete_under_annotation_policy',
      'annotation_count_underflows_reconstructed_evidence'
    )
  ),
  source_row_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, clause_code),
  FOREIGN KEY (source_acquisition_run_id, source_resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT,
  CHECK (
    raw_text_sha256 =
      encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  ),
  CHECK (
    normalized_text_sha256 =
      encode(sha256(convert_to(normalized_text, 'UTF8')), 'hex')
  ),
  CHECK (
    comparison_sha256 =
      encode(sha256(convert_to(comparison_text, 'UTF8')), 'hex')
  ),
  CHECK (
    inventory_status = CASE
      WHEN annotation_count_underflows_reconstructed
        THEN 'annotation_count_underflows_reconstructed_evidence'
      WHEN missing_version_count > 0
        THEN 'history_full_text_missing'
      ELSE 'complete_under_annotation_policy'
    END
  )
);

CREATE INDEX current_clause_search_idx
  ON nhi_rule_history_publication.current_clause (
    run_id, chapter_number, clause_code
  );

CREATE INDEX current_clause_gap_idx
  ON nhi_rule_history_publication.current_clause (
    run_id, missing_version_count DESC, clause_code
  );

CREATE TABLE nhi_rule_history_publication.current_clause_block (
  run_id uuid NOT NULL,
  clause_code text NOT NULL,
  block_order integer NOT NULL CHECK (block_order >= 0),
  source_block_id text NOT NULL,
  block_kind text NOT NULL CHECK (block_kind <> ''),
  container text NOT NULL CHECK (container <> ''),
  raw_text text NOT NULL,
  raw_text_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  source_row_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, clause_code, block_order),
  FOREIGN KEY (run_id, clause_code)
    REFERENCES nhi_rule_history_publication.current_clause
      (run_id, clause_code)
    ON DELETE RESTRICT,
  CHECK (
    raw_text_sha256 =
      encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE nhi_rule_history_publication.current_clause_date (
  run_id uuid NOT NULL,
  clause_code text NOT NULL,
  date_value date NOT NULL,
  raw_expressions jsonb NOT NULL CHECK (
    jsonb_typeof(raw_expressions) = 'array'
    AND jsonb_array_length(raw_expressions) >= 1
  ),
  occurrence_count integer NOT NULL CHECK (occurrence_count >= 1),
  source_row_sha256 nhi_rule_history_publication.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, clause_code, date_value),
  FOREIGN KEY (run_id, clause_code)
    REFERENCES nhi_rule_history_publication.current_clause
      (run_id, clause_code)
    ON DELETE RESTRICT
);

CREATE TABLE nhi_rule_history_publication.publication_activation (
  activation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_publication.publication_run (run_id)
    ON DELETE RESTRICT,
  activated_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE INDEX publication_activation_run_idx
  ON nhi_rule_history_publication.publication_activation (
    run_id, activation_id DESC
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_publication.guard_run_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.state <> 'loading' THEN
    RAISE EXCEPTION 'publication runs must be inserted in loading state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_publication.guard_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  actual_counts jsonb;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed publication runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'publication run permits only loading to sealed';
  END IF;
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.source_parse_run_id IS DISTINCT FROM OLD.source_parse_run_id
     OR NEW.source_acquisition_run_id IS DISTINCT FROM
       OLD.source_acquisition_run_id
     OR NEW.source_policy_id IS DISTINCT FROM OLD.source_policy_id
     OR NEW.loader_version IS DISTINCT FROM OLD.loader_version
     OR NEW.extractor_version IS DISTINCT FROM OLD.extractor_version
     OR NEW.version_count_policy IS DISTINCT FROM OLD.version_count_policy
     OR NEW.authority_page_url IS DISTINCT FROM OLD.authority_page_url
     OR NEW.whole_split_parity_status IS DISTINCT FROM
       OLD.whole_split_parity_status
     OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
     OR NEW.expected_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'publication run identity and input fields are immutable';
  END IF;

  SELECT jsonb_build_object(
    'current_clause',
      (SELECT count(*) FROM nhi_rule_history_publication.current_clause
       WHERE run_id = OLD.run_id),
    'current_clause_block',
      (SELECT count(*) FROM
         nhi_rule_history_publication.current_clause_block
       WHERE run_id = OLD.run_id),
    'current_clause_date',
      (SELECT count(*) FROM
         nhi_rule_history_publication.current_clause_date
       WHERE run_id = OLD.run_id)
  )
  INTO actual_counts;

  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'publication seal counts do not match child rows';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_publication.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'publication evidence is append-only: %.%',
    TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_publication.guard_child_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'publication child rows are append-only';
  END IF;
  SELECT state INTO parent_state
  FROM nhi_rule_history_publication.publication_run
  WHERE run_id = NEW.run_id
  FOR SHARE;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION
      'publication child insert requires a loading parent run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_publication.guard_activation_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
BEGIN
  SELECT state INTO parent_state
  FROM nhi_rule_history_publication.publication_run
  WHERE run_id = NEW.run_id
  FOR SHARE;
  IF parent_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'only a sealed publication run can be activated';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER publication_run_update_guard
BEFORE UPDATE ON nhi_rule_history_publication.publication_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_publication.guard_run_update();

CREATE TRIGGER publication_run_insert_guard
BEFORE INSERT ON nhi_rule_history_publication.publication_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_publication.guard_run_insert();

CREATE TRIGGER publication_run_delete_guard
BEFORE DELETE ON nhi_rule_history_publication.publication_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_publication.reject_mutation();

CREATE TRIGGER publication_run_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_publication.publication_run
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_publication.reject_mutation();

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'current_clause',
    'current_clause_block',
    'current_clause_date'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_child_dml_guard '
      'BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_publication.%I FOR EACH ROW '
      'EXECUTE FUNCTION '
      'nhi_rule_history_publication.guard_child_insert()',
      table_name,
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_child_truncate_guard '
      'BEFORE TRUNCATE ON nhi_rule_history_publication.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_publication.reject_mutation()',
      table_name,
      table_name
    );
  END LOOP;
END;
$$;

CREATE TRIGGER publication_activation_insert_guard
BEFORE INSERT ON nhi_rule_history_publication.publication_activation
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_publication.guard_activation_insert();

CREATE TRIGGER publication_activation_update_delete_guard
BEFORE UPDATE OR DELETE
ON nhi_rule_history_publication.publication_activation
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_publication.reject_mutation();

CREATE TRIGGER publication_activation_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_publication.publication_activation
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_publication.reject_mutation();

CREATE OR REPLACE VIEW
  nhi_rule_history_publication.v_active_publication_run AS
SELECT run.*
FROM nhi_rule_history_publication.publication_activation activation
JOIN nhi_rule_history_publication.publication_run run
  ON run.run_id = activation.run_id
ORDER BY activation.activation_id DESC
LIMIT 1;

CREATE OR REPLACE VIEW
  nhi_rule_history_publication.v_current_clause AS
SELECT clause.*
FROM nhi_rule_history_publication.current_clause clause
JOIN nhi_rule_history_publication.v_active_publication_run run
  ON run.run_id = clause.run_id;

CREATE OR REPLACE VIEW
  nhi_rule_history_publication.v_current_clause_block AS
SELECT block.*
FROM nhi_rule_history_publication.current_clause_block block
JOIN nhi_rule_history_publication.v_active_publication_run run
  ON run.run_id = block.run_id;

CREATE OR REPLACE VIEW
  nhi_rule_history_publication.v_current_clause_date AS
SELECT clause_date.*
FROM nhi_rule_history_publication.current_clause_date clause_date
JOIN nhi_rule_history_publication.v_active_publication_run run
  ON run.run_id = clause_date.run_id;

CREATE OR REPLACE VIEW
  nhi_rule_history_publication.v_history_inventory_summary AS
SELECT
  count(*)::integer AS current_clause_count,
  sum(expected_version_count)::bigint AS expected_version_count,
  sum(reconstructed_version_count)::bigint AS reconstructed_version_count,
  sum(missing_version_count)::bigint AS missing_version_count,
  count(*) FILTER (
    WHERE missing_version_count > 0
  )::integer AS clauses_with_missing_versions,
  count(*) FILTER (
    WHERE missing_version_count = 0
  )::integer AS clauses_without_missing_versions,
  count(*) FILTER (
    WHERE annotation_count_underflows_reconstructed
  )::integer AS annotation_underflow_clause_count
FROM nhi_rule_history_publication.v_current_clause;

COMMENT ON VIEW
  nhi_rule_history_publication.v_history_inventory_summary IS
  'Owner-policy inventory: expected versions are the distinct valid ROC dates '
  'present in the current clause, with a minimum of one current version. '
  'Missing means full-text states not yet reconstructed under that policy.';

COMMIT;
