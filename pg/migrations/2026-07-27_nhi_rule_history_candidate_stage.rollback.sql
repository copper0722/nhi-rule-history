-- Guarded rollback for the stage-only continuous-update candidate schema.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_candidate_stage-global', 0)
);

DO $schema_guard$
DECLARE
  expected_comment text :=
    'Stage-only source-grounded proposals for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_candidate_stage/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_candidate_stage'
  ) THEN
    RETURN;
  END IF;
  SELECT obj_description(n.oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'nhi_rule_history_candidate_stage';
  IF existing_comment IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION
      'refusing rollback: candidate-stage managed marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  expected_comment text :=
    'NOLOGIN capability role for NHI source-grounded candidate staging only. managed=nhi_rule_history_candidate_runtime/v1';
  existing_comment text;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime'
  ) THEN
    SELECT shobj_description(oid, 'pg_authid')
      INTO existing_comment
    FROM pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime';
    IF existing_comment IS DISTINCT FROM expected_comment THEN
      RAISE EXCEPTION
        'refusing rollback: candidate runtime role marker does not match'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

REVOKE ALL ON nhi_rule_history_candidate_stage.current_candidate_state
  FROM nhi_rule_history_candidate_runtime;
REVOKE ALL ON FUNCTION
  nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(jsonb)
  FROM nhi_rule_history_candidate_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_candidate_stage
  FROM nhi_rule_history_candidate_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_candidate_stage
  FROM nhi_rule_history_candidate_runtime;
REVOKE ALL ON
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.worker_attempt,
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.bundle_receipt
  FROM nhi_rule_history_candidate_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_update_ops
  FROM nhi_rule_history_candidate_runtime;

DROP VIEW IF EXISTS
  nhi_rule_history_candidate_stage.current_candidate_state RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_candidate_stage.candidate_state_transition RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_candidate_stage.candidate_evidence RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_candidate_stage.candidate_source_span RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_candidate_stage.candidate_proposal RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_candidate_stage.guard_state_transition_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_candidate_stage.guard_candidate_proposal_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_candidate_stage.reject_truncate() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_candidate_stage.reject_append_only_change() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(jsonb)
  RESTRICT;
DROP DOMAIN IF EXISTS nhi_rule_history_candidate_stage.sha256_hex RESTRICT;
DROP SCHEMA IF EXISTS nhi_rule_history_candidate_stage RESTRICT;

DROP ROLE IF EXISTS nhi_rule_history_candidate_runtime;

COMMIT;
