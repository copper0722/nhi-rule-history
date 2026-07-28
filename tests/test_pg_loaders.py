from __future__ import annotations

import re
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.parsers.odt import NON_CLAIM
from nhi_rule_history.pg import acquisition
from nhi_rule_history.pg.common import row_sha256

ROOT = Path(__file__).resolve().parents[1]
ACQ_MIGRATION = (
    ROOT / "pg/migrations/2026-07-27_nhi_rule_history_acquisition_v2.sql"
)
ACQ_ROLLBACK = (
    ROOT / "pg/migrations/2026-07-27_nhi_rule_history_acquisition_v2.rollback.sql"
)
STRUCTURAL_MIGRATION = (
    ROOT / "pg/migrations/2026-07-27_nhi_rule_history_structural_v2.sql"
)
STRUCTURAL_ROLLBACK = (
    ROOT / "pg/migrations/2026-07-27_nhi_rule_history_structural_v2.rollback.sql"
)


class MigrationContractTests(unittest.TestCase):
    def test_structural_schema_has_exactly_four_tables(self) -> None:
        sql = STRUCTURAL_MIGRATION.read_text(encoding="utf-8")
        tables = re.findall(
            r"CREATE TABLE tw_drug_history_structural_stage\.([a-z_]+)", sql
        )
        self.assertEqual(
            tables,
            [
                "parse_run",
                "structural_block",
                "occurrence_candidate",
                "parse_issue",
            ],
        )
        self.assertIn(NON_CLAIM, sql)
        for forbidden in (
            "stable_rule_id",
            "legal_effective_date date",
            "predecessor_id",
            "successor_id",
            "event_effect",
            "diff_payload",
        ):
            self.assertNotIn(forbidden, sql)

    def test_rollbacks_are_explicit_restrict_only(self) -> None:
        for path, marker in (
            (ACQ_ROLLBACK, "managed=tw_drug_history_acq_stage/v2"),
            (STRUCTURAL_ROLLBACK, "managed=tw_drug_history_structural_stage/v2"),
        ):
            sql = path.read_text(encoding="utf-8")
            code = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
            self.assertNotRegex(code, r"(?i)\bCASCADE\b")
            self.assertIn(marker, sql)
            self.assertRegex(sql, r"DROP SCHEMA [a-z_]+ RESTRICT")

    def test_acquisition_migration_matches_wp2_tables(self) -> None:
        sql = ACQ_MIGRATION.read_text(encoding="utf-8")
        for table in (
            "acquisition_run",
            "input_file",
            "discovery_observation",
            "discovered_resource",
            "fetch_attempt",
            "raw_artifact",
            "resource_artifact_link",
            "artifact_url_observation",
            "acquisition_issue",
        ):
            self.assertIn(f"tw_drug_history_acq_stage.{table}", sql)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE", sql)
        self.assertIn("source_row_sha256", sql)


class CanonicalHashTests(unittest.TestCase):
    def test_acquisition_source_hash_is_exact_canonical_row_bytes(self) -> None:
        row = {"schema": "synthetic/v2", "z": "值", "a": [1, 2]}
        import hashlib

        self.assertEqual(
            row_sha256(row),
            hashlib.sha256(canonical_json_bytes(row)).hexdigest(),
        )

    def test_structural_derived_hash_field_is_excluded(self) -> None:
        row = {"schema": "synthetic/v2", "value": 1}
        digest = row_sha256(row)
        enriched = {**row, "source_row_sha256": digest}
        self.assertEqual(
            row_sha256(enriched, derived_key="source_row_sha256"), digest
        )


class _FakeCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple]]] = []
        self.rowcount = -1
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self._last_sql = sql
        if "UPDATE tw_drug_history_acq_stage.acquisition_run" in sql:
            self.rowcount = 1

    def executemany(self, sql, params):
        self.executemany_calls.append((sql, list(params)))

    def fetchone(self):
        if "FROM tw_drug_history_acq_stage.acquisition_run" in self._last_sql:
            return None
        return (None,)


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_object = _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_object


class ConnectionLifecycleTests(unittest.TestCase):
    def test_apply_uses_cursor_executemany_and_fresh_verifier(self) -> None:
        run_id = str(uuid.uuid4())
        empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        material = acquisition.AcquisitionMaterial(
            run_id=run_id,
            source_plan_sha256="1" * 64,
            capture_cut="2026-07-27",
            discovery_manifest_sha256="2" * 64,
            raw_manifest_sha256="3" * 64,
            migration_sha256="4" * 64,
            code_sha256="5" * 64,
            rows={name: tuple() for name in acquisition.FILE_SCHEMAS},
            input_files=(
                {
                    "logical_name": "raw-manifest.json",
                    "schema_id": "x",
                    "content_sha256": "6" * 64,
                    "byte_size": 1,
                    "row_count": 1,
                },
            ),
            expected_counts={
                **{table: 0 for table in acquisition.TABLE_FILE},
                "input_file": 1,
                "artifact_bytes": 0,
            },
            table_fingerprints={
                table: empty for table in acquisition.TABLE_FILE
            },
            input_fingerprint="7" * 64,
            output_fingerprint="8" * 64,
            sealed_fingerprint="9" * 64,
        )
        connection = _FakeConnection()
        expected_result = {"run_id": run_id, "state": "sealed"}
        with (
            mock.patch.object(
                acquisition, "validate_acquisition_run", return_value=material
            ),
            mock.patch.object(
                acquisition,
                "verify_loaded_acquisition_run",
                return_value=expected_result,
            ) as verifier,
        ):
            result = acquisition.load_acquisition_run(
                Path(tempfile.gettempdir()),
                conninfo="",
                connect=lambda _dsn: connection,
            )
        self.assertTrue(connection.cursor_object.executemany_calls)
        self.assertEqual(result["state"], "sealed")
        verifier.assert_called_once()


if __name__ == "__main__":
    unittest.main()
