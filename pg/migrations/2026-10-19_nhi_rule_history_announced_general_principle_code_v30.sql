-- 2026-10-19 — the announced layer admits the project-assigned 通則 clause codes (v30).
--
-- The general principles (通則) are published as lettered points (一、 to 十二、)
-- and the project names point N as clause 0.N (code_origin = project_assigned;
-- the publication layer has held 0.1 to 0.12 since v18).  A comparison table
-- can amend such a point, for example the 四、 point on injectable drugs, and
-- the announced layer then has to hold a clause_patch row for 0.4.  Since v21
-- its clause_patch_clause_code_check admits dotted codes that start with
-- 1 to 9 only, so the database refuses the row and with it the whole load.
--
-- This migration widens ONLY nhi_rule_history_announced.clause_patch_clause_code_check
-- to the publication layer's pattern: either 0.N (N >= 1, no leading zero) or
-- a dotted code that starts with 1 to 9.  The same-named checks of
-- composed_clause_version and clause_reader_profile are left as they are: a
-- 通則 point is only ever a patch_only patch.
--
-- What it is not: no row is written or removed, no loader, lane, hold or
-- consumer changes behavior by itself.  The loader reads the live constraint
-- (announced_release.general_principle_patches_admitted) and projects a 0.N
-- point as a patch only when this check admits it, so the same loader is safe
-- before and after this file is applied.  Applying it twice is refused: the
-- guard below requires the v21 check.
--
-- Rollback: 2026-10-19_nhi_rule_history_announced_general_principle_code_v30.rollback.sql
-- restores the v21 check exactly, and refuses while any clause_patch row holds
-- a 0.N code.  Sealed runs are write-once, so once a run that holds a 0.N patch
-- has been loaded the check cannot be narrowed again.
-- Readback: database/queries/announced-general-principle-code-readback-v30.sql.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-general-principle-code-v30', 0)
);
-- Serialize with overlay loads, activations and rollbacks, which hold this lock.
SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-global', 0)
);

DO $guard$
DECLARE
  definition text;
  pattern text;
BEGIN
  SELECT pg_get_constraintdef(con.oid) INTO definition
  FROM pg_constraint con
  WHERE con.conrelid = 'nhi_rule_history_announced.clause_patch'::regclass
    AND con.conname = 'clause_patch_clause_code_check'
    AND con.contype = 'c';
  IF definition IS NULL THEN
    RAISE EXCEPTION 'v30 refused: clause_patch_clause_code_check is missing';
  END IF;
  pattern := substring(definition from '~ ''((?:[^'']|'''')*)''::text');
  IF pattern IS DISTINCT FROM '^[1-9][0-9]*(?:[.][0-9]+)+$' THEN
    RAISE EXCEPTION
      'v30 refused: clause_patch_clause_code_check is not the v21 check (already widened?): %',
      definition;
  END IF;
END
$guard$;

ALTER TABLE nhi_rule_history_announced.clause_patch
  DROP CONSTRAINT clause_patch_clause_code_check,
  ADD CONSTRAINT clause_patch_clause_code_check
    CHECK (clause_code ~ '^(0[.][1-9][0-9]*|[1-9][0-9]*(?:[.][0-9]+)+)$');

COMMENT ON CONSTRAINT clause_patch_clause_code_check
  ON nhi_rule_history_announced.clause_patch IS
  'A dotted clause code that starts with 1 to 9, or the project-assigned code 0.N of a 通則 point (v30; the pattern of nhi_rule_history_publication.current_clause).';

COMMIT;
