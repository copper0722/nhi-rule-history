from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERY_MANIFEST_SCHEMA,
    DISCOVERY_OBSERVATION_SCHEMA,
    ISSUE_SCHEMA,
    SourcePlan,
    append_jsonl,
    ensure_run_layout,
    manifest_file_entry,
    stable_id,
    unique_rows,
    utc_now,
    write_json,
)
from nhi_rule_history.discovery.base import DiscoveryContext
from nhi_rule_history.discovery.fint import MohwFintAdapter
from nhi_rule_history.discovery.nhi_current import (
    NhiCurrentChaptersAdapter,
    NhiCurrentWholeAdapter,
)
from nhi_rule_history.discovery.nhi_listing import NhiListingAdapter
from nhi_rule_history.fetch.http import HttpClient
from nhi_rule_history.raw import RawStore


class DiscoveryRecorder:
    def __init__(
        self,
        run_dir: Path,
        plan: SourcePlan,
        client: HttpClient,
    ):
        self.run_dir = run_dir
        self.plan = plan
        self.client = client
        self.store = RawStore(run_dir)
        self.observation_path = run_dir / "discovery-observations.jsonl"
        self.resource_path = run_dir / "discovered-resources.jsonl"
        self.issue_path = run_dir / "issues.jsonl"
        self.resources = unique_rows(self.resource_path, "resource_id")
        self.observations = list(
            unique_rows(self.observation_path, "observation_id").values()
        )

    def _cached_success(self, request_url: str) -> dict[str, Any] | None:
        matches = [
            row
            for row in self.observations
            if row.get("request_url") == request_url and row.get("status") == "success"
        ]
        for row in reversed(matches):
            if self.store.verify(
                row["content_path"],
                row["content_sha256"],
                row["byte_size"],
            ):
                return {
                    "payload": self.store.read(
                        row["content_path"],
                        row["content_sha256"],
                        row["byte_size"],
                    ),
                    "headers": row["response_headers"],
                    "observation": row,
                    "cache_hit": True,
                }
        return None

    def observe(
        self,
        *,
        adapter_id: str,
        request_url: str,
        locator: Mapping[str, Any],
    ) -> dict[str, Any]:
        cached = self._cached_success(request_url)
        if cached:
            return cached
        started_at = utc_now()
        try:
            response = self.client.get(request_url)
        except Exception as exc:
            observation = {
                "schema": DISCOVERY_OBSERVATION_SCHEMA,
                "observation_id": stable_id(
                    "discovery-observation",
                    request_url,
                    started_at,
                    "failed",
                    str(len(self.observations)),
                ),
                "adapter_id": adapter_id,
                "request_url": request_url,
                "locator": dict(locator),
                "status": "failed",
                "observed_at": started_at,
                "error_code": type(exc).__name__,
            }
            append_jsonl(self.observation_path, observation)
            self.observations.append(observation)
            self.record_issue(
                adapter_id=adapter_id,
                source_url=request_url,
                code="discovery_request_failed",
                locator=locator,
            )
            raise
        stored = self.store.put(response.body)
        observation = {
            "schema": DISCOVERY_OBSERVATION_SCHEMA,
            "observation_id": stable_id(
                "discovery-observation",
                request_url,
                stored.sha256,
                started_at,
            ),
            "adapter_id": adapter_id,
            "request_url": response.request_url,
            "final_url": response.final_url,
            "locator": dict(locator),
            "status": "success",
            "observed_at": started_at,
            "http_status": response.status_code,
            "response_headers": response.headers,
            "content_sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "content_path": stored.relative_path,
        }
        append_jsonl(self.observation_path, observation)
        self.observations.append(observation)
        return {
            "payload": response.body,
            "headers": response.headers,
            "observation": observation,
            "cache_hit": False,
        }

    def record_resource(self, row: dict[str, Any]) -> None:
        resource_id = row["resource_id"]
        previous = self.resources.get(resource_id)
        if previous is not None:
            if previous != row:
                raise ContractError(f"conflicting discovery resource: {resource_id}")
            return
        append_jsonl(self.resource_path, row)
        self.resources[resource_id] = row

    def record_issue(
        self,
        *,
        adapter_id: str,
        source_url: str,
        code: str,
        locator: Mapping[str, Any],
    ) -> None:
        row = {
            "schema": ISSUE_SCHEMA,
            "issue_id": stable_id(
                "acquisition-issue",
                adapter_id,
                source_url,
                code,
                utc_now(),
            ),
            "stage": "discovery",
            "severity": "blocking",
            "adapter_id": adapter_id,
            "source_url": source_url,
            "code": code,
            "locator": dict(locator),
            "recorded_at": utc_now(),
        }
        append_jsonl(self.issue_path, row)


def discover_run(
    plan_path: Path,
    run_dir: Path,
    *,
    timeout_seconds: float = 30.0,
    max_bytes: int = 256 * 1024 * 1024,
    ca_file: str | None = None,
    allow_insecure_tls: bool = False,
    client: HttpClient | None = None,
) -> dict[str, Any]:
    plan = SourcePlan.load(plan_path)
    ensure_run_layout(run_dir)
    active_client = client or HttpClient(
        plan.allowed_hosts,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        ca_file=ca_file,
        allow_insecure_tls=allow_insecure_tls,
    )
    recorder = DiscoveryRecorder(run_dir, plan, active_client)
    implementations = {
        "mohw_fint": MohwFintAdapter(),
        "nhi_current_whole": NhiCurrentWholeAdapter(),
        "nhi_chapters": NhiCurrentChaptersAdapter(),
        "nhi_3258": NhiListingAdapter(),
    }
    adapter_results: list[dict[str, Any]] = []
    for adapter_config in plan.adapters:
        if not adapter_config.get("enabled", True):
            adapter_results.append(
                {
                    "adapter_id": adapter_config["id"],
                    "kind": adapter_config["kind"],
                    "status": "declared_disabled",
                }
            )
            continue
        config = dict(adapter_config)
        config["capture_cut"] = plan.document["capture_cut"]
        implementation = implementations[config["kind"]]
        context = DiscoveryContext(plan.sha256, config, active_client, recorder)
        result = implementation.discover(context)
        adapter_results.append({**result, "status": "success"})

    partitions = [
        partition
        for result in adapter_results
        for partition in result.get("partitions", [])
    ]
    manifest = {
        "schema": DISCOVERY_MANIFEST_SCHEMA,
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
        },
        "adapters": adapter_results,
        "parity": {
            "expected_rows": sum(item["expected_rows"] for item in partitions),
            "fetched_rows": sum(item["fetched_rows"] for item in partitions),
        },
        "counts": {
            "observations": len(
                unique_rows(run_dir / "discovery-observations.jsonl", "observation_id")
            ),
            "resources": len(
                unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
            ),
        },
        "files": [
            manifest_file_entry(run_dir / filename)
            for filename in (
                "discovery-observations.jsonl",
                "discovered-resources.jsonl",
                "issues.jsonl",
            )
        ],
    }
    if manifest["parity"]["expected_rows"] != manifest["parity"]["fetched_rows"]:
        raise ContractError("discovery expected/fetched row parity failed")
    write_json(run_dir / "discovery-manifest.json", manifest)
    return manifest
