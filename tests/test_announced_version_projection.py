from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from nhi_rule_history.announced_dyslipidemia import (
    VERSION_PROJECTION_MIGRATION,
    _adjacent_diff_rows,
    _terminology_projection_rows,
)
from nhi_rule_history.terminology import normalize_alias


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AnnouncedVersionProjectionTest(unittest.TestCase):
    def test_scanner_writes_complete_block_denominator_and_exact_offsets(
        self,
    ) -> None:
        sources = [
            {
                "source_block_id": "new:0",
                "raw_text": "糖尿病使用insulin",
                "raw_text_sha256": _sha("糖尿病使用insulin"),
            },
            {
                "source_block_id": "new:1",
                "raw_text": "沒有標註",
                "raw_text_sha256": _sha("沒有標註"),
            },
        ]
        projection = {
            "tagging_run_id": "11111111-1111-1111-1111-111111111111",
            "aliases": [
                {
                    "alias_id": "22222222-2222-2222-2222-222222222222",
                    "concept_id": "33333333-3333-3333-3333-333333333333",
                    "normalized_alias": normalize_alias("insulin"),
                    "production_status": "admitted",
                    "match_rule": "case_insensitive_token",
                }
            ],
        }
        inputs, occurrences = _terminology_projection_rows(
            run_id="44444444-4444-4444-4444-444444444444",
            version_id="55555555-5555-5555-5555-555555555555",
            clause_code="2.6.1",
            composite_sources=sources,
            terminology_projection=projection,
        )
        self.assertEqual(len(inputs), 2)
        self.assertEqual(
            [row["scan_status"] for row in inputs],
            ["scanned_with_match", "scanned_no_match"],
        )
        self.assertEqual(len(occurrences), 1)
        occurrence = occurrences[0]
        text = sources[0]["raw_text"]
        self.assertEqual(
            text[occurrence["start_scalar"] : occurrence["end_scalar"]],
            "insulin",
        )

    def test_adjacent_addition_does_not_invent_a_deleted_side(self) -> None:
        rows = _adjacent_diff_rows(
            run_id="44444444-4444-4444-4444-444444444444",
            version_id="55555555-5555-5555-5555-555555555555",
            clause_code="2.6.1",
            predecessor={
                "run_id": "66666666-6666-6666-6666-666666666666",
                "raw_text_sha256": _sha("ABC"),
            },
            predecessor_blocks=[{"raw_text": "ABC"}],
            composite_sources=[{"raw_text": "ABCD"}],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["semantic_change_kind"], "added")
        self.assertEqual(rows[0]["display_note"], "本版新增")
        self.assertFalse(
            any(
                segment["kind"] == "removed"
                for segment in rows[0]["inline_segments"]
            )
        )

    def test_migration_seals_shared_tagging_and_direct_diff(self) -> None:
        sql = VERSION_PROJECTION_MIGRATION.read_text(encoding="utf-8")
        for required in (
            "composed_clause_tagging_block_input",
            "composed_clause_terminology_occurrence",
            "announced_composed_admitted_occurrence_no_overlap",
            "composed_clause_diff_hunk",
            "與上一版本差異",
            "version terminology offsets differ from source blocks",
            "version tagging or adjacent diff coverage is incomplete",
        ):
            self.assertIn(required, sql)


if __name__ == "__main__":
    unittest.main()
