-- 2026-07-28 — reviewed evidence and atomic canonical promotion v1
--
-- v1 accepts only operation=amend with a complete single-clause replacement.
-- Split, merge, move, restore, delete, rename, and correction are outside this
-- migration and fail closed.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history-promotion-v1-global', 0)
);

DO $dependency_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history'
      AND obj_description(oid, 'pg_namespace') ~
        '^Canonical NHI drug reimbursement-rule history[.] managed=nhi_rule_history_canonical/v1 contract_sha256=[0-9a-f]{64}$'
  ) THEN
    RAISE EXCEPTION
      'managed canonical v1 schema is required before promotion v1'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_candidate_stage'
      AND obj_description(oid, 'pg_namespace') =
        'Stage-only source-grounded proposals for the NHI rule-history continuous updater; not legal history. managed=nhi_rule_history_candidate_stage/v1'
  ) THEN
    RAISE EXCEPTION
      'managed candidate-stage v1 schema is required before promotion v1'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$dependency_guard$;

DO $schema_guard$
DECLARE
  managed_prefix text :=
    'Reviewed evidence and atomic promotion boundary for canonical NHI rule history. managed=nhi_rule_history_promotion/v1';
  existing_comment text;
  expected_fingerprint text;
  actual_fingerprint text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_promotion'
  ) THEN
    CREATE SCHEMA nhi_rule_history_promotion;
    EXECUTE format(
      'COMMENT ON SCHEMA nhi_rule_history_promotion IS %L',
      managed_prefix
    );
  ELSE
    SELECT obj_description(oid, 'pg_namespace')
      INTO existing_comment
    FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_promotion';
    IF existing_comment !~
       (
         '^' || managed_prefix ||
         ' contract_sha256=[0-9a-f]{64}$'
       ) THEN
      RAISE EXCEPTION
        'promotion schema exists without the sealed v1 contract marker'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    expected_fingerprint :=
      substring(existing_comment FROM 'contract_sha256=([0-9a-f]{64})$');

    IF EXISTS (
      SELECT 1
      FROM pg_class relation_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_promotion'
        AND relation_row.relkind IN ('r', 'p', 'm', 'S', 'i')
        AND relation_row.relpersistence <> 'p'
    ) THEN
      RAISE EXCEPTION
        'promotion v1 requires persistent relations'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    WITH contract_line AS (
      SELECT
        'N|' || namespace_row.nspname || '|' ||
        pg_get_userbyid(namespace_row.nspowner) || '|' ||
        coalesce(namespace_row.nspacl::text, '') AS line
      FROM pg_namespace namespace_row
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
          'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
          'nhi_rule_history_promotion'
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
      WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
      UNION ALL
      SELECT
        'Y|' || type_row.typname || '|' ||
        type_row.typtype::text || '|' ||
        pg_get_userbyid(type_row.typowner) || '|' ||
        coalesce(type_row.typacl::text, '')
      FROM pg_type type_row
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = type_row.typnamespace
      WHERE namespace_row.nspname =
          'nhi_rule_history_promotion'
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
        'promotion v1 structural contract drift: expected %, observed %',
        expected_fingerprint,
        actual_fingerprint
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
END;
$schema_guard$;

DO $domain_guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type type_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = type_row.typnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history_promotion'
      AND type_row.typname = 'sha256_hex'
      AND type_row.typtype = 'd'
  ) THEN
    CREATE DOMAIN nhi_rule_history_promotion.sha256_hex AS text
      CHECK (VALUE ~ '^[0-9a-f]{64}$');
  END IF;
END;
$domain_guard$;

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.promotion_case (
    case_id uuid PRIMARY KEY,
    case_fingerprint
      nhi_rule_history_promotion.sha256_hex NOT NULL UNIQUE,
    proposal_id uuid NOT NULL UNIQUE
      REFERENCES nhi_rule_history_candidate_stage.candidate_proposal
        (proposal_id)
      ON DELETE RESTRICT,
    operation text NOT NULL CHECK (operation = 'amend'),
    replacement_scope text NOT NULL CHECK (
      replacement_scope = 'full_single_clause'
    ),
    effective_from date NOT NULL,
    new_raw_text text NOT NULL CHECK (new_raw_text <> ''),
    new_normalized_text text NOT NULL CHECK (new_normalized_text <> ''),
    new_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    new_normalized_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    new_structured_json jsonb NOT NULL CHECK (
      jsonb_typeof(new_structured_json) = 'object'
    ),
    parser_version text NOT NULL,
    publication_status text NOT NULL CHECK (
      publication_status IN ('blocked', 'canary')
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    created_at timestamptz NOT NULL DEFAULT current_timestamp,
    CHECK (
      new_raw_sha256 =
        encode(sha256(convert_to(new_raw_text, 'UTF8')), 'hex')
    ),
    CHECK (
      new_normalized_sha256 =
        encode(sha256(convert_to(new_normalized_text, 'UTF8')), 'hex')
    ),
    UNIQUE (case_id, proposal_id)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.effect_resolution (
    case_id uuid PRIMARY KEY
      REFERENCES nhi_rule_history_promotion.promotion_case (case_id)
      ON DELETE RESTRICT,
    rule_id text NOT NULL
      REFERENCES nhi_rule_history.rule_identity (rule_id)
      ON DELETE RESTRICT,
    predecessor_snapshot_id text NOT NULL,
    designation_id text NOT NULL,
    target_designation_raw text NOT NULL,
    resolved_event_id text NOT NULL,
    event_detail_url text NOT NULL CHECK (event_detail_url ~ '^https://'),
    event_issuer text NOT NULL,
    event_reference_number text NOT NULL CHECK (
      event_reference_number <> ''
    ),
    event_subject text NOT NULL,
    event_type text NOT NULL CHECK (event_type = 'amendment'),
    document_date date NOT NULL,
    document_date_raw text NOT NULL CHECK (
      document_date_raw <> ''
    ),
    document_date_calendar_system text NOT NULL CHECK (
      document_date_calendar_system IN ('gregorian', 'roc')
    ),
    document_date_parser_version text NOT NULL CHECK (
      document_date_parser_version = 'nhi-date-normalize/v1'
    ),
    document_date_parse_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    publication_date date NOT NULL,
    publication_date_raw text NOT NULL CHECK (
      publication_date_raw <> ''
    ),
    publication_date_calendar_system text NOT NULL CHECK (
      publication_date_calendar_system IN ('gregorian', 'roc')
    ),
    publication_date_parser_version text NOT NULL CHECK (
      publication_date_parser_version = 'nhi-date-normalize/v1'
    ),
    publication_date_parse_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    effective_from date NOT NULL,
    effective_date_raw text NOT NULL,
    effective_date_calendar_system text NOT NULL CHECK (
      effective_date_calendar_system IN ('gregorian', 'roc')
    ),
    effective_date_parser_version text NOT NULL CHECK (
      effective_date_parser_version = 'nhi-date-normalize/v1'
    ),
    effective_date_parse_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    effective_date_basis text NOT NULL,
    effective_date_locator jsonb NOT NULL CHECK (
      jsonb_typeof(effective_date_locator) = 'object'
      AND effective_date_locator <> '{}'::jsonb
    ),
    authoritative_event_order bigint NOT NULL CHECK (
      authoritative_event_order >= 1
    ),
    authoritative_event_order_raw text NOT NULL CHECK (
      authoritative_event_order_raw <> ''
    ),
    new_release_id text NOT NULL
      REFERENCES nhi_rule_history.dataset_release (release_id)
      ON DELETE RESTRICT,
    identity_resolution_status text NOT NULL CHECK (
      identity_resolution_status = 'verified'
    ),
    event_resolution_status text NOT NULL CHECK (
      event_resolution_status = 'verified'
    ),
    full_text_resolution_status text NOT NULL CHECK (
      full_text_resolution_status = 'verified'
    ),
    operation text NOT NULL CHECK (operation = 'amend'),
    replacement_scope text NOT NULL CHECK (
      replacement_scope = 'full_single_clause'
    ),
    split_ambiguity boolean NOT NULL CHECK (NOT split_ambiguity),
    merge_ambiguity boolean NOT NULL CHECK (NOT merge_ambiguity),
    move_ambiguity boolean NOT NULL CHECK (NOT move_ambiguity),
    restore_ambiguity boolean NOT NULL CHECK (NOT restore_ambiguity),
    correction_ambiguity boolean NOT NULL CHECK (NOT correction_ambiguity),
    number_reuse_ambiguity boolean NOT NULL CHECK (
      NOT number_reuse_ambiguity
    ),
    comparison_algorithm_version text NOT NULL,
    comparison_input_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    comparison_output_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    comparison_mapping_coverage numeric NOT NULL CHECK (
      comparison_mapping_coverage = 1
    ),
    comparison_format_only boolean NOT NULL,
    resolution_evidence jsonb NOT NULL CHECK (
      jsonb_typeof(resolution_evidence) = 'object'
      AND resolution_evidence <> '{}'::jsonb
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (rule_id, predecessor_snapshot_id)
      REFERENCES nhi_rule_history.rule_snapshot (rule_id, snapshot_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (rule_id, designation_id)
      REFERENCES nhi_rule_history.rule_designation
        (rule_id, designation_id)
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.effect_resolution_span (
    case_id uuid NOT NULL
      REFERENCES nhi_rule_history_promotion.effect_resolution (case_id)
      ON DELETE RESTRICT,
    proposal_id uuid NOT NULL,
    candidate_span_id
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    span_order integer NOT NULL CHECK (span_order >= 1),
    span_role text NOT NULL CHECK (
      span_role IN (
        'comparison_old_full_text',
        'comparison_new_full_text',
        'effective_date',
        'designation',
        'official_event',
        'event_detail_url',
        'event_issuer',
        'event_reference_number',
        'event_subject',
        'document_date',
        'publication_date',
        'authoritative_order'
      )
    ),
    release_id text NOT NULL,
    artifact_id text NOT NULL,
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    char_start bigint NOT NULL CHECK (char_start >= 0),
    char_end bigint NOT NULL,
    raw_text text NOT NULL CHECK (raw_text <> ''),
    raw_text_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    covers_full_clause boolean NOT NULL,
    evidence_status text NOT NULL CHECK (evidence_status = 'verified'),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (case_id, span_order),
    UNIQUE (case_id, span_role),
    FOREIGN KEY (case_id, proposal_id)
      REFERENCES nhi_rule_history_promotion.promotion_case
        (case_id, proposal_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (proposal_id, candidate_span_id)
      REFERENCES
        nhi_rule_history_candidate_stage.candidate_source_span
        (proposal_id, span_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (release_id, artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT,
    CHECK (
      char_end > char_start
      AND char_end - char_start = char_length(raw_text)
    ),
    CHECK (
      raw_text_sha256 =
        encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        span_role IN (
          'comparison_old_full_text',
          'comparison_new_full_text'
        )
        AND covers_full_clause
      )
      OR (
        span_role NOT IN (
          'comparison_old_full_text',
          'comparison_new_full_text'
        )
        AND NOT covers_full_clause
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.anchor_snapshot (
    case_id uuid NOT NULL
      REFERENCES nhi_rule_history_promotion.promotion_case (case_id)
      ON DELETE RESTRICT,
    anchor_role text NOT NULL CHECK (anchor_role IN ('pre', 'post')),
    release_id text NOT NULL,
    artifact_id text NOT NULL,
    anchor_date date NOT NULL,
    whole_release_manifest_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    declared_rule_count integer NOT NULL CHECK (
      declared_rule_count >= 1
    ),
    rule_set_fingerprint
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (case_id, anchor_role),
    FOREIGN KEY (release_id, artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.anchor_clause (
    case_id uuid NOT NULL,
    anchor_role text NOT NULL,
    member_order integer NOT NULL CHECK (member_order >= 1),
    rule_id text NOT NULL
      REFERENCES nhi_rule_history.rule_identity (rule_id)
      ON DELETE RESTRICT,
    designation_raw text NOT NULL,
    raw_text text NOT NULL CHECK (raw_text <> ''),
    raw_text_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
      AND source_locator <> '{}'::jsonb
    ),
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (case_id, anchor_role, rule_id),
    UNIQUE (case_id, anchor_role, member_order),
    FOREIGN KEY (case_id, anchor_role)
      REFERENCES nhi_rule_history_promotion.anchor_snapshot
        (case_id, anchor_role)
      ON DELETE RESTRICT,
    CHECK (
      raw_text_sha256 =
        encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
    )
  );

CREATE TABLE IF NOT EXISTS nhi_rule_history_promotion.replay_run (
  case_id uuid PRIMARY KEY
    REFERENCES nhi_rule_history_promotion.promotion_case (case_id)
    ON DELETE RESTRICT,
  replay_algorithm_version text NOT NULL,
  pre_anchor_release_id text NOT NULL
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  post_anchor_release_id text NOT NULL
    REFERENCES nhi_rule_history.dataset_release (release_id)
    ON DELETE RESTRICT,
  accepted_event_count integer NOT NULL CHECK (accepted_event_count >= 1),
  accepted_event_stream_sha256
    nhi_rule_history_promotion.sha256_hex NOT NULL,
  replay_input_sha256
    nhi_rule_history_promotion.sha256_hex NOT NULL,
  expected_rule_set_sha256
    nhi_rule_history_promotion.sha256_hex NOT NULL,
  actual_rule_set_sha256
    nhi_rule_history_promotion.sha256_hex NOT NULL,
  verification_status text NOT NULL CHECK (
    verification_status = 'verified'
  ),
  recorded_by name NOT NULL DEFAULT session_user,
  recorded_role name NOT NULL DEFAULT current_user,
  recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
  CHECK (expected_rule_set_sha256 = actual_rule_set_sha256)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.replay_rule_result (
    case_id uuid NOT NULL
      REFERENCES nhi_rule_history_promotion.replay_run (case_id)
      ON DELETE RESTRICT,
    rule_id text NOT NULL
      REFERENCES nhi_rule_history.rule_identity (rule_id)
      ON DELETE RESTRICT,
    before_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    expected_after_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    actual_after_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (case_id, rule_id),
    CHECK (
      expected_after_raw_sha256 = actual_after_raw_sha256
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.replay_event (
    case_id uuid NOT NULL
      REFERENCES nhi_rule_history_promotion.replay_run (case_id)
      ON DELETE RESTRICT,
    event_order integer NOT NULL CHECK (event_order >= 1),
    event_source text NOT NULL CHECK (
      event_source IN ('canonical', 'candidate_resolution')
    ),
    event_id text NOT NULL,
    rule_id text NOT NULL
      REFERENCES nhi_rule_history.rule_identity (rule_id)
      ON DELETE RESTRICT,
    effective_from date NOT NULL,
    authoritative_order bigint NOT NULL CHECK (
      authoritative_order >= 1
    ),
    before_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    after_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (case_id, event_order),
    UNIQUE (case_id, event_source, event_id, rule_id),
    UNIQUE (case_id, rule_id, authoritative_order),
    CHECK (before_raw_sha256 <> after_raw_sha256)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.format_parity_receipt (
    case_id uuid PRIMARY KEY
      REFERENCES nhi_rule_history_promotion.promotion_case (case_id)
      ON DELETE RESTRICT,
    proposal_id uuid NOT NULL,
    release_id text NOT NULL,
    format_policy text NOT NULL CHECK (
      format_policy IN (
        'odt_pdf_verified',
        'source_declared_odt_only',
        'pdf_verified'
      )
    ),
    odt_artifact_id text,
    pdf_artifact_id text,
    format_declaration_artifact_id text NOT NULL,
    format_declaration_candidate_span_id
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    format_declaration_source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(format_declaration_source_locator) = 'object'
      AND format_declaration_source_locator <> '{}'::jsonb
    ),
    format_declaration_char_start bigint NOT NULL CHECK (
      format_declaration_char_start >= 0
    ),
    format_declaration_char_end bigint NOT NULL,
    format_declaration_raw_text text NOT NULL CHECK (
      format_declaration_raw_text <> ''
    ),
    format_declaration_raw_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    pdf_candidate_span_id
      nhi_rule_history_promotion.sha256_hex,
    source_declared_formats jsonb NOT NULL CHECK (
      jsonb_typeof(source_declared_formats) = 'array'
    ),
    declared_official_attachment_count integer NOT NULL CHECK (
      declared_official_attachment_count >= 1
    ),
    official_attachment_inventory_fingerprint
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    odt_clause_sha256
      nhi_rule_history_promotion.sha256_hex,
    pdf_clause_sha256 nhi_rule_history_promotion.sha256_hex,
    parity_fingerprint
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    verification_status text NOT NULL CHECK (
      verification_status = 'verified'
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (case_id, proposal_id)
      REFERENCES nhi_rule_history_promotion.promotion_case
        (case_id, proposal_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (
      proposal_id,
      format_declaration_candidate_span_id
    )
      REFERENCES
        nhi_rule_history_candidate_stage.candidate_source_span
        (proposal_id, span_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (proposal_id, pdf_candidate_span_id)
      REFERENCES
        nhi_rule_history_candidate_stage.candidate_source_span
        (proposal_id, span_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (release_id, odt_artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (release_id, format_declaration_artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (release_id, pdf_artifact_id)
      REFERENCES nhi_rule_history.release_artifact
        (release_id, artifact_id)
      ON DELETE RESTRICT,
    CHECK (
      format_declaration_char_end >
        format_declaration_char_start
      AND format_declaration_char_end -
        format_declaration_char_start =
        char_length(format_declaration_raw_text)
    ),
    CHECK (
      format_declaration_raw_sha256 =
        encode(
          sha256(
            convert_to(format_declaration_raw_text, 'UTF8')
          ),
          'hex'
        )
    ),
    CHECK (
      (
        format_policy = 'odt_pdf_verified'
        AND odt_artifact_id IS NOT NULL
        AND odt_clause_sha256 IS NOT NULL
        AND pdf_artifact_id IS NOT NULL
        AND pdf_candidate_span_id IS NOT NULL
        AND pdf_clause_sha256 IS NOT NULL
        AND odt_artifact_id <> pdf_artifact_id
        AND odt_clause_sha256 = pdf_clause_sha256
        AND source_declared_formats = '["odt", "pdf"]'::jsonb
      )
      OR (
        format_policy = 'source_declared_odt_only'
        AND odt_artifact_id IS NOT NULL
        AND odt_clause_sha256 IS NOT NULL
        AND pdf_artifact_id IS NULL
        AND pdf_candidate_span_id IS NULL
        AND pdf_clause_sha256 IS NULL
        AND source_declared_formats = '["odt"]'::jsonb
      )
      OR (
        format_policy = 'pdf_verified'
        AND odt_artifact_id IS NULL
        AND odt_clause_sha256 IS NULL
        AND pdf_artifact_id IS NOT NULL
        AND pdf_candidate_span_id IS NOT NULL
        AND pdf_clause_sha256 IS NOT NULL
        AND source_declared_formats = '["pdf"]'::jsonb
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_promotion.promotion_transition (
    case_id uuid NOT NULL
      REFERENCES nhi_rule_history_promotion.promotion_case (case_id)
      ON DELETE RESTRICT,
    transition_seq integer NOT NULL CHECK (transition_seq >= 1),
    transition_id text NOT NULL UNIQUE,
    state text NOT NULL CHECK (
      state IN ('ready', 'promoted', 'rejected')
    ),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_role name NOT NULL DEFAULT current_user,
    decision_basis_sha256
      nhi_rule_history_promotion.sha256_hex NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (case_id, transition_seq)
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_promotion.reject_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
BEGIN
  RAISE EXCEPTION
    'promotion evidence and transitions are append-only'
    USING ERRCODE = 'object_not_in_prerequisite_state';
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_promotion.guard_evidence_insert_actor()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
BEGIN
  IF CURRENT_USER <>
       'nhi_rule_history_promotion_writer'
     OR NEW.recorded_role IS DISTINCT FROM
       CURRENT_USER
     OR NEW.recorded_by IS DISTINCT FROM
       SESSION_USER THEN
    RAISE EXCEPTION
      'promotion evidence must be inserted under the writer capability with authenticated session identity'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_promotion.guard_promotion_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
DECLARE
  prior_state text;
  prior_seq integer;
  proposal_state text;
  evidence_producer name;
BEGIN
  IF NEW.recorded_by IS DISTINCT FROM SESSION_USER
     OR NEW.recorded_role IS DISTINCT FROM
       CURRENT_USER THEN
    RAISE EXCEPTION
      'promotion transition actor fields must match authenticated session and capability role'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NEW.state IN ('ready', 'rejected')
     AND CURRENT_USER <>
       'nhi_rule_history_promotion_reviewer' THEN
    RAISE EXCEPTION
      'ready/rejected decisions require the reviewer capability'
      USING ERRCODE = 'insufficient_privilege';
  ELSIF NEW.state = 'promoted'
     AND CURRENT_USER <>
       'nhi_rule_history_owner' THEN
    RAISE EXCEPTION
      'promoted transition requires the security-definer owner'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-promotion-transition:' || NEW.case_id::text,
      0
    )
  );

  SELECT transition_seq, state
    INTO prior_seq, prior_state
  FROM nhi_rule_history_promotion.promotion_transition
  WHERE case_id = NEW.case_id
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF prior_seq IS NULL THEN
    IF NEW.transition_seq <> 1
       OR NEW.state NOT IN ('ready', 'rejected') THEN
      RAISE EXCEPTION
        'initial promotion transition must be ready or rejected'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    IF NEW.transition_seq <> prior_seq + 1 THEN
      RAISE EXCEPTION
        'promotion transition sequence must be gap-free'
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF prior_state = 'ready' AND NEW.state <> 'promoted' THEN
      RAISE EXCEPTION
        'ready promotion case may transition only to promoted'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF prior_state IN ('promoted', 'rejected') THEN
      RAISE EXCEPTION
        'promoted and rejected promotion cases are terminal'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  IF NEW.state = 'ready' THEN
    SELECT case_row.recorded_by
      INTO evidence_producer
    FROM nhi_rule_history_promotion.promotion_case case_row
    WHERE case_row.case_id = NEW.case_id;

    IF evidence_producer IS NULL
       OR evidence_producer = NEW.recorded_by
       OR EXISTS (
         SELECT 1
         FROM (
           SELECT resolution_row.recorded_by
           FROM nhi_rule_history_promotion.effect_resolution
             resolution_row
           WHERE resolution_row.case_id = NEW.case_id
           UNION ALL
           SELECT span_row.recorded_by
           FROM nhi_rule_history_promotion.effect_resolution_span
             span_row
           WHERE span_row.case_id = NEW.case_id
           UNION ALL
           SELECT anchor_row.recorded_by
           FROM nhi_rule_history_promotion.anchor_snapshot anchor_row
           WHERE anchor_row.case_id = NEW.case_id
           UNION ALL
           SELECT clause_row.recorded_by
           FROM nhi_rule_history_promotion.anchor_clause clause_row
           WHERE clause_row.case_id = NEW.case_id
           UNION ALL
           SELECT replay_row.recorded_by
           FROM nhi_rule_history_promotion.replay_run replay_row
           WHERE replay_row.case_id = NEW.case_id
           UNION ALL
           SELECT result_row.recorded_by
           FROM nhi_rule_history_promotion.replay_rule_result
             result_row
           WHERE result_row.case_id = NEW.case_id
           UNION ALL
           SELECT event_row.recorded_by
           FROM nhi_rule_history_promotion.replay_event event_row
           WHERE event_row.case_id = NEW.case_id
           UNION ALL
           SELECT parity_row.recorded_by
           FROM nhi_rule_history_promotion.format_parity_receipt
             parity_row
           WHERE parity_row.case_id = NEW.case_id
         ) evidence_actor
         WHERE evidence_actor.recorded_by <>
           evidence_producer
      ) THEN
      RAISE EXCEPTION
        'promotion evidence producer and independent reviewer must differ, and one authenticated producer must own all evidence'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT candidate_state.state
      INTO proposal_state
    FROM nhi_rule_history_promotion.promotion_case case_row
    JOIN nhi_rule_history_candidate_stage.current_candidate_state
      candidate_state
      ON candidate_state.proposal_id = case_row.proposal_id
    WHERE case_row.case_id = NEW.case_id;

    IF proposal_state IS DISTINCT FROM
       'promotion_ready_pending_anchor' THEN
      RAISE EXCEPTION
        'promotion case requires a stage proposal pending anchor replay'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM nhi_rule_history_promotion.effect_resolution
      WHERE case_id = NEW.case_id
    ) OR (
      SELECT count(*)
      FROM nhi_rule_history_promotion.effect_resolution_span
      WHERE case_id = NEW.case_id
  ) <> 12 OR (
      SELECT count(*)
      FROM nhi_rule_history_promotion.anchor_snapshot
      WHERE case_id = NEW.case_id
    ) <> 2 OR (
      SELECT count(*)
      FROM nhi_rule_history_promotion.anchor_clause
      WHERE case_id = NEW.case_id
    ) < 2 OR NOT EXISTS (
      SELECT 1 FROM nhi_rule_history_promotion.replay_run
      WHERE case_id = NEW.case_id
    ) OR NOT EXISTS (
      SELECT 1 FROM nhi_rule_history_promotion.replay_rule_result
      WHERE case_id = NEW.case_id
    ) OR NOT EXISTS (
      SELECT 1 FROM nhi_rule_history_promotion.replay_event
      WHERE case_id = NEW.case_id
    ) OR NOT EXISTS (
      SELECT 1 FROM nhi_rule_history_promotion.format_parity_receipt
      WHERE case_id = NEW.case_id
    ) THEN
      RAISE EXCEPTION
        'ready transition requires complete resolution, spans, anchors, replay, and parity evidence'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSIF NEW.state = 'promoted' THEN
    IF NOT EXISTS (
         SELECT 1
         FROM nhi_rule_history.promotion_receipt
         WHERE case_id = NEW.case_id
       ) THEN
      RAISE EXCEPTION
        'promoted transition may be written only by the promotion transaction'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;

  RETURN NEW;
END;
$function$;

DO $evidence_trigger_guard$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'promotion_case',
    'effect_resolution',
    'effect_resolution_span',
    'anchor_snapshot',
    'anchor_clause',
    'replay_run',
    'replay_rule_result',
    'replay_event',
    'format_parity_receipt',
    'promotion_transition'
  ]
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history_promotion'
        AND relation_row.relname = table_name
        AND trigger_row.tgname = 'promotion_append_only_guard'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER promotion_append_only_guard BEFORE UPDATE OR DELETE ON nhi_rule_history_promotion.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_promotion.reject_evidence_mutation()',
        table_name
      );
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_trigger trigger_row
      JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
      JOIN pg_namespace namespace_row
        ON namespace_row.oid = relation_row.relnamespace
      WHERE namespace_row.nspname = 'nhi_rule_history_promotion'
        AND relation_row.relname = table_name
        AND trigger_row.tgname = 'promotion_truncate_guard'
        AND NOT trigger_row.tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER promotion_truncate_guard BEFORE TRUNCATE ON nhi_rule_history_promotion.%I FOR EACH STATEMENT EXECUTE FUNCTION nhi_rule_history_promotion.reject_evidence_mutation()',
        table_name
      );
    END IF;

    IF table_name <> 'promotion_transition'
       AND NOT EXISTS (
        SELECT 1
        FROM pg_trigger trigger_row
        JOIN pg_class relation_row
          ON relation_row.oid = trigger_row.tgrelid
        JOIN pg_namespace namespace_row
          ON namespace_row.oid = relation_row.relnamespace
        WHERE namespace_row.nspname =
            'nhi_rule_history_promotion'
          AND relation_row.relname = table_name
          AND trigger_row.tgname =
            'promotion_evidence_insert_actor_guard'
          AND NOT trigger_row.tgisinternal
      ) THEN
      EXECUTE format(
        'CREATE TRIGGER promotion_evidence_insert_actor_guard BEFORE INSERT ON nhi_rule_history_promotion.%I FOR EACH ROW EXECUTE FUNCTION nhi_rule_history_promotion.guard_evidence_insert_actor()',
        table_name
      );
    END IF;
  END LOOP;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger trigger_row
    JOIN pg_class relation_row ON relation_row.oid = trigger_row.tgrelid
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname = 'nhi_rule_history_promotion'
      AND relation_row.relname = 'promotion_transition'
      AND trigger_row.tgname = 'promotion_transition_insert_guard'
      AND NOT trigger_row.tgisinternal
  ) THEN
    CREATE TRIGGER promotion_transition_insert_guard
    BEFORE INSERT ON nhi_rule_history_promotion.promotion_transition
    FOR EACH ROW
    EXECUTE FUNCTION
      nhi_rule_history_promotion.guard_promotion_transition();
  END IF;
END;
$evidence_trigger_guard$;

CREATE OR REPLACE FUNCTION nhi_rule_history_promotion.promote_case(
  case_id uuid,
  expected_case_fingerprint text,
  expected_head_generation bigint
)
RETURNS TABLE (
  receipt_id text,
  replayed boolean,
  new_snapshot_id text,
  new_head_generation bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
#variable_conflict error
DECLARE
  case_row nhi_rule_history_promotion.promotion_case%ROWTYPE;
  resolution_row
    nhi_rule_history_promotion.effect_resolution%ROWTYPE;
  predecessor_row nhi_rule_history.rule_snapshot%ROWTYPE;
  head_snapshot_id text;
  head_generation_value bigint;
  prior_transition_seq integer;
  prior_transition_state text;
  prior_transition_actor name;
  reviewer_actor name;
  stage_state text;
  proposal_row
    nhi_rule_history_candidate_stage.candidate_proposal%ROWTYPE;
  receipt_row nhi_rule_history.promotion_receipt%ROWTYPE;
  parity_row
    nhi_rule_history_promotion.format_parity_receipt%ROWTYPE;
  replay_row nhi_rule_history_promotion.replay_run%ROWTYPE;
  old_span
    nhi_rule_history_promotion.effect_resolution_span%ROWTYPE;
  new_span
    nhi_rule_history_promotion.effect_resolution_span%ROWTYPE;
  pre_anchor nhi_rule_history_promotion.anchor_snapshot%ROWTYPE;
  post_anchor nhi_rule_history_promotion.anchor_snapshot%ROWTYPE;
  pre_clause nhi_rule_history_promotion.anchor_clause%ROWTYPE;
  post_clause nhi_rule_history_promotion.anchor_clause%ROWTYPE;
  pre_release nhi_rule_history.dataset_release%ROWTYPE;
  post_release nhi_rule_history.dataset_release%ROWTYPE;
  parity_release nhi_rule_history.dataset_release%ROWTYPE;
  pre_clause_count bigint;
  post_clause_count bigint;
  pre_rule_set_fingerprint text;
  post_rule_set_fingerprint text;
  inventory_attachment_count bigint;
  inventory_detection_count bigint;
  inventory_detection_review_count bigint;
  inventory_attachment_fingerprint text;
  replay_event_count bigint;
  replay_event_fingerprint text;
  accepted_event_count bigint;
  accepted_event_fingerprint text;
  authenticated_executor name;
  legal_today date;
  parsed_effective_date date;
  parsed_document_date date;
  parsed_publication_date date;
  new_snapshot_id_value text;
  event_effect_id_value text;
  comparison_id_value text;
  receipt_id_value text;
  affected_count bigint;
BEGIN
  authenticated_executor := SESSION_USER;
  legal_today :=
    (pg_catalog.clock_timestamp() AT TIME ZONE 'Asia/Taipei')::date;

  IF $2 IS NULL OR $2 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION
      'expected_case_fingerprint must be lowercase SHA-256'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF $3 IS NULL OR $3 < 1 THEN
    RAISE EXCEPTION
      'expected_head_generation must be positive'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO STRICT case_row
  FROM nhi_rule_history_promotion.promotion_case
  WHERE promotion_case.case_id = $1;

  IF case_row.case_fingerprint IS DISTINCT FROM $2 THEN
    RAISE EXCEPTION
      'promotion case fingerprint mismatch'
      USING ERRCODE = 'serialization_failure';
  END IF;

  SELECT * INTO STRICT resolution_row
  FROM nhi_rule_history_promotion.effect_resolution
  WHERE effect_resolution.case_id = $1;

  BEGIN
    parsed_effective_date := CASE
      WHEN resolution_row.effective_date_calendar_system =
           'gregorian'
        AND resolution_row.effective_date_raw ~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      THEN resolution_row.effective_date_raw::date
      WHEN resolution_row.effective_date_calendar_system = 'roc'
        AND resolution_row.effective_date_raw ~
          '^中華民國[0-9]{1,3}年[0-9]{1,2}月[0-9]{1,2}日$'
        AND substring(
          resolution_row.effective_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer >= 1
      THEN make_date(
        substring(
          resolution_row.effective_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer + 1911,
        substring(
          resolution_row.effective_date_raw
          FROM '年([0-9]{1,2})月'
        )::integer,
        substring(
          resolution_row.effective_date_raw
          FROM '月([0-9]{1,2})日$'
        )::integer
      )
      ELSE NULL
    END;

    parsed_document_date := CASE
      WHEN resolution_row.document_date_calendar_system =
           'gregorian'
        AND resolution_row.document_date_raw ~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      THEN resolution_row.document_date_raw::date
      WHEN resolution_row.document_date_calendar_system = 'roc'
        AND resolution_row.document_date_raw ~
          '^中華民國[0-9]{1,3}年[0-9]{1,2}月[0-9]{1,2}日$'
        AND substring(
          resolution_row.document_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer >= 1
      THEN make_date(
        substring(
          resolution_row.document_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer + 1911,
        substring(
          resolution_row.document_date_raw
          FROM '年([0-9]{1,2})月'
        )::integer,
        substring(
          resolution_row.document_date_raw
          FROM '月([0-9]{1,2})日$'
        )::integer
      )
      ELSE NULL
    END;

    parsed_publication_date := CASE
      WHEN resolution_row.publication_date_calendar_system =
           'gregorian'
        AND resolution_row.publication_date_raw ~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      THEN resolution_row.publication_date_raw::date
      WHEN resolution_row.publication_date_calendar_system = 'roc'
        AND resolution_row.publication_date_raw ~
          '^中華民國[0-9]{1,3}年[0-9]{1,2}月[0-9]{1,2}日$'
        AND substring(
          resolution_row.publication_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer >= 1
      THEN make_date(
        substring(
          resolution_row.publication_date_raw
          FROM '^中華民國([0-9]{1,3})年'
        )::integer + 1911,
        substring(
          resolution_row.publication_date_raw
          FROM '年([0-9]{1,2})月'
        )::integer,
        substring(
          resolution_row.publication_date_raw
          FROM '月([0-9]{1,2})日$'
        )::integer
      )
      ELSE NULL
    END;
  EXCEPTION
    WHEN OTHERS THEN
      RAISE EXCEPTION
        'official date source text cannot be deterministically normalized'
        USING ERRCODE = 'data_exception';
  END;

  IF parsed_effective_date IS NULL
     OR parsed_document_date IS NULL
     OR parsed_publication_date IS NULL THEN
    RAISE EXCEPTION
      'official date source text cannot be deterministically normalized'
      USING ERRCODE = 'data_exception';
  END IF;

  IF parsed_effective_date > legal_today THEN
    RAISE EXCEPTION
      'parsed effective date has not arrived'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT transition_row.recorded_by
    INTO reviewer_actor
  FROM nhi_rule_history_promotion.promotion_transition transition_row
  WHERE transition_row.case_id = $1
    AND transition_row.state = 'ready'
  ORDER BY transition_row.transition_seq DESC
  LIMIT 1;

  IF authenticated_executor = case_row.recorded_by
     OR authenticated_executor = reviewer_actor THEN
    RAISE EXCEPTION
      'promotion executor identity must differ from evidence producer and reviewer'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'nhi-rule-canonical-head:' || resolution_row.rule_id,
      0
    )
  );

  SELECT * INTO receipt_row
  FROM nhi_rule_history.promotion_receipt
  WHERE promotion_receipt.case_id = $1;

  IF FOUND THEN
    IF receipt_row.case_fingerprint IS DISTINCT FROM $2
       OR receipt_row.prior_head_generation IS DISTINCT FROM $3 THEN
      RAISE EXCEPTION
        'idempotent promotion replay parameters differ from receipt'
        USING ERRCODE = 'serialization_failure';
    END IF;

    SELECT current_snapshot_id, head_generation
      INTO head_snapshot_id, head_generation_value
    FROM nhi_rule_history.rule_head receipt_head
    WHERE receipt_head.rule_id = receipt_row.rule_id;

    IF head_generation_value < receipt_row.new_head_generation
       OR (
         head_generation_value = receipt_row.new_head_generation
         AND head_snapshot_id IS DISTINCT FROM
           receipt_row.new_snapshot_id
       )
       OR NOT EXISTS (
         SELECT 1
         FROM nhi_rule_history.rule_snapshot snapshot_row
         WHERE snapshot_row.snapshot_id =
             receipt_row.new_snapshot_id
           AND snapshot_row.rule_id = receipt_row.rule_id
           AND snapshot_row.event_id = receipt_row.event_id
           AND snapshot_row.effective_from =
             receipt_row.effective_from
       ) THEN
      RAISE EXCEPTION
        'promotion receipt no longer matches canonical history'
        USING ERRCODE = 'serialization_failure';
    END IF;

    RETURN QUERY SELECT
      receipt_row.receipt_id,
      true,
      receipt_row.new_snapshot_id,
      receipt_row.new_head_generation;
    RETURN;
  END IF;

  SELECT transition_seq, state, recorded_by
    INTO
      prior_transition_seq,
      prior_transition_state,
      prior_transition_actor
  FROM nhi_rule_history_promotion.promotion_transition
  WHERE promotion_transition.case_id = $1
  ORDER BY transition_seq DESC
  LIMIT 1;

  IF prior_transition_state IS DISTINCT FROM 'ready' THEN
    RAISE EXCEPTION
      'promotion case is not in ready state'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF prior_transition_actor IS NULL
     OR prior_transition_actor = case_row.recorded_by THEN
    RAISE EXCEPTION
      'promotion evidence producer and reviewer identities are not independent'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT * INTO STRICT parity_row
  FROM nhi_rule_history_promotion.format_parity_receipt
  WHERE format_parity_receipt.case_id = $1;

  SELECT candidate_state.state
    INTO stage_state
  FROM nhi_rule_history_candidate_stage.current_candidate_state
    candidate_state
  WHERE candidate_state.proposal_id = case_row.proposal_id;

  SELECT * INTO STRICT proposal_row
  FROM nhi_rule_history_candidate_stage.candidate_proposal proposal
  WHERE proposal.proposal_id = case_row.proposal_id;

  IF stage_state IS DISTINCT FROM 'promotion_ready_pending_anchor'
     OR proposal_row.replacement_scope IS DISTINCT FROM
       'full_single_clause'
     OR proposal_row.effective_from IS DISTINCT FROM
       case_row.effective_from
     OR proposal_row.raw_effective_expression IS DISTINCT FROM
       resolution_row.effective_date_raw
     OR proposal_row.calendar_system IS DISTINCT FROM
       resolution_row.effective_date_calendar_system
     OR proposal_row.omitted_text_present
     OR proposal_row.merged_cells_present
     OR proposal_row.cross_row_dependency
     OR proposal_row.multiple_designations_present
     OR proposal_row.identity_resolution <>
       'source_designation_only'
     OR NOT EXISTS (
       SELECT 1
       FROM
         nhi_rule_history_candidate_stage.candidate_source_span
         candidate_span
       WHERE candidate_span.proposal_id = case_row.proposal_id
         AND candidate_span.source_role = 'comparison_new'
         AND candidate_span.raw_text = case_row.new_raw_text
         AND candidate_span.raw_text_sha256 =
           case_row.new_raw_sha256
         AND candidate_span.raw_text_sha256 =
           encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(candidate_span.raw_text, 'UTF8')
             ),
             'hex'
           )
     ) THEN
    RAISE EXCEPTION
      'stage proposal is not eligible for full-single-clause promotion'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF case_row.operation <> 'amend'
     OR case_row.replacement_scope <> 'full_single_clause'
     OR resolution_row.recorded_by <> case_row.recorded_by
     OR resolution_row.operation <> 'amend'
     OR resolution_row.replacement_scope <> 'full_single_clause'
     OR resolution_row.effective_from IS DISTINCT FROM
       case_row.effective_from
     OR parsed_effective_date IS DISTINCT FROM
       resolution_row.effective_from
     OR resolution_row.identity_resolution_status <> 'verified'
     OR resolution_row.event_resolution_status <> 'verified'
     OR resolution_row.full_text_resolution_status <> 'verified'
     OR resolution_row.split_ambiguity
     OR resolution_row.merge_ambiguity
     OR resolution_row.move_ambiguity
     OR resolution_row.restore_ambiguity
     OR resolution_row.correction_ambiguity
     OR resolution_row.number_reuse_ambiguity
     OR resolution_row.comparison_mapping_coverage <> 1 THEN
    RAISE EXCEPTION
      'only an unambiguous verified full-single-clause amendment is supported'
      USING ERRCODE = 'feature_not_supported';
  END IF;

  IF parsed_publication_date > legal_today
     OR parsed_document_date > parsed_publication_date
     OR resolution_row.document_date IS DISTINCT FROM
       parsed_document_date
     OR resolution_row.publication_date IS DISTINCT FROM
       parsed_publication_date THEN
    RAISE EXCEPTION
      'official document/publication dates are not valid, arrived, and deterministically normalized'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history.rule_identity identity_row
    WHERE identity_row.rule_id = resolution_row.rule_id
      AND identity_row.identity_status = 'active'
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history.rule_designation designation_row
    WHERE designation_row.rule_id = resolution_row.rule_id
      AND designation_row.designation_id =
        resolution_row.designation_id
      AND designation_row.designation_value =
        resolution_row.target_designation_raw
      AND (
        designation_row.valid_from IS NULL
        OR designation_row.valid_from <= case_row.effective_from
      )
      AND (
        designation_row.valid_until_exclusive IS NULL
        OR designation_row.valid_until_exclusive >
          case_row.effective_from
      )
  ) THEN
    RAISE EXCEPTION
      'stable rule identity or effective designation is not verified'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  SELECT current_snapshot_id, head_generation
    INTO STRICT head_snapshot_id, head_generation_value
  FROM nhi_rule_history.rule_head current_head
  WHERE current_head.rule_id = resolution_row.rule_id
  FOR UPDATE;

  IF head_generation_value IS DISTINCT FROM $3
     OR head_snapshot_id IS DISTINCT FROM
       resolution_row.predecessor_snapshot_id THEN
    RAISE EXCEPTION
      'stale canonical rule head'
      USING ERRCODE = 'serialization_failure';
  END IF;

  SELECT * INTO STRICT predecessor_row
  FROM nhi_rule_history.rule_snapshot predecessor_snapshot
  WHERE predecessor_snapshot.snapshot_id =
      resolution_row.predecessor_snapshot_id
    AND predecessor_snapshot.rule_id = resolution_row.rule_id;

  IF predecessor_row.effective_until_exclusive IS NOT NULL
     OR predecessor_row.effective_from >= case_row.effective_from THEN
    RAISE EXCEPTION
      'predecessor is not the single open interval before effective date'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF predecessor_row.raw_sha256 IS DISTINCT FROM
       encode(
         pg_catalog.sha256(
           pg_catalog.convert_to(predecessor_row.raw_text, 'UTF8')
         ),
         'hex'
       )
     OR case_row.new_raw_sha256 IS DISTINCT FROM
       encode(
         pg_catalog.sha256(
           pg_catalog.convert_to(case_row.new_raw_text, 'UTF8')
         ),
         'hex'
       )
     OR case_row.new_normalized_sha256 IS DISTINCT FROM
       encode(
         pg_catalog.sha256(
           pg_catalog.convert_to(
             case_row.new_normalized_text,
             'UTF8'
           )
         ),
         'hex'
       )
     OR predecessor_row.raw_sha256 =
       case_row.new_raw_sha256
     OR resolution_row.comparison_input_sha256 <>
       predecessor_row.raw_sha256
     OR resolution_row.comparison_output_sha256 <>
       case_row.new_raw_sha256 THEN
    RAISE EXCEPTION
      'old/new full-text hashes do not match canonical inputs'
      USING ERRCODE = 'data_exception';
  END IF;

  SELECT * INTO STRICT old_span
  FROM nhi_rule_history_promotion.effect_resolution_span
  WHERE effect_resolution_span.case_id = $1
    AND span_role = 'comparison_old_full_text';
  SELECT * INTO STRICT new_span
  FROM nhi_rule_history_promotion.effect_resolution_span
  WHERE effect_resolution_span.case_id = $1
    AND span_role = 'comparison_new_full_text';

  IF NOT old_span.covers_full_clause
     OR NOT new_span.covers_full_clause
     OR old_span.raw_text IS DISTINCT FROM predecessor_row.raw_text
     OR old_span.raw_text_sha256 IS DISTINCT FROM
       predecessor_row.raw_sha256
     OR old_span.release_id IS DISTINCT FROM
       resolution_row.new_release_id
     OR new_span.raw_text IS DISTINCT FROM case_row.new_raw_text
     OR new_span.raw_text_sha256 IS DISTINCT FROM
       case_row.new_raw_sha256
     OR new_span.release_id IS DISTINCT FROM
       resolution_row.new_release_id THEN
    RAISE EXCEPTION
      'ordered comparison spans do not prove complete old/new clause text'
      USING ERRCODE = 'data_exception';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    LEFT JOIN
      nhi_rule_history_candidate_stage.candidate_source_span
      candidate_span
      ON candidate_span.proposal_id = span_row.proposal_id
      AND candidate_span.span_id = span_row.candidate_span_id
    LEFT JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = span_row.artifact_id
    WHERE span_row.case_id = $1
      AND (
        span_row.proposal_id <> case_row.proposal_id
        OR span_row.recorded_by <> case_row.recorded_by
        OR candidate_span.span_id IS NULL
        OR candidate_span.artifact_sha256 <>
          artifact_row.sha256
        OR candidate_span.locator <> span_row.source_locator
        OR candidate_span.char_start <> span_row.char_start
        OR candidate_span.char_end <> span_row.char_end
        OR candidate_span.raw_text <> span_row.raw_text
        OR candidate_span.raw_text_sha256 <>
          span_row.raw_text_sha256
        OR candidate_span.raw_text_sha256 <>
          encode(
            pg_catalog.sha256(
              pg_catalog.convert_to(candidate_span.raw_text, 'UTF8')
            ),
            'hex'
          )
        OR candidate_span.source_role <>
          CASE span_row.span_role
            WHEN 'comparison_old_full_text' THEN 'comparison_old'
            WHEN 'comparison_new_full_text' THEN 'comparison_new'
            WHEN 'effective_date' THEN 'effective_expression'
            WHEN 'designation' THEN 'current_anchor'
            WHEN 'official_event' THEN 'detail_announcement'
            WHEN 'event_detail_url' THEN 'detail_announcement'
            WHEN 'event_issuer' THEN 'detail_announcement'
            WHEN 'event_reference_number' THEN
              'detail_announcement'
            WHEN 'event_subject' THEN 'detail_announcement'
            WHEN 'document_date' THEN 'detail_announcement'
            WHEN 'publication_date' THEN 'detail_announcement'
            WHEN 'authoritative_order' THEN
              'detail_announcement'
          END
      )
  ) THEN
    RAISE EXCEPTION
      'promotion source span does not exactly bind its staged candidate span, artifact, and locator'
      USING ERRCODE = 'data_exception';
  END IF;

  IF (
    SELECT count(*)
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = span_row.artifact_id
    JOIN nhi_rule_history.dataset_release release_row
      ON release_row.release_id = span_row.release_id
    WHERE span_row.case_id = $1
      AND span_row.evidence_status = 'verified'
      AND artifact_row.verification_status = 'full_text_verified'
      AND release_row.verification_status = 'verified'
  ) <> 12 OR EXISTS (
    SELECT 1
    FROM (
      VALUES
        ('comparison_old_full_text'),
        ('comparison_new_full_text'),
        ('effective_date'),
        ('designation'),
        ('official_event'),
        ('event_detail_url'),
        ('event_issuer'),
        ('event_reference_number'),
        ('event_subject'),
        ('document_date'),
        ('publication_date'),
        ('authoritative_order')
    ) required_role(span_role)
    WHERE NOT EXISTS (
      SELECT 1
      FROM nhi_rule_history_promotion.effect_resolution_span span_row
      WHERE span_row.case_id = $1
        AND span_row.span_role = required_role.span_role
    )
  ) THEN
    RAISE EXCEPTION
      'twelve exact, verified, full-text source roles are required'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = span_row.artifact_id
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'effective_date'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.effective_date_raw
      AND span_row.source_locator =
        resolution_row.effective_date_locator
      AND resolution_row.effective_date_parse_sha256 =
        encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(
              jsonb_build_array(
                artifact_row.sha256,
                span_row.source_locator,
                span_row.raw_text,
                resolution_row.effective_date_calendar_system,
                resolution_row.effective_date_parser_version,
                parsed_effective_date
              )::text,
              'UTF8'
            )
          ),
          'hex'
        )
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'designation'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text =
        resolution_row.target_designation_raw
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'official_event'
      AND span_row.release_id = resolution_row.new_release_id
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'event_detail_url'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.event_detail_url
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'event_issuer'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.event_issuer
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'event_reference_number'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text =
        resolution_row.event_reference_number
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'event_subject'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.event_subject
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = span_row.artifact_id
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'document_date'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.document_date_raw
      AND resolution_row.document_date_parse_sha256 =
        encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(
              jsonb_build_array(
                artifact_row.sha256,
                span_row.source_locator,
                span_row.raw_text,
                resolution_row.document_date_calendar_system,
                resolution_row.document_date_parser_version,
                parsed_document_date
              )::text,
              'UTF8'
            )
          ),
          'hex'
        )
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = span_row.artifact_id
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'publication_date'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text = resolution_row.publication_date_raw
      AND resolution_row.publication_date_parse_sha256 =
        encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(
              jsonb_build_array(
                artifact_row.sha256,
                span_row.source_locator,
                span_row.raw_text,
                resolution_row.publication_date_calendar_system,
                resolution_row.publication_date_parser_version,
                parsed_publication_date
              )::text,
              'UTF8'
            )
          ),
          'hex'
        )
  ) OR NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_promotion.effect_resolution_span span_row
    WHERE span_row.case_id = $1
      AND span_row.span_role = 'authoritative_order'
      AND span_row.release_id = resolution_row.new_release_id
      AND span_row.raw_text =
        resolution_row.authoritative_event_order_raw
      AND span_row.raw_text =
        resolution_row.authoritative_event_order::text
  ) THEN
    RAISE EXCEPTION
      'effective-date, designation, or exact event-field spans do not match resolution'
      USING ERRCODE = 'data_exception';
  END IF;

  SELECT * INTO STRICT pre_anchor
  FROM nhi_rule_history_promotion.anchor_snapshot
  WHERE anchor_snapshot.case_id = $1
    AND anchor_snapshot.anchor_role = 'pre';
  SELECT * INTO STRICT post_anchor
  FROM nhi_rule_history_promotion.anchor_snapshot
  WHERE anchor_snapshot.case_id = $1
    AND anchor_snapshot.anchor_role = 'post';
  SELECT * INTO STRICT pre_release
  FROM nhi_rule_history.dataset_release
  WHERE dataset_release.release_id = pre_anchor.release_id;
  SELECT * INTO STRICT post_release
  FROM nhi_rule_history.dataset_release
  WHERE dataset_release.release_id = post_anchor.release_id;

  SELECT
    count(*),
    encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          jsonb_agg(
            jsonb_build_array(
              clause_row.member_order,
              clause_row.rule_id,
              clause_row.designation_raw,
              clause_row.raw_text_sha256
            )
            ORDER BY clause_row.member_order
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
    INTO pre_clause_count, pre_rule_set_fingerprint
  FROM nhi_rule_history_promotion.anchor_clause clause_row
  WHERE clause_row.case_id = $1
    AND clause_row.anchor_role = 'pre';

  SELECT
    count(*),
    encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          jsonb_agg(
            jsonb_build_array(
              clause_row.member_order,
              clause_row.rule_id,
              clause_row.designation_raw,
              clause_row.raw_text_sha256
            )
            ORDER BY clause_row.member_order
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
    INTO post_clause_count, post_rule_set_fingerprint
  FROM nhi_rule_history_promotion.anchor_clause clause_row
  WHERE clause_row.case_id = $1
    AND clause_row.anchor_role = 'post';

  SELECT * INTO STRICT pre_clause
  FROM nhi_rule_history_promotion.anchor_clause
  WHERE anchor_clause.case_id = $1
    AND anchor_clause.anchor_role = 'pre'
    AND anchor_clause.rule_id = resolution_row.rule_id;
  SELECT * INTO STRICT post_clause
  FROM nhi_rule_history_promotion.anchor_clause
  WHERE anchor_clause.case_id = $1
    AND anchor_clause.anchor_role = 'post'
    AND anchor_clause.rule_id = resolution_row.rule_id;

  IF pre_anchor.verification_status <> 'verified'
     OR post_anchor.verification_status <> 'verified'
     OR pre_anchor.recorded_by <> case_row.recorded_by
     OR post_anchor.recorded_by <> case_row.recorded_by
     OR pre_anchor.anchor_date >= case_row.effective_from
     OR post_anchor.anchor_date < case_row.effective_from
     OR NOT pre_release.is_cumulative_anchor
     OR NOT post_release.is_cumulative_anchor
     OR pre_release.verification_status <> 'verified'
     OR post_release.verification_status <> 'verified'
     OR pre_anchor.whole_release_manifest_sha256 <>
       pre_release.manifest_sha256
     OR post_anchor.whole_release_manifest_sha256 <>
       post_release.manifest_sha256
     OR pre_anchor.declared_rule_count <>
       pre_release.declared_rule_count
     OR post_anchor.declared_rule_count <>
       post_release.declared_rule_count
     OR pre_clause_count <> pre_anchor.declared_rule_count
     OR post_clause_count <> post_anchor.declared_rule_count
     OR pre_rule_set_fingerprint <>
       pre_anchor.rule_set_fingerprint
     OR post_rule_set_fingerprint <>
       post_anchor.rule_set_fingerprint
     OR pre_rule_set_fingerprint <>
       pre_release.rule_set_fingerprint
     OR post_rule_set_fingerprint <>
       post_release.rule_set_fingerprint
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.anchor_clause clause_row
       WHERE clause_row.case_id = $1
         AND clause_row.recorded_by <> case_row.recorded_by
     )
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.anchor_clause clause_row
       WHERE clause_row.case_id = $1
         AND (
           clause_row.member_order < 1
           OR (
             clause_row.anchor_role = 'pre'
             AND clause_row.member_order > pre_clause_count
           )
           OR (
             clause_row.anchor_role = 'post'
             AND clause_row.member_order > post_clause_count
           )
         )
     )
     OR EXISTS (
       SELECT 1
       FROM (
         SELECT pre_clause_row.rule_id
         FROM nhi_rule_history_promotion.anchor_clause
           pre_clause_row
         WHERE pre_clause_row.case_id = $1
           AND pre_clause_row.anchor_role = 'pre'
       ) pre_member
       FULL JOIN (
         SELECT post_clause_row.rule_id
         FROM nhi_rule_history_promotion.anchor_clause
           post_clause_row
         WHERE post_clause_row.case_id = $1
           AND post_clause_row.anchor_role = 'post'
       ) post_member
         USING (rule_id)
       WHERE pre_member.rule_id IS NULL
         OR post_member.rule_id IS NULL
     )
     OR post_clause.raw_text IS DISTINCT FROM case_row.new_raw_text
     OR post_clause.raw_text_sha256 IS DISTINCT FROM
       case_row.new_raw_sha256 THEN
    RAISE EXCEPTION
      'manifest-derived pre/post anchor membership or text is incomplete'
      USING ERRCODE = 'data_exception';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM (
      VALUES
        (pre_anchor.release_id, pre_anchor.artifact_id),
        (post_anchor.release_id, post_anchor.artifact_id)
    ) anchor_artifact(release_id, artifact_id)
    LEFT JOIN nhi_rule_history.release_artifact release_link
      ON release_link.release_id = anchor_artifact.release_id
      AND release_link.artifact_id = anchor_artifact.artifact_id
    LEFT JOIN nhi_rule_history.source_artifact artifact_row
      ON artifact_row.artifact_id = anchor_artifact.artifact_id
    LEFT JOIN nhi_rule_history.dataset_release release_row
      ON release_row.release_id = anchor_artifact.release_id
    WHERE release_link.release_id IS NULL
      OR artifact_row.verification_status <> 'full_text_verified'
      OR release_row.verification_status <> 'verified'
  ) THEN
    RAISE EXCEPTION
      'anchor artifacts are not verified full text'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM (
      VALUES
        (pre_anchor.release_id),
        (post_anchor.release_id)
    ) target_release(release_id)
    JOIN nhi_rule_history.release_artifact release_link
      ON release_link.release_id = target_release.release_id
    JOIN nhi_rule_history.artifact_format_detection detection_row
      ON detection_row.artifact_id = release_link.artifact_id
    JOIN
      nhi_rule_history.artifact_format_detection_review review_row
      ON review_row.detection_receipt_id =
        detection_row.detection_receipt_id
    WHERE review_row.independently_detected_media_type IN (
      'application/vnd.oasis.opendocument.text',
      'application/vnd.oasis.opendocument.spreadsheet'
    )
  ) THEN
    RAISE EXCEPTION
      'blocked_pending_external_archive_integrity_verifier'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM (
      VALUES
        (pre_anchor.release_id),
        (post_anchor.release_id)
    ) target_release(release_id)
    JOIN nhi_rule_history.dataset_release release_row
      ON release_row.release_id = target_release.release_id
    CROSS JOIN LATERAL (
      SELECT
        count(*) AS linked_count,
        count(detection_row.artifact_id) AS detection_count,
        count(review_row.artifact_id) AS review_count,
        bool_or(
          artifact_row.verification_status = 'quarantined'
          OR detection_row.artifact_id IS NULL
          OR detection_row.verification_status <> 'verified'
          OR detection_row.detector_name <>
            'nhi-byte-media-detector'
          OR detection_row.detector_version <>
            'nhi-byte-media-detector/v1'
          OR detection_row.detector_executable_sha256 <>
            encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(
                  jsonb_build_array(
                    pg_catalog.pg_get_functiondef(
                      'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)'::regprocedure
                    ),
                    pg_catalog.pg_get_functiondef(
                      'nhi_rule_history.inspect_odf_container_detector(bytea)'::regprocedure
                    )
                  )::text,
                  'UTF8'
                )
              ),
              'hex'
            )
          OR review_row.artifact_id IS NULL
          OR review_row.verification_status <> 'verified'
          OR review_row.independent_verifier_version <>
            'nhi-independent-byte-media-verifier/v1'
          OR
            review_row.independent_verifier_executable_sha256 <>
            encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(
                  jsonb_build_array(
                    pg_catalog.pg_get_functiondef(
                      'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)'::regprocedure
                    ),
                    pg_catalog.pg_get_functiondef(
                      'nhi_rule_history.inspect_odf_container_reviewer(bytea)'::regprocedure
                    )
                  )::text,
                  'UTF8'
                )
              ),
              'hex'
            )
          OR review_row.reviewed_by = detection_row.recorded_by
          OR review_row.detection_receipt_sha256 <>
            detection_row.detection_receipt_sha256
          OR review_row.independently_detected_media_type <>
            detection_row.detected_media_type
          OR (
            review_row.independently_detected_media_type IN (
              'application/vnd.oasis.opendocument.text',
              'application/vnd.oasis.opendocument.spreadsheet'
            )
          )
        ) AS invalid_detection,
        encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(
              jsonb_agg(
                jsonb_build_array(
                  release_link.source_order,
                  release_link.artifact_id,
                  release_link.artifact_role,
                  artifact_row.sha256,
                  artifact_row.byte_length,
                  artifact_row.media_type,
                  artifact_row.official_url,
                  artifact_row.verification_status,
                  detection_row.detection_receipt_sha256,
                  detection_row.detector_version,
                  detection_row.detector_executable_sha256,
                  detection_row.detected_media_type,
                  detection_row.verification_status,
                  detection_row.recorded_by,
                  review_row.review_receipt_sha256,
                  review_row.independent_verifier_version,
                  review_row.independent_verifier_executable_sha256,
                  review_row.independently_detected_media_type,
                  review_row.verification_status,
                  review_row.reviewed_by
                )
                ORDER BY release_link.source_order
              )::text,
              'UTF8'
            )
          ),
          'hex'
        ) AS inventory_fingerprint
      FROM nhi_rule_history.release_artifact release_link
      JOIN nhi_rule_history.source_artifact artifact_row
        ON artifact_row.artifact_id = release_link.artifact_id
      LEFT JOIN
        nhi_rule_history.artifact_format_detection detection_row
        ON detection_row.artifact_id = artifact_row.artifact_id
      LEFT JOIN
        nhi_rule_history.artifact_format_detection_review review_row
        ON review_row.detection_receipt_id =
          detection_row.detection_receipt_id
      WHERE release_link.release_id = target_release.release_id
    ) observed_inventory
    WHERE release_row.official_attachment_inventory_status <>
        'exhaustive_verified'
      OR release_row.declared_official_attachment_count <>
        observed_inventory.linked_count
      OR observed_inventory.detection_count <>
        observed_inventory.linked_count
      OR observed_inventory.review_count <>
        observed_inventory.linked_count
      OR observed_inventory.invalid_detection
      OR release_row.official_attachment_inventory_fingerprint <>
        observed_inventory.inventory_fingerprint
  ) THEN
    RAISE EXCEPTION
      'anchor release byte-derived artifact inventory is not exhaustively verified'
      USING ERRCODE = 'data_exception';
  END IF;

  SELECT * INTO STRICT replay_row
  FROM nhi_rule_history_promotion.replay_run
  WHERE replay_run.case_id = $1;

  SELECT
    count(*),
    encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          jsonb_agg(
            jsonb_build_array(
              event_row.event_order,
              event_row.event_source,
              event_row.event_id,
              event_row.rule_id,
              event_row.effective_from,
              event_row.authoritative_order,
              event_row.before_raw_sha256,
              event_row.after_raw_sha256
            )
            ORDER BY event_row.event_order
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
    INTO replay_event_count, replay_event_fingerprint
  FROM nhi_rule_history_promotion.replay_event event_row
  WHERE event_row.case_id = $1;

  WITH accepted_event_unordered AS (
    SELECT
      'canonical'::text AS event_source,
      event_row.event_id,
      effect_row.rule_id,
      effect_row.effective_from,
      effect_row.authoritative_order,
      old_snapshot.raw_sha256 AS before_raw_sha256,
      new_snapshot.raw_sha256 AS after_raw_sha256
    FROM nhi_rule_history.official_event_effect effect_row
    JOIN nhi_rule_history.official_event event_row
      ON event_row.event_id = effect_row.event_id
    JOIN nhi_rule_history.rule_snapshot old_snapshot
      ON old_snapshot.rule_id = effect_row.rule_id
      AND old_snapshot.snapshot_id = effect_row.old_snapshot_id
    JOIN nhi_rule_history.rule_snapshot new_snapshot
      ON new_snapshot.rule_id = effect_row.rule_id
      AND new_snapshot.snapshot_id = effect_row.new_snapshot_id
    WHERE effect_row.resolution_status = 'verified'
      AND event_row.status = 'verified'
      AND old_snapshot.validation_status = 'verified'
      AND new_snapshot.validation_status = 'verified'
      AND effect_row.effective_from > pre_anchor.anchor_date
      AND effect_row.effective_from <= post_anchor.anchor_date
      AND EXISTS (
        SELECT 1
        FROM nhi_rule_history_promotion.anchor_clause member_row
        WHERE member_row.case_id = $1
          AND member_row.anchor_role = 'post'
          AND member_row.rule_id = effect_row.rule_id
      )
    UNION ALL
    SELECT
      'candidate_resolution'::text,
      resolution_row.resolved_event_id,
      resolution_row.rule_id,
      resolution_row.effective_from,
      resolution_row.authoritative_event_order,
      predecessor_row.raw_sha256,
      case_row.new_raw_sha256
  ),
  accepted_event_ordered AS (
    SELECT
      row_number() OVER (
        ORDER BY
          rule_id,
          authoritative_order
      )::integer AS event_order,
      event_source,
      event_id,
      rule_id,
      effective_from,
      authoritative_order,
      before_raw_sha256,
      after_raw_sha256
    FROM accepted_event_unordered
  )
  SELECT
    count(*),
    encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          jsonb_agg(
            jsonb_build_array(
              expected_row.event_order,
              expected_row.event_source,
              expected_row.event_id,
              expected_row.rule_id,
              expected_row.effective_from,
              expected_row.authoritative_order,
              expected_row.before_raw_sha256,
              expected_row.after_raw_sha256
            )
            ORDER BY expected_row.event_order
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
    INTO accepted_event_count, accepted_event_fingerprint
  FROM accepted_event_ordered expected_row;

  IF replay_row.verification_status <> 'verified'
     OR replay_row.recorded_by <> case_row.recorded_by
     OR replay_event_count <> accepted_event_count
     OR replay_row.accepted_event_count <> accepted_event_count
     OR replay_event_fingerprint <> accepted_event_fingerprint
     OR replay_row.accepted_event_stream_sha256 <>
       accepted_event_fingerprint
     OR replay_row.replay_input_sha256 <>
       accepted_event_fingerprint
     OR EXISTS (
       WITH accepted_event_unordered AS (
         SELECT
           'canonical'::text AS event_source,
           event_row.event_id,
           effect_row.rule_id,
           effect_row.effective_from,
           effect_row.authoritative_order,
           old_snapshot.raw_sha256 AS before_raw_sha256,
           new_snapshot.raw_sha256 AS after_raw_sha256
         FROM nhi_rule_history.official_event_effect effect_row
         JOIN nhi_rule_history.official_event event_row
           ON event_row.event_id = effect_row.event_id
         JOIN nhi_rule_history.rule_snapshot old_snapshot
           ON old_snapshot.rule_id = effect_row.rule_id
           AND old_snapshot.snapshot_id = effect_row.old_snapshot_id
         JOIN nhi_rule_history.rule_snapshot new_snapshot
           ON new_snapshot.rule_id = effect_row.rule_id
           AND new_snapshot.snapshot_id = effect_row.new_snapshot_id
         WHERE effect_row.resolution_status = 'verified'
           AND event_row.status = 'verified'
           AND old_snapshot.validation_status = 'verified'
           AND new_snapshot.validation_status = 'verified'
           AND effect_row.effective_from > pre_anchor.anchor_date
           AND effect_row.effective_from <= post_anchor.anchor_date
           AND EXISTS (
             SELECT 1
             FROM nhi_rule_history_promotion.anchor_clause member_row
             WHERE member_row.case_id = $1
               AND member_row.anchor_role = 'post'
               AND member_row.rule_id = effect_row.rule_id
           )
         UNION ALL
         SELECT
           'candidate_resolution'::text,
           resolution_row.resolved_event_id,
           resolution_row.rule_id,
           resolution_row.effective_from,
           resolution_row.authoritative_event_order,
           predecessor_row.raw_sha256,
           case_row.new_raw_sha256
       ),
       accepted_event_ordered AS (
         SELECT
           row_number() OVER (
             ORDER BY
               rule_id,
               authoritative_order
           )::integer AS event_order,
           event_source,
           event_id,
           rule_id,
           effective_from,
           authoritative_order,
           before_raw_sha256,
           after_raw_sha256
         FROM accepted_event_unordered
       )
       SELECT 1
       FROM accepted_event_ordered expected_row
       FULL JOIN (
         SELECT *
         FROM nhi_rule_history_promotion.replay_event
         WHERE replay_event.case_id = $1
       ) evidence_row
         ON evidence_row.event_order = expected_row.event_order
       WHERE expected_row.event_order IS NULL
         OR evidence_row.event_order IS NULL
         OR evidence_row.event_source <> expected_row.event_source
         OR evidence_row.event_id <> expected_row.event_id
         OR evidence_row.rule_id <> expected_row.rule_id
         OR evidence_row.effective_from <>
           expected_row.effective_from
         OR evidence_row.authoritative_order <>
           expected_row.authoritative_order
         OR evidence_row.before_raw_sha256 <>
           expected_row.before_raw_sha256
         OR evidence_row.after_raw_sha256 <>
           expected_row.after_raw_sha256
         OR evidence_row.verification_status <> 'verified'
         OR evidence_row.recorded_by <> case_row.recorded_by
     )
     OR EXISTS (
       WITH accepted_event_unordered AS (
         SELECT
           effect_row.rule_id,
           effect_row.authoritative_order,
           old_snapshot.raw_sha256 AS before_raw_sha256,
           new_snapshot.raw_sha256 AS after_raw_sha256
         FROM nhi_rule_history.official_event_effect effect_row
         JOIN nhi_rule_history.official_event event_row
           ON event_row.event_id = effect_row.event_id
         JOIN nhi_rule_history.rule_snapshot old_snapshot
           ON old_snapshot.rule_id = effect_row.rule_id
           AND old_snapshot.snapshot_id = effect_row.old_snapshot_id
         JOIN nhi_rule_history.rule_snapshot new_snapshot
           ON new_snapshot.rule_id = effect_row.rule_id
           AND new_snapshot.snapshot_id = effect_row.new_snapshot_id
         WHERE effect_row.resolution_status = 'verified'
           AND event_row.status = 'verified'
           AND old_snapshot.validation_status = 'verified'
           AND new_snapshot.validation_status = 'verified'
           AND effect_row.effective_from > pre_anchor.anchor_date
           AND effect_row.effective_from <= post_anchor.anchor_date
           AND EXISTS (
             SELECT 1
             FROM nhi_rule_history_promotion.anchor_clause member_row
             WHERE member_row.case_id = $1
               AND member_row.anchor_role = 'post'
               AND member_row.rule_id = effect_row.rule_id
           )
         UNION ALL
         SELECT
           resolution_row.rule_id,
           resolution_row.authoritative_event_order,
           predecessor_row.raw_sha256,
           case_row.new_raw_sha256
       ),
       replay_chain AS (
         SELECT
           event_row.*,
           lag(event_row.after_raw_sha256) OVER (
             PARTITION BY event_row.rule_id
             ORDER BY event_row.authoritative_order
           ) AS prior_after_raw_sha256,
           row_number() OVER (
             PARTITION BY event_row.rule_id
             ORDER BY event_row.authoritative_order DESC
           ) AS reverse_order
         FROM accepted_event_unordered event_row
       )
       SELECT 1
       FROM replay_chain chain_row
       JOIN nhi_rule_history_promotion.anchor_clause pre_member
         ON pre_member.case_id = $1
         AND pre_member.anchor_role = 'pre'
         AND pre_member.rule_id = chain_row.rule_id
       JOIN nhi_rule_history_promotion.anchor_clause post_member
         ON post_member.case_id = $1
         AND post_member.anchor_role = 'post'
         AND post_member.rule_id = chain_row.rule_id
       WHERE chain_row.before_raw_sha256 <>
         coalesce(
           chain_row.prior_after_raw_sha256,
           pre_member.raw_text_sha256
         )
         OR (
           chain_row.reverse_order = 1
           AND chain_row.after_raw_sha256 <>
             post_member.raw_text_sha256
         )
     )
     OR EXISTS (
       WITH accepted_event_key AS (
         SELECT
           effect_row.rule_id,
           effect_row.authoritative_order
         FROM nhi_rule_history.official_event_effect effect_row
         JOIN nhi_rule_history.official_event event_row
           ON event_row.event_id = effect_row.event_id
         WHERE effect_row.resolution_status = 'verified'
           AND event_row.status = 'verified'
           AND effect_row.effective_from > pre_anchor.anchor_date
           AND effect_row.effective_from <= post_anchor.anchor_date
           AND EXISTS (
             SELECT 1
             FROM nhi_rule_history_promotion.anchor_clause member_row
             WHERE member_row.case_id = $1
               AND member_row.anchor_role = 'post'
               AND member_row.rule_id = effect_row.rule_id
           )
         UNION ALL
         SELECT
           resolution_row.rule_id,
           resolution_row.authoritative_event_order
       )
       SELECT 1
       FROM accepted_event_key
       GROUP BY rule_id, authoritative_order
       HAVING count(*) <> 1
     )
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.anchor_clause pre_member
       JOIN nhi_rule_history_promotion.anchor_clause post_member
         ON post_member.case_id = pre_member.case_id
         AND post_member.anchor_role = 'post'
         AND post_member.rule_id = pre_member.rule_id
       WHERE pre_member.case_id = $1
         AND pre_member.anchor_role = 'pre'
         AND pre_member.raw_text_sha256 <>
           post_member.raw_text_sha256
         AND pre_member.rule_id <> resolution_row.rule_id
         AND NOT EXISTS (
           SELECT 1
           FROM nhi_rule_history.official_event_effect effect_row
           JOIN nhi_rule_history.official_event event_row
             ON event_row.event_id = effect_row.event_id
           WHERE effect_row.rule_id = pre_member.rule_id
             AND effect_row.resolution_status = 'verified'
             AND event_row.status = 'verified'
             AND effect_row.effective_from >
               pre_anchor.anchor_date
             AND effect_row.effective_from <=
               post_anchor.anchor_date
         )
     )
     OR replay_row.expected_rule_set_sha256 <>
       replay_row.actual_rule_set_sha256
     OR replay_row.pre_anchor_release_id <>
       pre_anchor.release_id
     OR replay_row.post_anchor_release_id <>
       post_anchor.release_id
     OR replay_row.expected_rule_set_sha256 <>
       post_rule_set_fingerprint
     OR (
       SELECT count(*)
       FROM nhi_rule_history_promotion.replay_rule_result result_row
       WHERE result_row.case_id = $1
     ) <> post_clause_count
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.replay_rule_result result_row
       WHERE result_row.case_id = $1
         AND (
           result_row.verification_status <> 'verified'
           OR result_row.expected_after_raw_sha256 <>
             result_row.actual_after_raw_sha256
           OR result_row.recorded_by <> case_row.recorded_by
         )
     )
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.anchor_clause post_member
       LEFT JOIN
         nhi_rule_history_promotion.anchor_clause pre_member
         ON pre_member.case_id = post_member.case_id
         AND pre_member.anchor_role = 'pre'
         AND pre_member.rule_id = post_member.rule_id
       LEFT JOIN
         nhi_rule_history_promotion.replay_rule_result result_row
         ON result_row.case_id = post_member.case_id
         AND result_row.rule_id = post_member.rule_id
       WHERE post_member.case_id = $1
         AND post_member.anchor_role = 'post'
         AND (
           pre_member.rule_id IS NULL
           OR result_row.rule_id IS NULL
           OR result_row.before_raw_sha256 <>
             pre_member.raw_text_sha256
           OR result_row.expected_after_raw_sha256 <>
             post_member.raw_text_sha256
           OR result_row.actual_after_raw_sha256 <>
             post_member.raw_text_sha256
           OR result_row.verification_status <> 'verified'
         )
     )
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.replay_rule_result result_row
       LEFT JOIN
         nhi_rule_history_promotion.anchor_clause post_member
         ON post_member.case_id = result_row.case_id
         AND post_member.anchor_role = 'post'
         AND post_member.rule_id = result_row.rule_id
       WHERE result_row.case_id = $1
         AND post_member.rule_id IS NULL
     )
     OR NOT EXISTS (
       SELECT 1
       FROM nhi_rule_history_promotion.replay_event event_row
       WHERE event_row.case_id = $1
         AND event_row.event_source = 'candidate_resolution'
         AND event_row.event_id = resolution_row.resolved_event_id
         AND event_row.rule_id = resolution_row.rule_id
         AND event_row.effective_from = resolution_row.effective_from
         AND event_row.authoritative_order =
           resolution_row.authoritative_event_order
         AND event_row.before_raw_sha256 =
           predecessor_row.raw_sha256
         AND event_row.after_raw_sha256 = case_row.new_raw_sha256
         AND event_row.verification_status = 'verified'
         AND event_row.recorded_by = case_row.recorded_by
     )
     OR NOT EXISTS (
       -- One replay_rule_result is the whole-anchor A -> C endpoint.
       -- The candidate-specific immediate predecessor B -> C is bound
       -- independently by the candidate_resolution replay_event below.
       SELECT 1
       FROM nhi_rule_history_promotion.replay_rule_result result_row
       WHERE result_row.case_id = $1
         AND result_row.rule_id = resolution_row.rule_id
         AND result_row.before_raw_sha256 =
           pre_clause.raw_text_sha256
         AND result_row.expected_after_raw_sha256 =
           case_row.new_raw_sha256
         AND result_row.actual_after_raw_sha256 =
           case_row.new_raw_sha256
         AND result_row.verification_status = 'verified'
     ) THEN
    RAISE EXCEPTION
      'accepted event-stream replay or whole-anchor endpoint parity is not verified'
      USING ERRCODE = 'data_exception';
  END IF;

  SELECT * INTO STRICT parity_release
  FROM nhi_rule_history.dataset_release
  WHERE dataset_release.release_id = parity_row.release_id;

  SELECT
    count(*),
    count(detection_row.artifact_id),
    count(review_row.artifact_id),
    encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          jsonb_agg(
            jsonb_build_array(
              release_link.source_order,
              release_link.artifact_id,
              release_link.artifact_role,
              artifact_row.sha256,
              artifact_row.byte_length,
              artifact_row.media_type,
              artifact_row.official_url,
              artifact_row.verification_status,
              detection_row.detection_receipt_sha256,
              detection_row.detector_version,
              detection_row.detector_executable_sha256,
              detection_row.detected_media_type,
              detection_row.verification_status,
              detection_row.recorded_by,
              review_row.review_receipt_sha256,
              review_row.independent_verifier_version,
              review_row.independent_verifier_executable_sha256,
              review_row.independently_detected_media_type,
              review_row.verification_status,
              review_row.reviewed_by
            )
            ORDER BY release_link.source_order
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
    INTO
      inventory_attachment_count,
      inventory_detection_count,
      inventory_detection_review_count,
      inventory_attachment_fingerprint
  FROM nhi_rule_history.release_artifact release_link
  JOIN nhi_rule_history.source_artifact artifact_row
    ON artifact_row.artifact_id = release_link.artifact_id
  LEFT JOIN nhi_rule_history.artifact_format_detection detection_row
    ON detection_row.artifact_id = artifact_row.artifact_id
  LEFT JOIN
    nhi_rule_history.artifact_format_detection_review review_row
    ON review_row.detection_receipt_id =
      detection_row.detection_receipt_id
  WHERE release_link.release_id = parity_row.release_id;

  IF parity_row.verification_status <> 'verified'
     OR parity_row.recorded_by <> case_row.recorded_by
     OR parity_row.proposal_id <> case_row.proposal_id
     OR parity_row.release_id <> resolution_row.new_release_id
     OR parity_release.verification_status <> 'verified'
     OR parity_release.official_attachment_inventory_status <>
       'exhaustive_verified'
     OR parity_release.declared_official_attachment_count <>
       inventory_attachment_count
     OR inventory_detection_count <> inventory_attachment_count
     OR inventory_detection_review_count <>
       inventory_attachment_count
     OR parity_release.official_attachment_inventory_fingerprint <>
       inventory_attachment_fingerprint
     OR parity_row.declared_official_attachment_count <>
       inventory_attachment_count
     OR parity_row.official_attachment_inventory_fingerprint <>
       inventory_attachment_fingerprint
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history.release_artifact release_link
       JOIN nhi_rule_history.source_artifact artifact_row
         ON artifact_row.artifact_id = release_link.artifact_id
       WHERE release_link.release_id = parity_row.release_id
         AND artifact_row.verification_status = 'quarantined'
     )
     OR EXISTS (
       SELECT 1
       FROM nhi_rule_history.release_artifact release_link
       JOIN nhi_rule_history.source_artifact artifact_row
         ON artifact_row.artifact_id = release_link.artifact_id
       LEFT JOIN
         nhi_rule_history.artifact_format_detection detection_row
         ON detection_row.artifact_id = artifact_row.artifact_id
       LEFT JOIN
         nhi_rule_history.artifact_format_detection_review review_row
         ON review_row.detection_receipt_id =
           detection_row.detection_receipt_id
       WHERE release_link.release_id = parity_row.release_id
         AND (
           detection_row.artifact_id IS NULL
           OR detection_row.verification_status <> 'verified'
           OR detection_row.detector_name <>
             'nhi-byte-media-detector'
           OR detection_row.detector_version <>
             'nhi-byte-media-detector/v1'
           OR detection_row.detector_executable_sha256 <>
             encode(
               pg_catalog.sha256(
                 pg_catalog.convert_to(
                   jsonb_build_array(
                     pg_catalog.pg_get_functiondef(
                       'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)'::regprocedure
                     ),
                     pg_catalog.pg_get_functiondef(
                       'nhi_rule_history.inspect_odf_container_detector(bytea)'::regprocedure
                     )
                   )::text,
                   'UTF8'
                 )
               ),
               'hex'
             )
           OR review_row.artifact_id IS NULL
           OR review_row.verification_status <> 'verified'
           OR review_row.independent_verifier_version <>
             'nhi-independent-byte-media-verifier/v1'
           OR
             review_row.independent_verifier_executable_sha256 <>
             encode(
               pg_catalog.sha256(
                 pg_catalog.convert_to(
                   jsonb_build_array(
                     pg_catalog.pg_get_functiondef(
                       'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)'::regprocedure
                     ),
                     pg_catalog.pg_get_functiondef(
                       'nhi_rule_history.inspect_odf_container_reviewer(bytea)'::regprocedure
                     )
                   )::text,
                   'UTF8'
                 )
               ),
               'hex'
             )
           OR review_row.reviewed_by = detection_row.recorded_by
           OR review_row.detection_receipt_sha256 <>
             detection_row.detection_receipt_sha256
           OR review_row.independently_detected_media_type <>
             detection_row.detected_media_type
           OR (
             review_row.independently_detected_media_type IN (
               'application/vnd.oasis.opendocument.text',
               'application/vnd.oasis.opendocument.spreadsheet'
             )
           )
           OR
           (
             release_link.artifact_role = 'official_odt'
             AND review_row.independently_detected_media_type <>
               'application/vnd.oasis.opendocument.text'
           )
           OR (
             release_link.artifact_role = 'official_pdf'
             AND review_row.independently_detected_media_type <>
               'application/pdf'
           )
           OR (
             release_link.artifact_role = 'official_ods'
             AND review_row.independently_detected_media_type <>
               'application/vnd.oasis.opendocument.spreadsheet'
           )
         )
     )
     OR parity_row.source_declared_formats IS DISTINCT FROM
       (
       CASE
         WHEN
           parity_row.format_declaration_raw_text ~*
             '(^|[^[:alnum:]])[.]?odt([^[:alnum:]]|$)'
           AND parity_row.format_declaration_raw_text ~*
             '(^|[^[:alnum:]])[.]?pdf([^[:alnum:]]|$)'
         THEN '["odt", "pdf"]'::jsonb
         WHEN
           parity_row.format_declaration_raw_text ~*
             '(^|[^[:alnum:]])[.]?pdf([^[:alnum:]]|$)'
           AND NOT (
             parity_row.format_declaration_raw_text ~*
               '(^|[^[:alnum:]])[.]?odt([^[:alnum:]]|$)'
           )
         THEN '["pdf"]'::jsonb
         WHEN
           parity_row.format_declaration_raw_text ~*
             '(^|[^[:alnum:]])[.]?odt([^[:alnum:]]|$)'
           AND NOT (
             parity_row.format_declaration_raw_text ~*
               '(^|[^[:alnum:]])[.]?pdf([^[:alnum:]]|$)'
           )
         THEN '["odt"]'::jsonb
         ELSE NULL
       END
       )
     OR (
       parity_row.format_policy IN (
         'odt_pdf_verified',
         'source_declared_odt_only'
       )
       AND (
         parity_row.odt_clause_sha256 <>
           case_row.new_raw_sha256
         OR parity_row.odt_artifact_id <>
           new_span.artifact_id
       )
     )
     OR (
       parity_row.format_policy = 'pdf_verified'
       AND (
         parity_row.pdf_clause_sha256 <>
           case_row.new_raw_sha256
         OR parity_row.pdf_artifact_id <>
           new_span.artifact_id
       )
     )
     OR NOT EXISTS (
       SELECT 1
       FROM
         nhi_rule_history_candidate_stage.candidate_source_span
         declaration_span
       JOIN nhi_rule_history.source_artifact declaration_artifact
         ON declaration_artifact.artifact_id =
           parity_row.format_declaration_artifact_id
       JOIN nhi_rule_history.release_artifact declaration_link
         ON declaration_link.release_id = parity_row.release_id
         AND declaration_link.artifact_id =
           parity_row.format_declaration_artifact_id
       WHERE declaration_span.proposal_id = case_row.proposal_id
         AND declaration_span.span_id =
           parity_row.format_declaration_candidate_span_id
         AND declaration_span.source_role = 'detail_announcement'
         AND declaration_span.artifact_sha256 =
           declaration_artifact.sha256
         AND declaration_artifact.verification_status =
           'full_text_verified'
         AND declaration_span.locator =
           parity_row.format_declaration_source_locator
         AND declaration_span.char_start =
           parity_row.format_declaration_char_start
         AND declaration_span.char_end =
           parity_row.format_declaration_char_end
         AND declaration_span.raw_text =
           parity_row.format_declaration_raw_text
         AND declaration_span.raw_text_sha256 =
           parity_row.format_declaration_raw_sha256
         AND declaration_span.raw_text_sha256 =
           encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(
                 declaration_span.raw_text,
                 'UTF8'
               )
             ),
             'hex'
           )
       )
     OR (
       parity_row.format_policy IN (
         'odt_pdf_verified',
         'source_declared_odt_only'
       )
       AND NOT EXISTS (
       SELECT 1
       FROM nhi_rule_history.source_artifact artifact_row
       JOIN nhi_rule_history.release_artifact release_link
         ON release_link.artifact_id = artifact_row.artifact_id
       JOIN nhi_rule_history.artifact_format_detection detection_row
         ON detection_row.artifact_id = artifact_row.artifact_id
       JOIN
         nhi_rule_history.artifact_format_detection_review review_row
         ON review_row.detection_receipt_id =
           detection_row.detection_receipt_id
       WHERE artifact_row.artifact_id =
           parity_row.odt_artifact_id
         AND review_row.independently_detected_media_type =
           'application/vnd.oasis.opendocument.text'
         AND artifact_row.verification_status =
           'full_text_verified'
         AND release_link.release_id = parity_row.release_id
         AND release_link.artifact_role = 'official_odt'
       )
     )
     OR (
       parity_row.format_policy = 'odt_pdf_verified'
       AND (
         proposal_row.odt_pdf_agreement <> 'agree'
         OR (
           SELECT count(*)
           FROM nhi_rule_history.release_artifact release_link
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND review_row.independently_detected_media_type =
               'application/vnd.oasis.opendocument.text'
         ) <> 1
         OR (
           SELECT count(*)
           FROM nhi_rule_history.release_artifact release_link
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND review_row.independently_detected_media_type =
               'application/pdf'
         ) <> 1
         OR parity_row.pdf_clause_sha256 <>
           case_row.new_raw_sha256
         OR NOT EXISTS (
           SELECT 1
           FROM nhi_rule_history.source_artifact artifact_row
           JOIN nhi_rule_history.release_artifact release_link
             ON release_link.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           JOIN
             nhi_rule_history_candidate_stage.candidate_source_span
             candidate_pdf
             ON candidate_pdf.proposal_id = case_row.proposal_id
             AND candidate_pdf.span_id =
               parity_row.pdf_candidate_span_id
           WHERE artifact_row.artifact_id =
               parity_row.pdf_artifact_id
             AND review_row.independently_detected_media_type =
               'application/pdf'
             AND artifact_row.verification_status =
               'full_text_verified'
             AND release_link.release_id = parity_row.release_id
             AND release_link.artifact_role = 'official_pdf'
             AND candidate_pdf.source_role = 'pdf_corroboration'
             AND candidate_pdf.artifact_sha256 =
               artifact_row.sha256
             AND candidate_pdf.raw_text =
               case_row.new_raw_text
             AND candidate_pdf.raw_text_sha256 =
               case_row.new_raw_sha256
         )
       )
     )
     OR (
       parity_row.format_policy =
         'source_declared_odt_only'
       AND (
         proposal_row.odt_pdf_agreement <> 'not_available'
         OR (
           SELECT count(*)
           FROM nhi_rule_history.release_artifact release_link
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND review_row.independently_detected_media_type =
               'application/vnd.oasis.opendocument.text'
         ) <> 1
         OR NOT EXISTS (
           SELECT 1
           FROM nhi_rule_history.release_artifact release_link
           JOIN nhi_rule_history.source_artifact artifact_row
             ON artifact_row.artifact_id =
               release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND release_link.artifact_id =
               parity_row.odt_artifact_id
             AND release_link.artifact_role = 'official_odt'
             AND review_row.independently_detected_media_type =
               'application/vnd.oasis.opendocument.text'
             AND artifact_row.verification_status =
               'full_text_verified'
         )
         OR EXISTS (
           SELECT 1
           FROM nhi_rule_history.release_artifact release_link
           JOIN nhi_rule_history.source_artifact artifact_row
             ON artifact_row.artifact_id =
               release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND (
               release_link.artifact_role = 'official_pdf'
               OR review_row.independently_detected_media_type =
                 'application/pdf'
             )
         )
       )
     )
     OR (
       parity_row.format_policy = 'pdf_verified'
       AND (
         proposal_row.odt_pdf_agreement <> 'not_available'
         OR (
           SELECT count(*)
           FROM nhi_rule_history.release_artifact release_link
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND review_row.independently_detected_media_type =
               'application/pdf'
         ) <> 1
         OR EXISTS (
           SELECT 1
           FROM nhi_rule_history.release_artifact release_link
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = release_link.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           WHERE release_link.release_id = parity_row.release_id
             AND review_row.independently_detected_media_type IN (
               'application/vnd.oasis.opendocument.text',
               'application/vnd.oasis.opendocument.spreadsheet'
             )
         )
         OR NOT EXISTS (
           SELECT 1
           FROM nhi_rule_history.source_artifact artifact_row
           JOIN nhi_rule_history.release_artifact release_link
             ON release_link.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection detection_row
             ON detection_row.artifact_id = artifact_row.artifact_id
           JOIN
             nhi_rule_history.artifact_format_detection_review review_row
             ON review_row.detection_receipt_id =
               detection_row.detection_receipt_id
           JOIN
             nhi_rule_history_candidate_stage.candidate_source_span
             candidate_pdf
             ON candidate_pdf.proposal_id = case_row.proposal_id
             AND candidate_pdf.span_id =
               parity_row.pdf_candidate_span_id
           WHERE artifact_row.artifact_id =
               parity_row.pdf_artifact_id
             AND review_row.independently_detected_media_type =
               'application/pdf'
             AND artifact_row.verification_status =
               'full_text_verified'
             AND release_link.release_id = parity_row.release_id
             AND release_link.artifact_role = 'official_pdf'
             AND candidate_pdf.artifact_sha256 =
               artifact_row.sha256
             AND candidate_pdf.raw_text =
               case_row.new_raw_text
             AND candidate_pdf.raw_text_sha256 =
               case_row.new_raw_sha256
         )
       )
     ) THEN
    RAISE EXCEPTION
      'declared format policy, ODT/PDF parity, or explicit ODT-only evidence is not verified'
      USING ERRCODE = 'data_exception';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM (
      VALUES
        (pre_anchor.release_id),
        (post_anchor.release_id)
    ) target_release(release_id)
    JOIN nhi_rule_history.release_artifact release_link
      ON release_link.release_id = target_release.release_id
    JOIN nhi_rule_history.artifact_format_detection detection_row
      ON detection_row.artifact_id = release_link.artifact_id
    JOIN
      nhi_rule_history.artifact_format_detection_review review_row
      ON review_row.detection_receipt_id =
        detection_row.detection_receipt_id
    WHERE review_row.independently_detected_media_type =
      'application/pdf'
  ) THEN
    RAISE EXCEPTION
      'blocked_pending_external_pdf_integrity_verifier'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;

  new_snapshot_id_value := 'snapshot:' || $1::text;
  event_effect_id_value := 'effect:' || $1::text;
  comparison_id_value := 'comparison:' || $1::text;
  receipt_id_value := 'promotion:' || $1::text;

  INSERT INTO nhi_rule_history.official_event (
    event_id,
    detail_url,
    issuer,
    reference_number,
    subject,
    event_type,
    document_date,
    publication_date,
    effective_from,
    effective_date_basis,
    effective_date_locator,
    status
  ) VALUES (
    resolution_row.resolved_event_id,
    resolution_row.event_detail_url,
    resolution_row.event_issuer,
    resolution_row.event_reference_number,
    resolution_row.event_subject,
    resolution_row.event_type,
    resolution_row.document_date,
    resolution_row.publication_date,
    resolution_row.effective_from,
    resolution_row.effective_date_basis,
    resolution_row.effective_date_locator,
    'verified'
  )
  ON CONFLICT (event_id) DO NOTHING;

  IF NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history.official_event event_row
    WHERE event_row.event_id = resolution_row.resolved_event_id
      AND event_row.detail_url = resolution_row.event_detail_url
      AND event_row.issuer = resolution_row.event_issuer
      AND event_row.reference_number IS NOT DISTINCT FROM
        resolution_row.event_reference_number
      AND event_row.subject = resolution_row.event_subject
      AND event_row.event_type = resolution_row.event_type
      AND event_row.document_date IS NOT DISTINCT FROM
        resolution_row.document_date
      AND event_row.publication_date =
        resolution_row.publication_date
      AND event_row.effective_from =
        resolution_row.effective_from
      AND event_row.effective_date_basis =
        resolution_row.effective_date_basis
      AND event_row.effective_date_locator =
        resolution_row.effective_date_locator
      AND event_row.status = 'verified'
  ) THEN
    RAISE EXCEPTION
      'resolved event id conflicts with existing canonical event'
      USING ERRCODE = 'unique_violation';
  END IF;

  UPDATE nhi_rule_history.rule_snapshot
  SET effective_until_exclusive = case_row.effective_from
  WHERE rule_snapshot.snapshot_id = predecessor_row.snapshot_id
    AND rule_snapshot.rule_id = resolution_row.rule_id
    AND rule_snapshot.effective_until_exclusive IS NULL;
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count <> 1 THEN
    RAISE EXCEPTION
      'predecessor interval close compare-and-swap failed'
      USING ERRCODE = 'serialization_failure';
  END IF;

  INSERT INTO nhi_rule_history.rule_snapshot (
    snapshot_id,
    rule_id,
    release_id,
    event_id,
    effective_from,
    effective_until_exclusive,
    date_basis,
    date_locator,
    raw_text,
    normalized_text,
    structured_json,
    raw_sha256,
    normalized_sha256,
    source_locator_json,
    parser_version,
    validation_status,
    publication_status
  ) VALUES (
    new_snapshot_id_value,
    resolution_row.rule_id,
    resolution_row.new_release_id,
    resolution_row.resolved_event_id,
    case_row.effective_from,
    NULL,
    resolution_row.effective_date_basis,
    resolution_row.effective_date_locator,
    case_row.new_raw_text,
    case_row.new_normalized_text,
    case_row.new_structured_json,
    case_row.new_raw_sha256,
    case_row.new_normalized_sha256,
    new_span.source_locator,
    case_row.parser_version,
    'verified',
    case_row.publication_status
  );

  INSERT INTO nhi_rule_history.official_event_effect (
    event_effect_id,
    event_id,
    operation,
    replacement_scope,
    effective_from,
    effective_date_raw,
    effective_date_locator,
    target_designation_raw,
    authoritative_order,
    rule_id,
    old_snapshot_id,
    new_snapshot_id,
    resolution_status
  ) VALUES (
    event_effect_id_value,
    resolution_row.resolved_event_id,
    'amend',
    'full_single_clause',
    case_row.effective_from,
    resolution_row.effective_date_raw,
    resolution_row.effective_date_locator,
    resolution_row.target_designation_raw,
    resolution_row.authoritative_event_order,
    resolution_row.rule_id,
    predecessor_row.snapshot_id,
    new_snapshot_id_value,
    'verified'
  );

  INSERT INTO nhi_rule_history.snapshot_evidence (
    snapshot_evidence_id,
    snapshot_id,
    artifact_id,
    evidence_kind,
    source_locator_json,
    source_text_sha256,
    evidence_status
  )
  SELECT
    'evidence:' || $1::text || ':' || span_row.span_order::text,
    new_snapshot_id_value,
    span_row.artifact_id,
    CASE span_row.span_role
      WHEN 'comparison_new_full_text' THEN
        'comparison_new_full_text'
      WHEN 'effective_date' THEN 'effective_date'
      WHEN 'designation' THEN 'designation'
      WHEN 'official_event' THEN 'official_event'
    END,
    span_row.source_locator,
    span_row.raw_text_sha256,
    'verified'
  FROM nhi_rule_history_promotion.effect_resolution_span span_row
  WHERE span_row.case_id = $1
    AND span_row.span_role IN (
      'comparison_new_full_text',
      'effective_date',
      'designation',
      'official_event'
    );

  INSERT INTO nhi_rule_history.snapshot_evidence (
    snapshot_evidence_id,
    snapshot_id,
    artifact_id,
    evidence_kind,
    source_locator_json,
    source_text_sha256,
    evidence_status
  ) VALUES (
    'evidence:' || $1::text || ':post-anchor',
    new_snapshot_id_value,
    post_anchor.artifact_id,
    'post_anchor',
    post_clause.source_locator,
    post_clause.raw_text_sha256,
    'verified'
  );

  INSERT INTO nhi_rule_history.comparison_edge (
    comparison_id,
    rule_id,
    older_snapshot_id,
    newer_snapshot_id,
    is_direct_predecessor,
    algorithm_version,
    input_sha256,
    output_sha256,
    mapping_coverage,
    format_only,
    crosses_known_gap,
    status
  ) VALUES (
    comparison_id_value,
    resolution_row.rule_id,
    predecessor_row.snapshot_id,
    new_snapshot_id_value,
    true,
    resolution_row.comparison_algorithm_version,
    resolution_row.comparison_input_sha256,
    resolution_row.comparison_output_sha256,
    resolution_row.comparison_mapping_coverage,
    resolution_row.comparison_format_only,
    false,
    'verified'
  );

  UPDATE nhi_rule_history.rule_head
  SET
    current_snapshot_id = new_snapshot_id_value,
    head_generation = head_generation + 1,
    updated_at = clock_timestamp()
  WHERE rule_head.rule_id = resolution_row.rule_id
    AND rule_head.current_snapshot_id = predecessor_row.snapshot_id
    AND rule_head.head_generation = $3;
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count <> 1 THEN
    RAISE EXCEPTION
      'canonical head generation compare-and-swap failed'
      USING ERRCODE = 'serialization_failure';
  END IF;

  INSERT INTO nhi_rule_history.promotion_receipt (
    receipt_id,
    case_id,
    case_fingerprint,
    rule_id,
    event_id,
    old_snapshot_id,
    new_snapshot_id,
    prior_head_generation,
    new_head_generation,
    effective_from,
    executed_by
  ) VALUES (
    receipt_id_value,
    $1,
    case_row.case_fingerprint,
    resolution_row.rule_id,
    resolution_row.resolved_event_id,
    predecessor_row.snapshot_id,
    new_snapshot_id_value,
    $3,
    $3 + 1,
    case_row.effective_from,
    authenticated_executor
  );

  INSERT INTO nhi_rule_history_promotion.promotion_transition (
    case_id,
    transition_seq,
    transition_id,
    state,
    decision_basis_sha256
  ) VALUES (
    $1,
    prior_transition_seq + 1,
    'transition:' || $1::text || ':promoted',
    'promoted',
    case_row.case_fingerprint
  );

  RETURN QUERY SELECT
    receipt_id_value,
    false,
    new_snapshot_id_value,
    $3 + 1;
END;
$function$;

ALTER SCHEMA nhi_rule_history_promotion
  OWNER TO nhi_rule_history_owner;
ALTER DOMAIN nhi_rule_history_promotion.sha256_hex
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.promotion_case
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.effect_resolution
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.effect_resolution_span
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.anchor_snapshot
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.anchor_clause
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.replay_run
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.replay_rule_result
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.replay_event
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.format_parity_receipt
  OWNER TO nhi_rule_history_owner;
ALTER TABLE nhi_rule_history_promotion.promotion_transition
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history_promotion.reject_evidence_mutation()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history_promotion.guard_evidence_insert_actor()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION
  nhi_rule_history_promotion.guard_promotion_transition()
  OWNER TO nhi_rule_history_owner;
ALTER FUNCTION nhi_rule_history_promotion.promote_case(uuid, text, bigint)
  OWNER TO nhi_rule_history_owner;

REVOKE ALL ON SCHEMA nhi_rule_history_promotion FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA
  nhi_rule_history_promotion FROM PUBLIC;
REVOKE ALL ON TYPE
  nhi_rule_history_promotion.sha256_hex FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA
  nhi_rule_history_promotion FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history_promotion
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history_promotion
  REVOKE ALL ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE nhi_rule_history_owner
  IN SCHEMA nhi_rule_history_promotion
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT USAGE ON SCHEMA nhi_rule_history_promotion
  TO
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
GRANT SELECT ON ALL TABLES IN SCHEMA
  nhi_rule_history_promotion TO
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
GRANT INSERT ON
  nhi_rule_history_promotion.promotion_case,
  nhi_rule_history_promotion.effect_resolution,
  nhi_rule_history_promotion.effect_resolution_span,
  nhi_rule_history_promotion.anchor_snapshot,
  nhi_rule_history_promotion.anchor_clause,
  nhi_rule_history_promotion.replay_run,
  nhi_rule_history_promotion.replay_rule_result,
  nhi_rule_history_promotion.replay_event,
  nhi_rule_history_promotion.format_parity_receipt
  TO nhi_rule_history_promotion_writer;
GRANT INSERT ON
  nhi_rule_history_promotion.promotion_transition
  TO nhi_rule_history_promotion_reviewer;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_promotion.promote_case(uuid, text, bigint)
  TO nhi_rule_history_promotion_executor;

GRANT USAGE ON SCHEMA nhi_rule_history_candidate_stage
  TO
    nhi_rule_history_owner,
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
GRANT SELECT ON
  nhi_rule_history_candidate_stage.candidate_proposal,
  nhi_rule_history_candidate_stage.candidate_source_span,
  nhi_rule_history_candidate_stage.current_candidate_state
  TO
    nhi_rule_history_owner,
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;

DO $stage_roles_zero_promotion_or_canonical_dml$
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
        'REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_promotion FROM %I',
        role_name
      );
      EXECUTE format(
        'REVOKE ALL ON SCHEMA nhi_rule_history FROM %I',
        role_name
      );
      EXECUTE format(
        'REVOKE ALL ON SCHEMA nhi_rule_history_promotion FROM %I',
        role_name
      );
    END IF;
  END LOOP;
END;
$stage_roles_zero_promotion_or_canonical_dml$;

DO $seal_contract$
DECLARE
  managed_prefix text :=
    'Reviewed evidence and atomic promotion boundary for canonical NHI rule history. managed=nhi_rule_history_promotion/v1';
  actual_fingerprint text;
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class relation_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = relation_row.relnamespace
    WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
      AND relation_row.relkind IN ('r', 'p', 'm', 'S', 'i')
      AND relation_row.relpersistence <> 'p'
  ) THEN
    RAISE EXCEPTION
      'promotion v1 requires persistent relations'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

  WITH contract_line AS (
    SELECT
      'N|' || namespace_row.nspname || '|' ||
      pg_get_userbyid(namespace_row.nspowner) || '|' ||
      coalesce(namespace_row.nspacl::text, '') AS line
    FROM pg_namespace namespace_row
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
    WHERE namespace_row.nspname =
      'nhi_rule_history_promotion'
    UNION ALL
    SELECT
      'Y|' || type_row.typname || '|' ||
      type_row.typtype::text || '|' ||
      pg_get_userbyid(type_row.typowner) || '|' ||
      coalesce(type_row.typacl::text, '')
    FROM pg_type type_row
    JOIN pg_namespace namespace_row
      ON namespace_row.oid = type_row.typnamespace
    WHERE namespace_row.nspname =
        'nhi_rule_history_promotion'
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
    'COMMENT ON SCHEMA nhi_rule_history_promotion IS %L',
    managed_prefix || ' contract_sha256=' || actual_fingerprint
  );
END;
$seal_contract$;

COMMENT ON TABLE nhi_rule_history_promotion.promotion_case IS
  'Immutable reviewed case envelope; only amend/full_single_clause is representable in v1.';
COMMENT ON TABLE nhi_rule_history_promotion.effect_resolution IS
  'Independent stable-identity and official-effect resolution for one case.';
COMMENT ON FUNCTION
  nhi_rule_history_promotion.promote_case(uuid, text, bigint) IS
  'Atomic, idempotent, per-rule locked promotion with date, evidence, replay, parity, and head-generation gates.';

COMMIT;
