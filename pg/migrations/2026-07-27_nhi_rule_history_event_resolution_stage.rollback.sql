-- Guarded rollback for the isolated event-resolution evidence stage.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_event_resolution_stage-global', 0)
);

DO $rollback$
DECLARE
  expected_comment text :=
    'Append-only annotation-to-official-event candidate evidence; not legal history. managed=nhi_rule_history_event_resolution_stage/v1';
  existing_comment text;
  schema_oid oid;
  leftover text;
  remaining_count integer;
BEGIN
  SELECT n.oid, obj_description(n.oid, 'pg_namespace')
    INTO schema_oid, existing_comment
  FROM pg_namespace n
  WHERE n.nspname = 'nhi_rule_history_event_resolution_stage';

  IF schema_oid IS NULL THEN
    RETURN;
  END IF;
  IF existing_comment IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION
      'refusing rollback: event-resolution managed marker mismatch'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT relation_row.relname
    INTO leftover
  FROM pg_class relation_row
  WHERE relation_row.relnamespace = schema_oid
    AND relation_row.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    AND relation_row.relname NOT IN (
      'resolution_run',
      'annotation_observation',
      'official_event_effect_observation',
      'candidate_observation',
      'resolution_outcome',
      'v_resolution_status_counts'
    )
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'event resolution stage: unexpected relation %', leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT procedure_row.proname
    INTO leftover
  FROM pg_proc procedure_row
  WHERE procedure_row.pronamespace = schema_oid
    AND procedure_row.proname NOT IN ('reject_evidence_mutation')
  LIMIT 1;
  IF leftover IS NOT NULL THEN
    RAISE EXCEPTION
      'event resolution stage: unexpected function %', leftover
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  DROP VIEW IF EXISTS
    nhi_rule_history_event_resolution_stage
    .v_resolution_status_counts;
  DROP TABLE IF EXISTS
    nhi_rule_history_event_resolution_stage.resolution_outcome;
  DROP TABLE IF EXISTS
    nhi_rule_history_event_resolution_stage.candidate_observation;
  DROP TABLE IF EXISTS
    nhi_rule_history_event_resolution_stage
    .official_event_effect_observation;
  DROP TABLE IF EXISTS
    nhi_rule_history_event_resolution_stage.annotation_observation;
  DROP TABLE IF EXISTS
    nhi_rule_history_event_resolution_stage.resolution_run;
  DROP FUNCTION IF EXISTS
    nhi_rule_history_event_resolution_stage
    .reject_evidence_mutation();
  DROP DOMAIN IF EXISTS
    nhi_rule_history_event_resolution_stage.sha256_hex;

  SELECT count(*)
    INTO remaining_count
  FROM pg_class relation_row
  WHERE relation_row.relnamespace = schema_oid;
  IF remaining_count <> 0 THEN
    RAISE EXCEPTION
      'event resolution stage: % relation(s) remain', remaining_count
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  SELECT count(*)
    INTO remaining_count
  FROM pg_proc procedure_row
  WHERE procedure_row.pronamespace = schema_oid;
  IF remaining_count <> 0 THEN
    RAISE EXCEPTION
      'event resolution stage: % function(s) remain', remaining_count
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;

  DROP SCHEMA
    nhi_rule_history_event_resolution_stage RESTRICT;
END;
$rollback$;

COMMIT;
