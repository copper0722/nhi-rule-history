from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nhi_rule_history.history_marker_preflight_cli import (
    HistoryMarkerPreflightCliError,
    _read_json_object,
    _read_jsonl_objects,
    build_parser,
    run_from_files,
)


class HistoryMarkerPreflightCliTest(unittest.TestCase):
    def test_parser_requires_all_explicit_replay_inputs(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            build_parser().parse_args([])

    def test_json_readers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            object_path = root / "object.json"
            object_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(HistoryMarkerPreflightCliError):
                _read_json_object(object_path, label="object")
            rows_path = root / "rows.jsonl"
            rows_path.write_text("{}\n[]\n", encoding="utf-8")
            with self.assertRaises(HistoryMarkerPreflightCliError):
                _read_jsonl_objects(rows_path, label="rows")

    def test_run_writes_ledger_before_bound_compact_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_path = root / "run.json"
            articles_path = root / "articles.jsonl"
            markers_path = root / "markers.jsonl"
            run_path.write_text('{"state":"sealed"}', encoding="utf-8")
            articles_path.write_text('{"article_id":"1"}\n', encoding="utf-8")
            markers_path.write_text(
                '{"annotation_id":"a"}\n', encoding="utf-8"
            )
            ledger_path = root / "evidence.jsonl"
            output_path = root / "report.json"
            args = SimpleNamespace(
                annotation_run_json=run_path,
                annotation_articles_jsonl=articles_path,
                annotation_markers_jsonl=markers_path,
                annotation_receipt=root / "annotation-receipt.json",
                historical_receipt=root / "historical-receipt.json",
                historical_raw_dir=root / "raw",
                historical_structural_dir=root / "structural",
                evidence_ledger=ledger_path,
                output=output_path,
            )
            full = {
                "schema": "nhi-rule-history/history-marker-odt-preflight/v1",
                "evidence_rows": {"rows": []},
            }
            compact = {
                "schema": full["schema"],
                "evidence_ledger": {
                    "filename": ledger_path.name,
                    "sha256": "a" * 64,
                    "bytes": 0,
                    "row_counts": {"rows": 0},
                },
            }
            with (
                mock.patch(
                    "nhi_rule_history.history_marker_preflight_cli."
                    "analyze_history_marker_preflight",
                    return_value=full,
                ) as analyze,
                mock.patch(
                    "nhi_rule_history.history_marker_preflight_cli."
                    "write_evidence_ledger",
                    return_value=compact["evidence_ledger"],
                ) as write_ledger,
                mock.patch(
                    "nhi_rule_history.history_marker_preflight_cli."
                    "compact_public_report",
                    return_value=compact,
                ) as compact_report,
            ):
                result = run_from_files(args)
            self.assertEqual(result, compact)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                compact,
            )
            analyze.assert_called_once()
            write_ledger.assert_called_once_with(full, ledger_path)
            compact_report.assert_called_once_with(
                full,
                evidence_ledger=compact["evidence_ledger"],
            )


if __name__ == "__main__":
    unittest.main()
