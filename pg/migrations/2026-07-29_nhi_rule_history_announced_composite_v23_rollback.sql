-- DESTRUCTIVE ROLLBACK: disposable or never-populated v23 databases only.
-- Production rollback uses append-only release_control_event, never this file.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-composite-v23', 0)
);

DROP VIEW IF EXISTS
  nhi_rule_history_announced.v_public_composed_clause_version;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.composed_clause_reimbursement_code;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.reimbursement_product_snapshot;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.composed_clause_block;
DROP TABLE IF EXISTS
  nhi_rule_history_announced.composed_clause_version;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_run_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual_counts jsonb;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed announced-rule runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'release run permits only loading to sealed';
  END IF;
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.loader_version IS DISTINCT FROM OLD.loader_version
     OR NEW.evaluator_version IS DISTINCT FROM OLD.evaluator_version
     OR NEW.source_artifact_sha256 IS DISTINCT FROM OLD.source_artifact_sha256
     OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
     OR NEW.expected_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'release run identity and inputs are immutable';
  END IF;
  SELECT jsonb_build_object(
    'notice_event', (
      SELECT count(*) FROM nhi_rule_history_announced.notice_event
      WHERE run_id=OLD.run_id
    ),
    'notice_effect', (
      SELECT count(*) FROM nhi_rule_history_announced.notice_effect
      WHERE run_id=OLD.run_id
    ),
    'clause_patch', (
      SELECT count(*) FROM nhi_rule_history_announced.clause_patch
      WHERE run_id=OLD.run_id
    ),
    'patch_component', (
      SELECT count(*) FROM nhi_rule_history_announced.patch_component
      WHERE run_id=OLD.run_id
    ),
    'decision_model', (
      SELECT count(*) FROM nhi_rule_history_announced.decision_model
      WHERE run_id=OLD.run_id
    ),
    'decision_input', (
      SELECT count(*) FROM nhi_rule_history_announced.decision_input
      WHERE run_id=OLD.run_id
    ),
    'risk_category', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_category
      WHERE run_id=OLD.run_id
    ),
    'risk_branch', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_branch
      WHERE run_id=OLD.run_id
    ),
    'risk_predicate', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_predicate
      WHERE run_id=OLD.run_id
    ),
    'model_product_code', (
      SELECT count(*) FROM nhi_rule_history_announced.model_product_code
      WHERE run_id=OLD.run_id
    )
  ) INTO actual_counts;
  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'announced-rule seal counts do not match child rows';
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
