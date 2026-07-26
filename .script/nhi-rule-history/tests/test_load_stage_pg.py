#!/usr/bin/env python3
"""Tests for isolated NHI rule-history stage migration + loader.

Synthetic fixtures only (no official prose). Real-corpus validation is an
optional empirical path exercised by implementation-notes dual-run receipts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import load_stage_pg as lsp  # noqa: E402
import occurrence_extract as oe  # noqa: E402


WORKTREE = Path(__file__).resolve().parents[3]
MIGRATION = (
    WORKTREE / "pg" / "migrations" / "2026-07-26_nhi_rule_history_stage.sql"
)
ROLLBACK = (
    WORKTREE
    / "pg"
    / "migrations"
    / "2026-07-26_nhi_rule_history_stage.rollback.sql"
)
REAL_STAGE = WORKTREE / ".work" / "nhi-rule-history-stage" / "grok-occurrences"
REAL_RECEIPT = (
    WORKTREE
    / "skills"
    / "model-harness"
    / "audit"
    / "2026-07-26-nhi-rule-history-rebuild-execution"
    / "grok-history-occurrences"
)
REAL_MANIFEST = (
    WORKTREE
    / "skills"
    / "model-harness"
    / "audit"
    / "2026-07-26-nhi-rule-history-rebuild-execution"
    / "grok-corpus-profile"
    / "manifest.jsonl"
)
def _resolve_real_history() -> Path | None:
    """Locate immutable history ODTs without embedding an absolute path literal."""
    candidates = []
    env = os.environ.get("NHI_RULE_HISTORY_DIR")
    if env:
        candidates.append(Path(env))
    # Sibling main checkout layout (worktree is a temp clone of _admin-private).
    candidates.append(
        Path.home()
        / "repos"
        / "_admin-private"
        / ".data"
        / "nhi-drug-payment-rules"
        / "history"
    )
    for c in candidates:
        if c.is_dir():
            return c
    return None


REAL_HISTORY = _resolve_real_history()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            fh.write("\n")


def _empty_sha() -> str:
    return _sha(b"")


def _locator_flow(doc_order: int = 0, xml_idx: int = 1) -> dict:
    return {
        "container": "flow",
        "doc_order": doc_order,
        "element": "p",
        "in_frame": 0,
        "in_index_context": 0,
        "list_depth": 0,
        "nested_table_depth": 0,
        "style_name": "P1",
        "xml_element_index": xml_idx,
    }


def _make_block(
    *,
    artifact_sha: str,
    raw_text: str,
    doc_order: int = 0,
    xml_idx: int = 1,
    block_kind: str = "paragraph",
    relative_path: str = "history/syn.odt",
) -> dict:
    loc = _locator_flow(doc_order=doc_order, xml_idx=xml_idx)
    locator_key = oe._locator_key(loc)
    raw_b = raw_text.encode("utf-8")
    return {
        "schema": lsp.SCHEMA_BLOCK,
        "block_id": oe._block_id(artifact_sha, locator_key),
        "artifact_sha256": artifact_sha,
        "relative_path": relative_path,
        "block_kind": block_kind,
        "container": "flow",
        "element_name": "p",
        "style_name": "P1",
        "in_table": False,
        "in_index_context": False,
        "xml_element_index": xml_idx,
        "locator": loc,
        "locator_key": locator_key,
        "raw_text": raw_text,
        "normalized_search_text": raw_text,
        "raw_text_sha256": _sha(raw_b),
        "raw_text_byte_length": len(raw_b),
        "raw_text_char_length": len(raw_text),
        "parser_version": lsp.BOUND_PARSER_VERSION,
    }


def _make_occ(block: dict, designation: str = "1.1") -> dict:
    # Ensure raw text starts with designation for offset gate.
    raw = f"{designation} synthetic label"
    raw_b = raw.encode("utf-8")
    loc = block["locator"]
    locator_key = block["locator_key"]
    art = block["artifact_sha256"]
    start = 0
    end = len(designation)
    return {
        "schema": lsp.SCHEMA_OCCURRENCE,
        "occurrence_id": oe._occurrence_id(art, locator_key, _sha(raw_b)),
        "artifact_sha256": art,
        "relative_path": block["relative_path"],
        "designation_text": designation,
        "block_id": block["block_id"],
        "locator": loc,
        "locator_key": locator_key,
        "raw_text": raw,
        "normalized_search_text": raw,
        "raw_text_sha256": _sha(raw_b),
        "raw_text_byte_length": len(raw_b),
        "raw_text_char_length": len(raw),
        "parser_version": lsp.BOUND_PARSER_VERSION,
        "ambiguity_flags": ["source_local_candidate_only"],
        "container": "flow",
        "match_start_in_raw": start,
        "match_end_in_raw": end,
        "statement": "source-local rule-occurrence candidate; NOT stable rule identity",
        "in_index_context": False,
    }


def _make_release(artifact_sha: str, *, blocks: int, occs: int, empty: int = 0) -> dict:
    return {
        "schema": lsp.SCHEMA_RELEASE,
        "release_id": artifact_sha,
        "relative_path": "history/syn.odt",
        "basename": "syn.odt",
        "sha256": artifact_sha,
        "byte_length": 12,
        "filename_label_raw": "syn",
        "filename_id_prefix": "syn",
        "filename_date_fragments_raw": [],
        "analysis_chronology": {
            "analysis_sort_key": "000.00",
            "legal_date_inferred": False,
            "parse_status": "synthetic",
            "roc_month": 0,
            "roc_year": 0,
            "statement": "analysis-only chronology; NOT a legal effective date",
        },
        "source_order_index": 0,
        "parser_version": lsp.BOUND_PARSER_VERSION,
        "block_count": blocks,
        "occurrence_count": occs,
        "table_count": 0,
        "row_count_xml": 0,
        "cell_count_xml": 0,
        "row_count_logical": 0,
        "cell_count_logical": 0,
        "empty_cell_count": empty,
        "nested_table_count": 0,
        "odt_repeat_attrs_present": False,
        "statement": "source-local release observation; NOT a legal effective date",
        "accepted_manifest_sha256": artifact_sha,
        "accepted_manifest_match": True,
        "xml_ph_element_count": blocks - empty,
        "xml_ph_nested_count": 0,
        "xml_ph_emitted_unique": blocks - empty,
        "xml_ph_unaccounted": 0,
        "source_structural_block_count_before_repeat_expansion": blocks - empty,
        "empty_table_cell_block_count": empty,
        "numeric_quantity_rejection_count": 0,
    }


def _make_summary(*, releases: int, blocks: int, occs: int, empty: int, issues: int) -> dict:
    return {
        "schema": lsp.SCHEMA_SUMMARY,
        "parser_version": lsp.BOUND_PARSER_VERSION,
        "deterministic": True,
        "release_count": releases,
        "expected_release_count": releases,
        "release_count_match": True,
        "all_source_sha_match_accepted_manifest": True,
        "block_count": blocks,
        "occurrence_count": occs,
        "occurrence_container_counts": {"flow": occs},
        "table_count_total": 0,
        "row_count_xml_total": 0,
        "cell_count_xml_total": 0,
        "row_count_logical_total": 0,
        "cell_count_logical_total": 0,
        "empty_cell_count_total": empty,
        "nested_table_count_total": 0,
        "xml_ph_element_count_total": blocks - empty,
        "xml_ph_nested_count_total": 0,
        "xml_ph_emitted_unique_total": blocks - empty,
        "xml_ph_unaccounted_total": 0,
        "empty_table_cell_block_count_total": empty,
        "numeric_quantity_rejection_count_total": 0,
        "numeric_quantity_rejection_by_code": {},
        "canary_hit_counts": {},
        "canary_hit_total": 0,
        "duplicate_designation_groups": [],
        "duplicate_designation_group_count": 0,
        "issue_count": issues,
        "issue_codes": {},
        "issue_severity_counts": {"info": issues},
        "source_order_release_sequence": [],
        "analysis_only_chronology_sequence": [],
        "per_release_counts": [],
        "notes": ["synthetic"],
        "canonical_rule_history_promoted": False,
        "cross_release_identity_inferred": False,
        "legal_dates_inferred": False,
        "cross_release_diffs_computed": False,
        "max_designation_first_segment": 99,
    }


class MigrationStaticTests(unittest.TestCase):
    def test_migration_tables_constraints_acl(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        # Must not bare-adopt an unrelated pre-existing schema.
        self.assertNotRegex(
            sql,
            r"(?i)CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+tw_drug_history_stage",
        )
        self.assertIn("CREATE SCHEMA tw_drug_history_stage", sql)
        self.assertIn("managed=tw_drug_history_stage/v1", sql)
        self.assertIn("refuse bare adoption", sql)
        for table in (
            "rebuild_run",
            "run_input_file",
            "source_release",
            "source_artifact",
            "release_artifact",
            "structural_block",
            "occurrence_candidate",
            "stage_issue",
        ):
            self.assertIn(f"tw_drug_history_stage.{table}", sql)
        self.assertIn("sha256_hex", sql)
        self.assertIn("^[0-9a-f]{64}$", sql)
        self.assertIn("REVOKE ALL ON SCHEMA tw_drug_history_stage FROM PUBLIC", sql)
        self.assertIn("ALTER DEFAULT PRIVILEGES IN SCHEMA tw_drug_history_stage", sql)
        self.assertIn("empty_table_cell", sql)
        self.assertIn("primary_parse_source", sql)
        # Severity contract matches producer: info|warning|error (not warn/fatal).
        self.assertIn("severity IN ('info', 'warning', 'error')", sql)
        self.assertIn("severity = 'error'", sql)
        # Full DML guard on child evidence + terminal parent guards.
        self.assertIn("guard_evidence_dml", sql)
        self.assertIn("reject_evidence_truncate", sql)
        self.assertIn("rebuild_run_update_guard", sql)
        self.assertIn("rebuild_run_delete_guard", sql)
        self.assertIn(
            "BEFORE INSERT OR UPDATE OR DELETE ON tw_drug_history_stage.%I",
            sql,
        )
        self.assertIn(
            "BEFORE TRUNCATE ON tw_drug_history_stage.%I",
            sql,
        )
        self.assertIn("'structural_block'", sql)
        self.assertIn("BEFORE UPDATE ON tw_drug_history_stage.rebuild_run", sql)
        self.assertIn("BEFORE DELETE ON tw_drug_history_stage.rebuild_run", sql)
        self.assertIn(
            "tw_drug_history_stage.drop_run_fingerprint",
            sql,
        )
        self.assertIn("loading→sealed|failed", sql)
        # Composite occurrence↔block artifact consistency.
        self.assertIn("occurrence_candidate_block_artifact_fk", sql)
        self.assertIn("structural_block_id_artifact_uidx", sql)
        self.assertIn(
            "FOREIGN KEY (run_id, block_id, artifact_sha256)",
            sql,
        )
        self.assertIn(
            "REFERENCES tw_drug_history_stage.structural_block (run_id, block_id, artifact_sha256)",
            sql,
        )
        # Legacy severity tokens must not appear as enum members.
        self.assertNotRegex(sql, r"severity\s+IN\s*\([^)]*'warn'[^)]*\)")
        self.assertNotRegex(sql, r"severity\s+IN\s*\([^)]*'fatal'[^)]*\)")
        # No legacy/canonical table creates (stage schema only).
        self.assertNotRegex(
            sql,
            r"(?i)CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+tw_drug\.",
        )
        self.assertNotRegex(
            sql,
            r"(?i)CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+tw_drug_history\.",
        )
        self.assertNotIn("tw_drug_history_read", sql)
        # Forbidden semantic columns must not be declared (CHECK text may name them).
        for banned_col in (
            "stable_rule_id",
            "predecessor_id",
            "successor_id",
            "event_effect",
            "lineage_id",
        ):
            self.assertNotRegex(
                sql,
                rf"(?i)\b{banned_col}\b\s+[a-zA-Z]",
            )
        self.assertNotRegex(
            sql,
            r"(?i)\blegal_effective_date\b\s+(text|date|timestamptz)",
        )

    def test_rollback_is_fail_closed_no_cascade(self) -> None:
        sql = ROLLBACK.read_text(encoding="utf-8")
        # Strip line comments before CASCADE audit (keep DO body executable SQL).
        code_only = re.sub(r"--.*?$", "", sql, flags=re.M)
        # CASCADE is forbidden — can drop dependents in other schemas.
        self.assertNotRegex(code_only, r"(?i)\bCASCADE\b")
        self.assertIn("DROP SCHEMA tw_drug_history_stage RESTRICT", sql)
        self.assertIn("managed=tw_drug_history_stage/v1", sql)
        # Allowlisted explicit drops only.
        for table in (
            "stage_issue",
            "occurrence_candidate",
            "structural_block",
            "release_artifact",
            "source_release",
            "source_artifact",
            "run_input_file",
            "rebuild_run",
        ):
            self.assertIn(f"DROP TABLE IF EXISTS tw_drug_history_stage.{table}", sql)
        self.assertIn(
            "DROP FUNCTION IF EXISTS tw_drug_history_stage.guard_evidence_dml()",
            sql,
        )
        self.assertIn(
            "DROP FUNCTION IF EXISTS tw_drug_history_stage.reject_evidence_truncate()",
            sql,
        )
        self.assertIn(
            "DROP FUNCTION IF EXISTS tw_drug_history_stage.rebuild_run_update_guard()",
            sql,
        )
        self.assertIn(
            "DROP FUNCTION IF EXISTS tw_drug_history_stage.rebuild_run_delete_guard()",
            sql,
        )
        self.assertIn(
            "DROP DOMAIN IF EXISTS tw_drug_history_stage.sha256_hex",
            sql,
        )
        self.assertNotIn("tw_drug.", sql.replace("tw_drug_history_stage", "X"))
        self.assertEqual(sql.count("DROP SCHEMA"), 1)

    def test_global_advisory_lock_consistent(self) -> None:
        mig = MIGRATION.read_text(encoding="utf-8")
        rb = ROLLBACK.read_text(encoding="utf-8")
        loader = Path(lsp.__file__).read_text(encoding="utf-8")
        key = lsp.STAGE_GLOBAL_LOCK_KEY
        self.assertEqual(key, "tw_drug_history_stage-global")
        for label, body in (("migration", mig), ("rollback", rb)):
            self.assertIn(
                f"hashtextextended('{key}', 0)",
                body,
                msg=f"{label} missing global lock",
            )
            # Unrelated per-file lock names must not reappear.
            self.assertNotIn("tw_drug_history_stage-migration-20260726", body)
            self.assertNotIn("tw_drug_history_stage-rollback-20260726", body)
        self.assertIn(f'STAGE_GLOBAL_LOCK_KEY = "{key}"', loader)
        self.assertIn("acquire_stage_locks", loader)
        # Count executable lock acquisitions (ignore comments).
        lock_pat = re.compile(
            rf"hashtextextended\(\s*'{re.escape(key)}'\s*,\s*0\s*\)"
        )
        for label, body in (("migration", mig), ("rollback", rb)):
            code_only = re.sub(r"--.*?$", "", body, flags=re.M)
            self.assertEqual(
                len(lock_pat.findall(code_only)),
                1,
                msg=f"{label} must acquire the global lock exactly once",
            )

    def test_migration_static_parse_balance(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        # Strip comments and dollar-quoted bodies for a lightweight balance check.
        stripped = re.sub(r"--.*?$", "", sql, flags=re.M)
        stripped = re.sub(r"\$[A-Za-z0-9_]*\$.*?\$[A-Za-z0-9_]*\$", "''", stripped, flags=re.S)
        self.assertEqual(stripped.count("("), stripped.count(")"))
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertEqual(sql.count("BEGIN;"), 1)
        self.assertEqual(sql.count("COMMIT;"), 1)
        rb = ROLLBACK.read_text(encoding="utf-8")
        self.assertIn("BEGIN;", rb)
        self.assertIn("COMMIT;", rb)
        self.assertEqual(rb.count("BEGIN;"), 1)
        self.assertEqual(rb.count("COMMIT;"), 1)


class LoaderUnitTests(unittest.TestCase):
    def test_module_does_not_import_psycopg(self) -> None:
        src = Path(lsp.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Top-level imports only — deferred import inside _import_psycopg is OK.
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("psycopg"))
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(node.module.startswith("psycopg"))
        # validate path must not call _import_psycopg
        self.assertIn("def _import_psycopg", src)
        self.assertTrue(hasattr(lsp, "validate_stage_inputs"))
        # Simulate validate without DB: apply flag required before any connect.
        with mock.patch.object(lsp, "_import_psycopg") as mocked:
            rc = lsp.main(["drop-run", "--drop-run-id", "00000000-0000-0000-0000-000000000001", "--expect-fingerprint", "a" * 64])
            self.assertEqual(rc, 2)
            mocked.assert_not_called()

    def test_apply_and_drop_require_flag(self) -> None:
        rc = lsp.main(
            [
                "apply",
                "--history-dir",
                "/nope",
                "--stage-dir",
                "/nope",
                "--receipt-dir",
                "/nope",
                "--accepted-manifest",
                "/nope",
            ]
        )
        self.assertEqual(rc, 2)

    def test_database_operation_requires_explicit_or_environment_dsn(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.resolve_operator_dsn(None)
        self.assertEqual(ctx.exception.code, "dsn_required")
        with mock.patch.dict(
            os.environ,
            {lsp.DSN_ENV_VAR: "host=example-test"},
            clear=True,
        ):
            self.assertEqual(
                lsp.resolve_operator_dsn(None),
                "host=example-test",
            )

    def test_drop_run_sets_transaction_local_fingerprint_capability(self) -> None:
        src = Path(lsp.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "'tw_drug_history_stage.drop_run_fingerprint'",
            src,
        )
        self.assertRegex(
            src,
            r"set_config\(\s*'tw_drug_history_stage\.drop_run_fingerprint',"
            r"\s*%s,\s*true\s*\)",
        )
        rc2 = lsp.main(
            [
                "drop-run",
                "--drop-run-id",
                "00000000-0000-0000-0000-000000000001",
                "--expect-fingerprint",
                "a" * 64,
            ]
        )
        self.assertEqual(rc2, 2)

    def test_sql_relations_restricted_to_stage(self) -> None:
        for rel in lsp.ALLOWED_SQL_RELATIONS:
            self.assertTrue(rel.startswith("tw_drug_history_stage."))
            self.assertNotIn("tw_drug.", rel.replace("tw_drug_history_stage", "X"))
        with self.assertRaises(lsp.StageLoadError) as ctx:
            lsp._assert_sql_relation("tw_drug.rule_articles")
        self.assertEqual(ctx.exception.code, "sql_relation_forbidden")

    def test_unknown_top_level_and_nested_keys_fail(self) -> None:
        art = "a" * 64
        block = _make_block(artifact_sha=art, raw_text="1.1 x")
        bad = dict(block)
        bad["extra_field"] = 1
        with self.assertRaises(lsp.StageLoadError) as ctx:
            lsp._validate_block_row(bad, where="t")
        self.assertEqual(ctx.exception.code, "unknown_field")

        bad_loc = dict(block)
        bad_loc["locator"] = dict(block["locator"])
        bad_loc["locator"]["unexpected"] = 1
        with self.assertRaises(lsp.StageLoadError) as ctx2:
            lsp._validate_block_row(bad_loc, where="t")
        self.assertEqual(ctx2.exception.code, "unknown_field")

    def test_forbidden_legal_identity_diff_fields_fail(self) -> None:
        art = "b" * 64
        rel = _make_release(art, blocks=1, occs=0)
        rel["legal_effective_date"] = "109/01/01"
        with self.assertRaises(lsp.StageLoadError) as ctx:
            lsp._validate_release_row(rel, where="t")
        self.assertEqual(ctx.exception.code, "unknown_field")

        chron = dict(rel["analysis_chronology"])
        chron["effective_date"] = "x"
        rel2 = _make_release(art, blocks=1, occs=0)
        rel2["analysis_chronology"] = chron
        with self.assertRaises(lsp.StageLoadError) as ctx2:
            lsp._validate_release_row(rel2, where="t")
        self.assertIn(ctx2.exception.code, {"unknown_field", "forbidden_semantic_field"})

    def test_receipt_leak_fails(self) -> None:
        with self.assertRaises(lsp.StageLoadError) as ctx:
            # synthetic banned marker (not a real filesystem path in this tree)
            lsp.receipt_is_clean({"path": "/home/example/secret"})
        self.assertEqual(ctx.exception.code, "receipt_leak")
        with self.assertRaises(lsp.StageLoadError) as ctx2:
            lsp.receipt_is_clean({"dsn": "host=example port=5432"})
        self.assertEqual(ctx2.exception.code, "receipt_leak")
        with self.assertRaises(lsp.StageLoadError) as ctx3:
            lsp.receipt_is_clean({"prose": "中" * 50})
        self.assertEqual(ctx3.exception.code, "receipt_prose_leak")

    def test_id_offset_hash_recompute(self) -> None:
        art = "c" * 64
        block = _make_block(artifact_sha=art, raw_text="hello")
        ok = lsp._validate_block_row(block, where="t")
        self.assertEqual(ok["block_id"], block["block_id"])
        bad = dict(block)
        bad["raw_text_sha256"] = "d" * 64
        with self.assertRaises(lsp.StageLoadError) as ctx:
            lsp._validate_block_row(bad, where="t")
        self.assertEqual(ctx.exception.code, "raw_text_sha_mismatch")

        # block with matching raw for occurrence
        b2 = _make_block(artifact_sha=art, raw_text="1.1 synthetic label")
        # rebuild with exact text used by _make_occ
        occ = _make_occ(b2, "1.1")
        # Align block text to occurrence for joint checks in full pipeline;
        # unit: designation slice
        occ_ok = lsp._validate_occurrence_row(
            occ, where="t", require_stage_text=True
        )
        self.assertEqual(occ_ok["designation_text"], "1.1")
        bad_off = dict(occ)
        bad_off["match_start_in_raw"] = 1
        with self.assertRaises(lsp.StageLoadError):
            lsp._validate_occurrence_row(
                bad_off, where="t", require_stage_text=True
            )

    def test_severity_vocab_aligned_with_producer(self) -> None:
        self.assertEqual(lsp.SEVERITIES, frozenset({"info", "warning", "error"}))
        self.assertEqual(lsp.BLOCKING_SEVERITIES, frozenset({"error"}))
        self.assertNotIn("warn", lsp.SEVERITIES)
        self.assertNotIn("fatal", lsp.SEVERITIES)
        ok = {
            "issue_code": "synthetic_warning",
            "severity": "warning",
            "relative_path": "history/syn.odt",
            "detail": "non-blocking warning",
            "issue_class": "test",
        }
        lsp._validate_issue_row(ok, where="t")
        bad_warn = dict(ok)
        bad_warn["severity"] = "warn"
        with self.assertRaises(lsp.StageLoadError) as ctx:
            lsp._validate_issue_row(bad_warn, where="t")
        self.assertEqual(ctx.exception.code, "invalid_severity")
        bad_fatal = dict(ok)
        bad_fatal["severity"] = "fatal"
        with self.assertRaises(lsp.StageLoadError) as ctx2:
            lsp._validate_issue_row(bad_fatal, where="t")
        self.assertEqual(ctx2.exception.code, "invalid_severity")

    def test_code_hash_binds_migration_sql(self) -> None:
        h1 = lsp.code_hash()
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", h1))
        # Must reference migration logical key, never absolute path in hash inputs
        # (code_hash only digests file bytes keyed by relative logical names).
        mig = lsp.migration_sql_path()
        self.assertTrue(mig.is_file())
        # Mutating migration bytes must change code_hash; restore after.
        original = mig.read_bytes()
        try:
            mig.write_bytes(original + b"\n-- code-hash-probe\n")
            h2 = lsp.code_hash()
            self.assertNotEqual(h1, h2)
        finally:
            mig.write_bytes(original)
        self.assertEqual(lsp.code_hash(), h1)

    def test_empty_cell_invariants(self) -> None:
        art = "e" * 64
        loc = {
            "cell_element": "table-cell",
            "cell_logical_index": 0,
            "cell_xml_index": 0,
            "col_repeat_attr": 1,
            "col_repeat_instance": 0,
            "container": "table_cell",
            "doc_order": 0,
            "element": "table-cell",
            "empty_cell": 1,
            "in_frame": 0,
            "in_index_context": 0,
            "is_header_row": 0,
            "list_depth": 0,
            "nested_table_depth": 0,
            "number_columns_spanned": 1,
            "number_rows_spanned": 1,
            "para_index_in_cell": -1,
            "row_logical_index": 0,
            "row_repeat_attr": 1,
            "row_repeat_instance": 0,
            "row_xml_index": 0,
            "style_name": "",
            "table_index": 0,
            "xml_element_index": 9,
        }
        locator_key = oe._locator_key(loc)
        row = {
            "schema": lsp.SCHEMA_BLOCK,
            "block_id": oe._block_id(art, locator_key),
            "artifact_sha256": art,
            "relative_path": "history/syn.odt",
            "block_kind": "empty_table_cell",
            "container": "table_cell",
            "element_name": "table-cell",
            "style_name": None,
            "in_table": True,
            "in_index_context": False,
            "xml_element_index": 9,
            "locator": loc,
            "locator_key": locator_key,
            "raw_text": "",
            "normalized_search_text": "",
            "raw_text_sha256": _empty_sha(),
            "raw_text_byte_length": 0,
            "raw_text_char_length": 0,
            "parser_version": lsp.BOUND_PARSER_VERSION,
        }
        lsp._validate_block_row(row, where="t")
        bad = dict(row)
        bad["raw_text"] = "x"
        bad["raw_text_byte_length"] = 1
        bad["raw_text_char_length"] = 1
        bad["raw_text_sha256"] = _sha(b"x")
        with self.assertRaises(lsp.StageLoadError):
            lsp._validate_block_row(bad, where="t")


class SyntheticCorpusValidateTests(unittest.TestCase):
    def _build_mini(self, root: Path) -> dict[str, Path]:
        history = root / "history"
        stage = root / "stage"
        receipt = root / "receipt"
        history.mkdir()
        stage.mkdir()
        receipt.mkdir()
        odt = b"PK\x03\x04synthetic"
        # minimal fake file; hash driven
        (history / "syn.odt").write_bytes(odt)
        art = _sha(odt)
        block = _make_block(artifact_sha=art, raw_text="1.1 synthetic label")
        # occurrence shares block text/hash
        occ = _make_occ(block, "1.1")
        # Re-bind block to occ text/hash/id for join
        block = dict(block)
        block["raw_text"] = occ["raw_text"]
        block["normalized_search_text"] = occ["normalized_search_text"]
        block["raw_text_sha256"] = occ["raw_text_sha256"]
        block["raw_text_byte_length"] = occ["raw_text_byte_length"]
        block["raw_text_char_length"] = occ["raw_text_char_length"]
        # occurrence_id depends on text hash; block_id on locator only — OK
        occ["block_id"] = block["block_id"]
        release = _make_release(art, blocks=1, occs=1, empty=0)
        release["byte_length"] = len(odt)
        release["accepted_manifest_sha256"] = art
        manifest_row = {
            "relative_path": "history/syn.odt",
            "basename": "syn.odt",
            "sha256": art,
            "byte_length": len(odt),
            "lane": "history",
        }
        _write_jsonl(stage / "releases.jsonl", [release])
        _write_jsonl(stage / "blocks.jsonl", [block])
        _write_jsonl(stage / "occurrences.jsonl", [occ])
        _write_jsonl(receipt / "release-index.jsonl", [release])
        tracked_occ = {
            k: v
            for k, v in occ.items()
            if k not in ("raw_text", "normalized_search_text")
        }
        _write_jsonl(receipt / "occurrence-index.jsonl", [tracked_occ])
        issues = [
            {
                "issue_code": "synthetic_info",
                "severity": "info",
                "relative_path": "history/syn.odt",
                "detail": "synthetic non-blocking issue",
                "issue_class": "test",
            }
        ]
        _write_jsonl(receipt / "issues.jsonl", issues)
        summary = _make_summary(releases=1, blocks=1, occs=1, empty=0, issues=1)
        (receipt / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        _write_jsonl(receipt / "canary-occurrences.jsonl", [])
        (receipt / "quality-report.md").write_text("# synthetic\n", encoding="utf-8")
        man_path = root / "manifest.jsonl"
        _write_jsonl(man_path, [manifest_row])
        return {
            "history": history,
            "stage": stage,
            "receipt": receipt,
            "manifest": man_path,
        }

    def test_synthetic_validate_and_tamper_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            # successful validate without bound gate
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            fp1 = material["sealed_fingerprint"]
            material2 = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            self.assertEqual(fp1, material2["sealed_fingerprint"])
            r1 = json.dumps(material["receipt"], sort_keys=True, separators=(",", ":"))
            r2 = json.dumps(material2["receipt"], sort_keys=True, separators=(",", ":"))
            self.assertEqual(r1, r2)
            lsp.receipt_is_clean(material["receipt"])

            # tampered source sha
            (paths["history"] / "syn.odt").write_bytes(b"PK\x03\x04tampered!!")
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.validate_stage_inputs(
                    history_dir=paths["history"],
                    stage_dir=paths["stage"],
                    receipt_dir=paths["receipt"],
                    accepted_manifest=paths["manifest"],
                    require_bound_gate=False,
                )
            self.assertEqual(ctx.exception.code, "source_sha_mismatch")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            # tampered block row hash/length
            blocks = list(lsp.stream_jsonl(paths["stage"] / "blocks.jsonl"))
            row = dict(blocks[0][1])
            row["raw_text_byte_length"] = 999
            _write_jsonl(paths["stage"] / "blocks.jsonl", [row])
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.validate_stage_inputs(
                    history_dir=paths["history"],
                    stage_dir=paths["stage"],
                    receipt_dir=paths["receipt"],
                    accepted_manifest=paths["manifest"],
                    require_bound_gate=False,
                )
            self.assertEqual(ctx.exception.code, "raw_byte_length_mismatch")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            # duplicate block row
            blocks = [blocks_row[1] for blocks_row in lsp.stream_jsonl(paths["stage"] / "blocks.jsonl")]
            _write_jsonl(paths["stage"] / "blocks.jsonl", blocks + blocks)
            # also fix summary/release counts so we hit duplicate gate
            rel = list(lsp.stream_jsonl(paths["stage"] / "releases.jsonl"))[0][1]
            rel = dict(rel)
            rel["block_count"] = 2
            _write_jsonl(paths["stage"] / "releases.jsonl", [rel])
            shutil.copy(
                paths["stage"] / "releases.jsonl",
                paths["receipt"] / "release-index.jsonl",
            )
            summary = json.loads((paths["receipt"] / "summary.json").read_text())
            summary["block_count"] = 2
            (paths["receipt"] / "summary.json").write_text(
                json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
            )
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.validate_stage_inputs(
                    history_dir=paths["history"],
                    stage_dir=paths["stage"],
                    receipt_dir=paths["receipt"],
                    accepted_manifest=paths["manifest"],
                    require_bound_gate=False,
                )
            self.assertEqual(ctx.exception.code, "duplicate_block_id")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            # occurrence block relation broken
            occs = [r[1] for r in lsp.stream_jsonl(paths["stage"] / "occurrences.jsonl")]
            occ = dict(occs[0])
            occ["block_id"] = "f" * 64
            # recompute occurrence_id? still fails missing block
            occ["occurrence_id"] = oe._occurrence_id(
                occ["artifact_sha256"], occ["locator_key"], occ["raw_text_sha256"]
            )
            _write_jsonl(paths["stage"] / "occurrences.jsonl", [occ])
            tracked = {
                k: v
                for k, v in occ.items()
                if k not in ("raw_text", "normalized_search_text")
            }
            _write_jsonl(paths["receipt"] / "occurrence-index.jsonl", [tracked])
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.validate_stage_inputs(
                    history_dir=paths["history"],
                    stage_dir=paths["stage"],
                    receipt_dir=paths["receipt"],
                    accepted_manifest=paths["manifest"],
                    require_bound_gate=False,
                )
            self.assertEqual(ctx.exception.code, "occurrence_missing_block")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            # tampered locator key
            blocks = [r[1] for r in lsp.stream_jsonl(paths["stage"] / "blocks.jsonl")]
            b = dict(blocks[0])
            b["locator_key"] = "tampered"
            # also break block_id consistency intentionally
            _write_jsonl(paths["stage"] / "blocks.jsonl", [b])
            with self.assertRaises(lsp.StageLoadError) as ctx:
                lsp.validate_stage_inputs(
                    history_dir=paths["history"],
                    stage_dir=paths["stage"],
                    receipt_dir=paths["receipt"],
                    accepted_manifest=paths["manifest"],
                    require_bound_gate=False,
                )
            self.assertIn(
                ctx.exception.code,
                {"locator_key_mismatch", "block_id_mismatch"},
            )

    def test_warning_severity_accepted_in_synthetic_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_mini(root)
            issues = [
                {
                    "issue_code": "synthetic_warning",
                    "severity": "warning",
                    "relative_path": "history/syn.odt",
                    "detail": "non-blocking warning regression",
                    "issue_class": "test",
                }
            ]
            _write_jsonl(paths["receipt"] / "issues.jsonl", issues)
            summary = json.loads((paths["receipt"] / "summary.json").read_text())
            summary["issue_severity_counts"] = {"warning": 1}
            (paths["receipt"] / "summary.json").write_text(
                json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            self.assertEqual(material["counts"]["blocking_issue_count"], 0)
            self.assertEqual(material["counts"]["issue_count"], 1)


class _FakeResult:
    def __init__(self, rows: list[tuple] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self._i = 0

    def fetchone(self) -> tuple | None:
        if self._i >= len(self._rows):
            return None
        row = self._rows[self._i]
        self._i += 1
        return row

    def fetchall(self) -> list[tuple]:
        rows = self._rows[self._i :]
        self._i = len(self._rows)
        return rows


class _FakeCursor:
    """Cursor-like object with executemany; Connection deliberately has none."""

    def __init__(self, store: dict) -> None:
        self.store = store
        self.rowcount = 0
        self._last: _FakeResult | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple | list | None = None) -> "_FakeCursor":
        params = tuple(params or ())
        s = " ".join(sql.split()).lower()
        self.rowcount = 0
        if "pg_advisory_xact_lock" in s:
            # Global (hashtextextended) and fingerprint locks both no-op.
            self._last = _FakeResult([])
            return self
        if (
            "from tw_drug_history_stage.rebuild_run" in s
            and "where sealed_fingerprint" in s
            and "select run_id" in s
        ):
            # already-loaded probe by sealed fingerprint
            run = self.store.get("rebuild_run") or {}
            if run.get("sealed_fingerprint") == (params[0] if params else None):
                self._last = _FakeResult(
                    [
                        (
                            run.get("run_id"),
                            run.get("state"),
                            run.get("sealed_fingerprint"),
                            run.get("input_fingerprint"),
                            run.get("code_hash"),
                            run.get("output_fingerprint"),
                        )
                    ]
                )
            else:
                self._last = _FakeResult([])
            return self
        if (
            "from tw_drug_history_stage.run_input_file" in s
            and "logical_name" in s
            and "order by logical_name" in s
        ):
            rows = sorted(
                self.store.get("run_input_file", []),
                key=lambda r: r["logical_name"],
            )
            self._last = _FakeResult(
                [(r["logical_name"], r["content_sha256"], r["row_count"]) for r in rows]
            )
            return self
        if s.strip().startswith("insert into tw_drug_history_stage.rebuild_run"):
            self.store["rebuild_run"] = {
                "run_id": params[0],
                "state": "loading",
                "parser_version": params[1],
                "loader_version": params[2],
                "contract_version": params[3],
                "code_hash": params[4],
                "input_fingerprint": params[5],
                "accepted_manifest_sha256": params[6],
                "expected_counts": params[7],
                "expected_release_count": params[8],
                "expected_block_count": params[9],
                "expected_occurrence_count": params[10],
            }
            self.rowcount = 1
            self._last = _FakeResult([], rowcount=1)
            return self
        if s.strip().startswith("update tw_drug_history_stage.rebuild_run"):
            if self.store.get("rebuild_run", {}).get("state") != "loading":
                self.rowcount = 0
                self._last = _FakeResult([], rowcount=0)
                return self
            run = self.store["rebuild_run"]
            run["state"] = "sealed"
            run["sealed_fingerprint"] = params[0]
            run["output_fingerprint"] = params[1]
            run["verified_counts"] = params[2]
            self.rowcount = 1
            self._last = _FakeResult([], rowcount=1)
            return self
        if "select count(*)" in s:
            if "empty_table_cell" in s:
                n = sum(
                    1
                    for r in self.store.get("structural_block", [])
                    if r.get("block_kind") == "empty_table_cell"
                )
                self._last = _FakeResult([(n,)])
                return self
            if "is_blocking is true" in s:
                n = sum(1 for r in self.store.get("stage_issue", []) if r.get("is_blocking"))
                self._last = _FakeResult([(n,)])
                return self
            table = None
            for name in (
                "source_release",
                "source_artifact",
                "structural_block",
                "occurrence_candidate",
                "stage_issue",
                "run_input_file",
                "release_artifact",
            ):
                if f"tw_drug_history_stage.{name}" in s:
                    table = name
                    break
            n = len(self.store.get(table or "", []))
            if table == "rebuild_run" or "rebuild_run" in s and table is None:
                n = 1 if self.store.get("rebuild_run") else 0
            self._last = _FakeResult([(n,)])
            return self
        if "select state, sealed_fingerprint" in s:
            run = self.store.get("rebuild_run") or {}
            if not run:
                self._last = _FakeResult([])
                return self
            self._last = _FakeResult(
                [
                    (
                        run.get("state"),
                        run.get("sealed_fingerprint"),
                        run.get("input_fingerprint"),
                        run.get("code_hash"),
                        run.get("output_fingerprint"),
                        run.get("verified_counts"),
                        run.get("expected_release_count"),
                        run.get("expected_block_count"),
                        run.get("expected_occurrence_count"),
                    )
                ]
            )
            return self
        if "select source_row_sha256 from tw_drug_history_stage.source_release" in s:
            rows = sorted(
                self.store.get("source_release", []),
                key=lambda r: r["source_order_index"],
            )
            self._last = _FakeResult([(r["source_row_sha256"],) for r in rows])
            return self
        if "select source_row_sha256 from tw_drug_history_stage.source_artifact" in s:
            rows = sorted(
                self.store.get("source_artifact", []),
                key=lambda r: r["relative_locator"],
            )
            self._last = _FakeResult([(r["source_row_sha256"],) for r in rows])
            return self
        if "select source_row_sha256 from tw_drug_history_stage.stage_issue" in s:
            rows = sorted(
                self.store.get("stage_issue", []),
                key=lambda r: r["issue_seq"],
            )
            self._last = _FakeResult([(r["source_row_sha256"],) for r in rows])
            return self
        if "select source_row_sha256 from tw_drug_history_stage.structural_block" in s:
            rows = self.store.get("structural_block", [])
            self._last = _FakeResult([(r["source_row_sha256"],) for r in rows])
            return self
        if "select source_row_sha256 from tw_drug_history_stage.occurrence_candidate" in s:
            rows = self.store.get("occurrence_candidate", [])
            self._last = _FakeResult([(r["source_row_sha256"],) for r in rows])
            return self
        # Default empty
        self._last = _FakeResult([])
        return self

    def executemany(self, sql: str, params_seq: list) -> "_FakeCursor":
        s = " ".join(sql.split()).lower()
        rows = list(params_seq)
        self.rowcount = len(rows)
        if "run_input_file" in s:
            self.store.setdefault("run_input_file", []).extend(
                [
                    {
                        "logical_name": p[1],
                        "content_sha256": p[5],
                        "row_count": p[4],
                    }
                    for p in rows
                ]
            )
        elif "source_artifact" in s and "release_artifact" not in s:
            self.store.setdefault("source_artifact", []).extend(
                [
                    {
                        "artifact_sha256": p[1],
                        "relative_locator": p[2],
                        "source_row_sha256": p[7],
                    }
                    for p in rows
                ]
            )
        elif "source_release" in s:
            self.store.setdefault("source_release", []).extend(
                [
                    {
                        "release_id": p[1],
                        "source_order_index": p[2],
                        "source_row_sha256": p[32],
                    }
                    for p in rows
                ]
            )
        elif "release_artifact" in s:
            self.store.setdefault("release_artifact", []).extend(rows)
        elif "structural_block" in s:
            self.store.setdefault("structural_block", []).extend(
                [
                    {
                        "block_id": p[1],
                        "block_kind": p[4],
                        "source_row_sha256": p[20],
                    }
                    for p in rows
                ]
            )
        elif "occurrence_candidate" in s:
            self.store.setdefault("occurrence_candidate", []).extend(
                [
                    {
                        "occurrence_id": p[1],
                        "source_row_sha256": p[16],
                    }
                    for p in rows
                ]
            )
        elif "stage_issue" in s:
            self.store.setdefault("stage_issue", []).extend(
                [
                    {
                        "issue_seq": p[1],
                        "is_blocking": p[5],
                        "source_row_sha256": p[11],
                    }
                    for p in rows
                ]
            )
        else:
            raise AssertionError(f"unexpected executemany sql: {sql[:80]}")
        self._last = _FakeResult([], rowcount=len(rows))
        return self

    def fetchone(self) -> tuple | None:
        assert self._last is not None
        return self._last.fetchone()

    def fetchall(self) -> list[tuple]:
        assert self._last is not None
        return self._last.fetchall()


class _FakeConnection:
    """psycopg3-like connection: has cursor(), deliberately NO executemany."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.committed = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.store)

    def commit(self) -> None:
        self.committed = True

    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("apply path must use cursor, not conn.execute for batches")


class _FakePsycopg:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def connect(self, dsn: str) -> _FakeConnection:
        assert dsn  # never log
        # post_commit opens a second connection — share store via same object
        return self._conn


class ApplyPathRegressionTests(unittest.TestCase):
    def test_apply_uses_cursor_executemany_not_connection(self) -> None:
        """psycopg3 Connection has no executemany; cursor must be used."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builder = SyntheticCorpusValidateTests()
            paths = builder._build_mini(root)
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            conn = _FakeConnection()
            self.assertFalse(hasattr(conn, "executemany"))
            fake_psycopg = _FakePsycopg(conn)

            def _import() -> _FakePsycopg:
                return fake_psycopg

            with mock.patch.object(lsp, "_import_psycopg", _import):
                receipt = lsp.apply_stage(material, dsn="host=example-test")
            self.assertEqual(receipt["status"], "loaded")
            self.assertTrue(conn.committed)
            self.assertEqual(conn.store["rebuild_run"]["state"], "sealed")
            self.assertEqual(len(conn.store.get("structural_block", [])), 1)
            self.assertEqual(len(conn.store.get("occurrence_candidate", [])), 1)
            # Cursor must have been used (executemany path populated store).
            self.assertGreaterEqual(len(conn.store.get("run_input_file", [])), 1)

    def test_tamper_between_validate_and_apply_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builder = SyntheticCorpusValidateTests()
            paths = builder._build_mini(root)
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            # TOCTOU: replace blocks after validation, before apply stream.
            (paths["stage"] / "blocks.jsonl").write_text(
                '{"schema":"tampered"}\n', encoding="utf-8"
            )
            conn = _FakeConnection()
            fake_psycopg = _FakePsycopg(conn)

            with mock.patch.object(lsp, "_import_psycopg", lambda: fake_psycopg):
                with self.assertRaises(lsp.StageLoadError) as ctx:
                    lsp.apply_stage(material, dsn="host=example-test")
            self.assertIn(
                ctx.exception.code,
                {
                    "apply_input_sha_mismatch",
                    "schema_mismatch",
                    "jsonl_invalid_json",
                    "unknown_field",
                },
            )
            # Must not report loaded / must not seal.
            self.assertNotEqual(conn.store.get("rebuild_run", {}).get("state"), "sealed")
            self.assertFalse(conn.committed)

    def test_already_loaded_rejects_non_sealed_or_corrupt(self) -> None:
        """already_loaded must verify sealed data; never trust inputs alone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builder = SyntheticCorpusValidateTests()
            paths = builder._build_mini(root)
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            sealed_fp = material["sealed_fingerprint"]

            # Case A: fingerprint row exists but state is not sealed → fail closed.
            conn_a = _FakeConnection()
            conn_a.store["rebuild_run"] = {
                "run_id": "00000000-0000-0000-0000-0000000000aa",
                "state": "loading",
                "sealed_fingerprint": sealed_fp,
                "output_fingerprint": sealed_fp,
                "input_fingerprint": material["input_fingerprint"],
                "code_hash": material["code_hash"],
                "expected_release_count": material["counts"]["release_count"],
                "expected_block_count": material["counts"]["block_count"],
                "expected_occurrence_count": material["counts"]["occurrence_count"],
            }
            conn_a.store["run_input_file"] = [
                {
                    "logical_name": r["logical_name"],
                    "content_sha256": r["content_sha256"],
                    "row_count": r["row_count"],
                }
                for r in material["input_files"]
            ]
            with mock.patch.object(lsp, "_import_psycopg", lambda: _FakePsycopg(conn_a)):
                with self.assertRaises(lsp.StageLoadError) as ctx_a:
                    lsp.apply_stage(material, dsn="host=example-test")
            self.assertEqual(ctx_a.exception.code, "already_loaded_not_sealed")

            # Case B: sealed but child counts/fingerprints incomplete → fail closed.
            conn_b = _FakeConnection()
            conn_b.store["rebuild_run"] = {
                "run_id": "00000000-0000-0000-0000-0000000000bb",
                "state": "sealed",
                "sealed_fingerprint": sealed_fp,
                "output_fingerprint": sealed_fp,
                "input_fingerprint": material["input_fingerprint"],
                "code_hash": material["code_hash"],
                "expected_release_count": material["counts"]["release_count"],
                "expected_block_count": material["counts"]["block_count"],
                "expected_occurrence_count": material["counts"]["occurrence_count"],
            }
            conn_b.store["run_input_file"] = [
                {
                    "logical_name": r["logical_name"],
                    "content_sha256": r["content_sha256"],
                    "row_count": r["row_count"],
                }
                for r in material["input_files"]
            ]
            # Deliberately omit child evidence rows → count mismatch.
            with mock.patch.object(lsp, "_import_psycopg", lambda: _FakePsycopg(conn_b)):
                with self.assertRaises(lsp.StageLoadError) as ctx_b:
                    lsp.apply_stage(material, dsn="host=example-test")
            self.assertTrue(
                ctx_b.exception.code.startswith("already_loaded_"),
                msg=ctx_b.exception.code,
            )
            self.assertNotEqual(ctx_b.exception.code, "already_loaded")

    def test_already_loaded_success_requires_full_sealed_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builder = SyntheticCorpusValidateTests()
            paths = builder._build_mini(root)
            material = lsp.validate_stage_inputs(
                history_dir=paths["history"],
                stage_dir=paths["stage"],
                receipt_dir=paths["receipt"],
                accepted_manifest=paths["manifest"],
                require_bound_gate=False,
            )
            # First apply seeds a real sealed run via the normal path.
            conn = _FakeConnection()
            with mock.patch.object(lsp, "_import_psycopg", lambda: _FakePsycopg(conn)):
                first = lsp.apply_stage(material, dsn="host=example-test")
            self.assertEqual(first["status"], "loaded")
            # Second apply must return already_loaded only after full verify.
            with mock.patch.object(lsp, "_import_psycopg", lambda: _FakePsycopg(conn)):
                second = lsp.apply_stage(material, dsn="host=example-test")
            self.assertEqual(second["status"], "already_loaded")
            self.assertEqual(second["run_id"], first["run_id"])


@unittest.skipUnless(
    REAL_STAGE.is_dir()
    and REAL_RECEIPT.is_dir()
    and REAL_MANIFEST.is_file()
    and REAL_HISTORY is not None
    and REAL_HISTORY.is_dir(),
    "real accepted corpus not available in this environment",
)
class RealCorpusValidateTests(unittest.TestCase):
    def test_real_validate_bound_counts_and_deterministic(self) -> None:
        assert REAL_HISTORY is not None
        m1 = lsp.validate_stage_inputs(
            history_dir=REAL_HISTORY,
            stage_dir=REAL_STAGE,
            receipt_dir=REAL_RECEIPT,
            accepted_manifest=REAL_MANIFEST,
            require_bound_gate=True,
        )
        m2 = lsp.validate_stage_inputs(
            history_dir=REAL_HISTORY,
            stage_dir=REAL_STAGE,
            receipt_dir=REAL_RECEIPT,
            accepted_manifest=REAL_MANIFEST,
            require_bound_gate=True,
        )
        self.assertEqual(m1["sealed_fingerprint"], m2["sealed_fingerprint"])
        self.assertEqual(
            json.dumps(m1["receipt"], sort_keys=True, separators=(",", ":")),
            json.dumps(m2["receipt"], sort_keys=True, separators=(",", ":")),
        )
        counts = m1["counts"]
        self.assertEqual(counts["release_count"], 14)
        self.assertEqual(counts["block_count"], 213512)
        self.assertEqual(counts["empty_table_cell_block_count"], 79195)
        self.assertEqual(counts["occurrence_count"], 9303)
        self.assertEqual(counts["xml_ph_emitted_unique_total"], 134317)
        self.assertEqual(counts["xml_ph_unaccounted_total"], 0)
        self.assertEqual(counts["blocking_issue_count"], 0)
        lsp.receipt_is_clean(m1["receipt"])


if __name__ == "__main__":
    unittest.main()
