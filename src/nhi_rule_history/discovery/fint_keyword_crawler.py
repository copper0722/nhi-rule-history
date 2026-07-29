from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from math import ceil
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from nhi_rule_history.contracts import (
    ContractError,
    assert_allowed_url,
    canonical_json_bytes,
    canonical_url,
    file_sha256,
    sha256_bytes,
    stable_id,
    utc_now,
)
from nhi_rule_history.discovery.fint import parse_fint
from nhi_rule_history.fetch.http import HttpClient
from nhi_rule_history.fetch.http import HttpResponse, SAFE_RESPONSE_HEADERS
from nhi_rule_history.raw import RawStore


FINT_HOST = "mohwlaw.mohw.gov.tw"
FINT_SEARCH_URL = "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY03.aspx"
FINT_DETAIL_URL = "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx"
CRAWLER_VERSION = "nhi-rule-history/fint-frontier-crawler/2.2.0"
TOTAL_RE = re.compile(r"共\s*([\d,]+)\s*筆")
DATE_RE = re.compile(
    r"發文日期\s*[:：]\s*(民國\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)"
)
NHI_ATTACHMENT_CANDIDATE_RE = re.compile(
    r"(全民健康保險|中央健康保險署|中央健康保險局|"
    r"藥品給付規定|健保(?:署|局|審|藥|醫|財)|衛署保)"
)


def normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()).strip()
    if not normalized:
        raise ContractError("FINT keyword must not be empty")
    if len(normalized) > 128:
        raise ContractError("FINT keyword exceeds 128 characters")
    return normalized


def normalize_keywords(
    value: str | Iterable[str],
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    raw = [value] if isinstance(value, str) else list(value)
    if not raw and allow_empty:
        return ()
    normalized = tuple(normalize_keyword(str(item)) for item in raw)
    if not normalized:
        raise ContractError("FINT query requires at least one keyword")
    if len(normalized) > 4:
        raise ContractError("FINT supports at most four keywords")
    return normalized


def normalize_document_number(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", "", normalized)
    if not normalized:
        raise ContractError("FINT document number must not be empty")
    return normalized


def detect_media_type(payload: bytes, declared: str | None) -> str:
    if payload.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "application/x-ole-storage"
    if payload.startswith(b"%PDF-"):
        return "application/pdf"
    if payload.startswith(b"PK\x03\x04"):
        return "application/zip"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if declared:
        return declared.split(";", 1)[0].strip().lower()
    return "application/octet-stream"


def _query_params(
    *,
    keywords: tuple[str, ...],
    start_date: str,
    end_date: str,
    row_number: int | None = None,
) -> dict[str, str]:
    params = {
        "starDate": start_date,
        "endDate": end_date,
        "no": "",
        "n1": "",
        "n2": "",
        "kt": "",
        "kw": keywords[0] if keywords else "",
        "kw2": keywords[1] if len(keywords) > 1 else "",
        "kw3": keywords[2] if len(keywords) > 2 else "",
        "kw4": keywords[3] if len(keywords) > 3 else "",
        "valid": "3",
        "type": "etype_",
    }
    if row_number is not None:
        params["RowNo"] = str(row_number)
    return params


def build_search_url(
    keyword: str | Iterable[str] | None,
    *,
    start_date: str = "00000000",
    end_date: str = "99991231",
    page: int | None = None,
) -> str:
    if page is not None and page < 1:
        raise ContractError("FINT search page must be positive")
    keywords = normalize_keywords(
        () if keyword is None else keyword,
        allow_empty=True,
    )
    params = _query_params(
        keywords=keywords,
        start_date=start_date,
        end_date=end_date,
    )
    if page is not None:
        params["page"] = str(page)
    return canonical_url(
        f"{FINT_SEARCH_URL}?"
        f"{urlencode(params)}"
    )


def build_detail_url(
    keyword: str | Iterable[str] | None,
    row_number: int,
    *,
    start_date: str = "00000000",
    end_date: str = "99991231",
) -> str:
    if row_number < 1:
        raise ContractError("FINT row number must be positive")
    keywords = normalize_keywords(
        () if keyword is None else keyword,
        allow_empty=True,
    )
    return canonical_url(
        f"{FINT_DETAIL_URL}?"
        f"{urlencode(_query_params(keywords=keywords, start_date=start_date, end_date=end_date, row_number=row_number))}"
    )


class FintSearchParser(HTMLParser):
    """Parse one FINTQRY03 result page without browser execution."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_text: list[str] = []
        self.has_result_table = False
        self.detail_links: list[str] = []
        self.result_rows: list[str] = []
        self._result_table_depth = 0
        self._row_depth = 0
        self._row_text: list[str] = []
        self._row_has_detail = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "dat02":
            self.has_result_table = True
            self._result_table_depth = 1
        elif tag == "table" and self._result_table_depth:
            self._result_table_depth += 1
        if tag == "tr" and self._result_table_depth:
            self._row_depth += 1
            if self._row_depth == 1:
                self._row_text = []
                self._row_has_detail = False
        if tag == "a":
            href = attributes.get("href")
            if (
                self._result_table_depth
                and href
                and "FINTQRY04.aspx" in href
                and "RowNo=" in href
            ):
                self.detail_links.append(href)
                self._row_has_detail = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._row_depth:
            if self._row_depth == 1 and self._row_has_detail:
                normalized = " ".join("".join(self._row_text).split())
                if not normalized:
                    raise ContractError("FINTQRY03 result row has no text")
                self.result_rows.append(normalized)
            self._row_depth -= 1
        if tag == "table" and self._result_table_depth:
            self._result_table_depth -= 1

    def handle_data(self, data: str) -> None:
        self.body_text.append(data)
        if self._row_depth:
            self._row_text.append(data)

    @property
    def normalized_body(self) -> str:
        return " ".join("".join(self.body_text).split())

    def result_count(self) -> int:
        if not self.has_result_table:
            raise ContractError("FINTQRY03 selector miss: table#dat02 absent")
        match = TOTAL_RE.search(self.normalized_body)
        if match:
            return int(match.group(1).replace(",", ""))
        if not self.detail_links:
            return 0
        raise ContractError("FINTQRY03 has result links but no total count")

    def validate_page(self, *, page: int, result_count: int) -> None:
        expected_size = min(10, max(0, result_count - ((page - 1) * 10)))
        if len(self.detail_links) != expected_size:
            raise ContractError(
                "FINTQRY03 page link parity failed: "
                f"page={page} {len(self.detail_links)} != {expected_size}"
            )
        if len(self.result_rows) != expected_size:
            raise ContractError(
                "FINTQRY03 page row parity failed: "
                f"page={page} {len(self.result_rows)} != {expected_size}"
            )
        expected_rows = list(
            range(((page - 1) * 10) + 1, ((page - 1) * 10) + expected_size + 1)
        )
        actual_rows: list[int] = []
        for href in self.detail_links:
            values = parse_qs(urlsplit(href).query).get("RowNo", [])
            if len(values) != 1 or not values[0].isdigit():
                raise ContractError(
                    f"FINTQRY03 page={page} has invalid RowNo link"
                )
            actual_rows.append(int(values[0]))
        if actual_rows != expected_rows:
            raise ContractError(
                "FINTQRY03 page RowNo sequence failed: "
                f"page={page} {actual_rows!r} != {expected_rows!r}"
            )

    @property
    def result_fingerprint(self) -> str:
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "detail_links": self.detail_links,
                    "result_rows": self.result_rows,
                }
            )
        )


def parse_fint_search(payload: bytes, content_type: str | None = None) -> FintSearchParser:
    from nhi_rule_history.discovery.fint import decode_html

    parser = FintSearchParser()
    parser.feed(decode_html(payload, content_type))
    return parser


class FintCurlClient:
    """Verified curl transport for the legacy FINT TLS chain.

    Python 3.14 rejects the site's legacy certificate because it lacks a
    Subject Key Identifier. The system curl trust stack validates it. This
    transport keeps verification enabled, forbids redirects, checks the final
    URL, and applies a hard response-size limit.
    """

    allowed_hosts = (FINT_HOST,)

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        curl = shutil.which("curl")
        if curl is None:
            raise ContractError("curl is required for the FINT TLS profile")
        self._curl = curl
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._temporary = tempfile.TemporaryDirectory(
            prefix="nhi-rule-history-fint-http-"
        )
        self._root = Path(self._temporary.name)

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
            self._temporary = None

    def get(self, url: str) -> HttpResponse:
        request_url = assert_allowed_url(url, self.allowed_hosts)
        body_path = self._root / "response.body"
        header_path = self._root / "response.headers"
        body_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                self._curl,
                "--http1.1",
                "--silent",
                "--show-error",
                "--request",
                "GET",
                "--proto",
                "=https",
                "--max-redirs",
                "0",
                "--connect-timeout",
                str(max(1, int(self.timeout_seconds))),
                "--max-time",
                str(max(1, int(self.timeout_seconds))),
                "--max-filesize",
                str(self.max_bytes),
                "--user-agent",
                "nhi-rule-history/0.2 (+public-source-archive)",
                "--output",
                str(body_path),
                "--dump-header",
                str(header_path),
                "--write-out",
                "%{http_code}\n%{redirect_url}\n%{url_effective}",
                "--url",
                request_url,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            raise ContractError(
                f"verified FINT curl request failed: "
                f"curl_exit_{completed.returncode}"
            )
        lines = completed.stdout.splitlines()
        if len(lines) != 3 or lines[0] != "200" or lines[1]:
            raise ContractError("FINT response status or redirect is invalid")
        final_url = assert_allowed_url(lines[2], self.allowed_hosts)
        if final_url != request_url:
            raise ContractError("FINT response changed the requested URL")
        body = body_path.read_bytes() if body_path.is_file() else b""
        if len(body) > self.max_bytes:
            raise ContractError("FINT response exceeds max_bytes")
        if not header_path.is_file():
            raise ContractError("FINT response headers are missing")
        raw_headers = header_path.read_text(
            encoding="iso-8859-1",
            errors="strict",
        )
        header_blocks = [
            block
            for block in re.split(r"\r?\n\r?\n", raw_headers)
            if block.startswith("HTTP/")
        ]
        if len(header_blocks) != 1:
            raise ContractError("FINT response redirected unexpectedly")
        headers: dict[str, str] = {}
        for line in header_blocks[0].splitlines()[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            normalized_name = name.strip().lower()
            if normalized_name in SAFE_RESPONSE_HEADERS:
                headers[normalized_name] = value.strip()
        return HttpResponse(
            request_url=request_url,
            final_url=final_url,
            status_code=200,
            headers=headers,
            body=body,
        )


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return ()
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise ContractError(
                    f"{path.name}:{line_number}: row must be an object"
                )
            rows.append(value)
    return rows


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(dict(row))
    with path.open("ab") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class Seed:
    keyword: str | None
    origin_kind: str
    origin_locator: str
    additional_keywords: tuple[str, ...] = ()

    @property
    def keywords(self) -> tuple[str, ...]:
        if self.keyword is None:
            if self.additional_keywords:
                raise ContractError(
                    "unfiltered FINT seed cannot have additional keywords"
                )
            return ()
        return normalize_keywords((self.keyword, *self.additional_keywords))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Seed":
        if "keywords" in value:
            raw_keywords = value["keywords"]
            if not isinstance(raw_keywords, list):
                raise ContractError("FINT seed keywords must be an array")
            keywords = normalize_keywords(
                (str(item) for item in raw_keywords),
                allow_empty=True,
            )
        else:
            keywords = normalize_keywords(str(value.get("keyword", "")))
        origin_kind = str(value.get("origin_kind", "")).strip()
        origin_locator = str(value.get("origin_locator", "")).strip()
        if not origin_kind or not origin_locator:
            raise ContractError(
                "each FINT seed requires origin_kind and origin_locator"
            )
        return cls(
            keywords[0] if keywords else None,
            origin_kind,
            origin_locator,
            keywords[1:],
        )


class FintKeywordCrawler:
    """Resumable, single-threaded public-record crawler for FINTQRY03/04."""

    FILES = {
        "seeds": "fint-query-seeds.jsonl",
        "queries": "fint-queries.jsonl",
        "documents": "fint-documents.jsonl",
        "snapshots": "fint-document-snapshots.jsonl",
        "matches": "fint-query-document-matches.jsonl",
        "attachment_declarations": "fint-attachment-declarations.jsonl",
        "attachments": "fint-attachment-snapshots.jsonl",
        "observations": "fint-crawl-observations.jsonl",
        "issues": "fint-crawl-issues.jsonl",
    }

    def __init__(
        self,
        run_dir: Path,
        *,
        client: HttpClient | None = None,
        min_interval_seconds: float = 0.8,
        max_results_per_query: int = 5000,
        attachment_policy: str = "all",
        start_date: str = "00000000",
        end_date: str = "99991231",
    ) -> None:
        if min_interval_seconds < 0:
            raise ContractError("min_interval_seconds must not be negative")
        if max_results_per_query < 1:
            raise ContractError("max_results_per_query must be positive")
        if attachment_policy not in {"all", "nhi_candidate", "none"}:
            raise ContractError(
                "attachment_policy must be all, nhi_candidate, or none"
            )
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.run_dir / "fint-crawl-manifest.json"
        resume_contract = {
            "start_date": start_date,
            "end_date": end_date,
            "valid": "3",
            "record_type": "etype_",
            "attachment_policy": attachment_policy,
        }
        if manifest_path.is_file():
            existing_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            if existing_manifest.get("crawler_version") != CRAWLER_VERSION:
                raise ContractError(
                    "FINT run directory belongs to another crawler version"
                )
            existing_contract = existing_manifest.get("query_contract", {})
            for key, expected in resume_contract.items():
                if existing_contract.get(key) != expected:
                    raise ContractError(
                        f"FINT resume contract changed: {key}"
                    )
        else:
            if any(self.run_dir.iterdir()):
                raise ContractError(
                    "non-empty FINT run directory has no resumable "
                    "crawler-version contract"
                )
            running_manifest = {
                "schema": (
                    "nhi-rule-history/fint-frontier-crawl-manifest/v3"
                ),
                "crawler_version": CRAWLER_VERSION,
                "status": "running",
                "started_at": utc_now(),
                "query_contract": {
                    **resume_contract,
                    "search_endpoint": FINT_SEARCH_URL,
                    "detail_endpoint": FINT_DETAIL_URL,
                    "single_threaded": True,
                    "minimum_interval_seconds": min_interval_seconds,
                    "max_results_per_query": max_results_per_query,
                },
            }
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_bytes(canonical_json_bytes(running_manifest))
            os.replace(temporary, manifest_path)
        self.paths = {
            name: self.run_dir / filename for name, filename in self.FILES.items()
        }
        for path in self.paths.values():
            path.touch(exist_ok=True)
        self.store = RawStore(run_dir)
        (run_dir / "raw" / "sha256").mkdir(parents=True, exist_ok=True)
        self.client = client or FintCurlClient()
        self._owns_client = client is None
        self.min_interval_seconds = min_interval_seconds
        self.max_results_per_query = max_results_per_query
        self.attachment_policy = attachment_policy
        self.start_date = start_date
        self.end_date = end_date
        self._last_network_at: float | None = None
        self._indexes = {
            name: self._index(path, self._key_for(name))
            for name, path in self.paths.items()
        }
        self._observations_by_url = {
            row["request_url"]: row
            for row in _iter_jsonl(self.paths["observations"])
            if row.get("status") == "success"
        }

    @staticmethod
    def _key_for(name: str) -> str:
        return {
            "seeds": "seed_id",
            "queries": "query_id",
            "documents": "document_id",
            "snapshots": "snapshot_id",
            "matches": "match_id",
            "attachment_declarations": "attachment_declaration_id",
            "attachments": "attachment_snapshot_id",
            "observations": "observation_id",
            "issues": "issue_id",
        }[name]

    @staticmethod
    def _index(path: Path, key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in _iter_jsonl(path):
            row_key = str(row.get(key, ""))
            if not row_key:
                raise ContractError(f"{path.name}: row missing {key}")
            previous = result.get(row_key)
            if previous is not None and previous != row:
                raise ContractError(f"{path.name}: conflicting row {row_key}")
            result[row_key] = row
        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _record_once(self, name: str, row: dict[str, Any]) -> None:
        key_name = self._key_for(name)
        key = str(row[key_name])
        previous = self._indexes[name].get(key)
        if previous is not None:
            if previous != row:
                raise ContractError(f"conflicting {name} row: {key}")
            return
        _append_row(self.paths[name], row)
        self._indexes[name][key] = row

    def _wait_for_rate_limit(self) -> None:
        if self._last_network_at is None:
            return
        remaining = (
            self.min_interval_seconds
            - (time.monotonic() - self._last_network_at)
        )
        if remaining > 0:
            time.sleep(remaining)

    def _fetch_uncached(self, request_url: str) -> HttpResponse:
        self._wait_for_rate_limit()
        try:
            return self.client.get(request_url)
        finally:
            self._last_network_at = time.monotonic()

    def _record_issue(
        self,
        *,
        request_url: str,
        resource_kind: str,
        error_code: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        recorded_at = utc_now()
        issue = {
            "schema": "nhi-rule-history/fint-crawl-issue/v2",
            "issue_id": stable_id(
                "fint-crawl-issue",
                request_url,
                error_code,
                canonical_json_bytes(dict(context or {})).decode("utf-8"),
            ),
            "request_url": request_url,
            "resource_kind": resource_kind,
            "error_code": error_code,
            "context": dict(context or {}),
            "recorded_at": recorded_at,
            "blocking": True,
        }
        self._record_once("issues", issue)

    def _observe(self, request_url: str, *, resource_kind: str) -> dict[str, Any]:
        cached = self._observations_by_url.get(request_url)
        if cached is not None and self.store.verify(
            cached["content_path"],
            cached["content_sha256"],
            cached["byte_size"],
        ):
            return {
                "payload": self.store.read(
                    cached["content_path"],
                    cached["content_sha256"],
                    cached["byte_size"],
                ),
                "headers": cached["response_headers"],
                "row": cached,
                "cache_hit": True,
            }

        observed_at = utc_now()
        response = self._fetch_uncached(request_url)
        stored = self.store.put(response.body)
        observation_id = stable_id(
            "fint-crawl-observation",
            request_url,
            stored.sha256,
        )
        row = {
            "schema": "nhi-rule-history/fint-crawl-observation/v1",
            "observation_id": observation_id,
            "request_url": request_url,
            "final_url": response.final_url,
            "resource_kind": resource_kind,
            "status": "success",
            "observed_at": observed_at,
            "http_status": response.status_code,
            "response_headers": response.headers,
            "content_sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "content_path": stored.relative_path,
        }
        self._record_once("observations", row)
        self._observations_by_url[request_url] = row
        return {
            "payload": response.body,
            "headers": response.headers,
            "row": row,
            "cache_hit": False,
        }

    def _record_seed(self, seed: Seed, query_id: str) -> None:
        seed_id = stable_id(
            "fint-query-seed",
            query_id,
            seed.origin_kind,
            seed.origin_locator,
        )
        self._record_once(
            "seeds",
            {
                "schema": "nhi-rule-history/fint-query-seed/v2",
                "seed_id": seed_id,
                "query_id": query_id,
                "keywords": list(seed.keywords),
                "origin_kind": seed.origin_kind,
                "origin_locator": seed.origin_locator,
            },
        )

    def crawl(self, seeds: Iterable[Seed]) -> dict[str, Any]:
        grouped: dict[tuple[str, ...], list[Seed]] = {}
        for seed in seeds:
            keywords = seed.keywords
            if (
                not keywords
                and seed.origin_kind == "fint_unfiltered_year_partition"
                and seed.origin_locator
                != f"{self.start_date}__{self.end_date}"
            ):
                raise ContractError(
                    "unfiltered FINT seed locator does not match query dates"
                )
            group_key = tuple(item.casefold() for item in keywords)
            grouped.setdefault(group_key, []).append(
                Seed(
                    keywords[0] if keywords else None,
                    seed.origin_kind,
                    seed.origin_locator,
                    keywords[1:],
                )
            )

        for group_key in sorted(grouped):
            seed_group = grouped[group_key]
            keywords = seed_group[0].keywords
            query_id = stable_id(
                "fint-keyword-query",
                *(group_key or ("<ALL_RECORDS>",)),
                self.start_date,
                self.end_date,
                "valid=3",
                "type=etype_",
            )
            for seed in seed_group:
                self._record_seed(seed, query_id)
            if query_id in self._indexes["queries"]:
                continue
            self._crawl_query(query_id, keywords)

        return self._write_manifest(grouped)

    def _crawl_query(
        self,
        query_id: str,
        keywords: tuple[str, ...],
    ) -> None:
        search_pages: list[dict[str, Any]] = []
        search_url = build_search_url(
            keywords,
            start_date=self.start_date,
            end_date=self.end_date,
            page=1,
        )
        search_observation = self._observe(
            search_url,
            resource_kind="fint_search_page",
        )
        search_parser = parse_fint_search(
            search_observation["payload"],
            search_observation["headers"].get("content-type"),
        )
        result_count = search_parser.result_count()
        if result_count > self.max_results_per_query:
            raise ContractError(
                f"FINT query {keywords!r} returned {result_count} rows; "
                f"limit is {self.max_results_per_query}"
            )
        page_count = max(1, ceil(result_count / 10))
        search_parser.validate_page(page=1, result_count=result_count)
        search_pages.append(
            {
                "page": 1,
                "url": search_url,
                "observation_id": search_observation["row"]["observation_id"],
                "result_fingerprint": search_parser.result_fingerprint,
            }
        )
        for page in range(2, page_count + 1):
            page_url = build_search_url(
                keywords,
                start_date=self.start_date,
                end_date=self.end_date,
                page=page,
            )
            page_observation = self._observe(
                page_url,
                resource_kind="fint_search_page",
            )
            page_parser = parse_fint_search(
                page_observation["payload"],
                page_observation["headers"].get("content-type"),
            )
            if page_parser.result_count() != result_count:
                raise ContractError(
                    f"FINTQRY03 total changed during initial index capture: "
                    f"page={page}"
                )
            page_parser.validate_page(page=page, result_count=result_count)
            search_pages.append(
                {
                    "page": page,
                    "url": page_url,
                    "observation_id": page_observation["row"][
                        "observation_id"
                    ],
                    "result_fingerprint": page_parser.result_fingerprint,
                }
            )

        search_index_sha256 = sha256_bytes(
            canonical_json_bytes(
                [
                    {
                        "page": row["page"],
                        "result_fingerprint": row["result_fingerprint"],
                    }
                    for row in search_pages
                ]
            )
        )

        fetched_rows = 0
        for row_number in range(1, result_count + 1):
            self._crawl_detail(query_id, keywords, row_number)
            fetched_rows += 1

        for expected in search_pages:
            verified = self._fetch_uncached(expected["url"])
            verified_parser = parse_fint_search(
                verified.body,
                verified.headers.get("content-type"),
            )
            if verified_parser.result_count() != result_count:
                raise ContractError(
                    "FINTQRY03 total changed before query completion"
                )
            verified_parser.validate_page(
                page=expected["page"],
                result_count=result_count,
            )
            if (
                verified_parser.result_fingerprint
                != expected["result_fingerprint"]
            ):
                raise ContractError(
                    "FINTQRY03 result set changed before query completion: "
                    f"page={expected['page']}"
                )
        index_verified_at = utc_now()

        self._record_once(
            "queries",
            {
                "schema": "nhi-rule-history/fint-query/v3",
                "query_id": query_id,
                "keywords": list(keywords),
                "normalized_keywords": [
                    item.casefold() for item in keywords
                ],
                "start_date": self.start_date,
                "end_date": self.end_date,
                "valid": "3",
                "record_type": "etype_",
                "search_url": search_url,
                "search_observation_id": search_observation["row"][
                    "observation_id"
                ],
                "search_page_observation_ids": [
                    row["observation_id"] for row in search_pages
                ],
                "search_index_sha256": search_index_sha256,
                "index_verified_at": index_verified_at,
                "expected_rows": result_count,
                "fetched_rows": fetched_rows,
                "completed_at": utc_now(),
            },
        )

    def _crawl_detail(
        self,
        query_id: str,
        keywords: tuple[str, ...],
        row_number: int,
    ) -> None:
        detail_url = build_detail_url(
            keywords,
            row_number,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        observation = self._observe(
            detail_url,
            resource_kind="fint_detail_page",
        )
        parser = parse_fint(
            observation["payload"],
            observation["headers"].get("content-type"),
        )
        if not parser.has_record_table or not parser.record_text:
            raise ContractError(
                f"FINT detail row {row_number} has no complete record table"
            )
        raw_number, parsed_number = parser.document_number()
        normalized_number = normalize_document_number(parsed_number)
        document_id = stable_id("fint-document", normalized_number)
        observed_at = observation["row"]["observed_at"]
        document_row = {
            "schema": "nhi-rule-history/fint-document/v1",
            "document_id": document_id,
            "normalized_document_number": normalized_number,
            "first_observed_raw_document_number": raw_number,
            "first_observed_at": observed_at,
        }
        if document_id not in self._indexes["documents"]:
            self._record_once("documents", document_row)

        record_text_bytes = (parser.record_text + "\n").encode("utf-8")
        record_text_blob = self.store.put(record_text_bytes)
        date_match = DATE_RE.search(parser.record_text)
        record_text_sha256 = sha256_bytes(record_text_bytes)
        snapshot_id = stable_id(
            "fint-document-snapshot",
            document_id,
            detail_url,
            record_text_sha256,
        )
        snapshot_row = {
            "schema": "nhi-rule-history/fint-document-snapshot/v2",
            "snapshot_id": snapshot_id,
            "document_id": document_id,
            "detail_url": detail_url,
            "detail_observation_id": observation["row"]["observation_id"],
            "record_text_sha256": record_text_sha256,
            "record_text_byte_size": len(record_text_bytes),
            "record_text_path": record_text_blob.relative_path,
            "document_date_raw": date_match.group(1) if date_match else None,
            "observed_at": observed_at,
        }
        self._record_once("snapshots", snapshot_row)

        match_id = stable_id(
            "fint-query-document-match",
            query_id,
            document_id,
            str(row_number),
        )
        match_row = {
            "schema": "nhi-rule-history/fint-query-document-match/v2",
            "match_id": match_id,
            "query_id": query_id,
            "document_id": document_id,
            "snapshot_id": snapshot_id,
            "row_number": row_number,
            "detail_url": detail_url,
            "detail_observation_id": observation["row"]["observation_id"],
            "observed_at": observed_at,
        }
        self._record_once("matches", match_row)

        fetch_attachments = (
            self.attachment_policy == "all"
            or (
                self.attachment_policy == "nhi_candidate"
                and NHI_ATTACHMENT_CANDIDATE_RE.search(
                    f"{normalized_number}\n{parser.record_text}"
                )
                is not None
            )
        )
        for ordinal, anchor in enumerate(parser.attachment_anchors(), 1):
            attachment_url = assert_allowed_url(
                urljoin(detail_url, anchor.href),
                (FINT_HOST,),
            )
            attachment_declaration_id = stable_id(
                "fint-attachment-declaration",
                snapshot_id,
                str(ordinal),
                attachment_url,
            )
            attachment_observation: dict[str, Any] | None = None
            fetch_state = "not_selected_by_policy"
            if fetch_attachments:
                attachment_observation = self._observe(
                    attachment_url,
                    resource_kind="fint_attachment",
                )
                fetch_state = "fetched"
            declaration_row = {
                "schema": "nhi-rule-history/fint-attachment-declaration/v1",
                "attachment_declaration_id": attachment_declaration_id,
                "document_id": document_id,
                "snapshot_id": snapshot_id,
                "match_id": match_id,
                "source_url": attachment_url,
                "source_label": anchor.text or "(official link has no label)",
                "source_label_missing": not bool(anchor.text),
                "attachment_ordinal": ordinal,
                "fetch_state": fetch_state,
            }
            self._record_once("attachment_declarations", declaration_row)
            if attachment_observation is None:
                continue
            attachment_snapshot_id = stable_id(
                "fint-attachment-snapshot",
                attachment_declaration_id,
                attachment_observation["row"]["content_sha256"],
            )
            attachment_row = {
                "schema": "nhi-rule-history/fint-attachment-snapshot/v4",
                "attachment_snapshot_id": attachment_snapshot_id,
                "attachment_declaration_id": attachment_declaration_id,
                "document_id": document_id,
                "snapshot_id": snapshot_id,
                "match_id": match_id,
                "source_url": attachment_url,
                "observation_id": attachment_observation["row"][
                    "observation_id"
                ],
                "content_sha256": attachment_observation["row"][
                    "content_sha256"
                ],
                "byte_size": attachment_observation["row"]["byte_size"],
                "content_path": attachment_observation["row"]["content_path"],
                "source_media_type": attachment_observation["row"][
                    "response_headers"
                ].get("content-type"),
                "detected_media_type": detect_media_type(
                    attachment_observation["payload"],
                    attachment_observation["row"]["response_headers"].get(
                        "content-type"
                    ),
                ),
                "observed_at": attachment_observation["row"]["observed_at"],
            }
            self._record_once("attachments", attachment_row)

    def _write_manifest(
        self,
        grouped: Mapping[tuple[str, ...], list[Seed]],
    ) -> dict[str, Any]:
        incomplete = [
            group_key
            for group_key in grouped
            if stable_id(
                "fint-keyword-query",
                *(group_key or ("<ALL_RECORDS>",)),
                self.start_date,
                self.end_date,
                "valid=3",
                "type=etype_",
            )
            not in self._indexes["queries"]
        ]
        if incomplete:
            raise ContractError(
                f"FINT crawl has incomplete keyword groups: {incomplete}"
            )
        files = []
        for path in self.paths.values():
            files.append(
                {
                    "filename": path.name,
                    "sha256": file_sha256(path),
                    "byte_size": path.stat().st_size,
                }
            )
        destination = self.run_dir / "fint-crawl-manifest.json"
        if destination.is_file():
            existing = json.loads(destination.read_text(encoding="utf-8"))
            if existing.get("crawler_version") != CRAWLER_VERSION:
                raise ContractError(
                    "FINT run directory belongs to another crawler version"
                )
            if (
                existing.get("status") == "complete_declared_keyword_set"
                and existing.get("counts")
                == {
                    name: len(index)
                    for name, index in self._indexes.items()
                }
                and existing.get("files") == files
            ):
                return existing
        manifest = {
            "schema": "nhi-rule-history/fint-frontier-crawl-manifest/v3",
            "crawler_version": CRAWLER_VERSION,
            "status": (
                "complete_declared_keyword_set"
                if not self._indexes["issues"]
                else "incomplete_blocking_issues"
            ),
            "completed_at": utc_now(),
            "query_contract": {
                "search_endpoint": FINT_SEARCH_URL,
                "detail_endpoint": FINT_DETAIL_URL,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "valid": "3",
                "record_type": "etype_",
                "single_threaded": True,
                "minimum_interval_seconds": self.min_interval_seconds,
                "max_results_per_query": self.max_results_per_query,
                "attachment_policy": self.attachment_policy,
            },
            "counts": {
                name: len(index)
                for name, index in self._indexes.items()
            },
            "coverage_claim": {
                "declared_query_frontier_complete": not incomplete,
                "all_declared_attachment_edges_recorded": not incomplete,
                "attachment_bytes_policy": self.attachment_policy,
                "unfiltered_date_partition_complete": (
                    len(grouped) == 1 and () in grouped and not incomplete
                ),
                "absolute_official_document_universe_complete": False,
                "reason": (
                    "Each run proves only its declared keyword/date query "
                    "frontier. Consecutive unfiltered yearly runs can jointly "
                    "enumerate the currently published FINT surface, but they "
                    "cannot prove that withdrawn or never-indexed records still "
                    "exist on the official service."
                ),
            },
            "files": files,
        }
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_bytes(canonical_json_bytes(manifest))
        os.replace(temporary, destination)
        return manifest


def load_seeds(path: Path) -> list[Seed]:
    return [Seed.from_mapping(row) for row in _iter_jsonl(path)]
