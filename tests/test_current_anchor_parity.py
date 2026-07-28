from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import ContractError, file_sha256
from nhi_rule_history.current_anchor_parity import (
    analyze_current_anchor_occurrence_parity,
    current_anchor_occurrence_parity,
)


RUN_ID = "11111111-1111-4111-8111-111111111111"


def occurrence(
    label: str,
    designation: str,
    header: str,
) -> dict[str, object]:
    return {
        "schema": "nhi-rule-history/occurrence-candidate/v2",
        "parse_run_id": RUN_ID,
        "source_labels": [label],
        "designation_text": designation,
        "normalized_search_text": header,
    }


class CurrentAnchorParityTests(unittest.TestCase):
    def test_nfkc_and_whitespace_do_not_create_false_mismatch(self) -> None:
        report = analyze_current_anchor_occurrence_parity(
            [
                occurrence(
                    "最新版藥品給付規定內容(整份帶走)-115.7.23更新",
                    "5.6.3.",
                    "5.6.3. Drug Ａ",
                ),
                occurrence(
                    "第五節 激素及影響內分泌機轉藥物",
                    "5.6.3",
                    "5.6.3.Drug A",
                ),
            ]
        )
        self.assertEqual(report["status"], "matched_preflight")
        self.assertEqual(report["counts"]["matched_occurrences"], 1)
        self.assertFalse(
            report["claims"]["whole_split_clause_parity_complete"]
        )

    def test_real_text_difference_is_preserved_and_reported(self) -> None:
        report = analyze_current_anchor_occurrence_parity(
            [
                occurrence(
                    "最新版藥品給付規定內容(整份帶走)",
                    "8.2.16",
                    "8.2.16. Drug：(114/6/1)",
                ),
                occurrence(
                    "第八節 免疫製劑",
                    "8.2.16.",
                    "8.2.16. Drug：(114/6/1、115/8/1)",
                ),
            ]
        )
        self.assertEqual(report["status"], "mismatch_detected")
        self.assertEqual(report["counts"]["whole_only_occurrences"], 1)
        self.assertEqual(report["counts"]["split_only_occurrences"], 1)

    def test_file_wrapper_verifies_manifest_hash_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                occurrence(
                    "最新版藥品給付規定內容(整份帶走)",
                    "1.1",
                    "1.1 A",
                ),
                occurrence("第一節", "1.1", "1.1 A"),
            ]
            occurrence_path = root / "occurrence-candidates.jsonl"
            occurrence_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in rows
                ),
                encoding="utf-8",
            )
            (root / "structural-manifest.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "parse_run_id": RUN_ID,
                        "files": [
                            {
                                "filename": occurrence_path.name,
                                "sha256": file_sha256(occurrence_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "report.json"
            report = current_anchor_occurrence_parity(
                root, output=output
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                report,
            )

            occurrence_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "differs"):
                current_anchor_occurrence_parity(root)
