from __future__ import annotations

import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import canonical_json_bytes, sha256_bytes
from nhi_rule_history.update.pg_queue import (
    UpdateQueueError,
    advance_work_recovery,
    append_work_transition,
    authorize_work_recovery,
    register_work_recovery_attempt,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
CANDIDATE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.sql"
)
QUEUE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_update_queue.sql"
)
RECOVERY_FORWARD = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_update_queue_recovery_v2.sql"
)
RECOVERY_ROLLBACK = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_update_queue_recovery_v2.rollback.sql"
)

WORK_ITEM_ID = "10000000-0000-0000-0000-000000000010"
PARTITION_WORK_ITEM_ID = "10000000-0000-0000-0000-000000000011"
OLD_PRIMARY_ID = "20000000-0000-0000-0000-000000000010"
OLD_FALLBACK_ID = "20000000-0000-0000-0000-000000000011"
NEW_JOB_ID = "30000000-0000-0000-0000-000000000001"
NEW_PRIMARY_ID = "30000000-0000-0000-0000-000000000010"
NEW_FALLBACK_ID = "30000000-0000-0000-0000-000000000011"
OLD_PROMPT = "a" * 64
NEW_PROMPT = "b" * 64
THIRD_PROMPT = "c" * 64
SOURCE_MANIFEST = "d" * 64


def sql_code(path: Path) -> str:
    return re.sub(
        r"--.*?$",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


class RecoveryMigrationStaticTests(unittest.TestCase):
    def test_migration_is_additive_stage_only_and_managed(self) -> None:
        sql = RECOVERY_FORWARD.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn(
            "managed=nhi_rule_history_update_queue/recovery-v2", sql
        )
        self.assertIn("work_recovery_authorization", sql)
        self.assertIn("recovery_superseded_attempt", sql)
        self.assertIn("work_generation_transition", sql)
        self.assertIn("recovery_route_attempt", sql)
        self.assertNotRegex(sql, r"\bUPDATE\s+.*work_item_transition")
        self.assertNotRegex(sql, r"\bDELETE\s+FROM\b")
        self.assertNotRegex(sql, r"\b(?:hmj|hm4|cm1)\b")
        self.assertNotRegex(sql, r"\btw_drug\.")

    def test_authorization_binds_every_material_recovery_input(self) -> None:
        sql = RECOVERY_FORWARD.read_text(encoding="utf-8")
        for field in (
            "source_bundle_uid",
            "source_manifest_sha256",
            "prior_generation",
            "new_generation",
            "prior_method_version",
            "new_method_version",
            "prior_semantic_prompt_fingerprint",
            "new_semantic_prompt_fingerprint",
            "decision_basis_id",
            "reason",
            "route",
            "superseded_attempt",
        ):
            self.assertIn(field, sql)
        self.assertIn(
            "method/prompt is unchanged",
            sql,
        )
        self.assertIn(
            "one active recovery generation is allowed", sql
        )

    def test_routes_are_bounded_and_second_failure_does_not_requeue(self) -> None:
        sql = RECOVERY_FORWARD.read_text(encoding="utf-8")
        self.assertIn(
            "PRIMARY KEY (work_item_id, generation, route)", sql
        )
        self.assertIn("route IN ('primary', 'fallback')", sql)
        self.assertIn(
            "fallback recovery route requires the failed linked primary", sql
        )
        self.assertIn(
            "failed recovery requires one failed primary and one failed fallback",
            sql,
        )
        route_function = sql.split(
            "register_recovery_route_attempt(", 1
        )[1].split(
            "DROP TRIGGER IF EXISTS work_generation_insert_guard", 1
        )[0]
        self.assertNotIn("work_generation (", route_function)
        self.assertNotIn("retry_pending", route_function)

    def test_partition_required_is_terminal_and_generic_retry_is_blocked(
        self,
    ) -> None:
        sql = RECOVERY_FORWARD.read_text(encoding="utf-8")
        self.assertIn("'partition_required'", sql)
        self.assertIn(
            "partition_required is a terminal fail-closed state", sql
        )
        self.assertRegex(
            sql,
            r"current_transition\.to_state IN \(\s*"
            r"'staged_needs_review',\s*'staged_pending_anchor',\s*"
            r"'failed_terminal',\s*'partition_required'",
        )
        self.assertIn(
            "generic transition path cannot recover failed work", sql
        )

    def test_runtime_has_function_capability_but_no_direct_insert(self) -> None:
        sql = RECOVERY_FORWARD.read_text(encoding="utf-8")
        self.assertEqual(sql.count("SECURITY DEFINER"), 5)
        self.assertGreaterEqual(sql.count("SET search_path = pg_catalog"), 7)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION\n"
            "  nhi_rule_history_update_queue.authorize_failed_work_recovery",
            sql,
        )
        grants = "\n".join(
            line.strip()
            for line in sql.splitlines()
            if line.lstrip().startswith("GRANT ")
        )
        self.assertNotIn("GRANT INSERT", grants)

    def test_rollback_is_empty_only_and_never_cascades(self) -> None:
        sql = RECOVERY_ROLLBACK.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn("ledger is nonempty", sql)
        self.assertIn("IN ACCESS EXCLUSIVE MODE", sql)
        self.assertNotRegex(sql_code(RECOVERY_ROLLBACK), r"(?i)\bCASCADE\b")
        self.assertIn(
            "Restore the exact v1 generic transition guard", sql
        )


class RecoveryApiValidationTests(unittest.TestCase):
    def test_same_method_and_prompt_is_rejected_before_database_access(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            UpdateQueueError, "change the method or semantic prompt"
        ):
            authorize_work_recovery(
                "postgresql://example.invalid/test",
                work_item_id=WORK_ITEM_ID,
                prior_generation=1,
                source_bundle_uid="fixture-bundle",
                source_manifest_sha256=SOURCE_MANIFEST,
                prior_method_version="method-v1",
                new_method_version="method-v1",
                prior_semantic_prompt_fingerprint=OLD_PROMPT,
                new_semantic_prompt_fingerprint=OLD_PROMPT,
                superseded_attempt_ids=[OLD_PRIMARY_ID],
                decision_basis_id="decision-1",
                reason="same inputs",
                actor_kind="fixture",
            )

    def test_duplicate_or_empty_superseded_attempts_are_rejected(self) -> None:
        common = {
            "conninfo": "postgresql://example.invalid/test",
            "work_item_id": WORK_ITEM_ID,
            "prior_generation": 1,
            "source_bundle_uid": "fixture-bundle",
            "source_manifest_sha256": SOURCE_MANIFEST,
            "prior_method_version": "method-v1",
            "new_method_version": "method-v2",
            "prior_semantic_prompt_fingerprint": OLD_PROMPT,
            "new_semantic_prompt_fingerprint": NEW_PROMPT,
            "decision_basis_id": "decision-1",
            "reason": "changed bounded method",
            "actor_kind": "fixture",
        }
        for attempts in ([], [OLD_PRIMARY_ID, OLD_PRIMARY_ID]):
            with self.subTest(attempts=attempts), self.assertRaisesRegex(
                UpdateQueueError, "distinct superseded"
            ):
                authorize_work_recovery(
                    superseded_attempt_ids=attempts, **common
                )


class DisposablePostgres:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nhi-queue-recovery-pg-"
        )
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.socket_dir = self.root / "socket"
        self.socket_dir.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.initdb = shutil.which("initdb")
        self.pg_ctl = shutil.which("pg_ctl")
        self.createdb = shutil.which("createdb")
        self.psql_bin = shutil.which("psql")
        if not all(
            (self.initdb, self.pg_ctl, self.createdb, self.psql_bin)
        ):
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "disposable PostgreSQL command-line tools are unavailable"
            )
        initialized = subprocess.run(
            [
                self.initdb,
                "-D",
                str(self.data),
                "--auth=trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if initialized.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot initialize disposable PostgreSQL: "
                + initialized.stderr
            )
        started = subprocess.run(
            [
                self.pg_ctl,
                "-D",
                str(self.data),
                "-l",
                str(self.root / "postgres.log"),
                "-o",
                f"-F -k {self.socket_dir} -p {self.port}",
                "-w",
                "start",
            ],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if started.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot start disposable PostgreSQL: " + started.stderr
            )
        self.running = True
        created = subprocess.run(
            [
                self.createdb,
                "-h",
                str(self.socket_dir),
                "-p",
                str(self.port),
                "nhi_recovery",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if created.returncode != 0:
            self.close()
            raise unittest.SkipTest(
                "cannot create disposable PostgreSQL database: "
                + created.stderr
            )
        self.dsn = (
            f"postgresql:///{'nhi_recovery'}?"
            f"host={self.socket_dir}&port={self.port}"
        )

    def close(self) -> None:
        if getattr(self, "running", False):
            subprocess.run(
                [
                    self.pg_ctl,
                    "-D",
                    str(self.data),
                    "-m",
                    "fast",
                    "-w",
                    "stop",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.running = False
        self.temporary.cleanup()

    def psql(
        self,
        *,
        file: Path | None = None,
        command: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            self.psql_bin,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "-h",
            str(self.socket_dir),
            "-p",
            str(self.port),
            "-d",
            "nhi_recovery",
        ]
        if file is not None:
            argv.extend(["--file", str(file)])
        if command is not None:
            argv.extend(["--command", command])
        result = subprocess.run(
            argv, check=False, text=True, capture_output=True
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"psql failed ({result.returncode}):\n{result.stderr}"
            )
        return result


class RecoveryMigrationLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = DisposablePostgres()
        for migration in (
            OPS_FORWARD,
            CANDIDATE_FORWARD,
            QUEUE_FORWARD,
            RECOVERY_FORWARD,
            RECOVERY_FORWARD,
        ):
            cls.pg.psql(file=migration)
        cls.pg.psql(file=RECOVERY_ROLLBACK)
        cls.pg.psql(file=RECOVERY_FORWARD)
        cls.pg.psql(file=RECOVERY_FORWARD)
        cls.pg.psql(command=cls.fixture_sql())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    @staticmethod
    def fixture_sql() -> str:
        return f"""
INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES
(
  '10000000-0000-0000-0000-000000000001', repeat('1',64),
  'fixture/v1', 'fixture', 'https://example.invalid/feed.xml',
  repeat('2',64), '2026-07-27 00:00:00+00',
  '2026-07-27 00:02:00+00', '2026-07-27',
  '2026-07-27 00:01:00+00'
),
(
  '20000000-0000-0000-0000-000000000001', repeat('3',64),
  'fixture/v1', 'fixture', 'https://example.invalid/old-worker',
  repeat('4',64), '2026-07-27 00:09:00+00',
  '2026-07-27 00:13:00+00', '2026-07-27',
  '2026-07-27 00:10:00+00'
),
(
  '{NEW_JOB_ID}', repeat('5',64),
  'fixture/v2', 'fixture', 'https://example.invalid/new-worker',
  repeat('6',64), '2026-07-28 00:00:00+00',
  '2026-07-28 00:04:00+00', '2026-07-28',
  '2026-07-28 00:01:00+00'
);

INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at,
  max_runtime_seconds
) VALUES
(
  '10000000-0000-0000-0000-000000000002',
  '10000000-0000-0000-0000-000000000001', 'fixture',
  '2026-07-27 00:00:00+00', '2026-07-27 00:02:00+00', 120
),
(
  '20000000-0000-0000-0000-000000000002',
  '20000000-0000-0000-0000-000000000001', 'fixture',
  '2026-07-27 00:09:00+00', '2026-07-27 00:12:00+00', 180
),
(
  '30000000-0000-0000-0000-000000000002',
  '{NEW_JOB_ID}', 'fixture',
  '2026-07-28 00:00:00+00', '2026-07-28 00:03:00+00', 180
);

INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES (
  repeat('7',64), 10, 'application/rss+xml',
  'polls/fixture/feed.xml', '2026-07-27 00:01:00+00'
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
  'fixture', 'https://example.invalid/feed.xml',
  'https://example.invalid/feed.xml', '2026-07-27 00:01:00+00',
  'response', 200, '{{}}'::jsonb, repeat('8',64), repeat('7',64),
  NULL, 'first_observation', NULL
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
  repeat('7',64), 'fixture/v1', 'parsed', 'fixture', 2,
  repeat('9',64), '2026-07-27 00:01:00+00', NULL
);
INSERT INTO nhi_rule_history_update_ops.feed_item_observation (
  feed_observation_id, item_index, item_fingerprint, guid_raw,
  title_raw, link_raw, published_raw, description_raw, raw_item_sha256
) VALUES
(
  '10000000-0000-0000-0000-000000000004', 0, repeat('a',64),
  'fixture-guid', 'fixture rule',
  'https://example.invalid/rule', NULL, '', repeat('b',64)
),
(
  '10000000-0000-0000-0000-000000000004', 1, repeat('d',64),
  'partition-guid', 'partition fixture rule',
  'https://example.invalid/partition-rule', NULL, '', repeat('e',64)
);
INSERT INTO nhi_rule_history_update_queue.rss_work_item (
  work_item_id, rss_identity_fingerprint, item_identity_kind,
  item_identity_value, source_feed_url, guid_raw,
  first_feed_observation_id, first_item_index, first_item_fingerprint,
  first_title_raw, first_link_raw, first_observed_at
) VALUES
(
  '{WORK_ITEM_ID}', repeat('c',64), 'rss_guid', 'fixture-guid',
  'https://example.invalid/feed.xml', 'fixture-guid',
  '10000000-0000-0000-0000-000000000004', 0, repeat('a',64),
  'fixture rule', 'https://example.invalid/rule',
  '2026-07-27 00:01:00+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', repeat('d',64), 'rss_guid',
  'partition-guid', 'https://example.invalid/feed.xml', 'partition-guid',
  '10000000-0000-0000-0000-000000000004', 1, repeat('d',64),
  'partition fixture rule', 'https://example.invalid/partition-rule',
  '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
(
  '{WORK_ITEM_ID}', 1, '10000000-0000-0000-0000-000000000020',
  NULL, 'observed', 'fixture', repeat('1',64), '{{"event":"observed"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
),
(
  '{WORK_ITEM_ID}', 2, '10000000-0000-0000-0000-000000000021',
  'observed', 'selected', 'fixture', repeat('2',64),
  '{{"event":"selected"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:01+00'
),
(
  '{WORK_ITEM_ID}', 3, '10000000-0000-0000-0000-000000000022',
  'selected', 'acquired', 'fixture', repeat('3',64),
  '{{"event":"acquired"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:02+00'
),
(
  '{WORK_ITEM_ID}', 4, '10000000-0000-0000-0000-000000000023',
  'acquired', 'corpus_registered', 'fixture', repeat('4',64),
  '{{"event":"corpus"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:03+00'
),
(
  '{WORK_ITEM_ID}', 5, '10000000-0000-0000-0000-000000000024',
  'corpus_registered', 'proposal_running', 'fixture', repeat('5',64),
  '{{"event":"proposal"}}',
  '20000000-0000-0000-0000-000000000001',
  '2026-07-27 00:10:00+00'
),
(
  '{WORK_ITEM_ID}', 6, '10000000-0000-0000-0000-000000000025',
  'proposal_running', 'failed_terminal', 'fixture', repeat('6',64),
  '{{"event":"failed"}}',
  '20000000-0000-0000-0000-000000000001',
  '2026-07-27 00:10:03+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', 1,
  '11000000-0000-0000-0000-000000000020',
  NULL, 'observed', 'fixture', repeat('7',64), '{{"event":"observed"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:00+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', 2,
  '11000000-0000-0000-0000-000000000021',
  'observed', 'selected', 'fixture', repeat('8',64),
  '{{"event":"selected"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:01+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', 3,
  '11000000-0000-0000-0000-000000000022',
  'selected', 'acquired', 'fixture', repeat('9',64),
  '{{"event":"acquired"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:02+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', 4,
  '11000000-0000-0000-0000-000000000023',
  'acquired', 'corpus_registered', 'fixture', repeat('a',64),
  '{{"event":"corpus"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:03+00'
),
(
  '{PARTITION_WORK_ITEM_ID}', 5,
  '11000000-0000-0000-0000-000000000024',
  'corpus_registered', 'proposal_running', 'fixture', repeat('b',64),
  '{{"event":"proposal"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:04+00'
);

INSERT INTO nhi_rule_history_update_ops.worker_attempt (
  attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
  primary_attempt_id, provider, runtime, model, prompt_sha256,
  output_sha256, started_at, completed_at, status, failure_code,
  fallback_reason
) VALUES
(
  '{OLD_PRIMARY_ID}', '20000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000002', 'fixture', 1, 'primary',
  NULL, 'fixture', 'fixture', 'fixture', '{OLD_PROMPT}', NULL,
  '2026-07-27 00:10:00+00', '2026-07-27 00:10:01+00',
  'failed', 'timeout', NULL
),
(
  '{OLD_FALLBACK_ID}', '20000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000002', 'fixture', 2, 'fallback',
  '{OLD_PRIMARY_ID}', 'fixture', 'fixture', 'fixture', '{OLD_PROMPT}',
  NULL, '2026-07-27 00:10:01+00', '2026-07-27 00:10:02+00',
  'failed', 'timeout', 'primary_failed'
),
(
  '{NEW_PRIMARY_ID}', '{NEW_JOB_ID}',
  '30000000-0000-0000-0000-000000000002', 'fixture', 1, 'primary',
  NULL, 'fixture', 'fixture', 'fixture', '{NEW_PROMPT}', NULL,
  '2026-07-28 00:00:02+00', '2026-07-28 00:00:02.4+00',
  'failed', 'timeout', NULL
),
(
  '{NEW_FALLBACK_ID}', '{NEW_JOB_ID}',
  '30000000-0000-0000-0000-000000000002', 'fixture', 2, 'fallback',
  '{NEW_PRIMARY_ID}', 'fixture', 'fixture', 'fixture', '{NEW_PROMPT}',
  NULL, '2026-07-28 00:00:02.5+00', '2026-07-28 00:00:02.9+00',
  'failed', 'timeout', 'primary_failed'
);
"""

    def test_authorized_generation_lifecycle_is_bounded_and_append_only(
        self,
    ) -> None:
        authorization = authorize_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            prior_generation=1,
            source_bundle_uid="fixture-bundle",
            source_manifest_sha256=SOURCE_MANIFEST,
            prior_method_version="worker-method-v1",
            new_method_version="worker-method-v2",
            prior_semantic_prompt_fingerprint=OLD_PROMPT,
            new_semantic_prompt_fingerprint=NEW_PROMPT,
            superseded_attempt_ids=[OLD_FALLBACK_ID, OLD_PRIMARY_ID],
            decision_basis_id="fixture-decision-1",
            reason="bounded prompt partition and method change",
            actor_kind="fixture-controller",
            authorized_at="2026-07-28T00:00:00+00:00",
        )
        self.assertEqual(authorization["generation"], 2)
        self.assertEqual(authorization["current_state"], "retry_pending")
        self.assertFalse(authorization["replayed"])

        replay = authorize_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            prior_generation=1,
            source_bundle_uid="fixture-bundle",
            source_manifest_sha256=SOURCE_MANIFEST,
            prior_method_version="worker-method-v1",
            new_method_version="worker-method-v2",
            prior_semantic_prompt_fingerprint=OLD_PROMPT,
            new_semantic_prompt_fingerprint=NEW_PROMPT,
            superseded_attempt_ids=[OLD_PRIMARY_ID, OLD_FALLBACK_ID],
            decision_basis_id="fixture-decision-1",
            reason="bounded prompt partition and method change",
            actor_kind="fixture-controller",
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            replay["authorization_id"], authorization["authorization_id"]
        )

        with self.assertRaisesRegex(
            Exception, "one active recovery generation"
        ):
            authorize_work_recovery(
                self.pg.dsn,
                work_item_id=WORK_ITEM_ID,
                prior_generation=2,
                source_bundle_uid="fixture-bundle",
                source_manifest_sha256=SOURCE_MANIFEST,
                prior_method_version="worker-method-v2",
                new_method_version="worker-method-v3",
                prior_semantic_prompt_fingerprint=NEW_PROMPT,
                new_semantic_prompt_fingerprint=THIRD_PROMPT,
                superseded_attempt_ids=[OLD_PRIMARY_ID, OLD_FALLBACK_ID],
                decision_basis_id="fixture-decision-active",
                reason="must not create a second active generation",
                actor_kind="fixture-controller",
            )

        started = advance_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            to_state="proposal_running",
            actor_kind="fixture-controller",
            source_job_id=NEW_JOB_ID,
            recorded_at="2026-07-28T00:00:01+00:00",
        )
        self.assertEqual(started["to_state"], "proposal_running")

        primary = register_work_recovery_attempt(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            route="primary",
            attempt_id=NEW_PRIMARY_ID,
            source_job_id=NEW_JOB_ID,
            method_version="worker-method-v2",
            semantic_prompt_fingerprint=NEW_PROMPT,
            recorded_at="2026-07-28T00:00:02.45+00:00",
        )
        self.assertEqual(primary["outcome"], "failed")
        with self.assertRaisesRegex(
            Exception, "already consumed by another attempt"
        ):
            register_work_recovery_attempt(
                self.pg.dsn,
                work_item_id=WORK_ITEM_ID,
                generation=2,
                route="primary",
                attempt_id=NEW_FALLBACK_ID,
                source_job_id=NEW_JOB_ID,
                method_version="worker-method-v2",
                semantic_prompt_fingerprint=NEW_PROMPT,
                recorded_at="2026-07-28T00:00:03+00:00",
            )
        fallback = register_work_recovery_attempt(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            route="fallback",
            attempt_id=NEW_FALLBACK_ID,
            source_job_id=NEW_JOB_ID,
            method_version="worker-method-v2",
            semantic_prompt_fingerprint=NEW_PROMPT,
            recorded_at="2026-07-28T00:00:03+00:00",
        )
        self.assertEqual(fallback["outcome"], "failed")

        before_terminal = self.pg.psql(
            command=f"""
SELECT
  (SELECT current_state
   FROM nhi_rule_history_update_queue.v_recovery_generation_current
   WHERE work_item_id = '{WORK_ITEM_ID}' AND generation = 2),
  (SELECT count(*)
   FROM nhi_rule_history_update_queue.work_generation
   WHERE work_item_id = '{WORK_ITEM_ID}');
"""
        ).stdout.strip()
        self.assertEqual(before_terminal, "proposal_running|1")

        terminal = advance_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            to_state="failed_terminal",
            actor_kind="fixture-controller",
            source_job_id=NEW_JOB_ID,
            recorded_at="2026-07-28T00:00:04+00:00",
        )
        self.assertTrue(terminal["is_terminal"])
        no_auto_generation = self.pg.psql(
            command=f"""
SELECT count(*)
FROM nhi_rule_history_update_queue.work_generation
WHERE work_item_id = '{WORK_ITEM_ID}';
"""
        ).stdout.strip()
        self.assertEqual(no_auto_generation, "1")

        partition_authorization = authorize_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            prior_generation=2,
            source_bundle_uid="fixture-bundle",
            source_manifest_sha256=SOURCE_MANIFEST,
            prior_method_version="worker-method-v2",
            new_method_version="worker-method-v3",
            prior_semantic_prompt_fingerprint=NEW_PROMPT,
            new_semantic_prompt_fingerprint=THIRD_PROMPT,
            superseded_attempt_ids=[NEW_PRIMARY_ID, NEW_FALLBACK_ID],
            decision_basis_id="fixture-decision-2",
            reason="deterministic partition is required before any model call",
            actor_kind="fixture-controller",
            authorized_at="2026-07-28T00:00:05+00:00",
        )
        self.assertEqual(partition_authorization["generation"], 3)
        partitioned = advance_work_recovery(
            self.pg.dsn,
            work_item_id=WORK_ITEM_ID,
            generation=3,
            to_state="partition_required",
            actor_kind="fixture-controller",
            recorded_at="2026-07-28T00:00:06+00:00",
        )
        self.assertTrue(partitioned["is_terminal"])
        self.assertEqual(partitioned["to_state"], "partition_required")
        self.assertEqual(
            partitioned["fingerprint"],
            sha256_bytes(
                canonical_json_bytes(
                    [
                        WORK_ITEM_ID,
                        3,
                        partitioned["transition_id"],
                        2,
                        "partition_required",
                        True,
                        None,
                        None,
                        None,
                    ]
                )
            ),
        )

        generic = self.pg.psql(
            command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES (
  '{WORK_ITEM_ID}', 7, '10000000-0000-0000-0000-000000000026',
  'failed_terminal', 'retry_pending', 'fixture', repeat('7',64),
  '{{"event":"forbidden"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-28 00:00:07+00'
);
""",
            check=False,
        )
        self.assertNotEqual(generic.returncode, 0)
        self.assertIn(
            "generic transition path cannot recover failed work",
            generic.stderr,
        )

        acl = self.pg.psql(
            command="""
SELECT
  has_table_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_update_queue.work_generation',
    'INSERT'
  ),
  has_function_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'EXECUTE'
  );
"""
        ).stdout.strip()
        self.assertEqual(acl, "f|t")

        rollback = self.pg.psql(file=RECOVERY_ROLLBACK, check=False)
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("ledger is nonempty", rollback.stderr)

    def test_generic_generation_one_partition_is_zero_call_terminal(
        self,
    ) -> None:
        evidence = {
            "event": "partition_required",
            "worker_calls": 0,
            "partition_receipt_sha256": "f" * 64,
        }
        receipt = append_work_transition(
            self.pg.dsn,
            work_item_id=PARTITION_WORK_ITEM_ID,
            to_state="partition_required",
            actor_kind="fixture-controller",
            evidence=evidence,
            source_job_id="10000000-0000-0000-0000-000000000001",
            recorded_at="2026-07-28T00:00:00+00:00",
        )
        self.assertTrue(receipt["is_terminal"])
        self.assertEqual(receipt["to_state"], "partition_required")

        persisted = self.pg.psql(
            command=f"""
SELECT
  transition.evidence_json->>'worker_calls',
  transition.evidence_json->>'partition_receipt_sha256',
  transition.bundle_receipt_id IS NULL,
  transition.candidate_proposal_id IS NULL,
  current.is_terminal,
  (
    SELECT count(*)
    FROM nhi_rule_history_update_ops.worker_attempt attempt
    WHERE attempt.job_id =
      '10000000-0000-0000-0000-000000000001'
  )
FROM nhi_rule_history_update_queue.work_item_transition transition
JOIN nhi_rule_history_update_queue.v_work_item_current current
  ON current.work_item_id = transition.work_item_id
WHERE transition.work_item_id = '{PARTITION_WORK_ITEM_ID}'
  AND transition.to_state = 'partition_required';
"""
        ).stdout.strip()
        self.assertEqual(persisted, f"0|{'f' * 64}|t|t|t|0")

        with self.assertRaisesRegex(
            UpdateQueueError, "terminal work-item state"
        ):
            append_work_transition(
                self.pg.dsn,
                work_item_id=PARTITION_WORK_ITEM_ID,
                to_state="failed_terminal",
                actor_kind="fixture-controller",
                evidence={"event": "forbidden-after-partition"},
                source_job_id=(
                    "10000000-0000-0000-0000-000000000001"
                ),
                recorded_at="2026-07-28T00:00:01+00:00",
            )


if __name__ == "__main__":
    unittest.main()
