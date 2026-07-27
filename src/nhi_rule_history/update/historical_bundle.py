"""Deterministic offline bundles for historical MOHW FINT notices.

This layer consumes one fully verified v2 acquisition run.  It preserves the
exact official detail-page bytes and every declared child attachment without
assuming that an ODT exists or interpreting legal effect.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    RAW_ARTIFACT_SCHEMA,
    RAW_MANIFEST_SCHEMA,
    RESOURCE_ARTIFACT_LINK_SCHEMA,
    SourcePlan,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    relative_blob_path,
    resolve_run_relative,
    sha256_bytes,
    stable_id,
    unique_rows,
)
from nhi_rule_history.raw.verify import verify_raw


BUNDLE_SCHEMA = "nhi-rule-history/historical-notice-bundle/v1"
BUNDLE_FINGERPRINT_SCHEMA = (
    "nhi-rule-history/historical-notice-bundle-fingerprint/v1"
)
BATCH_SCHEMA = "nhi-rule-history/historical-notice-batch-index/v1"
BATCH_FINGERPRINT_SCHEMA = (
    "nhi-rule-history/historical-notice-batch-fingerprint/v1"
)
NON_CLAIM_STATEMENT = (
    "This bundle preserves one source-local official notice and its declared "
    "attachments. It does not establish legal effective dates, stable clause "
    "identity, predecessor/successor lineage, historical completeness, or a "
    "complete-history claim."
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_RAW_MANIFEST_FILES = {
    "discovery-observations.jsonl",
    "discovered-resources.jsonl",
    "fetch-attempts.jsonl",
    "raw-artifacts.jsonl",
    "resource-artifact-links.jsonl",
    "artifact-url-observations.jsonl",
    "issues.jsonl",
    "discovery-manifest.json",
}
_EXTENSIONS = {
    "text/html": ".html",
    "application/pdf": ".pdf",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx"
    ),
    "application/x-ole-storage": ".ole",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/tiff": ".tiff",
    "image/png": ".png",
    "application/zip": ".zip",
}


@dataclass(frozen=True)
class MaterializedHistoricalBatch:
    batch_id: str
    batch_fingerprint: str
    index_path: Path
    document_count: int
    attachment_count: int
    replayed: bool


@dataclass(frozen=True)
class _BundlePlan:
    manifest: dict[str, Any]
    source_files: tuple[tuple[Path, str], ...]


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def _document_number(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError("official detail has no formal document number")
    exact = raw
    normalized = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", exact),
    )
    if not normalized:
        raise ContractError("formal document number normalizes to empty")
    return exact, normalized


def _single_string(row: Mapping[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} has no {key}")
    return value


def _positive_ordinal(row: Mapping[str, Any]) -> int:
    locator = row.get("discovery_locator")
    value = (
        locator.get("attachment_ordinal")
        if isinstance(locator, Mapping)
        else None
    )
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError("official attachment has no positive declared ordinal")
    return value


def _artifact_content_path(
    artifact_sha256: str,
    media_type: str,
) -> str:
    extension = _EXTENSIONS.get(media_type, ".bin")
    return (
        f"artifacts/sha256/{artifact_sha256[:2]}/"
        f"{artifact_sha256}{extension}"
    )


def _safe_relative(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ContractError(f"{label} is missing")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ContractError(f"{label} escapes its root")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ContractError(f"{label} escapes its root") from exc
    return resolved


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _manifest_receipts(
    run_dir: Path,
    raw_manifest: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    entries = raw_manifest.get("files")
    if not isinstance(entries, list):
        raise ContractError("raw manifest has no file receipts")
    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ContractError("raw manifest file receipt is invalid")
        filename = entry.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
        ):
            raise ContractError("raw manifest file receipt path escapes run")
        if filename in by_name:
            raise ContractError("raw manifest repeats a file receipt")
        path = _safe_relative(run_dir, filename, "manifested file path")
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            raise ContractError(f"manifested file changed: {filename}")
        by_name[filename] = entry
    missing = sorted(_REQUIRED_RAW_MANIFEST_FILES - set(by_name))
    if missing:
        raise ContractError(
            f"raw manifest omits required file receipts: {missing}"
        )
    return by_name


def _resource_record(
    *,
    relation: str,
    resource: Mapping[str, Any],
    artifact: Mapping[str, Any],
    parent_resource_id: str | None = None,
    ordinal: int | None = None,
) -> dict[str, Any]:
    resource_id = _single_string(resource, "resource_id", relation)
    artifact_sha256 = _require_sha256(
        artifact.get("artifact_sha256"),
        f"{relation} artifact_sha256",
    )
    media_type = _single_string(artifact, "media_type", relation)
    byte_size = artifact.get("byte_size")
    if (
        not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or byte_size < 0
    ):
        raise ContractError(f"{relation} has invalid artifact byte size")
    record: dict[str, Any] = {
        "relation": relation,
        "resource_id": resource_id,
        "source_url": _single_string(resource, "source_url", relation),
        "artifact_sha256": artifact_sha256,
        "media_type": media_type,
        "byte_size": byte_size,
        "content_path": _artifact_content_path(
            artifact_sha256, media_type
        ),
    }
    if relation == "declared_attachment":
        if parent_resource_id is None or ordinal is None:
            raise ContractError("attachment record lacks parent or ordinal")
        record.update(
            {
                "parent_resource_id": parent_resource_id,
                "declared_attachment_ordinal": ordinal,
                "declared_label": _single_string(
                    resource, "source_label", relation
                ),
            }
        )
    return record


def _bundle_fingerprint_basis(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BUNDLE_FINGERPRINT_SCHEMA,
        "source_plan_sha256": manifest.get("source_plan_sha256"),
        "raw_manifest_sha256": manifest.get("raw_manifest_sha256"),
        "official_document_number_raw": manifest.get(
            "official_document_number_raw"
        ),
        "official_document_number_normalized": manifest.get(
            "official_document_number_normalized"
        ),
        "detail": manifest.get("detail"),
        "attachments": manifest.get("attachments"),
        "declared_attachment_count": manifest.get(
            "declared_attachment_count"
        ),
        "acquired_attachment_count": manifest.get(
            "acquired_attachment_count"
        ),
    }


def _bundle_plan(
    *,
    run_dir: Path,
    source_plan_sha256: str,
    raw_manifest_sha256: str,
    detail: Mapping[str, Any],
    attachments: Iterable[Mapping[str, Any]],
    artifact_by_resource: Mapping[str, Mapping[str, Any]],
) -> _BundlePlan:
    detail_resource_id = _single_string(
        detail, "resource_id", "official detail"
    )
    document_raw, document_normalized = _document_number(
        detail.get("official_document_number_raw")
    )
    detail_artifact = artifact_by_resource.get(detail_resource_id)
    if detail_artifact is None:
        raise ContractError("official detail has no exact linked artifact")
    detail_record = _resource_record(
        relation="detail_page",
        resource=detail,
        artifact=detail_artifact,
    )

    ordered = sorted(attachments, key=_positive_ordinal)
    ordinals = [_positive_ordinal(row) for row in ordered]
    if ordinals != list(range(1, len(ordered) + 1)):
        raise ContractError(
            "declared attachment ordinals are duplicated, missing, or non-contiguous"
        )
    attachment_records: list[dict[str, Any]] = []
    source_files: dict[str, Path] = {}

    detail_source_path = resolve_run_relative(
        run_dir,
        _single_string(
            detail_artifact,
            "content_path",
            "detail artifact",
        ),
    )
    source_files[detail_record["content_path"]] = detail_source_path
    used_resource_ids = {detail_resource_id}
    for attachment, ordinal in zip(ordered, ordinals, strict=True):
        resource_id = _single_string(
            attachment, "resource_id", "official attachment"
        )
        if resource_id in used_resource_ids:
            raise ContractError("resource is reused within one notice bundle")
        used_resource_ids.add(resource_id)
        if attachment.get("parent_resource_id") != detail_resource_id:
            raise ContractError("official attachment has the wrong parent detail")
        attachment_raw, attachment_normalized = _document_number(
            attachment.get("official_document_number_raw")
        )
        if attachment_normalized != document_normalized:
            raise ContractError(
                "attachment and detail have conflicting formal document numbers"
            )
        artifact = artifact_by_resource.get(resource_id)
        if artifact is None:
            raise ContractError("official attachment has no exact linked artifact")
        record = _resource_record(
            relation="declared_attachment",
            resource=attachment,
            artifact=artifact,
            parent_resource_id=detail_resource_id,
            ordinal=ordinal,
        )
        record["official_document_number_raw"] = attachment_raw
        attachment_records.append(record)
        source_path = resolve_run_relative(
            run_dir,
            _single_string(
                artifact, "content_path", "attachment artifact"
            ),
        )
        previous = source_files.get(record["content_path"])
        if previous is not None and previous != source_path:
            raise ContractError("bundle content path maps to conflicting bytes")
        source_files[record["content_path"]] = source_path

    draft = {
        "source_plan_sha256": source_plan_sha256,
        "raw_manifest_sha256": raw_manifest_sha256,
        "official_document_number_raw": document_raw,
        "official_document_number_normalized": document_normalized,
        "detail": detail_record,
        "attachments": attachment_records,
        "declared_attachment_count": len(attachment_records),
        "acquired_attachment_count": len(attachment_records),
    }
    fingerprint = sha256_bytes(
        canonical_json_bytes(_bundle_fingerprint_basis(draft))
    )
    bundle_id = stable_id(
        "nhi-historical-notice-bundle",
        document_normalized,
        detail_resource_id,
        fingerprint,
    )
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "status": "materialized_verified",
        "bundle_id": bundle_id,
        "bundle_fingerprint": fingerprint,
        "fingerprint_schema": BUNDLE_FINGERPRINT_SCHEMA,
        **draft,
        "non_claim_statement": NON_CLAIM_STATEMENT,
    }
    assert_public_value(manifest)
    return _BundlePlan(
        manifest=manifest,
        source_files=tuple(sorted(source_files.items())),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_verified(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    digest = hashlib.sha256()
    with source.open("rb") as input_stream, destination.open(
        "xb"
    ) as output_stream:
        for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
            output_stream.write(chunk)
            digest.update(chunk)
            written += len(chunk)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    if written != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ContractError("source bytes changed during bundle materialization")


def _resource_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    detail = manifest.get("detail")
    attachments = manifest.get("attachments")
    if not isinstance(detail, dict) or not isinstance(attachments, list):
        raise ContractError("historical bundle resources are incomplete")
    if any(not isinstance(row, dict) for row in attachments):
        raise ContractError("historical bundle attachment row is invalid")
    return [detail, *attachments]


def verify_historical_notice_bundle(bundle_path: Path) -> dict[str, Any]:
    """Verify one standalone historical notice bundle and its exact bytes."""

    manifest_path = bundle_path / "manifest.json"
    manifest = _read_object(manifest_path, "historical bundle manifest")
    if (
        manifest.get("schema") != BUNDLE_SCHEMA
        or manifest.get("status") != "materialized_verified"
        or manifest.get("fingerprint_schema")
        != BUNDLE_FINGERPRINT_SCHEMA
    ):
        raise ContractError("historical bundle manifest contract mismatch")
    assert_public_value(manifest)
    _require_sha256(
        manifest.get("source_plan_sha256"), "source_plan_sha256"
    )
    _require_sha256(
        manifest.get("raw_manifest_sha256"), "raw_manifest_sha256"
    )
    document_raw, document_normalized = _document_number(
        manifest.get("official_document_number_raw")
    )
    if (
        document_raw != manifest.get("official_document_number_raw")
        or document_normalized
        != manifest.get("official_document_number_normalized")
    ):
        raise ContractError("historical bundle document number is inconsistent")

    rows = _resource_rows(manifest)
    detail = rows[0]
    attachments = rows[1:]
    if detail.get("relation") != "detail_page":
        raise ContractError("historical bundle has no exact detail resource")
    detail_resource_id = _single_string(
        detail, "resource_id", "detail resource"
    )
    ordinals = [
        row.get("declared_attachment_ordinal") for row in attachments
    ]
    if ordinals != list(range(1, len(attachments) + 1)):
        raise ContractError("historical bundle attachment order is incomplete")
    if (
        manifest.get("declared_attachment_count") != len(attachments)
        or manifest.get("acquired_attachment_count") != len(attachments)
    ):
        raise ContractError("historical bundle attachment coverage is incomplete")

    resource_ids: set[str] = set()
    artifact_bytes_by_path: dict[str, int] = {}
    expected_files = {"manifest.json"}
    for row in rows:
        resource_id = _single_string(row, "resource_id", "bundle resource")
        if resource_id in resource_ids:
            raise ContractError("historical bundle reuses a resource")
        resource_ids.add(resource_id)
        if row is not detail:
            if row.get("relation") != "declared_attachment":
                raise ContractError("historical bundle attachment relation is invalid")
            if row.get("parent_resource_id") != detail_resource_id:
                raise ContractError("historical bundle attachment parent is invalid")
            _single_string(row, "declared_label", "declared attachment")
            attachment_raw, attachment_normalized = _document_number(
                row.get("official_document_number_raw")
            )
            if (
                attachment_raw != row.get("official_document_number_raw")
                or attachment_normalized != document_normalized
            ):
                raise ContractError(
                    "historical bundle attachment document number conflicts"
                )
        digest = _require_sha256(
            row.get("artifact_sha256"), "bundle artifact_sha256"
        )
        byte_size = row.get("byte_size")
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size < 0
        ):
            raise ContractError("bundle artifact byte size is invalid")
        _single_string(row, "media_type", "bundle resource")
        relative = _single_string(row, "content_path", "bundle resource")
        path = _safe_relative(
            bundle_path, relative, "bundle artifact content path"
        )
        if (
            not path.is_file()
            or path.stat().st_size != byte_size
            or file_sha256(path) != digest
        ):
            raise ContractError("historical bundle artifact verification failed")
        previous_bytes = artifact_bytes_by_path.get(relative)
        if previous_bytes is not None and previous_bytes != byte_size:
            raise ContractError("historical bundle artifact path is inconsistent")
        artifact_bytes_by_path[relative] = byte_size
        expected_files.add(relative)

    actual_files = {
        path.relative_to(bundle_path).as_posix()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ContractError("historical bundle contains unmanifested or missing files")
    expected_fingerprint = sha256_bytes(
        canonical_json_bytes(_bundle_fingerprint_basis(manifest))
    )
    if manifest.get("bundle_fingerprint") != expected_fingerprint:
        raise ContractError("historical bundle fingerprint mismatch")
    expected_id = stable_id(
        "nhi-historical-notice-bundle",
        document_normalized,
        detail_resource_id,
        expected_fingerprint,
    )
    if manifest.get("bundle_id") != expected_id:
        raise ContractError("historical bundle id mismatch")
    return {
        "status": "passed",
        "bundle_id": expected_id,
        "bundle_fingerprint": expected_fingerprint,
        "manifest_sha256": file_sha256(manifest_path),
        "document_number_normalized": document_normalized,
        "resource_ids": sorted(resource_ids),
        "attachment_count": len(attachments),
        "artifact_bytes": sum(artifact_bytes_by_path.values()),
    }


def _seal_bundle(bundle_root: Path, plan: _BundlePlan) -> bool:
    manifest = plan.manifest
    bundle_id = manifest["bundle_id"]
    destination = bundle_root / bundle_id
    if destination.exists():
        checked = verify_historical_notice_bundle(destination)
        if (
            checked["bundle_id"] != bundle_id
            or checked["bundle_fingerprint"]
            != manifest["bundle_fingerprint"]
            or file_sha256(destination / "manifest.json")
            != sha256_bytes(canonical_json_bytes(manifest))
        ):
            raise ContractError("existing historical bundle identity conflicts")
        return True

    bundle_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=bundle_root)
    )
    try:
        resources_by_path = {
            row["content_path"]: row for row in _resource_rows(manifest)
        }
        for relative, source in plan.source_files:
            record = resources_by_path[relative]
            _copy_verified(
                source,
                temporary / relative,
                expected_sha256=record["artifact_sha256"],
                expected_bytes=record["byte_size"],
            )
        _write_bytes(
            temporary / "manifest.json",
            canonical_json_bytes(manifest),
        )
        for directory in sorted(
            {
                path.parent
                for path in temporary.rglob("*")
                if path.is_file()
            },
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _fsync_directory(temporary)
        verify_historical_notice_bundle(temporary)
        os.replace(temporary, destination)
        _fsync_directory(bundle_root)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return False


def _batch_fingerprint_basis(index: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": BATCH_FINGERPRINT_SCHEMA,
        "source_plan_sha256": index.get("source_plan_sha256"),
        "raw_manifest_sha256": index.get("raw_manifest_sha256"),
        "denominator": index.get("denominator"),
        "documents": index.get("documents"),
    }


def verify_historical_bundle_batch(output_root: Path) -> dict[str, Any]:
    """Verify the batch receipt and every materialized notice bundle."""

    index_path = output_root / "batch-index.json"
    index = _read_object(index_path, "historical bundle batch index")
    if (
        index.get("schema") != BATCH_SCHEMA
        or index.get("status") != "passed"
        or index.get("fingerprint_schema")
        != BATCH_FINGERPRINT_SCHEMA
    ):
        raise ContractError("historical batch index contract mismatch")
    assert_public_value(index)
    source_plan_sha256 = _require_sha256(
        index.get("source_plan_sha256"), "batch source_plan_sha256"
    )
    raw_manifest_sha256 = _require_sha256(
        index.get("raw_manifest_sha256"), "batch raw_manifest_sha256"
    )
    documents = index.get("documents")
    denominator = index.get("denominator")
    if not isinstance(documents, list) or not isinstance(denominator, dict):
        raise ContractError("historical batch denominator is missing")
    if any(not isinstance(row, dict) for row in documents):
        raise ContractError("historical batch document row is invalid")
    expected_order = sorted(
        documents,
        key=lambda row: (
            str(row.get("official_document_number_normalized", "")),
            str(row.get("detail_resource_id", "")),
        ),
    )
    if documents != expected_order:
        raise ContractError("historical batch documents are not deterministic")

    all_resource_ids: set[str] = set()
    attachment_total = 0
    artifact_bytes = 0
    for row in documents:
        if row.get("terminal_status") != "materialized_verified":
            raise ContractError("historical batch has a non-terminal document")
        relative = _single_string(row, "bundle_path", "batch document")
        bundle_path = _safe_relative(
            output_root, relative, "batch bundle path"
        )
        checked = verify_historical_notice_bundle(bundle_path)
        if (
            checked["bundle_id"] != row.get("bundle_id")
            or checked["bundle_fingerprint"]
            != row.get("bundle_fingerprint")
            or checked["manifest_sha256"]
            != row.get("manifest_sha256")
            or checked["attachment_count"]
            != row.get("attachment_count")
            or checked["document_number_normalized"]
            != row.get("official_document_number_normalized")
        ):
            raise ContractError("historical batch row differs from its bundle")
        manifest = _read_object(
            bundle_path / "manifest.json", "historical bundle manifest"
        )
        if (
            manifest.get("source_plan_sha256") != source_plan_sha256
            or manifest.get("raw_manifest_sha256")
            != raw_manifest_sha256
            or manifest.get("detail", {}).get("resource_id")
            != row.get("detail_resource_id")
        ):
            raise ContractError("historical batch source binding mismatch")
        reused = all_resource_ids.intersection(checked["resource_ids"])
        if reused:
            raise ContractError("historical batch reuses a source resource")
        all_resource_ids.update(checked["resource_ids"])
        attachment_total += checked["attachment_count"]
        artifact_bytes += checked["artifact_bytes"]

    actual_denominator = {
        "official_documents": len(documents),
        "detail_resources": len(documents),
        "declared_attachments": attachment_total,
        "materialized_bundles": len(documents),
        "materialized_resources": len(all_resource_ids),
        "materialized_artifact_bytes": artifact_bytes,
        "terminal_status_counts": {
            "materialized_verified": len(documents)
        },
    }
    if denominator != actual_denominator:
        raise ContractError("historical batch denominator is not exact")
    expected_fingerprint = sha256_bytes(
        canonical_json_bytes(_batch_fingerprint_basis(index))
    )
    if index.get("batch_fingerprint") != expected_fingerprint:
        raise ContractError("historical batch fingerprint mismatch")
    expected_id = stable_id(
        "nhi-historical-notice-batch",
        source_plan_sha256,
        raw_manifest_sha256,
        expected_fingerprint,
    )
    if index.get("batch_id") != expected_id:
        raise ContractError("historical batch id mismatch")
    return {
        "status": "passed",
        "batch_id": expected_id,
        "batch_fingerprint": expected_fingerprint,
        "document_count": len(documents),
        "attachment_count": attachment_total,
        "resource_count": len(all_resource_ids),
        "artifact_bytes": artifact_bytes,
        "index_sha256": file_sha256(index_path),
    }


def materialize_historical_notice_bundles(
    run_dir: Path,
    *,
    source_plan: Path,
    output_root: Path,
) -> MaterializedHistoricalBatch:
    """Materialize every formal document in a sealed v2 acquisition run.

    Validation and planning cover the complete run before any output is
    written.  A document with no attachments is still a valid one-resource
    bundle; no media type, including ODT, is required.
    """

    plan = SourcePlan.load(source_plan)
    raw_manifest_path = run_dir / "raw-manifest.json"
    raw_manifest = _read_object(raw_manifest_path, "raw manifest")
    if (
        raw_manifest.get("schema") != RAW_MANIFEST_SCHEMA
        or raw_manifest.get("status") != "success"
    ):
        raise ContractError("acquisition run is not a successful v2 raw run")
    if (
        raw_manifest.get("source_plan_schema") != plan.document["schema"]
        or raw_manifest.get("source_plan_sha256") != plan.sha256
    ):
        raise ContractError("raw run is not bound to the supplied source plan")
    _manifest_receipts(run_dir, raw_manifest)
    verification = verify_raw(run_dir)
    if verification.get("status") != "passed":
        raise ContractError("v2 acquisition run did not verify")
    raw_manifest_sha256 = file_sha256(raw_manifest_path)

    resources = unique_rows(
        run_dir / "discovered-resources.jsonl", "resource_id"
    )
    artifacts = unique_rows(
        run_dir / "raw-artifacts.jsonl", "artifact_sha256"
    )
    links = list(
        unique_rows(
            run_dir / "resource-artifact-links.jsonl", "link_id"
        ).values()
    )
    acquisition_issues = unique_rows(
        run_dir / "issues.jsonl", "issue_id"
    )
    if any(
        str(row.get("severity", "")).lower()
        in {"blocking", "error", "fatal"}
        for row in acquisition_issues.values()
    ):
        raise ContractError(
            "sealed historical acquisition contains a blocking issue"
        )
    links_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        if link.get("schema") != RESOURCE_ARTIFACT_LINK_SCHEMA:
            raise ContractError("resource-artifact link schema mismatch")
        links_by_resource[str(link.get("resource_id"))].append(link)
    artifact_by_resource: dict[str, Mapping[str, Any]] = {}
    for resource_id, resource in resources.items():
        if resource.get("schema") != DISCOVERED_RESOURCE_SCHEMA:
            raise ContractError("discovered resource schema mismatch")
        resource_links = links_by_resource.get(resource_id, [])
        if len(resource_links) != 1:
            raise ContractError(
                "each source resource must have exactly one sealed artifact link"
            )
        artifact_sha256 = resource_links[0].get("artifact_sha256")
        artifact = artifacts.get(str(artifact_sha256))
        if artifact is None or artifact.get("schema") != RAW_ARTIFACT_SCHEMA:
            raise ContractError("source resource links to a missing artifact")
        digest = _require_sha256(
            artifact.get("artifact_sha256"), "raw artifact_sha256"
        )
        relative = _single_string(
            artifact, "content_path", "raw artifact"
        )
        if relative != relative_blob_path(digest):
            raise ContractError("raw artifact path is not content-addressed")
        path = resolve_run_relative(run_dir, relative)
        if (
            not path.is_file()
            or path.stat().st_size != artifact.get("byte_size")
            or file_sha256(path) != digest
        ):
            raise ContractError("raw artifact bytes are missing or changed")
        artifact_by_resource[resource_id] = artifact

    detail_rows: list[dict[str, Any]] = []
    attachments_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    attached_resource_ids: set[str] = set()
    for resource in resources.values():
        kind = resource.get("resource_kind")
        if kind == "official_detail_page":
            detail_rows.append(resource)
        elif kind == "official_attachment":
            resource_id = _single_string(
                resource, "resource_id", "official attachment"
            )
            if resource_id in attached_resource_ids:
                raise ContractError("official attachment resource is reused")
            attached_resource_ids.add(resource_id)
            parent_resource_id = resource.get("parent_resource_id")
            if not isinstance(parent_resource_id, str) or not parent_resource_id:
                raise ContractError("official attachment is orphaned")
            attachments_by_parent[parent_resource_id].append(resource)
        else:
            raise ContractError(
                "historical bundle run contains an unsupported resource kind"
            )
    if not detail_rows:
        raise ContractError("historical acquisition run has no official details")
    detail_ids = {
        _single_string(row, "resource_id", "official detail")
        for row in detail_rows
    }
    orphan_parents = sorted(set(attachments_by_parent) - detail_ids)
    if orphan_parents:
        raise ContractError("official attachment points to an unknown detail")

    document_numbers: dict[str, str] = {}
    plans: list[_BundlePlan] = []
    used_resources: set[str] = set()
    for detail in sorted(
        detail_rows,
        key=lambda row: (
            _document_number(
                row.get("official_document_number_raw")
            )[1],
            str(row.get("resource_id")),
        ),
    ):
        detail_id = _single_string(
            detail, "resource_id", "official detail"
        )
        _raw, normalized = _document_number(
            detail.get("official_document_number_raw")
        )
        previous_detail = document_numbers.get(normalized)
        if previous_detail is not None and previous_detail != detail_id:
            raise ContractError(
                "formal document number is assigned to conflicting detail resources"
            )
        document_numbers[normalized] = detail_id
        child_rows = attachments_by_parent.get(detail_id, [])
        planned_resource_ids = {
            detail_id,
            *[
                _single_string(
                    row, "resource_id", "official attachment"
                )
                for row in child_rows
            ],
        }
        reused = used_resources.intersection(planned_resource_ids)
        if reused:
            raise ContractError("source resource is reused across notice bundles")
        used_resources.update(planned_resource_ids)
        plans.append(
            _bundle_plan(
                run_dir=run_dir,
                source_plan_sha256=plan.sha256,
                raw_manifest_sha256=raw_manifest_sha256,
                detail=detail,
                attachments=child_rows,
                artifact_by_resource=artifact_by_resource,
            )
        )
    if used_resources != set(resources):
        raise ContractError("historical bundle planning omitted source resources")

    bundle_root = output_root / "bundles"
    replay_flags = [_seal_bundle(bundle_root, item) for item in plans]
    document_rows: list[dict[str, Any]] = []
    materialized_bytes = 0
    attachment_count = 0
    for item in plans:
        manifest = item.manifest
        bundle_path = bundle_root / manifest["bundle_id"]
        checked = verify_historical_notice_bundle(bundle_path)
        attachment_count += checked["attachment_count"]
        materialized_bytes += checked["artifact_bytes"]
        document_rows.append(
            {
                "official_document_number_raw": manifest[
                    "official_document_number_raw"
                ],
                "official_document_number_normalized": manifest[
                    "official_document_number_normalized"
                ],
                "detail_resource_id": manifest["detail"]["resource_id"],
                "bundle_id": manifest["bundle_id"],
                "bundle_fingerprint": manifest["bundle_fingerprint"],
                "manifest_sha256": checked["manifest_sha256"],
                "bundle_path": f"bundles/{manifest['bundle_id']}",
                "attachment_count": checked["attachment_count"],
                "terminal_status": "materialized_verified",
            }
        )
    document_rows.sort(
        key=lambda row: (
            row["official_document_number_normalized"],
            row["detail_resource_id"],
        )
    )
    denominator = {
        "official_documents": len(document_rows),
        "detail_resources": len(document_rows),
        "declared_attachments": attachment_count,
        "materialized_bundles": len(document_rows),
        "materialized_resources": len(used_resources),
        "materialized_artifact_bytes": materialized_bytes,
        "terminal_status_counts": {
            "materialized_verified": len(document_rows)
        },
    }
    draft_index = {
        "source_plan_sha256": plan.sha256,
        "raw_manifest_sha256": raw_manifest_sha256,
        "denominator": denominator,
        "documents": document_rows,
    }
    batch_fingerprint = sha256_bytes(
        canonical_json_bytes(_batch_fingerprint_basis(draft_index))
    )
    batch_id = stable_id(
        "nhi-historical-notice-batch",
        plan.sha256,
        raw_manifest_sha256,
        batch_fingerprint,
    )
    index = {
        "schema": BATCH_SCHEMA,
        "status": "passed",
        "batch_id": batch_id,
        "batch_fingerprint": batch_fingerprint,
        "fingerprint_schema": BATCH_FINGERPRINT_SCHEMA,
        **draft_index,
        "non_claim_statement": NON_CLAIM_STATEMENT,
    }
    assert_public_value(index)
    index_payload = canonical_json_bytes(index)
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "batch-index.json"
    index_replayed = False
    if index_path.exists():
        if (
            not index_path.is_file()
            or index_path.read_bytes() != index_payload
        ):
            raise ContractError("existing historical batch index conflicts")
        index_replayed = True
    else:
        temporary_index = output_root / ".batch-index.json.tmp"
        if temporary_index.exists():
            raise ContractError("historical batch temporary index already exists")
        _write_bytes(temporary_index, index_payload)
        os.replace(temporary_index, index_path)
        _fsync_directory(output_root)
    checked_batch = verify_historical_bundle_batch(output_root)
    if (
        checked_batch["batch_id"] != batch_id
        or checked_batch["batch_fingerprint"] != batch_fingerprint
    ):
        raise ContractError("materialized historical batch failed verification")
    return MaterializedHistoricalBatch(
        batch_id=batch_id,
        batch_fingerprint=batch_fingerprint,
        index_path=index_path,
        document_count=len(document_rows),
        attachment_count=attachment_count,
        replayed=index_replayed and all(replay_flags),
    )
