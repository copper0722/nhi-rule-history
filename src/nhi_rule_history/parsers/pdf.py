"""Deterministic source-local PDF text and geometry extraction.

This lane consumes an already verified v2 acquisition run.  It intentionally
does not infer legal dates, clause identity, amendment events, effect,
predecessor/successor relationships, or history completeness.  Poppler emits
XHTML; this module validates and normalizes that output into page rows whose
nested flow/block/line/word ordinals and bounding boxes form source locators.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from nhi_rule_history.contracts import (
    ContractError,
    RAW_MANIFEST_SCHEMA,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    manifest_file_entry,
    resolve_run_relative,
    sha256_bytes,
    unique_rows,
    utc_now,
    write_json,
)
from nhi_rule_history.raw.verify import verify_raw


PDF_MANIFEST_SCHEMA = "nhi-rule-history/pdf-extraction-manifest/v1"
PDF_ARTIFACT_SCHEMA = "nhi-rule-history/pdf-extraction-artifact/v1"
PDF_PAGE_SCHEMA = "nhi-rule-history/pdf-layout-page/v1"
PDF_ISSUE_SCHEMA = "nhi-rule-history/pdf-extraction-issue/v1"
PDF_PARSER_VERSION = "nhi-rule-history-poppler-pdf/1.0.0"

TEXT_EXTRACTED = "text_extracted"
IMAGE_ONLY_NEEDS_OCR = "image_only_needs_ocr"
BLOCKING_PARSE_FAILURE = "blocking_parse_failure"
TERMINAL_CLASSIFICATIONS = (
    TEXT_EXTRACTED,
    IMAGE_ONLY_NEEDS_OCR,
    BLOCKING_PARSE_FAILURE,
)

PDF_STAGE_FILES = (
    "pdf-artifacts.jsonl",
    "pdf-pages.jsonl",
    "pdf-issues.jsonl",
)

NON_CLAIM = (
    "Source-local PDF text and geometry observation only; not a legal date, "
    "stable clause identity, amendment event, legal effect, current version, "
    "predecessor/successor relationship, diff, or history-completeness claim."
)

TEXT_ASSEMBLY = {
    "word": "Poppler XHTML word element Unicode text",
    "line": "word texts concatenated without an invented separator",
    "block": "line texts joined with LF",
    "flow": "block texts joined with LF",
    "page": "flow texts joined with LF",
}

_PDF_RESOURCE_KINDS = {
    "official_attachment",
    "official_current_whole_attachment",
    "official_current_chapter_attachment",
}
_PAGE_COUNT_RE = re.compile(r"^Pages:\s*([0-9]+)\s*$", re.MULTILINE)
_BBOX_ATTRIBUTES = ("xMin", "yMin", "xMax", "yMax")


class _ArtifactFailure(Exception):
    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


def _source_row_sha(row: Mapping[str, Any]) -> str:
    clean = {key: value for key, value in row.items() if key != "source_row_sha256"}
    return sha256_bytes(canonical_json_bytes(clean))


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    enriched = dict(row)
    enriched["source_row_sha256"] = _source_row_sha(enriched)
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(enriched))


def _preflight_manifest(
    run_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_raw_manifest_sha256):
        raise ContractError("expected_raw_manifest_sha256 must be lowercase SHA-256")
    manifest_path = run_dir / "raw-manifest.json"
    if not manifest_path.is_file():
        raise ContractError("raw-manifest.json is missing")
    actual_sha = file_sha256(manifest_path)
    if actual_sha != expected_raw_manifest_sha256:
        raise ContractError("raw-manifest.json does not match the expected sealed hash")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("raw-manifest.json is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ContractError("raw-manifest.json must be an object")
    if manifest.get("schema") != RAW_MANIFEST_SCHEMA:
        raise ContractError("raw manifest schema mismatch")
    if manifest.get("status") != "success":
        raise ContractError("raw manifest is not a successful sealed input")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ContractError("raw manifest files must be a non-empty array")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ContractError("raw manifest file entry must be an object")
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename or filename in seen:
            raise ContractError("raw manifest filenames must be unique strings")
        resolve_run_relative(run_dir, filename)
        if (
            not isinstance(entry.get("bytes"), int)
            or entry["bytes"] < 0
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise ContractError("raw manifest file receipt is malformed")
        seen.add(filename)
    return manifest, actual_sha


def _artifact_resources(
    resources: Mapping[str, Mapping[str, Any]],
    links_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for link in iter_jsonl(links_path):
        resource_id = link["resource_id"]
        resource = resources.get(resource_id)
        if resource is None:
            raise ContractError("PDF input link references unknown resource")
        key = (link["artifact_sha256"], resource_id)
        if key in seen:
            continue
        seen.add(key)
        result[link["artifact_sha256"]].append(dict(resource))
    for rows in result.values():
        rows.sort(key=lambda row: row["resource_id"])
    return result


def _resource_declares_pdf(row: Mapping[str, Any]) -> bool:
    if row.get("resource_kind") not in _PDF_RESOURCE_KINDS:
        return False
    locator = row.get("discovery_locator")
    locator = locator if isinstance(locator, Mapping) else {}
    values = (
        row.get("source_label"),
        row.get("source_url"),
        locator.get("attachment_title"),
        locator.get("attachment_visible_label"),
    )
    return any(
        str(value).strip().lower().endswith(".pdf")
        or str(value).strip().lower() == "pdf"
        for value in values
        if value is not None
    )


def _has_pdf_magic(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(5) == b"%PDF-"


def _resource_bindings(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "resource_id": row["resource_id"],
            "resource_kind": row["resource_kind"],
            "source_label": row["source_label"],
        }
        for row in rows
    ]


def _resolve_tool(name: str) -> Path:
    candidate = shutil.which(name)
    if not candidate:
        raise ContractError(f"required local Poppler tool is missing: {name}")
    path = Path(candidate).resolve()
    if not path.is_file():
        raise ContractError(f"required local Poppler tool is not a file: {name}")
    return path


def _tool_environment() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "TZ": "UTC",
    }


def _run_tool(
    arguments: Sequence[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(arguments),
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_tool_environment(),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise _ArtifactFailure(
            "poppler_timeout",
            details={"tool": Path(arguments[0]).name},
        ) from exc
    except OSError as exc:
        raise _ArtifactFailure(
            "poppler_execution_failed",
            details={
                "tool": Path(arguments[0]).name,
                "error_type": type(exc).__name__,
            },
        ) from exc


def _tool_receipt(path: Path) -> dict[str, str]:
    completed = _run_tool([str(path), "-v"], timeout_seconds=30)
    if completed.returncode != 0:
        raise ContractError(f"cannot read Poppler tool version: {path.name}")
    version_output = (completed.stdout + completed.stderr).decode(
        "utf-8", errors="replace"
    )
    first_line = next(
        (line.strip() for line in version_output.splitlines() if line.strip()),
        "",
    )
    if not first_line:
        raise ContractError(f"empty Poppler tool version: {path.name}")
    return {
        "tool": path.name,
        "version": first_line,
        "executable_sha256": file_sha256(path),
    }


def _parser_bundle_sha256() -> str:
    return file_sha256(Path(__file__).resolve())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decimal_text(raw: str | None, *, attribute: str) -> str:
    if raw is None or not raw:
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": f"missing_{attribute}"},
        )
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": f"invalid_{attribute}"},
        ) from exc
    if not value.is_finite():
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": f"nonfinite_{attribute}"},
        )
    return raw


def _bbox(element: ET.Element) -> dict[str, str]:
    values = {
        key: _decimal_text(element.get(key), attribute=key)
        for key in _BBOX_ATTRIBUTES
    }
    if (
        Decimal(values["xMin"]) > Decimal(values["xMax"])
        or Decimal(values["yMin"]) > Decimal(values["yMax"])
    ):
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": "inverted_bbox"},
        )
    return {
        "x_min": values["xMin"],
        "y_min": values["yMin"],
        "x_max": values["xMax"],
        "y_max": values["yMax"],
    }


def _union_bbox(boxes: Iterable[Mapping[str, str]]) -> dict[str, str] | None:
    values = list(boxes)
    if not values:
        return None
    x_min = min(values, key=lambda row: Decimal(row["x_min"]))["x_min"]
    y_min = min(values, key=lambda row: Decimal(row["y_min"]))["y_min"]
    x_max = max(values, key=lambda row: Decimal(row["x_max"]))["x_max"]
    y_max = max(values, key=lambda row: Decimal(row["y_max"]))["y_max"]
    return {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}


def _children(element: ET.Element, expected: str) -> list[ET.Element]:
    children = list(element)
    unexpected = sorted(
        {_local_name(child.tag) for child in children if _local_name(child.tag) != expected}
    )
    if unexpected:
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={
                "reason": "unexpected_hierarchy_element",
                "expected": expected,
                "observed": unexpected,
            },
        )
    return children


def _node_text(children: Sequence[Mapping[str, Any]], separator: str) -> str:
    return separator.join(str(child["text"]) for child in children)


def _parse_poppler_xhtml(
    payload: bytes,
    *,
    artifact_sha256: str,
    expected_page_count: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": "xml_parse_error"},
        ) from exc
    docs = [element for element in root.iter() if _local_name(element.tag) == "doc"]
    if len(docs) != 1:
        raise _ArtifactFailure(
            "malformed_poppler_xhtml",
            details={"reason": "doc_element_count", "observed": len(docs)},
        )
    page_elements = _children(docs[0], "page")
    if len(page_elements) != expected_page_count:
        raise _ArtifactFailure(
            "page_count_mismatch",
            details={
                "pdfinfo_page_count": expected_page_count,
                "xhtml_page_count": len(page_elements),
            },
        )
    pages: list[dict[str, Any]] = []
    counts = {"pages": 0, "flows": 0, "blocks": 0, "lines": 0, "words": 0}
    for page_number, page_element in enumerate(page_elements, 1):
        width = _decimal_text(page_element.get("width"), attribute="page_width")
        height = _decimal_text(page_element.get("height"), attribute="page_height")
        if Decimal(width) <= 0 or Decimal(height) <= 0:
            raise _ArtifactFailure(
                "malformed_poppler_xhtml",
                details={"reason": "nonpositive_page_dimensions"},
            )
        flows: list[dict[str, Any]] = []
        for flow_index, flow_element in enumerate(_children(page_element, "flow"), 1):
            blocks: list[dict[str, Any]] = []
            for block_index, block_element in enumerate(
                _children(flow_element, "block"), 1
            ):
                block_bbox = _bbox(block_element)
                lines: list[dict[str, Any]] = []
                for line_index, line_element in enumerate(
                    _children(block_element, "line"), 1
                ):
                    line_bbox = _bbox(line_element)
                    words: list[dict[str, Any]] = []
                    for word_index, word_element in enumerate(
                        _children(line_element, "word"), 1
                    ):
                        if list(word_element):
                            raise _ArtifactFailure(
                                "malformed_poppler_xhtml",
                                details={"reason": "nested_word_element"},
                            )
                        text = word_element.text or ""
                        words.append(
                            {
                                "word_index": word_index,
                                "text": text,
                                "bbox": _bbox(word_element),
                            }
                        )
                    lines.append(
                        {
                            "line_index": line_index,
                            "text": _node_text(words, ""),
                            "bbox": line_bbox,
                            "words": words,
                        }
                    )
                    counts["words"] += len(words)
                blocks.append(
                    {
                        "block_index": block_index,
                        "text": _node_text(lines, "\n"),
                        "bbox": block_bbox,
                        "lines": lines,
                    }
                )
                counts["lines"] += len(lines)
            flows.append(
                {
                    "flow_index": flow_index,
                    "text": _node_text(blocks, "\n"),
                    "bbox": _union_bbox(block["bbox"] for block in blocks),
                    "blocks": blocks,
                }
            )
            counts["blocks"] += len(blocks)
        pages.append(
            {
                "schema": PDF_PAGE_SCHEMA,
                "artifact_sha256": artifact_sha256,
                "page_number": page_number,
                "text": _node_text(flows, "\n"),
                "bbox": {
                    "x_min": "0",
                    "y_min": "0",
                    "x_max": width,
                    "y_max": height,
                },
                "flows": flows,
                "statement": NON_CLAIM,
            }
        )
        counts["flows"] += len(flows)
        counts["pages"] += 1
    return pages, counts


def _parse_pdfinfo_page_count(stdout: bytes) -> int:
    text = stdout.decode("utf-8", errors="replace")
    match = _PAGE_COUNT_RE.search(text)
    if not match:
        raise _ArtifactFailure("pdfinfo_page_count_missing")
    count = int(match.group(1))
    if count <= 0:
        raise _ArtifactFailure(
            "pdfinfo_page_count_invalid",
            details={"page_count": count},
        )
    return count


def _extract_layout(
    blob_path: Path,
    *,
    artifact_sha256: str,
    pdfinfo_path: Path,
    pdftotext_path: Path,
    temporary_dir: Path,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    info = _run_tool(
        [str(pdfinfo_path), "-enc", "UTF-8", str(blob_path)],
        timeout_seconds=timeout_seconds,
    )
    if info.returncode != 0:
        raise _ArtifactFailure(
            "pdfinfo_failed",
            details={
                "return_code": info.returncode,
                "stdout_sha256": sha256_bytes(info.stdout),
                "stderr_sha256": sha256_bytes(info.stderr),
            },
        )
    page_count = _parse_pdfinfo_page_count(info.stdout)
    xhtml_path = temporary_dir / f"{artifact_sha256}.xhtml"
    extracted = _run_tool(
        [
            str(pdftotext_path),
            "-bbox-layout",
            "-enc",
            "UTF-8",
            str(blob_path),
            str(xhtml_path),
        ],
        timeout_seconds=timeout_seconds,
    )
    if extracted.returncode != 0:
        raise _ArtifactFailure(
            "pdftotext_failed",
            details={
                "return_code": extracted.returncode,
                "stdout_sha256": sha256_bytes(extracted.stdout),
                "stderr_sha256": sha256_bytes(extracted.stderr),
            },
        )
    if not xhtml_path.is_file():
        raise _ArtifactFailure("pdftotext_output_missing")
    xhtml = xhtml_path.read_bytes()
    if not xhtml:
        raise _ArtifactFailure("pdftotext_output_empty")
    pages, hierarchy_counts = _parse_poppler_xhtml(
        xhtml,
        artifact_sha256=artifact_sha256,
        expected_page_count=page_count,
    )
    return pages, {
        "pdfinfo_page_count": page_count,
        "pdfinfo_output_sha256": sha256_bytes(info.stdout),
        "pdfinfo_stderr_sha256": sha256_bytes(info.stderr),
        "pdftotext_stdout_sha256": sha256_bytes(extracted.stdout),
        "pdftotext_stderr_sha256": sha256_bytes(extracted.stderr),
        "poppler_xhtml_sha256": sha256_bytes(xhtml),
        "hierarchy_counts": hierarchy_counts,
    }


def _artifact_layout_sha256(pages: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for page in pages:
        clean = {key: value for key, value in page.items() if key != "source_row_sha256"}
        digest.update(canonical_json_bytes(clean))
    return digest.hexdigest()


def _artifact_text_sha256(pages: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes("\f".join(str(page["text"]) for page in pages).encode("utf-8"))


def _issue_row(
    *,
    parse_run_id: str,
    artifact_sha256: str,
    code: str,
    resource_bindings: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parameters = {
        "resource_ids": [row["resource_id"] for row in resource_bindings],
        "source_labels": [row["source_label"] for row in resource_bindings],
        **dict(details or {}),
    }
    return {
        "schema": PDF_ISSUE_SCHEMA,
        "parse_run_id": parse_run_id,
        "issue_id": sha256_bytes(
            canonical_json_bytes(
                [parse_run_id, artifact_sha256, code, parameters]
            )
        ),
        "artifact_sha256": artifact_sha256,
        "issue_code": code,
        "severity": "error",
        "blocking": True,
        "message_parameters": parameters,
        "statement": NON_CLAIM,
    }


def parse_verified_pdf_run(
    run_dir: Path,
    stage_dir: Path,
    *,
    parse_run_id: str,
    expected_raw_manifest_sha256: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Extract every declared PDF artifact from one hash-bound v2 raw run.

    A successful result means only that the declared PDF denominator was
    exhausted into text-bearing or no-text/OCR-needed source observations.
    Any per-artifact parse failure is recorded, but makes the batch fail closed.
    """

    try:
        uuid.UUID(parse_run_id)
    except ValueError as exc:
        raise ContractError("parse_run_id must be a UUID") from exc
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ContractError("timeout_seconds must be a positive integer")
    raw_manifest, raw_manifest_sha = _preflight_manifest(
        run_dir,
        expected_raw_manifest_sha256=expected_raw_manifest_sha256,
    )
    raw_verification = verify_raw(run_dir)
    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    by_artifact = _artifact_resources(
        resources,
        run_dir / "resource-artifact-links.jsonl",
    )
    pdfinfo_path = _resolve_tool("pdfinfo")
    pdftotext_path = _resolve_tool("pdftotext")
    tools = {
        "pdfinfo": _tool_receipt(pdfinfo_path),
        "pdftotext": _tool_receipt(pdftotext_path),
    }
    parser_bundle_sha = _parser_bundle_sha256()
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "raw_manifest_sha256": raw_manifest_sha,
                "parser_version": PDF_PARSER_VERSION,
                "parser_bundle_sha256": parser_bundle_sha,
                "tools": tools,
                "text_assembly": TEXT_ASSEMBLY,
                "statement": NON_CLAIM,
            }
        )
    )

    stage_dir.mkdir(parents=True, exist_ok=True)
    if any((stage_dir / filename).exists() for filename in PDF_STAGE_FILES):
        raise ContractError("PDF stage files already exist; use a fresh stage_dir")
    if (stage_dir / "pdf-manifest.json").exists():
        raise ContractError("PDF stage manifest already exists; use a fresh stage_dir")
    for filename in PDF_STAGE_FILES:
        (stage_dir / filename).touch()

    counts = {
        "declared_pdf_resources": 0,
        "declared_pdf_artifacts": 0,
        "text_extracted_artifacts": 0,
        "image_only_needs_ocr_artifacts": 0,
        "blocking_parse_failure_artifacts": 0,
        "pages": 0,
        "pages_without_words": 0,
        "flows": 0,
        "blocks": 0,
        "lines": 0,
        "words": 0,
        "blocking_issues": 0,
    }
    started_at = utc_now()
    with tempfile.TemporaryDirectory(prefix=".pdf-poppler-", dir=stage_dir) as temp:
        temporary_dir = Path(temp)
        for artifact_sha256 in sorted(artifacts):
            artifact = artifacts[artifact_sha256]
            resource_rows = by_artifact.get(artifact_sha256, [])
            if not resource_rows:
                continue
            blob_path = resolve_run_relative(run_dir, artifact["content_path"])
            magic_pdf = _has_pdf_magic(blob_path)
            declared_rows = [
                row for row in resource_rows if _resource_declares_pdf(row)
            ]
            if (
                artifact.get("media_type") != "application/pdf"
                and not magic_pdf
                and not declared_rows
            ):
                continue

            bindings = _resource_bindings(resource_rows)
            counts["declared_pdf_resources"] += len(resource_rows)
            counts["declared_pdf_artifacts"] += 1
            classification = BLOCKING_PARSE_FAILURE
            pages: list[dict[str, Any]] = []
            extraction_receipt: dict[str, Any] = {}
            failure: _ArtifactFailure | None = None

            if artifact.get("media_type") != "application/pdf" or not magic_pdf:
                failure = _ArtifactFailure(
                    "declared_pdf_magic_type_mismatch",
                    details={
                        "recorded_media_type": artifact.get("media_type"),
                        "pdf_magic": magic_pdf,
                    },
                )
            else:
                try:
                    pages, extraction_receipt = _extract_layout(
                        blob_path,
                        artifact_sha256=artifact_sha256,
                        pdfinfo_path=pdfinfo_path,
                        pdftotext_path=pdftotext_path,
                        temporary_dir=temporary_dir,
                        timeout_seconds=timeout_seconds,
                    )
                    word_count = extraction_receipt["hierarchy_counts"]["words"]
                    classification = (
                        TEXT_EXTRACTED if word_count else IMAGE_ONLY_NEEDS_OCR
                    )
                except _ArtifactFailure as exc:
                    failure = exc
                except Exception as exc:
                    failure = _ArtifactFailure(
                        "unexpected_pdf_parser_failure",
                        details={"error_type": type(exc).__name__},
                    )

            if failure is not None:
                issue = _issue_row(
                    parse_run_id=parse_run_id,
                    artifact_sha256=artifact_sha256,
                    code=failure.code,
                    resource_bindings=bindings,
                    details=failure.details,
                )
                _append_row(stage_dir / "pdf-issues.jsonl", issue)
                counts["blocking_issues"] += 1
                counts["blocking_parse_failure_artifacts"] += 1
            else:
                for page in pages:
                    page["parse_run_id"] = parse_run_id
                    _append_row(stage_dir / "pdf-pages.jsonl", page)
                hierarchy_counts = extraction_receipt["hierarchy_counts"]
                for key in ("pages", "flows", "blocks", "lines", "words"):
                    counts[key] += hierarchy_counts[key]
                counts["pages_without_words"] += sum(
                    not any(
                        word["text"]
                        for flow in page["flows"]
                        for block in flow["blocks"]
                        for line in block["lines"]
                        for word in line["words"]
                    )
                    for page in pages
                )
                counts[f"{classification}_artifacts"] += 1

            artifact_row = {
                "schema": PDF_ARTIFACT_SCHEMA,
                "parse_run_id": parse_run_id,
                "artifact_sha256": artifact_sha256,
                "byte_size": artifact["byte_size"],
                "media_type": artifact["media_type"],
                "resource_bindings": bindings,
                "classification": classification,
                "page_count": len(pages),
                "layout_sha256": (
                    _artifact_layout_sha256(pages) if failure is None else None
                ),
                "text_sha256": (
                    _artifact_text_sha256(pages) if failure is None else None
                ),
                "tool_output_receipt": (
                    extraction_receipt if failure is None else None
                ),
                "statement": NON_CLAIM,
            }
            _append_row(stage_dir / "pdf-artifacts.jsonl", artifact_row)

    classified = sum(
        counts[f"{classification}_artifacts"]
        for classification in TERMINAL_CLASSIFICATIONS
    )
    if counts["declared_pdf_artifacts"] == 0:
        raise ContractError("verified acquisition run contains no declared PDF artifact")
    if classified != counts["declared_pdf_artifacts"]:
        raise ContractError("declared PDF artifact classification denominator mismatch")
    status = "failed" if counts["blocking_issues"] else "passed"
    completed_at = utc_now()
    files = [
        manifest_file_entry(stage_dir / filename)
        for filename in PDF_STAGE_FILES
    ]
    output_fingerprint = sha256_bytes(canonical_json_bytes(files))
    manifest = {
        "schema": PDF_MANIFEST_SCHEMA,
        "parse_run_id": parse_run_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "raw_manifest_sha256": raw_manifest_sha,
        "raw_manifest_source_plan_sha256": raw_manifest.get("source_plan_sha256"),
        "raw_verification": raw_verification,
        "parser_version": PDF_PARSER_VERSION,
        "parser_bundle_sha256": parser_bundle_sha,
        "tools": tools,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
        "text_assembly": TEXT_ASSEMBLY,
        "locator_contract": {
            "unit": "artifact/page/flow/block/line/word",
            "page_number_base": 1,
            "nested_index_base": 1,
            "coordinates": "Poppler PDF points, top-left origin as emitted",
            "span_binding": (
                "artifact_sha256 plus page_number and nested flow/block/line/"
                "word ordinals; word bbox is the narrowest retained locator"
            ),
        },
        "classifications": {
            TEXT_EXTRACTED: "at least one Poppler word was extracted",
            IMAGE_ONLY_NEEDS_OCR: (
                "zero Poppler words were extracted; OCR/visual review remains open"
            ),
            BLOCKING_PARSE_FAILURE: (
                "artifact could not be admitted as deterministic PDF text/layout"
            ),
        },
        "counts": counts,
        "closure_claims": {
            "declared_pdf_artifacts_exhaustively_classified": (
                classified == counts["declared_pdf_artifacts"]
            ),
            "all_text_bearing_pdf_pages_have_poppler_geometry": (
                status == "passed"
            ),
            "ocr_complete": False,
            "tables_semantically_reconstructed": False,
            "legal_dates_interpreted": False,
            "clause_identity_resolved": False,
            "event_effect_resolved": False,
            "history_complete": False,
        },
        "known_limitations": [
            (
                "Zero-word artifacts and zero-word pages need OCR or visual review; "
                "this lane does not render or recognize images."
            ),
            (
                "Poppler reading order and bbox geometry are preserved, but table "
                "cells, merged cells, borders, and semantic row/column relations "
                "are not reconstructed."
            ),
            (
                "Aggregate line/block/flow/page text is deterministic token "
                "concatenation; word text and word bbox are the exact locator layer."
            ),
        ],
        "statement": NON_CLAIM,
        "files": files,
    }
    write_json(stage_dir / "pdf-manifest.json", manifest)
    if status != "passed":
        raise ContractError(
            f"PDF parse produced {counts['blocking_issues']} blocking issue(s)"
        )
    return manifest


__all__ = [
    "BLOCKING_PARSE_FAILURE",
    "IMAGE_ONLY_NEEDS_OCR",
    "PDF_PARSER_VERSION",
    "TEXT_EXTRACTED",
    "parse_verified_pdf_run",
]
