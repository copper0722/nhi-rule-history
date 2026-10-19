-- Readback for v29 (diff-card store nhi_rule_history_diff_card).
--
-- Run on a fresh connection after applying the migration, and again after each
-- load-pg run:
--   psql -X -q -v ON_ERROR_STOP=1 -f database/queries/diff-card-readback-v29.sql
-- Sections 1 to 3 each end in an "ok" column that should read t; an empty store
-- passes them, because there is nothing to violate.  Sections 4 and 5 are
-- informational.  The whole file is one read-only transaction: it writes nothing.
-- It reads through the view, as a consumer does, so a wrong view definition shows.

\set ON_ERROR_STOP on
BEGIN READ ONLY;

\echo '== 1. shape: two tables, one view, write-once guards, owner-only access =='
WITH rel AS (
  SELECT c.oid, c.relkind, c.relowner, c.relacl, c.reloptions, c.relname
  FROM pg_class AS c
  WHERE c.relnamespace = 'nhi_rule_history_diff_card'::regnamespace
    AND c.relkind IN ('r', 'v')
), guards AS (
  SELECT t.tgname::text AS name
  FROM pg_trigger AS t
  JOIN rel ON rel.oid = t.tgrelid AND rel.relkind = 'r'
  WHERE NOT t.tgisinternal
), cols AS (
  SELECT array_agg(column_name::text || ':' || data_type::text
                   ORDER BY ordinal_position) AS list
  FROM information_schema.columns
  WHERE table_schema = 'nhi_rule_history_diff_card'
    AND table_name = 'v_public_comparison_block'
), shape AS (
  SELECT
    (SELECT count(*) FROM rel WHERE relkind = 'r') AS tables,
    (SELECT count(*) FROM rel WHERE relkind = 'v') AS views,
    (SELECT count(*) FROM rel
     WHERE relkind = 'v' AND reloptions @> ARRAY['security_invoker=true'])
      AS security_invoker,
    (SELECT count(*) FROM information_schema.views AS i
     WHERE i.table_schema = 'nhi_rule_history_diff_card'
       AND i.is_updatable = 'NO' AND i.is_insertable_into = 'NO'
       AND i.is_trigger_updatable = 'NO' AND i.is_trigger_deletable = 'NO'
       AND i.is_trigger_insertable_into = 'NO') AS not_writable,
    (SELECT count(*) FROM rel
     WHERE NOT EXISTS (
       SELECT 1 FROM aclexplode(rel.relacl) AS a WHERE a.grantee <> rel.relowner
     )) AS owner_only_relations,
    NOT has_schema_privilege('public', 'nhi_rule_history_diff_card', 'USAGE')
      AS schema_closed_to_public,
    (SELECT count(*) FROM rel WHERE has_table_privilege('public', rel.oid, 'SELECT'))
      AS relations_readable_by_public,
    (SELECT count(*) FROM guards
     WHERE name IN ('comparison_write_once', 'comparison_truncate_guard',
                    'comparison_block_write_once', 'comparison_block_truncate_guard',
                    'comparison_complete', 'comparison_block_complete'))
      AS guards,
    (SELECT list = ARRAY[
       'source_artifact_sha256:text', 'generator_version:text',
       'comparison_sha256:text', 'extra_table_count:integer',
       'block_order:integer', 'block_kind:text', 'clause_id:text',
       'clause_codes:ARRAY', 'path:text', 'badge:text', 'hunk_old:text',
       'hunk_new:text', 'adds:integer', 'dels:integer',
       'old_paragraphs:jsonb', 'new_paragraphs:jsonb', 'rows:jsonb']
     FROM cols) AS view_columns_as_designed
)
SELECT shape.*,
       tables = 2 AND views = 1 AND security_invoker = 1 AND not_writable = 1
         AND owner_only_relations = 3 AND schema_closed_to_public
         AND relations_readable_by_public = 0 AND guards = 6
         AND view_columns_as_designed AS ok
FROM shape;

\echo '== 2. comparisons: each is complete, and the view serves the newest of each source file =='
WITH per AS (
  SELECT c.comparison_id, c.source_artifact_sha256, c.generator_version,
         c.loaded_at, c.block_count, count(b.comparison_id) AS blocks,
         min(b.block_order) AS first_order, max(b.block_order) AS last_order
  FROM nhi_rule_history_diff_card.comparison AS c
  LEFT JOIN nhi_rule_history_diff_card.comparison_block AS b
    ON b.comparison_id = c.comparison_id
  GROUP BY c.comparison_id
), newest AS (
  SELECT DISTINCT ON (source_artifact_sha256) *
  FROM per
  ORDER BY source_artifact_sha256, loaded_at DESC, generator_version DESC
), figures AS (
  SELECT
    (SELECT count(*) FROM per) AS comparisons,
    (SELECT count(DISTINCT source_artifact_sha256) FROM per) AS source_files,
    (SELECT coalesce(sum(blocks), 0) FROM per) AS blocks,
    (SELECT count(*) FROM per
     WHERE blocks = block_count AND first_order = 0
       AND last_order = block_count - 1) AS complete,
    (SELECT coalesce(sum(blocks), 0) FROM newest) AS newest_blocks,
    (SELECT count(*) FROM nhi_rule_history_diff_card.v_public_comparison_block)
      AS served_rows,
    (SELECT count(DISTINCT source_artifact_sha256)
     FROM nhi_rule_history_diff_card.v_public_comparison_block) AS served_files
)
SELECT figures.*,
       comparisons = complete AND served_rows = newest_blocks
         AND served_files = source_files AS ok
FROM figures;

\echo '== 3. replay invariant: the stored rows replay their paragraphs =='
WITH r AS (
  SELECT v.source_artifact_sha256 AS sha, v.block_order, v.old_paragraphs,
         v.new_paragraphs, v.adds, v.dels, e.ord, e.rw
  FROM nhi_rule_history_diff_card.v_public_comparison_block AS v
  CROSS JOIN LATERAL jsonb_array_elements(v.rows) WITH ORDINALITY AS e(rw, ord)
), rr AS (
  SELECT r.sha, r.block_order, r.ord, r.old_paragraphs, r.new_paragraphs,
         r.adds, r.dels,
         r.rw ->> 'kind' AS kind,
         r.rw ->> 'old_no' AS old_no,
         r.rw ->> 'new_no' AS new_no,
         (SELECT string_agg(t.s ->> 'text', '' ORDER BY t.so)
          FROM jsonb_array_elements(r.rw -> 'segments') WITH ORDINALITY AS t(s, so))
           AS shown,
         EXISTS (
           SELECT 1 FROM jsonb_array_elements(r.rw -> 'segments') AS t(s)
           WHERE (t.s ->> 'mark')::boolean
         ) AS marked
  FROM r
), checked AS (
  SELECT rr.*,
         CASE WHEN old_no ~ '^[1-9][0-9]*$' THEN old_no::integer END AS old_n,
         CASE WHEN new_no ~ '^[1-9][0-9]*$' THEN new_no::integer END AS new_n
  FROM rr
), bad_rows AS (
  SELECT count(*) AS n
  FROM checked
  WHERE NOT CASE kind
    WHEN 'ctx' THEN old_n IS NOT NULL AND new_n IS NOT NULL AND NOT marked
      AND shown IS NOT DISTINCT FROM (new_paragraphs ->> (new_n - 1))
    WHEN 'del' THEN old_n IS NOT NULL AND new_no = ''
      AND shown IS NOT DISTINCT FROM (old_paragraphs ->> (old_n - 1))
    WHEN 'add' THEN new_n IS NOT NULL AND old_no = ''
      AND shown IS NOT DISTINCT FROM (new_paragraphs ->> (new_n - 1))
    ELSE false
  END
), blocks AS (
  SELECT sha, block_order, adds, dels,
         jsonb_array_length(old_paragraphs) AS n_old,
         jsonb_array_length(new_paragraphs) AS n_new,
         coalesce(array_agg(old_n ORDER BY ord)
                  FILTER (WHERE kind IN ('ctx', 'del')), ARRAY[]::integer[])
           AS old_numbers,
         coalesce(array_agg(new_n ORDER BY ord)
                  FILTER (WHERE kind IN ('ctx', 'add')), ARRAY[]::integer[])
           AS new_numbers,
         count(*) FILTER (WHERE kind = 'add') AS add_rows,
         count(*) FILTER (WHERE kind = 'del') AS del_rows
  FROM checked
  GROUP BY sha, block_order, adds, dels, old_paragraphs, new_paragraphs
), bad_blocks AS (
  SELECT count(*) AS n
  FROM blocks
  WHERE old_numbers IS DISTINCT FROM ARRAY(SELECT generate_series(1, n_old))
     OR new_numbers IS DISTINCT FROM ARRAY(SELECT generate_series(1, n_new))
     OR add_rows <> adds OR del_rows <> dels
)
SELECT (SELECT count(*) FROM blocks) AS blocks_checked,
       (SELECT count(*) FROM checked) AS rows_checked,
       bad_rows.n AS row_violations,
       bad_blocks.n AS block_violations,
       bad_rows.n = 0 AND bad_blocks.n = 0 AS ok
FROM bad_rows, bad_blocks;

SELECT to_regclass('nhi_rule_history_announced.v_public_clause_patch') IS NOT NULL
  AS has_announced \gset
\if :has_announced
\echo '== 4. coverage of the served announced patches (informational) =='
SELECT
  count(DISTINCT p.source_artifact_sha256::text) AS served_source_files,
  count(DISTINCT p.source_artifact_sha256::text) FILTER (
    WHERE EXISTS (
      SELECT 1 FROM nhi_rule_history_diff_card.v_public_comparison_block AS v
      WHERE v.source_artifact_sha256 = p.source_artifact_sha256::text
    )
  ) AS source_files_with_a_card,
  count(*) AS served_patches,
  count(*) FILTER (
    WHERE EXISTS (
      SELECT 1 FROM nhi_rule_history_diff_card.v_public_comparison_block AS v
      WHERE v.source_artifact_sha256 = p.source_artifact_sha256::text
        AND p.clause_code = ANY (v.clause_codes)
    )
  ) AS patches_with_a_block
FROM nhi_rule_history_announced.v_public_clause_patch AS p;

\echo '   served source files without a card (clause codes, composition status):'
SELECT left(p.source_artifact_sha256::text, 12) AS source_file,
       array_agg(p.clause_code ORDER BY p.clause_code) AS clause_codes,
       min(p.composition_status) AS composition_status
FROM nhi_rule_history_announced.v_public_clause_patch AS p
WHERE NOT EXISTS (
  SELECT 1 FROM nhi_rule_history_diff_card.v_public_comparison_block AS v
  WHERE v.source_artifact_sha256 = p.source_artifact_sha256::text
)
GROUP BY p.source_artifact_sha256
ORDER BY 1;
\else
\echo '== 4. skipped: the announced layer is not in this database =='
\endif

\echo '== 5. spot checks: a nested clause row and the attachment blocks (informational) =='
SELECT left(v.source_artifact_sha256, 12) AS source_file, v.block_order,
       v.block_kind, v.clause_id, v.clause_codes
FROM nhi_rule_history_diff_card.v_public_comparison_block AS v
WHERE cardinality(v.clause_codes) > 1 OR v.block_kind = 'attachment'
ORDER BY v.source_artifact_sha256, v.block_order;

ROLLBACK;
