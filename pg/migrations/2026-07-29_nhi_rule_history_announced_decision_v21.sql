-- 2026-07-29 — official notice events, source-exact clause patches, and
-- version-bound deterministic decision models.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-decision-v21', 0)
);

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_announced;

CREATE DOMAIN nhi_rule_history_announced.sha256_hex AS text
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE nhi_rule_history_announced.release_run (
  run_id uuid PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  loader_version text NOT NULL,
  evaluator_version text NOT NULL,
  source_artifact_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  input_fingerprint nhi_rule_history_announced.sha256_hex NOT NULL UNIQUE,
  expected_counts jsonb NOT NULL CHECK (jsonb_typeof(expected_counts) = 'object'),
  verified_counts jsonb CHECK (
    verified_counts IS NULL OR jsonb_typeof(verified_counts) = 'object'
  ),
  table_fingerprints jsonb CHECK (
    table_fingerprints IS NULL OR jsonb_typeof(table_fingerprints) = 'object'
  ),
  output_fingerprint nhi_rule_history_announced.sha256_hex,
  sealed_fingerprint nhi_rule_history_announced.sha256_hex UNIQUE,
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  CHECK (
    (state = 'loading' AND verified_counts IS NULL
      AND table_fingerprints IS NULL
      AND output_fingerprint IS NULL AND sealed_fingerprint IS NULL
      AND sealed_at IS NULL)
    OR
    (state = 'sealed' AND verified_counts IS NOT NULL
      AND table_fingerprints IS NOT NULL
      AND output_fingerprint IS NOT NULL AND sealed_fingerprint IS NOT NULL
      AND sealed_at IS NOT NULL)
  )
);

CREATE TABLE nhi_rule_history_announced.notice_event (
  run_id uuid NOT NULL REFERENCES nhi_rule_history_announced.release_run(run_id),
  notice_id uuid NOT NULL,
  reference_number text NOT NULL CHECK (reference_number <> ''),
  title text NOT NULL CHECK (title <> ''),
  official_url text NOT NULL CHECK (official_url ~ '^https://'),
  published_on date NOT NULL,
  effective_on date NOT NULL,
  civil_timezone text NOT NULL CHECK (civil_timezone = 'Asia/Taipei'),
  source_artifact_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  source_artifact_filename text NOT NULL CHECK (source_artifact_filename <> ''),
  source_exact boolean NOT NULL CHECK (source_exact),
  event_scope_complete boolean NOT NULL,
  unresolved_scope jsonb NOT NULL CHECK (jsonb_typeof(unresolved_scope) = 'array'),
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, notice_id),
  UNIQUE (run_id, reference_number)
);

CREATE TABLE nhi_rule_history_announced.notice_effect (
  run_id uuid NOT NULL,
  effect_id uuid NOT NULL,
  notice_id uuid NOT NULL,
  effect_type text NOT NULL CHECK (
    effect_type IN ('clause_amendment', 'reimbursed_item_change')
  ),
  clause_code text,
  projection_status text NOT NULL CHECK (
    projection_status IN ('projected_source_exact_patch', 'pending_projection')
  ),
  scope_note text NOT NULL CHECK (scope_note <> ''),
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, effect_id),
  FOREIGN KEY (run_id, notice_id)
    REFERENCES nhi_rule_history_announced.notice_event(run_id, notice_id)
);

CREATE TABLE nhi_rule_history_announced.clause_patch (
  run_id uuid NOT NULL,
  patch_id uuid NOT NULL,
  effect_id uuid NOT NULL,
  clause_code text NOT NULL CHECK (
    clause_code ~ '^[1-9][0-9]*(?:[.][0-9]+)+$'
  ),
  predecessor_text_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  effective_from date NOT NULL,
  effective_until date,
  resolution_state text NOT NULL CHECK (
    resolution_state IN (
      'verified_scheduled', 'effective_unconsolidated', 'reconciled',
      'corrected', 'withdrawn', 'conflicted'
    )
  ),
  source_exact_patch_text text NOT NULL CHECK (source_exact_patch_text <> ''),
  source_exact_patch_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  omitted_text_present boolean NOT NULL,
  composition_status text NOT NULL CHECK (
    composition_status IN ('patch_only', 'reviewed_composite')
  ),
  comparison_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  component_manifest_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  partial_event_projection boolean NOT NULL,
  unprocessed_event_scope jsonb NOT NULL CHECK (
    jsonb_typeof(unprocessed_event_scope) = 'array'
  ),
  public_note text NOT NULL CHECK (public_note <> ''),
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, patch_id),
  UNIQUE (run_id, clause_code, effective_from),
  FOREIGN KEY (run_id, effect_id)
    REFERENCES nhi_rule_history_announced.notice_effect(run_id, effect_id),
  CHECK (
    effective_until IS NULL OR effective_until > effective_from
  ),
  CHECK (
    source_exact_patch_sha256 =
      encode(sha256(convert_to(source_exact_patch_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE nhi_rule_history_announced.patch_component (
  run_id uuid NOT NULL,
  patch_id uuid NOT NULL,
  component_order integer NOT NULL CHECK (component_order >= 0),
  component_role text NOT NULL CHECK (
    component_role IN (
      'clause_heading', 'applicability', 'table2_code_set',
      'table1_heading', 'table1_matrix', 'table2_heading', 'risk_definition',
      'risk_factor_definition', 'assessment_note', 'secondary_target',
      'omitted_remainder_marker'
    )
  ),
  source_block_id text NOT NULL CHECK (source_block_id <> ''),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object' AND source_locator <> '{}'::jsonb
  ),
  raw_text text NOT NULL CHECK (raw_text <> ''),
  raw_text_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, patch_id, component_order),
  FOREIGN KEY (run_id, patch_id)
    REFERENCES nhi_rule_history_announced.clause_patch(run_id, patch_id),
  CHECK (
    raw_text_sha256 =
      encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE nhi_rule_history_announced.decision_model (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  patch_id uuid NOT NULL,
  model_key text NOT NULL CHECK (model_key <> ''),
  title text NOT NULL CHECK (title <> ''),
  scope_label text NOT NULL CHECK (
    scope_label = '表一 LDL-C 起始治療門檻檢查'
  ),
  model_status text NOT NULL CHECK (
    model_status IN ('future_opt_in', 'current', 'retired', 'blocked')
  ),
  effective_from date NOT NULL,
  effective_until date,
  evaluator_version text NOT NULL,
  predicate_set_fingerprint nhi_rule_history_announced.sha256_hex NOT NULL,
  product_set_fingerprint nhi_rule_history_announced.sha256_hex NOT NULL,
  outcome_codes jsonb NOT NULL CHECK (
    outcome_codes = '[
      "table1_threshold_met",
      "table1_threshold_not_met",
      "requires_table2_assessment",
      "insufficient_information"
    ]'::jsonb
  ),
  explanation_disclaimer text NOT NULL CHECK (explanation_disclaimer <> ''),
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, model_id),
  UNIQUE (run_id, model_key),
  FOREIGN KEY (run_id, patch_id)
    REFERENCES nhi_rule_history_announced.clause_patch(run_id, patch_id),
  CHECK (effective_until IS NULL OR effective_until > effective_from)
);

CREATE TABLE nhi_rule_history_announced.decision_input (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  input_key text NOT NULL CHECK (input_key ~ '^[a-z][a-z0-9_]*$'),
  label text NOT NULL CHECK (label <> ''),
  help_text text NOT NULL,
  control_type text NOT NULL CHECK (
    control_type IN ('tri_state', 'number', 'product_code')
  ),
  unit text,
  min_value numeric,
  max_value numeric,
  display_group text NOT NULL CHECK (display_group <> ''),
  display_order integer NOT NULL CHECK (display_order >= 0),
  source_component_order integer,
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, model_id, input_key),
  FOREIGN KEY (run_id, model_id)
    REFERENCES nhi_rule_history_announced.decision_model(run_id, model_id)
);

-- Model component references are checked by the deterministic loader against
-- the model's patch. They cannot be represented as a direct three-column FK
-- because model_id and patch_id are deliberately distinct identities.

CREATE TABLE nhi_rule_history_announced.risk_category (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  category_key text NOT NULL CHECK (category_key <> ''),
  label text NOT NULL CHECK (label <> ''),
  priority integer NOT NULL CHECK (priority >= 1),
  ldl_threshold_mg_dl numeric NOT NULL CHECK (ldl_threshold_mg_dl > 0),
  source_component_order integer NOT NULL,
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, model_id, category_key),
  UNIQUE (run_id, model_id, priority),
  FOREIGN KEY (run_id, model_id)
    REFERENCES nhi_rule_history_announced.decision_model(run_id, model_id)
);

CREATE TABLE nhi_rule_history_announced.risk_branch (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  category_key text NOT NULL,
  branch_key text NOT NULL CHECK (branch_key <> ''),
  branch_order integer NOT NULL CHECK (branch_order >= 0),
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, model_id, category_key, branch_key),
  UNIQUE (run_id, model_id, category_key, branch_order),
  FOREIGN KEY (run_id, model_id, category_key)
    REFERENCES nhi_rule_history_announced.risk_category
      (run_id, model_id, category_key)
);

CREATE TABLE nhi_rule_history_announced.risk_predicate (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  category_key text NOT NULL,
  branch_key text NOT NULL,
  predicate_order integer NOT NULL CHECK (predicate_order >= 0),
  input_key text,
  operator text NOT NULL CHECK (
    operator IN ('is_true', 'is_false', 'gte', 'lt', 'aggregate_gte', 'aggregate_eq')
  ),
  operand jsonb NOT NULL,
  source_component_order integer NOT NULL,
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (
    run_id, model_id, category_key, branch_key, predicate_order
  ),
  FOREIGN KEY (run_id, model_id, category_key, branch_key)
    REFERENCES nhi_rule_history_announced.risk_branch
      (run_id, model_id, category_key, branch_key),
  FOREIGN KEY (run_id, model_id, input_key)
    REFERENCES nhi_rule_history_announced.decision_input
      (run_id, model_id, input_key),
  CHECK (
    (operator IN ('aggregate_gte', 'aggregate_eq') AND input_key IS NULL)
    OR
    (operator NOT IN ('aggregate_gte', 'aggregate_eq') AND input_key IS NOT NULL)
  )
);

CREATE TABLE nhi_rule_history_announced.model_product_code (
  run_id uuid NOT NULL,
  model_id uuid NOT NULL,
  nhi_code text NOT NULL CHECK (nhi_code ~ '^[A-Z0-9]{10}$'),
  product_name text NOT NULL,
  ingredient_name text,
  atc_code text,
  rule_lane text NOT NULL CHECK (rule_lane IN ('table1', 'table2')),
  membership_source text NOT NULL CHECK (
    membership_source IN ('notice_exact_code_set', 'nhi_product_master_snapshot')
  ),
  source_component_order integer,
  source_row_sha256 nhi_rule_history_announced.sha256_hex NOT NULL,
  PRIMARY KEY (run_id, model_id, nhi_code),
  FOREIGN KEY (run_id, model_id)
    REFERENCES nhi_rule_history_announced.decision_model(run_id, model_id)
);

CREATE TABLE nhi_rule_history_announced.release_activation (
  activation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_announced.release_run(run_id),
  activated_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE OR REPLACE FUNCTION nhi_rule_history_announced.reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'announced-rule evidence is append-only: %.%',
    TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION nhi_rule_history_announced.guard_child_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_state text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'announced-rule child rows are append-only';
  END IF;
  SELECT state INTO parent_state
  FROM nhi_rule_history_announced.release_run
  WHERE run_id = NEW.run_id FOR SHARE;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION 'child insert requires a loading release run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION nhi_rule_history_announced.guard_run_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.state <> 'loading' THEN
    RAISE EXCEPTION 'release runs must be inserted in loading state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION nhi_rule_history_announced.guard_run_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual_counts jsonb;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed announced-rule runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'release run permits only loading to sealed';
  END IF;
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.loader_version IS DISTINCT FROM OLD.loader_version
     OR NEW.evaluator_version IS DISTINCT FROM OLD.evaluator_version
     OR NEW.source_artifact_sha256 IS DISTINCT FROM OLD.source_artifact_sha256
     OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
     OR NEW.expected_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'release run identity and inputs are immutable';
  END IF;
  SELECT jsonb_build_object(
    'notice_event', (SELECT count(*) FROM nhi_rule_history_announced.notice_event WHERE run_id=OLD.run_id),
    'notice_effect', (SELECT count(*) FROM nhi_rule_history_announced.notice_effect WHERE run_id=OLD.run_id),
    'clause_patch', (SELECT count(*) FROM nhi_rule_history_announced.clause_patch WHERE run_id=OLD.run_id),
    'patch_component', (SELECT count(*) FROM nhi_rule_history_announced.patch_component WHERE run_id=OLD.run_id),
    'decision_model', (SELECT count(*) FROM nhi_rule_history_announced.decision_model WHERE run_id=OLD.run_id),
    'decision_input', (SELECT count(*) FROM nhi_rule_history_announced.decision_input WHERE run_id=OLD.run_id),
    'risk_category', (SELECT count(*) FROM nhi_rule_history_announced.risk_category WHERE run_id=OLD.run_id),
    'risk_branch', (SELECT count(*) FROM nhi_rule_history_announced.risk_branch WHERE run_id=OLD.run_id),
    'risk_predicate', (SELECT count(*) FROM nhi_rule_history_announced.risk_predicate WHERE run_id=OLD.run_id),
    'model_product_code', (SELECT count(*) FROM nhi_rule_history_announced.model_product_code WHERE run_id=OLD.run_id)
  ) INTO actual_counts;
  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'announced-rule seal counts do not match child rows';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION nhi_rule_history_announced.guard_activation_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_state text;
BEGIN
  SELECT state INTO parent_state
  FROM nhi_rule_history_announced.release_run
  WHERE run_id = NEW.run_id FOR SHARE;
  IF parent_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'only a sealed announced-rule run can be activated';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER release_run_insert_guard
BEFORE INSERT ON nhi_rule_history_announced.release_run
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.guard_run_insert();

CREATE TRIGGER release_run_update_guard
BEFORE UPDATE ON nhi_rule_history_announced.release_run
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.guard_run_update();

CREATE TRIGGER release_run_delete_guard
BEFORE DELETE ON nhi_rule_history_announced.release_run
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

CREATE TRIGGER release_run_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.release_run
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'notice_event','notice_effect','clause_patch','patch_component',
    'decision_model','decision_input','risk_category','risk_branch',
    'risk_predicate','model_product_code'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_dml_guard BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.guard_child_insert()',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_announced.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_announced.reject_mutation()',
      table_name, table_name
    );
  END LOOP;
END;
$$;

CREATE TRIGGER release_activation_insert_guard
BEFORE INSERT ON nhi_rule_history_announced.release_activation
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_activation_insert();

CREATE TRIGGER release_activation_mutation_guard
BEFORE UPDATE OR DELETE ON nhi_rule_history_announced.release_activation
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

CREATE TRIGGER release_activation_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.release_activation
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

CREATE OR REPLACE VIEW nhi_rule_history_announced.v_active_run AS
SELECT run.*
FROM nhi_rule_history_announced.release_activation activation
JOIN nhi_rule_history_announced.release_run run USING (run_id)
ORDER BY activation.activation_id DESC
LIMIT 1;

CREATE OR REPLACE VIEW nhi_rule_history_announced.v_public_clause_patch AS
SELECT
  patch.*,
  notice.notice_id,
  notice.reference_number,
  notice.title AS notice_title,
  notice.official_url,
  notice.published_on,
  notice.effective_on,
  notice.civil_timezone,
  notice.source_artifact_sha256,
  CASE
    WHEN patch.resolution_state IN ('corrected','withdrawn','conflicted')
      THEN patch.resolution_state
    WHEN current_date < patch.effective_from THEN 'future'
    WHEN patch.effective_until IS NOT NULL
         AND current_date >= patch.effective_until THEN 'superseded'
    ELSE 'effective'
  END AS display_lifecycle,
  false AS legally_auto_selectable
FROM nhi_rule_history_announced.clause_patch patch
JOIN nhi_rule_history_announced.v_active_run run USING (run_id)
JOIN nhi_rule_history_announced.notice_effect effect
  ON effect.run_id=patch.run_id AND effect.effect_id=patch.effect_id
JOIN nhi_rule_history_announced.notice_event notice
  ON notice.run_id=effect.run_id AND notice.notice_id=effect.notice_id;

CREATE OR REPLACE VIEW nhi_rule_history_announced.v_public_decision_model AS
SELECT model.*
FROM nhi_rule_history_announced.decision_model model
JOIN nhi_rule_history_announced.v_active_run run USING (run_id)
WHERE model.model_status IN ('future_opt_in','current');

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.evaluate_predicate_v1(
    p_operator text,
    p_input_key text,
    p_operand jsonb,
    p_facts jsonb
  )
RETURNS smallint
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  value_json jsonb;
  value_text text;
  value_number numeric;
  member jsonb;
  derived jsonb;
  member_state smallint;
  true_count integer := 0;
  unknown_count integer := 0;
  derived_true integer;
  derived_unknown integer;
  minimum_count integer;
  target_count integer;
BEGIN
  IF p_operator IN ('aggregate_gte','aggregate_eq') THEN
    FOR member IN SELECT value FROM jsonb_array_elements(p_operand->'members')
    LOOP
      IF NOT (p_facts ? (member #>> '{}'))
         OR p_facts->(member #>> '{}') IS NULL
         OR p_facts->(member #>> '{}') = 'null'::jsonb THEN
        unknown_count := unknown_count + 1;
      ELSIF jsonb_typeof(p_facts->(member #>> '{}')) = 'boolean'
            AND (p_facts->>(member #>> '{}'))::boolean THEN
        true_count := true_count + 1;
      ELSIF jsonb_typeof(p_facts->(member #>> '{}')) <> 'boolean' THEN
        unknown_count := unknown_count + 1;
      END IF;
    END LOOP;
    FOR derived IN
      SELECT value FROM jsonb_array_elements(
        coalesce(p_operand->'derived_members','[]'::jsonb)
      )
    LOOP
      derived_true := 0;
      derived_unknown := 0;
      minimum_count := (derived->>'minimum')::integer;
      FOR member IN SELECT value FROM jsonb_array_elements(derived->'members')
      LOOP
        IF NOT (p_facts ? (member #>> '{}'))
           OR p_facts->(member #>> '{}') IS NULL
           OR p_facts->(member #>> '{}') = 'null'::jsonb THEN
          derived_unknown := derived_unknown + 1;
        ELSIF jsonb_typeof(p_facts->(member #>> '{}')) = 'boolean'
              AND (p_facts->>(member #>> '{}'))::boolean THEN
          derived_true := derived_true + 1;
        ELSIF jsonb_typeof(p_facts->(member #>> '{}')) <> 'boolean' THEN
          derived_unknown := derived_unknown + 1;
        END IF;
      END LOOP;
      IF derived_true >= minimum_count THEN
        true_count := true_count + 1;
      ELSIF derived_true + derived_unknown >= minimum_count THEN
        unknown_count := unknown_count + 1;
      END IF;
    END LOOP;
    target_count := (p_operand->>'target')::integer;
    IF p_operator = 'aggregate_gte' THEN
      IF true_count >= target_count THEN RETURN 1; END IF;
      IF true_count + unknown_count < target_count THEN RETURN 0; END IF;
      RETURN -1;
    END IF;
    IF unknown_count = 0 AND true_count = target_count THEN RETURN 1; END IF;
    IF target_count < true_count OR target_count > true_count + unknown_count
      THEN RETURN 0;
    END IF;
    RETURN -1;
  END IF;

  IF p_input_key IS NULL OR NOT (p_facts ? p_input_key)
     OR p_facts->p_input_key IS NULL
     OR p_facts->p_input_key = 'null'::jsonb THEN
    RETURN -1;
  END IF;
  value_json := p_facts->p_input_key;
  value_text := lower(coalesce(p_facts->>p_input_key,''));
  IF value_text IN ('unknown','') THEN RETURN -1; END IF;
  IF p_operator IN ('is_true','is_false') THEN
    IF jsonb_typeof(value_json) <> 'boolean' THEN RETURN -1; END IF;
    IF p_operator = 'is_true' THEN
      RETURN CASE WHEN value_text::boolean THEN 1 ELSE 0 END;
    END IF;
    RETURN CASE WHEN value_text::boolean THEN 0 ELSE 1 END;
  END IF;
  IF jsonb_typeof(value_json) <> 'number' THEN RETURN -1; END IF;
  value_number := value_text::numeric;
  IF p_operator = 'gte' THEN
    RETURN CASE WHEN value_number >= (p_operand #>> '{}')::numeric
      THEN 1 ELSE 0 END;
  END IF;
  IF p_operator = 'lt' THEN
    RETURN CASE WHEN value_number < (p_operand #>> '{}')::numeric
      THEN 1 ELSE 0 END;
  END IF;
  RETURN -1;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.evaluate_table1_v1(
    p_model_id uuid,
    p_facts jsonb
  )
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  model_row record;
  product_row record;
  category_row record;
  branch_row record;
  predicate_row record;
  branch_state smallint;
  predicate_state smallint;
  category_true boolean;
  category_unknown boolean;
  selected_category text;
  selected_label text;
  selected_threshold numeric;
  selected_branch text;
  ldl_value numeric;
  product_code text;
BEGIN
  SELECT model.* INTO model_row
  FROM nhi_rule_history_announced.v_public_decision_model model
  WHERE model.model_id = p_model_id;
  IF NOT FOUND THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','model_unavailable'
    );
  END IF;
  product_code := upper(trim(coalesce(p_facts->>'product_code','')));
  IF product_code = '' THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','product_code_missing',
      'effective_from',model_row.effective_from
    );
  END IF;
  SELECT product.* INTO product_row
  FROM nhi_rule_history_announced.model_product_code product
  WHERE product.run_id=model_row.run_id
    AND product.model_id=model_row.model_id
    AND product.nhi_code=product_code;
  IF NOT FOUND THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','product_code_unknown',
      'product_code',product_code,
      'effective_from',model_row.effective_from
    );
  END IF;
  IF product_row.rule_lane='table2' THEN
    RETURN jsonb_build_object(
      'outcome','requires_table2_assessment',
      'reason','notice_exact_table2_code',
      'product_code',product_code,
      'product_name',product_row.product_name,
      'effective_from',model_row.effective_from
    );
  END IF;
  IF NOT (p_facts ? 'ldl_c_mg_dl')
     OR jsonb_typeof(p_facts->'ldl_c_mg_dl') <> 'number' THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','ldl_value_missing',
      'effective_from',model_row.effective_from
    );
  END IF;
  ldl_value := (p_facts->>'ldl_c_mg_dl')::numeric;
  IF ldl_value < 0 THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','ldl_value_invalid',
      'effective_from',model_row.effective_from
    );
  END IF;

  FOR category_row IN
    SELECT category.*
    FROM nhi_rule_history_announced.risk_category category
    WHERE category.run_id=model_row.run_id
      AND category.model_id=model_row.model_id
    ORDER BY category.priority
  LOOP
    category_true := false;
    category_unknown := false;
    FOR branch_row IN
      SELECT branch.*
      FROM nhi_rule_history_announced.risk_branch branch
      WHERE branch.run_id=model_row.run_id
        AND branch.model_id=model_row.model_id
        AND branch.category_key=category_row.category_key
      ORDER BY branch.branch_order
    LOOP
      branch_state := 1;
      FOR predicate_row IN
        SELECT predicate.*
        FROM nhi_rule_history_announced.risk_predicate predicate
        WHERE predicate.run_id=model_row.run_id
          AND predicate.model_id=model_row.model_id
          AND predicate.category_key=branch_row.category_key
          AND predicate.branch_key=branch_row.branch_key
        ORDER BY predicate.predicate_order
      LOOP
        predicate_state :=
          nhi_rule_history_announced.evaluate_predicate_v1(
            predicate_row.operator,
            predicate_row.input_key,
            predicate_row.operand,
            p_facts
          );
        IF predicate_state = 0 THEN
          branch_state := 0;
          EXIT;
        ELSIF predicate_state = -1 THEN
          branch_state := -1;
        END IF;
      END LOOP;
      IF branch_state = 1 THEN
        category_true := true;
        selected_branch := branch_row.branch_key;
        EXIT;
      ELSIF branch_state = -1 THEN
        category_unknown := true;
      END IF;
    END LOOP;
    IF category_true THEN
      selected_category := category_row.category_key;
      selected_label := category_row.label;
      selected_threshold := category_row.ldl_threshold_mg_dl;
      EXIT;
    ELSIF category_unknown THEN
      RETURN jsonb_build_object(
        'outcome','insufficient_information',
        'reason','higher_priority_path_unknown',
        'blocked_at_category',category_row.category_key,
        'effective_from',model_row.effective_from
      );
    END IF;
  END LOOP;
  IF selected_category IS NULL THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','risk_category_unresolved',
      'effective_from',model_row.effective_from
    );
  END IF;
  RETURN jsonb_build_object(
    'outcome',CASE WHEN ldl_value >= selected_threshold
      THEN 'table1_threshold_met'
      ELSE 'table1_threshold_not_met' END,
    'category_key',selected_category,
    'category_label',selected_label,
    'matched_branch',selected_branch,
    'ldl_c_mg_dl',ldl_value,
    'threshold_mg_dl',selected_threshold,
    'product_code',product_code,
    'effective_from',model_row.effective_from
  );
END;
$$;

COMMIT;
