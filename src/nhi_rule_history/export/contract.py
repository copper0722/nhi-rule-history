"""Allowlisted v1 staging export contract.

The order here is normative. Rows are selected using exactly these columns and
sorted by the complete primary key. Adding a PostgreSQL column therefore cannot
silently add private or semantically unreviewed data to a public release.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    nullable: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]


def _c(name: str, kind: str = "text", nullable: bool = False) -> Column:
    return Column(name=name, kind=kind, nullable=nullable)


TABLES: tuple[Table, ...] = (
    Table(
        "rebuild_run",
        (
            _c("run_id"),
            _c("state"),
            _c("parser_version"),
            _c("loader_version"),
            _c("contract_version"),
            _c("code_hash"),
            _c("input_fingerprint"),
            _c("sealed_fingerprint", nullable=True),
            _c("output_fingerprint", nullable=True),
            _c("accepted_manifest_sha256"),
            _c("expected_counts", "json"),
            _c("verified_counts", "json", True),
            _c("expected_release_count", "integer"),
            _c("expected_block_count", "integer"),
            _c("expected_occurrence_count", "integer"),
            _c("expected_empty_table_cell_block_count", "integer"),
            _c("expected_xml_ph_element_count_total", "integer"),
            _c("expected_xml_ph_emitted_unique_total", "integer"),
            _c("expected_xml_ph_unaccounted_total", "integer"),
            _c("created_at", "timestamp"),
            _c("sealed_at", "timestamp", True),
            _c("failed_at", "timestamp", True),
            _c("failure_code", nullable=True),
            _c("failure_detail", nullable=True),
        ),
        ("run_id",),
    ),
    Table(
        "run_input_file",
        (
            _c("run_id"),
            _c("logical_name"),
            _c("declared_schema"),
            _c("byte_length", "integer"),
            _c("row_count", "integer"),
            _c("content_sha256"),
            _c("relative_locator"),
        ),
        ("run_id", "logical_name"),
    ),
    Table(
        "source_release",
        (
            _c("run_id"),
            _c("release_id"),
            _c("source_order_index", "integer"),
            _c("relative_path"),
            _c("basename"),
            _c("content_sha256"),
            _c("byte_length", "integer"),
            _c("filename_label_raw"),
            _c("filename_id_prefix", nullable=True),
            _c("filename_date_fragments_raw", "json"),
            _c("analysis_chronology", "json"),
            _c("parser_version"),
            _c("block_count", "integer"),
            _c("occurrence_count", "integer"),
            _c("table_count", "integer"),
            _c("row_count_xml", "integer"),
            _c("cell_count_xml", "integer"),
            _c("row_count_logical", "integer"),
            _c("cell_count_logical", "integer"),
            _c("empty_cell_count", "integer"),
            _c("nested_table_count", "integer"),
            _c("empty_table_cell_block_count", "integer"),
            _c("numeric_quantity_rejection_count", "integer"),
            _c("odt_repeat_attrs_present", "boolean"),
            _c("xml_ph_element_count", "integer"),
            _c("xml_ph_nested_count", "integer"),
            _c("xml_ph_emitted_unique", "integer"),
            _c("xml_ph_unaccounted", "integer"),
            _c(
                "source_structural_block_count_before_repeat_expansion",
                "integer",
            ),
            _c("accepted_manifest_sha256"),
            _c("accepted_manifest_match", "boolean"),
            _c("statement"),
            _c("source_row_sha256"),
        ),
        ("run_id", "release_id"),
    ),
    Table(
        "source_artifact",
        (
            _c("run_id"),
            _c("artifact_sha256"),
            _c("relative_locator"),
            _c("basename"),
            _c("byte_length", "integer"),
            _c("media_type"),
            _c("content_sha256"),
            _c("source_row_sha256"),
        ),
        ("run_id", "artifact_sha256"),
    ),
    Table(
        "release_artifact",
        (
            _c("run_id"),
            _c("release_id"),
            _c("artifact_sha256"),
            _c("association_role"),
        ),
        ("run_id", "release_id", "artifact_sha256"),
    ),
    Table(
        "structural_block",
        (
            _c("run_id"),
            _c("block_id"),
            _c("artifact_sha256"),
            _c("relative_path"),
            _c("block_kind"),
            _c("container"),
            _c("element_name"),
            _c("style_name", nullable=True),
            _c("in_table", "boolean"),
            _c("in_index_context", "boolean"),
            _c("xml_element_index", "integer"),
            _c("parser_order", "integer"),
            _c("locator", "json"),
            _c("locator_key"),
            _c("raw_text"),
            _c("normalized_search_text"),
            _c("raw_text_sha256"),
            _c("raw_text_byte_length", "integer"),
            _c("raw_text_char_length", "integer"),
            _c("parser_version"),
            _c("source_row_sha256"),
        ),
        ("run_id", "block_id"),
    ),
    Table(
        "occurrence_candidate",
        (
            _c("run_id"),
            _c("occurrence_id"),
            _c("artifact_sha256"),
            _c("block_id"),
            _c("relative_path"),
            _c("designation_text"),
            _c("match_start_in_raw", "integer"),
            _c("match_end_in_raw", "integer"),
            _c("raw_text_sha256"),
            _c("raw_text_byte_length", "integer"),
            _c("raw_text_char_length", "integer"),
            _c("container"),
            _c("in_index_context", "boolean"),
            _c("ambiguity_flags", "json"),
            _c("parser_version"),
            _c("statement"),
            _c("source_row_sha256"),
        ),
        ("run_id", "occurrence_id"),
    ),
    Table(
        "stage_issue",
        (
            _c("run_id"),
            _c("issue_seq", "integer"),
            _c("issue_code"),
            _c("issue_class"),
            _c("severity"),
            _c("is_blocking", "boolean"),
            _c("relative_path", nullable=True),
            _c("detail"),
            _c("artifact_sha256", nullable=True),
            _c("block_id", nullable=True),
            _c("locator_key", nullable=True),
            _c("attributes", "json"),
            _c("source_row_sha256"),
        ),
        ("run_id", "issue_seq"),
    ),
)

TABLE_BY_NAME = {table.name: table for table in TABLES}
TABLE_NAMES = tuple(table.name for table in TABLES)
EXPORT_CONTRACT_VERSION = "nhi-rule-history-stage-public-export/v1"
SQLITE_SCHEMA_VERSION = "nhi-rule-history-stage-sqlite/v1"
DATASET_KIND = "source_occurrence_staging"
NON_CLAIM = (
    "Bounded source-occurrence staging from 14 historical ODT files; "
    "not a complete legal history and not evidence of legal effective dates."
)
