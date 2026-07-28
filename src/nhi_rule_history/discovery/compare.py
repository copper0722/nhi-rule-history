"""Offline parity check for two independently enumerated discovery passes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    unique_rows,
)


def compare_discovery_runs(run_a: Path, run_b: Path) -> dict[str, Any]:
    manifests: list[dict[str, Any]] = []
    resources: list[dict[str, dict[str, Any]]] = []
    for label, directory in (("A", run_a), ("B", run_b)):
        manifest_path = directory / "discovery-manifest.json"
        if not manifest_path.is_file():
            raise ContractError(f"discovery pass {label} manifest is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContractError(
                f"discovery pass {label} manifest is invalid JSON"
            ) from exc
        if manifest.get("status") != "success":
            raise ContractError(f"discovery pass {label} is not successful")
        manifests.append(manifest)
        resources.append(
            unique_rows(directory / "discovered-resources.jsonl", "resource_id")
        )

    if manifests[0]["source_plan_sha256"] != manifests[1]["source_plan_sha256"]:
        raise ContractError("discovery passes used different source plans")
    if manifests[0]["capture_cut"] != manifests[1]["capture_cut"]:
        raise ContractError("discovery passes used different capture cuts")

    keys_a = set(resources[0])
    keys_b = set(resources[1])
    missing_from_b = sorted(keys_a - keys_b)
    new_in_b = sorted(keys_b - keys_a)
    if missing_from_b or new_in_b:
        raise ContractError(
            "discovery resource-key parity failed: "
            f"missing_from_b={len(missing_from_b)} new_in_b={len(new_in_b)}"
        )

    return {
        "schema": "nhi-rule-history/discovery-parity/v2",
        "status": "passed",
        "source_plan_sha256": manifests[0]["source_plan_sha256"],
        "capture_cut": manifests[0]["capture_cut"],
        "resource_count": len(keys_a),
        "resource_key_set_sha256": sha256_bytes(
            canonical_json_bytes(sorted(keys_a))
        ),
        "pass_a": {
            "manifest_sha256": file_sha256(run_a / "discovery-manifest.json"),
            "completed_at": manifests[0]["completed_at"],
        },
        "pass_b": {
            "manifest_sha256": file_sha256(run_b / "discovery-manifest.json"),
            "completed_at": manifests[1]["completed_at"],
        },
        "missing_from_b": [],
        "new_in_b": [],
        "statement": (
            "Two independent enumerations returned the same complete resource-key "
            "set for this bounded source plan and capture cut; this is not a legal-"
            "history completeness claim."
        ),
    }
