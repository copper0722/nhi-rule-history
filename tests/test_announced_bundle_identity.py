"""Queued corpus bundles must be the registered ones, proven by hash.

Corpus registration pins the SHA-256 of the canonical manifest bytes.  Later
corpus bookkeeping may re-serialize manifest.json and add or advance
``extraction_status`` progress keys.  Such a bundle is admitted only when
reverting exactly those keys rebuilds the registered digest; any other
manifest change, even one consistent with the files on disk, is refused.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.announced_notice import (
    AnnouncedNoticeError,
    ODT_MEDIA_TYPE,
    parse_notice,
    read_notice_bundle,
    registered_manifest_identity,
)
from nhi_rule_history.announced_release import (
    AnnouncedReleaseError,
    queued_bundle,
)
from nhi_rule_history.contracts import canonical_json_bytes
from tests.test_announced_notice import _comparison, _odt


REFERENCE = "健保審字第0000000000號"
RELATIVE = "2026/gov_健保審字第0000000000號"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _registered_bundle(root: Path) -> tuple[Path, dict]:
    """Write a bundle whose manifest.json is the registered canonical form."""

    bundle = root / RELATIVE
    bundle.mkdir(parents=True)
    odt = _odt(
        _comparison([(["9.4.Fixture：(115/9/1)", "限用於"], ["9.4.Fixture："])])
    )
    raw_md = "# 公告\n\n## 公告事項\n\n修訂給付規定。\n".encode("utf-8")
    (bundle / "attachment-000.odt").write_bytes(odt)
    (bundle / "raw.md").write_bytes(raw_md)
    manifest = {
        "schema": "nhi-rule-history/corpus-source-bundle/v1",
        "canonical_url": "https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
        "declared_attachment_count": 1,
        "extraction_status": {
            "deterministic_blocks": "done",
            "legal_history_promotion": "blocked_pending_anchor_replay",
            "proofread": "not_started",
        },
        "files": [
            {
                "byte_size": len(raw_md),
                "file_name": "raw.md",
                "role": "deterministic_extraction",
                "sha256": _sha(raw_md),
            },
            {
                "byte_size": len(odt),
                "declared_sequence": 0,
                "file_name": "attachment-000.odt",
                "media_type": ODT_MEDIA_TYPE,
                "role": "declared_attachment",
                "sha256": _sha(odt),
            },
        ],
        "publish_date": "2026-07-29",
        "ref_number": REFERENCE,
        "source_uid": "gov_健保審字第0000000000號",
        "title_zh": "公告修訂給付規定。",
    }
    (bundle / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return bundle, manifest


def _bookkept(bundle: Path, manifest: dict, **status: str) -> None:
    """Rewrite manifest.json the way corpus bookkeeping lanes do."""

    rewritten = json.loads(json.dumps(manifest))
    rewritten["extraction_status"].update(status)
    (bundle / "manifest.json").write_text(
        json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class RegisteredManifestIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle, self.manifest = _registered_bundle(self.root)
        self.registered = _sha((self.bundle / "manifest.json").read_bytes())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _identity(self) -> dict:
        return registered_manifest_identity(
            (self.bundle / "manifest.json").read_bytes(), self.registered
        )

    def test_unchanged_bytes(self) -> None:
        self.assertEqual(self._identity()["method"], "identical_bytes")
        bundle = read_notice_bundle(
            self.bundle, registered_manifest_sha256=self.registered
        )
        self.assertEqual(bundle.manifest_sha256, self.registered)
        self.assertEqual(bundle.manifest_identity["method"], "identical_bytes")

    def test_reserialized_manifest(self) -> None:
        _bookkept(self.bundle, self.manifest)
        self.assertEqual(self._identity()["method"], "canonical_bytes")

    def test_bookkeeping_is_reverted_and_recorded(self) -> None:
        _bookkept(self.bundle, self.manifest, mineru="done", proofread="done")
        current = _sha((self.bundle / "manifest.json").read_bytes())
        identity = self._identity()
        self.assertEqual(
            identity["method"], "canonical_bytes_with_bookkeeping_reverted"
        )
        self.assertEqual(
            identity["reverted_extraction_status"],
            {
                "mineru": {"registered": None, "current": "done"},
                "proofread": {"registered": "not_started", "current": "done"},
            },
        )
        self.assertEqual(identity["current_manifest_sha256"], current)
        bundle = read_notice_bundle(
            self.bundle, registered_manifest_sha256=self.registered
        )
        # The notice is identified by the registered manifest, not the rewrite.
        self.assertEqual(bundle.manifest_sha256, self.registered)
        notice = parse_notice(bundle)
        self.assertEqual([clause.clause_code for clause in notice.clauses], ["9.4"])
        # Without a receipt the current bytes identify the notice.
        self.assertEqual(read_notice_bundle(self.bundle).manifest_sha256, current)

    def test_bookkeeping_progress_value_is_not_constrained(self) -> None:
        # The digest proves the registered value; the lane's current progress
        # value is bookkeeping and may be anything.
        _bookkept(self.bundle, self.manifest, proofread="in_progress")
        self.assertEqual(
            self._identity()["reverted_extraction_status"],
            {"proofread": {"registered": "not_started", "current": "in_progress"}},
        )

    def test_file_row_change_is_refused_even_when_disk_agrees(self) -> None:
        # Confusable negative: the attachment and its row change together, so
        # the bundle is self-consistent but is not the registered bundle.
        odt = _odt(
            _comparison([(["9.4.Fixture：(115/9/1)", "限用於X"], ["9.4.Fixture："])])
        )
        (self.bundle / "attachment-000.odt").write_bytes(odt)
        changed = json.loads(json.dumps(self.manifest))
        changed["files"][1].update(byte_size=len(odt), sha256=_sha(odt))
        _bookkept(self.bundle, changed, mineru="done")
        self.assertEqual(read_notice_bundle(self.bundle).attachments[0].sha256, _sha(odt))
        with self.assertRaisesRegex(AnnouncedNoticeError, "beyond extraction-status"):
            self._identity()
        with self.assertRaisesRegex(AnnouncedNoticeError, "registration receipt"):
            read_notice_bundle(self.bundle, registered_manifest_sha256=self.registered)

    def test_other_changes_are_refused(self) -> None:
        cases = {
            "deterministic_blocks": {"deterministic_blocks": "pending"},
            "legal_history_promotion": {"legal_history_promotion": "done"},
            "unknown_status_key": {"mineru": "done", "html": "done"},
        }
        for name, status in cases.items():
            with self.subTest(case=name):
                _bookkept(self.bundle, self.manifest, **status)
                with self.assertRaises(AnnouncedNoticeError):
                    self._identity()
        changed = json.loads(json.dumps(self.manifest))
        changed["title_zh"] = "公告修訂給付規定（更正）。"
        _bookkept(self.bundle, changed, mineru="done")
        with self.assertRaises(AnnouncedNoticeError):
            self._identity()

    def test_a_status_key_present_at_registration_may_not_change(self) -> None:
        registered = json.loads(json.dumps(self.manifest))
        registered["extraction_status"]["mineru"] = "pending"
        (self.bundle / "manifest.json").write_bytes(canonical_json_bytes(registered))
        self.registered = _sha((self.bundle / "manifest.json").read_bytes())
        _bookkept(self.bundle, registered, mineru="done")
        with self.assertRaises(AnnouncedNoticeError):
            self._identity()

    def test_receipt_digest_is_required(self) -> None:
        for digest in ("", "0" * 63, "G" * 64):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(AnnouncedNoticeError, "no manifest digest"):
                    registered_manifest_identity(b"{}", digest)


class QueuedBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle, self.manifest = _registered_bundle(self.root)
        self.registered = _sha((self.bundle / "manifest.json").read_bytes())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _row(self, **evidence: str) -> dict:
        return {
            "work_item_id": "00000000-0000-0000-0000-000000000001",
            "first_title_raw": "公告修訂給付規定",
            "evidence_json": {
                "source_uid": "gov_健保審字第0000000000號",
                "corpus_bundle_relative_path": RELATIVE,
                "corpus_manifest_sha256": self.registered,
                **evidence,
            },
        }

    def test_bookkept_bundle_is_admitted_with_its_identity(self) -> None:
        _bookkept(self.bundle, self.manifest, mineru="done")
        queued = queued_bundle(self._row(), self.root)
        self.assertIsNone(queued.problem)
        self.assertEqual(queued.corpus_manifest_sha256, self.registered)
        self.assertEqual(
            queued.manifest_identity["method"],
            "canonical_bytes_with_bookkeeping_reverted",
        )

    def test_changed_bundle_carries_a_problem(self) -> None:
        changed = json.loads(json.dumps(self.manifest))
        changed["declared_attachment_count"] = 2
        _bookkept(self.bundle, changed, mineru="done")
        queued = queued_bundle(self._row(), self.root)
        self.assertIn("registration receipt", queued.problem)
        missing = queued_bundle(
            self._row(corpus_bundle_relative_path="2026/missing"), self.root
        )
        self.assertEqual(missing.problem, "corpus manifest is missing")

    def test_unsafe_receipt_path_raises(self) -> None:
        for path in ("", "/2026/x", "2026/../x"):
            with self.subTest(path=path):
                with self.assertRaises(AnnouncedReleaseError):
                    queued_bundle(
                        self._row(corpus_bundle_relative_path=path), self.root
                    )


if __name__ == "__main__":
    unittest.main()
