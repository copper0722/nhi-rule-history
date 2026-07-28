PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value) VALUES
  ('schema_name', 'nhi-rule-history-clause'),
  ('schema_version', '1'),
  ('authority', 'PostgreSQL'),
  ('projection', 'sqlite-portable');

CREATE TABLE import_run (
  run_id TEXT PRIMARY KEY,
  edition_import_run_id TEXT NOT NULL,
  source_set_sha256 TEXT NOT NULL UNIQUE CHECK (
    length(source_set_sha256) = 64
  ),
  extractor_version TEXT NOT NULL,
  diff_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state = 'sealed'),
  row_counts TEXT NOT NULL,
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  started_at TEXT NOT NULL,
  sealed_at TEXT NOT NULL
);

CREATE TABLE source_edition (
  source_edition_version_id TEXT PRIMARY KEY,
  edition_import_run_id TEXT NOT NULL,
  chronology_order INTEGER NOT NULL UNIQUE CHECK (chronology_order >= 0),
  edition_label TEXT NOT NULL,
  official_date_role TEXT NOT NULL CHECK (
    official_date_role IN ('official_edition_label', 'official_update_date')
  ),
  official_date_raw_value TEXT NOT NULL,
  official_date_value TEXT NOT NULL,
  official_date_precision TEXT NOT NULL CHECK (
    official_date_precision IN ('day', 'month', 'year')
  ),
  legal_effective_status TEXT NOT NULL CHECK (
    legal_effective_status = 'not_claimed'
  ),
  official_url TEXT NOT NULL,
  source_page_url TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64)
);

CREATE TABLE chapter (
  chapter_id TEXT PRIMARY KEY,
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  display_label TEXT NOT NULL,
  source_designation_raw TEXT NOT NULL,
  navigation_code TEXT NOT NULL UNIQUE,
  navigation_code_origin TEXT NOT NULL CHECK (
    navigation_code_origin IN ('official_source', 'project_assigned')
  )
);

CREATE TABLE clause (
  clause_id TEXT PRIMARY KEY,
  chapter_id TEXT NOT NULL REFERENCES chapter(chapter_id),
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  canonical_code TEXT NOT NULL,
  ordinal_number INTEGER NOT NULL CHECK (ordinal_number >= 1),
  code_origin TEXT NOT NULL CHECK (code_origin = 'project_assigned'),
  identity_basis TEXT NOT NULL,
  identity_status TEXT NOT NULL CHECK (
    identity_status IN (
      'verified_within_declared_edition_set',
      'identity_review_required'
    )
  ),
  UNIQUE (chapter_id, canonical_code),
  UNIQUE (chapter_id, ordinal_number),
  UNIQUE (clause_id, chapter_id)
);

CREATE TABLE clause_version (
  clause_version_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  state_order INTEGER NOT NULL CHECK (state_order >= 0),
  display_title TEXT NOT NULL,
  representative_raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  representative_raw_sha256 TEXT NOT NULL CHECK (
    length(representative_raw_sha256) = 64
  ),
  normalized_sha256 TEXT NOT NULL CHECK (
    length(normalized_sha256) = 64
  ),
  comparison_sha256 TEXT NOT NULL CHECK (
    length(comparison_sha256) = 64
  ),
  extractor_version TEXT NOT NULL,
  legal_effective_status TEXT NOT NULL CHECK (
    legal_effective_status IN (
      'not_claimed', 'candidate_unresolved', 'verified'
    )
  ),
  UNIQUE (clause_id, state_order),
  UNIQUE (clause_id, clause_version_id)
);

CREATE TABLE clause_version_observation (
  observation_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL,
  clause_version_id TEXT NOT NULL,
  source_edition_version_id TEXT NOT NULL
    REFERENCES source_edition(source_edition_version_id),
  first_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  chronology_order INTEGER NOT NULL CHECK (chronology_order >= 0),
  edition_label TEXT NOT NULL,
  source_designation_raw TEXT NOT NULL,
  source_order_start INTEGER NOT NULL CHECK (source_order_start >= 0),
  source_order_end INTEGER NOT NULL CHECK (
    source_order_end >= source_order_start
  ),
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  normalized_sha256 TEXT NOT NULL CHECK (length(normalized_sha256) = 64),
  source_locator TEXT NOT NULL,
  FOREIGN KEY (clause_id, clause_version_id)
    REFERENCES clause_version(clause_id, clause_version_id),
  UNIQUE (clause_id, source_edition_version_id),
  UNIQUE (clause_version_id, source_edition_version_id),
  UNIQUE (clause_version_id, observation_id)
);

CREATE TABLE clause_version_block (
  block_id TEXT PRIMARY KEY,
  clause_version_id TEXT NOT NULL REFERENCES clause_version(clause_version_id),
  representative_observation_id TEXT NOT NULL,
  block_order INTEGER NOT NULL CHECK (block_order >= 0),
  block_kind TEXT NOT NULL,
  structural_path TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  comparison_key TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  source_locator TEXT NOT NULL,
  FOREIGN KEY (clause_version_id, representative_observation_id)
    REFERENCES clause_version_observation(
      clause_version_id, observation_id
    ),
  UNIQUE (clause_version_id, block_order),
  UNIQUE (clause_version_id, block_id)
);

CREATE TABLE clause_version_date (
  date_fact_id TEXT PRIMARY KEY,
  clause_version_id TEXT NOT NULL REFERENCES clause_version(clause_version_id),
  representative_observation_id TEXT NOT NULL
    REFERENCES clause_version_observation(observation_id),
  date_role TEXT NOT NULL CHECK (
    date_role = 'text_amendment_annotation'
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
      'candidate_unresolved', 'verified', 'rejected_non_date'
    )
  ),
  source_locator TEXT NOT NULL,
  CHECK (date_precision = 'unknown' OR date_value IS NOT NULL)
);

CREATE TABLE clause_version_edge (
  edge_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  older_clause_version_id TEXT NOT NULL,
  newer_clause_version_id TEXT NOT NULL UNIQUE,
  adjacency_basis TEXT NOT NULL CHECK (
    adjacency_basis =
      'adjacent_distinct_text_state_across_official_editions'
  ),
  legal_predecessor_status TEXT NOT NULL CHECK (
    legal_predecessor_status IN ('not_claimed', 'verified')
  ),
  crosses_known_gap INTEGER NOT NULL CHECK (crosses_known_gap IN (0, 1)),
  older_last_observed_order INTEGER NOT NULL CHECK (
    older_last_observed_order >= 0
  ),
  newer_first_observed_order INTEGER NOT NULL CHECK (
    newer_first_observed_order > older_last_observed_order
  ),
  algorithm_version TEXT NOT NULL,
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  change_hunk_count INTEGER NOT NULL CHECK (change_hunk_count >= 1),
  status TEXT NOT NULL CHECK (
    status IN ('verified_source_edition_diff', 'ambiguous', 'blocked')
  ),
  FOREIGN KEY (clause_id, older_clause_version_id)
    REFERENCES clause_version(clause_id, clause_version_id),
  FOREIGN KEY (clause_id, newer_clause_version_id)
    REFERENCES clause_version(clause_id, clause_version_id),
  CHECK (older_clause_version_id <> newer_clause_version_id)
);

CREATE TABLE clause_diff_hunk (
  hunk_id TEXT PRIMARY KEY,
  edge_id TEXT NOT NULL REFERENCES clause_version_edge(edge_id),
  hunk_order INTEGER NOT NULL CHECK (hunk_order >= 0),
  change_kind TEXT NOT NULL CHECK (
    change_kind IN ('added', 'removed', 'replaced')
  ),
  context_label TEXT NOT NULL,
  old_block_id TEXT REFERENCES clause_version_block(block_id),
  new_block_id TEXT REFERENCES clause_version_block(block_id),
  old_text TEXT,
  new_text TEXT,
  old_text_sha256 TEXT,
  new_text_sha256 TEXT,
  inline_segments TEXT NOT NULL,
  display_note TEXT NOT NULL,
  UNIQUE (edge_id, hunk_order),
  CHECK (old_text IS NOT NULL OR new_text IS NOT NULL)
);

CREATE TABLE diff_run (
  run_id TEXT PRIMARY KEY,
  clause_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  algorithm_version TEXT NOT NULL,
  ignored_change_policy TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state = 'sealed'),
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  hunk_count INTEGER NOT NULL CHECK (hunk_count >= 0),
  started_at TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  UNIQUE (clause_import_run_id, algorithm_version)
);

CREATE TABLE diff_hunk_presentation (
  diff_run_id TEXT NOT NULL REFERENCES diff_run(run_id),
  hunk_id TEXT NOT NULL REFERENCES clause_diff_hunk(hunk_id),
  semantic_change_kind TEXT NOT NULL CHECK (
    semantic_change_kind IN (
      'added', 'removed', 'replaced', 'format_only'
    )
  ),
  inline_segments TEXT NOT NULL,
  ignored_change_classes TEXT NOT NULL,
  display_note TEXT NOT NULL,
  PRIMARY KEY (diff_run_id, hunk_id)
);

CREATE TABLE reader_enrichment_run (
  run_id TEXT PRIMARY KEY,
  clause_import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  diff_run_id TEXT NOT NULL REFERENCES diff_run(run_id),
  generator_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state = 'sealed'),
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
  semantic_tag_count INTEGER NOT NULL CHECK (semantic_tag_count >= 0),
  tag_atc_count INTEGER NOT NULL CHECK (tag_atc_count >= 0),
  tag_icd11_lookup_count INTEGER NOT NULL CHECK (
    tag_icd11_lookup_count >= 0
  ),
  condition_marker_count INTEGER NOT NULL CHECK (
    condition_marker_count >= 0
  ),
  summary_count INTEGER NOT NULL CHECK (summary_count >= 0),
  started_at TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  UNIQUE (clause_import_run_id, diff_run_id, generator_version)
);

CREATE TABLE clause_semantic_tag (
  enrichment_run_id TEXT NOT NULL REFERENCES reader_enrichment_run(run_id),
  tag_id TEXT NOT NULL,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  tag_text TEXT NOT NULL,
  normalized_tag TEXT NOT NULL,
  display_text TEXT NOT NULL,
  tag_type TEXT NOT NULL CHECK (tag_type IN ('drug', 'disease')),
  entity_type TEXT NOT NULL CHECK (
    entity_type IN (
      'ingredient', 'brand', 'drug_class', 'abbreviation',
      'disease', 'clinical_condition'
    )
  ),
  match_mode TEXT NOT NULL CHECK (match_mode = 'exact_case_insensitive'),
  resolution_status TEXT NOT NULL CHECK (
    resolution_status IN (
      'resolved_atc', 'multiple_atc',
      'official_lookup_only', 'terminology_pending'
    )
  ),
  provenance TEXT NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id),
  UNIQUE (enrichment_run_id, clause_id, normalized_tag)
);

CREATE TABLE clause_semantic_tag_atc (
  enrichment_run_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  atc_code TEXT NOT NULL,
  is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
  mapping_basis TEXT NOT NULL CHECK (
    mapping_basis IN (
      'nhi_product_name_atc_match',
      'nhi_atc_reference_plus_clause_context',
      'nhi_rule_group_mapping'
    )
  ),
  evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
  source_updated_at TEXT,
  source_url TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (
    review_status IN ('agent_verified', 'agent_curated')
  ),
  PRIMARY KEY (enrichment_run_id, tag_id, atc_code),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES clause_semantic_tag(enrichment_run_id, tag_id)
);

CREATE TABLE clause_semantic_tag_icd11_lookup (
  enrichment_run_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  lookup_query TEXT NOT NULL,
  official_lookup_url TEXT NOT NULL,
  icd11_release TEXT NOT NULL,
  language_tag TEXT NOT NULL,
  mapping_status TEXT NOT NULL CHECK (
    mapping_status = 'official_lookup_only_no_crosswalk'
  ),
  icd11_code TEXT CHECK (icd11_code IS NULL),
  icd11_uri TEXT CHECK (icd11_uri IS NULL),
  license_note TEXT NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES clause_semantic_tag(enrichment_run_id, tag_id)
);

CREATE TABLE clause_semantic_tag_icd11_code (
  enrichment_run_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  candidate_rank INTEGER NOT NULL CHECK (candidate_rank >= 1),
  icd11_code TEXT NOT NULL,
  mapping_status TEXT NOT NULL CHECK (
    mapping_status IN ('agent_selected', 'candidate')
  ),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  display_note TEXT NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id, candidate_rank),
  UNIQUE (enrichment_run_id, tag_id, icd11_code),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES clause_semantic_tag(enrichment_run_id, tag_id)
);

CREATE TABLE clause_condition_marker (
  enrichment_run_id TEXT NOT NULL REFERENCES reader_enrichment_run(run_id),
  marker_id TEXT NOT NULL,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  marker_text TEXT NOT NULL,
  normalized_marker TEXT NOT NULL,
  semantic_role TEXT NOT NULL CHECK (
    semantic_role IN (
      'restriction', 'maximum', 'prohibition', 'requirement',
      'conjunction', 'logical', 'duration', 'exception',
      'prior_authorization'
    )
  ),
  match_mode TEXT NOT NULL CHECK (match_mode = 'exact_longest_first'),
  provenance TEXT NOT NULL,
  PRIMARY KEY (enrichment_run_id, marker_id),
  UNIQUE (enrichment_run_id, clause_id, normalized_marker)
);

CREATE TABLE agent_history_summary (
  enrichment_run_id TEXT NOT NULL REFERENCES reader_enrichment_run(run_id),
  summary_id TEXT NOT NULL,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  language_tag TEXT NOT NULL CHECK (language_tag = 'zh-TW'),
  summary_markdown TEXT NOT NULL,
  source_edge_ids TEXT NOT NULL,
  source_diff_sha256 TEXT NOT NULL CHECK (length(source_diff_sha256) = 64),
  generation_method TEXT NOT NULL CHECK (
    generation_method = 'pure_agentic_from_structured_diff'
  ),
  generator_id TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (
    review_status IN ('agent_generated_unreviewed', 'human_reviewed')
  ),
  created_at TEXT NOT NULL,
  PRIMARY KEY (enrichment_run_id, summary_id),
  UNIQUE (enrichment_run_id, clause_id)
);

CREATE TABLE coverage_assessment (
  assessment_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL REFERENCES clause(clause_id),
  import_run_id TEXT NOT NULL REFERENCES import_run(run_id),
  declared_edition_count INTEGER NOT NULL CHECK (
    declared_edition_count >= 1
  ),
  observed_edition_count INTEGER NOT NULL CHECK (
    observed_edition_count >= 1
    AND observed_edition_count <= declared_edition_count
  ),
  first_observed_order INTEGER NOT NULL CHECK (first_observed_order >= 0),
  last_observed_order INTEGER NOT NULL CHECK (
    last_observed_order >= first_observed_order
  ),
  version_state_count INTEGER NOT NULL CHECK (version_state_count >= 1),
  unique_comparison_text_count INTEGER NOT NULL CHECK (
    unique_comparison_text_count >= 1
    AND unique_comparison_text_count <= version_state_count
  ),
  version_edge_count INTEGER NOT NULL CHECK (
    version_edge_count = version_state_count - 1
  ),
  observed_presence_contiguous INTEGER NOT NULL CHECK (
    observed_presence_contiguous IN (0, 1)
  ),
  declared_edition_set_complete INTEGER NOT NULL CHECK (
    declared_edition_set_complete IN (0, 1)
  ),
  official_source_universe_closed INTEGER NOT NULL CHECK (
    official_source_universe_closed IN (0, 1)
  ),
  legal_history_complete INTEGER NOT NULL CHECK (
    legal_history_complete IN (0, 1)
  ),
  status TEXT NOT NULL CHECK (
    status IN (
      'complete_for_declared_source_edition_observations',
      'incomplete_declared_source_edition_observations'
    )
  ),
  gap_reasons TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  UNIQUE (clause_id, import_run_id)
);

CREATE INDEX clause_observation_edition_idx
  ON clause_version_observation(source_edition_version_id, clause_id);
CREATE INDEX clause_observation_chronology_idx
  ON clause_version_observation(clause_id, chronology_order);
CREATE INDEX clause_version_date_lookup_idx
  ON clause_version_date(clause_version_id, date_value, date_fact_id);
CREATE INDEX clause_block_version_order_idx
  ON clause_version_block(clause_version_id, block_order);
CREATE INDEX clause_hunk_edge_order_idx
  ON clause_diff_hunk(edge_id, hunk_order);
CREATE INDEX diff_presentation_hunk_idx
  ON diff_hunk_presentation(hunk_id, diff_run_id);
CREATE INDEX clause_semantic_tag_lookup_idx
  ON clause_semantic_tag(
    enrichment_run_id, clause_id, normalized_tag
  );
