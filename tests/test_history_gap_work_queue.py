from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import ContractError, canonical_json_bytes
from nhi_rule_history.history_gap_work_queue import (
    CROSS_LEDGER_SCHEMA,
    CROSS_PAIR_SCHEMA,
    DOCUMENT_CANDIDATE_SCHEMA,
    build_gap_work_units,
    write_gap_work_queue,
)


def _pair(index: int, *, native: bool, joint: bool | None) -> dict:
    return {
        "schema": CROSS_PAIR_SCHEMA,
        "pair_id": f"{index:064x}",
        "article_id": str(index),
        "article_num": f"9.{index}",
        "designation_kind": "official_dotted_numeric_candidate",
        "normalized_iso_candidate": f"202{index}-01-01",
        "marker_occurrence_count": index,
        "native_cross_format_date_candidate": native,
        "native_cross_format_joint_candidate": joint,
        "native_typed_date_artifact_sha256s": (
            [f"{index + 10:064x}"] if native else []
        ),
        "native_typed_joint_artifact_sha256s": (
            [f"{index + 20:064x}"] if joint else []
        ),
    }


def _wrapper(pair: dict) -> dict:
    return {
        "schema": CROSS_LEDGER_SCHEMA,
        "evidence_group": "article_date_pairs",
        "input_fingerprint": "a" * 64,
        "output_fingerprint": "b" * 64,
        "evidence": pair,
    }


def _candidate(pair: dict, count: int) -> dict:
    return {
        "schema": DOCUMENT_CANDIDATE_SCHEMA,
        "pair_id": pair["pair_id"],
        "article_id": pair["article_id"],
        "article_num": pair["article_num"],
        "designation_kind": pair["designation_kind"],
        "normalized_iso_candidate": pair["normalized_iso_candidate"],
        "candidate_count": count,
        "candidate_status": (
            "unique_document_candidate"
            if count == 1
            else "ambiguous_document_candidates"
        ),
        "official_document_candidates": [
            {
                "official_document_number_normalized": (
                    f"健保審字第115000000{value}號"
                ),
                "official_document_number_raw_values": [
                    f"健保審字第 115000000{value} 號"
                ],
                "artifact_sha256s": [f"{value + 30:064x}"],
                "resource_ids": [f"{value + 40:064x}"],
            }
            for value in range(count)
        ],
        "joint_evidence_artifact_sha256s": [f"{count + 50:064x}"],
        "amendment_effect_resolved": False,
        "legal_effective_date_resolved": False,
        "direct_predecessor_resolved": False,
    }


class HistoryGapWorkQueueTests(unittest.TestCase):
    def _ledgers(self, root: Path) -> tuple[Path, Path]:
        pairs = [
            _pair(1, native=True, joint=True),
            _pair(2, native=True, joint=True),
            _pair(3, native=True, joint=False),
            _pair(4, native=False, joint=False),
        ]
        cross = root / "cross.jsonl"
        cross.write_bytes(
            b"".join(
                canonical_json_bytes(_wrapper(pair)) for pair in pairs
            )
        )
        documents = root / "documents.jsonl"
        documents.write_bytes(
            canonical_json_bytes(_candidate(pairs[0], 1))
            + canonical_json_bytes(_candidate(pairs[1], 2))
        )
        return cross, documents

    def test_builds_one_stable_unit_per_pair_in_priority_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cross, documents = self._ledgers(Path(temporary))
            units, manifest = build_gap_work_units(
                cross, documents, declared_cut="2026-07-27"
            )
            self.assertEqual(len(units), 4)
            self.assertEqual(
                [row["priority"] for row in units], [1, 2, 3, 4]
            )
            self.assertEqual(
                manifest["counts_by_priority_lane"],
                {
                    "unique_document_candidate": 1,
                    "ambiguous_document_candidates": 1,
                    "native_date_without_joint_document_candidate": 1,
                    "marker_without_native_document_date_match": 1,
                },
            )
            self.assertFalse(units[0]["canonical_write_authorized"])
            self.assertEqual(
                units[0]["worker_authority"], "candidate_extraction_only"
            )

    def test_written_queue_is_hash_bound_and_row_count_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cross, documents = self._ledgers(root)
            output = root / "queue.jsonl"
            manifest_path = root / "manifest.json"
            manifest = write_gap_work_queue(
                cross,
                documents,
                output,
                manifest_path,
                declared_cut="2026-07-27",
                expected_row_count=4,
            )
            self.assertEqual(manifest["row_count"], 4)
            self.assertEqual(
                len(output.read_text(encoding="utf-8").splitlines()), 4
            )
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["queue"], manifest["queue"])
            with self.assertRaisesRegex(
                ContractError, "declared denominator"
            ):
                write_gap_work_queue(
                    cross,
                    documents,
                    output,
                    manifest_path,
                    declared_cut="2026-07-27",
                    expected_row_count=5,
                )

    def test_candidate_metadata_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cross, documents = self._ledgers(root)
            rows = [
                json.loads(line)
                for line in documents.read_text(encoding="utf-8").splitlines()
            ]
            rows[0]["article_num"] = "wrong"
            documents.write_bytes(
                b"".join(canonical_json_bytes(row) for row in rows)
            )
            with self.assertRaisesRegex(
                ContractError, "disagrees on article_num"
            ):
                build_gap_work_units(
                    cross, documents, declared_cut="2026-07-27"
                )
