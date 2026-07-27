"""Generic ODT structural stage over verified v2 acquisition bytes.

This adapter deliberately reuses the sealed v1, lossless ODT walker without
reusing the v1 assumption that the corpus contains exactly fourteen annual
files.  It emits source-local observations only.  An occurrence candidate is
not a stable rule identity, legal event, effective date, current version, or
predecessor/successor relationship.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    manifest_file_entry,
    resolve_run_relative,
    sha256_bytes,
    unique_rows,
    utc_now,
    write_json,
)
from nhi_rule_history.raw.verify import verify_raw


STRUCTURAL_RUN_SCHEMA = "nhi-rule-history/structural-run/v2"
STRUCTURAL_BLOCK_SCHEMA = "nhi-rule-history/structural-block/v2"
OCCURRENCE_CANDIDATE_SCHEMA = "nhi-rule-history/occurrence-candidate/v2"
PARSE_ISSUE_SCHEMA = "nhi-rule-history/parse-issue/v2"
STRUCTURAL_MANIFEST_SCHEMA = "nhi-rule-history/structural-manifest/v2"
PARSER_ADAPTER_VERSION = "nhi-rule-history-generic-odt/2.0.0"

STRUCTURAL_FILES = (
    "structural-blocks.jsonl",
    "occurrence-candidates.jsonl",
    "parse-issues.jsonl",
)

NON_CLAIM = (
    "Source-local structural observation only; not stable rule identity, "
    "legal effective date, legal event, current version, predecessor/successor, "
    "or diff."
)


def _repository_root() -> Path:
    # <root>/src/nhi_rule_history/parsers/odt.py
    return Path(__file__).resolve().parents[3]


def _legacy_parser() -> Any:
    """Load the accepted v1 walker from the checkout that owns this package."""

    script_dir = _repository_root() / ".script" / "nhi-rule-history"
    if not script_dir.is_dir():
        raise ContractError("sealed v1 ODT parser directory is missing")
    script_text = str(script_dir)
    if script_text not in sys.path:
        sys.path.insert(0, script_text)
    return importlib.import_module("occurrence_extract")


def _install_v2_list_walker(parser_module: Any) -> None:
    """Teach the sealed walker about LibreOffice ``text:list-header``.

    The v1 annual corpus did not exercise this legal ODT structure.  A 2023
    amendment attachment does.  We patch the imported module in memory, leaving
    the sealed v1 source and fingerprint untouched.
    """

    if getattr(parser_module, "_nhi_v2_list_header_installed", False):
        return

    def walk_list(
        list_el: Any,
        state: Any,
        *,
        container: str,
        table_ctx: dict[str, Any] | None,
        list_depth: int,
        in_frame: bool,
        nested_table_depth: int,
        in_index_context: bool,
    ) -> None:
        for item in list_el:
            item_name = parser_module.cp.local_name(item.tag)
            if item_name not in {"list-item", "list-header"}:
                if item_name in ("p", "h"):
                    parser_module._emit_text_block(
                        state,
                        item,
                        container=container,
                        table_ctx=table_ctx,
                        list_depth=list_depth,
                        in_frame=in_frame,
                        nested_table_depth=nested_table_depth,
                        in_index_context=in_index_context,
                    )
                elif item_name == "list":
                    walk_list(
                        item,
                        state,
                        container=container,
                        table_ctx=table_ctx,
                        list_depth=list_depth + 1,
                        in_frame=in_frame,
                        nested_table_depth=nested_table_depth,
                        in_index_context=in_index_context,
                    )
                continue
            for child in item:
                child_name = parser_module.cp.local_name(child.tag)
                if child_name in ("p", "h"):
                    parser_module._emit_text_block(
                        state,
                        child,
                        container=container,
                        table_ctx=table_ctx,
                        list_depth=list_depth,
                        in_frame=in_frame,
                        nested_table_depth=nested_table_depth,
                        in_index_context=in_index_context,
                    )
                elif child_name == "list":
                    walk_list(
                        child,
                        state,
                        container=container,
                        table_ctx=table_ctx,
                        list_depth=list_depth + 1,
                        in_frame=in_frame,
                        nested_table_depth=nested_table_depth,
                        in_index_context=in_index_context,
                    )
                elif child_name == "table":
                    parser_module._walk_table(
                        child,
                        state,
                        nested_table_depth=(
                            nested_table_depth + (1 if table_ctx else 0)
                        ),
                        in_frame=in_frame,
                        in_index_context=in_index_context,
                    )

    def walk_cell_content(
        cell: Any,
        state: Any,
        *,
        table_ctx_base: dict[str, Any],
        nested_table_depth: int,
        in_frame: bool,
        in_index_context: bool,
    ) -> None:
        if not parser_module._cell_has_structural_descendant(cell):
            parser_module._emit_empty_table_cell_block(
                state,
                cell,
                table_ctx=dict(table_ctx_base),
                in_frame=in_frame,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
            return

        paragraph_index = 0

        def emit(element: Any, depth: int) -> None:
            nonlocal paragraph_index
            context = dict(table_ctx_base)
            context["para_index_in_cell"] = paragraph_index
            if depth:
                context["list_depth"] = depth
            parser_module._emit_text_block(
                state,
                element,
                container="table_cell",
                table_ctx=context,
                list_depth=depth,
                in_frame=in_frame,
                nested_table_depth=nested_table_depth,
                in_index_context=in_index_context,
            )
            paragraph_index += 1

        def walk_cell_list(list_element: Any, depth: int) -> None:
            for item in list_element:
                item_name = parser_module.cp.local_name(item.tag)
                if item_name in {"list-item", "list-header"}:
                    for child in item:
                        child_name = parser_module.cp.local_name(child.tag)
                        if child_name in ("p", "h"):
                            emit(child, depth)
                        elif child_name == "list":
                            walk_cell_list(child, depth + 1)
                        elif child_name == "table":
                            parser_module._walk_table(
                                child,
                                state,
                                nested_table_depth=nested_table_depth + 1,
                                in_frame=in_frame,
                                in_index_context=in_index_context,
                            )
                elif item_name in ("p", "h"):
                    emit(item, depth)

        for child in cell:
            child_name = parser_module.cp.local_name(child.tag)
            if child_name in ("p", "h"):
                emit(child, 0)
            elif child_name == "list":
                walk_cell_list(child, 1)
            elif child_name == "table":
                state.nested_table_count += 1
                parser_module._walk_table(
                    child,
                    state,
                    nested_table_depth=nested_table_depth + 1,
                    in_frame=in_frame,
                    in_index_context=in_index_context,
                )
            elif child_name in parser_module._FRAME_CONTAINER_NAMES:
                parser_module._walk_frame(
                    child,
                    state,
                    container="table_cell",
                    table_ctx=dict(table_ctx_base),
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )
            elif any(
                parser_module.cp.local_name(descendant.tag) in ("p", "h")
                for descendant in child.iter()
                if descendant is not child
            ):
                parser_module._walk_nested_inside_text_element(
                    child,
                    state,
                    container="table_cell",
                    table_ctx=dict(table_ctx_base),
                    list_depth=0,
                    in_frame=in_frame,
                    nested_table_depth=nested_table_depth,
                    in_index_context=in_index_context,
                )

    parser_module._walk_list = walk_list
    parser_module._walk_cell_content = walk_cell_content
    parser_module._nhi_v2_list_header_installed = True


def _source_row_sha(row: Mapping[str, Any]) -> str:
    clean = {key: value for key, value in row.items() if key != "source_row_sha256"}
    return sha256_bytes(canonical_json_bytes(clean))


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    enriched = dict(row)
    enriched["source_row_sha256"] = _source_row_sha(enriched)
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(enriched))


def _is_odt(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            if "content.xml" not in archive.namelist():
                return False
            if "mimetype" not in archive.namelist():
                return False
            return (
                archive.read("mimetype").strip()
                == b"application/vnd.oasis.opendocument.text"
            )
    except (OSError, zipfile.BadZipFile):
        return False


def _parser_bundle_sha(parser_module: Any) -> str:
    files = (
        Path(__file__),
        Path(parser_module.__file__).resolve(),
        Path(parser_module.cp.__file__).resolve(),
    )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _artifact_resources(
    resources: Mapping[str, Mapping[str, Any]],
    links_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for link in iter_jsonl(links_path):
        resource_id = link["resource_id"]
        resource = resources.get(resource_id)
        if resource is None:
            raise ContractError("structural input link references unknown resource")
        key = (link["artifact_sha256"], resource_id)
        if key in seen:
            continue
        seen.add(key)
        result[link["artifact_sha256"]].append(dict(resource))
    for values in result.values():
        values.sort(key=lambda row: row["resource_id"])
    return result


def _odt_candidate(resource_rows: list[Mapping[str, Any]]) -> bool:
    return any(_resource_declares_odt(row) for row in resource_rows)


def _resource_declares_odt(row: Mapping[str, Any]) -> bool:
    if row.get("resource_kind") not in {
        "official_attachment",
        "official_current_whole_attachment",
        "official_current_chapter_attachment",
    }:
        return False
    locator = row.get("discovery_locator")
    locator = locator if isinstance(locator, Mapping) else {}
    declared_values = (
        row.get("source_label"),
        row.get("source_url"),
        locator.get("attachment_title"),
        locator.get("attachment_visible_label"),
    )
    return any(
        str(value).strip().lower().endswith(".odt")
        or str(value).strip().lower() == "odt"
        for value in declared_values
        if value is not None
    )


def parse_verified_odt_run(
    run_dir: Path,
    stage_dir: Path,
    *,
    parse_run_id: str,
) -> dict[str, Any]:
    """Parse all verified, declared ODT attachments exactly once by content hash."""

    try:
        uuid.UUID(parse_run_id)
    except ValueError as exc:
        raise ContractError("parse_run_id must be a UUID") from exc

    raw_verification = verify_raw(run_dir)
    raw_manifest_path = run_dir / "raw-manifest.json"
    raw_manifest_sha = file_sha256(raw_manifest_path)
    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    by_artifact = _artifact_resources(
        resources,
        run_dir / "resource-artifact-links.jsonl",
    )
    parser_module = _legacy_parser()
    _install_v2_list_walker(parser_module)
    parser_bundle_sha = _parser_bundle_sha(parser_module)
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "raw_manifest_sha256": raw_manifest_sha,
                "parser_bundle_sha256": parser_bundle_sha,
                "parser_adapter_version": PARSER_ADAPTER_VERSION,
                "non_claim": NON_CLAIM,
            }
        )
    )

    stage_dir.mkdir(parents=True, exist_ok=True)
    if any((stage_dir / filename).exists() for filename in STRUCTURAL_FILES):
        raise ContractError("structural stage files already exist; use a fresh stage_dir")
    for filename in STRUCTURAL_FILES:
        (stage_dir / filename).touch()

    counts = {
        "declared_odt_resources": 0,
        "declared_odt_artifacts": 0,
        "parsed_odt_artifacts": 0,
        "structural_blocks": 0,
        "occurrence_candidates": 0,
        "parse_issues": 0,
        "blocking_issues": 0,
    }
    parsed_artifacts: list[dict[str, Any]] = []
    started_at = utc_now()

    for artifact_sha in sorted(artifacts):
        resource_rows = by_artifact.get(artifact_sha, [])
        if not _odt_candidate(resource_rows):
            continue
        counts["declared_odt_resources"] += sum(
            _resource_declares_odt(row)
            for row in resource_rows
        )
        counts["declared_odt_artifacts"] += 1
        artifact = artifacts[artifact_sha]
        blob_path = resolve_run_relative(run_dir, artifact["content_path"])
        resource_ids = [row["resource_id"] for row in resource_rows]
        source_labels = sorted(
            {
                str(row.get("source_label", ""))
                for row in resource_rows
                if row.get("source_label")
            }
        )

        if not _is_odt(blob_path):
            issue = {
                "schema": PARSE_ISSUE_SCHEMA,
                "parse_run_id": parse_run_id,
                "issue_id": sha256_bytes(
                    canonical_json_bytes(
                        [parse_run_id, artifact_sha, "declared_odt_magic_mismatch"]
                    )
                ),
                "artifact_sha256": artifact_sha,
                "issue_code": "declared_odt_magic_mismatch",
                "severity": "error",
                "blocking": True,
                "message_parameters": {
                    "resource_ids": resource_ids,
                    "source_labels": source_labels,
                },
                "statement": NON_CLAIM,
            }
            _append_row(stage_dir / "parse-issues.jsonl", issue)
            counts["parse_issues"] += 1
            counts["blocking_issues"] += 1
            continue

        data = blob_path.read_bytes()
        relative_path = artifact["content_path"]
        try:
            state = parser_module.parse_odt_blocks(
                data,
                artifact_sha=artifact_sha,
                relative_path=relative_path,
            )
            occurrences, legacy_issues, rejection_counts = (
                parser_module.blocks_to_occurrences(
                    state.blocks,
                    relative_path=relative_path,
                )
            )
            # v1 preserved a historical bookkeeping defect: occurrence IDs were
            # appended twice to its duplicate-designation accumulator.  The
            # sealed v1 bytes and stage remain untouched; v2 corrects only this
            # derived ambiguity signal from the actual emitted occurrences.
            designation_counts = Counter(
                str(row["designation_text"]) for row in occurrences
            )
            for occurrence in occurrences:
                flags = set(occurrence.get("ambiguity_flags", []))
                if designation_counts[str(occurrence["designation_text"])] > 1:
                    flags.add("duplicate_designation_in_artifact")
                else:
                    flags.discard("duplicate_designation_in_release")
                occurrence["ambiguity_flags"] = sorted(flags)
            legacy_issues = [
                issue
                for issue in legacy_issues
                if issue.get("issue_code") != "duplicate_designation_within_release"
            ]
            for designation, occurrence_count in sorted(designation_counts.items()):
                if occurrence_count > 1:
                    legacy_issues.append(
                        {
                            "issue_code": "duplicate_designation_within_artifact",
                            "severity": "info",
                            "issue_class": "content_ambiguity",
                            "designation_text": designation,
                            "occurrence_count": occurrence_count,
                            "detail": (
                                "designation occurs more than once in this source "
                                "artifact; all candidates retained"
                            ),
                        }
                    )
        except Exception as exc:
            issue = {
                "schema": PARSE_ISSUE_SCHEMA,
                "parse_run_id": parse_run_id,
                "issue_id": sha256_bytes(
                    canonical_json_bytes(
                        [
                            parse_run_id,
                            artifact_sha,
                            "odt_parse_failed",
                            type(exc).__name__,
                        ]
                    )
                ),
                "artifact_sha256": artifact_sha,
                "issue_code": "odt_parse_failed",
                "severity": "error",
                "blocking": True,
                "message_parameters": {
                    "error_type": type(exc).__name__,
                    "resource_ids": resource_ids,
                },
                "statement": NON_CLAIM,
            }
            _append_row(stage_dir / "parse-issues.jsonl", issue)
            counts["parse_issues"] += 1
            counts["blocking_issues"] += 1
            continue

        for legacy in state.blocks:
            block = dict(legacy)
            block.update(
                {
                    "schema": STRUCTURAL_BLOCK_SCHEMA,
                    "parse_run_id": parse_run_id,
                    "source_resource_ids": resource_ids,
                    "source_labels": source_labels,
                    "statement": NON_CLAIM,
                }
            )
            _append_row(stage_dir / "structural-blocks.jsonl", block)
            counts["structural_blocks"] += 1

        for legacy in occurrences:
            occurrence = dict(legacy)
            occurrence.update(
                {
                    "schema": OCCURRENCE_CANDIDATE_SCHEMA,
                    "parse_run_id": parse_run_id,
                    "source_resource_ids": resource_ids,
                    "source_labels": source_labels,
                    "statement": NON_CLAIM,
                }
            )
            _append_row(stage_dir / "occurrence-candidates.jsonl", occurrence)
            counts["occurrence_candidates"] += 1

        for ordinal, legacy in enumerate(legacy_issues):
            issue_code = str(legacy.get("issue_code", "legacy_parser_issue"))
            issue = {
                "schema": PARSE_ISSUE_SCHEMA,
                "parse_run_id": parse_run_id,
                "issue_id": sha256_bytes(
                    canonical_json_bytes(
                        [
                            parse_run_id,
                            artifact_sha,
                            issue_code,
                            str(ordinal),
                            legacy,
                        ]
                    )
                ),
                "artifact_sha256": artifact_sha,
                "issue_code": issue_code,
                "severity": str(legacy.get("severity", "info")),
                "blocking": False,
                "message_parameters": legacy,
                "statement": NON_CLAIM,
            }
            _append_row(stage_dir / "parse-issues.jsonl", issue)
            counts["parse_issues"] += 1

        counts["parsed_odt_artifacts"] += 1
        parsed_artifacts.append(
            {
                "artifact_sha256": artifact_sha,
                "byte_size": artifact["byte_size"],
                "resource_ids": resource_ids,
                "source_labels": source_labels,
                "block_count": len(state.blocks),
                "occurrence_count": len(occurrences),
                "parse_issue_count": len(legacy_issues),
                "numeric_rejection_counts": rejection_counts,
                "xml_ph_element_count": state.xml_ph_element_count,
                "xml_ph_emitted_unique": len(state.emitted_ph_xml_ids),
            }
        )

    if counts["blocking_issues"]:
        status = "failed"
    elif counts["declared_odt_artifacts"] == 0:
        raise ContractError("verified acquisition run contains no declared ODT artifact")
    else:
        status = "passed"
    completed_at = utc_now()
    manifest = {
        "schema": STRUCTURAL_MANIFEST_SCHEMA,
        "parse_run_id": parse_run_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "raw_manifest_sha256": raw_manifest_sha,
        "raw_verification": raw_verification,
        "parser_adapter_version": PARSER_ADAPTER_VERSION,
        "legacy_parser_version": parser_module.PARSER_VERSION,
        "parser_bundle_sha256": parser_bundle_sha,
        "input_fingerprint": input_fingerprint,
        "fidelity_class": "lossless_structural",
        "counts": counts,
        "parsed_artifacts": parsed_artifacts,
        "closure_claims": {
            "declared_odt_artifacts_exhausted": (
                counts["declared_odt_artifacts"] == counts["parsed_odt_artifacts"]
            ),
            "structural_text_coverage_checked_per_odt": True,
            "semantic_history_complete": False,
        },
        "statement": NON_CLAIM,
        "files": [
            manifest_file_entry(stage_dir / filename)
            for filename in STRUCTURAL_FILES
        ],
    }
    write_json(stage_dir / "structural-manifest.json", manifest)
    if status != "passed":
        raise ContractError(
            f"structural parse produced {counts['blocking_issues']} blocking issue(s)"
        )
    return manifest
