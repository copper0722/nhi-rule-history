"""Migration v30 and the 通則 point's patch: one loader, safe before and after the check is widened.

A 通則 point is clause ``0.N``.  The announced layer's ``clause_patch_clause_code_check`` admitted
dotted codes only until v30, so an unmigrated database refuses a ``0.N`` patch row and with it the
whole load.  The loader therefore reads the live constraint: before v30 a point is a held clause and
the notice's dotted clauses load; after v30 the same code projects the point as a ``patch_only``
patch, and a notice served with the point held is superseded to add it.  Everything here runs on
disposable PostgreSQL clusters built from the migrations; nothing touches a production database.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from nhi_rule_history import announced_release
from nhi_rule_history.announced_notice import sha256_text
from nhi_rule_history.announced_release import (
    AnnouncedReleaseError,
    _connect,
    activate_overlay_release,
    compose_overlay_release,
    general_principle_patches_admitted,
    load_overlay_release,
)
from tests import test_announced_notice as notice_fixture
from tests import test_update_queue_recovery_v2 as recovery_fixture
from tests.test_announced_release import (
    BASE_RUN,
    MIGRATIONS,
    SERVED,
    TODAY,
    V30_FORWARD,
    V30_ROLLBACK,
    _apply_migrations,
    _public_patches,
    _seed,
)

ROOT = Path(__file__).resolve().parents[1]
READBACK = ROOT / "database" / "queries" / "announced-general-principle-code-readback-v30.sql"
V18 = MIGRATIONS / "2026-07-29_nhi_rule_history_current_publication_v18.sql"
V21 = MIGRATIONS / "2026-07-29_nhi_rule_history_announced_decision_v21.sql"
V21_PATTERN = "^[1-9][0-9]*(?:[.][0-9]+)+$"
V30_PATTERN = "^(0[.][1-9][0-9]*|[1-9][0-9]*(?:[.][0-9]+)+)$"
REASON = "general_principle_patch_needs_v30"
PRIVATE_MARKERS = re.compile(r"\b[a-z][a-z0-9-]*:\d{4,5}\b|/home/|/data/|\.ts\.net\b|vault" + "_main", re.IGNORECASE)

SERVED_WITH_POINT = {
    **SERVED,
    "0.4": "四、注射藥品之使用原則：\n（一）served text of the point",
    "4.2.7": "4.2.7.served text",
    "4.2.8": "4.2.8.served text",
}
CHECK_NAME = "clause_patch_clause_code_check"
GUARDED_CHECKS = ("composed_clause_version_clause_code_check", "clause_reader_profile_clause_code_check")


def pattern_of(statement: str, *, after: str | None = None) -> str:
    """The first ``clause_code ~ '...'`` pattern of a migration, optionally after an anchor."""

    text = statement if after is None else statement[statement.index(after):]
    return re.search(r"clause_code ~ '([^']*)'", text).group(1)


class MigrationFilesTest(unittest.TestCase):
    def test_patterns_agree_with_the_migrations_they_extend_and_restore(self) -> None:
        forward, rollback = V30_FORWARD.read_text(encoding="utf-8"), V30_ROLLBACK.read_text(encoding="utf-8")
        # v30 widens to the publication layer's pattern; the rollback restores v21's, byte for byte
        self.assertEqual(pattern_of(forward, after="ADD CONSTRAINT"), V30_PATTERN)
        self.assertEqual(pattern_of(V18.read_text(encoding="utf-8"), after="CREATE TABLE nhi_rule_history_publication.current_clause"),
                         V30_PATTERN)
        self.assertEqual(pattern_of(rollback, after="ADD CONSTRAINT"), V21_PATTERN)
        self.assertEqual(
            pattern_of(V21.read_text(encoding="utf-8"), after="CREATE TABLE nhi_rule_history_announced.clause_patch"),
            V21_PATTERN,
        )

    def test_the_files_change_one_constraint_and_nothing_else(self) -> None:
        for path in (V30_FORWARD, V30_ROLLBACK):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                alters = re.findall(r"^ALTER TABLE (\S+)", text, re.MULTILINE)
                self.assertEqual(alters, ["nhi_rule_history_announced.clause_patch"])
                self.assertEqual(len(re.findall(r"^\s*DROP CONSTRAINT (\w+)", text, re.MULTILINE)), 1)
                self.assertEqual(len(re.findall(r"^\s*ADD CONSTRAINT (\w+)", text, re.MULTILINE)), 1)
                self.assertEqual(len(re.findall(r"^COMMENT ON CONSTRAINT", text, re.MULTILINE)), 1)
                for verb in ("CREATE TABLE", "DROP TABLE", "CREATE SCHEMA", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                             "GRANT", "REVOKE", "CREATE FUNCTION", "CREATE TRIGGER"):
                    self.assertNotRegex(text, rf"^{verb}\b", verb)
                # the two other tables' code checks are named only to say they stay as they are
                for name in GUARDED_CHECKS:
                    self.assertNotIn(f"CONSTRAINT {name}", text)
                self.assertIn("BEGIN;", text)
                self.assertIn("COMMIT;", text)

    def test_the_files_carry_no_private_host_or_path(self) -> None:
        for path in (V30_FORWARD, V30_ROLLBACK, READBACK):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(PRIVATE_MARKERS.search(line), f"{path.name}:{number}: {line}")


class MigrationLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg, exclude=(V30_FORWARD.name,))
        _seed(cls.pg.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def setUp(self) -> None:
        self.reset()

    # ---- helpers ----

    def rows(self, sql: str, params=()) -> list[tuple]:
        with psycopg.connect(self.pg.dsn) as connection:
            return [tuple(row) for row in connection.execute(sql, params).fetchall()]

    def definition(self, name: str = CHECK_NAME) -> str | None:
        rows = self.rows(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s "
            "AND connamespace = 'nhi_rule_history_announced'::regnamespace", (name,))
        return rows[0][0] if rows else None

    def comment(self) -> str | None:
        return self.rows(
            "SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint WHERE conname = %s "
            "AND connamespace = 'nhi_rule_history_announced'::regnamespace", (CHECK_NAME,))[0][0]

    def widened(self) -> bool:
        return V30_PATTERN in (self.definition() or "")

    def remove_project_assigned_patches(self) -> None:
        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute("SET session_replication_role = replica")  # the sealed-run guards are test fixtures here
            connection.execute("DELETE FROM nhi_rule_history_announced.clause_patch WHERE clause_code ~ '^0[.]'")
            connection.commit()

    def reset(self) -> None:
        """The database as it is before v30: roll back if a test applied it."""

        if self.widened():
            self.remove_project_assigned_patches()
            self.pg.psql(file=V30_ROLLBACK)
        self.assertEqual(self.definition(), f"CHECK ((clause_code ~ '{V21_PATTERN}'::text))")

    def accepts(self, table_row_code: str) -> bool:
        """Whether the database takes a clause_patch row with this code; the row is rolled back either way."""

        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute("SET session_replication_role = replica")  # skip FKs and write-once guards only
            try:
                connection.execute(
                    """
                    INSERT INTO nhi_rule_history_announced.clause_patch
                    SELECT (jsonb_populate_record(
                             NULL::nhi_rule_history_announced.clause_patch,
                             to_jsonb(p) || jsonb_build_object('clause_code', %s::text,
                                                               'patch_id', gen_random_uuid()))).*
                    FROM nhi_rule_history_announced.clause_patch AS p WHERE p.clause_code = '9.9'
                    """, (table_row_code,))
                accepted = True
            except psycopg.errors.CheckViolation:
                accepted = False
            connection.rollback()
        return accepted

    def detected(self) -> bool:
        with _connect(self.pg.dsn, read_only=True) as connection:
            return general_principle_patches_admitted(connection)

    # ---- tests ----

    def test_an_unmigrated_database_refuses_a_project_assigned_patch_code(self) -> None:
        self.assertTrue(self.accepts("4.2.9"))  # the probe itself is sound: a dotted code goes in
        self.assertFalse(self.accepts("0.4"))
        self.assertFalse(self.detected())

    def replace_check(self, expression: str | None, *, not_valid: bool = False) -> None:
        """Put another check in place (NOT VALID: the probes need not hold for the rows already there)."""

        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute(
                f"ALTER TABLE nhi_rule_history_announced.clause_patch DROP CONSTRAINT IF EXISTS {CHECK_NAME}")
            if expression is not None:
                connection.execute(
                    f"ALTER TABLE nhi_rule_history_announced.clause_patch ADD CONSTRAINT {CHECK_NAME} "
                    f"CHECK ({expression})" + (" NOT VALID" if not_valid else ""))
            connection.commit()

    def test_the_detector_fails_closed_on_a_check_it_cannot_read_or_that_admits_only_some_codes(self) -> None:
        cases = {
            "v30": (f"clause_code ~ '{V30_PATTERN}'", True),
            "only some of the 0.N codes": (
                "clause_code ~ '^(0[.]([1-9]|1[0-2])|[1-9][0-9]*(?:[.][0-9]+)+)$'", False),
            "0.N only, no dotted code": ("clause_code ~ '^0[.][1-9][0-9]*$'", False),
            "no pattern to read": ("clause_code <> ''", False),
            "no check at all": (None, False),
        }
        try:
            for name, (expression, expected) in cases.items():
                with self.subTest(check=name):
                    self.replace_check(expression, not_valid=True)
                    self.assertEqual(self.detected(), expected)
        finally:
            self.replace_check(f"clause_code ~ '{V21_PATTERN}'")
        self.assertFalse(self.detected())

    def test_the_migrated_database_admits_exactly_the_designed_codes(self) -> None:
        before = {name: self.definition(name) for name in GUARDED_CHECKS}
        self.assertTrue(all(before.values()))
        self.pg.psql(file=V30_FORWARD)
        self.assertEqual(self.definition(), f"CHECK ((clause_code ~ '{V30_PATTERN}'::text))")
        for code in ("0.1", "0.4", "0.12", "0.99", "2.1.4.2", "4.2.9", "9.140"):
            with self.subTest(admitted=code):
                self.assertTrue(self.accepts(code))
        for code in ("0.0", "0.04", "00.4", "0.4.1", "0", "4", "", "a.b", "0.", "0.4 ", " 0.4", "通則"):
            with self.subTest(refused=code):
                self.assertFalse(self.accepts(code))
        # only that check changed
        self.assertEqual({name: self.definition(name) for name in GUARDED_CHECKS}, before)
        self.assertIn("project-assigned", self.comment())
        self.assertTrue(self.detected())

    def test_rollback_restores_the_v21_check_exactly_and_forward_applies_again(self) -> None:
        original = (self.definition(), self.comment())
        self.pg.psql(file=V30_FORWARD)
        self.pg.psql(file=V30_ROLLBACK)
        self.assertEqual((self.definition(), self.comment()), original)
        self.assertIsNone(original[1])
        self.assertFalse(self.accepts("0.4"))
        self.pg.psql(file=V30_FORWARD)
        self.assertTrue(self.accepts("0.4"))

    def test_the_forward_file_refuses_a_second_application_and_changes_nothing(self) -> None:
        self.pg.psql(file=V30_FORWARD)
        again = self.pg.psql(file=V30_FORWARD, check=False)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("not the v21 check", again.stderr)
        self.assertEqual(self.definition(), f"CHECK ((clause_code ~ '{V30_PATTERN}'::text))")

    def test_the_rollback_file_refuses_an_unmigrated_database(self) -> None:
        refused = self.pg.psql(file=V30_ROLLBACK, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("not the v30 check", refused.stderr)

    def test_the_rollback_refuses_while_a_project_assigned_patch_exists(self) -> None:
        self.pg.psql(file=V30_FORWARD)
        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                """
                INSERT INTO nhi_rule_history_announced.clause_patch
                SELECT (jsonb_populate_record(
                         NULL::nhi_rule_history_announced.clause_patch,
                         to_jsonb(p) || jsonb_build_object('clause_code', '0.4', 'patch_id', gen_random_uuid()))).*
                FROM nhi_rule_history_announced.clause_patch AS p WHERE p.clause_code = '9.9'
                """)
            connection.commit()
        refused = self.pg.psql(file=V30_ROLLBACK, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("1 clause_patch row(s) hold a project-assigned 0.N code", refused.stderr)
        self.assertTrue(self.widened(), "a refused rollback changes nothing")
        # the gap is only the row: once it is gone the rollback applies
        self.remove_project_assigned_patches()
        self.pg.psql(file=V30_ROLLBACK)
        self.assertFalse(self.widened())

    def test_the_readback_file_reads_ok_after_v30_and_not_before(self) -> None:
        def verdicts() -> list[str]:
            result = self.pg.psql(file=READBACK)
            return [line.rsplit("|", 1)[1] for line in result.stdout.splitlines()
                    if "|" in line and line.rsplit("|", 1)[1] in {"t", "f"}]

        before = verdicts()
        self.assertIn("f", before)  # the v21 check is not the v30 check, and 0.N is refused
        self.pg.psql(file=V30_FORWARD)
        after = verdicts()
        self.assertEqual(set(after), {"t"})
        # 3 code checks + 19 probes + 1 row verdict; one line per probe, so a missing section would show here
        self.assertEqual(len(after), 3 + 19 + 1)


class _LoaderCase(unittest.TestCase):
    """A disposable database seeded with the served clauses of the Alhemo notice's rows."""

    exclude: tuple[str, ...] = ()

    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg, exclude=cls.exclude)
        _seed(cls.pg.dsn, SERVED_WITH_POINT)
        cls.notice = notice_fixture.parse_alhemo()
        cls.dotted = ["4.2", "4.2.7", "4.2.8", "4.2.9"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def compose(self, **options):
        return compose_overlay_release(self.pg.dsn, [self.notice], today=TODAY, **options)

    def serve(self, composition, base_run: str):
        load_overlay_release(self.pg.dsn, composition.release)
        return activate_overlay_release(
            self.pg.dsn,
            run_id=composition.release.run_id,
            expected_sealed_fingerprint=composition.release.sealed_fingerprint,
            expected_base_run_id=base_run,
        )

    def patch_codes(self, composition) -> list[str]:
        return sorted(row["clause_code"] for row in composition.release.rows["clause_patch"] if row["clause_code"] != "9.9")

    def runs(self) -> int:
        with _connect(self.pg.dsn, read_only=True) as connection:
            return connection.execute("SELECT count(*) AS n FROM nhi_rule_history_announced.release_run").fetchone()["n"]


class PointHeldThenSupersededLiveTest(_LoaderCase):
    """Before v30 the notice loads with its point held; after v30 the same notice is superseded to add it."""

    exclude = (V30_FORWARD.name,)

    def test_before_v30_the_dotted_clauses_load_and_after_v30_the_point_is_added(self) -> None:
        # 1. unmigrated: the point waits, the four dotted clauses of the notice compose
        first = self.compose(base_run_id=BASE_RUN)
        self.assertFalse(first.general_principle_patches)
        self.assertEqual(self.patch_codes(first), self.dotted)
        self.assertEqual(
            [(item["clause_code"], item["reason"], item["origin"]) for item in first.blocked_clauses],
            [("0.4", REASON, "new")],
        )
        self.assertEqual(first.status, "passed_with_holds")
        self.assertEqual(first.failures, ())
        (effect,) = [row for row in first.release.rows["notice_effect"] if row["clause_code"] == "0.4"]
        self.assertEqual(effect["projection_status"], "pending_projection")
        self.serve(first, BASE_RUN)
        served = _public_patches(self.pg.dsn)
        self.assertEqual(sorted(served), sorted(["9.9", *self.dotted]))

        # 2. the held point is the only thing that changes nothing: the served notice is carried as it is
        unchanged = self.compose()
        self.assertIsNone(unchanged.release)
        self.assertEqual(unchanged.status, "no_change_with_holds")
        self.assertEqual(
            [(item["clause_code"], item["reason"], item["origin"]) for item in unchanged.blocked_clauses],
            [("0.4", REASON, "carried")],
        )
        self.assertEqual([item["reproduced_clauses"] for item in unchanged.carried_notices], [sorted(self.dotted)])

        # 3. a release that holds the point, loaded into the unmigrated database, is refused before it writes
        with mock.patch.object(announced_release, "general_principle_patches_admitted", return_value=True):
            forced = self.compose(supersede=True)
        self.assertEqual(self.patch_codes(forced), sorted(["0.4", *self.dotted]))
        runs = self.runs()
        with self.assertRaisesRegex(AnnouncedReleaseError, "load refused: .*0.N patch.*migration v30.*nothing was written"):
            load_overlay_release(self.pg.dsn, forced.release)
        self.assertEqual(self.runs(), runs)
        with _connect(self.pg.dsn, read_only=True) as connection:  # the cause: the v21 check is still the live one
            definition = connection.execute(
                "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conname = %s "
                "AND connamespace = 'nhi_rule_history_announced'::regnamespace", (CHECK_NAME,)).fetchone()["definition"]
        self.assertIn(V21_PATTERN, definition)

        # 4. v30 applied: the same code now sees the point as projectable, and supersedes the served notice
        self.pg.psql(file=V30_FORWARD)
        waiting = self.compose()
        self.assertTrue(waiting.general_principle_patches)
        self.assertIsNone(waiting.release)
        self.assertEqual(
            [(item["reason"], item["clause_codes"]) for item in waiting.dropped_notices],
            [("carried_notice_has_newly_projectable_clauses", ["0.4"])],
        )
        second = self.compose(supersede=True)
        self.assertEqual(self.patch_codes(second), sorted(["0.4", *self.dotted]))
        self.assertEqual(second.blocked_clauses, ())
        self.assertEqual(second.status, "passed")
        (superseded,) = second.superseded_notices
        self.assertEqual((superseded["served_clauses"], superseded["added_clauses"]), (sorted(self.dotted), ["0.4"]))
        # the served dotted patches come back byte for byte
        before = {code: (served[code]["patch_id"], served[code]["source_exact_patch_sha256"]) for code in self.dotted}
        again = {row["clause_code"]: (row["patch_id"], row["source_exact_patch_sha256"])
                 for row in second.release.rows["clause_patch"] if row["clause_code"] in self.dotted}
        self.assertEqual({code: (str(p), h) for code, (p, h) in before.items()},
                         {code: (str(p), h) for code, (p, h) in again.items()})
        self.serve(second, str(first.release.run_id))
        now = _public_patches(self.pg.dsn)
        self.assertEqual(sorted(now), sorted(["9.9", "0.4", *self.dotted]))
        self.assertTrue(now["0.4"]["source_exact_patch_text"].startswith("四、注射藥品之使用原則："))
        self.assertEqual(str(now["0.4"]["effective_from"]), "2026-11-01")
        with _connect(self.pg.dsn, read_only=True) as connection:
            row = connection.execute(
                "SELECT composition_status, predecessor_text_sha256 FROM nhi_rule_history_announced.clause_patch "
                "WHERE run_id = %s AND clause_code = '0.4'", (second.release.run_id,)).fetchone()
        self.assertEqual((row["composition_status"], row["predecessor_text_sha256"]),
                         ("patch_only", sha256_text(SERVED_WITH_POINT["0.4"])))
        self.assertTrue(self.compose().release is None)  # and the notice is carried, nothing held, nothing to add

        # 5. a sealed run holds the 0.N patch now: the migration cannot be rolled back
        refused = self.pg.psql(file=V30_ROLLBACK, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("hold a project-assigned 0.N code", refused.stderr)


class PointAdmittedLiveTest(_LoaderCase):
    """On a database that already has v30 the point is a patch from the first compose."""

    def test_the_point_is_a_patch_from_the_first_compose(self) -> None:
        composition = self.compose(base_run_id=BASE_RUN)
        self.assertTrue(composition.general_principle_patches)
        self.assertEqual(self.patch_codes(composition), sorted(["0.4", *self.dotted]))
        self.assertEqual(composition.blocked_clauses, ())
        self.assertEqual(composition.status, "passed")
        self.serve(composition, BASE_RUN)
        served = _public_patches(self.pg.dsn)
        self.assertEqual(sorted(served), sorted(["9.9", "0.4", *self.dotted]))
        self.assertEqual(served["0.4"]["current_resolution_state"], "verified_scheduled")
        self.assertIsNone(self.compose().release)  # carried, nothing more to add


if __name__ == "__main__":
    unittest.main()
