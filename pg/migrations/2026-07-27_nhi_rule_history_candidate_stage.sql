-- 2026-07-27 — source-grounded continuous-update candidates (stage only)
--
-- Candidates are nonauthoritative proposals.  They preserve exact source spans
-- and validation evidence, stop before any legal-history write, and cannot
-- encode executable mutation instructions.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_candidate_stage-global', 0)
);

DO $dependency_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_update_ops'
      AND obj_description(oid, 'pg_namespace') =
        'Stage-only operational evidence for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_update_ops/v1'
  ) THEN
    RAISE EXCEPTION
      'managed update-ops v1 schema is required before candidate stage'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Stage-only source-grounded proposals for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_candidate_stage/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_candidate_stage'
  ) THEN
    CREATE SCHEMA nhi_rule_history_candidate_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_candidate_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'nhi_rule_history_candidate_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'nhi_rule_history_candidate_stage exists without the managed v1 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  managed_comment text :=
    'NOLOGIN capability role for NHI source-grounded candidate staging only. managed=nhi_rule_history_candidate_runtime/v1';
  existing_comment text;
  can_login boolean;
  is_super boolean;
  can_create_db boolean;
  can_create_role boolean;
  inherits_privileges boolean;
  can_replicate boolean;
  can_bypass_rls boolean;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime'
  ) THEN
    CREATE ROLE nhi_rule_history_candidate_runtime
      NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
      NOBYPASSRLS;
    COMMENT ON ROLE nhi_rule_history_candidate_runtime IS
      'NOLOGIN capability role for NHI source-grounded candidate staging only. managed=nhi_rule_history_candidate_runtime/v1';
  ELSE
    SELECT
      shobj_description(oid, 'pg_authid'),
      rolcanlogin,
      rolsuper,
      rolcreatedb,
      rolcreaterole,
      rolinherit,
      rolreplication,
      rolbypassrls
      INTO
        existing_comment,
        can_login,
        is_super,
        can_create_db,
        can_create_role,
        inherits_privileges,
        can_replicate,
        can_bypass_rls
    FROM pg_roles
    WHERE rolname = 'nhi_rule_history_candidate_runtime';
    IF existing_comment IS DISTINCT FROM managed_comment
       OR can_login OR is_super OR can_create_db OR can_create_role
       OR inherits_privileges OR can_replicate OR can_bypass_rls THEN
      RAISE EXCEPTION
        'nhi_rule_history_candidate_runtime exists without the managed least-privilege marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$role_guard$;

CREATE DOMAIN nhi_rule_history_candidate_stage.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE FUNCTION
  nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(
    document jsonb
  )
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $$
DECLARE
  item record;
  element jsonb;
BEGIN
  IF pg_catalog.jsonb_typeof(document) = 'object' THEN
    FOR item IN
      SELECT key, value FROM pg_catalog.jsonb_each(document)
    LOOP
      IF pg_catalog.lower(item.key) = ANY (
        ARRAY[
          'rule_id',
          'stable_rule_id',
          'canonical_slug',
          'predecessor_id',
          'old_snapshot_id',
          'new_snapshot_id',
          'close_snapshot_id',
          'effective_until',
          'effective_until_exclusive',
          'effective_to',
          'head_generation',
          'proposed_operation',
          'proposed_operations',
          'executable_operation',
          'executable_operations'
        ]
      ) THEN
        RETURN true;
      END IF;
      IF nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(
        item.value
      ) THEN
        RETURN true;
      END IF;
    END LOOP;
  ELSIF pg_catalog.jsonb_typeof(document) = 'array' THEN
    FOR element IN
      SELECT value FROM pg_catalog.jsonb_array_elements(document)
    LOOP
      IF nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(
        element
      ) THEN
        RETURN true;
      END IF;
    END LOOP;
  END IF;
  RETURN false;
END;
$$;

CREATE TABLE nhi_rule_history_candidate_stage.candidate_proposal (
  proposal_id uuid PRIMARY KEY,
  proposal_fingerprint
    nhi_rule_history_candidate_stage.sha256_hex NOT NULL UNIQUE,
  contract_version text NOT NULL,
  job_id uuid NOT NULL,
  bundle_receipt_id uuid NOT NULL,
  producer_attempt_id uuid NOT NULL,
  producer_output_sha256
    nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  source_designation_text text NOT NULL,
  raw_effective_expression text,
  calendar_system text NOT NULL,
  effective_from date,
  date_precision text NOT NULL,
  date_role text NOT NULL,
  date_scope text NOT NULL,
  conditionality text NOT NULL,
  replacement_scope text NOT NULL,
  omitted_text_present boolean NOT NULL,
  merged_cells_present boolean NOT NULL,
  cross_row_dependency boolean NOT NULL,
  multiple_designations_present boolean NOT NULL,
  odt_pdf_agreement text NOT NULL,
  identity_resolution text NOT NULL,
  confidence numeric(5,4) NOT NULL,
  candidate_note text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (job_id, bundle_receipt_id)
    REFERENCES nhi_rule_history_update_ops.bundle_receipt
      (job_id, receipt_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (job_id, producer_attempt_id)
    REFERENCES nhi_rule_history_update_ops.worker_attempt
      (job_id, attempt_id)
    ON DELETE RESTRICT,
  CONSTRAINT candidate_calendar_chk
    CHECK (calendar_system IN ('roc', 'gregorian', 'unresolved')),
  CONSTRAINT candidate_date_precision_chk
    CHECK (date_precision IN ('day', 'month', 'year', 'unresolved')),
  CONSTRAINT candidate_date_role_chk
    CHECK (
      date_role IN (
        'effective_date',
        'announcement_date',
        'document_date',
        'unresolved'
      )
    ),
  CONSTRAINT candidate_date_scope_chk
    CHECK (
      date_scope IN (
        'single_clause',
        'table_row',
        'document',
        'conditional',
        'unresolved'
      )
    ),
  CONSTRAINT candidate_conditionality_chk
    CHECK (
      conditionality IN (
        'unconditional',
        'conditional',
        'unresolved'
      )
    ),
  CONSTRAINT candidate_replacement_scope_chk
    CHECK (
      replacement_scope IN (
        'full_single_clause',
        'partial_patch',
        'multiple_clauses',
        'correction',
        'unresolved'
      )
    ),
  CONSTRAINT candidate_agreement_chk
    CHECK (
      odt_pdf_agreement IN (
        'agree',
        'disagree',
        'not_available',
        'unresolved'
      )
    ),
  CONSTRAINT candidate_identity_chk
    CHECK (
      identity_resolution IN (
        'source_designation_only',
        'ambiguous',
        'split_or_merge_possible',
        'designation_reuse_possible'
      )
    ),
  CONSTRAINT candidate_confidence_chk
    CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT candidate_date_expression_chk
    CHECK (
      (
        effective_from IS NULL
        AND (
          raw_effective_expression IS NULL
          OR calendar_system = 'unresolved'
          OR date_role <> 'effective_date'
        )
      )
      OR (
        effective_from IS NOT NULL
        AND raw_effective_expression IS NOT NULL
        AND calendar_system <> 'unresolved'
        AND date_precision <> 'unresolved'
        AND date_role = 'effective_date'
      )
    )
);

CREATE TABLE nhi_rule_history_candidate_stage.candidate_source_span (
  proposal_id uuid NOT NULL
    REFERENCES nhi_rule_history_candidate_stage.candidate_proposal
      (proposal_id)
    ON DELETE RESTRICT,
  span_id nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  artifact_sha256 nhi_rule_history_candidate_stage.sha256_hex NOT NULL
    REFERENCES nhi_rule_history_update_ops.content_artifact
      (artifact_sha256)
    ON DELETE RESTRICT,
  source_role text NOT NULL,
  locator jsonb NOT NULL,
  locator_key text NOT NULL,
  char_start bigint NOT NULL CHECK (char_start >= 0),
  char_end bigint NOT NULL,
  raw_text text NOT NULL,
  raw_text_sha256 nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  raw_text_char_length bigint NOT NULL,
  observed_at timestamptz NOT NULL,
  statement text NOT NULL,
  PRIMARY KEY (proposal_id, span_id),
  CONSTRAINT candidate_source_span_role_chk
    CHECK (
      source_role IN (
        'feed_item',
        'detail_announcement',
        'comparison_new',
        'comparison_old',
        'effective_expression',
        'current_anchor',
        'next_anchor',
        'pdf_corroboration'
      )
    ),
  CONSTRAINT candidate_source_span_locator_chk
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND NOT nhi_rule_history_candidate_stage
        .document_has_forbidden_candidate_key(locator)
    ),
  CONSTRAINT candidate_source_span_offsets_chk
    CHECK (
      char_end > char_start
      AND char_end - char_start = raw_text_char_length
      AND raw_text_char_length = char_length(raw_text)
      AND raw_text_char_length > 0
    ),
  CONSTRAINT candidate_source_span_nonclaim_chk
    CHECK (
      statement =
      'Source-grounded candidate evidence only; no legal-history identity, adjacency, interval closure, or executable mutation authority.'
    )
);

CREATE TABLE nhi_rule_history_candidate_stage.candidate_evidence (
  proposal_id uuid NOT NULL,
  evidence_id nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  span_id nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  evidence_code text NOT NULL,
  outcome text NOT NULL,
  assertion_text text NOT NULL,
  evidence_details jsonb NOT NULL,
  validator_version text NOT NULL,
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (proposal_id, evidence_id),
  FOREIGN KEY (proposal_id, span_id)
    REFERENCES nhi_rule_history_candidate_stage.candidate_source_span
      (proposal_id, span_id)
    ON DELETE RESTRICT,
  CONSTRAINT candidate_evidence_outcome_chk
    CHECK (outcome IN ('pass', 'fail', 'unresolved', 'not_applicable')),
  CONSTRAINT candidate_evidence_details_chk
    CHECK (
      jsonb_typeof(evidence_details) = 'object'
      AND NOT nhi_rule_history_candidate_stage
        .document_has_forbidden_candidate_key(evidence_details)
    )
);

CREATE TABLE nhi_rule_history_candidate_stage.candidate_state_transition (
  proposal_id uuid NOT NULL
    REFERENCES nhi_rule_history_candidate_stage.candidate_proposal
      (proposal_id)
    ON DELETE RESTRICT,
  transition_seq integer NOT NULL CHECK (transition_seq >= 1),
  transition_id uuid NOT NULL UNIQUE,
  state text NOT NULL,
  actor_kind text NOT NULL,
  decision_basis_sha256
    nhi_rule_history_candidate_stage.sha256_hex NOT NULL,
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (proposal_id, transition_seq),
  CONSTRAINT candidate_state_allowed_chk
    CHECK (
      state IN (
        'validated_candidate',
        'promotion_ready_pending_anchor',
        'needs_review',
        'rejected'
      )
    ),
  CONSTRAINT candidate_state_actor_chk
    CHECK (
      actor_kind IN (
        'deterministic_validator',
        'source_capable_reviewer',
        'system_gate'
      )
    )
);

CREATE VIEW nhi_rule_history_candidate_stage.current_candidate_state AS
SELECT DISTINCT ON (transition.proposal_id)
  transition.proposal_id,
  transition.transition_seq,
  transition.state,
  transition.actor_kind,
  transition.decision_basis_sha256,
  transition.recorded_at
FROM nhi_rule_history_candidate_stage.candidate_state_transition transition
ORDER BY transition.proposal_id, transition.transition_seq DESC;

CREATE FUNCTION
  nhi_rule_history_candidate_stage.reject_append_only_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'candidate content and decisions are append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION nhi_rule_history_candidate_stage.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION
    'candidate stage tables cannot be truncated'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$$;

CREATE FUNCTION
  nhi_rule_history_candidate_stage.guard_candidate_proposal_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  receipt_state text;
  attempt_state text;
  attempt_output nhi_rule_history_update_ops.sha256_hex;
BEGIN
  SELECT receipt_status
    INTO receipt_state
  FROM nhi_rule_history_update_ops.bundle_receipt
  WHERE job_id = NEW.job_id
    AND receipt_id = NEW.bundle_receipt_id;
  IF receipt_state IS DISTINCT FROM 'received' THEN
    RAISE EXCEPTION
      'candidate requires a received, durable bundle receipt'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT status, output_sha256
    INTO attempt_state, attempt_output
  FROM nhi_rule_history_update_ops.worker_attempt
  WHERE job_id = NEW.job_id
    AND attempt_id = NEW.producer_attempt_id;
  IF attempt_state IS DISTINCT FROM 'success'
     OR attempt_output IS DISTINCT FROM NEW.producer_output_sha256 THEN
    RAISE EXCEPTION
      'candidate must match a successful recorded worker output'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION
  nhi_rule_history_candidate_stage.guard_state_transition_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  prior_state text;
  prior_seq integer;
  proposal_row nhi_rule_history_candidate_stage.candidate_proposal%ROWTYPE;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi_rule_history_candidate_transition:' || NEW.proposal_id::text,
      0
    )
  );
  SELECT * INTO proposal_row
  FROM nhi_rule_history_candidate_stage.candidate_proposal
  WHERE proposal_id = NEW.proposal_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'candidate proposal does not exist'
      USING ERRCODE = 'foreign_key_violation';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_candidate_stage.candidate_source_span
    WHERE proposal_id = NEW.proposal_id
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_candidate_stage.candidate_evidence
    WHERE proposal_id = NEW.proposal_id
  ) THEN
    RAISE EXCEPTION
      'candidate state requires at least one exact source span and evidence row'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT transition_seq, state
    INTO prior_seq, prior_state
  FROM nhi_rule_history_candidate_stage.candidate_state_transition
  WHERE proposal_id = NEW.proposal_id
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF prior_seq IS NULL THEN
    IF NEW.transition_seq <> 1
       OR NEW.state NOT IN (
         'validated_candidate', 'needs_review', 'rejected'
       ) THEN
      RAISE EXCEPTION
        'invalid initial candidate state'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    IF NEW.transition_seq <> prior_seq + 1 THEN
      RAISE EXCEPTION
        'candidate transition sequence must be gap-free'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF prior_state = 'validated_candidate'
       AND NEW.state NOT IN (
         'promotion_ready_pending_anchor', 'needs_review', 'rejected'
       ) THEN
      RAISE EXCEPTION
        'invalid transition from validated_candidate'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    ELSIF prior_state = 'promotion_ready_pending_anchor'
       AND NEW.state NOT IN ('needs_review', 'rejected') THEN
      RAISE EXCEPTION
        'pending-anchor candidates can only be demoted or rejected here'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    ELSIF prior_state IN ('needs_review', 'rejected') THEN
      RAISE EXCEPTION
        'review/rejection states are terminal for an immutable proposal'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  IF NEW.state = 'promotion_ready_pending_anchor'
     AND (
       proposal_row.replacement_scope <> 'full_single_clause'
       OR proposal_row.omitted_text_present
       OR proposal_row.merged_cells_present
       OR proposal_row.cross_row_dependency
       OR proposal_row.multiple_designations_present
       OR proposal_row.odt_pdf_agreement NOT IN ('agree', 'not_available')
       OR proposal_row.identity_resolution <> 'source_designation_only'
     ) THEN
    RAISE EXCEPTION
      'only a complete, single-clause, source-consistent proposal may wait for anchor replay'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER candidate_proposal_insert_guard
BEFORE INSERT ON nhi_rule_history_candidate_stage.candidate_proposal
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_candidate_stage.guard_candidate_proposal_insert();

CREATE TRIGGER candidate_state_transition_insert_guard
BEFORE INSERT ON
  nhi_rule_history_candidate_stage.candidate_state_transition
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_candidate_stage.guard_state_transition_insert();

DO $append_only_guards$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'candidate_proposal',
    'candidate_source_span',
    'candidate_evidence',
    'candidate_state_transition'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON nhi_rule_history_candidate_stage.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_candidate_stage.reject_append_only_change()',
      table_name || '_append_only_guard',
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON nhi_rule_history_candidate_stage.%I FOR EACH STATEMENT EXECUTE FUNCTION nhi_rule_history_candidate_stage.reject_truncate()',
      table_name || '_truncate_guard',
      table_name
    );
  END LOOP;
END;
$append_only_guards$;

REVOKE ALL ON SCHEMA nhi_rule_history_candidate_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_candidate_stage
  FROM PUBLIC;
REVOKE ALL ON TYPE nhi_rule_history_candidate_stage.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA
  nhi_rule_history_candidate_stage FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_candidate_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_candidate_stage
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhi_rule_history_candidate_stage
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history_update_ops
  TO nhi_rule_history_candidate_runtime;
GRANT SELECT ON
  nhi_rule_history_update_ops.update_job,
  nhi_rule_history_update_ops.worker_attempt,
  nhi_rule_history_update_ops.content_artifact,
  nhi_rule_history_update_ops.bundle_receipt
  TO nhi_rule_history_candidate_runtime;
GRANT USAGE ON SCHEMA nhi_rule_history_candidate_stage
  TO nhi_rule_history_candidate_runtime;
GRANT SELECT, INSERT ON
  nhi_rule_history_candidate_stage.candidate_proposal,
  nhi_rule_history_candidate_stage.candidate_source_span,
  nhi_rule_history_candidate_stage.candidate_evidence,
  nhi_rule_history_candidate_stage.candidate_state_transition
  TO nhi_rule_history_candidate_runtime;
GRANT SELECT ON nhi_rule_history_candidate_stage.current_candidate_state
  TO nhi_rule_history_candidate_runtime;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_candidate_stage.document_has_forbidden_candidate_key(jsonb)
  TO nhi_rule_history_candidate_runtime;

COMMENT ON TABLE nhi_rule_history_candidate_stage.candidate_proposal IS
  'Immutable source-grounded interpretation proposal; never a legal-history mutation.';
COMMENT ON TABLE
  nhi_rule_history_candidate_stage.candidate_source_span IS
  'Exact text spans and locators tying every proposal back to captured official bytes.';
COMMENT ON TABLE nhi_rule_history_candidate_stage.candidate_evidence IS
  'Validator findings attached to exact source spans; agent output remains nonauthoritative.';
COMMENT ON TABLE
  nhi_rule_history_candidate_stage.candidate_state_transition IS
  'Append-only candidate state machine ending before any legal-history write.';

COMMIT;
