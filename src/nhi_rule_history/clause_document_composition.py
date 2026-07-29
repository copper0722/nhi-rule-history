"""Verify complete-expression composition across API, JSONL, and SQLite.

This gate does not infer legal completeness.  It checks that an already
admitted ``verified_composite`` Expression remains a lossless, content-addressed
assembly of source-bound blocks and that every public projection preserves the
same provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

from nhi_rule_history.clause_document_portable import (
    _canonical_json,
    _read_jsonl,
    build_sqlite,
)
from nhi_rule_history.contracts import canonical_json_bytes


RECEIPT_SCHEMA = "nhi-rule-history/clause-composition-verification/v1"
API_CONTRACT = "nhi-reimbursement-rules/announced-decision/v1"
DOCUMENT_CONTRACT = "nhi-reimbursement-rules/clause-document/v2"
ASSEMBLY_SEPARATOR = "\n\n"


class ClauseCompositionError(RuntimeError):
    """A completeness-provenance invariant failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ClauseCompositionError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _fingerprint(value: Any) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


def _ordered(
    rows: Iterable[Mapping[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: int(row[key]))


def _manifest_rows(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "block_order": block["block_order"],
            "origin_lane": block["origin_lane"],
            "source_artifact_sha256": block["source_artifact_sha256"],
            "source_block_id": block["source_block_id"],
            "raw_text_sha256": block["raw_text_sha256"],
            "patch_component_order": block["patch_component_order"],
            "predecessor_publication_run_id": block[
                "predecessor_publication_run_id"
            ],
            "predecessor_block_order": block["predecessor_block_order"],
            "render_locator": block["render_locator"],
        }
        for block in blocks
    ]


def _assembled_ranges(
    blocks: Iterable[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    chunks: list[str] = []
    ranges: list[dict[str, Any]] = []
    scalar_cursor = 0
    byte_cursor = 0
    for ordinal, block in enumerate(blocks):
        raw_text = str(block["raw_text"])
        prefix = "" if ordinal == 0 else ASSEMBLY_SEPARATOR
        chunk = prefix + raw_text
        scalar_end = scalar_cursor + len(chunk)
        byte_end = byte_cursor + len(chunk.encode("utf-8"))
        ranges.append(
            {
                "assembly_ordinal": ordinal,
                "component_role": block["origin_lane"],
                "source_artifact_sha256": block["source_artifact_sha256"],
                "source_block_id": block["source_block_id"],
                "source_locator": block["source_locator"],
                "exact_component_text_sha256": block["raw_text_sha256"],
                "assembled_scalar_start": scalar_cursor,
                "assembled_scalar_end": scalar_end,
                "assembled_utf8_byte_start": byte_cursor,
                "assembled_utf8_byte_end": byte_end,
                "separator_before": prefix,
            }
        )
        chunks.append(chunk)
        scalar_cursor = scalar_end
        byte_cursor = byte_end
    return "".join(chunks), ranges


def _verify_source_spans(blocks: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    nonempty = 0
    empty = 0
    span_count = 0
    for block in blocks:
        raw_text = str(block["raw_text"])
        spans = _ordered(block.get("source_spans") or [], "scalar_start")
        if not raw_text:
            _require(not spans, "empty source block owns a fabricated span")
            empty += 1
            continue
        nonempty += 1
        scalar_cursor = 0
        byte_cursor = 0
        replay: list[str] = []
        for span in spans:
            text = str(span["exact_span_text"])
            _require(
                int(span["scalar_start"]) == scalar_cursor,
                "source span scalar coverage has a gap or overlap",
            )
            _require(
                int(span["utf8_byte_start"]) == byte_cursor,
                "source span byte coverage has a gap or overlap",
            )
            scalar_cursor = int(span["scalar_end"])
            byte_cursor = int(span["utf8_byte_end"])
            _require(
                raw_text[int(span["scalar_start"]) : scalar_cursor] == text,
                "source span scalar replay drifted",
            )
            replay.append(text)
        _require("".join(replay) == raw_text, "source span replay is incomplete")
        _require(scalar_cursor == len(raw_text), "source scalar tail is uncovered")
        _require(
            byte_cursor == len(raw_text.encode("utf-8")),
            "source UTF-8 byte tail is uncovered",
        )
        span_count += len(spans)
    return {
        "nonempty_component_count": nonempty,
        "empty_physical_component_count": empty,
        "source_span_count": span_count,
    }


def _load_api_bytes(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as response:
            return response.read()
    return Path(source).read_bytes()


def verify_composition(
    api_payload: Mapping[str, Any],
    *,
    api_sha256: str,
    jsonl_dir: Path,
    sqlite_output: Path,
) -> dict[str, Any]:
    _require(api_payload.get("contract") == API_CONTRACT, "API contract drifted")
    patches = list(api_payload.get("patches") or [])
    _require(len(patches) == 1, "verification packet must resolve one patch")
    patch = patches[0]
    _require(patch.get("clause_code") == "2.6.1", "unexpected clause")

    composed = dict(patch.get("composed_clause") or {})
    document = dict(composed.get("document_structure") or {})
    _require(
        document.get("contract") == DOCUMENT_CONTRACT,
        "normalized document contract drifted",
    )
    expressions = {
        str(expression["reader_state"]): dict(expression)
        for expression in document.get("expressions") or []
    }
    _require(
        set(expressions)
        == {"current_effective_complete", "future_announced_complete"},
        "complete-expression selector is ambiguous",
    )
    current = expressions["current_effective_complete"]
    future = expressions["future_announced_complete"]
    _require(
        current["expression_completeness"] == "source_complete",
        "current Expression is not source_complete",
    )
    _require(
        future["expression_completeness"] == "verified_composite",
        "future Expression is not verified_composite",
    )

    blocks = _ordered(composed.get("blocks") or [], "block_order")
    _require(
        [int(block["block_order"]) for block in blocks]
        == list(range(len(blocks))),
        "composition block order is incomplete",
    )
    for block in blocks:
        _require(
            _sha256_text(str(block["raw_text"])) == block["raw_text_sha256"],
            "composition block text hash drifted",
        )
        for key in (
            "source_artifact_sha256",
            "source_block_id",
            "source_locator",
            "origin_lane",
        ):
            _require(block.get(key) not in (None, ""), f"component lacks {key}")

    manifest_sha256 = str(composed["composition_manifest_sha256"])
    _require(
        _fingerprint(_manifest_rows(blocks)) == manifest_sha256,
        "composition manifest hash does not recompute",
    )
    manifest_id = f"sha256:{manifest_sha256}"
    _require(
        future.get("composition_manifest_sha256") == manifest_sha256,
        "future Expression does not bind the composition manifest",
    )

    assembled_text, assembled_ranges = _assembled_ranges(blocks)
    _require(
        assembled_text == composed["composed_text"],
        "deterministic component assembly drifted",
    )
    _require(
        assembled_text == future["exact_text"],
        "assembled text does not equal future Expression",
    )
    _require(
        _sha256_text(assembled_text) == composed["composed_text_sha256"],
        "composed text hash drifted",
    )
    _require(
        _sha256_text(assembled_text) == future["exact_text_sha256"],
        "future Expression hash drifted",
    )
    _require(
        assembled_ranges[-1]["assembled_scalar_end"] == len(assembled_text),
        "assembled scalar coverage is incomplete",
    )
    _require(
        assembled_ranges[-1]["assembled_utf8_byte_end"]
        == len(assembled_text.encode("utf-8")),
        "assembled UTF-8 byte coverage is incomplete",
    )

    future_blocks = _ordered(future.get("source_blocks") or [], "block_order")
    _require(len(future_blocks) == len(blocks), "v25 future blocks are incomplete")
    shared_pairs = (
        ("block_order", "block_order"),
        ("source_lane", "origin_lane"),
        ("source_artifact_sha256", "source_artifact_sha256"),
        ("source_block_id", "source_block_id"),
        ("source_locator", "source_locator"),
        ("render_locator", "render_locator"),
        ("raw_text", "raw_text"),
        ("raw_text_sha256", "raw_text_sha256"),
    )
    for document_block, component in zip(future_blocks, blocks, strict=True):
        for document_key, component_key in shared_pairs:
            _require(
                document_block[document_key] == component[component_key],
                f"v25/source component mismatch: {document_key}",
            )
    span_receipt = _verify_source_spans(future_blocks)

    amendment_blocks = [
        block for block in blocks if block["origin_lane"] == "amendment_exact"
    ]
    inherited_blocks = [
        block
        for block in blocks
        if block["origin_lane"] == "predecessor_inherited"
    ]
    _require(len(amendment_blocks) == 336, "amendment block count drifted")
    _require(len(inherited_blocks) == 70, "inherited block count drifted")
    _require(
        [int(block["block_order"]) for block in amendment_blocks]
        == list(range(336)),
        "amendment components are not the first 336 blocks",
    )
    _require(
        [int(block["block_order"]) for block in inherited_blocks]
        == list(range(336, 406)),
        "inherited components are not the final 70 blocks",
    )

    notice_components = _ordered(patch.get("components") or [], "component_order")
    _require(len(notice_components) == 337, "notice component count drifted")
    omitted_marker = notice_components[-1]
    _require(
        omitted_marker["component_role"] == "omitted_remainder_marker"
        and omitted_marker["raw_text"] == "(以下略)",
        "official omission marker is not explicit",
    )
    future_source_ids = {str(block["source_block_id"]) for block in blocks}
    _require(
        omitted_marker["source_block_id"] not in future_source_ids,
        "omission marker was laundered into the complete Expression",
    )
    _require(
        all(
            block["origin_lane"] == "predecessor_inherited"
            for block in blocks[336:]
        ),
        "notice-omitted Table 2 owns amendment-source blocks",
    )

    current_blocks = {
        int(block["block_order"]): block
        for block in current.get("source_blocks") or []
    }
    predecessor_run_id = str(composed["predecessor_publication_run_id"])
    predecessor_artifact_sha256 = str(
        composed["predecessor_source_artifact_sha256"]
    )
    for inherited in inherited_blocks:
        predecessor_order = int(inherited["predecessor_block_order"])
        prior = current_blocks.get(predecessor_order)
        _require(prior is not None, "inherited block lacks predecessor block")
        _require(
            inherited["predecessor_publication_run_id"] == predecessor_run_id,
            "inherited block binds a different predecessor run",
        )
        _require(
            inherited["source_artifact_sha256"]
            == predecessor_artifact_sha256
            == prior["source_artifact_sha256"],
            "inherited block binds a different predecessor artifact",
        )
        for key in ("source_block_id", "raw_text", "raw_text_sha256"):
            _require(inherited[key] == prior[key], f"inherited {key} drifted")

    history = dict(document.get("history_transition") or {})
    hunks = list(history.get("hunks") or [])
    _require(len(hunks) == 1, "exact Work-level hunk count drifted")
    segments = _ordered(hunks[0].get("segments") or [], "segment_order")
    _require(len(segments) == 7, "exact diff segment count drifted")
    old_replay = "".join(str(segment.get("old_text") or "") for segment in segments)
    new_replay = "".join(str(segment.get("new_text") or "") for segment in segments)
    _require(old_replay == current["exact_text"], "diff old replay drifted")
    _require(new_replay == future["exact_text"], "diff new replay drifted")

    jsonl_dir = Path(jsonl_dir)
    jsonl_expressions = _read_jsonl(
        jsonl_dir / "clause_document_expression.jsonl"
    )
    jsonl_future = next(
        row
        for row in jsonl_expressions
        if row["reader_state"] == "future_announced_complete"
    )
    jsonl_current = next(
        row
        for row in jsonl_expressions
        if row["reader_state"] == "current_effective_complete"
    )
    for projected, api_expression in (
        (jsonl_current, current),
        (jsonl_future, future),
    ):
        for key in (
            "expression_id",
            "expression_completeness",
            "composition_manifest_sha256",
            "exact_text_sha256",
            "completeness_receipt_sha256",
        ):
            _require(
                projected[key] == api_expression[key],
                f"API/JSONL Expression mismatch: {key}",
            )

    jsonl_source_blocks = _read_jsonl(
        jsonl_dir / "clause_document_source_block.jsonl"
    )
    jsonl_future_blocks = _ordered(
        (
            row
            for row in jsonl_source_blocks
            if row["expression_id"] == jsonl_future["expression_id"]
        ),
        "block_order",
    )
    _require(
        jsonl_future_blocks == [
            {key: block[key] for key in jsonl_future_blocks[0]}
            for block in future_blocks
        ],
        "API/JSONL component provenance drifted",
    )
    normalization = dict(document.get("normalization") or {})
    jsonl_normalizations = _read_jsonl(
        jsonl_dir / "clause_document_normalization_run.jsonl"
    )
    _require(
        len(jsonl_normalizations) == 1,
        "JSONL normalization run is ambiguous",
    )
    for key in (
        "normalization_run_id",
        "output_fingerprint",
        "sealed_fingerprint",
    ):
        _require(
            jsonl_normalizations[0][key] == normalization[key],
            f"API/JSONL normalization mismatch: {key}",
        )
    jsonl_diff_runs = _read_jsonl(
        jsonl_dir / "clause_document_diff_run.jsonl"
    )
    _require(len(jsonl_diff_runs) == 1, "JSONL exact diff run is ambiguous")
    for key in ("diff_run_id", "output_fingerprint", "sealed_fingerprint"):
        _require(
            jsonl_diff_runs[0][key] == history[key],
            f"API/JSONL exact diff mismatch: {key}",
        )

    sqlite_receipt = build_sqlite(jsonl_dir, sqlite_output)
    with sqlite3.connect(sqlite_output) as connection:
        sqlite_expressions = [
            json.loads(row[0])
            for row in connection.execute(
                'SELECT row_json FROM "clause_document_expression"'
            )
        ]
        sqlite_blocks = [
            json.loads(row[0])
            for row in connection.execute(
                'SELECT row_json FROM "clause_document_source_block"'
            )
        ]
        _require(
            sorted(map(_canonical_json, sqlite_expressions))
            == sorted(map(_canonical_json, jsonl_expressions)),
            "JSONL/SQLite Expression rows drifted",
        )
        _require(
            sorted(map(_canonical_json, sqlite_blocks))
            == sorted(map(_canonical_json, jsonl_source_blocks)),
            "JSONL/SQLite component rows drifted",
        )

    return {
        "schema": RECEIPT_SCHEMA,
        "status": "passed",
        "clause_code": patch["clause_code"],
        "effective_from": composed["effective_from"],
        "api": {
            "contract": api_payload["contract"],
            "payload_sha256": api_sha256,
            "document_contract": document["contract"],
        },
        "current_expression": {
            "expression_id": current["expression_id"],
            "completeness": current["expression_completeness"],
            "exact_text_sha256": current["exact_text_sha256"],
            "source_artifact_sha256": current_blocks[0][
                "source_artifact_sha256"
            ],
        },
        "future_expression": {
            "expression_id": future["expression_id"],
            "completeness": future["expression_completeness"],
            "exact_text_sha256": future["exact_text_sha256"],
            "composition_manifest_id": manifest_id,
            "composition_manifest_sha256": manifest_sha256,
            "completeness_receipt_sha256": future[
                "completeness_receipt_sha256"
            ],
        },
        "composition": {
            "rule_version": composed["composition_rule_version"],
            "separator": "\\n\\n",
            "component_count": len(blocks),
            "amendment_exact_count": len(amendment_blocks),
            "predecessor_inherited_count": len(inherited_blocks),
            "component_binding_fingerprint": _fingerprint(assembled_ranges),
            "scalar_length": len(assembled_text),
            "utf8_byte_length": len(assembled_text.encode("utf-8")),
            "scalar_and_utf8_exact_once_coverage": "passed",
            **span_receipt,
        },
        "omitted_remainder": {
            "notice_marker_source_block_id": omitted_marker["source_block_id"],
            "notice_marker_text_sha256": omitted_marker["raw_text_sha256"],
            "marker_excluded_from_future_expression": True,
            "notice_table2_body_source_span_count": 0,
            "inherited_table2_source_artifact_sha256": (
                predecessor_artifact_sha256
            ),
            "predecessor_publication_run_id": predecessor_run_id,
            "predecessor_block_range": [2, 71],
        },
        "normalization": {
            "normalization_run_id": normalization["normalization_run_id"],
            "output_fingerprint": normalization["output_fingerprint"],
            "sealed_fingerprint": normalization["sealed_fingerprint"],
        },
        "exact_diff": {
            "diff_run_id": history["diff_run_id"],
            "output_fingerprint": history["output_fingerprint"],
            "sealed_fingerprint": history["sealed_fingerprint"],
            "hunk_count": len(hunks),
            "segment_count": len(segments),
            "segment_kinds": [segment["segment_kind"] for segment in segments],
            "old_and_new_exact_replay": "passed",
        },
        "projection_parity": {
            "jsonl_manifest_fingerprint": json.loads(
                (jsonl_dir / "manifest.json").read_text(encoding="utf-8")
            )["manifest_fingerprint"],
            "jsonl_expression_count": len(jsonl_expressions),
            "jsonl_source_block_count": len(jsonl_source_blocks),
            "api_jsonl_component_provenance": "passed",
            "sqlite": sqlite_receipt,
        },
        "reader_provenance": {
            "public_note": composed["public_note"],
            "claims_source_exact_from_amendment_attachment": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-json", required=True)
    parser.add_argument("--jsonl-dir", type=Path, required=True)
    parser.add_argument("--sqlite-output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    api_bytes = _load_api_bytes(args.api_json)
    payload = json.loads(api_bytes)
    result = verify_composition(
        payload,
        api_sha256=_sha256_bytes(api_bytes),
        jsonl_dir=args.jsonl_dir,
        sqlite_output=args.sqlite_output,
    )
    output = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if args.receipt:
        args.receipt.write_text(output, encoding="utf-8", newline="\n")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
