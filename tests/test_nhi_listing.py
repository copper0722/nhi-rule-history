from __future__ import annotations

import unittest
from types import SimpleNamespace

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.discovery.nhi_listing import (
    NhiListingAdapter,
    parse_nhi_listing,
)
from nhi_rule_history.update.rss import parse_attachment_links


HEADERS = (
    "編號",
    "主旨",
    "發文字號",
    "發文日期",
    "刊登日期",
    "刊登期限",
)


def _row(ordinal: int) -> str:
    return f"""
      <tr>
        <td>{ordinal}</td>
        <td class="title"><a href="/ch/cp-{20000 + ordinal}-a{ordinal}b-3258-1.html">公告 {ordinal}</a></td>
        <td>健保審字第115000{ordinal:04d}號</td>
        <td>115-07-24</td>
        <td>115-07-25</td>
        <td>118-07-25</td>
      </tr>
    """


def _page(page: int, ordinals: range) -> bytes:
    next_link = (
        '<a href="lp-3258-1.html?pi=2&amp;ps=20">下一頁</a>'
        if page == 1
        else ""
    )
    return f"""
      <html><body>
      <section class="list">
        <table class="rwdTable">
          <thead><tr>{''.join(f'<th>{value}</th>' for value in HEADERS)}</tr></thead>
          <tbody>{''.join(_row(value) for value in ordinals)}</tbody>
        </table>
      </section>
      <section class="pagination">
        <form>
          <div class="total">共21筆資料，第{page}/2頁，每頁顯示
            <a href="lp-3258-1.html?pi=1&amp;ps=20">20</a>
            <a href="lp-3258-1.html?pi=1&amp;ps=40">40</a>
          </div>
        </form>
        <ul class="page">
          <li><a href="lp-3258-1.html?pi=1&amp;ps=20">1</a></li>
          <li><a href="lp-3258-1.html?pi=2&amp;ps=20">2</a></li>
          <li>{next_link}</li>
        </ul>
      </section>
      </body></html>
    """.encode()


class _Recorder:
    def __init__(self) -> None:
        self.resources: list[dict] = []
        self.pages = {
            "https://www.nhi.gov.tw/ch/lp-3258-1.html": _page(
                1, range(1, 21)
            ),
            (
                "https://www.nhi.gov.tw/ch/"
                "lp-3258-1.html?pi=2&ps=20"
            ): _page(2, range(21, 22)),
        }

    def observe(self, **kwargs):
        return {
            "payload": self.pages[kwargs["request_url"]],
            "headers": {"content-type": "text/html; charset=utf-8"},
        }

    def record_resource(self, row):
        self.resources.append(row)


def _context(recorder: _Recorder | None = None):
    return SimpleNamespace(
        adapter={
            "id": "nhi-listing",
            "base_url": "https://www.nhi.gov.tw/ch/lp-3258-1.html",
            "max_pages": 10,
        },
        client=SimpleNamespace(allowed_hosts={"www.nhi.gov.tw"}),
        recorder=recorder,
    )


class NhiListingTest(unittest.TestCase):
    def test_listing_expansion_can_preserve_zero_attachment_detail(self) -> None:
        detail_url = (
            "https://www.nhi.gov.tw/ch/cp-20001-a1b-3258-1.html"
        )
        payload = b"<html><body><p>no download</p></body></html>"
        self.assertEqual(
            parse_attachment_links(
                detail_url,
                payload,
                require_nonempty=False,
            ),
            [],
        )
        with self.assertRaises(ContractError):
            parse_attachment_links(detail_url, payload)

    def test_parser_matches_live_section_table_contract(self) -> None:
        parser = parse_nhi_listing(_page(1, range(1, 21)))
        self.assertEqual(parser.listing_sections, 1)
        self.assertEqual(parser.pagination_sections, 1)
        self.assertEqual(parser.headers, list(HEADERS))
        self.assertEqual(len(parser.rows), 20)
        self.assertEqual(parser.declared_total, 21)
        self.assertEqual(parser.current_page, 1)
        self.assertEqual(parser.declared_pages, 2)
        self.assertEqual(
            [anchor.text for anchor in parser.pagination_anchors],
            ["1", "2", "下一頁"],
        )
        self.assertEqual(
            parser.rows[0].cells[1].anchors[0].text,
            "公告 1",
        )
        self.assertEqual(parser.structural_errors, [])

    def test_listing_url_requires_exact_pi_ps_contract(self) -> None:
        adapter = NhiListingAdapter()
        context = _context()
        base = "https://www.nhi.gov.tw/ch/lp-3258-1.html"
        self.assertEqual(
            adapter._validate_listing_url(
                context,
                "lp-3258-1.html?pi=2&ps=20",
                page_url=base,
                expected_page=2,
            ),
            "https://www.nhi.gov.tw/ch/lp-3258-1.html?pi=2&ps=20",
        )
        for value in (
            "lp-3258-2.html",
            "lp-3258-1.html?pi=2&ps=40",
            "lp-3258-1.html?pi=1&ps=20",
            "lp-3258-1.html?pi=2&ps=20&pi=2",
        ):
            with self.subTest(value=value), self.assertRaises(ContractError):
                adapter._validate_listing_url(
                    context,
                    value,
                    page_url=base,
                    expected_page=2,
                )

    def test_two_page_discovery_is_exhaustive_and_unfiltered(self) -> None:
        recorder = _Recorder()
        result = NhiListingAdapter().discover(_context(recorder))
        self.assertEqual(result["declared_pages"], 2)
        self.assertEqual(result["declared_rows"], 21)
        self.assertEqual(result["recorded_detail_resources"], 21)
        self.assertEqual(len(recorder.resources), 21)
        locator = recorder.resources[20]["discovery_locator"]
        self.assertEqual(locator["displayed_ordinal"], 21)
        self.assertEqual(
            locator["document_number_raw"],
            "健保審字第1150000021號",
        )
        self.assertEqual(locator["document_date_raw"], "115-07-24")
        self.assertEqual(locator["listing_date_raw"], "115-07-25")
        self.assertEqual(locator["expiry_date_raw"], "118-07-25")
        self.assertEqual(
            locator["listing_occurrences"],
            [
                {
                    "page_number": 2,
                    "row_ordinal": 1,
                    "listing_page_url": (
                        "https://www.nhi.gov.tw/ch/"
                        "lp-3258-1.html?pi=2&ps=20"
                    ),
                }
            ],
        )

    def test_header_or_date_drift_fails_closed(self) -> None:
        bad_header = _page(1, range(1, 21)).replace(
            "刊登日期".encode(),
            "更新日期".encode(),
        )
        parser = parse_nhi_listing(bad_header)
        with self.assertRaises(ContractError):
            NhiListingAdapter()._validate_parser(
                parser,
                expected_page=1,
            )

        parser = parse_nhi_listing(
            _page(1, range(1, 21)).replace(
                "115-07-25".encode(),
                "即日起".encode(),
                1,
            )
        )
        NhiListingAdapter()._validate_parser(parser, expected_page=1)
        with self.assertRaises(ContractError):
            NhiListingAdapter()._row(
                _context(),
                parser.rows[0],
                page_number=1,
                row_ordinal=1,
                page_url=(
                    "https://www.nhi.gov.tw/ch/lp-3258-1.html"
                ),
            )


if __name__ == "__main__":
    unittest.main()
