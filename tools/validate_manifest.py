#!/usr/bin/env python3
"""Validate a public source-artifact JSONL manifest and optional local files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_KEYS = {
    "schema",
    "source_id",
    "official_label",
    "source_page_url",
    "official_url",
    "filename",
    "media_type",
    "byte_length",
    "sha256",
    "verified_on",
    "licence",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_https(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    args = parser.parse_args()

    seen_sha: set[str] = set()
    seen_url: set[str] = set()
    count = 0
    total_bytes = 0

    with args.manifest.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = REQUIRED_KEYS - set(row)
            if missing:
                raise SystemExit(
                    f"line {line_number}: missing keys {sorted(missing)}"
                )
            if row["schema"] != "nhi-source-artifact/v1":
                raise SystemExit(f"line {line_number}: unsupported schema")
            if not valid_https(row["source_page_url"]) or not valid_https(
                row["official_url"]
            ):
                raise SystemExit(f"line {line_number}: official URLs must be https")
            sha = row["sha256"]
            if not isinstance(sha, str) or len(sha) != 64:
                raise SystemExit(f"line {line_number}: invalid sha256")
            if sha in seen_sha or row["official_url"] in seen_url:
                raise SystemExit(f"line {line_number}: duplicate artifact")
            seen_sha.add(sha)
            seen_url.add(row["official_url"])

            byte_length = row["byte_length"]
            if not isinstance(byte_length, int) or byte_length < 0:
                raise SystemExit(f"line {line_number}: invalid byte_length")

            if args.raw_dir:
                path = args.raw_dir / row["filename"]
                if not path.is_file():
                    raise SystemExit(f"line {line_number}: missing {path.name}")
                if path.stat().st_size != byte_length:
                    raise SystemExit(f"line {line_number}: byte length mismatch")
                if sha256_file(path) != sha:
                    raise SystemExit(f"line {line_number}: sha256 mismatch")

            count += 1
            total_bytes += byte_length

    print(
        json.dumps(
            {
                "status": "ok",
                "artifacts": count,
                "total_bytes": total_bytes,
                "local_files_verified": args.raw_dir is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
