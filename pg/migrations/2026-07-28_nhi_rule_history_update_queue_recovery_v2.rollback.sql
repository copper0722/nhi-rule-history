-- Empty-only rollback for explicit queue recovery generations.
--
-- Recovery authorizations and generations are audit evidence.  Export/archive
-- them before rollback; this script refuses to remove a nonempty ledger.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $marker_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.recovery_schema_migration
    WHERE migration_id =
      '2026-07-28_nhi_rule_history_update_queue_recovery_v2'
      AND contract_marker =
        'managed=nhi_rule_history_update_queue/recovery-v2'
  ) THEN
    RAISE EXCEPTION
      'refusing rollback: recovery v2 managed marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$marker_guard$;

LOCK TABLE
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence,
  nhi_rule_history_update_queue.legacy_failure_evidence,
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.recovery_superseded_attempt,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.recovery_route_attempt
IN ACCESS EXCLUSIVE MODE;

DO $empty_only_guard$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.legacy_failure_attempt_evidence
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.legacy_failure_evidence
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_recovery_authorization
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.recovery_superseded_attempt
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_generation
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_generation_transition
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.recovery_route_attempt
  ) OR EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.work_item_transition
    WHERE from_state = 'partition_required'
       OR to_state = 'partition_required'
  ) THEN
    RAISE EXCEPTION
      'refusing rollback: recovery ledger is nonempty; export/archive it first'
      USING ERRCODE = 'dependent_objects_still_exist';
  END IF;
END;
$empty_only_guard$;

REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    uuid, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text[], text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, text, timestamptz
  )
  FROM nhi_rule_history_update_queue_runtime;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex, text, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    uuid[], text, text, text, text, timestamptz
  )
  FROM nhi_rule_history_update_queue_runtime;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      uuid, uuid, uuid, integer, integer, uuid, text,
      nhi_rule_history_update_ops.sha256_hex, text, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, text, text, text, timestamptz
    )
  FROM nhi_rule_history_update_queue_runtime;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.advance_recovery_generation(
    uuid, uuid, integer, text, text, uuid, uuid, uuid, timestamptz
  )
  FROM nhi_rule_history_update_queue_runtime;
REVOKE EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.register_recovery_route_attempt(
    uuid, integer, text, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex, timestamptz
  )
  FROM nhi_rule_history_update_queue_runtime;
REVOKE ALL ON
  nhi_rule_history_update_queue.v_work_dispatch_v2,
  nhi_rule_history_update_queue.v_recovery_backlog,
  nhi_rule_history_update_queue.v_recovery_generation_current,
  nhi_rule_history_update_queue.recovery_route_attempt,
  nhi_rule_history_update_queue.work_generation_transition,
  nhi_rule_history_update_queue.work_generation,
  nhi_rule_history_update_queue.recovery_superseded_attempt,
  nhi_rule_history_update_queue.work_recovery_authorization,
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence,
  nhi_rule_history_update_queue.legacy_failure_evidence
  FROM nhi_rule_history_update_queue_runtime;

DROP VIEW IF EXISTS
  nhi_rule_history_update_queue.v_work_dispatch_v2 RESTRICT;
DROP VIEW IF EXISTS
  nhi_rule_history_update_queue.v_recovery_backlog RESTRICT;
DROP VIEW IF EXISTS
  nhi_rule_history_update_queue.v_recovery_generation_current RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.admit_legacy_failure_evidence(
    uuid, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text, nhi_rule_history_update_ops.sha256_hex, jsonb,
    text[], text, nhi_rule_history_update_ops.sha256_hex,
    text, nhi_rule_history_update_ops.sha256_hex, text, timestamptz
  ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex, text, text,
    nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex,
    uuid[], text, text, text, text, timestamptz
  ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.
    authorize_failed_work_recovery_from_legacy(
      uuid, uuid, uuid, integer, integer, uuid, text,
      nhi_rule_history_update_ops.sha256_hex, text, text,
      nhi_rule_history_update_ops.sha256_hex,
      nhi_rule_history_update_ops.sha256_hex,
      text, text, text, text, timestamptz
    ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.advance_recovery_generation(
    uuid, uuid, integer, text, text, uuid, uuid, uuid, timestamptz
  ) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.register_recovery_route_attempt(
    uuid, integer, text, uuid, uuid, text,
    nhi_rule_history_update_ops.sha256_hex, timestamptz
  ) RESTRICT;

DROP TRIGGER IF EXISTS work_generation_insert_guard
  ON nhi_rule_history_update_queue.work_generation;
DROP TRIGGER IF EXISTS work_generation_transition_insert_guard
  ON nhi_rule_history_update_queue.work_generation_transition;
DROP TRIGGER IF EXISTS recovery_route_attempt_insert_guard
  ON nhi_rule_history_update_queue.recovery_route_attempt;

DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.recovery_route_attempt RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.work_generation_transition RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.work_generation RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.recovery_superseded_attempt RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.work_recovery_authorization RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.legacy_failure_attempt_evidence RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.legacy_failure_evidence RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_update_queue.recovery_schema_migration RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_recovery_route_attempt_insert()
  RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_generation_transition_insert()
  RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_update_queue.guard_work_generation_insert()
  RESTRICT;

ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  DROP CONSTRAINT IF EXISTS work_item_transition_from_state_chk;
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  ADD CONSTRAINT work_item_transition_from_state_chk CHECK (
    from_state IS NULL
    OR from_state IN (
      'observed',
      'selected',
      'acquired',
      'corpus_registered',
      'proposal_running',
      'staged_needs_review',
      'staged_pending_anchor',
      'failed_terminal',
      'ignored_non_rule'
    )
  );
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  DROP CONSTRAINT IF EXISTS work_item_transition_to_state_chk;
ALTER TABLE nhi_rule_history_update_queue.work_item_transition
  ADD CONSTRAINT work_item_transition_to_state_chk CHECK (
    to_state IN (
      'observed',
      'selected',
      'acquired',
      'corpus_registered',
      'proposal_running',
      'staged_needs_review',
      'staged_pending_anchor',
      'failed_terminal',
      'ignored_non_rule'
    )
  );

CREATE OR REPLACE VIEW
  nhi_rule_history_update_queue.v_work_item_current AS
SELECT
  item.work_item_id,
  item.rss_identity_fingerprint,
  item.item_identity_kind,
  item.item_identity_value,
  item.source_feed_url,
  item.guid_raw,
  item.first_title_raw,
  item.first_link_raw,
  item.first_observed_at,
  current_transition.transition_seq,
  current_transition.to_state AS current_state,
  current_transition.evidence_sha256,
  current_transition.evidence_json,
  current_transition.bundle_receipt_id,
  current_transition.candidate_proposal_id,
  current_transition.recorded_at AS state_recorded_at,
  current_transition.to_state IN (
    'staged_needs_review',
    'staged_pending_anchor',
    'failed_terminal',
    'ignored_non_rule'
  ) AS is_terminal
FROM nhi_rule_history_update_queue.rss_work_item item
JOIN LATERAL (
  SELECT transition.*
  FROM nhi_rule_history_update_queue.work_item_transition transition
  WHERE transition.work_item_id = item.work_item_id
  ORDER BY transition.transition_seq DESC
  LIMIT 1
) current_transition ON true;

-- Restore the exact v1 generic transition guard.  The v1 failed_terminal
-- terminal rule remains in force before, during, and after this rollback.
CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_queue.guard_transition_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  prior_seq integer;
  prior_state text;
  prior_recorded_at timestamptz;
  candidate_bundle_id uuid;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-history-work-item:' || NEW.work_item_id::text,
      0
    )
  );

  SELECT transition_seq, to_state, recorded_at
    INTO prior_seq, prior_state, prior_recorded_at
  FROM nhi_rule_history_update_queue.work_item_transition
  WHERE work_item_id = NEW.work_item_id
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF NOT FOUND THEN
    IF NEW.transition_seq <> 1
       OR NEW.from_state IS NOT NULL
       OR NEW.to_state <> 'observed' THEN
      RAISE EXCEPTION
        'first work-item transition must be seq 1: NULL -> observed'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    IF prior_state IN (
      'staged_needs_review',
      'staged_pending_anchor',
      'failed_terminal',
      'ignored_non_rule'
    ) THEN
      RAISE EXCEPTION
        'terminal work-item states prevent silent retry'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF NEW.transition_seq <> prior_seq + 1
       OR NEW.from_state IS DISTINCT FROM prior_state
       OR NEW.recorded_at < prior_recorded_at THEN
      RAISE EXCEPTION
        'work-item transition sequence/state/time is not gap-free'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF NOT (
      (prior_state = 'observed'
        AND NEW.to_state IN ('selected', 'ignored_non_rule', 'failed_terminal'))
      OR (prior_state = 'selected'
        AND NEW.to_state IN (
          'acquired', 'failed_terminal', 'ignored_non_rule'
        ))
      OR (prior_state = 'acquired'
        AND NEW.to_state IN ('corpus_registered', 'failed_terminal'))
      OR (prior_state = 'corpus_registered'
        AND NEW.to_state IN ('proposal_running', 'failed_terminal'))
      OR (prior_state = 'proposal_running'
        AND NEW.to_state IN (
          'staged_needs_review',
          'staged_pending_anchor',
          'failed_terminal'
        ))
    ) THEN
      RAISE EXCEPTION
        'work-item transition edge is not allowed'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  IF NEW.to_state IN (
    'observed',
    'selected',
    'acquired',
    'corpus_registered',
    'proposal_running',
    'ignored_non_rule'
  ) AND (
    NEW.bundle_receipt_id IS NOT NULL
    OR NEW.candidate_proposal_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION
      'pre-staging queue states cannot claim update bundle or candidate identifiers'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF NEW.to_state IN (
    'staged_needs_review', 'staged_pending_anchor'
  ) THEN
    IF NEW.bundle_receipt_id IS NULL
       OR NEW.candidate_proposal_id IS NULL THEN
      RAISE EXCEPTION
        'staged states require matching bundle and candidate identifiers'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT bundle_receipt_id
      INTO candidate_bundle_id
    FROM nhi_rule_history_candidate_stage.candidate_proposal
    WHERE proposal_id = NEW.candidate_proposal_id;
    IF NOT FOUND
       OR candidate_bundle_id IS DISTINCT FROM NEW.bundle_receipt_id THEN
      RAISE EXCEPTION
        'staged candidate does not belong to the supplied bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;

  IF NEW.to_state = 'failed_terminal'
     AND NEW.candidate_proposal_id IS NOT NULL THEN
    IF NEW.bundle_receipt_id IS NULL THEN
      RAISE EXCEPTION
        'terminal failure candidate identifier requires its bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT bundle_receipt_id
      INTO candidate_bundle_id
    FROM nhi_rule_history_candidate_stage.candidate_proposal
    WHERE proposal_id = NEW.candidate_proposal_id;
    IF NOT FOUND
       OR candidate_bundle_id IS DISTINCT FROM NEW.bundle_receipt_id THEN
      RAISE EXCEPTION
        'terminal failure candidate does not belong to the supplied bundle receipt'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
