-- 2026-07-29 — immutable source-level transcript and segmentation evidence
--
-- This schema deliberately stops before legal identity/version adjudication.
-- A proofread source segment is an official-source observation, not a
-- canonical legal version or a verified direct predecessor.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-source-transcript-v19', 0)
);

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_transcript;

COMMENT ON SCHEMA nhi_rule_history_transcript IS
  'Immutable agent-proofread source transcripts, source-local segments, and '
  'unadjudicated cross-edition lineage candidates.';

CREATE TABLE IF NOT EXISTS nhi_rule_history_transcript.transcript_run (
  run_id uuid PRIMARY KEY,
  source_fint_run_id uuid NOT NULL,
  source_attachment_snapshot_id text NOT NULL,
  source_attachment_sha256 text NOT NULL CHECK (
    source_attachment_sha256 ~ '^[0-9a-f]{64}$'
  ),
  source_attachment_byte_size bigint NOT NULL CHECK (
    source_attachment_byte_size > 0
  ),
  source_document_number text NOT NULL CHECK (
    btrim(source_document_number) <> ''
  ),
  source_edition_label text NOT NULL CHECK (
    btrim(source_edition_label) <> ''
  ),
  source_url text NOT NULL CHECK (
    source_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/'
  ),
  target_source_edition_label text NOT NULL CHECK (
    btrim(target_source_edition_label) <> ''
  ),
  target_artifact_sha256 text NOT NULL CHECK (
    target_artifact_sha256 ~ '^[0-9a-f]{64}$'
  ),
  producer_provider text NOT NULL CHECK (
    producer_provider = 'openai'
  ),
  producer_model_lane text NOT NULL CHECK (
    producer_model_lane = 'gpt-pro'
  ),
  producer_role text NOT NULL CHECK (btrim(producer_role) <> ''),
  prompt_sha256 text NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
  proofread_sha256 text NOT NULL CHECK (
    proofread_sha256 ~ '^[0-9a-f]{64}$'
  ),
  segment_jsonl_sha256 text NOT NULL CHECK (
    segment_jsonl_sha256 ~ '^[0-9a-f]{64}$'
  ),
  lineage_analysis_sha256 text NOT NULL CHECK (
    lineage_analysis_sha256 ~ '^[0-9a-f]{64}$'
  ),
  review_status text NOT NULL CHECK (
    review_status IN (
      'agent_proofread_pending_independent_review',
      'independently_reviewed'
    )
  ),
  source_observation_only boolean NOT NULL CHECK (
    source_observation_only
  ),
  legal_identity_adjudicated boolean NOT NULL CHECK (
    NOT legal_identity_adjudicated
  ),
  direct_predecessor_claimed boolean NOT NULL CHECK (
    NOT direct_predecessor_claimed
  ),
  legal_effective_date_assigned_per_segment boolean NOT NULL CHECK (
    NOT legal_effective_date_assigned_per_segment
  ),
  complete_history_claimed boolean NOT NULL CHECK (
    NOT complete_history_claimed
  ),
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  loader_version text NOT NULL CHECK (btrim(loader_version) <> ''),
  input_fingerprint text NOT NULL UNIQUE CHECK (
    input_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  expected_counts jsonb NOT NULL CHECK (
    jsonb_typeof(expected_counts) = 'object'
  ),
  verified_counts jsonb,
  table_fingerprints jsonb,
  output_fingerprint text CHECK (
    output_fingerprint IS NULL
    OR output_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  migration_sha256 text NOT NULL CHECK (
    migration_sha256 ~ '^[0-9a-f]{64}$'
  ),
  code_sha256 text NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
  sealed_fingerprint text CHECK (
    sealed_fingerprint IS NULL
    OR sealed_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  FOREIGN KEY (source_fint_run_id, source_attachment_snapshot_id)
    REFERENCES nhi_rule_history_edition.fint_attachment_snapshot (
      run_id, attachment_snapshot_id
    )
    ON DELETE RESTRICT,
  CHECK (
    (
      state = 'loading'
      AND verified_counts IS NULL
      AND table_fingerprints IS NULL
      AND output_fingerprint IS NULL
      AND sealed_fingerprint IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND verified_counts IS NOT NULL
      AND table_fingerprints IS NOT NULL
      AND output_fingerprint IS NOT NULL
      AND sealed_fingerprint IS NOT NULL
      AND sealed_at IS NOT NULL
      AND verified_counts = expected_counts
    )
  )
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_transcript.proofread_artifact (
    run_id uuid PRIMARY KEY
      REFERENCES nhi_rule_history_transcript.transcript_run (run_id)
      ON DELETE RESTRICT,
    transcript_markdown text NOT NULL CHECK (
      transcript_markdown <> ''
    ),
    transcript_sha256 text NOT NULL CHECK (
      transcript_sha256 ~ '^[0-9a-f]{64}$'
      AND transcript_sha256 = encode(
        sha256(convert_to(transcript_markdown, 'UTF8')), 'hex'
      )
    ),
    page_count integer NOT NULL CHECK (page_count > 0),
    unresolved_visual_reading_count integer NOT NULL CHECK (
      unresolved_visual_reading_count >= 0
    ),
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    source_row_sha256 text NOT NULL CHECK (
      source_row_sha256 ~ '^[0-9a-f]{64}$'
    )
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_transcript.source_page (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_transcript.transcript_run (run_id)
    ON DELETE RESTRICT,
  page_number integer NOT NULL CHECK (page_number > 0),
  transcript_text text NOT NULL CHECK (transcript_text <> ''),
  transcript_text_sha256 text NOT NULL CHECK (
    transcript_text_sha256 ~ '^[0-9a-f]{64}$'
    AND transcript_text_sha256 = encode(
      sha256(convert_to(transcript_text, 'UTF8')), 'hex'
    )
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  source_row_sha256 text NOT NULL CHECK (
    source_row_sha256 ~ '^[0-9a-f]{64}$'
  ),
  PRIMARY KEY (run_id, page_number)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_transcript.source_segment (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_transcript.transcript_run (run_id)
    ON DELETE RESTRICT,
  source_segment_id text NOT NULL CHECK (
    btrim(source_segment_id) <> ''
  ),
  source_page_start integer NOT NULL CHECK (source_page_start > 0),
  source_page_end integer NOT NULL CHECK (
    source_page_end >= source_page_start
  ),
  section_path jsonb NOT NULL CHECK (
    jsonb_typeof(section_path) = 'array'
    AND jsonb_array_length(section_path) > 0
  ),
  designation_raw text NOT NULL CHECK (btrim(designation_raw) <> ''),
  heading_raw text NOT NULL CHECK (btrim(heading_raw) <> ''),
  exact_text text NOT NULL CHECK (btrim(exact_text) <> ''),
  exact_text_sha256 text NOT NULL CHECK (
    exact_text_sha256 ~ '^[0-9a-f]{64}$'
    AND exact_text_sha256 = encode(
      sha256(convert_to(exact_text, 'UTF8')), 'hex'
    )
  ),
  substructure jsonb NOT NULL CHECK (
    jsonb_typeof(substructure) = 'array'
  ),
  literal_deleted_marker boolean NOT NULL,
  uncertainties jsonb NOT NULL CHECK (
    jsonb_typeof(uncertainties) = 'array'
  ),
  proofread_review_status text NOT NULL CHECK (
    proofread_review_status IN (
      'agent_proofread_pending_independent_review',
      'independently_reviewed'
    )
  ),
  identity_status text NOT NULL CHECK (
    identity_status = 'unadjudicated_source_segment'
  ),
  legal_version_status text NOT NULL CHECK (
    legal_version_status = 'not_claimed'
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  source_row_sha256 text NOT NULL CHECK (
    source_row_sha256 ~ '^[0-9a-f]{64}$'
  ),
  PRIMARY KEY (run_id, source_segment_id),
  FOREIGN KEY (run_id, source_page_start)
    REFERENCES nhi_rule_history_transcript.source_page (
      run_id, page_number
    )
    ON DELETE RESTRICT,
  FOREIGN KEY (run_id, source_page_end)
    REFERENCES nhi_rule_history_transcript.source_page (
      run_id, page_number
    )
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_transcript.lineage_analysis_artifact (
    run_id uuid PRIMARY KEY
      REFERENCES nhi_rule_history_transcript.transcript_run (run_id)
      ON DELETE RESTRICT,
    analysis_markdown text NOT NULL CHECK (analysis_markdown <> ''),
    analysis_sha256 text NOT NULL CHECK (
      analysis_sha256 ~ '^[0-9a-f]{64}$'
      AND analysis_sha256 = encode(
        sha256(convert_to(analysis_markdown, 'UTF8')), 'hex'
      )
    ),
    target_source_edition_label text NOT NULL CHECK (
      btrim(target_source_edition_label) <> ''
    ),
    target_artifact_sha256 text NOT NULL CHECK (
      target_artifact_sha256 ~ '^[0-9a-f]{64}$'
    ),
    recoding_hypothesis_status text NOT NULL CHECK (
      recoding_hypothesis_status =
        'supported_at_source_observation_level_not_adjudicated'
    ),
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    source_row_sha256 text NOT NULL CHECK (
      source_row_sha256 ~ '^[0-9a-f]{64}$'
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_transcript.lineage_candidate (
    run_id uuid NOT NULL,
    candidate_id text NOT NULL CHECK (candidate_id ~ '^[0-9a-f]{64}$'),
    source_segment_id text NOT NULL,
    disposition text NOT NULL CHECK (
      disposition IN (
        'same_designation_text_continuity_candidate',
        'renumber_or_move_candidate',
        'absent_in_96_observation',
        'new_in_96_observation',
        'ambiguous'
      )
    ),
    target_source_edition_label text NOT NULL CHECK (
      btrim(target_source_edition_label) <> ''
    ),
    target_artifact_sha256 text NOT NULL CHECK (
      target_artifact_sha256 ~ '^[0-9a-f]{64}$'
    ),
    target_evidence_text text NOT NULL CHECK (
      btrim(target_evidence_text) <> ''
    ),
    rationale_text text NOT NULL CHECK (btrim(rationale_text) <> ''),
    identity_status text NOT NULL CHECK (
      identity_status = 'candidate_unadjudicated'
    ),
    direct_predecessor_status text NOT NULL CHECK (
      direct_predecessor_status = 'not_claimed'
    ),
    legal_transition_status text NOT NULL CHECK (
      legal_transition_status = 'not_claimed'
    ),
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    source_row_sha256 text NOT NULL CHECK (
      source_row_sha256 ~ '^[0-9a-f]{64}$'
    ),
    PRIMARY KEY (run_id, candidate_id),
    UNIQUE (run_id, source_segment_id),
    FOREIGN KEY (run_id, source_segment_id)
      REFERENCES nhi_rule_history_transcript.source_segment (
        run_id, source_segment_id
      )
      ON DELETE RESTRICT
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_transcript.guard_run_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.state <> 'loading' THEN
    RAISE EXCEPTION 'transcript runs must begin in loading state';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS transcript_run_insert_guard
  ON nhi_rule_history_transcript.transcript_run;
CREATE TRIGGER transcript_run_insert_guard
BEFORE INSERT ON nhi_rule_history_transcript.transcript_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_transcript.guard_run_insert();

CREATE OR REPLACE FUNCTION
  nhi_rule_history_transcript.guard_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'transcript runs cannot be deleted';
  END IF;
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed transcript runs are immutable';
  END IF;
  IF NEW.state <> 'sealed'
     OR OLD.run_id <> NEW.run_id
     OR OLD.source_fint_run_id <> NEW.source_fint_run_id
     OR OLD.source_attachment_snapshot_id <>
        NEW.source_attachment_snapshot_id
     OR OLD.source_attachment_sha256 <> NEW.source_attachment_sha256
     OR OLD.source_attachment_byte_size <>
        NEW.source_attachment_byte_size
     OR OLD.source_document_number <> NEW.source_document_number
     OR OLD.source_edition_label <> NEW.source_edition_label
     OR OLD.source_url <> NEW.source_url
     OR OLD.target_source_edition_label <>
        NEW.target_source_edition_label
     OR OLD.target_artifact_sha256 <> NEW.target_artifact_sha256
     OR OLD.producer_provider <> NEW.producer_provider
     OR OLD.producer_model_lane <> NEW.producer_model_lane
     OR OLD.producer_role <> NEW.producer_role
     OR OLD.prompt_sha256 <> NEW.prompt_sha256
     OR OLD.proofread_sha256 <> NEW.proofread_sha256
     OR OLD.segment_jsonl_sha256 <> NEW.segment_jsonl_sha256
     OR OLD.lineage_analysis_sha256 <> NEW.lineage_analysis_sha256
     OR OLD.review_status <> NEW.review_status
     OR OLD.source_observation_only <>
        NEW.source_observation_only
     OR OLD.legal_identity_adjudicated <>
        NEW.legal_identity_adjudicated
     OR OLD.direct_predecessor_claimed <>
        NEW.direct_predecessor_claimed
     OR OLD.legal_effective_date_assigned_per_segment <>
        NEW.legal_effective_date_assigned_per_segment
     OR OLD.complete_history_claimed <> NEW.complete_history_claimed
     OR OLD.loader_version <> NEW.loader_version
     OR OLD.input_fingerprint <> NEW.input_fingerprint
     OR OLD.expected_counts <> NEW.expected_counts
     OR OLD.migration_sha256 <> NEW.migration_sha256
     OR OLD.code_sha256 <> NEW.code_sha256
     OR OLD.started_at <> NEW.started_at THEN
    RAISE EXCEPTION 'only loading-to-sealed transcript transition is allowed';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_transcript.guard_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'transcript evidence tables cannot be truncated';
END;
$$;

DROP TRIGGER IF EXISTS transcript_run_update_guard
  ON nhi_rule_history_transcript.transcript_run;
CREATE TRIGGER transcript_run_update_guard
BEFORE UPDATE OR DELETE ON nhi_rule_history_transcript.transcript_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_transcript.guard_run_update();

CREATE OR REPLACE FUNCTION
  nhi_rule_history_transcript.guard_child_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_state text;
  target_run_id uuid;
BEGIN
  target_run_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.run_id ELSE NEW.run_id END;
  SELECT state INTO parent_state
  FROM nhi_rule_history_transcript.transcript_run
  WHERE run_id = target_run_id;
  IF TG_OP <> 'INSERT' OR parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION 'transcript child rows are immutable after insert';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'transcript_run',
    'proofread_artifact',
    'source_page',
    'source_segment',
    'lineage_analysis_artifact',
    'lineage_candidate'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_transcript.%I',
      table_name || '_truncate_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE '
      'ON nhi_rule_history_transcript.%I FOR EACH STATEMENT '
      'EXECUTE FUNCTION nhi_rule_history_transcript.guard_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'proofread_artifact',
    'source_page',
    'source_segment',
    'lineage_analysis_artifact',
    'lineage_candidate'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I ON nhi_rule_history_transcript.%I',
      table_name || '_write_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE '
      'ON nhi_rule_history_transcript.%I FOR EACH ROW '
      'EXECUTE FUNCTION nhi_rule_history_transcript.guard_child_write()',
      table_name || '_write_guard',
      table_name
    );
  END LOOP;
END;
$$;

CREATE OR REPLACE VIEW
  nhi_rule_history_transcript.v_sealed_transcript_run
AS
SELECT *
FROM nhi_rule_history_transcript.transcript_run
WHERE state = 'sealed';

CREATE OR REPLACE VIEW
  nhi_rule_history_transcript.v_sealed_source_segment
AS
SELECT segment.*
FROM nhi_rule_history_transcript.source_segment segment
JOIN nhi_rule_history_transcript.transcript_run run
  ON run.run_id = segment.run_id
 AND run.state = 'sealed';

CREATE OR REPLACE VIEW
  nhi_rule_history_transcript.v_sealed_lineage_candidate
AS
SELECT candidate.*
FROM nhi_rule_history_transcript.lineage_candidate candidate
JOIN nhi_rule_history_transcript.transcript_run run
  ON run.run_id = candidate.run_id
 AND run.state = 'sealed';

COMMIT;
