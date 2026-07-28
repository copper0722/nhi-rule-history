from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY = (
    ROOT
    / "database"
    / "queries"
    / "history-completeness-status.sql"
)


class HistoryCompletenessStatusQueryTest(unittest.TestCase):
    def test_query_is_read_only_and_fail_closed(self) -> None:
        sql = QUERY.read_text(encoding="utf-8")
        upper = sql.upper()
        self.assertIn("BEGIN TRANSACTION READ ONLY", upper)
        self.assertIn(r"\SET ON_ERROR_STOP ON", upper)
        self.assertIn("TO_REGCLASS", upper)
        self.assertIn("TO_REGNAMESPACE", upper)
        self.assertIn("'UNRESOLVED_EVENT'", upper)
        self.assertIn("'VALID_CALENDAR_CANDIDATES'", upper)
        self.assertIn("'INVALID_CALENDAR_CANDIDATES'", upper)
        self.assertIn("'ARTICLES_WITH_VALID_CALENDAR_CANDIDATES'", upper)
        self.assertIn("'RESOLVED_CANDIDATE'", upper)
        self.assertIn("'NO_MATCH'", upper)
        self.assertIn("'INVALID'", upper)
        self.assertIn("'NEEDS_REVIEW'", upper)
        self.assertIn(
            "'PREFLIGHT_CAN_CERTIFY_COMPLETE_HISTORY'",
            upper,
        )
        self.assertNotRegex(
            upper,
            re.compile(
                r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b"
            ),
        )


if __name__ == "__main__":
    unittest.main()
