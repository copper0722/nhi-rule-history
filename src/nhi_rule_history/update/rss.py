"""Deterministic, fail-closed acquisition from the official NHI RSS surface."""

from __future__ import annotations

import base64
import email.utils
import http.cookiejar
import json
import os
import re
import time
import shutil
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPSHandler,
    Request,
    build_opener,
)
from xml.etree import ElementTree

from nhi_rule_history.contracts import (
    ContractError,
    assert_allowed_url,
    canonical_json_bytes,
    sha256_bytes,
    utc_now,
)
from nhi_rule_history.fetch.runner import media_type


NHI_RSS_URL = "https://www.nhi.gov.tw/ch/rss-3258-1.xml"
NHI_ALLOWED_HOSTS = ("www.nhi.gov.tw",)
NHI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15"
)
NHI_ACCEPT = (
    "application/rss+xml, application/xml;q=0.9, "
    "text/xml;q=0.8, */*;q=0.1"
)
NHI_ACCEPT_LANGUAGE = "zh-TW,zh;q=0.9,en;q=0.5"
HTTP_PROFILE_ID = "nhi-official-safari-http11/v1"
CDP_PROFILE_ID = "nhi-official-resident-chrome-cdp/v1"
TRANSPORT_ENV = "NHI_RULE_HISTORY_TRANSPORT"
CDP_ENDPOINT_ENV = "NHI_RULE_HISTORY_CDP_ENDPOINT"
_TRANSPORTS = ("curl", "resident-chrome-cdp")

_DETAIL_PATH_RE = re.compile(r"^/ch/cp-[^?#]+\.html$")
_ATTACHMENT_PATH_RE = re.compile(r"^/ch/dl-[^?#]+$")
_DRUG_NOUNS = (
    "藥品",
    "藥物",
)
_REIMBURSEMENT_RULE_TERMS = (
    "給付規定",
    "給付條件",
    "給付範圍",
)
RSS_CLASSIFIER_VERSION = "nhi-rule-history-drug-rule-classifier/2.0.0"
RSS_LEGACY_CLASSIFIER_VERSION = "nhi-rule-history-drug-rule-classifier/1.0.0"


def active_transport() -> str:
    """Deployment-selected transport; one profile per process, never mixed.

    The Safari/curl profile started drawing Cloudflare 403 challenges from
    www.nhi.gov.tw in 2026-07. A resident, signed-in Chrome on the same host
    passes the same challenge, so the deployment may pin this process to the
    resident-Chrome DevTools transport instead. The choice is environment
    configuration, not a silent per-request fallback: request_profile_sha256
    in every queue row records which profile produced the bytes.
    """
    value = (os.environ.get(TRANSPORT_ENV) or "curl").strip() or "curl"
    if value not in _TRANSPORTS:
        raise ContractError(
            f"{TRANSPORT_ENV} must be one of {_TRANSPORTS}, got {value!r}"
        )
    return value


def http_profile_contract() -> dict[str, object]:
    if active_transport() == "resident-chrome-cdp":
        return {
            "profile_id": CDP_PROFILE_ID,
            "method": "GET",
            "protocol": "browser-negotiated",
            "redirect_policy": "reject",
            "allowed_hosts": list(NHI_ALLOWED_HOSTS),
            "headers": {
                "User-Agent": "resident-chrome-browser-managed",
                "Accept": "per-request-fetch-header",
                "Accept-Language": "browser-managed",
            },
            "cookies": "resident-chrome-profile-managed",
            "status_policy": "HTTP-200-only",
            "transport": "in-page fetch over Chrome DevTools Protocol; "
                         "body bytes via arrayBuffer, base64 over the wire",
        }
    return {
        "profile_id": HTTP_PROFILE_ID,
        "method": "GET",
        "protocol": "HTTP/1.1",
        "redirect_policy": "reject",
        "allowed_hosts": list(NHI_ALLOWED_HOSTS),
        "headers": {
            "User-Agent": NHI_USER_AGENT,
            "Accept": NHI_ACCEPT,
            "Accept-Language": NHI_ACCEPT_LANGUAGE,
            "Cache-Control": "no-cache",
        },
        "cookies": "ephemeral-memory-or-0600-tempfile-never-logged",
        "status_policy": "HTTP-200-only",
    }


def http_profile_sha256() -> str:
    return sha256_bytes(canonical_json_bytes(http_profile_contract()))


@dataclass(frozen=True)
class OfficialResponse:
    request_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    observed_at: str


@dataclass(frozen=True)
class RssItem:
    guid: str
    title: str
    link: str
    description: str
    published_at: str | None
    sequence: int

    @property
    def is_likely_drug_rule(self) -> bool:
        return self.is_likely_drug_rule_for(RSS_CLASSIFIER_VERSION)

    def is_likely_drug_rule_for(self, classifier_version: str) -> bool:
        haystack = f"{self.title}\n{self.description}"
        if classifier_version == RSS_LEGACY_CLASSIFIER_VERSION:
            return any(
                keyword in haystack
                for keyword in (
                    "藥品給付",
                    "給付規定",
                    "藥物給付",
                    "全民健康保險藥物給付",
                )
            )
        if classifier_version != RSS_CLASSIFIER_VERSION:
            raise ContractError("unsupported RSS drug-rule classifier version")
        # Descriptions contain breadcrumb boilerplate such as
        # ``健保藥品與特材`` even for special-material notices.  The title is
        # the stable public selection surface, so both signals must occur
        # there.  This only selects work for review; it does not adjudicate
        # the legal content.
        return (
            any(noun in self.title for noun in _DRUG_NOUNS)
            and any(
                term in self.title for term in _REIMBURSEMENT_RULE_TERMS
            )
        )

    def as_dict(
        self,
        *,
        classifier_version: str = RSS_CLASSIFIER_VERSION,
    ) -> dict[str, object]:
        return {
            "guid": self.guid,
            "title": self.title,
            "link": self.link,
            "description": self.description,
            "published_at": self.published_at,
            "sequence": self.sequence,
            "is_likely_drug_rule": self.is_likely_drug_rule_for(
                classifier_version
            ),
        }


@dataclass(frozen=True)
class AttachmentLink:
    url: str
    label: str
    sequence: int


class _AttachmentParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[AttachmentLink] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        if _ATTACHMENT_PATH_RE.match(urlsplit(absolute).path):
            self._href = absolute
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        label = " ".join("".join(self._parts).split())
        self.links.append(
            AttachmentLink(
                url=self._href,
                label=label,
                sequence=len(self.links),
            )
        )
        self._href = None
        self._parts = []


class OfficialNhiClient:
    """Cookie-aware GET-only client with a versioned official request profile."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_bytes: int = 64 * 1024 * 1024,
        ca_file: str | None = None,
        allow_insecure_tls: bool = False,
        opener: object | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.ca_file = ca_file
        self.allow_insecure_tls = allow_insecure_tls
        self._curl_temp: tempfile.TemporaryDirectory[str] | None = None
        self._transport = active_transport()
        if self._transport == "resident-chrome-cdp":
            endpoint = (os.environ.get(CDP_ENDPOINT_ENV) or "").strip()
            if not endpoint.startswith("http://127.0.0.1:") and not endpoint.startswith(
                "http://localhost:"
            ):
                raise ContractError(
                    f"{CDP_ENDPOINT_ENV} must name a loopback DevTools endpoint"
                )
            self._cdp_endpoint = endpoint.rstrip("/")
            self._cdp_target: dict[str, str] | None = None
            self._opener = None
            return
        if opener is not None:
            self._opener = opener
            return
        curl = shutil.which("curl")
        if curl is None:
            raise ContractError(
                "curl is required for the versioned official NHI HTTP profile"
            )
        self._opener = None
        self._curl = curl
        self._curl_temp = tempfile.TemporaryDirectory(
            prefix="nhi-rule-history-http-"
        )
        cookie_path = Path(self._curl_temp.name) / "cookies.txt"
        cookie_path.touch(mode=0o600)
        self._cookie_path = cookie_path

    @staticmethod
    def request_headers(*, accept: str, referer: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": NHI_USER_AGENT,
            "Accept": accept,
            "Accept-Language": NHI_ACCEPT_LANGUAGE,
            "Cache-Control": "no-cache",
        }
        if referer is not None:
            headers["Referer"] = referer
        return headers

    def get(
        self,
        url: str,
        *,
        accept: str = NHI_ACCEPT,
        referer: str | None = None,
    ) -> OfficialResponse:
        request_url = assert_allowed_url(url, NHI_ALLOWED_HOSTS)
        if self._transport == "resident-chrome-cdp":
            return self._cdp_get(request_url, accept=accept)
        request = Request(
            request_url,
            method="GET",
            headers=self.request_headers(accept=accept, referer=referer),
        )
        if self._opener is None:
            return self._curl_get(
                request_url,
                accept=accept,
                referer=referer,
            )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
            with response:
                final_url = assert_allowed_url(
                    response.geturl(), NHI_ALLOWED_HOSTS
                )
                status = int(response.getcode())
                if status != 200:
                    raise ContractError(
                        f"official NHI request returned HTTP {status}"
                    )
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > self.max_bytes:
                    raise ContractError("official response exceeds max_bytes")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise ContractError("official response exceeds max_bytes")
                headers = {
                    name.lower(): value
                    for name, value in response.headers.items()
                    if name.lower()
                    in {
                        "content-type",
                        "content-length",
                        "content-disposition",
                        "etag",
                        "last-modified",
                    }
                }
        except ContractError:
            raise
        except Exception as exc:
            raise ContractError(
                f"official NHI request failed: {type(exc).__name__}"
            ) from exc
        return OfficialResponse(
            request_url=request_url,
            final_url=final_url,
            status_code=status,
            headers=headers,
            body=body,
            observed_at=utc_now(),
        )

    def _curl_get(
        self,
        request_url: str,
        *,
        accept: str,
        referer: str | None,
    ) -> OfficialResponse:
        if self._curl_temp is None:
            raise ContractError("official curl transport is not initialized")
        temporary = Path(self._curl_temp.name)
        body_path = temporary / "response.body"
        header_path = temporary / "response.headers"
        body_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)
        argv = [
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
            "--cookie",
            str(self._cookie_path),
            "--cookie-jar",
            str(self._cookie_path),
            "--output",
            str(body_path),
            "--dump-header",
            str(header_path),
            "--write-out",
            "%{http_code}",
        ]
        for name, value in self.request_headers(
            accept=accept, referer=referer
        ).items():
            argv.extend(["--header", f"{name}: {value}"])
        if self.ca_file:
            argv.extend(["--cacert", self.ca_file])
        if self.allow_insecure_tls:
            argv.append("--insecure")
        argv.extend(["--url", request_url])
        completed = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            raise ContractError(
                f"official NHI request failed: curl_exit_{completed.returncode}"
            )
        try:
            status = int(completed.stdout.strip())
        except ValueError as exc:
            raise ContractError("official curl status is malformed") from exc
        body = body_path.read_bytes() if body_path.is_file() else b""
        if len(body) > self.max_bytes:
            raise ContractError("official response exceeds max_bytes")
        if not header_path.is_file():
            raise ContractError("official response headers are missing")
        raw_headers = header_path.read_text(
            encoding="iso-8859-1", errors="strict"
        )
        header_blocks = [
            block
            for block in re.split(r"\r?\n\r?\n", raw_headers)
            if block.startswith("HTTP/")
        ]
        if len(header_blocks) != 1:
            raise ContractError("official response redirected unexpectedly")
        headers: dict[str, str] = {}
        for line in header_blocks[0].splitlines()[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            lowered = name.strip().lower()
            if lowered in {
                "content-type",
                "content-length",
                "content-disposition",
                "etag",
                "last-modified",
            }:
                headers[lowered] = value.strip()
        if status != 200:
            raise ContractError(
                f"official NHI request returned HTTP {status}"
            )
        return OfficialResponse(
            request_url=request_url,
            final_url=request_url,
            status_code=status,
            headers=headers,
            body=body,
            observed_at=utc_now(),
        )

    # ------------------------------------------------------------------
    # resident-chrome-cdp transport (profile nhi-official-resident-chrome-cdp/v1)
    #
    # The page is opened AT the NHI origin and the request is an in-page,
    # same-origin fetch: same TLS/JA3, same cookies, same fingerprint as the
    # signed-in resident browser that demonstrably passes the Cloudflare
    # challenge the curl profile fails. Bytes come back via arrayBuffer ->
    # base64, so the stored artifact is byte-identical to what the browser
    # received. Rules of the shared browser: open ONE tab, read, close ONLY
    # that tab; never touch the rest of the runtime.
    # ------------------------------------------------------------------

    def _cdp_call(self, ws, call_id: int, method: str, timeout: float, **params):
        ws.send(json.dumps({"id": call_id, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError(f"cdp call {method} timed out")
            message = json.loads(ws.recv(timeout=remaining))
            if message.get("id") == call_id:
                if "error" in message:
                    raise ContractError(
                        f"cdp call {method} failed: {message['error'].get('message', '?')}"
                    )
                return message.get("result", {})

    def _cdp_eval(self, ws, call_id: int, expression: str, timeout: float):
        result = self._cdp_call(
            ws, call_id, "Runtime.evaluate", timeout,
            expression=expression, awaitPromise=True, returnByValue=True,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"].get("text", "evaluation failed")
            raise ContractError(f"cdp evaluation failed: {detail}")
        return result.get("result", {}).get("value")

    def _cdp_get(self, request_url: str, *, accept: str) -> OfficialResponse:
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as exc:  # pragma: no cover - deployment prerequisite
            raise ContractError(
                "resident-chrome-cdp transport requires the websockets package"
            ) from exc
        from urllib.request import urlopen as _urlopen

        parts = urlsplit(request_url)
        origin = f"{parts.scheme}://{parts.netloc}/"
        from urllib.parse import quote as _quote
        open_req = Request(
            f"{self._cdp_endpoint}/json/new?{_quote(origin, safe='')}", method="PUT"
        )
        with _urlopen(open_req, timeout=10) as response:
            target = json.loads(response.read().decode("utf-8"))
        target_id = target.get("id")
        ws_url = target.get("webSocketDebuggerUrl")
        if not target_id or not ws_url:
            raise ContractError("cdp target creation returned no debugger URL")

        fetch_js = (
            "fetch(%s, {credentials:'include', redirect:'error', cache:'no-store',"
            " headers:{'Accept': %s, 'Cache-Control':'no-cache'},"
            " signal: AbortSignal.timeout(%d)})"
            ".then(async r => {"
            " const buf = new Uint8Array(await r.arrayBuffer());"
            " if (buf.length > %d) return '\x00ERR body exceeds max_bytes';"
            " let bin = ''; const step = 0x8000;"
            " for (let i = 0; i < buf.length; i += step)"
            "   bin += String.fromCharCode.apply(null, buf.subarray(i, i + step));"
            " const names = ['content-type','content-length','content-disposition',"
            "'etag','last-modified'];"
            " const headers = {};"
            " for (const name of names) {"
            "   const value = r.headers.get(name);"
            "   if (value !== null) headers[name] = value; }"
            " return JSON.stringify({status: r.status, final_url: r.url,"
            " headers: headers, b64: btoa(bin)});"
            "})"
            ".catch(e => '\x00ERR ' + e.message)"
        ) % (
            json.dumps(request_url), json.dumps(accept),
            int(self.timeout_seconds * 1000), self.max_bytes,
        )

        try:
            with ws_connect(ws_url, max_size=None,
                            open_timeout=15, close_timeout=5) as ws:
                last = "no attempt ran"
                for attempt in range(1, 5):
                    # The challenged origin can re-navigate after load and the
                    # first in-page fetch can fire before the document settles
                    # (both measured on this origin, 2026-08-31): settle, then
                    # confirm readiness, then fetch; every failure mode is one
                    # more retryable outcome.
                    time.sleep(2)
                    try:
                        ready = self._cdp_eval(
                            ws, attempt * 10, "document.readyState", 30
                        )
                        if ready not in ("interactive", "complete"):
                            last = f"document not ready: {ready!r}"
                            continue
                        value = self._cdp_eval(
                            ws, attempt * 10 + 1, fetch_js,
                            self.timeout_seconds + 30,
                        )
                    except ContractError as exc:
                        last = str(exc)[:160]
                        continue
                    if isinstance(value, str) and value.startswith("\x00ERR "):
                        last = value[1:]
                        continue
                    if not isinstance(value, str) or not value:
                        last = "empty evaluation result"
                        continue
                    payload = json.loads(value)
                    status = int(payload["status"])
                    if status != 200:
                        raise ContractError(
                            f"official NHI request returned HTTP {status}"
                        )
                    body = base64.b64decode(payload["b64"])
                    if len(body) > self.max_bytes:
                        raise ContractError("official response exceeds max_bytes")
                    final_url = assert_allowed_url(
                        payload.get("final_url") or request_url, NHI_ALLOWED_HOSTS
                    )
                    headers = {
                        str(name).lower(): str(value_)
                        for name, value_ in dict(payload.get("headers", {})).items()
                    }
                    return OfficialResponse(
                        request_url=request_url,
                        final_url=final_url,
                        status_code=status,
                        headers=headers,
                        body=body,
                        observed_at=utc_now(),
                    )
                raise ContractError(f"official NHI request failed: {last}")
        finally:
            try:
                close_req = Request(
                    f"{self._cdp_endpoint}/json/close/{target_id}", method="GET"
                )
                with _urlopen(close_req, timeout=10):
                    pass
            except Exception:  # noqa: BLE001 - closing our own tab, best effort
                pass

    def get_feed(self, url: str = NHI_RSS_URL) -> OfficialResponse:
        response = self.get(url, accept=NHI_ACCEPT)
        content_type = response.headers.get("content-type", "").lower()
        if "xml" not in content_type:
            raise ContractError("official RSS response is not XML")
        parse_rss(response.body)
        return response

    def get_detail(self, url: str) -> OfficialResponse:
        path = urlsplit(assert_allowed_url(url, NHI_ALLOWED_HOSTS)).path
        if not _DETAIL_PATH_RE.match(path):
            raise ContractError("unexpected official NHI detail URL")
        response = self.get(
            url,
            accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        )
        if media_type(response.headers, response.body) != "text/html":
            raise ContractError("official detail response is not HTML")
        return response

    def get_attachment(
        self, url: str, *, detail_url: str
    ) -> OfficialResponse:
        path = urlsplit(assert_allowed_url(url, NHI_ALLOWED_HOSTS)).path
        if not _ATTACHMENT_PATH_RE.match(path):
            raise ContractError("unexpected official NHI attachment URL")
        response = self.get(
            url,
            accept=(
                "application/vnd.oasis.opendocument.text,application/pdf,"
                "application/octet-stream;q=0.8,*/*;q=0.1"
            ),
            referer=detail_url,
        )
        detected = media_type(response.headers, response.body)
        if detected in {"text/html", "application/xml", "text/xml"}:
            raise ContractError(
                "attachment returned markup instead of a document"
            )
        return response


def _required_text(parent: ElementTree.Element, tag: str) -> str:
    child = parent.find(tag)
    value = "".join(child.itertext()).strip() if child is not None else ""
    if not value:
        raise ContractError(f"RSS item is missing {tag}")
    return value


def _parse_pub_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("RSS pubDate is malformed") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_rss(payload: bytes) -> list[RssItem]:
    """Parse one exact RSS response; reject XML expansion and silent collapse."""

    upper_prefix = payload[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise ContractError("RSS XML declarations are not allowed")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ContractError("official RSS XML is malformed") from exc
    if root.tag != "rss":
        raise ContractError("official feed root must be rss")
    channel = root.find("channel")
    if channel is None:
        raise ContractError("official RSS channel is missing")
    items: list[RssItem] = []
    seen: set[str] = set()
    for sequence, item in enumerate(channel.findall("item")):
        link = assert_allowed_url(_required_text(item, "link"), NHI_ALLOWED_HOSTS)
        if not _DETAIL_PATH_RE.match(urlsplit(link).path):
            raise ContractError("RSS item link is not an NHI detail page")
        guid_node = item.find("guid")
        guid = (
            "".join(guid_node.itertext()).strip()
            if guid_node is not None
            else link
        )
        if not guid:
            guid = link
        if guid in seen:
            raise ContractError("RSS contains a duplicate item identity")
        seen.add(guid)
        description_node = item.find("description")
        description = (
            "".join(description_node.itertext()).strip()
            if description_node is not None
            else ""
        )
        pub_date = item.findtext("pubDate")
        items.append(
            RssItem(
                guid=guid,
                title=_required_text(item, "title"),
                link=link,
                description=description,
                published_at=_parse_pub_date(pub_date),
                sequence=sequence,
            )
        )
    if not items:
        raise ContractError("official RSS unexpectedly contains zero items")
    return items


def parse_attachment_links(
    detail_url: str,
    payload: bytes,
    *,
    require_nonempty: bool = True,
) -> list[AttachmentLink]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("utf-8", errors="replace")
    parser = _AttachmentParser(detail_url)
    parser.feed(text)
    unique: list[AttachmentLink] = []
    seen: set[str] = set()
    for link in parser.links:
        canonical = assert_allowed_url(link.url, NHI_ALLOWED_HOSTS)
        if canonical in seen:
            continue
        seen.add(canonical)
        unique.append(
            AttachmentLink(canonical, link.label, len(unique))
        )
    if not unique and require_nonempty:
        raise ContractError("official detail declares no downloadable attachments")
    return unique


def filter_new_items(
    items: Iterable[RssItem],
    observed_guids: Iterable[str],
    *,
    likely_drug_rules_only: bool = True,
) -> list[RssItem]:
    observed = set(observed_guids)
    return [
        item
        for item in items
        if item.guid not in observed
        and (item.is_likely_drug_rule or not likely_drug_rules_only)
    ]
