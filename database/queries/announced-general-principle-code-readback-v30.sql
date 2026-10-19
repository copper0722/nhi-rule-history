-- Readback for v30 (the announced layer admits the project-assigned 通則 codes).
--
-- Run on a fresh connection after applying the migration:
--   psql -X -q -v ON_ERROR_STOP=1 -f database/queries/announced-general-principle-code-readback-v30.sql
-- Sections 1 to 3 each end in an "ok" column that should read t.  Section 4 is
-- informational.  The whole file is one read-only transaction: it writes
-- nothing.  It tests the codes with the pattern the live constraint holds, so a
-- wrong pattern shows, and it reads the constraints of the two tables v30 must
-- leave alone.

\set ON_ERROR_STOP on
BEGIN READ ONLY;

\echo '== 1. the clause_patch code check is the v30 check; the other two code checks are untouched =='
WITH live AS (
  SELECT con.conrelid::regclass::text AS relation, con.conname::text AS name,
         substring(pg_get_constraintdef(con.oid)
                   from '~ ''((?:[^'']|'''')*)''::text') AS pattern
  FROM pg_constraint AS con
  WHERE con.connamespace = 'nhi_rule_history_announced'::regnamespace
    AND con.contype = 'c'
    AND con.conname IN ('clause_patch_clause_code_check',
                        'composed_clause_version_clause_code_check',
                        'clause_reader_profile_clause_code_check')
)
SELECT relation, name, pattern,
       CASE name
         WHEN 'clause_patch_clause_code_check'
           THEN pattern = '^(0[.][1-9][0-9]*|[1-9][0-9]*(?:[.][0-9]+)+)$'
         ELSE pattern = '^[1-9][0-9]*(?:[.][0-9]+)+$'
       END AS ok
FROM live
ORDER BY name;

\echo '== 2. the live pattern admits 0.1 to 0.99 and dotted codes, and refuses the rest =='
WITH pattern AS (
  SELECT substring(pg_get_constraintdef(con.oid)
                   from '~ ''((?:[^'']|'''')*)''::text') AS value
  FROM pg_constraint AS con
  WHERE con.conrelid = 'nhi_rule_history_announced.clause_patch'::regclass
    AND con.conname = 'clause_patch_clause_code_check'
), probe(code, expected) AS (
  VALUES ('0.1', true), ('0.4', true), ('0.12', true), ('0.99', true),
         ('2.1.4.2', true), ('4.2.9', true), ('9.140', true),
         ('0.0', false), ('0.04', false), ('00.4', false), ('0.4.1', false),
         ('0', false), ('4', false), ('', false), ('a.b', false), ('0.', false),
         ('0.4 ', false), (' 0.4', false), ('通則', false)
), result AS (
  SELECT code, expected, code ~ pattern.value AS admitted
  FROM probe CROSS JOIN pattern
)
SELECT code, expected, admitted, admitted = expected AS ok
FROM result
ORDER BY expected DESC, code;

\echo '== 3. every clause_patch row satisfies the live check =='
WITH pattern AS (
  SELECT substring(pg_get_constraintdef(con.oid)
                   from '~ ''((?:[^'']|'''')*)''::text') AS value
  FROM pg_constraint AS con
  WHERE con.conrelid = 'nhi_rule_history_announced.clause_patch'::regclass
    AND con.conname = 'clause_patch_clause_code_check'
)
SELECT count(*) AS patches,
       count(*) FILTER (WHERE patch.clause_code !~ pattern.value) AS violating,
       count(*) FILTER (WHERE patch.clause_code !~ pattern.value) = 0 AS ok
FROM nhi_rule_history_announced.clause_patch AS patch
CROSS JOIN pattern;

\echo '== 4. (informational) project-assigned patches by run state =='
SELECT run.state, count(*) AS project_assigned_patches
FROM nhi_rule_history_announced.clause_patch AS patch
JOIN nhi_rule_history_announced.release_run AS run USING (run_id)
WHERE patch.clause_code ~ '^0[.]'
GROUP BY run.state
ORDER BY run.state;

ROLLBACK;
