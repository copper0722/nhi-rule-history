from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from nhi_rule_history.annotation_stage import (
    AnnotationStageError,
    load_annotation_stage,
)
from nhi_rule_history.contracts import ContractError, SourcePlan, write_json
from nhi_rule_history.current_anchor_parity import (
    current_anchor_occurrence_parity,
)
from nhi_rule_history.current_anchor_clause_parity import (
    analyze_current_anchor_clause_parity,
)
from nhi_rule_history.discovery import compare_discovery_runs, discover_run
from nhi_rule_history.export.canonical import CanonicalError
from nhi_rule_history.export.stage import (
    ExportError,
    export_stage_from_connection,
    verify_export_directory,
)
from nhi_rule_history.fetch import fetch_run
from nhi_rule_history.parsers.odt import parse_verified_odt_run
from nhi_rule_history.pg import (
    AcquisitionLoadError,
    StructuralLoadError,
    load_acquisition_run,
    load_structural_run,
)
from nhi_rule_history.raw.verify import verify_raw
from nhi_rule_history.release import (
    PrepareError,
    prepare_release,
    prepare_v2_evidence_release,
)
from nhi_rule_history.terminology import (
    DEFAULT_ALIAS_PROPOSAL,
    TerminologyError,
    load_terminology,
    preview_terminology,
    verify_terminology,
)
from nhi_rule_history.update.bundle import acquire_notice_bundle
from nhi_rule_history.update.corpus_bundle import prepare_corpus_bundle
from nhi_rule_history.update.historical_bundle import (
    materialize_historical_notice_bundles,
    verify_historical_bundle_batch,
)
from nhi_rule_history.update.poll import observe_feed
from nhi_rule_history.update.pg_stage import (
    UpdateStageLoadError,
    load_update_candidate,
)
from nhi_rule_history.update.pg_queue import (
    PARTITION_RECOVERY_DISPATCH_CONTRACT,
    UpdateQueueError,
    admit_partition_recovery,
    append_work_transition,
    authorize_partition_recovery,
    close_partition_recovery_generation,
    consume_partition_recovery_dispatch,
    finish_partition_recovery_route,
    load_partition_recovery_evidence,
    load_poll_package,
    reserve_partition_recovery_route,
    revoke_partition_recovery,
    show_partition_recovery,
    verify_partition_recovery_admission,
)
from nhi_rule_history.update.proposal import ProposalError
from nhi_rule_history.update.rss import OfficialNhiClient, parse_rss
from nhi_rule_history.update.workers import (
    WorkerFailure,
    WorkerOrchestrator,
    WorkerSpec,
)


def _network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--ca-file")
    parser.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help=(
            "Explicit compatibility escape hatch for an official endpoint whose "
            "certificate chain the local Python runtime rejects; never the default"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nhi-rule-history")
    subparsers = parser.add_subparsers(dest="command", required=True)
    root = Path(__file__).resolve().parents[2]

    plan = subparsers.add_parser("plan-validate", help="validate source-plan/v2")
    plan.add_argument("--plan", type=Path, required=True)

    discover = subparsers.add_parser(
        "discover", help="enumerate official resources into a resumable run directory"
    )
    discover.add_argument("--plan", type=Path, required=True)
    discover.add_argument("--run-dir", type=Path, required=True)
    _network_options(discover)

    fetch = subparsers.add_parser(
        "fetch", help="fetch discovered resources into the content-addressed raw store"
    )
    fetch.add_argument("--plan", type=Path, required=True)
    fetch.add_argument("--run-dir", type=Path, required=True)
    fetch.add_argument(
        "--refresh-successes",
        action="store_true",
        help="explicitly re-fetch resources that already have verified raw bytes",
    )
    _network_options(fetch)

    verify = subparsers.add_parser(
        "verify-raw", help="verify manifests, hashes, foreign keys, and coverage offline"
    )
    verify.add_argument("--run-dir", type=Path, required=True)

    compare = subparsers.add_parser(
        "compare-discovery",
        help="compare two independently enumerated resource-key sets offline",
    )
    compare.add_argument("--pass-a", type=Path, required=True)
    compare.add_argument("--pass-b", type=Path, required=True)
    compare.add_argument("--output", type=Path)

    parse_odt = subparsers.add_parser(
        "parse-odt",
        help="parse verified official ODT attachments into structural v2 rows",
    )
    parse_odt.add_argument("--run-dir", type=Path, required=True)
    parse_odt.add_argument("--stage-dir", type=Path, required=True)
    parse_odt.add_argument("--parse-run-id", required=True)

    anchor_parity = subparsers.add_parser(
        "anchor-parity-preflight",
        help=(
            "compare current whole versus split ODT designation/header "
            "occurrences without claiming full-clause parity"
        ),
    )
    anchor_parity.add_argument("--stage-dir", type=Path, required=True)
    anchor_parity.add_argument("--output", type=Path)

    anchor_clause_parity = subparsers.add_parser(
        "anchor-clause-parity",
        help=(
            "reconstruct and compare full current whole-versus-chapter "
            "clauses without making a history-completeness claim"
        ),
    )
    anchor_clause_parity.add_argument(
        "--stage-dir", type=Path, required=True
    )
    anchor_clause_parity.add_argument("--output", type=Path)

    terminology_preview = subparsers.add_parser(
        "terminology-preview",
        help=(
            "scan every active current-clause source block without writing "
            "PostgreSQL"
        ),
    )
    terminology_preview.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    terminology_preview.add_argument(
        "--alias-proposal",
        type=Path,
        default=DEFAULT_ALIAS_PROPOSAL,
    )
    terminology_preview.add_argument("--publication-run-id")
    terminology_preview.add_argument("--seed-enrichment-run-id")

    terminology_load = subparsers.add_parser(
        "terminology-load",
        help=(
            "scan, transactionally seal, fresh-verify, and activate one "
            "normalized terminology run"
        ),
    )
    terminology_load.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    terminology_load.add_argument(
        "--alias-proposal",
        type=Path,
        default=DEFAULT_ALIAS_PROPOSAL,
    )
    terminology_load.add_argument("--publication-run-id")
    terminology_load.add_argument("--seed-enrichment-run-id")
    terminology_load.add_argument(
        "--no-activate", action="store_true"
    )

    terminology_verify = subparsers.add_parser(
        "terminology-verify",
        help="fresh-verify one sealed terminology run from PostgreSQL",
    )
    terminology_verify.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    terminology_verify.add_argument("--tagging-run-id", required=True)

    load_acquisition = subparsers.add_parser(
        "load-acquisition",
        help="validate, transactionally seal, and fresh-verify a v2 acquisition run",
    )
    load_acquisition.add_argument("--run-dir", type=Path, required=True)
    load_acquisition.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )

    load_structural = subparsers.add_parser(
        "load-structural",
        help="validate, transactionally seal, and fresh-verify a v2 structural run",
    )
    load_structural.add_argument("--stage-dir", type=Path, required=True)
    load_structural.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )

    annotation_stage = subparsers.add_parser(
        "load-annotation-stage",
        help=(
            "extract exact ROC date markers from caller-supplied legacy "
            "article JSONL and seal them in the isolated PG evidence stage"
        ),
    )
    annotation_stage.add_argument("--input-jsonl", type=Path, required=True)
    annotation_stage.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )

    export = subparsers.add_parser(
        "export",
        help="export one exact sealed source-occurrence stage to JSONL and SQLite",
    )
    export.add_argument("--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN"))
    export.add_argument("--run-id", required=True)
    export.add_argument("--fingerprint", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument(
        "--schema",
        type=Path,
        default=root / "database" / "stage-sqlite-schema.sql",
    )

    verify_export = subparsers.add_parser(
        "verify-export",
        help="verify a prepared stage export without PostgreSQL",
    )
    verify_export.add_argument("--input-dir", type=Path, required=True)
    verify_export.add_argument("--run-id")
    verify_export.add_argument("--fingerprint")

    release = subparsers.add_parser(
        "release",
        help="prepare checksummed release assets locally; never publish",
    )
    release.add_argument("--export-dir", type=Path, required=True)
    release.add_argument("--output-dir", type=Path, required=True)

    release_v2 = subparsers.add_parser(
        "release-v2",
        help="prepare verified raw/structural v2 assets locally; never publish",
    )
    release_v2.add_argument("--run-dir", type=Path, required=True)
    release_v2.add_argument("--stage-dir", type=Path, required=True)
    release_v2.add_argument("--source-plan", type=Path, required=True)
    release_v2.add_argument("--eligibility-receipt", type=Path, required=True)
    release_v2.add_argument("--output-dir", type=Path, required=True)

    historical_bundles = subparsers.add_parser(
        "historical-bundles",
        help=(
            "materialize one verified source-local bundle per formal "
            "historical notice without interpreting legal effect"
        ),
    )
    historical_bundles.add_argument("--run-dir", type=Path, required=True)
    historical_bundles.add_argument(
        "--source-plan", type=Path, required=True
    )
    historical_bundles.add_argument(
        "--output-root", type=Path, required=True
    )

    verify_historical_bundles = subparsers.add_parser(
        "verify-historical-bundles",
        help=(
            "verify a materialized historical notice batch and every "
            "declared source artifact offline"
        ),
    )
    verify_historical_bundles.add_argument(
        "--output-root", type=Path, required=True
    )

    update_acquire = subparsers.add_parser(
        "update-acquire",
        help="acquire one RSS-listed official notice into an immutable source bundle",
    )
    update_acquire.add_argument("--bundle-root", type=Path, required=True)
    update_acquire.add_argument(
        "--detail-url",
        required=True,
        help="exact detail URL currently present in the official RSS response",
    )
    _network_options(update_acquire)

    update_poll = subparsers.add_parser(
        "update-poll",
        help="capture and parse one exact official RSS observation",
    )
    update_poll.add_argument("--poll-root", type=Path, required=True)
    update_poll.add_argument(
        "--observed-guids-json",
        type=Path,
        required=True,
        help="JSON array from the canonical stage database",
    )
    update_poll.add_argument("--previous-item-count", type=int)
    _network_options(update_poll)

    update_propose = subparsers.add_parser(
        "update-propose",
        help="run one primary and one failure-only fallback source proposal worker",
    )
    update_propose.add_argument("--bundle-path", type=Path, required=True)
    update_propose.add_argument("--candidate-root", type=Path, required=True)
    update_propose.add_argument(
        "--primary-spec",
        type=Path,
        required=True,
        help="private runtime JSON containing worker metadata and command argv",
    )
    update_propose.add_argument(
        "--fallback-spec",
        type=Path,
        required=True,
        help="private runtime JSON containing worker metadata and command argv",
    )

    update_corpus = subparsers.add_parser(
        "update-corpus",
        help="prepare one deterministic atomic corpus source bundle",
    )
    update_corpus.add_argument("--bundle-path", type=Path, required=True)
    update_corpus.add_argument("--corpus-root", type=Path, required=True)

    update_stage = subparsers.add_parser(
        "update-stage",
        help="transactionally load one verified bundle/candidate into PG stage only",
    )
    update_stage.add_argument("--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN"))
    update_stage.add_argument("--bundle-path", type=Path, required=True)
    update_stage.add_argument(
        "--candidate-receipt", type=Path, required=True
    )
    update_stage.add_argument("--bundle-relative-path", required=True)
    update_stage.add_argument("--activation-cut", required=True)
    update_stage.add_argument("--owner-key", required=True)
    update_stage.add_argument("--notification-window-start", required=True)
    update_stage.add_argument("--notification-window-end", required=True)

    update_poll_stage = subparsers.add_parser(
        "update-poll-stage",
        help="load one verified immutable RSS poll into the durable stage queue",
    )
    update_poll_stage.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    update_poll_stage.add_argument("--poll-path", type=Path, required=True)
    update_poll_stage.add_argument("--owner-key", required=True)
    update_poll_stage.add_argument("--poll-relative-root")

    update_transition = subparsers.add_parser(
        "update-queue-transition",
        help="append one evidence-bearing transition to one RSS work item",
    )
    update_transition.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    update_transition.add_argument("--work-item-id", required=True)
    update_transition.add_argument(
        "--to-state",
        required=True,
        choices=(
            "selected",
            "acquired",
            "corpus_registered",
            "proposal_running",
            "staged_needs_review",
            "staged_pending_anchor",
            "failed_terminal",
            "ignored_non_rule",
        ),
    )
    update_transition.add_argument("--actor-kind", required=True)
    update_transition.add_argument(
        "--evidence-json",
        type=Path,
        required=True,
        help="path to one non-empty JSON object",
    )
    update_transition.add_argument("--source-job-id", required=True)
    update_transition.add_argument("--bundle-receipt-id")
    update_transition.add_argument("--candidate-proposal-id")
    update_transition.add_argument("--recorded-at")

    partition_recovery = subparsers.add_parser(
        "partition-recovery",
        help="operator-only exact zero-call partition-recovery controls",
    )
    partition_commands = partition_recovery.add_subparsers(
        dest="partition_recovery_command",
        required=True,
    )
    partition_verify = partition_commands.add_parser(
        "verify",
        help="offline-verify one canonical typed admission payload",
    )
    partition_verify.add_argument(
        "--evidence-json", type=Path, required=True
    )
    partition_verify.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    partition_admit = partition_commands.add_parser(
        "admit",
        help="operator-admit one verified immutable recovery preimage",
    )
    partition_admit.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    partition_admit.add_argument(
        "--evidence-json", type=Path, required=True
    )
    partition_admit.add_argument("--actor-kind", required=True)
    partition_admit.add_argument("--admitted-at")
    partition_authorize = partition_commands.add_parser(
        "authorize",
        help="operator-authorize exact generation 2 with an expiry",
    )
    partition_authorize.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    partition_authorize.add_argument("--admission-id", required=True)
    partition_authorize.add_argument("--work-item-id", required=True)
    partition_authorize.add_argument(
        "--generation", type=int, required=True
    )
    partition_authorize.add_argument(
        "--admission-payload-sha256", required=True
    )
    partition_authorize.add_argument("--expires-at", required=True)
    partition_authorize.add_argument("--actor-kind", required=True)
    partition_authorize.add_argument("--authorized-at")
    partition_show = partition_commands.add_parser(
        "show",
        help="operator-show one exact admission or authorization",
    )
    partition_show.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    partition_show_selector = partition_show.add_mutually_exclusive_group(
        required=True
    )
    partition_show_selector.add_argument("--admission-id")
    partition_show_selector.add_argument("--authorization-id")
    partition_revoke = partition_commands.add_parser(
        "revoke",
        help="operator-revoke one exact unconsumed authorization",
    )
    partition_revoke.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    partition_revoke.add_argument("--authorization-id", required=True)
    partition_revoke.add_argument("--reason", required=True)
    partition_revoke.add_argument("--actor-kind", required=True)
    partition_revoke.add_argument("--revoked-at")

    dispatch_v2 = subparsers.add_parser(
        "dispatch-v2",
        help="runtime-only exact partition-recovery generation controls",
    )
    dispatch_commands = dispatch_v2.add_subparsers(
        dest="dispatch_v2_command",
        required=True,
    )
    dispatch_consume = dispatch_commands.add_parser(
        "consume",
        help="consume one exact admission/authorization/generation tuple",
    )
    dispatch_consume.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    dispatch_consume.add_argument("--work-item-id", required=True)
    dispatch_consume.add_argument(
        "--generation", type=int, required=True
    )
    dispatch_consume.add_argument("--authorization-id", required=True)
    dispatch_consume.add_argument("--admission-id", required=True)
    dispatch_consume.add_argument(
        "--admission-payload-sha256", required=True
    )
    dispatch_consume.add_argument(
        "--sealed-packet-manifest-sha256", required=True
    )
    dispatch_consume.add_argument(
        "--suitability-v2-receipt-sha256", required=True
    )
    dispatch_consume.add_argument("--job-fingerprint", required=True)
    dispatch_consume.add_argument("--prompt-sha256", required=True)
    dispatch_consume.add_argument("--route-policy-sha256", required=True)
    dispatch_consume.add_argument("--owner-key", required=True)
    dispatch_consume.add_argument(
        "--max-runtime-seconds", type=int, required=True
    )
    dispatch_consume.add_argument(
        "--dispatch-contract-version",
        default=PARTITION_RECOVERY_DISPATCH_CONTRACT,
    )
    dispatch_consume.add_argument("--consumed-at")

    dispatch_reserve = dispatch_commands.add_parser(
        "reserve-route",
        help="durably reserve primary or fallback before any worker call",
    )
    dispatch_reserve.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    dispatch_reserve.add_argument("--dispatch-claim-id", required=True)
    dispatch_reserve.add_argument("--work-item-id", required=True)
    dispatch_reserve.add_argument(
        "--generation", type=int, required=True
    )
    dispatch_reserve.add_argument("--authorization-id", required=True)
    dispatch_reserve.add_argument("--admission-id", required=True)
    dispatch_reserve.add_argument(
        "--route-ordinal", type=int, choices=(1, 2), required=True
    )
    dispatch_reserve.add_argument("--packet-sha256", required=True)
    dispatch_reserve.add_argument("--prompt-sha256", required=True)
    dispatch_reserve.add_argument("--recovery-job-id", required=True)
    dispatch_reserve.add_argument("--lease-id", required=True)
    dispatch_reserve.add_argument("--owner-key", required=True)
    dispatch_reserve.add_argument("--runtime-id", required=True)
    dispatch_reserve.add_argument("--provider", required=True)
    dispatch_reserve.add_argument("--model", required=True)
    dispatch_reserve.add_argument(
        "--controller-commit-sha256", required=True
    )
    dispatch_reserve.add_argument("--job-fingerprint", required=True)
    dispatch_reserve.add_argument("--reserved-at")

    dispatch_finish = dispatch_commands.add_parser(
        "finish-route",
        help="finish one reserved route with typed result evidence",
    )
    dispatch_finish.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    dispatch_finish.add_argument("--reservation-id", required=True)
    dispatch_finish.add_argument("--dispatch-claim-id", required=True)
    dispatch_finish.add_argument("--work-item-id", required=True)
    dispatch_finish.add_argument(
        "--generation", type=int, required=True
    )
    dispatch_finish.add_argument("--authorization-id", required=True)
    dispatch_finish.add_argument("--admission-id", required=True)
    dispatch_finish.add_argument(
        "--route-ordinal", type=int, choices=(1, 2), required=True
    )
    dispatch_finish.add_argument("--attempt-namespace", required=True)
    dispatch_finish.add_argument("--job-fingerprint", required=True)
    dispatch_finish.add_argument("--recovery-job-id", required=True)
    dispatch_finish.add_argument("--lease-id", required=True)
    dispatch_finish.add_argument("--owner-key", required=True)
    dispatch_finish.add_argument(
        "--status",
        choices=("succeeded", "failed", "execution_unknown"),
        required=True,
    )
    dispatch_finish.add_argument("--failure-class")
    dispatch_finish.add_argument("--worker-attempt-id")
    dispatch_finish.add_argument("--stdout-sha256")
    dispatch_finish.add_argument("--stderr-sha256")
    dispatch_finish.add_argument("--output-sha256")
    dispatch_finish.add_argument("--process-exit-code", type=int)
    dispatch_finish.add_argument("--timed-out", action="store_true")
    dispatch_finish.add_argument(
        "--result-receipt-sha256", required=True
    )
    dispatch_finish.add_argument(
        "--evidence-json", type=Path, required=True
    )
    dispatch_finish.add_argument("--completed-at")

    dispatch_close = dispatch_commands.add_parser(
        "close",
        help="close exact generation 2 with typed terminal evidence",
    )
    dispatch_close.add_argument(
        "--dsn", default=os.environ.get("NHI_RULE_HISTORY_DSN")
    )
    dispatch_close.add_argument("--dispatch-claim-id", required=True)
    dispatch_close.add_argument("--work-item-id", required=True)
    dispatch_close.add_argument(
        "--generation", type=int, required=True
    )
    dispatch_close.add_argument("--authorization-id", required=True)
    dispatch_close.add_argument("--admission-id", required=True)
    dispatch_close.add_argument(
        "--to-state",
        choices=(
            "staged_needs_review",
            "failed_terminal",
        ),
        required=True,
    )
    dispatch_close.add_argument("--evidence-contract", required=True)
    dispatch_close.add_argument("--evidence-sha256", required=True)
    dispatch_close.add_argument(
        "--evidence-json", type=Path, required=True
    )
    dispatch_close.add_argument("--terminal-receipt-id", required=True)
    dispatch_close.add_argument("--source-job-id")
    dispatch_close.add_argument("--bundle-receipt-id")
    dispatch_close.add_argument("--candidate-proposal-id")
    dispatch_close.add_argument("--closed-at")
    return parser


def _worker_spec(path: Path) -> WorkerSpec:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerFailure("worker spec is unreadable") from exc
    expected = {
        "worker_id",
        "runtime_id",
        "provider",
        "model",
        "command",
        "timeout_seconds",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkerFailure("worker spec fields are invalid")
    command = value["command"]
    if not isinstance(command, list) or any(
        not isinstance(item, str) or not item for item in command
    ):
        raise WorkerFailure("worker command must be a JSON argv array")
    return WorkerSpec(
        worker_id=value["worker_id"],
        runtime_id=value["runtime_id"],
        provider=value["provider"],
        model=value["model"],
        command=tuple(command),
        timeout_seconds=value["timeout_seconds"],
    )


def _json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateQueueError(f"{label} JSON is unreadable") from exc
    if not isinstance(value, dict) or not value:
        raise UpdateQueueError(f"{label} must be a non-empty JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan-validate":
            plan = SourcePlan.load(args.plan)
            result = {
                "status": "passed",
                "schema": plan.document["schema"],
                "capture_cut": plan.document["capture_cut"],
                "source_plan_sha256": plan.sha256,
                "adapter_count": len(plan.adapters),
            }
        elif args.command == "discover":
            result = discover_run(
                args.plan,
                args.run_dir,
                timeout_seconds=args.timeout_seconds,
                max_bytes=args.max_bytes,
                ca_file=args.ca_file,
                allow_insecure_tls=args.allow_insecure_tls,
            )
        elif args.command == "fetch":
            result = fetch_run(
                args.plan,
                args.run_dir,
                timeout_seconds=args.timeout_seconds,
                max_bytes=args.max_bytes,
                ca_file=args.ca_file,
                allow_insecure_tls=args.allow_insecure_tls,
                refresh_successes=args.refresh_successes,
            )
        elif args.command == "verify-raw":
            result = verify_raw(args.run_dir)
        elif args.command == "compare-discovery":
            result = compare_discovery_runs(args.pass_a, args.pass_b)
            if args.output is not None:
                write_json(args.output, result)
        elif args.command == "parse-odt":
            result = parse_verified_odt_run(
                args.run_dir,
                args.stage_dir,
                parse_run_id=args.parse_run_id,
            )
        elif args.command == "anchor-parity-preflight":
            result = current_anchor_occurrence_parity(
                args.stage_dir,
                output=args.output,
            )
        elif args.command == "anchor-clause-parity":
            result = analyze_current_anchor_clause_parity(args.stage_dir)
            if args.output is not None:
                write_json(args.output, result)
        elif args.command == "terminology-preview":
            if not args.dsn:
                raise TerminologyError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            result = preview_terminology(
                conninfo=args.dsn,
                alias_proposal_path=args.alias_proposal,
                publication_run_id=args.publication_run_id,
                seed_enrichment_run_id=args.seed_enrichment_run_id,
            )
        elif args.command == "terminology-load":
            if not args.dsn:
                raise TerminologyError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            result = load_terminology(
                conninfo=args.dsn,
                alias_proposal_path=args.alias_proposal,
                publication_run_id=args.publication_run_id,
                seed_enrichment_run_id=args.seed_enrichment_run_id,
                activate=not args.no_activate,
            )
        elif args.command == "terminology-verify":
            if not args.dsn:
                raise TerminologyError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            result = verify_terminology(
                args.tagging_run_id, conninfo=args.dsn
            )
        elif args.command == "load-acquisition":
            result = load_acquisition_run(args.run_dir, conninfo=args.dsn)
        elif args.command == "load-structural":
            result = load_structural_run(args.stage_dir, conninfo=args.dsn)
        elif args.command == "load-annotation-stage":
            if not args.dsn:
                raise AnnotationStageError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            try:
                records = [
                    json.loads(line)
                    for line in args.input_jsonl.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                raise AnnotationStageError(
                    "annotation-stage JSONL is unreadable"
                ) from exc
            if any(not isinstance(record, dict) for record in records):
                raise AnnotationStageError(
                    "annotation-stage JSONL rows must be objects"
                )
            result = load_annotation_stage(records, conninfo=args.dsn)
        elif args.command == "export":
            if not args.dsn:
                raise ExportError("--dsn or NHI_RULE_HISTORY_DSN is required")
            try:
                import psycopg
            except ImportError as exc:
                raise ExportError(
                    "psycopg is required for PostgreSQL export"
                ) from exc
            with psycopg.connect(args.dsn) as connection:
                result = export_stage_from_connection(
                    connection,
                    run_id=args.run_id,
                    fingerprint=args.fingerprint,
                    output_dir=args.output_dir,
                    schema_path=args.schema,
                )
        elif args.command == "verify-export":
            result = verify_export_directory(
                args.input_dir,
                run_id=args.run_id,
                fingerprint=args.fingerprint,
            )
        elif args.command == "release":
            result = prepare_release(
                export_dir=args.export_dir,
                output_dir=args.output_dir,
            )
        elif args.command == "release-v2":
            result = prepare_v2_evidence_release(
                run_dir=args.run_dir,
                stage_dir=args.stage_dir,
                source_plan=args.source_plan,
                eligibility_receipt=args.eligibility_receipt,
                output_dir=args.output_dir,
            )
        elif args.command == "historical-bundles":
            batch = materialize_historical_notice_bundles(
                args.run_dir,
                source_plan=args.source_plan,
                output_root=args.output_root,
            )
            result = {
                "status": "passed",
                "batch_id": batch.batch_id,
                "batch_fingerprint": batch.batch_fingerprint,
                "index_path": str(batch.index_path),
                "document_count": batch.document_count,
                "attachment_count": batch.attachment_count,
                "replayed": batch.replayed,
            }
        elif args.command == "verify-historical-bundles":
            result = verify_historical_bundle_batch(args.output_root)
        elif args.command in {"update-poll", "update-acquire"}:
            client = OfficialNhiClient(
                timeout_seconds=args.timeout_seconds,
                max_bytes=args.max_bytes,
                ca_file=args.ca_file,
                allow_insecure_tls=args.allow_insecure_tls,
            )
            feed_response = client.get_feed()
            if args.command == "update-poll":
                try:
                    observed_guids = json.loads(
                        args.observed_guids_json.read_text(encoding="utf-8")
                    )
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as exc:
                    raise ContractError(
                        "observed GUID state is unreadable"
                    ) from exc
                if not isinstance(observed_guids, list) or any(
                    not isinstance(value, str) for value in observed_guids
                ):
                    raise ContractError(
                        "observed GUID state must be a JSON string array"
                    )
                poll = observe_feed(
                    args.poll_root,
                    response=feed_response,
                    observed_guids=observed_guids,
                    previous_item_count=args.previous_item_count,
                )
                result = {
                    "status": "passed",
                    "poll_id": poll.poll_id,
                    "poll_path": str(poll.path),
                    "item_count": len(poll.items),
                    "new_item_count": len(poll.new_items),
                    "new_items": [
                        item.as_dict() for item in poll.new_items
                    ],
                    "replayed": poll.replayed,
                }
                print(
                    json.dumps(
                        result, ensure_ascii=False, sort_keys=True, indent=2
                    )
                )
                return 0
            items = parse_rss(feed_response.body)
            selected = [item for item in items if item.link == args.detail_url]
            if len(selected) != 1:
                raise ContractError(
                    "detail URL must identify exactly one current RSS item"
                )
            sealed = acquire_notice_bundle(
                args.bundle_root,
                client=client,
                item=selected[0],
                feed_response=feed_response,
            )
            result = {
                "status": "passed",
                "bundle_id": sealed.bundle_id,
                "bundle_fingerprint": sealed.bundle_fingerprint,
                "bundle_path": str(sealed.path),
                "replayed": sealed.replayed,
                "resource_count": len(sealed.manifest["resources"]),
                "attachment_count": sealed.manifest[
                    "declared_attachment_count"
                ],
            }
        elif args.command == "update-propose":
            orchestrator = WorkerOrchestrator(
                primary=_worker_spec(args.primary_spec),
                fallback=_worker_spec(args.fallback_spec),
            )
            result = orchestrator.run(
                bundle_path=args.bundle_path,
                candidate_root=args.candidate_root,
            )
        elif args.command == "update-corpus":
            result = prepare_corpus_bundle(
                args.bundle_path,
                corpus_root=args.corpus_root,
            )
        elif args.command == "update-stage":
            if not args.dsn:
                raise UpdateStageLoadError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            result = load_update_candidate(
                args.dsn,
                bundle_path=args.bundle_path,
                candidate_receipt_path=args.candidate_receipt,
                bundle_relative_path=args.bundle_relative_path,
                activation_cut=args.activation_cut,
                owner_key=args.owner_key,
                notification_window_start=args.notification_window_start,
                notification_window_end=args.notification_window_end,
            )
        elif args.command == "update-poll-stage":
            if not args.dsn:
                raise UpdateQueueError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            result = load_poll_package(
                args.dsn,
                args.poll_path,
                owner_key=args.owner_key,
                poll_relative_root=args.poll_relative_root,
            )
        elif args.command == "partition-recovery":
            lane = args.partition_recovery_command
            if lane == "verify":
                if not args.dsn:
                    raise UpdateQueueError(
                        "--dsn or NHI_RULE_HISTORY_DSN is required"
                    )
                verified = load_partition_recovery_evidence(
                    args.evidence_json
                )
                result = verify_partition_recovery_admission(
                    args.dsn,
                    evidence=verified["payload"],
                )
            else:
                if not args.dsn:
                    raise UpdateQueueError(
                        "--dsn or NHI_RULE_HISTORY_DSN is required"
                    )
                if lane == "admit":
                    verified = load_partition_recovery_evidence(
                        args.evidence_json
                    )
                    result = admit_partition_recovery(
                        args.dsn,
                        evidence=verified["payload"],
                        actor_kind=args.actor_kind,
                        admitted_at=args.admitted_at,
                    )
                elif lane == "authorize":
                    result = authorize_partition_recovery(
                        args.dsn,
                        admission_id=args.admission_id,
                        work_item_id=args.work_item_id,
                        generation=args.generation,
                        admission_payload_sha256=(
                            args.admission_payload_sha256
                        ),
                        expires_at=args.expires_at,
                        actor_kind=args.actor_kind,
                        authorized_at=args.authorized_at,
                    )
                elif lane == "show":
                    result = show_partition_recovery(
                        args.dsn,
                        admission_id=args.admission_id,
                        authorization_id=args.authorization_id,
                    )
                else:
                    result = revoke_partition_recovery(
                        args.dsn,
                        authorization_id=args.authorization_id,
                        reason=args.reason,
                        actor_kind=args.actor_kind,
                        revoked_at=args.revoked_at,
                    )
        elif args.command == "dispatch-v2":
            if not args.dsn:
                raise UpdateQueueError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            lane = args.dispatch_v2_command
            if lane == "consume":
                result = consume_partition_recovery_dispatch(
                    args.dsn,
                    work_item_id=args.work_item_id,
                    generation=args.generation,
                    authorization_id=args.authorization_id,
                    admission_id=args.admission_id,
                    admission_payload_sha256=(
                        args.admission_payload_sha256
                    ),
                    sealed_packet_manifest_sha256=(
                        args.sealed_packet_manifest_sha256
                    ),
                    suitability_v2_receipt_sha256=(
                        args.suitability_v2_receipt_sha256
                    ),
                    job_fingerprint=args.job_fingerprint,
                    prompt_sha256=args.prompt_sha256,
                    route_policy_sha256=args.route_policy_sha256,
                    owner_key=args.owner_key,
                    max_runtime_seconds=args.max_runtime_seconds,
                    consumed_at=args.consumed_at,
                    dispatch_contract_version=(
                        args.dispatch_contract_version
                    ),
                )
            elif lane == "reserve-route":
                result = reserve_partition_recovery_route(
                    args.dsn,
                    dispatch_claim_id=args.dispatch_claim_id,
                    work_item_id=args.work_item_id,
                    generation=args.generation,
                    authorization_id=args.authorization_id,
                    admission_id=args.admission_id,
                    route_ordinal=args.route_ordinal,
                    packet_sha256=args.packet_sha256,
                    prompt_sha256=args.prompt_sha256,
                    recovery_job_id=args.recovery_job_id,
                    lease_id=args.lease_id,
                    owner_key=args.owner_key,
                    runtime_id=args.runtime_id,
                    provider=args.provider,
                    model=args.model,
                    controller_commit_sha256=(
                        args.controller_commit_sha256
                    ),
                    job_fingerprint=args.job_fingerprint,
                    reserved_at=args.reserved_at,
                )
            elif lane == "finish-route":
                result = finish_partition_recovery_route(
                    args.dsn,
                    reservation_id=args.reservation_id,
                    dispatch_claim_id=args.dispatch_claim_id,
                    work_item_id=args.work_item_id,
                    generation=args.generation,
                    authorization_id=args.authorization_id,
                    admission_id=args.admission_id,
                    route_ordinal=args.route_ordinal,
                    attempt_namespace=args.attempt_namespace,
                    job_fingerprint=args.job_fingerprint,
                    recovery_job_id=args.recovery_job_id,
                    lease_id=args.lease_id,
                    owner_key=args.owner_key,
                    status=args.status,
                    failure_class=args.failure_class,
                    worker_attempt_id=args.worker_attempt_id,
                    stdout_sha256=args.stdout_sha256,
                    stderr_sha256=args.stderr_sha256,
                    output_sha256=args.output_sha256,
                    process_exit_code=args.process_exit_code,
                    timed_out=args.timed_out,
                    result_receipt_sha256=(
                        args.result_receipt_sha256
                    ),
                    evidence=_json_object(
                        args.evidence_json, "route result evidence"
                    ),
                    completed_at=args.completed_at,
                )
            else:
                result = close_partition_recovery_generation(
                    args.dsn,
                    dispatch_claim_id=args.dispatch_claim_id,
                    work_item_id=args.work_item_id,
                    generation=args.generation,
                    authorization_id=args.authorization_id,
                    admission_id=args.admission_id,
                    to_state=args.to_state,
                    evidence_contract=args.evidence_contract,
                    evidence_sha256=args.evidence_sha256,
                    evidence=_json_object(
                        args.evidence_json, "terminal evidence"
                    ),
                    terminal_receipt_id=args.terminal_receipt_id,
                    source_job_id=args.source_job_id,
                    bundle_receipt_id=args.bundle_receipt_id,
                    candidate_proposal_id=args.candidate_proposal_id,
                    closed_at=args.closed_at,
                )
        else:
            if not args.dsn:
                raise UpdateQueueError(
                    "--dsn or NHI_RULE_HISTORY_DSN is required"
                )
            try:
                evidence = json.loads(
                    args.evidence_json.read_text(encoding="utf-8")
                )
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                raise UpdateQueueError(
                    "transition evidence JSON is unreadable"
                ) from exc
            if not isinstance(evidence, dict) or not evidence:
                raise UpdateQueueError(
                    "transition evidence must be a non-empty JSON object"
                )
            result = append_work_transition(
                args.dsn,
                work_item_id=args.work_item_id,
                to_state=args.to_state,
                actor_kind=args.actor_kind,
                evidence=evidence,
                source_job_id=args.source_job_id,
                bundle_receipt_id=args.bundle_receipt_id,
                candidate_proposal_id=args.candidate_proposal_id,
                recorded_at=args.recorded_at,
            )
    except (
        ContractError,
        CanonicalError,
        ExportError,
        PrepareError,
        AcquisitionLoadError,
        StructuralLoadError,
        AnnotationStageError,
        TerminologyError,
        UpdateStageLoadError,
        UpdateQueueError,
        ProposalError,
        WorkerFailure,
    ) as exc:
        parser = build_parser()
        parser.error(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
