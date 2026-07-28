"""Conservative whole-versus-split current-anchor occurrence preflight.

This compares only source-local designation/header occurrences emitted by the
generic ODT parser.  It deliberately does not call the result clause parity:
full-clause reconstruction, stable identity, appendices, and legal-effective
time remain separate gates.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    file_sha256,
    sha256_bytes,
    write_json,
)


REPORT_SCHEMA = (
    "nhi-rule-history/current-anchor-occurrence-parity-preflight/v1"
)
NORMALIZER_VERSION = "nfkc-remove-whitespace-terminal-designation-punct/1.0.0"
WHOLE_LABEL_PREFIX = "最新版藥品給付規定內容(整份帶走)"
OCCURRENCE_SCHEMA = "nhi-rule-history/occurrence-candidate/v2"


def _iter_occurrence_rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        raise ContractError("occurrence candidate file is missing")
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise ContractError(
                    f"{path.name}:{line_number}: row is not an object"
                )
            if row.get("schema") != OCCURRENCE_SCHEMA:
                raise ContractError(
                    f"{path.name}:{line_number}: wrong schema"
                )
            assert_public_value(row)
            yield row


def _normalize_designation(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = "".join(normalized.split())
    normalized = re.sub(r"(?<=\d)[.:。：]+$", "", normalized)
    if not normalized:
        raise ContractError("anchor occurrence has an empty designation")
    return normalized


def _normalize_header(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = "".join(normalized.split())
    if not normalized:
        raise ContractError("anchor occurrence has empty header text")
    return normalized


def _counter_rows(
    counter: Counter[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [
        {
            "designation": designation,
            "normalized_header": header,
            "count": count,
            "key_sha256": sha256_bytes(
                f"{designation}\x1f{header}".encode("utf-8")
            ),
        }
        for (designation, header), count in sorted(counter.items())
    ]


def analyze_current_anchor_occurrence_parity(
    rows: Iterable[Mapping[str, Any]],
    *,
    parse_run_id: str | None = None,
    occurrence_file_sha256: str | None = None,
) -> dict[str, Any]:
    whole: Counter[tuple[str, str]] = Counter()
    split: Counter[tuple[str, str]] = Counter()
    observed_run_ids: set[str] = set()

    for row in rows:
        labels = row.get("source_labels")
        if (
            not isinstance(labels, list)
            or len(labels) != 1
            or not isinstance(labels[0], str)
            or not labels[0].strip()
        ):
            raise ContractError(
                "anchor occurrence must have exactly one source label"
            )
        row_run_id = row.get("parse_run_id")
        if not isinstance(row_run_id, str) or not row_run_id:
            raise ContractError("anchor occurrence has no parse_run_id")
        observed_run_ids.add(row_run_id)
        key = (
            _normalize_designation(row.get("designation_text")),
            _normalize_header(row.get("normalized_search_text")),
        )
        if labels[0].startswith(WHOLE_LABEL_PREFIX):
            whole[key] += 1
        else:
            split[key] += 1

    if len(observed_run_ids) != 1:
        raise ContractError(
            "anchor occurrence input must contain exactly one parse run"
        )
    actual_run_id = next(iter(observed_run_ids))
    if parse_run_id is not None and actual_run_id != parse_run_id:
        raise ContractError("anchor occurrence parse_run_id mismatch")
    if not whole or not split:
        raise ContractError(
            "anchor preflight requires whole and split occurrences"
        )

    matched = whole & split
    whole_only = whole - split
    split_only = split - whole
    report = {
        "schema": REPORT_SCHEMA,
        "parse_run_id": actual_run_id,
        "normalizer_version": NORMALIZER_VERSION,
        "occurrence_file_sha256": occurrence_file_sha256,
        "counts": {
            "whole_occurrences": sum(whole.values()),
            "split_occurrences": sum(split.values()),
            "matched_occurrences": sum(matched.values()),
            "whole_only_occurrences": sum(whole_only.values()),
            "split_only_occurrences": sum(split_only.values()),
            "whole_unique_keys": len(whole),
            "split_unique_keys": len(split),
        },
        "whole_only": _counter_rows(whole_only),
        "split_only": _counter_rows(split_only),
        "claims": {
            "occurrence_header_multiset_equal": whole == split,
            "full_clause_text_compared": False,
            "whole_split_clause_parity_complete": False,
            "legal_effective_date_inferred": False,
            "canonical_history_written": False,
        },
        "statement": (
            "Source-local designation/header occurrence preflight only; "
            "mismatches require full-clause and source-layout adjudication."
        ),
    }
    report["status"] = (
        "matched_preflight" if whole == split else "mismatch_detected"
    )
    return report


def current_anchor_occurrence_parity(
    stage_dir: Path,
    *,
    output: Path | None = None,
) -> dict[str, Any]:
    manifest_path = stage_dir / "structural-manifest.json"
    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ContractError("structural manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "passed"
        or not isinstance(manifest.get("parse_run_id"), str)
    ):
        raise ContractError("structural manifest is not a passed parse run")
    occurrence_path = stage_dir / "occurrence-candidates.jsonl"
    expected_entries = [
        entry
        for entry in manifest.get("files", [])
        if isinstance(entry, dict)
        and entry.get("filename") == occurrence_path.name
    ]
    if len(expected_entries) != 1:
        raise ContractError(
            "structural manifest has no unique occurrence file entry"
        )
    actual_sha256 = file_sha256(occurrence_path)
    if expected_entries[0].get("sha256") != actual_sha256:
        raise ContractError("occurrence file hash differs from manifest")
    report = analyze_current_anchor_occurrence_parity(
        _iter_occurrence_rows(occurrence_path),
        parse_run_id=manifest["parse_run_id"],
        occurrence_file_sha256=actual_sha256,
    )
    if output is not None:
        write_json(output, report)
    return report
