-- 2026-09-28 — resolution writes follow the served run.
--
-- A resolution written to a run that was served before but is not served now
-- is lost to readers, and a writer that read the served run just before an
-- overlay activation would land its event there.  Every resolution insert
-- (through set_patch_resolution or directly) now takes the global announced
-- lock, which overlay loads, activations and rollbacks hold, and is refused
-- for a run that has been activated before and is not the active run.  A run
-- that has never been activated (a freshly loaded run receiving its first
-- resolutions) is still accepted.  Rollback activates the restored run before
-- it carries resolutions back to it.
--
-- Rollback: 2026-09-28_nhi_rule_history_announced_resolution_guard_v27.rollback.sql
-- restores the v22 definitions; no row is written or removed by either file.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-resolution-guard-v27', 0)
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_patch_resolution_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE active_run uuid;
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
  -- Serialize with overlay loads, activations and rollbacks, then read the
  -- served run as it is after any of them committed.
  PERFORM pg_advisory_xact_lock(
    hashtextextended('nhi-rule-history-announced-global', 0)
  );
  SELECT run_id INTO active_run
  FROM nhi_rule_history_announced.v_active_run;
  IF active_run IS DISTINCT FROM NEW.run_id AND EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.release_control_event control
    WHERE control.run_id = NEW.run_id
      AND control.action = 'activate'
  ) THEN
    RAISE EXCEPTION
      'patch resolution refused: run % was served before and is not the served run; write to the served run',
      NEW.run_id;
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
  PERFORM pg_advisory_xact_lock(
    hashtextextended('nhi-rule-history-announced-global', 0)
  );
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
