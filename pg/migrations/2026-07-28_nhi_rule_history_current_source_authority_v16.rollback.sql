BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-current-source-authority-v16', 0)
);

DROP VIEW IF EXISTS
  nhi_rule_history_edition.v_current_source_authority;

DROP TRIGGER IF EXISTS current_source_authority_policy_no_update
  ON nhi_rule_history_edition.current_source_authority_policy;
DROP TRIGGER IF EXISTS current_source_authority_policy_no_delete
  ON nhi_rule_history_edition.current_source_authority_policy;

DROP TABLE IF EXISTS
  nhi_rule_history_edition.current_source_authority_policy;

DROP FUNCTION IF EXISTS
  nhi_rule_history_edition.reject_current_source_authority_mutation();

COMMIT;
