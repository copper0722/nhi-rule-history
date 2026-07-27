"""Offline expansion of verified NHI listing detail pages into attachments.

This stage has deliberately narrow semantics:

* the upstream raw acquisition must remain immutable and pass ``verify_raw``;
* every expected listing detail must have exactly one verified HTML binding;
* attachment links are source-local observations, not amendment events;
* output is deterministic and contains no wall-clock, network, PostgreSQL, or
  legal-history work.

The immutable expansion directory is not itself mutated by a later fetch.
``materialize_attachment_fetch_run`` copies its standard discovery projection
to a fresh acquisition run that the existing ``fetch_run`` function can use
with the unchanged listing source plan.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    DISCOVERY_MANIFEST_SCHEMA,
    assert_allowed_url,
    assert_public_value,
    canonical_json_bytes,
    canonical_url,
    ensure_run_layout,
    file_sha256,
    iter_jsonl,
    manifest_file_entry,
    sha256_bytes,
    stable_id,
    unique_rows,
    validate_jsonl_row,
    write_json,
)
from nhi_rule_history.discovery.nhi_listing import (
    DETAIL_PATH_RE,
    LISTING_PATH_RE,
)
from nhi_rule_history.raw import RawStore
from nhi_rule_history.raw.verify import verify_raw
from nhi_rule_history.update.rss import (
    NHI_ALLOWED_HOSTS,
    parse_attachment_links,
)


PARSER_VERSION = "nhi-listing-detail-expansion/1.0.0"
DEFAULT_EXPECTED_DETAIL_COUNT = 858
DETAIL_SCHEMA = "nhi-rule-history/nhi-listing-detail-provenance/v1"
OCCURRENCE_SCHEMA = "nhi-rule-history/nhi-listing-attachment-occurrence/v1"
MANIFEST_SCHEMA = "nhi-rule-history/nhi-listing-detail-expansion/v1"
SURFACE = "nhi_amendment_listing_detail_attachments"
NON_CLAIM = (
    "Offline source-local attachment discovery only; listing dates and links "
    "do not establish legal effect, rule identity, lineage, predecessor, diff, "
    "or history completeness."
)

_RAW_MANIFESTED_FILES = frozenset(
    {
        "discovery-observations.jsonl",
        "discovered-resources.jsonl",
        "fetch-attempts.jsonl",
        "raw-artifacts.jsonl",
        "resource-artifact-links.jsonl",
        "artifact-url-observations.jsonl",
        "issues.jsonl",
        "discovery-manifest.json",
    }
)
_EXPANSION_DATA_FILES = (
    "detail-provenance.jsonl",
    "attachment-occurrences.jsonl",
    "discovery-observations.jsonl",
    "discovered-resources.jsonl",
    "issues.jsonl",
)
_EXPANSION_FILES = (
    *_EXPANSION_DATA_FILES,
    "discovery-manifest.json",
    "detail-expansion-manifest.json",
)
_FETCH_PROJECTION_FILES = (
    "discovery-observations.jsonl",
    "discovered-resources.jsonl",
    "issues.jsonl",
    "discovery-manifest.json",
)


@dataclass(frozen=True)
class _DerivedExpansion:
    raw_manifest: dict[str, Any]
    raw_manifest_sha256: str
    parser_code_sha256: str
    input_fingerprint: str
    expansion_id: str
    adapter_id: str
    detail_rows: tuple[dict[str, Any], ...]
    occurrence_rows: tuple[dict[str, Any], ...]
    attachment_rows: tuple[dict[str, Any], ...]


def _fail(message: str) -> None:
    raise ContractError(message)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is missing or invalid JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payloads: list[bytes] = []
    for row in rows:
        assert_public_value(row)
        payloads.append(canonical_json_bytes(dict(row)))
    path.write_bytes(b"".join(payloads))


def _row_set_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    hashes = sorted(
        sha256_bytes(canonical_json_bytes(dict(row)))
        for row in rows
    )
    return sha256_bytes(canonical_json_bytes(hashes))


def _validate_output_target(
    input_dirs: Iterable[Path],
    output_dir: Path,
) -> None:
    output = output_dir.resolve()
    if output_dir.exists():
        _fail("output directory already exists")
    for input_dir in input_dirs:
        source = input_dir.resolve()
        if source == output or source in output.parents:
            _fail("output directory must not mutate an input directory")


def _validate_listing_locator(
    resource: Mapping[str, Any],
    *,
    expected_detail_count: int,
) -> tuple[int, dict[str, Any]]:
    locator = resource.get("discovery_locator")
    if not isinstance(locator, dict):
        _fail("listing detail resource has no object discovery locator")
    if locator.get("surface") != "nhi_amendment_listing_3258":
        _fail("listing detail resource has an unexpected discovery surface")
    source_url = resource["source_url"]
    if locator.get("stable_row_identity") != f"url:{source_url}":
        _fail("listing detail stable row identity does not match its URL")
    displayed_ordinal = locator.get("displayed_ordinal")
    if (
        not isinstance(displayed_ordinal, int)
        or isinstance(displayed_ordinal, bool)
        or not 1 <= displayed_ordinal <= expected_detail_count
    ):
        _fail("listing detail displayed ordinal is outside the denominator")
    document_number = locator.get("document_number_raw")
    if not isinstance(document_number, str) or not document_number.strip():
        _fail("listing detail document-number locator is empty")
    occurrences = locator.get("listing_occurrences")
    if not isinstance(occurrences, list) or len(occurrences) != 1:
        _fail("listing detail must have exactly one listing occurrence")
    occurrence = occurrences[0]
    if not isinstance(occurrence, dict):
        _fail("listing occurrence locator must be an object")
    if set(occurrence) != {
        "listing_page_url",
        "page_number",
        "row_ordinal",
    }:
        _fail("listing occurrence locator fields are incomplete or ambiguous")
    page_number = occurrence["page_number"]
    row_ordinal = occurrence["row_ordinal"]
    if (
        not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
        or not isinstance(row_ordinal, int)
        or isinstance(row_ordinal, bool)
        or not 1 <= row_ordinal <= 20
    ):
        _fail("listing occurrence page/row locator is invalid")
    if displayed_ordinal != (page_number - 1) * 20 + row_ordinal:
        _fail("listing occurrence order disagrees with displayed ordinal")
    page_url = assert_allowed_url(
        occurrence["listing_page_url"],
        NHI_ALLOWED_HOSTS,
    )
    parts = urlsplit(page_url)
    if (
        parts.scheme != "https"
        or LISTING_PATH_RE.fullmatch(parts.path) is None
    ):
        _fail("listing occurrence URL is not the official listing surface")
    if canonical_url(occurrence["listing_page_url"]) != page_url:
        _fail("listing occurrence URL is not canonical")
    return displayed_ordinal, dict(locator)


def _derive_expansion(
    raw_run_dir: Path,
    *,
    expected_detail_count: int,
) -> _DerivedExpansion:
    if (
        not isinstance(expected_detail_count, int)
        or isinstance(expected_detail_count, bool)
        or expected_detail_count < 1
    ):
        _fail("expected_detail_count must be a positive integer")
    raw_run_dir = Path(raw_run_dir)
    verification = verify_raw(raw_run_dir)
    if verification.get("status") != "passed":
        _fail("upstream raw acquisition did not pass verification")
    raw_manifest_path = raw_run_dir / "raw-manifest.json"
    raw_manifest = _load_json_object(raw_manifest_path, "raw manifest")
    if raw_manifest.get("status") != "success":
        _fail("upstream raw manifest is not successful")
    manifested_names = {
        entry.get("filename")
        for entry in raw_manifest.get("files", [])
        if isinstance(entry, dict)
    }
    if manifested_names != _RAW_MANIFESTED_FILES:
        _fail("upstream raw manifest does not bind the exact acquisition files")
    if any(iter_jsonl(raw_run_dir / "issues.jsonl")):
        _fail("upstream raw acquisition contains issues")

    resources = unique_rows(
        raw_run_dir / "discovered-resources.jsonl",
        "resource_id",
    )
    artifacts = unique_rows(
        raw_run_dir / "raw-artifacts.jsonl",
        "artifact_sha256",
    )
    links = list(iter_jsonl(raw_run_dir / "resource-artifact-links.jsonl"))
    if len(resources) != expected_detail_count:
        _fail("listing detail denominator differs from expectation")
    counts = raw_manifest.get("counts")
    if not isinstance(counts, dict) or (
        counts.get("resources") != expected_detail_count
        or counts.get("resource_artifact_links") != expected_detail_count
    ):
        _fail("raw-manifest detail/link counts do not match the denominator")

    links_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        links_by_resource[link["resource_id"]].append(link)

    adapter_ids: set[str] = set()
    detail_inputs: list[
        tuple[int, dict[str, Any], dict[str, Any], bytes]
    ] = []
    raw_store = RawStore(raw_run_dir)
    for resource in resources.values():
        if resource.get("resource_kind") != "official_detail_page":
            _fail("raw run contains a non-detail resource")
        adapter_id = resource.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id:
            _fail("listing detail adapter id is empty")
        adapter_ids.add(adapter_id)
        source_url = assert_allowed_url(
            resource["source_url"],
            NHI_ALLOWED_HOSTS,
        )
        parts = urlsplit(source_url)
        if (
            parts.scheme != "https"
            or DETAIL_PATH_RE.fullmatch(parts.path) is None
            or parts.query
            or canonical_url(resource["source_url"]) != source_url
        ):
            _fail("listing detail URL is not a canonical official detail URL")
        if not isinstance(resource.get("source_label"), str) or not resource[
            "source_label"
        ].strip():
            _fail("listing detail source label is empty")
        displayed_ordinal, locator = _validate_listing_locator(
            resource,
            expected_detail_count=expected_detail_count,
        )
        resource_links = links_by_resource.get(resource["resource_id"], [])
        if len(resource_links) != 1:
            _fail("listing detail does not have exactly one artifact binding")
        link = resource_links[0]
        artifact = artifacts.get(link["artifact_sha256"])
        if artifact is None:
            _fail("listing detail binding references an unknown artifact")
        if artifact.get("media_type") != "text/html":
            _fail("listing detail artifact is not verified as text/html")
        payload = raw_store.read(
            artifact["content_path"],
            artifact["artifact_sha256"],
            artifact["byte_size"],
        )
        detail_inputs.append(
            (
                displayed_ordinal,
                dict(resource),
                {
                    **dict(artifact),
                    "parent_discovery_locator": locator,
                },
                payload,
            )
        )

    if len(adapter_ids) != 1:
        _fail("listing detail run contains ambiguous adapter identities")
    ordinals = sorted(item[0] for item in detail_inputs)
    if ordinals != list(range(1, expected_detail_count + 1)):
        _fail("listing detail displayed ordinals do not close the denominator")

    raw_manifest_sha256 = file_sha256(raw_manifest_path)
    parser_code_sha256 = file_sha256(Path(__file__))
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "parser_version": PARSER_VERSION,
                "parser_code_sha256": parser_code_sha256,
                "raw_manifest_sha256": raw_manifest_sha256,
                "source_plan_sha256": raw_manifest["source_plan_sha256"],
                "expected_detail_count": expected_detail_count,
                "detail_resource_ids": sorted(resources),
                "detail_artifact_bindings": sorted(
                    [
                        link["resource_id"],
                        link["artifact_sha256"],
                    ]
                    for link in links
                ),
                "non_claim": NON_CLAIM,
            }
        )
    )
    expansion_id = sha256_bytes(
        canonical_json_bytes(
            ["nhi-listing-detail-expansion", input_fingerprint]
        )
    )

    detail_rows: list[dict[str, Any]] = []
    occurrence_rows: list[dict[str, Any]] = []
    occurrence_order = 0
    for displayed_ordinal, resource, artifact, payload in sorted(
        detail_inputs,
        key=lambda item: item[0],
    ):
        attachment_links = parse_attachment_links(
            resource["source_url"],
            payload,
            require_nonempty=False,
        )
        parent_locator = artifact["parent_discovery_locator"]
        detail_rows.append(
            {
                "schema": DETAIL_SCHEMA,
                "expansion_id": expansion_id,
                "displayed_ordinal": displayed_ordinal,
                "parent_resource_id": resource["resource_id"],
                "parent_source_url": resource["source_url"],
                "parent_source_label": resource["source_label"],
                "parent_document_number_raw": parent_locator[
                    "document_number_raw"
                ],
                "parent_discovery_locator": parent_locator,
                "parent_artifact_sha256": artifact["artifact_sha256"],
                "parent_artifact_byte_size": artifact["byte_size"],
                "parent_artifact_content_path": artifact["content_path"],
                "attachment_count": len(attachment_links),
                "zero_attachment": not attachment_links,
            }
        )
        for attachment in attachment_links:
            occurrence_order += 1
            attachment_resource_id = stable_id(
                "nhi-listing-attachment",
                attachment.url,
            )
            occurrence_id = stable_id(
                "nhi-listing-attachment-occurrence",
                expansion_id,
                resource["resource_id"],
                str(attachment.sequence),
                attachment.url,
            )
            occurrence_rows.append(
                {
                    "schema": OCCURRENCE_SCHEMA,
                    "occurrence_id": occurrence_id,
                    "expansion_id": expansion_id,
                    "global_occurrence_ordinal": occurrence_order,
                    "source_attachment_sequence": attachment.sequence,
                    "attachment_resource_id": attachment_resource_id,
                    "attachment_url": attachment.url,
                    "attachment_label": attachment.label,
                    "parent_resource_id": resource["resource_id"],
                    "parent_source_url": resource["source_url"],
                    "parent_source_label": resource["source_label"],
                    "parent_document_number_raw": parent_locator[
                        "document_number_raw"
                    ],
                    "parent_discovery_locator": parent_locator,
                    "parent_artifact_sha256": artifact["artifact_sha256"],
                }
            )

    occurrences_by_attachment: dict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    for occurrence in occurrence_rows:
        occurrences_by_attachment[
            occurrence["attachment_resource_id"]
        ].append(occurrence)

    attachment_rows: list[dict[str, Any]] = []
    for attachment_resource_id in sorted(occurrences_by_attachment):
        occurrences = occurrences_by_attachment[attachment_resource_id]
        first = occurrences[0]
        parent_occurrences = [
            {
                "occurrence_id": row["occurrence_id"],
                "global_occurrence_ordinal": row[
                    "global_occurrence_ordinal"
                ],
                "source_attachment_sequence": row[
                    "source_attachment_sequence"
                ],
                "attachment_label": row["attachment_label"],
                "parent_resource_id": row["parent_resource_id"],
                "parent_source_url": row["parent_source_url"],
                "parent_source_label": row["parent_source_label"],
                "parent_document_number_raw": row[
                    "parent_document_number_raw"
                ],
                "parent_discovery_locator": row[
                    "parent_discovery_locator"
                ],
                "parent_artifact_sha256": row[
                    "parent_artifact_sha256"
                ],
            }
            for row in occurrences
        ]
        row = {
            "schema": DISCOVERED_RESOURCE_SCHEMA,
            "resource_id": attachment_resource_id,
            "adapter_id": next(iter(adapter_ids)),
            "resource_kind": "official_attachment",
            "source_url": first["attachment_url"],
            "discovery_locator": {
                "surface": SURFACE,
                "upstream_raw_manifest_sha256": raw_manifest_sha256,
                "expansion_id": expansion_id,
                "parent_occurrences": parent_occurrences,
            },
            "source_label": (
                first["attachment_label"]
                or first["attachment_url"]
            ),
            "fetch_state": "pending",
        }
        validate_jsonl_row(row)
        attachment_rows.append(row)

    return _DerivedExpansion(
        raw_manifest=raw_manifest,
        raw_manifest_sha256=raw_manifest_sha256,
        parser_code_sha256=parser_code_sha256,
        input_fingerprint=input_fingerprint,
        expansion_id=expansion_id,
        adapter_id=next(iter(adapter_ids)),
        detail_rows=tuple(detail_rows),
        occurrence_rows=tuple(occurrence_rows),
        attachment_rows=tuple(attachment_rows),
    )


def _manifest_counts(derived: _DerivedExpansion) -> dict[str, int]:
    attachment_counts = [
        row["attachment_count"] for row in derived.detail_rows
    ]
    return {
        "detail_resources": len(derived.detail_rows),
        "detail_artifacts": len(
            {
                row["parent_artifact_sha256"]
                for row in derived.detail_rows
            }
        ),
        "zero_attachment_details": sum(
            row["zero_attachment"] for row in derived.detail_rows
        ),
        "attachment_occurrences": len(derived.occurrence_rows),
        "attachment_resources": len(derived.attachment_rows),
        "cross_detail_duplicate_occurrences": (
            len(derived.occurrence_rows) - len(derived.attachment_rows)
        ),
        "max_attachments_per_detail": max(attachment_counts, default=0),
    }


def _discovery_manifest(
    derived: _DerivedExpansion,
    stage_dir: Path,
) -> dict[str, Any]:
    counts = _manifest_counts(derived)
    return {
        "schema": DISCOVERY_MANIFEST_SCHEMA,
        "source_plan_schema": derived.raw_manifest["source_plan_schema"],
        "source_plan_sha256": derived.raw_manifest["source_plan_sha256"],
        "capture_cut": derived.raw_manifest["capture_cut"],
        "completed_at": derived.raw_manifest["completed_at"],
        "status": "success",
        "transport": {
            "mode": "offline_verified_detail_expansion",
            "network_requested": False,
            "postgresql_written": False,
        },
        "timestamp_basis": (
            "upstream raw-manifest completed_at reused for deterministic replay"
        ),
        "adapters": [
            {
                "adapter_id": derived.adapter_id,
                "kind": "nhi_3258",
                "surface": SURFACE,
                "status": "success",
                "mode": "offline_verified_detail_expansion",
                "upstream_detail_resources": counts["detail_resources"],
                "attachment_occurrences": counts["attachment_occurrences"],
                "recorded_attachment_resources": counts[
                    "attachment_resources"
                ],
            }
        ],
        "parity": {
            "expected_rows": counts["attachment_resources"],
            "fetched_rows": counts["attachment_resources"],
        },
        "counts": {
            "observations": 0,
            "resources": counts["attachment_resources"],
        },
        "files": [
            manifest_file_entry(stage_dir / filename)
            for filename in (
                "discovery-observations.jsonl",
                "discovered-resources.jsonl",
                "issues.jsonl",
            )
        ],
        "upstream_binding": {
            "raw_manifest_sha256": derived.raw_manifest_sha256,
            "input_fingerprint": derived.input_fingerprint,
            "expansion_id": derived.expansion_id,
            "detail_resources": counts["detail_resources"],
        },
        "closure_claims": {
            "sealed_detail_denominator_exhausted": True,
            "exactly_one_verified_html_binding_per_detail": True,
            "all_source_attachment_occurrences_retained": True,
            "attachment_resources_deduplicated_by_canonical_url": True,
            "legal_semantics_inferred": False,
            "history_complete": False,
        },
        "statement": NON_CLAIM,
    }


def expand_nhi_listing_details(
    raw_run_dir: Path,
    stage_dir: Path,
    *,
    expected_detail_count: int = DEFAULT_EXPECTED_DETAIL_COUNT,
) -> dict[str, Any]:
    """Create one immutable, deterministic detail-expansion stage."""

    raw_run_dir = Path(raw_run_dir)
    stage_dir = Path(stage_dir)
    _validate_output_target((raw_run_dir,), stage_dir)
    derived = _derive_expansion(
        raw_run_dir,
        expected_detail_count=expected_detail_count,
    )
    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{stage_dir.name}.",
            dir=stage_dir.parent,
        )
    )
    try:
        _write_jsonl(
            temporary / "detail-provenance.jsonl",
            derived.detail_rows,
        )
        _write_jsonl(
            temporary / "attachment-occurrences.jsonl",
            derived.occurrence_rows,
        )
        (temporary / "discovery-observations.jsonl").write_bytes(b"")
        _write_jsonl(
            temporary / "discovered-resources.jsonl",
            derived.attachment_rows,
        )
        (temporary / "issues.jsonl").write_bytes(b"")
        discovery_manifest = _discovery_manifest(derived, temporary)
        write_json(
            temporary / "discovery-manifest.json",
            discovery_manifest,
        )
        counts = _manifest_counts(derived)
        row_set_fingerprints = {
            "detail_provenance": _row_set_fingerprint(
                derived.detail_rows
            ),
            "attachment_occurrence": _row_set_fingerprint(
                derived.occurrence_rows
            ),
            "attachment_resource": _row_set_fingerprint(
                derived.attachment_rows
            ),
        }
        files = [
            manifest_file_entry(temporary / filename)
            for filename in (
                *_EXPANSION_DATA_FILES,
                "discovery-manifest.json",
            )
        ]
        output_fingerprint = sha256_bytes(
            canonical_json_bytes(
                {
                    "counts": counts,
                    "row_set_fingerprints": row_set_fingerprints,
                    "files": files,
                }
            )
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "status": "passed",
            "parser_version": PARSER_VERSION,
            "parser_code_sha256": derived.parser_code_sha256,
            "expansion_id": derived.expansion_id,
            "source_plan_sha256": derived.raw_manifest[
                "source_plan_sha256"
            ],
            "raw_manifest_sha256": derived.raw_manifest_sha256,
            "expected_detail_count": expected_detail_count,
            "input_fingerprint": derived.input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "counts": counts,
            "row_set_fingerprints": row_set_fingerprints,
            "files": files,
            "fetch_projection": {
                "status": "materializable",
                "required_source_plan_sha256": derived.raw_manifest[
                    "source_plan_sha256"
                ],
                "materialization_api": (
                    "materialize_attachment_fetch_run"
                ),
                "mutates_upstream_raw_run": False,
                "mutates_expansion_stage": False,
            },
            "closure_claims": {
                "sealed_detail_denominator_exhausted": (
                    counts["detail_resources"]
                    == expected_detail_count
                ),
                "exactly_one_verified_html_binding_per_detail": True,
                "zero_attachment_details_preserved": True,
                "all_source_attachment_occurrences_retained": True,
                "attachment_resources_deduplicated_by_canonical_url": True,
                "network_requested": False,
                "postgresql_written": False,
                "legal_semantics_inferred": False,
                "history_complete": False,
            },
            "statement": NON_CLAIM,
        }
        write_json(
            temporary / "detail-expansion-manifest.json",
            manifest,
        )
        os.replace(temporary, stage_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def verify_nhi_listing_detail_expansion(
    raw_run_dir: Path,
    stage_dir: Path,
    *,
    expected_detail_count: int = DEFAULT_EXPECTED_DETAIL_COUNT,
) -> dict[str, Any]:
    """Rebuild the stage independently and require byte-identical output."""

    raw_run_dir = Path(raw_run_dir)
    stage_dir = Path(stage_dir)
    if not stage_dir.is_dir():
        _fail("detail expansion stage is missing")
    actual_files = sorted(
        str(path.relative_to(stage_dir))
        for path in stage_dir.rglob("*")
        if path.is_file()
    )
    if actual_files != sorted(_EXPANSION_FILES):
        _fail("detail expansion stage file set is incomplete or ambiguous")

    manifest = _load_json_object(
        stage_dir / "detail-expansion-manifest.json",
        "detail expansion manifest",
    )
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "passed"
        or manifest.get("expected_detail_count") != expected_detail_count
    ):
        _fail("detail expansion manifest contract mismatch")
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict):
            _fail("detail expansion file receipt is malformed")
        path = stage_dir / entry.get("filename", "")
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            _fail("detail expansion manifested file changed")

    container = Path(
        tempfile.mkdtemp(
            prefix=f".{stage_dir.name}.verify.",
            dir=stage_dir.parent,
        )
    )
    expected = container / "expected"
    try:
        expand_nhi_listing_details(
            raw_run_dir,
            expected,
            expected_detail_count=expected_detail_count,
        )
        for filename in _EXPANSION_FILES:
            if (stage_dir / filename).read_bytes() != (
                expected / filename
            ).read_bytes():
                _fail(
                    "detail expansion is not a byte-identical deterministic replay"
                )
    finally:
        shutil.rmtree(container, ignore_errors=True)

    return {
        "status": "passed",
        "schema": MANIFEST_SCHEMA,
        "expansion_id": manifest["expansion_id"],
        "input_fingerprint": manifest["input_fingerprint"],
        "output_fingerprint": manifest["output_fingerprint"],
        "counts": manifest["counts"],
        "byte_identical_replay": True,
        "legal_semantics_inferred": False,
        "history_complete": False,
    }


def materialize_attachment_fetch_run(
    raw_run_dir: Path,
    stage_dir: Path,
    fetch_run_dir: Path,
    *,
    expected_detail_count: int = DEFAULT_EXPECTED_DETAIL_COUNT,
) -> dict[str, Any]:
    """Copy a verified expansion into a fresh standard discovery run.

    The caller must pass the unchanged source plan whose SHA-256 is recorded in
    the returned discovery manifest to ``fetch_run``.  This function performs
    no network access and leaves both input directories untouched.
    """

    raw_run_dir = Path(raw_run_dir)
    stage_dir = Path(stage_dir)
    fetch_run_dir = Path(fetch_run_dir)
    verification = verify_nhi_listing_detail_expansion(
        raw_run_dir,
        stage_dir,
        expected_detail_count=expected_detail_count,
    )
    _validate_output_target(
        (raw_run_dir, stage_dir),
        fetch_run_dir,
    )
    fetch_run_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{fetch_run_dir.name}.",
            dir=fetch_run_dir.parent,
        )
    )
    try:
        ensure_run_layout(temporary)
        for filename in _FETCH_PROJECTION_FILES:
            (temporary / filename).write_bytes(
                (stage_dir / filename).read_bytes()
            )
        os.replace(temporary, fetch_run_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    discovery_manifest = _load_json_object(
        fetch_run_dir / "discovery-manifest.json",
        "materialized discovery manifest",
    )
    return {
        "status": "passed",
        "resources": discovery_manifest["counts"]["resources"],
        "source_plan_sha256": discovery_manifest[
            "source_plan_sha256"
        ],
        "expansion_id": verification["expansion_id"],
        "input_fingerprint": verification["input_fingerprint"],
        "network_requested": False,
        "postgresql_written": False,
    }
