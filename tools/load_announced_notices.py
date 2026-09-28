#!/usr/bin/env python3
"""Deterministic announced-notice overlay: verify, load, activate, roll back.

verify    parse notices and print the per-clause verification table (no writes)
compose   verify, then compose the would-be release run in memory (no writes)
load      compose and seal the release run and its re-bound projections;
          nothing served changes until ``activate``
activate  make a loaded run the served run (precondition-checked)
rollback  re-activate the chain recorded when a run was activated

Notices come from ``--notice`` bundle paths (relative to ``--corpus-root``)
or from the update queue: ``--queue-state`` selects work items by their state
now, ``--queue-registered`` every item that was ever registered.  A queued
bundle's manifest must be proven to be the registered one.  No command calls
a model.

compose, load and activate print one JSON receipt on stdout; errors go to
stderr.  Every receipt lists ``failures`` (notices that failed to parse, bind,
render or supersede, with the reason), ``dropped_notices`` (parsed notices
left out, with the reason) and ``blocked_clauses`` (clauses the run holds
back, with the reason).  Its ``status`` is ``passed`` or ``no_change`` only
when all three are empty, and ``passed_with_holds`` or
``no_change_with_holds`` otherwise.  Exit status: 0 green, 3 completed with
holds (a load did seal its run and an activation did serve it), 1 error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from nhi_rule_history.announced_notice import (
    AnnouncedNoticeError,
    libreoffice_text_export,
    parse_notice,
    read_notice_bundle,
    verification_row,
)
from nhi_rule_history.announced_release import (
    STATUS_NO_CHANGE,
    STATUS_PASSED,
    AnnouncedReleaseError,
    Composition,
    _connect,
    activate_overlay_release,
    compose_overlay_release,
    load_overlay_release,
    queued_bundles,
    receipt_status,
    registered_bundles,
    rollback_overlay_release,
    served_clauses,
)
from nhi_rule_history.pg.common import PgLoadError


CORPUS_ROOT_ENV = "NHI_RULE_HISTORY_CORPUS_ROOT"
DYSLIPIDEMIA_BUNDLE = "2026/gov_健保審字第1150671962號"
DYSLIPIDEMIA_ODT = "attachment-003.odt"
EXIT_HOLDS = 3


def _bundles(
    args: argparse.Namespace,
) -> tuple[list[tuple[Path, str | None]], list[dict[str, Any]]]:
    """Bundle paths, each with its registered manifest digest if queued."""

    paths: list[tuple[Path, str | None]] = [
        (args.corpus_root / item, None) for item in args.notice or ()
    ]
    failures: list[dict[str, Any]] = []
    selected = []
    if args.queue_state:
        selected.extend(
            queued_bundles(
                args.dsn, corpus_root=args.corpus_root, state=args.queue_state
            )
        )
    if args.queue_registered:
        selected.extend(
            registered_bundles(args.dsn, corpus_root=args.corpus_root)
        )
    for item in selected:
        if item.problem:
            failures.append(
                {"bundle": item.bundle_dir.name, "error": item.problem}
            )
        else:
            paths.append((item.bundle_dir, item.corpus_manifest_sha256))
    unique: dict[Path, str | None] = {}
    for path, registered in paths:
        if unique.get(path) is None:
            unique[path] = registered
    return list(unique.items()), failures


def _parse(
    args: argparse.Namespace,
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parsed notices, failures, and notices left out by ``--effective-on``."""

    parsed = []
    paths, failures = _bundles(args)
    for failure in failures:
        failure.setdefault("stage", "queue")
    dropped: list[dict[str, Any]] = []
    for path, registered in paths:
        try:
            notice = parse_notice(
                read_notice_bundle(path, registered_manifest_sha256=registered)
            )
        except AnnouncedNoticeError as exc:
            failures.append(
                {"bundle": path.name, "stage": "parse", "error": str(exc)}
            )
            continue
        if args.effective_on and notice.effective_on not in args.effective_on:
            dropped.append(
                {
                    "reference_number": notice.bundle.reference_number,
                    "bundle": path.name,
                    "effective_on": notice.effective_on,
                    "reason": "effective_on_not_selected",
                }
            )
            continue
        parsed.append(notice)
    return parsed, failures, dropped


def _carried_references(dsn: str) -> set[str]:
    with _connect(dsn, read_only=True) as connection:
        return {
            row["reference_number"]
            for row in connection.execute(
                """
                SELECT notice.reference_number
                FROM nhi_rule_history_announced.notice_event notice
                JOIN nhi_rule_history_announced.v_active_run run USING (run_id)
                """
            ).fetchall()
        }


def _verification(args: argparse.Namespace) -> dict[str, Any]:
    notices, failures, dropped = _parse(args)
    carried = _carried_references(args.dsn)
    rows = []
    with _connect(args.dsn, read_only=True) as connection:
        codes = [c.clause_code for n in notices for c in n.clauses]
        served_run_id, served = served_clauses(connection, codes)
    for notice in notices:
        rendering = (
            None
            if args.no_libreoffice
            else libreoffice_text_export(notice.attachment.path.read_bytes())
        )
        for clause in notice.clauses:
            current = served.get(clause.clause_code)
            row = verification_row(
                notice,
                clause,
                served_text=None if current is None else current["raw_text"],
                rendering=rendering,
            )
            row["already_in_active_run"] = (
                notice.bundle.reference_number in carried
            )
            rows.append(row)
    return {
        "served_publication_run_id": served_run_id,
        "notices": [
            {
                "reference_number": n.bundle.reference_number,
                "source_uid": n.bundle.source_uid,
                "effective_on": n.effective_on,
                "source_artifact": n.attachment.file_name,
                "source_artifact_sha256": n.attachment.sha256,
                "clauses": [c.clause_code for c in n.clauses],
                "pending_effects": [e.key for e in n.other_effects],
                "already_in_active_run": n.bundle.reference_number in carried,
            }
            for n in notices
        ],
        "clauses": rows,
        "failures": failures,
        "dropped_notices": dropped,
    }


def _print_table(report: dict[str, Any]) -> None:
    header = (
        f"{'reference':24s} {'clause':8s} {'effective':10s} "
        f"{'revised=official':27s} {'ws':>2s} {'lbl':>3s} {'omit':4s} "
        f"{'old=served':10s} {'old~served':10s} {'old-found':9s} note"
    )
    print(header)
    print("-" * len(header))
    for row in report["clauses"]:
        found = row["old_paragraphs_found_in_served"]
        note = []
        if row["blocked_reason"]:
            note.append("BLOCKED " + row["blocked_reason"])
        if row["original_is_none"]:
            note.append("new clause")
        if not row["served_clause_present"]:
            note.append("not served")
        if row["already_in_active_run"]:
            note.append("already active")
        print(
            f"{row['reference_number']:24s} {row['clause_code']:8s} "
            f"{row['effective_on']:10s} "
            f"{row['revised_equals_official_rendering']:27s} "
            f"{row['revised_blocks_with_whitespace_elements']:>2d} "
            f"{row['revised_blocks_with_generated_labels']:>3d} "
            f"{'yes' if row['omitted_text_present'] else 'no':4s} "
            f"{str(row['old_equals_served_exact']):10s} "
            f"{str(row['old_equals_served_semantic']):10s} "
            f"{('-' if found is None else f'{found[0]}/{found[1]}'):9s} "
            f"{', '.join(note)}"
        )
    for failure in report["failures"]:
        print(f"FAIL {failure['bundle']}: {failure['error']}")


def _odt(args: argparse.Namespace) -> Path | None:
    if args.dyslipidemia_odt:
        return args.dyslipidemia_odt
    candidate = args.corpus_root / DYSLIPIDEMIA_BUNDLE / DYSLIPIDEMIA_ODT
    return candidate if candidate.is_file() else None


def _compose(
    args: argparse.Namespace,
) -> tuple[Composition, list[dict[str, Any]], list[dict[str, Any]]]:
    notices, failures, dropped = _parse(args)
    renderings = {
        notice.bundle.reference_number: libreoffice_text_export(
            notice.attachment.path.read_bytes()
        )
        for notice in notices
    }
    composition = compose_overlay_release(
        args.dsn,
        notices,
        base_run_id=args.base_run_id,
        dyslipidemia_odt=_odt(args),
        official_renderings=renderings,
        require_official_rendering=not args.allow_without_rendering_check,
        supersede=args.supersede_carried,
    )
    return composition, failures, dropped


def _holds(
    composition: Composition,
    failures: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
) -> dict[str, Any]:
    """Receipt head: status plus everything the batch did not serve."""

    failures = [*failures, *composition.failures]
    dropped = [*dropped, *composition.dropped_notices]
    return {
        "status": receipt_status(
            composed=composition.release is not None,
            failures=failures,
            dropped_notices=dropped,
            blocked_clauses=composition.blocked_clauses,
        ),
        "failures": failures,
        "dropped_notices": dropped,
        "blocked_clauses": list(composition.blocked_clauses),
        "superseded_notices": list(composition.superseded_notices),
        "carried_notices": list(composition.carried_notices),
    }


def _release_summary(composition: Composition) -> dict[str, Any]:
    release = composition.release
    if release is None:
        return {
            "run_id": None,
            "base_run_id": composition.base_run_id,
            "base_sealed_fingerprint": composition.base_sealed_fingerprint,
        }
    return {
        "run_id": release.run_id,
        "base_run_id": str(release.base.run["run_id"]),
        "base_sealed_fingerprint": release.base.run["sealed_fingerprint"],
        "input_fingerprint": release.input_fingerprint,
        "output_fingerprint": release.output_fingerprint,
        "sealed_fingerprint": release.sealed_fingerprint,
        "source_set_sha256": release.source_set_sha256,
        "expected_counts": dict(release.expected_counts),
        "all_counts": dict(release.all_counts),
        "notices": [
            {
                "reference_number": item.notice.bundle.reference_number,
                "effective_on": item.notice.effective_on,
                "projected_clauses": sorted(
                    row["clause_code"] for row in item.rows["clause_patch"]
                ),
                "pending_effects": item.rows["notice_event"][0][
                    "unresolved_scope"
                ],
            }
            for item in release.notices
        ],
        "normalization_run_id": release.normalization_run_id,
        "diff_run_id": release.diff_run_id,
        "reader_profile_run_id": (
            release.profile.profile_run_id if release.profile else None
        ),
        "rebinding_positive_control": release.rebinding_control.get(
            "positive_control"
        ),
    }


def _load_receipt_holds(
    path: Path, *, run_id: str, sealed_fingerprint: str
) -> dict[str, Any]:
    """Compose-time holds from the load receipt that sealed ``run_id``."""

    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnnouncedReleaseError(
            f"cannot read the load receipt: {exc}"
        ) from exc
    load = receipt.get("load") if isinstance(receipt, dict) else None
    if (
        not isinstance(load, dict)
        or load.get("run_id") != run_id
        or load.get("sealed_fingerprint") != sealed_fingerprint
    ):
        raise AnnouncedReleaseError(
            "the load receipt does not name the run being activated"
        )
    holds = {
        "load_status": receipt.get("status"),
        "failures": receipt.get("failures"),
        "dropped_notices": receipt.get("dropped_notices"),
        "superseded_notices": receipt.get("superseded_notices"),
    }
    if not all(
        isinstance(holds[key], list)
        for key in ("failures", "dropped_notices", "superseded_notices")
    ):
        raise AnnouncedReleaseError("the load receipt lists no holds")
    return holds


def _print(receipt: dict[str, Any]) -> None:
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))


def _exit_status(receipt: dict[str, Any]) -> int:
    green = receipt["status"] in {STATUS_PASSED, STATUS_NO_CHANGE}
    return 0 if green else EXIT_HOLDS


def _run(args: argparse.Namespace) -> int:
    if args.command == "verify":
        report = _verification(args)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_table(report)
        return 1 if report["failures"] else 0
    if args.command in {"compose", "load"}:
        composition, failures, dropped = _compose(args)
        receipt = _holds(composition, failures, dropped)
        if receipt["failures"] and not args.skip_failed:
            print(
                "error: notice failures (use --skip-failed to leave them "
                "out): " + json.dumps(receipt["failures"], ensure_ascii=False),
                file=sys.stderr,
            )
            return 1
        if args.command == "compose":
            receipt.update(_release_summary(composition))
        else:
            receipt["release"] = _release_summary(composition)
            receipt["load"] = (
                load_overlay_release(args.dsn, composition.release)
                if composition.release is not None
                else None
            )
        _print(receipt)
        return _exit_status(receipt)
    if args.command == "activate":
        holds = (
            _load_receipt_holds(
                args.load_receipt,
                run_id=args.run_id,
                sealed_fingerprint=args.expect_sealed_fingerprint,
            )
            if args.load_receipt
            else None
        )
        result = activate_overlay_release(
            args.dsn,
            run_id=args.run_id,
            expected_sealed_fingerprint=args.expect_sealed_fingerprint,
            expected_base_run_id=args.expect_base_run_id,
            compose_holds=holds,
        )
        # Without --load-receipt the compose-time holds are unknown (null),
        # not empty; the held-back clauses are read from the served run.
        receipt = {
            "status": receipt_status(
                composed=True,
                failures=holds and holds["failures"],
                dropped_notices=holds and holds["dropped_notices"],
                blocked_clauses=result["blocked_clauses"],
            ),
            "failures": None if holds is None else holds["failures"],
            "dropped_notices": (
                None if holds is None else holds["dropped_notices"]
            ),
            "blocked_clauses": result["blocked_clauses"],
            "superseded_notices": (
                None if holds is None else holds["superseded_notices"]
            ),
            "load_status": None if holds is None else holds["load_status"],
            **{k: v for k, v in result.items() if k != "blocked_clauses"},
        }
        _print(receipt)
        return _exit_status(receipt)
    if args.command == "rollback":
        result = rollback_overlay_release(args.dsn, from_run_id=args.from_run_id)
        _print(result)
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "compose", "load"):
        command = sub.add_parser(name)
        command.add_argument("--dsn", required=True)
        command.add_argument(
            "--corpus-root",
            type=Path,
            default=(
                Path(os.environ[CORPUS_ROOT_ENV])
                if os.environ.get(CORPUS_ROOT_ENV)
                else None
            ),
            help=f"tw-gov NHI bundle root; defaults to ${CORPUS_ROOT_ENV}",
        )
        command.add_argument("--notice", action="append")
        command.add_argument("--queue-state")
        command.add_argument(
            "--queue-registered",
            action="store_true",
            help=(
                "every bundle the update queue ever registered, whatever the "
                "work item's state now"
            ),
        )
        command.add_argument("--effective-on", action="append")
        command.add_argument("--json", action="store_true")
        if name == "verify":
            command.add_argument("--no-libreoffice", action="store_true")
        else:
            command.add_argument("--base-run-id")
            command.add_argument("--dyslipidemia-odt", type=Path)
            command.add_argument("--skip-failed", action="store_true")
            command.add_argument(
                "--allow-without-rendering-check", action="store_true"
            )
            command.add_argument(
                "--supersede-carried",
                action="store_true",
                help=(
                    "replace a carried notice's rows by its fresh projection "
                    "when it projects clauses the served run holds back and "
                    "every clause patch it serves is re-projected "
                    "byte-identically"
                ),
            )
    activate = sub.add_parser("activate")
    activate.add_argument("--dsn", required=True)
    activate.add_argument("--run-id", required=True)
    activate.add_argument("--expect-sealed-fingerprint", required=True)
    activate.add_argument("--expect-base-run-id", required=True)
    activate.add_argument(
        "--load-receipt",
        type=Path,
        help=(
            "JSON receipt of the load that sealed --run-id; its failures and "
            "dropped notices are carried into the activation receipt and "
            "evidence"
        ),
    )
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--dsn", required=True)
    rollback.add_argument("--from-run-id", required=True)
    args = parser.parse_args(argv)
    if args.command in {"verify", "compose", "load"} and args.corpus_root is None:
        parser.error(f"--corpus-root or ${CORPUS_ROOT_ENV} is required")
    try:
        return _run(args)
    except PgLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
