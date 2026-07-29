from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.clause_document_portable import (
    EXPORT_SCHEMA,
    TABLES,
    PortableClauseDocumentError,
    _canonical_json,
    _row_set_fingerprint,
    _sha256_bytes,
    build_sqlite,
)


def _fixture(directory: Path) -> None:
    receipts = {}
    for index, table in enumerate(TABLES):
        rows = [{"table": table, "index": index, "nested": {"ok": True}}]
        text = "".join(f"{_canonical_json(row)}\n" for row in rows)
        path = directory / f"{table}.jsonl"
        path.write_text(text, encoding="utf-8", newline="\n")
        receipts[table] = {
            "row_count": 1,
            "jsonl_sha256": _sha256_bytes(path.read_bytes()),
            "logical_row_set_fingerprint": _row_set_fingerprint(rows),
        }
    manifest = {
        "schema": EXPORT_SCHEMA,
        "normalization_run_id": "normalization",
        "diff_run_id": "diff",
        "clause_work_id": "work",
        "table_order": list(TABLES),
        "tables": receipts,
    }
    manifest["manifest_fingerprint"] = _sha256_bytes(
        _canonical_json(manifest).encode("utf-8")
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


class ClauseDocumentPortableTest(unittest.TestCase):
    def test_sqlite_rebuild_has_full_logical_row_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            output = root / "portable.sqlite"
            receipt = build_sqlite(root, output)
            self.assertEqual(receipt["logical_row_parity"], "passed")
            self.assertEqual(receipt["table_count"], len(TABLES))
            self.assertEqual(receipt["logical_row_count"], len(TABLES))
            with sqlite3.connect(output) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM portable_manifest"
                    ).fetchone(),
                    (len(TABLES),),
                )
                row_json = connection.execute(
                    'SELECT row_json FROM "clause_document_expression"'
                ).fetchone()[0]
                self.assertEqual(
                    json.loads(row_json)["table"],
                    "clause_document_expression",
                )

    def test_noncanonical_jsonl_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            path = root / "clause_document_expression.jsonl"
            path.write_text('{"index": 3, "table": "drift"}\n')
            with self.assertRaisesRegex(
                PortableClauseDocumentError, "not canonical JSON"
            ):
                build_sqlite(root, root / "portable.sqlite")


if __name__ == "__main__":
    unittest.main()
