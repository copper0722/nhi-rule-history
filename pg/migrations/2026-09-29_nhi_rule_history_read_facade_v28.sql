-- 2026-09-29 — read-only facade schema nhi_rules (v28).
--
-- One schema that answers the reader's questions end to end (current clause
-- text, announced amendments with their effective dates, the general-principles
-- (通則) version chain, official notices and their sources) by views over the
-- stores that already hold the data.  Views only: no table, function, trigger
-- or writer is created and no data is moved or copied.  Every view is
-- security_invoker and none is updatable; nothing is granted to PUBLIC.  The
-- views select the same relations, under the same active-run rules, as the
-- reader API, so a value here is a value served there.  Where the API hides
-- something (a hunk it classes as format_only) the facade keeps it and says so.
--
-- What it does not do: it is not an authority (the stores it reads stay the
-- authority), it adds no version history beyond the 通則 chain, it does not turn
-- a source-observed date into a legal effective date, and it changes no loader,
-- lane, hold or consumer.
--
-- Coupling: each view depends on the columns it selects, so a later migration
-- that alters the type of, or drops, one of those columns or objects has to drop
-- this schema first.  The rollbacks of v18, v21, clause_v1 and edition_v1 end in
-- DROP SCHEMA ... CASCADE and would drop these views silently: roll v28 back first.
--
-- Rollback: 2026-09-29_nhi_rule_history_read_facade_v28.rollback.sql drops the
-- thirteen views and the schema (RESTRICT, no CASCADE); no other object changes.
-- Readback: database/queries/read-facade-readback-v28.sql.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-read-facade-v28', 0)
);

CREATE SCHEMA nhi_rules;
REVOKE ALL ON SCHEMA nhi_rules FROM PUBLIC;
COMMENT ON SCHEMA nhi_rules IS
  'Read-only facade over the nhi_rule_history_* stores: views only, no table, no writer, not an authority. view_catalog lists each view, the question it answers and the relations it reads.';

-- ---------------------------------------------------------------------------
-- Current clause text (store: nhi_rule_history_publication)
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rules.current_clause
WITH (security_invoker = true) AS
SELECT
  c.clause_code,
  c.chapter_number,
  c.source_designation,
  c.code_origin,
  c.display_title,
  c.raw_text AS clause_text,
  c.raw_text_sha256::text AS clause_text_sha256,
  c.source_label,
  c.source_url,
  c.source_artifact_sha256::text AS source_artifact_sha256,
  c.inventory_status,
  c.valid_distinct_roc_date_count AS text_date_count,
  c.expected_version_count,
  c.reconstructed_version_count,
  c.missing_version_count,
  CASE WHEN c.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(c.clause_code, '.')::integer[]
  END AS sort_key,
  c.run_id AS publication_run_id
FROM nhi_rule_history_publication.v_current_clause AS c;

COMMENT ON VIEW nhi_rules.current_clause IS
  'The text of every drug reimbursement clause in the sealed publication, one row per clause: the chapter files as they read when the publication was sealed (see release_state.as_of), with heading, source file and hash. Amendments announced or in effect since are not merged in; they appear in announced_patch. Not a version history.';
COMMENT ON COLUMN nhi_rules.current_clause.code_origin IS
  'official_source = the code is printed in the official file; project_assigned = the project numbered the clause (general principles 0.1-0.12).';
COMMENT ON COLUMN nhi_rules.current_clause.missing_version_count IS
  'Versions the text itself implies (from its own dates) that are not reconstructed yet; 0 for a clause with nothing missing.';
COMMENT ON COLUMN nhi_rules.current_clause.sort_key IS
  'Numeric clause path for natural ordering: ORDER BY sort_key.';

CREATE VIEW nhi_rules.current_clause_block
WITH (security_invoker = true) AS
SELECT
  b.clause_code,
  b.block_order,
  b.block_kind,
  b.container,
  b.raw_text AS block_text,
  b.raw_text_sha256::text AS block_text_sha256,
  b.source_block_id,
  b.source_locator,
  CASE WHEN b.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(b.clause_code, '.')::integer[]
  END AS sort_key,
  b.run_id AS publication_run_id
FROM nhi_rule_history_publication.v_current_clause_block AS b;

COMMENT ON VIEW nhi_rules.current_clause_block IS
  'The current clauses split into ordered source blocks (paragraph, list item, table cell) with the source locator and hash of each block.';

CREATE VIEW nhi_rules.current_clause_date
WITH (security_invoker = true) AS
SELECT
  d.clause_code,
  d.date_value,
  d.raw_expressions,
  d.occurrence_count,
  CASE WHEN d.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(d.clause_code, '.')::integer[]
  END AS sort_key,
  d.run_id AS publication_run_id
FROM nhi_rule_history_publication.v_current_clause_date AS d;

COMMENT ON VIEW nhi_rules.current_clause_date IS
  'ROC dates that appear inside the current clause text, for example amendment marks such as 115/9/1. Source-observed annotations, not legal effective dates.';

CREATE VIEW nhi_rules.current_source_file
WITH (security_invoker = true) AS
SELECT
  c.source_label,
  c.source_url,
  c.source_artifact_sha256::text AS source_artifact_sha256,
  count(*)::integer AS clause_count,
  array_agg(DISTINCT c.chapter_number ORDER BY c.chapter_number) AS chapters,
  c.run_id AS publication_run_id
FROM nhi_rule_history_publication.v_current_clause AS c
GROUP BY c.source_label, c.source_url, c.source_artifact_sha256, c.run_id;

COMMENT ON VIEW nhi_rules.current_source_file IS
  'The official chapter files the current text was built from: file label (it carries the file''s own update date), address, hash and how many clauses each file supplies.';

-- ---------------------------------------------------------------------------
-- Announced amendments (store: nhi_rule_history_announced, active release)
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rules.announced_patch
WITH (security_invoker = true) AS
SELECT
  p.clause_code,
  p.effective_from,
  p.effective_until,
  p.display_lifecycle,
  p.current_resolution_state,
  p.resolution_reason,
  p.resolution_recorded_at,
  p.composition_status,
  p.reference_number AS notice_reference,
  p.notice_title,
  p.official_url AS notice_url,
  p.published_on AS notice_published_on,
  p.effective_on AS notice_effective_on,
  p.source_exact_patch_text AS patch_text,
  p.source_exact_patch_sha256::text AS patch_text_sha256,
  p.predecessor_text_sha256::text AS base_text_sha256,
  CASE WHEN c.clause_code IS NULL THEN NULL
       ELSE p.predecessor_text_sha256::text = c.raw_text_sha256::text
  END AS base_text_matches_current,
  p.omitted_text_present,
  p.partial_event_projection,
  p.unprocessed_event_scope,
  p.public_note,
  p.source_artifact_sha256::text AS notice_source_sha256,
  CASE WHEN p.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(p.clause_code, '.')::integer[]
  END AS sort_key,
  p.run_id AS announced_run_id,
  p.patch_id
FROM nhi_rule_history_announced.v_public_clause_patch AS p
LEFT JOIN nhi_rule_history_publication.v_current_clause AS c
  ON c.clause_code = p.clause_code;

COMMENT ON VIEW nhi_rules.announced_patch IS
  'Official amendments as announced, before and after they take effect: the clause, the effective date, the exact patch text, the notice it came from and whether it is scheduled, in effect or superseded. Reads the active announced release only.';
COMMENT ON COLUMN nhi_rules.announced_patch.display_lifecycle IS
  'Worked out when you read it, so a row changes on its effective date. future = effective date not reached; effective_reconciled = effective and the refreshed official chapter file matches it; effective_unconsolidated = effective, no correction or withdrawal found, not yet consolidated into the chapter file; effective_date_reached_unresolved = effective date reached, no post-effective resolution recorded yet; superseded, corrected, withdrawn and conflicted as named.';
COMMENT ON COLUMN nhi_rules.announced_patch.current_resolution_state IS
  'The latest resolution receipt for the patch (verified_scheduled, effective_unconsolidated, reconciled, corrected, withdrawn, conflicted).';
COMMENT ON COLUMN nhi_rules.announced_patch.composition_status IS
  'patch_only = only the amended text of the notice is held; reviewed_composite = also merged with the current text (see announced_composed_clause).';
COMMENT ON COLUMN nhi_rules.announced_patch.base_text_matches_current IS
  'true = the patch''s base hash equals the hash of the clause text in the publication now (a hash equality, not proof of what the notice was drafted against); false = the publication text changed since; NULL = there is no publication text to compare (a new clause, or no active publication).';

CREATE VIEW nhi_rules.announced_composed_clause
WITH (security_invoker = true) AS
SELECT
  v.clause_code,
  v.effective_from,
  v.composed_text,
  v.composed_text_sha256::text AS composed_text_sha256,
  v.amendment_block_count,
  v.inherited_block_count,
  v.review_status,
  v.composition_rule_version,
  v.predecessor_publication_run_id,
  v.predecessor_text_sha256::text AS base_text_sha256,
  v.public_note,
  CASE WHEN v.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(v.clause_code, '.')::integer[]
  END AS sort_key,
  v.run_id AS announced_run_id,
  v.version_id
FROM nhi_rule_history_announced.v_public_composed_clause_version AS v;

COMMENT ON VIEW nhi_rules.announced_composed_clause IS
  'For a clause whose patch was merged with the current text and reviewed: the full text as it reads once the amendment is effective, with how many blocks came from the amendment and how many were inherited.';

CREATE VIEW nhi_rules.announced_notice_effect
WITH (security_invoker = true) AS
SELECT
  n.reference_number AS notice_reference,
  n.title AS notice_title,
  n.official_url AS notice_url,
  n.published_on AS notice_published_on,
  n.effective_on AS notice_effective_on,
  e.effect_type,
  e.clause_code,
  e.projection_status,
  e.scope_note,
  CASE WHEN e.clause_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(e.clause_code, '.')::integer[]
  END AS sort_key,
  e.run_id AS announced_run_id,
  e.notice_id
FROM nhi_rule_history_announced.notice_effect AS e
JOIN nhi_rule_history_announced.v_active_run AS r
  ON r.run_id = e.run_id
JOIN nhi_rule_history_announced.notice_event AS n
  ON n.run_id = e.run_id AND n.notice_id = e.notice_id;

COMMENT ON VIEW nhi_rules.announced_notice_effect IS
  'What each announced notice changes, effect by effect, and whether that change is projected into a patch yet (projection_status). A change that is not projected is announced but not yet in announced_patch.';

-- ---------------------------------------------------------------------------
-- General-principles (通則) version chain
-- (stores: nhi_rule_history_clause, nhi_rule_history_edition)
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rules.general_principle_version
WITH (security_invoker = true) AS
WITH latest_run AS (
  SELECT r.run_id
  FROM nhi_rule_history_clause.import_run AS r
  WHERE r.state = 'sealed'
  ORDER BY r.sealed_at DESC, r.run_id DESC
  LIMIT 1
)
SELECT
  c.canonical_code AS clause_code,
  v.state_order AS version_no,
  v.state_order = max(v.state_order) OVER (PARTITION BY v.clause_id)
    AS is_latest_version,
  v.display_title,
  v.representative_raw_text AS version_text,
  v.representative_raw_sha256 AS version_text_sha256,
  obs.first_seen_edition,
  obs.last_seen_edition,
  obs.edition_count,
  dts.text_annotation_dates,
  v.legal_effective_status,
  CASE WHEN c.canonical_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(c.canonical_code, '.')::integer[]
  END AS sort_key,
  run.run_id AS clause_import_run_id
FROM latest_run AS run
JOIN nhi_rule_history_clause.clause AS c
  ON c.first_import_run_id = run.run_id
JOIN nhi_rule_history_clause.clause_version AS v
  ON v.clause_id = c.clause_id AND v.first_import_run_id = run.run_id
LEFT JOIN LATERAL (
  SELECT
    (array_agg(o.edition_label ORDER BY o.chronology_order))[1]
      AS first_seen_edition,
    (array_agg(o.edition_label ORDER BY o.chronology_order DESC))[1]
      AS last_seen_edition,
    count(*)::integer AS edition_count
  FROM nhi_rule_history_clause.clause_version_observation AS o
  WHERE o.clause_version_id = v.clause_version_id
    AND o.first_import_run_id = run.run_id
) AS obs ON true
LEFT JOIN LATERAL (
  SELECT array_agg(DISTINCT d.date_value ORDER BY d.date_value)
           FILTER (WHERE d.date_value IS NOT NULL) AS text_annotation_dates
  FROM nhi_rule_history_clause.clause_version_date AS d
  WHERE d.clause_version_id = v.clause_version_id
) AS dts ON true
WHERE c.chapter_id = 'chapter:general-principles';

COMMENT ON VIEW nhi_rules.general_principle_version IS
  'The version chain of each general-principles (通則) clause: every distinct text state across the official editions, in order, with the editions where it was seen. It shows what changed between editions; legal_effective_status stays not_claimed, so it is not a verified legal effective-date history. Only this chapter has a chain today. Reads the latest sealed clause import, as the reader API''s history endpoint does.';
COMMENT ON COLUMN nhi_rules.general_principle_version.version_no IS
  'Position of the text state in the clause chain, 0 = oldest observed.';
COMMENT ON COLUMN nhi_rules.general_principle_version.text_annotation_dates IS
  'Dates written inside this version''s own text (amendment marks). Source-local annotations, unresolved as legal effective dates.';
COMMENT ON COLUMN nhi_rules.general_principle_version.legal_effective_status IS
  'not_claimed = no legal effective date is asserted for this version.';

CREATE VIEW nhi_rules.general_principle_change
WITH (security_invoker = true) AS
WITH latest_run AS (
  SELECT r.run_id
  FROM nhi_rule_history_clause.import_run AS r
  WHERE r.state = 'sealed'
  ORDER BY r.sealed_at DESC, r.run_id DESC
  LIMIT 1
)
SELECT
  c.canonical_code AS clause_code,
  ov.state_order AS from_version_no,
  nv.state_order AS to_version_no,
  newer.edition_label AS changed_in_edition,
  h.hunk_order,
  pres.semantic_change_kind AS change_kind,
  h.change_kind AS source_change_kind,
  h.context_label,
  h.old_text,
  h.new_text,
  e.legal_predecessor_status,
  e.crosses_known_gap,
  CASE WHEN c.canonical_code ~ '^[0-9]{1,9}([.][0-9]{1,9})*$'
       THEN string_to_array(c.canonical_code, '.')::integer[]
  END AS sort_key,
  run.run_id AS clause_import_run_id
FROM latest_run AS run
JOIN nhi_rule_history_clause.clause AS c
  ON c.first_import_run_id = run.run_id
JOIN nhi_rule_history_clause.clause_version_edge AS e
  ON e.clause_id = c.clause_id
JOIN nhi_rule_history_clause.clause_version AS ov
  ON ov.clause_version_id = e.older_clause_version_id
JOIN nhi_rule_history_clause.clause_version AS nv
  ON nv.clause_version_id = e.newer_clause_version_id
LEFT JOIN LATERAL (
  SELECT d.run_id
  FROM nhi_rule_history_clause.diff_run AS d
  WHERE d.clause_import_run_id = run.run_id AND d.state = 'sealed'
  ORDER BY d.sealed_at DESC, d.run_id DESC
  LIMIT 1
) AS diff ON true
LEFT JOIN nhi_rule_history_clause.clause_diff_hunk AS h
  ON h.edge_id = e.edge_id
LEFT JOIN nhi_rule_history_clause.diff_hunk_presentation AS pres
  ON pres.hunk_id = h.hunk_id AND pres.diff_run_id = diff.run_id
LEFT JOIN LATERAL (
  SELECT o.edition_label
  FROM nhi_rule_history_clause.clause_version_observation AS o
  WHERE o.clause_version_id = e.newer_clause_version_id
    AND o.chronology_order = e.newer_first_observed_order
  ORDER BY o.observation_id
  LIMIT 1
) AS newer ON true
WHERE c.chapter_id = 'chapter:general-principles';

COMMENT ON VIEW nhi_rules.general_principle_change IS
  'What changed between adjacent general-principles (通則) versions: the old and new text hunk by hunk, with the edition where the newer text first appears. Adjacent text states only; legal_predecessor_status stays not_claimed.';
COMMENT ON COLUMN nhi_rules.general_principle_change.change_kind IS
  'The kind the reader sees, from the latest sealed diff run: added, removed, replaced, or format_only (the reader API hides format_only hunks; they are kept here). NULL = that diff run has no presentation for the hunk.';
COMMENT ON COLUMN nhi_rules.general_principle_change.source_change_kind IS
  'The kind the store recorded for the hunk. It can differ from change_kind (a hunk stored as replaced can read as added).';

CREATE VIEW nhi_rules.general_principle_edition
WITH (security_invoker = true) AS
SELECT
  rv.chronology_order AS edition_order,
  rv.version_label AS edition_label,
  d.source_kind,
  d.official_label,
  d.official_url,
  d.source_page_url,
  d.artifact_sha256 AS source_artifact_sha256,
  d.media_type,
  d.byte_length,
  d.observed_at AS source_observed_at,
  rv.validation_status,
  rv.legal_effective_status
FROM nhi_rule_history_edition.rule_version AS rv
JOIN nhi_rule_history_edition.rule AS r
  ON r.rule_id = rv.rule_id
JOIN nhi_rule_history_edition.source_document AS d
  ON d.document_id = rv.primary_document_id
WHERE r.canonical_slug = 'general-principles';

COMMENT ON VIEW nhi_rules.general_principle_edition IS
  'The official general-principles (通則) editions the version chain is built from, each with the address and hash of its source file and when the file was observed.';

-- ---------------------------------------------------------------------------
-- Notices and sources (stores: nhi_rule_history_update_queue, _update_ops)
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rules.notice
WITH (security_invoker = true) AS
SELECT
  w.work_item_id AS notice_id,
  w.first_title_raw AS title,
  w.first_link_raw AS official_url,
  i.published_raw AS rss_published_raw,
  w.first_observed_at,
  ann.reference_number AS announced_reference,
  ann.official_url IS NOT NULL AS in_announced_release
FROM nhi_rule_history_update_queue.v_work_item_current AS cur
JOIN nhi_rule_history_update_queue.rss_work_item AS w
  ON w.work_item_id = cur.work_item_id
JOIN nhi_rule_history_update_ops.feed_item_observation AS i
  ON i.feed_observation_id = w.first_feed_observation_id
 AND i.item_index = w.first_item_index
LEFT JOIN (
  SELECT ne.official_url,
         string_agg(DISTINCT ne.reference_number, ', '
                    ORDER BY ne.reference_number) AS reference_number
  FROM nhi_rule_history_announced.notice_event AS ne
  JOIN nhi_rule_history_announced.v_active_run AS r
    ON r.run_id = ne.run_id
  GROUP BY ne.official_url
) AS ann ON ann.official_url = w.first_link_raw
WHERE cur.current_state <> 'ignored_non_rule';

COMMENT ON VIEW nhi_rules.notice IS
  'Official NHI notices selected as reimbursement-rule work, with their official address, and whether a notice is already structured in the active announced release. The feed time is an observation of publication, not an effective date; internal processing states are not shown.';
COMMENT ON COLUMN nhi_rules.notice.announced_reference IS
  'The reference number of the announced notice at this address; when several notices share one address their numbers are listed together.';
COMMENT ON COLUMN nhi_rules.notice.rss_published_raw IS
  'The feed''s own date text; not a legal effective date.';

-- ---------------------------------------------------------------------------
-- Which runs are read, and the map
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rules.release_state
WITH (security_invoker = true) AS
SELECT
  'current_text'::text AS layer,
  run.run_id,
  run.state,
  run.loader_version AS producer_version,
  run.sealed_at AS as_of,
  jsonb_build_object(
    'clause_count', (SELECT count(*) FROM nhi_rules.current_clause),
    'block_count', (SELECT count(*) FROM nhi_rules.current_clause_block),
    'whole_split_parity_status', run.whole_split_parity_status,
    'version_count_policy', run.version_count_policy,
    'authority_page_url', run.authority_page_url,
    'sealed_fingerprint', run.sealed_fingerprint::text
  ) AS detail
FROM (SELECT 1) AS one
LEFT JOIN nhi_rule_history_publication.v_active_publication_run AS run ON true
UNION ALL
SELECT
  'announced'::text,
  run.run_id,
  run.state,
  run.loader_version,
  run.sealed_at,
  jsonb_build_object(
    'patch_count', (SELECT count(*) FROM nhi_rules.announced_patch),
    'notice_effect_count',
      (SELECT count(*) FROM nhi_rules.announced_notice_effect),
    'evaluator_version', run.evaluator_version,
    'sealed_fingerprint', run.sealed_fingerprint::text
  )
FROM (SELECT 1) AS one
LEFT JOIN nhi_rule_history_announced.v_active_run AS run ON true
UNION ALL
SELECT
  'general_principles'::text,
  run.run_id,
  run.state,
  run.extractor_version,
  run.sealed_at,
  jsonb_build_object(
    'version_count', (SELECT count(*) FROM nhi_rules.general_principle_version),
    'edition_count', (SELECT count(*) FROM nhi_rules.general_principle_edition),
    'output_sha256', run.output_sha256
  )
FROM (SELECT 1) AS one
LEFT JOIN LATERAL (
  SELECT r.run_id, r.state, r.extractor_version, r.sealed_at, r.output_sha256
  FROM nhi_rule_history_clause.import_run AS r
  WHERE r.state = 'sealed'
  ORDER BY r.sealed_at DESC, r.run_id DESC
  LIMIT 1
) AS run ON true
UNION ALL
SELECT
  'notices'::text,
  obs.feed_observation_id,
  obs.parse_status,
  obs.parser_version,
  obs.parsed_at,
  jsonb_build_object(
    'notice_count', (SELECT count(*) FROM nhi_rules.notice),
    'in_announced_release_count',
      (SELECT count(*) FROM nhi_rules.notice WHERE in_announced_release)
  )
FROM (SELECT 1) AS one
LEFT JOIN LATERAL (
  SELECT o.feed_observation_id, o.parse_status, o.parser_version, o.parsed_at
  FROM nhi_rule_history_update_ops.feed_observation AS o
  WHERE o.parse_status = 'parsed'
  ORDER BY o.parsed_at DESC, o.feed_observation_id DESC
  LIMIT 1
) AS obs ON true;

COMMENT ON VIEW nhi_rules.release_state IS
  'Which sealed run each layer of this schema is reading right now, when it was sealed or last observed, and its headline counts. Start here before quoting a number. A layer with no run shows NULL, never a missing row.';
COMMENT ON COLUMN nhi_rules.release_state.as_of IS
  'When the run was sealed; for notices, when the feed was last parsed successfully.';

CREATE VIEW nhi_rules.view_catalog
WITH (security_invoker = true) AS
SELECT
  c.relname::text AS view_name,
  obj_description(c.oid, 'pg_class') AS answers,
  COALESCE((
    SELECT array_agg(DISTINCT dn.nspname::text ORDER BY dn.nspname::text)
    FROM pg_rewrite AS rw
    JOIN pg_depend AS d
      ON d.classid = 'pg_rewrite'::regclass
     AND d.objid = rw.oid
     AND d.refclassid = 'pg_class'::regclass
     AND d.refobjid <> c.oid
    JOIN pg_class AS dc ON dc.oid = d.refobjid
    JOIN pg_namespace AS dn ON dn.oid = dc.relnamespace
    WHERE rw.ev_class = c.oid
  ), ARRAY[]::text[]) AS reads_schemas,
  COALESCE((
    SELECT array_agg(DISTINCT dn.nspname::text || '.' || dc.relname::text
                     ORDER BY dn.nspname::text || '.' || dc.relname::text)
    FROM pg_rewrite AS rw
    JOIN pg_depend AS d
      ON d.classid = 'pg_rewrite'::regclass
     AND d.objid = rw.oid
     AND d.refclassid = 'pg_class'::regclass
     AND d.refobjid <> c.oid
    JOIN pg_class AS dc ON dc.oid = d.refobjid
    JOIN pg_namespace AS dn ON dn.oid = dc.relnamespace
    WHERE rw.ev_class = c.oid
  ), ARRAY[]::text[]) AS reads_relations,
  (SELECT count(*)::integer
   FROM pg_attribute AS a
   WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  ) AS column_count
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'nhi_rules' AND c.relkind = 'v';

COMMENT ON VIEW nhi_rules.view_catalog IS
  'This map: each view of the schema, the question it answers, and the schemas and relations it actually reads, taken from the database dependency catalog so it cannot drift from the definitions.';

COMMIT;
