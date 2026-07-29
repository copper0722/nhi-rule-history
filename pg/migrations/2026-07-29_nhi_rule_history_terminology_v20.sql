-- 2026-07-29 — immutable concept / alias / occurrence projection
--
-- PostgreSQL owns terminology identity, reviewed alias admission, external
-- code links, exact source-block occurrences, and the complete scan
-- denominator.  Browser/API consumers read only the latest activated sealed
-- run whose source publication is still active.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-terminology-v20', 0)
);

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_terminology;

CREATE DOMAIN nhi_rule_history_terminology.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE nhi_rule_history_terminology.concept_registry (
  concept_id uuid PRIMARY KEY,
  identity_basis text NOT NULL UNIQUE CHECK (identity_basis <> ''),
  identity_version text NOT NULL CHECK (identity_version <> ''),
  created_from text NOT NULL CHECK (
    created_from IN ('reviewed_legacy_tag_set', 'future_reviewed_source')
  ),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  created_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE nhi_rule_history_terminology.tagging_run (
  tagging_run_id uuid PRIMARY KEY,
  publication_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_publication.publication_run (run_id)
    ON DELETE RESTRICT,
  seed_enrichment_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
    ON DELETE RESTRICT,
  alias_proposal_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  matcher_version text NOT NULL CHECK (matcher_version <> ''),
  loader_version text NOT NULL CHECK (loader_version <> ''),
  offset_contract text NOT NULL CHECK (
    offset_contract = 'unicode_scalar_half_open+utf8_byte_half_open/v1'
  ),
  alias_admission_policy text NOT NULL CHECK (
    alias_admission_policy = 'reviewed_source_observed_only/v1'
  ),
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  input_fingerprint nhi_rule_history_terminology.sha256_hex NOT NULL UNIQUE,
  expected_counts jsonb NOT NULL CHECK (
    jsonb_typeof(expected_counts) = 'object'
    AND expected_counts <> '{}'::jsonb
  ),
  verified_counts jsonb,
  verified_metrics jsonb,
  table_fingerprints jsonb,
  output_fingerprint nhi_rule_history_terminology.sha256_hex,
  sealed_fingerprint nhi_rule_history_terminology.sha256_hex,
  started_at timestamptz NOT NULL DEFAULT current_timestamp,
  sealed_at timestamptz,
  CHECK (
    (
      state = 'loading'
      AND verified_counts IS NULL
      AND verified_metrics IS NULL
      AND table_fingerprints IS NULL
      AND output_fingerprint IS NULL
      AND sealed_fingerprint IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND verified_counts IS NOT NULL
      AND verified_metrics IS NOT NULL
      AND table_fingerprints IS NOT NULL
      AND output_fingerprint IS NOT NULL
      AND sealed_fingerprint IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  )
);

CREATE TABLE nhi_rule_history_terminology.run_concept (
  tagging_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_terminology.tagging_run (tagging_run_id)
    ON DELETE RESTRICT,
  concept_id uuid NOT NULL
    REFERENCES nhi_rule_history_terminology.concept_registry (concept_id)
    ON DELETE RESTRICT,
  concept_type text NOT NULL CHECK (
    concept_type IN (
      'disease', 'drug_brand', 'drug_class', 'drug_ingredient',
      'treatment_modality'
    )
  ),
  canonical_label_zh text,
  canonical_label_en text,
  link_family text NOT NULL CHECK (
    link_family IN ('atc', 'icd11', 'nhi_treatment')
  ),
  review_status text NOT NULL CHECK (
    review_status = 'reviewed_seed_group'
  ),
  provenance jsonb NOT NULL CHECK (
    jsonb_typeof(provenance) = 'object'
    AND provenance <> '{}'::jsonb
  ),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, concept_id),
  CHECK (
    coalesce(nullif(canonical_label_zh, ''), nullif(canonical_label_en, ''))
      IS NOT NULL
  )
);

CREATE TABLE nhi_rule_history_terminology.concept_seed_tag_link (
  tagging_run_id uuid NOT NULL,
  concept_id uuid NOT NULL,
  legacy_tag_id text NOT NULL CHECK (legacy_tag_id <> ''),
  seed_enrichment_run_id uuid NOT NULL,
  mapping_status text NOT NULL CHECK (
    mapping_status = 'reviewed_seed_group_member'
  ),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, legacy_tag_id),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES nhi_rule_history_terminology.run_concept
      (tagging_run_id, concept_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (seed_enrichment_run_id, legacy_tag_id)
    REFERENCES nhi_rule_history_clause.clause_semantic_tag
      (enrichment_run_id, tag_id)
    ON DELETE RESTRICT
);

CREATE TABLE nhi_rule_history_terminology.concept_alias (
  tagging_run_id uuid NOT NULL,
  alias_id uuid NOT NULL,
  concept_id uuid NOT NULL,
  alias_text text NOT NULL CHECK (alias_text <> ''),
  normalized_alias text NOT NULL CHECK (normalized_alias <> ''),
  language_tag text NOT NULL CHECK (language_tag <> ''),
  alias_type text NOT NULL CHECK (
    alias_type IN (
      'abbreviation', 'brand_spelling', 'canonical',
      'historical_spelling', 'synonym', 'variant'
    )
  ),
  source_status text NOT NULL CHECK (
    source_status IN ('source_observed', 'model_suggested')
  ),
  proposed_auto_match boolean NOT NULL,
  match_rule text NOT NULL CHECK (
    match_rule IN ('exact', 'case_insensitive_token', 'context_required')
  ),
  production_status text NOT NULL CHECK (
    production_status IN ('admitted', 'candidate', 'blocked')
  ),
  production_reason text NOT NULL CHECK (
    production_reason IN (
      'reviewed_source_observed',
      'model_suggested_candidate',
      'context_required',
      'normalized_cross_concept_collision',
      'lexical_ambiguity_not_allowlisted'
    )
  ),
  ambiguity_note text,
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, alias_id),
  UNIQUE (tagging_run_id, concept_id, normalized_alias, alias_text),
  UNIQUE (tagging_run_id, alias_id, concept_id),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES nhi_rule_history_terminology.run_concept
      (tagging_run_id, concept_id)
    ON DELETE RESTRICT,
  CHECK (
    (production_status = 'admitted'
      AND source_status = 'source_observed'
      AND match_rule <> 'context_required'
      AND production_reason = 'reviewed_source_observed')
    OR production_status <> 'admitted'
  )
);

CREATE UNIQUE INDEX concept_alias_admitted_normalized_uniq
  ON nhi_rule_history_terminology.concept_alias (
    tagging_run_id, normalized_alias
  )
  WHERE production_status = 'admitted';

CREATE INDEX concept_alias_search_idx
  ON nhi_rule_history_terminology.concept_alias (
    tagging_run_id, normalized_alias, production_status
  );

CREATE TABLE nhi_rule_history_terminology.concept_external_code (
  tagging_run_id uuid NOT NULL,
  concept_id uuid NOT NULL,
  code_system text NOT NULL CHECK (
    code_system IN ('ATC', 'ICD11', 'NHI_TREATMENT')
  ),
  code text NOT NULL CHECK (code <> ''),
  relation_type text NOT NULL CHECK (
    relation_type = 'reviewed_seed_mapping'
  ),
  review_status text NOT NULL CHECK (
    review_status IN ('agent_verified', 'agent_curated')
  ),
  public_safe boolean NOT NULL CHECK (public_safe),
  master_source text NOT NULL CHECK (
    master_source IN (
      'tw_drug.ref_atc',
      'medical_knowledge.icd11_who',
      'tw_health_open.nhi_payment_standard'
    )
  ),
  master_release text NOT NULL CHECK (master_release <> ''),
  provenance jsonb NOT NULL CHECK (
    jsonb_typeof(provenance) = 'object'
    AND provenance <> '{}'::jsonb
  ),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, concept_id, code_system, code),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES nhi_rule_history_terminology.run_concept
      (tagging_run_id, concept_id)
    ON DELETE RESTRICT,
  CHECK (
    (code_system = 'ATC'
      AND code ~ '^[A-Z][0-9A-Z]{0,6}$'
      AND master_source = 'tw_drug.ref_atc')
    OR
    (code_system = 'ICD11'
      AND master_source = 'medical_knowledge.icd11_who')
    OR
    (code_system = 'NHI_TREATMENT'
      AND master_source = 'tw_health_open.nhi_payment_standard')
  )
);

CREATE TABLE nhi_rule_history_terminology.tagging_run_block_input (
  tagging_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_terminology.tagging_run (tagging_run_id)
    ON DELETE RESTRICT,
  publication_run_id uuid NOT NULL,
  clause_code text NOT NULL,
  block_order integer NOT NULL CHECK (block_order >= 0),
  source_block_id text NOT NULL CHECK (source_block_id <> ''),
  source_block_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  scan_status text NOT NULL CHECK (
    scan_status IN ('scanned_no_match', 'scanned_with_match')
  ),
  candidate_match_count integer NOT NULL CHECK (candidate_match_count >= 0),
  admitted_match_count integer NOT NULL CHECK (admitted_match_count >= 0),
  blocked_match_count integer NOT NULL CHECK (blocked_match_count >= 0),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, clause_code, block_order),
  FOREIGN KEY (publication_run_id, clause_code, block_order)
    REFERENCES nhi_rule_history_publication.current_clause_block
      (run_id, clause_code, block_order)
    ON DELETE RESTRICT,
  CHECK (
    scan_status = CASE
      WHEN candidate_match_count + admitted_match_count + blocked_match_count
        > 0
        THEN 'scanned_with_match'
      ELSE 'scanned_no_match'
    END
  )
);

CREATE TABLE nhi_rule_history_terminology.clause_occurrence (
  tagging_run_id uuid NOT NULL,
  occurrence_id uuid NOT NULL,
  publication_run_id uuid NOT NULL,
  clause_code text NOT NULL,
  block_order integer NOT NULL CHECK (block_order >= 0),
  source_block_id text NOT NULL CHECK (source_block_id <> ''),
  source_block_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  concept_id uuid NOT NULL,
  alias_id uuid NOT NULL,
  start_scalar integer NOT NULL CHECK (start_scalar >= 0),
  end_scalar integer NOT NULL CHECK (end_scalar > start_scalar),
  start_utf8_byte integer NOT NULL CHECK (start_utf8_byte >= 0),
  end_utf8_byte integer NOT NULL CHECK (end_utf8_byte > start_utf8_byte),
  matched_text text NOT NULL CHECK (matched_text <> ''),
  matched_text_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  occurrence_status text NOT NULL CHECK (
    occurrence_status IN ('candidate', 'admitted', 'blocked')
  ),
  occurrence_reason text NOT NULL CHECK (
    occurrence_reason IN (
      'alias_candidate',
      'reviewed_alias_longest_match',
      'alias_blocked',
      'overlap_lost',
      'same_span_cross_concept',
      'same_concept_duplicate'
    )
  ),
  match_rule text NOT NULL CHECK (
    match_rule IN ('exact', 'case_insensitive_token', 'context_required')
  ),
  source_row_sha256 nhi_rule_history_terminology.sha256_hex NOT NULL,
  PRIMARY KEY (tagging_run_id, occurrence_id),
  UNIQUE (
    tagging_run_id, clause_code, block_order, concept_id,
    start_scalar, end_scalar, occurrence_status, occurrence_reason
  ),
  FOREIGN KEY (tagging_run_id, alias_id, concept_id)
    REFERENCES nhi_rule_history_terminology.concept_alias
      (tagging_run_id, alias_id, concept_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (tagging_run_id, clause_code, block_order)
    REFERENCES nhi_rule_history_terminology.tagging_run_block_input
      (tagging_run_id, clause_code, block_order)
    ON DELETE RESTRICT,
  FOREIGN KEY (publication_run_id, clause_code, block_order)
    REFERENCES nhi_rule_history_publication.current_clause_block
      (run_id, clause_code, block_order)
    ON DELETE RESTRICT,
  CHECK (
    matched_text_sha256 =
      encode(sha256(convert_to(matched_text, 'UTF8')), 'hex')
  ),
  CHECK (
    occurrence_status <> 'admitted'
    OR occurrence_reason = 'reviewed_alias_longest_match'
  )
);

ALTER TABLE nhi_rule_history_terminology.clause_occurrence
  ADD CONSTRAINT clause_occurrence_admitted_no_overlap
  EXCLUDE USING gist (
    tagging_run_id WITH =,
    clause_code WITH =,
    block_order WITH =,
    int4range(start_scalar, end_scalar, '[)') WITH &&
  )
  WHERE (occurrence_status = 'admitted');

CREATE INDEX clause_occurrence_lookup_idx
  ON nhi_rule_history_terminology.clause_occurrence (
    tagging_run_id, clause_code, block_order, start_scalar
  );

CREATE TABLE nhi_rule_history_terminology.tagging_run_activation (
  activation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tagging_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_terminology.tagging_run (tagging_run_id)
    ON DELETE RESTRICT,
  prior_tagging_run_id uuid
    REFERENCES nhi_rule_history_terminology.tagging_run (tagging_run_id)
    ON DELETE RESTRICT,
  activation_reason text NOT NULL CHECK (
    activation_reason IN ('initial_release', 'supersede', 'rollback')
  ),
  activated_at timestamptz NOT NULL DEFAULT current_timestamp,
  CHECK (
    prior_tagging_run_id IS NULL
    OR prior_tagging_run_id <> tagging_run_id
  )
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'immutable terminology relation rejects %', TG_OP;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.guard_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  publication_block_count integer;
  scanned_block_count integer;
  publication_clause_count integer;
  scanned_clause_count integer;
  seed_tag_count integer;
  linked_seed_tag_count integer;
  unresolved_code_count integer;
BEGIN
  IF TG_OP = 'DELETE' OR OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed terminology run is immutable';
  END IF;
  IF NEW.state <> 'sealed' OR OLD.state <> 'loading' THEN
    RAISE EXCEPTION 'only loading to sealed transition is allowed';
  END IF;
  IF (
    NEW.tagging_run_id,
    NEW.publication_run_id,
    NEW.seed_enrichment_run_id,
    NEW.alias_proposal_sha256,
    NEW.matcher_version,
    NEW.loader_version,
    NEW.offset_contract,
    NEW.alias_admission_policy,
    NEW.input_fingerprint,
    NEW.expected_counts,
    NEW.started_at
  ) IS DISTINCT FROM (
    OLD.tagging_run_id,
    OLD.publication_run_id,
    OLD.seed_enrichment_run_id,
    OLD.alias_proposal_sha256,
    OLD.matcher_version,
    OLD.loader_version,
    OLD.offset_contract,
    OLD.alias_admission_policy,
    OLD.input_fingerprint,
    OLD.expected_counts,
    OLD.started_at
  ) THEN
    RAISE EXCEPTION 'terminology run identity changed during seal';
  END IF;

  SELECT count(*)::integer
  INTO publication_block_count
  FROM nhi_rule_history_publication.current_clause_block
  WHERE run_id = NEW.publication_run_id;

  SELECT count(*)::integer, count(DISTINCT clause_code)::integer
  INTO scanned_block_count, scanned_clause_count
  FROM nhi_rule_history_terminology.tagging_run_block_input
  WHERE tagging_run_id = NEW.tagging_run_id;

  SELECT count(*)::integer
  INTO publication_clause_count
  FROM nhi_rule_history_publication.current_clause
  WHERE run_id = NEW.publication_run_id;

  IF publication_block_count <> scanned_block_count
     OR publication_clause_count <> scanned_clause_count THEN
    RAISE EXCEPTION
      'terminology scan denominator mismatch: blocks %/%, clauses %/%',
      scanned_block_count, publication_block_count,
      scanned_clause_count, publication_clause_count;
  END IF;

  SELECT count(*)::integer
  INTO seed_tag_count
  FROM nhi_rule_history_clause.clause_semantic_tag
  WHERE enrichment_run_id = NEW.seed_enrichment_run_id;

  SELECT count(DISTINCT legacy_tag_id)::integer
  INTO linked_seed_tag_count
  FROM nhi_rule_history_terminology.concept_seed_tag_link
  WHERE tagging_run_id = NEW.tagging_run_id;

  IF seed_tag_count <> linked_seed_tag_count THEN
    RAISE EXCEPTION
      'reviewed seed tag conservation mismatch: %/%',
      linked_seed_tag_count, seed_tag_count;
  END IF;

  SELECT count(*)::integer
  INTO unresolved_code_count
  FROM nhi_rule_history_terminology.concept_external_code code_link
  WHERE code_link.tagging_run_id = NEW.tagging_run_id
    AND NOT (
      (
        code_link.code_system = 'ATC'
        AND EXISTS (
          SELECT 1 FROM tw_drug.ref_atc master
          WHERE master.atc_code = code_link.code
        )
      )
      OR
      (
        code_link.code_system = 'ICD11'
        AND EXISTS (
          SELECT 1 FROM medical_knowledge.icd11_who master
          WHERE master.code = code_link.code
            AND master.release_id = code_link.master_release
        )
      )
      OR
      (
        code_link.code_system = 'NHI_TREATMENT'
        AND EXISTS (
          SELECT 1 FROM tw_health_open.nhi_payment_standard master
          WHERE master.code = code_link.code
            AND master.end_date IS NULL
        )
      )
    );
  IF unresolved_code_count <> 0 THEN
    RAISE EXCEPTION
      'terminology run has % unresolved external codes',
      unresolved_code_count;
  END IF;

  IF NEW.verified_counts IS DISTINCT FROM NEW.expected_counts THEN
    RAISE EXCEPTION 'verified terminology counts differ from expected counts';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.guard_child_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  owner_run_id uuid;
  owner_state text;
BEGIN
  owner_run_id := CASE
    WHEN TG_OP = 'DELETE' THEN OLD.tagging_run_id
    ELSE NEW.tagging_run_id
  END;
  SELECT state INTO owner_state
  FROM nhi_rule_history_terminology.tagging_run
  WHERE tagging_run_id = owner_run_id;
  IF owner_state IS NULL THEN
    RAISE EXCEPTION 'terminology child has no owner run';
  END IF;
  IF owner_state <> 'loading' THEN
    RAISE EXCEPTION 'sealed terminology child rows are immutable';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.validate_block_input()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  owner_publication uuid;
  source_row record;
BEGIN
  SELECT publication_run_id INTO owner_publication
  FROM nhi_rule_history_terminology.tagging_run
  WHERE tagging_run_id = NEW.tagging_run_id;
  IF owner_publication IS DISTINCT FROM NEW.publication_run_id THEN
    RAISE EXCEPTION 'block input publication does not match owner run';
  END IF;
  SELECT source_block_id, raw_text_sha256
  INTO source_row
  FROM nhi_rule_history_publication.current_clause_block
  WHERE run_id = NEW.publication_run_id
    AND clause_code = NEW.clause_code
    AND block_order = NEW.block_order;
  IF source_row.source_block_id IS DISTINCT FROM NEW.source_block_id
     OR source_row.raw_text_sha256 IS DISTINCT FROM NEW.source_block_sha256
  THEN
    RAISE EXCEPTION 'block input identity/hash mismatch';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.validate_occurrence_offsets()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  owner_publication uuid;
  source_row record;
  scalar_slice text;
  byte_slice text;
BEGIN
  SELECT publication_run_id INTO owner_publication
  FROM nhi_rule_history_terminology.tagging_run
  WHERE tagging_run_id = NEW.tagging_run_id;
  IF owner_publication IS DISTINCT FROM NEW.publication_run_id THEN
    RAISE EXCEPTION 'occurrence publication does not match owner run';
  END IF;
  SELECT source_block_id, raw_text, raw_text_sha256
  INTO source_row
  FROM nhi_rule_history_publication.current_clause_block
  WHERE run_id = NEW.publication_run_id
    AND clause_code = NEW.clause_code
    AND block_order = NEW.block_order;
  IF source_row.source_block_id IS DISTINCT FROM NEW.source_block_id
     OR source_row.raw_text_sha256 IS DISTINCT FROM NEW.source_block_sha256
  THEN
    RAISE EXCEPTION 'occurrence source block identity/hash mismatch';
  END IF;
  scalar_slice := substring(
    source_row.raw_text
    FROM NEW.start_scalar + 1
    FOR NEW.end_scalar - NEW.start_scalar
  );
  byte_slice := convert_from(
    substring(
      convert_to(source_row.raw_text, 'UTF8')
      FROM NEW.start_utf8_byte + 1
      FOR NEW.end_utf8_byte - NEW.start_utf8_byte
    ),
    'UTF8'
  );
  IF scalar_slice IS DISTINCT FROM NEW.matched_text
     OR byte_slice IS DISTINCT FROM NEW.matched_text THEN
    RAISE EXCEPTION 'occurrence offset slice does not equal matched text';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_terminology.validate_activation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  candidate record;
  latest_run uuid;
BEGIN
  SELECT state, publication_run_id INTO candidate
  FROM nhi_rule_history_terminology.tagging_run
  WHERE tagging_run_id = NEW.tagging_run_id;
  IF candidate.state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'only sealed terminology runs may be activated';
  END IF;
  IF candidate.publication_run_id IS DISTINCT FROM (
    SELECT run_id
    FROM nhi_rule_history_publication.v_active_publication_run
  ) THEN
    RAISE EXCEPTION 'terminology run source publication is not active';
  END IF;
  SELECT tagging_run_id INTO latest_run
  FROM nhi_rule_history_terminology.tagging_run_activation
  ORDER BY activation_id DESC
  LIMIT 1;
  IF latest_run IS NOT DISTINCT FROM NEW.tagging_run_id THEN
    RAISE EXCEPTION 'terminology run is already active';
  END IF;
  IF latest_run IS DISTINCT FROM NEW.prior_tagging_run_id THEN
    RAISE EXCEPTION 'activation prior run does not match current active run';
  END IF;
  IF latest_run IS NULL
     AND NEW.activation_reason <> 'initial_release' THEN
    RAISE EXCEPTION 'first terminology activation must be initial_release';
  END IF;
  IF latest_run IS NOT NULL
     AND NEW.activation_reason = 'initial_release' THEN
    RAISE EXCEPTION 'later terminology activation is not initial_release';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER concept_registry_immutable
BEFORE UPDATE OR DELETE
ON nhi_rule_history_terminology.concept_registry
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.reject_mutation();

CREATE TRIGGER concept_registry_truncate_guard
BEFORE TRUNCATE
ON nhi_rule_history_terminology.concept_registry
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_terminology.reject_mutation();

CREATE TRIGGER tagging_run_update_guard
BEFORE UPDATE OR DELETE
ON nhi_rule_history_terminology.tagging_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.guard_run_update();

CREATE TRIGGER tagging_run_truncate_guard
BEFORE TRUNCATE
ON nhi_rule_history_terminology.tagging_run
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_terminology.reject_mutation();

CREATE TRIGGER block_input_validate
BEFORE INSERT OR UPDATE
ON nhi_rule_history_terminology.tagging_run_block_input
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.validate_block_input();

CREATE TRIGGER occurrence_offsets_validate
BEFORE INSERT OR UPDATE
ON nhi_rule_history_terminology.clause_occurrence
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.validate_occurrence_offsets();

CREATE TRIGGER activation_validate
BEFORE INSERT
ON nhi_rule_history_terminology.tagging_run_activation
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.validate_activation();

CREATE TRIGGER activation_immutable
BEFORE UPDATE OR DELETE
ON nhi_rule_history_terminology.tagging_run_activation
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_terminology.reject_mutation();

CREATE TRIGGER activation_truncate_guard
BEFORE TRUNCATE
ON nhi_rule_history_terminology.tagging_run_activation
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_terminology.reject_mutation();

DO $$
DECLARE
  relation_name text;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'run_concept',
    'concept_seed_tag_link',
    'concept_alias',
    'concept_external_code',
    'tagging_run_block_input',
    'clause_occurrence'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_terminology.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_terminology.guard_child_mutation()',
      relation_name || '_mutation_guard',
      relation_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON '
      'nhi_rule_history_terminology.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_terminology.reject_mutation()',
      relation_name || '_truncate_guard',
      relation_name
    );
  END LOOP;
END;
$$;

CREATE OR REPLACE VIEW
  nhi_rule_history_terminology.v_active_tagging_run AS
WITH latest AS (
  SELECT activation.*
  FROM nhi_rule_history_terminology.tagging_run_activation activation
  ORDER BY activation.activation_id DESC
  LIMIT 1
)
SELECT run.*
FROM latest
JOIN nhi_rule_history_terminology.tagging_run run
  ON run.tagging_run_id = latest.tagging_run_id
JOIN nhi_rule_history_publication.v_active_publication_run publication
  ON publication.run_id = run.publication_run_id
WHERE run.state = 'sealed';

CREATE OR REPLACE VIEW
  nhi_rule_history_terminology.v_admitted_clause_occurrence AS
SELECT occurrence.*
FROM nhi_rule_history_terminology.clause_occurrence occurrence
JOIN nhi_rule_history_terminology.v_active_tagging_run run
  ON run.tagging_run_id = occurrence.tagging_run_id
WHERE occurrence.occurrence_status = 'admitted';

CREATE OR REPLACE VIEW
  nhi_rule_history_terminology.v_active_concept AS
SELECT concept.*
FROM nhi_rule_history_terminology.run_concept concept
JOIN nhi_rule_history_terminology.v_active_tagging_run run
  ON run.tagging_run_id = concept.tagging_run_id;

CREATE OR REPLACE VIEW
  nhi_rule_history_terminology.v_active_external_code AS
SELECT code.*
FROM nhi_rule_history_terminology.concept_external_code code
JOIN nhi_rule_history_terminology.v_active_tagging_run run
  ON run.tagging_run_id = code.tagging_run_id;

COMMENT ON SCHEMA nhi_rule_history_terminology IS
  'Immutable normalized terminology and exact occurrence projection. '
  'Model aliases remain candidate evidence unless reviewed seed lineage admits '
  'them. Public ICD output is code-only.';

COMMENT ON COLUMN
  nhi_rule_history_terminology.clause_occurrence.start_scalar IS
  'Zero-based Unicode-scalar half-open start offset in exact source block.';

COMMENT ON COLUMN
  nhi_rule_history_terminology.clause_occurrence.start_utf8_byte IS
  'Zero-based UTF-8-byte half-open start offset in exact source block.';

COMMIT;
