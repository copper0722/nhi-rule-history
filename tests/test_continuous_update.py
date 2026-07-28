from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import nhi_rule_history.update.workers as worker_contract
from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.update.bundle import BundleBuilder, verify_bundle
from nhi_rule_history.update.corpus_bundle import (
    _expected_replay_raw,
    prepare_corpus_bundle,
)
from nhi_rule_history.update.notice import (
    extract_notice_metadata,
    extract_notice_metadata_v12,
    normalize_reference_number,
)
from nhi_rule_history.update.proposal import (
    PROPOSAL_SCHEMA,
    ProposalError,
    validate_proposal,
)
from nhi_rule_history.update.poll import observe_feed, verify_poll
from nhi_rule_history.update.rss import (
    OfficialResponse,
    RssItem,
    filter_new_items,
    parse_attachment_links,
    parse_rss,
)
from nhi_rule_history.update.workers import (
    WORKER_PROMPT_VERSION,
    WorkerOrchestrator,
    WorkerSpec,
    build_worker_prompt,
    source_packet,
    worker_job_fingerprint,
)


def fixture_odt(new_text: str = "9.4 新條文完整文字") -> bytes:
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
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <text:p>(自115年8月1日生效)</text:p>
  <table:table>
   <table:table-row>
    <table:table-cell><text:p>建議修訂後給付規定</text:p></table:table-cell>
    <table:table-cell><text:p>原給付規定</text:p></table:table-cell>
   </table:table-row>
   <table:table-row>
    <table:table-cell><text:p>{new_text}</text:p></table:table-cell>
    <table:table-cell><text:p>9.4 舊條文完整文字</text:p></table:table-cell>
   </table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>""",
        )
    return buffer.getvalue()


def fixture_nested_table_odt() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "content.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <text:p>表格外生效資訊</text:p>
  <table:table>
   <table:table-row>
    <table:table-cell>
     <text:p>外層儲存格文字</text:p>
     <text:list><text:list-item><text:p>外層清單文字</text:p></text:list-item></text:list>
     <table:table>
      <table:table-row>
       <table:table-cell><text:p>內層儲存格文字</text:p></table:table-cell>
      </table:table-row>
     </table:table>
     <text:p>內層表格之後的外層文字</text:p>
    </table:table-cell>
   </table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>""",
        )
    return buffer.getvalue()


def fixture_structural_table_odt() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "content.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <table:table>
   <table:table-header-rows>
    <table:table-row>
     <table:table-cell table:number-columns-spanned="2">
      <text:p>相同文字</text:p>
     </table:table-cell>
     <table:covered-table-cell><text:p>covered 來源文字</text:p></table:covered-table-cell>
    </table:table-row>
   </table:table-header-rows>
   <table:table-row table:number-rows-repeated="2">
    <table:table-cell table:number-columns-repeated="3">
     <text:p>相同文字</text:p>
    </table:table-cell>
    <table:table-cell table:number-rows-spanned="2">
     <text:p>row span 來源文字</text:p>
    </table:table-cell>
   </table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>""",
        )
    return buffer.getvalue()


def fixture_nested_duplicate_regression_odt(
    paragraph_count: int = 106,
) -> bytes:
    paragraphs = "".join(
        f"<text:p>內層段落 {index:03d}</text:p>"
        for index in range(paragraph_count)
    )
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
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <table:table><table:table-row><table:table-cell>
   <table:table><table:table-row><table:table-cell>
    {paragraphs}
   </table:table-cell></table:table-row></table:table>
  </table:table-cell></table:table-row></table:table>
 </office:text></office:body>
</office:document-content>""",
        )
    return buffer.getvalue()


def fixture_feed() -> bytes:
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>NHI</title>
<item><title>修訂「全民健康保險藥品給付規定」</title>
<link>https://www.nhi.gov.tw/ch/cp-test-3258-1.html</link>
<guid>notice-1</guid><description>藥品給付規定</description>
<pubDate>Mon, 20 Jul 2026 08:00:00 +0800</pubDate></item>
</channel></rss>""".encode("utf-8")


def response(
    url: str, body: bytes, content_type: str
) -> OfficialResponse:
    return OfficialResponse(
        request_url=url,
        final_url=url,
        status_code=200,
        headers={"content-type": content_type},
        body=body,
        observed_at="2026-07-27T00:00:00+00:00",
    )


def span(block: dict[str, object]) -> dict[str, object]:
    text = str(block["raw_text"])
    return {
        "artifact_sha256": block["artifact_sha256"],
        "block_id": block["block_id"],
        "start": 0,
        "end": len(text),
        "exact_text": text,
        "exact_text_sha256": sha256_bytes(text.encode("utf-8")),
    }


def valid_proposal(
    blocks: list[dict[str, object]],
    *,
    parity_unverified: bool,
    identity_uncertainty: bool,
) -> dict[str, object]:
    effective = next(row for row in blocks if "生效" in str(row["raw_text"]))
    new = next(row for row in blocks if "新條文" in str(row["raw_text"]))
    old = next(row for row in blocks if "舊條文" in str(row["raw_text"]))
    temporal_span = span(effective)
    return {
        "schema": PROPOSAL_SCHEMA,
        "temporal_evidence": [
            {
                "source_span": temporal_span,
                "expression_raw": "115年8月1日",
                "calendar": "ROC",
                "precision": "day",
                "semantic_role": "effective_from",
                "scope_raw": "本公告比較表",
                "conditionality": "unconditional",
                "iso_date_candidate": "2026-08-01",
            }
        ],
        "effect_candidates": [
            {
                "designation_raw": "9.4",
                "parent_chapter_raw": "第9章",
                "comparison_kind_hint": "full_replacement",
                "old_text_spans": [span(old)],
                "new_text_spans": [span(new)],
                "scope_count": 1,
                "comparison_row_count": 1,
                "review_flags": {
                    "omitted_text": False,
                    "merged_cells": False,
                    "cross_row_dependency": False,
                    "partial_patch": False,
                    "multi_rule": False,
                    "correction": False,
                    "same_url_different_bytes": False,
                    "odt_pdf_disagreement": False,
                    "identity_uncertainty": identity_uncertainty,
                },
            }
        ],
        "document_flags": {
            "correction_notice": False,
            "same_url_different_bytes": False,
            "odt_pdf_disagreement": False,
            "odt_pdf_parity_unverified": parity_unverified,
            "declared_attachment_coverage_uncertain": False,
        },
        "model_assessment": (
            "needs_review"
            if identity_uncertainty
            else "single_full_replacement_candidate"
        ),
        "reason_codes": (
            ["IDENTITY_REQUIRES_ANCHOR"]
            if identity_uncertainty
            else ["FULL_SINGLE_CLAUSE_SHAPE"]
        ),
    }


class ContinuousUpdateTests(unittest.TestCase):
    def _bundle(
        self,
        root: Path,
        *,
        reference_number: str = "健保審字第1150000000號",
        announcement_html: str = "修訂第9節9.4規定",
    ):
        item = parse_rss(fixture_feed())[0]
        detail_url = item.link
        detail = response(
            detail_url,
            (
                "<html><table>"
                "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
                f"<tr><th>發文字號</th><td>{reference_number}</td></tr>"
                f"<tr><th>公告事項</th><td>{announcement_html}</td></tr>"
                "<tr><th>發文日期</th><td>115-07-15</td></tr>"
                "</table><dl>"
                "<dt>發布日期</dt><dd>115-07-15</dd>"
                "<dt>更新日期</dt><dd>115-07-16</dd></dl>"
                '<a href="/ch/dl-test-1.odt">修訂對照表.ODT</a>'
                '<a href="/ch/dl-test-2.pdf">修訂對照表.PDF</a></html>'
            ).encode(),
            "text/html; charset=utf-8",
        )
        links = parse_attachment_links(detail_url, detail.body)
        attachments = [
            (
                links[0],
                response(
                    links[0].url,
                    fixture_odt(),
                    "application/vnd.oasis.opendocument.text",
                ),
            ),
            (
                links[1],
                response(
                    links[1].url,
                    b"%PDF-1.7\nfixture",
                    "application/pdf",
                ),
            ),
        ]
        return BundleBuilder(
            root,
            rss_item=item,
            feed_response=response(
                "https://www.nhi.gov.tw/ch/rss-3258-1.xml",
                fixture_feed(),
                "application/rss+xml",
            ),
            detail_response=detail,
            attachments=attachments,
        ).seal()

    def test_rss_exact_identity_filter_and_attachment_dedup(self) -> None:
        items = parse_rss(fixture_feed())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_likely_drug_rule)
        self.assertEqual(filter_new_items(items, []), items)
        self.assertEqual(filter_new_items(items, ["notice-1"]), [])
        detail = (
            '<a href="/ch/dl-a-1.odt">A</a>'
            '<a href="/ch/dl-a-1.odt">A duplicate</a>'
        ).encode()
        links = parse_attachment_links(items[0].link, detail)
        self.assertEqual(len(links), 1)
        with self.assertRaises(ContractError):
            parse_rss(b"<rss><channel></channel></rss>")

    def test_nested_odt_table_paragraphs_are_emitted_exactly_once(self) -> None:
        from nhi_rule_history.update.odt import (
            extract_odt_blocks,
            inspect_odt_document,
        )

        inspected = inspect_odt_document(
            fixture_nested_table_odt(),
            "a" * 64,
        )
        blocks = inspected["blocks"]
        texts = [block["raw_text"] for block in blocks]
        self.assertEqual(
            texts,
            [
                "表格外生效資訊",
                "外層儲存格文字",
                "外層清單文字",
                "內層儲存格文字",
                "內層表格之後的外層文字",
            ],
        )
        self.assertEqual(len({block["block_id"] for block in blocks}), 5)
        self.assertEqual(
            [block["locator"]["table_index"] for block in blocks],
            [None, 0, 0, 1, 0],
        )
        self.assertEqual(
            [block["locator"]["table_depth"] for block in blocks],
            [None, 0, 0, 1, 0],
        )
        self.assertEqual(
            [block["locator"]["parent_table_index"] for block in blocks],
            [None, None, None, 0, None],
        )
        self.assertEqual(
            [blocks[index]["locator"]["paragraph_index"] for index in (1, 2, 4)],
            [0, 1, 2],
        )
        self.assertEqual(
            inspected["structural_facts"]["source_paragraph_count"],
            inspected["structural_facts"]["emitted_block_count"],
        )
        self.assertTrue(
            inspected["structural_facts"]["exact_once_verified"]
        )
        self.assertEqual(
            extract_odt_blocks(fixture_nested_table_odt(), "a" * 64),
            blocks,
        )

    def test_odt_structural_facts_preserve_distinct_equal_text(self) -> None:
        from nhi_rule_history.update.odt import inspect_odt_document

        first = inspect_odt_document(
            fixture_structural_table_odt(),
            "b" * 64,
        )
        second = inspect_odt_document(
            fixture_structural_table_odt(),
            "b" * 64,
        )
        blocks = first["blocks"]
        facts = first["structural_facts"]
        self.assertEqual(
            [block["raw_text"] for block in blocks],
            [
                "相同文字",
                "covered 來源文字",
                "相同文字",
                "row span 來源文字",
            ],
        )
        self.assertNotEqual(blocks[0]["block_id"], blocks[2]["block_id"])
        self.assertEqual(first, second)
        self.assertEqual(facts["covered_cell_count"], 1)
        self.assertEqual(facts["column_span_cell_count"], 1)
        self.assertEqual(facts["row_span_cell_count"], 1)
        self.assertEqual(facts["repeated_cell_count"], 1)
        self.assertEqual(facts["repeated_row_count"], 1)
        self.assertEqual(facts["source_paragraph_count"], 4)
        self.assertEqual(facts["emitted_block_count"], 4)
        self.assertEqual(blocks[0]["locator"]["row_kind"], "header")
        self.assertEqual(blocks[1]["locator"]["row_kind"], "header")
        self.assertEqual(blocks[2]["locator"]["row_kind"], "body")
        self.assertEqual(blocks[3]["locator"]["row_kind"], "body")

    def test_observed_nested_duplicate_pattern_emits_106_not_212(self) -> None:
        from nhi_rule_history.update.odt import inspect_odt_document

        inspected = inspect_odt_document(
            fixture_nested_duplicate_regression_odt(),
            "c" * 64,
        )
        self.assertEqual(len(inspected["blocks"]), 106)
        self.assertEqual(
            inspected["structural_facts"]["source_paragraph_count"],
            106,
        )
        self.assertEqual(
            len({block["block_id"] for block in inspected["blocks"]}),
            106,
        )

    def test_non_drug_payment_notice_is_not_selected(self) -> None:
        payload = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>公告暫予支付特殊材料「可控式導引鞘」之給付規定</title>
  <link>https://www.nhi.gov.tw/ch/cp-1-example-3258-1.html</link>
  <guid>special-material-1</guid>
  <description>特殊材料給付規定</description>
</item>
</channel></rss>""".encode()
        items = parse_rss(payload)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].is_likely_drug_rule)

    def test_poll_observation_is_immutable_and_collapse_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            feed_response = response(
                "https://www.nhi.gov.tw/ch/rss-3258-1.xml",
                fixture_feed(),
                "application/rss+xml",
            )
            poll = observe_feed(
                Path(temporary),
                response=feed_response,
                observed_guids=[],
                previous_item_count=None,
            )
            self.assertEqual(len(poll.new_items), 1)
            self.assertEqual(verify_poll(poll.path)["new_item_count"], 1)
            replay = observe_feed(
                Path(temporary),
                response=feed_response,
                observed_guids=["notice-1"],
                previous_item_count=1,
            )
            self.assertFalse(replay.replayed)
            self.assertEqual(len(replay.new_items), 0)
            with self.assertRaises(ContractError):
                observe_feed(
                    Path(temporary) / "collapse",
                    response=feed_response,
                    observed_guids=[],
                    previous_item_count=20,
                )

    def test_bundle_seal_verify_and_idempotent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._bundle(root)
            self.assertFalse(first.replayed)
            checked = verify_bundle(first.path)
            self.assertEqual(checked["attachment_count"], 2)
            second = self._bundle(root)
            self.assertTrue(second.replayed)
            self.assertEqual(first.bundle_id, second.bundle_id)

    def test_corpus_bundle_is_atomic_content_bound_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(root / "source")
            first = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            self.assertFalse(first["replayed"])
            target = Path(first["bundle_path"])
            self.assertTrue((target / "attachment-000.odt").is_file())
            self.assertTrue((target / "attachment-001.pdf").is_file())
            self.assertTrue((target / "raw.md").is_file())
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], "1.3")
            self.assertEqual(manifest["declared_attachment_count"], 2)
            self.assertEqual(
                manifest["source_uid"], "gov_健保審字第1150000000號"
            )
            self.assertEqual(
                manifest["ref_number"], "健保審字第1150000000號"
            )
            self.assertEqual(
                manifest["ref_number_raw"], "健保審字第1150000000號"
            )
            self.assertEqual(
                manifest["ref_number_normalization"], "exact"
            )
            second = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            self.assertTrue(second["replayed"])
            original_raw = (target / "raw.md").read_bytes()
            original_manifest = (target / "manifest.json").read_bytes()
            altered_raw = original_raw + b"\ncoordinated tamper\n"
            (target / "raw.md").write_bytes(altered_raw)
            altered_manifest = json.loads(
                original_manifest.decode("utf-8")
            )
            altered_manifest["raw_md_sha256"] = sha256_bytes(altered_raw)
            altered_manifest["raw_md_bytes"] = len(altered_raw)
            raw_row = next(
                row
                for row in altered_manifest["files"]
                if row["file_name"] == "raw.md"
            )
            raw_row["sha256"] = sha256_bytes(altered_raw)
            raw_row["byte_size"] = len(altered_raw)
            (target / "manifest.json").write_bytes(
                canonical_json_bytes(altered_manifest)
            )
            with self.assertRaisesRegex(
                ContractError, "raw markdown does not verify"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )
            (target / "raw.md").write_bytes(original_raw)
            (target / "manifest.json").write_bytes(original_manifest)
            outside_manifest = root / "outside-manifest.json"
            outside_manifest.write_bytes(original_manifest)
            (target / "manifest.json").unlink()
            (target / "manifest.json").symlink_to(outside_manifest)
            with self.assertRaisesRegex(
                ContractError, "lacks manifest"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )

    def test_missing_terminal_hao_is_normalized_without_losing_source_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(
                root / "source",
                reference_number="健保審字第1150671800",
            )
            result = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            target = Path(result["bundle_path"])
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["source_uid"], "gov_健保審字第1150671800號"
            )
            self.assertEqual(
                manifest["ref_number"], "健保審字第1150671800號"
            )
            self.assertEqual(
                manifest["ref_number_raw"], "健保審字第1150671800"
            )
            self.assertEqual(
                manifest["ref_number_normalization"],
                "terminal_hao_appended",
            )
            self.assertEqual(
                manifest["ref_number_normalization_rule"],
                "nhi-reference-number-normalization/1.1.0",
            )
            raw = (target / "raw.md").read_text(encoding="utf-8")
            self.assertIn(
                'reference_number_raw: "健保審字第1150671800"',
                raw,
            )
            packet = source_packet(sealed.path)
            self.assertEqual(
                packet["notice_metadata"]["reference_number_raw"],
                "健保審字第1150671800",
            )
            self.assertEqual(
                packet["notice_metadata"]["reference_number_normalized"],
                "健保審字第1150671800號",
            )

    def test_terminal_full_stop_is_removed_without_losing_source_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(
                root / "source",
                reference_number="健保審字第1150055418號。",
                announcement_html=(
                    "<p>一、收載新藥品項目。</p>"
                    "<p>二、修訂 cefiderocol 給付規定。</p>"
                ),
            )
            result = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            target = Path(result["bundle_path"])
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], "1.3")
            self.assertEqual(
                manifest["source_uid"], "gov_健保審字第1150055418號"
            )
            self.assertEqual(
                manifest["ref_number"], "健保審字第1150055418號"
            )
            self.assertEqual(
                manifest["ref_number_raw"], "健保審字第1150055418號。"
            )
            self.assertEqual(
                manifest["ref_number_normalization"],
                "terminal_full_stop_removed",
            )
            self.assertEqual(
                manifest["ref_number_normalization_rule"],
                "nhi-reference-number-normalization/1.1.0",
            )
            raw = (target / "raw.md").read_text(encoding="utf-8")
            self.assertIn(
                'reference_number_raw: "健保審字第1150055418號。"',
                raw,
            )
            frontmatter = raw.split("---", 2)[1]
            self.assertIn(
                'reference_number: "健保審字第1150055418號"',
                frontmatter,
            )
            self.assertIn(
                (
                    "reference_number_normalization: "
                    "terminal_full_stop_removed"
                ),
                frontmatter,
            )
            self.assertIn(
                (
                    "reference_number_normalization_rule: "
                    "nhi-reference-number-normalization/1.1.0"
                ),
                frontmatter,
            )
            self.assertIn(
                "extraction: nhi-rule-history-corpus-bundle/1.3.0",
                frontmatter,
            )
            self.assertEqual(
                manifest["raw_md_sha256"],
                sha256_bytes(raw.encode("utf-8")),
            )
            self.assertEqual(
                manifest["raw_md_bytes"],
                len(raw.encode("utf-8")),
            )
            packet = source_packet(sealed.path)
            self.assertEqual(
                packet["notice_metadata"]["reference_number_raw"],
                "健保審字第1150055418號。",
            )
            self.assertEqual(
                packet["notice_metadata"]["reference_number_normalized"],
                "健保審字第1150055418號",
            )
            self.assertEqual(
                packet["notice_metadata"]["announcement_text_raw"],
                (
                    "一、收載新藥品項目。\n\n"
                    "二、修訂 cefiderocol 給付規定。"
                ),
            )
            self.assertIn("一、收載新藥品項目。", raw)
            self.assertIn("二、修訂 cefiderocol 給付規定。", raw)

    def test_terminal_full_stop_and_missing_hao_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(
                root / "source",
                reference_number="健保審字第1150055418。",
            )
            result = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            manifest = json.loads(
                (
                    Path(result["bundle_path"]) / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["ref_number"], "健保審字第1150055418號"
            )
            self.assertEqual(
                manifest["ref_number_normalization"],
                (
                    "terminal_full_stop_removed_and_"
                    "terminal_hao_appended"
                ),
            )

    def test_v13_whitespace_set_includes_nbsp_and_ideographic_space(
        self,
    ) -> None:
        normalized, reason = normalize_reference_number(
            "\u3000健保審字第1150055418。\u00a0"
        )
        self.assertEqual(normalized, "健保審字第1150055418號")
        self.assertEqual(
            reason,
            (
                "whitespace_removed_and_"
                "terminal_full_stop_removed_and_"
                "terminal_hao_appended"
            ),
        )

    def test_existing_v12_target_replays_without_byte_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template_sealed = self._bundle(root / "template-source")
            generated = prepare_corpus_bundle(
                template_sealed.path,
                corpus_root=root / "generated",
            )
            source_reference = "\u2003健保審字第1150000000號\u2003"
            sealed = self._bundle(
                root / "source",
                reference_number=source_reference,
            )
            generated_target = Path(generated["bundle_path"])
            target = (
                root
                / "corpus"
                / "2026"
                / "gov_健保審字第1150000000號"
            )
            target.mkdir(parents=True)
            for path in generated_target.iterdir():
                if path.is_file():
                    (target / path.name).write_bytes(path.read_bytes())
            legacy_manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            legacy_manifest["schema_version"] = "1.2"
            legacy_manifest["origin_update_bundle_id"] = sealed.bundle_id
            legacy_manifest["origin_update_bundle_fingerprint"] = (
                sealed.bundle_fingerprint
            )
            legacy_manifest["ref_number_raw"] = (
                "健保審字第1150000000號"
            )
            legacy_manifest["ref_number_normalization"] = "exact"
            legacy_manifest["ref_number_normalization_rule"] = (
                "nhi-reference-number-normalization/1.0.0"
            )
            raw_path = target / "raw.md"
            raw = raw_path.read_bytes().replace(
                b"nhi-rule-history-corpus-bundle/1.3.0",
                b"nhi-rule-history-corpus-bundle/1.2.0",
            )
            raw = raw.replace(
                b"nhi-reference-number-normalization/1.1.0",
                b"nhi-reference-number-normalization/1.0.0",
            )
            raw = raw.replace(
                json.dumps(
                    source_reference,
                    ensure_ascii=False,
                ).encode("utf-8"),
                json.dumps(
                    "健保審字第1150000000號",
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            raw = raw.replace(
                source_reference.encode("utf-8"),
                "健保審字第1150000000號".encode("utf-8"),
            )
            raw_path.write_bytes(raw)
            legacy_manifest["raw_md_sha256"] = sha256_bytes(raw)
            legacy_manifest["raw_md_bytes"] = len(raw)
            for row in legacy_manifest["files"]:
                if row["file_name"] == "raw.md":
                    row["sha256"] = sha256_bytes(raw)
                    row["byte_size"] = len(raw)
            source_manifest = json.loads(
                (sealed.path / "manifest.json").read_text(encoding="utf-8")
            )
            source_name_by_relation = {
                "detail_page": "source.html",
                "rss_feed": "source-rss.xml",
            }
            for source_row in source_manifest["resources"]:
                relation = source_row["relation"]
                if relation in source_name_by_relation:
                    name = source_name_by_relation[relation]
                elif relation == "declared_attachment":
                    name = (
                        f"attachment-"
                        f"{source_row['declared_sequence']:03d}"
                        f"{Path(source_row['content_path']).suffix}"
                    )
                else:
                    continue
                payload = (
                    sealed.path / source_row["content_path"]
                ).read_bytes()
                (target / name).write_bytes(payload)
                manifest_row = next(
                    row
                    for row in legacy_manifest["files"]
                    if row["file_name"] == name
                )
                manifest_row["sha256"] = sha256_bytes(payload)
                manifest_row["byte_size"] = len(payload)
                if relation == "declared_attachment":
                    manifest_row["declared_label"] = source_row[
                        "declared_label"
                    ]
                    manifest_row["media_type"] = source_row["media_type"]
                    manifest_row["origin_artifact_sha256"] = source_row[
                        "artifact_sha256"
                    ]
            (target / "manifest.json").write_bytes(
                canonical_json_bytes(legacy_manifest)
            )
            self.assertEqual(
                legacy_manifest["declared_attachment_count"],
                len(
                    [
                        row
                        for row in legacy_manifest["files"]
                        if row["role"] == "declared_attachment"
                    ]
                ),
            )
            for row in legacy_manifest["files"]:
                path = target / row["file_name"]
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, row["byte_size"])
                self.assertEqual(
                    sha256_bytes(path.read_bytes()),
                    row["sha256"],
                )
            before = {
                path.name: path.read_bytes()
                for path in target.iterdir()
                if path.is_file()
            }
            result = prepare_corpus_bundle(
                sealed.path,
                corpus_root=root / "corpus",
            )
            after = {
                path.name: path.read_bytes()
                for path in target.iterdir()
                if path.is_file()
            }
            self.assertTrue(result["replayed"])
            self.assertEqual(after, before)
            (target / "raw.md").write_bytes(b"corrupt")
            with self.assertRaisesRegex(
                ContractError, "does not verify"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )
            (target / "raw.md").write_bytes(before["raw.md"])
            (target / "unlisted.txt").write_text(
                "not in manifest",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ContractError, "does not match its manifest"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )
            (target / "unlisted.txt").unlink()
            altered_manifest = json.loads(
                before["manifest.json"].decode("utf-8")
            )
            next(
                row
                for row in altered_manifest["files"]
                if row["role"] == "declared_attachment"
            )["declared_label"] = "不同標籤"
            (target / "manifest.json").write_bytes(
                canonical_json_bytes(altered_manifest)
            )
            with self.assertRaisesRegex(
                ContractError, "source binding does not verify"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )
            (target / "manifest.json").write_bytes(
                before["manifest.json"]
            )
            altered_manifest = json.loads(
                before["manifest.json"].decode("utf-8")
            )
            altered_manifest["title_zh"] = "遭竄改標題"
            (target / "manifest.json").write_bytes(
                canonical_json_bytes(altered_manifest)
            )
            with self.assertRaisesRegex(
                ContractError, "metadata does not match source"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )
            (target / "manifest.json").write_bytes(
                before["manifest.json"]
            )
            source_html = target / "source.html"
            source_html_payload = source_html.read_bytes()
            outside = root / "outside-source.html"
            outside.write_bytes(source_html_payload)
            source_html.unlink()
            source_html.symlink_to(outside)
            with self.assertRaisesRegex(
                ContractError, "does not verify"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=root / "corpus",
                )

    def test_existing_v11_replay_preserves_raw_reference_semantics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = "健保審字第 1150000000 號"
            sealed = self._bundle(
                root / "source",
                reference_number=reference,
            )
            created = prepare_corpus_bundle(
                sealed.path,
                corpus_root=root / "corpus",
            )
            target = Path(created["bundle_path"])
            manifest_path = target / "manifest.json"
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["schema_version"] = "1.1"
            manifest["ref_number"] = reference
            manifest.pop("ref_number_raw")
            manifest.pop("ref_number_normalization")
            manifest.pop("ref_number_normalization_rule")
            source_manifest = json.loads(
                (sealed.path / "manifest.json").read_text(encoding="utf-8")
            )
            detail = next(
                row
                for row in source_manifest["resources"]
                if row["relation"] == "detail_page"
            )
            detail_payload = (
                sealed.path / detail["content_path"]
            ).read_bytes()
            metadata = extract_notice_metadata_v12(
                detail_payload,
                detail["artifact_sha256"],
            )
            raw = _expected_replay_raw(
                source_bundle_path=sealed.path,
                source_manifest=source_manifest,
                existing_manifest=manifest,
                source_uid=manifest["source_uid"],
                metadata=metadata,
            )
            self.assertIn(
                f"reference_number: {json.dumps(reference, ensure_ascii=False)}",
                raw.decode("utf-8"),
            )
            self.assertNotIn(
                "reference_number_raw:",
                raw.decode("utf-8"),
            )
            (target / "raw.md").write_bytes(raw)
            manifest["raw_md_sha256"] = sha256_bytes(raw)
            manifest["raw_md_bytes"] = len(raw)
            raw_row = next(
                row
                for row in manifest["files"]
                if row["file_name"] == "raw.md"
            )
            raw_row["sha256"] = sha256_bytes(raw)
            raw_row["byte_size"] = len(raw)
            manifest_path.write_bytes(canonical_json_bytes(manifest))
            replay = prepare_corpus_bundle(
                sealed.path,
                corpus_root=root / "corpus",
            )
            self.assertTrue(replay["replayed"])

    def test_html_metadata_rejects_whitespace_outside_fixed_set(
        self,
    ) -> None:
        payload = (
            "<html><table>"
            "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
            "<tr><th>發文字號</th>"
            "<td>\u2003健保審字第1150055418號。</td></tr>"
            "<tr><th>公告事項</th><td>修訂規定。</td></tr>"
            "<tr><th>發文日期</th><td>115-07-15</td></tr>"
            "</table><dl>"
            "<dt>發布日期</dt><dd>115-07-15</dd>"
            "<dt>更新日期</dt><dd>115-07-16</dd>"
            "</dl></html>"
        ).encode()
        with self.assertRaisesRegex(
            ContractError, "missing or ambiguous"
        ):
            extract_notice_metadata(payload, sha256_bytes(payload))

    def test_html_metadata_preserves_allowed_whitespace_exactly(
        self,
    ) -> None:
        reference = "\u3000健保審字第\u00a01150055418號。\t"
        payload = (
            "<html><table>"
            "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
            f"<tr><th>發文字號</th><td>{reference}</td></tr>"
            "<tr><th>公告事項</th><td>修訂規定。</td></tr>"
            "<tr><th>發文日期</th><td>115-07-15</td></tr>"
            "</table><dl>"
            "<dt>發布日期</dt><dd>115-07-15</dd>"
            "<dt>更新日期</dt><dd>115-07-16</dd>"
            "</dl></html>"
        ).encode()
        metadata = extract_notice_metadata(payload, sha256_bytes(payload))
        self.assertEqual(metadata["reference_number_raw"], reference)
        self.assertEqual(
            metadata["reference_number_normalized"],
            "健保審字第1150055418號",
        )
        self.assertEqual(
            metadata["reference_number_normalization"],
            "whitespace_removed_and_terminal_full_stop_removed",
        )

    def test_announcement_uses_same_row_and_is_order_independent(
        self,
    ) -> None:
        payload = (
            "<html><table>"
            "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
            "<tr><th>發文字號</th>"
            "<td>健保審字第1150055418號。</td></tr>"
            "<tr><th>發文日期</th><td>115-07-15</td></tr>"
            "<tr><th>公告事項</th><td>"
            "<p>一、第一段。</p><p>發文日期</p><p>二、第二段。</p>"
            "</td></tr>"
            "<tr><th>備註</th><td>不得併入公告事項。</td></tr>"
            "</table><dl>"
            "<dt>發布日期</dt><dd>115-07-15</dd>"
            "<dt>更新日期</dt><dd>115-07-16</dd>"
            "</dl></html>"
        ).encode()
        metadata = extract_notice_metadata(payload, sha256_bytes(payload))
        self.assertEqual(
            metadata["announcement_text_raw"],
            "一、第一段。\n\n發文日期\n\n二、第二段。",
        )
        self.assertNotIn(
            "不得併入公告事項",
            metadata["announcement_text_raw"],
        )

    def test_nested_metadata_table_fails_closed(self) -> None:
        payload = (
            "<html><table>"
            "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
            "<tr><th>發文字號</th><td>"
            "<table><tr><td>健保審字第1150055418號。</td></tr></table>"
            "</td></tr>"
            "<tr><th>公告事項</th><td>修訂規定。</td></tr>"
            "<tr><th>發文日期</th><td>115-07-15</td></tr>"
            "</table><dl>"
            "<dt>發布日期</dt><dd>115-07-15</dd>"
            "<dt>更新日期</dt><dd>115-07-16</dd>"
            "</dl></html>"
        ).encode()
        with self.assertRaisesRegex(
            ContractError, "missing or ambiguous"
        ):
            extract_notice_metadata(payload, sha256_bytes(payload))

    def test_announcement_preserves_mixed_blocks_breaks_and_bare_text(
        self,
    ) -> None:
        payload = (
            "<html><table>"
            "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
            "<tr><th>發文字號</th>"
            "<td>健保審字第1150055418號。</td></tr>"
            "<tr><th>公告事項</th><td>"
            "<p>一<br>續</p>裸文字<div>二</div>"
            "</td></tr>"
            "<tr><th>發文日期</th><td>\n 115-07-15 \n</td></tr>"
            "</table><dl>"
            "<div><dt>發布日期</dt><dd>\n115-07-15\n</dd></div>"
            "<div><dt>更新日期</dt><dd> 115-07-16 </dd></div>"
            "</dl></html>"
        ).encode()
        metadata = extract_notice_metadata(payload, sha256_bytes(payload))
        self.assertEqual(
            metadata["announcement_text_raw"],
            "一\n續\n\n裸文字\n\n二",
        )
        self.assertEqual(metadata["document_date_roc_raw"], "115-07-15")
        self.assertEqual(metadata["publication_date_roc_raw"], "115-07-15")
        self.assertEqual(metadata["update_date_roc_raw"], "115-07-16")

    def test_definition_pairs_cannot_cross_dl_or_exist_outside_dl(
        self,
    ) -> None:
        variants = (
            (
                "<dl><dt>發布日期</dt></dl>"
                "<dl><dd>115-07-15</dd>"
                "<dt>更新日期</dt><dd>115-07-16</dd></dl>"
            ),
            (
                "<dt>發布日期</dt><dd>115-07-15</dd>"
                "<dl><dt>更新日期</dt><dd>115-07-16</dd></dl>"
            ),
            (
                "<dl><dt>發布日期</dt>"
                "<dd>115-07-15</dl></dd>"
                "<dl><dt>更新日期</dt><dd>115-07-16</dd></dl>"
            ),
        )
        for definition_html in variants:
            with self.subTest(definition_html=definition_html):
                payload = (
                    "<html><table>"
                    "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
                    "<tr><th>發文字號</th>"
                    "<td>健保審字第1150055418號。</td></tr>"
                    "<tr><th>公告事項</th><td>修訂規定。</td></tr>"
                    "<tr><th>發文日期</th><td>115-07-15</td></tr>"
                    "</table>"
                    f"{definition_html}</html>"
                ).encode()
                with self.assertRaisesRegex(
                    ContractError, "missing or ambiguous"
                ):
                    extract_notice_metadata(payload, sha256_bytes(payload))

    def test_ignored_void_content_does_not_pollute_cell_text(
        self,
    ) -> None:
        for void_html in ("<br/>", "<br>"):
            with self.subTest(void_html=void_html):
                payload = (
                    "<html><table>"
                    "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
                    "<tr><th>發文字號</th>"
                    "<td>健保審字第1150055418號。</td></tr>"
                    "<tr><th>公告事項</th><td>"
                    f"a<svg>{void_html}</svg>b"
                    "</td></tr>"
                    "<tr><th>發文日期</th><td>115-07-15</td></tr>"
                    "</table><dl>"
                    "<dt>發布日期</dt><dd>115-07-15</dd>"
                    "<dt>更新日期</dt><dd>115-07-16</dd>"
                    "</dl></html>"
                ).encode()
                metadata = extract_notice_metadata(
                    payload, sha256_bytes(payload)
                )
                self.assertEqual(metadata["announcement_text_raw"], "ab")

    def test_reference_normalization_rejects_unsupported_punctuation(
        self,
    ) -> None:
        invalid = (
            "健保審字第1150055418號。。",
            "健保審字第1150055418號.",
            "健保審字第1150055418。號",
            "健保審字第1150055418號。附註",
            "。健保審字第1150055418號",
            "健保審字第１１５００５５４１８號。",
            "健保審字第١١٥٠٠٥٥٤١٨號。",
        )
        for reference_number in invalid:
            with self.subTest(reference_number=reference_number):
                with tempfile.TemporaryDirectory() as temporary:
                    sealed = self._bundle(
                        Path(temporary) / "source",
                        reference_number=reference_number,
                    )
                    with self.assertRaisesRegex(
                        ContractError, "missing or ambiguous"
                    ):
                        prepare_corpus_bundle(
                            sealed.path,
                            corpus_root=Path(temporary) / "corpus",
                        )

    def test_reference_normalization_rejects_other_missing_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sealed = self._bundle(
                Path(temporary) / "source",
                reference_number="1150671800",
            )
            with self.assertRaisesRegex(
                ContractError, "missing or ambiguous"
            ):
                prepare_corpus_bundle(
                    sealed.path,
                    corpus_root=Path(temporary) / "corpus",
                )

    def test_corpus_bundle_preserves_all_declared_attachments_in_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = parse_rss(fixture_feed())[0]
            detail = response(
                item.link,
                (
                    "<html><table>"
                    "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
                    "<tr><th>發文字號</th><td>健保審字第1150000000號</td></tr>"
                    "<tr><th>公告事項</th><td>修訂多附件規定</td></tr>"
                    "<tr><th>發文日期</th><td>115-07-15</td></tr>"
                    "</table><dl>"
                    "<dt>發布日期</dt><dd>115-07-15</dd>"
                    "<dt>更新日期</dt><dd>115-07-16</dd></dl>"
                    '<a href="/ch/dl-a-1.pdf">附件1 PDF</a>'
                    '<a href="/ch/dl-a-1.ods">附件1 ODS</a>'
                    '<a href="/ch/dl-a-2.pdf">附件2 PDF</a>'
                    '<a href="/ch/dl-a-2.odt">附件2 ODT</a>'
                    '<a href="/ch/dl-a-3.odt">附件3 ODT</a>'
                    "</html>"
                ).encode(),
                "text/html; charset=utf-8",
            )
            links = parse_attachment_links(item.link, detail.body)
            payloads = [
                (b"%PDF-1.7\nfirst", "application/pdf"),
                (
                    b"ods-source",
                    "application/vnd.oasis.opendocument.spreadsheet",
                ),
                (b"%PDF-1.7\nsecond", "application/pdf"),
                (
                    fixture_odt(),
                    "application/vnd.oasis.opendocument.text",
                ),
                (
                    fixture_odt("9.5 第二份 ODT 新條文"),
                    "application/vnd.oasis.opendocument.text",
                ),
            ]
            sealed = BundleBuilder(
                root / "source",
                rss_item=item,
                feed_response=response(
                    "https://www.nhi.gov.tw/ch/rss-3258-1.xml",
                    fixture_feed(),
                    "application/rss+xml",
                ),
                detail_response=detail,
                attachments=[
                    (link, response(link.url, payload, content_type))
                    for link, (payload, content_type) in zip(links, payloads)
                ],
            ).seal()
            result = prepare_corpus_bundle(
                sealed.path, corpus_root=root / "corpus"
            )
            target = Path(result["bundle_path"])
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            source_manifest = json.loads(
                (sealed.path / "manifest.json").read_text(encoding="utf-8")
            )
            source_attachment_rows = [
                row
                for row in source_manifest["resources"]
                if row["relation"] == "declared_attachment"
            ]
            attachment_rows = [
                row
                for row in manifest["files"]
                if row["role"] == "declared_attachment"
            ]
            self.assertEqual(manifest["declared_attachment_count"], 5)
            self.assertEqual(len(attachment_rows), 5)
            self.assertEqual(
                [row["file_name"] for row in attachment_rows],
                [
                    "attachment-000.pdf",
                    "attachment-001.ods",
                    "attachment-002.pdf",
                    "attachment-003.odt",
                    "attachment-004.odt",
                ],
            )
            self.assertEqual(
                [row["declared_sequence"] for row in attachment_rows],
                [0, 1, 2, 3, 4],
            )
            self.assertEqual(
                [row["declared_label"] for row in attachment_rows],
                [
                    "附件1 PDF",
                    "附件1 ODS",
                    "附件2 PDF",
                    "附件2 ODT",
                    "附件3 ODT",
                ],
            )
            self.assertEqual(
                [row["media_type"] for row in attachment_rows],
                [
                    "application/pdf",
                    "application/vnd.oasis.opendocument.spreadsheet",
                    "application/pdf",
                    "application/vnd.oasis.opendocument.text",
                    "application/vnd.oasis.opendocument.text",
                ],
            )
            self.assertEqual(
                [row["origin_artifact_sha256"] for row in attachment_rows],
                [
                    row["artifact_sha256"]
                    for row in source_attachment_rows
                ],
            )
            self.assertEqual(
                [row["byte_size"] for row in attachment_rows],
                [row["byte_size"] for row in source_attachment_rows],
            )
            raw = (target / "raw.md").read_text(encoding="utf-8")
            self.assertIn('"declared_sequence":3', raw)
            self.assertIn("9.4 新條文完整文字", raw)
            self.assertIn('"declared_sequence":4', raw)
            self.assertIn("9.5 第二份 ODT 新條文", raw)

    def test_proposal_authority_and_anchor_pending_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sealed = self._bundle(Path(temporary))
            packet = source_packet(sealed.path)
            proposal = valid_proposal(
                packet["source_blocks"],
                parity_unverified=False,
                identity_uncertainty=False,
            )
            validated = validate_proposal(
                proposal,
                source_blocks=packet["source_blocks"],
                bundle_id=sealed.bundle_id,
                bundle_fingerprint=sealed.bundle_fingerprint,
                expected_notice=packet["notice_binding_source"],
            )
            self.assertTrue(validated["first_lane_shape"])
            self.assertEqual(
                validated["state"], "promotion_ready_pending_anchor"
            )
            bad = json.loads(json.dumps(proposal))
            bad["rule_id"] = "forbidden"
            with self.assertRaises(ProposalError):
                validate_proposal(
                    bad,
                    source_blocks=packet["source_blocks"],
                    bundle_id=sealed.bundle_id,
                    bundle_fingerprint=sealed.bundle_fingerprint,
                    expected_notice=packet["notice_binding_source"],
                )

    def test_legacy_worker_directory_does_not_collide_and_new_run_replays(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(root / "bundles")
            packet = source_packet(sealed.path)
            prompt_sha = sha256_bytes(
                build_worker_prompt(packet).encode("utf-8")
            )
            legacy_fingerprint = stable_id(
                "nhi-worker-job",
                packet["bundle_id"],
                packet["bundle_fingerprint"],
                WORKER_PROMPT_VERSION,
                prompt_sha,
            )
            current_fingerprint = worker_job_fingerprint(
                manifest_sha256=packet["manifest_sha256"],
                prompt_sha256=prompt_sha,
            )
            self.assertNotEqual(legacy_fingerprint, current_fingerprint)
            candidate_root = root / "candidates"
            legacy_dir = candidate_root / legacy_fingerprint
            legacy_dir.mkdir(parents=True)
            legacy_receipt = legacy_dir / "candidate-receipt.json"
            legacy_receipt.write_text(
                '{"schema":"nhi-rule-history/worker-run/v1"}',
                encoding="utf-8",
            )

            payload = json.dumps(
                valid_proposal(
                    packet["source_blocks"],
                    parity_unverified=True,
                    identity_uncertainty=True,
                ),
                ensure_ascii=False,
            )
            calls: list[list[str]] = []

            def runner(argv, **_kwargs):
                calls.append(argv)
                return SimpleNamespace(
                    stdout=payload,
                    stderr="",
                    returncode=0,
                )

            orchestrator = WorkerOrchestrator(
                primary=WorkerSpec(
                    "primary-worker",
                    "primary-runtime",
                    "test-provider-a",
                    "test-model-a",
                    ("primary",),
                ),
                fallback=WorkerSpec(
                    "fallback-worker",
                    "fallback-runtime",
                    "test-provider-b",
                    "test-model-b",
                    ("fallback",),
                ),
                runner=runner,
            )
            receipt = orchestrator.run(
                bundle_path=sealed.path,
                candidate_root=candidate_root,
            )
            self.assertEqual(
                receipt["job_fingerprint"], current_fingerprint
            )
            self.assertEqual(
                legacy_receipt.read_text(encoding="utf-8"),
                '{"schema":"nhi-rule-history/worker-run/v1"}',
            )
            replay = orchestrator.run(
                bundle_path=sealed.path,
                candidate_root=candidate_root,
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(calls), 1)

    def test_worker_fingerprint_binds_every_persisted_contract(self) -> None:
        manifest_sha = "a" * 64
        prompt_sha = "b" * 64
        baseline = worker_job_fingerprint(
            manifest_sha256=manifest_sha,
            prompt_sha256=prompt_sha,
        )
        self.assertNotEqual(
            baseline,
            worker_job_fingerprint(
                manifest_sha256="c" * 64,
                prompt_sha256=prompt_sha,
            ),
        )
        self.assertNotEqual(
            baseline,
            worker_job_fingerprint(
                manifest_sha256=manifest_sha,
                prompt_sha256="d" * 64,
            ),
        )
        for contract_name in (
            "WORKER_PROMPT_VERSION",
            "WORKER_ATTEMPT_SCHEMA",
            "WORKER_RUN_SCHEMA",
            "PROPOSAL_SCHEMA",
        ):
            with self.subTest(contract=contract_name), mock.patch.object(
                worker_contract,
                contract_name,
                getattr(worker_contract, contract_name) + "-next",
            ):
                self.assertNotEqual(
                    baseline,
                    worker_contract.worker_job_fingerprint(
                        manifest_sha256=manifest_sha,
                        prompt_sha256=prompt_sha,
                    ),
                )

    def test_worker_primary_contract_failure_then_linked_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = self._bundle(root / "bundles")
            packet = source_packet(sealed.path)
            fallback_payload = json.dumps(
                valid_proposal(
                    packet["source_blocks"],
                    parity_unverified=True,
                    identity_uncertainty=True,
                ),
                ensure_ascii=False,
            )
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                calls.append(argv)
                if len(calls) == 1:
                    return SimpleNamespace(
                        stdout="not json", stderr="", returncode=0
                    )
                return SimpleNamespace(
                    stdout=fallback_payload, stderr="", returncode=0
                )

            orchestrator = WorkerOrchestrator(
                primary=WorkerSpec(
                    "primary-worker",
                    "primary-runtime",
                    "test-provider-a",
                    "test-model-a",
                    ("primary",),
                ),
                fallback=WorkerSpec(
                    "fallback-worker",
                    "fallback-runtime",
                    "test-provider-b",
                    "test-model-b",
                    ("fallback",),
                ),
                runner=runner,
            )
            receipt = orchestrator.run(
                bundle_path=sealed.path,
                candidate_root=root / "candidates",
            )
            self.assertEqual(receipt["attempt_count"], 2)
            self.assertEqual(receipt["selected_role"], "fallback")
            self.assertEqual(receipt["candidate"]["state"], "needs_review")
            attempts = [
                json.loads(line)
                for line in (
                    root
                    / "candidates"
                    / receipt["job_fingerprint"]
                    / "attempts.jsonl"
                )
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(attempts[0]["status"], "contract_failed")
            self.assertEqual(
                attempts[1]["primary_attempt_id"],
                attempts[0]["attempt_id"],
            )
            self.assertEqual(attempts[1]["fallback_reason"], "contract_failed")
            run_dir = root / "candidates" / receipt["job_fingerprint"]
            self.assertEqual(
                (run_dir / "primary-stdout.bin").read_text(encoding="utf-8"),
                "not json",
            )
            self.assertEqual(
                (run_dir / "fallback-stdout.bin").read_text(encoding="utf-8"),
                fallback_payload,
            )
            self.assertTrue((run_dir / "primary-stderr.bin").is_file())
            self.assertTrue((run_dir / "fallback-stderr.bin").is_file())
            replay = orchestrator.run(
                bundle_path=sealed.path,
                candidate_root=root / "candidates",
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(calls), 2)

    def test_worker_double_failure_is_sealed_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed = self._bundle(root / "bundles")
            calls: list[list[str]] = []

            def runner(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(
                    command, 0, stdout="not json", stderr=""
                )

            orchestrator = WorkerOrchestrator(
                primary=WorkerSpec(
                    "primary-worker",
                    "primary-runtime",
                    "provider-a",
                    "model-a",
                    ("primary",),
                    30,
                ),
                fallback=WorkerSpec(
                    "fallback-worker",
                    "fallback-runtime",
                    "provider-b",
                    "model-b",
                    ("fallback",),
                    30,
                ),
                runner=runner,
            )
            with self.assertRaisesRegex(
                Exception, "primary and fallback worker attempts failed"
            ):
                orchestrator.run(
                    bundle_path=sealed.path,
                    candidate_root=root / "candidates",
                )
            with self.assertRaisesRegex(
                Exception, "already failed for this immutable job"
            ):
                orchestrator.run(
                    bundle_path=sealed.path,
                    candidate_root=root / "candidates",
                )
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
