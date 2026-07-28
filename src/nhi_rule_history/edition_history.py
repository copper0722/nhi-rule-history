"""Normalized official-edition history and reader projection.

PostgreSQL is the authority. JSONL, SQLite, and the browser payload are
deterministic exports of the same selected PostgreSQL rows.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


EXTRACTOR_VERSION = "chapter-00-edition-extractor/v1"
DIFF_VERSION = "chapter-00-reader-diff/v1.1"
RULE_ID = "rule:general-principles"
RULE_SLUG = "general-principles"
ANNUAL_STAGE_RUN_ID = uuid.UUID("33ce4d34-ab19-40be-bbe6-f7838a97ead5")
CURRENT_ACQUISITION_RUN_ID = uuid.UUID(
    "06fbf976-fa8c-4f7a-b682-c3e94f9bf23e"
)
CURRENT_PARSE_RUN_ID = uuid.UUID("baae912e-8d5f-46b0-9efd-77cf4d567428")
SOURCE_PAGE_URL = "https://www.nhi.gov.tw/ch/cp-2192-9951a-2509-1.html"

TOP_LEVEL_RE = re.compile(r"^([一二三四五六七八九十]+)、\s*(.*)")
SECTION_ONE_RE = re.compile(r"^第\s*1\s*(?:章|節)\b")
EDITION_LABEL_RE = re.compile(r"^(?P<year>\d{2,3})年(?:(?P<month>\d{1,2})月)?版$")
CURRENT_UPDATE_RE = re.compile(
    r"通則\s*[（(](?P<year>\d{2,3})[.／/](?P<month>\d{1,2})"
    r"[.／/](?P<day>\d{1,2})更新[）)]"
)
ROC_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{2,3})\s*/\s*(?P<month>\d{1,2})"
    r"\s*/\s*(?P<day>\d{1,2})(?!\d)"
)
LIST_PREFIX_RE = re.compile(
    r"^(?:"
    r"[一二三四五六七八九十]+、|"
    r"[（(][一二三四五六七八九十0-9]+[）)]|"
    r"\d+[.、]|"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.、]"
    r")"
)
TOKEN_RE = re.compile(
    r"[A-Za-zΑ-Ωα-ω]+(?:[-+][A-Za-z0-9Α-Ωα-ω]+)*"
    r"|\d+(?:[.,/]\d+)*"
    r"|[\u3400-\u9fff]"
    r"|[^\w\s]"
    r"|\s+",
    re.UNICODE,
)


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    source_kind: str
    official_label: str
    source_page_url: str
    official_url: str
    artifact_sha256: str
    media_type: str
    byte_length: int
    source_stage_schema: str
    source_stage_run_id: uuid.UUID
    source_resource_id: str | None
    source_locator: dict[str, Any]
    observed_at: datetime


@dataclass(frozen=True)
class SourceBlock:
    source_block_ids: tuple[str, ...]
    source_orders: tuple[int, ...]
    block_kind: str
    raw_text: str
    normalized_text: str
    comparison_key: str
    structural_path: tuple[str, ...]
    source_locators: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Edition:
    version_id: str
    chronology_order: int
    version_label: str
    primary_document: SourceDocument
    evidence_documents: tuple[tuple[SourceDocument, str, str], ...]
    blocks: tuple[SourceBlock, ...]
    source_locator: dict[str, Any]

    @property
    def raw_text(self) -> str:
        return "\n".join(block.raw_text for block in self.blocks)

    @property
    def normalized_text(self) -> str:
        return "\n".join(block.normalized_text for block in self.blocks)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{_sha256_text(_canonical_json(value))}"


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _display_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _comparison_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", "", normalized)


def _heading_text(text: str) -> bool:
    return _comparison_key(text) in {
        _comparison_key("藥品給付規定通則"),
        _comparison_key("全民健康保險藥品給付規定通則"),
    }


def _should_join_soft_wrap(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous[-1] in "。；：！？.!?;:）)]」』":
        return False
    if LIST_PREFIX_RE.match(current):
        return False
    return True


def _logical_blocks(rows: Sequence[dict[str, Any]]) -> tuple[SourceBlock, ...]:
    logical: list[dict[str, Any]] = []
    current_path: tuple[str, ...] = ()
    for row in rows:
        raw = str(row["raw_text"])
        display = _display_text(raw)
        if not display:
            continue
        top_match = TOP_LEVEL_RE.match(display)
        if top_match:
            label_tail = top_match.group(2).rstrip("：:")
            current_path = (f"{top_match.group(1)}、{label_tail}",)

        if logical and _should_join_soft_wrap(
            str(logical[-1]["normalized_text"]),
            display,
        ):
            previous = logical[-1]
            previous["raw_text"] = f"{previous['raw_text']}\n{raw}"
            previous["normalized_text"] = (
                f"{previous['normalized_text']}{display}"
            )
            previous["comparison_key"] = _comparison_key(
                str(previous["normalized_text"])
            )
            previous["source_block_ids"].append(str(row["block_id"]))
            previous["source_orders"].append(int(row["source_order"]))
            previous["source_locators"].append(dict(row["locator"]))
            continue

        logical.append(
            {
                "source_block_ids": [str(row["block_id"])],
                "source_orders": [int(row["source_order"])],
                "block_kind": str(row["block_kind"]),
                "raw_text": raw,
                "normalized_text": display,
                "comparison_key": _comparison_key(display),
                "structural_path": current_path,
                "source_locators": [dict(row["locator"])],
            }
        )

    return tuple(
        SourceBlock(
            source_block_ids=tuple(item["source_block_ids"]),
            source_orders=tuple(item["source_orders"]),
            block_kind=str(item["block_kind"]),
            raw_text=str(item["raw_text"]),
            normalized_text=str(item["normalized_text"]),
            comparison_key=str(item["comparison_key"]),
            structural_path=tuple(item["structural_path"]),
            source_locators=tuple(item["source_locators"]),
        )
        for item in logical
    )


def _extract_chapter_rows(
    rows: Sequence[dict[str, Any]],
    *,
    allow_eof: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    heading_index = next(
        (index for index, row in enumerate(rows) if _heading_text(row["raw_text"])),
        None,
    )
    if heading_index is None:
        raise ValueError("通則 heading not found")

    end_index = next(
        (
            index
            for index, row in enumerate(rows[heading_index + 1 :], heading_index + 1)
            if SECTION_ONE_RE.match(_display_text(str(row["raw_text"])))
        ),
        None,
    )
    if end_index is None:
        if not allow_eof:
            raise ValueError("第1章／第1節 boundary not found")
        end_index = len(rows)

    selected = [
        dict(row)
        for row in rows[heading_index + 1 : end_index]
        if _display_text(str(row["raw_text"]))
    ]
    if not selected:
        raise ValueError("通則 chapter is empty")
    return selected, {
        "heading_source_order": int(rows[heading_index]["source_order"]),
        "end_source_order_exclusive": (
            int(rows[end_index]["source_order"])
            if end_index < len(rows)
            else None
        ),
        "source_block_count": len(selected),
        "boundary": "next_section_heading" if end_index < len(rows) else "eof",
    }


def _document_id(artifact_sha256: str) -> str:
    return f"document:{artifact_sha256}"


def _version_id(document: SourceDocument, version_label: str) -> str:
    return _stable_id(
        "version",
        {
            "rule_id": RULE_ID,
            "primary_document_id": document.document_id,
            "version_label": version_label,
        },
    )


def _read_manifest(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            filename = str(row["filename"])
            if filename in result:
                raise ValueError(f"duplicate manifest filename at line {line_number}")
            result[filename] = row
    if len(result) != 14:
        raise ValueError(f"expected 14 annual manifest rows, got {len(result)}")
    return result


def _annual_editions(
    conn: psycopg.Connection[Any],
    manifest_path: Path,
) -> list[Edition]:
    manifest = _read_manifest(manifest_path)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              source_order_index,
              basename,
              filename_label_raw,
              release_id,
              byte_length,
              analysis_chronology
            FROM tw_drug_history_stage.source_release
            WHERE run_id = %s
            ORDER BY source_order_index DESC
            """,
            (ANNUAL_STAGE_RUN_ID,),
        )
        releases = list(cur.fetchall())
    if len(releases) != 14:
        raise ValueError(f"expected 14 annual stage releases, got {len(releases)}")

    editions: list[Edition] = []
    for chronology_order, release in enumerate(releases):
        filename = str(release["basename"])
        source = manifest.get(filename)
        if source is None:
            raise ValueError(f"annual manifest missing {filename}")
        if source["sha256"] != str(release["release_id"]):
            raise ValueError(f"manifest/stage hash mismatch for {filename}")
        if int(source["byte_length"]) != int(release["byte_length"]):
            raise ValueError(f"manifest/stage byte mismatch for {filename}")

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  block_id,
                  parser_order AS source_order,
                  block_kind,
                  raw_text,
                  locator
                FROM tw_drug_history_stage.structural_block
                WHERE run_id = %s
                  AND artifact_sha256 = %s
                ORDER BY parser_order
                """,
                (ANNUAL_STAGE_RUN_ID, release["release_id"]),
            )
            all_rows = list(cur.fetchall())
        selected, locator = _extract_chapter_rows(all_rows, allow_eof=False)
        blocks = _logical_blocks(selected)

        verified_on = date.fromisoformat(str(source["verified_on"]))
        document = SourceDocument(
            document_id=_document_id(str(source["sha256"])),
            source_kind="annual_full",
            official_label=str(source["official_label"]),
            source_page_url=str(source["source_page_url"]),
            official_url=str(source["official_url"]),
            artifact_sha256=str(source["sha256"]),
            media_type=str(source["media_type"]),
            byte_length=int(source["byte_length"]),
            source_stage_schema="tw_drug_history_stage",
            source_stage_run_id=ANNUAL_STAGE_RUN_ID,
            source_resource_id=None,
            source_locator={
                "filename": filename,
                "source_order_index": int(release["source_order_index"]),
                "analysis_chronology": release["analysis_chronology"],
            },
            observed_at=datetime.combine(
                verified_on,
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
        )
        version_label = str(source["official_label"])
        editions.append(
            Edition(
                version_id=_version_id(document, version_label),
                chronology_order=chronology_order,
                version_label=version_label,
                primary_document=document,
                evidence_documents=((document, "primary_text", "primary"),),
                blocks=blocks,
                source_locator={
                    **locator,
                    "artifact_sha256": document.artifact_sha256,
                    "source_stage_schema": document.source_stage_schema,
                    "source_stage_run_id": str(document.source_stage_run_id),
                    "statement": (
                        "Official cumulative edition snapshot; edition label "
                        "is not asserted as a legal effective date."
                    ),
                },
            )
        )
    return editions


def _current_document(
    conn: psycopg.Connection[Any],
    source_kind: str,
) -> tuple[SourceDocument, list[dict[str, Any]]]:
    resource_kind = {
        "current_chapter": "official_current_chapter_attachment",
        "current_full": "official_current_whole_attachment",
    }[source_kind]
    label_clause = (
        "AND resource.source_label LIKE '通則(%%'"
        if source_kind == "current_chapter"
        else ""
    )
    query = f"""
        SELECT
          resource.resource_id,
          resource.source_label,
          resource.source_url,
          resource.row_payload,
          artifact.artifact_sha256,
          artifact.byte_size,
          artifact.media_type,
          artifact.first_observed_at
        FROM tw_drug_history_acq_stage.discovered_resource resource
        JOIN tw_drug_history_acq_stage.resource_artifact_link link_row
          ON link_row.run_id = resource.run_id
          AND link_row.resource_id = resource.resource_id
        JOIN tw_drug_history_acq_stage.raw_artifact artifact
          ON artifact.run_id = link_row.run_id
          AND artifact.artifact_sha256 = link_row.artifact_sha256
        WHERE resource.run_id = %s
          AND resource.resource_kind = %s
          AND resource.source_url LIKE '%%.odt'
          {label_clause}
        ORDER BY resource.source_label DESC
        LIMIT 1
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (CURRENT_ACQUISITION_RUN_ID, resource_kind))
        source = cur.fetchone()
    if source is None:
        raise ValueError(f"current {source_kind} ODT resource not found")

    artifact_sha256 = str(source["artifact_sha256"])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              block_id,
              xml_element_index AS source_order,
              block_kind,
              raw_text,
              locator
            FROM tw_drug_history_structural_stage.structural_block
            WHERE parse_run_id = %s
              AND artifact_sha256 = %s
            ORDER BY xml_element_index
            """,
            (CURRENT_PARSE_RUN_ID, artifact_sha256),
        )
        rows = list(cur.fetchall())
    if not rows:
        raise ValueError(f"current {source_kind} structural blocks not found")

    locator = dict(source["row_payload"].get("discovery_locator", {}))
    source_page_url = (
        SOURCE_PAGE_URL
        if source_kind == "current_chapter"
        else "https://www.nhi.gov.tw/ch/cp-13108-67ddf-2508-1.html"
    )
    document = SourceDocument(
        document_id=_document_id(artifact_sha256),
        source_kind=source_kind,
        official_label=str(source["source_label"]),
        source_page_url=source_page_url,
        official_url=str(source["source_url"]),
        artifact_sha256=artifact_sha256,
        media_type=str(source["media_type"]),
        byte_length=int(source["byte_size"]),
        source_stage_schema="tw_drug_history_structural_stage",
        source_stage_run_id=CURRENT_PARSE_RUN_ID,
        source_resource_id=str(source["resource_id"]),
        source_locator={
            "resource_kind": resource_kind,
            "discovery_locator": locator,
        },
        observed_at=source["first_observed_at"],
    )
    return document, rows


def _current_edition(
    conn: psycopg.Connection[Any],
    chronology_order: int,
) -> Edition:
    chapter_document, chapter_rows = _current_document(conn, "current_chapter")
    whole_document, whole_rows = _current_document(conn, "current_full")
    chapter_selected, chapter_locator = _extract_chapter_rows(
        chapter_rows,
        allow_eof=True,
    )
    whole_selected, whole_locator = _extract_chapter_rows(
        whole_rows,
        allow_eof=False,
    )
    chapter_blocks = _logical_blocks(chapter_selected)
    whole_blocks = _logical_blocks(whole_selected)
    chapter_text = "\n".join(block.normalized_text for block in chapter_blocks)
    whole_text = "\n".join(block.normalized_text for block in whole_blocks)
    chapter_key = _comparison_key(chapter_text)
    whole_key = _comparison_key(whole_text)
    if chapter_key != whole_key:
        parity_status = "content_mismatch"
    elif chapter_text == whole_text:
        parity_status = "exact_normalized"
    else:
        parity_status = "format_only_difference"

    version_label = chapter_document.official_label
    return Edition(
        version_id=_version_id(chapter_document, version_label),
        chronology_order=chronology_order,
        version_label=version_label,
        primary_document=chapter_document,
        evidence_documents=(
            (chapter_document, "primary_text", "primary"),
            (
                whole_document,
                "whole_document_cross_check",
                parity_status,
            ),
        ),
        blocks=chapter_blocks,
        source_locator={
            **chapter_locator,
            "artifact_sha256": chapter_document.artifact_sha256,
            "source_stage_schema": chapter_document.source_stage_schema,
            "source_stage_run_id": str(chapter_document.source_stage_run_id),
            "whole_document_cross_check": {
                **whole_locator,
                "artifact_sha256": whole_document.artifact_sha256,
                "parity_status": parity_status,
            },
            "statement": (
                "Official current chapter snapshot. The update date is stored "
                "with its source role and is not silently promoted to a legal "
                "effective date."
            ),
        },
    )


def collect_editions(
    conn: psycopg.Connection[Any],
    manifest_path: Path,
) -> list[Edition]:
    editions = _annual_editions(conn, manifest_path)
    editions.append(_current_edition(conn, len(editions)))
    if len(editions) != 15:
        raise ValueError(f"expected 15 editions, got {len(editions)}")
    return editions


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _inline_segments(old_text: str | None, new_text: str | None) -> list[dict[str, str]]:
    old_tokens = _tokenize(old_text or "")
    new_tokens = _tokenize(new_text or "")
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    segments: list[dict[str, str]] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in ("equal", "delete", "replace") and old_start != old_end:
            segments.append(
                {
                    "side": "both" if tag == "equal" else "old",
                    "kind": "unchanged" if tag == "equal" else "removed",
                    "text": "".join(old_tokens[old_start:old_end]),
                }
            )
        if tag in ("insert", "replace") and new_start != new_end:
            segments.append(
                {
                    "side": "new",
                    "kind": "added",
                    "text": "".join(new_tokens[new_start:new_end]),
                }
            )
    return segments


def _block_id(version_id: str, source_order: int, raw_text: str) -> str:
    return _stable_id(
        "block",
        {
            "version_id": version_id,
            "source_order": source_order,
            "raw_sha256": _sha256_text(raw_text),
        },
    )


def _materialized_blocks(edition: Edition) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_order, block in enumerate(edition.blocks):
        rows.append(
            {
                "block_id": _block_id(
                    edition.version_id,
                    source_order,
                    block.raw_text,
                ),
                "version_id": edition.version_id,
                "source_order": source_order,
                "block_kind": block.block_kind,
                "structural_path": list(block.structural_path),
                "raw_text": block.raw_text,
                "normalized_text": block.normalized_text,
                "comparison_key": block.comparison_key,
                "raw_sha256": _sha256_text(block.raw_text),
                "source_locator": {
                    "source_block_ids": list(block.source_block_ids),
                    "source_orders": list(block.source_orders),
                    "source_locators": list(block.source_locators),
                },
            }
        )
    return rows


def _context_label(
    old_blocks: Sequence[dict[str, Any]],
    new_blocks: Sequence[dict[str, Any]],
) -> str:
    for block in [*new_blocks, *old_blocks]:
        path = block["structural_path"]
        if path:
            return str(path[-1])
    return "通則"


def _edge_rows(
    older: Edition,
    newer: Edition,
    old_blocks: Sequence[dict[str, Any]],
    new_blocks: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    matcher = SequenceMatcher(
        None,
        [row["comparison_key"] for row in old_blocks],
        [row["comparison_key"] for row in new_blocks],
        autojunk=False,
    )
    hunks: list[dict[str, Any]] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        selected_old = list(old_blocks[old_start:old_end])
        selected_new = list(new_blocks[new_start:new_end])
        old_text = (
            "\n".join(str(row["normalized_text"]) for row in selected_old)
            or None
        )
        new_text = (
            "\n".join(str(row["normalized_text"]) for row in selected_new)
            or None
        )
        change_kind = {
            "delete": "removed",
            "insert": "added",
            "replace": "replaced",
        }[tag]
        payload = {
            "edge": [older.version_id, newer.version_id],
            "hunk_order": len(hunks),
            "change_kind": change_kind,
            "old_text_sha256": _sha256_text(old_text) if old_text else None,
            "new_text_sha256": _sha256_text(new_text) if new_text else None,
        }
        hunks.append(
            {
                "hunk_id": _stable_id("hunk", payload),
                "hunk_order": len(hunks),
                "change_kind": change_kind,
                "context_label": _context_label(selected_old, selected_new),
                "old_block_id": (
                    selected_old[0]["block_id"]
                    if len(selected_old) == 1
                    else None
                ),
                "new_block_id": (
                    selected_new[0]["block_id"]
                    if len(selected_new) == 1
                    else None
                ),
                "old_text": old_text,
                "new_text": new_text,
                "old_text_sha256": (
                    _sha256_text(old_text) if old_text else None
                ),
                "new_text_sha256": (
                    _sha256_text(new_text) if new_text else None
                ),
                "inline_segments": _inline_segments(old_text, new_text),
                "display_note": {
                    "added": "本版新增",
                    "removed": "本版刪除",
                    "replaced": "本版改寫",
                }[change_kind],
            }
        )

    format_only = (
        not hunks
        and _sha256_text(older.raw_text) != _sha256_text(newer.raw_text)
    )
    edge_input = {
        "older_version_id": older.version_id,
        "newer_version_id": newer.version_id,
        "older_normalized_sha256": _sha256_text(older.normalized_text),
        "newer_normalized_sha256": _sha256_text(newer.normalized_text),
        "algorithm_version": DIFF_VERSION,
    }
    edge_id = _stable_id("edge", edge_input)
    output_payload = [
        {
            key: hunk[key]
            for key in (
                "hunk_order",
                "change_kind",
                "context_label",
                "old_text_sha256",
                "new_text_sha256",
                "inline_segments",
            )
        }
        for hunk in hunks
    ]
    edge = {
        "edge_id": edge_id,
        "rule_id": RULE_ID,
        "older_version_id": older.version_id,
        "newer_version_id": newer.version_id,
        "adjacency_basis": "adjacent_official_edition",
        "legal_predecessor_status": "not_claimed",
        "crosses_known_gap": True,
        "algorithm_version": DIFF_VERSION,
        "input_sha256": _sha256_text(_canonical_json(edge_input)),
        "output_sha256": _sha256_text(_canonical_json(output_payload)),
        "format_only": format_only,
        "change_hunk_count": len(hunks),
        "status": "verified_edition_diff",
    }
    for hunk in hunks:
        hunk["edge_id"] = edge_id
    return edge, hunks


def _edition_date_fact(edition: Edition) -> dict[str, Any]:
    label = edition.version_label
    annual = EDITION_LABEL_RE.match(label)
    if annual:
        roc_year = int(annual.group("year"))
        month = int(annual.group("month") or 1)
        value = date(roc_year + 1911, month, 1)
        precision = "month" if annual.group("month") else "year"
        role = "official_edition_label"
    else:
        current = CURRENT_UPDATE_RE.search(label)
        if current is None:
            raise ValueError(f"cannot parse edition label date: {label}")
        value = date(
            int(current.group("year")) + 1911,
            int(current.group("month")),
            int(current.group("day")),
        )
        precision = "day"
        role = "official_update_date"
    locator = {
        "source": "official_label",
        "document_id": edition.primary_document.document_id,
    }
    return {
        "date_fact_id": _stable_id(
            "date",
            [edition.version_id, role, label, value.isoformat(), locator],
        ),
        "version_id": edition.version_id,
        "date_role": role,
        "raw_value": label,
        "calendar_system": "ROC",
        "date_value": value,
        "date_precision": precision,
        "basis": (
            "Official edition/update label. This field is not a legal "
            "effective-date claim."
        ),
        "legal_effective_status": "not_claimed",
        "source_locator": locator,
    }


def _annotation_date_facts(
    edition: Edition,
    block_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for block in block_rows:
        text = str(block["raw_text"])
        for match in ROC_DATE_RE.finditer(text):
            raw = match.group(0)
            try:
                value = date(
                    int(match.group("year")) + 1911,
                    int(match.group("month")),
                    int(match.group("day")),
                )
                precision = "day"
                status = "candidate_unresolved"
            except ValueError:
                value = None
                precision = "unknown"
                status = "rejected_non_date"
            locator = {
                "block_id": block["block_id"],
                "char_start": match.start(),
                "char_end": match.end(),
                "raw_text_sha256": block["raw_sha256"],
            }
            facts.append(
                {
                    "date_fact_id": _stable_id(
                        "date",
                        [
                            edition.version_id,
                            "text_amendment_annotation",
                            raw,
                            locator,
                        ],
                    ),
                    "version_id": edition.version_id,
                    "date_role": "text_amendment_annotation",
                    "raw_value": raw,
                    "calendar_system": "ROC",
                    "date_value": value,
                    "date_precision": precision,
                    "basis": (
                        "Source-local date annotation. It remains unresolved "
                        "until an official event and transition are verified."
                    ),
                    "legal_effective_status": status,
                    "source_locator": locator,
                }
            )
    return facts


def build_rows(editions: Sequence[Edition]) -> dict[str, list[dict[str, Any]]]:
    documents: dict[str, SourceDocument] = {}
    for edition in editions:
        for document, _, _ in edition.evidence_documents:
            documents[document.document_id] = document

    source_documents = [
        {
            "document_id": document.document_id,
            "source_kind": document.source_kind,
            "official_label": document.official_label,
            "source_page_url": document.source_page_url,
            "official_url": document.official_url,
            "artifact_sha256": document.artifact_sha256,
            "media_type": document.media_type,
            "byte_length": document.byte_length,
            "source_stage_schema": document.source_stage_schema,
            "source_stage_run_id": document.source_stage_run_id,
            "source_resource_id": document.source_resource_id,
            "source_locator": document.source_locator,
            "observed_at": document.observed_at,
        }
        for document in sorted(documents.values(), key=lambda item: item.document_id)
    ]

    versions: list[dict[str, Any]] = []
    version_sources: list[dict[str, Any]] = []
    dates: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    blocks_by_version: dict[str, list[dict[str, Any]]] = {}
    for edition in editions:
        materialized = _materialized_blocks(edition)
        blocks_by_version[edition.version_id] = materialized
        blocks.extend(materialized)
        versions.append(
            {
                "version_id": edition.version_id,
                "rule_id": RULE_ID,
                "primary_document_id": edition.primary_document.document_id,
                "chronology_order": edition.chronology_order,
                "version_label": edition.version_label,
                "raw_text": edition.raw_text,
                "normalized_text": edition.normalized_text,
                "structured_json": {
                    "schema": "nhi-rule-history/edition-structured-text/v1",
                    "display_label": "通則",
                    "navigation_code": "chapter:00",
                    "navigation_code_origin": "project_assigned",
                    "blocks": [
                        {
                            "source_order": row["source_order"],
                            "structural_path": row["structural_path"],
                            "normalized_text": row["normalized_text"],
                        }
                        for row in materialized
                    ],
                },
                "raw_sha256": _sha256_text(edition.raw_text),
                "normalized_sha256": _sha256_text(edition.normalized_text),
                "source_locator": edition.source_locator,
                "extractor_version": EXTRACTOR_VERSION,
                "validation_status": "verified_source_snapshot",
                "legal_effective_status": "not_claimed",
            }
        )
        for document, role, parity in edition.evidence_documents:
            version_sources.append(
                {
                    "version_id": edition.version_id,
                    "document_id": document.document_id,
                    "evidence_role": role,
                    "parity_status": parity,
                    "source_locator": edition.source_locator,
                }
            )
        dates.append(_edition_date_fact(edition))
        dates.extend(_annotation_date_facts(edition, materialized))

    edges: list[dict[str, Any]] = []
    hunks: list[dict[str, Any]] = []
    for older, newer in zip(editions, editions[1:]):
        edge, edge_hunks = _edge_rows(
            older,
            newer,
            blocks_by_version[older.version_id],
            blocks_by_version[newer.version_id],
        )
        edges.append(edge)
        hunks.extend(edge_hunks)

    return {
        "source_document": source_documents,
        "rule": [
            {
                "rule_id": RULE_ID,
                "canonical_slug": RULE_SLUG,
                "display_label": "通則",
                "source_designation_raw": "通則",
                "navigation_code": "chapter:00",
                "navigation_code_origin": "project_assigned",
                "identity_status": "active",
            }
        ],
        "rule_version": versions,
        "version_source": version_sources,
        "rule_version_date": dates,
        "rule_block": blocks,
        "version_edge": edges,
        "diff_hunk": hunks,
    }


def _source_set_sha256(rows: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        "documents": [
            {
                "document_id": row["document_id"],
                "artifact_sha256": row["artifact_sha256"],
            }
            for row in rows["source_document"]
        ],
        "versions": [
            {
                "version_id": row["version_id"],
                "normalized_sha256": row["normalized_sha256"],
            }
            for row in rows["rule_version"]
        ],
        "extractor_version": EXTRACTOR_VERSION,
        "diff_version": DIFF_VERSION,
    }
    return _sha256_text(_canonical_json(payload))


def _dataset_output_sha256(rows: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        table: [
            {
                key: value
                for key, value in row.items()
                if key not in {"first_import_run_id", "import_run_id"}
            }
            for row in table_rows
        ]
        for table, table_rows in sorted(rows.items())
    }
    return _sha256_text(_canonical_json(payload))


def _insert_many(
    cur: psycopg.Cursor[Any],
    table: str,
    rows: Sequence[dict[str, Any]],
    *,
    json_columns: set[str] | None = None,
) -> None:
    if not rows:
        return
    json_columns = json_columns or set()
    columns = list(rows[0])
    placeholders = ", ".join(["%s"] * len(columns))
    quoted = ", ".join(f'"{column}"' for column in columns)
    values: list[tuple[Any, ...]] = []
    for row in rows:
        if list(row) != columns:
            raise ValueError(f"inconsistent columns for {table}")
        values.append(
            tuple(
                Jsonb(row[column]) if column in json_columns else row[column]
                for column in columns
            )
        )
    cur.executemany(
        f"""
        INSERT INTO nhi_rule_history_edition.{table} ({quoted})
        VALUES ({placeholders})
        ON CONFLICT DO NOTHING
        """,
        values,
    )


def rebuild(
    conn: psycopg.Connection[Any],
    *,
    manifest_path: Path,
) -> dict[str, Any]:
    editions = collect_editions(conn, manifest_path)
    rows = build_rows(editions)
    source_set_sha256 = _source_set_sha256(rows)
    run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nhi-rule-history-edition:{source_set_sha256}",
    )
    output_sha256 = _dataset_output_sha256(rows)
    started_at = datetime.now(timezone.utc)
    row_counts = {table: len(table_rows) for table, table_rows in rows.items()}
    sealed_counts = {**row_counts, "coverage_assessment": 1}

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nhi-rule-history-edition-loader-v1",),
            )
            cur.execute(
                """
                SELECT state, output_sha256, row_counts
                FROM nhi_rule_history_edition.import_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            existing = cur.fetchone()
            if existing is not None:
                if (
                    existing["state"] != "sealed"
                    or existing["output_sha256"] != output_sha256
                    or dict(existing["row_counts"]) != sealed_counts
                ):
                    raise ValueError("existing import run does not match replay")
                return {
                    "status": "already_sealed",
                    "run_id": str(run_id),
                    "source_set_sha256": source_set_sha256,
                    "output_sha256": output_sha256,
                    "row_counts": sealed_counts,
                }

            cur.execute(
                """
                INSERT INTO nhi_rule_history_edition.import_run (
                  run_id, source_set_sha256, extractor_version, diff_version,
                  state, source_stage_refs, started_at
                ) VALUES (%s, %s, %s, %s, 'loading', %s, %s)
                """,
                (
                    run_id,
                    source_set_sha256,
                    EXTRACTOR_VERSION,
                    DIFF_VERSION,
                    Jsonb(
                        {
                            "annual_stage_run_id": str(ANNUAL_STAGE_RUN_ID),
                            "current_acquisition_run_id": str(
                                CURRENT_ACQUISITION_RUN_ID
                            ),
                            "current_parse_run_id": str(CURRENT_PARSE_RUN_ID),
                        }
                    ),
                    started_at,
                ),
            )

            document_rows = [
                {"first_import_run_id": run_id, **row}
                for row in rows["source_document"]
            ]
            _insert_many(
                cur,
                "source_document",
                document_rows,
                json_columns={"source_locator"},
            )
            _insert_many(cur, "rule", rows["rule"])
            version_rows = [
                {"first_import_run_id": run_id, **row}
                for row in rows["rule_version"]
            ]
            _insert_many(
                cur,
                "rule_version",
                version_rows,
                json_columns={"structured_json", "source_locator"},
            )
            _insert_many(
                cur,
                "version_source",
                rows["version_source"],
                json_columns={"source_locator"},
            )
            _insert_many(
                cur,
                "rule_version_date",
                rows["rule_version_date"],
                json_columns={"source_locator"},
            )
            _insert_many(
                cur,
                "rule_block",
                rows["rule_block"],
                json_columns={"structural_path", "source_locator"},
            )
            _insert_many(cur, "version_edge", rows["version_edge"])
            _insert_many(
                cur,
                "diff_hunk",
                rows["diff_hunk"],
                json_columns={"inline_segments"},
            )

            cur.execute(
                """
                SELECT count(*)
                FROM nhi_rule_history_edition.rule_version
                WHERE version_id = ANY(%s)
                """,
                ([row["version_id"] for row in rows["rule_version"]],),
            )
            if int(cur.fetchone()["count"]) != len(rows["rule_version"]):
                raise ValueError("rule_version identity/count parity failed")
            cur.execute(
                """
                SELECT count(*)
                FROM nhi_rule_history_edition.version_edge
                WHERE edge_id = ANY(%s)
                """,
                ([row["edge_id"] for row in rows["version_edge"]],),
            )
            if int(cur.fetchone()["count"]) != len(rows["version_edge"]):
                raise ValueError("version_edge identity/count parity failed")
            cur.execute(
                """
                SELECT count(*)
                FROM nhi_rule_history_edition.diff_hunk
                WHERE hunk_id = ANY(%s)
                """,
                ([row["hunk_id"] for row in rows["diff_hunk"]],),
            )
            if int(cur.fetchone()["count"]) != len(rows["diff_hunk"]):
                raise ValueError("diff_hunk identity/count parity failed")

            material_change_edges = sum(
                1
                for row in rows["version_edge"]
                if int(row["change_hunk_count"]) > 0
            )
            assessment_id = _stable_id(
                "coverage",
                [RULE_ID, str(run_id), source_set_sha256],
            )
            assessment = {
                "assessment_id": assessment_id,
                "rule_id": RULE_ID,
                "import_run_id": run_id,
                "declared_edition_count": len(editions),
                "loaded_edition_count": len(editions),
                "adjacent_edge_count": len(rows["version_edge"]),
                "material_change_edge_count": material_change_edges,
                "edition_set_complete": True,
                "official_source_universe_closed": False,
                "legal_history_complete": False,
                "status": "complete_for_declared_edition_set",
                "gap_reasons": [
                    {
                        "code": "OFFICIAL_SOURCE_UNIVERSE_OPEN",
                        "meaning": (
                            "The declared 14 annual ODT editions plus the "
                            "current chapter are complete, but this does not "
                            "prove that every amendment notice still exists "
                            "or has been found."
                        ),
                    },
                    {
                        "code": "LEGAL_ADJACENCY_NOT_CLAIMED",
                        "meaning": (
                            "Each diff compares adjacent official cumulative "
                            "editions, not necessarily adjacent legal events."
                        ),
                    },
                ],
                "assessed_at": started_at,
            }
            _insert_many(
                cur,
                "coverage_assessment",
                [assessment],
                json_columns={"gap_reasons"},
            )

            cur.execute(
                """
                UPDATE nhi_rule_history_edition.import_run
                SET state = 'sealed',
                    row_counts = %s,
                    output_sha256 = %s,
                    sealed_at = %s
                WHERE run_id = %s
                  AND state = 'loading'
                """,
                (
                    Jsonb(sealed_counts),
                    output_sha256,
                    datetime.now(timezone.utc),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("failed to seal import run")

    return {
        "status": "sealed",
        "run_id": str(run_id),
        "source_set_sha256": source_set_sha256,
        "output_sha256": output_sha256,
        "row_counts": {**row_counts, "coverage_assessment": 1},
    }


EXPORT_TABLES = (
    "import_run",
    "source_document",
    "rule",
    "rule_version",
    "version_source",
    "rule_version_date",
    "rule_block",
    "version_edge",
    "diff_hunk",
    "coverage_assessment",
)


def _rows_for_export(
    conn: psycopg.Connection[Any],
    table: str,
    rule_id: str,
) -> list[dict[str, Any]]:
    filters = {
        "import_run": (
            "run_id IN ("
            "SELECT import_run_id "
            "FROM nhi_rule_history_edition.coverage_assessment "
            "WHERE rule_id = %s)"
        ),
        "rule": "rule_id = %s",
        "rule_version": "rule_id = %s",
        "version_edge": "rule_id = %s",
        "coverage_assessment": "rule_id = %s",
        "source_document": (
            "document_id IN ("
            "SELECT document_id FROM nhi_rule_history_edition.version_source "
            "WHERE version_id IN ("
            "SELECT version_id FROM nhi_rule_history_edition.rule_version "
            "WHERE rule_id = %s))"
        ),
        "version_source": (
            "version_id IN (SELECT version_id "
            "FROM nhi_rule_history_edition.rule_version WHERE rule_id = %s)"
        ),
        "rule_version_date": (
            "version_id IN (SELECT version_id "
            "FROM nhi_rule_history_edition.rule_version WHERE rule_id = %s)"
        ),
        "rule_block": (
            "version_id IN (SELECT version_id "
            "FROM nhi_rule_history_edition.rule_version WHERE rule_id = %s)"
        ),
        "diff_hunk": (
            "edge_id IN (SELECT edge_id "
            "FROM nhi_rule_history_edition.version_edge WHERE rule_id = %s)"
        ),
    }
    order_by = {
        "import_run": "started_at",
        "source_document": "document_id",
        "rule": "rule_id",
        "rule_version": "chronology_order",
        "version_source": "version_id, evidence_role, document_id",
        "rule_version_date": "version_id, date_role, date_value, date_fact_id",
        "rule_block": "version_id, source_order",
        "version_edge": "newer_version_id",
        "diff_hunk": "edge_id, hunk_order",
        "coverage_assessment": "assessed_at",
    }
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT *
            FROM nhi_rule_history_edition.{table}
            WHERE {filters[table]}
            ORDER BY {order_by[table]}
            """,
            (rule_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def export_jsonl(
    conn: psycopg.Connection[Any],
    *,
    output_dir: Path,
    rule_id: str = RULE_ID,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for table in EXPORT_TABLES:
        rows = _rows_for_export(conn, table, rule_id)
        path = output_dir / f"{table}.jsonl"
        content = "".join(f"{_canonical_json(row)}\n" for row in rows)
        path.write_text(content, encoding="utf-8")
        counts[table] = len(rows)
        files[path.name] = {
            "rows": len(rows),
            "bytes": path.stat().st_size,
            "sha256": _sha256_bytes(path.read_bytes()),
        }
    manifest = {
        "schema": "nhi-rule-history/edition-export/v1",
        "generated_from": "PostgreSQL nhi_rule_history_edition",
        "rule_id": rule_id,
        "postgresql_is_authority": True,
        "legal_history_complete": False,
        "counts": counts,
        "files": files,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _primary_date(
    date_rows: Sequence[dict[str, Any]],
    version_id: str,
) -> dict[str, Any]:
    candidates = [
        row
        for row in date_rows
        if row["version_id"] == version_id
        and row["date_role"] in {
            "official_update_date",
            "official_edition_label",
        }
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one primary date for {version_id}")
    row = candidates[0]
    return {
        "role": row["date_role"],
        "raw_value": row["raw_value"],
        "date_value": row["date_value"],
        "precision": row["date_precision"],
        "legal_effective_status": row["legal_effective_status"],
    }


def reader_projection(
    conn: psycopg.Connection[Any],
    *,
    rule_id: str = RULE_ID,
) -> dict[str, Any]:
    data = {table: _rows_for_export(conn, table, rule_id) for table in EXPORT_TABLES}
    rule = data["rule"][0]
    versions = sorted(data["rule_version"], key=lambda row: row["chronology_order"])
    latest = versions[-1]
    documents = {
        row["document_id"]: row for row in data["source_document"]
    }
    blocks_by_version: dict[str, list[dict[str, Any]]] = {}
    for block in data["rule_block"]:
        blocks_by_version.setdefault(block["version_id"], []).append(block)
    hunks_by_edge: dict[str, list[dict[str, Any]]] = {}
    for hunk in data["diff_hunk"]:
        hunks_by_edge.setdefault(hunk["edge_id"], []).append(hunk)
    version_by_id = {row["version_id"]: row for row in versions}

    transitions: list[dict[str, Any]] = []
    edges = sorted(
        data["version_edge"],
        key=lambda row: version_by_id[row["newer_version_id"]]["chronology_order"],
        reverse=True,
    )
    for edge in edges:
        older = version_by_id[edge["older_version_id"]]
        newer = version_by_id[edge["newer_version_id"]]
        older_document = documents[older["primary_document_id"]]
        newer_document = documents[newer["primary_document_id"]]
        transitions.append(
            {
                "edge_id": edge["edge_id"],
                "older": {
                    "version_id": older["version_id"],
                    "label": older["version_label"],
                    "date": _primary_date(
                        data["rule_version_date"],
                        older["version_id"],
                    ),
                    "source_url": older_document["official_url"],
                },
                "newer": {
                    "version_id": newer["version_id"],
                    "label": newer["version_label"],
                    "date": _primary_date(
                        data["rule_version_date"],
                        newer["version_id"],
                    ),
                    "source_url": newer_document["official_url"],
                },
                "adjacency_basis": edge["adjacency_basis"],
                "legal_predecessor_status": edge["legal_predecessor_status"],
                "crosses_known_gap": edge["crosses_known_gap"],
                "format_only": edge["format_only"],
                "status": edge["status"],
                "hunks": [
                    {
                        "hunk_order": hunk["hunk_order"],
                        "change_kind": hunk["change_kind"],
                        "context_label": hunk["context_label"],
                        "old_text": hunk["old_text"],
                        "new_text": hunk["new_text"],
                        "inline_segments": hunk["inline_segments"],
                        "display_note": hunk["display_note"],
                    }
                    for hunk in sorted(
                        hunks_by_edge.get(edge["edge_id"], []),
                        key=lambda row: row["hunk_order"],
                    )
                    if hunk["change_kind"] != "format_only"
                ],
            }
        )

    latest_document = documents[latest["primary_document_id"]]
    coverage = sorted(
        data["coverage_assessment"],
        key=lambda row: row["assessed_at"],
    )[-1]
    return {
        "schema": "nhi-rule-history/reader-projection/v1",
        "generated_from": "PostgreSQL nhi_rule_history_edition",
        "rule": {
            "rule_id": rule["rule_id"],
            "canonical_slug": rule["canonical_slug"],
            "display_label": rule["display_label"],
            "source_designation_raw": rule["source_designation_raw"],
            "navigation_code": rule["navigation_code"],
            "navigation_code_origin": rule["navigation_code_origin"],
        },
        "latest": {
            "version_id": latest["version_id"],
            "label": latest["version_label"],
            "date": _primary_date(data["rule_version_date"], latest["version_id"]),
            "full_text_blocks": [
                {
                    "source_order": block["source_order"],
                    "structural_path": block["structural_path"],
                    "text": block["normalized_text"],
                }
                for block in sorted(
                    blocks_by_version[latest["version_id"]],
                    key=lambda row: row["source_order"],
                )
            ],
            "source": {
                "official_label": latest_document["official_label"],
                "official_url": latest_document["official_url"],
                "source_page_url": latest_document["source_page_url"],
                "artifact_sha256": latest_document["artifact_sha256"],
            },
        },
        "transitions": transitions,
        "coverage": {
            "declared_edition_count": coverage["declared_edition_count"],
            "loaded_edition_count": coverage["loaded_edition_count"],
            "adjacent_edge_count": coverage["adjacent_edge_count"],
            "material_change_edge_count": coverage["material_change_edge_count"],
            "edition_set_complete": coverage["edition_set_complete"],
            "official_source_universe_closed": coverage[
                "official_source_universe_closed"
            ],
            "legal_history_complete": coverage["legal_history_complete"],
            "status": coverage["status"],
            "gap_reasons": coverage["gap_reasons"],
        },
    }


def write_reader_projection(
    conn: psycopg.Connection[Any],
    *,
    output: Path,
    rule_id: str = RULE_ID,
) -> dict[str, Any]:
    projection = reader_projection(conn, rule_id=rule_id)
    content = (
        json.dumps(
            projection,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return {
        "output": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256_bytes(output.read_bytes()),
        "edition_count": projection["coverage"]["loaded_edition_count"],
        "transition_count": len(projection["transitions"]),
    }


def build_sqlite(
    *,
    jsonl_dir: Path,
    schema_path: Path,
    output: Path,
    force: bool = False,
) -> dict[str, Any]:
    if output.exists():
        if not force:
            raise ValueError(f"output exists: {output}")
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    counts: dict[str, int] = {}
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        for table in EXPORT_TABLES:
            path = jsonl_dir / f"{table}.jsonl"
            rows: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        rows.append(json.loads(line))
            columns = {
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            for row in rows:
                unknown = set(row) - columns
                if unknown:
                    raise ValueError(
                        f"{table}: unknown SQLite columns {sorted(unknown)}"
                    )
                keys = sorted(row)
                values = []
                for key in keys:
                    value = row[key]
                    if isinstance(value, bool):
                        value = int(value)
                    elif isinstance(value, (dict, list)):
                        value = _canonical_json(value)
                    values.append(value)
                conn.execute(
                    f"""
                    INSERT INTO "{table}" (
                      {", ".join(f'"{key}"' for key in keys)}
                    ) VALUES ({", ".join("?" for _ in keys)})
                    """,
                    values,
                )
            counts[table] = len(rows)
        conn.commit()
        foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
        if foreign_keys:
            raise ValueError(f"SQLite foreign_key_check: {foreign_keys[:3]}")
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check: {integrity}")
    except Exception:
        conn.close()
        if output.exists():
            output.unlink()
        raise
    finally:
        if output.exists():
            conn.close()
    return {
        "output": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256_bytes(output.read_bytes()),
        "counts": counts,
        "foreign_key_check": "passed",
        "integrity_check": "passed",
    }


def connect(dsn: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn)
