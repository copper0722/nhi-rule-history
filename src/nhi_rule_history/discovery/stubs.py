from __future__ import annotations

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.discovery.base import DiscoveryContext


class PlannedAdapter:
    """Fail-closed placeholder for declared source families not implemented yet."""

    def __init__(self, kind: str):
        self.kind = kind

    def discover(self, context: DiscoveryContext) -> dict[str, object]:
        raise ContractError(
            f"adapter {context.adapter['id']!r} ({self.kind}) is declared but not implemented"
        )
