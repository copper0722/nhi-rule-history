from __future__ import annotations

import contextlib
import io
import inspect
import json
import unittest
import uuid
from unittest import mock

from nhi_rule_history.cli import build_parser
from nhi_rule_history.contracts import canonical_json_bytes, sha256_bytes
from nhi_rule_history.update.pg_queue import (
    PARTITION_RECOVERY_ADMISSION_SCHEMA,
    PARTITION_RECOVERY_CANONICAL_ENCODING,
    PARTITION_RECOVERY_DISPATCH_CONTRACT,
    PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT,
    PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
    UpdateQueueError,
    _partition_recovery_sha256_uuid,
    _verify_partition_terminal_evidence,
    authorize_partition_recovery,
    close_partition_recovery_generation,
    consume_partition_recovery_dispatch,
    finish_partition_recovery_route,
    partition_recovery_output_namespace,
    reserve_partition_recovery_route,
    show_partition_recovery,
    verify_partition_recovery_admission,
    verify_partition_recovery_evidence,
)


WORK_ITEM_ID = "10000000-0000-0000-0000-000000000001"
AUTHORIZATION_ID = "20000000-0000-0000-0000-000000000001"
ADMISSION_ID = "30000000-0000-0000-0000-000000000001"
CLAIM_ID = "40000000-0000-0000-0000-000000000001"
LEASE_ID = "80000000-0000-0000-0000-000000000001"
STAGE_PROPOSAL_ID = "90000000-0000-0000-0000-000000000001"
BUNDLE_RECEIPT_ID = "a0000000-0000-0000-0000-000000000001"
TERMINAL_RECEIPT_ID = "b0000000-0000-0000-0000-000000000001"
RECOVERY_JOB_ID = str(
    uuid.uuid5(
        uuid.UUID("9b541831-50a7-5867-8c52-5b7ac7ea272c"),
        "\x1f".join(("partition-recovery-update-job", CLAIM_ID)),
    )
)
HEX = {
    character: character * 64
    for character in "0123456789abcdef"
}


def terminal_evidence(
    to_state: str,
    **extras: object,
) -> dict[str, object]:
    return {
        "schema": PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
        "dispatch_claim_id": CLAIM_ID,
        "work_item_id": WORK_ITEM_ID,
        "generation": 2,
        "authorization_id": AUTHORIZATION_ID,
        "admission_id": ADMISSION_ID,
        "to_state": to_state,
        "auto_promotion_enabled": False,
        **extras,
    }


def terminal_hash_and_id(
    evidence: dict[str, object],
) -> tuple[str, str]:
    digest = sha256_bytes(canonical_json_bytes(evidence))
    return digest, _partition_recovery_sha256_uuid(
        "partition-recovery-terminal-receipt",
        WORK_ITEM_ID,
        "2",
        str(evidence["to_state"]),
        digest,
    )


def persisted_terminal_result(
    *_args: object, **kwargs: object
) -> dict[str, object]:
    values = kwargs["values"]
    assert isinstance(values, tuple)
    return {
        "transition_id": values[0],
        "transition_seq": 7,
        "to_state": values[7],
        "terminal_receipt_id": values[2],
        "replayed": False,
        "recorded_at": values[-1],
        "transition_evidence_id": values[1],
        "evidence_sha256": values[9],
    }


def admission_payload() -> dict[str, object]:
    states = (
        (None, "observed"),
        ("observed", "selected"),
        ("selected", "acquired"),
        ("acquired", "corpus_registered"),
        ("corpus_registered", "proposal_running"),
        ("proposal_running", "partition_required"),
    )
    transitions = [
        {
            "work_item_id": WORK_ITEM_ID,
            "transition_seq": sequence,
            "transition_id": (
                f"00000000-0000-0000-0000-{sequence:012d}"
            ),
            "from_state": from_state,
            "to_state": to_state,
            "actor_kind": "fixture-stage",
            "evidence_sha256": f"{sequence:x}" * 64,
            "evidence_json": {
                "kind": "fixture-transition",
                "transition_seq": sequence,
            },
            "source_job_id": (
                f"01000000-0000-0000-0000-{sequence:012d}"
            ),
            "bundle_receipt_id": None,
            "candidate_proposal_id": None,
            "recorded_at": f"2026-07-28T00:00:0{sequence}+00:00",
        }
        for sequence, (from_state, to_state) in enumerate(states, start=1)
    ]
    ordered_chain_sha256 = sha256_bytes(
        canonical_json_bytes(transitions)
    )
    rowset_fingerprint = sha256_bytes(
        canonical_json_bytes(
            sorted(transitions, key=lambda row: row["transition_id"])
        )
    )
    artifacts = [
        {
            "logical_object_id": "packet-source",
            "logical_locator": "packet/source.json",
            "byte_count": 12,
            "sha256": HEX["a"],
            "media_type": "application/json",
        },
        {
            "logical_object_id": "packet-corpus",
            "logical_locator": "packet/corpus.json",
            "byte_count": 13,
            "sha256": HEX["b"],
            "media_type": "application/json",
        },
    ]
    artifact_digest = sha256_bytes(
        canonical_json_bytes(
            [
                {"ordinal": index, "sha256": artifact["sha256"]}
                for index, artifact in enumerate(artifacts, start=1)
            ]
        )
    )
    output_namespace = partition_recovery_output_namespace(
        work_item_id=WORK_ITEM_ID,
        generation=2,
        job_fingerprint=HEX["c"],
    )
    return {
        "schema": PARTITION_RECOVERY_ADMISSION_SCHEMA,
        "canonical_encoding_contract": (
            PARTITION_RECOVERY_CANONICAL_ENCODING
        ),
        "generation_1": {
            "work_item_id": WORK_ITEM_ID,
            "prior_generation": 1,
            "terminal_state": "partition_required",
            "terminal_transition_sequence": 6,
            "terminal_transition_id": transitions[-1]["transition_id"],
            "terminal_evidence_sha256": transitions[-1][
                "evidence_sha256"
            ],
            "old_job_fingerprint": HEX["9"],
            "old_partition_receipt": {
                "logical_object_id": "old-partition-receipt",
                "logical_locator": "generation-1/partition-receipt.json",
                "byte_count": 101,
                "sha256": HEX["7"],
                "media_type": "application/json",
            },
            "old_suitability_receipt": {
                "logical_object_id": "old-suitability-receipt",
                "logical_locator": "generation-1/suitability.json",
                "byte_count": 102,
                "sha256": HEX["8"],
                "media_type": "application/json",
            },
            "worker_call_count": 0,
            "worker_attempt_count": 0,
            "candidate_count": 0,
            "route_attempt_count": 0,
            "transition_count": 6,
            "ordered_chain_sha256": ordered_chain_sha256,
            "rowset_fingerprint": rowset_fingerprint,
            "transitions": transitions,
        },
        "source_evidence": {
            "source_bundle": {
                "bundle_id": "source-bundle",
                "manifest_sha256": HEX["d"],
                "byte_count": 1001,
                "logical_locator": "bundles/source",
            },
            "corpus_bundle": {
                "bundle_id": "corpus-bundle",
                "manifest_sha256": HEX["e"],
                "byte_count": 1002,
                "logical_locator": "corpus/bundle",
            },
            "sealed_packet": {
                "manifest_sha256": HEX["f"],
                "byte_count": 25,
                "logical_locator": "packet/manifest.json",
                "ordered_artifact_sha256_set_digest": artifact_digest,
                "artifacts": artifacts,
            },
            "reuse_existing_bundle": True,
            "repoll_allowed": False,
            "reacquire_allowed": False,
            "new_corpus_registration_allowed": False,
        },
        "execution_delta": {
            "old_suitability_contract": (
                "nhi-rule-history/worker-suitability/v1"
            ),
            "new_suitability_contract": (
                "nhi-rule-history/worker-suitability/v2"
            ),
            "old_fingerprint_domain": (
                "nhi-rule-history/worker-job-fingerprint/v3"
            ),
            "new_fingerprint_domain": (
                "nhi-rule-history/worker-job-fingerprint/v4"
            ),
            "new_job_fingerprint": HEX["c"],
            "suitability_preflight": {
                "designation_candidates": ["10.3", "10.3.8"],
                "effective_designation_candidates": ["10.3.8"],
                "collapsed_parent_designations": ["10.3"],
                "decision": "suitable",
                "reason_codes": [],
            },
            "suitability_v2_schema_sha256": HEX["0"],
            "suitability_v2_receipt_sha256": HEX["1"],
            "verifier_contract_version": "fixture-verifier/v1",
            "verifier_code_commit": "a" * 40,
            "verifier_config_sha256": HEX["2"],
            "verifier_executable_sha256": HEX["3"],
            "execution_contract_version": "fixture-execution/v2",
            "execution_contract_sha256": HEX["4"],
            "dispatch_contract_version": (
                PARTITION_RECOVERY_DISPATCH_CONTRACT
            ),
            "route_policy_sha256": HEX["5"],
        },
        "worker_semantics": {
            "prompt_version": "nhi-rule-history-source-proposal/2.0.0",
            "prompt_sha256": HEX["6"],
            "semantic_prompt_changed": False,
            "execution_contract_changed": True,
        },
        "governance": {
            "decision_basis_id": "partition-recovery-fixture",
            "public_repo_commit": "b" * 40,
            "private_controller_commit": "c" * 40,
            "migration_sha256": HEX["a"],
            "admission_contract_version": (
                PARTITION_RECOVERY_ADMISSION_SCHEMA
            ),
            "review_decision_receipt_sha256": HEX["b"],
        },
        "output_namespace": {
            "contract": PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT,
            "generation": 2,
            "relative_path": output_namespace,
        },
    }


def known_route_evidence(
    attempt_namespace: str,
    *,
    completed_at: str,
    lease_id: str = LEASE_ID,
    owner_key: str = "fixture-runtime",
) -> dict[str, object]:
    return {
        "lease_id": lease_id,
        "owner_key": owner_key,
        "started_at": "2026-07-28T00:00:00+00:00",
        "completed_at": completed_at,
        "raw_worker_attempt_id": HEX["a"],
        "attempt_namespace": attempt_namespace,
    }


def persisted_route_result(
    *,
    reservation_id: str,
    route_ordinal: int,
    status: str,
    failure_class: str | None,
    worker_attempt_id: str | None,
    replayed: bool = False,
) -> dict[str, object]:
    fallback_eligible = (
        route_ordinal == 1
        and status == "failed"
        and failure_class is not None
    )
    return {
        "reservation_id": reservation_id,
        "route_ordinal": route_ordinal,
        "route": "primary" if route_ordinal == 1 else "fallback",
        "status": status,
        "failure_class": (
            "execution_unknown"
            if status == "execution_unknown"
            else failure_class
        ),
        "worker_attempt_id": worker_attempt_id,
        "fallback_eligible": fallback_eligible,
        "replayed": replayed,
    }


class PartitionRecoveryEvidenceTests(unittest.TestCase):
    def test_full_payload_is_deterministic_and_generation_scoped(self) -> None:
        payload = admission_payload()
        first = verify_partition_recovery_evidence(payload)
        second = verify_partition_recovery_evidence(payload)
        self.assertEqual(first["admission_id"], second["admission_id"])
        self.assertEqual(
            first["admission_payload_sha256"],
            sha256_bytes(canonical_json_bytes(payload)),
        )
        self.assertIn("/generation-2/", first["output_namespace"])
        self.assertEqual(first["new_generation"], 2)

    def test_chain_packet_and_no_reacquisition_tampering_fail_closed(
        self,
    ) -> None:
        mutations = (
            (
                ("generation_1", "worker_call_count"),
                1,
                "zero prior worker",
            ),
            (
                ("generation_1", "ordered_chain_sha256"),
                HEX["0"][:-1],
                "lowercase SHA-256",
            ),
            (
                (
                    "source_evidence",
                    "sealed_packet",
                    "ordered_artifact_sha256_set_digest",
                ),
                HEX["0"],
                "ordered artifact digest",
            ),
            (
                ("source_evidence", "reacquire_allowed"),
                True,
                "forbid poll",
            ),
            (
                ("output_namespace", "relative_path"),
                "generation-1/collision",
                "generation-2",
            ),
        )
        for path, replacement, message in mutations:
            with self.subTest(path=path):
                payload = admission_payload()
                target = payload
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index]
                target[path[-1]] = replacement  # type: ignore[index]
                with self.assertRaisesRegex(UpdateQueueError, message):
                    verify_partition_recovery_evidence(payload)

    def test_float_and_absolute_locator_are_rejected(self) -> None:
        payload = admission_payload()
        payload["source_evidence"]["sealed_packet"]["byte_count"] = 1.0
        with self.assertRaisesRegex(UpdateQueueError, "floating-point"):
            verify_partition_recovery_evidence(payload)
        payload = admission_payload()
        payload["source_evidence"]["source_bundle"][
            "logical_locator"
        ] = "/private/source"
        with self.assertRaisesRegex(UpdateQueueError, "relative POSIX"):
            verify_partition_recovery_evidence(payload)

    def test_full_transition_projection_and_canonical_hashes_are_exact(
        self,
    ) -> None:
        payload = admission_payload()
        payload["generation_1"]["transitions"][0][
            "actor_kind"
        ] = "different-actor"
        with self.assertRaisesRegex(UpdateQueueError, "ordered chain hash"):
            verify_partition_recovery_evidence(payload)

        payload = admission_payload()
        payload["generation_1"]["transitions"][0]["created_at"] = (
            "2026-07-28T00:00:01+00:00"
        )
        with self.assertRaisesRegex(UpdateQueueError, "fields are invalid"):
            verify_partition_recovery_evidence(payload)

        payload = admission_payload()
        payload["generation_1"]["transitions"][0]["work_item_id"] = (
            "10000000-0000-0000-0000-000000000002"
        )
        with self.assertRaisesRegex(UpdateQueueError, "work item"):
            verify_partition_recovery_evidence(payload)


class PartitionRecoveryApiTests(unittest.TestCase):
    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value={"work_item_id": WORK_ITEM_ID, "verified": True},
    )
    def test_operator_verify_compares_live_generation_one_chain(
        self,
        database: mock.Mock,
    ) -> None:
        result = verify_partition_recovery_admission(
            "postgresql://example.invalid/test",
            evidence=admission_payload(),
        )
        self.assertTrue(result["database"]["verified"])
        self.assertEqual(
            database.call_args.kwargs["function_name"],
            "verify_partition_recovery_admission",
        )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result"
    )
    def test_authorization_and_consume_use_exact_deterministic_ids(
        self,
        database: mock.Mock,
    ) -> None:
        def persisted_result(
            _conninfo: str,
            *,
            function_name: str,
            values: tuple[object, ...],
            **_kwargs: object,
        ) -> dict[str, object]:
            if function_name != "consume_partition_recovery_dispatch":
                return {"replayed": False}
            return {
                "source_job_id": values[12],
                "lease_id": values[13],
                "owner_key": values[14],
                "max_runtime_seconds": values[15],
                "lease_expires_at": values[16],
                "replayed": False,
                "generation_state": "retry_pending",
                "open_reservation_id": None,
                "open_route_ordinal": None,
                "open_attempt_namespace": None,
                "finished_route_count": 0,
                "finished_route_statuses": [],
                "terminal_state": None,
                "terminal_receipt_id": None,
            }

        database.side_effect = persisted_result
        authorized = authorize_partition_recovery(
            "postgresql://example.invalid/test",
            admission_id=ADMISSION_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            admission_payload_sha256=HEX["0"],
            expires_at="2026-07-29T00:00:00+00:00",
            actor_kind="operator",
            authorized_at="2026-07-28T00:00:00+00:00",
        )
        replay = authorize_partition_recovery(
            "postgresql://example.invalid/test",
            admission_id=ADMISSION_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            admission_payload_sha256=HEX["0"],
            expires_at="2026-07-29T00:00:00+00:00",
            actor_kind="operator",
            authorized_at="2026-07-28T00:00:01+00:00",
        )
        self.assertEqual(
            authorized["authorization_id"], replay["authorization_id"]
        )
        consumed = consume_partition_recovery_dispatch(
            "postgresql://example.invalid/test",
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=authorized["authorization_id"],
            admission_id=ADMISSION_ID,
            admission_payload_sha256=HEX["0"],
            sealed_packet_manifest_sha256=HEX["1"],
            suitability_v2_receipt_sha256=HEX["2"],
            job_fingerprint=HEX["3"],
            prompt_sha256=HEX["4"],
            route_policy_sha256=HEX["5"],
            owner_key="fixture-runtime",
            max_runtime_seconds=300,
            consumed_at="2026-07-28T00:00:02+00:00",
        )
        self.assertIn("/generation-2/", consumed["output_namespace"])
        self.assertTrue(consumed["dispatch_claim_id"])
        self.assertTrue(consumed["recovery_job_id"])
        self.assertTrue(consumed["lease_id"])
        self.assertEqual(
            database.call_args_list[-1].kwargs["function_name"],
            "consume_partition_recovery_dispatch",
        )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result"
    )
    def test_consume_replay_exposes_exact_no_retry_reconciliation(
        self,
        database: mock.Mock,
    ) -> None:
        open_reservation_id = (
            "50000000-0000-0000-0000-000000000001"
        )

        def persisted_replay(
            _conninfo: str,
            *,
            values: tuple[object, ...],
            **_kwargs: object,
        ) -> dict[str, object]:
            namespace = partition_recovery_output_namespace(
                work_item_id=str(values[1]),
                generation=int(values[2]),
                job_fingerprint=str(values[9]),
            )
            return {
                "claim_id": values[0],
                "source_job_id": values[12],
                "lease_id": values[13],
                "owner_key": values[14],
                "max_runtime_seconds": values[15],
                "lease_expires_at": "2026-07-28T00:05:00+00:00",
                "replayed": True,
                "generation_state": "proposal_running",
                "open_reservation_id": open_reservation_id,
                "open_route_ordinal": 1,
                "open_attempt_namespace": namespace,
                "finished_route_count": 0,
                "finished_route_statuses": [],
                "terminal_state": None,
                "terminal_receipt_id": None,
            }

        database.side_effect = persisted_replay
        result = consume_partition_recovery_dispatch(
            "postgresql://example.invalid/test",
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            admission_payload_sha256=HEX["0"],
            sealed_packet_manifest_sha256=HEX["1"],
            suitability_v2_receipt_sha256=HEX["2"],
            job_fingerprint=HEX["3"],
            prompt_sha256=HEX["4"],
            route_policy_sha256=HEX["5"],
            owner_key="fixture-runtime",
            max_runtime_seconds=300,
            consumed_at="2026-07-28T01:00:00+00:00",
        )
        self.assertTrue(result["replayed"])
        self.assertEqual(result["generation_state"], "proposal_running")
        self.assertEqual(
            result["open_reservation"]["reservation_id"],
            open_reservation_id,
        )
        self.assertEqual(result["finished_route_count"], 0)
        self.assertEqual(
            result["lease_expires_at"],
            "2026-07-28T00:05:00+00:00",
        )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value={"replayed": False},
    )
    def test_route_reservation_and_finish_bind_attempt_namespace(
        self,
        database: mock.Mock,
    ) -> None:
        reserved = reserve_partition_recovery_route(
            "postgresql://example.invalid/test",
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            route_ordinal=1,
            packet_sha256=HEX["1"],
            prompt_sha256=HEX["2"],
            recovery_job_id=RECOVERY_JOB_ID,
            lease_id=LEASE_ID,
            owner_key="fixture-runtime",
            runtime_id="runtime",
            provider="provider",
            model="model",
            controller_commit_sha256=HEX["3"],
            job_fingerprint=HEX["4"],
            reserved_at="2026-07-28T00:00:00+00:00",
        )
        self.assertEqual(
            reserved["attempt_namespace"], reserved["output_namespace"]
        )
        database.return_value = persisted_route_result(
            reservation_id=reserved["reservation_id"],
            route_ordinal=1,
            status="failed",
            failure_class="output_schema_invalid",
            worker_attempt_id=reserved["generation_bound_attempt_id"],
        )
        finished = finish_partition_recovery_route(
            "postgresql://example.invalid/test",
            reservation_id=reserved["reservation_id"],
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            route_ordinal=1,
            attempt_namespace=reserved["attempt_namespace"],
            job_fingerprint=HEX["4"],
            recovery_job_id=RECOVERY_JOB_ID,
            lease_id=LEASE_ID,
            owner_key="fixture-runtime",
            status="failed",
            failure_class="output_schema_invalid",
            worker_attempt_id=reserved["generation_bound_attempt_id"],
            result_receipt_sha256=HEX["5"],
            evidence=known_route_evidence(
                reserved["attempt_namespace"],
                completed_at="2026-07-28T00:00:01+00:00",
            ),
            completed_at="2026-07-28T00:00:01+00:00",
        )
        self.assertTrue(finished["automatic_fallback_allowed"])
        self.assertFalse(finished["automatic_retry_allowed"])
        self.assertEqual(
            database.call_args_list[-1].kwargs["function_name"],
            "finish_partition_recovery_route",
        )
        persisted_receipt = json.loads(
            database.call_args_list[-1].kwargs["values"][-2]
        )
        self.assertEqual(
            persisted_receipt["raw_worker_attempt_id"], HEX["a"]
        )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value=persisted_route_result(
            reservation_id="50000000-0000-0000-0000-000000000001",
            route_ordinal=1,
            status="execution_unknown",
            failure_class=None,
            worker_attempt_id=None,
        ),
    )
    def test_execution_unknown_forbids_retry_and_fallback(
        self,
        _database: mock.Mock,
    ) -> None:
        namespace = partition_recovery_output_namespace(
            work_item_id=WORK_ITEM_ID,
            generation=2,
            job_fingerprint=HEX["4"],
        )
        result = finish_partition_recovery_route(
            "postgresql://example.invalid/test",
            reservation_id="50000000-0000-0000-0000-000000000001",
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            route_ordinal=1,
            attempt_namespace=namespace,
            job_fingerprint=HEX["4"],
            recovery_job_id=RECOVERY_JOB_ID,
            lease_id=LEASE_ID,
            owner_key="fixture-runtime",
            status="execution_unknown",
            result_receipt_sha256=HEX["5"],
            evidence={"kind": "crash_after_reservation"},
        )
        self.assertFalse(result["automatic_retry_allowed"])
        self.assertFalse(result["automatic_fallback_allowed"])
        self.assertEqual(result["failure_class"], "execution_unknown")

    def test_finish_rejects_unverified_database_route_receipts(self) -> None:
        reservation_id = "50000000-0000-0000-0000-000000000001"
        namespace = partition_recovery_output_namespace(
            work_item_id=WORK_ITEM_ID,
            generation=2,
            job_fingerprint=HEX["4"],
        )
        valid = persisted_route_result(
            reservation_id=reservation_id,
            route_ordinal=1,
            status="execution_unknown",
            failure_class=None,
            worker_attempt_id=None,
        )
        mutations = (
            ("reservation_id", "50000000-0000-0000-0000-000000000002"),
            ("route_ordinal", 2),
            ("route", "fallback"),
            ("status", "failed"),
            ("failure_class", None),
            (
                "worker_attempt_id",
                "60000000-0000-0000-0000-000000000001",
            ),
            ("fallback_eligible", True),
            ("automatic_fallback_allowed", True),
            ("automatic_retry_allowed", True),
        )
        for field, replacement in mutations:
            with self.subTest(field=field):
                persisted = dict(valid)
                persisted[field] = replacement
                with (
                    mock.patch(
                        "nhi_rule_history.update.pg_queue."
                        "_partition_function_result",
                        return_value=persisted,
                    ),
                    self.assertRaisesRegex(
                        UpdateQueueError, "database|Database"
                    ),
                ):
                    finish_partition_recovery_route(
                        "postgresql://example.invalid/test",
                        reservation_id=reservation_id,
                        dispatch_claim_id=CLAIM_ID,
                        work_item_id=WORK_ITEM_ID,
                        generation=2,
                        authorization_id=AUTHORIZATION_ID,
                        admission_id=ADMISSION_ID,
                        route_ordinal=1,
                        attempt_namespace=namespace,
                        job_fingerprint=HEX["4"],
                        recovery_job_id=RECOVERY_JOB_ID,
                        lease_id=LEASE_ID,
                        owner_key="fixture-runtime",
                        status="execution_unknown",
                        result_receipt_sha256=HEX["5"],
                        evidence={"kind": "crash_after_reservation"},
                    )

        with (
            mock.patch(
                "nhi_rule_history.update.pg_queue."
                "_partition_function_result",
                return_value={"replayed": False},
            ),
            self.assertRaisesRegex(UpdateQueueError, "missing fields"),
        ):
            finish_partition_recovery_route(
                "postgresql://example.invalid/test",
                reservation_id=reservation_id,
                dispatch_claim_id=CLAIM_ID,
                work_item_id=WORK_ITEM_ID,
                generation=2,
                authorization_id=AUTHORIZATION_ID,
                admission_id=ADMISSION_ID,
                route_ordinal=1,
                attempt_namespace=namespace,
                job_fingerprint=HEX["4"],
                recovery_job_id=RECOVERY_JOB_ID,
                lease_id=LEASE_ID,
                owner_key="fixture-runtime",
                status="execution_unknown",
                result_receipt_sha256=HEX["5"],
                evidence={"kind": "crash_after_reservation"},
            )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value={"replayed": False},
    )
    def test_fallback_route_failure_never_allows_a_third_route(
        self,
        database: mock.Mock,
    ) -> None:
        reserved = reserve_partition_recovery_route(
            "postgresql://example.invalid/test",
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            route_ordinal=2,
            packet_sha256=HEX["1"],
            prompt_sha256=HEX["2"],
            recovery_job_id=RECOVERY_JOB_ID,
            lease_id=LEASE_ID,
            owner_key="fixture-runtime",
            runtime_id="runtime",
            provider="provider",
            model="model",
            controller_commit_sha256=HEX["3"],
            job_fingerprint=HEX["4"],
        )
        database.return_value = persisted_route_result(
            reservation_id=reserved["reservation_id"],
            route_ordinal=2,
            status="failed",
            failure_class="transport_failure",
            worker_attempt_id=reserved["generation_bound_attempt_id"],
        )
        finished = finish_partition_recovery_route(
            "postgresql://example.invalid/test",
            reservation_id=reserved["reservation_id"],
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            route_ordinal=2,
            attempt_namespace=reserved["attempt_namespace"],
            job_fingerprint=HEX["4"],
            recovery_job_id=RECOVERY_JOB_ID,
            lease_id=LEASE_ID,
            owner_key="fixture-runtime",
            status="failed",
            failure_class="transport_failure",
            worker_attempt_id=reserved["generation_bound_attempt_id"],
            result_receipt_sha256=HEX["5"],
            evidence=known_route_evidence(
                reserved["attempt_namespace"],
                completed_at="2026-07-28T00:00:01+00:00",
            ),
            completed_at="2026-07-28T00:00:01+00:00",
        )
        self.assertFalse(finished["automatic_fallback_allowed"])
        self.assertFalse(
            finished["primary_failure_fallback_eligible"]
        )

    def test_invalid_fallback_class_and_ambiguous_show_are_rejected(
        self,
    ) -> None:
        namespace = partition_recovery_output_namespace(
            work_item_id=WORK_ITEM_ID,
            generation=2,
            job_fingerprint=HEX["4"],
        )
        with self.assertRaisesRegex(UpdateQueueError, "allowlisted"):
            finish_partition_recovery_route(
                "postgresql://example.invalid/test",
                reservation_id=(
                    "50000000-0000-0000-0000-000000000001"
                ),
                dispatch_claim_id=CLAIM_ID,
                work_item_id=WORK_ITEM_ID,
                generation=2,
                authorization_id=AUTHORIZATION_ID,
                admission_id=ADMISSION_ID,
                route_ordinal=1,
                attempt_namespace=namespace,
                job_fingerprint=HEX["4"],
                recovery_job_id=RECOVERY_JOB_ID,
                lease_id=LEASE_ID,
                owner_key="fixture-runtime",
                status="failed",
                failure_class="low_confidence",
                worker_attempt_id=(
                    "60000000-0000-0000-0000-000000000001"
                ),
                result_receipt_sha256=HEX["5"],
                evidence=known_route_evidence(
                    namespace,
                    completed_at="2026-07-28T00:00:01+00:00",
                ),
                completed_at="2026-07-28T00:00:01+00:00",
            )
        with self.assertRaisesRegex(UpdateQueueError, "exactly one"):
            show_partition_recovery(
                "postgresql://example.invalid/test"
            )

    def test_finish_accepts_only_pg_stage_candidate_at_close(self) -> None:
        parameters = inspect.signature(
            finish_partition_recovery_route
        ).parameters
        self.assertNotIn("candidate_proposal_id", parameters)

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value={"terminal_state": "staged_needs_review"},
    )
    def legacy_close_accepts_only_canonical_pg_stage_proposal_uuid(
        self,
        database: mock.Mock,
    ) -> None:
        result = close_partition_recovery_generation(
            "postgresql://example.invalid/test",
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            to_state="staged_needs_review",
            evidence_contract="fixture-terminal/v1",
            evidence_sha256=HEX["7"],
            evidence={"dispatch_claim_id": CLAIM_ID},
            terminal_receipt_id=TERMINAL_RECEIPT_ID,
            source_job_id=RECOVERY_JOB_ID,
            bundle_receipt_id=BUNDLE_RECEIPT_ID,
            candidate_proposal_id=STAGE_PROPOSAL_ID,
            closed_at="2026-07-28T00:00:02+00:00",
        )
        self.assertEqual(
            result["candidate_proposal_id"], STAGE_PROPOSAL_ID
        )
        self.assertEqual(
            database.call_args.kwargs["function_name"],
            "close_partition_recovery_generation",
        )
        with self.assertRaisesRegex(UpdateQueueError, "UUID"):
            close_partition_recovery_generation(
                "postgresql://example.invalid/test",
                dispatch_claim_id=CLAIM_ID,
                work_item_id=WORK_ITEM_ID,
                generation=2,
                authorization_id=AUTHORIZATION_ID,
                admission_id=ADMISSION_ID,
                to_state="staged_needs_review",
                evidence_contract="fixture-terminal/v1",
                evidence_sha256=HEX["7"],
                evidence={"dispatch_claim_id": CLAIM_ID},
                terminal_receipt_id=TERMINAL_RECEIPT_ID,
                source_job_id=RECOVERY_JOB_ID,
                bundle_receipt_id=BUNDLE_RECEIPT_ID,
                candidate_proposal_id=HEX["a"],
            )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        return_value={"terminal_state": "failed_terminal"},
    )
    def legacy_close_enforces_typed_terminal_reason_and_identifiers(
        self,
        _database: mock.Mock,
    ) -> None:
        common = {
            "conninfo": "postgresql://example.invalid/test",
            "dispatch_claim_id": CLAIM_ID,
            "work_item_id": WORK_ITEM_ID,
            "generation": 2,
            "authorization_id": AUTHORIZATION_ID,
            "admission_id": ADMISSION_ID,
            "to_state": "failed_terminal",
            "evidence_contract": "fixture-terminal/v1",
            "evidence_sha256": HEX["7"],
            "terminal_receipt_id": TERMINAL_RECEIPT_ID,
        }
        precall = close_partition_recovery_generation(
            **common,
            evidence={
                "dispatch_claim_id": CLAIM_ID,
                "reason_code": "preflight_replay_mismatch",
            },
        )
        self.assertIsNone(precall["source_job_id"])
        executed = close_partition_recovery_generation(
            **common,
            evidence={
                "dispatch_claim_id": CLAIM_ID,
                "reason_code": "execution_unknown",
            },
            source_job_id=RECOVERY_JOB_ID,
        )
        self.assertEqual(executed["source_job_id"], RECOVERY_JOB_ID)

        invalid_cases = (
            (
                {"dispatch_claim_id": CLAIM_ID},
                None,
                None,
                None,
                "reason_code|non-empty",
            ),
            (
                {
                    "dispatch_claim_id": CLAIM_ID,
                    "reason_code": "retry_again",
                },
                None,
                None,
                None,
                "allowlisted",
            ),
            (
                {
                    "dispatch_claim_id": CLAIM_ID,
                    "reason_code": "packet_or_contract_tamper",
                },
                RECOVERY_JOB_ID,
                None,
                None,
                "cannot claim a source job",
            ),
            (
                {
                    "dispatch_claim_id": CLAIM_ID,
                    "reason_code": "execution_unknown",
                },
                None,
                None,
                None,
                "requires its recovery source job",
            ),
            (
                {
                    "dispatch_claim_id": CLAIM_ID,
                    "reason_code": "primary_and_fallback_failed",
                },
                RECOVERY_JOB_ID,
                BUNDLE_RECEIPT_ID,
                None,
                "cannot carry a bundle",
            ),
            (
                {
                    "dispatch_claim_id": CLAIM_ID,
                    "reason_code": "restart_after_model_result",
                },
                RECOVERY_JOB_ID,
                None,
                STAGE_PROPOSAL_ID,
                "cannot carry a bundle",
            ),
        )
        for (
            evidence,
            source_job_id,
            bundle_receipt_id,
            candidate_proposal_id,
            message,
        ) in invalid_cases:
            with (
                self.subTest(reason=evidence.get("reason_code")),
                self.assertRaisesRegex(UpdateQueueError, message),
            ):
                close_partition_recovery_generation(
                    **common,
                    evidence=evidence,
                    source_job_id=source_job_id,
                    bundle_receipt_id=bundle_receipt_id,
                    candidate_proposal_id=candidate_proposal_id,
                )

    def legacy_staged_close_requires_complete_pg_stage_tuple(self) -> None:
        identifiers = {
            "source_job_id": RECOVERY_JOB_ID,
            "bundle_receipt_id": BUNDLE_RECEIPT_ID,
            "candidate_proposal_id": STAGE_PROPOSAL_ID,
        }
        for missing in identifiers:
            incomplete = dict(identifiers)
            incomplete[missing] = None
            with (
                self.subTest(missing=missing),
                self.assertRaisesRegex(UpdateQueueError, "requires"),
            ):
                close_partition_recovery_generation(
                    "postgresql://example.invalid/test",
                    dispatch_claim_id=CLAIM_ID,
                    work_item_id=WORK_ITEM_ID,
                    generation=2,
                    authorization_id=AUTHORIZATION_ID,
                    admission_id=ADMISSION_ID,
                    to_state="staged_needs_review",
                    evidence_contract="fixture-terminal/v1",
                    evidence_sha256=HEX["7"],
                    evidence={"dispatch_claim_id": CLAIM_ID},
                    terminal_receipt_id=TERMINAL_RECEIPT_ID,
                    **incomplete,
                )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
        side_effect=persisted_terminal_result,
    )
    def test_close_verifies_terminal_hash_ids_and_database_receipt(
        self,
        database: mock.Mock,
    ) -> None:
        evidence = terminal_evidence(
            "staged_needs_review",
            candidate_receipt_sha256=HEX["7"],
            candidate_state="needs_review",
            selected_worker_role="fallback",
            worker_calls=2,
            finished_routes={"primary": HEX["8"], "fallback": HEX["9"]},
            canonical_history_writes=0,
        )
        evidence_sha256, terminal_receipt_id = terminal_hash_and_id(evidence)
        common = {
            "conninfo": "postgresql://example.invalid/test",
            "dispatch_claim_id": CLAIM_ID,
            "work_item_id": WORK_ITEM_ID,
            "generation": 2,
            "authorization_id": AUTHORIZATION_ID,
            "admission_id": ADMISSION_ID,
            "to_state": "staged_needs_review",
            "evidence_contract": PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
            "evidence_sha256": evidence_sha256,
            "evidence": evidence,
            "terminal_receipt_id": terminal_receipt_id,
            "source_job_id": RECOVERY_JOB_ID,
            "bundle_receipt_id": BUNDLE_RECEIPT_ID,
            "candidate_proposal_id": STAGE_PROPOSAL_ID,
        }
        result = close_partition_recovery_generation(**common)
        self.assertEqual(result["transition_seq"], 7)
        self.assertFalse(result["replayed"])
        for field, value, message in (
            ("evidence_contract", "wrong/v1", "terminal evidence contract"),
            ("evidence_sha256", HEX["0"], "canonical terminal evidence"),
            (
                "terminal_receipt_id",
                TERMINAL_RECEIPT_ID,
                "bound to the exact terminal evidence",
            ),
            ("candidate_proposal_id", HEX["a"], "UUID"),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(UpdateQueueError, message),
            ):
                close_partition_recovery_generation(
                    **{**common, field: value}
                )
        expected = persisted_terminal_result(**database.call_args.kwargs)
        database.side_effect = None
        for field, value in (
            ("transition_id", str(uuid.uuid4())),
            ("transition_seq", 0),
            ("to_state", "failed_terminal"),
            ("terminal_receipt_id", str(uuid.uuid4())),
            ("replayed", "false"),
            ("transition_evidence_id", str(uuid.uuid4())),
            ("evidence_sha256", HEX["0"]),
        ):
            database.return_value = {**expected, field: value}
            with self.subTest(database_field=field), self.assertRaises(
                UpdateQueueError
            ):
                close_partition_recovery_generation(**common)

    def test_close_rejects_forged_terminal_core_and_variant(self) -> None:
        evidence = terminal_evidence(
            "failed_terminal",
            reason_code="execution_unknown",
            failure_code="WORKER_EXECUTION_UNKNOWN",
            execution_unknown_routes={"primary": HEX["8"]},
            automatic_retry=False,
            automatic_fallback=False,
        )
        evidence_sha256, terminal_receipt_id = terminal_hash_and_id(evidence)
        common = {
            "conninfo": "postgresql://example.invalid/test",
            "dispatch_claim_id": CLAIM_ID,
            "work_item_id": WORK_ITEM_ID,
            "generation": 2,
            "authorization_id": AUTHORIZATION_ID,
            "admission_id": ADMISSION_ID,
            "to_state": "failed_terminal",
            "evidence_contract": PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
            "evidence_sha256": evidence_sha256,
            "evidence": evidence,
            "terminal_receipt_id": terminal_receipt_id,
            "source_job_id": RECOVERY_JOB_ID,
        }
        forged = dict(evidence)
        forged["work_item_id"] = str(uuid.uuid4())
        forged_hash, forged_id = terminal_hash_and_id(forged)
        with self.assertRaisesRegex(UpdateQueueError, "core tuple"):
            close_partition_recovery_generation(
                **{
                    **common,
                    "evidence": forged,
                    "evidence_sha256": forged_hash,
                    "terminal_receipt_id": forged_id,
                }
            )
        contradictory = dict(evidence)
        contradictory["automatic_retry"] = True
        contradictory_hash, contradictory_id = terminal_hash_and_id(
            contradictory
        )
        with self.assertRaisesRegex(UpdateQueueError, "automatic retry"):
            close_partition_recovery_generation(
                **{
                    **common,
                    "evidence": contradictory,
                    "evidence_sha256": contradictory_hash,
                    "terminal_receipt_id": contradictory_id,
                }
            )
        packet_wrong_shape = terminal_evidence(
            "failed_terminal",
            reason_code="packet_or_contract_tamper",
            failure_code="PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE",
            admitted=False,
            replayed=False,
            worker_calls=0,
            automatic_retry=False,
        )
        packet_hash, packet_id = terminal_hash_and_id(packet_wrong_shape)
        with self.assertRaisesRegex(UpdateQueueError, "fields are invalid"):
            close_partition_recovery_generation(
                **{
                    **common,
                    "evidence": packet_wrong_shape,
                    "evidence_sha256": packet_hash,
                    "terminal_receipt_id": packet_id,
                    "source_job_id": None,
                }
            )
        packet_contradiction = terminal_evidence(
            "failed_terminal",
            reason_code="packet_or_contract_tamper",
            failure_code="PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE",
            preexisting_output_namespace=False,
            generation_state="retry_pending",
            finished_route_statuses=[],
            open_route_reconciled_as_execution_unknown=None,
            worker_reinvocation=False,
            automatic_retry=False,
            automatic_fallback=False,
        )
        packet_hash, packet_id = terminal_hash_and_id(packet_contradiction)
        with self.assertRaisesRegex(
            UpdateQueueError, "tamper terminal evidence is contradictory"
        ):
            close_partition_recovery_generation(
                **{
                    **common,
                    "evidence": packet_contradiction,
                    "evidence_sha256": packet_hash,
                    "terminal_receipt_id": packet_id,
                    "source_job_id": None,
                }
            )

    @mock.patch(
        "nhi_rule_history.update.pg_queue._partition_function_result",
    )
    def test_close_replay_returns_stored_recorded_at(
        self,
        database: mock.Mock,
    ) -> None:
        evidence = terminal_evidence(
            "staged_needs_review",
            candidate_receipt_sha256=HEX["7"],
            candidate_state="needs_review",
            selected_worker_role="primary",
            worker_calls=1,
            finished_routes={"primary": HEX["8"]},
            canonical_history_writes=0,
        )
        evidence_sha256, terminal_receipt_id = terminal_hash_and_id(evidence)

        def stored_receipt(*args: object, **kwargs: object):
            receipt = persisted_terminal_result(*args, **kwargs)
            receipt["replayed"] = True
            receipt["recorded_at"] = "2026-07-28T00:00:02+00:00"
            return receipt

        database.side_effect = stored_receipt
        result = close_partition_recovery_generation(
            "postgresql://example.invalid/test",
            dispatch_claim_id=CLAIM_ID,
            work_item_id=WORK_ITEM_ID,
            generation=2,
            authorization_id=AUTHORIZATION_ID,
            admission_id=ADMISSION_ID,
            to_state="staged_needs_review",
            evidence_contract=PARTITION_RECOVERY_TERMINAL_EVIDENCE_SCHEMA,
            evidence_sha256=evidence_sha256,
            evidence=evidence,
            terminal_receipt_id=terminal_receipt_id,
            source_job_id=RECOVERY_JOB_ID,
            bundle_receipt_id=BUNDLE_RECEIPT_ID,
            candidate_proposal_id=STAGE_PROPOSAL_ID,
            closed_at="2026-07-28T00:00:09+00:00",
        )
        self.assertTrue(result["replayed"])
        self.assertEqual(
            result["closed_at"], "2026-07-28T00:00:02+00:00"
        )

    def test_terminal_canonical_hash_and_sha256_uuid_vector(self) -> None:
        evidence = terminal_evidence(
            "staged_needs_review",
            candidate_receipt_sha256=HEX["7"],
            candidate_state="needs_review",
            selected_worker_role="primary",
            worker_calls=1,
            finished_routes={"primary": HEX["8"]},
            canonical_history_writes=0,
        )
        payload = canonical_json_bytes(evidence)
        self.assertTrue(payload.endswith(b"\n"))
        digest, terminal_id = terminal_hash_and_id(evidence)
        self.assertEqual(
            digest,
            "0d5953b153bf0cf39ae0b212e684253a"
            "b2b4ecb21445af25455fb77837bdb17c",
        )
        self.assertEqual(
            terminal_id, "7907d9e4-0c99-883a-b69a-b3f65612a540"
        )

    def test_all_terminal_evidence_variants_are_exact_and_admitted(
        self,
    ) -> None:
        route_status = {
            "reservation_id": (
                "50000000-0000-0000-0000-000000000001"
            ),
            "route_ordinal": 1,
            "route": "primary",
            "status": "failed",
            "failure_class": "timeout",
        }
        variants = (
            {
                "reason_code": "preflight_replay_mismatch",
                "failure_code": "ADMITTED_SUITABILITY_REPLAY_MISMATCH",
                "admitted": {"receipt": HEX["1"]},
                "replayed": {"receipt": HEX["2"]},
                "worker_calls": 0,
                "automatic_retry": False,
            },
            {
                "reason_code": "preflight_nondeterminism",
                "failure_code": (
                    "ADMITTED_SUITABILITY_CHANGED_DURING_RUN"
                ),
                "worker_status": "changed",
                "worker_calls": 0,
                "automatic_retry": False,
            },
            {
                "reason_code": "packet_or_contract_tamper",
                "failure_code": (
                    "PREEXISTING_OUTPUT_WITHOUT_DB_EVIDENCE"
                ),
                "preexisting_output_namespace": True,
                "generation_state": "retry_pending",
                "finished_route_statuses": [],
                "open_route_reconciled_as_execution_unknown": None,
                "worker_reinvocation": False,
                "automatic_retry": False,
                "automatic_fallback": False,
            },
            {
                "reason_code": "restart_before_model_reservation",
                "failure_code": (
                    "RECOVERY_RESTART_BEFORE_MODEL_RESERVATION"
                ),
                "preexisting_output_namespace": False,
                "generation_state": "retry_pending",
                "finished_route_statuses": [],
                "open_route_reconciled_as_execution_unknown": None,
                "worker_reinvocation": False,
                "automatic_retry": False,
                "automatic_fallback": False,
            },
            {
                "reason_code": "restart_after_model_result",
                "failure_code": "RECOVERY_RESTART_AFTER_MODEL_RESULT",
                "preexisting_output_namespace": True,
                "generation_state": "proposal_running",
                "finished_route_statuses": [route_status],
                "open_route_reconciled_as_execution_unknown": None,
                "worker_reinvocation": False,
                "automatic_retry": False,
                "automatic_fallback": False,
            },
            {
                "reason_code": "restart_open_route_execution_unknown",
                "failure_code": "RECOVERY_OPEN_ROUTE_EXECUTION_UNKNOWN",
                "preexisting_output_namespace": True,
                "generation_state": "proposal_running",
                "finished_route_statuses": [],
                "open_route_reconciled_as_execution_unknown": HEX["3"],
                "worker_reinvocation": False,
                "automatic_retry": False,
                "automatic_fallback": False,
            },
            {
                "reason_code": "execution_unknown",
                "failure_code": "WORKER_EXECUTION_UNKNOWN",
                "execution_unknown_routes": {"primary": HEX["4"]},
                "automatic_retry": False,
                "automatic_fallback": False,
            },
            {
                "reason_code": "primary_and_fallback_failed",
                "failure_code": "PRIMARY_AND_FALLBACK_FAILED",
                "failure_receipt_sha256": HEX["5"],
                "finished_routes": {
                    "primary": HEX["6"],
                    "fallback": HEX["7"],
                },
                "automatic_retry": False,
            },
        )
        for extras in variants:
            evidence = terminal_evidence("failed_terminal", **extras)
            with self.subTest(reason=extras["reason_code"]):
                verified = _verify_partition_terminal_evidence(
                    evidence,
                    dispatch_claim_id=CLAIM_ID,
                    work_item_id=WORK_ITEM_ID,
                    generation=2,
                    authorization_id=AUTHORIZATION_ID,
                    admission_id=ADMISSION_ID,
                    to_state="failed_terminal",
                )
                self.assertEqual(verified, evidence)


class PartitionRecoveryCliTests(unittest.TestCase):
    def test_operator_and_runtime_commands_are_exact_only(self) -> None:
        parser = build_parser()
        verified = parser.parse_args(
            [
                "partition-recovery",
                "verify",
                "--evidence-json",
                "evidence.json",
            ]
        )
        self.assertEqual(verified.partition_recovery_command, "verify")
        consumed = parser.parse_args(
            [
                "dispatch-v2",
                "consume",
                "--work-item-id",
                WORK_ITEM_ID,
                "--generation",
                "2",
                "--authorization-id",
                AUTHORIZATION_ID,
                "--admission-id",
                ADMISSION_ID,
                "--admission-payload-sha256",
                HEX["0"],
                "--sealed-packet-manifest-sha256",
                HEX["1"],
                "--suitability-v2-receipt-sha256",
                HEX["2"],
                "--job-fingerprint",
                HEX["3"],
                "--prompt-sha256",
                HEX["4"],
                "--route-policy-sha256",
                HEX["5"],
                "--owner-key",
                "fixture-runtime",
                "--max-runtime-seconds",
                "300",
            ]
        )
        self.assertEqual(consumed.dispatch_v2_command, "consume")
        for forbidden in ("--latest", "--next", "--force"):
            with (
                self.subTest(forbidden=forbidden),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["dispatch-v2", "consume", forbidden])

    def test_partition_recovery_command_set_has_no_acquisition_lane(
        self,
    ) -> None:
        parser = build_parser()
        partition_action = next(
            action
            for action in parser._actions
            if getattr(action, "choices", None)
            and "partition-recovery" in action.choices
        )
        partition_parser = partition_action.choices["partition-recovery"]
        subcommand_action = next(
            action
            for action in partition_parser._actions
            if getattr(action, "choices", None)
        )
        self.assertEqual(
            set(subcommand_action.choices),
            {"verify", "admit", "authorize", "show", "revoke"},
        )
        self.assertTrue(
            {"poll", "acquire", "register", "write"}.isdisjoint(
                subcommand_action.choices
            )
        )

        dispatch_parser = next(
            action.choices["dispatch-v2"]
            for action in parser._actions
            if getattr(action, "choices", None)
            and "dispatch-v2" in action.choices
        )
        dispatch_action = next(
            action
            for action in dispatch_parser._actions
            if getattr(action, "choices", None)
        )
        for lane in ("reserve-route", "finish-route"):
            option_strings = {
                option
                for action in dispatch_action.choices[lane]._actions
                for option in action.option_strings
            }
            self.assertTrue(
                {
                    "--recovery-job-id",
                    "--lease-id",
                    "--owner-key",
                }.issubset(option_strings)
            )
        finish_options = {
            option
            for action in dispatch_action.choices["finish-route"]._actions
            for option in action.option_strings
        }
        self.assertNotIn("--candidate-proposal-id", finish_options)


if __name__ == "__main__":
    unittest.main()
