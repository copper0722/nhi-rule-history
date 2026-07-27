"""Extract and stage source-local ROC date markers from legacy rule articles.

This module is deliberately non-canonical.  It preserves caller-supplied
current text, exact source identity, and exact marker offsets in an isolated
append-only PostgreSQL schema.  A normalized date is only a calendar
candidate; every marker starts as ``unresolved_event``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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


SCHEMA = "nhi_rule_history_annotation_stage"
GLOBAL_LOCK_KEY = "nhi_rule_history_annotation_stage-global"
CONTRACT_VERSION = "nhi-rule-history/legacy-annotation-stage/v1"
EXTRACTOR_VERSION = "nhi-rule-history/roc-date-marker-extractor/1.0.0"
LOADER_VERSION = "nhi-rule-history/annotation-stage-loader/1.0.0"
ARTICLE_ROW_SCHEMA = "nhi-rule-history/legacy-article-observation/v1"
ANNOTATION_ROW_SCHEMA = "nhi-rule-history/source-date-annotation/v1"
DSN_ENV = "NHI_RULE_HISTORY_DSN"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_annotation_stage.sql"
)

_UUID_NAMESPACE = uuid.UUID("0f0fbbf4-9868-55db-9e36-33721b829c8c")
_ROC_DATE_RE = re.compile(
    r"(?<![0-9])"
    r"(?P<year>[0-9]{2,3})"
    r"[ \t]*[/／][ \t]*"
    r"(?P<month>[0-9]{1,2})"
    r"[ \t]*[/／][ \t]*"
    r"(?P<day>[0-9]{1,2})"
    r"(?![0-9])"
)


class AnnotationStageError(PgLoadError):
    """Invalid legacy input, staging failure, or verification mismatch."""


@dataclass(frozen=True)
class AnnotationStageMaterial:
    run_id: str
    articles: tuple[dict[str, Any], ...]
    annotations: tuple[dict[str, Any], ...]
    migration_sha256: str
    code_sha256: str
    input_fingerprint: str
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    output_fingerprint: str
    sealed_fingerprint: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_uuid(label: str, *values: object) -> str:
    material = "\x1f".join([label, *(str(value) for value in values)])
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def _normalize_iso_candidate(
    roc_year: int, month: int, day: int
) -> tuple[str | None, str]:
    if roc_year < 1:
        return None, "invalid_calendar_date"
    try:
        candidate = date(roc_year + 1911, month, day)
    except ValueError:
        return None, "invalid_calendar_date"
    return candidate.isoformat(), "normalized"


def extract_roc_date_markers(full_text: str) -> tuple[dict[str, Any], ...]:
    """Return every source-local ROC slash-date marker in character order.

    Offsets are zero-based Unicode-code-point offsets with an exclusive end.
    Spacing and slash width remain byte-for-byte identical in
    ``raw_expression``.  Invalid calendar expressions remain visible with a
    null ISO candidate instead of being silently discarded.
    """

    if not isinstance(full_text, str):
        raise AnnotationStageError("full_text must be a string")
    markers: list[dict[str, Any]] = []
    for ordinal, match in enumerate(_ROC_DATE_RE.finditer(full_text)):
        raw_expression = match.group(0)
        roc_year = int(match.group("year"))
        roc_month = int(match.group("month"))
        roc_day = int(match.group("day"))
        iso_candidate, normalization_status = _normalize_iso_candidate(
            roc_year, roc_month, roc_day
        )
        unresolved_reason = (
            "legacy_marker_not_yet_linked_to_official_event"
            if normalization_status == "normalized"
            else "invalid_roc_calendar_date"
        )
        markers.append(
            {
                "marker_ordinal": ordinal,
                "char_start": match.start(),
                "char_end": match.end(),
                "raw_expression": raw_expression,
                "raw_expression_sha256": _sha256_text(raw_expression),
                "roc_year": roc_year,
                "roc_month": roc_month,
                "roc_day": roc_day,
                "normalized_iso_candidate": iso_candidate,
                "normalization_status": normalization_status,
                "resolution_status": "unresolved_event",
                "unresolved_reason": unresolved_reason,
            }
        )
    return tuple(markers)


def _identity_json(value: Any) -> Any:
    if isinstance(value, str):
        if not value.strip():
            raise AnnotationStageError("source_identity string is empty")
    elif isinstance(value, Mapping):
        if not value:
            raise AnnotationStageError("source_identity object is empty")
        if any(not isinstance(key, str) for key in value):
            raise AnnotationStageError(
                "source_identity object keys must be strings"
            )
        value = dict(value)
    else:
        raise AnnotationStageError(
            "source_identity must be a non-empty string or object"
        )
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AnnotationStageError(
            "source_identity is not canonical JSON"
        ) from exc


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AnnotationStageError(f"{field} must be a string or integer")
    result = str(value)
    if not result.strip():
        raise AnnotationStageError(f"{field} is empty")
    return result


def prepare_annotation_stage(
    records: Iterable[Mapping[str, Any]],
) -> AnnotationStageMaterial:
    """Validate caller records and build an order-independent sealed load."""

    prepared_articles: list[dict[str, Any]] = []
    prepared_annotations: list[dict[str, Any]] = []
    seen_article_ids: set[str] = set()

    for input_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise AnnotationStageError(
                f"record {input_index} is not an object"
            )
        article_id = _required_text(record, "article_id")
        article_num = _required_text(record, "article_num")
        if article_id in seen_article_ids:
            raise AnnotationStageError(
                f"duplicate article_id: {article_id}"
            )
        seen_article_ids.add(article_id)
        full_text = record.get("full_text")
        if not isinstance(full_text, str):
            raise AnnotationStageError("full_text must be a string")
        if "source_identity" not in record:
            raise AnnotationStageError("source_identity is required")
        source_identity = _identity_json(record["source_identity"])
        source_identity_sha256 = hashlib.sha256(
            canonical_json_bytes(source_identity)
        ).hexdigest()
        full_text_sha256 = _sha256_text(full_text)
        caller_row = {
            "schema": ARTICLE_ROW_SCHEMA,
            "article_id": article_id,
            "article_num": article_num,
            "full_text": full_text,
            "source_identity": source_identity,
        }
        caller_record_sha256 = row_sha256(caller_row)
        markers = extract_roc_date_markers(full_text)
        article = {
            "article_id": article_id,
            "article_num": article_num,
            "source_identity": source_identity,
            "source_identity_sha256": source_identity_sha256,
            "full_text": full_text,
            "full_text_sha256": full_text_sha256,
            "annotation_count": len(markers),
            "caller_record_sha256": caller_record_sha256,
        }
        article["source_row_sha256"] = row_sha256(
            {
                "schema": "nhi-rule-history/staged-legacy-article/v1",
                **article,
            }
        )
        prepared_articles.append(article)

        for marker in markers:
            annotation_id = _stable_uuid(
                "legacy-date-annotation",
                caller_record_sha256,
                marker["marker_ordinal"],
                marker["char_start"],
                marker["char_end"],
                marker["raw_expression_sha256"],
            )
            annotation = {
                "schema": ANNOTATION_ROW_SCHEMA,
                "annotation_id": annotation_id,
                "article_id": article_id,
                **marker,
            }
            annotation["source_row_sha256"] = row_sha256(annotation)
            prepared_annotations.append(annotation)

    prepared_articles.sort(key=lambda row: row["article_id"])
    prepared_annotations.sort(
        key=lambda row: (
            row["article_id"],
            row["marker_ordinal"],
            row["annotation_id"],
        )
    )

    article_row_hashes = [
        row["source_row_sha256"] for row in prepared_articles
    ]
    caller_record_hashes = [
        row["caller_record_sha256"] for row in prepared_articles
    ]
    annotation_hashes = [
        row["source_row_sha256"] for row in prepared_annotations
    ]
    input_fingerprint = object_fingerprint(
        {
            "contract_version": CONTRACT_VERSION,
            "caller_record_sha256": sorted(caller_record_hashes),
        }
    )
    run_id = _stable_uuid("legacy-annotation-run", input_fingerprint)
    expected_counts = {
        "legacy_article_observation": len(prepared_articles),
        "date_annotation": len(prepared_annotations),
        "article_with_annotation": sum(
            row["annotation_count"] > 0 for row in prepared_articles
        ),
        "normalized_annotation": sum(
            row["normalized_iso_candidate"] is not None
            for row in prepared_annotations
        ),
        "unresolved_annotation": len(prepared_annotations),
        "coverage_projection": len(prepared_articles),
        "article_annotation_total": len(prepared_annotations),
    }
    table_fingerprints = {
        "legacy_article_observation": row_set_fingerprint(
            article_row_hashes
        ),
        "date_annotation": row_set_fingerprint(annotation_hashes),
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
            "extractor_version": EXTRACTOR_VERSION,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
        }
    )
    return AnnotationStageMaterial(
        run_id=run_id,
        articles=tuple(prepared_articles),
        annotations=tuple(prepared_annotations),
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
        raise AnnotationStageError(
            "psycopg is required for PostgreSQL staging"
        ) from exc
    return psycopg.connect(conninfo)


def _apply_material(
    material: AnnotationStageMaterial,
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
                FROM {SCHEMA}.annotation_run
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
                    raise AnnotationStageError(
                        "input fingerprint already exists with different seal"
                    )
                replayed = True
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.annotation_run (
                      run_id, contract_version, extractor_version,
                      migration_sha256, code_sha256, input_fingerprint,
                      output_fingerprint, sealed_fingerprint, article_count,
                      article_with_annotation_count, annotation_count,
                      normalized_annotation_count,
                      unresolved_annotation_count, expected_counts,
                      table_fingerprints, state
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s::jsonb,%s::jsonb,'sealed'
                    )
                    """,
                    (
                        material.run_id,
                        CONTRACT_VERSION,
                        EXTRACTOR_VERSION,
                        material.migration_sha256,
                        material.code_sha256,
                        material.input_fingerprint,
                        material.output_fingerprint,
                        material.sealed_fingerprint,
                        material.expected_counts[
                            "legacy_article_observation"
                        ],
                        material.expected_counts[
                            "article_with_annotation"
                        ],
                        material.expected_counts["date_annotation"],
                        material.expected_counts[
                            "normalized_annotation"
                        ],
                        material.expected_counts[
                            "unresolved_annotation"
                        ],
                        json_text(material.expected_counts),
                        json_text(material.table_fingerprints),
                    ),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.legacy_article_observation (
                      run_id, article_id, article_num, source_identity,
                      source_identity_sha256, full_text, full_text_sha256,
                      annotation_count, caller_record_sha256,
                      source_row_sha256
                    ) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                    """,
                    [
                        (
                            material.run_id,
                            row["article_id"],
                            row["article_num"],
                            json_text(row["source_identity"]),
                            row["source_identity_sha256"],
                            row["full_text"],
                            row["full_text_sha256"],
                            row["annotation_count"],
                            row["caller_record_sha256"],
                            row["source_row_sha256"],
                        )
                        for row in material.articles
                    ],
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {SCHEMA}.date_annotation (
                      run_id, annotation_id, article_id, marker_ordinal,
                      char_start, char_end, raw_expression,
                      raw_expression_sha256, roc_year, roc_month, roc_day,
                      normalized_iso_candidate, normalization_status,
                      resolution_status, unresolved_reason,
                      source_row_sha256
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    [
                        (
                            material.run_id,
                            row["annotation_id"],
                            row["article_id"],
                            row["marker_ordinal"],
                            row["char_start"],
                            row["char_end"],
                            row["raw_expression"],
                            row["raw_expression_sha256"],
                            row["roc_year"],
                            row["roc_month"],
                            row["roc_day"],
                            row["normalized_iso_candidate"],
                            row["normalization_status"],
                            row["resolution_status"],
                            row["unresolved_reason"],
                            row["source_row_sha256"],
                        )
                        for row in material.annotations
                    ],
                )
    return replayed


def verify_loaded_annotation_stage(
    run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: AnnotationStageMaterial | None = None,
) -> dict[str, Any]:
    """Fresh-connect and recompute counts plus order-independent row hashes."""

    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, table_fingerprints,
                       input_fingerprint, output_fingerprint,
                       sealed_fingerprint
                FROM {SCHEMA}.annotation_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise AnnotationStageError(
                    "fresh verification found no sealed annotation run"
                )
            fingerprints: dict[str, str] = {}
            counts: dict[str, int] = {}
            for table in (
                "legacy_article_observation",
                "date_annotation",
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
                SELECT
                  count(*) FILTER (WHERE annotation_count > 0),
                  count(*),
                  COALESCE(sum(annotation_count), 0)
                FROM {SCHEMA}.v_rule_date_coverage
                WHERE run_id = %s
                """,
                (run_id,),
            )
            coverage = cursor.fetchone()
            counts["article_with_annotation"] = int(coverage[0])
            counts["coverage_projection"] = int(coverage[1])
            counts["article_annotation_total"] = int(coverage[2])
            cursor.execute(
                f"""
                SELECT
                  count(*) FILTER (
                    WHERE normalized_iso_candidate IS NOT NULL
                  ),
                  count(*) FILTER (
                    WHERE resolution_status = 'unresolved_event'
                  )
                FROM {SCHEMA}.date_annotation
                WHERE run_id = %s
                """,
                (run_id,),
            )
            annotation_counts = cursor.fetchone()
            counts["normalized_annotation"] = int(annotation_counts[0])
            counts["unresolved_annotation"] = int(annotation_counts[1])

    stored_expected = dict(run[1])
    stored_fingerprints = dict(run[2])
    if counts != stored_expected:
        raise AnnotationStageError(
            "fresh annotation-stage count verification failed"
        )
    if fingerprints != stored_fingerprints:
        raise AnnotationStageError(
            "fresh annotation-stage row fingerprint verification failed"
        )
    output_fingerprint = object_fingerprint(
        {
            "counts": counts,
            "table_fingerprints": fingerprints,
        }
    )
    if output_fingerprint != run[4]:
        raise AnnotationStageError(
            "fresh annotation-stage output fingerprint verification failed"
        )
    if expected is not None and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or run[3] != expected.input_fingerprint
        or run[4] != expected.output_fingerprint
        or run[5] != expected.sealed_fingerprint
    ):
        raise AnnotationStageError(
            "fresh annotation-stage verification differs from input"
        )
    return {
        "run_id": run_id,
        "state": "sealed",
        "counts": counts,
        "table_fingerprints": fingerprints,
        "input_fingerprint": run[3],
        "output_fingerprint": run[4],
        "sealed_fingerprint": run[5],
    }


def load_annotation_stage(
    records: Iterable[Mapping[str, Any]],
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Load once, replay idempotently, then verify through a fresh connection."""

    material = prepare_annotation_stage(records)
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    replayed = _apply_material(
        material,
        conninfo=dsn,
        connect=connector,
    )
    verification = verify_loaded_annotation_stage(
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
