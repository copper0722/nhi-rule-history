-- 2026-07-28 — versioned semantic presentation for clause diffs
--
-- Base clause_diff_hunk rows remain immutable extraction/comparison evidence.
-- A sealed presentation run applies an explicit ignore policy and classifies
-- each hunk as substantive added/removed/replaced or format_only.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-diff-policy-v2', 0)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.diff_run (
  run_id uuid PRIMARY KEY,
  clause_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  algorithm_version text NOT NULL,
  ignored_change_policy jsonb NOT NULL CHECK (
    jsonb_typeof(ignored_change_policy) = 'array'
    AND jsonb_array_length(ignored_change_policy) >= 1
  ),
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  input_sha256 text NOT NULL CHECK (
    input_sha256 ~ '^[0-9a-f]{64}$'
  ),
  output_sha256 text CHECK (
    output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'
  ),
  hunk_count integer CHECK (hunk_count IS NULL OR hunk_count >= 0),
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  UNIQUE (clause_import_run_id, algorithm_version),
  CHECK (
    (state = 'loading' AND output_sha256 IS NULL
      AND hunk_count IS NULL AND sealed_at IS NULL)
    OR
    (state = 'sealed' AND output_sha256 IS NOT NULL
      AND hunk_count IS NOT NULL AND sealed_at IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.diff_hunk_presentation (
    diff_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.diff_run (run_id)
      ON DELETE RESTRICT,
    hunk_id text NOT NULL
      REFERENCES nhi_rule_history_clause.clause_diff_hunk (hunk_id)
      ON DELETE RESTRICT,
    semantic_change_kind text NOT NULL CHECK (
      semantic_change_kind IN (
        'added', 'removed', 'replaced', 'format_only'
      )
    ),
    inline_segments jsonb NOT NULL CHECK (
      jsonb_typeof(inline_segments) = 'array'
    ),
    ignored_change_classes jsonb NOT NULL CHECK (
      jsonb_typeof(ignored_change_classes) = 'array'
    ),
    display_note text NOT NULL,
    PRIMARY KEY (diff_run_id, hunk_id)
  );

CREATE INDEX IF NOT EXISTS diff_presentation_hunk_idx
  ON nhi_rule_history_clause.diff_hunk_presentation (hunk_id, diff_run_id);

CREATE OR REPLACE VIEW
  nhi_rule_history_clause.v_current_diff_hunk_presentation AS
SELECT
  presentation.diff_run_id,
  presentation.hunk_id,
  presentation.semantic_change_kind,
  presentation.inline_segments,
  presentation.ignored_change_classes,
  presentation.display_note,
  run.algorithm_version,
  run.ignored_change_policy,
  run.sealed_at
FROM nhi_rule_history_clause.diff_hunk_presentation presentation
JOIN nhi_rule_history_clause.diff_run run
  ON run.run_id = presentation.diff_run_id
WHERE run.state = 'sealed'
  AND run.run_id = (
    SELECT candidate.run_id
    FROM nhi_rule_history_clause.diff_run candidate
    WHERE candidate.state = 'sealed'
      AND candidate.clause_import_run_id = run.clause_import_run_id
    ORDER BY candidate.sealed_at DESC, candidate.run_id DESC
    LIMIT 1
  );

COMMIT;
