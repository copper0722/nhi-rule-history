-- 2026-07-27 — bounded rollback for the isolated annotation stage

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_annotation_stage-global', 0)
);

DO $rollback$
DECLARE
  managed_comment text :=
    'Isolated append-only legacy date-marker stage; not legal history. managed=nhi_rule_history_annotation_stage/v1';
  schema_oid oid;
  existing_comment text;
  leftover text;
  remaining_count integer;
BEGIN
  SELECT namespace_row.oid,
         obj_description(namespace_row.oid, 'pg_namespace')
    INTO schema_oid, existing_comment
  FROM pg_namespace namespace_row
  WHERE namespace_row.nspname =
    'nhi_rule_history_annotation_stage';

  IF schema_oid IS NULL THEN
    RETURN;
  END IF;

  IF existing_comment IS DISTINCT FROM managed_comment THEN
    RAISE EXCEPTION
      'nhi_rule_history_annotation_stage: managed marker mismatch'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT relation_row.relname
    INTO leftover
  FROM pg_class relation_row
  WHERE relation_row.relnamespace = schema_oid
    AND relation_row.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    AND relation_row.relname NOT IN (
      'annotation_run',
      'legacy_article_observation',
      'date_annotation',
      'v_rule_date_coverage'
    )
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'nhi_rule_history_annotation_stage: unexpected relation %',
      leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT procedure_row.proname
    INTO leftover
  FROM pg_proc procedure_row
  WHERE procedure_row.pronamespace = schema_oid
    AND procedure_row.proname NOT IN (
      'reject_evidence_mutation'
    )
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'nhi_rule_history_annotation_stage: unexpected function %',
      leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  DROP VIEW IF EXISTS
    nhi_rule_history_annotation_stage.v_rule_date_coverage;
  DROP TABLE IF EXISTS
    nhi_rule_history_annotation_stage.date_annotation;
  DROP TABLE IF EXISTS
    nhi_rule_history_annotation_stage.legacy_article_observation;
  DROP TABLE IF EXISTS
    nhi_rule_history_annotation_stage.annotation_run;
  DROP FUNCTION IF EXISTS
    nhi_rule_history_annotation_stage.reject_evidence_mutation();
  DROP DOMAIN IF EXISTS
    nhi_rule_history_annotation_stage.sha256_hex;

  SELECT count(*)
    INTO remaining_count
  FROM pg_class relation_row
  WHERE relation_row.relnamespace = schema_oid;
  IF remaining_count <> 0 THEN
    RAISE EXCEPTION
      'nhi_rule_history_annotation_stage: % relation(s) remain',
      remaining_count
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT count(*)
    INTO remaining_count
  FROM pg_proc procedure_row
  WHERE procedure_row.pronamespace = schema_oid;
  IF remaining_count <> 0 THEN
    RAISE EXCEPTION
      'nhi_rule_history_annotation_stage: % function(s) remain',
      remaining_count
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  DROP SCHEMA nhi_rule_history_annotation_stage RESTRICT;
END;
$rollback$;

COMMIT;
