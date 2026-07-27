from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import canonical_json_bytes, file_sha256

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PgLoadError(RuntimeError):
    """Sanitized staging load or verification failure."""


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PgLoadError(f"invalid JSON object: {path.name}") from exc
    if not isinstance(value, dict):
        raise PgLoadError(f"JSON root is not an object: {path.name}")
    return value


def row_sha256(row: Mapping[str, Any], *, derived_key: str | None = None) -> str:
    clean = dict(row)
    if derived_key is not None:
        clean.pop(derived_key, None)
    return hashlib.sha256(canonical_json_bytes(clean)).hexdigest()


def row_set_fingerprint(row_hashes: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(row_hashes):
        if not SHA256_RE.fullmatch(value):
            raise PgLoadError("row fingerprint contains invalid SHA-256")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def object_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def code_fingerprint(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def migration_fingerprint(path: Path) -> str:
    return file_sha256(path)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
