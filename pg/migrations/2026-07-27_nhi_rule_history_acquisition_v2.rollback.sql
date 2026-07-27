-- Guarded rollback for the isolated v2 acquisition/raw evidence schema.
-- Every drop is explicit and RESTRICT; external dependencies fail atomically.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_acq_stage-global', 0)
);

DO $schema_guard$
DECLARE
  expected_comment text :=
    'Isolated immutable acquisition/raw evidence for NHI rule-history v2; not legal history. managed=tw_drug_history_acq_stage/v2';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'tw_drug_history_acq_stage'
  ) THEN
    RAISE EXCEPTION 'tw_drug_history_acq_stage does not exist';
  END IF;
  SELECT obj_description(n.oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'tw_drug_history_acq_stage';
  IF existing_comment IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION
      'refusing rollback: managed v2 schema marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

DROP TABLE IF EXISTS tw_drug_history_acq_stage.acquisition_issue RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.artifact_url_observation RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.resource_artifact_link RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.fetch_attempt RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.raw_artifact RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.discovered_resource RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.discovery_observation RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.input_file RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_acq_stage.acquisition_run RESTRICT;

DROP FUNCTION IF EXISTS tw_drug_history_acq_stage.reject_run_delete() RESTRICT;
DROP FUNCTION IF EXISTS tw_drug_history_acq_stage.guard_run_update() RESTRICT;
DROP FUNCTION IF EXISTS tw_drug_history_acq_stage.reject_truncate() RESTRICT;
DROP FUNCTION IF EXISTS tw_drug_history_acq_stage.guard_evidence_dml() RESTRICT;
DROP DOMAIN IF EXISTS tw_drug_history_acq_stage.sha256_hex RESTRICT;
DROP SCHEMA tw_drug_history_acq_stage RESTRICT;

COMMIT;
