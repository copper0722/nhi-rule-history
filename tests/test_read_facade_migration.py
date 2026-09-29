"""The read-only facade schema nhi_rules (v28) on a disposable cluster.

The disposable database is built from the project's own forward migrations,
which include v28.  The facade is checked empty, then over the small seeded
publication and announced release the overlay-release tests use, then rolled
back and applied again.  Nothing here touches a production database.
"""

from __future__ import annotations

import hashlib
import re
import unittest
import uuid
from pathlib import Path

import psycopg

from tests import test_announced_release as announced
from tests import test_update_queue_recovery_v2 as recovery_fixture

ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "pg" / "migrations" / "2026-09-29_nhi_rule_history_read_facade_v28.sql"
ROLLBACK = (
    ROOT / "pg" / "migrations"
    / "2026-09-29_nhi_rule_history_read_facade_v28.rollback.sql"
)
READBACK = ROOT / "database" / "queries" / "read-facade-readback-v28.sql"

VIEWS = frozenset(
    {
        "announced_composed_clause",
        "announced_notice_effect",
        "announced_patch",
        "current_clause",
        "current_clause_block",
        "current_clause_date",
        "current_source_file",
        "general_principle_change",
        "general_principle_edition",
        "general_principle_version",
        "notice",
        "release_state",
        "view_catalog",
    }
)
STORE_SCHEMAS = frozenset(
    {
        "nhi_rule_history_announced",
        "nhi_rule_history_clause",
        "nhi_rule_history_edition",
        "nhi_rule_history_publication",
        "nhi_rule_history_update_ops",
        "nhi_rule_history_update_queue",
    }
)
# Public repository: no private host, address or path may appear in the files.
PRIVATE_MARKERS = re.compile(
    r"\b[a-z][a-z0-9-]*:\d{4,5}\b"  # host:port
    r"|(?<!['\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.'])"  # unquoted IPv4 (clause codes are quoted)
    r"|/home/|/data/|\.ts\.net\b",  # private paths, tailnet names
    re.IGNORECASE,
)


def _rows(dsn: str, sql: str, params=()) -> list[tuple]:
    with psycopg.connect(dsn) as connection:
        connection.execute("SET default_transaction_read_only = on")
        return [tuple(row) for row in connection.execute(sql, params).fetchall()]


def _relations(dsn: str, *, exclude_schema: str) -> set[tuple[str, str]]:
    return {
        (schema, name)
        for schema, name in _rows(
            dsn,
            """
            SELECT n.nspname, c.relname
            FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname <> %s
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname !~ '^pg_toast'
            """,
            (exclude_schema,),
        )
    }


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# One general-principles clause with two text states across two editions; the
# older diff run says "replaced", the latest sealed diff run says "added" while
# the store recorded the hunk as "replaced".
GP_OLD = "四、注射藥品之使用原則：舊"
GP_NEW = "四、注射藥品之使用原則：新"
COMPOSED_TEXT = "9.8.Fixture composed：(115/9/1)\n限用於甲。"


def _seed_facade_rows(dsn: str) -> None:
    """Rows for the views the overlay-release fixture leaves empty."""

    run_pub = announced.PUBLICATION_RUN
    run_ann = announced.BASE_RUN
    run_edition, run_clause = str(uuid.uuid4()), str(uuid.uuid4())
    diff_old, diff_new = str(uuid.uuid4()), str(uuid.uuid4())
    obs_doc = str(uuid.uuid4())
    feed_obs, job = str(uuid.uuid4()), str(uuid.uuid4())
    item_url = "https://www.nhi.gov.tw/ch/cp-1-1-3258-1.html"  # BASE notice
    with psycopg.connect(dsn) as c:
        c.execute("SET session_replication_role = replica")
        run_sql = (
            "INSERT INTO nhi_rule_history_{schema}.import_run (run_id, {extra}"
            "source_set_sha256, extractor_version, diff_version, state, "
            "{counts}output_sha256, started_at, sealed_at) VALUES "
            "(%s, {marks}%s, 'fixture', 'fixture', 'sealed', {vals}%s, "
            "'2026-07-28T09:00:00Z', '2026-07-28T09:47:43Z')"
        )
        c.execute(
            run_sql.format(schema="edition", extra="", counts="source_stage_refs, row_counts, ",
                           marks="", vals="'{}'::jsonb, '{}'::jsonb, "),
            (run_edition, "1" * 64, "2" * 64),
        )
        c.execute(
            run_sql.format(schema="clause", extra="edition_import_run_id, ", counts="row_counts, ",
                           marks="%s, ", vals="'{}'::jsonb, "),
            (run_clause, run_edition, "3" * 64, "4" * 64),
        )
        # publication: blocks and dates of the seeded clause 9.9
        for order, text in enumerate(("9.9.Fixture：(100/1/1)", "限用於乙。")):
            c.execute(
                "INSERT INTO nhi_rule_history_publication.current_clause_block "
                "(run_id, clause_code, block_order, source_block_id, block_kind, "
                "container, raw_text, raw_text_sha256, source_locator, source_row_sha256) "
                "VALUES (%s,'9.9',%s,%s,'paragraph','flow',%s,%s,'{\"k\":1}',%s)",
                (run_pub, order, f"blk-{order}", text, _h(text), "e" * 64),
            )
        c.execute(
            "INSERT INTO nhi_rule_history_publication.current_clause_date "
            "(run_id, clause_code, date_value, raw_expressions, occurrence_count, "
            "source_row_sha256) VALUES (%s,'9.9','2011-01-01','[\"100/1/1\"]',1,%s)",
            (run_pub, "e" * 64),
        )
        # edition store: one rule, two editions, two source documents
        c.execute(
            "INSERT INTO nhi_rule_history_edition.rule (rule_id, canonical_slug, display_label, "
            "source_designation_raw, navigation_code, navigation_code_origin, identity_status) "
            "VALUES ('rule:general-principles','general-principles','通則','通則',"
            "'chapter:00','project_assigned','active')"
        )
        for order, (label, text) in enumerate((("96年7月版", GP_OLD), ("97年9月版", GP_NEW))):
            c.execute(
                "INSERT INTO nhi_rule_history_edition.source_document (document_id, "
                "first_import_run_id, source_kind, official_label, source_page_url, "
                "official_url, artifact_sha256, media_type, byte_length, source_stage_schema, "
                "source_stage_run_id, source_locator, observed_at) VALUES (%s,%s,'annual_full',%s,"
                "'https://example.test/page',%s,%s,'application/vnd.oasis.opendocument.text',10,"
                "'tw_drug_history_stage',%s,'{\"k\":1}','2026-07-27T08:00:00Z')",
                (f"document:{order}", run_edition, label,
                 f"https://example.test/{order}.odt", _h(label), obs_doc),
            )
            c.execute(
                "INSERT INTO nhi_rule_history_edition.rule_version (version_id, rule_id, "
                "primary_document_id, first_import_run_id, chronology_order, version_label, "
                "raw_text, normalized_text, structured_json, raw_sha256, normalized_sha256, "
                "source_locator, extractor_version, validation_status, legal_effective_status) "
                "VALUES (%s,'rule:general-principles',%s,%s,%s,%s,%s,%s,'{}',%s,%s,'{\"k\":1}',"
                "'fixture','verified_source_snapshot','not_claimed')",
                (f"version:{order}", f"document:{order}", run_edition, order, label,
                 text, text, _h(text), _h(text)),
            )
        # clause store: chapter, clause 0.4, two versions, observations, a date,
        # one edge with one hunk, two sealed diff runs with different presentation
        c.execute(
            "INSERT INTO nhi_rule_history_clause.chapter (chapter_id, first_import_run_id, "
            "display_label, source_designation_raw, navigation_code, navigation_code_origin) "
            "VALUES ('chapter:general-principles',%s,'通則','通則','chapter:00','project_assigned')",
            (run_clause,),
        )
        c.execute(
            "INSERT INTO nhi_rule_history_clause.clause (clause_id, chapter_id, first_import_run_id, "
            "canonical_code, ordinal_number, code_origin, identity_basis, identity_status) "
            "VALUES ('clause:0.4','chapter:general-principles',%s,'0.4',4,'project_assigned',"
            "'fixture','verified_within_declared_edition_set')",
            (run_clause,),
        )
        for order, (label, text) in enumerate((("96年7月版", GP_OLD), ("97年9月版", GP_NEW))):
            c.execute(
                "INSERT INTO nhi_rule_history_clause.clause_version (clause_version_id, clause_id, "
                "first_import_run_id, state_order, display_title, representative_raw_text, "
                "normalized_text, structured_json, representative_raw_sha256, normalized_sha256, "
                "comparison_sha256, extractor_version, legal_effective_status) VALUES "
                "(%s,'clause:0.4',%s,%s,'注射藥品之使用原則',%s,%s,'{}',%s,%s,%s,'fixture','not_claimed')",
                (f"cv{order}", run_clause, order, text, text, _h(text), _h(text), _h("c" + text)),
            )
            c.execute(
                "INSERT INTO nhi_rule_history_clause.clause_version_observation (observation_id, "
                "clause_id, clause_version_id, source_edition_version_id, first_import_run_id, "
                "chronology_order, edition_label, source_designation_raw, source_order_start, "
                "source_order_end, raw_text, normalized_text, raw_sha256, normalized_sha256, "
                "source_locator) VALUES (%s,'clause:0.4',%s,%s,%s,%s,%s,'四、',0,0,%s,%s,%s,%s,'{\"k\":1}')",
                (f"obs{order}", f"cv{order}", f"version:{order}", run_clause, order, label,
                 text, text, _h(text), _h(text)),
            )
        c.execute(
            "INSERT INTO nhi_rule_history_clause.clause_version_date (date_fact_id, clause_version_id, "
            "representative_observation_id, date_role, raw_value, calendar_system, date_value, "
            "date_precision, basis, legal_effective_status, source_locator) VALUES "
            "('d0','cv1','obs1','text_amendment_annotation','97/9/1','ROC','2008-09-01','day',"
            "'fixture','candidate_unresolved','{\"k\":1}')"
        )
        c.execute(
            "INSERT INTO nhi_rule_history_clause.clause_version_edge (edge_id, clause_id, "
            "older_clause_version_id, newer_clause_version_id, adjacency_basis, "
            "legal_predecessor_status, crosses_known_gap, older_last_observed_order, "
            "newer_first_observed_order, algorithm_version, input_sha256, output_sha256, "
            "change_hunk_count, status) VALUES ('e0','clause:0.4','cv0','cv1',"
            "'adjacent_distinct_text_state_across_official_editions','not_claimed',false,0,1,"
            "'fixture',%s,%s,1,'verified_source_edition_diff')",
            ("5" * 64, "6" * 64),
        )
        c.execute(
            "INSERT INTO nhi_rule_history_clause.clause_diff_hunk (hunk_id, edge_id, hunk_order, "
            "change_kind, context_label, old_text, new_text, old_text_sha256, new_text_sha256, "
            "inline_segments, display_note) VALUES ('h0','e0',0,'replaced','0.4',%s,%s,%s,%s,'[]','')",
            (GP_OLD, GP_NEW, _h(GP_OLD), _h(GP_NEW)),
        )
        for number, (run, sealed, kind) in enumerate(
            ((diff_old, "2026-07-28T11:42:30Z", "replaced"),
             (diff_new, "2026-07-28T14:33:06Z", "added"))
        ):
            c.execute(
                "INSERT INTO nhi_rule_history_clause.diff_run (run_id, clause_import_run_id, "
                "algorithm_version, ignored_change_policy, state, input_sha256, output_sha256, "
                "hunk_count, started_at, sealed_at) VALUES (%s,%s,%s,'[\"whitespace\"]',"
                "'sealed',%s,%s,1,'2026-07-28T10:00:00Z',%s)",
                (run, run_clause, f"fixture/{number}", "7" * 64, "8" * 64, sealed),
            )
            c.execute(
                "INSERT INTO nhi_rule_history_clause.diff_hunk_presentation (diff_run_id, hunk_id, "
                "semantic_change_kind, inline_segments, ignored_change_classes, display_note) "
                "VALUES (%s,'h0',%s,'[]','[]','')",
                (run, kind),
            )
        # announced: a reviewed composite (patch, effect, resolution, composed text)
        composite_effect, composite_patch = str(uuid.uuid4()), str(uuid.uuid4())
        c.execute(
            "INSERT INTO nhi_rule_history_announced.notice_effect (run_id, effect_id, notice_id, "
            "effect_type, clause_code, projection_status, scope_note, source_row_sha256) "
            "VALUES (%s,%s,%s,'clause_amendment','9.8','projected_source_exact_patch','fixture',%s)",
            (run_ann, composite_effect, announced.BASE_NOTICE, "e" * 64),
        )
        patch_text = "9.8.Fixture patch：(115/9/1)"
        c.execute(
            "INSERT INTO nhi_rule_history_announced.clause_patch (run_id, patch_id, effect_id, "
            "clause_code, predecessor_text_sha256, effective_from, effective_until, resolution_state, "
            "source_exact_patch_text, source_exact_patch_sha256, omitted_text_present, "
            "composition_status, comparison_sha256, component_manifest_sha256, "
            "partial_event_projection, unprocessed_event_scope, public_note, source_row_sha256) "
            "VALUES (%s,%s,%s,'9.8',%s,'2026-09-01',NULL,'verified_scheduled',%s,%s,false,"
            "'reviewed_composite',%s,%s,false,'[]','fixture composite',%s)",
            (run_ann, composite_patch, composite_effect, _h(""), patch_text, _h(patch_text),
             "1" * 64, "2" * 64, "e" * 64),
        )
        c.execute(
            "INSERT INTO nhi_rule_history_announced.patch_resolution_event (run_id, patch_id, "
            "resolution_state, reason, evidence) VALUES (%s,%s,'verified_scheduled','fixture','{}')",
            (run_ann, composite_patch),
        )
        c.execute(
            "INSERT INTO nhi_rule_history_announced.composed_clause_version (run_id, version_id, "
            "patch_id, clause_code, effective_from, predecessor_publication_run_id, "
            "predecessor_text_sha256, predecessor_source_artifact_sha256, composition_rule_version, "
            "composition_manifest_sha256, composed_text, composed_text_sha256, amendment_block_count, "
            "inherited_block_count, review_status, public_note, source_row_sha256) VALUES "
            "(%s,%s,%s,'9.8','2026-09-01',%s,%s,%s,'fixture',%s,%s,%s,1,1,"
            "'deterministic_owner_directed','fixture composite',%s)",
            (run_ann, str(uuid.uuid4()), composite_patch, run_pub, "9" * 64, "d" * 64,
             "a" * 64, COMPOSED_TEXT, _h(COMPOSED_TEXT), "e" * 64),
        )
        # notices: one at the announced notice's address, one ignored as non-rule
        c.execute(
            "INSERT INTO nhi_rule_history_update_ops.feed_observation (feed_observation_id, job_id, "
            "url_observation_id, response_artifact_sha256, parser_version, parse_status, "
            "channel_title_raw, item_count, item_sequence_sha256, parsed_at) VALUES "
            "(%s,%s,%s,%s,'fixture-rss/1','parsed','fixture',2,%s,'2026-09-29T09:28:57Z')",
            (feed_obs, job, str(uuid.uuid4()), "a" * 64, "b" * 64),
        )
        for index, (title, link, state) in enumerate((
            ("公告修訂9.9.給付規定。", item_url, "corpus_registered"),
            ("公告非給付規定事項", "https://www.nhi.gov.tw/ch/cp-2-2-3258-1.html", "ignored_non_rule"),
        )):
            c.execute(
                "INSERT INTO nhi_rule_history_update_ops.feed_item_observation (feed_observation_id, "
                "item_index, item_fingerprint, guid_raw, title_raw, link_raw, published_raw, "
                "description_raw, raw_item_sha256) VALUES (%s,%s,%s,%s,%s,%s,"
                "'Tue, 15 Sep 2026 00:00:00 +0800','fixture',%s)",
                (feed_obs, index, _h(title), f"guid-{index}", title, link, _h(link)),
            )
            work_item = str(uuid.uuid4())
            c.execute(
                "INSERT INTO nhi_rule_history_update_queue.rss_work_item (work_item_id, "
                "rss_identity_fingerprint, item_identity_kind, item_identity_value, source_feed_url, "
                "guid_raw, first_feed_observation_id, first_item_index, first_item_fingerprint, "
                "first_title_raw, first_link_raw, first_observed_at) VALUES "
                "(%s,%s,'rss_guid',%s,'https://www.nhi.gov.tw/ch/rss-3258-1.xml',%s,%s,%s,%s,%s,%s,"
                "'2026-09-16T10:00:00Z')",
                (work_item, _h(f"id{index}"), f"guid-{index}", f"guid-{index}", feed_obs, index,
                 _h(title), title, link),
            )
            c.execute(
                "INSERT INTO nhi_rule_history_update_queue.work_item_transition (work_item_id, "
                "transition_seq, transition_id, from_state, to_state, actor_kind, evidence_sha256, "
                "evidence_json, source_job_id, recorded_at) VALUES "
                "(%s,1,%s,NULL,%s,'fixture',%s,'{\"k\":1}',%s,'2026-09-16T10:00:00Z')",
                (work_item, str(uuid.uuid4()), state, _h(state), job),
            )
        c.commit()


class ReadFacadeLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pg = recovery_fixture.DisposablePostgres()
        announced._apply_migrations(cls.pg)
        # Empty-database behaviour is recorded before any row is seeded.
        cls.empty_counts = {
            view: _rows(cls.pg.dsn, f"SELECT count(*) FROM nhi_rules.{view}")[0][0]
            for view in sorted(VIEWS)
            if _rows(
                cls.pg.dsn,
                "SELECT to_regclass(%s) IS NOT NULL",
                (f"nhi_rules.{view}",),
            )[0][0]
        }
        cls.empty_release_state = _rows(
            cls.pg.dsn,
            "SELECT layer, run_id, state FROM nhi_rules.release_state ORDER BY layer",
        )
        announced._seed(cls.pg.dsn)
        _seed_facade_rows(cls.pg.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pg.close()

    def test_forward_migration_created_exactly_the_thirteen_views(self) -> None:
        relations = _rows(
            self.pg.dsn,
            """
            SELECT c.relname, c.relkind::text
            FROM pg_class AS c
            WHERE c.relnamespace = 'nhi_rules'::regnamespace
            """,
        )
        self.assertEqual({name for name, _ in relations}, VIEWS)
        self.assertEqual({kind for _, kind in relations}, {"v"})
        self.assertEqual(
            _rows(
                self.pg.dsn,
                "SELECT count(*) FROM pg_proc WHERE pronamespace = 'nhi_rules'::regnamespace",
            )[0][0],
            0,
        )

    def test_every_view_runs_on_an_empty_database(self) -> None:
        self.assertEqual(set(self.empty_counts), VIEWS)
        expected_rows = {"release_state": 4, "view_catalog": 13}
        for view, count in self.empty_counts.items():
            self.assertEqual(count, expected_rows.get(view, 0), view)
        # A layer with no run shows a row with NULL, not a missing row.
        self.assertEqual(
            self.empty_release_state,
            [
                ("announced", None, None),
                ("current_text", None, None),
                ("general_principles", None, None),
                ("notices", None, None),
            ],
        )

    def test_views_are_not_writable_and_carry_invoker_rights(self) -> None:
        rows = _rows(
            self.pg.dsn,
            """
            SELECT i.table_name, i.is_updatable, i.is_insertable_into,
                   i.is_trigger_updatable, i.is_trigger_deletable,
                   i.is_trigger_insertable_into,
                   c.reloptions @> ARRAY['security_invoker=true'],
                   NOT EXISTS (
                     SELECT 1 FROM aclexplode(c.relacl) AS a WHERE a.grantee <> c.relowner
                   )
            FROM information_schema.views AS i
            JOIN pg_class AS c
              ON c.relnamespace = 'nhi_rules'::regnamespace
             AND c.relname = i.table_name
            WHERE i.table_schema = 'nhi_rules'
            """,
        )
        self.assertEqual({row[0] for row in rows}, VIEWS)
        for row in rows:
            self.assertEqual(row[1:6], ("NO",) * 5, row[0])
            self.assertTrue(row[6], f"{row[0]} lacks security_invoker")
            self.assertTrue(row[7], f"{row[0]} is granted to someone other than its owner")
        self.assertTrue(
            _rows(
                self.pg.dsn,
                "SELECT coalesce(nspacl::text !~ '(^|,)=', true)"
                " FROM pg_namespace WHERE nspname = 'nhi_rules'",
            )[0][0]
        )
        with psycopg.connect(self.pg.dsn) as connection:
            for statement in (
                "INSERT INTO nhi_rules.current_clause (clause_code) VALUES ('1.1')",
                "UPDATE nhi_rules.current_clause SET clause_text = 'x'",
                "DELETE FROM nhi_rules.announced_patch",
            ):
                with self.assertRaises(psycopg.Error, msg=statement):
                    connection.execute(statement)
                connection.rollback()

    def test_seeded_rows_come_through_unchanged(self) -> None:
        clauses = _rows(
            self.pg.dsn,
            "SELECT clause_code, clause_text, clause_text_sha256 FROM nhi_rules.current_clause",
        )
        self.assertEqual({code for code, _, _ in clauses}, set(announced.SERVED))
        for code, text, digest in clauses:
            self.assertEqual(text, announced.SERVED[code])
            self.assertEqual(digest, announced.sha256_text(announced.SERVED[code]))
        patches = _rows(
            self.pg.dsn,
            """
            SELECT clause_code, effective_from::text, current_resolution_state,
                   composition_status, notice_reference, base_text_matches_current,
                   patch_text_sha256, announced_run_id::text
            FROM nhi_rules.announced_patch WHERE clause_code = '9.9'
            """,
        )
        self.assertEqual(
            patches,
            [
                (
                    "9.9",
                    "2026-09-01",
                    "reconciled",
                    "patch_only",
                    announced.BASE_REFERENCE,
                    True,
                    announced.sha256_text("9.9.Fixture：(115/9/1)"),
                    announced.BASE_RUN,
                )
            ],
        )
        self.assertEqual(
            _rows(self.pg.dsn, "SELECT count(*) FROM nhi_rules.announced_notice_effect")[0][0],
            2,
        )
        state = {
            layer: (run_id, state)
            for layer, run_id, state in _rows(
                self.pg.dsn,
                "SELECT layer, run_id::text, state FROM nhi_rules.release_state",
            )
        }
        self.assertEqual(state["current_text"], (announced.PUBLICATION_RUN, "sealed"))
        self.assertEqual(state["announced"], (announced.BASE_RUN, "sealed"))
        self.assertEqual(state["general_principles"][1], "sealed")
        self.assertIsNotNone(state["general_principles"][0])

    def test_general_principle_chain_reads_the_latest_sealed_presentation(self) -> None:
        versions = _rows(
            self.pg.dsn,
            """
            SELECT clause_code, version_no, is_latest_version, version_text,
                   first_seen_edition, last_seen_edition, edition_count,
                   text_annotation_dates::text, legal_effective_status
            FROM nhi_rules.general_principle_version ORDER BY version_no
            """,
        )
        self.assertEqual(
            versions,
            [
                ("0.4", 0, False, GP_OLD, "96年7月版", "96年7月版", 1, None, "not_claimed"),
                ("0.4", 1, True, GP_NEW, "97年9月版", "97年9月版", 1, "{2008-09-01}", "not_claimed"),
            ],
        )
        changes = _rows(
            self.pg.dsn,
            """
            SELECT clause_code, from_version_no, to_version_no, changed_in_edition,
                   hunk_order, change_kind, source_change_kind, old_text, new_text,
                   legal_predecessor_status
            FROM nhi_rules.general_principle_change
            """,
        )
        # The store recorded the hunk as replaced; the latest sealed diff run
        # presents it as added (the older sealed run says replaced).
        self.assertEqual(
            changes,
            [("0.4", 0, 1, "97年9月版", 0, "added", "replaced", GP_OLD, GP_NEW, "not_claimed")],
        )
        editions = _rows(
            self.pg.dsn,
            "SELECT edition_order, edition_label, official_url FROM nhi_rules.general_principle_edition ORDER BY edition_order",
        )
        self.assertEqual(
            editions,
            [(0, "96年7月版", "https://example.test/0.odt"), (1, "97年9月版", "https://example.test/1.odt")],
        )

    def test_blocks_dates_composed_clause_and_notices_come_through(self) -> None:
        blocks = _rows(
            self.pg.dsn,
            "SELECT clause_code, block_order, block_text, block_text_sha256 FROM nhi_rules.current_clause_block ORDER BY block_order",
        )
        self.assertEqual(
            [(code, order, text) for code, order, text, _ in blocks],
            [("9.9", 0, "9.9.Fixture：(100/1/1)"), ("9.9", 1, "限用於乙。")],
        )
        for _, _, text, digest in blocks:
            self.assertEqual(digest, _h(text))
        self.assertEqual(
            _rows(self.pg.dsn, "SELECT clause_code, date_value::text, occurrence_count FROM nhi_rules.current_clause_date"),
            [("9.9", "2011-01-01", 1)],
        )
        self.assertEqual(
            _rows(
                self.pg.dsn,
                "SELECT clause_code, composed_text, composed_text_sha256, review_status FROM nhi_rules.announced_composed_clause",
            ),
            [("9.8", COMPOSED_TEXT, _h(COMPOSED_TEXT), "deterministic_owner_directed")],
        )
        patches = dict(
            _rows(
                self.pg.dsn,
                "SELECT clause_code, base_text_matches_current::text FROM nhi_rules.announced_patch",
            )
        )
        # 9.9 is anchored on the seeded text; 9.8 is a new clause (no publication text).
        self.assertEqual(patches, {"9.9": "true", "9.8": None})
        # An ignored-as-non-rule notice is not shown; the shown one is structured in the release.
        self.assertEqual(
            _rows(
                self.pg.dsn,
                "SELECT title, official_url, in_announced_release, announced_reference FROM nhi_rules.notice",
            ),
            [
                (
                    "公告修訂9.9.給付規定。",
                    "https://www.nhi.gov.tw/ch/cp-1-1-3258-1.html",
                    True,
                    announced.BASE_REFERENCE,
                )
            ],
        )
        self.assertEqual(
            _rows(self.pg.dsn, "SELECT count(*) FROM nhi_rules.current_source_file")[0][0],
            1,
        )

    def test_invoker_rights_deny_a_role_without_access_to_the_store(self) -> None:
        with psycopg.connect(self.pg.dsn, autocommit=True) as connection:
            connection.execute("CREATE ROLE facade_probe NOLOGIN")
            try:
                connection.execute("GRANT USAGE ON SCHEMA nhi_rules TO facade_probe")
                connection.execute(
                    "GRANT SELECT ON nhi_rules.current_clause, nhi_rules.announced_patch TO facade_probe"
                )
                connection.execute("SET ROLE facade_probe")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute("SELECT count(*) FROM nhi_rules.current_clause")
                connection.execute("RESET ROLE")
                # Control: with access to the store the same query works, so the
                # denial above came from the invoker's rights, not from the view.
                connection.execute(
                    "GRANT USAGE ON SCHEMA nhi_rule_history_publication TO facade_probe"
                )
                connection.execute(
                    "GRANT SELECT ON nhi_rule_history_publication.v_current_clause TO facade_probe"
                )
                connection.execute("SET ROLE facade_probe")
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM nhi_rules.current_clause").fetchone()[0],
                    len(announced.SERVED),
                )
                # A view whose store the role still cannot read stays closed.
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute("SELECT count(*) FROM nhi_rules.announced_patch")
            finally:
                connection.execute("RESET ROLE")
                connection.execute("DROP OWNED BY facade_probe")
                connection.execute("DROP ROLE facade_probe")

    def test_map_names_each_view_its_question_and_the_stores_it_reads(self) -> None:
        catalog = {
            name: (answers, tuple(schemas), tuple(relations))
            for name, answers, schemas, relations in _rows(
                self.pg.dsn,
                "SELECT view_name, answers, reads_schemas, reads_relations FROM nhi_rules.view_catalog",
            )
        }
        self.assertEqual(set(catalog), VIEWS)
        for name, (answers, schemas, relations) in catalog.items():
            self.assertTrue(answers and len(answers) > 40, name)
            if name != "view_catalog":
                self.assertTrue(schemas and relations, name)
            self.assertNotIn(name, {relation.split(".")[1] for relation in relations})
        for name in VIEWS - {"release_state", "view_catalog"}:
            self.assertTrue(set(catalog[name][1]) <= STORE_SCHEMAS, name)
        self.assertEqual(catalog["current_clause"][1], ("nhi_rule_history_publication",))
        self.assertEqual(catalog["announced_patch"][1], (
            "nhi_rule_history_announced", "nhi_rule_history_publication",
        ))
        self.assertEqual(
            catalog["general_principle_version"][1],
            ("nhi_rule_history_clause",),
        )
        # System catalogs are pinned objects: the database records no
        # dependency on them, so the map reports no store relation for itself.
        self.assertEqual(catalog["view_catalog"][1:], ((), ()))

    def test_readback_file_reports_ok_on_the_seeded_database(self) -> None:
        result = self.pg.psql(file=READBACK)
        sections: dict[str, list[str]] = {}
        current = ""
        for line in result.stdout.splitlines():
            marker = re.match(r"== (\d+)\.", line)
            if marker:
                current = marker.group(1)
                sections[current] = []
            elif current and line.strip() and line not in ("BEGIN", "ROLLBACK"):
                sections[current].append(line)
        self.assertEqual(set(sections), {str(number) for number in range(1, 9)})
        for number in ("1", "2", "3", "4"):
            self.assertTrue(sections[number], number)
            for line in sections[number]:
                self.assertTrue(line.endswith("|t"), f"section {number}: {line}")
        self.assertEqual(len(sections["6"]), 5)
        self.assertEqual(len(sections["8"]), 13)

    def test_rollback_drops_only_the_schema_and_forward_applies_again(self) -> None:
        before = _relations(self.pg.dsn, exclude_schema="nhi_rules")
        self.pg.psql(file=ROLLBACK)
        self.assertEqual(
            _rows(self.pg.dsn, "SELECT count(*) FROM pg_namespace WHERE nspname = 'nhi_rules'")[0][0],
            0,
        )
        self.assertEqual(_relations(self.pg.dsn, exclude_schema="nhi_rules"), before)
        # Rolling back twice is harmless; the base data is untouched.
        self.pg.psql(file=ROLLBACK)
        self.assertEqual(
            _rows(
                self.pg.dsn,
                "SELECT count(*) FROM nhi_rule_history_publication.current_clause",
            )[0][0],
            len(announced.SERVED),
        )
        self.pg.psql(file=FORWARD)
        self.assertEqual(
            {
                name
                for (name,) in _rows(
                    self.pg.dsn,
                    "SELECT relname FROM pg_class WHERE relnamespace = 'nhi_rules'::regnamespace",
                )
            },
            VIEWS,
        )
        self.assertEqual(_relations(self.pg.dsn, exclude_schema="nhi_rules"), before)

    def test_rollback_refuses_to_drop_what_it_did_not_create(self) -> None:
        self.pg.psql(command="CREATE TABLE nhi_rules.stray (id integer)")
        try:
            refused = self.pg.psql(file=ROLLBACK, check=False)
            self.assertNotEqual(refused.returncode, 0)
            # The failed rollback changed nothing: the views are still there.
            self.assertEqual(
                _rows(
                    self.pg.dsn,
                    "SELECT count(*) FROM pg_class WHERE relnamespace = 'nhi_rules'::regnamespace AND relkind = 'v'",
                )[0][0],
                len(VIEWS),
            )
        finally:
            self.pg.psql(command="DROP TABLE nhi_rules.stray")


class PublicFilesTest(unittest.TestCase):
    def test_files_carry_no_private_host_or_path(self) -> None:
        for path in (FORWARD, ROLLBACK, READBACK):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(
                    PRIVATE_MARKERS.search(line), f"{path.name}:{number}: {line}"
                )

    def test_forward_file_creates_only_views_and_the_schema(self) -> None:
        text = FORWARD.read_text(encoding="utf-8")
        statements = re.findall(
            r"^(CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\s+(\w+)",
            text,
            re.MULTILINE,
        )
        allowed = {
            ("CREATE", "SCHEMA"),
            ("CREATE", "VIEW"),
            ("REVOKE", "ALL"),
        }
        self.assertLessEqual(set(statements), allowed)
        self.assertEqual(statements.count(("CREATE", "VIEW")), len(VIEWS))
        self.assertEqual(text.count("security_invoker = true"), len(VIEWS))


if __name__ == "__main__":
    unittest.main()
