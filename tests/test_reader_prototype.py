from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "prototype" / "reader"
HTML = (READER / "index.html").read_text(encoding="utf-8")
SCRIPT = (READER / "app.js").read_text(encoding="utf-8")
CSS = (READER / "styles.css").read_text(encoding="utf-8")
INDEX = json.loads(
    (READER / "data" / "clauses" / "index.json").read_text(encoding="utf-8")
)
CLAUSE_04 = json.loads(
    (READER / "data" / "clauses" / "0.4.json").read_text(encoding="utf-8")
)
CLAUSE_02 = json.loads(
    (READER / "data" / "clauses" / "0.2.json").read_text(encoding="utf-8")
)


class ReaderPrototypeTests(unittest.TestCase):
    def test_index_routes_to_one_clause_per_page(self) -> None:
        self.assertEqual(
            INDEX["schema"],
            "nhi-rule-history/single-clause-index/v1",
        )
        self.assertEqual(
            INDEX["generated_from"],
            "PostgreSQL nhi_rule_history_clause",
        )
        self.assertEqual(INDEX["canonical_version_unit"], "single_clause")
        self.assertEqual(INDEX["default_clause_code"], "0.4")
        self.assertEqual(
            [clause["canonical_code"] for clause in INDEX["clauses"]],
            [f"0.{number}" for number in range(1, 13)],
        )
        self.assertIn('get("rule")', SCRIPT)
        self.assertIn("INDEX_URL", SCRIPT)
        self.assertNotIn("./data/chapter-00-reader.json", SCRIPT)

    def test_clause_04_is_the_only_full_text_on_its_page(self) -> None:
        self.assertEqual(
            CLAUSE_04["schema"],
            "nhi-rule-history/single-clause-reader/v1",
        )
        self.assertEqual(CLAUSE_04["clause"]["canonical_code"], "0.4")
        self.assertEqual(
            CLAUSE_04["clause"]["display_title"],
            "注射藥品之使用原則",
        )
        self.assertEqual(CLAUSE_04["coverage"]["observed_edition_count"], 15)
        self.assertEqual(CLAUSE_04["coverage"]["version_state_count"], 10)
        self.assertEqual(CLAUSE_04["coverage"]["version_edge_count"], 9)
        self.assertEqual(len(CLAUSE_04["transitions"]), 9)
        latest_blocks = [
            block["text"] for block in CLAUSE_04["latest"]["full_text_blocks"]
        ]
        self.assertTrue(latest_blocks[0].startswith("四、"))
        self.assertFalse(any(block.startswith("五、") for block in latest_blocks))
        self.assertIn("本條最新版全文", HTML)
        self.assertIn("本條歷史變更", HTML)

    def test_unchanged_annual_observations_do_not_make_fake_versions(self) -> None:
        self.assertEqual(CLAUSE_02["coverage"]["observed_edition_count"], 15)
        self.assertEqual(CLAUSE_02["coverage"]["version_state_count"], 1)
        self.assertEqual(CLAUSE_02["coverage"]["version_edge_count"], 0)
        self.assertEqual(len(CLAUSE_02["latest"]["observed_editions"]), 15)
        self.assertEqual(CLAUSE_02["transitions"], [])
        self.assertIn("未改字的年度版本只作來源觀察", HTML)

    def test_diff_is_only_between_versions_of_the_selected_clause(self) -> None:
        for transition in CLAUSE_04["transitions"]:
            self.assertGreaterEqual(len(transition["hunks"]), 1)
            self.assertEqual(
                transition["adjacency_basis"],
                "adjacent_distinct_text_state_across_official_editions",
            )
            self.assertEqual(
                transition["legal_predecessor_status"],
                "not_claimed",
            )
        self.assertIn("下一版刪除", SCRIPT)
        self.assertIn("下一版新增", SCRIPT)
        self.assertIn("diff-side--old", SCRIPT)
        self.assertIn("diff-side--new", SCRIPT)
        self.assertIn(".inline-change--old", CSS)
        self.assertIn(".inline-change--new", CSS)

    def test_search_is_global_across_clause_index(self) -> None:
        insulin = [
            clause
            for clause in INDEX["clauses"]
            if "insulin" in clause["search_text"]
        ]
        biomarker = [
            clause
            for clause in INDEX["clauses"]
            if "生物標記" in clause["search_text"]
        ]
        self.assertEqual([row["canonical_code"] for row in insulin], ["0.4"])
        self.assertEqual([row["canonical_code"] for row in biomarker], ["0.12"])
        self.assertIn("clause-results", HTML)
        self.assertIn("normalizedSearch(clause.search_text)", SCRIPT)
        self.assertIn(".clause-result", CSS)

    def test_navigation_codes_and_legal_scope_are_not_overclaimed(self) -> None:
        self.assertEqual(CLAUSE_04["chapter"]["display_label"], "通則")
        self.assertEqual(
            CLAUSE_04["chapter"]["navigation_code_origin"],
            "project_assigned",
        )
        self.assertEqual(
            CLAUSE_04["clause"]["code_origin"],
            "project_assigned",
        )
        self.assertFalse(CLAUSE_04["coverage"]["official_source_universe_closed"])
        self.assertFalse(CLAUSE_04["coverage"]["legal_history_complete"])
        self.assertIn("整章是來源；單一條文才是版本", HTML)
        self.assertIn("未宣稱兩份來源之間沒有其他公告", SCRIPT)

    def test_mobile_reduced_motion_and_accessibility_rules_exist(self) -> None:
        self.assertIn("@media (max-width: 820px)", CSS)
        self.assertIn("@media (max-width: 560px)", CSS)
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        self.assertIn('aria-live="polite"', HTML)
        self.assertIn('role="alert"', HTML)
        self.assertIn('target="_blank"', SCRIPT)
        self.assertIn('rel="noopener noreferrer"', SCRIPT)


if __name__ == "__main__":
    unittest.main()
