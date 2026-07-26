-- 2026-07-26 — fail-closed bounded rollback for tw_drug_history_stage only
--
-- Drops only allowlisted stage objects, then schema drop with RESTRICT.
-- Never cross-schema-cascade-delete dependents in other schemas.
-- Does NOT touch tw_drug, tw_drug_history, or any other schema.
-- Operator-only; this work unit never executes against a live database.
--
-- Lock order (must match forward migration + loader apply/drop-run):
--   1) global stage lock key tw_drug_history_stage-global

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

SELECT pg_advisory_xact_lock(
  hashtextextended('tw_drug_history_stage-global', 0)
);

DO $rollback$
DECLARE
  managed_comment text :=
    'Isolated immutable staging for NHI rule-history occurrence rebuild runs; not legal history. managed=tw_drug_history_stage/v1';
  existing_comment text;
  schema_oid oid;
  leftover text;
  n int;
BEGIN
  SELECT n.oid, obj_description(n.oid, 'pg_namespace')
    INTO schema_oid, existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'tw_drug_history_stage';

  IF schema_oid IS NULL THEN
    -- Idempotent: schema already absent.
    RETURN;
  END IF;

  IF existing_comment IS DISTINCT FROM managed_comment THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — schema comment is not the managed marker'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  -- Refuse if any non-allowlisted relation exists (tables/views/matviews/sequences).
  SELECT c.relname INTO leftover
  FROM pg_class c
  WHERE c.relnamespace = schema_oid
    AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    AND c.relname NOT IN (
      'rebuild_run',
      'run_input_file',
      'source_release',
      'source_artifact',
      'release_artifact',
      'structural_block',
      'occurrence_candidate',
      'stage_issue'
    )
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — unexpected relation %', leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  -- Refuse unexpected functions (allowlist only the two immutability guards).
  SELECT p.proname INTO leftover
  FROM pg_proc p
  WHERE p.pronamespace = schema_oid
    AND p.proname NOT IN (
      'guard_evidence_dml',
      'reject_evidence_truncate',
      'reject_evidence_update',
      'rebuild_run_update_guard',
      'rebuild_run_delete_guard'
    )
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — unexpected function %', leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  -- Drop allowlisted tables in safe dependency order (no cascade option).
  -- External dependents cause the DROP to fail and roll back the transaction.
  DROP TABLE IF EXISTS tw_drug_history_stage.stage_issue;
  DROP TABLE IF EXISTS tw_drug_history_stage.occurrence_candidate;
  DROP TABLE IF EXISTS tw_drug_history_stage.structural_block;
  DROP TABLE IF EXISTS tw_drug_history_stage.release_artifact;
  DROP TABLE IF EXISTS tw_drug_history_stage.source_release;
  DROP TABLE IF EXISTS tw_drug_history_stage.source_artifact;
  DROP TABLE IF EXISTS tw_drug_history_stage.run_input_file;
  DROP TABLE IF EXISTS tw_drug_history_stage.rebuild_run;

  DROP FUNCTION IF EXISTS tw_drug_history_stage.guard_evidence_dml();
  DROP FUNCTION IF EXISTS tw_drug_history_stage.reject_evidence_truncate();
  DROP FUNCTION IF EXISTS tw_drug_history_stage.reject_evidence_update();
  DROP FUNCTION IF EXISTS tw_drug_history_stage.rebuild_run_update_guard();
  DROP FUNCTION IF EXISTS tw_drug_history_stage.rebuild_run_delete_guard();

  DROP DOMAIN IF EXISTS tw_drug_history_stage.sha256_hex;

  -- Any remaining object must fail closed (RESTRICT will also fail).
  SELECT count(*) INTO n
  FROM pg_class c
  WHERE c.relnamespace = schema_oid;
  IF n <> 0 THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — % relation(s) remain after allowlisted drops',
      n
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT count(*) INTO n
  FROM pg_proc p
  WHERE p.pronamespace = schema_oid;
  IF n <> 0 THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — % function(s) remain after allowlisted drops',
      n
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT count(*) INTO n
  FROM pg_type t
  WHERE t.typnamespace = schema_oid
    AND t.typtype IN ('d', 'c', 'e', 'r');
  IF n <> 0 THEN
    RAISE EXCEPTION
      'tw_drug_history_stage: refuse rollback — % type(s) remain after allowlisted drops',
      n
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  -- RESTRICT: fail if any external dependency still references the schema.
  DROP SCHEMA tw_drug_history_stage RESTRICT;
END;
$rollback$;

COMMIT;
