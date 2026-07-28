-- Exact rollback for the additive single-clause schema.
--
-- This removes only `nhi_rule_history_clause`. The source-edition schema and
-- every upstream source/acquisition/structural stage remain untouched.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-v1', 0)
);

DROP SCHEMA IF EXISTS nhi_rule_history_clause CASCADE;

COMMIT;
