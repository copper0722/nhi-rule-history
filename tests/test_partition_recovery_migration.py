from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from nhi_rule_history.contracts import canonical_json_bytes, sha256_bytes
from nhi_rule_history.update.pg_queue import (
    PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT,
    admit_partition_recovery,
    authorize_partition_recovery,
    authorize_work_recovery,
    close_partition_recovery_generation,
    consume_partition_recovery_dispatch,
    finish_partition_recovery_route,
    partition_recovery_output_namespace,
    reserve_partition_recovery_route,
    verify_partition_recovery_admission,
)
from tests import test_partition_recovery_api_cli as _api_contract_tests
from tests import test_update_queue_recovery_v2 as _recovery_tests
from tests.test_update_queue_recovery_v2 import (
    CANDIDATE_FORWARD,
    OPS_FORWARD,
    PARTITION_WORK_ITEM_ID,
    QUEUE_FORWARD,
    RECOVERY_FORWARD,
    DisposablePostgres,
)


ROOT = Path(__file__).resolve().parents[1]
FORWARD = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_partition_recovery_a_plus.sql"
)
ROLLBACK = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-28_nhi_rule_history_partition_recovery_a_plus.rollback.sql"
)

ADMISSION_ID = "41000000-0000-0000-0000-000000000001"
AUTHORIZATION_ID = "41000000-0000-0000-0000-000000000002"
INITIAL_TRANSITION_ID = "41000000-0000-0000-0000-000000000003"
CLAIM_ID = "41000000-0000-0000-0000-000000000004"
RECOVERY_JOB_ID = "41000000-0000-0000-0000-000000000005"
LEASE_ID = "41000000-0000-0000-0000-000000000006"
RESERVATION_ID = "41000000-0000-0000-0000-000000000007"
TERMINAL_TRANSITION_ID = "41000000-0000-0000-0000-000000000008"
TERMINAL_EVIDENCE_ID = "41000000-0000-0000-0000-000000000009"
TERMINAL_RECEIPT_ID = "41000000-0000-0000-0000-00000000000a"
TERMINAL_EVIDENCE_CONTRACT = (
    "nhi-rule-history/partition-recovery-terminal-evidence/v1"
)


def _sha256_uuid_v8(label: str, *parts: str) -> str:
    digest = bytearray(
        hashlib.sha256("\x1f".join((label, *parts)).encode("utf-8")).digest()[
            :16
        ]
    )
    digest[6] = (digest[6] & 0x0F) | 0x80
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _terminal_evidence(
    to_state: str,
    **variant: object,
) -> dict[str, object]:
    return {
        "schema": TERMINAL_EVIDENCE_CONTRACT,
        "dispatch_claim_id": CLAIM_ID,
        "work_item_id": PARTITION_WORK_ITEM_ID,
        "generation": 2,
        "authorization_id": AUTHORIZATION_ID,
        "admission_id": ADMISSION_ID,
        "to_state": to_state,
        "auto_promotion_enabled": False,
        **variant,
    }


def _terminal_identity(
    evidence: dict[str, object],
    *,
    source_job_id: str | None = None,
    bundle_receipt_id: str | None = None,
    candidate_proposal_id: str | None = None,
) -> dict[str, str]:
    evidence_sha256 = sha256_bytes(canonical_json_bytes(evidence))
    to_state = str(evidence["to_state"])
    work_item_id = str(evidence["work_item_id"])
    generation = str(evidence["generation"])
    dispatch_claim_id = str(evidence["dispatch_claim_id"])
    authorization_id = str(evidence["authorization_id"])
    admission_id = str(evidence["admission_id"])
    terminal_receipt_id = _sha256_uuid_v8(
        "partition-recovery-terminal-receipt",
        work_item_id,
        generation,
        to_state,
        evidence_sha256,
    )
    transition_material = {
        "dispatch_claim_id": dispatch_claim_id,
        "work_item_id": work_item_id,
        "generation": int(generation),
        "authorization_id": authorization_id,
        "admission_id": admission_id,
        "to_state": to_state,
        "evidence_contract": TERMINAL_EVIDENCE_CONTRACT,
        "evidence_sha256": evidence_sha256,
        "terminal_receipt_id": terminal_receipt_id,
        "source_job_id": source_job_id,
        "bundle_receipt_id": bundle_receipt_id,
        "candidate_proposal_id": candidate_proposal_id,
    }
    transition_id = _sha256_uuid_v8(
        "partition-recovery-transition",
        sha256_bytes(canonical_json_bytes(transition_material)),
    )
    transition_evidence_id = _sha256_uuid_v8(
        "partition-recovery-transition-evidence",
        transition_id,
        terminal_receipt_id,
        evidence_sha256,
    )
    return {
        "evidence_sha256": evidence_sha256,
        "terminal_receipt_id": terminal_receipt_id,
        "transition_id": transition_id,
        "transition_evidence_id": transition_evidence_id,
    }


def _base_cluster(*, fixture: bool = False) -> DisposablePostgres:
    pg = DisposablePostgres()
    for migration in (
        OPS_FORWARD,
        CANDIDATE_FORWARD,
        QUEUE_FORWARD,
        RECOVERY_FORWARD,
    ):
        pg.psql(file=migration)
    if fixture:
        pg.psql(
            command=_recovery_tests.RecoveryMigrationLiveTests.fixture_sql()
        )
    return pg


def _prepare_consumed_dispatch() -> tuple[DisposablePostgres, dict[str, str]]:
    pg = _base_cluster(fixture=True)
    try:
        pg.psql(file=FORWARD)
        pg.psql(
            command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id,
  recorded_at
) VALUES (
  '{PARTITION_WORK_ITEM_ID}', 6,
  '11000000-0000-0000-0000-000000000025',
  'proposal_running', 'partition_required', 'fixture',
  repeat('c',64), '{{"event":"partition-required"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:05+00'
);
"""
        )
        with psycopg.connect(pg.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
SELECT
  terminal_transition_id, terminal_transition_sequence,
  terminal_state, terminal_evidence_sha256, transition_count,
  transition_rows, old_job_fingerprint
FROM
  nhi_rule_history_partition_recovery.
    generation_one_chain_receipt(%s::uuid)
""",
                    (PARTITION_WORK_ITEM_ID,),
                )
                receipt = cursor.fetchone()
                assert receipt is not None
                transitions = receipt[5]
                ordered_hash = sha256_bytes(
                    canonical_json_bytes(transitions)
                )
                rowset_hash = sha256_bytes(
                    canonical_json_bytes(
                        sorted(
                            transitions,
                            key=lambda row: row["transition_id"],
                        )
                    )
                )
                hash_values = {
                    letter: letter * 64
                    for letter in "def0123456789abcdef"
                }
                new_job_fingerprint = "0f" * 32
                payload = {
                    "canonical_encoding_contract": (
                        "nhi-rule-history/canonical-json-bytes/no-float/v1"
                    ),
                    "generation_1": {
                        "work_item_id": PARTITION_WORK_ITEM_ID,
                        "prior_generation": 1,
                        "terminal_transition_id": str(receipt[0]),
                        "terminal_transition_sequence": receipt[1],
                        "terminal_state": receipt[2],
                        "terminal_evidence_sha256": receipt[3],
                        "transition_count": receipt[4],
                        "transitions": transitions,
                        "ordered_chain_sha256": ordered_hash,
                        "rowset_fingerprint": rowset_hash,
                        "old_job_fingerprint": receipt[6],
                        "old_partition_receipt": {
                            "sha256": hash_values["d"]
                        },
                        "old_suitability_receipt": {
                            "sha256": hash_values["e"]
                        },
                        "worker_call_count": 0,
                        "worker_attempt_count": 0,
                        "candidate_count": 0,
                        "route_attempt_count": 0,
                    },
                    "source_evidence": {
                        "reuse_existing_bundle": True,
                        "repoll_allowed": False,
                        "reacquire_allowed": False,
                        "new_corpus_registration_allowed": False,
                        "source_bundle": {
                            "bundle_id": "source-bundle-fixture",
                            "manifest_sha256": hash_values["f"],
                        },
                        "corpus_bundle": {
                            "bundle_id": "corpus-bundle-fixture",
                            "manifest_sha256": hash_values["0"],
                        },
                        "sealed_packet": {
                            "manifest_sha256": hash_values["1"],
                            "byte_count": 123,
                            "ordered_artifact_sha256_set_digest": (
                                hash_values["2"]
                            ),
                        },
                    },
                    "execution_delta": {
                        "old_suitability_contract": "suitability/v1",
                        "new_suitability_contract": "suitability/v2",
                        "old_fingerprint_domain": "worker/v1",
                        "new_fingerprint_domain": "worker/v2",
                        "new_job_fingerprint": new_job_fingerprint,
                        "suitability_v2_schema_sha256": hash_values["4"],
                        "suitability_v2_receipt_sha256": hash_values["5"],
                        "verifier_contract_version": "verifier/v2",
                        "verifier_code_commit": "6" * 40,
                        "verifier_config_sha256": hash_values["7"],
                        "verifier_executable_sha256": hash_values["8"],
                        "execution_contract_version": "execution/v2",
                        "execution_contract_sha256": hash_values["9"],
                        "route_policy_sha256": hash_values["a"],
                        "suitability_preflight": {
                            "decision": "suitable",
                            "designation_candidates": ["drug"],
                            "effective_designation_candidates": ["drug"],
                            "collapsed_parent_designations": [],
                            "reason_codes": [],
                        },
                    },
                    "worker_semantics": {
                        "prompt_version": "prompt/v1",
                        "prompt_sha256": hash_values["b"],
                        "semantic_prompt_changed": False,
                        "execution_contract_changed": True,
                    },
                    "governance": {
                        "decision_basis_id": "pro-reconciled-a-plus",
                        "public_repo_commit": "c" * 40,
                        "private_controller_commit": "d" * 40,
                        "migration_sha256": hash_values["e"],
                        "admission_contract_version": "admission/v1",
                        "review_decision_receipt_sha256": hash_values["f"],
                    },
                }
                payload_bytes = canonical_json_bytes(payload)
                payload_hash = sha256_bytes(payload_bytes)
                cursor.execute(
                    "SET ROLE nhi_rule_history_recovery_authorizer"
                )
                cursor.execute(
                    """
SELECT *
FROM
  nhi_rule_history_partition_recovery.admit_partition_recovery(
    %s::uuid, %s, %s::jsonb, %s::bytea, 'fixture-operator',
    '2099-01-01T00:00:00+00'::timestamptz
  )
""",
                    (
                        ADMISSION_ID,
                        payload_hash,
                        Jsonb(payload),
                        payload_bytes,
                    ),
                )
                cursor.fetchone()
                cursor.execute(
                    """
SELECT *
FROM
  nhi_rule_history_partition_recovery.authorize_partition_recovery(
    %s::uuid, %s::uuid, %s::uuid, %s::uuid, 1, 2,
    'dispatch/v1', '2099-01-02T00:00:00+00'::timestamptz,
    'fixture-operator', '2099-01-01T00:00:00+00'::timestamptz
  )
""",
                    (
                        AUTHORIZATION_ID,
                        INITIAL_TRANSITION_ID,
                        ADMISSION_ID,
                        PARTITION_WORK_ITEM_ID,
                    ),
                )
                cursor.fetchone()
                cursor.execute("RESET ROLE")
                cursor.execute(
                    "SET ROLE nhi_rule_history_update_queue_runtime"
                )
                cursor.execute(
                    """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid, 'dispatch/v1',
      %s, %s, %s, %s, %s, %s,
      %s::uuid, %s::uuid, 'fixture-owner', 180,
      '2099-01-01T00:03:00+00'::timestamptz,
      '2099-01-01T00:00:00+00'::timestamptz
    )
""",
                    (
                        CLAIM_ID,
                        PARTITION_WORK_ITEM_ID,
                        AUTHORIZATION_ID,
                        ADMISSION_ID,
                        payload_hash,
                        hash_values["1"],
                        hash_values["5"],
                        new_job_fingerprint,
                        hash_values["b"],
                        hash_values["a"],
                        RECOVERY_JOB_ID,
                        LEASE_ID,
                    ),
                )
                cursor.fetchone()
                cursor.execute("RESET ROLE")
        return pg, {
            **hash_values,
            "payload_hash": payload_hash,
            "job_fingerprint": new_job_fingerprint,
            "namespace": (
                f"partition-recovery/{PARTITION_WORK_ITEM_ID}/"
                f"generation-2/{new_job_fingerprint}"
            ),
        }
    except Exception:
        pg.close()
        raise


class PartitionRecoveryMigrationStaticTests(unittest.TestCase):
    def test_forward_and_rollback_are_stage_only_and_fail_closed(self) -> None:
        forward = FORWARD.read_text(encoding="utf-8")
        rollback = ROLLBACK.read_text(encoding="utf-8")
        self.assertRegex(forward, r"(?m)^BEGIN;$")
        self.assertRegex(forward, r"(?m)^COMMIT;$")
        self.assertIn("NOLOGIN", forward)
        self.assertIn("NOINHERIT", forward)
        self.assertIn("SECURITY DEFINER", forward)
        self.assertIn("SET search_path = pg_catalog", forward)
        self.assertIn("generation_one_chain_matches_payload", forward)
        self.assertIn("fresh route reservation requires", forward)
        self.assertIn("p_recovery_job_id uuid", forward)
        self.assertIn("finished_route_statuses jsonb", forward)
        self.assertNotRegex(forward, r"\b(?:hmj|hm4|cm1)\b")
        self.assertNotRegex(forward, r"\btw_drug\.")
        self.assertEqual(rollback.count("\nCOMMIT;"), 2)
        self.assertIn("dependent_objects_still_exist", rollback)
        self.assertNotIn(" CASCADE", rollback)


class PartitionRecoveryMigrationLiveTests(unittest.TestCase):
    def test_canonical_json_and_sha256_uuid_match_python_bytes(self) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            nested = {
                "中": "臺灣\n引號\"、反斜線\\、控制\u0001",
                "a": [0, -7, True, None, {"z": "尾", "β": "值"}],
                "𐀀": {"array": [["巢狀"], {"b": 2, "a": 1}]},
            }
            expected_bytes = canonical_json_bytes(nested)
            staged = _api_contract_tests.terminal_evidence(
                "staged_needs_review",
                candidate_receipt_sha256="7" * 64,
                candidate_state="needs_review",
                selected_worker_role="primary",
                worker_calls=1,
                finished_routes={"primary": "8" * 64},
                canonical_history_writes=0,
            )
            expected_staged_hash = sha256_bytes(
                canonical_json_bytes(staged)
            )
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
SELECT
  nhi_rule_history_partition_recovery.canonical_jsonb_text(
    %s::jsonb
  ) || E'\\n',
  nhi_rule_history_partition_recovery.canonical_jsonb_sha256(
    %s::jsonb
  ),
  nhi_rule_history_partition_recovery.canonical_jsonb_sha256(
    %s::jsonb
  ),
  nhi_rule_history_partition_recovery.sha256_uuid_v8(
    'partition-recovery-terminal-receipt',
    %s, '2', 'staged_needs_review', %s
  )::text
""",
                        (
                            Jsonb(nested),
                            Jsonb(nested),
                            Jsonb(staged),
                            _api_contract_tests.WORK_ITEM_ID,
                            expected_staged_hash,
                        ),
                    )
                    row = cursor.fetchone()
                    assert row is not None
                    self.assertEqual(row[0].encode("utf-8"), expected_bytes)
                    self.assertEqual(row[1], sha256_bytes(expected_bytes))
                    self.assertEqual(
                        row[2],
                        "0d5953b153bf0cf39ae0b212e684253a"
                        "b2b4ecb21445af25455fb77837bdb17c",
                    )
                    self.assertEqual(
                        row[3],
                        "7907d9e4-0c99-883a-b69a-b3f65612a540",
                    )
                    cursor.execute("SAVEPOINT non_integer")
                    with self.assertRaisesRegex(
                        psycopg.Error, "forbids non-integer"
                    ):
                        cursor.execute(
                            """
SELECT
  nhi_rule_history_partition_recovery.
    canonical_jsonb_sha256('{"float":1.5}'::jsonb)
"""
                        )
                    cursor.execute("ROLLBACK TO SAVEPOINT non_integer")
        finally:
            pg.close()

    def test_direct_sql_rejects_forged_terminal_and_replays_stored_time(
        self,
    ) -> None:
        pg, _hashes = _prepare_consumed_dispatch()
        evidence = _terminal_evidence(
            "failed_terminal",
            reason_code="restart_before_model_reservation",
            failure_code="RECOVERY_RESTART_BEFORE_MODEL_RESERVATION",
            preexisting_output_namespace=False,
            generation_state="retry_pending",
            finished_route_statuses=[],
            open_route_reconciled_as_execution_unknown=None,
            worker_reinvocation=False,
            automatic_retry=False,
            automatic_fallback=False,
        )
        identity = _terminal_identity(evidence)
        close_sql = """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, %s::uuid, NULL, NULL, %s::timestamptz
    )
"""

        def close_parameters(
            candidate_evidence: dict[str, object],
            candidate_identity: dict[str, str],
            *,
            evidence_contract: str = TERMINAL_EVIDENCE_CONTRACT,
            evidence_sha256: str | None = None,
            transition_id: str | None = None,
            transition_evidence_id: str | None = None,
            terminal_receipt_id: str | None = None,
            source_job_id: str | None = None,
            caller_time: str = "1900-01-01T00:00:00+00:00",
        ) -> tuple[object, ...]:
            return (
                transition_id or candidate_identity["transition_id"],
                transition_evidence_id
                or candidate_identity["transition_evidence_id"],
                terminal_receipt_id
                or candidate_identity["terminal_receipt_id"],
                PARTITION_WORK_ITEM_ID,
                AUTHORIZATION_ID,
                ADMISSION_ID,
                evidence_contract,
                evidence_sha256 or candidate_identity["evidence_sha256"],
                Jsonb(candidate_evidence),
                source_job_id,
                caller_time,
            )

        try:
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    invalid_calls: list[tuple[str, tuple[object, ...]]] = [
                        (
                            "contract",
                            close_parameters(
                                evidence,
                                identity,
                                evidence_contract="forged/terminal/v1",
                            ),
                        ),
                        (
                            "hash",
                            close_parameters(
                                evidence,
                                identity,
                                evidence_sha256="f" * 64,
                            ),
                        ),
                        (
                            "terminal_id",
                            close_parameters(
                                evidence,
                                identity,
                                terminal_receipt_id=TERMINAL_RECEIPT_ID,
                            ),
                        ),
                        (
                            "transition_id",
                            close_parameters(
                                evidence,
                                identity,
                                transition_id=TERMINAL_TRANSITION_ID,
                            ),
                        ),
                        (
                            "transition_evidence_id",
                            close_parameters(
                                evidence,
                                identity,
                                transition_evidence_id=TERMINAL_EVIDENCE_ID,
                            ),
                        ),
                    ]
                    mutated_cases = {
                        "schema": {
                            **evidence,
                            "schema": "forged/terminal/v1",
                        },
                        "core": {**evidence, "generation": 3},
                        "extra": {**evidence, "unexpected": "smuggled"},
                        "failure_pair": {
                            **evidence,
                            "failure_code": "WORKER_EXECUTION_UNKNOWN",
                        },
                        "json_null": {
                            **evidence,
                            "worker_reinvocation": None,
                        },
                    }
                    invalid_calls.extend(
                        (
                            label,
                            close_parameters(
                                material, _terminal_identity(material)
                            ),
                        )
                        for label, material in mutated_cases.items()
                    )
                    source_identity = _terminal_identity(
                        evidence, source_job_id=RECOVERY_JOB_ID
                    )
                    invalid_calls.append(
                        (
                            "reason_source",
                            close_parameters(
                                evidence,
                                source_identity,
                                source_job_id=RECOVERY_JOB_ID,
                            ),
                        )
                    )
                    for label, parameters in invalid_calls:
                        with self.subTest(label=label):
                            cursor.execute(f"SAVEPOINT reject_{label}")
                            with self.assertRaises(psycopg.Error):
                                cursor.execute(close_sql, parameters)
                            cursor.execute(
                                f"ROLLBACK TO SAVEPOINT reject_{label}"
                            )
                            cursor.execute(
                                f"RELEASE SAVEPOINT reject_{label}"
                            )
                    cursor.execute(
                        close_sql,
                        close_parameters(evidence, identity),
                    )
                    first = cursor.fetchone()
                    assert first is not None
                    self.assertFalse(first[4])
                    self.assertEqual(first[6], uuid.UUID(
                        identity["transition_evidence_id"]
                    ))
                    self.assertEqual(
                        first[7], identity["evidence_sha256"]
                    )
                    stored_time = first[5]
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
SELECT byte_count, evidence_sha256
FROM
  nhi_rule_history_partition_recovery.
    generation_transition_evidence
WHERE transition_evidence_id = %s::uuid
""",
                        (identity["transition_evidence_id"],),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        (
                            len(canonical_json_bytes(evidence)),
                            identity["evidence_sha256"],
                        ),
                    )
            with psycopg.connect(pg.dsn) as replay_connection:
                with replay_connection.cursor() as cursor:
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    cursor.execute(
                        close_sql,
                        close_parameters(
                            evidence,
                            identity,
                            caller_time="2200-01-01T00:00:00+00:00",
                        ),
                    )
                    replay = cursor.fetchone()
                    assert replay is not None
                    self.assertTrue(replay[4])
                    self.assertEqual(replay[5], stored_time)
                    self.assertEqual(
                        str(replay[6]),
                        identity["transition_evidence_id"],
                    )
                    self.assertEqual(
                        replay[7], identity["evidence_sha256"]
                    )
        finally:
            pg.close()

    def test_precall_terminal_variants_accept_only_exact_state_shapes(
        self,
    ) -> None:
        suitability_preflight = {
            "decision": "suitable",
            "designation_candidates": ["drug"],
            "effective_designation_candidates": ["drug"],
            "collapsed_parent_designations": [],
            "reason_codes": [],
        }
        for reason_code in (
            "preflight_replay_mismatch",
            "packet_or_contract_tamper",
        ):
            with self.subTest(reason_code=reason_code):
                pg, hashes = _prepare_consumed_dispatch()
                try:
                    if reason_code == "preflight_replay_mismatch":
                        evidence = _terminal_evidence(
                            "failed_terminal",
                            reason_code=reason_code,
                            failure_code=(
                                "ADMITTED_SUITABILITY_REPLAY_MISMATCH"
                            ),
                            admitted={
                                "prompt_sha256": hashes["b"],
                                "job_fingerprint": hashes[
                                    "job_fingerprint"
                                ],
                                "suitability_receipt_sha256": hashes["5"],
                                "suitability_preflight": (
                                    suitability_preflight
                                ),
                                "route_policy_sha256": hashes["a"],
                            },
                            replayed={
                                "prompt_sha256": hashes["c"],
                                "job_fingerprint": hashes["d"],
                                "suitability_receipt_sha256": hashes["e"],
                                "suitability_preflight": {
                                    **suitability_preflight,
                                    "decision": "not_suitable",
                                },
                                "route_policy_sha256": hashes["f"],
                                "matches_admission": False,
                            },
                            worker_calls=0,
                            automatic_retry=False,
                        )
                    else:
                        evidence = _terminal_evidence(
                            "failed_terminal",
                            reason_code=reason_code,
                            failure_code=(
                                "PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE"
                            ),
                            preexisting_output_namespace=True,
                            generation_state="retry_pending",
                            finished_route_statuses=[],
                            open_route_reconciled_as_execution_unknown=None,
                            worker_reinvocation=False,
                            automatic_retry=False,
                            automatic_fallback=False,
                        )
                    identity = _terminal_identity(evidence)
                    sql = """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, NULL, NULL, NULL,
      '1900-01-01T00:00:00+00'::timestamptz
    )
"""
                    parameters = (
                        identity["transition_id"],
                        identity["transition_evidence_id"],
                        identity["terminal_receipt_id"],
                        PARTITION_WORK_ITEM_ID,
                        AUTHORIZATION_ID,
                        ADMISSION_ID,
                        TERMINAL_EVIDENCE_CONTRACT,
                        identity["evidence_sha256"],
                        Jsonb(evidence),
                    )
                    with psycopg.connect(pg.dsn) as connection:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SET ROLE "
                                "nhi_rule_history_update_queue_runtime"
                            )
                            if reason_code == "preflight_replay_mismatch":
                                malformed = {
                                    **evidence,
                                    "replayed": {
                                        **evidence["replayed"],
                                        "smuggled": True,
                                    },
                                }
                                malformed_identity = _terminal_identity(
                                    malformed
                                )
                                malformed_parameters = (
                                    malformed_identity["transition_id"],
                                    malformed_identity[
                                        "transition_evidence_id"
                                    ],
                                    malformed_identity[
                                        "terminal_receipt_id"
                                    ],
                                    PARTITION_WORK_ITEM_ID,
                                    AUTHORIZATION_ID,
                                    ADMISSION_ID,
                                    TERMINAL_EVIDENCE_CONTRACT,
                                    malformed_identity[
                                        "evidence_sha256"
                                    ],
                                    Jsonb(malformed),
                                )
                                cursor.execute("SAVEPOINT nested_shape")
                                with self.assertRaises(psycopg.Error):
                                    cursor.execute(
                                        sql, malformed_parameters
                                    )
                                cursor.execute(
                                    "ROLLBACK TO SAVEPOINT nested_shape"
                                )
                            cursor.execute(sql, parameters)
                            closed = cursor.fetchone()
                            assert closed is not None
                            self.assertEqual(
                                str(closed[3]),
                                identity["terminal_receipt_id"],
                            )
                            cursor.execute("RESET ROLE")
                            cursor.execute(
                                """
SELECT reason_code
FROM
  nhi_rule_history_partition_recovery.
    partition_terminal_receipt
WHERE terminal_receipt_id = %s::uuid
""",
                                (identity["terminal_receipt_id"],),
                            )
                            self.assertEqual(
                                cursor.fetchone()[0], reason_code
                            )
                finally:
                    pg.close()

    def test_forward_twice_empty_rollback_and_forward_again(self) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            pg.psql(file=FORWARD)
            pg.psql(file=ROLLBACK)
            pg.psql(file=FORWARD)
            marker = pg.psql(
                command="""
SELECT count(*)
FROM nhi_rule_history_partition_recovery.schema_migration;
"""
            ).stdout.strip()
            self.assertEqual(marker, "1")
        finally:
            pg.close()

    def test_acl_boundary_revokes_direct_and_transitive_runtime_access(
        self,
    ) -> None:
        pg = _base_cluster()
        try:
            pg.psql(
                command="""
CREATE ROLE rogue_scheduler NOLOGIN NOINHERIT;
GRANT EXECUTE ON FUNCTION
  nhi_rule_history_update_queue.authorize_failed_work_recovery(
    uuid, uuid, uuid, integer, integer, text,
    nhi_rule_history_update_ops.sha256_hex,
    text, text, nhi_rule_history_update_ops.sha256_hex,
    nhi_rule_history_update_ops.sha256_hex, uuid[],
    text, text, text, text, timestamptz
  )
  TO rogue_scheduler;
"""
            )
            pg.psql(file=FORWARD)
            result = pg.psql(
                command="""
SELECT
  has_function_privilege(
    'rogue_scheduler',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'EXECUTE'
  ),
  has_schema_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_partition_recovery',
    'CREATE'
  ),
  has_table_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_partition_recovery.dispatch_claim',
    'INSERT'
  ),
  has_function_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_partition_recovery.consume_partition_recovery_dispatch(uuid,uuid,integer,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid,uuid,text,integer,timestamptz,timestamptz)',
    'EXECUTE'
  ),
  EXISTS (
    WITH RECURSIVE membership(member, roleid) AS (
      SELECT member, roleid FROM pg_catalog.pg_auth_members
      UNION
      SELECT membership.member, edge.roleid
      FROM membership
      JOIN pg_catalog.pg_auth_members edge
        ON edge.member = membership.roleid
    )
    SELECT 1
    FROM membership
    WHERE member IN (
      SELECT oid FROM pg_catalog.pg_roles
      WHERE rolname IN (
        'nhi_rule_history_update_queue_runtime',
        'nhi_rule_history_candidate_runtime'
      )
    )
      AND roleid IN (
        SELECT oid FROM pg_catalog.pg_roles
        WHERE rolname IN (
          'nhi_rule_history_recovery_owner',
          'nhi_rule_history_recovery_authorizer'
        )
      )
  ),
  NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_proc function
    JOIN pg_catalog.pg_namespace namespace
      ON namespace.oid = function.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(
        function.proacl,
        pg_catalog.acldefault('f', function.proowner)
      )
    ) acl
    WHERE namespace.nspname =
      'nhi_rule_history_partition_recovery'
      AND acl.grantee = 0
      AND acl.privilege_type = 'EXECUTE'
  );
"""
            ).stdout.strip()
            self.assertEqual(result, "f|f|f|t|f|t")
        finally:
            pg.close()

    def test_candidate_runtime_has_only_bounded_recovery_attach_acl(
        self,
    ) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            result = pg.psql(
                command="""
SELECT
  has_schema_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery',
    'USAGE'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery.dispatch_claim',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery.worker_route_reservation',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery.worker_route_outcome',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.job_lease',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.content_artifact',
    'INSERT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.bundle_receipt',
    'INSERT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.update_job',
    'INSERT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.job_lease',
    'INSERT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.worker_attempt',
    'INSERT'
  ),
  NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_proc function
    JOIN pg_catalog.pg_namespace namespace
      ON namespace.oid = function.pronamespace
    WHERE namespace.nspname =
      'nhi_rule_history_partition_recovery'
      AND pg_catalog.has_function_privilege(
        'nhi_rule_history_candidate_runtime',
        function.oid,
        'EXECUTE'
      )
  );
"""
            ).stdout.strip()
            self.assertEqual(
                result,
                "t|t|t|t|t|t|t|f|f|f|t",
            )
            denied = pg.psql(
                command="""
SET ROLE nhi_rule_history_candidate_runtime;
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    verify_partition_recovery_admission('{}'::jsonb);
""",
                check=False,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("permission denied", denied.stderr)
        finally:
            pg.close()

    def test_recovery_function_acl_allowlists_are_exact(self) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            result = pg.psql(
                command="""
WITH capability(role_name) AS (
  VALUES
    ('nhi_rule_history_candidate_runtime'::text),
    ('nhi_rule_history_recovery_authorizer'::text),
    ('nhi_rule_history_update_queue_runtime'::text)
)
SELECT
  capability.role_name,
  COALESCE(
    pg_catalog.string_agg(
      function.proname,
      ',' ORDER BY function.proname
    ) FILTER (
      WHERE pg_catalog.has_function_privilege(
        capability.role_name,
        function.oid,
        'EXECUTE'
      )
    ),
    ''
  )
FROM capability
CROSS JOIN pg_catalog.pg_proc function
JOIN pg_catalog.pg_namespace namespace
  ON namespace.oid = function.pronamespace
WHERE namespace.nspname =
  'nhi_rule_history_partition_recovery'
GROUP BY capability.role_name
ORDER BY capability.role_name;
"""
            ).stdout.strip()
            self.assertEqual(
                result,
                "\n".join(
                    (
                        "nhi_rule_history_candidate_runtime|",
                        (
                            "nhi_rule_history_recovery_authorizer|"
                            "admit_partition_recovery,"
                            "authorize_partition_recovery,"
                            "revoke_partition_recovery,"
                            "show_partition_recovery,"
                            "verify_partition_recovery_admission"
                        ),
                        (
                            "nhi_rule_history_update_queue_runtime|"
                            "close_partition_recovery_generation,"
                            "consume_partition_recovery_dispatch,"
                            "finish_partition_recovery_route,"
                            "reserve_partition_recovery_route"
                        ),
                    )
                ),
            )
            legacy = pg.psql(
                command="""
WITH legacy(function_oid) AS (
  VALUES
    (pg_catalog.to_regprocedure(
      'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)'
    )),
    (pg_catalog.to_regprocedure(
      'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)'
    )),
    (pg_catalog.to_regprocedure(
      'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)'
    ))
)
SELECT
  pg_catalog.bool_and(
    pg_catalog.has_function_privilege(
      'nhi_rule_history_recovery_authorizer',
      function_oid,
      'EXECUTE'
    )
  ),
  pg_catalog.bool_and(
    NOT pg_catalog.has_function_privilege(
      'nhi_rule_history_update_queue_runtime',
      function_oid,
      'EXECUTE'
    )
  ),
  pg_catalog.bool_and(
    NOT pg_catalog.has_function_privilege(
      'nhi_rule_history_candidate_runtime',
      function_oid,
      'EXECUTE'
    )
  )
FROM legacy;
"""
            ).stdout.strip()
            self.assertEqual(legacy, "t|t|t")
        finally:
            pg.close()

    def test_forward_reapply_revokes_direct_authorizer_owner_membership_and_rejects_transitive_path(
        self,
    ) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            pg.psql(
                command="""
GRANT nhi_rule_history_recovery_owner
  TO
    nhi_rule_history_recovery_authorizer,
    nhi_rule_history_candidate_runtime;
GRANT nhi_rule_history_update_queue_runtime
  TO nhi_rule_history_candidate_runtime;
"""
            )
            pg.psql(file=FORWARD)
            direct_path = pg.psql(
                command="""
SELECT
  pg_catalog.pg_has_role(
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_recovery_owner',
    'MEMBER'
  ),
  pg_catalog.pg_has_role(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_recovery_owner',
    'MEMBER'
  ),
  pg_catalog.pg_has_role(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_queue_runtime',
    'MEMBER'
  );
"""
            ).stdout.strip()
            self.assertEqual(direct_path, "f|f|f")

            pg.psql(
                command="""
CREATE ROLE injected_recovery_bridge NOLOGIN NOINHERIT;
GRANT nhi_rule_history_recovery_owner
  TO injected_recovery_bridge;
GRANT injected_recovery_bridge
  TO nhi_rule_history_recovery_authorizer;
"""
            )
            reapplied = pg.psql(file=FORWARD, check=False)
            self.assertNotEqual(reapplied.returncode, 0)
            self.assertIn(
                "unsafe transitive recovery authority path",
                reapplied.stderr,
            )
        finally:
            pg.close()

    def test_existing_failed_recovery_rows_remain_byte_stable(self) -> None:
        pg = _base_cluster(fixture=True)
        try:
            authorize_work_recovery(
                pg.dsn,
                work_item_id=_recovery_tests.WORK_ITEM_ID,
                prior_generation=1,
                source_bundle_uid="fixture-bundle",
                source_manifest_sha256=_recovery_tests.SOURCE_MANIFEST,
                prior_method_version="worker-method-v1",
                new_method_version="worker-method-v2",
                prior_semantic_prompt_fingerprint=(
                    _recovery_tests.OLD_PROMPT
                ),
                new_semantic_prompt_fingerprint=(
                    _recovery_tests.NEW_PROMPT
                ),
                superseded_attempt_ids=[
                    _recovery_tests.OLD_PRIMARY_ID,
                    _recovery_tests.OLD_FALLBACK_ID,
                ],
                decision_basis_id="pre-a-plus-fixture",
                reason="existing failed recovery fixture",
                actor_kind="fixture-controller",
                authorized_at="2026-07-28T00:00:00+00:00",
            )
            snapshot_sql = """
SELECT
  (SELECT count(*) FROM
     nhi_rule_history_update_queue.work_recovery_authorization),
  (SELECT count(*) FROM
     nhi_rule_history_update_queue.work_generation),
  (SELECT count(*) FROM
     nhi_rule_history_update_queue.work_generation_transition),
  (
    SELECT md5(
      COALESCE(string_agg(row_value::text, '|' ORDER BY row_value::text), '')
    )
    FROM
      nhi_rule_history_update_queue.work_generation_transition row_value
  );
"""
            before = pg.psql(command=snapshot_sql).stdout.strip()
            pg.psql(file=FORWARD)
            after = pg.psql(command=snapshot_sql).stdout.strip()
            self.assertEqual(after, before)
        finally:
            pg.close()

    def test_any_evidence_blocks_destructive_rollback_after_disable(self) -> None:
        pg = _base_cluster()
        try:
            pg.psql(file=FORWARD)
            pg.psql(
                command="""
SET session_replication_role = replica;
INSERT INTO
  nhi_rule_history_partition_recovery.generation_transition_evidence (
    transition_evidence_id, generation_transition_id,
    evidence_kind, evidence_contract, evidence_object_id,
    evidence_sha256, byte_count, logical_locator, ordinal,
    canonical_payload_sha256, created_by_session
  ) VALUES (
    '42000000-0000-0000-0000-000000000001',
    '42000000-0000-0000-0000-000000000002',
    'rollback-fixture', 'fixture/v1', 'fixture',
    repeat('a', 64), 1, 'partition-recovery/fixtures/rollback',
    1, repeat('b', 64), session_user
  );
SET session_replication_role = origin;
"""
            )
            rollback = pg.psql(file=ROLLBACK, check=False)
            self.assertNotEqual(rollback.returncode, 0)
            self.assertIn(
                "partition recovery evidence exists", rollback.stderr
            )
            state = pg.psql(
                command="""
SELECT
  to_regnamespace('nhi_rule_history_partition_recovery') IS NOT NULL,
  (
    SELECT count(*)
    FROM
      nhi_rule_history_partition_recovery.
        generation_transition_evidence
  ),
  has_schema_privilege(
    'nhi_rule_history_update_queue_runtime',
    'nhi_rule_history_partition_recovery',
    'USAGE'
  ),
  has_schema_privilege(
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_partition_recovery',
    'USAGE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'EXECUTE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'EXECUTE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_authorizer',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)',
    'EXECUTE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_owner',
    'nhi_rule_history_update_queue.admit_legacy_failure_evidence(uuid,uuid,uuid,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text,nhi_rule_history_update_ops.sha256_hex,jsonb,text[],text,nhi_rule_history_update_ops.sha256_hex,text,nhi_rule_history_update_ops.sha256_hex,text,timestamptz)',
    'EXECUTE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_owner',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery(uuid,uuid,uuid,integer,integer,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,uuid[],text,text,text,text,timestamptz)',
    'EXECUTE'
  ),
  has_function_privilege(
    'nhi_rule_history_recovery_owner',
    'nhi_rule_history_update_queue.authorize_failed_work_recovery_from_legacy(uuid,uuid,uuid,integer,integer,uuid,text,nhi_rule_history_update_ops.sha256_hex,text,text,nhi_rule_history_update_ops.sha256_hex,nhi_rule_history_update_ops.sha256_hex,text,text,text,text,timestamptz)',
    'EXECUTE'
  ),
  has_schema_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery',
    'USAGE'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery.dispatch_claim',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.job_lease',
    'SELECT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.content_artifact',
    'INSERT'
  ),
  has_table_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_update_ops.bundle_receipt',
    'INSERT'
  ),
  has_function_privilege(
    'nhi_rule_history_candidate_runtime',
    'nhi_rule_history_partition_recovery.close_partition_recovery_generation(uuid,uuid,uuid,uuid,integer,uuid,uuid,text,text,nhi_rule_history_update_ops.sha256_hex,jsonb,uuid,uuid,uuid,timestamptz)',
    'EXECUTE'
  );
"""
            ).stdout.strip()
            self.assertEqual(
                state,
                "t|1|f|f|f|f|f|f|f|f|f|f|f|f|f|f",
            )
            denied = pg.psql(
                command="""
SET ROLE nhi_rule_history_recovery_authorizer;
SELECT *
FROM nhi_rule_history_update_queue.authorize_failed_work_recovery(
  NULL::uuid, NULL::uuid, NULL::uuid, NULL::integer, NULL::integer,
  NULL::text, NULL::nhi_rule_history_update_ops.sha256_hex,
  NULL::text, NULL::text,
  NULL::nhi_rule_history_update_ops.sha256_hex,
  NULL::nhi_rule_history_update_ops.sha256_hex,
  NULL::uuid[], NULL::text, NULL::text, NULL::text, NULL::text,
  NULL::timestamptz
);
""",
                check=False,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("permission denied", denied.stderr)
        finally:
            pg.close()

    def test_restart_before_model_reservation_is_zero_route_terminal(
        self,
    ) -> None:
        pg, hashes = _prepare_consumed_dispatch()
        try:
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    missing_reason = {
                        "dispatch_claim_id": CLAIM_ID,
                        "failure_code": "RECOVERY_RUNTIME_RESTARTED",
                    }
                    cursor.execute("SAVEPOINT missing_reason")
                    with self.assertRaises(psycopg.Error):
                        cursor.execute(
                            """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', 'terminal/v1',
      %s, %s::jsonb, NULL, NULL, NULL,
      '2099-01-01T00:00:02+00'::timestamptz
    )
""",
                            (
                                TERMINAL_TRANSITION_ID,
                                TERMINAL_EVIDENCE_ID,
                                TERMINAL_RECEIPT_ID,
                                PARTITION_WORK_ITEM_ID,
                                AUTHORIZATION_ID,
                                ADMISSION_ID,
                                hashes["e"],
                                Jsonb(missing_reason),
                            ),
                        )
                    cursor.execute("ROLLBACK TO SAVEPOINT missing_reason")
                    cursor.execute("RELEASE SAVEPOINT missing_reason")
                    evidence = _terminal_evidence(
                        "failed_terminal",
                        reason_code="restart_before_model_reservation",
                        failure_code=(
                            "RECOVERY_RESTART_BEFORE_MODEL_RESERVATION"
                        ),
                        preexisting_output_namespace=False,
                        generation_state="retry_pending",
                        finished_route_statuses=[],
                        open_route_reconciled_as_execution_unknown=None,
                        worker_reinvocation=False,
                        automatic_retry=False,
                        automatic_fallback=False,
                    )
                    identity = _terminal_identity(evidence)
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, NULL, NULL, NULL,
      '2099-01-01T00:00:03+00'::timestamptz
    )
""",
                        (
                            identity["transition_id"],
                            identity["transition_evidence_id"],
                            identity["terminal_receipt_id"],
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            TERMINAL_EVIDENCE_CONTRACT,
                            identity["evidence_sha256"],
                            Jsonb(evidence),
                        ),
                    )
                    closed = cursor.fetchone()
                    assert closed is not None
                    self.assertEqual(closed[2], "failed_terminal")
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
SELECT terminal.reason_code, transition.source_job_id,
       (SELECT count(*) FROM
          nhi_rule_history_partition_recovery.
            worker_route_reservation)
FROM
  nhi_rule_history_partition_recovery.partition_terminal_receipt
    terminal
JOIN nhi_rule_history_update_queue.work_generation_transition transition
  ON transition.transition_id = terminal.terminal_transition_id
WHERE terminal.terminal_receipt_id = %s::uuid
""",
                        (identity["terminal_receipt_id"],),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        ("restart_before_model_reservation", None, 0),
                    )
        finally:
            pg.close()

    def test_restart_after_model_result_requires_recovery_source_job(
        self,
    ) -> None:
        pg, hashes = _prepare_consumed_dispatch()
        try:
            attempt_id = "41000000-0000-0000-0000-00000000000b"
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      1::smallint, %s, %s, %s, %s::uuid, %s::uuid,
      'fixture-owner', 'codex', 'openai', 'fixture-model', %s,
      '2099-01-01T00:00:01+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            hashes["1"],
                            hashes["b"],
                            hashes["namespace"],
                            RECOVERY_JOB_ID,
                            LEASE_ID,
                            hashes["c"],
                        ),
                    )
                    cursor.fetchone()
                    route_receipt = {
                        "lease_id": LEASE_ID,
                        "owner_key": "fixture-owner",
                        "started_at": "2099-01-01T00:00:01+00:00",
                        "completed_at": "2099-01-01T00:00:02+00:00",
                        "raw_worker_attempt_id": "1a" * 32,
                        "attempt_namespace": hashes["namespace"],
                    }
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      'failed', 'timeout', %s::uuid, NULL, NULL, NULL, NULL, true,
      %s, %s::jsonb, '2099-01-01T00:00:02+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            attempt_id,
                            hashes["d"],
                            Jsonb(route_receipt),
                        ),
                    )
                    cursor.fetchone()
                    evidence = _terminal_evidence(
                        "failed_terminal",
                        reason_code="restart_after_model_result",
                        failure_code="RECOVERY_RESTART_AFTER_MODEL_RESULT",
                        preexisting_output_namespace=True,
                        generation_state="proposal_running",
                        finished_route_statuses=[
                            {
                                "reservation_id": RESERVATION_ID,
                                "route_ordinal": 1,
                                "route": "primary",
                                "status": "failed",
                                "failure_class": "timeout",
                            }
                        ],
                        open_route_reconciled_as_execution_unknown=None,
                        worker_reinvocation=False,
                        automatic_retry=False,
                        automatic_fallback=False,
                    )
                    identity = _terminal_identity(
                        evidence, source_job_id=RECOVERY_JOB_ID
                    )
                    cursor.execute("SAVEPOINT wrong_source_job")
                    with self.assertRaises(psycopg.Error):
                        cursor.execute(
                            """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, NULL, NULL, NULL,
      '2099-01-01T00:00:03+00'::timestamptz
    )
""",
                            (
                                identity["transition_id"],
                                identity["transition_evidence_id"],
                                identity["terminal_receipt_id"],
                                PARTITION_WORK_ITEM_ID,
                                AUTHORIZATION_ID,
                                ADMISSION_ID,
                                TERMINAL_EVIDENCE_CONTRACT,
                                identity["evidence_sha256"],
                                Jsonb(evidence),
                            ),
                        )
                    cursor.execute("ROLLBACK TO SAVEPOINT wrong_source_job")
                    cursor.execute("RELEASE SAVEPOINT wrong_source_job")
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, %s::uuid, NULL, NULL,
      '2099-01-01T00:00:04+00'::timestamptz
    )
""",
                        (
                            identity["transition_id"],
                            identity["transition_evidence_id"],
                            identity["terminal_receipt_id"],
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            TERMINAL_EVIDENCE_CONTRACT,
                            identity["evidence_sha256"],
                            Jsonb(evidence),
                            RECOVERY_JOB_ID,
                        ),
                    )
                    self.assertEqual(cursor.fetchone()[2], "failed_terminal")
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
SELECT terminal.reason_code, transition.source_job_id::text
FROM
  nhi_rule_history_partition_recovery.partition_terminal_receipt
    terminal
JOIN nhi_rule_history_update_queue.work_generation_transition transition
  ON transition.transition_id = terminal.terminal_transition_id
WHERE terminal.terminal_receipt_id = %s::uuid
""",
                        (identity["terminal_receipt_id"],),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        ("restart_after_model_result", RECOVERY_JOB_ID),
                    )
        finally:
            pg.close()

    def test_public_pg_queue_api_executes_exact_sql_contract_end_to_end(
        self,
    ) -> None:
        pg = _base_cluster(fixture=True)
        try:
            pg.psql(file=FORWARD)
            pg.psql(
                command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id,
  recorded_at
) VALUES (
  '{PARTITION_WORK_ITEM_ID}', 6,
  '11000000-0000-0000-0000-000000000025',
  'proposal_running', 'partition_required', 'fixture',
  repeat('c',64), '{{"event":"partition-required"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:05+00'
);
"""
            )
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
SELECT
  terminal_transition_id, terminal_transition_sequence,
  terminal_state, terminal_evidence_sha256, transition_count,
  transition_rows, old_job_fingerprint
FROM
  nhi_rule_history_partition_recovery.
    generation_one_chain_receipt(%s::uuid)
""",
                        (PARTITION_WORK_ITEM_ID,),
                    )
                    receipt = cursor.fetchone()
                    assert receipt is not None
            evidence = _api_contract_tests.admission_payload()
            transitions = receipt[5]
            generation = evidence["generation_1"]
            assert isinstance(generation, dict)
            generation.update(
                {
                    "work_item_id": PARTITION_WORK_ITEM_ID,
                    "terminal_transition_id": str(receipt[0]),
                    "terminal_transition_sequence": receipt[1],
                    "terminal_state": receipt[2],
                    "terminal_evidence_sha256": receipt[3],
                    "transition_count": receipt[4],
                    "transitions": transitions,
                    "old_job_fingerprint": receipt[6],
                    "ordered_chain_sha256": sha256_bytes(
                        canonical_json_bytes(transitions)
                    ),
                    "rowset_fingerprint": sha256_bytes(
                        canonical_json_bytes(
                            sorted(
                                transitions,
                                key=lambda row: row["transition_id"],
                            )
                        )
                    ),
                }
            )
            delta = evidence["execution_delta"]
            assert isinstance(delta, dict)
            output_namespace = partition_recovery_output_namespace(
                work_item_id=PARTITION_WORK_ITEM_ID,
                generation=2,
                job_fingerprint=str(delta["new_job_fingerprint"]),
            )
            evidence["output_namespace"] = {
                "contract": PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT,
                "generation": 2,
                "relative_path": output_namespace,
            }
            verified = verify_partition_recovery_admission(
                pg.dsn,
                evidence=evidence,
            )
            self.assertTrue(verified["database"]["verified"])
            admitted = admit_partition_recovery(
                pg.dsn,
                evidence=evidence,
                actor_kind="fixture-operator",
                admitted_at="2099-01-01T00:00:00+00:00",
            )
            authorized = authorize_partition_recovery(
                pg.dsn,
                admission_id=admitted["admission_id"],
                work_item_id=PARTITION_WORK_ITEM_ID,
                generation=2,
                admission_payload_sha256=admitted[
                    "admission_payload_sha256"
                ],
                expires_at="2099-01-02T00:00:00+00:00",
                actor_kind="fixture-operator",
                authorized_at="2099-01-01T00:00:00+00:00",
            )
            dispatch_args = {
                "work_item_id": PARTITION_WORK_ITEM_ID,
                "generation": 2,
                "authorization_id": authorized["authorization_id"],
                "admission_id": admitted["admission_id"],
                "admission_payload_sha256": admitted[
                    "admission_payload_sha256"
                ],
                "sealed_packet_manifest_sha256": admitted[
                    "sealed_packet_manifest_sha256"
                ],
                "suitability_v2_receipt_sha256": admitted[
                    "suitability_v2_receipt_sha256"
                ],
                "job_fingerprint": admitted["new_job_fingerprint"],
                "prompt_sha256": admitted["prompt_sha256"],
                "route_policy_sha256": admitted["route_policy_sha256"],
                "owner_key": "fixture-api-owner",
                "max_runtime_seconds": 180,
            }
            dispatch = consume_partition_recovery_dispatch(
                pg.dsn,
                **dispatch_args,
                consumed_at="2099-01-01T00:00:00+00:00",
            )
            self.assertFalse(dispatch["replayed"])
            self.assertEqual(dispatch["generation_state"], "retry_pending")
            self.assertEqual(
                dispatch["output_namespace"], output_namespace
            )
            reserved = reserve_partition_recovery_route(
                pg.dsn,
                dispatch_claim_id=dispatch["dispatch_claim_id"],
                work_item_id=PARTITION_WORK_ITEM_ID,
                generation=2,
                authorization_id=authorized["authorization_id"],
                admission_id=admitted["admission_id"],
                route_ordinal=1,
                packet_sha256=admitted[
                    "sealed_packet_manifest_sha256"
                ],
                prompt_sha256=admitted["prompt_sha256"],
                recovery_job_id=dispatch["recovery_job_id"],
                lease_id=dispatch["lease_id"],
                owner_key=dispatch["owner_key"],
                runtime_id="codex",
                provider="openai",
                model="fixture-model",
                controller_commit_sha256="a1" * 32,
                job_fingerprint=admitted["new_job_fingerprint"],
                reserved_at="2099-01-01T00:00:01+00:00",
            )
            self.assertFalse(reserved["replayed"])
            self.assertEqual(
                reserved["source_job_id"], dispatch["recovery_job_id"]
            )
            finished = finish_partition_recovery_route(
                pg.dsn,
                reservation_id=reserved["reservation_id"],
                dispatch_claim_id=dispatch["dispatch_claim_id"],
                work_item_id=PARTITION_WORK_ITEM_ID,
                generation=2,
                authorization_id=authorized["authorization_id"],
                admission_id=admitted["admission_id"],
                route_ordinal=1,
                status="execution_unknown",
                result_receipt_sha256="b1" * 32,
                evidence={
                    "schema": "fixture/route-execution-unknown/v1",
                    "reason": "restart-after-reservation",
                },
                attempt_namespace=reserved["attempt_namespace"],
                job_fingerprint=admitted["new_job_fingerprint"],
                recovery_job_id=dispatch["recovery_job_id"],
                lease_id=dispatch["lease_id"],
                owner_key=dispatch["owner_key"],
                completed_at="2099-01-01T00:03:00+00:00",
            )
            self.assertEqual(finished["status"], "execution_unknown")
            self.assertFalse(finished["replayed"])
            terminal_evidence = _terminal_evidence(
                "failed_terminal",
                dispatch_claim_id=dispatch["dispatch_claim_id"],
                authorization_id=authorized["authorization_id"],
                admission_id=admitted["admission_id"],
                reason_code="execution_unknown",
                failure_code="WORKER_EXECUTION_UNKNOWN",
                execution_unknown_routes={"primary": "b1" * 32},
                automatic_retry=False,
                automatic_fallback=False,
            )
            terminal_identity = _terminal_identity(
                terminal_evidence, source_job_id=dispatch["recovery_job_id"]
            )
            closed = close_partition_recovery_generation(
                pg.dsn,
                dispatch_claim_id=dispatch["dispatch_claim_id"],
                work_item_id=PARTITION_WORK_ITEM_ID,
                generation=2,
                authorization_id=authorized["authorization_id"],
                admission_id=admitted["admission_id"],
                to_state="failed_terminal",
                evidence_contract=TERMINAL_EVIDENCE_CONTRACT,
                evidence_sha256=terminal_identity["evidence_sha256"],
                evidence=terminal_evidence,
                terminal_receipt_id=terminal_identity[
                    "terminal_receipt_id"
                ],
                source_job_id=dispatch["recovery_job_id"],
                closed_at="2099-01-01T00:03:01+00:00",
            )
            self.assertEqual(closed["to_state"], "failed_terminal")
            replay = consume_partition_recovery_dispatch(
                pg.dsn,
                **dispatch_args,
                consumed_at="2099-02-01T00:00:00+00:00",
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["generation_state"], "failed_terminal")
            self.assertEqual(replay["terminal_state"], "failed_terminal")
            self.assertEqual(
                replay["terminal_receipt_id"],
                terminal_identity["terminal_receipt_id"],
            )
            self.assertEqual(replay["finished_route_count"], 1)
            self.assertEqual(
                replay["finished_route_statuses"][0]["status"],
                "execution_unknown",
            )
        finally:
            pg.close()

    def test_fallback_success_can_close_staged_candidate(self) -> None:
        pg, hashes = _prepare_consumed_dispatch()
        fallback_reservation_id = "41000000-0000-0000-0000-00000000000c"
        primary_attempt_id = "41000000-0000-0000-0000-00000000000d"
        fallback_attempt_id = "41000000-0000-0000-0000-00000000000e"
        bundle_receipt_id = "41000000-0000-0000-0000-00000000000f"
        candidate_id = "41000000-0000-0000-0000-000000000010"
        manifest_sha256 = "2a" * 32
        output_sha256 = "5a" * 32
        try:
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      1::smallint, %s, %s, %s, %s::uuid, %s::uuid,
      'fixture-owner', 'codex', 'openai', 'primary-model', %s,
      '2099-01-01T00:00:01+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            hashes["1"],
                            hashes["b"],
                            hashes["namespace"],
                            RECOVERY_JOB_ID,
                            LEASE_ID,
                            hashes["c"],
                        ),
                    )
                    cursor.fetchone()
                    primary_receipt = {
                        "lease_id": LEASE_ID,
                        "owner_key": "fixture-owner",
                        "started_at": "2099-01-01T00:00:01+00:00",
                        "completed_at": "2099-01-01T00:00:02+00:00",
                        "raw_worker_attempt_id": "6a" * 32,
                        "attempt_namespace": hashes["namespace"],
                    }
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      'failed', 'timeout', %s::uuid, NULL, NULL, NULL, NULL, true,
      %s, %s::jsonb, '2099-01-01T00:00:02+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            primary_attempt_id,
                            hashes["d"],
                            Jsonb(primary_receipt),
                        ),
                    )
                    cursor.fetchone()
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      2::smallint, %s, %s, %s, %s::uuid, %s::uuid,
      'fixture-owner', 'codex', 'openai', 'fallback-model', %s,
      '2099-01-01T00:00:03+00'::timestamptz
    )
""",
                        (
                            fallback_reservation_id,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            hashes["1"],
                            hashes["b"],
                            hashes["namespace"],
                            RECOVERY_JOB_ID,
                            LEASE_ID,
                            hashes["c"],
                        ),
                    )
                    cursor.fetchone()
                    fallback_receipt = {
                        "lease_id": LEASE_ID,
                        "owner_key": "fixture-owner",
                        "started_at": "2099-01-01T00:00:03+00:00",
                        "completed_at": "2099-01-01T00:00:04+00:00",
                        "raw_worker_attempt_id": "7a" * 32,
                        "attempt_namespace": hashes["namespace"],
                    }
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      'succeeded', NULL, %s::uuid, %s, %s, %s, 0, false,
      %s, %s::jsonb, '2099-01-01T00:00:04+00'::timestamptz
    )
""",
                        (
                            fallback_reservation_id,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            fallback_attempt_id,
                            "3a" * 32,
                            "4a" * 32,
                            output_sha256,
                            hashes["e"],
                            Jsonb(fallback_receipt),
                        ),
                    )
                    self.assertEqual(cursor.fetchone()[3], "succeeded")
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES (
  %s, 20, 'application/json', 'partition/fallback/manifest.json',
  '2099-01-01T00:00:05+00'::timestamptz
)
""",
                        (manifest_sha256,),
                    )
                    cursor.execute(
                        """
INSERT INTO nhi_rule_history_update_ops.bundle_receipt (
  receipt_id, job_id, bundle_uid, manifest_sha256,
  bundle_relative_path, artifact_count, total_bytes, prepared_at,
  atomically_published_at, pg_received_at, fsync_verified,
  receipt_status, rejection_code
) VALUES (
  %s::uuid, %s::uuid, 'fallback-success-bundle', %s,
  'partition/fallback-success-bundle', 1, 20,
  '2099-01-01T00:00:05+00'::timestamptz,
  '2099-01-01T00:00:05+00'::timestamptz,
  '2099-01-01T00:00:05+00'::timestamptz,
  true, 'received', NULL
)
""",
                        (
                            bundle_receipt_id,
                            RECOVERY_JOB_ID,
                            manifest_sha256,
                        ),
                    )
                    cursor.execute(
                        """
INSERT INTO nhi_rule_history_candidate_stage.candidate_proposal (
  proposal_id, proposal_fingerprint, contract_version, job_id,
  bundle_receipt_id, producer_attempt_id, producer_output_sha256,
  source_designation_text, raw_effective_expression,
  calendar_system, effective_from, date_precision, date_role,
  date_scope, conditionality, replacement_scope,
  omitted_text_present, merged_cells_present, cross_row_dependency,
  multiple_designations_present, odt_pdf_agreement,
  identity_resolution, confidence, candidate_note
) VALUES (
  %s::uuid, %s, 'fixture-candidate/v1', %s::uuid, %s::uuid,
  %s::uuid, %s, 'fixture designation', NULL, 'unresolved', NULL,
  'unresolved', 'unresolved', 'unresolved', 'unresolved',
  'full_single_clause', false, false, false, false, 'not_available',
  'source_designation_only', 0.9000, 'fallback success fixture'
)
""",
                        (
                            candidate_id,
                            "8a" * 32,
                            RECOVERY_JOB_ID,
                            bundle_receipt_id,
                            fallback_attempt_id,
                            output_sha256,
                        ),
                    )
                    cursor.execute(
                        """
INSERT INTO
  nhi_rule_history_candidate_stage.candidate_source_span (
    proposal_id, span_id, artifact_sha256, source_role, locator,
    locator_key, char_start, char_end, raw_text, raw_text_sha256,
    raw_text_char_length, observed_at, statement
  ) VALUES (
    %s::uuid, %s, %s, 'comparison_new',
    '{"table":1,"row":1,"cell":1}'::jsonb,
    'table:1/row:1/cell:1', 0, 4, 'rule', %s, 4,
    '2099-01-01T00:00:05+00'::timestamptz,
    'Source-grounded candidate evidence only; no legal-history identity, adjacency, interval closure, or executable mutation authority.'
  )
""",
                        (
                            candidate_id,
                            "9a" * 32,
                            manifest_sha256,
                            "aa" * 32,
                        ),
                    )
                    cursor.execute(
                        """
INSERT INTO nhi_rule_history_candidate_stage.candidate_evidence (
  proposal_id, evidence_id, span_id, evidence_code, outcome,
  assertion_text, evidence_details, validator_version, recorded_at
) VALUES (
  %s::uuid, %s, %s, 'fixture_candidate', 'pass',
  'Fixture candidate evidence.', '{"fixture":true}'::jsonb,
  'fixture-validator', '2099-01-01T00:00:05+00'::timestamptz
)
""",
                        (candidate_id, "ab" * 32, "9a" * 32),
                    )
                    cursor.execute(
                        """
INSERT INTO
  nhi_rule_history_candidate_stage.candidate_state_transition (
    proposal_id, transition_seq, transition_id, state, actor_kind,
    decision_basis_sha256, recorded_at
  ) VALUES (
    %s::uuid, 1,
    '41000000-0000-0000-0000-000000000011'::uuid,
    'validated_candidate', 'deterministic_validator', %s,
    '2099-01-01T00:00:05+00'::timestamptz
  ), (
    %s::uuid, 2,
    '41000000-0000-0000-0000-000000000012'::uuid,
    'promotion_ready_pending_anchor', 'system_gate', %s,
    '2099-01-01T00:00:05+00'::timestamptz
  )
""",
                        (
                            candidate_id,
                            "ac" * 32,
                            candidate_id,
                            "ad" * 32,
                        ),
                    )
                    terminal_evidence = _terminal_evidence(
                        "staged_needs_review",
                        candidate_receipt_sha256=hashes["f"],
                        candidate_state="needs_review",
                        selected_worker_role="fallback",
                        worker_calls=2,
                        finished_routes={
                            "primary": hashes["d"],
                            "fallback": hashes["e"],
                        },
                        canonical_history_writes=0,
                    )
                    terminal_identity = _terminal_identity(
                        terminal_evidence,
                        source_job_id=RECOVERY_JOB_ID,
                        bundle_receipt_id=bundle_receipt_id,
                        candidate_proposal_id=candidate_id,
                    )
                    close_sql = """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'staged_needs_review', %s,
      %s, %s::jsonb, %s::uuid, %s::uuid, %s::uuid,
      '2099-01-01T00:00:06+00'::timestamptz
    )
"""
                    close_parameters = (
                        terminal_identity["transition_id"],
                        terminal_identity["transition_evidence_id"],
                        terminal_identity["terminal_receipt_id"],
                        PARTITION_WORK_ITEM_ID,
                        AUTHORIZATION_ID,
                        ADMISSION_ID,
                        TERMINAL_EVIDENCE_CONTRACT,
                        terminal_identity["evidence_sha256"],
                        Jsonb(terminal_evidence),
                        RECOVERY_JOB_ID,
                        bundle_receipt_id,
                        candidate_id,
                    )
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    cursor.execute("SAVEPOINT candidate_state_gate")
                    with self.assertRaisesRegex(
                        psycopg.errors.IntegrityError,
                        "needs-review proposal",
                    ):
                        cursor.execute(close_sql, close_parameters)
                    cursor.execute("ROLLBACK TO SAVEPOINT candidate_state_gate")
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
INSERT INTO
  nhi_rule_history_candidate_stage.candidate_state_transition (
    proposal_id, transition_seq, transition_id, state, actor_kind,
    decision_basis_sha256, recorded_at
  ) VALUES (
    %s::uuid, 3,
    '41000000-0000-0000-0000-000000000013'::uuid,
    'needs_review', 'source_capable_reviewer', %s,
    '2099-01-01T00:00:05+00'::timestamptz
  )
""",
                        (candidate_id, "ae" * 32),
                    )
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    cursor.execute(
                        close_sql,
                        close_parameters,
                    )
                    self.assertEqual(
                        cursor.fetchone()[2], "staged_needs_review"
                    )
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
SELECT
  terminal.terminal_state,
  terminal.reason_code,
  (
    SELECT jsonb_agg(
      jsonb_build_object(
        'route', reservation.route,
        'status', outcome.status
      )
      ORDER BY reservation.route_ordinal
    )
    FROM
      nhi_rule_history_partition_recovery.worker_route_reservation
        reservation
    JOIN
      nhi_rule_history_partition_recovery.worker_route_outcome outcome
      ON outcome.reservation_id = reservation.reservation_id
    WHERE reservation.claim_id = %s::uuid
  )
FROM
  nhi_rule_history_partition_recovery.partition_terminal_receipt
    terminal
WHERE terminal.terminal_receipt_id = %s::uuid
""",
                        (
                            CLAIM_ID,
                            terminal_identity["terminal_receipt_id"],
                        ),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        (
                            "staged_needs_review",
                            "valid_output",
                            [
                                {"route": "primary", "status": "failed"},
                                {"route": "fallback", "status": "succeeded"},
                            ],
                        ),
                    )
        finally:
            pg.close()

    def test_atomic_job_lease_and_restart_after_open_reservation(self) -> None:
        pg = _base_cluster(fixture=True)
        try:
            pg.psql(file=FORWARD)
            pg.psql(
                command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id,
  recorded_at
) VALUES (
  '{PARTITION_WORK_ITEM_ID}', 6,
  '11000000-0000-0000-0000-000000000025',
  'proposal_running', 'partition_required', 'fixture',
  repeat('c',64), '{{"event":"partition-required"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:05+00'
);
"""
            )
            with psycopg.connect(pg.dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
SELECT
  terminal_transition_id, terminal_transition_sequence,
  terminal_state, terminal_evidence_sha256, transition_count,
  transition_rows, old_job_fingerprint, worker_call_count,
  worker_attempt_count, candidate_count, route_attempt_count
FROM
  nhi_rule_history_partition_recovery.
    generation_one_chain_receipt(%s::uuid)
""",
                        (PARTITION_WORK_ITEM_ID,),
                    )
                    receipt = cursor.fetchone()
                    assert receipt is not None
                    transitions = receipt[5]
                    ordered_hash = sha256_bytes(
                        canonical_json_bytes(transitions)
                    )
                    rowset_hash = sha256_bytes(
                        canonical_json_bytes(
                            sorted(
                                transitions,
                                key=lambda row: row["transition_id"],
                            )
                        )
                    )
                    hashes = {
                        letter: letter * 64
                        for letter in "def0123456789abcdef"
                    }
                    new_job_fingerprint = "0f" * 32
                    payload = {
                        "schema": "nhi-rule-history/partition-recovery-admission/v1",
                        "canonical_encoding_contract": (
                            "nhi-rule-history/canonical-json-bytes/no-float/v1"
                        ),
                        "generation_1": {
                            "work_item_id": PARTITION_WORK_ITEM_ID,
                            "prior_generation": 1,
                            "terminal_transition_id": str(receipt[0]),
                            "terminal_transition_sequence": receipt[1],
                            "terminal_state": receipt[2],
                            "terminal_evidence_sha256": receipt[3],
                            "transition_count": receipt[4],
                            "transitions": transitions,
                            "ordered_chain_sha256": ordered_hash,
                            "rowset_fingerprint": rowset_hash,
                            "old_job_fingerprint": receipt[6],
                            "old_partition_receipt": {
                                "sha256": hashes["d"]
                            },
                            "old_suitability_receipt": {
                                "sha256": hashes["e"]
                            },
                            "worker_call_count": 0,
                            "worker_attempt_count": 0,
                            "candidate_count": 0,
                            "route_attempt_count": 0,
                        },
                        "source_evidence": {
                            "reuse_existing_bundle": True,
                            "repoll_allowed": False,
                            "reacquire_allowed": False,
                            "new_corpus_registration_allowed": False,
                            "source_bundle": {
                                "bundle_id": "source-bundle-fixture",
                                "manifest_sha256": hashes["f"],
                                "logical_locator": "bundles/source/fixture",
                            },
                            "corpus_bundle": {
                                "bundle_id": "corpus-bundle-fixture",
                                "manifest_sha256": hashes["0"],
                                "logical_locator": "bundles/corpus/fixture",
                            },
                            "sealed_packet": {
                                "manifest_sha256": hashes["1"],
                                "byte_count": 123,
                                "ordered_artifact_sha256_set_digest": (
                                    hashes["2"]
                                ),
                                "logical_locator": (
                                    "partition-recovery/packets/fixture"
                                ),
                            },
                            "artifacts": [],
                        },
                        "execution_delta": {
                            "old_suitability_contract": "suitability/v1",
                            "new_suitability_contract": "suitability/v2",
                            "old_fingerprint_domain": "worker/v1",
                            "new_fingerprint_domain": "worker/v2",
                            "new_job_fingerprint": new_job_fingerprint,
                            "suitability_v2_schema_sha256": hashes["4"],
                            "suitability_v2_receipt_sha256": hashes["5"],
                            "verifier_contract_version": "verifier/v2",
                            "verifier_code_commit": "6" * 40,
                            "verifier_config_sha256": hashes["7"],
                            "verifier_executable_sha256": hashes["8"],
                            "execution_contract_version": "execution/v2",
                            "execution_contract_sha256": hashes["9"],
                            "route_policy_sha256": hashes["a"],
                            "suitability_preflight": {
                                "decision": "suitable",
                                "designation_candidates": ["drug"],
                                "effective_designation_candidates": ["drug"],
                                "collapsed_parent_designations": [],
                                "reason_codes": [],
                            },
                        },
                        "worker_semantics": {
                            "prompt_version": "prompt/v1",
                            "prompt_sha256": hashes["b"],
                            "semantic_prompt_changed": False,
                            "execution_contract_changed": True,
                        },
                        "governance": {
                            "decision_basis_id": "pro-reconciled-a-plus",
                            "public_repo_commit": "c" * 40,
                            "private_controller_commit": "d" * 40,
                            "migration_sha256": hashes["e"],
                            "admission_contract_version": "admission/v1",
                            "review_decision_receipt_sha256": hashes["f"],
                        },
                        "output_namespace": {
                            "generation": 2,
                            "relative_root": (
                                f"partition-recovery/"
                                f"{PARTITION_WORK_ITEM_ID}/generation-2"
                            ),
                        },
                    }
                    payload_bytes = canonical_json_bytes(payload)
                    payload_hash = sha256_bytes(payload_bytes)

                    cursor.execute(
                        "SET ROLE nhi_rule_history_recovery_authorizer"
                    )
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.admit_partition_recovery(
    %s::uuid, %s, %s::jsonb, %s::bytea, %s, %s::timestamptz
  )
""",
                        (
                            ADMISSION_ID,
                            payload_hash,
                            Jsonb(payload),
                            payload_bytes,
                            "fixture-operator",
                            "2099-01-01T00:00:00+00:00",
                        ),
                    )
                    self.assertFalse(cursor.fetchone()[2])
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.authorize_partition_recovery(
    %s::uuid, %s::uuid, %s::uuid, %s::uuid, 1, 2,
    %s, %s::timestamptz, %s, %s::timestamptz
  )
""",
                        (
                            AUTHORIZATION_ID,
                            INITIAL_TRANSITION_ID,
                            ADMISSION_ID,
                            PARTITION_WORK_ITEM_ID,
                            "dispatch/v1",
                            "2099-01-02T00:00:00+00:00",
                            "fixture-operator",
                            "2099-01-01T00:00:00+00:00",
                        ),
                    )
                    self.assertFalse(cursor.fetchone()[3])
                    cursor.execute(
                        "RESET ROLE"
                    )
                    cursor.execute(
                        "SET ROLE nhi_rule_history_update_queue_runtime"
                    )
                    consume_args = (
                        CLAIM_ID,
                        PARTITION_WORK_ITEM_ID,
                        AUTHORIZATION_ID,
                        ADMISSION_ID,
                        payload_hash,
                        hashes["1"],
                        hashes["5"],
                        new_job_fingerprint,
                        hashes["b"],
                        hashes["a"],
                        RECOVERY_JOB_ID,
                        LEASE_ID,
                    )
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid, 'dispatch/v1',
      %s, %s, %s, %s, %s, %s,
      %s::uuid, %s::uuid, 'fixture-owner', 180,
      '2099-01-01T00:03:00+00'::timestamptz,
      '2099-01-01T00:00:00+00'::timestamptz
    )
""",
                        consume_args,
                    )
                    consumed = cursor.fetchone()
                    assert consumed is not None
                    self.assertFalse(consumed[17])
                    self.assertEqual(str(consumed[12]), RECOVERY_JOB_ID)
                    self.assertEqual(str(consumed[13]), LEASE_ID)
                    namespace = (
                        f"partition-recovery/{PARTITION_WORK_ITEM_ID}/"
                        f"generation-2/{new_job_fingerprint}"
                    )
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    reserve_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      1::smallint,
      %s, %s, %s, %s::uuid, %s::uuid, %s,
      'codex', 'openai', 'fixture-model', %s,
      '2099-01-01T00:00:01+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            hashes["1"],
                            hashes["b"],
                            namespace,
                            RECOVERY_JOB_ID,
                            LEASE_ID,
                            "fixture-owner",
                            hashes["c"],
                        ),
                    )
                    self.assertFalse(cursor.fetchone()[7])

                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid, 'dispatch/v1',
      %s, %s, %s, %s, %s, %s,
      %s::uuid, %s::uuid, 'fixture-owner', 180,
      '2099-02-01T00:03:00+00'::timestamptz,
      '2099-02-01T00:00:00+00'::timestamptz
    )
""",
                        consume_args,
                    )
                    replay = cursor.fetchone()
                    assert replay is not None
                    self.assertTrue(replay[17])
                    self.assertEqual(str(replay[19]), RESERVATION_ID)
                    self.assertEqual(replay[20], 1)

                    unknown_receipt = {
                        "schema": "partition-route-outcome/v1",
                        "reason": "restart-after-reservation",
                    }
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    finish_partition_recovery_route(
      %s::uuid, %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid,
      'execution_unknown', NULL, NULL, NULL, NULL, NULL, NULL, false,
      %s, %s::jsonb, '2099-02-01T00:00:01+00'::timestamptz
    )
""",
                        (
                            RESERVATION_ID,
                            CLAIM_ID,
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            hashes["d"],
                            Jsonb(unknown_receipt),
                        ),
                    )
                    self.assertEqual(cursor.fetchone()[3], "execution_unknown")
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    consume_partition_recovery_dispatch(
      %s::uuid, %s::uuid, 2, %s::uuid, %s::uuid, 'dispatch/v1',
      %s, %s, %s, %s, %s, %s,
      %s::uuid, %s::uuid, 'fixture-owner', 180,
      '2099-03-01T00:03:00+00'::timestamptz,
      '2099-03-01T00:00:00+00'::timestamptz
    )
""",
                        consume_args,
                    )
                    finished_replay = cursor.fetchone()
                    assert finished_replay is not None
                    self.assertTrue(finished_replay[17])
                    self.assertIsNone(finished_replay[19])
                    self.assertEqual(finished_replay[22], 1)
                    self.assertEqual(
                        finished_replay[23][0]["status"],
                        "execution_unknown",
                    )
                    terminal_json = _terminal_evidence(
                        "failed_terminal",
                        reason_code=(
                            "restart_open_route_execution_unknown"
                        ),
                        failure_code=(
                            "RECOVERY_OPEN_ROUTE_EXECUTION_UNKNOWN"
                        ),
                        preexisting_output_namespace=True,
                        generation_state="proposal_running",
                        finished_route_statuses=[],
                        open_route_reconciled_as_execution_unknown=hashes["d"],
                        worker_reinvocation=False,
                        automatic_retry=False,
                        automatic_fallback=False,
                    )
                    terminal_identity = _terminal_identity(
                        terminal_json, source_job_id=RECOVERY_JOB_ID
                    )
                    cursor.execute("SAVEPOINT unknown_wrong_source")
                    with self.assertRaises(psycopg.Error):
                        cursor.execute(
                            """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, NULL, NULL, NULL,
      '2099-02-01T00:00:02+00'::timestamptz
    )
""",
                            (
                                terminal_identity["transition_id"],
                                terminal_identity[
                                    "transition_evidence_id"
                                ],
                                terminal_identity["terminal_receipt_id"],
                                PARTITION_WORK_ITEM_ID,
                                AUTHORIZATION_ID,
                                ADMISSION_ID,
                                TERMINAL_EVIDENCE_CONTRACT,
                                terminal_identity["evidence_sha256"],
                                Jsonb(terminal_json),
                            ),
                        )
                    cursor.execute(
                        "ROLLBACK TO SAVEPOINT unknown_wrong_source"
                    )
                    cursor.execute("RELEASE SAVEPOINT unknown_wrong_source")
                    cursor.execute(
                        """
SELECT *
FROM
  nhi_rule_history_partition_recovery.
    close_partition_recovery_generation(
      %s::uuid, %s::uuid, %s::uuid, %s::uuid, 2,
      %s::uuid, %s::uuid, 'failed_terminal', %s,
      %s, %s::jsonb, %s::uuid, NULL, NULL,
      '2099-02-01T00:00:02+00'::timestamptz
    )
""",
                        (
                            terminal_identity["transition_id"],
                            terminal_identity["transition_evidence_id"],
                            terminal_identity["terminal_receipt_id"],
                            PARTITION_WORK_ITEM_ID,
                            AUTHORIZATION_ID,
                            ADMISSION_ID,
                            TERMINAL_EVIDENCE_CONTRACT,
                            terminal_identity["evidence_sha256"],
                            Jsonb(terminal_json),
                            RECOVERY_JOB_ID,
                        ),
                    )
                    self.assertEqual(cursor.fetchone()[2], "failed_terminal")
                    cursor.execute("RESET ROLE")
                    cursor.execute(
                        """
SELECT
  (SELECT count(*) FROM nhi_rule_history_update_ops.update_job
   WHERE job_id = %s::uuid),
  (SELECT count(*) FROM nhi_rule_history_update_ops.job_lease
   WHERE lease_id = %s::uuid),
  (SELECT count(*) FROM
     nhi_rule_history_partition_recovery.worker_route_outcome
   WHERE status = 'execution_unknown'),
  (SELECT to_state FROM
     nhi_rule_history_update_queue.work_generation_transition
   WHERE transition_id = %s::uuid)
""",
                        (
                            RECOVERY_JOB_ID,
                            LEASE_ID,
                            terminal_identity["transition_id"],
                        ),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        (1, 1, 1, "failed_terminal"),
                    )
        finally:
            pg.close()
