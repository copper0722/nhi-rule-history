#!/usr/bin/env python3
"""Load the sealed 2026-09-01 dyslipidemia amendment patch.

The subscriber sync runs this on every tick with activation.  The loader
reads the served announced run and re-activates its own run when the served
run lacks the 2.6.1 composed version, while the overlay loader activates
composite runs under the global announced lock.  The whole call therefore
runs in one database session that holds that lock from before the served run
is read until after it is (re-)activated, so an overlay activation waits
instead of being undone by a read it made stale.  The lock is taken here, not
in the loader module, because that module's code hash is part of the 2.6.1
run identities.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg

from nhi_rule_history.announced_dyslipidemia import (
    load_announced_dyslipidemia,
)
from nhi_rule_history.announced_release import GLOBAL_LOCK_KEY


class LockedSession:
    """One session, holding the global announced lock, for every step.

    Used as the loader's ``connect``: each ``with connect(dsn)`` block is a
    transaction of this session, committed on success and rolled back on
    error, and the session-level lock outlives them all until ``close``.
    """

    def __init__(self, dsn: str) -> None:
        self.connection = psycopg.connect(dsn)
        try:
            self.connection.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                (GLOBAL_LOCK_KEY,),
            )
            self.connection.commit()
        except BaseException:
            self.connection.close()
            raise

    def __call__(self, dsn: str) -> "LockedSession":
        return self

    def __enter__(self) -> Any:
        return self.connection

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        return False

    def close(self) -> None:
        # Ending the session releases the lock.
        self.connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load the sealed 2026-09-01 dyslipidemia amendment patch"
    )
    parser.add_argument("odt_path", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args(argv)
    session = LockedSession(args.dsn)
    try:
        result = load_announced_dyslipidemia(
            args.odt_path,
            conninfo=args.dsn,
            connect=session,
            activate=not args.no_activate,
        )
    finally:
        session.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
