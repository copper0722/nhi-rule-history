-- 2026-07-28 — explicit append-only recovery generations for failed queue work
--
-- The v1 work_item_transition row that ended in failed_terminal remains
-- immutable.  Recovery is a separate, stage-only generation ledger:
-- an explicit authorization creates generation G+1 in retry_pending, and a
-- second explicit transition starts proposal work.  Nothing in this migration
-- writes legal history or publication state.

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
    WHERE nspname = 'nhi_rule_history_update_queue'
      AND obj_description(oid, 'pg_namespace') =
        'Stage-only durable per-RSS-identity work queue for the NHI rule-history updater; not legal history. managed=nhi_rule_history_update_queue/v1'
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.schema_migration
    WHERE migration_id = '2026-07-27_nhi_rule_history_update_queue'
      AND contract_marker = 'managed=nhi_rule_history_update_queue/v1'
  ) THEN
    RAISE EXCEPTION
      'managed update queue v1 is required before recovery v2'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.recovery_schema_migration (
    migration_id text PRIMARY KEY,
    contract_marker text NOT NULL UNIQUE,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT recovery_schema_migration_id_chk CHECK (
      migration_id =
        '2026-07-28_nhi_rule_history_update_queue_recovery_v2'
    ),
    CONSTRAINT recovery_schema_migration_marker_chk CHECK (
      contract_marker =
        'managed=nhi_rule_history_update_queue/recovery-v2'
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.legacy_failure_evidence (
    admission_id uuid PRIMARY KEY,
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    terminal_transition_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_update_queue.work_item_transition
        (transition_id)
      ON DELETE RESTRICT,
    terminal_evidence_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    source_bundle_uid text NOT NULL,
    source_bundle_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    source_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    worker_job_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    method_version text NOT NULL,
    semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    failure_receipt_relative_path text NOT NULL,
    failure_receipt_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    failure_receipt_json jsonb NOT NULL,
    attempts_relative_path text NOT NULL,
    attempts_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    verifier_contract_version text NOT NULL,
    verifier_code_identity
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    verifier_output_schema_version text NOT NULL,
    admission_payload_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_item_id, terminal_transition_id),
    CONSTRAINT legacy_failure_source_uid_chk
      CHECK (btrim(source_bundle_uid) <> ''),
    CONSTRAINT legacy_failure_method_chk
      CHECK (btrim(method_version) <> ''),
    CONSTRAINT legacy_failure_path_chk CHECK (
      failure_receipt_relative_path !~ '(^/|(^|/)\.\.?(/|$)|\\)'
      AND attempts_relative_path !~ '(^/|(^|/)\.\.?(/|$)|\\)'
      AND failure_receipt_relative_path ~ '(^|/)failure-receipt\.json$'
      AND attempts_relative_path ~ '(^|/)attempts\.jsonl$'
      AND regexp_replace(
        failure_receipt_relative_path,
        '/?failure-receipt\.json$',
        ''
      ) = regexp_replace(
        attempts_relative_path,
        '/?attempts\.jsonl$',
        ''
      )
      AND regexp_replace(
        failure_receipt_relative_path,
        '/?failure-receipt\.json$',
        ''
      ) ~ ('(^|/)' || worker_job_fingerprint::text || '$')
    ),
    CONSTRAINT legacy_failure_receipt_object_chk
      CHECK (jsonb_typeof(failure_receipt_json) = 'object'),
    CONSTRAINT legacy_failure_verifier_contract_chk CHECK (
      btrim(verifier_contract_version) <> ''
      AND btrim(verifier_output_schema_version) <> ''
    ),
    CONSTRAINT legacy_failure_actor_chk
      CHECK (btrim(admitted_by) <> '')
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence (
    admission_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.legacy_failure_evidence
        (admission_id)
      ON DELETE RESTRICT,
    route text NOT NULL CHECK (route IN ('primary', 'fallback')),
    attempt_id nhi_rule_history_update_ops.sha256_hex NOT NULL UNIQUE,
    attempt_id_scheme text NOT NULL DEFAULT 'sha256_hex_v1',
    attempt_id_origin text NOT NULL DEFAULT
      'immutable_worker_attempt_jsonl',
    attempt_record_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    status text NOT NULL CHECK (
      status IN (
        'execution_failed',
        'contract_failed',
        'timeout',
        'transport_failed'
      )
    ),
    worker_id text NOT NULL CHECK (btrim(worker_id) <> ''),
    prompt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    primary_attempt_id nhi_rule_history_update_ops.sha256_hex,
    attempt_record_json jsonb NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (admission_id, route),
    CONSTRAINT legacy_failure_attempt_record_chk
      CHECK (jsonb_typeof(attempt_record_json) = 'object'),
    CONSTRAINT legacy_failure_attempt_identity_scheme_chk CHECK (
      attempt_id_scheme = 'sha256_hex_v1'
      AND attempt_id_origin = 'immutable_worker_attempt_jsonl'
    ),
    CONSTRAINT legacy_failure_attempt_lineage_chk CHECK (
      (route = 'primary' AND primary_attempt_id IS NULL)
      OR (
        route = 'fallback'
        AND primary_attempt_id IS NOT NULL
        AND primary_attempt_id <> attempt_id
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.work_recovery_authorization (
    authorization_id uuid PRIMARY KEY,
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    prior_generation integer NOT NULL CHECK (prior_generation >= 1),
    new_generation integer NOT NULL CHECK (new_generation >= 2),
    source_bundle_uid text NOT NULL,
    source_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prior_method_version text NOT NULL,
    new_method_version text NOT NULL,
    prior_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    new_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    decision_basis_id text NOT NULL,
    reason text NOT NULL,
    route text NOT NULL,
    legacy_failure_admission_id uuid
      REFERENCES nhi_rule_history_update_queue.legacy_failure_evidence
        (admission_id)
      ON DELETE RESTRICT,
    authorized_by text NOT NULL,
    authorized_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_item_id, new_generation),
    UNIQUE (work_item_id, decision_basis_id),
    UNIQUE (legacy_failure_admission_id),
    UNIQUE (
      work_item_id,
      source_bundle_uid,
      source_manifest_sha256,
      new_method_version,
      new_semantic_prompt_fingerprint
    ),
    CONSTRAINT work_recovery_generation_chk
      CHECK (new_generation = prior_generation + 1),
    CONSTRAINT work_recovery_source_uid_chk
      CHECK (btrim(source_bundle_uid) <> ''),
    CONSTRAINT work_recovery_method_chk CHECK (
      btrim(prior_method_version) <> ''
      AND btrim(new_method_version) <> ''
      AND (
        prior_method_version <> new_method_version
        OR prior_semantic_prompt_fingerprint <>
          new_semantic_prompt_fingerprint
      )
    ),
    CONSTRAINT work_recovery_decision_chk CHECK (
      btrim(decision_basis_id) <> ''
      AND btrim(reason) <> ''
      AND btrim(authorized_by) <> ''
    ),
    CONSTRAINT work_recovery_route_chk
      CHECK (route = 'primary_then_fallback'),
    CONSTRAINT work_recovery_legacy_generation_chk CHECK (
      legacy_failure_admission_id IS NULL
      OR (prior_generation = 1 AND new_generation = 2)
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.recovery_superseded_attempt (
    authorization_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_update_queue.work_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    attempt_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_update_ops.worker_attempt (attempt_id)
      ON DELETE RESTRICT,
    route text NOT NULL CHECK (route IN ('primary', 'fallback')),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (authorization_id, attempt_id),
    UNIQUE (authorization_id, route)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.work_generation (
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    generation integer NOT NULL CHECK (generation >= 2),
    authorization_id uuid NOT NULL UNIQUE
      REFERENCES
        nhi_rule_history_update_queue.work_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, generation)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.work_generation_transition (
    work_item_id uuid NOT NULL,
    generation integer NOT NULL,
    transition_seq integer NOT NULL CHECK (transition_seq > 0),
    transition_id uuid NOT NULL UNIQUE,
    from_state text,
    to_state text NOT NULL,
    actor_kind text NOT NULL CHECK (btrim(actor_kind) <> ''),
    source_job_id uuid
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
    PRIMARY KEY (work_item_id, generation, transition_seq),
    FOREIGN KEY (work_item_id, generation)
      REFERENCES nhi_rule_history_update_queue.work_generation
        (work_item_id, generation)
      ON DELETE RESTRICT,
    CONSTRAINT work_generation_transition_from_chk CHECK (
      from_state IS NULL
      OR from_state IN (
        'retry_pending',
        'proposal_running',
        'staged_needs_review',
        'staged_pending_anchor',
        'failed_terminal',
        'partition_required'
      )
    ),
    CONSTRAINT work_generation_transition_to_chk CHECK (
      to_state IN (
        'retry_pending',
        'proposal_running',
        'staged_needs_review',
        'staged_pending_anchor',
        'failed_terminal',
        'partition_required'
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.recovery_route_attempt (
    work_item_id uuid NOT NULL,
    generation integer NOT NULL,
    route text NOT NULL CHECK (route IN ('primary', 'fallback')),
    attempt_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_update_ops.worker_attempt (attempt_id)
      ON DELETE RESTRICT,
    source_job_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    method_version text NOT NULL CHECK (btrim(method_version) <> ''),
    semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    recorded_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, generation, route),
    FOREIGN KEY (work_item_id, generation)
      REFERENCES nhi_rule_history_update_queue.work_generation
        (work_item_id, generation)
      ON DELETE RESTRICT
  );

ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  DROP CONSTRAINT IF EXISTS work_item_transition_from_state_chk;
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  ADD CONSTRAINT work_item_transition_from_state_chk CHECK (
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
      'partition_required',
      'ignored_non_rule'
    )
  );
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  DROP CONSTRAINT IF EXISTS work_item_transition_to_state_chk;
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  ADD CONSTRAINT work_item_transition_to_state_chk CHECK (
    to_state IN (
      'observed',
      'selected',
      'acquired',
      'corpus_registered',
      'proposal_running',
      'staged_needs_review',
      'staged_pending_anchor',
      'failed_terminal',
      'partition_required',
      'ignored_non_rule'
    )
  );

CREATE INDEX IF NOT EXISTS work_recovery_authorization_item_idx
  ON nhi_rule_history_update_queue.work_recovery_authorization
    (work_item_id, new_generation);

CREATE INDEX IF NOT EXISTS legacy_failure_evidence_item_idx
  ON nhi_rule_history_update_queue.legacy_failure_evidence
    (work_item_id, terminal_transition_id);

CREATE INDEX IF NOT EXISTS work_generation_transition_state_idx
  ON nhi_rule_history_update_queue.work_generation_transition
    (to_state, recorded_at, work_item_id, generation);

CREATE INDEX IF NOT EXISTS recovery_route_attempt_job_idx
  ON nhi_rule_history_update_queue.recovery_route_attempt
    (source_job_id, attempt_id);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_work_generation_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  auth_item uuid;
  auth_generation integer;
  auth_prior_generation integer;
  latest_generation integer;
  latest_state text;
  legacy_state text;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  SELECT
    auth.work_item_id,
    auth.new_generation,
    auth.prior_generation
  INTO auth_item, auth_generation, auth_prior_generation
  FROM
    nhi_rule_history_update_queue.work_recovery_authorization auth
  WHERE auth.authorization_id = NEW.authorization_id;

  IF NOT FOUND
     OR auth_item IS DISTINCT FROM NEW.work_item_id
     OR auth_generation IS DISTINCT FROM NEW.generation THEN
    RAISE EXCEPTION
      'work generation does not match its recovery authorization'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT generation INTO latest_generation
  FROM nhi_rule_history_update_queue.work_generation
  WHERE work_item_id = NEW.work_item_id
  ORDER BY generation DESC
  LIMIT 1;

  IF NOT FOUND THEN
    SELECT current_state INTO legacy_state
    FROM nhi_rule_history_update_queue.v_work_item_current
    WHERE work_item_id = NEW.work_item_id;
    IF legacy_state IS DISTINCT FROM 'failed_terminal'
       OR auth_prior_generation <> 1
       OR NEW.generation <> 2 THEN
      RAISE EXCEPTION
        'first recovery generation requires immutable failed_terminal generation 1'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    SELECT transition.to_state INTO latest_state
    FROM nhi_rule_history_update_queue.work_generation_transition transition
    WHERE transition.work_item_id = NEW.work_item_id
      AND transition.generation = latest_generation
    ORDER BY transition.transition_seq DESC
    LIMIT 1;
    IF latest_state IS DISTINCT FROM 'failed_terminal'
       OR auth_prior_generation <> latest_generation
       OR NEW.generation <> latest_generation + 1 THEN
      RAISE EXCEPTION
        'one active recovery generation is allowed and only failed generations may be explicitly retried'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_generation_transition_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  prior_seq integer;
  prior_state text;
  prior_recorded_at timestamptz;
  prior_source_job_id uuid;
  candidate_bundle_id uuid;
  successful_routes integer;
  failed_routes integer;
  route_count integer;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_generation generation
    WHERE generation.work_item_id = NEW.work_item_id
      AND generation.generation = NEW.generation
  ) THEN
    RAISE EXCEPTION
      'recovery transition requires an authorized generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT transition_seq, to_state, recorded_at, source_job_id
  INTO
    prior_seq,
    prior_state,
    prior_recorded_at,
    prior_source_job_id
  FROM nhi_rule_history_update_queue.work_generation_transition
  WHERE work_item_id = NEW.work_item_id
    AND generation = NEW.generation
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF NOT FOUND THEN
    IF NEW.transition_seq <> 1
       OR NEW.from_state IS NOT NULL
       OR NEW.to_state <> 'retry_pending'
       OR NEW.source_job_id IS NOT NULL
       OR NEW.bundle_receipt_id IS NOT NULL
       OR NEW.candidate_proposal_id IS NOT NULL THEN
      RAISE EXCEPTION
        'first recovery transition must be seq 1: NULL -> retry_pending'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    RETURN NEW;
  END IF;

  IF prior_state IN (
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'partition_required'
  ) THEN
    RAISE EXCEPTION
      'terminal recovery generation rejects silent retry'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF NEW.transition_seq <> prior_seq + 1
     OR NEW.from_state IS DISTINCT FROM prior_state
     OR NEW.recorded_at < prior_recorded_at THEN
    RAISE EXCEPTION
      'recovery transition sequence/state/time is not gap-free'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF NOT (
    (
      prior_state = 'retry_pending'
      AND NEW.to_state IN ('proposal_running', 'partition_required')
    )
    OR (
      prior_state = 'proposal_running'
      AND NEW.to_state IN (
        'staged_needs_review',
        'staged_pending_anchor',
        'failed_terminal',
        'partition_required'
      )
    )
  ) THEN
    RAISE EXCEPTION
      'recovery transition edge is not allowed'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NEW.to_state = 'proposal_running' THEN
    IF NEW.source_job_id IS NULL
       OR NEW.bundle_receipt_id IS NOT NULL
       OR NEW.candidate_proposal_id IS NOT NULL THEN
      RAISE EXCEPTION
        'proposal_running recovery requires only its execution job'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSIF NEW.to_state = 'partition_required' THEN
    IF NEW.bundle_receipt_id IS NOT NULL
       OR NEW.candidate_proposal_id IS NOT NULL THEN
      RAISE EXCEPTION
        'partition_required is a terminal fail-closed state without candidate claims'
      USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  IF prior_state = 'proposal_running'
     AND NEW.source_job_id IS DISTINCT FROM prior_source_job_id THEN
    RAISE EXCEPTION
      'terminal recovery transition must retain the execution job identity'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF prior_state = 'retry_pending'
     AND NEW.to_state = 'partition_required'
     AND NEW.source_job_id IS NOT NULL THEN
    RAISE EXCEPTION
      'pre-execution partition_required cannot claim an execution job'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT
    count(*),
    count(*) FILTER (WHERE attempt.status = 'success'),
    count(*) FILTER (WHERE attempt.status = 'failed')
  INTO route_count, successful_routes, failed_routes
  FROM nhi_rule_history_update_queue.recovery_route_attempt linked
  JOIN nhi_rule_history_update_ops.worker_attempt attempt
    ON attempt.attempt_id = linked.attempt_id
  WHERE linked.work_item_id = NEW.work_item_id
    AND linked.generation = NEW.generation;

  IF NEW.to_state IN (
    'staged_needs_review', 'staged_pending_anchor'
  ) THEN
    IF successful_routes <> 1
       OR NEW.bundle_receipt_id IS NULL
       OR NEW.candidate_proposal_id IS NULL THEN
      RAISE EXCEPTION
        'staged recovery requires one successful route and matching candidate identifiers'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT bundle_receipt_id INTO candidate_bundle_id
    FROM nhi_rule_history_candidate_stage.candidate_proposal
    WHERE proposal_id = NEW.candidate_proposal_id;
    IF NOT FOUND
       OR candidate_bundle_id IS DISTINCT FROM NEW.bundle_receipt_id THEN
      RAISE EXCEPTION
        'staged recovery candidate does not belong to its bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSIF NEW.to_state = 'failed_terminal' THEN
    IF route_count <> 2
       OR failed_routes <> 2
       OR NEW.bundle_receipt_id IS NOT NULL
       OR NEW.candidate_proposal_id IS NOT NULL THEN
      RAISE EXCEPTION
        'failed recovery requires one failed primary and one failed fallback'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_recovery_route_attempt_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  current_generation integer;
  current_state text;
  current_recorded_at timestamptz;
  execution_job_id uuid;
  expected_method text;
  expected_prompt
    nhi_rule_history_update_ops.sha256_hex;
  attempt_job_id uuid;
  attempt_lane text;
  attempt_no smallint;
  attempt_primary_id uuid;
  attempt_status text;
  attempt_started_at timestamptz;
  attempt_completed_at timestamptz;
  linked_primary_id uuid;
  linked_primary_status text;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  SELECT max(generation) INTO current_generation
  FROM nhi_rule_history_update_queue.work_generation
  WHERE work_item_id = NEW.work_item_id;
  IF current_generation IS DISTINCT FROM NEW.generation THEN
    RAISE EXCEPTION
      'route attempt must belong to the one active recovery generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT
    transition.to_state,
    transition.recorded_at,
    transition.source_job_id
  INTO current_state, current_recorded_at, execution_job_id
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = NEW.work_item_id
    AND transition.generation = NEW.generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF current_state IS DISTINCT FROM 'proposal_running'
     OR execution_job_id IS NULL THEN
    RAISE EXCEPTION
      'route attempts require an active proposal_running generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT
    auth.new_method_version,
    auth.new_semantic_prompt_fingerprint
  INTO expected_method, expected_prompt
  FROM nhi_rule_history_update_queue.work_generation generation
  JOIN
    nhi_rule_history_update_queue.work_recovery_authorization auth
    ON auth.authorization_id = generation.authorization_id
  WHERE generation.work_item_id = NEW.work_item_id
    AND generation.generation = NEW.generation;

  SELECT
    attempt.job_id,
    attempt.lane,
    attempt.attempt_no,
    attempt.primary_attempt_id,
    attempt.status,
    attempt.started_at,
    attempt.completed_at
  INTO
    attempt_job_id,
    attempt_lane,
    attempt_no,
    attempt_primary_id,
    attempt_status,
    attempt_started_at,
    attempt_completed_at
  FROM nhi_rule_history_update_ops.worker_attempt attempt
  WHERE attempt.attempt_id = NEW.attempt_id;

  IF NOT FOUND
     OR attempt_job_id IS DISTINCT FROM execution_job_id
     OR NEW.source_job_id IS DISTINCT FROM execution_job_id
     OR attempt_lane IS DISTINCT FROM NEW.route
     OR NEW.method_version IS DISTINCT FROM expected_method
     OR NEW.semantic_prompt_fingerprint IS DISTINCT FROM expected_prompt
     OR attempt_started_at < current_recorded_at
     OR NEW.recorded_at < attempt_completed_at THEN
    RAISE EXCEPTION
      'route attempt does not match its generation, method, prompt, job, or time'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.recovery_route_attempt linked
    JOIN nhi_rule_history_update_ops.worker_attempt prior
      ON prior.attempt_id = linked.attempt_id
    WHERE linked.work_item_id = NEW.work_item_id
      AND linked.generation = NEW.generation
      AND prior.status = 'success'
  ) THEN
    RAISE EXCEPTION
      'a successful recovery route rejects further model attempts'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NEW.route = 'primary' THEN
    IF attempt_no <> 1 OR attempt_primary_id IS NOT NULL THEN
      RAISE EXCEPTION
        'primary recovery route must be worker attempt 1'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSE
    SELECT linked.attempt_id, attempt.status
    INTO linked_primary_id, linked_primary_status
    FROM nhi_rule_history_update_queue.recovery_route_attempt linked
    JOIN nhi_rule_history_update_ops.worker_attempt attempt
      ON attempt.attempt_id = linked.attempt_id
    WHERE linked.work_item_id = NEW.work_item_id
      AND linked.generation = NEW.generation
      AND linked.route = 'primary';
    IF NOT FOUND
       OR linked_primary_status IS DISTINCT FROM 'failed'
       OR attempt_no <> 2
       OR attempt_primary_id IS DISTINCT FROM linked_primary_id THEN
      RAISE EXCEPTION
        'fallback recovery route requires the failed linked primary'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

-- Harden the generic v1 transition path.  Recovery-only state names and every
-- failed_terminal origin must use the dedicated generation functions below.
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

  IF NEW.from_state IN ('failed_terminal', 'partition_required')
     OR NEW.to_state = 'retry_pending' THEN
    RAISE EXCEPTION
      'generic transition path cannot recover failed work; use an authorized generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

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
      'partition_required',
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
          'failed_terminal',
          'partition_required'
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
    'partition_required',
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
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    p_admission_id uuid,
    p_work_item_id uuid,
    p_terminal_transition_id uuid,
    p_source_bundle_uid text,
    p_source_bundle_fingerprint
      nhi_rule_history_update_ops.sha256_hex,
    p_source_manifest_sha256 nhi_rule_history_update_ops.sha256_hex,
    p_worker_job_fingerprint nhi_rule_history_update_ops.sha256_hex,
    p_method_version text,
    p_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex,
    p_failure_receipt_relative_path text,
    p_failure_receipt_sha256 nhi_rule_history_update_ops.sha256_hex,
    p_failure_receipt_json jsonb,
    p_attempts_relative_path text,
    p_attempts_sha256 nhi_rule_history_update_ops.sha256_hex,
    p_attempt_records jsonb,
    p_attempt_record_sha256s text[],
    p_verifier_contract_version text,
    p_verifier_code_identity nhi_rule_history_update_ops.sha256_hex,
    p_verifier_output_schema_version text,
    p_admission_payload_sha256
      nhi_rule_history_update_ops.sha256_hex,
    p_actor_kind text,
    p_admitted_at timestamptz
  )
RETURNS TABLE (
  admission_id uuid,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  terminal
    nhi_rule_history_update_queue.work_item_transition%ROWTYPE;
  existing
    nhi_rule_history_update_queue.legacy_failure_evidence%ROWTYPE;
  primary_record jsonb;
  fallback_record jsonb;
  expected_terminal_attempts jsonb;
  existing_attempt_count integer;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );

  IF coalesce(btrim(p_source_bundle_uid), '') = ''
     OR coalesce(btrim(p_method_version), '') = ''
     OR coalesce(btrim(p_actor_kind), '') = ''
     OR p_source_bundle_fingerprint IS NULL
     OR p_source_manifest_sha256 IS NULL
     OR p_worker_job_fingerprint IS NULL
     OR p_semantic_prompt_fingerprint IS NULL
     OR p_failure_receipt_sha256 IS NULL
     OR p_attempts_sha256 IS NULL
     OR coalesce(btrim(p_verifier_contract_version), '') = ''
     OR p_verifier_code_identity IS NULL
     OR coalesce(btrim(p_verifier_output_schema_version), '') = ''
     OR p_admission_payload_sha256 IS NULL
     OR p_failure_receipt_relative_path ~ '(^/|(^|/)\.\.?(/|$)|\\)'
     OR p_attempts_relative_path ~ '(^/|(^|/)\.\.?(/|$)|\\)'
     OR p_failure_receipt_relative_path !~
        '(^|/)failure-receipt\.json$'
     OR p_attempts_relative_path !~ '(^|/)attempts\.jsonl$'
     OR regexp_replace(
          p_failure_receipt_relative_path,
          '/?failure-receipt\.json$',
          ''
        ) <> regexp_replace(
          p_attempts_relative_path,
          '/?attempts\.jsonl$',
          ''
        ) THEN
    RAISE EXCEPTION
      'legacy failure admission identity, actor, or relative paths are invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF jsonb_typeof(p_failure_receipt_json) IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_attempt_records) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION
      'legacy failure receipt and attempt records must be JSON objects/array'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF jsonb_array_length(p_attempt_records) IS DISTINCT FROM 2
     OR cardinality(p_attempt_record_sha256s) IS DISTINCT FROM 2
     OR EXISTS (
       SELECT 1
       FROM unnest(p_attempt_record_sha256s) record_sha
       WHERE record_sha !~ '^[0-9a-f]{64}$'
     ) THEN
    RAISE EXCEPTION
      'legacy failure evidence requires exactly two hashed attempt records'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  primary_record := p_attempt_records -> 0;
  fallback_record := p_attempt_records -> 1;
  IF jsonb_typeof(primary_record) IS DISTINCT FROM 'object'
     OR jsonb_typeof(fallback_record) IS DISTINCT FROM 'object'
     OR primary_record ->> 'schema' IS DISTINCT FROM
        'nhi-rule-history/worker-attempt/v1'
     OR fallback_record ->> 'schema' IS DISTINCT FROM
        'nhi-rule-history/worker-attempt/v1'
     OR primary_record ->> 'role' IS DISTINCT FROM 'primary'
     OR fallback_record ->> 'role' IS DISTINCT FROM 'fallback'
     OR coalesce(primary_record ->> 'attempt_id', '') !~
        '^[0-9a-f]{64}$'
     OR coalesce(fallback_record ->> 'attempt_id', '') !~
        '^[0-9a-f]{64}$'
     OR primary_record ->> 'attempt_id' IS NOT DISTINCT FROM
        fallback_record ->> 'attempt_id'
     OR primary_record -> 'primary_attempt_id' IS DISTINCT FROM
        'null'::jsonb
     OR fallback_record ->> 'primary_attempt_id' IS DISTINCT FROM
        primary_record ->> 'attempt_id'
     OR coalesce(primary_record ->> 'status', '') NOT IN (
       'execution_failed',
       'contract_failed',
       'timeout',
       'transport_failed'
     )
     OR coalesce(fallback_record ->> 'status', '') NOT IN (
       'execution_failed',
       'contract_failed',
       'timeout',
       'transport_failed'
     )
     OR coalesce(btrim(primary_record ->> 'worker_id'), '') = ''
     OR coalesce(btrim(fallback_record ->> 'worker_id'), '') = ''
     OR primary_record ->> 'prompt_version' IS DISTINCT FROM
        p_method_version
     OR fallback_record ->> 'prompt_version' IS DISTINCT FROM
        p_method_version
     OR primary_record ->> 'prompt_sha256' IS DISTINCT FROM
        p_semantic_prompt_fingerprint::text
     OR fallback_record ->> 'prompt_sha256' IS DISTINCT FROM
        p_semantic_prompt_fingerprint::text
     OR coalesce(btrim(fallback_record ->> 'fallback_reason'), '') = ''
     OR fallback_record ->> 'fallback_reason' IS DISTINCT FROM
        primary_record ->> 'status' THEN
    RAISE EXCEPTION
      'legacy failure attempts are not one exact failed primary/fallback lineage'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF p_failure_receipt_json ->> 'schema' IS DISTINCT FROM
       'nhi-rule-history/worker-run/v2'
     OR p_failure_receipt_json ->> 'status' IS DISTINCT FROM 'failed'
     OR p_failure_receipt_json ->> 'bundle_id' IS DISTINCT FROM
        p_source_bundle_uid
     OR p_failure_receipt_json ->> 'bundle_fingerprint'
        IS DISTINCT FROM
        p_source_bundle_fingerprint::text
     OR p_failure_receipt_json ->> 'manifest_sha256' IS DISTINCT FROM
        p_source_manifest_sha256::text
     OR p_failure_receipt_json ->> 'job_fingerprint' IS DISTINCT FROM
        p_worker_job_fingerprint::text
     OR p_failure_receipt_json ->> 'prompt_sha256' IS DISTINCT FROM
        p_semantic_prompt_fingerprint::text
     OR p_failure_receipt_json ->> 'attempts_sha256' IS DISTINCT FROM
        p_attempts_sha256::text
     OR p_failure_receipt_json ->> 'attempt_count' IS DISTINCT FROM '2'
     OR p_failure_receipt_json -> 'selected_attempt_id' IS DISTINCT FROM
        'null'::jsonb
     OR regexp_replace(
          p_failure_receipt_relative_path,
          '/?failure-receipt\.json$',
          ''
        ) !~ ('(^|/)' || p_worker_job_fingerprint::text || '$') THEN
    RAISE EXCEPTION
      'legacy failure receipt does not bind the supplied source and attempts'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT transition.* INTO terminal
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = p_work_item_id
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF NOT FOUND
     OR terminal.transition_id IS DISTINCT FROM p_terminal_transition_id
     OR terminal.to_state <> 'failed_terminal' THEN
    RAISE EXCEPTION
      'legacy failure evidence must bind the current immutable failed_terminal transition'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  expected_terminal_attempts := jsonb_build_array(
    jsonb_build_object(
      'role', 'primary',
      'status', primary_record ->> 'status',
      'worker_id', primary_record ->> 'worker_id',
      'attempt_id', primary_record ->> 'attempt_id'
    ),
    jsonb_build_object(
      'role', 'fallback',
      'status', fallback_record ->> 'status',
      'worker_id', fallback_record ->> 'worker_id',
      'attempt_id', fallback_record ->> 'attempt_id'
    )
  );
  IF terminal.evidence_json ->> 'failure_receipt_relative_path'
       IS DISTINCT FROM
       p_failure_receipt_relative_path
     OR terminal.evidence_json ->> 'failure_receipt_sha256'
        IS DISTINCT FROM
        p_failure_receipt_sha256::text
     OR terminal.evidence_json -> 'worker_attempts' IS DISTINCT FROM
        expected_terminal_attempts THEN
    RAISE EXCEPTION
      'legacy failure files do not exactly match terminal controller evidence'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT * INTO existing
  FROM nhi_rule_history_update_queue.legacy_failure_evidence evidence
  WHERE evidence.admission_id = p_admission_id;
  IF FOUND THEN
    SELECT count(*) INTO existing_attempt_count
    FROM nhi_rule_history_update_queue.legacy_failure_attempt_evidence attempt
    WHERE attempt.admission_id = p_admission_id
      AND (
        (
          attempt.route = 'primary'
          AND attempt.attempt_id::text =
            primary_record ->> 'attempt_id'
          AND attempt.attempt_record_sha256::text =
            p_attempt_record_sha256s[1]
          AND attempt.attempt_record_json = primary_record
        )
        OR (
          attempt.route = 'fallback'
          AND attempt.attempt_id::text =
            fallback_record ->> 'attempt_id'
          AND attempt.attempt_record_sha256::text =
            p_attempt_record_sha256s[2]
          AND attempt.attempt_record_json = fallback_record
        )
      );
    IF existing.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing.terminal_transition_id IS DISTINCT FROM
          p_terminal_transition_id
       OR existing.source_bundle_uid <> p_source_bundle_uid
       OR existing.source_bundle_fingerprint <>
          p_source_bundle_fingerprint
       OR existing.source_manifest_sha256 <> p_source_manifest_sha256
       OR existing.worker_job_fingerprint <> p_worker_job_fingerprint
       OR existing.method_version <> p_method_version
       OR existing.semantic_prompt_fingerprint <>
          p_semantic_prompt_fingerprint
       OR existing.failure_receipt_relative_path <>
          p_failure_receipt_relative_path
       OR existing.failure_receipt_sha256 <> p_failure_receipt_sha256
       OR existing.failure_receipt_json <> p_failure_receipt_json
       OR existing.attempts_relative_path <> p_attempts_relative_path
       OR existing.attempts_sha256 <> p_attempts_sha256
       OR existing.verifier_contract_version <>
          p_verifier_contract_version
       OR existing.verifier_code_identity <> p_verifier_code_identity
       OR existing.verifier_output_schema_version <>
          p_verifier_output_schema_version
       OR existing.admission_payload_sha256 <>
          p_admission_payload_sha256
       OR existing.admitted_by <> p_actor_kind
       OR existing_attempt_count <> 2 THEN
      RAISE EXCEPTION
        'legacy failure admission identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT p_admission_id, true;
    RETURN;
  END IF;

  INSERT INTO nhi_rule_history_update_queue.legacy_failure_evidence (
    admission_id,
    work_item_id,
    terminal_transition_id,
    terminal_evidence_sha256,
    source_bundle_uid,
    source_bundle_fingerprint,
    source_manifest_sha256,
    worker_job_fingerprint,
    method_version,
    semantic_prompt_fingerprint,
    failure_receipt_relative_path,
    failure_receipt_sha256,
    failure_receipt_json,
    attempts_relative_path,
    attempts_sha256,
    verifier_contract_version,
    verifier_code_identity,
    verifier_output_schema_version,
    admission_payload_sha256,
    admitted_by,
    admitted_at
  ) VALUES (
    p_admission_id,
    p_work_item_id,
    p_terminal_transition_id,
    terminal.evidence_sha256,
    p_source_bundle_uid,
    p_source_bundle_fingerprint,
    p_source_manifest_sha256,
    p_worker_job_fingerprint,
    p_method_version,
    p_semantic_prompt_fingerprint,
    p_failure_receipt_relative_path,
    p_failure_receipt_sha256,
    p_failure_receipt_json,
    p_attempts_relative_path,
    p_attempts_sha256,
    p_verifier_contract_version,
    p_verifier_code_identity,
    p_verifier_output_schema_version,
    p_admission_payload_sha256,
    p_actor_kind,
    p_admitted_at
  );

  INSERT INTO
    nhi_rule_history_update_queue.legacy_failure_attempt_evidence (
      admission_id,
      route,
      attempt_id,
      attempt_id_scheme,
      attempt_id_origin,
      attempt_record_sha256,
      status,
      worker_id,
      prompt_sha256,
      primary_attempt_id,
      attempt_record_json,
      recorded_at
    )
  VALUES
    (
      p_admission_id,
      'primary',
      (primary_record ->> 'attempt_id')::text,
      'sha256_hex_v1',
      'immutable_worker_attempt_jsonl',
      p_attempt_record_sha256s[1],
      primary_record ->> 'status',
      primary_record ->> 'worker_id',
      p_semantic_prompt_fingerprint,
      NULL,
      primary_record,
      p_admitted_at
    ),
    (
      p_admission_id,
      'fallback',
      (fallback_record ->> 'attempt_id')::text,
      'sha256_hex_v1',
      'immutable_worker_attempt_jsonl',
      p_attempt_record_sha256s[2],
      fallback_record ->> 'status',
      fallback_record ->> 'worker_id',
      p_semantic_prompt_fingerprint,
      (primary_record ->> 'attempt_id')::text,
      fallback_record,
      p_admitted_at
    );

  RETURN QUERY SELECT p_admission_id, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    p_authorization_id uuid,
    p_initial_transition_id uuid,
    p_work_item_id uuid,
    p_prior_generation integer,
    p_new_generation integer,
    p_source_bundle_uid text,
    p_source_manifest_sha256 nhi_rule_history_update_ops.sha256_hex,
    p_prior_method_version text,
    p_new_method_version text,
    p_prior_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex,
    p_new_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex,
    p_superseded_attempt_ids uuid[],
    p_decision_basis_id text,
    p_reason text,
    p_route text,
    p_actor_kind text,
    p_authorized_at timestamptz
  )
RETURNS TABLE (
  authorization_id uuid,
  generation integer,
  transition_id uuid,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  current_legacy_state text;
  latest_generation integer;
  latest_generation_state text;
  latest_source_uid text;
  latest_manifest
    nhi_rule_history_update_ops.sha256_hex;
  latest_method text;
  latest_prompt
    nhi_rule_history_update_ops.sha256_hex;
  requested_count integer;
  found_count integer;
  failed_count integer;
  job_count integer;
  lane_count integer;
  existing_initial_transition uuid;
  existing_auth
    nhi_rule_history_update_queue.work_recovery_authorization%ROWTYPE;
  prior_linked_attempts uuid[];
  normalized_requested uuid[];
  attempt_row record;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );

  IF p_prior_generation < 1
     OR p_new_generation <> p_prior_generation + 1
     OR btrim(p_source_bundle_uid) = ''
     OR btrim(p_prior_method_version) = ''
     OR btrim(p_new_method_version) = ''
     OR btrim(p_decision_basis_id) = ''
     OR btrim(p_reason) = ''
     OR btrim(p_actor_kind) = ''
     OR p_route <> 'primary_then_fallback'
     OR (
       p_prior_method_version = p_new_method_version
       AND p_prior_semantic_prompt_fingerprint =
         p_new_semantic_prompt_fingerprint
     ) THEN
    RAISE EXCEPTION
      'recovery authorization fields are invalid or method/prompt is unchanged'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT array_agg(DISTINCT requested ORDER BY requested)
  INTO normalized_requested
  FROM unnest(p_superseded_attempt_ids) requested;
  requested_count := coalesce(cardinality(p_superseded_attempt_ids), 0);
  IF requested_count < 1
     OR requested_count > 2
     OR cardinality(normalized_requested) <> requested_count
     OR array_position(p_superseded_attempt_ids, NULL) IS NOT NULL THEN
    RAISE EXCEPTION
      'one or two distinct superseded attempt IDs are required'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing_auth
  FROM nhi_rule_history_update_queue.work_recovery_authorization auth
  WHERE auth.authorization_id = p_authorization_id;
  IF FOUND THEN
    SELECT array_agg(linked.attempt_id ORDER BY linked.attempt_id)
    INTO prior_linked_attempts
    FROM nhi_rule_history_update_queue.recovery_superseded_attempt linked
    WHERE linked.authorization_id = p_authorization_id;
    SELECT transition.transition_id
    INTO existing_initial_transition
    FROM nhi_rule_history_update_queue.work_generation_transition transition
    WHERE transition.work_item_id = p_work_item_id
      AND transition.generation = p_new_generation
      AND transition.transition_seq = 1;
    IF existing_auth.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing_auth.prior_generation <> p_prior_generation
       OR existing_auth.new_generation <> p_new_generation
       OR existing_auth.source_bundle_uid <> p_source_bundle_uid
       OR existing_auth.source_manifest_sha256 <>
          p_source_manifest_sha256
       OR existing_auth.prior_method_version <> p_prior_method_version
       OR existing_auth.new_method_version <> p_new_method_version
       OR existing_auth.prior_semantic_prompt_fingerprint <>
          p_prior_semantic_prompt_fingerprint
       OR existing_auth.new_semantic_prompt_fingerprint <>
          p_new_semantic_prompt_fingerprint
       OR existing_auth.decision_basis_id <> p_decision_basis_id
       OR existing_auth.reason <> p_reason
       OR existing_auth.route <> p_route
       OR existing_auth.legacy_failure_admission_id IS NOT NULL
       OR existing_auth.authorized_by <> p_actor_kind
       OR prior_linked_attempts IS DISTINCT FROM normalized_requested
       OR existing_initial_transition IS DISTINCT FROM
          p_initial_transition_id THEN
      RAISE EXCEPTION
        'recovery authorization identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      p_authorization_id, p_new_generation, p_initial_transition_id, true;
    RETURN;
  END IF;

  SELECT current_state INTO current_legacy_state
  FROM nhi_rule_history_update_queue.v_work_item_current
  WHERE work_item_id = p_work_item_id;
  IF current_legacy_state IS DISTINCT FROM 'failed_terminal' THEN
    RAISE EXCEPTION
      'recovery authorization requires immutable failed_terminal work'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT max(prior_generation_row.generation) INTO latest_generation
  FROM
    nhi_rule_history_update_queue.work_generation prior_generation_row
  WHERE prior_generation_row.work_item_id = p_work_item_id;
  IF latest_generation IS NULL THEN
    IF p_prior_generation <> 1 OR p_new_generation <> 2 THEN
      RAISE EXCEPTION
        'first recovery must advance implicit generation 1 to generation 2'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    SELECT
      transition.to_state,
      auth.source_bundle_uid,
      auth.source_manifest_sha256,
      auth.new_method_version,
      auth.new_semantic_prompt_fingerprint
    INTO
      latest_generation_state,
      latest_source_uid,
      latest_manifest,
      latest_method,
      latest_prompt
    FROM nhi_rule_history_update_queue.work_generation generation
    JOIN
      nhi_rule_history_update_queue.work_recovery_authorization auth
      ON auth.authorization_id = generation.authorization_id
    JOIN LATERAL (
      SELECT current_transition.to_state
      FROM
        nhi_rule_history_update_queue.work_generation_transition
          current_transition
      WHERE current_transition.work_item_id = generation.work_item_id
        AND current_transition.generation = generation.generation
      ORDER BY current_transition.transition_seq DESC
      LIMIT 1
    ) transition ON true
    WHERE generation.work_item_id = p_work_item_id
      AND generation.generation = latest_generation;
    IF latest_generation_state IS DISTINCT FROM 'failed_terminal' THEN
      RAISE EXCEPTION
        'one active recovery generation is allowed; explicit retry requires a failed prior generation'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF p_prior_generation <> latest_generation
       OR p_new_generation <> latest_generation + 1
       OR latest_source_uid <> p_source_bundle_uid
       OR latest_manifest <> p_source_manifest_sha256
       OR latest_method <> p_prior_method_version
       OR latest_prompt <> p_prior_semantic_prompt_fingerprint THEN
      RAISE EXCEPTION
        'recovery chain, source identity, method, or semantic prompt is discontinuous'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    SELECT array_agg(linked.attempt_id ORDER BY linked.attempt_id)
    INTO prior_linked_attempts
    FROM nhi_rule_history_update_queue.recovery_route_attempt linked
    WHERE linked.work_item_id = p_work_item_id
      AND linked.generation = latest_generation;
    IF prior_linked_attempts IS DISTINCT FROM normalized_requested THEN
      RAISE EXCEPTION
        'superseded attempt IDs must exactly identify the prior failed generation'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;

  SELECT
    count(attempt.attempt_id),
    count(*) FILTER (WHERE attempt.status = 'failed'),
    count(DISTINCT attempt.job_id),
    count(DISTINCT attempt.lane)
  INTO found_count, failed_count, job_count, lane_count
  FROM unnest(normalized_requested) requested
  LEFT JOIN nhi_rule_history_update_ops.worker_attempt attempt
    ON attempt.attempt_id = requested;
  IF found_count <> requested_count
     OR failed_count <> requested_count
     OR job_count <> 1
     OR lane_count <> requested_count THEN
    RAISE EXCEPTION
      'superseded attempts must be distinct failed routes from one worker job'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM unnest(normalized_requested) requested
    JOIN nhi_rule_history_update_ops.worker_attempt fallback
      ON fallback.attempt_id = requested
    WHERE fallback.lane = 'fallback'
      AND NOT fallback.primary_attempt_id = ANY(normalized_requested)
  ) THEN
    RAISE EXCEPTION
      'superseded fallback must include its failed primary'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  INSERT INTO
    nhi_rule_history_update_queue.work_recovery_authorization (
      authorization_id,
      work_item_id,
      prior_generation,
      new_generation,
      source_bundle_uid,
      source_manifest_sha256,
      prior_method_version,
      new_method_version,
      prior_semantic_prompt_fingerprint,
      new_semantic_prompt_fingerprint,
      decision_basis_id,
      reason,
      route,
      authorized_by,
      authorized_at
    ) VALUES (
      p_authorization_id,
      p_work_item_id,
      p_prior_generation,
      p_new_generation,
      p_source_bundle_uid,
      p_source_manifest_sha256,
      p_prior_method_version,
      p_new_method_version,
      p_prior_semantic_prompt_fingerprint,
      p_new_semantic_prompt_fingerprint,
      p_decision_basis_id,
      p_reason,
      p_route,
      p_actor_kind,
      p_authorized_at
    );

  FOR attempt_row IN
    SELECT attempt.attempt_id, attempt.lane
    FROM unnest(normalized_requested) requested
    JOIN nhi_rule_history_update_ops.worker_attempt attempt
      ON attempt.attempt_id = requested
  LOOP
    INSERT INTO
      nhi_rule_history_update_queue.recovery_superseded_attempt (
        authorization_id, attempt_id, route, recorded_at
      ) VALUES (
        p_authorization_id,
        attempt_row.attempt_id,
        attempt_row.lane,
        p_authorized_at
      );
  END LOOP;

  INSERT INTO nhi_rule_history_update_queue.work_generation (
    work_item_id, generation, authorization_id
  ) VALUES (
    p_work_item_id, p_new_generation, p_authorization_id
  );

  INSERT INTO
    nhi_rule_history_update_queue.work_generation_transition (
      work_item_id,
      generation,
      transition_seq,
      transition_id,
      from_state,
      to_state,
      actor_kind,
      source_job_id,
      bundle_receipt_id,
      candidate_proposal_id,
      recorded_at
    ) VALUES (
      p_work_item_id,
      p_new_generation,
      1,
      p_initial_transition_id,
      NULL,
      'retry_pending',
      p_actor_kind,
      NULL,
      NULL,
      NULL,
      p_authorized_at
    );

  RETURN QUERY SELECT
    p_authorization_id, p_new_generation, p_initial_transition_id, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      p_authorization_id uuid,
      p_initial_transition_id uuid,
      p_work_item_id uuid,
      p_prior_generation integer,
      p_new_generation integer,
      p_legacy_failure_admission_id uuid,
      p_source_bundle_uid text,
      p_source_manifest_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_prior_method_version text,
      p_new_method_version text,
      p_prior_semantic_prompt_fingerprint
        nhi_rule_history_update_ops.sha256_hex,
      p_new_semantic_prompt_fingerprint
        nhi_rule_history_update_ops.sha256_hex,
      p_decision_basis_id text,
      p_reason text,
      p_route text,
      p_actor_kind text,
      p_authorized_at timestamptz
    )
RETURNS TABLE (
  authorization_id uuid,
  generation integer,
  transition_id uuid,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  current_legacy_state text;
  latest_generation integer;
  existing_initial_transition uuid;
  existing_auth
    nhi_rule_history_update_queue.work_recovery_authorization%ROWTYPE;
  legacy
    nhi_rule_history_update_queue.legacy_failure_evidence%ROWTYPE;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_prior_generation <> 1
     OR p_new_generation <> 2
     OR coalesce(btrim(p_source_bundle_uid), '') = ''
     OR coalesce(btrim(p_prior_method_version), '') = ''
     OR coalesce(btrim(p_new_method_version), '') = ''
     OR coalesce(btrim(p_decision_basis_id), '') = ''
     OR coalesce(btrim(p_reason), '') = ''
     OR coalesce(btrim(p_actor_kind), '') = ''
     OR p_route <> 'primary_then_fallback'
     OR p_legacy_failure_admission_id IS NULL
     OR (
       p_prior_method_version = p_new_method_version
       AND p_prior_semantic_prompt_fingerprint =
         p_new_semantic_prompt_fingerprint
     ) THEN
    RAISE EXCEPTION
      'legacy recovery authorization is invalid or method/prompt is unchanged'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing_auth
  FROM nhi_rule_history_update_queue.work_recovery_authorization auth
  WHERE auth.authorization_id = p_authorization_id;
  IF FOUND THEN
    SELECT transition.transition_id
    INTO existing_initial_transition
    FROM nhi_rule_history_update_queue.work_generation_transition transition
    WHERE transition.work_item_id = p_work_item_id
      AND transition.generation = p_new_generation
      AND transition.transition_seq = 1;
    IF existing_auth.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing_auth.prior_generation <> p_prior_generation
       OR existing_auth.new_generation <> p_new_generation
       OR existing_auth.legacy_failure_admission_id IS DISTINCT FROM
          p_legacy_failure_admission_id
       OR existing_auth.source_bundle_uid <> p_source_bundle_uid
       OR existing_auth.source_manifest_sha256 <>
          p_source_manifest_sha256
       OR existing_auth.prior_method_version <> p_prior_method_version
       OR existing_auth.new_method_version <> p_new_method_version
       OR existing_auth.prior_semantic_prompt_fingerprint <>
          p_prior_semantic_prompt_fingerprint
       OR existing_auth.new_semantic_prompt_fingerprint <>
          p_new_semantic_prompt_fingerprint
       OR existing_auth.decision_basis_id <> p_decision_basis_id
       OR existing_auth.reason <> p_reason
       OR existing_auth.route <> p_route
       OR existing_auth.authorized_by <> p_actor_kind
       OR existing_initial_transition IS DISTINCT FROM
          p_initial_transition_id THEN
      RAISE EXCEPTION
        'legacy recovery authorization identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      p_authorization_id, p_new_generation, p_initial_transition_id, true;
    RETURN;
  END IF;

  SELECT * INTO legacy
  FROM nhi_rule_history_update_queue.legacy_failure_evidence evidence
  WHERE evidence.admission_id = p_legacy_failure_admission_id;
  IF NOT FOUND
     OR legacy.work_item_id IS DISTINCT FROM p_work_item_id
     OR legacy.source_bundle_uid <> p_source_bundle_uid
     OR legacy.source_manifest_sha256 <> p_source_manifest_sha256
     OR legacy.method_version <> p_prior_method_version
     OR legacy.semantic_prompt_fingerprint <>
        p_prior_semantic_prompt_fingerprint THEN
    RAISE EXCEPTION
      'legacy recovery inputs do not match the admitted failure evidence'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT current_state INTO current_legacy_state
  FROM nhi_rule_history_update_queue.v_work_item_current
  WHERE work_item_id = p_work_item_id;
  SELECT max(generation.generation) INTO latest_generation
  FROM nhi_rule_history_update_queue.work_generation generation
  WHERE generation.work_item_id = p_work_item_id;
  IF current_legacy_state IS DISTINCT FROM 'failed_terminal'
     OR latest_generation IS NOT NULL THEN
    RAISE EXCEPTION
      'legacy failure admission can authorize only the first explicit generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  INSERT INTO
    nhi_rule_history_update_queue.work_recovery_authorization (
      authorization_id,
      work_item_id,
      prior_generation,
      new_generation,
      source_bundle_uid,
      source_manifest_sha256,
      prior_method_version,
      new_method_version,
      prior_semantic_prompt_fingerprint,
      new_semantic_prompt_fingerprint,
      decision_basis_id,
      reason,
      route,
      legacy_failure_admission_id,
      authorized_by,
      authorized_at
    ) VALUES (
      p_authorization_id,
      p_work_item_id,
      p_prior_generation,
      p_new_generation,
      p_source_bundle_uid,
      p_source_manifest_sha256,
      p_prior_method_version,
      p_new_method_version,
      p_prior_semantic_prompt_fingerprint,
      p_new_semantic_prompt_fingerprint,
      p_decision_basis_id,
      p_reason,
      p_route,
      p_legacy_failure_admission_id,
      p_actor_kind,
      p_authorized_at
    );

  INSERT INTO nhi_rule_history_update_queue.work_generation (
    work_item_id, generation, authorization_id
  ) VALUES (
    p_work_item_id, p_new_generation, p_authorization_id
  );

  INSERT INTO
    nhi_rule_history_update_queue.work_generation_transition (
      work_item_id,
      generation,
      transition_seq,
      transition_id,
      from_state,
      to_state,
      actor_kind,
      source_job_id,
      bundle_receipt_id,
      candidate_proposal_id,
      recorded_at
    ) VALUES (
      p_work_item_id,
      p_new_generation,
      1,
      p_initial_transition_id,
      NULL,
      'retry_pending',
      p_actor_kind,
      NULL,
      NULL,
      NULL,
      p_authorized_at
    );

  RETURN QUERY SELECT
    p_authorization_id, p_new_generation, p_initial_transition_id, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.advance_recovery_generation(
    p_transition_id uuid,
    p_work_item_id uuid,
    p_generation integer,
    p_to_state text,
    p_actor_kind text,
    p_source_job_id uuid,
    p_bundle_receipt_id uuid,
    p_candidate_proposal_id uuid,
    p_recorded_at timestamptz
  )
RETURNS TABLE (
  transition_id uuid,
  transition_seq integer,
  to_state text,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  prior_seq integer;
  prior_state text;
  existing
    nhi_rule_history_update_queue.work_generation_transition%ROWTYPE;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_to_state NOT IN (
    'proposal_running',
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'partition_required'
  ) OR btrim(p_actor_kind) = '' THEN
    RAISE EXCEPTION
      'recovery transition target or actor is invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.transition_id = p_transition_id;
  IF FOUND THEN
    IF existing.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing.generation <> p_generation
       OR existing.to_state <> p_to_state
       OR existing.actor_kind <> p_actor_kind
       OR existing.source_job_id IS DISTINCT FROM p_source_job_id
       OR existing.bundle_receipt_id IS DISTINCT FROM p_bundle_receipt_id
       OR existing.candidate_proposal_id IS DISTINCT FROM
          p_candidate_proposal_id THEN
      RAISE EXCEPTION
        'recovery transition identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      existing.transition_id,
      existing.transition_seq,
      existing.to_state,
      true;
    RETURN;
  END IF;

  SELECT transition.transition_seq, transition.to_state
  INTO prior_seq, prior_state
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = p_work_item_id
    AND transition.generation = p_generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION
      'recovery generation lacks its retry_pending transition'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  INSERT INTO
    nhi_rule_history_update_queue.work_generation_transition (
      work_item_id,
      generation,
      transition_seq,
      transition_id,
      from_state,
      to_state,
      actor_kind,
      source_job_id,
      bundle_receipt_id,
      candidate_proposal_id,
      recorded_at
    ) VALUES (
      p_work_item_id,
      p_generation,
      prior_seq + 1,
      p_transition_id,
      prior_state,
      p_to_state,
      p_actor_kind,
      p_source_job_id,
      p_bundle_receipt_id,
      p_candidate_proposal_id,
      p_recorded_at
    );
  RETURN QUERY SELECT
    p_transition_id, prior_seq + 1, p_to_state, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.register_recovery_route_attempt(
    p_work_item_id uuid,
    p_generation integer,
    p_route text,
    p_attempt_id uuid,
    p_source_job_id uuid,
    p_method_version text,
    p_semantic_prompt_fingerprint
      nhi_rule_history_update_ops.sha256_hex,
    p_recorded_at timestamptz
  )
RETURNS TABLE (
  attempt_id uuid,
  route text,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  existing
    nhi_rule_history_update_queue.recovery_route_attempt%ROWTYPE;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_route NOT IN ('primary', 'fallback')
     OR btrim(p_method_version) = '' THEN
    RAISE EXCEPTION
      'recovery route or method version is invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing
  FROM nhi_rule_history_update_queue.recovery_route_attempt linked
  WHERE linked.work_item_id = p_work_item_id
    AND linked.generation = p_generation
    AND linked.route = p_route;
  IF FOUND THEN
    IF existing.attempt_id IS DISTINCT FROM p_attempt_id
       OR existing.source_job_id IS DISTINCT FROM p_source_job_id
       OR existing.method_version <> p_method_version
       OR existing.semantic_prompt_fingerprint <>
          p_semantic_prompt_fingerprint THEN
      RAISE EXCEPTION
        'recovery route is already consumed by another attempt'
        USING ERRCODE = 'unique_violation';
    END IF;
    RETURN QUERY SELECT existing.attempt_id, existing.route, true;
    RETURN;
  END IF;

  INSERT INTO nhi_rule_history_update_queue.recovery_route_attempt (
    work_item_id,
    generation,
    route,
    attempt_id,
    source_job_id,
    method_version,
    semantic_prompt_fingerprint,
    recorded_at
  ) VALUES (
    p_work_item_id,
    p_generation,
    p_route,
    p_attempt_id,
    p_source_job_id,
    p_method_version,
    p_semantic_prompt_fingerprint,
    p_recorded_at
  );
  RETURN QUERY SELECT p_attempt_id, p_route, false;
END;
$$;

DROP TRIGGER IF EXISTS work_generation_insert_guard
  ON nhi_rule_history_update_queue.work_generation;
CREATE TRIGGER work_generation_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.work_generation
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_update_queue.guard_work_generation_insert();

DROP TRIGGER IF EXISTS work_generation_transition_insert_guard
  ON nhi_rule_history_update_queue.work_generation_transition;
CREATE TRIGGER work_generation_transition_insert_guard
BEFORE INSERT ON
  nhi_rule_history_update_queue.work_generation_transition
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_update_queue.guard_generation_transition_insert();

DROP TRIGGER IF EXISTS recovery_route_attempt_insert_guard
  ON nhi_rule_history_update_queue.recovery_route_attempt;
CREATE TRIGGER recovery_route_attempt_insert_guard
BEFORE INSERT ON nhi_rule_history_update_queue.recovery_route_attempt
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_update_queue.guard_recovery_route_attempt_insert();

DO $append_only_guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'recovery_schema_migration',
    'legacy_failure_evidence',
    'legacy_failure_attempt_evidence',
    'work_recovery_authorization',
    'recovery_superseded_attempt',
    'work_generation',
    'work_generation_transition',
    'recovery_route_attempt'
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
    'partition_required',
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
  nhi_rule_history_update_queue.v_recovery_generation_current AS
SELECT
  generation.work_item_id,
  generation.generation,
  generation.authorization_id,
  auth.source_bundle_uid,
  auth.source_manifest_sha256,
  auth.prior_method_version,
  auth.new_method_version,
  auth.prior_semantic_prompt_fingerprint,
  auth.new_semantic_prompt_fingerprint,
  auth.decision_basis_id,
  auth.reason,
  auth.route,
  auth.legacy_failure_admission_id,
  current_transition.transition_seq,
  current_transition.to_state AS current_state,
  current_transition.source_job_id,
  current_transition.bundle_receipt_id,
  current_transition.candidate_proposal_id,
  current_transition.recorded_at AS state_recorded_at,
  current_transition.to_state IN (
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'partition_required'
  ) AS is_terminal
FROM nhi_rule_history_update_queue.work_generation generation
JOIN
  nhi_rule_history_update_queue.work_recovery_authorization auth
  ON auth.authorization_id = generation.authorization_id
JOIN LATERAL (
  SELECT transition.*
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = generation.work_item_id
    AND transition.generation = generation.generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1
) current_transition ON true;

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_recovery_backlog AS
SELECT *
FROM nhi_rule_history_update_queue.v_recovery_generation_current
WHERE current_state IN ('retry_pending', 'proposal_running');

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_dispatch_v2 AS
SELECT
  current.work_item_id,
  1 AS generation,
  current.current_state,
  'initial'::text AS generation_kind,
  NULL::uuid AS authorization_id,
  NULL::text AS route,
  NULL::text AS source_bundle_uid,
  NULL::text AS source_manifest_sha256
FROM nhi_rule_history_update_queue.v_work_backlog current
UNION ALL
SELECT
  recovery.work_item_id,
  recovery.generation,
  recovery.current_state,
  'authorized_recovery'::text AS generation_kind,
  recovery.authorization_id,
  recovery.route,
  recovery.source_bundle_uid,
  recovery.source_manifest_sha256::text
FROM nhi_rule_history_update_queue.v_recovery_backlog recovery;

REVOKE ALL ON
  nhi_rule_history_update_queue.recovery_schema_migration,
  nhi_rule_history_update_queue.legacy_failure_evidence,
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence,
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.recovery_superseded_attempt,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.recovery_route_attempt,
  nhi_rule_history_update_queue.v_recovery_generation_current,
  nhi_rule_history_update_queue.v_recovery_backlog,
  nhi_rule_history_update_queue.v_work_dispatch_v2
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    uuid, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text[], text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, text, timestamptz
  )
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex, text, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    uuid[], text, text, text, text, timestamptz
  )
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      uuid, uuid, uuid, integer, integer, uuid, text,
      nhi_rule_history_update_ops.sha256_hex, text, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, text, text, text, timestamptz
    )
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.advance_recovery_generation(
    uuid, uuid, integer, text, text, uuid, uuid, uuid, timestamptz
  )
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.register_recovery_route_attempt(
    uuid, integer, text, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex, timestamptz
  )
  FROM PUBLIC;

GRANT SELECT ON
  nhi_rule_history_update_queue.legacy_failure_evidence,
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence,
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.recovery_superseded_attempt,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.recovery_route_attempt,
  nhi_rule_history_update_queue.v_recovery_generation_current,
  nhi_rule_history_update_queue.v_recovery_backlog,
  nhi_rule_history_update_queue.v_work_dispatch_v2
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    uuid, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text[], text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, text, timestamptz
  )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex, text, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    uuid[], text, text, text, text, timestamptz
  )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      uuid, uuid, uuid, integer, integer, uuid, text,
      nhi_rule_history_update_ops.sha256_hex, text, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, text, text, text, timestamptz
    )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.advance_recovery_generation(
    uuid, uuid, integer, text, text, uuid, uuid, uuid, timestamptz
  )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.register_recovery_route_attempt(
    uuid, integer, text, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex, timestamptz
  )
  TO nhi_rule_history_update_queue_runtime;

INSERT INTO
  nhi_rule_history_update_queue.recovery_schema_migration (
    migration_id, contract_marker
  ) VALUES (
    '2026-07-28_nhi_rule_history_update_queue_recovery_v2',
    'managed=nhi_rule_history_update_queue/recovery-v2'
  )
ON CONFLICT (migration_id) DO NOTHING;

DO $marker_verify$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.recovery_schema_migration
    WHERE migration_id =
      '2026-07-28_nhi_rule_history_update_queue_recovery_v2'
      AND contract_marker =
        'managed=nhi_rule_history_update_queue/recovery-v2'
  ) THEN
    RAISE EXCEPTION
      'recovery v2 migration marker is absent or inconsistent'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$marker_verify$;

COMMENT ON TABLE
  nhi_rule_history_update_queue.legacy_failure_evidence IS
  'Append-only admission bridge for canonical pre-PG worker-run/v2 failure receipts. Stores relative paths and exact file hashes; never fabricates worker_attempt UUID rows or changes the terminal transition.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence IS
  'Exactly one 64-hex primary and one primary-linked fallback record from an admitted legacy attempts.jsonl stream, with immutable per-record hashes.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.work_recovery_authorization IS
  'Explicit decision record for generation G+1; binds source bundle identity, old/new method and semantic prompt fingerprints, superseded attempts, reason, decision basis, and abstract route. Stage only.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.work_generation_transition IS
  'Append-only recovery-generation state. partition_required is terminal and fail-closed; failed generations never schedule another generation automatically.';
COMMENT ON TABLE
  nhi_rule_history_update_queue.recovery_route_attempt IS
  'At most one primary and one failed-primary-linked fallback per recovery generation; linkage is append-only and never advances state automatically.';
COMMENT ON VIEW
  nhi_rule_history_update_queue.v_work_dispatch_v2 IS
  'Dispatch union for legacy generation 1 backlog and explicitly authorized recovery generations only.';

COMMIT;
