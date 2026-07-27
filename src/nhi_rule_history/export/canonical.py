"""Canonical values, JSONL serialization, and storage-independent digests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contract import TABLES, Column, Table


class CanonicalError(ValueError):
    pass


def canonical_timestamp(value: Any) -> str:
    if isinstance(value, str):
        text = value
        if text.endswith("Z"):
            parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            parsed = dt.datetime.fromisoformat(text)
    elif isinstance(value, dt.datetime):
        parsed = value
    else:
        raise CanonicalError(f"timestamp has unsupported type: {type(value).__name__}")
    if parsed.tzinfo is None:
        raise CanonicalError("timestamp must include a UTC offset")
    parsed = parsed.astimezone(dt.timezone.utc)
    if parsed.microsecond:
        rendered = parsed.isoformat(timespec="microseconds")
    else:
        rendered = parsed.isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def canonical_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise CanonicalError(f"non-integral decimal is not allowed: {value}")
        return int(value)
    if isinstance(value, dt.datetime):
        return canonical_timestamp(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalError("JSON object keys must be strings")
        return {
            key: canonical_json_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        return [canonical_json_value(item) for item in value]
    raise CanonicalError(f"unsupported public value type: {type(value).__name__}")


def canonicalize_column(value: Any, column: Column) -> Any:
    if value is None:
        if not column.nullable:
            raise CanonicalError(f"{column.name} is unexpectedly null")
        return None
    if column.kind == "timestamp":
        return canonical_timestamp(value)
    value = canonical_json_value(value)
    if column.kind == "text" and not isinstance(value, str):
        raise CanonicalError(f"{column.name} must be text")
    if column.kind == "integer" and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        raise CanonicalError(f"{column.name} must be an integer")
    if column.kind == "boolean" and not isinstance(value, bool):
        raise CanonicalError(f"{column.name} must be a boolean")
    if column.kind == "json" and not isinstance(value, (dict, list)):
        raise CanonicalError(f"{column.name} must be a JSON object or array")
    return value


def canonicalize_row(row: Mapping[str, Any], table: Table) -> dict[str, Any]:
    expected = {column.name for column in table.columns}
    actual = set(row)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise CanonicalError(
            f"{table.name}: column mismatch missing={missing} unknown={unknown}"
        )
    return {
        column.name: canonicalize_column(row[column.name], column)
        for column in table.columns
    }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        canonical_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def row_sort_key(row: Mapping[str, Any], table: Table) -> tuple[Any, ...]:
    return tuple(row[column] for column in table.primary_key)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    count = 0
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for row in rows:
            line = canonical_json_bytes(row) + b"\n"
            handle.write(line)
            digest.update(line)
            count += 1
    return count, digest.hexdigest()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.endswith(b"\n"):
                raise CanonicalError(f"{path.name}:{line_number}: missing LF terminator")
            if raw_line.endswith(b"\r\n"):
                raise CanonicalError(f"{path.name}:{line_number}: CRLF is forbidden")
            try:
                line = raw_line[:-1].decode("utf-8")
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CanonicalError(
                    f"{path.name}:{line_number}: invalid canonical JSONL"
                ) from exc
            if not isinstance(row, dict):
                raise CanonicalError(f"{path.name}:{line_number}: row is not an object")
            if canonical_json_bytes(row) != raw_line[:-1]:
                raise CanonicalError(
                    f"{path.name}:{line_number}: non-canonical JSON encoding"
                )
            yield row


def logical_digest(table_rows: Mapping[str, Iterable[Mapping[str, Any]]]) -> str:
    digest = hashlib.sha256()
    for table in TABLES:
        digest.update(table.name.encode("ascii") + b"\0")
        for row in table_rows[table.name]:
            canonical = canonicalize_row(row, table)
            digest.update(canonical_json_bytes(canonical) + b"\n")
    return digest.hexdigest()


_REDACTION_PATTERNS = (
    ("private macOS path", re.compile(r"/Users/[A-Za-z0-9._-]+/")),
    ("private Unix home path", re.compile(r"/home/[A-Za-z0-9._-]+/")),
    ("PostgreSQL DSN", re.compile(r"postgres(?:ql)?://", re.IGNORECASE)),
    ("credential assignment", re.compile(
        r"(?:password|passwd|api[_-]?key|secret|token)\s*[=:]",
        re.IGNORECASE,
    )),
)


def redaction_scan_text(text: str, *, label: str) -> None:
    for description, pattern in _REDACTION_PATTERNS:
        match = pattern.search(text)
        if match:
            raise CanonicalError(
                f"{label}: redaction scan found {description} at offset {match.start()}"
            )


def redaction_scan_jsonl(paths: Iterable[Path]) -> None:
    for path in paths:
        redaction_scan_text(path.read_text(encoding="utf-8"), label=path.name)
