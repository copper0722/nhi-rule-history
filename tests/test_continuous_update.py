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
from nhi_rule_history.contracts import ContractError, sha256_bytes, stable_id
from nhi_rule_history.update.bundle import BundleBuilder, verify_bundle
from nhi_rule_history.update.corpus_bundle import prepare_corpus_bundle
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
        "notice": {
            "reference_number_raw": "健保審字第1150000000號",
            "subject_raw": "修訂藥品給付規定",
        },
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
    ):
        item = parse_rss(fixture_feed())[0]
        detail_url = item.link
        detail = response(
            detail_url,
            (
                "<html><table>"
                "<tr><th>主旨</th><td>修訂藥品給付規定</td></tr>"
                f"<tr><th>發文字號</th><td>{reference_number}</td></tr>"
                "<tr><th>公告事項</th><td>修訂第9節9.4規定</td></tr>"
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
            self.assertEqual(manifest["schema_version"], "1.2")
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
                "nhi-reference-number-normalization/1.0.0",
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
            attachment_rows = [
                row
                for row in manifest["files"]
                if row["role"] == "declared_attachment"
            ]
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
