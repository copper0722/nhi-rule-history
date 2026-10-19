-- Rollback of v30: narrow nhi_rule_history_announced.clause_patch_clause_code_check
-- back to the v21 pattern (dotted codes that start with 1 to 9).
--
-- Refused, changing nothing, while any clause_patch row holds a 0.N code.  A
-- loaded run is sealed and write-once, so such a row cannot be removed, and a
-- check that its own rows violate cannot be added: once a run that holds a 0.N
-- patch has been loaded, this check stays wide.  Deactivating that run (the
-- loader's rollback) does not delete it.
-- The composed_clause_version and clause_reader_profile checks were never
-- changed and are not touched here.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-general-principle-code-v30', 0)
);
SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-global', 0)
);

DO $guard$
DECLARE
  definition text;
  pattern text;
  project_assigned bigint;
BEGIN
  SELECT pg_get_constraintdef(con.oid) INTO definition
  FROM pg_constraint con
  WHERE con.conrelid = 'nhi_rule_history_announced.clause_patch'::regclass
    AND con.conname = 'clause_patch_clause_code_check'
    AND con.contype = 'c';
  IF definition IS NULL THEN
    RAISE EXCEPTION 'v30 rollback refused: clause_patch_clause_code_check is missing';
  END IF;
  pattern := substring(definition from '~ ''((?:[^'']|'''')*)''::text');
  IF pattern IS DISTINCT FROM '^(0[.][1-9][0-9]*|[1-9][0-9]*(?:[.][0-9]+)+)$' THEN
    RAISE EXCEPTION
      'v30 rollback refused: clause_patch_clause_code_check is not the v30 check: %',
      definition;
  END IF;
  SELECT count(*) INTO project_assigned
  FROM nhi_rule_history_announced.clause_patch
  WHERE clause_code !~ '^[1-9][0-9]*(?:[.][0-9]+)+$';
  IF project_assigned > 0 THEN
    RAISE EXCEPTION
      'v30 rollback refused: % clause_patch row(s) hold a project-assigned 0.N code; sealed runs are write-once, so the check stays wide',
      project_assigned;
  END IF;
END
$guard$;

ALTER TABLE nhi_rule_history_announced.clause_patch
  DROP CONSTRAINT clause_patch_clause_code_check,
  ADD CONSTRAINT clause_patch_clause_code_check
    CHECK (clause_code ~ '^[1-9][0-9]*(?:[.][0-9]+)+$');

COMMENT ON CONSTRAINT clause_patch_clause_code_check
  ON nhi_rule_history_announced.clause_patch IS NULL;

COMMIT;
