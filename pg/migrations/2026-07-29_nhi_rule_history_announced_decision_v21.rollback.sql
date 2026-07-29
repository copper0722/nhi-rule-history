-- DESTRUCTIVE: disposable or never-populated databases only.
-- Production rollback is append-only:
--   SELECT nhi_rule_history_announced.set_release_control(
--     '<run-id>', 'deactivate', '<reason>', '{"ticket":"..."}'::jsonb
--   );
-- Never drop the production schema because it contains sealed source and audit
-- receipts.
BEGIN;
DROP SCHEMA IF EXISTS nhi_rule_history_announced CASCADE;
COMMIT;
