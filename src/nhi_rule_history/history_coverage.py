"""Deterministic, fail-closed audits for per-rule history coverage.

The audit accepts JSON-like records and returns JSON-serializable records.  It
does not read PostgreSQL, infer official effective dates, or promote legacy
rows.  Source-local date annotations are completeness checksums: matching the
stored version dates is necessary, but never sufficient, for a
``complete_to_declared_cut`` result.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence


AUDIT_SCHEMA = "nhi-rule-history/rule-history-coverage-audit/v1"

_RESOLVED_ANNOTATION_STATES = frozenset(
    {"event_resolved", "transition_verified", "rejected_non_amendment"}
)
_TRANSITION_VERIFIED_STATE = "transition_verified"
_REJECTED_ANNOTATION_STATE = "rejected_non_amendment"
_CHAPTER_ZERO_CODE = "chapter:00"
_CHAPTER_ZERO_SOURCE_LABEL = "通則"
_PROJECT_ASSIGNED = "project_assigned"
_OFFICIAL_CHAPTER_ZERO_RE = re.compile(r"第\s*0\s*章")


class HistoryCoverageError(ValueError):
    """Raised when the audit envelope is structurally unusable."""


def _as_iso_date(value: Any) -> tuple[str | None, str | None]:
    """Return ``(iso_date, error_code)`` without guessing non-ISO dates."""

    if value is None or value == "":
        return None, "missing"
    if isinstance(value, datetime):
        return value.date().isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    if not isinstance(value, str):
        return None, "invalid_type"
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, "invalid_iso_date"
    return parsed.isoformat(), None


def _record_id(record: Mapping[str, Any], fields: Sequence[str], fallback: str) -> str:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return fallback


def _rule_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("rule_id")
    return None if value in (None, "") else str(value)


def _text_digest(version: Mapping[str, Any]) -> str | None:
    for field in ("normalized_sha256", "raw_sha256", "text_sha256"):
        value = version.get(field)
        if value not in (None, ""):
            return str(value).lower()
    for field in ("normalized_text", "raw_text", "full_text", "text"):
        value = version.get(field)
        if value is not None:
            text = str(value).replace("\r\n", "\n").replace("\r", "\n")
            return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return None


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _navigation_result(
    rule_id: str,
    assignments: Sequence[Mapping[str, Any]],
    *,
    chapter_zero_required: bool,
) -> dict[str, Any]:
    records = [dict(item) for item in assignments if _rule_id(item) == rule_id]
    records.sort(
        key=lambda row: (
            str(row.get("navigation_code", "")),
            str(row.get("valid_from", "")),
            str(row.get("navigation_assignment_id", "")),
        )
    )
    errors: list[str] = []
    chapter_zero: dict[str, Any] | None = None

    for record in records:
        if record.get("navigation_code") != _CHAPTER_ZERO_CODE:
            continue
        candidate = {
            "source_designation_raw": record.get("source_designation_raw"),
            "navigation_code": record.get("navigation_code"),
            "code_origin": record.get("code_origin"),
            "display_label": record.get("display_label"),
        }
        if chapter_zero is None:
            chapter_zero = candidate
        if candidate["code_origin"] != _PROJECT_ASSIGNED:
            errors.append("chapter_zero_not_project_assigned")
        if candidate["source_designation_raw"] != _CHAPTER_ZERO_SOURCE_LABEL:
            errors.append("chapter_zero_source_designation_not_tongze")
        if candidate["display_label"] != _CHAPTER_ZERO_SOURCE_LABEL:
            errors.append("chapter_zero_display_label_not_tongze")
        if any(
            _OFFICIAL_CHAPTER_ZERO_RE.search(str(value or ""))
            for value in (
                candidate["source_designation_raw"],
                candidate["display_label"],
            )
        ):
            errors.append("chapter_zero_presented_as_official_chapter")

    if chapter_zero_required and chapter_zero is None:
        errors.append("chapter_zero_navigation_assignment_missing")

    return {
        "assignments": records,
        "chapter_zero": chapter_zero,
        "valid": not errors,
        "errors": sorted(set(errors)),
    }


def _rule_claims_chapter_zero(rule: Mapping[str, Any]) -> bool:
    """Recognize legacy/project navigation hints without calling them official."""

    for field in ("chapter_id", "chapter", "chapter_code", "navigation_code"):
        value = rule.get(field)
        if value in (0, "0", "00", _CHAPTER_ZERO_CODE):
            return True
    return rule.get("source_designation_raw") == _CHAPTER_ZERO_SOURCE_LABEL


def audit_rule_history(
    rule: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
    source_date_annotations: Sequence[Mapping[str, Any]],
    *,
    navigation_assignments: Sequence[Mapping[str, Any]] = (),
    declared_cut: str | date,
    source_universe_closed: bool | None = None,
) -> dict[str, Any]:
    """Audit one rule and return a stable JSON-compatible result.

    Readiness inputs may live on the rule record:

    ``source_universe_closed``, ``cumulative_anchor_parity``,
    ``direct_edge_count``, and ``unresolved_gap_count``.

    The function deliberately treats missing readiness evidence as a gap.
    ``source_universe_closed`` may be supplied as a dataset-wide keyword; a
    per-rule value takes precedence when present.
    """

    rule_id = _rule_id(rule)
    if rule_id is None:
        raise HistoryCoverageError("rule_id is required")
    cut, cut_error = _as_iso_date(declared_cut)
    if cut_error is not None:
        raise HistoryCoverageError("declared_cut must be an ISO date")
    assert cut is not None

    rule_versions = [dict(item) for item in versions if _rule_id(item) == rule_id]
    version_id_to_rule: dict[str, str] = {}
    for index, version in enumerate(rule_versions):
        version_id = _record_id(
            version, ("snapshot_id", "version_id"), f"{rule_id}:version:{index}"
        )
        version_id_to_rule[version_id] = rule_id

    rule_annotations: list[dict[str, Any]] = []
    for annotation in source_date_annotations:
        annotation_rule_id = _rule_id(annotation)
        snapshot_id = annotation.get("snapshot_id")
        if annotation_rule_id == rule_id or (
            annotation_rule_id is None
            and snapshot_id is not None
            and version_id_to_rule.get(str(snapshot_id)) == rule_id
        ):
            rule_annotations.append(dict(annotation))

    version_dates: list[str] = []
    version_date_errors: list[dict[str, str]] = []
    version_entries: list[tuple[str, str, str | None]] = []
    post_cut_version_dates: list[str] = []
    for index, version in enumerate(rule_versions):
        version_id = _record_id(
            version, ("snapshot_id", "version_id"), f"{rule_id}:version:{index}"
        )
        iso_value, error = _as_iso_date(
            version.get("effective_from", version.get("effective_date"))
        )
        if error is not None:
            version_date_errors.append({"version_id": version_id, "error": error})
            continue
        assert iso_value is not None
        if iso_value > cut:
            post_cut_version_dates.append(iso_value)
            continue
        version_dates.append(iso_value)
        version_entries.append((version_id, iso_value, _text_digest(version)))

    accepted_annotations: list[dict[str, Any]] = []
    rejected_annotation_count = 0
    annotation_date_errors: list[dict[str, str]] = []
    post_cut_source_dates: list[str] = []
    unresolved_annotation_ids: list[str] = []
    unverified_transition_annotation_ids: list[str] = []
    for index, annotation in enumerate(rule_annotations):
        annotation_id = _record_id(
            annotation,
            ("annotation_id",),
            f"{rule_id}:annotation:{index}",
        )
        resolution_status = str(annotation.get("resolution_status", ""))
        if resolution_status not in _RESOLVED_ANNOTATION_STATES:
            unresolved_annotation_ids.append(annotation_id)
        if resolution_status == _REJECTED_ANNOTATION_STATE:
            rejected_annotation_count += 1
            continue

        iso_value, error = _as_iso_date(
            annotation.get("iso_date_candidate", annotation.get("effective_date"))
        )
        if error is not None:
            annotation_date_errors.append(
                {"annotation_id": annotation_id, "error": error}
            )
            continue
        assert iso_value is not None
        if iso_value > cut:
            post_cut_source_dates.append(iso_value)
            continue
        accepted_annotations.append(
            {
                "annotation_id": annotation_id,
                "date": iso_value,
                "resolution_status": resolution_status,
            }
        )
        if resolution_status != _TRANSITION_VERIFIED_STATE:
            unverified_transition_annotation_ids.append(annotation_id)

    declared_source_dates = _sorted_unique(
        item["date"] for item in accepted_annotations
    )
    effective_version_dates = _sorted_unique(version_dates)
    source_dates_without_version = sorted(
        set(declared_source_dates) - set(effective_version_dates)
    )
    version_dates_without_source = sorted(
        set(effective_version_dates) - set(declared_source_dates)
    )

    versions_by_date: dict[str, list[str]] = defaultdict(list)
    for version_id, iso_value, _ in version_entries:
        versions_by_date[iso_value].append(version_id)
    duplicate_effective_dates = [
        {"date": iso_value, "version_ids": sorted(version_ids)}
        for iso_value, version_ids in sorted(versions_by_date.items())
        if len(version_ids) > 1
    ]

    dates_by_digest: dict[str, set[str]] = defaultdict(set)
    version_ids_by_digest: dict[str, list[str]] = defaultdict(list)
    for version_id, iso_value, digest in version_entries:
        if digest is None:
            continue
        dates_by_digest[digest].add(iso_value)
        version_ids_by_digest[digest].append(version_id)
    identical_text_at_different_dates = [
        {
            "text_sha256": digest,
            "dates": sorted(dates),
            "version_ids": sorted(version_ids_by_digest[digest]),
        }
        for digest, dates in sorted(dates_by_digest.items())
        if len(dates) > 1
    ]

    navigation = _navigation_result(
        rule_id,
        navigation_assignments,
        chapter_zero_required=_rule_claims_chapter_zero(rule),
    )
    snapshot_count = len(version_entries)
    expected_direct_edge_count = max(snapshot_count - 1, 0)
    direct_edge_count = rule.get("direct_edge_count")
    unresolved_gap_count = rule.get("unresolved_gap_count")
    anchor_parity = rule.get("cumulative_anchor_parity")
    rule_source_universe_closed = rule.get(
        "source_universe_closed", source_universe_closed
    )

    gap_reasons: list[str] = []
    if rule.get("identity_status") == "unresolved":
        gap_reasons.append("rule_identity_unresolved")
    if version_date_errors:
        gap_reasons.append("version_effective_date_missing_or_invalid")
    if annotation_date_errors:
        gap_reasons.append("annotation_iso_date_missing_or_invalid")
    if unresolved_annotation_ids:
        gap_reasons.append("source_annotation_unresolved")
    if unverified_transition_annotation_ids:
        gap_reasons.append("annotation_transition_not_verified")
    if source_dates_without_version:
        gap_reasons.append("declared_source_date_without_version")
    if version_dates_without_source:
        gap_reasons.append("version_date_without_source_annotation")
    if duplicate_effective_dates:
        gap_reasons.append("duplicate_version_effective_date")
    if identical_text_at_different_dates:
        gap_reasons.append("identical_text_at_different_dates_unadjudicated")
    if not navigation["valid"]:
        gap_reasons.extend(navigation["errors"])
    if direct_edge_count is None:
        gap_reasons.append("direct_edge_count_not_provided")
    elif isinstance(direct_edge_count, bool) or not isinstance(direct_edge_count, int):
        gap_reasons.append("direct_edge_count_invalid")
    elif direct_edge_count != expected_direct_edge_count:
        gap_reasons.append("direct_predecessor_edges_incomplete")
    if unresolved_gap_count is None:
        gap_reasons.append("unresolved_gap_count_not_provided")
    elif (
        isinstance(unresolved_gap_count, bool)
        or not isinstance(unresolved_gap_count, int)
        or unresolved_gap_count < 0
    ):
        gap_reasons.append("unresolved_gap_count_invalid")
    elif unresolved_gap_count != 0:
        gap_reasons.append("unresolved_gaps_present")
    if rule_source_universe_closed is not True:
        gap_reasons.append("source_universe_not_closed")
    if anchor_parity is not True:
        gap_reasons.append("cumulative_anchor_parity_not_passed")

    gap_reasons = sorted(set(gap_reasons))
    complete = not gap_reasons
    return {
        "rule_id": rule_id,
        "declared_cut": cut,
        "declared_source_date_set": declared_source_dates,
        "version_effective_date_set": effective_version_dates,
        "unmatched": {
            "source_dates_without_version": source_dates_without_version,
            "version_dates_without_source_annotation": version_dates_without_source,
        },
        "duplicates": {
            "effective_date_groups": duplicate_effective_dates,
        },
        "identical_text_at_different_dates": identical_text_at_different_dates,
        "flags": {
            "has_unmatched_dates": bool(
                source_dates_without_version or version_dates_without_source
            ),
            "has_duplicate_effective_dates": bool(duplicate_effective_dates),
            "has_identical_text_at_different_dates": bool(
                identical_text_at_different_dates
            ),
            "has_missing_or_invalid_dates": bool(
                version_date_errors or annotation_date_errors
            ),
        },
        "counts": {
            "annotation_count": len(rule_annotations),
            "accepted_annotation_count": len(accepted_annotations),
            "rejected_non_amendment_count": rejected_annotation_count,
            "resolved_annotation_count": (
                len(rule_annotations) - len(unresolved_annotation_ids)
            ),
            "verified_transition_count": (
                len(accepted_annotations)
                - len(unverified_transition_annotation_ids)
            ),
            "snapshot_count": snapshot_count,
            "direct_edge_count": direct_edge_count,
            "expected_direct_edge_count": expected_direct_edge_count,
            "unresolved_gap_count": unresolved_gap_count,
        },
        "date_errors": {
            "versions": version_date_errors,
            "annotations": annotation_date_errors,
        },
        "post_cut": {
            "source_dates": _sorted_unique(post_cut_source_dates),
            "version_dates": _sorted_unique(post_cut_version_dates),
        },
        "annotation_resolution": {
            "unresolved_annotation_ids": sorted(unresolved_annotation_ids),
            "unverified_transition_annotation_ids": sorted(
                unverified_transition_annotation_ids
            ),
        },
        "navigation": navigation,
        "readiness": {
            "source_universe_closed": rule_source_universe_closed is True,
            "cumulative_anchor_parity": anchor_parity is True,
        },
        "gap_reasons": gap_reasons,
        "complete_to_declared_cut": complete,
        "completion_status": (
            "complete_to_declared_cut" if complete else "blocked"
        ),
    }


def audit_history_coverage(
    rules: Sequence[Mapping[str, Any]],
    versions: Sequence[Mapping[str, Any]],
    source_date_annotations: Sequence[Mapping[str, Any]],
    *,
    navigation_assignments: Sequence[Mapping[str, Any]] = (),
    declared_cut: str | date,
    source_universe_closed: bool | None = None,
) -> dict[str, Any]:
    """Audit all rules and return a stable machine-readable envelope."""

    seen_rule_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    for rule in rules:
        rule_id = _rule_id(rule)
        if rule_id is None:
            raise HistoryCoverageError("every rule requires rule_id")
        if rule_id in seen_rule_ids:
            raise HistoryCoverageError(f"duplicate rule_id: {rule_id}")
        seen_rule_ids.add(rule_id)
        results.append(
            audit_rule_history(
                rule,
                versions,
                source_date_annotations,
                navigation_assignments=navigation_assignments,
                declared_cut=declared_cut,
                source_universe_closed=source_universe_closed,
            )
        )
    results.sort(key=lambda row: row["rule_id"])

    orphan_version_ids = sorted(
        _record_id(version, ("snapshot_id", "version_id"), f"version:{index}")
        for index, version in enumerate(versions)
        if _rule_id(version) not in seen_rule_ids
    )
    known_snapshot_ids = {
        _record_id(version, ("snapshot_id", "version_id"), f"version:{index}")
        for index, version in enumerate(versions)
        if _rule_id(version) in seen_rule_ids
    }
    orphan_annotation_ids = sorted(
        _record_id(
            annotation,
            ("annotation_id",),
            f"annotation:{index}",
        )
        for index, annotation in enumerate(source_date_annotations)
        if (
            _rule_id(annotation) not in (None, *seen_rule_ids)
            or (
                _rule_id(annotation) is None
                and str(annotation.get("snapshot_id", "")) not in known_snapshot_ids
            )
        )
    )
    complete_count = sum(
        result["complete_to_declared_cut"] for result in results
    )
    return {
        "schema": AUDIT_SCHEMA,
        "declared_cut": _as_iso_date(declared_cut)[0],
        "canonical_history_claim": (
            bool(results)
            and complete_count == len(results)
            and not orphan_version_ids
            and not orphan_annotation_ids
        ),
        "counts": {
            "rules": len(results),
            "complete_to_declared_cut": complete_count,
            "blocked": len(results) - complete_count,
            "orphan_versions": len(orphan_version_ids),
            "orphan_annotations": len(orphan_annotation_ids),
        },
        "orphan_version_ids": orphan_version_ids,
        "orphan_annotation_ids": orphan_annotation_ids,
        "rules": results,
    }


def audit_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """CLI-shaped pure entry point for a decoded JSON document."""

    required = ("rules", "versions", "source_date_annotations", "declared_cut")
    missing = [field for field in required if field not in document]
    if missing:
        raise HistoryCoverageError(
            "missing audit document fields: " + ", ".join(sorted(missing))
        )
    for field in ("rules", "versions", "source_date_annotations"):
        if not isinstance(document[field], list):
            raise HistoryCoverageError(f"{field} must be an array")
    navigation_assignments = document.get("navigation_assignments", [])
    if not isinstance(navigation_assignments, list):
        raise HistoryCoverageError("navigation_assignments must be an array")
    return audit_history_coverage(
        document["rules"],
        document["versions"],
        document["source_date_annotations"],
        navigation_assignments=navigation_assignments,
        declared_cut=document["declared_cut"],
        source_universe_closed=document.get("source_universe_closed"),
    )
