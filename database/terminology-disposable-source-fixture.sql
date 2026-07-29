-- Minimal canonical-source surface for disposable terminology migration/load
-- verification.  Populate it only from an already sealed PostgreSQL source.

BEGIN;

CREATE SCHEMA nhi_rule_history_publication;
CREATE SCHEMA nhi_rule_history_clause;
CREATE SCHEMA tw_drug;
CREATE SCHEMA medical_knowledge;
CREATE SCHEMA tw_health_open;

CREATE TABLE nhi_rule_history_publication.publication_run (
  run_id uuid PRIMARY KEY,
  state text NOT NULL,
  sealed_fingerprint text NOT NULL
);

CREATE TABLE nhi_rule_history_publication.current_clause (
  run_id uuid NOT NULL,
  clause_code text NOT NULL,
  PRIMARY KEY (run_id, clause_code)
);

CREATE TABLE nhi_rule_history_publication.current_clause_block (
  run_id uuid NOT NULL,
  clause_code text NOT NULL,
  block_order integer NOT NULL,
  source_block_id text NOT NULL,
  raw_text text NOT NULL,
  raw_text_sha256 text NOT NULL,
  PRIMARY KEY (run_id, clause_code, block_order),
  FOREIGN KEY (run_id, clause_code)
    REFERENCES nhi_rule_history_publication.current_clause
      (run_id, clause_code)
);

CREATE TABLE nhi_rule_history_publication.publication_activation (
  activation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_publication.publication_run (run_id)
);

CREATE VIEW nhi_rule_history_publication.v_active_publication_run AS
SELECT run.*
FROM nhi_rule_history_publication.publication_activation activation
JOIN nhi_rule_history_publication.publication_run run
  ON run.run_id = activation.run_id
ORDER BY activation.activation_id DESC
LIMIT 1;

CREATE TABLE nhi_rule_history_clause.reader_enrichment_run (
  run_id uuid PRIMARY KEY,
  state text NOT NULL,
  output_sha256 text NOT NULL,
  sealed_at timestamptz NOT NULL
);

CREATE TABLE nhi_rule_history_clause.clause_semantic_tag (
  enrichment_run_id uuid NOT NULL,
  tag_id text NOT NULL,
  tag_text text NOT NULL,
  tag_type text NOT NULL,
  entity_type text NOT NULL,
  resolution_status text NOT NULL,
  provenance jsonb NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id),
  FOREIGN KEY (enrichment_run_id)
    REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id)
);

CREATE TABLE nhi_rule_history_clause.clause_semantic_tag_atc (
  enrichment_run_id uuid NOT NULL,
  tag_id text NOT NULL,
  atc_code text NOT NULL,
  mapping_basis text NOT NULL,
  review_status text NOT NULL,
  source_updated_at text,
  PRIMARY KEY (enrichment_run_id, tag_id, atc_code),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES nhi_rule_history_clause.clause_semantic_tag
      (enrichment_run_id, tag_id)
);

CREATE TABLE nhi_rule_history_clause.clause_semantic_tag_icd11_lookup (
  enrichment_run_id uuid NOT NULL,
  tag_id text NOT NULL,
  icd11_release text NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES nhi_rule_history_clause.clause_semantic_tag
      (enrichment_run_id, tag_id)
);

CREATE TABLE nhi_rule_history_clause.clause_semantic_tag_icd11_code (
  enrichment_run_id uuid NOT NULL,
  tag_id text NOT NULL,
  icd11_code text NOT NULL,
  mapping_status text NOT NULL,
  PRIMARY KEY (enrichment_run_id, tag_id, icd11_code),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES nhi_rule_history_clause.clause_semantic_tag
      (enrichment_run_id, tag_id)
);

CREATE TABLE nhi_rule_history_clause.clause_semantic_tag_nhi_treatment (
  enrichment_run_id uuid NOT NULL,
  tag_id text NOT NULL,
  treatment_code text NOT NULL,
  mapping_basis text NOT NULL,
  review_status text NOT NULL,
  source_resource_modified date,
  PRIMARY KEY (enrichment_run_id, tag_id, treatment_code),
  FOREIGN KEY (enrichment_run_id, tag_id)
    REFERENCES nhi_rule_history_clause.clause_semantic_tag
      (enrichment_run_id, tag_id)
);

CREATE TABLE tw_drug.ref_atc (
  atc_code text PRIMARY KEY
);

CREATE TABLE medical_knowledge.icd11_who (
  code text NOT NULL,
  release_id text NOT NULL,
  PRIMARY KEY (code, release_id)
);

CREATE TABLE tw_health_open.nhi_payment_standard (
  code text NOT NULL,
  end_date date
);

COMMIT;
