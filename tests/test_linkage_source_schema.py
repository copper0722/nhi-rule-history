from __future__ import annotations

import sqlite3
import os
import unittest
from pathlib import Path

from tools.build_sqlite import TABLE_ORDER


ROOT = Path(__file__).resolve().parents[1]
PG_SCHEMA = (ROOT / "database" / "postgresql-schema.sql").read_text(
    encoding="utf-8"
)
SQLITE_SCHEMA = (ROOT / "database" / "sqlite-schema.sql").read_text(
    encoding="utf-8"
)


class LinkageSourceSchemaTests(unittest.TestCase):
    def test_pg_and_sqlite_preserve_source_observations(self) -> None:
        for table in (
            "linkage_import_run",
            "nhi_drug_item_observation",
            "nhi_drug_rule_reference",
        ):
            self.assertIn(f"CREATE TABLE {table}", PG_SCHEMA)
            self.assertIn(f"CREATE TABLE {table}", SQLITE_SCHEMA)
            self.assertIn(table, TABLE_ORDER)

        for field in (
            "dataset_identifier",
            "resource_id",
            "source_row_number",
            "source_record_sha256",
            "nhi_drug_code_raw",
            "atc_code_raw",
            "atc_code_normalized",
            "rule_section_raw",
            "rule_source_url",
            "resolution_status",
            "resolution_evidence_json",
        ):
            self.assertIn(field, PG_SCHEMA)
            self.assertIn(field, SQLITE_SCHEMA)

    def test_rule_reference_resolution_fails_closed(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            """
            INSERT INTO dataset_release (
                release_id, release_kind, official_label, source_page_url,
                manifest_sha256, status, created_at
            ) VALUES (
                'release-linkage', 'nhi_drug_item_snapshot', 'test',
                'https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111',
                ?, 'verified', '2026-07-27T00:00:00Z'
            )
            """,
            ("0" * 64,),
        )
        conn.execute(
            """
            INSERT INTO source_artifact (
                artifact_id, official_url, filename, media_type, byte_length,
                sha256, fetched_at, fetch_transport, licence, parse_status
            ) VALUES (
                'artifact-linkage',
                'https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=x',
                'items.csv', 'text/csv', 10, ?,
                '2026-07-27T00:00:00Z', 'https',
                '政府資料開放授權條款第1版', 'verified'
            )
            """,
            ("1" * 64,),
        )
        conn.execute(
            """
            INSERT INTO linkage_import_run (
                linkage_import_run_id, release_id, artifact_id, source_system,
                dataset_identifier, resource_id, parser_version, raw_row_count,
                distinct_product_count, state, counts_json, started_at,
                completed_at
            ) VALUES (
                'run-linkage', 'release-linkage', 'artifact-linkage',
                'NHI_IODE_DRUG_ITEMS', 'A21030000I-E41001',
                'A21030000I-E41001-001', 'test/1', 1, 1, 'validated', '{}',
                '2026-07-27T00:00:00Z', '2026-07-27T00:00:01Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO nhi_drug_item_observation (
                observation_id, linkage_import_run_id, source_row_number,
                source_record_sha256, product_resolution_status,
                nhi_drug_code_raw, atc_code_raw, atc_code_normalized,
                raw_record_json
            ) VALUES (
                'observation-1', 'run-linkage', 2, ?, 'unresolved',
                'AC58256100', 'n06ab05', 'N06AB05', '{}'
            )
            """,
            ("2" * 64,),
        )
        conn.execute(
            """
            INSERT INTO nhi_drug_rule_reference (
                rule_reference_id, observation_id, reference_order,
                rule_section_raw, rule_source_url, resolution_status,
                resolution_evidence_json
            ) VALUES (
                'reference-unresolved', 'observation-1', 1, '1.2.1.',
                'https://info.nhi.gov.tw/example.pdf',
                'unresolved_designation', '{}'
            )
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO nhi_drug_rule_reference (
                    rule_reference_id, observation_id, reference_order,
                    resolution_status, resolution_evidence_json
                ) VALUES (
                    'reference-invalid', 'observation-1', 2,
                    'snapshot_resolved', '{}'
                )
                """
            )

        conn.executemany(
            """
            INSERT INTO rule_identity (
                rule_id, canonical_slug, identity_status, first_seen_release_id
            ) VALUES (?, ?, 'active', 'release-linkage')
            """,
            (("rule-1", "rule-1"), ("rule-2", "rule-2")),
        )
        conn.execute(
            """
            INSERT INTO rule_snapshot (
                snapshot_id, rule_id, release_id, raw_text, normalized_text,
                structured_json, raw_sha256, normalized_sha256,
                source_locator_json, parser_version, validation_status,
                publication_status
            ) VALUES (
                'snapshot-1', 'rule-1', 'release-linkage', 'a', 'a', '{}',
                ?, ?, '{}', 'test/1', 'verified', 'blocked'
            )
            """,
            ("3" * 64, "4" * 64),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO nhi_drug_rule_reference (
                    rule_reference_id, observation_id, reference_order,
                    rule_id, snapshot_id, resolution_status,
                    resolution_evidence_json
                ) VALUES (
                    'reference-cross-rule', 'observation-1', 3,
                    'rule-2', 'snapshot-1', 'snapshot_resolved', '{}'
                )
                """
            )

        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class PostgresLinkageSourceSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.environ.get("NHI_RULE_HISTORY_LINKAGE_TEST_DSN")
        if not dsn:
            raise unittest.SkipTest(
                "NHI_RULE_HISTORY_LINKAGE_TEST_DSN is not set"
            )
        try:
            import psycopg
        except ImportError as exc:
            raise unittest.SkipTest("psycopg is not installed") from exc
        cls.psycopg = psycopg
        cls.conn = psycopg.connect(dsn)
        existing = cls.conn.execute(
            "SELECT to_regnamespace('nhi_rule_history')"
        ).fetchone()[0]
        if existing is not None:
            cls.conn.close()
            raise unittest.SkipTest(
                "linkage test DSN must not contain nhi_rule_history schema"
            )
        cls.conn.execute(PG_SCHEMA)

    @classmethod
    def tearDownClass(cls) -> None:
        conn = getattr(cls, "conn", None)
        if conn is not None and not conn.closed:
            conn.rollback()
            conn.close()

    def test_postgres_rejects_non_hex_source_record_hash(self) -> None:
        self.conn.execute(
            """
            INSERT INTO nhi_rule_history.dataset_release (
                release_id, release_kind, official_label, source_page_url,
                manifest_sha256, status
            ) VALUES (
                'release-linkage', 'nhi_drug_item_snapshot', 'test',
                'https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111',
                %s, 'verified'
            )
            """,
            ("0" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO nhi_rule_history.source_artifact (
                artifact_id, official_url, filename, media_type, byte_length,
                sha256, fetched_at, fetch_transport, licence, parse_status
            ) VALUES (
                'artifact-linkage',
                'https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=x',
                'items.csv', 'text/csv', 10, %s,
                '2026-07-27T00:00:00Z', 'https',
                '政府資料開放授權條款第1版', 'verified'
            )
            """,
            ("1" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO nhi_rule_history.linkage_import_run (
                linkage_import_run_id, release_id, artifact_id, source_system,
                dataset_identifier, resource_id, parser_version, raw_row_count,
                distinct_product_count, state, counts_json, started_at
            ) VALUES (
                'run-linkage', 'release-linkage', 'artifact-linkage',
                'NHI_IODE_DRUG_ITEMS', 'A21030000I-E41001',
                'A21030000I-E41001-001', 'test/1', 1, 1, 'validated', '{}',
                '2026-07-27T00:00:00Z'
            )
            """
        )
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute(
                    """
                    INSERT INTO nhi_rule_history.nhi_drug_item_observation (
                        observation_id, linkage_import_run_id,
                        source_row_number, source_record_sha256,
                        product_resolution_status, nhi_drug_code_raw,
                        raw_record_json
                    ) VALUES (
                        'observation-invalid', 'run-linkage', 2, 'short',
                        'unresolved', 'AC58256100', '{}'
                    )
                    """
                )


if __name__ == "__main__":
    unittest.main()
