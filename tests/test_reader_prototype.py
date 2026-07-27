from __future__ import annotations

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
READER_CONTRACT = (ROOT / "docs" / "reader-experience.md").read_text(
    encoding="utf-8"
)


class ReaderPrototypeTests(unittest.TestCase):
    def test_latest_full_text_and_adjacent_diff_contract_are_visible(
        self,
    ) -> None:
        self.assertIn("今天適用", HTML)
        self.assertIn("最新公告", HTML)
        self.assertIn("給付條文全文", HTML)
        self.assertIn("每一列只回答：下一版改了什麼？", HTML)
        self.assertIn("下一版刪除", HTML)
        self.assertIn("下一版新增", HTML)
        self.assertIn("目前不產生推測性的 diff", HTML)

    def test_real_source_and_future_effective_state_are_explicit(self) -> None:
        self.assertIn("健保審字第 1150055419 號", HTML)
        self.assertIn("2026-08-01 生效", HTML)
        self.assertIn("尚未生效", SCRIPT)
        self.assertIn("cp-20273-b2f8c-3258-1.html", HTML)
        self.assertIn(
            "dl-100543-c9d73a25335b4d42814a86104e985078-1.odt",
            HTML,
        )

    def test_change_and_search_highlights_use_distinct_channels(self) -> None:
        self.assertIn("key-dot--add", HTML)
        self.assertIn("key-dot--search", HTML)
        self.assertIn("本版新增", SCRIPT)
        self.assertIn("--search: #fff0a6", CSS)
        self.assertIn(".diff-label", CSS)

    def test_mobile_and_reduced_motion_rules_exist(self) -> None:
        self.assertIn("@media (max-width: 820px)", CSS)
        self.assertIn("@media (max-width: 520px)", CSS)
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        self.assertIn(".timeline-row", CSS)

    def test_project_navigation_code_never_becomes_official_chapter_zero(
        self,
    ) -> None:
        self.assertIn('"source_designation_raw": "通則"', READER_CONTRACT)
        self.assertIn('"reader_display_label": "通則"', READER_CONTRACT)
        self.assertIn('"navigation_code": "chapter:00"', READER_CONTRACT)
        self.assertIn('"code_origin": "project_assigned"', READER_CONTRACT)
        self.assertIn(
            "never emitted as `official_chapter_number`",
            READER_CONTRACT,
        )


if __name__ == "__main__":
    unittest.main()
