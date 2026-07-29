-- 2026-07-28 — A+ typed zero-call partition recovery admission
--
-- Dedicated typed admission and authorization are layered over the existing
-- recovery-v2 generation/transition/route infrastructure.  This migration is
-- stage-only: it cannot write canonical legal history, promote a candidate,
-- reacquire a source, or poll RSS.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_catalog.pg_advisory_xact_lock(
  pg_catalog.hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $dependency_guard$
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
      'recovery-v2 is required before partition recovery A+'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF pg_catalog.to_regnamespace('nhi_rule_history') IS NOT NULL THEN
    RAISE EXCEPTION
      'canonical legal-history schema must remain absent'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

DO $role_guard$
DECLARE
  role_name text;
  expected_comment text;
  role_row record;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'nhi_rule_history_recovery_owner',
    'nhi_rule_history_recovery_authorizer'
  ]
  LOOP
    expected_comment := CASE role_name
      WHEN 'nhi_rule_history_recovery_owner' THEN
        'NOLOGIN non-superuser owner for typed NHI partition-recovery objects. managed=nhi_rule_history_partition_recovery/a-plus'
      ELSE
        'NOLOGIN operator-only capability for explicit NHI recovery admission and authorization. managed=nhi_rule_history_partition_recovery/a-plus'
    END;
    SELECT
      role.rolcanlogin,
      role.rolsuper,
      role.rolcreatedb,
      role.rolcreaterole,
      role.rolinherit,
      role.rolreplication,
      role.rolbypassrls,
      pg_catalog.shobj_description(role.oid, 'pg_authid') AS comment
    INTO role_row
    FROM pg_catalog.pg_roles role
    WHERE role.rolname = role_name;
    IF NOT FOUND THEN
      EXECUTE pg_catalog.format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
        role_name
      );
      EXECUTE pg_catalog.format(
        'COMMENT ON ROLE %I IS %L', role_name, expected_comment
      );
    ELSIF role_row.rolcanlogin
       OR role_row.rolsuper
       OR role_row.rolcreatedb
       OR role_row.rolcreaterole
       OR role_row.rolinherit
       OR role_row.rolreplication
       OR role_row.rolbypassrls
       OR role_row.comment IS DISTINCT FROM expected_comment THEN
      RAISE EXCEPTION
        'managed recovery role % is absent or has unsafe attributes',
        role_name
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END LOOP;
END;
$role_guard$;

DO $role_separation$
DECLARE
  source_role text;
BEGIN
  REVOKE nhi_rule_history_recovery_owner
    FROM nhi_rule_history_recovery_authorizer;
  REVOKE nhi_rule_history_recovery_authorizer
    FROM nhi_rule_history_recovery_owner;
  REVOKE nhi_rule_history_update_queue_runtime
    FROM nhi_rule_history_recovery_authorizer;
  REVOKE nhi_rule_history_candidate_runtime
    FROM nhi_rule_history_recovery_authorizer;

  FOREACH source_role IN ARRAY ARRAY[
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_stage_writer',
    'nhi_rule_history_candidate_runtime'
  ]
  LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = source_role
    ) THEN
      EXECUTE pg_catalog.format(
        'REVOKE nhi_rule_history_recovery_authorizer FROM %I',
        source_role
      );
      EXECUTE pg_catalog.format(
        'REVOKE nhi_rule_history_recovery_owner FROM %I',
        source_role
      );
      IF source_role <> 'nhi_rule_history_update_queue_runtime' THEN
        EXECUTE pg_catalog.format(
          'REVOKE nhi_rule_history_update_queue_runtime FROM %I',
          source_role
        );
      END IF;
      IF source_role <> 'nhi_rule_history_candidate_runtime' THEN
        EXECUTE pg_catalog.format(
          'REVOKE nhi_rule_history_candidate_runtime FROM %I',
          source_role
        );
      END IF;
    END IF;
  END LOOP;
END;
$role_separation$;

DO $schema_guard$
DECLARE
  schema_oid oid;
  schema_owner text;
  schema_comment text;
BEGIN
  SELECT namespace.oid,
         pg_catalog.pg_get_userbyid(namespace.nspowner),
         pg_catalog.obj_description(namespace.oid, 'pg_namespace')
  INTO schema_oid, schema_owner, schema_comment
  FROM pg_catalog.pg_namespace namespace
  WHERE namespace.nspname = 'nhi_rule_history_partition_recovery';
  IF NOT FOUND THEN
    CREATE SCHEMA nhi_rule_history_partition_recovery
      AUTHORIZATION nhi_rule_history_recovery_owner;
    COMMENT ON SCHEMA nhi_rule_history_partition_recovery IS
      'Typed operator-admitted zero-call partition recovery; stage only. managed=nhi_rule_history_partition_recovery/a-plus';
  ELSIF schema_owner <> 'nhi_rule_history_recovery_owner'
     OR schema_comment IS DISTINCT FROM
       'Typed operator-admitted zero-call partition recovery; stage only. managed=nhi_rule_history_partition_recovery/a-plus' THEN
    RAISE EXCEPTION
      'partition recovery schema is not the managed A+ schema'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.schema_migration (
    migration_id text PRIMARY KEY,
    contract_marker text NOT NULL UNIQUE,
    applied_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CONSTRAINT partition_recovery_migration_id_chk CHECK (
      migration_id =
        '2026-07-28_nhi_rule_history_partition_recovery_a_plus'
    ),
    CONSTRAINT partition_recovery_migration_marker_chk CHECK (
      contract_marker =
        'managed=nhi_rule_history_partition_recovery/a-plus'
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.legacy_function_owner_snapshot (
    function_identity text PRIMARY KEY,
    prior_owner name NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT pg_catalog.now()
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.partition_recovery_admission (
    admission_id uuid PRIMARY KEY,
    work_item_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.rss_work_item (work_item_id)
      ON DELETE RESTRICT,
    prior_generation integer NOT NULL CHECK (prior_generation = 1),
    terminal_transition_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_queue.work_item_transition
        (transition_id)
      ON DELETE RESTRICT,
    terminal_transition_sequence integer NOT NULL CHECK (
      terminal_transition_sequence > 0
    ),
    terminal_evidence_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prior_generation_transition_count integer NOT NULL CHECK (
      prior_generation_transition_count > 0
    ),
    prior_generation_ordered_chain_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prior_generation_rowset_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    old_job_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    old_partition_receipt_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    old_suitability_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    worker_call_count integer NOT NULL CHECK (worker_call_count = 0),
    worker_attempt_count integer NOT NULL CHECK (worker_attempt_count = 0),
    candidate_count integer NOT NULL CHECK (candidate_count = 0),
    route_attempt_count integer NOT NULL CHECK (route_attempt_count = 0),
    source_bundle_id text NOT NULL CHECK (pg_catalog.btrim(source_bundle_id) <> ''),
    source_bundle_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    corpus_bundle_id text NOT NULL CHECK (pg_catalog.btrim(corpus_bundle_id) <> ''),
    corpus_bundle_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    sealed_packet_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    packet_byte_count bigint NOT NULL CHECK (packet_byte_count > 0),
    ordered_artifact_sha256_set_digest
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    reuse_existing_bundle boolean NOT NULL CHECK (reuse_existing_bundle),
    repoll_allowed boolean NOT NULL CHECK (NOT repoll_allowed),
    reacquire_allowed boolean NOT NULL CHECK (NOT reacquire_allowed),
    new_corpus_registration_allowed boolean NOT NULL CHECK (
      NOT new_corpus_registration_allowed
    ),
    old_suitability_contract text NOT NULL,
    new_suitability_contract text NOT NULL,
    old_fingerprint_domain text NOT NULL,
    new_fingerprint_domain text NOT NULL,
    new_job_fingerprint
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    suitability_v2_schema_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    suitability_v2_receipt_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    verifier_contract_version text NOT NULL,
    verifier_code_commit text NOT NULL CHECK (
      verifier_code_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'
    ),
    verifier_config_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    verifier_executable_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prompt_version text NOT NULL,
    prompt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    semantic_prompt_changed boolean NOT NULL CHECK (
      NOT semantic_prompt_changed
    ),
    execution_contract_changed boolean NOT NULL CHECK (
      execution_contract_changed
    ),
    decision_basis_id text NOT NULL,
    public_repo_commit text NOT NULL CHECK (
      public_repo_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'
    ),
    private_controller_commit text NOT NULL CHECK (
      private_controller_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'
    ),
    migration_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    admission_contract_version text NOT NULL,
    execution_contract_version text NOT NULL,
    new_execution_contract_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    route_policy_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    review_decision_receipt_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    canonical_encoding_contract text NOT NULL CHECK (
      canonical_encoding_contract =
        'nhi-rule-history/canonical-json-bytes/no-float/v1'
    ),
    admission_payload_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL UNIQUE,
    admission_payload_json jsonb NOT NULL CHECK (
      pg_catalog.jsonb_typeof(admission_payload_json) = 'object'
    ),
    admission_payload_canonical_utf8 bytea NOT NULL,
    admitted_by_actor text NOT NULL CHECK (
      pg_catalog.btrim(admitted_by_actor) <> ''
    ),
    admitted_by_session name NOT NULL,
    admitted_by_capability text NOT NULL,
    admitted_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (
      work_item_id,
      prior_generation,
      terminal_transition_id,
      new_execution_contract_sha256
    ),
    CONSTRAINT partition_recovery_contract_delta_chk CHECK (
      old_suitability_contract <> new_suitability_contract
      AND old_fingerprint_domain <> new_fingerprint_domain
      AND pg_catalog.btrim(verifier_contract_version) <> ''
      AND pg_catalog.btrim(prompt_version) <> ''
      AND pg_catalog.btrim(decision_basis_id) <> ''
      AND pg_catalog.btrim(admission_contract_version) <> ''
      AND pg_catalog.btrim(execution_contract_version) <> ''
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.partition_suitability_receipt (
    admission_id uuid PRIMARY KEY
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_admission
          (admission_id)
      ON DELETE RESTRICT,
    decision text NOT NULL CHECK (decision = 'suitable'),
    designation_candidates jsonb NOT NULL,
    effective_designation_candidates jsonb NOT NULL,
    collapsed_parent_designations jsonb NOT NULL,
    reason_codes jsonb NOT NULL,
    schema_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    receipt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    receipt_contract text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CONSTRAINT partition_suitability_arrays_chk CHECK (
      pg_catalog.jsonb_typeof(designation_candidates) = 'array'
      AND pg_catalog.jsonb_typeof(effective_designation_candidates) = 'array'
      AND pg_catalog.jsonb_array_length(effective_designation_candidates) > 0
      AND pg_catalog.jsonb_typeof(collapsed_parent_designations) = 'array'
      AND pg_catalog.jsonb_typeof(reason_codes) = 'array'
      AND pg_catalog.jsonb_array_length(reason_codes) = 0
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.partition_recovery_authorization (
    authorization_id uuid PRIMARY KEY
      REFERENCES
        nhi_rule_history_update_queue.work_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    admission_id uuid NOT NULL UNIQUE
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_admission
          (admission_id)
      ON DELETE RESTRICT,
    work_item_id uuid NOT NULL,
    prior_generation integer NOT NULL CHECK (prior_generation = 1),
    new_generation integer NOT NULL CHECK (new_generation = 2),
    initial_transition_id uuid NOT NULL UNIQUE,
    dispatch_contract_version text NOT NULL CHECK (
      pg_catalog.btrim(dispatch_contract_version) <> ''
    ),
    expires_at timestamptz NOT NULL,
    authorized_by_actor text NOT NULL,
    authorized_by_session name NOT NULL,
    authorized_by_capability text NOT NULL,
    authorized_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (work_item_id, new_generation),
    FOREIGN KEY (work_item_id, new_generation)
      REFERENCES nhi_rule_history_update_queue.work_generation
        (work_item_id, generation)
      DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT partition_authorization_time_chk CHECK (
      expires_at > authorized_at
      AND pg_catalog.btrim(authorized_by_actor) <> ''
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.authorization_event (
    authorization_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    event_seq integer NOT NULL CHECK (event_seq BETWEEN 1 AND 2),
    event_kind text NOT NULL CHECK (
      event_kind IN ('authorized', 'revoked', 'consumed')
    ),
    reason text,
    actor text NOT NULL CHECK (pg_catalog.btrim(actor) <> ''),
    actor_session name NOT NULL,
    actor_capability text NOT NULL,
    recorded_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    PRIMARY KEY (authorization_id, event_seq),
    UNIQUE (authorization_id, event_kind),
    CONSTRAINT authorization_event_shape_chk CHECK (
      (event_seq = 1 AND event_kind = 'authorized' AND reason IS NULL)
      OR (
        event_seq = 2
        AND event_kind IN ('revoked', 'consumed')
        AND (
          (event_kind = 'revoked' AND pg_catalog.btrim(reason) <> '')
          OR (event_kind = 'consumed' AND reason IS NULL)
        )
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.dispatch_claim (
    claim_id uuid PRIMARY KEY,
    authorization_id uuid NOT NULL UNIQUE
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    admission_id uuid NOT NULL UNIQUE
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_admission
          (admission_id)
      ON DELETE RESTRICT,
    work_item_id uuid NOT NULL,
    generation integer NOT NULL CHECK (generation = 2),
    dispatch_contract_version text NOT NULL,
    admission_payload_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    sealed_packet_manifest_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    suitability_receipt_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    job_fingerprint nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prompt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    route_policy_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    source_job_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    lease_id uuid NOT NULL UNIQUE,
    owner_key text NOT NULL CHECK (pg_catalog.btrim(owner_key) <> ''),
    max_runtime_seconds integer NOT NULL CHECK (
      max_runtime_seconds BETWEEN 1 AND 21600
    ),
    lease_expires_at timestamptz NOT NULL,
    claimed_by_session name NOT NULL,
    claimed_by_capability text NOT NULL,
    consumed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (work_item_id, generation),
    FOREIGN KEY (work_item_id, generation)
      REFERENCES nhi_rule_history_update_queue.work_generation
        (work_item_id, generation)
      ON DELETE RESTRICT,
    FOREIGN KEY (source_job_id, lease_id)
      REFERENCES nhi_rule_history_update_ops.job_lease (job_id, lease_id)
      ON DELETE RESTRICT,
    CONSTRAINT dispatch_claim_lease_window_chk CHECK (
      lease_expires_at > consumed_at
      AND lease_expires_at <=
        consumed_at + max_runtime_seconds * interval '1 second'
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.worker_route_reservation (
    reservation_id uuid PRIMARY KEY,
    claim_id uuid NOT NULL
      REFERENCES nhi_rule_history_partition_recovery.dispatch_claim (claim_id)
      ON DELETE RESTRICT,
    work_item_id uuid NOT NULL,
    generation integer NOT NULL CHECK (generation = 2),
    authorization_id uuid NOT NULL,
    admission_id uuid NOT NULL,
    route_ordinal smallint NOT NULL CHECK (route_ordinal IN (1, 2)),
    route text GENERATED ALWAYS AS (
      CASE route_ordinal WHEN 1 THEN 'primary' ELSE 'fallback' END
    ) STORED,
    source_job_id uuid NOT NULL
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    packet_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    prompt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    attempt_namespace text NOT NULL CHECK (
      pg_catalog.btrim(attempt_namespace) <> ''
      AND attempt_namespace !~ '(^/|(^|/)\.\.?(/|$)|\\)'
    ),
    runtime text NOT NULL CHECK (pg_catalog.btrim(runtime) <> ''),
    provider text NOT NULL CHECK (pg_catalog.btrim(provider) <> ''),
    model text NOT NULL CHECK (pg_catalog.btrim(model) <> ''),
    controller_commit nhi_rule_history_update_ops.sha256_hex NOT NULL,
    reserved_by_session name NOT NULL,
    reserved_by_capability text NOT NULL,
    reserved_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (claim_id, route_ordinal),
    UNIQUE (work_item_id, generation, route_ordinal),
    FOREIGN KEY (authorization_id)
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (admission_id)
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_admission
          (admission_id)
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.worker_route_outcome (
    reservation_id uuid PRIMARY KEY
      REFERENCES
        nhi_rule_history_partition_recovery.worker_route_reservation
          (reservation_id)
      ON DELETE RESTRICT,
    status text NOT NULL CHECK (
      status IN ('succeeded', 'failed', 'execution_unknown')
    ),
    failure_class text,
    attempt_id uuid
      REFERENCES nhi_rule_history_update_ops.worker_attempt (attempt_id)
      ON DELETE RESTRICT,
    source_job_id uuid
      REFERENCES nhi_rule_history_update_ops.update_job (job_id)
      ON DELETE RESTRICT,
    candidate_proposal_id uuid
      REFERENCES nhi_rule_history_candidate_stage.candidate_proposal
        (proposal_id)
      ON DELETE RESTRICT,
    stdout_sha256 nhi_rule_history_update_ops.sha256_hex,
    stderr_sha256 nhi_rule_history_update_ops.sha256_hex,
    output_sha256 nhi_rule_history_update_ops.sha256_hex,
    process_exit_code integer,
    timed_out boolean NOT NULL,
    receipt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    receipt_json jsonb NOT NULL CHECK (
      pg_catalog.jsonb_typeof(receipt_json) = 'object'
    ),
    finished_by_session name NOT NULL,
    finished_by_capability text NOT NULL,
    finished_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CONSTRAINT worker_route_outcome_shape_chk CHECK (
      (
        status = 'succeeded'
        AND failure_class IS NULL
        AND attempt_id IS NOT NULL
        AND source_job_id IS NOT NULL
        AND output_sha256 IS NOT NULL
        AND NOT timed_out
      )
      OR (
        status = 'failed'
        AND failure_class IN (
          'transport_failure',
          'execution_failure',
          'timeout',
          'process_exit_failure',
          'invalid_json',
          'output_schema_invalid',
          'unknown_enum',
          'missing_locator',
          'locator_mismatch',
          'source_text_mismatch',
          'output_contract_inconsistent'
        )
        AND attempt_id IS NOT NULL
        AND source_job_id IS NOT NULL
        AND candidate_proposal_id IS NULL
      )
      OR (
        status = 'execution_unknown'
        AND failure_class = 'execution_unknown'
        AND attempt_id IS NULL
        AND source_job_id IS NULL
        AND candidate_proposal_id IS NULL
        AND output_sha256 IS NULL
        AND NOT timed_out
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.late_worker_output_quarantine (
    quarantine_id uuid PRIMARY KEY,
    reservation_id uuid NOT NULL UNIQUE
      REFERENCES
        nhi_rule_history_partition_recovery.worker_route_reservation
          (reservation_id)
      ON DELETE RESTRICT,
    output_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    receipt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    quarantined_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now()
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.partition_terminal_receipt (
    terminal_receipt_id uuid PRIMARY KEY,
    terminal_transition_id uuid NOT NULL UNIQUE,
    claim_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_partition_recovery.dispatch_claim (claim_id)
      ON DELETE RESTRICT,
    work_item_id uuid NOT NULL,
    generation integer NOT NULL CHECK (generation = 2),
    authorization_id uuid NOT NULL,
    admission_id uuid NOT NULL,
    terminal_state text NOT NULL CHECK (
      terminal_state IN (
        'staged_needs_review', 'partition_required', 'failed_terminal'
      )
    ),
    reason_code text NOT NULL,
    receipt_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    receipt_json jsonb NOT NULL CHECK (
      pg_catalog.jsonb_typeof(receipt_json) = 'object'
    ),
    closed_by_session name NOT NULL,
    closed_by_capability text NOT NULL,
    recorded_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    FOREIGN KEY (terminal_transition_id)
      REFERENCES
        nhi_rule_history_update_queue.work_generation_transition
          (transition_id)
      DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (authorization_id)
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_authorization
          (authorization_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (admission_id)
      REFERENCES
        nhi_rule_history_partition_recovery.partition_recovery_admission
          (admission_id)
      ON DELETE RESTRICT,
    CONSTRAINT partition_terminal_reason_chk CHECK (
      (terminal_state = 'staged_needs_review' AND reason_code = 'valid_output')
      OR (
        terminal_state = 'partition_required'
        AND reason_code = 'deterministic_partition_preflight'
      )
      OR (
        terminal_state = 'failed_terminal'
        AND reason_code IN (
          'preflight_replay_mismatch',
          'preflight_nondeterminism',
          'packet_or_contract_tamper',
          'primary_and_fallback_failed',
          'execution_unknown',
          'restart_before_model_reservation',
          'restart_after_model_result',
          'restart_open_route_execution_unknown'
        )
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_partition_recovery.generation_transition_evidence (
    transition_evidence_id uuid PRIMARY KEY,
    generation_transition_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_update_queue.work_generation_transition
          (transition_id)
      ON DELETE RESTRICT,
    evidence_kind text NOT NULL,
    evidence_contract text NOT NULL,
    evidence_object_id text NOT NULL,
    evidence_sha256 nhi_rule_history_update_ops.sha256_hex NOT NULL,
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    logical_locator text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    canonical_payload_sha256
      nhi_rule_history_update_ops.sha256_hex NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    created_by_session name NOT NULL,
    UNIQUE (generation_transition_id, evidence_kind, ordinal),
    CONSTRAINT transition_evidence_text_chk CHECK (
      pg_catalog.btrim(evidence_kind) <> ''
      AND pg_catalog.btrim(evidence_contract) <> ''
      AND pg_catalog.btrim(evidence_object_id) <> ''
      AND pg_catalog.btrim(logical_locator) <> ''
      AND logical_locator !~ '(^/|(^|/)\.\.?(/|$)|\\)'
    )
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.generation_one_chain_receipt(
    p_work_item_id uuid
  )
RETURNS TABLE (
  terminal_transition_id uuid,
  terminal_transition_sequence integer,
  terminal_state text,
  terminal_evidence_sha256 nhi_rule_history_update_ops.sha256_hex,
  transition_count integer,
  ordered_chain_sha256 nhi_rule_history_update_ops.sha256_hex,
  rowset_fingerprint nhi_rule_history_update_ops.sha256_hex,
  transition_rows jsonb,
  old_job_fingerprint nhi_rule_history_update_ops.sha256_hex,
  worker_call_count integer,
  worker_attempt_count integer,
  candidate_count integer,
  route_attempt_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  transition_row record;
  transition_json jsonb;
  row_digest text;
  ordered_digest text := pg_catalog.repeat('0', 64);
  sorted_row_digests text[] := ARRAY[]::text[];
  ordered_rows jsonb := '[]'::jsonb;
  latest_transition record;
BEGIN
  SELECT transition.*
  INTO latest_transition
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = p_work_item_id
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION
      'partition recovery work item has no generation-1 transition chain'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  FOR transition_row IN
    SELECT transition.*
    FROM nhi_rule_history_update_queue.work_item_transition transition
    WHERE transition.work_item_id = p_work_item_id
    ORDER BY transition.transition_seq
  LOOP
    transition_json := pg_catalog.jsonb_build_object(
      'actor_kind', transition_row.actor_kind,
      'bundle_receipt_id', transition_row.bundle_receipt_id,
      'candidate_proposal_id', transition_row.candidate_proposal_id,
      'evidence_json', transition_row.evidence_json,
      'evidence_sha256', transition_row.evidence_sha256,
      'from_state', transition_row.from_state,
      'recorded_at', transition_row.recorded_at,
      'source_job_id', transition_row.source_job_id,
      'to_state', transition_row.to_state,
      'transition_id', transition_row.transition_id,
      'transition_seq', transition_row.transition_seq,
      'work_item_id', transition_row.work_item_id
    );
    row_digest := pg_catalog.encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(transition_json::text, 'UTF8')
      ),
      'hex'
    );
    sorted_row_digests :=
      pg_catalog.array_append(sorted_row_digests, row_digest);
    ordered_rows := ordered_rows || pg_catalog.jsonb_build_array(
      transition_json
    );
    ordered_digest := pg_catalog.encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          ordered_digest || ':' || row_digest,
          'UTF8'
        )
      ),
      'hex'
    );
  END LOOP;

  terminal_transition_id := latest_transition.transition_id;
  terminal_transition_sequence := latest_transition.transition_seq;
  terminal_state := latest_transition.to_state;
  terminal_evidence_sha256 :=
    latest_transition.evidence_sha256::text;
  transition_count := pg_catalog.cardinality(sorted_row_digests);
  transition_rows := ordered_rows;
  ordered_chain_sha256 := ordered_digest;
  SELECT pg_catalog.encode(
           pg_catalog.sha256(
             pg_catalog.convert_to(
               COALESCE(
                 pg_catalog.string_agg(digest_value, ':' ORDER BY digest_value),
                 ''
               ),
               'UTF8'
             )
           ),
           'hex'
         )
  INTO rowset_fingerprint
  FROM pg_catalog.unnest(sorted_row_digests) AS rows(digest_value);

  SELECT job.job_fingerprint
  INTO old_job_fingerprint
  FROM nhi_rule_history_update_ops.update_job job
  WHERE job.job_id = latest_transition.source_job_id;
  IF old_job_fingerprint IS NULL THEN
    RAISE EXCEPTION
      'generation-1 terminal transition lacks its old job fingerprint'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT pg_catalog.count(*)::integer
  INTO worker_attempt_count
  FROM nhi_rule_history_update_ops.worker_attempt attempt
  WHERE attempt.job_id IN (
    SELECT DISTINCT transition.source_job_id
    FROM nhi_rule_history_update_queue.work_item_transition transition
    WHERE transition.work_item_id = p_work_item_id
  );
  worker_call_count := worker_attempt_count;

  SELECT pg_catalog.count(DISTINCT transition.candidate_proposal_id)::integer
  INTO candidate_count
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = p_work_item_id
    AND transition.candidate_proposal_id IS NOT NULL;

  SELECT pg_catalog.count(*)::integer
  INTO route_attempt_count
  FROM nhi_rule_history_update_queue.recovery_route_attempt attempt
  WHERE attempt.work_item_id = p_work_item_id;

  RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.generation_one_chain_matches_payload(
    p_work_item_id uuid,
    p_transition_rows jsonb
  )
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog
AS $$
DECLARE
  payload_row record;
  live_row
    nhi_rule_history_update_queue.work_item_transition%ROWTYPE;
  payload_count integer := 0;
  live_count integer;
BEGIN
  IF pg_catalog.jsonb_typeof(p_transition_rows) IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(p_transition_rows) = 0 THEN
    RETURN false;
  END IF;
  FOR payload_row IN
    SELECT item, ordinality
    FROM pg_catalog.jsonb_array_elements(p_transition_rows)
      WITH ORDINALITY AS payload(item, ordinality)
    ORDER BY ordinality
  LOOP
    payload_count := payload_count + 1;
    SELECT transition.*
    INTO live_row
    FROM nhi_rule_history_update_queue.work_item_transition transition
    WHERE transition.work_item_id = p_work_item_id
      AND transition.transition_seq = payload_row.ordinality::integer;
    IF NOT FOUND
       OR (payload_row.item - 'recorded_at') IS DISTINCT FROM
         pg_catalog.jsonb_build_object(
           'actor_kind', live_row.actor_kind,
           'bundle_receipt_id', live_row.bundle_receipt_id,
           'candidate_proposal_id', live_row.candidate_proposal_id,
           'evidence_json', live_row.evidence_json,
           'evidence_sha256', live_row.evidence_sha256,
           'from_state', live_row.from_state,
           'source_job_id', live_row.source_job_id,
           'to_state', live_row.to_state,
           'transition_id', live_row.transition_id,
           'transition_seq', live_row.transition_seq,
           'work_item_id', live_row.work_item_id
         )
       OR (payload_row.item ->> 'recorded_at')::timestamptz
         IS DISTINCT FROM live_row.recorded_at THEN
      RETURN false;
    END IF;
  END LOOP;
  SELECT pg_catalog.count(*)::integer
  INTO live_count
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = p_work_item_id;
  RETURN payload_count = live_count;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
  RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.verify_partition_recovery_admission(
    p_payload jsonb
  )
RETURNS TABLE (
  work_item_id uuid,
  terminal_transition_id uuid,
  terminal_transition_sequence integer,
  transition_count integer,
  ordered_chain_sha256 nhi_rule_history_update_ops.sha256_hex,
  rowset_fingerprint nhi_rule_history_update_ops.sha256_hex,
  verified boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  receipt record;
BEGIN
  IF pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION
      'partition recovery admission payload must be an object'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  work_item_id := (p_payload #>> '{generation_1,work_item_id}')::uuid;
  SELECT *
  INTO receipt
  FROM
    nhi_rule_history_partition_recovery.generation_one_chain_receipt(
      work_item_id
    );
  IF receipt.terminal_state IS DISTINCT FROM 'partition_required'
     OR receipt.terminal_transition_id IS DISTINCT FROM
       (p_payload #>> '{generation_1,terminal_transition_id}')::uuid
     OR receipt.terminal_transition_sequence IS DISTINCT FROM
       (p_payload #>> '{generation_1,terminal_transition_sequence}')::integer
     OR receipt.terminal_evidence_sha256 IS DISTINCT FROM
       (p_payload #>> '{generation_1,terminal_evidence_sha256}')::text
     OR receipt.transition_count IS DISTINCT FROM
       (p_payload #>> '{generation_1,transition_count}')::integer
     OR NOT
       nhi_rule_history_partition_recovery.
         generation_one_chain_matches_payload(
           work_item_id,
           p_payload #> '{generation_1,transitions}'
         )
     OR receipt.old_job_fingerprint IS DISTINCT FROM
       (p_payload #>> '{generation_1,old_job_fingerprint}')::text
     OR receipt.worker_call_count IS DISTINCT FROM
       (p_payload #>> '{generation_1,worker_call_count}')::integer
     OR receipt.worker_attempt_count IS DISTINCT FROM
       (p_payload #>> '{generation_1,worker_attempt_count}')::integer
     OR receipt.candidate_count IS DISTINCT FROM
       (p_payload #>> '{generation_1,candidate_count}')::integer
     OR receipt.route_attempt_count IS DISTINCT FROM
       (p_payload #>> '{generation_1,route_attempt_count}')::integer THEN
    RAISE EXCEPTION
      'partition recovery generation-1 chain or zero-call evidence is stale'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF receipt.worker_call_count <> 0
     OR receipt.worker_attempt_count <> 0
     OR receipt.candidate_count <> 0
     OR receipt.route_attempt_count <> 0
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_update_queue.work_generation generation
       WHERE generation.work_item_id =
         (p_payload #>> '{generation_1,work_item_id}')::uuid
     ) THEN
    RAISE EXCEPTION
      'partition recovery requires zero calls, attempts, routes, candidates, and generations'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  terminal_transition_id := receipt.terminal_transition_id;
  terminal_transition_sequence := receipt.terminal_transition_sequence;
  transition_count := receipt.transition_count;
  ordered_chain_sha256 :=
    (p_payload #>> '{generation_1,ordered_chain_sha256}')::text;
  rowset_fingerprint :=
    (p_payload #>> '{generation_1,rowset_fingerprint}')::text;
  verified := true;
  RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.admit_partition_recovery(
    p_admission_id uuid,
    p_admission_payload_sha256
      nhi_rule_history_update_ops.sha256_hex,
    p_admission_payload_json jsonb,
    p_admission_payload_canonical_utf8 bytea,
    p_actor text,
    p_admitted_at timestamptz
  )
RETURNS TABLE (
  admission_id uuid,
  admission_payload_sha256 nhi_rule_history_update_ops.sha256_hex,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  verified_row record;
  existing
    nhi_rule_history_partition_recovery.partition_recovery_admission%ROWTYPE;
  canonical_hash text;
  canonical_payload jsonb;
  supplied_id uuid;
BEGIN
  IF COALESCE(pg_catalog.btrim(p_actor), '') = '' THEN
    RAISE EXCEPTION
      'partition recovery admission actor is required'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  canonical_hash := pg_catalog.encode(
    pg_catalog.sha256(p_admission_payload_canonical_utf8), 'hex'
  );
  BEGIN
    canonical_payload := pg_catalog.convert_from(
      p_admission_payload_canonical_utf8, 'UTF8'
    )::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION
      'partition recovery canonical payload bytes are not valid UTF-8 JSON'
      USING ERRCODE = 'invalid_parameter_value';
  END;
  IF canonical_hash IS DISTINCT FROM p_admission_payload_sha256::text
     OR canonical_payload IS DISTINCT FROM p_admission_payload_json
     OR p_admission_payload_json ->> 'canonical_encoding_contract'
        IS DISTINCT FROM
          'nhi-rule-history/canonical-json-bytes/no-float/v1' THEN
    RAISE EXCEPTION
      'partition recovery canonical payload bytes or SHA-256 do not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT *
  INTO verified_row
  FROM
    nhi_rule_history_partition_recovery.
      verify_partition_recovery_admission(p_admission_payload_json);

  IF (
       p_admission_payload_json #>>
         '{generation_1,prior_generation}'
     )::integer <> 1
     OR (
       p_admission_payload_json #>>
         '{generation_1,worker_call_count}'
     )::integer <> 0
     OR (
       p_admission_payload_json #>>
         '{generation_1,worker_attempt_count}'
     )::integer <> 0
     OR (
       p_admission_payload_json #>>
         '{generation_1,candidate_count}'
     )::integer <> 0
     OR (
       p_admission_payload_json #>>
         '{generation_1,route_attempt_count}'
     )::integer <> 0
     OR (
       p_admission_payload_json #>>
         '{source_evidence,reuse_existing_bundle}'
     )::boolean
        IS DISTINCT FROM true
     OR (
       p_admission_payload_json #>>
         '{source_evidence,repoll_allowed}'
     )::boolean
        IS DISTINCT FROM false
     OR (
       p_admission_payload_json #>>
         '{source_evidence,reacquire_allowed}'
     )::boolean
        IS DISTINCT FROM false
     OR (
       p_admission_payload_json #>>
         '{source_evidence,new_corpus_registration_allowed}'
     )::boolean IS DISTINCT FROM false
     OR (
       p_admission_payload_json #>>
         '{worker_semantics,semantic_prompt_changed}'
     )::boolean
        IS DISTINCT FROM false
     OR (
       p_admission_payload_json #>>
         '{worker_semantics,execution_contract_changed}'
     )::boolean IS DISTINCT FROM true
     OR p_admission_payload_json #>>
          '{execution_delta,suitability_preflight,decision}'
        IS DISTINCT FROM 'suitable' THEN
    RAISE EXCEPTION
      'partition recovery payload violates zero-call reuse or contract-delta invariants'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
  WHERE partition_recovery_admission.admission_id = p_admission_id;
  IF FOUND THEN
    IF existing.admission_payload_sha256 <>
         p_admission_payload_sha256
       OR existing.admission_payload_json <>
         p_admission_payload_json
       OR existing.admission_payload_canonical_utf8 <>
         p_admission_payload_canonical_utf8 THEN
      RAISE EXCEPTION
        'partition recovery admission identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      existing.admission_id,
      existing.admission_payload_sha256,
      true;
    RETURN;
  END IF;

  SELECT partition_recovery_admission.admission_id
  INTO supplied_id
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
  WHERE partition_recovery_admission.admission_payload_sha256 =
    p_admission_payload_sha256
     OR (
       partition_recovery_admission.work_item_id =
         verified_row.work_item_id
       AND partition_recovery_admission.prior_generation = 1
       AND partition_recovery_admission.terminal_transition_id =
         verified_row.terminal_transition_id
       AND
         partition_recovery_admission.new_execution_contract_sha256 =
           (
             p_admission_payload_json #>>
               '{execution_delta,execution_contract_sha256}'
           )::text
     )
  LIMIT 1;
  IF FOUND THEN
    SELECT * INTO existing
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_admission
    WHERE partition_recovery_admission.admission_id = supplied_id;
    IF existing.admission_payload_sha256 <>
         p_admission_payload_sha256 THEN
      RAISE EXCEPTION
        'partition recovery admission tuple already has a different payload'
        USING ERRCODE = 'unique_violation';
    END IF;
    RETURN QUERY SELECT
      existing.admission_id,
      existing.admission_payload_sha256,
      true;
    RETURN;
  END IF;

  INSERT INTO
    nhi_rule_history_partition_recovery.partition_recovery_admission (
      admission_id, work_item_id, prior_generation,
      terminal_transition_id, terminal_transition_sequence,
      terminal_evidence_sha256, prior_generation_transition_count,
      prior_generation_ordered_chain_sha256,
      prior_generation_rowset_fingerprint, old_job_fingerprint,
      old_partition_receipt_sha256, old_suitability_sha256,
      worker_call_count, worker_attempt_count, candidate_count,
      route_attempt_count, source_bundle_id,
      source_bundle_manifest_sha256, corpus_bundle_id,
      corpus_bundle_manifest_sha256, sealed_packet_manifest_sha256,
      packet_byte_count, ordered_artifact_sha256_set_digest,
      reuse_existing_bundle, repoll_allowed, reacquire_allowed,
      new_corpus_registration_allowed, old_suitability_contract,
      new_suitability_contract, old_fingerprint_domain,
      new_fingerprint_domain, new_job_fingerprint,
      suitability_v2_schema_sha256, suitability_v2_receipt_sha256,
      verifier_contract_version, verifier_code_commit,
      verifier_config_sha256, verifier_executable_sha256,
      prompt_version, prompt_sha256, semantic_prompt_changed,
      execution_contract_changed, decision_basis_id,
      public_repo_commit, private_controller_commit, migration_sha256,
      admission_contract_version, execution_contract_version,
      new_execution_contract_sha256, route_policy_sha256,
      review_decision_receipt_sha256, canonical_encoding_contract,
      admission_payload_sha256, admission_payload_json,
      admission_payload_canonical_utf8, admitted_by_actor,
      admitted_by_session, admitted_by_capability, admitted_at
    ) VALUES (
      p_admission_id, verified_row.work_item_id, 1,
      verified_row.terminal_transition_id,
      verified_row.terminal_transition_sequence,
      (
        p_admission_payload_json #>>
          '{generation_1,terminal_evidence_sha256}'
      )::text,
      verified_row.transition_count, verified_row.ordered_chain_sha256,
      verified_row.rowset_fingerprint,
      (
        p_admission_payload_json #>>
          '{generation_1,old_job_fingerprint}'
      )::text,
      (
        p_admission_payload_json #>>
          '{generation_1,old_partition_receipt,sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{generation_1,old_suitability_receipt,sha256}'
      )::text,
      0, 0, 0, 0,
      p_admission_payload_json #>>
        '{source_evidence,source_bundle,bundle_id}',
      (
        p_admission_payload_json #>>
          '{source_evidence,source_bundle,manifest_sha256}'
      )::text,
      p_admission_payload_json #>>
        '{source_evidence,corpus_bundle,bundle_id}',
      (
        p_admission_payload_json #>>
          '{source_evidence,corpus_bundle,manifest_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{source_evidence,sealed_packet,manifest_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{source_evidence,sealed_packet,byte_count}'
      )::bigint,
      (
        p_admission_payload_json #>>
          '{source_evidence,sealed_packet,ordered_artifact_sha256_set_digest}'
      )::text,
      true, false, false, false,
      p_admission_payload_json #>>
        '{execution_delta,old_suitability_contract}',
      p_admission_payload_json #>>
        '{execution_delta,new_suitability_contract}',
      p_admission_payload_json #>>
        '{execution_delta,old_fingerprint_domain}',
      p_admission_payload_json #>>
        '{execution_delta,new_fingerprint_domain}',
      (
        p_admission_payload_json #>>
          '{execution_delta,new_job_fingerprint}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,suitability_v2_schema_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,suitability_v2_receipt_sha256}'
      )::text,
      p_admission_payload_json #>>
        '{execution_delta,verifier_contract_version}',
      (
        p_admission_payload_json #>>
          '{execution_delta,verifier_code_commit}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,verifier_config_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,verifier_executable_sha256}'
      )::text,
      p_admission_payload_json #>> '{worker_semantics,prompt_version}',
      (
        p_admission_payload_json #>>
          '{worker_semantics,prompt_sha256}'
      )::text,
      false, true,
      p_admission_payload_json #>>
        '{governance,decision_basis_id}',
      (
        p_admission_payload_json #>>
          '{governance,public_repo_commit}'
      )::text,
      (
        p_admission_payload_json #>>
          '{governance,private_controller_commit}'
      )::text,
      (
        p_admission_payload_json #>>
          '{governance,migration_sha256}'
      )::text,
      p_admission_payload_json #>>
        '{governance,admission_contract_version}',
      p_admission_payload_json #>>
        '{execution_delta,execution_contract_version}',
      (
        p_admission_payload_json #>>
          '{execution_delta,execution_contract_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,route_policy_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{governance,review_decision_receipt_sha256}'
      )::text,
      p_admission_payload_json ->> 'canonical_encoding_contract',
      p_admission_payload_sha256, p_admission_payload_json,
      p_admission_payload_canonical_utf8, p_actor,
      SESSION_USER,
      pg_catalog.current_setting('role', true),
      p_admitted_at
    );

  INSERT INTO
    nhi_rule_history_partition_recovery.partition_suitability_receipt (
      admission_id, decision, designation_candidates,
      effective_designation_candidates, collapsed_parent_designations,
      reason_codes, schema_sha256, receipt_sha256, receipt_contract
    ) VALUES (
      p_admission_id,
      p_admission_payload_json #>>
        '{execution_delta,suitability_preflight,decision}',
      p_admission_payload_json #>
        '{execution_delta,suitability_preflight,designation_candidates}',
      p_admission_payload_json
        #> '{execution_delta,suitability_preflight,effective_designation_candidates}',
      p_admission_payload_json
        #> '{execution_delta,suitability_preflight,collapsed_parent_designations}',
      p_admission_payload_json
        #> '{execution_delta,suitability_preflight,reason_codes}',
      (
        p_admission_payload_json #>>
          '{execution_delta,suitability_v2_schema_sha256}'
      )::text,
      (
        p_admission_payload_json #>>
          '{execution_delta,suitability_v2_receipt_sha256}'
      )::text,
      p_admission_payload_json #>>
        '{execution_delta,new_suitability_contract}'
    );

  RETURN QUERY SELECT
    p_admission_id, p_admission_payload_sha256, false;
END;
$$;

-- Preserve the shared generation table while admitting partition_required as
-- the generation-1 terminal only when a typed A+ authorization already binds
-- the exact work item and generation.
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
  is_partition_authorization boolean;
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

  SELECT EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_authorization
        partition_auth
    WHERE partition_auth.authorization_id = NEW.authorization_id
      AND partition_auth.work_item_id = NEW.work_item_id
      AND partition_auth.new_generation = NEW.generation
  )
  INTO is_partition_authorization;

  SELECT generation INTO latest_generation
  FROM nhi_rule_history_update_queue.work_generation
  WHERE work_item_id = NEW.work_item_id
  ORDER BY generation DESC
  LIMIT 1;

  IF NOT FOUND THEN
    SELECT current_state INTO legacy_state
    FROM nhi_rule_history_update_queue.v_work_item_current
    WHERE work_item_id = NEW.work_item_id;
    IF auth_prior_generation <> 1 OR NEW.generation <> 2
       OR (
         is_partition_authorization
         AND legacy_state IS DISTINCT FROM 'partition_required'
       )
       OR (
         NOT is_partition_authorization
         AND legacy_state IS DISTINCT FROM 'failed_terminal'
       ) THEN
      RAISE EXCEPTION
        'first recovery generation requires its typed immutable generation-1 terminal'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    IF is_partition_authorization THEN
      RAISE EXCEPTION
        'partition recovery admission authorizes generation 2 only'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
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
  nhi_rule_history_partition_recovery.authorize_partition_recovery(
    p_authorization_id uuid,
    p_initial_transition_id uuid,
    p_admission_id uuid,
    p_work_item_id uuid,
    p_prior_generation integer,
    p_new_generation integer,
    p_dispatch_contract_version text,
    p_expires_at timestamptz,
    p_actor text,
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
  admission
    nhi_rule_history_partition_recovery.partition_recovery_admission%ROWTYPE;
  existing
    nhi_rule_history_partition_recovery.
      partition_recovery_authorization%ROWTYPE;
  chain_receipt record;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_prior_generation <> 1
     OR p_new_generation <> 2
     OR COALESCE(
       pg_catalog.btrim(p_dispatch_contract_version), ''
     ) = ''
     OR COALESCE(pg_catalog.btrim(p_actor), '') = ''
     OR p_expires_at <= p_authorized_at THEN
    RAISE EXCEPTION
      'partition recovery authorization fields or expiry are invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO admission
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
  WHERE partition_recovery_admission.admission_id = p_admission_id
  FOR UPDATE;
  IF NOT FOUND
     OR admission.work_item_id IS DISTINCT FROM p_work_item_id
     OR admission.prior_generation <> p_prior_generation THEN
    RAISE EXCEPTION
      'partition recovery authorization does not match its admission'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT * INTO existing
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_authorization
  WHERE partition_recovery_authorization.authorization_id =
    p_authorization_id;
  IF FOUND THEN
    IF existing.admission_id IS DISTINCT FROM p_admission_id
       OR existing.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing.prior_generation <> p_prior_generation
       OR existing.new_generation <> p_new_generation
       OR existing.initial_transition_id IS DISTINCT FROM
         p_initial_transition_id
       OR existing.dispatch_contract_version <>
         p_dispatch_contract_version
       OR existing.expires_at <> p_expires_at
       OR existing.authorized_by_actor <> p_actor THEN
      RAISE EXCEPTION
        'partition recovery authorization identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      existing.authorization_id,
      existing.new_generation,
      existing.initial_transition_id,
      true;
    RETURN;
  END IF;

  SELECT *
  INTO chain_receipt
  FROM
    nhi_rule_history_partition_recovery.generation_one_chain_receipt(
      p_work_item_id
    );
  IF chain_receipt.terminal_state IS DISTINCT FROM 'partition_required'
     OR chain_receipt.terminal_transition_id IS DISTINCT FROM
       admission.terminal_transition_id
     OR chain_receipt.terminal_transition_sequence IS DISTINCT FROM
       admission.terminal_transition_sequence
     OR chain_receipt.terminal_evidence_sha256 IS DISTINCT FROM
       admission.terminal_evidence_sha256
     OR chain_receipt.transition_count IS DISTINCT FROM
       admission.prior_generation_transition_count
     OR NOT
       nhi_rule_history_partition_recovery.
         generation_one_chain_matches_payload(
           p_work_item_id,
           admission.admission_payload_json
             #> '{generation_1,transitions}'
         )
     OR chain_receipt.old_job_fingerprint IS DISTINCT FROM
       admission.old_job_fingerprint
     OR chain_receipt.worker_call_count <> 0
     OR chain_receipt.worker_attempt_count <> 0
     OR chain_receipt.candidate_count <> 0
     OR chain_receipt.route_attempt_count <> 0
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_update_queue.work_generation generation
       WHERE generation.work_item_id = p_work_item_id
     ) THEN
    RAISE EXCEPTION
      'partition recovery authorization detected stale or tampered admission evidence'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  INSERT INTO
    nhi_rule_history_update_queue.work_recovery_authorization (
      authorization_id, work_item_id, prior_generation, new_generation,
      source_bundle_uid, source_manifest_sha256,
      prior_method_version, new_method_version,
      prior_semantic_prompt_fingerprint,
      new_semantic_prompt_fingerprint, decision_basis_id,
      reason, route, authorized_by, authorized_at
    ) VALUES (
      p_authorization_id, p_work_item_id, 1, 2,
      admission.source_bundle_id,
      admission.source_bundle_manifest_sha256,
      admission.old_suitability_contract,
      admission.execution_contract_version,
      admission.prompt_sha256,
      admission.prompt_sha256,
      admission.decision_basis_id,
      'typed zero-call partition recovery admission ' ||
        admission.admission_id::text,
      'primary_then_fallback',
      p_actor,
      p_authorized_at
    );

  INSERT INTO
    nhi_rule_history_partition_recovery.partition_recovery_authorization (
      authorization_id, admission_id, work_item_id, prior_generation,
      new_generation, initial_transition_id, dispatch_contract_version,
      expires_at, authorized_by_actor, authorized_by_session,
      authorized_by_capability, authorized_at
    ) VALUES (
      p_authorization_id, p_admission_id, p_work_item_id, 1, 2,
      p_initial_transition_id, p_dispatch_contract_version,
      p_expires_at, p_actor, SESSION_USER,
      pg_catalog.current_setting('role', true), p_authorized_at
    );

  INSERT INTO nhi_rule_history_update_queue.work_generation (
    work_item_id, generation, authorization_id
  ) VALUES (
    p_work_item_id, 2, p_authorization_id
  );

  INSERT INTO
    nhi_rule_history_update_queue.work_generation_transition (
      work_item_id, generation, transition_seq, transition_id,
      from_state, to_state, actor_kind, source_job_id,
      bundle_receipt_id, candidate_proposal_id, recorded_at
    ) VALUES (
      p_work_item_id, 2, 1, p_initial_transition_id,
      NULL, 'retry_pending', p_actor, NULL, NULL, NULL, p_authorized_at
    );

  INSERT INTO
    nhi_rule_history_partition_recovery.authorization_event (
      authorization_id, event_seq, event_kind, reason, actor,
      actor_session, actor_capability, recorded_at
    ) VALUES (
      p_authorization_id, 1, 'authorized', NULL, p_actor,
      SESSION_USER, pg_catalog.current_setting('role', true),
      p_authorized_at
    );

  INSERT INTO
    nhi_rule_history_partition_recovery.generation_transition_evidence (
      transition_evidence_id, generation_transition_id, evidence_kind,
      evidence_contract, evidence_object_id, evidence_sha256,
      byte_count, logical_locator, ordinal, canonical_payload_sha256,
      created_by_session
    ) VALUES
    (
      p_admission_id, p_initial_transition_id,
      'partition_recovery_admission',
      admission.admission_contract_version,
      p_admission_id::text,
      admission.admission_payload_sha256,
      pg_catalog.octet_length(
        admission.admission_payload_canonical_utf8
      ),
      'partition-recovery/admissions/' || p_admission_id::text,
      1, admission.admission_payload_sha256, SESSION_USER
    ),
    (
      p_authorization_id, p_initial_transition_id,
      'partition_recovery_authorization',
      p_dispatch_contract_version,
      p_authorization_id::text,
      pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            pg_catalog.jsonb_build_object(
              'admission_id', p_admission_id,
              'authorization_id', p_authorization_id,
              'expires_at', p_expires_at,
              'generation', 2,
              'work_item_id', p_work_item_id
            )::text,
            'UTF8'
          )
        ),
        'hex'
      ),
      0,
      'partition-recovery/authorizations/' ||
        p_authorization_id::text,
      1,
      pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            pg_catalog.jsonb_build_object(
              'admission_id', p_admission_id,
              'authorization_id', p_authorization_id,
              'expires_at', p_expires_at,
              'generation', 2,
              'work_item_id', p_work_item_id
            )::text,
            'UTF8'
          )
        ),
        'hex'
      ),
      SESSION_USER
    );

  RETURN QUERY SELECT
    p_authorization_id, 2, p_initial_transition_id, false;
END;
$$;

CREATE OR REPLACE VIEW
  nhi_rule_history_partition_recovery.v_authorization_current AS
SELECT
  auth_row.*,
  terminal_event.event_kind AS terminal_event_kind,
  terminal_event.reason AS terminal_event_reason,
  terminal_event.recorded_at AS terminal_event_at,
  CASE
    WHEN terminal_event.event_kind = 'revoked' THEN 'revoked'
    WHEN terminal_event.event_kind = 'consumed' THEN 'consumed'
    WHEN auth_row.expires_at <= pg_catalog.now() THEN 'expired'
    ELSE 'authorized'
  END AS current_status
FROM
  nhi_rule_history_partition_recovery.partition_recovery_authorization
    auth_row
LEFT JOIN LATERAL (
  SELECT event.event_kind, event.reason, event.recorded_at
  FROM nhi_rule_history_partition_recovery.authorization_event event
  WHERE event.authorization_id = auth_row.authorization_id
    AND event.event_seq = 2
) terminal_event ON true;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.show_partition_recovery(
    p_admission_id uuid,
    p_authorization_id uuid
  )
RETURNS TABLE (
  admission_id uuid,
  work_item_id uuid,
  admission_payload_sha256 nhi_rule_history_update_ops.sha256_hex,
  authorization_id uuid,
  generation integer,
  authorization_status text,
  dispatch_claim_id uuid,
  primary_status text,
  fallback_status text,
  terminal_state text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog
AS $$
BEGIN
  IF (p_admission_id IS NULL) = (p_authorization_id IS NULL) THEN
    RAISE EXCEPTION
      'show partition recovery requires exactly one exact admission or authorization ID'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  SELECT
    admission.admission_id,
    admission.work_item_id,
    admission.admission_payload_sha256,
    auth_row.authorization_id,
    auth_row.new_generation,
    current_auth.current_status,
    claim.claim_id,
    primary_outcome.status,
    fallback_outcome.status,
    terminal.terminal_state
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
      admission
  LEFT JOIN
    nhi_rule_history_partition_recovery.partition_recovery_authorization
      auth_row
    ON auth_row.admission_id = admission.admission_id
  LEFT JOIN
    nhi_rule_history_partition_recovery.v_authorization_current current_auth
    ON current_auth.authorization_id = auth_row.authorization_id
  LEFT JOIN nhi_rule_history_partition_recovery.dispatch_claim claim
    ON claim.authorization_id = auth_row.authorization_id
  LEFT JOIN
    nhi_rule_history_partition_recovery.worker_route_reservation
      primary_reservation
    ON primary_reservation.claim_id = claim.claim_id
   AND primary_reservation.route_ordinal = 1
  LEFT JOIN
    nhi_rule_history_partition_recovery.worker_route_outcome
      primary_outcome
    ON primary_outcome.reservation_id =
      primary_reservation.reservation_id
  LEFT JOIN
    nhi_rule_history_partition_recovery.worker_route_reservation
      fallback_reservation
    ON fallback_reservation.claim_id = claim.claim_id
   AND fallback_reservation.route_ordinal = 2
  LEFT JOIN
    nhi_rule_history_partition_recovery.worker_route_outcome
      fallback_outcome
    ON fallback_outcome.reservation_id =
      fallback_reservation.reservation_id
  LEFT JOIN
    nhi_rule_history_partition_recovery.partition_terminal_receipt terminal
    ON terminal.claim_id = claim.claim_id
  WHERE (
    p_admission_id IS NOT NULL
    AND admission.admission_id = p_admission_id
  ) OR (
    p_authorization_id IS NOT NULL
    AND auth_row.authorization_id = p_authorization_id
  );
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.revoke_partition_recovery(
    p_authorization_id uuid,
    p_reason text,
    p_actor text,
    p_revoked_at timestamptz
  )
RETURNS TABLE (
  authorization_id uuid,
  status text,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  auth_row
    nhi_rule_history_partition_recovery.
      partition_recovery_authorization%ROWTYPE;
  terminal_event
    nhi_rule_history_partition_recovery.authorization_event%ROWTYPE;
BEGIN
  IF COALESCE(pg_catalog.btrim(p_reason), '') = ''
     OR COALESCE(pg_catalog.btrim(p_actor), '') = '' THEN
    RAISE EXCEPTION
      'partition recovery revocation reason and actor are required'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO auth_row
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_authorization
  WHERE partition_recovery_authorization.authorization_id =
    p_authorization_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION
      'partition recovery authorization does not exist'
      USING ERRCODE = 'no_data_found';
  END IF;
  SELECT * INTO terminal_event
  FROM nhi_rule_history_partition_recovery.authorization_event event
  WHERE event.authorization_id = p_authorization_id
    AND event.event_seq = 2;
  IF FOUND THEN
    IF terminal_event.event_kind = 'revoked'
       AND terminal_event.reason = p_reason
       AND terminal_event.actor = p_actor THEN
      RETURN QUERY SELECT p_authorization_id, 'revoked'::text, true;
      RETURN;
    END IF;
    RAISE EXCEPTION
      'consumed or differently revoked authorization cannot be revoked'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  INSERT INTO
    nhi_rule_history_partition_recovery.authorization_event (
      authorization_id, event_seq, event_kind, reason, actor,
      actor_session, actor_capability, recorded_at
    ) VALUES (
      p_authorization_id, 2, 'revoked', p_reason, p_actor,
      SESSION_USER, pg_catalog.current_setting('role', true), p_revoked_at
    );
  RETURN QUERY SELECT p_authorization_id, 'revoked'::text, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      p_claim_id uuid,
      p_work_item_id uuid,
      p_generation integer,
      p_authorization_id uuid,
      p_admission_id uuid,
      p_dispatch_contract_version text,
      p_expected_admission_payload_sha256
        nhi_rule_history_update_ops.sha256_hex,
      p_expected_sealed_packet_manifest_sha256
        nhi_rule_history_update_ops.sha256_hex,
      p_expected_suitability_receipt_sha256
        nhi_rule_history_update_ops.sha256_hex,
      p_expected_job_fingerprint
        nhi_rule_history_update_ops.sha256_hex,
      p_expected_prompt_sha256
        nhi_rule_history_update_ops.sha256_hex,
      p_expected_route_policy_sha256
        nhi_rule_history_update_ops.sha256_hex,
      p_recovery_job_id uuid,
      p_lease_id uuid,
      p_owner_key text,
      p_max_runtime_seconds integer,
      p_lease_expires_at timestamptz,
      p_consumed_at timestamptz
    )
RETURNS TABLE (
  claim_id uuid,
  work_item_id uuid,
  generation integer,
  authorization_id uuid,
  admission_id uuid,
  dispatch_contract_version text,
  admission_payload_sha256 nhi_rule_history_update_ops.sha256_hex,
  sealed_packet_manifest_sha256 nhi_rule_history_update_ops.sha256_hex,
  suitability_receipt_sha256 nhi_rule_history_update_ops.sha256_hex,
  job_fingerprint nhi_rule_history_update_ops.sha256_hex,
  prompt_sha256 nhi_rule_history_update_ops.sha256_hex,
  route_policy_sha256 nhi_rule_history_update_ops.sha256_hex,
  source_job_id uuid,
  lease_id uuid,
  owner_key text,
  max_runtime_seconds integer,
  lease_expires_at timestamptz,
  replayed boolean,
  generation_state text,
  open_reservation_id uuid,
  open_route_ordinal smallint,
  open_attempt_namespace text,
  finished_route_count integer,
  finished_route_statuses jsonb,
  terminal_state text,
  terminal_receipt_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  auth_row
    nhi_rule_history_partition_recovery.
      partition_recovery_authorization%ROWTYPE;
  admission
    nhi_rule_history_partition_recovery.partition_recovery_admission%ROWTYPE;
  suitability
    nhi_rule_history_partition_recovery.partition_suitability_receipt%ROWTYPE;
  existing_claim
    nhi_rule_history_partition_recovery.dispatch_claim%ROWTYPE;
  prior_job nhi_rule_history_update_ops.update_job%ROWTYPE;
  open_reservation
    nhi_rule_history_partition_recovery.worker_route_reservation%ROWTYPE;
  terminal_receipt
    nhi_rule_history_partition_recovery.partition_terminal_receipt%ROWTYPE;
  current_state text;
  terminal_event text;
  completed_route_count integer;
  completed_route_statuses jsonb;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_recovery_job_id IS NULL
     OR p_lease_id IS NULL
     OR COALESCE(pg_catalog.btrim(p_owner_key), '') = ''
     OR p_max_runtime_seconds NOT BETWEEN 1 AND 21600 THEN
    RAISE EXCEPTION
      'dispatch consume requires a bounded exact recovery job lease'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO auth_row
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_authorization
  WHERE partition_recovery_authorization.authorization_id =
    p_authorization_id
  FOR UPDATE;
  IF NOT FOUND
     OR auth_row.admission_id IS DISTINCT FROM p_admission_id
     OR auth_row.work_item_id IS DISTINCT FROM p_work_item_id
     OR auth_row.new_generation IS DISTINCT FROM p_generation
     OR auth_row.dispatch_contract_version IS DISTINCT FROM
       p_dispatch_contract_version THEN
    RAISE EXCEPTION
      'dispatch consume requires the exact partition authorization tuple'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT * INTO admission
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
  WHERE partition_recovery_admission.admission_id = p_admission_id;
  SELECT * INTO suitability
  FROM
    nhi_rule_history_partition_recovery.partition_suitability_receipt
  WHERE partition_suitability_receipt.admission_id = p_admission_id;

  SELECT event.event_kind INTO terminal_event
  FROM nhi_rule_history_partition_recovery.authorization_event event
  WHERE event.authorization_id = p_authorization_id
    AND event.event_seq = 2;

  SELECT transition.to_state
  INTO current_state
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = p_work_item_id
    AND transition.generation = p_generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1;

  SELECT * INTO existing_claim
  FROM nhi_rule_history_partition_recovery.dispatch_claim claim
  WHERE claim.claim_id = p_claim_id
     OR claim.authorization_id = p_authorization_id
  LIMIT 1;
  IF FOUND THEN
    IF existing_claim.claim_id IS DISTINCT FROM p_claim_id
       OR existing_claim.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing_claim.generation IS DISTINCT FROM p_generation
       OR existing_claim.authorization_id IS DISTINCT FROM
         p_authorization_id
       OR existing_claim.admission_id IS DISTINCT FROM p_admission_id
       OR existing_claim.dispatch_contract_version IS DISTINCT FROM
         p_dispatch_contract_version
       OR existing_claim.admission_payload_sha256 IS DISTINCT FROM
         p_expected_admission_payload_sha256
       OR existing_claim.sealed_packet_manifest_sha256 IS DISTINCT FROM
         p_expected_sealed_packet_manifest_sha256
       OR existing_claim.suitability_receipt_sha256 IS DISTINCT FROM
         p_expected_suitability_receipt_sha256
       OR existing_claim.job_fingerprint IS DISTINCT FROM
         p_expected_job_fingerprint
       OR existing_claim.prompt_sha256 IS DISTINCT FROM
         p_expected_prompt_sha256
       OR existing_claim.route_policy_sha256 IS DISTINCT FROM
         p_expected_route_policy_sha256
       OR existing_claim.source_job_id IS DISTINCT FROM
         p_recovery_job_id
       OR existing_claim.lease_id IS DISTINCT FROM p_lease_id
       OR existing_claim.owner_key IS DISTINCT FROM p_owner_key
       OR existing_claim.max_runtime_seconds IS DISTINCT FROM
         p_max_runtime_seconds THEN
      RAISE EXCEPTION
        'dispatch claim or authorization is already consumed with different material'
        USING ERRCODE = 'unique_violation';
    END IF;
    SELECT reservation.*
    INTO open_reservation
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
        reservation
    LEFT JOIN
      nhi_rule_history_partition_recovery.worker_route_outcome outcome
      ON outcome.reservation_id = reservation.reservation_id
    WHERE reservation.claim_id = existing_claim.claim_id
      AND outcome.reservation_id IS NULL
    ORDER BY reservation.route_ordinal
    LIMIT 1;
    SELECT
      pg_catalog.count(*)::integer,
      COALESCE(
        pg_catalog.jsonb_agg(
          pg_catalog.jsonb_build_object(
            'reservation_id', reservation.reservation_id,
            'route_ordinal', reservation.route_ordinal,
            'route', reservation.route,
            'status', outcome.status,
            'failure_class', outcome.failure_class
          )
          ORDER BY reservation.route_ordinal
        ),
        '[]'::jsonb
      )
    INTO completed_route_count, completed_route_statuses
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
        reservation
    JOIN
      nhi_rule_history_partition_recovery.worker_route_outcome outcome
      ON outcome.reservation_id = reservation.reservation_id
    WHERE reservation.claim_id = existing_claim.claim_id;
    SELECT terminal.*
    INTO terminal_receipt
    FROM
      nhi_rule_history_partition_recovery.partition_terminal_receipt
        terminal
    WHERE terminal.claim_id = existing_claim.claim_id;
    RETURN QUERY SELECT
      existing_claim.claim_id, existing_claim.work_item_id,
      existing_claim.generation, existing_claim.authorization_id,
      existing_claim.admission_id,
      existing_claim.dispatch_contract_version,
      existing_claim.admission_payload_sha256,
      existing_claim.sealed_packet_manifest_sha256,
      existing_claim.suitability_receipt_sha256,
      existing_claim.job_fingerprint, existing_claim.prompt_sha256,
      existing_claim.route_policy_sha256,
      existing_claim.source_job_id, existing_claim.lease_id,
      existing_claim.owner_key, existing_claim.max_runtime_seconds,
      existing_claim.lease_expires_at, true, current_state,
      open_reservation.reservation_id,
      open_reservation.route_ordinal,
      open_reservation.attempt_namespace,
      completed_route_count, completed_route_statuses,
      terminal_receipt.terminal_state,
      terminal_receipt.terminal_receipt_id;
    RETURN;
  END IF;

  IF p_generation <> 2
     OR current_state IS DISTINCT FROM 'retry_pending'
     OR terminal_event IS NOT NULL
     OR auth_row.expires_at <= p_consumed_at
     OR p_lease_expires_at <= p_consumed_at
     OR p_lease_expires_at > auth_row.expires_at
     OR p_lease_expires_at >
       p_consumed_at + p_max_runtime_seconds * interval '1 second'
     OR admission.admission_payload_sha256 IS DISTINCT FROM
       p_expected_admission_payload_sha256
     OR admission.sealed_packet_manifest_sha256 IS DISTINCT FROM
       p_expected_sealed_packet_manifest_sha256
     OR suitability.receipt_sha256 IS DISTINCT FROM
       p_expected_suitability_receipt_sha256
     OR admission.new_job_fingerprint IS DISTINCT FROM
       p_expected_job_fingerprint
     OR admission.prompt_sha256 IS DISTINCT FROM
       p_expected_prompt_sha256
     OR admission.route_policy_sha256 IS DISTINCT FROM
       p_expected_route_policy_sha256 THEN
    RAISE EXCEPTION
      'dispatch authorization is expired, revoked, consumed, stale, or hash-mismatched'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT job.*
  INTO prior_job
  FROM nhi_rule_history_update_ops.update_job job
  JOIN nhi_rule_history_update_queue.work_item_transition transition
    ON transition.source_job_id = job.job_id
  WHERE transition.work_item_id = p_work_item_id
    AND transition.transition_id = admission.terminal_transition_id
    AND job.job_fingerprint = admission.old_job_fingerprint;
  IF NOT FOUND THEN
    RAISE EXCEPTION
      'dispatch consume cannot resolve the exact generation-1 source job'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  INSERT INTO nhi_rule_history_update_ops.update_job (
    job_id, job_fingerprint, contract_version, runner_version,
    feed_url, request_profile_sha256, notification_window_start,
    notification_window_end, activation_cut, scheduled_at, created_at
  ) VALUES (
    p_recovery_job_id, p_expected_job_fingerprint,
    admission.execution_contract_version, p_dispatch_contract_version,
    prior_job.feed_url, prior_job.request_profile_sha256,
    prior_job.notification_window_start,
    prior_job.notification_window_end, prior_job.activation_cut,
    p_consumed_at, p_consumed_at
  );
  INSERT INTO nhi_rule_history_update_ops.job_lease (
    lease_id, job_id, owner_key, acquired_at, expires_at,
    max_runtime_seconds, created_at
  ) VALUES (
    p_lease_id, p_recovery_job_id, p_owner_key, p_consumed_at,
    p_lease_expires_at, p_max_runtime_seconds, p_consumed_at
  );

  INSERT INTO nhi_rule_history_partition_recovery.dispatch_claim (
    claim_id, authorization_id, admission_id, work_item_id, generation,
    dispatch_contract_version, admission_payload_sha256,
    sealed_packet_manifest_sha256, suitability_receipt_sha256,
    job_fingerprint, prompt_sha256, route_policy_sha256,
    source_job_id, lease_id, owner_key, max_runtime_seconds,
    lease_expires_at,
    claimed_by_session, claimed_by_capability, consumed_at
  ) VALUES (
    p_claim_id, p_authorization_id, p_admission_id, p_work_item_id,
    p_generation, p_dispatch_contract_version,
    p_expected_admission_payload_sha256,
    p_expected_sealed_packet_manifest_sha256,
    p_expected_suitability_receipt_sha256,
    p_expected_job_fingerprint, p_expected_prompt_sha256,
    p_expected_route_policy_sha256, p_recovery_job_id, p_lease_id,
    p_owner_key, p_max_runtime_seconds, p_lease_expires_at, SESSION_USER,
    pg_catalog.current_setting('role', true), p_consumed_at
  );
  INSERT INTO
    nhi_rule_history_partition_recovery.authorization_event (
      authorization_id, event_seq, event_kind, reason, actor,
      actor_session, actor_capability, recorded_at
    ) VALUES (
      p_authorization_id, 2, 'consumed', NULL, 'runtime-dispatch',
      SESSION_USER, pg_catalog.current_setting('role', true), p_consumed_at
    );
  INSERT INTO
    nhi_rule_history_partition_recovery.generation_transition_evidence (
      transition_evidence_id, generation_transition_id, evidence_kind,
      evidence_contract, evidence_object_id, evidence_sha256,
      byte_count, logical_locator, ordinal, canonical_payload_sha256,
      created_by_session
    ) VALUES (
      p_claim_id, auth_row.initial_transition_id, 'dispatch_claim',
      p_dispatch_contract_version, p_claim_id::text,
      p_expected_admission_payload_sha256, 0,
      'partition-recovery/dispatch-claims/' || p_claim_id::text,
      1, p_expected_admission_payload_sha256, SESSION_USER
    );

  RETURN QUERY SELECT
    p_claim_id, p_work_item_id, p_generation, p_authorization_id,
    p_admission_id, p_dispatch_contract_version,
    p_expected_admission_payload_sha256,
    p_expected_sealed_packet_manifest_sha256,
    p_expected_suitability_receipt_sha256,
    p_expected_job_fingerprint, p_expected_prompt_sha256,
    p_expected_route_policy_sha256, p_recovery_job_id, p_lease_id,
    p_owner_key, p_max_runtime_seconds, p_lease_expires_at,
    false, current_state, NULL::uuid, NULL::smallint, NULL::text,
    0, '[]'::jsonb, NULL::text, NULL::uuid;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      p_reservation_id uuid,
      p_claim_id uuid,
      p_work_item_id uuid,
      p_generation integer,
      p_authorization_id uuid,
      p_admission_id uuid,
      p_route_ordinal smallint,
      p_packet_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_prompt_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_attempt_namespace text,
      p_source_job_id uuid,
      p_lease_id uuid,
      p_owner_key text,
      p_runtime text,
      p_provider text,
      p_model text,
      p_controller_commit nhi_rule_history_update_ops.sha256_hex,
      p_reserved_at timestamptz
    )
RETURNS TABLE (
  reservation_id uuid,
  route_ordinal smallint,
  route text,
  attempt_namespace text,
  source_job_id uuid,
  lease_id uuid,
  owner_key text,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim nhi_rule_history_partition_recovery.dispatch_claim%ROWTYPE;
  existing
    nhi_rule_history_partition_recovery.worker_route_reservation%ROWTYPE;
  primary_reservation
    nhi_rule_history_partition_recovery.worker_route_reservation%ROWTYPE;
  primary_outcome
    nhi_rule_history_partition_recovery.worker_route_outcome%ROWTYPE;
  current_transition record;
  source_job_fingerprint text;
  expected_namespace text;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  IF p_route_ordinal NOT IN (1, 2)
     OR COALESCE(pg_catalog.btrim(p_runtime), '') = ''
     OR COALESCE(pg_catalog.btrim(p_provider), '') = ''
     OR COALESCE(pg_catalog.btrim(p_model), '') = ''
     OR COALESCE(
       pg_catalog.btrim(p_attempt_namespace), ''
     ) = '' THEN
    RAISE EXCEPTION
      'partition route reservation fields are invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO claim
  FROM nhi_rule_history_partition_recovery.dispatch_claim
  WHERE dispatch_claim.claim_id = p_claim_id
  FOR UPDATE;
  IF NOT FOUND
     OR claim.work_item_id IS DISTINCT FROM p_work_item_id
     OR claim.generation IS DISTINCT FROM p_generation
     OR claim.authorization_id IS DISTINCT FROM p_authorization_id
     OR claim.admission_id IS DISTINCT FROM p_admission_id
     OR claim.prompt_sha256 IS DISTINCT FROM p_prompt_sha256
     OR claim.source_job_id IS DISTINCT FROM p_source_job_id
     OR claim.lease_id IS DISTINCT FROM p_lease_id
     OR claim.owner_key IS DISTINCT FROM p_owner_key THEN
    RAISE EXCEPTION
      'route reservation requires the exact consumed dispatch tuple'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT job.job_fingerprint
  INTO source_job_fingerprint
  FROM nhi_rule_history_update_ops.update_job job
  WHERE job.job_id = p_source_job_id;
  expected_namespace :=
    'partition-recovery/' || p_work_item_id::text ||
    '/generation-' || p_generation::text || '/' ||
    claim.job_fingerprint::text;
  IF source_job_fingerprint IS DISTINCT FROM claim.job_fingerprint::text
     OR p_attempt_namespace IS DISTINCT FROM expected_namespace THEN
    RAISE EXCEPTION
      'route source job or attempt namespace does not match the generation-bound dispatch'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT * INTO existing
  FROM
    nhi_rule_history_partition_recovery.worker_route_reservation
      reservation
  WHERE reservation.reservation_id = p_reservation_id
     OR (
       reservation.claim_id = p_claim_id
       AND reservation.route_ordinal = p_route_ordinal
     )
  LIMIT 1;
  IF FOUND THEN
    IF existing.reservation_id IS DISTINCT FROM p_reservation_id
       OR existing.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing.generation IS DISTINCT FROM p_generation
       OR existing.authorization_id IS DISTINCT FROM p_authorization_id
       OR existing.admission_id IS DISTINCT FROM p_admission_id
       OR existing.route_ordinal IS DISTINCT FROM p_route_ordinal
       OR existing.packet_sha256 IS DISTINCT FROM p_packet_sha256
       OR existing.prompt_sha256 IS DISTINCT FROM p_prompt_sha256
       OR existing.attempt_namespace IS DISTINCT FROM
         p_attempt_namespace
       OR existing.source_job_id IS DISTINCT FROM p_source_job_id
       OR existing.runtime IS DISTINCT FROM p_runtime
       OR existing.provider IS DISTINCT FROM p_provider
       OR existing.model IS DISTINCT FROM p_model
       OR existing.controller_commit IS DISTINCT FROM
         p_controller_commit THEN
      RAISE EXCEPTION
        'partition recovery route is already reserved with different material'
        USING ERRCODE = 'unique_violation';
    END IF;
    RETURN QUERY SELECT
      existing.reservation_id, existing.route_ordinal, existing.route,
      existing.attempt_namespace, existing.source_job_id,
      claim.lease_id, claim.owner_key, true;
    RETURN;
  END IF;

  IF claim.lease_expires_at <= p_reserved_at
     OR NOT EXISTS (
       SELECT 1
       FROM nhi_rule_history_update_ops.job_lease lease
       WHERE lease.job_id = claim.source_job_id
         AND lease.lease_id = claim.lease_id
         AND lease.owner_key = claim.owner_key
         AND lease.max_runtime_seconds = claim.max_runtime_seconds
         AND lease.expires_at = claim.lease_expires_at
     ) THEN
    RAISE EXCEPTION
      'fresh route reservation requires the exact unexpired recovery lease'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_terminal_receipt
        terminal
    WHERE terminal.claim_id = p_claim_id
  ) THEN
    RAISE EXCEPTION
      'terminal partition recovery rejects new route reservation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT transition.*
  INTO current_transition
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = p_work_item_id
    AND transition.generation = p_generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF p_route_ordinal = 1 THEN
    IF current_transition.to_state IS DISTINCT FROM 'retry_pending' THEN
      RAISE EXCEPTION
        'primary route reservation requires retry_pending'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    SELECT * INTO primary_reservation
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
    WHERE worker_route_reservation.claim_id = p_claim_id
      AND worker_route_reservation.route_ordinal = 1;
    SELECT * INTO primary_outcome
    FROM nhi_rule_history_partition_recovery.worker_route_outcome
    WHERE worker_route_outcome.reservation_id =
      primary_reservation.reservation_id;
    IF current_transition.to_state IS DISTINCT FROM 'proposal_running'
       OR NOT FOUND
       OR primary_outcome.status IS DISTINCT FROM 'failed'
       OR primary_outcome.failure_class NOT IN (
         'transport_failure', 'execution_failure', 'timeout',
         'process_exit_failure', 'invalid_json',
         'output_schema_invalid', 'unknown_enum', 'missing_locator',
         'locator_mismatch', 'source_text_mismatch',
         'output_contract_inconsistent'
       )
       OR primary_reservation.packet_sha256 IS DISTINCT FROM
         p_packet_sha256
       OR primary_reservation.prompt_sha256 IS DISTINCT FROM
         p_prompt_sha256
       OR primary_reservation.attempt_namespace IS DISTINCT FROM
         p_attempt_namespace
       OR primary_reservation.source_job_id IS DISTINCT FROM
         p_source_job_id THEN
      RAISE EXCEPTION
        'fallback requires one typed allowlisted terminal primary failure and identical input'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  INSERT INTO
    nhi_rule_history_partition_recovery.worker_route_reservation (
      reservation_id, claim_id, work_item_id, generation,
      authorization_id, admission_id, route_ordinal, source_job_id,
      packet_sha256, prompt_sha256, attempt_namespace, runtime,
      provider, model, controller_commit, reserved_by_session,
      reserved_by_capability, reserved_at
    ) VALUES (
      p_reservation_id, p_claim_id, p_work_item_id, p_generation,
      p_authorization_id, p_admission_id, p_route_ordinal,
      p_source_job_id, p_packet_sha256, p_prompt_sha256,
      p_attempt_namespace, p_runtime, p_provider, p_model,
      p_controller_commit, SESSION_USER,
      pg_catalog.current_setting('role', true), p_reserved_at
    );

  IF p_route_ordinal = 1 THEN
    INSERT INTO
      nhi_rule_history_update_queue.work_generation_transition (
        work_item_id, generation, transition_seq, transition_id,
        from_state, to_state, actor_kind, source_job_id,
        bundle_receipt_id, candidate_proposal_id, recorded_at
      ) VALUES (
        p_work_item_id, p_generation,
        current_transition.transition_seq + 1, p_reservation_id,
        'retry_pending', 'proposal_running', 'runtime-route-reservation',
        p_source_job_id, NULL, NULL, p_reserved_at
      );
    INSERT INTO
      nhi_rule_history_partition_recovery.generation_transition_evidence (
        transition_evidence_id, generation_transition_id,
        evidence_kind, evidence_contract, evidence_object_id,
        evidence_sha256, byte_count, logical_locator, ordinal,
        canonical_payload_sha256, created_by_session
      ) VALUES (
        p_reservation_id, p_reservation_id, 'route_reservation',
        'nhi-rule-history/partition-route-reservation/v1',
        p_reservation_id::text, p_packet_sha256, 0,
        'partition-recovery/routes/' || p_reservation_id::text,
        1, p_packet_sha256, SESSION_USER
      );
  END IF;

  RETURN QUERY SELECT
    p_reservation_id, p_route_ordinal,
    CASE p_route_ordinal WHEN 1 THEN 'primary' ELSE 'fallback' END,
    p_attempt_namespace, p_source_job_id, claim.lease_id,
    claim.owner_key, false;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      p_reservation_id uuid,
      p_claim_id uuid,
      p_work_item_id uuid,
      p_generation integer,
      p_authorization_id uuid,
      p_admission_id uuid,
      p_status text,
      p_failure_class text,
      p_worker_attempt_id uuid,
      p_stdout_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_stderr_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_output_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_process_exit_code integer,
      p_timed_out boolean,
      p_receipt_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_receipt_json jsonb,
      p_finished_at timestamptz
    )
RETURNS TABLE (
  reservation_id uuid,
  route_ordinal smallint,
  route text,
  status text,
  failure_class text,
  worker_attempt_id uuid,
  fallback_eligible boolean,
  replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  reservation
    nhi_rule_history_partition_recovery.worker_route_reservation%ROWTYPE;
  existing
    nhi_rule_history_partition_recovery.worker_route_outcome%ROWTYPE;
  primary_reservation
    nhi_rule_history_partition_recovery.worker_route_reservation%ROWTYPE;
  primary_outcome
    nhi_rule_history_partition_recovery.worker_route_outcome%ROWTYPE;
  claim nhi_rule_history_partition_recovery.dispatch_claim%ROWTYPE;
  receipt_lease_id uuid;
  receipt_owner_key text;
  started_at timestamptz;
  receipt_completed_at timestamptz;
  primary_attempt_id uuid;
  worker_status text;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  SELECT * INTO reservation
  FROM
    nhi_rule_history_partition_recovery.worker_route_reservation
  WHERE worker_route_reservation.reservation_id = p_reservation_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.claim_id IS DISTINCT FROM p_claim_id
     OR reservation.work_item_id IS DISTINCT FROM p_work_item_id
     OR reservation.generation IS DISTINCT FROM p_generation
     OR reservation.authorization_id IS DISTINCT FROM
       p_authorization_id
     OR reservation.admission_id IS DISTINCT FROM p_admission_id THEN
    RAISE EXCEPTION
      'route outcome requires the exact durable reservation tuple'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT * INTO claim
  FROM nhi_rule_history_partition_recovery.dispatch_claim
  WHERE dispatch_claim.claim_id = p_claim_id;
  IF NOT FOUND
     OR claim.source_job_id IS DISTINCT FROM reservation.source_job_id THEN
    RAISE EXCEPTION
      'route outcome cannot resolve its exact recovery job lease'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF pg_catalog.jsonb_typeof(p_receipt_json) IS DISTINCT FROM 'object'
     OR p_receipt_json = '{}'::jsonb THEN
    RAISE EXCEPTION
      'route outcome requires a typed nonempty receipt object'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO existing
  FROM nhi_rule_history_partition_recovery.worker_route_outcome
  WHERE worker_route_outcome.reservation_id = p_reservation_id;
  IF FOUND THEN
    IF existing.status IS DISTINCT FROM p_status
       OR existing.failure_class IS DISTINCT FROM (
         CASE
           WHEN p_status = 'execution_unknown' THEN 'execution_unknown'
           ELSE p_failure_class
         END
       )
       OR existing.attempt_id IS DISTINCT FROM p_worker_attempt_id
       OR existing.candidate_proposal_id IS NOT NULL
       OR existing.stdout_sha256 IS DISTINCT FROM p_stdout_sha256
       OR existing.stderr_sha256 IS DISTINCT FROM p_stderr_sha256
       OR existing.output_sha256 IS DISTINCT FROM p_output_sha256
       OR existing.process_exit_code IS DISTINCT FROM
         p_process_exit_code
       OR existing.timed_out IS DISTINCT FROM p_timed_out
       OR existing.receipt_sha256 IS DISTINCT FROM p_receipt_sha256
       OR existing.receipt_json IS DISTINCT FROM p_receipt_json THEN
      RAISE EXCEPTION
        'route outcome identifier was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      reservation.reservation_id, reservation.route_ordinal,
      reservation.route, existing.status, existing.failure_class,
      existing.attempt_id,
      (
        reservation.route_ordinal = 1
        AND existing.status = 'failed'
        AND existing.failure_class IN (
          'transport_failure', 'execution_failure', 'timeout',
          'process_exit_failure', 'invalid_json',
          'output_schema_invalid', 'unknown_enum', 'missing_locator',
          'locator_mismatch', 'source_text_mismatch',
          'output_contract_inconsistent'
        )
      ),
      true;
    RETURN;
  END IF;

  IF p_status NOT IN ('succeeded', 'failed', 'execution_unknown')
     OR (
       p_status = 'failed'
       AND p_failure_class NOT IN (
         'transport_failure', 'execution_failure', 'timeout',
         'process_exit_failure', 'invalid_json',
         'output_schema_invalid', 'unknown_enum', 'missing_locator',
         'locator_mismatch', 'source_text_mismatch',
         'output_contract_inconsistent'
       )
     )
     OR (
       p_status <> 'failed'
       AND p_failure_class IS NOT NULL
     )
     OR (
       p_status = 'execution_unknown'
       AND (
         p_worker_attempt_id IS NOT NULL
         OR p_output_sha256 IS NOT NULL
       )
     ) THEN
    RAISE EXCEPTION
      'route outcome status, failure class, or execution evidence is invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  IF p_status = 'execution_unknown' THEN
    INSERT INTO
      nhi_rule_history_partition_recovery.worker_route_outcome (
        reservation_id, status, failure_class, attempt_id,
        source_job_id, candidate_proposal_id, stdout_sha256,
        stderr_sha256, output_sha256, process_exit_code, timed_out,
        receipt_sha256, receipt_json, finished_by_session,
        finished_by_capability, finished_at
      ) VALUES (
        p_reservation_id, 'execution_unknown', 'execution_unknown',
        NULL, NULL, NULL, p_stdout_sha256, p_stderr_sha256, NULL,
        p_process_exit_code, false, p_receipt_sha256, p_receipt_json,
        SESSION_USER, pg_catalog.current_setting('role', true),
        p_finished_at
      );
  ELSE
    receipt_lease_id := (p_receipt_json ->> 'lease_id')::uuid;
    receipt_owner_key := p_receipt_json ->> 'owner_key';
    started_at := (p_receipt_json ->> 'started_at')::timestamptz;
    receipt_completed_at :=
      (p_receipt_json ->> 'completed_at')::timestamptz;
    IF COALESCE(pg_catalog.btrim(receipt_owner_key), '') = ''
       OR receipt_lease_id IS DISTINCT FROM claim.lease_id
       OR receipt_owner_key IS DISTINCT FROM claim.owner_key
       OR receipt_completed_at IS DISTINCT FROM p_finished_at
       OR receipt_completed_at < started_at
       OR p_receipt_json ->> 'attempt_namespace' IS DISTINCT FROM
         reservation.attempt_namespace
       OR p_receipt_json ->> 'raw_worker_attempt_id' !~
         '^[0-9a-f]{64}$'
       OR NOT EXISTS (
         SELECT 1
         FROM nhi_rule_history_update_ops.job_lease lease
         WHERE lease.job_id = reservation.source_job_id
           AND lease.lease_id = receipt_lease_id
           AND lease.owner_key = receipt_owner_key
       ) THEN
      RAISE EXCEPTION
        'route receipt lease, namespace, raw attempt, or timestamps do not match'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF p_worker_attempt_id IS NULL THEN
      RAISE EXCEPTION
        'known worker execution requires a generation-bound attempt UUID'
        USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF reservation.route_ordinal = 2 THEN
      SELECT * INTO primary_reservation
      FROM
        nhi_rule_history_partition_recovery.worker_route_reservation
      WHERE worker_route_reservation.claim_id = p_claim_id
        AND worker_route_reservation.route_ordinal = 1;
      SELECT * INTO primary_outcome
      FROM nhi_rule_history_partition_recovery.worker_route_outcome
      WHERE worker_route_outcome.reservation_id =
        primary_reservation.reservation_id;
      IF NOT FOUND
         OR primary_outcome.status IS DISTINCT FROM 'failed'
         OR primary_outcome.failure_class NOT IN (
           'transport_failure', 'execution_failure', 'timeout',
           'process_exit_failure', 'invalid_json',
           'output_schema_invalid', 'unknown_enum', 'missing_locator',
           'locator_mismatch', 'source_text_mismatch',
           'output_contract_inconsistent'
         ) THEN
        RAISE EXCEPTION
          'fallback outcome requires its allowlisted failed primary'
          USING ERRCODE = 'object_not_in_prerequisite_state';
      END IF;
      primary_attempt_id := primary_outcome.attempt_id;
    END IF;
    worker_status := CASE p_status
      WHEN 'succeeded' THEN 'success'
      ELSE 'failed'
    END;
    INSERT INTO nhi_rule_history_update_ops.worker_attempt (
      attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
      primary_attempt_id, provider, runtime, model, prompt_sha256,
      output_sha256, started_at, completed_at, status, failure_code,
      fallback_reason
    ) VALUES (
      p_worker_attempt_id, reservation.source_job_id,
      receipt_lease_id, receipt_owner_key,
      reservation.route_ordinal, reservation.route,
      primary_attempt_id, reservation.provider, reservation.runtime,
      reservation.model, reservation.prompt_sha256, p_output_sha256,
      started_at, p_finished_at, worker_status,
      CASE WHEN p_status = 'failed' THEN p_failure_class END,
      CASE
        WHEN reservation.route_ordinal = 2
        THEN primary_outcome.failure_class
      END
    );

    INSERT INTO nhi_rule_history_update_queue.recovery_route_attempt (
      work_item_id, generation, route, attempt_id, source_job_id,
      method_version, semantic_prompt_fingerprint, recorded_at
    )
    SELECT
      reservation.work_item_id, reservation.generation,
      reservation.route, p_worker_attempt_id,
      reservation.source_job_id, admission.execution_contract_version,
      admission.prompt_sha256, p_finished_at
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_admission
        admission
    WHERE admission.admission_id = reservation.admission_id;

    INSERT INTO
      nhi_rule_history_partition_recovery.worker_route_outcome (
        reservation_id, status, failure_class, attempt_id,
        source_job_id, candidate_proposal_id, stdout_sha256,
        stderr_sha256, output_sha256, process_exit_code, timed_out,
        receipt_sha256, receipt_json, finished_by_session,
        finished_by_capability, finished_at
      ) VALUES (
        p_reservation_id, p_status, p_failure_class,
        p_worker_attempt_id, reservation.source_job_id,
        NULL, p_stdout_sha256, p_stderr_sha256,
        p_output_sha256, p_process_exit_code, p_timed_out,
        p_receipt_sha256, p_receipt_json, SESSION_USER,
        pg_catalog.current_setting('role', true), p_finished_at
      );
  END IF;

  RETURN QUERY SELECT
    reservation.reservation_id, reservation.route_ordinal,
    reservation.route, p_status,
    CASE
      WHEN p_status = 'execution_unknown' THEN 'execution_unknown'
      ELSE p_failure_class
    END,
    p_worker_attempt_id,
    (
      reservation.route_ordinal = 1
      AND p_status = 'failed'
      AND p_failure_class IN (
        'transport_failure', 'execution_failure', 'timeout',
        'process_exit_failure', 'invalid_json',
        'output_schema_invalid', 'unknown_enum', 'missing_locator',
        'locator_mismatch', 'source_text_mismatch',
        'output_contract_inconsistent'
      )
    ),
    false;
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
  successful_routes integer;
  failed_routes integer;
  route_count integer;
  is_partition_generation boolean;
  typed_terminal
    nhi_rule_history_partition_recovery.partition_terminal_receipt%ROWTYPE;
  outcome_count integer;
  succeeded_outcomes integer;
  failed_outcomes integer;
  unknown_outcomes integer;
  total_reservations integer;
  fallback_reservations integer;
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
  SELECT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_generation generation
    JOIN
      nhi_rule_history_partition_recovery.
        partition_recovery_authorization partition_auth
      ON partition_auth.authorization_id = generation.authorization_id
    WHERE generation.work_item_id = NEW.work_item_id
      AND generation.generation = NEW.generation
  )
  INTO is_partition_generation;

  SELECT transition_seq, to_state, recorded_at, source_job_id
  INTO prior_seq, prior_state, prior_recorded_at, prior_source_job_id
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
    'staged_needs_review', 'staged_pending_anchor',
    'failed_terminal', 'partition_required'
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

  IF is_partition_generation THEN
    IF NEW.to_state = 'proposal_running' THEN
      IF prior_state <> 'retry_pending'
         OR NEW.source_job_id IS NULL
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'partition proposal_running requires one pre-call primary reservation job'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      RETURN NEW;
    END IF;
    IF NEW.to_state NOT IN (
      'staged_needs_review', 'partition_required', 'failed_terminal'
    ) THEN
      RAISE EXCEPTION
        'partition recovery transition edge is not allowlisted'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    SELECT * INTO typed_terminal
    FROM
      nhi_rule_history_partition_recovery.partition_terminal_receipt
    WHERE partition_terminal_receipt.terminal_transition_id =
      NEW.transition_id;
    IF NOT FOUND
       OR typed_terminal.work_item_id IS DISTINCT FROM NEW.work_item_id
       OR typed_terminal.generation IS DISTINCT FROM NEW.generation
       OR typed_terminal.terminal_state IS DISTINCT FROM NEW.to_state THEN
      RAISE EXCEPTION
        'partition terminal transition requires its exact typed receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.to_state = 'partition_required' THEN
      RAISE EXCEPTION
        'reviewed-suitable partition admission cannot close as partition_required'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF prior_state = 'proposal_running'
       AND NEW.source_job_id IS DISTINCT FROM prior_source_job_id THEN
      RAISE EXCEPTION
        'partition terminal transition must retain its reserved execution job'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT
      pg_catalog.count(*)::integer,
      pg_catalog.count(*) FILTER (
        WHERE outcome.status = 'succeeded'
      )::integer,
      pg_catalog.count(*) FILTER (
        WHERE outcome.status = 'failed'
      )::integer,
      pg_catalog.count(*) FILTER (
        WHERE outcome.status = 'execution_unknown'
      )::integer
    INTO
      outcome_count, succeeded_outcomes,
      failed_outcomes, unknown_outcomes
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
        reservation
    JOIN
      nhi_rule_history_partition_recovery.worker_route_outcome outcome
      ON outcome.reservation_id = reservation.reservation_id
    WHERE reservation.work_item_id = NEW.work_item_id
      AND reservation.generation = NEW.generation;
    SELECT
      pg_catalog.count(*)::integer,
      pg_catalog.count(*) FILTER (
        WHERE reservation.route_ordinal = 2
      )::integer
    INTO total_reservations, fallback_reservations
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
        reservation
    WHERE reservation.work_item_id = NEW.work_item_id
      AND reservation.generation = NEW.generation;

    IF NEW.to_state = 'staged_needs_review' THEN
      IF prior_state <> 'proposal_running'
         OR outcome_count <> total_reservations
         OR succeeded_outcomes <> 1
         OR unknown_outcomes <> 0
         OR NOT (
           (
             total_reservations = 1
             AND fallback_reservations = 0
             AND failed_outcomes = 0
             AND EXISTS (
               SELECT 1
               FROM
                 nhi_rule_history_partition_recovery.
                   worker_route_reservation reservation
               JOIN
                 nhi_rule_history_partition_recovery.
                   worker_route_outcome outcome
                 ON outcome.reservation_id = reservation.reservation_id
               WHERE reservation.work_item_id = NEW.work_item_id
                 AND reservation.generation = NEW.generation
                 AND reservation.route_ordinal = 1
                 AND outcome.status = 'succeeded'
             )
           )
           OR (
             total_reservations = 2
             AND fallback_reservations = 1
             AND failed_outcomes = 1
             AND EXISTS (
               SELECT 1
               FROM
                 nhi_rule_history_partition_recovery.
                   worker_route_reservation reservation
               JOIN
                 nhi_rule_history_partition_recovery.
                   worker_route_outcome outcome
                 ON outcome.reservation_id = reservation.reservation_id
               WHERE reservation.work_item_id = NEW.work_item_id
                 AND reservation.generation = NEW.generation
                 AND reservation.route_ordinal = 1
                 AND outcome.status = 'failed'
                 AND outcome.failure_class IN (
                   'transport_failure', 'execution_failure', 'timeout',
                   'process_exit_failure', 'invalid_json',
                   'output_schema_invalid', 'unknown_enum',
                   'missing_locator', 'locator_mismatch',
                   'source_text_mismatch',
                   'output_contract_inconsistent'
                 )
             )
             AND EXISTS (
               SELECT 1
               FROM
                 nhi_rule_history_partition_recovery.
                   worker_route_reservation reservation
               JOIN
                 nhi_rule_history_partition_recovery.
                   worker_route_outcome outcome
                 ON outcome.reservation_id = reservation.reservation_id
               WHERE reservation.work_item_id = NEW.work_item_id
                 AND reservation.generation = NEW.generation
                 AND reservation.route_ordinal = 2
                 AND outcome.status = 'succeeded'
             )
           )
         )
         OR NEW.bundle_receipt_id IS NULL
         OR NEW.candidate_proposal_id IS NULL THEN
        RAISE EXCEPTION
          'staged partition recovery requires primary success or allowlisted primary failure plus fallback success'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      IF NOT EXISTS (
        SELECT 1
        FROM
          nhi_rule_history_candidate_stage.candidate_proposal
            candidate
        JOIN
          nhi_rule_history_candidate_stage.current_candidate_state
            candidate_state
          ON candidate_state.proposal_id = candidate.proposal_id
        JOIN
          nhi_rule_history_partition_recovery.
            worker_route_reservation reservation
          ON reservation.work_item_id = NEW.work_item_id
         AND reservation.generation = NEW.generation
         AND reservation.source_job_id = candidate.job_id
        JOIN
          nhi_rule_history_partition_recovery.
            worker_route_outcome outcome
          ON outcome.reservation_id = reservation.reservation_id
         AND outcome.status = 'succeeded'
         AND outcome.source_job_id = candidate.job_id
         AND outcome.attempt_id = candidate.producer_attempt_id
         AND outcome.output_sha256 = candidate.producer_output_sha256
        WHERE candidate.proposal_id = NEW.candidate_proposal_id
          AND candidate.bundle_receipt_id = NEW.bundle_receipt_id
          AND candidate.job_id = NEW.source_job_id
          AND candidate_state.state = 'needs_review'
      ) THEN
        RAISE EXCEPTION
          'staged partition candidate must be the needs-review proposal produced by the single successful recovery route'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code IN (
      'preflight_replay_mismatch', 'preflight_nondeterminism',
      'packet_or_contract_tamper'
    ) THEN
      IF prior_state <> 'retry_pending'
         OR outcome_count <> 0
         OR total_reservations <> 0
         OR NEW.source_job_id IS NOT NULL
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'preflight replay mismatch must fail before every route and candidate'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code =
      'restart_before_model_reservation' THEN
      IF prior_state <> 'retry_pending'
         OR outcome_count <> 0
         OR total_reservations <> 0
         OR NEW.source_job_id IS NOT NULL
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'restart before model reservation requires zero routes and no execution job transition'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code = 'execution_unknown' THEN
      IF prior_state <> 'proposal_running'
         OR outcome_count <> 1
         OR unknown_outcomes <> 1
         OR fallback_reservations <> 0
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'execution_unknown forbids retry, fallback, and candidate staging'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code =
      'restart_open_route_execution_unknown' THEN
      IF prior_state <> 'proposal_running'
         OR total_reservations NOT BETWEEN 1 AND 2
         OR outcome_count <> total_reservations
         OR unknown_outcomes <> 1
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'reconciled open route requires one durable execution-unknown outcome without staging'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code =
      'restart_after_model_result' THEN
      IF prior_state <> 'proposal_running'
         OR outcome_count NOT BETWEEN 1 AND 2
         OR unknown_outcomes <> 0
         OR EXISTS (
           SELECT 1
           FROM
             nhi_rule_history_partition_recovery.
               worker_route_reservation reservation
           LEFT JOIN
             nhi_rule_history_partition_recovery.
               worker_route_outcome outcome
             ON outcome.reservation_id = reservation.reservation_id
           WHERE reservation.work_item_id = NEW.work_item_id
             AND reservation.generation = NEW.generation
             AND outcome.reservation_id IS NULL
         )
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'restart after a durable model result forbids resume, retry, fallback, and staging'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF typed_terminal.reason_code = 'primary_and_fallback_failed' THEN
      IF prior_state <> 'proposal_running'
         OR outcome_count <> 2
         OR failed_outcomes <> 2
         OR fallback_reservations <> 1
         OR NEW.bundle_receipt_id IS NOT NULL
         OR NEW.candidate_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION
          'failed partition recovery requires one failed primary and fallback'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSE
      RAISE EXCEPTION
        'partition failed_terminal reason is not typed'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT (
    (prior_state = 'retry_pending'
      AND NEW.to_state IN ('proposal_running', 'partition_required'))
    OR (prior_state = 'proposal_running'
      AND NEW.to_state IN (
        'staged_needs_review', 'staged_pending_anchor',
        'failed_terminal', 'partition_required'
      ))
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
    pg_catalog.count(*),
    pg_catalog.count(*) FILTER (WHERE attempt.status = 'success'),
    pg_catalog.count(*) FILTER (WHERE attempt.status = 'failed')
  INTO route_count, successful_routes, failed_routes
  FROM nhi_rule_history_update_queue.recovery_route_attempt linked
  JOIN nhi_rule_history_update_ops.worker_attempt attempt
    ON attempt.attempt_id = linked.attempt_id
  WHERE linked.work_item_id = NEW.work_item_id
    AND linked.generation = NEW.generation;
  IF NEW.to_state IN ('staged_needs_review', 'staged_pending_anchor') THEN
    IF successful_routes <> 1
       OR NEW.bundle_receipt_id IS NULL
       OR NEW.candidate_proposal_id IS NULL THEN
      RAISE EXCEPTION
        'staged recovery requires one successful route and matching candidate identifiers'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM nhi_rule_history_candidate_stage.candidate_proposal candidate
      JOIN
        nhi_rule_history_candidate_stage.current_candidate_state
          candidate_state
        ON candidate_state.proposal_id = candidate.proposal_id
      JOIN
        nhi_rule_history_update_queue.recovery_route_attempt linked
        ON linked.work_item_id = NEW.work_item_id
       AND linked.generation = NEW.generation
       AND linked.source_job_id = candidate.job_id
       AND linked.attempt_id = candidate.producer_attempt_id
      JOIN nhi_rule_history_update_ops.worker_attempt attempt
        ON attempt.job_id = candidate.job_id
       AND attempt.attempt_id = candidate.producer_attempt_id
       AND attempt.status = 'success'
       AND attempt.output_sha256 = candidate.producer_output_sha256
      WHERE candidate.proposal_id = NEW.candidate_proposal_id
        AND candidate.bundle_receipt_id = NEW.bundle_receipt_id
        AND candidate.job_id = NEW.source_job_id
        AND candidate_state.state = CASE NEW.to_state
          WHEN 'staged_needs_review' THEN 'needs_review'
          WHEN 'staged_pending_anchor'
            THEN 'promotion_ready_pending_anchor'
        END
    ) THEN
      RAISE EXCEPTION
        'staged recovery candidate must match the successful route and requested candidate state'
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
  nhi_rule_history_partition_recovery.canonical_jsonb_text(
    p_value jsonb
  )
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $$
DECLARE
  value_kind text;
  serialized text;
BEGIN
  value_kind := pg_catalog.jsonb_typeof(p_value);
  IF value_kind = 'object' THEN
    SELECT COALESCE(
      '{' || pg_catalog.string_agg(
        pg_catalog.to_jsonb(member.key)::text || ':' ||
          nhi_rule_history_partition_recovery.
            canonical_jsonb_text(member.value),
        ',' ORDER BY member.key COLLATE "C"
      ) || '}',
      '{}'
    )
    INTO serialized
    FROM pg_catalog.jsonb_each(p_value) member;
    RETURN serialized;
  ELSIF value_kind = 'array' THEN
    SELECT COALESCE(
      '[' || pg_catalog.string_agg(
        nhi_rule_history_partition_recovery.
          canonical_jsonb_text(member.value),
        ',' ORDER BY member.ordinal
      ) || ']',
      '[]'
    )
    INTO serialized
    FROM pg_catalog.jsonb_array_elements(p_value)
      WITH ORDINALITY AS member(value, ordinal);
    RETURN serialized;
  ELSIF value_kind = 'number' THEN
    serialized := p_value::text;
    IF serialized !~ '^-?(0|[1-9][0-9]*)$' THEN
      RAISE EXCEPTION
        'canonical recovery JSON forbids non-integer numbers'
        USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN serialized;
  ELSIF value_kind IN ('string', 'boolean', 'null') THEN
    RETURN p_value::text;
  END IF;
  RAISE EXCEPTION
    'canonical recovery JSON contains an unsupported value'
    USING ERRCODE = 'invalid_parameter_value';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.canonical_jsonb_sha256(
    p_value jsonb
  )
RETURNS nhi_rule_history_update_ops.sha256_hex
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog
RETURN pg_catalog.encode(
  pg_catalog.sha256(
    pg_catalog.convert_to(
      nhi_rule_history_partition_recovery.
        canonical_jsonb_text(p_value) || E'\n',
      'UTF8'
    )
  ),
  'hex'
)::nhi_rule_history_update_ops.sha256_hex;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.sha256_uuid_v8(
    p_label text,
    VARIADIC p_parts text[]
  )
RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $$
DECLARE
  material text;
  uuid_bytes bytea;
  uuid_hex text;
BEGIN
  IF COALESCE(pg_catalog.btrim(p_label), '') = ''
     OR pg_catalog.array_position(p_parts, NULL) IS NOT NULL THEN
    RAISE EXCEPTION
      'SHA-256 UUID domain label and parts must be nonempty/non-null'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  material := p_label;
  IF pg_catalog.cardinality(p_parts) > 0 THEN
    material := material || pg_catalog.chr(31) ||
      pg_catalog.array_to_string(p_parts, pg_catalog.chr(31));
  END IF;
  uuid_bytes := pg_catalog.substr(
    pg_catalog.sha256(pg_catalog.convert_to(material, 'UTF8')),
    1, 16
  );
  uuid_bytes := pg_catalog.set_byte(
    uuid_bytes, 6,
    (pg_catalog.get_byte(uuid_bytes, 6) & 15) | 128
  );
  uuid_bytes := pg_catalog.set_byte(
    uuid_bytes, 8,
    (pg_catalog.get_byte(uuid_bytes, 8) & 63) | 128
  );
  uuid_hex := pg_catalog.encode(uuid_bytes, 'hex');
  RETURN (
    pg_catalog.substr(uuid_hex, 1, 8) || '-' ||
    pg_catalog.substr(uuid_hex, 9, 4) || '-' ||
    pg_catalog.substr(uuid_hex, 13, 4) || '-' ||
    pg_catalog.substr(uuid_hex, 17, 4) || '-' ||
    pg_catalog.substr(uuid_hex, 21, 12)
  )::uuid;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      p_transition_id uuid,
      p_transition_evidence_id uuid,
      p_terminal_receipt_id uuid,
      p_work_item_id uuid,
      p_generation integer,
      p_authorization_id uuid,
      p_admission_id uuid,
      p_to_state text,
      p_evidence_contract text,
      p_evidence_sha256 nhi_rule_history_update_ops.sha256_hex,
      p_evidence_json jsonb,
      p_source_job_id uuid,
      p_bundle_receipt_id uuid,
      p_candidate_proposal_id uuid,
      p_recorded_at timestamptz
    )
RETURNS TABLE (
  transition_id uuid,
  transition_seq integer,
  to_state text,
  terminal_receipt_id uuid,
  replayed boolean,
  recorded_at timestamptz,
  transition_evidence_id uuid,
  evidence_sha256 nhi_rule_history_update_ops.sha256_hex
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claim nhi_rule_history_partition_recovery.dispatch_claim%ROWTYPE;
  current_transition record;
  existing_terminal
    nhi_rule_history_partition_recovery.partition_terminal_receipt%ROWTYPE;
  reason_code text;
  expected_keys text[];
  canonical_evidence_text text;
  expected_evidence_sha256
    nhi_rule_history_update_ops.sha256_hex;
  transition_material jsonb;
  transition_material_sha256
    nhi_rule_history_update_ops.sha256_hex;
  expected_terminal_receipt_id uuid;
  expected_transition_id uuid;
  expected_transition_evidence_id uuid;
  reservation_count integer;
  outcome_count integer;
  succeeded_outcomes integer;
  failed_outcomes integer;
  unknown_outcomes integer;
  successful_route text;
  finished_route_receipts jsonb;
  finished_route_statuses jsonb;
  known_finished_route_statuses jsonb;
  execution_unknown_route_receipts jsonb;
  execution_unknown_receipt_sha256
    nhi_rule_history_update_ops.sha256_hex;
  persisted_recorded_at timestamptz;
BEGIN
  persisted_recorded_at := pg_catalog.transaction_timestamp();
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || p_work_item_id::text,
      0
    )
  );
  SELECT * INTO claim
  FROM nhi_rule_history_partition_recovery.dispatch_claim
  WHERE dispatch_claim.work_item_id = p_work_item_id
    AND dispatch_claim.generation = p_generation
    AND dispatch_claim.authorization_id = p_authorization_id
    AND dispatch_claim.admission_id = p_admission_id;
  IF NOT FOUND
     OR p_evidence_json ->> 'dispatch_claim_id' IS DISTINCT FROM
       claim.claim_id::text THEN
    RAISE EXCEPTION
      'terminal evidence must bind the exact consumed dispatch claim'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF pg_catalog.jsonb_typeof(p_evidence_json) IS DISTINCT FROM 'object'
     OR p_evidence_contract IS DISTINCT FROM
       'nhi-rule-history/partition-recovery-terminal-evidence/v1'
     OR p_generation <> 2
     OR p_to_state NOT IN ('staged_needs_review', 'failed_terminal') THEN
    RAISE EXCEPTION
      'partition terminal receipt or state is invalid'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  canonical_evidence_text :=
    nhi_rule_history_partition_recovery.
      canonical_jsonb_text(p_evidence_json);
  expected_evidence_sha256 :=
    nhi_rule_history_partition_recovery.
      canonical_jsonb_sha256(p_evidence_json);
  IF p_evidence_sha256 IS DISTINCT FROM expected_evidence_sha256 THEN
    RAISE EXCEPTION
      'terminal evidence SHA-256 does not match canonical evidence bytes'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF p_evidence_json ->> 'schema' IS DISTINCT FROM
       'nhi-rule-history/partition-recovery-terminal-evidence/v1'
     OR p_evidence_json ->> 'dispatch_claim_id' IS DISTINCT FROM
       claim.claim_id::text
     OR p_evidence_json ->> 'work_item_id' IS DISTINCT FROM
       p_work_item_id::text
     OR p_evidence_json -> 'generation' IS DISTINCT FROM
       pg_catalog.to_jsonb(p_generation)
     OR p_evidence_json ->> 'authorization_id' IS DISTINCT FROM
       p_authorization_id::text
     OR p_evidence_json ->> 'admission_id' IS DISTINCT FROM
       p_admission_id::text
     OR p_evidence_json ->> 'to_state' IS DISTINCT FROM p_to_state
     OR p_evidence_json -> 'auto_promotion_enabled' IS DISTINCT FROM
       'false'::jsonb THEN
    RAISE EXCEPTION
      'terminal evidence core tuple/schema does not match its claim'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  reason_code := p_evidence_json ->> 'reason_code';
  IF p_to_state = 'staged_needs_review' THEN
    reason_code := 'valid_output';
  ELSIF reason_code NOT IN (
    'preflight_replay_mismatch', 'preflight_nondeterminism',
    'packet_or_contract_tamper', 'primary_and_fallback_failed',
    'execution_unknown', 'restart_before_model_reservation',
    'restart_after_model_result',
    'restart_open_route_execution_unknown'
  ) THEN
    RAISE EXCEPTION
      'failed partition terminal receipt requires an allowlisted reason code'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  expected_terminal_receipt_id :=
    nhi_rule_history_partition_recovery.sha256_uuid_v8(
      'partition-recovery-terminal-receipt',
      p_work_item_id::text, p_generation::text, p_to_state,
      expected_evidence_sha256::text
    );
  transition_material := pg_catalog.jsonb_build_object(
    'dispatch_claim_id', claim.claim_id::text,
    'work_item_id', p_work_item_id::text,
    'generation', p_generation,
    'authorization_id', p_authorization_id::text,
    'admission_id', p_admission_id::text,
    'to_state', p_to_state,
    'evidence_contract', p_evidence_contract,
    'evidence_sha256', expected_evidence_sha256::text,
    'terminal_receipt_id', expected_terminal_receipt_id::text,
    'source_job_id', p_source_job_id::text,
    'bundle_receipt_id', p_bundle_receipt_id::text,
    'candidate_proposal_id', p_candidate_proposal_id::text
  );
  transition_material_sha256 :=
    nhi_rule_history_partition_recovery.
      canonical_jsonb_sha256(transition_material);
  expected_transition_id :=
    nhi_rule_history_partition_recovery.sha256_uuid_v8(
      'partition-recovery-transition',
      transition_material_sha256::text
    );
  expected_transition_evidence_id :=
    nhi_rule_history_partition_recovery.sha256_uuid_v8(
      'partition-recovery-transition-evidence',
      expected_transition_id::text,
      expected_terminal_receipt_id::text,
      expected_evidence_sha256::text
    );
  IF p_terminal_receipt_id IS DISTINCT FROM
       expected_terminal_receipt_id
     OR p_transition_id IS DISTINCT FROM expected_transition_id
     OR p_transition_evidence_id IS DISTINCT FROM
       expected_transition_evidence_id THEN
    RAISE EXCEPTION
      'terminal receipt, transition, or evidence identity is not deterministic'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT * INTO existing_terminal
  FROM
    nhi_rule_history_partition_recovery.partition_terminal_receipt
  WHERE partition_terminal_receipt.terminal_receipt_id =
    p_terminal_receipt_id
     OR partition_terminal_receipt.claim_id = claim.claim_id
  LIMIT 1;
  IF FOUND THEN
    IF existing_terminal.terminal_receipt_id IS DISTINCT FROM
         p_terminal_receipt_id
       OR existing_terminal.terminal_transition_id IS DISTINCT FROM
         p_transition_id
       OR existing_terminal.claim_id IS DISTINCT FROM claim.claim_id
       OR existing_terminal.work_item_id IS DISTINCT FROM p_work_item_id
       OR existing_terminal.generation IS DISTINCT FROM p_generation
       OR existing_terminal.authorization_id IS DISTINCT FROM
          p_authorization_id
       OR existing_terminal.admission_id IS DISTINCT FROM p_admission_id
       OR existing_terminal.terminal_state IS DISTINCT FROM p_to_state
       OR existing_terminal.receipt_sha256 IS DISTINCT FROM
         expected_evidence_sha256
       OR existing_terminal.receipt_json IS DISTINCT FROM
         p_evidence_json
       OR existing_terminal.reason_code IS DISTINCT FROM reason_code THEN
      RAISE EXCEPTION
        'partition terminal receipt was reused with different material'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT transition.transition_seq, transition.recorded_at
    INTO transition_seq, persisted_recorded_at
    FROM nhi_rule_history_update_queue.work_generation_transition transition
    WHERE transition.transition_id = expected_transition_id
      AND transition.work_item_id = p_work_item_id
      AND transition.generation = p_generation
      AND transition.to_state = p_to_state
      AND transition.actor_kind = 'runtime-partition-terminal'
      AND transition.source_job_id IS NOT DISTINCT FROM p_source_job_id
      AND transition.bundle_receipt_id IS NOT DISTINCT FROM
        p_bundle_receipt_id
      AND transition.candidate_proposal_id IS NOT DISTINCT FROM
        p_candidate_proposal_id;
    IF existing_terminal.recorded_at IS DISTINCT FROM
         persisted_recorded_at
       OR NOT EXISTS (
         SELECT 1
         FROM
           nhi_rule_history_partition_recovery.
             generation_transition_evidence evidence
         WHERE evidence.transition_evidence_id =
             expected_transition_evidence_id
           AND evidence.generation_transition_id =
             expected_transition_id
           AND evidence.evidence_kind =
             'partition_terminal_receipt'
           AND evidence.evidence_contract = p_evidence_contract
           AND evidence.evidence_object_id =
             expected_terminal_receipt_id::text
           AND evidence.evidence_sha256 = expected_evidence_sha256
           AND evidence.canonical_payload_sha256 =
             expected_evidence_sha256
           AND evidence.logical_locator =
             'partition-recovery/terminal-receipts/' ||
               expected_terminal_receipt_id::text
           AND evidence.ordinal = 1
           AND evidence.byte_count = pg_catalog.octet_length(
             pg_catalog.convert_to(
               canonical_evidence_text || E'\n', 'UTF8'
             )
           )
       ) THEN
      RAISE EXCEPTION
        'persisted terminal transition evidence is incomplete or different'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN QUERY SELECT
      expected_transition_id, transition_seq, p_to_state,
      expected_terminal_receipt_id, true, persisted_recorded_at,
      expected_transition_evidence_id, expected_evidence_sha256;
    RETURN;
  END IF;

  SELECT transition.*
  INTO current_transition
  FROM nhi_rule_history_update_queue.work_generation_transition transition
  WHERE transition.work_item_id = p_work_item_id
    AND transition.generation = p_generation
  ORDER BY transition.transition_seq DESC
  LIMIT 1;
  IF current_transition.to_state NOT IN (
    'retry_pending', 'proposal_running'
  ) THEN
    RAISE EXCEPTION
      'partition generation is no longer closable'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF pg_catalog.transaction_timestamp() <
       current_transition.recorded_at THEN
    persisted_recorded_at := current_transition.recorded_at;
  ELSE
    persisted_recorded_at := pg_catalog.transaction_timestamp();
  END IF;

  SELECT
    pg_catalog.count(*)::integer,
    pg_catalog.count(outcome.reservation_id)::integer,
    pg_catalog.count(outcome.reservation_id) FILTER (
      WHERE outcome.status = 'succeeded'
    )::integer,
    pg_catalog.count(outcome.reservation_id) FILTER (
      WHERE outcome.status = 'failed'
    )::integer,
    pg_catalog.count(outcome.reservation_id) FILTER (
      WHERE outcome.status = 'execution_unknown'
    )::integer,
    pg_catalog.min(reservation.route) FILTER (
      WHERE outcome.status = 'succeeded'
    ),
    COALESCE(
      pg_catalog.jsonb_object_agg(
        reservation.route, outcome.receipt_sha256::text
        ORDER BY reservation.route_ordinal
      ) FILTER (WHERE outcome.reservation_id IS NOT NULL),
      '{}'::jsonb
    ),
    COALESCE(
      pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'reservation_id', reservation.reservation_id,
          'route_ordinal', reservation.route_ordinal,
          'route', reservation.route,
          'status', outcome.status,
          'failure_class', outcome.failure_class
        )
        ORDER BY reservation.route_ordinal
      ) FILTER (WHERE outcome.reservation_id IS NOT NULL),
      '[]'::jsonb
    ),
    COALESCE(
      pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'reservation_id', reservation.reservation_id,
          'route_ordinal', reservation.route_ordinal,
          'route', reservation.route,
          'status', outcome.status,
          'failure_class', outcome.failure_class
        )
        ORDER BY reservation.route_ordinal
      ) FILTER (
        WHERE outcome.reservation_id IS NOT NULL
          AND outcome.status <> 'execution_unknown'
      ),
      '[]'::jsonb
    ),
    COALESCE(
      pg_catalog.jsonb_object_agg(
        reservation.route, outcome.receipt_sha256::text
        ORDER BY reservation.route_ordinal
      ) FILTER (WHERE outcome.status = 'execution_unknown'),
      '{}'::jsonb
    ),
    pg_catalog.min(outcome.receipt_sha256) FILTER (
      WHERE outcome.status = 'execution_unknown'
    )
  INTO
    reservation_count, outcome_count, succeeded_outcomes,
    failed_outcomes, unknown_outcomes, successful_route,
    finished_route_receipts, finished_route_statuses,
    known_finished_route_statuses,
    execution_unknown_route_receipts,
    execution_unknown_receipt_sha256
  FROM
    nhi_rule_history_partition_recovery.worker_route_reservation
      reservation
  LEFT JOIN
    nhi_rule_history_partition_recovery.worker_route_outcome outcome
    ON outcome.reservation_id = reservation.reservation_id
  WHERE reservation.claim_id = claim.claim_id;

  expected_keys := ARRAY[
    'schema', 'dispatch_claim_id', 'work_item_id', 'generation',
    'authorization_id', 'admission_id', 'to_state',
    'auto_promotion_enabled'
  ];
  IF p_to_state = 'staged_needs_review' THEN
    expected_keys := expected_keys || ARRAY[
      'candidate_receipt_sha256', 'candidate_state',
      'selected_worker_role', 'worker_calls', 'finished_routes',
      'canonical_history_writes'
    ];
  ELSIF reason_code IN (
    'restart_before_model_reservation', 'restart_after_model_result',
    'restart_open_route_execution_unknown',
    'packet_or_contract_tamper'
  ) THEN
    expected_keys := expected_keys || ARRAY[
      'reason_code', 'failure_code', 'preexisting_output_namespace',
      'generation_state', 'finished_route_statuses',
      'open_route_reconciled_as_execution_unknown',
      'worker_reinvocation', 'automatic_retry', 'automatic_fallback'
    ];
  ELSIF reason_code = 'preflight_replay_mismatch' THEN
    expected_keys := expected_keys || ARRAY[
      'reason_code', 'failure_code', 'admitted', 'replayed',
      'worker_calls', 'automatic_retry'
    ];
  ELSIF reason_code = 'execution_unknown' THEN
    expected_keys := expected_keys || ARRAY[
      'reason_code', 'failure_code', 'execution_unknown_routes',
      'automatic_retry', 'automatic_fallback'
    ];
  ELSIF reason_code = 'primary_and_fallback_failed' THEN
    expected_keys := expected_keys || ARRAY[
      'reason_code', 'failure_code', 'failure_receipt_sha256',
      'finished_routes', 'automatic_retry'
    ];
  ELSIF reason_code = 'preflight_nondeterminism' THEN
    expected_keys := expected_keys || ARRAY[
      'reason_code', 'failure_code', 'worker_status', 'worker_calls',
      'automatic_retry'
    ];
  END IF;
  IF EXISTS (
    SELECT actual.key
    FROM pg_catalog.jsonb_object_keys(p_evidence_json) actual(key)
    EXCEPT
    SELECT expected.key
    FROM pg_catalog.unnest(expected_keys) expected(key)
  ) OR EXISTS (
    SELECT expected.key
    FROM pg_catalog.unnest(expected_keys) expected(key)
    EXCEPT
    SELECT actual.key
    FROM pg_catalog.jsonb_object_keys(p_evidence_json) actual(key)
  ) THEN
    RAISE EXCEPTION
      'terminal evidence fields do not match its exact reason/state variant'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF p_to_state = 'staged_needs_review' THEN
    IF p_source_job_id IS NULL
       OR p_bundle_receipt_id IS NULL
       OR p_candidate_proposal_id IS NULL
       OR claim.source_job_id IS DISTINCT FROM p_source_job_id
       OR COALESCE(
            p_evidence_json ->> 'candidate_receipt_sha256', ''
          ) !~ '^[0-9a-f]{64}$'
       OR p_evidence_json ->> 'candidate_state'
          IS DISTINCT FROM 'needs_review'
       OR p_evidence_json ->> 'selected_worker_role' IS DISTINCT FROM
          successful_route
       OR p_evidence_json -> 'worker_calls' IS DISTINCT FROM
          pg_catalog.to_jsonb(outcome_count)
       OR p_evidence_json -> 'finished_routes' IS DISTINCT FROM
          finished_route_receipts
       OR p_evidence_json -> 'canonical_history_writes'
          IS DISTINCT FROM '0'::jsonb
       OR outcome_count <> reservation_count
       OR succeeded_outcomes <> 1
       OR unknown_outcomes <> 0
       OR NOT EXISTS (
         SELECT 1
         FROM
           nhi_rule_history_candidate_stage.candidate_proposal
             candidate
         JOIN
           nhi_rule_history_candidate_stage.current_candidate_state
             candidate_state
           ON candidate_state.proposal_id = candidate.proposal_id
         JOIN
           nhi_rule_history_partition_recovery.
             worker_route_reservation reservation
           ON reservation.claim_id = claim.claim_id
          AND reservation.source_job_id = candidate.job_id
         JOIN
           nhi_rule_history_partition_recovery.
             worker_route_outcome outcome
           ON outcome.reservation_id = reservation.reservation_id
          AND outcome.status = 'succeeded'
          AND outcome.source_job_id = candidate.job_id
          AND outcome.attempt_id = candidate.producer_attempt_id
          AND outcome.output_sha256 = candidate.producer_output_sha256
         WHERE candidate.proposal_id = p_candidate_proposal_id
           AND candidate.bundle_receipt_id = p_bundle_receipt_id
           AND candidate.job_id = p_source_job_id
           AND candidate_state.state = 'needs_review'
       ) THEN
      RAISE EXCEPTION
        'staged terminal evidence/candidate must match the successful route and needs-review proposal'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSE
    IF p_bundle_receipt_id IS NOT NULL
       OR p_candidate_proposal_id IS NOT NULL
       OR pg_catalog.jsonb_typeof(
            p_evidence_json -> 'failure_code'
          ) IS DISTINCT FROM 'string'
       OR COALESCE(
            pg_catalog.btrim(p_evidence_json ->> 'failure_code'), ''
          ) = '' THEN
      RAISE EXCEPTION
        'failed terminal evidence or candidate identifiers are invalid'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF p_evidence_json ->> 'failure_code' IS DISTINCT FROM (
      CASE reason_code
        WHEN 'preflight_replay_mismatch'
          THEN 'ADMITTED_SUITABILITY_REPLAY_MISMATCH'
        WHEN 'preflight_nondeterminism'
          THEN 'ADMITTED_SUITABILITY_CHANGED_DURING_RUN'
        WHEN 'packet_or_contract_tamper'
          THEN 'PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE'
        WHEN 'restart_before_model_reservation'
          THEN 'RECOVERY_RESTART_BEFORE_MODEL_RESERVATION'
        WHEN 'restart_after_model_result'
          THEN 'RECOVERY_RESTART_AFTER_MODEL_RESULT'
        WHEN 'restart_open_route_execution_unknown'
          THEN 'RECOVERY_OPEN_ROUTE_EXECUTION_UNKNOWN'
        WHEN 'execution_unknown'
          THEN 'WORKER_EXECUTION_UNKNOWN'
        WHEN 'primary_and_fallback_failed'
          THEN 'PRIMARY_AND_FALLBACK_FAILED'
      END
    ) THEN
      RAISE EXCEPTION
        'failed terminal reason and failure code do not match'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF reason_code IN (
      'preflight_replay_mismatch', 'preflight_nondeterminism',
      'packet_or_contract_tamper', 'restart_before_model_reservation'
    ) AND p_source_job_id IS NOT NULL THEN
      RAISE EXCEPTION
        'pre-call terminal evidence cannot claim a recovery source job'
        USING ERRCODE = 'integrity_constraint_violation';
    ELSIF reason_code IN (
      'primary_and_fallback_failed', 'execution_unknown',
      'restart_after_model_result',
      'restart_open_route_execution_unknown'
    ) AND (
      p_source_job_id IS NULL
      OR claim.source_job_id IS DISTINCT FROM p_source_job_id
    ) THEN
      RAISE EXCEPTION
        'post-reservation terminal evidence requires the exact recovery job'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF reason_code = 'preflight_replay_mismatch' THEN
      IF reservation_count <> 0
         OR pg_catalog.jsonb_typeof(
              p_evidence_json -> 'admitted'
            ) IS DISTINCT FROM 'object'
         OR p_evidence_json -> 'admitted' = '{}'::jsonb
         OR pg_catalog.jsonb_typeof(
              p_evidence_json -> 'replayed'
            ) IS DISTINCT FROM 'object'
         OR p_evidence_json -> 'replayed' = '{}'::jsonb
         OR EXISTS (
           SELECT actual.key
           FROM pg_catalog.jsonb_object_keys(
             p_evidence_json -> 'admitted'
           ) actual(key)
           EXCEPT
           SELECT expected.key
           FROM pg_catalog.unnest(ARRAY[
             'prompt_sha256', 'job_fingerprint',
             'suitability_receipt_sha256', 'suitability_preflight',
             'route_policy_sha256'
           ]) expected(key)
         )
         OR EXISTS (
           SELECT expected.key
           FROM pg_catalog.unnest(ARRAY[
             'prompt_sha256', 'job_fingerprint',
             'suitability_receipt_sha256', 'suitability_preflight',
             'route_policy_sha256'
           ]) expected(key)
           EXCEPT
           SELECT actual.key
           FROM pg_catalog.jsonb_object_keys(
             p_evidence_json -> 'admitted'
           ) actual(key)
         )
         OR EXISTS (
           SELECT actual.key
           FROM pg_catalog.jsonb_object_keys(
             p_evidence_json -> 'replayed'
           ) actual(key)
           EXCEPT
           SELECT expected.key
           FROM pg_catalog.unnest(ARRAY[
             'prompt_sha256', 'job_fingerprint',
             'suitability_receipt_sha256', 'suitability_preflight',
             'route_policy_sha256', 'matches_admission'
           ]) expected(key)
         )
         OR EXISTS (
           SELECT expected.key
           FROM pg_catalog.unnest(ARRAY[
             'prompt_sha256', 'job_fingerprint',
             'suitability_receipt_sha256', 'suitability_preflight',
             'route_policy_sha256', 'matches_admission'
           ]) expected(key)
           EXCEPT
           SELECT actual.key
           FROM pg_catalog.jsonb_object_keys(
             p_evidence_json -> 'replayed'
           ) actual(key)
         )
         OR p_evidence_json #>> '{admitted,prompt_sha256}'
            IS DISTINCT FROM claim.prompt_sha256::text
         OR p_evidence_json #>> '{admitted,job_fingerprint}'
            IS DISTINCT FROM claim.job_fingerprint::text
         OR p_evidence_json #>>
              '{admitted,suitability_receipt_sha256}'
            IS DISTINCT FROM claim.suitability_receipt_sha256::text
         OR p_evidence_json #>> '{admitted,route_policy_sha256}'
            IS DISTINCT FROM claim.route_policy_sha256::text
         OR p_evidence_json #> '{admitted,suitability_preflight}'
            IS DISTINCT FROM (
              SELECT admission.admission_payload_json #>
                '{execution_delta,suitability_preflight}'
              FROM
                nhi_rule_history_partition_recovery.
                  partition_recovery_admission admission
              WHERE admission.admission_id = claim.admission_id
            )
         OR COALESCE(
              p_evidence_json #>>
                '{replayed,prompt_sha256}', ''
            ) !~ '^[0-9a-f]{64}$'
         OR COALESCE(
              p_evidence_json #>>
                '{replayed,job_fingerprint}', ''
            ) !~ '^[0-9a-f]{64}$'
         OR COALESCE(
              p_evidence_json #>>
                '{replayed,suitability_receipt_sha256}', ''
            ) !~ '^[0-9a-f]{64}$'
         OR COALESCE(
              p_evidence_json #>>
                '{replayed,route_policy_sha256}', ''
            ) !~ '^[0-9a-f]{64}$'
         OR pg_catalog.jsonb_typeof(
              p_evidence_json #>
                '{replayed,suitability_preflight}'
            ) IS DISTINCT FROM 'object'
         OR p_evidence_json #> '{replayed,matches_admission}'
            IS DISTINCT FROM 'false'::jsonb
         OR p_evidence_json -> 'worker_calls' IS DISTINCT FROM '0'::jsonb
         OR p_evidence_json -> 'automatic_retry' IS DISTINCT FROM
           'false'::jsonb THEN
        RAISE EXCEPTION
          'preflight mismatch evidence is inconsistent with zero-call closure'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF reason_code = 'preflight_nondeterminism' THEN
      IF reservation_count <> 0
         OR pg_catalog.jsonb_typeof(
              p_evidence_json -> 'worker_status'
            ) IS DISTINCT FROM 'string'
         OR COALESCE(
              pg_catalog.btrim(p_evidence_json ->> 'worker_status'), ''
            ) = ''
         OR p_evidence_json -> 'worker_calls' IS DISTINCT FROM '0'::jsonb
         OR p_evidence_json -> 'automatic_retry' IS DISTINCT FROM
           'false'::jsonb THEN
        RAISE EXCEPTION
          'preflight nondeterminism evidence is inconsistent with zero calls'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF reason_code = 'execution_unknown' THEN
      IF reservation_count <> 1
         OR outcome_count <> 1
         OR unknown_outcomes <> 1
         OR execution_unknown_route_receipts = '{}'::jsonb
         OR p_evidence_json -> 'execution_unknown_routes'
            IS DISTINCT FROM execution_unknown_route_receipts
         OR p_evidence_json -> 'automatic_retry' IS DISTINCT FROM
            'false'::jsonb
         OR p_evidence_json -> 'automatic_fallback' IS DISTINCT FROM
            'false'::jsonb THEN
        RAISE EXCEPTION
          'execution-unknown evidence does not match its durable route'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSIF reason_code = 'primary_and_fallback_failed' THEN
      IF outcome_count <> 2
         OR failed_outcomes <> 2
         OR COALESCE(
              p_evidence_json ->> 'failure_receipt_sha256', ''
            ) !~ '^[0-9a-f]{64}$'
         OR p_evidence_json -> 'finished_routes' IS DISTINCT FROM
            finished_route_receipts
         OR p_evidence_json -> 'automatic_retry' IS DISTINCT FROM
            'false'::jsonb THEN
        RAISE EXCEPTION
          'dual-failure evidence does not match both failed routes'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSE
      IF p_evidence_json ->> 'generation_state' IS DISTINCT FROM
           current_transition.to_state
         OR p_evidence_json -> 'finished_route_statuses'
            IS DISTINCT FROM (
              CASE reason_code
              WHEN 'restart_open_route_execution_unknown'
                THEN known_finished_route_statuses
              ELSE finished_route_statuses
              END
            )
         OR p_evidence_json -> 'worker_reinvocation' IS DISTINCT FROM
            'false'::jsonb
         OR p_evidence_json -> 'automatic_retry' IS DISTINCT FROM
            'false'::jsonb
         OR p_evidence_json -> 'automatic_fallback' IS DISTINCT FROM
            'false'::jsonb
         OR pg_catalog.jsonb_typeof(
              p_evidence_json -> 'preexisting_output_namespace'
            ) IS DISTINCT FROM 'boolean' THEN
        RAISE EXCEPTION
          'restart/tamper evidence does not match durable recovery state'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      IF reason_code IN (
        'packet_or_contract_tamper',
        'restart_before_model_reservation'
      ) AND (
        current_transition.to_state <> 'retry_pending'
        OR reservation_count <> 0
        OR p_evidence_json -> 'preexisting_output_namespace'
           IS DISTINCT FROM (
             CASE reason_code
             WHEN 'packet_or_contract_tamper' THEN 'true'::jsonb
             ELSE 'false'::jsonb
             END
           )
        OR p_evidence_json ->
             'open_route_reconciled_as_execution_unknown'
           IS DISTINCT FROM 'null'::jsonb
      ) THEN
        RAISE EXCEPTION
          'pre-call restart/tamper evidence must remain zero-route'
          USING ERRCODE = 'integrity_constraint_violation';
      ELSIF reason_code = 'restart_after_model_result' AND (
        current_transition.to_state <> 'proposal_running'
        OR outcome_count NOT BETWEEN 1 AND 2
        OR outcome_count <> reservation_count
        OR unknown_outcomes <> 0
        OR p_evidence_json -> 'preexisting_output_namespace'
           IS DISTINCT FROM 'true'::jsonb
        OR p_evidence_json ->
             'open_route_reconciled_as_execution_unknown'
           IS DISTINCT FROM 'null'::jsonb
      ) THEN
        RAISE EXCEPTION
          'post-result restart evidence does not match finished routes'
          USING ERRCODE = 'integrity_constraint_violation';
      ELSIF reason_code =
        'restart_open_route_execution_unknown' AND (
        current_transition.to_state <> 'proposal_running'
        OR reservation_count NOT BETWEEN 1 AND 2
        OR outcome_count <> reservation_count
        OR unknown_outcomes <> 1
        OR p_evidence_json -> 'finished_route_statuses'
           IS DISTINCT FROM known_finished_route_statuses
        OR p_evidence_json -> 'preexisting_output_namespace'
           IS DISTINCT FROM 'true'::jsonb
        OR p_evidence_json ->>
             'open_route_reconciled_as_execution_unknown'
           IS DISTINCT FROM execution_unknown_receipt_sha256::text
      ) THEN
        RAISE EXCEPTION
          'open-route restart evidence does not match execution-unknown receipt'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    END IF;
  END IF;

  INSERT INTO
    nhi_rule_history_partition_recovery.partition_terminal_receipt (
      terminal_receipt_id, terminal_transition_id, claim_id,
      work_item_id, generation, authorization_id, admission_id,
      terminal_state, reason_code, receipt_sha256, receipt_json,
      closed_by_session, closed_by_capability, recorded_at
    ) VALUES (
      expected_terminal_receipt_id, expected_transition_id,
      claim.claim_id,
      p_work_item_id, p_generation, p_authorization_id, p_admission_id,
      p_to_state, reason_code, expected_evidence_sha256,
      p_evidence_json,
      SESSION_USER, pg_catalog.current_setting('role', true),
      persisted_recorded_at
    );

  INSERT INTO
    nhi_rule_history_update_queue.work_generation_transition (
      work_item_id, generation, transition_seq, transition_id,
      from_state, to_state, actor_kind, source_job_id,
      bundle_receipt_id, candidate_proposal_id, recorded_at
    ) VALUES (
      p_work_item_id, p_generation,
      current_transition.transition_seq + 1, expected_transition_id,
      current_transition.to_state, p_to_state,
      'runtime-partition-terminal', p_source_job_id,
      p_bundle_receipt_id, p_candidate_proposal_id,
      persisted_recorded_at
    );

  INSERT INTO
    nhi_rule_history_partition_recovery.generation_transition_evidence (
      transition_evidence_id, generation_transition_id, evidence_kind,
      evidence_contract, evidence_object_id, evidence_sha256,
      byte_count, logical_locator, ordinal, canonical_payload_sha256,
      created_by_session
    ) VALUES (
      expected_transition_evidence_id, expected_transition_id,
      'partition_terminal_receipt', p_evidence_contract,
      expected_terminal_receipt_id::text, expected_evidence_sha256,
      pg_catalog.octet_length(
        pg_catalog.convert_to(canonical_evidence_text || E'\n', 'UTF8')
      ),
      'partition-recovery/terminal-receipts/' ||
        expected_terminal_receipt_id::text,
      1, expected_evidence_sha256, SESSION_USER
    );
  RETURN QUERY SELECT
    expected_transition_id, current_transition.transition_seq + 1,
    p_to_state, expected_terminal_receipt_id, false,
    persisted_recorded_at, expected_transition_evidence_id,
    expected_evidence_sha256;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_partition_recovery.guard_legacy_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_partition_recovery.partition_recovery_admission
        admission
    WHERE admission.work_item_id = NEW.work_item_id
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_generation generation
    WHERE generation.work_item_id = NEW.work_item_id
  ) THEN
    RAISE EXCEPTION
      'legacy generation-1 transition writer is disabled after recovery admission'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS partition_recovery_legacy_transition_guard
  ON nhi_rule_history_update_queue.work_item_transition;
CREATE TRIGGER partition_recovery_legacy_transition_guard
BEFORE INSERT ON nhi_rule_history_update_queue.work_item_transition
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_partition_recovery.guard_legacy_transition();

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_backlog AS
SELECT current.*
FROM nhi_rule_history_update_queue.v_work_item_current current
WHERE current.current_state IN (
  'observed', 'selected', 'acquired',
  'corpus_registered', 'proposal_running'
)
AND NOT EXISTS (
  SELECT 1
  FROM nhi_rule_history_update_queue.work_generation generation
  WHERE generation.work_item_id = current.work_item_id
)
AND NOT EXISTS (
  SELECT 1
  FROM
    nhi_rule_history_partition_recovery.partition_recovery_admission
      admission
  WHERE admission.work_item_id = current.work_item_id
);

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_dispatch_v2 AS
SELECT
  recovery.work_item_id,
  recovery.generation,
  recovery.current_state,
  CASE
    WHEN partition_auth.authorization_id IS NOT NULL
    THEN 'partition_recovery'
    ELSE 'authorized_recovery'
  END::text AS generation_kind,
  recovery.authorization_id,
  recovery.route,
  recovery.source_bundle_uid,
  recovery.source_manifest_sha256::text
FROM nhi_rule_history_update_queue.v_recovery_backlog recovery
LEFT JOIN
  nhi_rule_history_partition_recovery.partition_recovery_authorization
    partition_auth
  ON partition_auth.authorization_id = recovery.authorization_id;

DO $append_only_guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'schema_migration',
    'legacy_function_owner_snapshot',
    'partition_recovery_admission',
    'partition_suitability_receipt',
    'partition_recovery_authorization',
    'authorization_event',
    'dispatch_claim',
    'worker_route_reservation',
    'worker_route_outcome',
    'late_worker_output_quarantine',
    'partition_terminal_receipt',
    'generation_transition_evidence'
  ]
  LOOP
    EXECUTE pg_catalog.format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_partition_recovery.%I',
      table_name || '_append_only_guard', table_name
    );
    EXECUTE pg_catalog.format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON nhi_rule_history_partition_recovery.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_update_queue.reject_append_only_change()',
      table_name || '_append_only_guard', table_name
    );
    EXECUTE pg_catalog.format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_partition_recovery.%I',
      table_name || '_truncate_guard', table_name
    );
    EXECUTE pg_catalog.format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON nhi_rule_history_partition_recovery.%I FOR EACH STATEMENT EXECUTE FUNCTION nhi_rule_history_update_queue.reject_truncate()',
      table_name || '_truncate_guard', table_name
    );
  END LOOP;
END;
$append_only_guards$;

DO $legacy_function_owners$
DECLARE
  function_signature text;
  function_oid regprocedure;
  prior_owner name;
BEGIN
  FOREACH function_signature IN ARRAY ARRAY[
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)',
    'nhi_rule_history_update_queue.guard_work_generation_insert()',
    'nhi_rule_history_update_queue.guard_generation_transition_insert()'
  ]
  LOOP
    function_oid := pg_catalog.to_regprocedure(function_signature);
    IF function_oid IS NULL THEN
      RAISE EXCEPTION
        'required legacy recovery function is missing: %',
        function_signature
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    SELECT pg_catalog.pg_get_userbyid(function.proowner)
    INTO prior_owner
    FROM pg_catalog.pg_proc function
    WHERE function.oid = function_oid;
    INSERT INTO
      nhi_rule_history_partition_recovery.legacy_function_owner_snapshot (
        function_identity, prior_owner
      ) VALUES (
        function_signature, prior_owner
      )
    ON CONFLICT ON CONSTRAINT
      legacy_function_owner_snapshot_pkey DO NOTHING;
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %s OWNER TO nhi_rule_history_recovery_owner',
      function_oid::text
    );
  END LOOP;
END;
$legacy_function_owners$;

DO $object_owners$
DECLARE
  object_name text;
BEGIN
  FOREACH object_name IN ARRAY ARRAY[
    'schema_migration',
    'legacy_function_owner_snapshot',
    'partition_recovery_admission',
    'partition_suitability_receipt',
    'partition_recovery_authorization',
    'authorization_event',
    'dispatch_claim',
    'worker_route_reservation',
    'worker_route_outcome',
    'late_worker_output_quarantine',
    'partition_terminal_receipt',
    'generation_transition_evidence'
  ]
  LOOP
    EXECUTE pg_catalog.format(
      'ALTER TABLE nhi_rule_history_partition_recovery.%I OWNER TO nhi_rule_history_recovery_owner',
      object_name
    );
  END LOOP;
END;
$object_owners$;

ALTER VIEW
  nhi_rule_history_partition_recovery.v_authorization_current
  OWNER TO nhi_rule_history_recovery_owner;

DO $function_owners$
DECLARE
  function_oid regprocedure;
  function_identity text;
BEGIN
  FOREACH function_identity IN ARRAY ARRAY[
    'nhi_rule_history_partition_recovery.generation_one_chain_receipt(uuid)',
    'nhi_rule_history_partition_recovery.generation_one_chain_matches_payload(uuid,jsonb)',
    'nhi_rule_history_partition_recovery.canonical_jsonb_text(jsonb)',
    'nhi_rule_history_partition_recovery.canonical_jsonb_sha256(jsonb)',
    'nhi_rule_history_partition_recovery.sha256_uuid_v8(text,text[])',
    'nhi_rule_history_partition_recovery.verify_partition_recovery_admission(jsonb)',
    'nhi_rule_history_partition_recovery.admit_partition_recovery(uuid,nhi_rule_history_update_ops.sha256_hex,jsonb,bytea,text,timestamptz)',
    'nhi_rule_history_partition_recovery.authorize_partition_recovery(uuid,uuid,uuid,uuid,integer,integer,text,timestamptz,text,timestamptz)',
    'nhi_rule_history_partition_recovery.show_partition_recovery(uuid,uuid)',
    'nhi_rule_history_partition_recovery.revoke_partition_recovery(uuid,text,text,timestamptz)',
    'nhi_rule_history_partition_recovery.consume_partition_recovery_dispatch(uuid,uuid,integer,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid,uuid,text,integer,timestamptz,timestamptz)',
    'nhi_rule_history_partition_recovery.reserve_partition_recovery_route(uuid,uuid,uuid,integer,uuid,uuid,smallint,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,uuid,uuid,text,text,text,text,nhi_rule_history_update_ops.sha256_hex,timestamptz)',
    'nhi_rule_history_partition_recovery.finish_partition_recovery_route(uuid,uuid,uuid,integer,uuid,uuid,text,text,uuid,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,integer,boolean,nhi_rule_history_update_ops.sha256_hex,jsonb,timestamptz)',
    'nhi_rule_history_partition_recovery.close_partition_recovery_generation(uuid,uuid,uuid,uuid,integer,uuid,uuid,text,text,nhi_rule_history_update_ops.sha256_hex,jsonb,uuid,uuid,uuid,timestamptz)',
    'nhi_rule_history_partition_recovery.guard_legacy_transition()',
    'nhi_rule_history_update_queue.guard_work_generation_insert()',
    'nhi_rule_history_update_queue.guard_generation_transition_insert()'
  ]
  LOOP
    function_oid := pg_catalog.to_regprocedure(function_identity);
    IF function_oid IS NULL THEN
      RAISE EXCEPTION 'missing managed recovery function %', function_identity;
    END IF;
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %s OWNER TO nhi_rule_history_recovery_owner',
      function_oid::text
    );
  END LOOP;
END;
$function_owners$;

REVOKE ALL ON SCHEMA nhi_rule_history_partition_recovery FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA
  nhi_rule_history_partition_recovery FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA
  nhi_rule_history_partition_recovery FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA
  nhi_rule_history_partition_recovery FROM PUBLIC;

DO $normalize_capability_acls$
DECLARE
  capability_role text;
BEGIN
  FOREACH capability_role IN ARRAY ARRAY[
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_stage_writer'
  ]
  LOOP
    IF EXISTS (
      SELECT 1
      FROM pg_catalog.pg_roles
      WHERE rolname = capability_role
    ) THEN
      EXECUTE pg_catalog.format(
        'REVOKE ALL ON SCHEMA nhi_rule_history_partition_recovery FROM %I',
        capability_role
      );
      EXECUTE pg_catalog.format(
        'REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_partition_recovery FROM %I',
        capability_role
      );
      EXECUTE pg_catalog.format(
        'REVOKE ALL ON ALL SEQUENCES IN SCHEMA nhi_rule_history_partition_recovery FROM %I',
        capability_role
      );
      EXECUTE pg_catalog.format(
        'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA nhi_rule_history_partition_recovery FROM %I',
        capability_role
      );
    END IF;
  END LOOP;
END;
$normalize_capability_acls$;

ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES
  FOR ROLE nhi_rule_history_recovery_owner
  IN SCHEMA nhi_rule_history_partition_recovery
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history_partition_recovery
  TO nhi_rule_history_recovery_authorizer;
GRANT USAGE ON SCHEMA nhi_rule_history_partition_recovery
  TO nhi_rule_history_update_queue_runtime;
GRANT USAGE ON SCHEMA nhi_rule_history_partition_recovery
  TO nhi_rule_history_candidate_runtime;
GRANT USAGE ON SCHEMA nhi_rule_history_update_ops
  TO nhi_rule_history_recovery_owner;
GRANT USAGE ON TYPE nhi_rule_history_update_ops.sha256_hex
  TO nhi_rule_history_recovery_owner;
GRANT USAGE ON SCHEMA nhi_rule_history_update_queue
  TO nhi_rule_history_recovery_owner;
GRANT USAGE ON SCHEMA nhi_rule_history_candidate_stage
  TO nhi_rule_history_recovery_owner;
GRANT SELECT, INSERT ON
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.recovery_route_attempt
  TO nhi_rule_history_recovery_owner;
GRANT SELECT, INSERT ON
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.job_lease
  TO nhi_rule_history_recovery_owner;
GRANT SELECT ON
  nhi_rule_history_update_queue.rss_work_item,
  nhi_rule_history_update_queue.work_item_transition,
  nhi_rule_history_update_queue.v_work_item_current,
  nhi_rule_history_update_queue.v_recovery_backlog,
  nhi_rule_history_update_ops.worker_attempt,
  nhi_rule_history_candidate_stage.candidate_proposal,
  nhi_rule_history_candidate_stage.current_candidate_state
  TO nhi_rule_history_recovery_owner;
GRANT INSERT ON nhi_rule_history_update_ops.worker_attempt
  TO nhi_rule_history_recovery_owner;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.job_lease,
  nhi_rule_history_update_ops.worker_attempt
  FROM nhi_rule_history_candidate_runtime;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.bundle_receipt
  FROM nhi_rule_history_candidate_runtime;
GRANT SELECT ON nhi_rule_history_update_ops.job_lease
  TO nhi_rule_history_candidate_runtime;
GRANT INSERT ON
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.bundle_receipt
  TO nhi_rule_history_candidate_runtime;
GRANT SELECT ON
  nhi_rule_history_partition_recovery.dispatch_claim,
  nhi_rule_history_partition_recovery.worker_route_reservation,
  nhi_rule_history_partition_recovery.worker_route_outcome
  TO nhi_rule_history_candidate_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.reject_append_only_change()
  TO nhi_rule_history_recovery_owner;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.reject_truncate()
  TO nhi_rule_history_recovery_owner;

DO $revoke_unsafe_existing_authorizers$
DECLARE
  function_identity text;
  function_oid oid;
  grantee_oid oid;
  grantee_name name;
BEGIN
  FOREACH function_identity IN ARRAY ARRAY[
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)'
  ]
  LOOP
    function_oid := pg_catalog.to_regprocedure(function_identity);
    FOR grantee_oid IN
      SELECT DISTINCT acl.grantee
      FROM pg_catalog.pg_proc function
      CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
          function.proacl,
          pg_catalog.acldefault('f', function.proowner)
        )
      ) acl
      WHERE function.oid = function_oid
        AND acl.privilege_type = 'EXECUTE'
    LOOP
      IF grantee_oid = 0 THEN
        EXECUTE pg_catalog.format(
          'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',
          function_oid::regprocedure::text
        );
      ELSE
        SELECT role.rolname INTO grantee_name
        FROM pg_catalog.pg_roles role
        WHERE role.oid = grantee_oid;
        IF grantee_name NOT IN (
          'nhi_rule_history_recovery_owner',
          'nhi_rule_history_recovery_authorizer'
        ) THEN
          EXECUTE pg_catalog.format(
            'REVOKE EXECUTE ON FUNCTION %s FROM %I',
            function_oid::regprocedure::text, grantee_name
          );
        END IF;
      END IF;
    END LOOP;
  END LOOP;
END;
$revoke_unsafe_existing_authorizers$;

REVOKE ALL ON
  nhi_rule_history_update_queue.v_work_dispatch_v2
  FROM nhi_rule_history_update_queue_runtime;

GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    verify_partition_recovery_admission(jsonb)
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    admit_partition_recovery(
      uuid, nhi_rule_history_update_ops.sha256_hex,
      jsonb, bytea, text, timestamptz
    )
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    authorize_partition_recovery(
      uuid, uuid, uuid, uuid, integer, integer,
      text, timestamptz, text, timestamptz
    )
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    show_partition_recovery(uuid, uuid)
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    revoke_partition_recovery(uuid, text, text, timestamptz)
  TO nhi_rule_history_recovery_authorizer;

GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      uuid, uuid, integer, uuid, uuid, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      uuid, uuid, text, integer, timestamptz,
      timestamptz
    )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      uuid, uuid, uuid, integer, uuid, uuid, smallint,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, uuid, uuid, text, text, text, text,
      nhi_rule_history_update_ops.sha256_hex, timestamptz
    )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      uuid, uuid, uuid, integer, uuid, uuid, text, text, uuid,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      integer, boolean, nhi_rule_history_update_ops.sha256_hex,
      jsonb, timestamptz
    )
  TO nhi_rule_history_update_queue_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      uuid, uuid, uuid, uuid, integer, uuid, uuid, text, text,
      nhi_rule_history_update_ops.sha256_hex, jsonb,
      uuid, uuid, uuid, timestamptz
    )
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
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex, text, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    uuid[], text, text, text, text, timestamptz
  )
  TO nhi_rule_history_recovery_authorizer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      uuid, uuid, uuid, integer, integer, uuid, text,
      nhi_rule_history_update_ops.sha256_hex, text, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, text, text, text, timestamptz
    )
  TO nhi_rule_history_recovery_authorizer;

DO $unauthorized_membership_guard$
DECLARE
  unsafe_path boolean;
BEGIN
  WITH RECURSIVE membership_path(member_oid, role_oid) AS (
    SELECT member, roleid FROM pg_catalog.pg_auth_members
    UNION
    SELECT path.member_oid, edge.roleid
    FROM membership_path path
    JOIN pg_catalog.pg_auth_members edge
      ON edge.member = path.role_oid
  )
  SELECT EXISTS (
    SELECT 1
    FROM membership_path path
    JOIN pg_catalog.pg_roles member_role
      ON member_role.oid = path.member_oid
    JOIN pg_catalog.pg_roles reached_role
      ON reached_role.oid = path.role_oid
    WHERE (
      member_role.rolname =
        'nhi_rule_history_update_queue_runtime'
      AND reached_role.rolname IN (
        'nhi_rule_history_recovery_authorizer',
        'nhi_rule_history_recovery_owner',
        'nhi_rule_history_candidate_runtime'
      )
    ) OR (
      member_role.rolname =
        'nhi_rule_history_candidate_runtime'
      AND reached_role.rolname IN (
        'nhi_rule_history_recovery_authorizer',
        'nhi_rule_history_recovery_owner',
        'nhi_rule_history_update_queue_runtime'
      )
    ) OR (
      member_role.rolname =
        'nhi_rule_history_recovery_authorizer'
      AND reached_role.rolname IN (
        'nhi_rule_history_recovery_owner',
        'nhi_rule_history_update_queue_runtime',
        'nhi_rule_history_candidate_runtime'
      )
    ) OR (
      member_role.rolname = 'nhi_rule_history_stage_writer'
      AND reached_role.rolname IN (
        'nhi_rule_history_recovery_owner',
        'nhi_rule_history_recovery_authorizer',
        'nhi_rule_history_update_queue_runtime',
        'nhi_rule_history_candidate_runtime'
      )
    )
  )
  INTO unsafe_path;
  IF unsafe_path THEN
    RAISE EXCEPTION
      'runtime, stage writer, candidate runtime, or operator authorizer has an unsafe transitive recovery authority path'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
END;
$unauthorized_membership_guard$;

INSERT INTO nhi_rule_history_partition_recovery.schema_migration (
  migration_id, contract_marker
) VALUES (
  '2026-07-28_nhi_rule_history_partition_recovery_a_plus',
  'managed=nhi_rule_history_partition_recovery/a-plus'
)
ON CONFLICT (migration_id) DO NOTHING;

COMMENT ON TABLE
  nhi_rule_history_partition_recovery.partition_recovery_admission IS
  'Typed immutable zero-call partition admission binding the complete generation-1 chain, sealed evidence, exact execution delta, unchanged worker semantics, and governance hashes.';
COMMENT ON TABLE
  nhi_rule_history_partition_recovery.generation_transition_evidence IS
  'General transition evidence envelope; it inventories typed receipts but can never replace their domain constraints.';
COMMENT ON TABLE
  nhi_rule_history_partition_recovery.worker_route_reservation IS
  'Generation-scoped durable pre-call reservation. execution_unknown forbids primary retry and fallback.';
COMMENT ON VIEW
  nhi_rule_history_update_queue.v_work_dispatch_v2 IS
  'Generation-2 recovery-only inspection surface. Runtime has no SELECT; execution requires exact consume_partition_recovery_dispatch arguments.';

COMMIT;
