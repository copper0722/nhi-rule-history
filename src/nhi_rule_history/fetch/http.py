from __future__ import annotations

import re
import shutil
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nhi_rule_history.contracts import ContractError, assert_allowed_url


SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "content-disposition",
    "etag",
    "last-modified",
}

NHI_HOST = "www.nhi.gov.tw"
NHI_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15"
)
NHI_BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)
NHI_BROWSER_ACCEPT_LANGUAGE = "zh-TW,zh;q=0.9,en;q=0.5"


@dataclass(frozen=True)
class HttpResponse:
    request_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes


class _NhiCurlTransport:
    """Ephemeral-cookie HTTP prime followed by an exact HTTPS GET.

    NHI's Cloudflare edge intermittently rejects Python urllib even with the
    same browser headers.  The HTTP endpoint issues a same-path HTTPS redirect
    and an ephemeral cookie.  We verify that redirect exactly, never follow it
    inside the prime request, and then fetch the already-validated HTTPS URL.
    Cookie values stay inside a mode-0600 temporary jar and are never returned
    or logged.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_bytes: int,
        ca_file: str | None,
        allow_insecure_tls: bool,
    ) -> None:
        curl = shutil.which("curl")
        if curl is None:
            raise ContractError(
                "curl is required for the official NHI acquisition profile"
            )
        self._curl = curl
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.ca_file = ca_file
        self.allow_insecure_tls = allow_insecure_tls
        self._temporary = tempfile.TemporaryDirectory(
            prefix="nhi-rule-history-current-http-"
        )
        self._root = Path(self._temporary.name)
        self._cookie_path = self._root / "cookies.txt"
        self._cookie_path.touch(mode=0o600)

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
            self._temporary = None

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def _prime_url(request_url: str) -> str:
        parsed = urlsplit(request_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != NHI_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ContractError("invalid official NHI bootstrap target")
        return urlunsplit(
            ("http", parsed.netloc, parsed.path, parsed.query, "")
        )

    def _common_argv(self) -> list[str]:
        argv = [
            self._curl,
            "--http1.1",
            "--silent",
            "--show-error",
            "--request",
            "GET",
            "--max-redirs",
            "0",
            "--connect-timeout",
            str(max(1, int(self.timeout_seconds))),
            "--max-time",
            str(max(1, int(self.timeout_seconds))),
            "--user-agent",
            NHI_BROWSER_USER_AGENT,
            "--header",
            f"Accept: {NHI_BROWSER_ACCEPT}",
            "--header",
            f"Accept-Language: {NHI_BROWSER_ACCEPT_LANGUAGE}",
            "--header",
            "Cache-Control: no-cache",
        ]
        if self.ca_file:
            argv.extend(["--cacert", self.ca_file])
        if self.allow_insecure_tls:
            argv.append("--insecure")
        return argv

    def _prime(self, request_url: str) -> None:
        prime_url = self._prime_url(request_url)
        completed = subprocess.run(
            [
                *self._common_argv(),
                "--proto",
                "=http",
                "--cookie-jar",
                str(self._cookie_path),
                "--output",
                str(self._root / "prime.body"),
                "--write-out",
                "%{http_code}\n%{redirect_url}",
                "--url",
                prime_url,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            raise ContractError(
                "official NHI bootstrap failed: "
                f"curl_exit_{completed.returncode}"
            )
        lines = completed.stdout.splitlines()
        if len(lines) != 2 or lines[0] not in {"301", "302", "307", "308"}:
            raise ContractError("official NHI bootstrap status is invalid")
        redirected = assert_allowed_url(lines[1], (NHI_HOST,))
        if redirected != request_url:
            raise ContractError(
                "official NHI bootstrap redirected to a different HTTPS URL"
            )

    def get(self, request_url: str) -> HttpResponse:
        self._prime(request_url)
        body_path = self._root / "response.body"
        header_path = self._root / "response.headers"
        body_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                *self._common_argv(),
                "--proto",
                "=https",
                "--cookie",
                str(self._cookie_path),
                "--cookie-jar",
                str(self._cookie_path),
                "--max-filesize",
                str(self.max_bytes),
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
                "official NHI request failed: "
                f"curl_exit_{completed.returncode}"
            )
        lines = completed.stdout.splitlines()
        if len(lines) != 3 or lines[0] != "200" or lines[1]:
            raise ContractError("official NHI response status is invalid")
        final_url = assert_allowed_url(lines[2], (NHI_HOST,))
        if final_url != request_url:
            raise ContractError(
                "official NHI response changed the requested HTTPS URL"
            )
        body = body_path.read_bytes() if body_path.is_file() else b""
        if len(body) > self.max_bytes:
            raise ContractError("response exceeds max_bytes during download")
        if not header_path.is_file():
            raise ContractError("official NHI response headers are missing")
        raw_headers = header_path.read_text(
            encoding="iso-8859-1", errors="strict"
        )
        header_blocks = [
            block
            for block in re.split(r"\r?\n\r?\n", raw_headers)
            if block.startswith("HTTP/")
        ]
        if len(header_blocks) != 1:
            raise ContractError("official NHI response redirected unexpectedly")
        headers: dict[str, str] = {}
        for line in header_blocks[0].splitlines()[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            normalized_name = name.strip().lower()
            if normalized_name in SAFE_RESPONSE_HEADERS:
                headers[normalized_name] = value.strip()
        content_length = headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            raise ContractError(
                f"response exceeds max_bytes before download: {content_length}"
            )
        return HttpResponse(
            request_url=request_url,
            final_url=final_url,
            status_code=200,
            headers=headers,
            body=body,
        )


class HttpClient:
    """Small GET-only transport with explicit host and response-size gates."""

    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        *,
        timeout_seconds: float = 30.0,
        max_bytes: int = 256 * 1024 * 1024,
        user_agent: str = "nhi-rule-history/0.2 (+public-source-archive)",
        ca_file: str | None = None,
        allow_insecure_tls: bool = False,
    ):
        self.allowed_hosts = allowed_hosts
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        if allow_insecure_tls:
            self.context = ssl._create_unverified_context()  # noqa: SLF001
        else:
            self.context = ssl.create_default_context(cafile=ca_file)
        self._nhi_transport = (
            _NhiCurlTransport(
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
                ca_file=ca_file,
                allow_insecure_tls=allow_insecure_tls,
            )
            if NHI_HOST in allowed_hosts
            else None
        )

    def get(self, url: str) -> HttpResponse:
        request_url = assert_allowed_url(url, self.allowed_hosts)
        if (
            urlsplit(request_url).hostname == NHI_HOST
            and self._nhi_transport is not None
        ):
            return self._nhi_transport.get(request_url)
        request = Request(
            request_url,
            method="GET",
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
        )
        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
                context=self.context,
            ) as response:
                final_url = assert_allowed_url(response.url, self.allowed_hosts)
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.max_bytes:
                    raise ContractError(
                        f"response exceeds max_bytes before download: {content_length}"
                    )
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise ContractError("response exceeds max_bytes during download")
                headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in SAFE_RESPONSE_HEADERS
                }
                return HttpResponse(
                    request_url=request_url,
                    final_url=final_url,
                    status_code=int(response.status),
                    headers=headers,
                    body=body,
                )
        except HTTPError as exc:
            raise ContractError(f"HTTP {exc.code} for {request_url}") from exc
        except URLError as exc:
            raise ContractError(f"network error for {request_url}: {exc.reason}") from exc

    def close(self) -> None:
        if self._nhi_transport is not None:
            self._nhi_transport.close()
