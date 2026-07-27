from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ARTIFACT_URL_OBSERVATION_SCHEMA,
    DISCOVERED_RESOURCE_SCHEMA,
    DISCOVERY_MANIFEST_SCHEMA,
    FETCH_ATTEMPT_SCHEMA,
    PLAN_SCHEMA,
    RAW_ARTIFACT_SCHEMA,
    RAW_MANIFEST_SCHEMA,
    RESOURCE_ARTIFACT_LINK_SCHEMA,
    ContractError,
    canonical_json_bytes,
    manifest_file_entry,
    stable_id,
)
from nhi_rule_history.discovery.source_universe_reconcile import (
    build_source_universe_reconciliation,
    normalize_document_number,
    parse_nhi_detail_metadata,
    write_source_universe_reconciliation,
)
from nhi_rule_history.raw import RawStore


OBSERVED_AT = "2026-07-27T00:00:00+00:00"


def _metadata_html(
    *,
    subject: str,
    document_number: str,
    document_date: str,
    basis: str | None = "法定依據",
    announcement: str | None = "公告內容",
    volatile: str = "",
) -> bytes:
    optional = ""
    if basis is not None:
        optional += f"<tr><td>依據</td><td>{basis}</td></tr>"
    if announcement is not None:
        optional += f"<tr><td>公告事項</td><td>{announcement}</td></tr>"
    return f"""<!doctype html><html><body>
    <div data-volatile="{volatile}">volatile page chrome</div>
    <table><thead><tr><th>項目</th><th>內容</th></tr></thead><tbody>
    <tr><td>主旨</td><td>{subject}</td></tr>
    <tr><td>發文字號</td><td>{document_number}</td></tr>
    {optional}
    <tr><td>發文日期</td><td>{document_date}</td></tr>
    </tbody></table></body></html>""".encode()


def _nhi_resource(
    ordinal: int,
    *,
    document_number: str,
    document_date: str,
    subject: str,
) -> dict[str, Any]:
    source_url = (
        f"https://www.nhi.gov.tw/ch/cp-{1000 + ordinal}"
        f"-abc{ordinal}-3258-1.html"
    )
    page_number = (ordinal - 1) // 20 + 1
    row_ordinal = (ordinal - 1) % 20 + 1
    listing_url = "https://www.nhi.gov.tw/ch/lp-3258-1.html"
    if page_number > 1:
        listing_url += f"?pi={page_number}&ps=20"
    return {
        "schema": DISCOVERED_RESOURCE_SCHEMA,
        "resource_id": stable_id("nhi-fixture", source_url),
        "adapter_id": "nhi-fixture",
        "resource_kind": "official_detail_page",
        "source_url": source_url,
        "discovery_locator": {
            "surface": "nhi_amendment_listing_3258",
            "stable_row_identity": f"url:{source_url}",
            "displayed_ordinal": ordinal,
            "document_number_raw": document_number,
            "document_date_raw": document_date,
            "listing_date_raw": document_date,
            "expiry_date_raw": "",
            "listing_occurrences": [
                {
                    "listing_page_url": listing_url,
                    "page_number": page_number,
                    "row_ordinal": row_ordinal,
                }
            ],
        },
        "source_label": subject,
        "fetch_state": "pending",
    }


def _fint_resource(
    row_number: int,
    *,
    document_number: str,
    document_date: str,
    subject: str,
) -> dict[str, Any]:
    source_url = (
        "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx?"
        f"RowNo={row_number}&starDate=20210101&endDate=20211231"
    )
    source_label = (
        "發文單位：衛生福利部中央健康保險署"
        f"發文字號：{document_number}"
        f"發文日期：{document_date}"
        "資料來源：衛生福利部中央健康保險署"
        f"主旨：{subject} 依據：法定依據 公告事項：公告內容"
    )
    return {
        "schema": DISCOVERED_RESOURCE_SCHEMA,
        "resource_id": stable_id("fint-fixture", source_url),
        "adapter_id": "fint-fixture",
        "resource_kind": "official_detail_page",
        "source_url": source_url,
        "discovery_locator": {
            "partition_id": "2021-01-01__2021-12-31",
            "query_id": "fixture",
            "row_number": row_number,
        },
        "source_label": source_label,
        "official_document_number_raw": document_number,
        "fetch_state": "cached_by_discovery",
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def _sealed_run(
    run_dir: Path,
    resources_and_payloads: list[tuple[dict[str, Any], bytes]],
    *,
    source_plan_sha256: str,
) -> None:
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
    resources: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    store = RawStore(run_dir)
    for resource, payload in resources_and_payloads:
        resources.append(resource)
        stored = store.put(payload)
        artifacts.setdefault(
            stored.sha256,
            {
                "schema": RAW_ARTIFACT_SCHEMA,
                "artifact_sha256": stored.sha256,
                "byte_size": stored.byte_size,
                "content_path": stored.relative_path,
                "media_type": "text/html",
                "first_observed_at": OBSERVED_AT,
            },
        )
        links.append(
            {
                "schema": RESOURCE_ARTIFACT_LINK_SCHEMA,
                "link_id": stable_id(
                    "fixture-link",
                    resource["resource_id"],
                    stored.sha256,
                ),
                "resource_id": resource["resource_id"],
                "artifact_sha256": stored.sha256,
                "relation": "retrieved_representation",
                "observed_at": OBSERVED_AT,
            }
        )
        attempts.append(
            {
                "schema": FETCH_ATTEMPT_SCHEMA,
                "attempt_id": stable_id(
                    "fixture-attempt",
                    resource["resource_id"],
                    stored.sha256,
                ),
                "resource_id": resource["resource_id"],
                "source_url": resource["source_url"],
                "started_at": OBSERVED_AT,
                "completed_at": OBSERVED_AT,
                "status": "success",
                "acquisition_mode": "discovery_cache",
                "http_status": 200,
                "final_url": resource["source_url"],
                "response_headers": {"content-type": "text/html"},
                "artifact_sha256": stored.sha256,
                "byte_size": stored.byte_size,
            }
        )
        observations.append(
            {
                "schema": ARTIFACT_URL_OBSERVATION_SCHEMA,
                "url_observation_id": stable_id(
                    "fixture-observation",
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
    _write_jsonl(run_dir / "raw-artifacts.jsonl", list(artifacts.values()))
    _write_jsonl(run_dir / "resource-artifact-links.jsonl", links)
    _write_jsonl(run_dir / "fetch-attempts.jsonl", attempts)
    _write_jsonl(run_dir / "artifact-url-observations.jsonl", observations)
    (run_dir / "discovery-manifest.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": DISCOVERY_MANIFEST_SCHEMA,
                "source_plan_schema": PLAN_SCHEMA,
                "source_plan_sha256": source_plan_sha256,
                "capture_cut": "2026-07-27",
                "completed_at": OBSERVED_AT,
                "status": "success",
                "adapters": [],
                "files": [],
                "counts": {},
            }
        )
    )
    filenames = (
        "discovery-observations.jsonl",
        "discovered-resources.jsonl",
        "fetch-attempts.jsonl",
        "raw-artifacts.jsonl",
        "resource-artifact-links.jsonl",
        "artifact-url-observations.jsonl",
        "issues.jsonl",
        "discovery-manifest.json",
    )
    (run_dir / "raw-manifest.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": RAW_MANIFEST_SCHEMA,
                "source_plan_schema": PLAN_SCHEMA,
                "source_plan_sha256": source_plan_sha256,
                "capture_cut": "2026-07-27",
                "completed_at": OBSERVED_AT,
                "status": "success",
                "transport": {},
                "outcomes": {"discovery_cache": len(resources)},
                "files": [
                    manifest_file_entry(run_dir / filename)
                    for filename in filenames
                ],
                "counts": {
                    "resources": len(resources),
                    "artifacts": len(artifacts),
                    "resource_artifact_links": len(links),
                    "artifact_bytes": sum(
                        row["byte_size"] for row in artifacts.values()
                    ),
                },
            }
        )
    )


class NhiDetailMetadataParserTests(unittest.TestCase):
    def test_parses_allowed_optional_rows_and_exact_locators(self) -> None:
        metadata = parse_nhi_detail_metadata(
            _metadata_html(
                subject="公告 <strong>藥品</strong> 修訂",
                document_number="健保審字第 1120000001 號",
                document_date="112-09-06",
                basis=None,
            )
        )
        fields = metadata.by_label()
        self.assertEqual(
            tuple(fields),
            ("主旨", "發文字號", "公告事項", "發文日期"),
        )
        self.assertEqual(fields["主旨"].value_normalized, "公告 藥品 修訂")
        self.assertEqual(
            fields["主旨"].locator(),
            {
                "table_ordinal": 1,
                "row_ordinal": 2,
                "label_cell_ordinal": 1,
                "value_cell_ordinal": 2,
            },
        )

    def test_rejects_ambiguous_or_noncanonical_metadata_tables(self) -> None:
        valid = _metadata_html(
            subject="公告",
            document_number="健保審字第1120000001號",
            document_date="112-09-06",
        ).decode()
        cases = {
            "multiple_tables": valid.replace(
                "</body>",
                "<table><tr><th>項目</th><th>內容</th></tr>"
                "<tr><td>主旨</td><td>x</td></tr>"
                "<tr><td>發文字號</td><td>第1120000002號</td></tr>"
                "<tr><td>發文日期</td><td>112-09-06</td></tr></table>"
                "</body>",
            ),
            "unknown_label": valid.replace(
                "<tr><td>依據</td>",
                "<tr><td>其他</td>",
            ),
            "duplicate_label": valid.replace(
                "<tr><td>依據</td><td>法定依據</td></tr>",
                "<tr><td>主旨</td><td>重複</td></tr>",
            ),
            "wrong_order": valid.replace(
                "<tr><td>主旨</td><td>公告</td></tr>",
                "<tr><td>__TEMP__</td><td>公告</td></tr>",
            )
            .replace(
                "<tr><td>發文字號</td><td>健保審字第1120000001號</td></tr>",
                "<tr><td>主旨</td><td>公告</td></tr>",
            )
            .replace(
                "<tr><td>__TEMP__</td><td>公告</td></tr>",
                "<tr><td>發文字號</td><td>健保審字第1120000001號</td></tr>",
            ),
            "missing_required": valid.replace(
                "<tr><td>發文日期</td><td>112-09-06</td></tr>",
                "",
            ),
        }
        for name, payload in cases.items():
            with self.subTest(name=name), self.assertRaises(ContractError):
                parse_nhi_detail_metadata(payload.encode())

    def test_document_number_normalization_preserves_missing_prefix(self) -> None:
        self.assertEqual(
            normalize_document_number(
                "114年2月19日健保醫字第 1140103154號公告"
            ),
            "健保醫字第1140103154號",
        )
        self.assertEqual(
            normalize_document_number("第1120672825號"),
            "第1120672825號",
        )
        with self.assertRaises(ContractError):
            normalize_document_number(
                "健保審字第1120000001號及第1120000002號"
            )


class SourceUniverseReconciliationTests(unittest.TestCase):
    def _fixture(self, root: Path, *, parity_mismatch: bool = False) -> tuple[Path, Path, Path]:
        nhi_a = root / "nhi-a"
        nhi_b = root / "nhi-b"
        fint = root / "fint"
        nhi_rows = [
            _nhi_resource(
                1,
                document_number="健保審字第1120000001號",
                document_date="112-09-06",
                subject="NHI only",
            ),
            _nhi_resource(
                2,
                document_number="健保審字第1120000002號",
                document_date="112-09-07",
                subject="Shared",
            ),
        ]
        payloads_a = [
            _metadata_html(
                subject=row["source_label"],
                document_number=row["discovery_locator"][
                    "document_number_raw"
                ],
                document_date=row["discovery_locator"]["document_date_raw"],
                volatile=f"a-{index}",
            )
            for index, row in enumerate(nhi_rows)
        ]
        payloads_b = [
            _metadata_html(
                subject=(
                    "Changed"
                    if parity_mismatch and index == 0
                    else row["source_label"]
                ),
                document_number=row["discovery_locator"][
                    "document_number_raw"
                ],
                document_date=row["discovery_locator"]["document_date_raw"],
                volatile=f"b-{index}",
            )
            for index, row in enumerate(nhi_rows)
        ]
        if parity_mismatch:
            nhi_rows_b = [dict(row) for row in nhi_rows]
            nhi_rows_b[0] = {
                **nhi_rows_b[0],
                "source_label": "Changed",
            }
        else:
            nhi_rows_b = nhi_rows
        _sealed_run(
            nhi_a,
            list(zip(nhi_rows, payloads_a, strict=True)),
            source_plan_sha256="1" * 64,
        )
        _sealed_run(
            nhi_b,
            list(zip(nhi_rows_b, payloads_b, strict=True)),
            source_plan_sha256="1" * 64,
        )
        fint_rows = [
            _fint_resource(
                1,
                document_number="健保審字第 1120000002 號",
                document_date="民國 112 年 09 月 07 日",
                subject="Shared",
            ),
            _fint_resource(
                2,
                document_number="健保審字第 1100000003 號",
                document_date="民國 110 年 01 月 01 日",
                subject="FINT only",
            ),
        ]
        _sealed_run(
            fint,
            [(row, b"<html>FINT detail</html>") for row in fint_rows],
            source_plan_sha256="2" * 64,
        )
        return nhi_a, nhi_b, fint

    def test_reconciles_surface_rows_and_preserves_temporal_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            nhi_a, nhi_b, fint = self._fixture(Path(temporary))
            report = build_source_universe_reconciliation(
                nhi_a,
                nhi_b,
                fint,
                expected_nhi_detail_count=2,
                expected_fint_detail_count=2,
            )
        self.assertEqual(
            report["status"],
            "passed_source_surface_reconciliation_not_legal_history",
        )
        self.assertEqual(
            report["counts"],
            {
                "nhi_listing_detail_records": 2,
                "fint_exact_phrase_detail_records": 2,
                "normalized_document_number_keys": 3,
                "intersection_keys": 1,
                "nhi_listing_only_keys": 1,
                "fint_exact_phrase_only_keys": 1,
            },
        )
        self.assertEqual(
            report["nhi_pass_a_b_parity"][
                "different_html_artifact_hash_pairs"
            ],
            2,
        )
        shared = report["intersection_rows"][0]
        self.assertEqual(
            shared["nhi_listing_records"][0]["raw_metadata"][
                "detail_title_raw"
            ],
            "Shared",
        )
        self.assertEqual(
            shared["fint_exact_phrase_records"][0]["title_raw"],
            "Shared",
        )
        self.assertEqual(
            report["temporal_surface_boundaries"][
                "fint_exact_phrase_capture"
            ]["declared_partition_start"],
            "2021-01-01",
        )
        self.assertFalse(
            report["classification_contract"]["legal_effective_date_inferred"]
        )

    def test_rejects_pass_a_b_metadata_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            nhi_a, nhi_b, fint = self._fixture(
                Path(temporary),
                parity_mismatch=True,
            )
            with self.assertRaisesRegex(
                ContractError,
                "parsed metadata projections differ",
            ):
                build_source_universe_reconciliation(
                    nhi_a,
                    nhi_b,
                    fint,
                    expected_nhi_detail_count=2,
                    expected_fint_detail_count=2,
                )

    def test_collisions_are_grouped_and_block_one_to_one_join(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nhi_a, nhi_b, fint = self._fixture(root)
            resources_path = fint / "discovered-resources.jsonl"
            resources = [
                json.loads(line)
                for line in resources_path.read_text().splitlines()
            ]
            resources[1]["official_document_number_raw"] = resources[0][
                "official_document_number_raw"
            ]
            resources[1]["source_label"] = resources[1][
                "source_label"
            ].replace(
                "健保審字第 1100000003 號",
                "健保審字第 1120000002 號",
            )
            _write_jsonl(resources_path, resources)
            manifest = json.loads(
                (fint / "raw-manifest.json").read_text()
            )
            manifest["files"] = [
                manifest_file_entry(fint / entry["filename"])
                for entry in manifest["files"]
            ]
            (fint / "raw-manifest.json").write_bytes(
                canonical_json_bytes(manifest)
            )
            report = build_source_universe_reconciliation(
                nhi_a,
                nhi_b,
                fint,
                expected_nhi_detail_count=2,
                expected_fint_detail_count=2,
            )
        self.assertEqual(
            report["status"],
            "passed_grouped_source_surface_reconciliation_with_collisions",
        )
        self.assertEqual(
            report["collision_checks"]["fint_exact_phrase"][
                "collision_key_count"
            ],
            1,
        )
        self.assertFalse(
            report["collision_checks"]["one_to_one_join_safe"]
        )
        self.assertEqual(
            len(report["intersection_rows"][0]["fint_exact_phrase_records"]),
            2,
        )

    def test_atomic_writer_is_idempotent_and_refuses_divergent_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nhi_a, nhi_b, fint = self._fixture(root)
            output = root / "receipt.json"
            first = write_source_universe_reconciliation(
                nhi_a,
                nhi_b,
                fint,
                output,
                expected_nhi_detail_count=2,
                expected_fint_detail_count=2,
            )
            second = write_source_universe_reconciliation(
                nhi_a,
                nhi_b,
                fint,
                output,
                expected_nhi_detail_count=2,
                expected_fint_detail_count=2,
            )
            self.assertEqual(first, second)
            output.write_text("{}\n")
            with self.assertRaisesRegex(
                ContractError,
                "already exists with different bytes",
            ):
                write_source_universe_reconciliation(
                    nhi_a,
                    nhi_b,
                    fint,
                    output,
                    expected_nhi_detail_count=2,
                    expected_fint_detail_count=2,
                )


if __name__ == "__main__":
    unittest.main()
