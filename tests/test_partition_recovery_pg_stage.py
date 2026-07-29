from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest import mock

import psycopg

from nhi_rule_history.update.pg_queue import (
    PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT,
    admit_partition_recovery,
    authorize_partition_recovery,
    consume_partition_recovery_dispatch,
    finish_partition_recovery_route,
    partition_recovery_output_namespace,
    reserve_partition_recovery_route,
    verify_partition_recovery_admission,
)
from nhi_rule_history.update.pg_stage import (
    UpdateStageLoadError,
    _apply_partition_recovery_candidate,
    _prepare_partition_recovery_candidate,
    _verify_partition_recovery_prerequisites,
    load_partition_recovery_candidate,
)
from tests.test_update_pg_loader import prepare_fixture
from tests import test_partition_recovery_api_cli as api_contract
from tests.test_partition_recovery_migration import (
    FORWARD as PARTITION_RECOVERY_FORWARD,
    _base_cluster,
)
from tests.test_update_queue_recovery_v2 import PARTITION_WORK_ITEM_ID
from nhi_rule_history.contracts import (
    canonical_json_bytes,
    file_sha256,
    sha256_bytes,
    write_json,
)


RECOVERY_JOB_ID = "51000000-0000-0000-0000-000000000001"
LEASE_ID = "51000000-0000-0000-0000-000000000002"
PRODUCER_ATTEMPT_ID = "51000000-0000-0000-0000-000000000003"
CLAIM_ID = "51000000-0000-0000-0000-000000000004"
WORK_ITEM_ID = "51000000-0000-0000-0000-000000000005"
ADMISSION_ID = "51000000-0000-0000-0000-000000000006"
SEALED_PACKET_MANIFEST_SHA256 = "a1" * 32


def prepare_recovery_material(root: Path):
    bundle, receipt, base = prepare_fixture(root)
    output_namespace = partition_recovery_output_namespace(
        work_item_id=WORK_ITEM_ID,
        generation=2,
        job_fingerprint=base.job_fingerprint,
    )
    material = _prepare_partition_recovery_candidate(
        bundle_path=bundle,
        candidate_receipt_path=receipt,
        bundle_relative_path="tw-gov/nhi/test-bundle",
        activation_cut="2026-07-27",
        owner_key="fixture-owner",
        notification_window_start="2026-07-27T00:00:00+00:00",
        notification_window_end="2026-07-27T00:05:00+00:00",
        recovery_job_id=RECOVERY_JOB_ID,
        lease_id=LEASE_ID,
        producer_attempt_id=PRODUCER_ATTEMPT_ID,
        dispatch_claim_id=CLAIM_ID,
        work_item_id=WORK_ITEM_ID,
        generation=2,
        admission_id=ADMISSION_ID,
        sealed_packet_manifest_sha256=SEALED_PACKET_MANIFEST_SHA256,
        output_namespace=output_namespace,
    )
    return bundle, receipt, base, material


def make_primary_only_receipt(receipt_path: Path) -> None:
    attempts_path = receipt_path.parent / "attempts.jsonl"
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    selected = dict(attempts[-1])
    selected["role"] = "primary"
    selected["primary_attempt_id"] = None
    selected["fallback_reason"] = None
    attempts_path.write_bytes(canonical_json_bytes(selected))
    for suffix in ("stdout.bin", "stderr.bin", "output.json"):
        source = receipt_path.parent / f"fallback-{suffix}"
        target = receipt_path.parent / f"primary-{suffix}"
        target.write_bytes(source.read_bytes())
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["attempt_count"] = 1
    receipt["attempts_sha256"] = file_sha256(attempts_path)
    receipt["selected_role"] = "primary"
    write_json(receipt_path, receipt)


class _Cursor:
    def __init__(
        self,
        *,
        receipt_id: str | None = None,
        proposal_id: str | None = None,
        fixed_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.receipt_id = receipt_id
        self.proposal_id = proposal_id
        self.fixed_rows = fixed_rows
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, parameters=None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, parameters))
        if self.fixed_rows is not None:
            self._rows = self.fixed_rows
        elif (
            "FROM nhi_rule_history_update_ops.bundle_receipt" in normalized
            and "OR (bundle_uid =" in normalized
        ):
            self._rows = (
                [] if self.receipt_id is None else [(self.receipt_id,)]
            )
        elif (
            "FROM nhi_rule_history_candidate_stage.candidate_proposal"
            in normalized
            and "OR proposal_fingerprint =" in normalized
        ):
            self._rows = (
                [] if self.proposal_id is None else [(self.proposal_id,)]
            )
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def prerequisite_row(material) -> tuple[object, ...]:
    expected_job = material.expected_job
    receipt = {
        "lease_id": material.lease_id,
        "owner_key": material.owner_key,
        "started_at": material.selected_started_at,
        "completed_at": material.selected_completed_at,
        "raw_worker_attempt_id": material.selected_raw_attempt_id,
        "attempt_namespace": material.output_namespace,
    }
    return (
        material.recovery_job_id,
        material.job_fingerprint,
        expected_job["feed_url"],
        expected_job["request_profile_sha256"],
        expected_job["notification_window_start"],
        expected_job["notification_window_end"],
        expected_job["activation_cut"],
        material.lease_id,
        material.owner_key,
        material.work_item_id,
        material.generation,
        material.admission_id,
        material.recovery_job_id,
        material.lease_id,
        material.owner_key,
        material.job_fingerprint,
        material.selected_prompt_sha256,
        material.sealed_packet_manifest_sha256,
        str(uuid.uuid4()),
        material.selected_role,
        material.recovery_job_id,
        material.sealed_packet_manifest_sha256,
        material.selected_prompt_sha256,
        material.output_namespace,
        material.selected_runtime,
        material.selected_provider,
        material.selected_model,
        "succeeded",
        None,
        material.producer_attempt_id,
        material.recovery_job_id,
        None,
        material.selected_output_sha256,
        receipt,
        material.producer_attempt_id,
        material.recovery_job_id,
        material.lease_id,
        material.owner_key,
        material.selected_role,
        material.selected_provider,
        material.selected_runtime,
        material.selected_model,
        material.selected_prompt_sha256,
        material.selected_output_sha256,
        material.selected_started_at,
        material.selected_completed_at,
        "success",
    )


class PartitionRecoveryPrepareTests(unittest.TestCase):
    def test_forces_needs_review_and_records_original_validator_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, receipt, base = prepare_fixture(Path(temporary))
            promoted_base = replace(
                base, final_state="promotion_ready_pending_anchor"
            )
            output_namespace = partition_recovery_output_namespace(
                work_item_id=WORK_ITEM_ID,
                generation=2,
                job_fingerprint=base.job_fingerprint,
            )
            with mock.patch(
                "nhi_rule_history.update.pg_stage._prepare_update_load",
                return_value=promoted_base,
            ):
                material = _prepare_partition_recovery_candidate(
                    bundle_path=bundle,
                    candidate_receipt_path=receipt,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start=(
                        "2026-07-27T00:00:00+00:00"
                    ),
                    notification_window_end=(
                        "2026-07-27T00:05:00+00:00"
                    ),
                    recovery_job_id=RECOVERY_JOB_ID,
                    lease_id=LEASE_ID,
                    producer_attempt_id=PRODUCER_ATTEMPT_ID,
                    dispatch_claim_id=CLAIM_ID,
                    work_item_id=WORK_ITEM_ID,
                    generation=2,
                    admission_id=ADMISSION_ID,
                    sealed_packet_manifest_sha256=(
                        SEALED_PACKET_MANIFEST_SHA256
                    ),
                    output_namespace=output_namespace,
                )
        self.assertEqual(
            material.original_candidate_state,
            "promotion_ready_pending_anchor",
        )
        self.assertEqual(material.final_state, "needs_review")
        self.assertEqual(
            [row["state"] for row in material.rows[
                "candidate_state_transition"
            ]],
            ["needs_review"],
        )
        proposal = material.rows["candidate_proposal"][0]
        self.assertEqual(proposal["job_id"], RECOVERY_JOB_ID)
        self.assertEqual(
            proposal["producer_attempt_id"], PRODUCER_ATTEMPT_ID
        )

    def test_rejects_wrong_generation_namespace_and_packet_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, receipt, base = prepare_fixture(Path(temporary))
            common = {
                "bundle_path": bundle,
                "candidate_receipt_path": receipt,
                "bundle_relative_path": "tw-gov/nhi/test-bundle",
                "activation_cut": "2026-07-27",
                "owner_key": "fixture-owner",
                "notification_window_start": (
                    "2026-07-27T00:00:00+00:00"
                ),
                "notification_window_end": (
                    "2026-07-27T00:05:00+00:00"
                ),
                "recovery_job_id": RECOVERY_JOB_ID,
                "lease_id": LEASE_ID,
                "producer_attempt_id": PRODUCER_ATTEMPT_ID,
                "dispatch_claim_id": CLAIM_ID,
                "work_item_id": WORK_ITEM_ID,
                "generation": 2,
                "admission_id": ADMISSION_ID,
                "sealed_packet_manifest_sha256": (
                    SEALED_PACKET_MANIFEST_SHA256
                ),
                "output_namespace": partition_recovery_output_namespace(
                    work_item_id=WORK_ITEM_ID,
                    generation=2,
                    job_fingerprint=base.job_fingerprint,
                ),
            }
            for field, value in (
                ("generation", 3),
                ("output_namespace", "partition-recovery/wrong"),
                ("sealed_packet_manifest_sha256", "not-a-digest"),
            ):
                arguments = {**common, field: value}
                with self.subTest(field=field), self.assertRaises(
                    UpdateStageLoadError
                ):
                    _prepare_partition_recovery_candidate(**arguments)


class PartitionRecoveryPrerequisiteTests(unittest.TestCase):
    def test_exact_durable_tuple_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            *_unused, material = prepare_recovery_material(
                Path(temporary)
            )
            cursor = _Cursor(fixed_rows=[prerequisite_row(material)])
            _verify_partition_recovery_prerequisites(cursor, material)

    def test_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            *_unused, material = prepare_recovery_material(
                Path(temporary)
            )
            original = prerequisite_row(material)
            cases: list[tuple[str, int, object]] = [
                ("job fingerprint", 1, "b1" * 32),
                ("lease owner", 8, "other-owner"),
                ("sealed packet", 21, "c1" * 32),
                ("route output", 32, "d1" * 32),
                ("worker output", 43, "e1" * 32),
                ("worker status", 46, "failed"),
            ]
            for label, index, replacement in cases:
                row = list(original)
                row[index] = replacement
                with self.subTest(label=label), self.assertRaises(
                    UpdateStageLoadError
                ):
                    _verify_partition_recovery_prerequisites(
                        _Cursor(fixed_rows=[tuple(row)]),
                        material,
                    )

            row = list(original)
            receipt = dict(row[33])
            receipt["raw_worker_attempt_id"] = "f1" * 32
            row[33] = receipt
            with self.assertRaises(UpdateStageLoadError):
                _verify_partition_recovery_prerequisites(
                    _Cursor(fixed_rows=[tuple(row)]),
                    material,
                )


class PartitionRecoveryApplyTests(unittest.TestCase):
    def _apply(
        self,
        material,
        *,
        receipt_id: str | None,
        proposal_id: str | None,
    ):
        cursor = _Cursor(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
        )
        connection = _Connection(cursor)
        with (
            mock.patch(
                "nhi_rule_history.update.pg_stage._connect",
                return_value=connection,
            ),
            mock.patch(
                "nhi_rule_history.update.pg_stage."
                "_verify_partition_recovery_prerequisites"
            ),
            mock.patch(
                "nhi_rule_history.update.pg_stage._insert_artifact"
            ),
            mock.patch(
                "nhi_rule_history.update.pg_stage."
                "_verify_partition_recovery_attach_identity"
            ),
            mock.patch(
                "nhi_rule_history.update.pg_stage."
                "_fetch_partition_recovery_attach_tokens",
                return_value=material.expected_tokens,
            ),
        ):
            replayed = _apply_partition_recovery_candidate(
                material, "postgresql://fixture.invalid/test"
            )
        return replayed, cursor

    def test_success_inserts_only_artifact_receipt_and_candidate_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            *_unused, material = prepare_recovery_material(
                Path(temporary)
            )
            replayed, cursor = self._apply(
                material, receipt_id=None, proposal_id=None
            )
        self.assertFalse(replayed)
        sql = "\n".join(command for command, _parameters in cursor.commands)
        self.assertIn(
            "INSERT INTO nhi_rule_history_update_ops.bundle_receipt", sql
        )
        self.assertIn(
            "INSERT INTO "
            "nhi_rule_history_candidate_stage.candidate_proposal",
            sql,
        )
        for forbidden in (
            "INSERT INTO nhi_rule_history_update_ops.update_job",
            "INSERT INTO nhi_rule_history_update_ops.job_lease",
            "INSERT INTO nhi_rule_history_update_ops.worker_attempt",
            "INSERT INTO nhi_rule_history_update_ops.url_observation",
            "INSERT INTO nhi_rule_history_update_ops.feed_observation",
            "INSERT INTO nhi_rule_history_update_ops.feed_item_observation",
        ):
            self.assertNotIn(forbidden, sql)

    def test_idempotent_replay_inserts_no_receipt_or_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            *_unused, material = prepare_recovery_material(
                Path(temporary)
            )
            replayed, cursor = self._apply(
                material,
                receipt_id=material.receipt_id,
                proposal_id=material.proposal_id,
            )
        self.assertTrue(replayed)
        sql = "\n".join(command for command, _parameters in cursor.commands)
        self.assertNotIn("INSERT INTO", sql)


class PartitionRecoveryPublicReceiptTests(unittest.TestCase):
    def test_public_receipt_returns_exact_recovery_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, receipt, _base, material = prepare_recovery_material(
                Path(temporary)
            )
            verification = {
                "counts": dict(material.expected_counts),
                "fingerprint": material.expected_fingerprint,
            }
            with (
                mock.patch(
                    "nhi_rule_history.update.pg_stage."
                    "_prepare_partition_recovery_candidate",
                    return_value=material,
                ),
                mock.patch(
                    "nhi_rule_history.update.pg_stage."
                    "_apply_partition_recovery_candidate",
                    return_value=False,
                ),
                mock.patch(
                    "nhi_rule_history.update.pg_stage."
                    "_verify_partition_recovery_loaded",
                    return_value=verification,
                ),
            ):
                result = load_partition_recovery_candidate(
                    "postgresql://fixture.invalid/test",
                    bundle,
                    receipt,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                    recovery_job_id=RECOVERY_JOB_ID,
                    lease_id=LEASE_ID,
                    producer_attempt_id=PRODUCER_ATTEMPT_ID,
                    dispatch_claim_id=CLAIM_ID,
                    work_item_id=WORK_ITEM_ID,
                    generation=2,
                    admission_id=ADMISSION_ID,
                    sealed_packet_manifest_sha256=(
                        SEALED_PACKET_MANIFEST_SHA256
                    ),
                    output_namespace=material.output_namespace,
                )
        self.assertEqual(result["job_id"], RECOVERY_JOB_ID)
        self.assertEqual(
            result["candidate_proposal_id"], material.proposal_id
        )
        self.assertEqual(result["candidate_state"], "needs_review")
        self.assertFalse(
            result["recovery_boundary"]["automatic_promotion_allowed"]
        )


class PartitionRecoveryPgStageLiveTests(unittest.TestCase):
    def test_fresh_pg_success_replay_and_zero_acquisition_duplication(
        self,
    ) -> None:
        pg = _base_cluster(fixture=True)
        try:
            pg.psql(file=PARTITION_RECOVERY_FORWARD)
            pg.psql(
                command=f"""
INSERT INTO nhi_rule_history_update_queue.work_item_transition (
  work_item_id, transition_seq, transition_id, from_state, to_state,
  actor_kind, evidence_sha256, evidence_json, source_job_id, recorded_at
) VALUES (
  '{PARTITION_WORK_ITEM_ID}', 6,
  '61000000-0000-0000-0000-000000000001',
  'proposal_running', 'partition_required', 'fixture',
  repeat('c',64), '{{"event":"partition-required"}}',
  '10000000-0000-0000-0000-000000000001',
  '2026-07-27 00:01:05+00'
);
"""
            )
            with tempfile.TemporaryDirectory() as temporary:
                bundle, receipt, _base = prepare_fixture(Path(temporary))
                make_primary_only_receipt(receipt)
                base = _prepare_partition_recovery_candidate(
                    bundle_path=bundle,
                    candidate_receipt_path=receipt,
                    bundle_relative_path="tw-gov/nhi/test-bundle",
                    activation_cut="2026-07-27",
                    owner_key="fixture-owner",
                    notification_window_start=(
                        "2026-07-27T00:00:00+00:00"
                    ),
                    notification_window_end=(
                        "2026-07-27T00:05:00+00:00"
                    ),
                    recovery_job_id=RECOVERY_JOB_ID,
                    lease_id=LEASE_ID,
                    producer_attempt_id=PRODUCER_ATTEMPT_ID,
                    dispatch_claim_id=CLAIM_ID,
                    work_item_id=PARTITION_WORK_ITEM_ID,
                    generation=2,
                    admission_id=ADMISSION_ID,
                    sealed_packet_manifest_sha256=(
                        SEALED_PACKET_MANIFEST_SHA256
                    ),
                    output_namespace=partition_recovery_output_namespace(
                        work_item_id=PARTITION_WORK_ITEM_ID,
                        generation=2,
                        job_fingerprint=_base.job_fingerprint,
                    ),
                )
                with psycopg.connect(pg.dsn) as connection:
                    chain = connection.execute(
                        """
SELECT terminal_transition_id, terminal_transition_sequence,
       terminal_state, terminal_evidence_sha256, transition_count,
       transition_rows, old_job_fingerprint
FROM nhi_rule_history_partition_recovery.
  generation_one_chain_receipt(%s::uuid)
""",
                        (PARTITION_WORK_ITEM_ID,),
                    ).fetchone()
                self.assertIsNotNone(chain)
                assert chain is not None
                evidence = api_contract.admission_payload()
                transitions = chain[5]
                generation_one = evidence["generation_1"]
                generation_one.update(
                    {
                        "work_item_id": PARTITION_WORK_ITEM_ID,
                        "terminal_transition_id": str(chain[0]),
                        "terminal_transition_sequence": chain[1],
                        "terminal_state": chain[2],
                        "terminal_evidence_sha256": chain[3],
                        "transition_count": chain[4],
                        "transitions": transitions,
                        "old_job_fingerprint": chain[6],
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
                source_bundle = evidence["source_evidence"]["source_bundle"]
                source_bundle["bundle_id"] = _base.bundle_id
                source_bundle["manifest_sha256"] = _base.manifest_sha256
                evidence["source_evidence"]["sealed_packet"][
                    "manifest_sha256"
                ] = SEALED_PACKET_MANIFEST_SHA256
                evidence["execution_delta"]["new_job_fingerprint"] = (
                    _base.job_fingerprint
                )
                evidence["worker_semantics"]["prompt_sha256"] = (
                    _base.selected_prompt_sha256
                )
                output_namespace = partition_recovery_output_namespace(
                    work_item_id=PARTITION_WORK_ITEM_ID,
                    generation=2,
                    job_fingerprint=_base.job_fingerprint,
                )
                evidence["output_namespace"] = {
                    "contract": (
                        PARTITION_RECOVERY_OUTPUT_NAMESPACE_CONTRACT
                    ),
                    "generation": 2,
                    "relative_path": output_namespace,
                }
                verify_partition_recovery_admission(pg.dsn, evidence=evidence)
                admitted = admit_partition_recovery(
                    pg.dsn,
                    evidence=evidence,
                    actor_kind="fixture-operator",
                    admitted_at="2026-07-27T00:01:05+00:00",
                )
                authorized = authorize_partition_recovery(
                    pg.dsn,
                    admission_id=admitted["admission_id"],
                    work_item_id=PARTITION_WORK_ITEM_ID,
                    generation=2,
                    admission_payload_sha256=admitted[
                        "admission_payload_sha256"
                    ],
                    expires_at="2026-07-28T00:00:00+00:00",
                    actor_kind="fixture-operator",
                    authorized_at="2026-07-27T00:01:05+00:00",
                )
                dispatch = consume_partition_recovery_dispatch(
                    pg.dsn,
                    work_item_id=PARTITION_WORK_ITEM_ID,
                    generation=2,
                    authorization_id=authorized["authorization_id"],
                    admission_id=admitted["admission_id"],
                    admission_payload_sha256=admitted[
                        "admission_payload_sha256"
                    ],
                    sealed_packet_manifest_sha256=admitted[
                        "sealed_packet_manifest_sha256"
                    ],
                    suitability_v2_receipt_sha256=admitted[
                        "suitability_v2_receipt_sha256"
                    ],
                    job_fingerprint=admitted["new_job_fingerprint"],
                    prompt_sha256=admitted["prompt_sha256"],
                    route_policy_sha256=admitted["route_policy_sha256"],
                    owner_key="fixture-owner",
                    max_runtime_seconds=300,
                    consumed_at="2026-07-27T00:01:06+00:00",
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
                    runtime_id=base.selected_runtime,
                    provider=base.selected_provider,
                    model=base.selected_model,
                    controller_commit_sha256="b1" * 32,
                    job_fingerprint=admitted["new_job_fingerprint"],
                    reserved_at="2026-07-27T00:01:30+00:00",
                )
                route_evidence = {
                    "lease_id": dispatch["lease_id"],
                    "owner_key": dispatch["owner_key"],
                    "started_at": base.selected_started_at,
                    "completed_at": base.selected_completed_at,
                    "raw_worker_attempt_id": (
                        base.selected_raw_attempt_id
                    ),
                    "attempt_namespace": output_namespace,
                }
                finish_partition_recovery_route(
                    pg.dsn,
                    reservation_id=reserved["reservation_id"],
                    dispatch_claim_id=dispatch["dispatch_claim_id"],
                    work_item_id=PARTITION_WORK_ITEM_ID,
                    generation=2,
                    authorization_id=authorized["authorization_id"],
                    admission_id=admitted["admission_id"],
                    route_ordinal=1,
                    attempt_namespace=output_namespace,
                    job_fingerprint=admitted["new_job_fingerprint"],
                    recovery_job_id=dispatch["recovery_job_id"],
                    lease_id=dispatch["lease_id"],
                    owner_key=dispatch["owner_key"],
                    status="succeeded",
                    worker_attempt_id=reserved[
                        "generation_bound_attempt_id"
                    ],
                    stdout_sha256=base.selected_output_sha256,
                    stderr_sha256=sha256_bytes(b""),
                    output_sha256=base.selected_output_sha256,
                    process_exit_code=0,
                    result_receipt_sha256=sha256_bytes(
                        canonical_json_bytes(route_evidence)
                    ),
                    evidence=route_evidence,
                    completed_at=base.selected_completed_at,
                )
                with psycopg.connect(pg.dsn) as connection:
                    before = connection.execute(
                        """
SELECT
  (SELECT count(*) FROM nhi_rule_history_update_ops.update_job),
  (SELECT count(*) FROM nhi_rule_history_update_ops.job_lease),
  (SELECT count(*) FROM nhi_rule_history_update_ops.worker_attempt),
  (SELECT count(*) FROM nhi_rule_history_update_ops.url_observation),
  (SELECT count(*) FROM nhi_rule_history_update_ops.feed_observation),
  (SELECT count(*) FROM
     nhi_rule_history_update_ops.feed_item_observation)
"""
                    ).fetchone()
                arguments = {
                    "recovery_job_id": dispatch["recovery_job_id"],
                    "lease_id": dispatch["lease_id"],
                    "producer_attempt_id": reserved[
                        "generation_bound_attempt_id"
                    ],
                    "dispatch_claim_id": dispatch["dispatch_claim_id"],
                    "work_item_id": PARTITION_WORK_ITEM_ID,
                    "generation": 2,
                    "admission_id": admitted["admission_id"],
                    "sealed_packet_manifest_sha256": admitted[
                        "sealed_packet_manifest_sha256"
                    ],
                    "output_namespace": output_namespace,
                }
                first = load_partition_recovery_candidate(
                    pg.dsn,
                    bundle,
                    receipt,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                    **arguments,
                )
                second = load_partition_recovery_candidate(
                    pg.dsn,
                    bundle,
                    receipt,
                    "tw-gov/nhi/test-bundle",
                    "2026-07-27",
                    "fixture-owner",
                    "2026-07-27T00:00:00+00:00",
                    "2026-07-27T00:05:00+00:00",
                    **arguments,
                )
                self.assertFalse(first["replayed"])
                self.assertTrue(second["replayed"])
                self.assertEqual(first["candidate_state"], "needs_review")
                with psycopg.connect(pg.dsn) as connection:
                    after = connection.execute(
                        """
SELECT
  (SELECT count(*) FROM nhi_rule_history_update_ops.update_job),
  (SELECT count(*) FROM nhi_rule_history_update_ops.job_lease),
  (SELECT count(*) FROM nhi_rule_history_update_ops.worker_attempt),
  (SELECT count(*) FROM nhi_rule_history_update_ops.url_observation),
  (SELECT count(*) FROM nhi_rule_history_update_ops.feed_observation),
  (SELECT count(*) FROM
     nhi_rule_history_update_ops.feed_item_observation)
"""
                    ).fetchone()
                    binding = connection.execute(
                        """
SELECT proposal.job_id::text,
       proposal.producer_attempt_id::text,
       state.state
FROM nhi_rule_history_candidate_stage.candidate_proposal proposal
JOIN nhi_rule_history_candidate_stage.current_candidate_state state
  ON state.proposal_id = proposal.proposal_id
WHERE proposal.proposal_id = %s::uuid
""",
                        (first["candidate_proposal_id"],),
                    ).fetchone()
                self.assertEqual(after, before)
                self.assertEqual(
                    binding,
                    (
                        dispatch["recovery_job_id"],
                        reserved["generation_bound_attempt_id"],
                        "needs_review",
                    ),
                )
        finally:
            pg.close()


if __name__ == "__main__":
    unittest.main()
