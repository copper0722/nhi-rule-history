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
_TAG_CELL = f"{{{_TABLE}}}table-cell"


def _text(element: ElementTree.Element) -> str:
    return "".join(element.itertext())


def extract_odt_blocks(payload: bytes, artifact_sha256: str) -> list[dict[str, Any]]:
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

    blocks: list[dict[str, Any]] = []

    def emit(
        element: ElementTree.Element,
        *,
        kind: str,
        table_index: int | None = None,
        row_index: int | None = None,
        cell_index: int | None = None,
        paragraph_index: int | None = None,
    ) -> None:
        raw_text = _text(element)
        if not raw_text:
            return
        locator = {
            "kind": kind,
            "document_order": len(blocks),
            "table_index": table_index,
            "row_index": row_index,
            "cell_index": cell_index,
            "paragraph_index": paragraph_index,
        }
        locator_key = ":".join(
            "" if value is None else str(value)
            for value in (
                kind,
                table_index,
                row_index,
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

    table_paragraph_ids = {
        id(descendant)
        for table in body.iter(_TAG_TABLE)
        for descendant in table.iter()
        if descendant.tag in {_TAG_P, _TAG_H}
    }
    top_table_index = 0
    for element in body.iter():
        if element.tag in {_TAG_P, _TAG_H}:
            if id(element) in table_paragraph_ids:
                continue
            emit(element, kind="paragraph")
        elif element.tag == _TAG_TABLE:
            for row_index, row in enumerate(element.findall(_TAG_ROW)):
                for cell_index, cell in enumerate(row.findall(_TAG_CELL)):
                    paragraphs = [
                        descendant
                        for descendant in cell.iter()
                        if descendant.tag in {_TAG_P, _TAG_H}
                    ]
                    for paragraph_index, paragraph in enumerate(paragraphs):
                        emit(
                            paragraph,
                            kind="table_cell",
                            table_index=top_table_index,
                            row_index=row_index,
                            cell_index=cell_index,
                            paragraph_index=paragraph_index,
                        )
            top_table_index += 1

    for document_order, row in enumerate(blocks):
        row["locator"]["document_order"] = document_order
    return blocks
