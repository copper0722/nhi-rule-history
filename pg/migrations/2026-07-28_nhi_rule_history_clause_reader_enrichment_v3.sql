-- 2026-07-28 — PG-canonical reader enrichment for one-clause pages
--
-- Drug-keyword/ATC mappings and agent-authored history summaries are
-- append-only projections tied to one sealed clause import and one sealed
-- semantic-diff run.  The browser never invents these rows.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-reader-enrichment-v3', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.reader_enrichment_run (
    run_id uuid PRIMARY KEY,
    clause_import_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.import_run (run_id)
      ON DELETE RESTRICT,
    diff_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.diff_run (run_id)
      ON DELETE RESTRICT,
    generator_version text NOT NULL,
    state text NOT NULL CHECK (state IN ('loading', 'sealed')),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    output_sha256 text CHECK (
      output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'
    ),
    semantic_tag_count integer CHECK (
      semantic_tag_count IS NULL OR semantic_tag_count >= 0
    ),
    tag_atc_count integer CHECK (
      tag_atc_count IS NULL OR tag_atc_count >= 0
    ),
    tag_icd11_lookup_count integer CHECK (
      tag_icd11_lookup_count IS NULL OR tag_icd11_lookup_count >= 0
    ),
    condition_marker_count integer CHECK (
      condition_marker_count IS NULL OR condition_marker_count >= 0
    ),
    summary_count integer CHECK (
      summary_count IS NULL OR summary_count >= 0
    ),
    started_at timestamptz NOT NULL,
    sealed_at timestamptz,
    UNIQUE (clause_import_run_id, diff_run_id, generator_version),
    CHECK (
      (state = 'loading'
        AND output_sha256 IS NULL
        AND semantic_tag_count IS NULL
        AND tag_atc_count IS NULL
        AND tag_icd11_lookup_count IS NULL
        AND condition_marker_count IS NULL
        AND summary_count IS NULL
        AND sealed_at IS NULL)
      OR
      (state = 'sealed'
        AND output_sha256 IS NOT NULL
        AND semantic_tag_count IS NOT NULL
        AND tag_atc_count IS NOT NULL
        AND tag_icd11_lookup_count IS NOT NULL
        AND condition_marker_count IS NOT NULL
        AND summary_count IS NOT NULL
        AND sealed_at IS NOT NULL)
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_semantic_tag (
    enrichment_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
      ON DELETE RESTRICT,
    tag_id text NOT NULL,
    clause_id text NOT NULL
      REFERENCES nhi_rule_history_clause.clause (clause_id)
      ON DELETE RESTRICT,
    tag_text text NOT NULL CHECK (tag_text <> ''),
    normalized_tag text NOT NULL CHECK (normalized_tag <> ''),
    display_text text NOT NULL CHECK (display_text <> ''),
    tag_type text NOT NULL CHECK (
      tag_type IN ('drug', 'disease')
    ),
    entity_type text NOT NULL CHECK (
      entity_type IN (
        'ingredient', 'brand', 'drug_class', 'abbreviation',
        'disease', 'clinical_condition'
      )
    ),
    match_mode text NOT NULL CHECK (
      match_mode IN ('exact_case_insensitive')
    ),
    resolution_status text NOT NULL CHECK (
      resolution_status IN (
        'resolved_atc', 'multiple_atc',
        'official_lookup_only', 'terminology_pending'
      )
    ),
    provenance jsonb NOT NULL CHECK (
      jsonb_typeof(provenance) = 'object'
      AND provenance <> '{}'::jsonb
    ),
    PRIMARY KEY (enrichment_run_id, tag_id),
    UNIQUE (enrichment_run_id, clause_id, normalized_tag)
  );

CREATE INDEX IF NOT EXISTS clause_semantic_tag_lookup_idx
  ON nhi_rule_history_clause.clause_semantic_tag (
    enrichment_run_id, clause_id, normalized_tag
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_semantic_tag_atc (
    enrichment_run_id uuid NOT NULL,
    tag_id text NOT NULL,
    atc_code text NOT NULL CHECK (
      atc_code ~ '^[A-Z][0-9A-Z]{0,6}$'
    ),
    is_primary boolean NOT NULL,
    mapping_basis text NOT NULL CHECK (
      mapping_basis IN (
        'nhi_product_name_atc_match',
        'nhi_atc_reference_plus_clause_context',
        'nhi_rule_group_mapping'
      )
    ),
    evidence_count integer NOT NULL CHECK (evidence_count >= 0),
    source_updated_at text,
    source_url text NOT NULL CHECK (source_url ~ '^https://'),
    review_status text NOT NULL CHECK (
      review_status IN ('agent_verified', 'agent_curated')
    ),
    PRIMARY KEY (enrichment_run_id, tag_id, atc_code),
    FOREIGN KEY (enrichment_run_id, tag_id)
      REFERENCES nhi_rule_history_clause.clause_semantic_tag (
        enrichment_run_id, tag_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_lookup (
    enrichment_run_id uuid NOT NULL,
    tag_id text NOT NULL,
    lookup_query text NOT NULL CHECK (lookup_query <> ''),
    official_lookup_url text NOT NULL CHECK (
      official_lookup_url ~ '^https://icd[.]who[.]int/'
    ),
    icd11_release text NOT NULL,
    language_tag text NOT NULL,
    mapping_status text NOT NULL CHECK (
      mapping_status = 'official_lookup_only_no_crosswalk'
    ),
    icd11_code text,
    icd11_uri text,
    license_note text NOT NULL,
    PRIMARY KEY (enrichment_run_id, tag_id),
    FOREIGN KEY (enrichment_run_id, tag_id)
      REFERENCES nhi_rule_history_clause.clause_semantic_tag (
        enrichment_run_id, tag_id
      )
      ON DELETE RESTRICT,
    CHECK (
      mapping_status <> 'official_lookup_only_no_crosswalk'
      OR (icd11_code IS NULL AND icd11_uri IS NULL)
    )
  );

-- Private runtime crosswalk.  Its rows are intentionally excluded from every
-- Git/JSONL/SQLite/browser exporter.
CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_private (
    enrichment_run_id uuid NOT NULL,
    tag_id text NOT NULL,
    candidate_rank integer NOT NULL CHECK (candidate_rank >= 1),
    mapping_status text NOT NULL CHECK (
      mapping_status IN (
        'agent_selected', 'candidate', 'ambiguous', 'unmapped'
      )
    ),
    icd11_code text,
    icd11_title text,
    icd11_uri text,
    icd11_release text NOT NULL,
    confidence numeric(5, 4) NOT NULL CHECK (
      confidence >= 0 AND confidence <= 1
    ),
    rationale text NOT NULL,
    source_table text NOT NULL CHECK (
      source_table = 'medical_knowledge.icd11_who'
    ),
    verified_at timestamptz NOT NULL,
    PRIMARY KEY (enrichment_run_id, tag_id, candidate_rank),
    FOREIGN KEY (enrichment_run_id, tag_id)
      REFERENCES nhi_rule_history_clause.clause_semantic_tag (
        enrichment_run_id, tag_id
      )
      ON DELETE RESTRICT,
    CHECK (
      (mapping_status IN ('agent_selected', 'candidate')
        AND icd11_code IS NOT NULL
        AND icd11_title IS NOT NULL
        AND icd11_uri IS NOT NULL)
      OR
      (mapping_status IN ('ambiguous', 'unmapped')
        AND icd11_code IS NULL
        AND icd11_title IS NULL
        AND icd11_uri IS NULL)
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_condition_marker (
    enrichment_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
      ON DELETE RESTRICT,
    marker_id text NOT NULL,
    clause_id text NOT NULL
      REFERENCES nhi_rule_history_clause.clause (clause_id)
      ON DELETE RESTRICT,
    marker_text text NOT NULL CHECK (marker_text <> ''),
    normalized_marker text NOT NULL CHECK (normalized_marker <> ''),
    semantic_role text NOT NULL CHECK (
      semantic_role IN (
        'restriction', 'maximum', 'prohibition', 'requirement',
        'conjunction', 'exception', 'prior_authorization'
      )
    ),
    match_mode text NOT NULL CHECK (
      match_mode = 'exact_longest_first'
    ),
    provenance jsonb NOT NULL CHECK (
      jsonb_typeof(provenance) = 'object'
      AND provenance <> '{}'::jsonb
    ),
    PRIMARY KEY (enrichment_run_id, marker_id),
    UNIQUE (enrichment_run_id, clause_id, normalized_marker)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.agent_history_summary (
    enrichment_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
      ON DELETE RESTRICT,
    summary_id text NOT NULL,
    clause_id text NOT NULL
      REFERENCES nhi_rule_history_clause.clause (clause_id)
      ON DELETE RESTRICT,
    language_tag text NOT NULL CHECK (language_tag = 'zh-TW'),
    summary_markdown text NOT NULL CHECK (summary_markdown <> ''),
    source_edge_ids jsonb NOT NULL CHECK (
      jsonb_typeof(source_edge_ids) = 'array'
      AND jsonb_array_length(source_edge_ids) >= 1
    ),
    source_diff_sha256 text NOT NULL CHECK (
      source_diff_sha256 ~ '^[0-9a-f]{64}$'
    ),
    generation_method text NOT NULL CHECK (
      generation_method = 'pure_agentic_from_structured_diff'
    ),
    generator_id text NOT NULL,
    review_status text NOT NULL CHECK (
      review_status IN (
        'agent_generated_unreviewed', 'human_reviewed'
      )
    ),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (enrichment_run_id, summary_id),
    UNIQUE (enrichment_run_id, clause_id)
  );

COMMIT;
