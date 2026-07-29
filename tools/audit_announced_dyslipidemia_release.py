#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


SCHEMA = "nhi_rule_history_announced"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _predicate_fixture(predicate: dict[str, Any], state: str) -> dict[str, Any]:
    operator = predicate["operator"]
    key = predicate["input_key"]
    operand = predicate["operand"]
    if operator == "is_true":
        return {key: {"true": True, "false": False, "unknown": None}[state]}
    if operator == "is_false":
        return {key: {"true": False, "false": True, "unknown": None}[state]}
    if operator == "gte":
        number = float(operand)
        return {key: {"true": number, "false": number - 1, "unknown": None}[state]}
    if operator == "lt":
        number = float(operand)
        return {key: {"true": number - 1, "false": number, "unknown": None}[state]}
    members = list(operand.get("members", []))
    derived = list(operand.get("derived_members", []))
    facts = {member: False for member in members}
    for item in derived:
        facts.update({member: False for member in item.get("members", [])})
    target = int(operand["target"])
    if operator == "aggregate_gte":
        if state == "true":
            for member in members[:target]:
                facts[member] = True
        elif state == "unknown":
            for member in members[: max(0, target - 1)]:
                facts[member] = True
            facts[members[max(0, target - 1)]] = None
        return facts
    if operator == "aggregate_eq":
        if state == "true":
            for member in members[:target]:
                facts[member] = True
        elif state == "false":
            facts[members[0]] = True
            if target == 1:
                facts[members[1]] = True
        else:
            facts[members[0]] = None
        return facts
    raise AssertionError(f"unsupported operator {operator}")


def _branch_state(
    connection: psycopg.Connection[Any],
    predicates: list[dict[str, Any]],
    facts: dict[str, Any],
) -> int:
    state = 1
    for predicate in predicates:
        value = connection.execute(
            f"""
            SELECT {SCHEMA}.evaluate_predicate_v1(
              %s, %s, %s::jsonb, %s::jsonb
            )
            """,
            (
                predicate["operator"],
                predicate["input_key"],
                _json(predicate["operand"]),
                _json(facts),
            ),
        ).fetchone()[0]
        if value == 0:
            return 0
        if value == -1:
            state = -1
    return state


def _all_known_false(inputs: list[dict[str, Any]], product_code: str) -> dict[str, Any]:
    facts: dict[str, Any] = {"product_code": product_code, "ldl_c_mg_dl": 0}
    numeric_false = {
        "mi_history_count": 0,
        "plaque_stenosis_percent": 0,
        "uacr_mg_g": 0,
        "egfr_ml_min_1_73m2": 100,
        "ckd_duration_months": 0,
        "cac_score": 0,
    }
    for item in inputs:
        key = item["input_key"]
        if item["control_type"] == "tri_state":
            facts[key] = False
        elif key not in facts:
            facts[key] = numeric_false.get(key, 0)
    return facts


def _evaluate(
    connection: psycopg.Connection[Any],
    model_id: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return connection.execute(
        f"SELECT {SCHEMA}.evaluate_table1_v1(%s,%s::jsonb)",
        (model_id, _json(facts)),
    ).fetchone()[0]


def audit(conninfo: str, *, drill_release_control: bool) -> dict[str, Any]:
    with psycopg.connect(conninfo, row_factory=dict_row) as connection:
        release = connection.execute(f"SELECT * FROM {SCHEMA}.v_active_run").fetchone()
        if not release:
            raise RuntimeError("no active announced release")
        run_id = str(release["run_id"])
        patch = connection.execute(
            f"SELECT * FROM {SCHEMA}.v_public_clause_patch WHERE clause_code='2.6.1'"
        ).fetchone()
        if not patch:
            raise RuntimeError("2.6.1 public patch is missing")
        model = connection.execute(
            f"SELECT * FROM {SCHEMA}.v_public_decision_model WHERE patch_id=%s",
            (patch["patch_id"],),
        ).fetchone()
        if not model:
            raise RuntimeError("2.6.1 decision model is unavailable")
        model_id = str(model["model_id"])
        inputs = connection.execute(
            f"""
            SELECT * FROM {SCHEMA}.decision_input
            WHERE run_id=%s AND model_id=%s ORDER BY display_order,input_key
            """,
            (run_id, model_id),
        ).fetchall()
        categories = connection.execute(
            f"""
            SELECT * FROM {SCHEMA}.risk_category
            WHERE run_id=%s AND model_id=%s ORDER BY priority
            """,
            (run_id, model_id),
        ).fetchall()
        branches = connection.execute(
            f"""
            SELECT * FROM {SCHEMA}.risk_branch
            WHERE run_id=%s AND model_id=%s ORDER BY category_key,branch_order
            """,
            (run_id, model_id),
        ).fetchall()
        predicates = connection.execute(
            f"""
            SELECT predicate.*, component.source_block_id,
                   component.source_locator, component.raw_text,
                   component.raw_text_sha256,
                   notice.source_artifact_sha256
            FROM {SCHEMA}.risk_predicate predicate
            JOIN {SCHEMA}.decision_model model
              ON model.run_id=predicate.run_id
             AND model.model_id=predicate.model_id
            JOIN {SCHEMA}.patch_component component
              ON component.run_id=model.run_id
             AND component.patch_id=model.patch_id
             AND component.component_order=predicate.source_component_order
            JOIN {SCHEMA}.clause_patch clause
              ON clause.run_id=model.run_id AND clause.patch_id=model.patch_id
            JOIN {SCHEMA}.notice_effect effect
              ON effect.run_id=clause.run_id AND effect.effect_id=clause.effect_id
            JOIN {SCHEMA}.notice_event notice
              ON notice.run_id=effect.run_id AND notice.notice_id=effect.notice_id
            WHERE predicate.run_id=%s AND predicate.model_id=%s
            ORDER BY predicate.category_key,predicate.branch_key,
                     predicate.predicate_order
            """,
            (run_id, model_id),
        ).fetchall()
        if len(predicates) != 34:
            raise AssertionError(f"expected 34 predicates, found {len(predicates)}")
        source_receipts = []
        for predicate in predicates:
            raw_hash = hashlib.sha256(predicate["raw_text"].encode("utf-8")).hexdigest()
            if raw_hash != predicate["raw_text_sha256"]:
                raise AssertionError("predicate source component hash mismatch")
            source_receipts.append(
                {
                    "category_key": predicate["category_key"],
                    "branch_key": predicate["branch_key"],
                    "predicate_order": predicate["predicate_order"],
                    "operator": predicate["operator"],
                    "input_key": predicate["input_key"],
                    "source_component_order": predicate["source_component_order"],
                    "source_block_id": predicate["source_block_id"],
                    "source_locator": predicate["source_locator"],
                    "source_component_sha256": predicate["raw_text_sha256"],
                    "source_artifact_sha256": predicate["source_artifact_sha256"],
                }
            )

        by_branch: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for predicate in predicates:
            by_branch.setdefault(
                (predicate["category_key"], predicate["branch_key"]), []
            ).append(predicate)
        branch_receipts = []
        for branch in branches:
            key = (branch["category_key"], branch["branch_key"])
            branch_predicates = by_branch[key]
            true_facts: dict[str, Any] = {}
            for predicate in branch_predicates:
                true_facts.update(_predicate_fixture(predicate, "true"))
            fixtures = {"true": true_facts}
            for state in ("false", "unknown"):
                facts = dict(true_facts)
                facts.update(_predicate_fixture(branch_predicates[0], state))
                fixtures[state] = facts
            states = {
                name: _branch_state(connection, branch_predicates, facts)
                for name, facts in fixtures.items()
            }
            if states != {"true": 1, "false": 0, "unknown": -1}:
                raise AssertionError(f"branch fixture failed for {key}: {states}")
            branch_receipts.append(
                {
                    "category_key": key[0],
                    "branch_key": key[1],
                    "states": states,
                }
            )
        if len(branch_receipts) != 21:
            raise AssertionError("branch coverage is not 21/21")

        table1_code = connection.execute(
            f"""
            SELECT nhi_code FROM {SCHEMA}.model_product_code
            WHERE run_id=%s AND model_id=%s AND rule_lane='table1'
            ORDER BY nhi_code LIMIT 1
            """,
            (run_id, model_id),
        ).fetchone()["nhi_code"]
        base = _all_known_false(inputs, table1_code)

        priority_unknown_fixtures = [
            ("extreme", {"coronary_artery_disease": None, "mi_within_one_year": True}),
            ("very_high", {"acute_coronary_syndrome": None}),
            ("high", {"diabetes": None}),
            ("moderate", {"risk_hypertension": None, "risk_smoking": None}),
            ("low", {"risk_hypertension": None}),
        ]
        priority_receipts = []
        for category_key, additions in priority_unknown_fixtures:
            result = _evaluate(connection, model_id, {**base, **additions})
            if result.get("reason") != "higher_priority_path_unknown":
                raise AssertionError(f"priority unknown fixture failed: {category_key}")
            priority_receipts.append(
                {
                    "category_key": category_key,
                    "blocked_at_category": result.get("blocked_at_category"),
                }
            )
        zero_predicates = by_branch[("zero", "zero_risk_factors")]
        zero_state = _branch_state(
            connection,
            zero_predicates,
            _predicate_fixture(zero_predicates[0], "unknown"),
        )
        if zero_state != -1:
            raise AssertionError("zero-risk branch unknown fixture failed")
        priority_receipts.append(
            {
                "category_key": "zero",
                "branch_state": zero_state,
                "full_evaluator_note": (
                    "zero-risk unknown is structurally preceded by the same "
                    "one-risk aggregate unknown and cannot be the first blocker"
                ),
            }
        )

        feature_receipts: dict[str, Any] = {}
        feature_receipts["sex_specific_age_threshold"] = _evaluate(
            connection,
            model_id,
            {
                **base,
                "ldl_c_mg_dl": 115,
                "risk_age_threshold": True,
                "risk_hypertension": True,
            },
        )
        feature_receipts["low_hdl"] = _evaluate(
            connection,
            model_id,
            {**base, "ldl_c_mg_dl": 130, "risk_low_hdl": True},
        )
        feature_receipts["ckd_duration_2_months"] = _evaluate(
            connection,
            model_id,
            {
                **base,
                "ldl_c_mg_dl": 100,
                "predialysis_ckd": True,
                "ckd_duration_months": 2,
                "uacr_mg_g": 30,
            },
        )
        feature_receipts["ckd_duration_3_months"] = _evaluate(
            connection,
            model_id,
            {
                **base,
                "ldl_c_mg_dl": 100,
                "predialysis_ckd": True,
                "ckd_duration_months": 3,
                "uacr_mg_g": 30,
            },
        )
        metabolic_two = {
            **base,
            "ldl_c_mg_dl": 130,
            "metabolic_abdominal_obesity": True,
            "metabolic_bp": True,
        }
        feature_receipts["metabolic_2_of_5"] = _evaluate(
            connection, model_id, metabolic_two
        )
        feature_receipts["metabolic_3_of_5"] = _evaluate(
            connection,
            model_id,
            {**metabolic_two, "metabolic_glucose": True},
        )
        feature_receipts["contradictory_inputs"] = _evaluate(
            connection,
            model_id,
            {
                **base,
                "coronary_artery_disease": False,
                "mi_within_one_year": True,
            },
        )
        if feature_receipts["contradictory_inputs"].get("reason") != "contradictory_inputs":
            raise AssertionError("contradictory inputs did not fail closed")

        boundary_facts = {
            "extreme": {
                "coronary_artery_disease": True,
                "mi_within_one_year": True,
            },
            "very_high": {"acute_coronary_syndrome": True},
            "high": {"diabetes": True},
            "moderate": {"risk_hypertension": True, "risk_smoking": True},
            "low": {"risk_hypertension": True},
            "zero": {},
        }
        boundary_receipts = []
        for category in categories:
            threshold = int(category["ldl_threshold_mg_dl"])
            below = _evaluate(
                connection,
                model_id,
                {
                    **base,
                    **boundary_facts[category["category_key"]],
                    "ldl_c_mg_dl": threshold - 1,
                },
            )
            at = _evaluate(
                connection,
                model_id,
                {
                    **base,
                    **boundary_facts[category["category_key"]],
                    "ldl_c_mg_dl": threshold,
                },
            )
            if below.get("outcome") != "table1_threshold_not_met":
                raise AssertionError(f"below-boundary failure: {category['category_key']}")
            if at.get("outcome") != "table1_threshold_met":
                raise AssertionError(f"at-boundary failure: {category['category_key']}")
            boundary_receipts.append(
                {
                    "category_key": category["category_key"],
                    "threshold_mg_dl": threshold,
                    "below_outcome": below["outcome"],
                    "at_outcome": at["outcome"],
                }
            )
        scope_result = _evaluate(
            connection,
            model_id,
            {
                **base,
                "risk_hypertension": True,
                "ldl_c_mg_dl": 130,
            },
        )
        forbidden_output_keys = {
            "eligible",
            "coverage_approved",
            "recommendation",
            "lifestyle_requirement_met",
        }
        if forbidden_output_keys.intersection(scope_result):
            raise AssertionError("numeric threshold model exceeded its declared scope")

        release_drill = None
        if drill_release_control:
            counts_before = dict(release["verified_counts"])
            fingerprints_before = dict(release["table_fingerprints"])
            first_id = connection.execute(
                f"""
                SELECT {SCHEMA}.set_release_control(
                  %s,'deactivate',%s,%s::jsonb
                )
                """,
                (
                    run_id,
                    "R3 production rollback drill",
                    _json({"audit": "2026-07-29-dyslipidemia-r3"}),
                ),
            ).fetchone()[0]
            if connection.execute(
                f"SELECT count(*) AS n FROM {SCHEMA}.v_active_run"
            ).fetchone()["n"] != 0:
                raise AssertionError("deactivation did not remove the public run")
            if connection.execute(
                f"SELECT count(*) AS n FROM {SCHEMA}.v_public_clause_patch"
            ).fetchone()["n"] != 0:
                raise AssertionError("deactivation did not remove the public patch")
            raw_counts = {
                table: connection.execute(
                    f"SELECT count(*) AS n FROM {SCHEMA}.{table} WHERE run_id=%s",
                    (run_id,),
                ).fetchone()["n"]
                for table in counts_before
            }
            if raw_counts != counts_before:
                raise AssertionError("deactivation changed sealed evidence rows")
            second_id = connection.execute(
                f"""
                SELECT {SCHEMA}.set_release_control(
                  %s,'activate',%s,%s::jsonb
                )
                """,
                (
                    run_id,
                    "R3 production rollback drill restore",
                    _json(
                        {
                            "audit": "2026-07-29-dyslipidemia-r3",
                            "deactivation_control_id": first_id,
                        }
                    ),
                ),
            ).fetchone()[0]
            restored = connection.execute(
                f"SELECT * FROM {SCHEMA}.v_active_run"
            ).fetchone()
            if str(restored["run_id"]) != run_id:
                raise AssertionError("reactivation restored a different run")
            if dict(restored["table_fingerprints"]) != fingerprints_before:
                raise AssertionError("reactivation changed sealed fingerprints")
            release_drill = {
                "deactivation_control_id": first_id,
                "reactivation_control_id": second_id,
                "public_patch_count_while_deactivated": 0,
                "sealed_row_counts_unchanged": True,
                "table_fingerprints_unchanged": True,
                "restored_run_id": run_id,
                "restored_sealed_fingerprint": restored["sealed_fingerprint"],
            }

        return {
            "contract": "nhi-rule-history/announced-release-r3-audit/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "patch_id": str(patch["patch_id"]),
            "model_id": model_id,
            "sealed_fingerprint": release["sealed_fingerprint"],
            "release_gate": {
                "display_lifecycle": patch["display_lifecycle"],
                "current_resolution_state": patch["current_resolution_state"],
                "decision_aid_available": patch["decision_aid_available"],
                "legally_auto_selectable": patch["legally_auto_selectable"],
            },
            "source_round_trip": {
                "verified_predicate_count": len(source_receipts),
                "expected_predicate_count": 34,
                "receipts": source_receipts,
            },
            "branch_truth_table": {
                "verified_branch_count": len(branch_receipts),
                "expected_branch_count": 21,
                "receipts": branch_receipts,
            },
            "priority_unknown": priority_receipts,
            "feature_fixtures": feature_receipts,
            "ldl_boundaries": boundary_receipts,
            "scope_receipt": {
                "declared_scope": model["scope_label"],
                "forbidden_output_keys_absent": sorted(forbidden_output_keys),
                "sample_output_keys": sorted(scope_result),
            },
            "release_control_drill": release_drill,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--drill-release-control", action="store_true")
    args = parser.parse_args()
    receipt = audit(
        args.dsn,
        drill_release_control=args.drill_release_control,
    )
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
