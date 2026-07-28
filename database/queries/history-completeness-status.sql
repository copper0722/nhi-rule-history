\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

BEGIN TRANSACTION READ ONLY;

SELECT
  to_regclass(
    'nhi_rule_history_annotation_stage.date_annotation'
  ) IS NOT NULL AS has_annotation_stage,
  to_regclass(
    'nhi_rule_history_event_resolution_stage.resolution_outcome'
  ) IS NOT NULL AS has_resolution_stage,
  to_regclass(
    'nhi_rule_history_candidate_stage.current_candidate_state'
  ) IS NOT NULL AS has_candidate_stage,
  to_regnamespace('nhi_rule_history') IS NOT NULL
    AS has_canonical_schema
\gset

\if :has_annotation_stage
SELECT jsonb_build_object(
  'check', 'source_date_annotations',
  'total', count(*),
  'valid_calendar_candidates',
    count(*) FILTER (WHERE normalization_status = 'normalized'),
  'invalid_calendar_candidates',
    count(*) FILTER (
      WHERE normalization_status = 'invalid_calendar_date'
    ),
  'articles_with_raw_candidates', count(DISTINCT article_id),
  'articles_with_valid_calendar_candidates',
    count(DISTINCT article_id) FILTER (
      WHERE normalization_status = 'normalized'
    ),
  'unresolved_event',
    count(*) FILTER (WHERE resolution_status = 'unresolved_event')
)
FROM nhi_rule_history_annotation_stage.date_annotation;
\else
SELECT jsonb_build_object(
  'check', 'source_date_annotations',
  'stage_missing', true
);
\endif

\if :has_resolution_stage
SELECT jsonb_build_object(
  'check', 'event_resolution',
  'total', count(*),
  'resolved_candidate',
    count(*) FILTER (WHERE resolution_status = 'resolved_candidate'),
  'ambiguous',
    count(*) FILTER (WHERE resolution_status = 'ambiguous'),
  'no_match',
    count(*) FILTER (WHERE resolution_status = 'no_match'),
  'invalid',
    count(*) FILTER (WHERE resolution_status = 'invalid')
)
FROM nhi_rule_history_event_resolution_stage.resolution_outcome;
\else
SELECT jsonb_build_object(
  'check', 'event_resolution',
  'stage_missing', true
);
\endif

\if :has_candidate_stage
SELECT jsonb_build_object(
  'check', 'candidate_proposals',
  'total', count(*),
  'needs_review', count(*) FILTER (WHERE state = 'needs_review')
)
FROM nhi_rule_history_candidate_stage.current_candidate_state;
\else
SELECT jsonb_build_object(
  'check', 'candidate_proposals',
  'stage_missing', true
);
\endif

SELECT jsonb_build_object(
  'check', 'canonical_history',
  'canonical_schema_exists', :'has_canonical_schema'::boolean,
  'preflight_can_certify_complete_history',
    CASE
      WHEN :'has_canonical_schema'::boolean THEN false
      ELSE false
    END,
  'reason',
    CASE
      WHEN :'has_canonical_schema'::boolean
        THEN 'canonical schema exists; run canonical completion gates'
      ELSE 'canonical schema absent'
    END
);

COMMIT;
