-- Guarded rollback for the isolated v2 structural evidence schema.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_structural_stage-global', 0)
);

DO $schema_guard$
DECLARE
  expected_comment text :=
    'Isolated immutable source-local structural parser evidence for NHI rule-history v2; not legal history. managed=tw_drug_history_structural_stage/v2';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'tw_drug_history_structural_stage'
  ) THEN
    RAISE EXCEPTION 'tw_drug_history_structural_stage does not exist';
  END IF;
  SELECT obj_description(n.oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'tw_drug_history_structural_stage';
  IF existing_comment IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION 'refusing rollback: managed v2 marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$schema_guard$;

DROP TABLE IF EXISTS tw_drug_history_structural_stage.parse_issue RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_structural_stage.occurrence_candidate RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_structural_stage.structural_block RESTRICT;
DROP TABLE IF EXISTS tw_drug_history_structural_stage.parse_run RESTRICT;
DROP FUNCTION IF EXISTS
  tw_drug_history_structural_stage.reject_parse_run_delete() RESTRICT;
DROP FUNCTION IF EXISTS
  tw_drug_history_structural_stage.guard_parse_run_update() RESTRICT;
DROP FUNCTION IF EXISTS
  tw_drug_history_structural_stage.reject_truncate() RESTRICT;
DROP FUNCTION IF EXISTS
  tw_drug_history_structural_stage.guard_evidence_dml() RESTRICT;
DROP DOMAIN IF EXISTS tw_drug_history_structural_stage.sha256_hex RESTRICT;
DROP SCHEMA tw_drug_history_structural_stage RESTRICT;

COMMIT;
