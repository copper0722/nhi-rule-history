-- 2026-07-27 — isolated annotation-to-official-event candidate resolution
--
-- This evidence stage preserves caller observations, same-day candidates, and
-- fail-closed gaps.  It cannot write canonical rule history.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_event_resolution_stage-global', 0)
);

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Append-only annotation-to-official-event candidate evidence; not legal history. managed=nhi_rule_history_event_resolution_stage/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_event_resolution_stage'
  ) THEN
    CREATE SCHEMA nhi_rule_history_event_resolution_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_event_resolution_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'nhi_rule_history_event_resolution_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'event resolution stage exists without the managed v1 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $domain_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'nhi_rule_history_event_resolution_stage'
      AND t.typname = 'sha256_hex'
      AND t.typtype = 'd'
  ) THEN
    CREATE DOMAIN
      nhi_rule_history_event_resolution_stage.sha256_hex AS text
      CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END;
$domain_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_event_resolution_stage.resolution_run (
    run_id uuid PRIMARY KEY,
    contract_version text NOT NULL,
    resolver_version text NOT NULL,
    migration_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    code_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    input_fingerprint
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL UNIQUE,
    output_fingerprint
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    sealed_fingerprint
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL UNIQUE,
    annotation_count integer NOT NULL CHECK (annotation_count >= 0),
    official_observation_count integer NOT NULL
      CHECK (official_observation_count >= 0),
    candidate_count integer NOT NULL CHECK (candidate_count >= 0),
    resolved_candidate_count integer NOT NULL
      CHECK (resolved_candidate_count >= 0),
    ambiguous_count integer NOT NULL CHECK (ambiguous_count >= 0),
    no_match_count integer NOT NULL CHECK (no_match_count >= 0),
    invalid_count integer NOT NULL CHECK (invalid_count >= 0),
    canonical_history_written boolean NOT NULL DEFAULT false
      CHECK (canonical_history_written = false),
    expected_counts jsonb NOT NULL
      CHECK (jsonb_typeof(expected_counts) = 'object'),
    table_fingerprints jsonb NOT NULL
      CHECK (jsonb_typeof(table_fingerprints) = 'object'),
    state text NOT NULL CHECK (state = 'sealed'),
    created_at timestamptz NOT NULL DEFAULT current_timestamp,
    sealed_at timestamptz NOT NULL DEFAULT current_timestamp,
    CHECK (
      resolved_candidate_count + ambiguous_count
      + no_match_count + invalid_count = annotation_count
    )
  );

COMMENT ON TABLE
  nhi_rule_history_event_resolution_stage.resolution_run IS
  'Immutable evidence-stage run. canonical_history_written is always false.';

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_event_resolution_stage.annotation_observation (
    run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_event_resolution_stage.resolution_run (run_id),
    annotation_id text NOT NULL,
    article_id text NOT NULL,
    normalized_iso_candidate date,
    iso_date_valid boolean NOT NULL,
    normalization_status text NOT NULL CHECK (
      normalization_status IN ('normalized', 'invalid_calendar_date')
    ),
    source_designation_raw text,
    source_designation_normalized text,
    designation_omitted boolean NOT NULL,
    multiple_clause_ambiguity boolean NOT NULL,
    source_locator jsonb,
    source_locator_present boolean NOT NULL,
    caller_observation jsonb NOT NULL
      CHECK (jsonb_typeof(caller_observation) = 'object'),
    caller_record_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, annotation_id),
    UNIQUE (run_id, source_row_sha256),
    CHECK (
      (iso_date_valid AND normalized_iso_candidate IS NOT NULL
       AND normalization_status = 'normalized')
      OR
      (NOT iso_date_valid AND normalized_iso_candidate IS NULL)
    ),
    CHECK (
      NOT source_locator_present OR source_locator IS NOT NULL
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_event_resolution_stage
  .official_event_effect_observation (
    run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_event_resolution_stage.resolution_run (run_id),
    official_event_id text NOT NULL,
    official_effect_id text NOT NULL,
    effective_date date,
    effective_date_valid boolean NOT NULL,
    raw_effective_date text,
    source_designation_raw text,
    source_designation_normalized text,
    designation_omitted boolean NOT NULL,
    multiple_clause_ambiguity boolean NOT NULL,
    omitted_text_present boolean NOT NULL,
    source_locator jsonb,
    source_locator_present boolean NOT NULL,
    caller_observation jsonb NOT NULL
      CHECK (jsonb_typeof(caller_observation) = 'object'),
    caller_record_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, official_event_id, official_effect_id),
    UNIQUE (run_id, source_row_sha256),
    CHECK (
      (effective_date_valid AND effective_date IS NOT NULL)
      OR
      (NOT effective_date_valid AND effective_date IS NULL)
    ),
    CHECK (
      NOT source_locator_present OR source_locator IS NOT NULL
    )
  );

DO $official_omitted_text_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'nhi_rule_history_event_resolution_stage'
      AND table_name = 'official_event_effect_observation'
      AND column_name = 'omitted_text_present'
  ) THEN
    IF EXISTS (
      SELECT 1
      FROM nhi_rule_history_event_resolution_stage
        .official_event_effect_observation
    ) THEN
      RAISE EXCEPTION
        'cannot add omitted-text evidence after resolution rows exist'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    ALTER TABLE nhi_rule_history_event_resolution_stage
      .official_event_effect_observation
      ADD COLUMN omitted_text_present boolean NOT NULL DEFAULT false;
    ALTER TABLE nhi_rule_history_event_resolution_stage
      .official_event_effect_observation
      ALTER COLUMN omitted_text_present DROP DEFAULT;
  END IF;
END;
$official_omitted_text_guard$;

CREATE INDEX IF NOT EXISTS official_event_effect_date_idx
  ON nhi_rule_history_event_resolution_stage
    .official_event_effect_observation (run_id, effective_date);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_event_resolution_stage.candidate_observation (
    run_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    annotation_id text NOT NULL,
    official_event_id text NOT NULL,
    official_effect_id text NOT NULL,
    exact_effective_date date NOT NULL,
    designation_compatibility text NOT NULL CHECK (
      designation_compatibility IN (
        'compatible', 'incompatible', 'indeterminate'
      )
    ),
    blocker_codes jsonb NOT NULL
      CHECK (jsonb_typeof(blocker_codes) = 'array'),
    eligible boolean NOT NULL,
    canonical_history_written boolean NOT NULL DEFAULT false
      CHECK (canonical_history_written = false),
    source_row_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, candidate_id),
    FOREIGN KEY (run_id, annotation_id)
      REFERENCES
        nhi_rule_history_event_resolution_stage.annotation_observation
        (run_id, annotation_id),
    FOREIGN KEY (run_id, official_event_id, official_effect_id)
      REFERENCES
        nhi_rule_history_event_resolution_stage
        .official_event_effect_observation
        (run_id, official_event_id, official_effect_id),
    UNIQUE (
      run_id, annotation_id, official_event_id, official_effect_id
    ),
    UNIQUE (run_id, source_row_sha256),
    CHECK (
      eligible = (
        designation_compatibility = 'compatible'
        AND jsonb_array_length(blocker_codes) = 0
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_event_resolution_stage.resolution_outcome (
    run_id uuid NOT NULL,
    outcome_id uuid NOT NULL,
    annotation_id text NOT NULL,
    resolution_status text NOT NULL CHECK (
      resolution_status IN (
        'resolved_candidate', 'ambiguous', 'no_match', 'invalid'
      )
    ),
    reason_codes jsonb NOT NULL CHECK (
      jsonb_typeof(reason_codes) = 'array'
      AND jsonb_array_length(reason_codes) > 0
    ),
    candidate_count integer NOT NULL CHECK (candidate_count >= 0),
    compatible_candidate_count integer NOT NULL CHECK (
      compatible_candidate_count >= 0
      AND compatible_candidate_count <= candidate_count
    ),
    eligible_candidate_count integer NOT NULL CHECK (
      eligible_candidate_count >= 0
      AND eligible_candidate_count <= compatible_candidate_count
    ),
    distinct_event_count integer NOT NULL CHECK (
      distinct_event_count >= 0
      AND distinct_event_count <= candidate_count
    ),
    selected_candidate_id uuid,
    canonical_history_written boolean NOT NULL DEFAULT false
      CHECK (canonical_history_written = false),
    source_row_sha256
      nhi_rule_history_event_resolution_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, outcome_id),
    FOREIGN KEY (run_id, annotation_id)
      REFERENCES
        nhi_rule_history_event_resolution_stage.annotation_observation
        (run_id, annotation_id),
    FOREIGN KEY (run_id, selected_candidate_id)
      REFERENCES
        nhi_rule_history_event_resolution_stage.candidate_observation
        (run_id, candidate_id),
    UNIQUE (run_id, annotation_id),
    UNIQUE (run_id, source_row_sha256),
    CHECK (
      (
        resolution_status = 'resolved_candidate'
        AND eligible_candidate_count = 1
        AND selected_candidate_id IS NOT NULL
      )
      OR
      (
        resolution_status <> 'resolved_candidate'
        AND selected_candidate_id IS NULL
      )
    )
  );

CREATE OR REPLACE VIEW
  nhi_rule_history_event_resolution_stage.v_resolution_status_counts AS
SELECT
  run_row.run_id,
  count(outcome.outcome_id)::integer AS annotation_count,
  count(*) FILTER (
    WHERE outcome.resolution_status = 'resolved_candidate'
  )::integer AS resolved_candidate_count,
  count(*) FILTER (
    WHERE outcome.resolution_status = 'ambiguous'
  )::integer AS ambiguous_count,
  count(*) FILTER (
    WHERE outcome.resolution_status = 'no_match'
  )::integer AS no_match_count,
  count(*) FILTER (
    WHERE outcome.resolution_status = 'invalid'
  )::integer AS invalid_count,
  false AS canonical_history_written
FROM nhi_rule_history_event_resolution_stage.resolution_run run_row
LEFT JOIN nhi_rule_history_event_resolution_stage.resolution_outcome outcome
  ON outcome.run_id = run_row.run_id
GROUP BY run_row.run_id;

COMMENT ON VIEW
  nhi_rule_history_event_resolution_stage.v_resolution_status_counts IS
  'Evidence-stage counts only; resolved_candidate is not canonical history.';

CREATE OR REPLACE FUNCTION
  nhi_rule_history_event_resolution_stage.reject_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION
    'nhi_rule_history_event_resolution_stage evidence is append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$function$;

DO $trigger_guard$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'resolution_run',
    'annotation_observation',
    'official_event_effect_observation',
    'candidate_observation',
    'resolution_outcome'
  ]
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row
        ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_event_resolution_stage'
        AND relation_row.relname = table_name
        AND trigger_row.tgname = 'reject_evidence_update_delete'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER reject_evidence_update_delete
         BEFORE UPDATE OR DELETE ON
           nhi_rule_history_event_resolution_stage.%I
         FOR EACH ROW EXECUTE FUNCTION
           nhi_rule_history_event_resolution_stage
           .reject_evidence_mutation()',
        table_name
      );
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row
        ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_event_resolution_stage'
        AND relation_row.relname = table_name
        AND trigger_row.tgname = 'reject_evidence_truncate'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER reject_evidence_truncate
         BEFORE TRUNCATE ON
           nhi_rule_history_event_resolution_stage.%I
         FOR EACH STATEMENT EXECUTE FUNCTION
           nhi_rule_history_event_resolution_stage
           .reject_evidence_mutation()',
        table_name
      );
    END IF;
  END LOOP;
END;
$trigger_guard$;

REVOKE ALL ON SCHEMA
  nhi_rule_history_event_resolution_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES
  IN SCHEMA nhi_rule_history_event_resolution_stage FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS
  IN SCHEMA nhi_rule_history_event_resolution_stage FROM PUBLIC;

ALTER DEFAULT PRIVILEGES
  IN SCHEMA nhi_rule_history_event_resolution_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES
  IN SCHEMA nhi_rule_history_event_resolution_stage
  REVOKE ALL ON FUNCTIONS FROM PUBLIC;

COMMIT;
