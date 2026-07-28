BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-edition-v1', 0)
);

-- This is intentionally destructive and is not part of normal operation.
-- Export the schema first when it contains any accepted import run.
DROP SCHEMA IF EXISTS nhi_rule_history_edition CASCADE;

COMMIT;
