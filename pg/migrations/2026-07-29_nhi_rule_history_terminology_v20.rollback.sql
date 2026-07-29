-- Roll back the isolated terminology projection.  The shared btree_gist
-- extension is deliberately retained.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-terminology-v20', 0)
);

DROP SCHEMA IF EXISTS nhi_rule_history_terminology CASCADE;

COMMIT;
