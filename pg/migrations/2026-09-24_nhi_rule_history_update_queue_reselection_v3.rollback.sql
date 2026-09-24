-- Rollback for classifier-version reselection v3.
--
-- Restores the recovery-v2 transition guard verbatim, so no further
-- ignored_non_rule -> selected transition is accepted.  Reselection
-- transitions already written stay in the append-only ledger as history; the
-- items they reopened continue along the ordinary edges.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_catalog.pg_advisory_xact_lock(
  pg_catalog.hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $marker_guard$
BEGIN
  IF pg_catalog.to_regclass(
       'nhi_rule_history_update_queue.reselection_schema_migration'
     ) IS NULL THEN
    RAISE EXCEPTION
      'reselection v3 is not applied'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$marker_guard$;

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

  IF NEW.from_state IN ('failed_terminal', 'partition_required')
     OR NEW.to_state = 'retry_pending' THEN
    RAISE EXCEPTION
      'generic transition path cannot recover failed work; use an authorized generation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

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
      'partition_required',
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
          'failed_terminal',
          'partition_required'
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
    'partition_required',
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

DROP TABLE nhi_rule_history_update_queue.reselection_schema_migration;

COMMIT;
