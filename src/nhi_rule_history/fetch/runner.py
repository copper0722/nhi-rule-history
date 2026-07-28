from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from nhi_rule_history.contracts import (
    ARTIFACT_URL_OBSERVATION_SCHEMA,
    ContractError,
    FETCH_ATTEMPT_SCHEMA,
    ISSUE_SCHEMA,
    RAW_ARTIFACT_SCHEMA,
    RAW_MANIFEST_SCHEMA,
    RESOURCE_ARTIFACT_LINK_SCHEMA,
    SourcePlan,
    append_jsonl,
    ensure_run_layout,
    manifest_file_entry,
    stable_id,
    unique_rows,
    utc_now,
    write_json,
)
from nhi_rule_history.fetch.http import HttpClient, HttpResponse
from nhi_rule_history.raw import RawStore


def media_type(headers: Mapping[str, str], payload: bytes) -> str:
    declared = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if payload.startswith(b"%PDF-"):
        return "application/pdf"
    if payload.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
                if "mimetype" in names:
                    odf_type = archive.read("mimetype").decode(
                        "ascii", errors="strict"
                    ).strip()
                    if odf_type.startswith("application/vnd.oasis.opendocument."):
                        return odf_type
                if "[Content_Types].xml" in names and "word/document.xml" in names:
                    return (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    )
                if "content.xml" in names and "META-INF/manifest.xml" in names:
                    return "application/vnd.oasis.opendocument.text"
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile):
            pass
        return "application/zip"
    if payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    prefix = payload[:512].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if declared and declared not in {"application/octet-stream", "binary/octet-stream"}:
        return declared
    return declared or "application/octet-stream"


class FetchRecorder:
    def __init__(self, run_dir: Path, client: HttpClient):
        self.run_dir = run_dir
        self.client = client
        self.store = RawStore(run_dir)
        self.artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
        self.links = unique_rows(run_dir / "resource-artifact-links.jsonl", "link_id")
        self.url_observations = unique_rows(
            run_dir / "artifact-url-observations.jsonl", "url_observation_id"
        )
        self.attempts = unique_rows(run_dir / "fetch-attempts.jsonl", "attempt_id")
        self.discovery_observations = unique_rows(
            run_dir / "discovery-observations.jsonl", "observation_id"
        )

    def _valid_links(self, resource_id: str) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for link in self.links.values():
            if link["resource_id"] != resource_id:
                continue
            artifact = self.artifacts.get(link["artifact_sha256"])
            if not artifact:
                continue
            if self.store.verify(
                artifact["content_path"],
                artifact["artifact_sha256"],
                artifact["byte_size"],
            ) and any(
                attempt.get("status") == "success"
                and attempt.get("resource_id") == resource_id
                and attempt.get("artifact_sha256") == link["artifact_sha256"]
                for attempt in self.attempts.values()
            ) and any(
                observation.get("resource_id") == resource_id
                and observation.get("artifact_sha256") == link["artifact_sha256"]
                for observation in self.url_observations.values()
            ):
                valid.append(link)
        return valid

    def _cached_discovery_response(self, source_url: str) -> HttpResponse | None:
        matches = [
            row
            for row in self.discovery_observations.values()
            if row.get("request_url") == source_url and row.get("status") == "success"
        ]
        for row in reversed(matches):
            if not self.store.verify(
                row["content_path"], row["content_sha256"], row["byte_size"]
            ):
                continue
            return HttpResponse(
                request_url=row["request_url"],
                final_url=row["final_url"],
                status_code=row["http_status"],
                headers=row["response_headers"],
                body=self.store.read(
                    row["content_path"],
                    row["content_sha256"],
                    row["byte_size"],
                ),
            )
        return None

    def _record_issue(self, resource: Mapping[str, Any], code: str) -> None:
        now = utc_now()
        append_jsonl(
            self.run_dir / "issues.jsonl",
            {
                "schema": ISSUE_SCHEMA,
                "issue_id": stable_id(
                    "acquisition-issue",
                    "fetch",
                    resource["resource_id"],
                    code,
                    now,
                ),
                "stage": "fetch",
                "severity": "blocking",
                "adapter_id": resource["adapter_id"],
                "resource_id": resource["resource_id"],
                "source_url": resource["source_url"],
                "code": code,
                "recorded_at": now,
            },
        )

    def _record_success(
        self,
        resource: Mapping[str, Any],
        response: HttpResponse,
        started_at: str,
        acquisition_mode: str,
    ) -> None:
        stored = self.store.put(response.body)
        observed_at = utc_now()
        artifact = {
            "schema": RAW_ARTIFACT_SCHEMA,
            "artifact_sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "content_path": stored.relative_path,
            "media_type": media_type(response.headers, response.body),
            "first_observed_at": observed_at,
        }
        previous_artifact = self.artifacts.get(stored.sha256)
        if previous_artifact is None:
            append_jsonl(self.run_dir / "raw-artifacts.jsonl", artifact)
            self.artifacts[stored.sha256] = artifact

        link_id = stable_id(
            "resource-artifact-link", resource["resource_id"], stored.sha256
        )
        link = {
            "schema": RESOURCE_ARTIFACT_LINK_SCHEMA,
            "link_id": link_id,
            "resource_id": resource["resource_id"],
            "artifact_sha256": stored.sha256,
            "relation": "retrieved_representation",
            "observed_at": observed_at,
        }
        if link_id not in self.links:
            append_jsonl(self.run_dir / "resource-artifact-links.jsonl", link)
            self.links[link_id] = link

        prior = sorted(
            (
                item
                for item in self.url_observations.values()
                if item["source_url"] == resource["source_url"]
            ),
            key=lambda item: (item["observed_at"], item["url_observation_id"]),
        )
        if not prior:
            relation = "first_observation"
            previous_sha = None
        elif prior[-1]["artifact_sha256"] == stored.sha256:
            relation = "same_bytes"
            previous_sha = prior[-1]["artifact_sha256"]
        else:
            relation = "same_url_different_bytes"
            previous_sha = prior[-1]["artifact_sha256"]
        url_observation_id = stable_id(
            "artifact-url-observation",
            resource["source_url"],
            stored.sha256,
            observed_at,
            resource["resource_id"],
        )
        url_observation = {
            "schema": ARTIFACT_URL_OBSERVATION_SCHEMA,
            "url_observation_id": url_observation_id,
            "resource_id": resource["resource_id"],
            "source_url": resource["source_url"],
            "artifact_sha256": stored.sha256,
            "relation_to_previous": relation,
            "observed_at": observed_at,
        }
        if previous_sha:
            url_observation["previous_artifact_sha256"] = previous_sha
        append_jsonl(
            self.run_dir / "artifact-url-observations.jsonl", url_observation
        )
        self.url_observations[url_observation_id] = url_observation

        attempt_id = stable_id(
            "fetch-attempt",
            resource["resource_id"],
            started_at,
            stored.sha256,
            acquisition_mode,
        )
        attempt = {
            "schema": FETCH_ATTEMPT_SCHEMA,
            "attempt_id": attempt_id,
            "resource_id": resource["resource_id"],
            "source_url": resource["source_url"],
            "started_at": started_at,
            "completed_at": observed_at,
            "status": "success",
            "acquisition_mode": acquisition_mode,
            "http_status": response.status_code,
            "final_url": response.final_url,
            "response_headers": response.headers,
            "artifact_sha256": stored.sha256,
            "byte_size": stored.byte_size,
        }
        append_jsonl(self.run_dir / "fetch-attempts.jsonl", attempt)
        self.attempts[attempt_id] = attempt

    def fetch_resource(
        self,
        resource: Mapping[str, Any],
        *,
        refresh_successes: bool = False,
    ) -> str:
        if not refresh_successes and self._valid_links(resource["resource_id"]):
            return "resumed_success"
        started_at = utc_now()
        cached = self._cached_discovery_response(resource["source_url"])
        try:
            if cached is not None and not refresh_successes:
                response = cached
                mode = "discovery_cache"
            else:
                response = self.client.get(resource["source_url"])
                mode = "network"
            self._record_success(resource, response, started_at, mode)
            return mode
        except Exception as exc:
            completed_at = utc_now()
            attempt_id = stable_id(
                "fetch-attempt",
                resource["resource_id"],
                started_at,
                "failed",
                str(len(self.attempts)),
            )
            attempt = {
                "schema": FETCH_ATTEMPT_SCHEMA,
                "attempt_id": attempt_id,
                "resource_id": resource["resource_id"],
                "source_url": resource["source_url"],
                "started_at": started_at,
                "completed_at": completed_at,
                "status": "failed",
                "acquisition_mode": "network",
                "error_code": type(exc).__name__,
            }
            append_jsonl(self.run_dir / "fetch-attempts.jsonl", attempt)
            self.attempts[attempt_id] = attempt
            self._record_issue(resource, "resource_fetch_failed")
            raise


def fetch_run(
    plan_path: Path,
    run_dir: Path,
    *,
    timeout_seconds: float = 60.0,
    max_bytes: int = 256 * 1024 * 1024,
    ca_file: str | None = None,
    allow_insecure_tls: bool = False,
    refresh_successes: bool = False,
    client: HttpClient | None = None,
) -> dict[str, Any]:
    plan = SourcePlan.load(plan_path)
    ensure_run_layout(run_dir)
    discovery_path = run_dir / "discovery-manifest.json"
    if not discovery_path.is_file():
        raise ContractError("discovery-manifest.json is required before fetch")
    discovery_manifest = json.loads(discovery_path.read_text(encoding="utf-8"))
    if discovery_manifest.get("source_plan_sha256") != plan.sha256:
        raise ContractError("source plan changed after discovery")
    active_client = client or HttpClient(
        plan.allowed_hosts,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        ca_file=ca_file,
        allow_insecure_tls=allow_insecure_tls,
    )
    recorder = FetchRecorder(run_dir, active_client)
    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    outcomes: dict[str, int] = {}
    for resource in sorted(resources.values(), key=lambda row: row["resource_id"]):
        outcome = recorder.fetch_resource(
            resource, refresh_successes=refresh_successes
        )
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    links = unique_rows(run_dir / "resource-artifact-links.jsonl", "link_id")
    manifest = {
        "schema": RAW_MANIFEST_SCHEMA,
        "source_plan_schema": plan.document["schema"],
        "source_plan_sha256": plan.sha256,
        "capture_cut": plan.document["capture_cut"],
        "completed_at": utc_now(),
        "status": "success",
        "transport": {
            "tls_verification": (
                "disabled_by_explicit_flag"
                if allow_insecure_tls
                else "system_or_custom_trust_store"
            ),
            "custom_ca_bundle": bool(ca_file),
            "timeout_seconds": timeout_seconds,
            "max_bytes": max_bytes,
            "refresh_successes": refresh_successes,
        },
        "counts": {
            "resources": len(resources),
            "artifacts": len(artifacts),
            "resource_artifact_links": len(links),
            "artifact_bytes": sum(row["byte_size"] for row in artifacts.values()),
        },
        "outcomes": outcomes,
        "files": [
            manifest_file_entry(run_dir / filename)
            for filename in (
                "discovery-observations.jsonl",
                "discovered-resources.jsonl",
                "fetch-attempts.jsonl",
                "raw-artifacts.jsonl",
                "resource-artifact-links.jsonl",
                "artifact-url-observations.jsonl",
                "issues.jsonl",
                "discovery-manifest.json",
            )
        ],
    }
    write_json(run_dir / "raw-manifest.json", manifest)
    return manifest
