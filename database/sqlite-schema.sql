PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value) VALUES
    ('schema_name', 'nhi-rule-history'),
    ('schema_version', '3'),
    ('projection', 'sqlite-portable');

CREATE TABLE dataset_release (
    release_id TEXT PRIMARY KEY,
    release_kind TEXT NOT NULL CHECK (release_kind IN (
        'annual_full', 'current_full', 'current_chapter', 'event_attachment',
        'nhi_drug_item_snapshot', 'tfda_drug_snapshot'
    )),
    official_label TEXT NOT NULL,
    jurisdiction TEXT NOT NULL DEFAULT 'TW',
    release_date TEXT,
    release_date_basis TEXT,
    source_page_url TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    status TEXT NOT NULL CHECK (status IN (
        'discovered', 'acquired', 'parsed', 'verified', 'quarantined'
    )),
    created_at TEXT NOT NULL
);

CREATE TABLE source_artifact (
    artifact_id TEXT PRIMARY KEY,
    official_url TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    sha256 TEXT NOT NULL UNIQUE CHECK (length(sha256) = 64),
    release_asset_url TEXT,
    fetched_at TEXT NOT NULL,
    fetch_transport TEXT NOT NULL,
    licence TEXT NOT NULL,
    supersedes_artifact_id TEXT REFERENCES source_artifact(artifact_id),
    parse_status TEXT NOT NULL
);

CREATE TABLE release_artifact (
    release_id TEXT NOT NULL REFERENCES dataset_release(release_id),
    artifact_id TEXT NOT NULL REFERENCES source_artifact(artifact_id),
    artifact_role TEXT NOT NULL,
    source_order INTEGER NOT NULL,
    PRIMARY KEY (release_id, artifact_id),
    UNIQUE (release_id, source_order)
);

CREATE TABLE official_event (
    event_id TEXT PRIMARY KEY,
    detail_url TEXT NOT NULL,
    issuer TEXT NOT NULL,
    reference_number TEXT,
    subject TEXT NOT NULL,
    event_type TEXT NOT NULL,
    document_date TEXT,
    publication_date TEXT,
    effective_from TEXT,
    effective_to TEXT,
    effective_date_basis TEXT,
    effective_date_locator TEXT,
    supersedes_event_id TEXT REFERENCES official_event(event_id),
    status TEXT NOT NULL,
    UNIQUE (issuer, reference_number, detail_url)
);

CREATE TABLE rule_identity (
    rule_id TEXT PRIMARY KEY,
    canonical_slug TEXT NOT NULL UNIQUE,
    identity_status TEXT NOT NULL CHECK (identity_status IN (
        'active', 'retired', 'split', 'merged', 'unresolved'
    )),
    first_seen_release_id TEXT REFERENCES dataset_release(release_id),
    last_seen_release_id TEXT REFERENCES dataset_release(release_id)
);

CREATE TABLE rule_designation (
    designation_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    designation_type TEXT NOT NULL,
    designation_value TEXT NOT NULL,
    title TEXT,
    valid_from TEXT,
    valid_to TEXT,
    evidence_locator TEXT NOT NULL,
    UNIQUE (rule_id, designation_type, designation_value, valid_from)
);

CREATE TABLE rule_navigation_assignment (
    navigation_assignment_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    source_designation_raw TEXT NOT NULL,
    navigation_code TEXT NOT NULL,
    code_origin TEXT NOT NULL CHECK (code_origin IN (
        'official_source', 'project_assigned'
    )),
    display_label TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    evidence_locator TEXT NOT NULL,
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    UNIQUE (rule_id, navigation_code, valid_from)
);

CREATE TABLE rule_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    release_id TEXT NOT NULL REFERENCES dataset_release(release_id),
    event_id TEXT REFERENCES official_event(event_id),
    effective_from TEXT,
    effective_to TEXT,
    date_basis TEXT,
    date_locator TEXT,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
    normalized_sha256 TEXT NOT NULL CHECK (length(normalized_sha256) = 64),
    source_locator_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    publication_status TEXT NOT NULL CHECK (publication_status IN (
        'blocked', 'canary', 'publishable'
    )),
    UNIQUE (snapshot_id, rule_id),
    UNIQUE (rule_id, release_id, raw_sha256)
);

CREATE TABLE source_date_annotation (
    annotation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES source_artifact(artifact_id),
    source_locator_sha256 TEXT NOT NULL CHECK (length(source_locator_sha256) = 64),
    source_locator_json TEXT NOT NULL,
    raw_date_text TEXT NOT NULL,
    calendar_system TEXT NOT NULL CHECK (calendar_system IN (
        'ROC', 'Gregorian', 'mixed', 'unknown'
    )),
    iso_date_candidate TEXT,
    annotation_scope TEXT NOT NULL CHECK (annotation_scope IN (
        'rule', 'subitem', 'sentence', 'marginal_note', 'unknown'
    )),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'unresolved_event', 'event_resolved', 'transition_verified',
        'rejected_non_amendment'
    )),
    unresolved_reason TEXT,
    CHECK (
        resolution_status <> 'unresolved_event'
        OR unresolved_reason IS NOT NULL
    ),
    UNIQUE (snapshot_id, artifact_id, source_locator_sha256, raw_date_text)
);

CREATE TABLE official_event_effect (
    event_effect_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES official_event(event_id),
    operation TEXT NOT NULL CHECK (operation IN (
        'create', 'amend', 'delete', 'restore', 'rename', 'move',
        'split', 'merge', 'correction'
    )),
    effective_date TEXT,
    effective_date_raw TEXT,
    effective_date_locator TEXT,
    target_designation_raw TEXT,
    rule_id TEXT REFERENCES rule_identity(rule_id),
    old_snapshot_id TEXT REFERENCES rule_snapshot(snapshot_id),
    new_snapshot_id TEXT REFERENCES rule_snapshot(snapshot_id),
    resolution_status TEXT NOT NULL
);

CREATE TABLE source_date_annotation_effect (
    annotation_id TEXT NOT NULL REFERENCES source_date_annotation(annotation_id),
    event_effect_id TEXT NOT NULL REFERENCES official_event_effect(event_effect_id),
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'supports', 'contradicts', 'superseded_by'
    )),
    decision_evidence_json TEXT NOT NULL,
    PRIMARY KEY (annotation_id, event_effect_id, relation_type)
);

CREATE TABLE rule_lineage_edge (
    lineage_edge_id TEXT PRIMARY KEY,
    from_rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    to_rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    relation TEXT NOT NULL CHECK (relation IN (
        'rename', 'move', 'split', 'merge', 'replacement', 'restore'
    )),
    event_id TEXT REFERENCES official_event(event_id),
    decision_id TEXT,
    CHECK (from_rule_id <> to_rule_id)
);

CREATE TABLE snapshot_evidence (
    snapshot_evidence_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES source_artifact(artifact_id),
    evidence_kind TEXT NOT NULL,
    source_locator_json TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    UNIQUE (snapshot_id, artifact_id, evidence_kind, source_locator_json)
);

CREATE TABLE rule_block (
    block_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    source_order INTEGER NOT NULL,
    block_kind TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256) = 64),
    source_locator_json TEXT NOT NULL,
    UNIQUE (snapshot_id, source_order)
);

CREATE TABLE comparison_edge (
    comparison_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    older_snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    newer_snapshot_id TEXT NOT NULL UNIQUE REFERENCES rule_snapshot(snapshot_id),
    is_direct_predecessor INTEGER NOT NULL CHECK (is_direct_predecessor IN (0, 1)),
    algorithm_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
    mapping_coverage REAL NOT NULL CHECK (mapping_coverage >= 0 AND mapping_coverage <= 1),
    format_only INTEGER NOT NULL CHECK (format_only IN (0, 1)),
    crosses_known_gap INTEGER NOT NULL CHECK (crosses_known_gap IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('verified', 'ambiguous', 'blocked')),
    CHECK (older_snapshot_id <> newer_snapshot_id)
);

CREATE TABLE rule_history_coverage (
    coverage_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    declared_cut_release_id TEXT NOT NULL REFERENCES dataset_release(release_id),
    annotation_count INTEGER NOT NULL CHECK (annotation_count >= 0),
    resolved_annotation_count INTEGER NOT NULL CHECK (
        resolved_annotation_count >= 0
        AND resolved_annotation_count <= annotation_count
    ),
    verified_transition_count INTEGER NOT NULL CHECK (
        verified_transition_count >= 0
        AND verified_transition_count <= resolved_annotation_count
    ),
    snapshot_count INTEGER NOT NULL CHECK (snapshot_count >= 0),
    direct_edge_count INTEGER NOT NULL CHECK (direct_edge_count >= 0),
    unresolved_gap_count INTEGER NOT NULL CHECK (unresolved_gap_count >= 0),
    source_universe_closed INTEGER NOT NULL CHECK (source_universe_closed IN (0, 1)),
    cumulative_anchor_parity INTEGER NOT NULL CHECK (cumulative_anchor_parity IN (0, 1)),
    completion_status TEXT NOT NULL CHECK (completion_status IN (
        'blocked', 'complete_to_declared_cut'
    )),
    gap_reasons_json TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    CHECK (
        completion_status <> 'complete_to_declared_cut'
        OR (
            resolved_annotation_count = annotation_count
            AND verified_transition_count = resolved_annotation_count
            AND direct_edge_count = max(snapshot_count - 1, 0)
            AND unresolved_gap_count = 0
            AND source_universe_closed = 1
            AND cumulative_anchor_parity = 1
        )
    ),
    UNIQUE (rule_id, declared_cut_release_id)
);

CREATE TABLE diff_hunk (
    hunk_id TEXT PRIMARY KEY,
    comparison_id TEXT NOT NULL REFERENCES comparison_edge(comparison_id),
    hunk_order INTEGER NOT NULL,
    hunk_type TEXT NOT NULL CHECK (hunk_type IN (
        'unchanged', 'added', 'removed', 'exact_move', 'format_only', 'ambiguous'
    )),
    old_block_id TEXT REFERENCES rule_block(block_id),
    new_block_id TEXT REFERENCES rule_block(block_id),
    old_start INTEGER,
    old_end INTEGER,
    new_start INTEGER,
    new_end INTEGER,
    old_text_sha256 TEXT,
    new_text_sha256 TEXT,
    display_on_new INTEGER NOT NULL CHECK (display_on_new IN (0, 1)),
    display_on_old INTEGER NOT NULL CHECK (display_on_old IN (0, 1)),
    UNIQUE (comparison_id, hunk_order)
);

CREATE TABLE drug_concept (
    drug_concept_id TEXT PRIMARY KEY,
    concept_kind TEXT NOT NULL CHECK (concept_kind IN (
        'ingredient', 'combination', 'product', 'route_strength', 'class'
    )),
    preferred_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    route TEXT,
    strength TEXT,
    review_status TEXT NOT NULL
);

CREATE TABLE drug_identifier (
    drug_identifier_id TEXT PRIMARY KEY,
    drug_concept_id TEXT NOT NULL REFERENCES drug_concept(drug_concept_id),
    identifier_system TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source_url TEXT,
    UNIQUE (identifier_system, identifier_value)
);

CREATE TABLE linkage_import_run (
    linkage_import_run_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES dataset_release(release_id),
    artifact_id TEXT NOT NULL REFERENCES source_artifact(artifact_id),
    source_system TEXT NOT NULL CHECK (source_system IN (
        'NHI_IODE_DRUG_ITEMS', 'NHI_INAE3000', 'TFDA_DRUG_PERMIT'
    )),
    dataset_identifier TEXT,
    resource_id TEXT,
    source_modified_at TEXT,
    parser_version TEXT NOT NULL,
    raw_row_count INTEGER NOT NULL CHECK (raw_row_count >= 0),
    distinct_product_count INTEGER NOT NULL CHECK (distinct_product_count >= 0),
    state TEXT NOT NULL CHECK (state IN (
        'staged', 'validated', 'quarantined'
    )),
    counts_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (release_id, artifact_id, parser_version)
);

CREATE TABLE nhi_drug_item_observation (
    observation_id TEXT PRIMARY KEY,
    linkage_import_run_id TEXT NOT NULL
        REFERENCES linkage_import_run(linkage_import_run_id),
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    source_record_sha256 TEXT NOT NULL
        CHECK (
            length(source_record_sha256) = 64
            AND source_record_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    drug_concept_id TEXT REFERENCES drug_concept(drug_concept_id),
    product_resolution_status TEXT NOT NULL CHECK (
        product_resolution_status IN ('unresolved', 'resolved', 'rejected')
    ),
    nhi_drug_code_raw TEXT NOT NULL CHECK (length(trim(nhi_drug_code_raw)) > 0),
    valid_from TEXT,
    valid_to TEXT,
    atc_code_raw TEXT,
    atc_code_normalized TEXT,
    drug_source_url TEXT,
    raw_record_json TEXT NOT NULL,
    CHECK (
        (product_resolution_status = 'resolved' AND drug_concept_id IS NOT NULL)
        OR (product_resolution_status IN ('unresolved', 'rejected')
            AND drug_concept_id IS NULL)
    ),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    UNIQUE (linkage_import_run_id, source_row_number)
);

CREATE TABLE nhi_drug_rule_reference (
    rule_reference_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL
        REFERENCES nhi_drug_item_observation(observation_id),
    reference_order INTEGER NOT NULL CHECK (reference_order >= 1),
    rule_section_raw TEXT,
    rule_source_url TEXT,
    rule_id TEXT REFERENCES rule_identity(rule_id),
    snapshot_id TEXT,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'unresolved_designation', 'rule_resolved', 'snapshot_resolved',
        'rejected'
    )),
    resolution_evidence_json TEXT NOT NULL,
    CHECK (
        (resolution_status = 'unresolved_designation'
            AND rule_id IS NULL AND snapshot_id IS NULL)
        OR (resolution_status = 'rule_resolved'
            AND rule_id IS NOT NULL AND snapshot_id IS NULL)
        OR (resolution_status = 'snapshot_resolved'
            AND rule_id IS NOT NULL AND snapshot_id IS NOT NULL)
        OR (resolution_status = 'rejected'
            AND rule_id IS NULL AND snapshot_id IS NULL)
    ),
    FOREIGN KEY (snapshot_id, rule_id)
        REFERENCES rule_snapshot(snapshot_id, rule_id),
    UNIQUE (observation_id, reference_order)
);

CREATE TABLE rule_drug_link (
    rule_drug_link_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    drug_concept_id TEXT NOT NULL REFERENCES drug_concept(drug_concept_id),
    relation_type TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    source_text TEXT,
    source_start INTEGER,
    source_end INTEGER,
    confidence REAL,
    review_status TEXT NOT NULL,
    UNIQUE (snapshot_id, drug_concept_id, relation_type, source_system, source_record_id)
);

CREATE TABLE drug_atc_link (
    drug_atc_link_id TEXT PRIMARY KEY,
    drug_concept_id TEXT NOT NULL REFERENCES drug_concept(drug_concept_id),
    atc_code TEXT NOT NULL,
    atc_version TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    source_url TEXT,
    source_text TEXT,
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    confidence REAL,
    review_status TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE (drug_concept_id, atc_code, atc_version, source_system, source_record_id)
);

CREATE TABLE indication (
    indication_id TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    qualifiers_json TEXT NOT NULL,
    source_language TEXT NOT NULL DEFAULT 'zh-TW'
);

CREATE TABLE rule_indication_link (
    rule_indication_link_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    indication_id TEXT NOT NULL REFERENCES indication(indication_id),
    relation_type TEXT NOT NULL,
    source_start INTEGER,
    source_end INTEGER,
    review_status TEXT NOT NULL,
    UNIQUE (snapshot_id, indication_id, relation_type, source_start, source_end)
);

CREATE TABLE external_concept_link (
    external_concept_link_id TEXT PRIMARY KEY,
    indication_id TEXT NOT NULL REFERENCES indication(indication_id),
    system TEXT NOT NULL,
    system_version TEXT NOT NULL,
    external_code TEXT,
    external_title TEXT,
    external_uri TEXT,
    relation_type TEXT NOT NULL,
    mapping_method TEXT NOT NULL,
    review_status TEXT NOT NULL,
    permission_basis TEXT,
    publication_status TEXT NOT NULL CHECK (publication_status IN (
        'internal_candidate', 'blocked', 'publishable'
    )),
    CHECK (
        system <> 'ICD11'
        OR publication_status <> 'publishable'
        OR permission_basis IS NOT NULL
    )
);

CREATE TABLE build_run (
    build_run_id TEXT PRIMARY KEY,
    code_version TEXT NOT NULL,
    input_manifest_sha256 TEXT NOT NULL CHECK (length(input_manifest_sha256) = 64),
    config_sha256 TEXT NOT NULL CHECK (length(config_sha256) = 64),
    output_fingerprint TEXT,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    counts_json TEXT NOT NULL
);

CREATE TABLE build_issue (
    build_issue_id TEXT PRIMARY KEY,
    build_run_id TEXT NOT NULL REFERENCES build_run(build_run_id),
    severity TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    message TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    resolution_status TEXT NOT NULL
);

CREATE TABLE search_document (
    document_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rule_identity(rule_id),
    snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(snapshot_id),
    title TEXT,
    designation TEXT,
    drug_names TEXT,
    atc_codes TEXT,
    indication_text TEXT,
    rule_text TEXT NOT NULL
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
CREATE INDEX idx_linkage_import_source ON linkage_import_run(
    source_system, dataset_identifier, resource_id
);
CREATE INDEX idx_nhi_drug_item_code ON nhi_drug_item_observation(
    nhi_drug_code_raw
);
CREATE INDEX idx_nhi_drug_item_atc ON nhi_drug_item_observation(
    atc_code_normalized
);
CREATE INDEX idx_nhi_rule_reference_rule ON nhi_drug_rule_reference(rule_id);
CREATE INDEX idx_indication_normalized ON indication(normalized_text);
CREATE INDEX idx_external_concept_code ON external_concept_link(system, external_code);
CREATE INDEX idx_build_issue_run_severity ON build_issue(build_run_id, severity);
