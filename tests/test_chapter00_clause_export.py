from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from nhi_rule_history.clause_history import (
    DIFF_PRESENTATION_VERSION,
    DIFF_VERSION,
    EXTRACTOR_VERSION,
    READER_ENRICHMENT_VERSION,
    build_sqlite,
    semantic_diff_presentation,
)


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data" / "templates" / "chapter-00-clauses"
MANIFEST = json.loads((EXPORT / "manifest.json").read_text(encoding="utf-8"))
MIGRATION = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_clause_v1.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_clause_v1.rollback.sql"
).read_text(encoding="utf-8")


def read_jsonl(name: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (EXPORT / f"{name}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


class Chapter00ClauseExportTests(unittest.TestCase):
    def test_manifest_hashes_counts_and_authority(self) -> None:
        self.assertTrue(MANIFEST["postgresql_is_authority"])
        self.assertEqual(
            MANIFEST["generated_from"],
            "PostgreSQL nhi_rule_history_clause",
        )
        self.assertEqual(MANIFEST["canonical_version_unit"], "single_clause")
        self.assertFalse(MANIFEST["legal_history_complete"])
        self.assertEqual(
            MANIFEST["counts"],
            {
                "chapter": 1,
                "clause": 12,
                "agent_history_summary": 1,
                "clause_condition_marker": 21,
                "clause_diff_hunk": 26,
                "clause_semantic_tag": 66,
                "clause_semantic_tag_atc": 51,
                "clause_semantic_tag_icd11_code": 21,
                "clause_semantic_tag_icd11_lookup": 21,
                "clause_version": 29,
                "clause_version_block": 318,
                "clause_version_date": 261,
                "clause_version_edge": 17,
                "clause_version_observation": 152,
                "coverage_assessment": 12,
                "diff_hunk_presentation": 26,
                "diff_run": 1,
                "import_run": 1,
                "reader_enrichment_run": 1,
                "source_edition": 15,
            },
        )
        for filename, receipt in MANIFEST["files"].items():
            path = EXPORT / filename
            self.assertEqual(path.stat().st_size, receipt["bytes"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                receipt["sha256"],
            )
            self.assertEqual(
                len(path.read_text(encoding="utf-8").splitlines()),
                receipt["rows"],
            )

    def test_each_top_level_clause_has_an_independent_version_chain(self) -> None:
        clauses = read_jsonl("clause")
        versions = read_jsonl("clause_version")
        observations = read_jsonl("clause_version_observation")
        edges = read_jsonl("clause_version_edge")
        coverage = {
            row["clause_id"]: row for row in read_jsonl("coverage_assessment")
        }
        clause_by_code = {
            row["canonical_code"]: row["clause_id"] for row in clauses
        }
        version_counts = Counter(row["clause_id"] for row in versions)
        observation_counts = Counter(row["clause_id"] for row in observations)
        edge_counts = Counter(row["clause_id"] for row in edges)

        self.assertEqual(
            list(clause_by_code),
            [f"0.{number}" for number in range(1, 13)],
        )
        for clause_id in clause_by_code.values():
            self.assertEqual(
                edge_counts[clause_id],
                version_counts[clause_id] - 1,
            )
            self.assertEqual(
                coverage[clause_id]["version_state_count"],
                version_counts[clause_id],
            )
            self.assertEqual(
                coverage[clause_id]["observed_edition_count"],
                observation_counts[clause_id],
            )

        unchanged = clause_by_code["0.2"]
        changed = clause_by_code["0.4"]
        self.assertEqual(observation_counts[unchanged], 15)
        self.assertEqual(version_counts[unchanged], 1)
        self.assertEqual(edge_counts[unchanged], 0)
        self.assertEqual(observation_counts[changed], 15)
        self.assertEqual(version_counts[changed], 10)
        self.assertEqual(edge_counts[changed], 9)

    def test_edges_never_cross_clause_boundaries_or_claim_legal_predecessors(self) -> None:
        version_clause = {
            row["clause_version_id"]: row["clause_id"]
            for row in read_jsonl("clause_version")
        }
        for edge in read_jsonl("clause_version_edge"):
            self.assertEqual(
                version_clause[edge["older_clause_version_id"]],
                edge["clause_id"],
            )
            self.assertEqual(
                version_clause[edge["newer_clause_version_id"]],
                edge["clause_id"],
            )
            self.assertEqual(
                edge["adjacency_basis"],
                "adjacent_distinct_text_state_across_official_editions",
            )
            self.assertEqual(edge["legal_predecessor_status"], "not_claimed")
            self.assertTrue(edge["crosses_known_gap"])

    def test_all_source_observations_are_preserved_after_text_collapse(self) -> None:
        observations_by_version: dict[str, list[int]] = defaultdict(list)
        for observation in read_jsonl("clause_version_observation"):
            observations_by_version[observation["clause_version_id"]].append(
                int(observation["chronology_order"])
            )
        self.assertTrue(
            any(len(orders) == 15 for orders in observations_by_version.values())
        )
        for orders in observations_by_version.values():
            self.assertEqual(orders, sorted(orders))
            self.assertEqual(
                orders,
                list(range(min(orders), max(orders) + 1)),
            )

    def test_import_is_sealed_with_deterministic_algorithms(self) -> None:
        runs = read_jsonl("import_run")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["state"], "sealed")
        self.assertEqual(runs[0]["extractor_version"], EXTRACTOR_VERSION)
        self.assertEqual(runs[0]["diff_version"], DIFF_VERSION)
        import_owned_tables = {
            "chapter",
            "clause",
            "clause_version",
            "clause_version_observation",
            "clause_version_block",
            "clause_version_date",
            "clause_version_edge",
            "clause_diff_hunk",
            "coverage_assessment",
        }
        expected_counts = {
            key: MANIFEST["counts"][key] for key in import_owned_tables
        }
        self.assertEqual(runs[0]["row_counts"], expected_counts)

    def test_semantic_diff_does_not_invent_a_deleted_old_side(self) -> None:
        addition = semantic_diff_presentation("ABC", "ABCD")
        self.assertEqual(addition["semantic_change_kind"], "added")
        self.assertFalse(
            any(
                segment["kind"] == "removed"
                for segment in addition["inline_segments"]
            )
        )

        formatting = semantic_diff_presentation(
            "Ａ B’C",
            "AＢC",
        )
        self.assertEqual(formatting["semantic_change_kind"], "format_only")
        self.assertEqual(
            set(formatting["ignored_change_classes"]),
            {"single_quote", "whitespace", "width_variant"},
        )

    def test_diff_and_reader_enrichment_are_sealed_projections(self) -> None:
        diff_run = read_jsonl("diff_run")[0]
        enrichment = read_jsonl("reader_enrichment_run")[0]
        self.assertEqual(diff_run["algorithm_version"], DIFF_PRESENTATION_VERSION)
        self.assertEqual(
            enrichment["generator_version"],
            READER_ENRICHMENT_VERSION,
        )
        self.assertEqual(diff_run["hunk_count"], 26)
        self.assertEqual(enrichment["semantic_tag_count"], 66)
        self.assertEqual(enrichment["condition_marker_count"], 21)

    def test_icd11_codes_are_public_but_icd_content_stays_private(self) -> None:
        self.assertFalse(
            (EXPORT / "clause_semantic_tag_icd11_private.jsonl").exists()
        )
        public_rows = read_jsonl("clause_semantic_tag_icd11_lookup")
        self.assertTrue(public_rows)
        self.assertTrue(all(row["icd11_code"] is None for row in public_rows))
        self.assertTrue(all(row["icd11_uri"] is None for row in public_rows))
        code_rows = read_jsonl("clause_semantic_tag_icd11_code")
        self.assertEqual(len(code_rows), 21)
        self.assertTrue(all(row["icd11_code"] for row in code_rows))
        self.assertTrue(all("icd11_title" not in row for row in code_rows))
        self.assertTrue(all("icd11_uri" not in row for row in code_rows))

    def test_sqlite_is_a_full_portable_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chapter-00-clauses.sqlite"
            receipt = build_sqlite(
                jsonl_dir=EXPORT,
                schema_path=ROOT / "database" / "clause-sqlite-schema.sql",
                output=output,
                force=True,
            )
            self.assertEqual(receipt["foreign_key_check"], "passed")
            self.assertEqual(receipt["integrity_check"], "passed")
            conn = sqlite3.connect(output)
            try:
                for table, expected in MANIFEST["counts"].items():
                    actual = conn.execute(
                        f'SELECT count(*) FROM "{table}"'
                    ).fetchone()[0]
                    self.assertEqual(actual, expected, table)
            finally:
                conn.close()

    def test_additive_pg_schema_names_the_single_clause_boundary(self) -> None:
        self.assertIn(
            "CREATE SCHEMA IF NOT EXISTS nhi_rule_history_clause",
            MIGRATION,
        )
        self.assertIn(
            "REFERENCES nhi_rule_history_edition.rule_version",
            MIGRATION,
        )
        self.assertIn("canonical_code", MIGRATION)
        self.assertIn("clause_version_observation", MIGRATION)
        self.assertIn(
            "adjacent_distinct_text_state_across_official_editions",
            MIGRATION,
        )
        self.assertIn("legal_predecessor_status", MIGRATION)
        self.assertIn("DROP SCHEMA IF EXISTS nhi_rule_history_clause CASCADE", ROLLBACK)
        self.assertNotIn("DROP SCHEMA IF EXISTS nhi_rule_history_edition", ROLLBACK)


if __name__ == "__main__":
    unittest.main()
