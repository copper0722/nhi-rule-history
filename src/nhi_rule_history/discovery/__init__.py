"""Official-source discovery adapters."""

from .compare import compare_discovery_runs
from .runner import discover_run

__all__ = ["compare_discovery_runs", "discover_run"]
