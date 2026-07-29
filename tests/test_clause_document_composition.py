from __future__ import annotations

import unittest

from nhi_rule_history.clause_document_composition import (
    _assembled_ranges,
    _sha256_text,
    _verify_source_spans,
)


class ClauseDocumentCompositionTest(unittest.TestCase):
    def test_scalar_and_utf8_ranges_cover_supplementary_and_combining_text(
        self,
    ) -> None:
        text = "A😀e\u0301"
        blocks = [
            {
                "raw_text": text,
                "raw_text_sha256": _sha256_text(text),
                "origin_lane": "amendment_exact",
                "source_artifact_sha256": "a" * 64,
                "source_block_id": "block-1",
                "source_locator": {"page": 1},
                "source_spans": [
                    {
                        "scalar_start": 0,
                        "scalar_end": len(text),
                        "utf8_byte_start": 0,
                        "utf8_byte_end": len(text.encode("utf-8")),
                        "exact_span_text": text,
                    }
                ],
            },
            {
                "raw_text": "",
                "raw_text_sha256": _sha256_text(""),
                "origin_lane": "predecessor_inherited",
                "source_artifact_sha256": "b" * 64,
                "source_block_id": "block-2",
                "source_locator": {"page": 2},
                "source_spans": [],
            },
        ]

        assembled, ranges = _assembled_ranges(blocks)
        self.assertEqual(assembled, text + "\n\n")
        self.assertEqual(ranges[0]["assembled_scalar_start"], 0)
        self.assertEqual(ranges[-1]["assembled_scalar_end"], len(assembled))
        self.assertEqual(
            ranges[-1]["assembled_utf8_byte_end"],
            len(assembled.encode("utf-8")),
        )
        self.assertEqual(
            _verify_source_spans(blocks),
            {
                "nonempty_component_count": 1,
                "empty_physical_component_count": 1,
                "source_span_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
