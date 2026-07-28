-- Guarded rollback for the stage-only continuous-updater operational schema.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_ops-global', 0)
);

DO $schema_guard$
DECLARE
  expected_comment text :=
    'Stage-only operational evidence for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_update_ops/v1';
  existing_comment text;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_candidate_stage'
  ) OR EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime'
  ) THEN
    RAISE EXCEPTION
      'candidate stage and its runtime role must be rolled back before update operations'
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_ops'
  ) THEN
    RETURN;
  END IF;
  SELECT obj_description(n.oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'nhi_rule_history_update_ops';
  IF existing_comment IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION
      'refusing rollback: update-ops managed marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  expected_comment text :=
    'NOLOGIN capability role for stage-only NHI updater operations. managed=nhi_rule_history_update_runtime/v1';
  existing_comment text;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_runtime'
  ) THEN
    SELECT shobj_description(oid, 'pg_authid')
      INTO existing_comment
    FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_runtime';
    IF existing_comment IS DISTINCT FROM expected_comment THEN
      RAISE EXCEPTION
        'refusing rollback: update runtime role marker does not match'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_update_ops
  FROM nhi_rule_history_update_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_update_ops
  FROM nhi_rule_history_update_runtime;

DROP TABLE IF EXISTS nhi_rule_history_update_ops.feed_item_observation RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.feed_observation RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.bundle_receipt RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.url_observation RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.content_artifact RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.worker_attempt RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.job_lease RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history_update_ops.update_job RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_update_ops.guard_owned_observation_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_ops.guard_worker_attempt_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_ops.guard_lease_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_ops.reject_truncate() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_ops.reject_append_only_change() RESTRICT;
DROP DOMAIN IF EXISTS nhi_rule_history_update_ops.sha256_hex RESTRICT;
DROP SCHEMA IF EXISTS nhi_rule_history_update_ops RESTRICT;

DROP ROLE IF EXISTS nhi_rule_history_update_runtime;

COMMIT;
