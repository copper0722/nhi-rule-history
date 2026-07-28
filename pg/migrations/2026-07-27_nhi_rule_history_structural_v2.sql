-- 2026-07-27 — immutable source-local structural evidence stage (v2)
--
-- Exactly four relations are created.  They preserve parser observations and
-- candidates only; they do not model legal dates, stable identity, currentness,
-- legal events, predecessor/successor relationships, lineage, or diffs.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_structural_stage-global', 0)
);

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Isolated immutable source-local structural parser evidence for NHI rule-history v2; not legal history. managed=tw_drug_history_structural_stage/v2';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'tw_drug_history_structural_stage'
  ) THEN
    CREATE SCHEMA tw_drug_history_structural_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA tw_drug_history_structural_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'tw_drug_history_structural_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'tw_drug_history_structural_stage exists without managed v2 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

CREATE DOMAIN tw_drug_history_structural_stage.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE tw_drug_history_structural_stage.parse_run (
  parse_run_id uuid PRIMARY KEY,
  acquisition_run_id uuid NOT NULL
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  state text NOT NULL DEFAULT 'loading',
  loader_version text NOT NULL,
  contract_version text NOT NULL,
  migration_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  code_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  raw_manifest_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  structural_manifest_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  parser_adapter_version text NOT NULL,
  legacy_parser_version text NOT NULL,
  parser_bundle_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  input_fingerprint tw_drug_history_structural_stage.sha256_hex NOT NULL,
  output_fingerprint tw_drug_history_structural_stage.sha256_hex,
  sealed_fingerprint tw_drug_history_structural_stage.sha256_hex,
  fidelity_class text NOT NULL,
  expected_counts jsonb NOT NULL,
  verified_counts jsonb,
  table_fingerprints jsonb,
  input_files jsonb NOT NULL,
  parser_started_at timestamptz NOT NULL,
  parser_completed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  sealed_at timestamptz,
  CONSTRAINT parse_run_state_chk CHECK (state IN ('loading', 'sealed')),
  CONSTRAINT parse_run_time_chk
    CHECK (parser_completed_at >= parser_started_at),
  CONSTRAINT parse_run_json_chk
    CHECK (
      jsonb_typeof(expected_counts) = 'object'
      AND jsonb_typeof(input_files) = 'array'
      AND (verified_counts IS NULL OR jsonb_typeof(verified_counts) = 'object')
      AND (
        table_fingerprints IS NULL
        OR jsonb_typeof(table_fingerprints) = 'object'
      )
    ),
  CONSTRAINT parse_run_terminal_chk
    CHECK (
      (
        state = 'loading'
        AND output_fingerprint IS NULL
        AND sealed_fingerprint IS NULL
        AND verified_counts IS NULL
        AND table_fingerprints IS NULL
        AND sealed_at IS NULL
      )
      OR (
        state = 'sealed'
        AND output_fingerprint IS NOT NULL
        AND sealed_fingerprint IS NOT NULL
        AND verified_counts IS NOT NULL
        AND table_fingerprints IS NOT NULL
        AND sealed_at IS NOT NULL
      )
    )
);

CREATE UNIQUE INDEX parse_run_sealed_fingerprint_uidx
  ON tw_drug_history_structural_stage.parse_run (sealed_fingerprint)
  WHERE sealed_fingerprint IS NOT NULL;

CREATE INDEX parse_run_input_fingerprint_idx
  ON tw_drug_history_structural_stage.parse_run (input_fingerprint);

CREATE TABLE tw_drug_history_structural_stage.structural_block (
  parse_run_id uuid NOT NULL,
  block_id tw_drug_history_structural_stage.sha256_hex NOT NULL,
  acquisition_run_id uuid NOT NULL,
  artifact_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  relative_path text NOT NULL,
  locator jsonb NOT NULL,
  locator_key text NOT NULL,
  block_kind text NOT NULL,
  element_name text NOT NULL,
  style_name text,
  container text NOT NULL,
  in_table boolean NOT NULL,
  in_index_context boolean NOT NULL,
  xml_element_index bigint NOT NULL CHECK (xml_element_index >= 0),
  raw_text text NOT NULL,
  normalized_search_text text NOT NULL,
  raw_text_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  raw_text_byte_length bigint NOT NULL CHECK (raw_text_byte_length >= 0),
  raw_text_char_length bigint NOT NULL CHECK (raw_text_char_length >= 0),
  parser_version text NOT NULL,
  source_resource_ids jsonb NOT NULL,
  source_labels jsonb NOT NULL,
  statement text NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  PRIMARY KEY (parse_run_id, block_id),
  FOREIGN KEY (parse_run_id)
    REFERENCES tw_drug_history_structural_stage.parse_run (parse_run_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (acquisition_run_id, artifact_sha256)
    REFERENCES tw_drug_history_acq_stage.raw_artifact
      (run_id, artifact_sha256)
    ON DELETE RESTRICT,
  CONSTRAINT structural_block_json_chk
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND jsonb_typeof(source_resource_ids) = 'array'
      AND jsonb_typeof(source_labels) = 'array'
      AND jsonb_typeof(row_payload) = 'object'
    ),
  CONSTRAINT structural_block_relative_path_chk
    CHECK (
      relative_path <> ''
      AND relative_path !~ '^[/\\]'
      AND relative_path !~ '(^|[\\/])\.\.([\\/]|$)'
    ),
  CONSTRAINT structural_block_lengths_chk
    CHECK (
      octet_length(raw_text) = raw_text_byte_length
      AND char_length(raw_text) = raw_text_char_length
    ),
  CONSTRAINT structural_block_nonclaim_chk
    CHECK (
      statement =
      'Source-local structural observation only; not stable rule identity, legal effective date, legal event, current version, predecessor/successor, or diff.'
    ),
  UNIQUE (parse_run_id, block_id, acquisition_run_id, artifact_sha256)
);

CREATE TABLE tw_drug_history_structural_stage.occurrence_candidate (
  parse_run_id uuid NOT NULL,
  occurrence_id tw_drug_history_structural_stage.sha256_hex NOT NULL,
  acquisition_run_id uuid NOT NULL,
  artifact_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  block_id tw_drug_history_structural_stage.sha256_hex NOT NULL,
  relative_path text NOT NULL,
  designation_text text NOT NULL,
  locator jsonb NOT NULL,
  locator_key text NOT NULL,
  raw_text text NOT NULL,
  normalized_search_text text NOT NULL,
  raw_text_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  raw_text_byte_length bigint NOT NULL CHECK (raw_text_byte_length >= 0),
  raw_text_char_length bigint NOT NULL CHECK (raw_text_char_length >= 0),
  parser_version text NOT NULL,
  ambiguity_flags jsonb NOT NULL,
  container text NOT NULL,
  match_start_in_raw bigint NOT NULL CHECK (match_start_in_raw >= 0),
  match_end_in_raw bigint NOT NULL,
  in_index_context boolean NOT NULL,
  source_resource_ids jsonb NOT NULL,
  source_labels jsonb NOT NULL,
  statement text NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  PRIMARY KEY (parse_run_id, occurrence_id),
  FOREIGN KEY (
    parse_run_id, block_id, acquisition_run_id, artifact_sha256
  )
    REFERENCES tw_drug_history_structural_stage.structural_block
      (parse_run_id, block_id, acquisition_run_id, artifact_sha256)
    ON DELETE RESTRICT,
  CONSTRAINT occurrence_candidate_offsets_chk
    CHECK (
      match_end_in_raw > match_start_in_raw
      AND match_end_in_raw <= raw_text_char_length
    ),
  CONSTRAINT occurrence_candidate_json_chk
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND jsonb_typeof(ambiguity_flags) = 'array'
      AND jsonb_typeof(source_resource_ids) = 'array'
      AND jsonb_typeof(source_labels) = 'array'
      AND jsonb_typeof(row_payload) = 'object'
    ),
  CONSTRAINT occurrence_candidate_lengths_chk
    CHECK (
      octet_length(raw_text) = raw_text_byte_length
      AND char_length(raw_text) = raw_text_char_length
    ),
  CONSTRAINT occurrence_candidate_nonclaim_chk
    CHECK (
      statement =
      'Source-local structural observation only; not stable rule identity, legal effective date, legal event, current version, predecessor/successor, or diff.'
    )
);

CREATE TABLE tw_drug_history_structural_stage.parse_issue (
  parse_run_id uuid NOT NULL,
  issue_id tw_drug_history_structural_stage.sha256_hex NOT NULL,
  acquisition_run_id uuid NOT NULL,
  artifact_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  issue_code text NOT NULL,
  severity text NOT NULL,
  blocking boolean NOT NULL,
  message_parameters jsonb NOT NULL,
  statement text NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_structural_stage.sha256_hex NOT NULL,
  PRIMARY KEY (parse_run_id, issue_id),
  FOREIGN KEY (parse_run_id)
    REFERENCES tw_drug_history_structural_stage.parse_run (parse_run_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (acquisition_run_id, artifact_sha256)
    REFERENCES tw_drug_history_acq_stage.raw_artifact
      (run_id, artifact_sha256)
    ON DELETE RESTRICT,
  CONSTRAINT parse_issue_json_chk
    CHECK (
      jsonb_typeof(message_parameters) = 'object'
      AND jsonb_typeof(row_payload) = 'object'
    ),
  CONSTRAINT parse_issue_nonclaim_chk
    CHECK (
      statement =
      'Source-local structural observation only; not stable rule identity, legal effective date, legal event, current version, predecessor/successor, or diff.'
    )
);

CREATE FUNCTION tw_drug_history_structural_stage.guard_evidence_dml()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    RAISE EXCEPTION 'structural evidence rows are append-only'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT state INTO parent_state
  FROM tw_drug_history_structural_stage.parse_run
  WHERE parse_run_id = NEW.parse_run_id;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION
      'structural evidence may be inserted only while parse run is loading'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION tw_drug_history_structural_stage.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'structural stage tables cannot be truncated'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION tw_drug_history_structural_stage.guard_parse_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed parse runs are immutable'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF OLD.parse_run_id <> NEW.parse_run_id
     OR OLD.acquisition_run_id <> NEW.acquisition_run_id
     OR OLD.loader_version <> NEW.loader_version
     OR OLD.contract_version <> NEW.contract_version
     OR OLD.migration_sha256 <> NEW.migration_sha256
     OR OLD.code_sha256 <> NEW.code_sha256
     OR OLD.raw_manifest_sha256 <> NEW.raw_manifest_sha256
     OR OLD.structural_manifest_sha256 <> NEW.structural_manifest_sha256
     OR OLD.parser_adapter_version <> NEW.parser_adapter_version
     OR OLD.legacy_parser_version <> NEW.legacy_parser_version
     OR OLD.parser_bundle_sha256 <> NEW.parser_bundle_sha256
     OR OLD.input_fingerprint <> NEW.input_fingerprint
     OR OLD.expected_counts <> NEW.expected_counts
     OR OLD.input_files <> NEW.input_files THEN
    RAISE EXCEPTION 'parse run identity fields are immutable'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.state <> 'sealed' THEN
    RAISE EXCEPTION 'loading parse run may transition only to sealed'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION tw_drug_history_structural_stage.reject_parse_run_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'parse runs cannot be deleted by ordinary DML'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE TRIGGER parse_run_update_guard
BEFORE UPDATE ON tw_drug_history_structural_stage.parse_run
FOR EACH ROW
EXECUTE FUNCTION tw_drug_history_structural_stage.guard_parse_run_update();

CREATE TRIGGER parse_run_delete_guard
BEFORE DELETE ON tw_drug_history_structural_stage.parse_run
FOR EACH ROW
EXECUTE FUNCTION tw_drug_history_structural_stage.reject_parse_run_delete();

CREATE TRIGGER parse_run_truncate_guard
BEFORE TRUNCATE ON tw_drug_history_structural_stage.parse_run
FOR EACH STATEMENT
EXECUTE FUNCTION tw_drug_history_structural_stage.reject_truncate();

DO $guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'structural_block', 'occurrence_candidate', 'parse_issue'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON tw_drug_history_structural_stage.%I FOR EACH ROW EXECUTE FUNCTION tw_drug_history_structural_stage.guard_evidence_dml()',
      table_name || '_dml_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON tw_drug_history_structural_stage.%I FOR EACH STATEMENT EXECUTE FUNCTION tw_drug_history_structural_stage.reject_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$guards$;

REVOKE ALL ON SCHEMA tw_drug_history_structural_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA tw_drug_history_structural_stage FROM PUBLIC;
REVOKE ALL ON TYPE tw_drug_history_structural_stage.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA tw_drug_history_structural_stage
  FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_structural_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_structural_stage
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_structural_stage
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

COMMENT ON TABLE tw_drug_history_structural_stage.structural_block IS
  'Lossless source-local parser blocks; no legal-history semantics.';
COMMENT ON TABLE tw_drug_history_structural_stage.occurrence_candidate IS
  'Source-local textual occurrence candidates; never stable cross-source identities.';

COMMIT;
