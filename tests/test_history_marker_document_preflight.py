from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import ContractError, canonical_json_bytes
from nhi_rule_history.history_marker_document_preflight import (
    build_marker_document_candidate_preflight,
    write_marker_document_candidate_preflight,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def _cross_pair(
    pair_id: str,
    *,
    article_num: str,
    odt_joint: bool,
    native_artifacts: list[str],
) -> dict:
    return {
        "schema": "nhi-rule-history/history-marker-cross-format-evidence-ledger/v1",
        "evidence_group": "article_date_pairs",
        "evidence": {
            "pair_id": pair_id,
            "article_id": f"article-{article_num}",
            "article_num": article_num,
            "normalized_iso_candidate": "2020-01-01",
            "designation_kind": "official_dotted_numeric_candidate",
            "native_cross_format_joint_candidate": True,
            "odt_joint_candidate": odt_joint,
            "native_typed_joint_artifact_sha256s": native_artifacts,
        },
    }


def _odt_pair(pair_id: str, artifacts: list[str]) -> dict:
    return {
        "schema": "nhi-rule-history/history-marker-odt-evidence-ledger/v1",
        "evidence_group": "article_date_pairs",
        "evidence": {
            "pair_id": pair_id,
            "same_artifact_sha256s": artifacts,
        },
    }


def _resource(
    resource_id: str,
    *,
    document_number: str | None = None,
    parent_resource_id: str | None = None,
) -> dict:
    row = {
        "schema": "nhi-rule-history/discovered-resource/v2",
        "resource_id": resource_id,
        "adapter_id": "fixture",
        "resource_kind": (
            "official_detail_page"
            if parent_resource_id is None
            else "official_attachment"
        ),
        "source_url": f"https://example.test/{resource_id}",
        "discovery_locator": {"fixture": resource_id},
        "source_label": resource_id,
        "fetch_state": "pending",
    }
    if document_number is not None:
        row["official_document_number_raw"] = document_number
    if parent_resource_id is not None:
        row["parent_resource_id"] = parent_resource_id
    return row


def _link(link_id: str, resource_id: str, artifact: str) -> dict:
    return {
        "schema": "nhi-rule-history/resource-artifact-link/v2",
        "link_id": link_id,
        "resource_id": resource_id,
        "artifact_sha256": artifact,
        "relation": "retrieved_representation",
        "observed_at": "2026-07-27T00:00:00+00:00",
    }


class HistoryMarkerDocumentPreflightTest(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path]:
        paths = {
            "cross": root / "cross.jsonl",
            "odt": root / "odt.jsonl",
            "resources": root / "resources.jsonl",
            "links": root / "links.jsonl",
        }
        _write_jsonl(
            paths["cross"],
            [
                _cross_pair(
                    "pair-1",
                    article_num="1.1",
                    odt_joint=True,
                    native_artifacts=[],
                ),
                _cross_pair(
                    "pair-2",
                    article_num="1.2",
                    odt_joint=False,
                    native_artifacts=["b" * 64, "c" * 64],
                ),
            ],
        )
        _write_jsonl(
            paths["odt"],
            [
                _odt_pair("pair-1", ["a" * 64]),
                _odt_pair("pair-2", []),
            ],
        )
        _write_jsonl(
            paths["resources"],
            [
                _resource("detail-a", document_number="健保醫字第 12345678 號"),
                _resource("attachment-a", parent_resource_id="detail-a"),
                _resource("detail-b", document_number="健保醫字第 22222222 號"),
                _resource(
                    "attachment-b",
                    document_number="健保醫字第 22222222 號",
                    parent_resource_id="detail-b",
                ),
                _resource("detail-c", document_number="健保醫字第 33333333 號"),
            ],
        )
        _write_jsonl(
            paths["links"],
            [
                _link("link-a", "attachment-a", "a" * 64),
                _link("link-b", "attachment-b", "b" * 64),
                _link("link-c", "detail-c", "c" * 64),
            ],
        )
        return paths

    def test_unique_and_ambiguous_document_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            report, rows = build_marker_document_candidate_preflight(
                cross_format_ledger_path=paths["cross"],
                odt_ledger_path=paths["odt"],
                discovered_resources_path=paths["resources"],
                resource_artifact_links_path=paths["links"],
            )
        self.assertEqual(report["counts"]["native_joint_article_date_pairs"], 2)
        self.assertEqual(report["counts"]["unique_document_candidate_pairs"], 1)
        self.assertEqual(
            report["counts"]["ambiguous_document_candidate_pairs"], 1
        )
        self.assertEqual(report["counts"]["unmapped_joint_pairs"], 0)
        self.assertEqual(
            report["counts"]["candidate_count_distribution"],
            {"1": 1, "2": 1},
        )
        self.assertEqual(rows[0]["candidate_status"], "unique_document_candidate")
        self.assertEqual(
            rows[0]["official_document_candidates"][0][
                "official_document_number_normalized"
            ],
            "健保醫字第12345678號",
        )
        self.assertEqual(
            rows[1]["candidate_status"], "ambiguous_document_candidates"
        )
        self.assertFalse(rows[0]["amendment_effect_resolved"])
        self.assertFalse(report["claims"]["per_clause_history_complete"])

    def test_write_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._fixture(root)
            report_path = root / "report.json"
            ledger_path = root / "ledger.jsonl"
            first = write_marker_document_candidate_preflight(
                report_path=report_path,
                ledger_path=ledger_path,
                cross_format_ledger_path=paths["cross"],
                odt_ledger_path=paths["odt"],
                discovered_resources_path=paths["resources"],
                resource_artifact_links_path=paths["links"],
            )
            report_bytes = report_path.read_bytes()
            ledger_bytes = ledger_path.read_bytes()
            second = write_marker_document_candidate_preflight(
                report_path=report_path,
                ledger_path=ledger_path,
                cross_format_ledger_path=paths["cross"],
                odt_ledger_path=paths["odt"],
                discovered_resources_path=paths["resources"],
                resource_artifact_links_path=paths["links"],
            )
            self.assertEqual(report_bytes, report_path.read_bytes())
            self.assertEqual(ledger_bytes, ledger_path.read_bytes())
            self.assertEqual(first, second)
            self.assertEqual(
                json.loads(report_bytes)["output"]["ledger_rows"], 2
            )

    def test_joint_artifact_without_acquisition_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._fixture(root)
            _write_jsonl(paths["links"], [])
            with self.assertRaisesRegex(
                ContractError, "no acquisition binding"
            ):
                build_marker_document_candidate_preflight(
                    cross_format_ledger_path=paths["cross"],
                    odt_ledger_path=paths["odt"],
                    discovered_resources_path=paths["resources"],
                    resource_artifact_links_path=paths["links"],
                )

    def test_duplicate_pair_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._fixture(root)
            duplicate = _cross_pair(
                "pair-1",
                article_num="1.3",
                odt_joint=True,
                native_artifacts=[],
            )
            with paths["cross"].open("ab") as handle:
                handle.write(canonical_json_bytes(duplicate))
            with self.assertRaisesRegex(ContractError, "duplicate.*pair_id"):
                build_marker_document_candidate_preflight(
                    cross_format_ledger_path=paths["cross"],
                    odt_ledger_path=paths["odt"],
                    discovered_resources_path=paths["resources"],
                    resource_artifact_links_path=paths["links"],
                )


if __name__ == "__main__":
    unittest.main()
