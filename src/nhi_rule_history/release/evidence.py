"""Prepare deterministic v2 raw/structural evidence assets without publishing."""

from __future__ import annotations

import json
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any, Iterable

from nhi_rule_history.contracts import (
    RAW_MANIFEST_SCHEMA,
    SourcePlan,
    canonical_url,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    stable_id,
)
from nhi_rule_history.parsers.odt import (
    NON_CLAIM,
    STRUCTURAL_FILES,
    STRUCTURAL_MANIFEST_SCHEMA,
)
from nhi_rule_history.raw.verify import verify_raw
from nhi_rule_history.release.prepare import (
    PrepareError,
    _compress,
    _decompressed_sha256,
)


def _deterministic_tar(
    destination: Path,
    members: Iterable[tuple[str, Path]],
) -> str:
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for archive_name, source in sorted(members, key=lambda item: item[0]):
            if not source.is_file():
                raise PrepareError(f"v2 release member is missing: {source.name}")
            info = tarfile.TarInfo(archive_name)
            info.size = source.stat().st_size
            info.mode = 0o644
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with source.open("rb") as stream:
                archive.addfile(info, stream)
    return file_sha256(destination)


def _verified_structural_manifest(stage_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(
            (stage_dir / "structural-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("structural manifest is missing or invalid") from exc
    if (
        manifest.get("schema") != STRUCTURAL_MANIFEST_SCHEMA
        or manifest.get("status") != "passed"
        or manifest.get("counts", {}).get("blocking_issues") != 0
    ):
        raise PrepareError("structural manifest did not pass")
    expected = {
        row["filename"]: row
        for row in manifest.get("files", [])
        if isinstance(row, dict) and isinstance(row.get("filename"), str)
    }
    for filename in STRUCTURAL_FILES:
        source = stage_dir / filename
        row = expected.get(filename)
        if (
            row is None
            or not source.is_file()
            or source.stat().st_size != row.get("bytes")
            or file_sha256(source) != row.get("sha256")
        ):
            raise PrepareError(f"structural input verification failed: {filename}")
    return manifest


def _verified_release_binding(
    *,
    run_dir: Path,
    source_plan: Path,
    eligibility_receipt: Path,
    structural: dict[str, Any],
    raw_verification: dict[str, Any],
) -> tuple[SourcePlan, str, dict[str, Any]]:
    plan = SourcePlan.load(source_plan)
    raw_manifest_path = run_dir / "raw-manifest.json"
    try:
        raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("raw manifest is missing or invalid") from exc
    if (
        raw_manifest.get("schema") != RAW_MANIFEST_SCHEMA
        or raw_manifest.get("status") != "success"
    ):
        raise PrepareError("raw manifest did not pass")

    raw_manifest_sha256 = file_sha256(raw_manifest_path)
    if raw_manifest.get("source_plan_sha256") != plan.sha256:
        raise PrepareError("source plan does not match the raw manifest")
    if raw_manifest.get("capture_cut") != plan.capture_cut.isoformat():
        raise PrepareError("source plan capture cut does not match the raw manifest")
    if structural.get("raw_manifest_sha256") != raw_manifest_sha256:
        raise PrepareError("structural stage does not match the raw manifest")
    if structural.get("statement") != NON_CLAIM:
        raise PrepareError("structural non-claim does not match the release contract")
    if structural.get("raw_verification", {}).get("counts") != raw_verification.get(
        "counts"
    ):
        raise PrepareError("structural/raw verification counts do not match")

    parity_path = run_dir / "discovery-parity.json"
    try:
        parity = json.loads(parity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("discovery parity receipt is missing or invalid") from exc
    if (
        parity.get("schema") != "nhi-rule-history/discovery-parity/v2"
        or parity.get("status") != "passed"
        or parity.get("missing_from_b") != []
        or parity.get("new_in_b") != []
    ):
        raise PrepareError("discovery parity receipt did not pass")
    if (
        parity.get("source_plan_sha256") != plan.sha256
        or parity.get("capture_cut") != plan.capture_cut.isoformat()
    ):
        raise PrepareError("discovery parity does not match the source plan")
    if parity.get("resource_count") != raw_verification.get("counts", {}).get(
        "resources"
    ):
        raise PrepareError("discovery parity resource count does not match raw data")

    try:
        eligibility = json.loads(eligibility_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("release eligibility receipt is missing or invalid") from exc
    if (
        eligibility.get("schema")
        != "nhi-rule-history/release-eligibility-receipt/v1"
        or eligibility.get("status") != "reviewed_bounded_evidence_only"
        or eligibility.get("legal_history_claim") is not False
        or eligibility.get("portable_dataset_release_ready") is not False
    ):
        raise PrepareError("release eligibility receipt has an invalid contract")
    if eligibility.get("source_plan_sha256") != plan.sha256:
        raise PrepareError("release eligibility receipt does not match the source plan")

    acquisition = eligibility.get("corrected_acquisition", {})
    structural_receipt = eligibility.get("corrected_structural", {})
    if (
        acquisition.get("raw_manifest_sha256") != raw_manifest_sha256
        or acquisition.get("release_eligible") is not True
        or acquisition.get("default_selector_eligible") is not True
    ):
        raise PrepareError("corrected acquisition is not release eligible")
    if (
        structural_receipt.get("parse_run_id") != structural.get("parse_run_id")
        or structural_receipt.get("input_fingerprint")
        != structural.get("input_fingerprint")
    ):
        raise PrepareError("structural receipt does not match the structural bundle")
    excluded = eligibility.get("superseded_runs")
    if not isinstance(excluded, list) or not excluded:
        raise PrepareError("superseded-run exclusion receipt is missing")
    for row in excluded:
        if (
            row.get("release_eligible") is not False
            or row.get("default_selector_eligible") is not False
            or row.get("superseded_by") != acquisition.get("run_id")
        ):
            raise PrepareError("superseded-run exclusion is incomplete")
        if row.get("run_id") == acquisition.get("run_id"):
            raise PrepareError("corrected acquisition is also marked superseded")

    resources = list(iter_jsonl(run_dir / "discovered-resources.jsonl"))
    details = [
        row for row in resources if row["resource_kind"] == "official_detail_page"
    ]
    attachments = [
        row for row in resources if row["resource_kind"] == "official_attachment"
    ]
    for row in details:
        normalized_number = re.sub(
            r"\s+", "", row.get("official_document_number_raw", "")
        )
        if row["resource_id"] != stable_id(
            "fint-detail", row["adapter_id"], normalized_number
        ):
            raise PrepareError("detail resource identity is not authority-native")
    for row in attachments:
        if row["resource_id"] != stable_id(
            "fint-attachment",
            row["adapter_id"],
            canonical_url(row["source_url"]),
        ):
            raise PrepareError("attachment resource identity is not authority-native")
    collision_counts = {
        "detail_rows": len(details),
        "distinct_detail_resource_keys": len(
            {row["resource_id"] for row in details}
        ),
        "distinct_normalized_formal_document_numbers": len(
            {
                re.sub(r"\s+", "", row["official_document_number_raw"])
                for row in details
            }
        ),
        "detail_normalization_collisions": (
            len(details) - len({row["resource_id"] for row in details})
        ),
        "attachment_rows": len(attachments),
        "distinct_attachment_resource_keys": len(
            {row["resource_id"] for row in attachments}
        ),
        "distinct_canonical_attachment_urls": len(
            {canonical_url(row["source_url"]) for row in attachments}
        ),
        "ambiguous_attachment_identity_collisions": (
            len(attachments) - len({row["resource_id"] for row in attachments})
        ),
    }
    if eligibility.get("resource_identity_receipt") != collision_counts:
        raise PrepareError("resource identity collision receipt does not match raw data")
    if (
        collision_counts["detail_normalization_collisions"] != 0
        or collision_counts["ambiguous_attachment_identity_collisions"] != 0
    ):
        raise PrepareError("resource identity collision gate failed")
    return plan, raw_manifest_sha256, eligibility


def prepare_v2_evidence_release(
    *,
    run_dir: Path,
    stage_dir: Path,
    source_plan: Path,
    eligibility_receipt: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create local checksummed assets. No code path performs publication."""

    if output_dir.exists():
        raise PrepareError(f"release output already exists: {output_dir}")
    raw_verification = verify_raw(run_dir)
    structural = _verified_structural_manifest(stage_dir)
    plan, raw_manifest_sha256, eligibility = _verified_release_binding(
        run_dir=run_dir,
        source_plan=source_plan,
        eligibility_receipt=eligibility_receipt,
        structural=structural,
        raw_verification=raw_verification,
    )
    output_dir.mkdir(parents=True)
    try:
        acquisition_names = (
            "discovery-observations.jsonl",
            "discovered-resources.jsonl",
            "fetch-attempts.jsonl",
            "raw-artifacts.jsonl",
            "resource-artifact-links.jsonl",
            "artifact-url-observations.jsonl",
            "issues.jsonl",
            "discovery-manifest.json",
            "raw-manifest.json",
        )
        members = [
            (f"acquisition/{name}", run_dir / name)
            for name in acquisition_names
        ]
        members.append(
            (
                "acquisition/discovery-parity.json",
                run_dir / "discovery-parity.json",
            )
        )
        members.append(("source-plan-v2.json", source_plan))
        members.append(("release-eligibility.json", eligibility_receipt))
        members.extend(
            (f"raw/sha256/{path.parent.name}/{path.name}", path)
            for path in (run_dir / "raw" / "sha256").glob("*/*")
            if path.is_file()
        )

        tar_path = output_dir / "nhi-rule-history-v2-raw.tar"
        tar_sha = _deterministic_tar(tar_path, members)
        raw_asset = output_dir / "nhi-rule-history-v2-raw.tar.zst"
        compression = _compress(tar_path, raw_asset)
        if compression is None:
            raise PrepareError("zstd is required for the v2 raw release bundle")
        if _decompressed_sha256(raw_asset, compression) != tar_sha:
            raise PrepareError("v2 raw bundle decompressed checksum mismatch")
        tar_path.unlink()

        assets: dict[str, dict[str, Any]] = {
            raw_asset.name: {
                "bytes": raw_asset.stat().st_size,
                "sha256": file_sha256(raw_asset),
                "source_tar_sha256": tar_sha,
                "compression": compression,
            }
        }
        entry_by_name = {row["filename"]: row for row in structural["files"]}
        count_key = {
            "structural-blocks.jsonl": "structural_blocks",
            "occurrence-candidates.jsonl": "occurrence_candidates",
            "parse-issues.jsonl": "parse_issues",
        }
        for filename in STRUCTURAL_FILES:
            source = stage_dir / filename
            destination = output_dir / f"{filename}.zst"
            mode = _compress(source, destination)
            if mode is None:
                raise PrepareError("zstd is required for structural assets")
            expected_sha = entry_by_name[filename]["sha256"]
            if _decompressed_sha256(destination, mode) != expected_sha:
                raise PrepareError(f"{filename}: decompressed checksum mismatch")
            assets[destination.name] = {
                "bytes": destination.stat().st_size,
                "sha256": file_sha256(destination),
                "source_sha256": expected_sha,
                "row_count": structural["counts"][count_key[filename]],
                "compression": mode,
            }

        manifest_copy = output_dir / "structural-manifest.json"
        shutil.copyfile(stage_dir / "structural-manifest.json", manifest_copy)
        assets[manifest_copy.name] = {
            "bytes": manifest_copy.stat().st_size,
            "sha256": file_sha256(manifest_copy),
            "compression": "none",
        }
        release = {
            "schema": "nhi-rule-history-v2-evidence-release-preparation/v1",
            "status": "prepared_partial_evidence_bundle_not_published",
            "publication_performed": False,
            "acquisition_run_id": eligibility["corrected_acquisition"]["run_id"],
            "acquisition_sealed_fingerprint": eligibility[
                "corrected_acquisition"
            ]["sealed_fingerprint"],
            "parse_run_id": structural["parse_run_id"],
            "structural_sealed_fingerprint": eligibility["corrected_structural"][
                "sealed_fingerprint"
            ],
            "source_plan_sha256": plan.sha256,
            "capture_cut": plan.capture_cut.isoformat(),
            "capture_window": eligibility["capture_window"],
            "raw_manifest_sha256": raw_manifest_sha256,
            "structural_input_fingerprint": structural["input_fingerprint"],
            "release_eligibility_receipt_sha256": file_sha256(
                eligibility_receipt
            ),
            "legal_history_claim": False,
            "scope_statement": NON_CLAIM,
            "counts": {
                "acquisition": raw_verification["counts"],
                "structural": structural["counts"],
            },
            "assets": assets,
            "verification": {
                "discovery_key_set_parity": "passed",
                "raw_offline_verification": "passed",
                "structural_manifest_verification": "passed",
                "zstd_decompressed_checksums": "passed",
                "network_publication": "not_performed",
                "postgresql_sealed_fingerprints": "bound_by_reviewed_receipt",
                "sqlite_v2_projection": "not_implemented",
            },
        }
        (output_dir / "release-manifest.json").write_bytes(
            canonical_json_bytes(release)
        )
        return release
    except Exception:
        for path in output_dir.glob("*"):
            if path.is_file():
                path.unlink()
        output_dir.rmdir()
        raise
