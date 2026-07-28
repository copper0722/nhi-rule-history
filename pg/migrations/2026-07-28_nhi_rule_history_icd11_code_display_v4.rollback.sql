-- Exact schema rollback for ICD-11 code-only reader display v4.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-icd11-code-display-v4', 0)
);

DROP TABLE IF EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_code;

COMMIT;
