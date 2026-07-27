-- 2026-07-27 — immutable WP2 acquisition/raw evidence stage (v2)
--
-- Rows mirror the verified public JSONL contract.  This schema deliberately
-- contains no legal effective date, stable rule identity, legal event,
-- current-version, predecessor/successor, lineage, replay, or diff model.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_acq_stage-global', 0)
);

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Isolated immutable acquisition/raw evidence for NHI rule-history v2; not legal history. managed=tw_drug_history_acq_stage/v2';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'tw_drug_history_acq_stage'
  ) THEN
    CREATE SCHEMA tw_drug_history_acq_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA tw_drug_history_acq_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'tw_drug_history_acq_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'tw_drug_history_acq_stage exists without the managed v2 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

CREATE DOMAIN tw_drug_history_acq_stage.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE tw_drug_history_acq_stage.acquisition_run (
  run_id uuid PRIMARY KEY,
  state text NOT NULL DEFAULT 'loading',
  loader_version text NOT NULL,
  contract_version text NOT NULL,
  migration_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  code_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  source_plan_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  capture_cut date NOT NULL,
  discovery_manifest_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  raw_manifest_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  input_fingerprint tw_drug_history_acq_stage.sha256_hex NOT NULL,
  output_fingerprint tw_drug_history_acq_stage.sha256_hex,
  sealed_fingerprint tw_drug_history_acq_stage.sha256_hex,
  expected_counts jsonb NOT NULL,
  verified_counts jsonb,
  table_fingerprints jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  sealed_at timestamptz,
  failed_at timestamptz,
  failure_code text,
  CONSTRAINT acquisition_run_state_chk
    CHECK (state IN ('loading', 'sealed', 'failed')),
  CONSTRAINT acquisition_run_json_chk
    CHECK (
      jsonb_typeof(expected_counts) = 'object'
      AND (verified_counts IS NULL OR jsonb_typeof(verified_counts) = 'object')
      AND (
        table_fingerprints IS NULL
        OR jsonb_typeof(table_fingerprints) = 'object'
      )
    ),
  CONSTRAINT acquisition_run_terminal_chk
    CHECK (
      (
        state = 'loading'
        AND output_fingerprint IS NULL
        AND sealed_fingerprint IS NULL
        AND verified_counts IS NULL
        AND table_fingerprints IS NULL
        AND sealed_at IS NULL
        AND failed_at IS NULL
        AND failure_code IS NULL
      )
      OR (
        state = 'sealed'
        AND output_fingerprint IS NOT NULL
        AND sealed_fingerprint IS NOT NULL
        AND verified_counts IS NOT NULL
        AND table_fingerprints IS NOT NULL
        AND sealed_at IS NOT NULL
        AND failed_at IS NULL
        AND failure_code IS NULL
      )
      OR (
        state = 'failed'
        AND sealed_fingerprint IS NULL
        AND sealed_at IS NULL
        AND failed_at IS NOT NULL
        AND failure_code IS NOT NULL
      )
    )
);

CREATE UNIQUE INDEX acquisition_run_sealed_fingerprint_uidx
  ON tw_drug_history_acq_stage.acquisition_run (sealed_fingerprint)
  WHERE sealed_fingerprint IS NOT NULL;

CREATE INDEX acquisition_run_input_fingerprint_idx
  ON tw_drug_history_acq_stage.acquisition_run (input_fingerprint);

CREATE TABLE tw_drug_history_acq_stage.input_file (
  run_id uuid NOT NULL
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  logical_name text NOT NULL,
  schema_id text NOT NULL,
  content_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  row_count bigint NOT NULL CHECK (row_count >= 0),
  PRIMARY KEY (run_id, logical_name),
  CONSTRAINT input_file_relative_name_chk
    CHECK (
      logical_name <> ''
      AND logical_name !~ '^[/\\]'
      AND logical_name !~ '(^|[\\/])\.\.([\\/]|$)'
    )
);

CREATE TABLE tw_drug_history_acq_stage.discovery_observation (
  run_id uuid NOT NULL,
  observation_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  adapter_id text NOT NULL,
  request_url text NOT NULL,
  final_url text,
  locator jsonb NOT NULL,
  status text NOT NULL,
  observed_at timestamptz NOT NULL,
  http_status integer,
  response_headers jsonb,
  content_sha256 tw_drug_history_acq_stage.sha256_hex,
  byte_size bigint,
  content_path text,
  error_code text,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, observation_id),
  FOREIGN KEY (run_id)
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  CONSTRAINT discovery_observation_status_chk
    CHECK (
      (
        status = 'success'
        AND final_url IS NOT NULL
        AND http_status BETWEEN 100 AND 599
        AND response_headers IS NOT NULL
        AND content_sha256 IS NOT NULL
        AND byte_size >= 0
        AND content_path IS NOT NULL
        AND error_code IS NULL
      )
      OR (
        status = 'failed'
        AND error_code IS NOT NULL
        AND content_sha256 IS NULL
        AND byte_size IS NULL
        AND content_path IS NULL
      )
    ),
  CONSTRAINT discovery_observation_json_chk
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND (
        response_headers IS NULL
        OR jsonb_typeof(response_headers) = 'object'
      )
      AND jsonb_typeof(row_payload) = 'object'
    ),
  CONSTRAINT discovery_observation_path_chk
    CHECK (
      content_path IS NULL
      OR (
        content_path <> ''
        AND content_path !~ '^[/\\]'
        AND content_path !~ '(^|[\\/])\.\.([\\/]|$)'
      )
    )
);

CREATE TABLE tw_drug_history_acq_stage.discovered_resource (
  run_id uuid NOT NULL,
  resource_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  adapter_id text NOT NULL,
  resource_kind text NOT NULL,
  source_url text NOT NULL,
  parent_resource_id tw_drug_history_acq_stage.sha256_hex,
  discovery_locator jsonb NOT NULL,
  source_label text NOT NULL,
  fetch_state text NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, resource_id),
  FOREIGN KEY (run_id)
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, parent_resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT discovered_resource_json_chk
    CHECK (
      jsonb_typeof(discovery_locator) = 'object'
      AND jsonb_typeof(row_payload) = 'object'
    )
);

CREATE TABLE tw_drug_history_acq_stage.fetch_attempt (
  run_id uuid NOT NULL,
  attempt_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  resource_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  source_url text NOT NULL,
  started_at timestamptz NOT NULL,
  completed_at timestamptz NOT NULL,
  status text NOT NULL,
  acquisition_mode text NOT NULL,
  http_status integer,
  final_url text,
  response_headers jsonb,
  artifact_sha256 tw_drug_history_acq_stage.sha256_hex,
  byte_size bigint,
  error_code text,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, attempt_id),
  FOREIGN KEY (run_id, resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT,
  CONSTRAINT fetch_attempt_time_chk CHECK (completed_at >= started_at),
  CONSTRAINT fetch_attempt_status_chk
    CHECK (
      (
        status = 'success'
        AND http_status BETWEEN 100 AND 599
        AND final_url IS NOT NULL
        AND response_headers IS NOT NULL
        AND artifact_sha256 IS NOT NULL
        AND byte_size >= 0
        AND error_code IS NULL
      )
      OR (
        status = 'failed'
        AND error_code IS NOT NULL
        AND artifact_sha256 IS NULL
        AND byte_size IS NULL
      )
    ),
  CONSTRAINT fetch_attempt_json_chk
    CHECK (
      (
        response_headers IS NULL
        OR jsonb_typeof(response_headers) = 'object'
      )
      AND jsonb_typeof(row_payload) = 'object'
    )
);

CREATE TABLE tw_drug_history_acq_stage.raw_artifact (
  run_id uuid NOT NULL,
  artifact_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  content_path text NOT NULL,
  media_type text NOT NULL,
  first_observed_at timestamptz NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, artifact_sha256),
  FOREIGN KEY (run_id)
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  CONSTRAINT raw_artifact_relative_path_chk
    CHECK (
      content_path <> ''
      AND content_path !~ '^[/\\]'
      AND content_path !~ '(^|[\\/])\.\.([\\/]|$)'
    ),
  CONSTRAINT raw_artifact_json_chk
    CHECK (jsonb_typeof(row_payload) = 'object')
);

CREATE TABLE tw_drug_history_acq_stage.resource_artifact_link (
  run_id uuid NOT NULL,
  link_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  resource_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  artifact_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  relation text NOT NULL,
  observed_at timestamptz NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, link_id),
  FOREIGN KEY (run_id, resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES tw_drug_history_acq_stage.raw_artifact
      (run_id, artifact_sha256)
    ON DELETE RESTRICT,
  CONSTRAINT resource_artifact_link_json_chk
    CHECK (jsonb_typeof(row_payload) = 'object')
);

CREATE TABLE tw_drug_history_acq_stage.artifact_url_observation (
  run_id uuid NOT NULL,
  url_observation_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  resource_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  source_url text NOT NULL,
  artifact_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  relation_to_previous text NOT NULL,
  observed_at timestamptz NOT NULL,
  previous_artifact_sha256 tw_drug_history_acq_stage.sha256_hex,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, url_observation_id),
  FOREIGN KEY (run_id, resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES tw_drug_history_acq_stage.raw_artifact
      (run_id, artifact_sha256)
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, previous_artifact_sha256)
    REFERENCES tw_drug_history_acq_stage.raw_artifact
      (run_id, artifact_sha256)
    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT artifact_url_observation_json_chk
    CHECK (jsonb_typeof(row_payload) = 'object')
);

CREATE TABLE tw_drug_history_acq_stage.acquisition_issue (
  run_id uuid NOT NULL,
  issue_id tw_drug_history_acq_stage.sha256_hex NOT NULL,
  stage text NOT NULL,
  severity text NOT NULL,
  adapter_id text NOT NULL,
  resource_id tw_drug_history_acq_stage.sha256_hex,
  source_url text NOT NULL,
  code text NOT NULL,
  locator jsonb,
  recorded_at timestamptz NOT NULL,
  row_payload jsonb NOT NULL,
  source_row_sha256 tw_drug_history_acq_stage.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, issue_id),
  FOREIGN KEY (run_id)
    REFERENCES tw_drug_history_acq_stage.acquisition_run (run_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, resource_id)
    REFERENCES tw_drug_history_acq_stage.discovered_resource
      (run_id, resource_id)
    ON DELETE RESTRICT,
  CONSTRAINT acquisition_issue_json_chk
    CHECK (
      (locator IS NULL OR jsonb_typeof(locator) = 'object')
      AND jsonb_typeof(row_payload) = 'object'
    )
);

CREATE FUNCTION tw_drug_history_acq_stage.guard_evidence_dml()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    RAISE EXCEPTION
      'acquisition evidence rows are append-only'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT state INTO parent_state
  FROM tw_drug_history_acq_stage.acquisition_run
  WHERE run_id = NEW.run_id;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION
      'acquisition evidence may be inserted only while its run is loading'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION tw_drug_history_acq_stage.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'acquisition stage tables cannot be truncated'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION tw_drug_history_acq_stage.guard_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.state IN ('sealed', 'failed') THEN
    RAISE EXCEPTION
      'terminal acquisition runs are immutable'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF OLD.run_id <> NEW.run_id
     OR OLD.loader_version <> NEW.loader_version
     OR OLD.contract_version <> NEW.contract_version
     OR OLD.migration_sha256 <> NEW.migration_sha256
     OR OLD.code_sha256 <> NEW.code_sha256
     OR OLD.source_plan_sha256 <> NEW.source_plan_sha256
     OR OLD.capture_cut <> NEW.capture_cut
     OR OLD.discovery_manifest_sha256 <> NEW.discovery_manifest_sha256
     OR OLD.raw_manifest_sha256 <> NEW.raw_manifest_sha256
     OR OLD.input_fingerprint <> NEW.input_fingerprint
     OR OLD.expected_counts <> NEW.expected_counts THEN
    RAISE EXCEPTION
      'acquisition run identity fields are immutable'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.state NOT IN ('sealed', 'failed') THEN
    RAISE EXCEPTION
      'loading acquisition run may transition only to sealed or failed'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION tw_drug_history_acq_stage.reject_run_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'acquisition runs cannot be deleted by ordinary DML'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE TRIGGER acquisition_run_update_guard
BEFORE UPDATE ON tw_drug_history_acq_stage.acquisition_run
FOR EACH ROW EXECUTE FUNCTION tw_drug_history_acq_stage.guard_run_update();

CREATE TRIGGER acquisition_run_delete_guard
BEFORE DELETE ON tw_drug_history_acq_stage.acquisition_run
FOR EACH ROW EXECUTE FUNCTION tw_drug_history_acq_stage.reject_run_delete();

CREATE TRIGGER acquisition_run_truncate_guard
BEFORE TRUNCATE ON tw_drug_history_acq_stage.acquisition_run
FOR EACH STATEMENT EXECUTE FUNCTION tw_drug_history_acq_stage.reject_truncate();

DO $guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'input_file',
    'discovery_observation',
    'discovered_resource',
    'fetch_attempt',
    'raw_artifact',
    'resource_artifact_link',
    'artifact_url_observation',
    'acquisition_issue'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON tw_drug_history_acq_stage.%I FOR EACH ROW EXECUTE FUNCTION tw_drug_history_acq_stage.guard_evidence_dml()',
      table_name || '_dml_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON tw_drug_history_acq_stage.%I FOR EACH STATEMENT EXECUTE FUNCTION tw_drug_history_acq_stage.reject_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$guards$;

REVOKE ALL ON SCHEMA tw_drug_history_acq_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA tw_drug_history_acq_stage FROM PUBLIC;
REVOKE ALL ON TYPE tw_drug_history_acq_stage.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA tw_drug_history_acq_stage FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_acq_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_acq_stage
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_acq_stage
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

COMMENT ON TABLE tw_drug_history_acq_stage.acquisition_run IS
  'One immutable verified WP2 acquisition/raw load; never a legal-history version.';
COMMENT ON TABLE tw_drug_history_acq_stage.discovered_resource IS
  'Source-discovered resources; rows do not establish legal events, stable identities, currentness, or effective dates.';
COMMENT ON TABLE tw_drug_history_acq_stage.raw_artifact IS
  'Content-addressed official-source bytes and source-local engineering metadata.';

COMMIT;
