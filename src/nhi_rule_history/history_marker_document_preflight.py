"""Map date-plus-designation search hits to official-document candidates.

This is a candidate-only bridge between the cross-format marker preflight and
the immutable historical acquisition.  It answers one bounded question:

``which official document numbers own the artifacts in which a date and an
exact dotted designation co-occur?``

It does not decide that the document amended the clause, that the observed date
is the legal effective date, or that either text span is an old/new effect.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    sha256_bytes,
)
from nhi_rule_history.discovery.source_universe_reconcile import (
    normalize_document_number,
)


REPORT_SCHEMA = (
    "nhi-rule-history/history-marker-document-candidate-preflight/v1"
)
ROW_SCHEMA = (
    "nhi-rule-history/history-marker-document-candidate/v1"
)
PARSER_VERSION = "nhi-history-marker-document-preflight/1.0.0"


def _fail(message: str) -> None:
    raise ContractError(message)


def _require_text(row: Mapping[str, Any], key: str, *, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        _fail(f"{label}.{key} must be non-empty text")
    return value


def _unique_by_id(
    rows: Iterable[Mapping[str, Any]],
    *,
    key: str,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = _require_text(row, key, label=label)
        if identity in result:
            _fail(f"duplicate {label} {key}: {identity}")
        result[identity] = row
    return result


def _load_pair_rows(
    path: Path,
    *,
    expected_group: str,
    expected_schema: str,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    pairs: dict[str, Mapping[str, Any]] = {}
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"{label} is missing: {path}") from exc
    with stream:
        wrappers: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                _fail(f"{label}:{line_number} is blank")
            try:
                wrapper = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"{label}:{line_number} is invalid JSON"
                ) from exc
            if not isinstance(wrapper, Mapping):
                _fail(f"{label}:{line_number} must be an object")
            if wrapper.get("schema") != expected_schema:
                _fail(f"{label}:{line_number} schema mismatch")
            wrappers.append(wrapper)
    for wrapper in wrappers:
        if wrapper.get("evidence_group") != expected_group:
            continue
        evidence = wrapper.get("evidence")
        if not isinstance(evidence, Mapping):
            _fail(f"{label} row evidence must be an object")
        pair_id = _require_text(evidence, "pair_id", label=label)
        if pair_id in pairs:
            _fail(f"duplicate {label} pair_id: {pair_id}")
        pairs[pair_id] = evidence
    if not pairs:
        _fail(f"{label} contains no article_date_pairs")
    return pairs


def _document_number_for_resource(
    resource: Mapping[str, Any],
    resources: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    raw = resource.get("official_document_number_raw")
    if isinstance(raw, str) and raw.strip():
        return raw, normalize_document_number(raw)
    parent_id = resource.get("parent_resource_id")
    if isinstance(parent_id, str) and parent_id:
        parent = resources.get(parent_id)
        if parent is None:
            _fail(f"resource parent is absent: {parent_id}")
        parent_raw = parent.get("official_document_number_raw")
        if isinstance(parent_raw, str) and parent_raw.strip():
            return parent_raw, normalize_document_number(parent_raw)
    _fail(
        "resource has no official document number and no resolvable parent: "
        + _require_text(resource, "resource_id", label="resource")
    )


def build_marker_document_candidate_preflight(
    *,
    cross_format_ledger_path: Path,
    odt_ledger_path: Path,
    discovered_resources_path: Path,
    resource_artifact_links_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a compact receipt and exact candidate ledger.

    All four paths are treated as sealed inputs.  A joint candidate without an
    artifact-to-resource-to-document path fails closed rather than silently
    lowering the denominator.
    """

    cross_pairs = _load_pair_rows(
        cross_format_ledger_path,
        expected_group="article_date_pairs",
        expected_schema=(
            "nhi-rule-history/"
            "history-marker-cross-format-evidence-ledger/v1"
        ),
        label="cross-format ledger",
    )
    odt_pairs = _load_pair_rows(
        odt_ledger_path,
        expected_group="article_date_pairs",
        expected_schema=(
            "nhi-rule-history/history-marker-odt-evidence-ledger/v1"
        ),
        label="ODT ledger",
    )
    resources = _unique_by_id(
        iter_jsonl(discovered_resources_path),
        key="resource_id",
        label="resource",
    )

    resource_ids_by_artifact: dict[str, set[str]] = defaultdict(set)
    seen_link_ids: set[str] = set()
    for link in iter_jsonl(resource_artifact_links_path):
        link_id = _require_text(link, "link_id", label="artifact link")
        if link_id in seen_link_ids:
            _fail(f"duplicate artifact link link_id: {link_id}")
        seen_link_ids.add(link_id)
        resource_id = _require_text(
            link, "resource_id", label="artifact link"
        )
        if resource_id not in resources:
            _fail(f"artifact link references absent resource: {resource_id}")
        artifact_sha256 = _require_text(
            link, "artifact_sha256", label="artifact link"
        )
        resource_ids_by_artifact[artifact_sha256].add(resource_id)

    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = defaultdict(int)
    candidate_count_distribution: dict[int, int] = defaultdict(int)
    document_pair_occurrences: dict[str, int] = defaultdict(int)

    for pair_id, cross in sorted(cross_pairs.items()):
        if cross.get("native_cross_format_joint_candidate") is not True:
            continue
        odt = odt_pairs.get(pair_id)
        if odt is None:
            _fail(f"cross-format pair is absent from ODT denominator: {pair_id}")

        artifact_sha256s: set[str] = set()
        if cross.get("odt_joint_candidate") is True:
            values = odt.get("same_artifact_sha256s")
            if not isinstance(values, list):
                _fail(f"ODT pair has invalid same_artifact_sha256s: {pair_id}")
            artifact_sha256s.update(str(value) for value in values)
        values = cross.get("native_typed_joint_artifact_sha256s")
        if not isinstance(values, list):
            _fail(
                "cross-format pair has invalid "
                f"native_typed_joint_artifact_sha256s: {pair_id}"
            )
        artifact_sha256s.update(str(value) for value in values)
        if not artifact_sha256s:
            _fail(f"joint pair has no joint evidence artifact: {pair_id}")

        candidates: dict[str, dict[str, set[str]]] = {}
        for artifact_sha256 in sorted(artifact_sha256s):
            resource_ids = resource_ids_by_artifact.get(artifact_sha256)
            if not resource_ids:
                _fail(
                    "joint evidence artifact has no acquisition binding: "
                    f"{artifact_sha256}"
                )
            for resource_id in sorted(resource_ids):
                resource = resources[resource_id]
                raw, normalized = _document_number_for_resource(
                    resource, resources
                )
                candidate = candidates.setdefault(
                    normalized,
                    {
                        "raw_values": set(),
                        "resource_ids": set(),
                        "artifact_sha256s": set(),
                    },
                )
                candidate["raw_values"].add(raw)
                candidate["resource_ids"].add(resource_id)
                candidate["artifact_sha256s"].add(artifact_sha256)

        if not candidates:
            _fail(f"joint pair has no official-document candidate: {pair_id}")
        candidate_count = len(candidates)
        status = (
            "unique_document_candidate"
            if candidate_count == 1
            else "ambiguous_document_candidates"
        )
        status_counts[status] += 1
        candidate_count_distribution[candidate_count] += 1
        for document_number in candidates:
            document_pair_occurrences[document_number] += 1

        rows.append(
            {
                "schema": ROW_SCHEMA,
                "pair_id": pair_id,
                "article_id": _require_text(
                    cross, "article_id", label="cross-format pair"
                ),
                "article_num": _require_text(
                    cross, "article_num", label="cross-format pair"
                ),
                "normalized_iso_candidate": _require_text(
                    cross,
                    "normalized_iso_candidate",
                    label="cross-format pair",
                ),
                "designation_kind": _require_text(
                    cross, "designation_kind", label="cross-format pair"
                ),
                "joint_evidence_artifact_sha256s": sorted(artifact_sha256s),
                "official_document_candidates": [
                    {
                        "official_document_number_normalized": number,
                        "official_document_number_raw_values": sorted(
                            details["raw_values"]
                        ),
                        "resource_ids": sorted(details["resource_ids"]),
                        "artifact_sha256s": sorted(
                            details["artifact_sha256s"]
                        ),
                    }
                    for number, details in sorted(candidates.items())
                ],
                "candidate_count": candidate_count,
                "candidate_status": status,
                "legal_effective_date_resolved": False,
                "amendment_effect_resolved": False,
                "direct_predecessor_resolved": False,
            }
        )

    if not rows:
        _fail("cross-format ledger contains no native joint candidates")

    ledger_bytes = b"".join(canonical_json_bytes(row) for row in rows)
    input_files = {
        "cross_format_ledger": {
            "path": str(cross_format_ledger_path),
            "sha256": file_sha256(cross_format_ledger_path),
        },
        "odt_ledger": {
            "path": str(odt_ledger_path),
            "sha256": file_sha256(odt_ledger_path),
        },
        "discovered_resources": {
            "path": str(discovered_resources_path),
            "sha256": file_sha256(discovered_resources_path),
        },
        "resource_artifact_links": {
            "path": str(resource_artifact_links_path),
            "sha256": file_sha256(resource_artifact_links_path),
        },
    }
    report = {
        "schema": REPORT_SCHEMA,
        "status": "candidate_document_mapping_only",
        "parser": {
            "version": PARSER_VERSION,
            "executable_sha256": file_sha256(Path(__file__)),
        },
        "inputs": input_files,
        "counts": {
            "native_joint_article_date_pairs": len(rows),
            "unique_document_candidate_pairs": status_counts[
                "unique_document_candidate"
            ],
            "ambiguous_document_candidate_pairs": status_counts[
                "ambiguous_document_candidates"
            ],
            "unmapped_joint_pairs": 0,
            "distinct_official_document_candidates": len(
                document_pair_occurrences
            ),
            "candidate_count_distribution": {
                str(key): value
                for key, value in sorted(candidate_count_distribution.items())
            },
        },
        "output": {
            "ledger_row_schema": ROW_SCHEMA,
            "ledger_rows": len(rows),
            "ledger_sha256": sha256_bytes(ledger_bytes),
        },
        "claims": {
            "official_event_resolved": False,
            "legal_effective_date_resolved": False,
            "amendment_effect_resolved": False,
            "clause_identity_resolved": False,
            "direct_predecessor_resolved": False,
            "canonical_history_written": False,
            "per_clause_history_complete": False,
        },
        "statement": (
            "Each native date-plus-designation co-occurrence is mapped to one "
            "or more owning official-document numbers. A unique owning "
            "document remains a search candidate, not an amendment/effect link."
        ),
    }
    return report, rows


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_marker_document_candidate_preflight(
    *,
    report_path: Path,
    ledger_path: Path,
    cross_format_ledger_path: Path,
    odt_ledger_path: Path,
    discovered_resources_path: Path,
    resource_artifact_links_path: Path,
) -> dict[str, Any]:
    report, rows = build_marker_document_candidate_preflight(
        cross_format_ledger_path=cross_format_ledger_path,
        odt_ledger_path=odt_ledger_path,
        discovered_resources_path=discovered_resources_path,
        resource_artifact_links_path=resource_artifact_links_path,
    )
    ledger_payload = b"".join(canonical_json_bytes(row) for row in rows)
    if sha256_bytes(ledger_payload) != report["output"]["ledger_sha256"]:
        _fail("ledger hash changed after report construction")
    _atomic_write(ledger_path, ledger_payload)
    report = {
        **report,
        "output": {
            **report["output"],
            "ledger_path": str(ledger_path),
        },
    }
    _atomic_write(report_path, canonical_json_bytes(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map cross-format date-plus-designation candidates to owning "
            "official-document numbers without creating legal links."
        )
    )
    parser.add_argument("--cross-format-ledger", type=Path, required=True)
    parser.add_argument("--odt-ledger", type=Path, required=True)
    parser.add_argument("--discovered-resources", type=Path, required=True)
    parser.add_argument("--resource-artifact-links", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = write_marker_document_candidate_preflight(
        report_path=arguments.report,
        ledger_path=arguments.ledger,
        cross_format_ledger_path=arguments.cross_format_ledger,
        odt_ledger_path=arguments.odt_ledger,
        discovered_resources_path=arguments.discovered_resources,
        resource_artifact_links_path=arguments.resource_artifact_links,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
