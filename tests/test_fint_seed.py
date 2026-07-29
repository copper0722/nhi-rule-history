from __future__ import annotations

import unittest

from nhi_rule_history.discovery.fint_seed import (
    Heading,
    extract_terms,
    seeds_from_headings,
)


class FintSeedTests(unittest.TestCase):
    def test_extracts_specific_terms_and_removes_dates_and_dosage_prose(self) -> None:
        terms = extract_terms(
            "1.1.5.非類固醇抗發炎劑（NSAIDs）藥品，屬下列成分之口服製劑："
            "celecoxib、nabumetone、meloxicam (90/7/1、97/9/1)"
        )
        self.assertIn("NSAIDs", terms)
        self.assertIn("非類固醇抗發炎劑", terms)
        self.assertNotIn("口服製劑", terms)
        self.assertNotIn("限用於", terms)

    def test_latin_drug_terms_query_alone_chinese_class_uses_baseline(self) -> None:
        seeds = seeds_from_headings(
            [
                Heading(
                    "1.1.4",
                    "1.1.4.Tramadol HCl＋acetaminophen（93/7/1）",
                ),
                Heading(
                    "1.1.1",
                    "1.1.1.非類固醇抗發炎劑外用製劑：(88/9/1)",
                ),
            ]
        )
        by_first = {seed.keyword: seed.keywords for seed in seeds}
        self.assertEqual(by_first["Tramadol"], ("Tramadol",))
        self.assertEqual(by_first["acetaminophen"], ("acetaminophen",))
        self.assertEqual(
            by_first["非類固醇抗發炎劑外用製劑"],
            ("非類固醇抗發炎劑外用製劑", "藥品給付規定"),
        )

    def test_duplicate_current_formats_preserve_one_query_with_two_origins(self) -> None:
        seeds = seeds_from_headings(
            [
                Heading("1.1.3", "1.1.3.Tramadol (87/4/1)"),
                Heading("1.1.3", "1.1.3.Tramadol (87/4/1)"),
            ]
        )
        tramadol = [seed for seed in seeds if seed.keyword == "Tramadol"]
        self.assertEqual(len(tramadol), 1)


if __name__ == "__main__":
    unittest.main()
