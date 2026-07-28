from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "prototype" / "reader"
HTML = (READER / "index.html").read_text(encoding="utf-8")
SCRIPT = (READER / "app.js").read_text(encoding="utf-8")
TAG_HTML = (READER / "tag.html").read_text(encoding="utf-8")
TAG_SCRIPT = (READER / "tag.js").read_text(encoding="utf-8")
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

    def test_history_uses_new_in_text_date_annotations_as_reader_labels(self) -> None:
        transition_to_99 = next(
            transition
            for transition in CLAUSE_04["transitions"]
            if transition["newer"]["observed_editions"][0]["edition_label"]
            == "99年版"
        )
        self.assertEqual(transition_to_99["display_date"]["label"], "99/11")
        self.assertEqual(
            transition_to_99["display_date"]["basis"],
            "new_text_date_annotation",
        )
        self.assertIn("條文註記；尚未認定為法律生效日", SCRIPT)

    def test_pg_semantic_tags_drive_atc_disease_and_condition_rendering(self) -> None:
        tags = CLAUSE_04["semantic_tags"]
        octreotide = next(tag for tag in tags if tag["tag_text"] == "octreotide")
        apomorphine = next(
            tag for tag in tags if tag["tag_text"] == "apomorphine"
        )
        diabetes = next(tag for tag in tags if tag["tag_text"] == "糖尿病")
        tags_by_text = {tag["tag_text"]: tag for tag in tags}
        self.assertEqual(octreotide["terminology"]["system"], "ATC")
        self.assertEqual(
            octreotide["terminology"]["codes"][0]["code"],
            "H01CB02",
        )
        self.assertEqual(
            apomorphine["terminology"]["codes"][0]["code"],
            "N04BC07",
        )
        self.assertEqual(diabetes["terminology"]["system"], "ICD-11")
        self.assertEqual(
            diabetes["terminology"]["public_export"],
            "code_only_no_icd_content",
        )
        self.assertEqual(
            diabetes["terminology"]["codes"][0]["code"],
            "5A14",
        )
        self.assertEqual(
            diabetes["terminology"]["codes"][0]["mapping_status"],
            "candidate",
        )
        self.assertNotIn("title", diabetes["terminology"]["codes"][0])
        self.assertNotIn("uri", diabetes["terminology"]["codes"][0])
        expected_atc = {
            "透析液": {"B05D"},
            "抗生素": {"J01"},
            "抗凝血劑": {"B01AB"},
            "第八、第九凝血因子": {"B02BD02", "B02BD04"},
            "繞徑治療藥物": {"B02BD03", "B02BD08"},
            "第十三凝血因子": {"B02BD07"},
            "靜脈營養輸液": {"B05BA"},
            "化學治療藥品": {"L01"},
            "exenatide": {"A10BJ01"},
            "liraglutide": {"A10BJ02"},
        }
        for term, codes in expected_atc.items():
            self.assertEqual(
                {
                    item["code"]
                    for item in tags_by_text[term]["terminology"]["codes"]
                },
                codes,
                term,
            )
        self.assertNotIn("CAPD", tags_by_text)
        self.assertNotIn("癌症", tags_by_text)
        marker_texts = {
            marker["marker_text"] for marker in CLAUSE_04["condition_markers"]
        }
        self.assertTrue({"限", "不得", "且", "或"}.issubset(marker_texts))
        self.assertNotIn("至多", marker_texts)
        logical_markers = {
            marker["marker_text"]
            for marker in CLAUSE_04["condition_markers"]
            if marker["semantic_role"] == "logical"
        }
        self.assertEqual(logical_markers, {"且", "或"})
        duration_markers = {
            marker["marker_text"]
            for marker in CLAUSE_04["condition_markers"]
            if marker["semantic_role"] == "duration"
        }
        self.assertTrue(
            {"二週", "六天", "一個月", "三個月", "每週"}.issubset(
                duration_markers
            )
        )
        quantity_markers = {
            marker["marker_text"]
            for marker in CLAUSE_04["condition_markers"]
            if marker["semantic_role"] == "quantity"
        }
        self.assertEqual(
            quantity_markers,
            {"15支", "20支", "20,000U", "100mcg"},
        )
        self.assertNotIn("需要", marker_texts)
        self.assertNotIn("應", marker_texts)
        self.assertNotIn("需", marker_texts)
        self.assertNotIn("不超過", marker_texts)
        self.assertIn("semantic-tag--drug", CSS)
        self.assertIn("semantic-tag--disease", CSS)
        semantic_tag_css = CSS[
            CSS.index(".semantic-tag {") : CSS.index(".semantic-tag--drug")
        ]
        self.assertIn("vertical-align: baseline;", semantic_tag_css)
        self.assertIn("text-indent: 0;", semantic_tag_css)
        self.assertIn("white-space: nowrap;", semantic_tag_css)
        self.assertNotIn(".semantic-tag small", CSS)
        self.assertIn("condition-term--prohibition", CSS)
        self.assertIn("isEmbeddedLatinToken", SCRIPT)
        self.assertIn("semanticLinkLabel", SCRIPT)
        self.assertNotIn("terminologyLabel", SCRIPT)
        self.assertIn(
            'new RegExp(escapeRegExp(marker.marker_text), "giu")',
            SCRIPT,
        )
        self.assertIn("condition-term--logical", CSS)
        self.assertIn("condition-term--duration", CSS)
        self.assertIn("condition-term--quantity", CSS)
        self.assertIn('marker.marker_text === "限"', SCRIPT)
        self.assertIn('=== "上限"', SCRIPT)
        self.assertIn("ATC", TAG_SCRIPT)
        self.assertIn("已確認關聯", TAG_SCRIPT)
        self.assertIn("候選關聯", TAG_SCRIPT)
        self.assertIn("ICD-11", TAG_HTML)

    def test_latin_token_boundary_guard_executes_expected_cases(self) -> None:
        boundary_source = SCRIPT[
            SCRIPT.index("const LATIN_TOKEN_CHARACTER")
            : SCRIPT.index("function semanticLinkLabel")
        ]
        assertions = """
const cases = [
  [isEmbeddedLatinToken("apomorphine", 0, 11), false],
  [isEmbeddedLatinToken("apomorphine", 3, 11), true],
  [isEmbeddedLatinToken("xapomorphine", 1, 12), true],
  [isEmbeddedLatinToken("apomorphineX", 0, 11), true],
  [isEmbeddedLatinToken("（apomorphine）", 1, 12), false],
];
for (const [actual, expected] of cases) {
  if (actual !== expected) process.exit(1);
}
"""
        subprocess.run(
            ["node", "-e", f"{boundary_source}\n{assertions}"],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_agent_summary_and_structured_clause_rendering_are_present(self) -> None:
        summary = CLAUSE_04["agent_history_summary"]
        self.assertIsNotNone(summary)
        self.assertEqual(
            summary["generation_method"],
            "pure_agentic_from_structured_diff",
        )
        self.assertIn("99/11", summary["summary_markdown"])
        blocks = CLAUSE_04["latest"]["full_text_blocks"]
        self.assertEqual(blocks[0]["render_kind"], "clause_heading")
        self.assertTrue(
            any(block["render_kind"] == "subsection" for block in blocks)
        )
        self.assertTrue(
            any(block["render_kind"] == "list_item" for block in blocks)
        )
        self.assertIn("歷史變更總覽（本節由生成式AI輸出）", HTML)
        self.assertNotIn("先看懂", HTML)
        self.assertNotIn("摘要不取代官方條文", HTML)
        summary_markdown = CLAUSE_04["agent_history_summary"][
            "summary_markdown"
        ]
        self.assertNotIn("不是反覆重寫全文，而是", summary_markdown)
        self.assertIn("rule-heading", SCRIPT)
        self.assertIn(".rule-date", CSS)

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

    def test_title_and_search_share_a_frozen_reader_dock(self) -> None:
        dock_start = HTML.index('<div class="reader-dock">')
        main_start = HTML.index('<main id="top">')
        self.assertLess(dock_start, main_start)
        dock_html = HTML[dock_start:main_start]
        self.assertIn('id="dock-page-title"', dock_html)
        self.assertIn("搜尋通則條文", dock_html)
        self.assertIn('id="page-search"', dock_html)
        self.assertIn('class="section-nav"', dock_html)
        self.assertIn(".reader-dock {\n  position: sticky;", CSS)
        self.assertIn("grid-template-columns: minmax(300px, 0.88fr)", CSS)
        self.assertIn("els.dockPageTitle.textContent", SCRIPT)

    def test_mobile_reader_uses_floating_island_and_scrollspy_toc(self) -> None:
        self.assertIn('class="mobile-reader-island"', HTML)
        self.assertIn('id="mobile-toc-drawer"', HTML)
        self.assertIn('data-mobile-toc-target="latest"', HTML)
        self.assertIn('data-mobile-toc-target="history"', HTML)
        self.assertIn('data-mobile-toc-target="method"', HTML)
        self.assertIn("initializeMobileTocScrollspy", SCRIPT)
        self.assertIn("new IntersectionObserver", SCRIPT)
        self.assertIn("selectFromScrollPosition", SCRIPT)
        self.assertIn(
            'window.addEventListener("scroll", requestScrollspyUpdate',
            SCRIPT,
        )
        self.assertIn('setMobilePanel(mobilePanel === "toc" ? null : "toc")', SCRIPT)
        self.assertIn(
            'setMobilePanel(mobilePanel === "search" ? null : "search")',
            SCRIPT,
        )
        self.assertIn("@media (max-width: 700px)", CSS)
        self.assertIn(".mobile-reader-island {", CSS)
        self.assertIn(".mobile-toc-drawer--open", CSS)
        self.assertIn(".reader-dock .find-bar--mobile-open", CSS)

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
