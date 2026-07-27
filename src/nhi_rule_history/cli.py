from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from nhi_rule_history.contracts import ContractError, SourcePlan, write_json
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
    return parser


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
        elif args.command == "load-acquisition":
            result = load_acquisition_run(args.run_dir, conninfo=args.dsn)
        elif args.command == "load-structural":
            result = load_structural_run(args.stage_dir, conninfo=args.dsn)
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
        else:
            result = prepare_v2_evidence_release(
                run_dir=args.run_dir,
                stage_dir=args.stage_dir,
                source_plan=args.source_plan,
                eligibility_receipt=args.eligibility_receipt,
                output_dir=args.output_dir,
            )
    except (
        ContractError,
        CanonicalError,
        ExportError,
        PrepareError,
        AcquisitionLoadError,
        StructuralLoadError,
    ) as exc:
        parser = build_parser()
        parser.error(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
