"""Validate, seal, and load source-level proofread transcript evidence.

This module deliberately records source observations below the canonical legal
version layer.  A source segment and an 84→96 lineage candidate are useful
evidence, but neither is a stable clause identity, an effective-date decision,
nor a direct predecessor claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from nhi_rule_history.contracts import canonical_json_bytes, file_sha256
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


SCHEMA = "nhi_rule_history_transcript"
EDITION_SCHEMA = "nhi_rule_history_edition"
GLOBAL_LOCK_KEY = "nhi-rule-history-source-transcript-global"
LOADER_VERSION = "nhi-rule-history/source-transcript-loader/1.0.0"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_source_transcript_v19.sql"
)
_UUID_NAMESPACE = uuid.UUID("ce63c782-e778-475f-b564-9b33d71da87e")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAGE_MARKER_RE = re.compile(
    r"^<!-- source_page: (?P<page>[1-9][0-9]*) -->[ \t]*$",
    re.MULTILINE,
)
_REPEATED_HEADER_RE = re.compile(
    r"^> 衛生署公報｜[^\n]*(?:\n|$)",
    re.MULTILINE,
)
_LINEAGE_HEADER_RE = re.compile(
    r"^- \*\*`(?P<segment_id>84:[^`]+)`[^*]*\*\*$"
)
_DISPOSITION_RE = re.compile(r"^  - disposition: `(?P<value>[^`]+)`$")
_TARGET_RE = re.compile(
    r"^  - 96 designation／text span: (?P<value>.+)$"
)
_RATIONALE_RE = re.compile(r"^  - 判讀：(?P<value>.+)$")
_SEGMENT_KEYS = frozenset(
    {
        "source_segment_id",
        "source_page_start",
        "source_page_end",
        "section_path",
        "designation_raw",
        "heading_raw",
        "exact_text",
        "substructure",
        "literal_deleted_marker",
        "uncertainties",
    }
)
_DISPOSITIONS = frozenset(
    {
        "same_designation_text_continuity_candidate",
        "renumber_or_move_candidate",
        "absent_in_96_observation",
        "new_in_96_observation",
        "ambiguous",
    }
)
_REVIEW_STATUSES = frozenset(
    {
        "agent_proofread_pending_independent_review",
        "independently_reviewed",
    }
)


class SourceTranscriptError(PgLoadError):
    """Unsafe source transcript bundle or PostgreSQL load."""


@dataclass(frozen=True)
class SourceTranscriptMaterial:
    run_id: str
    manifest: Mapping[str, Any]
    proofread_artifacts: tuple[dict[str, Any], ...]
    pages: tuple[dict[str, Any], ...]
    segments: tuple[dict[str, Any], ...]
    lineage_artifacts: tuple[dict[str, Any], ...]
    lineage_candidates: tuple[dict[str, Any], ...]
    expected_counts: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    input_fingerprint: str
    output_fingerprint: str
    migration_sha256: str
    code_sha256: str
    sealed_fingerprint: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SourceTranscriptError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceTranscriptError(f"{label} must be a non-empty string")
    return value


def _require_sha256(value: Any, label: str) -> str:
    result = _require_string(value, label)
    if not _SHA256_RE.fullmatch(result):
        raise SourceTranscriptError(f"{label} must be a lowercase SHA-256")
    return result


def _relative_bundle_file(
    bundle_dir: Path,
    section: Mapping[str, Any],
    label: str,
) -> tuple[Path, str]:
    relative = _require_string(section.get("path"), f"{label}.path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SourceTranscriptError(f"{label}.path must remain inside bundle")
    path = bundle_dir / candidate
    if not path.is_file():
        raise SourceTranscriptError(f"{label} file is missing")
    expected_size = section.get("byte_size")
    if not isinstance(expected_size, int) or expected_size < 1:
        raise SourceTranscriptError(f"{label}.byte_size is invalid")
    if path.stat().st_size != expected_size:
        raise SourceTranscriptError(f"{label} byte size differs from manifest")
    expected_hash = _require_sha256(section.get("sha256"), f"{label}.sha256")
    if file_sha256(path) != expected_hash:
        raise SourceTranscriptError(f"{label} SHA-256 differs from manifest")
    return path, relative


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceTranscriptError(f"{label} is not valid UTF-8") from exc


def _parse_pages(
    transcript: str,
    expected_page_count: int,
) -> dict[int, str]:
    matches = list(_PAGE_MARKER_RE.finditer(transcript))
    if len(matches) != expected_page_count:
        raise SourceTranscriptError(
            "proofread page-marker count differs from manifest"
        )
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page_number = int(match.group("page"))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(
            transcript
        )
        text = transcript[start:end].strip("\n")
        if page_number in pages or not text.strip():
            raise SourceTranscriptError(
                "proofread has a duplicate or empty source page"
            )
        pages[page_number] = text
    if sorted(pages) != list(range(1, expected_page_count + 1)):
        raise SourceTranscriptError(
            "proofread source pages must be contiguous from page 1"
        )
    return pages


def _comparison_text(value: str) -> str:
    without_headers = _REPEATED_HEADER_RE.sub("", value)
    normalized = unicodedata.normalize("NFKC", without_headers)
    return "".join(
        character
        for character in normalized
        if not character.isspace() and character not in "#>*_`"
    )


def _read_segments(
    path: Path,
    *,
    expected_count: int,
    pages: Mapping[int, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceTranscriptError(
                    f"segments JSONL line {line_number} is invalid"
                ) from exc
            if not isinstance(row, dict) or frozenset(row) != _SEGMENT_KEYS:
                raise SourceTranscriptError(
                    f"segments JSONL line {line_number} has wrong keys"
                )
            segment_id = _require_string(
                row["source_segment_id"],
                f"segment {line_number} id",
            )
            if not segment_id.startswith("84:") or segment_id in seen:
                raise SourceTranscriptError(
                    "source segment IDs must be unique 84: locators"
                )
            seen.add(segment_id)
            start = row["source_page_start"]
            end = row["source_page_end"]
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start not in pages
                or end not in pages
                or end < start
            ):
                raise SourceTranscriptError(
                    f"segment {segment_id} has an invalid page span"
                )
            section_path = row["section_path"]
            if (
                not isinstance(section_path, list)
                or not section_path
                or not all(
                    isinstance(item, str) and item.strip()
                    for item in section_path
                )
            ):
                raise SourceTranscriptError(
                    f"segment {segment_id} has an invalid section path"
                )
            for key in ("designation_raw", "heading_raw", "exact_text"):
                _require_string(row[key], f"segment {segment_id}.{key}")
            if not isinstance(row["substructure"], list):
                raise SourceTranscriptError(
                    f"segment {segment_id} substructure must be an array"
                )
            if not isinstance(row["literal_deleted_marker"], bool):
                raise SourceTranscriptError(
                    f"segment {segment_id} deletion marker must be boolean"
                )
            uncertainties = row["uncertainties"]
            if not isinstance(uncertainties, list):
                raise SourceTranscriptError(
                    f"segment {segment_id} uncertainties must be an array"
                )
            page_span = "\n".join(
                pages[page] for page in range(start, end + 1)
            )
            if _comparison_text(row["exact_text"]) not in _comparison_text(
                page_span
            ):
                raise SourceTranscriptError(
                    f"segment {segment_id} exact text is absent from page span"
                )
            rows.append(row)
    if len(rows) != expected_count:
        raise SourceTranscriptError(
            "segment count differs from manifest"
        )
    return rows


def _parse_lineage_candidates(
    analysis: str,
    *,
    expected_count: int,
    source_segment_ids: set[str],
) -> list[dict[str, str]]:
    lines = analysis.splitlines()
    candidates: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        header = _LINEAGE_HEADER_RE.fullmatch(line)
        if header is None:
            continue
        if index + 3 >= len(lines):
            raise SourceTranscriptError("lineage candidate block is truncated")
        disposition = _DISPOSITION_RE.fullmatch(lines[index + 1])
        target = _TARGET_RE.fullmatch(lines[index + 2])
        rationale = _RATIONALE_RE.fullmatch(lines[index + 3])
        if disposition is None or target is None or rationale is None:
            raise SourceTranscriptError(
                "lineage candidate block has unexpected structure"
            )
        value = disposition.group("value")
        if value not in _DISPOSITIONS:
            raise SourceTranscriptError(
                f"unsupported lineage disposition: {value}"
            )
        candidates.append(
            {
                "source_segment_id": header.group("segment_id"),
                "disposition": value,
                "target_evidence_text": target.group("value"),
                "rationale_text": rationale.group("value"),
            }
        )
    ids = [row["source_segment_id"] for row in candidates]
    if (
        len(candidates) != expected_count
        or len(set(ids)) != expected_count
        or set(ids) != source_segment_ids
    ):
        raise SourceTranscriptError(
            "lineage candidates do not exactly cover source segments"
        )
    return candidates


def prepare_source_transcript(bundle_dir: Path) -> SourceTranscriptMaterial:
    """Validate one bundle and materialize its immutable PG rows."""

    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceTranscriptError("manifest.json is unreadable") from exc
    manifest = _require_mapping(manifest, "manifest")
    if manifest.get("schema") != "nhi-rule-history/source-transcript-bundle/v1":
        raise SourceTranscriptError("unsupported source transcript schema")

    source = _require_mapping(manifest.get("source"), "source")
    producer = _require_mapping(manifest.get("producer"), "producer")
    proofread = _require_mapping(manifest.get("proofread"), "proofread")
    segments_section = _require_mapping(manifest.get("segments"), "segments")
    lineage = _require_mapping(
        manifest.get("lineage_analysis"),
        "lineage_analysis",
    )
    claims = _require_mapping(manifest.get("claims"), "claims")

    source_fint_run_id = str(
        uuid.UUID(_require_string(source.get("fint_run_id"), "source.fint_run_id"))
    )
    attachment_snapshot_id = _require_sha256(
        source.get("attachment_snapshot_id"),
        "source.attachment_snapshot_id",
    )
    attachment_sha256 = _require_sha256(
        source.get("attachment_sha256"),
        "source.attachment_sha256",
    )
    attachment_byte_size = source.get("attachment_byte_size")
    if (
        not isinstance(attachment_byte_size, int)
        or isinstance(attachment_byte_size, bool)
        or attachment_byte_size < 1
    ):
        raise SourceTranscriptError("source.attachment_byte_size is invalid")
    source_url = _require_string(source.get("source_url"), "source.source_url")
    if not source_url.startswith("https://mohwlaw.mohw.gov.tw/"):
        raise SourceTranscriptError("source URL is outside the official host")

    if producer.get("provider") != "openai":
        raise SourceTranscriptError("producer.provider must be openai")
    if producer.get("model_lane") != "gpt-pro":
        raise SourceTranscriptError("producer.model_lane must be gpt-pro")
    review_status = _require_string(
        proofread.get("review_status"),
        "proofread.review_status",
    )
    if review_status not in _REVIEW_STATUSES:
        raise SourceTranscriptError("unsupported proofread review status")
    if (
        segments_section.get("identity_status")
        != "unadjudicated_source_segment"
        or segments_section.get("legal_version_status") != "not_claimed"
    ):
        raise SourceTranscriptError(
            "segments must remain below canonical legal identity"
        )
    expected_claims = {
        "source_observation_only": True,
        "legal_identity_adjudicated": False,
        "direct_predecessor_claimed": False,
        "legal_effective_date_assigned_per_segment": False,
        "complete_history_claimed": False,
    }
    if dict(claims) != expected_claims:
        raise SourceTranscriptError("manifest legal claims are unsafe")

    proofread_path, proofread_relative = _relative_bundle_file(
        bundle_dir,
        proofread,
        "proofread",
    )
    segments_path, segments_relative = _relative_bundle_file(
        bundle_dir,
        segments_section,
        "segments",
    )
    lineage_path, lineage_relative = _relative_bundle_file(
        bundle_dir,
        lineage,
        "lineage_analysis",
    )
    proofread_text = _read_utf8(proofread_path, "proofread transcript")
    lineage_text = _read_utf8(lineage_path, "lineage analysis")

    page_count = proofread.get("page_count")
    segment_count = segments_section.get("segment_count")
    candidate_count = lineage.get("candidate_count")
    for value, label in (
        (page_count, "proofread.page_count"),
        (segment_count, "segments.segment_count"),
        (candidate_count, "lineage_analysis.candidate_count"),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SourceTranscriptError(f"{label} is invalid")
    if segment_count != candidate_count:
        raise SourceTranscriptError(
            "segment and lineage candidate counts must match"
        )
    pages = _parse_pages(proofread_text, page_count)
    raw_segments = _read_segments(
        segments_path,
        expected_count=segment_count,
        pages=pages,
    )
    raw_candidates = _parse_lineage_candidates(
        lineage_text,
        expected_count=candidate_count,
        source_segment_ids={
            row["source_segment_id"] for row in raw_segments
        },
    )
    disposition_counts = lineage.get("disposition_counts")
    observed_dispositions = Counter(
        row["disposition"] for row in raw_candidates
    )
    if not isinstance(disposition_counts, dict) or {
        key: int(observed_dispositions[key])
        for key in sorted(_DISPOSITIONS)
    } != disposition_counts:
        raise SourceTranscriptError(
            "lineage disposition counts differ from manifest"
        )
    literal_deleted_count = segments_section.get(
        "literal_deleted_marker_count"
    )
    if literal_deleted_count != sum(
        row["literal_deleted_marker"] for row in raw_segments
    ):
        raise SourceTranscriptError(
            "literal deletion-marker count differs from manifest"
        )
    unresolved_count = proofread.get("unresolved_visual_reading_count")
    if (
        not isinstance(unresolved_count, int)
        or isinstance(unresolved_count, bool)
        or unresolved_count < 0
        or unresolved_count
        != sum(bool(row["uncertainties"]) for row in raw_segments)
    ):
        raise SourceTranscriptError(
            "unresolved visual-reading count differs from segments"
        )
    if (
        lineage.get("recoding_hypothesis")
        != "supported_at_source_observation_level_not_adjudicated"
    ):
        raise SourceTranscriptError("recoding hypothesis overclaims evidence")

    input_fingerprint = object_fingerprint(
        {
            "manifest": manifest,
            "manifest_sha256": file_sha256(manifest_path),
            "proofread_sha256": proofread["sha256"],
            "segments_sha256": segments_section["sha256"],
            "lineage_sha256": lineage["sha256"],
        }
    )
    run_id = str(
        uuid.uuid5(
            _UUID_NAMESPACE,
            canonical_json_bytes(
                ["source-transcript-run", input_fingerprint]
            ).decode("utf-8"),
        )
    )
    logical_bundle = (
        f"external_bundle:{source.get('document_number')}/"
        "gpt-pro-20260729-v1"
    )

    proofread_rows = [
        {
            "run_id": run_id,
            "transcript_markdown": proofread_text,
            "transcript_sha256": proofread["sha256"],
            "page_count": page_count,
            "unresolved_visual_reading_count": unresolved_count,
            "source_locator": {
                "bundle": logical_bundle,
                "path": proofread_relative,
                "source_attachment_snapshot_id": attachment_snapshot_id,
            },
        }
    ]
    page_rows = [
        {
            "run_id": run_id,
            "page_number": page_number,
            "transcript_text": text,
            "transcript_text_sha256": _sha256_text(text),
            "source_locator": {
                "bundle": logical_bundle,
                "path": proofread_relative,
                "source_page": page_number,
                "source_attachment_snapshot_id": attachment_snapshot_id,
            },
        }
        for page_number, text in sorted(pages.items())
    ]
    segment_rows = [
        {
            "run_id": run_id,
            **row,
            "exact_text_sha256": _sha256_text(row["exact_text"]),
            "proofread_review_status": review_status,
            "identity_status": "unadjudicated_source_segment",
            "legal_version_status": "not_claimed",
            "source_locator": {
                "bundle": logical_bundle,
                "path": segments_relative,
                "source_page_start": row["source_page_start"],
                "source_page_end": row["source_page_end"],
                "source_attachment_snapshot_id": attachment_snapshot_id,
            },
        }
        for row in raw_segments
    ]
    target_label = _require_string(
        lineage.get("target_source_edition_label"),
        "lineage_analysis.target_source_edition_label",
    )
    target_artifact_sha256 = _require_sha256(
        lineage.get("target_artifact_sha256"),
        "lineage_analysis.target_artifact_sha256",
    )
    lineage_rows = [
        {
            "run_id": run_id,
            "analysis_markdown": lineage_text,
            "analysis_sha256": lineage["sha256"],
            "target_source_edition_label": target_label,
            "target_artifact_sha256": target_artifact_sha256,
            "recoding_hypothesis_status": (
                "supported_at_source_observation_level_not_adjudicated"
            ),
            "source_locator": {
                "bundle": logical_bundle,
                "path": lineage_relative,
            },
        }
    ]
    candidate_rows: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        candidate_id = object_fingerprint(
            {
                "source_segment_id": candidate["source_segment_id"],
                "disposition": candidate["disposition"],
                "target_source_edition_label": target_label,
                "target_artifact_sha256": target_artifact_sha256,
                "target_evidence_text": candidate["target_evidence_text"],
                "rationale_text": candidate["rationale_text"],
            }
        )
        candidate_rows.append(
            {
                "run_id": run_id,
                "candidate_id": candidate_id,
                **candidate,
                "target_source_edition_label": target_label,
                "target_artifact_sha256": target_artifact_sha256,
                "identity_status": "candidate_unadjudicated",
                "direct_predecessor_status": "not_claimed",
                "legal_transition_status": "not_claimed",
                "source_locator": {
                    "bundle": logical_bundle,
                    "path": lineage_relative,
                    "source_segment_id": candidate["source_segment_id"],
                },
            }
        )

    table_rows = {
        "proofread_artifact": proofread_rows,
        "source_page": page_rows,
        "source_segment": segment_rows,
        "lineage_analysis_artifact": lineage_rows,
        "lineage_candidate": candidate_rows,
    }
    for rows in table_rows.values():
        for row in rows:
            row["source_row_sha256"] = row_sha256(row)
    expected_counts = {
        table: len(rows) for table, rows in table_rows.items()
    }
    table_fingerprints = {
        table: row_set_fingerprint(
            row["source_row_sha256"] for row in rows
        )
        for table, rows in table_rows.items()
    }
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "table_fingerprints": table_fingerprints,
        }
    )
    migration_sha256 = migration_fingerprint(MIGRATION)
    code_sha256 = code_fingerprint(Path(__file__).resolve())
    sealed_fingerprint = object_fingerprint(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
            "review_status": review_status,
            "source_observation_only": True,
        }
    )
    return SourceTranscriptMaterial(
        run_id=run_id,
        manifest=manifest,
        proofread_artifacts=tuple(proofread_rows),
        pages=tuple(page_rows),
        segments=tuple(segment_rows),
        lineage_artifacts=tuple(lineage_rows),
        lineage_candidates=tuple(candidate_rows),
        expected_counts=expected_counts,
        table_fingerprints=table_fingerprints,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        migration_sha256=migration_sha256,
        code_sha256=code_sha256,
        sealed_fingerprint=sealed_fingerprint,
    )


def _source_context(
    connection: Any,
    material: SourceTranscriptMaterial,
) -> None:
    source = material.manifest["source"]
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT content_sha256, byte_size
            FROM {EDITION_SCHEMA}.fint_attachment_snapshot
            WHERE run_id = %s AND attachment_snapshot_id = %s
            """,
            (
                source["fint_run_id"],
                source["attachment_snapshot_id"],
            ),
        )
        row = cursor.fetchone()
    if row is None:
        raise SourceTranscriptError(
            "official FINT attachment snapshot is absent"
        )
    if (
        str(row[0]) != source["attachment_sha256"]
        or int(row[1]) != source["attachment_byte_size"]
    ):
        raise SourceTranscriptError(
            "official FINT attachment identity differs from manifest"
        )


def _insert_material(
    connection: Any,
    material: SourceTranscriptMaterial,
) -> bool:
    source = material.manifest["source"]
    producer = material.manifest["producer"]
    proofread = material.manifest["proofread"]
    lineage = material.manifest["lineage_analysis"]
    claims = material.manifest["claims"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (GLOBAL_LOCK_KEY,),
        )
        _source_context(connection, material)
        cursor.execute(
            f"""
            SELECT input_fingerprint, sealed_fingerprint, state
            FROM {SCHEMA}.transcript_run
            WHERE run_id = %s
            """,
            (material.run_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if (
                existing[0] != material.input_fingerprint
                or existing[1] != material.sealed_fingerprint
                or existing[2] != "sealed"
            ):
                raise SourceTranscriptError(
                    "existing transcript run differs from prepared material"
                )
            return True

        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.transcript_run (
              run_id, source_fint_run_id, source_attachment_snapshot_id,
              source_attachment_sha256, source_attachment_byte_size,
              source_document_number, source_edition_label, source_url,
              target_source_edition_label, target_artifact_sha256,
              producer_provider, producer_model_lane, producer_role,
              prompt_sha256, proofread_sha256, segment_jsonl_sha256,
              lineage_analysis_sha256, review_status,
              source_observation_only, legal_identity_adjudicated,
              direct_predecessor_claimed,
              legal_effective_date_assigned_per_segment,
              complete_history_claimed, state, loader_version,
              input_fingerprint, expected_counts, migration_sha256,
              code_sha256, started_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              %s,%s,%s,%s,%s,'loading',%s,%s,%s::jsonb,%s,%s,%s::timestamptz
            )
            """,
            (
                material.run_id,
                source["fint_run_id"],
                source["attachment_snapshot_id"],
                source["attachment_sha256"],
                source["attachment_byte_size"],
                source["document_number"],
                source["source_edition_label"],
                source["source_url"],
                lineage["target_source_edition_label"],
                lineage["target_artifact_sha256"],
                producer["provider"],
                producer["model_lane"],
                producer["role"],
                producer["prompt_sha256"],
                proofread["sha256"],
                material.manifest["segments"]["sha256"],
                lineage["sha256"],
                proofread["review_status"],
                claims["source_observation_only"],
                claims["legal_identity_adjudicated"],
                claims["direct_predecessor_claimed"],
                claims["legal_effective_date_assigned_per_segment"],
                claims["complete_history_claimed"],
                LOADER_VERSION,
                material.input_fingerprint,
                json_text(material.expected_counts),
                material.migration_sha256,
                material.code_sha256,
                material.manifest["captured_at"],
            ),
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.proofread_artifact (
              run_id, transcript_markdown, transcript_sha256, page_count,
              unresolved_visual_reading_count, source_locator,
              source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            [
                (
                    row["run_id"],
                    row["transcript_markdown"],
                    row["transcript_sha256"],
                    row["page_count"],
                    row["unresolved_visual_reading_count"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.proofread_artifacts
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.source_page (
              run_id, page_number, transcript_text, transcript_text_sha256,
              source_locator, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s::jsonb,%s)
            """,
            [
                (
                    row["run_id"],
                    row["page_number"],
                    row["transcript_text"],
                    row["transcript_text_sha256"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.pages
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.source_segment (
              run_id, source_segment_id, source_page_start, source_page_end,
              section_path, designation_raw, heading_raw, exact_text,
              exact_text_sha256, substructure, literal_deleted_marker,
              uncertainties, proofread_review_status, identity_status,
              legal_version_status, source_locator, source_row_sha256
            ) VALUES (
              %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,
              %s,%s,%s,%s::jsonb,%s
            )
            """,
            [
                (
                    row["run_id"],
                    row["source_segment_id"],
                    row["source_page_start"],
                    row["source_page_end"],
                    json_text(row["section_path"]),
                    row["designation_raw"],
                    row["heading_raw"],
                    row["exact_text"],
                    row["exact_text_sha256"],
                    json_text(row["substructure"]),
                    row["literal_deleted_marker"],
                    json_text(row["uncertainties"]),
                    row["proofread_review_status"],
                    row["identity_status"],
                    row["legal_version_status"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.segments
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.lineage_analysis_artifact (
              run_id, analysis_markdown, analysis_sha256,
              target_source_edition_label, target_artifact_sha256,
              recoding_hypothesis_status, source_locator, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            [
                (
                    row["run_id"],
                    row["analysis_markdown"],
                    row["analysis_sha256"],
                    row["target_source_edition_label"],
                    row["target_artifact_sha256"],
                    row["recoding_hypothesis_status"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.lineage_artifacts
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.lineage_candidate (
              run_id, candidate_id, source_segment_id, disposition,
              target_source_edition_label, target_artifact_sha256,
              target_evidence_text, rationale_text, identity_status,
              direct_predecessor_status, legal_transition_status,
              source_locator, source_row_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
            )
            """,
            [
                (
                    row["run_id"],
                    row["candidate_id"],
                    row["source_segment_id"],
                    row["disposition"],
                    row["target_source_edition_label"],
                    row["target_artifact_sha256"],
                    row["target_evidence_text"],
                    row["rationale_text"],
                    row["identity_status"],
                    row["direct_predecessor_status"],
                    row["legal_transition_status"],
                    json_text(row["source_locator"]),
                    row["source_row_sha256"],
                )
                for row in material.lineage_candidates
            ],
        )
        cursor.execute(
            f"""
            UPDATE {SCHEMA}.transcript_run
            SET state = 'sealed',
                verified_counts = %s::jsonb,
                table_fingerprints = %s::jsonb,
                output_fingerprint = %s,
                sealed_fingerprint = %s,
                sealed_at = now()
            WHERE run_id = %s AND state = 'loading'
            """,
            (
                json_text(material.expected_counts),
                json_text(material.table_fingerprints),
                material.output_fingerprint,
                material.sealed_fingerprint,
                material.run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise SourceTranscriptError("transcript seal transition failed")
    return False


def verify_source_transcript(
    run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: SourceTranscriptMaterial | None = None,
) -> dict[str, Any]:
    """Recompute counts and fingerprints from a fresh PG connection."""

    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, verified_counts,
                       table_fingerprints, output_fingerprint,
                       sealed_fingerprint, review_status,
                       source_observation_only, legal_identity_adjudicated,
                       direct_predecessor_claimed,
                       legal_effective_date_assigned_per_segment,
                       complete_history_claimed
                FROM {SCHEMA}.transcript_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise SourceTranscriptError(
                    "fresh verification found no sealed transcript"
                )
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in (
                "proofread_artifact",
                "source_page",
                "source_segment",
                "lineage_analysis_artifact",
                "lineage_candidate",
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
                hashes = [str(row[0]) for row in cursor.fetchall()]
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT disposition, count(*)::integer
                FROM {SCHEMA}.lineage_candidate
                WHERE run_id = %s
                GROUP BY disposition
                ORDER BY disposition
                """,
                (run_id,),
            )
            dispositions = {
                str(row[0]): int(row[1]) for row in cursor.fetchall()
            }
    output_fingerprint = object_fingerprint(
        {"counts": counts, "table_fingerprints": fingerprints}
    )
    if counts != run[1] or counts != run[2]:
        raise SourceTranscriptError(
            "fresh transcript counts differ from sealed receipt"
        )
    if fingerprints != run[3] or output_fingerprint != run[4]:
        raise SourceTranscriptError(
            "fresh transcript fingerprint differs from sealed receipt"
        )
    if (
        run[6] not in _REVIEW_STATUSES
        or run[7] is not True
        or any(run[index] is not False for index in range(8, 12))
    ):
        raise SourceTranscriptError("sealed transcript legal claims drifted")
    if expected is not None and (
        counts != expected.expected_counts
        or fingerprints != expected.table_fingerprints
        or output_fingerprint != expected.output_fingerprint
        or run[5] != expected.sealed_fingerprint
    ):
        raise SourceTranscriptError(
            "fresh transcript differs from prepared material"
        )
    return {
        "run_id": run_id,
        "state": "sealed",
        "review_status": run[6],
        "source_observation_only": True,
        "counts": counts,
        "disposition_counts": dispositions,
        "table_fingerprints": fingerprints,
        "output_fingerprint": output_fingerprint,
        "sealed_fingerprint": run[5],
    }


def load_source_transcript(
    bundle_dir: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Prepare, load, seal, and freshly verify one transcript bundle."""

    material = prepare_source_transcript(bundle_dir)
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        already_loaded = _insert_material(connection, material)
    result = verify_source_transcript(
        material.run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result["already_loaded"] = already_loaded
    return result
