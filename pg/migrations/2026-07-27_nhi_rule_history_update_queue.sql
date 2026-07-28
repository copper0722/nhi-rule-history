-- 2026-07-27 — durable per-RSS-identity work queue (stage only)
--
-- One work item represents one exact official-feed item identity: explicit
-- RSS GUID when present, otherwise its validated official detail URL.
-- Re-observation is many-to-one and never changes first-observation
-- provenance. State is an append-only, gap-free transition log. This schema
-- has no write privilege on legal-history or publication tables.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $dependency_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_ops'
      AND obj_description(oid, 'pg_namespace') =
        'Stage-only operational evidence for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_update_ops/v1'
  ) THEN
    RAISE EXCEPTION
      'managed update-ops v1 schema is required before update queue'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_candidate_stage'
      AND obj_description(oid, 'pg_namespace') =
        'Stage-only source-grounded proposals for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_candidate_stage/v1'
  ) THEN
    RAISE EXCEPTION
      'managed candidate-stage v1 schema is required before update queue'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Stage-only durable per-RSS-identity work queue for the NHI rule-history updater; not legal history. managed=nhi_rule_history_update_queue/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_queue'
  ) THEN
    CREATE SCHEMA nhi_rule_history_update_queue;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_update_queue IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'nhi_rule_history_update_queue';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'update queue exists without the managed v1 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  managed_comment text :=
    'NOLOGIN capability role for stage-only NHI RSS queue operations. managed=nhi_rule_history_update_queue_runtime/v1';
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
    WHERE rolname = 'nhi_rule_history_update_queue_runtime'
  ) THEN
    CREATE ROLE nhi_rule_history_update_queue_runtime
      NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
      NOBYPASSRLS;
    COMMENT ON ROLE nhi_rule_history_update_queue_runtime IS
      'NOLOGIN capability role for stage-only NHI RSS queue operations. managed=nhi_rule_history_update_queue_runtime/v1';
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
    WHERE rolname = 'nhi_rule_history_update_queue_runtime';
    IF existing_comment IS DISTINCT FROM managed_comment
       OR can_login OR is_super OR can_create_db OR can_create_role
       OR inherits_privileges OR can_replicate OR can_bypass_rls THEN
      RAISE EXCEPTION
        'update queue runtime role lacks its managed least-privilege marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.schema_migration (
    migration_id text PRIMARY KEY,
    contract_marker text NOT NULL UNIQUE,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT schema_migration_id_chk
      CHECK (migration_id = '2026-07-27_nhi_rule_history_update_queue'),
    CONSTRAINT schema_migration_marker_chk
      CHECK (
        contract_marker =
          'managed=nhi_rule_history_update_queue/v1'
      )
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_update_queue.rss_work_item (
  work_item_id uuid PRIMARY KEY,
  rss_identity_fingerprint text NOT NULL UNIQUE
    CHECK (rss_identity_fingerprint ~ '^[0-9a-f]{64}$'),
  item_identity_kind text NOT NULL,
  item_identity_value text NOT NULL,
  source_feed_url text NOT NULL,
  guid_raw text,
  first_feed_observation_id uuid NOT NULL,
  first_item_index integer NOT NULL CHECK (first_item_index >= 0),
  first_item_fingerprint text NOT NULL
    CHECK (first_item_fingerprint ~ '^[0-9a-f]{64}$'),
  first_title_raw text NOT NULL,
  first_link_raw text NOT NULL,
  first_observed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_feed_url, item_identity_kind, item_identity_value),
  FOREIGN KEY (first_feed_observation_id, first_item_index)
    REFERENCES nhi_rule_history_update_ops.feed_item_observation
      (feed_observation_id, item_index)
    ON DELETE RESTRICT,
  CONSTRAINT rss_work_item_identity_chk CHECK (
    btrim(item_identity_value) <> ''
    AND (
      (
        item_identity_kind = 'rss_guid'
        AND guid_raw IS NOT NULL
        AND btrim(guid_raw) <> ''
        AND item_identity_value = guid_raw
      )
      OR (
        item_identity_kind = 'official_detail_url'
        AND guid_raw IS NULL
        AND item_identity_value = first_link_raw
      )
    )
  ),
  CONSTRAINT rss_work_item_title_chk CHECK (btrim(first_title_raw) <> ''),
  CONSTRAINT rss_work_item_feed_url_chk
    CHECK (source_feed_url ~ '^https://'),
  CONSTRAINT rss_work_item_link_url_chk
    CHECK (first_link_raw ~ '^https://')
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.rss_work_observation (
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    feed_observation_id uuid NOT NULL,
    item_index integer NOT NULL CHECK (item_index >= 0),
    observed_at timestamptz NOT NULL,
    item_fingerprint text NOT NULL
      CHECK (item_fingerprint ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, feed_observation_id, item_index),
    UNIQUE (feed_observation_id, item_index),
    FOREIGN KEY (feed_observation_id, item_index)
      REFERENCES nhi_rule_history_update_ops.feed_item_observation
        (feed_observation_id, item_index)
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.work_item_transition (
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    transition_seq integer NOT NULL CHECK (transition_seq > 0),
    transition_id uuid NOT NULL UNIQUE,
    from_state text,
    to_state text NOT NULL,
    actor_kind text NOT NULL,
    evidence_sha256 text NOT NULL
      CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_json jsonb NOT NULL,
    source_job_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    bundle_receipt_id uuid
      REFERENCES nhi_rule_history_update_ops.bundle_receipt (receipt_id)
      ON DELETE RESTRICT,
    candidate_proposal_id uuid
      REFERENCES nhi_rule_history_candidate_stage.candidate_proposal
        (proposal_id)
      ON DELETE RESTRICT,
    recorded_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, transition_seq),
    CONSTRAINT work_item_transition_actor_chk
      CHECK (btrim(actor_kind) <> ''),
    CONSTRAINT work_item_transition_evidence_chk
      CHECK (
        jsonb_typeof(evidence_json) = 'object'
        AND evidence_json <> '{}'::jsonb
      ),
    CONSTRAINT work_item_transition_from_state_chk
      CHECK (
        from_state IS NULL
        OR from_state IN (
          'observed',
          'selected',
          'acquired',
          'corpus_registered',
          'proposal_running',
          'staged_needs_review',
          'staged_pending_anchor',
          'failed_terminal',
          'ignored_non_rule'
        )
      ),
    CONSTRAINT work_item_transition_to_state_chk
      CHECK (
        to_state IN (
          'observed',
          'selected',
          'acquired',
          'corpus_registered',
          'proposal_running',
          'staged_needs_review',
          'staged_pending_anchor',
          'failed_terminal',
          'ignored_non_rule'
        )
      )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.work_item_attempt (
    attempt_id uuid PRIMARY KEY,
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    attempt_fingerprint text NOT NULL UNIQUE
      CHECK (attempt_fingerprint ~ '^[0-9a-f]{64}$'),
    attempt_kind text NOT NULL
      CHECK (
        attempt_kind IN (
          'acquisition', 'corpus_registration', 'proposal'
        )
      ),
    outcome text NOT NULL
      CHECK (outcome IN ('success', 'transient_failure')),
    work_state_at_attempt text NOT NULL
      CHECK (
        work_state_at_attempt IN (
          'selected', 'acquired', 'proposal_running'
        )
      ),
    actor_kind text NOT NULL CHECK (btrim(actor_kind) <> ''),
    sanitization_profile text NOT NULL
      CHECK (
        sanitization_profile =
          'nhi-rule-history/attempt-evidence-sanitization/v1'
      ),
    evidence_sha256 text NOT NULL
      CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_json jsonb NOT NULL,
    source_job_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    recorded_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_item_id, attempt_fingerprint),
    CONSTRAINT work_item_attempt_evidence_chk CHECK (
      jsonb_typeof(evidence_json) = 'object'
      AND evidence_json <> '{}'::jsonb
      AND evidence_json::text !~*
        '"[^"]*(authorization|cookie|credential|password|passwd|secret|token|api[_-]?key|dsn|conninfo)[^"]*"[[:space:]]*:'
      AND evidence_json::text !~*
        '(postgres|postgresql)://[^"[:space:]@/]+@'
      AND evidence_json::text !~*
        '(bearer|basic)[[:space:]]+[A-Za-z0-9._~+/=-]{8,}'
    )
  );

CREATE INDEX IF NOT EXISTS rss_work_observation_observed_idx
  ON nhi_rule_history_update_queue.rss_work_observation
    (observed_at, work_item_id);

CREATE INDEX IF NOT EXISTS work_item_transition_state_idx
  ON nhi_rule_history_update_queue.work_item_transition
    (to_state, recorded_at, work_item_id);

CREATE INDEX IF NOT EXISTS work_item_attempt_item_time_idx
  ON nhi_rule_history_update_queue.work_item_attempt
    (work_item_id, recorded_at, attempt_id);

CREATE INDEX IF NOT EXISTS work_item_attempt_kind_outcome_idx
  ON nhi_rule_history_update_queue.work_item_attempt
    (attempt_kind, outcome, recorded_at);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.reject_append_only_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'NHI update queue evidence is append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'NHI update queue tables cannot be truncated'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_work_item_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  source_guid text;
  source_title text;
  source_link text;
  source_fingerprint text;
  source_feed_url text;
  source_observed_at timestamptz;
BEGIN
  SELECT
    item.guid_raw,
    item.title_raw,
    item.link_raw,
    item.item_fingerprint,
    job.feed_url,
    feed.parsed_at
  INTO
    source_guid,
    source_title,
    source_link,
    source_fingerprint,
    source_feed_url,
    source_observed_at
  FROM nhi_rule_history_update_ops.feed_item_observation item
  JOIN nhi_rule_history_update_ops.feed_observation feed
    ON feed.feed_observation_id = item.feed_observation_id
  JOIN nhi_rule_history_update_ops.update_job job
    ON job.job_id = feed.job_id
  WHERE item.feed_observation_id = NEW.first_feed_observation_id
    AND item.item_index = NEW.first_item_index;

  IF NOT FOUND
     OR source_guid IS NULL
     OR btrim(source_guid) = ''
     OR source_guid IS DISTINCT FROM NEW.item_identity_value
     OR (
       NEW.item_identity_kind = 'rss_guid'
       AND NEW.guid_raw IS DISTINCT FROM NEW.item_identity_value
     )
     OR (
       NEW.item_identity_kind = 'official_detail_url'
       AND (
         NEW.guid_raw IS NOT NULL
         OR NEW.first_link_raw IS DISTINCT FROM NEW.item_identity_value
       )
     )
     OR source_title IS DISTINCT FROM NEW.first_title_raw
     OR source_link IS DISTINCT FROM NEW.first_link_raw
     OR source_fingerprint IS DISTINCT FROM NEW.first_item_fingerprint
     OR source_feed_url IS DISTINCT FROM NEW.source_feed_url
     OR source_observed_at IS DISTINCT FROM NEW.first_observed_at THEN
    RAISE EXCEPTION
      'work-item first-observation provenance does not match the RSS row'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_work_observation_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  source_guid text;
  source_fingerprint text;
  source_feed_url text;
  source_observed_at timestamptz;
  item_guid text;
  item_feed_url text;
BEGIN
  SELECT
    item.guid_raw,
    item.item_fingerprint,
    job.feed_url,
    feed.parsed_at
  INTO
    source_guid,
    source_fingerprint,
    source_feed_url,
    source_observed_at
  FROM nhi_rule_history_update_ops.feed_item_observation item
  JOIN nhi_rule_history_update_ops.feed_observation feed
    ON feed.feed_observation_id = item.feed_observation_id
  JOIN nhi_rule_history_update_ops.update_job job
    ON job.job_id = feed.job_id
  WHERE item.feed_observation_id = NEW.feed_observation_id
    AND item.item_index = NEW.item_index;

  SELECT queued.item_identity_value, queued.source_feed_url
    INTO item_guid, item_feed_url
  FROM nhi_rule_history_update_queue.rss_work_item queued
  WHERE queued.work_item_id = NEW.work_item_id;

  IF NOT FOUND
     OR source_guid IS NULL
     OR btrim(source_guid) = ''
     OR source_guid IS DISTINCT FROM item_guid
     OR source_feed_url IS DISTINCT FROM item_feed_url
     OR source_fingerprint IS DISTINCT FROM NEW.item_fingerprint
     OR source_observed_at IS DISTINCT FROM NEW.observed_at THEN
    RAISE EXCEPTION
      'work observation does not match its RSS identity and exact feed row'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_transition_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  prior_seq integer;
  prior_state text;
  prior_recorded_at timestamptz;
  candidate_bundle_id uuid;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  SELECT transition_seq, to_state, recorded_at
    INTO prior_seq, prior_state, prior_recorded_at
  FROM nhi_rule_history_update_queue.work_item_transition
  WHERE work_item_id = NEW.work_item_id
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF NOT FOUND THEN
    IF NEW.transition_seq <> 1
       OR NEW.from_state IS NOT NULL
       OR NEW.to_state <> 'observed' THEN
      RAISE EXCEPTION
        'first work-item transition must be seq 1: NULL -> observed'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    IF prior_state IN (
      'staged_needs_review',
      'staged_pending_anchor',
      'failed_terminal',
      'ignored_non_rule'
    ) THEN
      RAISE EXCEPTION
        'terminal work-item states prevent silent retry'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF NEW.transition_seq <> prior_seq + 1
       OR NEW.from_state IS DISTINCT FROM prior_state
       OR NEW.recorded_at < prior_recorded_at THEN
      RAISE EXCEPTION
        'work-item transition sequence/state/time is not gap-free'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF NOT (
      (prior_state = 'observed'
        AND NEW.to_state IN ('selected', 'ignored_non_rule', 'failed_terminal'))
      OR (prior_state = 'selected'
        AND NEW.to_state IN (
          'acquired', 'failed_terminal', 'ignored_non_rule'
        ))
      OR (prior_state = 'acquired'
        AND NEW.to_state IN ('corpus_registered', 'failed_terminal'))
      OR (prior_state = 'corpus_registered'
        AND NEW.to_state IN ('proposal_running', 'failed_terminal'))
      OR (prior_state = 'proposal_running'
        AND NEW.to_state IN (
          'staged_needs_review',
          'staged_pending_anchor',
          'failed_terminal'
        ))
    ) THEN
      RAISE EXCEPTION
        'work-item transition edge is not allowed'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  IF NEW.to_state IN (
    'observed',
    'selected',
    'acquired',
    'corpus_registered',
    'proposal_running',
    'ignored_non_rule'
  ) AND (
    NEW.bundle_receipt_id IS NOT NULL
    OR NEW.candidate_proposal_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION
      'pre-staging queue states cannot claim update bundle or candidate identifiers'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF NEW.to_state IN (
    'staged_needs_review', 'staged_pending_anchor'
  ) THEN
    IF NEW.bundle_receipt_id IS NULL
       OR NEW.candidate_proposal_id IS NULL THEN
      RAISE EXCEPTION
        'staged states require matching bundle and candidate identifiers'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT bundle_receipt_id
      INTO candidate_bundle_id
    FROM nhi_rule_history_candidate_stage.candidate_proposal
    WHERE proposal_id = NEW.candidate_proposal_id;
    IF NOT FOUND
       OR candidate_bundle_id IS DISTINCT FROM NEW.bundle_receipt_id THEN
      RAISE EXCEPTION
        'staged candidate does not belong to the supplied bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;

  IF NEW.to_state = 'failed_terminal'
     AND NEW.candidate_proposal_id IS NOT NULL THEN
    IF NEW.bundle_receipt_id IS NULL THEN
      RAISE EXCEPTION
        'terminal failure candidate identifier requires its bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT bundle_receipt_id
      INTO candidate_bundle_id
    FROM nhi_rule_history_candidate_stage.candidate_proposal
    WHERE proposal_id = NEW.candidate_proposal_id;
    IF NOT FOUND
       OR candidate_bundle_id IS DISTINCT FROM NEW.bundle_receipt_id THEN
      RAISE EXCEPTION
        'terminal failure candidate does not belong to the supplied bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_work_attempt_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  current_state text;
  current_recorded_at timestamptz;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  SELECT transition.to_state, transition.recorded_at
    INTO current_state, current_recorded_at
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = NEW.work_item_id
  ORDER BY transition.transition_seq DESC
  LIMIT 1;

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'work attempt requires an observed work-item state'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF current_state IN (
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'ignored_non_rule'
  ) THEN
    RAISE EXCEPTION
      'terminal work-item states reject new operational attempts'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF NEW.work_state_at_attempt IS DISTINCT FROM current_state
     OR NEW.recorded_at < current_recorded_at THEN
    RAISE EXCEPTION
      'work attempt does not match the current append-only state'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF NOT (
    (NEW.attempt_kind = 'acquisition' AND current_state = 'selected')
    OR (
      NEW.attempt_kind = 'corpus_registration'
      AND current_state = 'acquired'
    )
    OR (
      NEW.attempt_kind = 'proposal'
      AND current_state = 'proposal_running'
    )
  ) THEN
    RAISE EXCEPTION
      'work attempt kind is incompatible with the current state'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS rss_work_item_insert_guard
  ON nhi_rule_history_update_queue.rss_work_item;
CREATE TRIGGER rss_work_item_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.rss_work_item
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_queue.guard_work_item_insert();

DROP TRIGGER IF EXISTS rss_work_observation_insert_guard
  ON nhi_rule_history_update_queue.rss_work_observation;
CREATE TRIGGER rss_work_observation_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.rss_work_observation
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_update_queue.guard_work_observation_insert();

DROP TRIGGER IF EXISTS work_item_transition_insert_guard
  ON nhi_rule_history_update_queue.work_item_transition;
CREATE TRIGGER work_item_transition_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.work_item_transition
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_queue.guard_transition_insert();

DROP TRIGGER IF EXISTS work_item_attempt_insert_guard
  ON nhi_rule_history_update_queue.work_item_attempt;
CREATE TRIGGER work_item_attempt_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.work_item_attempt
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_update_queue.guard_work_attempt_insert();

DO $append_only_guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'schema_migration',
    'rss_work_item',
    'rss_work_observation',
    'work_item_transition',
    'work_item_attempt'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_update_queue.%I',
      table_name || '_append_only_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON nhi_rule_history_update_queue.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_update_queue.reject_append_only_change()',
      table_name || '_append_only_guard',
      table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_update_queue.%I',
      table_name || '_truncate_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON nhi_rule_history_update_queue.%I FOR EACH STATEMENT EXECUTE FUNCTION nhi_rule_history_update_queue.reject_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$append_only_guards$;

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_item_current AS
SELECT
  item.work_item_id,
  item.rss_identity_fingerprint,
  item.item_identity_kind,
  item.item_identity_value,
  item.source_feed_url,
  item.guid_raw,
  item.first_title_raw,
  item.first_link_raw,
  item.first_observed_at,
  current_transition.transition_seq,
  current_transition.to_state AS current_state,
  current_transition.evidence_sha256,
  current_transition.evidence_json,
  current_transition.bundle_receipt_id,
  current_transition.candidate_proposal_id,
  current_transition.recorded_at AS state_recorded_at,
  current_transition.to_state IN (
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'ignored_non_rule'
  ) AS is_terminal
FROM nhi_rule_history_update_queue.rss_work_item item
JOIN LATERAL (
  SELECT transition.*
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = item.work_item_id
  ORDER BY transition.transition_seq DESC
  LIMIT 1
) current_transition ON true;

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_backlog AS
SELECT *
FROM nhi_rule_history_update_queue.v_work_item_current
WHERE current_state IN (
  'observed',
  'selected',
  'acquired',
  'corpus_registered',
  'proposal_running'
);

REVOKE ALL ON SCHEMA nhi_rule_history_update_queue FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_update_queue FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA
  nhi_rule_history_update_queue FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_update_queue
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_update_queue
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history_update_ops
  TO nhi_rule_history_update_queue_runtime;
GRANT USAGE ON TYPE nhi_rule_history_update_ops.sha256_hex
  TO nhi_rule_history_update_queue_runtime;
GRANT SELECT, INSERT ON
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.job_lease,
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.url_observation,
  nhi_rule_history_update_ops.feed_observation,
  nhi_rule_history_update_ops.feed_item_observation
  TO nhi_rule_history_update_queue_runtime;
GRANT SELECT ON nhi_rule_history_update_ops.bundle_receipt
  TO nhi_rule_history_update_queue_runtime;
GRANT USAGE ON SCHEMA nhi_rule_history_candidate_stage
  TO nhi_rule_history_update_queue_runtime;
GRANT USAGE ON TYPE nhi_rule_history_candidate_stage.sha256_hex
  TO nhi_rule_history_update_queue_runtime;
GRANT SELECT ON nhi_rule_history_candidate_stage.candidate_proposal
  TO nhi_rule_history_update_queue_runtime;
GRANT USAGE ON SCHEMA nhi_rule_history_update_queue
  TO nhi_rule_history_update_queue_runtime;
GRANT SELECT, INSERT ON
  nhi_rule_history_update_queue.rss_work_item,
  nhi_rule_history_update_queue.rss_work_observation,
  nhi_rule_history_update_queue.work_item_transition,
  nhi_rule_history_update_queue.work_item_attempt
  TO nhi_rule_history_update_queue_runtime;
GRANT SELECT ON
  nhi_rule_history_update_queue.v_work_item_current,
  nhi_rule_history_update_queue.v_work_backlog
  TO nhi_rule_history_update_queue_runtime;

INSERT INTO nhi_rule_history_update_queue.schema_migration (
  migration_id, contract_marker
) VALUES (
  '2026-07-27_nhi_rule_history_update_queue',
  'managed=nhi_rule_history_update_queue/v1'
)
ON CONFLICT (migration_id) DO NOTHING;

DO $marker_verify$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.schema_migration
    WHERE migration_id =
      '2026-07-27_nhi_rule_history_update_queue'
      AND contract_marker =
        'managed=nhi_rule_history_update_queue/v1'
  ) THEN
    RAISE EXCEPTION
      'update queue migration marker is absent or inconsistent'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$marker_verify$;

COMMENT ON TABLE nhi_rule_history_update_queue.rss_work_item IS
  'One durable work identity per exact official feed URL plus explicit RSS GUID when present, otherwise the validated official detail URL; identity kind is always explicit.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.rss_work_observation IS
  'One append-only association for each exact feed observation of a work identity.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.work_item_transition IS
  'Gap-free append-only stage workflow; terminal states reject silent retry. bundle_receipt_id is the update-stage receipt and therefore remains null before staged_*; external corpus-registration receipts belong in evidence_json.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.work_item_attempt IS
  'Append-only, non-state-changing acquisition/corpus/proposal attempt ledger. Exact API replay is keyed by a deterministic fingerprint; transient failures remain visible and retryable without rewriting workflow state.';
COMMENT ON VIEW nhi_rule_history_update_queue.v_work_backlog IS
  'Nonterminal work items only; observing or completing one item never advances its siblings.';

COMMIT;
