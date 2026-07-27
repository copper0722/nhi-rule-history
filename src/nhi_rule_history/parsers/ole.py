"""Deterministic typed extraction for historical OLE Compound File artifacts.

The parser first inventories the Compound File Binary (CFB) container itself.
Root-level stream names, not filename extensions, determine the candidate
Office subtype.  Legacy Word documents are converted inside a network-denied
macOS sandbox and normalized from DOCX XML.  Legacy Excel workbooks are read
with xlrd and normalized as sheet/cell observations.

All output is source-local.  No legal date, clause identity, amendment event,
effect, lineage, or history-completeness inference is made here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from nhi_rule_history.contracts import (
    ContractError,
    RAW_MANIFEST_SCHEMA,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    manifest_file_entry,
    resolve_run_relative,
    sha256_bytes,
    unique_rows,
    utc_now,
    write_json,
)
from nhi_rule_history.raw.verify import verify_raw


OLE_MANIFEST_SCHEMA = "nhi-rule-history/ole-extraction-manifest/v1"
OLE_ARTIFACT_SCHEMA = "nhi-rule-history/ole-extraction-artifact/v1"
OLE_STREAM_SCHEMA = "nhi-rule-history/ole-stream-inventory/v1"
OLE_WORD_PARAGRAPH_SCHEMA = "nhi-rule-history/ole-word-paragraph/v1"
OLE_WORD_TABLE_SCHEMA = "nhi-rule-history/ole-word-table/v1"
OLE_WORD_CELL_SCHEMA = "nhi-rule-history/ole-word-cell/v1"
OLE_WORD_PAGE_SCHEMA = "nhi-rule-history/ole-word-visual-page/v1"
OLE_EXCEL_SHEET_SCHEMA = "nhi-rule-history/ole-excel-sheet/v1"
OLE_EXCEL_CELL_SCHEMA = "nhi-rule-history/ole-excel-cell/v1"
OLE_ISSUE_SCHEMA = "nhi-rule-history/ole-extraction-issue/v1"
OLE_PARSER_VERSION = "nhi-rule-history-cfb-office/1.1.0"

WORD_TYPED_EXTRACTED = "word_typed_extracted"
EXCEL_TYPED_EXTRACTED = "excel_typed_extracted"

OLE_STAGE_FILES = (
    "ole-artifacts.jsonl",
    "ole-streams.jsonl",
    "ole-word-paragraphs.jsonl",
    "ole-word-tables.jsonl",
    "ole-word-cells.jsonl",
    "ole-word-pages.jsonl",
    "ole-excel-sheets.jsonl",
    "ole-excel-cells.jsonl",
    "ole-issues.jsonl",
)

NON_CLAIM = (
    "Source-local OLE/Office structure and text observation only; not a legal "
    "date, stable clause identity, amendment event, legal effect, current "
    "version, predecessor/successor relationship, diff, or history-completeness "
    "claim."
)

NETWORK_DENY_POLICY = "(version 1)(allow default)(deny network*)"
CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
_RESERVED_SECTORS = {FREESECT, FATSECT, DIFSECT}
_OLE_MEDIA_TYPE = "application/x-ole-storage"
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class _CFBError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _TypedExtractionError(ValueError):
    def __init__(self, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class _DirectoryEntry:
    entry_id: int
    name: str
    object_type: int
    left_id: int
    right_id: int
    child_id: int
    clsid: str
    state_bits: int
    start_sector: int
    stream_size: int


@dataclass(frozen=True)
class _BoundEntry:
    entry: _DirectoryEntry
    path: tuple[str, ...]


class _CFBContainer:
    """Strict read-only CFB sector, directory, and stream walker."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self._parse_header()
        self._parse_allocation_tables()
        self._parse_directory()

    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.payload, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.payload, offset)[0]

    def _parse_header(self) -> None:
        if len(self.payload) < 512 or self.payload[:8] != CFB_MAGIC:
            raise _CFBError("ole_magic_mismatch")
        self.minor_version = self._u16(24)
        self.major_version = self._u16(26)
        if self.major_version not in {3, 4}:
            raise _CFBError("unsupported_cfb_major_version")
        if self._u16(28) != 0xFFFE:
            raise _CFBError("invalid_cfb_byte_order")
        sector_shift = self._u16(30)
        mini_sector_shift = self._u16(32)
        expected_sector_shift = 9 if self.major_version == 3 else 12
        if sector_shift != expected_sector_shift or mini_sector_shift != 6:
            raise _CFBError("invalid_cfb_sector_shift")
        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_sector_shift
        if len(self.payload) < self.sector_size:
            raise _CFBError("truncated_cfb_header_sector")
        if len(self.payload) % self.sector_size:
            raise _CFBError("cfb_file_size_not_sector_aligned")
        self.total_sectors = len(self.payload) // self.sector_size - 1
        self.number_of_directory_sectors = self._u32(40)
        if self.major_version == 3 and self.number_of_directory_sectors != 0:
            raise _CFBError("invalid_v3_directory_sector_count")
        self.number_of_fat_sectors = self._u32(44)
        self.first_directory_sector = self._u32(48)
        self.mini_stream_cutoff = self._u32(56)
        if self.mini_stream_cutoff != 4096:
            raise _CFBError("unsupported_cfb_mini_stream_cutoff")
        self.first_minifat_sector = self._u32(60)
        self.number_of_minifat_sectors = self._u32(64)
        self.first_difat_sector = self._u32(68)
        self.number_of_difat_sectors = self._u32(72)

    def _sector(self, sector_id: int) -> bytes:
        if sector_id >= self.total_sectors:
            raise _CFBError("cfb_sector_out_of_range")
        start = (sector_id + 1) * self.sector_size
        return self.payload[start : start + self.sector_size]

    def _parse_allocation_tables(self) -> None:
        fat_sector_ids = [
            self._u32(76 + ordinal * 4)
            for ordinal in range(109)
            if self._u32(76 + ordinal * 4) != FREESECT
        ]
        difat_sector_ids: list[int] = []
        next_difat = self.first_difat_sector
        entries_per_difat = self.sector_size // 4 - 1
        for _ in range(self.number_of_difat_sectors):
            if next_difat in _RESERVED_SECTORS or next_difat == ENDOFCHAIN:
                raise _CFBError("truncated_difat_chain")
            if next_difat in difat_sector_ids:
                raise _CFBError("cyclic_difat_chain")
            difat_sector_ids.append(next_difat)
            sector = self._sector(next_difat)
            values = struct.unpack(
                f"<{self.sector_size // 4}I",
                sector,
            )
            fat_sector_ids.extend(
                value for value in values[:entries_per_difat] if value != FREESECT
            )
            next_difat = values[-1]
        if self.number_of_difat_sectors == 0:
            if self.first_difat_sector not in {ENDOFCHAIN, FREESECT}:
                raise _CFBError("unexpected_difat_start_sector")
        elif next_difat != ENDOFCHAIN:
            raise _CFBError("unterminated_difat_chain")
        if len(fat_sector_ids) != self.number_of_fat_sectors:
            raise _CFBError("fat_sector_count_mismatch")
        if len(set(fat_sector_ids)) != len(fat_sector_ids):
            raise _CFBError("duplicate_fat_sector")
        self.fat_sector_ids = tuple(fat_sector_ids)
        self.difat_sector_ids = tuple(difat_sector_ids)
        fat: list[int] = []
        for sector_id in self.fat_sector_ids:
            fat.extend(
                struct.unpack(
                    f"<{self.sector_size // 4}I",
                    self._sector(sector_id),
                )
            )
        self.fat = tuple(fat)
        for sector_id in self.fat_sector_ids:
            if sector_id >= len(self.fat) or self.fat[sector_id] != FATSECT:
                raise _CFBError("fat_sector_marker_mismatch")
        for sector_id in self.difat_sector_ids:
            if sector_id >= len(self.fat) or self.fat[sector_id] != DIFSECT:
                raise _CFBError("difat_sector_marker_mismatch")

        if self.number_of_minifat_sectors:
            minifat_chain = self._chain(
                self.first_minifat_sector,
                self.fat,
                "minifat",
            )
            if len(minifat_chain) != self.number_of_minifat_sectors:
                raise _CFBError("minifat_sector_count_mismatch")
            minifat: list[int] = []
            for sector_id in minifat_chain:
                minifat.extend(
                    struct.unpack(
                        f"<{self.sector_size // 4}I",
                        self._sector(sector_id),
                    )
                )
            self.minifat = tuple(minifat)
        else:
            if self.first_minifat_sector not in {ENDOFCHAIN, FREESECT}:
                raise _CFBError("unexpected_minifat_start_sector")
            self.minifat = ()

    def _chain(
        self,
        start_sector: int,
        allocation_table: Sequence[int],
        label: str,
    ) -> list[int]:
        if start_sector in {ENDOFCHAIN, FREESECT}:
            return []
        result: list[int] = []
        seen: set[int] = set()
        current = start_sector
        while current != ENDOFCHAIN:
            if current in _RESERVED_SECTORS:
                raise _CFBError(f"{label}_chain_reserved_sector")
            if current >= len(allocation_table):
                raise _CFBError(f"{label}_chain_out_of_range")
            if current in seen:
                raise _CFBError(f"{label}_chain_cycle")
            seen.add(current)
            result.append(current)
            if len(result) > max(self.total_sectors, len(allocation_table)):
                raise _CFBError(f"{label}_chain_unbounded")
            current = allocation_table[current]
        return result

    def _read_standard_stream(self, start_sector: int, size: int) -> bytes:
        if size == 0:
            return b""
        chain = self._chain(start_sector, self.fat, "fat")
        capacity = len(chain) * self.sector_size
        if capacity < size:
            raise _CFBError("standard_stream_truncated")
        return b"".join(self._sector(sector_id) for sector_id in chain)[:size]

    def _parse_directory(self) -> None:
        chain = self._chain(self.first_directory_sector, self.fat, "directory")
        if self.major_version == 4 and len(chain) != self.number_of_directory_sectors:
            raise _CFBError("directory_sector_count_mismatch")
        directory_bytes = b"".join(self._sector(sector_id) for sector_id in chain)
        if not directory_bytes or len(directory_bytes) % 128:
            raise _CFBError("malformed_cfb_directory_stream")
        entries: list[_DirectoryEntry | None] = []
        for entry_id in range(len(directory_bytes) // 128):
            raw = directory_bytes[entry_id * 128 : (entry_id + 1) * 128]
            object_type = raw[66]
            if object_type == 0:
                entries.append(None)
                continue
            if object_type not in {1, 2, 5}:
                raise _CFBError("unsupported_cfb_directory_object_type")
            name_length = struct.unpack_from("<H", raw, 64)[0]
            if name_length < 2 or name_length > 64 or name_length % 2:
                raise _CFBError("malformed_cfb_directory_name")
            try:
                name = raw[: name_length - 2].decode("utf-16le")
            except UnicodeDecodeError as exc:
                raise _CFBError("invalid_cfb_directory_name_utf16") from exc
            stream_size = struct.unpack_from("<Q", raw, 120)[0]
            if self.major_version == 3:
                stream_size &= 0xFFFFFFFF
            entries.append(
                _DirectoryEntry(
                    entry_id=entry_id,
                    name=name,
                    object_type=object_type,
                    left_id=struct.unpack_from("<I", raw, 68)[0],
                    right_id=struct.unpack_from("<I", raw, 72)[0],
                    child_id=struct.unpack_from("<I", raw, 76)[0],
                    clsid=raw[80:96].hex(),
                    state_bits=struct.unpack_from("<I", raw, 96)[0],
                    start_sector=struct.unpack_from("<I", raw, 116)[0],
                    stream_size=stream_size,
                )
            )
        if not entries or entries[0] is None or entries[0].object_type != 5:
            raise _CFBError("missing_cfb_root_storage")
        self.directory_entries = tuple(entries)
        self.root_entry = entries[0]
        bound: list[_BoundEntry] = []
        visited: set[int] = {0}
        visiting: set[int] = set()

        def walk_sibling_tree(entry_id: int, parent: tuple[str, ...]) -> None:
            if entry_id == NOSTREAM:
                return
            if entry_id >= len(entries) or entries[entry_id] is None:
                raise _CFBError("directory_tree_reference_invalid")
            if entry_id in visited or entry_id in visiting:
                raise _CFBError("directory_tree_cycle_or_duplicate")
            entry = entries[entry_id]
            visiting.add(entry_id)
            walk_sibling_tree(entry.left_id, parent)
            if entry_id in visited:
                raise _CFBError("directory_tree_cycle_or_duplicate")
            visited.add(entry_id)
            path = (*parent, entry.name)
            bound.append(_BoundEntry(entry=entry, path=path))
            if entry.object_type == 1:
                walk_sibling_tree(entry.child_id, path)
            elif entry.child_id != NOSTREAM:
                raise _CFBError("stream_directory_entry_has_child")
            walk_sibling_tree(entry.right_id, parent)
            visiting.remove(entry_id)

        walk_sibling_tree(self.root_entry.child_id, ())
        live_ids = {
            entry.entry_id for entry in entries if entry is not None and entry.entry_id != 0
        }
        if visited - {0} != live_ids:
            raise _CFBError("unreachable_live_directory_entry")
        self.bound_entries = tuple(sorted(bound, key=lambda item: item.entry.entry_id))
        self._mini_stream = self._read_standard_stream(
            self.root_entry.start_sector,
            self.root_entry.stream_size,
        )

    def read_stream(self, bound: _BoundEntry) -> bytes:
        entry = bound.entry
        if entry.object_type != 2:
            raise _CFBError("directory_entry_is_not_stream")
        if entry.stream_size == 0:
            return b""
        if entry.stream_size >= self.mini_stream_cutoff:
            return self._read_standard_stream(entry.start_sector, entry.stream_size)
        if not self.minifat:
            raise _CFBError("small_stream_without_minifat")
        chain = self._chain(entry.start_sector, self.minifat, "mini")
        capacity = len(chain) * self.mini_sector_size
        if capacity < entry.stream_size:
            raise _CFBError("mini_stream_truncated")
        chunks: list[bytes] = []
        for mini_sector_id in chain:
            start = mini_sector_id * self.mini_sector_size
            end = start + self.mini_sector_size
            if end > len(self._mini_stream):
                raise _CFBError("mini_stream_sector_out_of_range")
            chunks.append(self._mini_stream[start:end])
        return b"".join(chunks)[: entry.stream_size]

    @property
    def stream_entries(self) -> tuple[_BoundEntry, ...]:
        return tuple(
            bound for bound in self.bound_entries if bound.entry.object_type == 2
        )

    @property
    def storage_count(self) -> int:
        return sum(bound.entry.object_type == 1 for bound in self.bound_entries)

    def primary_office_type(self) -> str:
        root_streams = {
            bound.entry.name
            for bound in self.stream_entries
            if len(bound.path) == 1
        }
        candidates: list[str] = []
        if "WordDocument" in root_streams:
            candidates.append("word_doc")
        if {"Workbook", "Book"} & root_streams:
            candidates.append("excel_xls")
        if "PowerPoint Document" in root_streams:
            candidates.append("powerpoint_ppt")
        if {"EncryptedPackage", "EncryptionInfo"} <= root_streams:
            candidates.append("encrypted_office_package")
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return "ambiguous_office_container"
        return "unsupported_ole_container"


def _source_row_sha(row: Mapping[str, Any]) -> str:
    clean = {key: value for key, value in row.items() if key != "source_row_sha256"}
    return sha256_bytes(canonical_json_bytes(clean))


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    enriched = dict(row)
    enriched["source_row_sha256"] = _source_row_sha(enriched)
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(enriched))


def _preflight_manifest(
    run_dir: Path,
    *,
    expected_raw_manifest_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_raw_manifest_sha256):
        raise ContractError("expected_raw_manifest_sha256 must be lowercase SHA-256")
    manifest_path = run_dir / "raw-manifest.json"
    if not manifest_path.is_file():
        raise ContractError("raw-manifest.json is missing")
    actual_sha = file_sha256(manifest_path)
    if actual_sha != expected_raw_manifest_sha256:
        raise ContractError("raw-manifest.json does not match the expected sealed hash")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("raw-manifest.json is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ContractError("raw-manifest.json must be an object")
    if (
        manifest.get("schema") != RAW_MANIFEST_SCHEMA
        or manifest.get("status") != "success"
    ):
        raise ContractError("raw manifest is not a successful v2 input")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ContractError("raw manifest files must be a non-empty array")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ContractError("raw manifest file entry must be an object")
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename or filename in seen:
            raise ContractError("raw manifest filenames must be unique strings")
        resolve_run_relative(run_dir, filename)
        seen.add(filename)
    return manifest, actual_sha


def _artifact_resources(
    resources: Mapping[str, Mapping[str, Any]],
    links_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for link in iter_jsonl(links_path):
        resource = resources.get(link["resource_id"])
        if resource is None:
            raise ContractError("OLE input link references unknown resource")
        key = (link["artifact_sha256"], link["resource_id"])
        if key in seen:
            continue
        seen.add(key)
        result[link["artifact_sha256"]].append(dict(resource))
    for rows in result.values():
        rows.sort(key=lambda row: row["resource_id"])
    return result


def _resource_bindings(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "resource_id": row["resource_id"],
            "resource_kind": row["resource_kind"],
            "source_label": row["source_label"],
        }
        for row in rows
    ]


def _declared_extension(bindings: Sequence[Mapping[str, Any]]) -> str | None:
    extensions = {
        Path(str(binding["source_label"])).suffix.lower()
        for binding in bindings
        if Path(str(binding["source_label"])).suffix
    }
    if len(extensions) == 1:
        return next(iter(extensions))
    return None


def _stream_row(
    *,
    parse_run_id: str,
    artifact_sha256: str,
    bound: _BoundEntry,
    stream_payload: bytes | None,
    read_status: str,
    error_code: str | None,
) -> dict[str, Any]:
    return {
        "schema": OLE_STREAM_SCHEMA,
        "parse_run_id": parse_run_id,
        "artifact_sha256": artifact_sha256,
        "directory_entry_id": bound.entry.entry_id,
        "stream_path": list(bound.path),
        "stream_name": bound.entry.name,
        "byte_size": bound.entry.stream_size,
        "content_sha256": (
            sha256_bytes(stream_payload) if stream_payload is not None else None
        ),
        "read_status": read_status,
        "error_code": error_code,
        "locator": {
            "artifact_sha256": artifact_sha256,
            "directory_entry_id": bound.entry.entry_id,
            "stream_path": list(bound.path),
        },
        "statement": NON_CLAIM,
    }


def _stream_inventory(
    *,
    parse_run_id: str,
    artifact_sha256: str,
    container: _CFBContainer,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for bound in container.stream_entries:
        try:
            payload = container.read_stream(bound)
            row = _stream_row(
                parse_run_id=parse_run_id,
                artifact_sha256=artifact_sha256,
                bound=bound,
                stream_payload=payload,
                read_status="read",
                error_code=None,
            )
        except _CFBError as exc:
            failures.append(exc.code)
            row = _stream_row(
                parse_run_id=parse_run_id,
                artifact_sha256=artifact_sha256,
                bound=bound,
                stream_payload=None,
                read_status="needs_stream_read_review",
                error_code=exc.code,
            )
        rows.append(row)
    return rows, sorted(set(failures))


def _inventory_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json_bytes(dict(row)))
    return digest.hexdigest()


def _resolve_soffice() -> Path | None:
    launcher = shutil.which("soffice")
    if not launcher:
        return None
    launcher_path = Path(launcher)
    runtime_native = (
        launcher_path.parents[2]
        / "native"
        / "libreoffice-headless"
        / "libreoffice"
        / "LibreOfficeDev.app"
        / "Contents"
        / "MacOS"
        / "soffice"
    )
    if runtime_native.is_file():
        return runtime_native
    resolved = launcher_path.resolve()
    return resolved if resolved.is_file() else None


def _resolve_pdfinfo() -> Path | None:
    executable = shutil.which("pdfinfo")
    if not executable:
        return None
    resolved = Path(executable).resolve()
    return resolved if resolved.is_file() else None


def _run(
    arguments: Sequence[str],
    *,
    timeout_seconds: int,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(arguments),
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise _TypedExtractionError(
            "needs_tool_timeout_review",
            {"tool": Path(arguments[0]).name},
        ) from exc
    except OSError as exc:
        raise _TypedExtractionError(
            "needs_tool_execution_review",
            {
                "tool": Path(arguments[0]).name,
                "error_type": type(exc).__name__,
            },
        ) from exc


def _tool_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "NO_PROXY": "*",
        "PATH": os.defpath,
        "TZ": "UTC",
    }


def _soffice_receipt(soffice: Path | None) -> dict[str, Any]:
    if soffice is None:
        return {"available": False}
    with tempfile.TemporaryDirectory(prefix="nhi-ole-tool-version-") as temporary:
        completed = _run(
            [str(soffice), "--version"],
            timeout_seconds=30,
            environment=_tool_environment(Path(temporary)),
        )
    if completed.returncode != 0:
        return {
            "available": False,
            "version_check_return_code": completed.returncode,
        }
    version = (completed.stdout + completed.stderr).decode(
        "utf-8", errors="replace"
    )
    first_line = next(
        (line.strip() for line in version.splitlines() if line.strip()),
        "",
    )
    return {
        "available": bool(first_line),
        "version": first_line,
        "executable_sha256": file_sha256(soffice),
    }


def _sandbox_receipt(sandbox_exec: Path | None) -> dict[str, Any]:
    if sandbox_exec is None:
        return {"available": False}
    return {
        "available": True,
        "executable_sha256": file_sha256(sandbox_exec),
        "network_deny_policy_sha256": sha256_bytes(
            NETWORK_DENY_POLICY.encode("utf-8")
        ),
    }


def _pdfinfo_receipt(pdfinfo: Path | None) -> dict[str, Any]:
    if pdfinfo is None:
        return {"available": False}
    with tempfile.TemporaryDirectory(prefix="nhi-ole-pdfinfo-version-") as temporary:
        completed = _run(
            [str(pdfinfo), "-v"],
            timeout_seconds=30,
            environment=_tool_environment(Path(temporary)),
        )
    version = (completed.stdout + completed.stderr).decode(
        "utf-8", errors="replace"
    )
    first_line = next(
        (line.strip() for line in version.splitlines() if line.strip()),
        "",
    )
    return {
        "available": completed.returncode == 0 and bool(first_line),
        "version": first_line,
        "executable_sha256": file_sha256(pdfinfo),
    }


def _xlrd_receipt() -> tuple[Any | None, dict[str, Any]]:
    try:
        import xlrd  # type: ignore[import-not-found]
    except ImportError:
        return None, {"available": False}
    package_root = Path(xlrd.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return xlrd, {
        "available": True,
        "version": str(xlrd.__version__),
        "package_python_sha256": digest.hexdigest(),
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _word_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        name = _local_name(element.tag)
        if name in {"t", "delText", "instrText"}:
            parts.append(element.text or "")
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
        elif name == "noBreakHyphen":
            parts.append("-")
        elif name == "softHyphen":
            parts.append("\u00ad")
    return "".join(parts)


def _parse_docx(
    path: Path,
    *,
    artifact_sha256: str,
    parse_run_id: str,
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise _TypedExtractionError("needs_duplicate_docx_member_review")
            for name in names:
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise _TypedExtractionError("needs_docx_path_escape_review")
            if "word/document.xml" not in names:
                raise _TypedExtractionError("needs_missing_word_document_xml_review")
            document_xml = archive.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise _TypedExtractionError("needs_malformed_docx_review") from exc
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise _TypedExtractionError("needs_malformed_word_xml_review") from exc
    bodies = [
        child for child in root if child.tag == f"{{{_WORD_NAMESPACE}}}body"
    ]
    if len(bodies) != 1:
        raise _TypedExtractionError("needs_word_body_structure_review")

    paragraphs: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    drawing_count = sum(
        _local_name(element.tag) in {"drawing", "pict", "object"}
        for element in root.iter()
    )
    paragraph_ordinal = 0
    table_ordinal = 0

    def emit_paragraph(
        paragraph: ET.Element,
        *,
        container_path: list[dict[str, int]],
        block_index: int,
    ) -> str:
        nonlocal paragraph_ordinal
        paragraph_ordinal += 1
        text = _word_paragraph_text(paragraph)
        explicit_page_breaks = sum(
            _local_name(element.tag) == "br"
            and element.get(f"{{{_WORD_NAMESPACE}}}type") == "page"
            for element in paragraph.iter()
        )
        paragraphs.append(
            {
                "schema": OLE_WORD_PARAGRAPH_SCHEMA,
                "parse_run_id": parse_run_id,
                "artifact_sha256": artifact_sha256,
                "paragraph_ordinal": paragraph_ordinal,
                "container_path": container_path,
                "block_index": block_index,
                "text": text,
                "explicit_page_breaks": explicit_page_breaks,
                "locator": {
                    "artifact_sha256": artifact_sha256,
                    "container_path": container_path,
                    "block_index": block_index,
                    "paragraph_ordinal": paragraph_ordinal,
                },
                "statement": NON_CLAIM,
            }
        )
        return text

    def emit_table(
        table: ET.Element,
        *,
        container_path: list[dict[str, int]],
        block_index: int,
    ) -> str:
        nonlocal table_ordinal
        table_ordinal += 1
        this_table = table_ordinal
        table_locator = [
            *container_path,
            {"table_ordinal": this_table, "block_index": block_index},
        ]
        row_elements = [
            child for child in table if _local_name(child.tag) == "tr"
        ]
        table_text_rows: list[str] = []
        cell_count = 0
        for row_index, row in enumerate(row_elements, 1):
            cell_elements = [
                child for child in row if _local_name(child.tag) == "tc"
            ]
            row_text: list[str] = []
            for cell_index, cell in enumerate(cell_elements, 1):
                cell_count += 1
                cell_path = [
                    *table_locator,
                    {"row_index": row_index, "cell_index": cell_index},
                ]
                cell_blocks: list[str] = []
                cell_block_index = 0
                for child in cell:
                    child_name = _local_name(child.tag)
                    if child_name == "p":
                        cell_block_index += 1
                        cell_blocks.append(
                            emit_paragraph(
                                child,
                                container_path=cell_path,
                                block_index=cell_block_index,
                            )
                        )
                    elif child_name == "tbl":
                        cell_block_index += 1
                        cell_blocks.append(
                            emit_table(
                                child,
                                container_path=cell_path,
                                block_index=cell_block_index,
                            )
                        )
                cell_text = "\n".join(cell_blocks)
                cells.append(
                    {
                        "schema": OLE_WORD_CELL_SCHEMA,
                        "parse_run_id": parse_run_id,
                        "artifact_sha256": artifact_sha256,
                        "table_ordinal": this_table,
                        "row_index": row_index,
                        "cell_index": cell_index,
                        "container_path": container_path,
                        "text": cell_text,
                        "locator": {
                            "artifact_sha256": artifact_sha256,
                            "table_ordinal": this_table,
                            "row_index": row_index,
                            "cell_index": cell_index,
                        },
                        "statement": NON_CLAIM,
                    }
                )
                row_text.append(cell_text)
            table_text_rows.append("\t".join(row_text))
        table_text = "\n".join(table_text_rows)
        tables.append(
            {
                "schema": OLE_WORD_TABLE_SCHEMA,
                "parse_run_id": parse_run_id,
                "artifact_sha256": artifact_sha256,
                "table_ordinal": this_table,
                "container_path": container_path,
                "block_index": block_index,
                "row_count": len(row_elements),
                "cell_count": cell_count,
                "text": table_text,
                "locator": {
                    "artifact_sha256": artifact_sha256,
                    "table_ordinal": this_table,
                    "container_path": container_path,
                    "block_index": block_index,
                },
                "statement": NON_CLAIM,
            }
        )
        return table_text

    body = bodies[0]
    body_text: list[str] = []
    block_index = 0
    unsupported_body_elements: Counter[str] = Counter()
    for child in body:
        name = _local_name(child.tag)
        if name == "p":
            block_index += 1
            body_text.append(
                emit_paragraph(child, container_path=[], block_index=block_index)
            )
        elif name == "tbl":
            block_index += 1
            body_text.append(
                emit_table(child, container_path=[], block_index=block_index)
            )
        elif name != "sectPr":
            unsupported_body_elements[name] += 1
    if unsupported_body_elements:
        raise _TypedExtractionError(
            "needs_unsupported_word_body_element_review",
            {"elements": dict(sorted(unsupported_body_elements.items()))},
        )
    text = "\n".join(body_text)
    return {
        "paragraphs": paragraphs,
        "tables": tables,
        "cells": cells,
        "text": text,
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "document_xml_sha256": sha256_bytes(document_xml),
        "drawing_count": drawing_count,
        "body_block_count": block_index,
    }


def _parse_pdfinfo_page_count(payload: bytes) -> int:
    text = payload.decode("utf-8", errors="replace")
    matches = re.findall(r"(?m)^Pages:\s*([0-9]+)\s*$", text)
    if len(matches) != 1:
        raise _TypedExtractionError(
            "needs_word_visual_page_count_review",
            {"pages_field_count": len(matches)},
        )
    page_count = int(matches[0])
    if page_count <= 0:
        raise _TypedExtractionError(
            "needs_word_visual_page_count_review",
            {"page_count": page_count},
        )
    return page_count


def _render_word_visual_pages(
    *,
    input_path: Path,
    output_dir: Path,
    profile_dir: Path,
    home_dir: Path,
    soffice: Path,
    sandbox_exec: Path,
    pdfinfo: Path | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    if pdfinfo is None:
        raise _TypedExtractionError("needs_pdfinfo_page_locator_tool")
    conversion_argv = [
        str(sandbox_exec),
        "-p",
        NETWORK_DENY_POLICY,
        str(soffice),
        "--headless",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(output_dir),
        str(input_path),
    ]
    completed = _run(
        conversion_argv,
        timeout_seconds=timeout_seconds,
        environment=_tool_environment(home_dir),
    )
    if completed.returncode != 0:
        raise _TypedExtractionError(
            "needs_word_visual_page_render_review",
            {
                "return_code": completed.returncode,
                "stdout_sha256": sha256_bytes(completed.stdout),
                "stderr_sha256": sha256_bytes(completed.stderr),
            },
        )
    outputs = sorted(output_dir.glob("*.pdf"))
    if len(outputs) != 1:
        raise _TypedExtractionError(
            "needs_word_visual_page_render_output_review",
            {"pdf_output_count": len(outputs)},
        )
    pdf_path = outputs[0]
    info_argv = [
        str(sandbox_exec),
        "-p",
        NETWORK_DENY_POLICY,
        str(pdfinfo),
        "-enc",
        "UTF-8",
        str(pdf_path),
    ]
    info = _run(
        info_argv,
        timeout_seconds=timeout_seconds,
        environment=_tool_environment(home_dir),
    )
    if info.returncode != 0:
        raise _TypedExtractionError(
            "needs_word_visual_page_count_review",
            {
                "return_code": info.returncode,
                "stdout_sha256": sha256_bytes(info.stdout),
                "stderr_sha256": sha256_bytes(info.stderr),
            },
        )
    page_count = _parse_pdfinfo_page_count(info.stdout)
    return {
        "page_count": page_count,
        "rendered_pdf_sha256": file_sha256(pdf_path),
        "conversion_receipt": {
            "argv_template": [
                "sandbox-exec",
                "-p",
                "<network-deny-policy>",
                "soffice",
                "--headless",
                "--invisible",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                "-env:UserInstallation=<isolated-profile-uri>",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                "<isolated-output-dir>",
                "<verified-artifact-copy.doc>",
            ],
            "return_code": completed.returncode,
            "stdout_sha256": sha256_bytes(completed.stdout),
            "stderr_sha256": sha256_bytes(completed.stderr),
        },
        "page_count_receipt": {
            "argv_template": [
                "sandbox-exec",
                "-p",
                "<network-deny-policy>",
                "pdfinfo",
                "-enc",
                "UTF-8",
                "<rendered.pdf>",
            ],
            "return_code": info.returncode,
            "stdout_sha256": sha256_bytes(info.stdout),
            "stderr_sha256": sha256_bytes(info.stderr),
        },
    }


def _extract_word(
    blob_path: Path,
    *,
    artifact_sha256: str,
    parse_run_id: str,
    temporary_dir: Path,
    soffice: Path | None,
    sandbox_exec: Path | None,
    pdfinfo: Path | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    if soffice is None:
        raise _TypedExtractionError("needs_libreoffice_tool")
    if sandbox_exec is None:
        raise _TypedExtractionError("needs_network_sandbox_tool")
    artifact_dir = temporary_dir / artifact_sha256
    input_dir = artifact_dir / "input"
    output_dir = artifact_dir / "output"
    profile_dir = artifact_dir / "profile"
    home_dir = artifact_dir / "home"
    for directory in (input_dir, output_dir, profile_dir, home_dir):
        directory.mkdir(parents=True)
    input_path = input_dir / "source.doc"
    shutil.copyfile(blob_path, input_path)
    argv = [
        str(sandbox_exec),
        "-p",
        NETWORK_DENY_POLICY,
        str(soffice),
        "--headless",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        "docx:Office Open XML Text",
        "--outdir",
        str(output_dir),
        str(input_path),
    ]
    completed = _run(
        argv,
        timeout_seconds=timeout_seconds,
        environment=_tool_environment(home_dir),
    )
    if completed.returncode != 0:
        raise _TypedExtractionError(
            "needs_word_conversion_review",
            {
                "return_code": completed.returncode,
                "stdout_sha256": sha256_bytes(completed.stdout),
                "stderr_sha256": sha256_bytes(completed.stderr),
            },
        )
    outputs = sorted(output_dir.glob("*.docx"))
    if len(outputs) != 1:
        raise _TypedExtractionError(
            "needs_word_conversion_output_review",
            {"docx_output_count": len(outputs)},
        )
    result = _parse_docx(
        outputs[0],
        artifact_sha256=artifact_sha256,
        parse_run_id=parse_run_id,
    )
    result["conversion_receipt"] = {
        "argv_template": [
            "sandbox-exec",
            "-p",
            "<network-deny-policy>",
            "soffice",
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            "-env:UserInstallation=<isolated-profile-uri>",
            "--convert-to",
            "docx:Office Open XML Text",
            "--outdir",
            "<isolated-output-dir>",
            "<verified-artifact-copy.doc>",
        ],
        "return_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout),
        "stderr_sha256": sha256_bytes(completed.stderr),
    }
    if not result["text"].strip():
        result["visual_page_inventory"] = _render_word_visual_pages(
            input_path=input_path,
            output_dir=output_dir,
            profile_dir=profile_dir,
            home_dir=home_dir,
            soffice=soffice,
            sandbox_exec=sandbox_exec,
            pdfinfo=pdfinfo,
            timeout_seconds=timeout_seconds,
        )
    else:
        result["visual_page_inventory"] = None
    return result


def _word_visual_page_rows(
    *,
    parse_run_id: str,
    artifact_sha256: str,
    page_count: int,
    rendered_pdf_sha256: str,
    content_status: str,
) -> list[dict[str, Any]]:
    return [
        {
            "schema": OLE_WORD_PAGE_SCHEMA,
            "parse_run_id": parse_run_id,
            "artifact_sha256": artifact_sha256,
            "page_number": page_number,
            "page_count": page_count,
            "rendered_pdf_sha256": rendered_pdf_sha256,
            "content_status": content_status,
            "locator": {
                "artifact_sha256": artifact_sha256,
                "rendered_pdf_sha256": rendered_pdf_sha256,
                "page_number": page_number,
            },
            "statement": NON_CLAIM,
        }
        for page_number in range(1, page_count + 1)
    ]


def _json_cell_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _TypedExtractionError("needs_nonfinite_excel_cell_review")
        return value
    return str(value)


def _extract_excel(
    payload: bytes,
    *,
    artifact_sha256: str,
    parse_run_id: str,
    xlrd: Any | None,
) -> dict[str, Any]:
    if xlrd is None:
        raise _TypedExtractionError("needs_xlrd_library")
    try:
        workbook = xlrd.open_workbook(
            file_contents=payload,
            on_demand=True,
            formatting_info=False,
            ragged_rows=True,
        )
    except Exception as exc:
        raise _TypedExtractionError(
            "needs_excel_parse_review",
            {"error_type": type(exc).__name__},
        ) from exc
    type_names = {
        xlrd.XL_CELL_EMPTY: "empty",
        xlrd.XL_CELL_TEXT: "text",
        xlrd.XL_CELL_NUMBER: "number",
        xlrd.XL_CELL_DATE: "date_serial",
        xlrd.XL_CELL_BOOLEAN: "boolean",
        xlrd.XL_CELL_ERROR: "error",
        xlrd.XL_CELL_BLANK: "blank",
    }
    sheets: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    text_parts: list[str] = []
    try:
        for sheet_index in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_index)
            merged_ranges = [
                {
                    "row_start": row_start + 1,
                    "row_end_exclusive": row_end + 1,
                    "column_start": column_start + 1,
                    "column_end_exclusive": column_end + 1,
                }
                for row_start, row_end, column_start, column_end in sheet.merged_cells
            ]
            sheets.append(
                {
                    "schema": OLE_EXCEL_SHEET_SCHEMA,
                    "parse_run_id": parse_run_id,
                    "artifact_sha256": artifact_sha256,
                    "sheet_index": sheet_index + 1,
                    "sheet_name": sheet.name,
                    "visibility": int(getattr(sheet, "visibility", 0)),
                    "row_count": sheet.nrows,
                    "column_count": sheet.ncols,
                    "merged_ranges": merged_ranges,
                    "locator": {
                        "artifact_sha256": artifact_sha256,
                        "sheet_index": sheet_index + 1,
                        "sheet_name": sheet.name,
                    },
                    "statement": NON_CLAIM,
                }
            )
            for row_index in range(sheet.nrows):
                for column_index in range(sheet.row_len(row_index)):
                    cell = sheet.cell(row_index, column_index)
                    if cell.ctype == xlrd.XL_CELL_EMPTY:
                        continue
                    value = _json_cell_value(cell.value)
                    error_text = None
                    if cell.ctype == xlrd.XL_CELL_ERROR:
                        error_text = xlrd.error_text_from_code.get(cell.value)
                    cells.append(
                        {
                            "schema": OLE_EXCEL_CELL_SCHEMA,
                            "parse_run_id": parse_run_id,
                            "artifact_sha256": artifact_sha256,
                            "sheet_index": sheet_index + 1,
                            "sheet_name": sheet.name,
                            "row_index": row_index + 1,
                            "column_index": column_index + 1,
                            "cell_type": type_names.get(
                                cell.ctype, f"unknown_{cell.ctype}"
                            ),
                            "value": value,
                            "error_text": error_text,
                            "date_system": (
                                int(workbook.datemode)
                                if cell.ctype == xlrd.XL_CELL_DATE
                                else None
                            ),
                            "locator": {
                                "artifact_sha256": artifact_sha256,
                                "sheet_index": sheet_index + 1,
                                "row_index": row_index + 1,
                                "column_index": column_index + 1,
                            },
                            "statement": NON_CLAIM,
                        }
                    )
                    if isinstance(value, str):
                        text_parts.append(value)
                    elif value is not None:
                        text_parts.append(str(value))
    finally:
        workbook.release_resources()
    text = "\n".join(text_parts)
    return {
        "sheets": sheets,
        "cells": cells,
        "text": text,
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "date_system": int(workbook.datemode),
    }


def _issue_row(
    *,
    parse_run_id: str,
    artifact_sha256: str,
    issue_code: str,
    resource_bindings: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parameters = {
        "resource_ids": [binding["resource_id"] for binding in resource_bindings],
        "source_labels": [binding["source_label"] for binding in resource_bindings],
        **dict(details or {}),
    }
    return {
        "schema": OLE_ISSUE_SCHEMA,
        "parse_run_id": parse_run_id,
        "issue_id": sha256_bytes(
            canonical_json_bytes(
                [parse_run_id, artifact_sha256, issue_code, parameters]
            )
        ),
        "artifact_sha256": artifact_sha256,
        "issue_code": issue_code,
        "severity": "warning",
        "blocking_typed_extraction": True,
        "message_parameters": parameters,
        "statement": NON_CLAIM,
    }


def _write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    for row in rows:
        _append_row(path, row)
        count += 1
    return count


def parse_verified_ole_run(
    run_dir: Path,
    stage_dir: Path,
    *,
    parse_run_id: str,
    expected_raw_manifest_sha256: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Inventory and typed-extract the exact OLE denominator in one raw run."""

    try:
        uuid.UUID(parse_run_id)
    except ValueError as exc:
        raise ContractError("parse_run_id must be a UUID") from exc
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ContractError("timeout_seconds must be a positive integer")
    raw_manifest, raw_manifest_sha = _preflight_manifest(
        run_dir,
        expected_raw_manifest_sha256=expected_raw_manifest_sha256,
    )
    raw_verification = verify_raw(run_dir)
    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    by_artifact = _artifact_resources(
        resources,
        run_dir / "resource-artifact-links.jsonl",
    )
    soffice = _resolve_soffice()
    pdfinfo = _resolve_pdfinfo()
    sandbox_exec = (
        Path("/usr/bin/sandbox-exec")
        if Path("/usr/bin/sandbox-exec").is_file()
        else None
    )
    xlrd, xlrd_tool = _xlrd_receipt()
    tools = {
        "libreoffice": _soffice_receipt(soffice),
        "pdfinfo": _pdfinfo_receipt(pdfinfo),
        "sandbox_exec": _sandbox_receipt(sandbox_exec),
        "xlrd": xlrd_tool,
    }
    parser_bundle_sha256 = file_sha256(Path(__file__).resolve())
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "raw_manifest_sha256": raw_manifest_sha,
                "parser_version": OLE_PARSER_VERSION,
                "parser_bundle_sha256": parser_bundle_sha256,
                "tools": tools,
                "network_deny_policy": NETWORK_DENY_POLICY,
                "statement": NON_CLAIM,
            }
        )
    )

    stage_dir.mkdir(parents=True, exist_ok=True)
    if any((stage_dir / filename).exists() for filename in OLE_STAGE_FILES):
        raise ContractError("OLE stage files already exist; use a fresh stage_dir")
    if (stage_dir / "ole-manifest.json").exists():
        raise ContractError("OLE stage manifest already exists; use a fresh stage_dir")
    for filename in OLE_STAGE_FILES:
        (stage_dir / filename).touch()

    counts: Counter[str] = Counter(
        {
            "declared_ole_resources": 0,
            "declared_ole_artifacts": 0,
            "word_candidate_artifacts": 0,
            "excel_candidate_artifacts": 0,
            "typed_extracted_artifacts": 0,
            "needs_review_artifacts": 0,
            "streams": 0,
            "storages": 0,
            "word_paragraphs": 0,
            "word_tables": 0,
            "word_cells": 0,
            "word_visual_pages": 0,
            "excel_sheets": 0,
            "excel_cells": 0,
            "issues": 0,
        }
    )
    subtype_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    started_at = utc_now()

    with tempfile.TemporaryDirectory(prefix=".ole-office-", dir=stage_dir) as temp:
        temporary_dir = Path(temp)
        for artifact_sha256 in sorted(artifacts):
            artifact = artifacts[artifact_sha256]
            if artifact.get("media_type") != _OLE_MEDIA_TYPE:
                continue
            resource_rows = by_artifact.get(artifact_sha256, [])
            bindings = _resource_bindings(resource_rows)
            counts["declared_ole_artifacts"] += 1
            counts["declared_ole_resources"] += len(bindings)
            blob_path = resolve_run_relative(run_dir, artifact["content_path"])
            payload = blob_path.read_bytes()
            classification = "needs_unclassified_ole_review"
            primary_type = "unclassified"
            issue_codes: list[str] = []
            written_issue_codes: set[str] = set()
            stream_rows: list[dict[str, Any]] = []
            stream_inventory_sha256: str | None = None
            container_receipt: dict[str, Any] | None = None
            extraction_receipt: dict[str, Any] | None = None
            extracted_text_sha256: str | None = None
            artifact_counts = {
                "streams": 0,
                "word_paragraphs": 0,
                "word_tables": 0,
                "word_cells": 0,
                "word_visual_pages": 0,
                "excel_sheets": 0,
                "excel_cells": 0,
            }
            try:
                if not bindings:
                    raise _TypedExtractionError(
                        "needs_missing_resource_binding_review"
                    )
                if payload[:8] != CFB_MAGIC:
                    raise _CFBError("ole_magic_mismatch")
                container = _CFBContainer(payload)
                primary_type = container.primary_office_type()
                subtype_counts[primary_type] += 1
                if primary_type == "word_doc":
                    counts["word_candidate_artifacts"] += 1
                elif primary_type == "excel_xls":
                    counts["excel_candidate_artifacts"] += 1
                stream_rows, stream_failures = _stream_inventory(
                    parse_run_id=parse_run_id,
                    artifact_sha256=artifact_sha256,
                    container=container,
                )
                stream_inventory_sha256 = _inventory_sha256(stream_rows)
                artifact_counts["streams"] = len(stream_rows)
                counts["streams"] += len(stream_rows)
                counts["storages"] += container.storage_count
                container_receipt = {
                    "cfb_major_version": container.major_version,
                    "cfb_minor_version": container.minor_version,
                    "sector_size": container.sector_size,
                    "mini_sector_size": container.mini_sector_size,
                    "stream_count": len(stream_rows),
                    "storage_count": container.storage_count,
                }
                _write_rows(stage_dir / "ole-streams.jsonl", stream_rows)
                if stream_failures:
                    raise _TypedExtractionError(
                        "needs_corrupt_stream_review",
                        {"stream_error_codes": stream_failures},
                    )
                declared_extension = _declared_extension(bindings)
                expected_extension = {
                    "word_doc": ".doc",
                    "excel_xls": ".xls",
                }.get(primary_type)
                if (
                    expected_extension is not None
                    and declared_extension != expected_extension
                ):
                    raise _TypedExtractionError(
                        "needs_label_type_mismatch_review",
                        {
                            "declared_extension": declared_extension,
                            "container_type": primary_type,
                        },
                    )
                if primary_type == "word_doc":
                    result = _extract_word(
                        blob_path,
                        artifact_sha256=artifact_sha256,
                        parse_run_id=parse_run_id,
                        temporary_dir=temporary_dir,
                        soffice=soffice,
                        sandbox_exec=sandbox_exec,
                        pdfinfo=pdfinfo,
                        timeout_seconds=timeout_seconds,
                    )
                    artifact_counts["word_paragraphs"] = _write_rows(
                        stage_dir / "ole-word-paragraphs.jsonl",
                        result["paragraphs"],
                    )
                    artifact_counts["word_tables"] = _write_rows(
                        stage_dir / "ole-word-tables.jsonl",
                        result["tables"],
                    )
                    artifact_counts["word_cells"] = _write_rows(
                        stage_dir / "ole-word-cells.jsonl",
                        result["cells"],
                    )
                    counts["word_paragraphs"] += artifact_counts["word_paragraphs"]
                    counts["word_tables"] += artifact_counts["word_tables"]
                    counts["word_cells"] += artifact_counts["word_cells"]
                    extracted_text_sha256 = result["text_sha256"]
                    visual_page_inventory = result.get("visual_page_inventory")
                    page_locator_status = (
                        "rendered_visual_review_pages"
                        if visual_page_inventory is not None
                        else "not_rendered_layout_dependent"
                    )
                    extraction_receipt = {
                        "document_xml_sha256": result["document_xml_sha256"],
                        "drawing_count": result["drawing_count"],
                        "body_block_count": result["body_block_count"],
                        "page_locator_status": page_locator_status,
                        "visual_page_inventory": visual_page_inventory,
                        "conversion": result["conversion_receipt"],
                    }
                    if not result["text"].strip():
                        issue_code = (
                            "needs_image_ocr_or_visual_review"
                            if result["drawing_count"]
                            else "needs_zero_word_content_review"
                        )
                        if visual_page_inventory is None:
                            raise _TypedExtractionError(
                                "needs_word_visual_page_inventory_review"
                            )
                        page_rows = _word_visual_page_rows(
                            parse_run_id=parse_run_id,
                            artifact_sha256=artifact_sha256,
                            page_count=visual_page_inventory["page_count"],
                            rendered_pdf_sha256=visual_page_inventory[
                                "rendered_pdf_sha256"
                            ],
                            content_status=issue_code,
                        )
                        artifact_counts["word_visual_pages"] = _write_rows(
                            stage_dir / "ole-word-pages.jsonl",
                            page_rows,
                        )
                        counts["word_visual_pages"] += artifact_counts[
                            "word_visual_pages"
                        ]
                        raise _TypedExtractionError(issue_code)
                    classification = WORD_TYPED_EXTRACTED
                elif primary_type == "excel_xls":
                    result = _extract_excel(
                        payload,
                        artifact_sha256=artifact_sha256,
                        parse_run_id=parse_run_id,
                        xlrd=xlrd,
                    )
                    artifact_counts["excel_sheets"] = _write_rows(
                        stage_dir / "ole-excel-sheets.jsonl",
                        result["sheets"],
                    )
                    artifact_counts["excel_cells"] = _write_rows(
                        stage_dir / "ole-excel-cells.jsonl",
                        result["cells"],
                    )
                    counts["excel_sheets"] += artifact_counts["excel_sheets"]
                    counts["excel_cells"] += artifact_counts["excel_cells"]
                    extracted_text_sha256 = result["text_sha256"]
                    extraction_receipt = {
                        "date_system": result["date_system"],
                        "formula_expression_status": (
                            "not_exposed_by_xlrd_cached_value_reader"
                        ),
                        "page_locator_status": "not_intrinsic_to_biff_sheet_cells",
                    }
                    if not result["cells"]:
                        raise _TypedExtractionError(
                            "needs_zero_excel_content_review"
                        )
                    classification = EXCEL_TYPED_EXTRACTED
                elif primary_type == "powerpoint_ppt":
                    raise _TypedExtractionError(
                        "needs_powerpoint_typed_parser"
                    )
                elif primary_type == "encrypted_office_package":
                    raise _TypedExtractionError(
                        "needs_encrypted_office_review"
                    )
                elif primary_type == "ambiguous_office_container":
                    raise _TypedExtractionError(
                        "needs_ambiguous_office_container_review"
                    )
                else:
                    raise _TypedExtractionError(
                        "needs_unsupported_ole_parser"
                    )
            except _CFBError as exc:
                classification = "needs_corrupt_or_non_cfb_review"
                issue_codes.append(exc.code)
            except _TypedExtractionError as exc:
                classification = exc.code
                issue_codes.append(exc.code)
                if exc.details:
                    issue = _issue_row(
                        parse_run_id=parse_run_id,
                        artifact_sha256=artifact_sha256,
                        issue_code=exc.code,
                        resource_bindings=bindings,
                        details=exc.details,
                    )
                    _append_row(stage_dir / "ole-issues.jsonl", issue)
                    counts["issues"] += 1
                    written_issue_codes.add(exc.code)
            except Exception as exc:
                classification = "needs_unexpected_ole_parser_review"
                issue_codes.append(classification)
                issue = _issue_row(
                    parse_run_id=parse_run_id,
                    artifact_sha256=artifact_sha256,
                    issue_code=classification,
                    resource_bindings=bindings,
                    details={"error_type": type(exc).__name__},
                )
                _append_row(stage_dir / "ole-issues.jsonl", issue)
                counts["issues"] += 1
                written_issue_codes.add(classification)

            for issue_code in issue_codes:
                if issue_code not in written_issue_codes:
                    issue = _issue_row(
                        parse_run_id=parse_run_id,
                        artifact_sha256=artifact_sha256,
                        issue_code=issue_code,
                        resource_bindings=bindings,
                    )
                    _append_row(stage_dir / "ole-issues.jsonl", issue)
                    counts["issues"] += 1
                    written_issue_codes.add(issue_code)

            classification_counts[classification] += 1
            if classification in {WORD_TYPED_EXTRACTED, EXCEL_TYPED_EXTRACTED}:
                counts["typed_extracted_artifacts"] += 1
            else:
                counts["needs_review_artifacts"] += 1
            artifact_row = {
                "schema": OLE_ARTIFACT_SCHEMA,
                "parse_run_id": parse_run_id,
                "artifact_sha256": artifact_sha256,
                "byte_size": artifact["byte_size"],
                "media_type": artifact["media_type"],
                "resource_bindings": bindings,
                "declared_extension": _declared_extension(bindings),
                "primary_office_type": primary_type,
                "classification": classification,
                "issue_codes": sorted(set(issue_codes)),
                "container_receipt": container_receipt,
                "stream_inventory_sha256": stream_inventory_sha256,
                "extracted_text_sha256": extracted_text_sha256,
                "extraction_receipt": extraction_receipt,
                "counts": artifact_counts,
                "statement": NON_CLAIM,
            }
            _append_row(stage_dir / "ole-artifacts.jsonl", artifact_row)

    if counts["declared_ole_artifacts"] == 0:
        raise ContractError("verified acquisition run contains no declared OLE artifact")
    classified = sum(classification_counts.values())
    if classified != counts["declared_ole_artifacts"]:
        raise ContractError("OLE classification denominator mismatch")
    status = "passed" if counts["needs_review_artifacts"] == 0 else "partial"
    files = [
        manifest_file_entry(stage_dir / filename)
        for filename in OLE_STAGE_FILES
    ]
    manifest = {
        "schema": OLE_MANIFEST_SCHEMA,
        "parse_run_id": parse_run_id,
        "status": status,
        "started_at": started_at,
        "completed_at": utc_now(),
        "raw_manifest_sha256": raw_manifest_sha,
        "raw_manifest_source_plan_sha256": raw_manifest.get("source_plan_sha256"),
        "raw_verification": raw_verification,
        "parser_version": OLE_PARSER_VERSION,
        "parser_bundle_sha256": parser_bundle_sha256,
        "tools": tools,
        "network_boundary": {
            "shell": False,
            "network": "denied for LibreOffice by sandbox-exec policy",
            "policy_sha256": sha256_bytes(NETWORK_DENY_POLICY.encode("utf-8")),
        },
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": sha256_bytes(canonical_json_bytes(files)),
        "counts": dict(sorted(counts.items())),
        "subtype_counts": dict(sorted(subtype_counts.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
        "closure_claims": {
            "declared_ole_artifacts_exhaustively_classified": (
                classified == counts["declared_ole_artifacts"]
            ),
            "all_typed_extraction_complete": (
                counts["needs_review_artifacts"] == 0
            ),
            "page_layout_complete": False,
            "formula_expressions_complete": False,
            "embedded_object_content_complete": False,
            "legal_dates_interpreted": False,
            "clause_identity_resolved": False,
            "event_effect_resolved": False,
            "history_complete": False,
        },
        "known_limitations": [
            (
                "Legacy Word pagination is layout-dependent; paragraph/table/cell "
                "locators are retained for text-bearing documents. Zero-text Word "
                "documents are additionally rendered with the bound LibreOffice "
                "version to retain visual-review page locators."
            ),
            (
                "xlrd exposes cached BIFF cell values and merged ranges, not full "
                "formula expressions or print-page layout."
            ),
            (
                "Embedded object streams are inventoried and hashed, but their "
                "nested semantic content is not recursively typed-extracted."
            ),
        ],
        "statement": NON_CLAIM,
        "files": files,
    }
    write_json(stage_dir / "ole-manifest.json", manifest)
    return manifest


__all__ = [
    "EXCEL_TYPED_EXTRACTED",
    "OLE_PARSER_VERSION",
    "WORD_TYPED_EXTRACTED",
    "parse_verified_ole_run",
]
