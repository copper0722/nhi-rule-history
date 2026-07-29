-- Portable read-only projection of one sealed PostgreSQL terminology run.
-- PostgreSQL remains canonical.  ICD-11 titles, URIs, definitions, and private
-- terminology snapshots are intentionally absent.

PRAGMA foreign_keys = ON;

CREATE TABLE tagging_run (
  tagging_run_id TEXT PRIMARY KEY,
  publication_run_id TEXT NOT NULL,
  seed_enrichment_run_id TEXT NOT NULL,
  alias_proposal_sha256 TEXT NOT NULL
    CHECK (length(alias_proposal_sha256) = 64),
  matcher_version TEXT NOT NULL,
  loader_version TEXT NOT NULL,
  offset_contract TEXT NOT NULL
    CHECK (
      offset_contract =
        'unicode_scalar_half_open+utf8_byte_half_open/v1'
    ),
  alias_admission_policy TEXT NOT NULL
    CHECK (
      alias_admission_policy = 'reviewed_source_observed_only/v1'
    ),
  input_fingerprint TEXT NOT NULL CHECK (length(input_fingerprint) = 64),
  expected_counts_json TEXT NOT NULL,
  verified_counts_json TEXT NOT NULL,
  verified_metrics_json TEXT NOT NULL,
  table_fingerprints_json TEXT NOT NULL,
  output_fingerprint TEXT NOT NULL CHECK (length(output_fingerprint) = 64),
  sealed_fingerprint TEXT NOT NULL CHECK (length(sealed_fingerprint) = 64),
  sealed_at TEXT NOT NULL
);

CREATE TABLE concept_registry (
  concept_id TEXT PRIMARY KEY,
  identity_basis TEXT NOT NULL UNIQUE,
  identity_version TEXT NOT NULL,
  created_from TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64)
);

CREATE TABLE run_concept (
  tagging_run_id TEXT NOT NULL
    REFERENCES tagging_run(tagging_run_id),
  concept_id TEXT NOT NULL
    REFERENCES concept_registry(concept_id),
  concept_type TEXT NOT NULL,
  canonical_label_zh TEXT,
  canonical_label_en TEXT,
  link_family TEXT NOT NULL,
  review_status TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, concept_id)
);

CREATE TABLE concept_seed_tag_link (
  tagging_run_id TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  legacy_tag_id TEXT NOT NULL,
  seed_enrichment_run_id TEXT NOT NULL,
  mapping_status TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, legacy_tag_id),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES run_concept(tagging_run_id, concept_id)
);

CREATE TABLE concept_alias (
  tagging_run_id TEXT NOT NULL,
  alias_id TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  alias_text TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  language_tag TEXT NOT NULL,
  alias_type TEXT NOT NULL,
  source_status TEXT NOT NULL,
  proposed_auto_match INTEGER NOT NULL CHECK (proposed_auto_match IN (0, 1)),
  match_rule TEXT NOT NULL,
  production_status TEXT NOT NULL
    CHECK (production_status IN ('admitted', 'candidate', 'blocked')),
  production_reason TEXT NOT NULL,
  ambiguity_note TEXT,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, alias_id),
  UNIQUE (tagging_run_id, alias_id, concept_id),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES run_concept(tagging_run_id, concept_id)
);

CREATE UNIQUE INDEX concept_alias_admitted_normalized_uniq
  ON concept_alias(tagging_run_id, normalized_alias)
  WHERE production_status = 'admitted';

CREATE TABLE concept_external_code (
  tagging_run_id TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  code_system TEXT NOT NULL
    CHECK (code_system IN ('ATC', 'ICD11', 'NHI_TREATMENT')),
  code TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  review_status TEXT NOT NULL,
  public_safe INTEGER NOT NULL CHECK (public_safe = 1),
  master_source TEXT NOT NULL,
  master_release TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, concept_id, code_system, code),
  FOREIGN KEY (tagging_run_id, concept_id)
    REFERENCES run_concept(tagging_run_id, concept_id)
);

CREATE TABLE tagging_run_block_input (
  tagging_run_id TEXT NOT NULL
    REFERENCES tagging_run(tagging_run_id),
  publication_run_id TEXT NOT NULL,
  clause_code TEXT NOT NULL,
  block_order INTEGER NOT NULL CHECK (block_order >= 0),
  source_block_id TEXT NOT NULL,
  source_block_sha256 TEXT NOT NULL CHECK (length(source_block_sha256) = 64),
  scan_status TEXT NOT NULL
    CHECK (scan_status IN ('scanned_no_match', 'scanned_with_match')),
  candidate_match_count INTEGER NOT NULL CHECK (candidate_match_count >= 0),
  admitted_match_count INTEGER NOT NULL CHECK (admitted_match_count >= 0),
  blocked_match_count INTEGER NOT NULL CHECK (blocked_match_count >= 0),
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, clause_code, block_order)
);

CREATE TABLE clause_occurrence (
  tagging_run_id TEXT NOT NULL,
  occurrence_id TEXT NOT NULL,
  publication_run_id TEXT NOT NULL,
  clause_code TEXT NOT NULL,
  block_order INTEGER NOT NULL,
  source_block_id TEXT NOT NULL,
  source_block_sha256 TEXT NOT NULL CHECK (length(source_block_sha256) = 64),
  concept_id TEXT NOT NULL,
  alias_id TEXT NOT NULL,
  start_scalar INTEGER NOT NULL CHECK (start_scalar >= 0),
  end_scalar INTEGER NOT NULL CHECK (end_scalar > start_scalar),
  start_utf8_byte INTEGER NOT NULL CHECK (start_utf8_byte >= 0),
  end_utf8_byte INTEGER NOT NULL CHECK (end_utf8_byte > start_utf8_byte),
  matched_text TEXT NOT NULL,
  matched_text_sha256 TEXT NOT NULL CHECK (length(matched_text_sha256) = 64),
  occurrence_status TEXT NOT NULL
    CHECK (occurrence_status IN ('candidate', 'admitted', 'blocked')),
  occurrence_reason TEXT NOT NULL,
  match_rule TEXT NOT NULL,
  source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256) = 64),
  PRIMARY KEY (tagging_run_id, occurrence_id),
  FOREIGN KEY (tagging_run_id, alias_id, concept_id)
    REFERENCES concept_alias(tagging_run_id, alias_id, concept_id),
  FOREIGN KEY (tagging_run_id, clause_code, block_order)
    REFERENCES tagging_run_block_input(
      tagging_run_id, clause_code, block_order
    )
);

CREATE INDEX clause_occurrence_lookup_idx
  ON clause_occurrence(
    tagging_run_id, clause_code, block_order, start_scalar
  );

CREATE TRIGGER clause_occurrence_admitted_no_overlap
BEFORE INSERT ON clause_occurrence
WHEN NEW.occurrence_status = 'admitted'
  AND EXISTS (
    SELECT 1
    FROM clause_occurrence existing
    WHERE existing.tagging_run_id = NEW.tagging_run_id
      AND existing.clause_code = NEW.clause_code
      AND existing.block_order = NEW.block_order
      AND existing.occurrence_status = 'admitted'
      AND NEW.start_scalar < existing.end_scalar
      AND existing.start_scalar < NEW.end_scalar
  )
BEGIN
  SELECT RAISE(ABORT, 'admitted terminology occurrences overlap');
END;
