"""Deterministic, source-local extraction of sealed historical ODS cells.

The parser expands neither repeated rows nor repeated cells.  It preserves
their exact one-based logical ranges, XML-node indices, spans, raw attributes,
rendered text, formula, declared value type, and raw value attributes.  The
stage is read-only with respect to PostgreSQL and makes no legal or historical
semantic inference.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree as ET

from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    manifest_file_entry,
    resolve_run_relative,
    sha256_bytes,
    write_json,
)
from nhi_rule_history.pg.acquisition import (
    AcquisitionLoadError,
    AcquisitionMaterial,
    validate_acquisition_run,
)


ODS_MEDIA_TYPE = "application/vnd.oasis.opendocument.spreadsheet"
PARSER_VERSION = "nhi-rule-history-ods-cell-extractor/1.0.0"
MANIFEST_SCHEMA = "nhi-rule-history/ods-extraction-manifest/v1"
ARTIFACT_SCHEMA = "nhi-rule-history/ods-artifact-extraction/v1"
CELL_SCHEMA = "nhi-rule-history/ods-cell-observation/v1"
PUBLIC_RECEIPT_SCHEMA = (
    "nhi-rule-history/historical-ods-extraction-public-receipt/v1"
)
HISTORICAL_CAPTURE_RECEIPT_SCHEMA = (
    "nhi-rule-history/historical-events-exact-phrase-capture-public-receipt/v1"
)
NON_CLAIM = (
    "Source-local ODS cell observation only; not a legal rule identity, "
    "official amendment event, legal effective date, predecessor/successor "
    "relationship, or history-completeness claim."
)
OUTPUT_FILES = ("ods-artifacts.jsonl", "ods-cells.jsonl")

OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


DOCUMENT_CONTENT = _q(OFFICE_NS, "document-content")
BODY = _q(OFFICE_NS, "body")
SPREADSHEET = _q(OFFICE_NS, "spreadsheet")
TABLE = _q(TABLE_NS, "table")
TABLE_NAME = _q(TABLE_NS, "name")
TABLE_ROW = _q(TABLE_NS, "table-row")
TABLE_CELL = _q(TABLE_NS, "table-cell")
COVERED_CELL = _q(TABLE_NS, "covered-table-cell")
ROW_REPEAT = _q(TABLE_NS, "number-rows-repeated")
COLUMN_REPEAT = _q(TABLE_NS, "number-columns-repeated")
COLUMNS_SPANNED = _q(TABLE_NS, "number-columns-spanned")
ROWS_SPANNED = _q(TABLE_NS, "number-rows-spanned")
MATRIX_COLUMNS_SPANNED = _q(
    TABLE_NS, "number-matrix-columns-spanned"
)
MATRIX_ROWS_SPANNED = _q(TABLE_NS, "number-matrix-rows-spanned")
FORMULA = _q(TABLE_NS, "formula")
VALUE_TYPE = _q(OFFICE_NS, "value-type")
VALUE_ATTRIBUTES = {
    _q(OFFICE_NS, "value"),
    _q(OFFICE_NS, "string-value"),
    _q(OFFICE_NS, "boolean-value"),
    _q(OFFICE_NS, "date-value"),
    _q(OFFICE_NS, "time-value"),
}
TEXT_PARAGRAPH = _q(TEXT_NS, "p")
TEXT_HEADING = _q(TEXT_NS, "h")
TEXT_SPACE = _q(TEXT_NS, "s")
TEXT_SPACE_COUNT = _q(TEXT_NS, "c")
TEXT_TAB = _q(TEXT_NS, "tab")
TEXT_LINE_BREAK = _q(TEXT_NS, "line-break")

_ROW_CONTAINERS = {
    _q(TABLE_NS, "table-header-rows"),
    _q(TABLE_NS, "table-rows"),
    _q(TABLE_NS, "table-row-group"),
}
_SUPPORTED_INLINE_TEXT = {
    TEXT_SPACE,
    TEXT_TAB,
    TEXT_LINE_BREAK,
    _q(TEXT_NS, "span"),
    _q(TEXT_NS, "a"),
    _q(TEXT_NS, "bookmark"),
    _q(TEXT_NS, "bookmark-start"),
    _q(TEXT_NS, "bookmark-end"),
    _q(TEXT_NS, "reference-mark"),
    _q(TEXT_NS, "reference-mark-start"),
    _q(TEXT_NS, "reference-mark-end"),
    _q(TEXT_NS, "soft-page-break"),
}
_SUPPORTED_VALUE_TYPES = {
    "float": _q(OFFICE_NS, "value"),
    "percentage": _q(OFFICE_NS, "value"),
    "currency": _q(OFFICE_NS, "value"),
    "date": _q(OFFICE_NS, "date-value"),
    "time": _q(OFFICE_NS, "time-value"),
    "boolean": _q(OFFICE_NS, "boolean-value"),
    "string": None,
}
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")


class OdsExtractionError(ContractError):
    """A sealed-input, ODS-format, XML, or extraction contract failure."""


def _fail(message: str) -> None:
    raise OdsExtractionError(message)


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source_row_sha256"] = sha256_bytes(
        canonical_json_bytes(result)
    )
    return result


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(canonical_json_bytes(dict(row)))
        stream.flush()
        os.fsync(stream.fileno())


def _positive_integer_attribute(
    element: ET.Element,
    name: str,
    *,
    default: int = 1,
) -> int:
    raw = element.attrib.get(name)
    if raw is None:
        return default
    if not _POSITIVE_INTEGER_RE.fullmatch(raw):
        _fail("ODS repeat/span attribute is not a positive integer")
    return int(raw)


def _xml_node_object(element: ET.Element) -> dict[str, Any]:
    return {
        "tag": element.tag,
        "attributes": dict(sorted(element.attrib.items())),
        "text": element.text,
        "children": [
            {
                "node": _xml_node_object(child),
                "tail": child.tail,
            }
            for child in element
        ],
    }


def _xml_node_fingerprint(element: ET.Element) -> str:
    return sha256_bytes(canonical_json_bytes(_xml_node_object(element)))


def _render_inline_text(
    element: ET.Element,
    unsupported: set[str],
) -> str:
    output: list[str] = []
    if element.text:
        output.append(element.text)
    for child in element:
        if child.tag == TEXT_SPACE:
            count = _positive_integer_attribute(
                child,
                TEXT_SPACE_COUNT,
            )
            output.append(" " * count)
        elif child.tag == TEXT_TAB:
            output.append("\t")
        elif child.tag == TEXT_LINE_BREAK:
            output.append("\n")
        else:
            if child.tag not in _SUPPORTED_INLINE_TEXT:
                unsupported.add(child.tag)
            output.append(_render_inline_text(child, unsupported))
        if child.tail:
            output.append(child.tail)
    return "".join(output)


def _paragraph_rows(
    cell: ET.Element,
) -> tuple[list[dict[str, Any]], str, set[str], set[str]]:
    paragraphs: list[dict[str, Any]] = []
    unsupported_children: set[str] = set()
    unsupported_inline: set[str] = set()
    for child_index, child in enumerate(cell):
        if child.tag not in {TEXT_PARAGRAPH, TEXT_HEADING}:
            unsupported_children.add(child.tag)
            continue
        text = _render_inline_text(child, unsupported_inline)
        paragraphs.append(
            {
                "child_xml_index": child_index,
                "element_name": child.tag,
                "text": text,
                "text_sha256": sha256_bytes(text.encode("utf-8")),
                "xml_node_fingerprint": _xml_node_fingerprint(child),
            }
        )
    return (
        paragraphs,
        "\n".join(row["text"] for row in paragraphs),
        unsupported_children,
        unsupported_inline,
    )


def _iter_rows(
    sheet: ET.Element,
) -> Iterable[tuple[ET.Element, list[dict[str, Any]]]]:
    def walk(
        parent: ET.Element,
        path: list[dict[str, Any]],
    ) -> Iterable[tuple[ET.Element, list[dict[str, Any]]]]:
        for child_index, child in enumerate(parent):
            if child.tag == TABLE_ROW:
                yield child, [
                    *path,
                    {
                        "element_name": child.tag,
                        "child_xml_index": child_index,
                    },
                ]
            elif child.tag in _ROW_CONTAINERS:
                yield from walk(
                    child,
                    [
                        *path,
                        {
                            "element_name": child.tag,
                            "child_xml_index": child_index,
                        },
                    ],
                )
            elif any(desc.tag == TABLE_ROW for desc in child.iter()):
                _fail("ODS row occurs in an unsupported structural container")

    yield from walk(sheet, [])


def _typed_payload(
    *,
    cell_kind: str,
    value_type: str | None,
    formula: str | None,
    rendered_text: str,
    attributes: Mapping[str, str],
    unsupported_children: set[str],
    unsupported_inline: set[str],
) -> tuple[dict[str, Any], list[str], bool]:
    problems: list[str] = []
    raw_values = {
        name: attributes[name]
        for name in sorted(VALUE_ATTRIBUTES)
        if name in attributes
    }
    if unsupported_children:
        problems.extend(
            f"unsupported_cell_child:{name}"
            for name in sorted(unsupported_children)
        )
    if unsupported_inline:
        problems.extend(
            f"unsupported_inline_text_element:{name}"
            for name in sorted(unsupported_inline)
        )

    primary_attribute: str | None = None
    primary_raw_value: str | None = None
    if value_type is None:
        if raw_values or formula is not None:
            problems.append("undeclared_value_type_with_typed_payload")
        status = (
            "empty_or_text_only"
            if not problems
            else "unsupported_preserved_raw"
        )
    elif value_type not in _SUPPORTED_VALUE_TYPES:
        problems.append(f"unsupported_value_type:{value_type}")
        status = "unsupported_preserved_raw"
    else:
        expected = _SUPPORTED_VALUE_TYPES[value_type]
        if value_type == "string":
            string_attribute = _q(OFFICE_NS, "string-value")
            if string_attribute in attributes:
                primary_attribute = string_attribute
                primary_raw_value = attributes[string_attribute]
            else:
                primary_attribute = "rendered_text"
                primary_raw_value = rendered_text
        else:
            primary_attribute = expected
            primary_raw_value = attributes.get(expected)
            if primary_raw_value is None:
                problems.append(
                    "declared_value_type_missing_expected_value_attribute"
                )
        status = (
            "supported_exact_raw"
            if not problems
            else "unsupported_preserved_raw"
        )
    if cell_kind == "covered_table_cell" and (
        value_type is not None
        or formula is not None
        or raw_values
        or rendered_text
    ):
        problems.append("covered_cell_contains_payload")
        status = "unsupported_preserved_raw"

    zero_payload = (
        rendered_text == ""
        and formula is None
        and not raw_values
    )
    return (
        {
            "status": status,
            "value_type_raw": value_type,
            "primary_value_attribute": primary_attribute,
            "primary_value_raw": primary_raw_value,
            "raw_value_attributes": raw_values,
        },
        sorted(set(problems)),
        zero_payload,
    )


def _cell_row(
    *,
    extraction_id: str,
    artifact_sha256: str,
    content_xml_sha256: str,
    source_resource_ids: list[str],
    source_labels: list[str],
    sheet_index: int,
    sheet_name: str,
    row_xml_index: int,
    row_container_path: list[dict[str, Any]],
    row_logical_start: int,
    row_logical_end: int,
    row_repeat: int,
    row: ET.Element,
    cell_xml_index: int,
    column_logical_start: int,
    cell: ET.Element,
) -> dict[str, Any]:
    if cell.tag == TABLE_CELL:
        cell_kind = "table_cell"
    elif cell.tag == COVERED_CELL:
        cell_kind = "covered_table_cell"
    else:
        _fail("unsupported ODS row child is not a cell")
    cell_repeat = _positive_integer_attribute(cell, COLUMN_REPEAT)
    column_logical_end = column_logical_start + cell_repeat - 1
    columns_spanned = _positive_integer_attribute(cell, COLUMNS_SPANNED)
    rows_spanned = _positive_integer_attribute(cell, ROWS_SPANNED)
    matrix_columns_spanned = _positive_integer_attribute(
        cell, MATRIX_COLUMNS_SPANNED
    )
    matrix_rows_spanned = _positive_integer_attribute(
        cell, MATRIX_ROWS_SPANNED
    )
    paragraphs, rendered_text, unsupported_children, unsupported_inline = (
        _paragraph_rows(cell)
    )
    formula = cell.attrib.get(FORMULA)
    value_type = cell.attrib.get(VALUE_TYPE)
    typed_payload, unsupported_codes, zero_payload = _typed_payload(
        cell_kind=cell_kind,
        value_type=value_type,
        formula=formula,
        rendered_text=rendered_text,
        attributes=cell.attrib,
        unsupported_children=unsupported_children,
        unsupported_inline=unsupported_inline,
    )
    locator = {
        "sheet_xml_index": sheet_index,
        "sheet_name": sheet_name,
        "row_xml_index": row_xml_index,
        "row_container_path": row_container_path,
        "row_logical_start": row_logical_start,
        "row_logical_end_inclusive": row_logical_end,
        "row_repeat": row_repeat,
        "cell_xml_index": cell_xml_index,
        "column_logical_start": column_logical_start,
        "column_logical_end_inclusive": column_logical_end,
        "cell_repeat": cell_repeat,
    }
    identity = {
        "artifact_sha256": artifact_sha256,
        "locator": locator,
        "xml_node_fingerprint": _xml_node_fingerprint(cell),
    }
    result = {
        "schema": CELL_SCHEMA,
        "extraction_id": extraction_id,
        "cell_id": sha256_bytes(canonical_json_bytes(identity)),
        "artifact_sha256": artifact_sha256,
        "content_xml_sha256": content_xml_sha256,
        "source_resource_ids": source_resource_ids,
        "source_labels": source_labels,
        "locator": locator,
        "cell_kind": cell_kind,
        "repeat": {
            "row_repeat": row_repeat,
            "cell_repeat": cell_repeat,
            "logical_cell_multiplicity": row_repeat * cell_repeat,
        },
        "span": {
            "number_columns_spanned": columns_spanned,
            "number_rows_spanned": rows_spanned,
            "number_matrix_columns_spanned": matrix_columns_spanned,
            "number_matrix_rows_spanned": matrix_rows_spanned,
        },
        "text": {
            "rendered": rendered_text,
            "rendered_sha256": sha256_bytes(
                rendered_text.encode("utf-8")
            ),
            "paragraphs": paragraphs,
        },
        "formula_raw": formula,
        "declared_value_type_raw": value_type,
        "typed_payload": typed_payload,
        "zero_payload": zero_payload,
        "unsupported_codes": unsupported_codes,
        "raw_attributes": dict(sorted(cell.attrib.items())),
        "row_raw_attributes": dict(sorted(row.attrib.items())),
        "xml_node_fingerprint": identity["xml_node_fingerprint"],
        "statement": NON_CLAIM,
    }
    return _source_row(result)


def _read_ods_content(path: Path) -> tuple[bytes, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                _fail("ODS ZIP contains duplicate member names")
            if "mimetype" not in names or "content.xml" not in names:
                _fail("ODS ZIP is missing mimetype or content.xml")
            if any(info.flag_bits & 0x1 for info in archive.infolist()):
                _fail("encrypted ODS ZIP members are unsupported")
            if archive.testzip() is not None:
                _fail("ODS ZIP member CRC verification failed")
            if archive.read("mimetype") != ODS_MEDIA_TYPE.encode("ascii"):
                _fail("ODS mimetype does not match declared media type")
            content = archive.read("content.xml")
    except OdsExtractionError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise OdsExtractionError("declared ODS ZIP is unreadable") from exc
    if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        _fail("ODS content.xml contains a forbidden DTD or entity declaration")
    return content, sha256_bytes(content)


def _parse_artifact(
    *,
    extraction_id: str,
    artifact: Mapping[str, Any],
    blob_path: Path,
    source_rows: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    content, content_xml_sha256 = _read_ods_content(blob_path)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise OdsExtractionError("ODS content.xml is malformed") from exc
    if root.tag != DOCUMENT_CONTENT:
        _fail("ODS content.xml root is not office:document-content")
    body = root.find(BODY)
    spreadsheet = body.find(SPREADSHEET) if body is not None else None
    if spreadsheet is None:
        _fail("ODS content.xml has no office:spreadsheet body")
    sheets = [child for child in spreadsheet if child.tag == TABLE]
    if not sheets:
        _fail("ODS content.xml contains no table:table sheet")

    source_resource_ids = sorted(
        {str(row["resource_id"]) for row in source_rows}
    )
    source_labels = sorted(
        {
            str(row["source_label"])
            for row in source_rows
            if row.get("source_label")
        }
    )
    cells: list[dict[str, Any]] = []
    sheet_summaries: list[dict[str, Any]] = []
    artifact_counts: Counter[str] = Counter()
    unsupported_code_counts: Counter[str] = Counter()
    for sheet_index, sheet in enumerate(sheets):
        sheet_name = sheet.attrib.get(TABLE_NAME)
        if not isinstance(sheet_name, str) or not sheet_name:
            _fail("ODS sheet has no non-empty table:name")
        row_logical_cursor = 1
        sheet_counts: Counter[str] = Counter()
        for row_xml_index, (row, container_path) in enumerate(
            _iter_rows(sheet)
        ):
            row_repeat = _positive_integer_attribute(row, ROW_REPEAT)
            row_logical_end = row_logical_cursor + row_repeat - 1
            column_logical_cursor = 1
            cell_xml_index = 0
            for child in row:
                if child.tag not in {TABLE_CELL, COVERED_CELL}:
                    _fail("ODS row contains an unsupported non-cell child")
                cell = _cell_row(
                    extraction_id=extraction_id,
                    artifact_sha256=artifact["artifact_sha256"],
                    content_xml_sha256=content_xml_sha256,
                    source_resource_ids=source_resource_ids,
                    source_labels=source_labels,
                    sheet_index=sheet_index,
                    sheet_name=sheet_name,
                    row_xml_index=row_xml_index,
                    row_container_path=container_path,
                    row_logical_start=row_logical_cursor,
                    row_logical_end=row_logical_end,
                    row_repeat=row_repeat,
                    row=row,
                    cell_xml_index=cell_xml_index,
                    column_logical_start=column_logical_cursor,
                    cell=child,
                )
                cells.append(cell)
                multiplicity = cell["repeat"][
                    "logical_cell_multiplicity"
                ]
                sheet_counts["physical_cells"] += 1
                sheet_counts["logical_cells"] += multiplicity
                if cell["cell_kind"] == "covered_table_cell":
                    sheet_counts["physical_covered_cells"] += 1
                    sheet_counts["logical_covered_cells"] += multiplicity
                if cell["zero_payload"]:
                    sheet_counts["physical_zero_payload_cells"] += 1
                    sheet_counts["logical_zero_payload_cells"] += (
                        multiplicity
                    )
                if cell["formula_raw"] is not None:
                    sheet_counts["physical_formula_cells"] += 1
                    sheet_counts["logical_formula_cells"] += multiplicity
                if cell["text"]["rendered"] != "":
                    sheet_counts["physical_nonempty_text_cells"] += 1
                    sheet_counts["logical_nonempty_text_cells"] += (
                        multiplicity
                    )
                if cell["unsupported_codes"]:
                    sheet_counts["physical_unsupported_cells"] += 1
                    sheet_counts["logical_unsupported_cells"] += multiplicity
                    unsupported_code_counts.update(
                        cell["unsupported_codes"]
                    )
                column_logical_cursor = (
                    cell["locator"]["column_logical_end_inclusive"] + 1
                )
                cell_xml_index += 1
            sheet_counts["physical_rows"] += 1
            sheet_counts["logical_rows"] += row_repeat
            row_logical_cursor = row_logical_end + 1
        sheet_summary = {
            "sheet_xml_index": sheet_index,
            "sheet_name": sheet_name,
            "sheet_raw_attributes": dict(sorted(sheet.attrib.items())),
            "counts": dict(sorted(sheet_counts.items())),
        }
        sheet_summaries.append(sheet_summary)
        artifact_counts.update(sheet_counts)
    artifact_counts["sheets"] = len(sheets)
    artifact_counts["rendered_text_characters"] = sum(
        len(row["text"]["rendered"]) for row in cells
    )
    artifact_counts["rendered_text_bytes"] = sum(
        len(row["text"]["rendered"].encode("utf-8")) for row in cells
    )
    artifact_result = _source_row(
        {
            "schema": ARTIFACT_SCHEMA,
            "extraction_id": extraction_id,
            "artifact_sha256": artifact["artifact_sha256"],
            "artifact_byte_size": artifact["byte_size"],
            "content_xml_sha256": content_xml_sha256,
            "source_resource_ids": source_resource_ids,
            "source_labels": source_labels,
            "sheets": sheet_summaries,
            "counts": dict(sorted(artifact_counts.items())),
            "unsupported_code_counts": dict(
                sorted(unsupported_code_counts.items())
            ),
            "cell_row_set_fingerprint": _row_set_fingerprint(cells),
            "statement": NON_CLAIM,
        }
    )
    return artifact_result, cells


def _row_set_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = sha256_bytes(
        "".join(
            f"{row['source_row_sha256']}\n"
            for row in sorted(
                rows,
                key=lambda item: item["source_row_sha256"],
            )
        ).encode("ascii")
    )
    return digest


def _artifact_sources(
    acquisition: AcquisitionMaterial,
) -> dict[str, list[dict[str, Any]]]:
    resources = {
        row["resource_id"]: row
        for row in acquisition.rows["discovered-resources.jsonl"]
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for link in acquisition.rows["resource-artifact-links.jsonl"]:
        resource = resources.get(link["resource_id"])
        if resource is None:
            _fail("ODS artifact link references an unknown resource")
        key = (link["artifact_sha256"], link["resource_id"])
        if key in seen:
            continue
        seen.add(key)
        result[link["artifact_sha256"]].append(dict(resource))
    for rows in result.values():
        rows.sort(key=lambda row: row["resource_id"])
    return result


def _aggregate_counts(
    artifact_rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    result: Counter[str] = Counter()
    materialized = list(artifact_rows)
    result["declared_ods_artifacts"] = len(materialized)
    result["parsed_ods_artifacts"] = len(materialized)
    for artifact in materialized:
        for key, value in artifact["counts"].items():
            result[key] += int(value)
        if artifact["counts"].get("physical_zero_payload_cells", 0):
            result["artifacts_with_zero_payload_cells"] += 1
        if artifact["counts"].get("physical_unsupported_cells", 0):
            result["artifacts_with_unsupported_cells"] += 1
    return dict(sorted(result.items()))


def parse_verified_ods_run(
    run_dir: Path,
    stage_dir: Path,
    *,
    expected_ods_artifact_count: int,
) -> dict[str, Any]:
    """Exhaustively extract all manifest-bound ODS artifacts into a new stage."""

    if (
        isinstance(expected_ods_artifact_count, bool)
        or not isinstance(expected_ods_artifact_count, int)
        or expected_ods_artifact_count < 1
    ):
        _fail("expected_ods_artifact_count must be a positive integer")
    run_dir = Path(run_dir)
    stage_dir = Path(stage_dir)
    if stage_dir.exists():
        _fail("ODS stage directory already exists")
    try:
        acquisition = validate_acquisition_run(run_dir)
    except (AcquisitionLoadError, ContractError, OSError) as exc:
        raise OdsExtractionError(
            "sealed acquisition input failed verification"
        ) from exc
    ods_artifacts = sorted(
        (
            row
            for row in acquisition.rows["raw-artifacts.jsonl"]
            if row.get("media_type") == ODS_MEDIA_TYPE
        ),
        key=lambda row: row["artifact_sha256"],
    )
    if len(ods_artifacts) != expected_ods_artifact_count:
        _fail("sealed ODS artifact denominator differs from expectation")
    sources = _artifact_sources(acquisition)
    missing_sources = [
        row["artifact_sha256"]
        for row in ods_artifacts
        if not sources.get(row["artifact_sha256"])
    ]
    if missing_sources:
        _fail("sealed ODS artifact has no source-resource link")

    parser_code_sha256 = file_sha256(Path(__file__))
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "parser_version": PARSER_VERSION,
                "parser_code_sha256": parser_code_sha256,
                "raw_manifest_sha256": acquisition.raw_manifest_sha256,
                "acquisition_run_id": acquisition.run_id,
                "acquisition_sealed_fingerprint": (
                    acquisition.sealed_fingerprint
                ),
                "expected_ods_artifact_count": (
                    expected_ods_artifact_count
                ),
                "artifact_sha256s": [
                    row["artifact_sha256"] for row in ods_artifacts
                ],
                "non_claim": NON_CLAIM,
            }
        )
    )
    extraction_id = sha256_bytes(
        canonical_json_bytes(
            ["ods-extraction", input_fingerprint]
        )
    )

    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{stage_dir.name}.",
            dir=stage_dir.parent,
        )
    )
    try:
        artifact_rows: list[dict[str, Any]] = []
        cell_rows: list[dict[str, Any]] = []
        for artifact in ods_artifacts:
            blob_path = resolve_run_relative(
                run_dir, artifact["content_path"]
            )
            artifact_row, artifact_cells = _parse_artifact(
                extraction_id=extraction_id,
                artifact=artifact,
                blob_path=blob_path,
                source_rows=sources[artifact["artifact_sha256"]],
            )
            artifact_rows.append(artifact_row)
            cell_rows.extend(artifact_cells)
        if len(artifact_rows) != expected_ods_artifact_count:
            _fail("ODS parser did not exhaust the sealed denominator")
        _write_jsonl(temporary / "ods-artifacts.jsonl", artifact_rows)
        _write_jsonl(temporary / "ods-cells.jsonl", cell_rows)
        counts = _aggregate_counts(artifact_rows)
        unsupported_code_counts: Counter[str] = Counter()
        unsupported_cell_locators: list[dict[str, Any]] = []
        for cell in cell_rows:
            if not cell["unsupported_codes"]:
                continue
            unsupported_code_counts.update(cell["unsupported_codes"])
            unsupported_cell_locators.append(
                {
                    "artifact_sha256": cell["artifact_sha256"],
                    "cell_id": cell["cell_id"],
                    "locator": cell["locator"],
                    "unsupported_codes": cell["unsupported_codes"],
                    "source_row_sha256": cell["source_row_sha256"],
                }
            )
        files = [
            manifest_file_entry(temporary / filename)
            for filename in OUTPUT_FILES
        ]
        output_fingerprint = sha256_bytes(
            canonical_json_bytes(
                {
                    "counts": counts,
                    "unsupported_code_counts": dict(
                        sorted(unsupported_code_counts.items())
                    ),
                    "artifact_row_set_fingerprint": (
                        _row_set_fingerprint(artifact_rows)
                    ),
                    "cell_row_set_fingerprint": _row_set_fingerprint(
                        cell_rows
                    ),
                    "files": files,
                }
            )
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "status": "passed",
            "extraction_id": extraction_id,
            "parser_version": PARSER_VERSION,
            "parser_code_sha256": parser_code_sha256,
            "acquisition_run_id": acquisition.run_id,
            "acquisition_sealed_fingerprint": (
                acquisition.sealed_fingerprint
            ),
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "expected_ods_artifact_count": expected_ods_artifact_count,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "counts": counts,
            "unsupported_code_counts": dict(
                sorted(unsupported_code_counts.items())
            ),
            "unsupported_cell_locators": unsupported_cell_locators,
            "row_set_fingerprints": {
                "ods_artifact": _row_set_fingerprint(artifact_rows),
                "ods_cell": _row_set_fingerprint(cell_rows),
            },
            "files": files,
            "closure_claims": {
                "sealed_ods_denominator_exhausted": (
                    counts["declared_ods_artifacts"]
                    == counts["parsed_ods_artifacts"]
                    == expected_ods_artifact_count
                ),
                "repeat_ranges_preserved_without_expansion": True,
                "unsupported_cells_preserved_and_reported": True,
                "legal_semantics_inferred": False,
                "history_complete": False,
                "postgresql_written": False,
            },
            "statement": NON_CLAIM,
        }
        write_json(temporary / "ods-manifest.json", manifest)
        os.replace(temporary, stage_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def _read_json_object(
    value: Path | Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        try:
            result = json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OdsExtractionError(
                f"{label} is missing or invalid JSON"
            ) from exc
    if not isinstance(result, dict):
        _fail(f"{label} is not a JSON object")
    return result


def _read_stage_rows(
    path: Path,
    *,
    schema: str,
    extraction_id: str,
    identity_key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise OdsExtractionError(
            "ODS stage output file is missing"
        ) from exc
    with stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                _fail("ODS stage JSONL contains a blank row")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OdsExtractionError(
                    "ODS stage JSONL is invalid"
                ) from exc
            if (
                not isinstance(row, dict)
                or row.get("schema") != schema
                or row.get("extraction_id") != extraction_id
            ):
                _fail("ODS stage JSONL row contract mismatch")
            identity = row.get(identity_key)
            if (
                not isinstance(identity, str)
                or not identity
                or identity in identities
            ):
                _fail("ODS stage JSONL identity is missing or duplicated")
            identities.add(identity)
            claimed = row.get("source_row_sha256")
            clean = dict(row)
            clean.pop("source_row_sha256", None)
            if claimed != sha256_bytes(canonical_json_bytes(clean)):
                _fail("ODS stage JSONL source row hash mismatch")
            rows.append(row)
    return rows


def verify_ods_stage(stage_dir: Path) -> dict[str, Any]:
    """Freshly recompute a completed ODS stage's deterministic contracts."""

    stage_dir = Path(stage_dir)
    manifest = _read_json_object(
        stage_dir / "ods-manifest.json",
        label="ODS manifest",
    )
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "passed"
    ):
        _fail("ODS manifest schema or status mismatch")
    extraction_id = manifest.get("extraction_id")
    if not isinstance(extraction_id, str) or not extraction_id:
        _fail("ODS manifest extraction_id is missing")
    manifested_files = {
        row.get("filename"): row
        for row in manifest.get("files", [])
        if isinstance(row, Mapping)
    }
    if set(manifested_files) != set(OUTPUT_FILES):
        _fail("ODS manifest output file set mismatch")
    file_receipts: list[dict[str, Any]] = []
    for filename in OUTPUT_FILES:
        path = stage_dir / filename
        entry = manifested_files[filename]
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            _fail("ODS manifested output file changed")
        file_receipts.append(dict(entry))
    artifact_rows = _read_stage_rows(
        stage_dir / "ods-artifacts.jsonl",
        schema=ARTIFACT_SCHEMA,
        extraction_id=extraction_id,
        identity_key="artifact_sha256",
    )
    cell_rows = _read_stage_rows(
        stage_dir / "ods-cells.jsonl",
        schema=CELL_SCHEMA,
        extraction_id=extraction_id,
        identity_key="cell_id",
    )
    artifact_by_sha = {
        row["artifact_sha256"]: row for row in artifact_rows
    }
    cells_by_artifact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in cell_rows:
        artifact_sha256 = cell.get("artifact_sha256")
        if artifact_sha256 not in artifact_by_sha:
            _fail("ODS cell references an unknown artifact")
        cells_by_artifact[artifact_sha256].append(cell)
    for artifact_sha256, artifact in artifact_by_sha.items():
        if artifact.get("cell_row_set_fingerprint") != (
            _row_set_fingerprint(cells_by_artifact[artifact_sha256])
        ):
            _fail("ODS artifact cell row-set fingerprint mismatch")
    counts = _aggregate_counts(artifact_rows)
    if counts != manifest.get("counts"):
        _fail("ODS manifest counts differ from artifact rows")
    if counts.get("physical_cells", 0) != len(cell_rows):
        _fail("ODS physical cell count differs from cell rows")
    unsupported_code_counts: Counter[str] = Counter()
    unsupported_cell_locators: list[dict[str, Any]] = []
    for cell in cell_rows:
        codes = cell.get("unsupported_codes")
        if not isinstance(codes, list):
            _fail("ODS cell unsupported_codes is not an array")
        if not codes:
            continue
        unsupported_code_counts.update(codes)
        unsupported_cell_locators.append(
            {
                "artifact_sha256": cell["artifact_sha256"],
                "cell_id": cell["cell_id"],
                "locator": cell["locator"],
                "unsupported_codes": codes,
                "source_row_sha256": cell["source_row_sha256"],
            }
        )
    unsupported_code_counts_dict = dict(
        sorted(unsupported_code_counts.items())
    )
    if (
        manifest.get("unsupported_code_counts")
        != unsupported_code_counts_dict
        or manifest.get("unsupported_cell_locators")
        != unsupported_cell_locators
    ):
        _fail("ODS unsupported-cell receipt differs from cell rows")
    row_set_fingerprints = {
        "ods_artifact": _row_set_fingerprint(artifact_rows),
        "ods_cell": _row_set_fingerprint(cell_rows),
    }
    if manifest.get("row_set_fingerprints") != row_set_fingerprints:
        _fail("ODS manifest row-set fingerprints mismatch")
    parser_code_sha256 = file_sha256(Path(__file__))
    if manifest.get("parser_code_sha256") != parser_code_sha256:
        _fail("ODS parser code fingerprint mismatch")
    expected_count = manifest.get("expected_ods_artifact_count")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 1
        or expected_count != len(artifact_rows)
    ):
        _fail("ODS manifest denominator is invalid")
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "parser_version": PARSER_VERSION,
                "parser_code_sha256": parser_code_sha256,
                "raw_manifest_sha256": manifest[
                    "raw_manifest_sha256"
                ],
                "acquisition_run_id": manifest["acquisition_run_id"],
                "acquisition_sealed_fingerprint": manifest[
                    "acquisition_sealed_fingerprint"
                ],
                "expected_ods_artifact_count": expected_count,
                "artifact_sha256s": sorted(artifact_by_sha),
                "non_claim": NON_CLAIM,
            }
        )
    )
    expected_extraction_id = sha256_bytes(
        canonical_json_bytes(["ods-extraction", input_fingerprint])
    )
    if (
        manifest.get("input_fingerprint") != input_fingerprint
        or extraction_id != expected_extraction_id
    ):
        _fail("ODS manifest input fingerprint or extraction_id mismatch")
    output_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "counts": counts,
                "unsupported_code_counts": unsupported_code_counts_dict,
                "artifact_row_set_fingerprint": row_set_fingerprints[
                    "ods_artifact"
                ],
                "cell_row_set_fingerprint": row_set_fingerprints[
                    "ods_cell"
                ],
                "files": file_receipts,
            }
        )
    )
    if manifest.get("output_fingerprint") != output_fingerprint:
        _fail("ODS manifest output fingerprint mismatch")
    return {
        "manifest": manifest,
        "manifest_sha256": file_sha256(
            stage_dir / "ods-manifest.json"
        ),
        "files": file_receipts,
        "artifact_rows": artifact_rows,
        "cell_rows": cell_rows,
        "counts": counts,
        "unsupported_code_counts": unsupported_code_counts_dict,
        "unsupported_cell_locators": unsupported_cell_locators,
        "row_set_fingerprints": row_set_fingerprints,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
    }


def build_public_ods_receipt(
    *,
    stage_dir: Path,
    historical_capture_receipt: Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a passed stage and return a compact public audit receipt."""

    stage_dir = Path(stage_dir)
    verified = verify_ods_stage(stage_dir)
    manifest = verified["manifest"]
    capture = _read_json_object(
        historical_capture_receipt,
        label="historical capture receipt",
    )
    if capture.get("schema") != HISTORICAL_CAPTURE_RECEIPT_SCHEMA:
        _fail("historical capture receipt schema mismatch")
    accepted = capture.get("accepted_acquisition")
    scope = capture.get("scope")
    if not isinstance(accepted, Mapping) or not isinstance(scope, Mapping):
        _fail("historical capture receipt lacks sealed acquisition binding")
    if (
        accepted.get("run_id") != manifest.get("acquisition_run_id")
        or accepted.get("state") != "sealed"
        or accepted.get("sealed_fingerprint")
        != manifest.get("acquisition_sealed_fingerprint")
        or accepted.get("raw_manifest_sha256")
        != manifest.get("raw_manifest_sha256")
    ):
        _fail("ODS manifest differs from the accepted sealed acquisition")
    media_counts = accepted.get("media_type_counts")
    if (
        not isinstance(media_counts, Mapping)
        or media_counts.get(ODS_MEDIA_TYPE)
        != manifest.get("expected_ods_artifact_count")
    ):
        _fail("historical receipt ODS denominator mismatch")
    file_receipts = verified["files"]
    manifest_sha256 = verified["manifest_sha256"]
    counts = manifest["counts"]
    receipt = {
        "schema": PUBLIC_RECEIPT_SCHEMA,
        "status": "passed_typed_source_local_extraction",
        "scope": {
            key: scope.get(key)
            for key in (
                "query_start",
                "query_end",
                "capture_cut",
                "query",
                "query_mode",
                "source_plan_sha256",
            )
        },
        "sealed_input": {
            "acquisition_run_id": manifest["acquisition_run_id"],
            "acquisition_sealed_fingerprint": manifest[
                "acquisition_sealed_fingerprint"
            ],
            "raw_manifest_sha256": manifest["raw_manifest_sha256"],
            "declared_ods_artifacts": manifest[
                "expected_ods_artifact_count"
            ],
        },
        "extraction": {
            "extraction_id": manifest["extraction_id"],
            "parser_version": manifest["parser_version"],
            "parser_code_sha256": manifest["parser_code_sha256"],
            "input_fingerprint": manifest["input_fingerprint"],
            "output_fingerprint": manifest["output_fingerprint"],
            "manifest_sha256": manifest_sha256,
            "files": file_receipts,
            "row_set_fingerprints": manifest[
                "row_set_fingerprints"
            ],
        },
        "counts": counts,
        "zero_content": {
            "definition": (
                "rendered text is empty, formula is absent, and no office "
                "value/string-value/boolean-value/date-value/time-value "
                "attribute is present"
            ),
            "physical_cells": counts.get(
                "physical_zero_payload_cells", 0
            ),
            "logical_cells_after_repeat": counts.get(
                "logical_zero_payload_cells", 0
            ),
            "affected_artifacts": counts.get(
                "artifacts_with_zero_payload_cells", 0
            ),
            "exact_locators_file": "ods-cells.jsonl",
            "locator_predicate": "zero_payload == true",
        },
        "unsupported_cells": {
            "physical_cells": counts.get(
                "physical_unsupported_cells", 0
            ),
            "logical_cells_after_repeat": counts.get(
                "logical_unsupported_cells", 0
            ),
            "affected_artifacts": counts.get(
                "artifacts_with_unsupported_cells", 0
            ),
            "code_counts": manifest["unsupported_code_counts"],
            "exact_locators": manifest["unsupported_cell_locators"],
        },
        "closure": manifest["closure_claims"],
        "claims": {
            "all_manifest_declared_ods_artifacts_parsed": True,
            "cell_source_payload_and_locators_preserved": True,
            "legal_rule_identity_resolved": False,
            "legal_effective_date_resolved": False,
            "amendment_effect_resolved": False,
            "history_complete": False,
            "postgresql_written": False,
        },
        "statement": NON_CLAIM,
    }
    assert_public_value(receipt)
    return receipt
