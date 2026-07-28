from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from nhi_rule_history.contracts import (
    PLAN_SCHEMA,
    SourcePlan,
    file_sha256,
    iter_jsonl,
    write_json,
)
from nhi_rule_history.discovery import compare_discovery_runs, discover_run
from nhi_rule_history.fetch import HttpResponse, fetch_run
from nhi_rule_history.fetch.runner import media_type
from nhi_rule_history.parsers.odt import parse_verified_odt_run
from nhi_rule_history.raw.verify import verify_raw
from nhi_rule_history.release.evidence import (
    PrepareError,
    _verified_release_binding,
    _verified_structural_manifest,
)


def fint_html(row_number: int, last_row: int = 2) -> bytes:
    return f"""<!doctype html>
<html><body>
<a id="hlLast" href="/FINT/FINTQRY04.aspx?kw=x&amp;RowNo={last_row}">最末筆</a>
<table id="dat04">
<tr><td><pre>發文字號：測試字第 {row_number} 號
發文日期：民國 110 年 1 月 {row_number} 日
主旨：修訂藥品給付規定。</pre></td></tr>
<tr><td><a href="../Flaw/GetFile.ashx?PFID={row_number}">
附件{row_number}-修訂對照表.ODT</a></td></tr>
</table>
</body></html>""".encode("utf-8")


class FakeDiscoveryClient:
    allowed_hosts = ("mohwlaw.mohw.gov.tw", "www.nhi.gov.tw")

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        row_number = int(
            next(value for key, value in parse_qsl(urlsplit(url).query) if key == "RowNo")
        )
        return HttpResponse(
            request_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=fint_html(row_number),
        )


class FakeFetchClient:
    allowed_hosts = ("mohwlaw.mohw.gov.tw", "www.nhi.gov.tw")

    def __init__(self, version: bytes = b"v1"):
        self.calls: list[str] = []
        self.version = version

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        if "GetFile.ashx" in url:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(
                    "mimetype",
                    "application/vnd.oasis.opendocument.text",
                    compress_type=zipfile.ZIP_STORED,
                )
                archive.writestr(
                    "content.xml",
                    f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:text>
  <text:p text:style-name="P1">1.1 測試條文 {self.version.decode("ascii")} {url}</text:p>
 </office:text></office:body>
</office:document-content>""",
                )
            body = buffer.getvalue()
            content_type = "text/html"
        else:
            row_number = int(
                next(
                    value
                    for key, value in parse_qsl(urlsplit(url).query)
                    if key == "RowNo"
                )
            )
            body = fint_html(row_number) + self.version
            content_type = "text/html; charset=utf-8"
        return HttpResponse(
            request_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": content_type},
            body=body,
        )


def write_plan(path: Path) -> None:
    plan = {
        "schema": PLAN_SCHEMA,
        "capture_cut": "2021-01-01",
        "allowed_hosts": ["mohwlaw.mohw.gov.tw", "www.nhi.gov.tw"],
        "adapters": [
            {
                "id": "fint-test",
                "kind": "mohw_fint",
                "enabled": True,
                "base_url": "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx",
                "start_date": "2021-01-01",
                "end_date": "2021-01-01",
                "partition_months": 12,
                "queries": [{"id": "q1", "keywords": ["藥品給付規定"]}],
            }
        ],
    }
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


class AcquisitionTests(unittest.TestCase):
    def test_magic_bytes_override_incorrect_html_header(self) -> None:
        self.assertEqual(
            media_type({"content-type": "text/html"}, b"%PDF-1.7\nfixture"),
            "application/pdf",
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "mimetype",
                "application/vnd.oasis.opendocument.text",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr("content.xml", "<office:document/>")
        self.assertEqual(
            media_type({"content-type": "text/html"}, buffer.getvalue()),
            "application/vnd.oasis.opendocument.text",
        )
        self.assertEqual(
            media_type(
                {"content-type": "text/html"},
                b"GIF89a" + b"x" * 20,
            ),
            "image/gif",
        )
        self.assertEqual(
            media_type(
                {"content-type": "text/html"},
                b"\xff\xd8\xff" + b"x" * 20,
            ),
            "image/jpeg",
        )
        self.assertEqual(
            media_type(
                {"content-type": "text/html"},
                b"II*\x00" + b"x" * 20,
            ),
            "image/tiff",
        )

    def test_discover_resume_fetch_verify_and_no_semantic_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            run_dir = root / "run"
            write_plan(plan_path)

            discovery_client = FakeDiscoveryClient()
            discovery = discover_run(plan_path, run_dir, client=discovery_client)
            self.assertEqual(discovery["parity"], {"expected_rows": 2, "fetched_rows": 2})
            self.assertEqual(len(discovery_client.calls), 2)

            pass_b_dir = root / "run-b"
            pass_b = discover_run(
                plan_path,
                pass_b_dir,
                client=FakeDiscoveryClient(),
            )
            self.assertEqual(pass_b["counts"]["resources"], 4)
            parity = compare_discovery_runs(run_dir, pass_b_dir)
            self.assertEqual(parity["status"], "passed")
            self.assertEqual(parity["resource_count"], 4)
            write_json(run_dir / "discovery-parity.json", parity)

            discover_run(plan_path, run_dir, client=discovery_client)
            self.assertEqual(
                len(discovery_client.calls),
                2,
                "successful discovery observations must be reused",
            )
            resources = list(iter_jsonl(run_dir / "discovered-resources.jsonl"))
            self.assertEqual(len(resources), 4)
            forbidden = {
                "legal_effective_date",
                "stable_rule_identity",
                "rule_lineage",
                "predecessor_diff",
            }
            self.assertFalse(forbidden.intersection({key for row in resources for key in row}))

            fetch_client = FakeFetchClient()
            raw = fetch_run(plan_path, run_dir, client=fetch_client)
            self.assertEqual(raw["counts"]["resources"], 4)
            self.assertEqual(
                len(fetch_client.calls),
                2,
                "detail HTML must be promoted from discovery cache, not re-fetched",
            )
            self.assertEqual(verify_raw(run_dir)["status"], "passed")

            fetch_run(plan_path, run_dir, client=fetch_client)
            self.assertEqual(
                len(fetch_client.calls),
                2,
                "verified successes must not be refreshed during resume",
            )
            for filename in (
                "discovery-observations.jsonl",
                "discovered-resources.jsonl",
                "fetch-attempts.jsonl",
                "raw-artifacts.jsonl",
                "resource-artifact-links.jsonl",
                "artifact-url-observations.jsonl",
                "issues.jsonl",
                "discovery-manifest.json",
                "raw-manifest.json",
            ):
                self.assertTrue((run_dir / filename).is_file(), filename)

            stage_dir = root / "structural"
            parsed = parse_verified_odt_run(
                run_dir,
                stage_dir,
                parse_run_id="11111111-1111-4111-8111-111111111111",
            )
            self.assertEqual(parsed["status"], "passed")
            self.assertEqual(parsed["counts"]["declared_odt_artifacts"], 2)
            self.assertEqual(parsed["counts"]["parsed_odt_artifacts"], 2)
            self.assertEqual(parsed["counts"]["structural_blocks"], 2)
            self.assertEqual(parsed["counts"]["occurrence_candidates"], 2)
            occurrences = [
                json.loads(line)
                for line in (stage_dir / "occurrence-candidates.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            self.assertTrue(
                all(
                    row["statement"].startswith(
                        "Source-local structural observation only"
                    )
                    for row in occurrences
                )
            )

            structural = _verified_structural_manifest(stage_dir)
            raw_verification = verify_raw(run_dir)
            eligibility_path = root / "eligibility.json"
            write_json(
                eligibility_path,
                {
                    "schema": "nhi-rule-history/release-eligibility-receipt/v1",
                    "status": "reviewed_bounded_evidence_only",
                    "source_plan_sha256": SourcePlan.load(plan_path).sha256,
                    "capture_window": {
                        "started_at": "2021-01-01T00:00:00+08:00",
                        "ended_at": "2021-01-01T00:01:00+08:00",
                        "declared_timezone": "Asia/Taipei",
                    },
                    "corrected_acquisition": {
                        "run_id": "11111111-1111-4111-8111-111111111111",
                        "raw_manifest_sha256": file_sha256(
                            run_dir / "raw-manifest.json"
                        ),
                        "sealed_fingerprint": "1" * 64,
                        "release_eligible": True,
                        "default_selector_eligible": True,
                    },
                    "corrected_structural": {
                        "parse_run_id": structural["parse_run_id"],
                        "input_fingerprint": structural["input_fingerprint"],
                        "sealed_fingerprint": "2" * 64,
                    },
                    "superseded_runs": [
                        {
                            "run_id": "22222222-2222-4222-8222-222222222222",
                            "release_eligible": False,
                            "default_selector_eligible": False,
                            "superseded_by": (
                                "11111111-1111-4111-8111-111111111111"
                            ),
                        }
                    ],
                    "resource_identity_receipt": {
                        "detail_rows": 2,
                        "distinct_detail_resource_keys": 2,
                        "distinct_normalized_formal_document_numbers": 2,
                        "detail_normalization_collisions": 0,
                        "attachment_rows": 2,
                        "distinct_attachment_resource_keys": 2,
                        "distinct_canonical_attachment_urls": 2,
                        "ambiguous_attachment_identity_collisions": 0,
                    },
                    "legal_history_claim": False,
                    "portable_dataset_release_ready": False,
                },
            )
            plan, raw_sha, eligibility = _verified_release_binding(
                run_dir=run_dir,
                source_plan=plan_path,
                eligibility_receipt=eligibility_path,
                structural=structural,
                raw_verification=raw_verification,
            )
            self.assertEqual(plan.capture_cut.isoformat(), "2021-01-01")
            self.assertEqual(raw_sha, structural["raw_manifest_sha256"])
            self.assertFalse(eligibility["portable_dataset_release_ready"])

            wrong_plan_path = root / "wrong-plan.json"
            wrong_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            wrong_plan["adapters"][0]["queries"][0]["keywords"] = ["不同查詢"]
            wrong_plan_path.write_text(
                json.dumps(wrong_plan, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(PrepareError):
                _verified_release_binding(
                    run_dir=run_dir,
                    source_plan=wrong_plan_path,
                    eligibility_receipt=eligibility_path,
                    structural=structural,
                    raw_verification=raw_verification,
                )

            wrong_structural = dict(structural)
            wrong_structural["raw_manifest_sha256"] = "0" * 64
            with self.assertRaises(PrepareError):
                _verified_release_binding(
                    run_dir=run_dir,
                    source_plan=plan_path,
                    eligibility_receipt=eligibility_path,
                    structural=wrong_structural,
                    raw_verification=raw_verification,
                )

            bad_exclusion_path = root / "bad-exclusion.json"
            bad_exclusion = json.loads(
                eligibility_path.read_text(encoding="utf-8")
            )
            bad_exclusion["superseded_runs"][0]["release_eligible"] = True
            write_json(bad_exclusion_path, bad_exclusion)
            with self.assertRaises(PrepareError):
                _verified_release_binding(
                    run_dir=run_dir,
                    source_plan=plan_path,
                    eligibility_receipt=bad_exclusion_path,
                    structural=structural,
                    raw_verification=raw_verification,
                )

            bad_collision_path = root / "bad-collision.json"
            bad_collision = json.loads(
                eligibility_path.read_text(encoding="utf-8")
            )
            bad_collision["resource_identity_receipt"][
                "detail_normalization_collisions"
            ] = 1
            write_json(bad_collision_path, bad_collision)
            with self.assertRaises(PrepareError):
                _verified_release_binding(
                    run_dir=run_dir,
                    source_plan=plan_path,
                    eligibility_receipt=bad_collision_path,
                    structural=structural,
                    raw_verification=raw_verification,
                )

    def test_explicit_refresh_records_same_url_different_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            run_dir = root / "run"
            write_plan(plan_path)
            discover_run(plan_path, run_dir, client=FakeDiscoveryClient())
            fetch_run(plan_path, run_dir, client=FakeFetchClient(b"v1"))
            fetch_run(
                plan_path,
                run_dir,
                client=FakeFetchClient(b"v2"),
                refresh_successes=True,
            )
            result = verify_raw(run_dir)
            self.assertEqual(result["same_url_different_bytes"], 4)


if __name__ == "__main__":
    unittest.main()
