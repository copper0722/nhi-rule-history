from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "prototype" / "reader" / "index.html").read_text(
    encoding="utf-8"
)
SCRIPT = (ROOT / "prototype" / "reader" / "app.js").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "prototype" / "reader" / "styles.css").read_text(
    encoding="utf-8"
)
DATA = json.loads(
    (
        ROOT
        / "prototype"
        / "reader"
        / "data"
        / "chapter-00-reader.json"
    ).read_text(encoding="utf-8")
)
READER_CONTRACT = (ROOT / "docs" / "reader-experience.md").read_text(
    encoding="utf-8"
)


class ReaderPrototypeTests(unittest.TestCase):
    def test_pg_projection_drives_latest_full_text_and_all_edges(self) -> None:
        self.assertEqual(
            DATA["generated_from"],
            "PostgreSQL nhi_rule_history_edition",
        )
        self.assertEqual(DATA["coverage"]["loaded_edition_count"], 15)
        self.assertEqual(DATA["coverage"]["adjacent_edge_count"], 14)
        self.assertEqual(len(DATA["transitions"]), 14)
        self.assertGreaterEqual(len(DATA["latest"]["full_text_blocks"]), 50)
        self.assertIn("最新版全文", HTML)
        self.assertIn("歷史變更", HTML)
        self.assertIn("DATA_URL", SCRIPT)
        self.assertNotIn("const currentRule", SCRIPT)

    def test_reader_surface_uses_tongze_not_official_chapter_zero(self) -> None:
        self.assertEqual(DATA["rule"]["display_label"], "通則")
        self.assertEqual(DATA["rule"]["source_designation_raw"], "通則")
        self.assertEqual(DATA["rule"]["navigation_code"], "chapter:00")
        self.assertEqual(
            DATA["rule"]["navigation_code_origin"],
            "project_assigned",
        )
        self.assertIn("<h1 id=\"page-title\">通則</h1>", HTML)
        self.assertIn("不是官方「第 0 章」", HTML)

    def test_dates_and_legal_scope_are_not_overclaimed(self) -> None:
        self.assertEqual(
            DATA["latest"]["date"]["role"],
            "official_update_date",
        )
        self.assertEqual(
            DATA["latest"]["date"]["legal_effective_status"],
            "not_claimed",
        )
        self.assertFalse(DATA["coverage"]["official_source_universe_closed"])
        self.assertFalse(DATA["coverage"]["legal_history_complete"])
        self.assertTrue(DATA["coverage"]["edition_set_complete"])
        self.assertIn("不是自動推定的法律生效日", SCRIPT)
        self.assertIn("相鄰官方累積版本", SCRIPT)

    def test_change_and_search_highlights_use_distinct_channels(self) -> None:
        kinds = {
            hunk["change_kind"]
            for transition in DATA["transitions"]
            for hunk in transition["hunks"]
        }
        self.assertIn("added", kinds)
        self.assertIn("replaced", kinds)
        self.assertIn("diff-side--old", SCRIPT)
        self.assertIn("diff-side--new", SCRIPT)
        self.assertIn("search-hit", SCRIPT)
        self.assertIn("--search: #ffe47b", CSS)
        self.assertIn(".inline-change--old", CSS)
        self.assertIn(".inline-change--new", CSS)

    def test_mobile_reduced_motion_and_accessibility_rules_exist(self) -> None:
        self.assertIn("@media (max-width: 820px)", CSS)
        self.assertIn("@media (max-width: 560px)", CSS)
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        self.assertIn("aria-live=\"polite\"", HTML)
        self.assertIn("role=\"alert\"", HTML)
        self.assertIn("target=\"_blank\"", SCRIPT)
        self.assertIn('rel="noopener noreferrer"', SCRIPT)

    def test_reader_contract_keeps_project_navigation_provenance(self) -> None:
        self.assertIn('"source_designation_raw": "通則"', READER_CONTRACT)
        self.assertIn('"reader_display_label": "通則"', READER_CONTRACT)
        self.assertIn('"navigation_code": "chapter:00"', READER_CONTRACT)
        self.assertIn('"code_origin": "project_assigned"', READER_CONTRACT)


if __name__ == "__main__":
    unittest.main()
