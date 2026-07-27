-- Guarded rollback for the stage-only per-RSS-identity update queue.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $schema_guard$
DECLARE
  expected_comment text :=
    'Stage-only durable per-RSS-identity work queue for the NHI rule-history updater; not legal history. managed=nhi_rule_history_update_queue/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_queue'
  ) THEN
    RETURN;
  END IF;
  SELECT obj_description(n.oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'nhi_rule_history_update_queue';
  IF existing_comment IS DISTINCT FROM expected_comment
     OR NOT EXISTS (
       SELECT 1
       FROM nhi_rule_history_update_queue.schema_migration
       WHERE migration_id =
         '2026-07-27_nhi_rule_history_update_queue'
         AND contract_marker =
           'managed=nhi_rule_history_update_queue/v1'
     ) THEN
    RAISE EXCEPTION
      'refusing rollback: update queue managed markers do not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  expected_comment text :=
    'NOLOGIN capability role for stage-only NHI RSS queue operations. managed=nhi_rule_history_update_queue_runtime/v1';
  existing_comment text;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_queue_runtime'
  ) THEN
    SELECT shobj_description(oid, 'pg_authid')
      INTO existing_comment
    FROM pg_roles
    WHERE rolname = 'nhi_rule_history_update_queue_runtime';
    IF existing_comment IS DISTINCT FROM expected_comment THEN
      RAISE EXCEPTION
        'refusing rollback: update queue runtime marker does not match'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

REVOKE ALL ON
  nhi_rule_history_update_queue.v_work_backlog,
  nhi_rule_history_update_queue.v_work_item_current,
  nhi_rule_history_update_queue.work_item_attempt,
  nhi_rule_history_update_queue.work_item_transition,
  nhi_rule_history_update_queue.rss_work_observation,
  nhi_rule_history_update_queue.rss_work_item
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_update_queue
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON nhi_rule_history_candidate_stage.candidate_proposal
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON TYPE nhi_rule_history_candidate_stage.sha256_hex
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_candidate_stage
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON
  nhi_rule_history_update_ops.feed_item_observation,
  nhi_rule_history_update_ops.feed_observation,
  nhi_rule_history_update_ops.url_observation,
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.job_lease,
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.bundle_receipt
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON TYPE nhi_rule_history_update_ops.sha256_hex
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON SCHEMA nhi_rule_history_update_ops
  FROM nhi_rule_history_update_queue_runtime;

DROP VIEW IF EXISTS
  nhi_rule_history_update_queue.v_work_backlog RESTRICT;
DROP VIEW IF EXISTS
  nhi_rule_history_update_queue.v_work_item_current RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.work_item_attempt RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.work_item_transition RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.rss_work_observation RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.rss_work_item RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.schema_migration RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_work_attempt_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_transition_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_work_observation_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_work_item_insert() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.reject_truncate() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.reject_append_only_change() RESTRICT;
DROP SCHEMA IF EXISTS nhi_rule_history_update_queue RESTRICT;

DROP ROLE IF EXISTS nhi_rule_history_update_queue_runtime;

COMMIT;
