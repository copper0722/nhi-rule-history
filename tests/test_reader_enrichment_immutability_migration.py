from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
FORWARD = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_clause_reader_enrichment_immutability_v14.sql"
)
ROLLBACK = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_clause_reader_enrichment_immutability_v14.rollback.sql"
)
FULL_COUNT_FORWARD = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_clause_reader_enrichment_full_count_receipt_v15.sql"
)
FULL_COUNT_ROLLBACK = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_clause_reader_enrichment_full_count_receipt_v15.rollback.sql"
)

CHILD_TABLES = (
    "clause_semantic_tag",
    "clause_semantic_tag_atc",
    "clause_semantic_tag_icd11_lookup",
    "clause_semantic_tag_icd11_code",
    "clause_semantic_tag_icd11_private",
    "clause_semantic_tag_nhi_treatment",
    "clause_condition_marker",
    "clause_condition_expression",
    "agent_history_summary",
)


class DisposablePostgres:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nhi-reader-immutability-pg-",
            dir="/tmp",
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

        initialized = subprocess.run(
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
        if initialized.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot initialize disposable PostgreSQL: "
                + initialized.stderr
            )

        started = subprocess.run(
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
        if started.returncode != 0:
            self.temporary.cleanup()
            raise unittest.SkipTest(
                "cannot start disposable PostgreSQL: " + started.stderr
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

    def database_args(self, database: str) -> list[str]:
        return [
            "-h",
            str(self.socket_dir),
            "-p",
            str(self.port),
            "-d",
            database,
        ]

    def create_database(self, database: str) -> None:
        created = subprocess.run(
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
        if created.returncode != 0:
            raise AssertionError(created.stderr)

    def drop_database(self, database: str) -> None:
        dropped = subprocess.run(
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
        if dropped.returncode != 0:
            raise AssertionError(dropped.stderr)

    def psql(
        self,
        database: str,
        *,
        command: str | None = None,
        file: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            self.psql_bin,
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            *self.database_args(database),
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


class ReaderEnrichmentImmutabilityMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = DisposablePostgres()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def setUp(self) -> None:
        self.database = "reader_immutability_" + uuid.uuid4().hex[:12]
        self.pg.create_database(self.database)
        self.execute(self.fixture_schema())

    def tearDown(self) -> None:
        self.pg.drop_database(self.database)

    def execute(self, sql: str, *, check: bool = True):
        return self.pg.psql(self.database, command=sql, check=check)

    def apply(self, path: Path, *, check: bool = True):
        return self.pg.psql(self.database, file=path, check=check)

    @staticmethod
    def fixture_schema() -> str:
        child_ddl = "\n".join(
            f"""
CREATE TABLE nhi_rule_history_clause.{table_name} (
  enrichment_run_id uuid NOT NULL
    REFERENCES nhi_rule_history_clause.reader_enrichment_run (run_id),
  row_id integer NOT NULL,
  payload text NOT NULL,
  PRIMARY KEY (enrichment_run_id, row_id)
);"""
            for table_name in CHILD_TABLES
        )
        return f"""
CREATE SCHEMA nhi_rule_history_clause;

CREATE TABLE nhi_rule_history_clause.reader_enrichment_run (
  run_id uuid PRIMARY KEY,
  clause_import_run_id uuid NOT NULL,
  diff_run_id uuid NOT NULL,
  generator_version text NOT NULL,
  input_sha256 text NOT NULL,
  state text NOT NULL CHECK (state IN ('loading', 'sealed')),
  output_sha256 text,
  semantic_tag_count integer,
  tag_atc_count integer,
  tag_icd11_lookup_count integer,
  tag_nhi_treatment_count integer,
  condition_marker_count integer,
  condition_expression_count integer,
  summary_count integer,
  started_at timestamptz NOT NULL,
  sealed_at timestamptz,
  CONSTRAINT reader_enrichment_run_check CHECK (
    (
      state = 'loading'
      AND output_sha256 IS NULL
      AND semantic_tag_count IS NULL
      AND tag_atc_count IS NULL
      AND tag_icd11_lookup_count IS NULL
      AND tag_nhi_treatment_count IS NULL
      AND condition_marker_count IS NULL
      AND condition_expression_count IS NULL
      AND summary_count IS NULL
      AND sealed_at IS NULL
    )
    OR
    (
      state = 'sealed'
      AND output_sha256 IS NOT NULL
      AND semantic_tag_count IS NOT NULL
      AND tag_atc_count IS NOT NULL
      AND tag_icd11_lookup_count IS NOT NULL
      AND tag_nhi_treatment_count IS NOT NULL
      AND condition_marker_count IS NOT NULL
      AND condition_expression_count IS NOT NULL
      AND summary_count IS NOT NULL
      AND sealed_at IS NOT NULL
    )
  )
);

{child_ddl}
"""

    @staticmethod
    def seed_loading_run(run_id: str) -> str:
        child_inserts = "\n".join(
            f"""
INSERT INTO nhi_rule_history_clause.{table_name} (
  enrichment_run_id, row_id, payload
) VALUES ('{run_id}', 1, 'fixture');"""
            for table_name in CHILD_TABLES
        )
        return f"""
INSERT INTO nhi_rule_history_clause.reader_enrichment_run (
  run_id, clause_import_run_id, diff_run_id, generator_version,
  input_sha256, state, started_at
) VALUES (
  '{run_id}', '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002', 'fixture/v1',
  repeat('a', 64), 'loading', '2026-07-28T00:00:00+00:00'
);
{child_inserts}
UPDATE nhi_rule_history_clause.reader_enrichment_run
SET state = 'sealed',
    output_sha256 = repeat('b', 64),
    semantic_tag_count = 1,
    tag_atc_count = 1,
    tag_icd11_lookup_count = 1,
    tag_nhi_treatment_count = 1,
    condition_marker_count = 1,
    condition_expression_count = 1,
    summary_count = 1,
    sealed_at = '2026-07-28T00:01:00+00:00'
WHERE run_id = '{run_id}';
"""

    def assert_rejected(self, sql: str, message: str) -> None:
        result = self.execute(sql, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)

    def test_migration_guards_sealed_parent_and_every_child_then_rolls_back(
        self,
    ) -> None:
        self.apply(FORWARD)
        trigger_count = self.execute(
            """
SELECT count(*)
FROM pg_trigger
WHERE NOT tgisinternal
  AND tgrelid IN (
    SELECT oid
    FROM pg_class
    WHERE relnamespace =
      'nhi_rule_history_clause'::regnamespace
  );
"""
        )
        self.assertEqual(trigger_count.stdout.strip(), "21")

        run_id = str(uuid.uuid4())
        self.execute(self.seed_loading_run(run_id))

        self.assert_rejected(
            f"""
UPDATE nhi_rule_history_clause.reader_enrichment_run
SET output_sha256 = repeat('c', 64)
WHERE run_id = '{run_id}';
""",
            "sealed reader enrichment runs are immutable",
        )
        self.assert_rejected(
            f"""
DELETE FROM nhi_rule_history_clause.reader_enrichment_run
WHERE run_id = '{run_id}';
""",
            "reader enrichment runs cannot be deleted",
        )
        self.assert_rejected(
            "TRUNCATE nhi_rule_history_clause.reader_enrichment_run;",
            "cannot truncate",
        )

        for table_name in CHILD_TABLES:
            with self.subTest(table=table_name, operation="insert"):
                self.assert_rejected(
                    f"""
INSERT INTO nhi_rule_history_clause.{table_name} (
  enrichment_run_id, row_id, payload
) VALUES ('{run_id}', 2, 'mutation');
""",
                    "requires a loading parent run",
                )
            with self.subTest(table=table_name, operation="update"):
                self.assert_rejected(
                    f"""
UPDATE nhi_rule_history_clause.{table_name}
SET payload = 'mutation'
WHERE enrichment_run_id = '{run_id}' AND row_id = 1;
""",
                    "reader enrichment child rows are append-only",
                )
            with self.subTest(table=table_name, operation="delete"):
                self.assert_rejected(
                    f"""
DELETE FROM nhi_rule_history_clause.{table_name}
WHERE enrichment_run_id = '{run_id}' AND row_id = 1;
""",
                    "reader enrichment child rows are append-only",
                )
            with self.subTest(table=table_name, operation="truncate"):
                self.assert_rejected(
                    f"TRUNCATE nhi_rule_history_clause.{table_name};",
                    "reader enrichment evidence cannot be truncated",
                )

        self.apply(ROLLBACK)
        trigger_count = self.execute(
            """
SELECT count(*)
FROM pg_trigger
WHERE NOT tgisinternal
  AND tgrelid IN (
    SELECT oid
    FROM pg_class
    WHERE relnamespace =
      'nhi_rule_history_clause'::regnamespace
  );
"""
        )
        self.assertEqual(trigger_count.stdout.strip(), "0")

    def test_seal_refuses_declared_counts_that_do_not_match_children(
        self,
    ) -> None:
        self.apply(FORWARD)
        run_id = str(uuid.uuid4())
        self.execute(
            f"""
INSERT INTO nhi_rule_history_clause.reader_enrichment_run (
  run_id, clause_import_run_id, diff_run_id, generator_version,
  input_sha256, state, started_at
) VALUES (
  '{run_id}', '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002', 'fixture/v1',
  repeat('a', 64), 'loading', '2026-07-28T00:00:00+00:00'
);
"""
        )
        self.assert_rejected(
            f"""
UPDATE nhi_rule_history_clause.reader_enrichment_run
SET state = 'sealed',
    output_sha256 = repeat('b', 64),
    semantic_tag_count = 1,
    tag_atc_count = 0,
    tag_icd11_lookup_count = 0,
    tag_nhi_treatment_count = 0,
    condition_marker_count = 0,
    condition_expression_count = 0,
    summary_count = 0,
    sealed_at = '2026-07-28T00:01:00+00:00'
WHERE run_id = '{run_id}';
""",
            "seal counts do not match child rows",
        )

    def test_full_count_receipt_covers_public_and_private_icd_children(
        self,
    ) -> None:
        self.apply(FORWARD)
        self.apply(FULL_COUNT_FORWARD)
        run_id = str(uuid.uuid4())
        child_inserts = "\n".join(
            f"""
INSERT INTO nhi_rule_history_clause.{table_name} (
  enrichment_run_id, row_id, payload
) VALUES ('{run_id}', 1, 'fixture');"""
            for table_name in CHILD_TABLES
        )
        self.execute(
            f"""
INSERT INTO nhi_rule_history_clause.reader_enrichment_run (
  run_id, clause_import_run_id, diff_run_id, generator_version,
  input_sha256, state, started_at
) VALUES (
  '{run_id}', '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002', 'fixture/v15',
  repeat('a', 64), 'loading', '2026-07-28T00:00:00+00:00'
);
{child_inserts}
UPDATE nhi_rule_history_clause.reader_enrichment_run
SET state = 'sealed',
    output_sha256 = repeat('b', 64),
    semantic_tag_count = 1,
    tag_atc_count = 1,
    tag_icd11_lookup_count = 1,
    tag_icd11_code_count = 1,
    tag_icd11_private_count = 1,
    tag_nhi_treatment_count = 1,
    condition_marker_count = 1,
    condition_expression_count = 1,
    summary_count = 1,
    sealed_at = '2026-07-28T00:01:00+00:00'
WHERE run_id = '{run_id}';
"""
        )

        mismatch_run_id = str(uuid.uuid4())
        self.execute(
            f"""
INSERT INTO nhi_rule_history_clause.reader_enrichment_run (
  run_id, clause_import_run_id, diff_run_id, generator_version,
  input_sha256, state, started_at
) VALUES (
  '{mismatch_run_id}', '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002', 'fixture/v15-mismatch',
  repeat('c', 64), 'loading', '2026-07-28T00:02:00+00:00'
);
INSERT INTO nhi_rule_history_clause.clause_semantic_tag_icd11_code (
  enrichment_run_id, row_id, payload
) VALUES ('{mismatch_run_id}', 1, 'unreceipted-code-row');
"""
        )
        self.assert_rejected(
            f"""
UPDATE nhi_rule_history_clause.reader_enrichment_run
SET state = 'sealed',
    output_sha256 = repeat('d', 64),
    semantic_tag_count = 0,
    tag_atc_count = 0,
    tag_icd11_lookup_count = 0,
    tag_icd11_code_count = 0,
    tag_icd11_private_count = 0,
    tag_nhi_treatment_count = 0,
    condition_marker_count = 0,
    condition_expression_count = 0,
    summary_count = 0,
    sealed_at = '2026-07-28T00:03:00+00:00'
WHERE run_id = '{mismatch_run_id}';
""",
            "seal counts do not match child rows",
        )

        self.apply(FULL_COUNT_ROLLBACK)
        remaining_columns = self.execute(
            """
SELECT count(*)
FROM information_schema.columns
WHERE table_schema = 'nhi_rule_history_clause'
  AND table_name = 'reader_enrichment_run'
  AND column_name IN (
    'tag_icd11_code_count',
    'tag_icd11_private_count'
  );
"""
        )
        self.assertEqual(remaining_columns.stdout.strip(), "0")
        self.apply(ROLLBACK)


if __name__ == "__main__":
    unittest.main()
