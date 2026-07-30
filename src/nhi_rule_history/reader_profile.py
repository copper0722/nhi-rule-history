"""Source-bound reader profiles for unusually complex clause versions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import PgLoadError, row_sha256


SCHEMA = "nhi_rule_history_announced"
PROFILE_CONTRACT = "nhi-reimbursement-rules/reader-profile/v1"
PROFILE_LOADER_VERSION = "nhi-rule-history/reader-profile-loader/1.0.0"
PROFILE_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "pg"
    / "migrations"
    / "2026-07-30_nhi_rule_history_clause_reader_profile_v26.sql"
)
_UUID_NAMESPACE = uuid.UUID("adff6ab0-0bc5-4d59-8520-1951aa03eaf0")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLAUSE_RE = re.compile(r"^[1-9][0-9]*(?:[.][0-9]+)+$")


class ReaderProfileError(PgLoadError):
    """A reader profile violated its source or publication contract."""


def _stable_uuid(label: str, value: object) -> str:
    material = canonical_json_bytes([label, value]).decode("utf-8")
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [
            text
            for item in value
            for text in _walk_strings(item)
        ]
    if isinstance(value, dict):
        return [
            text
            for item in value.values()
            for text in _walk_strings(item)
        ]
    return []


def validate_reader_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract",
        "clause_code",
        "effective_on",
        "presentation_mode",
        "template_key",
        "authoring_method",
        "review_status",
        "disclosure_text",
        "source_binding",
        "content",
    }
    if set(payload) != required:
        raise ReaderProfileError(
            "reader profile fields differ from the v1 contract"
        )
    if payload["contract"] != PROFILE_CONTRACT:
        raise ReaderProfileError("reader profile contract is unsupported")
    if not _CLAUSE_RE.fullmatch(str(payload["clause_code"])):
        raise ReaderProfileError("reader profile clause code is invalid")
    if payload["presentation_mode"] != "agentic_specialized":
        raise ReaderProfileError("reader profile mode is unsupported")
    if payload["template_key"] != "dyslipidemia_pathway_v1":
        raise ReaderProfileError("reader profile template is unsupported")
    if payload["authoring_method"] != "agentic_owner_authorized":
        raise ReaderProfileError("reader profile authoring lane is invalid")
    if payload["review_status"] != "owner_authorized":
        raise ReaderProfileError("reader profile is not owner-authorized")
    try:
        datetime.strptime(str(payload["effective_on"]), "%Y-%m-%d")
    except ValueError as exc:
        raise ReaderProfileError(
            "reader profile effective date is invalid"
        ) from exc

    binding = payload["source_binding"]
    if not isinstance(binding, dict) or set(binding) != {
        "source_release_run_id",
        "source_version_id",
        "source_composed_text_sha256",
        "source_diff_run_id",
        "source_diff_output_fingerprint",
    }:
        raise ReaderProfileError("reader profile source binding is invalid")
    for key in (
        "source_release_run_id",
        "source_version_id",
        "source_diff_run_id",
    ):
        try:
            uuid.UUID(str(binding[key]))
        except ValueError as exc:
            raise ReaderProfileError(
                f"reader profile {key} is invalid"
            ) from exc
    for key in (
        "source_composed_text_sha256",
        "source_diff_output_fingerprint",
    ):
        if not _SHA256_RE.fullmatch(str(binding[key])):
            raise ReaderProfileError(
                f"reader profile {key} is invalid"
            )

    content = payload["content"]
    if not isinstance(content, dict) or set(content) != {
        "eyebrow",
        "title",
        "lead",
        "pathway_steps",
        "lane_labels",
        "change_digest",
        "raw_appendix_note",
    }:
        raise ReaderProfileError("reader profile content shape is invalid")
    if len(content["pathway_steps"]) != 4:
        raise ReaderProfileError("reader profile pathway must have four steps")
    if len(content["change_digest"]) < 4:
        raise ReaderProfileError("reader profile change digest is incomplete")
    if set(content["lane_labels"]) != {"table1", "table2", "unknown"}:
        raise ReaderProfileError("reader profile lane labels are incomplete")
    for text in _walk_strings(payload):
        lowered = text.lower()
        if "<script" in lowered or "javascript:" in lowered:
            raise ReaderProfileError("reader profile contains executable HTML")
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _apply_migration(connection: Any) -> None:
    connection.execute(PROFILE_MIGRATION.read_text(encoding="utf-8"))
    connection.commit()


def _source_receipt(connection: Any, payload: Mapping[str, Any]) -> None:
    binding = payload["source_binding"]
    row = connection.execute(
        f"""
        SELECT version.clause_code, version.effective_from,
               version.composed_text_sha256,
               diff.state AS diff_state,
               diff.output_fingerprint AS diff_output_fingerprint,
               release.state AS release_state,
               newer.source_version_id AS diff_newer_version_id,
               newer.exact_text_sha256 AS diff_newer_text_sha256
        FROM {SCHEMA}.composed_clause_version version
        JOIN {SCHEMA}.release_run release
          ON release.run_id=version.run_id
        JOIN {SCHEMA}.clause_document_diff_run diff
          ON diff.diff_run_id=%s
        JOIN {SCHEMA}.clause_document_expression_relation relation
          ON relation.normalization_run_id=diff.normalization_run_id
         AND relation.relation_id=diff.relation_id
        JOIN {SCHEMA}.clause_document_expression newer
          ON newer.normalization_run_id=relation.normalization_run_id
         AND newer.expression_id=relation.newer_expression_id
        WHERE version.run_id=%s
          AND version.version_id=%s
        """,
        (
            binding["source_diff_run_id"],
            binding["source_release_run_id"],
            binding["source_version_id"],
        ),
    ).fetchone()
    if not row:
        raise ReaderProfileError("reader profile source version is missing")
    (
        clause_code,
        effective_from,
        composed_sha,
        diff_state,
        diff_output,
        release_state,
        diff_newer_version_id,
        diff_newer_text_sha256,
    ) = row
    expected = (
        payload["clause_code"],
        payload["effective_on"],
        binding["source_composed_text_sha256"],
        "sealed",
        binding["source_diff_output_fingerprint"],
        "sealed",
        binding["source_version_id"],
        binding["source_composed_text_sha256"],
    )
    actual = (
        str(clause_code),
        str(effective_from),
        str(composed_sha),
        str(diff_state),
        str(diff_output),
        str(release_state),
        str(diff_newer_version_id),
        str(diff_newer_text_sha256),
    )
    if actual != expected:
        raise ReaderProfileError(
            "reader profile source binding is stale or mismatched"
        )


def load_reader_profile(
    profile_path: Path,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    path = Path(profile_path)
    raw = path.read_bytes()
    payload = validate_reader_profile(json.loads(raw))
    input_fingerprint = _sha256_bytes(raw)
    binding = payload["source_binding"]
    profile_run_id = _stable_uuid("reader-profile-run", input_fingerprint)
    profile_id = _stable_uuid(
        "reader-profile",
        [
            payload["clause_code"],
            binding["source_version_id"],
            input_fingerprint,
        ],
    )
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")

    with connector(dsn) as connection:
        _apply_migration(connection)
    with connector(dsn) as connection:
        _source_receipt(connection, payload)
        existing = connection.execute(
            f"""
            SELECT state, sealed_fingerprint
            FROM {SCHEMA}.clause_reader_profile_run
            WHERE profile_run_id=%s
            """,
            (profile_run_id,),
        ).fetchone()
        already_loaded = bool(existing)
        if existing and existing[0] != "sealed":
            raise ReaderProfileError(
                "existing reader profile run is not sealed"
            )
        if not existing:
            started_at = datetime.now(timezone.utc)
            content_payload = payload["content"]
            content_json = json.dumps(
                content_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_sha = connection.execute(
                """
                SELECT encode(
                  sha256(convert_to(%s::jsonb::text, 'UTF8')),
                  'hex'
                )
                """,
                (content_json,),
            ).fetchone()[0]
            profile_row = {
                "profile_run_id": profile_run_id,
                "profile_id": profile_id,
                "clause_code": payload["clause_code"],
                "source_release_run_id": binding["source_release_run_id"],
                "source_version_id": binding["source_version_id"],
                "source_composed_text_sha256": (
                    binding["source_composed_text_sha256"]
                ),
                "source_diff_run_id": binding["source_diff_run_id"],
                "source_diff_output_fingerprint": (
                    binding["source_diff_output_fingerprint"]
                ),
                "presentation_mode": payload["presentation_mode"],
                "template_key": payload["template_key"],
                "profile_contract": payload["contract"],
                "authoring_method": payload["authoring_method"],
                "review_status": payload["review_status"],
                "disclosure_text": payload["disclosure_text"],
                "content_payload": content_payload,
                "content_sha256": content_sha,
            }
            source_row_sha = row_sha256(
                profile_row, derived_key="source_row_sha256"
            )
            output_fingerprint = _sha256_text(source_row_sha)
            sealed_fingerprint = _sha256_text(
                "|".join(
                    [
                        profile_run_id,
                        input_fingerprint,
                        output_fingerprint,
                    ]
                )
            )
            connection.execute(
                f"""
                INSERT INTO {SCHEMA}.clause_reader_profile_run (
                  profile_run_id, state, schema_version, loader_version,
                  source_release_run_id, input_fingerprint,
                  expected_profile_count, started_at
                ) VALUES (%s,'loading',%s,%s,%s,%s,1,%s)
                """,
                (
                    profile_run_id,
                    PROFILE_CONTRACT,
                    PROFILE_LOADER_VERSION,
                    binding["source_release_run_id"],
                    input_fingerprint,
                    started_at,
                ),
            )
            connection.execute(
                f"""
                INSERT INTO {SCHEMA}.clause_reader_profile (
                  profile_run_id, profile_id, clause_code,
                  source_release_run_id, source_version_id,
                  source_composed_text_sha256, source_diff_run_id,
                  source_diff_output_fingerprint, presentation_mode,
                  template_key, profile_contract, authoring_method,
                  review_status, disclosure_text, content_payload,
                  content_sha256, source_row_sha256
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s
                )
                """,
                (
                    profile_run_id,
                    profile_id,
                    payload["clause_code"],
                    binding["source_release_run_id"],
                    binding["source_version_id"],
                    binding["source_composed_text_sha256"],
                    binding["source_diff_run_id"],
                    binding["source_diff_output_fingerprint"],
                    payload["presentation_mode"],
                    payload["template_key"],
                    payload["contract"],
                    payload["authoring_method"],
                    payload["review_status"],
                    payload["disclosure_text"],
                    content_json,
                    content_sha,
                    source_row_sha,
                ),
            )
            connection.execute(
                f"""
                UPDATE {SCHEMA}.clause_reader_profile_run
                SET state='sealed',
                    verified_profile_count=1,
                    output_fingerprint=%s,
                    sealed_fingerprint=%s,
                    sealed_at=%s
                WHERE profile_run_id=%s
                """,
                (
                    output_fingerprint,
                    sealed_fingerprint,
                    started_at,
                    profile_run_id,
                ),
            )
        if activate:
            latest = connection.execute(
                f"""
                SELECT action, profile_id
                FROM {SCHEMA}.clause_reader_profile_control_event
                WHERE profile_run_id=%s
                ORDER BY recorded_at DESC, control_event_id DESC
                LIMIT 1
                """,
                (profile_run_id,),
            ).fetchone()
            if not latest or latest[0] != "activate":
                recorded_at = datetime.now(timezone.utc)
                control_event_id = _stable_uuid(
                    "reader-profile-control",
                    [profile_id, "activate"],
                )
                control_row = {
                    "control_event_id": control_event_id,
                    "profile_run_id": profile_run_id,
                    "profile_id": profile_id,
                    "action": "activate",
                    "reason": (
                        "owner authorized a specialized 2.6.1 reading profile"
                    ),
                    "recorded_at": recorded_at.isoformat(),
                }
                connection.execute(
                    f"""
                    INSERT INTO
                      {SCHEMA}.clause_reader_profile_control_event (
                        control_event_id, profile_run_id, profile_id,
                        action, reason, recorded_at, source_row_sha256
                      ) VALUES (%s,%s,%s,'activate',%s,%s,%s)
                    ON CONFLICT (control_event_id) DO NOTHING
                    """,
                    (
                        control_event_id,
                        profile_run_id,
                        profile_id,
                        control_row["reason"],
                        recorded_at,
                        row_sha256(
                            control_row, derived_key="source_row_sha256"
                        ),
                    ),
                )
        connection.commit()
        public_row = connection.execute(
            f"""
            SELECT profile_id, clause_code, source_version_id,
                   presentation_mode, template_key, content_sha256,
                   activated_at
            FROM {SCHEMA}.v_public_clause_reader_profile
            WHERE profile_id=%s
            """,
            (profile_id,),
        ).fetchone()
        if activate and not public_row:
            raise ReaderProfileError(
                "reader profile activation did not become public"
            )
    return {
        "contract": PROFILE_CONTRACT,
        "profile_run_id": profile_run_id,
        "profile_id": profile_id,
        "clause_code": payload["clause_code"],
        "source_version_id": binding["source_version_id"],
        "input_fingerprint": input_fingerprint,
        "already_loaded": already_loaded,
        "active": activate,
    }
