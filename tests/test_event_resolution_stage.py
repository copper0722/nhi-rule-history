from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history import event_resolution_stage
from nhi_rule_history.event_resolution_stage import (
    EventResolutionStageError,
    load_event_resolution_stage,
    prepare_event_resolution_stage,
)


ROOT = Path(__file__).resolve().parents[1]
FORWARD = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_event_resolution_stage.sql"
)
ROLLBACK = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-27_nhi_rule_history_event_resolution_stage.rollback.sql"
)


def annotation(
    annotation_id: str = "ann-1",
    *,
    effective_date: str | None = "2024-06-01",
    normalization_status: str = "normalized",
    designation: str | None = "9.4.1",
    designation_omitted: bool = False,
    multiple_clause_ambiguity: bool = False,
    locator: object = None,
) -> dict[str, object]:
    return {
        "annotation_id": annotation_id,
        "article_id": "article-9.4.1",
        "normalized_iso_candidate": effective_date,
        "normalization_status": normalization_status,
        "source_designation_raw": designation,
        "designation_omitted": designation_omitted,
        "multiple_clause_ambiguity": multiple_clause_ambiguity,
        "source_locator": (
            locator
            if locator is not None
            else {"char_start": 100, "char_end": 108}
        ),
    }


def official(
    event_id: str = "event-A",
    effect_id: str = "effect-A",
    *,
    effective_date: str | None = "2024-06-01",
    designation: str | None = "9.4.1",
    designation_omitted: bool = False,
    multiple_clause_ambiguity: bool = False,
    omitted_text_present: bool = False,
    locator: object = None,
) -> dict[str, object]:
    return {
        "official_event_id": event_id,
        "official_effect_id": effect_id,
        "effective_date": effective_date,
        "source_designation_raw": designation,
        "designation_omitted": designation_omitted,
        "multiple_clause_ambiguity": multiple_clause_ambiguity,
        "omitted_text_present": omitted_text_present,
        "source_locator": (
            locator
            if locator is not None
            else {
                "detail_url": "https://example.invalid/official/event-A",
                "artifact_sha256": "a" * 64,
                "paragraph": 3,
            }
        ),
    }


def outcome_for(material, annotation_id: str = "ann-1"):
    return next(
        row
        for row in material.outcomes
        if row["annotation_id"] == annotation_id
    )


class EventResolutionRulesTests(unittest.TestCase):
    def test_one_exact_date_compatible_designation_is_candidate_only(self):
        material = prepare_event_resolution_stage(
            [annotation()], [official()]
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "resolved_candidate")
        self.assertEqual(outcome["eligible_candidate_count"], 1)
        self.assertIsNotNone(outcome["selected_candidate_id"])
        self.assertFalse(outcome["canonical_history_written"])
        self.assertEqual(len(material.candidates), 1)
        self.assertTrue(material.candidates[0]["eligible"])
        self.assertFalse(
            material.candidates[0]["canonical_history_written"]
        )

    def test_designation_normalization_accepts_terminal_numeric_punctuation(
        self,
    ):
        material = prepare_event_resolution_stage(
            [annotation(designation=" ９．４．１ ")],
            [official(designation="9.4.1")],
        )
        self.assertEqual(
            outcome_for(material)["resolution_status"],
            "resolved_candidate",
        )
        punctuation_change = prepare_event_resolution_stage(
            [annotation(designation="9.4.1.")],
            [official(designation="9.4.1")],
        )
        self.assertEqual(
            outcome_for(punctuation_change)["resolution_status"],
            "resolved_candidate",
        )
        internal_change = prepare_event_resolution_stage(
            [annotation(designation="9.4.10")],
            [official(designation="9.4.1")],
        )
        self.assertEqual(
            outcome_for(internal_change)["resolution_status"],
            "no_match",
        )

    def test_multiple_official_events_are_ambiguous(self):
        material = prepare_event_resolution_stage(
            [annotation()],
            [
                official("event-A", "effect-A"),
                official("event-B", "effect-B"),
            ],
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "ambiguous")
        self.assertIn("multiple_official_events", outcome["reason_codes"])
        self.assertEqual(outcome["distinct_event_count"], 2)
        self.assertIsNone(outcome["selected_candidate_id"])

    def test_multiple_effects_in_one_event_are_also_ambiguous(self):
        material = prepare_event_resolution_stage(
            [annotation()],
            [
                official("event-A", "effect-A"),
                official("event-A", "effect-B"),
            ],
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "ambiguous")
        self.assertIn(
            "multiple_official_event_effects", outcome["reason_codes"]
        )

    def test_explicitly_incompatible_designation_is_no_match_but_preserved(
        self,
    ):
        material = prepare_event_resolution_stage(
            [annotation()], [official(designation="9.4.2")]
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "no_match")
        self.assertIn(
            "no_compatible_source_designation", outcome["reason_codes"]
        )
        self.assertEqual(len(material.candidates), 1)
        self.assertEqual(
            material.candidates[0]["designation_compatibility"],
            "incompatible",
        )
        self.assertFalse(material.candidates[0]["eligible"])

    def test_no_exact_date_is_no_match(self):
        material = prepare_event_resolution_stage(
            [annotation()], [official(effective_date="2024-06-02")]
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "no_match")
        self.assertEqual(outcome["candidate_count"], 0)
        self.assertIn(
            "no_official_effect_on_exact_date", outcome["reason_codes"]
        )

    def test_invalid_annotation_date_is_invalid_not_a_match(self):
        material = prepare_event_resolution_stage(
            [
                annotation(
                    effective_date=None,
                    normalization_status="invalid_calendar_date",
                )
            ],
            [official()],
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "invalid")
        self.assertEqual(outcome["candidate_count"], 0)
        self.assertIn("annotation_date_invalid", outcome["reason_codes"])

    def test_claimed_normalized_but_non_iso_date_is_invalid(self):
        material = prepare_event_resolution_stage(
            [annotation(effective_date="2024-02-30")], [official()]
        )
        self.assertEqual(
            outcome_for(material)["resolution_status"], "invalid"
        )

    def test_omitted_or_multiple_clause_designation_is_ambiguous(self):
        cases = (
            annotation(designation=None, designation_omitted=True),
            annotation(multiple_clause_ambiguity=True),
        )
        for row in cases:
            with self.subTest(row=row):
                material = prepare_event_resolution_stage(
                    [row], [official()]
                )
                self.assertEqual(
                    outcome_for(material)["resolution_status"],
                    "ambiguous",
                )
                self.assertFalse(material.candidates[0]["eligible"])

    def test_unscoped_official_effect_is_ambiguous_not_no_match(self):
        cases = (
            official(designation=None, designation_omitted=True),
            official(multiple_clause_ambiguity=True),
            official(omitted_text_present=True),
        )
        for row in cases:
            with self.subTest(row=row):
                material = prepare_event_resolution_stage(
                    [annotation()], [row]
                )
                outcome = outcome_for(material)
                self.assertEqual(
                    outcome["resolution_status"], "ambiguous"
                )
                self.assertIn(
                    "indeterminate_designation_scope",
                    outcome["reason_codes"],
                )

    def test_missing_annotation_or_official_locator_is_ambiguous(self):
        missing_annotation_locator = annotation(locator={})
        material = prepare_event_resolution_stage(
            [missing_annotation_locator], [official()]
        )
        self.assertEqual(
            outcome_for(material)["resolution_status"], "ambiguous"
        )
        self.assertIn(
            "annotation_source_locator_missing",
            outcome_for(material)["reason_codes"],
        )

        missing_official_locator = official(locator={})
        material = prepare_event_resolution_stage(
            [annotation()], [missing_official_locator]
        )
        self.assertEqual(
            outcome_for(material)["resolution_status"], "ambiguous"
        )
        self.assertIn(
            "official_source_locator_missing",
            outcome_for(material)["reason_codes"],
        )

    def test_unscoped_second_event_prevents_single_candidate_resolution(self):
        material = prepare_event_resolution_stage(
            [annotation()],
            [
                official("event-A", "effect-A"),
                official(
                    "event-B",
                    "effect-B",
                    designation=None,
                    designation_omitted=True,
                ),
            ],
        )
        outcome = outcome_for(material)
        self.assertEqual(outcome["resolution_status"], "ambiguous")
        self.assertIn("multiple_official_events", outcome["reason_codes"])


class EventResolutionMaterialTests(unittest.TestCase):
    def test_order_independent_deterministic_run_and_fingerprints(self):
        annotations = [
            annotation("ann-2", effective_date="2024-06-02"),
            annotation("ann-1"),
        ]
        officials = [
            official("event-B", "effect-B", effective_date="2024-06-02"),
            official("event-A", "effect-A"),
        ]
        first = prepare_event_resolution_stage(annotations, officials)
        second = prepare_event_resolution_stage(
            reversed(annotations), reversed(officials)
        )
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.annotations, second.annotations)
        self.assertEqual(
            first.official_observations, second.official_observations
        )
        self.assertEqual(first.candidates, second.candidates)
        self.assertEqual(first.outcomes, second.outcomes)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(first.output_fingerprint, second.output_fingerprint)
        self.assertEqual(first.sealed_fingerprint, second.sealed_fingerprint)

    def test_status_counts_cover_all_four_outcomes_and_never_history(self):
        annotations = [
            annotation("resolved"),
            annotation("ambiguous", multiple_clause_ambiguity=True),
            annotation("no-match", effective_date="2024-06-03"),
            annotation(
                "invalid",
                effective_date=None,
                normalization_status="invalid_calendar_date",
            ),
        ]
        material = prepare_event_resolution_stage(
            annotations, [official()]
        )
        self.assertEqual(
            material.expected_counts,
            {
                "annotation_observation": 4,
                "official_event_effect_observation": 1,
                "candidate_observation": 2,
                "resolution_outcome": 4,
                "resolved_candidate": 1,
                "ambiguous": 1,
                "no_match": 1,
                "invalid": 1,
                "canonical_history_written": 0,
            },
        )
        self.assertTrue(
            all(
                row["canonical_history_written"] is False
                for row in (*material.candidates, *material.outcomes)
            )
        )

    def test_exact_caller_evidence_change_changes_run_identity(self):
        baseline = prepare_event_resolution_stage(
            [annotation()], [official()]
        )
        changed = official()
        changed["source_locator"] = {
            **changed["source_locator"],
            "paragraph": 4,
        }
        revised = prepare_event_resolution_stage(
            [annotation()], [changed]
        )
        self.assertNotEqual(baseline.run_id, revised.run_id)

    def test_duplicate_identities_fail_closed(self):
        with self.assertRaisesRegex(
            EventResolutionStageError, "duplicate annotation_id"
        ):
            prepare_event_resolution_stage(
                [annotation(), annotation()], [official()]
            )
        with self.assertRaisesRegex(
            EventResolutionStageError,
            "duplicate official event/effect identity",
        ):
            prepare_event_resolution_stage(
                [annotation()], [official(), official()]
            )

    def test_invalid_field_types_fail_closed(self):
        bad = annotation()
        bad["multiple_clause_ambiguity"] = "false"
        with self.assertRaisesRegex(
            EventResolutionStageError,
            "multiple_clause_ambiguity must be boolean",
        ):
            prepare_event_resolution_stage([bad], [official()])

    def test_public_load_uses_apply_then_fresh_verification(self):
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

        def fake_verify(run_id, *, conninfo, connect, expected):
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
                "canonical_history_written": False,
            }

        with (
            mock.patch.object(
                event_resolution_stage,
                "_apply_material",
                side_effect=fake_apply,
            ) as apply,
            mock.patch.object(
                event_resolution_stage,
                "verify_loaded_event_resolution_stage",
                side_effect=fake_verify,
            ) as verify,
        ):
            result = load_event_resolution_stage(
                [annotation()],
                [official()],
                conninfo="fixture-dsn",
                connect=connector,
            )
        self.assertFalse(result["replayed"])
        self.assertEqual(len(opened), 2)
        self.assertIsNot(opened[0], opened[1])
        apply.assert_called_once()
        verify.assert_called_once()


class EventResolutionMigrationTests(unittest.TestCase):
    def test_migration_is_isolated_append_only_and_stage_only(self):
        sql = FORWARD.read_text(encoding="utf-8")
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")
        self.assertIn(
            "managed=nhi_rule_history_event_resolution_stage/v1", sql
        )
        for relation in (
            "resolution_run",
            "annotation_observation",
            "official_event_effect_observation",
            "candidate_observation",
            "resolution_outcome",
            "v_resolution_status_counts",
        ):
            self.assertIn(relation, sql)
        for status in (
            "resolved_candidate",
            "ambiguous",
            "no_match",
            "invalid",
        ):
            self.assertIn(status, sql)
        self.assertIn("canonical_history_written", sql)
        self.assertIn("CHECK (canonical_history_written = false)", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("BEFORE TRUNCATE", sql)
        self.assertIn(
            "REVOKE ALL ON SCHEMA\n"
            "  nhi_rule_history_event_resolution_stage FROM PUBLIC",
            sql,
        )
        self.assertNotRegex(
            sql,
            r"(?im)^\s*(INSERT|UPDATE|DELETE)\s+"
            r"(?:INTO\s+|FROM\s+)?tw_drug(?:\.|\s)",
        )

    def test_rollback_is_managed_restrict_only(self):
        sql = ROLLBACK.read_text(encoding="utf-8")
        code = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        self.assertIn(
            "managed=nhi_rule_history_event_resolution_stage/v1", sql
        )
        self.assertNotRegex(code, r"(?i)\bCASCADE\b")
        self.assertIn(
            "nhi_rule_history_event_resolution_stage RESTRICT", sql
        )
        self.assertRegex(sql, r"(?m)^BEGIN;$")
        self.assertRegex(sql, r"(?m)^COMMIT;$")


class EventResolutionLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
    def run_psql(cls, path: Path):
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

    def test_live_apply_replay_view_append_only_and_rollback(self):
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*) FROM pg_namespace
                    WHERE nspname =
                      'nhi_rule_history_event_resolution_stage'
                    """
                )
                if cursor.fetchone()[0] != 0:
                    self.fail(
                        "test DSN already has event resolution stage schema"
                    )
        applied = False
        try:
            self.run_psql(FORWARD)
            applied = True
            self.run_psql(FORWARD)
            annotations = [
                annotation("resolved"),
                annotation("ambiguous", multiple_clause_ambiguity=True),
                annotation("no-match", effective_date="2024-06-03"),
                annotation(
                    "invalid",
                    effective_date=None,
                    normalization_status="invalid_calendar_date",
                ),
            ]
            first = load_event_resolution_stage(
                annotations, [official()], conninfo=self.dsn
            )
            second = load_event_resolution_stage(
                reversed(annotations), [official()], conninfo=self.dsn
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
                        SELECT annotation_count,
                               resolved_candidate_count,
                               ambiguous_count,
                               no_match_count,
                               invalid_count,
                               canonical_history_written
                        FROM
                          nhi_rule_history_event_resolution_stage
                          .v_resolution_status_counts
                        """
                    )
                    self.assertEqual(
                        cursor.fetchone(), (4, 1, 1, 1, 1, False)
                    )
                    with self.assertRaises(psycopg.Error):
                        cursor.execute(
                            """
                            UPDATE
                              nhi_rule_history_event_resolution_stage
                              .resolution_run
                            SET state = 'sealed'
                            """
                        )
                    connection.rollback()
        finally:
            if applied:
                self.run_psql(ROLLBACK)


if __name__ == "__main__":
    unittest.main()
