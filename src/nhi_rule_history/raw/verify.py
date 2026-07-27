from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ContractError,
    RAW_MANIFEST_SCHEMA,
    file_sha256,
    unique_rows,
)
from nhi_rule_history.raw.store import RawStore


def verify_raw(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "raw-manifest.json"
    if not manifest_path.is_file():
        raise ContractError("raw-manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError("raw-manifest.json is invalid JSON") from exc
    if manifest.get("schema") != RAW_MANIFEST_SCHEMA:
        raise ContractError("raw manifest schema mismatch")

    for entry in manifest.get("files", []):
        path = run_dir / entry["filename"]
        if not path.is_file():
            raise ContractError(f"manifested file missing: {entry['filename']}")
        if path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
            raise ContractError(f"manifested file changed: {entry['filename']}")

    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    links = unique_rows(run_dir / "resource-artifact-links.jsonl", "link_id")
    attempts = unique_rows(run_dir / "fetch-attempts.jsonl", "attempt_id")
    url_observations = unique_rows(
        run_dir / "artifact-url-observations.jsonl", "url_observation_id"
    )
    store = RawStore(run_dir)

    for digest, artifact in artifacts.items():
        if digest != artifact["artifact_sha256"]:
            raise ContractError("raw artifact key mismatch")
        if not store.verify(
            artifact["content_path"], digest, artifact["byte_size"]
        ):
            raise ContractError(f"raw blob verification failed: {digest}")

    linked_resources: set[str] = set()
    for link in links.values():
        if link["resource_id"] not in resources:
            raise ContractError("resource-artifact link has unknown resource")
        if link["artifact_sha256"] not in artifacts:
            raise ContractError("resource-artifact link has unknown artifact")
        if not any(
            attempt.get("status") == "success"
            and attempt.get("resource_id") == link["resource_id"]
            and attempt.get("artifact_sha256") == link["artifact_sha256"]
            for attempt in attempts.values()
        ):
            raise ContractError("resource-artifact link lacks successful fetch attempt")
        if not any(
            observation.get("resource_id") == link["resource_id"]
            and observation.get("artifact_sha256") == link["artifact_sha256"]
            for observation in url_observations.values()
        ):
            raise ContractError("resource-artifact link lacks URL observation")
        linked_resources.add(link["resource_id"])
    missing = sorted(set(resources) - linked_resources)
    if missing:
        raise ContractError(f"resources without raw artifacts: {len(missing)}")

    for attempt in attempts.values():
        if attempt["resource_id"] not in resources:
            raise ContractError("fetch attempt has unknown resource")
        if attempt["status"] == "success":
            if attempt["artifact_sha256"] not in artifacts:
                raise ContractError("successful fetch references unknown artifact")
    for observation in url_observations.values():
        if observation["resource_id"] not in resources:
            raise ContractError("URL observation has unknown resource")
        if observation["artifact_sha256"] not in artifacts:
            raise ContractError("URL observation has unknown artifact")
        previous = observation.get("previous_artifact_sha256")
        if previous and previous not in artifacts:
            raise ContractError("URL observation previous artifact is unknown")
        if (
            observation["relation_to_previous"] == "same_url_different_bytes"
            and previous == observation["artifact_sha256"]
        ):
            raise ContractError("different-bytes relation points to identical hash")

    expected_counts = manifest["counts"]
    actual_counts = {
        "resources": len(resources),
        "artifacts": len(artifacts),
        "resource_artifact_links": len(links),
        "artifact_bytes": sum(row["byte_size"] for row in artifacts.values()),
    }
    if expected_counts != actual_counts:
        raise ContractError("raw manifest counts do not match files")
    return {
        "status": "passed",
        "counts": actual_counts,
        "same_url_different_bytes": sum(
            row["relation_to_previous"] == "same_url_different_bytes"
            for row in url_observations.values()
        ),
    }
