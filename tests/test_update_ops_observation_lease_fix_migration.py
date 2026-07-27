from __future__ import annotations

import subprocess
import unittest
import uuid
from pathlib import Path

from tests.test_canonical_promotion_migrations import DisposablePostgres


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
FIX_FORWARD = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.sql"
)
FIX_ROLLBACK = (
    MIGRATIONS
    / "2026-07-27_nhi_rule_history_update_ops_observation_lease_fix.rollback.sql"
)
TEMP_EXPECTED_VIEW = (
    "expected_nhi_url_response_chronology_observation_lease_fix_v1"
)


class ObservationLeaseFixMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = DisposablePostgres()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def setUp(self) -> None:
        self.database = "observation_fix_" + uuid.uuid4().hex[:12]
        self.pg.create_database(self.database)

    def tearDown(self) -> None:
        self.pg.drop_database(self.database)

    def apply(self, path: Path, *, check: bool = True):
        return self.pg.psql(
            self.database,
            file=path,
            check=check,
        )

    def execute(self, sql: str, *, check: bool = True):
        return self.pg.psql(
            self.database,
            command=sql,
            check=check,
        )

    def same_session(
        self,
        *,
        files: tuple[Path, ...],
        before: str | None = None,
        after: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            self.pg.psql_bin,
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            *self.pg.database_args(self.database),
        ]
        if before is not None:
            argv.extend(["--command", before])
        for path in files:
            argv.extend(["--file", str(path)])
        if after is not None:
            argv.extend(["--command", after])
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr)
        return result

    def assert_fix_fails(self, path: Path, message: str) -> None:
        failed = self.apply(path, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(message, failed.stderr)

    def test_extra_insert_triggers_fail_forward_and_rollback(self) -> None:
        self.apply(OPS_FORWARD)
        self.execute(
            """
CREATE FUNCTION public.extra_observation_insert_trigger()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RETURN NEW;
END;
$$;
"""
        )
        trigger_cases = (
            (
                "url_observation",
                "extra_url_insert_trigger",
                "BEFORE INSERT",
                "FOR EACH ROW",
                False,
            ),
            (
                "worker_attempt",
                "extra_worker_insert_trigger",
                "AFTER INSERT",
                "FOR EACH STATEMENT",
                True,
            ),
        )
        for table, trigger_name, timing, scope, disabled in trigger_cases:
            with self.subTest(phase="forward", table=table):
                self.execute(
                    f"""
CREATE TRIGGER {trigger_name}
{timing} ON nhi_rule_history_update_ops.{table}
{scope}
EXECUTE FUNCTION public.extra_observation_insert_trigger();
"""
                )
                if disabled:
                    self.execute(
                        f"""
ALTER TABLE nhi_rule_history_update_ops.{table}
DISABLE TRIGGER {trigger_name};
"""
                    )
                self.assert_fix_fails(
                    FIX_FORWARD,
                    "unmanaged INSERT trigger exists on a chronology relation",
                )
                self.execute(
                    f"""
DROP TRIGGER {trigger_name}
ON nhi_rule_history_update_ops.{table};
"""
                )

        self.apply(FIX_FORWARD)
        for table, trigger_name, timing, scope, disabled in trigger_cases:
            with self.subTest(phase="rollback", table=table):
                self.execute(
                    f"""
CREATE TRIGGER {trigger_name}
{timing} ON nhi_rule_history_update_ops.{table}
{scope}
EXECUTE FUNCTION public.extra_observation_insert_trigger();
"""
                )
                if disabled:
                    self.execute(
                        f"""
ALTER TABLE nhi_rule_history_update_ops.{table}
DISABLE TRIGGER {trigger_name};
"""
                    )
                self.assert_fix_fails(
                    FIX_ROLLBACK,
                    (
                        "rollback refuses unmanaged INSERT trigger "
                        "on a chronology relation"
                    ),
                )
                self.execute(
                    f"""
DROP TRIGGER {trigger_name}
ON nhi_rule_history_update_ops.{table};
"""
                )

    def test_managed_insert_trigger_state_is_exact(self) -> None:
        self.apply(OPS_FORWARD)
        self.execute(
            """
ALTER TABLE nhi_rule_history_update_ops.url_observation
DISABLE TRIGGER url_observation_insert_guard;
"""
        )
        self.assert_fix_fails(
            FIX_FORWARD,
            "managed chronology triggers are missing, disabled, or rebound",
        )
        self.execute(
            """
ALTER TABLE nhi_rule_history_update_ops.url_observation
ENABLE TRIGGER url_observation_insert_guard;
"""
        )
        self.apply(FIX_FORWARD)
        self.execute(
            """
ALTER TABLE nhi_rule_history_update_ops.worker_attempt
DISABLE TRIGGER worker_attempt_insert_guard;
"""
        )
        self.assert_fix_fails(
            FIX_ROLLBACK,
            "managed chronology triggers are missing, disabled, or rebound",
        )

    def test_view_acl_is_exact_in_forward_and_rollback(self) -> None:
        mutation_cases = (
            (
                "missing_runtime_select",
                """
REVOKE SELECT ON
  nhi_rule_history_update_ops.v_url_response_chronology
FROM nhi_rule_history_update_runtime;
""",
            ),
            (
                "runtime_grant_option",
                """
GRANT SELECT ON
  nhi_rule_history_update_ops.v_url_response_chronology
TO nhi_rule_history_update_runtime WITH GRANT OPTION;
""",
            ),
            (
                "public_select",
                """
GRANT SELECT ON
  nhi_rule_history_update_ops.v_url_response_chronology
TO PUBLIC;
""",
            ),
        )
        for label, mutation in mutation_cases:
            with self.subTest(label=label):
                database = self.database
                self.apply(OPS_FORWARD)
                self.apply(FIX_FORWARD)
                self.execute(mutation)
                self.assert_fix_fails(
                    FIX_FORWARD,
                    "managed URL chronology view definition, owner, or ACL drifted",
                )
                self.assert_fix_fails(
                    FIX_ROLLBACK,
                    "rollback refuses a drifted or missing URL chronology view",
                )
                self.pg.drop_database(database)
                self.database = "observation_fix_" + uuid.uuid4().hex[:12]
                self.pg.create_database(self.database)

        extra_role = "acl_extra_" + uuid.uuid4().hex[:12]
        self.execute(f"CREATE ROLE {extra_role} NOLOGIN;")
        self.apply(OPS_FORWARD)
        self.apply(FIX_FORWARD)
        self.execute(
            f"""
GRANT SELECT ON
  nhi_rule_history_update_ops.v_url_response_chronology
TO {extra_role};
"""
        )
        self.assert_fix_fails(
            FIX_FORWARD,
            "managed URL chronology view definition, owner, or ACL drifted",
        )
        self.assert_fix_fails(
            FIX_ROLLBACK,
            "rollback refuses a drifted or missing URL chronology view",
        )

    def test_view_acl_tracks_version_default_plus_runtime_select(self) -> None:
        forward_source = FIX_FORWARD.read_text(encoding="utf-8")
        rollback_source = FIX_ROLLBACK.read_text(encoding="utf-8")
        for source in (forward_source, rollback_source):
            self.assertNotIn("count(*) <> 8", source)
            self.assertNotIn(") <> 7", source)
            self.assertIn(
                "FROM aclexplode(acldefault('r', schema_owner)) acl",
                source,
            )
            self.assertIn("EXCEPT ALL", source)

        self.apply(OPS_FORWARD)
        self.apply(FIX_FORWARD)
        probe = self.execute(
            """
WITH relation AS (
  SELECT relation.relowner, relation.relacl
  FROM pg_class relation
  WHERE relation.oid =
    'nhi_rule_history_update_ops.v_url_response_chronology'::regclass
),
actual_acl AS (
  SELECT
    acl.grantor,
    acl.grantee,
    acl.privilege_type,
    acl.is_grantable
  FROM relation
  CROSS JOIN LATERAL aclexplode(
    coalesce(
      relation.relacl,
      acldefault('r', relation.relowner)
    )
  ) acl
),
owner_default_acl AS (
  SELECT
    acl.grantor,
    acl.grantee,
    acl.privilege_type,
    acl.is_grantable
  FROM relation
  CROSS JOIN LATERAL aclexplode(
    acldefault('r', relation.relowner)
  ) acl
),
expected_acl AS (
  SELECT * FROM owner_default_acl
  UNION ALL
  SELECT
    relation.relowner,
    'nhi_rule_history_update_runtime'::regrole::oid,
    'SELECT'::text,
    false
  FROM relation
),
simulated_owner_default_acl AS (
  SELECT * FROM owner_default_acl
  UNION ALL
  SELECT
    relation.relowner,
    relation.relowner,
    'SIMULATED_FUTURE_OWNER_PRIVILEGE'::text,
    false
  FROM relation
),
simulated_expected_acl AS (
  SELECT * FROM simulated_owner_default_acl
  UNION ALL
  SELECT
    relation.relowner,
    'nhi_rule_history_update_runtime'::regrole::oid,
    'SELECT'::text,
    false
  FROM relation
),
simulated_actual_acl AS (
  SELECT * FROM simulated_expected_acl
)
SELECT
  current_setting('server_version_num') || '|' ||
  (
    NOT EXISTS (
      (
        SELECT * FROM actual_acl
        EXCEPT ALL
        SELECT * FROM expected_acl
      )
      UNION ALL
      (
        SELECT * FROM expected_acl
        EXCEPT ALL
        SELECT * FROM actual_acl
      )
    )
  )::text || '|' ||
  (
    NOT EXISTS (
      (
        SELECT * FROM simulated_actual_acl
        EXCEPT ALL
        SELECT * FROM simulated_expected_acl
      )
      UNION ALL
      (
        SELECT * FROM simulated_expected_acl
        EXCEPT ALL
        SELECT * FROM simulated_actual_acl
      )
    )
  )::text;
"""
        ).stdout.strip()
        version_num, actual_exact, simulated_future_exact = probe.split("|")
        self.assertGreaterEqual(int(version_num), 160000)
        self.assertEqual(actual_exact, "true")
        self.assertEqual(simulated_future_exact, "true")

    def test_view_owner_is_exact_in_forward_and_rollback(self) -> None:
        extra_role = "owner_extra_" + uuid.uuid4().hex[:12]
        self.execute(f"CREATE ROLE {extra_role} NOLOGIN;")
        self.apply(OPS_FORWARD)
        self.apply(FIX_FORWARD)
        self.execute(
            f"""
ALTER VIEW nhi_rule_history_update_ops.v_url_response_chronology
OWNER TO {extra_role};
"""
        )
        self.assert_fix_fails(
            FIX_FORWARD,
            "managed URL chronology view definition, owner, or ACL drifted",
        )
        self.assert_fix_fails(
            FIX_ROLLBACK,
            "rollback refuses a drifted or missing URL chronology view",
        )

    def test_first_forward_rejects_default_acl_extra_grantee(self) -> None:
        extra_role = "default_acl_extra_" + uuid.uuid4().hex[:12]
        self.execute(f"CREATE ROLE {extra_role} NOLOGIN;")
        self.apply(OPS_FORWARD)
        self.execute(
            f"""
ALTER DEFAULT PRIVILEGES
IN SCHEMA nhi_rule_history_update_ops
GRANT SELECT ON TABLES TO {extra_role};
"""
        )
        self.assert_fix_fails(
            FIX_FORWARD,
            "created URL chronology view owner or ACL is not exact",
        )
        self.assertEqual(
            self.execute(
                """
SELECT
  to_regclass(
    'nhi_rule_history_update_ops.v_url_response_chronology'
  ) IS NULL;
"""
            ).stdout.strip(),
            "t",
        )

    def test_temp_verifier_cleanup_allows_same_session_replay(self) -> None:
        forward_twice = self.same_session(
            files=(OPS_FORWARD, FIX_FORWARD, FIX_FORWARD),
            after=f"""
SELECT
  (to_regclass('pg_temp.{TEMP_EXPECTED_VIEW}') IS NULL)::text || '|' ||
  (to_regclass(
    'nhi_rule_history_update_ops.v_url_response_chronology'
  ) IS NOT NULL)::text;
""",
        )
        self.assertEqual(
            forward_twice.stdout.strip().splitlines()[-1],
            "true|true",
        )

        self.pg.drop_database(self.database)
        self.database = "observation_fix_" + uuid.uuid4().hex[:12]
        self.pg.create_database(self.database)
        forward_rollback = self.same_session(
            files=(OPS_FORWARD, FIX_FORWARD, FIX_ROLLBACK),
            after=f"""
SELECT
  (to_regclass('pg_temp.{TEMP_EXPECTED_VIEW}') IS NULL)::text || '|' ||
  (to_regclass(
    'nhi_rule_history_update_ops.v_url_response_chronology'
  ) IS NULL)::text;
""",
        )
        self.assertEqual(
            forward_rollback.stdout.strip().splitlines()[-1],
            "true|true",
        )

    def test_temp_verifier_name_collision_fails_without_overwrite(self) -> None:
        self.apply(OPS_FORWARD)
        collided = self.same_session(
            before=f"CREATE TEMP TABLE {TEMP_EXPECTED_VIEW} (sentinel int);",
            files=(FIX_FORWARD,),
            check=False,
        )
        self.assertNotEqual(collided.returncode, 0)
        self.assertIn(
            "temporary chronology verifier relation name collision",
            collided.stderr,
        )


if __name__ == "__main__":
    unittest.main()
