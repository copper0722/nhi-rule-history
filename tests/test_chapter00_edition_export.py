from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.edition_history import (
    DIFF_VERSION,
    EXTRACTOR_VERSION,
    build_sqlite,
)


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data" / "templates" / "chapter-00"
MANIFEST = json.loads((EXPORT / "manifest.json").read_text(encoding="utf-8"))
MIGRATION = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_edition_v1.sql"
).read_text(encoding="utf-8")


def read_jsonl(name: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (EXPORT / f"{name}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


class Chapter00EditionExportTests(unittest.TestCase):
    def test_manifest_hashes_and_counts_match_every_jsonl(self) -> None:
        self.assertTrue(MANIFEST["postgresql_is_authority"])
        self.assertFalse(MANIFEST["legal_history_complete"])
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

    def test_normalized_pg_export_has_complete_declared_edition_chain(self) -> None:
        versions = sorted(
            read_jsonl("rule_version"),
            key=lambda row: int(row["chronology_order"]),
        )
        edges = read_jsonl("version_edge")
        self.assertEqual(len(versions), 15)
        self.assertEqual(len(edges), 14)
        self.assertEqual(versions[0]["version_label"], "96年7月版")
        self.assertEqual(versions[-1]["version_label"], "通則(113.05.28更新)")
        self.assertEqual(
            {edge["newer_version_id"] for edge in edges},
            {version["version_id"] for version in versions[1:]},
        )
        self.assertTrue(
            all(edge["adjacency_basis"] == "adjacent_official_edition" for edge in edges)
        )
        self.assertTrue(
            all(edge["legal_predecessor_status"] == "not_claimed" for edge in edges)
        )

    def test_dates_are_typed_by_role_and_annotations_stay_unresolved(self) -> None:
        date_rows = read_jsonl("rule_version_date")
        primary = [
            row
            for row in date_rows
            if row["date_role"]
            in {"official_edition_label", "official_update_date"}
        ]
        annotations = [
            row
            for row in date_rows
            if row["date_role"] == "text_amendment_annotation"
        ]
        self.assertEqual(len(primary), 15)
        self.assertGreater(len(annotations), 400)
        self.assertTrue(
            all(row["legal_effective_status"] == "not_claimed" for row in primary)
        )
        self.assertTrue(
            all(
                row["legal_effective_status"]
                in {"candidate_unresolved", "rejected_non_date"}
                for row in annotations
            )
        )

    def test_current_chapter_has_whole_document_cross_check(self) -> None:
        version_sources = read_jsonl("version_source")
        cross_checks = [
            row
            for row in version_sources
            if row["evidence_role"] == "whole_document_cross_check"
        ]
        self.assertEqual(len(cross_checks), 1)
        self.assertIn(
            cross_checks[0]["parity_status"],
            {"exact_normalized", "format_only_difference"},
        )

    def test_import_run_is_sealed_with_expected_algorithms(self) -> None:
        runs = read_jsonl("import_run")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["state"], "sealed")
        self.assertEqual(runs[0]["extractor_version"], EXTRACTOR_VERSION)
        self.assertEqual(runs[0]["diff_version"], DIFF_VERSION)
        counts = runs[0]["row_counts"]
        self.assertEqual(counts["rule_version"], 15)
        self.assertEqual(counts["version_edge"], 14)

    def test_sqlite_is_rebuilt_from_jsonl_with_full_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chapter-00.sqlite"
            receipt = build_sqlite(
                jsonl_dir=EXPORT,
                schema_path=ROOT / "database" / "edition-sqlite-schema.sql",
                output=output,
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

    def test_migration_separates_edition_adjacency_from_legal_history(self) -> None:
        self.assertIn(
            "CREATE SCHEMA IF NOT EXISTS nhi_rule_history_edition",
            MIGRATION,
        )
        self.assertIn("adjacent_official_edition", MIGRATION)
        self.assertIn("legal_predecessor_status", MIGRATION)
        self.assertIn("official_source_universe_closed", MIGRATION)
        self.assertIn("legal_history_complete", MIGRATION)


if __name__ == "__main__":
    unittest.main()
