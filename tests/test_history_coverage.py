from __future__ import annotations

import unittest

from nhi_rule_history.history_coverage import (
    AUDIT_SCHEMA,
    HistoryCoverageError,
    audit_document,
    audit_history_coverage,
)


def ready_rule(rule_id: str = "rule-1") -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "identity_status": "active",
        "direct_edge_count": 1,
        "unresolved_gap_count": 0,
        "cumulative_anchor_parity": True,
    }


def versions(rule_id: str = "rule-1") -> list[dict[str, object]]:
    return [
        {
            "snapshot_id": f"{rule_id}-v1",
            "rule_id": rule_id,
            "effective_from": "2023-06-01",
            "normalized_text": "old text",
        },
        {
            "snapshot_id": f"{rule_id}-v2",
            "rule_id": rule_id,
            "effective_from": "2024-06-01",
            "normalized_text": "new text",
        },
    ]


def annotations(rule_id: str = "rule-1") -> list[dict[str, object]]:
    return [
        {
            "annotation_id": f"{rule_id}-a1",
            "snapshot_id": f"{rule_id}-v2",
            "iso_date_candidate": "2023-06-01",
            "resolution_status": "transition_verified",
        },
        {
            "annotation_id": f"{rule_id}-a2",
            "snapshot_id": f"{rule_id}-v2",
            "iso_date_candidate": "2024-06-01",
            "resolution_status": "transition_verified",
        },
    ]


class HistoryCoverageTests(unittest.TestCase):
    def test_matching_date_sets_are_not_enough_without_explicit_readiness(self) -> None:
        rule = {"rule_id": "rule-1", "identity_status": "active"}
        report = audit_history_coverage(
            [rule],
            versions(),
            annotations(),
            declared_cut="2026-07-27",
        )
        result = report["rules"][0]
        self.assertEqual(
            result["declared_source_date_set"],
            ["2023-06-01", "2024-06-01"],
        )
        self.assertEqual(
            result["version_effective_date_set"],
            ["2023-06-01", "2024-06-01"],
        )
        self.assertFalse(result["complete_to_declared_cut"])
        self.assertIn("source_universe_not_closed", result["gap_reasons"])
        self.assertIn("direct_edge_count_not_provided", result["gap_reasons"])
        self.assertIn(
            "cumulative_anchor_parity_not_passed", result["gap_reasons"]
        )

    def test_explicitly_ready_rule_can_complete(self) -> None:
        report = audit_history_coverage(
            [ready_rule()],
            versions(),
            annotations(),
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )
        result = report["rules"][0]
        self.assertTrue(result["complete_to_declared_cut"])
        self.assertEqual(result["completion_status"], "complete_to_declared_cut")
        self.assertEqual(result["gap_reasons"], [])
        self.assertTrue(report["canonical_history_claim"])

    def test_unmatched_duplicate_and_identical_text_flags_are_deterministic(
        self,
    ) -> None:
        duplicate_versions = [
            {
                "snapshot_id": "v1",
                "rule_id": "rule-1",
                "effective_date": "2023-06-01",
                "normalized_text": "same",
            },
            {
                "snapshot_id": "v1-copy",
                "rule_id": "rule-1",
                "effective_date": "2023-06-01",
                "normalized_text": "same",
            },
            {
                "snapshot_id": "v2",
                "rule_id": "rule-1",
                "effective_date": "2026-05-18",
                "normalized_text": "same",
            },
        ]
        date_annotations = [
            {
                "annotation_id": "a1",
                "rule_id": "rule-1",
                "iso_date_candidate": "2023-06-01",
                "resolution_status": "transition_verified",
            },
            {
                "annotation_id": "a2",
                "rule_id": "rule-1",
                "iso_date_candidate": "2024-06-01",
                "resolution_status": "transition_verified",
            },
        ]
        rule = ready_rule()
        rule["direct_edge_count"] = 2
        result = audit_history_coverage(
            [rule],
            duplicate_versions,
            date_annotations,
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )["rules"][0]

        self.assertEqual(
            result["unmatched"],
            {
                "source_dates_without_version": ["2024-06-01"],
                "version_dates_without_source_annotation": ["2026-05-18"],
            },
        )
        self.assertEqual(
            result["duplicates"]["effective_date_groups"][0]["version_ids"],
            ["v1", "v1-copy"],
        )
        self.assertEqual(
            result["identical_text_at_different_dates"][0]["dates"],
            ["2023-06-01", "2026-05-18"],
        )
        self.assertTrue(result["flags"]["has_unmatched_dates"])
        self.assertTrue(result["flags"]["has_duplicate_effective_dates"])
        self.assertTrue(
            result["flags"]["has_identical_text_at_different_dates"]
        )
        self.assertFalse(result["complete_to_declared_cut"])

    def test_unresolved_or_missing_annotation_date_fails_closed(self) -> None:
        broken_annotations = annotations()
        broken_annotations[1] = {
            "annotation_id": "a-broken",
            "snapshot_id": "rule-1-v2",
            "iso_date_candidate": None,
            "resolution_status": "unresolved_event",
        }
        result = audit_history_coverage(
            [ready_rule()],
            versions(),
            broken_annotations,
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )["rules"][0]
        self.assertIn(
            "annotation_iso_date_missing_or_invalid", result["gap_reasons"]
        )
        self.assertIn("source_annotation_unresolved", result["gap_reasons"])
        self.assertFalse(result["complete_to_declared_cut"])

    def test_post_cut_dates_are_reported_but_not_compared(self) -> None:
        later_versions = versions() + [
            {
                "snapshot_id": "rule-1-v3",
                "rule_id": "rule-1",
                "effective_from": "2027-01-01",
                "normalized_text": "future",
            }
        ]
        rule = ready_rule()
        result = audit_history_coverage(
            [rule],
            later_versions,
            annotations(),
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )["rules"][0]
        self.assertEqual(result["post_cut"]["version_dates"], ["2027-01-01"])
        self.assertEqual(result["counts"]["snapshot_count"], 2)
        self.assertTrue(result["complete_to_declared_cut"])

    def test_chapter_zero_requires_project_assigned_tongze_metadata(self) -> None:
        chapter_zero_rule = ready_rule()
        chapter_zero_rule["chapter_id"] = 0
        valid_navigation = [
            {
                "navigation_assignment_id": "nav-1",
                "rule_id": "rule-1",
                "source_designation_raw": "通則",
                "navigation_code": "chapter:00",
                "code_origin": "project_assigned",
                "display_label": "通則",
            }
        ]
        report = audit_history_coverage(
            [chapter_zero_rule],
            versions(),
            annotations(),
            navigation_assignments=valid_navigation,
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )
        chapter_zero = report["rules"][0]["navigation"]["chapter_zero"]
        self.assertEqual(chapter_zero["navigation_code"], "chapter:00")
        self.assertEqual(chapter_zero["code_origin"], "project_assigned")
        self.assertEqual(chapter_zero["source_designation_raw"], "通則")
        self.assertTrue(report["rules"][0]["complete_to_declared_cut"])

        invalid_navigation = [
            {
                "navigation_assignment_id": "nav-bad",
                "rule_id": "rule-1",
                "source_designation_raw": "第 0 章",
                "navigation_code": "chapter:00",
                "code_origin": "official_source",
                "display_label": "第0章",
            }
        ]
        invalid = audit_history_coverage(
            [chapter_zero_rule],
            versions(),
            annotations(),
            navigation_assignments=invalid_navigation,
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )["rules"][0]
        self.assertIn(
            "chapter_zero_not_project_assigned", invalid["gap_reasons"]
        )
        self.assertIn(
            "chapter_zero_presented_as_official_chapter",
            invalid["gap_reasons"],
        )
        self.assertFalse(invalid["complete_to_declared_cut"])

        missing = audit_history_coverage(
            [chapter_zero_rule],
            versions(),
            annotations(),
            declared_cut="2026-07-27",
            source_universe_closed=True,
        )["rules"][0]
        self.assertIn(
            "chapter_zero_navigation_assignment_missing",
            missing["gap_reasons"],
        )

    def test_document_entry_point_and_orphans_block_dataset_claim(self) -> None:
        report = audit_document(
            {
                "declared_cut": "2026-07-27",
                "source_universe_closed": True,
                "rules": [ready_rule()],
                "versions": versions()
                + [
                    {
                        "snapshot_id": "orphan-v1",
                        "rule_id": "missing-rule",
                        "effective_from": "2024-01-01",
                        "normalized_text": "orphan",
                    }
                ],
                "source_date_annotations": annotations(),
                "navigation_assignments": [],
            }
        )
        self.assertEqual(report["schema"], AUDIT_SCHEMA)
        self.assertEqual(report["orphan_version_ids"], ["orphan-v1"])
        self.assertFalse(report["canonical_history_claim"])

    def test_invalid_document_and_duplicate_rule_ids_raise(self) -> None:
        with self.assertRaises(HistoryCoverageError):
            audit_document({})
        with self.assertRaises(HistoryCoverageError):
            audit_history_coverage(
                [{"rule_id": "r"}, {"rule_id": "r"}],
                [],
                [],
                declared_cut="2026-07-27",
            )


if __name__ == "__main__":
    unittest.main()
