PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value) VALUES
  ('schema_name', 'nhi-rule-history-edition'),
  ('schema_version', '1'),
  ('authority', 'PostgreSQL'),
  ('projection', 'sqlite-portable');

CREATE TABLE import_run (
  run_id TEXT PRIMARY KEY,
  source_set_sha256 TEXT NOT NULL UNIQUE CHECK (
    length(source_set_sha256) = 64
  ),
  extractor_version TEXT NOT NULL,
  diff_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state = 'sealed'),
  source_stage_refs TEXT NOT NULL,
  row_counts TEXT NOT NULL,
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  started_at TEXT NOT NULL,
  sealed_at TEXT NOT NULL
);

CREATE TABLE source_document (
  document_id TEXT PRIMARY KEY,
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  source_kind TEXT NOT NULL CHECK (
    source_kind IN ('annual_full', 'current_chapter', 'current_full')
  ),
  official_label TEXT NOT NULL,
  source_page_url TEXT NOT NULL,
  official_url TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  media_type TEXT NOT NULL,
  byte_length INTEGER NOT NULL CHECK (byte_length > 0),
  source_stage_schema TEXT NOT NULL,
  source_stage_run_id TEXT NOT NULL,
  source_resource_id TEXT,
  source_locator TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  UNIQUE (source_kind, official_url, artifact_sha256)
);

CREATE TABLE rule (
  rule_id TEXT PRIMARY KEY,
  canonical_slug TEXT NOT NULL UNIQUE,
  display_label TEXT NOT NULL,
  source_designation_raw TEXT NOT NULL,
  navigation_code TEXT NOT NULL,
  navigation_code_origin TEXT NOT NULL CHECK (
    navigation_code_origin IN ('official_source', 'project_assigned')
  ),
  identity_status TEXT NOT NULL CHECK (
    identity_status IN ('active', 'retired', 'unresolved')
  ),
  created_at TEXT NOT NULL,
  CHECK (
    navigation_code <> 'chapter:00'
    OR (
      source_designation_raw = '通則'
      AND display_label = '通則'
      AND navigation_code_origin = 'project_assigned'
    )
  )
);

CREATE TABLE rule_version (
  version_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES rule(rule_id),
  primary_document_id TEXT NOT NULL REFERENCES source_document(document_id),
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  chronology_order INTEGER NOT NULL CHECK (chronology_order >= 0),
  version_label TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  normalized_sha256 TEXT NOT NULL CHECK (length(normalized_sha256) = 64),
  source_locator TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  validation_status TEXT NOT NULL CHECK (
    validation_status IN ('verified_source_snapshot', 'quarantined')
  ),
  legal_effective_status TEXT NOT NULL CHECK (
    legal_effective_status IN (
      'not_claimed', 'candidate_unresolved', 'verified'
    )
  ),
  created_at TEXT NOT NULL,
  UNIQUE (rule_id, chronology_order),
  UNIQUE (rule_id, primary_document_id),
  UNIQUE (rule_id, version_id)
);

CREATE TABLE version_source (
  version_id TEXT NOT NULL REFERENCES rule_version(version_id),
  document_id TEXT NOT NULL REFERENCES source_document(document_id),
  evidence_role TEXT NOT NULL CHECK (
    evidence_role IN ('primary_text', 'whole_document_cross_check')
  ),
  parity_status TEXT NOT NULL CHECK (
    parity_status IN (
      'primary', 'exact_normalized', 'format_only_difference',
      'content_mismatch'
    )
  ),
  source_locator TEXT NOT NULL,
  PRIMARY KEY (version_id, document_id, evidence_role)
);

CREATE TABLE rule_version_date (
  date_fact_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES rule_version(version_id),
  date_role TEXT NOT NULL CHECK (
    date_role IN (
      'official_edition_label',
      'official_update_date',
      'source_observed_at',
      'text_amendment_annotation'
    )
  ),
  raw_value TEXT NOT NULL,
  calendar_system TEXT NOT NULL CHECK (
    calendar_system IN ('ROC', 'Gregorian', 'mixed', 'unknown')
  ),
  date_value TEXT,
  date_precision TEXT NOT NULL CHECK (
    date_precision IN ('day', 'month', 'year', 'unknown')
  ),
  basis TEXT NOT NULL,
  legal_effective_status TEXT NOT NULL CHECK (
    legal_effective_status IN (
      'not_claimed', 'candidate_unresolved', 'verified',
      'rejected_non_date'
    )
  ),
  source_locator TEXT NOT NULL,
  CHECK (date_precision = 'unknown' OR date_value IS NOT NULL)
);

CREATE TABLE rule_block (
  block_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES rule_version(version_id),
  source_order INTEGER NOT NULL CHECK (source_order >= 0),
  block_kind TEXT NOT NULL,
  structural_path TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  comparison_key TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  source_locator TEXT NOT NULL,
  UNIQUE (version_id, source_order),
  UNIQUE (version_id, block_id)
);

CREATE TABLE version_edge (
  edge_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES rule(rule_id),
  older_version_id TEXT NOT NULL REFERENCES rule_version(version_id),
  newer_version_id TEXT NOT NULL UNIQUE REFERENCES rule_version(version_id),
  adjacency_basis TEXT NOT NULL CHECK (
    adjacency_basis = 'adjacent_official_edition'
  ),
  legal_predecessor_status TEXT NOT NULL CHECK (
    legal_predecessor_status IN ('not_claimed', 'verified')
  ),
  crosses_known_gap INTEGER NOT NULL CHECK (crosses_known_gap IN (0, 1)),
  algorithm_version TEXT NOT NULL,
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  format_only INTEGER NOT NULL CHECK (format_only IN (0, 1)),
  change_hunk_count INTEGER NOT NULL CHECK (change_hunk_count >= 0),
  status TEXT NOT NULL CHECK (
    status IN ('verified_edition_diff', 'ambiguous', 'blocked')
  ),
  created_at TEXT NOT NULL,
  CHECK (older_version_id <> newer_version_id)
);

CREATE TABLE diff_hunk (
  hunk_id TEXT PRIMARY KEY,
  edge_id TEXT NOT NULL REFERENCES version_edge(edge_id),
  hunk_order INTEGER NOT NULL CHECK (hunk_order >= 0),
  change_kind TEXT NOT NULL CHECK (
    change_kind IN ('added', 'removed', 'replaced', 'format_only')
  ),
  context_label TEXT NOT NULL,
  old_block_id TEXT REFERENCES rule_block(block_id),
  new_block_id TEXT REFERENCES rule_block(block_id),
  old_text TEXT,
  new_text TEXT,
  old_text_sha256 TEXT,
  new_text_sha256 TEXT,
  inline_segments TEXT NOT NULL,
  display_note TEXT NOT NULL,
  UNIQUE (edge_id, hunk_order),
  CHECK (old_text IS NOT NULL OR new_text IS NOT NULL)
);

CREATE TABLE coverage_assessment (
  assessment_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES rule(rule_id),
  import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  declared_edition_count INTEGER NOT NULL CHECK (declared_edition_count >= 1),
  loaded_edition_count INTEGER NOT NULL CHECK (
    loaded_edition_count >= 0
    AND loaded_edition_count <= declared_edition_count
  ),
  adjacent_edge_count INTEGER NOT NULL CHECK (adjacent_edge_count >= 0),
  material_change_edge_count INTEGER NOT NULL CHECK (
    material_change_edge_count >= 0
    AND material_change_edge_count <= adjacent_edge_count
  ),
  edition_set_complete INTEGER NOT NULL CHECK (edition_set_complete IN (0, 1)),
  official_source_universe_closed INTEGER NOT NULL CHECK (
    official_source_universe_closed IN (0, 1)
  ),
  legal_history_complete INTEGER NOT NULL CHECK (
    legal_history_complete IN (0, 1)
  ),
  status TEXT NOT NULL CHECK (
    status IN (
      'complete_for_declared_edition_set',
      'incomplete_declared_edition_set'
    )
  ),
  gap_reasons TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  UNIQUE (rule_id, import_run_id),
  CHECK (
    status <> 'complete_for_declared_edition_set'
    OR (
      edition_set_complete = 1
      AND loaded_edition_count = declared_edition_count
      AND adjacent_edge_count = max(loaded_edition_count - 1, 0)
    )
  )
);

CREATE INDEX rule_version_date_lookup_idx
  ON rule_version_date(version_id, date_role, date_value);
CREATE INDEX rule_block_version_order_idx
  ON rule_block(version_id, source_order);
CREATE INDEX diff_hunk_edge_order_idx
  ON diff_hunk(edge_id, hunk_order);
