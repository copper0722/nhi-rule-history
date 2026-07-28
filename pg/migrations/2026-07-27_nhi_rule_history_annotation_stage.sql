-- 2026-07-27 — isolated legacy current-text date-annotation stage
--
-- This schema preserves exact legacy article text and source-local ROC date
-- markers as nonauthoritative evidence.  Every marker begins unresolved.  No
-- object in this schema promotes, mutates, or references canonical rule rows.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_annotation_stage-global', 0)
);

DO $schema_guard$
DECLARE
  managed_comment text :=
    'Isolated append-only legacy date-marker stage; not legal history. managed=nhi_rule_history_annotation_stage/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_annotation_stage'
  ) THEN
    CREATE SCHEMA nhi_rule_history_annotation_stage;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_annotation_stage IS %L',
      managed_comment
    );
  ELSE
    SELECT obj_description(n.oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace n
    WHERE n.nspname = 'nhi_rule_history_annotation_stage';
    IF existing_comment IS DISTINCT FROM managed_comment THEN
      RAISE EXCEPTION
        'nhi_rule_history_annotation_stage exists without the managed v1 marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $domain_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'nhi_rule_history_annotation_stage'
      AND t.typname = 'sha256_hex'
      AND t.typtype = 'd'
  ) THEN
    CREATE DOMAIN nhi_rule_history_annotation_stage.sha256_hex AS text
      CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END;
$domain_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_annotation_stage.annotation_run (
    run_id uuid PRIMARY KEY,
    contract_version text NOT NULL,
    extractor_version text NOT NULL,
    migration_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    code_sha256 nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    input_fingerprint
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL UNIQUE,
    output_fingerprint
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    sealed_fingerprint
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL UNIQUE,
    article_count integer NOT NULL CHECK (article_count >= 0),
    article_with_annotation_count integer NOT NULL
      CHECK (
        article_with_annotation_count >= 0
        AND article_with_annotation_count <= article_count
      ),
    annotation_count integer NOT NULL CHECK (annotation_count >= 0),
    normalized_annotation_count integer NOT NULL
      CHECK (
        normalized_annotation_count >= 0
        AND normalized_annotation_count <= annotation_count
      ),
    unresolved_annotation_count integer NOT NULL
      CHECK (
        unresolved_annotation_count >= 0
        AND unresolved_annotation_count = annotation_count
      ),
    expected_counts jsonb NOT NULL
      CHECK (jsonb_typeof(expected_counts) = 'object'),
    table_fingerprints jsonb NOT NULL
      CHECK (jsonb_typeof(table_fingerprints) = 'object'),
    state text NOT NULL CHECK (state = 'sealed'),
    created_at timestamptz NOT NULL DEFAULT current_timestamp,
    sealed_at timestamptz NOT NULL DEFAULT current_timestamp
  );

COMMENT ON TABLE
  nhi_rule_history_annotation_stage.annotation_run IS
  'One immutable extraction run over caller-supplied legacy article records.';

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_annotation_stage.legacy_article_observation (
    run_id uuid NOT NULL
      REFERENCES nhi_rule_history_annotation_stage.annotation_run (run_id),
    article_id text NOT NULL,
    article_num text NOT NULL,
    source_identity jsonb NOT NULL,
    source_identity_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    full_text text NOT NULL,
    full_text_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    annotation_count integer NOT NULL CHECK (annotation_count >= 0),
    caller_record_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, article_id),
    UNIQUE (run_id, source_row_sha256)
  );

COMMENT ON TABLE
  nhi_rule_history_annotation_stage.legacy_article_observation IS
  'Exact caller-supplied legacy current text plus explicit source identity.';

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_annotation_stage.date_annotation (
    run_id uuid NOT NULL,
    annotation_id uuid NOT NULL,
    article_id text NOT NULL,
    marker_ordinal integer NOT NULL CHECK (marker_ordinal >= 0),
    char_start integer NOT NULL CHECK (char_start >= 0),
    char_end integer NOT NULL CHECK (char_end > char_start),
    raw_expression text NOT NULL,
    raw_expression_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    roc_year integer NOT NULL CHECK (roc_year BETWEEN 0 AND 999),
    roc_month integer NOT NULL CHECK (roc_month BETWEEN 0 AND 99),
    roc_day integer NOT NULL CHECK (roc_day BETWEEN 0 AND 99),
    normalized_iso_candidate date,
    normalization_status text NOT NULL CHECK (
      normalization_status IN ('normalized', 'invalid_calendar_date')
    ),
    resolution_status text NOT NULL CHECK (
      resolution_status = 'unresolved_event'
    ),
    unresolved_reason text NOT NULL,
    source_row_sha256
      nhi_rule_history_annotation_stage.sha256_hex NOT NULL,
    PRIMARY KEY (run_id, annotation_id),
    FOREIGN KEY (run_id, article_id)
      REFERENCES
        nhi_rule_history_annotation_stage.legacy_article_observation
        (run_id, article_id),
    UNIQUE (run_id, article_id, marker_ordinal),
    UNIQUE (
      run_id, article_id, char_start, char_end, raw_expression_sha256
    ),
    CHECK (char_length(raw_expression) = char_end - char_start),
    CHECK (
      (normalization_status = 'normalized'
       AND normalized_iso_candidate IS NOT NULL)
      OR
      (normalization_status = 'invalid_calendar_date'
       AND normalized_iso_candidate IS NULL)
    )
  );

COMMENT ON TABLE
  nhi_rule_history_annotation_stage.date_annotation IS
  'One exact source-local ROC date expression. Initial event resolution is always unresolved.';

CREATE INDEX IF NOT EXISTS date_annotation_rule_date_idx
  ON nhi_rule_history_annotation_stage.date_annotation (
    run_id, article_id, normalized_iso_candidate
  );

CREATE OR REPLACE VIEW
  nhi_rule_history_annotation_stage.v_rule_date_coverage AS
SELECT
  article.run_id,
  article.article_id,
  article.article_num,
  article.source_identity,
  article.source_identity_sha256,
  article.full_text_sha256,
  article.caller_record_sha256,
  article.annotation_count,
  COALESCE(markers.normalized_annotation_count, 0)
    AS normalized_annotation_count,
  COALESCE(markers.distinct_normalized_date_count, 0)
    AS distinct_normalized_date_count,
  COALESCE(markers.unresolved_annotation_count, 0)
    AS unresolved_annotation_count,
  COALESCE(markers.invalid_calendar_count, 0)
    AS invalid_calendar_count,
  markers.earliest_iso_candidate,
  markers.latest_iso_candidate,
  COALESCE(markers.date_markers, '[]'::jsonb) AS date_markers,
  CASE
    WHEN article.annotation_count = 0
      THEN 'no_date_marker_observed'
    WHEN COALESCE(markers.invalid_calendar_count, 0) > 0
      THEN 'normalization_blocked'
    ELSE 'unresolved_event_linkage'
  END AS coverage_status
FROM nhi_rule_history_annotation_stage.legacy_article_observation article
LEFT JOIN LATERAL (
  SELECT
    count(annotation.normalized_iso_candidate)
      AS normalized_annotation_count,
    count(DISTINCT annotation.normalized_iso_candidate)
      AS distinct_normalized_date_count,
    count(*) FILTER (
      WHERE annotation.resolution_status = 'unresolved_event'
    ) AS unresolved_annotation_count,
    count(*) FILTER (
      WHERE annotation.normalization_status = 'invalid_calendar_date'
    ) AS invalid_calendar_count,
    min(annotation.normalized_iso_candidate) AS earliest_iso_candidate,
    max(annotation.normalized_iso_candidate) AS latest_iso_candidate,
    jsonb_agg(
      jsonb_build_object(
        'annotation_id', annotation.annotation_id,
        'marker_ordinal', annotation.marker_ordinal,
        'char_start', annotation.char_start,
        'char_end', annotation.char_end,
        'raw_expression', annotation.raw_expression,
        'normalized_iso_candidate',
          annotation.normalized_iso_candidate,
        'resolution_status', annotation.resolution_status
      )
      ORDER BY annotation.marker_ordinal
    ) AS date_markers
  FROM nhi_rule_history_annotation_stage.date_annotation annotation
  WHERE annotation.run_id = article.run_id
    AND annotation.article_id = article.article_id
) markers ON true;

COMMENT ON VIEW
  nhi_rule_history_annotation_stage.v_rule_date_coverage IS
  'Per-legacy-article date-marker coverage; never a complete-history claim.';

CREATE OR REPLACE FUNCTION
  nhi_rule_history_annotation_stage.reject_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION
    'nhi_rule_history_annotation_stage evidence is append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$function$;

DO $trigger_guard$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'annotation_run',
    'legacy_article_observation',
    'date_annotation'
  ]
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row
        ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_annotation_stage'
        AND relation_row.relname = table_name
        AND trigger_row.tgname =
          'reject_evidence_update_delete'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER reject_evidence_update_delete
         BEFORE UPDATE OR DELETE ON
           nhi_rule_history_annotation_stage.%I
         FOR EACH ROW EXECUTE FUNCTION
           nhi_rule_history_annotation_stage.reject_evidence_mutation()',
        table_name
      );
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row
        ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_annotation_stage'
        AND relation_row.relname = table_name
        AND trigger_row.tgname =
          'reject_evidence_truncate'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER reject_evidence_truncate
         BEFORE TRUNCATE ON
           nhi_rule_history_annotation_stage.%I
         FOR EACH STATEMENT EXECUTE FUNCTION
           nhi_rule_history_annotation_stage.reject_evidence_mutation()',
        table_name
      );
    END IF;
  END LOOP;
END;
$trigger_guard$;

REVOKE ALL ON SCHEMA nhi_rule_history_annotation_stage FROM PUBLIC;
REVOKE ALL ON ALL TABLES
  IN SCHEMA nhi_rule_history_annotation_stage FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS
  IN SCHEMA nhi_rule_history_annotation_stage FROM PUBLIC;

ALTER DEFAULT PRIVILEGES
  IN SCHEMA nhi_rule_history_annotation_stage
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES
  IN SCHEMA nhi_rule_history_annotation_stage
  REVOKE ALL ON FUNCTIONS FROM PUBLIC;

COMMIT;
