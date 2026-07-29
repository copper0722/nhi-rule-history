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
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

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
from nhi_rule_history.update.odt import inspect_odt_document


SCHEMA = "nhi_rule_history_announced"
LOADER_VERSION = "nhi-rule-history/announced-dyslipidemia-loader/1.2.1"
EVALUATOR_VERSION = "nhi-rule-history/table1-open-world-dnf/1.1.0"
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
_UUID_NAMESPACE = uuid.UUID("90f6ded1-5025-4938-9e68-fcdfdc349c1c")
_TABLE2_CODE_RE = __import__("re").compile(r"^[A-Z0-9]{10}$")


class AnnouncedDyslipidemiaError(PgLoadError):
    """The official patch or decision model failed a closed invariant."""


@dataclass(frozen=True)
class AnnouncedMaterial:
    run_id: str
    notice_id: str
    patch_id: str
    version_id: str
    model_id: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
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


def prepare_announced_material(
    odt_path: Path,
    *,
    known_products: Sequence[Mapping[str, Any]],
    predecessor: Mapping[str, Any],
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
        {"counts": expected_counts, "table_fingerprints": table_fingerprints}
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
        rows=frozen_rows,
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
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
_JSON_COLUMNS = {
    "unresolved_scope", "unprocessed_event_scope", "source_locator",
    "render_locator",
    "outcome_codes", "operand",
}


def _insert_rows(cursor: Any, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = _TABLE_COLUMNS[table]
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
                json_text(row[column]) if column in _JSON_COLUMNS else row[column]
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


def _insert_material(connection: Any, material: AnnouncedMaterial) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("nhi-rule-history-announced-global",),
        )
        cursor.execute(
            f"SELECT run_id,sealed_fingerprint FROM {SCHEMA}.release_run "
            "WHERE input_fingerprint=%s",
            (material.input_fingerprint,),
        )
        existing = cursor.fetchone()
        if existing:
            if str(existing[0]) != material.run_id or existing[1] != material.sealed_fingerprint:
                raise AnnouncedDyslipidemiaError(
                    "announced release input collision or loader drift"
                )
            return True
        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.release_run (
              run_id,state,loader_version,evaluator_version,
              source_artifact_sha256,input_fingerprint,expected_counts,started_at
            ) VALUES (%s,'loading',%s,%s,%s,%s,%s::jsonb,now())
            """,
            (
                material.run_id, LOADER_VERSION, EVALUATOR_VERSION,
                EXPECTED_ARTIFACT_SHA256, material.input_fingerprint,
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
                output_fingerprint=%s, sealed_fingerprint=%s, sealed_at=now()
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
            raise AnnouncedDyslipidemiaError("announced release seal failed")
    return False


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
    output = object_fingerprint(
        {"counts": counts, "table_fingerprints": fingerprints}
    )
    if (
        counts != run[1]
        or counts != run[2]
        or fingerprints != run[3]
        or output != run[4]
    ):
        raise AnnouncedDyslipidemiaError("sealed announced receipt does not replay")
    if expected and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
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
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
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
    material = prepare_announced_material(
        Path(odt_path),
        known_products=known_products,
        predecessor=predecessor,
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
                    (material.run_id, material.patch_id),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.set_patch_resolution(
                          %s, %s, 'verified_scheduled', %s, %s::jsonb
                        )
                        """,
                        (
                            material.run_id,
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
                if not active or str(active[0]) != material.run_id:
                    cursor.execute(
                        f"""
                        SELECT {SCHEMA}.set_release_control(
                          %s, 'activate', %s, %s::jsonb
                        )
                        """,
                        (
                            material.run_id,
                            "announced dyslipidemia loader activation",
                            json_text(
                                {
                                    "loader_version": LOADER_VERSION,
                                    "sealed_fingerprint": material.sealed_fingerprint,
                                }
                            ),
                        ),
                    )
    result = verify_announced_material(
        material.run_id, conninfo=dsn, connect=connector, expected=material
    )
    result.update(
        {
            "notice_id": material.notice_id,
            "patch_id": material.patch_id,
            "version_id": material.version_id,
            "model_id": material.model_id,
            "already_loaded": already_loaded,
            "active": activate,
            "effective_on": EFFECTIVE_DATE,
            "resolution_state": "verified_scheduled",
            "legally_auto_selectable": False,
        }
    )
    return result
