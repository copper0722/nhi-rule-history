from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Mapping
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


@dataclass(frozen=True)
class HttpResponse:
    request_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes


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

    def get(self, url: str) -> HttpResponse:
        request_url = assert_allowed_url(url, self.allowed_hosts)
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
