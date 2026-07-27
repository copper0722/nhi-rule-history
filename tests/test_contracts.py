from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    PLAN_SCHEMA,
    SourcePlan,
    assert_public_value,
    canonical_url,
    validate_jsonl_row,
)


class ContractTests(unittest.TestCase):
    def test_repository_source_plan_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        plan = SourcePlan.load(root / "sources" / "source-plan-v2.json")
        self.assertEqual(plan.document["schema"], PLAN_SCHEMA)
        fint = next(adapter for adapter in plan.adapters if adapter["kind"] == "mohw_fint")
        self.assertEqual(fint["start_date"], "2021-01-01")
        self.assertEqual(fint["end_date"], plan.document["capture_cut"])

    def test_public_rows_reject_secrets_and_local_paths(self) -> None:
        with self.assertRaises(ContractError):
            assert_public_value({"cookie": "abc"})
        with self.assertRaises(ContractError):
            assert_public_value({"content_path": "/Users/example/private"})
        assert_public_value(
            {
                "source_url": "https://www.nhi.gov.tw/ch/example",
                "content_path": "raw/sha256/aa/example",
            }
        )

    def test_canonical_url_sorts_query_and_removes_fragment(self) -> None:
        self.assertEqual(
            canonical_url("HTTPS://EXAMPLE.ORG/a?z=2&a=1#fragment"),
            "https://example.org/a?a=1&z=2",
        )

    def test_jsonl_schema_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ContractError):
            validate_jsonl_row(
                {
                    "schema": DISCOVERED_RESOURCE_SCHEMA,
                    "resource_id": "r",
                    "adapter_id": "a",
                    "resource_kind": "official_detail_page",
                    "source_url": "https://example.org/source",
                    "discovery_locator": {},
                    "source_label": "",
                    "fetch_state": "pending",
                    "legal_effective_date": "2026-01-01",
                }
            )

    def test_plan_rejects_unsupported_adapter(self) -> None:
        document = {
            "schema": PLAN_SCHEMA,
            "capture_cut": "2026-07-27",
            "allowed_hosts": ["example.org"],
            "adapters": [
                {
                    "id": "bad",
                    "kind": "unknown",
                    "base_url": "https://example.org/",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ContractError):
                SourcePlan.load(path)


if __name__ == "__main__":
    unittest.main()
