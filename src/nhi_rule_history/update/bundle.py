"""Immutable, content-addressed source bundles for one official notice."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
    utc_now,
)
from nhi_rule_history.fetch.runner import media_type
from nhi_rule_history.update.rss import (
    HTTP_PROFILE_ID,
    AttachmentLink,
    OfficialNhiClient,
    OfficialResponse,
    RssItem,
    parse_attachment_links,
)


BUNDLE_SCHEMA = "nhi-rule-history/update-source-bundle/v1"
BUNDLE_FINGERPRINT_SCHEMA = "nhi-rule-history/update-source-fingerprint/v1"


def _extension(detected_media_type: str) -> str:
    return {
        "application/rss+xml": ".xml",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/html": ".html",
        "application/pdf": ".pdf",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }.get(detected_media_type, ".bin")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_fsynced(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "content-length",
        "content-disposition",
        "etag",
        "last-modified",
    }
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in allowed
    }


@dataclass(frozen=True)
class SealedBundle:
    bundle_id: str
    bundle_fingerprint: str
    path: Path
    manifest: dict[str, Any]
    replayed: bool
    current_observations: tuple[dict[str, Any], ...]


class BundleBuilder:
    """Build once in a temporary directory, fsync, and atomically seal."""

    def __init__(
        self,
        bundle_root: Path,
        *,
        rss_item: RssItem,
        feed_response: OfficialResponse,
        detail_response: OfficialResponse,
        attachments: Iterable[
            tuple[AttachmentLink, OfficialResponse]
        ],
    ):
        self.bundle_root = bundle_root
        self.rss_item = rss_item
        self.feed_response = feed_response
        self.detail_response = detail_response
        self.attachments = list(attachments)

    @staticmethod
    def _resource(
        relation: str,
        response: OfficialResponse,
        *,
        declared_sequence: int | None = None,
        declared_label: str | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        digest = sha256_bytes(response.body)
        detected = media_type(response.headers, response.body)
        relative_path = (
            f"artifacts/sha256/{digest[:2]}/{digest}"
            f"{_extension(detected)}"
        )
        record: dict[str, Any] = {
            "relation": relation,
            "request_url": response.request_url,
            "final_url": response.final_url,
            "http_status": response.status_code,
            "response_headers": _safe_headers(response.headers),
            "artifact_sha256": digest,
            "byte_size": len(response.body),
            "media_type": detected,
            "content_path": relative_path,
            "observed_at": response.observed_at,
        }
        if declared_sequence is not None:
            record["declared_sequence"] = declared_sequence
        if declared_label is not None:
            record["declared_label"] = declared_label
        return record, response.body

    def seal(self) -> SealedBundle:
        if not self.attachments:
            raise ContractError("source bundle cannot omit declared attachments")
        records_and_bytes = [
            self._resource("rss_feed", self.feed_response),
            self._resource("detail_page", self.detail_response),
        ]
        records_and_bytes.extend(
            self._resource(
                "declared_attachment",
                response,
                declared_sequence=link.sequence,
                declared_label=link.label,
            )
            for link, response in self.attachments
        )
        resources = [record for record, _payload in records_and_bytes]
        declared_sequences = [
            row["declared_sequence"]
            for row in resources
            if row["relation"] == "declared_attachment"
        ]
        if declared_sequences != list(range(len(declared_sequences))):
            raise ContractError("declared attachment sequence is not contiguous")
        if any(row["http_status"] != 200 for row in resources):
            raise ContractError("source bundle contains a non-200 response")

        fingerprint_resources = [
            {
                key: value
                for key, value in resource.items()
                if key != "observed_at"
            }
            for resource in resources
        ]
        fingerprint_basis = {
            "schema": BUNDLE_FINGERPRINT_SCHEMA,
            "rss_item": self.rss_item.as_dict(),
            "http_profile_id": HTTP_PROFILE_ID,
            "resources": fingerprint_resources,
            "declared_attachment_count": len(self.attachments),
            "acquired_attachment_count": sum(
                row["relation"] == "declared_attachment" for row in resources
            ),
        }
        fingerprint = sha256_bytes(canonical_json_bytes(fingerprint_basis))
        bundle_id = stable_id(
            "nhi-update-source-bundle",
            self.rss_item.guid,
            fingerprint,
        )
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "bundle_id": bundle_id,
            "bundle_fingerprint": fingerprint,
            "sealed_at": utc_now(),
            "fingerprint_schema": BUNDLE_FINGERPRINT_SCHEMA,
            **{
                key: value
                for key, value in fingerprint_basis.items()
                if key not in {"schema", "resources"}
            },
            "resources": resources,
        }

        self.bundle_root.mkdir(parents=True, exist_ok=True)
        destination = self.bundle_root / bundle_id
        if destination.exists():
            existing_path = destination / "manifest.json"
            if not existing_path.is_file():
                raise ContractError("existing bundle is missing manifest.json")
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if (
                existing.get("bundle_id") != bundle_id
                or existing.get("bundle_fingerprint") != fingerprint
            ):
                raise ContractError("bundle identity collision")
            verify_bundle(destination)
            return SealedBundle(
                bundle_id,
                fingerprint,
                destination,
                existing,
                True,
                tuple(resources),
            )

        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{bundle_id}.",
                dir=self.bundle_root,
            )
        )
        try:
            written: set[str] = set()
            for record, payload in records_and_bytes:
                relative = record["content_path"]
                if relative in written:
                    continue
                written.add(relative)
                _write_fsynced(temporary / relative, payload)
            _write_fsynced(
                temporary / "manifest.json",
                canonical_json_bytes(manifest),
            )
            for directory in sorted(
                {
                    path.parent
                    for path in temporary.rglob("*")
                    if path.is_file()
                },
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                _fsync_directory(directory)
            _fsync_directory(temporary)
            verify_bundle(temporary)
            os.replace(temporary, destination)
            _fsync_directory(self.bundle_root)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return SealedBundle(
            bundle_id,
            fingerprint,
            destination,
            manifest,
            False,
            tuple(resources),
        )


def verify_bundle(bundle_path: Path) -> dict[str, Any]:
    manifest_path = bundle_path / "manifest.json"
    if not manifest_path.is_file():
        raise ContractError("bundle manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("bundle manifest is malformed") from exc
    if manifest.get("schema") != BUNDLE_SCHEMA:
        raise ContractError("unexpected bundle schema")
    resources = manifest.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ContractError("bundle resources are missing")
    for resource in resources:
        path = bundle_path / str(resource.get("content_path", ""))
        try:
            path.resolve().relative_to(bundle_path.resolve())
        except ValueError as exc:
            raise ContractError("bundle content path escapes bundle") from exc
        if (
            not path.is_file()
            or path.stat().st_size != resource.get("byte_size")
            or file_sha256(path) != resource.get("artifact_sha256")
        ):
            raise ContractError("bundle artifact verification failed")
    declared = manifest.get("declared_attachment_count")
    acquired = sum(
        row.get("relation") == "declared_attachment" for row in resources
    )
    if (
        not isinstance(declared, int)
        or declared <= 0
        or manifest.get("acquired_attachment_count") != declared
        or acquired != declared
    ):
        raise ContractError("bundle attachment coverage is incomplete")
    fingerprint_basis = {
        "schema": manifest.get("fingerprint_schema"),
        "rss_item": manifest.get("rss_item"),
        "http_profile_id": manifest.get("http_profile_id"),
        "resources": [
            {
                key: value
                for key, value in resource.items()
                if key != "observed_at"
            }
            for resource in resources
        ],
        "declared_attachment_count": declared,
        "acquired_attachment_count": manifest.get(
            "acquired_attachment_count"
        ),
    }
    expected_fingerprint = sha256_bytes(canonical_json_bytes(fingerprint_basis))
    if manifest.get("bundle_fingerprint") != expected_fingerprint:
        raise ContractError("bundle fingerprint mismatch")
    expected_id = stable_id(
        "nhi-update-source-bundle",
        str(manifest.get("rss_item", {}).get("guid", "")),
        expected_fingerprint,
    )
    if manifest.get("bundle_id") != expected_id:
        raise ContractError("bundle id mismatch")
    return {
        "status": "passed",
        "bundle_id": expected_id,
        "bundle_fingerprint": expected_fingerprint,
        "resource_count": len(resources),
        "attachment_count": declared,
    }


def acquire_notice_bundle(
    bundle_root: Path,
    *,
    client: OfficialNhiClient,
    item: RssItem,
    feed_response: OfficialResponse,
) -> SealedBundle:
    detail = client.get_detail(item.link)
    declared = parse_attachment_links(item.link, detail.body)
    acquired = [
        (
            link,
            client.get_attachment(link.url, detail_url=item.link),
        )
        for link in declared
    ]
    return BundleBuilder(
        bundle_root,
        rss_item=item,
        feed_response=feed_response,
        detail_response=detail,
        attachments=acquired,
    ).seal()
