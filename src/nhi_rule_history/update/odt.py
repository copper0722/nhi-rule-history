"""Lossless-enough source blocks for bounded agent proposals.

These blocks are not canonical clauses.  They provide deterministic locators
and exact text hashes so a model proposal can be verified against official ODT
bytes without granting the model authority over identity or legal history.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any
from xml.etree import ElementTree

from nhi_rule_history.contracts import ContractError, sha256_bytes, stable_id


_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TAG_P = f"{{{_TEXT}}}p"
_TAG_H = f"{{{_TEXT}}}h"
_TAG_TABLE = f"{{{_TABLE}}}table"
_TAG_ROW = f"{{{_TABLE}}}table-row"
_TAG_HEADER_ROWS = f"{{{_TABLE}}}table-header-rows"
_TAG_CELL = f"{{{_TABLE}}}table-cell"
_TAG_COVERED_CELL = f"{{{_TABLE}}}covered-table-cell"
_ATTR_COLUMNS_REPEATED = f"{{{_TABLE}}}number-columns-repeated"
_ATTR_ROWS_REPEATED = f"{{{_TABLE}}}number-rows-repeated"
_ATTR_COLUMNS_SPANNED = f"{{{_TABLE}}}number-columns-spanned"
_ATTR_ROWS_SPANNED = f"{{{_TABLE}}}number-rows-spanned"

ODT_STRUCTURAL_FACTS_SCHEMA = "nhi-rule-history/odt-structural-facts/v1"


def _text(element: ElementTree.Element) -> str:
    return "".join(element.itertext())


def _positive_integer_attribute(
    element: ElementTree.Element,
    attribute: str,
) -> int:
    raw = element.attrib.get(attribute, "1")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError(
            "ODT table repetition/span attribute is invalid"
        ) from exc
    if value < 1:
        raise ContractError("ODT table repetition/span attribute is invalid")
    return value


def _cell_paragraphs_excluding_nested_tables(
    cell: ElementTree.Element,
) -> list[ElementTree.Element]:
    """Return this cell's paragraphs without re-emitting nested-table text.

    ``Element.iter()`` crosses into nested ODF tables.  The outer table would
    therefore emit those paragraphs once as part of its cell, and the normal
    document traversal would emit them a second time when it reached the
    nested table itself.  Walk non-table descendants only; nested tables retain
    their own deterministic table/row/cell locators later in the traversal.
    """

    paragraphs: list[ElementTree.Element] = []

    def visit(parent: ElementTree.Element) -> None:
        for child in parent:
            if child.tag == _TAG_TABLE:
                continue
            if child.tag in {_TAG_P, _TAG_H}:
                paragraphs.append(child)
            visit(child)

    visit(cell)
    return paragraphs


def _rows_excluding_nested_tables(
    table: ElementTree.Element,
) -> list[ElementTree.Element]:
    rows: list[ElementTree.Element] = []

    def visit(parent: ElementTree.Element) -> None:
        for child in parent:
            if child.tag == _TAG_TABLE:
                continue
            if child.tag == _TAG_ROW:
                rows.append(child)
                continue
            visit(child)

    visit(table)
    return rows


def inspect_odt_document(
    payload: bytes,
    artifact_sha256: str,
) -> dict[str, Any]:
    """Return exact-once source blocks plus fail-closed structural facts."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            mimetype = archive.read("mimetype").strip()
            if mimetype != b"application/vnd.oasis.opendocument.text":
                raise ContractError("attachment is not an ODT document")
            content = archive.read("content.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ContractError("ODT container is malformed") from exc
    if b"<!DOCTYPE" in content[:4096].upper() or b"<!ENTITY" in content[:4096].upper():
        raise ContractError("ODT XML declarations are not allowed")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ContractError("ODT content.xml is malformed") from exc
    body = root.find(f".//{{{_OFFICE}}}text")
    if body is None:
        raise ContractError("ODT office:text is missing")

    parent_by_id = {
        id(child): parent
        for parent in body.iter()
        for child in parent
    }
    tables = list(body.iter(_TAG_TABLE))
    table_index_by_id = {
        id(table): index for index, table in enumerate(tables)
    }

    def table_depth(table: ElementTree.Element) -> int:
        depth = 0
        parent = parent_by_id.get(id(table))
        while parent is not None:
            if parent.tag == _TAG_TABLE:
                depth += 1
            parent = parent_by_id.get(id(parent))
        return depth

    def parent_table_index(table: ElementTree.Element) -> int | None:
        parent = parent_by_id.get(id(table))
        while parent is not None:
            if parent.tag == _TAG_TABLE:
                return table_index_by_id[id(parent)]
            parent = parent_by_id.get(id(parent))
        return None

    def row_kind(row: ElementTree.Element) -> str:
        parent = parent_by_id.get(id(row))
        while parent is not None and parent.tag != _TAG_TABLE:
            if parent.tag == _TAG_HEADER_ROWS:
                return "header"
            parent = parent_by_id.get(id(parent))
        return "body"

    blocks: list[dict[str, Any]] = []
    emitted_element_ids: set[int] = set()

    def emit(
        element: ElementTree.Element,
        *,
        kind: str,
        table_index: int | None = None,
        table_depth_value: int | None = None,
        parent_table_index_value: int | None = None,
        row_index: int | None = None,
        row_kind_value: str | None = None,
        cell_index: int | None = None,
        paragraph_index: int | None = None,
    ) -> None:
        raw_text = _text(element)
        if not raw_text:
            return
        element_id = id(element)
        if element_id in emitted_element_ids:
            raise ContractError("ODT source paragraph was emitted more than once")
        emitted_element_ids.add(element_id)
        locator = {
            "kind": kind,
            "document_order": len(blocks),
            "table_index": table_index,
            "table_depth": table_depth_value,
            "parent_table_index": parent_table_index_value,
            "row_index": row_index,
            "row_kind": row_kind_value,
            "cell_index": cell_index,
            "paragraph_index": paragraph_index,
        }
        locator_key = ":".join(
            "" if value is None else str(value)
            for value in (
                kind,
                table_index,
                table_depth_value,
                parent_table_index_value,
                row_index,
                row_kind_value,
                cell_index,
                paragraph_index,
                len(blocks),
            )
        )
        blocks.append(
            {
                "block_id": stable_id(
                    "nhi-update-odt-block",
                    artifact_sha256,
                    locator_key,
                    sha256_bytes(raw_text.encode("utf-8")),
                ),
                "artifact_sha256": artifact_sha256,
                "locator": locator,
                "raw_text": raw_text,
                "raw_text_sha256": sha256_bytes(raw_text.encode("utf-8")),
            }
        )

    row_index_by_table_and_id = {
        (id(table), id(row)): index
        for table in tables
        for index, row in enumerate(_rows_excluding_nested_tables(table))
    }

    def visit(
        element: ElementTree.Element,
        *,
        table: ElementTree.Element | None = None,
        row: ElementTree.Element | None = None,
        cell: ElementTree.Element | None = None,
        cell_index: int | None = None,
        paragraph_counter: list[int] | None = None,
    ) -> None:
        """Emit paragraphs in actual XML order, including nested tables.

        Bulk-processing an outer table before a nested table preserves
        exact-once counts but moves paragraphs that follow the nested table to
        the wrong side of it. Recursive traversal retains document order while
        carrying the owning table/row/cell locator for each paragraph.
        """

        if element.tag in {_TAG_P, _TAG_H}:
            if table is None or row is None or cell is None:
                emit(element, kind="paragraph")
                return
            if paragraph_counter is None or cell_index is None:
                raise ContractError("ODT table paragraph context is incomplete")
            paragraph_index = paragraph_counter[0]
            paragraph_counter[0] += 1
            emit(
                element,
                kind=(
                    "covered_table_cell"
                    if cell.tag == _TAG_COVERED_CELL
                    else "table_cell"
                ),
                table_index=table_index_by_id[id(table)],
                table_depth_value=table_depth(table),
                parent_table_index_value=parent_table_index(table),
                row_index=row_index_by_table_and_id[(id(table), id(row))],
                row_kind_value=row_kind(row),
                cell_index=cell_index,
                paragraph_index=paragraph_index,
            )
            return

        if element.tag == _TAG_TABLE:
            for child in element:
                visit(child, table=element)
            return

        if element.tag == _TAG_ROW:
            if table is None:
                raise ContractError("ODT table row has no owning table")
            cells = [
                child
                for child in element
                if child.tag in {_TAG_CELL, _TAG_COVERED_CELL}
            ]
            cell_indices = {id(child): index for index, child in enumerate(cells)}
            for child in element:
                if child.tag in {_TAG_CELL, _TAG_COVERED_CELL}:
                    visit(
                        child,
                        table=table,
                        row=element,
                        cell=child,
                        cell_index=cell_indices[id(child)],
                        paragraph_counter=[0],
                    )
                else:
                    visit(child, table=table, row=element)
            return

        for child in element:
            if child.tag == _TAG_TABLE:
                visit(child)
            else:
                visit(
                    child,
                    table=table,
                    row=row,
                    cell=cell,
                    cell_index=cell_index,
                    paragraph_counter=paragraph_counter,
                )

    visit(body)

    for document_order, row in enumerate(blocks):
        row["locator"]["document_order"] = document_order

    nonempty_source_paragraph_ids = {
        id(element)
        for element in body.iter()
        if element.tag in {_TAG_P, _TAG_H} and _text(element)
    }
    if emitted_element_ids != nonempty_source_paragraph_ids:
        raise ContractError(
            "ODT exact-once traversal did not account for every source paragraph"
        )

    rows = list(body.iter(_TAG_ROW))
    cells = [
        element
        for element in body.iter()
        if element.tag in {_TAG_CELL, _TAG_COVERED_CELL}
    ]
    structural_facts = {
        "schema": ODT_STRUCTURAL_FACTS_SCHEMA,
        "artifact_sha256": artifact_sha256,
        "table_count": len(tables),
        "nested_table_count": sum(
            1 for table in tables if table_depth(table) > 0
        ),
        "max_table_depth": max(
            (table_depth(table) for table in tables),
            default=0,
        ),
        "row_count": len(rows),
        "cell_count": len(cells),
        "covered_cell_count": sum(
            1 for cell in cells if cell.tag == _TAG_COVERED_CELL
        ),
        "column_span_cell_count": sum(
            1
            for cell in cells
            if _positive_integer_attribute(cell, _ATTR_COLUMNS_SPANNED) > 1
        ),
        "row_span_cell_count": sum(
            1
            for cell in cells
            if _positive_integer_attribute(cell, _ATTR_ROWS_SPANNED) > 1
        ),
        "repeated_cell_count": sum(
            1
            for cell in cells
            if _positive_integer_attribute(cell, _ATTR_COLUMNS_REPEATED) > 1
        ),
        "repeated_row_count": sum(
            1
            for row in rows
            if _positive_integer_attribute(row, _ATTR_ROWS_REPEATED) > 1
        ),
        "source_paragraph_count": len(nonempty_source_paragraph_ids),
        "emitted_block_count": len(blocks),
        "exact_once_verified": True,
    }
    return {
        "blocks": blocks,
        "structural_facts": structural_facts,
    }


def extract_odt_blocks(payload: bytes, artifact_sha256: str) -> list[dict[str, Any]]:
    """Backward-compatible block-only projection."""

    return inspect_odt_document(payload, artifact_sha256)["blocks"]
