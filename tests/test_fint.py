from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from nhi_rule_history.discovery.fint import (
    MohwFintAdapter,
    date_partitions,
    parse_fint,
)


HTML = """<!doctype html>
<html><body>
<a id="hlLast" href="/FINT/FINTQRY04.aspx?kw=x&amp;RowNo=2">最末筆</a>
<table id="dat04">
<tr><td><pre>發文字號：測試字第 1 號
主旨：修訂藥品給付規定。</pre></td></tr>
<tr><td><a href="../Flaw/GetFile.ashx?PFID=42">附件修訂對照表.ODT</a></td></tr>
</table>
</body></html>"""


class FintTests(unittest.TestCase):
    def test_deterministic_date_partitions(self) -> None:
        self.assertEqual(
            list(date_partitions(date(2021, 1, 1), date(2022, 2, 4), 12)),
            [
                (date(2021, 1, 1), date(2021, 12, 31)),
                (date(2022, 1, 1), date(2022, 2, 4)),
            ],
        )

    def test_parser_extracts_last_row_record_and_attachment(self) -> None:
        parsed = parse_fint(HTML.encode("utf-8"), "text/html; charset=utf-8")
        self.assertTrue(parsed.has_record_table)
        self.assertEqual(parsed.last_row_number(), 2)
        self.assertIn("測試字第 1 號", parsed.record_excerpt)
        attachments = parsed.attachment_anchors()
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].text, "附件修訂對照表.ODT")

    def test_resource_identity_excludes_query_locators_and_separates_sources(
        self,
    ) -> None:
        adapter = MohwFintAdapter()
        context = SimpleNamespace(
            adapter={"id": "fint-test"},
            client=SimpleNamespace(
                allowed_hosts=("mohwlaw.mohw.gov.tw",)
            ),
        )

        def resources(document_number: str, pfid: str, row_number: int) -> list[dict]:
            html = f"""<!doctype html><html><body>
<table id="dat04"><tr><td><pre>
發文字號：{document_number}
發文日期：民國 110 年 1 月 1 日
主旨：修訂藥品給付規定。
</pre></td></tr><tr><td>
<a href="../Flaw/GetFile.ashx?PFID={pfid}">附件.ODT</a>
</td></tr></table></body></html>"""
            return adapter._record_resources(
                context,
                query_id="q",
                partition_id="2021",
                row_number=row_number,
                request_url=(
                    "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx"
                    f"?RowNo={row_number}"
                ),
                parser=parse_fint(html.encode("utf-8"), "text/html; charset=utf-8"),
            )

        first = resources("測試字第 1 號", "42", 1)
        reordered = resources("測試字第 1 號", "42", 999)
        distinct = resources("測試字第 2 號", "43", 1)
        self.assertEqual(
            [row["resource_id"] for row in first],
            [row["resource_id"] for row in reordered],
        )
        self.assertNotEqual(first[0]["resource_id"], distinct[0]["resource_id"])
        self.assertNotEqual(first[1]["resource_id"], distinct[1]["resource_id"])


if __name__ == "__main__":
    unittest.main()
