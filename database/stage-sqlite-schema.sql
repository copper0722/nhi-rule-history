-- Immutable portable projection of the bounded v1 source-occurrence stage.
-- This schema is NOT the future canonical legal-history schema.

PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE rebuild_run (
  run_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state = 'sealed'),
  parser_version TEXT NOT NULL,
  loader_version TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  code_hash TEXT NOT NULL CHECK (length(code_hash) = 64),
  input_fingerprint TEXT NOT NULL CHECK (length(input_fingerprint) = 64),
  sealed_fingerprint TEXT NOT NULL CHECK (length(sealed_fingerprint) = 64),
  output_fingerprint TEXT CHECK (output_fingerprint IS NULL OR length(output_fingerprint) = 64),
  accepted_manifest_sha256 TEXT NOT NULL CHECK (length(accepted_manifest_sha256) = 64),
  expected_counts TEXT NOT NULL CHECK (json_valid(expected_counts)),
  verified_counts TEXT CHECK (verified_counts IS NULL OR json_valid(verified_counts)),
  expected_release_count INTEGER NOT NULL CHECK (expected_release_count >= 0),
  expected_block_count INTEGER NOT NULL CHECK (expected_block_count >= 0),
  expected_occurrence_count INTEGER NOT NULL CHECK (expected_occurrence_count >= 0),
  expected_empty_table_cell_block_count INTEGER NOT NULL CHECK (expected_empty_table_cell_block_count >= 0),
  expected_xml_ph_element_count_total INTEGER NOT NULL CHECK (expected_xml_ph_element_count_total >= 0),
  expected_xml_ph_emitted_unique_total INTEGER NOT NULL CHECK (expected_xml_ph_emitted_unique_total >= 0),
  expected_xml_ph_unaccounted_total INTEGER NOT NULL CHECK (expected_xml_ph_unaccounted_total >= 0),
  created_at TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  failed_at TEXT,
  failure_code TEXT,
  failure_detail TEXT
) WITHOUT ROWID;

CREATE TABLE run_input_file (
  run_id TEXT NOT NULL REFERENCES rebuild_run(run_id) ON DELETE RESTRICT,
  logical_name TEXT NOT NULL,
  declared_schema TEXT NOT NULL,
  byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
  row_count INTEGER NOT NULL CHECK (row_count >= 0),
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  relative_locator TEXT NOT NULL CHECK (
    relative_locator <> ''
    AND substr(relative_locator, 1, 1) NOT IN ('/', '\')
    AND instr(relative_locator, '..') = 0
  ),
  PRIMARY KEY (run_id, logical_name)
) WITHOUT ROWID;

CREATE TABLE source_release (
  run_id TEXT NOT NULL REFERENCES rebuild_run(run_id) ON DELETE RESTRICT,
  release_id TEXT NOT NULL CHECK (length(release_id) = 64),
  source_order_index INTEGER NOT NULL CHECK (source_order_index >= 0),
  relative_path TEXT NOT NULL,
  basename TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK (content_sha256 = release_id),
  byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
  filename_label_raw TEXT NOT NULL,
  filename_id_prefix TEXT,
  filename_date_fragments_raw TEXT NOT NULL CHECK (json_valid(filename_date_fragments_raw)),
  analysis_chronology TEXT NOT NULL CHECK (json_valid(analysis_chronology)),
  parser_version TEXT NOT NULL,
  block_count INTEGER NOT NULL CHECK (block_count >= 0),
  occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 0),
  table_count INTEGER NOT NULL CHECK (table_count >= 0),
  row_count_xml INTEGER NOT NULL CHECK (row_count_xml >= 0),
  cell_count_xml INTEGER NOT NULL CHECK (cell_count_xml >= 0),
  row_count_logical INTEGER NOT NULL CHECK (row_count_logical >= 0),
  cell_count_logical INTEGER NOT NULL CHECK (cell_count_logical >= 0),
  empty_cell_count INTEGER NOT NULL CHECK (empty_cell_count >= 0),
  nested_table_count INTEGER NOT NULL CHECK (nested_table_count >= 0),
  empty_table_cell_block_count INTEGER NOT NULL CHECK (empty_table_cell_block_count >= 0),
  numeric_quantity_rejection_count INTEGER NOT NULL CHECK (numeric_quantity_rejection_count >= 0),
  odt_repeat_attrs_present INTEGER NOT NULL CHECK (odt_repeat_attrs_present IN (0, 1)),
  xml_ph_element_count INTEGER NOT NULL CHECK (xml_ph_element_count >= 0),
  xml_ph_nested_count INTEGER NOT NULL CHECK (xml_ph_nested_count >= 0),
  xml_ph_emitted_unique INTEGER NOT NULL CHECK (xml_ph_emitted_unique >= 0),
  xml_ph_unaccounted INTEGER NOT NULL CHECK (xml_ph_unaccounted >= 0),
  source_structural_block_count_before_repeat_expansion INTEGER NOT NULL CHECK (source_structural_block_count_before_repeat_expansion >= 0),
  accepted_manifest_sha256 TEXT NOT NULL CHECK (length(accepted_manifest_sha256) = 64),
  accepted_manifest_match INTEGER NOT NULL CHECK (accepted_manifest_match IN (0, 1)),
  statement TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (run_id, release_id),
  UNIQUE (run_id, source_order_index),
  UNIQUE (run_id, relative_path)
) WITHOUT ROWID;

CREATE TABLE source_artifact (
  run_id TEXT NOT NULL REFERENCES rebuild_run(run_id) ON DELETE RESTRICT,
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  relative_locator TEXT NOT NULL,
  basename TEXT NOT NULL,
  byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
  media_type TEXT NOT NULL CHECK (media_type = 'application/vnd.oasis.opendocument.text'),
  content_sha256 TEXT NOT NULL CHECK (content_sha256 = artifact_sha256),
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (run_id, artifact_sha256),
  UNIQUE (run_id, relative_locator)
) WITHOUT ROWID;

CREATE TABLE release_artifact (
  run_id TEXT NOT NULL,
  release_id TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  association_role TEXT NOT NULL CHECK (association_role = 'primary_parse_source'),
  PRIMARY KEY (run_id, release_id, artifact_sha256),
  FOREIGN KEY (run_id, release_id)
    REFERENCES source_release(run_id, release_id) ON DELETE RESTRICT,
  FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES source_artifact(run_id, artifact_sha256) ON DELETE RESTRICT,
  CHECK (release_id = artifact_sha256),
  UNIQUE (run_id, release_id)
) WITHOUT ROWID;

CREATE TABLE structural_block (
  run_id TEXT NOT NULL,
  block_id TEXT NOT NULL CHECK (length(block_id) = 64),
  artifact_sha256 TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  block_kind TEXT NOT NULL CHECK (block_kind IN (
    'paragraph', 'heading', 'table_paragraph', 'frame_paragraph',
    'index_paragraph', 'empty_table_cell'
  )),
  container TEXT NOT NULL CHECK (container IN ('flow', 'table_cell', 'frame', 'index', 'other')),
  element_name TEXT NOT NULL,
  style_name TEXT,
  in_table INTEGER NOT NULL CHECK (in_table IN (0, 1)),
  in_index_context INTEGER NOT NULL CHECK (in_index_context IN (0, 1)),
  xml_element_index INTEGER NOT NULL CHECK (xml_element_index >= 0),
  parser_order INTEGER NOT NULL CHECK (parser_order >= 0),
  locator TEXT NOT NULL CHECK (json_valid(locator)),
  locator_key TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  normalized_search_text TEXT NOT NULL,
  raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256) = 64),
  raw_text_byte_length INTEGER NOT NULL CHECK (raw_text_byte_length >= 0),
  raw_text_char_length INTEGER NOT NULL CHECK (raw_text_char_length >= 0),
  parser_version TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (run_id, block_id),
  UNIQUE (run_id, block_id, artifact_sha256),
  UNIQUE (run_id, artifact_sha256, locator_key),
  FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES source_artifact(run_id, artifact_sha256) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE occurrence_candidate (
  run_id TEXT NOT NULL,
  occurrence_id TEXT NOT NULL CHECK (length(occurrence_id) = 64),
  artifact_sha256 TEXT NOT NULL,
  block_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  designation_text TEXT NOT NULL CHECK (designation_text <> ''),
  match_start_in_raw INTEGER NOT NULL CHECK (match_start_in_raw >= 0),
  match_end_in_raw INTEGER NOT NULL CHECK (match_end_in_raw > match_start_in_raw),
  raw_text_sha256 TEXT NOT NULL CHECK (length(raw_text_sha256) = 64),
  raw_text_byte_length INTEGER NOT NULL CHECK (raw_text_byte_length >= 0),
  raw_text_char_length INTEGER NOT NULL CHECK (raw_text_char_length >= match_end_in_raw),
  container TEXT NOT NULL CHECK (container IN ('flow', 'table_cell', 'frame', 'index', 'other')),
  in_index_context INTEGER NOT NULL CHECK (in_index_context IN (0, 1)),
  ambiguity_flags TEXT NOT NULL CHECK (json_valid(ambiguity_flags)),
  parser_version TEXT NOT NULL,
  statement TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (run_id, occurrence_id),
  FOREIGN KEY (run_id, block_id, artifact_sha256)
    REFERENCES structural_block(run_id, block_id, artifact_sha256) ON DELETE RESTRICT,
  FOREIGN KEY (run_id, artifact_sha256)
    REFERENCES source_artifact(run_id, artifact_sha256) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE stage_issue (
  run_id TEXT NOT NULL REFERENCES rebuild_run(run_id) ON DELETE RESTRICT,
  issue_seq INTEGER NOT NULL CHECK (issue_seq >= 0),
  issue_code TEXT NOT NULL,
  issue_class TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
  is_blocking INTEGER NOT NULL CHECK (is_blocking IN (0, 1)),
  relative_path TEXT,
  detail TEXT NOT NULL,
  artifact_sha256 TEXT,
  block_id TEXT,
  locator_key TEXT,
  attributes TEXT NOT NULL CHECK (json_valid(attributes)),
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (run_id, issue_seq),
  FOREIGN KEY (run_id, block_id)
    REFERENCES structural_block(run_id, block_id) ON DELETE RESTRICT,
  CHECK (is_blocking = 0 OR severity = 'error')
) WITHOUT ROWID;

CREATE TABLE dataset_metadata (
  dataset_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  export_contract_version TEXT NOT NULL,
  dataset_kind TEXT NOT NULL CHECK (dataset_kind = 'source_occurrence_staging'),
  run_id TEXT NOT NULL UNIQUE REFERENCES rebuild_run(run_id) ON DELETE RESTRICT,
  sealed_fingerprint TEXT NOT NULL CHECK (length(sealed_fingerprint) = 64),
  logical_row_digest TEXT NOT NULL CHECK (length(logical_row_digest) = 64),
  legal_history_claim INTEGER NOT NULL CHECK (legal_history_claim = 0),
  scope_statement TEXT NOT NULL CHECK (instr(lower(scope_statement), 'not a complete legal history') > 0),
  table_counts_json TEXT NOT NULL CHECK (json_valid(table_counts_json))
) WITHOUT ROWID;

CREATE INDEX structural_block_artifact_order_idx
  ON structural_block(run_id, artifact_sha256, parser_order);
CREATE INDEX occurrence_candidate_designation_idx
  ON occurrence_candidate(run_id, designation_text);
CREATE INDEX stage_issue_code_idx
  ON stage_issue(run_id, issue_code);
