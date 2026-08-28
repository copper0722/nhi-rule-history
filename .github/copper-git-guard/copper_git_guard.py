#!/usr/bin/env python3
"""Copper Git Boundary Guard.

A fail-closed Git transport boundary scanner for Copper's repository fleet.
It scans exact Git blobs from the index or outgoing commit history rather than
trusting the mutable working tree.

The default policy is intentionally strict:
- Git is text/code only.
- Every binary payload, including a Git LFS pointer, is blocked unless a future
  higher-authority policy explicitly introduces a narrow exception.
- Secrets, credential containers, likely PHI/PII, corpus payloads, caches,
  databases, archives, model weights, executables, and unsafe symlinks are
  blocked.

The program never prints matched secret values. JSON/SARIF receipts contain
only rule identifiers, paths, blob identities, types, and safe metadata.
"""

from __future__ import annotations

import argparse
import base64
import bz2
import dataclasses
import datetime as dt
import fnmatch
import gzip
import hashlib
import io
import json
import lzma
import math
import os
import pathlib
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unicodedata
import urllib.parse
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python <3.11
    raise SystemExit("Copper Git Boundary Guard requires Python 3.11+") from exc

VERSION = "0.4.0"
MAX_EMBEDDED_DECODE_BYTES = 8 * 1024 * 1024
ZERO_SHA = "0" * 40
DEFAULT_CONFIG = ".copper-git-guard.toml"
DEFAULT_RECEIPT = ".git/copper-git-guard/last-scan.json"
DEFAULT_SARIF = ".git/copper-git-guard/last-scan.sarif"
MIB = 1024 * 1024


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    BLOCK = 30
    ERROR = 40

    @classmethod
    def parse(cls, value: str) -> "Severity":
        return {
            "info": cls.INFO,
            "warning": cls.WARNING,
            "block": cls.BLOCK,
            "error": cls.ERROR,
        }[value.lower()]

    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    path: str
    message: str
    blob_sha: str | None = None
    commit: str | None = None
    inner_path: str | None = None
    scanner: str = "copper-git-guard"
    safe_details: Mapping[str, Any] = field(default_factory=dict)

    def display_path(self) -> str:
        path = redact_metadata_value(self.path, "path")
        if self.inner_path:
            return f"{path}!{redact_metadata_value(self.inner_path, 'inner-path')}"
        return path

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity.label(),
            "path": redact_metadata_value(self.path, "path"),
            "message": self.message,
            "scanner": self.scanner,
        }
        if self.blob_sha:
            payload["blob_sha"] = self.blob_sha
        if self.commit:
            payload["commit"] = self.commit
        if self.inner_path:
            payload["inner_path"] = redact_metadata_value(self.inner_path, "inner-path")
        if self.safe_details:
            payload["details"] = dict(self.safe_details)
        return payload


@dataclass(frozen=True)
class Artifact:
    path: str
    mode: str
    object_type: str
    blob_sha: str
    size: int
    commit: str | None = None
    collision_with: str | None = None

    @property
    def is_symlink(self) -> bool:
        return self.mode == "120000"

    @property
    def is_submodule(self) -> bool:
        return self.mode == "160000"


@dataclass(frozen=True)
class Classification:
    kind: str
    mime: str
    is_text: bool
    encoding: str | None = None
    detail: str | None = None
    lfs_oid: str | None = None
    lfs_size: int | None = None


@dataclass
class Limits:
    warn_blob_bytes: int = 1 * MIB
    max_text_bytes: int = 10 * MIB
    max_blob_bytes: int = 100 * MIB
    max_scan_bytes: int = 32 * MIB
    max_commits: int = 5000
    archive_max_depth: int = 3
    archive_max_members: int = 5000
    archive_max_unpacked_bytes: int = 512 * MIB
    archive_max_ratio: float = 200.0
    archive_member_scan_bytes: int = 8 * MIB


@dataclass
class Policy:
    repo_root: pathlib.Path
    config_path: pathlib.Path | None = None
    repository: str | None = None
    visibility: str | None = None
    repo_status: str | None = None
    write_policy: str | None = None
    host: str = field(default_factory=lambda: socket.gethostname().split(".")[0].lower())
    device_binary_at_rest: str | None = None
    require_roster: bool = False
    fail_closed: bool = True
    warnings_as_errors: bool = False
    binary_mode: str = "deny"
    lfs_mode: str = "deny"
    external_gitleaks: str = "auto"  # off | auto | required
    gitleaks_archive_depth: int = 2
    gitleaks_decode_depth: int = 2
    allow_external_symlink_globs: list[str] = field(default_factory=list)
    allow_submodule_globs: list[str] = field(default_factory=list)
    extra_deny_path_globs: list[str] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    roster_path: pathlib.Path | None = None
    roster_fingerprint: str | None = None


@dataclass
class ScanResult:
    mode: str
    repo_root: str
    repository: str | None
    visibility: str | None
    host: str
    started_at: str
    finished_at: str
    artifacts_scanned: int
    unique_blobs_scanned: int
    bytes_considered: int
    findings: list[Finding]
    scanner_versions: dict[str, str]
    policy: dict[str, Any]
    refs: dict[str, Any]

    @property
    def blocking(self) -> bool:
        return any(f.severity >= Severity.BLOCK for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        counts = Counter(f.severity.label() for f in self.findings)
        return {
            "schema_version": 1,
            "tool": "copper-git-guard",
            "tool_version": VERSION,
            "mode": self.mode,
            "repo_root": self.repo_root,
            "repository": self.repository,
            "visibility": self.visibility,
            "host": self.host,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifacts_scanned": self.artifacts_scanned,
            "unique_blobs_scanned": self.unique_blobs_scanned,
            "bytes_considered": self.bytes_considered,
            "blocking": self.blocking,
            "finding_counts": dict(sorted(counts.items())),
            "findings": [f.as_dict() for f in self.findings],
            "scanner_versions": self.scanner_versions,
            "policy": self.policy,
            "refs": self.refs,
        }


class GuardError(RuntimeError):
    pass


class Git:
    def __init__(self, root: pathlib.Path):
        self.root = root

    @classmethod
    def discover(cls, start: pathlib.Path | None = None) -> "Git":
        cwd = start or pathlib.Path.cwd()
        cp = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if cp.returncode != 0:
            raise GuardError(cp.stderr.strip() or "not inside a Git repository")
        return cls(pathlib.Path(cp.stdout.strip()).resolve())

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        text: bool = False,
        input_data: bytes | str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        cp = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=False,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            env=dict(os.environ, **(env or {})),
        )
        if check and cp.returncode != 0:
            stderr = cp.stderr.strip() if text else cp.stderr.decode("utf-8", "replace").strip()
            raise GuardError(f"git {' '.join(shlex.quote(a) for a in args)} failed: {stderr}")
        return cp

    def rev_parse(self, ref: str) -> str:
        return self.run(["rev-parse", "--verify", ref], text=True).stdout.strip()

    def object_size(self, sha: str) -> int:
        raw = self.run(["cat-file", "-s", sha], text=True).stdout.strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise GuardError(f"invalid Git object size for {sha}: {raw!r}") from exc

    def object_type(self, sha: str) -> str:
        return self.run(["cat-file", "-t", sha], text=True).stdout.strip()

    def read_object(self, sha: str, object_type: str, max_bytes: int) -> tuple[bytes, bool]:
        """Read at most max_bytes+1 from one typed Git object."""
        if object_type not in {"blob", "commit", "tag"}:
            raise GuardError(f"unsupported Git object type for content scan: {object_type}")
        proc = subprocess.Popen(
            ["git", "-C", str(self.root), "cat-file", object_type, sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        try:
            data = proc.stdout.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
                proc.kill()
            _, stderr = proc.communicate(timeout=10)
        except Exception:
            proc.kill()
            proc.communicate()
            raise
        if not truncated and proc.returncode not in (0, None):
            raise GuardError(stderr.decode("utf-8", "replace").strip())
        return data, truncated

    @staticmethod
    def _annotate_path_collisions(artifacts: list[Artifact], all_paths: Iterable[str]) -> list[Artifact]:
        by_key: dict[str, set[str]] = {}
        for candidate in all_paths:
            normalized = normalize_repo_path(candidate)
            key = unicodedata.normalize("NFC", normalized).casefold()
            by_key.setdefault(key, set()).add(normalized)
        result: list[Artifact] = []
        for artifact in artifacts:
            normalized = normalize_repo_path(artifact.path)
            peers = by_key.get(unicodedata.normalize("NFC", normalized).casefold(), set()) - {normalized}
            result.append(dataclasses.replace(artifact, collision_with=sorted(peers)[0] if peers else None))
        return result

    def metadata_artifact(self, sha: str, *, ref_label: str | None = None) -> Artifact | None:
        object_type = self.object_type(sha)
        if object_type not in {"commit", "tag"}:
            return None
        label = ref_label or sha
        return Artifact(
            path=f"@git/{object_type}/{label}",
            mode="100644",
            object_type=object_type,
            blob_sha=sha,
            size=self.object_size(sha),
            commit=sha if object_type == "commit" else None,
        )

    def tag_chain_artifacts(self, sha: str, *, ref_label: str) -> list[Artifact]:
        artifacts: list[Artifact] = []
        current = sha
        seen: set[str] = set()
        for depth in range(16):
            if current in seen:
                raise GuardError("annotated tag chain contains a cycle")
            seen.add(current)
            if self.object_type(current) != "tag":
                return artifacts
            metadata = self.metadata_artifact(current, ref_label=f"{ref_label}/tag-{depth}")
            if metadata:
                artifacts.append(metadata)
            raw, truncated = self.read_object(current, "tag", 64 * 1024)
            if truncated:
                raise GuardError("annotated tag object exceeds metadata traversal limit")
            match = re.match(br"object ([0-9a-f]{40})\n", raw)
            if not match:
                raise GuardError("annotated tag object has no canonical target header")
            current = match.group(1).decode("ascii")
        raise GuardError("annotated tag chain exceeds depth limit")

    def remote_repository(self) -> str | None:
        cp = self.run(["remote", "get-url", "origin"], check=False, text=True)
        if cp.returncode != 0:
            return None
        return parse_github_repository(cp.stdout.strip())

    def git_path(self, relative: str) -> pathlib.Path:
        raw = self.run(["rev-parse", "--git-path", relative], text=True).stdout.strip()
        path = pathlib.Path(raw)
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def staged_artifacts(self) -> list[Artifact]:
        cp = self.run(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRTUXB"])
        paths = [p.decode("utf-8", "surrogateescape") for p in cp.stdout.split(b"\0") if p]
        artifacts: list[Artifact] = []
        for path in paths:
            entries = self.run(["ls-files", "-s", "-z", "--", path]).stdout.split(b"\0")
            selected: tuple[str, str, str] | None = None
            for entry in entries:
                if not entry:
                    continue
                prefix, _, encoded_path = entry.partition(b"\t")
                fields = prefix.decode("ascii", "replace").split()
                if len(fields) != 3:
                    continue
                mode, sha, stage = fields
                if stage == "0" and encoded_path.decode("utf-8", "surrogateescape") == path:
                    selected = (mode, sha, stage)
                    break
            if selected is None:
                raise GuardError(f"unable to resolve staged index entry for {path}")
            mode, sha, _ = selected
            object_type = "commit" if mode == "160000" else "blob"
            size = 0 if object_type == "commit" else self.object_size(sha)
            artifacts.append(Artifact(path=path, mode=mode, object_type=object_type, blob_sha=sha, size=size))
        all_paths = [p.decode("utf-8", "surrogateescape") for p in self.run(["ls-files", "-z"]).stdout.split(b"\0") if p]
        return self._annotate_path_collisions(artifacts, all_paths)

    def tree_artifacts(self, ref: str) -> list[Artifact]:
        metadata: list[Artifact] = []
        object_sha = self.rev_parse(ref)
        if self.object_type(object_sha) == "tag":
            metadata.extend(self.tag_chain_artifacts(object_sha, ref_label=ref))
        else:
            direct = self.metadata_artifact(object_sha, ref_label=ref)
            if direct:
                metadata.append(direct)
        peeled = self.run(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False, text=True)
        if peeled.returncode == 0:
            commit_sha = peeled.stdout.strip()
            if commit_sha != object_sha:
                commit_metadata = self.metadata_artifact(commit_sha)
                if commit_metadata:
                    metadata.append(commit_metadata)
        cp = self.run(["ls-tree", "-r", "-z", "--full-tree", ref])
        artifacts: list[Artifact] = []
        for record in cp.stdout.split(b"\0"):
            if not record:
                continue
            meta, sep, encoded_path = record.partition(b"\t")
            if not sep:
                raise GuardError(f"malformed ls-tree record at {ref}")
            mode, object_type, sha = meta.decode("ascii", "replace").split()
            path = encoded_path.decode("utf-8", "surrogateescape")
            size = 0 if object_type == "commit" else self.object_size(sha)
            artifacts.append(Artifact(path=path, mode=mode, object_type=object_type, blob_sha=sha, size=size, commit=ref))
        paths = [artifact.path for artifact in artifacts]
        return metadata + self._annotate_path_collisions(artifacts, paths)

    def commits_between(self, base: str | None, head: str, *, remote_name: str = "origin", max_commits: int = 5000) -> list[str]:
        if base == ZERO_SHA:
            args = ["rev-list", "--reverse", head]
        elif base:
            args = ["rev-list", "--reverse", f"{base}..{head}"]
        else:
            args = ["rev-list", "--reverse", head, "--not", f"--remotes={remote_name}"]
        lines = [line for line in self.run(args, text=True).stdout.splitlines() if line]
        if len(lines) > max_commits:
            raise GuardError(f"outgoing range contains {len(lines)} commits; maximum is {max_commits}")
        return lines

    def changed_artifacts_for_commit(self, commit: str, *, check_collisions: bool = True) -> list[Artifact]:
        cp = self.run(
            ["diff-tree", "--root", "-m", "-r", "--no-commit-id", "--name-only", "-z", "--diff-filter=ACMRTUXB", commit]
        )
        paths = list(dict.fromkeys(p.decode("utf-8", "surrogateescape") for p in cp.stdout.split(b"\0") if p))
        artifacts: list[Artifact] = []
        for path in paths:
            entry_cp = self.run(["ls-tree", "-z", commit, "--", path])
            records = [r for r in entry_cp.stdout.split(b"\0") if r]
            if not records:
                continue
            # Exact path can be represented once; reject ambiguity rather than guess.
            exact_records = []
            for record in records:
                meta, sep, encoded_path = record.partition(b"\t")
                if sep and encoded_path.decode("utf-8", "surrogateescape") == path:
                    exact_records.append((meta, encoded_path))
            if len(exact_records) != 1:
                raise GuardError(f"unable to resolve exact tree object for {commit}:{path}")
            meta, _ = exact_records[0]
            mode, object_type, sha = meta.decode("ascii", "replace").split()
            size = 0 if object_type == "commit" else self.object_size(sha)
            artifacts.append(
                Artifact(path=path, mode=mode, object_type=object_type, blob_sha=sha, size=size, commit=commit)
            )
        if not check_collisions:
            return artifacts
        tree_paths = [p.decode("utf-8", "surrogateescape") for p in self.run(["ls-tree", "-r", "--name-only", "-z", commit]).stdout.split(b"\0") if p]
        return self._annotate_path_collisions(artifacts, tree_paths)

    def range_artifacts(
        self,
        base: str | None,
        head: str,
        *,
        remote_name: str = "origin",
        max_commits: int = 5000,
    ) -> tuple[list[Artifact], list[str]]:
        commits = self.commits_between(base, head, remote_name=remote_name, max_commits=max_commits)
        artifacts: list[Artifact] = []
        seen: set[tuple[str, str]] = set()
        head_sha = self.rev_parse(head)
        if self.object_type(head_sha) == "tag":
            for metadata in self.tag_chain_artifacts(head_sha, ref_label=head):
                seen.add((metadata.path, metadata.blob_sha))
                artifacts.append(metadata)
        for commit in commits:
            metadata = self.metadata_artifact(commit)
            commit_artifacts = ([metadata] if metadata else []) + self.changed_artifacts_for_commit(commit)
            for artifact in commit_artifacts:
                key = (artifact.path, artifact.blob_sha)
                if key not in seen:
                    seen.add(key)
                    artifacts.append(artifact)
        return artifacts, commits

    def history_artifacts(self, *, max_commits: int = 5000) -> tuple[list[Artifact], list[str]]:
        commits = [line for line in self.run(["rev-list", "--reverse", "--all"], text=True).stdout.splitlines() if line]
        if len(commits) > max_commits:
            raise GuardError(f"reachable history contains {len(commits)} commits; maximum is {max_commits}")
        artifacts: list[Artifact] = []
        seen: set[tuple[str, str]] = set()
        for commit in commits:
            metadata = self.metadata_artifact(commit)
            commit_artifacts = ([metadata] if metadata else []) + self.changed_artifacts_for_commit(commit, check_collisions=False)
            for artifact in commit_artifacts:
                key = (artifact.path, artifact.blob_sha)
                if key not in seen:
                    seen.add(key)
                    artifacts.append(artifact)
        refs = self.run(["for-each-ref", "--format=%(objectname) %(objecttype)", "refs/tags"], text=True).stdout.splitlines()
        for record in refs:
            sha, _, object_type = record.partition(" ")
            if object_type != "tag":
                continue
            for metadata in self.tag_chain_artifacts(sha, ref_label=sha):
                if (metadata.path, metadata.blob_sha) not in seen:
                    seen.add((metadata.path, metadata.blob_sha))
                    artifacts.append(metadata)
        return artifacts, commits


# Paths that should never cross a Git boundary. This list intentionally covers
# text-shaped credential files too; binary classification alone is insufficient.
SECRET_PATH_GLOBS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa",
    "id_ed25519",
    "**/id_rsa",
    "**/id_ed25519",
    ".netrc",
    "**/.netrc",
    ".npmrc",
    "**/.npmrc",
    ".pypirc",
    "**/.pypirc",
    "**/.aws/credentials",
    "*service-account*.json",
    "*credentials*.json",
    "*.mobileprovision",
]

FORBIDDEN_PAYLOAD_EXTENSIONS = {
    ".7z", ".a", ".apk", ".app", ".avi", ".bin", ".bmp", ".bz2", ".class",
    ".ckpt", ".db", ".dcm", ".dicom", ".dmg", ".doc", ".docm", ".docx",
    ".dylib", ".egg", ".epub", ".exe", ".flac", ".gif", ".gguf", ".gz",
    ".h5", ".hdf5", ".heic", ".ico", ".iso", ".jar", ".jpeg", ".jpg",
    ".keynote", ".lib", ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4",
    ".msi", ".npy", ".npz", ".numbers", ".o", ".obj", ".ogg", ".onnx",
    ".otf", ".pages", ".parquet", ".pdf", ".pickle", ".pkl", ".png", ".ppt",
    ".pptm", ".pptx", ".psd", ".pt", ".pth", ".rar", ".safetensors", ".so",
    ".sqlite", ".sqlite3", ".tar", ".tgz", ".tif", ".tiff", ".ttf", ".wav",
    ".webm", ".webp", ".whl", ".woff", ".woff2", ".xls", ".xlsb", ".xlsm",
    ".xlsx", ".xz", ".zip",
}

CACHE_COMPONENTS = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", ".terraform", ".gradle", ".parcel-cache",
    ".next", ".nuxt", ".cache", "DerivedData",
}

# Exact tokens are never echoed in findings.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("SECRET_PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("SECRET_AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("SECRET_GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("SECRET_GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("SECRET_SLACK_TOKEN", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    # A live bot token sat in a tracked file for months and this guard passed it:
    # the assignment was `BOT="..."`, which GENERIC_SECRET_ASSIGNMENT does not
    # match (no api_key/secret/token/password in the name), and there was no
    # Telegram rule. Found 2026-08-28 by asking why an outbound message was not
    # archived. The token shape is unusually precise -- bot id, colon, then a
    # 35-character body that Telegram always begins `AA` -- so anchoring on it
    # costs no false positives on timestamps, ratios or `sha256:` digests.
    ("SECRET_TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_-]{30,}\b")),
    ("SECRET_STRIPE_LIVE_KEY", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
    ("SECRET_OPENAI_STYLE_KEY", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("SECRET_BASIC_AUTH_URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.I)),
    ("SECRET_BEARER_TOKEN", re.compile(r"\bAuthorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
]

GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|client[_-]?secret|secret|token|password|passwd|pwd)\b"
    r"\s*[:=]\s*[\"']?([^\s\"';,#]{12,})"
)

TAIWAN_ID_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z][12]\d{8}(?![A-Z0-9])")
BASE64_PAYLOAD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+/=])(?:[A-Za-z0-9+/]{4}){128,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?(?![A-Za-z0-9+/=])"
)

PHI_CONTEXT_PATTERN = re.compile(
    r"(?i)(?:病歷號|病歷編號|身分證字號|病人姓名|患者姓名|patient\s*(?:name|id)|medical\s*record\s*(?:number|no)|\bMRN\b)"
    r"\s*[:=：]\s*(\S+)"
)

# A PHI_CONTEXT hit is only a leak when the VALUE is patient data — a record
# number or a CJK personal name. Application source spells the same keys as URL
# query parameters, HTML data-attributes, object-literal fields, regexes and
# documentation placeholders; treating those as PHI makes the guard unusable on
# any clinical web front end. Measured 2026-08-25 on one clinical repository:
# two front-end files carried 34 such hits between them, none of them patient
# data, and the guard refused every commit touching either file.
# The narrowing still blocks a record-number or personal-name VALUE; it only
# skips a locator-shaped key (preceded by a query/attribute character) and a
# value that is an identifier, regex, placeholder or punctuation.
PHI_CODE_LEAD_CHARS = frozenset("?&-/_$%")
PHI_CJK_NAME_PATTERN = re.compile(r"[一-鿿]{2,}")
# A captured value that starts with `identifier(` is a call expression: the code is
# *asking* for the field (a prompt, a fetch, a formatter), not carrying a patient.
# A bare record number or personal name can never start that way, so this cannot
# hide a real value — a leak written inside parentheses captures the closing paren
# on the value side and is still judged.
PHI_CODE_CALL_VALUE = re.compile(r"^[A-Za-z_$][\w$.]*\s*\(")


def phi_value_is_patient_data(value: str) -> bool:
    """True when the captured value looks like a record number or a CJK name."""
    stripped = value.strip().strip("\"'`,;:)]}")
    if not stripped:
        return False
    if PHI_CJK_NAME_PATTERN.search(stripped):
        return True
    if re.fullmatch(r"[0-9]{1,12}", stripped):
        return True
    # zero-padded or prefixed record numbers, e.g. A-000124
    return bool(re.search(r"\d{3,}", stripped))


def phi_context_hit(text: str) -> bool:
    """PHI_CONTEXT_PATTERN match that survives the code-reference filter."""
    for match in PHI_CONTEXT_PATTERN.finditer(text):
        if match.start() and text[match.start() - 1] in PHI_CODE_LEAD_CHARS:
            # A query parameter, attribute name or path segment addresses the
            # field; it does not carry a patient. Spelled out rather than shown,
            # because a literal example here is itself a PHI_CONTEXT_PATTERN hit
            # and the prior released scanner — the one CI runs to judge this
            # change — would refuse the commit that documents the narrowing.
            continue
        if PHI_CODE_CALL_VALUE.match(match.group(1)):
            # The value side is a call expression, not stored data. Real case that
            # forced this narrowing: a clinic page assigns the field from a prompt
            # whose label is the field name in Chinese, so the captured value was
            # read as a personal name and every commit touching that file blocked.
            continue
        if phi_value_is_patient_data(match.group(1)):
            return True
    return False

PUBLIC_PRIVATE_MARKERS = (
    "internal" + " only",
    "confiden" + "tial",
    "private" + " draft",
    "not for" + " distribution",
)

LFS_VERSION_ALIASES = {
    "https://git-lfs.github.com/spec/v1",
    "https://hawser.github.com/spec/v1",
    "http://git-media.io/v/2",
}
LFS_KEY_RE = re.compile(r"[a-z0-9.-]+")
LFS_OID_RE = re.compile(r"sha256:([0-9a-f]{64})")
LFS_EXTENSION_RE = re.compile(r"ext-[0-9]-[A-Za-z0-9_]+")

MAGIC_RULES: list[tuple[bytes, int, str, str]] = [
    (b"\x7fELF", 0, "executable-elf", "application/x-elf"),
    (b"MZ", 0, "executable-pe", "application/vnd.microsoft.portable-executable"),
    (b"\xcf\xfa\xed\xfe", 0, "executable-mach-o", "application/x-mach-binary"),
    (b"\xfe\xed\xfa\xcf", 0, "executable-mach-o", "application/x-mach-binary"),
    (b"\xca\xfe\xba\xbe", 0, "java-class-or-fat-mach-o", "application/octet-stream"),
    (b"\x00asm", 0, "webassembly", "application/wasm"),
    (b"%PDF-", 0, "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", 0, "image-png", "image/png"),
    (b"\xff\xd8\xff", 0, "image-jpeg", "image/jpeg"),
    (b"GIF87a", 0, "image-gif", "image/gif"),
    (b"GIF89a", 0, "image-gif", "image/gif"),
    (b"RIFF", 0, "riff-container", "application/octet-stream"),
    (b"fLaC", 0, "audio-flac", "audio/flac"),
    (b"OggS", 0, "ogg-container", "application/ogg"),
    (b"ID3", 0, "audio-mp3", "audio/mpeg"),
    (b"PK\x03\x04", 0, "zip", "application/zip"),
    (b"PK\x05\x06", 0, "zip", "application/zip"),
    (b"\x1f\x8b", 0, "gzip", "application/gzip"),
    (b"BZh", 0, "bzip2", "application/x-bzip2"),
    (b"\xfd7zXZ\x00", 0, "xz", "application/x-xz"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7z", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", 0, "rar", "application/vnd.rar"),
    (b"SQLite format 3\x00", 0, "sqlite", "application/vnd.sqlite3"),
    (b"\x89HDF\r\n\x1a\n", 0, "hdf5", "application/x-hdf5"),
    (b"PAR1", 0, "parquet", "application/vnd.apache.parquet"),
    (b"NUMPY", 1, "numpy", "application/x-npy"),
    (b"GGUF", 0, "model-gguf", "application/octet-stream"),
    (b"OTTO", 0, "font-opentype", "font/otf"),
    (b"\x00\x01\x00\x00", 0, "font-truetype", "font/ttf"),
    (b"wOFF", 0, "font-woff", "font/woff"),
    (b"wOF2", 0, "font-woff2", "font/woff2"),
]


class Budget:
    def __init__(self, *, max_members: int, max_unpacked: int):
        self.max_members = max_members
        self.max_unpacked = max_unpacked
        self.members = 0
        self.unpacked = 0

    def charge(self, size: int) -> None:
        self.members += 1
        self.unpacked += max(0, size)
        if self.members > self.max_members:
            raise GuardError(f"archive member limit exceeded ({self.max_members})")
        if self.unpacked > self.max_unpacked:
            raise GuardError(f"archive unpacked-byte limit exceeded ({self.max_unpacked})")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_github_repository(url: str) -> str | None:
    value = url.strip()
    patterns = [
        r"^(?:ssh://)?git@github\.com[:/](?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
        r"^https?://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
        r"^git://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value, re.I)
        if match:
            return match.group("repo").removesuffix(".git")
    return None


def remote_url_receipt(url: str | None) -> Mapping[str, Any] | None:
    if not url:
        return None
    repository = parse_github_repository(url)
    parsed = urllib.parse.urlparse(url)
    if not repository and parsed.hostname and parsed.hostname.lower() == "github.com":
        candidate = parsed.path.strip("/").removesuffix(".git")
        if candidate.count("/") == 1:
            repository = candidate
    payload: dict[str, Any] = {"sha256": sha256_bytes(url.encode("utf-8", "surrogateescape"))}
    if repository:
        payload["repository"] = redact_metadata_value(repository, "repository")
        payload["transport"] = "github"
    else:
        payload["transport"] = parsed.scheme or "local-or-scp"
        if parsed.hostname:
            payload["host"] = redact_metadata_value(parsed.hostname, "remote-host")
    return payload


def normalize_repo_path(path: str) -> str:
    normalized = unicodedata.normalize("NFC", path.replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path)
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def suffixes_lower(path: str) -> list[str]:
    return [suffix.lower() for suffix in pathlib.PurePosixPath(path).suffixes]


def likely_binary_extension(path: str) -> str | None:
    for suffix in reversed(suffixes_lower(path)):
        if suffix in FORBIDDEN_PAYLOAD_EXTENSIONS:
            return suffix
    return None


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    placeholders = ("example", "sample", "dummy", "changeme", "replace", "redacted", "placeholder", "test", "xxxx", "****")
    return any(token in lowered for token in placeholders) or "${" in value or "{{" in value


# A secret READ from the environment is the behaviour Law requires, and the
# generic-assignment pattern flags it: `password = os.environ.get(` captures
# "os.environ.get(" whose Shannon entropy is 3.37, over the 3.0 threshold. A
# guard that blocks the correct idiom teaches people to avoid it — measured
# 2026-08-23 when it refused a script whose only sin was reading GARMIN_PASSWORD
# out of the environment.
CODE_REFERENCE_PREFIXES = (
    "os.environ", "os.getenv", "getenv(", "environ[", "environ.get",
    "process.env", "config.", "settings.", "self.", "args.", "opts.",
    "secrets.get", "keyring.", "input(", "getpass",
    # 2026-08-26: the same defect, one language over. The Python idiom was fixed
    # above and the JavaScript one was not, so a Cloudflare Worker reading
    # `secret: context.env.ACCESS_SESSION_SECRET` blocked every commit that touched
    # the file. Measured on personal-website-s: nine hits across four files, all nine
    # of them an environment or object read, not one of them a credential. The repo
    # was simply uncommittable until this landed.
    "context.env", "ctx.env", "env.", "import.meta.env", "deno.env",
    "globalthis.", "window.", "locals.", "runtime.env", "platform.env",
)


def looks_like_code_reference(value: str) -> bool:
    """True when the captured 'value' is an expression that FETCHES a secret."""
    lowered = value.strip().lower()
    if lowered.startswith(CODE_REFERENCE_PREFIXES):
        return True
    # A call expression — `str(uuid.uuid4())`, `secrets.token_hex(16)` — is a
    # value the program GENERATES, never a credential someone typed. Measured
    # 2026-08-23: `token = str(uuid.uuid4())`, a lease id, blocked a commit.
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\(", value.strip()):
        return True
    # A value that begins with "(" is a parenthesised expression, not a typed
    # credential — a ternary or a boolean guard assigned to a secret-named field.
    # The capture stops at the first space, so only the opening fragment is visible
    # here; "starts with an open paren" is the whole signal available.
    #   Known gap: a real secret whose literal first character is "(" slips this filter.
    #   Accepted because the provider-specific and base64 rules still see it, and because
    #   the alternative measured on 2026-08-25 was worse: the false positive on a lab-code
    #   palette line got "fixed" by inserting a space to dodge the regex, which trains
    #   people to evade the scanner and invites the next reader to tidy the space away.
    if value.strip().startswith("("):
        return True
    # Optional chaining is syntax, so a value containing "?." is an expression the
    # program evaluates and can never be a credential someone typed. This is what
    # `keyRing?.[cohort.keyId]` and `tokens.hub?.token` are, and both blocked commits
    # on 2026-08-26. Deliberately narrower than "any dotted identifier": a bare
    # dotted chain of alphanumerics also describes a JWT, and admitting that shape
    # would let a hardcoded one through.
    if "?." in value:
        return True
    # a bare call or attribute chain — never a literal credential
    return lowered.endswith("(") and "." in lowered


def valid_taiwan_id(value: str) -> bool:
    mapping = {
        "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
        "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
        "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
        "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
    }
    if not re.fullmatch(r"[A-Z][12]\d{8}", value):
        return False
    code = mapping[value[0]]
    digits = [int(ch) for ch in value[1:]]
    checksum = code // 10 + (code % 10) * 9
    checksum += sum(digit * weight for digit, weight in zip(digits[:-1], range(8, 0, -1), strict=True))
    checksum += digits[-1]
    return checksum % 10 == 0


def metadata_sensitive_codes(value: str) -> list[str]:
    """Classify sensitive Git metadata without ever returning the matched value."""
    codes = [code for code, pattern in SECRET_PATTERNS if pattern.search(value)]
    for match in GENERIC_SECRET_ASSIGNMENT.finditer(value):
        candidate_value = match.group(1)
        if (not looks_like_placeholder(candidate_value)
                and not looks_like_code_reference(candidate_value)
                and shannon_entropy(candidate_value) >= 3.0):
            codes.append("SECRET_GENERIC_ASSIGNMENT")
            break
    if any(valid_taiwan_id(match.group(0)) for match in TAIWAN_ID_PATTERN.finditer(value)):
        codes.append("PII_TAIWAN_NATIONAL_ID")
    if phi_context_hit(value):
        codes.append("PHI_CONTEXT_FIELD")
    return sorted(set(codes))


def redact_metadata_value(value: str, label: str) -> str:
    if not metadata_sensitive_codes(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return f"[redacted-{label}:{digest}]"


def decode_text(data: bytes) -> tuple[str | None, str | None]:
    if not data:
        return "", "utf-8"
    bom_encodings = [
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ]
    for bom, encoding in bom_encodings:
        if data.startswith(bom):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                return None, None
    if b"\x00" in data:
        return None, None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t\f\b")
    if text and controls / len(text) > 0.01:
        return None, None
    return text, "utf-8"


def parse_lfs_pointer(data: bytes) -> tuple[str, int] | None:
    if not data or len(data) >= 1024 or b"\r" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.endswith("\n"):
        return None
    pairs: list[tuple[str, str]] = []
    for line in text[:-1].split("\n"):
        key, separator, value = line.partition(" ")
        if not separator or not value or " " in value or not LFS_KEY_RE.fullmatch(key):
            return None
        pairs.append((key, value))
    if not pairs or pairs[0][0] != "version" or pairs[0][1] not in LFS_VERSION_ALIASES:
        return None
    values = {key: value for key, value in pairs[1:] if key in {"oid", "size"}}
    oid_match = LFS_OID_RE.fullmatch(values.get("oid", ""))
    if not oid_match or not values.get("size", "").isdigit():
        return None
    for key, value in pairs[1:]:
        if key in {"oid", "size"}:
            continue
        if not LFS_EXTENSION_RE.fullmatch(key) or not LFS_OID_RE.fullmatch(value):
            return None
    return oid_match.group(1), int(values["size"])


def classify(path: str, data: bytes) -> Classification:
    lfs = parse_lfs_pointer(data)
    if lfs:
        oid, size = lfs
        return Classification(
            kind="git-lfs-pointer",
            mime="text/plain",
            is_text=True,
            encoding="ascii",
            lfs_oid=oid,
            lfs_size=size,
        )

    if b"%PDF-" in data[:1024]:
        return Classification("pdf", "application/pdf", False)

    if len(data) >= 132 and data[128:132] == b"DICM":
        return Classification("dicom", "application/dicom", False)

    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].decode("ascii", "replace")
        return Classification("iso-base-media", "video/mp4", False, detail=f"brand={brand}")

    if len(data) >= 262 and data[257:262] == b"ustar":
        return Classification("tar", "application/x-tar", False)

    for magic, offset, kind, mime in MAGIC_RULES:
        if len(data) >= offset + len(magic) and data[offset : offset + len(magic)] == magic:
            if kind == "riff-container" and len(data) >= 12:
                form = data[8:12]
                if form == b"WAVE":
                    return Classification("audio-wav", "audio/wav", False)
                if form == b"WEBP":
                    return Classification("image-webp", "image/webp", False)
                if form == b"AVI ":
                    return Classification("video-avi", "video/x-msvideo", False)
            if kind == "zip":
                return classify_zip(data)
            return Classification(kind, mime, False)

    text, encoding = decode_text(data)
    if text is not None:
        return Classification("text", "text/plain", True, encoding=encoding)

    extension = likely_binary_extension(path)
    if extension:
        return Classification(f"binary{extension}", "application/octet-stream", False)
    return Classification("binary-unknown", "application/octet-stream", False)


def classify_zip(data: bytes) -> Classification:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return Classification("zip-corrupt", "application/zip", False)
    if "[Content_Types].xml" in names:
        if any(name.startswith("word/") for name in names):
            return Classification("office-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", False)
        if any(name.startswith("xl/") for name in names):
            return Classification("office-xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False)
        if any(name.startswith("ppt/") for name in names):
            return Classification("office-pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", False)
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                mime = archive.read("mimetype")[:128].decode("ascii", "replace").strip()
            if mime == "application/epub+zip":
                return Classification("epub", "application/epub+zip", False)
        except Exception:
            pass
    if "AndroidManifest.xml" in names and "classes.dex" in names:
        return Classification("android-apk", "application/vnd.android.package-archive", False)
    if "META-INF/MANIFEST.MF" in names:
        return Classification("java-jar", "application/java-archive", False)
    return Classification("zip", "application/zip", False)


def inspect_path(artifact: Artifact, policy: Policy) -> list[Finding]:
    path = normalize_repo_path(artifact.path)
    findings: list[Finding] = []
    parts = pathlib.PurePosixPath(path).parts

    if metadata_sensitive_codes(path):
        findings.append(Finding("SENSITIVE_PATH_METADATA", Severity.BLOCK, path, "Repository path contains sensitive metadata; value is redacted.", artifact.blob_sha, artifact.commit))

    if path.startswith("/") or ".." in parts:
        findings.append(Finding("PATH_UNSAFE", Severity.BLOCK, path, "Repository path is absolute or contains '..'.", artifact.blob_sha, artifact.commit))

    if path != unicodedata.normalize("NFC", path):
        findings.append(Finding("PATH_UNICODE_NOT_NFC", Severity.BLOCK, path, "Path is not Unicode NFC-normalized.", artifact.blob_sha, artifact.commit))

    if any(component in CACHE_COMPONENTS for component in parts):
        findings.append(Finding("CACHE_OR_BUILD_TREE", Severity.BLOCK, path, "Runtime cache, dependency tree, or generated build tree must not be committed.", artifact.blob_sha, artifact.commit))

    if path_matches(path, SECRET_PATH_GLOBS):
        findings.append(Finding("CREDENTIAL_PATH", Severity.BLOCK, path, "Credential-bearing path is forbidden even when content appears to be text.", artifact.blob_sha, artifact.commit))

    if path_matches(path, policy.extra_deny_path_globs):
        findings.append(Finding("POLICY_DENY_PATH", Severity.BLOCK, path, "Path matches a repository-specific deny rule.", artifact.blob_sha, artifact.commit))

    if parts and parts[0] == "agent-share":
        findings.append(Finding("RETIRED_CORPUS_PATH", Severity.BLOCK, path, "The retired agent-share corpus path must not enter Git.", artifact.blob_sha, artifact.commit))

    if parts and parts[0] == "corpus" and not artifact.is_symlink:
        findings.append(Finding("CORPUS_PAYLOAD_IN_GIT", Severity.BLOCK, path, "Canonical corpus material must remain outside Git.", artifact.blob_sha, artifact.commit))

    extension = likely_binary_extension(path)
    if extension:
        findings.append(
            Finding(
                "FORBIDDEN_PAYLOAD_EXTENSION",
                Severity.BLOCK,
                path,
                f"Payload extension {extension} is outside the text/code Git boundary.",
                artifact.blob_sha,
                artifact.commit,
                safe_details={"extension": extension},
            )
        )

    basename = pathlib.PurePosixPath(path).name
    if basename in {".DS_Store", "Thumbs.db", "desktop.ini"} or basename.startswith("._"):
        findings.append(Finding("OS_METADATA", Severity.BLOCK, path, "Operating-system metadata file must not be committed.", artifact.blob_sha, artifact.commit))

    return findings


def inspect_symlink(artifact: Artifact, data: bytes, policy: Policy) -> list[Finding]:
    path = normalize_repo_path(artifact.path)
    try:
        target = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding("SYMLINK_TARGET_INVALID", Severity.BLOCK, path, "Symlink target is not UTF-8 text.", artifact.blob_sha, artifact.commit)]
    target = target.strip()
    if "\x00" in target or not target:
        return [Finding("SYMLINK_TARGET_INVALID", Severity.BLOCK, path, "Symlink target is empty or malformed.", artifact.blob_sha, artifact.commit)]

    if metadata_sensitive_codes(target):
        return [Finding("SENSITIVE_SYMLINK_TARGET", Severity.BLOCK, path, "Symlink target contains sensitive metadata; value is redacted.", artifact.blob_sha, artifact.commit)]

    if path_matches(path, policy.allow_external_symlink_globs):
        return []

    pure = pathlib.PurePosixPath(target)
    if pure.is_absolute():
        return [Finding("SYMLINK_EXTERNAL", Severity.BLOCK, path, "Absolute symlink target is not allowlisted.", artifact.blob_sha, artifact.commit)]
    parent = pathlib.PurePosixPath(path).parent
    resolved_parts: list[str] = []
    for part in (parent / pure).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved_parts:
                return [Finding("SYMLINK_EXTERNAL", Severity.BLOCK, path, "Symlink escapes the repository and is not allowlisted.", artifact.blob_sha, artifact.commit)]
            resolved_parts.pop()
        else:
            resolved_parts.append(part)
    return []


def inspect_text(path: str, text: str, artifact: Artifact, visibility: str | None) -> list[Finding]:
    findings: list[Finding] = []
    for code, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(code, Severity.BLOCK, path, "Potential credential material detected; matched value is redacted.", artifact.blob_sha, artifact.commit))

    for match in GENERIC_SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1)
        if (not looks_like_placeholder(value)
                and not looks_like_code_reference(value)
                and shannon_entropy(value) >= 3.0):
            findings.append(Finding("SECRET_GENERIC_ASSIGNMENT", Severity.BLOCK, path, "High-entropy value assigned to a secret-like field; matched value is redacted.", artifact.blob_sha, artifact.commit))
            break

    for match in TAIWAN_ID_PATTERN.finditer(text):
        if valid_taiwan_id(match.group(0)):
            findings.append(Finding("PII_TAIWAN_NATIONAL_ID", Severity.BLOCK, path, "Taiwan national identification number shape with valid checksum detected; value is redacted.", artifact.blob_sha, artifact.commit))
            break

    if phi_context_hit(text):
        findings.append(Finding("PHI_CONTEXT_FIELD", Severity.BLOCK, path, "Patient/medical-record identifying field detected; value is redacted.", artifact.blob_sha, artifact.commit))

    # Binary payloads are sometimes hidden in text as long base64 literals. Decode
    # only bounded candidates, never include the encoded or decoded value in output.
    for index, match in enumerate(BASE64_PAYLOAD_PATTERN.finditer(text)):
        if index >= 20:
            findings.append(Finding("ENCODED_PAYLOAD_SCAN_LIMIT", Severity.BLOCK, path, "More than 20 large base64-shaped payloads were found; inspection stopped fail-closed.", artifact.blob_sha, artifact.commit))
            break
        encoded = match.group(0)
        estimated = (len(encoded) * 3) // 4
        if estimated > MAX_EMBEDDED_DECODE_BYTES:
            findings.append(Finding("ENCODED_PAYLOAD_TOO_LARGE", Severity.BLOCK, path, "Large base64-shaped payload exceeds the bounded decoder and is blocked.", artifact.blob_sha, artifact.commit, safe_details={"encoded_chars": len(encoded), "decoded_estimate": estimated}))
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error):
            continue
        if len(decoded) < 384:
            continue
        embedded = classify(f"{path}.decoded", decoded)
        if not embedded.is_text:
            findings.append(Finding("ENCODED_BINARY_PAYLOAD", Severity.BLOCK, path, "Base64 text contains a decoded binary payload; values are redacted.", artifact.blob_sha, artifact.commit, safe_details={"kind": embedded.kind, "mime": embedded.mime, "decoded_bytes": len(decoded)}))
            break

    if "-----BEGIN CERTIFICATE-----" in text and "PRIVATE KEY" not in text:
        findings.append(Finding("CERTIFICATE_MATERIAL", Severity.WARNING, path, "Certificate material is present; verify that no corresponding private key or private identity is embedded.", artifact.blob_sha, artifact.commit))

    if visibility == "public" and any(marker in text.casefold() for marker in PUBLIC_PRIVATE_MARKERS):
        findings.append(Finding("PUBLIC_REPO_PRIVATE_MARKER", Severity.BLOCK, path, "Repository visibility policy marker detected in a public repository change.", artifact.blob_sha, artifact.commit))

    return findings


def is_archive_path_safe(name: str) -> bool:
    pure = pathlib.PurePosixPath(name.replace("\\", "/"))
    return not pure.is_absolute() and ".." not in pure.parts and "\x00" not in name


def inspect_archive(
    path: str,
    data: bytes,
    artifact: Artifact,
    policy: Policy,
    classification: Classification,
    *,
    depth: int = 0,
    budget: Budget | None = None,
) -> list[Finding]:
    if depth >= policy.limits.archive_max_depth:
        return [Finding("ARCHIVE_DEPTH_LIMIT", Severity.BLOCK, path, "Archive nesting exceeds the configured inspection depth.", artifact.blob_sha, artifact.commit)]
    budget = budget or Budget(max_members=policy.limits.archive_max_members, max_unpacked=policy.limits.archive_max_unpacked_bytes)
    findings: list[Finding] = []
    try:
        if classification.kind in {"zip", "office-docx", "office-xlsx", "office-pptx", "epub", "java-jar", "android-apk"}:
            findings.extend(_inspect_zip(path, data, artifact, policy, depth, budget))
        elif classification.kind == "tar":
            findings.extend(_inspect_tar(path, data, artifact, policy, depth, budget))
        elif classification.kind == "gzip":
            unpacked = _bounded_decompress_gzip(data, policy.limits.archive_max_unpacked_bytes)
            budget.charge(len(unpacked))
            findings.extend(_inspect_inner_bytes(path, "<gzip-member>", unpacked, artifact, policy, depth + 1, budget))
        elif classification.kind == "bzip2":
            unpacked = _bounded_decompress_bz2(data, policy.limits.archive_max_unpacked_bytes)
            budget.charge(len(unpacked))
            findings.extend(_inspect_inner_bytes(path, "<bzip2-member>", unpacked, artifact, policy, depth + 1, budget))
        elif classification.kind == "xz":
            unpacked = _bounded_decompress_xz(data, policy.limits.archive_max_unpacked_bytes)
            budget.charge(len(unpacked))
            findings.extend(_inspect_inner_bytes(path, "<xz-member>", unpacked, artifact, policy, depth + 1, budget))
        elif classification.kind in {"7z", "rar"}:
            findings.append(Finding("ARCHIVE_UNSUPPORTED", Severity.BLOCK, path, "Archive format cannot be safely inspected by the standard-library scanner.", artifact.blob_sha, artifact.commit, safe_details={"kind": classification.kind}))
    except (GuardError, OSError, EOFError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        findings.append(Finding("ARCHIVE_INSPECTION_FAILED", Severity.BLOCK, path, f"Archive inspection failed closed: {type(exc).__name__}.", artifact.blob_sha, artifact.commit))
    return findings


def _inspect_zip(path: str, data: bytes, artifact: Artifact, policy: Policy, depth: int, budget: Budget) -> list[Finding]:
    findings: list[Finding] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > policy.limits.archive_max_members:
            raise GuardError("archive member count exceeds limit")
        for info in infos:
            if info.is_dir():
                continue
            inner = info.filename
            if not is_archive_path_safe(inner):
                findings.append(Finding("ARCHIVE_PATH_TRAVERSAL", Severity.BLOCK, path, "Archive contains an absolute or parent-traversing member path.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            if info.flag_bits & 0x1:
                findings.append(Finding("ARCHIVE_ENCRYPTED_MEMBER", Severity.BLOCK, path, "Encrypted archive member cannot be inspected.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                findings.append(Finding("ARCHIVE_LINK_MEMBER", Severity.BLOCK, path, "Archive contains a symlink member.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            budget.charge(info.file_size)
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > policy.limits.archive_max_ratio and info.file_size > 1 * MIB:
                findings.append(Finding("ARCHIVE_COMPRESSION_BOMB", Severity.BLOCK, path, "Archive member has an excessive expansion ratio.", artifact.blob_sha, artifact.commit, inner_path=inner, safe_details={"ratio": round(ratio, 2), "unpacked_bytes": info.file_size}))
                continue
            if info.file_size > policy.limits.archive_member_scan_bytes:
                findings.append(Finding("ARCHIVE_MEMBER_TOO_LARGE", Severity.BLOCK, path, "Archive member exceeds the bounded inspection size.", artifact.blob_sha, artifact.commit, inner_path=inner, safe_details={"unpacked_bytes": info.file_size}))
                continue
            with archive.open(info, "r") as member:
                payload = member.read(policy.limits.archive_member_scan_bytes + 1)
            if len(payload) > policy.limits.archive_member_scan_bytes:
                findings.append(Finding("ARCHIVE_MEMBER_TOO_LARGE", Severity.BLOCK, path, "Archive member exceeded inspection size while reading.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            findings.extend(_inspect_inner_bytes(path, inner, payload, artifact, policy, depth + 1, budget))
    return findings


def _inspect_tar(path: str, data: bytes, artifact: Artifact, policy: Policy, depth: int, budget: Budget) -> list[Finding]:
    findings: list[Finding] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > policy.limits.archive_max_members:
            raise GuardError("archive member count exceeds limit")
        for member in members:
            if not member.isfile():
                if member.issym() or member.islnk():
                    findings.append(Finding("ARCHIVE_LINK_MEMBER", Severity.BLOCK, path, "Archive contains a symlink or hard-link member.", artifact.blob_sha, artifact.commit, inner_path=member.name))
                continue
            inner = member.name
            if not is_archive_path_safe(inner):
                findings.append(Finding("ARCHIVE_PATH_TRAVERSAL", Severity.BLOCK, path, "Archive contains an absolute or parent-traversing member path.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            budget.charge(member.size)
            if member.size > policy.limits.archive_member_scan_bytes:
                findings.append(Finding("ARCHIVE_MEMBER_TOO_LARGE", Severity.BLOCK, path, "Archive member exceeds the bounded inspection size.", artifact.blob_sha, artifact.commit, inner_path=inner, safe_details={"unpacked_bytes": member.size}))
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                findings.append(Finding("ARCHIVE_MEMBER_UNREADABLE", Severity.BLOCK, path, "Archive member could not be read.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            payload = extracted.read(policy.limits.archive_member_scan_bytes + 1)
            if len(payload) > policy.limits.archive_member_scan_bytes:
                findings.append(Finding("ARCHIVE_MEMBER_TOO_LARGE", Severity.BLOCK, path, "Archive member exceeded inspection size while reading.", artifact.blob_sha, artifact.commit, inner_path=inner))
                continue
            findings.extend(_inspect_inner_bytes(path, inner, payload, artifact, policy, depth + 1, budget))
    return findings


def _inspect_inner_bytes(path: str, inner: str, payload: bytes, artifact: Artifact, policy: Policy, depth: int, budget: Budget) -> list[Finding]:
    findings: list[Finding] = []
    member_artifact = dataclasses.replace(artifact, path=inner, size=len(payload))
    for finding in inspect_path(member_artifact, policy):
        findings.append(dataclasses.replace(finding, path=path, inner_path=inner))
    inner_class = classify(inner, payload)
    if inner_class.is_text:
        text, _ = decode_text(payload)
        if text is not None:
            for finding in inspect_text(path, text, artifact, policy.visibility):
                findings.append(dataclasses.replace(finding, inner_path=inner))
    else:
        findings.append(Finding("ARCHIVE_BINARY_MEMBER", Severity.BLOCK, path, "Archive contains a binary member.", artifact.blob_sha, artifact.commit, inner_path=inner, safe_details={"kind": inner_class.kind, "mime": inner_class.mime}))
        if inner_class.kind in {"zip", "office-docx", "office-xlsx", "office-pptx", "epub", "java-jar", "android-apk", "tar", "gzip", "bzip2", "xz", "7z", "rar"}:
            findings.extend(inspect_archive(path, payload, artifact, policy, inner_class, depth=depth, budget=budget))
    return findings


def _bounded_decompress_gzip(data: bytes, max_bytes: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
        result = stream.read(max_bytes + 1)
    if len(result) > max_bytes:
        raise GuardError("gzip output exceeds limit")
    return result


def _bounded_decompress_bz2(data: bytes, max_bytes: int) -> bytes:
    decompressor = bz2.BZ2Decompressor()
    result = decompressor.decompress(data, max_length=max_bytes + 1)
    if len(result) > max_bytes or not decompressor.eof:
        raise GuardError("bzip2 output exceeds limit or is incomplete")
    return result


def _bounded_decompress_xz(data: bytes, max_bytes: int) -> bytes:
    decompressor = lzma.LZMADecompressor()
    result = decompressor.decompress(data, max_length=max_bytes + 1)
    if len(result) > max_bytes or not decompressor.eof:
        raise GuardError("xz output exceeds limit or is incomplete")
    return result


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot read JSON {path}: {exc}") from exc


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_roster(repo_root: pathlib.Path, explicit: str | None) -> pathlib.Path | None:
    candidates: list[pathlib.Path] = []
    if explicit:
        candidates.append(pathlib.Path(explicit).expanduser())
    env = os.environ.get("COPPER_REPO_ROSTER")
    if env:
        candidates.append(pathlib.Path(env).expanduser())
    candidates.extend([
        pathlib.Path.home() / "repos" / "repo-roster.json",
        pathlib.Path.home() / "repos" / "_law" / "repo-roster.json",
        repo_root.parent / "repo-roster.json",
        repo_root.parent / "_law" / "repo-roster.json",
    ])
    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def deep_get(mapping: Mapping[str, Any], path: Sequence[str], default: Any) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def load_policy(git: Git, args: argparse.Namespace) -> tuple[Policy, list[Finding]]:
    repo_root = git.root
    findings: list[Finding] = []
    config_path: pathlib.Path | None
    if args.config:
        config_path = pathlib.Path(args.config).expanduser().resolve()
    else:
        candidate = repo_root / DEFAULT_CONFIG
        config_path = candidate if candidate.is_file() else None

    config: dict[str, Any] = {}
    if config_path:
        try:
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise GuardError(f"cannot load policy {config_path}: {exc}") from exc

    policy = Policy(repo_root=repo_root, config_path=config_path)
    observed_repository = git.remote_repository()
    configured_repository = str(config.get("repository")) if config.get("repository") else None
    configured_visibility = str(config.get("visibility")) if config.get("visibility") else None
    policy.repository = configured_repository or observed_repository
    policy.visibility = configured_visibility
    if observed_repository and configured_repository and configured_repository != observed_repository:
        findings.append(Finding("POLICY_REPOSITORY_MISMATCH", Severity.ERROR, DEFAULT_CONFIG, "Configured repository identity does not match the Git origin."))
        policy.repository = observed_repository
    expected_repository = args.expected_repository or os.environ.get("COPPER_GIT_GUARD_EXPECTED_REPOSITORY")
    expected_visibility = args.expected_visibility or os.environ.get("COPPER_GIT_GUARD_EXPECTED_VISIBILITY")
    if expected_repository:
        if policy.repository and policy.repository != expected_repository:
            findings.append(Finding("POLICY_REPOSITORY_MISMATCH", Severity.ERROR, DEFAULT_CONFIG, "Configured or observed repository identity does not match the trusted runtime identity."))
        policy.repository = expected_repository
    if expected_visibility:
        if expected_visibility not in {"public", "private", "internal"}:
            findings.append(Finding("POLICY_VISIBILITY_INVALID", Severity.ERROR, DEFAULT_CONFIG, "Trusted runtime visibility has an unsupported value."))
        if configured_visibility and configured_visibility != expected_visibility:
            findings.append(Finding("POLICY_VISIBILITY_MISMATCH", Severity.ERROR, DEFAULT_CONFIG, "Configured visibility does not match the trusted runtime visibility."))
        policy.visibility = expected_visibility
    policy.require_roster = bool(config.get("require_roster", False)) or bool(args.require_roster)
    policy.fail_closed = bool(config.get("fail_closed", True))
    policy.warnings_as_errors = bool(config.get("warnings_as_errors", False)) or bool(args.warnings_as_errors)
    policy.binary_mode = str(config.get("binary_mode", "deny")).lower()
    policy.lfs_mode = str(config.get("lfs_mode", "deny")).lower()
    policy.external_gitleaks = str(config.get("external_gitleaks", "auto")).lower()
    if args.require_gitleaks:
        policy.external_gitleaks = "required"
    if args.no_gitleaks:
        policy.external_gitleaks = "off"
    policy.gitleaks_archive_depth = int(config.get("gitleaks_archive_depth", 2))
    policy.gitleaks_decode_depth = int(config.get("gitleaks_decode_depth", 2))
    policy.allow_external_symlink_globs = list(config.get("allow_external_symlink_globs", []))
    policy.allow_submodule_globs = list(config.get("allow_submodule_globs", []))
    policy.extra_deny_path_globs = list(config.get("deny_path_globs", []))

    limits = config.get("limits", {}) if isinstance(config.get("limits", {}), Mapping) else {}
    for field_info in dataclasses.fields(Limits):
        if field_info.name in limits:
            setattr(policy.limits, field_info.name, type(getattr(policy.limits, field_info.name))(limits[field_info.name]))

    if not policy.fail_closed:
        findings.append(Finding("POLICY_WEAKENING_REJECTED", Severity.ERROR, DEFAULT_CONFIG, "fail_closed must remain true for the Git transport boundary."))
    if policy.binary_mode != "deny":
        findings.append(Finding("POLICY_WEAKENING_REJECTED", Severity.ERROR, DEFAULT_CONFIG, "Current fleet authority permits text/code only; binary_mode must remain 'deny'."))
    if policy.lfs_mode != "deny":
        findings.append(Finding("POLICY_WEAKENING_REJECTED", Severity.ERROR, DEFAULT_CONFIG, "Git LFS is not an automatic exception to the text/code-only boundary; lfs_mode must remain 'deny'."))
    if policy.external_gitleaks not in {"off", "auto", "required"}:
        findings.append(Finding("POLICY_INVALID", Severity.ERROR, DEFAULT_CONFIG, "external_gitleaks must be off, auto, or required."))

    roster = find_roster(repo_root, args.roster)
    policy.roster_path = roster
    if roster:
        policy.roster_fingerprint = file_sha256(roster)
        data = load_json(roster)
        devices = data.get("devices", {}) if isinstance(data, Mapping) else {}
        if isinstance(devices, Mapping) and policy.host in devices and isinstance(devices[policy.host], Mapping):
            policy.device_binary_at_rest = devices[policy.host].get("binary_at_rest")
        repositories = data.get("repositories", []) if isinstance(data, Mapping) else []
        matches: list[Mapping[str, Any]] = []
        for entry in repositories if isinstance(repositories, list) else []:
            if not isinstance(entry, Mapping):
                continue
            github = entry.get("github") if isinstance(entry.get("github"), Mapping) else {}
            remote = github.get("repository") if isinstance(github, Mapping) else None
            local_path = entry.get("path")
            if observed_repository and remote == observed_repository:
                matches.append(entry)
            elif local_path and repo_root.name == local_path:
                matches.append(entry)
        unique = []
        seen_ids: set[int] = set()
        for entry in matches:
            if id(entry) not in seen_ids:
                seen_ids.add(id(entry))
                unique.append(entry)
        if len(unique) == 1:
            entry = unique[0]
            github = entry.get("github") if isinstance(entry.get("github"), Mapping) else {}
            policy.repository = github.get("repository") or policy.repository
            policy.visibility = github.get("visibility") or policy.visibility
            policy.write_policy = github.get("write_policy")
            policy.repo_status = entry.get("status")
        elif len(unique) > 1:
            findings.append(Finding("ROSTER_AMBIGUOUS", Severity.ERROR, str(roster), "Repository matches multiple roster entries."))
        elif policy.require_roster:
            findings.append(Finding("ROSTER_UNREGISTERED", Severity.ERROR, str(roster), "Current repository is absent from the fleet repository roster."))
    elif policy.require_roster:
        findings.append(Finding("ROSTER_UNAVAILABLE", Severity.ERROR, str(args.roster or "repo-roster.json"), "Fleet repository roster is required but unavailable."))

    if policy.repo_status and policy.repo_status != "active":
        findings.append(Finding("REPO_NOT_ACTIVE", Severity.BLOCK, ".", f"Repository roster status is {policy.repo_status!r}; ordinary writes are blocked."))
    if policy.write_policy and "read_only" in policy.write_policy:
        findings.append(Finding("REPO_READ_ONLY", Severity.BLOCK, ".", f"Repository write policy is {policy.write_policy!r}."))

    return policy, findings


def scan_artifacts(git: Git, artifacts: list[Artifact], policy: Policy, initial_findings: list[Finding]) -> tuple[list[Finding], dict[tuple[str, str], bytes], int]:
    findings = list(initial_findings)
    materialized: dict[tuple[str, str], bytes] = {}
    bytes_considered = 0

    # Detect collisions within the scan and against the complete resulting index/tree.
    path_keys: dict[str, str] = {}
    for artifact in artifacts:
        normalized = normalize_repo_path(artifact.path)
        key = unicodedata.normalize("NFC", normalized).casefold()
        previous = path_keys.get(key)
        if previous is not None and previous != normalized:
            findings.append(Finding("PATH_COLLISION", Severity.BLOCK, normalized, "Path collides case-insensitively or under Unicode normalization with another Git path.", artifact.blob_sha, artifact.commit))
        else:
            path_keys[key] = normalized
        if artifact.collision_with:
            findings.append(Finding("PATH_COLLISION", Severity.BLOCK, normalized, "Path collides case-insensitively or under Unicode normalization with an existing Git path.", artifact.blob_sha, artifact.commit))

    for artifact in artifacts:
        path = normalize_repo_path(artifact.path)
        bytes_considered += artifact.size
        findings.extend(inspect_path(artifact, policy))

        if artifact.is_submodule:
            if not path_matches(path, policy.allow_submodule_globs):
                findings.append(Finding("SUBMODULE_NOT_ALLOWLISTED", Severity.BLOCK, path, "Git submodule/gitlink is not allowlisted.", artifact.blob_sha, artifact.commit))
            continue

        if artifact.size > policy.limits.max_blob_bytes:
            findings.append(Finding("BLOB_TOO_LARGE", Severity.BLOCK, path, "Blob exceeds the configured maximum Git object size.", artifact.blob_sha, artifact.commit, safe_details={"bytes": artifact.size, "limit": policy.limits.max_blob_bytes}))
            continue

        if artifact.size > policy.limits.max_scan_bytes:
            findings.append(Finding("BLOB_SCAN_LIMIT", Severity.BLOCK, path, "Blob exceeds the bounded content-inspection size and is blocked fail-closed.", artifact.blob_sha, artifact.commit, safe_details={"bytes": artifact.size, "scan_limit": policy.limits.max_scan_bytes}))
            continue

        key = (path, artifact.blob_sha)
        data, truncated = git.read_object(artifact.blob_sha, artifact.object_type, policy.limits.max_scan_bytes)
        if truncated:
            findings.append(Finding("BLOB_SCAN_TRUNCATED", Severity.ERROR, path, "Blob inspection was truncated unexpectedly.", artifact.blob_sha, artifact.commit))
            continue
        materialized[key] = data

        if artifact.is_symlink:
            findings.extend(inspect_symlink(artifact, data, policy))
            continue

        classification = classify(path, data)
        if artifact.size > policy.limits.warn_blob_bytes:
            findings.append(Finding("BLOB_LARGE_WARNING", Severity.WARNING, path, "Blob exceeds the recommended small-object threshold.", artifact.blob_sha, artifact.commit, safe_details={"bytes": artifact.size, "threshold": policy.limits.warn_blob_bytes}))

        if classification.kind == "git-lfs-pointer":
            findings.append(Finding("GIT_LFS_FORBIDDEN", Severity.BLOCK, path, "Git LFS pointer represents an out-of-Git binary payload and is not an approved exception.", artifact.blob_sha, artifact.commit, safe_details={"lfs_oid": classification.lfs_oid, "lfs_size": classification.lfs_size}))
            continue

        if classification.is_text:
            if artifact.size > policy.limits.max_text_bytes:
                findings.append(Finding("TEXT_BLOB_TOO_LARGE", Severity.BLOCK, path, "Text blob exceeds the configured text/code size boundary.", artifact.blob_sha, artifact.commit, safe_details={"bytes": artifact.size, "limit": policy.limits.max_text_bytes}))
                continue
            text, _ = decode_text(data)
            if text is None:
                findings.append(Finding("TEXT_DECODE_FAILED", Severity.ERROR, path, "Text classification could not be decoded deterministically.", artifact.blob_sha, artifact.commit))
                continue
            findings.extend(inspect_text(path, text, artifact, policy.visibility))
        else:
            findings.append(Finding("BINARY_FORBIDDEN", Severity.BLOCK, path, "Binary payload is outside the fleet text/code-only Git boundary.", artifact.blob_sha, artifact.commit, safe_details={"kind": classification.kind, "mime": classification.mime, "bytes": artifact.size}))
            if policy.device_binary_at_rest == "forbidden":
                findings.append(Finding("DEVICE_BINARY_AT_REST_FORBIDDEN", Severity.BLOCK, path, f"Host {policy.host!r} is rostered as binary-at-rest forbidden.", artifact.blob_sha, artifact.commit))
            if classification.kind == "dicom":
                findings.append(Finding("PHI_RISK_DICOM", Severity.BLOCK, path, "DICOM payload is presumed to carry patient-identifying metadata unless proven otherwise; Git transport is forbidden.", artifact.blob_sha, artifact.commit))
            if classification.kind in {"zip", "office-docx", "office-xlsx", "office-pptx", "epub", "java-jar", "android-apk", "tar", "gzip", "bzip2", "xz", "7z", "rar"}:
                findings.extend(inspect_archive(path, data, artifact, policy, classification))

    return dedupe_findings(findings), materialized, bytes_considered


def dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple[Any, ...]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.severity, finding.path, finding.blob_sha, finding.commit, finding.inner_path, finding.message)
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return sorted(result, key=lambda f: (-int(f.severity), f.path, f.inner_path or "", f.code, f.commit or ""))


def command_version(command: str, args: Sequence[str]) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    cp = subprocess.run([executable, *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    first = cp.stdout.strip().splitlines()
    return first[0][:200] if first else f"{command}:exit={cp.returncode}"


def run_gitleaks(materialized: Mapping[tuple[str, str], bytes], policy: Policy) -> tuple[list[Finding], str | None]:
    executable = shutil.which("gitleaks")
    if not executable:
        if policy.external_gitleaks == "required":
            return [Finding("GITLEAKS_REQUIRED", Severity.ERROR, ".", "gitleaks is required for this gate but is not installed or not on PATH.", scanner="gitleaks")], None
        if policy.external_gitleaks == "auto":
            return [Finding("GITLEAKS_UNAVAILABLE", Severity.WARNING, ".", "gitleaks is unavailable; built-in redacted secret scanning still ran.", scanner="gitleaks")], None
        return [], None

    version = command_version("gitleaks", ["version"])
    with tempfile.TemporaryDirectory(prefix="copper-git-guard-") as temp_name:
        temp_root = pathlib.Path(temp_name)
        os.chmod(temp_root, 0o700)
        input_root = temp_root / "input"
        input_root.mkdir(mode=0o700)
        mapping: dict[str, tuple[str, str]] = {}
        for index, ((path, sha), data) in enumerate(materialized.items()):
            # Historical paths cannot share one real filesystem topology: a file
            # named "node" may later become "node/child". Use an opaque flat name
            # and retain the original identity only in the in-memory map.
            basename = pathlib.PurePosixPath(normalize_repo_path(path)).name
            safe_basename = re.sub(r"[^A-Za-z0-9._-]", "_", basename)[:80] or "blob"
            relative = f"objects/{index:06d}-{sha[:12]}-{safe_basename}"
            destination = input_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            os.chmod(destination, 0o600)
            mapping[str(destination.resolve())] = (path, sha)

        report = temp_root / "gitleaks.json"
        cmd = [
            executable,
            "dir",
            "--no-banner",
            "--redact",
            "--exit-code", "1",
            "--report-format", "json",
            "--report-path", str(report),
            "--max-archive-depth", str(policy.gitleaks_archive_depth),
            "--max-decode-depth", str(policy.gitleaks_decode_depth),
            str(input_root),
        ]
        cp = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if cp.returncode not in (0, 1):
            return [Finding("GITLEAKS_FAILED", Severity.ERROR, ".", f"gitleaks failed closed with exit code {cp.returncode}; output omitted to avoid leaking data.", scanner="gitleaks")], version
        if not report.exists():
            if cp.returncode == 1:
                return [Finding("GITLEAKS_REPORT_MISSING", Severity.ERROR, ".", "gitleaks reported findings but produced no machine-readable report.", scanner="gitleaks")], version
            return [], version
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [Finding("GITLEAKS_REPORT_INVALID", Severity.ERROR, ".", "gitleaks report is missing or invalid.", scanner="gitleaks")], version

        findings: list[Finding] = []
        if not isinstance(payload, list):
            return [Finding("GITLEAKS_REPORT_INVALID", Severity.ERROR, ".", "gitleaks report has an unexpected schema.", scanner="gitleaks")], version
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            reported_file = str(item.get("File", ""))
            physical_reported, separator, reported_inner = reported_file.partition("!")
            raw_reported = pathlib.Path(physical_reported)
            candidates = [raw_reported.resolve(strict=False)] if raw_reported.is_absolute() else [
                (input_root / raw_reported).resolve(strict=False),
                (temp_root / raw_reported).resolve(strict=False),
                raw_reported.resolve(strict=False),
            ]
            original = next((mapping[str(candidate)] for candidate in candidates if str(candidate) in mapping), None)
            if original is None:
                resolved = candidates[0]
                try:
                    relative = resolved.relative_to(input_root.resolve()).as_posix()
                except ValueError:
                    relative = "<unmapped>"
                path, sha = relative, None
            else:
                path, sha = original
            rule_id = str(item.get("RuleID", "unknown"))[:100]
            findings.append(Finding("GITLEAKS_SECRET", Severity.BLOCK, path, f"gitleaks detected a secret pattern ({rule_id}); secret value is redacted.", sha, inner_path=reported_inner if separator else None, scanner="gitleaks", safe_details={"rule_id": rule_id}))
        return findings, version


def policy_snapshot(policy: Policy) -> dict[str, Any]:
    return {
        "config_path": str(policy.config_path) if policy.config_path else None,
        "roster_path": str(policy.roster_path) if policy.roster_path else None,
        "roster_sha256": policy.roster_fingerprint,
        "repo_status": policy.repo_status,
        "write_policy": policy.write_policy,
        "device_binary_at_rest": policy.device_binary_at_rest,
        "require_roster": policy.require_roster,
        "fail_closed": policy.fail_closed,
        "warnings_as_errors": policy.warnings_as_errors,
        "binary_mode": policy.binary_mode,
        "lfs_mode": policy.lfs_mode,
        "external_gitleaks": policy.external_gitleaks,
        "limits": dataclasses.asdict(policy.limits),
    }


def write_json_atomic(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def to_sarif(result: ScanResult) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []
    level_map = {
        Severity.INFO: "note",
        Severity.WARNING: "warning",
        Severity.BLOCK: "error",
        Severity.ERROR: "error",
    }
    for finding in result.findings:
        rules.setdefault(
            finding.code,
            {
                "id": finding.code,
                "name": finding.code,
                "shortDescription": {"text": finding.message},
                "defaultConfiguration": {"level": level_map[finding.severity]},
            },
        )
        sarif_results.append(
            {
                "ruleId": finding.code,
                "level": level_map[finding.severity],
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": urllib.parse.quote(finding.display_path(), safe="/!._-")}
                        }
                    }
                ],
                "properties": {
                    "severity": finding.severity.label(),
                    "blob_sha": finding.blob_sha,
                    "commit": finding.commit,
                    "scanner": finding.scanner,
                },
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Copper Git Boundary Guard",
                        "version": VERSION,
                        "informationUri": "https://github.com/copper0722/_admin-private",
                        "rules": list(rules.values()),
                    }
                },
                "results": sarif_results,
            }
        ],
    }


def github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A").replace(",", "%2C")


def print_human(result: ScanResult, *, quiet: bool = False) -> None:
    if quiet and not result.findings:
        return
    for finding in result.findings:
        prefix = {
            Severity.INFO: "INFO",
            Severity.WARNING: "WARN",
            Severity.BLOCK: "BLOCK",
            Severity.ERROR: "ERROR",
        }[finding.severity]
        print(f"{prefix} [{finding.code}] {finding.display_path()}: {finding.message}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            command = "warning" if finding.severity == Severity.WARNING else "error" if finding.severity >= Severity.BLOCK else "notice"
            print(f"::{command} file={github_escape(finding.display_path())}::{github_escape('[' + finding.code + '] ' + finding.message)}")
    status = "BLOCK" if result.blocking else "PASS"
    print(
        f"{status} — mode={result.mode} artifacts={result.artifacts_scanned} "
        f"unique_blobs={result.unique_blobs_scanned} findings={len(result.findings)}"
    )


def parse_pre_push_updates(stdin_text: str) -> list[tuple[str, str, str, str]]:
    updates: list[tuple[str, str, str, str]] = []
    for line_number, line in enumerate(stdin_text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise GuardError(f"malformed pre-push input line {line_number}")
        local_ref, local_sha, remote_ref, remote_sha = fields
        if not re.fullmatch(r"[0-9a-f]{40}", local_sha) or not re.fullmatch(r"[0-9a-f]{40}", remote_sha):
            raise GuardError(f"invalid object identity in pre-push input line {line_number}")
        updates.append((local_ref, local_sha, remote_ref, remote_sha))
    return updates


def collect_for_mode(git: Git, args: argparse.Namespace, policy: Policy) -> tuple[list[Artifact], dict[str, Any]]:
    refs: dict[str, Any] = {}
    if args.command == "staged":
        return git.staged_artifacts(), refs
    if args.command == "tree":
        ref = args.ref or "HEAD"
        refs["ref"] = redact_metadata_value(ref, "git-ref")
        return git.tree_artifacts(ref), refs
    if args.command == "range":
        base = args.base
        head = args.head or "HEAD"
        refs.update({
            "base": redact_metadata_value(base, "git-ref") if base else None,
            "head": redact_metadata_value(head, "git-ref"),
            "remote": redact_metadata_value(args.remote, "remote-name"),
        })
        artifacts, commits = git.range_artifacts(base, head, remote_name=args.remote, max_commits=policy.limits.max_commits)
        refs["commit_count"] = len(commits)
        refs["commits"] = commits
        return artifacts, refs
    if args.command == "history":
        artifacts, commits = git.history_artifacts(max_commits=policy.limits.max_commits)
        refs["commit_count"] = len(commits)
        refs["refs"] = "--all"
        return artifacts, refs
    if args.command == "pre-push":
        stdin_text = sys.stdin.read()
        updates = parse_pre_push_updates(stdin_text)
        all_artifacts: list[Artifact] = []
        seen: set[tuple[str, str, str | None]] = set()
        update_receipts: list[dict[str, Any]] = []
        for local_ref, local_sha, remote_ref, remote_sha in updates:
            if local_sha == ZERO_SHA:
                update_receipts.append({"local_ref": redact_metadata_value(local_ref, "local-ref"), "remote_ref": redact_metadata_value(remote_ref, "remote-ref"), "delete": True})
                continue
            artifacts, commits = git.range_artifacts(
                None if remote_sha == ZERO_SHA else remote_sha,
                local_sha,
                remote_name=args.remote,
                max_commits=policy.limits.max_commits,
            )
            for artifact in artifacts:
                key = (artifact.path, artifact.blob_sha, artifact.commit)
                if key not in seen:
                    seen.add(key)
                    all_artifacts.append(artifact)
            update_receipts.append(
                {
                    "local_ref": redact_metadata_value(local_ref, "local-ref"),
                    "local_sha": local_sha,
                    "remote_ref": redact_metadata_value(remote_ref, "remote-ref"),
                    "remote_sha": remote_sha,
                    "commit_count": len(commits),
                }
            )
        refs["remote"] = redact_metadata_value(args.remote, "remote-name")
        refs["remote_url"] = remote_url_receipt(args.remote_url)
        refs["updates"] = update_receipts
        return all_artifacts, refs
    raise GuardError(f"unsupported scan mode: {args.command}")


def run_scan(args: argparse.Namespace) -> int:
    started = utc_now()
    try:
        git = Git.discover(pathlib.Path(args.repo).expanduser() if args.repo else None)
        policy, initial_findings = load_policy(git, args)
        artifacts, refs = collect_for_mode(git, args, policy)
        findings, materialized, bytes_considered = scan_artifacts(git, artifacts, policy, initial_findings)
        gitleaks_findings, gitleaks_version = run_gitleaks(materialized, policy)
        findings = dedupe_findings([*findings, *gitleaks_findings])
        if policy.warnings_as_errors:
            findings = [
                dataclasses.replace(f, severity=Severity.BLOCK, message=f"{f.message} (warning promoted by policy)")
                if f.severity == Severity.WARNING else f
                for f in findings
            ]
            findings = dedupe_findings(findings)

        versions = {
            "python": sys.version.split()[0],
            "git": command_version("git", ["--version"]) or "unavailable",
            "copper_git_guard": VERSION,
        }
        if gitleaks_version:
            versions["gitleaks"] = gitleaks_version

        result = ScanResult(
            mode=args.command,
            repo_root=str(git.root),
            repository=policy.repository,
            visibility=policy.visibility,
            host=policy.host,
            started_at=started,
            finished_at=utc_now(),
            artifacts_scanned=len(artifacts),
            unique_blobs_scanned=len({artifact.blob_sha for artifact in artifacts}),
            bytes_considered=bytes_considered,
            findings=findings,
            scanner_versions=versions,
            policy=policy_snapshot(policy),
            refs=refs,
        )

        receipt_path = pathlib.Path(args.receipt) if args.receipt else git.git_path("copper-git-guard/last-scan.json")
        if not receipt_path.is_absolute():
            receipt_path = git.root / receipt_path
        write_json_atomic(receipt_path, result.as_dict())

        if args.sarif:
            sarif_path = pathlib.Path(args.sarif)
            if not sarif_path.is_absolute():
                sarif_path = git.root / sarif_path
            write_json_atomic(sarif_path, to_sarif(result))

        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_human(result, quiet=args.quiet)
        return 2 if result.blocking else 0
    except (GuardError, OSError, subprocess.SubprocessError) as exc:
        error_finding = Finding("SCANNER_INTERNAL_ERROR", Severity.ERROR, ".", f"Scanner failed closed: {type(exc).__name__}: {exc}")
        error_result = ScanResult(
            mode=getattr(args, "command", "unknown"),
            repo_root=str(pathlib.Path(getattr(args, "repo", None) or pathlib.Path.cwd()).expanduser()),
            repository=None,
            visibility=None,
            host=socket.gethostname().split(".")[0].lower(),
            started_at=started,
            finished_at=utc_now(),
            artifacts_scanned=0,
            unique_blobs_scanned=0,
            bytes_considered=0,
            findings=[error_finding],
            scanner_versions={
                "python": sys.version.split()[0],
                "git": command_version("git", ["--version"]) or "unavailable",
                "copper_git_guard": VERSION,
            },
            policy={"fail_closed": True},
            refs={},
        )
        payload = error_result.as_dict()
        try:
            if getattr(args, "receipt", None):
                write_json_atomic(pathlib.Path(args.receipt).expanduser(), payload)
            if getattr(args, "sarif", None):
                write_json_atomic(pathlib.Path(args.sarif).expanduser(), to_sarif(error_result))
        except OSError:
            pass
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"ERROR [SCANNER_INTERNAL_ERROR] .: {error_finding.message}", file=sys.stderr)
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copper-git-guard",
        description="Scan exact staged or outgoing Git blobs, including binary containers, before GitHub transport.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Exit codes:
              0  pass
              2  policy violation
              3  scanner/configuration failure (fail closed)

            The scanner never prints matched secret or PHI values.
            """
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--repo", help="Path inside the repository; default is the current directory.")
    parser.add_argument("--config", help=f"Policy TOML path; default is repository/{DEFAULT_CONFIG} when present.")
    parser.add_argument("--roster", help="Explicit repo-roster.json path.")
    parser.add_argument("--expected-repository", help="Trusted runtime repository identity (owner/name); mismatch blocks.")
    parser.add_argument("--expected-visibility", choices=("public", "private", "internal"), help="Trusted runtime visibility; mismatch blocks.")
    parser.add_argument("--require-roster", action="store_true", help="Fail closed unless this repository resolves uniquely in repo-roster.json.")
    parser.add_argument("--require-gitleaks", action="store_true", help="Fail closed unless external gitleaks runs successfully.")
    parser.add_argument("--no-gitleaks", action="store_true", help="Disable external gitleaks; built-in checks still run.")
    parser.add_argument("--warnings-as-errors", action="store_true", help="Promote warnings to blocking findings.")
    parser.add_argument("--receipt", help=f"JSON receipt path; default {DEFAULT_RECEIPT}.")
    parser.add_argument("--sarif", help="Optional SARIF output path.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of human output.")
    parser.add_argument("--quiet", action="store_true", help="Suppress output on a clean pass.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("staged", help="Scan exact stage-0 blobs in the Git index.")

    tree = subparsers.add_parser("tree", help="Scan every tracked object in one tree/ref.")
    tree.add_argument("--ref", default="HEAD", help="Tree-ish to scan; default HEAD.")

    subparsers.add_parser("history", help="Scan every changed blob version in all commits reachable from all refs.")

    range_parser = subparsers.add_parser("range", help="Scan every changed blob version in all commits in BASE..HEAD.")
    range_parser.add_argument("--base", help="Base commit. Omit for commits not present on --remote.")
    range_parser.add_argument("--head", default="HEAD", help="Head commit; default HEAD.")
    range_parser.add_argument("--remote", default="origin", help="Remote name for new-branch reachability; default origin.")

    pre_push = subparsers.add_parser("pre-push", help="Read Git pre-push updates from stdin and scan all outgoing blob versions.")
    pre_push.add_argument("--remote", default="origin", help="Remote name supplied by Git hook.")
    pre_push.add_argument("--remote-url", default=None, help="Remote URL supplied by Git hook; recorded but never used for authentication.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.require_gitleaks and args.no_gitleaks:
        parser.error("--require-gitleaks and --no-gitleaks are mutually exclusive")
    return run_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
