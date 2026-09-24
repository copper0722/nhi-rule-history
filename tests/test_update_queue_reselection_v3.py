from __future__ import annotations

import unittest
from pathlib import Path

from nhi_rule_history.contracts import sha256_bytes
from nhi_rule_history.update.pg_queue import (
    RESELECTION_ACTOR,
    UpdateQueueError,
    _ignoring_classifier_version,
    _reselection_decision,
    append_work_transition,
    plan_classifier_reselection,
    reselect_ignored_work_item,
)
from nhi_rule_history.update.rss import (
    RSS_CLASSIFIER_VERSION,
    RSS_LEGACY_CLASSIFIER_VERSION,
    RSS_V2_CLASSIFIER_VERSION,
)
from tests import test_update_queue_recovery_v2 as recovery_fixture

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
RESELECTION_FORWARD = (
    MIGRATIONS / "2026-09-24_nhi_rule_history_update_queue_reselection_v3.sql"
)
RESELECTION_ROLLBACK = (
    MIGRATIONS
    / "2026-09-24_nhi_rule_history_update_queue_reselection_v3.rollback.sql"
)

SECTION_4_2 = (
    "公告修訂4.2.血液代用製劑及血液成分製劑及附表十八之五"
    "重型血友病患醫療評估追蹤紀錄表之給付規定。"
)
SPECIAL_MATERIAL = "公告修正既有功能類別特殊材料「特殊材質縫合錨釘」給付規定。"

POLL_JOB_ID = "40000000-0000-0000-0000-000000000001"
V3_JOB_ID = "40000000-0000-0000-0000-000000000101"
FEED_OBSERVATION_ID = "40000000-0000-0000-0000-000000000004"
SECTION_ITEM_ID = "40000000-0000-0000-0000-000000000010"
MATERIAL_ITEM_ID = "40000000-0000-0000-0000-000000000011"
FAILED_ITEM_ID = "40000000-0000-0000-0000-000000000012"
MALFORMED_ITEM_ID = "40000000-0000-0000-0000-000000000013"


def guard_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(
        "CREATE OR REPLACE FUNCTION\n"
        "  nhi_rule_history_update_queue.guard_transition_insert()"
    )
    return text[start:text.index("$$;", start) + 3]


def row(
    *,
    evidence: dict,
    parser_version: str | None,
    title: str = SECTION_4_2,
    reselected_by: list[str] | None = None,
) -> tuple:
    return (
        SECTION_ITEM_ID,
        2,
        "2026-09-16T06:12:40+00:00",
        evidence,
        "rss_guid",
        "section-4-2",
        title,
        "https://www.nhi.gov.tw/ch/cp-section-3258-1.html",
        parser_version,
        reselected_by or [],
    )


POLL_IGNORED = {"classifier": "rss-item-keywords/v1", "event": "ignored_non_rule"}


class ReselectionStaticTests(unittest.TestCase):
    def test_rollback_restores_the_recovery_v2_guard_verbatim(self) -> None:
        self.assertEqual(
            guard_block(RESELECTION_ROLLBACK),
            guard_block(recovery_fixture.RECOVERY_FORWARD),
        )

    def test_forward_guard_only_adds_the_reselection_exit(self) -> None:
        forward = guard_block(RESELECTION_FORWARD)
        self.assertIn("NEW.actor_kind = 'deterministic_classifier_reselection'", forward)
        self.assertIn("IF NOT reselection AND NOT (", forward)
        self.assertIn("'terminal work-item states prevent silent retry'", forward)
        self.assertIn("'this classifier version already reselected the work item'", forward)
        self.assertEqual(RESELECTION_ACTOR, "deterministic_classifier_reselection")


class ReselectionDecisionTests(unittest.TestCase):
    def test_prior_classifier_comes_from_the_deciding_writer(self) -> None:
        self.assertEqual(
            _ignoring_classifier_version(POLL_IGNORED, "nhi-rule-history-rss/1.1.0"),
            RSS_V2_CLASSIFIER_VERSION,
        )
        self.assertEqual(
            _ignoring_classifier_version(POLL_IGNORED, "nhi-rule-history-rss/1.0.0"),
            RSS_LEGACY_CLASSIFIER_VERSION,
        )
        self.assertEqual(
            _ignoring_classifier_version(
                {"classifier": "drug-noun-and-reimbursement-term-required"}, None
            ),
            RSS_V2_CLASSIFIER_VERSION,
        )
        self.assertEqual(
            _ignoring_classifier_version({"classifier": RSS_CLASSIFIER_VERSION}, None),
            RSS_CLASSIFIER_VERSION,
        )
        self.assertIsNone(_ignoring_classifier_version(POLL_IGNORED, "unknown/9"))
        self.assertIsNone(_ignoring_classifier_version({"event": "x"}, None))

    def test_decisions(self) -> None:
        v2_poll = "nhi-rule-history-rss/1.1.0"
        decide = lambda r: _reselection_decision(r, RSS_CLASSIFIER_VERSION)
        chosen = decide(row(evidence=POLL_IGNORED, parser_version=v2_poll))
        self.assertEqual(chosen["decision"], "reselect")
        self.assertEqual(chosen["prior_classifier_version"], RSS_V2_CLASSIFIER_VERSION)
        self.assertEqual(
            chosen["title_sha256"], sha256_bytes(SECTION_4_2.encode("utf-8"))
        )
        self.assertEqual(
            decide(row(evidence=POLL_IGNORED, parser_version="nhi-rule-history-rss/1.2.0"))["decision"],
            "same_classifier_version",
        )
        self.assertEqual(
            decide(row(evidence=POLL_IGNORED, parser_version=v2_poll, title=SPECIAL_MATERIAL))["decision"],
            "still_not_selected",
        )
        self.assertEqual(
            decide(
                row(
                    evidence=POLL_IGNORED,
                    parser_version=v2_poll,
                    reselected_by=[RSS_CLASSIFIER_VERSION],
                )
            )["decision"],
            "already_reselected",
        )
        self.assertEqual(
            decide(row(evidence={"event": "ignored"}, parser_version=v2_poll))["decision"],
            "unknown_prior_classifier",
        )

    def test_only_title_classifiers_can_reselect(self) -> None:
        with self.assertRaises(UpdateQueueError):
            plan_classifier_reselection(
                "postgresql://unused.invalid/db",
                classifier_version=RSS_LEGACY_CLASSIFIER_VERSION,
            )


class ReselectionLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        for migration in (
            recovery_fixture.OPS_FORWARD,
            recovery_fixture.CANDIDATE_FORWARD,
            recovery_fixture.QUEUE_FORWARD,
            recovery_fixture.RECOVERY_FORWARD,
            RESELECTION_FORWARD,
            RESELECTION_FORWARD,
        ):
            cls.pg.psql(file=migration)
        cls.pg.psql(command=cls.fixture_sql())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    @staticmethod
    def fixture_sql() -> str:
        items = (
            (SECTION_ITEM_ID, 0, "section-4-2", SECTION_4_2, "6"),
            (MATERIAL_ITEM_ID, 1, "material-1", SPECIAL_MATERIAL, "7"),
            (FAILED_ITEM_ID, 2, "failed-1", "公告修訂8.1.3.高單位免疫球蛋白之藥品給付規定。", "8"),
            (MALFORMED_ITEM_ID, 3, "malformed-1", "公告修訂5.6.1.抗骨質再吸收劑之給付規定。", "9"),
        )
        feed_items = ",\n".join(
            f"('{FEED_OBSERVATION_ID}', {index}, repeat('{digit}', 64), "
            f"'{guid}', '{title}', 'https://example.invalid/{guid}', NULL, '', "
            f"repeat('{digit}', 64))"
            for _, index, guid, title, digit in items
        )
        work_items = ",\n".join(
            f"('{item_id}', repeat('{chr(ord('a') + index)}', 64), 'rss_guid', '{guid}', "
            f"'https://example.invalid/feed.xml', '{guid}', '{FEED_OBSERVATION_ID}', "
            f"{index}, repeat('{digit}', 64), '{title}', "
            f"'https://example.invalid/{guid}', '2026-09-16 06:12:40+00')"
            for item_id, index, guid, title, digit in items
        )
        observations = ",\n".join(
            f"('{item_id}', '{FEED_OBSERVATION_ID}', {index}, "
            f"'2026-09-16 06:12:40+00', repeat('{digit}', 64))"
            for item_id, index, _, _, digit in items
        )
        ignored = (
            '{"classifier":"rss-item-keywords/v1","event":"ignored_non_rule",'
            f'"feed_observation_id":"{FEED_OBSERVATION_ID}"}}'
        )
        malformed = ignored.replace(FEED_OBSERVATION_ID, "-" * 36)
        return f"""
INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES
(
  '{POLL_JOB_ID}', repeat('1', 64), 'fixture/v1', 'fixture',
  'https://example.invalid/feed.xml', repeat('2', 64),
  '2026-09-16 06:10:00+00', '2026-09-16 06:14:00+00', '2026-09-16',
  '2026-09-16 06:12:00+00'
),
(
  '{V3_JOB_ID}', repeat('3', 64), 'fixture/v1', 'fixture',
  'https://example.invalid/feed.xml', repeat('4', 64),
  '2026-09-24 06:53:00+00', '2026-09-24 06:57:00+00', '2026-09-24',
  '2026-09-24 06:55:00+00'
);
INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at, max_runtime_seconds
) VALUES (
  '40000000-0000-0000-0000-000000000002', '{POLL_JOB_ID}', 'fixture',
  '2026-09-16 06:12:00+00', '2026-09-16 06:13:00+00', 60
);
INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES (
  repeat('5', 64), 10, 'application/rss+xml', 'polls/fixture/feed.xml',
  '2026-09-16 06:12:40+00'
);
INSERT INTO nhi_rule_history_update_ops.url_observation (
  url_observation_id, job_id, lease_id, owner_key, requested_url,
  final_url, observed_at, outcome, http_status, response_headers,
  response_headers_sha256, artifact_sha256, previous_artifact_sha256,
  relation_to_previous, error_code
) VALUES (
  '40000000-0000-0000-0000-000000000003', '{POLL_JOB_ID}',
  '40000000-0000-0000-0000-000000000002', 'fixture',
  'https://example.invalid/feed.xml', 'https://example.invalid/feed.xml',
  '2026-09-16 06:12:40+00', 'response', 200, '{{}}'::jsonb,
  repeat('9', 64), repeat('5', 64), NULL, 'first_observation', NULL
);
INSERT INTO nhi_rule_history_update_ops.feed_observation (
  feed_observation_id, job_id, url_observation_id, response_artifact_sha256,
  parser_version, parse_status, channel_title_raw, item_count,
  item_sequence_sha256, parsed_at, parse_error_code
) VALUES (
  '{FEED_OBSERVATION_ID}', '{POLL_JOB_ID}',
  '40000000-0000-0000-0000-000000000003', repeat('5', 64),
  'nhi-rule-history-rss/1.1.0', 'parsed', 'fixture', 4, repeat('0', 64),
  '2026-09-16 06:12:40+00', NULL
);
INSERT INTO nhi_rule_history_update_ops.feed_item_observation (
  feed_observation_id, item_index, item_fingerprint, guid_raw, title_raw,
  link_raw, published_raw, description_raw, raw_item_sha256
) VALUES
{feed_items};
INSERT INTO nhi_rule_history_update_queue.rss_work_item (
  work_item_id, rss_identity_fingerprint, item_identity_kind,
  item_identity_value, source_feed_url, guid_raw, first_feed_observation_id,
  first_item_index, first_item_fingerprint, first_title_raw, first_link_raw,
  first_observed_at
) VALUES
{work_items};
INSERT INTO nhi_rule_history_update_queue.rss_work_observation (
  work_item_id, feed_observation_id, item_index, observed_at, item_fingerprint
) VALUES
{observations};
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
('{SECTION_ITEM_ID}', 1, '41000000-0000-0000-0000-000000000001', NULL,
 'observed', 'deterministic_poll_loader', repeat('a', 64),
 '{{"event":"observed"}}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{SECTION_ITEM_ID}', 2, '41000000-0000-0000-0000-000000000002', 'observed',
 'ignored_non_rule', 'deterministic_poll_classifier', repeat('b', 64),
 '{ignored}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{MATERIAL_ITEM_ID}', 1, '41000000-0000-0000-0000-000000000003', NULL,
 'observed', 'deterministic_poll_loader', repeat('c', 64),
 '{{"event":"observed"}}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{MATERIAL_ITEM_ID}', 2, '41000000-0000-0000-0000-000000000004', 'observed',
 'ignored_non_rule', 'deterministic_poll_classifier', repeat('d', 64),
 '{ignored}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{FAILED_ITEM_ID}', 1, '41000000-0000-0000-0000-000000000005', NULL,
 'observed', 'deterministic_poll_loader', repeat('e', 64),
 '{{"event":"observed"}}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{FAILED_ITEM_ID}', 2, '41000000-0000-0000-0000-000000000006', 'observed',
 'selected', 'deterministic_poll_classifier', repeat('f', 64),
 '{{"event":"selected"}}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{FAILED_ITEM_ID}', 3, '41000000-0000-0000-0000-000000000007', 'selected',
 'failed_terminal', 'fixture', repeat('0', 64),
 '{{"event":"failed"}}', '{POLL_JOB_ID}', '2026-09-16 06:13:00+00'),
('{MALFORMED_ITEM_ID}', 1, '41000000-0000-0000-0000-000000000008', NULL,
 'observed', 'deterministic_poll_loader', repeat('1', 64),
 '{{"event":"observed"}}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00'),
('{MALFORMED_ITEM_ID}', 2, '41000000-0000-0000-0000-000000000009', 'observed',
 'ignored_non_rule', 'deterministic_poll_classifier', repeat('2', 64),
 '{malformed}', '{POLL_JOB_ID}', '2026-09-16 06:12:40+00');
"""

    def raw_reselection(self, work_item_id: str, seq: int, **overrides: str) -> str:
        evidence = {
            "decision": "classifier_version_reselection",
            "prior_classifier_version": RSS_V2_CLASSIFIER_VERSION,
            "classifier_version": RSS_CLASSIFIER_VERSION,
            "title_sha256": sha256_bytes(SPECIAL_MATERIAL.encode("utf-8")),
        }
        evidence.update(overrides)
        evidence_sql = "jsonb_build_object(" + ", ".join(
            f"'{key}', '{value}'" for key, value in evidence.items()
        ) + ")"
        actor = overrides.get("_actor", RESELECTION_ACTOR)
        result = self.pg.psql(
            command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES (
  '{work_item_id}', {seq}, gen_random_uuid(),
  (SELECT current_state FROM nhi_rule_history_update_queue.v_work_item_current
   WHERE work_item_id = '{work_item_id}'),
  'selected', '{actor}', repeat('1', 64), {evidence_sql} - '_actor',
  '{V3_JOB_ID}', '2026-09-24 07:00:00+00'
);""",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, "guard accepted a bad reselection")
        return result.stderr

    def current(self, work_item_id: str) -> str:
        return self.pg.psql(
            command=(
                "SELECT current_state || ':' || transition_seq FROM "
                "nhi_rule_history_update_queue.v_work_item_current "
                f"WHERE work_item_id = '{work_item_id}'"
            )
        ).stdout.strip()

    def test_01_plan_selects_only_the_clause_code_notice(self) -> None:
        plan = {
            entry["work_item_id"]: entry["decision"]
            for entry in plan_classifier_reselection(self.pg.dsn)
        }
        # The malformed deciding-observation id makes only that item's prior
        # classifier unknown; it neither aborts the plan nor borrows the
        # first observation's parser version.
        self.assertEqual(
            plan,
            {
                SECTION_ITEM_ID: "reselect",
                MATERIAL_ITEM_ID: "still_not_selected",
                MALFORMED_ITEM_ID: "unknown_prior_classifier",
            },
        )
        with self.assertRaisesRegex(UpdateQueueError, "unknown_prior_classifier"):
            reselect_ignored_work_item(
                self.pg.dsn,
                work_item_id=MALFORMED_ITEM_ID,
                source_job_id=V3_JOB_ID,
            )

    def test_02_guard_refuses_incomplete_or_foreign_reselection(self) -> None:
        material_title = sha256_bytes(SPECIAL_MATERIAL.encode("utf-8"))
        self.assertIn(
            "two distinct classifier versions",
            self.raw_reselection(
                MATERIAL_ITEM_ID, 3, prior_classifier_version=RSS_CLASSIFIER_VERSION
            ),
        )
        self.assertIn(
            "item title hash",
            self.raw_reselection(MATERIAL_ITEM_ID, 3, title_sha256="0" * 64),
        )
        self.assertIn(
            "its decision",
            self.raw_reselection(MATERIAL_ITEM_ID, 3, decision="manual"),
        )
        self.assertIn(
            "prevent silent retry",
            self.raw_reselection(
                MATERIAL_ITEM_ID, 3, _actor="operator", title_sha256=material_title
            ),
        )
        self.assertIn(
            "cannot recover failed work",
            self.raw_reselection(FAILED_ITEM_ID, 4),
        )
        with self.assertRaisesRegex(UpdateQueueError, "still_not_selected"):
            reselect_ignored_work_item(
                self.pg.dsn,
                work_item_id=MATERIAL_ITEM_ID,
                source_job_id=V3_JOB_ID,
            )
        self.assertEqual(self.current(MATERIAL_ITEM_ID), "ignored_non_rule:2")
        self.assertEqual(self.current(FAILED_ITEM_ID), "failed_terminal:3")

    def test_03_reselection_reopens_once_and_the_lane_continues(self) -> None:
        receipt = reselect_ignored_work_item(
            self.pg.dsn,
            work_item_id=SECTION_ITEM_ID,
            source_job_id=V3_JOB_ID,
            recorded_at="2026-09-24T07:00:00+00:00",
        )
        self.assertEqual(receipt["transition_seq"], 3)
        self.assertEqual(receipt["current_state"], "selected")
        self.assertEqual(receipt["evidence"]["prior_classifier_version"], RSS_V2_CLASSIFIER_VERSION)
        self.assertEqual(receipt["evidence"]["classifier_version"], RSS_CLASSIFIER_VERSION)
        self.assertEqual(self.current(SECTION_ITEM_ID), "selected:3")
        with self.assertRaisesRegex(UpdateQueueError, "not currently ignored"):
            reselect_ignored_work_item(
                self.pg.dsn,
                work_item_id=SECTION_ITEM_ID,
                source_job_id=V3_JOB_ID,
            )
        # The runner may still close it again (for instance a later
        # reclassification); the same classifier version cannot reopen it.
        append_work_transition(
            self.pg.dsn,
            work_item_id=SECTION_ITEM_ID,
            to_state="ignored_non_rule",
            actor_kind="fixture_reclassifier",
            evidence={"classifier": RSS_V2_CLASSIFIER_VERSION, "event": "ignored"},
            source_job_id=V3_JOB_ID,
            recorded_at="2026-09-24T07:01:00+00:00",
        )
        with self.assertRaisesRegex(UpdateQueueError, "already_reselected"):
            reselect_ignored_work_item(
                self.pg.dsn,
                work_item_id=SECTION_ITEM_ID,
                source_job_id=V3_JOB_ID,
            )
        self.assertIn(
            "already reselected",
            self.raw_reselection(
                SECTION_ITEM_ID,
                5,
                title_sha256=sha256_bytes(SECTION_4_2.encode("utf-8")),
            ),
        )
        self.assertEqual(self.current(SECTION_ITEM_ID), "ignored_non_rule:4")

    def test_04_rollback_restores_the_terminal_refusal(self) -> None:
        self.pg.psql(file=RESELECTION_ROLLBACK)
        try:
            self.assertIn(
                "prevent silent retry",
                self.raw_reselection(
                    MATERIAL_ITEM_ID,
                    3,
                    title_sha256=sha256_bytes(SPECIAL_MATERIAL.encode("utf-8")),
                ),
            )
            self.assertEqual(
                self.pg.psql(
                    command=(
                        "SELECT to_regclass('nhi_rule_history_update_queue."
                        "reselection_schema_migration') IS NULL"
                    )
                ).stdout.strip(),
                "t",
            )
        finally:
            self.pg.psql(file=RESELECTION_FORWARD)


if __name__ == "__main__":
    unittest.main()
