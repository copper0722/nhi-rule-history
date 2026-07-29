"""Stable public contracts shared by discovery, fetch, and verification.

The acquisition layer intentionally knows nothing about legal effective dates,
stable rule identity, clause lineage, or diffs.  It records official resources
and exact bytes so later, separately gated layers can reason from evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PLAN_SCHEMA = "nhi-rule-history/source-plan/v2"
DISCOVERY_OBSERVATION_SCHEMA = "nhi-rule-history/discovery-observation/v2"
DISCOVERED_RESOURCE_SCHEMA = "nhi-rule-history/discovered-resource/v2"
FETCH_ATTEMPT_SCHEMA = "nhi-rule-history/fetch-attempt/v2"
RAW_ARTIFACT_SCHEMA = "nhi-rule-history/raw-artifact/v2"
RESOURCE_ARTIFACT_LINK_SCHEMA = "nhi-rule-history/resource-artifact-link/v2"
ARTIFACT_URL_OBSERVATION_SCHEMA = "nhi-rule-history/artifact-url-observation/v2"
ISSUE_SCHEMA = "nhi-rule-history/acquisition-issue/v2"
DISCOVERY_MANIFEST_SCHEMA = "nhi-rule-history/discovery-manifest/v2"
RAW_MANIFEST_SCHEMA = "nhi-rule-history/raw-manifest/v2"
WORKER_ATTEMPT_SCHEMA = "nhi-rule-history/worker-attempt/v1"

JSONL_FILES = (
    "discovery-observations.jsonl",
    "discovered-resources.jsonl",
    "fetch-attempts.jsonl",
    "raw-artifacts.jsonl",
    "resource-artifact-links.jsonl",
    "artifact-url-observations.jsonl",
    "issues.jsonl",
)

_SCHEMA_BY_FILE = {
    "discovery-observations.jsonl": DISCOVERY_OBSERVATION_SCHEMA,
    "discovered-resources.jsonl": DISCOVERED_RESOURCE_SCHEMA,
    "fetch-attempts.jsonl": FETCH_ATTEMPT_SCHEMA,
    "raw-artifacts.jsonl": RAW_ARTIFACT_SCHEMA,
    "resource-artifact-links.jsonl": RESOURCE_ARTIFACT_LINK_SCHEMA,
    "artifact-url-observations.jsonl": ARTIFACT_URL_OBSERVATION_SCHEMA,
    "issues.jsonl": ISSUE_SCHEMA,
}

ROW_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    DISCOVERY_OBSERVATION_SCHEMA: frozenset(
        {
            "schema",
            "observation_id",
            "adapter_id",
            "request_url",
            "final_url",
            "locator",
            "status",
            "observed_at",
            "http_status",
            "response_headers",
            "content_sha256",
            "byte_size",
            "content_path",
            "error_code",
        }
    ),
    DISCOVERED_RESOURCE_SCHEMA: frozenset(
        {
            "schema",
            "resource_id",
            "adapter_id",
            "resource_kind",
            "source_url",
            "parent_resource_id",
            "discovery_locator",
            "source_label",
            "official_document_number_raw",
            "fetch_state",
        }
    ),
    FETCH_ATTEMPT_SCHEMA: frozenset(
        {
            "schema",
            "attempt_id",
            "resource_id",
            "source_url",
            "started_at",
            "completed_at",
            "status",
            "acquisition_mode",
            "http_status",
            "final_url",
            "response_headers",
            "artifact_sha256",
            "byte_size",
            "error_code",
        }
    ),
    RAW_ARTIFACT_SCHEMA: frozenset(
        {
            "schema",
            "artifact_sha256",
            "byte_size",
            "content_path",
            "media_type",
            "first_observed_at",
        }
    ),
    RESOURCE_ARTIFACT_LINK_SCHEMA: frozenset(
        {
            "schema",
            "link_id",
            "resource_id",
            "artifact_sha256",
            "relation",
            "observed_at",
        }
    ),
    ARTIFACT_URL_OBSERVATION_SCHEMA: frozenset(
        {
            "schema",
            "url_observation_id",
            "resource_id",
            "source_url",
            "artifact_sha256",
            "relation_to_previous",
            "observed_at",
            "previous_artifact_sha256",
        }
    ),
    ISSUE_SCHEMA: frozenset(
        {
            "schema",
            "issue_id",
            "stage",
            "severity",
            "adapter_id",
            "resource_id",
            "source_url",
            "code",
            "locator",
            "recorded_at",
        }
    ),
    WORKER_ATTEMPT_SCHEMA: frozenset(
        {
            "schema",
            "attempt_id",
            "attempt_namespace",
            "role",
            "worker_id",
            "runtime_id",
            "provider",
            "model",
            "prompt_version",
            "prompt_sha256",
            "started_at",
            "completed_at",
            "status",
            "primary_attempt_id",
            "fallback_reason",
            "exit_code",
            "output_sha256",
            "stderr_sha256",
            "validation_error_code",
            "candidate_id",
        }
    ),
}

ROW_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    DISCOVERY_OBSERVATION_SCHEMA: frozenset(
        {
            "schema",
            "observation_id",
            "adapter_id",
            "request_url",
            "locator",
            "status",
            "observed_at",
        }
    ),
    DISCOVERED_RESOURCE_SCHEMA: frozenset(
        {
            "schema",
            "resource_id",
            "adapter_id",
            "resource_kind",
            "source_url",
            "discovery_locator",
            "source_label",
            "fetch_state",
        }
    ),
    FETCH_ATTEMPT_SCHEMA: frozenset(
        {
            "schema",
            "attempt_id",
            "resource_id",
            "source_url",
            "started_at",
            "completed_at",
            "status",
            "acquisition_mode",
        }
    ),
    RAW_ARTIFACT_SCHEMA: frozenset(
        {
            "schema",
            "artifact_sha256",
            "byte_size",
            "content_path",
            "media_type",
            "first_observed_at",
        }
    ),
    RESOURCE_ARTIFACT_LINK_SCHEMA: frozenset(
        {
            "schema",
            "link_id",
            "resource_id",
            "artifact_sha256",
            "relation",
            "observed_at",
        }
    ),
    ARTIFACT_URL_OBSERVATION_SCHEMA: frozenset(
        {
            "schema",
            "url_observation_id",
            "resource_id",
            "source_url",
            "artifact_sha256",
            "relation_to_previous",
            "observed_at",
        }
    ),
    ISSUE_SCHEMA: frozenset(
        {
            "schema",
            "issue_id",
            "stage",
            "severity",
            "adapter_id",
            "source_url",
            "code",
            "recorded_at",
        }
    ),
    WORKER_ATTEMPT_SCHEMA: frozenset(
        {
            "schema",
            "attempt_id",
            "role",
            "worker_id",
            "runtime_id",
            "provider",
            "model",
            "prompt_version",
            "prompt_sha256",
            "started_at",
            "completed_at",
            "status",
            "primary_attempt_id",
            "fallback_reason",
            "exit_code",
            "output_sha256",
            "stderr_sha256",
            "validation_error_code",
        }
    ),
}

_SECRET_KEY = re.compile(
    r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|dsn)",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """Raised when public acquisition data violates a fail-closed contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_id(namespace: str, *parts: str) -> str:
    payload = "\x1f".join((namespace, *parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"https", "http"}:
        raise ContractError(f"unsupported URL scheme: {parts.scheme!r}")
    if not parts.hostname or parts.username or parts.password:
        raise ContractError("URL must have a hostname and no userinfo")
    host = parts.hostname.lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def assert_allowed_url(raw: str, allowed_hosts: Iterable[str]) -> str:
    normalized = canonical_url(raw)
    hostname = (urlsplit(normalized).hostname or "").lower()
    allowed = {item.lower() for item in allowed_hosts}
    if hostname not in allowed:
        raise ContractError(f"host not allowed by source plan: {hostname}")
    return normalized


def assert_public_value(value: Any, path: str = "$") -> None:
    """Reject credentials, local absolute paths, and non-JSON values.

    Official URLs may naturally use absolute URL paths; only standalone local
    filesystem-like strings are rejected.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ContractError(f"secret-like field is forbidden at {path}.{key}")
            assert_public_value(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_public_value(child, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if not isinstance(value, str):
        raise ContractError(f"non-JSON value at {path}: {type(value).__name__}")
    if value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\/]", value):
        raise ContractError(f"local absolute path is forbidden at {path}")
    lowered = value.lower()
    if lowered.startswith(("file://", "postgres://", "postgresql://")):
        raise ContractError(f"private locator is forbidden at {path}")


def validate_jsonl_row(row: Mapping[str, Any]) -> None:
    schema = row.get("schema")
    allowed = ROW_ALLOWED_FIELDS.get(schema)
    required = ROW_REQUIRED_FIELDS.get(schema)
    if allowed is None or required is None:
        raise ContractError(f"unknown JSONL row schema: {schema!r}")
    unknown = set(row) - allowed
    missing = required - set(row)
    if unknown:
        raise ContractError(f"{schema}: unknown fields: {sorted(unknown)}")
    if missing:
        raise ContractError(f"{schema}: missing fields: {sorted(missing)}")
    assert_public_value(row)
    if schema == DISCOVERY_OBSERVATION_SCHEMA:
        if row["status"] == "success":
            expected = {
                "final_url",
                "http_status",
                "response_headers",
                "content_sha256",
                "byte_size",
                "content_path",
            }
        elif row["status"] == "failed":
            expected = {"error_code"}
        else:
            raise ContractError("discovery observation status is invalid")
        if not expected <= set(row):
            raise ContractError("discovery observation status fields are incomplete")
    if schema == FETCH_ATTEMPT_SCHEMA:
        if row["status"] == "success":
            expected = {
                "http_status",
                "final_url",
                "response_headers",
                "artifact_sha256",
                "byte_size",
            }
        elif row["status"] == "failed":
            expected = {"error_code"}
        else:
            raise ContractError("fetch attempt status is invalid")
        if not expected <= set(row):
            raise ContractError("fetch attempt status fields are incomplete")
    if schema == WORKER_ATTEMPT_SCHEMA:
        if "attempt_namespace" in row and (
            not isinstance(row["attempt_namespace"], str)
            or not row["attempt_namespace"]
        ):
            raise ContractError(
                "worker attempt namespace must be a non-empty string"
            )
        if row["role"] not in {"primary", "fallback"}:
            raise ContractError("worker attempt role is invalid")
        if row["status"] not in {
            "validated",
            "execution_failed",
            "contract_failed",
            "timeout",
            "transport_failed",
            "execution_unknown",
        }:
            raise ContractError("worker attempt status is invalid")
        if row["role"] == "primary" and (
            row["primary_attempt_id"] is not None
            or row["fallback_reason"] is not None
        ):
            raise ContractError("primary attempt cannot reference fallback fields")
        if row["role"] == "fallback" and (
            not row["primary_attempt_id"] or not row["fallback_reason"]
        ):
            raise ContractError("fallback attempt must reference primary failure")
        if row["status"] == "validated" and not row.get("candidate_id"):
            raise ContractError("validated worker attempt requires candidate_id")


def relative_blob_path(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ContractError("artifact digest must be lowercase SHA-256")
    return f"raw/sha256/{digest[:2]}/{digest}"


def resolve_run_relative(run_dir: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ContractError("run-relative path escapes run directory")
    root = run_dir.resolve()
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise ContractError("run-relative path escapes run directory")
    return resolved


def ensure_run_layout(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for filename in JSONL_FILES:
        (run_dir / filename).touch(exist_ok=True)
    (run_dir / "raw" / "sha256").mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    expected = _SCHEMA_BY_FILE.get(path.name)
    if expected and row.get("schema") != expected:
        raise ContractError(
            f"{path.name} requires schema={expected!r}, got {row.get('schema')!r}"
        )
    validate_jsonl_row(row)
    payload = canonical_json_bytes(dict(row))
    with path.open("ab") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"{path.name}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ContractError(f"{path.name}:{line_number}: row is not an object")
            expected = _SCHEMA_BY_FILE.get(path.name)
            if expected and row.get("schema") != expected:
                raise ContractError(
                    f"{path.name}:{line_number}: wrong schema {row.get('schema')!r}"
                )
            try:
                validate_jsonl_row(row)
            except ContractError as exc:
                raise ContractError(f"{path.name}:{line_number}: {exc}") from exc
            yield row


def unique_rows(path: Path, key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ContractError(f"{path.name}: missing string key {key!r}")
        previous = result.get(value)
        if previous is not None and previous != row:
            raise ContractError(f"{path.name}: conflicting duplicate {key}={value}")
        result[value] = row
    return result


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    assert_public_value(value)
    payload = canonical_json_bytes(dict(value))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourcePlan:
    document: dict[str, Any]

    @property
    def capture_cut(self) -> date:
        return date.fromisoformat(self.document["capture_cut"])

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return tuple(self.document["allowed_hosts"])

    @property
    def adapters(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.document["adapters"])

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.document))

    @classmethod
    def load(cls, path: Path) -> "SourcePlan":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot load source plan: {path}") from exc
        if not isinstance(document, dict):
            raise ContractError("source plan must be a JSON object")
        if document.get("schema") != PLAN_SCHEMA:
            raise ContractError(f"source plan schema must be {PLAN_SCHEMA}")
        try:
            capture_cut = date.fromisoformat(document["capture_cut"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError("source plan capture_cut must be an ISO date") from exc
        if capture_cut < date(2021, 1, 1):
            raise ContractError("capture_cut predates the declared v2 acquisition window")
        hosts = document.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts or not all(
            isinstance(item, str) and item for item in hosts
        ):
            raise ContractError("allowed_hosts must be a non-empty string array")
        adapters = document.get("adapters")
        if not isinstance(adapters, list) or not adapters:
            raise ContractError("adapters must be a non-empty array")
        ids: set[str] = set()
        for adapter in adapters:
            if not isinstance(adapter, dict):
                raise ContractError("each adapter must be an object")
            adapter_id = adapter.get("id")
            kind = adapter.get("kind")
            if not isinstance(adapter_id, str) or not adapter_id or adapter_id in ids:
                raise ContractError("adapter ids must be unique non-empty strings")
            if kind not in {"mohw_fint", "nhi_current_whole", "nhi_chapters", "nhi_3258"}:
                raise ContractError(f"unsupported adapter kind: {kind!r}")
            ids.add(adapter_id)
            if adapter.get("enabled", True):
                assert_allowed_url(adapter["base_url"], hosts)
        assert_public_value(document)
        return cls(document=document)


def manifest_file_entry(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }
