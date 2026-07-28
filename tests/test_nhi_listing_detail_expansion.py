from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    DISCOVERY_MANIFEST_SCHEMA,
    PLAN_SCHEMA,
    RAW_ARTIFACT_SCHEMA,
    RAW_MANIFEST_SCHEMA,
    RESOURCE_ARTIFACT_LINK_SCHEMA,
    canonical_json_bytes,
    file_sha256,
    manifest_file_entry,
    stable_id,
)
from nhi_rule_history.discovery.nhi_listing_detail_expansion import (
    expand_nhi_listing_details,
    materialize_attachment_fetch_run,
    verify_nhi_listing_detail_expansion,
)
from nhi_rule_history.raw import RawStore


ADAPTER_ID = "nhi-amendment-listing-3258-fixture"
SOURCE_PLAN_SHA256 = "1" * 64
OBSERVED_AT = "2026-07-27T00:00:00+00:00"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(
        b"".join(canonical_json_bytes(row) for row in rows)
    )


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _resource(
    resource_id: str,
    ordinal: int,
) -> dict[str, Any]:
    page_number = (ordinal - 1) // 20 + 1
    row_ordinal = (ordinal - 1) % 20 + 1
    source_url = (
        "https://www.nhi.gov.tw/ch/"
        f"cp-{1000 + ordinal}-abc{ordinal}-3258-1.html"
    )
    listing_url = "https://www.nhi.gov.tw/ch/lp-3258-1.html"
    if page_number > 1:
        listing_url += f"?pi={page_number}&ps=20"
    return {
        "schema": DISCOVERED_RESOURCE_SCHEMA,
        "resource_id": resource_id,
        "adapter_id": ADAPTER_ID,
        "resource_kind": "official_detail_page",
        "source_url": source_url,
        "discovery_locator": {
            "surface": "nhi_amendment_listing_3258",
            "stable_row_identity": f"url:{source_url}",
            "displayed_ordinal": ordinal,
            "document_number_raw": f"健保審字第{ordinal:010d}號",
            "document_date_raw": "115-07-27",
            "listing_date_raw": "115-07-27",
            "expiry_date_raw": "118-07-27",
            "listing_occurrences": [
                {
                    "listing_page_url": listing_url,
                    "page_number": page_number,
                    "row_ordinal": row_ordinal,
                }
            ],
        },
        "source_label": f"fixture detail {ordinal}",
        "fetch_state": "pending",
    }


def _reseal(run_dir: Path) -> None:
    manifest_path = run_dir / "raw-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = [entry["filename"] for entry in manifest["files"]]
    manifest["files"] = [
        manifest_file_entry(run_dir / filename)
        for filename in names
    ]
    resources = _rows(run_dir / "discovered-resources.jsonl")
    artifacts = _rows(run_dir / "raw-artifacts.jsonl")
    links = _rows(run_dir / "resource-artifact-links.jsonl")
    manifest["counts"] = {
        "resources": len(resources),
        "artifacts": len(artifacts),
        "resource_artifact_links": len(links),
        "artifact_bytes": sum(row["byte_size"] for row in artifacts),
    }
    manifest_path.write_bytes(canonical_json_bytes(manifest))


def _fixture_run(root: Path) -> Path:
    run_dir = root / "raw-run"
    run_dir.mkdir()
    for filename in (
        "discovery-observations.jsonl",
        "discovered-resources.jsonl",
        "fetch-attempts.jsonl",
        "raw-artifacts.jsonl",
        "resource-artifact-links.jsonl",
        "artifact-url-observations.jsonl",
        "issues.jsonl",
    ):
        (run_dir / filename).write_bytes(b"")
    resources = [
        _resource(stable_id("detail", str(ordinal)), ordinal)
        for ordinal in range(1, 4)
    ]
    payloads = [
        b"""<!doctype html><html><body>
        <a href="/ch/dl-10001-aa111">shared attachment</a>
        <a href="/ch/dl-10002-bb222">unique attachment</a>
        <a href="/ch/dl-10001-aa111">duplicate same detail</a>
        </body></html>""",
        b"""<!doctype html><html><body>
        <a href="https://www.nhi.gov.tw/ch/dl-10001-aa111">
        shared under a second detail</a></body></html>""",
        b"<!doctype html><html><body><p>no attachment</p></body></html>",
    ]
    store = RawStore(run_dir)
    artifacts: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for resource, payload in zip(resources, payloads, strict=True):
        stored = store.put(payload)
        artifacts.append(
            {
                "schema": RAW_ARTIFACT_SCHEMA,
                "artifact_sha256": stored.sha256,
                "byte_size": stored.byte_size,
                "content_path": stored.relative_path,
                "media_type": "text/html",
                "first_observed_at": OBSERVED_AT,
            }
        )
        link = {
            "schema": RESOURCE_ARTIFACT_LINK_SCHEMA,
            "link_id": stable_id(
                "resource-artifact-link",
                resource["resource_id"],
                stored.sha256,
            ),
            "resource_id": resource["resource_id"],
            "artifact_sha256": stored.sha256,
            "relation": "retrieved_representation",
            "observed_at": OBSERVED_AT,
        }
        links.append(link)
        attempts.append(
            {
                "schema": "nhi-rule-history/fetch-attempt/v2",
                "attempt_id": stable_id(
                    "fetch-attempt",
                    resource["resource_id"],
                    stored.sha256,
                ),
                "resource_id": resource["resource_id"],
                "source_url": resource["source_url"],
                "started_at": OBSERVED_AT,
                "completed_at": OBSERVED_AT,
                "status": "success",
                "acquisition_mode": "network",
                "http_status": 200,
                "final_url": resource["source_url"],
                "response_headers": {
                    "content-type": "text/html; charset=utf-8"
                },
                "artifact_sha256": stored.sha256,
                "byte_size": stored.byte_size,
            }
        )
        observations.append(
            {
                "schema": (
                    "nhi-rule-history/artifact-url-observation/v2"
                ),
                "url_observation_id": stable_id(
                    "url-observation",
                    resource["resource_id"],
                    stored.sha256,
                ),
                "resource_id": resource["resource_id"],
                "source_url": resource["source_url"],
                "artifact_sha256": stored.sha256,
                "relation_to_previous": "first_observation",
                "observed_at": OBSERVED_AT,
            }
        )
    _write_jsonl(run_dir / "discovered-resources.jsonl", resources)
    _write_jsonl(run_dir / "raw-artifacts.jsonl", artifacts)
    _write_jsonl(run_dir / "resource-artifact-links.jsonl", links)
    _write_jsonl(run_dir / "fetch-attempts.jsonl", attempts)
    _write_jsonl(
        run_dir / "artifact-url-observations.jsonl",
        observations,
    )
    (run_dir / "discovery-manifest.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": DISCOVERY_MANIFEST_SCHEMA,
                "source_plan_schema": PLAN_SCHEMA,
                "source_plan_sha256": SOURCE_PLAN_SHA256,
                "capture_cut": "2026-07-27",
                "completed_at": OBSERVED_AT,
                "status": "success",
            }
        )
    )
    manifested_names = (
        "discovery-observations.jsonl",
        "discovered-resources.jsonl",
        "fetch-attempts.jsonl",
        "raw-artifacts.jsonl",
        "resource-artifact-links.jsonl",
        "artifact-url-observations.jsonl",
        "issues.jsonl",
        "discovery-manifest.json",
    )
    manifest = {
        "schema": RAW_MANIFEST_SCHEMA,
        "source_plan_schema": PLAN_SCHEMA,
        "source_plan_sha256": SOURCE_PLAN_SHA256,
        "capture_cut": "2026-07-27",
        "completed_at": OBSERVED_AT,
        "status": "success",
        "transport": {},
        "counts": {},
        "outcomes": {"network": 3},
        "files": [
            manifest_file_entry(run_dir / filename)
            for filename in manifested_names
        ],
    }
    (run_dir / "raw-manifest.json").write_bytes(
        canonical_json_bytes(manifest)
    )
    _reseal(run_dir)
    return run_dir


class NhiListingDetailExpansionTests(unittest.TestCase):
    def test_zero_attachments_cross_detail_dedupe_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            stage_a = root / "stage-a"
            stage_b = root / "stage-b"
            manifest_a = expand_nhi_listing_details(
                raw_run,
                stage_a,
                expected_detail_count=3,
            )
            manifest_b = expand_nhi_listing_details(
                raw_run,
                stage_b,
                expected_detail_count=3,
            )

            self.assertEqual(
                manifest_a["counts"],
                {
                    "detail_resources": 3,
                    "detail_artifacts": 3,
                    "zero_attachment_details": 1,
                    "attachment_occurrences": 3,
                    "attachment_resources": 2,
                    "cross_detail_duplicate_occurrences": 1,
                    "max_attachments_per_detail": 2,
                },
            )
            self.assertEqual(manifest_a, manifest_b)
            for filename in (
                "detail-provenance.jsonl",
                "attachment-occurrences.jsonl",
                "discovery-observations.jsonl",
                "discovered-resources.jsonl",
                "issues.jsonl",
                "discovery-manifest.json",
                "detail-expansion-manifest.json",
            ):
                self.assertEqual(
                    (stage_a / filename).read_bytes(),
                    (stage_b / filename).read_bytes(),
                )
            resources = _rows(stage_a / "discovered-resources.jsonl")
            shared = next(
                row
                for row in resources
                if row["source_url"].endswith("dl-10001-aa111")
            )
            parent_occurrences = shared["discovery_locator"][
                "parent_occurrences"
            ]
            self.assertEqual(len(parent_occurrences), 2)
            self.assertEqual(
                [
                    row["global_occurrence_ordinal"]
                    for row in parent_occurrences
                ],
                [1, 3],
            )
            self.assertEqual(
                parent_occurrences[0]["parent_discovery_locator"][
                    "displayed_ordinal"
                ],
                1,
            )
            self.assertEqual(
                parent_occurrences[1]["parent_discovery_locator"][
                    "displayed_ordinal"
                ],
                2,
            )
            receipt = verify_nhi_listing_detail_expansion(
                raw_run,
                stage_a,
                expected_detail_count=3,
            )
            self.assertTrue(receipt["byte_identical_replay"])

    def test_materializes_fresh_standard_fetch_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            stage = root / "stage"
            fetch_run = root / "attachment-fetch"
            expand_nhi_listing_details(
                raw_run,
                stage,
                expected_detail_count=3,
            )
            receipt = materialize_attachment_fetch_run(
                raw_run,
                stage,
                fetch_run,
                expected_detail_count=3,
            )
            self.assertEqual(receipt["resources"], 2)
            self.assertEqual(
                receipt["source_plan_sha256"],
                SOURCE_PLAN_SHA256,
            )
            self.assertEqual(
                (fetch_run / "discovered-resources.jsonl").read_bytes(),
                (stage / "discovered-resources.jsonl").read_bytes(),
            )
            for filename in (
                "fetch-attempts.jsonl",
                "raw-artifacts.jsonl",
                "resource-artifact-links.jsonl",
                "artifact-url-observations.jsonl",
            ):
                self.assertEqual(
                    (fetch_run / filename).read_bytes(),
                    b"",
                )
            self.assertFalse((fetch_run / "raw-manifest.json").exists())

    def test_missing_raw_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            (raw_run / "raw-manifest.json").unlink()
            with self.assertRaises(ContractError):
                expand_nhi_listing_details(
                    raw_run,
                    root / "stage",
                    expected_detail_count=3,
                )

    def test_unverified_blob_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            artifact = _rows(raw_run / "raw-artifacts.jsonl")[0]
            (raw_run / artifact["content_path"]).write_bytes(b"tampered")
            with self.assertRaises(ContractError):
                expand_nhi_listing_details(
                    raw_run,
                    root / "stage",
                    expected_detail_count=3,
                )

    def test_ambiguous_artifact_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            links = _rows(raw_run / "resource-artifact-links.jsonl")
            duplicate = dict(links[0])
            duplicate["link_id"] = stable_id(
                "second-link",
                duplicate["resource_id"],
                duplicate["artifact_sha256"],
            )
            links.append(duplicate)
            _write_jsonl(
                raw_run / "resource-artifact-links.jsonl",
                links,
            )
            _reseal(raw_run)
            with self.assertRaisesRegex(
                ContractError,
                "link counts|exactly one artifact binding",
            ):
                expand_nhi_listing_details(
                    raw_run,
                    root / "stage",
                    expected_detail_count=3,
                )

    def test_non_html_artifact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            artifacts = _rows(raw_run / "raw-artifacts.jsonl")
            artifacts[0]["media_type"] = "application/pdf"
            _write_jsonl(raw_run / "raw-artifacts.jsonl", artifacts)
            _reseal(raw_run)
            with self.assertRaisesRegex(
                ContractError,
                "not verified as text/html",
            ):
                expand_nhi_listing_details(
                    raw_run,
                    root / "stage",
                    expected_detail_count=3,
                )

    def test_malformed_listing_locator_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            resources = _rows(raw_run / "discovered-resources.jsonl")
            resources[0]["discovery_locator"][
                "document_number_raw"
            ] = ""
            _write_jsonl(
                raw_run / "discovered-resources.jsonl",
                resources,
            )
            _reseal(raw_run)
            with self.assertRaisesRegex(
                ContractError,
                "document-number locator is empty",
            ):
                expand_nhi_listing_details(
                    raw_run,
                    root / "stage",
                    expected_detail_count=3,
                )

    def test_output_inside_raw_run_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            with self.assertRaisesRegex(
                ContractError,
                "must not mutate an input",
            ):
                expand_nhi_listing_details(
                    raw_run,
                    raw_run / "derived",
                    expected_detail_count=3,
                )

    def test_modified_stage_fails_replay_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_run = _fixture_run(root)
            stage = root / "stage"
            expand_nhi_listing_details(
                raw_run,
                stage,
                expected_detail_count=3,
            )
            path = stage / "attachment-occurrences.jsonl"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaises(ContractError):
                verify_nhi_listing_detail_expansion(
                    raw_run,
                    stage,
                    expected_detail_count=3,
                )


if __name__ == "__main__":
    unittest.main()
