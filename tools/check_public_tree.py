#!/usr/bin/env python3
"""Fail closed when generated release assets enter the public Git tree."""

from __future__ import annotations

import subprocess
from pathlib import Path


MAX_GIT_FILE_BYTES = 50 * 1024 * 1024
FORBIDDEN_SUFFIXES = (
    ".odt",
    ".ods",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".zst",
)
FORBIDDEN_PREFIXES = ("build/", "data/raw/files/")
FIXTURE_PREFIX = "tests/fixtures/"


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def main() -> int:
    errors: list[str] = []
    for path in candidate_paths():
        normalized = path.as_posix()
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_GIT_FILE_BYTES:
            errors.append(f"{normalized}: {size} bytes exceeds 50 MiB")
        if normalized.startswith(FORBIDDEN_PREFIXES):
            errors.append(f"{normalized}: generated/raw path is forbidden in Git")
        if (
            not normalized.startswith(FIXTURE_PREFIX)
            and normalized.lower().endswith(FORBIDDEN_SUFFIXES)
        ):
            errors.append(f"{normalized}: release/binary suffix is forbidden in Git")
    if errors:
        raise SystemExit("public tree gate failed:\n" + "\n".join(sorted(errors)))
    print("public tree gate: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
