"""Deterministic marker-to-historical-ODT candidate-coverage preflight.

This module asks two deliberately narrow questions for every valid date marker
in the sealed legacy current-text observation:

1. Does the same normalized ROC calendar date occur in any ODT artifact in the
   sealed bounded historical structural run?
2. For an official dotted numeric designation, do that date and designation
   occur in the same ODT artifact?

The result is source-local candidate evidence only.  It does not resolve an
official notice, a legal effective date, an amendment effect, adjacency, or
history completeness.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.annotation_stage import (
    CONTRACT_VERSION as ANNOTATION_CONTRACT_VERSION,
    EXTRACTOR_VERSION,
    AnnotationStageMaterial,
    extract_roc_date_markers,
    prepare_annotation_stage,
)
from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
)
from nhi_rule_history.pg.acquisition import (
    AcquisitionLoadError,
    AcquisitionMaterial,
    validate_acquisition_run,
)
from nhi_rule_history.pg.common import (
    PgLoadError,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)
from nhi_rule_history.pg.structural import (
    CONTRACT_VERSION as STRUCTURAL_CONTRACT_VERSION,
    LOADER_VERSION as STRUCTURAL_LOADER_VERSION,
    StructuralLoadError,
    StructuralMaterial,
    validate_structural_run,
)


REPORT_SCHEMA = "nhi-rule-history/history-marker-odt-preflight/v1"
LEDGER_SCHEMA = "nhi-rule-history/history-marker-odt-evidence-ledger/v1"
PAIR_SCHEMA = "nhi-rule-history/history-marker-odt-article-date-pair/v1"
ANNOTATION_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-odt-annotation-locator/v1"
)
HISTORICAL_DATE_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-odt-historical-date-locator/v1"
)
DESIGNATION_LOCATOR_SCHEMA = (
    "nhi-rule-history/history-marker-odt-designation-locator/v1"
)
REJECTED_DATE_SCHEMA = (
    "nhi-rule-history/history-marker-odt-rejected-date-locator/v1"
)
MATCHER_VERSION = "nhi-rule-history/history-marker-odt-matcher/1.0.0"
NON_CLAIM_STATEMENT = (
    "Candidate coverage only. A date or date-plus-designation co-occurrence "
    "does not establish an official event, legal effective date, amendment "
    "effect, predecessor adjacency, or complete clause history."
)

_ANNOTATION_RECEIPT_SCHEMA = (
    "nhi-rule-history/legacy-date-annotation-stage-public-receipt/v1"
)
_HISTORICAL_RECEIPT_SCHEMA = (
    "nhi-rule-history/historical-events-exact-phrase-capture-public-receipt/v1"
)
_OFFICIAL_DESIGNATION_RE = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)+$")
_PROJECT_NAVIGATION_RE = re.compile(r"^0(?:\.[0-9]+)+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class HistoryMarkerPreflightError(RuntimeError):
    """A missing, tampered, malformed, or incompatible preflight input."""


def _fail(message: str) -> None:
    raise HistoryMarkerPreflightError(message)


def _read_receipt(
    value: Path | Mapping[str, Any],
    *,
    schema: str,
    label: str,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        receipt = dict(value)
    else:
        path = Path(value)
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _fail(f"{label} receipt is missing or invalid JSON")
    if not isinstance(receipt, dict) or receipt.get("schema") != schema:
        _fail(f"{label} receipt schema mismatch")
    return receipt


def _required_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(f"{label} is not a SHA-256")
    return value


def _snapshot_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    expected_keys: set[str],
    label: str,
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for index, value in enumerate(rows):
        if not isinstance(value, Mapping):
            _fail(f"{label} row {index} is not an object")
        row = dict(value)
        if row.pop("run_id", None) != run_id:
            _fail(f"{label} row {index} belongs to another run")
        if set(row) != expected_keys:
            _fail(f"{label} row {index} has an incompatible field set")
        result.append(row)
    return tuple(result)


def _validate_annotation_snapshot(
    *,
    annotation_run: Mapping[str, Any],
    articles: Iterable[Mapping[str, Any]],
    annotations: Iterable[Mapping[str, Any]],
    receipt: Mapping[str, Any],
) -> AnnotationStageMaterial:
    run_id = annotation_run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        _fail("annotation run_id is missing")
    if annotation_run.get("state") != "sealed":
        _fail("annotation run is not sealed")
    if annotation_run.get("contract_version") != ANNOTATION_CONTRACT_VERSION:
        _fail("annotation contract version mismatch")
    if annotation_run.get("extractor_version") != EXTRACTOR_VERSION:
        _fail("annotation extractor version mismatch")

    article_keys = {
        "article_id",
        "article_num",
        "source_identity",
        "source_identity_sha256",
        "full_text",
        "full_text_sha256",
        "annotation_count",
        "caller_record_sha256",
        "source_row_sha256",
    }
    annotation_keys = {
        "schema",
        "annotation_id",
        "article_id",
        "marker_ordinal",
        "char_start",
        "char_end",
        "raw_expression",
        "raw_expression_sha256",
        "roc_year",
        "roc_month",
        "roc_day",
        "normalized_iso_candidate",
        "normalization_status",
        "resolution_status",
        "unresolved_reason",
        "source_row_sha256",
    }
    snapshot_articles = _snapshot_rows(
        articles,
        run_id=run_id,
        expected_keys=article_keys,
        label="annotation article",
    )
    snapshot_annotations = _snapshot_rows(
        annotations,
        run_id=run_id,
        expected_keys=annotation_keys,
        label="date annotation",
    )

    material = prepare_annotation_stage(
        {
            "article_id": row["article_id"],
            "article_num": row["article_num"],
            "source_identity": row["source_identity"],
            "full_text": row["full_text"],
        }
        for row in snapshot_articles
    )
    if tuple(sorted(snapshot_articles, key=lambda row: row["article_id"])) != (
        material.articles
    ):
        _fail("annotation article snapshot differs from deterministic replay")
    if tuple(
        sorted(
            snapshot_annotations,
            key=lambda row: (
                row["article_id"],
                row["marker_ordinal"],
                row["annotation_id"],
            ),
        )
    ) != material.annotations:
        _fail("date annotation snapshot differs from deterministic replay")

    expected_run = {
        "run_id": material.run_id,
        "contract_version": ANNOTATION_CONTRACT_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "migration_sha256": material.migration_sha256,
        "code_sha256": material.code_sha256,
        "input_fingerprint": material.input_fingerprint,
        "output_fingerprint": material.output_fingerprint,
        "sealed_fingerprint": material.sealed_fingerprint,
        "expected_counts": dict(material.expected_counts),
        "table_fingerprints": dict(material.table_fingerprints),
        "state": "sealed",
    }
    for key, expected in expected_run.items():
        if annotation_run.get(key) != expected:
            _fail(f"annotation run {key} differs from deterministic replay")

    counts = receipt.get("counts")
    verification = receipt.get("verification")
    extractor = receipt.get("extractor")
    if (
        receipt.get("run_id") != material.run_id
        or not isinstance(counts, Mapping)
        or not isinstance(verification, Mapping)
        or not isinstance(extractor, Mapping)
        or verification.get("state") != "sealed"
        or verification.get("sealed_fingerprint")
        != material.sealed_fingerprint
        or extractor.get("version") != EXTRACTOR_VERSION
    ):
        _fail("annotation receipt is not bound to the sealed replay")
    receipt_counts = {
        "article_observations": len(material.articles),
        "articles_with_markers": sum(
            row["annotation_count"] > 0 for row in material.articles
        ),
        "date_annotations": len(material.annotations),
        "normalized_iso_candidates": sum(
            row["normalized_iso_candidate"] is not None
            for row in material.annotations
        ),
        "invalid_calendar_candidates": sum(
            row["normalization_status"] == "invalid_calendar_date"
            for row in material.annotations
        ),
        "unresolved_annotations": sum(
            row["resolution_status"] == "unresolved_event"
            for row in material.annotations
        ),
        "coverage_projection_rows": len(material.articles),
    }
    if any(counts.get(key) != value for key, value in receipt_counts.items()):
        _fail("annotation receipt counts differ from the sealed replay")
    valid_dates = sorted(
        {
            row["normalized_iso_candidate"]
            for row in material.annotations
            if row["normalized_iso_candidate"] is not None
        }
    )
    if (
        counts.get("unique_normalized_iso_dates") != len(valid_dates)
        or counts.get("earliest_normalized_iso_date")
        != (valid_dates[0] if valid_dates else None)
        or counts.get("latest_normalized_iso_date")
        != (valid_dates[-1] if valid_dates else None)
    ):
        _fail("annotation receipt date range differs from the sealed replay")
    return material


def _validate_historical_inputs(
    *,
    raw_dir: Path,
    structural_dir: Path,
    receipt: Mapping[str, Any],
) -> tuple[AcquisitionMaterial, StructuralMaterial, str]:
    try:
        acquisition = validate_acquisition_run(Path(raw_dir))
        structural = validate_structural_run(Path(structural_dir))
    except (
        AcquisitionLoadError,
        StructuralLoadError,
        ContractError,
        PgLoadError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        raise HistoryMarkerPreflightError(
            "historical sealed manifests or files failed verification"
        ) from exc

    accepted_acquisition = receipt.get("accepted_acquisition")
    accepted_structural = receipt.get("accepted_structural_stage")
    scope = receipt.get("scope")
    if (
        not isinstance(accepted_acquisition, Mapping)
        or not isinstance(accepted_structural, Mapping)
        or not isinstance(scope, Mapping)
    ):
        _fail("historical receipt is missing accepted run bindings")

    raw_manifest = acquisition.raw_manifest_sha256
    if (
        accepted_acquisition.get("run_id") != acquisition.run_id
        or accepted_acquisition.get("state") != "sealed"
        or accepted_acquisition.get("raw_manifest_sha256") != raw_manifest
        or accepted_acquisition.get("sealed_fingerprint")
        != acquisition.sealed_fingerprint
        or scope.get("source_plan_sha256")
        != acquisition.source_plan_sha256
    ):
        _fail("historical acquisition receipt binding mismatch")
    raw_counts = {
        "resources": len(acquisition.rows["discovered-resources.jsonl"]),
        "artifacts": len(acquisition.rows["raw-artifacts.jsonl"]),
        "artifact_bytes": sum(
            row["byte_size"]
            for row in acquisition.rows["raw-artifacts.jsonl"]
        ),
        "issues": len(acquisition.rows["issues.jsonl"]),
        "same_url_different_bytes": sum(
            row["relation_to_previous"] == "same_url_different_bytes"
            for row in acquisition.rows[
                "artifact-url-observations.jsonl"
            ]
        ),
    }
    if any(
        accepted_acquisition.get(key) != value
        for key, value in raw_counts.items()
    ):
        _fail("historical acquisition receipt counts mismatch")
    media_type_counts = dict(
        sorted(
            Counter(
                row["media_type"]
                for row in acquisition.rows["raw-artifacts.jsonl"]
            ).items()
        )
    )
    if accepted_acquisition.get("media_type_counts") != media_type_counts:
        _fail("historical acquisition media-type counts mismatch")

    if (
        structural.manifest.get("raw_manifest_sha256") != raw_manifest
        or accepted_structural.get("parse_run_id")
        != structural.parse_run_id
        or accepted_structural.get("state") != "sealed"
        or accepted_structural.get("structural_manifest_sha256")
        != structural.structural_manifest_sha256
    ):
        _fail("historical structural receipt binding mismatch")
    manifest_counts = structural.manifest["counts"]
    structural_count_keys = (
        "declared_odt_artifacts",
        "parsed_odt_artifacts",
        "structural_blocks",
        "occurrence_candidates",
        "parse_issues",
        "blocking_issues",
    )
    if any(
        accepted_structural.get(key) != manifest_counts.get(key)
        for key in structural_count_keys
    ):
        _fail("historical structural receipt counts mismatch")
    structural_seal = object_fingerprint(
        {
            "loader_version": STRUCTURAL_LOADER_VERSION,
            "contract_version": STRUCTURAL_CONTRACT_VERSION,
            "migration_sha256": structural.migration_sha256,
            "code_sha256": structural.code_sha256,
            "acquisition_run_id": acquisition.run_id,
            "input_fingerprint": structural.manifest["input_fingerprint"],
            "output_fingerprint": structural.output_fingerprint,
        }
    )
    if accepted_structural.get("sealed_fingerprint") != structural_seal:
        _fail("historical structural sealed fingerprint mismatch")
    return acquisition, structural, structural_seal


def _date_locator(
    *,
    row: Mapping[str, Any],
    marker: Mapping[str, Any],
    rejected: bool,
) -> dict[str, Any]:
    locator = {
        "schema": (
            REJECTED_DATE_SCHEMA
            if rejected
            else HISTORICAL_DATE_LOCATOR_SCHEMA
        ),
        "source": "historical_odt_structural_block",
        "artifact_sha256": row["artifact_sha256"],
        "block_id": row["block_id"],
        "locator_key": row["locator_key"],
        "marker_ordinal_in_block": marker["marker_ordinal"],
        "char_start_in_block": marker["char_start"],
        "char_end_in_block": marker["char_end"],
        "raw_expression": marker["raw_expression"],
        "raw_expression_sha256": marker["raw_expression_sha256"],
        "normalized_iso_candidate": marker[
            "normalized_iso_candidate"
        ],
        "normalization_status": marker["normalization_status"],
        "source_row_sha256": row["source_row_sha256"],
    }
    return locator


def _annotation_locator(
    row: Mapping[str, Any],
    *,
    article_num: str,
) -> dict[str, Any]:
    return {
        "schema": (
            REJECTED_DATE_SCHEMA
            if row["normalized_iso_candidate"] is None
            else ANNOTATION_LOCATOR_SCHEMA
        ),
        "source": "sealed_legacy_current_text_annotation",
        "annotation_id": row["annotation_id"],
        "article_id": row["article_id"],
        "article_num": article_num,
        "marker_ordinal": row["marker_ordinal"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
        "raw_expression": row["raw_expression"],
        "raw_expression_sha256": row["raw_expression_sha256"],
        "normalized_iso_candidate": row[
            "normalized_iso_candidate"
        ],
        "normalization_status": row["normalization_status"],
        "source_row_sha256": row["source_row_sha256"],
    }


def _designation_locator(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_text = row.get("raw_text")
    start = row.get("match_start_in_raw")
    end = row.get("match_end_in_raw")
    designation = row.get("designation_text")
    if (
        not isinstance(raw_text, str)
        or not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or raw_text[start:end] != designation
    ):
        _fail("historical designation occurrence has an invalid exact offset")
    if not isinstance(designation, str) or not _OFFICIAL_DESIGNATION_RE.fullmatch(
        designation
    ):
        _fail("historical designation occurrence is malformed")
    return {
        "schema": DESIGNATION_LOCATOR_SCHEMA,
        "source": "historical_odt_occurrence_candidate",
        "designation": designation,
        "artifact_sha256": row["artifact_sha256"],
        "block_id": row["block_id"],
        "occurrence_id": row["occurrence_id"],
        "locator_key": row["locator_key"],
        "match_start_in_raw": start,
        "match_end_in_raw": end,
        "source_row_sha256": row["source_row_sha256"],
    }


def _row_fingerprint(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = tuple(rows)
    return {
        "row_count": len(materialized),
        "row_set_fingerprint": row_set_fingerprint(
            row_sha256(row) for row in materialized
        ),
    }


def analyze_history_marker_preflight(
    *,
    annotation_run: Mapping[str, Any],
    articles: Iterable[Mapping[str, Any]],
    annotations: Iterable[Mapping[str, Any]],
    annotation_receipt: Path | Mapping[str, Any],
    historical_receipt: Path | Mapping[str, Any],
    raw_dir: Path,
    structural_dir: Path,
) -> dict[str, Any]:
    """Return a deterministic full-locator candidate-coverage result."""

    annotation_public_receipt = _read_receipt(
        annotation_receipt,
        schema=_ANNOTATION_RECEIPT_SCHEMA,
        label="annotation",
    )
    historical_public_receipt = _read_receipt(
        historical_receipt,
        schema=_HISTORICAL_RECEIPT_SCHEMA,
        label="historical capture",
    )
    annotation = _validate_annotation_snapshot(
        annotation_run=annotation_run,
        articles=articles,
        annotations=annotations,
        receipt=annotation_public_receipt,
    )
    acquisition, structural, structural_seal = _validate_historical_inputs(
        raw_dir=raw_dir,
        structural_dir=structural_dir,
        receipt=historical_public_receipt,
    )

    article_by_id = {row["article_id"]: row for row in annotation.articles}
    annotation_locators: list[dict[str, Any]] = []
    rejected_annotation_dates: list[dict[str, Any]] = []
    annotations_by_pair: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    for row in annotation.annotations:
        article = article_by_id[row["article_id"]]
        locator = _annotation_locator(row, article_num=article["article_num"])
        if row["normalized_iso_candidate"] is None:
            rejected_annotation_dates.append(locator)
            continue
        annotation_locators.append(locator)
        annotations_by_pair[
            (row["article_id"], row["normalized_iso_candidate"])
        ].append(locator)

    historical_date_locators: list[dict[str, Any]] = []
    rejected_historical_dates: list[dict[str, Any]] = []
    date_artifacts: dict[str, set[str]] = defaultdict(set)
    for row in structural.rows["structural-blocks.jsonl"]:
        for marker in extract_roc_date_markers(row["raw_text"]):
            rejected = marker["normalized_iso_candidate"] is None
            locator = _date_locator(
                row=row,
                marker=marker,
                rejected=rejected,
            )
            if rejected:
                rejected_historical_dates.append(locator)
                continue
            historical_date_locators.append(locator)
            date_artifacts[marker["normalized_iso_candidate"]].add(
                row["artifact_sha256"]
            )

    designation_locators: list[dict[str, Any]] = []
    designation_artifacts: dict[str, set[str]] = defaultdict(set)
    for row in structural.rows["occurrence-candidates.jsonl"]:
        locator = _designation_locator(row)
        designation_locators.append(locator)
        designation_artifacts[locator["designation"]].add(
            locator["artifact_sha256"]
        )

    pair_rows: list[dict[str, Any]] = []
    for (article_id, normalized_date), locators in sorted(
        annotations_by_pair.items()
    ):
        article_num = article_by_id[article_id]["article_num"]
        if _OFFICIAL_DESIGNATION_RE.fullmatch(article_num):
            designation_kind = "official_dotted_numeric_candidate"
            same_artifacts = sorted(
                date_artifacts.get(normalized_date, set())
                & designation_artifacts.get(article_num, set())
            )
            joint: bool | None = bool(same_artifacts)
            joint_status = (
                "candidate_found" if joint else "candidate_not_found"
            )
        elif _PROJECT_NAVIGATION_RE.fullmatch(article_num):
            designation_kind = "project_navigation_general_rules"
            same_artifacts = []
            joint = None
            joint_status = "not_evaluable_project_navigation"
        else:
            _fail("legacy article designation is malformed")
        date_matches = sorted(date_artifacts.get(normalized_date, set()))
        pair_basis = {
            "article_id": article_id,
            "article_num": article_num,
            "normalized_iso_candidate": normalized_date,
        }
        pair_rows.append(
            {
                "schema": PAIR_SCHEMA,
                "pair_id": object_fingerprint(pair_basis),
                **pair_basis,
                "designation_kind": designation_kind,
                "marker_occurrence_count": len(locators),
                "annotation_ids": sorted(
                    row["annotation_id"] for row in locators
                ),
                "date_in_historical_odt_artifact": bool(date_matches),
                "date_artifact_sha256s": date_matches,
                "date_and_designation_in_same_artifact": joint,
                "joint_evaluation_status": joint_status,
                "same_artifact_sha256s": same_artifacts,
            }
        )

    valid_occurrence_count = len(annotation_locators)
    pair_count = len(pair_rows)
    official_pairs = [
        row
        for row in pair_rows
        if row["designation_kind"]
        == "official_dotted_numeric_candidate"
    ]
    project_navigation_pairs = [
        row
        for row in pair_rows
        if row["designation_kind"]
        == "project_navigation_general_rules"
    ]
    occurrence_by_pair_id = {
        row["pair_id"]: row["marker_occurrence_count"] for row in pair_rows
    }
    date_found_occurrences = sum(
        occurrence_by_pair_id[row["pair_id"]]
        for row in pair_rows
        if row["date_in_historical_odt_artifact"]
    )
    joint_found_occurrences = sum(
        occurrence_by_pair_id[row["pair_id"]]
        for row in official_pairs
        if row["date_and_designation_in_same_artifact"]
    )

    row_sets = {
        "valid_annotation_marker_locators": tuple(
            sorted(
                annotation_locators,
                key=lambda row: (
                    row["article_id"],
                    row["marker_ordinal"],
                    row["annotation_id"],
                ),
            )
        ),
        "rejected_annotation_date_locators": tuple(
            sorted(
                rejected_annotation_dates,
                key=lambda row: (
                    row["article_id"],
                    row["marker_ordinal"],
                    row["annotation_id"],
                ),
            )
        ),
        "historical_date_marker_locators": tuple(
            sorted(
                historical_date_locators,
                key=lambda row: (
                    row["artifact_sha256"],
                    row["block_id"],
                    row["marker_ordinal_in_block"],
                    row["char_start_in_block"],
                ),
            )
        ),
        "rejected_historical_date_locators": tuple(
            sorted(
                rejected_historical_dates,
                key=lambda row: (
                    row["artifact_sha256"],
                    row["block_id"],
                    row["marker_ordinal_in_block"],
                    row["char_start_in_block"],
                ),
            )
        ),
        "historical_designation_locators": tuple(
            sorted(
                designation_locators,
                key=lambda row: (
                    row["designation"],
                    row["artifact_sha256"],
                    row["occurrence_id"],
                ),
            )
        ),
        "article_date_pairs": tuple(
            sorted(
                pair_rows,
                key=lambda row: (
                    row["article_num"],
                    row["normalized_iso_candidate"],
                    row["article_id"],
                ),
            )
        ),
    }
    evidence_fingerprints = {
        name: _row_fingerprint(rows) for name, rows in row_sets.items()
    }
    input_fingerprint = object_fingerprint(
        {
            "matcher_version": MATCHER_VERSION,
            "annotation_run_id": annotation.run_id,
            "annotation_input_fingerprint": annotation.input_fingerprint,
            "annotation_output_fingerprint": annotation.output_fingerprint,
            "annotation_sealed_fingerprint": annotation.sealed_fingerprint,
            "acquisition_run_id": acquisition.run_id,
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "acquisition_sealed_fingerprint": (
                acquisition.sealed_fingerprint
            ),
            "structural_parse_run_id": structural.parse_run_id,
            "structural_manifest_sha256": (
                structural.structural_manifest_sha256
            ),
            "structural_sealed_fingerprint": structural_seal,
        }
    )
    coverage = {
        "valid_marker_occurrences": valid_occurrence_count,
        "invalid_annotation_date_candidates_rejected": len(
            rejected_annotation_dates
        ),
        "unique_article_date_pairs": pair_count,
        "unique_normalized_annotation_dates": len(
            {
                row["normalized_iso_candidate"]
                for row in annotation_locators
            }
        ),
        "historical_odt_valid_date_occurrences": len(
            historical_date_locators
        ),
        "historical_odt_invalid_date_candidates_rejected": len(
            rejected_historical_dates
        ),
        "historical_odt_unique_normalized_dates": len(date_artifacts),
        "historical_odt_designation_occurrences": len(
            designation_locators
        ),
        "historical_odt_unique_designations": len(
            designation_artifacts
        ),
        "date_present_marker_occurrences": date_found_occurrences,
        "date_absent_marker_occurrences": (
            valid_occurrence_count - date_found_occurrences
        ),
        "date_present_article_date_pairs": sum(
            row["date_in_historical_odt_artifact"] for row in pair_rows
        ),
        "date_absent_article_date_pairs": sum(
            not row["date_in_historical_odt_artifact"]
            for row in pair_rows
        ),
        "official_designation_article_date_pairs": len(official_pairs),
        "project_navigation_article_date_pairs": len(
            project_navigation_pairs
        ),
        "date_and_designation_same_artifact_marker_occurrences": (
            joint_found_occurrences
        ),
        "date_and_designation_not_same_artifact_marker_occurrences": (
            sum(row["marker_occurrence_count"] for row in official_pairs)
            - joint_found_occurrences
        ),
        "date_and_designation_same_artifact_article_date_pairs": sum(
            row["date_and_designation_in_same_artifact"] is True
            for row in official_pairs
        ),
        "date_and_designation_not_same_artifact_article_date_pairs": sum(
            row["date_and_designation_in_same_artifact"] is False
            for row in official_pairs
        ),
        "date_and_designation_not_evaluable_project_navigation_pairs": len(
            project_navigation_pairs
        ),
    }
    output_fingerprint = object_fingerprint(
        {
            "coverage": coverage,
            "evidence_fingerprints": evidence_fingerprints,
        }
    )
    result = {
        "schema": REPORT_SCHEMA,
        "status": "candidate_coverage_only",
        "matcher_version": MATCHER_VERSION,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
        "inputs": {
            "annotation": {
                "run_id": annotation.run_id,
                "input_fingerprint": annotation.input_fingerprint,
                "output_fingerprint": annotation.output_fingerprint,
                "sealed_fingerprint": annotation.sealed_fingerprint,
                "table_fingerprints": dict(
                    annotation.table_fingerprints
                ),
            },
            "historical_acquisition": {
                "run_id": acquisition.run_id,
                "raw_manifest_sha256": acquisition.raw_manifest_sha256,
                "sealed_fingerprint": acquisition.sealed_fingerprint,
            },
            "historical_structural": {
                "parse_run_id": structural.parse_run_id,
                "structural_manifest_sha256": (
                    structural.structural_manifest_sha256
                ),
                "input_fingerprint": structural.manifest[
                    "input_fingerprint"
                ],
                "output_fingerprint": structural.output_fingerprint,
                "sealed_fingerprint": structural_seal,
                "table_fingerprints": dict(
                    structural.table_fingerprints
                ),
            },
        },
        "method": {
            "annotation_date_normalization": EXTRACTOR_VERSION,
            "historical_date_normalization": EXTRACTOR_VERSION,
            "designation_syntax": (
                "official dotted numeric candidate: "
                "^[1-9][0-9]*(?:\\.[0-9]+)+$"
            ),
            "project_navigation_syntax": (
                "general-rules navigation only: ^0(?:\\.[0-9]+)+$"
            ),
            "date_candidate_test": (
                "same normalized ROC calendar date in any structural block "
                "of any ODT artifact"
            ),
            "joint_candidate_test": (
                "same artifact_sha256 occurs in the date index and exact "
                "designation occurrence-candidate index"
            ),
            "invalid_date_policy": (
                "preserve exact locator, reject from every denominator and "
                "match; a token mislabeled as normalized fails closed"
            ),
            "malformed_designation_policy": "fail entire preflight closed",
            "project_navigation_policy": (
                "date coverage is evaluated; joint designation coverage is "
                "not evaluable and is never treated as an official label"
            ),
        },
        "scope_and_limitations": {
            "historical_query": {
                key: historical_public_receipt["scope"].get(key)
                for key in (
                    "query_start",
                    "query_end",
                    "capture_cut",
                    "query",
                    "query_mode",
                )
            },
            "historical_media_type_counts": dict(
                historical_public_receipt["accepted_acquisition"][
                    "media_type_counts"
                ]
            ),
            "structurally_searched_odt_artifacts": (
                structural.manifest["counts"]["parsed_odt_artifacts"]
            ),
            "non_odt_artifacts_not_searched_by_this_matcher": (
                len(acquisition.rows["raw-artifacts.jsonl"])
                - structural.manifest["counts"][
                    "parsed_odt_artifacts"
                ]
            ),
            "receipt_open_gaps": dict(
                historical_public_receipt.get("open_gaps", {})
            ),
            "date_absence_meaning": (
                "not found in the 240 parsed ODT artifacts of this bounded "
                "exact-phrase run; it is not evidence that no official "
                "source exists"
            ),
            "cooccurrence_meaning": (
                "date and designation are source-local candidates in one "
                "artifact; the artifact, notice, amendment effect, and legal "
                "date remain unresolved"
            ),
            "designation_candidate_limit": (
                "the structural occurrence parser can retain numeric form or "
                "table labels; this stage deliberately does not adjudicate "
                "their legal clause identity"
            ),
        },
        "coverage": coverage,
        "evidence_fingerprints": evidence_fingerprints,
        "rejected_date_locators": {
            "annotation": list(
                row_sets["rejected_annotation_date_locators"]
            ),
            "historical_odt": list(
                row_sets["rejected_historical_date_locators"]
            ),
        },
        "evidence_rows": {
            name: list(rows) for name, rows in row_sets.items()
        },
        "claims": {
            "candidate_coverage_computed": True,
            "official_event_resolved": False,
            "legal_effective_date_resolved": False,
            "amendment_effect_resolved": False,
            "adjacent_snapshot_resolved": False,
            "official_source_universe_closed": False,
            "per_clause_history_complete": False,
            "canonical_history_written": False,
        },
        "statement": NON_CLAIM_STATEMENT,
    }
    return result


def write_evidence_ledger(
    result: Mapping[str, Any],
    path: Path,
) -> dict[str, Any]:
    """Atomically write all exact-locator evidence rows as canonical JSONL."""

    if result.get("schema") != REPORT_SCHEMA:
        _fail("cannot write ledger for an incompatible report")
    evidence_rows = result.get("evidence_rows")
    if not isinstance(evidence_rows, Mapping):
        _fail("preflight result has no evidence rows")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for group in sorted(evidence_rows):
                rows = evidence_rows[group]
                if not isinstance(rows, list):
                    _fail("preflight evidence group is not an array")
                row_counts[group] = len(rows)
                for row in rows:
                    envelope = {
                        "schema": LEDGER_SCHEMA,
                        "evidence_group": group,
                        "input_fingerprint": result["input_fingerprint"],
                        "output_fingerprint": result[
                            "output_fingerprint"
                        ],
                        "evidence": row,
                    }
                    assert_public_value(envelope)
                    stream.write(canonical_json_bytes(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "filename": path.name,
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "row_counts": row_counts,
    }


def compact_public_report(
    result: Mapping[str, Any],
    *,
    evidence_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove large row arrays while retaining exact ledger file bindings."""

    if result.get("schema") != REPORT_SCHEMA:
        _fail("cannot compact an incompatible report")
    report = {
        key: value
        for key, value in result.items()
        if key != "evidence_rows"
    }
    report["evidence_ledger"] = dict(evidence_ledger)
    assert_public_value(report)
    return report
