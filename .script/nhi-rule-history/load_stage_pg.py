#!/usr/bin/env python3
"""Validate and (explicitly) load NHI rule-history occurrence stage into PG.

Modes:
  validate (default)  — filesystem/schema/hash/count gates; no DB connection
  apply --apply       — re-validate, insert one sealed run into
                        tw_drug_history_stage only
  drop-run --drop-run-id UUID --expect-fingerprint SHA256 --apply
                      — delete one exact sealed run via FK cascade

Never logs DSNs, credentials, absolute paths, hostnames, or source prose.
psycopg is imported only when --apply is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat as statmod
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import corpus_profile as cp
import occurrence_extract as oe

# ---------------------------------------------------------------------------
# Versions / bound contract for the accepted repaired-v2 corpus
# ---------------------------------------------------------------------------

LOADER_VERSION = "nhi-rule-history-stage-loader/1.0.4"
CONTRACT_VERSION = "nhi-rule-history-stage-contract/v1"
PARSER_VERSION = oe.PARSER_VERSION
MIGRATION_RELATIVE = "pg/migrations/2026-07-26_nhi_rule_history_stage.sql"
ROLLBACK_RELATIVE = "pg/migrations/2026-07-26_nhi_rule_history_stage.rollback.sql"

SCHEMA_RELEASE = oe.SCHEMA_RELEASE
SCHEMA_BLOCK = oe.SCHEMA_BLOCK
SCHEMA_OCCURRENCE = oe.SCHEMA_OCCURRENCE
SCHEMA_SUMMARY = oe.SCHEMA_SUMMARY
SCHEMA_ISSUE = "nhi-rule-history-stage-issue/v1"
SCHEMA_SOURCE_ARTIFACT = "nhi-rule-history-source-artifact/v1"

STAGE_SCHEMA = "tw_drug_history_stage"
DSN_ENV_VAR = "NHI_RULE_HISTORY_DSN"

# Stable global advisory lock name (must match migration + rollback SQL exactly).
# Acquire order everywhere: (1) global, (2) fingerprint lock when applicable.
STAGE_GLOBAL_LOCK_KEY = "tw_drug_history_stage-global"
MANAGED_SCHEMA_COMMENT = (
    "Isolated immutable staging for NHI rule-history occurrence rebuild runs; "
    "not legal history. managed=tw_drug_history_stage/v1"
)

# Bound to accepted repaired-v2 manifest + parser 1.1.0 (not timeless product).
BOUND_PARSER_VERSION = "nhi-rule-history-occurrence-extract/1.1.0"
BOUND_COUNTS = {
    "release_count": 14,
    "block_count": 213512,
    "empty_table_cell_block_count": 79195,
    "occurrence_count": 9303,
    "xml_ph_element_count_total": 134317,
    "xml_ph_emitted_unique_total": 134317,
    "xml_ph_unaccounted_total": 0,
    "blocking_issue_count": 0,
}

INSERT_BATCH_SIZE = 500

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DESIGNATION_RE = re.compile(r"^\d+(?:\.\d+)+$")

# Forbidden semantic columns / keys (legal identity/diff layer).
# Exact keys only — allowlisted summary flags like cross_release_diffs_computed
# (always false) are meta gates, not payload fields.
FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "legal_effective_date",
        "legal_effective_date_present",
        "effective_date",
        "effective_date_iso",
        "stable_rule_id",
        "rule_identity",
        "predecessor",
        "successor",
        "predecessor_id",
        "successor_id",
        "event_effect",
        "lineage",
        "lineage_id",
        "diff",
        "diffs",
        "diff_payload",
        "reader_version",
        "canonical_rule_id",
    }
)

# Absolute-path / credential leakage markers banned from receipts.
_LEAK_MARKERS = (
    "/Users/",
    "/home/",
    "/private/tmp/",
    "/var/folders/",
    "host=",
    "password=",
    "dbname=",
    "postgresql://",
    "postgres://",
    "\\\\",
)

# ---------------------------------------------------------------------------
# Allowlists (fail closed)
# ---------------------------------------------------------------------------

RELEASE_KEYS = oe.TRACKED_RELEASE_KEYS
BLOCK_KEYS = frozenset(
    {
        "schema",
        "block_id",
        "artifact_sha256",
        "relative_path",
        "block_kind",
        "container",
        "element_name",
        "style_name",
        "in_table",
        "in_index_context",
        "xml_element_index",
        "locator",
        "locator_key",
        "raw_text",
        "normalized_search_text",
        "raw_text_sha256",
        "raw_text_byte_length",
        "raw_text_char_length",
        "parser_version",
    }
)
OCCURRENCE_KEYS = oe.TRACKED_OCCURRENCE_KEYS | frozenset(
    {"raw_text", "normalized_search_text"}
)
OCCURRENCE_TRACKED_KEYS = oe.TRACKED_OCCURRENCE_KEYS
ISSUE_KEYS = oe.TRACKED_ISSUE_KEYS
SUMMARY_KEYS = oe.TRACKED_SUMMARY_KEYS
CANARY_KEYS = oe.TRACKED_CANARY_KEYS

LOCATOR_KEYS = frozenset(
    {
        "cell_element",
        "cell_logical_index",
        "cell_xml_index",
        "col_repeat_attr",
        "col_repeat_instance",
        "container",
        "doc_order",
        "element",
        "empty_cell",
        "in_frame",
        "in_index_context",
        "is_header_row",
        "list_depth",
        "nested_table_depth",
        "number_columns_spanned",
        "number_rows_spanned",
        "para_index_in_cell",
        "row_logical_index",
        "row_repeat_attr",
        "row_repeat_instance",
        "row_xml_index",
        "style_name",
        "table_index",
        "xml_element_index",
    }
)

CHRONOLOGY_KEYS = frozenset(
    {
        "analysis_sort_key",
        "legal_date_inferred",
        "parse_status",
        "roc_month",
        "roc_year",
        "statement",
    }
)

AMBIGUITY_FLAGS = frozenset(
    {
        "source_local_candidate_only",
        "duplicate_designation_in_release",
        "in_frame",
        "in_table_cell",
        "leading_whitespace_or_punctuation_stripped",
    }
)

ISSUE_ATTRIBUTE_KEYS = frozenset(
    {
        "designation_text",
        "occurrence_count",
        "occurrence_ids",
        "table_index",
        "nested_table_depth",
        "empty_cell_count",
        "table_count",
        "canary",
        "rejection_code",
        "locator_key",
        "xml_element_index",
        "block_id",
        "first_segment",
        "unit_token",
    }
)

BLOCK_KINDS = frozenset(
    {
        "paragraph",
        "heading",
        "table_paragraph",
        "frame_paragraph",
        "index_paragraph",
        "empty_table_cell",
    }
)
CONTAINERS = frozenset({"flow", "table_cell", "frame", "index", "other"})
# Align with producer (corpus_profile / occurrence_extract): info|warning|error.
SEVERITIES = frozenset({"info", "warning", "error"})
BLOCKING_SEVERITIES = frozenset({"error"})

INPUT_LOGICAL_NAMES = (
    "stage/releases.jsonl",
    "stage/blocks.jsonl",
    "stage/occurrences.jsonl",
    "receipt/release-index.jsonl",
    "receipt/occurrence-index.jsonl",
    "receipt/issues.jsonl",
    "receipt/summary.json",
    "receipt/canary-occurrences.jsonl",
    "receipt/quality-report.md",
    "accepted/manifest.jsonl",
)

# Tables the apply path may INSERT/DELETE against (schema-qualified).
ALLOWED_SQL_RELATIONS = frozenset(
    {
        f"{STAGE_SCHEMA}.rebuild_run",
        f"{STAGE_SCHEMA}.run_input_file",
        f"{STAGE_SCHEMA}.source_release",
        f"{STAGE_SCHEMA}.source_artifact",
        f"{STAGE_SCHEMA}.release_artifact",
        f"{STAGE_SCHEMA}.structural_block",
        f"{STAGE_SCHEMA}.occurrence_candidate",
        f"{STAGE_SCHEMA}.stage_issue",
    }
)


class StageLoadError(Exception):
    """Fail-closed validation or load error (no source prose)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Hash / JSON helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_sha256(obj: Mapping[str, Any]) -> str:
    return sha256_text(stable_json_dumps(obj))


def require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StageLoadError("invalid_sha256", f"{field} is not lowercase 64-hex")
    return value


def assert_no_forbidden_keys(obj: Mapping[str, Any], *, where: str) -> None:
    for key in obj.keys():
        kl = str(key).lower()
        if kl in FORBIDDEN_EXACT_KEYS:
            raise StageLoadError(
                "forbidden_semantic_field",
                f"{where}: forbidden key {key!r}",
            )


def assert_allowlist(
    obj: Mapping[str, Any], allowed: frozenset[str], *, where: str
) -> None:
    unknown = set(obj.keys()) - allowed
    if unknown:
        raise StageLoadError(
            "unknown_field",
            f"{where}: unknown fields {sorted(unknown)}",
        )
    assert_no_forbidden_keys(obj, where=where)


def assert_locator(loc: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(loc, dict):
        raise StageLoadError("invalid_locator", f"{where}: locator must be object")
    assert_allowlist(loc, LOCATOR_KEYS, where=f"{where}.locator")
    for k, v in loc.items():
        if k in {
            "doc_order",
            "xml_element_index",
            "table_index",
            "row_xml_index",
            "row_logical_index",
            "cell_xml_index",
            "cell_logical_index",
            "para_index_in_cell",
            "list_depth",
            "nested_table_depth",
            "in_frame",
            "in_index_context",
            "empty_cell",
            "is_header_row",
            "col_repeat_attr",
            "col_repeat_instance",
            "row_repeat_attr",
            "row_repeat_instance",
            "number_columns_spanned",
            "number_rows_spanned",
        }:
            if not isinstance(v, int):
                raise StageLoadError(
                    "invalid_locator_index",
                    f"{where}.locator.{k} must be int",
                )
            # non-negative except para_index_in_cell may be -1 for empty cells
            if k == "para_index_in_cell":
                if v < -1:
                    raise StageLoadError(
                        "invalid_locator_index",
                        f"{where}.locator.{k} out of range",
                    )
            elif v < 0:
                raise StageLoadError(
                    "invalid_locator_index",
                    f"{where}.locator.{k} must be nonnegative",
                )
    return loc


def assert_chronology(chron: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(chron, dict):
        raise StageLoadError("invalid_chronology", f"{where}: must be object")
    assert_allowlist(chron, CHRONOLOGY_KEYS, where=where)
    if chron.get("legal_date_inferred") is not False:
        raise StageLoadError(
            "legal_date_inferred",
            f"{where}: legal_date_inferred must be false",
        )
    return chron


def stream_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any], bytes]]:
    """Yield (1-based line_no, object, raw_line_bytes) without full-file list."""
    with path.open("rb") as fh:
        for line_no, raw in enumerate(fh, start=1):
            if not raw.endswith(b"\n"):
                raise StageLoadError(
                    "jsonl_missing_trailing_newline",
                    f"{path.name}: line {line_no} missing newline",
                )
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise StageLoadError(
                    "jsonl_not_utf8", f"{path.name}: line {line_no}"
                ) from exc
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise StageLoadError(
                    "jsonl_invalid_json", f"{path.name}: line {line_no}"
                ) from exc
            if not isinstance(obj, dict):
                raise StageLoadError(
                    "jsonl_not_object", f"{path.name}: line {line_no}"
                )
            yield line_no, obj, raw


def file_sha256_and_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def count_jsonl_rows(path: Path) -> int:
    n = 0
    with path.open("rb") as fh:
        for _ in fh:
            n += 1
    return n


def relative_locator(path: Path, *, root_label: str) -> str:
    """Repo-relative logical locator; never absolute."""
    return f"{root_label}/{path.name}"


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _worktree_root() -> Path:
    # .script/nhi-rule-history → worktree root (three parents up).
    return _package_dir().parents[1]


def migration_sql_path() -> Path:
    """Apply migration path relative to the authorized worktree root."""
    return _worktree_root() / MIGRATION_RELATIVE


def code_hash() -> str:
    """Hash of loader + parser sources + apply migration SQL (deterministic).

    Absolute paths are never included — only logical basenames / relative keys.
    """
    parts: list[str] = []
    for name in (
        "load_stage_pg.py",
        "occurrence_extract.py",
        "corpus_profile.py",
    ):
        p = _package_dir() / name
        digest, _ = file_sha256_and_size(p)
        parts.append(f"{name}:{digest}")
    mig = migration_sql_path()
    if not mig.is_file():
        raise StageLoadError("migration_missing", MIGRATION_RELATIVE)
    mig_digest, _ = file_sha256_and_size(mig)
    parts.append(f"{MIGRATION_RELATIVE}:{mig_digest}")
    return sha256_text("\n".join(parts) + "\n")


def advisory_lock_keys(fingerprint: str) -> tuple[int, int]:
    """Derive two int32 keys from a fingerprint for pg_advisory_xact_lock."""
    raw = hashlib.sha256(f"tw_drug_history_stage:{fingerprint}".encode()).digest()
    k1 = int.from_bytes(raw[0:4], "big", signed=True)
    k2 = int.from_bytes(raw[4:8], "big", signed=True)
    return k1, k2


def acquire_stage_locks(cur: Any, fingerprint: str | None = None) -> None:
    """Acquire global stage lock, then optional fingerprint lock (deadlock-safe order)."""
    _cursor_execute(
        cur,
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (STAGE_GLOBAL_LOCK_KEY,),
    )
    if fingerprint is not None:
        k1, k2 = advisory_lock_keys(fingerprint)
        _cursor_execute(cur, "SELECT pg_advisory_xact_lock(%s, %s)", (k1, k2))


def receipt_is_clean(obj: Any) -> None:
    """Fail if receipt JSON would leak paths/DSN/prose-like long CJK."""
    blob = stable_json_dumps(obj)
    for marker in _LEAK_MARKERS:
        if marker in blob:
            raise StageLoadError(
                "receipt_leak",
                f"receipt contains banned marker {marker!r}",
            )
    # No long source-prose blobs in receipts (CJK >= 40 consecutive).
    if re.search(r"[\u4e00-\u9fff]{40,}", blob):
        raise StageLoadError("receipt_prose_leak", "receipt contains long CJK prose")
    if re.search(r"[A-Za-z]{80,}", blob):
        raise StageLoadError(
            "receipt_prose_leak", "receipt contains long Latin prose"
        )


# ---------------------------------------------------------------------------
# Validation core
# ---------------------------------------------------------------------------


def _validate_release_row(obj: Mapping[str, Any], *, where: str) -> dict[str, Any]:
    assert_allowlist(obj, RELEASE_KEYS, where=where)
    if obj.get("schema") != SCHEMA_RELEASE:
        raise StageLoadError("schema_mismatch", f"{where}: bad schema")
    if obj.get("parser_version") != BOUND_PARSER_VERSION:
        raise StageLoadError("parser_version_mismatch", f"{where}: parser_version")
    require_sha256(obj["sha256"], field=f"{where}.sha256")
    require_sha256(obj["release_id"], field=f"{where}.release_id")
    require_sha256(
        obj["accepted_manifest_sha256"], field=f"{where}.accepted_manifest_sha256"
    )
    if obj["release_id"] != obj["sha256"]:
        raise StageLoadError("release_id_mismatch", f"{where}: release_id != sha256")
    if not obj.get("accepted_manifest_match") is True:
        raise StageLoadError(
            "accepted_manifest_match_false", f"{where}: accepted_manifest_match"
        )
    assert_chronology(obj["analysis_chronology"], where=f"{where}.analysis_chronology")
    if not isinstance(obj.get("filename_date_fragments_raw"), list):
        raise StageLoadError(
            "invalid_fragments", f"{where}: filename_date_fragments_raw"
        )
    for key in (
        "source_order_index",
        "byte_length",
        "block_count",
        "occurrence_count",
        "table_count",
        "row_count_xml",
        "cell_count_xml",
        "row_count_logical",
        "cell_count_logical",
        "empty_cell_count",
        "nested_table_count",
        "empty_table_cell_block_count",
        "numeric_quantity_rejection_count",
        "xml_ph_element_count",
        "xml_ph_nested_count",
        "xml_ph_emitted_unique",
        "xml_ph_unaccounted",
        "source_structural_block_count_before_repeat_expansion",
    ):
        if not isinstance(obj.get(key), int) or obj[key] < 0:
            raise StageLoadError("invalid_count", f"{where}.{key}")
    return dict(obj)


def _validate_block_row(obj: Mapping[str, Any], *, where: str) -> dict[str, Any]:
    assert_allowlist(obj, BLOCK_KEYS, where=where)
    if obj.get("schema") != SCHEMA_BLOCK:
        raise StageLoadError("schema_mismatch", f"{where}: bad schema")
    if obj.get("parser_version") != BOUND_PARSER_VERSION:
        raise StageLoadError("parser_version_mismatch", f"{where}: parser_version")
    require_sha256(obj["block_id"], field=f"{where}.block_id")
    require_sha256(obj["artifact_sha256"], field=f"{where}.artifact_sha256")
    require_sha256(obj["raw_text_sha256"], field=f"{where}.raw_text_sha256")
    if obj.get("block_kind") not in BLOCK_KINDS:
        raise StageLoadError("invalid_block_kind", f"{where}: block_kind")
    if obj.get("container") not in CONTAINERS:
        raise StageLoadError("invalid_container", f"{where}: container")
    loc = assert_locator(obj["locator"], where=where)
    locator_key = obj["locator_key"]
    if not isinstance(locator_key, str) or not locator_key:
        raise StageLoadError("invalid_locator_key", f"{where}: locator_key")
    recomputed_key = oe._locator_key(loc)
    if recomputed_key != locator_key:
        raise StageLoadError("locator_key_mismatch", f"{where}: locator_key")
    expected_block_id = oe._block_id(obj["artifact_sha256"], locator_key)
    if expected_block_id != obj["block_id"]:
        raise StageLoadError("block_id_mismatch", f"{where}: block_id")
    raw_text = obj["raw_text"]
    if not isinstance(raw_text, str):
        raise StageLoadError("invalid_raw_text", f"{where}: raw_text type")
    raw_b = raw_text.encode("utf-8")
    if len(raw_b) != obj["raw_text_byte_length"]:
        raise StageLoadError("raw_byte_length_mismatch", f"{where}: byte_length")
    if len(raw_text) != obj["raw_text_char_length"]:
        raise StageLoadError("raw_char_length_mismatch", f"{where}: char_length")
    if sha256_bytes(raw_b) != obj["raw_text_sha256"]:
        raise StageLoadError("raw_text_sha_mismatch", f"{where}: raw_text_sha256")
    if not isinstance(obj["xml_element_index"], int) or obj["xml_element_index"] < 0:
        raise StageLoadError("invalid_xml_element_index", f"{where}")
    if obj["xml_element_index"] != loc.get("xml_element_index"):
        raise StageLoadError(
            "xml_element_index_mismatch",
            f"{where}: top-level vs locator",
        )
    if obj["block_kind"] == "empty_table_cell":
        if raw_text != "" or obj["normalized_search_text"] != "":
            raise StageLoadError("empty_cell_nonempty_text", f"{where}")
        if obj["raw_text_byte_length"] != 0 or obj["raw_text_char_length"] != 0:
            raise StageLoadError("empty_cell_length", f"{where}")
        if obj["raw_text_sha256"] != sha256_bytes(b""):
            raise StageLoadError("empty_cell_hash", f"{where}")
        if not obj.get("in_table") is True or obj.get("container") != "table_cell":
            raise StageLoadError("empty_cell_container", f"{where}")
        if loc.get("empty_cell") != 1:
            raise StageLoadError("empty_cell_flag", f"{where}")
        for span_key in ("number_columns_spanned", "number_rows_spanned"):
            if span_key not in loc:
                raise StageLoadError("empty_cell_missing_span", f"{where}.{span_key}")
            if not isinstance(loc[span_key], int) or loc[span_key] < 1:
                raise StageLoadError("empty_cell_invalid_span", f"{where}.{span_key}")
    return dict(obj)


def _validate_occurrence_row(
    obj: Mapping[str, Any],
    *,
    where: str,
    require_stage_text: bool,
) -> dict[str, Any]:
    allowed = OCCURRENCE_KEYS if require_stage_text else OCCURRENCE_TRACKED_KEYS
    assert_allowlist(obj, allowed, where=where)
    if require_stage_text:
        if "raw_text" not in obj or "normalized_search_text" not in obj:
            raise StageLoadError("occurrence_missing_text", f"{where}")
    if obj.get("schema") != SCHEMA_OCCURRENCE:
        raise StageLoadError("schema_mismatch", f"{where}: bad schema")
    if obj.get("parser_version") != BOUND_PARSER_VERSION:
        raise StageLoadError("parser_version_mismatch", f"{where}: parser_version")
    require_sha256(obj["occurrence_id"], field=f"{where}.occurrence_id")
    require_sha256(obj["artifact_sha256"], field=f"{where}.artifact_sha256")
    require_sha256(obj["block_id"], field=f"{where}.block_id")
    require_sha256(obj["raw_text_sha256"], field=f"{where}.raw_text_sha256")
    if obj.get("container") not in CONTAINERS:
        raise StageLoadError("invalid_container", f"{where}: container")
    loc = assert_locator(obj["locator"], where=where)
    locator_key = obj["locator_key"]
    if oe._locator_key(loc) != locator_key:
        raise StageLoadError("locator_key_mismatch", f"{where}: locator_key")
    desig = obj.get("designation_text")
    if not isinstance(desig, str) or not DESIGNATION_RE.fullmatch(desig):
        raise StageLoadError("invalid_designation", f"{where}: designation_text")
    start = obj.get("match_start_in_raw")
    end = obj.get("match_end_in_raw")
    if not isinstance(start, int) or not isinstance(end, int):
        raise StageLoadError("invalid_offsets", f"{where}: offsets type")
    if start < 0 or end <= start or end > obj["raw_text_char_length"]:
        raise StageLoadError("invalid_offsets", f"{where}: offset range")
    flags = obj.get("ambiguity_flags")
    if not isinstance(flags, list) or not flags:
        raise StageLoadError("invalid_ambiguity_flags", f"{where}")
    for flag in flags:
        if flag not in AMBIGUITY_FLAGS:
            raise StageLoadError(
                "unknown_ambiguity_flag", f"{where}: {flag!r}"
            )
    expected_occ_id = oe._occurrence_id(
        obj["artifact_sha256"], locator_key, obj["raw_text_sha256"]
    )
    if expected_occ_id != obj["occurrence_id"]:
        raise StageLoadError("occurrence_id_mismatch", f"{where}: occurrence_id")
    if require_stage_text:
        raw_text = obj["raw_text"]
        if not isinstance(raw_text, str):
            raise StageLoadError("invalid_raw_text", f"{where}")
        raw_b = raw_text.encode("utf-8")
        if len(raw_b) != obj["raw_text_byte_length"]:
            raise StageLoadError("raw_byte_length_mismatch", f"{where}")
        if len(raw_text) != obj["raw_text_char_length"]:
            raise StageLoadError("raw_char_length_mismatch", f"{where}")
        if sha256_bytes(raw_b) != obj["raw_text_sha256"]:
            raise StageLoadError("raw_text_sha_mismatch", f"{where}")
        if raw_text[start:end] != desig:
            raise StageLoadError(
                "designation_offset_mismatch", f"{where}: slice != designation"
            )
    return dict(obj)


def _validate_issue_row(obj: Mapping[str, Any], *, where: str) -> dict[str, Any]:
    assert_allowlist(obj, ISSUE_KEYS, where=where)
    if obj.get("severity") not in SEVERITIES:
        raise StageLoadError("invalid_severity", f"{where}")
    if not isinstance(obj.get("issue_code"), str) or not obj["issue_code"]:
        raise StageLoadError("invalid_issue_code", f"{where}")
    if not isinstance(obj.get("issue_class"), str) or not obj["issue_class"]:
        raise StageLoadError("invalid_issue_class", f"{where}")
    if not isinstance(obj.get("detail"), str):
        raise StageLoadError("invalid_detail", f"{where}")
    # detail must not embed long prose / absolute paths
    for marker in _LEAK_MARKERS:
        if marker in obj["detail"]:
            raise StageLoadError("issue_detail_leak", f"{where}")
    attrs = {
        k: v
        for k, v in obj.items()
        if k
        not in {
            "issue_code",
            "severity",
            "relative_path",
            "detail",
            "issue_class",
        }
    }
    unknown_attrs = set(attrs) - ISSUE_ATTRIBUTE_KEYS
    if unknown_attrs:
        raise StageLoadError(
            "unknown_issue_attribute",
            f"{where}: {sorted(unknown_attrs)}",
        )
    return dict(obj)


def _validate_summary(obj: Mapping[str, Any]) -> dict[str, Any]:
    assert_allowlist(obj, SUMMARY_KEYS, where="summary")
    if obj.get("schema") != SCHEMA_SUMMARY:
        raise StageLoadError("schema_mismatch", "summary.schema")
    if obj.get("parser_version") != BOUND_PARSER_VERSION:
        raise StageLoadError("parser_version_mismatch", "summary.parser_version")
    for key, expected in (
        ("release_count", BOUND_COUNTS["release_count"]),
        ("block_count", BOUND_COUNTS["block_count"]),
        ("occurrence_count", BOUND_COUNTS["occurrence_count"]),
        (
            "empty_table_cell_block_count_total",
            BOUND_COUNTS["empty_table_cell_block_count"],
        ),
        (
            "xml_ph_element_count_total",
            BOUND_COUNTS["xml_ph_element_count_total"],
        ),
        (
            "xml_ph_emitted_unique_total",
            BOUND_COUNTS["xml_ph_emitted_unique_total"],
        ),
        (
            "xml_ph_unaccounted_total",
            BOUND_COUNTS["xml_ph_unaccounted_total"],
        ),
    ):
        if obj.get(key) != expected:
            raise StageLoadError(
                "bound_count_mismatch",
                f"summary.{key}={obj.get(key)} expected={expected}",
            )
    if obj.get("all_source_sha_match_accepted_manifest") is not True:
        raise StageLoadError("manifest_match_false", "summary")
    if obj.get("legal_dates_inferred") is not False:
        raise StageLoadError("legal_dates_inferred", "summary")
    if obj.get("cross_release_identity_inferred") is not False:
        raise StageLoadError("identity_inferred", "summary")
    if obj.get("cross_release_diffs_computed") is not False:
        raise StageLoadError("diffs_computed", "summary")
    if obj.get("canonical_rule_history_promoted") is not False:
        raise StageLoadError("canonical_promoted", "summary")
    sev = obj.get("issue_severity_counts") or {}
    blocking = 0
    if isinstance(sev, dict):
        for s in BLOCKING_SEVERITIES:
            blocking += int(sev.get(s, 0) or 0)
    if blocking != 0:
        raise StageLoadError("blocking_issues_present", f"count={blocking}")
    return dict(obj)


def load_accepted_manifest(
    path: Path, *, require_bound_gate: bool = True
) -> dict[str, dict[str, Any]]:
    """Map history relative_path -> manifest row (history lane only)."""
    by_path: dict[str, dict[str, Any]] = {}
    for line_no, obj, _raw in stream_jsonl(path):
        rel = obj.get("relative_path")
        if not isinstance(rel, str):
            raise StageLoadError("manifest_bad_path", f"line {line_no}")
        if not rel.startswith("history/"):
            continue
        if rel in by_path:
            raise StageLoadError("manifest_duplicate_path", rel)
        require_sha256(obj.get("sha256"), field=f"manifest[{rel}].sha256")
        by_path[rel] = obj
    if require_bound_gate and len(by_path) != BOUND_COUNTS["release_count"]:
        raise StageLoadError(
            "manifest_history_count",
            f"got={len(by_path)} expected={BOUND_COUNTS['release_count']}",
        )
    if not by_path:
        raise StageLoadError("manifest_history_empty", "no history rows")
    return by_path


def inventory_history_sources(
    history_dir: Path,
    accepted: Mapping[str, Mapping[str, Any]],
    *,
    require_bound_gate: bool = True,
) -> list[dict[str, Any]]:
    """Rehash history ODTs; fail closed on extras/symlinks/mismatches."""
    if not history_dir.is_dir():
        raise StageLoadError("history_dir_missing", "history dir not a directory")
    entries = sorted(history_dir.iterdir(), key=lambda p: p.name.encode("utf-8"))
    artifacts: list[dict[str, Any]] = []
    seen_basenames: set[str] = set()
    accepted_by_base = {Path(k).name: v for k, v in accepted.items()}
    for entry in entries:
        st = entry.lstat()
        if statmod.S_ISLNK(st.st_mode):
            raise StageLoadError("history_symlink", entry.name)
        if not statmod.S_ISREG(st.st_mode):
            raise StageLoadError("history_non_regular", entry.name)
        if not entry.name.endswith(".odt"):
            raise StageLoadError("history_non_odt", entry.name)
        if entry.name in seen_basenames:
            raise StageLoadError("history_duplicate_basename", entry.name)
        seen_basenames.add(entry.name)
        if entry.name not in accepted_by_base:
            raise StageLoadError("history_extra_file", entry.name)
        digest, size = file_sha256_and_size(entry)
        acc = accepted_by_base[entry.name]
        if digest != acc["sha256"]:
            raise StageLoadError("source_sha_mismatch", entry.name)
        if acc.get("byte_length") is not None and size != acc["byte_length"]:
            raise StageLoadError("source_byte_length_mismatch", entry.name)
        rel = f"history/{entry.name}"
        row = {
            "schema": SCHEMA_SOURCE_ARTIFACT,
            "artifact_sha256": digest,
            "content_sha256": digest,
            "relative_locator": rel,
            "basename": entry.name,
            "byte_length": size,
            "media_type": "application/vnd.oasis.opendocument.text",
        }
        row["source_row_sha256"] = row_sha256(row)
        artifacts.append(row)
    missing = set(accepted_by_base) - seen_basenames
    if missing:
        raise StageLoadError(
            "history_missing_file",
            f"count={len(missing)}",
        )
    if require_bound_gate and len(artifacts) != BOUND_COUNTS["release_count"]:
        raise StageLoadError(
            "history_count",
            f"got={len(artifacts)}",
        )
    return artifacts


def _ordered_fingerprint(row_hashes: Sequence[str]) -> str:
    return sha256_text("\n".join(row_hashes) + ("\n" if row_hashes else ""))


def validate_stage_inputs(
    *,
    history_dir: Path,
    stage_dir: Path,
    receipt_dir: Path,
    accepted_manifest: Path,
    require_bound_gate: bool = True,
) -> dict[str, Any]:
    """Full strict validation; returns deterministic receipt payload.

    ``require_bound_gate`` binds the accepted repaired-v2 counts/file digests.
    Unit tests may set it False for synthetic mini corpora.
    """
    for label, path in (
        ("history_dir", history_dir),
        ("stage_dir", stage_dir),
        ("receipt_dir", receipt_dir),
        ("accepted_manifest", accepted_manifest),
    ):
        if not path.exists():
            raise StageLoadError("path_missing", label)

    accepted = load_accepted_manifest(
        accepted_manifest, require_bound_gate=require_bound_gate
    )
    source_artifacts = inventory_history_sources(
        history_dir, accepted, require_bound_gate=require_bound_gate
    )
    source_by_sha = {a["artifact_sha256"]: a for a in source_artifacts}
    source_by_rel = {a["relative_locator"]: a for a in source_artifacts}

    paths = {
        "stage/releases.jsonl": stage_dir / "releases.jsonl",
        "stage/blocks.jsonl": stage_dir / "blocks.jsonl",
        "stage/occurrences.jsonl": stage_dir / "occurrences.jsonl",
        "receipt/release-index.jsonl": receipt_dir / "release-index.jsonl",
        "receipt/occurrence-index.jsonl": receipt_dir / "occurrence-index.jsonl",
        "receipt/issues.jsonl": receipt_dir / "issues.jsonl",
        "receipt/summary.json": receipt_dir / "summary.json",
        "receipt/canary-occurrences.jsonl": receipt_dir / "canary-occurrences.jsonl",
        "receipt/quality-report.md": receipt_dir / "quality-report.md",
        "accepted/manifest.jsonl": accepted_manifest,
    }
    for logical, path in paths.items():
        if not path.is_file():
            raise StageLoadError("input_missing", logical)
        st = path.lstat()
        if statmod.S_ISLNK(st.st_mode):
            raise StageLoadError("input_symlink", logical)

    input_files: list[dict[str, Any]] = []
    for logical in INPUT_LOGICAL_NAMES:
        path = paths[logical]
        digest, size = file_sha256_and_size(path)
        if logical.endswith(".jsonl"):
            rows = count_jsonl_rows(path)
            declared = {
                "stage/releases.jsonl": SCHEMA_RELEASE,
                "stage/blocks.jsonl": SCHEMA_BLOCK,
                "stage/occurrences.jsonl": SCHEMA_OCCURRENCE,
                "receipt/release-index.jsonl": SCHEMA_RELEASE,
                "receipt/occurrence-index.jsonl": SCHEMA_OCCURRENCE,
                "receipt/issues.jsonl": SCHEMA_ISSUE,
                "receipt/canary-occurrences.jsonl": "nhi-rule-history-canary-occurrence/v1",
                "accepted/manifest.jsonl": "nhi-rule-history-corpus-manifest/v1",
            }[logical]
        elif logical.endswith(".json"):
            rows = 1
            declared = SCHEMA_SUMMARY
        else:
            rows = 0
            declared = "text/markdown"
        input_files.append(
            {
                "logical_name": logical,
                "declared_schema": declared,
                "byte_length": size,
                "row_count": rows,
                "content_sha256": digest,
                "relative_locator": logical,
            }
        )

    # Known stage digests for the accepted repaired run (bound gate only).
    expected_file_sha = {
        "stage/blocks.jsonl": "1759061eeeaa19fe80a1ea7234a8ec513f1ee9478e089a333d1f4d730b93d4a1",
        "stage/occurrences.jsonl": "00fb68b18dac7d0be064eb1c4a1f3fd97192c15081deab1f61484f6451be3cf6",
        "receipt/release-index.jsonl": "4086da805539ad77a398e22e1a25e5ba7fb6e385aed82d70ea1e99d4c785680c",
        "receipt/occurrence-index.jsonl": "0e28ae8f16983db937cc3685c3e5247840f8762d5c843acd52fa572c63ddf391",
        "receipt/issues.jsonl": "88297798ed2a618548707c4833456f57d13fbf5cda5ced2ab0c91c6e6d9d5cc9",
        "receipt/summary.json": "7da34bb96e0dedf74dae5cd2f1d41d136ca6556e2a299c51ef77efbae687d934",
        "receipt/canary-occurrences.jsonl": "e399111a5e9aac7dc4ebcdbb8cdf9b08fe1ceeb45c4409a03a3aa779bf73da66",
        "receipt/quality-report.md": "3162f39f783b41d66194ad8f8afab32b3d1b1bd5700213960b428f355f0653e9",
    }
    by_logical = {r["logical_name"]: r for r in input_files}
    if require_bound_gate:
        for logical, expected in expected_file_sha.items():
            if by_logical[logical]["content_sha256"] != expected:
                raise StageLoadError(
                    "input_sha_mismatch",
                    f"{logical} digest does not match accepted repaired-v2",
                )
        if by_logical["stage/releases.jsonl"]["row_count"] != BOUND_COUNTS[
            "release_count"
        ]:
            raise StageLoadError("release_row_count", "releases")
        if by_logical["stage/blocks.jsonl"]["row_count"] != BOUND_COUNTS["block_count"]:
            raise StageLoadError("block_row_count", "blocks")
        if (
            by_logical["stage/occurrences.jsonl"]["row_count"]
            != BOUND_COUNTS["occurrence_count"]
        ):
            raise StageLoadError("occurrence_row_count", "occurrences")
    if by_logical["stage/releases.jsonl"]["content_sha256"] != by_logical[
        "receipt/release-index.jsonl"
    ]["content_sha256"]:
        raise StageLoadError(
            "release_stage_receipt_mismatch",
            "stage/releases vs receipt/release-index",
        )

    summary_obj = json.loads(paths["receipt/summary.json"].read_text(encoding="utf-8"))
    if require_bound_gate:
        _validate_summary(summary_obj)
    else:
        assert_allowlist(summary_obj, SUMMARY_KEYS, where="summary")
        if summary_obj.get("schema") != SCHEMA_SUMMARY:
            raise StageLoadError("schema_mismatch", "summary.schema")
        if summary_obj.get("parser_version") != BOUND_PARSER_VERSION:
            raise StageLoadError("parser_version_mismatch", "summary.parser_version")
    summary_sha = row_sha256(summary_obj)

    # Releases
    release_rows: list[dict[str, Any]] = []
    release_hashes: list[str] = []
    seen_order: set[int] = set()
    seen_release_ids: set[str] = set()
    release_by_sha: dict[str, dict[str, Any]] = {}
    for line_no, obj, _raw in stream_jsonl(paths["stage/releases.jsonl"]):
        row = _validate_release_row(obj, where=f"releases:{line_no}")
        if row["source_order_index"] in seen_order:
            raise StageLoadError("duplicate_source_order", f"line {line_no}")
        seen_order.add(row["source_order_index"])
        if row["sha256"] in seen_release_ids:
            raise StageLoadError("duplicate_release_id", f"line {line_no}")
        seen_release_ids.add(row["sha256"])
        if row["relative_path"] not in source_by_rel:
            raise StageLoadError("release_unknown_source", f"line {line_no}")
        src = source_by_rel[row["relative_path"]]
        if src["artifact_sha256"] != row["sha256"]:
            raise StageLoadError("release_source_sha_mismatch", f"line {line_no}")
        if row["byte_length"] != src["byte_length"]:
            raise StageLoadError("release_byte_length_mismatch", f"line {line_no}")
        if row["xml_ph_unaccounted"] != 0:
            raise StageLoadError("xml_unaccounted_nonzero", f"line {line_no}")
        release_rows.append(row)
        release_hashes.append(row_sha256(row))
        release_by_sha[row["sha256"]] = row
    expected_release_n = (
        BOUND_COUNTS["release_count"] if require_bound_gate else len(source_artifacts)
    )
    if len(release_rows) != expected_release_n:
        raise StageLoadError("release_count", str(len(release_rows)))
    orders = sorted(r["source_order_index"] for r in release_rows)
    if orders != list(range(expected_release_n)):
        raise StageLoadError("source_order_not_contiguous", "releases")

    # Cross-check release-index
    for line_no, obj, _raw in stream_jsonl(paths["receipt/release-index.jsonl"]):
        row = _validate_release_row(obj, where=f"release-index:{line_no}")
        if row_sha256(row) != release_hashes[line_no - 1]:
            raise StageLoadError("release_index_mismatch", f"line {line_no}")

    # Blocks (streamed; keep compact index for occurrence join)
    block_index: dict[str, dict[str, Any]] = {}
    block_hashes: list[str] = []
    empty_cell_count = 0
    ph_unique: dict[str, set[int]] = defaultdict(set)
    ph_total_emit: Counter[str] = Counter()
    block_counts_by_artifact: Counter[str] = Counter()
    seen_block_ids: set[str] = set()
    seen_locator_keys: set[tuple[str, str]] = set()
    for line_no, obj, _raw in stream_jsonl(paths["stage/blocks.jsonl"]):
        row = _validate_block_row(obj, where=f"blocks:{line_no}")
        art = row["artifact_sha256"]
        if art not in source_by_sha:
            raise StageLoadError("block_unknown_artifact", f"line {line_no}")
        if row["block_id"] in seen_block_ids:
            raise StageLoadError("duplicate_block_id", f"line {line_no}")
        seen_block_ids.add(row["block_id"])
        loc_pair = (art, row["locator_key"])
        if loc_pair in seen_locator_keys:
            raise StageLoadError("duplicate_block_locator", f"line {line_no}")
        seen_locator_keys.add(loc_pair)
        if row["block_kind"] == "empty_table_cell":
            empty_cell_count += 1
        else:
            # p/h structural emission tracking
            el = row.get("element_name")
            if el in ("p", "h"):
                ph_unique[art].add(row["xml_element_index"])
                ph_total_emit[art] += 1
        block_counts_by_artifact[art] += 1
        # Compact index for occurrence validation (no prose retained).
        block_index[row["block_id"]] = {
            "artifact_sha256": art,
            "locator_key": row["locator_key"],
            "raw_text_sha256": row["raw_text_sha256"],
            "raw_text_byte_length": row["raw_text_byte_length"],
            "raw_text_char_length": row["raw_text_char_length"],
            "relative_path": row["relative_path"],
            "container": row["container"],
            "xml_element_index": row["xml_element_index"],
            "parser_order": row["locator"]["doc_order"],
            "block_kind": row["block_kind"],
        }
        # Fingerprint includes full row (with text) for sealed integrity.
        block_hashes.append(row_sha256(row))
        if line_no % 50000 == 0:
            # keep memory bounded: block_index is required; no extra lists of text
            pass
    if require_bound_gate and len(block_hashes) != BOUND_COUNTS["block_count"]:
        raise StageLoadError("block_count", str(len(block_hashes)))
    if require_bound_gate and empty_cell_count != BOUND_COUNTS[
        "empty_table_cell_block_count"
    ]:
        raise StageLoadError("empty_cell_count", str(empty_cell_count))
    if summary_obj.get("block_count") != len(block_hashes):
        raise StageLoadError(
            "summary_block_count_mismatch",
            f"{summary_obj.get('block_count')} vs {len(block_hashes)}",
        )
    if summary_obj.get("empty_table_cell_block_count_total") != empty_cell_count:
        raise StageLoadError(
            "summary_empty_cell_mismatch",
            str(empty_cell_count),
        )

    # Per-release block/xml coverage
    for rel_row in release_rows:
        art = rel_row["sha256"]
        if block_counts_by_artifact[art] != rel_row["block_count"]:
            raise StageLoadError(
                "per_release_block_count",
                rel_row["relative_path"],
            )
        if len(ph_unique[art]) != rel_row["xml_ph_emitted_unique"]:
            raise StageLoadError(
                "xml_ph_unique_mismatch",
                rel_row["relative_path"],
            )
        if rel_row["xml_ph_emitted_unique"] != rel_row["xml_ph_element_count"]:
            raise StageLoadError(
                "xml_ph_element_vs_unique",
                rel_row["relative_path"],
            )
        if rel_row["empty_table_cell_block_count"] != rel_row["empty_cell_count"]:
            raise StageLoadError(
                "empty_cell_count_inconsistent",
                rel_row["relative_path"],
            )

    global_ph_unique = sum(len(s) for s in ph_unique.values())
    if require_bound_gate and global_ph_unique != BOUND_COUNTS[
        "xml_ph_emitted_unique_total"
    ]:
        raise StageLoadError("global_xml_ph_unique", str(global_ph_unique))
    xml_ph_element_total = sum(
        rel_row["xml_ph_element_count"] for rel_row in release_rows
    )
    if require_bound_gate and xml_ph_element_total != BOUND_COUNTS[
        "xml_ph_element_count_total"
    ]:
        raise StageLoadError("global_xml_ph_element", "sum mismatch")
    if summary_obj.get("xml_ph_emitted_unique_total") != global_ph_unique:
        raise StageLoadError(
            "summary_xml_unique_mismatch",
            str(global_ph_unique),
        )

    # Occurrences
    occ_hashes: list[str] = []
    occ_ids: set[str] = set()
    occ_counts_by_artifact: Counter[str] = Counter()
    for line_no, obj, _raw in stream_jsonl(paths["stage/occurrences.jsonl"]):
        row = _validate_occurrence_row(
            obj, where=f"occurrences:{line_no}", require_stage_text=True
        )
        if row["occurrence_id"] in occ_ids:
            raise StageLoadError("duplicate_occurrence_id", f"line {line_no}")
        occ_ids.add(row["occurrence_id"])
        blk = block_index.get(row["block_id"])
        if blk is None:
            raise StageLoadError("occurrence_missing_block", f"line {line_no}")
        if blk["artifact_sha256"] != row["artifact_sha256"]:
            raise StageLoadError("occurrence_artifact_mismatch", f"line {line_no}")
        if blk["locator_key"] != row["locator_key"]:
            raise StageLoadError("occurrence_locator_mismatch", f"line {line_no}")
        if blk["raw_text_sha256"] != row["raw_text_sha256"]:
            raise StageLoadError("occurrence_text_sha_mismatch", f"line {line_no}")
        if blk["raw_text_byte_length"] != row["raw_text_byte_length"]:
            raise StageLoadError("occurrence_byte_len_mismatch", f"line {line_no}")
        if blk["raw_text_char_length"] != row["raw_text_char_length"]:
            raise StageLoadError("occurrence_char_len_mismatch", f"line {line_no}")
        if blk["relative_path"] != row["relative_path"]:
            raise StageLoadError("occurrence_path_mismatch", f"line {line_no}")
        if blk["block_kind"] == "empty_table_cell":
            raise StageLoadError("occurrence_on_empty_cell", f"line {line_no}")
        # PG-facing row omits prose/locator (design).
        pg_row = {
            k: row[k]
            for k in (
                "schema",
                "occurrence_id",
                "artifact_sha256",
                "block_id",
                "relative_path",
                "designation_text",
                "match_start_in_raw",
                "match_end_in_raw",
                "raw_text_sha256",
                "raw_text_byte_length",
                "raw_text_char_length",
                "container",
                "in_index_context",
                "ambiguity_flags",
                "parser_version",
                "statement",
            )
            if k in row
        }
        pg_row["source_row_sha256"] = row_sha256(pg_row)
        occ_hashes.append(pg_row["source_row_sha256"])
        occ_counts_by_artifact[row["artifact_sha256"]] += 1
    if require_bound_gate and len(occ_hashes) != BOUND_COUNTS["occurrence_count"]:
        raise StageLoadError("occurrence_count", str(len(occ_hashes)))
    if summary_obj.get("occurrence_count") != len(occ_hashes):
        raise StageLoadError(
            "summary_occurrence_count_mismatch",
            str(len(occ_hashes)),
        )
    for rel_row in release_rows:
        if occ_counts_by_artifact[rel_row["sha256"]] != rel_row["occurrence_count"]:
            raise StageLoadError(
                "per_release_occurrence_count",
                rel_row["relative_path"],
            )

    # occurrence-index (tracked; no prose)
    occ_index_n = 0
    for line_no, obj, _raw in stream_jsonl(paths["receipt/occurrence-index.jsonl"]):
        _validate_occurrence_row(
            obj, where=f"occurrence-index:{line_no}", require_stage_text=False
        )
        occ_index_n = line_no
    if occ_index_n != len(occ_hashes):
        raise StageLoadError(
            "occurrence_index_count_mismatch",
            f"{occ_index_n} vs {len(occ_hashes)}",
        )

    # Issues
    issue_hashes: list[str] = []
    blocking_issues = 0
    for line_no, obj, _raw in stream_jsonl(paths["receipt/issues.jsonl"]):
        row = _validate_issue_row(obj, where=f"issues:{line_no}")
        is_blocking = row["severity"] in BLOCKING_SEVERITIES
        if is_blocking:
            blocking_issues += 1
        core = {
            "issue_seq": line_no - 1,
            "issue_code": row["issue_code"],
            "issue_class": row["issue_class"],
            "severity": row["severity"],
            "is_blocking": is_blocking,
            "relative_path": row.get("relative_path"),
            "detail": row["detail"],
            "attributes": {
                k: row[k]
                for k in ISSUE_ATTRIBUTE_KEYS
                if k in row
            },
        }
        issue_hashes.append(row_sha256(core))
    if blocking_issues != 0:
        raise StageLoadError("blocking_issues_present", str(blocking_issues))
    if len(issue_hashes) != summary_obj.get("issue_count"):
        raise StageLoadError(
            "issue_count_mismatch",
            f"{len(issue_hashes)} vs summary {summary_obj.get('issue_count')}",
        )

    # Canaries (schema only)
    for line_no, obj, _raw in stream_jsonl(paths["receipt/canary-occurrences.jsonl"]):
        assert_allowlist(obj, CANARY_KEYS, where=f"canary:{line_no}")

    # Build PG-facing release/artifact/block fingerprints
    release_pg_hashes: list[str] = []
    for row in release_rows:
        pg = {k: row[k] for k in sorted(RELEASE_KEYS) if k in row}
        pg["source_row_sha256"] = row_sha256(pg)
        release_pg_hashes.append(pg["source_row_sha256"])

    artifact_hashes = [a["source_row_sha256"] for a in source_artifacts]
    # block fingerprints already hashed with full stage rows
    release_fp = _ordered_fingerprint(release_pg_hashes)
    artifact_fp = _ordered_fingerprint(artifact_hashes)
    block_fp = _ordered_fingerprint(block_hashes)
    occ_fp = _ordered_fingerprint(occ_hashes)
    issue_fp = _ordered_fingerprint(issue_hashes)

    input_fp_payload = {
        "contract_version": CONTRACT_VERSION,
        "parser_version": BOUND_PARSER_VERSION,
        "loader_version": LOADER_VERSION,
        "inputs": sorted(
            (
                {
                    "logical_name": r["logical_name"],
                    "content_sha256": r["content_sha256"],
                    "row_count": r["row_count"],
                    "byte_length": r["byte_length"],
                }
                for r in input_files
            ),
            key=lambda x: x["logical_name"],
        ),
        "source_artifacts": sorted(
            (
                {
                    "relative_locator": a["relative_locator"],
                    "content_sha256": a["content_sha256"],
                    "byte_length": a["byte_length"],
                }
                for a in source_artifacts
            ),
            key=lambda x: x["relative_locator"],
        ),
    }
    input_fingerprint = sha256_text(stable_json_dumps(input_fp_payload))

    table_fingerprints = {
        "source_release": release_fp,
        "source_artifact": artifact_fp,
        "structural_block": block_fp,
        "occurrence_candidate": occ_fp,
        "stage_issue": issue_fp,
        "summary": summary_sha,
    }
    # Multiset fingerprints retained for sealed-data verification (already_loaded
    # / post-commit); stream order is not a DB column for large tables.
    multiset_table_fingerprints = {
        "structural_block": _multiset_fingerprint(block_hashes),
        "occurrence_candidate": _multiset_fingerprint(occ_hashes),
    }
    counts = {
        "release_count": len(release_rows),
        "source_artifact_count": len(source_artifacts),
        "block_count": len(block_hashes),
        "empty_table_cell_block_count": empty_cell_count,
        "occurrence_count": len(occ_hashes),
        "issue_count": len(issue_hashes),
        "blocking_issue_count": blocking_issues,
        "xml_ph_element_count_total": xml_ph_element_total,
        "xml_ph_emitted_unique_total": global_ph_unique,
        "xml_ph_unaccounted_total": 0,
    }
    bound_for_receipt = BOUND_COUNTS if require_bound_gate else dict(counts)
    sealed_payload = {
        "contract_version": CONTRACT_VERSION,
        "parser_version": BOUND_PARSER_VERSION,
        "loader_version": LOADER_VERSION,
        "code_hash": code_hash(),
        "input_fingerprint": input_fingerprint,
        "table_fingerprints": table_fingerprints,
        "counts": counts,
        "bound_counts": bound_for_receipt,
    }
    sealed_fingerprint = sha256_text(stable_json_dumps(sealed_payload))
    output_fingerprint = sealed_fingerprint

    accepted_manifest_sha = by_logical["accepted/manifest.jsonl"]["content_sha256"]

    receipt = {
        "schema": "nhi-rule-history-stage-load-receipt/v1",
        "mode": "validate",
        "status": "validated",
        "contract_version": CONTRACT_VERSION,
        "parser_version": BOUND_PARSER_VERSION,
        "loader_version": LOADER_VERSION,
        "code_hash": sealed_payload["code_hash"],
        "input_fingerprint": input_fingerprint,
        "sealed_fingerprint": sealed_fingerprint,
        "output_fingerprint": output_fingerprint,
        "run_fingerprint": sealed_fingerprint,
        "accepted_manifest_sha256": accepted_manifest_sha,
        "counts": counts,
        "bound_counts": bound_for_receipt,
        "table_fingerprints": table_fingerprints,
        "input_files": [
            {
                "logical_name": r["logical_name"],
                "declared_schema": r["declared_schema"],
                "byte_length": r["byte_length"],
                "row_count": r["row_count"],
                "content_sha256": r["content_sha256"],
                "relative_locator": r["relative_locator"],
            }
            for r in input_files
        ],
        "source_artifacts": [
            {
                "relative_locator": a["relative_locator"],
                "basename": a["basename"],
                "byte_length": a["byte_length"],
                "content_sha256": a["content_sha256"],
                "media_type": a["media_type"],
            }
            for a in source_artifacts
        ],
        "releases": [
            {
                "source_order_index": r["source_order_index"],
                "relative_path": r["relative_path"],
                "content_sha256": r["sha256"],
                "block_count": r["block_count"],
                "occurrence_count": r["occurrence_count"],
                "empty_table_cell_block_count": r["empty_table_cell_block_count"],
                "xml_ph_emitted_unique": r["xml_ph_emitted_unique"],
                "xml_ph_unaccounted": r["xml_ph_unaccounted"],
            }
            for r in release_rows
        ],
    }
    receipt_is_clean(receipt)

    # Material for apply (not written to receipt to keep it small/prose-free)
    material = {
        "receipt": receipt,
        "input_files": input_files,
        "source_artifacts": source_artifacts,
        "release_rows": release_rows,
        "paths": paths,
        "block_index": block_index,
        "sealed_fingerprint": sealed_fingerprint,
        "input_fingerprint": input_fingerprint,
        "accepted_manifest_sha256": accepted_manifest_sha,
        "counts": counts,
        "table_fingerprints": table_fingerprints,
        "multiset_table_fingerprints": multiset_table_fingerprints,
        "code_hash": sealed_payload["code_hash"],
    }
    return material


# ---------------------------------------------------------------------------
# Apply / drop-run (psycopg only when --apply)
# ---------------------------------------------------------------------------


def _import_psycopg():
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise StageLoadError(
            "psycopg_missing",
            "psycopg is required only for --apply modes",
        ) from exc
    return psycopg


def _assert_sql_relation(qualified: str) -> str:
    if qualified not in ALLOWED_SQL_RELATIONS:
        raise StageLoadError("sql_relation_forbidden", qualified)
    return qualified


def _batched(rows: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _executemany(cur: Any, sql: str, params_seq: Sequence[Sequence[Any]]) -> None:
    """Batch insert via cursor.

    psycopg3 exposes executemany on Cursor, not Connection. Callers must pass a
    cursor (or cursor-like) object.
    """
    cur.executemany(sql, params_seq)


def _input_file_meta(
    input_files: Sequence[Mapping[str, Any]], logical_name: str
) -> Mapping[str, Any]:
    for row in input_files:
        if row["logical_name"] == logical_name:
            return row
    raise StageLoadError("input_meta_missing", logical_name)


def _stream_jsonl_hashed(
    path: Path,
    *,
    logical_name: str,
    expected: Mapping[str, Any],
) -> Iterator[tuple[int, dict[str, Any], bytes]]:
    """Stream JSONL while hashing the exact bytes consumed.

    After the iterator is exhausted, content_sha256 / row_count / byte_length
    must match the accepted run_input_file metadata or StageLoadError is raised.
    """
    hasher = hashlib.sha256()
    size = 0
    rows = 0
    for line_no, obj, raw in stream_jsonl(path):
        hasher.update(raw)
        size += len(raw)
        rows += 1
        yield line_no, obj, raw
    digest = hasher.hexdigest()
    if digest != expected["content_sha256"]:
        raise StageLoadError(
            "apply_input_sha_mismatch",
            logical_name,
        )
    if rows != expected["row_count"]:
        raise StageLoadError(
            "apply_input_row_count_mismatch",
            f"{logical_name}: got={rows} expected={expected['row_count']}",
        )
    if size != expected["byte_length"]:
        raise StageLoadError(
            "apply_input_byte_length_mismatch",
            f"{logical_name}: got={size} expected={expected['byte_length']}",
        )


def _cursor_execute(cur: Any, sql: str, params: Sequence[Any] | None = None) -> Any:
    if params is None:
        return cur.execute(sql)
    return cur.execute(sql, params)


def _cursor_fetchone(result_or_cur: Any) -> Any:
    if hasattr(result_or_cur, "fetchone"):
        return result_or_cur.fetchone()
    return result_or_cur


def _cursor_fetchall(result_or_cur: Any) -> list[Any]:
    if hasattr(result_or_cur, "fetchall"):
        return list(result_or_cur.fetchall())
    return list(result_or_cur)


def _rowcount(result_or_cur: Any) -> int:
    if hasattr(result_or_cur, "rowcount"):
        return int(result_or_cur.rowcount)
    raise StageLoadError("rowcount_unavailable", "seal update")


def _multiset_fingerprint(row_hashes: Sequence[str]) -> str:
    return _ordered_fingerprint(sorted(row_hashes))


def verify_sealed_run(
    cur: Any,
    *,
    run_id: uuid.UUID,
    material: Mapping[str, Any],
    expected_counts: Mapping[str, Any],
    stream_table_fingerprints: Mapping[str, str],
    multiset_table_fingerprints: Mapping[str, str],
    error_prefix: str = "post_commit",
) -> dict[str, Any]:
    """Read-only sealed-run verification; fail closed on any mismatch.

    Used by post-commit verification and the already_loaded path. Never returns
    success for non-sealed, incomplete, or fingerprint-mismatched data.
    """
    sealed_fp = material["sealed_fingerprint"]
    counts = material["counts"]
    res = _cursor_execute(
        cur,
        f"""
        SELECT state, sealed_fingerprint, input_fingerprint, code_hash,
               output_fingerprint, verified_counts,
               expected_release_count, expected_block_count,
               expected_occurrence_count
        FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')}
        WHERE run_id = %s
        """,
        (run_id,),
    )
    row = _cursor_fetchone(res)
    if row is None:
        raise StageLoadError(f"{error_prefix}_run_missing", str(run_id))
    (
        state,
        sealed_db,
        input_db,
        code_db,
        output_db,
        _verified_db,
        exp_rel,
        exp_blk,
        exp_occ,
    ) = row
    if state != "sealed":
        raise StageLoadError(f"{error_prefix}_state", str(state))
    if sealed_db != sealed_fp or output_db != sealed_fp:
        raise StageLoadError(f"{error_prefix}_sealed_fp", "mismatch")
    if input_db != material["input_fingerprint"]:
        raise StageLoadError(f"{error_prefix}_input_fp", "mismatch")
    if code_db != material["code_hash"]:
        raise StageLoadError(f"{error_prefix}_code_hash", "mismatch")
    if exp_rel != counts["release_count"] or exp_blk != counts["block_count"]:
        raise StageLoadError(f"{error_prefix}_expected_counts", "mismatch")
    if exp_occ != counts["occurrence_count"]:
        raise StageLoadError(f"{error_prefix}_expected_counts", "occ")

    db_counts: dict[str, int] = {}
    for table, col in (
        ("source_release", "release_count"),
        ("source_artifact", "source_artifact_count"),
        ("structural_block", "block_count"),
        ("occurrence_candidate", "occurrence_count"),
        ("stage_issue", "issue_count"),
    ):
        r = _cursor_execute(
            cur,
            f"SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.{table}')} WHERE run_id = %s",
            (run_id,),
        )
        db_counts[col] = _cursor_fetchone(r)[0]
    r = _cursor_execute(
        cur,
        f"""
        SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.structural_block')}
        WHERE run_id = %s AND block_kind = 'empty_table_cell'
        """,
        (run_id,),
    )
    db_counts["empty_table_cell_block_count"] = _cursor_fetchone(r)[0]
    r = _cursor_execute(
        cur,
        f"""
        SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.stage_issue')}
        WHERE run_id = %s AND is_blocking IS TRUE
        """,
        (run_id,),
    )
    db_counts["blocking_issue_count"] = _cursor_fetchone(r)[0]

    for key, expected in expected_counts.items():
        if db_counts.get(key) != expected or counts.get(key) != expected:
            raise StageLoadError(
                f"{error_prefix}_count_mismatch",
                key,
            )

    # Ordered fingerprints for tables with stable natural order.
    ordered_checks = {
        "source_release": (
            f"SELECT source_row_sha256 FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.source_release')} "
            f"WHERE run_id = %s ORDER BY source_order_index",
            "source_release",
        ),
        "source_artifact": (
            f"SELECT source_row_sha256 FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.source_artifact')} "
            f"WHERE run_id = %s ORDER BY relative_locator",
            "source_artifact",
        ),
        "stage_issue": (
            f"SELECT source_row_sha256 FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.stage_issue')} "
            f"WHERE run_id = %s ORDER BY issue_seq",
            "stage_issue",
        ),
    }
    db_table_fps: dict[str, str] = {}
    for name, (sql, fp_key) in ordered_checks.items():
        r = _cursor_execute(cur, sql, (run_id,))
        hashes = [x[0] for x in _cursor_fetchall(r)]
        fp = _ordered_fingerprint(hashes)
        db_table_fps[name] = fp
        if fp != stream_table_fingerprints[fp_key]:
            raise StageLoadError(
                f"{error_prefix}_ordered_fp_mismatch",
                name,
            )
        if fp != material["table_fingerprints"][fp_key]:
            raise StageLoadError(
                f"{error_prefix}_material_fp_mismatch",
                name,
            )

    # Multiset fingerprints for stream-ordered large tables.
    for table, fp_key in (
        ("structural_block", "structural_block"),
        ("occurrence_candidate", "occurrence_candidate"),
    ):
        r = _cursor_execute(
            cur,
            f"SELECT source_row_sha256 FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.{table}')} WHERE run_id = %s",
            (run_id,),
        )
        hashes = [x[0] for x in _cursor_fetchall(r)]
        ms = _multiset_fingerprint(hashes)
        db_table_fps[f"{fp_key}_multiset"] = ms
        if ms != multiset_table_fingerprints[fp_key]:
            raise StageLoadError(
                f"{error_prefix}_multiset_fp_mismatch",
                fp_key,
            )
        if len(hashes) != counts[
            "block_count" if fp_key == "structural_block" else "occurrence_count"
        ]:
            raise StageLoadError(f"{error_prefix}_row_fp_count", fp_key)

    return {
        "state": "sealed",
        "sealed_fingerprint": sealed_fp,
        "counts": dict(db_counts),
        "table_fingerprints_checked": sorted(db_table_fps.keys()),
    }


def post_commit_verify(
    *,
    psycopg_mod: Any,
    dsn: str,
    run_id: uuid.UUID,
    material: Mapping[str, Any],
    verified_counts: Mapping[str, Any],
    stream_table_fingerprints: Mapping[str, str],
    multiset_table_fingerprints: Mapping[str, str],
) -> dict[str, Any]:
    """Fresh read connection after commit; fail closed on any mismatch."""
    with psycopg_mod.connect(dsn) as conn:
        with conn.cursor() as cur:
            return verify_sealed_run(
                cur,
                run_id=run_id,
                material=material,
                expected_counts=verified_counts,
                stream_table_fingerprints=stream_table_fingerprints,
                multiset_table_fingerprints=multiset_table_fingerprints,
                error_prefix="post_commit",
            )


def apply_stage(
    material: Mapping[str, Any],
    *,
    dsn: str,
) -> dict[str, Any]:
    psycopg = _import_psycopg()
    sealed_fp = material["sealed_fingerprint"]
    run_id = uuid.uuid4()
    receipt = dict(material["receipt"])
    receipt["mode"] = "apply"

    paths = material["paths"]
    release_rows = material["release_rows"]
    source_artifacts = material["source_artifacts"]
    input_files = material["input_files"]
    counts = material["counts"]
    expected_multiset_fps = dict(material["multiset_table_fingerprints"])
    expected_stream_fps = dict(material["table_fingerprints"])

    stream_table_fingerprints: dict[str, str] = {}
    multiset_table_fingerprints: dict[str, str] = {}
    verified: dict[str, int] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Lock order: global, then fingerprint (must match drop-run).
            acquire_stage_locks(cur, sealed_fp)
            existing_res = _cursor_execute(
                cur,
                f"""
                SELECT run_id, state, sealed_fingerprint, input_fingerprint,
                       code_hash, output_fingerprint
                FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')}
                WHERE sealed_fingerprint = %s
                """,
                (sealed_fp,),
            )
            existing = _cursor_fetchone(existing_res)
            if existing is not None:
                existing_run_id = existing[0]
                existing_state = existing[1]
                existing_sealed = existing[2]
                existing_input = existing[3]
                existing_code = existing[4]
                existing_output = existing[5]
                # Fail closed: never return already_loaded for non-sealed data.
                if existing_state != "sealed":
                    raise StageLoadError(
                        "already_loaded_not_sealed",
                        str(existing_state),
                    )
                if (
                    existing_sealed != sealed_fp
                    or existing_output != sealed_fp
                    or existing_input != material["input_fingerprint"]
                    or existing_code != material["code_hash"]
                ):
                    raise StageLoadError(
                        "already_loaded_fingerprint_mismatch",
                        "run/input/code/output/sealed fingerprints disagree",
                    )
                stored_res = _cursor_execute(
                    cur,
                    f"""
                    SELECT logical_name, content_sha256, row_count
                    FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.run_input_file')}
                    WHERE run_id = %s
                    ORDER BY logical_name
                    """,
                    (existing_run_id,),
                )
                stored_inputs = _cursor_fetchall(stored_res)
                expected = {
                    (r["logical_name"], r["content_sha256"], r["row_count"])
                    for r in input_files
                }
                got = {(r[0], r[1], r[2]) for r in stored_inputs}
                if expected != got:
                    raise StageLoadError(
                        "already_loaded_input_mismatch",
                        "sealed fingerprint exists but inputs differ",
                    )
                # Full sealed-data verification (counts + ordered/multiset fps).
                already_expected_counts = {
                    k: counts[k]
                    for k in (
                        "release_count",
                        "source_artifact_count",
                        "block_count",
                        "occurrence_count",
                        "issue_count",
                        "empty_table_cell_block_count",
                        "blocking_issue_count",
                    )
                }
                verify_sealed_run(
                    cur,
                    run_id=existing_run_id
                    if isinstance(existing_run_id, uuid.UUID)
                    else uuid.UUID(str(existing_run_id)),
                    material=material,
                    expected_counts=already_expected_counts,
                    stream_table_fingerprints=expected_stream_fps,
                    multiset_table_fingerprints=expected_multiset_fps,
                    error_prefix="already_loaded",
                )
                receipt["status"] = "already_loaded"
                receipt["run_id"] = str(existing_run_id)
                receipt_is_clean(receipt)
                return receipt

            _cursor_execute(
                cur,
                f"""
                INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')} (
                  run_id, state, parser_version, loader_version, contract_version,
                  code_hash, input_fingerprint, accepted_manifest_sha256,
                  expected_counts,
                  expected_release_count, expected_block_count,
                  expected_occurrence_count, expected_empty_table_cell_block_count,
                  expected_xml_ph_element_count_total,
                  expected_xml_ph_emitted_unique_total,
                  expected_xml_ph_unaccounted_total
                ) VALUES (
                  %s, 'loading', %s, %s, %s,
                  %s, %s, %s,
                  %s::jsonb,
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    run_id,
                    BOUND_PARSER_VERSION,
                    LOADER_VERSION,
                    CONTRACT_VERSION,
                    material["code_hash"],
                    material["input_fingerprint"],
                    material["accepted_manifest_sha256"],
                    stable_json_dumps(counts),
                    counts["release_count"],
                    counts["block_count"],
                    counts["occurrence_count"],
                    counts["empty_table_cell_block_count"],
                    counts["xml_ph_element_count_total"],
                    counts["xml_ph_emitted_unique_total"],
                    counts["xml_ph_unaccounted_total"],
                ),
            )

            for batch in _batched(input_files, INSERT_BATCH_SIZE):
                args = [
                    (
                        run_id,
                        r["logical_name"],
                        r["declared_schema"],
                        r["byte_length"],
                        r["row_count"],
                        r["content_sha256"],
                        r["relative_locator"],
                    )
                    for r in batch
                ]
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.run_input_file')} (
                      run_id, logical_name, declared_schema, byte_length, row_count,
                      content_sha256, relative_locator
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    args,
                )

            for batch in _batched(source_artifacts, INSERT_BATCH_SIZE):
                args = [
                    (
                        run_id,
                        a["artifact_sha256"],
                        a["relative_locator"],
                        a["basename"],
                        a["byte_length"],
                        a["media_type"],
                        a["content_sha256"],
                        a["source_row_sha256"],
                    )
                    for a in batch
                ]
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.source_artifact')} (
                      run_id, artifact_sha256, relative_locator, basename, byte_length,
                      media_type, content_sha256, source_row_sha256
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    args,
                )
            art_hashes = [a["source_row_sha256"] for a in source_artifacts]
            stream_table_fingerprints["source_artifact"] = _ordered_fingerprint(
                art_hashes
            )
            if (
                stream_table_fingerprints["source_artifact"]
                != material["table_fingerprints"]["source_artifact"]
            ):
                raise StageLoadError("apply_artifact_fp_mismatch", "source_artifact")

            release_hashes: list[str] = []
            for batch in _batched(release_rows, INSERT_BATCH_SIZE):
                args = []
                rel_art = []
                for r in batch:
                    pg = {k: r[k] for k in sorted(RELEASE_KEYS) if k in r}
                    rel_row_sha = row_sha256(pg)
                    release_hashes.append(rel_row_sha)
                    args.append(
                        (
                            run_id,
                            r["release_id"],
                            r["source_order_index"],
                            r["relative_path"],
                            r["basename"],
                            r["sha256"],
                            r["byte_length"],
                            r["filename_label_raw"],
                            r.get("filename_id_prefix"),
                            stable_json_dumps(r["filename_date_fragments_raw"]),
                            stable_json_dumps(r["analysis_chronology"]),
                            r["parser_version"],
                            r["block_count"],
                            r["occurrence_count"],
                            r["table_count"],
                            r["row_count_xml"],
                            r["cell_count_xml"],
                            r["row_count_logical"],
                            r["cell_count_logical"],
                            r["empty_cell_count"],
                            r["nested_table_count"],
                            r["empty_table_cell_block_count"],
                            r["numeric_quantity_rejection_count"],
                            r["odt_repeat_attrs_present"],
                            r["xml_ph_element_count"],
                            r["xml_ph_nested_count"],
                            r["xml_ph_emitted_unique"],
                            r["xml_ph_unaccounted"],
                            r["source_structural_block_count_before_repeat_expansion"],
                            r["accepted_manifest_sha256"],
                            r["accepted_manifest_match"],
                            r["statement"],
                            rel_row_sha,
                        )
                    )
                    rel_art.append(
                        (run_id, r["release_id"], r["sha256"], "primary_parse_source")
                    )
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.source_release')} (
                      run_id, release_id, source_order_index, relative_path, basename,
                      content_sha256, byte_length, filename_label_raw, filename_id_prefix,
                      filename_date_fragments_raw, analysis_chronology, parser_version,
                      block_count, occurrence_count, table_count, row_count_xml,
                      cell_count_xml, row_count_logical, cell_count_logical,
                      empty_cell_count, nested_table_count, empty_table_cell_block_count,
                      numeric_quantity_rejection_count, odt_repeat_attrs_present,
                      xml_ph_element_count, xml_ph_nested_count, xml_ph_emitted_unique,
                      xml_ph_unaccounted,
                      source_structural_block_count_before_repeat_expansion,
                      accepted_manifest_sha256, accepted_manifest_match, statement,
                      source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    args,
                )
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.release_artifact')} (
                      run_id, release_id, artifact_sha256, association_role
                    ) VALUES (%s,%s,%s,%s)
                    """,
                    rel_art,
                )
            stream_table_fingerprints["source_release"] = _ordered_fingerprint(
                release_hashes
            )
            if (
                stream_table_fingerprints["source_release"]
                != material["table_fingerprints"]["source_release"]
            ):
                raise StageLoadError("apply_release_fp_mismatch", "source_release")

            # Reread blocks with exact-byte integrity (TOCTOU close).
            block_meta = _input_file_meta(input_files, "stage/blocks.jsonl")
            block_hashes: list[str] = []

            def block_tuples() -> Iterator[tuple[Any, ...]]:
                for _line_no, obj, _raw in _stream_jsonl_hashed(
                    paths["stage/blocks.jsonl"],
                    logical_name="stage/blocks.jsonl",
                    expected=block_meta,
                ):
                    row = _validate_block_row(obj, where="apply.blocks")
                    row_hash = row_sha256(row)
                    block_hashes.append(row_hash)
                    yield (
                        run_id,
                        row["block_id"],
                        row["artifact_sha256"],
                        row["relative_path"],
                        row["block_kind"],
                        row["container"],
                        row["element_name"],
                        row.get("style_name"),
                        row["in_table"],
                        row["in_index_context"],
                        row["xml_element_index"],
                        row["locator"]["doc_order"],
                        stable_json_dumps(row["locator"]),
                        row["locator_key"],
                        row["raw_text"],
                        row["normalized_search_text"],
                        row["raw_text_sha256"],
                        row["raw_text_byte_length"],
                        row["raw_text_char_length"],
                        row["parser_version"],
                        row_hash,
                    )

            for batch in _batched(block_tuples(), INSERT_BATCH_SIZE):
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.structural_block')} (
                      run_id, block_id, artifact_sha256, relative_path, block_kind,
                      container, element_name, style_name, in_table, in_index_context,
                      xml_element_index, parser_order, locator, locator_key,
                      raw_text, normalized_search_text, raw_text_sha256,
                      raw_text_byte_length, raw_text_char_length, parser_version,
                      source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                      %s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    batch,
                )
            stream_table_fingerprints["structural_block"] = _ordered_fingerprint(
                block_hashes
            )
            multiset_table_fingerprints["structural_block"] = _multiset_fingerprint(
                block_hashes
            )
            if (
                stream_table_fingerprints["structural_block"]
                != material["table_fingerprints"]["structural_block"]
            ):
                raise StageLoadError("apply_block_fp_mismatch", "structural_block")
            if (
                multiset_table_fingerprints["structural_block"]
                != expected_multiset_fps["structural_block"]
            ):
                raise StageLoadError(
                    "apply_block_multiset_fp_mismatch", "structural_block"
                )

            occ_meta = _input_file_meta(input_files, "stage/occurrences.jsonl")
            # Single declaration; stream hashes compared to validation material.
            apply_occ_hashes: list[str] = []

            def occ_tuples() -> Iterator[tuple[Any, ...]]:
                for _line_no, obj, _raw in _stream_jsonl_hashed(
                    paths["stage/occurrences.jsonl"],
                    logical_name="stage/occurrences.jsonl",
                    expected=occ_meta,
                ):
                    row = _validate_occurrence_row(
                        obj, where="apply.occurrences", require_stage_text=True
                    )
                    pg = {
                        "schema": row["schema"],
                        "occurrence_id": row["occurrence_id"],
                        "artifact_sha256": row["artifact_sha256"],
                        "block_id": row["block_id"],
                        "relative_path": row["relative_path"],
                        "designation_text": row["designation_text"],
                        "match_start_in_raw": row["match_start_in_raw"],
                        "match_end_in_raw": row["match_end_in_raw"],
                        "raw_text_sha256": row["raw_text_sha256"],
                        "raw_text_byte_length": row["raw_text_byte_length"],
                        "raw_text_char_length": row["raw_text_char_length"],
                        "container": row["container"],
                        "in_index_context": row["in_index_context"],
                        "ambiguity_flags": row["ambiguity_flags"],
                        "parser_version": row["parser_version"],
                        "statement": row["statement"],
                    }
                    row_hash = row_sha256(pg)
                    apply_occ_hashes.append(row_hash)
                    yield (
                        run_id,
                        row["occurrence_id"],
                        row["artifact_sha256"],
                        row["block_id"],
                        row["relative_path"],
                        row["designation_text"],
                        row["match_start_in_raw"],
                        row["match_end_in_raw"],
                        row["raw_text_sha256"],
                        row["raw_text_byte_length"],
                        row["raw_text_char_length"],
                        row["container"],
                        row["in_index_context"],
                        stable_json_dumps(row["ambiguity_flags"]),
                        row["parser_version"],
                        row["statement"],
                        row_hash,
                    )

            for batch in _batched(occ_tuples(), INSERT_BATCH_SIZE):
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.occurrence_candidate')} (
                      run_id, occurrence_id, artifact_sha256, block_id, relative_path,
                      designation_text, match_start_in_raw, match_end_in_raw,
                      raw_text_sha256, raw_text_byte_length, raw_text_char_length,
                      container, in_index_context, ambiguity_flags, parser_version,
                      statement, source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s
                    )
                    """,
                    batch,
                )
            stream_table_fingerprints["occurrence_candidate"] = _ordered_fingerprint(
                apply_occ_hashes
            )
            multiset_table_fingerprints["occurrence_candidate"] = _multiset_fingerprint(
                apply_occ_hashes
            )
            if (
                stream_table_fingerprints["occurrence_candidate"]
                != material["table_fingerprints"]["occurrence_candidate"]
            ):
                raise StageLoadError(
                    "apply_occurrence_fp_mismatch", "occurrence_candidate"
                )
            if (
                multiset_table_fingerprints["occurrence_candidate"]
                != expected_multiset_fps["occurrence_candidate"]
            ):
                raise StageLoadError(
                    "apply_occurrence_multiset_fp_mismatch", "occurrence_candidate"
                )

            issue_meta = _input_file_meta(input_files, "receipt/issues.jsonl")
            issue_hashes: list[str] = []

            def issue_tuples() -> Iterator[tuple[Any, ...]]:
                for line_no, obj, _raw in _stream_jsonl_hashed(
                    paths["receipt/issues.jsonl"],
                    logical_name="receipt/issues.jsonl",
                    expected=issue_meta,
                ):
                    row = _validate_issue_row(obj, where="apply.issues")
                    attrs = {k: row[k] for k in ISSUE_ATTRIBUTE_KEYS if k in row}
                    is_blocking = row["severity"] in BLOCKING_SEVERITIES
                    core = {
                        "issue_seq": line_no - 1,
                        "issue_code": row["issue_code"],
                        "issue_class": row["issue_class"],
                        "severity": row["severity"],
                        "is_blocking": is_blocking,
                        "relative_path": row.get("relative_path"),
                        "detail": row["detail"],
                        "attributes": attrs,
                    }
                    row_hash = row_sha256(core)
                    issue_hashes.append(row_hash)
                    yield (
                        run_id,
                        line_no - 1,
                        row["issue_code"],
                        row["issue_class"],
                        row["severity"],
                        is_blocking,
                        row.get("relative_path"),
                        row["detail"],
                        row.get("block_id")
                        if isinstance(row.get("block_id"), str)
                        else None,
                        row.get("locator_key"),
                        stable_json_dumps(attrs),
                        row_hash,
                    )

            for batch in _batched(issue_tuples(), INSERT_BATCH_SIZE):
                _executemany(
                    cur,
                    f"""
                    INSERT INTO {_assert_sql_relation(f'{STAGE_SCHEMA}.stage_issue')} (
                      run_id, issue_seq, issue_code, issue_class, severity, is_blocking,
                      relative_path, detail, block_id, locator_key, attributes,
                      source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
                    )
                    """,
                    batch,
                )
            stream_table_fingerprints["stage_issue"] = _ordered_fingerprint(issue_hashes)
            if (
                stream_table_fingerprints["stage_issue"]
                != material["table_fingerprints"]["stage_issue"]
            ):
                raise StageLoadError("apply_issue_fp_mismatch", "stage_issue")

            # Pre-seal in-transaction counts.
            for table, col in (
                ("source_release", "release_count"),
                ("source_artifact", "source_artifact_count"),
                ("structural_block", "block_count"),
                ("occurrence_candidate", "occurrence_count"),
                ("stage_issue", "issue_count"),
            ):
                r = _cursor_execute(
                    cur,
                    f"SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.{table}')} WHERE run_id = %s",
                    (run_id,),
                )
                verified[col] = _cursor_fetchone(r)[0]
            r = _cursor_execute(
                cur,
                f"""
                SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.structural_block')}
                WHERE run_id = %s AND block_kind = 'empty_table_cell'
                """,
                (run_id,),
            )
            verified["empty_table_cell_block_count"] = _cursor_fetchone(r)[0]
            r = _cursor_execute(
                cur,
                f"""
                SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.stage_issue')}
                WHERE run_id = %s AND is_blocking IS TRUE
                """,
                (run_id,),
            )
            verified["blocking_issue_count"] = _cursor_fetchone(r)[0]
            for key in (
                "release_count",
                "source_artifact_count",
                "block_count",
                "occurrence_count",
                "issue_count",
                "empty_table_cell_block_count",
                "blocking_issue_count",
            ):
                if verified.get(key) != counts.get(key):
                    raise StageLoadError(
                        "post_load_count_mismatch",
                        f"{key}: db={verified.get(key)} expected={counts.get(key)}",
                    )

            # Seal only from loading; require exactly one affected row.
            _cursor_execute(
                cur,
                f"""
                UPDATE {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')}
                SET state = 'sealed',
                    sealed_fingerprint = %s,
                    output_fingerprint = %s,
                    verified_counts = %s::jsonb,
                    sealed_at = now()
                WHERE run_id = %s AND state = 'loading'
                """,
                (
                    sealed_fp,
                    sealed_fp,
                    stable_json_dumps(verified),
                    run_id,
                ),
            )
            rc = getattr(cur, "rowcount", None)
            if rc != 1:
                raise StageLoadError(
                    "seal_rowcount",
                    f"expected 1 got {rc}",
                )

        conn.commit()

    # Post-commit verification on a fresh connection (fail closed).
    try:
        post_commit_verify(
            psycopg_mod=psycopg,
            dsn=dsn,
            run_id=run_id,
            material=material,
            verified_counts=verified,
            stream_table_fingerprints=stream_table_fingerprints,
            multiset_table_fingerprints=multiset_table_fingerprints,
        )
    except StageLoadError:
        receipt["status"] = "post_commit_verify_failed"
        receipt["run_id"] = str(run_id)
        raise

    receipt["status"] = "loaded"
    receipt["run_id"] = str(run_id)
    receipt["verified_counts"] = verified
    receipt_is_clean(receipt)
    return receipt


def drop_run(
    *,
    run_id: uuid.UUID,
    expect_fingerprint: str,
    dsn: str,
) -> dict[str, Any]:
    psycopg = _import_psycopg()
    require_sha256(expect_fingerprint, field="expect_fingerprint")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Lock order: global, then fingerprint (must match apply).
            acquire_stage_locks(cur, expect_fingerprint)
            row = _cursor_fetchone(
                _cursor_execute(
                    cur,
                    f"""
                    SELECT run_id, state, sealed_fingerprint
                    FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')}
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
            )
            if row is None:
                raise StageLoadError("drop_run_missing", str(run_id))
            if row[2] != expect_fingerprint:
                raise StageLoadError("drop_run_fingerprint_mismatch", "fingerprint")
            counts_before = {}
            for table in (
                "run_input_file",
                "source_release",
                "source_artifact",
                "release_artifact",
                "structural_block",
                "occurrence_candidate",
                "stage_issue",
            ):
                counts_before[table] = _cursor_fetchone(
                    _cursor_execute(
                        cur,
                        f"SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.{table}')} WHERE run_id = %s",
                        (run_id,),
                    )
                )[0]
            _cursor_execute(
                cur,
                """
                SELECT set_config(
                  'tw_drug_history_stage.drop_run_fingerprint',
                  %s,
                  true
                )
                """,
                (expect_fingerprint,),
            )
            _cursor_execute(
                cur,
                f"DELETE FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.rebuild_run')} WHERE run_id = %s",
                (run_id,),
            )
            survivors = {}
            for table in (
                "rebuild_run",
                "run_input_file",
                "source_release",
                "source_artifact",
                "release_artifact",
                "structural_block",
                "occurrence_candidate",
                "stage_issue",
            ):
                n = _cursor_fetchone(
                    _cursor_execute(
                        cur,
                        f"SELECT count(*) FROM {_assert_sql_relation(f'{STAGE_SCHEMA}.{table}')} WHERE run_id = %s",
                        (run_id,),
                    )
                )[0]
                survivors[table] = n
                if n != 0:
                    raise StageLoadError("drop_run_survivors", table)
        conn.commit()
    receipt = {
        "schema": "nhi-rule-history-stage-load-receipt/v1",
        "mode": "drop-run",
        "status": "dropped",
        "run_id": str(run_id),
        "expect_fingerprint": expect_fingerprint,
        "counts_before": counts_before,
        "survivors": survivors,
        "loader_version": LOADER_VERSION,
        "contract_version": CONTRACT_VERSION,
    }
    receipt_is_clean(receipt)
    return receipt


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = stable_json_dumps(receipt) + "\n"
    receipt_is_clean(receipt)
    path.write_text(payload, encoding="utf-8")
    return sha256_bytes(payload.encode("utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validate/load NHI rule-history stage into tw_drug_history_stage"
    )
    p.add_argument(
        "command",
        nargs="?",
        default="validate",
        choices=("validate", "apply", "drop-run"),
        help="validate (default) | apply | drop-run",
    )
    p.add_argument("--history-dir", type=Path, help="Immutable history ODT directory")
    p.add_argument("--stage-dir", type=Path, help="Stage JSONL directory")
    p.add_argument("--receipt-dir", type=Path, help="Tracked occurrence receipt dir")
    p.add_argument(
        "--accepted-manifest",
        type=Path,
        help="Accepted corpus-profile manifest.jsonl",
    )
    p.add_argument(
        "--output-receipt",
        type=Path,
        help="Write validation/load receipt JSON here",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Required to open a database connection for apply/drop-run",
    )
    p.add_argument(
        "--dsn",
        default=None,
        help=(
            "Operator DSN; otherwise read NHI_RULE_HISTORY_DSN. "
            "The value is never logged."
        ),
    )
    p.add_argument("--drop-run-id", type=str, default=None)
    p.add_argument("--expect-fingerprint", type=str, default=None)
    p.add_argument(
        "--print-fingerprint",
        action="store_true",
        help="Print sealed/run fingerprint only (no paths)",
    )
    return p


def resolve_operator_dsn(explicit_dsn: str | None) -> str:
    dsn = explicit_dsn or os.environ.get(DSN_ENV_VAR)
    if not dsn:
        raise StageLoadError(
            "dsn_required",
            f"--dsn or {DSN_ENV_VAR} is required for database operations",
        )
    return dsn


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "drop-run":
            if not args.apply:
                raise StageLoadError(
                    "apply_required",
                    "drop-run requires explicit --apply",
                )
            if not args.drop_run_id or not args.expect_fingerprint:
                raise StageLoadError(
                    "drop_run_args",
                    "--drop-run-id and --expect-fingerprint required",
                )
            dsn = resolve_operator_dsn(args.dsn)
            receipt = drop_run(
                run_id=uuid.UUID(args.drop_run_id),
                expect_fingerprint=args.expect_fingerprint,
                dsn=dsn,
            )
            if args.output_receipt:
                write_receipt(args.output_receipt, receipt)
            if args.print_fingerprint:
                print(receipt.get("expect_fingerprint", ""))
            return 0

        if args.command == "apply" and not args.apply:
            raise StageLoadError(
                "apply_required",
                "apply requires explicit --apply",
            )
        if args.command == "validate" and args.apply:
            raise StageLoadError(
                "apply_not_for_validate",
                "validate must not pass --apply",
            )

        for req in (
            "history_dir",
            "stage_dir",
            "receipt_dir",
            "accepted_manifest",
        ):
            if getattr(args, req) is None:
                raise StageLoadError("missing_arg", req)

        material = validate_stage_inputs(
            history_dir=args.history_dir,
            stage_dir=args.stage_dir,
            receipt_dir=args.receipt_dir,
            accepted_manifest=args.accepted_manifest,
        )
        receipt = material["receipt"]

        if args.command == "apply":
            dsn = resolve_operator_dsn(args.dsn)
            receipt = apply_stage(material, dsn=dsn)
        elif args.command != "validate":
            raise StageLoadError("unknown_command", args.command)

        if args.output_receipt:
            write_receipt(args.output_receipt, receipt)
        if args.print_fingerprint:
            print(receipt["run_fingerprint"])
        else:
            # Minimal stdout: fingerprint + status only
            print(
                stable_json_dumps(
                    {
                        "status": receipt["status"],
                        "run_fingerprint": receipt["run_fingerprint"],
                        "counts": receipt["counts"],
                    }
                )
            )
        return 0
    except StageLoadError as exc:
        print(stable_json_dumps({"status": "error", "code": exc.code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
