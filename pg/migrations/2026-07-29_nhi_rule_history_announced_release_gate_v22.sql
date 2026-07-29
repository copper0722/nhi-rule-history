-- 2026-07-29 — append-only release controls and fail-closed effective-date
-- reconciliation for announced reimbursement-rule patches.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-release-gate-v22', 0)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_announced.release_control_event (
  control_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_announced.release_run(run_id),
  action text NOT NULL CHECK (action IN ('activate', 'deactivate')),
  reason text NOT NULL CHECK (reason <> ''),
  evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
  recorded_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_announced.patch_resolution_event (
  resolution_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL,
  patch_id uuid NOT NULL,
  resolution_state text NOT NULL CHECK (
    resolution_state IN (
      'verified_scheduled', 'effective_unconsolidated', 'reconciled',
      'corrected', 'withdrawn', 'conflicted'
    )
  ),
  reason text NOT NULL CHECK (reason <> ''),
  evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
  recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
  FOREIGN KEY (run_id, patch_id)
    REFERENCES nhi_rule_history_announced.clause_patch(run_id, patch_id)
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_release_control_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_state text;
BEGIN
  SELECT state INTO parent_state
  FROM nhi_rule_history_announced.release_run
  WHERE run_id = NEW.run_id FOR SHARE;
  IF parent_state IS DISTINCT FROM 'sealed' THEN
    RAISE EXCEPTION 'release control requires a sealed release run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_patch_resolution_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
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
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS release_control_event_insert_guard
  ON nhi_rule_history_announced.release_control_event;
CREATE TRIGGER release_control_event_insert_guard
BEFORE INSERT ON nhi_rule_history_announced.release_control_event
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_release_control_insert();

DROP TRIGGER IF EXISTS release_control_event_mutation_guard
  ON nhi_rule_history_announced.release_control_event;
CREATE TRIGGER release_control_event_mutation_guard
BEFORE UPDATE OR DELETE ON nhi_rule_history_announced.release_control_event
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS release_control_event_truncate_guard
  ON nhi_rule_history_announced.release_control_event;
CREATE TRIGGER release_control_event_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.release_control_event
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS patch_resolution_event_insert_guard
  ON nhi_rule_history_announced.patch_resolution_event;
CREATE TRIGGER patch_resolution_event_insert_guard
BEFORE INSERT ON nhi_rule_history_announced.patch_resolution_event
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_patch_resolution_insert();

DROP TRIGGER IF EXISTS patch_resolution_event_mutation_guard
  ON nhi_rule_history_announced.patch_resolution_event;
CREATE TRIGGER patch_resolution_event_mutation_guard
BEFORE UPDATE OR DELETE ON nhi_rule_history_announced.patch_resolution_event
FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_announced.reject_mutation();

DROP TRIGGER IF EXISTS patch_resolution_event_truncate_guard
  ON nhi_rule_history_announced.patch_resolution_event;
CREATE TRIGGER patch_resolution_event_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_announced.patch_resolution_event
FOR EACH STATEMENT EXECUTE FUNCTION
  nhi_rule_history_announced.reject_mutation();

-- Preserve the v21 activation and patch state as append-only bootstrap receipts.
INSERT INTO nhi_rule_history_announced.release_control_event (
  run_id, action, reason, evidence, recorded_at
)
SELECT activation.run_id, 'activate', 'v21 activation receipt imported',
       jsonb_build_object(
         'source_table', 'release_activation',
         'activation_id', activation.activation_id
       ),
       activation.activated_at
FROM nhi_rule_history_announced.release_activation activation
WHERE NOT EXISTS (
  SELECT 1
  FROM nhi_rule_history_announced.release_control_event control
  WHERE control.run_id = activation.run_id
);

INSERT INTO nhi_rule_history_announced.patch_resolution_event (
  run_id, patch_id, resolution_state, reason, evidence
)
SELECT patch.run_id, patch.patch_id, patch.resolution_state,
       'v21 patch resolution receipt imported',
       jsonb_build_object(
         'source_table', 'clause_patch',
         'source_row_sha256', patch.source_row_sha256
       )
FROM nhi_rule_history_announced.clause_patch patch
WHERE NOT EXISTS (
  SELECT 1
  FROM nhi_rule_history_announced.patch_resolution_event event
  WHERE event.run_id = patch.run_id AND event.patch_id = patch.patch_id
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.set_release_control(
    p_run_id uuid,
    p_action text,
    p_reason text,
    p_evidence jsonb DEFAULT '{}'::jsonb
  )
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE inserted_id bigint;
BEGIN
  IF p_action NOT IN ('activate', 'deactivate') THEN
    RAISE EXCEPTION 'invalid release control action';
  END IF;
  INSERT INTO nhi_rule_history_announced.release_control_event (
    run_id, action, reason, evidence
  ) VALUES (
    p_run_id, p_action, p_reason, coalesce(p_evidence, '{}'::jsonb)
  )
  RETURNING control_id INTO inserted_id;
  RETURN inserted_id;
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

CREATE OR REPLACE VIEW nhi_rule_history_announced.v_active_run AS
WITH latest_control AS (
  SELECT control.*
  FROM nhi_rule_history_announced.release_control_event control
  ORDER BY control.control_id DESC
  LIMIT 1
)
SELECT run.*
FROM latest_control control
JOIN nhi_rule_history_announced.release_run run USING (run_id)
WHERE control.action = 'activate';

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_current_patch_resolution AS
SELECT DISTINCT ON (event.run_id, event.patch_id)
  event.run_id,
  event.patch_id,
  event.resolution_id,
  event.resolution_state,
  event.reason AS resolution_reason,
  event.evidence AS resolution_evidence,
  event.recorded_at AS resolution_recorded_at
FROM nhi_rule_history_announced.patch_resolution_event event
ORDER BY event.run_id, event.patch_id, event.resolution_id DESC;

DROP VIEW nhi_rule_history_announced.v_public_decision_model;
DROP VIEW nhi_rule_history_announced.v_public_clause_patch;

CREATE VIEW nhi_rule_history_announced.v_public_clause_patch AS
SELECT
  patch.*,
  resolution.resolution_state AS current_resolution_state,
  resolution.resolution_reason,
  resolution.resolution_evidence,
  resolution.resolution_recorded_at,
  notice.notice_id,
  notice.reference_number,
  notice.title AS notice_title,
  notice.official_url,
  notice.published_on,
  notice.effective_on,
  notice.civil_timezone,
  notice.source_artifact_sha256,
  CASE
    WHEN resolution.resolution_state IN ('corrected','withdrawn','conflicted')
      THEN resolution.resolution_state
    WHEN patch.effective_until IS NOT NULL
         AND current_date >= patch.effective_until THEN 'superseded'
    WHEN current_date < patch.effective_from THEN 'future'
    WHEN resolution.resolution_state = 'reconciled'
      THEN 'effective_reconciled'
    WHEN resolution.resolution_state = 'effective_unconsolidated'
      THEN 'effective_unconsolidated'
    ELSE 'effective_date_reached_unresolved'
  END AS display_lifecycle,
  CASE
    WHEN resolution.resolution_state IN ('corrected','withdrawn','conflicted')
      THEN false
    WHEN patch.effective_until IS NOT NULL
         AND current_date >= patch.effective_until THEN false
    WHEN current_date < patch.effective_from
      THEN resolution.resolution_state = 'verified_scheduled'
    ELSE resolution.resolution_state IN (
      'effective_unconsolidated', 'reconciled'
    )
  END AS decision_aid_available,
  false AS legally_auto_selectable
FROM nhi_rule_history_announced.clause_patch patch
JOIN nhi_rule_history_announced.v_active_run run USING (run_id)
JOIN nhi_rule_history_announced.v_current_patch_resolution resolution
  ON resolution.run_id = patch.run_id
 AND resolution.patch_id = patch.patch_id
JOIN nhi_rule_history_announced.notice_effect effect
  ON effect.run_id=patch.run_id AND effect.effect_id=patch.effect_id
JOIN nhi_rule_history_announced.notice_event notice
  ON notice.run_id=effect.run_id AND notice.notice_id=effect.notice_id;

CREATE VIEW nhi_rule_history_announced.v_public_decision_model AS
SELECT
  model.*,
  patch.current_resolution_state,
  patch.display_lifecycle,
  patch.decision_aid_available
FROM nhi_rule_history_announced.decision_model model
JOIN nhi_rule_history_announced.v_public_clause_patch patch
  ON patch.run_id = model.run_id AND patch.patch_id = model.patch_id
WHERE model.model_status IN ('future_opt_in','current')
  AND patch.decision_aid_available;

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
  IF (
    p_facts->'coronary_artery_disease' = 'false'::jsonb
    AND (
      p_facts->'mi_within_one_year' = 'true'::jsonb
      OR (
        jsonb_typeof(p_facts->'mi_history_count') = 'number'
        AND (p_facts->>'mi_history_count')::numeric >= 1
      )
    )
  ) OR (
    p_facts->'peripheral_artery_disease' = 'false'::jsonb
    AND p_facts->'symptomatic_or_treated_pad' = 'true'::jsonb
  ) THEN
    RETURN jsonb_build_object(
      'outcome','insufficient_information',
      'reason','contradictory_inputs',
      'effective_from',model_row.effective_from
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
