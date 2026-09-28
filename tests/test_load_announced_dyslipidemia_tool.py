"""The 2.6.1 tick holds the global announced lock across its whole call.

2026-09-28 finding MEDIUM-F: the subscriber sync's 2.6.1 loader read the
served run outside the global lock and later re-activated its own run when
the read did not find the 2.6.1 version.  An overlay activation in between
was undone.  The tick now runs in one session that holds the lock from the
read of the served run until after (re-)activation.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

import psycopg

from nhi_rule_history.announced_release import GLOBAL_LOCK_KEY
from tests import test_update_queue_recovery_v2 as recovery_fixture
from tools import load_announced_dyslipidemia as tool


class TickLockLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def _lock_free(self) -> bool:
        """What an overlay activation, in its own session, would find."""

        with psycopg.connect(self.pg.dsn) as other:
            return other.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                (GLOBAL_LOCK_KEY,),
            ).fetchone()[0]

    def _tick(self, loader) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(
            tool, "load_announced_dyslipidemia", side_effect=loader
        ), contextlib.redirect_stdout(out):
            code = tool.main(["fixture.odt", "--dsn", self.pg.dsn])
        return code, out.getvalue()

    def test_the_lock_spans_the_read_and_the_activation(self) -> None:
        seen: dict[str, object] = {}

        def loader(odt_path, *, conninfo, connect, activate):
            # The loader reads the served run, prepares its material, then
            # inserts and (re-)activates, each in a `with connect()` block.
            with connect(conninfo) as connection:
                seen["read"] = connection.execute(
                    "SELECT pg_backend_pid()"
                ).fetchone()[0]
            seen["free_between"] = self._lock_free()
            with connect(conninfo) as connection:
                seen["write"] = connection.execute(
                    "SELECT pg_backend_pid()"
                ).fetchone()[0]
            return {"state": "sealed", "active": activate}

        code, output = self._tick(loader)
        self.assertEqual((code, json.loads(output)), (0, {"active": True, "state": "sealed"}))
        self.assertFalse(seen["free_between"])
        self.assertEqual(seen["read"], seen["write"])
        self.assertTrue(self._lock_free())

    def test_a_failing_loader_releases_the_lock(self) -> None:
        def loader(odt_path, *, conninfo, connect, activate):
            with connect(conninfo) as connection:
                connection.execute("SELECT 1")
            raise RuntimeError("fixture failure")

        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            self._tick(loader)
        self.assertTrue(self._lock_free())

    def test_separate_sessions_hold_nothing_between_steps(self) -> None:
        # Confusable negative: the loader's own connector, as before, leaves
        # the lock free between the read and the activation.
        with psycopg.connect(self.pg.dsn):
            pass
        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute("SELECT 1")
        self.assertTrue(self._lock_free())


if __name__ == "__main__":
    unittest.main()
