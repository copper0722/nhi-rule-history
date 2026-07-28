from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (
    ROOT
    / "contracts"
    / "history-closure-work-result-v3.schema.json"
)
METHOD = ROOT / "docs" / "agent-work-methodology.md"
COMPLETION = ROOT / "docs" / "completion-contract.md"
PROJECT = ROOT / "project.yaml"
LINKAGE = ROOT / "docs" / "linkage.md"


class MethodologyV3ContractTests(unittest.TestCase):
    def test_notice_is_optional_enrichment_not_a_required_result_field(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            "nhi-rule-history/history-closure-work-result/v3",
        )
        self.assertNotIn("official_event_id", schema["required"])
        serialized = json.dumps(schema, sort_keys=True)
        self.assertNotIn('"official_event_id"', serialized)
        self.assertIn(
            "notice_not_found_after_bounded_search",
            schema["properties"]["notice_linkage"]["properties"]["status"][
                "enum"
            ],
        )

    def test_transition_candidate_requires_evidence_adjacency_and_replay(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        conditional = schema["allOf"][0]
        then = conditional["then"]["properties"]
        self.assertEqual(then["transition_evidence"]["minItems"], 1)
        self.assertEqual(
            then["adjacency"]["properties"]["status"]["const"],
            "direct_in_declared_source_set",
        )
        self.assertEqual(
            then["replay"]["properties"]["edge_replay_status"]["const"],
            "passed",
        )

    def test_public_wording_does_not_turn_search_miss_into_nonexistence(self) -> None:
        method = METHOD.read_text(encoding="utf-8")
        completion = COMPLETION.read_text(encoding="utf-8")
        self.assertIn("不得記為「公告不存在」", method)
        self.assertIn("不是 transition completion gate", completion)
        self.assertIn(
            "evidence_complete_to_declared_cut_for_enumerated_official_versions",
            method,
        )

    def test_project_records_pause_and_v1_queue_supersession(self) -> None:
        project = PROJECT.read_text(encoding="utf-8")
        self.assertIn("status: paused_by_owner", project)
        self.assertIn(
            "disposition: retained_discovery_provenance_not_executable",
            project,
        )
        self.assertIn("admin_ops_project_todo_id: 41", project)

    def test_rule_rss_can_supply_product_linkage_without_duplicate_search(self) -> None:
        method = METHOD.read_text(encoding="utf-8")
        linkage = LINKAGE.read_text(encoding="utf-8")
        project = PROJECT.read_text(encoding="utf-8")
        self.assertIn("給付規定 RSS 優先", method)
        self.assertIn("drug_rule_link_evidence", linkage)
        self.assertIn("gov_健保審字第1150055452號", linkage)
        self.assertIn("separate_drug_announcement_required: false", project)


if __name__ == "__main__":
    unittest.main()
