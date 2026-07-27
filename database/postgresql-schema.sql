CREATE SCHEMA IF NOT EXISTS nhi_rule_history;
SET search_path TO nhi_rule_history, public;

CREATE TABLE dataset_release (
    release_id text PRIMARY KEY,
    release_kind text NOT NULL CHECK (release_kind IN (
        'annual_full', 'current_full', 'current_chapter', 'event_attachment'
    )),
    official_label text NOT NULL,
    jurisdiction text NOT NULL DEFAULT 'TW',
    release_date date,
    release_date_basis text,
    source_page_url text NOT NULL,
    manifest_sha256 char(64) NOT NULL,
    status text NOT NULL CHECK (status IN (
        'discovered', 'acquired', 'parsed', 'verified', 'quarantined'
    )),
    created_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE source_artifact (
    artifact_id text PRIMARY KEY,
    official_url text NOT NULL,
    filename text NOT NULL,
    media_type text NOT NULL,
    byte_length bigint NOT NULL CHECK (byte_length >= 0),
    sha256 char(64) NOT NULL UNIQUE,
    release_asset_url text,
    fetched_at timestamptz NOT NULL,
    fetch_transport text NOT NULL,
    licence text NOT NULL,
    supersedes_artifact_id text REFERENCES source_artifact(artifact_id),
    parse_status text NOT NULL
);

CREATE TABLE release_artifact (
    release_id text NOT NULL REFERENCES dataset_release(release_id),
    artifact_id text NOT NULL REFERENCES source_artifact(artifact_id),
    artifact_role text NOT NULL,
    source_order integer NOT NULL,
    PRIMARY KEY (release_id, artifact_id),
    UNIQUE (release_id, source_order)
);

CREATE TABLE official_event (
    event_id text PRIMARY KEY,
    detail_url text NOT NULL,
    issuer text NOT NULL,
    reference_number text,
    subject text NOT NULL,
    event_type text NOT NULL,
    document_date date,
    publication_date date,
    effective_from date,
    effective_to date,
    effective_date_basis text,
    effective_date_locator jsonb,
    supersedes_event_id text REFERENCES official_event(event_id),
    status text NOT NULL,
    UNIQUE (issuer, reference_number, detail_url)
);

CREATE TABLE rule_identity (
    rule_id text PRIMARY KEY,
    canonical_slug text NOT NULL UNIQUE,
    identity_status text NOT NULL CHECK (identity_status IN (
        'active', 'retired', 'split', 'merged', 'unresolved'
    )),
    first_seen_release_id text REFERENCES dataset_release(release_id),
    last_seen_release_id text REFERENCES dataset_release(release_id)
);

CREATE TABLE rule_designation (
    designation_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    designation_type text NOT NULL,
    designation_value text NOT NULL,
    title text,
    valid_from date,
    valid_to date,
    evidence_locator jsonb NOT NULL,
    UNIQUE (rule_id, designation_type, designation_value, valid_from)
);

CREATE TABLE rule_navigation_assignment (
    navigation_assignment_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    source_designation_raw text NOT NULL,
    navigation_code text NOT NULL,
    code_origin text NOT NULL CHECK (code_origin IN (
        'official_source', 'project_assigned'
    )),
    display_label text NOT NULL,
    sort_order integer NOT NULL,
    valid_from date,
    valid_to date,
    evidence_locator jsonb NOT NULL,
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    UNIQUE (rule_id, navigation_code, valid_from)
);

CREATE TABLE rule_snapshot (
    snapshot_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    release_id text NOT NULL REFERENCES dataset_release(release_id),
    event_id text REFERENCES official_event(event_id),
    effective_from date,
    effective_to date,
    date_basis text,
    date_locator jsonb,
    raw_text text NOT NULL,
    normalized_text text NOT NULL,
    structured_json jsonb NOT NULL,
    raw_sha256 char(64) NOT NULL,
    normalized_sha256 char(64) NOT NULL,
    source_locator_json jsonb NOT NULL,
    parser_version text NOT NULL,
    validation_status text NOT NULL,
    publication_status text NOT NULL CHECK (publication_status IN (
        'blocked', 'canary', 'publishable'
    )),
    UNIQUE (rule_id, release_id, raw_sha256)
);

CREATE TABLE source_date_annotation (
    annotation_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    artifact_id text NOT NULL REFERENCES source_artifact(artifact_id),
    source_locator_sha256 char(64) NOT NULL,
    source_locator_json jsonb NOT NULL,
    raw_date_text text NOT NULL,
    calendar_system text NOT NULL CHECK (calendar_system IN (
        'ROC', 'Gregorian', 'mixed', 'unknown'
    )),
    iso_date_candidate date,
    annotation_scope text NOT NULL CHECK (annotation_scope IN (
        'rule', 'subitem', 'sentence', 'marginal_note', 'unknown'
    )),
    resolution_status text NOT NULL CHECK (resolution_status IN (
        'unresolved_event', 'event_resolved', 'transition_verified',
        'rejected_non_amendment'
    )),
    unresolved_reason text,
    CHECK (
        resolution_status <> 'unresolved_event'
        OR unresolved_reason IS NOT NULL
    ),
    UNIQUE (snapshot_id, artifact_id, source_locator_sha256, raw_date_text)
);

CREATE TABLE official_event_effect (
    event_effect_id text PRIMARY KEY,
    event_id text NOT NULL REFERENCES official_event(event_id),
    operation text NOT NULL CHECK (operation IN (
        'create', 'amend', 'delete', 'restore', 'rename', 'move',
        'split', 'merge', 'correction'
    )),
    effective_date date,
    effective_date_raw text,
    effective_date_locator jsonb,
    target_designation_raw text,
    rule_id text REFERENCES rule_identity(rule_id),
    old_snapshot_id text REFERENCES rule_snapshot(snapshot_id),
    new_snapshot_id text REFERENCES rule_snapshot(snapshot_id),
    resolution_status text NOT NULL
);

CREATE TABLE source_date_annotation_effect (
    annotation_id text NOT NULL REFERENCES source_date_annotation(annotation_id),
    event_effect_id text NOT NULL REFERENCES official_event_effect(event_effect_id),
    relation_type text NOT NULL CHECK (relation_type IN (
        'supports', 'contradicts', 'superseded_by'
    )),
    decision_evidence_json jsonb NOT NULL,
    PRIMARY KEY (annotation_id, event_effect_id, relation_type)
);

CREATE TABLE rule_lineage_edge (
    lineage_edge_id text PRIMARY KEY,
    from_rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    to_rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    relation text NOT NULL CHECK (relation IN (
        'rename', 'move', 'split', 'merge', 'replacement', 'restore'
    )),
    event_id text REFERENCES official_event(event_id),
    decision_id text,
    CHECK (from_rule_id <> to_rule_id)
);

CREATE TABLE snapshot_evidence (
    snapshot_evidence_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    artifact_id text NOT NULL REFERENCES source_artifact(artifact_id),
    evidence_kind text NOT NULL,
    source_locator_json jsonb NOT NULL,
    evidence_status text NOT NULL,
    UNIQUE (snapshot_id, artifact_id, evidence_kind, source_locator_json)
);

CREATE TABLE rule_block (
    block_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    source_order integer NOT NULL,
    block_kind text NOT NULL,
    raw_text text NOT NULL,
    normalized_text text NOT NULL,
    raw_sha256 char(64) NOT NULL,
    source_locator_json jsonb NOT NULL,
    UNIQUE (snapshot_id, source_order)
);

CREATE TABLE comparison_edge (
    comparison_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    older_snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    newer_snapshot_id text NOT NULL UNIQUE REFERENCES rule_snapshot(snapshot_id),
    is_direct_predecessor boolean NOT NULL,
    algorithm_version text NOT NULL,
    input_sha256 char(64) NOT NULL,
    output_sha256 char(64) NOT NULL,
    mapping_coverage numeric NOT NULL CHECK (
        mapping_coverage >= 0 AND mapping_coverage <= 1
    ),
    format_only boolean NOT NULL,
    crosses_known_gap boolean NOT NULL,
    status text NOT NULL CHECK (status IN ('verified', 'ambiguous', 'blocked')),
    CHECK (older_snapshot_id <> newer_snapshot_id)
);

CREATE TABLE rule_history_coverage (
    coverage_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    declared_cut_release_id text NOT NULL REFERENCES dataset_release(release_id),
    annotation_count integer NOT NULL CHECK (annotation_count >= 0),
    resolved_annotation_count integer NOT NULL CHECK (
        resolved_annotation_count >= 0
        AND resolved_annotation_count <= annotation_count
    ),
    verified_transition_count integer NOT NULL CHECK (
        verified_transition_count >= 0
        AND verified_transition_count <= resolved_annotation_count
    ),
    snapshot_count integer NOT NULL CHECK (snapshot_count >= 0),
    direct_edge_count integer NOT NULL CHECK (direct_edge_count >= 0),
    unresolved_gap_count integer NOT NULL CHECK (unresolved_gap_count >= 0),
    source_universe_closed boolean NOT NULL,
    cumulative_anchor_parity boolean NOT NULL,
    completion_status text NOT NULL CHECK (completion_status IN (
        'blocked', 'complete_to_declared_cut'
    )),
    gap_reasons_json jsonb NOT NULL,
    assessed_at timestamptz NOT NULL,
    CHECK (
        completion_status <> 'complete_to_declared_cut'
        OR (
            resolved_annotation_count = annotation_count
            AND verified_transition_count = resolved_annotation_count
            AND direct_edge_count = GREATEST(snapshot_count - 1, 0)
            AND unresolved_gap_count = 0
            AND source_universe_closed
            AND cumulative_anchor_parity
        )
    ),
    UNIQUE (rule_id, declared_cut_release_id)
);

CREATE TABLE diff_hunk (
    hunk_id text PRIMARY KEY,
    comparison_id text NOT NULL REFERENCES comparison_edge(comparison_id),
    hunk_order integer NOT NULL,
    hunk_type text NOT NULL CHECK (hunk_type IN (
        'unchanged', 'added', 'removed', 'exact_move', 'format_only', 'ambiguous'
    )),
    old_block_id text REFERENCES rule_block(block_id),
    new_block_id text REFERENCES rule_block(block_id),
    old_start integer,
    old_end integer,
    new_start integer,
    new_end integer,
    old_text_sha256 char(64),
    new_text_sha256 char(64),
    display_on_new boolean NOT NULL,
    display_on_old boolean NOT NULL,
    UNIQUE (comparison_id, hunk_order)
);

CREATE TABLE drug_concept (
    drug_concept_id text PRIMARY KEY,
    concept_kind text NOT NULL CHECK (concept_kind IN (
        'ingredient', 'combination', 'product', 'route_strength', 'class'
    )),
    preferred_name text NOT NULL,
    normalized_name text NOT NULL,
    route text,
    strength text,
    review_status text NOT NULL
);

CREATE TABLE drug_identifier (
    drug_identifier_id text PRIMARY KEY,
    drug_concept_id text NOT NULL REFERENCES drug_concept(drug_concept_id),
    identifier_system text NOT NULL,
    identifier_value text NOT NULL,
    source_url text,
    UNIQUE (identifier_system, identifier_value)
);

CREATE TABLE rule_drug_link (
    rule_drug_link_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    drug_concept_id text NOT NULL REFERENCES drug_concept(drug_concept_id),
    relation_type text NOT NULL,
    source_system text NOT NULL,
    source_record_id text,
    source_text text,
    source_start integer,
    source_end integer,
    confidence numeric,
    review_status text NOT NULL,
    UNIQUE (snapshot_id, drug_concept_id, relation_type, source_system, source_record_id)
);

CREATE TABLE drug_atc_link (
    drug_atc_link_id text PRIMARY KEY,
    drug_concept_id text NOT NULL REFERENCES drug_concept(drug_concept_id),
    atc_code text NOT NULL,
    atc_version text NOT NULL,
    relation_type text NOT NULL,
    source_system text NOT NULL,
    source_record_id text,
    source_url text,
    source_text text,
    is_primary boolean NOT NULL,
    confidence numeric,
    review_status text NOT NULL,
    reviewed_at timestamptz,
    UNIQUE (drug_concept_id, atc_code, atc_version, source_system, source_record_id)
);

CREATE TABLE indication (
    indication_id text PRIMARY KEY,
    source_text text NOT NULL,
    normalized_text text NOT NULL,
    qualifiers_json jsonb NOT NULL,
    source_language text NOT NULL DEFAULT 'zh-TW'
);

CREATE TABLE rule_indication_link (
    rule_indication_link_id text PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    indication_id text NOT NULL REFERENCES indication(indication_id),
    relation_type text NOT NULL,
    source_start integer,
    source_end integer,
    review_status text NOT NULL,
    UNIQUE (snapshot_id, indication_id, relation_type, source_start, source_end)
);

CREATE TABLE external_concept_link (
    external_concept_link_id text PRIMARY KEY,
    indication_id text NOT NULL REFERENCES indication(indication_id),
    system text NOT NULL,
    system_version text NOT NULL,
    external_code text,
    external_title text,
    external_uri text,
    relation_type text NOT NULL,
    mapping_method text NOT NULL,
    review_status text NOT NULL,
    permission_basis text,
    publication_status text NOT NULL CHECK (publication_status IN (
        'internal_candidate', 'blocked', 'publishable'
    )),
    CHECK (
        system <> 'ICD11'
        OR publication_status <> 'publishable'
        OR permission_basis IS NOT NULL
    )
);

CREATE TABLE build_run (
    build_run_id text PRIMARY KEY,
    code_version text NOT NULL,
    input_manifest_sha256 char(64) NOT NULL,
    config_sha256 char(64) NOT NULL,
    output_fingerprint char(64),
    state text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    counts_json jsonb NOT NULL
);

CREATE TABLE build_issue (
    build_issue_id text PRIMARY KEY,
    build_run_id text NOT NULL REFERENCES build_run(build_run_id),
    severity text NOT NULL,
    issue_code text NOT NULL,
    object_type text,
    object_id text,
    message text NOT NULL,
    evidence_json jsonb NOT NULL,
    resolution_status text NOT NULL
);

CREATE TABLE search_document (
    document_id text PRIMARY KEY,
    rule_id text NOT NULL REFERENCES rule_identity(rule_id),
    snapshot_id text NOT NULL REFERENCES rule_snapshot(snapshot_id),
    title text,
    designation text,
    drug_names text,
    atc_codes text,
    indication_text text,
    rule_text text NOT NULL
);

CREATE INDEX idx_release_artifact_artifact ON release_artifact(artifact_id);
CREATE INDEX idx_event_reference ON official_event(reference_number);
CREATE INDEX idx_snapshot_rule_date ON rule_snapshot(rule_id, effective_from);
CREATE INDEX idx_designation_value ON rule_designation(designation_value);
CREATE INDEX idx_navigation_code ON rule_navigation_assignment(navigation_code);
CREATE INDEX idx_annotation_snapshot ON source_date_annotation(snapshot_id);
CREATE INDEX idx_annotation_iso_date ON source_date_annotation(iso_date_candidate);
CREATE INDEX idx_coverage_status ON rule_history_coverage(completion_status);
CREATE INDEX idx_rule_drug_snapshot ON rule_drug_link(snapshot_id);
CREATE INDEX idx_drug_atc_code ON drug_atc_link(atc_code);
CREATE INDEX idx_indication_normalized ON indication(normalized_text);
CREATE INDEX idx_external_concept_code ON external_concept_link(system, external_code);
CREATE INDEX idx_build_issue_run_severity ON build_issue(build_run_id, severity);

-- Operational continuous-update state is deliberately outside this canonical
-- legal-history build schema. Its stage-only PostgreSQL structure, including
-- the append-only `nhi_rule_history_update_queue.work_item_attempt` ledger for
-- successful and transiently failed acquisition/corpus/proposal attempts, is
-- defined by pg/migrations/2026-07-27_nhi_rule_history_update_queue.sql.
