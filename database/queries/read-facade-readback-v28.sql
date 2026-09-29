-- Readback for v28 (read-only facade schema nhi_rules).
--
-- Run on a fresh connection after applying the migration:
--   psql -X -q -v ON_ERROR_STOP=1 -f database/queries/read-facade-readback-v28.sql
-- Every "ok" column should read t. The whole file is one read-only
-- transaction: it writes nothing.

\set ON_ERROR_STOP on
BEGIN READ ONLY;

\echo '== 1. shape: thirteen views, none writable, invoker rights, owner-only access =='
SELECT
  count(*) AS views,
  count(*) FILTER (WHERE c.reloptions @> ARRAY['security_invoker=true'])
    AS security_invoker,
  count(*) FILTER (
    WHERE NOT EXISTS (
      SELECT 1 FROM aclexplode(c.relacl) AS a WHERE a.grantee <> c.relowner
    )
  ) AS owner_only_access,
  count(*) FILTER (
    WHERE i.is_updatable = 'NO' AND i.is_insertable_into = 'NO'
      AND i.is_trigger_updatable = 'NO' AND i.is_trigger_deletable = 'NO'
      AND i.is_trigger_insertable_into = 'NO'
  ) AS not_writable,
  (SELECT count(*) FROM pg_class AS o
   WHERE o.relnamespace = 'nhi_rules'::regnamespace AND o.relkind <> 'v'
  ) AS non_view_objects,
  (SELECT count(*) FROM pg_proc AS p
   WHERE p.pronamespace = 'nhi_rules'::regnamespace) AS functions,
  NOT has_schema_privilege('public', 'nhi_rules', 'USAGE')
    AS schema_closed_to_public,
  count(*) FILTER (WHERE has_table_privilege('public', c.oid, 'SELECT'))
    AS views_readable_by_public,
  count(*) = 13
    AND count(*) FILTER (WHERE c.reloptions @> ARRAY['security_invoker=true']) = 13
    AND count(*) FILTER (
      WHERE NOT EXISTS (
        SELECT 1 FROM aclexplode(c.relacl) AS a WHERE a.grantee <> c.relowner
      )
    ) = 13
    AND count(*) FILTER (
      WHERE i.is_updatable = 'NO' AND i.is_insertable_into = 'NO'
        AND i.is_trigger_updatable = 'NO' AND i.is_trigger_deletable = 'NO'
        AND i.is_trigger_insertable_into = 'NO'
    ) = 13
    AND NOT has_schema_privilege('public', 'nhi_rules', 'USAGE')
    AND count(*) FILTER (WHERE has_table_privilege('public', c.oid, 'SELECT')) = 0
    AS ok
FROM pg_class AS c
JOIN information_schema.views AS i
  ON i.table_schema = 'nhi_rules' AND i.table_name = c.relname
WHERE c.relnamespace = 'nhi_rules'::regnamespace AND c.relkind = 'v';

\echo '== 2. row counts: facade view vs the store table it stands on =='
WITH active_publication AS (
  SELECT run_id FROM nhi_rule_history_publication.v_active_publication_run
), active_release AS (
  SELECT run_id FROM nhi_rule_history_announced.v_active_run
), latest_clause_run AS (
  SELECT run_id FROM nhi_rule_history_clause.import_run
  WHERE state = 'sealed' ORDER BY sealed_at DESC, run_id DESC LIMIT 1
), counts(view_name, facade_rows, store_rows) AS (
  VALUES
  ('current_clause',
   (SELECT count(*) FROM nhi_rules.current_clause),
   (SELECT count(*) FROM nhi_rule_history_publication.current_clause
    WHERE run_id IN (SELECT run_id FROM active_publication))),
  ('current_clause_block',
   (SELECT count(*) FROM nhi_rules.current_clause_block),
   (SELECT count(*) FROM nhi_rule_history_publication.current_clause_block
    WHERE run_id IN (SELECT run_id FROM active_publication))),
  ('current_clause_date',
   (SELECT count(*) FROM nhi_rules.current_clause_date),
   (SELECT count(*) FROM nhi_rule_history_publication.current_clause_date
    WHERE run_id IN (SELECT run_id FROM active_publication))),
  ('current_source_file',
   (SELECT count(*) FROM nhi_rules.current_source_file),
   (SELECT count(*) FROM (
      SELECT DISTINCT source_label, source_url, source_artifact_sha256
      FROM nhi_rule_history_publication.current_clause
      WHERE run_id IN (SELECT run_id FROM active_publication)) AS s)),
  ('announced_patch',
   (SELECT count(*) FROM nhi_rules.announced_patch),
   (SELECT count(*) FROM nhi_rule_history_announced.clause_patch
    WHERE run_id IN (SELECT run_id FROM active_release))),
  ('announced_composed_clause',
   (SELECT count(*) FROM nhi_rules.announced_composed_clause),
   (SELECT count(*) FROM nhi_rule_history_announced.composed_clause_version AS v
    JOIN nhi_rule_history_announced.clause_patch AS p
      ON p.run_id = v.run_id AND p.patch_id = v.patch_id
    WHERE v.run_id IN (SELECT run_id FROM active_release)
      AND p.composition_status = 'reviewed_composite')),
  ('announced_notice_effect',
   (SELECT count(*) FROM nhi_rules.announced_notice_effect),
   (SELECT count(*) FROM nhi_rule_history_announced.notice_effect
    WHERE run_id IN (SELECT run_id FROM active_release))),
  ('general_principle_version',
   (SELECT count(*) FROM nhi_rules.general_principle_version),
   (SELECT count(*) FROM nhi_rule_history_clause.clause_version AS v
    JOIN nhi_rule_history_clause.clause AS c ON c.clause_id = v.clause_id
    WHERE c.chapter_id = 'chapter:general-principles'
      AND c.first_import_run_id IN (SELECT run_id FROM latest_clause_run)
      AND v.first_import_run_id IN (SELECT run_id FROM latest_clause_run))),
  ('general_principle_change',
   (SELECT count(*) FROM nhi_rules.general_principle_change),
   (SELECT count(*) FROM nhi_rule_history_clause.clause_version_edge AS e
    JOIN nhi_rule_history_clause.clause AS c ON c.clause_id = e.clause_id
    LEFT JOIN nhi_rule_history_clause.clause_diff_hunk AS h
      ON h.edge_id = e.edge_id
    WHERE c.chapter_id = 'chapter:general-principles'
      AND c.first_import_run_id IN (SELECT run_id FROM latest_clause_run))),
  ('general_principle_edition',
   (SELECT count(*) FROM nhi_rules.general_principle_edition),
   (SELECT count(*) FROM nhi_rule_history_edition.rule_version AS rv
    JOIN nhi_rule_history_edition.rule AS r ON r.rule_id = rv.rule_id
    WHERE r.canonical_slug = 'general-principles')),
  ('notice',
   (SELECT count(*) FROM nhi_rules.notice),
   (SELECT count(*) FROM nhi_rule_history_update_queue.rss_work_item AS w
    JOIN nhi_rule_history_update_queue.v_work_item_current AS cur
      ON cur.work_item_id = w.work_item_id
    WHERE cur.current_state <> 'ignored_non_rule')),
  ('release_state', (SELECT count(*) FROM nhi_rules.release_state), 4::bigint),
  ('view_catalog', (SELECT count(*) FROM nhi_rules.view_catalog), 13::bigint)
)
SELECT view_name, facade_rows, store_rows, facade_rows = store_rows AS ok
FROM counts ORDER BY view_name;

\echo '== 3. text integrity: every text shown hashes to its published sha256 =='
SELECT view_name, rows, hash_matches, rows = hash_matches AS ok
FROM (
  SELECT 'current_clause' AS view_name, count(*) AS rows,
         count(*) FILTER (WHERE encode(sha256(convert_to(clause_text, 'UTF8')), 'hex') = clause_text_sha256) AS hash_matches
  FROM nhi_rules.current_clause
  UNION ALL
  SELECT 'current_clause_block', count(*),
         count(*) FILTER (WHERE encode(sha256(convert_to(block_text, 'UTF8')), 'hex') = block_text_sha256)
  FROM nhi_rules.current_clause_block
  UNION ALL
  SELECT 'announced_patch', count(*),
         count(*) FILTER (WHERE encode(sha256(convert_to(patch_text, 'UTF8')), 'hex') = patch_text_sha256)
  FROM nhi_rules.announced_patch
  UNION ALL
  SELECT 'announced_composed_clause', count(*),
         count(*) FILTER (WHERE encode(sha256(convert_to(composed_text, 'UTF8')), 'hex') = composed_text_sha256)
  FROM nhi_rules.announced_composed_clause
  UNION ALL
  SELECT 'general_principle_version', count(*),
         count(*) FILTER (WHERE encode(sha256(convert_to(version_text, 'UTF8')), 'hex') = version_text_sha256)
  FROM nhi_rules.general_principle_version
) AS t
ORDER BY view_name;

\echo '== 4. announced patches are anchored on the text that is current now =='
SELECT
  count(*) AS patches,
  count(*) FILTER (WHERE base_text_matches_current) AS on_current_text,
  count(*) FILTER (WHERE base_text_matches_current IS NULL) AS new_clauses,
  count(*) FILTER (WHERE base_text_matches_current = false) AS on_older_text,
  count(*) FILTER (WHERE base_text_matches_current = false) = 0 AS ok
FROM nhi_rules.announced_patch;

\echo '== 5. informational: general-principles text in the two stores (trailing space ignored) =='
SELECT
  count(*) AS clauses,
  count(*) FILTER (WHERE btrim(p.clause_text) = btrim(v.version_text)) AS same_text,
  count(*) FILTER (WHERE p.clause_text = v.version_text) AS byte_identical
FROM nhi_rules.general_principle_version AS v
JOIN nhi_rules.current_clause AS p ON p.clause_code = v.clause_code
WHERE v.is_latest_version;

\echo '== 6. spot checks: five clauses across the layers =='
WITH spot(clause_code) AS (
  VALUES ('0.3'), ('2.1.4.2'), ('2.6.1'), ('3.3.32'), ('9.5')
)
SELECT
  s.clause_code,
  c.chapter_number,
  c.clause_text_sha256,
  c.source_label,
  (SELECT count(*) FROM nhi_rules.announced_patch AS p
   WHERE p.clause_code = s.clause_code) AS patches,
  (SELECT min(p.effective_from) FROM nhi_rules.announced_patch AS p
   WHERE p.clause_code = s.clause_code) AS first_effective_from,
  (SELECT string_agg(p.display_lifecycle, ',' ORDER BY p.effective_from)
   FROM nhi_rules.announced_patch AS p
   WHERE p.clause_code = s.clause_code) AS lifecycle,
  (SELECT count(*) FROM nhi_rules.announced_composed_clause AS k
   WHERE k.clause_code = s.clause_code) AS composed,
  (SELECT count(*) FROM nhi_rules.general_principle_version AS g
   WHERE g.clause_code = s.clause_code) AS chain_versions
FROM spot AS s
LEFT JOIN nhi_rules.current_clause AS c ON c.clause_code = s.clause_code
ORDER BY string_to_array(s.clause_code, '.')::integer[];

\echo '== 7. which runs are read =='
SELECT layer, run_id, state, producer_version, as_of
FROM nhi_rules.release_state ORDER BY layer;

\echo '== 8. the map =='
SELECT view_name, reads_schemas, column_count
FROM nhi_rules.view_catalog ORDER BY view_name;

ROLLBACK;
