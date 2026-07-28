from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
OPS_FORWARD = MIGRATIONS / "2026-07-27_nhi_rule_history_update_ops.sql"
CANDIDATE_FORWARD = (
    MIGRATIONS / "2026-07-27_nhi_rule_history_candidate_stage.sql"
)
CANONICAL_FORWARD = (
    MIGRATIONS / "2026-07-28_nhi_rule_history_canonical_v1.sql"
)
CANONICAL_ROLLBACK = (
    MIGRATIONS / "2026-07-28_nhi_rule_history_canonical_v1.rollback.sql"
)
PROMOTION_FORWARD = (
    MIGRATIONS / "2026-07-28_nhi_rule_history_promotion_v1.sql"
)
PROMOTION_ROLLBACK = (
    MIGRATIONS / "2026-07-28_nhi_rule_history_promotion_v1.rollback.sql"
)

PRODUCER = "fixture_promotion_producer"
REVIEWER = "fixture_promotion_reviewer"
EXECUTOR = "fixture_promotion_executor"
INTRUDER = "fixture_promotion_intruder"
DETECTOR_PRODUCER = "fixture_format_detector"
DETECTOR_REVIEWER = "fixture_format_reviewer"
DETECTOR_WRITER_ROLE = "nhi_rule_history_format_detector_writer"
DETECTOR_REVIEWER_ROLE = "nhi_rule_history_format_detector_reviewer"
WRITER_ROLE = "nhi_rule_history_promotion_writer"
REVIEWER_ROLE = "nhi_rule_history_promotion_reviewer"
EXECUTOR_ROLE = "nhi_rule_history_promotion_executor"


def sql_code(path: Path) -> str:
    return re.sub(
        r"--.*?$",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pg_jsonb_array_fingerprint(rows: list[list[object]]) -> str:
    rendered = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return sha256(rendered)


def pg_jsonb_value_fingerprint(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return sha256(rendered)


DETECTOR_NAME = "nhi-byte-media-detector"
DETECTOR_VERSION = "nhi-byte-media-detector/v1"


def deterministic_odf_bytes(
    media_type: str,
    token: str,
    *,
    compress_payloads: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    payload_compression = (
        zipfile.ZIP_DEFLATED
        if compress_payloads
        else zipfile.ZIP_STORED
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.date_time = (1980, 1, 1, 0, 0, 0)
        mimetype_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype_info, media_type.encode("ascii"))
        content_info = zipfile.ZipInfo("content.xml")
        content_info.date_time = (1980, 1, 1, 0, 0, 0)
        content_info.compress_type = payload_compression
        archive.writestr(
            content_info,
            f"<fixture token={json.dumps(token)}/>".encode("utf-8"),
        )
        manifest_info = zipfile.ZipInfo("META-INF/manifest.xml")
        manifest_info.date_time = (1980, 1, 1, 0, 0, 0)
        manifest_info.compress_type = payload_compression
        archive.writestr(
            manifest_info,
            (
                '<manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:'
                'xmlns:manifest:1.0"/>'
            ).encode("utf-8"),
        )
    return buffer.getvalue()


def deterministic_zip_bytes(
    entries: list[tuple[str, bytes, int]],
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content, compression in entries:
            entry_info = zipfile.ZipInfo(filename)
            entry_info.date_time = (1980, 1, 1, 0, 0, 0)
            entry_info.compress_type = compression
            archive.writestr(entry_info, content)
    return buffer.getvalue()


def corrupt_zip_member_crc(raw_bytes: bytes, member_name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as archive:
        member = archive.getinfo(member_name)
    corrupted = bytearray(raw_bytes)
    wrong_crc = (member.CRC ^ 1).to_bytes(4, "little")
    corrupted[member.header_offset + 14 : member.header_offset + 18] = (
        wrong_crc
    )
    cursor = 0
    while True:
        central_offset = raw_bytes.find(b"PK\x01\x02", cursor)
        if central_offset < 0:
            raise AssertionError(f"missing central entry for {member_name}")
        filename_length = int.from_bytes(
            raw_bytes[central_offset + 28 : central_offset + 30],
            "little",
        )
        extra_length = int.from_bytes(
            raw_bytes[central_offset + 30 : central_offset + 32],
            "little",
        )
        comment_length = int.from_bytes(
            raw_bytes[central_offset + 32 : central_offset + 34],
            "little",
        )
        filename_start = central_offset + 46
        filename_end = filename_start + filename_length
        if (
            raw_bytes[filename_start:filename_end].decode("utf-8")
            == member_name
        ):
            corrupted[central_offset + 16 : central_offset + 20] = wrong_crc
            return bytes(corrupted)
        cursor = filename_end + extra_length + comment_length


def deterministic_pdf_bytes(token: str) -> bytes:
    output = bytearray(b"%PDF-1.7\n")
    offsets = []
    for object_body in (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
    ):
        offsets.append(len(output))
        object_number = len(offsets)
        output.extend(f"{object_number} 0 obj\n".encode("ascii"))
        output.extend(object_body)
        output.extend(b"\nendobj\n")
    output.extend(f"% fixture {token}\n".encode("ascii"))
    xref_offset = len(output)
    output.extend(b"xref\n0 3\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            "trailer\n<< /Size 3 /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


class CanonicalPromotionStaticTests(unittest.TestCase):
    def test_canonical_schema_has_exact_phase_one_tables(self) -> None:
        sql = CANONICAL_FORWARD.read_text(encoding="utf-8")
        self.assertIn("managed=nhi_rule_history_canonical/v1", sql)
        for table in (
            "dataset_release",
            "source_artifact",
            "artifact_format_detection",
            "artifact_format_detection_review",
            "release_artifact",
            "rule_identity",
            "rule_designation",
            "rule_snapshot",
            "rule_head",
            "official_event",
            "official_event_effect",
            "snapshot_evidence",
            "comparison_edge",
            "promotion_receipt",
        ):
            self.assertRegex(
                sql,
                rf"CREATE TABLE IF NOT EXISTS nhi_rule_history\.{table}\b",
            )
        self.assertIn("is_cumulative_anchor", sql)
        self.assertIn("declared_rule_count", sql)
        self.assertIn("rule_set_fingerprint", sql)
        self.assertIn("official_attachment_inventory_status", sql)
        self.assertIn("official_attachment_inventory_fingerprint", sql)
        self.assertIn("nhi-byte-media-detector/v1", sql)
        self.assertIn("detection_receipt_sha256", sql)
        self.assertIn("register_artifact_format_detection", sql)
        self.assertIn("attest_artifact_format_detection", sql)
        self.assertIn("inspect_odf_container_detector", sql)
        self.assertIn("inspect_odf_container_reviewer", sql)
        self.assertIn("odf-zip-container/v1", sql)
        self.assertIn("effective_until_exclusive", sql)
        self.assertIn("rule_snapshot_one_open_uidx", sql)
        self.assertIn("head_generation", sql)
        self.assertNotIn("'publishable'", sql)

    def test_promotion_schema_has_exact_evidence_tables(self) -> None:
        sql = PROMOTION_FORWARD.read_text(encoding="utf-8")
        self.assertIn("managed=nhi_rule_history_promotion/v1", sql)
        for table in (
            "promotion_case",
            "effect_resolution",
            "effect_resolution_span",
            "anchor_snapshot",
            "anchor_clause",
            "replay_run",
            "replay_rule_result",
            "replay_event",
            "format_parity_receipt",
            "promotion_transition",
        ):
            self.assertRegex(
                sql,
                rf"nhi_rule_history_promotion\.{table}\b",
            )
        self.assertIn("comparison_mapping_coverage = 1", sql)
        self.assertIn("source_declared_odt_only", sql)
        self.assertIn("pdf_verified", sql)
        self.assertIn("candidate_span_id", sql)
        self.assertIn("accepted_event_stream_sha256", sql)
        self.assertIn("authoritative_event_order", sql)
        self.assertIn("authoritative_order", sql)
        self.assertIn("format_declaration_raw_text", sql)
        self.assertNotIn("'publishable'", sql)

    def test_promote_function_is_single_atomic_security_boundary(self) -> None:
        sql = PROMOTION_FORWARD.read_text(encoding="utf-8")
        function = sql.split(
            "CREATE OR REPLACE FUNCTION nhi_rule_history_promotion.promote_case(",
            1,
        )[1].split(
            "ALTER SCHEMA nhi_rule_history_promotion",
            1,
        )[0]
        for required in (
            "case_id uuid",
            "expected_case_fingerprint text",
            "expected_head_generation bigint",
            "SECURITY DEFINER",
            "pg_advisory_xact_lock",
            "AT TIME ZONE 'Asia/Taipei'",
            "FOR UPDATE",
            "effective_until_exclusive = case_row.effective_from",
            "effective_until_exclusive IS NULL",
            "head_generation = $3",
            "promotion_ready_pending_anchor",
            "full_single_clause",
            "candidate_span.span_id = span_row.candidate_span_id",
            "pre_anchor.declared_rule_count",
            "post_anchor.declared_rule_count",
            "ORDER BY\n          rule_id,\n          authoritative_order",
            "whole-anchor endpoint parity",
            "blocked_pending_external_archive_integrity_verifier",
            "blocked_pending_external_pdf_integrity_verifier",
            "promotion_receipt",
        ):
            self.assertIn(required, function)
        self.assertNotIn("CURRENT_DATE", function)
        self.assertNotIn(
            "ORDER BY\n          effective_from,\n          event_id",
            function,
        )
        self.assertRegex(
            function,
            r"RETURN QUERY SELECT\s+receipt_row\.receipt_id,\s+true,",
        )
        first_canonical_insert = function.index(
            "INSERT INTO nhi_rule_history.official_event ("
        )
        self.assertLess(
            function.index(
                "blocked_pending_external_archive_integrity_verifier"
            ),
            first_canonical_insert,
        )
        self.assertLess(
            function.index(
                "blocked_pending_external_pdf_integrity_verifier"
            ),
            first_canonical_insert,
        )

    def test_roles_are_split_and_grants_do_not_conflate_duties(self) -> None:
        combined = (
            CANONICAL_FORWARD.read_text(encoding="utf-8")
            + PROMOTION_FORWARD.read_text(encoding="utf-8")
        )
        for role in (
            "nhi_rule_history_owner",
            "nhi_rule_history_reader",
            DETECTOR_WRITER_ROLE,
            DETECTOR_REVIEWER_ROLE,
            WRITER_ROLE,
            REVIEWER_ROLE,
            EXECUTOR_ROLE,
        ):
            self.assertIn(role, combined)
        self.assertIn(
            "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
            combined,
        )
        self.assertIn("stage_roles_zero_canonical_dml", combined)
        self.assertIn(
            "stage_roles_zero_promotion_or_canonical_dml",
            combined,
        )
        promotion = PROMOTION_FORWARD.read_text(encoding="utf-8")
        writer_grant = re.search(
            r"GRANT INSERT ON\s+"
            r"nhi_rule_history_promotion\.promotion_case,"
            r".*?TO nhi_rule_history_promotion_writer;",
            promotion,
            re.DOTALL,
        )
        reviewer_grant = re.search(
            r"GRANT INSERT ON\s+"
            r"nhi_rule_history_promotion\.promotion_transition"
            r"\s+TO nhi_rule_history_promotion_reviewer;",
            promotion,
            re.DOTALL,
        )
        executor_grant = re.search(
            r"GRANT EXECUTE ON FUNCTION\s+"
            r"nhi_rule_history_promotion\.promote_case"
            r"\(uuid, text, bigint\)\s+"
            r"TO nhi_rule_history_promotion_executor;",
            promotion,
            re.DOTALL,
        )
        self.assertIsNotNone(writer_grant)
        self.assertIsNotNone(reviewer_grant)
        self.assertIsNotNone(executor_grant)
        self.assertNotIn(REVIEWER_ROLE, writer_grant.group(0))
        self.assertNotIn(EXECUTOR_ROLE, writer_grant.group(0))

    def test_reapply_is_sealed_by_structural_fingerprint(self) -> None:
        for path in (CANONICAL_FORWARD, PROMOTION_FORWARD):
            sql = path.read_text(encoding="utf-8")
            self.assertIn("contract_sha256=", sql)
            self.assertIn("structural contract drift", sql)
            self.assertIn("pg_get_functiondef", sql)
            self.assertIn("pg_get_constraintdef", sql)
            self.assertIn("relrowsecurity", sql)
            self.assertIn("relforcerowsecurity", sql)
            self.assertIn("relreplident", sql)
            self.assertIn("relpersistence", sql)
            self.assertIn("relam", sql)
            self.assertIn("reltablespace", sql)
            self.assertIn("reloptions", sql)
            self.assertIn("attidentity", sql)
            self.assertIn("attgenerated", sql)
            self.assertIn("convalidated", sql)
            self.assertIn("tgenabled", sql)
            self.assertIn("pg_policy", sql)

    def test_rollbacks_are_empty_only_and_never_broad(self) -> None:
        for path in (CANONICAL_ROLLBACK, PROMOTION_ROLLBACK):
            sql = path.read_text(encoding="utf-8")
            self.assertRegex(sql, r"(?m)^BEGIN;$")
            self.assertRegex(sql, r"(?m)^COMMIT;$")
            self.assertIn("nonempty", sql)
            self.assertIn("IN ACCESS EXCLUSIVE MODE", sql)
            self.assertNotRegex(sql_code(path), r"(?i)\bCASCADE\b")
            self.assertRegex(
                sql,
                r"DROP SCHEMA IF EXISTS [a-z_]+ RESTRICT;",
            )


class DisposablePostgres:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nhi-promotion-pg-"
        )
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.socket_dir = self.root / "socket"
        self.socket_dir.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]

        self.initdb = shutil.which("initdb")
        self.pg_ctl = shutil.which("pg_ctl")
        self.createdb = shutil.which("createdb")
        self.dropdb = shutil.which("dropdb")
        self.psql_bin = shutil.which("psql")
        if not all(
            (
                self.initdb,
                self.pg_ctl,
                self.createdb,
                self.dropdb,
                self.psql_bin,
            )
        ):
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "disposable PostgreSQL command-line tools are unavailable"
            )

        init = subprocess.run(
            [
                self.initdb,
                "-D",
                str(self.data),
                "--auth=trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if init.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot initialize disposable PostgreSQL: " + init.stderr
            )

        start = subprocess.run(
            [
                self.pg_ctl,
                "-D",
                str(self.data),
                "-l",
                str(self.root / "postgres.log"),
                "-o",
                f"-F -k {self.socket_dir} -p {self.port}",
                "-w",
                "start",
            ],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if start.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot start disposable PostgreSQL: " + start.stderr
            )
        self.running = True

    def close(self) -> None:
        if getattr(self, "running", False):
            subprocess.run(
                [
                    self.pg_ctl,
                    "-D",
                    str(self.data),
                    "-m",
                    "fast",
                    "-w",
                    "stop",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.running = False
        self.temporary.cleanup()

    def database_args(
        self,
        database: str,
        *,
        user: str | None = None,
    ) -> list[str]:
        args = [
            "-h",
            str(self.socket_dir),
            "-p",
            str(self.port),
            "-d",
            database,
        ]
        if user is not None:
            args.extend(["-U", user])
        return args

    def create_database(self, database: str) -> None:
        result = subprocess.run(
            [
                self.createdb,
                "-h",
                str(self.socket_dir),
                "-p",
                str(self.port),
                database,
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)

    def drop_database(self, database: str) -> None:
        result = subprocess.run(
            [
                self.dropdb,
                "-h",
                str(self.socket_dir),
                "-p",
                str(self.port),
                "--force",
                "--if-exists",
                database,
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)

    def psql(
        self,
        database: str,
        *,
        command: str | None = None,
        file: Path | None = None,
        user: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            self.psql_bin,
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            *self.database_args(database, user=user),
        ]
        if command is not None:
            argv.extend(["--command", command])
        if file is not None:
            argv.extend(["--file", str(file)])
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"psql failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    def popen_psql(
        self,
        database: str,
        command: str,
        *,
        user: str | None = None,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                self.psql_bin,
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                *self.database_args(database, user=user),
                "--command",
                command,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


class CanonicalPromotionLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = DisposablePostgres()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def setUp(self) -> None:
        self.database = "test_" + uuid.uuid4().hex
        self.pg.create_database(self.database)
        for migration in (
            OPS_FORWARD,
            CANDIDATE_FORWARD,
            CANONICAL_FORWARD,
            PROMOTION_FORWARD,
        ):
            self.pg.psql(self.database, file=migration)
        self.ensure_actor_roles()

    def tearDown(self) -> None:
        self.pg.drop_database(self.database)

    def ensure_actor_roles(self) -> None:
        self.pg.psql(
            self.database,
            command=f"""
DO $actors$
DECLARE
  actor_name text;
BEGIN
  FOREACH actor_name IN ARRAY ARRAY[
    {quote(PRODUCER)}, {quote(REVIEWER)},
    {quote(EXECUTOR)}, {quote(INTRUDER)},
    {quote(DETECTOR_PRODUCER)}, {quote(DETECTOR_REVIEWER)}
  ]
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_roles WHERE rolname = actor_name
    ) THEN
      EXECUTE format(
        'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
        actor_name
      );
    END IF;
  END LOOP;
END;
$actors$;
COMMENT ON ROLE {PRODUCER} IS
  'NHI rule-history capability login allowlist. managed=nhi_rule_history_capability_login/v1 roles=nhi_rule_history_promotion_executor,nhi_rule_history_promotion_reviewer,nhi_rule_history_promotion_writer';
COMMENT ON ROLE {REVIEWER} IS
  'NHI rule-history capability login allowlist. managed=nhi_rule_history_capability_login/v1 roles=nhi_rule_history_promotion_executor,nhi_rule_history_promotion_reviewer,nhi_rule_history_promotion_writer';
COMMENT ON ROLE {EXECUTOR} IS
  'NHI rule-history capability login allowlist. managed=nhi_rule_history_capability_login/v1 roles=nhi_rule_history_promotion_executor';
COMMENT ON ROLE {DETECTOR_PRODUCER} IS
  'NHI rule-history capability login allowlist. managed=nhi_rule_history_capability_login/v1 roles=nhi_rule_history_format_detector_writer';
COMMENT ON ROLE {DETECTOR_REVIEWER} IS
  'NHI rule-history capability login allowlist. managed=nhi_rule_history_capability_login/v1 roles=nhi_rule_history_format_detector_reviewer';
GRANT {DETECTOR_WRITER_ROLE} TO {DETECTOR_PRODUCER};
GRANT {DETECTOR_REVIEWER_ROLE} TO {DETECTOR_REVIEWER};
GRANT {WRITER_ROLE} TO {PRODUCER};
GRANT {WRITER_ROLE} TO {REVIEWER};
GRANT {REVIEWER_ROLE} TO {REVIEWER};
GRANT {REVIEWER_ROLE} TO {PRODUCER};
GRANT {EXECUTOR_ROLE} TO {EXECUTOR};
GRANT {EXECUTOR_ROLE} TO {PRODUCER};
GRANT {EXECUTOR_ROLE} TO {REVIEWER};
""",
        )

    def query(self, command: str, *, user: str | None = None) -> str:
        return self.pg.psql(
            self.database,
            command=command,
            user=user,
        ).stdout.strip()

    def prepare_case(
        self,
        *,
        effective_from: dt.date,
        case_id: uuid.UUID | None = None,
        anchor_target_only: bool = False,
        omit_companion_replay: bool = False,
        provenance_substitution: bool = False,
        format_policy: str = "pdf_verified",
        format_declaration_locator_override: dict[str, str] | None = None,
        format_declaration_override: str | None = None,
        declared_event_count: int = 1,
        event_stream_fingerprint_override: str | None = None,
        hidden_pdf_attachment: bool = False,
        quarantined_supporting_pdf: bool = False,
        disguised_supporting_pdf: bool = False,
        omit_new_odt_detection_receipt: bool = False,
        omit_disguised_pdf_detection_receipt: bool = False,
        new_odt_bytes_override: bytes | None = None,
        new_pdf_bytes_override: bytes | None = None,
        replay_before_hash_override: str | None = None,
        publication_date_override: dt.date | None = None,
        document_date_override: dt.date | None = None,
        effective_date_raw_override: str | None = None,
        effective_date_calendar_system: str = "gregorian",
        document_date_raw_override: str | None = None,
        document_date_calendar_system: str = "gregorian",
        publication_date_raw_override: str | None = None,
        publication_date_calendar_system: str = "gregorian",
        effective_locator_override: dict[str, str] | None = None,
        companion_post_text_override: str | None = None,
        prior_target_event: bool = False,
        ready: bool = True,
    ) -> dict[str, str]:
        if format_policy not in (
            "odt_pdf_verified",
            "source_declared_odt_only",
            "pdf_verified",
        ):
            raise ValueError(format_policy)

        case_id = case_id or uuid.uuid4()
        token = case_id.hex
        job_id = uuid.uuid5(case_id, "job")
        lease_id = uuid.uuid5(case_id, "lease")
        attempt_id = uuid.uuid5(case_id, "attempt")
        bundle_id = uuid.uuid5(case_id, "bundle")
        proposal_id = uuid.uuid5(case_id, "proposal")
        candidate_transition_one = uuid.uuid5(case_id, "candidate-one")
        candidate_transition_two = uuid.uuid5(case_id, "candidate-two")

        rule_id = "rule:" + token
        companion_rule_id = "rule:companion:" + token
        designation = "9.9." + str(int(token[:2], 16))
        companion_designation = "9.9.companion." + token[:6]
        old_release = "release:old:" + token
        new_release = "release:new:" + token
        pdf_only = format_policy == "pdf_verified"
        old_artifact = (
            "artifact:old-pdf:" if pdf_only else "artifact:old-odt:"
        ) + token
        new_odt = "artifact:new-odt:" + token
        new_pdf = "artifact:new-pdf:" + token
        hidden_pdf = "artifact:hidden-pdf:" + token
        quarantined_pdf = "artifact:quarantined-pdf:" + token
        disguised_pdf = "artifact:disguised-pdf:" + token
        old_text = "舊版完整條文 " + token[:8]
        predecessor_text = (
            "中間版完整條文 " + token[:8]
            if prior_target_event
            else old_text
        )
        new_text = "新版完整條文 " + token[:8]
        companion_text = "未變動的同批條文 " + token[:8]
        companion_post_text = (
            companion_post_text_override or companion_text
        )
        effective_text = effective_from.isoformat()
        event_text = "正式公告 " + token[:8]
        event_detail_url = "https://example.invalid/event"
        event_issuer = "NHIA"
        event_reference_number = "REF-" + token[:8]
        event_subject = "fixture amendment"
        format_declaration = format_declaration_override or (
            "官方附件格式：ODT、PDF"
            if format_policy == "odt_pdf_verified"
            else (
                "官方附件格式：PDF"
                if pdf_only
                else "官方附件格式：ODT"
            )
        )

        anchor_old_hash = sha256(old_text)
        old_hash = sha256(predecessor_text)
        new_hash = sha256(new_text)
        companion_hash = sha256(companion_text)
        companion_post_hash = sha256(companion_post_text)
        case_fingerprint = sha256("case:" + token)
        old_artifact_bytes = (
            deterministic_pdf_bytes("old:" + token)
            if pdf_only
            else deterministic_odf_bytes(
                "application/vnd.oasis.opendocument.text",
                "old:" + token,
            )
        )
        new_odt_bytes = (
            new_odt_bytes_override
            if new_odt_bytes_override is not None
            else deterministic_odf_bytes(
                "application/vnd.oasis.opendocument.text",
                "new:" + token,
            )
        )
        new_pdf_bytes = (
            new_pdf_bytes_override
            if new_pdf_bytes_override is not None
            else deterministic_pdf_bytes("new:" + token)
        )
        hidden_pdf_bytes = deterministic_pdf_bytes("hidden:" + token)
        quarantined_pdf_bytes = deterministic_pdf_bytes(
            "quarantined:" + token
        )
        disguised_pdf_bytes = deterministic_pdf_bytes(
            "disguised:" + token
        )
        old_artifact_hash = hashlib.sha256(
            old_artifact_bytes
        ).hexdigest()
        new_odt_hash = hashlib.sha256(new_odt_bytes).hexdigest()
        new_pdf_hash = hashlib.sha256(new_pdf_bytes).hexdigest()
        hidden_pdf_hash = hashlib.sha256(hidden_pdf_bytes).hexdigest()
        quarantined_pdf_hash = hashlib.sha256(
            quarantined_pdf_bytes
        ).hexdigest()
        disguised_pdf_hash = hashlib.sha256(
            disguised_pdf_bytes
        ).hexdigest()
        primary_artifact = new_pdf if pdf_only else new_odt
        primary_artifact_bytes = (
            new_pdf_bytes if pdf_only else new_odt_bytes
        )
        primary_artifact_hash = (
            new_pdf_hash if pdf_only else new_odt_hash
        )
        provenance_pdf_attachment = provenance_substitution and pdf_only
        pre_manifest = sha256("pre-manifest:" + token)
        post_manifest = sha256("post-manifest:" + token)
        pre_rule_set = pg_jsonb_array_fingerprint(
            [
                [1, rule_id, designation, anchor_old_hash],
                [
                    2,
                    companion_rule_id,
                    companion_designation,
                    companion_post_hash,
                ],
            ]
        )
        post_rule_set = pg_jsonb_array_fingerprint(
            [
                [1, rule_id, designation, new_hash],
                [
                    2,
                    companion_rule_id,
                    companion_designation,
                    companion_hash,
                ],
            ]
        )
        publication_date = (
            publication_date_override
            or effective_from - dt.timedelta(days=7)
        )
        document_date = document_date_override or publication_date
        effective_date_raw = (
            effective_date_raw_override or effective_text
        )
        document_date_raw = (
            document_date_raw_override or document_date.isoformat()
        )
        publication_date_raw = (
            publication_date_raw_override
            or publication_date.isoformat()
        )
        effective_locator = json.dumps(
            effective_locator_override
            or {"fixture": "effective_expression"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        date_parser_version = "nhi-date-normalize/v1"
        effective_parse_fingerprint = pg_jsonb_value_fingerprint(
            [
                primary_artifact_hash,
                json.loads(effective_locator),
                effective_date_raw,
                effective_date_calendar_system,
                date_parser_version,
                effective_text,
            ]
        )
        document_parse_fingerprint = pg_jsonb_value_fingerprint(
            [
                primary_artifact_hash,
                {"fixture": "document_date"},
                document_date_raw,
                document_date_calendar_system,
                date_parser_version,
                document_date.isoformat(),
            ]
        )
        publication_parse_fingerprint = pg_jsonb_value_fingerprint(
            [
                primary_artifact_hash,
                {"fixture": "publication_date"},
                publication_date_raw,
                publication_date_calendar_system,
                date_parser_version,
                publication_date.isoformat(),
            ]
        )
        old_effective = effective_from - dt.timedelta(days=365)
        pre_anchor_date = effective_from - dt.timedelta(
            days=30 if prior_target_event else 1
        )
        prior_event_effective = effective_from - dt.timedelta(days=10)
        predecessor_snapshot_id = (
            "snapshot:predecessor:" + token
            if prior_target_event
            else "snapshot:old:" + token
        )
        candidate_authoritative_order = 2 if prior_target_event else 1
        prior_head_generation = 2 if prior_target_event else 1

        source_rows = [
            (
                "comparison_old",
                "comparison_old",
                predecessor_text,
                primary_artifact_hash,
            ),
            (
                "comparison_new",
                "comparison_new",
                new_text,
                primary_artifact_hash,
            ),
            (
                "effective_expression",
                "effective_expression",
                effective_date_raw,
                primary_artifact_hash,
            ),
            (
                "current_anchor",
                "current_anchor",
                designation,
                primary_artifact_hash,
            ),
            (
                "official_event",
                "detail_announcement",
                event_text,
                primary_artifact_hash,
            ),
            (
                "event_detail_url",
                "detail_announcement",
                event_detail_url,
                primary_artifact_hash,
            ),
            (
                "event_issuer",
                "detail_announcement",
                event_issuer,
                primary_artifact_hash,
            ),
            (
                "event_reference_number",
                "detail_announcement",
                event_reference_number,
                primary_artifact_hash,
            ),
            (
                "event_subject",
                "detail_announcement",
                event_subject,
                primary_artifact_hash,
            ),
            (
                "document_date",
                "detail_announcement",
                document_date_raw,
                primary_artifact_hash,
            ),
            (
                "publication_date",
                "detail_announcement",
                publication_date_raw,
                primary_artifact_hash,
            ),
            (
                "authoritative_order",
                "detail_announcement",
                str(candidate_authoritative_order),
                primary_artifact_hash,
            ),
            (
                "format_declaration",
                "detail_announcement",
                format_declaration,
                primary_artifact_hash,
            ),
        ]
        if format_policy == "odt_pdf_verified":
            source_rows.append(
                (
                    "pdf_corroboration",
                    "pdf_corroboration",
                    new_text,
                    new_pdf_hash,
                )
            )

        source_span_ids = {
            key: sha256(f"candidate-span:{key}:{token}")
            for key, _, _, _ in source_rows
        }
        source_span_values = []
        for key, source_role, text, artifact_hash in source_rows:
            locator = json.dumps(
                {"fixture": key},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            source_span_values.append(
                "("
                f"{quote(str(proposal_id))}::uuid,"
                f"{quote(source_span_ids[key])},"
                f"{quote(artifact_hash)},{quote(source_role)},"
                f"{quote(locator)}::jsonb,"
                f"{quote('fixture:' + key)},0,{len(text)},"
                f"{quote(text)},{quote(sha256(text))},{len(text)},"
                "'2026-01-01 00:02:00+00',"
                "'Source-grounded candidate evidence only; no legal-history identity, adjacency, interval closure, or executable mutation authority.'"
                ")"
            )

        content_artifact_values = [
            (
                f"({quote(primary_artifact_hash)},"
                f"{len(primary_artifact_bytes)},"
                + (
                    "'application/pdf',"
                    if pdf_only
                    else "'application/vnd.oasis.opendocument.text',"
                )
                + f"{quote('raw/' + token + ('/source.pdf' if pdf_only else '/source.odt'))},"
                "'2026-01-01 00:01:00+00')"
            )
        ]
        if format_policy == "odt_pdf_verified":
            content_artifact_values.append(
                (
                    f"({quote(new_pdf_hash)},{len(new_pdf_bytes)},"
                    "'application/pdf',"
                    f"{quote('raw/' + token + '/source.pdf')},"
                    "'2026-01-01 00:01:00+00')"
                )
            )
        if provenance_pdf_attachment:
            content_artifact_values.append(
                (
                    f"({quote(hidden_pdf_hash)},{len(hidden_pdf_bytes)},"
                    "'application/pdf',"
                    f"{quote('raw/' + token + '/substitution.pdf')},"
                    "'2026-01-01 00:01:00+00')"
                )
            )
        content_artifact_total_bytes = len(primary_artifact_bytes)
        if format_policy == "odt_pdf_verified":
            content_artifact_total_bytes += len(new_pdf_bytes)
        if provenance_pdf_attachment:
            content_artifact_total_bytes += len(hidden_pdf_bytes)

        odt_pdf_agreement = (
            "agree"
            if format_policy == "odt_pdf_verified"
            else "not_available"
        )
        canonical_pdf_values = ""
        release_pdf_values = ""
        if format_policy == "odt_pdf_verified":
            canonical_pdf_values = f""",
(
  {quote(new_pdf)}, 'https://example.invalid/new.pdf', 'new.pdf',
  'application/pdf', {len(new_pdf_bytes)}, {quote(new_pdf_hash)},
  '2026-01-01 00:00:00+00', 'https', 'official-public',
  'full_text_verified'
)"""
            release_pdf_values = (
                f",\n  ({quote(new_release)}, {quote(new_pdf)},"
                " 'official_pdf', 1)"
            )
        if provenance_pdf_attachment:
            canonical_pdf_values += f""",
(
  {quote(hidden_pdf)}, 'https://example.invalid/substitution.pdf',
  'substitution.pdf', 'application/pdf', {len(hidden_pdf_bytes)},
  {quote(hidden_pdf_hash)}, '2026-01-01 00:00:00+00', 'https',
  'official-public', 'full_text_verified'
)"""
            release_pdf_values += (
                f",\n  ({quote(new_release)}, {quote(hidden_pdf)},"
                " 'supporting', 1)"
            )
        if hidden_pdf_attachment:
            if format_policy != "source_declared_odt_only":
                raise ValueError(
                    "hidden PDF fixture is for the ODT-only adversary"
                )
            canonical_pdf_values += f""",
(
  {quote(hidden_pdf)}, 'https://example.invalid/hidden.pdf', 'hidden.pdf',
  'application/pdf', {len(hidden_pdf_bytes)}, {quote(hidden_pdf_hash)},
  '2026-01-01 00:00:00+00', 'https', 'official-public',
  'full_text_verified'
)"""
            release_pdf_values += (
                f",\n  ({quote(new_release)}, {quote(hidden_pdf)},"
                " 'supporting', 1)"
            )
        if quarantined_supporting_pdf:
            if format_policy not in ("odt_pdf_verified", "pdf_verified"):
                raise ValueError(
                    "quarantined PDF adversary requires a PDF policy"
                )
            canonical_pdf_values += f""",
(
  {quote(quarantined_pdf)},
  'https://example.invalid/quarantined.pdf', 'quarantined.pdf',
  'application/pdf', {len(quarantined_pdf_bytes)},
  {quote(quarantined_pdf_hash)},
  '2026-01-01 00:00:00+00', 'https', 'official-public',
  'quarantined'
)"""
            release_pdf_values += (
                f",\n  ({quote(new_release)}, {quote(quarantined_pdf)},"
                " 'supporting', 2)"
            )
        if disguised_supporting_pdf:
            if format_policy != "source_declared_odt_only":
                raise ValueError(
                    "disguised PDF adversary requires ODT-only policy"
                )
            canonical_pdf_values += f""",
(
  {quote(disguised_pdf)},
  'https://example.invalid/disguised.bin', 'supporting.bin',
  'application/octet-stream', {len(disguised_pdf_bytes)},
  {quote(disguised_pdf_hash)},
  '2026-01-01 00:00:00+00', 'https', 'official-public',
  'full_text_verified'
)"""
            release_pdf_values += (
                f",\n  ({quote(new_release)}, {quote(disguised_pdf)},"
                " 'supporting', 1)"
            )

        detector_inputs: list[tuple[str, str, str, bytes]] = [
            (old_artifact, old_release, pre_manifest, old_artifact_bytes)
        ]
        if not omit_new_odt_detection_receipt:
            detector_inputs.append(
                (
                    primary_artifact,
                    new_release,
                    post_manifest,
                    primary_artifact_bytes,
                )
            )
        if format_policy == "odt_pdf_verified":
            detector_inputs.append(
                (new_pdf, new_release, post_manifest, new_pdf_bytes)
            )
        if provenance_pdf_attachment:
            detector_inputs.append(
                (hidden_pdf, new_release, post_manifest, hidden_pdf_bytes)
            )
        if hidden_pdf_attachment:
            detector_inputs.append(
                (
                    hidden_pdf,
                    new_release,
                    post_manifest,
                    hidden_pdf_bytes,
                )
            )
        if quarantined_supporting_pdf:
            detector_inputs.append(
                (
                    quarantined_pdf,
                    new_release,
                    post_manifest,
                    quarantined_pdf_bytes,
                )
            )
        if (
            disguised_supporting_pdf
            and not omit_disguised_pdf_detection_receipt
        ):
            detector_inputs.append(
                (
                    disguised_pdf,
                    new_release,
                    post_manifest,
                    disguised_pdf_bytes,
                )
            )

        old_inventory_fingerprint = "0" * 64
        new_inventory_rows: list[str] = [primary_artifact]
        if format_policy == "odt_pdf_verified":
            new_inventory_rows.append(new_pdf)
        if provenance_pdf_attachment:
            new_inventory_rows.append(hidden_pdf)
        if hidden_pdf_attachment:
            new_inventory_rows.append(hidden_pdf)
        if quarantined_supporting_pdf:
            new_inventory_rows.append(quarantined_pdf)
        if disguised_supporting_pdf:
            new_inventory_rows.append(disguised_pdf)
        new_inventory_fingerprint = "0" * 64
        replay_before_hash = replay_before_hash_override or old_hash
        accepted_event_rows: list[list[object]] = []
        if prior_target_event:
            accepted_event_rows.append(
                [
                    1,
                    "canonical",
                    "event:prior:" + token,
                    rule_id,
                    prior_event_effective.isoformat(),
                    1,
                    anchor_old_hash,
                    old_hash,
                ]
            )
        accepted_event_rows.append(
            [
                2 if prior_target_event else 1,
                "candidate_resolution",
                "event:" + token,
                rule_id,
                effective_text,
                candidate_authoritative_order,
                replay_before_hash,
                new_hash,
            ]
        )
        accepted_event_fingerprint = pg_jsonb_array_fingerprint(
            accepted_event_rows
        )
        actual_declared_event_count = (
            2
            if prior_target_event and declared_event_count == 1
            else declared_event_count
        )
        declared_event_fingerprint = (
            event_stream_fingerprint_override
            or accepted_event_fingerprint
        )
        declaration_locator = json.dumps(
            format_declaration_locator_override
            or {"fixture": "format_declaration"},
            ensure_ascii=False,
            separators=(",", ":"),
        )

        anchor_snapshot_id = "snapshot:old:" + token
        if prior_target_event:
            snapshot_values = f"""
(
  {quote(anchor_snapshot_id)}, {quote(rule_id)}, {quote(old_release)},
  {quote(old_effective.isoformat())}::date,
  {quote(prior_event_effective.isoformat())}::date, 'official',
  '{{"fixture":"anchor-old-date"}}', {quote(old_text)}, {quote(old_text)},
  '{{"blocks":[]}}', {quote(anchor_old_hash)}, {quote(anchor_old_hash)},
  '{{"fixture":"anchor-old-text"}}', 'fixture-parser',
  'verified', 'canary'
), (
  {quote(predecessor_snapshot_id)}, {quote(rule_id)},
  {quote(old_release)}, {quote(prior_event_effective.isoformat())}::date,
  NULL, 'official',
  '{{"fixture":"predecessor-date"}}',
  {quote(predecessor_text)}, {quote(predecessor_text)},
  '{{"blocks":[]}}', {quote(old_hash)}, {quote(old_hash)},
  '{{"fixture":"predecessor-text"}}', 'fixture-parser',
  'verified', 'canary'
)"""
            prior_event_sql = f"""
INSERT INTO nhi_rule_history.official_event (
  event_id, detail_url, issuer, reference_number, subject,
  event_type, document_date, publication_date, effective_from,
  effective_date_basis, effective_date_locator, status
) VALUES (
  {quote("event:prior:" + token)},
  'https://example.invalid/prior-event/{token}', 'NHIA',
  {quote("PRIOR-" + token[:8])}, 'prior fixture amendment',
  'amendment', {quote((prior_event_effective - dt.timedelta(days=1)).isoformat())}::date,
  {quote((prior_event_effective - dt.timedelta(days=1)).isoformat())}::date,
  {quote(prior_event_effective.isoformat())}::date,
  'official_notice', '{{"fixture":"prior-effective"}}', 'verified'
);
INSERT INTO nhi_rule_history.official_event_effect (
  event_effect_id, event_id, operation, replacement_scope,
  effective_from, effective_date_raw, effective_date_locator,
  target_designation_raw, authoritative_order, rule_id,
  old_snapshot_id, new_snapshot_id, resolution_status
) VALUES (
  {quote("effect:prior:" + token)}, {quote("event:prior:" + token)},
  'amend', 'full_single_clause',
  {quote(prior_event_effective.isoformat())}::date,
  {quote(prior_event_effective.isoformat())},
  '{{"fixture":"prior-effective"}}', {quote(designation)}, 1,
  {quote(rule_id)}, {quote(anchor_snapshot_id)},
  {quote(predecessor_snapshot_id)}, 'verified'
);
"""
        else:
            snapshot_values = f"""
(
  {quote(anchor_snapshot_id)}, {quote(rule_id)}, {quote(old_release)},
  {quote(old_effective.isoformat())}::date, NULL, 'official',
  '{{"fixture":"old-date"}}', {quote(old_text)}, {quote(old_text)},
  '{{"blocks":[]}}', {quote(anchor_old_hash)}, {quote(anchor_old_hash)},
  '{{"fixture":"old-text"}}', 'fixture-parser',
  'verified', 'canary'
)"""
            prior_event_sql = ""

        base_sql = f"""
INSERT INTO nhi_rule_history_update_ops.update_job (
  job_id, job_fingerprint, contract_version, runner_version, feed_url,
  request_profile_sha256, notification_window_start,
  notification_window_end, activation_cut, scheduled_at
) VALUES (
  {quote(str(job_id))}::uuid, {quote(sha256("job:" + token))},
  'test/v1', 'test-runner', 'https://example.invalid/feed.xml',
  {quote(sha256("profile:" + token))},
  '2026-01-01 00:00:00+00', '2026-01-01 01:00:00+00',
  '2026-01-01', '2026-01-01 00:00:00+00'
);
INSERT INTO nhi_rule_history_update_ops.job_lease (
  lease_id, job_id, owner_key, acquired_at, expires_at,
  max_runtime_seconds
) VALUES (
  {quote(str(lease_id))}::uuid, {quote(str(job_id))}::uuid,
  'fixture-owner', '2026-01-01 00:00:00+00',
  '2026-01-01 00:05:00+00', 300
);
INSERT INTO nhi_rule_history_update_ops.worker_attempt (
  attempt_id, job_id, lease_id, owner_key, attempt_no, lane,
  provider, runtime, model, prompt_sha256, output_sha256,
  started_at, completed_at, status
) VALUES (
  {quote(str(attempt_id))}::uuid, {quote(str(job_id))}::uuid,
  {quote(str(lease_id))}::uuid, 'fixture-owner', 1, 'primary',
  'fixture', 'fixture', 'fixture', {quote(sha256("prompt:" + token))},
  {quote(sha256("output:" + token))}, '2026-01-01 00:00:00+00',
  '2026-01-01 00:01:00+00', 'success'
);
INSERT INTO nhi_rule_history_update_ops.content_artifact (
  artifact_sha256, byte_size, media_type, bundle_relative_path,
  first_observed_at
) VALUES
{",\n".join(content_artifact_values)};
INSERT INTO nhi_rule_history_update_ops.bundle_receipt (
  receipt_id, job_id, bundle_uid, manifest_sha256, bundle_relative_path,
  artifact_count, total_bytes, prepared_at, atomically_published_at,
  pg_received_at, fsync_verified, receipt_status
) VALUES (
  {quote(str(bundle_id))}::uuid, {quote(str(job_id))}::uuid,
  {quote("fixture-" + token)}, {quote(primary_artifact_hash)},
  {quote("tw-gov/fixture-" + token)}, {len(content_artifact_values)},
  {content_artifact_total_bytes},
  '2026-01-01 00:01:00+00', '2026-01-01 00:01:01+00',
  '2026-01-01 00:01:02+00', true, 'received'
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
  {quote(str(proposal_id))}::uuid, {quote(sha256("proposal:" + token))},
  'test/v1', {quote(str(job_id))}::uuid, {quote(str(bundle_id))}::uuid,
  {quote(str(attempt_id))}::uuid, {quote(sha256("output:" + token))},
  {quote(designation)}, {quote(effective_date_raw)},
  {quote(effective_date_calendar_system)},
  {quote(effective_text)}::date, 'day', 'effective_date',
  'single_clause', 'unconditional', 'full_single_clause',
  false, false, false, false, {quote(odt_pdf_agreement)},
  'source_designation_only', 1.0000
);
INSERT INTO nhi_rule_history_candidate_stage.candidate_source_span (
  proposal_id, span_id, artifact_sha256, source_role, locator,
  locator_key, char_start, char_end, raw_text, raw_text_sha256,
  raw_text_char_length, observed_at, statement
) VALUES
{",\n".join(source_span_values)};
INSERT INTO nhi_rule_history_candidate_stage.candidate_evidence (
  proposal_id, evidence_id, span_id, evidence_code, outcome,
  assertion_text, evidence_details, validator_version, recorded_at
) VALUES (
  {quote(str(proposal_id))}::uuid,
  {quote(sha256("candidate-evidence:" + token))},
  {quote(source_span_ids["comparison_new"])},
  'full_single_clause_replacement', 'pass',
  'Fixture proves complete replacement.', '{{"fixture":true}}',
  'fixture-validator', '2026-01-01 00:03:00+00'
);
INSERT INTO nhi_rule_history_candidate_stage.candidate_state_transition (
  proposal_id, transition_seq, transition_id, state, actor_kind,
  decision_basis_sha256, recorded_at
) VALUES (
  {quote(str(proposal_id))}::uuid, 1,
  {quote(str(candidate_transition_one))}::uuid, 'validated_candidate',
  'deterministic_validator', {quote(sha256("candidate-state-1:" + token))},
  '2026-01-01 00:04:00+00'
), (
  {quote(str(proposal_id))}::uuid, 2,
  {quote(str(candidate_transition_two))}::uuid,
  'promotion_ready_pending_anchor', 'system_gate',
  {quote(sha256("candidate-state-2:" + token))},
  '2026-01-01 00:05:00+00'
);

INSERT INTO nhi_rule_history.dataset_release (
  release_id, release_kind, official_label, release_date,
  release_date_basis, source_page_url, manifest_sha256,
  is_cumulative_anchor, declared_rule_count, rule_set_fingerprint,
  official_attachment_inventory_status,
  declared_official_attachment_count,
  official_attachment_inventory_fingerprint,
  verification_status
) VALUES (
  {quote(old_release)}, 'current_full', 'old fixture',
  {quote(old_effective.isoformat())}::date, 'official',
  'https://example.invalid/old', {quote(pre_manifest)},
  true, 2, {quote(pre_rule_set)}, 'exhaustive_verified', 1,
  {quote(old_inventory_fingerprint)}, 'verified'
), (
  {quote(new_release)}, 'current_full', 'new fixture',
  {quote(publication_date.isoformat())}::date, 'official',
  'https://example.invalid/new', {quote(post_manifest)},
  true, 2, {quote(post_rule_set)}, 'exhaustive_verified',
  {len(new_inventory_rows)}, {quote(new_inventory_fingerprint)},
  'verified'
);
INSERT INTO nhi_rule_history.source_artifact (
  artifact_id, official_url, filename, media_type, byte_length,
  sha256, fetched_at, fetch_transport, licence, verification_status
) VALUES (
  {quote(old_artifact)},
  {quote('https://example.invalid/old.pdf' if pdf_only else 'https://example.invalid/old.odt')},
  {quote('old.pdf' if pdf_only else 'old.odt')},
  {quote('application/pdf' if pdf_only else 'application/vnd.oasis.opendocument.text')},
  {len(old_artifact_bytes)},
  {quote(old_artifact_hash)}, '2026-01-01 00:00:00+00',
  'https', 'official-public', 'full_text_verified'
), (
  {quote(primary_artifact)},
  {quote('https://example.invalid/new.pdf' if pdf_only else 'https://example.invalid/new.odt')},
  {quote('new.pdf' if pdf_only else 'new.odt')},
  {quote('application/pdf' if pdf_only else 'application/vnd.oasis.opendocument.text')},
  {len(primary_artifact_bytes)},
  {quote(primary_artifact_hash)}, '2026-01-01 00:00:00+00',
  'https', 'official-public', 'full_text_verified'
)
{canonical_pdf_values};
INSERT INTO nhi_rule_history.release_artifact (
  release_id, artifact_id, artifact_role, source_order
) VALUES
  ({quote(old_release)}, {quote(old_artifact)},
   {quote('official_pdf' if pdf_only else 'official_odt')}, 0),
  ({quote(new_release)}, {quote(primary_artifact)},
   {quote('official_pdf' if pdf_only else 'official_odt')}, 0)
  {release_pdf_values};
INSERT INTO nhi_rule_history.rule_identity (
  rule_id, canonical_slug, identity_status,
  first_seen_release_id, last_seen_release_id
) VALUES (
  {quote(rule_id)}, {quote("rule-" + token)}, 'active',
  {quote(old_release)}, {quote(new_release)}
), (
  {quote(companion_rule_id)}, {quote("companion-" + token)}, 'active',
  {quote(old_release)}, {quote(new_release)}
);
INSERT INTO nhi_rule_history.rule_designation (
  designation_id, rule_id, designation_type, designation_value,
  valid_from, evidence_artifact_id, evidence_locator
) VALUES (
  {quote("designation:" + token)}, {quote(rule_id)}, 'article_number',
  {quote(designation)}, {quote(old_effective.isoformat())}::date,
  {quote(old_artifact)}, '{{"fixture":"designation"}}'
);
INSERT INTO nhi_rule_history.rule_snapshot (
  snapshot_id, rule_id, release_id, effective_from,
  effective_until_exclusive, date_basis,
  date_locator, raw_text, normalized_text, structured_json,
  raw_sha256, normalized_sha256, source_locator_json, parser_version,
  validation_status, publication_status
) VALUES
{snapshot_values};
{prior_event_sql}
INSERT INTO nhi_rule_history.rule_head (
  rule_id, current_snapshot_id, head_generation
) VALUES (
  {quote(rule_id)}, {quote(predecessor_snapshot_id)},
  {prior_head_generation}
);
"""
        self.pg.psql(self.database, command=base_sql)

        detector_calls = "\n".join(
            (
                "SELECT "
                "nhi_rule_history.register_artifact_format_detection("
                f"{quote(artifact_id)}, {quote(release_id)}, "
                f"{quote(manifest_sha)}, "
                f"decode({quote(raw_bytes.hex())}, 'hex'));"
            )
            for artifact_id, release_id, manifest_sha, raw_bytes
            in detector_inputs
        )
        self.pg.psql(
            self.database,
            user=DETECTOR_PRODUCER,
            command=(
                f"SET ROLE {DETECTOR_WRITER_ROLE};\n"
                + detector_calls
                + "\nRESET ROLE;"
            ),
        )
        reviewer_calls = "\n".join(
            (
                "SELECT "
                "nhi_rule_history.attest_artifact_format_detection("
                f"{quote(artifact_id)}, {quote(release_id)}, "
                f"{quote(manifest_sha)}, "
                f"decode({quote(raw_bytes.hex())}, 'hex'));"
            )
            for artifact_id, release_id, manifest_sha, raw_bytes
            in detector_inputs
        )
        self.pg.psql(
            self.database,
            user=DETECTOR_REVIEWER,
            command=(
                f"SET ROLE {DETECTOR_REVIEWER_ROLE};\n"
                + reviewer_calls
                + "\nRESET ROLE;"
            ),
        )

        def inventory_fingerprint(release_id: str) -> str:
            return self.query(
                f"""
SELECT encode(
  sha256(
    convert_to(
      jsonb_agg(
        jsonb_build_array(
          release_link.source_order,
          release_link.artifact_id,
          release_link.artifact_role,
          artifact_row.sha256,
          artifact_row.byte_length,
          artifact_row.media_type,
          artifact_row.official_url,
          artifact_row.verification_status,
          detection_row.detection_receipt_sha256,
          detection_row.detector_version,
          detection_row.detector_executable_sha256,
          detection_row.detected_media_type,
          detection_row.verification_status,
          detection_row.recorded_by,
          review_row.review_receipt_sha256,
          review_row.independent_verifier_version,
          review_row.independent_verifier_executable_sha256,
          review_row.independently_detected_media_type,
          review_row.verification_status,
          review_row.reviewed_by
        )
        ORDER BY release_link.source_order
      )::text,
      'UTF8'
    )
  ),
  'hex'
)
FROM nhi_rule_history.release_artifact release_link
JOIN nhi_rule_history.source_artifact artifact_row
  ON artifact_row.artifact_id = release_link.artifact_id
LEFT JOIN nhi_rule_history.artifact_format_detection detection_row
  ON detection_row.artifact_id = artifact_row.artifact_id
LEFT JOIN
  nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE release_link.release_id={quote(release_id)};
"""
            )

        old_inventory_fingerprint = inventory_fingerprint(old_release)
        new_inventory_fingerprint = inventory_fingerprint(new_release)
        self.pg.psql(
            self.database,
            command=f"""
UPDATE nhi_rule_history.dataset_release
SET official_attachment_inventory_fingerprint =
  CASE release_id
    WHEN {quote(old_release)} THEN {quote(old_inventory_fingerprint)}
    WHEN {quote(new_release)} THEN {quote(new_inventory_fingerprint)}
  END
WHERE release_id IN ({quote(old_release)}, {quote(new_release)});
""",
        )

        effect_span_rows = []
        effect_roles = (
            (
                "comparison_old_full_text",
                "comparison_old",
                predecessor_text,
                True,
            ),
            ("comparison_new_full_text", "comparison_new", new_text, True),
            (
                "effective_date",
                "effective_expression",
                effective_date_raw,
                False,
            ),
            ("designation", "current_anchor", designation, False),
            ("official_event", "official_event", event_text, False),
            (
                "event_detail_url",
                "event_detail_url",
                event_detail_url,
                False,
            ),
            ("event_issuer", "event_issuer", event_issuer, False),
            (
                "event_reference_number",
                "event_reference_number",
                event_reference_number,
                False,
            ),
            ("event_subject", "event_subject", event_subject, False),
            (
                "document_date",
                "document_date",
                document_date_raw,
                False,
            ),
            (
                "publication_date",
                "publication_date",
                publication_date_raw,
                False,
            ),
            (
                "authoritative_order",
                "authoritative_order",
                str(candidate_authoritative_order),
                False,
            ),
        )
        for order, (span_role, candidate_key, text, full) in enumerate(
            effect_roles,
            1,
        ):
            locator = json.dumps(
                {"fixture": candidate_key},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            artifact_id = (
                (hidden_pdf if pdf_only else new_pdf)
                if provenance_substitution
                and span_role == "official_event"
                else primary_artifact
            )
            effect_span_rows.append(
                "("
                f"{quote(str(case_id))}::uuid,"
                f"{quote(str(proposal_id))}::uuid,"
                f"{quote(source_span_ids[candidate_key])},"
                f"{order},{quote(span_role)},{quote(new_release)},"
                f"{quote(artifact_id)},{quote(locator)}::jsonb,"
                f"0,{len(text)},{quote(text)},{quote(sha256(text))},"
                f"{str(full).lower()},'verified'"
                ")"
            )

        anchor_clause_rows = [
            (
                f"({quote(str(case_id))}::uuid,'pre',1,{quote(rule_id)},"
                f"{quote(designation)},{quote(old_text)},"
                f"{quote(anchor_old_hash)},"
                "'{\"fixture\":\"pre-target\"}'::jsonb,'verified')"
            ),
            (
                f"({quote(str(case_id))}::uuid,'post',1,{quote(rule_id)},"
                f"{quote(designation)},{quote(new_text)},{quote(new_hash)},"
                "'{\"fixture\":\"post-target\"}'::jsonb,'verified')"
            ),
        ]
        if not anchor_target_only:
            anchor_clause_rows.extend(
                [
                    (
                        f"({quote(str(case_id))}::uuid,'pre',2,"
                        f"{quote(companion_rule_id)},"
                        f"{quote(companion_designation)},"
                        f"{quote(companion_post_text)},"
                        f"{quote(companion_post_hash)},"
                        "'{\"fixture\":\"pre-companion\"}'::jsonb,"
                        "'verified')"
                    ),
                    (
                        f"({quote(str(case_id))}::uuid,'post',2,"
                        f"{quote(companion_rule_id)},"
                        f"{quote(companion_designation)},"
                        f"{quote(companion_text)},{quote(companion_hash)},"
                        "'{\"fixture\":\"post-companion\"}'::jsonb,"
                        "'verified')"
                    ),
                ]
            )

        replay_rows = [
            (
                f"({quote(str(case_id))}::uuid,{quote(rule_id)},"
                f"{quote(anchor_old_hash)},{quote(new_hash)},"
                f"{quote(new_hash)},'verified')"
            )
        ]
        if not omit_companion_replay:
            replay_rows.append(
                (
                    f"({quote(str(case_id))}::uuid,"
                    f"{quote(companion_rule_id)},{quote(companion_hash)},"
                    f"{quote(companion_post_hash)},"
                    f"{quote(companion_post_hash)},"
                    "'verified')"
                )
            )

        if format_policy == "odt_pdf_verified":
            parity_evidence_columns = (
                f"{quote(new_pdf)},"
                f"{quote(new_odt)},"
                f"{quote(source_span_ids['format_declaration'])},"
                f"{quote(declaration_locator)}::jsonb,"
                f"0,{len(format_declaration)},"
                f"{quote(format_declaration)},"
                f"{quote(sha256(format_declaration))},"
                f"{quote(source_span_ids['pdf_corroboration'])},"
                "'[\"odt\", \"pdf\"]'::jsonb,"
                f"{len(new_inventory_rows)},"
                f"{quote(new_inventory_fingerprint)},"
                f"{quote(new_hash)},{quote(new_hash)}"
            )
            parity_odt_artifact_sql = quote(new_odt)
        elif pdf_only:
            parity_evidence_columns = (
                f"{quote(new_pdf)},"
                f"{quote(new_pdf)},"
                f"{quote(source_span_ids['format_declaration'])},"
                f"{quote(declaration_locator)}::jsonb,"
                f"0,{len(format_declaration)},"
                f"{quote(format_declaration)},"
                f"{quote(sha256(format_declaration))},"
                f"{quote(source_span_ids['comparison_new'])},"
                "'[\"pdf\"]'::jsonb,"
                f"{len(new_inventory_rows)},"
                f"{quote(new_inventory_fingerprint)},"
                f"NULL,{quote(new_hash)}"
            )
            parity_odt_artifact_sql = "NULL"
        else:
            parity_evidence_columns = (
                f"NULL,{quote(new_odt)},"
                f"{quote(source_span_ids['format_declaration'])},"
                f"{quote(declaration_locator)}::jsonb,"
                f"0,{len(format_declaration)},"
                f"{quote(format_declaration)},"
                f"{quote(sha256(format_declaration))},"
                "NULL,'[\"odt\"]'::jsonb,"
                f"{len(new_inventory_rows)},"
                f"{quote(new_inventory_fingerprint)},"
                f"{quote(new_hash)},NULL"
            )
            parity_odt_artifact_sql = quote(new_odt)

        replay_event_values = []
        for replay_event in accepted_event_rows:
            replay_event_values.append(
                "("
                f"{quote(str(case_id))}::uuid,{replay_event[0]},"
                f"{quote(str(replay_event[1]))},"
                f"{quote(str(replay_event[2]))},"
                f"{quote(str(replay_event[3]))},"
                f"{quote(str(replay_event[4]))}::date,"
                f"{replay_event[5]},"
                f"{quote(str(replay_event[6]))},"
                f"{quote(str(replay_event[7]))},'verified'"
                ")"
            )

        evidence_sql = f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.promotion_case (
  case_id, case_fingerprint, proposal_id, operation, replacement_scope,
  effective_from, new_raw_text, new_normalized_text, new_raw_sha256,
  new_normalized_sha256, new_structured_json, parser_version,
  publication_status
) VALUES (
  {quote(str(case_id))}::uuid, {quote(case_fingerprint)},
  {quote(str(proposal_id))}::uuid, 'amend', 'full_single_clause',
  {quote(effective_text)}::date, {quote(new_text)}, {quote(new_text)},
  {quote(new_hash)}, {quote(new_hash)}, '{{"blocks":[]}}',
  'fixture-parser', 'canary'
);
INSERT INTO nhi_rule_history_promotion.effect_resolution (
  case_id, rule_id, predecessor_snapshot_id, designation_id,
  target_designation_raw, resolved_event_id, event_detail_url,
  event_issuer, event_reference_number, event_subject, event_type,
  document_date, document_date_raw, document_date_calendar_system,
  document_date_parser_version, document_date_parse_sha256,
  publication_date, publication_date_raw,
  publication_date_calendar_system, publication_date_parser_version,
  publication_date_parse_sha256,
  effective_from, effective_date_raw, effective_date_calendar_system,
  effective_date_parser_version, effective_date_parse_sha256,
  effective_date_basis, effective_date_locator,
  authoritative_event_order, authoritative_event_order_raw,
  new_release_id,
  identity_resolution_status, event_resolution_status,
  full_text_resolution_status, operation, replacement_scope,
  split_ambiguity, merge_ambiguity, move_ambiguity,
  restore_ambiguity, correction_ambiguity, number_reuse_ambiguity,
  comparison_algorithm_version, comparison_input_sha256,
  comparison_output_sha256, comparison_mapping_coverage,
  comparison_format_only, resolution_evidence
) VALUES (
  {quote(str(case_id))}::uuid, {quote(rule_id)},
  {quote(predecessor_snapshot_id)}, {quote("designation:" + token)},
  {quote(designation)}, {quote("event:" + token)},
  {quote(event_detail_url)}, {quote(event_issuer)},
  {quote(event_reference_number)}, {quote(event_subject)}, 'amendment',
  {quote(document_date.isoformat())}::date,
  {quote(document_date_raw)},
  {quote(document_date_calendar_system)},
  {quote(date_parser_version)}, {quote(document_parse_fingerprint)},
  {quote(publication_date.isoformat())}::date,
  {quote(publication_date_raw)},
  {quote(publication_date_calendar_system)},
  {quote(date_parser_version)}, {quote(publication_parse_fingerprint)},
  {quote(effective_text)}::date, {quote(effective_date_raw)},
  {quote(effective_date_calendar_system)},
  {quote(date_parser_version)}, {quote(effective_parse_fingerprint)},
  'official_notice',
  {quote(effective_locator)}::jsonb,
  {candidate_authoritative_order},
  {quote(str(candidate_authoritative_order))}, {quote(new_release)},
  'verified', 'verified', 'verified', 'amend', 'full_single_clause',
  false, false, false, false, false, false, 'fixture-diff/v1',
  {quote(old_hash)}, {quote(new_hash)}, 1, false,
  '{{"fixture":"resolution"}}'
);
INSERT INTO nhi_rule_history_promotion.effect_resolution_span (
  case_id, proposal_id, candidate_span_id, span_order, span_role,
  release_id, artifact_id, source_locator, char_start, char_end,
  raw_text, raw_text_sha256, covers_full_clause, evidence_status
) VALUES
{",\n".join(effect_span_rows)};
INSERT INTO nhi_rule_history_promotion.anchor_snapshot (
  case_id, anchor_role, release_id, artifact_id, anchor_date,
  whole_release_manifest_sha256, declared_rule_count,
  rule_set_fingerprint, verification_status
) VALUES (
  {quote(str(case_id))}::uuid, 'pre', {quote(old_release)},
  {quote(old_artifact)},
  {quote(pre_anchor_date.isoformat())}::date,
  {quote(pre_manifest)}, 2, {quote(pre_rule_set)}, 'verified'
), (
  {quote(str(case_id))}::uuid, 'post', {quote(new_release)},
  {quote(primary_artifact)}, {quote(effective_text)}::date,
  {quote(post_manifest)}, 2, {quote(post_rule_set)}, 'verified'
);
INSERT INTO nhi_rule_history_promotion.anchor_clause (
  case_id, anchor_role, member_order, rule_id, designation_raw,
  raw_text, raw_text_sha256, source_locator, verification_status
) VALUES
{",\n".join(anchor_clause_rows)};
INSERT INTO nhi_rule_history_promotion.replay_run (
  case_id, replay_algorithm_version, pre_anchor_release_id,
  post_anchor_release_id, accepted_event_count,
  accepted_event_stream_sha256, replay_input_sha256,
  expected_rule_set_sha256, actual_rule_set_sha256,
  verification_status
) VALUES (
  {quote(str(case_id))}::uuid, 'fixture-replay/v1', {quote(old_release)},
  {quote(new_release)}, {actual_declared_event_count},
  {quote(declared_event_fingerprint)},
  {quote(declared_event_fingerprint)},
  {quote(post_rule_set)}, {quote(post_rule_set)}, 'verified'
);
INSERT INTO nhi_rule_history_promotion.replay_event (
  case_id, event_order, event_source, event_id, rule_id,
  effective_from, authoritative_order,
  before_raw_sha256, after_raw_sha256,
  verification_status
) VALUES
{",\n".join(replay_event_values)};
INSERT INTO nhi_rule_history_promotion.replay_rule_result (
  case_id, rule_id, before_raw_sha256, expected_after_raw_sha256,
  actual_after_raw_sha256, verification_status
) VALUES
{",\n".join(replay_rows)};
INSERT INTO nhi_rule_history_promotion.format_parity_receipt (
  case_id, proposal_id, release_id, format_policy,
  odt_artifact_id, pdf_artifact_id, format_declaration_artifact_id,
  format_declaration_candidate_span_id,
  format_declaration_source_locator,
  format_declaration_char_start, format_declaration_char_end,
  format_declaration_raw_text, format_declaration_raw_sha256,
  pdf_candidate_span_id, source_declared_formats,
  declared_official_attachment_count,
  official_attachment_inventory_fingerprint,
  odt_clause_sha256, pdf_clause_sha256,
  parity_fingerprint, verification_status
) VALUES (
  {quote(str(case_id))}::uuid, {quote(str(proposal_id))}::uuid,
  {quote(new_release)}, {quote(format_policy)}, {parity_odt_artifact_sql},
  {parity_evidence_columns},
  {quote(sha256("parity:" + token))}, 'verified'
);
RESET ROLE;
"""
        self.pg.psql(
            self.database,
            command=evidence_sql,
            user=PRODUCER,
        )

        if ready:
            self.insert_ready_transition(
                case_id=str(case_id),
                case_fingerprint=case_fingerprint,
            )

        return {
            "case_id": str(case_id),
            "case_fingerprint": case_fingerprint,
            "proposal_id": str(proposal_id),
            "rule_id": rule_id,
            "companion_rule_id": companion_rule_id,
            "companion_text": companion_text,
            "companion_hash": companion_hash,
            "old_release": old_release,
            "new_release": new_release,
            "new_odt": new_odt,
            "new_pdf": new_pdf,
            "primary_artifact": primary_artifact,
            "disguised_pdf": disguised_pdf,
            "post_manifest": post_manifest,
            "token": token,
            "old_snapshot_id": predecessor_snapshot_id,
            "anchor_snapshot_id": anchor_snapshot_id,
            "new_snapshot_id": "snapshot:" + str(case_id),
            "prior_head_generation": str(prior_head_generation),
            "anchor_old_hash": anchor_old_hash,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "effective_from": effective_text,
        }

    def insert_ready_transition(
        self,
        *,
        case_id: str,
        case_fingerprint: str,
        user: str = REVIEWER,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.pg.psql(
            self.database,
            user=user,
            check=check,
            command=f"""
SET ROLE {REVIEWER_ROLE};
INSERT INTO nhi_rule_history_promotion.promotion_transition (
  case_id, transition_seq, transition_id, state,
  decision_basis_sha256
) VALUES (
  {quote(case_id)}::uuid, 1,
  {quote("transition:" + case_id + ":ready")},
  'ready', {quote(case_fingerprint)}
);
RESET ROLE;
""",
        )

    def promote(
        self,
        fixture: dict[str, str],
        generation: int = 1,
        *,
        user: str = EXECUTOR,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.pg.psql(
            self.database,
            user=user,
            command=(
                f"SET ROLE {EXECUTOR_ROLE}; "
                "SELECT * FROM nhi_rule_history_promotion.promote_case("
                f"{quote(fixture['case_id'])}::uuid,"
                f"{quote(fixture['case_fingerprint'])},{generation});"
            ),
            check=check,
        )

    def test_role_capabilities_are_real_and_separated(self) -> None:
        result = self.query(
            f"""
SELECT
  has_table_privilege(
    '{WRITER_ROLE}',
    'nhi_rule_history_promotion.promotion_case', 'INSERT'
  ),
  has_table_privilege(
    '{WRITER_ROLE}',
    'nhi_rule_history_promotion.promotion_transition', 'INSERT'
  ),
  has_function_privilege(
    '{WRITER_ROLE}',
    'nhi_rule_history_promotion.promote_case(uuid,text,bigint)',
    'EXECUTE'
  ),
  has_table_privilege(
    '{REVIEWER_ROLE}',
    'nhi_rule_history_promotion.promotion_transition', 'INSERT'
  ),
  has_table_privilege(
    '{REVIEWER_ROLE}',
    'nhi_rule_history_promotion.promotion_case', 'INSERT'
  ),
  has_function_privilege(
    '{REVIEWER_ROLE}',
    'nhi_rule_history_promotion.promote_case(uuid,text,bigint)',
    'EXECUTE'
  ),
  has_table_privilege(
    '{EXECUTOR_ROLE}',
    'nhi_rule_history_promotion.promotion_case', 'SELECT'
  ),
  has_table_privilege(
    '{EXECUTOR_ROLE}',
    'nhi_rule_history_promotion.promotion_case', 'INSERT'
  ),
  has_table_privilege(
    '{EXECUTOR_ROLE}',
    'nhi_rule_history_promotion.promotion_transition', 'INSERT'
  ),
  has_function_privilege(
    '{EXECUTOR_ROLE}',
    'nhi_rule_history_promotion.promote_case(uuid,text,bigint)',
    'EXECUTE'
  ),
  has_table_privilege(
    '{EXECUTOR_ROLE}', 'nhi_rule_history.rule_snapshot', 'SELECT'
  ),
  has_table_privilege(
    '{EXECUTOR_ROLE}', 'nhi_rule_history.rule_snapshot', 'INSERT'
  ),
  has_table_privilege(
    '{DETECTOR_WRITER_ROLE}',
    'nhi_rule_history.artifact_format_detection', 'INSERT'
  ),
  has_function_privilege(
    '{DETECTOR_WRITER_ROLE}',
    'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)',
    'EXECUTE'
  ),
  has_function_privilege(
    '{DETECTOR_WRITER_ROLE}',
    'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)',
    'EXECUTE'
  ),
  has_table_privilege(
    '{DETECTOR_REVIEWER_ROLE}',
    'nhi_rule_history.artifact_format_detection_review', 'INSERT'
  ),
  has_function_privilege(
    '{DETECTOR_REVIEWER_ROLE}',
    'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)',
    'EXECUTE'
  ),
  has_function_privilege(
    '{DETECTOR_REVIEWER_ROLE}',
    'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)',
    'EXECUTE'
  ),
  has_function_privilege(
    '{DETECTOR_WRITER_ROLE}',
    'nhi_rule_history.inspect_odf_container_detector(bytea)',
    'EXECUTE'
  ),
  has_function_privilege(
    '{DETECTOR_REVIEWER_ROLE}',
    'nhi_rule_history.inspect_odf_container_reviewer(bytea)',
    'EXECUTE'
  );
"""
        )
        self.assertEqual(
            result,
            "t|f|f|t|f|f|t|f|f|t|t|f|f|t|f|f|f|t|f|f",
        )

        denied = self.pg.psql(
            self.database,
            user=EXECUTOR,
            check=False,
            command=f"""
SET ROLE {EXECUTOR_ROLE};
INSERT INTO nhi_rule_history_promotion.promotion_transition (
  case_id, transition_seq, transition_id, state,
  decision_basis_sha256
) VALUES (
  gen_random_uuid(), 1, 'executor-forbidden', 'rejected',
  repeat('0', 64)
);
""",
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("permission denied", denied.stderr)

    def test_producer_cannot_review_or_forge_actor_identity(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            ready=False,
        )
        same_actor = self.insert_ready_transition(
            case_id=fixture["case_id"],
            case_fingerprint=fixture["case_fingerprint"],
            user=PRODUCER,
            check=False,
        )
        self.assertNotEqual(same_actor.returncode, 0)
        self.assertIn("producer and independent reviewer", same_actor.stderr)

        mixed_text = "跨角色混入的證據"
        mixed_rule = "rule:mixed:" + fixture["case_id"]
        self.pg.psql(
            self.database,
            command=f"""
INSERT INTO nhi_rule_history.rule_identity (
  rule_id, canonical_slug, identity_status
) VALUES (
  {quote(mixed_rule)},
  {quote("mixed-" + fixture["case_id"])},
  'active'
);
""",
        )
        self.pg.psql(
            self.database,
            user=REVIEWER,
            command=f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.anchor_clause (
  case_id, anchor_role, member_order, rule_id, designation_raw,
  raw_text, raw_text_sha256, source_locator, verification_status
) VALUES (
  {quote(fixture["case_id"])}::uuid, 'pre', 3,
  {quote(mixed_rule)}, 'mixed', {quote(mixed_text)},
  {quote(sha256(mixed_text))}, '{{"fixture":"mixed-actor"}}',
  'verified'
);
""",
        )
        mixed_actor = self.insert_ready_transition(
            case_id=fixture["case_id"],
            case_fingerprint=fixture["case_fingerprint"],
            user=REVIEWER,
            check=False,
        )
        self.assertNotEqual(mixed_actor.returncode, 0)
        self.assertIn("one authenticated producer", mixed_actor.stderr)

        forged = self.pg.psql(
            self.database,
            user=PRODUCER,
            check=False,
            command=f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.anchor_clause (
  case_id, anchor_role, member_order, rule_id, designation_raw,
  raw_text, raw_text_sha256, source_locator, verification_status,
  recorded_by
)
SELECT
  case_id, anchor_role, member_order, rule_id, designation_raw,
  raw_text, raw_text_sha256, source_locator, verification_status,
  {quote(REVIEWER)}::name
FROM nhi_rule_history_promotion.anchor_clause
WHERE case_id={quote(fixture["case_id"])}::uuid
LIMIT 1;
""",
        )
        self.assertNotEqual(forged.returncode, 0)
        self.assertIn("authenticated session identity", forged.stderr)

    def test_future_date_and_stale_head_fail_without_side_effects(self) -> None:
        future = self.prepare_case(
            effective_from=dt.date.today() + dt.timedelta(days=1)
        )
        failed = self.promote(future, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("effective date has not arrived", failed.stderr)
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history.promotion_receipt;"
            ),
            "0",
        )

    def test_official_date_spans_order_and_locator_fail_closed(
        self,
    ) -> None:
        normalized_past = dt.date.today() - dt.timedelta(days=1)
        future_raw = (
            dt.date.today() + dt.timedelta(days=30)
        ).isoformat()
        mismatched_effective = self.prepare_case(
            effective_from=normalized_past,
            effective_date_raw_override=future_raw,
        )
        failed = self.promote(mismatched_effective, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("parsed effective date has not arrived", failed.stderr)

        future_publication = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            publication_date_override=(
                dt.date.today() + dt.timedelta(days=1)
            ),
        )
        failed = self.promote(future_publication, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("document/publication dates", failed.stderr)

        invalid_order = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            publication_date_override=(
                dt.date.today() - dt.timedelta(days=3)
            ),
            document_date_override=(
                dt.date.today() - dt.timedelta(days=2)
            ),
        )
        failed = self.promote(invalid_order, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("document/publication dates", failed.stderr)

        bad_locator = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            effective_locator_override={"fixture": "wrong-effective"},
        )
        failed = self.promote(bad_locator, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("effective-date", failed.stderr)

    def test_roc_year_zero_is_rejected_for_every_date_role(self) -> None:
        effective = dt.date.today() - dt.timedelta(days=1)
        zero_roc = "中華民國0年1月1日"
        cases = (
            {
                "effective_date_raw_override": zero_roc,
                "effective_date_calendar_system": "roc",
            },
            {
                "document_date_raw_override": zero_roc,
                "document_date_calendar_system": "roc",
            },
            {
                "publication_date_raw_override": zero_roc,
                "publication_date_calendar_system": "roc",
            },
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                fixture = self.prepare_case(
                    effective_from=effective,
                    **overrides,
                )
                failed = self.promote(fixture, check=False)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(
                    "cannot be deterministically normalized",
                    failed.stderr,
                )
                self.assertEqual(
                    self.query(
                        f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
                    ),
                    "0",
                )

    def test_roc_date_text_is_preserved_and_deterministically_parsed(
        self,
    ) -> None:
        effective = dt.date.today() - dt.timedelta(days=1)
        publication = effective - dt.timedelta(days=7)

        def roc_text(value: dt.date) -> str:
            return (
                f"中華民國{value.year - 1911}年"
                f"{value.month}月{value.day}日"
            )

        fixture = self.prepare_case(
            effective_from=effective,
            publication_date_override=publication,
            document_date_override=publication,
            effective_date_raw_override=roc_text(effective),
            effective_date_calendar_system="roc",
            document_date_raw_override=roc_text(publication),
            document_date_calendar_system="roc",
            publication_date_raw_override=roc_text(publication),
            publication_date_calendar_system="roc",
        )
        self.assertEqual(
            self.query(
                f"""
SELECT
  document_date_raw || '|' || document_date_calendar_system || '|' ||
  publication_date_raw || '|' || publication_date_calendar_system || '|' ||
  effective_date_raw || '|' || effective_date_calendar_system || '|' ||
  document_date_parser_version
FROM nhi_rule_history_promotion.effect_resolution
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
            ),
            (
                f"{roc_text(publication)}|roc|"
                f"{roc_text(publication)}|roc|"
                f"{roc_text(effective)}|roc|"
                "nhi-date-normalize/v1"
            ),
        )
        blocked = self.promote(fixture, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            blocked.stderr,
        )
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
            ),
            "0",
        )

    def test_integrity_blocker_prevents_every_canonical_change(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        failed = self.promote(fixture, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            failed.stderr,
        )
        state = self.query(
            f"""
SELECT
  (SELECT effective_until_exclusive IS NULL
   FROM nhi_rule_history.rule_snapshot
   WHERE snapshot_id={quote(fixture["old_snapshot_id"])}),
  (SELECT current_snapshot_id={quote(fixture["old_snapshot_id"])}
   FROM nhi_rule_history.rule_head
   WHERE rule_id={quote(fixture["rule_id"])}),
  (SELECT count(*) FROM nhi_rule_history.official_event),
  (SELECT count(*) FROM nhi_rule_history.official_event_effect),
  (SELECT count(*) FROM nhi_rule_history.promotion_receipt);
"""
        )
        self.assertEqual(state, "t|t|0|0|0")

    def test_valid_pdf_cannot_promote_or_replay(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        for _ in range(2):
            blocked = self.promote(fixture, check=False)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn(
                "blocked_pending_external_pdf_integrity_verifier",
                blocked.stderr,
            )
        state = self.query(
            f"""
SELECT
  (SELECT effective_until_exclusive IS NULL
   FROM nhi_rule_history.rule_snapshot
   WHERE snapshot_id={quote(fixture["old_snapshot_id"])}),
  (SELECT current_snapshot_id={quote(fixture["old_snapshot_id"])}
   FROM nhi_rule_history.rule_head
   WHERE rule_id={quote(fixture["rule_id"])}),
  (SELECT head_generation
   FROM nhi_rule_history.rule_head
   WHERE rule_id={quote(fixture["rule_id"])}),
  (SELECT count(*) FROM nhi_rule_history.official_event),
  (SELECT count(*) FROM nhi_rule_history.official_event_effect),
  (SELECT count(*) FROM nhi_rule_history.snapshot_evidence),
  (SELECT count(*) FROM nhi_rule_history.comparison_edge),
  (SELECT count(*) FROM nhi_rule_history.promotion_receipt);
"""
        )
        self.assertEqual(
            state,
            "t|t|1|0|0|0|0|0",
        )

    def test_concurrent_blocked_calls_create_no_receipt(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        promote_sql = (
            f"SET ROLE {EXECUTOR_ROLE}; "
            "SELECT * FROM nhi_rule_history_promotion.promote_case("
            f"{quote(fixture['case_id'])}::uuid,"
            f"{quote(fixture['case_fingerprint'])},1)"
        )
        first = self.pg.popen_psql(
            self.database,
            "BEGIN; "
            + promote_sql
            + "; SELECT pg_sleep(1); COMMIT;",
            user=EXECUTOR,
        )
        time.sleep(0.2)
        second = self.pg.psql(
            self.database,
            user=EXECUTOR,
            command=promote_sql + ";",
            check=False,
        )
        first_stdout, first_stderr = first.communicate(timeout=10)
        self.assertNotEqual(first.returncode, 0, first_stdout)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            first_stderr,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            second.stderr,
        )
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history.promotion_receipt;"
            ),
            "0",
        )

    def test_target_only_anchor_and_partial_replay_fail_closed(self) -> None:
        target_only = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            anchor_target_only=True,
            omit_companion_replay=True,
        )
        failed = self.promote(target_only, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("manifest-derived", failed.stderr)

        partial_replay = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            omit_companion_replay=True,
        )
        failed = self.promote(partial_replay, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("whole-anchor endpoint parity", failed.stderr)
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history.promotion_receipt;"
            ),
            "0",
        )

    def test_candidate_artifact_substitution_fails_closed(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            provenance_substitution=True,
        )
        failed = self.promote(fixture, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("exactly bind its staged candidate span", failed.stderr)
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history.promotion_receipt;"
            ),
            "0",
        )

    def test_source_declared_deflated_odt_is_blocked_pending_external_verifier(
        self,
    ) -> None:
        case_id = uuid.uuid4()
        deflated_odt = deterministic_odf_bytes(
            "application/vnd.oasis.opendocument.text",
            "new:" + case_id.hex,
            compress_payloads=True,
        )
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            case_id=case_id,
            format_policy="source_declared_odt_only",
            new_odt_bytes_override=deflated_odt,
        )
        self.assertEqual(
            self.query(
                f"""
SELECT
  (detection_row.detector_evidence
    ->>'contains_compressed_payload') || '|' ||
  (detection_row.detector_evidence
    ->>'compressed_payload_integrity_verified') || '|' ||
  (detection_row.detector_evidence
    ->>'promotion_eligible') || '|' ||
  (review_row.independent_evidence
    ->>'archive_integrity_gate')
FROM nhi_rule_history.artifact_format_detection detection_row
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE detection_row.artifact_id={quote(fixture["new_odt"])};
"""
            ),
            (
                "true|false|false|"
                "blocked_pending_external_archive_integrity_verifier"
            ),
        )
        blocked = self.promote(fixture, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            blocked.stderr,
        )
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
            ),
            "0",
        )

    def test_every_structural_odt_or_ods_is_observation_only(self) -> None:
        deflated_odt = deterministic_odf_bytes(
            "application/vnd.oasis.opendocument.text",
            "crc-adversary",
            compress_payloads=True,
        )
        bad_crc_odt = corrupt_zip_member_crc(
            deflated_odt,
            "content.xml",
        )
        with self.assertRaisesRegex(zipfile.BadZipFile, "Bad CRC-32"):
            with zipfile.ZipFile(
                io.BytesIO(bad_crc_odt),
                "r",
            ) as archive:
                archive.read("content.xml")

        cases = (
            (
                "stored_odt",
                "application/vnd.oasis.opendocument.text",
                deterministic_odf_bytes(
                    "application/vnd.oasis.opendocument.text",
                    "stored-observation",
                ),
                "false",
            ),
            (
                "bad_crc_odt",
                "application/vnd.oasis.opendocument.text",
                bad_crc_odt,
                "true",
            ),
            (
                "stored_ods",
                "application/vnd.oasis.opendocument.spreadsheet",
                deterministic_odf_bytes(
                    "application/vnd.oasis.opendocument.spreadsheet",
                    "stored-spreadsheet-observation",
                ),
                "false",
            ),
        )
        for label, media_type, raw_bytes, compressed in cases:
            with self.subTest(label=label):
                fixture = self.prepare_case(
                    effective_from=(
                        dt.date.today() - dt.timedelta(days=1)
                    ),
                    format_policy="source_declared_odt_only",
                    new_odt_bytes_override=raw_bytes,
                )
                observation = self.query(
                    f"""
SELECT
  detection_row.detected_media_type || '|' ||
  (detection_row.detector_evidence
    ->>'contains_compressed_payload') || '|' ||
  (detection_row.detector_evidence
    ->>'compressed_payload_integrity_verified') || '|' ||
  (detection_row.detector_evidence
    ->>'promotion_eligible') || '|' ||
  (review_row.independent_evidence
    ->>'promotion_eligible') || '|' ||
  (review_row.independent_evidence
    ->>'archive_integrity_gate')
FROM nhi_rule_history.artifact_format_detection detection_row
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE detection_row.artifact_id={quote(fixture["new_odt"])};
"""
                )
                self.assertEqual(
                    observation,
                    (
                        f"{media_type}|{compressed}|false|false|false|"
                        "blocked_pending_external_archive_integrity_verifier"
                    ),
                )
                blocked = self.promote(fixture, check=False)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(
                    "blocked_pending_external_archive_integrity_verifier",
                    blocked.stderr,
                )
                self.assertEqual(
                    self.query(
                        f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
                    ),
                    "0",
                )

    def test_every_pdf_is_observation_only(self) -> None:
        cases: list[tuple[str, uuid.UUID, bytes]] = []
        valid_case_id = uuid.uuid4()
        cases.append(
            (
                "valid_pdf",
                valid_case_id,
                deterministic_pdf_bytes("new:" + valid_case_id.hex),
            )
        )
        fake_case_id = uuid.UUID(
            "43f2bb18-669f-4ad8-a21d-a4b995e4ce61"
        )
        fake_pdf = (
            b"%PDF-NOT-A-PDF|new:" + fake_case_id.hex.encode("ascii")
        )
        self.assertEqual(len(fake_pdf), 51)
        self.assertEqual(
            hashlib.sha256(fake_pdf).hexdigest(),
            "84bcb02e1928fef8c2b1e04a0584051f8af75c5cc8a13cdc98dfed7f18d421ab",
        )
        self.assertFalse(fake_pdf.rstrip().endswith(b"%%EOF"))
        cases.append(("51_byte_pdf_magic_junk", fake_case_id, fake_pdf))

        for label, case_id, raw_bytes in cases:
            with self.subTest(label=label):
                fixture = self.prepare_case(
                    effective_from=(
                        dt.date.today() - dt.timedelta(days=1)
                    ),
                    case_id=case_id,
                    new_pdf_bytes_override=raw_bytes,
                )
                observation = self.query(
                    f"""
SELECT
  detection_row.detected_media_type || '|' ||
  (detection_row.detector_evidence
    ->>'pdf_integrity_verified') || '|' ||
  (detection_row.detector_evidence
    ->>'promotion_eligible') || '|' ||
  (review_row.independent_evidence
    ->>'pdf_integrity_verified') || '|' ||
  (review_row.independent_evidence
    ->>'promotion_eligible') || '|' ||
  (review_row.independent_evidence
    ->>'pdf_integrity_gate')
FROM nhi_rule_history.artifact_format_detection detection_row
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE detection_row.artifact_id={quote(fixture["new_pdf"])};
"""
                )
                self.assertEqual(
                    observation,
                    (
                        "application/pdf|false|false|false|false|"
                        "blocked_pending_external_pdf_integrity_verifier"
                    ),
                )
                blocked = self.promote(fixture, check=False)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(
                    "blocked_pending_external_pdf_integrity_verifier",
                    blocked.stderr,
                )
                self.assertEqual(
                    self.query(
                        f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
                    ),
                    "0",
                )

    def test_all_format_policies_have_no_promotion_lane(self) -> None:
        cases = (
            (
                "odt_pdf_verified",
                "blocked_pending_external_archive_integrity_verifier",
            ),
            (
                "source_declared_odt_only",
                "blocked_pending_external_archive_integrity_verifier",
            ),
            (
                "pdf_verified",
                "blocked_pending_external_pdf_integrity_verifier",
            ),
        )
        for format_policy, blocker in cases:
            with self.subTest(format_policy=format_policy):
                fixture = self.prepare_case(
                    effective_from=(
                        dt.date.today() - dt.timedelta(days=1)
                    ),
                    format_policy=format_policy,
                )
                blocked = self.promote(fixture, check=False)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(blocker, blocked.stderr)
                self.assertEqual(
                    self.query(
                        f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
                    ),
                    "0",
                )

    def test_attachment_inventory_and_exact_format_span_fail_closed(
        self,
    ) -> None:
        inventory = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        oracle_pdf_bytes = deterministic_pdf_bytes(
            "new:" + inventory["token"]
        )
        byte_receipt_rows = self.query(
            f"""
SELECT
  release_link.source_order::text || '|' ||
  artifact_row.artifact_id || '|' ||
  artifact_row.sha256 || '|' ||
  artifact_row.byte_length::text || '|' ||
  detection_row.detected_media_type || '|' ||
  review_row.independently_detected_media_type || '|' ||
  detection_row.recorded_by || '|' ||
  review_row.reviewed_by || '|' ||
  detection_row.detector_executable_sha256 || '|' ||
  review_row.independent_verifier_executable_sha256
FROM nhi_rule_history.release_artifact release_link
JOIN nhi_rule_history.source_artifact artifact_row
  ON artifact_row.artifact_id = release_link.artifact_id
JOIN nhi_rule_history.artifact_format_detection detection_row
  ON detection_row.artifact_id = artifact_row.artifact_id
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE release_link.release_id={quote(inventory["new_release"])}
ORDER BY release_link.source_order;
"""
        ).splitlines()
        self.assertEqual(len(byte_receipt_rows), 1)
        expected = (
            (
                "0",
                inventory["new_pdf"],
                hashlib.sha256(oracle_pdf_bytes).hexdigest(),
                str(len(oracle_pdf_bytes)),
                "application/pdf",
            ),
        )
        for row, oracle in zip(byte_receipt_rows, expected):
            fields = row.split("|")
            self.assertEqual(tuple(fields[:4]), oracle[:4])
            self.assertEqual(fields[4], oracle[4])
            self.assertEqual(fields[5], oracle[4])
            self.assertEqual(fields[6], DETECTOR_PRODUCER)
            self.assertEqual(fields[7], DETECTOR_REVIEWER)
            self.assertRegex(fields[8], r"^[0-9a-f]{64}$")
            self.assertRegex(fields[9], r"^[0-9a-f]{64}$")
            self.assertNotEqual(fields[8], fields[9])
        inventory_fingerprint = self.query(
            f"""
SELECT official_attachment_inventory_fingerprint
FROM nhi_rule_history.dataset_release
WHERE release_id={quote(inventory["new_release"])};
"""
        )
        self.assertRegex(inventory_fingerprint, r"^[0-9a-f]{64}$")
        self.pg.psql(
            self.database,
            command=f"""
UPDATE nhi_rule_history.dataset_release
SET official_attachment_inventory_fingerprint = repeat('f', 64)
WHERE release_id={quote(inventory["new_release"])};
""",
        )
        failed = self.promote(inventory, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("byte-derived artifact inventory", failed.stderr)

        bad_span = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_declaration_locator_override={"fixture": "wrong-span"},
        )
        failed = self.promote(bad_span, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("declared format policy", failed.stderr)

        inconsistent_declaration = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            format_declaration_override="官方附件格式：ODT、PDF",
        )
        failed = self.promote(inconsistent_declaration, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )

        hidden_pdf = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            hidden_pdf_attachment=True,
        )
        failed = self.promote(hidden_pdf, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )

        quarantined_pdf = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            quarantined_supporting_pdf=True,
        )
        failed = self.promote(quarantined_pdf, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("byte-derived artifact inventory", failed.stderr)

    def test_byte_derived_format_receipts_fail_closed(self) -> None:
        disguised = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            disguised_supporting_pdf=True,
        )
        raw_pdf = deterministic_pdf_bytes(
            "disguised:" + disguised["token"]
        )
        self.assertEqual(
            self.query(
                f"""
SELECT
  artifact_row.media_type || '|' ||
  detection_row.detected_media_type || '|' ||
  review_row.independently_detected_media_type || '|' ||
  detection_row.artifact_sha256 || '|' ||
  detection_row.artifact_byte_length::text || '|' ||
  detection_row.recorded_by || '|' ||
  review_row.reviewed_by
FROM nhi_rule_history.source_artifact artifact_row
JOIN nhi_rule_history.artifact_format_detection detection_row
  ON detection_row.artifact_id = artifact_row.artifact_id
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE artifact_row.artifact_id =
  {quote(disguised["disguised_pdf"])};
"""
            ),
            (
                "application/octet-stream|application/pdf|application/pdf|"
                f"{hashlib.sha256(raw_pdf).hexdigest()}|{len(raw_pdf)}|"
                f"{DETECTOR_PRODUCER}|{DETECTOR_REVIEWER}"
            ),
        )
        failed = self.promote(disguised, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )
        mutation = self.pg.psql(
            self.database,
            check=False,
            command=f"""
UPDATE nhi_rule_history.artifact_format_detection
SET detected_media_type='application/octet-stream'
WHERE artifact_id={quote(disguised["new_odt"])};
""",
        )
        self.assertNotEqual(mutation.returncode, 0)
        self.assertIn(
            "byte-derived artifact format detections are immutable",
            mutation.stderr,
        )

        missing = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            omit_new_odt_detection_receipt=True,
        )
        wrong_detector_bytes = deterministic_pdf_bytes(
            "wrong-for:" + missing["primary_artifact"]
        )
        wrong_byte_registration = self.pg.psql(
            self.database,
            user=DETECTOR_PRODUCER,
            check=False,
            command=f"""
SET ROLE {DETECTOR_WRITER_ROLE};
SELECT nhi_rule_history.register_artifact_format_detection(
  {quote(missing["primary_artifact"])},
  {quote(missing["new_release"])},
  {quote(missing["post_manifest"])},
  decode({quote(wrong_detector_bytes.hex())}, 'hex')
);
""",
        )
        self.assertNotEqual(wrong_byte_registration.returncode, 0)
        self.assertIn(
            "input bytes do not match the canonical artifact",
            wrong_byte_registration.stderr,
        )
        failed = self.promote(missing, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("byte-derived artifact inventory", failed.stderr)

        def forged_opaque_insert(
            fixture: dict[str, str],
            artifact_id: str,
            raw_bytes: bytes,
            *,
            magic_hex: str | None = None,
        ) -> str:
            evidence = {
                "basis": "opaque",
                "magic_hex": magic_hex or raw_bytes[:8].hex(),
            }
            evidence_sha = pg_jsonb_value_fingerprint(evidence)
            executable_sha = sha256(
                "adversarial-untrusted-detector-executable"
            )
            artifact_sha = hashlib.sha256(raw_bytes).hexdigest()
            receipt_sha = pg_jsonb_array_fingerprint(
                [
                    artifact_sha,
                    len(raw_bytes),
                    fixture["new_release"],
                    fixture["post_manifest"],
                    DETECTOR_NAME,
                    DETECTOR_VERSION,
                    executable_sha,
                    "application/octet-stream",
                    evidence_sha,
                    DETECTOR_PRODUCER,
                    DETECTOR_WRITER_ROLE,
                ]
            )
            return f"""
INSERT INTO nhi_rule_history.artifact_format_detection (
  detection_receipt_id, artifact_id, raw_release_id,
  raw_manifest_sha256, artifact_sha256, artifact_byte_length,
  detector_name, detector_version, detector_executable_sha256,
  detected_media_type, detector_evidence,
  detector_evidence_sha256, detection_receipt_sha256,
  verification_status, recorded_by, authority_role, detected_at
) VALUES (
  {quote("detection:" + artifact_id)}, {quote(artifact_id)},
  {quote(fixture["new_release"])},
  {quote(fixture["post_manifest"])},
  {quote(artifact_sha)}, {len(raw_bytes)},
  {quote(DETECTOR_NAME)}, {quote(DETECTOR_VERSION)},
  {quote(executable_sha)}, 'application/octet-stream',
  {quote(json.dumps(evidence, separators=(",", ":"), sort_keys=True))}::jsonb,
  {quote(evidence_sha)}, {quote(receipt_sha)}, 'verified',
  {quote(DETECTOR_PRODUCER)}, {quote(DETECTOR_WRITER_ROLE)},
  clock_timestamp()
);
"""

        false_pdf = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            disguised_supporting_pdf=True,
            omit_disguised_pdf_detection_receipt=True,
        )
        false_pdf_bytes = deterministic_pdf_bytes(
            "disguised:" + false_pdf["token"]
        )
        self.assertTrue(false_pdf_bytes.startswith(b"%PDF-"))
        self.assertTrue(false_pdf_bytes.rstrip().endswith(b"%%EOF"))
        direct_by_detector = self.pg.psql(
            self.database,
            user=DETECTOR_PRODUCER,
            check=False,
            command=(
                f"SET ROLE {DETECTOR_WRITER_ROLE};\n"
                + forged_opaque_insert(
                    false_pdf,
                    false_pdf["disguised_pdf"],
                    false_pdf_bytes,
                )
            ),
        )
        self.assertNotEqual(direct_by_detector.returncode, 0)
        self.assertIn(
            "permission denied for table artifact_format_detection",
            direct_by_detector.stderr,
        )

        self_consistent_false_pdf = self.pg.psql(
            self.database,
            check=False,
            command=forged_opaque_insert(
                false_pdf,
                false_pdf["disguised_pdf"],
                false_pdf_bytes,
            ),
        )
        self.assertNotEqual(self_consistent_false_pdf.returncode, 0)
        self.assertIn("check constraint", self_consistent_false_pdf.stderr)
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.artifact_format_detection
WHERE artifact_id={quote(false_pdf["disguised_pdf"])};
"""
            ),
            "0",
        )
        failed = self.promote(false_pdf, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )

        false_odt = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            omit_new_odt_detection_receipt=True,
        )
        false_odt_bytes = deterministic_odf_bytes(
            "application/vnd.oasis.opendocument.text",
            "new:" + false_odt["token"],
        )
        self.assertTrue(false_odt_bytes.startswith(b"PK\x03\x04"))
        with zipfile.ZipFile(
            io.BytesIO(false_odt_bytes),
            "r",
        ) as odt_archive:
            self.assertEqual(
                odt_archive.read("mimetype"),
                b"application/vnd.oasis.opendocument.text",
            )
        self_consistent_false_odt = self.pg.psql(
            self.database,
            check=False,
            command=forged_opaque_insert(
                false_odt,
                false_odt["new_odt"],
                false_odt_bytes,
            ),
        )
        self.assertNotEqual(self_consistent_false_odt.returncode, 0)
        self.assertIn("check constraint", self_consistent_false_odt.stderr)
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.artifact_format_detection
WHERE artifact_id={quote(false_odt["new_odt"])};
"""
            ),
            "0",
        )
        failed = self.promote(false_odt, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )

        forged_magic = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            format_policy="source_declared_odt_only",
            disguised_supporting_pdf=True,
            omit_disguised_pdf_detection_receipt=True,
        )
        forged_magic_bytes = deterministic_pdf_bytes(
            "disguised:" + forged_magic["token"]
        )
        forged_insert = self.pg.psql(
            self.database,
            command=forged_opaque_insert(
                forged_magic,
                forged_magic["disguised_pdf"],
                forged_magic_bytes,
                magic_hex="0000000000000000",
            ),
        )
        self.assertEqual(forged_insert.returncode, 0)
        independent_review = self.pg.psql(
            self.database,
            user=DETECTOR_REVIEWER,
            check=False,
            command=f"""
SET ROLE {DETECTOR_REVIEWER_ROLE};
SELECT nhi_rule_history.attest_artifact_format_detection(
  {quote(forged_magic["disguised_pdf"])},
  {quote(forged_magic["new_release"])},
  {quote(forged_magic["post_manifest"])},
  decode({quote(forged_magic_bytes.hex())}, 'hex')
);
""",
        )
        self.assertNotEqual(independent_review.returncode, 0)
        self.assertIn(
            "independent byte verifier disagrees",
            independent_review.stderr,
        )
        failed = self.promote(forged_magic, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )

    def test_odf_container_contract_rejects_invalid_zip_variants(
        self,
    ) -> None:
        adversarial_case_id = uuid.UUID(
            "8307c53d-3d20-4fc9-8269-1ad6d1ce4e3b"
        )
        token = adversarial_case_id.hex
        fake_zip = (
            b"PK\x03\x04NOT-A-ZIP|"
            b"application/vnd.oasis.opendocument.text|"
            + ("new:" + token).encode("ascii")
        )
        self.assertEqual(len(fake_zip), 90)
        self.assertEqual(
            hashlib.sha256(fake_zip).hexdigest(),
            "b0654966ca98b93046201a56d269a4675b6045604a051dde10502434cb8596ae",
        )
        with self.assertRaises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(fake_zip), "r")

        invalid = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            case_id=adversarial_case_id,
            format_policy="source_declared_odt_only",
            omit_new_odt_detection_receipt=True,
            new_odt_bytes_override=fake_zip,
        )
        detector = self.pg.psql(
            self.database,
            user=DETECTOR_PRODUCER,
            check=False,
            command=f"""
SET ROLE {DETECTOR_WRITER_ROLE};
SELECT nhi_rule_history.register_artifact_format_detection(
  {quote(invalid["new_odt"])},
  {quote(invalid["new_release"])},
  {quote(invalid["post_manifest"])},
  decode({quote(fake_zip.hex())}, 'hex')
);
""",
        )
        self.assertNotEqual(detector.returncode, 0)
        self.assertIn("ODF container contract", detector.stderr)
        self.assertEqual(
            self.query(
                f"""
SELECT
  (nhi_rule_history.inspect_odf_container_detector(
    decode({quote(fake_zip.hex())}, 'hex')
  ) IS NULL)::text || '|' ||
  (nhi_rule_history.inspect_odf_container_reviewer(
    decode({quote(fake_zip.hex())}, 'hex')
  ) IS NULL)::text || '|' ||
  (SELECT count(*)::text
   FROM nhi_rule_history.artifact_format_detection
   WHERE artifact_id={quote(invalid["new_odt"])}) || '|' ||
  (SELECT count(*)::text
   FROM nhi_rule_history.artifact_format_detection_review
   WHERE artifact_id={quote(invalid["new_odt"])});
"""
            ),
            "true|true|0|0",
        )
        failed = self.promote(invalid, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "blocked_pending_external_archive_integrity_verifier",
            failed.stderr,
        )
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(invalid["case_id"])}::uuid;
"""
            ),
            "0",
        )

        valid_odt = deterministic_odf_bytes(
            "application/vnd.oasis.opendocument.text",
            "container-variants",
        )
        central_signature = valid_odt.find(b"PK\x01\x02")
        self.assertGreater(central_signature, 0)
        corrupt_central = bytearray(valid_odt)
        corrupt_central[central_signature + 2] = 0x7F
        second_local_signature = valid_odt.find(b"PK\x03\x04", 4)
        self.assertGreater(second_local_signature, 0)
        corrupt_local_metadata = bytearray(valid_odt)
        corrupt_local_metadata[second_local_signature + 8] ^= 0x01
        required_entries = [
            (
                "content.xml",
                b"<office:document-content/>",
                zipfile.ZIP_DEFLATED,
            ),
            (
                "META-INF/manifest.xml",
                b"<manifest:manifest/>",
                zipfile.ZIP_DEFLATED,
            ),
        ]
        decoy_mimetype = deterministic_zip_bytes(
            [
                (
                    "decoy.txt",
                    b"application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_STORED,
                ),
                *required_entries,
            ]
        )
        compressed_mimetype = deterministic_zip_bytes(
            [
                (
                    "mimetype",
                    b"application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_DEFLATED,
                ),
                *required_entries,
            ]
        )
        unsafe_path = deterministic_zip_bytes(
            [
                (
                    "mimetype",
                    b"application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_STORED,
                ),
                *required_entries,
                ("../escape.txt", b"escape", zipfile.ZIP_STORED),
            ]
        )
        missing_manifest = deterministic_zip_bytes(
            [
                (
                    "mimetype",
                    b"application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_STORED,
                ),
                required_entries[0],
            ]
        )
        variants = {
            "truncated_eocd": valid_odt[:-10],
            "corrupt_central_signature": bytes(corrupt_central),
            "corrupt_local_metadata": bytes(corrupt_local_metadata),
            "decoy_mimetype_string": decoy_mimetype,
            "compressed_mimetype": compressed_mimetype,
            "unsafe_path": unsafe_path,
            "missing_manifest": missing_manifest,
        }
        for label, raw_bytes in variants.items():
            with self.subTest(label=label):
                self.assertEqual(
                    self.query(
                        f"""
SELECT
  (nhi_rule_history.inspect_odf_container_detector(
    decode({quote(raw_bytes.hex())}, 'hex')
  ) IS NULL)::text || '|' ||
  (nhi_rule_history.inspect_odf_container_reviewer(
    decode({quote(raw_bytes.hex())}, 'hex')
  ) IS NULL)::text;
"""
                    ),
                    "true|true",
                )

        valid_manifest = self.query(
            f"""
SELECT
  (nhi_rule_history.inspect_odf_container_detector(
    decode({quote(valid_odt.hex())}, 'hex')
  ) =
  nhi_rule_history.inspect_odf_container_reviewer(
    decode({quote(valid_odt.hex())}, 'hex')
  ))::text || '|' ||
  (nhi_rule_history.inspect_odf_container_detector(
    decode({quote(valid_odt.hex())}, 'hex')
  )->>'basis') || '|' ||
  (nhi_rule_history.inspect_odf_container_detector(
    decode({quote(valid_odt.hex())}, 'hex')
  )->>'entry_count');
"""
        )
        self.assertEqual(valid_manifest, "true|odf-zip-container|3")

    def test_detector_executable_identity_is_bound_at_promotion(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        self.assertEqual(
            self.query(
                f"""
SELECT bool_and(
  detection_row.detector_executable_sha256 =
    encode(
      sha256(
        convert_to(
          jsonb_build_array(
            pg_get_functiondef(
              'nhi_rule_history.register_artifact_format_detection(text,text,text,bytea)'::regprocedure
            ),
            pg_get_functiondef(
              'nhi_rule_history.inspect_odf_container_detector(bytea)'::regprocedure
            )
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
  AND review_row.independent_verifier_executable_sha256 =
    encode(
      sha256(
        convert_to(
          jsonb_build_array(
            pg_get_functiondef(
              'nhi_rule_history.attest_artifact_format_detection(text,text,text,bytea)'::regprocedure
            ),
            pg_get_functiondef(
              'nhi_rule_history.inspect_odf_container_reviewer(bytea)'::regprocedure
            )
          )::text,
          'UTF8'
        )
      ),
      'hex'
    )
  AND detection_row.detector_executable_sha256 <>
    review_row.independent_verifier_executable_sha256
)
FROM nhi_rule_history.release_artifact release_link
JOIN nhi_rule_history.artifact_format_detection detection_row
  ON detection_row.artifact_id = release_link.artifact_id
JOIN nhi_rule_history.artifact_format_detection_review review_row
  ON review_row.detection_receipt_id =
    detection_row.detection_receipt_id
WHERE release_link.release_id={quote(fixture["new_release"])};
"""
            ),
            "t",
        )
        self.pg.psql(
            self.database,
            command="""
CREATE OR REPLACE FUNCTION
  nhi_rule_history.inspect_odf_container_reviewer(
    artifact_bytes bytea
  )
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $changed$
BEGIN
  RETURN NULL;
END;
$changed$;
""",
        )
        failed = self.promote(fixture, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("byte-derived artifact inventory", failed.stderr)
        self.assertEqual(
            self.query(
                f"""
SELECT count(*)
FROM nhi_rule_history.promotion_receipt
WHERE case_id={quote(fixture["case_id"])}::uuid;
"""
            ),
            "0",
        )

    def test_arbitrary_replay_count_and_stream_hash_fail_closed(self) -> None:
        bad_count = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            declared_event_count=2,
        )
        failed = self.promote(bad_count, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("accepted event-stream replay", failed.stderr)

    def test_same_rule_same_order_cannot_fall_back_to_event_id(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            ready=False,
        )
        duplicate_order = self.pg.psql(
            self.database,
            user=PRODUCER,
            check=False,
            command=f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.replay_event (
  case_id, event_order, event_source, event_id, rule_id,
  effective_from, authoritative_order,
  before_raw_sha256, after_raw_sha256, verification_status
) VALUES (
  {quote(fixture["case_id"])}::uuid, 2, 'canonical',
  'aaa-lexically-first', {quote(fixture["rule_id"])},
  {quote(fixture["effective_from"])}::date, 1,
  {quote(sha256("same-day-before"))},
  {quote(sha256("same-day-after"))}, 'verified'
);
""",
        )
        self.assertNotEqual(duplicate_order.returncode, 0)
        self.assertIn("replay_event_case_id_rule_id", duplicate_order.stderr)

    def test_missing_verified_event_inside_anchor_interval_fails_closed(
        self,
    ) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        changed_text = "同批條文的已驗證歷史變更 " + fixture["case_id"][:8]
        changed_hash = sha256(changed_text)
        canonical_event_id = "event:canonical:" + fixture["case_id"]
        old_snapshot_id = "snapshot:companion-old:" + fixture["case_id"]
        new_snapshot_id = "snapshot:companion-new:" + fixture["case_id"]
        self.pg.psql(
            self.database,
            command=f"""
INSERT INTO nhi_rule_history.official_event (
  event_id, detail_url, issuer, reference_number, subject,
  event_type, document_date, publication_date, effective_from,
  effective_date_basis, effective_date_locator, status
) VALUES (
  {quote(canonical_event_id)},
  {quote("https://example.invalid/" + canonical_event_id)},
  'NHIA', {quote("CAN-" + fixture["case_id"][:8])},
  'verified companion change', 'amendment',
  ({quote(fixture["effective_from"])}::date - 1),
  ({quote(fixture["effective_from"])}::date - 1),
  {quote(fixture["effective_from"])}::date,
  'official_notice', '{{"fixture":"canonical-event"}}',
  'verified'
);
INSERT INTO nhi_rule_history.rule_snapshot (
  snapshot_id, rule_id, release_id, effective_from,
  effective_until_exclusive, date_basis, date_locator,
  raw_text, normalized_text, structured_json, raw_sha256,
  normalized_sha256, source_locator_json, parser_version,
  validation_status, publication_status
) VALUES (
  {quote(old_snapshot_id)}, {quote(fixture["companion_rule_id"])},
  {quote(fixture["old_release"])},
  ({quote(fixture["effective_from"])}::date - 365),
  {quote(fixture["effective_from"])}::date,
  'official', '{{"fixture":"companion-old-date"}}',
  {quote(fixture["companion_text"])},
  {quote(fixture["companion_text"])}, '{{"blocks":[]}}',
  {quote(fixture["companion_hash"])},
  {quote(fixture["companion_hash"])},
  '{{"fixture":"companion-old"}}', 'fixture-parser',
  'verified', 'canary'
), (
  {quote(new_snapshot_id)}, {quote(fixture["companion_rule_id"])},
  {quote(fixture["new_release"])},
  {quote(fixture["effective_from"])}::date, NULL,
  'official', '{{"fixture":"companion-new-date"}}',
  {quote(changed_text)}, {quote(changed_text)}, '{{"blocks":[]}}',
  {quote(changed_hash)}, {quote(changed_hash)},
  '{{"fixture":"companion-new"}}', 'fixture-parser',
  'verified', 'canary'
);
INSERT INTO nhi_rule_history.official_event_effect (
  event_effect_id, event_id, operation, replacement_scope,
  effective_from, effective_date_raw, effective_date_locator,
  target_designation_raw, authoritative_order,
  rule_id, old_snapshot_id,
  new_snapshot_id, resolution_status
) VALUES (
  {quote("effect:canonical:" + fixture["case_id"])},
  {quote(canonical_event_id)}, 'amend', 'full_single_clause',
  {quote(fixture["effective_from"])}::date,
  {quote(fixture["effective_from"])},
  '{{"fixture":"canonical-effective"}}', 'companion',
  1,
  {quote(fixture["companion_rule_id"])},
  {quote(old_snapshot_id)}, {quote(new_snapshot_id)}, 'verified'
);
""",
        )
        failed = self.promote(fixture, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("accepted event-stream replay", failed.stderr)

    def test_changed_eventless_anchor_member_fails_closed(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            companion_post_text_override="無事件卻被改寫的同批條文",
        )
        failed = self.promote(fixture, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("accepted event-stream replay", failed.stderr)
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history.promotion_receipt;"
            ),
            "0",
        )

        bad_hash = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            event_stream_fingerprint_override=sha256("arbitrary-stream"),
        )
        failed = self.promote(bad_hash, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("accepted event-stream replay", failed.stderr)

        broken_chain = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            replay_before_hash_override=sha256("not-the-pre-anchor"),
        )
        failed = self.promote(broken_chain, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("accepted event-stream replay", failed.stderr)

    def test_target_multi_event_anchor_is_staged_but_blocked(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            prior_target_event=True,
        )
        blocked = self.promote(fixture, generation=2, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            blocked.stderr,
        )

        token = fixture["token"]
        anchor_hash = sha256("舊版完整條文 " + token[:8])
        predecessor_hash = sha256("中間版完整條文 " + token[:8])
        expected_hash_chain = ">".join(
            (anchor_hash, predecessor_hash)
        )
        expected_effect_chain = (
            f"1:{anchor_hash}>{predecessor_hash}"
        )
        self.assertEqual(
            self.query(
                f"""
SELECT
  (
    SELECT string_agg(
      snapshot_row.raw_sha256,
      '>' ORDER BY snapshot_row.effective_from
    )
    FROM nhi_rule_history.rule_snapshot snapshot_row
    WHERE snapshot_row.rule_id={quote(fixture["rule_id"])}
  ) || '|' ||
  (
    SELECT string_agg(
      effect_row.authoritative_order::text || ':' ||
      old_snapshot.raw_sha256 || '>' || new_snapshot.raw_sha256,
      '|' ORDER BY effect_row.authoritative_order
    )
    FROM nhi_rule_history.official_event_effect effect_row
    JOIN nhi_rule_history.rule_snapshot old_snapshot
      ON old_snapshot.snapshot_id=effect_row.old_snapshot_id
    JOIN nhi_rule_history.rule_snapshot new_snapshot
      ON new_snapshot.snapshot_id=effect_row.new_snapshot_id
    WHERE effect_row.rule_id={quote(fixture["rule_id"])}
  );
"""
            ),
            f"{expected_hash_chain}|{expected_effect_chain}",
        )
        self.assertEqual(
            self.query(
                f"""
SELECT
  (SELECT count(*)::text
   FROM nhi_rule_history_promotion.replay_event
   WHERE case_id={quote(fixture["case_id"])}::uuid) || '|' ||
  (SELECT count(*)::text
   FROM nhi_rule_history.promotion_receipt
   WHERE case_id={quote(fixture["case_id"])}::uuid);
"""
            ),
            "2|0",
        )

    def test_executor_session_identity_must_be_independent(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1)
        )
        for actor in (PRODUCER, REVIEWER):
            failed = self.promote(
                fixture,
                user=actor,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(
                "executor identity must differ",
                failed.stderr,
            )

        blocked = self.promote(fixture, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(
            "blocked_pending_external_pdf_integrity_verifier",
            blocked.stderr,
        )
        identities = self.query(
            f"""
SELECT
  (SELECT count(*)::text
   FROM nhi_rule_history.promotion_receipt
   WHERE case_id={quote(fixture["case_id"])}::uuid) || '|' ||
  (SELECT count(*)::text
   FROM nhi_rule_history_promotion.promotion_transition
   WHERE case_id={quote(fixture["case_id"])}::uuid
     AND state='promoted');
"""
        )
        self.assertEqual(identities, "0|0")

    def test_publication_and_mapping_contracts_reject_weaker_values(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            ready=False,
        )
        publishable = self.pg.psql(
            self.database,
            user=PRODUCER,
            check=False,
            command=f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.promotion_case (
  case_id, case_fingerprint, proposal_id, operation, replacement_scope,
  effective_from, new_raw_text, new_normalized_text, new_raw_sha256,
  new_normalized_sha256, new_structured_json, parser_version,
  publication_status
)
SELECT
  gen_random_uuid(), repeat('a', 64), proposal_id, operation,
  replacement_scope, effective_from, new_raw_text, new_normalized_text,
  new_raw_sha256, new_normalized_sha256, new_structured_json,
  parser_version, 'publishable'
FROM nhi_rule_history_promotion.promotion_case
WHERE case_id={quote(fixture["case_id"])}::uuid;
""",
        )
        self.assertNotEqual(publishable.returncode, 0)
        self.assertIn("publication_status_check", publishable.stderr)

        partial_mapping = self.pg.psql(
            self.database,
            user=PRODUCER,
            check=False,
            command=f"""
SET ROLE {WRITER_ROLE};
INSERT INTO nhi_rule_history_promotion.effect_resolution
SELECT
  case_id, rule_id, predecessor_snapshot_id, designation_id,
  target_designation_raw, resolved_event_id, event_detail_url,
  event_issuer, event_reference_number, event_subject, event_type,
  document_date, document_date_raw, document_date_calendar_system,
  document_date_parser_version, document_date_parse_sha256,
  publication_date, publication_date_raw,
  publication_date_calendar_system, publication_date_parser_version,
  publication_date_parse_sha256,
  effective_from, effective_date_raw, effective_date_calendar_system,
  effective_date_parser_version, effective_date_parse_sha256,
  effective_date_basis, effective_date_locator,
  authoritative_event_order, authoritative_event_order_raw,
  new_release_id,
  identity_resolution_status, event_resolution_status,
  full_text_resolution_status, operation, replacement_scope,
  split_ambiguity, merge_ambiguity, move_ambiguity,
  restore_ambiguity, correction_ambiguity, number_reuse_ambiguity,
  comparison_algorithm_version, comparison_input_sha256,
  comparison_output_sha256, 0.999, comparison_format_only,
  resolution_evidence, recorded_by, recorded_role, recorded_at
FROM nhi_rule_history_promotion.effect_resolution
WHERE case_id={quote(fixture["case_id"])}::uuid;
""",
        )
        self.assertNotEqual(partial_mapping.returncode, 0)
        self.assertIn(
            "effect_resolution_comparison_mapping_coverage_check",
            partial_mapping.stderr,
        )

    def test_exact_reapply_succeeds_and_structural_drift_fails(self) -> None:
        self.pg.psql(self.database, file=CANONICAL_FORWARD)
        self.pg.psql(self.database, file=PROMOTION_FORWARD)

        self.pg.psql(
            self.database,
            command=(
                "ALTER TABLE nhi_rule_history.rule_identity "
                "ADD COLUMN contract_drift text;"
            ),
        )
        canonical_drift = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(canonical_drift.returncode, 0)
        self.assertIn("structural contract drift", canonical_drift.stderr)

        self.pg.psql(
            self.database,
            command=(
                "ALTER TABLE nhi_rule_history_promotion.promotion_case "
                "ADD COLUMN contract_drift text;"
            ),
        )
        promotion_drift = self.pg.psql(
            self.database,
            file=PROMOTION_FORWARD,
            check=False,
        )
        self.assertNotEqual(promotion_drift.returncode, 0)
        self.assertIn("structural contract drift", promotion_drift.stderr)

    def test_disabled_trigger_is_structural_drift(self) -> None:
        self.pg.psql(
            self.database,
            command="""
ALTER TABLE nhi_rule_history_promotion.promotion_transition
  DISABLE TRIGGER promotion_transition_insert_guard;
""",
        )
        drift = self.pg.psql(
            self.database,
            file=PROMOTION_FORWARD,
            check=False,
        )
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("structural contract drift", drift.stderr)

    def test_row_level_security_state_is_structural_drift(self) -> None:
        self.pg.psql(
            self.database,
            command="""
ALTER TABLE nhi_rule_history.rule_identity
  ENABLE ROW LEVEL SECURITY;
""",
        )
        drift = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("structural contract drift", drift.stderr)

    def test_relation_durability_and_options_are_structural_drift(
        self,
    ) -> None:
        self.pg.psql(
            self.database,
            command="""
CREATE UNLOGGED TABLE nhi_rule_history.unlogged_drift (
  drift_id bigint PRIMARY KEY
);
""",
        )
        durability = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(durability.returncode, 0)
        self.assertIn("requires persistent relations", durability.stderr)

        self.pg.psql(
            self.database,
            command="""
DROP TABLE nhi_rule_history.unlogged_drift;
ALTER TABLE nhi_rule_history.rule_identity SET (fillfactor=80);
""",
        )
        options = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(options.returncode, 0)
        self.assertIn("structural contract drift", options.stderr)

    def test_owner_and_capability_memberships_fail_closed(self) -> None:
        self.pg.psql(
            self.database,
            command=f"GRANT {WRITER_ROLE} TO {INTRUDER};",
        )
        capability = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(capability.returncode, 0)
        self.assertTrue(
            "structural contract drift" in capability.stderr
            or "explicitly allowlisted" in capability.stderr
        )
        self.pg.psql(
            self.database,
            command=f"REVOKE {WRITER_ROLE} FROM {INTRUDER};",
        )

        self.pg.psql(
            self.database,
            command=(
                f"GRANT {DETECTOR_REVIEWER_ROLE} "
                f"TO {DETECTOR_PRODUCER};"
            ),
        )
        detector_role_overlap = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(detector_role_overlap.returncode, 0)
        self.assertTrue(
            "structural contract drift" in detector_role_overlap.stderr
            or (
                "producer and independent reviewer must be different"
                in detector_role_overlap.stderr
            )
        )
        self.pg.psql(
            self.database,
            command=(
                f"REVOKE {DETECTOR_REVIEWER_ROLE} "
                f"FROM {DETECTOR_PRODUCER};"
            ),
        )

        self.pg.psql(
            self.database,
            command=(
                "GRANT nhi_rule_history_reader "
                "TO nhi_rule_history_owner;"
            ),
        )
        owner = self.pg.psql(
            self.database,
            file=CANONICAL_FORWARD,
            check=False,
        )
        self.assertNotEqual(owner.returncode, 0)
        self.assertTrue(
            "structural contract drift" in owner.stderr
            or "zero role memberships" in owner.stderr
        )
        self.pg.psql(
            self.database,
            command=(
                "REVOKE nhi_rule_history_reader "
                "FROM nhi_rule_history_owner;"
            ),
        )

    def test_rollbacks_reject_any_managed_rows(self) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            ready=False,
        )
        promotion_rollback = self.pg.psql(
            self.database,
            file=PROMOTION_ROLLBACK,
            check=False,
        )
        self.assertNotEqual(promotion_rollback.returncode, 0)
        self.assertIn("nonempty", promotion_rollback.stderr)
        self.assertEqual(
            self.query(
                "SELECT count(*) FROM nhi_rule_history_promotion.promotion_case "
                f"WHERE case_id={quote(fixture['case_id'])}::uuid;"
            ),
            "1",
        )

    def test_promotion_rollback_waits_for_authenticated_writer(
        self,
    ) -> None:
        fixture = self.prepare_case(
            effective_from=dt.date.today() - dt.timedelta(days=1),
            ready=False,
        )
        writer = self.pg.popen_psql(
            self.database,
            f"""
BEGIN;
SET ROLE {REVIEWER_ROLE};
INSERT INTO nhi_rule_history_promotion.promotion_transition (
  case_id, transition_seq, transition_id, state,
  decision_basis_sha256
) VALUES (
  {quote(fixture["case_id"])}::uuid, 1,
  {quote("transition:" + fixture["case_id"] + ":rejected")},
  'rejected', {quote(fixture["case_fingerprint"])}
);
SELECT pg_sleep(1);
COMMIT;
""",
            user=REVIEWER,
        )
        time.sleep(0.2)
        started = time.monotonic()
        rollback = self.pg.psql(
            self.database,
            file=PROMOTION_ROLLBACK,
            check=False,
        )
        elapsed = time.monotonic() - started
        writer_stdout, writer_stderr = writer.communicate(timeout=10)
        self.assertEqual(writer.returncode, 0, writer_stderr)
        self.assertIn("INSERT 0 1", writer_stdout)
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("nonempty", rollback.stderr)

    def test_canonical_rollback_rejects_any_managed_rows(self) -> None:
        self.pg.psql(self.database, file=PROMOTION_ROLLBACK)
        self.pg.psql(
            self.database,
            command=f"""
INSERT INTO nhi_rule_history.dataset_release (
  release_id, release_kind, official_label, release_date_basis,
  source_page_url, manifest_sha256, is_cumulative_anchor,
  official_attachment_inventory_status,
  declared_official_attachment_count,
  official_attachment_inventory_fingerprint,
  verification_status
) VALUES (
  'rollback-nonempty', 'event_attachment', 'fixture', 'official',
  'https://example.invalid/rollback',
  {quote(sha256("rollback-nonempty"))}, false,
  'exhaustive_verified', 1,
  {quote(sha256("rollback-inventory"))}, 'verified'
);
""",
        )
        rollback = self.pg.psql(
            self.database,
            file=CANONICAL_ROLLBACK,
            check=False,
        )
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("nonempty", rollback.stderr)

    def test_canonical_rollback_waits_for_authenticated_writer(
        self,
    ) -> None:
        self.pg.psql(self.database, file=PROMOTION_ROLLBACK)
        self.pg.psql(
            self.database,
            command=f"GRANT nhi_rule_history_owner TO {INTRUDER};",
        )
        writer = self.pg.popen_psql(
            self.database,
            f"""
BEGIN;
SET ROLE nhi_rule_history_owner;
INSERT INTO nhi_rule_history.dataset_release (
  release_id, release_kind, official_label, release_date_basis,
  source_page_url, manifest_sha256, is_cumulative_anchor,
  official_attachment_inventory_status,
  declared_official_attachment_count,
  official_attachment_inventory_fingerprint,
  verification_status
) VALUES (
  'rollback-race', 'event_attachment', 'race fixture', 'official',
  'https://example.invalid/rollback-race',
  {quote(sha256("rollback-race-manifest"))}, false,
  'exhaustive_verified', 1,
  {quote(sha256("rollback-race-inventory"))}, 'verified'
);
SELECT pg_sleep(1);
COMMIT;
""",
            user=INTRUDER,
        )
        time.sleep(0.2)
        started = time.monotonic()
        rollback = self.pg.psql(
            self.database,
            file=CANONICAL_ROLLBACK,
            check=False,
        )
        elapsed = time.monotonic() - started
        writer_stdout, writer_stderr = writer.communicate(timeout=10)
        self.assertEqual(writer.returncode, 0, writer_stderr)
        self.assertIn("INSERT 0 1", writer_stdout)
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("nonempty", rollback.stderr)
        self.pg.psql(
            self.database,
            command=f"REVOKE nhi_rule_history_owner FROM {INTRUDER};",
        )

    def test_migrations_leave_simulated_legacy_tables_unchanged(self) -> None:
        self.pg.psql(self.database, file=PROMOTION_ROLLBACK)
        self.pg.psql(self.database, file=CANONICAL_ROLLBACK)
        self.pg.psql(
            self.database,
            command="""
CREATE SCHEMA tw_drug;
CREATE TABLE tw_drug.rule_articles (
  article_id bigint PRIMARY KEY,
  article_num text NOT NULL,
  full_text text NOT NULL
);
CREATE TABLE tw_drug.rule_article_versions (
  version_id bigint PRIMARY KEY,
  article_id bigint NOT NULL,
  effective_date date,
  full_text text NOT NULL
);
INSERT INTO tw_drug.rule_articles VALUES
  (1, '1.1', 'alpha'), (2, '1.2', 'beta');
INSERT INTO tw_drug.rule_article_versions VALUES
  (10, 1, '2020-01-01', 'old alpha'),
  (11, 1, '2021-01-01', 'alpha');
""",
        )
        fingerprint_sql = """
SELECT
  (SELECT count(*) FROM tw_drug.rule_articles),
  (SELECT md5(string_agg(
     article_id::text || '|' || article_num || '|' || full_text,
     E'\\n' ORDER BY article_id
   )) FROM tw_drug.rule_articles),
  (SELECT count(*) FROM tw_drug.rule_article_versions),
  (SELECT md5(string_agg(
     version_id::text || '|' || article_id::text || '|' ||
     coalesce(effective_date::text,'') || '|' || full_text,
     E'\\n' ORDER BY version_id
   )) FROM tw_drug.rule_article_versions);
"""
        before = self.query(fingerprint_sql)
        self.pg.psql(self.database, file=CANONICAL_FORWARD)
        self.pg.psql(self.database, file=PROMOTION_FORWARD)
        after = self.query(fingerprint_sql)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
