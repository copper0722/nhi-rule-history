\set ON_ERROR_STOP on

-- Usage:
-- psql -X "$DATABASE_URL" \
--   -v tagging_run_id=8d5b7f1d-01cf-5af6-932b-8bb2378f35ff \
--   -f database/queries/terminology-release-gates.sql
--
-- Returns one machine-readable JSON receipt. This is read-only and may be
-- rerun after release; all counts are derived from sealed PG rows and the
-- pinned reviewed seed enrichment.

WITH
params AS (
  SELECT :'tagging_run_id'::uuid AS tagging_run_id
),
run AS (
  SELECT r.*
  FROM nhi_rule_history_terminology.tagging_run r
  JOIN params p USING (tagging_run_id)
  WHERE r.state = 'sealed'
),
seed_links AS (
  SELECT link.*
  FROM nhi_rule_history_terminology.concept_seed_tag_link link
  JOIN params p USING (tagging_run_id)
),
seed_tags AS (
  SELECT link.concept_id,
         link.legacy_tag_id,
         tag.tag_text,
         tag.tag_type,
         tag.entity_type
  FROM seed_links link
  JOIN nhi_rule_history_clause.clause_semantic_tag tag
    ON (tag.enrichment_run_id, tag.tag_id) =
       (link.seed_enrichment_run_id, link.legacy_tag_id)
),
source_codes AS (
  SELECT link.concept_id,
         link.legacy_tag_id,
         'ATC'::text AS code_system,
         code.atc_code AS code
  FROM seed_links link
  JOIN run r ON true
  JOIN nhi_rule_history_clause.clause_semantic_tag_atc code
    ON (code.enrichment_run_id, code.tag_id) =
       (r.seed_enrichment_run_id, link.legacy_tag_id)
  UNION ALL
  SELECT link.concept_id,
         link.legacy_tag_id,
         'ICD11'::text,
         code.icd11_code
  FROM seed_links link
  JOIN run r ON true
  JOIN nhi_rule_history_clause.clause_semantic_tag_icd11_code code
    ON (code.enrichment_run_id, code.tag_id) =
       (r.seed_enrichment_run_id, link.legacy_tag_id)
  UNION ALL
  SELECT link.concept_id,
         link.legacy_tag_id,
         'NHI_TREATMENT'::text,
         code.treatment_code
  FROM seed_links link
  JOIN run r ON true
  JOIN nhi_rule_history_clause.clause_semantic_tag_nhi_treatment code
    ON (code.enrichment_run_id, code.tag_id) =
       (r.seed_enrichment_run_id, link.legacy_tag_id)
),
normalized_codes AS (
  SELECT output.*
  FROM nhi_rule_history_terminology.concept_external_code output
  JOIN params p USING (tagging_run_id)
),
source_code_groups AS (
  SELECT concept_id,
         code_system,
         code,
         count(*)::integer AS source_link_count,
         array_agg(legacy_tag_id ORDER BY legacy_tag_id) AS legacy_tag_ids
  FROM source_codes
  GROUP BY concept_id, code_system, code
),
code_mapping AS (
  SELECT source.concept_id,
         source.legacy_tag_id,
         source.code_system,
         source.code,
         output.concept_id IS NOT NULL AS mapped,
         coalesce(
           output.provenance->'legacy_tag_ids' ? source.legacy_tag_id,
           false
         ) AS provenance_preserved
  FROM source_codes source
  LEFT JOIN normalized_codes output
    ON (output.concept_id, output.code_system, output.code) =
       (source.concept_id, source.code_system, source.code)
),
master_resolution AS (
  SELECT output.concept_id,
         output.code_system,
         output.code,
         CASE output.code_system
           WHEN 'ATC' THEN EXISTS (
             SELECT 1
             FROM tw_drug.ref_atc master
             WHERE master.atc_code = output.code
           )
           WHEN 'ICD11' THEN EXISTS (
             SELECT 1
             FROM medical_knowledge.icd11_who master
             WHERE master.code = output.code
               AND master.release_id = output.master_release
           )
           WHEN 'NHI_TREATMENT' THEN EXISTS (
             SELECT 1
             FROM tw_health_open.nhi_payment_standard master
             WHERE master.code = output.code
           )
           ELSE false
         END AS resolved,
         output.public_safe
  FROM normalized_codes output
),
normalized_collisions AS (
  SELECT normalized_alias
  FROM nhi_rule_history_terminology.concept_alias alias_row
  JOIN params p USING (tagging_run_id)
  GROUP BY normalized_alias
  HAVING count(DISTINCT concept_id) > 1
),
consolidation_rows AS (
  SELECT concept.concept_id,
         concept.concept_type,
         concept.canonical_label_zh,
         concept.canonical_label_en,
         concept.provenance->>'review_basis' AS review_basis,
         jsonb_agg(
           jsonb_build_object(
             'legacy_tag_id', tag.legacy_tag_id,
             'tag_text', tag.tag_text,
             'tag_type', tag.tag_type,
             'entity_type', tag.entity_type,
             'external_codes', (
               SELECT coalesce(
                 jsonb_agg(
                   jsonb_build_object(
                     'system', source.code_system,
                     'code', source.code
                   )
                   ORDER BY source.code_system, source.code
                 ),
                 '[]'::jsonb
               )
               FROM source_codes source
               WHERE source.legacy_tag_id = tag.legacy_tag_id
             )
           )
           ORDER BY tag.legacy_tag_id
         ) AS members
  FROM nhi_rule_history_terminology.run_concept concept
  JOIN params p USING (tagging_run_id)
  JOIN seed_tags tag USING (concept_id)
  GROUP BY concept.concept_id,
           concept.concept_type,
           concept.canonical_label_zh,
           concept.canonical_label_en,
           concept.provenance
  HAVING count(*) > 1
),
receipt AS (
  SELECT jsonb_build_object(
    'schema', 'nhi-rule-history/reviewed-seed-admission-reconciliation/v1',
    'tagging_run_id', r.tagging_run_id,
    'publication_run_id', r.publication_run_id,
    'seed_enrichment_run_id', r.seed_enrichment_run_id,
    'input_fingerprint', r.input_fingerprint,
    'output_fingerprint', r.output_fingerprint,
    'sealed_fingerprint', r.sealed_fingerprint,
    'alias_admission_policy', r.alias_admission_policy,
    'reviewed_seed_admission', jsonb_build_object(
      'reviewed_seed_tag_count', (
        SELECT count(*) FROM nhi_rule_history_clause.clause_semantic_tag tag
        WHERE tag.enrichment_run_id = r.seed_enrichment_run_id
      ),
      'linked_seed_tag_count', (SELECT count(*) FROM seed_links),
      'distinct_linked_seed_tag_count', (
        SELECT count(DISTINCT legacy_tag_id) FROM seed_links
      ),
      'admitted_alias_without_reviewed_seed_link', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.concept_alias alias_row
        WHERE alias_row.tagging_run_id = r.tagging_run_id
          AND alias_row.production_status = 'admitted'
          AND NOT EXISTS (
            SELECT 1 FROM seed_links link
            WHERE link.concept_id = alias_row.concept_id
          )
      ),
      'admitted_alias_from_model_only_source', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.concept_alias alias_row
        WHERE alias_row.tagging_run_id = r.tagging_run_id
          AND alias_row.production_status = 'admitted'
          AND alias_row.source_status <> 'source_observed'
      ),
      'admitted_context_required_alias', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.concept_alias alias_row
        WHERE alias_row.tagging_run_id = r.tagging_run_id
          AND alias_row.production_status = 'admitted'
          AND alias_row.match_rule = 'context_required'
      ),
      'admitted_collision_involved_alias', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.concept_alias alias_row
        WHERE alias_row.tagging_run_id = r.tagging_run_id
          AND alias_row.production_status = 'admitted'
          AND alias_row.normalized_alias IN (
            SELECT normalized_alias FROM normalized_collisions
          )
      ),
      'admitted_occurrence_via_nonadmitted_alias', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.clause_occurrence occurrence
        JOIN nhi_rule_history_terminology.concept_alias alias_row
          ON (alias_row.tagging_run_id, alias_row.alias_id) =
             (occurrence.tagging_run_id, occurrence.alias_id)
        WHERE occurrence.tagging_run_id = r.tagging_run_id
          AND occurrence.occurrence_status = 'admitted'
          AND alias_row.production_status <> 'admitted'
      ),
      'admitted_occurrence_via_unreviewed_concept', (
        SELECT count(*)
        FROM nhi_rule_history_terminology.clause_occurrence occurrence
        JOIN nhi_rule_history_terminology.run_concept concept
          ON (concept.tagging_run_id, concept.concept_id) =
             (occurrence.tagging_run_id, occurrence.concept_id)
        WHERE occurrence.tagging_run_id = r.tagging_run_id
          AND occurrence.occurrence_status = 'admitted'
          AND (
            concept.review_status <> 'reviewed_seed_group'
            OR NOT EXISTS (
              SELECT 1 FROM seed_links link
              WHERE link.concept_id = occurrence.concept_id
            )
          )
      ),
      'consolidation_count', (SELECT count(*) FROM consolidation_rows),
      'consolidations', coalesce(
        (SELECT jsonb_agg(to_jsonb(item) ORDER BY item.concept_id)
         FROM consolidation_rows item),
        '[]'::jsonb
      )
    ),
    'external_code_conservation', jsonb_build_object(
      'source_external_code_links', (SELECT count(*) FROM source_codes),
      'source_by_system', (
        SELECT jsonb_object_agg(code_system, code_count ORDER BY code_system)
        FROM (
          SELECT code_system, count(*) AS code_count
          FROM source_codes
          GROUP BY code_system
        ) grouped
      ),
      'normalized_external_code_rows', (SELECT count(*) FROM normalized_codes),
      'explicit_duplicate_collapses', (
        SELECT coalesce(sum(source_link_count - 1), 0)
        FROM source_code_groups
      ),
      'unmapped_source_links', (
        SELECT count(*) FROM code_mapping WHERE NOT mapped
      ),
      'source_links_without_output_provenance', (
        SELECT count(*) FROM code_mapping
        WHERE mapped AND NOT provenance_preserved
      ),
      'conflicting_collapses', (
        SELECT count(*)
        FROM source_code_groups source
        JOIN normalized_codes output
          ON (output.concept_id, output.code_system, output.code) =
             (source.concept_id, source.code_system, source.code)
        WHERE source.source_link_count > 1
          AND output.provenance->'legacy_tag_ids'
              IS DISTINCT FROM to_jsonb(source.legacy_tag_ids)
      ),
      'unresolved_master_rows', (
        SELECT count(*) FROM master_resolution WHERE NOT resolved
      ),
      'non_public_safe_rows', (
        SELECT count(*) FROM master_resolution WHERE NOT public_safe
      ),
      'duplicate_collapses', coalesce(
        (
          SELECT jsonb_agg(
            jsonb_build_object(
              'concept_id', source.concept_id,
              'system', source.code_system,
              'code', source.code,
              'legacy_tag_ids', source.legacy_tag_ids
            )
            ORDER BY source.concept_id, source.code_system, source.code
          )
          FROM source_code_groups source
          WHERE source.source_link_count > 1
        ),
        '[]'::jsonb
      )
    )
  ) AS value
  FROM run r
)
SELECT jsonb_pretty(value)
FROM receipt;
