from __future__ import annotations

import hashlib
import subprocess
import unittest
import uuid
from pathlib import Path

from tests.test_canonical_promotion_migrations import DisposablePostgres


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
OBSERVATION_FIX = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.sql"
)
OBSERVATION_FIX_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.rollback.sql"
)
LOCK_FIX = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_runtime_lock_fix.sql"
)
LOCK_FIX_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_runtime_lock_fix.rollback.sql"
)


class RuntimeLockFixMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = DisposablePostgres()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def setUp(self) -> None:
        self.database = "runtime_lock_" + uuid.uuid4().hex[:12]
        self.pg.create_database(self.database)

    def tearDown(self) -> None:
        self.pg.drop_database(self.database)

    def apply(self, path: Path, *, check: bool = True):
        return self.pg.psql(self.database, file=path, check=check)

    def execute(self, sql: str, *, check: bool = True):
        return self.pg.psql(self.database, command=sql, check=check)

    def apply_base(self) -> None:
        self.apply(OPS_FORWARD)
        self.apply(OBSERVATION_FIX)

    def psql_argv(self) -> list[str]:
        return [
            self.pg.psql_bin,
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            *self.pg.database_args(self.database),
        ]

    @staticmethod
    def seed_sql(job_id: str, lease_id: str) -> str:
        return f"""
INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES (
  '{job_id}', '{job_id.replace("-", "") * 2}'::text,
  'fixture', 'fixture', 'https://example.test/feed',
  '{"a" * 64}', '2026-07-27T00:00:00+00:00',
  '2026-07-27T00:05:00+00:00', '2026-07-27',
  '2026-07-27T00:00:00+00:00'
);
INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at,
  max_runtime_seconds
) VALUES (
  '{lease_id}', '{job_id}', 'fixture-owner',
  '2026-07-27T00:00:00+00:00',
  '2026-07-27T00:05:00+00:00', 300
);
"""

    @staticmethod
    def observation_insert(
        observation_id: str,
        job_id: str,
        lease_id: str,
        observed_at: str,
    ) -> str:
        return f"""
INSERT INTO nhi_rule_history_update_ops.url_observation (
  url_observation_id, job_id, lease_id, owner_key, requested_url,
  observed_at, outcome, relation_to_previous, error_code
) VALUES (
  '{observation_id}', '{job_id}', '{lease_id}', 'fixture-owner',
  'https://example.test/feed', '{observed_at}', 'transport_error',
  'not_comparable', 'fixture_transport_error'
);
"""

    @staticmethod
    def worker_insert(
        attempt_id: str,
        job_id: str,
        lease_id: str,
        started_at: str,
    ) -> str:
        return f"""
INSERT INTO nhi_rule_history_update_ops.worker_attempt (
  attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
  primary_attempt_id, provider, runtime, model, prompt_sha256,
  output_sha256, started_at, completed_at, status, failure_code,
  fallback_reason
) VALUES (
  '{attempt_id}', '{job_id}', '{lease_id}', 'fixture-owner', 1,
  'primary', NULL, 'fixture', 'fixture', 'fixture', '{"b" * 64}',
  '{"c" * 64}', '{started_at}', '{started_at}', 'success',
  NULL, NULL
);
"""

    def test_applied_observation_fix_files_are_frozen(self) -> None:
        self.assertEqual(
            hashlib.sha256(OBSERVATION_FIX.read_bytes()).hexdigest(),
            "9b35cfdde52dff06982dffe3a0bde13ae79118238cbe9403d0c1d384e5ee6299",
        )
        self.assertEqual(
            hashlib.sha256(OBSERVATION_FIX_ROLLBACK.read_bytes()).hexdigest(),
            "c234966ab666862e8527286dbde8d8f1488ce8dc73955fe6a8f36e33ab1ca92b",
        )

    def test_base_forward_reapply_and_rollback(self) -> None:
        self.apply_base()
        self.apply(LOCK_FIX)
        self.apply(LOCK_FIX)
        target = self.execute(
            """
SELECT
  position('PERFORM pg_advisory_xact_lock(' in observation.prosrc) > 0
  AND position('FOR UPDATE' in observation.prosrc) = 0
  AND position('PERFORM pg_advisory_xact_lock(' in worker.prosrc) > 0
  AND position('FOR UPDATE' in worker.prosrc) = 0
FROM pg_proc observation, pg_proc worker
WHERE observation.oid =
  'nhi_rule_history_update_ops.guard_owned_observation_insert()'
    ::regprocedure
AND worker.oid =
  'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
    ::regprocedure;
"""
        )
        self.assertEqual(target.stdout.strip(), "t")
        self.apply(LOCK_FIX_ROLLBACK)
        rolled_back = self.execute(
            """
SELECT
  position('FOR UPDATE' in observation.prosrc) > 0
  AND position('pg_advisory_xact_lock' in observation.prosrc) = 0
  AND position('FOR UPDATE' in worker.prosrc) > 0
  AND position('pg_advisory_xact_lock' in worker.prosrc) = 0
FROM pg_proc observation, pg_proc worker
WHERE observation.oid =
  'nhi_rule_history_update_ops.guard_owned_observation_insert()'
    ::regprocedure
AND worker.oid =
  'nhi_rule_history_update_ops.guard_worker_attempt_insert()'
    ::regprocedure;
"""
        )
        self.assertEqual(rolled_back.stdout.strip(), "t")
        self.apply(LOCK_FIX)

    def test_unknown_view_drift_fails_closed(self) -> None:
        self.apply_base()
        self.execute(
            """
COMMENT ON VIEW
  nhi_rule_history_update_ops.v_url_response_chronology IS 'drift';
"""
        )
        failed = self.apply(LOCK_FIX, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "runtime-lock fix refuses URL chronology view or ACL drift",
            failed.stderr,
        )

    def test_function_acl_requires_complete_owner_default_without_grant_option(
        self,
    ) -> None:
        self.apply_base()
        self.execute(
            """
REVOKE ALL PRIVILEGES ON FUNCTION
  nhi_rule_history_update_ops.guard_owned_observation_insert()
FROM CURRENT_USER;
"""
        )
        empty_owner_acl = self.apply(LOCK_FIX, check=False)
        self.assertNotEqual(empty_owner_acl.returncode, 0)
        self.assertIn(
            "runtime-lock fix refuses chronology function attribute or ACL drift",
            empty_owner_acl.stderr,
        )

        self.pg.drop_database(self.database)
        self.database = "runtime_lock_" + uuid.uuid4().hex[:12]
        self.pg.create_database(self.database)
        self.apply_base()
        self.apply(LOCK_FIX)
        self.execute(
            """
REVOKE ALL PRIVILEGES ON FUNCTION
  nhi_rule_history_update_ops.guard_worker_attempt_insert()
FROM CURRENT_USER;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_ops.guard_worker_attempt_insert()
TO CURRENT_USER WITH GRANT OPTION;
"""
        )
        owner_grant_option = self.apply(LOCK_FIX_ROLLBACK, check=False)
        self.assertNotEqual(owner_grant_option.returncode, 0)
        self.assertIn(
            "runtime-lock rollback refuses owner, function, or ACL drift",
            owner_grant_option.stderr,
        )

    def test_advisory_lock_serializes_observation_and_worker(self) -> None:
        self.apply_base()
        self.apply(LOCK_FIX)
        job_id = str(uuid.uuid4())
        lease_id = str(uuid.uuid4())
        observation_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        self.execute(self.seed_sql(job_id, lease_id))
        first_sql = (
            "BEGIN;\n"
            + self.observation_insert(
                observation_id,
                job_id,
                lease_id,
                "2026-07-27T00:00:30+00:00",
            )
            + "SELECT 'observation-ready';\n"
            + "SELECT pg_sleep(1.5);\nCOMMIT;\n"
        )
        first = subprocess.Popen(
            self.psql_argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert first.stdin is not None
        assert first.stdout is not None
        first.stdin.write(first_sql)
        first.stdin.close()
        while True:
            line = first.stdout.readline()
            self.assertNotEqual(line, "")
            if line.strip() == "observation-ready":
                break
        second = subprocess.run(
            [
                *self.psql_argv(),
                "--command",
                self.worker_insert(
                    attempt_id,
                    job_id,
                    lease_id,
                    "2026-07-27T00:00:20+00:00",
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        _stdout, first_stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn(
            "worker attempt starts before an existing source observation",
            second.stderr,
        )

    def test_runtime_role_inserts_without_update_or_delete(self) -> None:
        self.apply_base()
        self.apply(LOCK_FIX)
        job_id = str(uuid.uuid4())
        lease_id = str(uuid.uuid4())
        self.execute(self.seed_sql(job_id, lease_id))
        observation_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        result = self.execute(
            """
SELECT
  has_table_privilege(
    'nhi_rule_history_update_runtime',
    'nhi_rule_history_update_ops.job_lease',
    'UPDATE'
  )::text || '|' ||
  has_table_privilege(
    'nhi_rule_history_update_runtime',
    'nhi_rule_history_update_ops.job_lease',
    'DELETE'
  )::text;
SET ROLE nhi_rule_history_update_runtime;
"""
            + self.observation_insert(
                observation_id,
                job_id,
                lease_id,
                "2026-07-27T00:00:10+00:00",
            )
            + self.worker_insert(
                attempt_id,
                job_id,
                lease_id,
                "2026-07-27T00:00:20+00:00",
            )
            + """
RESET ROLE;
SELECT count(*) FROM nhi_rule_history_update_ops.worker_attempt
WHERE attempt_id = '"""
            + attempt_id
            + """';
"""
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("false|false", lines)
        self.assertEqual(lines[-1], "1")


if __name__ == "__main__":
    unittest.main()
