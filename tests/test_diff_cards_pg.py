"""The website's git-diff card store: row serialisation, the v29 migration and ``load-pg``.

Three layers.  Serialisation is pure: official comparison tables (the repository's reduced
official fixtures, plus paragraphs cut from the public 2026-10-01 tables) become block objects
that must replay their paragraphs.  Selection is checked against the overlay's own
``parse_notice``.  The store tests run against a disposable PostgreSQL cluster built from the
migration pair alone; nothing here touches a production database.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import functools
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
import uuid
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import psycopg

from nhi_rule_history.announced_notice import (
    ODT_MEDIA_TYPE,
    AnnouncedNoticeError,
    NoticeBundle,
    parse_notice,
    read_notice_bundle,
    read_odt_document,
)
from nhi_rule_history.announced_release import QueuedBundle
from nhi_rule_history.contracts import canonical_json_bytes
from tests import test_update_queue_recovery_v2 as recovery_fixture
from tests.test_announced_notice import _cell, _comparison, _document, _odt
from tools.build_announced_notice_fixture import fixture_odt
from tools.diff_cards import pg_store
from tools.diff_cards.build import main
from tools.diff_cards.core import Row, key
from tools.diff_cards.sources_nhi import read_table

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
FORWARD = MIGRATIONS / "2026-10-19_nhi_rule_history_diff_card_v29.sql"
ROLLBACK = MIGRATIONS / "2026-10-19_nhi_rule_history_diff_card_v29.rollback.sql"
READBACK = ROOT / "database" / "queries" / "diff-card-readback-v29.sql"
ANNOUNCED = ROOT / "tests" / "fixtures" / "announced_notices"
CLAUSES = json.loads(
    (ROOT / "tests" / "fixtures" / "diff_cards" / "nhi-2026-10-01-clauses.json").read_text(encoding="utf-8")
)

# Public repository: no private host, address or path may appear in the files.
PRIVATE_MARKERS = re.compile(
    r"\b[a-z][a-z0-9-]*:\d{4,5}\b"  # host:port
    r"|(?<!['\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.'])"  # unquoted IPv4 (clause codes are quoted)
    r"|/home/|/data/|\.ts\.net\b",  # private paths, tailnet names
    re.IGNORECASE,
)
# Clause codes such as 2.1.4.2 look like an address to the pattern above; the fixture is text from
# the official tables, so it is only checked for hosts and paths.
PRIVATE_PATHS = re.compile(r"\b[a-z][a-z0-9-]*:\d{4,5}\b|/home/|/data/|\.ts\.net\b", re.IGNORECASE)

OFFICIAL = {  # reduced official content, by the notice number its file name carries
    "1150056995": "2.1.4.2",
    "1150057033": "3.3.28",
    "1150672381": "4.2",
    "1150672509": "8.1.3",
}
VIEW_COLUMNS = [
    ("source_artifact_sha256", "text"), ("generator_version", "text"), ("comparison_sha256", "text"),
    ("extra_table_count", "integer"), ("block_order", "integer"), ("block_kind", "text"),
    ("clause_id", "text"), ("clause_codes", "ARRAY"), ("path", "text"), ("badge", "text"),
    ("hunk_old", "text"), ("hunk_new", "text"), ("adds", "integer"), ("dels", "integer"),
    ("old_paragraphs", "jsonb"), ("new_paragraphs", "jsonb"), ("rows", "jsonb"),
]


@functools.cache
def official_odt(number: str) -> bytes:
    """The reduced official content as an ODT; built once, because the container stamps the build time."""

    return fixture_odt((ANNOUNCED / f"{number}.content.xml").read_bytes())


def fixture_table(code: str) -> dict:
    """A table as ``read_table`` returns it, from paragraphs cut out of the public 2026-10-01 tables."""

    entry = CLAUSES["clauses"][code]
    return {
        "sha256": hashlib.sha256(json.dumps(entry, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "clauses": [{"old": entry["old"], "new": entry["new"]}],
        "extra_comparisons": [],
        "extra_tables": [],
    }


def official(number: str) -> dict:
    return pg_store.build_comparison(official_odt(number))


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_bundle(root: Path, reference: str, attachments: Sequence[tuple[bytes, str]]) -> QueuedBundle:
    """A registered corpus bundle on disk: manifest in its registered form, raw.md with block receipts."""

    bundle = root / f"gov_{reference}"
    bundle.mkdir(parents=True)
    receipts, rows = [], []
    for sequence, (payload, media_type) in enumerate(attachments):
        name = f"attachment-{sequence:03d}.{'odt' if media_type == ODT_MEDIA_TYPE else 'pdf'}"
        (bundle / name).write_bytes(payload)
        rows.append({
            "byte_size": len(payload), "declared_sequence": sequence, "file_name": name,
            "media_type": media_type, "role": "declared_attachment", "sha256": sha(payload),
        })
        if media_type == ODT_MEDIA_TYPE:
            receipts += [
                "<!-- source-block " + json.dumps({
                    "attachment_file_name": name, "block_id": item.block_id,
                    "raw_text_sha256": item.block_text_sha256,
                }, ensure_ascii=False) + " -->\n"
                for item in read_odt_document(payload).paragraphs
            ]
    raw_md = ("# 公告\n\n## 公告事項\n\n修訂給付規定。\n\n" + "".join(receipts)).encode("utf-8")
    (bundle / "raw.md").write_bytes(raw_md)
    manifest = {
        "schema": "nhi-rule-history/corpus-source-bundle/v1",
        "canonical_url": "https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
        "declared_attachment_count": len(rows),
        "extraction_status": {"deterministic_blocks": "done", "proofread": "not_started"},
        "files": [{"byte_size": len(raw_md), "file_name": "raw.md",
                   "role": "deterministic_extraction", "sha256": sha(raw_md)}, *rows],
        "publish_date": "2026-07-29",
        "ref_number": f"健保審字第{reference}號",
        "source_uid": f"gov_{reference}",
        "title_zh": "公告修訂給付規定。",
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    return QueuedBundle(
        work_item_id=str(uuid.uuid5(uuid.NAMESPACE_URL, reference)), source_uid=manifest["source_uid"],
        bundle_dir=bundle, corpus_manifest_sha256=sha(manifest_bytes), first_title_raw=manifest["title_zh"],
    )


def synthetic_odt(number: int, extra_rows: Sequence[tuple[list[str], list[str]]] = (), **header) -> bytes:
    rows = [([f"9.{number}.Fixture：(115/9/1)", "限用於甲"], [f"9.{number}.Fixture：", "限用於乙"]), *extra_rows]
    return _odt(_comparison(rows, **header))


def comparison_table(header: tuple[str, str], rows: Sequence[tuple[list[str], list[str]]]) -> str:
    return (
        "<table:table><table:table-header-rows><table:table-row>" + _cell(header[0]) + _cell(header[1])
        + "</table:table-row></table:table-header-rows>"
        + "".join("<table:table-row>" + _cell(*new) + _cell(*old) + "</table:table-row>" for new, old in rows)
        + "</table:table>"
    )


def contiguous_run(part: list[str], whole: list[str]) -> int:
    """Index where ``part`` occurs as a contiguous run of ``whole``, or -1."""

    for start in range(len(whole) - len(part) + 1):
        if whole[start:start + len(part)] == part:
            return start
    return -1


class SerialisationTest(unittest.TestCase):
    def test_official_2_1_4_2_row_by_row(self) -> None:
        comparison = official("1150056995")
        (block,) = comparison["blocks"]
        self.assertEqual(
            {k: block[k] for k in ("block_order", "block_kind", "clause_id", "clause_codes", "path", "badge",
                                   "hunk_old", "hunk_new", "adds", "dels")},
            {"block_order": 0, "block_kind": "clause", "clause_id": "2.1.4.2", "clause_codes": ["2.1.4.2"],
             "path": "藥品給付規定/2.1.4.2", "badge": "修訂", "hunk_old": "第1～13段", "hunk_new": "第1～19段",
             "adds": 11, "dels": 5},
        )
        self.assertEqual((len(block["old_paragraphs"]), len(block["new_paragraphs"])), (13, 19))
        kinds = [row["kind"] for row in block["rows"]]
        self.assertEqual({"ctx", "del", "add"}, set(kinds))
        self.assertEqual((kinds.count("add"), kinds.count("del")), (11, 5))
        for row in block["rows"]:
            self.assertEqual(set(row), {"kind", "old_no", "new_no", "pair", "indent_em", "segments"})
            marked = any(piece["mark"] for piece in row["segments"])
            self.assertTrue(row["kind"] != "ctx" or not marked)
            self.assertEqual(
                (row["old_no"] != "", row["new_no"] != ""),
                {"ctx": (True, True), "del": (True, False), "add": (False, True)}[row["kind"]],
            )
        # the edited first paragraph: a del and an add row sharing one pair id, the change marked
        first_del, first_add = block["rows"][0], block["rows"][1]
        self.assertEqual((first_del["kind"], first_add["kind"]), ("del", "add"))
        self.assertEqual(first_del["pair"], first_add["pair"])
        self.assertEqual([p["text"] for p in first_add["segments"] if p["mark"]], ["、115/10/1"])
        pg_store.check_replay(block)

    def test_every_official_fixture_replays_and_is_deterministic(self) -> None:
        for number in OFFICIAL:
            with self.subTest(notice=number):
                comparison = official(number)
                self.assertEqual(comparison["comparison_sha256"], official(number)["comparison_sha256"])
                for block in comparison["blocks"]:
                    pg_store.check_replay(block)
                    self.assertNotIn("ctxc", {row["kind"] for row in block["rows"]})

    def test_nested_heading_row_serves_both_codes(self) -> None:
        # 9.5 holds the heading of 9.5.1: the announced layer splits it into two patches
        comparison = pg_store.comparison_from_table(fixture_table("9.5"))
        (block,) = comparison["blocks"]
        self.assertEqual((block["clause_id"], block["clause_codes"]), ("9.5", ["9.5", "9.5.1"]))
        self.assertEqual((block["hunk_old"], block["hunk_new"], block["badge"]), ("第1～6段", "第1～6段", "修訂"))
        pg_store.check_replay(block)

    def test_new_clause_has_no_old_side(self) -> None:
        comparison = pg_store.comparison_from_table(fixture_table("9.139"))
        (block,) = comparison["blocks"]
        self.assertEqual(CLAUSES["clauses"]["9.139"]["old"], ["無"])
        self.assertEqual((block["badge"], block["hunk_old"], block["hunk_new"]), ("新增", "（無）", "第1～7段"))
        self.assertEqual(block["old_paragraphs"], [])
        self.assertEqual({row["kind"] for row in block["rows"]}, {"add"})
        self.assertEqual([row["new_no"] for row in block["rows"]], [str(n) for n in range(1, 8)])
        self.assertEqual((block["adds"], block["dels"]), (7, 0))
        pg_store.check_replay(block)

    def test_one_paragraph_original_and_the_attachment_table(self) -> None:
        comparison = official("1150672381")
        clause, attachment = comparison["blocks"]
        self.assertEqual((clause["block_kind"], clause["clause_id"], clause["clause_codes"]), ("clause", "4.2", ["4.2"]))
        self.assertEqual((len(clause["old_paragraphs"]), clause["hunk_old"], clause["hunk_new"]), (1, "第1段", "第1～10段"))
        self.assertEqual(
            (attachment["block_order"], attachment["block_kind"], attachment["clause_id"], attachment["path"],
             attachment["clause_codes"]),
            (1, "attachment", "附表十八之五", "藥品給付規定/附表十八之五", ["4.2"]),
        )
        self.assertEqual((attachment["adds"], attachment["dels"]), (1, 1))
        self.assertEqual(comparison["extra_table_count"], 0)

    def test_attachment_serves_the_union_of_the_clause_codes_in_block_order(self) -> None:
        body = (
            "<text:p>「藥品給付規定」修訂對照表</text:p><text:p>（自115年10月1日生效）</text:p>"
            + comparison_table(("修訂後給付規定", "原給付規定"), [
                (["9.41.Fixture：(115/9/1)", "9.41.1.Sub：(115/9/1)", "限用於甲"],
                 ["9.41.Fixture：", "9.41.1.Sub：", "限用於乙"]),
                (["9.7.Other：(115/9/1)"], ["9.7.Other："]),
            ])
            + "<text:p>附表二十 測試表單</text:p>"
            + comparison_table(("修訂後附表規定", "原附表規定"), [(["甲表（新）"], ["甲表（舊）"])])
        )
        comparison = pg_store.comparison_from_table(read_table(_odt(_document(body))))
        self.assertEqual([(b["block_order"], b["block_kind"], b["clause_id"], b["clause_codes"])
                          for b in comparison["blocks"]], [
            (0, "clause", "9.41", ["9.41", "9.41.1"]),
            (1, "clause", "9.7", ["9.7"]),
            (2, "attachment", "附表二十", ["9.41", "9.41.1", "9.7"]),  # block order, not numeric order
        ])

    def test_attachment_of_a_notice_with_a_nested_row_serves_the_nested_code_too(self) -> None:
        table = fixture_table("9.5")
        table["extra_comparisons"] = [{"index": 1, "kind": "附表", "title": "附表二十 測試表單",
                                       "rows": [{"new": ["甲表（新）"], "old": ["甲表（舊）"]}]}]
        clause, attachment = pg_store.comparison_from_table(table)["blocks"]
        self.assertEqual(clause["clause_codes"], ["9.5", "9.5.1"])
        self.assertEqual((attachment["block_kind"], attachment["clause_id"], attachment["clause_codes"]),
                         ("attachment", "附表二十", ["9.5", "9.5.1"]))

    def test_a_sub_clause_only_the_original_column_has_is_still_served(self) -> None:
        # the amendment deletes 9.8.1: its heading is in the original column only
        comparison = pg_store.comparison_from_table(read_table(_odt(_comparison([(
            ["9.8.Fixture：(115/10/1)", "限用於甲"], ["9.8.Fixture：", "9.8.1.Removed：", "限用於乙"])]))))
        (block,) = comparison["blocks"]
        self.assertEqual(block["clause_codes"], ["9.8", "9.8.1"])
        self.assertNotIn("9.8.1", "".join(block["new_paragraphs"]))
        deleted = [row for row in block["rows"] if row["kind"] == "del"
                   and "".join(p["text"] for p in row["segments"]).startswith("9.8.1.")]
        self.assertEqual(len(deleted), 1)

    def test_clause_codes_are_own_id_then_revised_order_then_original_only(self) -> None:
        new = ["9.8.A：", "9.8.2.B：", "9.8.1.C："]
        old = ["9.8.A：", "9.8.3.D：", "9.8.1.C：", "9.8.4.E："]
        # revised column order (2 before 1, not numeric), then what only the original column has
        self.assertEqual(pg_store.clause_codes_of("9.8", old, new), ["9.8", "9.8.2", "9.8.1", "9.8.3", "9.8.4"])
        self.assertEqual(pg_store.clause_codes_of("9.8", [], new), ["9.8", "9.8.2", "9.8.1"])
        self.assertEqual(pg_store.clause_codes_of("9.8", old, []), ["9.8", "9.8.3", "9.8.1", "9.8.4"])
        self.assertEqual(pg_store.clause_codes_of("9.8", ["9.8.A："], ["9.8.A："]), ["9.8"])

    def test_only_headings_nested_under_the_clause_are_added_to_the_codes(self) -> None:
        # a heading nested under the row's clause is served by the row; a sibling clause that
        # shares the row, or a number inside the text, is not
        def codes(new, old=()):
            return pg_store.clause_codes_of("9.8", old, new)

        self.assertEqual(codes(["9.8.A：", "9.8.1.B：", "9.8.1.2.C：", "9.9.D：", "9.80.E："]),
                         ["9.8", "9.8.1", "9.8.1.2"])
        self.assertEqual(codes(["9.8.A：", "限用於 9.8.1.", "1.9.8.1.x"]), ["9.8"])
        self.assertEqual(codes(["9.8.A：", "9.9.D："], ["9.8.A：", "9.10.E："]), ["9.8"])
        # the overlay's grammar reads full-width digits and stops
        self.assertEqual(codes(["９.８.１．B："]), ["9.8", "9.8.1"])
        self.assertEqual(pg_store.clause_codes_of("10.3", [], ["10.3.A：", "10.3.8.B：", "10.30.C："]), ["10.3", "10.3.8"])

    def test_segments(self) -> None:
        self.assertEqual(pg_store.segments("abcdef", []), [{"text": "abcdef", "mark": False}])
        self.assertEqual(pg_store.segments("abcdef", [(0, 2), (4, 6)]), [
            {"text": "ab", "mark": True}, {"text": "cd", "mark": False}, {"text": "ef", "mark": True}])
        self.assertEqual(pg_store.segments("abcdef", [(2, 4)]), [
            {"text": "ab", "mark": False}, {"text": "cd", "mark": True}, {"text": "ef", "mark": False}])
        self.assertEqual(pg_store.segments("abcd", [(0, 2), (2, 4)]), [
            {"text": "ab", "mark": True}, {"text": "cd", "mark": True}])
        self.assertEqual(pg_store.segments("", []), [{"text": "", "mark": False}])
        for spans in ([(0, 3), (2, 4)], [(3, 3)], [(2, 9)], [(-1, 2)]):
            with self.subTest(spans=spans), self.assertRaises(pg_store.CardError):
                pg_store.segments("abcdef", spans)
        # characters outside the BMP are single characters, whatever the consumer's string type
        text = "甲𠀀乙"
        self.assertEqual("".join(p["text"] for p in pg_store.segments(text, [(1, 2)])), text)

    def test_row_object_collapses_ctxc_and_marks_only_changed_rows(self) -> None:
        context = pg_store.row_object(Row("ctxc", "x" * 40, "3", "4", indent_em=1.5, covers_old=[2], covers_new=[3]))
        self.assertEqual(context, {"kind": "ctx", "old_no": "3", "new_no": "4", "pair": -1, "indent_em": 1.5,
                                   "segments": [{"text": "x" * 40, "mark": False}]})
        added = pg_store.row_object(Row("add", "新增文字", "", "2", spans=[(2, 4)], pair=0))
        self.assertEqual(added["segments"], [{"text": "新增", "mark": False}, {"text": "文字", "mark": True}])
        with self.assertRaises(pg_store.CardError):
            pg_store.row_object(Row("fold", "未修訂 4 段"))

    def test_replay_invariant_names_each_kind_of_damage(self) -> None:
        block = official("1150056995")["blocks"][0]
        pg_store.check_replay(block)  # the undamaged block is the positive control

        def damaged(change) -> dict:
            clone = copy.deepcopy(block)
            change(clone)
            return clone

        def drop_a_context_row(b):
            index = next(i for i, r in enumerate(b["rows"]) if r["kind"] == "ctx")
            del b["rows"][index]

        def swap_numbers(b):
            ctx = [r for r in b["rows"] if r["kind"] == "ctx"]
            ctx[0]["old_no"], ctx[1]["old_no"] = ctx[1]["old_no"], ctx[0]["old_no"]

        cases = {
            "a dropped context row": drop_a_context_row,
            "swapped old numbers": swap_numbers,
            "a changed deleted text": lambda b: b["rows"][0]["segments"][0].update(text="X" + b["rows"][0]["segments"][0]["text"]),
            "a changed added text": lambda b: b["rows"][1]["segments"][-1].update(text="Y"),
            "a mark on a context row": lambda b: next(r for r in b["rows"] if r["kind"] == "ctx")["segments"][0].update(mark=True),
            "a wrong add count": lambda b: b.update(adds=b["adds"] + 1),
            "a broken pair": lambda b: b["rows"][0].update(pair=90),
            "a context row with a pair": lambda b: next(r for r in b["rows"] if r["kind"] == "ctx").update(pair=0),
            "a number with a leading zero": lambda b: next(r for r in b["rows"] if r["kind"] == "ctx").update(new_no="0" + next(r for r in b["rows"] if r["kind"] == "ctx")["new_no"]),
            "an add row with an old number": lambda b: b["rows"][1].update(old_no="1"),
            "a missing field": lambda b: b["rows"][0].pop("segments"),
            "an unknown kind": lambda b: b["rows"][0].update(kind="fold"),
            "a negative indent": lambda b: b["rows"][0].update(indent_em=-1.0),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(pg_store.CardError):
                pg_store.check_replay(damaged(change))

    def test_hash_is_a_function_of_the_blocks_alone(self) -> None:
        comparison = official("1150672381")
        self.assertEqual(
            comparison["comparison_sha256"],
            hashlib.sha256(canonical_json_bytes(comparison["blocks"])).hexdigest(),
        )
        reordered = [dict(reversed(list(b.items()))) for b in comparison["blocks"]]
        self.assertEqual(pg_store.comparison_sha256(reordered), comparison["comparison_sha256"])
        changed = copy.deepcopy(comparison["blocks"])
        changed[1]["rows"][0]["indent_em"] += 0.5
        self.assertNotEqual(pg_store.comparison_sha256(changed), comparison["comparison_sha256"])
        # the block list is ordered: the same blocks in another order are another comparison
        self.assertNotEqual(pg_store.comparison_sha256(comparison["blocks"][::-1]), comparison["comparison_sha256"])


class KeyEqualityTest(unittest.TestCase):
    """The announced patch text equals a contiguous run of the block's revised paragraphs.

    The announced layer prints ODT auto-numbering as ``1.<tab>`` and splits nested clauses, so the
    texts differ byte for byte; under ``core.key`` they agree, which is how a card is laid next to
    its patch.
    """

    def patch_keys(self, code: str) -> list[str]:
        paragraphs = CLAUSES["patch_texts"][code]["patch_text"].split("\n\n")
        return [key(p) for p in paragraphs]

    def test_2_1_4_2_patch_is_the_whole_revised_column(self) -> None:
        paragraphs = CLAUSES["patch_texts"]["2.1.4.2"]["patch_text"].split("\n\n")
        self.assertTrue(any(re.match(r"\d+\.\t", p) for p in paragraphs), "the announced text carries list labels")
        (block,) = official("1150056995")["blocks"]
        new_keys = [key(p) for p in block["new_paragraphs"]]
        self.assertEqual(contiguous_run(self.patch_keys("2.1.4.2"), new_keys), 0)
        self.assertEqual(len(self.patch_keys("2.1.4.2")), len(new_keys))

    def test_9_5_1_patch_is_the_tail_of_the_9_5_row(self) -> None:
        (block,) = pg_store.comparison_from_table(fixture_table("9.5"))["blocks"]
        new_keys = [key(p) for p in block["new_paragraphs"]]
        patch = self.patch_keys("9.5.1")
        self.assertEqual(len(patch), 5)
        self.assertEqual(contiguous_run(patch, new_keys), 1)
        self.assertIn("9.5.1", block["clause_codes"])
        # confusable negatives: the same text with one paragraph changed or moved is not found
        changed = patch[:2] + [patch[2] + "。"] + patch[3:]
        self.assertEqual(contiguous_run(changed, new_keys), -1)
        self.assertEqual(contiguous_run(patch[::-1], new_keys), -1)
        self.assertEqual(contiguous_run(self.patch_keys("2.1.4.2"), new_keys), -1)


class SelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def bundle(self, reference: str, attachments: Sequence[tuple[bytes, str]]) -> NoticeBundle:
        item = write_bundle(self.root, reference, attachments)
        return read_notice_bundle(item.bundle_dir, registered_manifest_sha256=item.corpus_manifest_sha256)

    def test_selected_attachment_is_the_one_the_overlay_records(self) -> None:
        for number in OFFICIAL:
            with self.subTest(notice=number):
                bundle = self.bundle(number, [(official_odt(number), ODT_MEDIA_TYPE)])
                attachment, payload = pg_store.comparison_attachment(bundle)
                self.assertEqual(attachment.sha256, parse_notice(bundle).attachment.sha256)
                self.assertEqual(sha(payload), attachment.sha256)

    def test_other_attachments_are_passed_over_like_the_overlay_does(self) -> None:
        plain = _odt(_document("<text:p>附件一</text:p>"))
        bundle = self.bundle("1150000001", [
            (plain, ODT_MEDIA_TYPE),
            (b"%PDF-1.4 not a comparison", "application/pdf"),
            (official_odt("1150056995"), ODT_MEDIA_TYPE),
        ])
        attachment, _ = pg_store.comparison_attachment(bundle)
        self.assertEqual(attachment.file_name, "attachment-002.odt")
        self.assertEqual(attachment.sha256, parse_notice(bundle).attachment.sha256)

    def test_no_comparison_table_and_several_are_refused_as_the_overlay_does(self) -> None:
        none = self.bundle("1150000002", [(b"%PDF-1.4 only a pdf", "application/pdf")])
        with self.assertRaisesRegex(pg_store.CardError, "no official comparison table"):
            pg_store.comparison_attachment(none)
        two = self.bundle("1150000003", [(synthetic_odt(41), ODT_MEDIA_TYPE), (synthetic_odt(42), ODT_MEDIA_TYPE)])
        with self.assertRaisesRegex(pg_store.CardError, "span several ODT attachments"):
            pg_store.comparison_attachment(two)
        with self.assertRaisesRegex(AnnouncedNoticeError, "span several ODT attachments"):
            parse_notice(two)

    def test_bundle_to_comparison_carries_the_queue_identity(self) -> None:
        item = write_bundle(self.root, "1150056995", [(official_odt("1150056995"), ODT_MEDIA_TYPE)])
        comparison = pg_store.comparison_for_bundle(item)
        self.assertEqual(comparison["work_item_id"], item.work_item_id)
        self.assertEqual(comparison["corpus_manifest_sha256"], item.corpus_manifest_sha256)
        self.assertEqual(comparison["source_artifact_sha256"], sha(official_odt("1150056995")))
        # a bundle whose manifest is not the registered one is refused before any ODT is read
        forged = dataclasses.replace(item, corpus_manifest_sha256="0" * 64)
        with self.assertRaises(AnnouncedNoticeError):
            pg_store.comparison_for_bundle(forged)


class DiffCardStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.real = [write_bundle(root, number, [(official_odt(number), ODT_MEDIA_TYPE)]) for number in OFFICIAL]
        cls.nested = write_bundle(root, "1150000009", [(_odt(_comparison([(
            ["9.7.Fixture：", "9.7.1.Nested：(115/10/1)", "限用於丙"], ["9.7.Fixture：", "9.7.1.Nested：", "限用於丁"])])),
            ODT_MEDIA_TYPE)])
        cls.root = root

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.pg.psql(file=ROLLBACK)
        self.pg.psql(file=FORWARD)

    def rows(self, sql: str, params=()) -> list[tuple]:
        with psycopg.connect(self.pg.dsn) as connection:
            connection.execute("SET default_transaction_read_only = on")
            return [tuple(row) for row in connection.execute(sql, params).fetchall()]

    def load(self, bundles, **kwargs) -> dict:
        return pg_store.run(self.pg.dsn, bundles, **kwargs)

    # ---- the migration ----

    def test_forward_creates_the_designed_objects_and_nothing_else(self) -> None:
        relations = self.rows("SELECT relname, relkind::text FROM pg_class "
                              "WHERE relnamespace = 'nhi_rule_history_diff_card'::regnamespace "
                              "AND relkind IN ('r', 'v')")
        self.assertEqual(sorted(relations), [("comparison", "r"), ("comparison_block", "r"),
                                             ("v_public_comparison_block", "v")])
        self.assertEqual(
            self.rows("SELECT column_name, data_type FROM information_schema.columns "
                      "WHERE table_schema = 'nhi_rule_history_diff_card' "
                      "AND table_name = 'v_public_comparison_block' ORDER BY ordinal_position"),
            VIEW_COLUMNS,
        )
        self.assertEqual(
            self.rows("SELECT proname FROM pg_proc WHERE pronamespace = 'nhi_rule_history_diff_card'::regnamespace "
                      "ORDER BY 1"),
            [("check_comparison_complete",), ("reject_write_once_change",)],
        )
        (view,) = self.rows("SELECT reloptions::text[] FROM pg_class "
                            "WHERE oid = 'nhi_rule_history_diff_card.v_public_comparison_block'::regclass")
        self.assertIn("security_invoker=true", view[0])
        self.assertEqual(
            self.rows("SELECT is_updatable, is_insertable_into, is_trigger_updatable, is_trigger_deletable, "
                      "is_trigger_insertable_into FROM information_schema.views "
                      "WHERE table_schema = 'nhi_rule_history_diff_card'"),
            [("NO", "NO", "NO", "NO", "NO")],
        )
        self.assertEqual(self.rows("SELECT has_schema_privilege('public', 'nhi_rule_history_diff_card', 'USAGE')"),
                         [(False,)])
        self.assertEqual(
            self.rows("SELECT count(*) FROM pg_class AS c WHERE c.relnamespace = 'nhi_rule_history_diff_card'::regnamespace "
                      "AND c.relkind IN ('r', 'v') AND (has_table_privilege('public', c.oid, 'SELECT') "
                      "OR EXISTS (SELECT 1 FROM aclexplode(c.relacl) AS a WHERE a.grantee <> c.relowner))"),
            [(0,)],
        )

    def test_tables_are_write_once_and_a_comparison_commits_whole(self) -> None:
        self.assertEqual(self.load(self.real[:1])["counts"]["inserted"], 1)
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            for statement in (
                "UPDATE nhi_rule_history_diff_card.comparison SET block_count = block_count",
                "DELETE FROM nhi_rule_history_diff_card.comparison",
                "TRUNCATE nhi_rule_history_diff_card.comparison CASCADE",
                "UPDATE nhi_rule_history_diff_card.comparison_block SET adds = adds",
                "DELETE FROM nhi_rule_history_diff_card.comparison_block",
                "TRUNCATE nhi_rule_history_diff_card.comparison_block",
            ):
                with self.subTest(statement), self.assertRaisesRegex(psycopg.errors.RaiseException, "write-once"):
                    connection.execute(statement)
            (cid,) = connection.execute("SELECT comparison_id FROM nhi_rule_history_diff_card.comparison").fetchone()
            block = ("INSERT INTO nhi_rule_history_diff_card.comparison_block VALUES (%s, %s, 'clause', '9.6', "
                     "ARRAY['9.6'], 'p', '修訂', '第1段', '第1段', 0, 0, '[]', '[\"x\"]', '[{}]', %s)")
            # a block added after the comparison committed
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "must commit with all of its blocks"), \
                    connection.transaction():
                connection.execute(block, (cid, 1, "d" * 64))
            parent = ("INSERT INTO nhi_rule_history_diff_card.comparison VALUES (%s, %s, 'x', %s, %s, 0, %s, %s)")
            for label, count, blocks in (("no blocks", 1, 0), ("one block short", 2, 1), ("one block too many", 1, 2)):
                with self.subTest(label), self.assertRaisesRegex(
                        psycopg.errors.RaiseException, "must commit with all of its blocks"), \
                        connection.transaction():
                    new_id = str(uuid.uuid4())
                    connection.execute(parent, (new_id, "e" * 64, "b" * 64, count, str(uuid.uuid4()), "c" * 64))
                    for order in range(blocks):
                        connection.execute(block, (new_id, order, "d" * 64))
            # positive control: the same statements, complete, commit
            with connection.transaction():
                new_id = str(uuid.uuid4())
                connection.execute(parent, (new_id, "f" * 64, "b" * 64, 2, str(uuid.uuid4()), "c" * 64))
                connection.execute(block, (new_id, 0, "d" * 64))
                connection.execute(block, (new_id, 1, "d" * 64))
            self.assertEqual(
                connection.execute("SELECT count(*) FROM nhi_rule_history_diff_card.comparison").fetchone()[0], 2)
        for column, value in (("source_artifact_sha256", "abc"), ("generator_version", ""), ("block_count", 0)):
            with self.subTest(column), psycopg.connect(self.pg.dsn) as connection:
                row = {"cid": str(uuid.uuid4()), "source_artifact_sha256": "a" * 64, "generator_version": "g",
                       "comparison_sha256": "b" * 64, "block_count": 1, "extra_table_count": 0,
                       "work_item_id": str(uuid.uuid4()), "corpus_manifest_sha256": "c" * 64, column: value}
                with self.assertRaises(psycopg.errors.CheckViolation):
                    connection.execute(
                        "INSERT INTO nhi_rule_history_diff_card.comparison (comparison_id, source_artifact_sha256, "
                        "generator_version, comparison_sha256, block_count, extra_table_count, work_item_id, "
                        "corpus_manifest_sha256) VALUES (%(cid)s, %(source_artifact_sha256)s, %(generator_version)s, "
                        "%(comparison_sha256)s, %(block_count)s, %(extra_table_count)s, %(work_item_id)s, "
                        "%(corpus_manifest_sha256)s)", row)

    def test_view_serves_the_newest_comparison_of_each_source_file(self) -> None:
        comparison = pg_store.comparison_for_bundle(self.real[0])
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            pg_store._insert(connection, comparison, "gen/1")
            pg_store._insert(connection, comparison, "gen/2")  # a later transaction: loaded later
            served = {r[0] for r in connection.execute(
                "SELECT generator_version FROM nhi_rule_history_diff_card.v_public_comparison_block").fetchall()}
            self.assertEqual(served, {"gen/2"})
            # same transaction, same loaded_at: the higher generator version wins the tie
            other = pg_store.comparison_for_bundle(self.real[1])
            with connection.transaction():
                pg_store._insert(connection, other, "gen/9")
                pg_store._insert(connection, other, "gen/10")
            tie = connection.execute(
                "SELECT DISTINCT generator_version FROM nhi_rule_history_diff_card.v_public_comparison_block "
                "WHERE source_artifact_sha256 = %s", (other["source_artifact_sha256"],)).fetchall()
            self.assertEqual(tie, [("gen/9",)])  # text order, as the contract says: loaded_at, then generator_version
            counts = connection.execute(
                "SELECT source_artifact_sha256, count(*) FROM nhi_rule_history_diff_card.v_public_comparison_block "
                "GROUP BY 1").fetchall()
            self.assertEqual(dict(counts), {comparison["source_artifact_sha256"]: len(comparison["blocks"]),
                                            other["source_artifact_sha256"]: len(other["blocks"])})

    def test_rollback_drops_only_the_schema_and_forward_applies_again(self) -> None:
        self.load(self.real)
        outside = self.rows("SELECT n.nspname, c.relname FROM pg_class AS c JOIN pg_namespace AS n "
                            "ON n.oid = c.relnamespace WHERE n.nspname <> 'nhi_rule_history_diff_card' "
                            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_toast'")
        self.pg.psql(file=ROLLBACK)
        self.assertEqual(self.rows("SELECT count(*) FROM pg_namespace WHERE nspname = 'nhi_rule_history_diff_card'"),
                         [(0,)])
        self.pg.psql(file=ROLLBACK)  # twice is harmless
        self.assertEqual(self.rows("SELECT count(*) FROM pg_proc WHERE proname IN "
                                   "('check_comparison_complete', 'reject_write_once_change')"), [(0,)])
        self.pg.psql(file=FORWARD)
        self.assertEqual(self.rows("SELECT count(*) FROM nhi_rule_history_diff_card.comparison"), [(0,)])
        self.assertEqual(self.rows("SELECT n.nspname, c.relname FROM pg_class AS c JOIN pg_namespace AS n "
                                   "ON n.oid = c.relnamespace WHERE n.nspname <> 'nhi_rule_history_diff_card' "
                                   "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                                   "AND n.nspname !~ '^pg_toast'"), outside)

    def test_rollback_refuses_to_drop_what_it_did_not_create(self) -> None:
        self.pg.psql(command="CREATE TABLE nhi_rule_history_diff_card.stray (id integer)")
        try:
            refused = self.pg.psql(file=ROLLBACK, check=False)
            self.assertNotEqual(refused.returncode, 0)
            # nothing was dropped: the two tables, the view and the stray table are all still there
            self.assertEqual(self.rows("SELECT count(*) FROM pg_class WHERE relnamespace = "
                                       "'nhi_rule_history_diff_card'::regnamespace AND relkind IN ('r', 'v')"), [(4,)])
        finally:
            self.pg.psql(command="DROP TABLE nhi_rule_history_diff_card.stray")

    # ---- the loader ----

    def test_dry_run_then_load_then_noop(self) -> None:
        bundles = [*self.real, self.nested]
        dry = self.load(bundles, dry_run=True)
        self.assertEqual(dry["counts"], {"registered": 5, "problem": 0, "parsed": 5, "inserted": 0, "present": 0,
                                         "failed": 0, "would_insert": 5})
        self.assertEqual((dry["dry_run"], dry["store_schema"]), (True, "present"))
        self.assertEqual(self.rows("SELECT count(*) FROM nhi_rule_history_diff_card.comparison"), [(0,)])

        first = self.load(bundles)
        self.assertEqual(first["counts"], {"registered": 5, "problem": 0, "parsed": 5, "inserted": 5, "present": 0,
                                           "failed": 0})
        self.assertEqual((first["readback"]["checked"], first["readback"]["mismatches"]), (5, []))
        stored = self.rows("SELECT source_artifact_sha256, comparison_sha256, loaded_at "
                           "FROM nhi_rule_history_diff_card.comparison ORDER BY 1")
        self.assertEqual(len(stored), 5)
        expected = {pg_store.comparison_for_bundle(b)["source_artifact_sha256"]: pg_store.comparison_for_bundle(b)
                    for b in bundles}
        for source_sha, comparison_sha, _ in stored:
            self.assertEqual(comparison_sha, expected[source_sha]["comparison_sha256"])
        served = self.rows("SELECT count(*), count(DISTINCT source_artifact_sha256) "
                           "FROM nhi_rule_history_diff_card.v_public_comparison_block")
        self.assertEqual(served, [(sum(len(c["blocks"]) for c in expected.values()), 5)])

        again = self.load(bundles)
        self.assertEqual(again["counts"], {"registered": 5, "problem": 0, "parsed": 5, "inserted": 0, "present": 5,
                                           "failed": 0})
        self.assertEqual(again["readback"]["mismatches"], [])
        self.assertEqual(self.rows("SELECT source_artifact_sha256, comparison_sha256, loaded_at "
                                   "FROM nhi_rule_history_diff_card.comparison ORDER BY 1"), stored)

        dry_after = self.load(bundles, dry_run=True)
        self.assertEqual((dry_after["counts"]["present"], dry_after["counts"]["would_insert"]), (5, 0))
        self.assertEqual(dry_after["readback"]["checked"], 5)

    def test_receipt_lists_clause_ids_and_codes_of_every_card(self) -> None:
        bundles = [*self.real, self.nested]
        expected = {
            self.real[0].work_item_id: [(0, "clause", "2.1.4.2", ["2.1.4.2"])],
            self.real[1].work_item_id: [(0, "clause", "3.3.28", ["3.3.28"])],
            self.real[2].work_item_id: [(0, "clause", "4.2", ["4.2"]), (1, "attachment", "附表十八之五", ["4.2"])],
            self.real[3].work_item_id: [(0, "clause", "8.1.3", ["8.1.3"])],
            self.nested.work_item_id: [(0, "clause", "9.7", ["9.7", "9.7.1"])],
        }

        def listing(receipt):
            self.assertEqual([c["source_artifact_sha256"] for c in receipt["comparisons"]],
                             sorted(c["source_artifact_sha256"] for c in receipt["comparisons"]))
            return {c["work_item_id"]: [(b["block_order"], b["block_kind"], b["clause_id"], b["clause_codes"])
                                        for b in c["blocks"]] for c in receipt["comparisons"]}

        dry = self.load(bundles, dry_run=True)
        self.assertEqual(listing(dry), expected)
        self.assertEqual({c["outcome"] for c in dry["comparisons"]}, {"would_insert"})
        first = self.load(bundles)
        self.assertEqual(listing(first), expected)
        self.assertEqual({c["outcome"] for c in first["comparisons"]}, {"inserted"})
        again = self.load(bundles)
        self.assertEqual({c["outcome"] for c in again["comparisons"]}, {"present"})
        # the stored view agrees with what the receipt says
        stored = self.rows("SELECT clause_id, clause_codes FROM nhi_rule_history_diff_card.v_public_comparison_block "
                           "ORDER BY source_artifact_sha256, block_order")
        self.assertEqual(sorted(stored), sorted((clause_id, codes) for blocks in expected.values()
                                                for _, _, clause_id, codes in blocks))
        # a comparison the database refuses is listed as failed, with the others unharmed
        with mock.patch.object(pg_store, "_insert", side_effect=psycopg.errors.DataError("refused")):
            refused = self.load(bundles)
        self.assertEqual(refused["counts"]["failed"], 5)
        self.assertEqual({c["outcome"] for c in refused["comparisons"]}, {"failed"})

    def test_nested_clause_and_attachment_codes_are_served(self) -> None:
        self.load([*self.real, self.nested])
        rows = self.rows("SELECT clause_id, block_kind, clause_codes FROM nhi_rule_history_diff_card."
                         "v_public_comparison_block WHERE block_kind = 'attachment' OR cardinality(clause_codes) > 1 "
                         "ORDER BY clause_id")
        self.assertEqual(rows, [("9.7", "clause", ["9.7", "9.7.1"]), ("附表十八之五", "attachment", ["4.2"])])
        self.assertEqual(self.rows("SELECT count(*) FROM nhi_rule_history_diff_card.v_public_comparison_block "
                                   "WHERE '9.7.1' = ANY (clause_codes)"), [(1,)])

    def test_dry_run_opens_only_read_only_sessions(self) -> None:
        self.load(self.real)
        options = []
        real_connect = psycopg.connect

        def recording(*args, **kwargs):
            options.append(kwargs.get("options"))
            return real_connect(*args, **kwargs)

        with mock.patch("psycopg.connect", side_effect=recording):
            receipt = self.load(self.real, dry_run=True)
        self.assertEqual(receipt["counts"]["present"], 4)
        self.assertTrue(options and all(o == "-c default_transaction_read_only=on" for o in options), options)
        options.clear()
        with mock.patch("psycopg.connect", side_effect=recording):
            self.load(self.real)  # control: a real run opens one writing session, then a read-only one
        self.assertEqual(options, [None, "-c default_transaction_read_only=on"])
        # and even a stray write inside a dry run's kind of session is refused by the server
        with psycopg.connect(self.pg.dsn, options="-c default_transaction_read_only=on") as connection, \
                self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute("DELETE FROM nhi_rule_history_diff_card.comparison")

    def test_missing_schema_is_reported_by_a_dry_run_and_refused_by_a_real_run(self) -> None:
        self.pg.psql(file=ROLLBACK)
        dry = self.load(self.real, dry_run=True)
        self.assertEqual((dry["store_schema"], dry["counts"]["would_insert"], dry["counts"]["present"]), ("missing", 4, 0))
        self.assertEqual(dry["readback"]["checked"], 0)
        with self.assertRaisesRegex(pg_store.StoreError, "schema nhi_rule_history_diff_card is missing"):
            self.load(self.real)

    def test_failures_are_listed_and_never_stop_the_others(self) -> None:
        # the overlay reads 修正後 as a revised column; the card parser does not, so its notice is listed as failed
        unsupported = write_bundle(self.root, "1150000011", [(synthetic_odt(
            55, header=("修正後給付規定", "原給付規定")), ODT_MEDIA_TYPE)])
        no_table = write_bundle(self.root, "1150000012", [(b"%PDF-1.4 only a pdf", "application/pdf")])
        problem = QueuedBundle(work_item_id=str(uuid.uuid4()), source_uid="gov_x", bundle_dir=self.root / "gov_x",
                               corpus_manifest_sha256="", first_title_raw="x",
                               problem="queue receipt has no safe bundle path")
        receipt = self.load([problem, unsupported, self.real[0], no_table])
        self.assertEqual(receipt["counts"], {"registered": 4, "problem": 1, "parsed": 1, "inserted": 1, "present": 0,
                                             "failed": 2})
        self.assertEqual(receipt["problems"], [{"work_item_id": problem.work_item_id,
                                                "error": "queue receipt has no safe bundle path"}])
        failed = {item["work_item_id"]: item["error"] for item in receipt["failed"]}
        self.assertEqual(set(failed), {unsupported.work_item_id, no_table.work_item_id})
        self.assertIn("unexpected comparison-table header", failed[unsupported.work_item_id])
        self.assertIn("no official comparison table", failed[no_table.work_item_id])
        self.assertNotIn(str(self.root), json.dumps(receipt, ensure_ascii=False))
        self.assertEqual(self.rows("SELECT count(*) FROM nhi_rule_history_diff_card.comparison"), [(1,)])
        counts = receipt["counts"]  # every registered item is a problem, a parsed card or a listed failure
        self.assertEqual(counts["registered"], counts["problem"] + counts["parsed"] + len(receipt["failed"]))

    def test_two_registered_items_with_one_official_file_store_it_once(self) -> None:
        twin = write_bundle(self.root, "1150000021", [(official_odt("1150056995"), ODT_MEDIA_TYPE)])
        receipt = self.load([self.real[0], twin])
        self.assertEqual((receipt["counts"]["parsed"], receipt["counts"]["inserted"], receipt["counts"]["present"]),
                         (2, 1, 1))
        self.assertEqual(self.rows("SELECT work_item_id::text FROM nhi_rule_history_diff_card.comparison"),
                         [(self.real[0].work_item_id,)])
        self.assertEqual([c["work_item_id"] for c in receipt["comparisons"]], [self.real[0].work_item_id])

    def test_a_generator_that_drifted_without_a_version_bump_is_a_readback_mismatch(self) -> None:
        self.load(self.real[:1])
        real_build = pg_store.comparison_for_bundle

        def drifted(item):
            comparison = real_build(item)
            comparison["blocks"][0]["rows"][1]["indent_em"] += 0.5
            comparison["comparison_sha256"] = pg_store.comparison_sha256(comparison["blocks"])
            return comparison

        with mock.patch.object(pg_store, "comparison_for_bundle", drifted):
            receipt = self.load(self.real[:1])
            self.assertEqual((receipt["counts"]["present"], receipt["counts"]["inserted"]), (1, 0))
            self.assertEqual([m["error"] for m in receipt["readback"]["mismatches"]], [
                "the stored rows differ from the comparison just built (generator changed without a version bump)"])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = pg_store.load_pg(self.pg.dsn, self.root, dry_run=False,
                                          bundle_source=lambda dsn, corpus_root: self.real[:1])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(out.getvalue())["readback"]["mismatches"][0]["source_artifact_sha256"],
                             real_build(self.real[0])["source_artifact_sha256"])
        # the same code under a new version loads beside the old comparison, and the view serves the new one
        with mock.patch.object(pg_store, "comparison_for_bundle", drifted):
            receipt = self.load(self.real[:1], generator_version="nhi-rule-history/diff-cards-pg/1.0.1")
        self.assertEqual((receipt["counts"]["inserted"], receipt["readback"]["mismatches"]), (1, []))
        self.assertEqual(self.rows("SELECT DISTINCT generator_version FROM nhi_rule_history_diff_card."
                                   "v_public_comparison_block"), [("nhi-rule-history/diff-cards-pg/1.0.1",)])
        later = self.load(self.real[:1])  # the default version is no longer the newest: superseded, not a mismatch
        self.assertEqual((later["counts"]["present"], later["readback"]["mismatches"]), (1, []))
        self.assertEqual(len(later["readback"]["superseded"]), 1)

    def test_readback_catches_a_stored_value_that_differs_from_its_own_rows(self) -> None:
        self.load(self.real[:1])
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")  # superuser bypass of the guards
            connection.execute("UPDATE nhi_rule_history_diff_card.comparison_block SET adds = adds + 1")
            connection.execute("UPDATE nhi_rule_history_diff_card.comparison "
                               "SET comparison_sha256 = repeat('0', 64)")
        receipt = self.load(self.real[:1])
        errors = [m["error"] for m in receipt["readback"]["mismatches"]]
        self.assertTrue(any("differs from the hash of its own rows" in e for e in errors), errors)
        self.assertTrue(any("replay invariant" in e for e in errors), errors)

    def test_readback_catches_a_block_whose_rows_hash_no_longer_matches(self) -> None:
        self.load(self.real[:1])
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")  # superuser bypass of the guards
            connection.execute("UPDATE nhi_rule_history_diff_card.comparison_block SET rows_sha256 = repeat('0', 64)")
        receipt = self.load(self.real[:1])
        self.assertEqual([m["error"] for m in receipt["readback"]["mismatches"]],
                         ["block 0: the stored rows_sha256 differs from the hash of its rows"])

    def test_readback_file_reports_ok_and_catches_a_damaged_row(self) -> None:
        def report() -> dict[str, list[str]]:
            sections: dict[str, list[str]] = {}
            current = ""
            for line in self.pg.psql(file=READBACK).stdout.splitlines():
                marker = re.match(r"== (\d+)\.", line)
                if marker:
                    current = marker.group(1)
                    sections[current] = []
                elif current and line.strip() and line not in ("BEGIN", "ROLLBACK"):
                    sections[current].append(line)
            return sections

        empty = report()
        for number in ("1", "2", "3"):
            self.assertTrue(all(line.endswith("|t") for line in empty[number]) and empty[number], (number, empty))
        self.load([*self.real, self.nested])
        loaded = report()
        self.assertEqual(set(loaded), {"1", "2", "3", "4", "5"})
        for number in ("1", "2", "3"):
            self.assertEqual(len(loaded[number]), 1, loaded[number])
            self.assertTrue(loaded[number][0].endswith("|t"), (number, loaded[number]))
        self.assertTrue(loaded["2"][0].startswith("5|5|"), loaded["2"])
        self.assertEqual(len(loaded["5"]), 2, loaded["5"])  # the nested row and the attachment block
        # a stub of the announced layer lights up section 4
        self.pg.psql(command="CREATE SCHEMA nhi_rule_history_announced; "
                             "CREATE VIEW nhi_rule_history_announced.v_public_clause_patch AS "
                             "SELECT v.sha AS source_artifact_sha256, v.code AS clause_code, "
                             "'patch_only'::text AS composition_status FROM (VALUES "
                             f"('{sha(official_odt('1150056995'))}', '2.1.4.2'), ('{'9' * 64}', '2.6.1')) "
                             "AS v(sha, code)")
        try:
            coverage = report()["4"]
            self.assertEqual(coverage[0], "2|1|2|1")
            self.assertIn("999999999999|{2.6.1}|patch_only", coverage[-1])
        finally:
            self.pg.psql(command="DROP SCHEMA nhi_rule_history_announced CASCADE")
        # a row that does not replay its paragraphs is reported by the file, in SQL, not only by the loader
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                "UPDATE nhi_rule_history_diff_card.comparison_block SET rows = jsonb_set(rows, '{0,old_no}', '\"9\"') "
                "WHERE block_order = 0 AND clause_id = '2.1.4.2'")
        damaged = report()
        self.assertTrue(damaged["3"][0].endswith("|f"), damaged["3"])
        self.assertTrue(damaged["1"][0].endswith("|t"), damaged["1"])

    # ---- the command line ----

    def test_cli_requires_a_dsn_and_a_bundle_root(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("NHI_RULE_HISTORY_DSN", "PGSERVICE",
                                                                "NHI_RULE_HISTORY_CORPUS_ROOT")}
        with mock.patch.dict(os.environ, env, clear=True):
            for argv, wanted in ((["load-pg"], "no DSN"), (["load-pg", "--dsn", self.pg.dsn], "no bundle root")):
                err = io.StringIO()
                with self.subTest(argv), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(argv), 2)
                self.assertIn(wanted, err.getvalue())

    def test_cli_prints_the_receipt_and_exits_zero_when_failures_are_only_listed(self) -> None:
        broken = write_bundle(self.root, "1150000031", [(b"%PDF-1.4 only a pdf", "application/pdf")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = pg_store.load_pg(self.pg.dsn, self.root, dry_run=False,
                                      bundle_source=lambda dsn, corpus_root: [self.real[0], broken])
        self.assertEqual(status, 0)
        receipt = json.loads(out.getvalue())
        self.assertEqual(receipt["counts"], {"registered": 2, "problem": 0, "parsed": 1, "inserted": 1, "present": 0,
                                             "failed": 1})
        self.assertEqual([f["work_item_id"] for f in receipt["failed"]], [broken.work_item_id])
        self.assertEqual(receipt["schema"], "nhi-rule-history/diff-card-load-receipt/v1")

    def test_cli_exits_one_without_a_receipt_on_an_infrastructure_error(self) -> None:
        self.pg.psql(file=ROLLBACK)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = pg_store.load_pg(self.pg.dsn, self.root, dry_run=False,
                                      bundle_source=lambda dsn, corpus_root: self.real[:1])
        self.assertEqual((status, out.getvalue()), (1, ""))
        self.assertIn("is missing", err.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):  # the dry run is allowed to see the missing schema
            status = pg_store.load_pg(self.pg.dsn, self.root, dry_run=True,
                                      bundle_source=lambda dsn, corpus_root: self.real[:1])
        self.assertEqual((status, json.loads(out.getvalue())["store_schema"]), (0, "missing"))

    def test_dsn_and_bundle_root_resolution(self) -> None:
        self.assertEqual(pg_store.resolve_dsn("explicit", {"NHI_RULE_HISTORY_DSN": "env", "PGSERVICE": "svc"}), "explicit")
        self.assertEqual(pg_store.resolve_dsn(None, {"NHI_RULE_HISTORY_DSN": "env", "PGSERVICE": "svc"}), "env")
        self.assertEqual(pg_store.resolve_dsn(None, {"PGSERVICE": "svc"}), "service=svc")
        self.assertIsNone(pg_store.resolve_dsn(None, {}))
        self.assertEqual(pg_store.resolve_corpus_root(None, {"NHI_RULE_HISTORY_CORPUS_ROOT": "/x"}), Path("/x"))
        self.assertEqual(pg_store.resolve_corpus_root(Path("/y"), {"NHI_RULE_HISTORY_CORPUS_ROOT": "/x"}), Path("/y"))
        self.assertIsNone(pg_store.resolve_corpus_root(None, {}))


class PublicFilesTest(unittest.TestCase):
    def test_files_carry_no_private_host_or_path(self) -> None:
        strict = (FORWARD, ROLLBACK, READBACK, ROOT / "tools" / "diff_cards" / "pg_store.py")
        loose = (
            ROOT / "docs" / "diff-cards.md", ROOT / "docs" / "reader-experience.md",
            ROOT / "tests" / "fixtures" / "diff_cards" / "nhi-2026-10-01-clauses.json",
        )
        for pattern, paths in ((PRIVATE_MARKERS, strict), (PRIVATE_PATHS, loose)):
            for path in paths:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    self.assertIsNone(pattern.search(line), f"{path.name}:{number}: {line}")
        for path in (*strict, *loose):  # the production database's service name stays in the private lane
            self.assertNotIn("vault" + "_main", path.read_text(encoding="utf-8"), path.name)

    def test_forward_file_creates_only_the_designed_objects(self) -> None:
        text = FORWARD.read_text(encoding="utf-8")
        statements = re.findall(
            r"^(CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\s+(CONSTRAINT TRIGGER|[A-Z]+)",
            text, re.MULTILINE)
        counts = {}
        for verb, noun in statements:
            counts[(verb, noun)] = counts.get((verb, noun), 0) + 1
        self.assertEqual(counts, {
            ("CREATE", "SCHEMA"): 1, ("REVOKE", "ALL"): 1, ("CREATE", "TABLE"): 2, ("CREATE", "FUNCTION"): 2,
            ("CREATE", "TRIGGER"): 4, ("CREATE", "CONSTRAINT TRIGGER"): 2, ("CREATE", "VIEW"): 1,
        })
        self.assertEqual(text.count("security_invoker = true"), 1)


if __name__ == "__main__":
    unittest.main()
