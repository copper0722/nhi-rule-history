from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nhi_rule_history.export.canonical import CanonicalError  # noqa: E402
from nhi_rule_history.cli import build_parser  # noqa: E402
from nhi_rule_history.export.stage import (  # noqa: E402
    ExportError,
    export_stage_from_rows,
    rows_from_connection,
    verify_export_directory,
)
from nhi_rule_history.release import prepare_release  # noqa: E402


RUN_ID = "33ce4d34-ab19-40be-bbe6-f7838a97ead5"
FINGERPRINT = "2" * 64
ARTIFACT = "a" * 64
BLOCK = "b" * 64
OCCURRENCE = "c" * 64
ROW_HASH = "d" * 64
MANIFEST_HASH = "e" * 64
CODE_HASH = "f" * 64
RAW_TEXT = "1.1 synthetic text"
RAW_HASH = hashlib.sha256(RAW_TEXT.encode()).hexdigest()
SCHEMA = ROOT / "database" / "stage-sqlite-schema.sql"


def sample_rows() -> dict[str, list[dict]]:
    return {
        "rebuild_run": [
            {
                "run_id": RUN_ID,
                "state": "sealed",
                "parser_version": "parser/1",
                "loader_version": "loader/1",
                "contract_version": "contract/1",
                "code_hash": CODE_HASH,
                "input_fingerprint": "1" * 64,
                "sealed_fingerprint": FINGERPRINT,
                "output_fingerprint": None,
                "accepted_manifest_sha256": MANIFEST_HASH,
                "expected_counts": {"source_releases": 1},
                "verified_counts": {"source_releases": 1},
                "expected_release_count": 1,
                "expected_block_count": 1,
                "expected_occurrence_count": 1,
                "expected_empty_table_cell_block_count": 0,
                "expected_xml_ph_element_count_total": 1,
                "expected_xml_ph_emitted_unique_total": 1,
                "expected_xml_ph_unaccounted_total": 0,
                "created_at": "2026-07-26T02:00:00+08:00",
                "sealed_at": "2026-07-26T03:00:00+08:00",
                "failed_at": None,
                "failure_code": None,
                "failure_detail": None,
            }
        ],
        "run_input_file": [
            {
                "run_id": RUN_ID,
                "logical_name": "blocks",
                "declared_schema": "blocks/v1",
                "byte_length": 100,
                "row_count": 1,
                "content_sha256": "1" * 64,
                "relative_locator": "inputs/blocks.jsonl",
            }
        ],
        "source_release": [
            {
                "run_id": RUN_ID,
                "release_id": ARTIFACT,
                "source_order_index": 0,
                "relative_path": "history/synthetic.odt",
                "basename": "synthetic.odt",
                "content_sha256": ARTIFACT,
                "byte_length": 100,
                "filename_label_raw": "synthetic",
                "filename_id_prefix": None,
                "filename_date_fragments_raw": [],
                "analysis_chronology": {
                    "legal_date_inferred": False,
                    "statement": "analysis only",
                },
                "parser_version": "parser/1",
                "block_count": 1,
                "occurrence_count": 1,
                "table_count": 0,
                "row_count_xml": 0,
                "cell_count_xml": 0,
                "row_count_logical": 0,
                "cell_count_logical": 0,
                "empty_cell_count": 0,
                "nested_table_count": 0,
                "empty_table_cell_block_count": 0,
                "numeric_quantity_rejection_count": 0,
                "odt_repeat_attrs_present": False,
                "xml_ph_element_count": 1,
                "xml_ph_nested_count": 0,
                "xml_ph_emitted_unique": 1,
                "xml_ph_unaccounted": 0,
                "source_structural_block_count_before_repeat_expansion": 1,
                "accepted_manifest_sha256": MANIFEST_HASH,
                "accepted_manifest_match": True,
                "statement": "source occurrence only",
                "source_row_sha256": ROW_HASH,
            }
        ],
        "source_artifact": [
            {
                "run_id": RUN_ID,
                "artifact_sha256": ARTIFACT,
                "relative_locator": "history/synthetic.odt",
                "basename": "synthetic.odt",
                "byte_length": 100,
                "media_type": "application/vnd.oasis.opendocument.text",
                "content_sha256": ARTIFACT,
                "source_row_sha256": ROW_HASH,
            }
        ],
        "release_artifact": [
            {
                "run_id": RUN_ID,
                "release_id": ARTIFACT,
                "artifact_sha256": ARTIFACT,
                "association_role": "primary_parse_source",
            }
        ],
        "structural_block": [
            {
                "run_id": RUN_ID,
                "block_id": BLOCK,
                "artifact_sha256": ARTIFACT,
                "relative_path": "history/synthetic.odt",
                "block_kind": "paragraph",
                "container": "flow",
                "element_name": "p",
                "style_name": None,
                "in_table": False,
                "in_index_context": False,
                "xml_element_index": 0,
                "parser_order": 0,
                "locator": {"doc_order": 0},
                "locator_key": "doc_order=0",
                "raw_text": RAW_TEXT,
                "normalized_search_text": RAW_TEXT,
                "raw_text_sha256": RAW_HASH,
                "raw_text_byte_length": len(RAW_TEXT.encode()),
                "raw_text_char_length": len(RAW_TEXT),
                "parser_version": "parser/1",
                "source_row_sha256": ROW_HASH,
            }
        ],
        "occurrence_candidate": [
            {
                "run_id": RUN_ID,
                "occurrence_id": OCCURRENCE,
                "artifact_sha256": ARTIFACT,
                "block_id": BLOCK,
                "relative_path": "history/synthetic.odt",
                "designation_text": "1.1",
                "match_start_in_raw": 0,
                "match_end_in_raw": 3,
                "raw_text_sha256": RAW_HASH,
                "raw_text_byte_length": len(RAW_TEXT.encode()),
                "raw_text_char_length": len(RAW_TEXT),
                "container": "flow",
                "in_index_context": False,
                "ambiguity_flags": ["source_local_candidate_only"],
                "parser_version": "parser/1",
                "statement": "not stable identity",
                "source_row_sha256": ROW_HASH,
            }
        ],
        # Deliberately reversed: exporter must sort by full primary key.
        "stage_issue": [
            {
                "run_id": RUN_ID,
                "issue_seq": 1,
                "issue_code": "SECOND",
                "issue_class": "synthetic",
                "severity": "info",
                "is_blocking": False,
                "relative_path": None,
                "detail": "second",
                "artifact_sha256": None,
                "block_id": None,
                "locator_key": None,
                "attributes": {},
                "source_row_sha256": ROW_HASH,
            },
            {
                "run_id": RUN_ID,
                "issue_seq": 0,
                "issue_code": "FIRST",
                "issue_class": "synthetic",
                "severity": "info",
                "is_blocking": False,
                "relative_path": None,
                "detail": "first",
                "artifact_sha256": None,
                "block_id": None,
                "locator_key": None,
                "attributes": {},
                "source_row_sha256": ROW_HASH,
            },
        ],
    }


class StagePublicExportTest(unittest.TestCase):
    def export(self, parent: Path) -> tuple[Path, dict]:
        output = parent / "export"
        manifest = export_stage_from_rows(
            sample_rows(),
            run_id=RUN_ID,
            fingerprint=FINGERPRINT,
            output_dir=output,
            schema_path=SCHEMA,
        )
        return output, manifest

    def test_export_jsonl_sqlite_typed_parity_and_non_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output, manifest = self.export(Path(tmp))
            self.assertFalse(manifest["legal_history_claim"])
            self.assertEqual(manifest["table_counts"]["stage_issue"], 2)
            issue_rows = [
                json.loads(line)
                for line in (output / "stage_issue.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual([row["issue_seq"] for row in issue_rows], [0, 1])
            self.assertTrue(
                (output / "stage_issue.jsonl").read_bytes().endswith(b"\n")
            )

            conn = sqlite3.connect(output / "nhi-rule-history-stage-v1.sqlite")
            try:
                metadata = conn.execute(
                    """
                    SELECT legal_history_claim, logical_row_digest, dataset_kind
                    FROM dataset_metadata
                    """
                ).fetchone()
                self.assertEqual(metadata[0], 0)
                self.assertEqual(metadata[1], manifest["logical_row_digest"])
                self.assertEqual(metadata[2], "source_occurrence_staging")
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            finally:
                conn.close()
            verified = verify_export_directory(output)
            self.assertEqual(
                verified["logical_row_digest"], manifest["logical_row_digest"]
            )

    def test_allowlist_rejects_unknown_column(self) -> None:
        rows = sample_rows()
        rows["stage_issue"][0]["private_note"] = "must not leak"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CanonicalError):
                export_stage_from_rows(
                    rows,
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    output_dir=Path(tmp) / "export",
                    schema_path=SCHEMA,
                )

    def test_exact_fingerprint_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExportError):
                export_stage_from_rows(
                    sample_rows(),
                    run_id=RUN_ID,
                    fingerprint="9" * 64,
                    output_dir=Path(tmp) / "export",
                    schema_path=SCHEMA,
                )

    def test_connection_uses_read_only_snapshot_and_exact_pair_guard(self) -> None:
        rows = sample_rows()

        class Cursor:
            def __init__(self) -> None:
                self.executions: list[tuple[str, tuple | None]] = []
                self.result: list[tuple] = []

            def execute(self, sql: str, params: tuple | None = None) -> None:
                self.executions.append((sql, params))
                if "SELECT 1" in sql:
                    self.result = [(1,)]
                    return
                for table_name, table_rows in rows.items():
                    if f'"{table_name}"' not in sql:
                        continue
                    from nhi_rule_history.export.contract import TABLE_BY_NAME

                    columns = TABLE_BY_NAME[table_name].columns
                    self.result = [
                        tuple(row[column.name] for column in columns)
                        for row in table_rows
                    ]
                    return
                self.result = []

            def fetchone(self) -> tuple | None:
                return self.result[0] if self.result else None

            def fetchall(self) -> list[tuple]:
                return list(self.result)

            def close(self) -> None:
                pass

        class Connection:
            def __init__(self) -> None:
                self.the_cursor = Cursor()

            def cursor(self) -> Cursor:
                return self.the_cursor

        connection = Connection()
        result = rows_from_connection(
            connection,
            run_id=RUN_ID,
            fingerprint=FINGERPRINT,
        )
        executions = connection.the_cursor.executions
        self.assertIn(
            "REPEATABLE READ READ ONLY",
            executions[0][0],
        )
        self.assertEqual(executions[1][1], (RUN_ID, FINGERPRINT))
        self.assertEqual(len(result), 8)
        self.assertEqual(result["rebuild_run"][0]["sealed_fingerprint"], FINGERPRINT)

    def test_redaction_scan_fails_closed(self) -> None:
        rows = sample_rows()
        rows["structural_block"][0]["raw_text"] = "/Users/alice/private"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CanonicalError):
                export_stage_from_rows(
                    rows,
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    output_dir=Path(tmp) / "export",
                    schema_path=SCHEMA,
                )

    def test_noncanonical_jsonl_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output, _ = self.export(Path(tmp))
            path = output / "stage_issue.jsonl"
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            with self.assertRaises(CanonicalError):
                verify_export_directory(output)

    def test_prepare_release_never_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            export, _ = self.export(parent)
            release = parent / "release"
            manifest = prepare_release(export_dir=export, output_dir=release)
            self.assertEqual(manifest["status"], "prepared_not_published")
            self.assertFalse(manifest["publication_performed"])
            self.assertEqual(
                manifest["verification"]["network_publication"], "not_performed"
            )
            self.assertTrue((release / "release-manifest.json").is_file())
            json_assets = [
                name
                for name in manifest["assets"]
                if name.endswith(".jsonl") or name.endswith(".jsonl.zst")
            ]
            self.assertEqual(len(json_assets), 8)

    def test_unified_cli_exposes_export_verify_and_release(self) -> None:
        parser = build_parser()
        export = parser.parse_args(
            [
                "export",
                "--dsn",
                "postgresql://example.invalid/test",
                "--run-id",
                RUN_ID,
                "--fingerprint",
                FINGERPRINT,
                "--output-dir",
                "build/export",
            ]
        )
        self.assertEqual(export.command, "export")
        self.assertEqual(export.schema, SCHEMA)
        verify = parser.parse_args(
            ["verify-export", "--input-dir", "build/export"]
        )
        self.assertEqual(verify.command, "verify-export")
        release = parser.parse_args(
            [
                "release",
                "--export-dir",
                "build/export",
                "--output-dir",
                "build/release",
            ]
        )
        self.assertEqual(release.command, "release")


if __name__ == "__main__":
    unittest.main()
