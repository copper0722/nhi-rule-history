"""Live composition, sealing, activation and rollback on a disposable cluster.

The disposable database is built from the project's own forward migrations.
A small base release (one patch-only notice) and a current publication are
seeded directly; the four fixture notices are then composed into a new run.
The 2.6.1 re-binding path needs the official 2.6.1 ODT and is exercised by
the operator rehearsal, not here.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from nhi_rule_history.announced_notice import sha256_text
from nhi_rule_history.announced_release import (
    AnnouncedReleaseError,
    SEALED_COUNT_TABLES,
    activate_overlay_release,
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


MIGRATIONS = Path(__file__).resolve().parents[1] / "pg" / "migrations"
BASE_RUN = "11111111-1111-5111-8111-111111111111"
PUBLICATION_RUN = "22222222-2222-5222-8222-222222222222"
BASE_NOTICE = "33333333-3333-5333-8333-333333333333"
BASE_EFFECT = "44444444-4444-5444-8444-444444444444"
BASE_PATCH = "55555555-5555-5555-8555-555555555555"
SERVED = {
    "2.1.4.2": "2.1.4.2.Rivaroxaban(如Xarelto)（101/1/1）\n限用於",
    "3.3.28": "3.3.28.Migalastat hydrochloride (如Galafold)：(112/8/1)",
    "8.1.3": "8.1.3.高單位免疫球蛋白(111/2/1)：",
    "4.2": "4.2.血液代用製劑及血液成分製劑 blood substituents and blood components",
    "9.9": "9.9.Fixture：(100/1/1)",
}


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
                        "reference_number": "健保審字第1159999999號",
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

        receipt = load_overlay_release(self.pg.dsn, release)
        self.assertFalse(receipt["replayed"])
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
        with self.assertRaisesRegex(AnnouncedReleaseError, "office rendering"):
            prepare_overlay_release(
                self.pg.dsn, self.notices, require_official_rendering=True
            )
        with self.assertRaisesRegex(AnnouncedReleaseError, "listed twice"):
            prepare_overlay_release(
                self.pg.dsn, [self.notices[0], self.notices[0]]
            )


if __name__ == "__main__":
    unittest.main()
