from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.test_update_queue_recovery_v2 import DisposablePostgres


ROOT = Path(__file__).resolve().parents[1]
EDITION = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_edition_v1.sql"
)
FORWARD_PATH = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_current_source_authority_v16.sql"
)
MIGRATION = (
    FORWARD_PATH
).read_text(encoding="utf-8")
ROLLBACK_PATH = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_current_source_authority_v16.rollback.sql"
)
ROLLBACK = (
    ROLLBACK_PATH
).read_text(encoding="utf-8")
POLICY = json.loads(
    (
        ROOT
        / "docs"
        / "audits"
        / "2026-07-28-current-source-authority-policy.json"
    ).read_text(encoding="utf-8")
)


class CurrentSourceAuthorityTests(unittest.TestCase):
    def test_policy_selects_chapter_page_as_sole_current_authority(self) -> None:
        current = POLICY["canonical_current_source"]
        secondary = POLICY["secondary_source"]
        self.assertEqual(
            current["page_url"],
            "https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html",
        )
        self.assertEqual(current["role"], "sole_current_text_authority")
        self.assertEqual(current["primary_structured_format"], "odt")
        self.assertEqual(
            secondary["role"],
            "non_authoritative_quality_crosscheck",
        )
        self.assertFalse(secondary["mismatch_blocks_current_publication"])

    def test_pg_policy_is_append_only_and_fail_closed(self) -> None:
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS",
            MIGRATION,
        )
        self.assertIn(
            "reject_current_source_authority_mutation",
            MIGRATION,
        )
        self.assertIn(
            "sole_current_text_authority",
            MIGRATION,
        )
        self.assertIn(
            "non_authoritative_quality_crosscheck",
            MIGRATION,
        )
        self.assertIn(
            "whole_mismatch_blocks_current_publication = false",
            MIGRATION,
        )

    def test_rollback_removes_only_policy_objects(self) -> None:
        self.assertIn(
            "DROP TABLE IF EXISTS",
            ROLLBACK,
        )
        self.assertIn(
            "current_source_authority_policy",
            ROLLBACK,
        )
        self.assertNotIn("DROP SCHEMA", ROLLBACK)


class CurrentSourceAuthorityLiveTests(unittest.TestCase):
    def test_forward_policy_immutability_and_scoped_rollback(self) -> None:
        pg = DisposablePostgres()
        try:
            pg.psql(file=EDITION)
            pg.psql(file=FORWARD_PATH)
            authority = pg.psql(
                command="""
SELECT authority_role || ':' || whole_role || ':' ||
       whole_mismatch_blocks_current_publication::text
FROM nhi_rule_history_edition.v_current_source_authority;
"""
            ).stdout.strip()
            self.assertEqual(
                authority,
                "sole_current_text_authority:"
                "non_authoritative_quality_crosscheck:false",
            )
            with self.assertRaises(AssertionError):
                pg.psql(
                    command="""
UPDATE nhi_rule_history_edition.current_source_authority_policy
SET whole_mismatch_blocks_current_publication = true
WHERE policy_version = 1;
"""
                )
            pg.psql(file=ROLLBACK_PATH)
            remaining = pg.psql(
                command="""
SELECT count(*)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'nhi_rule_history_edition'
  AND c.relname = 'current_source_authority_policy';
"""
            ).stdout.strip()
            self.assertEqual(remaining, "0")
        finally:
            pg.close()


if __name__ == "__main__":
    unittest.main()
