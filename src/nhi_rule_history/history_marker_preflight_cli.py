"""Public file-based entry point for the history-marker ODT preflight.

The analytical core deliberately accepts already sealed snapshots instead of
opening PostgreSQL itself.  This wrapper makes the exact inputs and outputs
explicit so another operator can replay the same check without database write
authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from nhi_rule_history.contracts import ContractError, write_json
from nhi_rule_history.history_marker_preflight import (
    HistoryMarkerPreflightError,
    analyze_history_marker_preflight,
    compact_public_report,
    write_evidence_ledger,
)


class HistoryMarkerPreflightCliError(RuntimeError):
    """An unreadable or structurally invalid file-based replay input."""


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryMarkerPreflightCliError(
            f"{label} is unreadable or invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise HistoryMarkerPreflightCliError(f"{label} must be a JSON object")
    return value


def _read_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise HistoryMarkerPreflightCliError(
            f"{label} is unreadable"
        ) from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoryMarkerPreflightCliError(
                f"{label}:{line_number} is invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise HistoryMarkerPreflightCliError(
                f"{label}:{line_number} must be a JSON object"
            )
        rows.append(value)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nhi-rule-history-marker-preflight",
        description=(
            "Replay the candidate-only marker-to-historical-ODT coverage "
            "check from explicit sealed snapshot files; never write PG or "
            "canonical history"
        ),
    )
    parser.add_argument("--annotation-run-json", type=Path, required=True)
    parser.add_argument("--annotation-articles-jsonl", type=Path, required=True)
    parser.add_argument(
        "--annotation-markers-jsonl", type=Path, required=True
    )
    parser.add_argument("--annotation-receipt", type=Path, required=True)
    parser.add_argument("--historical-receipt", type=Path, required=True)
    parser.add_argument("--historical-raw-dir", type=Path, required=True)
    parser.add_argument("--historical-structural-dir", type=Path, required=True)
    parser.add_argument("--evidence-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def run_from_files(args: argparse.Namespace) -> dict[str, Any]:
    result = analyze_history_marker_preflight(
        annotation_run=_read_json_object(
            args.annotation_run_json,
            label="annotation run",
        ),
        articles=_read_jsonl_objects(
            args.annotation_articles_jsonl,
            label="annotation articles",
        ),
        annotations=_read_jsonl_objects(
            args.annotation_markers_jsonl,
            label="annotation markers",
        ),
        annotation_receipt=args.annotation_receipt,
        historical_receipt=args.historical_receipt,
        raw_dir=args.historical_raw_dir,
        structural_dir=args.historical_structural_dir,
    )
    ledger = write_evidence_ledger(result, args.evidence_ledger)
    report = compact_public_report(result, evidence_ledger=ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_from_files(args)
    except (
        ContractError,
        HistoryMarkerPreflightError,
        HistoryMarkerPreflightCliError,
    ) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
