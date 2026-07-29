from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from nhi_rule_history.terminology import (
    DEFAULT_ALIAS_PROPOSAL,
    MIGRATION,
    TerminologySource,
    _iter_matches,
    _resolve_occurrence_statuses,
    normalize_alias,
    prepare_terminology,
)


ROOT = Path(__file__).resolve().parents[1]


def _alias(
    text: str,
    *,
    concept_id: str,
    alias_id: str,
    status: str = "admitted",
) -> dict[str, object]:
    return {
        "alias_text": text,
        "normalized_alias": normalize_alias(text),
        "concept_id": concept_id,
        "alias_id": alias_id,
        "production_status": status,
        "match_rule": "case_insensitive_token",
    }


class TerminologyMatcherTest(unittest.TestCase):
    def test_nfkc_offsets_preserve_original_scalar_and_utf8_slice(self) -> None:
        raw = "治療ＡＢＣ後"
        matches = list(_iter_matches(raw, _alias("abc", concept_id="c", alias_id="a")))
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(raw[match["start_scalar"] : match["end_scalar"]], "ＡＢＣ")
        encoded = raw.encode("utf-8")
        self.assertEqual(
            encoded[
                match["start_utf8_byte"] : match["end_utf8_byte"]
            ].decode("utf-8"),
            "ＡＢＣ",
        )

    def test_latin_token_boundary_does_not_match_inside_longer_word(self) -> None:
        raw = "atorvastatin 與 statin類"
        matches = list(
            _iter_matches(
                raw,
                _alias("statin", concept_id="class", alias_id="statin"),
            )
        )
        self.assertEqual([match["matched_text"] for match in matches], ["statin"])
        self.assertEqual(matches[0]["start_scalar"], raw.rindex("statin"))

    def test_longest_admitted_match_wins_and_loser_is_blocked(self) -> None:
        matches = [
            {
                "concept_id": "short",
                "alias_id": "a-short",
                "normalized_alias": "statin",
                "start_scalar": 5,
                "end_scalar": 11,
                "occurrence_status": "admitted",
                "occurrence_reason": "reviewed_alias_longest_match",
            },
            {
                "concept_id": "long",
                "alias_id": "a-long",
                "normalized_alias": "atorvastatin",
                "start_scalar": 0,
                "end_scalar": 12,
                "occurrence_status": "admitted",
                "occurrence_reason": "reviewed_alias_longest_match",
            },
        ]
        resolved = _resolve_occurrence_statuses(matches)
        by_alias = {row["alias_id"]: row for row in resolved}
        self.assertEqual(by_alias["a-long"]["occurrence_status"], "admitted")
        self.assertEqual(by_alias["a-short"]["occurrence_status"], "blocked")
        self.assertEqual(by_alias["a-short"]["occurrence_reason"], "overlap_lost")

    def test_same_span_cross_concept_fails_closed(self) -> None:
        matches = [
            {
                "concept_id": concept,
                "alias_id": f"a-{concept}",
                "normalized_alias": "g-csf",
                "start_scalar": 0,
                "end_scalar": 5,
                "occurrence_status": status,
                "occurrence_reason": reason,
            }
            for concept, status, reason in (
                ("class", "admitted", "reviewed_alias_longest_match"),
                ("ingredient", "candidate", "alias_candidate"),
            )
        ]
        resolved = _resolve_occurrence_statuses(matches)
        self.assertEqual(
            {row["occurrence_status"] for row in resolved}, {"blocked"}
        )
        self.assertEqual(
            {row["occurrence_reason"] for row in resolved},
            {"same_span_cross_concept"},
        )


class TerminologyMaterialTest(unittest.TestCase):
    @staticmethod
    def _synthetic_source() -> TerminologySource:
        proposal = [
            json.loads(line)
            for line in DEFAULT_ALIAS_PROPOSAL.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        seed_tags: dict[str, dict[str, object]] = {}
        seed_codes: dict[str, tuple[dict[str, object], ...]] = {}
        observed_texts: list[str] = []
        for concept_index, concept in enumerate(proposal, 1):
            observed = sorted(
                (
                    alias["text"]
                    for alias in concept["aliases"]
                    if alias["source_status"] == "source_observed"
                ),
                key=lambda value: (normalize_alias(value), value),
            )
            tag_ids = sorted(concept["source_tag_ids"])
            if len(observed) != len(tag_ids):
                raise AssertionError("proposal fixture lost seed conservation")
            if concept["concept_type"] == "disease":
                tag_type, entity_type = "disease", "disease"
                code_system, code = "ICD11", f"X{concept_index}"
                mapping_basis = "agent_selected"
                release = "2024-01"
            elif concept["concept_type"] == "treatment_modality":
                tag_type, entity_type = "treatment", "treatment_modality"
                code_system, code = "NHI_TREATMENT", f"{concept_index:05d}A"
                mapping_basis = "official_payment_standard_exact_code"
                release = "2026-07-03"
            else:
                tag_type = "drug"
                entity_type = {
                    "drug_brand": "brand",
                    "drug_class": "drug_class",
                    "drug_ingredient": "ingredient",
                }[concept["concept_type"]]
                code_system, code = "ATC", f"A{concept_index:02d}"
                mapping_basis = "nhi_rule_group_mapping"
                release = "current"
            for tag_id, tag_text in zip(tag_ids, observed, strict=True):
                seed_tags[tag_id] = {
                    "tag_id": tag_id,
                    "tag_text": tag_text,
                    "tag_type": tag_type,
                    "entity_type": entity_type,
                    "resolution_status": "fixture",
                    "provenance": {"fixture": True},
                }
                seed_codes[tag_id] = (
                    {
                        "tag_id": tag_id,
                        "code_system": code_system,
                        "code": code,
                        "mapping_basis": mapping_basis,
                        "review_status": "agent_verified",
                        "master_release": release,
                    },
                )
                observed_texts.append(tag_text)
        raw_text = " / ".join(observed_texts)
        return TerminologySource(
            publication_run_id="a707d13a-0b06-5dfe-96b7-6d107ab8793f",
            publication_sealed_fingerprint="1" * 64,
            seed_enrichment_run_id="44640535-2f19-51d2-afcf-1572fea9be63",
            seed_output_sha256="2" * 64,
            seed_tags=seed_tags,
            seed_codes=seed_codes,
            blocks=(
                {
                    "clause_code": "0.4",
                    "block_order": 0,
                    "source_block_id": "fixture:block:0",
                    "raw_text": raw_text,
                    "raw_text_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            publication_clause_count=1,
        )

    def test_real_proposal_is_conserved_and_deterministic(self) -> None:
        source = self._synthetic_source()
        first = prepare_terminology(source)
        second = prepare_terminology(source)
        self.assertEqual(first.tagging_run_id, second.tagging_run_id)
        self.assertEqual(first.output_fingerprint, second.output_fingerprint)
        self.assertEqual(first.expected_counts["concept_registry"], 79)
        self.assertEqual(first.expected_counts["concept_seed_tag_link"], 82)
        self.assertEqual(first.expected_counts["concept_alias"], 371)
        self.assertEqual(first.verified_metrics["scanned_block_count"], 1)
        self.assertEqual(first.verified_metrics["scanned_clause_count"], 1)
        self.assertEqual(first.verified_metrics["admitted_alias_count"], 76)

    def test_migration_contains_r3_release_invariants(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        for required in (
            "concept_registry",
            "concept_seed_tag_link",
            "tagging_run_block_input",
            "tagging_run_activation",
            "unicode_scalar_half_open+utf8_byte_half_open/v1",
            "clause_occurrence_admitted_no_overlap",
            "v_admitted_clause_occurrence",
            "sealed terminology child rows are immutable",
        ):
            self.assertIn(required, sql)


if __name__ == "__main__":
    unittest.main()
