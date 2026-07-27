"""Acquire and inspect the official NHI drug-item linkage snapshot.

The source CSV contains product identifiers, payment validity windows, ATC
codes, reimbursement-rule designations, and exact rule-document URLs.  This
module preserves that CSV byte-for-byte and emits a small machine-readable
manifest.  It does not resolve a rule designation to canonical legal history.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import ssl
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PARSER_VERSION = "nhi-rule-history/nhi-drug-linkage-snapshot/1.0.0"
DATASET_IDENTIFIER = "A21030000I-E41001"
RESOURCE_ID = "A21030000I-E41001-001"
CATALOG_URL = "https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111"
SEARCH_URL = "https://info.nhi.gov.tw/INAE3000/INAE3000S01?type=1"
DOWNLOAD_URL = (
    "https://info.nhi.gov.tw/api/iode0000s01/"
    f"Dataset?rId={RESOURCE_ID}"
)

EXPECTED_COLUMNS = (
    "異動",
    "藥品代號",
    "藥品英文名稱",
    "藥品中文名稱",
    "成分",
    "規格量",
    "規格單位",
    "單複方",
    "支付價",
    "有效起日",
    "有效迄日",
    "藥商",
    "製造廠名稱",
    "劑型",
    "藥品分類",
    "分類分組名稱",
    "ATC代碼",
    "給付規定章節",
    "藥品代碼超連結",
    "給付規定章節連結",
)


class LinkageSnapshotError(RuntimeError):
    """Raised when acquisition or source validation fails closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_csv(path: Path) -> dict[str, Any]:
    """Validate the exact source header and return bounded aggregate counts."""

    row_count = 0
    drug_codes: set[str] = set()
    atc_codes: set[str] = set()
    rows_with_atc = 0
    rows_with_rule_section = 0
    rows_with_rule_url = 0
    missing_drug_code_rows: list[int] = []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_columns = tuple(reader.fieldnames or ())
            if actual_columns != EXPECTED_COLUMNS:
                raise LinkageSnapshotError(
                    "source CSV header drift: "
                    f"expected={EXPECTED_COLUMNS!r} actual={actual_columns!r}"
                )

            for source_row_number, row in enumerate(reader, 2):
                if None in row:
                    raise LinkageSnapshotError(
                        f"source row {source_row_number} has extra columns"
                    )
                if any(value is None for value in row.values()):
                    raise LinkageSnapshotError(
                        f"source row {source_row_number} is truncated"
                    )
                row_count += 1
                drug_code = (row["藥品代號"] or "").strip()
                atc_code = (row["ATC代碼"] or "").strip().upper()
                if not drug_code:
                    missing_drug_code_rows.append(source_row_number)
                    if len(missing_drug_code_rows) > 20:
                        raise LinkageSnapshotError(
                            "more than 20 rows have no NHI drug code"
                        )
                else:
                    drug_codes.add(drug_code)
                if atc_code:
                    rows_with_atc += 1
                    atc_codes.add(atc_code)
                if (row["給付規定章節"] or "").strip():
                    rows_with_rule_section += 1
                if (row["給付規定章節連結"] or "").strip():
                    rows_with_rule_url += 1
    except UnicodeDecodeError as exc:
        raise LinkageSnapshotError("source CSV is not UTF-8") from exc
    except csv.Error as exc:
        raise LinkageSnapshotError("source CSV is malformed") from exc

    if row_count == 0:
        raise LinkageSnapshotError("source CSV has zero data rows")
    if missing_drug_code_rows:
        raise LinkageSnapshotError(
            "source CSV contains rows without NHI drug code: "
            f"{missing_drug_code_rows!r}"
        )

    return {
        "row_count": row_count,
        "distinct_drug_codes": len(drug_codes),
        "rows_with_atc": rows_with_atc,
        "distinct_atc_codes": len(atc_codes),
        "rows_with_rule_section": rows_with_rule_section,
        "rows_with_rule_url": rows_with_rule_url,
        "header": list(EXPECTED_COLUMNS),
    }


def _ssl_context(*, ca_file: Path | None, allow_insecure_tls: bool) -> ssl.SSLContext:
    if ca_file is not None and allow_insecure_tls:
        raise LinkageSnapshotError(
            "--ca-file and --allow-insecure-tls are mutually exclusive"
        )
    if allow_insecure_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if ca_file is not None:
        return ssl.create_default_context(cafile=str(ca_file))
    return ssl.create_default_context()


def _validate_download_url(url: str) -> None:
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "info.nhi.gov.tw"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.port not in (None, 443)
        or parsed_url.path != "/api/iode0000s01/Dataset"
        or parsed_url.query != f"rId={RESOURCE_ID}"
        or parsed_url.fragment
    ):
        raise LinkageSnapshotError(
            "download URL is not the declared official NHI IODE resource"
        )


def acquire_snapshot(
    *,
    output_dir: Path,
    url: str = DOWNLOAD_URL,
    retrieved_at: dt.datetime | None = None,
    ca_file: Path | None = None,
    allow_insecure_tls: bool = False,
) -> dict[str, Any]:
    """Download, hash, inspect, and atomically manifest one official snapshot."""

    retrieved_at = retrieved_at or dt.datetime.now(dt.timezone.utc)
    if retrieved_at.tzinfo is None:
        raise LinkageSnapshotError("retrieved_at must include a timezone")
    retrieved_at = retrieved_at.astimezone(dt.timezone.utc)

    artifacts_dir = output_dir / "artifacts"
    manifests_dir = output_dir / "manifests"
    quarantine_dir = output_dir / "quarantine"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    _validate_download_url(url)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"{PARSER_VERSION} (+public-data-audit)"},
    )
    context = _ssl_context(
        ca_file=ca_file,
        allow_insecure_tls=allow_insecure_tls,
    )

    temp_path: Path | None = None
    response_metadata: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(
            request,
            timeout=300,
            context=context,
        ) as response:
            status = int(getattr(response, "status", 200))
            if status != 200:
                raise LinkageSnapshotError(f"source returned HTTP {status}")
            final_url = str(response.geturl())
            _validate_download_url(final_url)
            response_metadata = {
                "status": status,
                "final_url": final_url,
                "content_type": response.headers.get("Content-Type"),
                "content_length": response.headers.get("Content-Length"),
                "content_disposition": response.headers.get(
                    "Content-Disposition"
                ),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".partial-",
                suffix=".csv",
                dir=artifacts_dir,
                delete=False,
            ) as temp_handle:
                temp_path = Path(temp_handle.name)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_handle.write(chunk)
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if isinstance(exc, LinkageSnapshotError):
            raise
        raise LinkageSnapshotError(f"snapshot download failed: {exc}") from exc

    assert temp_path is not None
    byte_length = temp_path.stat().st_size
    sha256 = sha256_file(temp_path)
    declared_length = response_metadata.get("content_length")
    if declared_length is not None:
        try:
            expected_length = int(str(declared_length))
        except ValueError as exc:
            quarantine_path = quarantine_dir / f"{sha256}.csv"
            os.replace(temp_path, quarantine_path)
            raise LinkageSnapshotError(
                "source Content-Length is not an integer; raw response quarantined"
            ) from exc
        if expected_length != byte_length:
            quarantine_path = quarantine_dir / f"{sha256}.csv"
            os.replace(temp_path, quarantine_path)
            raise LinkageSnapshotError(
                "source Content-Length mismatch; raw response quarantined"
            )

    try:
        inspection = inspect_csv(temp_path)
    except Exception as exc:
        quarantine_path = quarantine_dir / f"{sha256}.csv"
        if quarantine_path.exists():
            if sha256_file(quarantine_path) != sha256:
                temp_path.unlink(missing_ok=True)
                raise LinkageSnapshotError(
                    "quarantine hash collision"
                ) from exc
            temp_path.unlink()
        else:
            os.replace(temp_path, quarantine_path)
        if isinstance(exc, LinkageSnapshotError):
            raise LinkageSnapshotError(
                f"{exc}; raw response quarantined as {quarantine_path.name}"
            ) from exc
        raise

    artifact_path = artifacts_dir / f"{sha256}.csv"
    if artifact_path.exists():
        if (
            artifact_path.stat().st_size != byte_length
            or sha256_file(artifact_path) != sha256
        ):
            temp_path.unlink(missing_ok=True)
            raise LinkageSnapshotError(
                "existing content-addressed artifact does not match its identity"
            )
        temp_path.unlink()
    else:
        os.replace(temp_path, artifact_path)

    manifest = {
        "schema_version": 1,
        "parser_version": PARSER_VERSION,
        "source_system": "NHI_IODE_DRUG_ITEMS",
        "dataset_identifier": DATASET_IDENTIFIER,
        "resource_id": RESOURCE_ID,
        "catalog_url": CATALOG_URL,
        "search_url": SEARCH_URL,
        "download_url": url,
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "artifact": {
            "path": f"artifacts/{artifact_path.name}",
            "media_type": "text/csv",
            "byte_length": byte_length,
            "sha256": sha256,
        },
        "response": response_metadata,
        "transport": {
            "tls_verification": (
                "disabled_explicitly"
                if allow_insecure_tls
                else "custom_ca"
                if ca_file is not None
                else "system_default"
            )
        },
        "inspection": inspection,
        "legal_semantics": {
            "product_to_atc": "official_source_assertion",
            "product_to_rule_reference": "official_source_assertion",
            "rule_identity_resolution": "unresolved_until_designation_and_url_match",
            "rule_to_atc": "derived_product_evidence_not_class_entailment",
        },
    }

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifests_dir / f"{stamp}-{sha256[:12]}.json"
    payload = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=".partial-",
        suffix=".json",
        dir=manifests_dir,
        delete=False,
    ) as temp_manifest:
        temp_manifest.write(payload)
        temp_manifest_path = Path(temp_manifest.name)
    if manifest_path.exists():
        if manifest_path.read_text(encoding="utf-8") != payload:
            temp_manifest_path.unlink()
            raise LinkageSnapshotError(
                "manifest identity collision for retrieved_at and artifact hash"
            )
        temp_manifest_path.unlink()
    else:
        os.replace(temp_manifest_path, manifest_path)

    return {
        "status": "ok",
        "artifact_path": str(artifact_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "artifact_sha256": sha256,
        "byte_length": byte_length,
        **inspection,
    }
