"""Candidate-only marker coverage across verified historical source formats.

This module extends :mod:`history_marker_preflight` beyond the bounded ODT
structural lane.  It searches only hash-bound, freshly verified typed output:

* native PDF page text;
* native legacy Word paragraphs/table cells and legacy Excel cells;
* native ODS cell text; and
* unreviewed image OCR in a separate, explicitly non-authoritative lane.

The output is discovery evidence.  A date or date-plus-designation
co-occurrence in one source artifact does not resolve the notice, legal
effective date, amendment effect, clause identity, predecessor adjacency, or
history completeness.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from nhi_rule_history.annotation_stage import extract_roc_date_markers
from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
)
from nhi_rule_history.history_marker_preflight import (
    HistoryMarkerPreflightError,
    analyze_history_marker_preflight,
)
from nhi_rule_history.parsers import ole as ole_parser
from nhi_rule_history.parsers import pdf as pdf_parser
from nhi_rule_history.parsers.image import (
    ImageExtractionError,
    verify_image_stage,
)
from nhi_rule_history.parsers.ods import (
    OdsExtractionError,
    verify_ods_stage,
)
from nhi_rule_history.pg.acquisition import (
    AcquisitionLoadError,
    validate_acquisition_run,
)
from nhi_rule_history.pg.common import (
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


REPORT_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-preflight/v1"
)
LEDGER_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-evidence-ledger/v1"
)
PAIR_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-article-date-pair/v1"
)
DATE_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-date-locator/v1"
)
DESIGNATION_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-designation-locator/v1"
)
REJECTED_DATE_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-cross-format-rejected-date-locator/v1"
)
MATCHER_VERSION = (
    "nhi-rule-history/history-marker-cross-format-matcher/1.0.0"
)
NON_CLAIM_STATEMENT = (
    "Candidate coverage only. Native text and unreviewed OCR are reported in "
    "separate lanes. A date or date-plus-designation co-occurrence does not "
    "establish an official event, legal effective date, amendment effect, "
    "clause identity, predecessor adjacency, or complete clause history."
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OFFICIAL_DESIGNATION_RE = re.compile(
    r"(?<![0-9.])([1-9][0-9]*(?:\.[0-9]+)+)(?![0-9.])"
)
_PDF_FILES = (
    "pdf-artifacts.jsonl",
    "pdf-pages.jsonl",
    "pdf-issues.jsonl",
)
_OLE_FILES = (
    "ole-artifacts.jsonl",
    "ole-streams.jsonl",
    "ole-word-paragraphs.jsonl",
    "ole-word-tables.jsonl",
    "ole-word-cells.jsonl",
    "ole-word-pages.jsonl",
    "ole-excel-sheets.jsonl",
    "ole-excel-cells.jsonl",
    "ole-issues.jsonl",
)
_IMAGE_FILES = (
    "image-artifacts.jsonl",
    "image-frames.jsonl",
    "image-ocr.jsonl",
)
_PDF_SCHEMAS = {
    "pdf-artifacts.jsonl": "nhi-rule-history/pdf-extraction-artifact/v1",
    "pdf-pages.jsonl": "nhi-rule-history/pdf-layout-page/v1",
    "pdf-issues.jsonl": "nhi-rule-history/pdf-extraction-issue/v1",
}
_OLE_SCHEMAS = {
    "ole-artifacts.jsonl": "nhi-rule-history/ole-extraction-artifact/v1",
    "ole-streams.jsonl": "nhi-rule-history/ole-stream-inventory/v1",
    "ole-word-paragraphs.jsonl": "nhi-rule-history/ole-word-paragraph/v1",
    "ole-word-tables.jsonl": "nhi-rule-history/ole-word-table/v1",
    "ole-word-cells.jsonl": "nhi-rule-history/ole-word-cell/v1",
    "ole-word-pages.jsonl": (
        "nhi-rule-history/ole-word-visual-page/v1"
    ),
    "ole-excel-sheets.jsonl": "nhi-rule-history/ole-excel-sheet/v1",
    "ole-excel-cells.jsonl": "nhi-rule-history/ole-excel-cell/v1",
    "ole-issues.jsonl": "nhi-rule-history/ole-extraction-issue/v1",
}
_IMAGE_SCHEMAS = {
    "image-artifacts.jsonl": (
        "nhi-rule-history/image-artifact-observation/v1"
    ),
    "image-frames.jsonl": (
        "nhi-rule-history/image-frame-observation/v1"
    ),
    "image-ocr.jsonl": "nhi-rule-history/image-ocr-observation/v1",
}


class HistoryMarkerCrossFormatPreflightError(RuntimeError):
    """A missing, tampered, ambiguous, or incompatible preflight input."""


def _fail(message: str) -> None:
    raise HistoryMarkerCrossFormatPreflightError(message)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryMarkerCrossFormatPreflightError(
            f"{label} is missing or invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        _fail(f"{label} is not an object")
    return value


def _read_jsonl(
    path: Path,
    *,
    schema: str,
    run_field: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise HistoryMarkerCrossFormatPreflightError(
            f"{path.name} is missing"
        ) from exc
    with stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                _fail(f"{path.name}:{line_number} is blank")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HistoryMarkerCrossFormatPreflightError(
                    f"{path.name}:{line_number} is invalid JSON"
                ) from exc
            if not isinstance(row, dict) or row.get("schema") != schema:
                _fail(f"{path.name}:{line_number} schema mismatch")
            if run_field is not None and row.get(run_field) != run_id:
                _fail(f"{path.name}:{line_number} run binding mismatch")
            clean = dict(row)
            claimed = clean.pop("source_row_sha256", None)
            if claimed != sha256_bytes(canonical_json_bytes(clean)):
                _fail(f"{path.name}:{line_number} source row hash mismatch")
            rows.append(row)
    return rows


def _verify_manifested_files(
    *,
    stage_dir: Path,
    entries: object,
    expected_files: Sequence[str],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        _fail(f"{label} file receipt list is missing")
    by_name: dict[str, dict[str, Any]] = {}
    for value in entries:
        if not isinstance(value, Mapping):
            _fail(f"{label} file receipt is malformed")
        row = dict(value)
        filename = row.get("filename")
        if not isinstance(filename, str) or filename in by_name:
            _fail(f"{label} file receipt name is missing or duplicated")
        by_name[filename] = row
    if set(by_name) != set(expected_files):
        _fail(f"{label} manifested file set mismatch")
    result: list[dict[str, Any]] = []
    for filename in expected_files:
        row = by_name[filename]
        path = stage_dir / filename
        if (
            not path.is_file()
            or path.stat().st_size != row.get("bytes")
            or not _is_sha256(row.get("sha256"))
            or file_sha256(path) != row["sha256"]
        ):
            _fail(f"{label} manifested file changed: {filename}")
        result.append(row)
    return result


def _verify_pdf_stage(
    stage_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
    expected_artifacts: set[str],
) -> dict[str, Any]:
    manifest = _read_json_object(
        stage_dir / "pdf-manifest.json",
        label="PDF manifest",
    )
    if (
        manifest.get("schema")
        != "nhi-rule-history/pdf-extraction-manifest/v1"
        or manifest.get("status") != "passed"
        or manifest.get("raw_manifest_sha256")
        != expected_raw_manifest_sha256
        or manifest.get("raw_verification", {}).get("status") != "passed"
    ):
        _fail("PDF manifest status or raw binding mismatch")
    run_id = manifest.get("parse_run_id")
    if not isinstance(run_id, str) or not run_id:
        _fail("PDF parse_run_id is missing")
    files = _verify_manifested_files(
        stage_dir=stage_dir,
        entries=manifest.get("files"),
        expected_files=_PDF_FILES,
        label="PDF",
    )
    parser_bundle_sha256 = file_sha256(Path(pdf_parser.__file__).resolve())
    expected_input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "raw_manifest_sha256": expected_raw_manifest_sha256,
                "parser_version": pdf_parser.PDF_PARSER_VERSION,
                "parser_bundle_sha256": parser_bundle_sha256,
                "tools": manifest.get("tools"),
                "text_assembly": pdf_parser.TEXT_ASSEMBLY,
                "statement": pdf_parser.NON_CLAIM,
            }
        )
    )
    if (
        manifest.get("parser_bundle_sha256") != parser_bundle_sha256
        or manifest.get("input_fingerprint")
        != expected_input_fingerprint
        or manifest.get("output_fingerprint")
        != sha256_bytes(canonical_json_bytes(files))
    ):
        _fail("PDF parser or stage fingerprint mismatch")
    rows = {
        filename: _read_jsonl(
            stage_dir / filename,
            schema=_PDF_SCHEMAS[filename],
            run_field="parse_run_id",
            run_id=run_id,
        )
        for filename in _PDF_FILES
    }
    artifacts = rows["pdf-artifacts.jsonl"]
    pages = rows["pdf-pages.jsonl"]
    issues = rows["pdf-issues.jsonl"]
    artifact_by_sha: dict[str, dict[str, Any]] = {}
    for row in artifacts:
        artifact_sha = row.get("artifact_sha256")
        if (
            not _is_sha256(artifact_sha)
            or artifact_sha in artifact_by_sha
        ):
            _fail("PDF artifact identity is missing or duplicated")
        artifact_by_sha[str(artifact_sha)] = row
    if set(artifact_by_sha) != expected_artifacts:
        _fail("PDF artifact denominator differs from the sealed raw run")
    page_by_identity: dict[tuple[str, int], dict[str, Any]] = {}
    pages_by_artifact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hierarchy = Counter(
        {"pages": 0, "flows": 0, "blocks": 0, "lines": 0, "words": 0}
    )
    pages_without_words = 0
    for row in pages:
        artifact_sha = row.get("artifact_sha256")
        page_number = row.get("page_number")
        if (
            artifact_sha not in artifact_by_sha
            or isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or (artifact_sha, page_number) in page_by_identity
            or not isinstance(row.get("text"), str)
            or not isinstance(row.get("bbox"), Mapping)
            or not isinstance(row.get("flows"), list)
        ):
            _fail("PDF page locator or text is ambiguous")
        page_by_identity[(artifact_sha, page_number)] = row
        pages_by_artifact[artifact_sha].append(row)
        hierarchy["pages"] += 1
        word_count = 0
        for flow in row["flows"]:
            if not isinstance(flow, Mapping) or not isinstance(
                flow.get("blocks"), list
            ):
                _fail("PDF flow hierarchy is malformed")
            hierarchy["flows"] += 1
            for block in flow["blocks"]:
                if not isinstance(block, Mapping) or not isinstance(
                    block.get("lines"), list
                ):
                    _fail("PDF block hierarchy is malformed")
                hierarchy["blocks"] += 1
                for line in block["lines"]:
                    if not isinstance(line, Mapping) or not isinstance(
                        line.get("words"), list
                    ):
                        _fail("PDF line hierarchy is malformed")
                    hierarchy["lines"] += 1
                    hierarchy["words"] += len(line["words"])
                    word_count += sum(
                        isinstance(word, Mapping)
                        and bool(word.get("text"))
                        for word in line["words"]
                    )
        if word_count == 0:
            pages_without_words += 1
    classifications = Counter(
        row.get("classification") for row in artifacts
    )
    if set(classifications) - {
        "text_extracted",
        "image_only_needs_ocr",
        "blocking_parse_failure",
    }:
        _fail("PDF terminal classification is unsupported")
    for artifact_sha, artifact in artifact_by_sha.items():
        artifact_pages = sorted(
            pages_by_artifact.get(artifact_sha, []),
            key=lambda row: row["page_number"],
        )
        if [row["page_number"] for row in artifact_pages] != list(
            range(1, artifact.get("page_count", -1) + 1)
        ):
            _fail("PDF page denominator or sequence mismatch")
        expected_text_sha = sha256_bytes(
            "\f".join(row["text"] for row in artifact_pages).encode("utf-8")
        )
        if artifact.get("text_sha256") != expected_text_sha:
            _fail("PDF artifact text fingerprint mismatch")
    expected_counts = {
        "declared_pdf_resources": sum(
            len(row.get("resource_bindings", [])) for row in artifacts
        ),
        "declared_pdf_artifacts": len(artifacts),
        "text_extracted_artifacts": classifications["text_extracted"],
        "image_only_needs_ocr_artifacts": classifications[
            "image_only_needs_ocr"
        ],
        "blocking_parse_failure_artifacts": classifications[
            "blocking_parse_failure"
        ],
        "pages": hierarchy["pages"],
        "pages_without_words": pages_without_words,
        "flows": hierarchy["flows"],
        "blocks": hierarchy["blocks"],
        "lines": hierarchy["lines"],
        "words": hierarchy["words"],
        "blocking_issues": len(issues),
    }
    if manifest.get("counts") != expected_counts:
        _fail("PDF counts differ from typed rows")
    if (
        expected_counts["blocking_issues"] != 0
        or expected_counts["blocking_parse_failure_artifacts"] != 0
    ):
        _fail("PDF stage has a blocking terminal classification")
    return {
        "manifest": manifest,
        "manifest_sha256": file_sha256(
            stage_dir / "pdf-manifest.json"
        ),
        "files": files,
        "artifacts": artifacts,
        "pages": pages,
        "issues": issues,
    }


def _verify_ole_stage(
    stage_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
    expected_artifacts: set[str],
) -> dict[str, Any]:
    manifest = _read_json_object(
        stage_dir / "ole-manifest.json",
        label="OLE manifest",
    )
    if (
        manifest.get("schema")
        != "nhi-rule-history/ole-extraction-manifest/v1"
        or manifest.get("status") not in {"passed", "partial"}
        or manifest.get("raw_manifest_sha256")
        != expected_raw_manifest_sha256
        or manifest.get("raw_verification", {}).get("status") != "passed"
    ):
        _fail("OLE manifest status or raw binding mismatch")
    run_id = manifest.get("parse_run_id")
    if not isinstance(run_id, str) or not run_id:
        _fail("OLE parse_run_id is missing")
    files = _verify_manifested_files(
        stage_dir=stage_dir,
        entries=manifest.get("files"),
        expected_files=_OLE_FILES,
        label="OLE",
    )
    parser_bundle_sha256 = file_sha256(Path(ole_parser.__file__).resolve())
    expected_input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "raw_manifest_sha256": expected_raw_manifest_sha256,
                "parser_version": ole_parser.OLE_PARSER_VERSION,
                "parser_bundle_sha256": parser_bundle_sha256,
                "tools": manifest.get("tools"),
                "network_deny_policy": ole_parser.NETWORK_DENY_POLICY,
                "statement": ole_parser.NON_CLAIM,
            }
        )
    )
    if (
        manifest.get("parser_bundle_sha256") != parser_bundle_sha256
        or manifest.get("input_fingerprint")
        != expected_input_fingerprint
        or manifest.get("output_fingerprint")
        != sha256_bytes(canonical_json_bytes(files))
    ):
        _fail("OLE parser or stage fingerprint mismatch")
    rows = {
        filename: _read_jsonl(
            stage_dir / filename,
            schema=_OLE_SCHEMAS[filename],
            run_field="parse_run_id",
            run_id=run_id,
        )
        for filename in _OLE_FILES
    }
    artifacts = rows["ole-artifacts.jsonl"]
    artifact_by_sha: dict[str, dict[str, Any]] = {}
    for row in artifacts:
        artifact_sha = row.get("artifact_sha256")
        if (
            not _is_sha256(artifact_sha)
            or artifact_sha in artifact_by_sha
            or not isinstance(row.get("counts"), Mapping)
        ):
            _fail("OLE artifact identity or counts are ambiguous")
        artifact_by_sha[str(artifact_sha)] = row
    if set(artifact_by_sha) != expected_artifacts:
        _fail("OLE artifact denominator differs from the sealed raw run")
    classification_counts = Counter(
        row.get("classification") for row in artifacts
    )
    if set(classification_counts) - {
        "word_typed_extracted",
        "excel_typed_extracted",
        "needs_image_ocr_or_visual_review",
    }:
        _fail("OLE terminal classification is unsupported")

    identity_fields = {
        "ole-streams.jsonl": (
            "artifact_sha256",
            "directory_entry_id",
        ),
        "ole-word-paragraphs.jsonl": (
            "artifact_sha256",
            "paragraph_ordinal",
        ),
        "ole-word-tables.jsonl": ("artifact_sha256", "table_ordinal"),
        "ole-word-cells.jsonl": (
            "artifact_sha256",
            "table_ordinal",
            "row_index",
            "cell_index",
        ),
        "ole-word-pages.jsonl": ("artifact_sha256", "page_number"),
        "ole-excel-sheets.jsonl": ("artifact_sha256", "sheet_index"),
        "ole-excel-cells.jsonl": (
            "artifact_sha256",
            "sheet_index",
            "row_index",
            "column_index",
        ),
        "ole-issues.jsonl": ("artifact_sha256", "issue_id"),
    }
    for filename, fields in identity_fields.items():
        identities: set[tuple[Any, ...]] = set()
        for row in rows[filename]:
            if row.get("artifact_sha256") not in artifact_by_sha:
                _fail(f"{filename} references an unknown artifact")
            identity = tuple(row.get(field) for field in fields)
            if any(value is None for value in identity) or identity in identities:
                _fail(f"{filename} locator identity is ambiguous")
            identities.add(identity)
            locator = row.get("locator")
            if filename not in {"ole-streams.jsonl", "ole-issues.jsonl"} and (
                not isinstance(locator, Mapping)
                or locator.get("artifact_sha256")
                != row["artifact_sha256"]
            ):
                _fail(f"{filename} locator binding mismatch")

    row_count_fields = {
        "ole-streams.jsonl": "streams",
        "ole-word-paragraphs.jsonl": "word_paragraphs",
        "ole-word-tables.jsonl": "word_tables",
        "ole-word-cells.jsonl": "word_cells",
        "ole-word-pages.jsonl": "word_visual_pages",
        "ole-excel-sheets.jsonl": "excel_sheets",
        "ole-excel-cells.jsonl": "excel_cells",
    }
    per_artifact_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for filename, count_field in row_count_fields.items():
        for row in rows[filename]:
            per_artifact_counts[row["artifact_sha256"]][count_field] += 1
    for artifact_sha, artifact in artifact_by_sha.items():
        for field in row_count_fields.values():
            if artifact["counts"].get(field) != per_artifact_counts[
                artifact_sha
            ][field]:
                _fail("OLE artifact count differs from typed rows")

    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        _fail("OLE manifest counts are missing")
    expected_counts = {
        "declared_ole_resources": sum(
            len(row.get("resource_bindings", [])) for row in artifacts
        ),
        "declared_ole_artifacts": len(artifacts),
        "word_candidate_artifacts": sum(
            row.get("primary_office_type") == "word_doc"
            for row in artifacts
        ),
        "excel_candidate_artifacts": sum(
            row.get("primary_office_type") == "excel_xls"
            for row in artifacts
        ),
        "typed_extracted_artifacts": (
            classification_counts["word_typed_extracted"]
            + classification_counts["excel_typed_extracted"]
        ),
        "needs_review_artifacts": sum(
            count
            for classification, count in classification_counts.items()
            if classification
            not in {"word_typed_extracted", "excel_typed_extracted"}
        ),
        "streams": len(rows["ole-streams.jsonl"]),
        "storages": sum(
            int(row.get("container_receipt", {}).get("storage_count", 0))
            for row in artifacts
        ),
        "word_paragraphs": len(rows["ole-word-paragraphs.jsonl"]),
        "word_tables": len(rows["ole-word-tables.jsonl"]),
        "word_cells": len(rows["ole-word-cells.jsonl"]),
        "word_visual_pages": len(rows["ole-word-pages.jsonl"]),
        "excel_sheets": len(rows["ole-excel-sheets.jsonl"]),
        "excel_cells": len(rows["ole-excel-cells.jsonl"]),
        "issues": len(rows["ole-issues.jsonl"]),
    }
    if dict(counts) != expected_counts:
        _fail("OLE counts differ from typed rows")
    if manifest.get("classification_counts") != dict(
        sorted(classification_counts.items())
    ):
        _fail("OLE classification counts differ from artifact rows")
    if sum(classification_counts.values()) != len(expected_artifacts):
        _fail("OLE terminal classification denominator mismatch")
    return {
        "manifest": manifest,
        "manifest_sha256": file_sha256(
            stage_dir / "ole-manifest.json"
        ),
        "files": files,
        "rows": rows,
    }


def _verify_ods_bound_stage(
    stage_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
    expected_acquisition_run_id: str,
    expected_acquisition_seal: str,
    expected_artifacts: set[str],
) -> dict[str, Any]:
    try:
        verified = verify_ods_stage(stage_dir)
    except (OdsExtractionError, ContractError, OSError, KeyError) as exc:
        raise HistoryMarkerCrossFormatPreflightError(
            "ODS typed stage verification failed"
        ) from exc
    manifest = verified["manifest"]
    artifact_rows = verified["artifact_rows"]
    if (
        manifest.get("raw_manifest_sha256")
        != expected_raw_manifest_sha256
        or manifest.get("acquisition_run_id")
        != expected_acquisition_run_id
        or manifest.get("acquisition_sealed_fingerprint")
        != expected_acquisition_seal
        or {row["artifact_sha256"] for row in artifact_rows}
        != expected_artifacts
        or manifest.get("counts", {}).get("parsed_ods_artifacts")
        != len(expected_artifacts)
        or manifest.get("counts", {}).get("physical_cells")
        != len(verified["cell_rows"])
    ):
        _fail("ODS denominator or sealed acquisition binding mismatch")
    return verified


def _verify_image_bound_stage(
    stage_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
    expected_acquisition_run_id: str,
    expected_acquisition_seal: str,
    expected_artifacts: set[str],
) -> dict[str, Any]:
    try:
        verified = verify_image_stage(stage_dir)
    except (ImageExtractionError, ContractError, OSError, KeyError) as exc:
        raise HistoryMarkerCrossFormatPreflightError(
            "image typed stage verification failed"
        ) from exc
    manifest = verified["manifest"]
    if (
        manifest.get("raw_manifest_sha256")
        != expected_raw_manifest_sha256
        or manifest.get("acquisition_run_id")
        != expected_acquisition_run_id
        or manifest.get("acquisition_sealed_fingerprint")
        != expected_acquisition_seal
    ):
        _fail("image sealed acquisition binding mismatch")
    extraction_id = manifest.get("extraction_id")
    rows = {
        filename: _read_jsonl(
            stage_dir / filename,
            schema=_IMAGE_SCHEMAS[filename],
            run_field="extraction_id",
            run_id=extraction_id,
        )
        for filename in _IMAGE_FILES
    }
    artifact_rows = rows["image-artifacts.jsonl"]
    frame_rows = rows["image-frames.jsonl"]
    ocr_rows = rows["image-ocr.jsonl"]
    if (
        {row["artifact_sha256"] for row in artifact_rows}
        != expected_artifacts
        or manifest.get("counts", {}).get("parsed_image_artifacts")
        != len(expected_artifacts)
        or manifest.get("counts", {}).get("frames") != len(frame_rows)
        or manifest.get("counts", {}).get("ocr_observations")
        != len(ocr_rows)
    ):
        _fail("image denominator differs from the sealed raw run")
    frames = {row["frame_id"]: row for row in frame_rows}
    if len(frames) != len(frame_rows):
        _fail("image frame identity is duplicated")
    for row in ocr_rows:
        frame = frames.get(row.get("frame_id"))
        if (
            frame is None
            or frame.get("artifact_sha256") != row.get("artifact_sha256")
            or not isinstance(row.get("frame_locator"), Mapping)
            or row.get("frame_locator") != frame.get("locator")
            or row.get("status")
            != "unreviewed_ocr_candidate_needs_visual_review"
        ):
            _fail("OCR locator or non-authoritative status mismatch")
    return {**verified, "rows": rows}


def _source_occurrence_id(row: Mapping[str, Any]) -> str:
    basis = {
        key: value
        for key, value in row.items()
        if key not in {"schema", "source_occurrence_id"}
    }
    return object_fingerprint(basis)


def _base_locator(
    *,
    source_format: str,
    confidence_lane: str,
    artifact_sha256: str,
    unit_type: str,
    unit_id: Mapping[str, Any],
    unit_locator: Mapping[str, Any],
    source_resource_bindings: Sequence[Mapping[str, Any]],
    source_row_sha256: str,
) -> dict[str, Any]:
    if (
        not _is_sha256(artifact_sha256)
        or not isinstance(unit_type, str)
        or not unit_type
        or not isinstance(unit_id, Mapping)
        or not unit_id
        or not isinstance(unit_locator, Mapping)
        or not unit_locator
        or not isinstance(source_resource_bindings, Sequence)
        or isinstance(source_resource_bindings, (str, bytes))
        or not source_resource_bindings
        or not _is_sha256(source_row_sha256)
    ):
        _fail(f"{source_format} text unit has an ambiguous locator")
    normalized_bindings: list[dict[str, Any]] = []
    for value in source_resource_bindings:
        if not isinstance(value, Mapping):
            _fail(f"{source_format} resource binding is malformed")
        binding = dict(value)
        if (
            not _is_sha256(binding.get("resource_id"))
            or not isinstance(binding.get("source_label"), str)
            or not binding["source_label"]
        ):
            _fail(f"{source_format} resource binding is ambiguous")
        if "resource_kind" in binding and (
            not isinstance(binding["resource_kind"], str)
            or not binding["resource_kind"]
        ):
            _fail(f"{source_format} resource kind is ambiguous")
        if set(binding) - {
            "resource_id",
            "resource_kind",
            "source_label",
        }:
            _fail(f"{source_format} resource binding has unknown fields")
        normalized_bindings.append(binding)
    normalized_bindings.sort(
        key=lambda row: (
            row["resource_id"],
            row.get("resource_kind", ""),
            row["source_label"],
        )
    )
    return {
        "source_format": source_format,
        "confidence_lane": confidence_lane,
        "artifact_sha256": artifact_sha256,
        "unit_type": unit_type,
        "unit_id": dict(unit_id),
        "unit_locator": dict(unit_locator),
        "source_resource_bindings": normalized_bindings,
        "source_row_sha256": source_row_sha256,
    }


def _text_unit(
    *,
    source_format: str,
    confidence_lane: str,
    row: Mapping[str, Any],
    text: object,
    unit_type: str,
    unit_id: Mapping[str, Any],
    unit_locator: object,
    source_resource_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(text, str):
        _fail(f"{source_format} {unit_type} text is not a string")
    if not isinstance(unit_locator, Mapping):
        _fail(f"{source_format} {unit_type} locator is ambiguous")
    base = _base_locator(
        source_format=source_format,
        confidence_lane=confidence_lane,
        artifact_sha256=row["artifact_sha256"],
        unit_type=unit_type,
        unit_id=unit_id,
        unit_locator=unit_locator,
        source_resource_bindings=source_resource_bindings,
        source_row_sha256=row["source_row_sha256"],
    )
    return {**base, "text": text}


def _bindings_from_parallel_lists(
    resource_ids: object,
    source_labels: object,
    *,
    source_format: str,
) -> list[dict[str, str]]:
    if (
        not isinstance(resource_ids, list)
        or not isinstance(source_labels, list)
        or len(resource_ids) != len(source_labels)
        or not resource_ids
    ):
        _fail(f"{source_format} resource binding lists are ambiguous")
    return [
        {
            "resource_id": resource_id,
            "source_label": source_label,
        }
        for resource_id, source_label in zip(
            resource_ids,
            source_labels,
            strict=True,
        )
    ]


def _native_text_units(
    *,
    pdf: Mapping[str, Any],
    ole: Mapping[str, Any],
    ods: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "pdf": [],
        "ole_word": [],
        "ole_excel": [],
        "ods": [],
    }
    pdf_bindings = {
        row["artifact_sha256"]: row["resource_bindings"]
        for row in pdf["artifacts"]
    }
    for row in pdf["pages"]:
        result["pdf"].append(
            _text_unit(
                source_format="pdf",
                confidence_lane="native_text_candidate",
                row=row,
                text=row["text"],
                unit_type="page",
                unit_id={"page_number": row["page_number"]},
                unit_locator={
                    "page_number": row["page_number"],
                    "bbox": row["bbox"],
                },
                source_resource_bindings=pdf_bindings[
                    row["artifact_sha256"]
                ],
            )
        )
    ole_rows = ole["rows"]
    ole_bindings = {
        row["artifact_sha256"]: row["resource_bindings"]
        for row in ole_rows["ole-artifacts.jsonl"]
    }
    for filename, unit_type, fields in (
        (
            "ole-word-paragraphs.jsonl",
            "word_paragraph",
            ("block_index", "paragraph_ordinal"),
        ),
        (
            "ole-word-cells.jsonl",
            "word_table_cell",
            ("table_ordinal", "row_index", "cell_index"),
        ),
    ):
        for row in ole_rows[filename]:
            result["ole_word"].append(
                _text_unit(
                    source_format="ole_word",
                    confidence_lane="native_text_candidate",
                    row=row,
                    text=row["text"],
                    unit_type=unit_type,
                    unit_id={field: row[field] for field in fields},
                    unit_locator=row["locator"],
                    source_resource_bindings=ole_bindings[
                        row["artifact_sha256"]
                    ],
                )
            )
    for row in ole_rows["ole-excel-cells.jsonl"]:
        value = row.get("value")
        if not isinstance(value, str):
            continue
        result["ole_excel"].append(
            _text_unit(
                source_format="ole_excel",
                confidence_lane="native_text_candidate",
                row=row,
                text=value,
                unit_type="excel_cell",
                unit_id={
                    "sheet_index": row["sheet_index"],
                    "row_index": row["row_index"],
                    "column_index": row["column_index"],
                },
                unit_locator=row["locator"],
                source_resource_bindings=ole_bindings[
                    row["artifact_sha256"]
                ],
            )
        )
    for row in ods["cell_rows"]:
        rendered = row.get("text", {}).get("rendered")
        if not isinstance(rendered, str):
            _fail("ODS cell rendered text is ambiguous")
        result["ods"].append(
            _text_unit(
                source_format="ods",
                confidence_lane="native_text_candidate",
                row=row,
                text=rendered,
                unit_type="ods_cell",
                unit_id={"cell_id": row["cell_id"]},
                unit_locator=row["locator"],
                source_resource_bindings=_bindings_from_parallel_lists(
                    row.get("source_resource_ids"),
                    row.get("source_labels"),
                    source_format="ods",
                ),
            )
        )
    return result


def _ocr_text_units(image: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    frames = {
        row["frame_id"]: row
        for row in image["rows"]["image-frames.jsonl"]
    }
    for row in image["rows"]["image-ocr.jsonl"]:
        frame = frames[row["frame_id"]]
        result.append(
            _text_unit(
                source_format="image_ocr",
                confidence_lane=(
                    "unreviewed_ocr_candidate_non_authoritative"
                ),
                row=row,
                text=row["text"],
                unit_type="image_frame_ocr",
                unit_id={
                    "frame_id": row["frame_id"],
                    "ocr_observation_id": row["ocr_observation_id"],
                },
                unit_locator=row["frame_locator"],
                source_resource_bindings=_bindings_from_parallel_lists(
                    frame.get("source_resource_ids"),
                    frame.get("source_labels"),
                    source_format="image_ocr",
                ),
            )
        )
    return result


def _extract_unit_evidence(
    units_by_format: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    official_designations: set[str],
) -> dict[str, Any]:
    date_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    designation_rows: list[dict[str, Any]] = []
    date_artifacts: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    designation_artifacts: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    observed_unit_count: Counter[str] = Counter()
    for source_format in sorted(units_by_format):
        for unit in units_by_format[source_format]:
            text = unit["text"]
            observed_unit_count[source_format] += 1
            for marker in extract_roc_date_markers(text):
                rejected = marker["normalized_iso_candidate"] is None
                row = {
                    "schema": (
                        REJECTED_DATE_LOCATOR_SCHEMA
                        if rejected
                        else DATE_LOCATOR_SCHEMA
                    ),
                    **{
                        key: unit[key]
                        for key in (
                            "source_format",
                            "confidence_lane",
                            "artifact_sha256",
                            "unit_type",
                            "unit_id",
                            "unit_locator",
                            "source_resource_bindings",
                            "source_row_sha256",
                        )
                    },
                    "marker_ordinal_in_unit": marker["marker_ordinal"],
                    "char_start_in_unit": marker["char_start"],
                    "char_end_in_unit": marker["char_end"],
                    "raw_expression": marker["raw_expression"],
                    "raw_expression_sha256": marker[
                        "raw_expression_sha256"
                    ],
                    "normalized_iso_candidate": marker[
                        "normalized_iso_candidate"
                    ],
                    "normalization_status": marker[
                        "normalization_status"
                    ],
                }
                row["source_occurrence_id"] = _source_occurrence_id(row)
                if rejected:
                    rejected_rows.append(row)
                else:
                    date_rows.append(row)
                    date_artifacts[source_format][
                        marker["normalized_iso_candidate"]
                    ].add(unit["artifact_sha256"])
            for match_ordinal, match in enumerate(
                _OFFICIAL_DESIGNATION_RE.finditer(text)
            ):
                designation = match.group(1)
                if designation not in official_designations:
                    continue
                row = {
                    "schema": DESIGNATION_LOCATOR_SCHEMA,
                    **{
                        key: unit[key]
                        for key in (
                            "source_format",
                            "confidence_lane",
                            "artifact_sha256",
                            "unit_type",
                            "unit_id",
                            "unit_locator",
                            "source_resource_bindings",
                            "source_row_sha256",
                        )
                    },
                    "designation": designation,
                    "match_ordinal_in_unit": match_ordinal,
                    "char_start_in_unit": match.start(1),
                    "char_end_in_unit": match.end(1),
                    "matched_text": match.group(1),
                }
                row["source_occurrence_id"] = _source_occurrence_id(row)
                designation_rows.append(row)
                designation_artifacts[source_format][designation].add(
                    unit["artifact_sha256"]
                )

    def deduplicate(
        rows: Sequence[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        by_id: dict[str, dict[str, Any]] = {}
        removed = 0
        for row in rows:
            identity = row["source_occurrence_id"]
            previous = by_id.get(identity)
            if previous is None:
                by_id[identity] = row
            elif previous == row:
                removed += 1
            else:
                _fail("source occurrence identity collision")
        return (
            sorted(
                by_id.values(),
                key=lambda row: (
                    row["source_format"],
                    row["artifact_sha256"],
                    canonical_json_bytes(row["unit_id"]),
                    row["char_start_in_unit"],
                    row["source_occurrence_id"],
                ),
            ),
            removed,
        )

    date_rows, date_dupes = deduplicate(date_rows)
    rejected_rows, rejected_dupes = deduplicate(rejected_rows)
    designation_rows, designation_dupes = deduplicate(designation_rows)
    return {
        "date_rows": date_rows,
        "rejected_rows": rejected_rows,
        "designation_rows": designation_rows,
        "date_artifacts": date_artifacts,
        "designation_artifacts": designation_artifacts,
        "observed_unit_count": dict(sorted(observed_unit_count.items())),
        "exact_duplicate_rows_removed": {
            "valid_date": date_dupes,
            "rejected_date": rejected_dupes,
            "designation": designation_dupes,
        },
    }


def _union_index(
    index: Mapping[str, Mapping[str, set[str]]],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for values in index.values():
        for key, artifacts in values.items():
            result[key].update(artifacts)
    return result


def _fingerprint_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = tuple(rows)
    return {
        "row_count": len(materialized),
        "row_set_fingerprint": row_set_fingerprint(
            row_sha256(row) for row in materialized
        ),
    }


def analyze_history_marker_cross_format_preflight(
    *,
    annotation_run: Mapping[str, Any],
    articles: Iterable[Mapping[str, Any]],
    annotations: Iterable[Mapping[str, Any]],
    annotation_receipt: Path | Mapping[str, Any],
    historical_receipt: Path | Mapping[str, Any],
    raw_dir: Path,
    structural_dir: Path,
    pdf_stage_dir: Path,
    ole_stage_dir: Path,
    ods_stage_dir: Path,
    image_stage_dir: Path,
) -> dict[str, Any]:
    """Return a deterministic, cross-format candidate-only coverage result."""

    article_rows = tuple(dict(row) for row in articles)
    annotation_rows = tuple(dict(row) for row in annotations)
    try:
        odt = analyze_history_marker_preflight(
            annotation_run=annotation_run,
            articles=article_rows,
            annotations=annotation_rows,
            annotation_receipt=annotation_receipt,
            historical_receipt=historical_receipt,
            raw_dir=raw_dir,
            structural_dir=structural_dir,
        )
        acquisition = validate_acquisition_run(Path(raw_dir))
    except (
        HistoryMarkerPreflightError,
        AcquisitionLoadError,
        ContractError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        raise HistoryMarkerCrossFormatPreflightError(
            "sealed annotation/ODT/raw baseline verification failed"
        ) from exc

    raw_artifacts_by_type: dict[str, set[str]] = defaultdict(set)
    for row in acquisition.rows["raw-artifacts.jsonl"]:
        raw_artifacts_by_type[row["media_type"]].add(
            row["artifact_sha256"]
        )
    expected_image_artifacts = set().union(
        raw_artifacts_by_type["image/jpeg"],
        raw_artifacts_by_type["image/gif"],
        raw_artifacts_by_type["image/tiff"],
    )
    pdf = _verify_pdf_stage(
        Path(pdf_stage_dir),
        expected_raw_manifest_sha256=acquisition.raw_manifest_sha256,
        expected_artifacts=raw_artifacts_by_type["application/pdf"],
    )
    ole = _verify_ole_stage(
        Path(ole_stage_dir),
        expected_raw_manifest_sha256=acquisition.raw_manifest_sha256,
        expected_artifacts=raw_artifacts_by_type[
            "application/x-ole-storage"
        ],
    )
    ods = _verify_ods_bound_stage(
        Path(ods_stage_dir),
        expected_raw_manifest_sha256=acquisition.raw_manifest_sha256,
        expected_acquisition_run_id=acquisition.run_id,
        expected_acquisition_seal=acquisition.sealed_fingerprint,
        expected_artifacts=raw_artifacts_by_type[
            "application/vnd.oasis.opendocument.spreadsheet"
        ],
    )
    image = _verify_image_bound_stage(
        Path(image_stage_dir),
        expected_raw_manifest_sha256=acquisition.raw_manifest_sha256,
        expected_acquisition_run_id=acquisition.run_id,
        expected_acquisition_seal=acquisition.sealed_fingerprint,
        expected_artifacts=expected_image_artifacts,
    )

    odt_pairs = odt["evidence_rows"]["article_date_pairs"]
    official_designations = {
        row["article_num"]
        for row in odt_pairs
        if row["designation_kind"]
        == "official_dotted_numeric_candidate"
    }
    native = _extract_unit_evidence(
        _native_text_units(pdf=pdf, ole=ole, ods=ods),
        official_designations=official_designations,
    )
    ocr = _extract_unit_evidence(
        {"image_ocr": _ocr_text_units(image)},
        official_designations=official_designations,
    )
    native_dates = _union_index(native["date_artifacts"])
    native_designations = _union_index(
        native["designation_artifacts"]
    )
    ocr_dates = _union_index(ocr["date_artifacts"])
    ocr_designations = _union_index(ocr["designation_artifacts"])

    pair_rows: list[dict[str, Any]] = []
    for odt_pair in sorted(
        odt_pairs,
        key=lambda row: (
            row["article_num"],
            row["normalized_iso_candidate"],
            row["article_id"],
        ),
    ):
        date = odt_pair["normalized_iso_candidate"]
        designation = odt_pair["article_num"]
        official = (
            odt_pair["designation_kind"]
            == "official_dotted_numeric_candidate"
        )
        native_date_artifacts = sorted(native_dates.get(date, set()))
        ocr_date_artifacts = sorted(ocr_dates.get(date, set()))
        native_joint_artifacts = (
            sorted(
                native_dates.get(date, set())
                & native_designations.get(designation, set())
            )
            if official
            else []
        )
        ocr_joint_artifacts = (
            sorted(
                ocr_dates.get(date, set())
                & ocr_designations.get(designation, set())
            )
            if official
            else []
        )
        odt_date = bool(odt_pair["date_in_historical_odt_artifact"])
        odt_joint = odt_pair[
            "date_and_designation_in_same_artifact"
        ]
        native_cross_format_date = odt_date or bool(
            native_date_artifacts
        )
        native_cross_format_joint: bool | None = (
            (odt_joint is True or bool(native_joint_artifacts))
            if official
            else None
        )
        pair_rows.append(
            {
                "schema": PAIR_SCHEMA,
                "pair_id": odt_pair["pair_id"],
                "article_id": odt_pair["article_id"],
                "article_num": designation,
                "normalized_iso_candidate": date,
                "designation_kind": odt_pair["designation_kind"],
                "marker_occurrence_count": odt_pair[
                    "marker_occurrence_count"
                ],
                "odt_date_candidate": odt_date,
                "odt_joint_candidate": odt_joint,
                "native_typed_date_artifact_sha256s": (
                    native_date_artifacts
                ),
                "native_typed_joint_artifact_sha256s": (
                    native_joint_artifacts
                ),
                "native_cross_format_date_candidate": (
                    native_cross_format_date
                ),
                "native_cross_format_joint_candidate": (
                    native_cross_format_joint
                ),
                "native_date_incremental_over_odt": (
                    not odt_date and bool(native_date_artifacts)
                ),
                "native_joint_incremental_over_odt": (
                    official
                    and odt_joint is False
                    and bool(native_joint_artifacts)
                ),
                "unreviewed_ocr_date_artifact_sha256s": (
                    ocr_date_artifacts
                ),
                "unreviewed_ocr_joint_artifact_sha256s": (
                    ocr_joint_artifacts
                ),
                "ocr_only_date_candidate_beyond_native": (
                    not native_cross_format_date
                    and bool(ocr_date_artifacts)
                ),
                "ocr_only_joint_candidate_beyond_native": (
                    official
                    and native_cross_format_joint is False
                    and bool(ocr_joint_artifacts)
                ),
                "ocr_authoritative_text": False,
            }
        )

    official_pairs = [
        row
        for row in pair_rows
        if row["designation_kind"]
        == "official_dotted_numeric_candidate"
    ]
    coverage = {
        "denominator": {
            "valid_marker_occurrences": odt["coverage"][
                "valid_marker_occurrences"
            ],
            "invalid_annotation_dates_preserved": odt["coverage"][
                "invalid_annotation_date_candidates_rejected"
            ],
            "article_date_pairs": len(pair_rows),
            "official_designation_article_date_pairs": len(
                official_pairs
            ),
            "project_navigation_article_date_pairs": (
                len(pair_rows) - len(official_pairs)
            ),
        },
        "odt_baseline": {
            "date_present_article_date_pairs": odt["coverage"][
                "date_present_article_date_pairs"
            ],
            "joint_present_official_article_date_pairs": odt[
                "coverage"
            ][
                "date_and_designation_same_artifact_article_date_pairs"
            ],
        },
        "native_cross_format": {
            "date_present_article_date_pairs": sum(
                row["native_cross_format_date_candidate"]
                for row in pair_rows
            ),
            "date_incremental_article_date_pairs_over_odt": sum(
                row["native_date_incremental_over_odt"]
                for row in pair_rows
            ),
            "joint_present_official_article_date_pairs": sum(
                row["native_cross_format_joint_candidate"] is True
                for row in official_pairs
            ),
            "joint_incremental_official_article_date_pairs_over_odt": sum(
                row["native_joint_incremental_over_odt"]
                for row in official_pairs
            ),
        },
        "unreviewed_ocr_separate_non_authoritative": {
            "date_candidate_article_date_pairs": sum(
                bool(row["unreviewed_ocr_date_artifact_sha256s"])
                for row in pair_rows
            ),
            "date_only_beyond_native_article_date_pairs": sum(
                row["ocr_only_date_candidate_beyond_native"]
                for row in pair_rows
            ),
            "joint_candidate_official_article_date_pairs": sum(
                bool(row["unreviewed_ocr_joint_artifact_sha256s"])
                for row in official_pairs
            ),
            "joint_only_beyond_native_official_article_date_pairs": sum(
                row["ocr_only_joint_candidate_beyond_native"]
                for row in official_pairs
            ),
            "authoritative_text_observations": 0,
        },
        "source_occurrences": {
            "native_valid_date_occurrences": len(native["date_rows"]),
            "native_invalid_date_candidates_preserved": len(
                native["rejected_rows"]
            ),
            "native_official_designation_occurrences": len(
                native["designation_rows"]
            ),
            "ocr_valid_date_occurrences": len(ocr["date_rows"]),
            "ocr_invalid_date_candidates_preserved": len(
                ocr["rejected_rows"]
            ),
            "ocr_official_designation_occurrences": len(
                ocr["designation_rows"]
            ),
        },
    }
    format_coverage: dict[str, Any] = {}
    native_formats = (
        set(native["observed_unit_count"])
        | set(native["date_artifacts"])
        | set(native["designation_artifacts"])
    )
    for source_format in sorted(native_formats):
        format_coverage[source_format] = {
            "confidence_lane": "native_text_candidate",
            "text_units": native["observed_unit_count"].get(
                source_format, 0
            ),
            "valid_date_occurrences": sum(
                row["source_format"] == source_format
                for row in native["date_rows"]
            ),
            "invalid_date_candidates_preserved": sum(
                row["source_format"] == source_format
                for row in native["rejected_rows"]
            ),
            "official_designation_occurrences": sum(
                row["source_format"] == source_format
                for row in native["designation_rows"]
            ),
            "unique_normalized_dates": len(
                native["date_artifacts"][source_format]
            ),
            "unique_official_designations": len(
                native["designation_artifacts"].get(source_format, {})
            ),
        }
    format_coverage["image_ocr"] = {
        "confidence_lane": (
            "unreviewed_ocr_candidate_non_authoritative"
        ),
        "text_units": ocr["observed_unit_count"].get("image_ocr", 0),
        "valid_date_occurrences": len(ocr["date_rows"]),
        "invalid_date_candidates_preserved": len(
            ocr["rejected_rows"]
        ),
        "official_designation_occurrences": len(
            ocr["designation_rows"]
        ),
        "unique_normalized_dates": len(
            ocr["date_artifacts"].get("image_ocr", {})
        ),
        "unique_official_designations": len(
            ocr["designation_artifacts"].get("image_ocr", {})
        ),
        "authoritative_text": False,
    }

    unsupported_or_review_lanes = {
        "pdf_zero_word_pages": pdf["manifest"]["counts"][
            "pages_without_words"
        ],
        "pdf_ocr_or_visual_review_required": (
            pdf["manifest"]["counts"]["pages_without_words"] > 0
        ),
        "ole_needs_image_ocr_or_visual_review_artifacts": ole[
            "manifest"
        ]["counts"]["needs_review_artifacts"],
        "ole_visual_review_pages": ole["manifest"]["counts"][
            "word_visual_pages"
        ],
        "ole_page_layout_exact_matching": (
            "unsupported_for_native_text_documents"
        ),
        "ole_embedded_object_content": "unsupported_not_recursively_parsed",
        "ods_unsupported_physical_cells": sum(
            ods["unsupported_code_counts"].values()
        ),
        "image_needs_visual_review_frames": image["manifest"][
            "counts"
        ]["needs_visual_review_frames"],
        "image_human_verified_ocr_observations": 0,
    }
    evidence_rows = {
        "native_date_locators": native["date_rows"],
        "native_rejected_date_locators": native["rejected_rows"],
        "native_designation_locators": native["designation_rows"],
        "unreviewed_ocr_date_locators": ocr["date_rows"],
        "unreviewed_ocr_rejected_date_locators": ocr[
            "rejected_rows"
        ],
        "unreviewed_ocr_designation_locators": ocr[
            "designation_rows"
        ],
        "article_date_pairs": pair_rows,
    }
    evidence_fingerprints = {
        name: _fingerprint_rows(rows)
        for name, rows in evidence_rows.items()
    }
    stage_inputs = {
        "odt_preflight": {
            "input_fingerprint": odt["input_fingerprint"],
            "output_fingerprint": odt["output_fingerprint"],
        },
        "historical_acquisition": {
            "run_id": acquisition.run_id,
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "sealed_fingerprint": acquisition.sealed_fingerprint,
        },
        "pdf": {
            "manifest_sha256": pdf["manifest_sha256"],
            "parse_run_id": pdf["manifest"]["parse_run_id"],
            "output_fingerprint": pdf["manifest"][
                "output_fingerprint"
            ],
        },
        "ole": {
            "manifest_sha256": ole["manifest_sha256"],
            "parse_run_id": ole["manifest"]["parse_run_id"],
            "output_fingerprint": ole["manifest"][
                "output_fingerprint"
            ],
        },
        "ods": {
            "manifest_sha256": ods["manifest_sha256"],
            "extraction_id": ods["manifest"]["extraction_id"],
            "output_fingerprint": ods["output_fingerprint"],
        },
        "image": {
            "manifest_sha256": image["manifest_sha256"],
            "extraction_id": image["manifest"]["extraction_id"],
            "output_fingerprint": image["output_fingerprint"],
            "ocr_runtime_fingerprint": image["manifest"][
                "ocr_runtime"
            ]["runtime_fingerprint"],
        },
    }
    input_fingerprint = object_fingerprint(
        {
            "matcher_version": MATCHER_VERSION,
            "stage_inputs": stage_inputs,
        }
    )
    output_fingerprint = object_fingerprint(
        {
            "coverage": coverage,
            "format_coverage": format_coverage,
            "unsupported_or_review_lanes": unsupported_or_review_lanes,
            "evidence_fingerprints": evidence_fingerprints,
        }
    )
    result = {
        "schema": REPORT_SCHEMA,
        "status": "candidate_coverage_only",
        "matcher_version": MATCHER_VERSION,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
        "inputs": stage_inputs,
        "method": {
            "date_normalization": (
                "reuse exact "
                "nhi-rule-history/roc-date-marker-extractor/1.0.0"
            ),
            "designation_matching": (
                "exact ASCII dotted numeric token from the official "
                "designation denominator, with digit/dot boundaries"
            ),
            "same_artifact_test": (
                "intersection of content-addressed artifact_sha256 sets "
                "within the same confidence lane"
            ),
            "native_formats": ["odt", "pdf", "ole_word", "ole_excel", "ods"],
            "ocr_policy": (
                "image OCR is unreviewed, non-authoritative candidate "
                "evidence and never contributes to native coverage"
            ),
            "deduplication": (
                "remove only byte-identical evidence rows with the same "
                "source occurrence identity; distinct source locators and "
                "offsets remain distinct"
            ),
            "invalid_date_policy": (
                "preserve exact locator and exclude from every normalized "
                "date denominator or match"
            ),
        },
        "coverage": coverage,
        "format_coverage": format_coverage,
        "exact_duplicate_rows_removed": {
            "native": native["exact_duplicate_rows_removed"],
            "ocr": ocr["exact_duplicate_rows_removed"],
        },
        "unsupported_or_review_lanes": unsupported_or_review_lanes,
        "evidence_fingerprints": evidence_fingerprints,
        "evidence_rows": evidence_rows,
        "claims": {
            "candidate_coverage_computed": True,
            "ocr_authoritative_text": False,
            "official_event_resolved": False,
            "legal_effective_date_resolved": False,
            "amendment_effect_resolved": False,
            "clause_identity_resolved": False,
            "adjacent_snapshot_resolved": False,
            "official_source_universe_closed": False,
            "per_clause_history_complete": False,
            "canonical_history_written": False,
            "postgresql_written": False,
        },
        "statement": NON_CLAIM_STATEMENT,
    }
    assert_public_value(result)
    return result


def write_cross_format_evidence_ledger(
    result: Mapping[str, Any],
    path: Path,
) -> dict[str, Any]:
    """Atomically write exact cross-format evidence as canonical JSONL."""

    if result.get("schema") != REPORT_SCHEMA:
        _fail("cannot write a ledger for an incompatible result")
    evidence_rows = result.get("evidence_rows")
    if not isinstance(evidence_rows, Mapping):
        _fail("cross-format result has no evidence rows")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    row_counts: dict[str, int] = {}
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for group in sorted(evidence_rows):
                rows = evidence_rows[group]
                if not isinstance(rows, list):
                    _fail("cross-format evidence group is not an array")
                row_counts[group] = len(rows)
                for row in rows:
                    envelope = {
                        "schema": LEDGER_SCHEMA,
                        "evidence_group": group,
                        "input_fingerprint": result[
                            "input_fingerprint"
                        ],
                        "output_fingerprint": result[
                            "output_fingerprint"
                        ],
                        "evidence": row,
                    }
                    assert_public_value(envelope)
                    stream.write(canonical_json_bytes(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "filename": path.name,
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "row_counts": row_counts,
    }


def compact_cross_format_public_report(
    result: Mapping[str, Any],
    *,
    evidence_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove row arrays while retaining the exact ledger binding."""

    if result.get("schema") != REPORT_SCHEMA:
        _fail("cannot compact an incompatible result")
    report = {
        key: value
        for key, value in result.items()
        if key != "evidence_rows"
    }
    report["evidence_ledger"] = dict(evidence_ledger)
    assert_public_value(report)
    return report
