-- 2026-07-28 — single-clause canonical units derived from official editions
--
-- `nhi_rule_history_edition` remains the source-edition container. This
-- additive schema makes one reimbursement clause, not one chapter, the
-- canonical version and comparison unit.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-v1', 0)
);

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_clause;

COMMENT ON SCHEMA nhi_rule_history_clause IS
  'Single-clause source-observed versions and diffs. Source-edition dates are '
  'not silently promoted to legal effective dates.';

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.import_run (
  run_id uuid PRIMARY KEY,
  edition_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.import_run (run_id)
    ON DELETE RESTRICT,
  source_set_sha256 text NOT NULL UNIQUE CHECK (
    source_set_sha256 ~ '^[0-9a-f]{64}$'
  ),
  extractor_version text NOT NULL,
  diff_version text NOT NULL,
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  row_counts jsonb CHECK (
    row_counts IS NULL OR jsonb_typeof(row_counts) = 'object'
  ),
  output_sha256 text CHECK (
    output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'
  ),
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  CHECK (
    (state = 'loading' AND row_counts IS NULL
      AND output_sha256 IS NULL AND sealed_at IS NULL)
    OR
    (state = 'sealed' AND row_counts IS NOT NULL
      AND output_sha256 IS NOT NULL AND sealed_at IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.chapter (
  chapter_id text PRIMARY KEY,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  display_label text NOT NULL,
  source_designation_raw text NOT NULL,
  navigation_code text NOT NULL UNIQUE,
  navigation_code_origin text NOT NULL CHECK (
    navigation_code_origin IN ('official_source', 'project_assigned')
  ),
  CHECK (
    navigation_code <> 'chapter:00'
    OR (
      display_label = '通則'
      AND source_designation_raw = '通則'
      AND navigation_code_origin = 'project_assigned'
    )
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause (
  clause_id text PRIMARY KEY,
  chapter_id text NOT NULL
    REFERENCES nhi_rule_history_clause.chapter (chapter_id)
    ON DELETE RESTRICT,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  canonical_code text NOT NULL,
  ordinal_number integer NOT NULL CHECK (ordinal_number >= 1),
  code_origin text NOT NULL CHECK (code_origin = 'project_assigned'),
  identity_basis text NOT NULL,
  identity_status text NOT NULL CHECK (
    identity_status IN (
      'verified_within_declared_edition_set',
      'identity_review_required'
    )
  ),
  UNIQUE (chapter_id, canonical_code),
  UNIQUE (chapter_id, ordinal_number),
  UNIQUE (clause_id, chapter_id),
  CHECK (canonical_code ~ '^0[.][1-9][0-9]*$')
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_version (
  clause_version_id text PRIMARY KEY,
  clause_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause (clause_id)
    ON DELETE RESTRICT,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  state_order integer NOT NULL CHECK (state_order >= 0),
  display_title text NOT NULL,
  representative_raw_text text NOT NULL CHECK (
    representative_raw_text <> ''
  ),
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  structured_json jsonb NOT NULL CHECK (
    jsonb_typeof(structured_json) = 'object'
  ),
  representative_raw_sha256 text NOT NULL CHECK (
    representative_raw_sha256 ~ '^[0-9a-f]{64}$'
  ),
  normalized_sha256 text NOT NULL CHECK (
    normalized_sha256 ~ '^[0-9a-f]{64}$'
  ),
  comparison_sha256 text NOT NULL CHECK (
    comparison_sha256 ~ '^[0-9a-f]{64}$'
  ),
  extractor_version text NOT NULL,
  legal_effective_status text NOT NULL CHECK (
    legal_effective_status IN (
      'not_claimed', 'candidate_unresolved', 'verified'
    )
  ),
  UNIQUE (clause_id, state_order),
  UNIQUE (clause_id, clause_version_id),
  CHECK (
    representative_raw_sha256 = encode(
      sha256(convert_to(representative_raw_text, 'UTF8')),
      'hex'
    )
  ),
  CHECK (
    normalized_sha256 = encode(
      sha256(convert_to(normalized_text, 'UTF8')),
      'hex'
    )
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_version_observation (
  observation_id text PRIMARY KEY,
  clause_id text NOT NULL,
  clause_version_id text NOT NULL,
  source_edition_version_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule_version (version_id)
    ON DELETE RESTRICT,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  chronology_order integer NOT NULL CHECK (chronology_order >= 0),
  edition_label text NOT NULL,
  source_designation_raw text NOT NULL,
  source_order_start integer NOT NULL CHECK (source_order_start >= 0),
  source_order_end integer NOT NULL CHECK (
    source_order_end >= source_order_start
  ),
  raw_text text NOT NULL CHECK (raw_text <> ''),
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  raw_sha256 text NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
  normalized_sha256 text NOT NULL CHECK (
    normalized_sha256 ~ '^[0-9a-f]{64}$'
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  FOREIGN KEY (clause_id, clause_version_id)
    REFERENCES nhi_rule_history_clause.clause_version (
      clause_id, clause_version_id
    )
    ON DELETE RESTRICT,
  UNIQUE (clause_id, source_edition_version_id),
  UNIQUE (clause_version_id, source_edition_version_id),
  UNIQUE (clause_version_id, observation_id),
  CHECK (
    raw_sha256 = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  ),
  CHECK (
    normalized_sha256 = encode(
      sha256(convert_to(normalized_text, 'UTF8')),
      'hex'
    )
  )
);

CREATE INDEX IF NOT EXISTS clause_observation_edition_idx
  ON nhi_rule_history_clause.clause_version_observation (
    source_edition_version_id, clause_id
  );

CREATE INDEX IF NOT EXISTS clause_observation_chronology_idx
  ON nhi_rule_history_clause.clause_version_observation (
    clause_id, chronology_order
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_version_block (
  block_id text PRIMARY KEY,
  clause_version_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause_version (clause_version_id)
    ON DELETE RESTRICT,
  representative_observation_id text NOT NULL,
  block_order integer NOT NULL CHECK (block_order >= 0),
  block_kind text NOT NULL,
  structural_path jsonb NOT NULL CHECK (
    jsonb_typeof(structural_path) = 'array'
  ),
  raw_text text NOT NULL CHECK (raw_text <> ''),
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  comparison_key text NOT NULL CHECK (comparison_key <> ''),
  raw_sha256 text NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  FOREIGN KEY (clause_version_id, representative_observation_id)
    REFERENCES nhi_rule_history_clause.clause_version_observation (
      clause_version_id, observation_id
    )
    ON DELETE RESTRICT,
  UNIQUE (clause_version_id, block_order),
  UNIQUE (clause_version_id, block_id),
  CHECK (
    raw_sha256 = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_version_date (
  date_fact_id text PRIMARY KEY,
  clause_version_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause_version (clause_version_id)
    ON DELETE RESTRICT,
  representative_observation_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause_version_observation (
      observation_id
    )
    ON DELETE RESTRICT,
  date_role text NOT NULL CHECK (
    date_role = 'text_amendment_annotation'
  ),
  raw_value text NOT NULL,
  calendar_system text NOT NULL CHECK (
    calendar_system IN ('ROC', 'Gregorian', 'mixed', 'unknown')
  ),
  date_value date,
  date_precision text NOT NULL CHECK (
    date_precision IN ('day', 'month', 'year', 'unknown')
  ),
  basis text NOT NULL,
  legal_effective_status text NOT NULL CHECK (
    legal_effective_status IN (
      'candidate_unresolved', 'verified', 'rejected_non_date'
    )
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  CHECK (date_precision = 'unknown' OR date_value IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS clause_version_date_lookup_idx
  ON nhi_rule_history_clause.clause_version_date (
    clause_version_id, date_value, date_fact_id
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_version_edge (
  edge_id text PRIMARY KEY,
  clause_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause (clause_id)
    ON DELETE RESTRICT,
  older_clause_version_id text NOT NULL,
  newer_clause_version_id text NOT NULL UNIQUE,
  adjacency_basis text NOT NULL CHECK (
    adjacency_basis =
      'adjacent_distinct_text_state_across_official_editions'
  ),
  legal_predecessor_status text NOT NULL CHECK (
    legal_predecessor_status IN ('not_claimed', 'verified')
  ),
  crosses_known_gap boolean NOT NULL,
  older_last_observed_order integer NOT NULL CHECK (
    older_last_observed_order >= 0
  ),
  newer_first_observed_order integer NOT NULL CHECK (
    newer_first_observed_order > older_last_observed_order
  ),
  algorithm_version text NOT NULL,
  input_sha256 text NOT NULL CHECK (
    input_sha256 ~ '^[0-9a-f]{64}$'
  ),
  output_sha256 text NOT NULL CHECK (
    output_sha256 ~ '^[0-9a-f]{64}$'
  ),
  change_hunk_count integer NOT NULL CHECK (change_hunk_count >= 1),
  status text NOT NULL CHECK (
    status IN ('verified_source_edition_diff', 'ambiguous', 'blocked')
  ),
  FOREIGN KEY (clause_id, older_clause_version_id)
    REFERENCES nhi_rule_history_clause.clause_version (
      clause_id, clause_version_id
    )
    ON DELETE RESTRICT,
  FOREIGN KEY (clause_id, newer_clause_version_id)
    REFERENCES nhi_rule_history_clause.clause_version (
      clause_id, clause_version_id
    )
    ON DELETE RESTRICT,
  CHECK (older_clause_version_id <> newer_clause_version_id)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.clause_diff_hunk (
  hunk_id text PRIMARY KEY,
  edge_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause_version_edge (edge_id)
    ON DELETE RESTRICT,
  hunk_order integer NOT NULL CHECK (hunk_order >= 0),
  change_kind text NOT NULL CHECK (
    change_kind IN ('added', 'removed', 'replaced')
  ),
  context_label text NOT NULL,
  old_block_id text,
  new_block_id text,
  old_text text,
  new_text text,
  old_text_sha256 text CHECK (
    old_text_sha256 IS NULL OR old_text_sha256 ~ '^[0-9a-f]{64}$'
  ),
  new_text_sha256 text CHECK (
    new_text_sha256 IS NULL OR new_text_sha256 ~ '^[0-9a-f]{64}$'
  ),
  inline_segments jsonb NOT NULL CHECK (
    jsonb_typeof(inline_segments) = 'array'
  ),
  display_note text NOT NULL,
  UNIQUE (edge_id, hunk_order),
  FOREIGN KEY (old_block_id)
    REFERENCES nhi_rule_history_clause.clause_version_block (block_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (new_block_id)
    REFERENCES nhi_rule_history_clause.clause_version_block (block_id)
    ON DELETE RESTRICT,
  CHECK (old_text IS NOT NULL OR new_text IS NOT NULL),
  CHECK (
    (old_text IS NULL AND old_text_sha256 IS NULL)
    OR old_text_sha256 =
      encode(sha256(convert_to(old_text, 'UTF8')), 'hex')
  ),
  CHECK (
    (new_text IS NULL AND new_text_sha256 IS NULL)
    OR new_text_sha256 =
      encode(sha256(convert_to(new_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_clause.coverage_assessment (
  assessment_id text PRIMARY KEY,
  clause_id text NOT NULL
    REFERENCES nhi_rule_history_clause.clause (clause_id)
    ON DELETE RESTRICT,
  import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.import_run (run_id)
    ON DELETE RESTRICT,
  declared_edition_count integer NOT NULL CHECK (
    declared_edition_count >= 1
  ),
  observed_edition_count integer NOT NULL CHECK (
    observed_edition_count >= 1
    AND observed_edition_count <= declared_edition_count
  ),
  first_observed_order integer NOT NULL CHECK (first_observed_order >= 0),
  last_observed_order integer NOT NULL CHECK (
    last_observed_order >= first_observed_order
  ),
  version_state_count integer NOT NULL CHECK (version_state_count >= 1),
  unique_comparison_text_count integer NOT NULL CHECK (
    unique_comparison_text_count >= 1
    AND unique_comparison_text_count <= version_state_count
  ),
  version_edge_count integer NOT NULL CHECK (
    version_edge_count = version_state_count - 1
  ),
  observed_presence_contiguous boolean NOT NULL,
  declared_edition_set_complete boolean NOT NULL,
  official_source_universe_closed boolean NOT NULL,
  legal_history_complete boolean NOT NULL,
  status text NOT NULL CHECK (
    status IN (
      'complete_for_declared_source_edition_observations',
      'incomplete_declared_source_edition_observations'
    )
  ),
  gap_reasons jsonb NOT NULL CHECK (
    jsonb_typeof(gap_reasons) = 'array'
  ),
  assessed_at timestamptz NOT NULL,
  UNIQUE (clause_id, import_run_id),
  CHECK (
    status <> 'complete_for_declared_source_edition_observations'
    OR (
      observed_presence_contiguous
      AND declared_edition_set_complete
    )
  )
);

CREATE OR REPLACE VIEW
  nhi_rule_history_clause.v_clause_version_summary AS
SELECT
  clause_version.clause_version_id,
  clause_version.clause_id,
  clause_version.state_order,
  clause_version.display_title,
  clause_version.normalized_sha256,
  clause_version.comparison_sha256,
  clause_version.legal_effective_status,
  min(observation.chronology_order) AS first_observed_order,
  max(observation.chronology_order) AS last_observed_order,
  count(*)::integer AS observation_count,
  array_agg(
    observation.edition_label
    ORDER BY observation.chronology_order
  ) AS observed_edition_labels
FROM nhi_rule_history_clause.clause_version clause_version
JOIN nhi_rule_history_clause.clause_version_observation observation
  ON observation.clause_version_id = clause_version.clause_version_id
GROUP BY
  clause_version.clause_version_id,
  clause_version.clause_id,
  clause_version.state_order,
  clause_version.display_title,
  clause_version.normalized_sha256,
  clause_version.comparison_sha256,
  clause_version.legal_effective_status;

CREATE OR REPLACE VIEW
  nhi_rule_history_clause.v_clause_transition_summary AS
SELECT
  edge.edge_id,
  edge.clause_id,
  edge.older_clause_version_id,
  older.state_order AS older_state_order,
  edge.newer_clause_version_id,
  newer.state_order AS newer_state_order,
  edge.older_last_observed_order,
  edge.newer_first_observed_order,
  edge.adjacency_basis,
  edge.legal_predecessor_status,
  edge.crosses_known_gap,
  edge.change_hunk_count,
  edge.status
FROM nhi_rule_history_clause.clause_version_edge edge
JOIN nhi_rule_history_clause.clause_version older
  ON older.clause_version_id = edge.older_clause_version_id
JOIN nhi_rule_history_clause.clause_version newer
  ON newer.clause_version_id = edge.newer_clause_version_id;

COMMIT;
