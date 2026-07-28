from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history.cli import build_parser
from nhi_rule_history.contracts import (
    canonical_json_bytes,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.update.pg_queue import (
    _ALLOWED_EDGES,
    _sanitize_attempt_evidence,
    AppliedPollLoad,
    UpdateQueueError,
    _prepare_poll_load,
    append_work_attempt,
    append_work_transition,
    load_poll_package,
)
from nhi_rule_history.update.poll import (
    RSS_LEGACY_PARSER_VERSION,
    observe_feed,
)
from nhi_rule_history.update.rss import (
    RSS_LEGACY_CLASSIFIER_VERSION,
    OfficialResponse,
    parse_rss,
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
QUEUE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_update_queue.sql"
)
QUEUE_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_queue.rollback.sql"
)


def feed(*, include_new: bool = False, explicit_guid: bool = True) -> bytes:
    guid = "<guid>rule-1</guid>" if explicit_guid else ""
    new_item = (
        """
<item><title>藥品給付規定新增公告</title>
<link>https://www.nhi.gov.tw/ch/cp-new-3258-1.html</link>
<guid>rule-2</guid><description>藥品給付規定</description>
<pubDate>Tue, 21 Jul 2026 08:00:00 +0800</pubDate></item>
"""
        if include_new
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>NHI queue fixture</title>
<item><title>修訂全民健康保險藥品給付規定</title>
<link>https://www.nhi.gov.tw/ch/cp-rule-3258-1.html</link>
{guid}<description>藥品給付規定</description>
<pubDate>Mon, 20 Jul 2026 08:00:00 +0800</pubDate></item>
<item><title>一般服務公告</title>
<link>https://www.nhi.gov.tw/ch/cp-general-3258-1.html</link>
<guid>general-1</guid><description>服務資訊</description>
<pubDate>Mon, 20 Jul 2026 09:00:00 +0800</pubDate></item>
{new_item}</channel></rss>""".encode()


def response(payload: bytes, observed_at: str) -> OfficialResponse:
    url = "https://www.nhi.gov.tw/ch/rss-3258-1.xml"
    return OfficialResponse(
        request_url=url,
        final_url=url,
        status_code=200,
        headers={"content-type": "application/rss+xml"},
        body=payload,
        observed_at=observed_at,
    )


class UpdatePgQueueUnitTests(unittest.TestCase):
    def test_selected_can_be_reclassified_without_rewriting_history(self) -> None:
        self.assertIn("ignored_non_rule", _ALLOWED_EDGES["selected"])

    def test_attempt_evidence_is_sanitized_before_hashing(self) -> None:
        sanitized = _sanitize_attempt_evidence(
            {
                "error_code": "transport_timeout",
                "password": "not-for-storage",
                "detail": (
                    "postgresql://writer:private@example.invalid/db "
                    "Bearer abcdefghijklmnop"
                ),
            }
        )
        encoded = canonical_json_bytes(sanitized).decode("utf-8")
        self.assertNotIn("not-for-storage", encoded)
        self.assertNotIn("writer:private", encoded)
        self.assertNotIn("abcdefghijklmnop", encoded)
        self.assertNotIn("postgresql://", encoded)
        self.assertNotIn("Bearer", encoded)
        self.assertEqual(
            sanitized["_redacted_sensitive_keys"], ["password"]
        )

    def test_loader_accepts_legacy_poll_with_legacy_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = feed().replace(
                "<title>一般服務公告</title>".encode(),
                "<title>特殊材料給付規定公告</title>".encode(),
            ).replace(
                "<description>服務資訊</description>".encode(),
                "<description>特殊材料給付規定</description>".encode(),
            )
            poll = observe_feed(
                Path(temporary),
                response=response(
                    payload, "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            manifest_path = poll.path / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            legacy_items = [
                item.as_dict(
                    classifier_version=RSS_LEGACY_CLASSIFIER_VERSION
                )
                for item in parse_rss(payload)
            ]
            sequence_sha = sha256_bytes(
                canonical_json_bytes(legacy_items)
            )
            manifest["parser_version"] = RSS_LEGACY_PARSER_VERSION
            manifest["items"] = legacy_items
            manifest["item_sequence_sha256"] = sequence_sha
            manifest["new_likely_drug_rule_guids"] = [
                "rule-1", "general-1"
            ]
            manifest["poll_id"] = stable_id(
                "nhi-rss-poll",
                manifest["feed_url"],
                manifest["observed_at"],
                manifest["feed_artifact_sha256"],
                sequence_sha,
                manifest["observed_guid_set_sha256"],
                str(manifest.get("previous_item_count")),
                format(float(manifest.get("collapse_ratio")), ".12g"),
            )
            manifest_path.write_bytes(canonical_json_bytes(manifest))

            material = _prepare_poll_load(
                poll.path, owner_key="fixture-owner"
            )
            self.assertEqual(
                material.new_likely_guids,
                frozenset({"rule-1", "general-1"}),
            )

    def test_cli_exposes_poll_load_and_individual_transition(self) -> None:
        parser = build_parser()
        load = parser.parse_args(
            [
                "update-poll-stage",
                "--dsn",
                "postgresql://example.invalid/test",
                "--poll-path",
                "polls/example",
                "--owner-key",
                "fixture",
            ]
        )
        self.assertEqual(load.command, "update-poll-stage")
        transition = parser.parse_args(
            [
                "update-queue-transition",
                "--dsn",
                "postgresql://example.invalid/test",
                "--work-item-id",
                "00000000-0000-0000-0000-000000000001",
                "--to-state",
                "acquired",
                "--actor-kind",
                "fixture",
                "--evidence-json",
                "evidence.json",
                "--source-job-id",
                "00000000-0000-0000-0000-000000000002",
            ]
        )
        self.assertEqual(transition.command, "update-queue-transition")
        self.assertEqual(transition.to_state, "acquired")

    def test_prepare_normalizes_every_identity_and_selects_only_new_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(), "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            material = _prepare_poll_load(
                poll.path, owner_key="fixture-owner"
            )
            self.assertEqual(
                len(material.rows["feed_item_observation"]), 2
            )
            self.assertEqual(len(material.rows["rss_work_item"]), 2)
            self.assertEqual(
                material.new_likely_guids, frozenset({"rule-1"})
            )
            identities = {
                row["rss_identity_fingerprint"]
                for row in material.rows["rss_work_item"]
            }
            self.assertEqual(len(identities), 2)
            self.assertTrue(
                all(row["guid_raw"] for row in material.rows["rss_work_item"])
            )

    def test_loader_uses_official_detail_url_when_guid_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(explicit_guid=False),
                    "2026-07-27T00:00:00+00:00",
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            material = _prepare_poll_load(
                poll.path, owner_key="fixture-owner"
            )
            work = material.rows["rss_work_item"][0]
            self.assertEqual(
                work["item_identity_kind"], "official_detail_url"
            )
            self.assertEqual(
                work["item_identity_value"],
                "https://www.nhi.gov.tw/ch/cp-rule-3258-1.html",
            )
            self.assertIsNone(work["guid_raw"])

    def test_loader_rejects_present_but_empty_guid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = feed().replace(
                b"<guid>rule-1</guid>",
                b"<guid></guid>",
            )
            poll = observe_feed(
                Path(temporary),
                response=response(
                    payload,
                    "2026-07-27T00:00:00+00:00",
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            with self.assertRaisesRegex(UpdateQueueError, "empty explicit GUID"):
                _prepare_poll_load(
                    poll.path,
                    owner_key="fixture-owner",
                )

    def test_loader_reverifies_canonical_immutable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(), "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            manifest_path = poll.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                UpdateQueueError, "canonical immutable JSON"
            ):
                _prepare_poll_load(
                    poll.path, owner_key="fixture-owner"
                )

    def test_loader_rejects_manifest_content_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(), "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            manifest_path = poll.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["feed_content_path"] = "../feed.xml"
            manifest_path.write_bytes(canonical_json_bytes(manifest))
            with self.assertRaisesRegex(
                UpdateQueueError, "content path"
            ):
                _prepare_poll_load(
                    poll.path, owner_key="fixture-owner"
                )

    def test_public_load_calls_apply_then_fresh_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(), "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            applied = AppliedPollLoad(
                replayed=False,
                created_work_item_count=2,
                selected_work_item_count=1,
                ignored_work_item_count=1,
                expected_projection={"update_job": [["ok"]]},
                expected_fingerprint="a" * 64,
            )
            verification = {
                "counts": {"update_job": 1},
                "fingerprint": "a" * 64,
            }
            with mock.patch(
                "nhi_rule_history.update.pg_queue._apply_poll",
                return_value=applied,
            ) as apply_mock, mock.patch(
                "nhi_rule_history.update.pg_queue._verify_poll_load",
                return_value=verification,
            ) as verify_mock:
                result = load_poll_package(
                    "postgresql://example.invalid/test",
                    poll.path,
                    owner_key="fixture-owner",
                )
            apply_mock.assert_called_once()
            verify_mock.assert_called_once()
            self.assertEqual(result["created_work_item_count"], 2)
            self.assertEqual(result["selected_work_item_count"], 1)
            self.assertEqual(result["ignored_work_item_count"], 1)

    def test_fixture_manifest_remains_canonical_for_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=response(
                    feed(), "2026-07-27T00:00:00+00:00"
                ),
                observed_guids=[],
                previous_item_count=None,
            )
            path = poll.path / "manifest.json"
            value = json.loads(path.read_bytes())
            self.assertEqual(path.read_bytes(), canonical_json_bytes(value))


class UpdatePgQueueLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ.get("NHI_RULE_HISTORY_TEST_DSN")
        if not cls.dsn:
            raise unittest.SkipTest("NHI_RULE_HISTORY_TEST_DSN is not set")
        cls.psql = shutil.which("psql")
        if not cls.psql:
            raise unittest.SkipTest("psql is unavailable")

    @classmethod
    def run_psql(
        cls,
        *,
        file: Path | None = None,
        command: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            cls.psql,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--dbname",
            cls.dsn,
        ]
        if file is not None:
            argv.extend(["--file", str(file)])
        if command is not None:
            argv.extend(["--command", command])
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"psql failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    def test_two_polls_replay_and_one_item_transition_do_not_touch_sibling(
        self,
    ) -> None:
        presence = self.run_psql(
            command=(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN "
                "('nhi_rule_history_update_ops',"
                "'nhi_rule_history_candidate_stage',"
                "'nhi_rule_history_update_queue');"
            )
        ).stdout.strip()
        self.assertEqual(
            presence, "0", "test DSN must be an unused scratch database"
        )
        applied_ops = applied_fix = applied_candidate = applied_queue = False
        try:
            self.run_psql(file=OPS_FORWARD)
            applied_ops = True
            self.run_psql(file=OPS_OBSERVATION_LEASE_FIX)
            applied_fix = True
            self.run_psql(file=CANDIDATE_FORWARD)
            applied_candidate = True
            self.run_psql(file=QUEUE_FORWARD)
            applied_queue = True
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                first_poll = observe_feed(
                    root,
                    response=response(
                        feed(), "2026-07-27T00:00:00+00:00"
                    ),
                    observed_guids=[],
                    previous_item_count=None,
                )
                first = load_poll_package(
                    self.dsn,
                    first_poll.path,
                    owner_key="fixture-owner",
                )
                self.assertFalse(first["replayed"])
                self.assertEqual(first["created_work_item_count"], 2)
                self.assertEqual(first["selected_work_item_count"], 1)
                self.assertEqual(first["ignored_work_item_count"], 1)
                with self.assertRaisesRegex(
                    AssertionError,
                    "non-worker URL observation is outside its owned lease",
                ):
                    self.run_psql(
                        command=f"""
INSERT INTO nhi_rule_history_update_ops.url_observation (
  url_observation_id, job_id, lease_id, owner_key, requested_url,
  final_url, observed_at, outcome, http_status, response_headers,
  response_headers_sha256, artifact_sha256,
  previous_artifact_sha256, relation_to_previous, error_code
)
SELECT
  '00000000-0000-0000-0000-000000000123'::uuid,
  job_id, lease_id, owner_key, requested_url || '?late',
  final_url, observed_at + interval '2 seconds', outcome,
  http_status, response_headers, response_headers_sha256,
  artifact_sha256, NULL, 'not_comparable', NULL
FROM nhi_rule_history_update_ops.url_observation
WHERE job_id = '{first["job_id"]}'::uuid
LIMIT 1;
"""
                    )

                replay = load_poll_package(
                    self.dsn,
                    first_poll.path,
                    owner_key="fixture-owner",
                )
                self.assertTrue(replay["replayed"])
                self.assertEqual(replay["created_work_item_count"], 0)

                second_poll = observe_feed(
                    root,
                    response=response(
                        feed(include_new=True),
                        "2026-07-27T01:00:00+00:00",
                    ),
                    observed_guids=["rule-1", "general-1"],
                    previous_item_count=2,
                )
                second = load_poll_package(
                    self.dsn,
                    second_poll.path,
                    owner_key="fixture-owner",
                )
                self.assertEqual(second["created_work_item_count"], 1)
                self.assertEqual(second["selected_work_item_count"], 1)

                selected_rows = self.run_psql(
                    command="""
SELECT work_item_id::text, guid_raw
FROM nhi_rule_history_update_queue.v_work_item_current
WHERE current_state = 'selected'
ORDER BY guid_raw;
"""
                ).stdout.strip().splitlines()
                self.assertEqual(len(selected_rows), 2)
                first_work_id, first_guid = selected_rows[0].split("|")
                self.assertEqual(first_guid, "rule-1")
                attempted = append_work_attempt(
                    self.dsn,
                    work_item_id=first_work_id,
                    attempt_kind="acquisition",
                    idempotency_key="fixture-acquisition-1",
                    outcome="transient_failure",
                    actor_kind="fixture-controller",
                    evidence={
                        "error_code": "transport_timeout",
                        "password": "must-not-persist",
                        "detail": (
                            "postgresql://writer:private@example.invalid/db "
                            "Bearer abcdefghijklmnop"
                        ),
                    },
                    source_job_id=first["job_id"],
                    recorded_at="2026-07-27T00:00:00.500000+00:00",
                )
                self.assertFalse(attempted["replayed"])
                self.assertEqual(
                    attempted["work_state_at_attempt"], "selected"
                )
                replayed_attempt = append_work_attempt(
                    self.dsn,
                    work_item_id=first_work_id,
                    attempt_kind="acquisition",
                    idempotency_key="fixture-acquisition-1",
                    outcome="transient_failure",
                    actor_kind="fixture-controller",
                    evidence={
                        "error_code": "transport_timeout",
                        "password": "must-not-persist",
                        "detail": (
                            "postgresql://writer:private@example.invalid/db "
                            "Bearer abcdefghijklmnop"
                        ),
                    },
                    source_job_id=first["job_id"],
                )
                self.assertTrue(replayed_attempt["replayed"])
                self.assertEqual(
                    replayed_attempt["attempt_id"],
                    attempted["attempt_id"],
                )
                with self.assertRaisesRegex(
                    UpdateQueueError, "reused with different material"
                ):
                    append_work_attempt(
                        self.dsn,
                        work_item_id=first_work_id,
                        attempt_kind="acquisition",
                        idempotency_key="fixture-acquisition-1",
                        outcome="transient_failure",
                        actor_kind="fixture-controller",
                        evidence={"error_code": "different_failure"},
                        source_job_id=first["job_id"],
                    )
                transitioned = append_work_transition(
                    self.dsn,
                    work_item_id=first_work_id,
                    to_state="acquired",
                    actor_kind="fixture-controller",
                    evidence={"event": "fixture acquisition"},
                    source_job_id=first["job_id"],
                    recorded_at="2026-07-27T00:00:01+00:00",
                )
                self.assertFalse(transitioned["replayed"])
                post_transition_attempt_replay = append_work_attempt(
                    self.dsn,
                    work_item_id=first_work_id,
                    attempt_kind="acquisition",
                    idempotency_key="fixture-acquisition-1",
                    outcome="transient_failure",
                    actor_kind="fixture-controller",
                    evidence={
                        "error_code": "transport_timeout",
                        "password": "must-not-persist",
                        "detail": (
                            "postgresql://writer:private@example.invalid/db "
                            "Bearer abcdefghijklmnop"
                        ),
                    },
                    source_job_id=first["job_id"],
                )
                self.assertTrue(post_transition_attempt_replay["replayed"])
                self.assertEqual(
                    post_transition_attempt_replay["current_state"],
                    "acquired",
                )
                replayed_transition = append_work_transition(
                    self.dsn,
                    work_item_id=first_work_id,
                    to_state="acquired",
                    actor_kind="fixture-controller",
                    evidence={"event": "fixture acquisition"},
                    source_job_id=first["job_id"],
                    recorded_at="2026-07-27T00:00:01+00:00",
                )
                self.assertTrue(replayed_transition["replayed"])
                self.assertEqual(
                    replayed_transition["transition_id"],
                    transitioned["transition_id"],
                )

                states = self.run_psql(
                    command="""
SELECT guid_raw, current_state
FROM nhi_rule_history_update_queue.v_work_item_current
WHERE guid_raw IN ('rule-1', 'rule-2')
ORDER BY guid_raw;
"""
                ).stdout.strip().splitlines()
                self.assertEqual(
                    states, ["rule-1|acquired", "rule-2|selected"]
                )
                with self.assertRaisesRegex(
                    UpdateQueueError, "not allowed"
                ):
                    append_work_transition(
                        self.dsn,
                        work_item_id=first_work_id,
                        to_state="staged_needs_review",
                        actor_kind="fixture-controller",
                        evidence={"event": "skip"},
                        source_job_id=first["job_id"],
                    )
        finally:
            if applied_queue:
                self.run_psql(file=QUEUE_ROLLBACK)
            if applied_candidate:
                self.run_psql(file=CANDIDATE_ROLLBACK)
            if applied_fix:
                self.run_psql(file=OPS_OBSERVATION_LEASE_FIX_ROLLBACK)
            if applied_ops:
                self.run_psql(file=OPS_ROLLBACK)
