-- Two-phase rollback for typed partition recovery A+.
--
-- Phase 1 always disables operator/runtime capabilities and commits.  Phase 2
-- is destructive only while every A+ evidence ledger is empty.  Once any
-- admission, authorization, dispatch, route, transition, or terminal evidence
-- exists, phase 2 fails closed and preserves all rows and objects.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_catalog.pg_advisory_xact_lock(
  pg_catalog.hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $disable_capabilities$
DECLARE
  target_role text;
  function_oid regprocedure;
BEGIN
  FOREACH target_role IN ARRAY ARRAY[
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_candidate_runtime'
  ]
  LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = target_role
    ) THEN
      FOR function_oid IN
        SELECT function.oid::regprocedure
        FROM pg_catalog.pg_proc function
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = function.pronamespace
        WHERE namespace.nspname =
          'nhi_rule_history_partition_recovery'
      LOOP
        EXECUTE pg_catalog.format(
          'REVOKE EXECUTE ON FUNCTION %s FROM %I',
          function_oid::text, target_role
        );
      END LOOP;
      EXECUTE pg_catalog.format(
        'REVOKE USAGE ON SCHEMA nhi_rule_history_partition_recovery FROM %I',
        target_role
      );
    END IF;
  END LOOP;
END;
$disable_capabilities$;

DO $disable_candidate_attach_capability$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime'
  ) THEN
    REVOKE SELECT ON
      nhi_rule_history_partition_recovery.dispatch_claim,
      nhi_rule_history_partition_recovery.worker_route_reservation,
      nhi_rule_history_partition_recovery.worker_route_outcome
      FROM nhi_rule_history_candidate_runtime;
    REVOKE SELECT ON nhi_rule_history_update_ops.job_lease
      FROM nhi_rule_history_candidate_runtime;
    REVOKE INSERT ON
      nhi_rule_history_update_ops.content_artifact,
      nhi_rule_history_update_ops.bundle_receipt
      FROM nhi_rule_history_candidate_runtime;
  END IF;
END;
$disable_candidate_attach_capability$;

DO $disable_legacy_recovery_capabilities$
DECLARE
  target_function_identity text;
  function_oid regprocedure;
  prior_owner name;
BEGIN
  FOREACH target_function_identity IN ARRAY ARRAY[
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)'
  ]
  LOOP
    function_oid :=
      pg_catalog.to_regprocedure(target_function_identity);
    SELECT snapshot.prior_owner
    INTO prior_owner
    FROM
      nhi_rule_history_partition_recovery.
        legacy_function_owner_snapshot snapshot
    WHERE snapshot.function_identity = target_function_identity;
    IF function_oid IS NULL
       OR prior_owner IS NULL
       OR prior_owner IN (
         'nhi_rule_history_recovery_owner',
         'nhi_rule_history_recovery_authorizer'
       ) THEN
      RAISE EXCEPTION
        'refusing rollback capability disable: unsafe or missing legacy function owner snapshot for %',
        target_function_identity
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %s OWNER TO %I',
      function_oid::text, prior_owner
    );
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM nhi_rule_history_recovery_authorizer',
      function_oid::text
    );
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM nhi_rule_history_recovery_owner',
      function_oid::text
    );
  END LOOP;
END;
$disable_legacy_recovery_capabilities$;

COMMIT;

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_catalog.pg_advisory_xact_lock(
  pg_catalog.hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $marker_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_partition_recovery.schema_migration
    WHERE migration_id =
      '2026-07-28_nhi_rule_history_partition_recovery_a_plus'
      AND contract_marker =
        'managed=nhi_rule_history_partition_recovery/a-plus'
  ) THEN
    RAISE EXCEPTION
      'refusing rollback: partition recovery A+ marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$marker_guard$;

LOCK TABLE
  nhi_rule_history_partition_recovery.partition_recovery_admission,
  nhi_rule_history_partition_recovery.partition_suitability_receipt,
  nhi_rule_history_partition_recovery.partition_recovery_authorization,
  nhi_rule_history_partition_recovery.authorization_event,
  nhi_rule_history_partition_recovery.dispatch_claim,
  nhi_rule_history_partition_recovery.worker_route_reservation,
  nhi_rule_history_partition_recovery.worker_route_outcome,
  nhi_rule_history_partition_recovery.late_worker_output_quarantine,
  nhi_rule_history_partition_recovery.partition_terminal_receipt,
  nhi_rule_history_partition_recovery.generation_transition_evidence
IN ACCESS EXCLUSIVE MODE;

DO $any_evidence_guard$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_admission
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_suitability_receipt
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_authorization
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_partition_recovery.authorization_event
  ) OR EXISTS (
    SELECT 1 FROM nhi_rule_history_partition_recovery.dispatch_claim
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_partition_recovery.worker_route_outcome
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.late_worker_output_quarantine
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_terminal_receipt
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.generation_transition_evidence
  ) THEN
    RAISE EXCEPTION
      'refusing destructive rollback: partition recovery evidence exists; capabilities remain disabled and every ledger row is preserved'
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;
END;
$any_evidence_guard$;

DROP TRIGGER IF EXISTS partition_recovery_legacy_transition_guard
  ON nhi_rule_history_update_queue.work_item_transition;

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

DO $restore_function_owners$
DECLARE
  snapshot record;
  function_oid regprocedure;
BEGIN
  FOR snapshot IN
    SELECT function_identity, prior_owner
    FROM
      nhi_rule_history_partition_recovery.legacy_function_owner_snapshot
  LOOP
    function_oid :=
      pg_catalog.to_regprocedure(snapshot.function_identity);
    IF function_oid IS NULL THEN
      RAISE EXCEPTION
        'refusing rollback: snapshotted function is missing: %',
        snapshot.function_identity
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %s OWNER TO %I',
      function_oid::text, snapshot.prior_owner
    );
  END LOOP;
END;
$restore_function_owners$;

REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    uuid, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, text,
    nhi_rule_history_update_ops.sha256_hex, jsonb, text,
    nhi_rule_history_update_ops.sha256_hex, jsonb, text[], text,
    nhi_rule_history_update_ops.sha256_hex, text,
    nhi_rule_history_update_ops.sha256_hex, text, timestamptz
  )
  FROM nhi_rule_history_recovery_authorizer;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex,
    text, text, nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex, uuid[],
    text, text, text, text, timestamptz
  )
  FROM nhi_rule_history_recovery_authorizer;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(
    uuid, uuid, uuid, integer, integer, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    text, text, nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, text, text, text, timestamptz
  )
  FROM nhi_rule_history_recovery_authorizer;

DROP VIEW
  nhi_rule_history_partition_recovery.v_authorization_current
  RESTRICT;

DO $drop_partition_functions$
DECLARE
  function_oid regprocedure;
BEGIN
  FOR function_oid IN
    SELECT function.oid::regprocedure
    FROM pg_catalog.pg_proc function
    JOIN pg_catalog.pg_namespace namespace
      ON namespace.oid = function.pronamespace
    WHERE namespace.nspname = 'nhi_rule_history_partition_recovery'
  LOOP
    EXECUTE pg_catalog.format(
      'DROP FUNCTION %s RESTRICT', function_oid::text
    );
  END LOOP;
END;
$drop_partition_functions$;

DROP TABLE
  nhi_rule_history_partition_recovery.generation_transition_evidence
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.partition_terminal_receipt
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.late_worker_output_quarantine
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.worker_route_outcome
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.worker_route_reservation
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.dispatch_claim
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.authorization_event
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.partition_recovery_authorization
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.partition_suitability_receipt
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.partition_recovery_admission
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.legacy_function_owner_snapshot
  RESTRICT;
DROP TABLE
  nhi_rule_history_partition_recovery.schema_migration
  RESTRICT;

ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  GRANT ALL ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  GRANT ALL ON SEQUENCES TO PUBLIC;
ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  GRANT EXECUTE ON FUNCTIONS TO PUBLIC;

REVOKE ALL ON nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.job_lease,
  nhi_rule_history_update_ops.worker_attempt
  FROM nhi_rule_history_recovery_owner;
REVOKE ALL ON
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.recovery_route_attempt,
  nhi_rule_history_update_queue.rss_work_item,
  nhi_rule_history_update_queue.work_item_transition
  FROM nhi_rule_history_recovery_owner;
REVOKE ALL ON
  nhi_rule_history_candidate_stage.candidate_proposal,
  nhi_rule_history_candidate_stage.current_candidate_state
  FROM nhi_rule_history_recovery_owner;

DROP SCHEMA nhi_rule_history_partition_recovery RESTRICT;

COMMIT;
