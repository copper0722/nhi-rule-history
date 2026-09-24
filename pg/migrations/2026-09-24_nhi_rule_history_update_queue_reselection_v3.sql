-- 2026-09-24 — classifier-version reselection of ignored RSS work items
--
-- A work item closed as ignored_non_rule by one RSS drug-rule classifier
-- version may be selected again when a newer version selects its title (the
-- 2026-09-15 section 4.2 notice was ignored by classifier 2.0.0).  This adds
-- that single exit to the generic transition guard; every other terminal
-- state still refuses a silent retry.  Nothing here writes legal history.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_catalog.pg_advisory_xact_lock(
  pg_catalog.hashtextextended('nhi_rule_history_update_queue-global', 0)
);

DO $dependency_guard$
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
      'recovery-v2 is required before classifier reselection v3'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_update_queue.reselection_schema_migration (
    migration_id text PRIMARY KEY,
    contract_marker text NOT NULL UNIQUE,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reselection_schema_migration_id_chk CHECK (
      migration_id =
        '2026-09-24_nhi_rule_history_update_queue_reselection_v3'
    ),
    CONSTRAINT reselection_schema_migration_marker_chk CHECK (
      contract_marker =
        'managed=nhi_rule_history_update_queue/reselection-v3'
    )
  );

DROP TRIGGER IF EXISTS reselection_schema_migration_append_only_guard
  ON nhi_rule_history_update_queue.reselection_schema_migration;
CREATE TRIGGER reselection_schema_migration_append_only_guard
  BEFORE UPDATE OR DELETE
  ON nhi_rule_history_update_queue.reselection_schema_migration
  FOR EACH ROW
  EXECUTE FUNCTION nhi_rule_history_update_queue.reject_append_only_change();
DROP TRIGGER IF EXISTS reselection_schema_migration_truncate_guard
  ON nhi_rule_history_update_queue.reselection_schema_migration;
CREATE TRIGGER reselection_schema_migration_truncate_guard
  BEFORE TRUNCATE
  ON nhi_rule_history_update_queue.reselection_schema_migration
  FOR EACH STATEMENT
  EXECUTE FUNCTION nhi_rule_history_update_queue.reject_truncate();

REVOKE ALL ON
  nhi_rule_history_update_queue.reselection_schema_migration
  FROM PUBLIC;

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
  reselection boolean := false;
  item_title_sha256 text;
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
    -- The one exit from a terminal state: a newer RSS classifier version
    -- selects an item that an older version ignored.  It must name both
    -- versions, bind the item's own title, and happen once per version.
    IF prior_state = 'ignored_non_rule'
       AND NEW.to_state = 'selected'
       AND NEW.actor_kind = 'deterministic_classifier_reselection' THEN
      SELECT pg_catalog.encode(
               pg_catalog.sha256(
                 pg_catalog.convert_to(item.first_title_raw, 'UTF8')
               ),
               'hex'
             )
        INTO item_title_sha256
      FROM nhi_rule_history_update_queue.rss_work_item AS item
      WHERE item.work_item_id = NEW.work_item_id;
      IF NEW.evidence_json ->> 'decision'
           IS DISTINCT FROM 'classifier_version_reselection'
         OR (NEW.evidence_json ->> 'classifier_version') IS NULL
         OR (NEW.evidence_json ->> 'classifier_version')
           !~ '^nhi-rule-history-drug-rule-classifier/[0-9]+[.][0-9]+[.][0-9]+$'
         OR (NEW.evidence_json ->> 'prior_classifier_version') IS NULL
         OR (NEW.evidence_json ->> 'prior_classifier_version')
           !~ '^nhi-rule-history-drug-rule-classifier/[0-9]+[.][0-9]+[.][0-9]+$'
         OR NEW.evidence_json ->> 'classifier_version'
           = NEW.evidence_json ->> 'prior_classifier_version'
         OR item_title_sha256 IS NULL
         OR NEW.evidence_json ->> 'title_sha256'
           IS DISTINCT FROM item_title_sha256 THEN
        RAISE EXCEPTION
          'classifier reselection needs its decision, two distinct classifier versions, and the item title hash'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      IF EXISTS (
        SELECT 1
        FROM nhi_rule_history_update_queue.work_item_transition AS earlier
        WHERE earlier.work_item_id = NEW.work_item_id
          AND earlier.actor_kind = 'deterministic_classifier_reselection'
          AND earlier.evidence_json ->> 'classifier_version'
            = NEW.evidence_json ->> 'classifier_version'
      ) THEN
        RAISE EXCEPTION
          'this classifier version already reselected the work item'
          USING ERRCODE = 'object_not_in_prerequisite_state';
      END IF;
      reselection := true;
    ELSIF prior_state IN (
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
    IF NOT reselection AND NOT (
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

INSERT INTO
  nhi_rule_history_update_queue.reselection_schema_migration (
    migration_id, contract_marker
  ) VALUES (
    '2026-09-24_nhi_rule_history_update_queue_reselection_v3',
    'managed=nhi_rule_history_update_queue/reselection-v3'
  )
ON CONFLICT (migration_id) DO NOTHING;

DO $marker_verify$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_queue.reselection_schema_migration
    WHERE migration_id =
      '2026-09-24_nhi_rule_history_update_queue_reselection_v3'
      AND contract_marker =
        'managed=nhi_rule_history_update_queue/reselection-v3'
  ) THEN
    RAISE EXCEPTION
      'reselection v3 migration marker is absent or inconsistent'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$marker_verify$;

COMMENT ON TABLE
  nhi_rule_history_update_queue.reselection_schema_migration IS
  'Marker for the classifier-version reselection edge ignored_non_rule -> selected. managed=nhi_rule_history_update_queue/reselection-v3';

COMMIT;
