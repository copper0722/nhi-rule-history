from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from nhi_rule_history.discovery.fint_keyword_crawler import (
    FintKeywordCrawler,
    Seed,
    build_detail_url,
    build_search_url,
    detect_media_type,
    parse_fint_search,
)
from nhi_rule_history.contracts import ContractError
from nhi_rule_history.fetch.http import HttpResponse


SEARCH_HTML = """<!doctype html><html><body>
<div class="total-page">共 2 筆 / 每頁 10 筆 / 共 1 頁</div>
<table id="dat02">
<tr><td><a href="FINTQRY04.aspx?kw=CAPD&RowNo=1">doc 1</a></td></tr>
<tr><td><a href="FINTQRY04.aspx?kw=CAPD&RowNo=2">doc 2</a></td></tr>
</table></body></html>""".encode()


def detail_html(number: str, pfid: str | None = None) -> bytes:
    attachment = (
        f'<a href="../Flaw/GetFile.ashx?PFID={pfid}">附件.odt</a>'
        if pfid
        else ""
    )
    return f"""<!doctype html><html><body>
<table id="dat04"><tr><td><pre>
發文字號：{number}
發文日期：民國 90 年 12 月 28 日
主旨：公告全民健康保險藥品給付規定 CAPD。
</pre>{attachment}</td></tr></table>
</body></html>""".encode()


class FakeClient:
    allowed_hosts = ("mohwlaw.mohw.gov.tw",)

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        if parsed.path.endswith("FINTQRY03.aspx"):
            body = SEARCH_HTML
            content_type = "text/html; charset=utf-8"
        elif parsed.path.endswith("FINTQRY04.aspx"):
            row = int(params["RowNo"][0])
            body = detail_html(
                f"健保醫 字第 09000162{58 + row} 號",
                "42" if row == 1 else None,
            )
            content_type = "text/html; charset=utf-8"
        elif parsed.path.endswith("GetFile.ashx"):
            body = b"PK fixture odt"
            content_type = "application/vnd.oasis.opendocument.text"
        else:
            raise AssertionError(url)
        return HttpResponse(
            request_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": content_type},
            body=body,
        )

    def close(self) -> None:
        return None


class FintKeywordCrawlerTests(unittest.TestCase):
    def test_search_parser_requires_result_table_and_reads_count(self) -> None:
        parser = parse_fint_search(
            SEARCH_HTML,
            "text/html; charset=utf-8",
        )
        self.assertEqual(parser.result_count(), 2)
        parser.validate_page(page=1, result_count=2)
        empty = parse_fint_search(
            b"<table id='dat02'></table>",
            "text/html; charset=utf-8",
        )
        self.assertEqual(empty.result_count(), 0)
        empty.validate_page(page=1, result_count=0)

    def test_search_parser_rejects_noncontiguous_row_links(self) -> None:
        broken = SEARCH_HTML.replace(b"RowNo=1", b"RowNo=3")
        parser = parse_fint_search(
            broken,
            "text/html; charset=utf-8",
        )
        with self.assertRaises(ContractError):
            parser.validate_page(page=1, result_count=2)

    def test_urls_preserve_full_range_and_keyword(self) -> None:
        search = parse_qs(urlsplit(build_search_url("CAPD")).query)
        detail = parse_qs(urlsplit(build_detail_url("CAPD", 6)).query)
        self.assertEqual(search["starDate"], ["00000000"])
        self.assertEqual(search["endDate"], ["99991231"])
        self.assertEqual(search["kw"], ["CAPD"])
        self.assertEqual(detail["RowNo"], ["6"])

    def test_magic_bytes_override_wrong_attachment_header(self) -> None:
        self.assertEqual(
            detect_media_type(
                bytes.fromhex("d0cf11e0a1b11ae1") + b"fixture",
                "text/html",
            ),
            "application/x-ole-storage",
        )

    def test_crawler_archives_search_details_attachments_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            client = FakeClient()
            crawler = FintKeywordCrawler(
                run_dir,
                client=client,
                min_interval_seconds=0,
            )
            seed = Seed("CAPD", "manual_clause_term", "general-rule/0.4")
            first = crawler.crawl([seed])
            self.assertEqual(first["status"], "complete_declared_keyword_set")
            self.assertEqual(first["counts"]["queries"], 1)
            self.assertEqual(first["counts"]["documents"], 2)
            self.assertEqual(first["counts"]["matches"], 2)
            self.assertEqual(first["counts"]["snapshots"], 2)
            self.assertEqual(first["counts"]["attachments"], 1)
            first_call_count = len(client.calls)

            crawler.crawl([seed])
            self.assertEqual(len(client.calls), first_call_count)
            crawler.close()

            documents = [
                json.loads(line)
                for line in (
                    run_dir / "fint-documents.jsonl"
                ).read_text().splitlines()
            ]
            self.assertEqual(
                documents[0]["normalized_document_number"],
                "健保醫字第0900016259號",
            )

    def test_same_document_from_two_keywords_is_one_identity_two_matches(self) -> None:
        class DuplicateClient(FakeClient):
            def get(self, url: str) -> HttpResponse:
                parsed = urlsplit(url)
                if parsed.path.endswith("FINTQRY03.aspx"):
                    body = SEARCH_HTML.replace(b"2 \xe7\xad\x86", b"1 \xe7\xad\x86")
                    body = body.replace(
                        b'<tr><td><a href="FINTQRY04.aspx?kw=CAPD&RowNo=2">doc 2</a></td></tr>',
                        b"",
                    )
                    self.calls.append(url)
                    return HttpResponse(
                        request_url=url,
                        final_url=url,
                        status_code=200,
                        headers={"content-type": "text/html; charset=utf-8"},
                        body=body,
                    )
                if parsed.path.endswith("FINTQRY04.aspx"):
                    self.calls.append(url)
                    return HttpResponse(
                        request_url=url,
                        final_url=url,
                        status_code=200,
                        headers={"content-type": "text/html; charset=utf-8"},
                        body=detail_html("健保醫 字第 0900016259 號"),
                    )
                return super().get(url)

        with tempfile.TemporaryDirectory() as temporary:
            crawler = FintKeywordCrawler(
                Path(temporary),
                client=DuplicateClient(),
                min_interval_seconds=0,
            )
            manifest = crawler.crawl(
                [
                    Seed("CAPD", "manual", "0.4"),
                    Seed("透析液", "manual", "0.4"),
                ]
            )
            self.assertEqual(manifest["counts"]["documents"], 1)
            self.assertEqual(manifest["counts"]["matches"], 2)
            self.assertEqual(manifest["counts"]["snapshots"], 2)
            crawler.close()

    def test_unfiltered_seed_and_empty_attachment_label_are_preserved(
        self,
    ) -> None:
        class EmptyLabelClient(FakeClient):
            def get(self, url: str) -> HttpResponse:
                parsed = urlsplit(url)
                if parsed.path.endswith("FINTQRY04.aspx"):
                    self.calls.append(url)
                    return HttpResponse(
                        request_url=url,
                        final_url=url,
                        status_code=200,
                        headers={"content-type": "text/html; charset=utf-8"},
                        body=detail_html(
                            "健保醫 字第 0900016259 號",
                            "42",
                        ).replace(
                            ">附件.odt</a>".encode(),
                            "></a>".encode(),
                        ),
                    )
                return super().get(url)

        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            crawler = FintKeywordCrawler(
                run_dir,
                client=EmptyLabelClient(),
                min_interval_seconds=0,
                attachment_policy="all",
            )
            manifest = crawler.crawl(
                [Seed(None, "unfiltered", "20000101__20001231")]
            )
            crawler.close()
            self.assertEqual(manifest["counts"]["attachment_declarations"], 2)
            declarations = [
                json.loads(line)
                for line in (
                    run_dir / "fint-attachment-declarations.jsonl"
                ).read_text().splitlines()
            ]
            self.assertTrue(all(row["source_label_missing"] for row in declarations))
            query = json.loads(
                (run_dir / "fint-queries.jsonl").read_text().splitlines()[0]
            )
            self.assertEqual(query["keywords"], [])

    def test_unfiltered_partition_locator_must_match_query_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            crawler = FintKeywordCrawler(
                Path(temporary),
                client=FakeClient(),
                min_interval_seconds=0,
                start_date="20260101",
                end_date="20261231",
            )
            with self.assertRaises(ContractError):
                crawler.crawl(
                    [
                        Seed(
                            None,
                            "fint_unfiltered_year_partition",
                            "19540101__19541231",
                        )
                    ]
                )
            crawler.close()

    def test_result_set_drift_before_completion_fails_closed(self) -> None:
        class DriftClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.search_calls = 0

            def get(self, url: str) -> HttpResponse:
                parsed = urlsplit(url)
                if parsed.path.endswith("FINTQRY03.aspx"):
                    self.search_calls += 1
                    self.calls.append(url)
                    body = SEARCH_HTML
                    if self.search_calls > 1:
                        body = body.replace(b"doc 1", b"changed doc 1")
                    return HttpResponse(
                        request_url=url,
                        final_url=url,
                        status_code=200,
                        headers={"content-type": "text/html; charset=utf-8"},
                        body=body,
                    )
                return super().get(url)

        with tempfile.TemporaryDirectory() as temporary:
            crawler = FintKeywordCrawler(
                Path(temporary),
                client=DriftClient(),
                min_interval_seconds=0,
            )
            with self.assertRaises(ContractError):
                crawler.crawl([Seed("CAPD", "fixture", "0.4")])
            crawler.close()


if __name__ == "__main__":
    unittest.main()
