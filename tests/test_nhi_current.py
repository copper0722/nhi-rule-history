from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nhi_rule_history.contracts import ContractError, PLAN_SCHEMA, iter_jsonl
from nhi_rule_history.discovery.compare import compare_discovery_runs
from nhi_rule_history.discovery.nhi_current import (
    NhiCurrentChaptersAdapter,
    NhiCurrentWholeAdapter,
    parse_nhi_current,
)
from nhi_rule_history.discovery.runner import discover_run
from nhi_rule_history.fetch.http import HttpResponse
from nhi_rule_history.parsers.odt import _odt_candidate


FIXTURE_DIR = Path(__file__).parent / "fixtures"
WHOLE_URL = "https://www.nhi.gov.tw/ch/cp-whole.html"
CHAPTER_URL = "https://www.nhi.gov.tw/ch/cp-chapters.html"


def fixture(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


class FakeNhiClient:
    allowed_hosts = ("www.nhi.gov.tw",)

    def __init__(
        self,
        *,
        whole: bytes | None = None,
        chapters: bytes | None = None,
    ) -> None:
        self.whole = whole or fixture("nhi_current_whole.html")
        self.chapters = chapters or fixture("nhi_current_chapters.html")
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        if url == WHOLE_URL:
            body = self.whole
        elif url == CHAPTER_URL:
            body = self.chapters
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return HttpResponse(
            request_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=body,
        )


class MemoryRecorder:
    def __init__(self, payload: bytes, allowed_hosts: tuple[str, ...]) -> None:
        self.payload = payload
        self.resources: list[dict] = []
        self.allowed_hosts = allowed_hosts

    def observe(self, **_: object) -> dict:
        return {
            "payload": self.payload,
            "headers": {"content-type": "text/html; charset=utf-8"},
        }

    def record_resource(self, row: dict) -> None:
        self.resources.append(row)


def context_for(
    adapter: object,
    payload: bytes,
    *,
    adapter_id: str = "nhi-current-test",
    base_url: str = CHAPTER_URL,
    allowed_hosts: tuple[str, ...] = ("www.nhi.gov.tw",),
) -> SimpleNamespace:
    recorder = MemoryRecorder(payload, allowed_hosts)
    return SimpleNamespace(
        adapter={"id": adapter_id, "base_url": base_url},
        client=SimpleNamespace(allowed_hosts=allowed_hosts),
        recorder=recorder,
    )


def write_plan(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": PLAN_SCHEMA,
                "capture_cut": "2026-07-27",
                "allowed_hosts": ["www.nhi.gov.tw"],
                "adapters": [
                    {
                        "id": "whole",
                        "kind": "nhi_current_whole",
                        "enabled": True,
                        "base_url": WHOLE_URL,
                    },
                    {
                        "id": "chapters",
                        "kind": "nhi_chapters",
                        "enabled": True,
                        "base_url": CHAPTER_URL,
                    },
                ],
                "excluded_semantics": [
                    "legal_effective_date",
                    "stable_rule_identity",
                    "rule_lineage",
                    "predecessor_diff",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class NhiCurrentTests(unittest.TestCase):
    def test_current_attachment_declares_odt_without_overloading_group_label(
        self,
    ) -> None:
        row = {
            "resource_kind": "official_current_chapter_attachment",
            "source_label": "通則(113.05.28更新)",
            "source_url": "https://www.nhi.gov.tw/ch/dl-42495-general-1.odt",
            "discovery_locator": {
                "source_designation_raw": "通則(113.05.28更新)",
                "attachment_title": "通則.odt",
                "attachment_visible_label": "odt",
            },
        }
        self.assertTrue(_odt_candidate([row]))
        row["resource_kind"] = "official_current_chapter_page"
        self.assertFalse(_odt_candidate([row]))

    def test_whole_page_yields_every_declared_asset_in_source_order(self) -> None:
        context = context_for(
            NhiCurrentWholeAdapter(),
            fixture("nhi_current_whole.html"),
            adapter_id="whole",
            base_url=WHOLE_URL,
        )
        result = NhiCurrentWholeAdapter().discover(context)
        self.assertEqual(result["declared_groups"], 1)
        self.assertEqual(result["declared_assets"], 3)
        self.assertEqual(len(context.recorder.resources), 4)
        attachments = context.recorder.resources[1:]
        self.assertEqual(
            [row["source_url"] for row in attachments],
            [
                "https://www.nhi.gov.tw/ch/dl-100715-whole-1.docx",
                "https://www.nhi.gov.tw/ch/dl-100717-whole-1.pdf",
                "https://www.nhi.gov.tw/ch/dl-100716-whole-1.odt",
            ],
        )
        self.assertEqual(
            [row["discovery_locator"]["attachment_ordinal"] for row in attachments],
            [1, 2, 3],
        )
        self.assertTrue(
            all(
                row["source_label"]
                == "最新版藥品給付規定內容(整份帶走)-115.7.23更新"
                for row in attachments
            )
        )
        self.assertTrue(
            all("legal_effective_date" not in row for row in attachments)
        )

    def test_chapter_page_preserves_designation_and_pfid_identity(self) -> None:
        context = context_for(
            NhiCurrentChaptersAdapter(),
            fixture("nhi_current_chapters.html"),
            adapter_id="chapters",
        )
        result = NhiCurrentChaptersAdapter().discover(context)
        self.assertEqual(result["declared_groups"], 2)
        self.assertEqual(result["declared_assets"], 4)
        attachments = context.recorder.resources[1:]
        self.assertEqual(
            [row["source_label"] for row in attachments],
            [
                "通則(113.05.28更新)",
                "通則(113.05.28更新)",
                "第一節 神經系統藥物(115.6.23更新)",
                "第一節 神經系統藥物(115.6.23更新)",
            ],
        )
        self.assertEqual(
            attachments[2]["discovery_locator"]["stable_attachment_identity"],
            "pfid:www.nhi.gov.tw:50595",
        )
        self.assertEqual(
            attachments[3]["source_url"],
            "https://www.nhi.gov.tw/ch/download?PFID=50594&format=pdf",
        )

    def test_runner_maps_both_adapters_and_two_passes_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "plan.json"
            write_plan(plan)
            run_a = root / "a"
            run_b = root / "b"
            manifest_a = discover_run(plan, run_a, client=FakeNhiClient())
            manifest_b = discover_run(plan, run_b, client=FakeNhiClient())
            self.assertEqual(manifest_a["parity"], {"expected_rows": 7, "fetched_rows": 7})
            self.assertEqual(manifest_b["counts"]["resources"], 9)
            self.assertEqual(
                len(list(iter_jsonl(run_a / "discovered-resources.jsonl"))),
                9,
            )
            parity = compare_discovery_runs(run_a, run_b)
            self.assertEqual(parity["status"], "passed")
            self.assertEqual(parity["resource_count"], 9)

    def test_identity_is_independent_of_group_and_attachment_order(self) -> None:
        payload_a = fixture("nhi_current_chapters.html")
        payload_b = """<!doctype html><html><head><title>x</title></head><body>
        <section class="fileDownload"><ul>
          <li><span class="fileName">第一節 神經系統藥物(115.6.23更新)</span>
            <ol class="downloadFiles">
              <li><a href="/ch/download?PFID=50594&amp;format=pdf" title="pdf">pdf</a></li>
              <li><a href="/ch/download?format=odt&amp;PFID=50595" title="odt">odt</a></li>
            </ol></li>
          <li><span class="fileName">通則(113.05.28更新)</span>
            <ol class="downloadFiles">
              <li><a href="/ch/dl-42496-general-1.pdf" title="pdf">pdf</a></li>
              <li><a href="/ch/dl-42495-general-1.odt" title="odt">odt</a></li>
            </ol></li>
        </ul></section></body></html>""".encode("utf-8")
        contexts = [
            context_for(
                NhiCurrentChaptersAdapter(),
                payload,
                adapter_id="chapters",
            )
            for payload in (payload_a, payload_b)
        ]
        for context in contexts:
            NhiCurrentChaptersAdapter().discover(context)
        ids = [
            {row["resource_id"] for row in context.recorder.resources[1:]}
            for context in contexts
        ]
        self.assertEqual(ids[0], ids[1])

    def test_fails_closed_on_zero_assets(self) -> None:
        context = context_for(
            NhiCurrentWholeAdapter(),
            b"<html><head><title>x</title></head><body></body></html>",
            adapter_id="whole",
            base_url=WHOLE_URL,
        )
        with self.assertRaisesRegex(ContractError, "fileDownload"):
            NhiCurrentWholeAdapter().discover(context)
        self.assertEqual(context.recorder.resources, [])

    def test_fails_closed_on_missing_chapter_designation(self) -> None:
        payload = """<html><head><title>x</title></head><body>
        <section class="fileDownload"><ul><li>
          <ol class="downloadFiles">
            <li><a href="/ch/dl-1-x-1.odt">odt</a></li>
          </ol>
        </li></ul></section></body></html>""".encode("utf-8")
        context = context_for(NhiCurrentChaptersAdapter(), payload)
        with self.assertRaisesRegex(ContractError, "designation"):
            NhiCurrentChaptersAdapter().discover(context)
        self.assertEqual(context.recorder.resources, [])

    def test_fails_closed_on_duplicate_stable_identity(self) -> None:
        payload = """<html><head><title>x</title></head><body>
        <section class="fileDownload"><ul><li>
          <span class="fileName">通則</span><ol class="downloadFiles">
            <li><a href="/ch/download?PFID=42&amp;format=odt">odt</a></li>
            <li><a href="/ch/other?format=pdf&amp;PFID=42">pdf</a></li>
          </ol>
        </li></ul></section></body></html>""".encode("utf-8")
        context = context_for(NhiCurrentChaptersAdapter(), payload)
        with self.assertRaisesRegex(ContractError, "duplicate"):
            NhiCurrentChaptersAdapter().discover(context)
        self.assertEqual(context.recorder.resources, [])

    def test_fails_closed_on_non_official_attachment_host(self) -> None:
        payload = """<html><head><title>x</title></head><body>
        <section class="fileDownload"><ul><li>
          <span class="fileName">通則</span><ol class="downloadFiles">
            <li><a href="https://files.example/anchor.odt">odt</a></li>
          </ol>
        </li></ul></section></body></html>""".encode("utf-8")
        context = context_for(
            NhiCurrentChaptersAdapter(),
            payload,
            allowed_hosts=("www.nhi.gov.tw", "files.example"),
        )
        with self.assertRaisesRegex(ContractError, "non-official"):
            NhiCurrentChaptersAdapter().discover(context)
        self.assertEqual(context.recorder.resources, [])

    def test_parser_ignores_unrelated_download_like_links(self) -> None:
        payload = """<html><head><title>x</title></head><body>
        <a href="/ch/dl-unrelated.pdf">unrelated</a>
        <section class="fileDownload"><ul><li>
          <span class="fileName">通則</span><ol class="downloadFiles">
            <li><a href="/ch/dl-anchor.odt">odt</a></li>
          </ol>
        </li></ul></section></body></html>""".encode("utf-8")
        parser = parse_nhi_current(payload, "text/html; charset=utf-8")
        self.assertEqual(len(parser.groups), 1)
        self.assertEqual(
            [item.href for item in parser.groups[0].attachments],
            ["/ch/dl-anchor.odt"],
        )


if __name__ == "__main__":
    unittest.main()
