from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nhi_rule_history.contracts import (
    ContractError,
    file_sha256,
    relative_blob_path,
    resolve_run_relative,
    sha256_bytes,
)


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    byte_size: int
    relative_path: str


class RawStore:
    """Immutable SHA-256 store rooted inside one acquisition run."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir

    def put(self, payload: bytes) -> StoredBlob:
        digest = sha256_bytes(payload)
        relative = relative_blob_path(digest)
        destination = resolve_run_relative(self.run_dir, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size != len(payload) or file_sha256(destination) != digest:
                raise ContractError(f"content-address collision at {relative}")
        else:
            temporary = destination.with_name(f".{digest}.tmp")
            temporary.write_bytes(payload)
            if file_sha256(temporary) != digest:
                temporary.unlink(missing_ok=True)
                raise ContractError("raw write verification failed")
            os.replace(temporary, destination)
        return StoredBlob(digest, len(payload), relative)

    def verify(self, relative: str, digest: str, expected_size: int) -> bool:
        path = resolve_run_relative(self.run_dir, relative)
        return (
            path.is_file()
            and path.stat().st_size == expected_size
            and file_sha256(path) == digest
        )

    def read(self, relative: str, digest: str, expected_size: int) -> bytes:
        if not self.verify(relative, digest, expected_size):
            raise ContractError(f"raw artifact failed verification: {relative}")
        return resolve_run_relative(self.run_dir, relative).read_bytes()
