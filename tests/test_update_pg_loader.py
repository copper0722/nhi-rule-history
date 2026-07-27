from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from nhi_rule_history.contracts import (
    WORKER_ATTEMPT_SCHEMA,
    append_jsonl,
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    stable_id,
    write_json,
)
from nhi_rule_history.update.bundle import BundleBuilder
from nhi_rule_history.update.pg_stage import (
    UpdateStageLoadError,
    _insert_new_material,
    _prepare_update_load,
    load_update_candidate,
)
from nhi_rule_history.update.proposal import validate_proposal
from nhi_rule_history.update.rss import (
    OfficialResponse,
    parse_attachment_links,
    parse_rss,
)
from nhi_rule_history.update.workers import (
    WORKER_PROMPT_VERSION,
    WORKER_RUN_SCHEMA,
    build_worker_prompt,
    source_packet,
    worker_job_fingerprint,
)
from tests.test_continuous_update import (
    fixture_feed,
    fixture_odt,
    valid_proposal,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
OPS_ROLLBACK = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.rollback.sql"
)
OPS_OBSERVATION_LEASE_FIX = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.sql"
)
OPS_OBSERVATION_LEASE_FIX_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.rollback.sql"
)
CANDIDATE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.sql"
)
CANDIDATE_ROLLBACK = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.rollback.sql"
)


def response(url: str, body: bytes, content_type: str) -> OfficialResponse:
    return OfficialResponse(
        request_url=url,
        final_url=url,
        status_code=200,
        headers={"content-type": content_type},
        body=body,
        observed_at="2026-07-27T00:00:00+00:00",
    )


def build_bundle(root: Path) -> Path:
    item = parse_rss(fixture_feed())[0]
    detail = response(
        item.link,
        (
            "<html><div>主旨</div><div>修訂藥品給付規定</div>"
            "<div>發文字號</div><div>健保審字第1150000000號</div>"
            "<div>發文日期</div><div>115-07-20</div>"
            "<div>發布日期</div><div>115-07-20</div>"
            "<div>更新日期</div><div>115-07-20</div>"
            "<div>公告事項</div><div>修訂對照表如附件。</div>"
            '<a href="/ch/dl-test-1.odt">修訂對照表.ODT</a>'
            '<a href="/ch/dl-test-2.pdf">修訂對照表.PDF</a></html>'
        ).encode(),
        "text/html; charset=utf-8",
    )
    links = parse_attachment_links(item.link, detail.body)
    bundle_path = BundleBuilder(
        root,
        rss_item=item,
        feed_response=response(
            "https://www.nhi.gov.tw/ch/rss-3258-1.xml",
            fixture_feed(),
            "application/rss+xml",
        ),
        detail_response=detail,
        attachments=[
            (
                links[0],
                response(
                    links[0].url,
                    fixture_odt(),
                    "application/vnd.oasis.opendocument.text",
                ),
            ),
            (
                links[1],
                response(
                    links[1].url,
                    b"%PDF-1.7\nfixture",
                    "application/pdf",
                ),
            ),
        ],
    ).seal().path
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sealed_at"] = "2026-07-27T00:00:30+00:00"
    write_json(manifest_path, manifest)
    return bundle_path


def rewrite_bundle_times(
    bundle_path: Path, *, observed_at: str, sealed_at: str
) -> None:
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for resource in manifest["resources"]:
        resource["observed_at"] = observed_at
    manifest["sealed_at"] = sealed_at
    write_json(manifest_path, manifest)


def build_candidate_receipt(bundle_path: Path, candidate_root: Path) -> Path:
    packet = source_packet(bundle_path)
    proposal = valid_proposal(
        packet["source_blocks"],
        parity_unverified=True,
        identity_uncertainty=True,
    )
    proposal["notice"] = {
        "reference_number_raw": packet["notice_metadata"][
            "reference_number_raw"
        ],
        "subject_raw": packet["notice_metadata"]["subject_raw"],
    }
    candidate = validate_proposal(
        proposal,
        source_blocks=packet["source_blocks"],
        bundle_id=packet["bundle_id"],
        bundle_fingerprint=packet["bundle_fingerprint"],
        required_true_document_flags={"odt_pdf_parity_unverified"},
        expected_notice=proposal["notice"],
    )
    prompt_sha = sha256_bytes(
        build_worker_prompt(packet).encode("utf-8")
    )
    job_fingerprint = worker_job_fingerprint(
        manifest_sha256=packet["manifest_sha256"],
        prompt_sha256=prompt_sha,
    )
    run_dir = candidate_root / job_fingerprint
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.json").write_bytes(
        build_worker_prompt(packet).encode("utf-8")
    )
    primary_id = stable_id("fixture-attempt", job_fingerprint, "primary")
    fallback_id = stable_id("fixture-attempt", job_fingerprint, "fallback")
    bad_output = "not-json"
    output = json.dumps(
        proposal,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    common = {
        "schema": WORKER_ATTEMPT_SCHEMA,
        "worker_id": "fixture-worker",
        "runtime_id": "fixture-runtime",
        "provider": "fixture-provider",
        "model": "fixture-model",
        "prompt_version": WORKER_PROMPT_VERSION,
        "prompt_sha256": prompt_sha,
        "stderr_sha256": sha256_bytes(b""),
    }
    primary = {
        **common,
        "attempt_id": primary_id,
        "role": "primary",
        "started_at": "2026-07-27T00:01:00+00:00",
        "completed_at": "2026-07-27T00:01:30+00:00",
        "status": "contract_failed",
        "primary_attempt_id": None,
        "fallback_reason": None,
        "exit_code": 0,
        "output_sha256": sha256_bytes(bad_output.encode()),
        "validation_error_code": "ProposalError",
    }
    fallback = {
        **common,
        "attempt_id": fallback_id,
        "role": "fallback",
        "started_at": "2026-07-27T00:01:31+00:00",
        "completed_at": "2026-07-27T00:02:00+00:00",
        "status": "validated",
        "primary_attempt_id": primary_id,
        "fallback_reason": "contract_failed",
        "exit_code": 0,
        "output_sha256": sha256_bytes(output.encode()),
        "validation_error_code": None,
        "candidate_id": candidate["candidate_id"],
    }
    append_jsonl(run_dir / "attempts.jsonl", primary)
    append_jsonl(run_dir / "attempts.jsonl", fallback)
    (run_dir / "primary-stdout.bin").write_text(
        bad_output, encoding="utf-8"
    )
    (run_dir / "primary-stderr.bin").write_bytes(b"")
    (run_dir / "fallback-stdout.bin").write_text(
        output, encoding="utf-8"
    )
    (run_dir / "fallback-stderr.bin").write_bytes(b"")
    (run_dir / "fallback-output.json").write_text(output, encoding="utf-8")
    receipt = {
        "schema": WORKER_RUN_SCHEMA,
        "job_fingerprint": job_fingerprint,
        "bundle_id": packet["bundle_id"],
        "bundle_fingerprint": packet["bundle_fingerprint"],
        "manifest_sha256": packet["manifest_sha256"],
        "attempts_sha256": file_sha256(run_dir / "attempts.jsonl"),
        "prompt_sha256": prompt_sha,
        "status": "staged",
        "attempt_count": 2,
        "selected_attempt_id": fallback_id,
        "selected_role": "fallback",
        "candidate": candidate,
        "replayed": False,
    }
    receipt_path = run_dir / "candidate-receipt.json"
    write_json(receipt_path, receipt)
    return receipt_path


def prepare_fixture(root: Path):
    bundle_path = build_bundle(root / "bundles")
    receipt_path = build_candidate_receipt(bundle_path, root / "candidates")
    material = _prepare_update_load(
        bundle_path=bundle_path,
        candidate_receipt_path=receipt_path,
        bundle_relative_path="tw-gov/nhi/test-bundle",
        activation_cut="2026-07-27",
        owner_key="fixture-owner",
        notification_window_start="2026-07-27T00:00:00+00:00",
        notification_window_end="2026-07-27T00:05:00+00:00",
    )
    return bundle_path, receipt_path, material


def rewrite_attempt_times(
    receipt_path: Path,
    *,
    primary_start: str,
    primary_completed: str,
    fallback_start: str,
    fallback_completed: str,
) -> None:
    attempts_path = receipt_path.parent / "attempts.jsonl"
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    attempts[0]["started_at"] = primary_start
    attempts[0]["completed_at"] = primary_completed
    attempts[1]["started_at"] = fallback_start
    attempts[1]["completed_at"] = fallback_completed
    attempts_path.write_bytes(
        b"".join(canonical_json_bytes(row) for row in attempts)
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["attempts_sha256"] = file_sha256(attempts_path)
    write_json(receipt_path, receipt)


class UpdatePgMaterialTests(unittest.TestCase):
    def test_material_is_deterministic_bounded_and_stage_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path, receipt_path, first = prepare_fixture(root)
            second = _prepare_update_load(
                bundle_path=bundle_path,
                candidate_receipt_path=receipt_path,
                bundle_relative_path="tw-gov/nhi/test-bundle",
                activation_cut="2026-07-27",
                owner_key="fixture-owner",
                notification_window_start="2026-07-27T00:00:00+00:00",
                notification_window_end="2026-07-27T00:05:00+00:00",
            )
            for value in (
                first.job_id,
                first.lease_id,
                first.receipt_id,
                first.proposal_id,
            ):
                uuid.UUID(value)
            self.assertEqual(first.job_id, second.job_id)
            self.assertEqual(
                first.expected_fingerprint, second.expected_fingerprint
            )
            self.assertEqual(first.final_state, "needs_review")
            attempts = first.rows["worker_attempt"]
            self.assertEqual([row["status"] for row in attempts], ["failed", "success"])
            self.assertEqual(
                attempts[1]["primary_attempt_id"], attempts[0]["attempt_id"]
            )
            self.assertEqual(
                attempts[0]["failure_code"], "contract_failed:ProposalError"
            )
            self.assertLessEqual(
                first.rows["job_lease"][0]["max_runtime_seconds"], 21600
            )
            self.assertIn(
                first.manifest_sha256,
                {
                    row["artifact_sha256"]
                    for row in first.rows["content_artifact"]
                },
            )
            self.assertEqual(
                {row["state"] for row in first.rows["candidate_state_transition"]},
                {"needs_review"},
            )
            self.assertEqual(
                {
                    row["source_role"]
                    for row in first.rows["candidate_source_span"]
                },
                {"effective_expression", "comparison_old", "comparison_new"},
            )

    def test_new_worker_fingerprint_loads_and_legacy_form_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            arguments = {
                "bundle_path": bundle_path,
                "bundle_relative_path": "tw-gov/nhi/test-bundle",
                "activation_cut": "2026-07-27",
                "owner_key": "fixture-owner",
                "notification_window_start": (
                    "2026-07-27T00:00:00+00:00"
                ),
                "notification_window_end": (
                    "2026-07-27T00:05:00+00:00"
                ),
            }
            loaded = _prepare_update_load(
                candidate_receipt_path=receipt_path,
                **arguments,
            )
            packet = source_packet(bundle_path)
            prompt_sha = sha256_bytes(
                build_worker_prompt(packet).encode("utf-8")
            )
            legacy_fingerprint = stable_id(
                "nhi-worker-job",
                packet["bundle_id"],
                packet["bundle_fingerprint"],
                WORKER_PROMPT_VERSION,
                prompt_sha,
            )
            self.assertEqual(
                loaded.job_fingerprint,
                worker_job_fingerprint(
                    manifest_sha256=packet["manifest_sha256"],
                    prompt_sha256=prompt_sha,
                ),
            )
            self.assertNotEqual(loaded.job_fingerprint, legacy_fingerprint)

            legacy_dir = root / "candidates" / legacy_fingerprint
            shutil.copytree(receipt_path.parent, legacy_dir)
            legacy_receipt_path = legacy_dir / "candidate-receipt.json"
            legacy_receipt = json.loads(
                legacy_receipt_path.read_text(encoding="utf-8")
            )
            legacy_receipt["job_fingerprint"] = legacy_fingerprint
            write_json(legacy_receipt_path, legacy_receipt)
            with self.assertRaisesRegex(
                UpdateStageLoadError,
                "job fingerprint does not match worker inputs",
            ):
                _prepare_update_load(
                    candidate_receipt_path=legacy_receipt_path,
                    **arguments,
                )

    def test_delayed_backlog_does_not_expand_the_worker_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            rewrite_bundle_times(
                bundle_path,
                observed_at="2026-07-26T00:00:00+00:00",
                sealed_at="2026-07-26T00:00:30+00:00",
            )
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            material = _prepare_update_load(
                bundle_path=bundle_path,
                candidate_receipt_path=receipt_path,
                bundle_relative_path="tw-gov/nhi/test-bundle",
                activation_cut="2026-07-27",
                owner_key="fixture-owner",
                notification_window_start="2026-07-26T00:00:00+00:00",
                notification_window_end="2026-07-26T00:05:00+00:00",
            )
            lease = material.rows["job_lease"][0]
            self.assertEqual(
                lease["acquired_at"], "2026-07-27T00:01:00+00:00"
            )
            self.assertEqual(
                lease["expires_at"], "2026-07-27T00:02:00+00:00"
            )
            self.assertEqual(lease["max_runtime_seconds"], 60)
            self.assertTrue(
                all(
                    row["observed_at"] < lease["acquired_at"]
                    for row in material.rows["url_observation"]
                )
            )

    def test_tampered_receipt_fails_before_database_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path, receipt_path, _material = prepare_fixture(root)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["candidate"]["proposal"]["stable_rule_id"] = "forbidden"
            write_json(receipt_path, receipt)
            with self.assertRaises(UpdateStageLoadError):
                _prepare_update_load(
                    bundle_path=bundle_path,
                    candidate_receipt_path=receipt_path,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start="2026-07-27T00:00:00+00:00",
                    notification_window_end="2026-07-27T00:05:00+00:00",
                )

    def test_manifest_timestamps_are_bound_and_causally_ordered(self) -> None:
        cases = (
            (
                "resource_without_timezone",
                "2026-07-27T00:00:00",
                "2026-07-27T00:00:30+00:00",
            ),
            (
                "resource_after_seal",
                "2026-07-27T00:00:31+00:00",
                "2026-07-27T00:00:30+00:00",
            ),
            (
                "seal_after_worker_start",
                "2026-07-27T00:00:00+00:00",
                "2026-07-27T00:01:01+00:00",
            ),
        )
        for label, observed_at, sealed_at in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle_path = build_bundle(root / "bundles")
                rewrite_bundle_times(
                    bundle_path,
                    observed_at=observed_at,
                    sealed_at=sealed_at,
                )
                receipt_path = build_candidate_receipt(
                    bundle_path, root / "candidates"
                )
                with self.assertRaises(UpdateStageLoadError):
                    _prepare_update_load(
                        bundle_path=bundle_path,
                        candidate_receipt_path=receipt_path,
                        bundle_relative_path="tw-gov/nhi/test-bundle",
                        activation_cut="2026-07-27",
                        owner_key="fixture-owner",
                        notification_window_start=(
                            "2026-07-27T00:00:00+00:00"
                        ),
                        notification_window_end=(
                            "2026-07-27T00:05:00+00:00"
                        ),
                    )

    def test_manifest_time_mutation_after_worker_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            rewrite_bundle_times(
                bundle_path,
                observed_at="2026-07-26T23:59:59+00:00",
                sealed_at="2026-07-27T00:00:29+00:00",
            )
            with self.assertRaises(UpdateStageLoadError):
                _prepare_update_load(
                    bundle_path=bundle_path,
                    candidate_receipt_path=receipt_path,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start=(
                        "2026-07-27T00:00:00+00:00"
                    ),
                    notification_window_end=(
                        "2026-07-27T00:05:00+00:00"
                    ),
                )

    def test_forged_self_consistent_prompt_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            attempts_path = receipt_path.parent / "attempts.jsonl"
            attempts = [
                json.loads(line)
                for line in attempts_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            forged_prompt_sha = "f" * 64
            for attempt in attempts:
                attempt["prompt_sha256"] = forged_prompt_sha
            attempts_path.write_bytes(
                b"".join(canonical_json_bytes(row) for row in attempts)
            )
            receipt = json.loads(
                receipt_path.read_text(encoding="utf-8")
            )
            receipt["prompt_sha256"] = forged_prompt_sha
            receipt["attempts_sha256"] = file_sha256(attempts_path)
            write_json(receipt_path, receipt)
            (receipt_path.parent / "prompt.json").write_text(
                '{"forged":true}', encoding="utf-8"
            )
            with self.assertRaises(UpdateStageLoadError):
                _prepare_update_load(
                    bundle_path=bundle_path,
                    candidate_receipt_path=receipt_path,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start=(
                        "2026-07-27T00:00:00+00:00"
                    ),
                    notification_window_end=(
                        "2026-07-27T00:05:00+00:00"
                    ),
                )

    def test_lease_envelope_boundaries(self) -> None:
        cases = (
            (
                "zero_duration",
                "2026-07-27T00:00:30+00:00",
                "2026-07-27T00:00:30+00:00",
                True,
            ),
            (
                "subsecond",
                "2026-07-27T00:00:30.500000+00:00",
                "2026-07-27T00:00:30.750000+00:00",
                True,
            ),
            (
                "exact_six_hours",
                "2026-07-27T00:00:30+00:00",
                "2026-07-27T06:00:30+00:00",
                True,
            ),
            (
                "six_hours_plus_epsilon",
                "2026-07-27T00:00:30+00:00",
                "2026-07-27T06:00:30.000001+00:00",
                False,
            ),
        )
        for label, start, completed, accepted in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle_path = build_bundle(root / "bundles")
                receipt_path = build_candidate_receipt(
                    bundle_path, root / "candidates"
                )
                rewrite_attempt_times(
                    receipt_path,
                    primary_start=start,
                    primary_completed=start,
                    fallback_start=start,
                    fallback_completed=completed,
                )
                arguments = {
                    "bundle_path": bundle_path,
                    "candidate_receipt_path": receipt_path,
                    "bundle_relative_path": "tw-gov/nhi/test-bundle",
                    "activation_cut": "2026-07-27",
                    "owner_key": "fixture-owner",
                    "notification_window_start": (
                        "2026-07-27T00:00:00+00:00"
                    ),
                    "notification_window_end": (
                        "2026-07-27T00:05:00+00:00"
                    ),
                }
                if accepted:
                    material = _prepare_update_load(**arguments)
                    self.assertLessEqual(
                        material.rows["job_lease"][0][
                            "max_runtime_seconds"
                        ],
                        21600,
                    )
                else:
                    with self.assertRaises(UpdateStageLoadError):
                        _prepare_update_load(**arguments)

    def test_semantic_timestamp_changes_change_replay_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            common = {
                "bundle_path": bundle_path,
                "candidate_receipt_path": receipt_path,
                "bundle_relative_path": "tw-gov/nhi/test-bundle",
                "activation_cut": "2026-07-27",
                "owner_key": "fixture-owner",
                "notification_window_end": (
                    "2026-07-27T00:05:00+00:00"
                ),
            }
            first = _prepare_update_load(
                **common,
                notification_window_start="2026-07-27T00:00:00+00:00",
            )
            shifted = _prepare_update_load(
                **common,
                notification_window_start="2026-07-26T23:59:59+00:00",
            )
            self.assertNotEqual(
                first.expected_fingerprint, shifted.expected_fingerprint
            )

    def test_fallback_cannot_overlap_primary_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = build_bundle(root / "bundles")
            receipt_path = build_candidate_receipt(
                bundle_path, root / "candidates"
            )
            rewrite_attempt_times(
                receipt_path,
                primary_start="2026-07-27T00:01:00+00:00",
                primary_completed="2026-07-27T00:02:00+00:00",
                fallback_start="2026-07-27T00:01:59+00:00",
                fallback_completed="2026-07-27T00:03:00+00:00",
            )
            with self.assertRaises(UpdateStageLoadError):
                _prepare_update_load(
                    bundle_path=bundle_path,
                    candidate_receipt_path=receipt_path,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start=(
                        "2026-07-27T00:00:00+00:00"
                    ),
                    notification_window_end=(
                        "2026-07-27T00:05:00+00:00"
                    ),
                )

    def test_public_loader_returns_prior_stage_receipt_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path, receipt_path, material = prepare_fixture(root)
            verification = {
                "counts": dict(material.expected_counts),
                "fingerprint": material.expected_fingerprint,
            }
            with (
                mock.patch(
                    "nhi_rule_history.update.pg_stage._apply_material",
                    return_value=True,
                ) as apply_mock,
                mock.patch(
                    "nhi_rule_history.update.pg_stage._verify_loaded",
                    return_value=verification,
                ) as verify_mock,
            ):
                result = load_update_candidate(
                    "postgresql://unused",
                    bundle_path,
                    receipt_path,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                )
            self.assertTrue(result["replayed"])
            self.assertEqual(result["bundle_receipt_id"], material.receipt_id)
            self.assertEqual(
                result["verification"]["fingerprint"],
                material.expected_fingerprint,
            )
            apply_mock.assert_called_once()
            verify_mock.assert_called_once()


class _RelationCursor:
    def __init__(self, material):
        self.material = material
        self.last_result = None
        self.inserted_relations: list[tuple[str, str | None, str]] = []
        self.advisory_locks: list[str] = []
        self.artifacts = {
            row["artifact_sha256"]: (row["byte_size"], row["media_type"])
            for row in material.rows["content_artifact"]
        }

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.last_result = None
        if normalized.startswith("SELECT byte_size, media_type"):
            self.last_result = self.artifacts[params[0]]
        elif "SELECT pg_advisory_xact_lock" in normalized:
            self.advisory_locks.append(params[0])
        elif "INSERT INTO nhi_rule_history_update_ops.url_observation" in normalized:
            requested_url = params[4]
            final_url = params[5]
            artifact_sha = params[11]
            previous_sha = params[12]
            relation = params[13]
            self.inserted_relations.append(
                (requested_url, previous_sha, relation)
            )

    def fetchone(self):
        return self.last_result


class UrlRelationTests(unittest.TestCase):
    def test_append_rows_do_not_assert_chronological_predecessors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _bundle, _receipt, material = prepare_fixture(Path(temporary))
            cursor = _RelationCursor(material)
            _insert_new_material(cursor, material)
            self.assertEqual(
                len(cursor.advisory_locks),
                len(material.rows["url_observation"]),
            )
            self.assertTrue(
                all(
                    previous is None and relation == "not_comparable"
                    for _url, previous, relation in cursor.inserted_relations
                )
            )


class ObservationLeaseMigrationTests(unittest.TestCase):
    def test_patch_derives_chronology_and_guards_forward_and_rollback(self) -> None:
        forward = OPS_OBSERVATION_LEASE_FIX.read_text(encoding="utf-8")
        rollback = OPS_OBSERVATION_LEASE_FIX_ROLLBACK.read_text(
            encoding="utf-8"
        )
        for required in (
            "v_url_response_chronology",
            "lag(observation.artifact_sha256)",
            "NEW.observed_at > earliest_attempt_start",
            "trigger.tgenabled",
            "trigger.tgfoid",
            "unknown exact chronology function state",
            "pg_get_viewdef",
            "base_observation_source",
        ):
            self.assertIn(required, forward)
        for required in (
            "rollback refuses an unmanaged or successor chronology function",
            "DROP VIEW nhi_rule_history_update_ops.v_url_response_chronology",
            "trigger.tgenabled",
            "trigger.tgfoid",
        ):
            self.assertIn(required, rollback)


class UpdatePgLoaderLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ.get("NHI_RULE_HISTORY_TEST_DSN")
        if not cls.dsn:
            raise unittest.SkipTest("NHI_RULE_HISTORY_TEST_DSN is not set")
        cls.psql = shutil.which("psql")
        if not cls.psql:
            raise unittest.SkipTest("psql is unavailable")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("psycopg is unavailable") from exc

    @classmethod
    def run_psql(cls, path: Path) -> None:
        result = subprocess.run(
            [
                cls.psql,
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--dbname",
                cls.dsn,
                "--file",
                str(path),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)

    @classmethod
    def assert_psql_fails(cls, path: Path, expected_error: str) -> None:
        result = subprocess.run(
            [
                cls.psql,
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--dbname",
                cls.dsn,
                "--file",
                str(path),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 or expected_error not in result.stderr:
            raise AssertionError(
                "migration did not fail with the expected exact-state guard: "
                + result.stderr
            )

    def test_concurrent_observation_and_attempt_are_serialized(self) -> None:
        import psycopg

        self.run_psql(OPS_FORWARD)
        self.run_psql(OPS_OBSERVATION_LEASE_FIX)
        try:
            job_id = str(uuid.uuid4())
            lease_id = str(uuid.uuid4())
            attempt_id = str(uuid.uuid4())
            observation_id = str(uuid.uuid4())
            artifact_sha = "7" * 64
            with psycopg.connect(self.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO nhi_rule_history_update_ops.update_job (
                          job_id, job_fingerprint, contract_version,
                          runner_version, feed_url, request_profile_sha256,
                          notification_window_start,
                          notification_window_end, activation_cut,
                          scheduled_at
                        ) VALUES (
                          %s,%s,'fixture','fixture',
                          'https://example.test/feed',%s,
                          '2026-07-27T00:00:00+00:00',
                          '2026-07-27T00:01:00+00:00',
                          '2026-07-27',
                          '2026-07-27T00:00:00+00:00'
                        )
                        """,
                        (
                            job_id,
                            sha256_bytes(b"concurrent-job"),
                            "8" * 64,
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO nhi_rule_history_update_ops.job_lease (
                          lease_id, job_id, owner_key, acquired_at,
                          expires_at, max_runtime_seconds
                        ) VALUES (
                          %s,%s,'fixture-owner',
                          '2026-07-27T00:00:00+00:00',
                          '2026-07-27T00:01:00+00:00',60
                        )
                        """,
                        (lease_id, job_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO
                          nhi_rule_history_update_ops.content_artifact (
                            artifact_sha256, byte_size, media_type,
                            bundle_relative_path, first_observed_at
                          ) VALUES (
                            %s,1,'text/plain','fixture/concurrent',
                            '2026-07-27T00:00:30+00:00'
                          )
                        """,
                        (artifact_sha,),
                    )

            ready = threading.Event()
            outcome: dict[str, str] = {}

            def insert_attempt() -> None:
                try:
                    with psycopg.connect(
                        self.dsn,
                        application_name="nhi-rule-history-concurrency-test",
                    ) as connection:
                        ready.set()
                        connection.execute(
                            """
                            INSERT INTO
                              nhi_rule_history_update_ops.worker_attempt (
                                attempt_id, job_id, lease_id, owner_key,
                                attempt_no, lane, primary_attempt_id,
                                provider, runtime, model, prompt_sha256,
                                output_sha256, started_at, completed_at,
                                status, failure_code, fallback_reason
                              ) VALUES (
                                %s,%s,%s,'fixture-owner',1,'primary',NULL,
                                'fixture','fixture','fixture',%s,%s,
                                '2026-07-27T00:00:20+00:00',
                                '2026-07-27T00:00:20+00:00',
                                'success',NULL,NULL
                              )
                            """,
                            (
                                attempt_id,
                                job_id,
                                lease_id,
                                "9" * 64,
                                "a" * 64,
                            ),
                        )
                    outcome["status"] = "unexpected_success"
                except psycopg.Error as exc:
                    outcome["status"] = "rejected"
                    outcome["sqlstate"] = str(exc.sqlstate)

            with psycopg.connect(self.dsn) as observation_connection:
                observation_connection.execute(
                    """
                    INSERT INTO
                      nhi_rule_history_update_ops.url_observation (
                        url_observation_id, job_id, lease_id, owner_key,
                        requested_url, final_url, observed_at, outcome,
                        http_status, response_headers,
                        response_headers_sha256, artifact_sha256,
                        previous_artifact_sha256, relation_to_previous,
                        error_code
                      ) VALUES (
                        %s,%s,%s,'fixture-owner',
                        'https://example.test/concurrent',
                        'https://example.test/concurrent',
                        '2026-07-27T00:00:30+00:00',
                        'response',200,'{}'::jsonb,%s,%s,NULL,
                        'not_comparable',NULL
                      )
                    """,
                    (
                        observation_id,
                        job_id,
                        lease_id,
                        sha256_bytes(b"{}"),
                        artifact_sha,
                    ),
                )
                thread = threading.Thread(target=insert_attempt, daemon=True)
                thread.start()
                self.assertTrue(ready.wait(timeout=2))
                waiting = False
                with psycopg.connect(self.dsn) as observer:
                    for _ in range(50):
                        waiting = bool(
                            observer.execute(
                                """
                                SELECT count(*) > 0
                                FROM pg_stat_activity
                                WHERE application_name =
                                  'nhi-rule-history-concurrency-test'
                                  AND wait_event_type = 'Lock'
                                """
                            ).fetchone()[0]
                        )
                        if waiting:
                            break
                        observer.execute("SELECT pg_sleep(0.02)")
                self.assertTrue(
                    waiting,
                    "attempt transaction never waited on the shared lease row",
                )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome.get("status"), "rejected")
            with psycopg.connect(self.dsn) as connection:
                count = connection.execute(
                    """
                    SELECT count(*)
                    FROM nhi_rule_history_update_ops.worker_attempt
                    WHERE attempt_id = %s
                    """,
                    (attempt_id,),
                ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            self.run_psql(OPS_OBSERVATION_LEASE_FIX_ROLLBACK)
            self.run_psql(OPS_ROLLBACK)

    def test_exact_migration_guards_reject_drift(self) -> None:
        import psycopg

        self.run_psql(OPS_FORWARD)
        try:
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    """
                    CREATE OR REPLACE FUNCTION
                      nhi_rule_history_update_ops
                        .guard_owned_observation_insert()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SET search_path = pg_catalog
                    AS $function$
                    DECLARE
                      lease_owner text;
                      lease_start timestamptz;
                      lease_end timestamptz;
                    BEGIN
                      PERFORM 1;
                      SELECT owner_key, acquired_at, expires_at
                        INTO lease_owner, lease_start, lease_end
                      FROM nhi_rule_history_update_ops.job_lease
                      WHERE job_id = NEW.job_id
                        AND lease_id = NEW.lease_id;
                      IF NOT FOUND
                         OR lease_owner IS DISTINCT FROM NEW.owner_key
                         OR NEW.observed_at < lease_start
                         OR NEW.observed_at > lease_end THEN
                        RAISE EXCEPTION
                          'URL observation is outside its owned lease'
                          USING ERRCODE = 'insufficient_privilege';
                      END IF;
                      RETURN NEW;
                    END;
                    $function$
                    """
                )
            self.assert_psql_fails(
                OPS_OBSERVATION_LEASE_FIX,
                "unknown exact chronology function state",
            )
        finally:
            self.run_psql(OPS_ROLLBACK)

        self.run_psql(OPS_FORWARD)
        self.run_psql(OPS_OBSERVATION_LEASE_FIX)
        try:
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW
                      nhi_rule_history_update_ops
                        .v_url_response_chronology
                    AS
                    SELECT
                      observation.*,
                      NULL::uuid
                        AS chronological_previous_observation_id,
                      NULL::nhi_rule_history_update_ops.sha256_hex
                        AS chronological_previous_artifact_sha256,
                      NULL::text
                        AS chronological_previous_final_url,
                      'not_comparable'::text
                        AS chronological_relation_to_previous
                    FROM nhi_rule_history_update_ops.url_observation
                      observation
                    """
                )
            self.assert_psql_fails(
                OPS_OBSERVATION_LEASE_FIX_ROLLBACK,
                "rollback refuses a drifted or missing URL chronology view",
            )
        finally:
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    """
                    DROP VIEW IF EXISTS
                      nhi_rule_history_update_ops
                        .v_url_response_chronology
                    """
                )
            self.run_psql(OPS_ROLLBACK)

    def test_live_load_replay_and_rollback(self) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*) FROM pg_namespace
                    WHERE nspname IN (
                      'nhi_rule_history_update_ops',
                      'nhi_rule_history_candidate_stage'
                    )
                    """
                )
                if cursor.fetchone()[0] != 0:
                    self.fail("test DSN must be an unused scratch database")
        applied_ops = False
        applied_observation_fix = False
        applied_candidate = False
        try:
            self.run_psql(OPS_FORWARD)
            applied_ops = True
            self.run_psql(OPS_OBSERVATION_LEASE_FIX)
            applied_observation_fix = True
            self.run_psql(OPS_OBSERVATION_LEASE_FIX)
            self.run_psql(CANDIDATE_FORWARD)
            applied_candidate = True
            with tempfile.TemporaryDirectory() as temporary:
                bundle, receipt, material = prepare_fixture(Path(temporary))
                first = load_update_candidate(
                    self.dsn,
                    bundle,
                    receipt,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                )
                second = load_update_candidate(
                    self.dsn,
                    bundle,
                    receipt,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                )
                self.assertFalse(first["replayed"])
                self.assertTrue(second["replayed"])
                self.assertEqual(
                    first["verification"]["fingerprint"],
                    material.expected_fingerprint,
                )
                self.assertEqual(first["verification"], second["verification"])
                chronology_url = "https://example.test/chronology"
                observations = (
                    (
                        "later",
                        "2026-07-26T02:00:00+00:00",
                        "2026-07-27T02:00:00+00:00",
                        "b" * 64,
                        "https://example.test/new",
                    ),
                    (
                        "earlier",
                        "2026-07-26T01:00:00+00:00",
                        "2026-07-27T01:00:00+00:00",
                        "a" * 64,
                        "https://example.test/old",
                    ),
                )
                inserted: dict[str, dict[str, str]] = {}
                with psycopg.connect(self.dsn) as connection:
                    with connection.cursor() as cursor:
                        for label, observed_at, attempted_at, artifact, final_url in observations:
                            identifiers = {
                                "job": str(uuid.uuid4()),
                                "lease": str(uuid.uuid4()),
                                "attempt": str(uuid.uuid4()),
                                "observation": str(uuid.uuid4()),
                            }
                            inserted[label] = identifiers
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.update_job (
                                  job_id, job_fingerprint, contract_version,
                                  runner_version, feed_url,
                                  request_profile_sha256,
                                  notification_window_start,
                                  notification_window_end, activation_cut,
                                  scheduled_at
                                ) VALUES (
                                  %s,%s,'fixture','fixture',
                                  'https://example.test/feed',%s,
                                  '2026-07-26T00:00:00+00:00',
                                  '2026-07-26T00:01:00+00:00',
                                  '2026-07-27',%s
                                )
                                """,
                                (
                                    identifiers["job"],
                                    sha256_bytes(
                                        f"job-{label}".encode("utf-8")
                                    ),
                                    "c" * 64,
                                    attempted_at,
                                ),
                            )
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.job_lease (
                                  lease_id, job_id, owner_key, acquired_at,
                                  expires_at, max_runtime_seconds
                                ) VALUES (
                                  %s,%s,'fixture-owner',%s,
                                  %s::timestamptz + interval '1 second',1
                                )
                                """,
                                (
                                    identifiers["lease"],
                                    identifiers["job"],
                                    attempted_at,
                                    attempted_at,
                                ),
                            )
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.worker_attempt (
                                  attempt_id, job_id, lease_id, owner_key,
                                  attempt_no, lane, primary_attempt_id,
                                  provider, runtime, model, prompt_sha256,
                                  output_sha256, started_at, completed_at,
                                  status, failure_code, fallback_reason
                                ) VALUES (
                                  %s,%s,%s,'fixture-owner',1,'primary',NULL,
                                  'fixture','fixture','fixture',%s,%s,%s,%s,
                                  'success',NULL,NULL
                                )
                                """,
                                (
                                    identifiers["attempt"],
                                    identifiers["job"],
                                    identifiers["lease"],
                                    "d" * 64,
                                    "e" * 64,
                                    attempted_at,
                                    attempted_at,
                                ),
                            )
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.content_artifact (
                                  artifact_sha256, byte_size, media_type,
                                  bundle_relative_path, first_observed_at
                                ) VALUES (%s,1,'text/plain',%s,%s)
                                """,
                                (
                                    artifact,
                                    f"fixture/{label}",
                                    observed_at,
                                ),
                            )
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.url_observation (
                                  url_observation_id, job_id, lease_id,
                                  owner_key, requested_url, final_url,
                                  observed_at, outcome, http_status,
                                  response_headers, response_headers_sha256,
                                  artifact_sha256,
                                  previous_artifact_sha256,
                                  relation_to_previous, error_code
                                ) VALUES (
                                  %s,%s,%s,'fixture-owner',%s,%s,%s,
                                  'response',200,'{}'::jsonb,%s,%s,NULL,
                                  'not_comparable',NULL
                                )
                                """,
                                (
                                    identifiers["observation"],
                                    identifiers["job"],
                                    identifiers["lease"],
                                    chronology_url,
                                    final_url,
                                    observed_at,
                                    sha256_bytes(b"{}"),
                                    artifact,
                                ),
                            )
                        cursor.execute(
                            """
                            SELECT url_observation_id::text,
                                   chronological_previous_observation_id::text,
                                   chronological_previous_artifact_sha256,
                                   chronological_relation_to_previous
                            FROM nhi_rule_history_update_ops
                              .v_url_response_chronology
                            WHERE requested_url = %s
                            ORDER BY observed_at, url_observation_id
                            """,
                            (chronology_url,),
                        )
                        chronology = cursor.fetchall()
                        self.assertEqual(
                            chronology[0],
                            (
                                inserted["earlier"]["observation"],
                                None,
                                None,
                                "first_observation",
                            ),
                        )
                        self.assertEqual(
                            chronology[1],
                            (
                                inserted["later"]["observation"],
                                inserted["earlier"]["observation"],
                                "a" * 64,
                                "redirect_changed",
                            ),
                        )
                        cursor.execute("SAVEPOINT future_observation")
                        try:
                            cursor.execute(
                                """
                                INSERT INTO nhi_rule_history_update_ops.url_observation (
                                  url_observation_id, job_id, lease_id,
                                  owner_key, requested_url, final_url,
                                  observed_at, outcome, http_status,
                                  response_headers, response_headers_sha256,
                                  artifact_sha256,
                                  previous_artifact_sha256,
                                  relation_to_previous, error_code
                                ) VALUES (
                                  %s,%s,%s,'fixture-owner',
                                  'https://example.test/future',
                                  'https://example.test/future',
                                  '2026-07-27T02:00:01+00:00',
                                  'response',200,'{}'::jsonb,%s,%s,NULL,
                                  'not_comparable',NULL
                                )
                                """,
                                (
                                    str(uuid.uuid4()),
                                    inserted["later"]["job"],
                                    inserted["later"]["lease"],
                                    sha256_bytes(b"{}"),
                                    "b" * 64,
                                ),
                            )
                        except psycopg.Error:
                            cursor.execute(
                                "ROLLBACK TO SAVEPOINT future_observation"
                            )
                        else:
                            self.fail(
                                "post-worker observation must fail closed"
                            )
        finally:
            if applied_candidate:
                self.run_psql(CANDIDATE_ROLLBACK)
            if applied_observation_fix:
                self.run_psql(OPS_OBSERVATION_LEASE_FIX_ROLLBACK)
            if applied_ops:
                self.run_psql(OPS_ROLLBACK)


if __name__ == "__main__":
    unittest.main()
