BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-source-transcript-v19', 0)
);

DROP SCHEMA IF EXISTS nhi_rule_history_transcript CASCADE;

COMMIT;
