-- 2026-07-30 — source-bound opt-out presentation profiles.
--
-- Official text, normalized structure and exact adjacent diff remain in their
-- existing sealed runs.  This additive lane stores a reviewed, agent-authored
-- reading arrangement for an unusually complex clause version.  A profile is
-- usable only with the exact composed-text hash and exact diff fingerprint it
-- reviewed.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-reader-profile-v26', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_reader_profile_run (
    profile_run_id uuid PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('loading', 'sealed')),
    schema_version text NOT NULL CHECK (
      schema_version = 'nhi-reimbursement-rules/reader-profile/v1'
    ),
    loader_version text NOT NULL CHECK (loader_version <> ''),
    source_release_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_announced.release_run(run_id),
    input_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL UNIQUE,
    expected_profile_count integer NOT NULL CHECK (
      expected_profile_count > 0
    ),
    verified_profile_count integer,
    output_fingerprint nhi_rule_history_announced.sha256_hex,
    sealed_fingerprint nhi_rule_history_announced.sha256_hex,
    started_at timestamptz NOT NULL,
    sealed_at timestamptz,
    CHECK (
      (
        state = 'loading'
        AND verified_profile_count IS NULL
        AND output_fingerprint IS NULL
        AND sealed_fingerprint IS NULL
        AND sealed_at IS NULL
      )
      OR
      (
        state = 'sealed'
        AND verified_profile_count = expected_profile_count
        AND output_fingerprint IS NOT NULL
        AND sealed_fingerprint IS NOT NULL
        AND sealed_at IS NOT NULL
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_reader_profile (
    profile_run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_announced.clause_reader_profile_run(profile_run_id),
    profile_id uuid NOT NULL,
    clause_code text NOT NULL CHECK (
      clause_code ~ '^[1-9][0-9]*(?:[.][0-9]+)+$'
    ),
    source_release_run_id uuid NOT NULL,
    source_version_id uuid NOT NULL,
    source_composed_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_diff_run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_announced.clause_document_diff_run(diff_run_id),
    source_diff_output_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL,
    presentation_mode text NOT NULL CHECK (
      presentation_mode = 'agentic_specialized'
    ),
    template_key text NOT NULL CHECK (
      template_key = 'dyslipidemia_pathway_v1'
    ),
    profile_contract text NOT NULL CHECK (
      profile_contract = 'nhi-reimbursement-rules/reader-profile/v1'
    ),
    authoring_method text NOT NULL CHECK (
      authoring_method = 'agentic_owner_authorized'
    ),
    review_status text NOT NULL CHECK (
      review_status = 'owner_authorized'
    ),
    disclosure_text text NOT NULL CHECK (disclosure_text <> ''),
    content_payload jsonb NOT NULL CHECK (
      jsonb_typeof(content_payload) = 'object'
      AND content_payload <> '{}'::jsonb
    ),
    content_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (profile_run_id, profile_id),
    UNIQUE (
      profile_run_id, clause_code, source_version_id, presentation_mode
    ),
    FOREIGN KEY (source_release_run_id, source_version_id)
      REFERENCES nhi_rule_history_announced.composed_clause_version(
        run_id, version_id
      )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_reader_profile_control_event (
    control_event_id uuid PRIMARY KEY,
    profile_run_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('activate', 'disable')),
    reason text NOT NULL CHECK (reason <> ''),
    recorded_at timestamptz NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    FOREIGN KEY (profile_run_id, profile_id)
      REFERENCES nhi_rule_history_announced.clause_reader_profile(
        profile_run_id, profile_id
      )
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  run_state text;
  run_source_release uuid;
  release_state text;
  actual_composed_sha text;
  diff_state text;
  actual_diff_fingerprint text;
  diff_newer_version_id uuid;
  diff_newer_text_sha text;
BEGIN
  SELECT state, source_release_run_id
    INTO run_state, run_source_release
  FROM nhi_rule_history_announced.clause_reader_profile_run
  WHERE profile_run_id=NEW.profile_run_id;
  IF run_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION 'reader profile rows require a loading profile run';
  END IF;
  IF run_source_release IS DISTINCT FROM NEW.source_release_run_id THEN
    RAISE EXCEPTION 'reader profile run and source release differ';
  END IF;

  SELECT state INTO release_state
  FROM nhi_rule_history_announced.release_run
  WHERE run_id=NEW.source_release_run_id;
  IF release_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'reader profile source release must be sealed';
  END IF;

  SELECT composed_text_sha256 INTO actual_composed_sha
  FROM nhi_rule_history_announced.composed_clause_version
  WHERE run_id=NEW.source_release_run_id
    AND version_id=NEW.source_version_id
    AND clause_code=NEW.clause_code;
  IF actual_composed_sha IS DISTINCT FROM NEW.source_composed_text_sha256 THEN
    RAISE EXCEPTION 'reader profile composed-text binding mismatch';
  END IF;

  SELECT diff.state, diff.output_fingerprint,
         newer.source_version_id, newer.exact_text_sha256
    INTO diff_state, actual_diff_fingerprint,
         diff_newer_version_id, diff_newer_text_sha
  FROM nhi_rule_history_announced.clause_document_diff_run diff
  JOIN nhi_rule_history_announced
    .clause_document_expression_relation relation
    ON relation.normalization_run_id=diff.normalization_run_id
   AND relation.relation_id=diff.relation_id
  JOIN nhi_rule_history_announced.clause_document_expression newer
    ON newer.normalization_run_id=relation.normalization_run_id
   AND newer.expression_id=relation.newer_expression_id
  WHERE diff.diff_run_id=NEW.source_diff_run_id;
  IF diff_state IS DISTINCT FROM 'sealed'
     OR actual_diff_fingerprint IS DISTINCT FROM
       NEW.source_diff_output_fingerprint
     OR diff_newer_version_id IS DISTINCT FROM NEW.source_version_id
     OR diff_newer_text_sha IS DISTINCT FROM
       NEW.source_composed_text_sha256 THEN
    RAISE EXCEPTION 'reader profile exact-diff binding mismatch';
  END IF;

  IF NEW.content_sha256 IS DISTINCT FROM
    encode(
      sha256(convert_to(NEW.content_payload::text, 'UTF8')),
      'hex'
    ) THEN
    RAISE EXCEPTION 'reader profile content hash mismatch';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_run_seal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  actual_count integer;
  actual_output text;
  expected_seal text;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed reader profile runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'reader profile run permits only loading to sealed';
  END IF;
  IF NEW.profile_run_id IS DISTINCT FROM OLD.profile_run_id
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.loader_version IS DISTINCT FROM OLD.loader_version
     OR NEW.source_release_run_id IS DISTINCT FROM OLD.source_release_run_id
     OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
     OR NEW.expected_profile_count IS DISTINCT FROM OLD.expected_profile_count
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'reader profile run identity and inputs are immutable';
  END IF;

  SELECT count(*),
         encode(
           sha256(
             convert_to(
               coalesce(
                 string_agg(
                   source_row_sha256::text,
                   ',' ORDER BY profile_id
                 ),
                 ''
               ),
               'UTF8'
             )
           ),
           'hex'
         )
    INTO actual_count, actual_output
  FROM nhi_rule_history_announced.clause_reader_profile
  WHERE profile_run_id=OLD.profile_run_id;

  expected_seal := encode(
    sha256(
      convert_to(
        concat_ws(
          '|',
          OLD.profile_run_id::text,
          OLD.input_fingerprint::text,
          actual_output
        ),
        'UTF8'
      )
    ),
    'hex'
  );
  IF actual_count <> OLD.expected_profile_count
     OR NEW.verified_profile_count <> actual_count
     OR NEW.output_fingerprint IS DISTINCT FROM actual_output
     OR NEW.sealed_fingerprint IS DISTINCT FROM expected_seal THEN
    RAISE EXCEPTION 'reader profile seal receipt mismatch';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_control_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  run_state text;
BEGIN
  SELECT state INTO run_state
  FROM nhi_rule_history_announced.clause_reader_profile_run
  WHERE profile_run_id=NEW.profile_run_id;
  IF run_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'reader profile activation requires a sealed profile run';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS clause_reader_profile_insert_guard
  ON nhi_rule_history_announced.clause_reader_profile;
CREATE TRIGGER clause_reader_profile_insert_guard
BEFORE INSERT ON nhi_rule_history_announced.clause_reader_profile
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_insert();

DROP TRIGGER IF EXISTS clause_reader_profile_mutation_guard
  ON nhi_rule_history_announced.clause_reader_profile;
CREATE TRIGGER clause_reader_profile_mutation_guard
BEFORE UPDATE OR DELETE ON
  nhi_rule_history_announced.clause_reader_profile
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS clause_reader_profile_truncate_guard
  ON nhi_rule_history_announced.clause_reader_profile;
CREATE TRIGGER clause_reader_profile_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.clause_reader_profile
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS clause_reader_profile_run_seal_guard
  ON nhi_rule_history_announced.clause_reader_profile_run;
CREATE TRIGGER clause_reader_profile_run_seal_guard
BEFORE UPDATE ON nhi_rule_history_announced.clause_reader_profile_run
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_run_seal();

DROP TRIGGER IF EXISTS clause_reader_profile_run_delete_guard
  ON nhi_rule_history_announced.clause_reader_profile_run;
CREATE TRIGGER clause_reader_profile_run_delete_guard
BEFORE DELETE ON nhi_rule_history_announced.clause_reader_profile_run
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS clause_reader_profile_run_truncate_guard
  ON nhi_rule_history_announced.clause_reader_profile_run;
CREATE TRIGGER clause_reader_profile_run_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.clause_reader_profile_run
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS clause_reader_profile_control_insert_guard
  ON nhi_rule_history_announced.clause_reader_profile_control_event;
CREATE TRIGGER clause_reader_profile_control_insert_guard
BEFORE INSERT ON
  nhi_rule_history_announced.clause_reader_profile_control_event
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_clause_reader_profile_control_insert();

DROP TRIGGER IF EXISTS clause_reader_profile_control_mutation_guard
  ON nhi_rule_history_announced.clause_reader_profile_control_event;
CREATE TRIGGER clause_reader_profile_control_mutation_guard
BEFORE UPDATE OR DELETE ON
  nhi_rule_history_announced.clause_reader_profile_control_event
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS clause_reader_profile_control_truncate_guard
  ON nhi_rule_history_announced.clause_reader_profile_control_event;
CREATE TRIGGER clause_reader_profile_control_truncate_guard
BEFORE TRUNCATE ON
  nhi_rule_history_announced.clause_reader_profile_control_event
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_public_clause_reader_profile AS
WITH latest_control AS (
  SELECT DISTINCT ON (
           profile.clause_code,
           profile.source_version_id
         )
         profile.clause_code,
         profile.source_version_id,
         control.profile_run_id,
         control.profile_id,
         control.action,
         control.recorded_at
  FROM
    nhi_rule_history_announced.clause_reader_profile_control_event control
  JOIN
    nhi_rule_history_announced.clause_reader_profile profile
    USING (profile_run_id, profile_id)
  ORDER BY
    profile.clause_code,
    profile.source_version_id,
    control.recorded_at DESC,
    control.control_event_id DESC
)
SELECT
  profile.profile_id,
  profile.clause_code,
  profile.source_release_run_id,
  profile.source_version_id,
  profile.source_composed_text_sha256,
  profile.source_diff_run_id,
  profile.source_diff_output_fingerprint,
  profile.presentation_mode,
  profile.template_key,
  profile.profile_contract,
  profile.authoring_method,
  profile.review_status,
  profile.disclosure_text,
  profile.content_payload,
  profile.content_sha256,
  latest.recorded_at AS activated_at
FROM latest_control latest
JOIN nhi_rule_history_announced.clause_reader_profile profile
  USING (profile_run_id, profile_id)
JOIN nhi_rule_history_announced.clause_reader_profile_run run
  USING (profile_run_id)
WHERE latest.action = 'activate'
  AND run.state = 'sealed';

COMMIT;
