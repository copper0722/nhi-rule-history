-- 2026-07-27 — continuous updater operational evidence (stage only)
--
-- This schema records acquisition observations, bounded worker leases, model
-- attempts, and corpus-bundle handoff receipts.  It grants no authority over
-- any legal-history or publication schema.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_ops-global', 0)
);

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Stage-only operational evidence for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_update_ops/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_ops'
  ) THEN
    CREATE SCHEMA nhi_rule_history_update_ops;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_update_ops IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'nhi_rule_history_update_ops';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'nhi_rule_history_update_ops exists without the managed v1 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  managed_comment text :=
    'NOLOGIN capability role for stage-only NHI updater operations. managed=nhi_rule_history_update_runtime/v1';
  existing_comment text;
  can_login boolean;
  is_super boolean;
  can_create_db boolean;
  can_create_role boolean;
  inherits_privileges boolean;
  can_replicate boolean;
  can_bypass_rls boolean;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_runtime'
  ) THEN
    CREATE ROLE nhi_rule_history_update_runtime
      NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
      NOBYPASSRLS;
    COMMENT ON ROLE nhi_rule_history_update_runtime IS
      'NOLOGIN capability role for stage-only NHI updater operations. managed=nhi_rule_history_update_runtime/v1';
  ELSE
    SELECT
      shobj_description(oid, 'pg_authid'),
      rolcanlogin,
      rolsuper,
      rolcreatedb,
      rolcreaterole,
      rolinherit,
      rolreplication,
      rolbypassrls
      INTO
        existing_comment,
        can_login,
        is_super,
        can_create_db,
        can_create_role,
        inherits_privileges,
        can_replicate,
        can_bypass_rls
    FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_runtime';
    IF existing_comment IS DISTINCT FROM managed_comment
       OR can_login OR is_super OR can_create_db OR can_create_role
       OR inherits_privileges OR can_replicate OR can_bypass_rls THEN
      RAISE EXCEPTION
        'nhi_rule_history_update_runtime exists without the managed least-privilege marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

CREATE DOMAIN nhi_rule_history_update_ops.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE nhi_rule_history_update_ops.update_job (
  job_id uuid PRIMARY KEY,
  job_fingerprint nhi_rule_history_update_ops.sha256_hex NOT NULL UNIQUE,
  contract_version text NOT NULL,
  runner_version text NOT NULL,
  feed_url text NOT NULL,
  request_profile_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
  notification_window_start timestamptz NOT NULL,
  notification_window_end timestamptz NOT NULL,
  activation_cut date NOT NULL,
  scheduled_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT update_job_window_chk
    CHECK (notification_window_end > notification_window_start),
  CONSTRAINT update_job_feed_url_chk
    CHECK (feed_url ~ '^https://')
);

CREATE TABLE nhi_rule_history_update_ops.job_lease (
  lease_id uuid PRIMARY KEY,
  job_id uuid NOT NULL
    REFERENCES nhi_rule_history_update_ops.update_job (job_id)
    ON DELETE RESTRICT,
  owner_key text NOT NULL,
  acquired_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  max_runtime_seconds integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, lease_id),
  CONSTRAINT job_lease_owner_chk CHECK (btrim(owner_key) <> ''),
  CONSTRAINT job_lease_runtime_chk
    CHECK (max_runtime_seconds BETWEEN 1 AND 21600),
  CONSTRAINT job_lease_interval_chk
    CHECK (
      expires_at > acquired_at
      AND expires_at <=
        acquired_at + max_runtime_seconds * interval '1 second'
    )
);

CREATE TABLE nhi_rule_history_update_ops.worker_attempt (
  attempt_id uuid PRIMARY KEY,
  job_id uuid NOT NULL,
  lease_id uuid NOT NULL,
  owner_key text NOT NULL,
  attempt_no smallint NOT NULL,
  lane text NOT NULL,
  primary_attempt_id uuid,
  provider text NOT NULL,
  runtime text NOT NULL,
  model text NOT NULL,
  prompt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
  output_sha256 nhi_rule_history_update_ops.sha256_hex,
  started_at timestamptz NOT NULL,
  completed_at timestamptz NOT NULL,
  status text NOT NULL,
  failure_code text,
  fallback_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, attempt_id),
  FOREIGN KEY (job_id, lease_id)
    REFERENCES nhi_rule_history_update_ops.job_lease (job_id, lease_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (job_id, primary_attempt_id)
    REFERENCES nhi_rule_history_update_ops.worker_attempt (job_id, attempt_id)
    ON DELETE RESTRICT,
  CONSTRAINT worker_attempt_time_chk CHECK (completed_at >= started_at),
  CONSTRAINT worker_attempt_lane_chk
    CHECK (
      (
        lane = 'primary'
        AND attempt_no = 1
        AND primary_attempt_id IS NULL
        AND fallback_reason IS NULL
      )
      OR (
        lane = 'fallback'
        AND attempt_no = 2
        AND primary_attempt_id IS NOT NULL
        AND fallback_reason IS NOT NULL
        AND btrim(fallback_reason) <> ''
      )
    ),
  CONSTRAINT worker_attempt_status_chk
    CHECK (
      (
        status = 'success'
        AND output_sha256 IS NOT NULL
        AND failure_code IS NULL
      )
      OR (
        status = 'failed'
        AND failure_code IS NOT NULL
        AND btrim(failure_code) <> ''
      )
    )
);

CREATE UNIQUE INDEX worker_attempt_one_primary_per_job_uidx
  ON nhi_rule_history_update_ops.worker_attempt (job_id)
  WHERE lane = 'primary';

CREATE UNIQUE INDEX worker_attempt_one_fallback_per_job_uidx
  ON nhi_rule_history_update_ops.worker_attempt (job_id)
  WHERE lane = 'fallback';

CREATE TABLE nhi_rule_history_update_ops.content_artifact (
  artifact_sha256 nhi_rule_history_update_ops.sha256_hex PRIMARY KEY,
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  media_type text NOT NULL,
  bundle_relative_path text NOT NULL,
  first_observed_at timestamptz NOT NULL,
  CONSTRAINT content_artifact_relative_path_chk
    CHECK (
      bundle_relative_path <> ''
      AND bundle_relative_path !~ '^[/\\]'
      AND bundle_relative_path !~ '(^|[\\/])\.\.([\\/]|$)'
    )
);

CREATE TABLE nhi_rule_history_update_ops.url_observation (
  url_observation_id uuid PRIMARY KEY,
  job_id uuid NOT NULL
    REFERENCES nhi_rule_history_update_ops.update_job (job_id)
    ON DELETE RESTRICT,
  lease_id uuid NOT NULL,
  owner_key text NOT NULL,
  requested_url text NOT NULL,
  final_url text,
  observed_at timestamptz NOT NULL,
  outcome text NOT NULL,
  http_status integer,
  response_headers jsonb,
  response_headers_sha256 nhi_rule_history_update_ops.sha256_hex,
  artifact_sha256 nhi_rule_history_update_ops.sha256_hex
    REFERENCES nhi_rule_history_update_ops.content_artifact (artifact_sha256)
    ON DELETE RESTRICT,
  previous_artifact_sha256 nhi_rule_history_update_ops.sha256_hex
    REFERENCES nhi_rule_history_update_ops.content_artifact (artifact_sha256)
    ON DELETE RESTRICT,
  relation_to_previous text NOT NULL,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, url_observation_id),
  FOREIGN KEY (job_id, lease_id)
    REFERENCES nhi_rule_history_update_ops.job_lease (job_id, lease_id)
    ON DELETE RESTRICT,
  CONSTRAINT url_observation_https_chk
    CHECK (
      requested_url ~ '^https://'
      AND (final_url IS NULL OR final_url ~ '^https://')
    ),
  CONSTRAINT url_observation_headers_chk
    CHECK (
      response_headers IS NULL
      OR jsonb_typeof(response_headers) = 'object'
    ),
  CONSTRAINT url_observation_outcome_chk
    CHECK (
      (
        outcome = 'response'
        AND http_status BETWEEN 100 AND 599
        AND final_url IS NOT NULL
        AND response_headers IS NOT NULL
        AND response_headers_sha256 IS NOT NULL
        AND artifact_sha256 IS NOT NULL
        AND error_code IS NULL
      )
      OR (
        outcome = 'transport_error'
        AND http_status IS NULL
        AND final_url IS NULL
        AND response_headers IS NULL
        AND response_headers_sha256 IS NULL
        AND artifact_sha256 IS NULL
        AND error_code IS NOT NULL
        AND btrim(error_code) <> ''
      )
    ),
  CONSTRAINT url_observation_relation_chk
    CHECK (
      relation_to_previous IN (
        'first_observation',
        'same_bytes',
        'same_url_new_bytes',
        'redirect_changed',
        'not_comparable'
      )
    )
);

CREATE INDEX url_observation_requested_url_idx
  ON nhi_rule_history_update_ops.url_observation
    (requested_url, observed_at);

CREATE TABLE nhi_rule_history_update_ops.feed_observation (
  feed_observation_id uuid PRIMARY KEY,
  job_id uuid NOT NULL,
  url_observation_id uuid NOT NULL UNIQUE
    REFERENCES nhi_rule_history_update_ops.url_observation (url_observation_id)
    ON DELETE RESTRICT,
  response_artifact_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL
    REFERENCES nhi_rule_history_update_ops.content_artifact (artifact_sha256)
    ON DELETE RESTRICT,
  parser_version text NOT NULL,
  parse_status text NOT NULL,
  channel_title_raw text,
  item_count integer,
  item_sequence_sha256 nhi_rule_history_update_ops.sha256_hex,
  parsed_at timestamptz NOT NULL,
  parse_error_code text,
  FOREIGN KEY (job_id, url_observation_id)
    REFERENCES nhi_rule_history_update_ops.url_observation
      (job_id, url_observation_id)
    ON DELETE RESTRICT,
  CONSTRAINT feed_observation_status_chk
    CHECK (
      (
        parse_status = 'parsed'
        AND item_count > 0
        AND item_sequence_sha256 IS NOT NULL
        AND parse_error_code IS NULL
      )
      OR (
        parse_status IN (
          'non_xml',
          'malformed_xml',
          'zero_item',
          'unexpected_item_collapse'
        )
        AND (item_count IS NULL OR item_count >= 0)
        AND parse_error_code IS NOT NULL
        AND btrim(parse_error_code) <> ''
      )
    )
);

CREATE TABLE nhi_rule_history_update_ops.feed_item_observation (
  feed_observation_id uuid NOT NULL
    REFERENCES nhi_rule_history_update_ops.feed_observation
      (feed_observation_id)
    ON DELETE RESTRICT,
  item_index integer NOT NULL CHECK (item_index >= 0),
  item_fingerprint nhi_rule_history_update_ops.sha256_hex NOT NULL,
  guid_raw text,
  title_raw text NOT NULL,
  link_raw text NOT NULL,
  published_raw text,
  description_raw text,
  raw_item_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
  PRIMARY KEY (feed_observation_id, item_index),
  UNIQUE (feed_observation_id, item_fingerprint)
);

CREATE TABLE nhi_rule_history_update_ops.bundle_receipt (
  receipt_id uuid PRIMARY KEY,
  job_id uuid NOT NULL
    REFERENCES nhi_rule_history_update_ops.update_job (job_id)
    ON DELETE RESTRICT,
  bundle_uid text NOT NULL,
  manifest_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
  bundle_relative_path text NOT NULL,
  artifact_count integer NOT NULL CHECK (artifact_count >= 0),
  total_bytes bigint NOT NULL CHECK (total_bytes >= 0),
  prepared_at timestamptz NOT NULL,
  atomically_published_at timestamptz,
  pg_received_at timestamptz NOT NULL,
  fsync_verified boolean NOT NULL,
  receipt_status text NOT NULL,
  rejection_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (manifest_sha256)
    REFERENCES nhi_rule_history_update_ops.content_artifact (artifact_sha256)
    ON DELETE RESTRICT,
  UNIQUE (job_id, receipt_id),
  UNIQUE (bundle_uid, manifest_sha256),
  CONSTRAINT bundle_receipt_relative_path_chk
    CHECK (
      bundle_relative_path <> ''
      AND bundle_relative_path !~ '^[/\\]'
      AND bundle_relative_path !~ '(^|[\\/])\.\.([\\/]|$)'
    ),
  CONSTRAINT bundle_receipt_time_chk
    CHECK (
      pg_received_at >= prepared_at
      AND (
        atomically_published_at IS NULL
        OR atomically_published_at >= prepared_at
      )
    ),
  CONSTRAINT bundle_receipt_status_chk
    CHECK (
      (
        receipt_status = 'received'
        AND fsync_verified
        AND atomically_published_at IS NOT NULL
        AND pg_received_at >= atomically_published_at
        AND rejection_code IS NULL
      )
      OR (
        receipt_status = 'rejected'
        AND rejection_code IS NOT NULL
        AND btrim(rejection_code) <> ''
      )
    )
);

CREATE FUNCTION nhi_rule_history_update_ops.reject_append_only_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'continuous-updater operational evidence is append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION nhi_rule_history_update_ops.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'continuous-updater operational tables cannot be truncated'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION nhi_rule_history_update_ops.guard_lease_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi_rule_history_update_ops-lease:' || NEW.job_id::text,
      0
    )
  );
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_ops.job_lease prior
    WHERE prior.job_id = NEW.job_id
      AND pg_catalog.tstzrange(
        prior.acquired_at, prior.expires_at, '[)'
      ) && pg_catalog.tstzrange(
        NEW.acquired_at, NEW.expires_at, '[)'
      )
  ) THEN
    RAISE EXCEPTION
      'overlapping leases for one update job are forbidden'
      USING ERRCODE = 'exclusion_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION nhi_rule_history_update_ops.guard_worker_attempt_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  primary_status text;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id;

  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.started_at < lease_start
     OR NEW.completed_at > lease_end THEN
    RAISE EXCEPTION
      'worker attempt is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NEW.lane = 'fallback' THEN
    SELECT status INTO primary_status
    FROM nhi_rule_history_update_ops.worker_attempt
    WHERE job_id = NEW.job_id
      AND attempt_id = NEW.primary_attempt_id
      AND lane = 'primary';
    IF primary_status IS DISTINCT FROM 'failed' THEN
      RAISE EXCEPTION
        'fallback must reference the failed primary attempt for the same job'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION nhi_rule_history_update_ops.guard_owned_observation_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id;
  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.observed_at < lease_start
     OR NEW.observed_at > lease_end THEN
    RAISE EXCEPTION
      'URL observation is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER job_lease_insert_guard
BEFORE INSERT ON nhi_rule_history_update_ops.job_lease
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_ops.guard_lease_insert();

CREATE TRIGGER worker_attempt_insert_guard
BEFORE INSERT ON nhi_rule_history_update_ops.worker_attempt
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_ops.guard_worker_attempt_insert();

CREATE TRIGGER url_observation_insert_guard
BEFORE INSERT ON nhi_rule_history_update_ops.url_observation
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_ops.guard_owned_observation_insert();

DO $append_only_guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'update_job',
    'job_lease',
    'worker_attempt',
    'content_artifact',
    'url_observation',
    'feed_observation',
    'feed_item_observation',
    'bundle_receipt'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON nhi_rule_history_update_ops.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_update_ops.reject_append_only_change()',
      table_name || '_append_only_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON nhi_rule_history_update_ops.%I FOR EACH STATEMENT EXECUTE FUNCTION nhi_rule_history_update_ops.reject_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$append_only_guards$;

REVOKE ALL ON SCHEMA nhi_rule_history_update_ops FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_update_ops FROM PUBLIC;
REVOKE ALL ON TYPE nhi_rule_history_update_ops.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA nhi_rule_history_update_ops
  FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_update_ops
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_update_ops
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_update_ops
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history_update_ops
  TO nhi_rule_history_update_runtime;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA nhi_rule_history_update_ops
  TO nhi_rule_history_update_runtime;

COMMENT ON TABLE nhi_rule_history_update_ops.update_job IS
  'One uniquely fingerprinted polling job; append-only and stage-only.';
COMMENT ON TABLE nhi_rule_history_update_ops.url_observation IS
  'Exact HTTP response or transport-error observation, including same-URL/new-bytes evidence.';
COMMENT ON TABLE nhi_rule_history_update_ops.feed_observation IS
  'Deterministic parse result tied to the exact captured RSS response artifact.';
COMMENT ON TABLE nhi_rule_history_update_ops.worker_attempt IS
  'At most one primary and one failed-primary-linked fallback model attempt per job.';
COMMENT ON TABLE nhi_rule_history_update_ops.bundle_receipt IS
  'Append-only receipt for fsync plus atomic-rename corpus bundle handoff.';

COMMIT;
