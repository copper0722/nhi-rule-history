from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


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
QUEUE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_update_queue.sql"
)
QUEUE_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_queue.rollback.sql"
)


def sql_code(path: Path) -> str:
    return re.sub(
        r"--.*?$",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


class UpdateQueueMigrationStaticTests(unittest.TestCase):
    def test_migration_is_transactional_idempotent_and_managed(self) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn("managed=nhi_rule_history_update_queue/v1", sql)
        self.assertIn("schema_migration", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS", sql)
        self.assertIn("CREATE OR REPLACE FUNCTION", sql)
        self.assertIn("ON CONFLICT (migration_id) DO NOTHING", sql)

    def test_one_exact_rss_identity_and_reobservations_are_normalized(self) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS nhi_rule_history_update_queue.rss_work_item",
            sql,
        )
        self.assertIn(
            "UNIQUE (source_feed_url, item_identity_kind, item_identity_value)",
            sql,
        )
        self.assertIn("rss_identity_fingerprint", sql)
        self.assertIn("item_identity_kind", sql)
        self.assertIn("'rss_guid'", sql)
        self.assertIn("'official_detail_url'", sql)
        self.assertIn("first_feed_observation_id", sql)
        self.assertIn("first_item_fingerprint", sql)
        self.assertIn("first_title_raw", sql)
        self.assertIn("first_link_raw", sql)
        self.assertIn("first_observed_at", sql)
        self.assertIn("rss_work_observation", sql)
        self.assertIn(
            "UNIQUE (feed_observation_id, item_index)",
            sql,
        )
        self.assertIn("rss_work_item_identity_chk", sql)

    def test_state_machine_is_append_only_gap_free_and_terminal(self) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        for state in (
            "observed",
            "selected",
            "acquired",
            "corpus_registered",
            "proposal_running",
            "staged_needs_review",
            "staged_pending_anchor",
            "failed_terminal",
            "ignored_non_rule",
        ):
            self.assertIn(f"'{state}'", sql)
        self.assertIn("prior_seq + 1", sql)
        self.assertIn("terminal work-item states prevent silent retry", sql)
        self.assertIn("work-item transition edge is not allowed", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("BEFORE TRUNCATE", sql)
        self.assertIn("bundle_receipt_id", sql)
        self.assertIn("candidate_proposal_id", sql)
        self.assertIn("evidence_sha256", sql)
        self.assertIn("evidence_json", sql)
        self.assertIn(
            "pre-staging queue states cannot claim update bundle",
            sql,
        )
        self.assertIn(
            "staged states require matching bundle and candidate identifiers",
            sql,
        )
        self.assertIn(
            "terminal failure candidate identifier requires its bundle receipt",
            sql,
        )
        self.assertRegex(
            sql,
            r"prior_state = 'selected'\s+AND NEW\.to_state IN \(\s*"
            r"'acquired', 'failed_terminal', 'ignored_non_rule'",
        )

    def test_nonterminal_attempt_ledger_is_append_only_and_stage_scoped(
        self,
    ) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        self.assertIn(
            "nhi_rule_history_update_queue.work_item_attempt", sql
        )
        self.assertIn("attempt_fingerprint", sql)
        self.assertIn("'transient_failure'", sql)
        self.assertIn("sanitization_profile", sql)
        self.assertIn(
            "nhi-rule-history/attempt-evidence-sanitization/v1", sql
        )
        self.assertIn("guard_work_attempt_insert", sql)
        self.assertIn(
            "work attempt does not match the current append-only state", sql
        )
        self.assertIn(
            "terminal work-item states reject new operational attempts", sql
        )
        self.assertIn(
            "GRANT SELECT, INSERT ON", sql
        )
        self.assertNotIn(
            "GRANT UPDATE ON\n  nhi_rule_history_update_queue.work_item_attempt",
            sql,
        )
        rollback = QUEUE_ROLLBACK.read_text(encoding="utf-8")
        self.assertIn(
            "DROP TABLE IF EXISTS\n"
            "  nhi_rule_history_update_queue.work_item_attempt RESTRICT",
            rollback,
        )

    def test_current_and_backlog_views_are_per_item(self) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        self.assertIn("v_work_item_current", sql)
        self.assertIn("v_work_backlog", sql)
        self.assertIn(
            "WHERE transition.work_item_id = item.work_item_id",
            sql,
        )
        self.assertIn(
            "Observing or completing one item never advances its siblings".lower(),
            sql.lower(),
        )

    def test_runtime_role_is_nologin_and_stage_scoped(self) -> None:
        sql = QUEUE_FORWARD.read_text(encoding="utf-8")
        self.assertRegex(
            sql,
            r"CREATE ROLE nhi_rule_history_update_queue_runtime"
            r"\s+NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE",
        )
        self.assertIn(
            "REVOKE ALL ON SCHEMA nhi_rule_history_update_queue FROM PUBLIC",
            sql,
        )
        grant_lines = "\n".join(
            line.strip()
            for line in sql.splitlines()
            if line.lstrip().startswith("GRANT ")
        )
        self.assertNotRegex(grant_lines, r"\btw_drug\b")
        self.assertNotRegex(grant_lines, r"\bnhi_rule_history\.")
        self.assertNotIn("CREATE ON SCHEMA", grant_lines)

    def test_rollback_is_guarded_and_never_cascades(self) -> None:
        sql = QUEUE_ROLLBACK.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn("managed=nhi_rule_history_update_queue/v1", sql)
        self.assertNotRegex(sql_code(QUEUE_ROLLBACK), r"(?i)\bCASCADE\b")
        self.assertIn(
            "DROP SCHEMA IF EXISTS nhi_rule_history_update_queue RESTRICT",
            sql,
        )


class UpdateQueueMigrationLiveTests(unittest.TestCase):
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
        expect_success: bool = True,
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
        if expect_success and result.returncode != 0:
            raise AssertionError(
                f"psql failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    def test_idempotent_apply_guards_views_and_rollback(self) -> None:
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
        applied_ops = applied_candidate = applied_queue = False
        try:
            self.run_psql(file=OPS_FORWARD)
            applied_ops = True
            self.run_psql(file=CANDIDATE_FORWARD)
            applied_candidate = True
            self.run_psql(file=QUEUE_FORWARD)
            applied_queue = True
            self.run_psql(file=QUEUE_FORWARD)

            result = self.run_psql(
                command=r"""
SET ROLE nhi_rule_history_update_queue_runtime;
INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES (
  '10000000-0000-0000-0000-000000000001',
  repeat('1', 64), 'test/v1', 'test',
  'https://example.invalid/feed.xml', repeat('2', 64),
  '2026-07-27 00:00:00+00', '2026-07-27 00:02:00+00',
  '2026-07-27', '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at,
  max_runtime_seconds
) VALUES (
  '10000000-0000-0000-0000-000000000002',
  '10000000-0000-0000-0000-000000000001',
  'test', '2026-07-27 00:01:00+00',
  '2026-07-27 00:01:10+00', 10
);
INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES (
  repeat('3', 64), 10, 'application/rss+xml',
  'polls/test/feed.xml', '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_ops.url_observation (
  url_observation_id, job_id, lease_id, owner_key, requested_url,
  final_url, observed_at, outcome, http_status, response_headers,
  response_headers_sha256, artifact_sha256,
  previous_artifact_sha256, relation_to_previous, error_code
) VALUES (
  '10000000-0000-0000-0000-000000000003',
  '10000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000002',
  'test', 'https://example.invalid/feed.xml',
  'https://example.invalid/feed.xml', '2026-07-27 00:01:00+00',
  'response', 200, '{}'::jsonb, repeat('4', 64),
  repeat('3', 64), NULL, 'first_observation', NULL
);
INSERT INTO nhi_rule_history_update_ops.feed_observation (
  feed_observation_id, job_id, url_observation_id,
  response_artifact_sha256, parser_version, parse_status,
  channel_title_raw, item_count, item_sequence_sha256, parsed_at,
  parse_error_code
) VALUES (
  '10000000-0000-0000-0000-000000000004',
  '10000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000003',
  repeat('3', 64), 'test/v1', 'parsed', 'test', 2,
  repeat('5', 64), '2026-07-27 00:01:00+00', NULL
);
INSERT INTO nhi_rule_history_update_ops.feed_item_observation (
  feed_observation_id, item_index, item_fingerprint, guid_raw,
  title_raw, link_raw, published_raw, description_raw, raw_item_sha256
) VALUES
(
  '10000000-0000-0000-0000-000000000004', 0, repeat('6', 64),
  'guid-1', 'rule 1', 'https://example.invalid/item-1', NULL, '',
  repeat('6', 64)
),
(
  '10000000-0000-0000-0000-000000000004', 1, repeat('7', 64),
  'guid-2', 'rule 2', 'https://example.invalid/item-2', NULL, '',
  repeat('7', 64)
);
INSERT INTO nhi_rule_history_update_queue.rss_work_item (
  work_item_id, rss_identity_fingerprint,
  item_identity_kind, item_identity_value,
  source_feed_url, guid_raw,
  first_feed_observation_id, first_item_index, first_item_fingerprint,
  first_title_raw, first_link_raw, first_observed_at
) VALUES
(
  '10000000-0000-0000-0000-000000000010', repeat('8', 64),
  'rss_guid', 'guid-1',
  'https://example.invalid/feed.xml', 'guid-1',
  '10000000-0000-0000-0000-000000000004', 0, repeat('6', 64),
  'rule 1', 'https://example.invalid/item-1',
  '2026-07-27 00:01:00+00'
),
(
  '10000000-0000-0000-0000-000000000011', repeat('9', 64),
  'rss_guid', 'guid-2',
  'https://example.invalid/feed.xml', 'guid-2',
  '10000000-0000-0000-0000-000000000004', 1, repeat('7', 64),
  'rule 2', 'https://example.invalid/item-2',
  '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_queue.rss_work_observation (
  work_item_id, feed_observation_id, item_index, observed_at,
  item_fingerprint
) VALUES
(
  '10000000-0000-0000-0000-000000000010',
  '10000000-0000-0000-0000-000000000004', 0,
  '2026-07-27 00:01:00+00', repeat('6', 64)
),
(
  '10000000-0000-0000-0000-000000000011',
  '10000000-0000-0000-0000-000000000004', 1,
  '2026-07-27 00:01:00+00', repeat('7', 64)
);
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
(
  '10000000-0000-0000-0000-000000000010', 1,
  '10000000-0000-0000-0000-000000000020', NULL, 'observed',
  'test', repeat('a', 64), '{"event":"observed"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
),
(
  '10000000-0000-0000-0000-000000000010', 2,
  '10000000-0000-0000-0000-000000000021', 'observed', 'selected',
  'test', repeat('b', 64), '{"event":"selected"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
),
(
  '10000000-0000-0000-0000-000000000011', 1,
  '10000000-0000-0000-0000-000000000022', NULL, 'observed',
  'test', repeat('c', 64), '{"event":"observed"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
),
(
  '10000000-0000-0000-0000-000000000011', 2,
  '10000000-0000-0000-0000-000000000023', 'observed', 'selected',
  'test', repeat('d', 64), '{"event":"selected"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_queue.work_item_attempt (
  attempt_id, work_item_id, attempt_fingerprint, attempt_kind, outcome,
  work_state_at_attempt, actor_kind, sanitization_profile,
  evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES (
  '10000000-0000-0000-0000-000000000040',
  '10000000-0000-0000-0000-000000000010',
  repeat('0', 64), 'acquisition', 'transient_failure', 'selected',
  'test', 'nhi-rule-history/attempt-evidence-sanitization/v1',
  repeat('1', 64), '{"error_code":"transport_timeout"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00.5+00'
);
RESET ROLE;

INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
(
  '10000000-0000-0000-0000-000000000011', 3,
  '10000000-0000-0000-0000-000000000030',
  'selected', 'acquired', 'test', repeat('2', 64),
  '{"event":"acquired"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:01+00'
),
(
  '10000000-0000-0000-0000-000000000011', 4,
  '10000000-0000-0000-0000-000000000031',
  'acquired', 'corpus_registered', 'test', repeat('3', 64),
  '{"event":"corpus-registered","external_receipt":"fixture"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:02+00'
),
(
  '10000000-0000-0000-0000-000000000011', 5,
  '10000000-0000-0000-0000-000000000032',
  'corpus_registered', 'proposal_running', 'test', repeat('4', 64),
  '{"event":"proposal-running"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:03+00'
);

DO $expected_staged_identifier_rejection$
BEGIN
  BEGIN
    INSERT INTO nhi_rule_history_update_queue.work_item_transition (
      work_item_id, transition_seq, transition_id, from_state, to_state,
      actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
    ) VALUES (
      '10000000-0000-0000-0000-000000000011', 6,
      '10000000-0000-0000-0000-000000000033',
      'proposal_running', 'staged_needs_review', 'test', repeat('5', 64),
      '{"event":"missing-stage-identifiers"}',
      '10000000-0000-0000-0000-000000000001',
      '2026-07-27 00:01:04+00'
    );
    RAISE EXCEPTION 'staged transition without identifiers succeeded';
  EXCEPTION WHEN integrity_constraint_violation THEN
    NULL;
  END;
END;
$expected_staged_identifier_rejection$;

DO $expected_gap_rejection$
BEGIN
  BEGIN
    INSERT INTO nhi_rule_history_update_queue.work_item_transition (
      work_item_id, transition_seq, transition_id, from_state, to_state,
      actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
    ) VALUES (
      '10000000-0000-0000-0000-000000000010', 4,
      '10000000-0000-0000-0000-000000000024',
      'selected', 'acquired', 'test', repeat('e', 64),
      '{"event":"bad-gap"}',
      '10000000-0000-0000-0000-000000000001',
      '2026-07-27 00:01:01+00'
    );
    RAISE EXCEPTION 'gap insertion unexpectedly succeeded';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    NULL;
  END;
END;
$expected_gap_rejection$;

INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
(
  '10000000-0000-0000-0000-000000000010', 3,
  '10000000-0000-0000-0000-000000000025',
  'selected', 'ignored_non_rule', 'test', repeat('f', 64),
  '{"event":"deterministic-reclassification"}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:01+00'
);

DO $expected_terminal_rejection$
BEGIN
  BEGIN
    INSERT INTO nhi_rule_history_update_queue.work_item_transition (
      work_item_id, transition_seq, transition_id, from_state, to_state,
      actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
    ) VALUES (
      '10000000-0000-0000-0000-000000000010', 4,
      '10000000-0000-0000-0000-000000000026',
      'ignored_non_rule', 'selected', 'test', repeat('1', 64),
      '{"event":"silent-retry"}',
      '10000000-0000-0000-0000-000000000001',
      '2026-07-27 00:01:02+00'
    );
    RAISE EXCEPTION 'terminal retry unexpectedly succeeded';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    NULL;
  END;
END;
$expected_terminal_rejection$;

DO $expected_mutation_rejection$
BEGIN
  BEGIN
    UPDATE nhi_rule_history_update_queue.rss_work_item
    SET first_title_raw = 'changed'
    WHERE guid_raw = 'guid-1';
    RAISE EXCEPTION 'append-only update unexpectedly succeeded';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    NULL;
  END;
END;
$expected_mutation_rejection$;

DO $expected_attempt_mutation_rejection$
BEGIN
  BEGIN
    UPDATE nhi_rule_history_update_queue.work_item_attempt
    SET outcome = 'success'
    WHERE attempt_id = '10000000-0000-0000-0000-000000000040';
    RAISE EXCEPTION 'attempt ledger update unexpectedly succeeded';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    NULL;
  END;
END;
$expected_attempt_mutation_rejection$;

SELECT
  (SELECT count(*) FROM nhi_rule_history_update_queue.v_work_backlog),
  (SELECT current_state
   FROM nhi_rule_history_update_queue.v_work_item_current
   WHERE guid_raw = 'guid-1'),
  (SELECT current_state
   FROM nhi_rule_history_update_queue.v_work_item_current
   WHERE guid_raw = 'guid-2');
"""
            )
            self.assertEqual(
                result.stdout.strip().splitlines()[-1],
                "1|ignored_non_rule|proposal_running",
            )

            role_check = self.run_psql(
                command="""
SELECT count(*)
FROM information_schema.role_table_grants
WHERE grantee = 'nhi_rule_history_update_queue_runtime'
  AND table_schema NOT IN (
    'nhi_rule_history_update_ops',
    'nhi_rule_history_candidate_stage',
    'nhi_rule_history_update_queue'
  );
"""
            ).stdout.strip()
            self.assertEqual(role_check, "0")
        finally:
            if applied_queue:
                self.run_psql(file=QUEUE_ROLLBACK)
            if applied_candidate:
                self.run_psql(file=CANDIDATE_ROLLBACK)
            if applied_ops:
                self.run_psql(file=OPS_ROLLBACK)
