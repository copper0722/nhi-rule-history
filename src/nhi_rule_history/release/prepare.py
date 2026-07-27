"""Prepare checksummed release assets without network or publication actions."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nhi_rule_history.export.canonical import canonical_json_bytes
from nhi_rule_history.export.contract import TABLES
from nhi_rule_history.export.stage import verify_export_directory


class PrepareError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compress_with_module(source: Path, destination: Path) -> bool:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        return False
    compressor = zstandard.ZstdCompressor(level=19, write_checksum=True)
    with source.open("rb") as reader, destination.open("wb") as writer:
        compressor.copy_stream(reader, writer)
    return True


def _compress_with_command(source: Path, destination: Path) -> bool:
    executable = shutil.which("zstd")
    if executable is None:
        return False
    with destination.open("wb") as handle:
        subprocess.run(
            [
                executable,
                "--quiet",
                "--force",
                "--no-progress",
                "-19",
                "--stdout",
                str(source),
            ],
            check=True,
            stdout=handle,
        )
    return True


def _compress(source: Path, destination: Path) -> str | None:
    if _compress_with_module(source, destination):
        return "zstandard-python-level-19"
    if _compress_with_command(source, destination):
        return "zstd-cli-level-19"
    destination.unlink(missing_ok=True)
    return None


def _decompressed_sha256(path: Path, compression: str) -> str:
    digest = hashlib.sha256()
    if compression.startswith("zstandard-python"):
        import zstandard  # type: ignore[import-not-found]

        decompressor = zstandard.ZstdDecompressor()
        with path.open("rb") as source, decompressor.stream_reader(source) as reader:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if compression.startswith("zstd-cli"):
        executable = shutil.which("zstd")
        if executable is None:
            raise PrepareError("zstd command disappeared during verification")
        process = subprocess.Popen(
            [executable, "--quiet", "--decompress", "--stdout", str(path)],
            stdout=subprocess.PIPE,
        )
        assert process.stdout is not None
        with process.stdout:
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
        return_code = process.wait()
        if return_code != 0:
            raise PrepareError(f"zstd verification failed with code {return_code}")
        return digest.hexdigest()
    raise PrepareError(f"unknown compression mode: {compression}")


def prepare_release(
    *,
    export_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Verify an export and create an offline, publish-ready asset directory."""
    if output_dir.exists():
        raise PrepareError(f"release output already exists: {output_dir}")
    export_manifest = verify_export_directory(export_dir)
    output_dir.mkdir(parents=True)
    try:
        assets: dict[str, dict[str, Any]] = {}
        compression_modes: set[str] = set()
        for table in TABLES:
            source = export_dir / f"{table.name}.jsonl"
            compressed = output_dir / f"{table.name}.jsonl.zst"
            compression = _compress(source, compressed)
            if compression is None:
                destination = output_dir / source.name
                shutil.copyfile(source, destination)
                media_type = "application/x-ndjson"
                compression = "none"
            else:
                destination = compressed
                media_type = "application/zstd"
                if _decompressed_sha256(destination, compression) != (
                    export_manifest["files"][source.name]["sha256"]
                ):
                    raise PrepareError(
                        f"{destination.name}: decompressed checksum mismatch"
                    )
            compression_modes.add(compression)
            assets[destination.name] = {
                "bytes": destination.stat().st_size,
                "media_type": media_type,
                "sha256": _sha256_file(destination),
                "source_sha256": export_manifest["files"][source.name]["sha256"],
                "row_count": export_manifest["files"][source.name]["row_count"],
                "compression": compression,
            }

        sqlite_source = export_dir / "nhi-rule-history-stage-v1.sqlite"
        sqlite_destination = output_dir / sqlite_source.name
        shutil.copyfile(sqlite_source, sqlite_destination)
        sqlite_sha256 = _sha256_file(sqlite_destination)
        if sqlite_sha256 != export_manifest["files"][sqlite_source.name]["sha256"]:
            raise PrepareError("copied SQLite checksum differs from verified export")
        assets[sqlite_destination.name] = {
            "bytes": sqlite_destination.stat().st_size,
            "media_type": "application/vnd.sqlite3",
            "sha256": sqlite_sha256,
            "source_sha256": export_manifest["files"][sqlite_source.name]["sha256"],
            "compression": "none",
        }

        release_manifest = {
            "schema": "nhi-rule-history-stage-release-preparation/v1",
            "status": "prepared_not_published",
            "publication_performed": False,
            "run_id": export_manifest["run_id"],
            "sealed_fingerprint": export_manifest["sealed_fingerprint"],
            "dataset_kind": export_manifest["dataset_kind"],
            "legal_history_claim": False,
            "scope_statement": export_manifest["scope_statement"],
            "logical_row_digest": export_manifest["logical_row_digest"],
            "table_counts": export_manifest["table_counts"],
            "compression_modes": sorted(compression_modes),
            "assets": assets,
            "verification": {
                "source_export_verified": "passed",
                "release_asset_checksums": "passed",
                "network_publication": "not_performed",
            },
        }
        manifest_path = output_dir / "release-manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(release_manifest) + b"\n")
        return release_manifest
    except Exception:
        for path in output_dir.glob("*"):
            if path.is_file():
                path.unlink()
        output_dir.rmdir()
        raise
