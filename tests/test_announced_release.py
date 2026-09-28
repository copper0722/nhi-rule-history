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
import unittest
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from nhi_rule_history import announced_dyslipidemia as dyslipidemia
from nhi_rule_history.announced_notice import (
    CORPUS_BUNDLE_SCHEMA,
    ODT_MEDIA_TYPE,
    NoticeAttachment,
    NoticeBundle,
    parse_comparison_document,
    read_odt_document,
    sha256_text,
)
from nhi_rule_history.announced_release import (
    AnnouncedReleaseError,
    SEALED_COUNT_TABLES,
    activate_overlay_release,
    compose_overlay_release,
    load_overlay_release,
    prepare_overlay_release,
    read_base_chain,
    rollback_overlay_release,
    _connect,
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
):
    """Parse a comparison table written in the parser's own table grammar.

    ``declared_sha256`` names the artifact the text claims to come from, so a
    re-parse of the same source with different text can be simulated.
    """

    payload = fixture_odt(notice_fixture._comparison(rows, effective=effective))
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
    return parse_comparison_document(bundle, attachment, document)


def _rendering(notice, *codes: str) -> str:
    """An office rendering that shows only the named clauses' revised text."""

    return "\n".join(
        item.text
        for clause in notice.clauses
        if not codes or clause.clause_code in codes
        for item in clause.revised
    ) + "\n"


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

    def test_held_back_notice_enters_the_run_with_pending_effects(self) -> None:
        # 2026-09-28 finding M1: a notice whose every clause was held back
        # was dropped from the run, so served data showed no 10-01 change.
        held = self._by_reference("1150672509")
        reference = held.bundle.reference_number
        mismatch = {reference: "與對照表無關的另一份文字\n"}
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
        self.assertIn("independent office rendering", note)
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
            HELD_REFERENCE: "與對照表無關的另一份文字\n",
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
            [item["reference_number"] for item in unchanged.carried_notices],
            [SUPERSEDE_REFERENCE, HELD_REFERENCE],
        )
        self.assertEqual(unchanged.status, "no_change_with_holds")
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
                    "supersede",
                    "served clause 9.2 is not re-projected byte-identically",
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
                },
                {
                    "reference_number": HELD_REFERENCE,
                    "bundle": f"gov_{HELD_REFERENCE}",
                    "effective_on": "2026-10-01",
                    "notice_id": held.notice_id,
                    "served_clauses": [],
                    "added_clauses": ["3.3.28"],
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



def _write_bundle(
    root: Path, reference: str, content_xml: bytes, *, corrupt: bool = False
) -> str:
    """Write a registered-bundle layout the CLI reads; return its path."""

    relative = f"2026/gov_{reference}"
    bundle = root / relative
    bundle.mkdir(parents=True)
    payload = fixture_odt(content_xml)
    raw = f"# 公告修訂藥品給付規定。\n\n## 公告事項\n\n一、{reference}。\n"
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
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    if corrupt:
        (bundle / "attachment-000.odt").write_bytes(payload + b"\0")
    return relative


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
            (cls.root / cls.held / "attachment-000.odt").read_bytes(): (
                "與對照表無關的另一份文字\n"
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
            "libreoffice_text_export",
            side_effect=lambda payload, **_: self.renderings.get(payload),
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


if __name__ == "__main__":
    unittest.main()
