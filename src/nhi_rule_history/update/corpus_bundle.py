"""Create a conventional source-corpus bundle from a verified update bundle.

The caller supplies the corpus root.  This module contains no private paths or
database credentials.  PostgreSQL identity reconciliation must occur before the
caller invokes it and a PG receipt must follow the atomic rename.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
)
from nhi_rule_history.update.bundle import verify_bundle
from nhi_rule_history.update.notice import (
    extract_notice_metadata,
    extract_notice_metadata_v12,
    normalize_reference_number,
)
from nhi_rule_history.update.odt import extract_odt_blocks


CORPUS_BUNDLE_SCHEMA = "nhi-rule-history/corpus-source-bundle/v1"
CORPUS_BUNDLE_SCHEMA_VERSION = "1.3"
_REFERENCE_RE = re.compile(r"^健保審字第([0-9]+)號$")
_ATTACHMENT_EXTENSION = {
    "application/pdf": ".pdf",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
_CORE_FILE_PLAN = {
    "source.html": "detail_page",
    "source-rss.xml": "rss_observation",
    "raw.md": "deterministic_extraction",
}
_LEGACY_FILE_PLAN = {
    **_CORE_FILE_PLAN,
    "source.odt": "comparison_odt",
    "source.pdf": "comparison_pdf",
}
_ATTACHMENT_NAME_RE = re.compile(
    r"^attachment-(?P<sequence>[0-9]{3,})"
    r"(?P<extension>[.](?:pdf|ods|odt|xlsx|docx|bin))$"
)


def _roc_date(raw: str) -> date:
    try:
        year, month, day = (int(value) for value in raw.split("-"))
        return date(year + 1911, month, day)
    except (TypeError, ValueError) as exc:
        raise ContractError("ROC source date is malformed") from exc


def source_uid_from_reference(reference_number_raw: str) -> str:
    normalized, _normalization = normalize_reference_number(
        reference_number_raw
    )
    if not _REFERENCE_RE.fullmatch(normalized):
        raise ContractError("official reference number cannot form a source uid")
    return f"gov_{normalized}"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _raw_markdown(
    *,
    source_uid: str,
    metadata: dict[str, Any],
    canonical_url: str,
    odt_sources: list[dict[str, Any]],
    schema_version: str = CORPUS_BUNDLE_SCHEMA_VERSION,
) -> bytes:
    if schema_version not in {"1.0", "1.1", "1.2", "1.3"}:
        raise ContractError("raw markdown schema version is unsupported")
    lines = [
        "---",
        f"source_uid: {json.dumps(source_uid, ensure_ascii=False)}",
        f"title: {json.dumps(metadata['subject_raw'], ensure_ascii=False)}",
        (
            "reference_number: "
            + json.dumps(
                (
                    metadata["reference_number_raw"]
                    if schema_version in {"1.0", "1.1"}
                    else metadata["reference_number_normalized"]
                ),
                ensure_ascii=False,
            )
        ),
    ]
    if schema_version in {"1.2", "1.3"}:
        lines.extend(
            [
                "reference_number_raw: "
                f"{json.dumps(metadata['reference_number_raw'], ensure_ascii=False)}",
                "reference_number_normalization: "
                f"{metadata['reference_number_normalization']}",
                "reference_number_normalization_rule: "
                f"{metadata['reference_number_normalization_rule']}",
            ]
        )
    lines.extend(
        [
        f"canonical_url: {canonical_url}",
        f"document_date_roc: {metadata['document_date_roc_raw']}",
        f"publication_date_roc: {metadata['publication_date_roc_raw']}",
        f"extraction: nhi-rule-history-corpus-bundle/{schema_version}.0",
        "---",
        "",
        f"# {metadata['subject_raw']}",
        "",
        ]
    )
    if schema_version in {"1.2", "1.3"}:
        lines.extend(
            [
                f"- 發文字號（來源原文）：{metadata['reference_number_raw']}",
                f"- 發文字號（正規化）：{metadata['reference_number_normalized']}",
            ]
        )
    else:
        lines.append(f"- 發文字號：{metadata['reference_number_raw']}")
    lines.extend(
        [
        f"- 發文日期：{metadata['document_date_roc_raw']}",
        f"- 發布日期：{metadata['publication_date_roc_raw']}",
        "",
        "## 公告事項",
        "",
        metadata["announcement_text_raw"],
        "",
        "## 藥品給付規定修訂對照表（ODT source blocks）",
        "",
        ]
    )
    for source in odt_sources:
        if schema_version != "1.0":
            lines.extend(
                [
                (
                    "### Declared attachment "
                    f"{source['declared_sequence']}: "
                    f"{source['declared_label']}"
                ),
                "",
                ]
            )
        for block in source["blocks"]:
            source_locator = {
                "block_id": block["block_id"],
                "artifact_sha256": block["artifact_sha256"],
                "locator": block["locator"],
                "raw_text_sha256": block["raw_text_sha256"],
            }
            if schema_version != "1.0":
                source_locator.update(
                    {
                        "attachment_file_name": source["file_name"],
                        "declared_label": source["declared_label"],
                        "declared_sequence": source[
                            "declared_sequence"
                        ],
                    }
                )
            lines.extend(
                [
                    (
                        "<!-- source-block "
                        + json.dumps(
                            source_locator,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + " -->"
                    ),
                    block["raw_text"],
                    "",
                ]
            )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _attachment_file_name(row: dict[str, Any]) -> str:
    sequence = row.get("declared_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ContractError("declared attachment sequence is invalid")
    extension = _ATTACHMENT_EXTENSION.get(str(row.get("media_type")), ".bin")
    return f"attachment-{sequence:03d}{extension}"


def _expected_replay_raw(
    *,
    source_bundle_path: Path,
    source_manifest: dict[str, Any],
    existing_manifest: dict[str, Any],
    source_uid: str,
    metadata: dict[str, Any],
) -> bytes:
    version = existing_manifest["schema_version"]
    source_attachment_rows = sorted(
        (
            row
            for row in source_manifest["resources"]
            if row["relation"] == "declared_attachment"
        ),
        key=lambda row: row.get("declared_sequence", -1),
    )
    if version == "1.0":
        legacy_odt_row = next(
            (
                row
                for row in existing_manifest["files"]
                if row.get("file_name") == "source.odt"
                and row.get("role") == "comparison_odt"
            ),
            None,
        )
        if legacy_odt_row is None:
            raise ContractError(
                "existing corpus v1.0 ODT inventory is invalid"
            )
        matches = [
            row
            for row in source_attachment_rows
            if row.get("media_type")
            == "application/vnd.oasis.opendocument.text"
            and row.get("artifact_sha256")
            == legacy_odt_row.get("sha256")
            and row.get("byte_size") == legacy_odt_row.get("byte_size")
        ]
        if len(matches) != 1:
            raise ContractError(
                "existing corpus v1.0 ODT source binding does not verify"
            )
        odt_rows = matches
        file_names = {
            int(matches[0]["declared_sequence"]): "source.odt"
        }
    else:
        if [
            row.get("declared_sequence") for row in source_attachment_rows
        ] != list(range(len(source_attachment_rows))):
            raise ContractError(
                "declared attachment sequence is not contiguous"
            )
        odt_rows = [
            row
            for row in source_attachment_rows
            if row.get("media_type")
            == "application/vnd.oasis.opendocument.text"
        ]
        file_names = {
            int(row["declared_sequence"]): _attachment_file_name(row)
            for row in source_attachment_rows
        }
    if not odt_rows:
        raise ContractError("corpus bundle requires at least one ODT")
    odt_sources = []
    for row in odt_rows:
        payload = (source_bundle_path / row["content_path"]).read_bytes()
        sequence = int(row["declared_sequence"])
        odt_sources.append(
            {
                "file_name": file_names[sequence],
                "declared_sequence": sequence,
                "declared_label": row["declared_label"],
                "blocks": extract_odt_blocks(
                    payload,
                    row["artifact_sha256"],
                ),
            }
        )
    return _raw_markdown(
        source_uid=source_uid,
        metadata=metadata,
        canonical_url=source_manifest["rss_item"]["link"],
        odt_sources=odt_sources,
        schema_version=version,
    )


def _verify_existing_corpus_target(
    target: Path,
    manifest: dict[str, Any],
    *,
    source_uid: str,
    origin_bundle_id: str,
    origin_fingerprint: str,
    source_manifest: dict[str, Any],
    metadata: dict[str, Any],
    publication_date: date,
    expected_raw: bytes,
) -> int:
    if (
        manifest.get("schema") != CORPUS_BUNDLE_SCHEMA
        or manifest.get("schema_version")
        not in {"1.0", "1.1", "1.2", "1.3"}
        or manifest.get("source_uid") != source_uid
        or manifest.get("origin_update_bundle_fingerprint")
        != origin_fingerprint
    ):
        raise ContractError("existing corpus identity conflicts with source")
    expected_top_level = {
        "source_type": "tw-gov",
        "department": "健保署",
        "publisher": "NHI",
        "type": "regulation",
        "source_lang": "zh-TW",
        "title_zh": metadata["subject_raw"],
        "ref_number": (
            metadata["reference_number_raw"]
            if manifest["schema_version"] in {"1.0", "1.1"}
            else metadata["reference_number_normalized"]
        ),
        "canonical_url": source_manifest["rss_item"]["link"],
        "source_url": source_manifest["rss_item"]["link"],
        "publish_date": publication_date.isoformat(),
        "document_date_roc_raw": metadata["document_date_roc_raw"],
        "publication_date_roc_raw": metadata[
            "publication_date_roc_raw"
        ],
        "update_date_roc_raw": metadata["update_date_roc_raw"],
        "source_binary_state": "captured_verified",
        "citation_eligible": True,
        "origin_update_bundle_id": origin_bundle_id,
        "origin_update_bundle_fingerprint": origin_fingerprint,
        "extraction_status": {
            "deterministic_blocks": "done",
            "proofread": "not_started",
            "legal_history_promotion": (
                "blocked_pending_anchor_replay"
            ),
        },
    }
    version = manifest["schema_version"]
    if version in {"1.2", "1.3"}:
        expected_top_level.update(
            {
                "ref_number_raw": metadata["reference_number_raw"],
                "ref_number_normalization": metadata[
                    "reference_number_normalization"
                ],
                "ref_number_normalization_rule": (
                    "nhi-reference-number-normalization/1.0.0"
                    if version == "1.2"
                    else "nhi-reference-number-normalization/1.1.0"
                ),
            }
        )
    if any(
        manifest.get(key) != expected
        for key, expected in expected_top_level.items()
    ):
        raise ContractError(
            "existing corpus manifest metadata does not match source"
        )
    file_rows = manifest.get("files")
    if not isinstance(file_rows, list) or not file_rows:
        raise ContractError("existing corpus manifest file inventory is invalid")
    names: set[str] = set()
    fixed_roles: set[str] = set()
    rows_by_name: dict[str, dict[str, Any]] = {}
    attachment_rows: list[dict[str, Any]] = []
    attachment_count = 0
    for row in file_rows:
        if not isinstance(row, dict):
            raise ContractError("existing corpus manifest file row is invalid")
        name = row.get("file_name")
        digest = row.get("sha256")
        byte_size = row.get("byte_size")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in names
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size < 1
        ):
            raise ContractError("existing corpus manifest file row is invalid")
        version = manifest["schema_version"]
        role = row.get("role")
        if version == "1.0":
            if (
                name not in _LEGACY_FILE_PLAN
                or role != _LEGACY_FILE_PLAN[name]
                or role in fixed_roles
            ):
                raise ContractError(
                    "existing corpus manifest file role is invalid"
                )
            fixed_roles.add(role)
        elif name in _CORE_FILE_PLAN:
            if role != _CORE_FILE_PLAN[name] or role in fixed_roles:
                raise ContractError(
                    "existing corpus manifest file role is invalid"
                )
            fixed_roles.add(role)
        else:
            match = _ATTACHMENT_NAME_RE.fullmatch(name)
            sequence = row.get("declared_sequence")
            label = row.get("declared_label")
            media_type = row.get("media_type")
            if (
                match is None
                or role != "declared_attachment"
                or not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 0
                or int(match.group("sequence")) != sequence
                or not isinstance(label, str)
                or not label.strip()
                or not isinstance(media_type, str)
                or not media_type.strip()
                or match.group("extension")
                != _ATTACHMENT_EXTENSION.get(media_type, ".bin")
                or row.get("origin_artifact_sha256") != digest
            ):
                raise ContractError(
                    "existing corpus attachment row is invalid"
                )
            attachment_rows.append(row)
        names.add(name)
        rows_by_name[name] = row
        path = target / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != byte_size
            or file_sha256(path) != digest
        ):
            raise ContractError("existing corpus file inventory does not verify")
        if row.get("role") == "declared_attachment":
            attachment_count += 1
    required = (
        {"source.html", "source.odt", "source-rss.xml", "raw.md"}
        if manifest["schema_version"] == "1.0"
        else set(_CORE_FILE_PLAN)
    )
    if not required.issubset(names):
        raise ContractError("existing corpus required file set is incomplete")
    disk_names = {
        child.name
        for child in target.iterdir()
        if child.name != "manifest.json"
    }
    if disk_names != names:
        raise ContractError(
            "existing corpus directory does not match its manifest"
        )
    if manifest["schema_version"] in {"1.1", "1.2", "1.3"}:
        declared = manifest.get("declared_attachment_count")
        if (
            not isinstance(declared, int)
            or isinstance(declared, bool)
            or declared != attachment_count
        ):
            raise ContractError(
                "existing corpus attachment inventory does not verify"
            )
        if [
            row["declared_sequence"] for row in attachment_rows
        ] != list(range(attachment_count)):
            raise ContractError(
                "existing corpus attachment sequence does not verify"
            )
        source_attachment_rows = sorted(
            (
                row
                for row in source_manifest["resources"]
                if row["relation"] == "declared_attachment"
            ),
            key=lambda row: row.get("declared_sequence", -1),
        )
        if len(source_attachment_rows) != attachment_count:
            raise ContractError(
                "existing corpus attachment source binding does not verify"
            )
        for corpus_row, source_row in zip(
            attachment_rows,
            source_attachment_rows,
            strict=True,
        ):
            if (
                corpus_row["declared_sequence"]
                != source_row.get("declared_sequence")
                or corpus_row["declared_label"]
                != source_row.get("declared_label")
                or corpus_row["media_type"] != source_row.get("media_type")
                or corpus_row["origin_artifact_sha256"]
                != source_row.get("artifact_sha256")
                or corpus_row["byte_size"] != source_row.get("byte_size")
            ):
                raise ContractError(
                    "existing corpus attachment source binding does not verify"
                )
    source_rows = {
        row["relation"]: row
        for row in source_manifest["resources"]
        if row["relation"] in {"detail_page", "rss_feed"}
    }
    for name, relation in (
        ("source.html", "detail_page"),
        ("source-rss.xml", "rss_feed"),
    ):
        source_row = source_rows.get(relation)
        corpus_row = rows_by_name.get(name)
        if (
            source_row is None
            or corpus_row is None
            or corpus_row["sha256"] != source_row.get("artifact_sha256")
            or corpus_row["byte_size"] != source_row.get("byte_size")
        ):
            raise ContractError(
                "existing corpus source binding does not verify"
            )
    if manifest["schema_version"] == "1.0":
        source_attachment_rows = [
            row
            for row in source_manifest["resources"]
            if row["relation"] == "declared_attachment"
        ]
        for name, media_type in (
            ("source.odt", "application/vnd.oasis.opendocument.text"),
            ("source.pdf", "application/pdf"),
        ):
            corpus_row = rows_by_name.get(name)
            if corpus_row is None:
                continue
            matches = [
                row
                for row in source_attachment_rows
                if row.get("media_type") == media_type
                and row.get("artifact_sha256") == corpus_row["sha256"]
                and row.get("byte_size") == corpus_row["byte_size"]
            ]
            if len(matches) != 1:
                raise ContractError(
                    "existing corpus legacy attachment source binding "
                    "does not verify"
                )
    raw_path = target / "raw.md"
    raw_sha = manifest.get("raw_md_sha256")
    raw_bytes = manifest.get("raw_md_bytes")
    if (
        not raw_path.is_file()
        or not isinstance(raw_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw_sha)
        or not isinstance(raw_bytes, int)
        or isinstance(raw_bytes, bool)
        or raw_bytes < 0
        or raw_path.stat().st_size != raw_bytes
        or file_sha256(raw_path) != raw_sha
        or raw_path.read_bytes() != expected_raw
    ):
        raise ContractError("existing corpus raw markdown does not verify")
    return len(file_rows) + 1


def prepare_corpus_bundle(
    source_bundle_path: Path,
    *,
    corpus_root: Path,
) -> dict[str, Any]:
    source_verification = verify_bundle(source_bundle_path)
    source_manifest = json.loads(
        (source_bundle_path / "manifest.json").read_text(encoding="utf-8")
    )
    detail = next(
        (
            row
            for row in source_manifest["resources"]
            if row["relation"] == "detail_page"
        ),
        None,
    )
    if detail is None:
        raise ContractError("source bundle has no detail page")
    detail_payload = (source_bundle_path / detail["content_path"]).read_bytes()
    current_metadata_error: ContractError | None = None
    try:
        metadata = extract_notice_metadata(
            detail_payload,
            detail["artifact_sha256"],
        )
    except ContractError as exc:
        current_metadata_error = exc
        try:
            metadata = extract_notice_metadata_v12(
                detail_payload,
                detail["artifact_sha256"],
            )
        except ContractError:
            raise exc
    source_uid = source_uid_from_reference(metadata["reference_number_raw"])
    publication_date = _roc_date(metadata["publication_date_roc_raw"])
    target = corpus_root / f"{publication_date.year:04d}" / source_uid
    if target.is_symlink():
        raise ContractError("existing corpus target cannot be a symlink")
    if target.exists():
        if not target.is_dir():
            raise ContractError("existing corpus target is not a directory")
        existing_path = target / "manifest.json"
        if existing_path.is_symlink() or not existing_path.is_file():
            raise ContractError("existing corpus bundle lacks manifest")
        try:
            existing = json.loads(
                existing_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                "existing corpus manifest is unreadable"
            ) from exc
        if not isinstance(existing, dict):
            raise ContractError("existing corpus manifest is invalid")
        if existing.get("schema_version") in {"1.0", "1.1", "1.2"}:
            existing_metadata = extract_notice_metadata_v12(
                detail_payload,
                detail["artifact_sha256"],
            )
        else:
            if current_metadata_error is not None:
                raise current_metadata_error
            existing_metadata = metadata
        expected_raw = _expected_replay_raw(
            source_bundle_path=source_bundle_path,
            source_manifest=source_manifest,
            existing_manifest=existing,
            source_uid=source_uid,
            metadata=existing_metadata,
        )
        existing_file_count = _verify_existing_corpus_target(
            target,
            existing,
            source_uid=source_uid,
            origin_bundle_id=source_verification["bundle_id"],
            origin_fingerprint=source_verification[
                "bundle_fingerprint"
            ],
            source_manifest=source_manifest,
            metadata=existing_metadata,
            publication_date=publication_date,
            expected_raw=expected_raw,
        )
        return {
            "status": "passed",
            "replayed": True,
            "source_uid": source_uid,
            "bundle_path": str(target),
            "manifest_sha256": file_sha256(existing_path),
            "file_count": existing_file_count,
        }
    if current_metadata_error is not None:
        raise current_metadata_error

    attachment_rows = [
        row
        for row in source_manifest["resources"]
        if row["relation"] == "declared_attachment"
    ]
    attachment_rows.sort(key=lambda row: row.get("declared_sequence", -1))
    if [
        row.get("declared_sequence") for row in attachment_rows
    ] != list(range(len(attachment_rows))):
        raise ContractError("declared attachment sequence is not contiguous")
    odt_rows = [
        row
        for row in attachment_rows
        if row["media_type"] == "application/vnd.oasis.opendocument.text"
    ]
    if not odt_rows:
        raise ContractError("corpus bundle requires at least one ODT")

    attachment_plan: list[tuple[str, bytes, str, dict[str, Any]]] = []
    for row in attachment_rows:
        file_name = _attachment_file_name(row)
        payload = (source_bundle_path / row["content_path"]).read_bytes()
        attachment_plan.append(
            (file_name, payload, "declared_attachment", row)
        )
    attachment_names = {
        row["declared_sequence"]: name
        for name, _payload, _role, row in attachment_plan
    }
    odt_sources = []
    for row in odt_rows:
        payload = (source_bundle_path / row["content_path"]).read_bytes()
        odt_sources.append(
            {
                "file_name": attachment_names[row["declared_sequence"]],
                "declared_sequence": row["declared_sequence"],
                "declared_label": row["declared_label"],
                "blocks": extract_odt_blocks(
                    payload, row["artifact_sha256"]
                ),
            }
        )
    raw_md = _raw_markdown(
        source_uid=source_uid,
        metadata=metadata,
        canonical_url=source_manifest["rss_item"]["link"],
        odt_sources=odt_sources,
    )

    base_plan: list[tuple[str, bytes, str]] = [
        ("source.html", detail_payload, "detail_page"),
        (
            "source-rss.xml",
            (
                source_bundle_path
                / next(
                    row["content_path"]
                    for row in source_manifest["resources"]
                    if row["relation"] == "rss_feed"
                )
            ).read_bytes(),
            "rss_observation",
        ),
        ("raw.md", raw_md, "deterministic_extraction"),
    ]
    file_plan: list[tuple[str, bytes, str, dict[str, Any] | None]] = [
        (name, payload, role, None)
        for name, payload, role in base_plan
    ]
    file_plan.extend(attachment_plan)
    file_rows = []
    for name, payload, role, source_row in file_plan:
        file_row = {
            "file_name": name,
            "role": role,
            "sha256": sha256_bytes(payload),
            "byte_size": len(payload),
        }
        if source_row is not None:
            file_row.update(
                {
                    "declared_sequence": source_row["declared_sequence"],
                    "declared_label": source_row["declared_label"],
                    "media_type": source_row["media_type"],
                    "origin_artifact_sha256": source_row[
                        "artifact_sha256"
                    ],
                }
            )
        file_rows.append(file_row)
    corpus_manifest = {
        "schema": CORPUS_BUNDLE_SCHEMA,
        "schema_version": CORPUS_BUNDLE_SCHEMA_VERSION,
        "source_uid": source_uid,
        "source_type": "tw-gov",
        "department": "健保署",
        "publisher": "NHI",
        "type": "regulation",
        "source_lang": "zh-TW",
        "title_zh": metadata["subject_raw"],
        "ref_number": metadata["reference_number_normalized"],
        "ref_number_raw": metadata["reference_number_raw"],
        "ref_number_normalization": metadata[
            "reference_number_normalization"
        ],
        "ref_number_normalization_rule": metadata[
            "reference_number_normalization_rule"
        ],
        "canonical_url": source_manifest["rss_item"]["link"],
        "source_url": source_manifest["rss_item"]["link"],
        "publish_date": publication_date.isoformat(),
        "document_date_roc_raw": metadata["document_date_roc_raw"],
        "publication_date_roc_raw": metadata["publication_date_roc_raw"],
        "update_date_roc_raw": metadata["update_date_roc_raw"],
        "source_binary_state": "captured_verified",
        "citation_eligible": True,
        "raw_md_sha256": sha256_bytes(raw_md),
        "raw_md_bytes": len(raw_md),
        "origin_update_bundle_id": source_verification["bundle_id"],
        "origin_update_bundle_fingerprint": source_verification[
            "bundle_fingerprint"
        ],
        "declared_attachment_count": len(attachment_rows),
        "files": file_rows,
        "extraction_status": {
            "deterministic_blocks": "done",
            "proofread": "not_started",
            "legal_history_promotion": "blocked_pending_anchor_replay",
        },
    }
    manifest_bytes = canonical_json_bytes(corpus_manifest)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{source_uid}.", dir=target.parent)
    )
    try:
        for name, payload, _role, _source_row in file_plan:
            _write(temporary / name, payload)
        _write(temporary / "manifest.json", manifest_bytes)
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return {
        "status": "passed",
        "replayed": False,
        "source_uid": source_uid,
        "bundle_path": str(target),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "file_count": len(file_plan) + 1,
    }
