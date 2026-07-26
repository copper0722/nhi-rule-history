#!/usr/bin/env python3
"""Source-local ODT structural block + rule-occurrence candidate extractor.

Emits release / block / occurrence observations from historical NHI ODT
snapshots. Does NOT infer cross-release identity, legal effective dates, or
diffs. Does NOT flatten multi-column comparison tables into one version.

CLI (hyphenated directory is NOT a Python package import name):

    python3 occurrence_extract.py \\
        --history-dir <path> \\
        --accepted-manifest <manifest.jsonl> \\
        --stage-dir <path> \\
        --receipt-dir <path>

    python3 run_occurrences.py ...  # preferred thin wrapper
"""

from __future__ import annotations

import argparse
import io
import json
import re
import stat as statmod
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

# Reuse deterministic helpers from the accepted corpus profiler.
import corpus_profile as cp

PARSER_VERSION = "nhi-rule-history-occurrence-extract/1.1.0"
SCHEMA_RELEASE = "nhi-rule-history-release-observation/v1"
SCHEMA_BLOCK = "nhi-rule-history-structural-block/v1"
SCHEMA_OCCURRENCE = "nhi-rule-history-occurrence-candidate/v1"
SCHEMA_SUMMARY = "nhi-rule-history-occurrence-summary/v1"

EXPECTED_HISTORY_COUNT = 14

DEFAULT_CANARIES = (
    "9.138",
    "9.24",
    "3.3.13",
    "8.2.4.7",
    "1.3.5",
    "3.3.31",
)

# NHI payment-rule chapter first-segment bound (parser policy, not legal law).
# Observed chapter heads are small integers (1–20 range). Values such as 563
# are numeric quantities, not chapter designations.
MAX_DESIGNATION_FIRST_SEGMENT = 99

# Top-level index / TOC container local-names (ODT).
_INDEX_CONTAINER_NAMES = frozenset(
    {
        "table-of-content",
        "illustration-index",
        "alphabetical-index",
        "bibliography",
        "user-index",
        "object-index",
        "table-index",
    }
)

_FRAME_CONTAINER_NAMES = frozenset(
    {
        "frame",
        "text-box",
        "image",
        "object",
        "object-ole",
        "plugin",
        "floating-frame",
    }
)

# Leading whitespace / light punctuation that may precede a designation.
_LEADING_STRIP_RE = re.compile(
    r"^(?:"
    r"[\s\u3000\u00a0]+|"
    r"[\[\(\{（【「『《〈]+|"
    r"[\]\)\}）】」』》〉]+"
    r")+"
)

# Designation at start after strip: one or more dotted numeric segments.
_DESIGNATION_RE = re.compile(
    r"^(?P<desig>\d+(?:\.\d+)+)"
    r"(?![0-9])"
)

# Dose / volume / mass / concentration units that mark a numeric quantity head.
# Matched against text immediately after the designation (optional whitespace).
_UNIT_AFTER_DESIGNATION_RE = re.compile(
    r"^(?:"
    r"m[Ll]|[µμu][Ll]|L\b|"
    r"mg|µg|ug|mcg|ng|pg|g\b|kg|"
    r"mEq|mmol|mol|"
    r"IU\b|U\b|"
    r"%|"
    r"mg/m[Ll]|µg/m[Ll]|ug/m[Ll]|ng/m[Ll]|mg/dL|g/dL|"
    r"m[Ll]/kg|mg/kg|"
    r"次|錠|粒|支|瓶|袋|amp|vial"
    r")",
    re.UNICODE,
)

# ---------------------------------------------------------------------------
# Checkout-local paths (portable; never hard-code a temporary worktree path)
# ---------------------------------------------------------------------------


def repository_root() -> Path:
    """Return the repository root that contains this script.

    Layout: <root>/.script/nhi-rule-history/occurrence_extract.py
    """
    return Path(__file__).resolve().parents[2]


def default_stage_root() -> Path:
    """Full-text stage root under the checkout that owns this script."""
    return (
        repository_root()
        / ".work"
        / "nhi-rule-history-stage"
        / "grok-occurrences"
    )


# Evaluated at import from the script's resolved location (portable).
DEFAULT_STAGE_ROOT = default_stage_root()

# ---------------------------------------------------------------------------
# Tracked-schema allowlists (fail closed on unknown fields)
# ---------------------------------------------------------------------------

STAGE_ONLY_OCCURRENCE_KEYS = frozenset({"raw_text", "normalized_search_text"})
STAGE_ONLY_BLOCK_KEYS = frozenset({"raw_text", "normalized_search_text"})

TRACKED_RELEASE_KEYS = frozenset(
    {
        "schema",
        "release_id",
        "relative_path",
        "basename",
        "sha256",
        "byte_length",
        "filename_label_raw",
        "filename_id_prefix",
        "filename_date_fragments_raw",
        "analysis_chronology",
        "source_order_index",
        "parser_version",
        "block_count",
        "occurrence_count",
        "table_count",
        "row_count_xml",
        "cell_count_xml",
        "row_count_logical",
        "cell_count_logical",
        "empty_cell_count",
        "nested_table_count",
        "odt_repeat_attrs_present",
        "statement",
        "accepted_manifest_sha256",
        "accepted_manifest_match",
        "xml_ph_element_count",
        "xml_ph_nested_count",
        "xml_ph_emitted_unique",
        "xml_ph_unaccounted",
        "source_structural_block_count_before_repeat_expansion",
        "empty_table_cell_block_count",
        "numeric_quantity_rejection_count",
    }
)

TRACKED_OCCURRENCE_KEYS = frozenset(
    {
        "schema",
        "occurrence_id",
        "artifact_sha256",
        "relative_path",
        "designation_text",
        "block_id",
        "locator",
        "locator_key",
        "raw_text_sha256",
        "raw_text_byte_length",
        "raw_text_char_length",
        "parser_version",
        "ambiguity_flags",
        "container",
        "match_start_in_raw",
        "match_end_in_raw",
        "statement",
        "in_index_context",
    }
)

TRACKED_CANARY_KEYS = frozenset(
    {
        "canary",
        "occurrence_id",
        "artifact_sha256",
        "relative_path",
        "designation_text",
        "locator_key",
        "locator",
        "container",
        "raw_text_sha256",
        "raw_text_byte_length",
        "raw_text_char_length",
        "parser_version",
        "statement",
        "in_index_context",
    }
)

TRACKED_ISSUE_KEYS = frozenset(
    {
        "issue_code",
        "severity",
        "relative_path",
        "detail",
        "issue_class",
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

TRACKED_SUMMARY_KEYS = frozenset(
    {
        "schema",
        "parser_version",
        "deterministic",
        "release_count",
        "expected_release_count",
        "release_count_match",
        "all_source_sha_match_accepted_manifest",
        "block_count",
        "occurrence_count",
        "occurrence_container_counts",
        "table_count_total",
        "row_count_xml_total",
        "cell_count_xml_total",
        "row_count_logical_total",
        "cell_count_logical_total",
        "empty_cell_count_total",
        "nested_table_count_total",
        "xml_ph_element_count_total",
        "xml_ph_nested_count_total",
        "xml_ph_emitted_unique_total",
        "xml_ph_unaccounted_total",
        "empty_table_cell_block_count_total",
        "numeric_quantity_rejection_count_total",
        "numeric_quantity_rejection_by_code",
        "canary_hit_counts",
        "canary_hit_total",
        "duplicate_designation_groups",
        "duplicate_designation_group_count",
        "issue_count",
        "issue_codes",
        "issue_severity_counts",
        "source_order_release_sequence",
        "analysis_only_chronology_sequence",
        "per_release_counts",
        "notes",
        "canonical_rule_history_promoted",
        "cross_release_identity_inferred",
        "legal_dates_inferred",
        "cross_release_diffs_computed",
        "max_designation_first_segment",
    }
)

# Absolute-path / host markers banned from tracked receipts and reports.
_ABS_PATH_MARKERS = (
    "/Users/",
    "/home/",
    "/private/tmp/",
    "/var/folders/",
    "\\\\",
)


class OccurrenceExtractError(Exception):
    """Fatal extraction failure (fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Text extraction (ODT-aware; does not merge nested structural p/h)
# ---------------------------------------------------------------------------


def extract_odt_text(el: ET.Element) -> str:
    """Concatenate text from an element, expanding ODT space/tab/break.

    Nested structural ``text:p`` / ``text:h`` elements are NOT merged into the
    ancestor's raw text; they are emitted as their own blocks by the walker.
    Inline spans/tabs/spaces under this element remain expanded here.
    """
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        name = cp.local_name(child.tag)
        if name in ("p", "h"):
            # Nested structural block — do not merge; keep parent tail only.
            if child.tail:
                parts.append(child.tail)
            continue
        if name == "s":
            raw_c = cp._odt_attr_local(child, "c")
            n = 1
            if raw_c is not None and raw_c != "":
                try:
                    n = int(raw_c)
                except ValueError as exc:
                    raise OccurrenceExtractError(
                        "invalid_odt_space_count",
                        f"text:s c={raw_c!r} is not an integer",
                    ) from exc
                if n < 0:
                    raise OccurrenceExtractError(
                        "invalid_odt_space_count",
                        f"text:s c={n} is negative",
                    )
                if n > cp.ODT_REPEAT_EXPANSION_CAP:
                    raise OccurrenceExtractError(
                        "invalid_odt_space_count",
                        f"text:s c={n} exceeds cap {cp.ODT_REPEAT_EXPANSION_CAP}",
                    )
            parts.append(" " * n)
        elif name == "tab":
            parts.append("\t")
        elif name == "line-break":
            parts.append("\n")
        elif name in (
            "soft-page-break",
            "bookmark-start",
            "bookmark-end",
            "reference-mark",
            "reference-mark-start",
            "reference-mark-end",
        ):
            parts.append(extract_odt_text(child))
        else:
            # spans, a, ruby, frame shells, etc. — recurse; nested p/h skipped above
            parts.append(extract_odt_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def normalize_search_text(raw: str) -> str:
    """Separate search field only; never replaces raw_text storage."""
    return re.sub(r"\s+", " ", raw).strip()


def detect_designation(raw_text: str) -> dict[str, Any] | None:
    """Return designation match info if a dotted numeric head begins this block.

    Conservative: strip leading whitespace/light punctuation, then require a
    dotted numeric designation at the start. Does not claim legal identity.
    Does not apply quantity rejection (see classify_non_rule_numeric_head).
    """
    if not raw_text:
        return None
    stripped = raw_text
    leading_removed = ""
    while True:
        m = _LEADING_STRIP_RE.match(stripped)
        if not m:
            break
        leading_removed += m.group(0)
        stripped = stripped[m.end() :]
    dm = _DESIGNATION_RE.match(stripped)
    if not dm:
        return None
    desig = dm.group("desig")
    if "." not in desig:
        return None
    remainder = stripped[len(desig) :]
    return {
        "designation_text": desig,
        "leading_stripped": leading_removed,
        "match_start_in_raw": len(leading_removed),
        "match_end_in_raw": len(leading_removed) + len(desig),
        "remainder_prefix": remainder[:1] if remainder else "",
        "remainder_after_designation": remainder,
    }


def classify_non_rule_numeric_head(
    designation_text: str,
    remainder_after_designation: str,
) -> dict[str, Any] | None:
    """Classify strong non-rule numeric heads; None means keep as candidate.

    Policy (documented parser bounds, not legal interpretation):
    - first dotted segment == 0 → zero-leading quantity (e.g. 0.2, 0.5)
    - first segment > MAX_DESIGNATION_FIRST_SEGMENT → out of NHI chapter range
    - designation immediately followed (after whitespace) by a dose/volume/mass
      /concentration unit → quantity with unit (e.g. ``12.5 mg``)
    """
    parts = designation_text.split(".")
    try:
        first = int(parts[0])
    except ValueError:
        return {
            "rejection_code": "non_integer_first_segment",
            "first_segment": parts[0],
            "unit_token": None,
        }
    if first == 0:
        return {
            "rejection_code": "zero_leading_first_segment",
            "first_segment": first,
            "unit_token": None,
        }
    if first > MAX_DESIGNATION_FIRST_SEGMENT:
        return {
            "rejection_code": "first_segment_exceeds_chapter_bound",
            "first_segment": first,
            "unit_token": None,
        }
    rest = remainder_after_designation.lstrip(" \t\u3000\u00a0")
    um = _UNIT_AFTER_DESIGNATION_RE.match(rest)
    if um:
        return {
            "rejection_code": "unit_suffixed_numeric_quantity",
            "first_segment": first,
            "unit_token": um.group(0),
        }
    return None


# ---------------------------------------------------------------------------
# Structural walk
# ---------------------------------------------------------------------------


def _style_name(el: ET.Element) -> str | None:
    return cp._odt_attr_local(el, "style-name")


def _parse_repeat(raw: str | None, *, what: str) -> int:
    try:
        return cp._parse_repeat_attr(raw, what=what)
    except cp.CorpusProfileError as exc:
        raise OccurrenceExtractError(
            getattr(exc, "code", "invalid_odt_repeat"),
            getattr(exc, "message", str(exc)),
        ) from exc


def _parse_span_attr(raw: str | None, *, what: str) -> int:
    """Parse table span attributes; fail closed on invalid/nonpositive/extreme."""
    return _parse_repeat(raw, what=what)


def _locator_key(parts: Mapping[str, Any]) -> str:
    """Stable compact locator string (sorted keys, no whitespace noise)."""
    items = []
    for k in sorted(parts.keys()):
        v = parts[k]
        if v is None:
            v = ""
        items.append(f"{k}={v}")
    return "|".join(items)


def _block_id(artifact_sha: str, locator_key: str) -> str:
    return cp.sha256_text(f"{artifact_sha}\n{locator_key}")


def _occurrence_id(artifact_sha: str, locator_key: str, raw_text_sha: str) -> str:
    return cp.sha256_text(f"{artifact_sha}\n{locator_key}\n{raw_text_sha}")


def assign_xml_element_indices(root: ET.Element) -> dict[int, int]:
    """Document-order index for every element in the parsed tree (depth-first)."""
    indices: dict[int, int] = {}
    i = 0
    for el in root.iter():
        indices[id(el)] = i
        i += 1
    return indices


def count_ph_elements(root: ET.Element) -> tuple[int, int]:
    """Return (total p/h count, nested p/h count) over the parsed tree."""
    parent: dict[int, ET.Element] = {}
    for pr in root.iter():
        for ch in pr:
            parent[id(ch)] = pr
    total = 0
    nested = 0
    for el in root.iter():
        if cp.local_name(el.tag) not in ("p", "h"):
            continue
        total += 1
        cur = parent.get(id(el))
        while cur is not None:
            if cp.local_name(cur.tag) in ("p", "h"):
                nested += 1
                break
            cur = parent.get(id(cur))
    return total, nested


class _WalkState:
    def __init__(
        self,
        artifact_sha: str,
        relative_path: str,
        xml_indices: Mapping[int, int],
    ) -> None:
        self.artifact_sha = artifact_sha
        self.relative_path = relative_path
        self.xml_indices = xml_indices
        self.doc_order = 0
        self.table_index = 0
        self.blocks: list[dict[str, Any]] = []
        self.locator_keys: set[str] = set()
        self.issues: list[dict[str, Any]] = []
        self.table_count = 0
        self.row_count_xml = 0
        self.cell_count_xml = 0
        self.row_count_logical = 0
        self.cell_count_logical = 0
        self.empty_cell_count = 0
        self.nested_table_count = 0
        self.repeat_attrs_present = False
        self.span_attrs_present = False
        # Unique source XML p/h element ids emitted (before logical expansion
        # counting; set membership is once per XML element).
        self.emitted_ph_xml_ids: set[int] = set()
        # Blocks that correspond 1:1 to source XML p/h before expansion would
        # still re-emit on expansion; track unique only via emitted_ph_xml_ids.
        self.empty_table_cell_block_count = 0
        self.source_structural_ph_emissions = 0  # counts unique at end
        self.xml_ph_element_count = 0
        self.xml_ph_nested_count = 0


def _xml_index_for(state: _WalkState, el: ET.Element) -> int:
    try:
        return state.xml_indices[id(el)]
    except KeyError as exc:
        raise OccurrenceExtractError(
            "missing_xml_element_index",
            f"{state.relative_path}: element without assigned xml_element_index",
        ) from exc


def _emit_text_block(
    state: _WalkState,
    el: ET.Element,
    *,
    container: str,
    table_ctx: dict[str, Any] | None,
    list_depth: int,
    in_frame: bool,
    nested_table_depth: int,
    in_index_context: bool,
) -> None:
    element_name = cp.local_name(el.tag)
    if element_name not in ("p", "h"):
        return
    raw = extract_odt_text(el)
    style = _style_name(el)
    xml_element_index = _xml_index_for(state, el)
    state.emitted_ph_xml_ids.add(id(el))

    loc_parts: dict[str, Any] = {
        "container": container,
        "doc_order": state.doc_order,
        "xml_element_index": xml_element_index,
        "element": element_name,
        "style_name": style or "",
        "list_depth": list_depth,
        "in_frame": 1 if in_frame else 0,
        "in_index_context": 1 if in_index_context else 0,
        "nested_table_depth": nested_table_depth,
    }
    if table_ctx is not None:
        loc_parts.update(table_ctx)

    locator_key = _locator_key(loc_parts)
    if locator_key in state.locator_keys:
        raise OccurrenceExtractError(
            "duplicate_structural_locator",
            f"{state.relative_path}: duplicate locator_key {locator_key!r}",
        )
    state.locator_keys.add(locator_key)

    raw_bytes = raw.encode("utf-8")
    raw_sha = cp.sha256_bytes(raw_bytes)
    block_id = _block_id(state.artifact_sha, locator_key)
    if element_name == "h":
        block_kind = "heading"
    elif container == "table_cell":
        block_kind = "table_paragraph"
    elif in_index_context:
        block_kind = "index_paragraph"
    elif in_frame:
        block_kind = "frame_paragraph"
    else:
        block_kind = "paragraph"
    block = {
        "schema": SCHEMA_BLOCK,
        "block_id": block_id,
        "artifact_sha256": state.artifact_sha,
        "relative_path": state.relative_path,
        "locator": {k: loc_parts[k] for k in sorted(loc_parts.keys())},
        "locator_key": locator_key,
        "block_kind": block_kind,
        "element_name": element_name,
        "style_name": style,
        "in_table": container == "table_cell",
        "in_index_context": bool(in_index_context),
        "container": container,
        "raw_text": raw,
        "normalized_search_text": normalize_search_text(raw),
        "raw_text_sha256": raw_sha,
        "raw_text_byte_length": len(raw_bytes),
        "raw_text_char_length": len(raw),
        "parser_version": PARSER_VERSION,
        "xml_element_index": xml_element_index,
    }
    state.blocks.append(block)
    state.doc_order += 1

    # Nested structural p/h (frames/text-boxes/notes inside this element) get
    # their own blocks; do not rely on ancestor raw_text.
    _walk_nested_inside_text_element(
        el,
        state,
        container=container,
        table_ctx=table_ctx,
        list_depth=list_depth,
        in_frame=in_frame,
        nested_table_depth=nested_table_depth,
        in_index_context=in_index_context,
    )


def _walk_nested_inside_text_element(
    parent_el: ET.Element,
    state: _WalkState,
    *,
    container: str,
    table_ctx: dict[str, Any] | None,
    list_depth: int,
    in_frame: bool,
    nested_table_depth: int,
    in_index_context: bool,
) -> None:
    """Walk children of a p/h for nested structural content (frames, nested p/h)."""
    for child in parent_el:
        name = cp.local_name(child.tag)
        if name in ("p", "h"):
            nested_container = container
            if in_frame and container != "table_cell":
                nested_container = "frame"
            _emit_text_block(
                state,
                child,
                container=nested_container,
                table_ctx=table_ctx,
                list_depth=list_depth,
                in_frame=in_frame,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
        elif name == "list":
            _walk_list(
                child,
                state,
                container=container,
                table_ctx=table_ctx,
                list_depth=list_depth + 1,
                in_frame=in_frame,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
        elif name == "table":
            state.nested_table_count += 1 if table_ctx is not None else 0
            _walk_table(
                child,
                state,
                nested_table_depth=nested_table_depth + (1 if table_ctx else 0),
                in_frame=in_frame,
                in_index_context=in_index_context,
            )
        elif name in _FRAME_CONTAINER_NAMES or name == "frame":
            _walk_frame(
                child,
                state,
                container=container if container == "table_cell" else "frame",
                table_ctx=table_ctx,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
        elif name in ("s", "tab", "line-break", "soft-page-break"):
            continue
        else:
            # spans, hyperlinks, notes, unknown inline shells — may nest frames/p
            if len(list(child)) > 0:
                _walk_nested_inside_text_element(
                    child,
                    state,
                    container=container,
                    table_ctx=table_ctx,
                    list_depth=list_depth,
                    in_frame=in_frame or name in _FRAME_CONTAINER_NAMES,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )


def _walk_list(
    list_el: ET.Element,
    state: _WalkState,
    *,
    container: str,
    table_ctx: dict[str, Any] | None,
    list_depth: int,
    in_frame: bool,
    nested_table_depth: int,
    in_index_context: bool,
) -> None:
    for item in list_el:
        if cp.local_name(item.tag) != "list-item":
            name = cp.local_name(item.tag)
            if name in ("p", "h"):
                _emit_text_block(
                    state,
                    item,
                    container=container,
                    table_ctx=table_ctx,
                    list_depth=list_depth,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif name == "list":
                _walk_list(
                    item,
                    state,
                    container=container,
                    table_ctx=table_ctx,
                    list_depth=list_depth + 1,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            continue
        for sub in item:
            sname = cp.local_name(sub.tag)
            if sname in ("p", "h"):
                _emit_text_block(
                    state,
                    sub,
                    container=container,
                    table_ctx=table_ctx,
                    list_depth=list_depth,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif sname == "list":
                _walk_list(
                    sub,
                    state,
                    container=container,
                    table_ctx=table_ctx,
                    list_depth=list_depth + 1,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif sname == "table":
                _walk_table(
                    sub,
                    state,
                    nested_table_depth=nested_table_depth + (1 if table_ctx else 0),
                    in_frame=in_frame,
                    in_index_context=in_index_context,
                )


def _cell_has_structural_descendant(cell: ET.Element) -> bool:
    for sub in cell.iter():
        if sub is cell:
            continue
        if cp.local_name(sub.tag) in ("p", "h", "list", "table"):
            return True
    return False


def _emit_empty_table_cell_block(
    state: _WalkState,
    cell: ET.Element,
    *,
    table_ctx: dict[str, Any],
    in_frame: bool,
    nested_table_depth: int,
    in_index_context: bool,
) -> None:
    raw = ""
    raw_bytes = b""
    raw_sha = cp.sha256_bytes(raw_bytes)
    xml_element_index = _xml_index_for(state, cell)
    cn = cp.local_name(cell.tag)
    loc_parts: dict[str, Any] = {
        "container": "table_cell",
        "doc_order": state.doc_order,
        "xml_element_index": xml_element_index,
        "element": cn,
        "style_name": _style_name(cell) or "",
        "list_depth": 0,
        "in_frame": 1 if in_frame else 0,
        "in_index_context": 1 if in_index_context else 0,
        "nested_table_depth": nested_table_depth,
        "empty_cell": 1,
        "para_index_in_cell": -1,
    }
    loc_parts.update(table_ctx)
    locator_key = _locator_key(loc_parts)
    if locator_key in state.locator_keys:
        raise OccurrenceExtractError(
            "duplicate_structural_locator",
            f"{state.relative_path}: duplicate empty-cell locator {locator_key!r}",
        )
    state.locator_keys.add(locator_key)
    block_id = _block_id(state.artifact_sha, locator_key)
    block = {
        "schema": SCHEMA_BLOCK,
        "block_id": block_id,
        "artifact_sha256": state.artifact_sha,
        "relative_path": state.relative_path,
        "locator": {k: loc_parts[k] for k in sorted(loc_parts.keys())},
        "locator_key": locator_key,
        "block_kind": "empty_table_cell",
        "element_name": cn,
        "style_name": _style_name(cell),
        "in_table": True,
        "in_index_context": bool(in_index_context),
        "container": "table_cell",
        "raw_text": raw,
        "normalized_search_text": "",
        "raw_text_sha256": raw_sha,
        "raw_text_byte_length": 0,
        "raw_text_char_length": 0,
        "parser_version": PARSER_VERSION,
        "xml_element_index": xml_element_index,
    }
    state.blocks.append(block)
    state.doc_order += 1
    state.empty_table_cell_block_count += 1


def _walk_cell_content(
    cell: ET.Element,
    state: _WalkState,
    *,
    table_ctx_base: dict[str, Any],
    nested_table_depth: int,
    in_frame: bool,
    in_index_context: bool,
) -> None:
    """Walk p/h/list/table inside a cell; emit empty_table_cell when none."""
    if not _cell_has_structural_descendant(cell):
        _emit_empty_table_cell_block(
            state,
            cell,
            table_ctx=dict(table_ctx_base),
            in_frame=in_frame,
            nested_table_depth=nested_table_depth,
            in_index_context=in_index_context,
        )
        return

    para_index = 0
    for sub in cell:
        sname = cp.local_name(sub.tag)
        if sname in ("p", "h"):
            ctx = dict(table_ctx_base)
            ctx["para_index_in_cell"] = para_index
            _emit_text_block(
                state,
                sub,
                container="table_cell",
                table_ctx=ctx,
                list_depth=0,
                in_frame=in_frame,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
            para_index += 1
        elif sname == "list":

            def walk_list_in_cell(list_el: ET.Element, depth: int) -> None:
                nonlocal para_index
                for item in list_el:
                    iname = cp.local_name(item.tag)
                    if iname == "list-item":
                        for sub2 in item:
                            n2 = cp.local_name(sub2.tag)
                            if n2 in ("p", "h"):
                                ctx = dict(table_ctx_base)
                                ctx["para_index_in_cell"] = para_index
                                ctx["list_depth"] = depth
                                _emit_text_block(
                                    state,
                                    sub2,
                                    container="table_cell",
                                    table_ctx=ctx,
                                    list_depth=depth,
                                    in_frame=in_frame,
                                    nested_table_depth=nested_table_depth,
                                    in_index_context=in_index_context,
                                )
                                para_index += 1
                            elif n2 == "list":
                                walk_list_in_cell(sub2, depth + 1)
                            elif n2 == "table":
                                _walk_table(
                                    sub2,
                                    state,
                                    nested_table_depth=nested_table_depth + 1,
                                    in_frame=in_frame,
                                    in_index_context=in_index_context,
                                )
                    elif iname in ("p", "h"):
                        ctx = dict(table_ctx_base)
                        ctx["para_index_in_cell"] = para_index
                        ctx["list_depth"] = depth
                        _emit_text_block(
                            state,
                            item,
                            container="table_cell",
                            table_ctx=ctx,
                            list_depth=depth,
                            in_frame=in_frame,
                            nested_table_depth=nested_table_depth,
                            in_index_context=in_index_context,
                        )
                        para_index += 1

            walk_list_in_cell(sub, 1)
        elif sname == "table":
            state.nested_table_count += 1
            _walk_table(
                sub,
                state,
                nested_table_depth=nested_table_depth + 1,
                in_frame=in_frame,
                in_index_context=in_index_context,
            )
        elif sname in _FRAME_CONTAINER_NAMES:
            _walk_frame(
                sub,
                state,
                container="table_cell",
                table_ctx=dict(table_ctx_base),
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
        else:
            # Unknown cell child that may still wrap p/h (future producers).
            if any(
                cp.local_name(d.tag) in ("p", "h")
                for d in sub.iter()
                if d is not sub or cp.local_name(sub.tag) in ("p", "h")
            ):
                for d in sub.iter():
                    if d is sub:
                        continue
                    if cp.local_name(d.tag) in ("p", "h"):
                        # Only emit topmost p/h under this unknown shell once
                        # via a dedicated nested walk to preserve order.
                        pass
                _walk_nested_inside_text_element(
                    sub,
                    state,
                    container="table_cell",
                    table_ctx=dict(table_ctx_base),
                    list_depth=0,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )


def _walk_table(
    table_el: ET.Element,
    state: _WalkState,
    *,
    nested_table_depth: int,
    in_frame: bool,
    in_index_context: bool,
) -> None:
    t_index = state.table_index
    state.table_index += 1
    state.table_count += 1
    if nested_table_depth > 0:
        state.issues.append(
            cp.make_issue(
                issue_code="nested_table_observed",
                severity=cp.SEVERITY_INFO,
                relative_path=state.relative_path,
                detail=(
                    f"nested table at table_index={t_index} "
                    f"nested_table_depth={nested_table_depth}; "
                    "cells not joined across tables"
                ),
                issue_class="layout_complexity",
                table_index=t_index,
                nested_table_depth=nested_table_depth,
            )
        )

    row_xml_index = 0
    row_logical_index = 0

    for child in table_el:
        cname = cp.local_name(child.tag)
        if cname == "table-header-rows":
            for hdr in child:
                if cp.local_name(hdr.tag) == "table-row":
                    row_xml_index, row_logical_index = _walk_table_row(
                        hdr,
                        state,
                        table_index=t_index,
                        row_xml_index=row_xml_index,
                        row_logical_index=row_logical_index,
                        nested_table_depth=nested_table_depth,
                        in_frame=in_frame,
                        is_header=True,
                        in_index_context=in_index_context,
                    )
            continue
        if cname != "table-row":
            continue
        row_xml_index, row_logical_index = _walk_table_row(
            child,
            state,
            table_index=t_index,
            row_xml_index=row_xml_index,
            row_logical_index=row_logical_index,
            nested_table_depth=nested_table_depth,
            in_frame=in_frame,
            is_header=False,
            in_index_context=in_index_context,
        )


def _walk_table_row(
    row_el: ET.Element,
    state: _WalkState,
    *,
    table_index: int,
    row_xml_index: int,
    row_logical_index: int,
    nested_table_depth: int,
    in_frame: bool,
    is_header: bool,
    in_index_context: bool,
) -> tuple[int, int]:
    row_rep_raw = cp._odt_attr_local(row_el, "number-rows-repeated")
    if row_rep_raw is not None:
        state.repeat_attrs_present = True
    row_rep = _parse_repeat(row_rep_raw, what="table:number-rows-repeated")
    state.row_count_xml += 1

    cells: list[ET.Element] = []
    for cell in row_el:
        cn = cp.local_name(cell.tag)
        if cn in ("table-cell", "covered-table-cell"):
            cells.append(cell)

    for r_inst in range(row_rep):
        state.row_count_logical += 1
        cell_xml_index = 0
        cell_logical_index = 0
        for cell in cells:
            cn = cp.local_name(cell.tag)
            col_rep_raw = cp._odt_attr_local(cell, "number-columns-repeated")
            if col_rep_raw is not None:
                state.repeat_attrs_present = True
            col_rep = _parse_repeat(
                col_rep_raw, what="table:number-columns-repeated"
            )
            if row_rep * col_rep > cp.ODT_REPEAT_EXPANSION_CAP:
                raise OccurrenceExtractError(
                    "invalid_odt_repeat",
                    f"row_rep*col_rep={row_rep * col_rep} exceeds cap "
                    f"{cp.ODT_REPEAT_EXPANSION_CAP}",
                )

            col_span_raw = cp._odt_attr_local(cell, "number-columns-spanned")
            row_span_raw = cp._odt_attr_local(cell, "number-rows-spanned")
            if col_span_raw is not None or row_span_raw is not None:
                state.span_attrs_present = True
            col_span = _parse_span_attr(
                col_span_raw, what="table:number-columns-spanned"
            )
            row_span = _parse_span_attr(
                row_span_raw, what="table:number-rows-spanned"
            )

            if r_inst == 0:
                state.cell_count_xml += 1

            for c_inst in range(col_rep):
                state.cell_count_logical += 1
                is_empty = not _cell_has_structural_descendant(cell)
                if is_empty:
                    state.empty_cell_count += 1
                table_ctx = {
                    "table_index": table_index,
                    "row_xml_index": row_xml_index,
                    "row_logical_index": row_logical_index,
                    "cell_xml_index": cell_xml_index,
                    "cell_logical_index": cell_logical_index,
                    "row_repeat_attr": row_rep,
                    "col_repeat_attr": col_rep,
                    "row_repeat_instance": r_inst,
                    "col_repeat_instance": c_inst,
                    "number_columns_spanned": col_span,
                    "number_rows_spanned": row_span,
                    "cell_element": cn,
                    "is_header_row": 1 if is_header else 0,
                }
                _walk_cell_content(
                    cell,
                    state,
                    table_ctx_base=table_ctx,
                    nested_table_depth=nested_table_depth,
                    in_frame=in_frame,
                    in_index_context=in_index_context,
                )
                cell_logical_index += 1
            cell_xml_index += 1
        row_logical_index += 1
    row_xml_index += 1
    return row_xml_index, row_logical_index


def _walk_frame(
    frame_el: ET.Element,
    state: _WalkState,
    *,
    container: str,
    table_ctx: dict[str, Any] | None,
    nested_table_depth: int,
    in_index_context: bool,
) -> None:
    def walk(node: ET.Element, list_depth: int) -> None:
        for ch in node:
            name = cp.local_name(ch.tag)
            if name in ("p", "h"):
                _emit_text_block(
                    state,
                    ch,
                    container=container if container == "table_cell" else "frame",
                    table_ctx=table_ctx,
                    list_depth=list_depth,
                    in_frame=True,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif name == "list":
                _walk_list(
                    ch,
                    state,
                    container="frame" if container != "table_cell" else "table_cell",
                    table_ctx=table_ctx,
                    list_depth=list_depth + 1,
                    in_frame=True,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif name == "table":
                _walk_table(
                    ch,
                    state,
                    nested_table_depth=nested_table_depth + (1 if table_ctx else 0),
                    in_frame=True,
                    in_index_context=in_index_context,
                )
            elif name in _FRAME_CONTAINER_NAMES:
                walk(ch, list_depth)
            else:
                if list(ch):
                    walk(ch, list_depth)

    walk(frame_el, 0)


def _walk_flow_node(
    node: ET.Element,
    state: _WalkState,
    *,
    nested_table_depth: int = 0,
    in_index_context: bool = False,
) -> None:
    name = cp.local_name(node.tag)
    if name in ("p", "h"):
        _emit_text_block(
            state,
            node,
            container="index" if in_index_context else "flow",
            table_ctx=None,
            list_depth=0,
            in_frame=False,
            nested_table_depth=0,
            in_index_context=in_index_context,
        )
    elif name == "list":
        _walk_list(
            node,
            state,
            container="index" if in_index_context else "flow",
            table_ctx=None,
            list_depth=1,
            in_frame=False,
            nested_table_depth=0,
            in_index_context=in_index_context,
        )
    elif name == "table":
        _walk_table(
            node,
            state,
            nested_table_depth=0,
            in_frame=False,
            in_index_context=in_index_context,
        )
    elif name == "frame":
        _walk_frame(
            node,
            state,
            container="flow",
            table_ctx=None,
            nested_table_depth=0,
            in_index_context=in_index_context,
        )
    elif name == "a":
        for ch in node:
            _walk_flow_node(
                ch, state, in_index_context=in_index_context
            )
    elif name in _INDEX_CONTAINER_NAMES:
        # Preserve TOC/index text blocks in document order; mark index context.
        for ch in node:
            _walk_flow_node(ch, state, in_index_context=True)
    elif name in (
        "section",
        "illustration",
        "index-body",
        "index-title",
        "index-source-style",
        "index-title-template",
        "table-of-content-source",
        "table-of-content-entry-template",
    ):
        child_index = in_index_context or name.startswith("index") or name.startswith(
            "table-of-content"
        )
        for ch in node:
            _walk_flow_node(ch, state, in_index_context=child_index)
    elif name == "soft-page-break":
        return
    else:
        # Unknown container: never silently drop nested p/h.
        has_ph = any(
            cp.local_name(d.tag) in ("p", "h") for d in node.iter() if d is not node
        )
        if has_ph:
            for ch in node:
                _walk_flow_node(
                    ch, state, in_index_context=in_index_context
                )


def find_office_text(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if cp.local_name(el.tag) == "body":
            for ch in el:
                if cp.local_name(ch.tag) == "text":
                    return ch
    raise OccurrenceExtractError(
        "missing_office_text",
        "content.xml has no office:body/office:text",
    )


def parse_odt_blocks(
    data: bytes, *, artifact_sha: str, relative_path: str
) -> _WalkState:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if "content.xml" not in names:
                raise OccurrenceExtractError(
                    "missing_content_xml",
                    f"{relative_path}: ODT missing content.xml",
                )
            try:
                content = zf.read("content.xml")
            except Exception as exc:  # noqa: BLE001
                raise OccurrenceExtractError(
                    "unreadable_content_xml",
                    f"{relative_path}: {type(exc).__name__}:{exc}",
                ) from exc
    except zipfile.BadZipFile as exc:
        raise OccurrenceExtractError(
            "invalid_odt_zip",
            f"{relative_path}: bad zip: {exc}",
        ) from exc

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise OccurrenceExtractError(
            "xml_parse_error",
            f"{relative_path}: {exc}",
        ) from exc

    xml_indices = assign_xml_element_indices(root)
    total_ph, nested_ph = count_ph_elements(root)
    text_el = find_office_text(root)
    state = _WalkState(artifact_sha, relative_path, xml_indices)
    state.xml_ph_element_count = total_ph
    state.xml_ph_nested_count = nested_ph
    for child in text_el:
        _walk_flow_node(child, state)

    unique_emitted = len(state.emitted_ph_xml_ids)
    unaccounted = total_ph - unique_emitted
    state.source_structural_ph_emissions = unique_emitted
    if unaccounted != 0 or unique_emitted != total_ph:
        raise OccurrenceExtractError(
            "structural_text_coverage_incomplete",
            (
                f"{relative_path}: xml_ph_total={total_ph} "
                f"nested_ph={nested_ph} unique_emitted={unique_emitted} "
                f"unaccounted={unaccounted}"
            ),
        )
    return state


# ---------------------------------------------------------------------------
# Occurrence detection
# ---------------------------------------------------------------------------


def blocks_to_occurrences(
    blocks: Sequence[Mapping[str, Any]],
    *,
    relative_path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Detect source-local occurrence candidates from structural blocks.

    Returns (occurrences, issues, numeric_rejection_counts_by_code).
    Structural blocks are never deleted; rejected numeric heads stay as blocks
    only and produce machine-readable rejection issues (no source prose).
    """
    occurrences: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    desig_to_ids: dict[str, list[str]] = defaultdict(list)
    rejection_counts: Counter[str] = Counter()

    for block in blocks:
        if block.get("block_kind") == "empty_table_cell":
            continue
        det = detect_designation(block["raw_text"])
        if det is None:
            continue
        desig = det["designation_text"]
        rejection = classify_non_rule_numeric_head(
            desig, det.get("remainder_after_designation", "")
        )
        if rejection is not None:
            code = rejection["rejection_code"]
            rejection_counts[code] += 1
            issues.append(
                cp.make_issue(
                    issue_code="numeric_quantity_rejected_from_occurrence",
                    severity=cp.SEVERITY_INFO,
                    relative_path=relative_path,
                    detail=(
                        f"designation {desig!r} classified as non-rule numeric "
                        f"({code}); structural block retained; not an occurrence"
                    ),
                    issue_class="numeric_quantity_filter",
                    designation_text=desig,
                    rejection_code=code,
                    locator_key=block["locator_key"],
                    xml_element_index=block.get("xml_element_index"),
                    block_id=block["block_id"],
                    first_segment=rejection.get("first_segment"),
                    unit_token=rejection.get("unit_token"),
                )
            )
            continue

        ambiguity: list[str] = []
        if det["leading_stripped"]:
            ambiguity.append("leading_whitespace_or_punctuation_stripped")
        if block["in_table"]:
            ambiguity.append("in_table_cell")
        if block["locator"].get("nested_table_depth", 0):
            ambiguity.append("nested_table_context")
        if block["locator"].get("list_depth", 0):
            ambiguity.append("in_list")
        if block["locator"].get("in_frame", 0):
            ambiguity.append("in_frame")
        in_index = bool(
            block.get("in_index_context")
            or block["locator"].get("in_index_context", 0)
        )
        if in_index:
            ambiguity.append("in_index_context")
        ambiguity.append("source_local_candidate_only")

        occ_id = _occurrence_id(
            block["artifact_sha256"],
            block["locator_key"],
            block["raw_text_sha256"],
        )
        raw = block["raw_text"]
        raw_b = raw.encode("utf-8")
        if len(raw_b) != block["raw_text_byte_length"]:
            raise OccurrenceExtractError(
                "raw_text_length_mismatch",
                f"{relative_path}: block {block['block_id']}",
            )
        if len(raw) != block["raw_text_char_length"]:
            raise OccurrenceExtractError(
                "raw_text_char_length_mismatch",
                f"{relative_path}: block {block['block_id']}",
            )
        if cp.sha256_bytes(raw_b) != block["raw_text_sha256"]:
            raise OccurrenceExtractError(
                "raw_text_hash_mismatch",
                f"{relative_path}: block {block['block_id']}",
            )

        occ = {
            "schema": SCHEMA_OCCURRENCE,
            "occurrence_id": occ_id,
            "artifact_sha256": block["artifact_sha256"],
            "relative_path": relative_path,
            "designation_text": desig,
            "block_id": block["block_id"],
            "locator": block["locator"],
            "locator_key": block["locator_key"],
            "raw_text": raw,
            "normalized_search_text": block["normalized_search_text"],
            "raw_text_sha256": block["raw_text_sha256"],
            "raw_text_byte_length": block["raw_text_byte_length"],
            "raw_text_char_length": block["raw_text_char_length"],
            "parser_version": PARSER_VERSION,
            "ambiguity_flags": sorted(set(ambiguity)),
            "container": block["container"],
            "match_start_in_raw": det["match_start_in_raw"],
            "match_end_in_raw": det["match_end_in_raw"],
            "in_index_context": in_index,
            "statement": (
                "source-local rule-occurrence candidate; "
                "NOT stable rule identity, legal effective date, "
                "predecessor/successor, or diff"
            ),
        }
        occurrences.append(occ)
        desig_to_ids[desig].append(occ_id)

    for desig, ids in sorted(desig_to_ids.items()):
        if len(ids) > 1:
            for oid in ids:
                for occ in occurrences:
                    if occ["occurrence_id"] == oid:
                        flags = list(occ["ambiguity_flags"])
                        if "duplicate_designation_in_release" not in flags:
                            flags.append("duplicate_designation_in_release")
                        occ["ambiguity_flags"] = sorted(set(flags))
            issues.append(
                cp.make_issue(
                    issue_code="duplicate_designation_within_release",
                    severity=cp.SEVERITY_INFO,
                    relative_path=relative_path,
                    detail=(
                        f"designation {desig!r} occurs {len(ids)} times in this "
                        "release; all retained (no first-wins dedup)"
                    ),
                    issue_class="content_ambiguity",
                    designation_text=desig,
                    occurrence_count=len(ids),
                    occurrence_ids=ids,
                )
            )
    return occurrences, issues, dict(rejection_counts)


# ---------------------------------------------------------------------------
# Manifest / release inventory
# ---------------------------------------------------------------------------


def load_accepted_history_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """Load accepted profiler manifest; history-lane rows by relative_path.

    Fail closed on duplicate relative_path rows (no silent overwrite).
    """
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("lane") != "history":
                continue
            rel = row["relative_path"]
            if rel in rows:
                raise OccurrenceExtractError(
                    "duplicate_manifest_relative_path",
                    f"{path}: duplicate history relative_path {rel!r} "
                    f"(line {line_no})",
                )
            rows[rel] = row
    return rows


def list_history_odt(
    history_dir: Path,
    *,
    accepted_basenames: set[str] | None = None,
) -> list[Path]:
    """Inventory every directory entry; accept only regular non-symlink ODTs.

    Fail closed on symlinks, non-regular entries, unsupported extensions,
    extra artifacts, and basenames not represented exactly once in the
    accepted manifest (when provided).
    """
    if not history_dir.is_dir():
        raise OccurrenceExtractError(
            "history_dir_missing",
            f"not a directory: {history_dir}",
        )

    try:
        entries = sorted(history_dir.iterdir(), key=lambda p: p.name.encode("utf-8"))
    except OSError as exc:
        raise OccurrenceExtractError(
            "history_dir_unreadable",
            f"{history_dir}: {exc}",
        ) from exc

    files: list[Path] = []
    seen_basenames: set[str] = set()
    for entry in entries:
        # lstat to detect symlinks even when target is a regular file.
        try:
            st = entry.lstat()
        except OSError as exc:
            raise OccurrenceExtractError(
                "history_entry_unreadable",
                f"{entry.name}: {exc}",
            ) from exc
        if entry.is_symlink() or statmod.S_ISLNK(st.st_mode):
            raise OccurrenceExtractError(
                "history_symlink_forbidden",
                f"symlink not allowed in history dir: {entry.name}",
            )
        if not statmod.S_ISREG(st.st_mode):
            raise OccurrenceExtractError(
                "history_non_regular_entry",
                f"non-regular entry not allowed: {entry.name}",
            )
        suffix = entry.suffix.lower()
        if suffix != ".odt":
            raise OccurrenceExtractError(
                "history_unsupported_artifact",
                f"unsupported or extra artifact in history dir: {entry.name}",
            )
        if entry.name in seen_basenames:
            raise OccurrenceExtractError(
                "history_duplicate_basename",
                f"duplicate basename in history dir: {entry.name}",
            )
        seen_basenames.add(entry.name)
        files.append(entry)

    if accepted_basenames is not None:
        if seen_basenames != accepted_basenames:
            missing = sorted(accepted_basenames - seen_basenames)
            extra = sorted(seen_basenames - accepted_basenames)
            raise OccurrenceExtractError(
                "history_artifact_set_mismatch",
                f"missing={missing!r} extra={extra!r}",
            )
        # Each accepted basename must appear exactly once (already enforced).
        for bn in accepted_basenames:
            if sum(1 for p in files if p.name == bn) != 1:
                raise OccurrenceExtractError(
                    "history_basename_not_unique",
                    f"basename not represented exactly once: {bn}",
                )
    return files


def _sanitize_analysis_chronology(chrono: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy profiler chronology fields without any effective_date-named keys."""
    if not chrono:
        return {
            "analysis_sort_key": None,
            "roc_year": None,
            "roc_month": None,
            "parse_status": "absent",
            "legal_date_inferred": False,
            "statement": (
                "analysis-only chronology candidate from filename label; "
                "NOT a legal effective date"
            ),
        }
    # Drop/rename profiler keys that contain the substring effective_date.
    out: dict[str, Any] = {}
    for k, v in chrono.items():
        if "effective_date" in k:
            # Preserve the negation intent under a safe key name.
            out["legal_date_inferred"] = False
            continue
        out[k] = v
    if "legal_date_inferred" not in out:
        out["legal_date_inferred"] = False
    return out


def build_release_row(
    *,
    path: Path,
    data: bytes,
    source_order_index: int,
    state: _WalkState,
    occurrence_count: int,
    accepted: Mapping[str, Any] | None,
    numeric_rejection_count: int,
) -> dict[str, Any]:
    basename = path.name
    rel = f"history/{basename}"
    sha = cp.sha256_bytes(data)
    label_info = cp.parse_filename_label(basename)
    chrono = _sanitize_analysis_chronology(
        cp.parse_historical_release_sort_key(label_info.get("filename_label_raw"))
    )
    unaccounted = state.xml_ph_element_count - len(state.emitted_ph_xml_ids)
    row = {
        "schema": SCHEMA_RELEASE,
        "release_id": sha,
        "relative_path": rel,
        "basename": basename,
        "sha256": sha,
        "byte_length": len(data),
        "filename_label_raw": label_info.get("filename_label_raw"),
        "filename_id_prefix": label_info.get("filename_id_prefix"),
        "filename_date_fragments_raw": label_info.get("filename_date_fragments_raw"),
        "analysis_chronology": chrono,
        "source_order_index": source_order_index,
        "parser_version": PARSER_VERSION,
        "block_count": len(state.blocks),
        "occurrence_count": occurrence_count,
        "table_count": state.table_count,
        "row_count_xml": state.row_count_xml,
        "cell_count_xml": state.cell_count_xml,
        "row_count_logical": state.row_count_logical,
        "cell_count_logical": state.cell_count_logical,
        "empty_cell_count": state.empty_cell_count,
        "nested_table_count": state.nested_table_count,
        "odt_repeat_attrs_present": state.repeat_attrs_present,
        "statement": (
            "source-local release observation from historical ODT artifact; "
            "filename chronology is analysis-only; NOT a legal effective date"
        ),
        "xml_ph_element_count": state.xml_ph_element_count,
        "xml_ph_nested_count": state.xml_ph_nested_count,
        "xml_ph_emitted_unique": len(state.emitted_ph_xml_ids),
        "xml_ph_unaccounted": unaccounted,
        "source_structural_block_count_before_repeat_expansion": (
            state.xml_ph_element_count
        ),
        "empty_table_cell_block_count": state.empty_table_cell_block_count,
        "numeric_quantity_rejection_count": numeric_rejection_count,
    }
    if accepted is not None:
        row["accepted_manifest_sha256"] = accepted.get("sha256")
        row["accepted_manifest_match"] = accepted.get("sha256") == sha
    # Fail closed if any residual legal-date field slipped in.
    for k in row:
        if "effective_date" in k:
            raise OccurrenceExtractError(
                "legal_effective_date_field_forbidden",
                f"release row must not contain key {k!r}",
            )
    return row


# ---------------------------------------------------------------------------
# Tracked receipt projection (allowlist fail-closed)
# ---------------------------------------------------------------------------


def _project_allowlist(
    row: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    path: str,
    stage_only: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    unknown = set(row.keys()) - allowed - stage_only
    if unknown:
        raise OccurrenceExtractError(
            "tracked_schema_unknown_field",
            f"{path}: unknown field(s) not on allowlist: {sorted(unknown)}",
        )
    return {k: row[k] for k in sorted(allowed) if k in row}


def strip_for_tracked_release(row: Mapping[str, Any]) -> dict[str, Any]:
    out = _project_allowlist(row, TRACKED_RELEASE_KEYS, path="release")
    for k in out:
        if "effective_date" in k:
            raise OccurrenceExtractError(
                "legal_effective_date_field_forbidden",
                f"tracked release must not contain key {k!r}",
            )
    return out


def strip_for_tracked_occurrence(row: Mapping[str, Any]) -> dict[str, Any]:
    return _project_allowlist(
        row,
        TRACKED_OCCURRENCE_KEYS,
        path="occurrence",
        stage_only=STAGE_ONLY_OCCURRENCE_KEYS,
    )


def strip_for_tracked_canary(row: Mapping[str, Any]) -> dict[str, Any]:
    return _project_allowlist(row, TRACKED_CANARY_KEYS, path="canary")


def strip_for_tracked_issue(row: Mapping[str, Any]) -> dict[str, Any]:
    return _project_allowlist(row, TRACKED_ISSUE_KEYS, path="issue")


def strip_for_tracked_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return _project_allowlist(row, TRACKED_SUMMARY_KEYS, path="summary")


# Fixed machine-written disclaimer fields may be moderately long English, but
# still must not carry absolute paths or source-body samples.
_PROSE_SCAN_EXEMPT_KEYS = frozenset({"statement", "detail", "notes"})


def assert_no_tracked_leakage(obj: Any, *, path: str) -> None:
    """Fail closed if tracked receipt contains prose leakage or abs paths."""

    def walk(node: Any, breadcrumb: str, *, parent_key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                lk = k.lower()
                if "effective_date" in lk:
                    # Any key whose name contains effective_date is banned in
                    # tracked/release observations (including nested chronology).
                    raise OccurrenceExtractError(
                        "legal_effective_date_field_forbidden",
                        f"{path}: forbidden key {k!r} at {breadcrumb}",
                    )
                # Known full-text field names never allowed in tracked objects.
                if k in STAGE_ONLY_OCCURRENCE_KEYS or lk in {
                    "raw_text",
                    "normalized_search_text",
                    "normalized_text",
                    "full_text",
                    "excerpt",
                    "rule_text",
                    "body",
                    "prose",
                    "content",
                    "text",
                    "sample",
                    "text_sample",
                    "extracted_text",
                }:
                    raise OccurrenceExtractError(
                        "tracked_receipt_text_leakage",
                        f"{path}: forbidden key {k!r} at {breadcrumb}",
                    )
                if k in {
                    "creator",
                    "author",
                    "company",
                    "generator",
                    "hostname",
                    "host",
                    "mtime",
                    "ctime",
                    "absolute_path",
                    "abs_path",
                    "source_path",
                }:
                    raise OccurrenceExtractError(
                        "tracked_receipt_meta_leakage",
                        f"{path}: forbidden meta key {k!r} at {breadcrumb}",
                    )
                walk(v, f"{breadcrumb}.{k}", parent_key=k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{breadcrumb}[{i}]", parent_key=parent_key)
        elif isinstance(node, str):
            for marker in _ABS_PATH_MARKERS:
                if marker in node:
                    raise OccurrenceExtractError(
                        "tracked_receipt_absolute_path",
                        f"{path}: absolute/local path marker at {breadcrumb}",
                    )
            # Fixed disclaimer fields: path-scan only; still bound extreme length.
            if parent_key in _PROSE_SCAN_EXEMPT_KEYS:
                if len(node) > 400:
                    raise OccurrenceExtractError(
                        "tracked_receipt_prose_sample",
                        f"{path}: disclaimer field too long at {breadcrumb} "
                        f"(len={len(node)})",
                    )
                return
            # Reject long source-like prose even below the old 500-char threshold.
            cjk = sum(1 for ch in node if "\u4e00" <= ch <= "\u9fff")
            if cjk >= 40 and len(node) >= 80:
                raise OccurrenceExtractError(
                    "tracked_receipt_prose_sample",
                    f"{path}: CJK prose-like string at {breadcrumb} "
                    f"(len={len(node)}, cjk={cjk})",
                )
            # Long English source-like text (not short designations / codes).
            if len(node) >= 120:
                letters = sum(
                    1 for ch in node if ("a" <= ch <= "z") or ("A" <= ch <= "Z")
                )
                spaces = node.count(" ")
                if letters >= 80 and spaces >= 8:
                    raise OccurrenceExtractError(
                        "tracked_receipt_prose_sample",
                        f"{path}: English prose-like string at {breadcrumb} "
                        f"(len={len(node)})",
                    )

    walk(obj, "$")


def assert_markdown_report_safe(text: str, *, path: str) -> None:
    for marker in _ABS_PATH_MARKERS:
        if marker in text:
            raise OccurrenceExtractError(
                "tracked_report_absolute_path",
                f"{path}: absolute/local path marker in quality report",
            )
    lowered = text.lower()
    for bad in ("creator:", "author:", "password", "postgresql", "host="):
        if bad in lowered:
            raise OccurrenceExtractError(
                "tracked_report_meta_leakage",
                f"{path}: prohibited metadata/credential marker {bad!r}",
            )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def assert_stage_dir_allowed(
    stage_dir: Path,
    *,
    allow_unrestricted: bool = False,
    stage_root: Path | None = None,
) -> None:
    """Require stage_dir under the checkout-local stage root.

    ``allow_unrestricted`` is for tests only (call the Python API directly).
    The production CLI never exposes this bypass.
    """
    if allow_unrestricted:
        return
    resolved = stage_dir.resolve()
    root = (stage_root or default_stage_root()).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        if resolved != root:
            raise OccurrenceExtractError(
                "unsafe_stage_dir",
                f"stage-dir {resolved} is not under required stage root {root}",
            ) from exc


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def extract_history_corpus(
    *,
    history_dir: Path,
    accepted_manifest: Path,
    stage_dir: Path,
    receipt_dir: Path,
    canaries: Sequence[str] = DEFAULT_CANARIES,
    allow_unrestricted_stage: bool = False,
) -> dict[str, Any]:
    """Extract occurrences from history ODTs.

    ``allow_unrestricted_stage`` is a test-only Python API override. The CLI
    never sets it; production always requires checkout-local staging.
    """
    assert_stage_dir_allowed(
        stage_dir, allow_unrestricted=allow_unrestricted_stage
    )

    if not history_dir.is_dir():
        raise OccurrenceExtractError(
            "history_dir_missing",
            f"not a directory: {history_dir}",
        )

    accepted = load_accepted_history_manifest(accepted_manifest)
    if len(accepted) != EXPECTED_HISTORY_COUNT:
        raise OccurrenceExtractError(
            "accepted_manifest_history_count",
            f"expected {EXPECTED_HISTORY_COUNT} history rows in accepted manifest, "
            f"got {len(accepted)}",
        )

    accepted_basenames = {Path(r["relative_path"]).name for r in accepted.values()}
    files = list_history_odt(
        history_dir, accepted_basenames=accepted_basenames
    )
    if len(files) != EXPECTED_HISTORY_COUNT:
        raise OccurrenceExtractError(
            "history_artifact_count",
            f"expected exactly {EXPECTED_HISTORY_COUNT} history ODT files, "
            f"found {len(files)}",
        )

    stage_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    releases: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    all_occurrences: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    rejection_totals: Counter[str] = Counter()

    for source_order_index, path in enumerate(files):
        rel = f"history/{path.name}"
        data = path.read_bytes()
        sha = cp.sha256_bytes(data)
        acc = accepted.get(rel)
        if acc is None:
            raise OccurrenceExtractError(
                "accepted_manifest_path_missing",
                f"{rel} not in accepted manifest",
            )
        if acc.get("sha256") != sha:
            raise OccurrenceExtractError(
                "source_sha_mismatch",
                f"{rel}: file sha256={sha} accepted={acc.get('sha256')}",
            )

        state = parse_odt_blocks(data, artifact_sha=sha, relative_path=rel)
        occurrences, occ_issues, rej_counts = blocks_to_occurrences(
            state.blocks, relative_path=rel
        )
        for k, v in rej_counts.items():
            rejection_totals[k] += v
        numeric_rej = sum(rej_counts.values())

        blocks_by_id = {b["block_id"]: b for b in state.blocks}
        blocks_by_locator = {b["locator_key"]: b for b in state.blocks}
        for occ in occurrences:
            b = blocks_by_id.get(occ["block_id"])
            if b is None:
                raise OccurrenceExtractError(
                    "occurrence_block_unresolved",
                    f"{rel}: occurrence {occ['occurrence_id']} block missing",
                )
            b2 = blocks_by_locator.get(occ["locator_key"])
            if b2 is None or b2["block_id"] != b["block_id"]:
                raise OccurrenceExtractError(
                    "occurrence_locator_mismatch",
                    f"{rel}: occurrence locator does not resolve uniquely",
                )
            if b["raw_text_sha256"] != occ["raw_text_sha256"]:
                raise OccurrenceExtractError(
                    "occurrence_block_hash_mismatch",
                    f"{rel}: occurrence/block hash mismatch",
                )
            if b["raw_text_byte_length"] != occ["raw_text_byte_length"]:
                raise OccurrenceExtractError(
                    "occurrence_block_length_mismatch",
                    f"{rel}: occurrence/block length mismatch",
                )

        release = build_release_row(
            path=path,
            data=data,
            source_order_index=source_order_index,
            state=state,
            occurrence_count=len(occurrences),
            accepted=acc,
            numeric_rejection_count=numeric_rej,
        )
        releases.append(release)
        all_blocks.extend(state.blocks)
        all_occurrences.extend(occurrences)
        all_issues.extend(state.issues)
        all_issues.extend(occ_issues)
        if state.empty_cell_count:
            all_issues.append(
                cp.make_issue(
                    issue_code="empty_table_cells_present",
                    severity=cp.SEVERITY_INFO,
                    relative_path=rel,
                    detail=(
                        f"{state.empty_cell_count} empty/covered table cell "
                        f"instances emitted as empty_table_cell structural "
                        f"blocks with locators; spans preserved"
                    ),
                    issue_class="layout",
                    empty_cell_count=state.empty_cell_count,
                    table_count=state.table_count,
                )
            )

    block_count = len(all_blocks)
    occ_count = len(all_occurrences)
    if sum(r["block_count"] for r in releases) != block_count:
        raise OccurrenceExtractError(
            "count_reconciliation_blocks",
            "release block_count sum != total blocks",
        )
    if sum(r["occurrence_count"] for r in releases) != occ_count:
        raise OccurrenceExtractError(
            "count_reconciliation_occurrences",
            "release occurrence_count sum != total occurrences",
        )
    if sum(r["xml_ph_unaccounted"] for r in releases) != 0:
        raise OccurrenceExtractError(
            "structural_text_coverage_incomplete",
            "aggregate xml_ph_unaccounted != 0",
        )

    canary_rows: list[dict[str, Any]] = []
    canary_hit_counts: dict[str, int] = {}
    for canary in canaries:
        hits = [o for o in all_occurrences if o["designation_text"] == canary]
        canary_hit_counts[canary] = len(hits)
        if not hits:
            all_issues.append(
                cp.make_issue(
                    issue_code="canary_zero_hits",
                    severity=cp.SEVERITY_INFO,
                    relative_path=None,
                    detail=(
                        f"canary designation {canary!r} has zero source-local "
                        "occurrence candidates across 14 history releases"
                    ),
                    issue_class="canary",
                    canary=canary,
                )
            )
        for h in hits:
            canary_rows.append(
                {
                    "canary": canary,
                    "occurrence_id": h["occurrence_id"],
                    "artifact_sha256": h["artifact_sha256"],
                    "relative_path": h["relative_path"],
                    "designation_text": h["designation_text"],
                    "locator_key": h["locator_key"],
                    "locator": h["locator"],
                    "container": h["container"],
                    "raw_text_sha256": h["raw_text_sha256"],
                    "raw_text_byte_length": h["raw_text_byte_length"],
                    "raw_text_char_length": h["raw_text_char_length"],
                    "parser_version": PARSER_VERSION,
                    "in_index_context": h.get("in_index_context", False),
                    "statement": (
                        "canary source-local occurrence locator only; "
                        "NOT identity, legal date, or diff"
                    ),
                }
            )

    all_issues = sorted(all_issues, key=cp.issue_sort_key)

    flow_occ = sum(1 for o in all_occurrences if o["container"] == "flow")
    table_occ = sum(1 for o in all_occurrences if o["container"] == "table_cell")
    frame_occ = sum(1 for o in all_occurrences if o["container"] == "frame")
    index_occ = sum(1 for o in all_occurrences if o["container"] == "index")
    other_occ = occ_count - flow_occ - table_occ - frame_occ - index_occ

    dup_desig_rows = []
    for rel_path in sorted({r["relative_path"] for r in releases}):
        counts: Counter[str] = Counter(
            o["designation_text"]
            for o in all_occurrences
            if o["relative_path"] == rel_path
        )
        for desig, n in sorted(counts.items()):
            if n > 1:
                dup_desig_rows.append(
                    {
                        "relative_path": rel_path,
                        "designation_text": desig,
                        "occurrence_count": n,
                    }
                )

    chrono_candidates = []
    for r in releases:
        ch = r["analysis_chronology"]
        chrono_candidates.append(
            {
                "relative_path": r["relative_path"],
                "filename_label_raw": r["filename_label_raw"],
                "sha256": r["sha256"],
                "analysis_sort_key": ch.get("analysis_sort_key"),
                "roc_year": ch.get("roc_year"),
                "roc_month": ch.get("roc_month"),
                "parse_status": ch.get("parse_status"),
                "legal_date_inferred": False,
                "statement": ch.get("statement"),
            }
        )
    chrono_sorted = sorted(
        chrono_candidates,
        key=lambda x: (
            x.get("analysis_sort_key") or "\uffff",
            x["relative_path"].encode("utf-8"),
        ),
    )

    summary = {
        "schema": SCHEMA_SUMMARY,
        "parser_version": PARSER_VERSION,
        "deterministic": True,
        "release_count": len(releases),
        "expected_release_count": EXPECTED_HISTORY_COUNT,
        "release_count_match": len(releases) == EXPECTED_HISTORY_COUNT,
        "all_source_sha_match_accepted_manifest": all(
            r.get("accepted_manifest_match") for r in releases
        ),
        "block_count": block_count,
        "occurrence_count": occ_count,
        "occurrence_container_counts": {
            "flow": flow_occ,
            "table_cell": table_occ,
            "frame": frame_occ,
            "index": index_occ,
            "other": other_occ,
        },
        "table_count_total": sum(r["table_count"] for r in releases),
        "row_count_xml_total": sum(r["row_count_xml"] for r in releases),
        "cell_count_xml_total": sum(r["cell_count_xml"] for r in releases),
        "row_count_logical_total": sum(r["row_count_logical"] for r in releases),
        "cell_count_logical_total": sum(r["cell_count_logical"] for r in releases),
        "empty_cell_count_total": sum(r["empty_cell_count"] for r in releases),
        "nested_table_count_total": sum(r["nested_table_count"] for r in releases),
        "xml_ph_element_count_total": sum(
            r["xml_ph_element_count"] for r in releases
        ),
        "xml_ph_nested_count_total": sum(r["xml_ph_nested_count"] for r in releases),
        "xml_ph_emitted_unique_total": sum(
            r["xml_ph_emitted_unique"] for r in releases
        ),
        "xml_ph_unaccounted_total": sum(r["xml_ph_unaccounted"] for r in releases),
        "empty_table_cell_block_count_total": sum(
            r["empty_table_cell_block_count"] for r in releases
        ),
        "numeric_quantity_rejection_count_total": sum(
            r["numeric_quantity_rejection_count"] for r in releases
        ),
        "numeric_quantity_rejection_by_code": {
            k: rejection_totals[k] for k in sorted(rejection_totals.keys())
        },
        "canary_hit_counts": {k: canary_hit_counts[k] for k in canaries},
        "canary_hit_total": sum(canary_hit_counts.values()),
        "duplicate_designation_groups": dup_desig_rows,
        "duplicate_designation_group_count": len(dup_desig_rows),
        "issue_count": len(all_issues),
        "issue_codes": dict(Counter(i["issue_code"] for i in all_issues)),
        "issue_severity_counts": dict(Counter(i["severity"] for i in all_issues)),
        "source_order_release_sequence": [
            {
                "source_order_index": r["source_order_index"],
                "relative_path": r["relative_path"],
                "filename_label_raw": r["filename_label_raw"],
                "sha256": r["sha256"],
                "byte_length": r["byte_length"],
            }
            for r in releases
        ],
        "analysis_only_chronology_sequence": chrono_sorted,
        "per_release_counts": [
            {
                "relative_path": r["relative_path"],
                "sha256": r["sha256"],
                "block_count": r["block_count"],
                "occurrence_count": r["occurrence_count"],
                "table_count": r["table_count"],
                "row_count_xml": r["row_count_xml"],
                "cell_count_xml": r["cell_count_xml"],
                "row_count_logical": r["row_count_logical"],
                "cell_count_logical": r["cell_count_logical"],
                "xml_ph_element_count": r["xml_ph_element_count"],
                "xml_ph_unaccounted": r["xml_ph_unaccounted"],
                "empty_table_cell_block_count": r["empty_table_cell_block_count"],
                "numeric_quantity_rejection_count": r[
                    "numeric_quantity_rejection_count"
                ],
            }
            for r in releases
        ],
        "notes": [
            "Results are source-local candidates only.",
            "No stable cross-release rule identity is assigned.",
            "No legal effective dates are inferred.",
            "No cross-release diffs are computed.",
            "Two-column table cells remain distinct structural blocks.",
            "Duplicate designations within a release are retained (no first-wins).",
            "Grouping across consecutive flow paragraphs is not performed.",
            "Every source text:p/text:h is emitted once before logical expansion.",
            "Empty/covered table cells are locatable empty_table_cell blocks.",
            "Obvious numeric quantities are rejected from occurrence candidacy.",
            f"MAX_DESIGNATION_FIRST_SEGMENT={MAX_DESIGNATION_FIRST_SEGMENT}.",
        ],
        "canonical_rule_history_promoted": False,
        "cross_release_identity_inferred": False,
        "legal_dates_inferred": False,
        "cross_release_diffs_computed": False,
        "max_designation_first_segment": MAX_DESIGNATION_FIRST_SEGMENT,
    }

    # ---- Write staged full-text outputs ----
    stage_releases = stage_dir / "releases.jsonl"
    stage_blocks = stage_dir / "blocks.jsonl"
    stage_occs = stage_dir / "occurrences.jsonl"
    cp.write_jsonl(stage_releases, releases)
    cp.write_jsonl(stage_blocks, all_blocks)
    cp.write_jsonl(stage_occs, all_occurrences)

    # ---- Tracked receipts (allowlist projection; fail closed) ----
    tracked_releases = [strip_for_tracked_release(r) for r in releases]
    tracked_occs = [strip_for_tracked_occurrence(o) for o in all_occurrences]
    tracked_canaries = [strip_for_tracked_canary(c) for c in canary_rows]
    tracked_issues = [strip_for_tracked_issue(i) for i in all_issues]
    tracked_summary = strip_for_tracked_summary(summary)

    for label, rows in (
        ("release-index", tracked_releases),
        ("occurrence-index", tracked_occs),
        ("canary-occurrences", tracked_canaries),
        ("issues", tracked_issues),
    ):
        for row in rows:
            assert_no_tracked_leakage(row, path=label)
    assert_no_tracked_leakage(tracked_summary, path="summary")

    cp.write_jsonl(receipt_dir / "release-index.jsonl", tracked_releases)
    cp.write_jsonl(receipt_dir / "occurrence-index.jsonl", tracked_occs)
    cp.write_jsonl(receipt_dir / "canary-occurrences.jsonl", tracked_canaries)
    cp.write_jsonl(receipt_dir / "issues.jsonl", tracked_issues)
    cp.write_json(receipt_dir / "summary.json", tracked_summary)

    quality = render_quality_report(
        tracked_summary, tracked_issues, tracked_canaries
    )
    assert_markdown_report_safe(quality, path="quality-report.md")
    (receipt_dir / "quality-report.md").write_text(quality, encoding="utf-8")

    digests = {
        "stage/releases.jsonl": cp.sha256_bytes(stage_releases.read_bytes()),
        "stage/blocks.jsonl": cp.sha256_bytes(stage_blocks.read_bytes()),
        "stage/occurrences.jsonl": cp.sha256_bytes(stage_occs.read_bytes()),
        "receipt/release-index.jsonl": cp.sha256_bytes(
            (receipt_dir / "release-index.jsonl").read_bytes()
        ),
        "receipt/occurrence-index.jsonl": cp.sha256_bytes(
            (receipt_dir / "occurrence-index.jsonl").read_bytes()
        ),
        "receipt/canary-occurrences.jsonl": cp.sha256_bytes(
            (receipt_dir / "canary-occurrences.jsonl").read_bytes()
        ),
        "receipt/issues.jsonl": cp.sha256_bytes(
            (receipt_dir / "issues.jsonl").read_bytes()
        ),
        "receipt/summary.json": cp.sha256_bytes(
            (receipt_dir / "summary.json").read_bytes()
        ),
        "receipt/quality-report.md": cp.sha256_bytes(
            (receipt_dir / "quality-report.md").read_bytes()
        ),
    }
    return {
        "summary": tracked_summary,
        "digests": digests,
        "release_count": len(releases),
        "block_count": block_count,
        "occurrence_count": occ_count,
        "issue_count": len(all_issues),
    }


def render_quality_report(
    summary: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
    canary_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Render Markdown only from already-allowlisted objects."""
    lines: list[str] = []
    lines.append("# NHI history occurrence extraction — quality report")
    lines.append("")
    lines.append(f"Parser version: `{summary['parser_version']}`")
    lines.append("")
    lines.append("## Scope statement")
    lines.append("")
    lines.append(
        "All results are **source-local rule-occurrence candidates**. "
        "This report does **not** assign stable cross-release rule identity, "
        "legal effective dates, predecessor/successor edges, or diffs."
    )
    lines.append("")
    lines.append("## Release inventory")
    lines.append("")
    lines.append(
        f"- Release count: **{summary['release_count']}** "
        f"(expected {summary['expected_release_count']}; "
        f"match={summary['release_count_match']})"
    )
    lines.append(
        f"- All source SHA-256 match accepted profiler manifest: "
        f"**{summary['all_source_sha_match_accepted_manifest']}**"
    )
    lines.append("")
    lines.append("### Bytewise source order (filename sort)")
    lines.append("")
    for row in summary["source_order_release_sequence"]:
        lines.append(
            f"- `{row['relative_path']}` label=`{row['filename_label_raw']}` "
            f"sha256=`{row['sha256'][:12]}…`"
        )
    lines.append("")
    lines.append("### Analysis-only filename chronology (NOT legal dates)")
    lines.append("")
    for row in summary["analysis_only_chronology_sequence"]:
        lines.append(
            f"- sort=`{row['analysis_sort_key']}` `{row['relative_path']}` "
            f"label=`{row['filename_label_raw']}`"
        )
    lines.append("")
    lines.append("## Structural + occurrence counts")
    lines.append("")
    lines.append(f"- Structural blocks (after logical expansion): **{summary['block_count']}**")
    lines.append(
        f"- Source XML text:p/text:h total: **{summary['xml_ph_element_count_total']}** "
        f"(nested={summary['xml_ph_nested_count_total']}; "
        f"unique_emitted={summary['xml_ph_emitted_unique_total']}; "
        f"unaccounted={summary['xml_ph_unaccounted_total']})"
    )
    lines.append(
        f"- Empty table cell blocks: **{summary['empty_table_cell_block_count_total']}**"
    )
    lines.append(f"- Occurrence candidates: **{summary['occurrence_count']}**")
    lines.append(
        f"- Numeric quantity rejections: "
        f"**{summary['numeric_quantity_rejection_count_total']}** "
        f"by_code=`{summary['numeric_quantity_rejection_by_code']}`"
    )
    occ_cc = summary["occurrence_container_counts"]
    lines.append(
        f"- By container: flow={occ_cc.get('flow', 0)}, "
        f"table_cell={occ_cc.get('table_cell', 0)}, "
        f"frame={occ_cc.get('frame', 0)}, "
        f"index={occ_cc.get('index', 0)}, "
        f"other={occ_cc.get('other', 0)}"
    )
    lines.append(
        f"- Tables (total): {summary['table_count_total']}; "
        f"rows_xml={summary['row_count_xml_total']}; "
        f"cells_xml={summary['cell_count_xml_total']}; "
        f"rows_logical={summary['row_count_logical_total']}; "
        f"cells_logical={summary['cell_count_logical_total']}"
    )
    lines.append(
        f"- Empty cells (expanded): {summary['empty_cell_count_total']}; "
        f"nested tables: {summary['nested_table_count_total']}"
    )
    lines.append("")
    lines.append("### Per-release counts")
    lines.append("")
    lines.append(
        "| relative_path | blocks | occurrences | tables | rows_xml | cells_xml | xml_ph | empty_cells |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in summary["per_release_counts"]:
        lines.append(
            f"| `{row['relative_path']}` | {row['block_count']} | "
            f"{row['occurrence_count']} | {row['table_count']} | "
            f"{row['row_count_xml']} | {row['cell_count_xml']} | "
            f"{row.get('xml_ph_element_count', '')} | "
            f"{row.get('empty_table_cell_block_count', '')} |"
        )
    lines.append("")
    lines.append("## Duplicate designations within a release")
    lines.append("")
    lines.append(
        f"Groups: **{summary['duplicate_designation_group_count']}** "
        "(all occurrences retained; no first-wins dedup)"
    )
    lines.append("")
    if summary["duplicate_designation_groups"]:
        for g in summary["duplicate_designation_groups"][:50]:
            lines.append(
                f"- `{g['relative_path']}` designation=`{g['designation_text']}` "
                f"count={g['occurrence_count']}"
            )
        if len(summary["duplicate_designation_groups"]) > 50:
            lines.append(
                f"- … {len(summary['duplicate_designation_groups']) - 50} more groups"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Canary occurrence counts and locators")
    lines.append("")
    for canary, n in summary["canary_hit_counts"].items():
        lines.append(f"### `{canary}` — hits={n}")
        lines.append("")
        hits = [c for c in canary_rows if c["canary"] == canary]
        if not hits:
            lines.append("- (zero hits; recorded as issue `canary_zero_hits`)")
        else:
            for h in hits:
                lines.append(
                    f"- `{h['relative_path']}` container=`{h['container']}` "
                    f"locator_key=`{h['locator_key']}` "
                    f"occurrence_id=`{h['occurrence_id'][:16]}…`"
                )
        lines.append("")
    lines.append("## Issues")
    lines.append("")
    lines.append(f"Total issues: **{summary['issue_count']}**")
    lines.append(f"By code: `{summary['issue_codes']}`")
    lines.append(f"By severity: `{summary['issue_severity_counts']}`")
    lines.append("")
    for issue in issues[:100]:
        rel = issue.get("relative_path") or "-"
        lines.append(
            f"- [{issue['severity']}] `{issue['issue_code']}` @ `{rel}` — "
            f"{issue['detail']}"
        )
    if len(issues) > 100:
        lines.append(f"- … {len(issues) - 100} more issues")
    lines.append("")
    lines.append("## Explicit non-claims")
    lines.append("")
    for note in summary["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract source-local structural blocks and rule-occurrence "
            "candidates from NHI history ODT snapshots."
        )
    )
    p.add_argument(
        "--history-dir",
        type=Path,
        required=True,
        help="Directory containing the 14 historical ODT files",
    )
    p.add_argument(
        "--accepted-manifest",
        type=Path,
        required=True,
        help="Accepted corpus-profiler manifest.jsonl for SHA verification",
    )
    p.add_argument(
        "--stage-dir",
        type=Path,
        required=True,
        help=(
            "Full-text staging directory (must be under the checkout-local "
            "stage root: .work/nhi-rule-history-stage/grok-occurrences)"
        ),
    )
    p.add_argument(
        "--receipt-dir",
        type=Path,
        required=True,
        help="Tracked receipt output directory (indices, summary, issues)",
    )
    p.add_argument(
        "--print-digests",
        action="store_true",
        help="Print output file SHA-256 digests to stdout",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = extract_history_corpus(
            history_dir=args.history_dir,
            accepted_manifest=args.accepted_manifest,
            stage_dir=args.stage_dir,
            receipt_dir=args.receipt_dir,
            # Production CLI never bypasses stage-root enforcement.
            allow_unrestricted_stage=False,
        )
    except OccurrenceExtractError as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    except cp.CorpusProfileError as exc:
        print(f"ERROR [corpus_profile]: {exc}", file=sys.stderr)
        return 1

    summary = result["summary"]
    print(
        f"OK releases={summary['release_count']} "
        f"blocks={summary['block_count']} "
        f"occurrences={summary['occurrence_count']} "
        f"issues={summary['issue_count']}"
    )
    print(
        "NOTE: source-local candidates only; not identity, legal dates, or diffs."
    )
    if args.print_digests:
        for k, v in sorted(result["digests"].items()):
            print(f"DIGEST {k} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
