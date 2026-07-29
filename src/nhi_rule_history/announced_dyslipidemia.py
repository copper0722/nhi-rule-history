"""Load the 2026-07-28 dyslipidemia notice and its complete clause projection.

The official amendment attachment elides the unchanged remainder below the new
Table 2 heading.  The loader therefore preserves two provenance lanes: exact
amendment blocks and byte-exact inherited predecessor blocks.  Their sealed
manifest forms one deterministic complete 2.6.1 version.  The same release also
normalizes NHI reimbursement-product-code links and a version-bound Table 1
LDL-C threshold model.  User-entered facts are never handled here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from nhi_rule_history.clause_history import (
    DIFF_PRESENTATION_VERSION,
    IGNORED_CHANGE_POLICY,
    semantic_diff_presentation,
)
from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.current_publication import semantic_comparison_text
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)
from nhi_rule_history.terminology import (
    ALIAS_ADMISSION_POLICY,
    MATCHER_VERSION,
    OFFSET_CONTRACT,
    scan_block_alias_occurrences,
)
from nhi_rule_history.update.odt import inspect_odt_document


SCHEMA = "nhi_rule_history_announced"
LOADER_VERSION = "nhi-rule-history/announced-dyslipidemia-loader/1.5.0"
EVALUATOR_VERSION = "nhi-rule-history/table1-open-world-dnf/1.1.0"
DOCUMENT_NORMALIZATION_VERSION = (
    "nhi-rule-history/clause-document-normalization/1.1.0"
)
DOCUMENT_ALIGNMENT_VERSION = (
    "nhi-rule-history/clause-document-tree-alignment/1.0.0"
)
EXACT_DIFF_ALGORITHM_VERSION = (
    "python-difflib-sequence-matcher-unicode-scalar-autojunk-false/1.0.0"
)
EXACT_DIFF_TOKENIZER_VERSION = "unicode-scalar/1.0.0"
EXACT_DIFF_TIE_BREAK_VERSION = "python-difflib-leftmost-longest/1.0.0"
EXACT_DIFF_UNICODE_PROFILE = "source-exact-no-normalization/1.0.0"
DIFF_DISPLAY_POLICY_VERSION = (
    "node-type-whitespace-display-classification/1.0.0"
)
COMPOSITION_RULE_VERSION = (
    "nhi-rule-history/2.6.1-amendment-plus-inherited-remainder/1.0.0"
)
NOTICE_URL = "https://www.nhi.gov.tw/ch/cp-20300-7968a-3258-1.html"
NOTICE_REFERENCE = "健保審字第1150671962號"
NOTICE_TITLE = "公告異動降血脂藥品支付價格及修訂其藥品給付規定"
PUBLICATION_DATE = "2026-07-28"
EFFECTIVE_DATE = "2026-09-01"
EXPECTED_ARTIFACT_SHA256 = (
    "207dde0b40e9ed0238b6b40746f2450d98205f6d39d5e167ec2b41c9ec8f9e44"
)
SOURCE_ARTIFACT_FILENAME = "attachment-003.odt"
PREDECESSOR_TEXT_SHA256 = (
    "5c6cbaaae104aaed9427080168c38ff25afc38667063c29eb04981fbdee56e3a"
)
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_announced_decision_v21.sql"
)
RELEASE_GATE_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_announced_release_gate_v22.sql"
)
COMPOSITION_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_announced_composite_v23.sql"
)
VERSION_PROJECTION_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_announced_version_projection_v24.sql"
)
DOCUMENT_COMPONENT_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_clause_components_v25.sql"
)
_UUID_NAMESPACE = uuid.UUID("90f6ded1-5025-4938-9e68-fcdfdc349c1c")
_TABLE2_CODE_RE = __import__("re").compile(r"^[A-Z0-9]{10}$")
_OUTLINE_MARKERS = (
    (
        re.compile(r"^(?P<marker>[一二三四五六七八九十百]+、)\s*"),
        "cjk_ideograph_comma",
        1,
    ),
    (
        re.compile(r"^(?P<marker>\d+[.、])\s*"),
        "arabic_delimited",
        1,
    ),
    (
        re.compile(
            r"^(?P<marker>[（(][一二三四五六七八九十百]+[）)])\s*"
        ),
        "parenthesized_cjk",
        2,
    ),
    (
        re.compile(r"^(?P<marker>[（(]\d+[）)])\s*"),
        "parenthesized_arabic",
        2,
    ),
    (
        re.compile(r"^(?P<marker>[A-Za-z][.、])\s*"),
        "latin_delimited",
        3,
    ),
    (
        re.compile(r"^(?P<marker>[①②③④⑤⑥⑦⑧⑨⑩])\s*"),
        "circled_arabic",
        3,
    ),
)


class AnnouncedDyslipidemiaError(PgLoadError):
    """The official patch or decision model failed a closed invariant."""


@dataclass(frozen=True)
class AnnouncedMaterial:
    run_id: str
    notice_id: str
    patch_id: str
    version_id: str
    model_id: str
    source_release_run_id: str
    normalization_run_id: str
    diff_run_id: str
    clause_work_id: str
    older_expression_id: str
    newer_expression_id: str
    expression_relation_id: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    identity_rows: Mapping[str, tuple[dict[str, Any], ...]]
    normalization_rows: Mapping[str, tuple[dict[str, Any], ...]]
    diff_rows: Mapping[str, tuple[dict[str, Any], ...]]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    normalization_expected_counts: Mapping[str, int]
    normalization_table_fingerprints: Mapping[str, str]
    diff_expected_counts: Mapping[str, int]
    diff_table_fingerprints: Mapping[str, str]
    document_structure_sha256: str
    normalization_receipt: Mapping[str, Any]
    normalization_input_fingerprint: str
    normalization_output_fingerprint: str
    normalization_sealed_fingerprint: str
    diff_input_fingerprint: str
    diff_output_fingerprint: str
    diff_sealed_fingerprint: str
    input_fingerprint: str
    output_fingerprint: str
    sealed_fingerprint: str
    migration_sha256: str
    code_sha256: str


def _stable_uuid(label: str, value: object) -> str:
    material = canonical_json_bytes([label, value]).decode("utf-8")
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _with_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["source_row_sha256"] = row_sha256(out, derived_key="source_row_sha256")
    return out


def _component_role(document_order: int) -> str:
    if document_order == 5:
        return "clause_heading"
    if document_order == 6:
        return "applicability"
    if 7 <= document_order <= 250:
        return "table2_code_set"
    if document_order == 254:
        return "table1_heading"
    if 255 <= document_order <= 293:
        return "table1_matrix"
    if 294 <= document_order <= 321:
        return "risk_definition"
    if 322 <= document_order <= 333:
        return "risk_factor_definition"
    if 334 <= document_order <= 341:
        return "assessment_note"
    if document_order == 342:
        return "secondary_target"
    if document_order == 343:
        return "table2_heading"
    if document_order == 344:
        return "omitted_remainder_marker"
    raise AnnouncedDyslipidemiaError(
        f"unexpected selected source block {document_order}"
    )


def _table_renderer_profile(table_role: str) -> str:
    if table_role == "table2_product_codes":
        return "product_code_directory_v1"
    if table_role in {
        "table1_ldl_thresholds",
        "table2_ldl_thresholds",
        "triglyceride_thresholds",
    }:
        return "threshold_matrix_v1"
    return "simple_reference_v1"


def _outline_text_shape(
    text: str,
    *,
    force_heading: bool = False,
) -> dict[str, Any]:
    if force_heading:
        return {
            "structural_kind": "heading",
            "marker_raw": None,
            "marker_scheme": None,
            "item_ordinal": None,
            "marker_scalar_end": None,
            "marker_utf8_byte_end": None,
            "marker_depth": 0,
            "content_text": text,
            "content_text_sha256": _sha256_text(text),
            "structure_status": "role_derived",
        }
    for pattern, scheme, depth in _OUTLINE_MARKERS:
        match = pattern.match(text)
        if not match:
            continue
        marker = match.group("marker")
        ordinal_match = re.search(r"\d+", marker)
        ordinal = int(ordinal_match.group()) if ordinal_match else None
        content = text[match.end() :]
        if re.search(
            r"(?:\r?\n)\s*(?:"
            r"[一二三四五六七八九十百]+、|"
            r"\d+[.、]|"
            r"[（(](?:[一二三四五六七八九十百]+|\d+)[）)]|"
            r"[A-Za-z][.、]|"
            r"[①②③④⑤⑥⑦⑧⑨⑩]"
            r")\s*",
            content,
        ):
            return {
                "structural_kind": "paragraph",
                "marker_raw": None,
                "marker_scheme": None,
                "item_ordinal": None,
                "marker_scalar_end": None,
                "marker_utf8_byte_end": None,
                "marker_depth": 1,
                "content_text": text,
                "content_text_sha256": _sha256_text(text),
                "structure_status": "unresolved_structure",
            }
        return {
            "structural_kind": "list_item",
            "marker_raw": marker,
            "marker_scheme": scheme,
            "item_ordinal": ordinal,
            "marker_scalar_end": match.end(),
            "marker_utf8_byte_end": len(
                text[: match.end()].encode("utf-8")
            ),
            "marker_depth": depth,
            "content_text": content,
            "content_text_sha256": _sha256_text(content),
            "structure_status": "deterministic_marker",
        }
    return {
        "structural_kind": "paragraph",
        "marker_raw": None,
        "marker_scheme": None,
        "item_ordinal": None,
        "marker_scalar_end": None,
        "marker_utf8_byte_end": None,
        "marker_depth": 1,
        "content_text": text,
        "content_text_sha256": _sha256_text(text),
        "structure_status": "plain_paragraph",
    }


def _table_carry_policy(table_role: str) -> str:
    if table_role in {
        "table2_product_codes",
        "table1_ldl_thresholds",
        "table2_ldl_thresholds",
        "triglyceride_thresholds",
    }:
        return "vertical_missing_from_previous_origin_v1"
    return "none"


def _cell_source_shape(
    blocks: Sequence[Mapping[str, Any]],
) -> tuple[bool, int, int]:
    covered = all(
        str(block.get("source_locator", {}).get("cell_element") or "")
        == "covered-table-cell"
        for block in blocks
    )
    row_span = max(
        int(
            block.get("source_locator", {}).get("number_rows_spanned")
            or 1
        )
        for block in blocks
    )
    column_span = max(
        int(
            block.get("source_locator", {}).get("number_columns_spanned")
            or 1
        )
        for block in blocks
    )
    return covered, row_span, column_span


def _normalized_table_blueprint(
    blocks: Sequence[Mapping[str, Any]],
    *,
    table_index: int,
    table_role: str,
) -> dict[str, Any]:
    direct: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for block in blocks:
        locator = block.get("render_locator") or {}
        coordinate = (
            int(locator["row_index"]),
            int(locator["cell_index"]),
        )
        direct.setdefault(coordinate, []).append(dict(block))
    if not direct:
        raise AnnouncedDyslipidemiaError(
            f"table {table_index} has no source cells"
        )
    for source_blocks in direct.values():
        source_blocks.sort(key=lambda row: int(row["block_order"]))

    row_count = max(row for row, _cell in direct) + 1
    column_count = 0
    for (_row, cell), source_blocks in direct.items():
        _covered, _row_span, column_span = _cell_source_shape(source_blocks)
        column_count = max(column_count, cell + column_span)
    header_row_count = 1
    carry_policy = _table_carry_policy(table_role)
    active_spans: dict[tuple[int, int], tuple[int, int]] = {}
    last_origin: dict[int, tuple[int, int]] = {}
    cells: list[dict[str, Any]] = []

    for row_index in range(row_count):
        for cell_index in range(column_count):
            coordinate = (row_index, cell_index)
            source_blocks = direct.get(coordinate, [])
            source_block_orders = [
                int(block["block_order"]) for block in source_blocks
            ]
            source_text = "\n".join(
                str(block["raw_text"]) for block in source_blocks
            )
            source_is_repeated = any(
                int(
                    block.get("source_locator", {}).get(
                        "col_repeat_attr"
                    )
                    or 1
                )
                > 1
                or int(
                    block.get("source_locator", {}).get(
                        "row_repeat_attr"
                    )
                    or 1
                )
                > 1
                or int(
                    block.get("source_locator", {}).get(
                        "col_repeat_instance"
                    )
                    or 0
                )
                > 0
                or int(
                    block.get("source_locator", {}).get(
                        "row_repeat_instance"
                    )
                    or 0
                )
                > 0
                for block in source_blocks
            )
            origin = active_spans.get(coordinate)
            if source_blocks:
                covered, row_span, column_span = _cell_source_shape(
                    source_blocks
                )
                if covered:
                    if origin is None:
                        origin = last_origin.get(cell_index)
                    if origin is None:
                        raise AnnouncedDyslipidemiaError(
                            "covered source cell has no preceding origin"
                        )
                    physical_state = "explicit_covered"
                    logical_value_state = "covered_from_origin"
                    physical_text = None
                    logical_value_text = None
                    row_span = 1
                    column_span = 1
                else:
                    physical_state = (
                        "source_repeated"
                        if source_is_repeated
                        else (
                            "present_text"
                            if source_text
                            else "present_empty"
                        )
                    )
                    logical_value_state = (
                        "own_source_value" if source_text else "none"
                    )
                    origin = None
                    physical_text = source_text
                    logical_value_text = source_text if source_text else None
                    for covered_row in range(
                        row_index, row_index + row_span
                    ):
                        for covered_cell in range(
                            cell_index, cell_index + column_span
                        ):
                            if (covered_row, covered_cell) != coordinate:
                                active_spans[
                                    (covered_row, covered_cell)
                                ] = coordinate
                    for covered_cell in range(
                        cell_index, cell_index + column_span
                    ):
                        last_origin[covered_cell] = coordinate
            elif origin is not None:
                physical_state = "explicit_covered"
                logical_value_state = "covered_from_origin"
                physical_text = None
                logical_value_text = None
                row_span = 1
                column_span = 1
            elif (
                carry_policy == "vertical_missing_from_previous_origin_v1"
                and cell_index in last_origin
            ):
                physical_state = "physically_omitted"
                logical_value_state = "policy_carried_from_origin"
                origin = last_origin[cell_index]
                physical_text = None
                logical_value_text = None
                row_span = 1
                column_span = 1
            else:
                physical_state = "physically_omitted"
                logical_value_state = "none"
                origin = None
                physical_text = None
                logical_value_text = None
                row_span = 1
                column_span = 1

            source_paragraphs = (
                [
                    {
                        "block_order": int(block["block_order"]),
                        "exact_text": str(block["raw_text"]),
                        "exact_text_sha256": str(
                            block["raw_text_sha256"]
                        ),
                        **_outline_text_shape(str(block["raw_text"])),
                    }
                    for block in source_blocks
                    if str(block["raw_text"])
                ]
                if physical_state
                in {"present_text", "source_repeated"}
                else []
            )

            cells.append(
                {
                    "row_index": row_index,
                    "cell_index": cell_index,
                    "physical_state": physical_state,
                    "logical_value_state": logical_value_state,
                    "cell_role": (
                        "column_header"
                        if row_index < header_row_count
                        else "body"
                    ),
                    "row_span": row_span,
                    "column_span": column_span,
                    "value_origin_row_index": (
                        origin[0] if origin else None
                    ),
                    "value_origin_cell_index": (
                        origin[1] if origin else None
                    ),
                    "physical_text": physical_text,
                    "physical_text_sha256": (
                        _sha256_text(physical_text)
                        if physical_text is not None
                        else None
                    ),
                    "logical_value_text": logical_value_text,
                    "logical_value_sha256": (
                        _sha256_text(logical_value_text)
                        if logical_value_text is not None
                        else None
                    ),
                    "carry_policy_receipt_sha256": None,
                    "source_content_count": len(source_paragraphs),
                    "source_block_orders": source_block_orders,
                    "source_paragraphs": source_paragraphs,
                }
            )

    by_coordinate = {
        (cell["row_index"], cell["cell_index"]): cell for cell in cells
    }
    for cell in cells:
        origin_coordinate = (
            cell["value_origin_row_index"],
            cell["value_origin_cell_index"],
        )
        if cell["logical_value_state"] not in {
            "covered_from_origin",
            "policy_carried_from_origin",
        }:
            continue
        origin_cell = by_coordinate.get(origin_coordinate)
        if (
            origin_cell is None
            or origin_cell["logical_value_state"] != "own_source_value"
        ):
            raise AnnouncedDyslipidemiaError(
                "normalized table cell does not resolve to a source origin"
            )
        cell["logical_value_text"] = origin_cell["logical_value_text"]
        cell["logical_value_sha256"] = origin_cell[
            "logical_value_sha256"
        ]
        if cell["logical_value_state"] == "policy_carried_from_origin":
            cell["carry_policy_receipt_sha256"] = object_fingerprint(
                {
                    "table_role": table_role,
                    "policy_version": carry_policy,
                    "row_index": cell["row_index"],
                    "cell_index": cell["cell_index"],
                    "origin": origin_coordinate,
                    "logical_value_sha256": cell[
                        "logical_value_sha256"
                    ],
                }
            )

    row_records = []
    for row_index in range(row_count):
        row_cells = [
            cell for cell in cells if cell["row_index"] == row_index
        ]
        row_records.append(
            {
                "row_index": row_index,
                "row_role": (
                    "header" if row_index < header_row_count else "body"
                ),
                "row_signature_sha256": object_fingerprint(
                    [
                        {
                            "cell_index": cell["cell_index"],
                            "logical_value_sha256": cell[
                                "logical_value_sha256"
                            ],
                        }
                        for cell in row_cells
                    ]
                ),
                "row_structure_sha256": object_fingerprint(row_cells),
            }
        )
    structure_manifest = {
        "table_index": table_index,
        "table_role": table_role,
        "renderer_profile": _table_renderer_profile(table_role),
        "logical_value_policy_version": carry_policy,
        "row_count": row_count,
        "column_count": column_count,
        "header_row_count": header_row_count,
        "rows": row_records,
        "cells": cells,
    }
    return {
        **structure_manifest,
        "table_structure_sha256": object_fingerprint(structure_manifest),
    }


def _document_structure_blueprint(
    composite_sources: Sequence[Mapping[str, Any]],
    *,
    clause_code: str,
) -> dict[str, Any]:
    ordered_blocks = [
        {"block_order": block_order, **dict(source)}
        for block_order, source in enumerate(composite_sources)
    ]
    components: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(ordered_blocks):
        block = ordered_blocks[cursor]
        if block.get("container") == "table_cell":
            locator = block.get("render_locator") or {}
            table_index = int(locator["table_index"])
            table_role = str(locator["table_role"])
            grouped: list[dict[str, Any]] = []
            while cursor < len(ordered_blocks):
                candidate = ordered_blocks[cursor]
                candidate_locator = candidate.get("render_locator") or {}
                if (
                    candidate.get("container") != "table_cell"
                    or int(candidate_locator.get("table_index", -1))
                    != table_index
                    or str(candidate_locator.get("table_role") or "")
                    != table_role
                ):
                    break
                grouped.append(candidate)
                cursor += 1
            table = _normalized_table_blueprint(
                grouped,
                table_index=table_index,
                table_role=table_role,
            )
            components.append(
                {
                    "component_order": len(components),
                    "component_kind": "table",
                    "structural_kind": "table",
                    "component_role": table_role,
                    "first_block_order": int(grouped[0]["block_order"]),
                    "last_block_order": int(grouped[-1]["block_order"]),
                    "marker_raw": None,
                    "marker_scheme": None,
                    "item_ordinal": None,
                    "marker_scalar_end": None,
                    "marker_utf8_byte_end": None,
                    "marker_depth": 1,
                    "exact_text": "\n\n".join(
                        str(row["raw_text"]) for row in grouped
                    ),
                    "exact_text_sha256": _sha256_text(
                        "\n\n".join(
                            str(row["raw_text"]) for row in grouped
                        )
                    ),
                    "content_text": "",
                    "content_text_sha256": _sha256_text(""),
                    "structure_status": "table_structure",
                    "block_orders": [
                        int(row["block_order"]) for row in grouped
                    ],
                    "table": table,
                }
            )
            continue
        locator = block.get("render_locator") or {}
        component_role = str(
            locator.get("section_role") or "paragraph"
        )
        outline_shape = _outline_text_shape(
            str(block["raw_text"]),
            force_heading=(
                component_role == "clause_heading"
                or component_role.endswith("_heading")
            ),
        )
        components.append(
            {
                "component_order": len(components),
                "component_kind": "flow",
                "component_role": component_role,
                "first_block_order": int(block["block_order"]),
                "last_block_order": int(block["block_order"]),
                "block_orders": [int(block["block_order"])],
                "table": None,
                "exact_text": str(block["raw_text"]),
                "exact_text_sha256": str(block["raw_text_sha256"]),
                **outline_shape,
            }
        )
        cursor += 1

    root_component_order = next(
        (
            int(component["component_order"])
            for component in components
            if component["structural_kind"] == "heading"
        ),
        None,
    )
    if root_component_order is None:
        raise AnnouncedDyslipidemiaError(
            "clause document has no deterministic clause root"
        )
    depth_stack: dict[int, int] = {}
    sibling_counts: dict[int | None, int] = {}
    derived_keys: dict[int, str] = {}
    for component in components:
        component_order = int(component["component_order"])
        depth = int(component["marker_depth"])
        if (
            component["structural_kind"] == "heading"
            and component_order == root_component_order
        ):
            component["hierarchy_depth"] = 0
            component["parent_component_order"] = None
            depth_stack = {0: component_order}
        else:
            if component["structural_kind"] == "heading":
                depth = 1
                component["marker_depth"] = 1
            component["hierarchy_depth"] = depth
            parent_order = depth_stack.get(depth - 1)
            if parent_order is None:
                parent_order = root_component_order
                if depth > 1:
                    component["structure_status"] = "unresolved_structure"
            component["parent_component_order"] = parent_order
            if component["structural_kind"] in {"list_item", "heading"}:
                depth_stack[depth] = component_order
                depth_stack = {
                    stack_depth: stack_order
                    for stack_depth, stack_order in depth_stack.items()
                    if stack_depth <= depth
                }
        structural_kind = component["structural_kind"]
        component["akn_element"] = {
            "heading": (
                "clause"
                if component_order == root_component_order
                else "hcontainer"
            ),
            "list_item": (
                "point"
                if int(component["hierarchy_depth"]) == 1
                else "subparagraph"
            ),
            "paragraph": "paragraph",
            "table": "table",
        }[structural_kind]
        parent_order = component["parent_component_order"]
        component["sibling_ordinal"] = sibling_counts.get(parent_order, 0)
        sibling_counts[parent_order] = component["sibling_ordinal"] + 1
        parent_key = derived_keys.get(parent_order) if parent_order is not None else None
        if component_order == root_component_order:
            derived_key = f"{clause_code}/clause"
        elif structural_kind in {"table", "heading"}:
            derived_key = (
                f"{clause_code}/{structural_kind}/"
                f"{component['component_role']}"
            )
        elif structural_kind == "list_item" and parent_key:
            marker_key = unicodedata.normalize(
                "NFKC", str(component["marker_raw"])
            )
            derived_key = (
                f"{parent_key}/{component['marker_scheme']}:{marker_key}"
            )
        else:
            derived_key = (
                f"{clause_code}/version-local/{component_order}"
            )
        component["derived_work_node_key"] = derived_key
        derived_keys[component_order] = derived_key

    covered_orders = [
        order
        for component in components
        for order in component["block_orders"]
    ]
    if covered_orders != list(range(len(composite_sources))):
        raise AnnouncedDyslipidemiaError(
            "document components do not conserve exact block order"
        )
    manifest = {
        "normalization_version": DOCUMENT_NORMALIZATION_VERSION,
        "source_block_count": len(composite_sources),
        "component_count": len(components),
        "table_count": sum(
            component["component_kind"] == "table"
            for component in components
        ),
        "components": components,
    }
    return {
        **manifest,
        "has_table": manifest["table_count"] > 0,
        "structure_manifest_sha256": object_fingerprint(manifest),
    }


def _legacy_document_projection_rows_v24(
    *,
    run_id: str,
    version_id: str,
    clause_code: str,
    blueprint: Mapping[str, Any],
) -> tuple[
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, int],
    dict[str, str],
]:
    rows: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "composed_clause_component",
            "composed_clause_component_block",
            "composed_clause_table",
            "composed_clause_table_row",
            "composed_clause_table_cell",
            "composed_clause_table_cell_block",
        )
    }
    component_ids = {
        int(component["component_order"]): _stable_uuid(
            "clause-component",
            [
                version_id,
                component["component_order"],
                component["component_kind"],
                component["component_role"],
                component["first_block_order"],
                component["last_block_order"],
            ],
        )
        for component in blueprint["components"]
    }
    for component in blueprint["components"]:
        component_id = component_ids[int(component["component_order"])]
        parent_order = component["parent_component_order"]
        parent_component_id = (
            component_ids[int(parent_order)]
            if parent_order is not None
            else None
        )
        component_manifest = {
            key: value
            for key, value in component.items()
            if key != "table"
        }
        rows["composed_clause_component"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "component_id": component_id,
                    "clause_code": clause_code,
                    "component_order": component["component_order"],
                    "component_kind": component["component_kind"],
                    "component_role": component["component_role"],
                    "akn_element": component["akn_element"],
                    "parent_component_id": parent_component_id,
                    "hierarchy_depth": component["hierarchy_depth"],
                    "marker_raw": component["marker_raw"],
                    "marker_scheme": component["marker_scheme"],
                    "item_ordinal": component["item_ordinal"],
                    "content_text": component["content_text"],
                    "content_text_sha256": component[
                        "content_text_sha256"
                    ],
                    "structure_status": component["structure_status"],
                    "work_node_key": component["work_node_key"],
                    "identity_status": component["identity_status"],
                    "first_block_order": component["first_block_order"],
                    "last_block_order": component["last_block_order"],
                    "normalization_version": DOCUMENT_NORMALIZATION_VERSION,
                    "component_structure_sha256": object_fingerprint(
                        component_manifest
                    ),
                }
            )
        )
        for position, block_order in enumerate(component["block_orders"]):
            rows["composed_clause_component_block"].append(
                _with_hash(
                    {
                        "run_id": run_id,
                        "version_id": version_id,
                        "component_id": component_id,
                        "block_order": block_order,
                        "position_in_component": position,
                    }
                )
            )
        table = component.get("table")
        if not table:
            continue
        table_id = _stable_uuid(
            "clause-table",
            [version_id, component_id, table["table_index"]],
        )
        rows["composed_clause_table"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "component_id": component_id,
                    "table_id": table_id,
                    "table_index": table["table_index"],
                    "table_role": table["table_role"],
                    "renderer_profile": table["renderer_profile"],
                    "carry_policy": table["carry_policy"],
                    "row_count": table["row_count"],
                    "column_count": table["column_count"],
                    "header_row_count": table["header_row_count"],
                    "table_structure_sha256": table[
                        "table_structure_sha256"
                    ],
                }
            )
        )
        for table_row in table["rows"]:
            rows["composed_clause_table_row"].append(
                _with_hash(
                    {
                        "run_id": run_id,
                        "version_id": version_id,
                        "table_id": table_id,
                        "row_index": table_row["row_index"],
                        "row_role": table_row["row_role"],
                        "row_structure_sha256": table_row[
                            "row_structure_sha256"
                        ],
                    }
                )
            )
        for cell in table["cells"]:
            rows["composed_clause_table_cell"].append(
                _with_hash(
                    {
                        "run_id": run_id,
                        "version_id": version_id,
                        "table_id": table_id,
                        "row_index": cell["row_index"],
                        "cell_index": cell["cell_index"],
                        "cell_state": cell["cell_state"],
                        "cell_role": cell["cell_role"],
                        "row_span": cell["row_span"],
                        "column_span": cell["column_span"],
                        "origin_row_index": cell["origin_row_index"],
                        "origin_cell_index": cell["origin_cell_index"],
                        "normalized_text": cell["normalized_text"],
                        "normalized_text_sha256": cell[
                            "normalized_text_sha256"
                        ],
                        "source_paragraph_count": cell[
                            "source_paragraph_count"
                        ],
                    }
                )
            )
            for paragraph_order, paragraph in enumerate(
                cell["source_paragraphs"]
            ):
                rows["composed_clause_table_cell_block"].append(
                    _with_hash(
                        {
                            "run_id": run_id,
                            "version_id": version_id,
                            "table_id": table_id,
                            "row_index": cell["row_index"],
                            "cell_index": cell["cell_index"],
                            "paragraph_order": paragraph_order,
                            "block_order": paragraph["block_order"],
                            "structural_kind": paragraph[
                                "structural_kind"
                            ],
                            "marker_raw": paragraph["marker_raw"],
                            "marker_scheme": paragraph["marker_scheme"],
                            "item_ordinal": paragraph["item_ordinal"],
                            "content_text": paragraph["content_text"],
                            "content_text_sha256": paragraph[
                                "content_text_sha256"
                            ],
                            "structure_status": paragraph[
                                "structure_status"
                            ],
                        }
                    )
                )
    frozen = {name: tuple(value) for name, value in rows.items()}
    expected_counts = {name: len(value) for name, value in frozen.items()}
    table_fingerprints = {
        name: row_set_fingerprint(
            row["source_row_sha256"] for row in value
        )
        for name, value in frozen.items()
    }
    return frozen, expected_counts, table_fingerprints


def _clause_document_node_id(
    expression_id: str,
    component: Mapping[str, Any],
) -> str:
    return _stable_uuid(
        "clause-document-node",
        [
            expression_id,
            component["component_order"],
            component["component_kind"],
            component["component_role"],
            component["first_block_order"],
            component["last_block_order"],
        ],
    )


def _project_expression_document(
    *,
    normalization_run_id: str,
    expression_id: str,
    clause_work_id: str,
    clause_code: str,
    sources: Sequence[Mapping[str, Any]],
    blueprint: Mapping[str, Any],
    rows: dict[str, list[dict[str, Any]]],
    root_node_work_id: str,
) -> dict[int, str]:
    node_ids = {
        int(component["component_order"]): _clause_document_node_id(
            expression_id, component
        )
        for component in blueprint["components"]
    }
    for block_order, source in enumerate(sources):
        raw_text = str(source["raw_text"])
        rows["clause_document_source_block"].append(
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": expression_id,
                    "block_order": block_order,
                    "source_block_id": source["source_block_id"],
                    "source_artifact_sha256": source[
                        "source_artifact_sha256"
                    ],
                    "source_lane": source["origin_lane"],
                    "container": source["container"],
                    "raw_text": raw_text,
                    "raw_text_sha256": source["raw_text_sha256"],
                    "scalar_length": len(raw_text),
                    "utf8_byte_length": len(raw_text.encode("utf-8")),
                    "source_locator": source["source_locator"],
                    "render_locator": source["render_locator"],
                }
            )
        )

    for component in blueprint["components"]:
        component_order = int(component["component_order"])
        node_id = node_ids[component_order]
        parent_order = component["parent_component_order"]
        parent_node_id = (
            node_ids[int(parent_order)]
            if parent_order is not None
            else None
        )
        node_manifest = {
            key: value
            for key, value in component.items()
            if key not in {"table", "block_orders"}
        }
        rows["clause_document_node"].append(
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": expression_id,
                    "node_id": node_id,
                    "parent_node_id": parent_node_id,
                    "tree_preorder": component_order,
                    "sibling_ordinal": component["sibling_ordinal"],
                    "hierarchy_depth": component["hierarchy_depth"],
                    "akn_element": component["akn_element"],
                    "structural_role": component["component_role"],
                    "marker_raw": component["marker_raw"],
                    "marker_scheme": component["marker_scheme"],
                    "item_ordinal": component["item_ordinal"],
                    "marker_scalar_end": component[
                        "marker_scalar_end"
                    ],
                    "marker_utf8_byte_end": component[
                        "marker_utf8_byte_end"
                    ],
                    "exact_text": component["exact_text"],
                    "exact_text_sha256": component[
                        "exact_text_sha256"
                    ],
                    "content_text": component["content_text"],
                    "content_text_sha256": component[
                        "content_text_sha256"
                    ],
                    "structure_status": component["structure_status"],
                    "derived_work_node_key": component[
                        "derived_work_node_key"
                    ],
                    "node_structure_sha256": object_fingerprint(
                        node_manifest
                    ),
                }
            )
        )
        is_root = component["akn_element"] == "clause"
        if is_root:
            identity_resolution_status = "verified"
            identity_basis = "explicit_source_mapping"
            decision_lane = "deterministic_rule"
            node_work_id = root_node_work_id
            evidence_receipt_sha256 = object_fingerprint(
                {
                    "clause_work_id": clause_work_id,
                    "clause_code": clause_code,
                    "expression_id": expression_id,
                    "node_id": node_id,
                    "basis": "one deterministic clause root",
                }
            )
        elif component["structure_status"] == "unresolved_structure":
            identity_resolution_status = "conflicted"
            identity_basis = "none"
            decision_lane = "not_assigned"
            node_work_id = None
            evidence_receipt_sha256 = None
        elif component["structural_kind"] in {"table", "heading"}:
            identity_resolution_status = "candidate"
            identity_basis = "structural_role"
            decision_lane = "deterministic_rule"
            node_work_id = None
            evidence_receipt_sha256 = None
        elif component["structural_kind"] == "list_item":
            identity_resolution_status = "candidate"
            identity_basis = "marker_path"
            decision_lane = "deterministic_rule"
            node_work_id = None
            evidence_receipt_sha256 = None
        else:
            identity_resolution_status = "version_local"
            identity_basis = "none"
            decision_lane = "not_assigned"
            node_work_id = None
            evidence_receipt_sha256 = None
        rows["clause_document_node_identity"].append(
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": expression_id,
                    "node_id": node_id,
                    "node_work_id": node_work_id,
                    "identity_resolution_status": (
                        identity_resolution_status
                    ),
                    "identity_basis": identity_basis,
                    "decision_lane": decision_lane,
                    "evidence_receipt_sha256": (
                        evidence_receipt_sha256
                    ),
                }
            )
        )

        table = component.get("table")
        if not table:
            for span_order, block_order in enumerate(
                component["block_orders"]
            ):
                raw_text = str(sources[int(block_order)]["raw_text"])
                if not raw_text:
                    continue
                rows["clause_document_source_span"].append(
                    _with_hash(
                        {
                            "normalization_run_id": normalization_run_id,
                            "expression_id": expression_id,
                            "span_id": _stable_uuid(
                                "clause-document-source-span",
                                [
                                    expression_id,
                                    block_order,
                                    node_id,
                                    span_order,
                                ],
                            ),
                            "block_order": block_order,
                            "span_order_in_block": 0,
                            "owner_kind": "expression_node",
                            "node_id": node_id,
                            "table_id": None,
                            "row_index": None,
                            "cell_index": None,
                            "content_order": None,
                            "scalar_start": 0,
                            "scalar_end": len(raw_text),
                            "utf8_byte_start": 0,
                            "utf8_byte_end": len(
                                raw_text.encode("utf-8")
                            ),
                            "mapping_role": "primary_leaf",
                            "exact_span_text": raw_text,
                            "exact_span_text_sha256": _sha256_text(
                                raw_text
                            ),
                        }
                    )
                )
            continue

        table_id = _stable_uuid(
            "clause-document-table",
            [expression_id, node_id, table["table_index"]],
        )
        rows["clause_document_table"].append(
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": expression_id,
                    "node_id": node_id,
                    "table_id": table_id,
                    "table_index": table["table_index"],
                    "table_role": table["table_role"],
                    "renderer_profile": table["renderer_profile"],
                    "logical_value_policy_version": table[
                        "logical_value_policy_version"
                    ],
                    "row_count": table["row_count"],
                    "column_count": table["column_count"],
                    "header_row_count": table["header_row_count"],
                    "table_structure_sha256": table[
                        "table_structure_sha256"
                    ],
                }
            )
        )
        for table_row in table["rows"]:
            rows["clause_document_table_row"].append(
                _with_hash(
                    {
                        "normalization_run_id": normalization_run_id,
                        "expression_id": expression_id,
                        "table_id": table_id,
                        "row_index": table_row["row_index"],
                        "row_role": table_row["row_role"],
                        "row_signature_sha256": table_row[
                            "row_signature_sha256"
                        ],
                        "row_structure_sha256": table_row[
                            "row_structure_sha256"
                        ],
                    }
                )
            )
        for cell in table["cells"]:
            rows["clause_document_table_cell"].append(
                _with_hash(
                    {
                        "normalization_run_id": normalization_run_id,
                        "expression_id": expression_id,
                        "table_id": table_id,
                        "row_index": cell["row_index"],
                        "cell_index": cell["cell_index"],
                        "physical_state": cell["physical_state"],
                        "logical_value_state": cell[
                            "logical_value_state"
                        ],
                        "cell_role": cell["cell_role"],
                        "row_span": cell["row_span"],
                        "column_span": cell["column_span"],
                        "value_origin_row_index": cell[
                            "value_origin_row_index"
                        ],
                        "value_origin_cell_index": cell[
                            "value_origin_cell_index"
                        ],
                        "physical_text": cell["physical_text"],
                        "physical_text_sha256": cell[
                            "physical_text_sha256"
                        ],
                        "logical_value_text": cell[
                            "logical_value_text"
                        ],
                        "logical_value_sha256": cell[
                            "logical_value_sha256"
                        ],
                        "carry_policy_receipt_sha256": cell[
                            "carry_policy_receipt_sha256"
                        ],
                        "source_content_count": cell[
                            "source_content_count"
                        ],
                    }
                )
            )
            for content_order, content in enumerate(
                cell["source_paragraphs"]
            ):
                rows["clause_document_table_cell_content"].append(
                    _with_hash(
                        {
                            "normalization_run_id": normalization_run_id,
                            "expression_id": expression_id,
                            "table_id": table_id,
                            "row_index": cell["row_index"],
                            "cell_index": cell["cell_index"],
                            "content_order": content_order,
                            "structural_kind": content[
                                "structural_kind"
                            ],
                            "marker_raw": content["marker_raw"],
                            "marker_scheme": content["marker_scheme"],
                            "item_ordinal": content["item_ordinal"],
                            "exact_text": content["exact_text"],
                            "exact_text_sha256": content[
                                "exact_text_sha256"
                            ],
                            "content_text": content["content_text"],
                            "content_text_sha256": content[
                                "content_text_sha256"
                            ],
                            "structure_status": content[
                                "structure_status"
                            ],
                        }
                    )
                )
                raw_text = content["exact_text"]
                rows["clause_document_source_span"].append(
                    _with_hash(
                        {
                            "normalization_run_id": normalization_run_id,
                            "expression_id": expression_id,
                            "span_id": _stable_uuid(
                                "clause-document-cell-source-span",
                                [
                                    expression_id,
                                    table_id,
                                    cell["row_index"],
                                    cell["cell_index"],
                                    content_order,
                                    content["block_order"],
                                ],
                            ),
                            "block_order": content["block_order"],
                            "span_order_in_block": 0,
                            "owner_kind": "table_cell_content",
                            "node_id": None,
                            "table_id": table_id,
                            "row_index": cell["row_index"],
                            "cell_index": cell["cell_index"],
                            "content_order": content_order,
                            "scalar_start": 0,
                            "scalar_end": len(raw_text),
                            "utf8_byte_start": 0,
                            "utf8_byte_end": len(
                                raw_text.encode("utf-8")
                            ),
                            "mapping_role": "primary_leaf",
                            "exact_span_text": raw_text,
                            "exact_span_text_sha256": content[
                                "exact_text_sha256"
                            ],
                        }
                    )
                )
    return node_ids


def _normalization_projection_rows(
    *,
    normalization_run_id: str,
    source_release_run_id: str,
    source_version_id: str,
    clause_work_id: str,
    older_expression_id: str,
    newer_expression_id: str,
    relation_id: str,
    predecessor: Mapping[str, Any],
    old_sources: Sequence[Mapping[str, Any]],
    new_sources: Sequence[Mapping[str, Any]],
    old_blueprint: Mapping[str, Any],
    new_blueprint: Mapping[str, Any],
    composition_manifest_sha256: str,
    notice_id: str,
) -> tuple[
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, int],
    dict[str, str],
    dict[str, Any],
    dict[str, dict[int, str]],
]:
    clause_work_row = _with_hash(
        {
            "clause_work_id": clause_work_id,
            "canonical_code": "2.6.1",
            "authority": "taiwan_nhi",
            "identity_basis": "official_designation",
            "identity_receipt_sha256": object_fingerprint(
                {
                    "clause_code": "2.6.1",
                    "predecessor_run_id": predecessor["run_id"],
                    "notice_id": notice_id,
                }
            ),
        }
    )
    root_node_work_id = _stable_uuid(
        "clause-document-root-work", clause_work_id
    )
    root_work_row = _with_hash(
        {
            "node_work_id": root_node_work_id,
            "clause_work_id": clause_work_id,
            "work_role": "clause_root",
            "creation_basis": "clause_root",
            "creation_receipt_sha256": object_fingerprint(
                {
                    "clause_work_id": clause_work_id,
                    "work_role": "clause_root",
                }
            ),
        }
    )
    identity_rows = {
        "clause_document_work": (clause_work_row,),
        "clause_document_node_work": (root_work_row,),
    }
    rows: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "clause_document_expression",
            "clause_document_expression_relation",
            "clause_document_source_block",
            "clause_document_node",
            "clause_document_node_identity",
            "clause_document_table",
            "clause_document_table_row",
            "clause_document_table_cell",
            "clause_document_table_cell_content",
            "clause_document_source_span",
        )
    }
    old_exact_text = "\n\n".join(
        str(row["raw_text"]) for row in old_sources
    )
    new_exact_text = "\n\n".join(
        str(row["raw_text"]) for row in new_sources
    )
    rows["clause_document_expression"].extend(
        (
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": older_expression_id,
                    "clause_work_id": clause_work_id,
                    "source_lane": "current_publication",
                    "source_run_id": predecessor["run_id"],
                    "source_version_id": None,
                    "effective_from": None,
                    "expression_completeness": "source_complete",
                    "reader_state": "current_effective_complete",
                    "exact_text": old_exact_text,
                    "exact_text_sha256": _sha256_text(old_exact_text),
                    "composition_manifest_sha256": None,
                    "completeness_receipt_sha256": object_fingerprint(
                        {
                            "source_lane": "current_publication",
                            "source_run_id": predecessor["run_id"],
                            "source_artifact_sha256": predecessor[
                                "source_artifact_sha256"
                            ],
                            "source_text_sha256": predecessor[
                                "raw_text_sha256"
                            ],
                        }
                    ),
                }
            ),
            _with_hash(
                {
                    "normalization_run_id": normalization_run_id,
                    "expression_id": newer_expression_id,
                    "clause_work_id": clause_work_id,
                    "source_lane": "announced_composite",
                    "source_run_id": source_release_run_id,
                    "source_version_id": source_version_id,
                    "effective_from": EFFECTIVE_DATE,
                    "expression_completeness": "verified_composite",
                    "reader_state": "future_announced_complete",
                    "exact_text": new_exact_text,
                    "exact_text_sha256": _sha256_text(new_exact_text),
                    "composition_manifest_sha256": (
                        composition_manifest_sha256
                    ),
                    "completeness_receipt_sha256": object_fingerprint(
                        {
                            "source_lane": "announced_composite",
                            "source_release_run_id": source_release_run_id,
                            "source_version_id": source_version_id,
                            "composition_manifest_sha256": (
                                composition_manifest_sha256
                            ),
                            "composition_rule_version": (
                                COMPOSITION_RULE_VERSION
                            ),
                        }
                    ),
                }
            ),
        )
    )
    evidence_receipt = {
        "notice_id": notice_id,
        "notice_reference": NOTICE_REFERENCE,
        "notice_effect_clause_code": "2.6.1",
        "predecessor_publication_run_id": predecessor["run_id"],
        "predecessor_text_sha256": predecessor["raw_text_sha256"],
        "newer_expression_id": newer_expression_id,
        "basis": "official amendment explicitly modifies current 2.6.1",
    }
    rows["clause_document_expression_relation"].append(
        _with_hash(
            {
                "normalization_run_id": normalization_run_id,
                "relation_id": relation_id,
                "clause_work_id": clause_work_id,
                "older_expression_id": older_expression_id,
                "newer_expression_id": newer_expression_id,
                "relation_status": "direct_predecessor_verified",
                "relation_basis": (
                    "official_amendment_to_current_effective_expression"
                ),
                "decision_lane": "deterministic_rule",
                "evidence_receipt": evidence_receipt,
                "evidence_receipt_sha256": _sha256_text(
                    json_text(evidence_receipt)
                ),
            }
        )
    )
    node_ids = {
        "older": _project_expression_document(
            normalization_run_id=normalization_run_id,
            expression_id=older_expression_id,
            clause_work_id=clause_work_id,
            clause_code="2.6.1",
            sources=old_sources,
            blueprint=old_blueprint,
            rows=rows,
            root_node_work_id=root_node_work_id,
        ),
        "newer": _project_expression_document(
            normalization_run_id=normalization_run_id,
            expression_id=newer_expression_id,
            clause_work_id=clause_work_id,
            clause_code="2.6.1",
            sources=new_sources,
            blueprint=new_blueprint,
            rows=rows,
            root_node_work_id=root_node_work_id,
        ),
    }
    frozen = {name: tuple(value) for name, value in rows.items()}
    counts = {name: len(value) for name, value in frozen.items()}
    fingerprints = {
        name: row_set_fingerprint(
            row["source_row_sha256"] for row in value
        )
        for name, value in frozen.items()
    }
    structure_manifest_sha256 = object_fingerprint(
        {"older": old_blueprint, "newer": new_blueprint}
    )
    source_reconstruction_sha256 = object_fingerprint(
        {
            older_expression_id: _sha256_text(old_exact_text),
            newer_expression_id: _sha256_text(new_exact_text),
        }
    )
    output_fingerprint = object_fingerprint(
        {
            "counts": counts,
            "table_fingerprints": fingerprints,
            "source_reconstruction_sha256": (
                source_reconstruction_sha256
            ),
            "structure_manifest_sha256": structure_manifest_sha256,
        }
    )
    receipt = _with_hash(
        {
            "normalization_run_id": normalization_run_id,
            "source_expression_count": 2,
            "expected_counts": counts,
            "table_fingerprints": fingerprints,
            "source_reconstruction_sha256": (
                source_reconstruction_sha256
            ),
            "structure_manifest_sha256": structure_manifest_sha256,
            "output_fingerprint": output_fingerprint,
        }
    )
    return (
        identity_rows,
        frozen,
        counts,
        fingerprints,
        receipt,
        node_ids,
    )


def _utf8_prefix_length(text: str, scalar_offset: int) -> int:
    return len(text[:scalar_offset].encode("utf-8"))


def _exact_inline_diff_segments(
    old_text: str,
    new_text: str,
    *,
    node_kind: str,
) -> list[dict[str, Any]]:
    matcher = SequenceMatcher(None, list(old_text), list(new_text), autojunk=False)
    whitespace_only = (
        old_text != new_text
        and re.sub(r"\s+", " ", old_text).strip()
        == re.sub(r"\s+", " ", new_text).strip()
        and node_kind in {"paragraph", "hcontainer"}
    )
    segments: list[dict[str, Any]] = []
    for opcode, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        kind = {
            "equal": "unchanged",
            "delete": "deleted",
            "insert": "inserted",
            "replace": "replaced",
        }[opcode]
        old_segment = old_text[old_start:old_end] if opcode != "insert" else None
        new_segment = new_text[new_start:new_end] if opcode != "delete" else None
        segments.append(
            {
                "segment_order": len(segments),
                "segment_kind": kind,
                "old_text": old_segment,
                "new_text": new_segment,
                "old_scalar_start": old_start if old_segment is not None else None,
                "old_scalar_end": old_end if old_segment is not None else None,
                "new_scalar_start": new_start if new_segment is not None else None,
                "new_scalar_end": new_end if new_segment is not None else None,
                "old_utf8_byte_start": (
                    _utf8_prefix_length(old_text, old_start)
                    if old_segment is not None
                    else None
                ),
                "old_utf8_byte_end": (
                    _utf8_prefix_length(old_text, old_end)
                    if old_segment is not None
                    else None
                ),
                "new_utf8_byte_start": (
                    _utf8_prefix_length(new_text, new_start)
                    if new_segment is not None
                    else None
                ),
                "new_utf8_byte_end": (
                    _utf8_prefix_length(new_text, new_end)
                    if new_segment is not None
                    else None
                ),
                "display_state": (
                    "deemphasized_formatting"
                    if whitespace_only and kind != "unchanged"
                    else "normal"
                ),
                "display_reason": (
                    "paragraph_whitespace_only"
                    if whitespace_only and kind != "unchanged"
                    else None
                ),
            }
        )
    return segments


def _table_row_alignment(
    old_table: Mapping[str, Any],
    new_table: Mapping[str, Any],
) -> dict[str, Any]:
    old_by_signature: dict[str, list[int]] = {}
    new_by_signature: dict[str, list[int]] = {}
    for row in old_table["rows"]:
        old_by_signature.setdefault(
            str(row["row_signature_sha256"]), []
        ).append(int(row["row_index"]))
    for row in new_table["rows"]:
        new_by_signature.setdefault(
            str(row["row_signature_sha256"]), []
        ).append(int(row["row_index"]))
    pairs: list[dict[str, Any]] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    for signature in sorted(set(old_by_signature) & set(new_by_signature)):
        old_indexes = old_by_signature[signature]
        new_indexes = new_by_signature[signature]
        if len(old_indexes) == 1 and len(new_indexes) == 1:
            used_old.add(old_indexes[0])
            used_new.add(new_indexes[0])
            pairs.append(
                {
                    "old_row_index": old_indexes[0],
                    "new_row_index": new_indexes[0],
                    "alignment_status": "exact_unique_signature",
                    "row_signature_sha256": signature,
                }
            )
    return {
        "algorithm": "unique-row-signature-then-unresolved/1.0.0",
        "pairs": sorted(
            pairs,
            key=lambda row: (
                row["old_row_index"], row["new_row_index"]
            ),
        ),
        "old_unresolved_rows": sorted(
            set(range(int(old_table["row_count"]))) - used_old
        ),
        "new_unresolved_rows": sorted(
            set(range(int(new_table["row_count"]))) - used_new
        ),
    }


def _diff_projection_rows(
    *,
    diff_run_id: str,
    older_expression_id: str,
    newer_expression_id: str,
    relation_status: str,
    old_blueprint: Mapping[str, Any],
    new_blueprint: Mapping[str, Any],
    old_node_ids: Mapping[int, str],
    new_node_ids: Mapping[int, str],
) -> tuple[
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, int],
    dict[str, str],
]:
    rows: dict[str, list[dict[str, Any]]] = {
        "clause_document_node_lineage": [],
        "clause_document_diff_hunk": [],
        "clause_document_inline_diff_segment": [],
    }
    old_components = {
        int(row["component_order"]): row
        for row in old_blueprint["components"]
    }
    new_components = {
        int(row["component_order"]): row
        for row in new_blueprint["components"]
    }
    old_unmatched = set(old_components)
    new_unmatched = set(new_components)
    aligned: list[tuple[int, int, str, str]] = []

    old_root = next(
        order
        for order, component in old_components.items()
        if component["akn_element"] == "clause"
    )
    new_root = next(
        order
        for order, component in new_components.items()
        if component["akn_element"] == "clause"
    )
    aligned.append(
        (
            old_root,
            new_root,
            "verified_work_identity",
            "shared verified clause-root Work identity",
        )
    )
    old_unmatched.remove(old_root)
    new_unmatched.remove(new_root)

    def unique_match(
        key_builder: Callable[[Mapping[str, Any]], object],
        status: str,
        basis: str,
    ) -> None:
        old_index: dict[object, list[int]] = {}
        new_index: dict[object, list[int]] = {}
        for order in old_unmatched:
            old_index.setdefault(
                key_builder(old_components[order]), []
            ).append(order)
        for order in new_unmatched:
            new_index.setdefault(
                key_builder(new_components[order]), []
            ).append(order)
        for key in sorted(
            set(old_index) & set(new_index), key=lambda item: repr(item)
        ):
            if len(old_index[key]) != 1 or len(new_index[key]) != 1:
                continue
            old_order = old_index[key][0]
            new_order = new_index[key][0]
            aligned.append((old_order, new_order, status, basis))
            old_unmatched.remove(old_order)
            new_unmatched.remove(new_order)

    unique_match(
        lambda row: (
            row["akn_element"],
            row["component_role"],
            row["exact_text_sha256"],
        ),
        "exact_unique_signature",
        "unique exact node signature within both expressions",
    )
    unique_match(
        lambda row: (
            row["akn_element"],
            row["component_role"],
            row["marker_scheme"],
            row["marker_raw"],
        ),
        "bounded_deterministic",
        "unique structural role and marker within both expressions",
    )

    comparison_label = (
        "與上一版本差異"
        if relation_status == "direct_predecessor_verified"
        else "與舊版本差異"
    )

    def append_hunk(
        *,
        old_order: int | None,
        new_order: int | None,
        alignment_status: str,
        old_text_override: str | None = None,
        new_text_override: str | None = None,
    ) -> None:
        old_component = (
            old_components[old_order] if old_order is not None else None
        )
        new_component = (
            new_components[new_order] if new_order is not None else None
        )
        old_text = (
            old_text_override
            if old_text_override is not None
            else (
                str(old_component["exact_text"])
                if old_component is not None
                else ""
            )
        )
        new_text = (
            new_text_override
            if new_text_override is not None
            else (
                str(new_component["exact_text"])
                if new_component is not None
                else ""
            )
        )
        if old_component is not None and new_component is not None:
            if old_text == new_text:
                return
            change_kind = (
                "structure_changed"
                if (
                    old_component["structural_kind"] == "table"
                    or new_component["structural_kind"] == "table"
                )
                else "replaced"
            )
            display_classification = "本版改寫"
        elif old_component is not None:
            change_kind = "removed"
            display_classification = "本版刪除"
        else:
            change_kind = "added"
            display_classification = "本版新增"
        if alignment_status == "alignment_unresolved":
            display_classification = "對齊未解"
        node_kind = str(
            (
                new_component["akn_element"]
                if new_component is not None
                else old_component["akn_element"]
            )
        )
        segments = _exact_inline_diff_segments(
            old_text, new_text, node_kind=node_kind
        )
        material_change_kinds = {
            str(segment["segment_kind"])
            for segment in segments
            if segment["segment_kind"] != "unchanged"
            and segment["display_state"] != "deemphasized_formatting"
        }
        if (
            old_component is not None
            and new_component is not None
            and material_change_kinds == {"inserted"}
        ):
            display_classification = "本版新增"
        elif (
            old_component is not None
            and new_component is not None
            and material_change_kinds == {"deleted"}
        ):
            display_classification = "本版刪除"
        elif segments and all(
            segment["display_state"] == "deemphasized_formatting"
            or segment["segment_kind"] == "unchanged"
            for segment in segments
        ):
            display_classification = "排版差異"
        hunk_order = len(rows["clause_document_diff_hunk"])
        hunk_id = _stable_uuid(
            "clause-document-diff-hunk",
            [
                diff_run_id,
                hunk_order,
                old_node_ids.get(old_order) if old_order is not None else None,
                new_node_ids.get(new_order) if new_order is not None else None,
                old_text,
                new_text,
            ],
        )
        table_alignment = None
        if (
            old_component is not None
            and new_component is not None
            and old_component.get("table")
            and new_component.get("table")
        ):
            table_alignment = _table_row_alignment(
                old_component["table"], new_component["table"]
            )
        rows["clause_document_diff_hunk"].append(
            _with_hash(
                {
                    "diff_run_id": diff_run_id,
                    "hunk_id": hunk_id,
                    "hunk_order": hunk_order,
                    "older_expression_id": older_expression_id,
                    "newer_expression_id": newer_expression_id,
                    "older_node_id": (
                        old_node_ids.get(old_order)
                        if old_order is not None
                        else None
                    ),
                    "newer_node_id": (
                        new_node_ids.get(new_order)
                        if new_order is not None
                        else None
                    ),
                    "alignment_status": alignment_status,
                    "exact_change_kind": change_kind,
                    "display_classification": display_classification,
                    "comparison_label": comparison_label,
                    "old_exact_text": (
                        old_text if old_component is not None else None
                    ),
                    "new_exact_text": (
                        new_text if new_component is not None else None
                    ),
                    "old_exact_text_sha256": (
                        _sha256_text(old_text)
                        if old_component is not None
                        else None
                    ),
                    "new_exact_text_sha256": (
                        _sha256_text(new_text)
                        if new_component is not None
                        else None
                    ),
                    "table_alignment": table_alignment,
                    "suppressed_display_segment_count": sum(
                        segment["display_state"]
                        == "deemphasized_formatting"
                        for segment in segments
                    ),
                }
            )
        )
        for segment in segments:
            rows["clause_document_inline_diff_segment"].append(
                _with_hash(
                    {
                        "diff_run_id": diff_run_id,
                        "hunk_id": hunk_id,
                        **segment,
                    }
                )
            )

    for old_order, new_order, status, basis in sorted(aligned):
        lineage_id = _stable_uuid(
            "clause-document-node-lineage",
            [
                diff_run_id,
                old_node_ids[old_order],
                new_node_ids[new_order],
                status,
            ],
        )
        rows["clause_document_node_lineage"].append(
            _with_hash(
                {
                    "diff_run_id": diff_run_id,
                    "lineage_id": lineage_id,
                    "older_expression_id": older_expression_id,
                    "older_node_id": old_node_ids[old_order],
                    "newer_expression_id": newer_expression_id,
                    "newer_node_id": new_node_ids[new_order],
                    "lineage_kind": "continues_as",
                    "alignment_status": status,
                    "alignment_basis": basis,
                }
            )
        )
        if old_order == old_root and new_order == new_root:
            append_hunk(
                old_order=old_order,
                new_order=new_order,
                alignment_status=status,
                old_text_override="\n\n".join(
                    str(component["exact_text"])
                    for component in old_blueprint["components"]
                ),
                new_text_override="\n\n".join(
                    str(component["exact_text"])
                    for component in new_blueprint["components"]
                ),
            )
    for old_order in sorted(old_unmatched):
        rows["clause_document_node_lineage"].append(
            _with_hash(
                {
                    "diff_run_id": diff_run_id,
                    "lineage_id": _stable_uuid(
                        "clause-document-node-lineage",
                        [diff_run_id, old_node_ids[old_order], None],
                    ),
                    "older_expression_id": older_expression_id,
                    "older_node_id": old_node_ids[old_order],
                    "newer_expression_id": newer_expression_id,
                    "newer_node_id": None,
                    "lineage_kind": "old_only",
                    "alignment_status": "alignment_unresolved",
                    "alignment_basis": (
                        "no admitted deterministic counterpart"
                    ),
                }
            )
        )
    for new_order in sorted(new_unmatched):
        rows["clause_document_node_lineage"].append(
            _with_hash(
                {
                    "diff_run_id": diff_run_id,
                    "lineage_id": _stable_uuid(
                        "clause-document-node-lineage",
                        [diff_run_id, None, new_node_ids[new_order]],
                    ),
                    "older_expression_id": older_expression_id,
                    "older_node_id": None,
                    "newer_expression_id": newer_expression_id,
                    "newer_node_id": new_node_ids[new_order],
                    "lineage_kind": "new_only",
                    "alignment_status": "alignment_unresolved",
                    "alignment_basis": (
                        "no admitted deterministic counterpart"
                    ),
                }
            )
        )
    frozen = {name: tuple(value) for name, value in rows.items()}
    counts = {name: len(value) for name, value in frozen.items()}
    fingerprints = {
        name: row_set_fingerprint(
            row["source_row_sha256"] for row in value
        )
        for name, value in frozen.items()
    }
    return frozen, counts, fingerprints


def _selected_source_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_order = {int(row["locator"]["document_order"]): dict(row) for row in blocks}
    selected_orders = [5, 6, *range(7, 251), *range(254, 345)]
    if len(by_order) != len(blocks):
        raise AnnouncedDyslipidemiaError("ODT document order is duplicated")
    try:
        selected = [by_order[order] for order in selected_orders]
    except KeyError as exc:
        raise AnnouncedDyslipidemiaError(
            "official ODT is missing an expected 2.6.1 source block"
        ) from exc
    if selected[-1]["raw_text"].strip() != "(以下略)":
        raise AnnouncedDyslipidemiaError(
            "official ODT omitted-remainder marker changed"
        )
    return selected


def _extract_table2_products(
    blocks: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: dict[int, dict[int, Mapping[str, Any]]] = {}
    for block in blocks:
        locator = block["locator"]
        if locator.get("table_index") != 1:
            continue
        rows.setdefault(int(locator["row_index"]), {})[
            int(locator["cell_index"])
        ] = block
    products: list[dict[str, Any]] = []
    code_doc_orders: dict[str, int] = {}
    ingredient = ""
    for row_index in sorted(rows):
        if row_index == 0:
            continue
        cells = rows[row_index]
        if 0 in cells:
            ingredient = str(cells[0]["raw_text"]).strip()
        code = str(cells.get(1, {}).get("raw_text", "")).strip().upper()
        name = str(cells.get(2, {}).get("raw_text", "")).strip()
        if not _TABLE2_CODE_RE.fullmatch(code) or not name:
            raise AnnouncedDyslipidemiaError(
                f"invalid Table-2 product row {row_index}"
            )
        if code in code_doc_orders:
            raise AnnouncedDyslipidemiaError("Table-2 code is duplicated")
        code_doc_orders[code] = int(cells[1]["locator"]["document_order"])
        products.append(
            {
                "nhi_code": code,
                "product_name": name,
                "ingredient_name": ingredient or None,
                "atc_code": None,
            }
        )
    if len(products) != 116:
        raise AnnouncedDyslipidemiaError(
            f"expected 116 Table-2 codes, found {len(products)}"
        )
    return products, code_doc_orders


def _inputs() -> list[dict[str, Any]]:
    # source_doc_order refers to an exact amendment component and is translated
    # to component_order after the source patch is materialized.
    return [
        ("product_code", "健保藥品代碼", "請選擇完整 10 碼健保代碼。", "product_code", None, None, None, "藥品", 0, 8),
        ("ldl_c_mg_dl", "LDL-C", "輸入本次判讀使用的 LDL-C 數值。", "number", "mg/dL", 0, 1000, "檢驗值", 10, 262),
        ("coronary_artery_disease", "冠狀動脈疾病", "", "tri_state", None, None, None, "極高風險", 20, 296),
        ("mi_within_one_year", "一年內曾經歷心肌梗塞", "", "tri_state", None, None, None, "極高風險", 21, 297),
        ("mi_history_count", "心肌梗塞病史次數", "", "number", "次", 0, 99, "極高風險", 22, 298),
        ("multivessel_coronary_obstruction", "多支冠狀動脈阻塞", "", "tri_state", None, None, None, "極高風險", 23, 299),
        ("acute_coronary_syndrome", "急性冠心症病史", "", "tri_state", None, None, None, "極高／非常高風險", 24, 307),
        ("diabetes", "糖尿病", "", "tri_state", None, None, None, "高風險", 25, 316),
        ("peripheral_artery_disease", "周邊動脈疾病", "", "tri_state", None, None, None, "極高風險", 26, 302),
        ("carotid_stenosis", "頸動脈狹窄", "", "tri_state", None, None, None, "極高風險", 27, 304),
        ("revascularization", "曾接受血管再通術", "含心導管介入治療或外科冠狀動脈繞道手術。", "tri_state", None, None, None, "非常高風險", 30, 308),
        ("ischemic_stroke_tia_atherosclerosis", "缺血性中風／TIA 合併動脈硬化相關疾病或病史", "", "tri_state", None, None, None, "非常高風險", 31, 309),
        ("symptomatic_or_treated_pad", "周邊動脈疾病且曾再通、有缺血症狀或截肢", "", "tri_state", None, None, None, "非常高風險", 32, 310),
        ("plaque_stenosis_percent", "影像顯示斑塊直徑狹窄率", "", "number", "%", 0, 100, "非常高風險", 33, 311),
        ("predialysis_ckd", "透析治療前慢性腎臟病", "", "tri_state", None, None, None, "高風險", 40, 317),
        ("uacr_mg_g", "UACR", "", "number", "mg/g", 0, 100000, "高風險", 41, 317),
        ("egfr_ml_min_1_73m2", "eGFR", "", "number", "mL/min/1.73m²", 0, 300, "高風險", 42, 317),
        ("ckd_duration_months", "上述腎功能狀況持續時間", "", "number", "月", 0, 1200, "高風險", 43, 317),
        ("cac_score", "冠狀動脈鈣化分數（CAC）", "", "number", "分", 0, 10000, "高風險", 44, 319),
        ("risk_hypertension", "高血壓", "", "tri_state", None, None, None, "心血管風險因子", 50, 323),
        ("risk_age_threshold", "男性≥45歲或女性≥55歲", "", "tri_state", None, None, None, "心血管風險因子", 51, 324),
        ("risk_family_history", "早發性冠心病家族史", "男性≤55歲、女性≤65歲。", "tri_state", None, None, None, "心血管風險因子", 52, 325),
        ("risk_low_hdl", "HDL-C 偏低", "男性<40 mg/dL、女性<50 mg/dL。", "tri_state", None, None, None, "心血管風險因子", 53, 326),
        ("risk_smoking", "抽菸", "", "tri_state", None, None, None, "心血管風險因子", 54, 327),
        ("metabolic_abdominal_obesity", "代謝症候群：腹部肥胖", "男性≥90 cm、女性≥80 cm。", "tri_state", None, None, None, "代謝症候群", 60, 329),
        ("metabolic_bp", "代謝症候群：血壓偏高", "≥130/85 mmHg 或使用高血壓藥物。", "tri_state", None, None, None, "代謝症候群", 61, 330),
        ("metabolic_glucose", "代謝症候群：空腹血糖偏高", "≥100 mg/dL 或使用糖尿病藥物。", "tri_state", None, None, None, "代謝症候群", 62, 331),
        ("metabolic_tg", "代謝症候群：空腹 TG 偏高", "≥150 mg/dL 或使用治療 TG 血脂藥物。", "tri_state", None, None, None, "代謝症候群", 63, 332),
        ("metabolic_low_hdl", "代謝症候群：HDL-C 偏低", "男性<40 mg/dL、女性<50 mg/dL。", "tri_state", None, None, None, "代謝症候群", 64, 333),
    ]


def _model_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    categories = [
        {"category_key": "extreme", "label": "極高風險", "priority": 1, "ldl_threshold_mg_dl": 55, "source_doc_order": 262},
        {"category_key": "very_high", "label": "非常高風險", "priority": 2, "ldl_threshold_mg_dl": 70, "source_doc_order": 269},
        {"category_key": "high", "label": "高風險", "priority": 3, "ldl_threshold_mg_dl": 100, "source_doc_order": 273},
        {"category_key": "moderate", "label": "中風險", "priority": 4, "ldl_threshold_mg_dl": 115, "source_doc_order": 286},
        {"category_key": "low", "label": "低風險", "priority": 5, "ldl_threshold_mg_dl": 130, "source_doc_order": 289},
        {"category_key": "zero", "label": "0項心血管風險因子", "priority": 6, "ldl_threshold_mg_dl": 160, "source_doc_order": 292},
    ]
    branches: list[dict[str, Any]] = []
    predicates: list[dict[str, Any]] = []

    def branch(category: str, key: str, specs: Sequence[tuple[str | None, str, Any, int]]) -> None:
        order = sum(1 for row in branches if row["category_key"] == category)
        branches.append(
            {"category_key": category, "branch_key": key, "branch_order": order}
        )
        for predicate_order, (input_key, operator, operand, source_doc_order) in enumerate(specs):
            predicates.append(
                {
                    "category_key": category,
                    "branch_key": key,
                    "predicate_order": predicate_order,
                    "input_key": input_key,
                    "operator": operator,
                    "operand": operand,
                    "source_doc_order": source_doc_order,
                }
            )

    T = ("is_true", True)
    branch("extreme", "cad_mi_within_year", [("coronary_artery_disease", *T, 296), ("mi_within_one_year", *T, 297)])
    branch("extreme", "cad_recurrent_mi", [("coronary_artery_disease", *T, 296), ("mi_history_count", "gte", 2, 298)])
    branch("extreme", "cad_multivessel", [("coronary_artery_disease", *T, 296), ("multivessel_coronary_obstruction", *T, 299)])
    branch("extreme", "cad_acs_diabetes", [("coronary_artery_disease", *T, 296), ("acute_coronary_syndrome", *T, 300), ("diabetes", *T, 300)])
    branch("extreme", "cad_pad", [("coronary_artery_disease", *T, 296), ("peripheral_artery_disease", *T, 301)])
    branch("extreme", "cad_carotid", [("coronary_artery_disease", *T, 296), ("carotid_stenosis", *T, 301)])
    branch("extreme", "pad_cad", [("peripheral_artery_disease", *T, 302), ("coronary_artery_disease", *T, 303)])
    branch("extreme", "pad_carotid", [("peripheral_artery_disease", *T, 302), ("carotid_stenosis", *T, 304)])
    branch("very_high", "acs_history", [("acute_coronary_syndrome", *T, 307)])
    branch("very_high", "revascularization", [("revascularization", *T, 308)])
    branch("very_high", "stroke_tia_atherosclerosis", [("ischemic_stroke_tia_atherosclerosis", *T, 309)])
    branch("very_high", "pad_symptomatic_or_treated", [("symptomatic_or_treated_pad", *T, 310)])
    branch("very_high", "imaging_plaque", [("plaque_stenosis_percent", "gte", 50, 311)])
    branch("high", "diabetes", [("diabetes", *T, 316)])
    branch("high", "ckd_uacr", [("predialysis_ckd", *T, 317), ("ckd_duration_months", "gte", 3, 317), ("uacr_mg_g", "gte", 30, 317)])
    branch("high", "ckd_egfr", [("predialysis_ckd", *T, 317), ("ckd_duration_months", "gte", 3, 317), ("egfr_ml_min_1_73m2", "lt", 60, 317)])
    branch("high", "ldl_190", [("ldl_c_mg_dl", "gte", 190, 318)])
    branch("high", "cac_400", [("cac_score", "gte", 400, 319)])
    aggregate = {
        "members": [
            "risk_hypertension", "risk_age_threshold", "risk_family_history",
            "risk_low_hdl", "risk_smoking",
        ],
        "derived_members": [
            {
                "minimum": 3,
                "members": [
                    "metabolic_abdominal_obesity", "metabolic_bp",
                    "metabolic_glucose", "metabolic_tg", "metabolic_low_hdl",
                ],
            }
        ],
    }
    branch("moderate", "two_or_more_risk_factors", [(None, "aggregate_gte", {**aggregate, "target": 2}, 320)])
    branch("low", "one_risk_factor", [(None, "aggregate_eq", {**aggregate, "target": 1}, 321)])
    branch("zero", "zero_risk_factors", [(None, "aggregate_eq", {**aggregate, "target": 0}, 322)])
    return categories, branches, predicates


def _validate_predecessor(predecessor: Mapping[str, Any]) -> list[dict[str, Any]]:
    if str(predecessor.get("clause_code") or "") != "2.6.1":
        raise AnnouncedDyslipidemiaError("predecessor clause is not 2.6.1")
    if predecessor.get("raw_text_sha256") != PREDECESSOR_TEXT_SHA256:
        raise AnnouncedDyslipidemiaError("predecessor 2.6.1 text hash changed")
    blocks = [dict(row) for row in predecessor.get("blocks") or []]
    if [int(row["block_order"]) for row in blocks] != list(range(72)):
        raise AnnouncedDyslipidemiaError(
            "predecessor 2.6.1 block coverage is not exactly 0..71"
        )
    if not str(blocks[0]["raw_text"]).startswith("2.6.1."):
        raise AnnouncedDyslipidemiaError("predecessor clause heading changed")
    if str(blocks[1]["raw_text"]).strip() != (
        "全民健康保險降膽固醇藥物給付規定表"
    ):
        raise AnnouncedDyslipidemiaError(
            "predecessor Table-2 inheritance anchor changed"
        )
    if str(blocks[51]["raw_text"]).strip() != (
        "全民健康保險降三酸甘油酯藥物給付規定表"
    ):
        raise AnnouncedDyslipidemiaError(
            "predecessor triglyceride-table anchor changed"
        )
    return blocks


def _patch_composite_source(
    block: Mapping[str, Any],
    *,
    patch_component_order: int,
) -> dict[str, Any]:
    locator = dict(block["locator"])
    role = _component_role(int(locator["document_order"]))
    render_locator: dict[str, Any] = {"section_role": role}
    if role == "table2_code_set":
        block_kind = "table_paragraph"
        container = "table_cell"
        render_locator.update(
            {
                "table_index": 0,
                "table_role": "table2_product_codes",
                "row_index": int(locator["row_index"]),
                "cell_index": int(locator["cell_index"]),
            }
        )
    elif role == "table1_matrix":
        block_kind = "table_paragraph"
        container = "table_cell"
        render_locator.update(
            {
                "table_index": 1,
                "table_role": "table1_ldl_thresholds",
                "row_index": int(locator["row_index"]),
                "cell_index": int(locator["cell_index"]),
            }
        )
    else:
        block_kind = "paragraph"
        container = "flow"
    return {
        "origin_lane": "amendment_exact",
        "patch_component_order": patch_component_order,
        "predecessor_publication_run_id": None,
        "predecessor_block_order": None,
        "source_artifact_sha256": EXPECTED_ARTIFACT_SHA256,
        "source_block_id": block["block_id"],
        "block_kind": block_kind,
        "container": container,
        "raw_text": block["raw_text"],
        "raw_text_sha256": block["raw_text_sha256"],
        "source_locator": locator,
        "render_locator": render_locator,
        "inheritance_basis": None,
    }


def _inherited_composite_source(
    block: Mapping[str, Any],
    *,
    predecessor: Mapping[str, Any],
) -> dict[str, Any]:
    source_order = int(block["block_order"])
    source_locator = dict(block["source_locator"])
    render_locator: dict[str, Any]
    if 2 <= source_order <= 38:
        render_locator = {
            "table_index": 2,
            "table_role": "table2_ldl_thresholds",
            "row_index": int(source_locator["row_logical_index"]),
            "cell_index": int(source_locator["cell_logical_index"]),
            "section_role": "inherited_table2",
        }
        block_kind = "table_paragraph"
        container = "table_cell"
    elif 52 <= source_order <= 71:
        render_locator = {
            "table_index": 3,
            "table_role": "triglyceride_thresholds",
            "row_index": int(source_locator["row_logical_index"]),
            "cell_index": int(source_locator["cell_logical_index"]),
            "section_role": "inherited_triglyceride_table",
        }
        block_kind = "table_paragraph"
        container = "table_cell"
    else:
        render_locator = {"section_role": "inherited_remainder"}
        block_kind = "paragraph"
        container = "flow"
    return {
        "origin_lane": "predecessor_inherited",
        "patch_component_order": None,
        "predecessor_publication_run_id": predecessor["run_id"],
        "predecessor_block_order": source_order,
        "source_artifact_sha256": predecessor["source_artifact_sha256"],
        "source_block_id": block["source_block_id"],
        "block_kind": block_kind,
        "container": container,
        "raw_text": block["raw_text"],
        "raw_text_sha256": block["raw_text_sha256"],
        "source_locator": source_locator,
        "render_locator": render_locator,
        "inheritance_basis": (
            "The official amendment comparison ends the new 2.6.1 column "
            "below its Table-2 heading with '(以下略)'; predecessor blocks "
            "2..71 are replayed byte-exact as the unchanged remainder."
        ),
    }


def _predecessor_document_sources(
    predecessor: Mapping[str, Any],
    predecessor_blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for block in predecessor_blocks:
        source_order = int(block["block_order"])
        source_locator = dict(block["source_locator"])
        if source_order == 0:
            render_locator = {"section_role": "clause_heading"}
            container = "flow"
            block_kind = "paragraph"
        elif source_order == 1:
            render_locator = {"section_role": "legacy_ldl_heading"}
            container = "flow"
            block_kind = "paragraph"
        elif 2 <= source_order <= 38:
            render_locator = {
                "table_index": 0,
                "table_role": "legacy_ldl_thresholds",
                "row_index": int(source_locator["row_logical_index"]),
                "cell_index": int(source_locator["cell_logical_index"]),
                "section_role": "legacy_ldl_thresholds",
            }
            container = "table_cell"
            block_kind = "table_paragraph"
        elif source_order == 51:
            render_locator = {"section_role": "triglyceride_heading"}
            container = "flow"
            block_kind = "paragraph"
        elif 52 <= source_order <= 71:
            render_locator = {
                "table_index": 1,
                "table_role": "triglyceride_thresholds",
                "row_index": int(source_locator["row_logical_index"]),
                "cell_index": int(source_locator["cell_logical_index"]),
                "section_role": "triglyceride_thresholds",
            }
            container = "table_cell"
            block_kind = "table_paragraph"
        else:
            render_locator = {"section_role": "legacy_remainder"}
            container = "flow"
            block_kind = "paragraph"
        sources.append(
            {
                "origin_lane": "source_complete",
                "patch_component_order": None,
                "predecessor_publication_run_id": predecessor["run_id"],
                "predecessor_block_order": source_order,
                "source_artifact_sha256": predecessor[
                    "source_artifact_sha256"
                ],
                "source_block_id": block["source_block_id"],
                "block_kind": block_kind,
                "container": container,
                "raw_text": block["raw_text"],
                "raw_text_sha256": block["raw_text_sha256"],
                "source_locator": source_locator,
                "render_locator": render_locator,
                "inheritance_basis": None,
            }
        )
    return sources


def _terminology_projection_rows(
    *,
    run_id: str,
    version_id: str,
    clause_code: str,
    composite_sources: Sequence[Mapping[str, Any]],
    terminology_projection: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tagging_run_id = str(
        terminology_projection.get("tagging_run_id") or ""
    )
    aliases = list(terminology_projection.get("aliases") or ())
    if not tagging_run_id or not aliases:
        raise AnnouncedDyslipidemiaError(
            "active reviewed terminology projection is unavailable"
        )
    block_inputs: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    for block_order, block in enumerate(composite_sources):
        raw_text = str(block["raw_text"])
        matches = scan_block_alias_occurrences(raw_text, aliases)
        status_counts = {
            status: sum(
                row["occurrence_status"] == status for row in matches
            )
            for status in ("admitted", "candidate", "blocked")
        }
        block_inputs.append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "block_order": block_order,
                    "terminology_tagging_run_id": tagging_run_id,
                    "source_block_id": block["source_block_id"],
                    "source_block_sha256": block["raw_text_sha256"],
                    "matcher_version": MATCHER_VERSION,
                    "offset_contract": OFFSET_CONTRACT,
                    "alias_admission_policy": ALIAS_ADMISSION_POLICY,
                    "scan_status": (
                        "scanned_with_match"
                        if matches
                        else "scanned_no_match"
                    ),
                    "candidate_match_count": status_counts["candidate"],
                    "admitted_match_count": status_counts["admitted"],
                    "blocked_match_count": status_counts["blocked"],
                }
            )
        )
        for match in matches:
            identity = {
                "run_id": run_id,
                "version_id": version_id,
                "block_order": block_order,
                "concept_id": match["concept_id"],
                "alias_id": match["alias_id"],
                "start_scalar": match["start_scalar"],
                "end_scalar": match["end_scalar"],
                "occurrence_status": match["occurrence_status"],
            }
            occurrences.append(
                _with_hash(
                    {
                        "run_id": run_id,
                        "version_id": version_id,
                        "occurrence_id": _stable_uuid(
                            "announced-terminology-occurrence", identity
                        ),
                        "clause_code": clause_code,
                        "block_order": block_order,
                        "terminology_tagging_run_id": tagging_run_id,
                        "source_block_id": block["source_block_id"],
                        "source_block_sha256": block["raw_text_sha256"],
                        "concept_id": match["concept_id"],
                        "alias_id": match["alias_id"],
                        "start_scalar": match["start_scalar"],
                        "end_scalar": match["end_scalar"],
                        "start_utf8_byte": match["start_utf8_byte"],
                        "end_utf8_byte": match["end_utf8_byte"],
                        "matched_text": match["matched_text"],
                        "matched_text_sha256": _sha256_text(
                            str(match["matched_text"])
                        ),
                        "occurrence_status": match["occurrence_status"],
                        "occurrence_reason": match["occurrence_reason"],
                        "match_rule": match["match_rule"],
                    }
                )
            )
    return block_inputs, occurrences


def _adjacent_diff_rows(
    *,
    run_id: str,
    version_id: str,
    clause_code: str,
    predecessor: Mapping[str, Any],
    predecessor_blocks: Sequence[Mapping[str, Any]],
    composite_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    matcher = SequenceMatcher(
        None,
        [
            semantic_comparison_text(str(row["raw_text"]))
            for row in predecessor_blocks
        ],
        [
            semantic_comparison_text(str(row["raw_text"]))
            for row in composite_sources
        ],
        autojunk=False,
    )
    rows: list[dict[str, Any]] = []
    for opcode, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if opcode == "equal":
            continue
        old_text = (
            "\n\n".join(
                str(row["raw_text"])
                for row in predecessor_blocks[old_start:old_end]
            )
            or None
        )
        new_text = (
            "\n\n".join(
                str(row["raw_text"])
                for row in composite_sources[new_start:new_end]
            )
            or None
        )
        presentation = semantic_diff_presentation(old_text, new_text)
        if presentation["semantic_change_kind"] == "format_only":
            continue
        hunk_order = len(rows)
        rows.append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "hunk_id": _stable_uuid(
                        "announced-adjacent-diff-hunk",
                        {
                            "version_id": version_id,
                            "hunk_order": hunk_order,
                            "old_start": old_start,
                            "old_end": old_end,
                            "new_start": new_start,
                            "new_end": new_end,
                            "old_text": old_text,
                            "new_text": new_text,
                        },
                    ),
                    "clause_code": clause_code,
                    "predecessor_publication_run_id": predecessor["run_id"],
                    "predecessor_text_sha256": predecessor[
                        "raw_text_sha256"
                    ],
                    "hunk_order": hunk_order,
                    "semantic_change_kind": presentation[
                        "semantic_change_kind"
                    ],
                    "display_note": presentation["display_note"],
                    "old_block_start": (
                        old_start if old_start != old_end else None
                    ),
                    "old_block_end": (
                        old_end if old_start != old_end else None
                    ),
                    "new_block_start": (
                        new_start if new_start != new_end else None
                    ),
                    "new_block_end": (
                        new_end if new_start != new_end else None
                    ),
                    "old_text": old_text,
                    "new_text": new_text,
                    "old_text_sha256": (
                        _sha256_text(old_text) if old_text else None
                    ),
                    "new_text_sha256": (
                        _sha256_text(new_text) if new_text else None
                    ),
                    "inline_segments": presentation["inline_segments"],
                    "ignored_change_classes": presentation[
                        "ignored_change_classes"
                    ],
                    "comparison_label": "與上一版本差異",
                    "algorithm_version": DIFF_PRESENTATION_VERSION,
                    "ignored_change_policy": IGNORED_CHANGE_POLICY,
                }
            )
        )
    if not rows:
        raise AnnouncedDyslipidemiaError(
            "announced version produced no substantive adjacent diff"
        )
    return rows


def prepare_announced_material(
    odt_path: Path,
    *,
    known_products: Sequence[Mapping[str, Any]],
    predecessor: Mapping[str, Any],
    terminology_projection: Mapping[str, Any],
    source_release_run_id: str | None = None,
    source_version_id: str | None = None,
) -> AnnouncedMaterial:
    payload = Path(odt_path).read_bytes()
    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    if artifact_sha256 != EXPECTED_ARTIFACT_SHA256:
        raise AnnouncedDyslipidemiaError("official amendment ODT hash mismatch")
    inspected = inspect_odt_document(payload, artifact_sha256)
    facts = inspected["structural_facts"]
    if not facts.get("exact_once_verified") or facts.get("emitted_block_count") != 397:
        raise AnnouncedDyslipidemiaError("official amendment structural parity failed")
    all_blocks = inspected["blocks"]
    selected = _selected_source_blocks(all_blocks)
    predecessor_blocks = _validate_predecessor(predecessor)
    table2_products, code_doc_orders = _extract_table2_products(all_blocks)
    component_manifest = [
        {
            "source_block_id": row["block_id"],
            "source_locator": row["locator"],
            "raw_text_sha256": row["raw_text_sha256"],
        }
        for row in selected
    ]
    patch_text = "\n\n".join(str(row["raw_text"]) for row in selected)
    patch_sha = _sha256_text(patch_text)
    component_manifest_sha = object_fingerprint(component_manifest)
    amendment_composite_sources = [
        _patch_composite_source(row, patch_component_order=index)
        for index, row in enumerate(selected[:-1])
    ]
    inherited_composite_sources = [
        _inherited_composite_source(row, predecessor=predecessor)
        for row in predecessor_blocks[2:]
    ]
    composite_sources = [
        *amendment_composite_sources,
        *inherited_composite_sources,
    ]
    if len(amendment_composite_sources) != 336:
        raise AnnouncedDyslipidemiaError(
            "composite amendment block count is not 336"
        )
    if len(inherited_composite_sources) != 70:
        raise AnnouncedDyslipidemiaError(
            "composite inherited block count is not 70"
        )
    composition_manifest = [
        {
            "block_order": index,
            "origin_lane": row["origin_lane"],
            "source_artifact_sha256": row["source_artifact_sha256"],
            "source_block_id": row["source_block_id"],
            "raw_text_sha256": row["raw_text_sha256"],
            "patch_component_order": row["patch_component_order"],
            "predecessor_publication_run_id": (
                row["predecessor_publication_run_id"]
            ),
            "predecessor_block_order": row["predecessor_block_order"],
            "render_locator": row["render_locator"],
        }
        for index, row in enumerate(composite_sources)
    ]
    composition_manifest_sha = object_fingerprint(composition_manifest)
    composed_text = "\n\n".join(
        str(row["raw_text"]) for row in composite_sources
    )
    composed_text_sha = _sha256_text(composed_text)
    old_document_sources = _predecessor_document_sources(
        predecessor, predecessor_blocks
    )
    old_document_blueprint = _document_structure_blueprint(
        old_document_sources,
        clause_code="2.6.1",
    )
    new_document_blueprint = _document_structure_blueprint(
        composite_sources,
        clause_code="2.6.1",
    )
    doc_to_component = {
        int(row["locator"]["document_order"]): index
        for index, row in enumerate(selected)
    }
    for required_order in {
        *[row[-1] for row in _inputs()],
        *[row["source_doc_order"] for row in _model_graph()[0]],
        *[row["source_doc_order"] for row in _model_graph()[2]],
    }:
        if required_order not in doc_to_component:
            raise AnnouncedDyslipidemiaError(
                f"decision source block {required_order} is outside patch"
            )

    table2_by_code = {row["nhi_code"]: row for row in table2_products}
    known_by_code: dict[str, dict[str, Any]] = {}
    for source in known_products:
        code = str(source.get("drug_code") or "").strip().upper()
        if not _TABLE2_CODE_RE.fullmatch(code):
            raise AnnouncedDyslipidemiaError("known C10 product code is invalid")
        if code in known_by_code:
            raise AnnouncedDyslipidemiaError("known C10 product code is duplicated")
        known_by_code[code] = {
            "nhi_code": code,
            "product_name": str(source.get("name_zh") or source.get("name_en") or "").strip(),
            "ingredient_name": None,
            "atc_code": str(source.get("atc_code") or "").strip().upper() or None,
        }
    missing_table2 = sorted(set(table2_by_code) - set(known_by_code))
    if missing_table2:
        raise AnnouncedDyslipidemiaError(
            f"Table-2 code missing from pinned C10 master: {missing_table2[:3]}"
        )

    graph_categories, graph_branches, graph_predicates = _model_graph()
    model_source = {
        "inputs": _inputs(),
        "categories": graph_categories,
        "branches": graph_branches,
        "predicates": graph_predicates,
        "table2_codes": sorted(table2_by_code),
        "known_products": [
            known_by_code[code] for code in sorted(known_by_code)
        ],
    }
    code_sha = code_fingerprint(Path(__file__).resolve())
    migration_sha = object_fingerprint(
        {
            "v21": migration_fingerprint(MIGRATION),
            "v22": migration_fingerprint(RELEASE_GATE_MIGRATION),
            "v23": migration_fingerprint(COMPOSITION_MIGRATION),
            "v24": migration_fingerprint(VERSION_PROJECTION_MIGRATION),
            "v25": migration_fingerprint(DOCUMENT_COMPONENT_MIGRATION),
        }
    )
    input_fingerprint = object_fingerprint(
        {
            "artifact_sha256": artifact_sha256,
            "predecessor_text_sha256": PREDECESSOR_TEXT_SHA256,
            "component_manifest_sha256": component_manifest_sha,
            "predecessor_publication_run_id": predecessor["run_id"],
            "predecessor_source_artifact_sha256": (
                predecessor["source_artifact_sha256"]
            ),
            "composition_rule_version": COMPOSITION_RULE_VERSION,
            "composition_manifest_sha256": composition_manifest_sha,
            "composed_text_sha256": composed_text_sha,
            "terminology_projection": {
                "tagging_run_id": terminology_projection.get(
                    "tagging_run_id"
                ),
                "output_fingerprint": terminology_projection.get(
                    "output_fingerprint"
                ),
                "sealed_fingerprint": terminology_projection.get(
                    "sealed_fingerprint"
                ),
                "matcher_version": MATCHER_VERSION,
                "offset_contract": OFFSET_CONTRACT,
                "alias_admission_policy": ALIAS_ADMISSION_POLICY,
            },
            "diff_algorithm_version": DIFF_PRESENTATION_VERSION,
            "ignored_change_policy": IGNORED_CHANGE_POLICY,
            "model": model_source,
            "loader_version": LOADER_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "code_sha256": code_sha,
            "migration_sha256": migration_sha,
        }
    )
    run_id = _stable_uuid("release-run", input_fingerprint)
    notice_id = _stable_uuid("notice", [NOTICE_REFERENCE, artifact_sha256])
    patch_id = _stable_uuid("patch", ["2.6.1", EFFECTIVE_DATE, patch_sha])
    version_id = _stable_uuid(
        "composed-version",
        [
            patch_id,
            predecessor["run_id"],
            composition_manifest_sha,
        ],
    )
    effective_source_release_run_id = source_release_run_id or run_id
    effective_source_version_id = source_version_id or version_id
    model_id = _stable_uuid("model", [patch_id, EVALUATOR_VERSION])
    effect_ids = {
        key: _stable_uuid("effect", [notice_id, key])
        for key in ("2.6.1", "2.6.2", "2.6.3", "reimbursed_item_change")
    }
    unresolved_scope = [
        {"effect_type": "clause_amendment", "clause_code": "2.6.2"},
        {"effect_type": "clause_amendment", "clause_code": "2.6.3"},
        {"effect_type": "reimbursed_item_change", "clause_code": None},
    ]

    rows: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "notice_event", "notice_effect", "clause_patch", "patch_component",
            "composed_clause_version", "composed_clause_block",
            "composed_clause_tagging_block_input",
            "composed_clause_terminology_occurrence",
            "composed_clause_diff_hunk",
            "reimbursement_product_snapshot",
            "composed_clause_reimbursement_code",
            "decision_model", "decision_input", "risk_category", "risk_branch",
            "risk_predicate", "model_product_code",
        )
    }
    rows["notice_event"].append(
        _with_hash(
            {
                "run_id": run_id,
                "notice_id": notice_id,
                "reference_number": NOTICE_REFERENCE,
                "title": NOTICE_TITLE,
                "official_url": NOTICE_URL,
                "published_on": PUBLICATION_DATE,
                "effective_on": EFFECTIVE_DATE,
                "civil_timezone": "Asia/Taipei",
                "source_artifact_sha256": artifact_sha256,
                "source_artifact_filename": SOURCE_ARTIFACT_FILENAME,
                "source_exact": True,
                "event_scope_complete": False,
                "unresolved_scope": unresolved_scope,
            }
        )
    )
    for key, effect_type, clause_code, status, note in (
        ("2.6.1", "clause_amendment", "2.6.1", "projected_source_exact_patch", "2.6.1 source-exact amendment patch projected first"),
        ("2.6.2", "clause_amendment", "2.6.2", "pending_projection", "2.6.2 remains in the same official event"),
        ("2.6.3", "clause_amendment", "2.6.3", "pending_projection", "2.6.3 remains in the same official event"),
        ("reimbursed_item_change", "reimbursed_item_change", None, "pending_projection", "attachment 1 price and reimbursed-item changes remain separate"),
    ):
        rows["notice_effect"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "effect_id": effect_ids[key],
                    "notice_id": notice_id,
                    "effect_type": effect_type,
                    "clause_code": clause_code,
                    "projection_status": status,
                    "scope_note": note,
                }
            )
        )
    rows["clause_patch"].append(
        _with_hash(
            {
                "run_id": run_id,
                "patch_id": patch_id,
                "effect_id": effect_ids["2.6.1"],
                "clause_code": "2.6.1",
                "predecessor_text_sha256": PREDECESSOR_TEXT_SHA256,
                "effective_from": EFFECTIVE_DATE,
                "effective_until": None,
                "resolution_state": "verified_scheduled",
                "source_exact_patch_text": patch_text,
                "source_exact_patch_sha256": patch_sha,
                "omitted_text_present": True,
                "composition_status": "reviewed_composite",
                "comparison_sha256": _sha256_text(semantic_comparison_text(patch_text)),
                "component_manifest_sha256": component_manifest_sha,
                "partial_event_projection": True,
                "unprocessed_event_scope": unresolved_scope,
                "public_note": (
                    "完整 2.6.1 由公告逐字修正 blocks 與 115.5.22 "
                    "官方分章檔未變 remainder 機械合成；每一 block "
                    "均保存來源 lane、locator 與 SHA-256。"
                ),
            }
        )
    )
    for order, block in enumerate(selected):
        rows["patch_component"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "patch_id": patch_id,
                    "component_order": order,
                    "component_role": _component_role(
                        int(block["locator"]["document_order"])
                    ),
                    "source_block_id": block["block_id"],
                    "source_locator": block["locator"],
                    "raw_text": block["raw_text"],
                    "raw_text_sha256": block["raw_text_sha256"],
                }
            )
        )
    rows["composed_clause_version"].append(
        _with_hash(
            {
                "run_id": run_id,
                "version_id": version_id,
                "patch_id": patch_id,
                "clause_code": "2.6.1",
                "effective_from": EFFECTIVE_DATE,
                "predecessor_publication_run_id": predecessor["run_id"],
                "predecessor_text_sha256": PREDECESSOR_TEXT_SHA256,
                "predecessor_source_artifact_sha256": (
                    predecessor["source_artifact_sha256"]
                ),
                "composition_rule_version": COMPOSITION_RULE_VERSION,
                "composition_manifest_sha256": composition_manifest_sha,
                "composed_text": composed_text,
                "composed_text_sha256": composed_text_sha,
                "amendment_block_count": len(amendment_composite_sources),
                "inherited_block_count": len(inherited_composite_sources),
                "review_status": "deterministic_owner_directed",
                "public_note": (
                    "公告新文與 predecessor 未變 remainder 已正規化為 "
                    "single-clause complete version。"
                ),
            }
        )
    )
    for order, source in enumerate(composite_sources):
        rows["composed_clause_block"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "patch_id": patch_id,
                    "clause_code": "2.6.1",
                    "block_order": order,
                    **source,
                }
            )
        )
    clause_work_id = _stable_uuid(
        "clause-document-work", ["taiwan_nhi", "2.6.1"]
    )
    older_expression_id = _stable_uuid(
        "clause-document-expression",
        [
            "current_publication",
            predecessor["run_id"],
            predecessor["raw_text_sha256"],
        ],
    )
    newer_expression_id = _stable_uuid(
        "clause-document-expression",
        [
            "announced_composite",
            effective_source_release_run_id,
            effective_source_version_id,
            composed_text_sha,
        ],
    )
    expression_relation_id = _stable_uuid(
        "clause-document-expression-relation",
        [older_expression_id, newer_expression_id, NOTICE_REFERENCE],
    )
    normalization_input_fingerprint = object_fingerprint(
        {
            "source_release_run_id": effective_source_release_run_id,
            "source_version_id": effective_source_version_id,
            "clause_work_id": clause_work_id,
            "older_expression_id": older_expression_id,
            "newer_expression_id": newer_expression_id,
            "predecessor_text_sha256": predecessor["raw_text_sha256"],
            "composition_manifest_sha256": composition_manifest_sha,
            "old_structure_manifest_sha256": old_document_blueprint[
                "structure_manifest_sha256"
            ],
            "new_structure_manifest_sha256": new_document_blueprint[
                "structure_manifest_sha256"
            ],
            "parser_version": DOCUMENT_NORMALIZATION_VERSION,
            "rules_version": DOCUMENT_NORMALIZATION_VERSION,
            "migration_sha256": migration_fingerprint(
                DOCUMENT_COMPONENT_MIGRATION
            ),
            "code_sha256": code_sha,
        }
    )
    normalization_run_id = _stable_uuid(
        "clause-document-normalization-run",
        normalization_input_fingerprint,
    )
    (
        identity_rows,
        normalization_rows,
        normalization_expected_counts,
        normalization_table_fingerprints,
        normalization_receipt,
        normalized_node_ids,
    ) = _normalization_projection_rows(
        normalization_run_id=normalization_run_id,
        source_release_run_id=effective_source_release_run_id,
        source_version_id=effective_source_version_id,
        clause_work_id=clause_work_id,
        older_expression_id=older_expression_id,
        newer_expression_id=newer_expression_id,
        relation_id=expression_relation_id,
        predecessor=predecessor,
        old_sources=old_document_sources,
        new_sources=composite_sources,
        old_blueprint=old_document_blueprint,
        new_blueprint=new_document_blueprint,
        composition_manifest_sha256=composition_manifest_sha,
        notice_id=notice_id,
    )
    normalization_output_fingerprint = str(
        normalization_receipt["output_fingerprint"]
    )
    normalization_sealed_fingerprint = object_fingerprint(
        {
            "normalization_run_id": normalization_run_id,
            "input_fingerprint": normalization_input_fingerprint,
            "output_fingerprint": normalization_output_fingerprint,
            "parser_version": DOCUMENT_NORMALIZATION_VERSION,
            "rules_version": DOCUMENT_NORMALIZATION_VERSION,
        }
    )
    diff_input_fingerprint = object_fingerprint(
        {
            "normalization_run_id": normalization_run_id,
            "expression_relation_id": expression_relation_id,
            "relation_status": "direct_predecessor_verified",
            "old_expression_sha256": predecessor["raw_text_sha256"],
            "new_expression_sha256": composed_text_sha,
            "alignment_version": DOCUMENT_ALIGNMENT_VERSION,
            "algorithm_version": EXACT_DIFF_ALGORITHM_VERSION,
            "tokenizer_version": EXACT_DIFF_TOKENIZER_VERSION,
            "tie_break_version": EXACT_DIFF_TIE_BREAK_VERSION,
            "unicode_profile": EXACT_DIFF_UNICODE_PROFILE,
            "display_policy_version": DIFF_DISPLAY_POLICY_VERSION,
        }
    )
    diff_run_id = _stable_uuid(
        "clause-document-diff-run", diff_input_fingerprint
    )
    (
        diff_rows,
        diff_expected_counts,
        diff_table_fingerprints,
    ) = _diff_projection_rows(
        diff_run_id=diff_run_id,
        older_expression_id=older_expression_id,
        newer_expression_id=newer_expression_id,
        relation_status="direct_predecessor_verified",
        old_blueprint=old_document_blueprint,
        new_blueprint=new_document_blueprint,
        old_node_ids=normalized_node_ids["older"],
        new_node_ids=normalized_node_ids["newer"],
    )
    diff_output_fingerprint = object_fingerprint(
        {
            "counts": diff_expected_counts,
            "table_fingerprints": diff_table_fingerprints,
        }
    )
    diff_sealed_fingerprint = object_fingerprint(
        {
            "diff_run_id": diff_run_id,
            "input_fingerprint": diff_input_fingerprint,
            "output_fingerprint": diff_output_fingerprint,
            "alignment_version": DOCUMENT_ALIGNMENT_VERSION,
            "algorithm_version": EXACT_DIFF_ALGORITHM_VERSION,
        }
    )
    tagging_inputs, terminology_occurrences = _terminology_projection_rows(
        run_id=run_id,
        version_id=version_id,
        clause_code="2.6.1",
        composite_sources=composite_sources,
        terminology_projection=terminology_projection,
    )
    rows["composed_clause_tagging_block_input"].extend(tagging_inputs)
    rows["composed_clause_terminology_occurrence"].extend(
        terminology_occurrences
    )
    rows["composed_clause_diff_hunk"].extend(
        _adjacent_diff_rows(
            run_id=run_id,
            version_id=version_id,
            clause_code="2.6.1",
            predecessor=predecessor,
            predecessor_blocks=predecessor_blocks,
            composite_sources=composite_sources,
        )
    )

    predicate_fingerprint = object_fingerprint(
        {
            "categories": graph_categories,
            "branches": graph_branches,
            "predicates": graph_predicates,
        }
    )
    product_fingerprint = object_fingerprint(
        {
            "known": sorted(known_by_code),
            "table2": sorted(table2_by_code),
        }
    )
    rows["decision_model"].append(
        _with_hash(
            {
                "run_id": run_id,
                "model_id": model_id,
                "patch_id": patch_id,
                "model_key": "dyslipidemia-2.6.1-table1-2026-09-01",
                "title": "2.6.1 表一 LDL-C 起始治療門檻檢查",
                "scope_label": "表一 LDL-C 起始治療門檻檢查",
                "model_status": "future_opt_in",
                "effective_from": EFFECTIVE_DATE,
                "effective_until": None,
                "evaluator_version": EVALUATOR_VERSION,
                "predicate_set_fingerprint": predicate_fingerprint,
                "product_set_fingerprint": product_fingerprint,
                "outcome_codes": [
                    "table1_threshold_met",
                    "table1_threshold_not_met",
                    "requires_table2_assessment",
                    "insufficient_information",
                ],
                "explanation_disclaimer": (
                    "本結果僅為所選官方版本之表一 LDL-C 起始治療門檻機械判讀，"
                    "不是臨床建議、申報核准或完整健保給付保證。"
                ),
            }
        )
    )
    for item in _inputs():
        (
            key, label, help_text, control_type, unit, min_value, max_value,
            group, display_order, source_doc_order,
        ) = item
        rows["decision_input"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    "input_key": key,
                    "label": label,
                    "help_text": help_text,
                    "control_type": control_type,
                    "unit": unit,
                    "min_value": min_value,
                    "max_value": max_value,
                    "display_group": group,
                    "display_order": display_order,
                    "source_component_order": doc_to_component[source_doc_order],
                }
            )
        )
    for category in graph_categories:
        rows["risk_category"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    "category_key": category["category_key"],
                    "label": category["label"],
                    "priority": category["priority"],
                    "ldl_threshold_mg_dl": category["ldl_threshold_mg_dl"],
                    "source_component_order": doc_to_component[
                        category["source_doc_order"]
                    ],
                }
            )
        )
    for branch_row in graph_branches:
        rows["risk_branch"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    **branch_row,
                }
            )
        )
    for predicate in graph_predicates:
        rows["risk_predicate"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    "category_key": predicate["category_key"],
                    "branch_key": predicate["branch_key"],
                    "predicate_order": predicate["predicate_order"],
                    "input_key": predicate["input_key"],
                    "operator": predicate["operator"],
                    "operand": predicate["operand"],
                    "source_component_order": doc_to_component[
                        predicate["source_doc_order"]
                    ],
                }
            )
        )
    for code in sorted(known_by_code):
        product = dict(known_by_code[code])
        exception = table2_by_code.get(code)
        source_component_order = (
            doc_to_component[code_doc_orders[code]]
            if exception
            else None
        )
        if exception:
            product.update(
                product_name=exception["product_name"],
                ingredient_name=exception["ingredient_name"],
            )
        rows["reimbursement_product_snapshot"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    **product,
                    "snapshot_basis": (
                        "notice_exact_code_set"
                        if exception
                        else "nhi_product_master_snapshot"
                    ),
                    "source_component_order": source_component_order,
                }
            )
        )
        rows["composed_clause_reimbursement_code"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "version_id": version_id,
                    "nhi_code": code,
                    "applicability_lane": (
                        "table2_exception"
                        if exception
                        else "table1_default"
                    ),
                    "link_basis": (
                        "notice_exact_code_set"
                        if exception
                        else (
                            "nhi_product_master_c10_minus_notice_exceptions"
                        )
                    ),
                    "source_component_order": source_component_order,
                }
            )
        )
        rows["model_product_code"].append(
            _with_hash(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    **product,
                    "rule_lane": "table2" if exception else "table1",
                    "membership_source": (
                        "notice_exact_code_set"
                        if exception
                        else "nhi_product_master_snapshot"
                    ),
                    "source_component_order": source_component_order,
                }
            )
        )

    frozen_rows = {name: tuple(value) for name, value in rows.items()}
    expected_counts = {name: len(value) for name, value in frozen_rows.items()}
    table_fingerprints = {
        name: row_set_fingerprint(row["source_row_sha256"] for row in value)
        for name, value in frozen_rows.items()
    }
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "table_fingerprints": table_fingerprints,
        }
    )
    sealed_fingerprint = object_fingerprint(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "loader_version": LOADER_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
        }
    )
    return AnnouncedMaterial(
        run_id=run_id,
        notice_id=notice_id,
        patch_id=patch_id,
        version_id=version_id,
        model_id=model_id,
        source_release_run_id=effective_source_release_run_id,
        normalization_run_id=normalization_run_id,
        diff_run_id=diff_run_id,
        clause_work_id=clause_work_id,
        older_expression_id=older_expression_id,
        newer_expression_id=newer_expression_id,
        expression_relation_id=expression_relation_id,
        rows=frozen_rows,
        identity_rows=identity_rows,
        normalization_rows=normalization_rows,
        diff_rows=diff_rows,
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
        normalization_expected_counts=normalization_expected_counts,
        normalization_table_fingerprints=(
            normalization_table_fingerprints
        ),
        diff_expected_counts=diff_expected_counts,
        diff_table_fingerprints=diff_table_fingerprints,
        document_structure_sha256=normalization_receipt[
            "structure_manifest_sha256"
        ],
        normalization_receipt=normalization_receipt,
        normalization_input_fingerprint=normalization_input_fingerprint,
        normalization_output_fingerprint=(
            normalization_output_fingerprint
        ),
        normalization_sealed_fingerprint=(
            normalization_sealed_fingerprint
        ),
        diff_input_fingerprint=diff_input_fingerprint,
        diff_output_fingerprint=diff_output_fingerprint,
        diff_sealed_fingerprint=diff_sealed_fingerprint,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        sealed_fingerprint=sealed_fingerprint,
        migration_sha256=migration_sha,
        code_sha256=code_sha,
    )


_TABLE_COLUMNS = {
    "notice_event": (
        "run_id","notice_id","reference_number","title","official_url",
        "published_on","effective_on","civil_timezone","source_artifact_sha256",
        "source_artifact_filename","source_exact","event_scope_complete",
        "unresolved_scope","source_row_sha256",
    ),
    "notice_effect": (
        "run_id","effect_id","notice_id","effect_type","clause_code",
        "projection_status","scope_note","source_row_sha256",
    ),
    "clause_patch": (
        "run_id","patch_id","effect_id","clause_code",
        "predecessor_text_sha256","effective_from","effective_until",
        "resolution_state","source_exact_patch_text","source_exact_patch_sha256",
        "omitted_text_present","composition_status","comparison_sha256",
        "component_manifest_sha256","partial_event_projection",
        "unprocessed_event_scope","public_note","source_row_sha256",
    ),
    "patch_component": (
        "run_id","patch_id","component_order","component_role","source_block_id",
        "source_locator","raw_text","raw_text_sha256","source_row_sha256",
    ),
    "composed_clause_version": (
        "run_id","version_id","patch_id","clause_code","effective_from",
        "predecessor_publication_run_id","predecessor_text_sha256",
        "predecessor_source_artifact_sha256","composition_rule_version",
        "composition_manifest_sha256","composed_text","composed_text_sha256",
        "amendment_block_count","inherited_block_count","review_status",
        "public_note","source_row_sha256",
    ),
    "composed_clause_block": (
        "run_id","version_id","patch_id","clause_code","block_order",
        "origin_lane","patch_component_order",
        "predecessor_publication_run_id","predecessor_block_order",
        "source_artifact_sha256","source_block_id","block_kind","container",
        "raw_text","raw_text_sha256","source_locator","render_locator",
        "inheritance_basis","source_row_sha256",
    ),
    "composed_clause_tagging_block_input": (
        "run_id","version_id","block_order","terminology_tagging_run_id",
        "source_block_id","source_block_sha256","matcher_version",
        "offset_contract","alias_admission_policy","scan_status",
        "candidate_match_count","admitted_match_count","blocked_match_count",
        "source_row_sha256",
    ),
    "composed_clause_terminology_occurrence": (
        "run_id","version_id","occurrence_id","clause_code","block_order",
        "terminology_tagging_run_id","source_block_id","source_block_sha256",
        "concept_id","alias_id","start_scalar","end_scalar",
        "start_utf8_byte","end_utf8_byte","matched_text",
        "matched_text_sha256","occurrence_status","occurrence_reason",
        "match_rule","source_row_sha256",
    ),
    "composed_clause_diff_hunk": (
        "run_id","version_id","hunk_id","clause_code",
        "predecessor_publication_run_id","predecessor_text_sha256",
        "hunk_order","semantic_change_kind","display_note",
        "old_block_start","old_block_end","new_block_start","new_block_end",
        "old_text","new_text","old_text_sha256","new_text_sha256",
        "inline_segments","ignored_change_classes","comparison_label",
        "algorithm_version","ignored_change_policy","source_row_sha256",
    ),
    "reimbursement_product_snapshot": (
        "run_id","nhi_code","product_name","ingredient_name","atc_code",
        "snapshot_basis","source_component_order","source_row_sha256",
    ),
    "composed_clause_reimbursement_code": (
        "run_id","version_id","nhi_code","applicability_lane","link_basis",
        "source_component_order","source_row_sha256",
    ),
    "decision_model": (
        "run_id","model_id","patch_id","model_key","title","scope_label",
        "model_status","effective_from","effective_until","evaluator_version",
        "predicate_set_fingerprint","product_set_fingerprint","outcome_codes",
        "explanation_disclaimer","source_row_sha256",
    ),
    "decision_input": (
        "run_id","model_id","input_key","label","help_text","control_type","unit",
        "min_value","max_value","display_group","display_order",
        "source_component_order","source_row_sha256",
    ),
    "risk_category": (
        "run_id","model_id","category_key","label","priority",
        "ldl_threshold_mg_dl","source_component_order","source_row_sha256",
    ),
    "risk_branch": (
        "run_id","model_id","category_key","branch_key","branch_order",
        "source_row_sha256",
    ),
    "risk_predicate": (
        "run_id","model_id","category_key","branch_key","predicate_order",
        "input_key","operator","operand","source_component_order",
        "source_row_sha256",
    ),
    "model_product_code": (
        "run_id","model_id","nhi_code","product_name","ingredient_name",
        "atc_code","rule_lane","membership_source","source_component_order",
        "source_row_sha256",
    ),
}
_IDENTITY_TABLE_COLUMNS = {
    "clause_document_work": (
        "clause_work_id", "canonical_code", "authority",
        "identity_basis", "identity_receipt_sha256", "source_row_sha256",
    ),
    "clause_document_node_work": (
        "node_work_id", "clause_work_id", "work_role",
        "creation_basis", "creation_receipt_sha256", "source_row_sha256",
    ),
}
_NORMALIZATION_TABLE_COLUMNS = {
    "clause_document_expression": (
        "normalization_run_id", "expression_id", "clause_work_id",
        "source_lane", "source_run_id", "source_version_id",
        "effective_from", "expression_completeness", "reader_state",
        "exact_text", "exact_text_sha256", "composition_manifest_sha256",
        "completeness_receipt_sha256", "source_row_sha256",
    ),
    "clause_document_expression_relation": (
        "normalization_run_id", "relation_id", "clause_work_id",
        "older_expression_id", "newer_expression_id", "relation_status",
        "relation_basis", "decision_lane", "evidence_receipt",
        "evidence_receipt_sha256", "source_row_sha256",
    ),
    "clause_document_source_block": (
        "normalization_run_id", "expression_id", "block_order",
        "source_block_id", "source_artifact_sha256", "source_lane",
        "container", "raw_text", "raw_text_sha256", "scalar_length",
        "utf8_byte_length", "source_locator", "render_locator",
        "source_row_sha256",
    ),
    "clause_document_node": (
        "normalization_run_id", "expression_id", "node_id",
        "parent_node_id", "tree_preorder", "sibling_ordinal",
        "hierarchy_depth", "akn_element", "structural_role",
        "marker_raw", "marker_scheme", "item_ordinal",
        "marker_scalar_end", "marker_utf8_byte_end", "exact_text",
        "exact_text_sha256", "content_text", "content_text_sha256",
        "structure_status", "derived_work_node_key",
        "node_structure_sha256", "source_row_sha256",
    ),
    "clause_document_node_identity": (
        "normalization_run_id", "expression_id", "node_id",
        "node_work_id", "identity_resolution_status", "identity_basis",
        "decision_lane", "evidence_receipt_sha256", "source_row_sha256",
    ),
    "clause_document_table": (
        "normalization_run_id", "expression_id", "node_id", "table_id",
        "table_index", "table_role", "renderer_profile",
        "logical_value_policy_version", "row_count", "column_count",
        "header_row_count", "table_structure_sha256", "source_row_sha256",
    ),
    "clause_document_table_row": (
        "normalization_run_id", "expression_id", "table_id", "row_index",
        "row_role", "row_signature_sha256", "row_structure_sha256",
        "source_row_sha256",
    ),
    "clause_document_table_cell": (
        "normalization_run_id", "expression_id", "table_id", "row_index",
        "cell_index", "physical_state", "logical_value_state",
        "cell_role", "row_span", "column_span", "value_origin_row_index",
        "value_origin_cell_index", "physical_text",
        "physical_text_sha256", "logical_value_text",
        "logical_value_sha256", "carry_policy_receipt_sha256",
        "source_content_count", "source_row_sha256",
    ),
    "clause_document_table_cell_content": (
        "normalization_run_id", "expression_id", "table_id", "row_index",
        "cell_index", "content_order", "structural_kind", "marker_raw",
        "marker_scheme", "item_ordinal", "exact_text",
        "exact_text_sha256", "content_text", "content_text_sha256",
        "structure_status", "source_row_sha256",
    ),
    "clause_document_source_span": (
        "normalization_run_id", "expression_id", "span_id", "block_order",
        "span_order_in_block", "owner_kind", "node_id", "table_id",
        "row_index", "cell_index", "content_order", "scalar_start",
        "scalar_end", "utf8_byte_start", "utf8_byte_end", "mapping_role",
        "exact_span_text", "exact_span_text_sha256", "source_row_sha256",
    ),
    "clause_document_normalization_receipt": (
        "normalization_run_id", "source_expression_count",
        "expected_counts", "table_fingerprints",
        "source_reconstruction_sha256", "structure_manifest_sha256",
        "output_fingerprint", "source_row_sha256",
    ),
}
_DIFF_TABLE_COLUMNS = {
    "clause_document_node_lineage": (
        "diff_run_id", "lineage_id", "older_expression_id",
        "older_node_id", "newer_expression_id", "newer_node_id",
        "lineage_kind", "alignment_status", "alignment_basis",
        "source_row_sha256",
    ),
    "clause_document_diff_hunk": (
        "diff_run_id", "hunk_id", "hunk_order", "older_expression_id",
        "newer_expression_id", "older_node_id", "newer_node_id",
        "alignment_status", "exact_change_kind", "display_classification",
        "comparison_label", "old_exact_text", "new_exact_text",
        "old_exact_text_sha256", "new_exact_text_sha256",
        "table_alignment", "suppressed_display_segment_count",
        "source_row_sha256",
    ),
    "clause_document_inline_diff_segment": (
        "diff_run_id", "hunk_id", "segment_order", "segment_kind",
        "old_text", "new_text", "old_scalar_start", "old_scalar_end",
        "new_scalar_start", "new_scalar_end", "old_utf8_byte_start",
        "old_utf8_byte_end", "new_utf8_byte_start", "new_utf8_byte_end",
        "display_state", "display_reason", "source_row_sha256",
    ),
}
_JSON_COLUMNS = {
    "unresolved_scope", "unprocessed_event_scope", "source_locator",
    "render_locator", "inline_segments", "ignored_change_policy",
    "outcome_codes", "operand", "expected_counts", "table_fingerprints",
    "evidence_receipt", "table_alignment",
}


def _insert_rows(cursor: Any, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = (
        _TABLE_COLUMNS
        | _IDENTITY_TABLE_COLUMNS
        | _NORMALIZATION_TABLE_COLUMNS
        | _DIFF_TABLE_COLUMNS
    )[table]
    placeholders = [
        "%s::jsonb" if column in _JSON_COLUMNS else "%s"
        for column in columns
    ]
    sql = (
        f"INSERT INTO {SCHEMA}.{table} ({','.join(columns)}) "
        f"VALUES ({','.join(placeholders)})"
    )
    params = []
    for row in rows:
        params.append(
            tuple(
                (
                    None
                    if column in _JSON_COLUMNS and row[column] is None
                    else (
                        json_text(row[column])
                        if column in _JSON_COLUMNS
                        else row[column]
                    )
                )
                for column in columns
            )
        )
    cursor.executemany(sql, params)


def _known_c10_products(connection: Any) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT drug_code, name_zh, name_en, atc_code
            FROM tw_drug.nhi_drugs
            WHERE upper(coalesce(atc_code,'')) LIKE 'C10%'
            ORDER BY drug_code
            """
        )
        return [
            {
                "drug_code": row[0],
                "name_zh": row[1],
                "name_en": row[2],
                "atc_code": row[3],
            }
            for row in cursor.fetchall()
        ]


def _current_predecessor(connection: Any) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT run_id, clause_code, raw_text, raw_text_sha256,
                   source_artifact_sha256, source_url, source_label
            FROM nhi_rule_history_publication.v_current_clause
            WHERE clause_code='2.6.1'
            """
        )
        clause = cursor.fetchone()
        if not clause:
            raise AnnouncedDyslipidemiaError(
                "current 2.6.1 predecessor is unavailable"
            )
        cursor.execute(
            """
            SELECT block_order, source_block_id, block_kind, container,
                   raw_text, raw_text_sha256, source_locator
            FROM nhi_rule_history_publication.v_current_clause_block
            WHERE run_id=%s AND clause_code='2.6.1'
            ORDER BY block_order
            """,
            (clause[0],),
        )
        blocks = [
            {
                "block_order": row[0],
                "source_block_id": row[1],
                "block_kind": row[2],
                "container": row[3],
                "raw_text": row[4],
                "raw_text_sha256": row[5],
                "source_locator": row[6],
            }
            for row in cursor.fetchall()
        ]
    return {
        "run_id": str(clause[0]),
        "clause_code": clause[1],
        "raw_text": clause[2],
        "raw_text_sha256": clause[3],
        "source_artifact_sha256": clause[4],
        "source_url": clause[5],
        "source_label": clause[6],
        "blocks": blocks,
    }


def _active_announced_source(
    connection: Any,
) -> dict[str, str] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT version.run_id,version.version_id,
                   version.composed_text_sha256
            FROM {SCHEMA}.v_public_composed_clause_version version
            JOIN {SCHEMA}.v_active_run active
              ON active.run_id=version.run_id
            JOIN {SCHEMA}.notice_event notice
              ON notice.run_id=version.run_id
            WHERE version.clause_code='2.6.1'
              AND notice.source_artifact_sha256=%s
            """,
            (EXPECTED_ARTIFACT_SHA256,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "run_id": str(row[0]),
        "version_id": str(row[1]),
        "composed_text_sha256": str(row[2]),
    }


def _active_terminology_projection(connection: Any) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tagging_run_id, output_fingerprint, sealed_fingerprint,
                   matcher_version, offset_contract, alias_admission_policy
            FROM nhi_rule_history_terminology.v_active_tagging_run
            """
        )
        run = cursor.fetchone()
        if run is None:
            raise AnnouncedDyslipidemiaError(
                "active sealed terminology run is unavailable"
            )
        cursor.execute(
            """
            SELECT alias_id, concept_id, normalized_alias,
                   production_status, match_rule
            FROM nhi_rule_history_terminology.concept_alias
            WHERE tagging_run_id=%s
            ORDER BY length(normalized_alias) DESC,
                     normalized_alias, concept_id, alias_id
            """,
            (run[0],),
        )
        aliases = [
            {
                "alias_id": str(row[0]),
                "concept_id": str(row[1]),
                "normalized_alias": str(row[2]),
                "production_status": str(row[3]),
                "match_rule": str(row[4]),
            }
            for row in cursor.fetchall()
        ]
    if not aliases:
        raise AnnouncedDyslipidemiaError(
            "active terminology run has no aliases"
        )
    if (
        str(run[3]) != MATCHER_VERSION
        or str(run[4]) != OFFSET_CONTRACT
        or str(run[5]) != ALIAS_ADMISSION_POLICY
    ):
        raise AnnouncedDyslipidemiaError(
            "active terminology policy differs from announced-version matcher"
        )
    return {
        "tagging_run_id": str(run[0]),
        "output_fingerprint": str(run[1]),
        "sealed_fingerprint": str(run[2]),
        "matcher_version": str(run[3]),
        "offset_contract": str(run[4]),
        "alias_admission_policy": str(run[5]),
        "aliases": aliases,
    }


def _apply_migration(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('nhi_rule_history_announced.release_run')"
        )
        if cursor.fetchone()[0] is None:
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT to_regclass("
            "'nhi_rule_history_announced.release_control_event'"
            ")"
        )
        if cursor.fetchone()[0] is None:
            cursor.execute(RELEASE_GATE_MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT to_regclass("
            "'nhi_rule_history_announced.composed_clause_version'"
            ")"
        )
        if cursor.fetchone()[0] is None:
            cursor.execute(COMPOSITION_MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT to_regclass("
            "'nhi_rule_history_announced."
            "composed_clause_terminology_occurrence'"
            ")"
        )
        if cursor.fetchone()[0] is None:
            cursor.execute(
                VERSION_PROJECTION_MIGRATION.read_text(encoding="utf-8")
            )
        cursor.execute(
            "SELECT to_regclass("
            "'nhi_rule_history_announced."
            "clause_document_normalization_run'"
            ")"
        )
        if cursor.fetchone()[0] is None:
            cursor.execute(
                DOCUMENT_COMPONENT_MIGRATION.read_text(encoding="utf-8")
            )


def _insert_material(connection: Any, material: AnnouncedMaterial) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("nhi-rule-history-announced-global",),
        )
        core_already_loaded = material.source_release_run_id != material.run_id
        if core_already_loaded:
            cursor.execute(
                f"""
                SELECT run.state, version.composed_text_sha256
                FROM {SCHEMA}.release_run run
                JOIN {SCHEMA}.composed_clause_version version
                  ON version.run_id = run.run_id
                 AND version.version_id = %s
                WHERE run.run_id = %s
                """,
                (
                    material.version_id,
                    material.source_release_run_id,
                ),
            )
            source_release = cursor.fetchone()
            if (
                source_release is None
                or source_release[0] != "sealed"
                or source_release[1]
                != material.rows["composed_clause_version"][0][
                    "composed_text_sha256"
                ]
            ):
                raise AnnouncedDyslipidemiaError(
                    "existing announced source release does not match "
                    "the normalization input"
                )
        else:
            cursor.execute(
                f"SELECT run_id,sealed_fingerprint "
                f"FROM {SCHEMA}.release_run WHERE input_fingerprint=%s",
                (material.input_fingerprint,),
            )
            existing = cursor.fetchone()
            if existing:
                if (
                    str(existing[0]) != material.run_id
                    or existing[1] != material.sealed_fingerprint
                ):
                    raise AnnouncedDyslipidemiaError(
                        "announced release input collision or loader drift"
                    )
                core_already_loaded = True
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.release_run (
                      run_id,state,loader_version,evaluator_version,
                      source_artifact_sha256,input_fingerprint,
                      expected_counts,started_at
                    ) VALUES (%s,'loading',%s,%s,%s,%s,%s::jsonb,now())
                    """,
                    (
                        material.run_id,
                        LOADER_VERSION,
                        EVALUATOR_VERSION,
                        EXPECTED_ARTIFACT_SHA256,
                        material.input_fingerprint,
                        json_text(material.expected_counts),
                    ),
                )
                for table in _TABLE_COLUMNS:
                    _insert_rows(cursor, table, material.rows[table])
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.release_run
                    SET state='sealed', verified_counts=%s::jsonb,
                        table_fingerprints=%s::jsonb,
                        output_fingerprint=%s, sealed_fingerprint=%s,
                        sealed_at=now()
                    WHERE run_id=%s AND state='loading'
                    """,
                    (
                        json_text(material.expected_counts),
                        json_text(material.table_fingerprints),
                        material.output_fingerprint,
                        material.sealed_fingerprint,
                        material.run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AnnouncedDyslipidemiaError(
                        "announced release seal failed"
                    )

        for table in _IDENTITY_TABLE_COLUMNS:
            for row in material.identity_rows[table]:
                primary_key = (
                    "clause_work_id"
                    if table == "clause_document_work"
                    else "node_work_id"
                )
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    f"WHERE {primary_key}=%s",
                    (row[primary_key],),
                )
                identity = cursor.fetchone()
                if identity is None:
                    _insert_rows(cursor, table, (row,))
                elif identity[0] != row["source_row_sha256"]:
                    raise AnnouncedDyslipidemiaError(
                        "persistent clause document identity drifted"
                    )

        cursor.execute(
            f"""
            SELECT state,input_fingerprint,sealed_fingerprint
            FROM {SCHEMA}.clause_document_normalization_run
            WHERE normalization_run_id=%s
            """,
            (material.normalization_run_id,),
        )
        normalization_existing = cursor.fetchone()
        if normalization_existing is None:
            cursor.execute(
                f"""
                INSERT INTO {SCHEMA}.clause_document_normalization_run (
                  normalization_run_id,source_release_run_id,state,
                  parser_version,rules_version,migration_sha256,
                  source_input_fingerprint,input_fingerprint,
                  expected_counts,started_at
                ) VALUES (
                  %s,%s,'loading',%s,%s,%s,%s,%s,%s::jsonb,now()
                )
                """,
                (
                    material.normalization_run_id,
                    material.source_release_run_id,
                    DOCUMENT_NORMALIZATION_VERSION,
                    DOCUMENT_NORMALIZATION_VERSION,
                    migration_fingerprint(DOCUMENT_COMPONENT_MIGRATION),
                    material.input_fingerprint,
                    material.normalization_input_fingerprint,
                    json_text(material.normalization_expected_counts),
                ),
            )
            for table in _NORMALIZATION_TABLE_COLUMNS:
                projection_rows = (
                    (material.normalization_receipt,)
                    if table == "clause_document_normalization_receipt"
                    else material.normalization_rows[table]
                )
                _insert_rows(cursor, table, projection_rows)
            cursor.execute(
                f"""
                UPDATE {SCHEMA}.clause_document_normalization_run
                SET state='sealed',verified_counts=%s::jsonb,
                    table_fingerprints=%s::jsonb,
                    output_fingerprint=%s,sealed_fingerprint=%s,
                    sealed_at=now()
                WHERE normalization_run_id=%s AND state='loading'
                """,
                (
                    json_text(material.normalization_expected_counts),
                    json_text(
                        material.normalization_table_fingerprints
                    ),
                    material.normalization_output_fingerprint,
                    material.normalization_sealed_fingerprint,
                    material.normalization_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AnnouncedDyslipidemiaError(
                    "clause document normalization seal failed"
                )
        elif (
            normalization_existing[0] != "sealed"
            or normalization_existing[1]
            != material.normalization_input_fingerprint
            or normalization_existing[2]
            != material.normalization_sealed_fingerprint
        ):
            raise AnnouncedDyslipidemiaError(
                "clause document normalization run drifted"
            )

        cursor.execute(
            f"""
            SELECT state,input_fingerprint,sealed_fingerprint
            FROM {SCHEMA}.clause_document_diff_run
            WHERE diff_run_id=%s
            """,
            (material.diff_run_id,),
        )
        diff_existing = cursor.fetchone()
        if diff_existing is None:
            cursor.execute(
                f"""
                INSERT INTO {SCHEMA}.clause_document_diff_run (
                  diff_run_id,normalization_run_id,relation_id,state,
                  alignment_version,algorithm_version,tokenizer_version,
                  tie_break_version,unicode_profile,display_policy_version,
                  input_fingerprint,expected_counts,started_at
                ) VALUES (
                  %s,%s,%s,'loading',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now()
                )
                """,
                (
                    material.diff_run_id,
                    material.normalization_run_id,
                    material.expression_relation_id,
                    DOCUMENT_ALIGNMENT_VERSION,
                    EXACT_DIFF_ALGORITHM_VERSION,
                    EXACT_DIFF_TOKENIZER_VERSION,
                    EXACT_DIFF_TIE_BREAK_VERSION,
                    EXACT_DIFF_UNICODE_PROFILE,
                    DIFF_DISPLAY_POLICY_VERSION,
                    material.diff_input_fingerprint,
                    json_text(material.diff_expected_counts),
                ),
            )
            for table in _DIFF_TABLE_COLUMNS:
                _insert_rows(cursor, table, material.diff_rows[table])
            cursor.execute(
                f"""
                UPDATE {SCHEMA}.clause_document_diff_run
                SET state='sealed',verified_counts=%s::jsonb,
                    table_fingerprints=%s::jsonb,
                    output_fingerprint=%s,sealed_fingerprint=%s,
                    sealed_at=now()
                WHERE diff_run_id=%s AND state='loading'
                """,
                (
                    json_text(material.diff_expected_counts),
                    json_text(material.diff_table_fingerprints),
                    material.diff_output_fingerprint,
                    material.diff_sealed_fingerprint,
                    material.diff_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AnnouncedDyslipidemiaError(
                    "clause document diff seal failed"
                )
        elif (
            diff_existing[0] != "sealed"
            or diff_existing[1] != material.diff_input_fingerprint
            or diff_existing[2] != material.diff_sealed_fingerprint
        ):
            raise AnnouncedDyslipidemiaError(
                "clause document diff run drifted"
            )
    return core_already_loaded


def _legacy_verify_announced_material_v24(
    run_id: str,
    *,
    conninfo: str,
    connect: Callable[[str], Any] | None = None,
    expected: AnnouncedMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    with connector(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state,expected_counts,verified_counts,
                       table_fingerprints,output_fingerprint,sealed_fingerprint
                FROM {SCHEMA}.release_run WHERE run_id=%s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if not run or run[0] != "sealed":
                raise AnnouncedDyslipidemiaError("fresh verification found no sealed run")
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in _TABLE_COLUMNS:
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    "WHERE run_id=%s ORDER BY source_row_sha256",
                    (run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT version_id, normalization_version,
                       source_block_count, component_count, table_count,
                       structure_manifest_sha256, expected_counts,
                       table_fingerprints, document_output_fingerprint
                FROM {SCHEMA}.composed_clause_document_receipt
                WHERE run_id=%s
                """,
                (run_id,),
            )
            document_receipt = cursor.fetchone()
            if document_receipt is None:
                raise AnnouncedDyslipidemiaError(
                    "fresh clause document receipt is unavailable"
                )
            document_counts: dict[str, int] = {}
            document_fingerprints: dict[str, str] = {}
            for table in _DOCUMENT_TABLE_COLUMNS:
                if table == "composed_clause_document_receipt":
                    continue
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    "WHERE run_id=%s ORDER BY source_row_sha256",
                    (run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                document_counts[table] = len(hashes)
                document_fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT count(*),
                       count(*) FILTER (WHERE component_kind='table'),
                       min(component_order), max(component_order)
                FROM {SCHEMA}.composed_clause_component
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, document_receipt[0]),
            )
            component_shape = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*), count(DISTINCT block_order),
                       min(block_order), max(block_order)
                FROM {SCHEMA}.composed_clause_component_block
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, document_receipt[0]),
            )
            component_block_shape = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*) FILTER (WHERE cell_state='source'),
                       count(*) FILTER (WHERE cell_state='covered'),
                       count(*) FILTER (
                         WHERE cell_state='implicit_carry'
                       ),
                       count(*) FILTER (WHERE cell_state='empty')
                FROM {SCHEMA}.composed_clause_table_cell
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, document_receipt[0]),
            )
            cell_state_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.composed_clause_table table_row
                WHERE table_row.run_id=%s
                  AND table_row.version_id=%s
                  AND (
                    (
                      SELECT count(*)
                      FROM {SCHEMA}.composed_clause_table_row row_data
                      WHERE row_data.run_id=table_row.run_id
                        AND row_data.version_id=table_row.version_id
                        AND row_data.table_id=table_row.table_id
                    ) <> table_row.row_count
                    OR
                    (
                      SELECT count(*)
                      FROM {SCHEMA}.composed_clause_table_cell cell
                      WHERE cell.run_id=table_row.run_id
                        AND cell.version_id=table_row.version_id
                        AND cell.table_id=table_row.table_id
                    ) <> table_row.row_count * table_row.column_count
                  )
                """,
                (run_id, document_receipt[0]),
            )
            non_rectangular_table_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*) FILTER (WHERE rule_lane='table2'),
                       count(*) FILTER (WHERE rule_lane='table1')
                FROM {SCHEMA}.model_product_code WHERE run_id=%s
                """,
                (run_id,),
            )
            product_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT version_id, composed_text_sha256,
                       amendment_block_count, inherited_block_count,
                       composition_rule_version
                FROM {SCHEMA}.composed_clause_version
                WHERE run_id=%s AND clause_code='2.6.1'
                """,
                (run_id,),
            )
            composed = cursor.fetchone()
            if composed is None:
                raise AnnouncedDyslipidemiaError(
                    "fresh composed clause version is unavailable"
                )
            cursor.execute(
                f"""
                SELECT count(*) FILTER (
                         WHERE origin_lane='amendment_exact'
                       ),
                       count(*) FILTER (
                         WHERE origin_lane='predecessor_inherited'
                       ),
                       count(*)
                FROM {SCHEMA}.composed_clause_block
                WHERE run_id=%s
                """,
                (run_id,),
            )
            composed_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*) FILTER (
                         WHERE applicability_lane='table2_exception'
                       ),
                       count(*) FILTER (
                         WHERE applicability_lane='table1_default'
                       )
                FROM {SCHEMA}.composed_clause_reimbursement_code
                WHERE run_id=%s
                """,
                (run_id,),
            )
            code_link_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*),
                       count(*) FILTER (
                         WHERE scan_status='scanned_with_match'
                       )
                FROM {SCHEMA}.composed_clause_tagging_block_input
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, composed[0]),
            )
            tagging_block_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*) FILTER (
                         WHERE occurrence_status='admitted'
                       ),
                       count(*) FILTER (
                         WHERE occurrence_status='candidate'
                       ),
                       count(*) FILTER (
                         WHERE occurrence_status='blocked'
                       )
                FROM {SCHEMA}.composed_clause_terminology_occurrence
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, composed[0]),
            )
            terminology_counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.composed_clause_diff_hunk
                WHERE run_id=%s AND version_id=%s
                """,
                (run_id, composed[0]),
            )
            diff_hunk_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                WITH inherited_announced AS (
                  SELECT occurrence.source_block_id,
                         occurrence.source_block_sha256,
                         occurrence.concept_id, occurrence.alias_id,
                         occurrence.start_scalar, occurrence.end_scalar,
                         occurrence.matched_text
                  FROM {SCHEMA}.composed_clause_terminology_occurrence
                    occurrence
                  JOIN {SCHEMA}.composed_clause_block block
                    ON (block.run_id, block.version_id, block.block_order) =
                       (occurrence.run_id, occurrence.version_id,
                        occurrence.block_order)
                  WHERE occurrence.run_id=%s
                    AND occurrence.version_id=%s
                    AND occurrence.occurrence_status='admitted'
                    AND block.origin_lane='predecessor_inherited'
                ),
                current_source AS (
                  SELECT occurrence.source_block_id,
                         occurrence.source_block_sha256,
                         occurrence.concept_id, occurrence.alias_id,
                         occurrence.start_scalar, occurrence.end_scalar,
                         occurrence.matched_text
                  FROM
                    nhi_rule_history_terminology
                      .v_admitted_clause_occurrence occurrence
                  WHERE occurrence.clause_code='2.6.1'
                    AND occurrence.source_block_id IN (
                      SELECT block.source_block_id
                      FROM {SCHEMA}.composed_clause_block block
                      WHERE block.run_id=%s
                        AND block.version_id=%s
                        AND block.origin_lane='predecessor_inherited'
                    )
                )
                SELECT (
                  SELECT count(*) FROM (
                    (SELECT * FROM inherited_announced
                     EXCEPT SELECT * FROM current_source)
                    UNION ALL
                    (SELECT * FROM current_source
                     EXCEPT SELECT * FROM inherited_announced)
                  ) mismatch
                )::integer
                """,
                (run_id, composed[0], run_id, composed[0]),
            )
            inherited_terminology_mismatch_count = int(
                cursor.fetchone()[0]
            )
    document_output = object_fingerprint(
        {
            "counts": document_counts,
            "table_fingerprints": document_fingerprints,
            "structure_manifest_sha256": document_receipt[5],
        }
    )
    output = object_fingerprint(
        {
            "core": {
                "counts": counts,
                "table_fingerprints": fingerprints,
            },
            "document": {
                "counts": document_counts,
                "table_fingerprints": document_fingerprints,
                "structure_manifest_sha256": document_receipt[5],
            },
        }
    )
    if (
        counts != run[1]
        or counts != run[2]
        or fingerprints != run[3]
        or output != run[4]
    ):
        raise AnnouncedDyslipidemiaError("sealed announced receipt does not replay")
    if (
        document_counts != document_receipt[6]
        or document_fingerprints != document_receipt[7]
        or document_output != document_receipt[8]
    ):
        raise AnnouncedDyslipidemiaError(
            "sealed clause document receipt does not replay"
        )
    if expected and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or document_counts != expected.document_expected_counts
        or document_fingerprints
        != expected.document_table_fingerprints
        or document_receipt[5] != expected.document_structure_sha256
        or output != expected.output_fingerprint
        or run[5] != expected.sealed_fingerprint
    ):
        raise AnnouncedDyslipidemiaError("fresh announced data differs from prepared material")
    if int(product_counts[0]) != 116:
        raise AnnouncedDyslipidemiaError("fresh Table-2 code count is not 116")
    if not composed or (
        int(composed[2]),
        int(composed[3]),
        composed[4],
    ) != (336, 70, COMPOSITION_RULE_VERSION):
        raise AnnouncedDyslipidemiaError(
            "fresh composed clause version is incomplete"
        )
    if tuple(int(value) for value in composed_counts) != (336, 70, 406):
        raise AnnouncedDyslipidemiaError(
            "fresh composed clause block coverage is not 336+70"
        )
    if int(code_link_counts[0]) != 116:
        raise AnnouncedDyslipidemiaError(
            "fresh Table-2 reimbursement-code link count is not 116"
        )
    if int(code_link_counts[1]) + int(code_link_counts[0]) != sum(
        int(value) for value in product_counts
    ):
        raise AnnouncedDyslipidemiaError(
            "fresh reimbursement-code links do not cover all products"
        )
    if int(tagging_block_counts[0]) != 406:
        raise AnnouncedDyslipidemiaError(
            "fresh terminology scan does not cover all composed blocks"
        )
    if int(terminology_counts[0]) < 1:
        raise AnnouncedDyslipidemiaError(
            "fresh composed clause has no admitted terminology occurrences"
        )
    if inherited_terminology_mismatch_count:
        raise AnnouncedDyslipidemiaError(
            "inherited composed blocks differ from current terminology scan"
        )
    if diff_hunk_count < 1:
        raise AnnouncedDyslipidemiaError(
            "fresh composed clause has no adjacent diff hunks"
        )
    if tuple(int(value) for value in component_shape[:2]) != (
        int(document_receipt[3]),
        int(document_receipt[4]),
    ):
        raise AnnouncedDyslipidemiaError(
            "fresh clause component or table count is inconsistent"
        )
    if tuple(int(value) for value in component_block_shape) != (
        int(document_receipt[2]),
        int(document_receipt[2]),
        0,
        int(document_receipt[2]) - 1,
    ):
        raise AnnouncedDyslipidemiaError(
            "fresh clause components do not conserve all source blocks"
        )
    if non_rectangular_table_count:
        raise AnnouncedDyslipidemiaError(
            "fresh normalized clause table is not rectangular"
        )
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "document_counts": document_counts,
        "document_table_fingerprints": document_fingerprints,
        "document_structure_sha256": document_receipt[5],
        "document_output_fingerprint": document_output,
        "output_fingerprint": output,
        "sealed_fingerprint": run[5],
        "table2_product_count": int(product_counts[0]),
        "table1_product_count": int(product_counts[1]),
        "version_id": str(composed[0]),
        "composed_text_sha256": composed[1],
        "composed_block_count": int(composed_counts[2]),
        "reimbursement_code_link_count": sum(
            int(value) for value in code_link_counts
        ),
        "tagged_block_count": int(tagging_block_counts[0]),
        "tagged_block_with_match_count": int(tagging_block_counts[1]),
        "terminology_occurrence_counts": {
            "admitted": int(terminology_counts[0]),
            "candidate": int(terminology_counts[1]),
            "blocked": int(terminology_counts[2]),
        },
        "diff_hunk_count": diff_hunk_count,
        "component_count": int(document_receipt[3]),
        "table_count": int(document_receipt[4]),
        "has_table": int(document_receipt[4]) > 0,
        "table_cell_state_counts": {
            "source": int(cell_state_counts[0]),
            "covered": int(cell_state_counts[1]),
            "implicit_carry": int(cell_state_counts[2]),
            "empty": int(cell_state_counts[3]),
        },
        "inherited_terminology_mismatch_count": (
            inherited_terminology_mismatch_count
        ),
    }


def verify_announced_material(
    run_id: str,
    *,
    conninfo: str,
    connect: Callable[[str], Any] | None = None,
    expected: AnnouncedMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    with connector(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state,expected_counts,verified_counts,
                       table_fingerprints,output_fingerprint,
                       sealed_fingerprint,input_fingerprint,
                       loader_version,evaluator_version
                FROM {SCHEMA}.release_run
                WHERE run_id=%s
                """,
                (run_id,),
            )
            release = cursor.fetchone()
            if release is None or release[0] != "sealed":
                raise AnnouncedDyslipidemiaError(
                    "fresh verification found no sealed source release"
                )
            core_counts: dict[str, int] = {}
            core_fingerprints: dict[str, str] = {}
            for table in _TABLE_COLUMNS:
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    "WHERE run_id=%s ORDER BY source_row_sha256",
                    (run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                core_counts[table] = len(hashes)
                core_fingerprints[table] = row_set_fingerprint(hashes)
            core_output = object_fingerprint(
                {
                    "counts": core_counts,
                    "table_fingerprints": core_fingerprints,
                }
            )
            stored_core_seal = object_fingerprint(
                {
                    "run_id": run_id,
                    "input_fingerprint": release[6],
                    "output_fingerprint": release[4],
                    "loader_version": release[7],
                    "evaluator_version": release[8],
                }
            )
            if (
                core_counts != release[1]
                or core_counts != release[2]
                or core_fingerprints != release[3]
                or stored_core_seal != release[5]
            ):
                raise AnnouncedDyslipidemiaError(
                    "sealed source release receipt does not replay"
                )
            if expected is None:
                cursor.execute(
                    f"""
                    SELECT normalization_run_id
                    FROM {SCHEMA}.clause_document_normalization_run
                    WHERE source_release_run_id=%s AND state='sealed'
                    ORDER BY sealed_at DESC
                    LIMIT 1
                    """,
                    (run_id,),
                )
                normalization_row = cursor.fetchone()
                if normalization_row is None:
                    raise AnnouncedDyslipidemiaError(
                        "sealed clause normalization is unavailable"
                    )
                normalization_run_id = str(normalization_row[0])
            else:
                normalization_run_id = expected.normalization_run_id
            cursor.execute(
                f"""
                SELECT state,expected_counts,verified_counts,
                       table_fingerprints,output_fingerprint,
                       sealed_fingerprint
                FROM {SCHEMA}.clause_document_normalization_run
                WHERE normalization_run_id=%s
                  AND source_release_run_id=%s
                """,
                (normalization_run_id, run_id),
            )
            normalization = cursor.fetchone()
            if normalization is None or normalization[0] != "sealed":
                raise AnnouncedDyslipidemiaError(
                    "fresh verification found no sealed normalization run"
                )
            normalization_counts: dict[str, int] = {}
            normalization_fingerprints: dict[str, str] = {}
            for table in _NORMALIZATION_TABLE_COLUMNS:
                if table == "clause_document_normalization_receipt":
                    continue
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    "WHERE normalization_run_id=%s "
                    "ORDER BY source_row_sha256",
                    (normalization_run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                normalization_counts[table] = len(hashes)
                normalization_fingerprints[table] = (
                    row_set_fingerprint(hashes)
                )
            cursor.execute(
                f"""
                SELECT source_expression_count,expected_counts,
                       table_fingerprints,source_reconstruction_sha256,
                       structure_manifest_sha256,output_fingerprint
                FROM {SCHEMA}.clause_document_normalization_receipt
                WHERE normalization_run_id=%s
                """,
                (normalization_run_id,),
            )
            normalization_receipt = cursor.fetchone()
            if normalization_receipt is None:
                raise AnnouncedDyslipidemiaError(
                    "normalization receipt is unavailable"
                )
            normalization_output = object_fingerprint(
                {
                    "counts": normalization_counts,
                    "table_fingerprints": normalization_fingerprints,
                    "source_reconstruction_sha256": (
                        normalization_receipt[3]
                    ),
                    "structure_manifest_sha256": (
                        normalization_receipt[4]
                    ),
                }
            )
            if (
                normalization_counts != normalization[1]
                or normalization_counts != normalization[2]
                or normalization_fingerprints != normalization[3]
                or normalization_output != normalization[4]
                or normalization_counts != normalization_receipt[1]
                or normalization_fingerprints != normalization_receipt[2]
                or normalization_output != normalization_receipt[5]
            ):
                raise AnnouncedDyslipidemiaError(
                    "sealed normalization receipt does not replay"
                )
            cursor.execute(
                f"""
                WITH rebuilt AS (
                  SELECT block.normalization_run_id,block.expression_id,
                         string_agg(block.raw_text,E'\\n\\n'
                                    ORDER BY block.block_order) AS exact_text
                  FROM {SCHEMA}.clause_document_source_block block
                  WHERE block.normalization_run_id=%s
                  GROUP BY block.normalization_run_id,block.expression_id
                )
                SELECT count(*)
                FROM rebuilt
                JOIN {SCHEMA}.clause_document_expression expression
                  ON expression.normalization_run_id =
                     rebuilt.normalization_run_id
                 AND expression.expression_id = rebuilt.expression_id
                WHERE rebuilt.exact_text IS DISTINCT FROM expression.exact_text
                """,
                (normalization_run_id,),
            )
            source_reconstruction_mismatches = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT physical_state,logical_value_state,count(*)
                FROM {SCHEMA}.clause_document_table_cell
                WHERE normalization_run_id=%s
                GROUP BY physical_state,logical_value_state
                ORDER BY physical_state,logical_value_state
                """,
                (normalization_run_id,),
            )
            cell_state_counts = {
                f"{row[0]}/{row[1]}": int(row[2])
                for row in cursor.fetchall()
            }
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.clause_document_table table_row
                WHERE table_row.normalization_run_id=%s
                  AND (
                    (
                      SELECT count(*)
                      FROM {SCHEMA}.clause_document_table_row row_data
                      WHERE row_data.normalization_run_id =
                            table_row.normalization_run_id
                        AND row_data.expression_id =
                            table_row.expression_id
                        AND row_data.table_id = table_row.table_id
                    ) <> table_row.row_count
                    OR
                    (
                      SELECT count(*)
                      FROM {SCHEMA}.clause_document_table_cell cell
                      WHERE cell.normalization_run_id =
                            table_row.normalization_run_id
                        AND cell.expression_id = table_row.expression_id
                        AND cell.table_id = table_row.table_id
                    ) <> table_row.row_count * table_row.column_count
                  )
                """,
                (normalization_run_id,),
            )
            non_rectangular_tables = int(cursor.fetchone()[0])
            if expected is None:
                cursor.execute(
                    f"""
                    SELECT diff_run_id
                    FROM {SCHEMA}.clause_document_diff_run
                    WHERE normalization_run_id=%s AND state='sealed'
                    ORDER BY sealed_at DESC
                    LIMIT 1
                    """,
                    (normalization_run_id,),
                )
                diff_row = cursor.fetchone()
                if diff_row is None:
                    raise AnnouncedDyslipidemiaError(
                        "sealed exact diff is unavailable"
                    )
                diff_run_id = str(diff_row[0])
            else:
                diff_run_id = expected.diff_run_id
            cursor.execute(
                f"""
                SELECT state,expected_counts,verified_counts,
                       table_fingerprints,output_fingerprint,
                       sealed_fingerprint
                FROM {SCHEMA}.clause_document_diff_run
                WHERE diff_run_id=%s AND normalization_run_id=%s
                """,
                (diff_run_id, normalization_run_id),
            )
            diff = cursor.fetchone()
            if diff is None or diff[0] != "sealed":
                raise AnnouncedDyslipidemiaError(
                    "fresh verification found no sealed exact diff"
                )
            diff_counts: dict[str, int] = {}
            diff_fingerprints: dict[str, str] = {}
            for table in _DIFF_TABLE_COLUMNS:
                cursor.execute(
                    f"SELECT source_row_sha256 FROM {SCHEMA}.{table} "
                    "WHERE diff_run_id=%s ORDER BY source_row_sha256",
                    (diff_run_id,),
                )
                hashes = [str(row[0]) for row in cursor.fetchall()]
                diff_counts[table] = len(hashes)
                diff_fingerprints[table] = row_set_fingerprint(hashes)
            diff_output = object_fingerprint(
                {
                    "counts": diff_counts,
                    "table_fingerprints": diff_fingerprints,
                }
            )
            if (
                diff_counts != diff[1]
                or diff_counts != diff[2]
                or diff_fingerprints != diff[3]
                or diff_output != diff[4]
            ):
                raise AnnouncedDyslipidemiaError(
                    "sealed exact diff receipt does not replay"
                )
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.clause_document_diff_hunk hunk
                LEFT JOIN LATERAL (
                  SELECT string_agg(coalesce(segment.old_text,''),''
                                    ORDER BY segment.segment_order) AS old_text,
                         string_agg(coalesce(segment.new_text,''),''
                                    ORDER BY segment.segment_order) AS new_text
                  FROM {SCHEMA}.clause_document_inline_diff_segment segment
                  WHERE segment.diff_run_id=hunk.diff_run_id
                    AND segment.hunk_id=hunk.hunk_id
                ) replay ON true
                WHERE hunk.diff_run_id=%s
                  AND (
                    replay.old_text IS DISTINCT FROM
                      coalesce(hunk.old_exact_text,'')
                    OR replay.new_text IS DISTINCT FROM
                      coalesce(hunk.new_exact_text,'')
                  )
                """,
                (diff_run_id,),
            )
            diff_reconstruction_mismatches = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*) FILTER (
                         WHERE applicability_lane='table2_exception'
                       ),
                       count(*) FILTER (
                         WHERE applicability_lane='table1_default'
                       )
                FROM {SCHEMA}.composed_clause_reimbursement_code
                WHERE run_id=%s
                """,
                (run_id,),
            )
            reimbursement_counts = tuple(
                int(value) for value in cursor.fetchone()
            )
            cursor.execute(
                f"""
                SELECT count(*) FILTER (
                         WHERE occurrence_status='admitted'
                       ),
                       count(*) FILTER (
                         WHERE occurrence_status='candidate'
                       ),
                       count(*) FILTER (
                         WHERE occurrence_status='blocked'
                       )
                FROM {SCHEMA}.composed_clause_terminology_occurrence
                WHERE run_id=%s
                """,
                (run_id,),
            )
            terminology_counts = tuple(
                int(value) for value in cursor.fetchone()
            )
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.clause_document_node
                WHERE normalization_run_id=%s
                """,
                (normalization_run_id,),
            )
            node_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {SCHEMA}.clause_document_table
                WHERE normalization_run_id=%s
                """,
                (normalization_run_id,),
            )
            table_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT normalization_run_id
                FROM {SCHEMA}.v_active_clause_document_normalization_run
                """
            )
            active_normalization = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT diff_run_id
                FROM {SCHEMA}.v_active_clause_document_diff_run
                """
            )
            active_diff = cursor.fetchone()
    if source_reconstruction_mismatches:
        raise AnnouncedDyslipidemiaError(
            "source blocks do not reconstruct exact expressions"
        )
    if non_rectangular_tables:
        raise AnnouncedDyslipidemiaError(
            "fresh normalized table is not rectangular"
        )
    if diff_reconstruction_mismatches:
        raise AnnouncedDyslipidemiaError(
            "fresh inline diff does not reconstruct both exact sides"
        )
    if expected is not None:
        if (
            normalization_counts
            != expected.normalization_expected_counts
            or normalization_fingerprints
            != expected.normalization_table_fingerprints
            or normalization[4]
            != expected.normalization_output_fingerprint
            or normalization[5]
            != expected.normalization_sealed_fingerprint
            or diff_counts != expected.diff_expected_counts
            or diff_fingerprints != expected.diff_table_fingerprints
            or diff[4] != expected.diff_output_fingerprint
            or diff[5] != expected.diff_sealed_fingerprint
        ):
            raise AnnouncedDyslipidemiaError(
                "fresh clause document data differs from prepared material"
            )
        if (
            expected.source_release_run_id == expected.run_id
            and (
                core_counts != expected.expected_counts
                or core_fingerprints != expected.table_fingerprints
                or core_output != expected.output_fingerprint
                or release[5] != expected.sealed_fingerprint
            )
        ):
            raise AnnouncedDyslipidemiaError(
                "fresh source release differs from prepared material"
            )
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": core_counts,
        "table_fingerprints": core_fingerprints,
        "output_fingerprint": release[4],
        "core_replay_fingerprint": core_output,
        "sealed_fingerprint": release[5],
        "normalization_run_id": normalization_run_id,
        "normalization_counts": normalization_counts,
        "normalization_table_fingerprints": (
            normalization_fingerprints
        ),
        "normalization_output_fingerprint": normalization[4],
        "normalization_sealed_fingerprint": normalization[5],
        "diff_run_id": diff_run_id,
        "diff_counts": diff_counts,
        "diff_table_fingerprints": diff_fingerprints,
        "diff_output_fingerprint": diff[4],
        "diff_sealed_fingerprint": diff[5],
        "source_expression_count": int(normalization_receipt[0]),
        "document_structure_sha256": normalization_receipt[4],
        "node_count": node_count,
        "table_count": table_count,
        "has_table": table_count > 0,
        "table_cell_state_counts": cell_state_counts,
        "reimbursement_code_link_count": sum(reimbursement_counts),
        "table2_product_count": reimbursement_counts[0],
        "table1_product_count": reimbursement_counts[1],
        "terminology_occurrence_counts": {
            "admitted": terminology_counts[0],
            "candidate": terminology_counts[1],
            "blocked": terminology_counts[2],
        },
        "active_normalization": (
            active_normalization is not None
            and str(active_normalization[0]) == normalization_run_id
        ),
        "active_diff": (
            active_diff is not None
            and str(active_diff[0]) == diff_run_id
        ),
    }


def load_announced_dyslipidemia(
    odt_path: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        _apply_migration(connection)
    with connector(dsn) as connection:
        known_products = _known_c10_products(connection)
        predecessor = _current_predecessor(connection)
        terminology_projection = _active_terminology_projection(connection)
        active_announced_source = _active_announced_source(connection)
    material = prepare_announced_material(
        Path(odt_path),
        known_products=known_products,
        predecessor=predecessor,
        terminology_projection=terminology_projection,
        source_release_run_id=(
            active_announced_source["run_id"]
            if active_announced_source
            else None
        ),
        source_version_id=(
            active_announced_source["version_id"]
            if active_announced_source
            else None
        ),
    )
    with connector(dsn) as connection:
        already_loaded = _insert_material(connection, material)
        if activate:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT 1 FROM {SCHEMA}.patch_resolution_event
                    WHERE run_id=%s AND patch_id=%s
                    LIMIT 1
                    """,
                    (material.source_release_run_id, material.patch_id),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.set_patch_resolution(
                          %s, %s, 'verified_scheduled', %s, %s::jsonb
                        )
                        """,
                        (
                            material.source_release_run_id,
                            material.patch_id,
                            "official source verified before stated effective date",
                            json_text(
                                {
                                    "loader_version": LOADER_VERSION,
                                    "source_artifact_sha256": (
                                        EXPECTED_ARTIFACT_SHA256
                                    ),
                                }
                            ),
                        ),
                    )
                cursor.execute(f"SELECT run_id FROM {SCHEMA}.v_active_run")
                active = cursor.fetchone()
                if (
                    not active
                    or str(active[0]) != material.source_release_run_id
                ):
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.set_release_control(
                          %s, 'activate', %s, %s::jsonb
                        )
                        """,
                        (
                            material.source_release_run_id,
                            "announced dyslipidemia loader activation",
                            json_text(
                                {
                                    "loader_version": LOADER_VERSION,
                                    "sealed_fingerprint": (
                                        material.sealed_fingerprint
                                    ),
                                }
                            ),
                        ),
                    )
                cursor.execute(
                    f"""
                    SELECT normalization_run_id
                    FROM {SCHEMA}.v_active_clause_document_normalization_run
                    """
                )
                active_normalization = cursor.fetchone()
                if (
                    active_normalization is None
                    or str(active_normalization[0])
                    != material.normalization_run_id
                ):
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.
                          set_clause_document_normalization_control(
                            %s,'activate',%s,%s::jsonb
                          )
                        """,
                        (
                            material.normalization_run_id,
                            "activate sealed clause normalization projection",
                            json_text(
                                {
                                    "normalization_sealed_fingerprint": (
                                        material
                                        .normalization_sealed_fingerprint
                                    ),
                                    "source_release_run_id": (
                                        material.source_release_run_id
                                    ),
                                }
                            ),
                        ),
                    )
                cursor.execute(
                    f"""
                    SELECT diff_run_id
                    FROM {SCHEMA}.v_active_clause_document_diff_run
                    """
                )
                active_diff = cursor.fetchone()
                if (
                    active_diff is None
                    or str(active_diff[0]) != material.diff_run_id
                ):
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.set_clause_document_diff_control(
                          %s,'activate',%s,%s::jsonb
                        )
                        """,
                        (
                            material.diff_run_id,
                            "activate sealed exact clause diff projection",
                            json_text(
                                {
                                    "diff_sealed_fingerprint": (
                                        material.diff_sealed_fingerprint
                                    ),
                                    "normalization_run_id": (
                                        material.normalization_run_id
                                    ),
                                }
                            ),
                        ),
                    )
    result = verify_announced_material(
        material.source_release_run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result.update(
        {
            "notice_id": material.notice_id,
            "patch_id": material.patch_id,
            "version_id": material.version_id,
            "normalization_run_id": material.normalization_run_id,
            "diff_run_id": material.diff_run_id,
            "model_id": material.model_id,
            "already_loaded": already_loaded,
            "active": activate,
            "effective_on": EFFECTIVE_DATE,
            "resolution_state": "verified_scheduled",
            "legally_auto_selectable": False,
        }
    )
    return result
