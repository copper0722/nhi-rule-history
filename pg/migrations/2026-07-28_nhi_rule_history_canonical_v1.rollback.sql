-- Guarded rollback for canonical NHI rule-history v1.
--
-- Any row in a managed canonical table is evidence-bearing state and blocks
-- this rollback.  A promotion receipt is called out separately for clarity.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history-canonical-v1-global', 0)
);

DO $schema_guard$
DECLARE
  expected_prefix text :=
    'Canonical NHI drug reimbursement-rule history. managed=nhi_rule_history_canonical/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'nhi_rule_history'
  ) THEN
    RETURN;
  END IF;

  SELECT obj_description(oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace
  WHERE nspname = 'nhi_rule_history';

  IF existing_comment !~
     (
       '^' || expected_prefix ||
       ' contract_sha256=[0-9a-f]{64}$'
     ) THEN
    RAISE EXCEPTION
      'refusing canonical rollback: managed marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_promotion'
  ) THEN
    RAISE EXCEPTION
      'refusing canonical rollback: remove the managed promotion schema first'
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;
END;
$schema_guard$;

LOCK TABLE
  nhi_rule_history.dataset_release,
  nhi_rule_history.source_artifact,
  nhi_rule_history.artifact_format_detection,
  nhi_rule_history.artifact_format_detection_review,
  nhi_rule_history.release_artifact,
  nhi_rule_history.rule_identity,
  nhi_rule_history.rule_designation,
  nhi_rule_history.rule_snapshot,
  nhi_rule_history.rule_head,
  nhi_rule_history.official_event,
  nhi_rule_history.official_event_effect,
  nhi_rule_history.snapshot_evidence,
  nhi_rule_history.comparison_edge,
  nhi_rule_history.promotion_receipt
IN ACCESS EXCLUSIVE MODE;

DO $nonempty_guard$
DECLARE
  table_name text;
  has_rows boolean;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'dataset_release',
    'source_artifact',
    'artifact_format_detection',
    'artifact_format_detection_review',
    'release_artifact',
    'rule_identity',
    'rule_designation',
    'rule_snapshot',
    'rule_head',
    'official_event',
    'official_event_effect',
    'snapshot_evidence',
    'comparison_edge',
    'promotion_receipt'
  ]
  LOOP
    EXECUTE format(
      'SELECT EXISTS (SELECT 1 FROM nhi_rule_history.%I)',
      table_name
    ) INTO has_rows;
    IF has_rows THEN
      RAISE EXCEPTION
        'refusing canonical rollback: managed table % is nonempty',
        table_name
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END LOOP;
END;
$nonempty_guard$;

DO $role_guard$
DECLARE
  role_name text;
  expected_comment text;
  existing_comment text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'nhi_rule_history_owner',
    'nhi_rule_history_reader',
    'nhi_rule_history_format_detector_writer',
    'nhi_rule_history_format_detector_reviewer',
    'nhi_rule_history_promotion_writer',
    'nhi_rule_history_promotion_reviewer',
    'nhi_rule_history_promotion_executor'
  ]
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      expected_comment := CASE role_name
        WHEN 'nhi_rule_history_owner' THEN
          'NOLOGIN owner for canonical NHI rule-history objects and the audited promotion function. managed=nhi_rule_history_owner/v1'
        WHEN 'nhi_rule_history_reader' THEN
          'NOLOGIN read-only capability for canonical NHI rule history. managed=nhi_rule_history_reader/v1'
        WHEN 'nhi_rule_history_format_detector_writer' THEN
          'NOLOGIN capability for byte-derived format registration through the sealed detector function only. managed=nhi_rule_history_format_detector_writer/v1'
        WHEN 'nhi_rule_history_format_detector_reviewer' THEN
          'NOLOGIN capability for independent byte-derived format attestation through the sealed verifier function only. managed=nhi_rule_history_format_detector_reviewer/v1'
        WHEN 'nhi_rule_history_promotion_writer' THEN
          'NOLOGIN capability for immutable promotion evidence production; no review, execution, or canonical DML. managed=nhi_rule_history_promotion_writer/v1'
        WHEN 'nhi_rule_history_promotion_reviewer' THEN
          'NOLOGIN capability for independent ready/rejected decisions; no evidence production, execution, or canonical DML. managed=nhi_rule_history_promotion_reviewer/v1'
        ELSE
          'NOLOGIN capability for canonical promotion SELECT and promote_case execution only; no evidence or canonical DML. managed=nhi_rule_history_promotion_executor/v1'
      END;
      SELECT shobj_description(oid, 'pg_authid')
        INTO existing_comment
      FROM pg_roles
      WHERE rolname = role_name;
      IF existing_comment IS DISTINCT FROM expected_comment THEN
        RAISE EXCEPTION
          'refusing canonical rollback: role % marker does not match',
          role_name
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    END IF;
  END LOOP;
END;
$role_guard$;

REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history
  FROM nhi_rule_history_reader;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_reader;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history.register_artifact_format_detection(
    text, text, text, bytea
  )
  FROM nhi_rule_history_format_detector_writer;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history.attest_artifact_format_detection(
    text, text, text, bytea
  )
  FROM nhi_rule_history_format_detector_reviewer;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_format_detector_writer;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_format_detector_reviewer;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_writer;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_writer;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_reviewer;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_reviewer;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_executor;
REVOKE ALL ON SCHEMA nhi_rule_history
  FROM nhi_rule_history_promotion_executor;

DROP FUNCTION IF EXISTS
  nhi_rule_history.attest_artifact_format_detection(
    text, text, text, bytea
  ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history.register_artifact_format_detection(
    text, text, text, bytea
  ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history.inspect_odf_container_reviewer(bytea) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history.inspect_odf_container_detector(bytea) RESTRICT;

DROP TABLE IF EXISTS nhi_rule_history.comparison_edge RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.snapshot_evidence RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.official_event_effect RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.promotion_receipt RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.rule_head RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.rule_snapshot RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.rule_designation RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.rule_identity RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.official_event RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history.artifact_format_detection_review RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history.artifact_format_detection RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.release_artifact RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.source_artifact RESTRICT;
DROP TABLE IF EXISTS nhi_rule_history.dataset_release RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history.reject_receipt_mutation() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history.reject_format_detection_mutation() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history.guard_snapshot_interval() RESTRICT;
DROP DOMAIN IF EXISTS nhi_rule_history.sha256_hex RESTRICT;
DROP SCHEMA IF EXISTS nhi_rule_history RESTRICT;

DROP ROLE IF EXISTS nhi_rule_history_promotion_executor;
DROP ROLE IF EXISTS nhi_rule_history_promotion_reviewer;
DROP ROLE IF EXISTS nhi_rule_history_promotion_writer;
DROP ROLE IF EXISTS nhi_rule_history_reader;
DROP ROLE IF EXISTS nhi_rule_history_format_detector_reviewer;
DROP ROLE IF EXISTS nhi_rule_history_format_detector_writer;
DROP ROLE IF EXISTS nhi_rule_history_owner;

COMMIT;
