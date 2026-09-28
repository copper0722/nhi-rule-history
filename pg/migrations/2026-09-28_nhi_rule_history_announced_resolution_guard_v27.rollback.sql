-- Rollback of v27: restore the v22 resolution insert guard and writer.
-- No row is written or removed; resolutions written under v27 stay.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-resolution-guard-v27', 0)
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_patch_resolution_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_patch patch
    JOIN nhi_rule_history_announced.release_run run USING (run_id)
    WHERE patch.run_id = NEW.run_id
      AND patch.patch_id = NEW.patch_id
      AND run.state = 'sealed'
  ) THEN
    RAISE EXCEPTION 'patch resolution requires a sealed patch';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.set_patch_resolution(
    p_run_id uuid,
    p_patch_id uuid,
    p_resolution_state text,
    p_reason text,
    p_evidence jsonb DEFAULT '{}'::jsonb
  )
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE inserted_id bigint;
BEGIN
  INSERT INTO nhi_rule_history_announced.patch_resolution_event (
    run_id, patch_id, resolution_state, reason, evidence
  ) VALUES (
    p_run_id, p_patch_id, p_resolution_state, p_reason,
    coalesce(p_evidence, '{}'::jsonb)
  )
  RETURNING resolution_id INTO inserted_id;
  RETURN inserted_id;
END;
$$;

COMMIT;
