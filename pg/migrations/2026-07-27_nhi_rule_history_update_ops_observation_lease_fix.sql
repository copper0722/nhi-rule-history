-- 2026-07-27 — preserve source chronology across delayed backlog staging
--
-- The lease bounds model the bounded primary/fallback worker attempt. An
-- immutable URL observation can legitimately predate that worker by more than
-- six hours when a queue item is retried.  The lower lease bound therefore
-- does not govern source chronology, but the source must already exist before
-- the earliest worker attempt.  URL predecessor relations are derived by a
-- chronological view instead of being asserted at append time.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history_update_ops-global', 0)
);

DO $temp_name_guard$
BEGIN
  IF to_regclass(
       'pg_temp.expected_nhi_url_response_chronology_observation_lease_fix_v1'
     ) IS NOT NULL THEN
    RAISE EXCEPTION
      'temporary chronology verifier relation name collision'
      USING ERRCODE = 'duplicate_object';
  END IF;
END;
$temp_name_guard$;

CREATE TEMP VIEW
  expected_nhi_url_response_chronology_observation_lease_fix_v1
AS
WITH ordered AS (
  SELECT
    observation.*,
    lag(observation.url_observation_id) OVER chronology
      AS chronological_previous_observation_id,
    lag(observation.artifact_sha256) OVER chronology
      AS chronological_previous_artifact_sha256,
    lag(observation.final_url) OVER chronology
      AS chronological_previous_final_url
  FROM nhi_rule_history_update_ops.url_observation observation
  WHERE observation.outcome = 'response'
    AND observation.artifact_sha256 IS NOT NULL
  WINDOW chronology AS (
    PARTITION BY observation.requested_url
    ORDER BY observation.observed_at, observation.url_observation_id
  )
)
SELECT
  ordered.*,
  CASE
    WHEN chronological_previous_observation_id IS NULL
      THEN 'first_observation'
    WHEN chronological_previous_artifact_sha256 = artifact_sha256
      THEN 'same_bytes'
    WHEN chronological_previous_final_url IS DISTINCT FROM final_url
      THEN 'redirect_changed'
    ELSE 'same_url_new_bytes'
  END AS chronological_relation_to_previous
FROM ordered;

DO $guard$
DECLARE
  expected_comment text :=
    'Stage-only operational evidence for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_update_ops/v1';
  observation_fix_comment text :=
    'Checks lease-owner row consistency and source chronology; owner_key is metadata, not actor authentication. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';
  worker_fix_comment text :=
    'Serializes on the job lease and refuses attempts that would postdate an already-recorded source observation. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';
  view_fix_comment text :=
    'Chronological URL-response predecessor projection; authoritative over append-time relation fields. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';
  base_observation_source text := $source$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id;
  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.observed_at < lease_start
     OR NEW.observed_at > lease_end THEN
    RAISE EXCEPTION
      'URL observation is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$source$;
  fixed_observation_source text := $source$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  earliest_attempt_start timestamptz;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id
  FOR UPDATE;

  IF NOT FOUND OR lease_owner IS DISTINCT FROM NEW.owner_key THEN
    RAISE EXCEPTION
      'URL observation owner metadata differs from its job lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT min(attempt.started_at)
    INTO earliest_attempt_start
  FROM nhi_rule_history_update_ops.worker_attempt attempt
  WHERE attempt.job_id = NEW.job_id
    AND attempt.lease_id = NEW.lease_id
    AND attempt.owner_key = NEW.owner_key;
  IF earliest_attempt_start IS NULL
     AND (
       NEW.observed_at < lease_start
       OR NEW.observed_at > lease_end
     ) THEN
    RAISE EXCEPTION
      'non-worker URL observation is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  ELSIF earliest_attempt_start IS NOT NULL
     AND NEW.observed_at > earliest_attempt_start THEN
    RAISE EXCEPTION
      'URL observation postdates the earliest worker attempt'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$source$;
  base_worker_source text := $source$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  primary_status text;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id;

  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.started_at < lease_start
     OR NEW.completed_at > lease_end THEN
    RAISE EXCEPTION
      'worker attempt is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NEW.lane = 'fallback' THEN
    SELECT status INTO primary_status
    FROM nhi_rule_history_update_ops.worker_attempt
    WHERE job_id = NEW.job_id
      AND attempt_id = NEW.primary_attempt_id
      AND lane = 'primary';
    IF primary_status IS DISTINCT FROM 'failed' THEN
      RAISE EXCEPTION
        'fallback must reference the failed primary attempt for the same job'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$source$;
  fixed_worker_source text := $source$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  primary_status text;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id
  FOR UPDATE;

  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.started_at < lease_start
     OR NEW.completed_at > lease_end THEN
    RAISE EXCEPTION
      'worker attempt is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_ops.url_observation observation
    WHERE observation.job_id = NEW.job_id
      AND observation.lease_id = NEW.lease_id
      AND observation.owner_key = NEW.owner_key
      AND observation.observed_at > NEW.started_at
  ) THEN
    RAISE EXCEPTION
      'worker attempt starts before an existing source observation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NEW.lane = 'fallback' THEN
    SELECT status INTO primary_status
    FROM nhi_rule_history_update_ops.worker_attempt
    WHERE job_id = NEW.job_id
      AND attempt_id = NEW.primary_attempt_id
      AND lane = 'primary';
    IF primary_status IS DISTINCT FROM 'failed' THEN
      RAISE EXCEPTION
        'fallback must reference the failed primary attempt for the same job'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$source$;
  expected_view_definition text :=
    pg_get_viewdef(
      'pg_temp.expected_nhi_url_response_chronology_observation_lease_fix_v1'
        ::regclass,
      true
    );
  observation_comment text;
  worker_comment text;
  observation_source text;
  worker_source text;
  base_state boolean;
  fixed_state boolean;
  chronology_relation regclass;
  schema_owner oid;
BEGIN
  IF obj_description(
       'nhi_rule_history_update_ops'::regnamespace,
       'pg_namespace'
     ) IS DISTINCT FROM expected_comment THEN
    RAISE EXCEPTION
      'nhi_rule_history_update_ops is not the managed v1 schema'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT nspowner INTO schema_owner
  FROM pg_namespace
  WHERE oid = 'nhi_rule_history_update_ops'::regnamespace;

  IF (
    SELECT count(*)
    FROM pg_trigger trigger
    WHERE NOT trigger.tgisinternal
      AND trigger.tgenabled = 'O'
      AND trigger.tgtype = 7
      AND trigger.tgnargs = 0
      AND trigger.tgqual IS NULL
      AND (
        (
          trigger.tgrelid =
            'nhi_rule_history_update_ops.url_observation'::regclass
          AND trigger.tgname = 'url_observation_insert_guard'
          AND trigger.tgfoid =
            'nhi_rule_history_update_ops.guard_owned_observation_insert()'
              ::regprocedure::oid
        )
        OR (
          trigger.tgrelid =
            'nhi_rule_history_update_ops.worker_attempt'::regclass
          AND trigger.tgname = 'worker_attempt_insert_guard'
          AND trigger.tgfoid =
            'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
              ::regprocedure::oid
        )
      )
  ) <> 2 THEN
    RAISE EXCEPTION
      'managed chronology triggers are missing, disabled, or rebound'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_trigger trigger
    WHERE NOT trigger.tgisinternal
      AND (trigger.tgtype & 4) = 4
      AND trigger.tgrelid IN (
        'nhi_rule_history_update_ops.url_observation'::regclass,
        'nhi_rule_history_update_ops.worker_attempt'::regclass
      )
      AND NOT (
        (
          trigger.tgrelid =
            'nhi_rule_history_update_ops.url_observation'::regclass
          AND trigger.tgname = 'url_observation_insert_guard'
          AND trigger.tgenabled = 'O'
          AND trigger.tgtype = 7
          AND trigger.tgnargs = 0
          AND trigger.tgqual IS NULL
          AND trigger.tgfoid =
            'nhi_rule_history_update_ops.guard_owned_observation_insert()'
              ::regprocedure::oid
        )
        OR (
          trigger.tgrelid =
            'nhi_rule_history_update_ops.worker_attempt'::regclass
          AND trigger.tgname = 'worker_attempt_insert_guard'
          AND trigger.tgenabled = 'O'
          AND trigger.tgtype = 7
          AND trigger.tgnargs = 0
          AND trigger.tgqual IS NULL
          AND trigger.tgfoid =
            'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
              ::regprocedure::oid
        )
      )
  ) THEN
    RAISE EXCEPTION
      'unmanaged INSERT trigger exists on a chronology relation'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_class relation
    WHERE relation.oid IN (
      'nhi_rule_history_update_ops.url_observation'::regclass,
      'nhi_rule_history_update_ops.worker_attempt'::regclass
    )
      AND relation.relowner <> schema_owner
  ) THEN
    RAISE EXCEPTION
      'chronology trigger relation owner drifted'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  SELECT
    obj_description(
      'nhi_rule_history_update_ops.guard_owned_observation_insert()'
        ::regprocedure,
      'pg_proc'
    ),
    prosrc
    INTO observation_comment, observation_source
  FROM pg_proc
  WHERE oid =
      'nhi_rule_history_update_ops.guard_owned_observation_insert()'
        ::regprocedure;
  SELECT
    obj_description(
      'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
        ::regprocedure,
      'pg_proc'
    ),
    prosrc
    INTO worker_comment, worker_source
  FROM pg_proc
  WHERE oid =
      'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
        ::regprocedure;

  base_state :=
    observation_comment IS NULL
    AND worker_comment IS NULL
    AND observation_source = base_observation_source
    AND worker_source = base_worker_source;
  fixed_state :=
    observation_comment = observation_fix_comment
    AND worker_comment = worker_fix_comment
    AND observation_source = fixed_observation_source
    AND worker_source = fixed_worker_source;
  IF NOT base_state AND NOT fixed_state THEN
    RAISE EXCEPTION
      'unknown exact chronology function state refuses observation fix'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_proc function
    JOIN pg_namespace namespace
      ON namespace.oid = function.pronamespace
    JOIN pg_language language
      ON language.oid = function.prolang
    WHERE function.oid IN (
      'nhi_rule_history_update_ops.guard_owned_observation_insert()'
        ::regprocedure,
      'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
        ::regprocedure
    )
      AND (
        function.proowner <> schema_owner
        OR function.prokind <> 'f'
        OR function.prosecdef
        OR function.proleakproof
        OR function.proisstrict
        OR function.provolatile <> 'v'
        OR function.proparallel <> 'u'
        OR function.proconfig IS DISTINCT FROM
          ARRAY['search_path=pg_catalog']::text[]
        OR language.lanname <> 'plpgsql'
        OR function.prorettype <> 'trigger'::regtype
        OR function.pronargs <> 0
        OR EXISTS (
          SELECT 1
          FROM aclexplode(
            coalesce(
              function.proacl,
              acldefault('f', function.proowner)
            )
          ) acl
          WHERE acl.grantee <> function.proowner
             OR acl.grantor <> function.proowner
        )
      )
  ) THEN
    RAISE EXCEPTION
      'chronology function attributes, owner, or ACL drifted'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  chronology_relation := to_regclass(
    'nhi_rule_history_update_ops.v_url_response_chronology'
  );
  IF base_state AND chronology_relation IS NOT NULL THEN
    RAISE EXCEPTION
      'base chronology state unexpectedly contains the managed view'
      USING ERRCODE = 'integrity_constraint_violation';
  ELSIF fixed_state THEN
    IF chronology_relation IS NULL
       OR (
         SELECT relkind <> 'v'
                OR relowner <> schema_owner
                OR obj_description(chronology_relation, 'pg_class')
                     IS DISTINCT FROM view_fix_comment
         FROM pg_class
         WHERE oid = chronology_relation
       )
       OR pg_get_viewdef(chronology_relation, true)
            IS DISTINCT FROM expected_view_definition
       OR (
         WITH actual_acl AS (
           SELECT
             acl.grantor,
             acl.grantee,
             acl.privilege_type,
             acl.is_grantable
           FROM pg_class relation
           CROSS JOIN LATERAL aclexplode(
             coalesce(
               relation.relacl,
               acldefault('r', relation.relowner)
             )
           ) acl
           WHERE relation.oid = chronology_relation
         ),
         expected_acl AS (
           SELECT
             acl.grantor,
             acl.grantee,
             acl.privilege_type,
             acl.is_grantable
           FROM aclexplode(acldefault('r', schema_owner)) acl
           UNION ALL
           SELECT
             schema_owner,
             'nhi_rule_history_update_runtime'::regrole::oid,
             'SELECT'::text,
             false
         )
         SELECT EXISTS (
           (
             SELECT * FROM actual_acl
             EXCEPT ALL
             SELECT * FROM expected_acl
           )
           UNION ALL
           (
             SELECT * FROM expected_acl
             EXCEPT ALL
             SELECT * FROM actual_acl
           )
         )
       )
    THEN
      RAISE EXCEPTION
        'managed URL chronology view definition, owner, or ACL drifted'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$guard$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_ops.guard_owned_observation_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  earliest_attempt_start timestamptz;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id
  FOR UPDATE;

  IF NOT FOUND OR lease_owner IS DISTINCT FROM NEW.owner_key THEN
    RAISE EXCEPTION
      'URL observation owner metadata differs from its job lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT min(attempt.started_at)
    INTO earliest_attempt_start
  FROM nhi_rule_history_update_ops.worker_attempt attempt
  WHERE attempt.job_id = NEW.job_id
    AND attempt.lease_id = NEW.lease_id
    AND attempt.owner_key = NEW.owner_key;
  IF earliest_attempt_start IS NULL
     AND (
       NEW.observed_at < lease_start
       OR NEW.observed_at > lease_end
     ) THEN
    RAISE EXCEPTION
      'non-worker URL observation is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  ELSIF earliest_attempt_start IS NOT NULL
     AND NEW.observed_at > earliest_attempt_start THEN
    RAISE EXCEPTION
      'URL observation postdates the earliest worker attempt'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_update_ops.guard_worker_attempt_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  lease_owner text;
  lease_start timestamptz;
  lease_end timestamptz;
  primary_status text;
BEGIN
  SELECT owner_key, acquired_at, expires_at
    INTO lease_owner, lease_start, lease_end
  FROM nhi_rule_history_update_ops.job_lease
  WHERE job_id = NEW.job_id AND lease_id = NEW.lease_id
  FOR UPDATE;

  IF NOT FOUND
     OR lease_owner IS DISTINCT FROM NEW.owner_key
     OR NEW.started_at < lease_start
     OR NEW.completed_at > lease_end THEN
    RAISE EXCEPTION
      'worker attempt is outside its owned lease'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_update_ops.url_observation observation
    WHERE observation.job_id = NEW.job_id
      AND observation.lease_id = NEW.lease_id
      AND observation.owner_key = NEW.owner_key
      AND observation.observed_at > NEW.started_at
  ) THEN
    RAISE EXCEPTION
      'worker attempt starts before an existing source observation'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NEW.lane = 'fallback' THEN
    SELECT status INTO primary_status
    FROM nhi_rule_history_update_ops.worker_attempt
    WHERE job_id = NEW.job_id
      AND attempt_id = NEW.primary_attempt_id
      AND lane = 'primary';
    IF primary_status IS DISTINCT FROM 'failed' THEN
      RAISE EXCEPTION
        'fallback must reference the failed primary attempt for the same job'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION
  nhi_rule_history_update_ops.guard_owned_observation_insert() IS
  'Checks lease-owner row consistency and source chronology; owner_key is metadata, not actor authentication. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';
COMMENT ON FUNCTION
  nhi_rule_history_update_ops.guard_worker_attempt_insert() IS
  'Serializes on the job lease and refuses attempts that would postdate an already-recorded source observation. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';

CREATE OR REPLACE VIEW
  nhi_rule_history_update_ops.v_url_response_chronology
AS
WITH ordered AS (
  SELECT
    observation.*,
    lag(observation.url_observation_id) OVER chronology
      AS chronological_previous_observation_id,
    lag(observation.artifact_sha256) OVER chronology
      AS chronological_previous_artifact_sha256,
    lag(observation.final_url) OVER chronology
      AS chronological_previous_final_url
  FROM nhi_rule_history_update_ops.url_observation observation
  WHERE observation.outcome = 'response'
    AND observation.artifact_sha256 IS NOT NULL
  WINDOW chronology AS (
    PARTITION BY observation.requested_url
    ORDER BY observation.observed_at, observation.url_observation_id
  )
)
SELECT
  ordered.*,
  CASE
    WHEN chronological_previous_observation_id IS NULL
      THEN 'first_observation'
    WHEN chronological_previous_artifact_sha256 = artifact_sha256
      THEN 'same_bytes'
    WHEN chronological_previous_final_url IS DISTINCT FROM final_url
      THEN 'redirect_changed'
    ELSE 'same_url_new_bytes'
  END AS chronological_relation_to_previous
FROM ordered;

COMMENT ON VIEW
  nhi_rule_history_update_ops.v_url_response_chronology IS
  'Chronological URL-response predecessor projection; authoritative over append-time relation fields. managed=nhi_rule_history_update_ops/observation-lease-fix-v1';

COMMENT ON COLUMN
  nhi_rule_history_update_ops.url_observation.previous_artifact_sha256 IS
  'Append-time, non-authoritative field retained for compatibility; use v_url_response_chronology for chronological predecessor evidence.';
COMMENT ON COLUMN
  nhi_rule_history_update_ops.url_observation.relation_to_previous IS
  'Append-time, non-authoritative field retained for compatibility; use v_url_response_chronology for chronological relation evidence.';
COMMENT ON COLUMN
  nhi_rule_history_update_ops.content_artifact.first_observed_at IS
  'Observation recorded when the content-addressed artifact row was first inserted; delayed backfill may contain an earlier URL observed_at, so chronological consumers must derive min(url_observation.observed_at).';

REVOKE ALL ON
  nhi_rule_history_update_ops.v_url_response_chronology FROM PUBLIC;
GRANT SELECT ON
  nhi_rule_history_update_ops.v_url_response_chronology
  TO nhi_rule_history_update_runtime;

DO $created_view_acl_guard$
DECLARE
  chronology_relation regclass :=
    'nhi_rule_history_update_ops.v_url_response_chronology'::regclass;
  schema_owner oid;
BEGIN
  SELECT nspowner INTO STRICT schema_owner
  FROM pg_namespace
  WHERE oid = 'nhi_rule_history_update_ops'::regnamespace;

  IF (
       SELECT relation.relowner <> schema_owner
       FROM pg_class relation
       WHERE relation.oid = chronology_relation
     )
     OR (
       WITH actual_acl AS (
         SELECT
           acl.grantor,
           acl.grantee,
           acl.privilege_type,
           acl.is_grantable
         FROM pg_class relation
         CROSS JOIN LATERAL aclexplode(
           coalesce(
             relation.relacl,
             acldefault('r', relation.relowner)
           )
         ) acl
         WHERE relation.oid = chronology_relation
       ),
       expected_acl AS (
         SELECT
           acl.grantor,
           acl.grantee,
           acl.privilege_type,
           acl.is_grantable
         FROM aclexplode(acldefault('r', schema_owner)) acl
         UNION ALL
         SELECT
           schema_owner,
           'nhi_rule_history_update_runtime'::regrole::oid,
           'SELECT'::text,
           false
       )
       SELECT EXISTS (
         (
           SELECT * FROM actual_acl
           EXCEPT ALL
           SELECT * FROM expected_acl
         )
         UNION ALL
         (
           SELECT * FROM expected_acl
           EXCEPT ALL
           SELECT * FROM actual_acl
         )
       )
     ) THEN
    RAISE EXCEPTION
      'created URL chronology view owner or ACL is not exact'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$created_view_acl_guard$;

DROP VIEW
  pg_temp.expected_nhi_url_response_chronology_observation_lease_fix_v1;

COMMIT;
