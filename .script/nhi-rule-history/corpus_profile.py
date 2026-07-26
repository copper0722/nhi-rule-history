#!/usr/bin/env python3
"""Read-only structural profiler for local NHI drug-payment-rule corpora.

Walks history/ and current/ under a caller-supplied corpus root, records
immutable artifact observations, and emits deterministic receipts.

Does NOT promote filename labels, embedded file metadata, or extracted text
to legal effective dates or canonical rule versions.

CLI (hyphenated directory is NOT a Python package import name):

    python3 corpus_profile.py --corpus-root ... --out-dir ...
    python3 run_profile.py --corpus-root ... --out-dir ...
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

try:
    from . import TOOL_VERSION
except ImportError:  # script-style execution from this directory
    TOOL_VERSION = "nhi-rule-history-corpus-profile/1.0.0"

SUPPORTED_EXTENSIONS = frozenset({".odt", ".docx"})
LANES = ("history", "current")

# Issue severity enum (documented): info | warning | error
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITIES = frozenset({SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR})

# ODT table repeat expansion: conservative logical expansion cap per attribute.
ODT_REPEAT_EXPANSION_CAP = 10_000

# Filename-derived release/update label patterns (raw capture only).
_HISTORY_LABEL_RE = re.compile(
    r"^(?P<id>\d+)_(?P<label>.+?)\.(?P<ext>odt|docx)$",
    re.IGNORECASE,
)
# Visible ROC-style date fragments in basename, e.g. 113.05.28 / 115.6.23 / 96年7月
_FILENAME_DATE_FRAGMENT_RE = re.compile(
    r"(?:"
    r"(?P<roc_dot>(?:1\d{2}|\d{2,3})\.(?:\d{1,2})\.(?:\d{1,2}))"
    r"|(?P<roc_year_month>(?:1\d{2}|\d{2,3})年(?:\d{1,2})月)"
    r"|(?P<roc_year_only>(?:1\d{2}|\d{2,3})年版)"
    r")"
)

# Historical release-label keys (analysis-only chronology, not legal dates).
_HIST_YEAR_MONTH_RE = re.compile(r"^(?P<y>\d{2,3})年(?P<m>\d{1,2})月")
_HIST_YEAR_BAN_RE = re.compile(r"^(?P<y>\d{2,3})年版")

# Embedded date/time property local-names only (never author/title/company).
ODT_DATE_META_NAMES = frozenset({"creation-date", "date", "print-date"})
DOCX_DATE_CORE_NAMES = frozenset({"created", "modified", "lastPrinted"})

# Official whole-file freshness anchor cited by plan/evaluation evidence only.
CITED_OFFICIAL_WHOLE_FILE_ANCHOR_LABEL = "115.07.23"
CITED_OFFICIAL_WHOLE_FILE_ANCHOR_SOURCE = (
    "plan/evaluation evidence: NHI current whole-file page title stamp 115.07.23"
)

DEFAULT_CANARY_STRINGS = (
    "9.138",
    "9.24",
    "3.3.13",
    "8.2.4.7",
    "1.3.5",
    "3.3.31",
    "gilteritinib",
    "aumolertinib",
    "pegunigalsidase alfa",
)

# ---------------------------------------------------------------------------
# Deterministic JSON helpers
# ---------------------------------------------------------------------------


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def stable_json_dumps(obj: Any) -> str:
    """Serialize with sorted keys and stable separators (no trailing newline)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(stable_json_dumps(row))
            fh.write("\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(obj) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def member_names_digest(names: Sequence[str]) -> str:
    """Digest of sorted member names (names only, not contents)."""
    payload = "\n".join(sorted(names)) + ("\n" if names else "")
    return sha256_text(payload)


def make_issue(
    *,
    issue_code: str,
    severity: str,
    relative_path: str | None,
    detail: str,
    issue_class: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a machine-readable issue row with the documented schema."""
    if severity not in SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}")
    row: dict[str, Any] = {
        "issue_code": issue_code,
        "severity": severity,
        "relative_path": relative_path,
        "detail": detail,
        "issue_class": issue_class,
    }
    for k, v in sorted(extra.items()):
        row[k] = v
    return row


def issue_sort_key(issue: Mapping[str, Any]) -> tuple:
    return (
        issue.get("issue_code", ""),
        issue.get("severity", ""),
        (issue.get("relative_path") or "").encode("utf-8"),
        stable_json_dumps(issue),
    )


# ---------------------------------------------------------------------------
# Filename label parsing (evidence candidates only)
# ---------------------------------------------------------------------------


def parse_filename_label(basename: str) -> dict[str, Any]:
    """Extract raw filename-derived release/update label without legal promotion.

    Returns fields clearly scoped as filename_label_* observations.
    """
    raw: dict[str, Any] = {
        "filename_label_raw": None,
        "filename_id_prefix": None,
        "filename_date_fragments_raw": [],
        "filename_date_parse_status": "no_date_fragment",
        "filename_date_parse_notes": [],
    }
    m = _HISTORY_LABEL_RE.match(basename)
    if not m:
        raw["filename_date_parse_status"] = "unparseable_basename"
        raw["filename_date_parse_notes"].append(
            "basename does not match <id>_<label>.<ext>"
        )
        return raw

    raw["filename_id_prefix"] = m.group("id")
    label = m.group("label")
    raw["filename_label_raw"] = label

    fragments: list[str] = []
    for fm in _FILENAME_DATE_FRAGMENT_RE.finditer(label):
        frag = fm.group(0)
        if frag not in fragments:
            fragments.append(frag)
    raw["filename_date_fragments_raw"] = fragments

    if not fragments:
        raw["filename_date_parse_status"] = "label_without_date_fragment"
        raw["filename_date_parse_notes"].append(
            "filename label present but no safe ROC date fragment recognized"
        )
    else:
        raw["filename_date_parse_status"] = "fragments_captured_raw_only"
        raw["filename_date_parse_notes"].append(
            "date fragments retained as raw text only; not legal effective dates"
        )
    return raw


def normalize_roc_dot_for_sort(fragment: str) -> str | None:
    """Normalize ROC dotted date for analysis/search only (not legal)."""
    m = re.fullmatch(r"(\d{2,3})\.(\d{1,2})\.(\d{1,2})", fragment)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mo < 1 or mo > 12 or d < 1 or d > 31:
        return None
    return f"{y:03d}.{mo:02d}.{d:02d}"


def parse_historical_release_sort_key(label_raw: str | None) -> dict[str, Any]:
    """Parse history-lane filename label into an analysis-only year/month key.

    Accepts forms such as ``96年7月版``, ``97年9月版``, ``98年版`` … ``109年版``.
    Returns a unique (year, month) sort key or a fail-closed status.
    Month defaults to 1 for ``N年版`` when no month is present (analysis only).
    """
    out: dict[str, Any] = {
        "analysis_sort_key": None,
        "roc_year": None,
        "roc_month": None,
        "parse_status": "unparsed",
        "not_legal_effective_date": True,
        "statement": (
            "analysis-only chronology candidate from filename label; "
            "NOT a legal effective date"
        ),
    }
    if not label_raw or not isinstance(label_raw, str):
        out["parse_status"] = "missing_label"
        return out

    m = _HIST_YEAR_MONTH_RE.match(label_raw)
    if m:
        y = int(m.group("y"))
        mo = int(m.group("m"))
        if mo < 1 or mo > 12 or y < 1 or y > 200:
            out["parse_status"] = "invalid_year_month"
            return out
        out["roc_year"] = y
        out["roc_month"] = mo
        out["analysis_sort_key"] = f"{y:03d}.{mo:02d}"
        out["parse_status"] = "year_month_from_label"
        return out

    m2 = _HIST_YEAR_BAN_RE.match(label_raw)
    if m2:
        y = int(m2.group("y"))
        if y < 1 or y > 200:
            out["parse_status"] = "invalid_year"
            return out
        # Year-only edition label → month=01 for stable ordering only.
        out["roc_year"] = y
        out["roc_month"] = 1
        out["analysis_sort_key"] = f"{y:03d}.01"
        out["parse_status"] = "year_only_edition_month_default_01"
        return out

    out["parse_status"] = "unrecognized_historical_label"
    return out


# ---------------------------------------------------------------------------
# ZIP / ODT / DOCX structural extraction
# ---------------------------------------------------------------------------


def inspect_zip_container(data: bytes) -> dict[str, Any]:
    """Validate ZIP container; return validity, member count, name digest."""
    out: dict[str, Any] = {
        "zip_valid": False,
        "zip_member_count": None,
        "zip_member_names_digest": None,
        "zip_error": None,
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            bad = zf.testzip()
            if bad is not None:
                out["zip_error"] = f"crc_failed:{bad}"
                return out
            names = list(zf.namelist())
            out["zip_valid"] = True
            out["zip_member_count"] = len(names)
            out["zip_member_names_digest"] = member_names_digest(names)
            return out
    except zipfile.BadZipFile as exc:
        out["zip_error"] = f"bad_zip:{exc}"
        return out
    except Exception as exc:  # noqa: BLE001 — fail-closed observation
        out["zip_error"] = f"zip_error:{type(exc).__name__}:{exc}"
        return out


def _text_content(el: ET.Element) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_text_content(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odt_attr_local(el: ET.Element, local: str) -> str | None:
    for k, v in el.attrib.items():
        if local_name(k) == local:
            return v
    return None


def _parse_repeat_attr(raw: str | None, *, what: str) -> int:
    """Parse a table repeat attribute; fail closed on invalid/negative/extreme."""
    if raw is None or raw == "":
        return 1
    try:
        n = int(raw)
    except ValueError as exc:
        raise CorpusProfileError(
            "invalid_odt_repeat",
            f"{what}={raw!r} is not an integer",
        ) from exc
    if n < 1:
        raise CorpusProfileError(
            "invalid_odt_repeat",
            f"{what}={n} is not a positive integer",
        )
    if n > ODT_REPEAT_EXPANSION_CAP:
        raise CorpusProfileError(
            "invalid_odt_repeat",
            f"{what}={n} exceeds expansion cap {ODT_REPEAT_EXPANSION_CAP}",
        )
    return n


def extract_odt_structure(data: bytes) -> dict[str, Any]:
    """Structural counts + date-only core-property metadata from ODT bytes."""
    result: dict[str, Any] = {
        "extraction_status": "ok",
        "warnings": [],
        "paragraph_count": 0,
        "table_count": 0,
        # XML element counts (direct).
        "table_row_count_xml": 0,
        "table_cell_count_xml": 0,
        # Logical counts after conservative expansion of ODT repeat attributes.
        "table_row_count": 0,
        "table_cell_count": 0,
        "odt_repeat_attrs_present": False,
        "odt_rows_repeated_attr_count": 0,
        "odt_columns_repeated_attr_count": 0,
        "non_whitespace_char_count": 0,
        "file_metadata_dates": {},
        "file_metadata_note": (
            "embedded document core-property / meta date/time fields only; "
            "NOT legal effective dates; author/title/company/generator omitted"
        ),
        "extracted_text_sample_sha256": None,
        "full_text_for_search": "",
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "content.xml" not in zf.namelist():
                result["extraction_status"] = "missing_content_xml"
                result["warnings"].append("ODT missing content.xml")
                return result
            content = zf.read("content.xml")
            root = ET.fromstring(content)
            paragraphs = 0
            tables = 0
            rows_xml = 0
            cells_xml = 0
            rows_logical = 0
            cells_logical = 0
            row_rep_attrs = 0
            col_rep_attrs = 0
            text_chunks: list[str] = []
            for el in root.iter():
                name = local_name(el.tag)
                if name == "p":
                    paragraphs += 1
                    t = _text_content(el)
                    if t:
                        text_chunks.append(t)
                elif name == "h":
                    paragraphs += 1
                    t = _text_content(el)
                    if t:
                        text_chunks.append(t)
                elif name == "table":
                    tables += 1
                elif name == "table-row":
                    rows_xml += 1
                    row_rep_raw = _odt_attr_local(el, "number-rows-repeated")
                    if row_rep_raw is not None:
                        row_rep_attrs += 1
                    row_rep = _parse_repeat_attr(
                        row_rep_raw, what="table:number-rows-repeated"
                    )
                    rows_logical += row_rep
                    # Expand cells inside this row by the row repeat.
                    for cell in el:
                        cname = local_name(cell.tag)
                        if cname not in ("table-cell", "covered-table-cell"):
                            continue
                        cells_xml += 1
                        col_rep_raw = _odt_attr_local(cell, "number-columns-repeated")
                        if col_rep_raw is not None:
                            col_rep_attrs += 1
                        col_rep = _parse_repeat_attr(
                            col_rep_raw, what="table:number-columns-repeated"
                        )
                        # Logical cells = col_rep * row_rep (conservative product).
                        product = col_rep * row_rep
                        if product > ODT_REPEAT_EXPANSION_CAP:
                            raise CorpusProfileError(
                                "invalid_odt_repeat",
                                f"logical cell expansion {product} exceeds cap "
                                f"{ODT_REPEAT_EXPANSION_CAP}",
                            )
                        cells_logical += product
                elif name in ("table-cell", "covered-table-cell"):
                    # Cells are counted inside their parent table-row above.
                    # Orphan cells (outside table-row) still contribute as 1.
                    parent = None  # counted in table-row walk via children
                    # Detect orphan: if parent is not table-row, count once.
                    # ElementTree has no parent pointer; we count only in row loop
                    # to avoid double-counting. Skip global cell iteration.
                    pass

            # Re-scan for orphan cells not under table-row is unnecessary for
            # well-formed ODT; cells under table-row were counted above.

            full_text = "\n".join(text_chunks)
            result["paragraph_count"] = paragraphs
            result["table_count"] = tables
            result["table_row_count_xml"] = rows_xml
            result["table_cell_count_xml"] = cells_xml
            result["table_row_count"] = rows_logical
            result["table_cell_count"] = cells_logical
            result["odt_rows_repeated_attr_count"] = row_rep_attrs
            result["odt_columns_repeated_attr_count"] = col_rep_attrs
            result["odt_repeat_attrs_present"] = bool(row_rep_attrs or col_rep_attrs)
            result["non_whitespace_char_count"] = sum(
                1 for ch in full_text if not ch.isspace()
            )
            result["full_text_for_search"] = full_text
            result["extracted_text_sample_sha256"] = sha256_text(full_text)

            meta_dates: dict[str, str | None] = {}
            if "meta.xml" in zf.namelist():
                meta_root = ET.fromstring(zf.read("meta.xml"))
                for el in meta_root.iter():
                    n = local_name(el.tag)
                    if n in ODT_DATE_META_NAMES:
                        meta_dates[f"meta:{n}"] = el.text
            result["file_metadata_dates"] = {
                k: meta_dates[k] for k in sorted(meta_dates)
            }
            if tables:
                result["warnings"].append(
                    "tables_present_structural_counts_only_not_flattened"
                )
            if result["odt_repeat_attrs_present"]:
                result["warnings"].append(
                    "odt_table_repeat_attributes_expanded_with_cap"
                )
            return result
    except CorpusProfileError:
        raise
    except ET.ParseError as exc:
        result["extraction_status"] = "xml_parse_error"
        result["warnings"].append(f"xml_parse_error:{exc}")
        return result
    except Exception as exc:  # noqa: BLE001
        result["extraction_status"] = "extraction_error"
        result["warnings"].append(f"extraction_error:{type(exc).__name__}:{exc}")
        return result


def extract_docx_structure(data: bytes) -> dict[str, Any]:
    """Structural counts + date-only core-property metadata from DOCX bytes.

    DOCX counts remain direct XML element counts (no ODT-style repeat attrs).
    """
    result: dict[str, Any] = {
        "extraction_status": "ok",
        "warnings": [],
        "paragraph_count": 0,
        "table_count": 0,
        "table_row_count_xml": 0,
        "table_cell_count_xml": 0,
        "table_row_count": 0,
        "table_cell_count": 0,
        "odt_repeat_attrs_present": False,
        "odt_rows_repeated_attr_count": 0,
        "odt_columns_repeated_attr_count": 0,
        "non_whitespace_char_count": 0,
        "file_metadata_dates": {},
        "file_metadata_note": (
            "embedded document core-property date/time fields only; "
            "NOT legal effective dates; author/title/company/generator omitted"
        ),
        "extracted_text_sample_sha256": None,
        "full_text_for_search": "",
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if "word/document.xml" not in names:
                result["extraction_status"] = "missing_document_xml"
                result["warnings"].append("DOCX missing word/document.xml")
                return result
            root = ET.fromstring(zf.read("word/document.xml"))
            paragraphs = 0
            tables = 0
            rows = 0
            cells = 0
            text_chunks: list[str] = []
            for el in root.iter():
                name = local_name(el.tag)
                if name == "p":
                    paragraphs += 1
                    texts = [
                        (t.text or "")
                        for t in el.iter()
                        if local_name(t.tag) == "t"
                    ]
                    joined = "".join(texts)
                    if joined:
                        text_chunks.append(joined)
                elif name == "tbl":
                    tables += 1
                elif name == "tr":
                    rows += 1
                elif name == "tc":
                    cells += 1
            full_text = "\n".join(text_chunks)
            result["paragraph_count"] = paragraphs
            result["table_count"] = tables
            result["table_row_count_xml"] = rows
            result["table_cell_count_xml"] = cells
            result["table_row_count"] = rows
            result["table_cell_count"] = cells
            result["non_whitespace_char_count"] = sum(
                1 for ch in full_text if not ch.isspace()
            )
            result["full_text_for_search"] = full_text
            result["extracted_text_sample_sha256"] = sha256_text(full_text)

            meta_dates: dict[str, str | None] = {}
            if "docProps/core.xml" in names:
                core = ET.fromstring(zf.read("docProps/core.xml"))
                for el in core.iter():
                    n = local_name(el.tag)
                    if n in DOCX_DATE_CORE_NAMES:
                        meta_dates[f"core:{n}"] = el.text
            # Intentionally do NOT read app.xml Company/Application/etc.
            result["file_metadata_dates"] = {
                k: meta_dates[k] for k in sorted(meta_dates)
            }
            if tables:
                result["warnings"].append(
                    "tables_present_structural_counts_only_not_flattened"
                )
            return result
    except ET.ParseError as exc:
        result["extraction_status"] = "xml_parse_error"
        result["warnings"].append(f"xml_parse_error:{exc}")
        return result
    except Exception as exc:  # noqa: BLE001
        result["extraction_status"] = "extraction_error"
        result["warnings"].append(f"extraction_error:{type(exc).__name__}:{exc}")
        return result


# ---------------------------------------------------------------------------
# Corpus walk
# ---------------------------------------------------------------------------


class CorpusProfileError(Exception):
    """Fatal profiling error (fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def iter_lane_files(corpus_root: Path) -> list[tuple[str, Path, str]]:
    """Return (rel_posix, absolute_path, lane) sorted by relative path bytes.

    Does not follow symlinks. Only history/ and current/ are in scope.
    """
    if not corpus_root.is_dir():
        raise CorpusProfileError("corpus_root_missing", str(corpus_root))

    found: list[tuple[str, Path, str]] = []
    seen_rel: set[str] = set()

    for lane in LANES:
        lane_dir = corpus_root / lane
        if not lane_dir.exists():
            continue
        if lane_dir.is_symlink():
            continue
        if not lane_dir.is_dir():
            raise CorpusProfileError(
                "lane_not_directory", f"{lane} is not a directory"
            )

        stack = [lane_dir]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name.encode("utf-8"))
            except OSError as exc:
                raise CorpusProfileError(
                    "unreadable_directory", f"{current}: {exc}"
                ) from exc
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                try:
                    rel = entry.relative_to(corpus_root).as_posix()
                except ValueError as exc:
                    raise CorpusProfileError(
                        "path_outside_root", str(entry)
                    ) from exc
                if rel in seen_rel:
                    raise CorpusProfileError("duplicate_relative_path", rel)
                seen_rel.add(rel)
                ext = entry.suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    raise CorpusProfileError(
                        "unsupported_extension",
                        f"{rel} has unsupported extension {ext!r}",
                    )
                found.append((rel, entry, lane))

    found.sort(key=lambda item: item[0].encode("utf-8"))
    return found


def profile_artifact(rel: str, path: Path, lane: str) -> dict[str, Any]:
    """Profile one artifact into a deterministic manifest row (+ internal text)."""
    basename = path.name
    ext = path.suffix.lower().lstrip(".")
    container_format = {"odt": "opendocument_text", "docx": "office_open_xml_word"}.get(
        ext, "unknown"
    )

    warnings: list[str] = []
    issues_local: list[dict[str, Any]] = []

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CorpusProfileError("unreadable_artifact", f"{rel}: {exc}") from exc

    byte_length = len(data)
    content_sha256 = sha256_bytes(data)

    zip_info = inspect_zip_container(data)
    if not zip_info["zip_valid"]:
        raise CorpusProfileError(
            "invalid_container",
            f"{rel}: {zip_info.get('zip_error') or 'invalid zip'}",
        )

    if ext == "odt":
        structure = extract_odt_structure(data)
    elif ext == "docx":
        structure = extract_docx_structure(data)
    else:
        raise CorpusProfileError("unsupported_extension", rel)

    warnings.extend(structure.get("warnings") or [])
    filename_info = parse_filename_label(basename)

    # Conflict candidate: embedded meta date year vs filename ROC fragment —
    # recorded as issue/evidence only, never resolved into a legal date.
    conflict_flags: list[str] = []
    meta_dates = structure.get("file_metadata_dates") or {}
    for frag in filename_info.get("filename_date_fragments_raw") or []:
        norm = normalize_roc_dot_for_sort(frag)
        if not norm:
            continue
        try:
            roc_y = int(norm.split(".")[0])
            ce_y = roc_y + 1911
        except ValueError:
            continue
        for mk, mv in meta_dates.items():
            if not mv or not isinstance(mv, str):
                continue
            iso_m = re.match(r"(\d{4})-", mv)
            if not iso_m:
                continue
            meta_y = int(iso_m.group(1))
            if abs(meta_y - ce_y) >= 2:
                conflict_flags.append(
                    f"filename_fragment={frag}; meta_field={mk}; meta_value={mv}; "
                    f"note=embedded_file_metadata_year_diverges_from_filename_label_candidate"
                )

    if conflict_flags:
        warnings.append("filename_vs_embedded_metadata_date_conflict_candidate")
        for flag in conflict_flags:
            issues_local.append(
                make_issue(
                    issue_code="filename_vs_embedded_metadata_date_conflict_candidate",
                    severity=SEVERITY_WARNING,
                    relative_path=rel,
                    detail=flag,
                    issue_class="content_ambiguity",
                )
            )

    if filename_info["filename_date_parse_status"] in (
        "unparseable_basename",
        "label_without_date_fragment",
    ):
        issues_local.append(
            make_issue(
                issue_code="filename_date_not_safely_parsed",
                severity=SEVERITY_INFO,
                relative_path=rel,
                detail=filename_info["filename_date_parse_status"],
                issue_class="content_ambiguity",
                filename_label_raw=filename_info.get("filename_label_raw"),
                notes=list(filename_info.get("filename_date_parse_notes") or []),
            )
        )

    if structure["extraction_status"] != "ok":
        issues_local.append(
            make_issue(
                issue_code="extraction_incomplete",
                severity=SEVERITY_WARNING,
                relative_path=rel,
                detail=structure["extraction_status"],
                issue_class="content_ambiguity",
            )
        )

    if structure.get("odt_repeat_attrs_present"):
        issues_local.append(
            make_issue(
                issue_code="odt_table_repeat_attributes_present",
                severity=SEVERITY_INFO,
                relative_path=rel,
                detail=(
                    f"rows_repeated_attrs={structure.get('odt_rows_repeated_attr_count')}; "
                    f"columns_repeated_attrs={structure.get('odt_columns_repeated_attr_count')}; "
                    f"table_row_count_xml={structure.get('table_row_count_xml')}; "
                    f"table_row_count_logical={structure.get('table_row_count')}; "
                    f"table_cell_count_xml={structure.get('table_cell_count_xml')}; "
                    f"table_cell_count_logical={structure.get('table_cell_count')}"
                ),
                issue_class="structural_observation",
            )
        )

    row: dict[str, Any] = {
        "relative_path": rel,
        "basename": basename,
        "lane": lane,
        "extension": ext,
        "container_format": container_format,
        "byte_length": byte_length,
        "sha256": content_sha256,
        "zip_valid": zip_info["zip_valid"],
        "zip_member_count": zip_info["zip_member_count"],
        "zip_member_names_digest": zip_info["zip_member_names_digest"],
        "filename_label_raw": filename_info["filename_label_raw"],
        "filename_id_prefix": filename_info["filename_id_prefix"],
        "filename_date_fragments_raw": filename_info["filename_date_fragments_raw"],
        "filename_date_parse_status": filename_info["filename_date_parse_status"],
        "filename_date_parse_notes": filename_info["filename_date_parse_notes"],
        "file_metadata_dates": structure["file_metadata_dates"],
        "file_metadata_note": structure["file_metadata_note"],
        "paragraph_count": structure["paragraph_count"],
        "table_count": structure["table_count"],
        "table_row_count": structure["table_row_count"],
        "table_cell_count": structure["table_cell_count"],
        "table_row_count_xml": structure["table_row_count_xml"],
        "table_cell_count_xml": structure["table_cell_count_xml"],
        "odt_repeat_attrs_present": structure.get("odt_repeat_attrs_present", False),
        "odt_rows_repeated_attr_count": structure.get(
            "odt_rows_repeated_attr_count", 0
        ),
        "odt_columns_repeated_attr_count": structure.get(
            "odt_columns_repeated_attr_count", 0
        ),
        "non_whitespace_char_count": structure["non_whitespace_char_count"],
        "extracted_text_sample_sha256": structure["extracted_text_sample_sha256"],
        "extraction_status": structure["extraction_status"],
        "warnings": sorted(set(warnings)),
        "tool_version": TOOL_VERSION,
        # Internal only — stripped before manifest emit.
        "_full_text_for_search": structure.get("full_text_for_search") or "",
        "_issues": issues_local,
    }
    return row


def strip_internal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Aggregate analysis
# ---------------------------------------------------------------------------


def find_duplicate_sha256(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_hash[row["sha256"]].append(row["relative_path"])
    dups = []
    for digest, paths in sorted(by_hash.items(), key=lambda kv: kv[0]):
        if len(paths) > 1:
            dups.append(
                {
                    "sha256": digest,
                    "paths": sorted(paths, key=lambda p: p.encode("utf-8")),
                    "count": len(paths),
                }
            )
    return dups


def historical_release_label_sequence(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """History-lane labels in stable bytewise path order (not legal chronology)."""
    seq = []
    for row in rows:
        if row["lane"] != "history":
            continue
        seq.append(
            {
                "relative_path": row["relative_path"],
                "filename_label_raw": row.get("filename_label_raw"),
                "filename_date_fragments_raw": row.get("filename_date_fragments_raw"),
                "sha256": row["sha256"],
                "byte_length": row["byte_length"],
            }
        )
    return seq


def historical_release_chronology_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build analysis-only chronology candidates sorted by year/month key.

    Returns (candidates, issues). Fails closed into issues when a history
    filename label cannot yield a unique analysis-only year/month key.
    """
    candidates: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row in rows:
        if row["lane"] != "history":
            continue
        label = row.get("filename_label_raw")
        parsed = parse_historical_release_sort_key(label)
        entry = {
            "relative_path": row["relative_path"],
            "filename_label_raw": label,
            "analysis_sort_key": parsed["analysis_sort_key"],
            "roc_year": parsed["roc_year"],
            "roc_month": parsed["roc_month"],
            "parse_status": parsed["parse_status"],
            "not_legal_effective_date": True,
            "statement": parsed["statement"],
        }
        if parsed["analysis_sort_key"] is None:
            issues.append(
                make_issue(
                    issue_code="historical_release_label_chronology_unparseable",
                    severity=SEVERITY_ERROR,
                    relative_path=row["relative_path"],
                    detail=(
                        f"label={label!r}; parse_status={parsed['parse_status']}; "
                        "cannot yield unique analysis-only year/month key"
                    ),
                    issue_class="content_ambiguity",
                )
            )
        candidates.append(entry)

    # Sort by analysis key (nulls last), then relative path bytes for stability.
    candidates.sort(
        key=lambda c: (
            c["analysis_sort_key"] is None,
            c["analysis_sort_key"] or "",
            c["relative_path"].encode("utf-8"),
        )
    )
    return candidates, issues


def compile_canary_pattern(canary: str) -> re.Pattern[str]:
    """Compile canary search pattern.

    - Latin drug names: case-insensitive literal substring match.
    - Dotted rule numbers: deterministic numeric-token boundaries so ``9.24``
      does not match ``9.240``, ``19.24``, or ``9.24.1``, while still matching
      forms such as ``9.24)`` or ``3.3.13.Agalsidase`` (trailing ``.`` + letter
      is not a longer dotted numeric suffix).
    """
    if re.search(r"[A-Za-z]", canary):
        return re.compile(re.escape(canary), re.IGNORECASE)
    # Left: not continuing a longer dotted/numeric token.
    # Right: not a longer numeric token (digit) or another dotted numeric segment
    # ('.' + digit). A bare '.' followed by a non-digit is allowed.
    return re.compile(
        r"(?<![0-9.])" + re.escape(canary) + r"(?![0-9])(?!\.[0-9])"
    )


def search_canaries(
    rows: Sequence[Mapping[str, Any]],
    canaries: Sequence[str] = DEFAULT_CANARY_STRINGS,
) -> list[dict[str, Any]]:
    """Locate canary strings in extracted text. Hits ≠ identity or legal date."""
    hits: list[dict[str, Any]] = []
    compiled = {c: compile_canary_pattern(c) for c in canaries}
    for canary in canaries:
        pattern = compiled[canary]
        for row in rows:
            text = row.get("_full_text_for_search") or ""
            if not text:
                continue
            for m in pattern.finditer(text):
                start = m.start()
                para_idx = text.count("\n", 0, start)
                ctx_lo = max(0, start - 40)
                ctx_hi = min(len(text), m.end() + 40)
                context = text[ctx_lo:ctx_hi].replace("\n", " ")
                hits.append(
                    {
                        "canary": canary,
                        "relative_path": row["relative_path"],
                        "lane": row["lane"],
                        "char_offset": start,
                        "paragraph_index_approx": para_idx,
                        "match_text": m.group(0),
                        "context_window": context,
                        "identity_claim": False,
                        "legal_date_claim": False,
                        "note": (
                            "string hit only; does not prove stable rule identity "
                            "or legal effective date"
                        ),
                    }
                )
    hits.sort(
        key=lambda h: (
            canaries.index(h["canary"]) if h["canary"] in canaries else 999,
            h["relative_path"].encode("utf-8"),
            h["char_offset"],
        )
    )
    return hits


def assess_freshness_gap(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare observed local filename date fragments to cited 115.07.23 anchor.

    Report only; never invent missing official URLs or legal dates.
    """
    current_frags: list[dict[str, Any]] = []
    for row in rows:
        if row["lane"] != "current":
            continue
        for frag in row.get("filename_date_fragments_raw") or []:
            norm = normalize_roc_dot_for_sort(frag)
            current_frags.append(
                {
                    "relative_path": row["relative_path"],
                    "fragment_raw": frag,
                    "fragment_normalized_analysis_only": norm,
                }
            )

    sortable = [
        f for f in current_frags if f["fragment_normalized_analysis_only"] is not None
    ]
    sortable.sort(
        key=lambda f: (
            f["fragment_normalized_analysis_only"],
            f["relative_path"].encode("utf-8"),
        )
    )
    max_local = sortable[-1] if sortable else None
    anchor_norm = normalize_roc_dot_for_sort(CITED_OFFICIAL_WHOLE_FILE_ANCHOR_LABEL)

    gap: dict[str, Any] = {
        "cited_official_whole_file_anchor_label": CITED_OFFICIAL_WHOLE_FILE_ANCHOR_LABEL,
        "cited_official_whole_file_anchor_source": CITED_OFFICIAL_WHOLE_FILE_ANCHOR_SOURCE,
        "anchor_normalized_analysis_only": anchor_norm,
        "observed_current_filename_date_fragment_count": len(current_frags),
        "observed_current_max_filename_date_fragment": max_local,
        "freshness_assessment": "undetermined",
        "notes": [
            "Assessment uses only plan/evaluation cited anchor plus local filename "
            "fragments. No official URL was fetched in this work unit.",
            "Filename update labels are not legal effective dates.",
            "Local corpus has per-chapter current files, not necessarily a single "
            "whole-file current ODT/DOCX matching the 115.07.23 page.",
        ],
    }

    if max_local is None or anchor_norm is None:
        gap["freshness_assessment"] = "insufficient_local_date_fragments"
        return gap

    if max_local["fragment_normalized_analysis_only"] < anchor_norm:
        gap["freshness_assessment"] = "local_current_filename_labels_older_than_cited_anchor"
        gap["notes"].append(
            "Max observed current-lane filename date fragment is strictly older "
            "than the cited official whole-file title stamp 115.07.23 — report as "
            "freshness gap; do not invent newer content."
        )
    elif max_local["fragment_normalized_analysis_only"] == anchor_norm:
        gap["freshness_assessment"] = "local_max_filename_label_matches_cited_anchor"
    else:
        gap["freshness_assessment"] = "local_filename_label_newer_than_cited_anchor"
        gap["notes"].append(
            "Local filename fragment newer than cited anchor may reflect "
            "per-chapter updates; still not proof of whole-file currency."
        )
    return gap


def conflict_counts_by_lane(issues: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"history": 0, "current": 0, "unknown": 0}
    for issue in issues:
        if issue.get("issue_code") != "filename_vs_embedded_metadata_date_conflict_candidate":
            continue
        rel = issue.get("relative_path") or ""
        if rel.startswith("history/"):
            counts["history"] += 1
        elif rel.startswith("current/"):
            counts["current"] += 1
        else:
            counts["unknown"] += 1
    return counts


def build_summary(
    rows: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    chronology_candidates: Sequence[Mapping[str, Any]],
    *,
    expected_history: int | None = 14,
    expected_current: int | None = 90,
) -> dict[str, Any]:
    by_lane = Counter(r["lane"] for r in rows)
    by_ext = Counter(r["extension"] for r in rows)
    by_status = Counter(r["extraction_status"] for r in rows)
    by_format = Counter(r["container_format"] for r in rows)
    dups = find_duplicate_sha256(rows)
    hist_seq = historical_release_label_sequence(rows)
    freshness = assess_freshness_gap(rows)
    conflicts = conflict_counts_by_lane(issues)

    observed_history = by_lane.get("history", 0)
    observed_current = by_lane.get("current", 0)

    summary: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "schema": "nhi-rule-history-corpus-profile-summary/v1",
        "observed_total": len(rows),
        "observed_by_lane": {
            "history": observed_history,
            "current": observed_current,
        },
        "expected_baseline": {
            "history": expected_history,
            "current": expected_current,
            "note": (
                "Baseline from plan/evaluation evidence (14 historical, 90 current). "
                "Observed counts are authoritative for this run; baseline is not forced."
            ),
        },
        "baseline_match": {
            "history": (
                observed_history == expected_history
                if expected_history is not None
                else None
            ),
            "current": (
                observed_current == expected_current
                if expected_current is not None
                else None
            ),
        },
        "counts_by_extension": dict(sorted(by_ext.items())),
        "counts_by_container_format": dict(sorted(by_format.items())),
        "counts_by_extraction_status": dict(sorted(by_status.items())),
        "duplicate_sha256_groups": dups,
        "duplicate_sha256_group_count": len(dups),
        "issue_count": len(issues),
        "issue_codes": dict(
            sorted(Counter(i["issue_code"] for i in issues).items())
        ),
        "issue_severity_counts": dict(
            sorted(Counter(i["severity"] for i in issues).items())
        ),
        "filename_vs_embedded_metadata_conflict_counts_by_lane": conflicts,
        "historical_release_label_sequence": hist_seq,
        "historical_release_label_count": len(hist_seq),
        "historical_release_chronology_candidates": list(chronology_candidates),
        "historical_release_chronology_candidate_count": len(chronology_candidates),
        "freshness_gap": freshness,
        "tables_present_file_count": sum(
            1 for r in rows if (r.get("table_count") or 0) > 0
        ),
        "total_paragraphs": sum(r.get("paragraph_count") or 0 for r in rows),
        "total_tables": sum(r.get("table_count") or 0 for r in rows),
        "total_non_whitespace_chars": sum(
            r.get("non_whitespace_char_count") or 0 for r in rows
        ),
        "odt_files_with_repeat_attrs": sum(
            1 for r in rows if r.get("odt_repeat_attrs_present")
        ),
        "manifest_row_count": len(rows),
        "deterministic": True,
        "canonical_rule_history_promoted": False,
        "notes": [
            "Structural profile only; no cleaned content promoted to canonical rule history.",
            "Filename labels and embedded file metadata are evidence candidates, not legal dates.",
            "Table content is counted structurally and never flattened into a false rule version.",
            "historical_release_chronology_candidates is analysis-only ordering; "
            "historical_release_label_sequence remains bytewise path order.",
            "file_metadata_dates contains only embedded date/time properties "
            "(ODT: creation-date/date/print-date; DOCX: created/modified/lastPrinted).",
        ],
    }
    return summary


def build_quality_report_md(
    summary: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
    canary_hits: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# NHI drug-payment-rule local corpus quality report")
    lines.append("")
    lines.append(f"Tool version: `{summary['tool_version']}`")
    lines.append("")
    lines.append("## Observed counts")
    lines.append("")
    lines.append(f"- Total artifacts: **{summary['observed_total']}**")
    lines.append(
        f"- History lane: **{summary['observed_by_lane']['history']}** "
        f"(baseline expectation {summary['expected_baseline']['history']}; "
        f"match={summary['baseline_match']['history']})"
    )
    lines.append(
        f"- Current lane: **{summary['observed_by_lane']['current']}** "
        f"(baseline expectation {summary['expected_baseline']['current']}; "
        f"match={summary['baseline_match']['current']})"
    )
    lines.append(
        f"- By extension: `{stable_json_dumps(summary['counts_by_extension'])}`"
    )
    lines.append(
        f"- By extraction status: "
        f"`{stable_json_dumps(summary['counts_by_extraction_status'])}`"
    )
    lines.append("")
    lines.append("## Duplicate payloads (SHA-256)")
    lines.append("")
    if summary["duplicate_sha256_group_count"] == 0:
        lines.append("- None observed.")
    else:
        for g in summary["duplicate_sha256_groups"]:
            lines.append(
                f"- `{g['sha256'][:16]}…` ×{g['count']}: "
                + ", ".join(f"`{p}`" for p in g["paths"])
            )
    lines.append("")
    lines.append("## Historical release-label sequence (bytewise path order)")
    lines.append("")
    lines.append(
        "Order is stable bytewise path order of local files, not a legal "
        "chronology. Labels are filename evidence only."
    )
    lines.append("")
    for item in summary["historical_release_label_sequence"]:
        lines.append(
            f"- `{item['relative_path']}` → label=`{item['filename_label_raw']}` "
            f"fragments={item['filename_date_fragments_raw']}"
        )
    lines.append("")
    lines.append(
        "## Historical release chronology candidates (analysis-only sort)"
    )
    lines.append("")
    lines.append(
        "Sorted by safely parsed filename year/month key (analysis only). "
        "Each row is **not** a legal effective date."
    )
    lines.append("")
    for item in summary.get("historical_release_chronology_candidates") or []:
        lines.append(
            f"- key=`{item.get('analysis_sort_key')}` "
            f"label=`{item.get('filename_label_raw')}` "
            f"path=`{item.get('relative_path')}` "
            f"— {item.get('statement')}"
        )
    lines.append("")
    lines.append("## Freshness gap vs cited official whole-file anchor")
    lines.append("")
    fg = summary["freshness_gap"]
    lines.append(
        f"- Cited anchor label (plan/evaluation evidence only): "
        f"**{fg['cited_official_whole_file_anchor_label']}**"
    )
    lines.append(f"- Source: {fg['cited_official_whole_file_anchor_source']}")
    lines.append(f"- Assessment: **{fg['freshness_assessment']}**")
    max_local = fg.get("observed_current_max_filename_date_fragment")
    if max_local:
        lines.append(
            f"- Max local current filename date fragment: "
            f"`{max_local['fragment_raw']}` in `{max_local['relative_path']}` "
            f"(analysis-normalized `{max_local['fragment_normalized_analysis_only']}`)"
        )
    else:
        lines.append("- Max local current filename date fragment: *(none parseable)*")
    for n in fg.get("notes") or []:
        lines.append(f"- Note: {n}")
    lines.append("")
    lines.append("## Canary string hits (not identity proofs)")
    lines.append("")
    lines.append(
        "A string hit does **not** prove stable rule identity or an effective date. "
        "Locators are artifact-relative. Dotted rule numbers use token boundaries."
    )
    lines.append("")
    if not canary_hits:
        lines.append("- No canary strings found in extracted local text.")
    else:
        by_c: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for h in canary_hits:
            by_c[h["canary"]].append(h)
        for canary in DEFAULT_CANARY_STRINGS:
            group = by_c.get(canary) or []
            if not group:
                lines.append(f"- `{canary}`: **0 hits**")
                continue
            lines.append(f"- `{canary}`: **{len(group)} hit(s)**")
            for h in group[:5]:
                lines.append(
                    f"  - `{h['relative_path']}` "
                    f"para≈{h['paragraph_index_approx']} "
                    f"offset={h['char_offset']}"
                )
            if len(group) > 5:
                lines.append(f"  - … {len(group) - 5} more (see canary-hits.jsonl)")
    lines.append("")
    lines.append("## Filename vs embedded metadata date conflicts by lane")
    lines.append("")
    conf = summary.get("filename_vs_embedded_metadata_conflict_counts_by_lane") or {}
    lines.append(
        f"- history: **{conf.get('history', 0)}**; "
        f"current: **{conf.get('current', 0)}**; "
        f"unknown: **{conf.get('unknown', 0)}**"
    )
    lines.append(
        "- Counts are derived from observed issue rows only; cause is limited to "
        "divergence between filename ROC fragments and embedded date/time "
        "metadata years (not author/company fields)."
    )
    lines.append("")
    lines.append("## Issues")
    lines.append("")
    lines.append(f"Total issues: **{summary['issue_count']}**")
    lines.append(f"Codes: `{stable_json_dumps(summary['issue_codes'])}`")
    lines.append(
        f"Severities: `{stable_json_dumps(summary.get('issue_severity_counts') or {})}`"
    )
    lines.append("")
    for issue in issues[:50]:
        lines.append(
            f"- [{issue.get('severity')}] `{issue['issue_code']}` "
            f"@ `{issue.get('relative_path')}` "
            f"({issue.get('issue_class')}): {issue.get('detail', '')}"
        )
    if len(issues) > 50:
        lines.append(f"- … {len(issues) - 50} more (see issues.jsonl)")
    lines.append("")
    lines.append("## Structural integrity")
    lines.append("")
    lines.append(
        f"- Files with tables: {summary['tables_present_file_count']} "
        "(counts only; content not flattened)"
    )
    lines.append(
        f"- ODT files with table repeat attributes: "
        f"{summary.get('odt_files_with_repeat_attrs', 0)} "
        "(XML element counts and expanded logical counts both recorded)"
    )
    lines.append(f"- Total paragraphs: {summary['total_paragraphs']}")
    lines.append(f"- Total tables: {summary['total_tables']}")
    lines.append(
        f"- Total non-whitespace characters: {summary['total_non_whitespace_chars']}"
    )
    lines.append(
        f"- Manifest row count equals observed total: "
        f"{summary['manifest_row_count'] == summary['observed_total']}"
    )
    lines.append("")
    lines.append("## Non-promotion statement")
    lines.append("")
    lines.append(
        "No cleaned or extracted content was promoted to canonical rule history. "
        "This utility is an immutable source manifest and structural data-quality "
        "profile only."
    )
    lines.append("")
    return "\n".join(lines)


def profile_corpus(
    corpus_root: Path,
    *,
    expected_history: int | None = 14,
    expected_current: int | None = 90,
    canaries: Sequence[str] = DEFAULT_CANARY_STRINGS,
) -> dict[str, Any]:
    """Profile corpus; return dict with manifest_rows, issues, summary, canary_hits, report_md."""
    files = iter_lane_files(corpus_root)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for rel, path, lane in files:
        row = profile_artifact(rel, path, lane)
        for issue in row.get("_issues") or []:
            issues.append(issue)
        rows.append(row)

    if len(rows) != len(files):
        raise CorpusProfileError(
            "manifest_count_mismatch",
            f"rows={len(rows)} files={len(files)}",
        )

    for g in find_duplicate_sha256(rows):
        issues.append(
            make_issue(
                issue_code="duplicate_sha256_payload",
                severity=SEVERITY_WARNING,
                relative_path=g["paths"][0],
                detail=stable_json_dumps(g),
                issue_class="content_ambiguity",
                paths=g["paths"],
                sha256=g["sha256"],
            )
        )

    chronology, chrono_issues = historical_release_chronology_candidates(rows)
    issues.extend(chrono_issues)

    canary_hits = search_canaries(rows, canaries)
    hit_canaries = {h["canary"] for h in canary_hits}
    for c in canaries:
        if c not in hit_canaries:
            issues.append(
                make_issue(
                    issue_code="canary_string_not_found_locally",
                    severity=SEVERITY_INFO,
                    relative_path=None,
                    detail=c,
                    issue_class="coverage_gap",
                )
            )

    issues.sort(key=issue_sort_key)

    manifest_rows = [strip_internal(r) for r in rows]
    if len(manifest_rows) != len(rows):
        raise CorpusProfileError(
            "manifest_count_mismatch",
            "stripped manifest length diverged",
        )

    summary = build_summary(
        rows,
        issues,
        chronology,
        expected_history=expected_history,
        expected_current=expected_current,
    )
    canary_counts = Counter(h["canary"] for h in canary_hits)
    summary["canary_hit_counts"] = {
        c: int(canary_counts.get(c, 0)) for c in canaries
    }
    summary["canary_hit_total"] = len(canary_hits)

    report_md = build_quality_report_md(summary, issues, canary_hits)

    return {
        "manifest_rows": manifest_rows,
        "issues": issues,
        "summary": summary,
        "canary_hits": canary_hits,
        "quality_report_md": report_md,
        "internal_rows": rows,
    }


def write_profile_outputs(result: Mapping[str, Any], out_dir: Path) -> dict[str, str]:
    """Write all receipts; return map of artifact name → sha256 of bytes written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest.jsonl": out_dir / "manifest.jsonl",
        "summary.json": out_dir / "summary.json",
        "issues.jsonl": out_dir / "issues.jsonl",
        "quality-report.md": out_dir / "quality-report.md",
        "canary-hits.jsonl": out_dir / "canary-hits.jsonl",
    }
    write_jsonl(paths["manifest.jsonl"], result["manifest_rows"])
    write_json(paths["summary.json"], result["summary"])
    write_jsonl(paths["issues.jsonl"], result["issues"])
    paths["quality-report.md"].write_text(
        result["quality_report_md"], encoding="utf-8"
    )
    write_jsonl(paths["canary-hits.jsonl"], result["canary_hits"])

    n_manifest = sum(
        1
        for line in paths["manifest.jsonl"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if n_manifest != result["summary"]["observed_total"]:
        raise CorpusProfileError(
            "manifest_count_mismatch",
            f"written_manifest={n_manifest} observed_total={result['summary']['observed_total']}",
        )
    if n_manifest != len(result["manifest_rows"]):
        raise CorpusProfileError(
            "manifest_count_mismatch",
            f"written_manifest={n_manifest} rows={len(result['manifest_rows'])}",
        )

    digests = {name: sha256_bytes(path.read_bytes()) for name, path in paths.items()}
    return digests


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Deterministic read-only structural profiler for local NHI "
            "drug-payment-rule history/current corpora. "
            "Invoke as: python3 corpus_profile.py or python3 run_profile.py "
            "(hyphenated directory is not a Python package import name)."
        )
    )
    p.add_argument(
        "--corpus-root",
        type=Path,
        required=True,
        help="Corpus root containing history/ and current/ subdirectories",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory for deterministic receipt outputs",
    )
    p.add_argument(
        "--expected-history",
        type=int,
        default=14,
        help="Baseline expectation for history count (reported, not forced)",
    )
    p.add_argument(
        "--expected-current",
        type=int,
        default=90,
        help="Baseline expectation for current count (reported, not forced)",
    )
    p.add_argument(
        "--print-digests",
        action="store_true",
        help="Print output file SHA-256 digests to stdout as JSON",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = profile_corpus(
            args.corpus_root.resolve(),
            expected_history=args.expected_history,
            expected_current=args.expected_current,
        )
        digests = write_profile_outputs(result, args.out_dir)
    except CorpusProfileError as exc:
        print(f"FATAL {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL unexpected: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if args.print_digests:
        print(stable_json_dumps(digests))
    else:
        print(
            stable_json_dumps(
                {
                    "status": "ok",
                    "tool_version": TOOL_VERSION,
                    "observed_total": result["summary"]["observed_total"],
                    "observed_by_lane": result["summary"]["observed_by_lane"],
                    "issue_count": result["summary"]["issue_count"],
                    "output_digests": digests,
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
