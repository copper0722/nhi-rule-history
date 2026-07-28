from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import (
    append_jsonl,
    canonical_json_bytes,
    sha256_bytes,
    stable_id,
    write_json,
)
from nhi_rule_history.update.pg_queue import (
    UpdateQueueError,
    admit_legacy_failure_evidence,
    authorize_work_recovery,
)
from tests import test_update_queue_recovery_v2 as recovery_fixture


LEGACY_WORK_ITEM_ID = "12000000-0000-0000-0000-000000000010"
TERMINAL_TRANSITION_ID = "12000000-0000-0000-0000-000000000025"
BUNDLE_ID = "legacy-fixture-bundle"
BUNDLE_FINGERPRINT = "1" * 64
MANIFEST_SHA256 = "2" * 64
PROMPT_SHA256 = "3" * 64
JOB_FINGERPRINT = "4" * 64
METHOD_VERSION = "nhi-rule-history-source-proposal/1.0.0"
NEW_PROMPT_SHA256 = "5" * 64
VERIFIER_CODE_IDENTITY = "9" * 64


def legacy_attempts() -> tuple[dict[str, object], dict[str, object]]:
    primary_id = stable_id(
        "nhi-worker-attempt",
        BUNDLE_ID,
        PROMPT_SHA256,
        "primary",
        "legacy-primary",
    )
    fallback_id = stable_id(
        "nhi-worker-attempt",
        BUNDLE_ID,
        PROMPT_SHA256,
        "fallback",
        "legacy-fallback",
    )
    primary: dict[str, object] = {
        "schema": "nhi-rule-history/worker-attempt/v1",
        "attempt_id": primary_id,
        "role": "primary",
        "worker_id": "legacy-primary",
        "runtime_id": "legacy-runtime",
        "provider": "legacy-provider",
        "model": "legacy-model",
        "prompt_version": METHOD_VERSION,
        "prompt_sha256": PROMPT_SHA256,
        "started_at": "2026-07-27T01:00:00+00:00",
        "completed_at": "2026-07-27T01:00:01+00:00",
        "status": "execution_failed",
        "primary_attempt_id": None,
        "fallback_reason": None,
        "exit_code": 1,
        "output_sha256": "6" * 64,
        "stderr_sha256": "7" * 64,
        "validation_error_code": None,
    }
    fallback: dict[str, object] = {
        "schema": "nhi-rule-history/worker-attempt/v1",
        "attempt_id": fallback_id,
        "role": "fallback",
        "worker_id": "legacy-fallback",
        "runtime_id": "legacy-runtime",
        "provider": "legacy-provider",
        "model": "legacy-model",
        "prompt_version": METHOD_VERSION,
        "prompt_sha256": PROMPT_SHA256,
        "started_at": "2026-07-27T01:00:01+00:00",
        "completed_at": "2026-07-27T01:00:02+00:00",
        "status": "timeout",
        "primary_attempt_id": primary_id,
        "fallback_reason": "execution_failed",
        "exit_code": None,
        "output_sha256": None,
        "stderr_sha256": "8" * 64,
        "validation_error_code": None,
    }
    return primary, fallback


class LegacyFailureBridgeApiValidationTests(unittest.TestCase):
    def test_noncanonical_or_unlinked_files_fail_before_database_access(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / JOB_FINGERPRINT
            run_dir.mkdir()
            primary, fallback = legacy_attempts()
            attempts_path = run_dir / "attempts.jsonl"
            append_jsonl(attempts_path, primary)
            append_jsonl(attempts_path, fallback)
            receipt = {
                "schema": "nhi-rule-history/worker-run/v2",
                "job_fingerprint": JOB_FINGERPRINT,
                "bundle_id": BUNDLE_ID,
                "bundle_fingerprint": BUNDLE_FINGERPRINT,
                "manifest_sha256": MANIFEST_SHA256,
                "attempts_sha256": sha256_bytes(attempts_path.read_bytes()),
                "prompt_sha256": PROMPT_SHA256,
                "status": "failed",
                "attempt_count": 2,
                "selected_attempt_id": None,
            }
            receipt_path = run_dir / "failure-receipt.json"
            write_json(receipt_path, receipt)
            with self.assertRaisesRegex(
                UpdateQueueError, "worker job fingerprint"
            ):
                admit_legacy_failure_evidence(
                    "postgresql://example.invalid/test",
                    work_item_id=LEGACY_WORK_ITEM_ID,
                    terminal_transition_id=TERMINAL_TRANSITION_ID,
                    failure_receipt_path=receipt_path,
                    attempts_path=attempts_path,
                    failure_receipt_relative_path=(
                        "legacy-candidates/wrong/failure-receipt.json"
                    ),
                    attempts_relative_path=(
                        "legacy-candidates/wrong/attempts.jsonl"
                    ),
                    verifier_code_identity=VERIFIER_CODE_IDENTITY,
                    actor_kind="fixture-controller",
                )


class LegacyFailureBridgeLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        cls.files = tempfile.TemporaryDirectory(
            prefix="nhi-legacy-failure-files-"
        )
        cls.file_root = Path(cls.files.name)
        cls.run_dir = cls.file_root / JOB_FINGERPRINT
        cls.run_dir.mkdir()
        cls.primary, cls.fallback = legacy_attempts()
        cls.attempts_path = cls.run_dir / "attempts.jsonl"
        append_jsonl(cls.attempts_path, cls.primary)
        append_jsonl(cls.attempts_path, cls.fallback)
        cls.attempts_sha256 = sha256_bytes(cls.attempts_path.read_bytes())
        cls.receipt = {
            "schema": "nhi-rule-history/worker-run/v2",
            "job_fingerprint": JOB_FINGERPRINT,
            "bundle_id": BUNDLE_ID,
            "bundle_fingerprint": BUNDLE_FINGERPRINT,
            "manifest_sha256": MANIFEST_SHA256,
            "attempts_sha256": cls.attempts_sha256,
            "prompt_sha256": PROMPT_SHA256,
            "status": "failed",
            "attempt_count": 2,
            "selected_attempt_id": None,
        }
        cls.receipt_path = cls.run_dir / "failure-receipt.json"
        write_json(cls.receipt_path, cls.receipt)
        cls.receipt_sha256 = sha256_bytes(cls.receipt_path.read_bytes())
        cls.relative_parent = f"legacy-candidates/{JOB_FINGERPRINT}"
        cls.receipt_relative = (
            f"{cls.relative_parent}/failure-receipt.json"
        )
        cls.attempts_relative = f"{cls.relative_parent}/attempts.jsonl"
        cls.terminal_evidence = {
            "failure_receipt_relative_path": cls.receipt_relative,
            "failure_receipt_sha256": cls.receipt_sha256,
            "worker_attempts": [
                {
                    "role": attempt["role"],
                    "status": attempt["status"],
                    "worker_id": attempt["worker_id"],
                    "attempt_id": attempt["attempt_id"],
                }
                for attempt in (cls.primary, cls.fallback)
            ],
        }
        for migration in (
            recovery_fixture.OPS_FORWARD,
            recovery_fixture.CANDIDATE_FORWARD,
            recovery_fixture.QUEUE_FORWARD,
            recovery_fixture.RECOVERY_FORWARD,
        ):
            cls.pg.psql(file=migration)
        cls.pg.psql(
            command=recovery_fixture.RecoveryMigrationLiveTests.fixture_sql()
        )
        cls.pg.psql(command=cls.legacy_fixture_sql())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()
        cls.files.cleanup()

    @classmethod
    def legacy_fixture_sql(cls) -> str:
        evidence_json = json.dumps(
            cls.terminal_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_sha = sha256_bytes(
            canonical_json_bytes(cls.terminal_evidence)
        )
        return f"""
INSERT INTO nhi_rule_history_update_ops.feed_item_observation (
  feed_observation_id, item_index, item_fingerprint, guid_raw,
  title_raw, link_raw, published_raw, description_raw, raw_item_sha256
) VALUES (
  '10000000-0000-0000-0000-000000000004', 2, repeat('e',64),
  'legacy-failure-guid', 'legacy failure fixture rule',
  'https://example.invalid/legacy-failure-rule', NULL, '', repeat('f',64)
);
INSERT INTO nhi_rule_history_update_queue.rss_work_item (
  work_item_id, rss_identity_fingerprint, item_identity_kind,
  item_identity_value, source_feed_url, guid_raw,
  first_feed_observation_id, first_item_index, first_item_fingerprint,
  first_title_raw, first_link_raw, first_observed_at
) VALUES (
  '{LEGACY_WORK_ITEM_ID}', repeat('e',64), 'rss_guid',
  'legacy-failure-guid', 'https://example.invalid/feed.xml',
  'legacy-failure-guid',
  '10000000-0000-0000-0000-000000000004', 2, repeat('e',64),
  'legacy failure fixture rule',
  'https://example.invalid/legacy-failure-rule',
  '2026-07-27 00:01:00+00'
);
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES
(
  '{LEGACY_WORK_ITEM_ID}', 1,
  '12000000-0000-0000-0000-000000000020',
  NULL, 'observed', 'fixture', repeat('1',64),
  '{{"event":"observed"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:59:00+00'
),
(
  '{LEGACY_WORK_ITEM_ID}', 2,
  '12000000-0000-0000-0000-000000000021',
  'observed', 'selected', 'fixture', repeat('2',64),
  '{{"event":"selected"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:59:01+00'
),
(
  '{LEGACY_WORK_ITEM_ID}', 3,
  '12000000-0000-0000-0000-000000000022',
  'selected', 'acquired', 'fixture', repeat('3',64),
  '{{"event":"acquired"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:59:02+00'
),
(
  '{LEGACY_WORK_ITEM_ID}', 4,
  '12000000-0000-0000-0000-000000000023',
  'acquired', 'corpus_registered', 'fixture', repeat('4',64),
  '{{"event":"corpus"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:59:03+00'
),
(
  '{LEGACY_WORK_ITEM_ID}', 5,
  '12000000-0000-0000-0000-000000000024',
  'corpus_registered', 'proposal_running', 'fixture', repeat('5',64),
  '{{"event":"proposal"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 01:00:00+00'
),
(
  '{LEGACY_WORK_ITEM_ID}', 6, '{TERMINAL_TRANSITION_ID}',
  'proposal_running', 'failed_terminal', 'fixture', '{evidence_sha}',
  $evidence${evidence_json}$evidence$::jsonb,
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 01:00:03+00'
);
"""

    def test_exact_file_evidence_admits_and_authorizes_without_uuid_attempts(
        self,
    ) -> None:
        worker_attempt_count_before = self.pg.psql(
            command=(
                "SELECT count(*) "
                "FROM nhi_rule_history_update_ops.worker_attempt"
            )
        ).stdout.strip()
        tampered_receipt_path = (
            self.file_root / "tampered-failure-receipt.json"
        )
        write_json(
            tampered_receipt_path,
            {**self.receipt, "controller_note": "different immutable bytes"},
        )
        with self.assertRaisesRegex(
            Exception, "exactly match terminal controller evidence"
        ):
            admit_legacy_failure_evidence(
                self.pg.dsn,
                work_item_id=LEGACY_WORK_ITEM_ID,
                terminal_transition_id=TERMINAL_TRANSITION_ID,
                failure_receipt_path=tampered_receipt_path,
                attempts_path=self.attempts_path,
                failure_receipt_relative_path=self.receipt_relative,
                attempts_relative_path=self.attempts_relative,
                verifier_code_identity=VERIFIER_CODE_IDENTITY,
                actor_kind="fixture-controller",
            )
        admitted = admit_legacy_failure_evidence(
            self.pg.dsn,
            work_item_id=LEGACY_WORK_ITEM_ID,
            terminal_transition_id=TERMINAL_TRANSITION_ID,
            failure_receipt_path=self.receipt_path,
            attempts_path=self.attempts_path,
            failure_receipt_relative_path=self.receipt_relative,
            attempts_relative_path=self.attempts_relative,
            verifier_code_identity=VERIFIER_CODE_IDENTITY,
            actor_kind="fixture-controller",
            admitted_at="2026-07-28T02:00:00+00:00",
        )
        self.assertFalse(admitted["replayed"])
        self.assertEqual(
            admitted["attempt_id_scheme"], "sha256_hex_v1"
        )
        self.assertEqual(
            admitted["attempt_id_origin"],
            "immutable_worker_attempt_jsonl",
        )
        self.assertEqual(
            admitted["verifier_code_identity"], VERIFIER_CODE_IDENTITY
        )
        self.assertEqual(
            set(admitted["attempt_ids"]),
            {
                self.primary["attempt_id"],
                self.fallback["attempt_id"],
            },
        )
        replay = admit_legacy_failure_evidence(
            self.pg.dsn,
            work_item_id=LEGACY_WORK_ITEM_ID,
            terminal_transition_id=TERMINAL_TRANSITION_ID,
            failure_receipt_path=self.receipt_path,
            attempts_path=self.attempts_path,
            failure_receipt_relative_path=self.receipt_relative,
            attempts_relative_path=self.attempts_relative,
            verifier_code_identity=VERIFIER_CODE_IDENTITY,
            actor_kind="fixture-controller",
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["admission_id"], admitted["admission_id"])

        authorization = authorize_work_recovery(
            self.pg.dsn,
            work_item_id=LEGACY_WORK_ITEM_ID,
            prior_generation=1,
            source_bundle_uid=BUNDLE_ID,
            source_manifest_sha256=MANIFEST_SHA256,
            prior_method_version=METHOD_VERSION,
            new_method_version="nhi-rule-history-source-proposal/2.0.0",
            prior_semantic_prompt_fingerprint=PROMPT_SHA256,
            new_semantic_prompt_fingerprint=NEW_PROMPT_SHA256,
            legacy_failure_admission_id=admitted["admission_id"],
            decision_basis_id="legacy-failure-recovery-1",
            reason="bounded controller-approved method change",
            actor_kind="fixture-controller",
            authorized_at="2026-07-28T02:01:00+00:00",
        )
        self.assertEqual(authorization["generation"], 2)
        self.assertEqual(authorization["current_state"], "retry_pending")
        self.assertEqual(
            authorization["legacy_failure_admission_id"],
            admitted["admission_id"],
        )
        self.assertEqual(authorization["superseded_attempt_ids"], [])

        persisted = self.pg.psql(
            command=f"""
SELECT
  (SELECT count(*)
   FROM nhi_rule_history_update_ops.worker_attempt),
  (SELECT count(*)
   FROM nhi_rule_history_update_queue.legacy_failure_attempt_evidence
   WHERE admission_id = '{admitted["admission_id"]}'),
  (SELECT count(*)
   FROM nhi_rule_history_update_queue.recovery_superseded_attempt
   WHERE authorization_id = '{authorization["authorization_id"]}'),
  (SELECT legacy_failure_admission_id::text
   FROM nhi_rule_history_update_queue.work_recovery_authorization
   WHERE authorization_id = '{authorization["authorization_id"]}'),
  (SELECT string_agg(
     attempt_id_scheme || ':' || attempt_id_origin,
     ',' ORDER BY route
   )
   FROM nhi_rule_history_update_queue.legacy_failure_attempt_evidence
   WHERE admission_id = '{admitted["admission_id"]}'),
  (SELECT admission_payload_sha256::text
   FROM nhi_rule_history_update_queue.legacy_failure_evidence
   WHERE admission_id = '{admitted["admission_id"]}');
"""
        ).stdout.strip()
        self.assertEqual(
            persisted,
            (
                f"{worker_attempt_count_before}|2|0|"
                f"{admitted['admission_id']}|"
                "sha256_hex_v1:immutable_worker_attempt_jsonl,"
                "sha256_hex_v1:immutable_worker_attempt_jsonl|"
                f"{admitted['admission_payload_sha256']}"
            ),
        )

        acl = self.pg.psql(
            command="""
SELECT
  has_table_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_update_queue.legacy_failure_evidence',
    'INSERT'
  ),
  has_function_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'EXECUTE'
  );
"""
        ).stdout.strip()
        self.assertEqual(acl, "f|t")

        rollback = self.pg.psql(
            file=recovery_fixture.RECOVERY_ROLLBACK, check=False
        )
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("ledger is nonempty", rollback.stderr)


if __name__ == "__main__":
    unittest.main()
