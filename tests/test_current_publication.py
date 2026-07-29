from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.current_publication import (
    MIGRATION,
    prepare_current_publication,
    semantic_comparison_text,
    version_inventory,
)
from tests.test_current_anchor_clause_parity import (
    _base_artifacts,
    _write_stage,
)


class CurrentPublicationPolicyTests(unittest.TestCase):
    def test_version_inventory_uses_distinct_valid_dates_minimum_one(self) -> None:
        inventory = version_inventory(
            ["2022-01-01", "2022-01-01", "2022-03-01"],
            1,
        )
        self.assertEqual(inventory["valid_distinct_roc_date_count"], 2)
        self.assertEqual(inventory["expected_version_count"], 2)
        self.assertEqual(inventory["missing_version_count"], 1)
        self.assertEqual(
            inventory["inventory_status"],
            "history_full_text_missing",
        )

    def test_known_reconstructed_states_never_produce_negative_missing(self) -> None:
        inventory = version_inventory([], 2)
        self.assertEqual(inventory["expected_version_count"], 1)
        self.assertEqual(inventory["missing_version_count"], 0)
        self.assertTrue(
            inventory["annotation_count_underflows_reconstructed"]
        )
        self.assertEqual(
            inventory["inventory_status"],
            "annotation_count_underflows_reconstructed_evidence",
        )

    def test_semantic_state_matching_ignores_only_declared_classes(self) -> None:
        self.assertEqual(
            semantic_comparison_text("A Ｂ ’ C"),
            semantic_comparison_text("AB'C"),
        )
        self.assertNotEqual(
            semantic_comparison_text("ABC"),
            semantic_comparison_text("ABCD"),
        )

    def test_sealed_structural_stage_materializes_single_clause_rows(self) -> None:
        artifacts = _base_artifacts()
        source_urls = {
            artifact["resource"]: (
                f"https://example.test/{artifact['resource']}.odt"
            )
            for artifact in artifacts
        }
        with tempfile.TemporaryDirectory() as temporary:
            stage = _write_stage(Path(temporary), artifacts)
            material = prepare_current_publication(
                stage,
                source_acquisition_run_id=(
                    "11111111-1111-4111-8111-111111111111"
                ),
                source_urls=source_urls,
                reconstructed_version_counts={"0.1": 2},
            )
        self.assertEqual(material.whole_split_parity_status, "parity_passed")
        self.assertEqual(material.expected_counts["current_clause"], 5)
        self.assertEqual(
            [row["clause_code"] for row in material.clauses],
            ["0.1", "0.2", "1.1", "1.1.1", "1.2"],
        )
        clause_01 = next(
            row for row in material.clauses
            if row["clause_code"] == "0.1"
        )
        self.assertEqual(
            clause_01["inventory_status"],
            "annotation_count_underflows_reconstructed_evidence",
        )
        parent = next(
            row for row in material.clauses
            if row["clause_code"] == "1.1"
        )
        parent_blocks = [
            row for row in material.blocks
            if row["clause_code"] == "1.1"
        ]
        self.assertIn("parent intro", parent["raw_text"])
        self.assertTrue(
            any(row["container"] == "table_cell" for row in parent_blocks)
        )

    def test_migration_guards_loading_and_uses_reversible_activation_log(self) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "publication runs must be inserted in loading state",
            migration,
        )
        self.assertIn(
            "BEFORE INSERT ON nhi_rule_history_publication.publication_run",
            migration,
        )
        activation_table = migration.split(
            "CREATE TABLE nhi_rule_history_publication.publication_activation",
            1,
        )[1].split(");", 1)[0]
        self.assertNotIn("run_id uuid NOT NULL UNIQUE", activation_table)


if __name__ == "__main__":
    unittest.main()
