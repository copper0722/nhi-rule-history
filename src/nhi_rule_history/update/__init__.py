"""Continuous official-source update pipeline.

The update package deliberately separates immutable acquisition, bounded model
proposals, candidate staging, and canonical promotion.  The public runner never
grants a model authority to mutate legal history.
"""

from nhi_rule_history.update.bundle import BundleBuilder, acquire_notice_bundle
from nhi_rule_history.update.corpus_bundle import prepare_corpus_bundle
from nhi_rule_history.update.proposal import (
    PROPOSAL_SCHEMA,
    ProposalError,
    validate_proposal,
)
from nhi_rule_history.update.poll import PollObservation, observe_feed, verify_poll
from nhi_rule_history.update.pg_stage import load_update_candidate
from nhi_rule_history.update.rss import (
    NHI_RSS_URL,
    OfficialNhiClient,
    parse_rss,
)
from nhi_rule_history.update.workers import WorkerOrchestrator

__all__ = [
    "BundleBuilder",
    "NHI_RSS_URL",
    "OfficialNhiClient",
    "PROPOSAL_SCHEMA",
    "PollObservation",
    "ProposalError",
    "WorkerOrchestrator",
    "acquire_notice_bundle",
    "load_update_candidate",
    "parse_rss",
    "prepare_corpus_bundle",
    "observe_feed",
    "validate_proposal",
    "verify_poll",
]
