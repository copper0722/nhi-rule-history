-- 2026-07-29 — deterministic complete-clause composition for an official
-- amendment that elides an unchanged remainder.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-composite-v23', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_version (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    patch_id uuid NOT NULL,
    clause_code text NOT NULL CHECK (
      clause_code ~ '^[1-9][0-9]*(?:[.][0-9]+)+$'
    ),
    effective_from date NOT NULL,
    predecessor_publication_run_id uuid NOT NULL,
    predecessor_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    predecessor_source_artifact_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    composition_rule_version text NOT NULL CHECK (
      composition_rule_version <> ''
    ),
    composition_manifest_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    composed_text text NOT NULL CHECK (composed_text <> ''),
    composed_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    amendment_block_count integer NOT NULL CHECK (
      amendment_block_count > 0
    ),
    inherited_block_count integer NOT NULL CHECK (
      inherited_block_count > 0
    ),
    review_status text NOT NULL CHECK (
      review_status = 'deterministic_owner_directed'
    ),
    public_note text NOT NULL CHECK (public_note <> ''),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id),
    UNIQUE (run_id, clause_code, effective_from),
    FOREIGN KEY (run_id, patch_id)
      REFERENCES nhi_rule_history_announced.clause_patch(run_id, patch_id),
    FOREIGN KEY (predecessor_publication_run_id, clause_code)
      REFERENCES nhi_rule_history_publication.current_clause(
        run_id, clause_code
      ),
    CHECK (
      composed_text_sha256 =
        encode(sha256(convert_to(composed_text, 'UTF8')), 'hex')
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_block (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    patch_id uuid NOT NULL,
    clause_code text NOT NULL,
    block_order integer NOT NULL CHECK (block_order >= 0),
    origin_lane text NOT NULL CHECK (
      origin_lane IN ('amendment_exact', 'predecessor_inherited')
    ),
    patch_component_order integer,
    predecessor_publication_run_id uuid,
    predecessor_block_order integer,
    source_artifact_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_block_id text NOT NULL CHECK (source_block_id <> ''),
    block_kind text NOT NULL CHECK (
      block_kind IN ('paragraph', 'table_paragraph')
    ),
    container text NOT NULL CHECK (
      container IN ('flow', 'table_cell')
    ),
    raw_text text NOT NULL,
    raw_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    render_locator jsonb NOT NULL CHECK (
      jsonb_typeof(render_locator) = 'object'
    ),
    inheritance_basis text,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id, block_order),
    FOREIGN KEY (run_id, version_id)
      REFERENCES nhi_rule_history_announced.composed_clause_version(
        run_id, version_id
      ),
    FOREIGN KEY (run_id, patch_id, patch_component_order)
      REFERENCES nhi_rule_history_announced.patch_component(
        run_id, patch_id, component_order
      ),
    FOREIGN KEY (
      predecessor_publication_run_id, clause_code,
      predecessor_block_order
    ) REFERENCES nhi_rule_history_publication.current_clause_block(
      run_id, clause_code, block_order
    ),
    CHECK (
      raw_text_sha256 =
        encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        origin_lane = 'amendment_exact'
        AND patch_component_order IS NOT NULL
        AND predecessor_publication_run_id IS NULL
        AND predecessor_block_order IS NULL
        AND inheritance_basis IS NULL
      )
      OR
      (
        origin_lane = 'predecessor_inherited'
        AND patch_component_order IS NULL
        AND predecessor_publication_run_id IS NOT NULL
        AND predecessor_block_order IS NOT NULL
        AND coalesce(inheritance_basis, '') <> ''
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.reimbursement_product_snapshot (
    run_id uuid NOT NULL,
    nhi_code text NOT NULL CHECK (nhi_code ~ '^[A-Z0-9]{10}$'),
    product_name text NOT NULL,
    ingredient_name text,
    atc_code text,
    snapshot_basis text NOT NULL CHECK (
      snapshot_basis IN (
        'nhi_product_master_snapshot',
        'notice_exact_code_set'
      )
    ),
    source_component_order integer,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, nhi_code),
    FOREIGN KEY (run_id)
      REFERENCES nhi_rule_history_announced.release_run(run_id),
    CHECK (
      (
        snapshot_basis = 'notice_exact_code_set'
        AND source_component_order IS NOT NULL
      )
      OR snapshot_basis = 'nhi_product_master_snapshot'
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_reimbursement_code (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    nhi_code text NOT NULL,
    applicability_lane text NOT NULL CHECK (
      applicability_lane IN ('table1_default', 'table2_exception')
    ),
    link_basis text NOT NULL CHECK (
      link_basis IN (
        'nhi_product_master_c10_minus_notice_exceptions',
        'notice_exact_code_set'
      )
    ),
    source_component_order integer,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id, nhi_code),
    FOREIGN KEY (run_id, version_id)
      REFERENCES nhi_rule_history_announced.composed_clause_version(
        run_id, version_id
      ),
    FOREIGN KEY (run_id, nhi_code)
      REFERENCES nhi_rule_history_announced.reimbursement_product_snapshot(
        run_id, nhi_code
      ),
    CHECK (
      (
        applicability_lane = 'table2_exception'
        AND link_basis = 'notice_exact_code_set'
        AND source_component_order IS NOT NULL
      )
      OR
      (
        applicability_lane = 'table1_default'
        AND link_basis =
          'nhi_product_master_c10_minus_notice_exceptions'
        AND source_component_order IS NULL
      )
    )
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_run_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  actual_counts jsonb;
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
    'notice_event', (
      SELECT count(*) FROM nhi_rule_history_announced.notice_event
      WHERE run_id=OLD.run_id
    ),
    'notice_effect', (
      SELECT count(*) FROM nhi_rule_history_announced.notice_effect
      WHERE run_id=OLD.run_id
    ),
    'clause_patch', (
      SELECT count(*) FROM nhi_rule_history_announced.clause_patch
      WHERE run_id=OLD.run_id
    ),
    'patch_component', (
      SELECT count(*) FROM nhi_rule_history_announced.patch_component
      WHERE run_id=OLD.run_id
    ),
    'composed_clause_version', (
      SELECT count(*)
      FROM nhi_rule_history_announced.composed_clause_version
      WHERE run_id=OLD.run_id
    ),
    'composed_clause_block', (
      SELECT count(*)
      FROM nhi_rule_history_announced.composed_clause_block
      WHERE run_id=OLD.run_id
    ),
    'reimbursement_product_snapshot', (
      SELECT count(*)
      FROM nhi_rule_history_announced.reimbursement_product_snapshot
      WHERE run_id=OLD.run_id
    ),
    'composed_clause_reimbursement_code', (
      SELECT count(*)
      FROM
        nhi_rule_history_announced.composed_clause_reimbursement_code
      WHERE run_id=OLD.run_id
    ),
    'decision_model', (
      SELECT count(*) FROM nhi_rule_history_announced.decision_model
      WHERE run_id=OLD.run_id
    ),
    'decision_input', (
      SELECT count(*) FROM nhi_rule_history_announced.decision_input
      WHERE run_id=OLD.run_id
    ),
    'risk_category', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_category
      WHERE run_id=OLD.run_id
    ),
    'risk_branch', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_branch
      WHERE run_id=OLD.run_id
    ),
    'risk_predicate', (
      SELECT count(*) FROM nhi_rule_history_announced.risk_predicate
      WHERE run_id=OLD.run_id
    ),
    'model_product_code', (
      SELECT count(*) FROM nhi_rule_history_announced.model_product_code
      WHERE run_id=OLD.run_id
    )
  ) INTO actual_counts;
  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'announced-rule seal counts do not match child rows';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_patch patch
    WHERE patch.run_id = OLD.run_id
      AND patch.composition_status = 'reviewed_composite'
      AND NOT EXISTS (
        SELECT 1
        FROM nhi_rule_history_announced.composed_clause_version version
        WHERE version.run_id = patch.run_id
          AND version.patch_id = patch.patch_id
      )
  ) THEN
    RAISE EXCEPTION 'reviewed composite patch is missing its clause version';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.composed_clause_version version
    LEFT JOIN LATERAL (
      SELECT
        count(*) FILTER (
          WHERE block.origin_lane = 'amendment_exact'
        ) AS amendment_count,
        count(*) FILTER (
          WHERE block.origin_lane = 'predecessor_inherited'
        ) AS inherited_count,
        count(*) AS total_count,
        min(block.block_order) AS first_order,
        max(block.block_order) AS last_order
      FROM nhi_rule_history_announced.composed_clause_block block
      WHERE block.run_id = version.run_id
        AND block.version_id = version.version_id
    ) counts ON true
    WHERE version.run_id = OLD.run_id
      AND (
        counts.amendment_count <> version.amendment_block_count
        OR counts.inherited_count <> version.inherited_block_count
        OR counts.first_order <> 0
        OR counts.last_order <> counts.total_count - 1
      )
  ) THEN
    RAISE EXCEPTION 'composed clause block coverage is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.composed_clause_version version
    LEFT JOIN LATERAL (
      SELECT
        count(*) FILTER (
          WHERE link.applicability_lane = 'table1_default'
        ) AS table1_count,
        count(*) FILTER (
          WHERE link.applicability_lane = 'table2_exception'
        ) AS table2_count,
        count(*) AS total_count
      FROM
        nhi_rule_history_announced.composed_clause_reimbursement_code link
      WHERE link.run_id = version.run_id
        AND link.version_id = version.version_id
    ) counts ON true
    WHERE version.run_id = OLD.run_id
      AND (
        counts.table2_count <> 116
        OR counts.total_count = 0
        OR counts.table1_count + counts.table2_count <> counts.total_count
      )
  ) THEN
    RAISE EXCEPTION 'composed clause reimbursement-code coverage is invalid';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'composed_clause_version', 'composed_clause_block',
    'reimbursement_product_snapshot',
    'composed_clause_reimbursement_code'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_dml_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_dml_guard BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.guard_child_insert()',
      table_name, table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_announced.%I',
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

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_public_composed_clause_version AS
SELECT version.*
FROM nhi_rule_history_announced.composed_clause_version version
JOIN nhi_rule_history_announced.v_public_clause_patch patch
  ON patch.run_id = version.run_id
 AND patch.patch_id = version.patch_id
WHERE patch.composition_status = 'reviewed_composite';

COMMIT;
