from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from nhi_rule_history.contracts import canonical_json_bytes, file_sha256
from nhi_rule_history.discovery.fint_keyword_crawler import (
    FintKeywordCrawler,
    Seed,
)
from nhi_rule_history.pg.fint_crawl import load_fint_crawl
from tests.test_fint_keyword_crawler import FakeClient
from tests.test_fint_keyword_crawler import detail_html
from nhi_rule_history.fetch.http import HttpResponse
from tests.test_update_queue_recovery_v2 import DisposablePostgres


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "pg" / "migrations"
EDITION = MIGRATIONS / "2026-07-28_nhi_rule_history_edition_v1.sql"
FORWARD = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.sql"
)
ROLLBACK = (
    MIGRATIONS
    / "2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.rollback.sql"
)
RUN_ID = "70000000-0000-0000-0000-000000000001"


def refresh_manifest_hashes(run_dir: Path) -> None:
    path = run_dir / "fint-crawl-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        source = run_dir / entry["filename"]
        entry["byte_size"] = source.stat().st_size
        entry["sha256"] = file_sha256(source)
    path.write_bytes(canonical_json_bytes(manifest))


class FintCrawlPgStaticTests(unittest.TestCase):
    def test_contract_is_append_only_and_keeps_match_separate_from_identity(
        self,
    ) -> None:
        sql = FORWARD.read_text(encoding="utf-8")
        self.assertIn("fint_document_number_identity", sql)
        self.assertIn("fint_query_document_match", sql)
        self.assertIn("fint_document_snapshot", sql)
        self.assertIn("record_text text NOT NULL", sql)
        self.assertIn("FINT crawl evidence is append-only", sql)
        self.assertIn("keyword match is discovery evidence", sql)
        self.assertNotIn("DROP SCHEMA", sql)

    def test_rollback_is_scoped_to_fint_objects(self) -> None:
        sql = ROLLBACK.read_text(encoding="utf-8")
        self.assertIn("DROP TABLE IF EXISTS", sql)
        self.assertIn("fint_crawl_run", sql)
        self.assertNotIn("DROP SCHEMA", sql)


class FintCrawlPgLiveTests(unittest.TestCase):
    def test_loader_rejects_query_match_parity_tamper(self) -> None:
        pg = DisposablePostgres()
        try:
            pg.psql(file=EDITION)
            pg.psql(file=FORWARD)
            with tempfile.TemporaryDirectory() as temporary:
                run_dir = Path(temporary)
                crawler = FintKeywordCrawler(
                    run_dir,
                    client=FakeClient(),
                    min_interval_seconds=0,
                )
                crawler.crawl(
                    [Seed("CAPD", "fixture", "general-rule/0.4")]
                )
                crawler.close()
                query_path = run_dir / "fint-queries.jsonl"
                query = json.loads(query_path.read_text().strip())
                query["expected_rows"] = 3
                query["fetched_rows"] = 3
                query_path.write_bytes(canonical_json_bytes(query))
                refresh_manifest_hashes(run_dir)
                with psycopg.connect(pg.dsn) as connection:
                    with self.assertRaisesRegex(
                        Exception,
                        "RowNo set is not contiguous",
                    ):
                        load_fint_crawl(connection, run_dir)
        finally:
            pg.close()

    def test_verified_crawler_projection_loads_and_is_idempotent(self) -> None:
        pg = DisposablePostgres()
        try:
            pg.psql(file=EDITION)
            pg.psql(file=FORWARD)
            with tempfile.TemporaryDirectory() as temporary:
                run_dir = Path(temporary)
                crawler = FintKeywordCrawler(
                    run_dir,
                    client=FakeClient(),
                    min_interval_seconds=0,
                )
                crawler.crawl(
                    [Seed("CAPD", "fixture", "general-rule/0.4")]
                )
                crawler.close()
                with psycopg.connect(pg.dsn) as connection:
                    first = load_fint_crawl(connection, run_dir)
                    second = load_fint_crawl(connection, run_dir)
                self.assertEqual(first["status"], "sealed")
                self.assertEqual(second["status"], "already_sealed")
                self.assertEqual(first["counts"]["documents"], 2)
                stored = pg.psql(
                    command="""
SELECT count(*) || ':' || min(length(record_text))
FROM nhi_rule_history_edition.fint_document_snapshot;
"""
                ).stdout.strip()
            self.assertRegex(stored, r"^2:[1-9][0-9]*$")
            with tempfile.TemporaryDirectory() as temporary:
                rerun_dir = Path(temporary)

                class ChangedDetailClient(FakeClient):
                    def get(self, url: str) -> HttpResponse:
                        if urlsplit(url).path.endswith("FINTQRY04.aspx"):
                            self.calls.append(url)
                            return HttpResponse(
                                request_url=url,
                                final_url=url,
                                status_code=200,
                                headers={
                                    "content-type": (
                                        "text/html; charset=utf-8"
                                    )
                                },
                                body=detail_html(
                                    "健保醫 字第 0900016259 號"
                                ).replace(
                                    "CAPD".encode(),
                                    "CAPD updated".encode(),
                                ),
                            )
                        return super().get(url)

                crawler = FintKeywordCrawler(
                    rerun_dir,
                    client=ChangedDetailClient(),
                    min_interval_seconds=0,
                )
                crawler.crawl(
                    [Seed("CAPD", "fixture", "general-rule/0.4")]
                )
                crawler.close()
                with psycopg.connect(pg.dsn) as connection:
                    rerun = load_fint_crawl(connection, rerun_dir)
                self.assertEqual(rerun["status"], "sealed")
            run_count = pg.psql(
                command=(
                    "SELECT count(*) FROM "
                    "nhi_rule_history_edition.fint_crawl_run;"
                )
            ).stdout.strip()
            self.assertEqual(run_count, "2")
        finally:
            pg.close()

    def test_load_seal_and_post_seal_insert_guard(self) -> None:
        pg = DisposablePostgres()
        try:
            pg.psql(file=EDITION)
            pg.psql(file=FORWARD)
            with tempfile.TemporaryDirectory() as temporary:
                run_dir = Path(temporary)
                crawler = FintKeywordCrawler(
                    run_dir,
                    client=FakeClient(),
                    min_interval_seconds=0,
                )
                crawler.crawl(
                    [Seed("CAPD", "fixture", "general-rule/0.4")]
                )
                crawler.close()
                with psycopg.connect(pg.dsn) as connection:
                    loaded = load_fint_crawl(connection, run_dir)
            run_id = loaded["run_id"]
            with self.assertRaises(AssertionError):
                pg.psql(
                    command=f"""
INSERT INTO nhi_rule_history_edition.fint_query_seed VALUES (
  '{run_id}', repeat('e',64), repeat('d',64),
  '["late"]'::jsonb, 'fixture', 'late'
);
"""
                )
            with self.assertRaises(AssertionError):
                pg.psql(
                    command=(
                        "TRUNCATE "
                        "nhi_rule_history_edition.fint_crawl_issue;"
                    )
                )
            with self.assertRaises(AssertionError):
                pg.psql(file=ROLLBACK)
        finally:
            pg.close()

    def test_forward_reapply_and_empty_rollback(self) -> None:
        pg = DisposablePostgres()
        try:
            pg.psql(file=EDITION)
            pg.psql(file=FORWARD)
            pg.psql(file=FORWARD)
            pg.psql(file=ROLLBACK)
        finally:
            pg.close()


if __name__ == "__main__":
    unittest.main()
