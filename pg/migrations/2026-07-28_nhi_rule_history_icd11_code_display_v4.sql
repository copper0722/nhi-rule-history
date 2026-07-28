-- Public reader projection for project-authored disease -> ICD-11 code
-- relations. This intentionally excludes WHO titles, URIs, definitions, and
-- the private ICD reference snapshot.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-icd11-code-display-v4', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_clause.clause_semantic_tag_icd11_code (
    enrichment_run_id uuid NOT NULL,
    tag_id text NOT NULL,
    candidate_rank integer NOT NULL CHECK (candidate_rank >= 1),
    icd11_code text NOT NULL CHECK (
      icd11_code ~ '^[0-9A-Z][0-9A-Z.]*$'
    ),
    mapping_status text NOT NULL CHECK (
      mapping_status IN ('agent_selected', 'candidate')
    ),
    confidence numeric(5, 4) NOT NULL CHECK (
      confidence >= 0 AND confidence <= 1
    ),
    display_note text NOT NULL CHECK (display_note <> ''),
    PRIMARY KEY (
      enrichment_run_id, tag_id, candidate_rank
    ),
    UNIQUE (
      enrichment_run_id, tag_id, icd11_code
    ),
    FOREIGN KEY (enrichment_run_id, tag_id)
      REFERENCES nhi_rule_history_clause.clause_semantic_tag (
        enrichment_run_id, tag_id
      )
      ON DELETE RESTRICT
  );

COMMENT ON TABLE
  nhi_rule_history_clause.clause_semantic_tag_icd11_code
IS
  'Project-authored code-only disease links for reader display; no WHO titles, URIs, definitions, or ICD reference content.';

COMMIT;
