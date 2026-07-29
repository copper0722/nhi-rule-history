-- DESTRUCTIVE: disposable or never-populated databases only.
-- Production recovery uses append-only release_control_event and
-- patch_resolution_event rows; it does not run this file.
BEGIN;

DROP VIEW IF EXISTS nhi_rule_history_announced.v_public_decision_model;
DROP VIEW IF EXISTS nhi_rule_history_announced.v_public_clause_patch;
DROP VIEW IF EXISTS nhi_rule_history_announced.v_current_patch_resolution;

CREATE OR REPLACE VIEW nhi_rule_history_announced.v_active_run AS
SELECT run.*
FROM nhi_rule_history_announced.release_activation activation
JOIN nhi_rule_history_announced.release_run run USING (run_id)
ORDER BY activation.activation_id DESC
LIMIT 1;

DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.set_patch_resolution(uuid,uuid,text,text,jsonb);
DROP FUNCTION IF EXISTS
  nhi_rule_history_announced.set_release_control(uuid,text,text,jsonb);
DROP TABLE IF EXISTS nhi_rule_history_announced.patch_resolution_event;
DROP TABLE IF EXISTS nhi_rule_history_announced.release_control_event;

COMMIT;
