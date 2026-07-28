from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from nhi_rule_history.fetch.http import HttpClient


@dataclass
class DiscoveryContext:
    plan_sha256: str
    adapter: dict[str, Any]
    client: HttpClient
    recorder: Any


class DiscoveryAdapter(Protocol):
    kind: str

    def discover(self, context: DiscoveryContext) -> dict[str, Any]:
        """Discover resources and return adapter-level public statistics."""
