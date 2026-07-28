"""Fail-closed full-clause parity for the sealed current NHI anchor.

The current NHI page publishes one whole-document ODT and chapter-local ODTs.
This module reconstructs clauses from the lossless structural parser output and
compares the two publications.  It intentionally makes no historical, legal
effective-date, or predecessor/successor claim.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
)


REPORT_SCHEMA = "nhi-rule-history/current-anchor-clause-parity/v1"
BLOCK_SCHEMA = "nhi-rule-history/structural-block/v2"
OCCURRENCE_SCHEMA = "nhi-rule-history/occurrence-candidate/v2"
ISSUE_SCHEMA = "nhi-rule-history/parse-issue/v2"
NORMALIZER_VERSION = "unicode-nfkc-remove-all-whitespace/1.0.0"
WHOLE_LABEL_PREFIX = "最新版藥品給付規定內容(整份帶走)"
NON_CLAIM_STATEMENT = (
    "Current cumulative whole-vs-chapter anchor parity only; this report does "
    "not establish legal effective dates, event lineage, predecessor "
    "adjacency, historical completeness, or a complete-history claim."
)

_FILES = (
    ("structural-blocks.jsonl", BLOCK_SCHEMA),
    ("occurrence-candidates.jsonl", OCCURRENCE_SCHEMA),
    ("parse-issues.jsonl", ISSUE_SCHEMA),
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_NUMBER = "零〇一二三四五六七八九十百"
_GENERAL_HEADING_RE = re.compile(
    rf"^\s*([{_CHINESE_NUMBER}]+)、"
)
_SECTION_HEADING_RE = re.compile(
    rf"^\s*第\s*([0-9]+|[{_CHINESE_NUMBER}]+)\s*節"
)
_SPLIT_SECTION_LABEL_RE = re.compile(
    rf"^第\s*([0-9]+|[{_CHINESE_NUMBER}]+)\s*節"
)
_SAFE_PARSE_ISSUES = {
    "numeric_quantity_rejected_from_occurrence",
    "duplicate_designation_within_artifact",
}


@dataclass(frozen=True)
class _Artifact:
    sha256: str
    label: str
    resource_id: str
    blocks: tuple[dict[str, Any], ...]
    occurrences: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]


class _Blocked(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.blocker = {"code": code, "message": message, **context}


def _block(code: str, message: str, **context: Any) -> None:
    raise _Blocked(code, message, **context)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _block("INPUT_JSON_UNREADABLE", f"{path.name} is unreadable JSON")
    if not isinstance(value, dict):
        _block("INPUT_JSON_NOT_OBJECT", f"{path.name} is not a JSON object")
    return value


def _read_jsonl(
    path: Path,
    *,
    schema: str,
    parse_run_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError:
        _block("INPUT_FILE_MISSING", f"{path.name} is missing")
    with stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                _block(
                    "INPUT_JSONL_UNREADABLE",
                    f"{path.name}:{line_number} is invalid JSON",
                    filename=path.name,
                    line_number=line_number,
                )
            if not isinstance(row, dict):
                _block(
                    "INPUT_JSONL_ROW_NOT_OBJECT",
                    f"{path.name}:{line_number} is not an object",
                    filename=path.name,
                    line_number=line_number,
                )
            if row.get("schema") != schema:
                _block(
                    "INPUT_SCHEMA_MISMATCH",
                    f"{path.name}:{line_number} has the wrong schema",
                    filename=path.name,
                    line_number=line_number,
                )
            if row.get("parse_run_id") != parse_run_id:
                _block(
                    "PARSE_RUN_MISMATCH",
                    f"{path.name}:{line_number} belongs to another parse run",
                    filename=path.name,
                    line_number=line_number,
                )
            rows.append(row)
    return rows


def _manifest_file_receipts(
    stage_dir: Path,
    manifest: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        _block("MANIFEST_FILES_MISSING", "manifest files are missing")
    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(
            entry.get("filename"), str
        ):
            _block(
                "MANIFEST_FILE_ENTRY_INVALID",
                "manifest contains an invalid file entry",
            )
        filename = entry["filename"]
        if filename in by_name:
            _block(
                "MANIFEST_FILE_ENTRY_DUPLICATE",
                f"manifest contains duplicate receipt for {filename}",
                filename=filename,
            )
        by_name[filename] = entry

    receipts: dict[str, dict[str, Any]] = {}
    for filename, _schema in _FILES:
        if filename not in by_name:
            _block(
                "MANIFEST_FILE_ENTRY_MISSING",
                f"manifest has no receipt for {filename}",
                filename=filename,
            )
        path = stage_dir / filename
        try:
            actual_sha256 = file_sha256(path)
            actual_bytes = path.stat().st_size
        except OSError:
            _block("INPUT_FILE_MISSING", f"{filename} is missing")
        entry = by_name[filename]
        if entry.get("sha256") != actual_sha256:
            _block(
                "INPUT_FILE_HASH_MISMATCH",
                f"{filename} differs from its sealed manifest hash",
                filename=filename,
                expected_sha256=entry.get("sha256"),
                actual_sha256=actual_sha256,
            )
        if entry.get("bytes") != actual_bytes:
            _block(
                "INPUT_FILE_SIZE_MISMATCH",
                f"{filename} differs from its sealed manifest size",
                filename=filename,
                expected_bytes=entry.get("bytes"),
                actual_bytes=actual_bytes,
            )
        receipts[filename] = {
            "sha256": actual_sha256,
            "bytes": actual_bytes,
        }
    return receipts


def _single_string(
    row: Mapping[str, Any],
    key: str,
    *,
    code: str,
    artifact_sha256: str,
) -> str:
    value = row.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], str)
        or not value[0]
    ):
        _block(
            code,
            f"{artifact_sha256} must have exactly one {key} value",
            artifact_sha256=artifact_sha256,
        )
    return value[0]


def _doc_order(row: Mapping[str, Any], artifact_sha256: str) -> int:
    locator = row.get("locator")
    value = locator.get("doc_order") if isinstance(locator, dict) else None
    if not isinstance(value, int) or value < 0:
        _block(
            "STRUCTURAL_LOCATOR_INVALID",
            "selected structural block has no valid doc_order",
            artifact_sha256=artifact_sha256,
            block_id=row.get("block_id"),
        )
    return value


def _validate_artifact(
    meta: Mapping[str, Any],
    blocks: Iterable[dict[str, Any]],
    occurrences: Iterable[dict[str, Any]],
    issues: Iterable[dict[str, Any]],
) -> _Artifact:
    artifact_sha256 = meta.get("artifact_sha256")
    if not isinstance(artifact_sha256, str) or not artifact_sha256:
        _block(
            "ARTIFACT_SHA_MISSING",
            "parsed artifact has no artifact_sha256",
        )
    labels = meta.get("source_labels")
    resources = meta.get("resource_ids")
    if (
        not isinstance(labels, list)
        or len(labels) != 1
        or not isinstance(labels[0], str)
        or not labels[0]
    ):
        _block(
            "SOURCE_MEMBERSHIP_AMBIGUOUS",
            "parsed artifact must have exactly one source label",
            artifact_sha256=artifact_sha256,
        )
    if (
        not isinstance(resources, list)
        or len(resources) != 1
        or not isinstance(resources[0], str)
        or not resources[0]
    ):
        _block(
            "SOURCE_MEMBERSHIP_MISSING",
            "parsed artifact must have exactly one source resource id",
            artifact_sha256=artifact_sha256,
        )
    label = labels[0]
    resource_id = resources[0]
    block_rows = tuple(blocks)
    occurrence_rows = tuple(occurrences)
    issue_rows = tuple(issues)
    expected_counts = {
        "block_count": len(block_rows),
        "occurrence_count": len(occurrence_rows),
        "parse_issue_count": len(issue_rows),
    }
    for key, actual in expected_counts.items():
        if meta.get(key) != actual:
            _block(
                "ARTIFACT_ROW_COUNT_MISMATCH",
                f"{artifact_sha256} {key} differs from the manifest",
                artifact_sha256=artifact_sha256,
                count_name=key,
                expected=meta.get(key),
                actual=actual,
            )

    orders: list[int] = []
    block_ids: set[str] = set()
    by_block_id: dict[str, dict[str, Any]] = {}
    for row in block_rows:
        if row.get("artifact_sha256") != artifact_sha256:
            _block(
                "ARTIFACT_MEMBERSHIP_MISMATCH",
                "structural block is assigned to the wrong artifact",
                artifact_sha256=artifact_sha256,
            )
        row_label = _single_string(
            row,
            "source_labels",
            code="SOURCE_MEMBERSHIP_AMBIGUOUS",
            artifact_sha256=artifact_sha256,
        )
        row_resource = _single_string(
            row,
            "source_resource_ids",
            code="SOURCE_MEMBERSHIP_MISSING",
            artifact_sha256=artifact_sha256,
        )
        if row_label != label or row_resource != resource_id:
            _block(
                "SOURCE_MEMBERSHIP_MISMATCH",
                "structural block provenance differs from its artifact",
                artifact_sha256=artifact_sha256,
                block_id=row.get("block_id"),
            )
        raw_text = row.get("raw_text")
        if not isinstance(raw_text, str) or row.get(
            "raw_text_sha256"
        ) != sha256_bytes(raw_text.encode("utf-8")):
            _block(
                "STRUCTURAL_TEXT_HASH_MISMATCH",
                "structural block raw text differs from its hash",
                artifact_sha256=artifact_sha256,
                block_id=row.get("block_id"),
            )
        block_id = row.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            _block(
                "STRUCTURAL_BLOCK_ID_MISSING",
                "structural block has no block id",
                artifact_sha256=artifact_sha256,
            )
        if block_id in block_ids:
            _block(
                "STRUCTURAL_BLOCK_ID_DUPLICATE",
                "structural block id is duplicated",
                artifact_sha256=artifact_sha256,
                block_id=block_id,
            )
        block_ids.add(block_id)
        by_block_id[block_id] = row
        orders.append(_doc_order(row, artifact_sha256))
    if orders != list(range(len(block_rows))):
        _block(
            "STRUCTURAL_ORDER_AMBIGUOUS",
            "selected artifact doc_order is not a complete contiguous sequence",
            artifact_sha256=artifact_sha256,
        )

    for row in occurrence_rows:
        if row.get("artifact_sha256") != artifact_sha256:
            _block(
                "ARTIFACT_MEMBERSHIP_MISMATCH",
                "occurrence is assigned to the wrong artifact",
                artifact_sha256=artifact_sha256,
            )
        row_label = _single_string(
            row,
            "source_labels",
            code="SOURCE_MEMBERSHIP_AMBIGUOUS",
            artifact_sha256=artifact_sha256,
        )
        row_resource = _single_string(
            row,
            "source_resource_ids",
            code="SOURCE_MEMBERSHIP_MISSING",
            artifact_sha256=artifact_sha256,
        )
        if row_label != label or row_resource != resource_id:
            _block(
                "SOURCE_MEMBERSHIP_MISMATCH",
                "occurrence provenance differs from its artifact",
                artifact_sha256=artifact_sha256,
                occurrence_id=row.get("occurrence_id"),
            )
        block = by_block_id.get(row.get("block_id"))
        if block is None:
            _block(
                "OCCURRENCE_BLOCK_MISSING",
                "occurrence points to a missing structural block",
                artifact_sha256=artifact_sha256,
                occurrence_id=row.get("occurrence_id"),
            )
        if (
            row.get("locator_key") != block.get("locator_key")
            or row.get("raw_text_sha256") != block.get("raw_text_sha256")
            or _doc_order(row, artifact_sha256)
            != _doc_order(block, artifact_sha256)
        ):
            _block(
                "OCCURRENCE_BLOCK_MISMATCH",
                "occurrence locator or text hash differs from its block",
                artifact_sha256=artifact_sha256,
                occurrence_id=row.get("occurrence_id"),
            )

    return _Artifact(
        sha256=artifact_sha256,
        label=label,
        resource_id=resource_id,
        blocks=block_rows,
        occurrences=occurrence_rows,
        issues=issue_rows,
    )


def _chinese_integer(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = _CHINESE_DIGITS.get(left, 1) if left else 1
        return hundreds * 100 + (_chinese_integer(right) if right else 0)
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(value) == 1 and value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    _block(
        "CHAPTER_NUMBER_UNREADABLE",
        f"cannot parse Chinese chapter number {value!r}",
    )


def _membership_from_label(label: str) -> tuple[str, int | None] | None:
    if label.startswith(WHOLE_LABEL_PREFIX):
        return ("whole", None)
    if label.startswith("通則"):
        return ("general", 0)
    match = _SPLIT_SECTION_LABEL_RE.match(label)
    if match:
        return ("section", _chinese_integer(match.group(1)))
    return None


def _normalize_clause_text(parts: Iterable[str]) -> str:
    text = unicodedata.normalize("NFKC", "".join(parts))
    return "".join(text.split())


def _strict_dotted_heading(
    occurrence: Mapping[str, Any],
    *,
    chapter_number: int,
) -> tuple[str, tuple[int, ...]] | None:
    raw = occurrence.get("raw_text")
    designation = occurrence.get("designation_text")
    start = occurrence.get("match_start_in_raw")
    end = occurrence.get("match_end_in_raw")
    if (
        not isinstance(raw, str)
        or not isinstance(designation, str)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(raw)
        or raw[:start].strip()
    ):
        _block(
            "OCCURRENCE_BOUNDARY_AMBIGUOUS",
            "occurrence has invalid text offsets or non-leading content",
            artifact_sha256=occurrence.get("artifact_sha256"),
            occurrence_id=occurrence.get("occurrence_id"),
        )
    normalized_designation = unicodedata.normalize(
        "NFKC", designation
    ).strip().rstrip(".")
    if not re.fullmatch(r"[1-9]\d*(?:\.\d+)+", normalized_designation):
        _block(
            "OCCURRENCE_DESIGNATION_INVALID",
            "occurrence designation is not a dotted numeric candidate",
            artifact_sha256=occurrence.get("artifact_sha256"),
            occurrence_id=occurrence.get("occurrence_id"),
        )
    matched_text = unicodedata.normalize(
        "NFKC", raw[start:end]
    ).strip().rstrip(".")
    if matched_text != normalized_designation:
        _block(
            "OCCURRENCE_BOUNDARY_AMBIGUOUS",
            "occurrence offsets do not select its designation",
            artifact_sha256=occurrence.get("artifact_sha256"),
            occurrence_id=occurrence.get("occurrence_id"),
        )
    segments = tuple(int(part) for part in normalized_designation.split("."))
    tail = unicodedata.normalize("NFKC", raw[end:]).lstrip()
    heading_syntax = tail.startswith(".") or re.match(r"[A-Za-z]", tail)
    if heading_syntax:
        if occurrence.get("container") != "flow" or occurrence.get(
            "in_index_context"
        ) is True:
            _block(
                "OCCURRENCE_CONTAINER_AMBIGUOUS",
                "heading-like occurrence is outside normal document flow",
                artifact_sha256=occurrence.get("artifact_sha256"),
                occurrence_id=occurrence.get("occurrence_id"),
            )
        if segments[0] != chapter_number:
            _block(
                "OCCURRENCE_CHAPTER_AMBIGUOUS",
                "heading-like occurrence does not belong to its chapter",
                artifact_sha256=occurrence.get("artifact_sha256"),
                occurrence_id=occurrence.get("occurrence_id"),
                expected_chapter=chapter_number,
                observed_first_segment=segments[0],
            )
        return normalized_designation, segments
    if re.match(r"(?:歲|年|月|日|小時|~|～|與)", tail):
        return None
    _block(
        "OCCURRENCE_CLASSIFICATION_AMBIGUOUS",
        "numeric occurrence is neither a strict heading nor a known non-heading",
        artifact_sha256=occurrence.get("artifact_sha256"),
        occurrence_id=occurrence.get("occurrence_id"),
        designation=normalized_designation,
    )


def _locator_receipt(block: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "block_id": block.get("block_id"),
        "locator_key": block.get("locator_key"),
        "raw_text_sha256": block.get("raw_text_sha256"),
        "source_row_sha256": block.get("source_row_sha256"),
    }


def _clause_receipt(
    *,
    membership: str,
    designation: str,
    artifact: _Artifact,
    blocks: tuple[dict[str, Any], ...],
    start: int,
    end: int,
) -> dict[str, Any]:
    if not blocks or start >= end:
        _block(
            "CLAUSE_BOUNDARY_EMPTY",
            "clause boundary produced no structural blocks",
            artifact_sha256=artifact.sha256,
            membership=membership,
            designation=designation,
        )
    selected = blocks[start:end]
    normalized = _normalize_clause_text(
        str(block.get("raw_text", "")) for block in selected
    )
    if not normalized:
        _block(
            "CLAUSE_TEXT_EMPTY",
            "clause normalization produced empty text",
            artifact_sha256=artifact.sha256,
            membership=membership,
            designation=designation,
        )
    ordered_receipts = [_locator_receipt(block) for block in selected]
    container_counts = Counter(
        str(block.get("container")) for block in selected
    )
    block_kind_counts = Counter(
        str(block.get("block_kind")) for block in selected
    )
    return {
        "membership": membership,
        "designation": designation,
        "depth": (
            1
            if designation.startswith("通則:")
            else len(designation.split("."))
        ),
        "artifact_sha256": artifact.sha256,
        "source_resource_id": artifact.resource_id,
        "source_label": artifact.label,
        "source_span": {
            "start_doc_order": _doc_order(
                selected[0], artifact.sha256
            ),
            "end_doc_order_exclusive": end,
            "start_locator": _locator_receipt(selected[0]),
            "last_locator": _locator_receipt(selected[-1]),
            "block_count": len(selected),
            "ordered_block_receipts_sha256": sha256_bytes(
                canonical_json_bytes(ordered_receipts)
            ),
            "container_counts": dict(sorted(container_counts.items())),
            "block_kind_counts": dict(sorted(block_kind_counts.items())),
        },
        "normalized_text_char_length": len(normalized),
        "normalized_text_sha256": sha256_bytes(normalized.encode("utf-8")),
    }


def _general_clauses(
    artifact: _Artifact,
    *,
    membership: str,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    headings: list[tuple[int, str]] = []
    for index in range(start, end):
        raw = artifact.blocks[index].get("raw_text")
        if not isinstance(raw, str):
            _block(
                "STRUCTURAL_TEXT_MISSING",
                "structural block raw text is missing",
                artifact_sha256=artifact.sha256,
            )
        match = _GENERAL_HEADING_RE.match(
            unicodedata.normalize("NFKC", raw)
        )
        if match:
            number = _chinese_integer(match.group(1))
            headings.append((index, f"通則:{number}"))
    if not headings:
        _block(
            "GENERAL_CLAUSE_BOUNDARY_MISSING",
            "no top-level general clauses were found",
            artifact_sha256=artifact.sha256,
        )
    designations = [designation for _, designation in headings]
    if len(set(designations)) != len(designations):
        _block(
            "DUPLICATE_DESIGNATION",
            "general clause designation occurs more than once",
            artifact_sha256=artifact.sha256,
        )
    expected = [f"通則:{number}" for number in range(1, len(headings) + 1)]
    if designations != expected:
        _block(
            "GENERAL_BOUNDARY_AMBIGUOUS",
            "general clause headings are not a contiguous sequence",
            artifact_sha256=artifact.sha256,
            observed_designations=designations,
        )
    result: list[dict[str, Any]] = []
    for position, (clause_start, designation) in enumerate(headings):
        clause_end = (
            headings[position + 1][0]
            if position + 1 < len(headings)
            else end
        )
        result.append(
            _clause_receipt(
                membership=membership,
                designation=designation,
                artifact=artifact,
                blocks=artifact.blocks,
                start=clause_start,
                end=clause_end,
            )
        )
    return result


def _section_clauses(
    artifact: _Artifact,
    *,
    chapter_number: int,
    membership: str,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    headings: list[tuple[int, str, tuple[int, ...]]] = []
    for occurrence in artifact.occurrences:
        order = _doc_order(occurrence, artifact.sha256)
        if order < start or order >= end:
            continue
        strict = _strict_dotted_heading(
            occurrence, chapter_number=chapter_number
        )
        if strict is not None:
            designation, segments = strict
            headings.append((order, designation, segments))
    headings.sort(key=lambda item: item[0])
    if not headings:
        _block(
            "SECTION_CLAUSE_BOUNDARY_MISSING",
            "no strict clause headings were found in a chapter",
            artifact_sha256=artifact.sha256,
            membership=membership,
        )
    seen: set[str] = set()
    for _order, designation, _segments in headings:
        if designation in seen:
            _block(
                "DUPLICATE_DESIGNATION",
                "strict clause heading occurs more than once",
                artifact_sha256=artifact.sha256,
                membership=membership,
                designation=designation,
            )
        seen.add(designation)

    result: list[dict[str, Any]] = []
    for position, (clause_start, designation, segments) in enumerate(headings):
        clause_end = end
        for later_start, _later_designation, later_segments in headings[
            position + 1 :
        ]:
            if len(later_segments) <= len(segments):
                clause_end = later_start
                break
        result.append(
            _clause_receipt(
                membership=membership,
                designation=designation,
                artifact=artifact,
                blocks=artifact.blocks,
                start=clause_start,
                end=clause_end,
            )
        )
    return result


def _whole_membership_ranges(
    whole: _Artifact,
) -> tuple[tuple[int, int], dict[int, tuple[int, int]]]:
    general_markers: list[int] = []
    section_markers: list[tuple[int, int]] = []
    annex_markers: list[int] = []
    for index, block in enumerate(whole.blocks):
        raw = unicodedata.normalize(
            "NFKC", str(block.get("raw_text", ""))
        )
        stripped = raw.strip()
        if stripped == "藥品給付規定通則":
            general_markers.append(index)
        section_match = _SECTION_HEADING_RE.match(raw)
        if section_match:
            section_markers.append(
                (index, _chinese_integer(section_match.group(1)))
            )
        if stripped.startswith("附表"):
            annex_markers.append(index)
    if len(general_markers) != 1:
        _block(
            "WHOLE_GENERAL_MEMBERSHIP_AMBIGUOUS",
            "whole artifact must contain exactly one general marker",
            artifact_sha256=whole.sha256,
            marker_count=len(general_markers),
        )
    if not section_markers:
        _block(
            "WHOLE_SECTION_MEMBERSHIP_MISSING",
            "whole artifact contains no chapter markers",
            artifact_sha256=whole.sha256,
        )
    section_numbers = [number for _, number in section_markers]
    if section_numbers != list(range(1, len(section_numbers) + 1)):
        _block(
            "WHOLE_SECTION_MEMBERSHIP_AMBIGUOUS",
            "whole artifact chapter markers are not one contiguous sequence",
            artifact_sha256=whole.sha256,
            observed_sections=section_numbers,
        )
    general_start = general_markers[0] + 1
    first_section_start = section_markers[0][0]
    if general_start >= first_section_start:
        _block(
            "WHOLE_GENERAL_BOUNDARY_AMBIGUOUS",
            "whole general section does not precede chapter 1",
            artifact_sha256=whole.sha256,
        )

    ranges: dict[int, tuple[int, int]] = {}
    for position, (chapter_start, number) in enumerate(section_markers):
        content_start = chapter_start + 1
        if position + 1 < len(section_markers):
            chapter_end = section_markers[position + 1][0]
        else:
            later_annexes = [
                item for item in annex_markers if item > chapter_start
            ]
            chapter_end = (
                min(later_annexes) if later_annexes else len(whole.blocks)
            )
        if content_start >= chapter_end:
            _block(
                "WHOLE_SECTION_BOUNDARY_AMBIGUOUS",
                "whole chapter has an empty or inverted boundary",
                artifact_sha256=whole.sha256,
                membership=f"section:{number}",
            )
        ranges[number] = (content_start, chapter_end)
    return (general_start, first_section_start), ranges


def _issue_gate(artifacts: Iterable[_Artifact]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for artifact in artifacts:
        retained_block_ids = {
            block["block_id"] for block in artifact.blocks
        }
        for issue in artifact.issues:
            code = issue.get("issue_code")
            blocking = issue.get("blocking") is True
            severity = str(issue.get("severity", "")).lower()
            if blocking or severity in {"error", "fatal", "blocking"}:
                _block(
                    "STRUCTURAL_PARSE_ISSUE_BLOCKING",
                    "selected artifact has a blocking structural parse issue",
                    artifact_sha256=artifact.sha256,
                    issue_code=code,
                    issue_id=issue.get("issue_id"),
                )
            if code not in _SAFE_PARSE_ISSUES:
                _block(
                    "TEXT_ALTERING_PARSE_ISSUE_UNRESOLVED",
                    "selected artifact has an unadjudicated parse issue",
                    artifact_sha256=artifact.sha256,
                    issue_code=code,
                    issue_id=issue.get("issue_id"),
                )
            if code == "numeric_quantity_rejected_from_occurrence":
                parameters = issue.get("message_parameters")
                block_id = (
                    parameters.get("block_id")
                    if isinstance(parameters, dict)
                    else None
                )
                if block_id not in retained_block_ids:
                    _block(
                        "REJECTED_NUMERIC_BLOCK_NOT_RETAINED",
                        "numeric rejection does not point to a retained structural block",
                        artifact_sha256=artifact.sha256,
                        issue_id=issue.get("issue_id"),
                    )
            reviewed.append(
                {
                    "artifact_sha256": artifact.sha256,
                    "issue_code": code,
                    "issue_id": issue.get("issue_id"),
                    "disposition": (
                        "structural_block_retained"
                        if code == "numeric_quantity_rejected_from_occurrence"
                        else "strict_heading_uniqueness_rechecked"
                    ),
                }
            )
    return reviewed


def _side_summary(
    clauses: list[dict[str, Any]],
    *,
    artifact_count: int,
) -> dict[str, Any]:
    text_counter = Counter(
        clause["normalized_text_sha256"] for clause in clauses
    )
    receipt_rows = [
        {
            "membership": clause["membership"],
            "designation": clause["designation"],
            "normalized_text_sha256": clause["normalized_text_sha256"],
            "source_span": clause["source_span"],
        }
        for clause in clauses
    ]
    return {
        "artifact_count": artifact_count,
        "clause_count": len(clauses),
        "unique_normalized_text_count": len(text_counter),
        "normalized_text_multiset_sha256": sha256_bytes(
            canonical_json_bytes(sorted(text_counter.items()))
        ),
        "clause_receipts_sha256": sha256_bytes(
            canonical_json_bytes(receipt_rows)
        ),
        "clauses": clauses,
    }


def _mismatch_rows(
    counter: Counter[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    return [
        {
            "membership": membership,
            "designation": designation,
            "normalized_text_sha256": digest,
            "count": count,
        }
        for (membership, designation, digest), count
        in sorted(counter.items())
    ]


def _blocked_report(
    blocker: Mapping[str, Any],
    *,
    manifest_sha256: str | None = None,
    parse_run_id: str | None = None,
    file_receipts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "blocked_preflight",
        "scope": "current_cumulative_whole_vs_chapter_clause_parity",
        "normalizer_version": NORMALIZER_VERSION,
        "non_claim_statement": NON_CLAIM_STATEMENT,
        "input": {
            "manifest_sha256": manifest_sha256,
            "parse_run_id": parse_run_id,
            "file_receipts": dict(file_receipts or {}),
        },
        "blockers": [dict(blocker)],
        "claims": {
            "full_clause_text_compared": False,
            "current_anchor_parity": False,
            "legal_effective_date_inferred": False,
            "historical_completeness": False,
            "complete_history_claim": False,
        },
    }


def analyze_current_anchor_clause_parity(
    stage_dir: Path,
) -> dict[str, Any]:
    """Reconstruct and compare complete clauses from one sealed parse stage.

    The function always returns a public report.  Unsafe reconstruction becomes
    ``blocked_preflight`` with an exact blocker instead of partial parity.
    """

    manifest_sha256: str | None = None
    parse_run_id: str | None = None
    file_receipts: dict[str, Any] = {}
    try:
        manifest_path = stage_dir / "structural-manifest.json"
        try:
            manifest_sha256 = file_sha256(manifest_path)
        except OSError:
            _block(
                "STRUCTURAL_MANIFEST_MISSING",
                "structural-manifest.json is missing",
            )
        manifest = _read_json(manifest_path)
        if manifest.get("status") != "passed":
            _block(
                "STRUCTURAL_PARSE_NOT_PASSED",
                "structural manifest is not a passed parse run",
            )
        parse_run_id_value = manifest.get("parse_run_id")
        if not isinstance(parse_run_id_value, str) or not parse_run_id_value:
            _block(
                "MANIFEST_PARSE_RUN_MISSING",
                "manifest has no parse_run_id",
            )
        parse_run_id = parse_run_id_value
        claims = manifest.get("closure_claims")
        if (
            manifest.get("fidelity_class") != "lossless_structural"
            or not isinstance(claims, dict)
            or claims.get("declared_odt_artifacts_exhausted") is not True
            or claims.get("structural_text_coverage_checked_per_odt")
            is not True
        ):
            _block(
                "LOSSLESS_STRUCTURAL_CLOSURE_UNPROVEN",
                "manifest does not prove exhaustive lossless structural coverage",
            )
        file_receipts = _manifest_file_receipts(stage_dir, manifest)
        rows_by_file = {
            filename: _read_jsonl(
                stage_dir / filename,
                schema=schema,
                parse_run_id=parse_run_id,
            )
            for filename, schema in _FILES
        }
        counts = manifest.get("counts")
        expected_global_counts = {
            "structural_blocks": len(
                rows_by_file["structural-blocks.jsonl"]
            ),
            "occurrence_candidates": len(
                rows_by_file["occurrence-candidates.jsonl"]
            ),
            "parse_issues": len(rows_by_file["parse-issues.jsonl"]),
        }
        if not isinstance(counts, dict):
            _block("MANIFEST_COUNTS_MISSING", "manifest counts are missing")
        for key, actual in expected_global_counts.items():
            if counts.get(key) != actual:
                _block(
                    "GLOBAL_ROW_COUNT_MISMATCH",
                    f"manifest {key} differs from the sealed file",
                    count_name=key,
                    expected=counts.get(key),
                    actual=actual,
                )

        parsed_meta = manifest.get("parsed_artifacts")
        if not isinstance(parsed_meta, list) or not parsed_meta:
            _block(
                "PARSED_ARTIFACTS_MISSING",
                "manifest has no parsed artifact provenance",
            )
        meta_by_sha: dict[str, dict[str, Any]] = {}
        for meta in parsed_meta:
            if not isinstance(meta, dict) or not isinstance(
                meta.get("artifact_sha256"), str
            ):
                _block(
                    "PARSED_ARTIFACT_INVALID",
                    "manifest contains invalid artifact provenance",
                )
            sha = meta["artifact_sha256"]
            if sha in meta_by_sha:
                _block(
                    "PARSED_ARTIFACT_DUPLICATE",
                    "manifest repeats an artifact",
                    artifact_sha256=sha,
                )
            meta_by_sha[sha] = meta
        if counts.get("parsed_odt_artifacts") != len(meta_by_sha):
            _block(
                "PARSED_ARTIFACT_COUNT_MISMATCH",
                "parsed artifact count differs from manifest",
                expected=counts.get("parsed_odt_artifacts"),
                actual=len(meta_by_sha),
            )

        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
            sha: {"blocks": [], "occurrences": [], "issues": []}
            for sha in meta_by_sha
        }
        for filename, group_key in (
            ("structural-blocks.jsonl", "blocks"),
            ("occurrence-candidates.jsonl", "occurrences"),
            ("parse-issues.jsonl", "issues"),
        ):
            for row in rows_by_file[filename]:
                sha = row.get("artifact_sha256")
                if sha not in grouped:
                    _block(
                        "ROW_ARTIFACT_NOT_DECLARED",
                        f"{filename} row belongs to an undeclared artifact",
                        filename=filename,
                        artifact_sha256=sha,
                    )
                grouped[sha][group_key].append(row)

        selected_meta: list[dict[str, Any]] = []
        memberships: dict[tuple[str, int | None], str] = {}
        for sha, meta in meta_by_sha.items():
            labels = meta.get("source_labels")
            if (
                not isinstance(labels, list)
                or len(labels) != 1
                or not isinstance(labels[0], str)
            ):
                continue
            membership = _membership_from_label(labels[0])
            if membership is None:
                continue
            if membership in memberships:
                _block(
                    "SOURCE_MEMBERSHIP_DUPLICATE",
                    "more than one artifact claims the same current-anchor membership",
                    membership=(
                        membership[0]
                        if membership[1] is None
                        else f"{membership[0]}:{membership[1]}"
                    ),
                    artifact_sha256s=sorted(
                        [memberships[membership], sha]
                    ),
                )
            memberships[membership] = sha
            selected_meta.append(meta)
        if ("whole", None) not in memberships:
            _block(
                "WHOLE_ARTIFACT_MISSING",
                "no whole current-anchor artifact was found",
            )
        if ("general", 0) not in memberships:
            _block(
                "SPLIT_GENERAL_ARTIFACT_MISSING",
                "no split general artifact was found",
            )

        artifacts = {
            meta["artifact_sha256"]: _validate_artifact(
                meta,
                grouped[meta["artifact_sha256"]]["blocks"],
                grouped[meta["artifact_sha256"]]["occurrences"],
                grouped[meta["artifact_sha256"]]["issues"],
            )
            for meta in selected_meta
        }
        whole = artifacts[memberships[("whole", None)]]
        general_range, section_ranges = _whole_membership_ranges(whole)
        expected_sections = set(section_ranges)
        actual_sections = {
            number
            for kind, number in memberships
            if kind == "section" and number is not None
        }
        missing_sections = sorted(expected_sections - actual_sections)
        extra_sections = sorted(actual_sections - expected_sections)
        if missing_sections or extra_sections:
            _block(
                "SPLIT_SECTION_MEMBERSHIP_INCOMPLETE",
                "split chapter membership differs from the whole artifact",
                missing_sections=missing_sections,
                extra_sections=extra_sections,
            )

        selected_artifacts = [
            whole,
            artifacts[memberships[("general", 0)]],
            *[
                artifacts[memberships[("section", number)]]
                for number in sorted(expected_sections)
            ],
        ]
        reviewed_issues = _issue_gate(selected_artifacts)

        whole_clauses = _general_clauses(
            whole,
            membership="general",
            start=general_range[0],
            end=general_range[1],
        )
        split_general = artifacts[memberships[("general", 0)]]
        split_clauses = _general_clauses(
            split_general,
            membership="general",
            start=0,
            end=len(split_general.blocks),
        )
        for number in sorted(expected_sections):
            whole_start, whole_end = section_ranges[number]
            whole_clauses.extend(
                _section_clauses(
                    whole,
                    chapter_number=number,
                    membership=f"section:{number}",
                    start=whole_start,
                    end=whole_end,
                )
            )
            split_artifact = artifacts[
                memberships[("section", number)]
            ]
            split_clauses.extend(
                _section_clauses(
                    split_artifact,
                    chapter_number=number,
                    membership=f"section:{number}",
                    start=0,
                    end=len(split_artifact.blocks),
                )
            )

        whole_designations = Counter(
            (item["membership"], item["designation"])
            for item in whole_clauses
        )
        split_designations = Counter(
            (item["membership"], item["designation"])
            for item in split_clauses
        )
        if whole_designations != split_designations:
            _block(
                "CLAUSE_MEMBERSHIP_MISMATCH",
                "whole and split publications do not expose the same strict clause designations",
                whole_only_count=sum(
                    (whole_designations - split_designations).values()
                ),
                split_only_count=sum(
                    (split_designations - whole_designations).values()
                ),
            )

        whole_counter = Counter(
            (
                item["membership"],
                item["designation"],
                item["normalized_text_sha256"],
            )
            for item in whole_clauses
        )
        split_counter = Counter(
            (
                item["membership"],
                item["designation"],
                item["normalized_text_sha256"],
            )
            for item in split_clauses
        )
        whole_only = whole_counter - split_counter
        split_only = split_counter - whole_counter
        equal = whole_counter == split_counter
        whole_by_designation = {
            (item["membership"], item["designation"]): item
            for item in whole_clauses
        }
        split_by_designation = {
            (item["membership"], item["designation"]): item
            for item in split_clauses
        }
        mismatch_designations = sorted(
            key
            for key, whole_clause in whole_by_designation.items()
            if whole_clause["normalized_text_sha256"]
            != split_by_designation[key]["normalized_text_sha256"]
        )
        leafmost_mismatches = [
            key
            for key in mismatch_designations
            if not any(
                other_membership == key[0]
                and other_designation.startswith(f"{key[1]}.")
                for other_membership, other_designation
                in mismatch_designations
            )
        ]
        report = {
            "schema": REPORT_SCHEMA,
            "status": "parity_passed" if equal else "parity_failed",
            "scope": "current_cumulative_whole_vs_chapter_clause_parity",
            "normalizer_version": NORMALIZER_VERSION,
            "non_claim_statement": NON_CLAIM_STATEMENT,
            "input": {
                "manifest_sha256": manifest_sha256,
                "parse_run_id": parse_run_id,
                "file_receipts": file_receipts,
                "fidelity_class": manifest.get("fidelity_class"),
            },
            "membership": {
                "whole": {
                    "artifact_sha256": whole.sha256,
                    "source_resource_id": whole.resource_id,
                    "source_label": whole.label,
                },
                "general": {
                    "artifact_sha256": split_general.sha256,
                    "source_resource_id": split_general.resource_id,
                    "source_label": split_general.label,
                },
                "sections": [
                    {
                        "section_number": number,
                        "artifact_sha256": artifacts[
                            memberships[("section", number)]
                        ].sha256,
                        "source_resource_id": artifacts[
                            memberships[("section", number)]
                        ].resource_id,
                        "source_label": artifacts[
                            memberships[("section", number)]
                        ].label,
                    }
                    for number in sorted(expected_sections)
                ],
            },
            "reviewed_nonblocking_parse_issues": reviewed_issues,
            "whole": _side_summary(
                whole_clauses, artifact_count=1
            ),
            "split": _side_summary(
                split_clauses,
                artifact_count=1 + len(expected_sections),
            ),
            "comparison": {
                "normalized_clause_text_multiset_equal": equal,
                "matched_clause_count": sum(
                    (whole_counter & split_counter).values()
                ),
                "whole_only_count": sum(whole_only.values()),
                "split_only_count": sum(split_only.values()),
                "whole_only": _mismatch_rows(whole_only),
                "split_only": _mismatch_rows(split_only),
                "leafmost_mismatch_designation_count": len(
                    leafmost_mismatches
                ),
                "leafmost_mismatch_designations": [
                    {
                        "membership": membership,
                        "designation": designation,
                    }
                    for membership, designation in leafmost_mismatches
                ],
                "designation_mismatches": [
                    {
                        "membership": whole_clause["membership"],
                        "designation": whole_clause["designation"],
                        "whole_normalized_text_sha256": whole_clause[
                            "normalized_text_sha256"
                        ],
                        "split_normalized_text_sha256": split_clause[
                            "normalized_text_sha256"
                        ],
                        "whole_normalized_text_char_length": whole_clause[
                            "normalized_text_char_length"
                        ],
                        "split_normalized_text_char_length": split_clause[
                            "normalized_text_char_length"
                        ],
                        "whole_source_span": whole_clause["source_span"],
                        "split_source_span": split_clause["source_span"],
                    }
                    for key, whole_clause in sorted(
                        whole_by_designation.items()
                    )
                    for split_clause in [split_by_designation[key]]
                    if whole_clause["normalized_text_sha256"]
                    != split_clause["normalized_text_sha256"]
                ],
            },
            "blockers": [],
            "claims": {
                "full_clause_text_compared": True,
                "current_anchor_parity": equal,
                "legal_effective_date_inferred": False,
                "historical_completeness": False,
                "complete_history_claim": False,
            },
        }
        assert_public_value(report)
        return report
    except _Blocked as exc:
        report = _blocked_report(
            exc.blocker,
            manifest_sha256=manifest_sha256,
            parse_run_id=parse_run_id,
            file_receipts=file_receipts,
        )
        assert_public_value(report)
        return report
