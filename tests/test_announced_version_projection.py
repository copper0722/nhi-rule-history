from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from nhi_rule_history.announced_dyslipidemia import (
    DOCUMENT_COMPONENT_MIGRATION,
    VERSION_PROJECTION_MIGRATION,
    _adjacent_diff_rows,
    _document_structure_blueprint,
    _diff_projection_rows,
    _exact_inline_diff_segments,
    _outline_text_shape,
    _table_row_alignment,
    _terminology_projection_rows,
)
from nhi_rule_history.terminology import normalize_alias


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source(
    raw_text: str,
    *,
    container: str,
    render_locator: dict[str, object],
    source_locator: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "source_block_id": f"fixture:{render_locator!r}:{raw_text}",
        "source_artifact_sha256": _sha("fixture-artifact"),
        "container": container,
        "render_locator": render_locator,
        "source_locator": source_locator or {},
        "raw_text": raw_text,
        "raw_text_sha256": _sha(raw_text),
    }


class AnnouncedVersionProjectionTest(unittest.TestCase):
    def test_clause_document_conserves_blocks_and_normalizes_tables(
        self,
    ) -> None:
        sources = [
            _source(
                "2.6.1",
                container="flow",
                render_locator={"section_role": "clause_heading"},
            ),
            _source(
                "成分",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 0,
                    "cell_index": 0,
                },
            ),
            _source(
                "代碼",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 0,
                    "cell_index": 1,
                },
            ),
            _source(
                "atorvastatin",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 1,
                    "cell_index": 0,
                },
            ),
            _source(
                "A",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 1,
                    "cell_index": 1,
                },
            ),
            _source(
                "B",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 2,
                    "cell_index": 1,
                },
            ),
            _source(
                "附註",
                container="flow",
                render_locator={"section_role": "note"},
            ),
        ]
        blueprint = _document_structure_blueprint(
            sources, clause_code="2.6.1"
        )
        self.assertTrue(blueprint["has_table"])
        self.assertEqual(blueprint["table_count"], 1)
        self.assertEqual(
            [
                order
                for component in blueprint["components"]
                for order in component["block_orders"]
            ],
            list(range(len(sources))),
        )
        table = blueprint["components"][1]["table"]
        self.assertEqual(table["renderer_profile"], "product_code_directory_v1")
        self.assertEqual((table["row_count"], table["column_count"]), (3, 2))
        carried = next(
            cell
            for cell in table["cells"]
            if (cell["row_index"], cell["cell_index"]) == (2, 0)
        )
        self.assertEqual(carried["physical_state"], "physically_omitted")
        self.assertEqual(
            carried["logical_value_state"],
            "policy_carried_from_origin",
        )
        self.assertIsNone(carried["physical_text"])
        self.assertIsNone(carried["physical_text_sha256"])
        self.assertEqual(carried["logical_value_text"], "atorvastatin")
        self.assertEqual(
            (
                carried["value_origin_row_index"],
                carried["value_origin_cell_index"],
            ),
            (1, 0),
        )
        self.assertIsNotNone(carried["carry_policy_receipt_sha256"])

    def test_source_rowspan_is_explicit_covered_cell(self) -> None:
        sources = [
            _source(
                "2.6.1",
                container="flow",
                render_locator={"section_role": "clause_heading"},
            ),
            _source(
                "處方規定",
                container="table_cell",
                render_locator={
                    "table_index": 2,
                    "table_role": "table2_ldl_thresholds",
                    "row_index": 0,
                    "cell_index": 0,
                },
                source_locator={
                    "cell_element": "table-cell",
                    "number_rows_spanned": 2,
                },
            ),
            _source(
                "",
                container="table_cell",
                render_locator={
                    "table_index": 2,
                    "table_role": "table2_ldl_thresholds",
                    "row_index": 1,
                    "cell_index": 0,
                },
                source_locator={
                    "cell_element": "covered-table-cell",
                },
            ),
        ]
        table = _document_structure_blueprint(
            sources, clause_code="2.6.1"
        )["components"][1]["table"]
        covered = table["cells"][1]
        self.assertEqual(covered["physical_state"], "explicit_covered")
        self.assertEqual(
            covered["logical_value_state"], "covered_from_origin"
        )
        self.assertIsNone(covered["physical_text"])
        self.assertEqual(covered["logical_value_text"], "處方規定")
        self.assertEqual(covered["source_block_orders"], [2])

    def test_source_repeat_is_not_confused_with_policy_carry(self) -> None:
        sources = [
            _source(
                "2.6.1",
                container="flow",
                render_locator={"section_role": "clause_heading"},
            ),
            _source(
                "重複值",
                container="table_cell",
                render_locator={
                    "table_index": 0,
                    "table_role": "table2_product_codes",
                    "row_index": 0,
                    "cell_index": 0,
                },
                source_locator={
                    "cell_element": "table-cell",
                    "row_repeat_attr": 2,
                },
            ),
        ]
        table = _document_structure_blueprint(
            sources, clause_code="2.6.1"
        )["components"][1]["table"]
        self.assertEqual(table["cells"][0]["physical_state"], "source_repeated")
        self.assertEqual(
            table["cells"][0]["logical_value_state"], "own_source_value"
        )

    def test_multiple_markers_in_one_physical_paragraph_fail_closed(self) -> None:
        shape = _outline_text_shape("1.第一點\n2.第二點")
        self.assertEqual(shape["structural_kind"], "paragraph")
        self.assertEqual(shape["structure_status"], "unresolved_structure")

    def test_exact_diff_reconstructs_both_sides_and_preserves_typography(
        self,
    ) -> None:
        old = "限用於「糖尿病」 20,000U"
        new = "限用於『糖尿病』　20000U"
        segments = _exact_inline_diff_segments(
            old, new, node_kind="paragraph"
        )
        self.assertEqual(
            "".join(
                segment["old_text"] or "" for segment in segments
            ),
            old,
        )
        self.assertEqual(
            "".join(
                segment["new_text"] or "" for segment in segments
            ),
            new,
        )
        self.assertTrue(
            any(
                segment["segment_kind"] != "unchanged"
                for segment in segments
            )
        )
        for segment in segments:
            if segment["old_text"] is not None:
                self.assertEqual(
                    old.encode("utf-8")[
                        segment["old_utf8_byte_start"]:
                        segment["old_utf8_byte_end"]
                    ].decode("utf-8"),
                    segment["old_text"],
                )
            if segment["new_text"] is not None:
                self.assertEqual(
                    new.encode("utf-8")[
                        segment["new_utf8_byte_start"]:
                        segment["new_utf8_byte_end"]
                    ].decode("utf-8"),
                    segment["new_text"],
                )

    def test_whitespace_deemphasis_never_erases_exact_diff(self) -> None:
        segments = _exact_inline_diff_segments(
            "甲 乙", "甲　乙", node_kind="paragraph"
        )
        self.assertEqual(
            "".join(segment["old_text"] or "" for segment in segments),
            "甲 乙",
        )
        self.assertEqual(
            "".join(segment["new_text"] or "" for segment in segments),
            "甲　乙",
        )
        self.assertTrue(
            any(
                segment["display_state"] == "deemphasized_formatting"
                for segment in segments
            )
        )

    def test_table_alignment_is_unique_signature_only(self) -> None:
        def table(signatures: list[str]) -> dict[str, object]:
            return {
                "row_count": len(signatures),
                "rows": [
                    {
                        "row_index": index,
                        "row_signature_sha256": signature,
                    }
                    for index, signature in enumerate(signatures)
                ],
            }

        inserted = _table_row_alignment(
            table(["a", "b", "c"]),
            table(["x", "a", "b", "c"]),
        )
        self.assertEqual(
            [
                (pair["old_row_index"], pair["new_row_index"])
                for pair in inserted["pairs"]
            ],
            [(0, 1), (1, 2), (2, 3)],
        )
        self.assertEqual(inserted["new_unresolved_rows"], [0])

        duplicates = _table_row_alignment(
            table(["same", "same"]),
            table(["same", "same"]),
        )
        self.assertEqual(duplicates["pairs"], [])
        self.assertEqual(duplicates["old_unresolved_rows"], [0, 1])
        self.assertEqual(duplicates["new_unresolved_rows"], [0, 1])

    def test_unmatched_nodes_stay_unresolved_beneath_exact_expression_diff(
        self,
    ) -> None:
        old_blueprint = _document_structure_blueprint(
            [
                _source(
                    "2.6.1",
                    container="flow",
                    render_locator={"section_role": "clause_heading"},
                ),
                _source(
                    "舊版未能對齊的段落",
                    container="flow",
                    render_locator={"section_role": "paragraph"},
                ),
            ],
            clause_code="2.6.1",
        )
        new_blueprint = _document_structure_blueprint(
            [
                _source(
                    "2.6.1",
                    container="flow",
                    render_locator={"section_role": "clause_heading"},
                ),
                _source(
                    "新版未能對齊的另一段",
                    container="flow",
                    render_locator={"section_role": "note"},
                ),
            ],
            clause_code="2.6.1",
        )
        rows, _, _ = _diff_projection_rows(
            diff_run_id="11111111-1111-1111-1111-111111111111",
            older_expression_id="22222222-2222-2222-2222-222222222222",
            newer_expression_id="33333333-3333-3333-3333-333333333333",
            relation_status="direct_predecessor_verified",
            old_blueprint=old_blueprint,
            new_blueprint=new_blueprint,
            old_node_ids={
                component["component_order"]: (
                    f"44444444-4444-4444-4444-{component['component_order']:012d}"
                )
                for component in old_blueprint["components"]
            },
            new_node_ids={
                component["component_order"]: (
                    f"55555555-5555-5555-5555-{component['component_order']:012d}"
                )
                for component in new_blueprint["components"]
            },
        )
        unresolved = [
            row
            for row in rows["clause_document_node_lineage"]
            if row["alignment_status"] == "alignment_unresolved"
        ]
        self.assertEqual(len(unresolved), 2)
        self.assertEqual(
            {row["lineage_kind"] for row in unresolved},
            {"old_only", "new_only"},
        )
        hunks = rows["clause_document_diff_hunk"]
        self.assertEqual(len(hunks), 1)
        self.assertEqual(
            hunks[0]["alignment_status"], "verified_work_identity"
        )
        self.assertEqual(hunks[0]["exact_change_kind"], "replaced")
        self.assertEqual(
            hunks[0]["old_exact_text"], "2.6.1\n\n舊版未能對齊的段落"
        )
        self.assertEqual(
            hunks[0]["new_exact_text"], "2.6.1\n\n新版未能對齊的另一段"
        )

    def test_expression_only_insertion_is_labeled_as_added(self) -> None:
        old_blueprint = _document_structure_blueprint(
            [
                _source(
                    "ABC",
                    container="flow",
                    render_locator={"section_role": "clause_heading"},
                )
            ],
            clause_code="2.6.1",
        )
        new_blueprint = _document_structure_blueprint(
            [
                _source(
                    "ABCD",
                    container="flow",
                    render_locator={"section_role": "clause_heading"},
                )
            ],
            clause_code="2.6.1",
        )
        rows, _, _ = _diff_projection_rows(
            diff_run_id="11111111-1111-1111-1111-111111111111",
            older_expression_id="22222222-2222-2222-2222-222222222222",
            newer_expression_id="33333333-3333-3333-3333-333333333333",
            relation_status="direct_predecessor_verified",
            old_blueprint=old_blueprint,
            new_blueprint=new_blueprint,
            old_node_ids={
                0: "44444444-4444-4444-4444-444444444444"
            },
            new_node_ids={
                0: "55555555-5555-5555-5555-555555555555"
            },
        )
        hunk = rows["clause_document_diff_hunk"][0]
        self.assertEqual(hunk["exact_change_kind"], "replaced")
        self.assertEqual(hunk["display_classification"], "本版新增")
        self.assertEqual(
            {
                segment["segment_kind"]
                for segment in rows[
                    "clause_document_inline_diff_segment"
                ]
                if segment["segment_kind"] != "unchanged"
            },
            {"inserted"},
        )

    def test_scanner_writes_complete_block_denominator_and_exact_offsets(
        self,
    ) -> None:
        sources = [
            {
                "source_block_id": "new:0",
                "raw_text": "糖尿病使用insulin",
                "raw_text_sha256": _sha("糖尿病使用insulin"),
            },
            {
                "source_block_id": "new:1",
                "raw_text": "沒有標註",
                "raw_text_sha256": _sha("沒有標註"),
            },
        ]
        projection = {
            "tagging_run_id": "11111111-1111-1111-1111-111111111111",
            "aliases": [
                {
                    "alias_id": "22222222-2222-2222-2222-222222222222",
                    "concept_id": "33333333-3333-3333-3333-333333333333",
                    "normalized_alias": normalize_alias("insulin"),
                    "production_status": "admitted",
                    "match_rule": "case_insensitive_token",
                }
            ],
        }
        inputs, occurrences = _terminology_projection_rows(
            run_id="44444444-4444-4444-4444-444444444444",
            version_id="55555555-5555-5555-5555-555555555555",
            clause_code="2.6.1",
            composite_sources=sources,
            terminology_projection=projection,
        )
        self.assertEqual(len(inputs), 2)
        self.assertEqual(
            [row["scan_status"] for row in inputs],
            ["scanned_with_match", "scanned_no_match"],
        )
        self.assertEqual(len(occurrences), 1)
        occurrence = occurrences[0]
        text = sources[0]["raw_text"]
        self.assertEqual(
            text[occurrence["start_scalar"] : occurrence["end_scalar"]],
            "insulin",
        )

    def test_adjacent_addition_does_not_invent_a_deleted_side(self) -> None:
        rows = _adjacent_diff_rows(
            run_id="44444444-4444-4444-4444-444444444444",
            version_id="55555555-5555-5555-5555-555555555555",
            clause_code="2.6.1",
            predecessor={
                "run_id": "66666666-6666-6666-6666-666666666666",
                "raw_text_sha256": _sha("ABC"),
            },
            predecessor_blocks=[{"raw_text": "ABC"}],
            composite_sources=[{"raw_text": "ABCD"}],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["semantic_change_kind"], "added")
        self.assertEqual(rows[0]["display_note"], "本版新增")
        self.assertFalse(
            any(
                segment["kind"] == "removed"
                for segment in rows[0]["inline_segments"]
            )
        )

    def test_migration_seals_shared_tagging_and_direct_diff(self) -> None:
        sql = VERSION_PROJECTION_MIGRATION.read_text(encoding="utf-8")
        for required in (
            "composed_clause_tagging_block_input",
            "composed_clause_terminology_occurrence",
            "announced_composed_admitted_occurrence_no_overlap",
            "composed_clause_diff_hunk",
            "與上一版本差異",
            "version terminology offsets differ from source blocks",
            "version tagging or adjacent diff coverage is incomplete",
        ):
            self.assertIn(required, sql)

    def test_document_migration_seals_component_and_table_invariants(
        self,
    ) -> None:
        sql = DOCUMENT_COMPONENT_MIGRATION.read_text(encoding="utf-8")
        for required in (
            "clause_document_work",
            "clause_document_expression",
            "expression_completeness",
            "clause_document_expression_relation",
            "direct_predecessor_verified",
            "clause_document_node_identity",
            "clause_document_source_span",
            "physical_state",
            "logical_value_state",
            "policy_carried_from_origin",
            "clause_document_node_lineage",
            "clause_document_inline_diff_segment",
            "old_utf8_byte_start",
            "new_utf8_byte_start",
            "primary source-span coverage is incomplete",
            "inline diff does not reconstruct both exact sides",
            "v_public_clause_document_expression",
            "v_public_clause_document_diff_hunk",
        ):
            self.assertIn(required, sql)


if __name__ == "__main__":
    unittest.main()
