from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from nhi_rule_history.contracts import (
    WORKER_ATTEMPT_SCHEMA,
    append_jsonl,
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
    source_packet,
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
    return BundleBuilder(
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
    prompt_sha = "1" * 64
    job_fingerprint = stable_id(
        "nhi-worker-job",
        packet["bundle_id"],
        packet["bundle_fingerprint"],
        WORKER_PROMPT_VERSION,
        prompt_sha,
    )
    run_dir = candidate_root / job_fingerprint
    run_dir.mkdir(parents=True)
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
    def __init__(self, material, prior_by_url):
        self.material = material
        self.prior_by_url = dict(prior_by_url)
        self.last_result = None
        self.inserted_relations: list[tuple[str, str | None, str]] = []
        self.artifacts = {
            row["artifact_sha256"]: (row["byte_size"], row["media_type"])
            for row in material.rows["content_artifact"]
        }

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.last_result = None
        if normalized.startswith("SELECT byte_size, media_type"):
            self.last_result = self.artifacts[params[0]]
        elif "SELECT artifact_sha256, final_url" in normalized:
            self.last_result = self.prior_by_url.get(params[0])
        elif "INSERT INTO nhi_rule_history_update_ops.url_observation" in normalized:
            requested_url = params[4]
            final_url = params[5]
            artifact_sha = params[11]
            previous_sha = params[12]
            relation = params[13]
            self.inserted_relations.append(
                (requested_url, previous_sha, relation)
            )
            self.prior_by_url[requested_url] = (artifact_sha, final_url)

    def fetchone(self):
        return self.last_result


class UrlRelationTests(unittest.TestCase):
    def test_prior_url_bytes_are_classified_before_insert(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _bundle, _receipt, material = prepare_fixture(Path(temporary))
            feed = next(
                row
                for row in material.rows["url_observation"]
                if row["requested_url"].endswith("rss-3258-1.xml")
            )
            for prior_sha, expected in (
                ("0" * 64, "same_url_new_bytes"),
                (feed["artifact_sha256"], "same_bytes"),
            ):
                with self.subTest(expected=expected):
                    cursor = _RelationCursor(
                        material,
                        {
                            feed["requested_url"]: (
                                prior_sha,
                                feed["final_url"],
                            )
                        },
                    )
                    _insert_new_material(cursor, material)
                    relation = next(
                        row
                        for row in cursor.inserted_relations
                        if row[0] == feed["requested_url"]
                    )
                    self.assertEqual(relation[1], prior_sha)
                    self.assertEqual(relation[2], expected)


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
        applied_candidate = False
        try:
            self.run_psql(OPS_FORWARD)
            applied_ops = True
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
        finally:
            if applied_candidate:
                self.run_psql(CANDIDATE_ROLLBACK)
            if applied_ops:
                self.run_psql(OPS_ROLLBACK)


if __name__ == "__main__":
    unittest.main()
