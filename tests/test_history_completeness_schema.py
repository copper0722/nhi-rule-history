from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PG_SCHEMA = (ROOT / "database" / "postgresql-schema.sql").read_text(
    encoding="utf-8"
)
SQLITE_SCHEMA = (ROOT / "database" / "sqlite-schema.sql").read_text(
    encoding="utf-8"
)


class HistoryCompletenessSchemaTests(unittest.TestCase):
    def test_pg_and_sqlite_expose_same_completeness_entities(self) -> None:
        for table in (
            "rule_navigation_assignment",
            "source_date_annotation",
            "source_date_annotation_effect",
            "rule_history_coverage",
        ):
            self.assertIn(f"CREATE TABLE {table}", PG_SCHEMA)
            self.assertIn(f"CREATE TABLE {table}", SQLITE_SCHEMA)

        for field in (
            "source_designation_raw",
            "navigation_code",
            "code_origin",
            "annotation_count",
            "resolved_annotation_count",
            "verified_transition_count",
            "source_universe_closed",
            "cumulative_anchor_parity",
            "completion_status",
        ):
            self.assertIn(field, PG_SCHEMA)
            self.assertIn(field, SQLITE_SCHEMA)

    def test_sqlite_schema_builds_and_completion_gate_fails_closed(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            """
            INSERT INTO dataset_release (
                release_id, release_kind, official_label, source_page_url,
                manifest_sha256, status, created_at
            ) VALUES (?, 'annual_full', 'test', 'https://example.invalid',
                      ?, 'verified', '2026-07-27T00:00:00Z')
            """,
            ("release-1", "0" * 64),
        )
        conn.execute(
            """
            INSERT INTO rule_identity (
                rule_id, canonical_slug, identity_status, first_seen_release_id
            ) VALUES ('rule-1', 'rule-1', 'active', 'release-1')
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO rule_history_coverage (
                    coverage_id, rule_id, declared_cut_release_id,
                    annotation_count, resolved_annotation_count,
                    verified_transition_count, snapshot_count, direct_edge_count,
                    unresolved_gap_count, source_universe_closed,
                    cumulative_anchor_parity, completion_status,
                    gap_reasons_json, assessed_at
                ) VALUES (
                    'coverage-invalid', 'rule-1', 'release-1',
                    2, 1, 1, 2, 1, 1, 0, 0,
                    'complete_to_declared_cut', '["missing event"]',
                    '2026-07-27T00:00:00Z'
                )
                """
            )

        conn.execute(
            """
            INSERT INTO rule_history_coverage (
                coverage_id, rule_id, declared_cut_release_id,
                annotation_count, resolved_annotation_count,
                verified_transition_count, snapshot_count, direct_edge_count,
                unresolved_gap_count, source_universe_closed,
                cumulative_anchor_parity, completion_status,
                gap_reasons_json, assessed_at
            ) VALUES (
                'coverage-blocked', 'rule-1', 'release-1',
                2, 1, 1, 2, 1, 1, 0, 0,
                'blocked', '["missing event"]',
                '2026-07-27T00:00:00Z'
            )
            """
        )
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_project_assigned_chapter_zero_is_explicit(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            """
            INSERT INTO dataset_release (
                release_id, release_kind, official_label, source_page_url,
                manifest_sha256, status, created_at
            ) VALUES (?, 'annual_full', 'test', 'https://example.invalid',
                      ?, 'verified', '2026-07-27T00:00:00Z')
            """,
            ("release-1", "0" * 64),
        )
        conn.execute(
            """
            INSERT INTO rule_identity (
                rule_id, canonical_slug, identity_status, first_seen_release_id
            ) VALUES ('rule-1', 'rule-1', 'active', 'release-1')
            """
        )
        conn.execute(
            """
            INSERT INTO rule_navigation_assignment (
                navigation_assignment_id, rule_id, source_designation_raw,
                navigation_code, code_origin, display_label, sort_order,
                evidence_locator
            ) VALUES (
                'nav-1', 'rule-1', '通則', 'chapter:00',
                'project_assigned', '通則', 0, '{}'
            )
            """
        )
        row = conn.execute(
            """
            SELECT source_designation_raw, navigation_code, code_origin,
                   display_label
            FROM rule_navigation_assignment
            """
        ).fetchone()
        self.assertEqual(row, ("通則", "chapter:00", "project_assigned", "通則"))


if __name__ == "__main__":
    unittest.main()
