-- 2026-07-28 — normalized, append-only FINT keyword crawl evidence
--
-- FINT is a keyword retrieval surface, not a published enumerable corpus.
-- These tables preserve the declared query frontier, every query-to-document
-- edge, complete official detail text, raw artifact fingerprints, attachments,
-- and blocking acquisition issues without claiming absolute source-universe
-- completeness.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-fint-keyword-crawl-v17', 0)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.fint_crawl_run (
  run_id uuid PRIMARY KEY,
  crawler_version text NOT NULL CHECK (crawler_version <> ''),
  input_sha256 text NOT NULL CHECK (
    input_sha256 ~ '^[0-9a-f]{64}$'
  ),
  start_date char(8) NOT NULL CHECK (start_date ~ '^[0-9]{8}$'),
  end_date char(8) NOT NULL CHECK (end_date ~ '^[0-9]{8}$'),
  valid_code text NOT NULL CHECK (valid_code = '3'),
  record_type text NOT NULL CHECK (record_type = 'etype_'),
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  seed_count integer CHECK (seed_count IS NULL OR seed_count >= 0),
  query_count integer CHECK (query_count IS NULL OR query_count >= 0),
  document_count integer CHECK (
    document_count IS NULL OR document_count >= 0
  ),
  snapshot_count integer CHECK (
    snapshot_count IS NULL OR snapshot_count >= 0
  ),
  match_count integer CHECK (match_count IS NULL OR match_count >= 0),
  attachment_count integer CHECK (
    attachment_count IS NULL OR attachment_count >= 0
  ),
  attachment_declaration_count integer CHECK (
    attachment_declaration_count IS NULL
    OR attachment_declaration_count >= 0
  ),
  observation_count integer CHECK (
    observation_count IS NULL OR observation_count >= 0
  ),
  issue_count integer CHECK (issue_count IS NULL OR issue_count >= 0),
  output_sha256 text CHECK (
    output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'
  ),
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  CHECK (
    (
      state = 'loading'
      AND seed_count IS NULL
      AND query_count IS NULL
      AND document_count IS NULL
      AND snapshot_count IS NULL
      AND match_count IS NULL
      AND attachment_count IS NULL
      AND attachment_declaration_count IS NULL
      AND observation_count IS NULL
      AND issue_count IS NULL
      AND output_sha256 IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND seed_count IS NOT NULL
      AND query_count IS NOT NULL
      AND document_count IS NOT NULL
      AND snapshot_count IS NOT NULL
      AND match_count IS NOT NULL
      AND attachment_count IS NOT NULL
      AND attachment_declaration_count IS NOT NULL
      AND observation_count IS NOT NULL
      AND issue_count IS NOT NULL
      AND output_sha256 IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  )
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.fint_document_number_identity (
    document_id text PRIMARY KEY CHECK (
      document_id ~ '^[0-9a-f]{64}$'
    ),
    normalized_document_number text NOT NULL UNIQUE CHECK (
      normalized_document_number <> ''
    ),
    first_observed_raw_document_number text NOT NULL CHECK (
      first_observed_raw_document_number <> ''
    ),
    first_observed_at timestamptz NOT NULL
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.fint_query_seed (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
    ON DELETE RESTRICT,
  seed_id text NOT NULL CHECK (seed_id ~ '^[0-9a-f]{64}$'),
  query_id text NOT NULL CHECK (query_id ~ '^[0-9a-f]{64}$'),
  keywords jsonb NOT NULL CHECK (
    jsonb_typeof(keywords) = 'array'
    AND jsonb_array_length(keywords) BETWEEN 0 AND 4
  ),
  origin_kind text NOT NULL CHECK (origin_kind <> ''),
  origin_locator text NOT NULL CHECK (origin_locator <> ''),
  PRIMARY KEY (run_id, seed_id)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.fint_query (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
    ON DELETE RESTRICT,
  query_id text NOT NULL CHECK (query_id ~ '^[0-9a-f]{64}$'),
  keywords jsonb NOT NULL CHECK (
    jsonb_typeof(keywords) = 'array'
    AND jsonb_array_length(keywords) BETWEEN 0 AND 4
  ),
  normalized_keywords jsonb NOT NULL CHECK (
    jsonb_typeof(normalized_keywords) = 'array'
    AND jsonb_array_length(normalized_keywords)
      = jsonb_array_length(keywords)
  ),
  search_url text NOT NULL CHECK (
    search_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/FINT/FINTQRY03[.]aspx'
  ),
  search_observation_id text NOT NULL CHECK (
    search_observation_id ~ '^[0-9a-f]{64}$'
  ),
  search_page_observation_ids jsonb NOT NULL CHECK (
    jsonb_typeof(search_page_observation_ids) = 'array'
    AND jsonb_array_length(search_page_observation_ids) >= 1
  ),
  search_index_sha256 text NOT NULL CHECK (
    search_index_sha256 ~ '^[0-9a-f]{64}$'
  ),
  index_verified_at timestamptz NOT NULL,
  expected_rows integer NOT NULL CHECK (expected_rows >= 0),
  fetched_rows integer NOT NULL CHECK (
    fetched_rows >= 0 AND fetched_rows = expected_rows
  ),
  completed_at timestamptz NOT NULL,
  PRIMARY KEY (run_id, query_id)
);

ALTER TABLE nhi_rule_history_edition.fint_query_seed
  DROP CONSTRAINT IF EXISTS fint_query_seed_query_fk;
ALTER TABLE nhi_rule_history_edition.fint_query_seed
  ADD CONSTRAINT fint_query_seed_query_fk
  FOREIGN KEY (run_id, query_id)
  REFERENCES nhi_rule_history_edition.fint_query (run_id, query_id)
  ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.fint_crawl_observation (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
    ON DELETE RESTRICT,
  observation_id text NOT NULL CHECK (
    observation_id ~ '^[0-9a-f]{64}$'
  ),
  request_url text NOT NULL CHECK (
    request_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/'
  ),
  final_url text NOT NULL CHECK (final_url = request_url),
  resource_kind text NOT NULL CHECK (
    resource_kind IN (
      'fint_search_page', 'fint_detail_page', 'fint_attachment'
    )
  ),
  observed_at timestamptz NOT NULL,
  http_status integer NOT NULL CHECK (http_status = 200),
  response_headers jsonb NOT NULL CHECK (
    jsonb_typeof(response_headers) = 'object'
  ),
  content_sha256 text NOT NULL CHECK (
    content_sha256 ~ '^[0-9a-f]{64}$'
  ),
  byte_size bigint NOT NULL CHECK (byte_size > 0),
  raw_locator text NOT NULL CHECK (
    raw_locator ~ '^raw/sha256/[0-9a-f]{2}/[0-9a-f]{64}$'
  ),
  PRIMARY KEY (run_id, observation_id),
  UNIQUE (run_id, request_url)
);

ALTER TABLE nhi_rule_history_edition.fint_query
  DROP CONSTRAINT IF EXISTS fint_query_search_observation_fk;
ALTER TABLE nhi_rule_history_edition.fint_query
  ADD CONSTRAINT fint_query_search_observation_fk
  FOREIGN KEY (run_id, search_observation_id)
  REFERENCES nhi_rule_history_edition.fint_crawl_observation (
    run_id, observation_id
  )
  ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.fint_document_snapshot (
    run_id uuid NOT NULL
      REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
      ON DELETE RESTRICT,
    snapshot_id text NOT NULL CHECK (
      snapshot_id ~ '^[0-9a-f]{64}$'
    ),
    document_id text NOT NULL
      REFERENCES
        nhi_rule_history_edition.fint_document_number_identity (document_id)
      ON DELETE RESTRICT,
    detail_url text NOT NULL CHECK (
      detail_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/FINT/FINTQRY04[.]aspx'
    ),
    detail_observation_id text NOT NULL,
    record_text text NOT NULL CHECK (record_text <> ''),
    record_text_sha256 text NOT NULL CHECK (
      record_text_sha256 ~ '^[0-9a-f]{64}$'
    ),
    record_text_byte_size bigint NOT NULL CHECK (
      record_text_byte_size > 0
    ),
    record_text_raw_locator text NOT NULL CHECK (
      record_text_raw_locator
        ~ '^raw/sha256/[0-9a-f]{2}/[0-9a-f]{64}$'
    ),
    document_date_raw text,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, snapshot_id),
    UNIQUE (run_id, snapshot_id, document_id),
    FOREIGN KEY (run_id, detail_observation_id)
      REFERENCES nhi_rule_history_edition.fint_crawl_observation (
        run_id, observation_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.fint_query_document_match (
    run_id uuid NOT NULL
      REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
      ON DELETE RESTRICT,
    match_id text NOT NULL CHECK (match_id ~ '^[0-9a-f]{64}$'),
    query_id text NOT NULL,
    document_id text NOT NULL
      REFERENCES
        nhi_rule_history_edition.fint_document_number_identity (document_id)
      ON DELETE RESTRICT,
    snapshot_id text NOT NULL,
    row_number integer NOT NULL CHECK (row_number > 0),
    detail_url text NOT NULL CHECK (
      detail_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/FINT/FINTQRY04[.]aspx'
    ),
    detail_observation_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, match_id),
    UNIQUE (run_id, query_id, row_number),
    UNIQUE (run_id, match_id, snapshot_id, document_id),
    FOREIGN KEY (run_id, query_id)
      REFERENCES nhi_rule_history_edition.fint_query (run_id, query_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (run_id, snapshot_id, document_id)
      REFERENCES nhi_rule_history_edition.fint_document_snapshot (
        run_id, snapshot_id, document_id
      )
      ON DELETE RESTRICT,
    FOREIGN KEY (run_id, detail_observation_id)
      REFERENCES nhi_rule_history_edition.fint_crawl_observation (
        run_id, observation_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.fint_attachment_declaration (
    run_id uuid NOT NULL
      REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
      ON DELETE RESTRICT,
    attachment_declaration_id text NOT NULL CHECK (
      attachment_declaration_id ~ '^[0-9a-f]{64}$'
    ),
    document_id text NOT NULL
      REFERENCES
        nhi_rule_history_edition.fint_document_number_identity (document_id)
      ON DELETE RESTRICT,
    snapshot_id text NOT NULL,
    match_id text NOT NULL,
    source_url text NOT NULL CHECK (
      source_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/'
    ),
    source_label text NOT NULL CHECK (source_label <> ''),
    source_label_missing boolean NOT NULL,
    attachment_ordinal integer NOT NULL CHECK (attachment_ordinal > 0),
    fetch_state text NOT NULL CHECK (
      fetch_state IN (
        'fetched', 'not_selected_by_policy', 'fetch_failed'
      )
    ),
    PRIMARY KEY (run_id, attachment_declaration_id),
    UNIQUE (run_id, snapshot_id, attachment_ordinal, source_url),
    FOREIGN KEY (run_id, match_id, snapshot_id, document_id)
      REFERENCES nhi_rule_history_edition.fint_query_document_match (
        run_id, match_id, snapshot_id, document_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.fint_attachment_snapshot (
    run_id uuid NOT NULL
      REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
      ON DELETE RESTRICT,
    attachment_snapshot_id text NOT NULL CHECK (
      attachment_snapshot_id ~ '^[0-9a-f]{64}$'
    ),
    attachment_declaration_id text NOT NULL,
    observation_id text NOT NULL,
    content_sha256 text NOT NULL CHECK (
      content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    byte_size bigint NOT NULL CHECK (byte_size > 0),
    raw_locator text NOT NULL CHECK (
      raw_locator ~ '^raw/sha256/[0-9a-f]{2}/[0-9a-f]{64}$'
    ),
    source_media_type text,
    detected_media_type text,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, attachment_snapshot_id),
    UNIQUE (run_id, attachment_declaration_id),
    FOREIGN KEY (run_id, attachment_declaration_id)
      REFERENCES nhi_rule_history_edition.fint_attachment_declaration (
        run_id, attachment_declaration_id
      )
      ON DELETE RESTRICT,
    FOREIGN KEY (run_id, observation_id)
      REFERENCES nhi_rule_history_edition.fint_crawl_observation (
        run_id, observation_id
      )
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_edition.fint_crawl_issue (
  run_id uuid NOT NULL
    REFERENCES nhi_rule_history_edition.fint_crawl_run (run_id)
    ON DELETE RESTRICT,
  issue_id text NOT NULL CHECK (issue_id ~ '^[0-9a-f]{64}$'),
  request_url text NOT NULL CHECK (
    request_url ~ '^https://mohwlaw[.]mohw[.]gov[.]tw/'
  ),
  resource_kind text NOT NULL,
  error_code text NOT NULL,
  blocking boolean NOT NULL CHECK (blocking = true),
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (run_id, issue_id)
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history_edition.guard_fint_crawl_child_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  run_state text;
BEGIN
  SELECT state INTO STRICT run_state
  FROM nhi_rule_history_edition.fint_crawl_run
  WHERE run_id = NEW.run_id
  FOR SHARE;

  IF run_state <> 'loading' THEN
    RAISE EXCEPTION 'FINT crawl child rows require a loading run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_edition.reject_fint_crawl_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'FINT crawl evidence is append-only';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_edition.guard_fint_crawl_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed FINT crawl runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'FINT crawl run permits only loading to sealed';
  END IF;
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.crawler_version IS DISTINCT FROM OLD.crawler_version
     OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256
     OR NEW.start_date IS DISTINCT FROM OLD.start_date
     OR NEW.end_date IS DISTINCT FROM OLD.end_date
     OR NEW.valid_code IS DISTINCT FROM OLD.valid_code
     OR NEW.record_type IS DISTINCT FROM OLD.record_type
     OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'FINT crawl run identity fields are immutable';
  END IF;
  IF NEW.seed_count <> (
       SELECT count(*) FROM nhi_rule_history_edition.fint_query_seed
       WHERE run_id = OLD.run_id
     )
     OR NEW.query_count <> (
       SELECT count(*) FROM nhi_rule_history_edition.fint_query
       WHERE run_id = OLD.run_id
     )
     OR NEW.document_count <> (
       SELECT count(DISTINCT document_id)
       FROM nhi_rule_history_edition.fint_query_document_match
       WHERE run_id = OLD.run_id
     )
     OR NEW.snapshot_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_document_snapshot
       WHERE run_id = OLD.run_id
     )
     OR NEW.match_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_query_document_match
       WHERE run_id = OLD.run_id
     )
     OR NEW.attachment_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_attachment_snapshot
       WHERE run_id = OLD.run_id
     )
     OR NEW.attachment_declaration_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_attachment_declaration
       WHERE run_id = OLD.run_id
     )
     OR NEW.observation_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_crawl_observation
       WHERE run_id = OLD.run_id
     )
     OR NEW.issue_count <> (
       SELECT count(*)
       FROM nhi_rule_history_edition.fint_crawl_issue
       WHERE run_id = OLD.run_id
     ) THEN
    RAISE EXCEPTION 'FINT crawl seal counts do not match child rows';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_edition.fint_query AS q
    LEFT JOIN LATERAL (
      SELECT
        count(*) AS match_count,
        count(DISTINCT m.row_number) AS distinct_row_count,
        min(m.row_number) AS min_row,
        max(m.row_number) AS max_row
      FROM nhi_rule_history_edition.fint_query_document_match AS m
      WHERE m.run_id = q.run_id
        AND m.query_id = q.query_id
    ) AS s ON true
    WHERE q.run_id = OLD.run_id
      AND (
        s.match_count <> q.expected_rows
        OR s.distinct_row_count <> q.expected_rows
        OR (
          q.expected_rows > 0
          AND (s.min_row <> 1 OR s.max_row <> q.expected_rows)
        )
      )
  ) THEN
    RAISE EXCEPTION 'FINT crawl query rows are not contiguous 1..N';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_edition.fint_document_snapshot AS s
    WHERE s.run_id = OLD.run_id
      AND NOT EXISTS (
        SELECT 1
        FROM nhi_rule_history_edition.fint_query_document_match AS m
        WHERE m.run_id = s.run_id
          AND m.snapshot_id = s.snapshot_id
          AND m.document_id = s.document_id
      )
  ) THEN
    RAISE EXCEPTION 'FINT crawl contains an unmatched detail snapshot';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_edition.fint_attachment_declaration AS d
    LEFT JOIN nhi_rule_history_edition.fint_attachment_snapshot AS a
      ON a.run_id = d.run_id
     AND a.attachment_declaration_id = d.attachment_declaration_id
    WHERE d.run_id = OLD.run_id
      AND (
        (d.fetch_state = 'fetched' AND a.attachment_snapshot_id IS NULL)
        OR (
          d.fetch_state = 'not_selected_by_policy'
          AND a.attachment_snapshot_id IS NOT NULL
        )
        OR d.fetch_state = 'fetch_failed'
      )
  ) THEN
    RAISE EXCEPTION 'FINT attachment fetch-state parity failed';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_edition.fint_query_document_match AS m
    JOIN nhi_rule_history_edition.fint_crawl_observation AS o
      ON o.run_id = m.run_id
     AND o.observation_id = m.detail_observation_id
    WHERE m.run_id = OLD.run_id
      AND (
        o.resource_kind <> 'fint_detail_page'
        OR o.request_url <> m.detail_url
      )
  ) THEN
    RAISE EXCEPTION 'FINT detail observation binding failed';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_edition.fint_attachment_snapshot AS a
    JOIN nhi_rule_history_edition.fint_attachment_declaration AS d
      ON d.run_id = a.run_id
     AND d.attachment_declaration_id = a.attachment_declaration_id
    JOIN nhi_rule_history_edition.fint_crawl_observation AS o
      ON o.run_id = a.run_id
     AND o.observation_id = a.observation_id
    WHERE a.run_id = OLD.run_id
      AND (
        o.resource_kind <> 'fint_attachment'
        OR o.request_url <> d.source_url
      )
  ) THEN
    RAISE EXCEPTION 'FINT attachment observation binding failed';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'fint_query_seed',
    'fint_query',
    'fint_crawl_observation',
    'fint_document_snapshot',
    'fint_query_document_match',
    'fint_attachment_declaration',
    'fint_attachment_snapshot',
    'fint_crawl_issue'
  ]
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_insert_guard ON '
      'nhi_rule_history_edition.%I',
      table_name,
      table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_update_guard ON '
      'nhi_rule_history_edition.%I',
      table_name,
      table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_edition.%I',
      table_name,
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_insert_guard BEFORE INSERT ON '
      'nhi_rule_history_edition.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_edition.guard_fint_crawl_child_insert()',
      table_name,
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_update_guard BEFORE UPDATE OR DELETE ON '
      'nhi_rule_history_edition.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_edition.reject_fint_crawl_mutation()',
      table_name,
      table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_edition.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_edition.reject_fint_crawl_mutation()',
      table_name,
      table_name
    );
  END LOOP;
END;
$$;

DROP TRIGGER IF EXISTS fint_document_number_identity_update_guard
  ON nhi_rule_history_edition.fint_document_number_identity;
DROP TRIGGER IF EXISTS fint_document_number_identity_truncate_guard
  ON nhi_rule_history_edition.fint_document_number_identity;
CREATE TRIGGER fint_document_number_identity_update_guard
BEFORE UPDATE OR DELETE
ON nhi_rule_history_edition.fint_document_number_identity
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_edition.reject_fint_crawl_mutation();
CREATE TRIGGER fint_document_number_identity_truncate_guard
BEFORE TRUNCATE
ON nhi_rule_history_edition.fint_document_number_identity
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_edition.reject_fint_crawl_mutation();

DROP TRIGGER IF EXISTS fint_crawl_run_update_guard
  ON nhi_rule_history_edition.fint_crawl_run;
DROP TRIGGER IF EXISTS fint_crawl_run_truncate_guard
  ON nhi_rule_history_edition.fint_crawl_run;
CREATE TRIGGER fint_crawl_run_update_guard
BEFORE UPDATE OR DELETE
ON nhi_rule_history_edition.fint_crawl_run
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_edition.guard_fint_crawl_run_update();
CREATE TRIGGER fint_crawl_run_truncate_guard
BEFORE TRUNCATE
ON nhi_rule_history_edition.fint_crawl_run
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_edition.reject_fint_crawl_mutation();

COMMENT ON TABLE nhi_rule_history_edition.fint_query_document_match IS
  'Many-to-many evidence: one document-number group may be retrieved by '
  'many terms and may contain multiple distinct detail snapshots. '
  'A keyword match is discovery evidence, not automatic clause adjudication.';

COMMENT ON TABLE
  nhi_rule_history_edition.fint_document_number_identity IS
  'Normalized formal-number grouping key, not a claim that one formal number '
  'always identifies exactly one official record or one legal rule.';

COMMENT ON COLUMN
  nhi_rule_history_edition.fint_attachment_declaration.source_label IS
  'Label declared by FINT. It may be unrelated or mislabeled and is not by '
  'itself proof that the attachment supports a reimbursement-rule version.';

COMMIT;
