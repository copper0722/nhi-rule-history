"""Live composition, sealing, activation and rollback on a disposable cluster.

The disposable database is built from the project's own forward migrations.
A small base release (one patch-only notice) and a current publication are
seeded directly; the four fixture notices, and synthetic notices built with
the parser's own table grammar, are then composed into new runs.  The 2.6.1
re-binding path needs the official 2.6.1 ODT and is exercised by the operator
rehearsal, not here.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import threading
import types
import unittest
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import psycopg

from nhi_rule_history import announced_dyslipidemia as dyslipidemia
from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.announced_notice import (
    CORPUS_BUNDLE_SCHEMA,
    ODT_MEDIA_TYPE,
    NoticeAttachment,
    NoticeBundle,
    _cell_items,
    parse_comparison_document,
    parse_notice,
    read_notice_bundle,
    read_odt_document,
    sha256_text,
)
from nhi_rule_history.announced_release import (
    GLOBAL_LOCK_KEY,
    LOADER_VERSION,
    PLACEHOLDER_RUN_ID,
    REPRODUCED_PATCH_KEYS,
    _receipt_bundle,
    AnnouncedReleaseError,
    CarriedNotice,
    SEALED_COUNT_TABLES,
    _served_differences,
    _supersede_refusal,
    activate_overlay_release,
    compose_overlay_release,
    load_overlay_release,
    notice_rows,
    prepare_overlay_release,
    read_base_chain,
    rollback_overlay_release,
    _connect,
    _jsonable,
)
from nhi_rule_history.pg.common import (
    json_text,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)
from tests import test_announced_notice as notice_fixture
from tests import test_update_queue_recovery_v2 as recovery_fixture
from tools import load_announced_notices as cli
from tools.build_announced_notice_fixture import fixture_odt


MIGRATIONS = Path(__file__).resolve().parents[1] / "pg" / "migrations"
BASE_RUN = "11111111-1111-5111-8111-111111111111"
PUBLICATION_RUN = "22222222-2222-5222-8222-222222222222"
BASE_NOTICE = "33333333-3333-5333-8333-333333333333"
BASE_EFFECT = "44444444-4444-5444-8444-444444444444"
BASE_PATCH = "55555555-5555-5555-8555-555555555555"
BASE_REFERENCE = "健保審字第1159999999號"
SERVED = {
    "2.1.4.2": "2.1.4.2.Rivaroxaban(如Xarelto)（101/1/1）\n限用於",
    "3.3.28": "3.3.28.Migalastat hydrochloride (如Galafold)：(112/8/1)",
    "8.1.3": "8.1.3.高單位免疫球蛋白(111/2/1)：",
    "4.2": "4.2.血液代用製劑及血液成分製劑 blood substituents and blood components",
    "9.9": "9.9.Fixture：(100/1/1)",
    "9.2": "9.2.Carboplatin：(100/1/1)\n限用於卵巢癌第一線。",
    "9.5": "9.5.Paclitaxel成分劑：(100/1/1)\n限用於轉移性乳癌。",
    "2.6.1": "2.6.1.降血脂藥物：(100/1/1)",
}
TODAY = date(2026, 9, 28)


def _hashed(row: dict) -> dict:
    out = dict(row)
    out["source_row_sha256"] = row_sha256(out, derived_key="source_row_sha256")
    return out


def _apply_migrations(pg: recovery_fixture.DisposablePostgres) -> None:
    pending = sorted(
        path for path in MIGRATIONS.glob("*.sql") if "rollback" not in path.name
    )
    while pending:
        applied = [
            path
            for path in list(pending)
            if pg.psql(file=path, check=False).returncode == 0
        ]
        if not applied:
            break
        pending = [path for path in pending if path not in applied]
    required = {
        "2026-07-29_nhi_rule_history_current_publication_v18.sql",
        "2026-07-29_nhi_rule_history_announced_decision_v21.sql",
        "2026-07-29_nhi_rule_history_announced_release_gate_v22.sql",
        "2026-07-29_nhi_rule_history_announced_composite_v23.sql",
        "2026-07-29_nhi_rule_history_announced_version_projection_v24.sql",
        "2026-07-29_nhi_rule_history_clause_components_v25.sql",
        "2026-07-30_nhi_rule_history_clause_reader_profile_v26.sql",
        "2026-09-28_nhi_rule_history_announced_resolution_guard_v27.sql",
    }
    missing = required & {path.name for path in pending}
    if missing:
        raise AssertionError(f"migrations did not apply: {sorted(missing)}")


def _seed(dsn: str) -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with _connect(dsn, read_only=False) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            """
            INSERT INTO nhi_rule_history_publication.publication_run (
              run_id, source_parse_run_id, source_acquisition_run_id,
              source_policy_id, state, loader_version, extractor_version,
              version_count_policy, authority_page_url,
              whole_split_parity_status, input_fingerprint, expected_counts,
              verified_counts, table_fingerprints, output_fingerprint,
              sealed_fingerprint, started_at, sealed_at
            ) VALUES (
              %s, %s, %s, 'fixture-policy', 'sealed', 'fixture', 'fixture',
              'distinct_valid_current_text_roc_dates_minimum_one/v1',
              'https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html',
              'parity_passed', %s, '{}', '{}', '{}', %s, %s, %s, %s
            )
            """,
            (
                PUBLICATION_RUN, str(uuid.uuid4()), str(uuid.uuid4()),
                "a" * 64, "b" * 64, "c" * 64, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO nhi_rule_history_publication.publication_activation"
            " (run_id) VALUES (%s)",
            (PUBLICATION_RUN,),
        )
        for code, text in SERVED.items():
            connection.execute(
                """
                INSERT INTO nhi_rule_history_publication.current_clause (
                  run_id, clause_code, chapter_number, source_designation,
                  code_origin, display_title, source_acquisition_run_id,
                  source_resource_id, source_url, source_label,
                  source_artifact_sha256, source_span, raw_text,
                  raw_text_sha256, normalized_text, normalized_text_sha256,
                  comparison_text, comparison_sha256,
                  valid_distinct_roc_date_count, expected_version_count,
                  reconstructed_version_count, missing_version_count,
                  annotation_count_underflows_reconstructed, inventory_status,
                  source_row_sha256
                ) VALUES (
                  %s, %s, %s, %s, 'official_source', %s, %s, 'fixture',
                  'https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html',
                  'fixture', %s, '{"fixture": true}', %s, %s, %s, %s, %s, %s,
                  0, 1, 1, 0, false, 'complete_under_annotation_policy', %s
                )
                """,
                (
                    PUBLICATION_RUN, code, int(code.split(".")[0]), code, code,
                    str(uuid.uuid4()), "d" * 64, text, sha256_text(text),
                    text, sha256_text(text), text, sha256_text(text), "e" * 64,
                ),
            )
        rows = {
            "notice_event": [
                _hashed(
                    {
                        "run_id": BASE_RUN,
                        "notice_id": BASE_NOTICE,
                        "reference_number": BASE_REFERENCE,
                        "title": "公告修訂9.9.給付規定。",
                        "official_url": "https://www.nhi.gov.tw/ch/cp-1-1-3258-1.html",
                        "published_on": "2026-08-01",
                        "effective_on": "2026-09-01",
                        "civil_timezone": "Asia/Taipei",
                        "source_artifact_sha256": "f" * 64,
                        "source_artifact_filename": "attachment-000.odt",
                        "source_exact": True,
                        "event_scope_complete": True,
                        "unresolved_scope": [],
                    }
                )
            ],
            "notice_effect": [
                _hashed(
                    {
                        "run_id": BASE_RUN,
                        "effect_id": BASE_EFFECT,
                        "notice_id": BASE_NOTICE,
                        "effect_type": "clause_amendment",
                        "clause_code": "9.9",
                        "projection_status": "projected_source_exact_patch",
                        "scope_note": "fixture base patch",
                    }
                )
            ],
            "clause_patch": [
                _hashed(
                    {
                        "run_id": BASE_RUN,
                        "patch_id": BASE_PATCH,
                        "effect_id": BASE_EFFECT,
                        "clause_code": "9.9",
                        "predecessor_text_sha256": sha256_text(SERVED["9.9"]),
                        "effective_from": "2026-09-01",
                        "effective_until": None,
                        "resolution_state": "verified_scheduled",
                        "source_exact_patch_text": "9.9.Fixture：(115/9/1)",
                        "source_exact_patch_sha256": sha256_text(
                            "9.9.Fixture：(115/9/1)"
                        ),
                        "omitted_text_present": False,
                        "composition_status": "patch_only",
                        "comparison_sha256": "1" * 64,
                        "component_manifest_sha256": "2" * 64,
                        "partial_event_projection": False,
                        "unprocessed_event_scope": [],
                        "public_note": "fixture base patch",
                    }
                )
            ],
        }
        counts = {name: len(rows.get(name, ())) for name in SEALED_COUNT_TABLES}
        fingerprints = {
            name: row_set_fingerprint(row["source_row_sha256"] for row in value)
            for name, value in rows.items()
        }
        connection.execute(
            """
            INSERT INTO nhi_rule_history_announced.release_run (
              run_id, state, loader_version, evaluator_version,
              source_artifact_sha256, input_fingerprint, expected_counts,
              verified_counts, table_fingerprints, output_fingerprint,
              sealed_fingerprint, started_at, sealed_at
            ) VALUES (%s,'sealed','fixture','fixture',%s,%s,%s::jsonb,
                      %s::jsonb,%s::jsonb,%s,%s,%s,%s)
            """,
            (
                BASE_RUN, "f" * 64, "3" * 64, json_text(counts),
                json_text(counts), json_text(fingerprints),
                object_fingerprint(fingerprints), "4" * 64, now, now,
            ),
        )
        for table in ("notice_event", "notice_effect", "clause_patch"):
            for row in rows[table]:
                columns = list(row)
                connection.execute(
                    f"INSERT INTO nhi_rule_history_announced.{table} "
                    f"({','.join(columns)}) VALUES ("
                    + ",".join(
                        "%s::jsonb" if isinstance(row[c], (list, dict)) else "%s"
                        for c in columns
                    )
                    + ")",
                    [
                        json_text(row[c]) if isinstance(row[c], (list, dict)) else row[c]
                        for c in columns
                    ],
                )
        connection.execute(
            """
            INSERT INTO nhi_rule_history_announced.patch_resolution_event (
              run_id, patch_id, resolution_state, reason, evidence
            ) VALUES (%s,%s,'reconciled','fixture reconciliation','{"k":1}')
            """,
            (BASE_RUN, BASE_PATCH),
        )
        connection.execute(
            """
            INSERT INTO nhi_rule_history_announced.release_control_event (
              run_id, action, reason, evidence
            ) VALUES (%s,'activate','fixture base','{}')
            """,
            (BASE_RUN,),
        )
        connection.commit()


MANIFEST = notice_fixture.MANIFEST


def _fixture_notices() -> list:
    helper = notice_fixture.OfficialFixtureTest()
    return [helper._parse(notice)[1] for notice in MANIFEST["notices"]]


def _synthetic_notice(
    reference: str,
    rows: list[tuple[list[str], list[str]]],
    *,
    effective: str = "（自115年10月1日生效）",
    declared_sha256: str | None = None,
    header: tuple[str, str] | None = None,
    preamble: str = "",
):
    """Parse a comparison table written in the parser's own table grammar.

    ``declared_sha256`` names the artifact the text claims to come from, so a
    re-parse of the same source with different text can be simulated.
    ``header`` and ``preamble`` (paragraphs before the table) vary the
    document around the same rows.
    """

    content = notice_fixture._comparison(
        rows, effective=effective, **({"header": header} if header else {})
    )
    if preamble:
        content = content.replace(
            b"<office:text>", b"<office:text>" + preamble.encode("utf-8"), 1
        )
    payload = fixture_odt(content)
    sha = declared_sha256 or hashlib.sha256(payload).hexdigest()
    attachment = NoticeAttachment(
        declared_sequence=0,
        file_name="attachment-000.odt",
        media_type=ODT_MEDIA_TYPE,
        sha256=sha,
        byte_size=len(payload),
        path=Path("attachment-000.odt"),
    )
    bundle = NoticeBundle(
        bundle_dir=Path(f"gov_{reference}"),
        source_uid=f"gov_{reference}",
        reference_number=reference,
        title="公告修訂藥品給付規定。",
        official_url="https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
        published_on="2026-09-15",
        manifest_sha256="0" * 64,
        attachments=(attachment,),
        announcement_items=(),
        raw_md_blocks={},
    )
    document = read_odt_document(payload, artifact_sha256=sha)
    bundle = replace(
        bundle,
        raw_md_blocks={
            attachment.file_name: tuple(
                (item.block_id, item.block_text_sha256)
                for item in document.paragraphs
            )
        },
    )
    return parse_comparison_document(bundle, attachment, document)


def _rendering(notice, *codes: str) -> dict:
    """A rendering that draws every cell and the body text as the parser reads it.

    When clauses are named, the revised paragraphs of every other clause are
    drawn with other text, so only the named clauses match; ``"-"`` names
    none, so every clause is held back while the notice itself is confirmed.
    """

    hidden = {
        item.document_order
        for clause in notice.clauses
        if codes and clause.clause_code not in codes
        for item in clause.revised
    }
    document = notice.document
    flow = [
        (
            item.document_order,
            {
                "text": item.text,
                "label": item.generated_label or "",
                "separator": (
                    item.numbering.separator if item.generated_label else None
                ),
                "hidden": False,
                "bullet": None,
                "transform": False,
            },
        )
        for item in document.paragraphs
        if item.top_table_index is None
        and item.document_order not in document.detached_orders
    ]
    for table_index in document.top_tables:
        orders = [
            item.document_order
            for item in document.paragraphs
            if item.top_table_index == table_index
        ]
        flow.append((min(orders) if orders else float("inf"), {"table": True}))
    return {
        "rendering_version": "test",
        "flow": [entry for _, entry in sorted(flow, key=lambda pair: pair[0])],
        "tables": [
            [
                [
                    [
                        {
                            "text": (
                                "與對照表無關的另一份文字"
                                if item.document_order in hidden
                                else item.text
                            ),
                            "label": item.generated_label or "",
                            "separator": (
                                item.numbering.separator
                                if item.generated_label
                                else None
                            ),
                            "hidden": False,
                            "bullet": None,
                            "transform": False,
                        }
                        for item in _cell_items(
                            notice.document.paragraphs,
                            table_index,
                            cell.row_index,
                            cell.cell_index,
                        )
                        if not item.nested
                    ]
                    for cell in row
                ]
                for row in grid
            ]
            for table_index, grid in sorted(notice.document.top_tables.items())
        ],
    }


# A rendering of some other document: no table lines up with the notice.
FOREIGN_RENDERING = {"rendering_version": "test", "tables": []}


def _public_patches(dsn: str) -> dict[str, dict]:
    with _connect(dsn, read_only=True) as connection:
        return {
            row["clause_code"]: dict(row)
            for row in connection.execute(
                """
                SELECT clause_code, patch_id, source_exact_patch_text,
                       source_exact_patch_sha256, effective_from,
                       current_resolution_state
                FROM nhi_rule_history_announced.v_public_clause_patch
                """
            ).fetchall()
        }


def _notice_rows(release, reference: str) -> dict[str, list[dict]]:
    """One notice's rows in a composed release, without run-bound columns."""

    def strip(row: dict) -> dict:
        return {
            key: value
            for key, value in row.items()
            if key not in {"run_id", "source_row_sha256"}
        }

    event = next(
        row
        for row in release.rows["notice_event"]
        if row["reference_number"] == reference
    )
    effects = [
        row
        for row in release.rows["notice_effect"]
        if row["notice_id"] == event["notice_id"]
    ]
    effect_ids = {row["effect_id"] for row in effects}
    patches = [
        row
        for row in release.rows["clause_patch"]
        if row["effect_id"] in effect_ids
    ]
    return {
        "notice_event": [strip(event)],
        "notice_effect": sorted(map(strip, effects), key=json_text),
        "clause_patch": sorted(map(strip, patches), key=json_text),
    }


class OverlayReleaseLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg)
        _seed(cls.pg.dsn)
        cls.notices = _fixture_notices()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def _served(self) -> list[tuple[str, str]]:
        with _connect(self.pg.dsn, read_only=True) as connection:
            return [
                (row["clause_code"], row["composition_status"])
                for row in connection.execute(
                    """
                    SELECT clause_code, composition_status
                    FROM nhi_rule_history_announced.v_public_clause_patch
                    ORDER BY effective_from, clause_code
                    """
                ).fetchall()
            ]

    def test_compose_load_activate_and_roll_back(self) -> None:
        release = prepare_overlay_release(
            self.pg.dsn, self.notices, base_run_id=BASE_RUN,
            today=date(2026, 9, 28),
        )
        again = prepare_overlay_release(
            self.pg.dsn, list(reversed(self.notices)), base_run_id=BASE_RUN,
            today=date(2026, 9, 28),
        )
        self.assertEqual(release.run_id, again.run_id)
        self.assertEqual(release.sealed_fingerprint, again.sealed_fingerprint)
        self.assertEqual(release.all_counts["notice_event"], 5)
        self.assertEqual(release.all_counts["clause_patch"], 5)
        patches = {row["clause_code"]: row for row in release.rows["clause_patch"]}
        pinned = {
            clause["clause_code"]: clause
            for notice in MANIFEST["notices"]
            for clause in notice["expected"]["clauses"]
        }
        for code, clause in pinned.items():
            self.assertEqual(
                patches[code]["source_exact_patch_sha256"],
                clause["revised_text_sha256"],
            )
            self.assertEqual(patches[code]["composition_status"], "patch_only")
            self.assertEqual(
                patches[code]["predecessor_text_sha256"],
                sha256_text(SERVED[code]),
            )
            self.assertEqual(patches[code]["effective_from"], "2026-10-01")
        carried = patches["9.9"]
        with _connect(self.pg.dsn, read_only=True) as connection:
            base = read_base_chain(connection, BASE_RUN)
        original = base.rows["clause_patch"][0]
        self.assertEqual(
            {k: v for k, v in carried.items() if k not in {"run_id", "source_row_sha256"}},
            {k: v for k, v in original.items() if k not in {"run_id", "source_row_sha256"}},
        )
        self.assertEqual(self._served(), [("9.9", "patch_only")])

        # The stored seal must have exactly the 2.6.1 loader's shape: the
        # subscriber sync re-verifies the active run with dict equality over
        # SEALED_COUNT_TABLES every tick (2026-09-28 finding: carrying the
        # legacy document tables into table_fingerprints broke that check).
        self.assertEqual(set(release.table_fingerprints), set(SEALED_COUNT_TABLES))
        self.assertEqual(set(release.expected_counts), set(SEALED_COUNT_TABLES))
        self.assertEqual(
            release.output_fingerprint,
            object_fingerprint(
                {
                    "counts": dict(release.expected_counts),
                    "table_fingerprints": dict(release.table_fingerprints),
                }
            ),
        )

        receipt = load_overlay_release(self.pg.dsn, release)
        self.assertFalse(receipt["replayed"])
        with _connect(self.pg.dsn, read_only=True) as connection:
            stored = connection.execute(
                """
                SELECT expected_counts, verified_counts, table_fingerprints
                FROM nhi_rule_history_announced.release_run WHERE run_id=%s
                """,
                (release.run_id,),
            ).fetchone()
        self.assertEqual(stored["table_fingerprints"], dict(release.table_fingerprints))
        self.assertEqual(stored["expected_counts"], dict(release.expected_counts))
        self.assertEqual(stored["verified_counts"], dict(release.expected_counts))
        # The subscriber sync's own check: its core receipt replay must pass.
        # This fixture has no 2.6.1 clause normalization, so the check may
        # only stop at that later step, never at the sealed receipt.
        with self.assertRaises(dyslipidemia.AnnouncedDyslipidemiaError) as caught:
            dyslipidemia.verify_announced_material(
                release.run_id, conninfo=self.pg.dsn
            )
        self.assertNotIn("does not replay", str(caught.exception))
        self.assertIn("normalization", str(caught.exception))
        self.assertEqual(receipt["resolved_patch_count"], 5)
        # Loading changes nothing that is served.
        self.assertEqual(self._served(), [("9.9", "patch_only")])
        self.assertTrue(load_overlay_release(self.pg.dsn, release)["replayed"])

        with self.assertRaises(AnnouncedReleaseError):
            activate_overlay_release(
                self.pg.dsn,
                run_id=release.run_id,
                expected_sealed_fingerprint="0" * 64,
                expected_base_run_id=BASE_RUN,
            )
        activated = activate_overlay_release(
            self.pg.dsn,
            run_id=release.run_id,
            expected_sealed_fingerprint=release.sealed_fingerprint,
            expected_base_run_id=BASE_RUN,
        )
        self.assertEqual(activated["public_patch_count"], 5)
        self.assertEqual(activated["previous"]["release_run_id"], BASE_RUN)
        served = dict(self._served())
        self.assertEqual(set(served), {"9.9", "2.1.4.2", "3.3.28", "8.1.3", "4.2"})
        with _connect(self.pg.dsn, read_only=True) as connection:
            carried_resolution = connection.execute(
                """
                SELECT current_resolution_state, resolution_reason,
                       resolution_evidence
                FROM nhi_rule_history_announced.v_public_clause_patch
                WHERE clause_code='9.9'
                """
            ).fetchone()
        self.assertEqual(carried_resolution["current_resolution_state"], "reconciled")
        self.assertEqual(carried_resolution["resolution_reason"], "fixture reconciliation")
        self.assertEqual(carried_resolution["resolution_evidence"]["k"], 1)
        self.assertEqual(
            carried_resolution["resolution_evidence"]["carried_forward"]["run_id"],
            BASE_RUN,
        )

        restored = rollback_overlay_release(self.pg.dsn, from_run_id=release.run_id)
        self.assertEqual(restored["served"]["release_run_id"], BASE_RUN)
        self.assertEqual(self._served(), [("9.9", "patch_only")])

    def test_fail_closed_inputs(self) -> None:
        with self.assertRaisesRegex(AnnouncedReleaseError, "expected base"):
            prepare_overlay_release(
                self.pg.dsn, self.notices, base_run_id=str(uuid.uuid4())
            )
        # A base-chain invariant is run-level: it aborts the whole batch.
        with self.assertRaisesRegex(AnnouncedReleaseError, "expected base"):
            compose_overlay_release(
                self.pg.dsn, self.notices, base_run_id=str(uuid.uuid4())
            )
        with self.assertRaisesRegex(AnnouncedReleaseError, "office rendering"):
            prepare_overlay_release(
                self.pg.dsn, self.notices, require_official_rendering=True
            )
        with self.assertRaisesRegex(AnnouncedReleaseError, "listed twice"):
            prepare_overlay_release(
                self.pg.dsn, [self.notices[0], self.notices[0]]
            )
        # The same inputs isolate per notice when composed leniently.
        unrendered = compose_overlay_release(
            self.pg.dsn,
            self.notices,
            official_renderings={
                self.notices[0].bundle.reference_number: _rendering(
                    self.notices[0]
                )
            },
            require_official_rendering=True,
            today=TODAY,
        )
        self.assertEqual(
            sorted(item["stage"] for item in unrendered.failures),
            ["rendering"] * 3,
        )
        self.assertEqual(
            [item.notice.bundle.reference_number for item in unrendered.release.notices],
            [self.notices[0].bundle.reference_number],
        )
        duplicated = compose_overlay_release(
            self.pg.dsn, [self.notices[0], self.notices[0]], today=TODAY
        )
        self.assertIsNone(duplicated.release)
        self.assertEqual(
            [item["stage"] for item in duplicated.failures], ["input", "input"]
        )
        self.assertEqual(duplicated.status, "no_change_with_holds")

    def _by_reference(self, number: str):
        return next(
            notice
            for notice in self.notices
            if number in notice.bundle.reference_number
        )

    def test_notice_whose_body_text_is_not_confirmed_fails_alone(self) -> None:
        # 2026-09-28 finding O1: the effective date LibreOffice draws was
        # never compared with the one the parser read.
        target = self._by_reference("1150672509")
        reference = target.bundle.reference_number
        statement = target.tables[0].effective_statement.text
        other_date = _rendering(target)
        next(
            entry for entry in other_date["flow"] if entry.get("text") == statement
        )["text"] = "（自115年11月1日生效）"
        renderings = {
            notice.bundle.reference_number: _rendering(notice)
            for notice in self.notices
        }
        for label, rendering, reason in (
            ("another date drawn", other_date, "drawn with other text"),
            ("rule 1.1.0 shape", FOREIGN_RENDERING, "does not report the body text"),
        ):
            with self.subTest(label):
                composition = compose_overlay_release(
                    self.pg.dsn,
                    self.notices,
                    base_run_id=BASE_RUN,
                    official_renderings={**renderings, reference: rendering},
                    require_official_rendering=True,
                    today=TODAY,
                )
                self.assertEqual(
                    [
                        (item["reference_number"], item["stage"])
                        for item in composition.failures
                    ],
                    [(reference, "rendering")],
                )
                self.assertIn(reason, composition.failures[0]["error"])
                self.assertEqual(
                    sorted(
                        item.notice.bundle.reference_number
                        for item in composition.release.notices
                    ),
                    sorted(set(renderings) - {reference}),
                )
                self.assertEqual(composition.status, "passed_with_holds")
        # Confusable negative: the faithful rendering composes every notice.
        clean = compose_overlay_release(
            self.pg.dsn,
            self.notices,
            base_run_id=BASE_RUN,
            official_renderings=renderings,
            require_official_rendering=True,
            today=TODAY,
        )
        self.assertEqual(clean.failures, ())
        self.assertEqual(clean.status, "passed")

    def test_held_back_notice_enters_the_run_with_pending_effects(self) -> None:
        # 2026-09-28 finding M1: a notice whose every clause was held back
        # was dropped from the run, so served data showed no 10-01 change.
        held = self._by_reference("1150672509")
        reference = held.bundle.reference_number
        mismatch = {reference: _rendering(held, "-")}
        composition = compose_overlay_release(
            self.pg.dsn,
            self.notices,
            base_run_id=BASE_RUN,
            official_renderings=mismatch,
            today=TODAY,
        )
        rows = _notice_rows(composition.release, reference)
        event = rows["notice_event"][0]
        self.assertFalse(event["event_scope_complete"])
        self.assertEqual(event["effective_on"], "2026-10-01")
        self.assertEqual(
            event["unresolved_scope"],
            [
                {
                    "effect_type": "clause_amendment",
                    "clause_code": "8.1.3",
                    "blocked_reason": "official_rendering_mismatch",
                }
            ],
        )
        self.assertEqual(
            [
                (row["clause_code"], row["projection_status"])
                for row in rows["notice_effect"]
            ],
            [("8.1.3", "pending_projection")],
        )
        note = rows["notice_effect"][0]["scope_note"]
        self.assertIn("rendering of its own revised cell", note)
        self.assertEqual(rows["clause_patch"], [])
        self.assertEqual(
            composition.blocked_clauses,
            (
                {
                    "reference_number": reference,
                    "clause_code": "8.1.3",
                    "effective_on": "2026-10-01",
                    "reason": "official_rendering_mismatch",
                    "scope_note": note,
                    "origin": "new",
                },
            ),
        )
        self.assertEqual(composition.failures, ())
        self.assertEqual(composition.status, "passed_with_holds")
        # Confusable negative: the same batch with nothing held back is green.
        clean = compose_overlay_release(
            self.pg.dsn, self.notices, base_run_id=BASE_RUN, today=TODAY
        )
        self.assertEqual(clean.status, "passed")
        self.assertEqual(clean.blocked_clauses, ())
        self.assertEqual(
            [
                row["clause_code"]
                for row in _notice_rows(clean.release, reference)["clause_patch"]
            ],
            ["8.1.3"],
        )
        # A batch of held-back notices only still seals a run that says so.
        only = compose_overlay_release(
            self.pg.dsn,
            [held],
            base_run_id=BASE_RUN,
            official_renderings=mismatch,
            today=TODAY,
        )
        self.assertEqual(only.release.all_counts["notice_event"], 2)
        self.assertEqual(only.release.all_counts["clause_patch"], 1)
        receipt = load_overlay_release(self.pg.dsn, only.release)
        self.assertFalse(receipt["replayed"])
        with _connect(self.pg.dsn, read_only=True) as connection:
            stored = connection.execute(
                """
                SELECT effect.clause_code, effect.projection_status,
                       notice.event_scope_complete, notice.effective_on
                FROM nhi_rule_history_announced.notice_effect effect
                JOIN nhi_rule_history_announced.notice_event notice
                  USING (run_id, notice_id)
                WHERE run_id=%s AND notice.reference_number=%s
                """,
                (only.release.run_id, reference),
            ).fetchall()
        self.assertEqual(
            [
                (
                    row["clause_code"],
                    row["projection_status"],
                    row["event_scope_complete"],
                    row["effective_on"],
                )
                for row in stored
            ],
            [("8.1.3", "pending_projection", False, date(2026, 10, 1))],
        )
        # A notice that states nothing fails instead of entering empty.
        empty = replace(held, clauses=(), other_effects=())
        nothing = compose_overlay_release(
            self.pg.dsn, [empty], base_run_id=BASE_RUN, today=TODAY
        )
        self.assertIsNone(nothing.release)
        self.assertEqual(
            [(item["reference_number"], item["stage"]) for item in nothing.failures],
            [(reference, "parse")],
        )
        self.assertEqual(nothing.status, "no_change_with_holds")

    def test_binding_failure_is_isolated_and_the_rest_loads(self) -> None:
        # 2026-09-28 finding M3: one binding error aborted the whole batch.
        source = self._by_reference("1150672509")
        broken = replace(
            source,
            clauses=tuple(
                replace(clause, clause_code="8.1.99") for clause in source.clauses
            ),
        )
        others = [
            notice
            for notice in self.notices
            if notice.bundle.reference_number != source.bundle.reference_number
        ]
        composition = compose_overlay_release(
            self.pg.dsn, [broken, *others], base_run_id=BASE_RUN, today=TODAY
        )
        self.assertEqual(
            [
                (item["reference_number"], item["stage"], item["error"])
                for item in composition.failures
            ],
            [
                (
                    source.bundle.reference_number,
                    "bind",
                    "8.1.99 has an original column but no served clause to bind",
                )
            ],
        )
        self.assertEqual(composition.status, "passed_with_holds")
        release = composition.release
        self.assertEqual(
            sorted(row["reference_number"] for row in release.rows["notice_event"]),
            sorted(
                [BASE_REFERENCE, *(n.bundle.reference_number for n in others)]
            ),
        )
        self.assertEqual(
            sorted(row["clause_code"] for row in release.rows["clause_patch"]),
            ["2.1.4.2", "3.3.28", "4.2", "9.9"],
        )
        # The strict entry point keeps failing closed on the same batch.
        with self.assertRaisesRegex(AnnouncedReleaseError, "no served clause"):
            prepare_overlay_release(
                self.pg.dsn, [broken, *others], base_run_id=BASE_RUN
            )
        receipt = load_overlay_release(self.pg.dsn, release)
        self.assertFalse(receipt["replayed"])
        self.assertEqual(receipt["resolved_patch_count"], 4)
        with _connect(self.pg.dsn, read_only=True) as connection:
            loaded = {
                row["reference_number"]
                for row in connection.execute(
                    """
                    SELECT reference_number
                    FROM nhi_rule_history_announced.notice_event
                    WHERE run_id=%s
                    """,
                    (release.run_id,),
                ).fetchall()
            }
        self.assertEqual(len(loaded), 4)
        self.assertNotIn(source.bundle.reference_number, loaded)
        # Confusable negative: a clause marked new but already served.
        rivaroxaban = self._by_reference("1150056995")
        marked_new = replace(
            rivaroxaban,
            clauses=tuple(
                replace(clause, original_is_none=True)
                for clause in rivaroxaban.clauses
            ),
        )
        galafold = self._by_reference("1150057033")
        second = compose_overlay_release(
            self.pg.dsn, [marked_new, galafold], base_run_id=BASE_RUN, today=TODAY
        )
        self.assertEqual(
            [(item["stage"], item["error"]) for item in second.failures],
            [("bind", "2.1.4.2 is marked new but is already served")],
        )
        self.assertEqual(
            sorted(row["clause_code"] for row in second.release.rows["clause_patch"]),
            ["3.3.28", "9.9"],
        )

    def test_same_clause_and_date_twice_is_isolated(self) -> None:
        rivaroxaban = self._by_reference("1150056995")
        twin = replace(
            rivaroxaban,
            bundle=replace(
                rivaroxaban.bundle, reference_number="健保審字第1159999998號"
            ),
        )
        galafold = self._by_reference("1150057033")
        composition = compose_overlay_release(
            self.pg.dsn,
            [rivaroxaban, twin, galafold],
            base_run_id=BASE_RUN,
            today=TODAY,
        )
        self.assertEqual(
            sorted(
                (item["reference_number"], item["stage"], item["error"])
                for item in composition.failures
            ),
            sorted(
                (
                    notice.bundle.reference_number,
                    "bind",
                    "another patch amends the same clause on the same date: "
                    "2.1.4.2 effective 2026-10-01",
                )
                for notice in (rivaroxaban, twin)
            ),
        )
        self.assertEqual(
            sorted(row["clause_code"] for row in composition.release.rows["clause_patch"]),
            ["3.3.28", "9.9"],
        )
        # A new notice may not add a second patch beside a carried one either.
        repeat = _synthetic_notice(
            "健保審字第1159999997號",
            [(["9.9.Fixture：(115/9/1)"], ["9.9.Fixture："])],
            effective="（自115年9月1日生效）",
        )
        carried = compose_overlay_release(
            self.pg.dsn, [repeat, galafold], base_run_id=BASE_RUN, today=TODAY
        )
        self.assertEqual(
            [(item["reference_number"], item["error"]) for item in carried.failures],
            [
                (
                    "健保審字第1159999997號",
                    "another patch amends the same clause on the same date: "
                    "9.9 effective 2026-09-01",
                )
            ],
        )


SUPERSEDE_REFERENCE = "健保審字第1159000001號"
HELD_REFERENCE = "健保審字第1159000003號"
SUPERSEDE_ROWS = [
    (
        ["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌。"],
        ["9.2.Carboplatin：", "限用於卵巢癌第一線。"],
    ),
    (
        ["9.5.Paclitaxel成分劑：(115/10/1)", "限用於乳癌。"],
        ["9.5.Paclitaxel成分劑：", "限用於轉移性乳癌。"],
    ),
]


class ServedPatchPinTest(unittest.TestCase):
    """2026-09-28 finding R2-M1: a supersede re-bound served predecessors.

    The served patch and its fresh projection must agree on patch id, text,
    effective date and predecessor, both when a carried notice is projected
    again and when it is superseded.
    """

    def _pair(self, **served_change: str) -> tuple:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS)
        fresh = notice_rows(
            notice,
            run_id=PLACEHOLDER_RUN_ID,
            served_run_id="publication",
            served={
                code: {"raw_text_sha256": "a" * 64} for code in ("9.2", "9.5")
            },
            rendering_checks={"9.2": "exact", "9.5": "exact"},
        )
        patches = tuple(
            {**row, **served_change} if row["clause_code"] == "9.2" else row
            for row in fresh.rows["clause_patch"]
        )
        previous = CarriedNotice(
            event=fresh.rows["notice_event"][0],
            effects=tuple(fresh.rows["notice_effect"]),
            patches=patches,
            dependent_tables=(),
        )
        base = types.SimpleNamespace(
            resolutions={
                str(row["patch_id"]): {"resolution_state": "verified_scheduled"}
                for row in patches
            }
        )
        return previous, fresh, base

    def test_identical_projection_is_reproduced(self) -> None:
        previous, fresh, base = self._pair()
        self.assertEqual(_served_differences(previous, fresh), ([], []))
        self.assertIsNone(_supersede_refusal(previous, fresh, base))

    def test_new_predecessor_is_a_move_not_a_rebind(self) -> None:
        # Finding HIGH-A: the served patch is the same, only the publication
        # text it was bound to moved.  That is reported apart from a
        # difference, and a supersede still refuses to re-bind it.
        previous, fresh, base = self._pair(predecessor_text_sha256="b" * 64)
        differences, moved = _served_differences(previous, fresh)
        self.assertEqual(differences, [])
        self.assertEqual(
            [(item["clause_code"], item["served_predecessor_text_sha256"],
              item["current_predecessor_text_sha256"]) for item in moved],
            [("9.2", "b" * 64, "a" * 64)],
        )
        self.assertEqual(
            _supersede_refusal(previous, fresh, base),
            "served clause 9.2 is not re-projected byte-identically "
            "(predecessor_text_sha256 differs)",
        )

    def test_changed_text_is_a_difference(self) -> None:
        previous, fresh, base = self._pair(source_exact_patch_sha256="c" * 64)
        self.assertEqual(
            _served_differences(previous, fresh),
            (["9.2 (source_exact_patch_sha256 differs)"], []),
        )

    def test_scope_fields_alone_are_not_a_difference(self) -> None:
        # Confusable negative: a supersede rewrites these by design; they are
        # reported as scope changes, not refused.
        previous, fresh, base = self._pair(
            partial_event_projection=True,
            unprocessed_event_scope=[{"clause_code": "9.69"}],
        )
        self.assertEqual(_served_differences(previous, fresh), ([], []))
        self.assertIsNone(_supersede_refusal(previous, fresh, base))


class SupersedeLiveTest(unittest.TestCase):
    """2026-09-28 finding M4: held-back clauses of a served notice."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg)
        _seed(cls.pg.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def _serve(self, composition, base_run_id: str) -> dict:
        load_overlay_release(self.pg.dsn, composition.release)
        return activate_overlay_release(
            self.pg.dsn,
            run_id=composition.release.run_id,
            expected_sealed_fingerprint=composition.release.sealed_fingerprint,
            expected_base_run_id=base_run_id,
        )

    def test_supersede_serves_new_clauses_without_changing_served_text(
        self,
    ) -> None:
        dsn = self.pg.dsn
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS)
        # A second notice whose only clause is held back serves no patch.
        held = _synthetic_notice(
            HELD_REFERENCE,
            [(["3.3.28.Migalastat：(115/10/1)", "限用於法布瑞氏症。"],
              ["3.3.28.Migalastat：", "限用於成人法布瑞氏症。"])],
        )
        partial = {
            SUPERSEDE_REFERENCE: _rendering(notice, "9.2"),
            HELD_REFERENCE: _rendering(held, "-"),
        }
        full = {
            SUPERSEDE_REFERENCE: _rendering(notice),
            HELD_REFERENCE: _rendering(held),
        }

        # 1. 9.5 and 3.3.28 are held back (their text is not in the
        # rendering); 9.2 serves.
        first = compose_overlay_release(
            dsn, [notice, held], base_run_id=BASE_RUN,
            official_renderings=partial, today=TODAY,
        )
        self.assertEqual(
            [(item["clause_code"], item["reason"], item["origin"])
             for item in first.blocked_clauses],
            [("9.5", "official_rendering_mismatch", "new"),
             ("3.3.28", "official_rendering_mismatch", "new")],
        )
        self._serve(first, BASE_RUN)
        served = _public_patches(dsn)
        self.assertEqual(sorted(served), ["9.2", "9.9"])

        # 2. Unchanged: the carried notices are left as they are.
        unchanged = compose_overlay_release(
            dsn, [notice, held], official_renderings=partial, today=TODAY
        )
        self.assertIsNone(unchanged.release)
        self.assertEqual(
            [
                (item["reference_number"], item["reproduced_clauses"])
                for item in unchanged.carried_notices
            ],
            [(SUPERSEDE_REFERENCE, ["9.2"]), (HELD_REFERENCE, [])],
        )
        self.assertEqual(unchanged.failures, ())
        self.assertEqual(unchanged.status, "no_change_with_holds")
        # Finding R2-M2: a carried notice whose fresh parse no longer
        # reproduces a served patch is a failure, not a green no-change; its
        # carried rows stay served.
        diverged = compose_overlay_release(
            dsn,
            [notice, held],
            official_renderings={
                SUPERSEDE_REFERENCE: _rendering(notice, "-"),
                HELD_REFERENCE: _rendering(held, "-"),
            },
            today=TODAY,
        )
        self.assertIsNone(diverged.release)
        self.assertEqual(
            [(item["reference_number"], item["stage"], item["error"])
             for item in diverged.failures],
            [(SUPERSEDE_REFERENCE, "carried",
              "the fresh projection does not reproduce the served patches: "
              "9.2 is held back now (official_rendering_mismatch)")],
        )
        self.assertEqual(
            [item["reference_number"] for item in diverged.carried_notices],
            [HELD_REFERENCE],
        )
        self.assertEqual(diverged.status, "no_change_with_holds")
        self.assertEqual(
            [(item["clause_code"], item["origin"])
             for item in unchanged.blocked_clauses],
            [("9.5", "carried"), ("3.3.28", "carried")],
        )

        # 3. Both are now projectable, but superseding was not asked for.
        waiting = compose_overlay_release(
            dsn, [notice, held], official_renderings=full, today=TODAY
        )
        self.assertIsNone(waiting.release)
        self.assertEqual(
            [
                (item["reference_number"], item["reason"], item["clause_codes"])
                for item in waiting.dropped_notices
            ],
            [
                (
                    SUPERSEDE_REFERENCE,
                    "carried_notice_has_newly_projectable_clauses",
                    ["9.5"],
                ),
                (
                    HELD_REFERENCE,
                    "carried_notice_has_newly_projectable_clauses",
                    ["3.3.28"],
                ),
            ],
        )
        self.assertEqual(waiting.status, "no_change_with_holds")

        # 4. Confusable negative: the fresh parse would change served 9.2
        # text.  That notice fails closed and keeps its carried rows; the
        # rest of the batch still composes.
        changed_rows = [
            (["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌及子宮頸癌。"],
             SUPERSEDE_ROWS[0][1]),
            SUPERSEDE_ROWS[1],
        ]
        changed = _synthetic_notice(
            SUPERSEDE_REFERENCE,
            changed_rows,
            declared_sha256=notice.attachment.sha256,
        )
        self.assertEqual(changed.notice_id, notice.notice_id)
        other = _synthetic_notice(
            "健保審字第1159000002號",
            [(["2.1.4.2.Rivaroxaban：(115/10/1)", "限用於心房纖維顫動。"],
              ["2.1.4.2.Rivaroxaban：", "限用於靜脈血栓。"])],
        )
        attempt = compose_overlay_release(
            dsn,
            [changed, other],
            official_renderings={SUPERSEDE_REFERENCE: _rendering(changed)},
            supersede=True,
            today=TODAY,
        )
        self.assertEqual(
            [(item["reference_number"], item["stage"], item["error"])
             for item in attempt.failures],
            [
                (
                    SUPERSEDE_REFERENCE,
                    "carried",
                    "the fresh projection does not reproduce the served "
                    "patches: 9.2 (patch_id, source_exact_patch_sha256 "
                    "differs)",
                )
            ],
        )
        self.assertEqual(attempt.superseded_notices, ())
        self.assertEqual(
            _notice_rows(attempt.release, SUPERSEDE_REFERENCE),
            _notice_rows(first.release, SUPERSEDE_REFERENCE),
        )
        self.assertEqual(
            sorted(row["clause_code"] for row in attempt.release.rows["clause_patch"]),
            ["2.1.4.2", "9.2", "9.9"],
        )
        load_overlay_release(dsn, attempt.release)

        # 5. The same reference with another source artifact fails closed.
        foreign = _synthetic_notice(
            SUPERSEDE_REFERENCE, SUPERSEDE_ROWS, declared_sha256="e" * 64
        )
        refused = compose_overlay_release(
            dsn,
            [foreign],
            official_renderings={SUPERSEDE_REFERENCE: _rendering(foreign)},
            supersede=True,
            today=TODAY,
        )
        self.assertIsNone(refused.release)
        self.assertEqual(
            [item["error"] for item in refused.failures],
            ["the notice's source artifact differs from the carried notice"],
        )

        # 6. A served patch whose resolution moved on is not reset.
        patch_92 = str(served["9.2"]["patch_id"])
        with _connect(dsn, read_only=False) as connection:
            connection.execute(
                "SELECT nhi_rule_history_announced.set_patch_resolution("
                "%s,%s,'effective_unconsolidated','fixture reviewer','{}')",
                (first.release.run_id, patch_92),
            )
            connection.commit()
        progressed = compose_overlay_release(
            dsn, [notice], official_renderings=full, supersede=True, today=TODAY
        )
        self.assertEqual(
            [item["error"] for item in progressed.failures],
            [
                "served clause 9.2 is effective_unconsolidated; superseding "
                "would reset its resolution"
            ],
        )
        with _connect(dsn, read_only=False) as connection:
            connection.execute(
                "SELECT nhi_rule_history_announced.set_patch_resolution("
                "%s,%s,'verified_scheduled','fixture reviewer undo','{}')",
                (first.release.run_id, patch_92),
            )
            connection.commit()

        # 7. Supersede: 9.5 and 3.3.28 are added; served 9.2 is
        # byte-identical.
        second = compose_overlay_release(
            dsn, [notice, held], official_renderings=full, supersede=True,
            today=TODAY,
        )
        self.assertEqual(second.failures, ())
        self.assertEqual(second.status, "passed")
        self.assertEqual(second.blocked_clauses, ())
        self.assertEqual(
            second.superseded_notices,
            (
                {
                    "reference_number": SUPERSEDE_REFERENCE,
                    "bundle": f"gov_{SUPERSEDE_REFERENCE}",
                    "effective_on": "2026-10-01",
                    "notice_id": notice.notice_id,
                    "served_clauses": ["9.2"],
                    "added_clauses": ["9.5"],
                    # Finding R2-M1: the scope fields a supersede rewrites
                    # are reported.
                    "scope_changes": [
                        {
                            "clause_code": "9.2",
                            "field": "partial_event_projection",
                            "served": True,
                            "fresh": False,
                        },
                        {
                            "clause_code": "9.2",
                            "field": "unprocessed_event_scope",
                            "served": [
                                {
                                    "effect_type": "clause_amendment",
                                    "clause_code": "9.5",
                                    "blocked_reason": "official_rendering_mismatch",
                                }
                            ],
                            "fresh": [],
                        },
                    ],
                },
                {
                    "reference_number": HELD_REFERENCE,
                    "bundle": f"gov_{HELD_REFERENCE}",
                    "effective_on": "2026-10-01",
                    "notice_id": held.notice_id,
                    "served_clauses": [],
                    "added_clauses": ["3.3.28"],
                    "scope_changes": [],
                },
            ),
        )
        self.assertTrue(
            _notice_rows(second.release, HELD_REFERENCE)["notice_event"][0][
                "event_scope_complete"
            ]
        )
        rows = _notice_rows(second.release, SUPERSEDE_REFERENCE)
        self.assertTrue(rows["notice_event"][0]["event_scope_complete"])
        self.assertEqual(
            [(row["clause_code"], row["projection_status"])
             for row in rows["notice_effect"]],
            [("9.2", "projected_source_exact_patch"),
             ("9.5", "projected_source_exact_patch")],
        )
        evidence = {
            item.patch_id: item.evidence for item in second.release.resolutions
        }
        self.assertEqual(
            evidence[patch_92]["superseded_projection"]["run_id"],
            first.release.run_id,
        )
        # The superseded patch keeps its served resolution state and reason.
        resolution_92 = next(
            item for item in second.release.resolutions if item.patch_id == patch_92
        )
        self.assertEqual(
            (resolution_92.resolution_state, resolution_92.reason),
            ("verified_scheduled", "fixture reviewer undo"),
        )
        self.assertEqual(
            evidence[BASE_PATCH]["carried_forward"]["run_id"],
            first.release.run_id,
        )
        activated = self._serve(second, first.release.run_id)
        self.assertEqual(activated["blocked_clauses"], [])
        after = _public_patches(dsn)
        self.assertEqual(sorted(after), ["3.3.28", "9.2", "9.5", "9.9"])
        for key in (
            "patch_id", "source_exact_patch_text",
            "source_exact_patch_sha256", "effective_from",
        ):
            self.assertEqual(after["9.2"][key], served["9.2"][key])
        self.assertEqual(after["9.9"]["current_resolution_state"], "reconciled")

        # 8. Activation refuses a run that would stop serving 9.5 and
        # 3.3.28.
        with self.assertRaisesRegex(
            AnnouncedReleaseError,
            'does not carry everything.*"clause_patches":\\["3.3.28","9.5"\\]',
        ):
            activate_overlay_release(
                dsn,
                run_id=attempt.release.run_id,
                expected_sealed_fingerprint=attempt.release.sealed_fingerprint,
                expected_base_run_id=second.release.run_id,
            )
        self.assertEqual(
            sorted(_public_patches(dsn)), ["3.3.28", "9.2", "9.5", "9.9"]
        )

        # 9. Rollback restores the chain that held both clauses back.
        restored = rollback_overlay_release(
            dsn, from_run_id=second.release.run_id
        )
        self.assertEqual(
            [item["clause_code"] for item in restored["blocked_clauses"]],
            ["9.5", "3.3.28"],
        )
        self.assertEqual(sorted(_public_patches(dsn)), ["9.2", "9.9"])

        # 10. Finding R2-H4: 9.2 is withdrawn in the served run after
        # `second` was composed.  Activating `second` would serve 9.2 as
        # verified_scheduled again, so it is refused.
        def resolve(run_id: str, patch_id: str, state: str, reason: str) -> None:
            with _connect(dsn, read_only=False) as connection:
                connection.execute(
                    "SELECT nhi_rule_history_announced.set_patch_resolution("
                    "%s,%s,%s,%s,'{}')",
                    (run_id, patch_id, state, reason),
                )
                connection.commit()

        resolve(first.release.run_id, patch_92, "withdrawn", "fixture withdrawal")
        with self.assertRaisesRegex(
            AnnouncedReleaseError,
            "does not carry the served run's current resolution of: 9.2;",
        ):
            activate_overlay_release(
                dsn,
                run_id=second.release.run_id,
                expected_sealed_fingerprint=second.release.sealed_fingerprint,
                expected_base_run_id=first.release.run_id,
            )
        self.assertEqual(
            _public_patches(dsn)["9.2"]["current_resolution_state"], "withdrawn"
        )
        # A run composed now carries the withdrawal (and cannot supersede
        # the withdrawn patch's notice); a resolution written meanwhile to a
        # run that is not served does not stop it.
        third = compose_overlay_release(
            dsn, [notice, held], official_renderings=full, supersede=True,
            today=TODAY,
        )
        self.assertEqual(
            [item["error"] for item in third.failures],
            ["served clause 9.2 is withdrawn; superseding would reset its "
             "resolution"],
        )
        load_overlay_release(dsn, third.release)
        resolve(
            attempt.release.run_id, patch_92, "withdrawn", "unserved run note"
        )
        activate_overlay_release(
            dsn,
            run_id=third.release.run_id,
            expected_sealed_fingerprint=third.release.sealed_fingerprint,
            expected_base_run_id=first.release.run_id,
        )
        served_now = _public_patches(dsn)
        self.assertEqual(sorted(served_now), ["3.3.28", "9.2", "9.9"])
        self.assertEqual(served_now["9.2"]["current_resolution_state"], "withdrawn")

        # 11. Finding R2-M3: `second` carries everything `third` serves, but
        # it was composed on `first`; activating it now is refused.
        with self.assertRaisesRegex(
            AnnouncedReleaseError,
            "composed on another base run: " + first.release.run_id,
        ):
            activate_overlay_release(
                dsn,
                run_id=second.release.run_id,
                expected_sealed_fingerprint=second.release.sealed_fingerprint,
                expected_base_run_id=third.release.run_id,
            )
        self.assertEqual(sorted(_public_patches(dsn)), ["3.3.28", "9.2", "9.9"])



OTHER_REFERENCE = "健保審字第1159000002號"
OTHER_ROWS = [
    (["2.1.4.2.Rivaroxaban：(115/10/1)", "限用於心房纖維顫動。"],
     ["2.1.4.2.Rivaroxaban：", "限用於靜脈血栓。"])
]
NEW_CLAUSE_REFERENCE = "健保審字第1159000004號"
NEW_CLAUSE_ROWS = [(["9.139.Mogamulizumab：(115/10/1)", "單獨用於。"], ["無"])]


class _LiveRunCase(unittest.TestCase):
    """A fresh disposable cluster per test, with served-run helpers."""

    def setUp(self) -> None:
        self.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(self.pg)
        _seed(self.pg.dsn)
        self.dsn = self.pg.dsn

    def tearDown(self) -> None:
        self.pg.close()

    def compose(self, notices, *, codes=None, **options):
        renderings = {
            notice.bundle.reference_number: _rendering(notice, *(codes or {}).get(
                notice.bundle.reference_number, ()))
            for notice in notices
        }
        return compose_overlay_release(
            self.dsn, notices, official_renderings=renderings, today=TODAY,
            **options,
        )

    def serve(self, composition, base_run_id: str) -> dict:
        load_overlay_release(self.dsn, composition.release)
        return self.activate(composition, base_run_id)

    def activate(self, composition, base_run_id: str) -> dict:
        return activate_overlay_release(
            self.dsn,
            run_id=composition.release.run_id,
            expected_sealed_fingerprint=composition.release.sealed_fingerprint,
            expected_base_run_id=base_run_id,
        )

    def resolve(self, run_id: str, patch_id: str, state: str, reason: str) -> None:
        with _connect(self.dsn, read_only=False) as connection:
            connection.execute(
                "SELECT nhi_rule_history_announced.set_patch_resolution("
                "%s,%s,%s,%s,'{}')",
                (run_id, patch_id, state, reason),
            )
            connection.commit()

    def publish(self, code: str, text: str) -> None:
        """The current publication now holds ``text`` for ``code``."""

        digest = sha256_text(text)
        with _connect(self.dsn, read_only=False) as connection:
            connection.execute("SET session_replication_role = replica")
            updated = connection.execute(
                "UPDATE nhi_rule_history_publication.current_clause SET "
                "raw_text=%s, raw_text_sha256=%s WHERE clause_code=%s",
                (text, digest, code),
            ).rowcount
            if not updated:
                connection.execute(
                    """
                    INSERT INTO nhi_rule_history_publication.current_clause
                    SELECT run_id, %s, chapter_number, %s, code_origin, %s,
                           source_acquisition_run_id, source_resource_id,
                           source_url, source_label, source_artifact_sha256,
                           source_span, %s, %s, normalized_text,
                           normalized_text_sha256, comparison_text,
                           comparison_sha256, valid_distinct_roc_date_count,
                           expected_version_count, reconstructed_version_count,
                           missing_version_count,
                           annotation_count_underflows_reconstructed,
                           inventory_status, source_row_sha256
                    FROM nhi_rule_history_publication.current_clause
                    WHERE clause_code='9.9'
                    """,
                    (code, code, code, text, digest),
                )
            connection.commit()

    def served_patch(self, code: str) -> dict:
        with _connect(self.dsn, read_only=True) as connection:
            row = connection.execute(
                "SELECT * FROM nhi_rule_history_announced.v_public_clause_patch "
                "WHERE clause_code=%s",
                (code,),
            ).fetchone()
        return {key: _jsonable(value) if not isinstance(value, datetime) else value
                for key, value in row.items()}


class PublicationMoveLiveTest(_LiveRunCase):
    """2026-09-28 finding HIGH-A: a consolidated clause must not stall the lane.

    When NHI consolidates a served clause, the publication text its patch was
    bound to moves.  The carried notice then reported a failure on every
    compose, and without --skip-failed no later notice could be served.
    """

    def test_moved_predecessors_keep_served_rows_and_let_others_serve(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS)
        new_clause = _synthetic_notice(NEW_CLAUSE_REFERENCE, NEW_CLAUSE_ROWS)
        # 9.2 serves; 9.5 is held back; 9.139 is a new clause.
        first = self.compose(
            [notice, new_clause], base_run_id=BASE_RUN,
            codes={SUPERSEDE_REFERENCE: ("9.2",)},
        )
        self.serve(first, BASE_RUN)
        served_92 = self.served_patch("9.2")
        served_139 = self.served_patch("9.139")

        # NHI consolidates both clauses into the publication.
        self.publish("9.2", "9.2.Carboplatin：(115/10/1)\n限用於卵巢癌。")
        self.publish("9.139", "9.139.Mogamulizumab：(115/10/1)\n單獨用於。")
        other = _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)
        later = self.compose(
            [notice, new_clause, other], codes={SUPERSEDE_REFERENCE: ("9.2",)},
        )
        self.assertEqual(later.failures, ())
        self.assertEqual(
            [(item["reference_number"], item["clause_code"],
              item["served_predecessor_text_sha256"],
              item["current_predecessor_text_sha256"], item["settled"])
             for item in later.predecessor_moved],
            [
                (SUPERSEDE_REFERENCE, "9.2",
                 served_92["predecessor_text_sha256"],
                 sha256_text("9.2.Carboplatin：(115/10/1)\n限用於卵巢癌。"),
                 False),
                (NEW_CLAUSE_REFERENCE, "9.139", sha256_text(""),
                 sha256_text("9.139.Mogamulizumab：(115/10/1)\n單獨用於。"),
                 False),
            ],
        )
        self.assertEqual(later.status, "passed_with_holds")
        # The new notice composes and serves; the moved patches stay exactly
        # as served, bound to the text they were verified against.
        self.serve(later, first.release.run_id)
        self.assertEqual(
            sorted(_public_patches(self.dsn)), ["2.1.4.2", "9.139", "9.2", "9.9"]
        )
        for code, before in (("9.2", served_92), ("9.139", served_139)):
            after = self.served_patch(code)
            self.assertEqual(after["run_id"], later.release.run_id)
            for key in (*REPRODUCED_PATCH_KEYS, "component_manifest_sha256"):
                self.assertEqual(after[key], before[key], (code, key))

        # A supersede would re-bind the moved predecessor, so a carried
        # notice with newly projectable clauses is dropped instead.
        waiting = self.compose(
            [notice, new_clause, other], supersede=True,
        )
        self.assertEqual(waiting.failures, ())
        self.assertIsNone(waiting.release)
        self.assertEqual(
            [(item["reference_number"], item["reason"], item["clause_codes"])
             for item in waiting.dropped_notices],
            [(SUPERSEDE_REFERENCE, "carried_predecessor_moved", ["9.5"])],
        )

        # Once the moved patch is settled (reconciled), it no longer holds.
        self.resolve(later.release.run_id, served_92["patch_id"],
                     "reconciled", "fixture consolidation")
        settled = self.compose([notice, new_clause, other],
                               codes={SUPERSEDE_REFERENCE: ("9.2",)})
        self.assertEqual(
            [(item["clause_code"], item["served_resolution_state"], item["settled"])
             for item in settled.predecessor_moved],
            [("9.2", "reconciled", True), ("9.139", "verified_scheduled", False)],
        )

    def test_changed_text_still_fails_the_carried_notice(self) -> None:
        # Confusable negative: a re-parse that changes served text is a
        # failure, whatever the publication does.
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        self.publish("9.2", "9.2.Carboplatin：(115/10/1)\n限用於卵巢癌。")
        changed = _synthetic_notice(
            SUPERSEDE_REFERENCE,
            [(["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌及子宮頸癌。"],
              SUPERSEDE_ROWS[0][1])],
            declared_sha256=notice.attachment.sha256,
        )
        attempt = self.compose([changed])
        self.assertEqual(
            [(item["stage"], item["error"]) for item in attempt.failures],
            [("carried", "the fresh projection does not reproduce the served "
              "patches: 9.2 (patch_id, source_exact_patch_sha256 differs)")],
        )
        self.assertEqual(attempt.predecessor_moved, ())


class ResolutionPinLiveTest(_LiveRunCase):
    """2026-09-28 finding MEDIUM-D: recompose must follow a new resolution.

    The run identity did not cover the served resolutions, so after a
    resolution was written to the served run the next compose gave the same
    run, whose load replayed the stale resolutions and whose activation was
    refused on every tick.
    """

    def test_lane_recovers_after_a_post_effective_write(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        patch_92 = str(self.served_patch("9.2")["patch_id"])
        other = _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)
        # The lane loads a run, and its activation is held.
        held = self.compose([notice, other])
        load_overlay_release(self.dsn, held.release)
        # Confusable negative: with nothing written, composing again gives
        # the same run.
        self.assertEqual(self.compose([notice, other]).release.run_id,
                         held.release.run_id)
        # Meanwhile the post-effective writer resolves the served 9.2.
        self.resolve(first.release.run_id, patch_92, "effective_unconsolidated",
                     "fixture post-effective resolution")
        with self.assertRaisesRegex(
            AnnouncedReleaseError, "current resolution of: 9.2;"
        ):
            self.activate(held, first.release.run_id)
        # The next compose is a new run that carries the new resolution.
        again = self.compose([notice, other])
        self.assertNotEqual(again.release.run_id, held.release.run_id)
        self.assertFalse(load_overlay_release(self.dsn, again.release)["replayed"])
        self.activate(again, first.release.run_id)
        served = self.served_patch("9.2")
        self.assertEqual(served["current_resolution_state"], "effective_unconsolidated")
        self.assertEqual(served["run_id"], again.release.run_id)


class ResolutionRaceLiveTest(_LiveRunCase):
    """2026-09-28 finding MEDIUM-C: a write in flight during activation.

    A withdrawal inserted into the served run by a writer that does not take
    the global lock, and committed while activation runs, was lost: the new
    run was served with the older resolution.
    """

    def test_activation_waits_for_a_resolution_in_flight(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        patch_92 = str(self.served_patch("9.2")["patch_id"])
        candidate = self.compose(
            [notice, _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)]
        )
        load_overlay_release(self.dsn, candidate.release)

        inserted, release_writer = threading.Event(), threading.Event()

        def writer() -> None:
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    "SELECT nhi_rule_history_announced.set_patch_resolution("
                    "%s,%s,'withdrawn','fixture withdrawal in flight','{}')",
                    (first.release.run_id, patch_92),
                )
                inserted.set()
                release_writer.wait(30)
                connection.commit()

        outcome: dict[str, object] = {}

        def activation() -> None:
            try:
                outcome["result"] = self.activate(candidate, first.release.run_id)
            except Exception as exc:  # recorded for the assertion below
                outcome["error"] = exc

        writing = threading.Thread(target=writer)
        writing.start()
        self.assertTrue(inserted.wait(30))
        activating = threading.Thread(target=activation)
        activating.start()
        activating.join(1.5)
        # Activation waits for the writer instead of reading past it.
        self.assertTrue(activating.is_alive())
        release_writer.set()
        writing.join(30)
        activating.join(60)
        self.assertIsInstance(outcome.get("error"), AnnouncedReleaseError)
        self.assertIn("current resolution of: 9.2;", str(outcome["error"]))
        self.assertEqual(self.served_patch("9.2")["current_resolution_state"], "withdrawn")
        self.assertEqual(self.served_patch("9.2")["run_id"], first.release.run_id)


class RollbackCarryLiveTest(_LiveRunCase):
    """2026-09-28 finding MEDIUM-E: rollback reverted a withdrawal."""

    def test_rollback_carries_the_current_resolutions_back(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        second = self.compose(
            [notice, _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)]
        )
        self.serve(second, first.release.run_id)
        patch_92 = str(self.served_patch("9.2")["patch_id"])
        self.resolve(second.release.run_id, patch_92, "withdrawn",
                     "fixture withdrawal")

        def events(run_id: str, patch_id: str) -> int:
            with _connect(self.dsn, read_only=True) as connection:
                return connection.execute(
                    "SELECT count(*) AS n FROM nhi_rule_history_announced."
                    "patch_resolution_event WHERE run_id=%s AND patch_id=%s",
                    (run_id, patch_id),
                ).fetchone()["n"]

        unchanged_before = events(first.release.run_id, BASE_PATCH)
        restored = rollback_overlay_release(self.dsn, from_run_id=second.release.run_id)
        served = self.served_patch("9.2")
        self.assertEqual(served["run_id"], first.release.run_id)
        self.assertEqual(served["current_resolution_state"], "withdrawn")
        self.assertEqual(
            served["resolution_evidence"]["carried_forward"]["run_id"],
            second.release.run_id,
        )
        self.assertEqual(
            [(item["clause_code"], item["resolution_state"])
             for item in restored["carried_back_resolutions"]],
            [("9.2", "withdrawn")],
        )
        # Confusable negative: a patch whose resolution did not change gets
        # no new event.
        self.assertEqual(events(first.release.run_id, BASE_PATCH), unchanged_before)


class RollbackRecencyLiveTest(_LiveRunCase):
    """2026-09-28 finding MEDIUM-C (DB): carry-back keeps the newer decision."""

    def _two_runs(self):
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        second = self.compose(
            [notice, _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)]
        )
        self.serve(second, first.release.run_id)
        return first, second, str(self.served_patch("9.2")["patch_id"])

    def _write_past_guard(self, run_id: str, patch_id: str, state: str) -> None:
        """A write that reached the unserved run before the guard existed."""

        with _connect(self.dsn, read_only=False) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                "INSERT INTO nhi_rule_history_announced.patch_resolution_event "
                "(run_id, patch_id, resolution_state, reason, evidence) "
                "VALUES (%s,%s,%s,'fixture late write','{}')",
                (run_id, patch_id, state),
            )
            connection.commit()

    def test_a_newer_decision_in_the_restored_run_is_kept(self) -> None:
        first, second, patch_92 = self._two_runs()
        self._write_past_guard(first.release.run_id, patch_92, "withdrawn")
        restored = rollback_overlay_release(self.dsn, from_run_id=second.release.run_id)
        self.assertEqual(
            [(item["clause_code"], item["decision"], item["resolution_state"])
             for item in restored["carried_back_resolutions"]],
            [("9.2", "kept_newer_restored", "withdrawn")],
        )
        served = self.served_patch("9.2")
        self.assertEqual(
            (served["run_id"], served["current_resolution_state"]),
            (first.release.run_id, "withdrawn"),
        )

    def test_decisions_in_both_runs_refuse_the_rollback(self) -> None:
        first, second, patch_92 = self._two_runs()
        self._write_past_guard(first.release.run_id, patch_92, "withdrawn")
        self.resolve(second.release.run_id, patch_92, "effective_unconsolidated",
                     "fixture post-effective resolution")
        with self.assertRaisesRegex(
            AnnouncedReleaseError, "rollback refused: the resolution of 9.2 changed in both runs"
        ) as refused:
            rollback_overlay_release(self.dsn, from_run_id=second.release.run_id)
        self.assertEqual(self.served_patch("9.2")["run_id"], second.release.run_id)
        # The refusal says what unblocks it: the restored run's state and
        # reason, written to the served run, because under v27 the restored
        # run takes no write.
        self.assertIn(
            "resolve it in the served run first: make the served run's "
            "resolution state and reason equal to the restored run's "
            '(9.2: state "withdrawn", reason "fixture late write"), then roll '
            "back again",
            str(refused.exception),
        )
        with self.assertRaisesRegex(psycopg.Error, "is not the served run"):
            self.resolve(first.release.run_id, patch_92, "withdrawn", "fixture late write")
        self.resolve(second.release.run_id, patch_92, "withdrawn", "fixture late write")
        restored = rollback_overlay_release(self.dsn, from_run_id=second.release.run_id)
        self.assertEqual(restored["carried_back_resolutions"], [])
        served = self.served_patch("9.2")
        self.assertEqual(
            (served["run_id"], served["current_resolution_state"]),
            (first.release.run_id, "withdrawn"),
        )

    def test_a_resolution_without_origin_refuses_the_rollback(self) -> None:
        first, second, patch_92 = self._two_runs()
        self._write_past_guard(first.release.run_id, patch_92, "withdrawn")
        # Without its origin, the rolled-back run's only resolution could be
        # a decision written while it was served, so neither side can win.
        with _connect(self.dsn, read_only=False) as connection:
            connection.execute("SET session_replication_role = replica")
            stripped = connection.execute(
                "UPDATE nhi_rule_history_announced.patch_resolution_event "
                "SET evidence = evidence - 'carried_forward' "
                "- 'superseded_projection' WHERE run_id=%s AND patch_id=%s",
                (second.release.run_id, patch_92),
            ).rowcount
            connection.commit()
        self.assertEqual(stripped, 1)
        with self.assertRaisesRegex(
            AnnouncedReleaseError,
            "rollback refused: the resolution of 9.2 .*origin is not recorded.*"
            '9.2: state "withdrawn", reason "fixture late write"',
        ):
            rollback_overlay_release(self.dsn, from_run_id=second.release.run_id)
        self.assertEqual(self.served_patch("9.2")["run_id"], second.release.run_id)


class ResolutionGuardMigrationTest(_LiveRunCase):
    """2026-09-28 finding MEDIUM-C (DB): migration v27 and its rollback."""

    GUARD = MIGRATIONS / "2026-09-28_nhi_rule_history_announced_resolution_guard_v27.sql"
    ROLLBACK = MIGRATIONS / (
        "2026-09-28_nhi_rule_history_announced_resolution_guard_v27.rollback.sql"
    )

    def _write(self, run_id: str, patch_id: str, *, lock_timeout: str | None = None):
        with _connect(self.dsn, read_only=False) as connection:
            if lock_timeout:
                connection.execute(f"SET lock_timeout = '{lock_timeout}'")
            connection.execute(
                "SELECT nhi_rule_history_announced.set_patch_resolution("
                "%s,%s,'withdrawn','fixture withdrawal','{}')",
                (run_id, patch_id),
            )
            connection.commit()

    def test_writes_follow_the_served_run(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        first = self.compose([notice], base_run_id=BASE_RUN)
        self.serve(first, BASE_RUN)
        patch_92 = str(self.served_patch("9.2")["patch_id"])
        second = self.compose([notice, _synthetic_notice(OTHER_REFERENCE, OTHER_ROWS)])
        load_overlay_release(self.dsn, second.release)
        # A loaded run that was never served still takes its resolutions.
        self._write(second.release.run_id, patch_92)
        third = self.compose(
            [notice, _synthetic_notice(NEW_CLAUSE_REFERENCE, NEW_CLAUSE_ROWS)]
        )
        self.serve(third, first.release.run_id)
        served = self.served_patch("9.2")["run_id"]
        self.assertEqual(served, third.release.run_id)
        # The run served before is refused; the served run is accepted.
        with self.assertRaisesRegex(psycopg.Error, "was served before and is not the served run"):
            self._write(first.release.run_id, patch_92)
        self._write(served, patch_92)
        # The writer waits for the global announced lock.
        with psycopg.connect(self.dsn) as holder:
            holder.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (GLOBAL_LOCK_KEY,)
            )
            with self.assertRaisesRegex(psycopg.errors.LockNotAvailable, "lock timeout"):
                self._write(served, patch_92, lock_timeout="300ms")
        # The rollback file restores the v22 writer, and the migration
        # applies again on top of it.
        self.pg.psql(file=self.ROLLBACK)
        self._write(first.release.run_id, patch_92)
        self.pg.psql(file=self.GUARD)
        with self.assertRaisesRegex(psycopg.Error, "was served before and is not the served run"):
            self._write(first.release.run_id, patch_92)


class SupersedeReportLiveTest(_LiveRunCase):
    """2026-09-28 finding LOW: a supersede must report every column it rewrites."""

    def test_scope_changes_list_manifest_and_note_changes(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS)
        first = self.compose(
            [notice], base_run_id=BASE_RUN, codes={SUPERSEDE_REFERENCE: ("9.2",)}
        )
        self.serve(first, BASE_RUN)
        # The same artifact read with a leading paragraph (block locators
        # shift) and the 建議 header (public note wording).
        drifted = _synthetic_notice(
            SUPERSEDE_REFERENCE, SUPERSEDE_ROWS,
            declared_sha256=notice.attachment.sha256,
            header=("建議修訂後給付規定", "原給付規定"),
            preamble="<text:p>附件一</text:p>",
        )
        composition = self.compose([drifted], supersede=True)
        self.assertEqual(composition.failures, ())
        self.assertEqual(
            sorted({item["field"] for entry in composition.superseded_notices
                    for item in entry["scope_changes"]}),
            ["component_manifest_sha256", "partial_event_projection",
             "public_note", "unprocessed_event_scope"],
        )


def _write_bundle(
    root: Path, reference: str, content_xml: bytes, *, corrupt: bool = False
) -> str:
    """Write a registered-bundle layout the CLI reads; return its path."""

    relative = f"2026/gov_{reference}"
    bundle = root / relative
    bundle.mkdir(parents=True)
    payload = fixture_odt(content_xml)
    # The source-block receipts that corpus registration writes into raw.md.
    receipts = "".join(
        "<!-- source-block "
        + json.dumps(
            {
                "attachment_file_name": "attachment-000.odt",
                "block_id": item.block_id,
                "raw_text_sha256": item.block_text_sha256,
            },
            ensure_ascii=False,
        )
        + " -->\n"
        for item in read_odt_document(payload).paragraphs
    )
    raw = (
        f"# 公告修訂藥品給付規定。\n\n## 公告事項\n\n一、{reference}。\n\n"
        + receipts
    )
    files = {"raw.md": raw.encode("utf-8"), "attachment-000.odt": payload}
    for name, data in files.items():
        (bundle / name).write_bytes(data)
    manifest = {
        "schema": CORPUS_BUNDLE_SCHEMA,
        "canonical_url": "https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
        "ref_number": reference,
        "title_zh": "公告修訂藥品給付規定。",
        "publish_date": "2026-09-15",
        "source_uid": f"gov_{reference}",
        "declared_attachment_count": 1,
        "extraction_status": {
            "deterministic_blocks": "done",
            "proofread": "not_started",
        },
        "files": [
            {
                "file_name": "raw.md",
                "role": "raw_markdown",
                "sha256": hashlib.sha256(files["raw.md"]).hexdigest(),
                "byte_size": len(files["raw.md"]),
            },
            {
                "file_name": "attachment-000.odt",
                "role": "declared_attachment",
                "declared_sequence": 0,
                "media_type": ODT_MEDIA_TYPE,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_size": len(payload),
            },
        ],
    }
    (bundle / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    if corrupt:
        (bundle / "attachment-000.odt").write_bytes(payload + b"\0")
    return relative


class RenderingInputTest(unittest.TestCase):
    """2026-09-28 finding LOW-3: the attachment is rendered as verified."""

    def test_changed_attachment_bytes_are_not_rendered(self) -> None:
        notice = _synthetic_notice(SUPERSEDE_REFERENCE, SUPERSEDE_ROWS[:1])
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "attachment-000.odt"
            good = replace(notice, attachment=replace(notice.attachment, path=path))
            path.write_bytes(b"changed after verification")
            seen = {}

            def render(payloads, **_):
                seen.update(payloads)
                return {key: {"tables": []} for key in payloads}

            with mock.patch.object(cli, "render_table_cells", side_effect=render):
                self.assertEqual(cli._renderings([good]), {SUPERSEDE_REFERENCE: None})
            self.assertEqual(seen, {})
            # Confusable negative: the verified bytes are rendered.
            payload = fixture_odt(
                notice_fixture._comparison(SUPERSEDE_ROWS[:1])
            )
            path.write_bytes(payload)
            verified = replace(
                good,
                attachment=replace(
                    good.attachment, sha256=hashlib.sha256(payload).hexdigest()
                ),
            )
            with mock.patch.object(cli, "render_table_cells", side_effect=render):
                self.assertEqual(
                    cli._renderings([verified]), {SUPERSEDE_REFERENCE: {"tables": []}}
                )


class CliReceiptLiveTest(unittest.TestCase):
    """2026-09-28 finding M2: receipts must say what was not served."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg)
        _seed(cls.pg.dsn)
        cls.scratch = tempfile.TemporaryDirectory(prefix="nhi-overlay-cli-")
        cls.root = Path(cls.scratch.name)
        cls.good = _write_bundle(
            cls.root,
            "健保審字第1159000011號",
            notice_fixture._comparison(
                [(["9.2.Carboplatin：(115/10/1)", "限用於卵巢癌。"],
                  ["9.2.Carboplatin：", "限用於卵巢癌第一線。"])]
            ),
        )
        cls.held = _write_bundle(
            cls.root,
            "健保審字第1159000012號",
            notice_fixture._comparison(
                [(["9.139.Mogamulizumab：(115/10/1)", "單獨用於。"], ["無"])]
            ),
        )
        # The office rendering of this one attachment lacks its revised text,
        # so its only clause is held back and the notice serves no patch.
        cls.renderings = {
            (cls.root / cls.held / "attachment-000.odt").read_bytes(): _rendering(
                parse_notice(read_notice_bundle(cls.root / cls.held)), "-"
            )
        }
        cls.later = _write_bundle(
            cls.root,
            "健保審字第1159000013號",
            notice_fixture._comparison(
                [(["9.5.Paclitaxel成分劑：(115/11/1)", "限用於乳癌。"],
                  ["9.5.Paclitaxel成分劑：", "限用於轉移性乳癌。"])],
                effective="（自115年11月1日生效）",
            ),
        )
        cls.broken = _write_bundle(
            cls.root,
            "健保審字第1159000014號",
            notice_fixture._comparison(
                [(["9.9.Fixture：(115/10/1)"], ["9.9.Fixture："])]
            ),
            corrupt=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.scratch.cleanup()
        cls.pg.close()

    def _cli(self, *argv: str) -> tuple[int, dict | None, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        # The office suite has its own tests; here the rendering is fixed so
        # the receipts do not depend on it.  Other attachments are unrendered,
        # which --allow-without-rendering-check admits.
        with mock.patch.object(
            cli,
            "render_table_cells",
            side_effect=lambda payloads, **_: {
                key: self.renderings.get(payload)
                for key, payload in payloads.items()
            },
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = cli.main(list(argv))
        text = stdout.getvalue()
        return code, (json.loads(text) if text.strip() else None), stderr.getvalue()

    def _batch(self, command: str, *extra: str) -> list[str]:
        return [
            command, "--dsn", self.pg.dsn, "--corpus-root", str(self.root),
            "--allow-without-rendering-check", *extra,
        ]

    def _seed_registered(self, items: list[tuple[str, dict]]) -> None:
        """Work items that reached corpus_registered and then moved on."""

        with _connect(self.pg.dsn, read_only=False) as connection:
            connection.execute("SET session_replication_role = replica")
            for index, (source_uid, receipt) in enumerate(items):
                work_item_id = str(uuid.uuid4())
                guid = f"fixture-guid-{source_uid}"
                connection.execute(
                    """
                    INSERT INTO nhi_rule_history_update_queue.rss_work_item (
                      work_item_id, rss_identity_fingerprint,
                      item_identity_kind, item_identity_value,
                      source_feed_url, guid_raw, first_feed_observation_id,
                      first_item_index, first_item_fingerprint,
                      first_title_raw, first_link_raw, first_observed_at
                    ) VALUES (%s,%s,'rss_guid',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        work_item_id, sha256_text(guid), guid,
                        "https://www.nhi.gov.tw/fixture-rss", guid,
                        str(uuid.uuid4()), index, sha256_text(guid + ":item"),
                        f"公告 {source_uid}",
                        "https://www.nhi.gov.tw/ch/cp-00000-00000-3258-1.html",
                        datetime(2026, 9, 1, index, tzinfo=timezone.utc),
                    ),
                )
                steps = (
                    ("acquired", "corpus_registered", receipt),
                    ("corpus_registered", "proposal_running", {"step": 2}),
                    ("proposal_running", "staged_needs_review", {"step": 3}),
                )
                for seq, (source, target, evidence) in enumerate(steps, 1):
                    evidence = {"source_uid": source_uid, **evidence}
                    connection.execute(
                        """
                        INSERT INTO nhi_rule_history_update_queue
                          .work_item_transition (
                          work_item_id, transition_seq, transition_id,
                          from_state, to_state, actor_kind, evidence_sha256,
                          evidence_json, source_job_id, recorded_at
                        ) VALUES (%s,%s,%s,%s,%s,'fixture',%s,%s::jsonb,%s,%s)
                        """,
                        (
                            work_item_id, seq, str(uuid.uuid4()), source,
                            target, sha256_text(json_text(evidence)),
                            json_text(evidence), str(uuid.uuid4()),
                            datetime(2026, 9, 2, index, seq, tzinfo=timezone.utc),
                        ),
                    )
            connection.commit()

    def test_queue_registered_proves_each_bundle(self) -> None:
        def receipt(relative: str) -> dict:
            data = (self.root / relative / "manifest.json").read_bytes()
            return {
                "corpus_bundle_relative_path": relative,
                "corpus_manifest_sha256": hashlib.sha256(data).hexdigest(),
            }

        rows = [
            (["2.1.4.2.Rivaroxaban：(115/10/1)", "限用於心房纖維顫動。"],
             ["2.1.4.2.Rivaroxaban：", "限用於靜脈血栓。"])
        ]
        resaved = _write_bundle(
            self.root, "健保審字第1159000015號", notice_fixture._comparison(rows)
        )
        tampered = _write_bundle(
            self.root, "健保審字第1159000016號", notice_fixture._comparison(rows)
        )
        items = [
            (f"gov_{relative.split('/')[-1][4:]}", receipt(relative))
            for relative in (
                self.good, self.held, self.later, self.broken, resaved, tampered
            )
        ]
        items.append(("gov_健保審字第1159000017號", {"note": "no bundle path"}))
        # After registration the proofread lane re-saves one manifest with
        # bookkeeping, metadata and a derived row; another manifest changes
        # a source row, as a rewritten attachment would.
        for relative, change in ((resaved, "derived"), (tampered, "source")):
            path = self.root / relative / "manifest.json"
            manifest = json.loads(path.read_bytes())
            if change == "derived":
                text = "校對稿".encode("utf-8")
                (self.root / relative / "proofread.md").write_bytes(text)
                manifest["extraction_status"].update(
                    proofread="done", mineru="done"
                )
                manifest["proofread_method"] = "odt structural walker"
                manifest["files"].append(
                    {
                        "file_name": "proofread.md",
                        "role": "proofread",
                        "sha256": hashlib.sha256(text).hexdigest(),
                        "byte_size": len(text),
                    }
                )
            else:
                raw = (self.root / relative / "raw.md").read_bytes() + b"\n"
                (self.root / relative / "raw.md").write_bytes(raw)
                row = next(r for r in manifest["files"] if r["file_name"] == "raw.md")
                row.update(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
            path.write_bytes(canonical_json_bytes(manifest))
        self._seed_registered(items)

        code, receipt_json, _ = self._cli(
            "compose", "--dsn", self.pg.dsn, "--corpus-root", str(self.root),
            "--allow-without-rendering-check", "--queue-registered",
            "--effective-on", "2026-10-01", "--skip-failed",
        )
        self.assertEqual((code, receipt_json["status"]), (3, "passed_with_holds"))
        self.assertEqual(
            sorted(
                (item["bundle"], item["stage"], item["error"].split(":")[0])
                for item in receipt_json["failures"]
            ),
            sorted(
                [
                    ("gov_健保審字第1159000014號", "parse", "corpus bundle size mismatch"),
                    (
                        "gov_健保審字第1159000016號",
                        "queue",
                        "corpus manifest differs from its registration receipt "
                        "beyond extraction-status bookkeeping and derived text "
                        "layers",
                    ),
                    (
                        "gov_健保審字第1159000017號",
                        "queue",
                        "queue receipt has no safe bundle path",
                    ),
                ]
            ),
        )
        self.assertEqual(
            [item["reference_number"] for item in receipt_json["dropped_notices"]],
            ["健保審字第1159000013號"],
        )
        self.assertEqual(
            {item["reference_number"]: item["projected_clauses"]
             for item in receipt_json["notices"]},
            {
                "健保審字第1159000011號": ["9.2"],
                "健保審字第1159000012號": [],
                "健保審字第1159000015號": ["2.1.4.2"],
            },
        )

    def test_malformed_bundles_fail_alone(self) -> None:
        # 2026-09-28 finding LOW: exceptions other than the parser's own
        # (a manifest that is not JSON, an ODT whose XML is cut, a manifest
        # row of the wrong type) aborted the whole batch.
        rows = [(["9.9.Fixture：(115/10/1)"], ["9.9.Fixture："])]

        def bundle(reference: str) -> tuple[str, Path]:
            relative = _write_bundle(
                self.root, reference, notice_fixture._comparison(rows)
            )
            return relative, self.root / relative

        not_json, path = bundle("健保審字第1159000018號")
        (path / "manifest.json").write_bytes(b"{not json")
        cut_xml, path = bundle("健保審字第1159000019號")
        payload = fixture_odt(b"<office:document-content")
        (path / "attachment-000.odt").write_bytes(payload)
        manifest = json.loads((path / "manifest.json").read_bytes())
        for row in manifest["files"]:
            if row["file_name"] == "attachment-000.odt":
                row.update(sha256=hashlib.sha256(payload).hexdigest(),
                           byte_size=len(payload))
        (path / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        wrong_type, path = bundle("健保審字第1159000020號")
        registered = hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
        manifest = json.loads((path / "manifest.json").read_bytes())
        manifest["files"].append(
            {"file_name": "x.md", "role": ["proofread"], "sha256": "0" * 64,
             "byte_size": 1}
        )
        (path / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        # A queue receipt for it becomes a problem of that bundle alone.
        queued = _receipt_bundle(
            {"work_item_id": "w", "first_title_raw": "t",
             "evidence_json": {"corpus_bundle_relative_path": wrong_type,
                               "corpus_manifest_sha256": registered}},
            self.root,
        )
        self.assertRegex(queued.problem, r"^[A-Za-z]+Error: ")
        malformed = _receipt_bundle(
            {"work_item_id": "w", "first_title_raw": "t", "evidence_json": ["x"]},
            self.root,
        )
        self.assertRegex(malformed.problem, r"^[A-Za-z]+Error: ")

        code, receipt, error = self._cli(
            *self._batch(
                "compose", "--notice", self.good, "--notice", not_json,
                "--notice", cut_xml, "--effective-on", "2026-10-01",
                "--skip-failed",
            )
        )
        self.assertEqual((code, error), (3, ""))
        failures = {
            item["bundle"]: (item["stage"], item["error"].split(":")[0])
            for item in receipt["failures"]
        }
        self.assertEqual(failures["gov_健保審字第1159000018號"], ("parse", "JSONDecodeError"))
        self.assertEqual(failures["gov_健保審字第1159000019號"][0], "parse")
        self.assertRegex(failures["gov_健保審字第1159000019號"][1], r"^[A-Za-z]+Error$")
        # The good notice still composes.
        self.assertIn(
            "健保審字第1159000011號",
            {item["reference_number"] for item in receipt["notices"]},
        )

    def test_acknowledged_failures_can_leave_the_batch_green(self) -> None:
        # 2026-09-28 finding R2-L1: known failures made every queue batch
        # non-green, so exit 3 and --skip-failed were noise.
        selection = ["--notice", self.good, "--notice", self.broken]
        code, receipt, _ = self._cli(
            *self._batch("compose", *selection, "--skip-failed")
        )
        self.assertEqual((code, receipt["status"]), (3, "passed_with_holds"))
        known = receipt["failures"]
        self.assertEqual(
            [(item["bundle"], item["stage"]) for item in known],
            [("gov_健保審字第1159000014號", "parse")],
        )
        path = self.root / "acknowledged.json"

        def acknowledge(entries: object) -> list[str]:
            path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            return ["--acknowledged-failures", str(path)]

        # Acknowledged exactly: green, and --skip-failed is not needed.
        code, receipt, error = self._cli(
            *self._batch("compose", *selection, *acknowledge(known))
        )
        self.assertEqual((code, receipt["status"], error), (0, "passed", ""))
        self.assertEqual(
            (receipt["failures"], receipt["acknowledged_failures"],
             receipt["stale_acknowledgments"]),
            ([], known, []),
        )
        # Confusable negative: one character more acknowledges nothing.
        near = [{**known[0], "error": known[0]["error"] + "."}]
        code, receipt, error = self._cli(
            *self._batch("compose", *selection, *acknowledge(near))
        )
        self.assertEqual((code, receipt), (1, None))
        self.assertIn("notice failures", error)
        code, receipt, _ = self._cli(
            *self._batch("compose", *selection, *acknowledge(near), "--skip-failed")
        )
        self.assertEqual((code, receipt["status"]), (3, "passed_with_holds"))
        self.assertEqual(
            (receipt["failures"], receipt["stale_acknowledgments"]), (known, near)
        )
        # A known failure that no longer occurs holds the batch until the
        # list is updated.
        code, receipt, _ = self._cli(
            *self._batch("compose", "--notice", self.good, *acknowledge(known))
        )
        self.assertEqual((code, receipt["status"]), (3, "passed_with_holds"))
        self.assertEqual(
            (receipt["failures"], receipt["stale_acknowledgments"]), ([], known)
        )
        code, receipt, error = self._cli(
            *self._batch("compose", *selection, *acknowledge({"bundle": "x"}))
        )
        self.assertEqual((code, receipt), (1, None))
        self.assertIn("acknowledged failures must be", error)

    def test_receipts_report_failures_drops_and_held_back_clauses(self) -> None:
        selection = [
            "--notice", self.good, "--notice", self.held,
            "--notice", self.later, "--notice", self.broken,
            "--effective-on", "2026-10-01",
        ]
        # Green only when nothing is held back.
        code, receipt, _ = self._cli(*self._batch("compose", "--notice", self.good))
        self.assertEqual((code, receipt["status"]), (0, "passed"))
        self.assertEqual(
            (receipt["failures"], receipt["dropped_notices"],
             receipt["blocked_clauses"]),
            ([], [], []),
        )

        # Without --skip-failed a failure refuses the batch: stderr only.
        code, receipt, error = self._cli(*self._batch("compose", *selection))
        self.assertEqual((code, receipt), (1, None))
        self.assertIn("notice failures", error)
        self.assertIn("gov_健保審字第1159000014號", error)

        code, receipt, error = self._cli(
            *self._batch("compose", *selection, "--skip-failed")
        )
        self.assertEqual((code, receipt["status"], error), (3, "passed_with_holds", ""))
        self.assertEqual(
            [(item["bundle"], item["stage"]) for item in receipt["failures"]],
            [("gov_健保審字第1159000014號", "parse")],
        )
        self.assertIn("size mismatch", receipt["failures"][0]["error"])
        self.assertEqual(
            [(item["reference_number"], item["reason"])
             for item in receipt["dropped_notices"]],
            [("健保審字第1159000013號", "effective_on_not_selected")],
        )
        self.assertEqual(
            [(item["reference_number"], item["clause_code"], item["reason"],
              item["origin"]) for item in receipt["blocked_clauses"]],
            [("健保審字第1159000012號", "9.139", "official_rendering_mismatch",
              "new")],
        )
        self.assertEqual(
            {item["reference_number"]: item["projected_clauses"]
             for item in receipt["notices"]},
            {"健保審字第1159000011號": ["9.2"], "健保審字第1159000012號": []},
        )
        composed_run = receipt["run_id"]

        code, loaded, _ = self._cli(
            *self._batch("load", *selection, "--skip-failed")
        )
        self.assertEqual((code, loaded["status"]), (3, "passed_with_holds"))
        self.assertEqual(loaded["release"]["run_id"], composed_run)
        self.assertEqual(loaded["load"]["run_id"], composed_run)
        self.assertEqual(loaded["failures"], receipt["failures"])
        receipt_path = self.root / "load-receipt.json"
        receipt_path.write_text(json.dumps(loaded, ensure_ascii=False), encoding="utf-8")
        sealed = loaded["load"]["sealed_fingerprint"]
        activate = [
            "activate", "--dsn", self.pg.dsn, "--run-id", composed_run,
            "--expect-sealed-fingerprint", sealed,
            "--expect-base-run-id", BASE_RUN,
        ]

        # A receipt of another run is refused before anything is served.
        wrong = self.root / "wrong-receipt.json"
        wrong.write_text(
            json.dumps({**loaded, "load": {**loaded["load"], "run_id": BASE_RUN}}),
            encoding="utf-8",
        )
        code, receipt, error = self._cli(*activate, "--load-receipt", str(wrong))
        self.assertEqual((code, receipt), (1, None))
        self.assertIn("does not name the run being activated", error)
        self.assertEqual(sorted(_public_patches(self.pg.dsn)), ["9.9"])

        code, activated, _ = self._cli(*activate, "--load-receipt", str(receipt_path))
        self.assertEqual((code, activated["status"]), (3, "passed_with_holds"))
        self.assertEqual(activated["failures"], loaded["failures"])
        self.assertEqual(activated["dropped_notices"], loaded["dropped_notices"])
        self.assertEqual(activated["load_status"], "passed_with_holds")
        self.assertEqual(
            [(item["clause_code"], item["reason"])
             for item in activated["blocked_clauses"]],
            [("9.139", "official_rendering_mismatch")],
        )
        self.assertEqual(sorted(_public_patches(self.pg.dsn)), ["9.2", "9.9"])
        with _connect(self.pg.dsn, read_only=True) as connection:
            evidence = connection.execute(
                """
                SELECT evidence FROM nhi_rule_history_announced.release_control_event
                WHERE run_id=%s ORDER BY control_id DESC LIMIT 1
                """,
                (composed_run,),
            ).fetchone()["evidence"]
        self.assertEqual(
            evidence["compose_holds"]["failures"], loaded["failures"]
        )

        # Everything selected is now carried: nothing to compose, holds stay.
        code, receipt, _ = self._cli(
            *self._batch(
                "compose", "--notice", self.good, "--notice", self.held
            )
        )
        self.assertEqual((code, receipt["status"]), (3, "no_change_with_holds"))
        self.assertIsNone(receipt["run_id"])
        self.assertEqual(
            sorted(item["reference_number"] for item in receipt["carried_notices"]),
            ["健保審字第1159000011號", "健保審字第1159000012號"],
        )

        code, restored, _ = self._cli(
            "rollback", "--dsn", self.pg.dsn, "--from-run-id", composed_run
        )
        self.assertEqual(code, 0)
        self.assertEqual(restored["served"]["release_run_id"], BASE_RUN)
        code, receipt, _ = self._cli(*self._batch("compose"))
        self.assertEqual((code, receipt["status"]), (0, "no_change"))

        # A run-level error goes to stderr with exit status 1.
        code, receipt, error = self._cli(
            *self._batch("compose", "--notice", self.good,
                         "--base-run-id", str(uuid.uuid4()))
        )
        self.assertEqual((code, receipt), (1, None))
        self.assertIn("error: the active announced run differs", error)


DYSLIPIDEMIA_TEXT = "2.6.1.降血脂藥物：(115/9/1)"
SERVED_DYSLIPIDEMIA_RUN = "aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa"
CARRYING_RUN = "bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb"
VERSIONLESS_RUN = "cccccccc-cccc-5ccc-8ccc-cccccccccccc"
ORPHAN_RUN = "dddddddd-dddd-5ddd-8ddd-dddddddddddd"


def _dyslipidemia_rows(
    run_id: str, *, version: bool = True, orphan_patch: bool = False
) -> dict[str, list[dict]]:
    """Rows of a 2.6.1-shaped run as the 2.6.1 loader's query sees them.

    ``orphan_patch`` points the patch at an effect the run lacks, which only
    rows inserted past the foreign keys can do.
    """

    notice_id = "77777777-7777-5777-8777-777777777777"
    effect_id = "88888888-8888-5888-8888-888888888888"
    patch_id = "99999999-9999-5999-8999-999999999999"
    rows = {
        "notice_event": [
            _hashed(
                {
                    "run_id": run_id,
                    "notice_id": notice_id,
                    "reference_number": dyslipidemia.NOTICE_REFERENCE,
                    "title": dyslipidemia.NOTICE_TITLE,
                    "official_url": dyslipidemia.NOTICE_URL,
                    "published_on": dyslipidemia.PUBLICATION_DATE,
                    "effective_on": dyslipidemia.EFFECTIVE_DATE,
                    "civil_timezone": "Asia/Taipei",
                    "source_artifact_sha256": (
                        dyslipidemia.EXPECTED_ARTIFACT_SHA256
                    ),
                    "source_artifact_filename": (
                        dyslipidemia.SOURCE_ARTIFACT_FILENAME
                    ),
                    "source_exact": True,
                    "event_scope_complete": True,
                    "unresolved_scope": [],
                }
            )
        ],
        "notice_effect": [
            _hashed(
                {
                    "run_id": run_id,
                    "effect_id": effect_id,
                    "notice_id": notice_id,
                    "effect_type": "clause_amendment",
                    "clause_code": "2.6.1",
                    "projection_status": "projected_source_exact_patch",
                    "scope_note": "fixture 2.6.1 patch",
                }
            )
        ],
        "clause_patch": [
            _hashed(
                {
                    "run_id": run_id,
                    "patch_id": patch_id,
                    "effect_id": str(uuid.uuid4()) if orphan_patch else effect_id,
                    "clause_code": "2.6.1",
                    "predecessor_text_sha256": sha256_text(SERVED["2.6.1"]),
                    "effective_from": dyslipidemia.EFFECTIVE_DATE,
                    "effective_until": None,
                    "resolution_state": "verified_scheduled",
                    "source_exact_patch_text": DYSLIPIDEMIA_TEXT,
                    "source_exact_patch_sha256": sha256_text(DYSLIPIDEMIA_TEXT),
                    "omitted_text_present": False,
                    "composition_status": "reviewed_composite",
                    "comparison_sha256": "1" * 64,
                    "component_manifest_sha256": "2" * 64,
                    "partial_event_projection": False,
                    "unprocessed_event_scope": [],
                    "public_note": "fixture 2.6.1 patch",
                }
            )
        ],
        "composed_clause_version": [],
    }
    if version:
        rows["composed_clause_version"].append(
            _hashed(
                {
                    "run_id": run_id,
                    "version_id": "12121212-1212-5212-8212-121212121212",
                    "patch_id": patch_id,
                    "clause_code": "2.6.1",
                    "effective_from": dyslipidemia.EFFECTIVE_DATE,
                    "predecessor_publication_run_id": PUBLICATION_RUN,
                    "predecessor_text_sha256": sha256_text(SERVED["2.6.1"]),
                    "predecessor_source_artifact_sha256": "d" * 64,
                    "composition_rule_version": "fixture",
                    "composition_manifest_sha256": "3" * 64,
                    "composed_text": DYSLIPIDEMIA_TEXT,
                    "composed_text_sha256": sha256_text(DYSLIPIDEMIA_TEXT),
                    "amendment_block_count": 1,
                    "inherited_block_count": 1,
                    "review_status": "deterministic_owner_directed",
                    "public_note": "fixture 2.6.1 composite",
                }
            )
        )
    return rows


def _insert_run(
    dsn: str,
    run_id: str,
    rows: dict[str, list[dict]],
    *,
    loader_version: str,
    fingerprint: str,
    activate: bool = False,
    carried_from: str | None = None,
) -> None:
    """Insert a sealed run directly, past triggers, as the base seed does.

    With ``carried_from``, a patch that run serves gets a resolution carried
    from its current one there, as composition records it.
    """

    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    counts = {name: len(rows.get(name, ())) for name in SEALED_COUNT_TABLES}
    with _connect(dsn, read_only=False) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            """
            INSERT INTO nhi_rule_history_announced.release_run (
              run_id, state, loader_version, evaluator_version,
              source_artifact_sha256, input_fingerprint, expected_counts,
              verified_counts, table_fingerprints, output_fingerprint,
              sealed_fingerprint, started_at, sealed_at
            ) VALUES (%s,'sealed',%s,'fixture',%s,%s,%s::jsonb,%s::jsonb,
                      '{}'::jsonb,%s,%s,%s,%s)
            """,
            (
                run_id, loader_version, "f" * 64, fingerprint,
                json_text(counts), json_text(counts), fingerprint,
                fingerprint, now, now,
            ),
        )
        for table, table_rows in rows.items():
            for row in table_rows:
                columns = list(row)
                connection.execute(
                    f"INSERT INTO nhi_rule_history_announced.{table} "
                    f"({','.join(columns)}) VALUES ("
                    + ",".join(
                        "%s::jsonb" if isinstance(row[c], (list, dict)) else "%s"
                        for c in columns
                    )
                    + ")",
                    [
                        json_text(row[c])
                        if isinstance(row[c], (list, dict))
                        else row[c]
                        for c in columns
                    ],
                )
        for patch in rows["clause_patch"]:
            origin = connection.execute(
                """
                SELECT resolution_id, resolution_state, resolution_reason
                FROM nhi_rule_history_announced.v_current_patch_resolution
                WHERE run_id=%s AND patch_id=%s
                """,
                (carried_from, patch["patch_id"]),
            ).fetchone() if carried_from else None
            evidence = (
                {}
                if origin is None
                else {
                    "carried_forward": {
                        "run_id": carried_from,
                        "resolution_id": origin["resolution_id"],
                    }
                }
            )
            connection.execute(
                """
                INSERT INTO nhi_rule_history_announced.patch_resolution_event (
                  run_id, patch_id, resolution_state, reason, evidence
                ) VALUES (%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    run_id,
                    patch["patch_id"],
                    "verified_scheduled" if origin is None else origin["resolution_state"],
                    "fixture" if origin is None else origin["resolution_reason"],
                    json_text(evidence),
                ),
            )
        if activate:
            connection.execute(
                """
                INSERT INTO nhi_rule_history_announced.release_control_event (
                  run_id, action, reason, evidence
                ) VALUES (%s,'activate','fixture 2.6.1 loader','{}')
                """,
                (run_id,),
            )
        connection.commit()


class DyslipidemiaGuardLiveTest(unittest.TestCase):
    """2026-09-28 finding F-L5: never unserve the 2.6.1 composed version.

    The subscriber sync runs the 2.6.1 loader with activation on every tick;
    if the served run has no 2.6.1 composed version for the 2.6.1 notice
    artifact, the loader re-activates its own run and unserves every overlay
    patch.  The loader's own query is the instrument here.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        _apply_migrations(cls.pg)
        _seed(cls.pg.dsn)
        # A real composite, built while the fixture base was served, carries
        # no 2.6.1 at all.
        galafold = [
            notice
            for notice in _fixture_notices()
            if "1150057033" in notice.bundle.reference_number
        ]
        cls.without = prepare_overlay_release(
            cls.pg.dsn, galafold, base_run_id=BASE_RUN, today=TODAY
        )
        load_overlay_release(cls.pg.dsn, cls.without)
        # Then a 2.6.1 run is served, as in production.
        _insert_run(
            cls.pg.dsn,
            SERVED_DYSLIPIDEMIA_RUN,
            _dyslipidemia_rows(SERVED_DYSLIPIDEMIA_RUN),
            loader_version="fixture 2.6.1 loader",
            fingerprint="a" * 64,
            activate=True,
        )
        for run_id, rows, fingerprint in (
            (CARRYING_RUN, _dyslipidemia_rows(CARRYING_RUN), "b" * 64),
            (
                VERSIONLESS_RUN,
                _dyslipidemia_rows(VERSIONLESS_RUN, version=False),
                "c" * 64,
            ),
            (
                ORPHAN_RUN,
                _dyslipidemia_rows(ORPHAN_RUN, orphan_patch=True),
                "d" * 64,
            ),
        ):
            _insert_run(
                cls.pg.dsn,
                run_id,
                rows,
                loader_version=LOADER_VERSION,
                fingerprint=fingerprint,
                carried_from=SERVED_DYSLIPIDEMIA_RUN,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def _served_source(self) -> dict | None:
        with psycopg.connect(self.pg.dsn) as connection:
            return dyslipidemia._active_announced_source(connection)

    def _activate(self, run_id: str, fingerprint: str) -> dict:
        return activate_overlay_release(
            self.pg.dsn,
            run_id=run_id,
            expected_sealed_fingerprint=fingerprint,
            expected_base_run_id=SERVED_DYSLIPIDEMIA_RUN,
        )

    def test_activation_keeps_the_served_2_6_1_version(self) -> None:
        served = self._served_source()
        # Positive control: the loader's query sees the served 2.6.1 run.
        self.assertEqual(served["run_id"], SERVED_DYSLIPIDEMIA_RUN)

        # A composite built without 2.6.1 is refused.
        with self.assertRaisesRegex(
            AnnouncedReleaseError, "served 2.6.1 composed version"
        ):
            self._activate(
                self.without.run_id, self.without.sealed_fingerprint
            )
        # So is one that keeps the 2.6.1 notice and patch but not the
        # composed version.
        with self.assertRaisesRegex(
            AnnouncedReleaseError, "served 2.6.1 composed version"
        ):
            self._activate(VERSIONLESS_RUN, "c" * 64)
        # Rows the base-table check accepts but the loader's view query
        # cannot see are caught after activation, which then rolls back.
        with mock.patch.object(
            dyslipidemia, "verify_announced_material", return_value={}
        ), self.assertRaisesRegex(
            AnnouncedReleaseError, "would not find its composed version"
        ):
            self._activate(ORPHAN_RUN, "d" * 64)
        self.assertEqual(self._served_source(), served)

        # A composite carrying the served version activates, and the loader
        # finds it in the new run.  The fixture has no 2.6.1 normalization,
        # so the 2.6.1 receipt check (tested with the real material in the
        # operator rehearsal) is replaced here; it must still be called.
        with mock.patch.object(
            dyslipidemia, "verify_announced_material", return_value={}
        ) as verify:
            activated = self._activate(CARRYING_RUN, "b" * 64)
        verify.assert_called_once_with(CARRYING_RUN, conninfo=self.pg.dsn)
        self.assertEqual(activated["served"]["release_run_id"], CARRYING_RUN)
        self.assertEqual(
            self._served_source(), {**served, "run_id": CARRYING_RUN}
        )


if __name__ == "__main__":
    unittest.main()
