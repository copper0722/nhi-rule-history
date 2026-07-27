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
    UpdateQueueError,
    append_work_transition,
    load_poll_package,
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
