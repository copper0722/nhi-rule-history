-- 2026-07-26 — isolated NHI rule-history staging schema
--
-- Creates ONLY tw_drug_history_stage. Stores immutable, run-scoped parser
-- evidence from the repaired 14-release ODT occurrence extraction.
--
-- Does NOT:
--   - touch tw_drug, tw_drug_history, or any read views
--   - invent legal effective dates, stable rule identity, event effects,
--     predecessor/successor, lineage, or diffs
--   - grant named application roles (operator migration owns grants)
--
-- Apply on the intended PostgreSQL primary only when an operator explicitly
-- runs this file.
-- This work unit never executes the migration against a live database.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- Global stage lock (must match rollback + loader apply/drop-run; acquire first).
SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_stage-global', 0)
);

-- Create schema only when absent; if present, require exact managed marker.
DO $schema_guard$
DECLARE
  managed_comment text :=
    'Isolated immutable staging for NHI rule-history occurrence rebuild runs; not legal history. managed=tw_drug_history_stage/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'tw_drug_history_stage'
  ) THEN
    CREATE SCHEMA tw_drug_history_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA tw_drug_history_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'tw_drug_history_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'tw_drug_history_stage exists but is not the managed stage schema (refuse bare adoption)'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

-- ---------------------------------------------------------------------------
-- Domain: lowercase 64-hex SHA-256
-- ---------------------------------------------------------------------------

DO $domain$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'tw_drug_history_stage'
      AND t.typname = 'sha256_hex'
  ) THEN
    CREATE DOMAIN tw_drug_history_stage.sha256_hex AS text
      CONSTRAINT sha256_hex_format
      CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END;
$domain$;

COMMENT ON DOMAIN tw_drug_history_stage.sha256_hex IS
  'Lowercase hex-encoded SHA-256 digest (64 chars).';

-- ---------------------------------------------------------------------------
-- 1. rebuild_run
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.rebuild_run (
  run_id                uuid PRIMARY KEY,
  state                 text NOT NULL,
  parser_version        text NOT NULL,
  loader_version        text NOT NULL,
  contract_version      text NOT NULL,
  code_hash             tw_drug_history_stage.sha256_hex NOT NULL,
  input_fingerprint     tw_drug_history_stage.sha256_hex NOT NULL,
  sealed_fingerprint    tw_drug_history_stage.sha256_hex,
  output_fingerprint    tw_drug_history_stage.sha256_hex,
  accepted_manifest_sha256 tw_drug_history_stage.sha256_hex NOT NULL,
  expected_counts       jsonb NOT NULL,
  verified_counts       jsonb,
  expected_release_count integer NOT NULL,
  expected_block_count   bigint NOT NULL,
  expected_occurrence_count bigint NOT NULL,
  expected_empty_table_cell_block_count bigint NOT NULL,
  expected_xml_ph_element_count_total bigint NOT NULL,
  expected_xml_ph_emitted_unique_total bigint NOT NULL,
  expected_xml_ph_unaccounted_total bigint NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  sealed_at             timestamptz,
  failed_at             timestamptz,
  failure_code          text,
  failure_detail        text,
  CONSTRAINT rebuild_run_state_chk
    CHECK (state IN ('loading', 'sealed', 'failed')),
  CONSTRAINT rebuild_run_expected_counts_object_chk
    CHECK (jsonb_typeof(expected_counts) = 'object'),
  CONSTRAINT rebuild_run_verified_counts_object_chk
    CHECK (verified_counts IS NULL OR jsonb_typeof(verified_counts) = 'object'),
  CONSTRAINT rebuild_run_nonneg_counts_chk
    CHECK (
      expected_release_count >= 0
      AND expected_block_count >= 0
      AND expected_occurrence_count >= 0
      AND expected_empty_table_cell_block_count >= 0
      AND expected_xml_ph_element_count_total >= 0
      AND expected_xml_ph_emitted_unique_total >= 0
      AND expected_xml_ph_unaccounted_total >= 0
    ),
  CONSTRAINT rebuild_run_sealed_fingerprint_required_chk
    CHECK (
      (state = 'sealed' AND sealed_fingerprint IS NOT NULL AND sealed_at IS NOT NULL)
      OR (state <> 'sealed')
    ),
  CONSTRAINT rebuild_run_failed_metadata_chk
    CHECK (
      (state = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL)
      OR (state <> 'failed')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS rebuild_run_sealed_fingerprint_uidx
  ON tw_drug_history_stage.rebuild_run (sealed_fingerprint)
  WHERE sealed_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS rebuild_run_state_idx
  ON tw_drug_history_stage.rebuild_run (state);

CREATE INDEX IF NOT EXISTS rebuild_run_input_fingerprint_idx
  ON tw_drug_history_stage.rebuild_run (input_fingerprint);

COMMENT ON TABLE tw_drug_history_stage.rebuild_run IS
  'One immutable staging load attempt; sealed fingerprint is unique when sealed.';

-- ---------------------------------------------------------------------------
-- 2. run_input_file
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.run_input_file (
  run_id            uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  logical_name      text NOT NULL,
  declared_schema   text NOT NULL,
  byte_length       bigint NOT NULL,
  row_count         bigint NOT NULL,
  content_sha256    tw_drug_history_stage.sha256_hex NOT NULL,
  relative_locator  text NOT NULL,
  PRIMARY KEY (run_id, logical_name),
  CONSTRAINT run_input_file_byte_length_chk CHECK (byte_length >= 0),
  CONSTRAINT run_input_file_row_count_chk CHECK (row_count >= 0),
  CONSTRAINT run_input_file_relative_locator_chk
    CHECK (
      relative_locator <> ''
      AND relative_locator !~ '^[/\\\\]'
      AND position('..' IN relative_locator) = 0
    )
);

CREATE INDEX IF NOT EXISTS run_input_file_content_sha_idx
  ON tw_drug_history_stage.run_input_file (content_sha256);

COMMENT ON TABLE tw_drug_history_stage.run_input_file IS
  'Immutable loader inputs keyed by run and logical name.';

-- ---------------------------------------------------------------------------
-- 3. source_release
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.source_release (
  run_id              uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  release_id          tw_drug_history_stage.sha256_hex NOT NULL,
  source_order_index  integer NOT NULL,
  relative_path       text NOT NULL,
  basename            text NOT NULL,
  content_sha256      tw_drug_history_stage.sha256_hex NOT NULL,
  byte_length         bigint NOT NULL,
  filename_label_raw  text NOT NULL,
  filename_id_prefix  text,
  filename_date_fragments_raw jsonb NOT NULL,
  analysis_chronology jsonb NOT NULL,
  parser_version      text NOT NULL,
  block_count         bigint NOT NULL,
  occurrence_count    bigint NOT NULL,
  table_count         bigint NOT NULL,
  row_count_xml       bigint NOT NULL,
  cell_count_xml      bigint NOT NULL,
  row_count_logical   bigint NOT NULL,
  cell_count_logical  bigint NOT NULL,
  empty_cell_count    bigint NOT NULL,
  nested_table_count  bigint NOT NULL,
  empty_table_cell_block_count bigint NOT NULL,
  numeric_quantity_rejection_count bigint NOT NULL,
  odt_repeat_attrs_present boolean NOT NULL,
  xml_ph_element_count bigint NOT NULL,
  xml_ph_nested_count  bigint NOT NULL,
  xml_ph_emitted_unique bigint NOT NULL,
  xml_ph_unaccounted   bigint NOT NULL,
  source_structural_block_count_before_repeat_expansion bigint NOT NULL,
  accepted_manifest_sha256 tw_drug_history_stage.sha256_hex NOT NULL,
  accepted_manifest_match boolean NOT NULL,
  statement           text NOT NULL,
  source_row_sha256   tw_drug_history_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, release_id),
  CONSTRAINT source_release_order_uidx UNIQUE (run_id, source_order_index),
  CONSTRAINT source_release_path_uidx UNIQUE (run_id, relative_path),
  CONSTRAINT source_release_content_sha_match_chk
    CHECK (release_id = content_sha256),
  CONSTRAINT source_release_byte_length_chk CHECK (byte_length >= 0),
  CONSTRAINT source_release_nonneg_counts_chk CHECK (
    source_order_index >= 0
    AND block_count >= 0
    AND occurrence_count >= 0
    AND table_count >= 0
    AND row_count_xml >= 0
    AND cell_count_xml >= 0
    AND row_count_logical >= 0
    AND cell_count_logical >= 0
    AND empty_cell_count >= 0
    AND nested_table_count >= 0
    AND empty_table_cell_block_count >= 0
    AND numeric_quantity_rejection_count >= 0
    AND xml_ph_element_count >= 0
    AND xml_ph_nested_count >= 0
    AND xml_ph_emitted_unique >= 0
    AND xml_ph_unaccounted >= 0
    AND source_structural_block_count_before_repeat_expansion >= 0
  ),
  CONSTRAINT source_release_fragments_array_chk
    CHECK (jsonb_typeof(filename_date_fragments_raw) = 'array'),
  CONSTRAINT source_release_chronology_object_chk
    CHECK (jsonb_typeof(analysis_chronology) = 'object'),
  CONSTRAINT source_release_no_legal_date_keys_chk
    CHECK (
      NOT (analysis_chronology ? 'legal_effective_date')
      AND NOT (analysis_chronology ? 'effective_date')
      AND NOT (analysis_chronology ? 'effective_date_iso')
    )
);

CREATE INDEX IF NOT EXISTS source_release_order_idx
  ON tw_drug_history_stage.source_release (run_id, source_order_index);

COMMENT ON TABLE tw_drug_history_stage.source_release IS
  'Source-local release observation; filename chronology is analysis-only.';

-- ---------------------------------------------------------------------------
-- 4. source_artifact
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.source_artifact (
  run_id            uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  artifact_sha256   tw_drug_history_stage.sha256_hex NOT NULL,
  relative_locator  text NOT NULL,
  basename          text NOT NULL,
  byte_length       bigint NOT NULL,
  media_type        text NOT NULL,
  content_sha256    tw_drug_history_stage.sha256_hex NOT NULL,
  source_row_sha256 tw_drug_history_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, artifact_sha256),
  CONSTRAINT source_artifact_locator_uidx UNIQUE (run_id, relative_locator),
  CONSTRAINT source_artifact_sha_match_chk
    CHECK (artifact_sha256 = content_sha256),
  CONSTRAINT source_artifact_byte_length_chk CHECK (byte_length >= 0),
  CONSTRAINT source_artifact_media_type_chk
    CHECK (media_type = 'application/vnd.oasis.opendocument.text')
);

CREATE INDEX IF NOT EXISTS source_artifact_locator_idx
  ON tw_drug_history_stage.source_artifact (run_id, relative_locator);

COMMENT ON TABLE tw_drug_history_stage.source_artifact IS
  'Immutable ODT payload identity for one staging run.';

-- ---------------------------------------------------------------------------
-- 5. release_artifact
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.release_artifact (
  run_id            uuid NOT NULL,
  release_id        tw_drug_history_stage.sha256_hex NOT NULL,
  artifact_sha256   tw_drug_history_stage.sha256_hex NOT NULL,
  association_role  text NOT NULL,
  PRIMARY KEY (run_id, release_id, artifact_sha256),
  CONSTRAINT release_artifact_release_fk
    FOREIGN KEY (run_id, release_id)
    REFERENCES tw_drug_history_stage.source_release (run_id, release_id)
    ON DELETE CASCADE,
  CONSTRAINT release_artifact_artifact_fk
    FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES tw_drug_history_stage.source_artifact (run_id, artifact_sha256)
    ON DELETE CASCADE,
  CONSTRAINT release_artifact_role_chk
    CHECK (association_role IN ('primary_parse_source')),
  CONSTRAINT release_artifact_primary_sha_match_chk
    CHECK (
      association_role <> 'primary_parse_source'
      OR release_id = artifact_sha256
    )
);

-- Exactly one primary parse artifact per release.
CREATE UNIQUE INDEX IF NOT EXISTS release_artifact_one_primary_uidx
  ON tw_drug_history_stage.release_artifact (run_id, release_id)
  WHERE association_role = 'primary_parse_source';

COMMENT ON TABLE tw_drug_history_stage.release_artifact IS
  'Run-scoped release/artifact association; one primary_parse_source per release.';

-- ---------------------------------------------------------------------------
-- 6. structural_block
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.structural_block (
  run_id                 uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  block_id               tw_drug_history_stage.sha256_hex NOT NULL,
  artifact_sha256        tw_drug_history_stage.sha256_hex NOT NULL,
  relative_path          text NOT NULL,
  block_kind             text NOT NULL,
  container              text NOT NULL,
  element_name           text NOT NULL,
  style_name             text,
  in_table               boolean NOT NULL,
  in_index_context       boolean NOT NULL,
  xml_element_index      bigint NOT NULL,
  parser_order           bigint NOT NULL,
  locator                jsonb NOT NULL,
  locator_key            text NOT NULL,
  raw_text               text NOT NULL,
  normalized_search_text text NOT NULL,
  raw_text_sha256        tw_drug_history_stage.sha256_hex NOT NULL,
  raw_text_byte_length   integer NOT NULL,
  raw_text_char_length   integer NOT NULL,
  parser_version         text NOT NULL,
  source_row_sha256      tw_drug_history_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, block_id),
  -- Superkey for occurrence composite FK (block must belong to same artifact).
  CONSTRAINT structural_block_id_artifact_uidx
    UNIQUE (run_id, block_id, artifact_sha256),
  CONSTRAINT structural_block_artifact_fk
    FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES tw_drug_history_stage.source_artifact (run_id, artifact_sha256)
    ON DELETE CASCADE,
  CONSTRAINT structural_block_locator_uidx UNIQUE (run_id, artifact_sha256, locator_key),
  CONSTRAINT structural_block_kind_chk CHECK (
    block_kind IN (
      'paragraph',
      'heading',
      'table_paragraph',
      'frame_paragraph',
      'index_paragraph',
      'empty_table_cell'
    )
  ),
  CONSTRAINT structural_block_container_chk CHECK (
    container IN ('flow', 'table_cell', 'frame', 'index', 'other')
  ),
  CONSTRAINT structural_block_locator_object_chk
    CHECK (jsonb_typeof(locator) = 'object'),
  CONSTRAINT structural_block_xml_index_chk CHECK (xml_element_index >= 0),
  CONSTRAINT structural_block_parser_order_chk CHECK (parser_order >= 0),
  CONSTRAINT structural_block_length_chk CHECK (
    raw_text_byte_length >= 0
    AND raw_text_char_length >= 0
    AND octet_length(convert_to(raw_text, 'UTF8')) = raw_text_byte_length
    AND char_length(raw_text) = raw_text_char_length
  ),
  CONSTRAINT structural_block_empty_cell_invariant_chk CHECK (
    (
      block_kind = 'empty_table_cell'
      AND raw_text = ''
      AND normalized_search_text = ''
      AND raw_text_byte_length = 0
      AND raw_text_char_length = 0
      AND in_table IS TRUE
      AND container = 'table_cell'
      AND (locator ? 'empty_cell')
    )
    OR (block_kind <> 'empty_table_cell')
  )
);

CREATE INDEX IF NOT EXISTS structural_block_artifact_xml_idx
  ON tw_drug_history_stage.structural_block (run_id, artifact_sha256, xml_element_index);

CREATE INDEX IF NOT EXISTS structural_block_artifact_order_idx
  ON tw_drug_history_stage.structural_block (run_id, artifact_sha256, parser_order);

CREATE INDEX IF NOT EXISTS structural_block_kind_idx
  ON tw_drug_history_stage.structural_block (run_id, block_kind);

COMMENT ON TABLE tw_drug_history_stage.structural_block IS
  'Source-local structural block evidence including empty_table_cell locators.';

-- ---------------------------------------------------------------------------
-- 7. occurrence_candidate
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.occurrence_candidate (
  run_id                 uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  occurrence_id          tw_drug_history_stage.sha256_hex NOT NULL,
  artifact_sha256        tw_drug_history_stage.sha256_hex NOT NULL,
  block_id               tw_drug_history_stage.sha256_hex NOT NULL,
  relative_path          text NOT NULL,
  designation_text       text NOT NULL,
  match_start_in_raw     integer NOT NULL,
  match_end_in_raw       integer NOT NULL,
  raw_text_sha256        tw_drug_history_stage.sha256_hex NOT NULL,
  raw_text_byte_length   integer NOT NULL,
  raw_text_char_length   integer NOT NULL,
  container              text NOT NULL,
  in_index_context       boolean NOT NULL,
  ambiguity_flags        jsonb NOT NULL,
  parser_version         text NOT NULL,
  statement              text NOT NULL,
  source_row_sha256      tw_drug_history_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, occurrence_id),
  -- Defense-in-depth: referenced block must belong to the same artifact.
  CONSTRAINT occurrence_candidate_block_artifact_fk
    FOREIGN KEY (run_id, block_id, artifact_sha256)
    REFERENCES tw_drug_history_stage.structural_block (run_id, block_id, artifact_sha256)
    ON DELETE CASCADE,
  CONSTRAINT occurrence_candidate_artifact_fk
    FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES tw_drug_history_stage.source_artifact (run_id, artifact_sha256)
    ON DELETE CASCADE,
  CONSTRAINT occurrence_candidate_container_chk CHECK (
    container IN ('flow', 'table_cell', 'frame', 'index', 'other')
  ),
  CONSTRAINT occurrence_candidate_offsets_chk CHECK (
    match_start_in_raw >= 0
    AND match_end_in_raw > match_start_in_raw
    AND match_end_in_raw <= raw_text_char_length
    AND raw_text_byte_length >= 0
    AND raw_text_char_length >= 0
  ),
  CONSTRAINT occurrence_candidate_designation_chk CHECK (designation_text <> ''),
  CONSTRAINT occurrence_candidate_ambiguity_array_chk
    CHECK (jsonb_typeof(ambiguity_flags) = 'array')
);

CREATE INDEX IF NOT EXISTS occurrence_candidate_block_fk_idx
  ON tw_drug_history_stage.occurrence_candidate (run_id, block_id);

CREATE INDEX IF NOT EXISTS occurrence_candidate_designation_idx
  ON tw_drug_history_stage.occurrence_candidate (run_id, designation_text);

CREATE INDEX IF NOT EXISTS occurrence_candidate_artifact_idx
  ON tw_drug_history_stage.occurrence_candidate (run_id, artifact_sha256);

COMMENT ON TABLE tw_drug_history_stage.occurrence_candidate IS
  'Source-local dotted-designation candidate; raw text/locator live on the block.';

-- ---------------------------------------------------------------------------
-- 8. stage_issue
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tw_drug_history_stage.stage_issue (
  run_id            uuid NOT NULL
    REFERENCES tw_drug_history_stage.rebuild_run (run_id) ON DELETE CASCADE,
  issue_seq         integer NOT NULL,
  issue_code        text NOT NULL,
  issue_class       text NOT NULL,
  severity          text NOT NULL,
  is_blocking       boolean NOT NULL,
  relative_path     text,
  detail            text NOT NULL,
  artifact_sha256   tw_drug_history_stage.sha256_hex,
  block_id          tw_drug_history_stage.sha256_hex,
  locator_key       text,
  attributes        jsonb NOT NULL,
  source_row_sha256 tw_drug_history_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, issue_seq),
  CONSTRAINT stage_issue_seq_chk CHECK (issue_seq >= 0),
  CONSTRAINT stage_issue_severity_chk
    CHECK (severity IN ('info', 'warning', 'error')),
  CONSTRAINT stage_issue_attributes_object_chk
    CHECK (jsonb_typeof(attributes) = 'object'),
  CONSTRAINT stage_issue_blocking_severity_chk
    CHECK (
      (is_blocking IS TRUE AND severity = 'error')
      OR (is_blocking IS FALSE)
    ),
  CONSTRAINT stage_issue_block_fk
    FOREIGN KEY (run_id, block_id)
    REFERENCES tw_drug_history_stage.structural_block (run_id, block_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS stage_issue_blocking_idx
  ON tw_drug_history_stage.stage_issue (run_id, is_blocking)
  WHERE is_blocking IS TRUE;

CREATE INDEX IF NOT EXISTS stage_issue_code_idx
  ON tw_drug_history_stage.stage_issue (run_id, issue_code);

COMMENT ON TABLE tw_drug_history_stage.stage_issue IS
  'Deterministic issue sequence for one staging run; blocking issues prevent seal.';

-- ---------------------------------------------------------------------------
-- Immutability: child evidence may be inserted only while its run is loading,
-- never updated, and deleted only by a parent-run cascade. Parent DELETE is
-- available only through the fingerprint-checked drop-run capability.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION tw_drug_history_stage.guard_evidence_dml()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
  run_state text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT r.state INTO run_state
    FROM tw_drug_history_stage.rebuild_run r
    WHERE r.run_id = NEW.run_id;

    IF run_state IS DISTINCT FROM 'loading' THEN
      RAISE EXCEPTION
        'tw_drug_history_stage: evidence INSERT requires a loading run (table=%)',
        TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: evidence table % is immutable (UPDATE forbidden)',
      TG_TABLE_NAME
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF EXISTS (
      SELECT 1
      FROM tw_drug_history_stage.rebuild_run r
      WHERE r.run_id = OLD.run_id
    ) THEN
      RAISE EXCEPTION
        'tw_drug_history_stage: direct evidence DELETE is forbidden (table=%)',
        TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
  END IF;

  RAISE EXCEPTION
    'tw_drug_history_stage: unsupported evidence operation %', TG_OP
    USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$;

COMMENT ON FUNCTION tw_drug_history_stage.guard_evidence_dml() IS
  'Allows evidence INSERT only for loading runs, rejects UPDATE, and allows DELETE only during parent cascade.';

CREATE OR REPLACE FUNCTION tw_drug_history_stage.reject_evidence_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  RAISE EXCEPTION
    'tw_drug_history_stage: evidence table % is immutable (TRUNCATE forbidden)',
    TG_TABLE_NAME
    USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$;

COMMENT ON FUNCTION tw_drug_history_stage.reject_evidence_truncate() IS
  'Rejects TRUNCATE on all evidence tables; bounded drop-run uses parent DELETE and FK cascades.';

DO $trigs$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'run_input_file',
    'source_release',
    'source_artifact',
    'release_artifact',
    'structural_block',
    'occurrence_candidate',
    'stage_issue'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS trg_%I_no_update ON tw_drug_history_stage.%I',
      t, t
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS trg_%I_dml_guard ON tw_drug_history_stage.%I',
      t, t
    );
    EXECUTE format(
      'CREATE TRIGGER trg_%I_dml_guard
         BEFORE INSERT OR UPDATE OR DELETE ON tw_drug_history_stage.%I
         FOR EACH ROW
         EXECUTE FUNCTION tw_drug_history_stage.guard_evidence_dml()',
      t, t
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS trg_%I_truncate_guard ON tw_drug_history_stage.%I',
      t, t
    );
    EXECUTE format(
      'CREATE TRIGGER trg_%I_truncate_guard
         BEFORE TRUNCATE ON tw_drug_history_stage.%I
         FOR EACH STATEMENT
         EXECUTE FUNCTION tw_drug_history_stage.reject_evidence_truncate()',
      t, t
    );
  END LOOP;
END;
$trigs$;

DROP FUNCTION IF EXISTS tw_drug_history_stage.reject_evidence_update();

-- rebuild_run: only loading → sealed|failed; identifying/input fields immutable;
-- terminal rows cannot be updated.
CREATE OR REPLACE FUNCTION tw_drug_history_stage.rebuild_run_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  IF OLD.state IN ('sealed', 'failed') THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: terminal rebuild_run row is immutable (state=%)',
      OLD.state
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF NOT (OLD.state = 'loading' AND NEW.state IN ('sealed', 'failed')) THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: rebuild_run only allows loading→sealed|failed (old=%, new=%)',
      OLD.state, NEW.state
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.parser_version IS DISTINCT FROM OLD.parser_version
     OR NEW.loader_version IS DISTINCT FROM OLD.loader_version
     OR NEW.contract_version IS DISTINCT FROM OLD.contract_version
     OR NEW.code_hash IS DISTINCT FROM OLD.code_hash
     OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
     OR NEW.accepted_manifest_sha256 IS DISTINCT FROM OLD.accepted_manifest_sha256
     OR NEW.expected_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.expected_release_count IS DISTINCT FROM OLD.expected_release_count
     OR NEW.expected_block_count IS DISTINCT FROM OLD.expected_block_count
     OR NEW.expected_occurrence_count IS DISTINCT FROM OLD.expected_occurrence_count
     OR NEW.expected_empty_table_cell_block_count
        IS DISTINCT FROM OLD.expected_empty_table_cell_block_count
     OR NEW.expected_xml_ph_element_count_total
        IS DISTINCT FROM OLD.expected_xml_ph_element_count_total
     OR NEW.expected_xml_ph_emitted_unique_total
        IS DISTINCT FROM OLD.expected_xml_ph_emitted_unique_total
     OR NEW.expected_xml_ph_unaccounted_total
        IS DISTINCT FROM OLD.expected_xml_ph_unaccounted_total
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: rebuild_run identifying/input fields are immutable'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION tw_drug_history_stage.rebuild_run_update_guard() IS
  'Allows only one terminal transition loading→sealed|failed; freezes identity inputs.';

DROP TRIGGER IF EXISTS trg_rebuild_run_update_guard
  ON tw_drug_history_stage.rebuild_run;
CREATE TRIGGER trg_rebuild_run_update_guard
  BEFORE UPDATE ON tw_drug_history_stage.rebuild_run
  FOR EACH ROW
  EXECUTE FUNCTION tw_drug_history_stage.rebuild_run_update_guard();

CREATE OR REPLACE FUNCTION tw_drug_history_stage.rebuild_run_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  IF OLD.state <> 'sealed'
     OR OLD.sealed_fingerprint IS NULL
     OR current_setting(
          'tw_drug_history_stage.drop_run_fingerprint',
          true
        ) IS DISTINCT FROM OLD.sealed_fingerprint::text
  THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: rebuild_run DELETE requires the bounded drop-run capability'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  RETURN OLD;
END;
$fn$;

COMMENT ON FUNCTION tw_drug_history_stage.rebuild_run_delete_guard() IS
  'Permits parent-run DELETE only after the loader sets the verified fingerprint as a transaction-local capability.';

DROP TRIGGER IF EXISTS trg_rebuild_run_delete_guard
  ON tw_drug_history_stage.rebuild_run;
CREATE TRIGGER trg_rebuild_run_delete_guard
  BEFORE DELETE ON tw_drug_history_stage.rebuild_run
  FOR EACH ROW
  EXECUTE FUNCTION tw_drug_history_stage.rebuild_run_delete_guard();

-- ---------------------------------------------------------------------------
-- ACL: revoke PUBLIC; do not invent named roles
-- ---------------------------------------------------------------------------

REVOKE ALL ON SCHEMA tw_drug_history_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA tw_drug_history_stage FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA tw_drug_history_stage FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA tw_drug_history_stage FROM PUBLIC;

ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_stage
  REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_stage
  REVOKE ALL ON FUNCTIONS FROM PUBLIC;

-- Domain privileges
REVOKE ALL ON DOMAIN tw_drug_history_stage.sha256_hex FROM PUBLIC;

COMMIT;
