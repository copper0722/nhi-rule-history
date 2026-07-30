from __future__ import annotations

import json
import unittest
from pathlib import Path

from nhi_rule_history.reader_profile import (
    PROFILE_CONTRACT,
    PROFILE_MIGRATION,
    ReaderProfileError,
    validate_reader_profile,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = (
    ROOT / "data" / "presentations" / "2.6.1-2026-09-01.json"
)


class ReaderProfileTest(unittest.TestCase):
    def test_checked_in_profile_is_source_bound_and_safe(self) -> None:
        payload = validate_reader_profile(
            json.loads(PROFILE.read_text(encoding="utf-8"))
        )
        self.assertEqual(payload["contract"], PROFILE_CONTRACT)
        self.assertEqual(
            payload["presentation_mode"], "agentic_specialized"
        )
        self.assertEqual(
            payload["template_key"], "dyslipidemia_pathway_v1"
        )
        self.assertEqual(len(payload["content"]["pathway_steps"]), 4)
        self.assertGreaterEqual(
            len(payload["content"]["change_digest"]), 5
        )

    def test_profile_rejects_executable_copy(self) -> None:
        payload = json.loads(PROFILE.read_text(encoding="utf-8"))
        payload["content"]["lead"] = "<script>alert(1)</script>"
        with self.assertRaisesRegex(
            ReaderProfileError, "executable HTML"
        ):
            validate_reader_profile(payload)

    def test_profile_rejects_stale_hash_shape(self) -> None:
        payload = json.loads(PROFILE.read_text(encoding="utf-8"))
        payload["source_binding"]["source_composed_text_sha256"] = "bad"
        with self.assertRaisesRegex(
            ReaderProfileError, "source_composed_text_sha256"
        ):
            validate_reader_profile(payload)

    def test_migration_keeps_agentic_copy_out_of_official_text(self) -> None:
        migration = PROFILE_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("source_composed_text_sha256", migration)
        self.assertIn("source_diff_output_fingerprint", migration)
        self.assertIn("agentic_specialized", migration)
        self.assertIn("content_payload", migration)
        self.assertNotIn("UPDATE composed_clause_version", migration)


if __name__ == "__main__":
    unittest.main()
