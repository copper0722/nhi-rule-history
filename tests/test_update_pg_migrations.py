from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
OPS_ROLLBACK = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.rollback.sql"
)
CANDIDATE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.sql"
)
CANDIDATE_ROLLBACK = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.rollback.sql"
)


def sql_code(path: Path) -> str:
    return re.sub(r"--.*?$", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE)


class UpdateMigrationStaticTests(unittest.TestCase):
    def test_migrations_are_transactional_managed_and_public_closed(self) -> None:
        for path, marker in (
            (OPS_FORWARD, "managed=nhi_rule_history_update_ops/v1"),
            (
                CANDIDATE_FORWARD,
                "managed=nhi_rule_history_candidate_stage/v1",
            ),
        ):
            sql = path.read_text(encoding="utf-8")
            self.assertRegex(sql, r"(?m)^BEGIN;$")
            self.assertRegex(sql, r"(?m)^COMMIT;$")
            self.assertIn(marker, sql)
            self.assertRegex(sql, r"REVOKE ALL ON SCHEMA .* FROM PUBLIC;")
            self.assertIn("ALTER DEFAULT PRIVILEGES IN SCHEMA", sql)

    def test_rollbacks_are_guarded_and_never_cascade(self) -> None:
        for path, marker in (
            (OPS_ROLLBACK, "managed=nhi_rule_history_update_ops/v1"),
            (
                CANDIDATE_ROLLBACK,
                "managed=nhi_rule_history_candidate_stage/v1",
            ),
        ):
            sql = path.read_text(encoding="utf-8")
            self.assertIn(marker, sql)
            self.assertNotRegex(sql_code(path), r"(?i)\bCASCADE\b")
            self.assertRegex(sql, r"DROP SCHEMA IF EXISTS [a-z_]+ RESTRICT;")
            self.assertRegex(sql, r"(?m)^BEGIN;$")
            self.assertRegex(sql, r"(?m)^COMMIT;$")

    def test_operational_contract_is_exact_and_append_only(self) -> None:
        sql = OPS_FORWARD.read_text(encoding="utf-8")
        for table in (
            "update_job",
            "job_lease",
            "worker_attempt",
            "content_artifact",
            "url_observation",
            "feed_observation",
            "feed_item_observation",
            "bundle_receipt",
        ):
            self.assertIn(
                f"CREATE TABLE nhi_rule_history_update_ops.{table}", sql
            )
        self.assertIn("job_fingerprint", sql)
        self.assertIn("NOT NULL UNIQUE", sql)
        self.assertIn("same_url_new_bytes", sql)
        self.assertIn("response_artifact_sha256", sql)
        self.assertIn("raw_item_sha256", sql)
        self.assertIn("fsync_verified", sql)
        self.assertIn("atomically_published_at", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("BEFORE TRUNCATE", sql)

    def test_model_attempts_are_bounded_and_failure_linked(self) -> None:
        sql = OPS_FORWARD.read_text(encoding="utf-8")
        self.assertIn("worker_attempt_one_primary_per_job_uidx", sql)
        self.assertIn("worker_attempt_one_fallback_per_job_uidx", sql)
        self.assertIn("WHERE lane = 'primary'", sql)
        self.assertIn("WHERE lane = 'fallback'", sql)
        self.assertIn("primary_attempt_id", sql)
        self.assertIn("primary_status IS DISTINCT FROM 'failed'", sql)
        self.assertIn("fallback_reason", sql)
        self.assertIn("attempt_no = 1", sql)
        self.assertIn("attempt_no = 2", sql)

    def test_leases_have_unique_ownership_overlap_and_runtime_gates(self) -> None:
        sql = OPS_FORWARD.read_text(encoding="utf-8")
        self.assertIn("owner_key", sql)
        self.assertIn("max_runtime_seconds BETWEEN 1 AND 21600", sql)
        self.assertIn("tstzrange(", sql)
        self.assertIn("overlapping leases", sql)
        self.assertIn("outside its owned lease", sql)

    def test_candidate_columns_and_json_cannot_encode_mutations(self) -> None:
        sql = CANDIDATE_FORWARD.read_text(encoding="utf-8")
        proposal_ddl = sql.split(
            "CREATE TABLE nhi_rule_history_candidate_stage.candidate_proposal",
            1,
        )[1].split(
            "CREATE TABLE nhi_rule_history_candidate_stage.candidate_source_span",
            1,
        )[0]
        forbidden_columns = (
            "rule_id",
            "stable_rule_id",
            "canonical_slug",
            "predecessor_id",
            "old_snapshot_id",
            "new_snapshot_id",
            "close_snapshot_id",
            "effective_until",
            "effective_until_exclusive",
            "effective_to",
            "head_generation",
            "proposed_operation",
            "proposed_operations",
            "executable_operation",
            "executable_operations",
        )
        for column in forbidden_columns:
            self.assertNotRegex(
                proposal_ddl,
                rf"(?m)^\s*{re.escape(column)}\s+",
            )
            self.assertIn(f"'{column}'", sql)
        self.assertIn("document_has_forbidden_candidate_key", sql)

    def test_candidate_requires_source_spans_and_evidence(self) -> None:
        sql = CANDIDATE_FORWARD.read_text(encoding="utf-8")
        self.assertIn(
            "nhi_rule_history_candidate_stage.candidate_source_span", sql
        )
        self.assertIn(
            "nhi_rule_history_candidate_stage.candidate_evidence", sql
        )
        for field in (
            "artifact_sha256",
            "locator",
            "char_start",
            "char_end",
            "raw_text",
            "raw_text_sha256",
            "evidence_code",
            "outcome",
            "assertion_text",
        ):
            self.assertIn(field, sql)
        self.assertIn(
            "at least one exact source span and evidence row", sql
        )

    def test_candidate_state_machine_stops_before_history_write(self) -> None:
        sql = CANDIDATE_FORWARD.read_text(encoding="utf-8")
        for state in (
            "validated_candidate",
            "promotion_ready_pending_anchor",
            "needs_review",
            "rejected",
        ):
            self.assertIn(f"'{state}'", sql)
        self.assertNotIn("canonical_applied", sql)
        self.assertIn("states are terminal", sql)
        self.assertIn("full_single_clause", sql)
        self.assertIn("omitted_text_present", sql)
        self.assertIn("cross_row_dependency", sql)

    def test_transition_guard_uses_advisory_lock_without_update_privilege(self) -> None:
        sql = CANDIDATE_FORWARD.read_text(encoding="utf-8")
        guard = sql.split(
            "CREATE FUNCTION\n"
            "  nhi_rule_history_candidate_stage.guard_state_transition_insert()",
            1,
        )[1].split("CREATE TRIGGER candidate_proposal_insert_guard", 1)[0]
        self.assertIn("pg_advisory_xact_lock", guard)
        self.assertNotIn("FOR UPDATE", guard)

    def test_runtime_roles_are_nologin_and_stage_scoped(self) -> None:
        combined = (
            OPS_FORWARD.read_text(encoding="utf-8")
            + CANDIDATE_FORWARD.read_text(encoding="utf-8")
        )
        self.assertEqual(
            len(
                re.findall(
                    r"CREATE ROLE nhi_rule_history_[a-z_]+"
                    r"\s+NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE",
                    combined,
                )
            ),
            2,
        )
        grant_lines = "\n".join(
            line.strip()
            for line in combined.splitlines()
            if line.lstrip().startswith("GRANT ")
        )
        self.assertNotRegex(grant_lines, r"\btw_drug\b")
        self.assertNotRegex(grant_lines, r"\bnhi_rule_history\.")
        self.assertNotIn("CREATE ON SCHEMA", grant_lines)


class UpdateMigrationLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ.get("NHI_RULE_HISTORY_TEST_DSN")
        if not cls.dsn:
            raise unittest.SkipTest("NHI_RULE_HISTORY_TEST_DSN is not set")
        cls.psql = shutil.which("psql")
        if not cls.psql:
            raise unittest.SkipTest("psql is unavailable")

    @classmethod
    def run_psql(
        cls, *, file: Path | None = None, command: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            cls.psql,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--dbname",
            cls.dsn,
        ]
        if file is not None:
            argv.extend(["--file", str(file)])
        if command is not None:
            argv.extend(["--command", command])
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"psql failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    def test_forward_contract_privileges_and_rollbacks(self) -> None:
        presence = self.run_psql(
            command=(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN "
                "('nhi_rule_history_update_ops',"
                "'nhi_rule_history_candidate_stage');"
            )
        ).stdout.strip()
        self.assertEqual(presence, "0", "test DSN must be an unused scratch database")

        applied_ops = False
        applied_candidate = False
        try:
            self.run_psql(file=OPS_FORWARD)
            applied_ops = True
            self.run_psql(file=CANDIDATE_FORWARD)
            applied_candidate = True

            self.run_psql(
                command=r"""
DO $test$
DECLARE
  forbidden_grants integer;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname IN (
      'nhi_rule_history_update_runtime',
      'nhi_rule_history_candidate_runtime'
    )
      AND (
        rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
        OR rolbypassrls
      )
  ) THEN
    RAISE EXCEPTION 'runtime role is not least privilege';
  END IF;

  SELECT count(*) INTO forbidden_grants
  FROM information_schema.role_table_grants
  WHERE grantee IN (
    'nhi_rule_history_update_runtime',
    'nhi_rule_history_candidate_runtime'
  )
    AND table_schema NOT IN (
      'nhi_rule_history_update_ops',
      'nhi_rule_history_candidate_stage'
    );
  IF forbidden_grants <> 0 THEN
    RAISE EXCEPTION 'runtime roles received out-of-stage table grants';
  END IF;
END;
$test$;

INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES (
  '00000000-0000-0000-0000-000000000001',
  repeat('1', 64), 'test/v1', 'test-runner',
  'https://example.invalid/feed.xml', repeat('2', 64),
  '2026-07-27 00:00:00+00', '2026-07-27 01:00:00+00',
  '2026-07-27', '2026-07-27 01:00:00+00'
);

INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at,
  max_runtime_seconds
) VALUES (
  '00000000-0000-0000-0000-000000000002',
  '00000000-0000-0000-0000-000000000001',
  'test-owner', '2026-07-27 01:00:00+00',
  '2026-07-27 01:05:00+00', 300
);

INSERT INTO nhi_rule_history_update_ops.worker_attempt (
  attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
  provider, runtime, model, prompt_sha256, output_sha256,
  started_at, completed_at, status
) VALUES (
  '00000000-0000-0000-0000-000000000003',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002',
  'test-owner', 1, 'primary', 'test', 'test', 'test',
  repeat('3', 64), repeat('4', 64),
  '2026-07-27 01:00:00+00', '2026-07-27 01:01:00+00', 'success'
);

INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES (
  repeat('7', 64), 4, 'application/vnd.oasis.opendocument.text',
  'raw/77/source.odt', '2026-07-27 01:01:00+00'
);

INSERT INTO nhi_rule_history_update_ops.bundle_receipt (
  receipt_id, job_id, bundle_uid, manifest_sha256, bundle_relative_path,
  artifact_count, total_bytes, prepared_at, atomically_published_at,
  pg_received_at, fsync_verified, receipt_status
) VALUES (
  '00000000-0000-0000-0000-000000000005',
  '00000000-0000-0000-0000-000000000001',
  'test-bundle', repeat('7', 64), 'tw-gov/test-bundle',
  1, 4, '2026-07-27 01:01:00+00', '2026-07-27 01:01:10+00',
  '2026-07-27 01:01:20+00', true, 'received'
);

INSERT INTO nhi_rule_history_candidate_stage.candidate_proposal (
  proposal_id, proposal_fingerprint, contract_version, job_id,
  bundle_receipt_id, producer_attempt_id, producer_output_sha256,
  source_designation_text, raw_effective_expression, calendar_system,
  effective_from, date_precision, date_role, date_scope, conditionality,
  replacement_scope, omitted_text_present, merged_cells_present,
  cross_row_dependency, multiple_designations_present, odt_pdf_agreement,
  identity_resolution, confidence
) VALUES (
  '00000000-0000-0000-0000-000000000006', repeat('8', 64),
  'test/v1', '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000005',
  '00000000-0000-0000-0000-000000000003', repeat('4', 64),
  '9.9.9', '自115年8月1日生效', 'roc', '2026-08-01', 'day',
  'effective_date', 'single_clause', 'unconditional',
  'full_single_clause', false, false, false, false, 'agree',
  'source_designation_only', 0.9000
);

INSERT INTO nhi_rule_history_candidate_stage.candidate_source_span (
  proposal_id, span_id, artifact_sha256, source_role, locator,
  locator_key, char_start, char_end, raw_text, raw_text_sha256,
  raw_text_char_length, observed_at, statement
) VALUES (
  '00000000-0000-0000-0000-000000000006', repeat('9', 64),
  repeat('7', 64), 'comparison_new', '{"table":1,"row":2,"cell":1}',
  'table:1/row:2/cell:1', 0, 4, 'rule', repeat('a', 64), 4,
  '2026-07-27 01:01:00+00',
  'Source-grounded candidate evidence only; no legal-history identity, adjacency, interval closure, or executable mutation authority.'
);

INSERT INTO nhi_rule_history_candidate_stage.candidate_evidence (
  proposal_id, evidence_id, span_id, evidence_code, outcome,
  assertion_text, evidence_details, validator_version, recorded_at
) VALUES (
  '00000000-0000-0000-0000-000000000006', repeat('b', 64),
  repeat('9', 64), 'full_single_clause_replacement', 'pass',
  'Synthetic source shows a full single-clause comparison.',
  '{"fixture":"synthetic"}', 'test-validator',
  '2026-07-27 01:02:00+00'
);

INSERT INTO nhi_rule_history_candidate_stage.candidate_state_transition (
  proposal_id, transition_seq, transition_id, state, actor_kind,
  decision_basis_sha256, recorded_at
) VALUES (
  '00000000-0000-0000-0000-000000000006', 1,
  '00000000-0000-0000-0000-000000000007',
  'validated_candidate', 'deterministic_validator', repeat('c', 64),
  '2026-07-27 01:03:00+00'
);

INSERT INTO nhi_rule_history_candidate_stage.candidate_state_transition (
  proposal_id, transition_seq, transition_id, state, actor_kind,
  decision_basis_sha256, recorded_at
) VALUES (
  '00000000-0000-0000-0000-000000000006', 2,
  '00000000-0000-0000-0000-000000000008',
  'promotion_ready_pending_anchor', 'system_gate', repeat('d', 64),
  '2026-07-27 01:04:00+00'
);

DO $test$
BEGIN
  BEGIN
    INSERT INTO nhi_rule_history_update_ops.worker_attempt (
      attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
      primary_attempt_id, provider, runtime, model, prompt_sha256,
      output_sha256, started_at, completed_at, status, fallback_reason
    ) VALUES (
      '00000000-0000-0000-0000-000000000004',
      '00000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000002',
      'test-owner', 2, 'fallback',
      '00000000-0000-0000-0000-000000000003',
      'test', 'test', 'test', repeat('5', 64), repeat('6', 64),
      '2026-07-27 01:01:00+00', '2026-07-27 01:02:00+00',
      'success', 'primary failed'
    );
    RAISE EXCEPTION 'fallback unexpectedly accepted a successful primary';
  EXCEPTION
    WHEN object_not_in_prerequisite_state THEN NULL;
  END;

  BEGIN
    INSERT INTO nhi_rule_history_candidate_stage.candidate_state_transition (
      proposal_id, transition_seq, transition_id, state, actor_kind,
      decision_basis_sha256, recorded_at
    ) VALUES (
      '00000000-0000-0000-0000-000000000006', 3,
      '00000000-0000-0000-0000-000000000009',
      'canonical_applied', 'system_gate', repeat('e', 64),
      '2026-07-27 01:05:00+00'
    );
    RAISE EXCEPTION 'candidate state escaped the staging state machine';
  EXCEPTION
    WHEN check_violation OR object_not_in_prerequisite_state THEN NULL;
  END;

  BEGIN
    INSERT INTO nhi_rule_history_candidate_stage.candidate_evidence (
      proposal_id, evidence_id, span_id, evidence_code, outcome,
      assertion_text, evidence_details, validator_version, recorded_at
    ) VALUES (
      '00000000-0000-0000-0000-000000000006', repeat('f', 64),
      repeat('9', 64), 'forbidden_payload_probe', 'fail',
      'Synthetic forbidden-key probe.',
      '{"proposed_operations":[{"action":"write"}]}',
      'test-validator', '2026-07-27 01:05:00+00'
    );
    RAISE EXCEPTION 'candidate evidence accepted executable operations';
  EXCEPTION
    WHEN check_violation THEN NULL;
  END;

  BEGIN
    UPDATE nhi_rule_history_update_ops.update_job
    SET runner_version = 'mutated'
    WHERE job_id = '00000000-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'append-only update unexpectedly succeeded';
  EXCEPTION
    WHEN object_not_in_prerequisite_state THEN NULL;
  END;
END;
$test$;
"""
            )
        finally:
            if applied_candidate:
                self.run_psql(file=CANDIDATE_ROLLBACK)
            if applied_ops:
                self.run_psql(file=OPS_ROLLBACK)

        absence = self.run_psql(
            command=(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN "
                "('nhi_rule_history_update_ops',"
                "'nhi_rule_history_candidate_stage');"
            )
        ).stdout.strip()
        self.assertEqual(absence, "0")


if __name__ == "__main__":
    unittest.main()
