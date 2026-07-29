-- 2026-07-29 — normalized single-clause legal-document projection.
--
-- This is an additive shadow model.  It separates persistent Work identity,
-- expression-local structure, exact source spans, physical table evidence,
-- logical display values and exact version diffs.  Existing publication and
-- announced-release rows are not rewritten.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-clause-document-v25', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_work (
    clause_work_id uuid PRIMARY KEY,
    canonical_code text NOT NULL UNIQUE CHECK (
      canonical_code ~ '^[1-9][0-9]*(?:[.][0-9]+)+$'
    ),
    authority text NOT NULL CHECK (authority = 'taiwan_nhi'),
    identity_basis text NOT NULL CHECK (
      identity_basis IN (
        'official_designation',
        'project_designation_with_source_receipt'
      )
    ),
    identity_receipt_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_node_work (
    node_work_id uuid PRIMARY KEY,
    clause_work_id uuid NOT NULL
      REFERENCES nhi_rule_history_announced.clause_document_work(
        clause_work_id
      ),
    work_role text NOT NULL CHECK (work_role <> ''),
    creation_basis text NOT NULL CHECK (
      creation_basis IN (
        'clause_root',
        'explicit_source_mapping',
        'reviewed_adjudication'
      )
    ),
    creation_receipt_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    UNIQUE (clause_work_id, work_role)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_normalization_run (
    normalization_run_id uuid PRIMARY KEY,
    source_release_run_id uuid NOT NULL
      REFERENCES nhi_rule_history_announced.release_run(run_id),
    state text NOT NULL CHECK (state IN ('loading', 'sealed')),
    parser_version text NOT NULL CHECK (parser_version <> ''),
    rules_version text NOT NULL CHECK (rules_version <> ''),
    migration_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_input_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL,
    input_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL UNIQUE,
    expected_counts jsonb NOT NULL CHECK (
      jsonb_typeof(expected_counts) = 'object'
    ),
    verified_counts jsonb,
    table_fingerprints jsonb,
    output_fingerprint
      nhi_rule_history_announced.sha256_hex,
    sealed_fingerprint
      nhi_rule_history_announced.sha256_hex,
    started_at timestamptz NOT NULL,
    sealed_at timestamptz,
    CHECK (
      (
        state = 'loading'
        AND verified_counts IS NULL
        AND table_fingerprints IS NULL
        AND output_fingerprint IS NULL
        AND sealed_fingerprint IS NULL
        AND sealed_at IS NULL
      )
      OR
      (
        state = 'sealed'
        AND jsonb_typeof(verified_counts) = 'object'
        AND jsonb_typeof(table_fingerprints) = 'object'
        AND output_fingerprint IS NOT NULL
        AND sealed_fingerprint IS NOT NULL
        AND sealed_at IS NOT NULL
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_expression (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    clause_work_id uuid NOT NULL
      REFERENCES nhi_rule_history_announced.clause_document_work(
        clause_work_id
      ),
    source_lane text NOT NULL CHECK (
      source_lane IN ('current_publication', 'announced_composite')
    ),
    source_run_id uuid NOT NULL,
    source_version_id uuid,
    effective_from date,
    expression_completeness text NOT NULL CHECK (
      expression_completeness IN (
        'source_complete', 'verified_composite', 'patch_only',
        'partial', 'unresolved'
      )
    ),
    reader_state text NOT NULL CHECK (
      reader_state IN (
        'current_effective_complete', 'future_announced_complete',
        'prior_effective', 'conflicted', 'unresolved'
      )
    ),
    exact_text text NOT NULL,
    exact_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    composition_manifest_sha256
      nhi_rule_history_announced.sha256_hex,
    completeness_receipt_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id),
    FOREIGN KEY (normalization_run_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_normalization_run(
          normalization_run_id
        ),
    UNIQUE (
      normalization_run_id, source_lane, source_run_id, source_version_id
    ),
    CHECK (
      exact_text_sha256 =
        encode(sha256(convert_to(exact_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        expression_completeness = 'verified_composite'
        AND composition_manifest_sha256 IS NOT NULL
      )
      OR expression_completeness <> 'verified_composite'
    ),
    CHECK (
      reader_state NOT IN (
        'current_effective_complete', 'future_announced_complete',
        'prior_effective'
      )
      OR expression_completeness IN (
        'source_complete', 'verified_composite'
      )
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS
  clause_document_one_current_expression_idx
ON nhi_rule_history_announced.clause_document_expression (
  normalization_run_id, clause_work_id, reader_state
)
WHERE reader_state IN (
  'current_effective_complete', 'future_announced_complete'
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_expression_relation (
    normalization_run_id uuid NOT NULL,
    relation_id uuid NOT NULL,
    clause_work_id uuid NOT NULL,
    older_expression_id uuid NOT NULL,
    newer_expression_id uuid NOT NULL,
    relation_status text NOT NULL CHECK (
      relation_status IN (
        'direct_predecessor_verified',
        'previous_available_expression_only',
        'unresolved',
        'conflicted'
      )
    ),
    relation_basis text NOT NULL CHECK (
      relation_basis IN (
        'official_amendment_to_current_effective_expression',
        'available_snapshot_order_only',
        'reviewed_adjudication'
      )
    ),
    decision_lane text NOT NULL CHECK (
      decision_lane IN (
        'deterministic_rule', 'human_authorized_replay'
      )
    ),
    evidence_receipt jsonb NOT NULL CHECK (
      jsonb_typeof(evidence_receipt) = 'object'
    ),
    evidence_receipt_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, relation_id),
    FOREIGN KEY (normalization_run_id, older_expression_id)
      REFERENCES nhi_rule_history_announced.clause_document_expression(
        normalization_run_id, expression_id
      ),
    FOREIGN KEY (normalization_run_id, newer_expression_id)
      REFERENCES nhi_rule_history_announced.clause_document_expression(
        normalization_run_id, expression_id
      ),
    FOREIGN KEY (clause_work_id)
      REFERENCES nhi_rule_history_announced.clause_document_work(
        clause_work_id
      ),
    UNIQUE (
      normalization_run_id, older_expression_id, newer_expression_id
    ),
    CHECK (older_expression_id <> newer_expression_id)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_source_block (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    block_order integer NOT NULL CHECK (block_order >= 0),
    source_block_id text NOT NULL CHECK (source_block_id <> ''),
    source_artifact_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_lane text NOT NULL CHECK (
      source_lane IN (
        'source_complete', 'amendment_exact', 'predecessor_inherited'
      )
    ),
    container text NOT NULL CHECK (
      container IN ('flow', 'table_cell')
    ),
    raw_text text NOT NULL,
    raw_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    scalar_length integer NOT NULL CHECK (scalar_length >= 0),
    utf8_byte_length integer NOT NULL CHECK (utf8_byte_length >= 0),
    source_locator jsonb NOT NULL CHECK (
      jsonb_typeof(source_locator) = 'object'
    ),
    render_locator jsonb NOT NULL CHECK (
      jsonb_typeof(render_locator) = 'object'
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id, block_order),
    FOREIGN KEY (normalization_run_id, expression_id)
      REFERENCES nhi_rule_history_announced.clause_document_expression(
        normalization_run_id, expression_id
      ),
    UNIQUE (
      normalization_run_id, expression_id, source_block_id, block_order
    ),
    CHECK (
      raw_text_sha256 =
        encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')
      AND scalar_length = char_length(raw_text)
      AND utf8_byte_length = octet_length(raw_text)
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_node (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    node_id uuid NOT NULL,
    parent_node_id uuid,
    tree_preorder integer NOT NULL CHECK (tree_preorder >= 0),
    sibling_ordinal integer NOT NULL CHECK (sibling_ordinal >= 0),
    hierarchy_depth integer NOT NULL CHECK (hierarchy_depth >= 0),
    akn_element text NOT NULL CHECK (
      akn_element IN (
        'clause', 'hcontainer', 'paragraph', 'point',
        'subparagraph', 'table', 'authorialNote'
      )
    ),
    structural_role text NOT NULL CHECK (structural_role <> ''),
    marker_raw text,
    marker_scheme text,
    item_ordinal integer CHECK (item_ordinal IS NULL OR item_ordinal > 0),
    marker_scalar_end integer CHECK (
      marker_scalar_end IS NULL OR marker_scalar_end >= 0
    ),
    marker_utf8_byte_end integer CHECK (
      marker_utf8_byte_end IS NULL OR marker_utf8_byte_end >= 0
    ),
    exact_text text NOT NULL,
    exact_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    content_text text NOT NULL,
    content_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    structure_status text NOT NULL CHECK (
      structure_status IN (
        'role_derived', 'deterministic_marker', 'plain_paragraph',
        'table_structure', 'unresolved_structure'
      )
    ),
    derived_work_node_key text NOT NULL CHECK (
      derived_work_node_key <> ''
    ),
    node_structure_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id, node_id),
    FOREIGN KEY (normalization_run_id, expression_id)
      REFERENCES nhi_rule_history_announced.clause_document_expression(
        normalization_run_id, expression_id
      ),
    FOREIGN KEY (
      normalization_run_id, expression_id, parent_node_id
    ) REFERENCES nhi_rule_history_announced.clause_document_node(
      normalization_run_id, expression_id, node_id
    ),
    UNIQUE (normalization_run_id, expression_id, tree_preorder),
    UNIQUE (
      normalization_run_id, expression_id, parent_node_id, sibling_ordinal
    ),
    CHECK (
      exact_text_sha256 =
        encode(sha256(convert_to(exact_text, 'UTF8')), 'hex')
      AND content_text_sha256 =
        encode(sha256(convert_to(content_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        akn_element = 'clause'
        AND parent_node_id IS NULL
        AND hierarchy_depth = 0
      )
      OR
      (
        akn_element <> 'clause'
        AND parent_node_id IS NOT NULL
        AND hierarchy_depth > 0
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_node_identity (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    node_id uuid NOT NULL,
    node_work_id uuid,
    identity_resolution_status text NOT NULL CHECK (
      identity_resolution_status IN (
        'unassigned', 'candidate', 'verified',
        'version_local', 'conflicted'
      )
    ),
    identity_basis text NOT NULL CHECK (
      identity_basis IN (
        'explicit_source_mapping', 'exact_predecessor_mapping',
        'marker_path', 'structural_role', 'reviewed_adjudication',
        'none'
      )
    ),
    decision_lane text NOT NULL CHECK (
      decision_lane IN (
        'deterministic_rule', 'human_authorized_replay', 'not_assigned'
      )
    ),
    evidence_receipt_sha256
      nhi_rule_history_announced.sha256_hex,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id, node_id),
    FOREIGN KEY (normalization_run_id, expression_id, node_id)
      REFERENCES nhi_rule_history_announced.clause_document_node(
        normalization_run_id, expression_id, node_id
      ),
    FOREIGN KEY (node_work_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_node_work(node_work_id),
    CHECK (
      (
        identity_resolution_status = 'verified'
        AND node_work_id IS NOT NULL
        AND evidence_receipt_sha256 IS NOT NULL
        AND decision_lane IN (
          'deterministic_rule', 'human_authorized_replay'
        )
      )
      OR identity_resolution_status <> 'verified'
    ),
    CHECK (
      node_work_id IS NOT NULL
      OR identity_resolution_status IN (
        'unassigned', 'candidate', 'version_local', 'conflicted'
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_table (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    node_id uuid NOT NULL,
    table_id uuid NOT NULL,
    table_index integer NOT NULL CHECK (table_index >= 0),
    table_role text NOT NULL CHECK (table_role <> ''),
    renderer_profile text NOT NULL CHECK (renderer_profile <> ''),
    logical_value_policy_version text NOT NULL CHECK (
      logical_value_policy_version <> ''
    ),
    row_count integer NOT NULL CHECK (row_count > 0),
    column_count integer NOT NULL CHECK (column_count > 0),
    header_row_count integer NOT NULL CHECK (
      header_row_count >= 0 AND header_row_count <= row_count
    ),
    table_structure_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id, table_id),
    FOREIGN KEY (normalization_run_id, expression_id, node_id)
      REFERENCES nhi_rule_history_announced.clause_document_node(
        normalization_run_id, expression_id, node_id
      ),
    UNIQUE (normalization_run_id, expression_id, node_id),
    UNIQUE (normalization_run_id, expression_id, table_index)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_table_row (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    table_id uuid NOT NULL,
    row_index integer NOT NULL CHECK (row_index >= 0),
    row_role text NOT NULL CHECK (row_role IN ('header', 'body')),
    row_signature_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    row_structure_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (
      normalization_run_id, expression_id, table_id, row_index
    ),
    FOREIGN KEY (normalization_run_id, expression_id, table_id)
      REFERENCES nhi_rule_history_announced.clause_document_table(
        normalization_run_id, expression_id, table_id
      )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_table_cell (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    table_id uuid NOT NULL,
    row_index integer NOT NULL,
    cell_index integer NOT NULL CHECK (cell_index >= 0),
    physical_state text NOT NULL CHECK (
      physical_state IN (
        'present_text', 'present_empty', 'explicit_covered',
        'source_repeated', 'physically_omitted', 'unresolved'
      )
    ),
    logical_value_state text NOT NULL CHECK (
      logical_value_state IN (
        'own_source_value', 'covered_from_origin',
        'policy_carried_from_origin', 'none', 'unresolved'
      )
    ),
    cell_role text NOT NULL CHECK (
      cell_role IN ('column_header', 'body')
    ),
    row_span integer NOT NULL CHECK (row_span > 0),
    column_span integer NOT NULL CHECK (column_span > 0),
    value_origin_row_index integer,
    value_origin_cell_index integer,
    physical_text text,
    physical_text_sha256
      nhi_rule_history_announced.sha256_hex,
    logical_value_text text,
    logical_value_sha256
      nhi_rule_history_announced.sha256_hex,
    carry_policy_receipt_sha256
      nhi_rule_history_announced.sha256_hex,
    source_content_count integer NOT NULL CHECK (
      source_content_count >= 0
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (
      normalization_run_id, expression_id, table_id,
      row_index, cell_index
    ),
    FOREIGN KEY (
      normalization_run_id, expression_id, table_id, row_index
    ) REFERENCES
      nhi_rule_history_announced.clause_document_table_row(
        normalization_run_id, expression_id, table_id, row_index
      ),
    FOREIGN KEY (
      normalization_run_id, expression_id, table_id,
      value_origin_row_index, value_origin_cell_index
    ) REFERENCES
      nhi_rule_history_announced.clause_document_table_cell(
        normalization_run_id, expression_id, table_id,
        row_index, cell_index
      ),
    CHECK (
      (physical_text IS NULL) = (physical_text_sha256 IS NULL)
      AND (
        physical_text IS NULL
        OR physical_text_sha256 =
          encode(sha256(convert_to(physical_text, 'UTF8')), 'hex')
      )
    ),
    CHECK (
      (logical_value_text IS NULL) = (logical_value_sha256 IS NULL)
      AND (
        logical_value_text IS NULL
        OR logical_value_sha256 =
          encode(sha256(convert_to(logical_value_text, 'UTF8')), 'hex')
      )
    ),
    CHECK (
      (
        logical_value_state = 'own_source_value'
        AND physical_state IN (
          'present_text', 'present_empty', 'source_repeated'
        )
        AND value_origin_row_index IS NULL
        AND value_origin_cell_index IS NULL
        AND physical_text IS NOT NULL
        AND logical_value_text = physical_text
        AND carry_policy_receipt_sha256 IS NULL
      )
      OR
      (
        logical_value_state = 'covered_from_origin'
        AND physical_state = 'explicit_covered'
        AND value_origin_row_index IS NOT NULL
        AND value_origin_cell_index IS NOT NULL
        AND physical_text IS NULL
        AND carry_policy_receipt_sha256 IS NULL
      )
      OR
      (
        logical_value_state = 'policy_carried_from_origin'
        AND physical_state = 'physically_omitted'
        AND value_origin_row_index IS NOT NULL
        AND value_origin_cell_index IS NOT NULL
        AND physical_text IS NULL
        AND carry_policy_receipt_sha256 IS NOT NULL
      )
      OR
      (
        logical_value_state = 'none'
        AND physical_state IN (
          'present_empty', 'physically_omitted'
        )
        AND value_origin_row_index IS NULL
        AND value_origin_cell_index IS NULL
        AND logical_value_text IS NULL
        AND carry_policy_receipt_sha256 IS NULL
      )
      OR
      (
        logical_value_state = 'unresolved'
        AND physical_state = 'unresolved'
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_table_cell_content (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    table_id uuid NOT NULL,
    row_index integer NOT NULL,
    cell_index integer NOT NULL,
    content_order integer NOT NULL CHECK (content_order >= 0),
    structural_kind text NOT NULL CHECK (
      structural_kind IN ('paragraph', 'list_item')
    ),
    marker_raw text,
    marker_scheme text,
    item_ordinal integer CHECK (item_ordinal IS NULL OR item_ordinal > 0),
    exact_text text NOT NULL,
    exact_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    content_text text NOT NULL,
    content_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    structure_status text NOT NULL CHECK (
      structure_status IN (
        'deterministic_marker', 'plain_paragraph',
        'unresolved_structure'
      )
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (
      normalization_run_id, expression_id, table_id,
      row_index, cell_index, content_order
    ),
    FOREIGN KEY (
      normalization_run_id, expression_id, table_id,
      row_index, cell_index
    ) REFERENCES
      nhi_rule_history_announced.clause_document_table_cell(
        normalization_run_id, expression_id, table_id,
        row_index, cell_index
      ),
    CHECK (
      exact_text_sha256 =
        encode(sha256(convert_to(exact_text, 'UTF8')), 'hex')
      AND content_text_sha256 =
        encode(sha256(convert_to(content_text, 'UTF8')), 'hex')
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_source_span (
    normalization_run_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    span_id uuid NOT NULL,
    block_order integer NOT NULL,
    span_order_in_block integer NOT NULL CHECK (
      span_order_in_block >= 0
    ),
    owner_kind text NOT NULL CHECK (
      owner_kind IN ('expression_node', 'table_cell_content')
    ),
    node_id uuid,
    table_id uuid,
    row_index integer,
    cell_index integer,
    content_order integer,
    scalar_start integer NOT NULL CHECK (scalar_start >= 0),
    scalar_end integer NOT NULL CHECK (scalar_end >= scalar_start),
    utf8_byte_start integer NOT NULL CHECK (utf8_byte_start >= 0),
    utf8_byte_end integer NOT NULL CHECK (
      utf8_byte_end >= utf8_byte_start
    ),
    mapping_role text NOT NULL CHECK (
      mapping_role = 'primary_leaf'
    ),
    exact_span_text text NOT NULL,
    exact_span_text_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (normalization_run_id, expression_id, span_id),
    FOREIGN KEY (normalization_run_id, expression_id, block_order)
      REFERENCES
        nhi_rule_history_announced.clause_document_source_block(
          normalization_run_id, expression_id, block_order
        ),
    UNIQUE (
      normalization_run_id, expression_id, block_order,
      span_order_in_block
    ),
    CHECK (
      exact_span_text_sha256 =
        encode(sha256(convert_to(exact_span_text, 'UTF8')), 'hex')
    ),
    CHECK (
      (
        owner_kind = 'expression_node'
        AND node_id IS NOT NULL
        AND table_id IS NULL
        AND row_index IS NULL
        AND cell_index IS NULL
        AND content_order IS NULL
      )
      OR
      (
        owner_kind = 'table_cell_content'
        AND node_id IS NULL
        AND table_id IS NOT NULL
        AND row_index IS NOT NULL
        AND cell_index IS NOT NULL
        AND content_order IS NOT NULL
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_normalization_receipt (
    normalization_run_id uuid PRIMARY KEY,
    source_expression_count integer NOT NULL CHECK (
      source_expression_count > 0
    ),
    expected_counts jsonb NOT NULL CHECK (
      jsonb_typeof(expected_counts) = 'object'
    ),
    table_fingerprints jsonb NOT NULL CHECK (
      jsonb_typeof(table_fingerprints) = 'object'
    ),
    source_reconstruction_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    structure_manifest_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    output_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    FOREIGN KEY (normalization_run_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_normalization_run(
          normalization_run_id
        )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_diff_run (
    diff_run_id uuid PRIMARY KEY,
    normalization_run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_announced.clause_document_normalization_run(
          normalization_run_id
        ),
    relation_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('loading', 'sealed')),
    alignment_version text NOT NULL CHECK (alignment_version <> ''),
    algorithm_version text NOT NULL CHECK (algorithm_version <> ''),
    tokenizer_version text NOT NULL CHECK (tokenizer_version <> ''),
    tie_break_version text NOT NULL CHECK (tie_break_version <> ''),
    unicode_profile text NOT NULL CHECK (unicode_profile <> ''),
    display_policy_version text NOT NULL CHECK (
      display_policy_version <> ''
    ),
    input_fingerprint
      nhi_rule_history_announced.sha256_hex NOT NULL UNIQUE,
    expected_counts jsonb NOT NULL CHECK (
      jsonb_typeof(expected_counts) = 'object'
    ),
    verified_counts jsonb,
    table_fingerprints jsonb,
    output_fingerprint
      nhi_rule_history_announced.sha256_hex,
    sealed_fingerprint
      nhi_rule_history_announced.sha256_hex,
    started_at timestamptz NOT NULL,
    sealed_at timestamptz,
    FOREIGN KEY (normalization_run_id, relation_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_expression_relation(
          normalization_run_id, relation_id
        ),
    CHECK (
      (
        state = 'loading'
        AND verified_counts IS NULL
        AND table_fingerprints IS NULL
        AND output_fingerprint IS NULL
        AND sealed_fingerprint IS NULL
        AND sealed_at IS NULL
      )
      OR
      (
        state = 'sealed'
        AND jsonb_typeof(verified_counts) = 'object'
        AND jsonb_typeof(table_fingerprints) = 'object'
        AND output_fingerprint IS NOT NULL
        AND sealed_fingerprint IS NOT NULL
        AND sealed_at IS NOT NULL
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_node_lineage (
    diff_run_id uuid NOT NULL,
    lineage_id uuid NOT NULL,
    older_expression_id uuid NOT NULL,
    older_node_id uuid,
    newer_expression_id uuid NOT NULL,
    newer_node_id uuid,
    lineage_kind text NOT NULL CHECK (
      lineage_kind IN (
        'continues_as', 'moved_to', 'split_into', 'merged_from',
        'replaced_by', 'number_reused', 'old_only', 'new_only'
      )
    ),
    alignment_status text NOT NULL CHECK (
      alignment_status IN (
        'verified_work_identity', 'exact_unique_signature',
        'bounded_deterministic', 'alignment_unresolved'
      )
    ),
    alignment_basis text NOT NULL CHECK (alignment_basis <> ''),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (diff_run_id, lineage_id),
    FOREIGN KEY (diff_run_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_diff_run(diff_run_id),
    CHECK (
      alignment_status <> 'verified_work_identity'
      OR lineage_kind IN ('continues_as', 'moved_to')
    ),
    CHECK (older_node_id IS NOT NULL OR newer_node_id IS NOT NULL)
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_diff_hunk (
    diff_run_id uuid NOT NULL,
    hunk_id uuid NOT NULL,
    hunk_order integer NOT NULL CHECK (hunk_order >= 0),
    older_expression_id uuid NOT NULL,
    newer_expression_id uuid NOT NULL,
    older_node_id uuid,
    newer_node_id uuid,
    alignment_status text NOT NULL CHECK (
      alignment_status IN (
        'verified_work_identity', 'exact_unique_signature',
        'bounded_deterministic', 'alignment_unresolved'
      )
    ),
    exact_change_kind text NOT NULL CHECK (
      exact_change_kind IN (
        'added', 'removed', 'replaced', 'structure_changed'
      )
    ),
    display_classification text NOT NULL CHECK (
      display_classification IN (
        '本版新增', '本版刪除', '本版改寫',
        '排版差異', '對齊未解'
      )
    ),
    comparison_label text NOT NULL CHECK (
      comparison_label IN ('與上一版本差異', '與舊版本差異')
    ),
    old_exact_text text,
    new_exact_text text,
    old_exact_text_sha256
      nhi_rule_history_announced.sha256_hex,
    new_exact_text_sha256
      nhi_rule_history_announced.sha256_hex,
    table_alignment jsonb CHECK (
      table_alignment IS NULL
      OR jsonb_typeof(table_alignment) = 'object'
    ),
    suppressed_display_segment_count integer NOT NULL CHECK (
      suppressed_display_segment_count >= 0
    ),
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (diff_run_id, hunk_id),
    UNIQUE (diff_run_id, hunk_order),
    FOREIGN KEY (diff_run_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_diff_run(diff_run_id),
    CHECK (
      (old_exact_text IS NULL) = (old_exact_text_sha256 IS NULL)
      AND (
        old_exact_text IS NULL
        OR old_exact_text_sha256 =
          encode(sha256(convert_to(old_exact_text, 'UTF8')), 'hex')
      )
    ),
    CHECK (
      (new_exact_text IS NULL) = (new_exact_text_sha256 IS NULL)
      AND (
        new_exact_text IS NULL
        OR new_exact_text_sha256 =
          encode(sha256(convert_to(new_exact_text, 'UTF8')), 'hex')
      )
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_inline_diff_segment (
    diff_run_id uuid NOT NULL,
    hunk_id uuid NOT NULL,
    segment_order integer NOT NULL CHECK (segment_order >= 0),
    segment_kind text NOT NULL CHECK (
      segment_kind IN ('unchanged', 'deleted', 'inserted', 'replaced')
    ),
    old_text text,
    new_text text,
    old_scalar_start integer,
    old_scalar_end integer,
    new_scalar_start integer,
    new_scalar_end integer,
    old_utf8_byte_start integer,
    old_utf8_byte_end integer,
    new_utf8_byte_start integer,
    new_utf8_byte_end integer,
    display_state text NOT NULL CHECK (
      display_state IN ('normal', 'deemphasized_formatting')
    ),
    display_reason text,
    source_row_sha256
      nhi_rule_history_announced.sha256_hex NOT NULL,
    PRIMARY KEY (diff_run_id, hunk_id, segment_order),
    FOREIGN KEY (diff_run_id, hunk_id)
      REFERENCES
        nhi_rule_history_announced.clause_document_diff_hunk(
          diff_run_id, hunk_id
        ),
    CHECK (
      (old_text IS NULL) = (old_scalar_start IS NULL)
      AND (old_text IS NULL) = (old_scalar_end IS NULL)
      AND (old_text IS NULL) = (old_utf8_byte_start IS NULL)
      AND (old_text IS NULL) = (old_utf8_byte_end IS NULL)
      AND (new_text IS NULL) = (new_scalar_start IS NULL)
      AND (new_text IS NULL) = (new_scalar_end IS NULL)
      AND (new_text IS NULL) = (new_utf8_byte_start IS NULL)
      AND (new_text IS NULL) = (new_utf8_byte_end IS NULL)
    )
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_normalization_control_event (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalization_run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_announced.clause_document_normalization_run(
          normalization_run_id
        ),
    action text NOT NULL CHECK (action IN ('activate', 'deactivate')),
    reason text NOT NULL CHECK (reason <> ''),
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
  );

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_announced.clause_document_diff_control_event (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diff_run_id uuid NOT NULL
      REFERENCES
        nhi_rule_history_announced.clause_document_diff_run(diff_run_id),
    action text NOT NULL CHECK (action IN ('activate', 'deactivate')),
    reason text NOT NULL CHECK (reason <> ''),
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
  );

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_document_identity_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'clause document Work identities are append-only';
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_document_normalization_child()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_state text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'clause document normalization rows are immutable';
  END IF;
  SELECT state INTO parent_state
  FROM
    nhi_rule_history_announced.clause_document_normalization_run
  WHERE normalization_run_id = NEW.normalization_run_id;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION 'normalization child insertion requires a loading run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_document_diff_child()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_state text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'clause document diff rows are immutable';
  END IF;
  SELECT state INTO parent_state
  FROM nhi_rule_history_announced.clause_document_diff_run
  WHERE diff_run_id = NEW.diff_run_id;
  IF parent_state IS DISTINCT FROM 'loading' THEN
    RAISE EXCEPTION 'diff child insertion requires a loading run';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_document_normalization_seal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual_counts jsonb;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed normalization runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'normalization run permits only loading to sealed';
  END IF;
  SELECT jsonb_build_object(
    'clause_document_expression', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_expression row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_expression_relation', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_expression_relation row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_source_block', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_source_block row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_node', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_node row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_node_identity', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_node_identity row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_table', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_table row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_table_row', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_table_row row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_table_cell', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_table_cell row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_table_cell_content', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_table_cell_content row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    ),
    'clause_document_source_span', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_source_span row
      WHERE row.normalization_run_id = OLD.normalization_run_id
    )
  ) INTO actual_counts;
  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'normalization seal counts do not match child rows';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM
      nhi_rule_history_announced.clause_document_normalization_receipt
    WHERE normalization_run_id = OLD.normalization_run_id
      AND expected_counts = actual_counts
  ) THEN
    RAISE EXCEPTION 'normalization receipt is missing or inconsistent';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_expression expression
    LEFT JOIN LATERAL (
      SELECT count(*) FILTER (WHERE node.akn_element = 'clause') AS roots,
             count(*) FILTER (
               WHERE node.structure_status = 'unresolved_structure'
             ) AS unresolved_nodes
      FROM nhi_rule_history_announced.clause_document_node node
      WHERE node.normalization_run_id = expression.normalization_run_id
        AND node.expression_id = expression.expression_id
    ) shape ON true
    WHERE expression.normalization_run_id = OLD.normalization_run_id
      AND (
        shape.roots <> 1
        OR (
          expression.reader_state IN (
            'current_effective_complete', 'future_announced_complete',
            'prior_effective'
          )
          AND shape.unresolved_nodes <> 0
        )
      )
  ) THEN
    RAISE EXCEPTION 'selectable expression tree is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_node child
    JOIN nhi_rule_history_announced.clause_document_node parent
      ON parent.normalization_run_id = child.normalization_run_id
     AND parent.expression_id = child.expression_id
     AND parent.node_id = child.parent_node_id
    WHERE child.normalization_run_id = OLD.normalization_run_id
      AND child.hierarchy_depth <> parent.hierarchy_depth + 1
  ) THEN
    RAISE EXCEPTION 'clause document tree depth is invalid';
  END IF;
  IF EXISTS (
    WITH RECURSIVE walk AS (
      SELECT node.normalization_run_id, node.expression_id,
             node.node_id, node.parent_node_id,
             ARRAY[node.node_id] AS path, false AS cycle
      FROM nhi_rule_history_announced.clause_document_node node
      WHERE node.normalization_run_id = OLD.normalization_run_id
      UNION ALL
      SELECT parent.normalization_run_id, parent.expression_id,
             parent.node_id, parent.parent_node_id,
             walk.path || parent.node_id,
             parent.node_id = ANY(walk.path)
      FROM walk
      JOIN nhi_rule_history_announced.clause_document_node parent
        ON parent.normalization_run_id = walk.normalization_run_id
       AND parent.expression_id = walk.expression_id
       AND parent.node_id = walk.parent_node_id
      WHERE NOT walk.cycle
    )
    SELECT 1 FROM walk WHERE cycle LIMIT 1
  ) THEN
    RAISE EXCEPTION 'clause document tree contains a cycle';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_source_span span
    LEFT JOIN nhi_rule_history_announced.clause_document_source_block block
      ON block.normalization_run_id = span.normalization_run_id
     AND block.expression_id = span.expression_id
     AND block.block_order = span.block_order
    WHERE span.normalization_run_id = OLD.normalization_run_id
      AND (
        span.scalar_end > block.scalar_length
        OR span.utf8_byte_end > block.utf8_byte_length
        OR substring(
          block.raw_text
          FROM span.scalar_start + 1
          FOR span.scalar_end - span.scalar_start
        ) <> span.exact_span_text
        OR (
          span.owner_kind = 'expression_node'
          AND NOT EXISTS (
            SELECT 1
            FROM nhi_rule_history_announced.clause_document_node node
            WHERE node.normalization_run_id = span.normalization_run_id
              AND node.expression_id = span.expression_id
              AND node.node_id = span.node_id
          )
        )
        OR (
          span.owner_kind = 'table_cell_content'
          AND NOT EXISTS (
            SELECT 1
            FROM
              nhi_rule_history_announced.clause_document_table_cell_content c
            WHERE c.normalization_run_id = span.normalization_run_id
              AND c.expression_id = span.expression_id
              AND c.table_id = span.table_id
              AND c.row_index = span.row_index
              AND c.cell_index = span.cell_index
              AND c.content_order = span.content_order
          )
        )
      )
  ) THEN
    RAISE EXCEPTION 'source span ownership or range is invalid';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_source_span a
    JOIN nhi_rule_history_announced.clause_document_source_span b
      ON b.normalization_run_id = a.normalization_run_id
     AND b.expression_id = a.expression_id
     AND b.block_order = a.block_order
     AND b.span_id > a.span_id
     AND int4range(a.scalar_start, a.scalar_end, '[)')
         && int4range(b.scalar_start, b.scalar_end, '[)')
    WHERE a.normalization_run_id = OLD.normalization_run_id
  ) THEN
    RAISE EXCEPTION 'primary source spans overlap';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_source_block block
    LEFT JOIN LATERAL (
      SELECT coalesce(sum(span.scalar_end - span.scalar_start), 0) AS covered,
             min(span.scalar_start) AS first_scalar,
             max(span.scalar_end) AS last_scalar
      FROM nhi_rule_history_announced.clause_document_source_span span
      WHERE span.normalization_run_id = block.normalization_run_id
        AND span.expression_id = block.expression_id
        AND span.block_order = block.block_order
    ) coverage ON true
    WHERE block.normalization_run_id = OLD.normalization_run_id
      AND block.scalar_length > 0
      AND (
        coverage.covered <> block.scalar_length
        OR coverage.first_scalar <> 0
        OR coverage.last_scalar <> block.scalar_length
      )
  ) THEN
    RAISE EXCEPTION 'primary source-span coverage is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_table table_row
    LEFT JOIN LATERAL (
      SELECT count(*) AS row_count
      FROM nhi_rule_history_announced.clause_document_table_row row_data
      WHERE row_data.normalization_run_id = table_row.normalization_run_id
        AND row_data.expression_id = table_row.expression_id
        AND row_data.table_id = table_row.table_id
    ) rows ON true
    LEFT JOIN LATERAL (
      SELECT count(*) AS cell_count
      FROM nhi_rule_history_announced.clause_document_table_cell cell
      WHERE cell.normalization_run_id = table_row.normalization_run_id
        AND cell.expression_id = table_row.expression_id
        AND cell.table_id = table_row.table_id
    ) cells ON true
    WHERE table_row.normalization_run_id = OLD.normalization_run_id
      AND (
        rows.row_count <> table_row.row_count
        OR cells.cell_count
           <> table_row.row_count * table_row.column_count
      )
  ) THEN
    RAISE EXCEPTION 'normalized table is not rectangular';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_table_cell cell
    LEFT JOIN nhi_rule_history_announced.clause_document_table_cell origin
      ON origin.normalization_run_id = cell.normalization_run_id
     AND origin.expression_id = cell.expression_id
     AND origin.table_id = cell.table_id
     AND origin.row_index = cell.value_origin_row_index
     AND origin.cell_index = cell.value_origin_cell_index
    WHERE cell.normalization_run_id = OLD.normalization_run_id
      AND cell.logical_value_state IN (
        'covered_from_origin', 'policy_carried_from_origin'
      )
      AND (
        origin.logical_value_state <> 'own_source_value'
        OR origin.logical_value_text IS DISTINCT FROM cell.logical_value_text
        OR origin.logical_value_sha256 IS DISTINCT FROM cell.logical_value_sha256
        OR (
          cell.logical_value_state = 'covered_from_origin'
          AND NOT (
            cell.row_index
              BETWEEN origin.row_index
                  AND origin.row_index + origin.row_span - 1
            AND cell.cell_index
              BETWEEN origin.cell_index
                  AND origin.cell_index + origin.column_span - 1
          )
        )
      )
  ) THEN
    RAISE EXCEPTION 'table logical-value origin is invalid';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.guard_clause_document_diff_seal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual_counts jsonb;
DECLARE relation_state text;
BEGIN
  IF OLD.state = 'sealed' THEN
    RAISE EXCEPTION 'sealed clause diff runs are immutable';
  END IF;
  IF NOT (OLD.state = 'loading' AND NEW.state = 'sealed') THEN
    RAISE EXCEPTION 'diff run permits only loading to sealed';
  END IF;
  SELECT jsonb_build_object(
    'clause_document_node_lineage', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_node_lineage row
      WHERE row.diff_run_id = OLD.diff_run_id
    ),
    'clause_document_diff_hunk', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_diff_hunk row
      WHERE row.diff_run_id = OLD.diff_run_id
    ),
    'clause_document_inline_diff_segment', (
      SELECT count(*) FROM
        nhi_rule_history_announced.clause_document_inline_diff_segment row
      WHERE row.diff_run_id = OLD.diff_run_id
    )
  ) INTO actual_counts;
  IF actual_counts IS DISTINCT FROM OLD.expected_counts
     OR NEW.verified_counts IS DISTINCT FROM actual_counts THEN
    RAISE EXCEPTION 'diff seal counts do not match child rows';
  END IF;
  SELECT relation.relation_status INTO relation_state
  FROM
    nhi_rule_history_announced.clause_document_expression_relation relation
  WHERE relation.normalization_run_id = OLD.normalization_run_id
    AND relation.relation_id = OLD.relation_id;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_diff_hunk hunk
    WHERE hunk.diff_run_id = OLD.diff_run_id
      AND (
        (
          hunk.comparison_label = '與上一版本差異'
          AND relation_state <> 'direct_predecessor_verified'
        )
        OR
        (
          hunk.comparison_label = '與舊版本差異'
          AND relation_state = 'direct_predecessor_verified'
        )
      )
  ) THEN
    RAISE EXCEPTION 'diff comparison label contradicts relation status';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_diff_hunk hunk
    LEFT JOIN LATERAL (
      SELECT string_agg(coalesce(segment.old_text, ''), ''
                        ORDER BY segment.segment_order) AS old_replay,
             string_agg(coalesce(segment.new_text, ''), ''
                        ORDER BY segment.segment_order) AS new_replay,
             count(*) AS segment_count,
             min(segment.segment_order) AS first_segment,
             max(segment.segment_order) AS last_segment
      FROM
        nhi_rule_history_announced.clause_document_inline_diff_segment segment
      WHERE segment.diff_run_id = hunk.diff_run_id
        AND segment.hunk_id = hunk.hunk_id
    ) replay ON true
    WHERE hunk.diff_run_id = OLD.diff_run_id
      AND (
        replay.segment_count < 1
        OR replay.first_segment <> 0
        OR replay.last_segment <> replay.segment_count - 1
        OR replay.old_replay IS DISTINCT FROM coalesce(hunk.old_exact_text, '')
        OR replay.new_replay IS DISTINCT FROM coalesce(hunk.new_exact_text, '')
      )
  ) THEN
    RAISE EXCEPTION 'inline diff does not reconstruct both exact sides';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS clause_document_normalization_run_seal_guard
ON nhi_rule_history_announced.clause_document_normalization_run;
CREATE TRIGGER clause_document_normalization_run_seal_guard
BEFORE UPDATE ON
  nhi_rule_history_announced.clause_document_normalization_run
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_clause_document_normalization_seal();

DROP TRIGGER IF EXISTS clause_document_diff_run_seal_guard
ON nhi_rule_history_announced.clause_document_diff_run;
CREATE TRIGGER clause_document_diff_run_seal_guard
BEFORE UPDATE ON nhi_rule_history_announced.clause_document_diff_run
FOR EACH ROW EXECUTE FUNCTION
  nhi_rule_history_announced.guard_clause_document_diff_seal();

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'clause_document_work', 'clause_document_node_work'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_mutation_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_mutation_guard BEFORE UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.guard_clause_document_identity_mutation()',
      table_name, table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_announced.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_announced.guard_clause_document_identity_mutation()',
      table_name, table_name
    );
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'clause_document_expression',
    'clause_document_expression_relation',
    'clause_document_source_block',
    'clause_document_node',
    'clause_document_node_identity',
    'clause_document_table',
    'clause_document_table_row',
    'clause_document_table_cell',
    'clause_document_table_cell_content',
    'clause_document_source_span',
    'clause_document_normalization_receipt'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_dml_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_dml_guard BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.'
      'guard_clause_document_normalization_child()',
      table_name, table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_announced.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_announced.reject_mutation()',
      table_name, table_name
    );
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'clause_document_node_lineage',
    'clause_document_diff_hunk',
    'clause_document_inline_diff_segment'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_dml_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_dml_guard BEFORE INSERT OR UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.guard_clause_document_diff_child()',
      table_name, table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_announced.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_announced.reject_mutation()',
      table_name, table_name
    );
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.set_clause_document_normalization_control(
    target_run_id uuid,
    target_action text,
    target_reason text,
    target_receipt jsonb
  )
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE created_event_id bigint;
BEGIN
  IF target_action NOT IN ('activate', 'deactivate') THEN
    RAISE EXCEPTION 'invalid normalization control action';
  END IF;
  IF target_action = 'activate' AND NOT EXISTS (
    SELECT 1
    FROM
      nhi_rule_history_announced.clause_document_normalization_run
    WHERE normalization_run_id = target_run_id
      AND state = 'sealed'
  ) THEN
    RAISE EXCEPTION 'only a sealed normalization run may be activated';
  END IF;
  INSERT INTO
    nhi_rule_history_announced.clause_document_normalization_control_event(
      normalization_run_id, action, reason, receipt
    )
  VALUES (
    target_run_id, target_action, target_reason, target_receipt
  )
  RETURNING event_id INTO created_event_id;
  RETURN created_event_id;
END;
$$;

CREATE OR REPLACE FUNCTION
  nhi_rule_history_announced.set_clause_document_diff_control(
    target_run_id uuid,
    target_action text,
    target_reason text,
    target_receipt jsonb
  )
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE created_event_id bigint;
BEGIN
  IF target_action NOT IN ('activate', 'deactivate') THEN
    RAISE EXCEPTION 'invalid diff control action';
  END IF;
  IF target_action = 'activate' AND NOT EXISTS (
    SELECT 1
    FROM nhi_rule_history_announced.clause_document_diff_run
    WHERE diff_run_id = target_run_id
      AND state = 'sealed'
  ) THEN
    RAISE EXCEPTION 'only a sealed diff run may be activated';
  END IF;
  INSERT INTO
    nhi_rule_history_announced.clause_document_diff_control_event(
      diff_run_id, action, reason, receipt
    )
  VALUES (
    target_run_id, target_action, target_reason, target_receipt
  )
  RETURNING event_id INTO created_event_id;
  RETURN created_event_id;
END;
$$;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'clause_document_normalization_control_event',
    'clause_document_diff_control_event'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_mutation_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_mutation_guard BEFORE UPDATE OR DELETE ON '
      'nhi_rule_history_announced.%I FOR EACH ROW EXECUTE FUNCTION '
      'nhi_rule_history_announced.reject_mutation()',
      table_name, table_name
    );
    EXECUTE format(
      'DROP TRIGGER IF EXISTS %I_truncate_guard ON '
      'nhi_rule_history_announced.%I',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_truncate_guard BEFORE TRUNCATE ON '
      'nhi_rule_history_announced.%I FOR EACH STATEMENT EXECUTE FUNCTION '
      'nhi_rule_history_announced.reject_mutation()',
      table_name, table_name
    );
  END LOOP;
END;
$$;

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_active_clause_document_normalization_run AS
WITH newest AS (
  SELECT event.*,
         row_number() OVER (ORDER BY event.event_id DESC) AS ordinal
  FROM
    nhi_rule_history_announced.clause_document_normalization_control_event
      event
)
SELECT run.*
FROM newest
JOIN nhi_rule_history_announced.clause_document_normalization_run run
  ON run.normalization_run_id = newest.normalization_run_id
JOIN nhi_rule_history_announced.v_active_run release
  ON release.run_id = run.source_release_run_id
WHERE newest.ordinal = 1
  AND newest.action = 'activate'
  AND run.state = 'sealed';

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_active_clause_document_diff_run AS
WITH newest AS (
  SELECT event.*,
         row_number() OVER (ORDER BY event.event_id DESC) AS ordinal
  FROM
    nhi_rule_history_announced.clause_document_diff_control_event event
)
SELECT run.*
FROM newest
JOIN nhi_rule_history_announced.clause_document_diff_run run
  ON run.diff_run_id = newest.diff_run_id
JOIN
  nhi_rule_history_announced.v_active_clause_document_normalization_run norm
  ON norm.normalization_run_id = run.normalization_run_id
WHERE newest.ordinal = 1
  AND newest.action = 'activate'
  AND run.state = 'sealed';

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_public_clause_document_expression AS
SELECT expression.*
FROM nhi_rule_history_announced.clause_document_expression expression
JOIN
  nhi_rule_history_announced.v_active_clause_document_normalization_run run
  ON run.normalization_run_id = expression.normalization_run_id
WHERE expression.expression_completeness IN (
  'source_complete', 'verified_composite'
);

CREATE OR REPLACE VIEW
  nhi_rule_history_announced.v_public_clause_document_diff_hunk AS
SELECT hunk.*
FROM nhi_rule_history_announced.clause_document_diff_hunk hunk
JOIN nhi_rule_history_announced.v_active_clause_document_diff_run run
  ON run.diff_run_id = hunk.diff_run_id;

COMMIT;
