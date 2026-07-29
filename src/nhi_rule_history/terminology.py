"""Build and load the normalized clause terminology projection.

The scanner is deterministic and source-block based.  Model-proposed aliases
are retained as candidate evidence, but v1 reader admission is limited to
collision-free aliases observed in the reviewed legacy enrichment run.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from nhi_rule_history.contracts import canonical_json_bytes, file_sha256
from nhi_rule_history.pg.acquisition import DSN_ENV, _default_connect
from nhi_rule_history.pg.common import (
    PgLoadError,
    code_fingerprint,
    json_text,
    migration_fingerprint,
    object_fingerprint,
    row_set_fingerprint,
    row_sha256,
)


SCHEMA = "nhi_rule_history_terminology"
PUBLICATION_SCHEMA = "nhi_rule_history_publication"
SEED_SCHEMA = "nhi_rule_history_clause"
GLOBAL_LOCK_KEY = "nhi-rule-history-terminology-global"
MATCHER_VERSION = "nhi-rule-history/terminology-longest-match/1.0.0"
LOADER_VERSION = "nhi-rule-history/terminology-loader/1.0.0"
OFFSET_CONTRACT = "unicode_scalar_half_open+utf8_byte_half_open/v1"
ALIAS_ADMISSION_POLICY = "reviewed_source_observed_only/v1"
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "pg"
    / "migrations"
    / "2026-07-29_nhi_rule_history_terminology_v20.sql"
)
DEFAULT_ALIAS_PROPOSAL = (
    ROOT
    / "data"
    / "proposals"
    / "gemini-semantic-alias-2026-07-29"
    / "candidates.jsonl"
)
_UUID_NAMESPACE = uuid.UUID("90f6da35-2df0-44a3-9b8d-52ed59bf2242")
_ABBREVIATION_ALLOWLIST = frozenset({"capd", "g-csf", "tpn", "vte"})
_TABLES = (
    "concept_registry",
    "run_concept",
    "concept_seed_tag_link",
    "concept_alias",
    "concept_external_code",
    "tagging_run_block_input",
    "clause_occurrence",
)
_STATUS_PRIORITY = {"admitted": 0, "candidate": 1, "blocked": 2}


class TerminologyError(PgLoadError):
    """Unsafe terminology input, scan, or PostgreSQL projection."""


@dataclass(frozen=True)
class TerminologySource:
    publication_run_id: str
    publication_sealed_fingerprint: str
    seed_enrichment_run_id: str
    seed_output_sha256: str
    seed_tags: Mapping[str, Mapping[str, Any]]
    seed_codes: Mapping[str, tuple[Mapping[str, Any], ...]]
    blocks: tuple[Mapping[str, Any], ...]
    publication_clause_count: int


@dataclass(frozen=True)
class TerminologyMaterial:
    tagging_run_id: str
    publication_run_id: str
    seed_enrichment_run_id: str
    alias_proposal_sha256: str
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    expected_counts: Mapping[str, int]
    verified_metrics: Mapping[str, int]
    table_fingerprints: Mapping[str, str]
    input_fingerprint: str
    output_fingerprint: str
    migration_sha256: str
    code_sha256: str
    sealed_fingerprint: str


def _stable_uuid(label: str, value: object) -> str:
    material = canonical_json_bytes([label, value]).decode("utf-8")
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_alias(value: str) -> str:
    """Return scalar-wise NFKC + casefold text used by the matcher."""

    if not isinstance(value, str) or not value:
        raise TerminologyError("alias text must be non-empty")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise TerminologyError("alias text contains a Unicode surrogate")
    return "".join(
        unicodedata.normalize("NFKC", char).casefold() for char in value
    )


def _normalized_text_map(
    value: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Normalize while mapping every normalized scalar to exact source bounds."""

    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise TerminologyError("source block contains a Unicode surrogate")
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    byte_prefix = [0]
    for index, char in enumerate(value):
        byte_prefix.append(byte_prefix[-1] + len(char.encode("utf-8")))
        chunk = unicodedata.normalize("NFKC", char).casefold()
        normalized.extend(chunk)
        starts.extend([index] * len(chunk))
        ends.extend([index + 1] * len(chunk))
    return (
        "".join(normalized),
        tuple(starts),
        tuple(ends),
        tuple(byte_prefix),
    )


def _ascii_alnum(char: str | None) -> bool:
    return bool(
        char
        and char.isascii()
        and ("0" <= char <= "9" or "a" <= char.lower() <= "z")
    )


def _token_boundary_ok(text: str, start: int, end: int, alias: str) -> bool:
    if _ascii_alnum(alias[0]):
        if start > 0 and _ascii_alnum(text[start - 1]):
            return False
    if _ascii_alnum(alias[-1]):
        if end < len(text) and _ascii_alnum(text[end]):
            return False
    return True


def _iter_matches(
    raw_text: str,
    alias: Mapping[str, Any],
    *,
    normalized_map: tuple[
        str, tuple[int, ...], tuple[int, ...], tuple[int, ...]
    ]
    | None = None,
) -> Iterable[dict[str, Any]]:
    normalized_text, starts, ends, byte_prefix = (
        normalized_map
        if normalized_map is not None
        else _normalized_text_map(raw_text)
    )
    needle = str(alias["normalized_alias"])
    cursor = 0
    while True:
        found = normalized_text.find(needle, cursor)
        if found < 0:
            return
        normalized_end = found + len(needle)
        cursor = found + 1
        if not _token_boundary_ok(
            normalized_text, found, normalized_end, needle
        ):
            continue
        start_scalar = starts[found]
        end_scalar = ends[normalized_end - 1]
        matched_text = raw_text[start_scalar:end_scalar]
        if normalize_alias(matched_text) != needle:
            continue
        yield {
            "start_scalar": start_scalar,
            "end_scalar": end_scalar,
            "start_utf8_byte": byte_prefix[start_scalar],
            "end_utf8_byte": byte_prefix[end_scalar],
            "matched_text": matched_text,
        }


def _read_alias_proposal(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise TerminologyError("alias proposal is unreadable") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TerminologyError(
                f"alias proposal line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise TerminologyError("alias proposal row is not an object")
        rows.append(row)
    if not rows:
        raise TerminologyError("alias proposal contains no concepts")
    return tuple(rows)


def _seed_concept_type(tag: Mapping[str, Any]) -> str:
    key = (str(tag["tag_type"]), str(tag["entity_type"]))
    mapping = {
        ("drug", "ingredient"): "drug_ingredient",
        ("drug", "brand"): "drug_brand",
        ("drug", "drug_class"): "drug_class",
        ("drug", "abbreviation"): "drug_class",
        ("disease", "disease"): "disease",
        ("disease", "clinical_condition"): "disease",
        ("disease", "abbreviation"): "disease",
        ("treatment", "treatment_modality"): "treatment_modality",
    }
    try:
        return mapping[key]
    except KeyError as exc:
        raise TerminologyError(
            f"unsupported reviewed seed tag type: {key}"
        ) from exc


def _validate_proposal(
    proposal: Sequence[Mapping[str, Any]],
    source: TerminologySource,
) -> tuple[dict[str, Any], ...]:
    seen_candidate_ids: set[str] = set()
    seen_seed_ids: list[str] = []
    validated: list[dict[str, Any]] = []
    for row in proposal:
        candidate_id = row.get("candidate_concept_id")
        source_tag_ids = row.get("source_tag_ids")
        aliases = row.get("aliases")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in seen_candidate_ids
        ):
            raise TerminologyError("candidate concept identity is invalid")
        if (
            not isinstance(source_tag_ids, list)
            or not source_tag_ids
            or len(set(source_tag_ids)) != len(source_tag_ids)
        ):
            raise TerminologyError("candidate source tag set is invalid")
        if not isinstance(aliases, list) or not aliases:
            raise TerminologyError("candidate aliases are missing")
        seed_rows: list[Mapping[str, Any]] = []
        for tag_id in source_tag_ids:
            if not isinstance(tag_id, str) or tag_id not in source.seed_tags:
                raise TerminologyError("candidate references an unknown seed tag")
            seed_rows.append(source.seed_tags[tag_id])
            seen_seed_ids.append(tag_id)
        concept_type = row.get("concept_type")
        if any(_seed_concept_type(tag) != concept_type for tag in seed_rows):
            raise TerminologyError(
                "candidate concept type disagrees with reviewed seed tags"
            )
        if len(seed_rows) > 1:
            code_sets = {
                frozenset(
                    (str(code["code_system"]), str(code["code"]))
                    for code in source.seed_codes[tag["tag_id"]]
                )
                for tag in seed_rows
            }
            if len(code_sets) != 1:
                raise TerminologyError(
                    "merged candidate seed tags do not have identical codes"
                )
        observed_aliases: dict[str, int] = defaultdict(int)
        clean_aliases: list[dict[str, Any]] = []
        for alias in aliases:
            if not isinstance(alias, dict):
                raise TerminologyError("candidate alias is not an object")
            text = alias.get("text")
            if not isinstance(text, str) or not text:
                raise TerminologyError("candidate alias text is invalid")
            clean = dict(alias)
            clean["normalized_alias"] = normalize_alias(text)
            clean_aliases.append(clean)
            if alias.get("source_status") == "source_observed":
                observed_aliases[clean["normalized_alias"]] += 1
        for tag in seed_rows:
            if observed_aliases[normalize_alias(str(tag["tag_text"]))] != 1:
                raise TerminologyError(
                    "reviewed seed text does not map to exactly one "
                    "source-observed alias"
                )
        seen_candidate_ids.add(candidate_id)
        clean_row = dict(row)
        clean_row["aliases"] = clean_aliases
        clean_row["source_tag_ids"] = sorted(source_tag_ids)
        validated.append(clean_row)
    if sorted(seen_seed_ids) != sorted(source.seed_tags):
        raise TerminologyError(
            "proposal does not conserve every reviewed seed tag exactly once"
        )
    return tuple(validated)


def _production_alias_status(
    *,
    alias: Mapping[str, Any],
    collision: bool,
) -> tuple[str, str]:
    normalized = str(alias["normalized_alias"])
    if collision:
        return "blocked", "normalized_cross_concept_collision"
    if alias.get("match_rule") == "context_required":
        return "blocked", "context_required"
    if alias.get("source_status") != "source_observed":
        return "candidate", "model_suggested_candidate"
    if (
        alias.get("alias_type") == "abbreviation"
        and normalized not in _ABBREVIATION_ALLOWLIST
    ):
        return "blocked", "lexical_ambiguity_not_allowlisted"
    return "admitted", "reviewed_source_observed"


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source_row_sha256"] = row_sha256(result)
    return result


def _concept_rows(
    proposal: Sequence[Mapping[str, Any]],
    source: TerminologySource,
    tagging_run_id: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
]:
    registry: list[dict[str, Any]] = []
    concepts: list[dict[str, Any]] = []
    seed_links: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    candidate_to_concept: dict[str, str] = {}
    normalized_concepts: dict[str, set[str]] = defaultdict(set)
    for candidate in proposal:
        for alias in candidate["aliases"]:
            normalized_concepts[str(alias["normalized_alias"])].add(
                str(candidate["candidate_concept_id"])
            )
    for candidate in proposal:
        seed_ids = sorted(str(value) for value in candidate["source_tag_ids"])
        seed_digest = object_fingerprint(seed_ids)
        identity_basis = f"reviewed-legacy-tag-set/v1:{seed_digest}"
        concept_id = _stable_uuid("concept-registry", identity_basis)
        candidate_id = str(candidate["candidate_concept_id"])
        candidate_to_concept[candidate_id] = concept_id
        registry.append(
            _source_row(
                {
                    "concept_id": concept_id,
                    "identity_basis": identity_basis,
                    "identity_version": "reviewed-legacy-tag-set/v1",
                    "created_from": "reviewed_legacy_tag_set",
                }
            )
        )
        concepts.append(
            _source_row(
                {
                    "tagging_run_id": tagging_run_id,
                    "concept_id": concept_id,
                    "concept_type": candidate["concept_type"],
                    "canonical_label_zh": candidate.get(
                        "canonical_label_zh"
                    ),
                    "canonical_label_en": candidate.get(
                        "canonical_label_en"
                    ),
                    "link_family": candidate["link_family"],
                    "review_status": "reviewed_seed_group",
                    "provenance": {
                        "candidate_concept_id": candidate_id,
                        "proposal_producer": candidate.get(
                            "proposal_provenance", {}
                        ).get("producer"),
                        "review_basis": (
                            "same seed concept type and identical reviewed "
                            "external-code set"
                        ),
                    },
                }
            )
        )
        for tag_id in seed_ids:
            seed_links.append(
                _source_row(
                    {
                        "tagging_run_id": tagging_run_id,
                        "concept_id": concept_id,
                        "legacy_tag_id": tag_id,
                        "seed_enrichment_run_id": (
                            source.seed_enrichment_run_id
                        ),
                        "mapping_status": "reviewed_seed_group_member",
                    }
                )
            )
        seen_alias_rows: set[tuple[str, str, str, str]] = set()
        for alias in candidate["aliases"]:
            normalized = str(alias["normalized_alias"])
            dedupe_key = (
                str(alias["text"]),
                normalized,
                str(alias["language"]),
                str(alias["source_status"]),
            )
            if dedupe_key in seen_alias_rows:
                continue
            seen_alias_rows.add(dedupe_key)
            collision = len(normalized_concepts[normalized]) > 1
            production_status, production_reason = _production_alias_status(
                alias=alias,
                collision=collision,
            )
            alias_identity = {
                "concept_id": concept_id,
                "alias_text": alias["text"],
                "normalized_alias": normalized,
                "language_tag": alias["language"],
                "alias_type": alias["alias_type"],
                "source_status": alias["source_status"],
            }
            aliases.append(
                _source_row(
                    {
                        "tagging_run_id": tagging_run_id,
                        "alias_id": _stable_uuid(
                            "concept-alias", alias_identity
                        ),
                        "concept_id": concept_id,
                        **alias_identity,
                        "proposed_auto_match": bool(alias["auto_match"]),
                        "match_rule": alias["match_rule"],
                        "production_status": production_status,
                        "production_reason": production_reason,
                        "ambiguity_note": alias.get("ambiguity_note"),
                    }
                )
            )
    return registry, concepts, seed_links, aliases, candidate_to_concept


def _external_code_rows(
    proposal: Sequence[Mapping[str, Any]],
    source: TerminologySource,
    tagging_run_id: str,
    candidate_to_concept: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in proposal:
        concept_id = candidate_to_concept[
            str(candidate["candidate_concept_id"])
        ]
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(
            list
        )
        for tag_id in candidate["source_tag_ids"]:
            for code in source.seed_codes[str(tag_id)]:
                grouped[(str(code["code_system"]), str(code["code"]))].append(
                    code
                )
        for (code_system, code), evidence in sorted(grouped.items()):
            if code_system == "ATC":
                master_source = "tw_drug.ref_atc"
                release_values = {
                    str(row.get("master_release") or "current")
                    for row in evidence
                }
            elif code_system == "ICD11":
                master_source = "medical_knowledge.icd11_who"
                release_values = {
                    str(row.get("master_release") or "2024-01")
                    for row in evidence
                }
            elif code_system == "NHI_TREATMENT":
                master_source = "tw_health_open.nhi_payment_standard"
                release_values = {
                    str(row.get("master_release") or "current")
                    for row in evidence
                }
            else:
                raise TerminologyError("unsupported external code system")
            if len(release_values) != 1:
                raise TerminologyError(
                    "reviewed code evidence has inconsistent master releases"
                )
            review_status = (
                "agent_curated"
                if any(
                    row.get("review_status") == "agent_curated"
                    for row in evidence
                )
                else "agent_verified"
            )
            rows.append(
                _source_row(
                    {
                        "tagging_run_id": tagging_run_id,
                        "concept_id": concept_id,
                        "code_system": code_system,
                        "code": code,
                        "relation_type": "reviewed_seed_mapping",
                        "review_status": review_status,
                        "public_safe": True,
                        "master_source": master_source,
                        "master_release": next(iter(release_values)),
                        "provenance": {
                            "legacy_tag_ids": sorted(
                                {
                                    str(row["tag_id"])
                                    for row in evidence
                                }
                            ),
                            "mapping_basis": sorted(
                                {
                                    str(row["mapping_basis"])
                                    for row in evidence
                                }
                            ),
                        },
                    }
                )
            )
    return rows


def _collapse_same_concept(
    matches: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(
        list
    )
    for match in matches:
        grouped[
            (
                str(match["concept_id"]),
                int(match["start_scalar"]),
                int(match["end_scalar"]),
            )
        ].append(match)
    result: list[dict[str, Any]] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda row: (
                _STATUS_PRIORITY[str(row["occurrence_status"])],
                -len(str(row["normalized_alias"])),
                str(row["alias_id"]),
            ),
        )
        result.append(dict(ordered[0]))
    return result


def _resolve_occurrence_statuses(
    matches: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    collapsed = _collapse_same_concept(matches)
    same_span: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for match in collapsed:
        same_span[
            (int(match["start_scalar"]), int(match["end_scalar"]))
        ].append(match)
    after_sense: list[dict[str, Any]] = []
    for group in same_span.values():
        concepts = {str(match["concept_id"]) for match in group}
        if len(concepts) > 1:
            for match in group:
                blocked = dict(match)
                blocked["occurrence_status"] = "blocked"
                blocked["occurrence_reason"] = "same_span_cross_concept"
                after_sense.append(blocked)
        else:
            after_sense.extend(dict(match) for match in group)

    admitted = sorted(
        (
            match
            for match in after_sense
            if match["occurrence_status"] == "admitted"
        ),
        key=lambda row: (
            -(int(row["end_scalar"]) - int(row["start_scalar"])),
            int(row["start_scalar"]),
            str(row["alias_id"]),
        ),
    )
    selected: list[dict[str, Any]] = []
    rejected_ids: set[str] = set()
    for match in admitted:
        overlaps = any(
            int(match["start_scalar"]) < int(kept["end_scalar"])
            and int(kept["start_scalar"]) < int(match["end_scalar"])
            for kept in selected
        )
        if overlaps:
            rejected_ids.add(str(match["alias_id"]))
        else:
            selected.append(match)
    result: list[dict[str, Any]] = []
    for match in after_sense:
        row = dict(match)
        if (
            row["occurrence_status"] == "admitted"
            and str(row["alias_id"]) in rejected_ids
        ):
            row["occurrence_status"] = "blocked"
            row["occurrence_reason"] = "overlap_lost"
        result.append(row)
    return sorted(
        result,
        key=lambda row: (
            int(row["start_scalar"]),
            int(row["end_scalar"]),
            str(row["concept_id"]),
            str(row["alias_id"]),
        ),
    )


def scan_block_alias_occurrences(
    raw_text: str,
    aliases: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Scan one immutable text block with the canonical terminology matcher.

    This is the version-agnostic entry point.  Current publications and newly
    announced clause versions must use this same matcher so a frontend never
    invents a second lexical-highlighting policy.
    """

    normalized_map = _normalized_text_map(raw_text)
    raw_matches: list[dict[str, Any]] = []
    for alias in aliases:
        for span in _iter_matches(
            raw_text, alias, normalized_map=normalized_map
        ):
            if alias["production_status"] == "admitted":
                occurrence_status = "admitted"
                occurrence_reason = "reviewed_alias_longest_match"
            elif alias["production_status"] == "candidate":
                occurrence_status = "candidate"
                occurrence_reason = "alias_candidate"
            else:
                occurrence_status = "blocked"
                occurrence_reason = "alias_blocked"
            raw_matches.append(
                {
                    **span,
                    "concept_id": alias["concept_id"],
                    "alias_id": alias["alias_id"],
                    "normalized_alias": alias["normalized_alias"],
                    "occurrence_status": occurrence_status,
                    "occurrence_reason": occurrence_reason,
                    "match_rule": alias["match_rule"],
                }
            )
    return tuple(_resolve_occurrence_statuses(raw_matches))


def _scan_blocks(
    *,
    tagging_run_id: str,
    source: TerminologySource,
    aliases: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    block_inputs: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    for block in source.blocks:
        raw_text = str(block["raw_text"])
        resolved = scan_block_alias_occurrences(raw_text, aliases)
        status_counts = defaultdict(int)
        for match in resolved:
            status_counts[str(match["occurrence_status"])] += 1
            identity = {
                "tagging_run_id": tagging_run_id,
                "clause_code": block["clause_code"],
                "block_order": block["block_order"],
                "concept_id": match["concept_id"],
                "alias_id": match["alias_id"],
                "start_scalar": match["start_scalar"],
                "end_scalar": match["end_scalar"],
                "occurrence_status": match["occurrence_status"],
                "occurrence_reason": match["occurrence_reason"],
            }
            occurrences.append(
                _source_row(
                    {
                        "tagging_run_id": tagging_run_id,
                        "occurrence_id": _stable_uuid(
                            "clause-occurrence", identity
                        ),
                        "publication_run_id": source.publication_run_id,
                        "clause_code": block["clause_code"],
                        "block_order": block["block_order"],
                        "source_block_id": block["source_block_id"],
                        "source_block_sha256": block["raw_text_sha256"],
                        "concept_id": match["concept_id"],
                        "alias_id": match["alias_id"],
                        "start_scalar": match["start_scalar"],
                        "end_scalar": match["end_scalar"],
                        "start_utf8_byte": match["start_utf8_byte"],
                        "end_utf8_byte": match["end_utf8_byte"],
                        "matched_text": match["matched_text"],
                        "matched_text_sha256": _sha256_text(
                            str(match["matched_text"])
                        ),
                        "occurrence_status": match["occurrence_status"],
                        "occurrence_reason": match["occurrence_reason"],
                        "match_rule": match["match_rule"],
                    }
                )
            )
        match_total = sum(status_counts.values())
        block_inputs.append(
            _source_row(
                {
                    "tagging_run_id": tagging_run_id,
                    "publication_run_id": source.publication_run_id,
                    "clause_code": block["clause_code"],
                    "block_order": block["block_order"],
                    "source_block_id": block["source_block_id"],
                    "source_block_sha256": block["raw_text_sha256"],
                    "scan_status": (
                        "scanned_with_match"
                        if match_total
                        else "scanned_no_match"
                    ),
                    "candidate_match_count": status_counts["candidate"],
                    "admitted_match_count": status_counts["admitted"],
                    "blocked_match_count": status_counts["blocked"],
                }
            )
        )
    return block_inputs, occurrences


def prepare_terminology(
    source: TerminologySource,
    *,
    alias_proposal_path: Path = DEFAULT_ALIAS_PROPOSAL,
) -> TerminologyMaterial:
    proposal_path = Path(alias_proposal_path)
    proposal = _validate_proposal(
        _read_alias_proposal(proposal_path), source
    )
    alias_proposal_sha256 = file_sha256(proposal_path)
    migration_sha256 = migration_fingerprint(MIGRATION)
    code_sha256 = code_fingerprint(Path(__file__))
    input_fingerprint = object_fingerprint(
        {
            "publication_run_id": source.publication_run_id,
            "publication_sealed_fingerprint": (
                source.publication_sealed_fingerprint
            ),
            "seed_enrichment_run_id": source.seed_enrichment_run_id,
            "seed_output_sha256": source.seed_output_sha256,
            "alias_proposal_sha256": alias_proposal_sha256,
            "matcher_version": MATCHER_VERSION,
            "loader_version": LOADER_VERSION,
            "offset_contract": OFFSET_CONTRACT,
            "alias_admission_policy": ALIAS_ADMISSION_POLICY,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
        }
    )
    tagging_run_id = _stable_uuid("terminology-run", input_fingerprint)
    (
        registry,
        concepts,
        seed_links,
        aliases,
        candidate_to_concept,
    ) = _concept_rows(proposal, source, tagging_run_id)
    external_codes = _external_code_rows(
        proposal, source, tagging_run_id, candidate_to_concept
    )
    block_inputs, occurrences = _scan_blocks(
        tagging_run_id=tagging_run_id,
        source=source,
        aliases=aliases,
    )
    rows: dict[str, tuple[dict[str, Any], ...]] = {
        "concept_registry": tuple(registry),
        "run_concept": tuple(concepts),
        "concept_seed_tag_link": tuple(seed_links),
        "concept_alias": tuple(aliases),
        "concept_external_code": tuple(external_codes),
        "tagging_run_block_input": tuple(block_inputs),
        "clause_occurrence": tuple(occurrences),
    }
    expected_counts = {
        table: len(rows[table]) for table in _TABLES
    }
    table_fingerprints = {
        table: row_set_fingerprint(
            row["source_row_sha256"] for row in rows[table]
        )
        for table in _TABLES
    }
    status_counts = defaultdict(int)
    for row in occurrences:
        status_counts[str(row["occurrence_status"])] += 1
    verified_metrics = {
        "publication_clause_count": source.publication_clause_count,
        "scanned_clause_count": len(
            {str(row["clause_code"]) for row in block_inputs}
        ),
        "publication_block_count": len(source.blocks),
        "scanned_block_count": len(block_inputs),
        "scanned_no_match_block_count": sum(
            row["scan_status"] == "scanned_no_match" for row in block_inputs
        ),
        "scanned_with_match_block_count": sum(
            row["scan_status"] == "scanned_with_match" for row in block_inputs
        ),
        "seed_tag_count": len(source.seed_tags),
        "admitted_alias_count": sum(
            row["production_status"] == "admitted" for row in aliases
        ),
        "candidate_alias_count": sum(
            row["production_status"] == "candidate" for row in aliases
        ),
        "blocked_alias_count": sum(
            row["production_status"] == "blocked" for row in aliases
        ),
        "admitted_occurrence_count": status_counts["admitted"],
        "candidate_occurrence_count": status_counts["candidate"],
        "blocked_occurrence_count": status_counts["blocked"],
    }
    if verified_metrics["scanned_clause_count"] != source.publication_clause_count:
        raise TerminologyError("scanner did not cover every publication clause")
    output_fingerprint = object_fingerprint(
        {
            "counts": expected_counts,
            "metrics": verified_metrics,
            "table_fingerprints": table_fingerprints,
        }
    )
    sealed_fingerprint = object_fingerprint(
        {
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "migration_sha256": migration_sha256,
            "code_sha256": code_sha256,
            "matcher_version": MATCHER_VERSION,
            "loader_version": LOADER_VERSION,
        }
    )
    return TerminologyMaterial(
        tagging_run_id=tagging_run_id,
        publication_run_id=source.publication_run_id,
        seed_enrichment_run_id=source.seed_enrichment_run_id,
        alias_proposal_sha256=alias_proposal_sha256,
        rows=rows,
        expected_counts=expected_counts,
        verified_metrics=verified_metrics,
        table_fingerprints=table_fingerprints,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        migration_sha256=migration_sha256,
        code_sha256=code_sha256,
        sealed_fingerprint=sealed_fingerprint,
    )


def _load_source(
    connection: Any,
    *,
    publication_run_id: str | None = None,
    seed_enrichment_run_id: str | None = None,
) -> TerminologySource:
    with connection.cursor() as cursor:
        if publication_run_id is None:
            cursor.execute(
                f"""
                SELECT run_id, sealed_fingerprint
                FROM {PUBLICATION_SCHEMA}.v_active_publication_run
                """
            )
        else:
            cursor.execute(
                f"""
                SELECT run_id, sealed_fingerprint
                FROM {PUBLICATION_SCHEMA}.publication_run
                WHERE run_id = %s AND state = 'sealed'
                """,
                (publication_run_id,),
            )
        publication = cursor.fetchone()
        if publication is None:
            raise TerminologyError("sealed source publication was not found")
        source_publication_id = str(publication[0])
        if seed_enrichment_run_id is None:
            cursor.execute(
                f"""
                SELECT run_id, output_sha256
                FROM {SEED_SCHEMA}.reader_enrichment_run
                WHERE state = 'sealed'
                ORDER BY sealed_at DESC, run_id DESC
                LIMIT 1
                """
            )
        else:
            cursor.execute(
                f"""
                SELECT run_id, output_sha256
                FROM {SEED_SCHEMA}.reader_enrichment_run
                WHERE run_id = %s AND state = 'sealed'
                """,
                (seed_enrichment_run_id,),
            )
        seed_run = cursor.fetchone()
        if seed_run is None:
            raise TerminologyError("sealed seed enrichment was not found")
        source_seed_id = str(seed_run[0])
        cursor.execute(
            f"""
            SELECT tag_id, tag_text, tag_type, entity_type,
                   resolution_status, provenance
            FROM {SEED_SCHEMA}.clause_semantic_tag
            WHERE enrichment_run_id = %s
            ORDER BY tag_id
            """,
            (source_seed_id,),
        )
        seed_tags = {
            str(row[0]): {
                "tag_id": str(row[0]),
                "tag_text": str(row[1]),
                "tag_type": str(row[2]),
                "entity_type": str(row[3]),
                "resolution_status": str(row[4]),
                "provenance": row[5],
            }
            for row in cursor.fetchall()
        }
        if not seed_tags:
            raise TerminologyError("seed enrichment has no semantic tags")
        seed_codes: dict[str, list[dict[str, Any]]] = {
            tag_id: [] for tag_id in seed_tags
        }
        cursor.execute(
            f"""
            SELECT tag_id, atc_code, mapping_basis, review_status,
                   coalesce(source_updated_at, 'current')
            FROM {SEED_SCHEMA}.clause_semantic_tag_atc
            WHERE enrichment_run_id = %s
            ORDER BY tag_id, atc_code
            """,
            (source_seed_id,),
        )
        for row in cursor.fetchall():
            seed_codes[str(row[0])].append(
                {
                    "tag_id": str(row[0]),
                    "code_system": "ATC",
                    "code": str(row[1]),
                    "mapping_basis": str(row[2]),
                    "review_status": str(row[3]),
                    "master_release": str(row[4]),
                }
            )
        cursor.execute(
            f"""
            SELECT code.tag_id, code.icd11_code, code.mapping_status,
                   'agent_curated', master.release_id
            FROM {SEED_SCHEMA}.clause_semantic_tag_icd11_code code
            JOIN LATERAL (
              SELECT candidate.release_id
              FROM medical_knowledge.icd11_who candidate
              WHERE candidate.code = code.icd11_code
              ORDER BY candidate.release_id DESC
              LIMIT 1
            ) master ON true
            WHERE code.enrichment_run_id = %s
            ORDER BY code.tag_id, code.icd11_code
            """,
            (source_seed_id,),
        )
        for row in cursor.fetchall():
            seed_codes[str(row[0])].append(
                {
                    "tag_id": str(row[0]),
                    "code_system": "ICD11",
                    "code": str(row[1]),
                    "mapping_basis": str(row[2]),
                    "review_status": str(row[3]),
                    "master_release": str(row[4]),
                }
            )
        cursor.execute(
            f"""
            SELECT tag_id, treatment_code, mapping_basis, review_status,
                   coalesce(source_resource_modified::text, 'current')
            FROM {SEED_SCHEMA}.clause_semantic_tag_nhi_treatment
            WHERE enrichment_run_id = %s
            ORDER BY tag_id, treatment_code
            """,
            (source_seed_id,),
        )
        for row in cursor.fetchall():
            seed_codes[str(row[0])].append(
                {
                    "tag_id": str(row[0]),
                    "code_system": "NHI_TREATMENT",
                    "code": str(row[1]),
                    "mapping_basis": str(row[2]),
                    "review_status": str(row[3]),
                    "master_release": str(row[4]),
                }
            )
        missing_codes = sorted(
            tag_id for tag_id, values in seed_codes.items() if not values
        )
        if missing_codes:
            raise TerminologyError(
                "reviewed seed tags without external codes: "
                + ", ".join(missing_codes[:3])
            )
        cursor.execute(
            f"""
            SELECT clause_code, block_order, source_block_id, raw_text,
                   raw_text_sha256
            FROM {PUBLICATION_SCHEMA}.current_clause_block
            WHERE run_id = %s
            ORDER BY clause_code, block_order
            """,
            (source_publication_id,),
        )
        blocks = tuple(
            {
                "clause_code": str(row[0]),
                "block_order": int(row[1]),
                "source_block_id": str(row[2]),
                "raw_text": str(row[3]),
                "raw_text_sha256": str(row[4]),
            }
            for row in cursor.fetchall()
        )
        cursor.execute(
            f"""
            SELECT count(*)::integer
            FROM {PUBLICATION_SCHEMA}.current_clause
            WHERE run_id = %s
            """,
            (source_publication_id,),
        )
        clause_count = int(cursor.fetchone()[0])
    if not blocks or clause_count < 1:
        raise TerminologyError("source publication has no clauses or blocks")
    return TerminologySource(
        publication_run_id=source_publication_id,
        publication_sealed_fingerprint=str(publication[1]),
        seed_enrichment_run_id=source_seed_id,
        seed_output_sha256=str(seed_run[1]),
        seed_tags=seed_tags,
        seed_codes={
            key: tuple(value) for key, value in seed_codes.items()
        },
        blocks=blocks,
        publication_clause_count=clause_count,
    )


def _insert_material(
    connection: Any, material: TerminologyMaterial
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (GLOBAL_LOCK_KEY,),
        )
        cursor.execute(
            f"""
            SELECT tagging_run_id, sealed_fingerprint
            FROM {SCHEMA}.tagging_run
            WHERE input_fingerprint = %s
            """,
            (material.input_fingerprint,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if (
                str(existing[0]) != material.tagging_run_id
                or str(existing[1]) != material.sealed_fingerprint
            ):
                raise TerminologyError(
                    "terminology input collision or loader drift"
                )
            return True

        for row in material.rows["concept_registry"]:
            cursor.execute(
                f"""
                SELECT identity_basis, identity_version, created_from,
                       source_row_sha256
                FROM {SCHEMA}.concept_registry
                WHERE concept_id = %s
                """,
                (row["concept_id"],),
            )
            found = cursor.fetchone()
            expected = (
                row["identity_basis"],
                row["identity_version"],
                row["created_from"],
                row["source_row_sha256"],
            )
            if found is None:
                cursor.execute(
                    f"""
                    INSERT INTO {SCHEMA}.concept_registry (
                      concept_id, identity_basis, identity_version,
                      created_from, source_row_sha256
                    ) VALUES (%s,%s,%s,%s,%s)
                    """,
                    (
                        row["concept_id"],
                        row["identity_basis"],
                        row["identity_version"],
                        row["created_from"],
                        row["source_row_sha256"],
                    ),
                )
            elif tuple(found) != expected:
                raise TerminologyError("global concept identity collision")

        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.tagging_run (
              tagging_run_id, publication_run_id, seed_enrichment_run_id,
              alias_proposal_sha256, matcher_version, loader_version,
              offset_contract, alias_admission_policy, state,
              input_fingerprint, expected_counts, started_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,'loading',%s,%s::jsonb,now()
            )
            """,
            (
                material.tagging_run_id,
                material.publication_run_id,
                material.seed_enrichment_run_id,
                material.alias_proposal_sha256,
                MATCHER_VERSION,
                LOADER_VERSION,
                OFFSET_CONTRACT,
                ALIAS_ADMISSION_POLICY,
                material.input_fingerprint,
                json_text(material.expected_counts),
            ),
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.run_concept (
              tagging_run_id, concept_id, concept_type, canonical_label_zh,
              canonical_label_en, link_family, review_status, provenance,
              source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            [
                (
                    row["tagging_run_id"],
                    row["concept_id"],
                    row["concept_type"],
                    row["canonical_label_zh"],
                    row["canonical_label_en"],
                    row["link_family"],
                    row["review_status"],
                    json_text(row["provenance"]),
                    row["source_row_sha256"],
                )
                for row in material.rows["run_concept"]
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.concept_seed_tag_link (
              tagging_run_id, concept_id, legacy_tag_id,
              seed_enrichment_run_id, mapping_status, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s)
            """,
            [
                tuple(
                    row[key]
                    for key in (
                        "tagging_run_id",
                        "concept_id",
                        "legacy_tag_id",
                        "seed_enrichment_run_id",
                        "mapping_status",
                        "source_row_sha256",
                    )
                )
                for row in material.rows["concept_seed_tag_link"]
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.concept_alias (
              tagging_run_id, alias_id, concept_id, alias_text,
              normalized_alias, language_tag, alias_type, source_status,
              proposed_auto_match, match_rule, production_status,
              production_reason, ambiguity_note, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            [
                tuple(
                    row[key]
                    for key in (
                        "tagging_run_id",
                        "alias_id",
                        "concept_id",
                        "alias_text",
                        "normalized_alias",
                        "language_tag",
                        "alias_type",
                        "source_status",
                        "proposed_auto_match",
                        "match_rule",
                        "production_status",
                        "production_reason",
                        "ambiguity_note",
                        "source_row_sha256",
                    )
                )
                for row in material.rows["concept_alias"]
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.concept_external_code (
              tagging_run_id, concept_id, code_system, code, relation_type,
              review_status, public_safe, master_source, master_release,
              provenance, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            [
                (
                    row["tagging_run_id"],
                    row["concept_id"],
                    row["code_system"],
                    row["code"],
                    row["relation_type"],
                    row["review_status"],
                    row["public_safe"],
                    row["master_source"],
                    row["master_release"],
                    json_text(row["provenance"]),
                    row["source_row_sha256"],
                )
                for row in material.rows["concept_external_code"]
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.tagging_run_block_input (
              tagging_run_id, publication_run_id, clause_code, block_order,
              source_block_id, source_block_sha256, scan_status,
              candidate_match_count, admitted_match_count,
              blocked_match_count, source_row_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            [
                tuple(
                    row[key]
                    for key in (
                        "tagging_run_id",
                        "publication_run_id",
                        "clause_code",
                        "block_order",
                        "source_block_id",
                        "source_block_sha256",
                        "scan_status",
                        "candidate_match_count",
                        "admitted_match_count",
                        "blocked_match_count",
                        "source_row_sha256",
                    )
                )
                for row in material.rows["tagging_run_block_input"]
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {SCHEMA}.clause_occurrence (
              tagging_run_id, occurrence_id, publication_run_id, clause_code,
              block_order, source_block_id, source_block_sha256, concept_id,
              alias_id, start_scalar, end_scalar, start_utf8_byte,
              end_utf8_byte, matched_text, matched_text_sha256,
              occurrence_status, occurrence_reason, match_rule,
              source_row_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            [
                tuple(
                    row[key]
                    for key in (
                        "tagging_run_id",
                        "occurrence_id",
                        "publication_run_id",
                        "clause_code",
                        "block_order",
                        "source_block_id",
                        "source_block_sha256",
                        "concept_id",
                        "alias_id",
                        "start_scalar",
                        "end_scalar",
                        "start_utf8_byte",
                        "end_utf8_byte",
                        "matched_text",
                        "matched_text_sha256",
                        "occurrence_status",
                        "occurrence_reason",
                        "match_rule",
                        "source_row_sha256",
                    )
                )
                for row in material.rows["clause_occurrence"]
            ],
        )
        cursor.execute(
            f"""
            UPDATE {SCHEMA}.tagging_run
            SET state = 'sealed',
                verified_counts = %s::jsonb,
                verified_metrics = %s::jsonb,
                table_fingerprints = %s::jsonb,
                output_fingerprint = %s,
                sealed_fingerprint = %s,
                sealed_at = now()
            WHERE tagging_run_id = %s AND state = 'loading'
            """,
            (
                json_text(material.expected_counts),
                json_text(material.verified_metrics),
                json_text(material.table_fingerprints),
                material.output_fingerprint,
                material.sealed_fingerprint,
                material.tagging_run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise TerminologyError("terminology seal transition failed")
    return False


def _fresh_table_hashes(
    cursor: Any, table: str, run_id: str
) -> list[str]:
    if table == "concept_registry":
        cursor.execute(
            f"""
            SELECT registry.source_row_sha256
            FROM {SCHEMA}.concept_registry registry
            JOIN {SCHEMA}.run_concept concept
              ON concept.concept_id = registry.concept_id
            WHERE concept.tagging_run_id = %s
            ORDER BY registry.source_row_sha256
            """,
            (run_id,),
        )
    else:
        cursor.execute(
            f"""
            SELECT source_row_sha256
            FROM {SCHEMA}.{table}
            WHERE tagging_run_id = %s
            ORDER BY source_row_sha256
            """,
            (run_id,),
        )
    return [str(row[0]) for row in cursor.fetchall()]


def verify_terminology(
    tagging_run_id: str,
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    expected: TerminologyMaterial | None = None,
) -> dict[str, Any]:
    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT state, expected_counts, verified_counts,
                       verified_metrics, table_fingerprints,
                       output_fingerprint, sealed_fingerprint,
                       publication_run_id
                FROM {SCHEMA}.tagging_run
                WHERE tagging_run_id = %s
                """,
                (tagging_run_id,),
            )
            run = cursor.fetchone()
            if run is None or run[0] != "sealed":
                raise TerminologyError(
                    "fresh verification found no sealed terminology run"
                )
            counts: dict[str, int] = {}
            fingerprints: dict[str, str] = {}
            for table in _TABLES:
                hashes = _fresh_table_hashes(cursor, table, tagging_run_id)
                counts[table] = len(hashes)
                fingerprints[table] = row_set_fingerprint(hashes)
            cursor.execute(
                f"""
                SELECT
                  count(DISTINCT input.clause_code)::integer,
                  count(*)::integer,
                  count(*) FILTER (
                    WHERE input.scan_status = 'scanned_no_match'
                  )::integer,
                  count(*) FILTER (
                    WHERE input.scan_status = 'scanned_with_match'
                  )::integer
                FROM {SCHEMA}.tagging_run_block_input input
                WHERE input.tagging_run_id = %s
                """,
                (tagging_run_id,),
            )
            scan = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT production_status, count(*)::integer
                FROM {SCHEMA}.concept_alias
                WHERE tagging_run_id = %s
                GROUP BY production_status
                """,
                (tagging_run_id,),
            )
            alias_status = {
                str(status): int(count)
                for status, count in cursor.fetchall()
            }
            cursor.execute(
                f"""
                SELECT occurrence_status, count(*)::integer
                FROM {SCHEMA}.clause_occurrence
                WHERE tagging_run_id = %s
                GROUP BY occurrence_status
                """,
                (tagging_run_id,),
            )
            occurrence_status = {
                str(status): int(count)
                for status, count in cursor.fetchall()
            }
            cursor.execute(
                f"""
                SELECT count(*)::integer
                FROM {SCHEMA}.concept_seed_tag_link
                WHERE tagging_run_id = %s
                """,
                (tagging_run_id,),
            )
            seed_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*)::integer
                FROM {PUBLICATION_SCHEMA}.current_clause
                WHERE run_id = %s
                """,
                (str(run[7]),),
            )
            publication_clause_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*)::integer
                FROM {SCHEMA}.clause_occurrence occurrence
                JOIN {PUBLICATION_SCHEMA}.current_clause_block block
                  ON (block.run_id, block.clause_code, block.block_order) =
                     (occurrence.publication_run_id,
                      occurrence.clause_code, occurrence.block_order)
                WHERE occurrence.tagging_run_id = %s
                  AND (
                    substring(
                      block.raw_text
                      FROM occurrence.start_scalar + 1
                      FOR occurrence.end_scalar - occurrence.start_scalar
                    ) IS DISTINCT FROM occurrence.matched_text
                    OR
                    convert_from(
                      substring(
                        convert_to(block.raw_text, 'UTF8')
                        FROM occurrence.start_utf8_byte + 1
                        FOR occurrence.end_utf8_byte
                            - occurrence.start_utf8_byte
                      ),
                      'UTF8'
                    ) IS DISTINCT FROM occurrence.matched_text
                  )
                """,
                (tagging_run_id,),
            )
            offset_mismatch_count = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT count(*)::integer
                FROM {SCHEMA}.clause_occurrence left_occurrence
                JOIN {SCHEMA}.clause_occurrence right_occurrence
                  ON right_occurrence.tagging_run_id =
                     left_occurrence.tagging_run_id
                 AND right_occurrence.clause_code =
                     left_occurrence.clause_code
                 AND right_occurrence.block_order =
                     left_occurrence.block_order
                 AND right_occurrence.occurrence_id >
                     left_occurrence.occurrence_id
                 AND right_occurrence.start_scalar <
                     left_occurrence.end_scalar
                 AND left_occurrence.start_scalar <
                     right_occurrence.end_scalar
                WHERE left_occurrence.tagging_run_id = %s
                  AND left_occurrence.occurrence_status = 'admitted'
                  AND right_occurrence.occurrence_status = 'admitted'
                """,
                (tagging_run_id,),
            )
            admitted_overlap_count = int(cursor.fetchone()[0])
    metrics = {
        "publication_clause_count": publication_clause_count,
        "scanned_clause_count": int(scan[0]),
        "publication_block_count": counts["tagging_run_block_input"],
        "scanned_block_count": int(scan[1]),
        "scanned_no_match_block_count": int(scan[2]),
        "scanned_with_match_block_count": int(scan[3]),
        "seed_tag_count": seed_count,
        "admitted_alias_count": alias_status.get("admitted", 0),
        "candidate_alias_count": alias_status.get("candidate", 0),
        "blocked_alias_count": alias_status.get("blocked", 0),
        "admitted_occurrence_count": occurrence_status.get("admitted", 0),
        "candidate_occurrence_count": occurrence_status.get("candidate", 0),
        "blocked_occurrence_count": occurrence_status.get("blocked", 0),
    }
    output_fingerprint = object_fingerprint(
        {
            "counts": counts,
            "metrics": metrics,
            "table_fingerprints": fingerprints,
        }
    )
    if counts != run[1] or counts != run[2]:
        raise TerminologyError(
            "fresh terminology counts differ from sealed receipt"
        )
    if metrics != run[3]:
        raise TerminologyError(
            "fresh terminology metrics differ from sealed receipt"
        )
    if fingerprints != run[4] or output_fingerprint != run[5]:
        raise TerminologyError(
            "fresh terminology fingerprint differs from sealed receipt"
        )
    if offset_mismatch_count or admitted_overlap_count:
        raise TerminologyError(
            "fresh occurrence offset/overlap verification failed"
        )
    if expected is not None and (
        counts != expected.expected_counts
        or metrics != expected.verified_metrics
        or fingerprints != expected.table_fingerprints
        or output_fingerprint != expected.output_fingerprint
        or str(run[6]) != expected.sealed_fingerprint
    ):
        raise TerminologyError(
            "fresh terminology projection differs from prepared material"
        )
    return {
        "schema": "nhi-rule-history/terminology-verification/v1",
        "tagging_run_id": tagging_run_id,
        "publication_run_id": str(run[7]),
        "state": "sealed",
        "counts": counts,
        "metrics": metrics,
        "table_fingerprints": fingerprints,
        "output_fingerprint": output_fingerprint,
        "sealed_fingerprint": str(run[6]),
        "offset_mismatch_count": offset_mismatch_count,
        "admitted_overlap_count": admitted_overlap_count,
    }


def load_terminology(
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    alias_proposal_path: Path = DEFAULT_ALIAS_PROPOSAL,
    publication_run_id: str | None = None,
    seed_enrichment_run_id: str | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    """Read canonical PG, scan every block, seal, verify, and activate."""

    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        source = _load_source(
            connection,
            publication_run_id=publication_run_id,
            seed_enrichment_run_id=seed_enrichment_run_id,
        )
    material = prepare_terminology(
        source, alias_proposal_path=alias_proposal_path
    )
    with connector(dsn) as connection:
        already_loaded = _insert_material(connection, material)
        if activate:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT tagging_run_id
                    FROM {SCHEMA}.tagging_run_activation
                    ORDER BY activation_id DESC
                    LIMIT 1
                    """
                )
                active = cursor.fetchone()
                active_id = str(active[0]) if active is not None else None
                if active_id != material.tagging_run_id:
                    cursor.execute(
                        f"""
                        INSERT INTO {SCHEMA}.tagging_run_activation (
                          tagging_run_id, prior_tagging_run_id,
                          activation_reason
                        ) VALUES (%s,%s,%s)
                        """,
                        (
                            material.tagging_run_id,
                            active_id,
                            (
                                "initial_release"
                                if active_id is None
                                else "supersede"
                            ),
                        ),
                    )
    result = verify_terminology(
        material.tagging_run_id,
        conninfo=dsn,
        connect=connector,
        expected=material,
    )
    result["already_loaded"] = already_loaded
    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT tagging_run_id
                FROM {SCHEMA}.v_active_tagging_run
                """
            )
            active = cursor.fetchone()
    result["active"] = (
        active is not None
        and str(active[0]) == material.tagging_run_id
    )
    return result


def preview_terminology(
    *,
    conninfo: str | None = None,
    connect: Callable[[str], Any] | None = None,
    alias_proposal_path: Path = DEFAULT_ALIAS_PROPOSAL,
    publication_run_id: str | None = None,
    seed_enrichment_run_id: str | None = None,
) -> dict[str, Any]:
    """Run the complete deterministic scan without writing PostgreSQL."""

    connector = connect or _default_connect
    dsn = conninfo if conninfo is not None else os.environ.get(DSN_ENV, "")
    with connector(dsn) as connection:
        source = _load_source(
            connection,
            publication_run_id=publication_run_id,
            seed_enrichment_run_id=seed_enrichment_run_id,
        )
    material = prepare_terminology(
        source, alias_proposal_path=alias_proposal_path
    )
    return {
        "schema": "nhi-rule-history/terminology-preview/v1",
        "tagging_run_id": material.tagging_run_id,
        "publication_run_id": material.publication_run_id,
        "seed_enrichment_run_id": material.seed_enrichment_run_id,
        "counts": material.expected_counts,
        "metrics": material.verified_metrics,
        "table_fingerprints": material.table_fingerprints,
        "input_fingerprint": material.input_fingerprint,
        "output_fingerprint": material.output_fingerprint,
        "sealed_fingerprint": material.sealed_fingerprint,
        "write_performed": False,
    }
