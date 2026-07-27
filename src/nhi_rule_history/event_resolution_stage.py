"""Stage date-annotation to official-event/effect resolution candidates.

This module is intentionally isolated from canonical legal history.  It accepts
caller-supplied annotation observations and official event/effect observations,
preserves every input plus every same-date comparison, and emits one
fail-closed outcome per annotation.  ``resolved_candidate`` means only that one
unambiguous evidence candidate survived the mechanical gates; it never promotes
or mutates a canonical rule, version, event, or history edge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


SCHEMA = "nhi_rule_history_event_resolution_stage"
GLOBAL_LOCK_KEY = "nhi_rule_history_event_resolution_stage-global"
CONTRACT_VERSION = "nhi-rule-history/event-resolution-stage/v1"
RESOLVER_VERSION = "nhi-rule-history/exact-date-designation-resolver/1.0.1"
LOADER_VERSION = "nhi-rule-history/event-resolution-stage-loader/1.0.0"
ANNOTATION_SCHEMA = "nhi-rule-history/event-resolution-annotation-input/v1"
OFFICIAL_SCHEMA = "nhi-rule-history/official-event-effect-input/v1"
CANDIDATE_SCHEMA = "nhi-rule-history/event-resolution-candidate/v1"
OUTCOME_SCHEMA = "nhi-rule-history/event-resolution-outcome/v1"
DSN_ENV = "NHI_RULE_HISTORY_DSN"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_event_resolution_stage.sql"
)

_UUID_NAMESPACE = uuid.UUID("190674df-ad64-55cc-b16d-a781d32eb1a2")
_STATUSES = ("resolved_candidate", "ambiguous", "no_match", "invalid")


class EventResolutionStageError(PgLoadError):
    """Invalid evidence input, staging failure, or verification mismatch."""


@dataclass(frozen=True)
class EventResolutionStageMaterial:
    run_id: str
    annotations: tuple[dict[str, Any], ...]
    official_observations: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]
    outcomes: tuple[dict[str, Any], ...]
    migration_sha256: str
    code_sha256: str
    input_fingerprint: str
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    output_fingerprint: str
    sealed_fingerprint: str


def _stable_uuid(label: str, *values: object) -> str:
    material = "\x1f".join([label, *(str(value) for value in values)])
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def _canonical_json(value: Any, *, field: str) -> Any:
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EventResolutionStageError(
            f"{field} is not canonical JSON"
        ) from exc


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise EventResolutionStageError(
            f"{field} must be a string or integer"
        )
    result = str(value)
    if not result.strip():
        raise EventResolutionStageError(f"{field} is empty")
    return result


def _optional_text(record: Mapping[str, Any], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventResolutionStageError(f"{field} must be a string or null")
    return value


def _boolean(record: Mapping[str, Any], field: str) -> bool:
    value = record.get(field, False)
    if not isinstance(value, bool):
        raise EventResolutionStageError(f"{field} must be boolean")
    return value


def _locator(record: Mapping[str, Any], field: str) -> tuple[Any, bool]:
    value = record.get(field)
    if value is None:
        return None, False
    canonical = _canonical_json(value, field=field)
    if isinstance(canonical, str):
        return canonical, bool(canonical.strip())
    if isinstance(canonical, (dict, list)):
        return canonical, bool(canonical)
    return canonical, False


def _parse_iso_day(value: Any) -> tuple[str | None, bool]:
    if not isinstance(value, str):
        return None, False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, False
    normalized = parsed.isoformat()
    return normalized, normalized == value


def _normalize_designation(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(normalized.split()).casefold()
    # Official comparison tables inconsistently retain the punctuation after a
    # numeric designation (for example ``5.6.3.`` versus ``5.6.3``).  Treat
    # only terminal punctuation immediately following a digit as typography;
    # punctuation inside the designation or after a name remains significant.
    normalized = re.sub(r"(?<=\d)[.:。：]+$", "", normalized)
    return normalized or None


def _prepare_annotation(record: Mapping[str, Any]) -> dict[str, Any]:
    annotation_id = _required_text(record, "annotation_id")
    article_id = _required_text(record, "article_id")
    raw_designation = _optional_text(record, "source_designation_raw")
    normalized_designation = _normalize_designation(raw_designation)
    designation_omitted = (
        _boolean(record, "designation_omitted")
        or normalized_designation is None
    )
    multiple_clause_ambiguity = _boolean(
        record, "multiple_clause_ambiguity"
    )
    source_locator, source_locator_present = _locator(
        record, "source_locator"
    )
    normalization_status = record.get("normalization_status")
    if normalization_status not in (
        "normalized",
        "invalid_calendar_date",
    ):
        raise EventResolutionStageError(
            "normalization_status must be normalized or "
            "invalid_calendar_date"
        )
    raw_iso_candidate = record.get("normalized_iso_candidate")
    iso_candidate, iso_date_valid = _parse_iso_day(raw_iso_candidate)
    if normalization_status != "normalized":
        iso_candidate = None
        iso_date_valid = False
    caller_observation = _canonical_json(
        dict(record), field="annotation observation"
    )
    caller_record_sha256 = row_sha256(
        {
            "schema": ANNOTATION_SCHEMA,
            "observation": caller_observation,
        }
    )
    prepared = {
        "annotation_id": annotation_id,
        "article_id": article_id,
        "normalized_iso_candidate": iso_candidate,
        "iso_date_valid": iso_date_valid,
        "normalization_status": normalization_status,
        "source_designation_raw": raw_designation,
        "source_designation_normalized": normalized_designation,
        "designation_omitted": designation_omitted,
        "multiple_clause_ambiguity": multiple_clause_ambiguity,
        "source_locator": source_locator,
        "source_locator_present": source_locator_present,
        "caller_observation": caller_observation,
        "caller_record_sha256": caller_record_sha256,
    }
    prepared["source_row_sha256"] = row_sha256(
        {
            "schema": "nhi-rule-history/staged-resolution-annotation/v1",
            **prepared,
        }
    )
    return prepared


def _prepare_official(record: Mapping[str, Any]) -> dict[str, Any]:
    official_event_id = _required_text(record, "official_event_id")
    official_effect_id = _required_text(record, "official_effect_id")
    raw_designation = _optional_text(record, "source_designation_raw")
    normalized_designation = _normalize_designation(raw_designation)
    designation_omitted = (
        _boolean(record, "designation_omitted")
        or normalized_designation is None
    )
    multiple_clause_ambiguity = _boolean(
        record, "multiple_clause_ambiguity"
    )
    omitted_text_present = _boolean(record, "omitted_text_present")
    source_locator, source_locator_present = _locator(
        record, "source_locator"
    )
    raw_effective_date = record.get("effective_date")
    effective_date, effective_date_valid = _parse_iso_day(
        raw_effective_date
    )
    caller_observation = _canonical_json(
        dict(record), field="official event/effect observation"
    )
    caller_record_sha256 = row_sha256(
        {
            "schema": OFFICIAL_SCHEMA,
            "observation": caller_observation,
        }
    )
    prepared = {
        "official_event_id": official_event_id,
        "official_effect_id": official_effect_id,
        "effective_date": effective_date,
        "effective_date_valid": effective_date_valid,
        "raw_effective_date": (
            raw_effective_date
            if isinstance(raw_effective_date, str)
            else None
        ),
        "source_designation_raw": raw_designation,
        "source_designation_normalized": normalized_designation,
        "designation_omitted": designation_omitted,
        "multiple_clause_ambiguity": multiple_clause_ambiguity,
        "omitted_text_present": omitted_text_present,
        "source_locator": source_locator,
        "source_locator_present": source_locator_present,
        "caller_observation": caller_observation,
        "caller_record_sha256": caller_record_sha256,
    }
    prepared["source_row_sha256"] = row_sha256(
        {
            "schema": "nhi-rule-history/staged-official-event-effect/v1",
            **prepared,
        }
    )
    return prepared


def _designation_compatibility(
    annotation: Mapping[str, Any],
    official: Mapping[str, Any],
) -> str:
    if (
        annotation["designation_omitted"]
        or annotation["multiple_clause_ambiguity"]
        or official["designation_omitted"]
        or official["multiple_clause_ambiguity"]
        or official["omitted_text_present"]
    ):
        return "indeterminate"
    if (
        annotation["source_designation_normalized"]
        == official["source_designation_normalized"]
    ):
        return "compatible"
    return "incompatible"


def _candidate_blockers(
    annotation: Mapping[str, Any],
    official: Mapping[str, Any],
    compatibility: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if annotation["designation_omitted"]:
        blockers.append("annotation_designation_omitted")
    if annotation["multiple_clause_ambiguity"]:
        blockers.append("annotation_multiple_clause_ambiguity")
    if not annotation["source_locator_present"]:
        blockers.append("annotation_source_locator_missing")
    if official["designation_omitted"]:
        blockers.append("official_designation_omitted")
    if official["multiple_clause_ambiguity"]:
        blockers.append("official_multiple_clause_ambiguity")
    if official["omitted_text_present"]:
        blockers.append("official_omitted_text_present")
    if not official["source_locator_present"]:
        blockers.append("official_source_locator_missing")
    if compatibility == "incompatible":
        blockers.append("designation_incompatible")
    elif compatibility == "indeterminate":
        blockers.append("designation_compatibility_indeterminate")
    return tuple(sorted(set(blockers)))


def _outcome_for(
    annotation: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    compatible = [
        row
        for row in candidates
        if row["designation_compatibility"] == "compatible"
    ]
    indeterminate = [
        row
        for row in candidates
        if row["designation_compatibility"] == "indeterminate"
    ]
    eligible = [row for row in candidates if row["eligible"]]
    potential = compatible + indeterminate
    distinct_event_count = len(
        {row["official_event_id"] for row in potential}
    )
    reasons: list[str] = []
    selected_candidate_id: str | None = None

    if not annotation["iso_date_valid"]:
        status = "invalid"
        reasons.append("annotation_date_invalid")
    elif annotation["designation_omitted"]:
        status = "ambiguous"
        reasons.append("annotation_designation_omitted")
    elif annotation["multiple_clause_ambiguity"]:
        status = "ambiguous"
        reasons.append("annotation_multiple_clause_ambiguity")
    elif not annotation["source_locator_present"]:
        status = "ambiguous"
        reasons.append("annotation_source_locator_missing")
    elif distinct_event_count > 1:
        status = "ambiguous"
        reasons.append("multiple_official_events")
    elif indeterminate:
        status = "ambiguous"
        reasons.append("indeterminate_designation_scope")
    elif len(compatible) > 1:
        status = "ambiguous"
        reasons.append("multiple_official_event_effects")
    elif len(compatible) == 1 and len(eligible) == 0:
        status = "ambiguous"
        reasons.extend(compatible[0]["blocker_codes"])
    elif len(eligible) == 1:
        status = "resolved_candidate"
        reasons.append("one_exact_date_compatible_designation_candidate")
        selected_candidate_id = eligible[0]["candidate_id"]
    else:
        status = "no_match"
        if candidates:
            reasons.append("no_compatible_source_designation")
        else:
            reasons.append("no_official_effect_on_exact_date")

    outcome = {
        "schema": OUTCOME_SCHEMA,
        "annotation_id": annotation["annotation_id"],
        "resolution_status": status,
        "reason_codes": sorted(set(reasons)),
        "candidate_count": len(candidates),
        "compatible_candidate_count": len(compatible),
        "eligible_candidate_count": len(eligible),
        "distinct_event_count": distinct_event_count,
        "selected_candidate_id": selected_candidate_id,
        "canonical_history_written": False,
    }
    outcome["outcome_id"] = _stable_uuid(
        "event-resolution-outcome",
        annotation["source_row_sha256"],
        object_fingerprint(
            {
                "candidate_ids": sorted(
                    row["candidate_id"] for row in candidates
                ),
                "resolution_status": status,
                "reason_codes": outcome["reason_codes"],
                "selected_candidate_id": selected_candidate_id,
            }
        ),
    )
    outcome["source_row_sha256"] = row_sha256(outcome)
    return outcome


def prepare_event_resolution_stage(
    annotation_rows: Iterable[Mapping[str, Any]],
    official_event_effect_rows: Iterable[Mapping[str, Any]],
) -> EventResolutionStageMaterial:
    """Build a deterministic, order-independent evidence-only resolution run."""

    annotations: list[dict[str, Any]] = []
    officials: list[dict[str, Any]] = []
    seen_annotations: set[str] = set()
    seen_officials: set[tuple[str, str]] = set()

    for input_index, record in enumerate(annotation_rows):
        if not isinstance(record, Mapping):
            raise EventResolutionStageError(
                f"annotation record {input_index} is not an object"
            )
        prepared = _prepare_annotation(record)
        identity = prepared["annotation_id"]
        if identity in seen_annotations:
            raise EventResolutionStageError(
                f"duplicate annotation_id: {identity}"
            )
        seen_annotations.add(identity)
        annotations.append(prepared)

    for input_index, record in enumerate(official_event_effect_rows):
        if not isinstance(record, Mapping):
            raise EventResolutionStageError(
                f"official record {input_index} is not an object"
            )
        prepared = _prepare_official(record)
        identity = (
            prepared["official_event_id"],
            prepared["official_effect_id"],
        )
        if identity in seen_officials:
            raise EventResolutionStageError(
                "duplicate official event/effect identity: "
                f"{identity[0]}/{identity[1]}"
            )
        seen_officials.add(identity)
        officials.append(prepared)

    annotations.sort(key=lambda row: row["annotation_id"])
    officials.sort(
        key=lambda row: (
            row["official_event_id"],
            row["official_effect_id"],
        )
    )
    officials_by_day: dict[str, list[dict[str, Any]]] = {}
    for official in officials:
        if official["effective_date_valid"]:
            officials_by_day.setdefault(
                official["effective_date"], []
            ).append(official)

    candidates: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for annotation in annotations:
        annotation_candidates: list[dict[str, Any]] = []
        if annotation["iso_date_valid"]:
            for official in officials_by_day.get(
                annotation["normalized_iso_candidate"], []
            ):
                compatibility = _designation_compatibility(
                    annotation, official
                )
                blockers = _candidate_blockers(
                    annotation, official, compatibility
                )
                candidate = {
                    "schema": CANDIDATE_SCHEMA,
                    "annotation_id": annotation["annotation_id"],
                    "official_event_id": official["official_event_id"],
                    "official_effect_id": official["official_effect_id"],
                    "exact_effective_date": official["effective_date"],
                    "designation_compatibility": compatibility,
                    "blocker_codes": list(blockers),
                    "eligible": (
                        compatibility == "compatible" and not blockers
                    ),
                    "canonical_history_written": False,
                }
                candidate["candidate_id"] = _stable_uuid(
                    "event-resolution-candidate",
                    annotation["source_row_sha256"],
                    official["source_row_sha256"],
                )
                candidate["source_row_sha256"] = row_sha256(candidate)
                candidates.append(candidate)
                annotation_candidates.append(candidate)
        outcomes.append(_outcome_for(annotation, annotation_candidates))

    candidates.sort(
        key=lambda row: (
            row["annotation_id"],
            row["official_event_id"],
            row["official_effect_id"],
        )
    )
    outcomes.sort(key=lambda row: row["annotation_id"])

    annotation_hashes = [
        row["source_row_sha256"] for row in annotations
    ]
    official_hashes = [row["source_row_sha256"] for row in officials]
    candidate_hashes = [row["source_row_sha256"] for row in candidates]
    outcome_hashes = [row["source_row_sha256"] for row in outcomes]
    input_fingerprint = object_fingerprint(
        {
            "contract_version": CONTRACT_VERSION,
            "annotation_caller_rows": sorted(
                row["caller_record_sha256"] for row in annotations
            ),
            "official_caller_rows": sorted(
                row["caller_record_sha256"] for row in officials
            ),
        }
    )
    run_id = _stable_uuid("event-resolution-run", input_fingerprint)
    status_counts = {
        status: sum(
            row["resolution_status"] == status for row in outcomes
        )
        for status in _STATUSES
    }
    expected_counts = {
        "annotation_observation": len(annotations),
        "official_event_effect_observation": len(officials),
        "candidate_observation": len(candidates),
        "resolution_outcome": len(outcomes),
        **status_counts,
        "canonical_history_written": 0,
    }
    table_fingerprints = {
        "annotation_observation": row_set_fingerprint(annotation_hashes),
        "official_event_effect_observation": row_set_fingerprint(
            official_hashes
        ),
        "candidate_observation": row_set_fingerprint(candidate_hashes),
        "resolution_outcome": row_set_fingerprint(outcome_hashes),
    }
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "table_fingerprints": table_fingerprints,
        }
    )
    migration_sha256 = migration_fingerprint(MIGRATION)
    code_sha256 = code_fingerprint(
        Path(__file__),
        Path(__file__).with_name("pg") / "common.py",
    )
    sealed_fingerprint = object_fingerprint(
        {
            "loader_version": LOADER_VERSION,
            "contract_version": CONTRACT_VERSION,
            "resolver_version": RESOLVER_VERSION,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
        }
    )
    return EventResolutionStageMaterial(
        run_id=run_id,
        annotations=tuple(annotations),
        official_observations=tuple(officials),
        candidates=tuple(candidates),
        outcomes=tuple(outcomes),
        migration_sha256=migration_sha256,
        code_sha256=code_sha256,
        input_fingerprint=input_fingerprint,
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
        output_fingerprint=output_fingerprint,
        sealed_fingerprint=sealed_fingerprint,
    )


def _default_connect(conninfo: str):
    try:
        import psycopg
    except ImportError as exc:
        raise EventResolutionStageError(
            "psycopg is required for PostgreSQL staging"
        ) from exc
    return psycopg.connect(conninfo)


def _apply_material(
    material: EventResolutionStageMaterial,
    *,
    conninfo: str,
    connect: Callable[[str], Any],
) -> bool:
    replayed = False
    with connect(conninfo) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (GLOBAL_LOCK_KEY,),
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{SCHEMA}:{material.input_fingerprint}",),
            )
            cursor.execute(
                f"""
                SELECT run_id, sealed_fingerprint
                FROM {SCHEMA}.resolution_run
                WHERE input_fingerprint = %s
                """,
                (material.input_fingerprint,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != material.run_id
                    or existing[1] != material.sealed_fingerprint
                ):
                    raise EventResolutionStageError(
                        "input fingerprint already exists with different seal"
                    )
                replayed = True
            else:
                status_counts = {
                    status: material.expected_counts[status]
                    for status in _STATUSES
                }
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.resolution_run (
                      run_id, contract_version, resolver_version,
                      migration_sha256, code_sha256, input_fingerprint,
                      output_fingerprint, sealed_fingerprint,
                      annotation_count, official_observation_count,
                      candidate_count, resolved_candidate_count,
                      ambiguous_count, no_match_count, invalid_count,
                      canonical_history_written, expected_counts,
                      table_fingerprints, state
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      false,%s::jsonb,%s::jsonb,'sealed'
                    )
                    """,
                    (
                        material.run_id,
                        CONTRACT_VERSION,
                        RESOLVER_VERSION,
                        material.migration_sha256,
                        material.code_sha256,
                        material.input_fingerprint,
                        material.output_fingerprint,
                        material.sealed_fingerprint,
                        material.expected_counts["annotation_observation"],
                        material.expected_counts[
                            "official_event_effect_observation"
                        ],
                        material.expected_counts["candidate_observation"],
                        status_counts["resolved_candidate"],
                        status_counts["ambiguous"],
                        status_counts["no_match"],
                        status_counts["invalid"],
                        json_text(material.expected_counts),
                        json_text(material.table_fingerprints),
                    ),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.annotation_observation (
                      run_id, annotation_id, article_id,
                      normalized_iso_candidate, iso_date_valid,
                      normalization_status, source_designation_raw,
                      source_designation_normalized, designation_omitted,
                      multiple_clause_ambiguity, source_locator,
                      source_locator_present, caller_observation,
                      caller_record_sha256, source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                      %s::jsonb,%s,%s
                    )
                    """,
                    [
                        (
                            material.run_id,
                            row["annotation_id"],
                            row["article_id"],
                            row["normalized_iso_candidate"],
                            row["iso_date_valid"],
                            row["normalization_status"],
                            row["source_designation_raw"],
                            row["source_designation_normalized"],
                            row["designation_omitted"],
                            row["multiple_clause_ambiguity"],
                            json_text(row["source_locator"]),
                            row["source_locator_present"],
                            json_text(row["caller_observation"]),
                            row["caller_record_sha256"],
                            row["source_row_sha256"],
                        )
                        for row in material.annotations
                    ],
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.official_event_effect_observation (
                      run_id, official_event_id, official_effect_id,
                      effective_date, effective_date_valid,
                      raw_effective_date, source_designation_raw,
                      source_designation_normalized, designation_omitted,
                      multiple_clause_ambiguity, omitted_text_present,
                      source_locator,
                      source_locator_present, caller_observation,
                      caller_record_sha256, source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                      %s::jsonb,%s,%s
                    )
                    """,
                    [
                        (
                            material.run_id,
                            row["official_event_id"],
                            row["official_effect_id"],
                            row["effective_date"],
                            row["effective_date_valid"],
                            row["raw_effective_date"],
                            row["source_designation_raw"],
                            row["source_designation_normalized"],
                            row["designation_omitted"],
                            row["multiple_clause_ambiguity"],
                            row["omitted_text_present"],
                            json_text(row["source_locator"]),
                            row["source_locator_present"],
                            json_text(row["caller_observation"]),
                            row["caller_record_sha256"],
                            row["source_row_sha256"],
                        )
                        for row in material.official_observations
                    ],
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.candidate_observation (
                      run_id, candidate_id, annotation_id,
                      official_event_id, official_effect_id,
                      exact_effective_date, designation_compatibility,
                      blocker_codes, eligible, canonical_history_written,
                      source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,false,%s
                    )
                    """,
                    [
                        (
                            material.run_id,
                            row["candidate_id"],
                            row["annotation_id"],
                            row["official_event_id"],
                            row["official_effect_id"],
                            row["exact_effective_date"],
                            row["designation_compatibility"],
                            json_text(row["blocker_codes"]),
                            row["eligible"],
                            row["source_row_sha256"],
                        )
                        for row in material.candidates
                    ],
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.resolution_outcome (
                      run_id, outcome_id, annotation_id, resolution_status,
                      reason_codes, candidate_count,
                      compatible_candidate_count, eligible_candidate_count,
                      distinct_event_count, selected_candidate_id,
                      canonical_history_written, source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,false,%s
                    )
                    """,
                    [
                        (
                            material.run_id,
                            row["outcome_id"],
                            row["annotation_id"],
                            row["resolution_status"],
                            json_text(row["reason_codes"]),
                            row["candidate_count"],
                            row["compatible_candidate_count"],
                            row["eligible_candidate_count"],
                            row["distinct_event_count"],
                            row["selected_candidate_id"],
                            row["source_row_sha256"],
                        )
                        for row in material.outcomes
                    ],
                )
    return replayed


def verify_loaded_event_resolution_stage(
    run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: EventResolutionStageMaterial | None = None,
) -> dict[str, Any]:
    """Fresh-connect and recompute counts plus row-set fingerprints."""

    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, table_fingerprints,
                       input_fingerprint, output_fingerprint,
                       sealed_fingerprint, canonical_history_written
                FROM {SCHEMA}.resolution_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if (
                run is None
                or run[0] != "sealed"
                or bool(run[6])
            ):
                raise EventResolutionStageError(
                    "fresh verification found no sealed stage-only run"
                )
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in (
                "annotation_observation",
                "official_event_effect_observation",
                "candidate_observation",
                "resolution_outcome",
            ):
                cursor.execute(
                    f"""
                    SELECT source_row_sha256
                    FROM {SCHEMA}.{table}
                    WHERE run_id = %s
                    ORDER BY source_row_sha256
                    """,
                    (run_id,),
                )
                hashes = [row[0] for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT resolved_candidate_count, ambiguous_count,
                       no_match_count, invalid_count,
                       canonical_history_written
                FROM {SCHEMA}.v_resolution_status_counts
                WHERE run_id = %s
                """,
                (run_id,),
            )
            status_row = cursor.fetchone()
            if status_row is None or bool(status_row[4]):
                raise EventResolutionStageError(
                    "resolution status projection is missing or canonical"
                )
            for index, status in enumerate(_STATUSES):
                counts[status] = int(status_row[index])
            counts["canonical_history_written"] = 0

    stored_expected = dict(run[1])
    stored_fingerprints = dict(run[2])
    if counts != stored_expected:
        raise EventResolutionStageError(
            "fresh event-resolution count verification failed"
        )
    if fingerprints != stored_fingerprints:
        raise EventResolutionStageError(
            "fresh event-resolution row fingerprint verification failed"
        )
    output_fingerprint = object_fingerprint(
        {
            "counts": counts,
            "table_fingerprints": fingerprints,
        }
    )
    if output_fingerprint != run[4]:
        raise EventResolutionStageError(
            "fresh event-resolution output fingerprint verification failed"
        )
    if expected is not None and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or run[3] != expected.input_fingerprint
        or run[4] != expected.output_fingerprint
        or run[5] != expected.sealed_fingerprint
    ):
        raise EventResolutionStageError(
            "fresh event-resolution verification differs from input"
        )
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "input_fingerprint": run[3],
        "output_fingerprint": run[4],
        "sealed_fingerprint": run[5],
        "canonical_history_written": False,
    }


def load_event_resolution_stage(
    annotation_rows: Iterable[Mapping[str, Any]],
    official_event_effect_rows: Iterable[Mapping[str, Any]],
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Load once, replay idempotently, and verify through a fresh connection."""

    material = prepare_event_resolution_stage(
        annotation_rows, official_event_effect_rows
    )
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    replayed = _apply_material(
        material,
        conninfo=dsn,
        connect=connector,
    )
    verification = verify_loaded_event_resolution_stage(
        material.run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    return {
        "run_id": material.run_id,
        "replayed": replayed,
        "verification": verification,
    }
