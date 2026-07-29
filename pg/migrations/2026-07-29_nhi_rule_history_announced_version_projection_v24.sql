-- 2026-07-29 — version-independent terminology and direct-predecessor diff.
--
-- Every announced complete clause version is scanned with the same reviewed
-- terminology run as the current publication.  Its direct predecessor diff is
-- also sealed here.  Frontends consume these rows; they do not run a second
-- highlighting or diff policy.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-announced-version-projection-v24', 0)
);

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_tagging_block_input (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    block_order integer NOT NULL CHECK (block_order >= 0),
    terminology_tagging_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_terminology.tagging_run(tagging_run_id)
      ON DELETE RESTRICT,
    source_block_id text NOT NULL CHECK (source_block_id <> ''),
    source_block_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    matcher_version text NOT NULL CHECK (matcher_version <> ''),
    offset_contract text NOT NULL CHECK (
      offset_contract =
        'unicode_scalar_half_open+utf8_byte_half_open/v1'
    ),
    alias_admission_policy text NOT NULL CHECK (
      alias_admission_policy = 'reviewed_source_observed_only/v1'
    ),
    scan_status text NOT NULL CHECK (
      scan_status IN ('scanned_no_match', 'scanned_with_match')
    ),
    candidate_match_count integer NOT NULL CHECK (
      candidate_match_count >= 0
    ),
    admitted_match_count integer NOT NULL CHECK (
      admitted_match_count >= 0
    ),
    blocked_match_count integer NOT NULL CHECK (
      blocked_match_count >= 0
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id, block_order),
    FOREIGN KEY (run_id, version_id, block_order)
      REFERENCES nhi_rule_history_announced.composed_clause_block(
        run_id, version_id, block_order
      ),
    CHECK (
      (
        scan_status = 'scanned_no_match'
        AND candidate_match_count = 0
        AND admitted_match_count = 0
        AND blocked_match_count = 0
      )
      OR
      (
        scan_status = 'scanned_with_match'
        AND candidate_match_count
          + admitted_match_count
          + blocked_match_count > 0
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_terminology_occurrence (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    occurrence_id uuid NOT NULL,
    clause_code text NOT NULL,
    block_order integer NOT NULL CHECK (block_order >= 0),
    terminology_tagging_run_id uuid NOT NULL,
    source_block_id text NOT NULL CHECK (source_block_id <> ''),
    source_block_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    concept_id uuid NOT NULL,
    alias_id uuid NOT NULL,
    start_scalar integer NOT NULL CHECK (start_scalar >= 0),
    end_scalar integer NOT NULL CHECK (end_scalar > start_scalar),
    start_utf8_byte integer NOT NULL CHECK (start_utf8_byte >= 0),
    end_utf8_byte integer NOT NULL CHECK (
      end_utf8_byte > start_utf8_byte
    ),
    matched_text text NOT NULL CHECK (matched_text <> ''),
    matched_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    occurrence_status text NOT NULL CHECK (
      occurrence_status IN ('admitted', 'candidate', 'blocked')
    ),
    occurrence_reason text NOT NULL CHECK (
      occurrence_reason IN (
        'reviewed_alias_longest_match', 'alias_candidate', 'alias_blocked',
        'same_span_cross_concept', 'overlap_lost'
      )
    ),
    match_rule text NOT NULL CHECK (
      match_rule IN ('exact', 'case_insensitive_token', 'context_required')
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id, occurrence_id),
    FOREIGN KEY (run_id, version_id, block_order)
      REFERENCES
        nhi_rule_history_announced.composed_clause_tagging_block_input(
          run_id, version_id, block_order
        ),
    FOREIGN KEY (terminology_tagging_run_id, concept_id)
      REFERENCES nhi_rule_history_terminology.run_concept(
        tagging_run_id, concept_id
      ),
    FOREIGN KEY (terminology_tagging_run_id, alias_id, concept_id)
      REFERENCES nhi_rule_history_terminology.concept_alias(
        tagging_run_id, alias_id, concept_id
      ),
    CHECK (
      matched_text_sha256 =
        encode(sha256(convert_to(matched_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        occurrence_status = 'admitted'
        AND occurrence_reason = 'reviewed_alias_longest_match'
        AND match_rule <> 'context_required'
      )
      OR occurrence_status <> 'admitted'
    )
  );

CREATE INDEX IF NOT EXISTS
  announced_composed_occurrence_source_idx
ON nhi_rule_history_announced.composed_clause_terminology_occurrence (
  run_id, version_id, block_order, start_scalar
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'announced_composed_admitted_occurrence_no_overlap'
      AND conrelid =
        'nhi_rule_history_announced.'
        'composed_clause_terminology_occurrence'::regclass
  ) THEN
    ALTER TABLE
      nhi_rule_history_announced.composed_clause_terminology_occurrence
    ADD CONSTRAINT announced_composed_admitted_occurrence_no_overlap
    EXCLUDE USING gist (
      run_id WITH =,
      version_id WITH =,
      block_order WITH =,
      int4range(start_scalar, end_scalar, '[)') WITH &&
    ) WHERE (occurrence_status = 'admitted');
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.composed_clause_diff_hunk (
    run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    hunk_id uuid NOT NULL,
    clause_code text NOT NULL,
    predecessor_publication_run_id uuid NOT NULL,
    predecessor_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    hunk_order integer NOT NULL CHECK (hunk_order >= 0),
    semantic_change_kind text NOT NULL CHECK (
      semantic_change_kind IN ('added', 'removed', 'replaced')
    ),
    display_note text NOT NULL CHECK (
      display_note IN ('本版新增', '本版刪除', '本版改寫')
    ),
    old_block_start integer,
    old_block_end integer,
    new_block_start integer,
    new_block_end integer,
    old_text text,
    new_text text,
    old_text_sha256
      nhi_rule_history_announced.sha256_hex,
    new_text_sha256
      nhi_rule_history_announced.sha256_hex,
    inline_segments jsonb NOT NULL CHECK (
      jsonb_typeof(inline_segments) = 'array'
    ),
    ignored_change_classes text[] NOT NULL,
    comparison_label text NOT NULL CHECK (
      comparison_label = '與上一版本差異'
    ),
    algorithm_version text NOT NULL CHECK (
      algorithm_version = 'chapter-00-semantic-diff-presentation/v3'
    ),
    ignored_change_policy jsonb NOT NULL CHECK (
      jsonb_typeof(ignored_change_policy) = 'array'
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, version_id, hunk_id),
    UNIQUE (run_id, version_id, hunk_order),
    FOREIGN KEY (run_id, version_id)
      REFERENCES nhi_rule_history_announced.composed_clause_version(
        run_id, version_id
      ),
    FOREIGN KEY (predecessor_publication_run_id, clause_code)
      REFERENCES nhi_rule_history_publication.current_clause(
        run_id, clause_code
      ),
    CHECK (
      (old_text IS NULL) = (old_text_sha256 IS NULL)
      AND (
        old_text IS NULL
        OR old_text_sha256 =
          encode(sha256(convert_to(old_text, 'UTF8')), 'hex')
      )
    ),
    CHECK (
      (new_text IS NULL) = (new_text_sha256 IS NULL)
      AND (
        new_text IS NULL
        OR new_text_sha256 =
          encode(sha256(convert_to(new_text, 'UTF8')), 'hex')
      )
    ),
    CHECK (
      (old_block_start IS NULL) = (old_block_end IS NULL)
      AND (
        old_block_start IS NULL
        OR (
          old_block_start >= 0
          AND old_block_end > old_block_start
          AND old_text IS NOT NULL
        )
      )
    ),
    CHECK (
      (new_block_start IS NULL) = (new_block_end IS NULL)
      AND (
        new_block_start IS NULL
        OR (
          new_block_start >= 0
          AND new_block_end > new_block_start
          AND new_text IS NOT NULL
        )
      )
    ),
    CHECK (
      (
        semantic_change_kind = 'added'
        AND new_text IS NOT NULL
      )
      OR
      (
        semantic_change_kind = 'removed'
        AND old_text IS NOT NULL
      )
      OR
      (
        semantic_change_kind = 'replaced'
        AND old_text IS NOT NULL
        AND new_text IS NOT NULL
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
    'composed_clause_tagging_block_input', (
      SELECT count(*)
      FROM
        nhi_rule_history_announced.composed_clause_tagging_block_input
      WHERE run_id=OLD.run_id
    ),
    'composed_clause_terminology_occurrence', (
      SELECT count(*)
      FROM
        nhi_rule_history_announced.composed_clause_terminology_occurrence
      WHERE run_id=OLD.run_id
    ),
    'composed_clause_diff_hunk', (
      SELECT count(*)
      FROM nhi_rule_history_announced.composed_clause_diff_hunk
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
      SELECT count(*) AS scan_count
      FROM
        nhi_rule_history_announced.composed_clause_tagging_block_input input
      WHERE input.run_id=version.run_id
        AND input.version_id=version.version_id
    ) scan ON true
    LEFT JOIN LATERAL (
      SELECT count(*) AS block_count
      FROM nhi_rule_history_announced.composed_clause_block block
      WHERE block.run_id=version.run_id
        AND block.version_id=version.version_id
    ) blocks ON true
    LEFT JOIN LATERAL (
      SELECT count(*) AS diff_count,
             min(hunk.hunk_order) AS first_hunk,
             max(hunk.hunk_order) AS last_hunk
      FROM nhi_rule_history_announced.composed_clause_diff_hunk hunk
      WHERE hunk.run_id=version.run_id
        AND hunk.version_id=version.version_id
    ) diff ON true
    WHERE version.run_id=OLD.run_id
      AND (
        scan.scan_count <> blocks.block_count
        OR diff.diff_count < 1
        OR diff.first_hunk <> 0
        OR diff.last_hunk <> diff.diff_count - 1
      )
  ) THEN
    RAISE EXCEPTION 'version tagging or adjacent diff coverage is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_announced.composed_clause_tagging_block_input input
    JOIN nhi_rule_history_announced.composed_clause_block block
      ON (block.run_id, block.version_id, block.block_order) =
         (input.run_id, input.version_id, input.block_order)
    WHERE input.run_id=OLD.run_id
      AND (
        input.source_block_id <> block.source_block_id
        OR input.source_block_sha256 <> block.raw_text_sha256
      )
  ) OR EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_announced.composed_clause_terminology_occurrence
        occurrence
    JOIN nhi_rule_history_announced.composed_clause_block block
      ON (block.run_id, block.version_id, block.block_order) =
         (occurrence.run_id, occurrence.version_id,
          occurrence.block_order)
    WHERE occurrence.run_id=OLD.run_id
      AND (
        occurrence.source_block_id <> block.source_block_id
        OR occurrence.source_block_sha256 <> block.raw_text_sha256
        OR substring(
          block.raw_text
          FROM occurrence.start_scalar + 1
          FOR occurrence.end_scalar - occurrence.start_scalar
        ) <> occurrence.matched_text
      )
  ) THEN
    RAISE EXCEPTION 'version terminology offsets differ from source blocks';
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
    'composed_clause_tagging_block_input',
    'composed_clause_terminology_occurrence',
    'composed_clause_diff_hunk'
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
  nhi_rule_history_announced.v_public_composed_clause_diff_hunk AS
SELECT hunk.*
FROM nhi_rule_history_announced.composed_clause_diff_hunk hunk
JOIN nhi_rule_history_announced.v_public_composed_clause_version version
  ON version.run_id=hunk.run_id
 AND version.version_id=hunk.version_id;

COMMIT;
