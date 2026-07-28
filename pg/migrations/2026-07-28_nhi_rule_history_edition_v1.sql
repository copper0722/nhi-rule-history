-- 2026-07-28 — normalized official-edition history for reader projections
--
-- This schema is PostgreSQL-canonical for source-observed cumulative editions.
-- It deliberately does not assert that adjacent editions are adjacent legal
-- amendment events. Verified legal promotion remains in `nhi_rule_history`.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-edition-v1', 0)
);

CREATE SCHEMA IF NOT EXISTS nhi_rule_history_edition;

COMMENT ON SCHEMA nhi_rule_history_edition IS
  'Normalized official-edition snapshots and derived reader diffs. '
  'Adjacent edition does not imply adjacent legal amendment event.';

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.import_run (
  run_id uuid PRIMARY KEY,
  source_set_sha256 text NOT NULL UNIQUE CHECK (
    source_set_sha256 ~ '^[0-9a-f]{64}$'
  ),
  extractor_version text NOT NULL,
  diff_version text NOT NULL,
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  source_stage_refs jsonb NOT NULL CHECK (
    jsonb_typeof(source_stage_refs) = 'object'
  ),
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

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.source_document (
  document_id text PRIMARY KEY,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.import_run (run_id)
    ON DELETE RESTRICT,
  source_kind text NOT NULL CHECK (
    source_kind IN ('annual_full', 'current_chapter', 'current_full')
  ),
  official_label text NOT NULL,
  source_page_url text NOT NULL CHECK (source_page_url ~ '^https://'),
  official_url text NOT NULL CHECK (official_url ~ '^https://'),
  artifact_sha256 text NOT NULL CHECK (
    artifact_sha256 ~ '^[0-9a-f]{64}$'
  ),
  media_type text NOT NULL,
  byte_length bigint NOT NULL CHECK (byte_length > 0),
  source_stage_schema text NOT NULL,
  source_stage_run_id uuid NOT NULL,
  source_resource_id text,
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  observed_at timestamptz NOT NULL,
  UNIQUE (source_kind, official_url, artifact_sha256)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.rule (
  rule_id text PRIMARY KEY,
  canonical_slug text NOT NULL UNIQUE,
  display_label text NOT NULL,
  source_designation_raw text NOT NULL,
  navigation_code text NOT NULL,
  navigation_code_origin text NOT NULL CHECK (
    navigation_code_origin IN ('official_source', 'project_assigned')
  ),
  identity_status text NOT NULL CHECK (
    identity_status IN ('active', 'retired', 'unresolved')
  ),
  created_at timestamptz NOT NULL DEFAULT current_timestamp,
  CHECK (
    navigation_code <> 'chapter:00'
    OR (
      source_designation_raw = '通則'
      AND display_label = '通則'
      AND navigation_code_origin = 'project_assigned'
    )
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.rule_version (
  version_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule (rule_id)
    ON DELETE RESTRICT,
  primary_document_id text NOT NULL
    REFERENCES nhi_rule_history_edition.source_document (document_id)
    ON DELETE RESTRICT,
  first_import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.import_run (run_id)
    ON DELETE RESTRICT,
  chronology_order integer NOT NULL CHECK (chronology_order >= 0),
  version_label text NOT NULL,
  raw_text text NOT NULL CHECK (raw_text <> ''),
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  structured_json jsonb NOT NULL CHECK (
    jsonb_typeof(structured_json) = 'object'
  ),
  raw_sha256 text NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
  normalized_sha256 text NOT NULL CHECK (
    normalized_sha256 ~ '^[0-9a-f]{64}$'
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  extractor_version text NOT NULL,
  validation_status text NOT NULL CHECK (
    validation_status IN ('verified_source_snapshot', 'quarantined')
  ),
  legal_effective_status text NOT NULL CHECK (
    legal_effective_status IN (
      'not_claimed', 'candidate_unresolved', 'verified'
    )
  ),
  created_at timestamptz NOT NULL DEFAULT current_timestamp,
  UNIQUE (rule_id, chronology_order),
  UNIQUE (rule_id, primary_document_id),
  UNIQUE (rule_id, version_id),
  CHECK (
    raw_sha256 = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  ),
  CHECK (
    normalized_sha256 =
      encode(sha256(convert_to(normalized_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.version_source (
  version_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule_version (version_id)
    ON DELETE RESTRICT,
  document_id text NOT NULL
    REFERENCES nhi_rule_history_edition.source_document (document_id)
    ON DELETE RESTRICT,
  evidence_role text NOT NULL CHECK (
    evidence_role IN ('primary_text', 'whole_document_cross_check')
  ),
  parity_status text NOT NULL CHECK (
    parity_status IN (
      'primary', 'exact_normalized', 'format_only_difference',
      'content_mismatch'
    )
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  PRIMARY KEY (version_id, document_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.rule_version_date (
  date_fact_id text PRIMARY KEY,
  version_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule_version (version_id)
    ON DELETE RESTRICT,
  date_role text NOT NULL CHECK (
    date_role IN (
      'official_edition_label',
      'official_update_date',
      'source_observed_at',
      'text_amendment_annotation'
    )
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
      'not_claimed', 'candidate_unresolved', 'verified',
      'rejected_non_date'
    )
  ),
  source_locator jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator) = 'object'
    AND source_locator <> '{}'::jsonb
  ),
  CHECK (
    date_precision = 'unknown' OR date_value IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS rule_version_date_lookup_idx
  ON nhi_rule_history_edition.rule_version_date (
    version_id, date_role, date_value
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.rule_block (
  block_id text PRIMARY KEY,
  version_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule_version (version_id)
    ON DELETE RESTRICT,
  source_order integer NOT NULL CHECK (source_order >= 0),
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
  UNIQUE (version_id, source_order),
  UNIQUE (version_id, block_id),
  CHECK (
    raw_sha256 = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.version_edge (
  edge_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule (rule_id)
    ON DELETE RESTRICT,
  older_version_id text NOT NULL,
  newer_version_id text NOT NULL UNIQUE,
  adjacency_basis text NOT NULL CHECK (
    adjacency_basis = 'adjacent_official_edition'
  ),
  legal_predecessor_status text NOT NULL CHECK (
    legal_predecessor_status IN ('not_claimed', 'verified')
  ),
  crosses_known_gap boolean NOT NULL,
  algorithm_version text NOT NULL,
  input_sha256 text NOT NULL CHECK (
    input_sha256 ~ '^[0-9a-f]{64}$'
  ),
  output_sha256 text NOT NULL CHECK (
    output_sha256 ~ '^[0-9a-f]{64}$'
  ),
  format_only boolean NOT NULL,
  change_hunk_count integer NOT NULL CHECK (change_hunk_count >= 0),
  status text NOT NULL CHECK (
    status IN ('verified_edition_diff', 'ambiguous', 'blocked')
  ),
  created_at timestamptz NOT NULL DEFAULT current_timestamp,
  FOREIGN KEY (rule_id, older_version_id)
    REFERENCES nhi_rule_history_edition.rule_version (rule_id, version_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (rule_id, newer_version_id)
    REFERENCES nhi_rule_history_edition.rule_version (rule_id, version_id)
    ON DELETE RESTRICT,
  CHECK (older_version_id <> newer_version_id)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.diff_hunk (
  hunk_id text PRIMARY KEY,
  edge_id text NOT NULL
    REFERENCES nhi_rule_history_edition.version_edge (edge_id)
    ON DELETE RESTRICT,
  hunk_order integer NOT NULL CHECK (hunk_order >= 0),
  change_kind text NOT NULL CHECK (
    change_kind IN ('added', 'removed', 'replaced', 'format_only')
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
    REFERENCES nhi_rule_history_edition.rule_block (block_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (new_block_id)
    REFERENCES nhi_rule_history_edition.rule_block (block_id)
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

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.coverage_assessment (
  assessment_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history_edition.rule (rule_id)
    ON DELETE RESTRICT,
  import_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.import_run (run_id)
    ON DELETE RESTRICT,
  declared_edition_count integer NOT NULL CHECK (
    declared_edition_count >= 1
  ),
  loaded_edition_count integer NOT NULL CHECK (
    loaded_edition_count >= 0
    AND loaded_edition_count <= declared_edition_count
  ),
  adjacent_edge_count integer NOT NULL CHECK (adjacent_edge_count >= 0),
  material_change_edge_count integer NOT NULL CHECK (
    material_change_edge_count >= 0
    AND material_change_edge_count <= adjacent_edge_count
  ),
  edition_set_complete boolean NOT NULL,
  official_source_universe_closed boolean NOT NULL,
  legal_history_complete boolean NOT NULL,
  status text NOT NULL CHECK (
    status IN (
      'complete_for_declared_edition_set',
      'incomplete_declared_edition_set'
    )
  ),
  gap_reasons jsonb NOT NULL CHECK (
    jsonb_typeof(gap_reasons) = 'array'
  ),
  assessed_at timestamptz NOT NULL,
  UNIQUE (rule_id, import_run_id),
  CHECK (
    NOT legal_history_complete OR official_source_universe_closed
  ),
  CHECK (
    status <> 'complete_for_declared_edition_set'
    OR (
      edition_set_complete
      AND loaded_edition_count = declared_edition_count
      AND adjacent_edge_count = GREATEST(loaded_edition_count - 1, 0)
    )
  )
);

CREATE OR REPLACE VIEW nhi_rule_history_edition.v_version_summary AS
SELECT
  version_row.version_id,
  version_row.rule_id,
  version_row.chronology_order,
  version_row.version_label,
  version_row.raw_sha256,
  version_row.normalized_sha256,
  version_row.validation_status,
  version_row.legal_effective_status,
  document_row.document_id,
  document_row.source_kind,
  document_row.official_url,
  document_row.artifact_sha256,
  primary_date.date_value AS display_date,
  primary_date.date_precision AS display_date_precision,
  primary_date.date_role AS display_date_role,
  count(block_row.block_id) AS block_count
FROM nhi_rule_history_edition.rule_version version_row
JOIN nhi_rule_history_edition.source_document document_row
  ON document_row.document_id = version_row.primary_document_id
LEFT JOIN LATERAL (
  SELECT date_row.date_value, date_row.date_precision, date_row.date_role
  FROM nhi_rule_history_edition.rule_version_date date_row
  WHERE date_row.version_id = version_row.version_id
    AND date_row.date_role IN (
      'official_update_date', 'official_edition_label'
    )
  ORDER BY
    CASE date_row.date_role
      WHEN 'official_update_date' THEN 0 ELSE 1
    END,
    date_row.date_value DESC
  LIMIT 1
) primary_date ON true
LEFT JOIN nhi_rule_history_edition.rule_block block_row
  ON block_row.version_id = version_row.version_id
GROUP BY
  version_row.version_id,
  document_row.document_id,
  primary_date.date_value,
  primary_date.date_precision,
  primary_date.date_role;

CREATE OR REPLACE VIEW nhi_rule_history_edition.v_transition_summary AS
SELECT
  edge_row.edge_id,
  edge_row.rule_id,
  edge_row.older_version_id,
  older.version_label AS older_label,
  edge_row.newer_version_id,
  newer.version_label AS newer_label,
  newer.chronology_order,
  edge_row.adjacency_basis,
  edge_row.legal_predecessor_status,
  edge_row.crosses_known_gap,
  edge_row.format_only,
  edge_row.change_hunk_count,
  edge_row.status,
  count(hunk_row.hunk_id) AS stored_hunk_count
FROM nhi_rule_history_edition.version_edge edge_row
JOIN nhi_rule_history_edition.rule_version older
  ON older.version_id = edge_row.older_version_id
JOIN nhi_rule_history_edition.rule_version newer
  ON newer.version_id = edge_row.newer_version_id
LEFT JOIN nhi_rule_history_edition.diff_hunk hunk_row
  ON hunk_row.edge_id = edge_row.edge_id
GROUP BY
  edge_row.edge_id,
  older.version_label,
  newer.version_label,
  newer.chronology_order;

COMMIT;
