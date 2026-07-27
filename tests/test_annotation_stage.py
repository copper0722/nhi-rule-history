from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history import annotation_stage
from nhi_rule_history.annotation_stage import (
    AnnotationStageError,
    extract_roc_date_markers,
    load_annotation_stage,
    prepare_annotation_stage,
)


ROOT = Path(__file__).resolve().parents[1]
FORWARD = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_annotation_stage.sql"
)
ROLLBACK = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_annotation_stage.rollback.sql"
)


def fixture_records():
    return [
        {
            "article_id": 20,
            "article_num": "9.2.1",
            "full_text": "修訂（112/6/1、113/06/01），另於 113／7／2 生效。",
            "source_identity": {
                "schema": "legacy.rule_articles",
                "primary_key": 20,
                "snapshot": "2026-07-27",
            },
        },
        {
            "article_id": "10",
            "article_num": "通則",
            "full_text": "本條沒有日期註記。",
            "source_identity": "legacy-export:rule_articles:10",
        },
    ]


class RocDateExtractionTests(unittest.TestCase):
    def test_multiple_dates_in_one_parenthesis_are_individual_exact_rows(
        self,
    ) -> None:
        text = "修訂（112/6/1、113/06/01），其餘不變"
        markers = extract_roc_date_markers(text)
        self.assertEqual(
            [row["raw_expression"] for row in markers],
            ["112/6/1", "113/06/01"],
        )
        self.assertEqual(
            [row["normalized_iso_candidate"] for row in markers],
            ["2023-06-01", "2024-06-01"],
        )
        for ordinal, marker in enumerate(markers):
            self.assertEqual(marker["marker_ordinal"], ordinal)
            self.assertEqual(
                text[marker["char_start"] : marker["char_end"]],
                marker["raw_expression"],
            )
            self.assertEqual(
                marker["raw_expression_sha256"],
                hashlib.sha256(
                    marker["raw_expression"].encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                marker["resolution_status"], "unresolved_event"
            )

    def test_spacing_and_fullwidth_slashes_remain_verbatim(self) -> None:
        text = "日期 113 ／ 06／ 01，另記 2024/06/01。"
        markers = extract_roc_date_markers(text)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["raw_expression"], "113 ／ 06／ 01")
        self.assertEqual(
            markers[0]["normalized_iso_candidate"], "2024-06-01"
        )

    def test_invalid_calendar_marker_is_preserved_not_dropped(self) -> None:
        markers = extract_roc_date_markers(
            "誤植（113/13/40、113/00/01、000/1/1）"
        )
        self.assertEqual(len(markers), 3)
        for marker in markers:
            self.assertIsNone(marker["normalized_iso_candidate"])
            self.assertEqual(
                marker["normalization_status"], "invalid_calendar_date"
            )
            self.assertEqual(
                marker["unresolved_reason"], "invalid_roc_calendar_date"
            )


class AnnotationMaterialTests(unittest.TestCase):
    def test_preparation_is_order_independent_and_stage_only(self) -> None:
        first = prepare_annotation_stage(fixture_records())
        second = prepare_annotation_stage(reversed(fixture_records()))
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(first.output_fingerprint, second.output_fingerprint)
        self.assertEqual(first.sealed_fingerprint, second.sealed_fingerprint)
        self.assertEqual(first.articles, second.articles)
        self.assertEqual(first.annotations, second.annotations)
        self.assertEqual(
            first.expected_counts,
            {
                "legacy_article_observation": 2,
                "date_annotation": 3,
                "article_with_annotation": 1,
                "normalized_annotation": 3,
                "unresolved_annotation": 3,
                "coverage_projection": 2,
                "article_annotation_total": 3,
            },
        )
        self.assertTrue(
            all(
                row["resolution_status"] == "unresolved_event"
                for row in first.annotations
            )
        )

    def test_exact_source_or_text_change_changes_run_identity(self) -> None:
        baseline = fixture_records()
        changed_text = [dict(row) for row in baseline]
        changed_text[0]["full_text"] += " "
        changed_source = [dict(row) for row in baseline]
        changed_source[0]["source_identity"] = {
            **baseline[0]["source_identity"],
            "snapshot": "2026-07-28",
        }
        original = prepare_annotation_stage(baseline)
        self.assertNotEqual(
            original.run_id, prepare_annotation_stage(changed_text).run_id
        )
        self.assertNotEqual(
            original.run_id, prepare_annotation_stage(changed_source).run_id
        )

    def test_duplicate_article_identity_fails_closed(self) -> None:
        records = fixture_records()
        records.append(
            {
                **records[0],
                "full_text": "不同內容",
            }
        )
        with self.assertRaisesRegex(
            AnnotationStageError, "duplicate article_id"
        ):
            prepare_annotation_stage(records)

    def test_missing_source_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            AnnotationStageError, "source_identity is required"
        ):
            prepare_annotation_stage(
                [
                    {
                        "article_id": 1,
                        "article_num": "1.1",
                        "full_text": "112/6/1",
                    }
                ]
            )

    def test_public_load_runs_apply_then_fresh_verification(self) -> None:
        opened: list[object] = []

        class Connection:
            def __enter__(self):
                opened.append(self)
                return self

            def __exit__(self, *_args):
                return False

        def connector(_dsn):
            return Connection()

        def fake_apply(material, *, conninfo, connect):
            self.assertEqual(conninfo, "fixture-dsn")
            with connect(conninfo):
                pass
            return False

        def fake_verify(
            run_id, *, conninfo, connect, expected
        ):
            self.assertEqual(run_id, expected.run_id)
            with connect(conninfo):
                pass
            return {
                "run_id": run_id,
                "state": "sealed",
                "counts": expected.expected_counts,
                "table_fingerprints": expected.table_fingerprints,
                "input_fingerprint": expected.input_fingerprint,
                "output_fingerprint": expected.output_fingerprint,
                "sealed_fingerprint": expected.sealed_fingerprint,
            }

        with (
            mock.patch.object(
                annotation_stage,
                "_apply_material",
                side_effect=fake_apply,
            ) as apply,
            mock.patch.object(
                annotation_stage,
                "verify_loaded_annotation_stage",
                side_effect=fake_verify,
            ) as verify,
        ):
            result = load_annotation_stage(
                fixture_records(),
                conninfo="fixture-dsn",
                connect=connector,
            )
        self.assertFalse(result["replayed"])
        self.assertEqual(len(opened), 2)
        self.assertIsNot(opened[0], opened[1])
        apply.assert_called_once()
        verify.assert_called_once()


class AnnotationStageMigrationTests(unittest.TestCase):
    def test_migration_is_isolated_append_only_and_has_coverage_view(
        self,
    ) -> None:
        sql = FORWARD.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn(
            "managed=nhi_rule_history_annotation_stage/v1", sql
        )
        for table in (
            "annotation_run",
            "legacy_article_observation",
            "date_annotation",
        ):
            self.assertIn(
                "nhi_rule_history_annotation_stage." + table, sql
            )
        self.assertIn("v_rule_date_coverage", sql)
        self.assertIn("unresolved_event", sql)
        self.assertIn("raw_expression_sha256", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("BEFORE TRUNCATE", sql)
        self.assertIn(
            "REVOKE ALL ON SCHEMA "
            "nhi_rule_history_annotation_stage FROM PUBLIC",
            sql,
        )
        self.assertNotRegex(
            sql,
            r"(?im)^\s*(INSERT|UPDATE|DELETE)\s+"
            r"(?:INTO\s+|FROM\s+)?tw_drug(?:\.|\s)",
        )

    def test_rollback_is_managed_restrict_only(self) -> None:
        sql = ROLLBACK.read_text(encoding="utf-8")
        code = re.sub(
            r"--.*?$", "", sql, flags=re.MULTILINE
        )
        self.assertIn(
            "managed=nhi_rule_history_annotation_stage/v1", sql
        )
        self.assertNotRegex(code, r"(?i)\bCASCADE\b")
        self.assertIn(
            "DROP SCHEMA nhi_rule_history_annotation_stage RESTRICT",
            sql,
        )
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")


class AnnotationStageLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ.get("NHI_RULE_HISTORY_TEST_DSN")
        if not cls.dsn:
            raise unittest.SkipTest(
                "NHI_RULE_HISTORY_TEST_DSN is not set"
            )
        cls.psql = shutil.which("psql")
        if not cls.psql:
            raise unittest.SkipTest("psql is unavailable")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("psycopg is unavailable") from exc

    @classmethod
    def run_psql(cls, path: Path) -> None:
        result = subprocess.run(
            [
                cls.psql,
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--dbname",
                cls.dsn,
                "--file",
                str(path),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)

    def test_live_apply_replay_fresh_verify_and_rollback(self) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM pg_namespace
                    WHERE nspname =
                      'nhi_rule_history_annotation_stage'
                    """
                )
                if cursor.fetchone()[0] != 0:
                    self.fail(
                        "test DSN already has the annotation stage schema"
                    )
        applied = False
        try:
            self.run_psql(FORWARD)
            applied = True
            self.run_psql(FORWARD)
            first = load_annotation_stage(
                fixture_records(), conninfo=self.dsn
            )
            second = load_annotation_stage(
                reversed(fixture_records()), conninfo=self.dsn
            )
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])
            self.assertEqual(
                first["verification"], second["verification"]
            )
            with psycopg.connect(self.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT count(*), sum(annotation_count)
                        FROM
                          nhi_rule_history_annotation_stage
                          .v_rule_date_coverage
                        """
                    )
                    self.assertEqual(cursor.fetchone(), (2, 3))
                    with self.assertRaises(psycopg.Error):
                        cursor.execute(
                            """
                            UPDATE
                              nhi_rule_history_annotation_stage
                              .annotation_run
                            SET state = 'sealed'
                            """
                        )
                    connection.rollback()
        finally:
            if applied:
                self.run_psql(ROLLBACK)


if __name__ == "__main__":
    unittest.main()
