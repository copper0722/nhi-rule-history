-- 2026-07-28 — minimal canonical NHI rule-history production core
--
-- This schema is intentionally independent from legacy tw_drug tables.  It
-- provides the smallest canonical surface required for one verified,
-- full-single-clause amendment.  Canonical mutation is owned by a NOLOGIN
-- owner role and is exposed later only through the promotion function.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history-canonical-v1-global', 0)
);

DO $schema_guard$
DECLARE
  managed_prefix text :=
    'Canonical NHI drug reimbursement-rule history. managed=nhi_rule_history_canonical/v1';
  existing_comment text;
  expected_fingerprint text;
  actual_fingerprint text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname = 'nhi_rule_history'
  ) THEN
    CREATE SCHEMA nhi_rule_history;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history IS %L',
      managed_prefix
    );
  ELSE
    SELECT obj_description(oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history';
    IF existing_comment !~
       (
         '^' || managed_prefix ||
         ' contract_sha256=[0-9a-f]{64}$'
       ) THEN
      RAISE EXCEPTION
        'nhi_rule_history exists without the sealed canonical v1 contract marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    expected_fingerprint :=
      substring(existing_comment FROM 'contract_sha256=([0-9a-f]{64})$');

    IF EXISTS (
      SELECT 1
      FROM pg_class relation_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
        AND relation_row.relkind IN ('r', 'p', 'm', 'S', 'i')
        AND relation_row.relpersistence <> 'p'
    ) THEN
      RAISE EXCEPTION
        'canonical v1 requires persistent relations'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    WITH contract_line AS (
      SELECT
        'N|' || namespace_row.nspname || '|' ||
        pg_get_userbyid(namespace_row.nspowner) || '|' ||
        coalesce(namespace_row.nspacl::text, '') AS line
      FROM pg_namespace namespace_row
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'M|' || granted_role.rolname || '|forbidden=' ||
        count(membership_row.member) FILTER (
          WHERE
            granted_role.rolname = 'nhi_rule_history_owner'
            OR NOT member_role.rolcanlogin
            OR membership_row.admin_option
            OR position(
              granted_role.rolname IN coalesce(
                shobj_description(member_role.oid, 'pg_authid'),
                ''
              )
            ) = 0
        )::text
      FROM pg_roles granted_role
      LEFT JOIN pg_auth_members membership_row
        ON membership_row.roleid = granted_role.oid
      LEFT JOIN pg_roles member_role
        ON member_role.oid = membership_row.member
      WHERE granted_role.rolname IN (
        'nhi_rule_history_owner',
        'nhi_rule_history_reader',
        'nhi_rule_history_format_detector_writer',
        'nhi_rule_history_format_detector_reviewer',
        'nhi_rule_history_promotion_writer',
        'nhi_rule_history_promotion_reviewer',
        'nhi_rule_history_promotion_executor'
      )
      GROUP BY granted_role.rolname
      UNION ALL
      SELECT
        'O|' || member_role.rolname || '|outgoing=' ||
        count(membership_row.roleid)::text
      FROM pg_roles member_role
      LEFT JOIN pg_auth_members membership_row
        ON membership_row.member = member_role.oid
      WHERE member_role.rolname IN (
        'nhi_rule_history_owner',
        'nhi_rule_history_reader',
        'nhi_rule_history_format_detector_writer',
        'nhi_rule_history_format_detector_reviewer',
        'nhi_rule_history_promotion_writer',
        'nhi_rule_history_promotion_reviewer',
        'nhi_rule_history_promotion_executor'
      )
      GROUP BY member_role.rolname
      UNION ALL
      SELECT
        'R|' || relation_row.relname || '|' ||
        relation_row.relkind::text || '|' ||
        pg_get_userbyid(relation_row.relowner) || '|' ||
        coalesce(relation_row.relacl::text, '') || '|' ||
        relation_row.relrowsecurity::text || '|' ||
        relation_row.relforcerowsecurity::text || '|' ||
        relation_row.relreplident::text || '|' ||
        relation_row.relpersistence::text || '|' ||
        coalesce(access_method.amname, '') || '|' ||
        coalesce(tablespace_row.spcname, '') || '|' ||
        coalesce(relation_row.reloptions::text, '')
      FROM pg_class relation_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      LEFT JOIN pg_am access_method
        ON access_method.oid = relation_row.relam
      LEFT JOIN pg_tablespace tablespace_row
        ON tablespace_row.oid = relation_row.reltablespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'A|' || relation_row.relname || '|' ||
        attribute_row.attnum::text || '|' ||
        attribute_row.attname || '|' ||
        format_type(
          attribute_row.atttypid,
          attribute_row.atttypmod
        ) || '|' ||
        attribute_row.attnotnull::text || '|' ||
        attribute_row.attidentity::text || '|' ||
        attribute_row.attgenerated::text || '|' ||
        coalesce(collation_namespace.nspname, '') || '|' ||
        coalesce(collation_row.collname, '') || '|' ||
        coalesce(
          pg_get_expr(default_row.adbin, default_row.adrelid),
          ''
        )
      FROM pg_attribute attribute_row
      JOIN pg_class relation_row
        ON relation_row.oid = attribute_row.attrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      LEFT JOIN pg_attrdef default_row
        ON default_row.adrelid = attribute_row.attrelid
        AND default_row.adnum = attribute_row.attnum
      LEFT JOIN pg_collation collation_row
        ON collation_row.oid = attribute_row.attcollation
      LEFT JOIN pg_namespace collation_namespace
        ON collation_namespace.oid = collation_row.collnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
        AND attribute_row.attnum > 0
        AND NOT attribute_row.attisdropped
      UNION ALL
      SELECT
        'C|' || coalesce(relation_row.relname, '') || '|' ||
        constraint_row.conname || '|' ||
        constraint_row.contype::text || '|' ||
        constraint_row.convalidated::text || '|' ||
        pg_get_constraintdef(constraint_row.oid, true)
      FROM pg_constraint constraint_row
      LEFT JOIN pg_class relation_row
        ON relation_row.oid = constraint_row.conrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = constraint_row.connamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'I|' || index_relation.relname || '|' ||
        pg_get_indexdef(index_relation.oid)
      FROM pg_index index_row
      JOIN pg_class index_relation
        ON index_relation.oid = index_row.indexrelid
      JOIN pg_class table_relation
        ON table_relation.oid = index_row.indrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = table_relation.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'P|' || procedure_row.proname || '|' ||
        pg_get_function_identity_arguments(procedure_row.oid) || '|' ||
        procedure_row.prosecdef::text || '|' ||
        pg_get_userbyid(procedure_row.proowner) || '|' ||
        coalesce(procedure_row.proconfig::text, '') || '|' ||
        coalesce(procedure_row.proacl::text, '') || '|' ||
        pg_get_functiondef(procedure_row.oid)
      FROM pg_proc procedure_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = procedure_row.pronamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'G|' || relation_row.relname || '|' ||
        trigger_row.tgname || '|' ||
        trigger_row.tgenabled::text || '|' ||
        pg_get_triggerdef(trigger_row.oid, true)
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row
        ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
        AND NOT trigger_row.tgisinternal
      UNION ALL
      SELECT
        'Q|' || relation_row.relname || '|' ||
        policy_row.polname || '|' ||
        policy_row.polpermissive::text || '|' ||
        policy_row.polroles::text || '|' ||
        policy_row.polcmd::text || '|' ||
        coalesce(
          pg_get_expr(policy_row.polqual, policy_row.polrelid),
          ''
        ) || '|' ||
        coalesce(
          pg_get_expr(
            policy_row.polwithcheck,
            policy_row.polrelid
          ),
          ''
        )
      FROM pg_policy policy_row
      JOIN pg_class relation_row
        ON relation_row.oid = policy_row.polrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
      UNION ALL
      SELECT
        'Y|' || type_row.typname || '|' ||
        type_row.typtype::text || '|' ||
        pg_get_userbyid(type_row.typowner) || '|' ||
        coalesce(type_row.typacl::text, '')
      FROM pg_type type_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = type_row.typnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history'
        AND type_row.typtype IN ('d', 'e')
    )
    SELECT encode(
      sha256(
        convert_to(
          jsonb_agg(line ORDER BY line)::text,
          'UTF8'
        )
      ),
      'hex'
    )
      INTO actual_fingerprint
    FROM contract_line;

    IF actual_fingerprint IS DISTINCT FROM expected_fingerprint THEN
      RAISE EXCEPTION
        'canonical v1 structural contract drift: expected %, observed %',
        expected_fingerprint,
        actual_fingerprint
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $role_guard$
DECLARE
  role_name text;
  role_comment text;
  expected_comment text;
  role_row record;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'nhi_rule_history_owner',
    'nhi_rule_history_reader',
    'nhi_rule_history_format_detector_writer',
    'nhi_rule_history_format_detector_reviewer',
    'nhi_rule_history_promotion_writer',
    'nhi_rule_history_promotion_reviewer',
    'nhi_rule_history_promotion_executor'
  ]
  LOOP
    expected_comment := CASE role_name
      WHEN 'nhi_rule_history_owner' THEN
        'NOLOGIN owner for canonical NHI rule-history objects and the audited promotion function. managed=nhi_rule_history_owner/v1'
      WHEN 'nhi_rule_history_reader' THEN
        'NOLOGIN read-only capability for canonical NHI rule history. managed=nhi_rule_history_reader/v1'
      WHEN 'nhi_rule_history_format_detector_writer' THEN
        'NOLOGIN capability for byte-derived format registration through the sealed detector function only. managed=nhi_rule_history_format_detector_writer/v1'
      WHEN 'nhi_rule_history_format_detector_reviewer' THEN
        'NOLOGIN capability for independent byte-derived format attestation through the sealed verifier function only. managed=nhi_rule_history_format_detector_reviewer/v1'
      WHEN 'nhi_rule_history_promotion_writer' THEN
        'NOLOGIN capability for immutable promotion evidence production; no review, execution, or canonical DML. managed=nhi_rule_history_promotion_writer/v1'
      WHEN 'nhi_rule_history_promotion_reviewer' THEN
        'NOLOGIN capability for independent ready/rejected decisions; no evidence production, execution, or canonical DML. managed=nhi_rule_history_promotion_reviewer/v1'
      ELSE
        'NOLOGIN capability for canonical promotion SELECT and promote_case execution only; no evidence or canonical DML. managed=nhi_rule_history_promotion_executor/v1'
    END;

    SELECT
      rolcanlogin,
      rolsuper,
      rolcreatedb,
      rolcreaterole,
      rolinherit,
      rolreplication,
      rolbypassrls,
      shobj_description(oid, 'pg_authid') AS object_comment
      INTO role_row
    FROM pg_roles
    WHERE rolname = role_name;

    IF NOT FOUND THEN
      EXECUTE format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
        role_name
      );
      EXECUTE format(
        'COMMENT ON ROLE %I IS %L',
        role_name,
        expected_comment
      );
    ELSIF role_row.object_comment IS DISTINCT FROM expected_comment
       OR role_row.rolcanlogin
       OR role_row.rolsuper
       OR role_row.rolcreatedb
       OR role_row.rolcreaterole
       OR role_row.rolinherit
       OR role_row.rolreplication
       OR role_row.rolbypassrls THEN
      RAISE EXCEPTION
        '% exists without the managed least-privilege v1 contract',
        role_name
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1
    FROM pg_auth_members membership_row
    JOIN pg_roles granted_role
      ON granted_role.oid = membership_row.roleid
    JOIN pg_roles member_role
      ON member_role.oid = membership_row.member
    WHERE (
      granted_role.rolname = 'nhi_rule_history_owner'
      OR member_role.rolname = 'nhi_rule_history_owner'
    )
  ) THEN
    RAISE EXCEPTION
      'nhi_rule_history_owner must have zero role memberships'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_auth_members membership_row
    JOIN pg_roles granted_role
      ON granted_role.oid = membership_row.roleid
    JOIN pg_roles member_role
      ON member_role.oid = membership_row.member
    WHERE granted_role.rolname IN (
      'nhi_rule_history_reader',
      'nhi_rule_history_format_detector_writer',
      'nhi_rule_history_format_detector_reviewer',
      'nhi_rule_history_promotion_writer',
      'nhi_rule_history_promotion_reviewer',
      'nhi_rule_history_promotion_executor'
    )
      AND (
        NOT member_role.rolcanlogin
        OR member_role.rolsuper
        OR member_role.rolcreatedb
        OR member_role.rolcreaterole
        OR member_role.rolreplication
        OR member_role.rolbypassrls
        OR membership_row.admin_option
        OR shobj_description(member_role.oid, 'pg_authid') !~
          '^NHI rule-history capability login allowlist[.] managed=nhi_rule_history_capability_login/v1 roles=[a-z_,]+$'
        OR granted_role.rolname <> ALL (
          string_to_array(
            substring(
              shobj_description(member_role.oid, 'pg_authid')
              FROM ' roles=([a-z_,]+)$'
            ),
            ','
          )
        )
      )
  ) THEN
    RAISE EXCEPTION
      'capability role membership is not an explicitly allowlisted login'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_auth_members membership_row
    JOIN pg_roles member_role
      ON member_role.oid = membership_row.member
    WHERE member_role.rolname IN (
      'nhi_rule_history_reader',
      'nhi_rule_history_format_detector_writer',
      'nhi_rule_history_format_detector_reviewer',
      'nhi_rule_history_promotion_writer',
      'nhi_rule_history_promotion_reviewer',
      'nhi_rule_history_promotion_executor'
    )
  ) THEN
    RAISE EXCEPTION
      'managed capability roles may not inherit any other role'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  IF EXISTS (
    SELECT membership_row.member
    FROM pg_auth_members membership_row
    JOIN pg_roles granted_role
      ON granted_role.oid = membership_row.roleid
    WHERE granted_role.rolname IN (
      'nhi_rule_history_format_detector_writer',
      'nhi_rule_history_format_detector_reviewer'
    )
    GROUP BY membership_row.member
    HAVING count(DISTINCT granted_role.rolname) = 2
  ) THEN
    RAISE EXCEPTION
      'format detector producer and independent reviewer must be different authenticated principals'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END;
$role_guard$;

DO $domain_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type type_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = type_row.typnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND type_row.typname = 'sha256_hex'
      AND type_row.typtype = 'd'
  ) THEN
    CREATE DOMAIN nhi_rule_history.sha256_hex AS text
      CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END;
$domain_guard$;

CREATE TABLE IF NOT EXISTS nhi_rule_history.dataset_release (
  release_id text PRIMARY KEY,
  release_kind text NOT NULL CHECK (
    release_kind IN (
      'annual_full',
      'current_full',
      'current_chapter',
      'event_attachment'
    )
  ),
  official_label text NOT NULL,
  jurisdiction text NOT NULL DEFAULT 'TW' CHECK (jurisdiction = 'TW'),
  release_date date,
  release_date_basis text NOT NULL,
  source_page_url text NOT NULL CHECK (source_page_url ~ '^https://'),
  manifest_sha256 nhi_rule_history.sha256_hex NOT NULL UNIQUE,
  is_cumulative_anchor boolean NOT NULL,
  declared_rule_count integer CHECK (declared_rule_count >= 1),
  rule_set_fingerprint nhi_rule_history.sha256_hex,
  official_attachment_inventory_status text NOT NULL CHECK (
    official_attachment_inventory_status = 'exhaustive_verified'
  ),
  declared_official_attachment_count integer NOT NULL CHECK (
    declared_official_attachment_count >= 1
  ),
  official_attachment_inventory_fingerprint
    nhi_rule_history.sha256_hex NOT NULL,
  verification_status text NOT NULL CHECK (
    verification_status IN ('verified', 'quarantined')
  ),
  created_at timestamptz NOT NULL DEFAULT current_timestamp,
  UNIQUE (release_id, manifest_sha256),
  CHECK (
    (
      is_cumulative_anchor
      AND declared_rule_count IS NOT NULL
      AND rule_set_fingerprint IS NOT NULL
    )
    OR (
      NOT is_cumulative_anchor
      AND declared_rule_count IS NULL
      AND rule_set_fingerprint IS NULL
    )
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.source_artifact (
  artifact_id text PRIMARY KEY,
  official_url text NOT NULL CHECK (official_url ~ '^https://'),
  filename text NOT NULL,
  media_type text NOT NULL,
  byte_length bigint NOT NULL CHECK (byte_length >= 0),
  sha256 nhi_rule_history.sha256_hex NOT NULL UNIQUE,
  fetched_at timestamptz NOT NULL,
  fetch_transport text NOT NULL,
  licence text NOT NULL,
  verification_status text NOT NULL CHECK (
    verification_status IN (
      'full_text_verified',
      'binary_verified',
      'quarantined'
    )
  ),
  UNIQUE (artifact_id, sha256, byte_length)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.artifact_format_detection (
    detection_receipt_id text PRIMARY KEY,
    artifact_id text NOT NULL UNIQUE,
    raw_release_id text NOT NULL
      REFERENCES nhi_rule_history.dataset_release (release_id)
      ON DELETE RESTRICT,
    raw_manifest_sha256 nhi_rule_history.sha256_hex NOT NULL,
    artifact_sha256 nhi_rule_history.sha256_hex NOT NULL,
    artifact_byte_length bigint NOT NULL CHECK (
      artifact_byte_length >= 0
    ),
    detector_name text NOT NULL CHECK (
      detector_name = 'nhi-byte-media-detector'
    ),
    detector_version text NOT NULL CHECK (
      detector_version = 'nhi-byte-media-detector/v1'
    ),
    detector_executable_sha256
      nhi_rule_history.sha256_hex NOT NULL,
    detected_media_type text NOT NULL CHECK (
      detected_media_type <> ''
    ),
    detector_evidence jsonb NOT NULL CHECK (
      jsonb_typeof(detector_evidence) = 'object'
      AND detector_evidence <> '{}'::jsonb
      AND detector_evidence ? 'basis'
      AND detector_evidence ? 'magic_hex'
    ),
    detector_evidence_sha256
      nhi_rule_history.sha256_hex NOT NULL,
    detection_receipt_sha256
      nhi_rule_history.sha256_hex NOT NULL UNIQUE,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL,
    authority_role name NOT NULL CHECK (
      authority_role = 'nhi_rule_history_format_detector_writer'
    ),
    detected_at timestamptz NOT NULL,
    FOREIGN KEY (
      artifact_id,
      artifact_sha256,
      artifact_byte_length
    )
      REFERENCES nhi_rule_history.source_artifact
        (artifact_id, sha256, byte_length)
      ON DELETE RESTRICT,
    FOREIGN KEY (
      raw_release_id,
      raw_manifest_sha256
    )
      REFERENCES nhi_rule_history.dataset_release
        (release_id, manifest_sha256)
      ON DELETE RESTRICT,
    UNIQUE (
      detection_receipt_id,
      artifact_id,
      raw_release_id,
      raw_manifest_sha256,
      artifact_sha256,
      artifact_byte_length,
      detection_receipt_sha256
    ),
    CHECK (
      detector_evidence_sha256 =
        encode(
          sha256(convert_to(detector_evidence::text, 'UTF8')),
          'hex'
        )
    ),
    CHECK (
      detection_receipt_sha256 =
        encode(
          sha256(
            convert_to(
              jsonb_build_array(
                artifact_sha256,
                artifact_byte_length,
                raw_release_id,
                raw_manifest_sha256,
                detector_name,
                detector_version,
                detector_executable_sha256,
                detected_media_type,
                detector_evidence_sha256,
                recorded_by,
                authority_role
              )::text,
              'UTF8'
            )
          ),
          'hex'
        )
    ),
    CHECK (
      (
        detected_media_type = 'application/pdf'
        AND detector_evidence->>'basis' = 'pdf-magic'
        AND detector_evidence->>'magic_hex' = '255044462d'
        AND detector_evidence->>'pdf_integrity_verified' = 'false'
        AND detector_evidence->>'promotion_eligible' = 'false'
        AND detector_evidence->>'pdf_integrity_gate' =
          'blocked_pending_external_pdf_integrity_verifier'
      )
      OR (
        detected_media_type =
          'application/vnd.oasis.opendocument.text'
        AND detector_evidence->>'basis' = 'odf-zip-container'
        AND detector_evidence ?& ARRAY[
          'odf_mimetype', 'container_contract', 'first_entry',
          'mimetype_compression_method', 'entry_count',
          'required_entries', 'central_directory_sha256',
          'entry_manifest_sha256', 'contains_compressed_payload',
          'compressed_payload_integrity_verified',
          'promotion_eligible', 'archive_integrity_gate'
        ]
        AND detector_evidence->>'magic_hex' = '504b0304'
        AND detector_evidence->>'odf_mimetype' =
          'application/vnd.oasis.opendocument.text'
        AND detector_evidence->>'container_contract' =
          'odf-zip-container/v1'
        AND detector_evidence->>'first_entry' = 'mimetype'
        AND detector_evidence->>'mimetype_compression_method' = '0'
        AND (detector_evidence->>'entry_count')::integer >= 3
        AND detector_evidence->'required_entries' =
          '["mimetype", "content.xml", "META-INF/manifest.xml"]'::jsonb
        AND detector_evidence->>'central_directory_sha256'
          ~ '^[0-9a-f]{64}$'
        AND detector_evidence->>'entry_manifest_sha256'
          ~ '^[0-9a-f]{64}$'
        AND detector_evidence->>'contains_compressed_payload'
          IN ('true', 'false')
        AND detector_evidence
          ->>'compressed_payload_integrity_verified' = 'false'
        AND detector_evidence->>'promotion_eligible' = 'false'
        AND detector_evidence->>'archive_integrity_gate' =
          'blocked_pending_external_archive_integrity_verifier'
      )
      OR (
        detected_media_type =
          'application/vnd.oasis.opendocument.spreadsheet'
        AND detector_evidence->>'basis' = 'odf-zip-container'
        AND detector_evidence ?& ARRAY[
          'odf_mimetype', 'container_contract', 'first_entry',
          'mimetype_compression_method', 'entry_count',
          'required_entries', 'central_directory_sha256',
          'entry_manifest_sha256', 'contains_compressed_payload',
          'compressed_payload_integrity_verified',
          'promotion_eligible', 'archive_integrity_gate'
        ]
        AND detector_evidence->>'magic_hex' = '504b0304'
        AND detector_evidence->>'odf_mimetype' =
          'application/vnd.oasis.opendocument.spreadsheet'
        AND detector_evidence->>'container_contract' =
          'odf-zip-container/v1'
        AND detector_evidence->>'first_entry' = 'mimetype'
        AND detector_evidence->>'mimetype_compression_method' = '0'
        AND (detector_evidence->>'entry_count')::integer >= 3
        AND detector_evidence->'required_entries' =
          '["mimetype", "content.xml", "META-INF/manifest.xml"]'::jsonb
        AND detector_evidence->>'central_directory_sha256'
          ~ '^[0-9a-f]{64}$'
        AND detector_evidence->>'entry_manifest_sha256'
          ~ '^[0-9a-f]{64}$'
        AND detector_evidence->>'contains_compressed_payload'
          IN ('true', 'false')
        AND detector_evidence
          ->>'compressed_payload_integrity_verified' = 'false'
        AND detector_evidence->>'promotion_eligible' = 'false'
        AND detector_evidence->>'archive_integrity_gate' =
          'blocked_pending_external_archive_integrity_verifier'
      )
      OR (
        detected_media_type NOT IN (
          'application/pdf',
          'application/vnd.oasis.opendocument.text',
          'application/vnd.oasis.opendocument.spreadsheet'
        )
        AND detector_evidence->>'basis' NOT IN (
          'pdf-magic',
          'odf-zip-container'
        )
        AND detector_evidence->>'magic_hex'
          NOT LIKE '255044462d%'
        AND detector_evidence->>'magic_hex'
          NOT LIKE '504b0304%'
      )
    )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.artifact_format_detection_review (
    review_receipt_id text PRIMARY KEY,
    detection_receipt_id text NOT NULL UNIQUE,
    artifact_id text NOT NULL UNIQUE,
    raw_release_id text NOT NULL,
    raw_manifest_sha256 nhi_rule_history.sha256_hex NOT NULL,
    artifact_sha256 nhi_rule_history.sha256_hex NOT NULL,
    artifact_byte_length bigint NOT NULL CHECK (
      artifact_byte_length >= 0
    ),
    detection_receipt_sha256
      nhi_rule_history.sha256_hex NOT NULL UNIQUE,
    independent_verifier_version text NOT NULL CHECK (
      independent_verifier_version =
        'nhi-independent-byte-media-verifier/v1'
    ),
    independent_verifier_executable_sha256
      nhi_rule_history.sha256_hex NOT NULL,
    independently_detected_media_type text NOT NULL CHECK (
      independently_detected_media_type <> ''
    ),
    independent_evidence jsonb NOT NULL CHECK (
      jsonb_typeof(independent_evidence) = 'object'
      AND independent_evidence <> '{}'::jsonb
      AND independent_evidence ? 'basis'
      AND independent_evidence ? 'magic_hex'
    ),
    independent_evidence_sha256
      nhi_rule_history.sha256_hex NOT NULL,
    review_receipt_sha256
      nhi_rule_history.sha256_hex NOT NULL UNIQUE,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    reviewed_by name NOT NULL,
    authority_role name NOT NULL CHECK (
      authority_role = 'nhi_rule_history_format_detector_reviewer'
    ),
    reviewed_at timestamptz NOT NULL,
    FOREIGN KEY (
      artifact_id,
      artifact_sha256,
      artifact_byte_length
    )
      REFERENCES nhi_rule_history.source_artifact
        (artifact_id, sha256, byte_length)
      ON DELETE RESTRICT,
    FOREIGN KEY (
      detection_receipt_id,
      artifact_id,
      raw_release_id,
      raw_manifest_sha256,
      artifact_sha256,
      artifact_byte_length,
      detection_receipt_sha256
    )
      REFERENCES nhi_rule_history.artifact_format_detection (
        detection_receipt_id,
        artifact_id,
        raw_release_id,
        raw_manifest_sha256,
        artifact_sha256,
        artifact_byte_length,
        detection_receipt_sha256
      )
      ON DELETE RESTRICT,
    CHECK (
      independent_evidence_sha256 =
        encode(
          sha256(convert_to(independent_evidence::text, 'UTF8')),
          'hex'
        )
    ),
    CHECK (
      review_receipt_sha256 =
        encode(
          sha256(
            convert_to(
              jsonb_build_array(
                detection_receipt_id,
                artifact_id,
                raw_release_id,
                raw_manifest_sha256,
                artifact_sha256,
                artifact_byte_length,
                detection_receipt_sha256,
                independent_verifier_version,
                independent_verifier_executable_sha256,
                independently_detected_media_type,
                independent_evidence_sha256,
                reviewed_by,
                authority_role
              )::text,
              'UTF8'
            )
          ),
          'hex'
        )
    ),
    CHECK (
      (
        independently_detected_media_type = 'application/pdf'
        AND independent_evidence->>'basis' = 'pdf-magic'
        AND independent_evidence->>'magic_hex' = '255044462d'
        AND independent_evidence->>'pdf_integrity_verified' = 'false'
        AND independent_evidence->>'promotion_eligible' = 'false'
        AND independent_evidence->>'pdf_integrity_gate' =
          'blocked_pending_external_pdf_integrity_verifier'
      )
      OR (
        independently_detected_media_type =
          'application/vnd.oasis.opendocument.text'
        AND independent_evidence->>'basis' = 'odf-zip-container'
        AND independent_evidence ?& ARRAY[
          'odf_mimetype', 'container_contract', 'first_entry',
          'mimetype_compression_method', 'entry_count',
          'required_entries', 'central_directory_sha256',
          'entry_manifest_sha256', 'contains_compressed_payload',
          'compressed_payload_integrity_verified',
          'promotion_eligible', 'archive_integrity_gate'
        ]
        AND independent_evidence->>'magic_hex' = '504b0304'
        AND independent_evidence->>'odf_mimetype' =
          'application/vnd.oasis.opendocument.text'
        AND independent_evidence->>'container_contract' =
          'odf-zip-container/v1'
        AND independent_evidence->>'first_entry' = 'mimetype'
        AND independent_evidence->>'mimetype_compression_method' = '0'
        AND (independent_evidence->>'entry_count')::integer >= 3
        AND independent_evidence->'required_entries' =
          '["mimetype", "content.xml", "META-INF/manifest.xml"]'::jsonb
        AND independent_evidence->>'central_directory_sha256'
          ~ '^[0-9a-f]{64}$'
        AND independent_evidence->>'entry_manifest_sha256'
          ~ '^[0-9a-f]{64}$'
        AND independent_evidence->>'contains_compressed_payload'
          IN ('true', 'false')
        AND independent_evidence
          ->>'compressed_payload_integrity_verified' = 'false'
        AND independent_evidence->>'promotion_eligible' = 'false'
        AND independent_evidence->>'archive_integrity_gate' =
          'blocked_pending_external_archive_integrity_verifier'
      )
      OR (
        independently_detected_media_type =
          'application/vnd.oasis.opendocument.spreadsheet'
        AND independent_evidence->>'basis' = 'odf-zip-container'
        AND independent_evidence ?& ARRAY[
          'odf_mimetype', 'container_contract', 'first_entry',
          'mimetype_compression_method', 'entry_count',
          'required_entries', 'central_directory_sha256',
          'entry_manifest_sha256', 'contains_compressed_payload',
          'compressed_payload_integrity_verified',
          'promotion_eligible', 'archive_integrity_gate'
        ]
        AND independent_evidence->>'magic_hex' = '504b0304'
        AND independent_evidence->>'odf_mimetype' =
          'application/vnd.oasis.opendocument.spreadsheet'
        AND independent_evidence->>'container_contract' =
          'odf-zip-container/v1'
        AND independent_evidence->>'first_entry' = 'mimetype'
        AND independent_evidence->>'mimetype_compression_method' = '0'
        AND (independent_evidence->>'entry_count')::integer >= 3
        AND independent_evidence->'required_entries' =
          '["mimetype", "content.xml", "META-INF/manifest.xml"]'::jsonb
        AND independent_evidence->>'central_directory_sha256'
          ~ '^[0-9a-f]{64}$'
        AND independent_evidence->>'entry_manifest_sha256'
          ~ '^[0-9a-f]{64}$'
        AND independent_evidence->>'contains_compressed_payload'
          IN ('true', 'false')
        AND independent_evidence
          ->>'compressed_payload_integrity_verified' = 'false'
        AND independent_evidence->>'promotion_eligible' = 'false'
        AND independent_evidence->>'archive_integrity_gate' =
          'blocked_pending_external_archive_integrity_verifier'
      )
      OR (
        independently_detected_media_type NOT IN (
          'application/pdf',
          'application/vnd.oasis.opendocument.text',
          'application/vnd.oasis.opendocument.spreadsheet'
        )
        AND independent_evidence->>'basis' NOT IN (
          'pdf-magic',
          'odf-zip-container'
        )
        AND independent_evidence->>'magic_hex'
          NOT LIKE '255044462d%'
        AND independent_evidence->>'magic_hex'
          NOT LIKE '504b0304%'
      )
    )
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history.release_artifact (
  release_id text NOT NULL
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  artifact_id text NOT NULL
    REFERENCES nhi_rule_history.source_artifact (artifact_id)
    ON DELETE RESTRICT,
  artifact_role text NOT NULL CHECK (
    artifact_role IN (
      'primary_text',
      'official_pdf',
      'official_odt',
      'official_ods',
      'announcement',
      'comparison_table',
      'supporting'
    )
  ),
  source_order integer NOT NULL CHECK (source_order >= 0),
  PRIMARY KEY (release_id, artifact_id),
  UNIQUE (release_id, source_order)
);

DO $format_detection_release_link_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint constraint_row
    JOIN pg_class relation_row
      ON relation_row.oid = constraint_row.conrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'artifact_format_detection'
      AND constraint_row.conname =
        'artifact_format_detection_release_artifact_fkey'
  ) THEN
    ALTER TABLE nhi_rule_history.artifact_format_detection
      ADD CONSTRAINT
        artifact_format_detection_release_artifact_fkey
      FOREIGN KEY (raw_release_id, artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT;
  END IF;
END;
$format_detection_release_link_guard$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.inspect_odf_container_detector(
    artifact_bytes bytea
  )
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $function$
#variable_conflict error
DECLARE
  byte_count integer := length($1);
  eocd_offset integer := -1;
  scan_offset integer;
  central_offset bigint;
  central_size bigint;
  central_position integer;
  central_end integer;
  entry_count integer;
  disk_entry_count integer;
  entry_index integer;
  flags integer;
  compression_method integer;
  crc32 bigint;
  compressed_size bigint;
  uncompressed_size bigint;
  filename_length integer;
  extra_length integer;
  comment_length integer;
  local_offset bigint;
  local_flags integer;
  local_method integer;
  local_crc32 bigint;
  local_compressed_size bigint;
  local_uncompressed_size bigint;
  local_filename_length integer;
  local_extra_length integer;
  local_data_offset integer;
  local_end bigint;
  prior_local_end bigint := 0;
  filename text;
  local_filename text;
  seen_names text[] := ARRAY[]::text[];
  entry_manifest jsonb := '[]'::jsonb;
  odf_mimetype text;
  has_content_xml boolean := false;
  has_manifest_xml boolean := false;
  has_compressed_payload boolean := false;
BEGIN
  IF byte_count < 22
     OR byte_count > 134217728
     OR substring($1 FROM 1 FOR 4) <> decode('504b0304', 'hex') THEN
    RETURN NULL;
  END IF;

  FOR scan_offset IN REVERSE
    (byte_count - 22)..greatest(byte_count - 65557, 0)
  LOOP
    IF substring($1 FROM scan_offset + 1 FOR 4) =
         decode('504b0506', 'hex')
       AND scan_offset + 22 <= byte_count
       AND scan_offset + 22 +
         (
           get_byte($1, scan_offset + 20) +
           get_byte($1, scan_offset + 21) * 256
         ) = byte_count THEN
      eocd_offset := scan_offset;
      EXIT;
    END IF;
  END LOOP;

  IF eocd_offset < 0
     OR get_byte($1, eocd_offset + 4) +
          get_byte($1, eocd_offset + 5) * 256 <> 0
     OR get_byte($1, eocd_offset + 6) +
          get_byte($1, eocd_offset + 7) * 256 <> 0 THEN
    RETURN NULL;
  END IF;

  disk_entry_count :=
    get_byte($1, eocd_offset + 8) +
    get_byte($1, eocd_offset + 9) * 256;
  entry_count :=
    get_byte($1, eocd_offset + 10) +
    get_byte($1, eocd_offset + 11) * 256;
  central_size :=
    get_byte($1, eocd_offset + 12)::bigint +
    get_byte($1, eocd_offset + 13)::bigint * 256 +
    get_byte($1, eocd_offset + 14)::bigint * 65536 +
    get_byte($1, eocd_offset + 15)::bigint * 16777216;
  central_offset :=
    get_byte($1, eocd_offset + 16)::bigint +
    get_byte($1, eocd_offset + 17)::bigint * 256 +
    get_byte($1, eocd_offset + 18)::bigint * 65536 +
    get_byte($1, eocd_offset + 19)::bigint * 16777216;

  IF entry_count < 3
     OR entry_count > 10000
     OR disk_entry_count <> entry_count
     OR central_size <= 0
     OR central_offset < 0
     OR central_offset + central_size <> eocd_offset
     OR central_offset > 134217728
     OR central_size > 134217728 THEN
    RETURN NULL;
  END IF;

  central_position := central_offset::integer;
  central_end := eocd_offset;
  FOR entry_index IN 0..entry_count - 1 LOOP
    IF central_position + 46 > central_end
       OR substring($1 FROM central_position + 1 FOR 4) <>
         decode('504b0102', 'hex') THEN
      RETURN NULL;
    END IF;

    flags :=
      get_byte($1, central_position + 8) +
      get_byte($1, central_position + 9) * 256;
    compression_method :=
      get_byte($1, central_position + 10) +
      get_byte($1, central_position + 11) * 256;
    crc32 :=
      get_byte($1, central_position + 16)::bigint +
      get_byte($1, central_position + 17)::bigint * 256 +
      get_byte($1, central_position + 18)::bigint * 65536 +
      get_byte($1, central_position + 19)::bigint * 16777216;
    compressed_size :=
      get_byte($1, central_position + 20)::bigint +
      get_byte($1, central_position + 21)::bigint * 256 +
      get_byte($1, central_position + 22)::bigint * 65536 +
      get_byte($1, central_position + 23)::bigint * 16777216;
    uncompressed_size :=
      get_byte($1, central_position + 24)::bigint +
      get_byte($1, central_position + 25)::bigint * 256 +
      get_byte($1, central_position + 26)::bigint * 65536 +
      get_byte($1, central_position + 27)::bigint * 16777216;
    filename_length :=
      get_byte($1, central_position + 28) +
      get_byte($1, central_position + 29) * 256;
    extra_length :=
      get_byte($1, central_position + 30) +
      get_byte($1, central_position + 31) * 256;
    comment_length :=
      get_byte($1, central_position + 32) +
      get_byte($1, central_position + 33) * 256;
    local_offset :=
      get_byte($1, central_position + 42)::bigint +
      get_byte($1, central_position + 43)::bigint * 256 +
      get_byte($1, central_position + 44)::bigint * 65536 +
      get_byte($1, central_position + 45)::bigint * 16777216;

    IF filename_length < 1
       OR filename_length > 4096
       OR central_position + 46 + filename_length +
         extra_length + comment_length > central_end
       OR (flags & 9) <> 0
       OR compression_method NOT IN (0, 8)
       OR compressed_size > byte_count
       OR uncompressed_size > 536870912
       OR local_offset > central_offset
       OR get_byte($1, central_position + 34) +
         get_byte($1, central_position + 35) * 256 <> 0 THEN
      RETURN NULL;
    END IF;

    filename := convert_from(
      substring(
        $1 FROM central_position + 47 FOR filename_length
      ),
      'UTF8'
    );
    IF filename = ''
       OR filename LIKE '/%'
       OR filename LIKE '%//%'
       OR strpos(filename, chr(92)) > 0
       OR filename ~ '(^|/)[.]{1,2}(/|$)'
       OR filename ~ '^[A-Za-z]:'
       OR filename = ANY(seen_names) THEN
      RETURN NULL;
    END IF;
    seen_names := array_append(seen_names, filename);

    IF local_offset <> prior_local_end
       OR local_offset + 30 > central_offset
       OR substring($1 FROM local_offset::integer + 1 FOR 4) <>
         decode('504b0304', 'hex') THEN
      RETURN NULL;
    END IF;

    local_flags :=
      get_byte($1, local_offset::integer + 6) +
      get_byte($1, local_offset::integer + 7) * 256;
    local_method :=
      get_byte($1, local_offset::integer + 8) +
      get_byte($1, local_offset::integer + 9) * 256;
    local_crc32 :=
      get_byte($1, local_offset::integer + 14)::bigint +
      get_byte($1, local_offset::integer + 15)::bigint * 256 +
      get_byte($1, local_offset::integer + 16)::bigint * 65536 +
      get_byte($1, local_offset::integer + 17)::bigint * 16777216;
    local_compressed_size :=
      get_byte($1, local_offset::integer + 18)::bigint +
      get_byte($1, local_offset::integer + 19)::bigint * 256 +
      get_byte($1, local_offset::integer + 20)::bigint * 65536 +
      get_byte($1, local_offset::integer + 21)::bigint * 16777216;
    local_uncompressed_size :=
      get_byte($1, local_offset::integer + 22)::bigint +
      get_byte($1, local_offset::integer + 23)::bigint * 256 +
      get_byte($1, local_offset::integer + 24)::bigint * 65536 +
      get_byte($1, local_offset::integer + 25)::bigint * 16777216;
    local_filename_length :=
      get_byte($1, local_offset::integer + 26) +
      get_byte($1, local_offset::integer + 27) * 256;
    local_extra_length :=
      get_byte($1, local_offset::integer + 28) +
      get_byte($1, local_offset::integer + 29) * 256;
    local_data_offset :=
      local_offset::integer + 30 +
      local_filename_length + local_extra_length;
    local_end := local_data_offset::bigint + local_compressed_size;

    IF local_filename_length <> filename_length
       OR local_data_offset > central_offset
       OR local_end > central_offset
       OR local_flags <> flags
       OR local_method <> compression_method
       OR local_crc32 <> crc32
       OR local_compressed_size <> compressed_size
       OR local_uncompressed_size <> uncompressed_size THEN
      RETURN NULL;
    END IF;
    local_filename := convert_from(
      substring(
        $1 FROM local_offset::integer + 31
        FOR local_filename_length
      ),
      'UTF8'
    );
    IF local_filename <> filename THEN
      RETURN NULL;
    END IF;

    IF entry_index = 0 THEN
      IF filename <> 'mimetype'
         OR compression_method <> 0
         OR extra_length <> 0
         OR comment_length <> 0
         OR local_extra_length <> 0
         OR compressed_size <> uncompressed_size THEN
        RETURN NULL;
      END IF;
      odf_mimetype := convert_from(
        substring(
          $1 FROM local_data_offset + 1
          FOR compressed_size::integer
        ),
        'UTF8'
      );
      IF odf_mimetype NOT IN (
        'application/vnd.oasis.opendocument.text',
        'application/vnd.oasis.opendocument.spreadsheet'
      ) THEN
        RETURN NULL;
      END IF;
    ELSIF filename = 'mimetype' THEN
      RETURN NULL;
    END IF;

    IF filename = 'content.xml' THEN
      has_content_xml := uncompressed_size > 0;
    ELSIF filename = 'META-INF/manifest.xml' THEN
      has_manifest_xml := uncompressed_size > 0;
    END IF;
    IF compression_method <> 0 THEN
      has_compressed_payload := true;
    END IF;

    entry_manifest := entry_manifest || jsonb_build_array(
      jsonb_build_array(
        entry_index,
        filename,
        flags,
        compression_method,
        crc32,
        compressed_size,
        uncompressed_size,
        local_offset
      )
    );
    prior_local_end := local_end;
    central_position := central_position + 46 +
      filename_length + extra_length + comment_length;
  END LOOP;

  IF central_position <> central_end
     OR prior_local_end <> central_offset
     OR NOT has_content_xml
     OR NOT has_manifest_xml
     OR odf_mimetype IS NULL THEN
    RETURN NULL;
  END IF;

  RETURN jsonb_build_object(
    'basis', 'odf-zip-container',
    'magic_hex', '504b0304',
    'odf_mimetype', odf_mimetype,
    'container_contract', 'odf-zip-container/v1',
    'entry_count', entry_count,
    'first_entry', 'mimetype',
    'mimetype_compression_method', 0,
    'contains_compressed_payload', has_compressed_payload,
    'compressed_payload_integrity_verified', false,
    'promotion_eligible', false,
    'archive_integrity_gate',
      'blocked_pending_external_archive_integrity_verifier',
    'required_entries',
      jsonb_build_array(
        'mimetype',
        'content.xml',
        'META-INF/manifest.xml'
      ),
    'central_directory_sha256',
      encode(
        sha256(
          substring(
            $1 FROM central_offset::integer + 1
            FOR central_size::integer
          )
        ),
        'hex'
      ),
    'entry_manifest_sha256',
      encode(
        sha256(convert_to(entry_manifest::text, 'UTF8')),
        'hex'
      )
  );
EXCEPTION
  WHEN OTHERS THEN
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.inspect_odf_container_reviewer(
    artifact_bytes bytea
  )
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $function$
#variable_conflict error
DECLARE
  byte_count integer := length($1);
  eocd_offset integer := -1;
  scan_offset integer;
  central_offset bigint;
  central_size bigint;
  entry_count integer;
  disk_entry_count integer;
  local_position integer := 0;
  central_position integer;
  entry_index integer := 0;
  flags integer;
  compression_method integer;
  crc32 bigint;
  compressed_size bigint;
  uncompressed_size bigint;
  filename_length integer;
  extra_length integer;
  comment_length integer;
  local_offset bigint;
  filename text;
  seen_local_names text[] := ARRAY[]::text[];
  local_entries jsonb := '[]'::jsonb;
  central_entries jsonb := '[]'::jsonb;
  expected_entry jsonb;
  odf_mimetype text;
  has_content_xml boolean := false;
  has_manifest_xml boolean := false;
  has_compressed_payload boolean := false;
BEGIN
  IF byte_count < 22
     OR byte_count > 134217728
     OR get_byte($1, 0) <> 80
     OR get_byte($1, 1) <> 75
     OR get_byte($1, 2) <> 3
     OR get_byte($1, 3) <> 4 THEN
    RETURN NULL;
  END IF;

  scan_offset := byte_count - 22;
  WHILE scan_offset >= greatest(byte_count - 65557, 0) LOOP
    IF get_byte($1, scan_offset) = 80
       AND get_byte($1, scan_offset + 1) = 75
       AND get_byte($1, scan_offset + 2) = 5
       AND get_byte($1, scan_offset + 3) = 6
       AND scan_offset + 22 <= byte_count
       AND scan_offset + 22 +
         (
           get_byte($1, scan_offset + 20) +
           get_byte($1, scan_offset + 21) * 256
         ) = byte_count THEN
      eocd_offset := scan_offset;
      EXIT;
    END IF;
    scan_offset := scan_offset - 1;
  END LOOP;

  IF eocd_offset < 0
     OR get_byte($1, eocd_offset + 4) <> 0
     OR get_byte($1, eocd_offset + 5) <> 0
     OR get_byte($1, eocd_offset + 6) <> 0
     OR get_byte($1, eocd_offset + 7) <> 0 THEN
    RETURN NULL;
  END IF;

  disk_entry_count :=
    get_byte($1, eocd_offset + 8) +
    get_byte($1, eocd_offset + 9) * 256;
  entry_count :=
    get_byte($1, eocd_offset + 10) +
    get_byte($1, eocd_offset + 11) * 256;
  central_size :=
    get_byte($1, eocd_offset + 12)::bigint +
    get_byte($1, eocd_offset + 13)::bigint * 256 +
    get_byte($1, eocd_offset + 14)::bigint * 65536 +
    get_byte($1, eocd_offset + 15)::bigint * 16777216;
  central_offset :=
    get_byte($1, eocd_offset + 16)::bigint +
    get_byte($1, eocd_offset + 17)::bigint * 256 +
    get_byte($1, eocd_offset + 18)::bigint * 65536 +
    get_byte($1, eocd_offset + 19)::bigint * 16777216;

  IF entry_count < 3
     OR entry_count > 10000
     OR disk_entry_count <> entry_count
     OR central_offset <= 0
     OR central_size <= 0
     OR central_offset + central_size <> eocd_offset
     OR central_offset > 134217728
     OR central_size > 134217728 THEN
    RETURN NULL;
  END IF;

  WHILE local_position < central_offset LOOP
    IF entry_index >= entry_count
       OR local_position + 30 > central_offset
       OR get_byte($1, local_position) <> 80
       OR get_byte($1, local_position + 1) <> 75
       OR get_byte($1, local_position + 2) <> 3
       OR get_byte($1, local_position + 3) <> 4 THEN
      RETURN NULL;
    END IF;

    flags :=
      get_byte($1, local_position + 6) +
      get_byte($1, local_position + 7) * 256;
    compression_method :=
      get_byte($1, local_position + 8) +
      get_byte($1, local_position + 9) * 256;
    crc32 :=
      get_byte($1, local_position + 14)::bigint +
      get_byte($1, local_position + 15)::bigint * 256 +
      get_byte($1, local_position + 16)::bigint * 65536 +
      get_byte($1, local_position + 17)::bigint * 16777216;
    compressed_size :=
      get_byte($1, local_position + 18)::bigint +
      get_byte($1, local_position + 19)::bigint * 256 +
      get_byte($1, local_position + 20)::bigint * 65536 +
      get_byte($1, local_position + 21)::bigint * 16777216;
    uncompressed_size :=
      get_byte($1, local_position + 22)::bigint +
      get_byte($1, local_position + 23)::bigint * 256 +
      get_byte($1, local_position + 24)::bigint * 65536 +
      get_byte($1, local_position + 25)::bigint * 16777216;
    filename_length :=
      get_byte($1, local_position + 26) +
      get_byte($1, local_position + 27) * 256;
    extra_length :=
      get_byte($1, local_position + 28) +
      get_byte($1, local_position + 29) * 256;

    IF filename_length < 1
       OR filename_length > 4096
       OR (flags & 9) <> 0
       OR compression_method NOT IN (0, 8)
       OR compressed_size > byte_count
       OR uncompressed_size > 536870912
       OR local_position + 30 + filename_length +
         extra_length + compressed_size > central_offset THEN
      RETURN NULL;
    END IF;

    filename := convert_from(
      substring(
        $1 FROM local_position + 31 FOR filename_length
      ),
      'UTF8'
    );
    IF filename = ''
       OR filename LIKE '/%'
       OR filename LIKE '%//%'
       OR strpos(filename, chr(92)) > 0
       OR filename ~ '(^|/)[.]{1,2}(/|$)'
       OR filename ~ '^[A-Za-z]:'
       OR filename = ANY(seen_local_names) THEN
      RETURN NULL;
    END IF;
    seen_local_names := array_append(seen_local_names, filename);

    IF entry_index = 0 THEN
      IF filename <> 'mimetype'
         OR compression_method <> 0
         OR extra_length <> 0
         OR compressed_size <> uncompressed_size THEN
        RETURN NULL;
      END IF;
      odf_mimetype := convert_from(
        substring(
          $1 FROM local_position + 31 +
            filename_length + extra_length
          FOR compressed_size::integer
        ),
        'UTF8'
      );
      IF odf_mimetype NOT IN (
        'application/vnd.oasis.opendocument.text',
        'application/vnd.oasis.opendocument.spreadsheet'
      ) THEN
        RETURN NULL;
      END IF;
    ELSIF filename = 'mimetype' THEN
      RETURN NULL;
    END IF;

    IF filename = 'content.xml' THEN
      has_content_xml := uncompressed_size > 0;
    ELSIF filename = 'META-INF/manifest.xml' THEN
      has_manifest_xml := uncompressed_size > 0;
    END IF;
    IF compression_method <> 0 THEN
      has_compressed_payload := true;
    END IF;

    local_entries := local_entries || jsonb_build_array(
      jsonb_build_array(
        entry_index,
        filename,
        flags,
        compression_method,
        crc32,
        compressed_size,
        uncompressed_size,
        local_position
      )
    );
    local_position := local_position + 30 +
      filename_length + extra_length + compressed_size::integer;
    entry_index := entry_index + 1;
  END LOOP;

  IF local_position <> central_offset
     OR entry_index <> entry_count
     OR NOT has_content_xml
     OR NOT has_manifest_xml
     OR odf_mimetype IS NULL THEN
    RETURN NULL;
  END IF;

  central_position := central_offset::integer;
  FOR entry_index IN 0..entry_count - 1 LOOP
    IF central_position + 46 > eocd_offset
       OR get_byte($1, central_position) <> 80
       OR get_byte($1, central_position + 1) <> 75
       OR get_byte($1, central_position + 2) <> 1
       OR get_byte($1, central_position + 3) <> 2 THEN
      RETURN NULL;
    END IF;

    flags :=
      get_byte($1, central_position + 8) +
      get_byte($1, central_position + 9) * 256;
    compression_method :=
      get_byte($1, central_position + 10) +
      get_byte($1, central_position + 11) * 256;
    crc32 :=
      get_byte($1, central_position + 16)::bigint +
      get_byte($1, central_position + 17)::bigint * 256 +
      get_byte($1, central_position + 18)::bigint * 65536 +
      get_byte($1, central_position + 19)::bigint * 16777216;
    compressed_size :=
      get_byte($1, central_position + 20)::bigint +
      get_byte($1, central_position + 21)::bigint * 256 +
      get_byte($1, central_position + 22)::bigint * 65536 +
      get_byte($1, central_position + 23)::bigint * 16777216;
    uncompressed_size :=
      get_byte($1, central_position + 24)::bigint +
      get_byte($1, central_position + 25)::bigint * 256 +
      get_byte($1, central_position + 26)::bigint * 65536 +
      get_byte($1, central_position + 27)::bigint * 16777216;
    filename_length :=
      get_byte($1, central_position + 28) +
      get_byte($1, central_position + 29) * 256;
    extra_length :=
      get_byte($1, central_position + 30) +
      get_byte($1, central_position + 31) * 256;
    comment_length :=
      get_byte($1, central_position + 32) +
      get_byte($1, central_position + 33) * 256;
    local_offset :=
      get_byte($1, central_position + 42)::bigint +
      get_byte($1, central_position + 43)::bigint * 256 +
      get_byte($1, central_position + 44)::bigint * 65536 +
      get_byte($1, central_position + 45)::bigint * 16777216;

    IF filename_length < 1
       OR filename_length > 4096
       OR central_position + 46 + filename_length +
         extra_length + comment_length > eocd_offset
       OR (flags & 9) <> 0
       OR compression_method NOT IN (0, 8)
       OR get_byte($1, central_position + 34) +
         get_byte($1, central_position + 35) * 256 <> 0 THEN
      RETURN NULL;
    END IF;
    filename := convert_from(
      substring(
        $1 FROM central_position + 47 FOR filename_length
      ),
      'UTF8'
    );
    expected_entry := local_entries -> entry_index;
    IF filename <> expected_entry->>1
       OR flags <> (expected_entry->>2)::integer
       OR compression_method <> (expected_entry->>3)::integer
       OR crc32 <> (expected_entry->>4)::bigint
       OR compressed_size <> (expected_entry->>5)::bigint
       OR uncompressed_size <> (expected_entry->>6)::bigint
       OR local_offset <> (expected_entry->>7)::bigint
       OR (
         entry_index = 0
         AND (extra_length <> 0 OR comment_length <> 0)
       ) THEN
      RETURN NULL;
    END IF;

    central_entries := central_entries || jsonb_build_array(
      jsonb_build_array(
        entry_index,
        filename,
        flags,
        compression_method,
        crc32,
        compressed_size,
        uncompressed_size,
        local_offset
      )
    );
    central_position := central_position + 46 +
      filename_length + extra_length + comment_length;
  END LOOP;

  IF central_position <> eocd_offset
     OR central_entries <> local_entries THEN
    RETURN NULL;
  END IF;

  RETURN jsonb_build_object(
    'basis', 'odf-zip-container',
    'magic_hex', '504b0304',
    'odf_mimetype', odf_mimetype,
    'container_contract', 'odf-zip-container/v1',
    'entry_count', entry_count,
    'first_entry', 'mimetype',
    'mimetype_compression_method', 0,
    'contains_compressed_payload', has_compressed_payload,
    'compressed_payload_integrity_verified', false,
    'promotion_eligible', false,
    'archive_integrity_gate',
      'blocked_pending_external_archive_integrity_verifier',
    'required_entries',
      jsonb_build_array(
        'mimetype',
        'content.xml',
        'META-INF/manifest.xml'
      ),
    'central_directory_sha256',
      encode(
        sha256(
          substring(
            $1 FROM central_offset::integer + 1
            FOR central_size::integer
          )
        ),
        'hex'
      ),
    'entry_manifest_sha256',
      encode(
        sha256(convert_to(central_entries::text, 'UTF8')),
        'hex'
      )
  );
EXCEPTION
  WHEN OTHERS THEN
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.register_artifact_format_detection(
    artifact_id text,
    raw_release_id text,
    raw_manifest_sha256 text,
    artifact_bytes bytea
  )
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
#variable_conflict error
DECLARE
  artifact_row nhi_rule_history.source_artifact%ROWTYPE;
  detector_executable_sha256 text;
  detected_media_type text;
  detector_evidence jsonb;
  detector_evidence_sha256 text;
  detection_receipt_sha256 text;
  detection_receipt_id text;
  magic_hex text;
BEGIN
  IF NOT pg_has_role(
       SESSION_USER,
       'nhi_rule_history_format_detector_writer',
       'MEMBER'
     )
     OR pg_has_role(
       SESSION_USER,
       'nhi_rule_history_format_detector_reviewer',
       'MEMBER'
     ) THEN
    RAISE EXCEPTION
      'format detection requires the exclusive detector-writer capability'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT source_row.* INTO STRICT artifact_row
  FROM nhi_rule_history.source_artifact source_row
  JOIN nhi_rule_history.release_artifact release_link
    ON release_link.artifact_id = source_row.artifact_id
  JOIN nhi_rule_history.dataset_release release_row
    ON release_row.release_id = release_link.release_id
  WHERE source_row.artifact_id = $1
    AND release_link.release_id = $2
    AND release_row.manifest_sha256 = $3;

  IF artifact_row.sha256 <>
       encode(sha256($4), 'hex')
     OR artifact_row.byte_length <> length($4) THEN
    RAISE EXCEPTION
      'detector input bytes do not match the canonical artifact SHA and length'
      USING ERRCODE = 'data_exception';
  END IF;

  magic_hex := encode(
    substring($4 FROM 1 FOR least(length($4), 8)),
    'hex'
  );

  IF length($4) >= 5
     AND get_byte($4, 0) = 37
     AND get_byte($4, 1) = 80
     AND get_byte($4, 2) = 68
     AND get_byte($4, 3) = 70
     AND get_byte($4, 4) = 45 THEN
    detected_media_type := 'application/pdf';
    detector_evidence := jsonb_build_object(
      'basis', 'pdf-magic',
      'magic_hex', '255044462d',
      'pdf_integrity_verified', false,
      'promotion_eligible', false,
      'pdf_integrity_gate',
        'blocked_pending_external_pdf_integrity_verifier'
    );
  ELSIF substring($4 FROM 1 FOR 4) =
          decode('504b0304', 'hex') THEN
    detector_evidence :=
      nhi_rule_history.inspect_odf_container_detector($4);
    IF detector_evidence IS NULL THEN
      RAISE EXCEPTION
        'ZIP bytes do not satisfy the sealed ODF container contract'
        USING ERRCODE = 'data_exception';
    END IF;
    detected_media_type := detector_evidence->>'odf_mimetype';
  ELSE
    detected_media_type := 'application/octet-stream';
    detector_evidence := jsonb_build_object(
      'basis', 'opaque',
      'magic_hex', magic_hex
    );
  END IF;

  detector_executable_sha256 := encode(
    sha256(
      convert_to(
        jsonb_build_array(
          pg_get_functiondef(
            'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)'::regprocedure
          ),
          pg_get_functiondef(
            'nhi_rule_history.inspect_odf_container_detector(bytea)'::regprocedure
          )
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  detector_evidence_sha256 := encode(
    sha256(convert_to(detector_evidence::text, 'UTF8')),
    'hex'
  );
  detection_receipt_id := 'detection:' || $1;
  detection_receipt_sha256 := encode(
    sha256(
      convert_to(
        jsonb_build_array(
          artifact_row.sha256,
          artifact_row.byte_length,
          $2,
          $3,
          'nhi-byte-media-detector',
          'nhi-byte-media-detector/v1',
          detector_executable_sha256,
          detected_media_type,
          detector_evidence_sha256,
          SESSION_USER,
          'nhi_rule_history_format_detector_writer'
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  INSERT INTO nhi_rule_history.artifact_format_detection (
    detection_receipt_id, artifact_id, raw_release_id,
    raw_manifest_sha256, artifact_sha256, artifact_byte_length,
    detector_name, detector_version, detector_executable_sha256,
    detected_media_type, detector_evidence,
    detector_evidence_sha256, detection_receipt_sha256,
    verification_status, recorded_by, authority_role, detected_at
  ) VALUES (
    detection_receipt_id, $1, $2, $3, artifact_row.sha256,
    artifact_row.byte_length, 'nhi-byte-media-detector',
    'nhi-byte-media-detector/v1', detector_executable_sha256,
    detected_media_type, detector_evidence,
    detector_evidence_sha256, detection_receipt_sha256,
    'verified', SESSION_USER,
    'nhi_rule_history_format_detector_writer',
    clock_timestamp()
  );

  RETURN detection_receipt_id;
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.attest_artifact_format_detection(
    artifact_id text,
    raw_release_id text,
    raw_manifest_sha256 text,
    artifact_bytes bytea
  )
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
#variable_conflict error
DECLARE
  artifact_row nhi_rule_history.source_artifact%ROWTYPE;
  detection_row
    nhi_rule_history.artifact_format_detection%ROWTYPE;
  independent_verifier_executable_sha256 text;
  independently_detected_media_type text;
  independent_evidence jsonb;
  independent_evidence_sha256 text;
  review_receipt_sha256 text;
  review_receipt_id text;
  magic_hex text;
BEGIN
  IF NOT pg_has_role(
       SESSION_USER,
       'nhi_rule_history_format_detector_reviewer',
       'MEMBER'
     )
     OR pg_has_role(
       SESSION_USER,
       'nhi_rule_history_format_detector_writer',
       'MEMBER'
     ) THEN
    RAISE EXCEPTION
      'format attestation requires the exclusive detector-reviewer capability'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT source_row.* INTO STRICT artifact_row
  FROM nhi_rule_history.source_artifact source_row
  JOIN nhi_rule_history.release_artifact release_link
    ON release_link.artifact_id = source_row.artifact_id
  JOIN nhi_rule_history.dataset_release release_row
    ON release_row.release_id = release_link.release_id
  WHERE source_row.artifact_id = $1
    AND release_link.release_id = $2
    AND release_row.manifest_sha256 = $3;

  SELECT * INTO STRICT detection_row
  FROM nhi_rule_history.artifact_format_detection detection
  WHERE detection.artifact_id = $1
    AND detection.raw_release_id = $2
    AND detection.raw_manifest_sha256 = $3;

  IF detection_row.recorded_by = SESSION_USER
     OR artifact_row.sha256 <>
       encode(sha256($4), 'hex')
     OR artifact_row.byte_length <> length($4) THEN
    RAISE EXCEPTION
      'independent verifier identity or input-byte binding is invalid'
      USING ERRCODE = 'data_exception';
  END IF;

  magic_hex := encode(
    substring($4 FROM 1 FOR least(length($4), 8)),
    'hex'
  );

  IF substring($4 FROM 1 FOR 5) =
       decode('255044462d', 'hex') THEN
    independently_detected_media_type := 'application/pdf';
    independent_evidence := jsonb_build_object(
      'basis', 'pdf-magic',
      'magic_hex', '255044462d',
      'pdf_integrity_verified', false,
      'promotion_eligible', false,
      'pdf_integrity_gate',
        'blocked_pending_external_pdf_integrity_verifier'
    );
  ELSIF substring($4 FROM 1 FOR 4) =
          decode('504b0304', 'hex') THEN
    independent_evidence :=
      nhi_rule_history.inspect_odf_container_reviewer($4);
    IF independent_evidence IS NULL THEN
      RAISE EXCEPTION
        'ZIP bytes do not satisfy the independent ODF container contract'
        USING ERRCODE = 'data_exception';
    END IF;
    independently_detected_media_type :=
      independent_evidence->>'odf_mimetype';
  ELSE
    independently_detected_media_type :=
      'application/octet-stream';
    independent_evidence := jsonb_build_object(
      'basis', 'opaque',
      'magic_hex', magic_hex
    );
  END IF;

  IF independently_detected_media_type <>
       detection_row.detected_media_type
     OR independent_evidence <> detection_row.detector_evidence THEN
    RAISE EXCEPTION
      'independent byte verifier disagrees with detector receipt'
      USING ERRCODE = 'data_exception';
  END IF;

  independent_verifier_executable_sha256 := encode(
    sha256(
      convert_to(
        jsonb_build_array(
          pg_get_functiondef(
            'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)'::regprocedure
          ),
          pg_get_functiondef(
            'nhi_rule_history.inspect_odf_container_reviewer(bytea)'::regprocedure
          )
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  independent_evidence_sha256 := encode(
    sha256(convert_to(independent_evidence::text, 'UTF8')),
    'hex'
  );
  review_receipt_id := 'detection-review:' || $1;
  review_receipt_sha256 := encode(
    sha256(
      convert_to(
        jsonb_build_array(
          detection_row.detection_receipt_id,
          $1,
          $2,
          $3,
          artifact_row.sha256,
          artifact_row.byte_length,
          detection_row.detection_receipt_sha256,
          'nhi-independent-byte-media-verifier/v1',
          independent_verifier_executable_sha256,
          independently_detected_media_type,
          independent_evidence_sha256,
          SESSION_USER,
          'nhi_rule_history_format_detector_reviewer'
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  INSERT INTO
    nhi_rule_history.artifact_format_detection_review (
      review_receipt_id, detection_receipt_id, artifact_id,
      raw_release_id, raw_manifest_sha256, artifact_sha256,
      artifact_byte_length, detection_receipt_sha256,
      independent_verifier_version,
      independent_verifier_executable_sha256,
      independently_detected_media_type, independent_evidence,
      independent_evidence_sha256, review_receipt_sha256,
      verification_status, reviewed_by, authority_role, reviewed_at
    ) VALUES (
      review_receipt_id, detection_row.detection_receipt_id, $1,
      $2, $3, artifact_row.sha256, artifact_row.byte_length,
      detection_row.detection_receipt_sha256,
      'nhi-independent-byte-media-verifier/v1',
      independent_verifier_executable_sha256,
      independently_detected_media_type, independent_evidence,
      independent_evidence_sha256, review_receipt_sha256,
      'verified', SESSION_USER,
      'nhi_rule_history_format_detector_reviewer',
      clock_timestamp()
    );

  RETURN review_receipt_id;
END;
$function$;

CREATE TABLE IF NOT EXISTS nhi_rule_history.rule_identity (
  rule_id text PRIMARY KEY,
  canonical_slug text NOT NULL UNIQUE,
  identity_status text NOT NULL CHECK (
    identity_status IN ('active', 'retired', 'unresolved')
  ),
  first_seen_release_id text
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  last_seen_release_id text
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.rule_designation (
  designation_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  designation_type text NOT NULL,
  designation_value text NOT NULL,
  title text,
  valid_from date,
  valid_until_exclusive date,
  evidence_artifact_id text NOT NULL
    REFERENCES nhi_rule_history.source_artifact (artifact_id)
    ON DELETE RESTRICT,
  evidence_locator jsonb NOT NULL CHECK (
    jsonb_typeof(evidence_locator) = 'object'
    AND evidence_locator <> '{}'::jsonb
  ),
  CHECK (
    valid_until_exclusive IS NULL
    OR valid_from IS NULL
    OR valid_until_exclusive > valid_from
  ),
  UNIQUE (
    rule_id,
    designation_type,
    designation_value,
    valid_from
  ),
  UNIQUE (rule_id, designation_id)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.official_event (
  event_id text PRIMARY KEY,
  detail_url text NOT NULL CHECK (detail_url ~ '^https://'),
  issuer text NOT NULL,
  reference_number text,
  subject text NOT NULL,
  event_type text NOT NULL CHECK (event_type = 'amendment'),
  document_date date NOT NULL,
  publication_date date NOT NULL,
  effective_from date NOT NULL,
  effective_date_basis text NOT NULL,
  effective_date_locator jsonb NOT NULL CHECK (
    jsonb_typeof(effective_date_locator) = 'object'
    AND effective_date_locator <> '{}'::jsonb
  ),
  status text NOT NULL CHECK (status = 'verified'),
  UNIQUE (issuer, reference_number, detail_url)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.rule_snapshot (
  snapshot_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  release_id text NOT NULL
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  event_id text
    REFERENCES nhi_rule_history.official_event (event_id)
    ON DELETE RESTRICT,
  effective_from date NOT NULL,
  effective_until_exclusive date,
  date_basis text NOT NULL,
  date_locator jsonb NOT NULL CHECK (
    jsonb_typeof(date_locator) = 'object'
    AND date_locator <> '{}'::jsonb
  ),
  raw_text text NOT NULL CHECK (raw_text <> ''),
  normalized_text text NOT NULL CHECK (normalized_text <> ''),
  structured_json jsonb NOT NULL CHECK (
    jsonb_typeof(structured_json) = 'object'
  ),
  raw_sha256 nhi_rule_history.sha256_hex NOT NULL,
  normalized_sha256 nhi_rule_history.sha256_hex NOT NULL,
  source_locator_json jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator_json) = 'object'
    AND source_locator_json <> '{}'::jsonb
  ),
  parser_version text NOT NULL,
  validation_status text NOT NULL CHECK (validation_status = 'verified'),
  publication_status text NOT NULL CHECK (
    publication_status IN ('blocked', 'canary')
  ),
  created_at timestamptz NOT NULL DEFAULT current_timestamp,
  CHECK (
    effective_until_exclusive IS NULL
    OR effective_until_exclusive > effective_from
  ),
  CHECK (
    raw_sha256 =
      encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
  ),
  CHECK (
    normalized_sha256 =
      encode(sha256(convert_to(normalized_text, 'UTF8')), 'hex')
  ),
  UNIQUE (rule_id, release_id, raw_sha256),
  UNIQUE (rule_id, snapshot_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS rule_snapshot_one_open_uidx
  ON nhi_rule_history.rule_snapshot (rule_id)
  WHERE effective_until_exclusive IS NULL;

CREATE INDEX IF NOT EXISTS rule_snapshot_rule_date_idx
  ON nhi_rule_history.rule_snapshot (rule_id, effective_from);

CREATE TABLE IF NOT EXISTS nhi_rule_history.rule_head (
  rule_id text PRIMARY KEY
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  current_snapshot_id text NOT NULL UNIQUE,
  head_generation bigint NOT NULL CHECK (head_generation >= 1),
  updated_at timestamptz NOT NULL DEFAULT current_timestamp,
  FOREIGN KEY (rule_id, current_snapshot_id)
    REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.official_event_effect (
  event_effect_id text PRIMARY KEY,
  event_id text NOT NULL
    REFERENCES nhi_rule_history.official_event (event_id)
    ON DELETE RESTRICT,
  operation text NOT NULL CHECK (operation = 'amend'),
  replacement_scope text NOT NULL CHECK (
    replacement_scope = 'full_single_clause'
  ),
  effective_from date NOT NULL,
  effective_date_raw text NOT NULL,
  effective_date_locator jsonb NOT NULL CHECK (
    jsonb_typeof(effective_date_locator) = 'object'
    AND effective_date_locator <> '{}'::jsonb
  ),
  target_designation_raw text NOT NULL,
  authoritative_order bigint NOT NULL CHECK (
    authoritative_order >= 1
  ),
  rule_id text NOT NULL
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  old_snapshot_id text NOT NULL,
  new_snapshot_id text NOT NULL,
  resolution_status text NOT NULL CHECK (resolution_status = 'verified'),
  FOREIGN KEY (rule_id, old_snapshot_id)
    REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (rule_id, new_snapshot_id)
    REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
    ON DELETE RESTRICT,
  CHECK (old_snapshot_id <> new_snapshot_id),
  UNIQUE (event_id, rule_id),
  UNIQUE (rule_id, authoritative_order)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.snapshot_evidence (
  snapshot_evidence_id text PRIMARY KEY,
  snapshot_id text NOT NULL
    REFERENCES nhi_rule_history.rule_snapshot (snapshot_id)
    ON DELETE RESTRICT,
  artifact_id text NOT NULL
    REFERENCES nhi_rule_history.source_artifact (artifact_id)
    ON DELETE RESTRICT,
  evidence_kind text NOT NULL CHECK (
    evidence_kind IN (
      'comparison_new_full_text',
      'effective_date',
      'designation',
      'official_event',
      'post_anchor'
    )
  ),
  source_locator_json jsonb NOT NULL CHECK (
    jsonb_typeof(source_locator_json) = 'object'
    AND source_locator_json <> '{}'::jsonb
  ),
  source_text_sha256 nhi_rule_history.sha256_hex NOT NULL,
  evidence_status text NOT NULL CHECK (evidence_status = 'verified'),
  UNIQUE (
    snapshot_id,
    artifact_id,
    evidence_kind,
    source_text_sha256
  )
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.comparison_edge (
  comparison_id text PRIMARY KEY,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  older_snapshot_id text NOT NULL,
  newer_snapshot_id text NOT NULL UNIQUE,
  is_direct_predecessor boolean NOT NULL CHECK (is_direct_predecessor),
  algorithm_version text NOT NULL,
  input_sha256 nhi_rule_history.sha256_hex NOT NULL,
  output_sha256 nhi_rule_history.sha256_hex NOT NULL,
  mapping_coverage numeric NOT NULL CHECK (
    mapping_coverage = 1
  ),
  format_only boolean NOT NULL,
  crosses_known_gap boolean NOT NULL CHECK (NOT crosses_known_gap),
  status text NOT NULL CHECK (status = 'verified'),
  FOREIGN KEY (rule_id, older_snapshot_id)
    REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (rule_id, newer_snapshot_id)
    REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
    ON DELETE RESTRICT,
  CHECK (older_snapshot_id <> newer_snapshot_id)
);

CREATE TABLE IF NOT EXISTS nhi_rule_history.promotion_receipt (
  receipt_id text PRIMARY KEY,
  case_id uuid NOT NULL UNIQUE,
  case_fingerprint nhi_rule_history.sha256_hex NOT NULL,
  rule_id text NOT NULL
    REFERENCES nhi_rule_history.rule_identity (rule_id)
    ON DELETE RESTRICT,
  event_id text NOT NULL
    REFERENCES nhi_rule_history.official_event (event_id)
    ON DELETE RESTRICT,
  old_snapshot_id text NOT NULL
    REFERENCES nhi_rule_history.rule_snapshot (snapshot_id)
    ON DELETE RESTRICT,
  new_snapshot_id text NOT NULL UNIQUE
    REFERENCES nhi_rule_history.rule_snapshot (snapshot_id)
    ON DELETE RESTRICT,
  prior_head_generation bigint NOT NULL CHECK (prior_head_generation >= 1),
  new_head_generation bigint NOT NULL,
  effective_from date NOT NULL,
  promoted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  executed_by name NOT NULL,
  CHECK (new_head_generation = prior_head_generation + 1),
  CHECK (old_snapshot_id <> new_snapshot_id)
);

CREATE OR REPLACE FUNCTION
  nhi_rule_history.guard_snapshot_interval()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history.rule_snapshot existing
    WHERE existing.rule_id = NEW.rule_id
      AND existing.snapshot_id <> NEW.snapshot_id
      AND pg_catalog.daterange(
        existing.effective_from,
        existing.effective_until_exclusive,
        '[)'
      ) && pg_catalog.daterange(
        NEW.effective_from,
        NEW.effective_until_exclusive,
        '[)'
      )
  ) THEN
    RAISE EXCEPTION
      'rule snapshot effective intervals overlap for rule %',
      NEW.rule_id
      USING ERRCODE = 'exclusion_violation';
  END IF;
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.reject_receipt_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
BEGIN
  RAISE EXCEPTION
    'canonical promotion receipts are append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history.reject_format_detection_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
BEGIN
  RAISE EXCEPTION
    'byte-derived artifact format detections are immutable'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$function$;

DO $trigger_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'artifact_format_detection'
      AND trigger_row.tgname =
        'artifact_format_detection_update_delete_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER artifact_format_detection_update_delete_guard
    BEFORE UPDATE OR DELETE
    ON nhi_rule_history.artifact_format_detection
    FOR EACH ROW
    EXECUTE FUNCTION
      nhi_rule_history.reject_format_detection_mutation();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname =
        'artifact_format_detection_review'
      AND trigger_row.tgname =
        'artifact_format_detection_review_update_delete_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER
      artifact_format_detection_review_update_delete_guard
    BEFORE UPDATE OR DELETE
    ON nhi_rule_history.artifact_format_detection_review
    FOR EACH ROW
    EXECUTE FUNCTION
      nhi_rule_history.reject_format_detection_mutation();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname =
        'artifact_format_detection_review'
      AND trigger_row.tgname =
        'artifact_format_detection_review_truncate_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER artifact_format_detection_review_truncate_guard
    BEFORE TRUNCATE
    ON nhi_rule_history.artifact_format_detection_review
    FOR EACH STATEMENT
    EXECUTE FUNCTION
      nhi_rule_history.reject_format_detection_mutation();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'artifact_format_detection'
      AND trigger_row.tgname =
        'artifact_format_detection_truncate_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER artifact_format_detection_truncate_guard
    BEFORE TRUNCATE
    ON nhi_rule_history.artifact_format_detection
    FOR EACH STATEMENT
    EXECUTE FUNCTION
      nhi_rule_history.reject_format_detection_mutation();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'rule_snapshot'
      AND trigger_row.tgname = 'rule_snapshot_interval_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER rule_snapshot_interval_guard
    BEFORE INSERT OR UPDATE ON nhi_rule_history.rule_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION nhi_rule_history.guard_snapshot_interval();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'promotion_receipt'
      AND trigger_row.tgname = 'promotion_receipt_update_delete_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER promotion_receipt_update_delete_guard
    BEFORE UPDATE OR DELETE ON nhi_rule_history.promotion_receipt
    FOR EACH ROW
    EXECUTE FUNCTION nhi_rule_history.reject_receipt_mutation();
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relname = 'promotion_receipt'
      AND trigger_row.tgname = 'promotion_receipt_truncate_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER promotion_receipt_truncate_guard
    BEFORE TRUNCATE ON nhi_rule_history.promotion_receipt
    FOR EACH STATEMENT
    EXECUTE FUNCTION nhi_rule_history.reject_receipt_mutation();
  END IF;
END;
$trigger_guard$;

ALTER SCHEMA nhi_rule_history OWNER TO nhi_rule_history_owner;
ALTER DOMAIN nhi_rule_history.sha256_hex OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.dataset_release
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.source_artifact
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.artifact_format_detection
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.artifact_format_detection_review
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.release_artifact
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.rule_identity
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.rule_designation
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.rule_snapshot
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.rule_head
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.official_event
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.official_event_effect
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.snapshot_evidence
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.comparison_edge
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history.promotion_receipt
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION nhi_rule_history.guard_snapshot_interval()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION nhi_rule_history.reject_receipt_mutation()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION nhi_rule_history.reject_format_detection_mutation()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history.register_artifact_format_detection(
    text, text, text, bytea
  )
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history.attest_artifact_format_detection(
    text, text, text, bytea
  )
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history.inspect_odf_container_detector(bytea)
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history.inspect_odf_container_reviewer(bytea)
  OWNER TO nhi_rule_history_owner;

REVOKE ALL ON SCHEMA nhi_rule_history FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history FROM PUBLIC;
REVOKE ALL ON TYPE nhi_rule_history.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA nhi_rule_history FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history TO
  nhi_rule_history_reader,
  nhi_rule_history_format_detector_writer,
  nhi_rule_history_format_detector_reviewer,
  nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;

GRANT EXECUTE ON FUNCTION
  nhi_rule_history.register_artifact_format_detection(
    text, text, text, bytea
  )
  TO nhi_rule_history_format_detector_writer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history.attest_artifact_format_detection(
    text, text, text, bytea
  )
  TO nhi_rule_history_format_detector_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA nhi_rule_history TO
  nhi_rule_history_reader,
  nhi_rule_history_promotion_writer,
  nhi_rule_history_promotion_reviewer,
  nhi_rule_history_promotion_executor;

DO $stage_roles_zero_canonical_dml$
DECLARE
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'nhi_rule_history_update_runtime',
    'nhi_rule_history_candidate_runtime'
  ]
  LOOP
    IF EXISTS (
      SELECT 1 FROM pg_roles WHERE rolname = role_name
    ) THEN
      EXECUTE format(
        'REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history FROM %I',
        role_name
      );
      EXECUTE format(
        'REVOKE ALL ON SCHEMA nhi_rule_history FROM %I',
        role_name
      );
    END IF;
  END LOOP;
END;
$stage_roles_zero_canonical_dml$;

DO $seal_contract$
DECLARE
  managed_prefix text :=
    'Canonical NHI drug reimbursement-rule history. managed=nhi_rule_history_canonical/v1';
  actual_fingerprint text;
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class relation_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND relation_row.relkind IN ('r', 'p', 'm', 'S', 'i')
      AND relation_row.relpersistence <> 'p'
  ) THEN
    RAISE EXCEPTION
      'canonical v1 requires persistent relations'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  WITH contract_line AS (
    SELECT
      'N|' || namespace_row.nspname || '|' ||
      pg_get_userbyid(namespace_row.nspowner) || '|' ||
      coalesce(namespace_row.nspacl::text, '') AS line
    FROM pg_namespace namespace_row
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'M|' || granted_role.rolname || '|forbidden=' ||
      count(membership_row.member) FILTER (
        WHERE
          granted_role.rolname = 'nhi_rule_history_owner'
          OR NOT member_role.rolcanlogin
          OR membership_row.admin_option
          OR position(
            granted_role.rolname IN coalesce(
              shobj_description(member_role.oid, 'pg_authid'),
              ''
            )
          ) = 0
      )::text
    FROM pg_roles granted_role
    LEFT JOIN pg_auth_members membership_row
      ON membership_row.roleid = granted_role.oid
    LEFT JOIN pg_roles member_role
      ON member_role.oid = membership_row.member
    WHERE granted_role.rolname IN (
      'nhi_rule_history_owner',
      'nhi_rule_history_reader',
      'nhi_rule_history_format_detector_writer',
      'nhi_rule_history_format_detector_reviewer',
      'nhi_rule_history_promotion_writer',
      'nhi_rule_history_promotion_reviewer',
      'nhi_rule_history_promotion_executor'
    )
    GROUP BY granted_role.rolname
    UNION ALL
    SELECT
      'O|' || member_role.rolname || '|outgoing=' ||
      count(membership_row.roleid)::text
    FROM pg_roles member_role
    LEFT JOIN pg_auth_members membership_row
      ON membership_row.member = member_role.oid
    WHERE member_role.rolname IN (
      'nhi_rule_history_owner',
      'nhi_rule_history_reader',
      'nhi_rule_history_format_detector_writer',
      'nhi_rule_history_format_detector_reviewer',
      'nhi_rule_history_promotion_writer',
      'nhi_rule_history_promotion_reviewer',
      'nhi_rule_history_promotion_executor'
    )
    GROUP BY member_role.rolname
    UNION ALL
    SELECT
      'R|' || relation_row.relname || '|' ||
      relation_row.relkind::text || '|' ||
      pg_get_userbyid(relation_row.relowner) || '|' ||
      coalesce(relation_row.relacl::text, '') || '|' ||
      relation_row.relrowsecurity::text || '|' ||
      relation_row.relforcerowsecurity::text || '|' ||
      relation_row.relreplident::text || '|' ||
      relation_row.relpersistence::text || '|' ||
      coalesce(access_method.amname, '') || '|' ||
      coalesce(tablespace_row.spcname, '') || '|' ||
      coalesce(relation_row.reloptions::text, '')
    FROM pg_class relation_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    LEFT JOIN pg_am access_method
      ON access_method.oid = relation_row.relam
    LEFT JOIN pg_tablespace tablespace_row
      ON tablespace_row.oid = relation_row.reltablespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'A|' || relation_row.relname || '|' ||
      attribute_row.attnum::text || '|' ||
      attribute_row.attname || '|' ||
      format_type(
        attribute_row.atttypid,
        attribute_row.atttypmod
      ) || '|' ||
      attribute_row.attnotnull::text || '|' ||
      attribute_row.attidentity::text || '|' ||
      attribute_row.attgenerated::text || '|' ||
      coalesce(collation_namespace.nspname, '') || '|' ||
      coalesce(collation_row.collname, '') || '|' ||
      coalesce(
        pg_get_expr(default_row.adbin, default_row.adrelid),
        ''
      )
    FROM pg_attribute attribute_row
    JOIN pg_class relation_row
      ON relation_row.oid = attribute_row.attrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    LEFT JOIN pg_attrdef default_row
      ON default_row.adrelid = attribute_row.attrelid
      AND default_row.adnum = attribute_row.attnum
    LEFT JOIN pg_collation collation_row
      ON collation_row.oid = attribute_row.attcollation
    LEFT JOIN pg_namespace collation_namespace
      ON collation_namespace.oid = collation_row.collnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND attribute_row.attnum > 0
      AND NOT attribute_row.attisdropped
    UNION ALL
    SELECT
      'C|' || coalesce(relation_row.relname, '') || '|' ||
      constraint_row.conname || '|' ||
      constraint_row.contype::text || '|' ||
      constraint_row.convalidated::text || '|' ||
      pg_get_constraintdef(constraint_row.oid, true)
    FROM pg_constraint constraint_row
    LEFT JOIN pg_class relation_row
      ON relation_row.oid = constraint_row.conrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = constraint_row.connamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'I|' || index_relation.relname || '|' ||
      pg_get_indexdef(index_relation.oid)
    FROM pg_index index_row
    JOIN pg_class index_relation
      ON index_relation.oid = index_row.indexrelid
    JOIN pg_class table_relation
      ON table_relation.oid = index_row.indrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = table_relation.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'P|' || procedure_row.proname || '|' ||
      pg_get_function_identity_arguments(procedure_row.oid) || '|' ||
      procedure_row.prosecdef::text || '|' ||
      pg_get_userbyid(procedure_row.proowner) || '|' ||
      coalesce(procedure_row.proconfig::text, '') || '|' ||
      coalesce(procedure_row.proacl::text, '') || '|' ||
      pg_get_functiondef(procedure_row.oid)
    FROM pg_proc procedure_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = procedure_row.pronamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'G|' || relation_row.relname || '|' ||
      trigger_row.tgname || '|' ||
      trigger_row.tgenabled::text || '|' ||
      pg_get_triggerdef(trigger_row.oid, true)
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row
      ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND NOT trigger_row.tgisinternal
    UNION ALL
    SELECT
      'Q|' || relation_row.relname || '|' ||
      policy_row.polname || '|' ||
      policy_row.polpermissive::text || '|' ||
      policy_row.polroles::text || '|' ||
      policy_row.polcmd::text || '|' ||
      coalesce(
        pg_get_expr(policy_row.polqual, policy_row.polrelid),
        ''
      ) || '|' ||
      coalesce(
        pg_get_expr(policy_row.polwithcheck, policy_row.polrelid),
        ''
      )
    FROM pg_policy policy_row
    JOIN pg_class relation_row
      ON relation_row.oid = policy_row.polrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
    UNION ALL
    SELECT
      'Y|' || type_row.typname || '|' ||
      type_row.typtype::text || '|' ||
      pg_get_userbyid(type_row.typowner) || '|' ||
      coalesce(type_row.typacl::text, '')
    FROM pg_type type_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = type_row.typnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history'
      AND type_row.typtype IN ('d', 'e')
  )
  SELECT encode(
    sha256(
      convert_to(
        jsonb_agg(line ORDER BY line)::text,
        'UTF8'
      )
    ),
    'hex'
  )
    INTO actual_fingerprint
  FROM contract_line;

  EXECUTE format(
    'COMMENT ON SCHEMA nhi_rule_history IS %L',
    managed_prefix || ' contract_sha256=' || actual_fingerprint
  );
END;
$seal_contract$;

COMMENT ON TABLE nhi_rule_history.rule_snapshot IS
  'Complete canonical clause text over a half-open effective interval.';
COMMENT ON TABLE nhi_rule_history.rule_head IS
  'Single compare-and-swap head and generation for each stable rule identity.';
COMMENT ON TABLE nhi_rule_history.promotion_receipt IS
  'Immutable evidence that one promotion case committed atomically.';

COMMIT;
