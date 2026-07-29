from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from nhi_rule_history.source_transcript import (
    SourceTranscriptError,
    prepare_source_transcript,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nhi-source-transcript-"
        )
        self.bundle = Path(self.temporary.name)
        self.proofread = self.bundle / "proofread.md"
        self.segments = self.bundle / "segments.jsonl"
        self.lineage = self.bundle / "lineage.md"
        self.proofread.write_text(
            """<!-- source_page: 1 -->

> 衛生署公報｜測試頁首｜一

# 通則

一、第一條來源文字。

<!-- source_page: 2 -->

> 衛生署公報｜測試頁首｜二

## 主題

（一）第二條跨頁來源文字。
""",
            encoding="utf-8",
        )
        rows = [
            {
                "source_segment_id": "84:p01:general:1",
                "source_page_start": 1,
                "source_page_end": 1,
                "section_path": ["通則"],
                "designation_raw": "一、",
                "heading_raw": "第一條",
                "exact_text": "一、第一條來源文字。",
                "substructure": [],
                "literal_deleted_marker": False,
                "uncertainties": [],
            },
            {
                "source_segment_id": "84:p02:topic:1",
                "source_page_start": 2,
                "source_page_end": 2,
                "section_path": ["主題"],
                "designation_raw": "（一）",
                "heading_raw": "第二條",
                "exact_text": "（一）第二條跨頁來源文字。",
                "substructure": [],
                "literal_deleted_marker": False,
                "uncertainties": [],
            },
        ]
        self.segments.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        self.lineage.write_text(
            """# 測試

- **`84:p01:general:1`｜84 p.1｜raw `一、`｜第一條**
  - disposition: `same_designation_text_continuity_candidate`
  - 96 designation／text span: **通則一** — 「第一條」
  - 判讀：只建立候選。
- **`84:p02:topic:1`｜84 p.2｜raw `（一）`｜第二條**
  - disposition: `absent_in_96_observation`
  - 96 designation／text span: **96 全文** — 「未見」
  - 判讀：未見不等於刪除。
""",
            encoding="utf-8",
        )
        self.write_manifest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self) -> None:
        def artifact(path: Path) -> dict[str, object]:
            return {
                "path": path.name,
                "sha256": sha256(path),
                "byte_size": path.stat().st_size,
            }

        proofread = artifact(self.proofread)
        proofread.update(
            {
                "page_count": 2,
                "unresolved_visual_reading_count": 0,
                "review_status": (
                    "agent_proofread_pending_independent_review"
                ),
            }
        )
        segments = artifact(self.segments)
        segments.update(
            {
                "segment_count": 2,
                "literal_deleted_marker_count": 0,
                "identity_status": "unadjudicated_source_segment",
                "legal_version_status": "not_claimed",
            }
        )
        lineage = artifact(self.lineage)
        lineage.update(
            {
                "target_source_edition_label": "96年7月版",
                "target_artifact_sha256": "9" * 64,
                "candidate_count": 2,
                "disposition_counts": {
                    "same_designation_text_continuity_candidate": 1,
                    "renumber_or_move_candidate": 0,
                    "absent_in_96_observation": 1,
                    "new_in_96_observation": 0,
                    "ambiguous": 0,
                },
                "recoding_hypothesis": (
                    "supported_at_source_observation_level_not_adjudicated"
                ),
            }
        )
        manifest = {
            "schema": "nhi-rule-history/source-transcript-bundle/v1",
            "captured_at": "2026-07-29T01:03:51Z",
            "source": {
                "document_number": "測試字第84號",
                "document_date_roc": "84-06-20",
                "source_edition_label": "84年測試版",
                "fint_run_id": "2fa58923-9a91-8c8a-9a8f-a4ee0010845d",
                "attachment_snapshot_id": "1" * 64,
                "attachment_sha256": "2" * 64,
                "attachment_byte_size": 100,
                "source_label": "測試.pdf",
                "source_url": (
                    "https://mohwlaw.mohw.gov.tw/Flaw/GetFile.ashx?PFID=1"
                ),
            },
            "producer": {
                "provider": "openai",
                "model_lane": "gpt-pro",
                "role": "proofreader",
                "prompt_sha256": "3" * 64,
            },
            "proofread": proofread,
            "segments": segments,
            "lineage_analysis": lineage,
            "claims": {
                "source_observation_only": True,
                "legal_identity_adjudicated": False,
                "direct_predecessor_claimed": False,
                "legal_effective_date_assigned_per_segment": False,
                "complete_history_claimed": False,
            },
        }
        (self.bundle / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_prepares_normalized_immutable_material(self) -> None:
        material = prepare_source_transcript(self.bundle)
        self.assertEqual(
            material.expected_counts,
            {
                "proofread_artifact": 1,
                "source_page": 2,
                "source_segment": 2,
                "lineage_analysis_artifact": 1,
                "lineage_candidate": 2,
            },
        )
        self.assertEqual(
            {row["source_segment_id"] for row in material.segments},
            {"84:p01:general:1", "84:p02:topic:1"},
        )
        self.assertTrue(
            all(
                row["identity_status"] == "candidate_unadjudicated"
                for row in material.lineage_candidates
            )
        )
        self.assertNotIn(
            str(self.bundle),
            json.dumps(
                material.lineage_candidates,
                ensure_ascii=False,
            ),
        )

    def test_rejects_segment_not_found_on_declared_page(self) -> None:
        rows = [
            json.loads(line)
            for line in self.segments.read_text(encoding="utf-8").splitlines()
        ]
        rows[1]["exact_text"] = "這段來源並不存在。"
        self.segments.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        self.write_manifest()
        with self.assertRaisesRegex(
            SourceTranscriptError,
            "exact text is absent",
        ):
            prepare_source_transcript(self.bundle)

    def test_rejects_legal_identity_overclaim(self) -> None:
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["claims"]["legal_identity_adjudicated"] = True
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SourceTranscriptError,
            "legal claims are unsafe",
        ):
            prepare_source_transcript(self.bundle)

    def test_rejects_lineage_candidate_coverage_gap(self) -> None:
        text = self.lineage.read_text(encoding="utf-8")
        self.lineage.write_text(
            text.replace("84:p02:topic:1", "84:p02:topic:other"),
            encoding="utf-8",
        )
        self.write_manifest()
        with self.assertRaisesRegex(
            SourceTranscriptError,
            "do not exactly cover",
        ):
            prepare_source_transcript(self.bundle)


class SourceTranscriptMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.forward = (
            root
            / "pg"
            / "migrations"
            / "2026-07-29_nhi_rule_history_source_transcript_v19.sql"
        ).read_text(encoding="utf-8")
        cls.rollback = (
            root
            / "pg"
            / "migrations"
            / "2026-07-29_nhi_rule_history_source_transcript_v19.rollback.sql"
        ).read_text(encoding="utf-8")

    def test_schema_keeps_source_observation_below_legal_identity(self) -> None:
        self.assertIn("source_observation_only", self.forward)
        self.assertIn("NOT legal_identity_adjudicated", self.forward)
        self.assertIn("NOT direct_predecessor_claimed", self.forward)
        self.assertIn("NOT complete_history_claimed", self.forward)

    def test_sealed_rows_and_truncate_are_immutable(self) -> None:
        self.assertIn("sealed transcript runs are immutable", self.forward)
        self.assertIn("transcript child rows are immutable", self.forward)
        self.assertIn("BEFORE TRUNCATE", self.forward)
        self.assertIn("transcript evidence tables cannot be truncated", self.forward)

    def test_rollback_is_schema_scoped(self) -> None:
        self.assertIn(
            "DROP SCHEMA IF EXISTS nhi_rule_history_transcript CASCADE",
            self.rollback,
        )


class SourceTranscriptPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.initdb = shutil.which("initdb")
        cls.pg_ctl = shutil.which("pg_ctl")
        cls.createdb = shutil.which("createdb")
        cls.dropdb = shutil.which("dropdb")
        cls.psql = shutil.which("psql")
        if not all(
            (
                cls.initdb,
                cls.pg_ctl,
                cls.createdb,
                cls.dropdb,
                cls.psql,
            )
        ):
            raise unittest.SkipTest(
                "disposable PostgreSQL tools are unavailable"
            )
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="nhi-source-transcript-pg-",
            dir="/tmp",
        )
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / "data"
        cls.socket_dir = cls.root / "socket"
        cls.socket_dir.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        initialized = subprocess.run(
            [
                cls.initdb,
                "-D",
                str(cls.data),
                "--auth=trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if initialized.returncode != 0:
            cls.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot initialize disposable PostgreSQL"
            )
        started = subprocess.run(
            [
                cls.pg_ctl,
                "-D",
                str(cls.data),
                "-l",
                str(cls.root / "postgres.log"),
                "-o",
                f"-F -k {cls.socket_dir} -p {cls.port}",
                "-w",
                "start",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if started.returncode != 0:
            cls.temporary.cleanup()
            raise unittest.SkipTest("cannot start disposable PostgreSQL")
        cls.running = True

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "running", False):
            subprocess.run(
                [
                    cls.pg_ctl,
                    "-D",
                    str(cls.data),
                    "-m",
                    "fast",
                    "-w",
                    "stop",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.database = "transcript_" + uuid.uuid4().hex[:12]
        result = subprocess.run(
            [
                self.createdb,
                "-h",
                str(self.socket_dir),
                "-p",
                str(self.port),
                self.database,
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        self.dsn = (
            f"postgresql:///{self.database}?host={self.socket_dir}"
            f"&port={self.port}"
        )

    def tearDown(self) -> None:
        subprocess.run(
            [
                self.dropdb,
                "-h",
                str(self.socket_dir),
                "-p",
                str(self.port),
                "--force",
                "--if-exists",
                self.database,
            ],
            check=False,
            text=True,
            capture_output=True,
        )

    def run_psql(
        self,
        *,
        command: str | None = None,
        file: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            self.psql,
            "--no-psqlrc",
            "-X",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "-h",
            str(self.socket_dir),
            "-p",
            str(self.port),
            "-d",
            self.database,
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
            self.fail(result.stderr)
        return result

    def test_forward_guards_and_rollback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        forward = (
            root
            / "pg"
            / "migrations"
            / "2026-07-29_nhi_rule_history_source_transcript_v19.sql"
        )
        rollback = (
            root
            / "pg"
            / "migrations"
            / "2026-07-29_nhi_rule_history_source_transcript_v19.rollback.sql"
        )
        self.run_psql(
            command="""
CREATE EXTENSION pgcrypto;
CREATE SCHEMA nhi_rule_history_edition;
CREATE TABLE nhi_rule_history_edition.fint_attachment_snapshot (
  run_id uuid NOT NULL,
  attachment_snapshot_id text NOT NULL,
  content_sha256 text NOT NULL,
  byte_size bigint NOT NULL,
  PRIMARY KEY (run_id, attachment_snapshot_id)
);
INSERT INTO nhi_rule_history_edition.fint_attachment_snapshot VALUES (
  '2fa58923-9a91-8c8a-9a8f-a4ee0010845d',
  '1111111111111111111111111111111111111111111111111111111111111111',
  '2222222222222222222222222222222222222222222222222222222222222222',
  100
);
"""
        )
        self.run_psql(file=forward)
        fixture = SourceTranscriptTests(
            methodName="test_prepares_normalized_immutable_material"
        )
        fixture.setUp()
        from nhi_rule_history.source_transcript import load_source_transcript

        self.addCleanup(fixture.tearDown)
        first = load_source_transcript(fixture.bundle, conninfo=self.dsn)
        second = load_source_transcript(fixture.bundle, conninfo=self.dsn)
        self.assertFalse(first["already_loaded"])
        self.assertTrue(second["already_loaded"])
        self.assertEqual(first["counts"]["source_page"], 2)
        self.assertEqual(first["counts"]["source_segment"], 2)
        self.assertEqual(first["counts"]["lineage_candidate"], 2)
        self.assertNotEqual(
            self.run_psql(
                command=(
                    "UPDATE nhi_rule_history_transcript.source_page "
                    "SET transcript_text='tamper'"
                ),
                check=False,
            ).returncode,
            0,
        )
        self.assertNotEqual(
            self.run_psql(
                command=(
                    "TRUNCATE "
                    "nhi_rule_history_transcript.source_segment CASCADE"
                ),
                check=False,
            ).returncode,
            0,
        )
        self.assertNotEqual(
            self.run_psql(
                command=(
                    "DELETE FROM "
                    "nhi_rule_history_transcript.transcript_run"
                ),
                check=False,
            ).returncode,
            0,
        )
        self.run_psql(file=rollback)
        result = self.run_psql(
            command=(
                "SELECT count(*) FROM pg_namespace "
                "WHERE nspname='nhi_rule_history_transcript'"
            )
        )
        self.assertEqual(result.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main()
