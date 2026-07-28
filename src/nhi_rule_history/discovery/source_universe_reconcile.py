"""Fail-closed reconciliation of two official reimbursement notice surfaces.

This module compares the unfiltered NHI ``lp-3258`` listing/detail surface with
the bounded MOHW FINT exact-phrase capture.  It deliberately stops at source
surface identity:

* an official document number is a join key for this audit, not a rule ID;
* listing/document dates are preserved observations, not effective dates;
* presence on either surface is not a legal-relevance classification;
* NHI pass A/B parity is required on parsed metadata, while volatile HTML bytes
  are expected and retained as separate artifact hashes.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    iter_jsonl,
    sha256_bytes,
    unique_rows,
)
from nhi_rule_history.discovery.nhi_listing import DETAIL_PATH_RE
from nhi_rule_history.raw import RawStore
from nhi_rule_history.raw.verify import verify_raw


PARSER_VERSION = "nhi-source-universe-reconcile/1.0.0"
REPORT_SCHEMA = "nhi-rule-history/source-universe-reconciliation/v1"
NHI_ALLOWED_HOST = "www.nhi.gov.tw"
FINT_ALLOWED_HOST = "mohwlaw.mohw.gov.tw"
NHI_REQUIRED_FIELDS = ("主旨", "發文字號", "發文日期")
NHI_CANONICAL_FIELD_ORDER = (
    "主旨",
    "發文字號",
    "依據",
    "公告事項",
    "發文日期",
)
ROC_DASH_DATE_RE = re.compile(
    r"^(?P<year>[0-9]{3})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})$"
)
FINT_ROC_DATE_RE = re.compile(
    r"^民國\s*(?P<year>[0-9]{3})\s*年\s*"
    r"(?P<month>[0-9]{1,2})\s*月\s*"
    r"(?P<day>[0-9]{1,2})\s*日$"
)
FINT_DOCUMENT_DATE_RE = re.compile(
    r"發\s*文\s*日\s*期\s*[:：]\s*"
    r"(?P<date>民國\s*[0-9]{3}\s*年\s*[0-9]{1,2}\s*月\s*"
    r"[0-9]{1,2}\s*日)"
)
FINT_DOCUMENT_NUMBER_RE = re.compile(
    r"發\s*文\s*字\s*號\s*[:：]\s*(?P<number>.*?)\s*"
    r"發\s*文\s*日\s*期\s*[:：]"
)
FINT_SUBJECT_RE = re.compile(
    r"主\s*旨\s*[:：]\s*(?P<subject>.*?)"
    r"(?=\s*(?:依\s*據|公\s*告\s*事\s*項|相\s*關\s*圖\s*表)"
    r"\s*[:：]|$)"
)
DOCUMENT_NUMBER_CORE_RE = re.compile(
    r"(?:(?P<prefix>健保[^第\s，,。、；;：:]{0,10}字))?"
    r"第(?P<number>[0-9]{6,12})(?:號)?"
)


def _fail(message: str) -> None:
    raise ContractError(message)


def _display_text(value: str) -> str:
    return " ".join(value.split())


def normalize_document_number(value: str) -> str:
    """Isolate one document-number frame without inferring a missing prefix.

    The NHI surface sometimes adds dates/``公告`` around the number, omits the
    trailing ``號``, or publishes only ``第…號``.  Those raw defects remain in
    the receipt.  Normalization removes whitespace, isolates exactly one
    supported frame, and restores only the syntactic trailing ``號``.  It never
    invents an absent issuing-unit prefix.
    """

    if not isinstance(value, str):
        _fail("official document number must be text")
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    if not normalized:
        _fail("official document number is empty")
    matches = list(DOCUMENT_NUMBER_CORE_RE.finditer(normalized))
    if len(matches) != 1:
        _fail("official document number has no unique supported 第…號 frame")
    match = matches[0]
    prefix = match.group("prefix") or ""
    return f"{prefix}第{match.group('number')}號"


def _calendar_date(
    raw: str,
    *,
    pattern: re.Pattern[str],
    label: str,
) -> dict[str, Any]:
    match = pattern.fullmatch(raw)
    if match is None:
        _fail(f"{label} is not an exact supported ROC date")
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        _fail(f"{label} has an invalid month/day")
    gregorian_year = year + 1911
    try:
        import datetime

        parsed = datetime.date(gregorian_year, month, day)
    except ValueError as exc:
        raise ContractError(f"{label} is not a calendar date") from exc
    return {
        "raw": raw,
        "calendar": "ROC",
        "roc_year": year,
        "iso_date": parsed.isoformat(),
        "semantic_role": "observed_document_or_listing_date_not_legal_effect",
    }


def parse_nhi_roc_date(raw: str, *, label: str) -> dict[str, Any]:
    return _calendar_date(raw, pattern=ROC_DASH_DATE_RE, label=label)


def parse_fint_roc_date(raw: str, *, label: str) -> dict[str, Any]:
    return _calendar_date(raw, pattern=FINT_ROC_DATE_RE, label=label)


@dataclass(frozen=True)
class NhiMetadataField:
    label_raw: str
    value_raw: str
    table_ordinal: int
    row_ordinal: int
    label_cell_ordinal: int = 1
    value_cell_ordinal: int = 2

    @property
    def label(self) -> str:
        return _display_text(self.label_raw)

    @property
    def value_normalized(self) -> str:
        return _display_text(self.value_raw)

    def locator(self) -> dict[str, int]:
        return {
            "table_ordinal": self.table_ordinal,
            "row_ordinal": self.row_ordinal,
            "label_cell_ordinal": self.label_cell_ordinal,
            "value_cell_ordinal": self.value_cell_ordinal,
        }


@dataclass(frozen=True)
class NhiDetailMetadata:
    table_ordinal: int
    fields: tuple[NhiMetadataField, ...]

    def by_label(self) -> dict[str, NhiMetadataField]:
        return {item.label: item for item in self.fields}

    def projection(self) -> dict[str, Any]:
        return {
            "table_ordinal": self.table_ordinal,
            "fields": [
                {
                    "label_raw": item.label_raw,
                    "label": item.label,
                    "value_raw": item.value_raw,
                    "value_normalized": item.value_normalized,
                    "source_locator": item.locator(),
                }
                for item in self.fields
            ],
        }


@dataclass
class _Cell:
    tag: str
    parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "".join(self.parts).strip()


@dataclass
class _Row:
    table_ordinal: int
    row_ordinal: int
    cells: list[_Cell] = field(default_factory=list)


@dataclass
class _Table:
    table_ordinal: int
    rows: list[_Row] = field(default_factory=list)


class _NhiMetadataTableParser(HTMLParser):
    """Collect table cell text without depending on volatile page chrome."""

    _BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "div",
            "dl",
            "fieldset",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self.errors: list[str] = []
        self._table: _Table | None = None
        self._row: _Row | None = None
        self._cell: _Cell | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag == "table":
            if self._table is not None:
                self.errors.append("nested_table")
                return
            self._table = _Table(table_ordinal=len(self.tables) + 1)
            return
        if self._table is None:
            return
        if tag == "tr":
            if self._row is not None:
                self.errors.append("nested_row")
                return
            self._row = _Row(
                table_ordinal=self._table.table_ordinal,
                row_ordinal=len(self._table.rows) + 1,
            )
            return
        if tag in {"th", "td"} and self._row is not None:
            if self._cell is not None:
                self.errors.append("nested_cell")
                return
            self._cell = _Cell(tag=tag)
            return
        if self._cell is not None and (
            tag == "br" or tag in self._BLOCK_TAGS
        ):
            self._cell.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None:
            if self._row is None:
                self.errors.append("cell_without_row")
            else:
                self._row.cells.append(self._cell)
            self._cell = None
            return
        if tag == "tr" and self._row is not None:
            if self._table is None:
                self.errors.append("row_without_table")
            elif self._row.cells:
                self._table.rows.append(self._row)
            self._row = None
            return
        if tag == "table" and self._table is not None:
            if self._row is not None or self._cell is not None:
                self.errors.append("unclosed_row_or_cell")
                self._row = None
                self._cell = None
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.parts.append(data)

    def close(self) -> None:
        super().close()
        if self._table is not None or self._row is not None or self._cell is not None:
            self.errors.append("unclosed_metadata_structure")


def parse_nhi_detail_metadata(payload: bytes) -> NhiDetailMetadata:
    """Parse exactly one ``項目／內容`` table and its allowed metadata rows."""

    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContractError("NHI detail HTML is not UTF-8") from exc
    parser = _NhiMetadataTableParser()
    parser.feed(html)
    parser.close()
    if parser.errors:
        _fail(
            "NHI detail metadata HTML is structurally ambiguous: "
            + ",".join(sorted(set(parser.errors)))
        )
    candidates: list[_Table] = []
    for table in parser.tables:
        if not table.rows:
            continue
        header = table.rows[0]
        if (
            len(header.cells) == 2
            and tuple(_display_text(cell.text()) for cell in header.cells)
            == ("項目", "內容")
        ):
            candidates.append(table)
    if len(candidates) != 1:
        _fail("NHI detail must contain exactly one 項目／內容 metadata table")
    table = candidates[0]
    fields: list[NhiMetadataField] = []
    for row in table.rows[1:]:
        if len(row.cells) != 2:
            _fail("NHI detail metadata row must contain exactly two cells")
        label_raw = row.cells[0].text()
        value_raw = row.cells[1].text()
        if not label_raw or not value_raw:
            _fail("NHI detail metadata row contains an empty label or value")
        fields.append(
            NhiMetadataField(
                label_raw=label_raw,
                value_raw=value_raw,
                table_ordinal=table.table_ordinal,
                row_ordinal=row.row_ordinal,
            )
        )
    labels = tuple(item.label for item in fields)
    if len(set(labels)) != len(labels):
        _fail("NHI detail metadata table contains duplicate labels")
    unknown = sorted(set(labels) - set(NHI_CANONICAL_FIELD_ORDER))
    if unknown:
        _fail("NHI detail metadata table contains unknown labels")
    if any(required not in labels for required in NHI_REQUIRED_FIELDS):
        _fail("NHI detail metadata table is missing a required label")
    expected_order = tuple(
        label for label in NHI_CANONICAL_FIELD_ORDER if label in labels
    )
    if labels != expected_order:
        _fail("NHI detail metadata labels are not in the canonical order")
    return NhiDetailMetadata(
        table_ordinal=table.table_ordinal,
        fields=tuple(fields),
    )


def _load_successful_raw_run(run_dir: Path) -> dict[str, Any]:
    verification = verify_raw(run_dir)
    if verification.get("status") != "passed":
        _fail("raw run did not pass offline verification")
    manifest_path = run_dir / "raw-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("raw manifest is unreadable") from exc
    if manifest.get("status") != "success":
        _fail("raw manifest is not successful")
    if any(iter_jsonl(run_dir / "issues.jsonl")):
        _fail("raw run contains acquisition issues")
    return manifest


def _single_artifact_bindings(
    run_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    artifacts = unique_rows(run_dir / "raw-artifacts.jsonl", "artifact_sha256")
    links_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_jsonl(run_dir / "resource-artifact-links.jsonl"):
        links_by_resource[row["resource_id"]].append(row)
    bindings: dict[str, dict[str, Any]] = {}
    for resource_id, links in links_by_resource.items():
        if len(links) != 1:
            _fail("resource does not have exactly one raw artifact binding")
        artifact = artifacts.get(links[0]["artifact_sha256"])
        if artifact is None:
            _fail("resource binding references an unknown artifact")
        bindings[resource_id] = artifact
    return artifacts, bindings


def _nhi_pass_projection(
    run_dir: Path,
    *,
    expected_detail_count: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _load_successful_raw_run(run_dir)
    resources = unique_rows(run_dir / "discovered-resources.jsonl", "resource_id")
    _, bindings = _single_artifact_bindings(run_dir)
    if len(resources) != expected_detail_count:
        _fail("NHI listing detail denominator differs from expectation")
    if set(bindings) != set(resources):
        _fail("NHI listing detail resource/artifact denominator is incomplete")
    store = RawStore(run_dir)
    projection: dict[str, dict[str, Any]] = {}
    observed_ordinals: list[int] = []
    for resource_id, resource in resources.items():
        if resource.get("resource_kind") != "official_detail_page":
            _fail("NHI listing run contains a non-detail resource")
        source_url = resource.get("source_url")
        if not isinstance(source_url, str):
            _fail("NHI detail URL is missing")
        parts = urlsplit(source_url)
        if (
            parts.scheme != "https"
            or parts.netloc != NHI_ALLOWED_HOST
            or DETAIL_PATH_RE.fullmatch(parts.path) is None
            or parts.query
            or parts.fragment
        ):
            _fail("NHI detail URL is not an exact official detail locator")
        locator = resource.get("discovery_locator")
        if not isinstance(locator, dict):
            _fail("NHI detail listing locator is missing")
        required_locator_fields = {
            "surface",
            "stable_row_identity",
            "displayed_ordinal",
            "document_number_raw",
            "document_date_raw",
            "listing_date_raw",
            "expiry_date_raw",
            "listing_occurrences",
        }
        if set(locator) != required_locator_fields:
            _fail("NHI detail listing locator fields are incomplete or ambiguous")
        if locator["surface"] != "nhi_amendment_listing_3258":
            _fail("NHI detail has an unexpected listing surface")
        if locator["stable_row_identity"] != f"url:{source_url}":
            _fail("NHI detail stable row identity disagrees with its URL")
        displayed_ordinal = locator["displayed_ordinal"]
        if (
            not isinstance(displayed_ordinal, int)
            or isinstance(displayed_ordinal, bool)
            or not 1 <= displayed_ordinal <= expected_detail_count
        ):
            _fail("NHI displayed ordinal is outside the expected denominator")
        observed_ordinals.append(displayed_ordinal)
        occurrences = locator["listing_occurrences"]
        if not isinstance(occurrences, list) or len(occurrences) != 1:
            _fail("NHI detail must have exactly one listing occurrence")
        occurrence = occurrences[0]
        if not isinstance(occurrence, dict) or set(occurrence) != {
            "listing_page_url",
            "page_number",
            "row_ordinal",
        }:
            _fail("NHI listing occurrence locator is incomplete")
        page_number = occurrence["page_number"]
        row_ordinal = occurrence["row_ordinal"]
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            or not isinstance(row_ordinal, int)
            or isinstance(row_ordinal, bool)
            or not 1 <= row_ordinal <= 20
            or displayed_ordinal != (page_number - 1) * 20 + row_ordinal
        ):
            _fail("NHI listing occurrence order is inconsistent")
        listing_parts = urlsplit(occurrence["listing_page_url"])
        if (
            listing_parts.scheme != "https"
            or listing_parts.netloc != NHI_ALLOWED_HOST
            or listing_parts.path != "/ch/lp-3258-1.html"
        ):
            _fail("NHI listing occurrence URL is not the official listing")
        artifact = bindings[resource_id]
        if artifact.get("media_type") != "text/html":
            _fail("NHI detail artifact is not text/html")
        payload = store.read(
            artifact["content_path"],
            artifact["artifact_sha256"],
            artifact["byte_size"],
        )
        metadata = parse_nhi_detail_metadata(payload)
        fields = metadata.by_label()
        detail_number_raw = fields["發文字號"].value_raw
        listing_number_raw = locator["document_number_raw"]
        detail_number = normalize_document_number(detail_number_raw)
        listing_number = normalize_document_number(listing_number_raw)
        if detail_number != listing_number:
            _fail("NHI listing/detail document numbers disagree")
        detail_date_raw = fields["發文日期"].value_normalized
        detail_date = parse_nhi_roc_date(
            detail_date_raw,
            label="NHI detail 發文日期",
        )
        locator_document_date = parse_nhi_roc_date(
            locator["document_date_raw"],
            label="NHI listing 發文日期",
        )
        listing_date = parse_nhi_roc_date(
            locator["listing_date_raw"],
            label="NHI listing 刊登日期",
        )
        expiry_date = (
            parse_nhi_roc_date(
                locator["expiry_date_raw"],
                label="NHI listing 刊登期限",
            )
            if locator["expiry_date_raw"]
            else None
        )
        if detail_date["iso_date"] != locator_document_date["iso_date"]:
            _fail("NHI listing/detail document dates disagree")
        source_label = resource.get("source_label")
        if not isinstance(source_label, str) or not source_label.strip():
            _fail("NHI listing source label is empty")
        subject = fields["主旨"]
        if _display_text(source_label) != subject.value_normalized:
            _fail("NHI listing/detail subjects disagree after whitespace normalization")
        projection[resource_id] = {
            "resource_id": resource_id,
            "source_url": source_url,
            "displayed_ordinal": displayed_ordinal,
            "listing_occurrence": occurrence,
            "listing_document_number_raw": listing_number_raw,
            "normalized_document_number": detail_number,
            "detail_document_number_raw": detail_number_raw,
            "detail_document_date": detail_date,
            "listing_document_date": locator_document_date,
            "listing_date": listing_date,
            "expiry_date": expiry_date,
            "listing_source_label_raw": source_label,
            "detail_subject_raw": subject.value_raw,
            "detail_basis_raw": (
                fields["依據"].value_raw if "依據" in fields else None
            ),
            "detail_announcement_raw": (
                fields["公告事項"].value_raw
                if "公告事項" in fields
                else None
            ),
            "metadata_projection": metadata.projection(),
            "listing_detail_checks": {
                "document_number_normalized_equal": True,
                "document_date_iso_equal": True,
                "subject_whitespace_normalized_equal": True,
                "subject_raw_equal": source_label == subject.value_raw,
                "listing_date_equals_detail_document_date_observation": (
                    listing_date["iso_date"] == detail_date["iso_date"]
                ),
                "listing_date_has_no_legal_effect_semantics": True,
            },
            "artifact_sha256": artifact["artifact_sha256"],
        }
    if sorted(observed_ordinals) != list(range(1, expected_detail_count + 1)):
        _fail("NHI displayed ordinals do not close the expected denominator")
    return manifest, projection


def _stable_nhi_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key != "artifact_sha256"
    }


def _parse_fint_resource_metadata(resource: Mapping[str, Any]) -> dict[str, Any]:
    if resource.get("resource_kind") != "official_detail_page":
        _fail("FINT reconciliation received a non-detail resource")
    source_url = resource.get("source_url")
    if not isinstance(source_url, str):
        _fail("FINT detail URL is missing")
    parts = urlsplit(source_url)
    if (
        parts.scheme != "https"
        or parts.netloc != FINT_ALLOWED_HOST
        or parts.path != "/FINT/FINTQRY04.aspx"
    ):
        _fail("FINT detail URL is not the expected official detail surface")
    source_label = resource.get("source_label")
    if not isinstance(source_label, str) or not source_label.strip():
        _fail("FINT detail source label is empty")
    document_number_raw = resource.get("official_document_number_raw")
    if not isinstance(document_number_raw, str):
        _fail("FINT detail has no official document number")
    normalized_number = normalize_document_number(document_number_raw)
    number_match = FINT_DOCUMENT_NUMBER_RE.search(source_label)
    if number_match is None:
        _fail("FINT detail label has no document-number locator")
    label_number_raw = number_match.group("number").strip()
    if normalize_document_number(label_number_raw) != normalized_number:
        _fail("FINT detail resource/label document numbers disagree")
    date_match = FINT_DOCUMENT_DATE_RE.search(source_label)
    if date_match is None:
        _fail("FINT detail label has no document date")
    date_raw = date_match.group("date").strip()
    document_date = parse_fint_roc_date(
        date_raw,
        label="FINT 發文日期",
    )
    subject_match = FINT_SUBJECT_RE.search(source_label)
    subject_raw = (
        subject_match.group("subject").strip()
        if subject_match is not None
        else None
    )
    locator = resource.get("discovery_locator")
    if not isinstance(locator, dict):
        _fail("FINT detail discovery locator is missing")
    partition_id = locator.get("partition_id")
    if not isinstance(partition_id, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}__[0-9]{4}-[0-9]{2}-[0-9]{2}",
        partition_id,
    ):
        _fail("FINT detail partition locator is invalid")
    return {
        "resource_id": resource["resource_id"],
        "source_url": source_url,
        "official_document_number_raw": document_number_raw,
        "normalized_document_number": normalized_number,
        "document_date": document_date,
        "subject_raw": subject_raw,
        "title_source": (
            "parsed_主旨" if subject_raw is not None else "source_label_fallback"
        ),
        "title_raw": subject_raw if subject_raw is not None else source_label,
        "discovery_locator": locator,
    }


def _fint_projection(
    run_dir: Path,
    *,
    expected_detail_count: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str, str]:
    manifest = _load_successful_raw_run(run_dir)
    resources = [
        row
        for row in iter_jsonl(run_dir / "discovered-resources.jsonl")
        if row.get("resource_kind") == "official_detail_page"
    ]
    if len(resources) != expected_detail_count:
        _fail("FINT detail denominator differs from expectation")
    projection: dict[str, dict[str, Any]] = {}
    starts: list[str] = []
    ends: list[str] = []
    for resource in resources:
        parsed = _parse_fint_resource_metadata(resource)
        resource_id = parsed["resource_id"]
        if resource_id in projection:
            _fail("FINT detail resource ID is duplicated")
        projection[resource_id] = parsed
        partition_start, partition_end = parsed["discovery_locator"][
            "partition_id"
        ].split("__", 1)
        starts.append(partition_start)
        ends.append(partition_end)
    return manifest, projection, min(starts), max(ends)


def _projection_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    return sha256_bytes(
        b"".join(
            canonical_json_bytes(dict(row))
            for row in sorted(
                rows,
                key=lambda item: canonical_json_bytes(dict(item)),
            )
        )
    )


def _group_by_number(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["normalized_document_number"]].append(dict(row))
    for key in grouped:
        grouped[key].sort(
            key=lambda item: (
                item["source_url"],
                item["resource_id"],
            )
        )
    return dict(sorted(grouped.items()))


def _collision_summary(
    grouped: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    groups = [
        {
            "normalized_document_number": key,
            "record_count": len(rows),
            "resource_ids": [row["resource_id"] for row in rows],
            "source_urls": [row["source_url"] for row in rows],
        }
        for key, rows in grouped.items()
        if len(rows) > 1
    ]
    return {
        "normalized_key_count": len(grouped),
        "collision_key_count": len(groups),
        "collision_groups": groups,
    }


def _public_nhi_row(
    row_a: Mapping[str, Any],
    row_b: Mapping[str, Any],
) -> dict[str, Any]:
    metadata_projection = row_a["metadata_projection"]
    locators = {
        field["label"]: field["source_locator"]
        for field in metadata_projection["fields"]
    }
    basis = row_a["detail_basis_raw"]
    announcement = row_a["detail_announcement_raw"]
    return {
        "resource_id": row_a["resource_id"],
        "source_url": row_a["source_url"],
        "displayed_ordinal": row_a["displayed_ordinal"],
        "normalized_document_number": row_a[
            "normalized_document_number"
        ],
        "raw_metadata": {
            "listing_document_number_raw": row_a[
                "listing_document_number_raw"
            ],
            "detail_document_number_raw": row_a[
                "detail_document_number_raw"
            ],
            "detail_document_date_raw": row_a[
                "detail_document_date"
            ]["raw"],
            "listing_document_date_raw": row_a[
                "listing_document_date"
            ]["raw"],
            "listing_date_raw": row_a["listing_date"]["raw"],
            "expiry_date_raw": (
                row_a["expiry_date"]["raw"]
                if row_a["expiry_date"] is not None
                else ""
            ),
            "listing_title_raw": row_a["listing_source_label_raw"],
            "detail_title_raw": row_a["detail_subject_raw"],
        },
        "observed_dates": {
            "detail_document_date_iso": row_a[
                "detail_document_date"
            ]["iso_date"],
            "listing_document_date_iso": row_a[
                "listing_document_date"
            ]["iso_date"],
            "listing_date_iso": row_a["listing_date"]["iso_date"],
            "expiry_date_iso": (
                row_a["expiry_date"]["iso_date"]
                if row_a["expiry_date"] is not None
                else None
            ),
            "roc_year": row_a["detail_document_date"]["roc_year"],
        },
        "listing_occurrence": row_a["listing_occurrence"],
        "metadata_source_locators": locators,
        "large_optional_field_receipts": {
            "依據": {
                "present": basis is not None,
                "characters": len(basis) if basis is not None else 0,
                "raw_text_sha256": (
                    sha256_bytes(basis.encode("utf-8"))
                    if basis is not None
                    else None
                ),
                "source_locator": locators.get("依據"),
            },
            "公告事項": {
                "present": announcement is not None,
                "characters": (
                    len(announcement) if announcement is not None else 0
                ),
                "raw_text_sha256": (
                    sha256_bytes(announcement.encode("utf-8"))
                    if announcement is not None
                    else None
                ),
                "source_locator": locators.get("公告事項"),
            },
        },
        "source_artifact_locators": {
            "pass_a_artifact_sha256": row_a["artifact_sha256"],
            "pass_b_artifact_sha256": row_b["artifact_sha256"],
        },
        "listing_detail_observations": {
            "subject_raw_equal": row_a["listing_detail_checks"][
                "subject_raw_equal"
            ],
            "listing_date_equals_detail_document_date": row_a[
                "listing_detail_checks"
            ]["listing_date_equals_detail_document_date_observation"],
        },
    }


def _surface_row(
    normalized_number: str,
    nhi_rows: list[dict[str, Any]],
    fint_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if nhi_rows and fint_rows:
        classification = "intersection"
    elif nhi_rows:
        classification = "nhi_listing_only"
    else:
        classification = "fint_exact_phrase_only"
    years = sorted(
        {
            row["observed_dates"]["roc_year"]
            for row in nhi_rows
        }
        | {
            row["document_date"]["roc_year"]
            for row in fint_rows
        }
    )
    return {
        "normalized_document_number": normalized_number,
        "source_surface_classification": classification,
        "observed_roc_years": years,
        "nhi_listing_records": nhi_rows,
        "fint_exact_phrase_records": fint_rows,
        "legal_relevance_classified": False,
    }


def _year_overlap(
    nhi_grouped: Mapping[str, list[dict[str, Any]]],
    fint_grouped: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, int]]:
    years = sorted(
        {
            row["observed_dates"]["roc_year"]
            for rows in nhi_grouped.values()
            for row in rows
        }
        | {
            row["document_date"]["roc_year"]
            for rows in fint_grouped.values()
            for row in rows
        }
    )
    result: list[dict[str, int]] = []
    for year in years:
        nhi_keys = {
            key
            for key, rows in nhi_grouped.items()
            if any(
                row["observed_dates"]["roc_year"] == year
                for row in rows
            )
        }
        fint_keys = {
            key
            for key, rows in fint_grouped.items()
            if any(row["document_date"]["roc_year"] == year for row in rows)
        }
        result.append(
            {
                "roc_year": year,
                "gregorian_year": year + 1911,
                "nhi_listing_keys": len(nhi_keys),
                "fint_exact_phrase_keys": len(fint_keys),
                "intersection_keys": len(nhi_keys & fint_keys),
                "nhi_listing_only_keys": len(nhi_keys - fint_keys),
                "fint_exact_phrase_only_keys": len(fint_keys - nhi_keys),
            }
        )
    return result


def build_source_universe_reconciliation(
    nhi_pass_a_dir: Path,
    nhi_pass_b_dir: Path,
    fint_run_dir: Path,
    *,
    expected_nhi_detail_count: int = 858,
    expected_fint_detail_count: int = 366,
) -> dict[str, Any]:
    """Build one deterministic source-surface reconciliation receipt."""

    for count, label in (
        (expected_nhi_detail_count, "expected NHI detail count"),
        (expected_fint_detail_count, "expected FINT detail count"),
    ):
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
        ):
            _fail(f"{label} must be a positive integer")
    nhi_pass_a_dir = Path(nhi_pass_a_dir)
    nhi_pass_b_dir = Path(nhi_pass_b_dir)
    fint_run_dir = Path(fint_run_dir)
    manifest_a, pass_a = _nhi_pass_projection(
        nhi_pass_a_dir,
        expected_detail_count=expected_nhi_detail_count,
    )
    manifest_b, pass_b = _nhi_pass_projection(
        nhi_pass_b_dir,
        expected_detail_count=expected_nhi_detail_count,
    )
    if set(pass_a) != set(pass_b):
        _fail("NHI pass A/B resource identities differ")
    parity_differences = [
        resource_id
        for resource_id in sorted(pass_a)
        if _stable_nhi_projection(pass_a[resource_id])
        != _stable_nhi_projection(pass_b[resource_id])
    ]
    if parity_differences:
        _fail(
            "NHI pass A/B parsed metadata projections differ for "
            f"{len(parity_differences)} resources"
        )
    if manifest_a.get("source_plan_sha256") != manifest_b.get(
        "source_plan_sha256"
    ):
        _fail("NHI pass A/B source plans differ")
    public_nhi_rows = [
        _public_nhi_row(pass_a[resource_id], pass_b[resource_id])
        for resource_id in sorted(
            pass_a,
            key=lambda key: pass_a[key]["displayed_ordinal"],
        )
    ]
    fint_manifest, fint, fint_start, fint_end = _fint_projection(
        fint_run_dir,
        expected_detail_count=expected_fint_detail_count,
    )
    public_fint_rows = [fint[key] for key in sorted(fint)]
    nhi_grouped = _group_by_number(public_nhi_rows)
    fint_grouped = _group_by_number(public_fint_rows)
    all_keys = sorted(set(nhi_grouped) | set(fint_grouped))
    surface_rows = [
        _surface_row(
            key,
            nhi_grouped.get(key, []),
            fint_grouped.get(key, []),
        )
        for key in all_keys
    ]
    intersection_rows = [
        row
        for row in surface_rows
        if row["source_surface_classification"] == "intersection"
    ]
    nhi_only_rows = [
        row
        for row in surface_rows
        if row["source_surface_classification"] == "nhi_listing_only"
    ]
    fint_only_rows = [
        row
        for row in surface_rows
        if row["source_surface_classification"] == "fint_exact_phrase_only"
    ]
    collision_nhi = _collision_summary(nhi_grouped)
    collision_fint = _collision_summary(fint_grouped)
    ambiguous_join_keys = sorted(
        {
            key for key, rows in nhi_grouped.items() if len(rows) > 1
        }
        | {
            key for key, rows in fint_grouped.items() if len(rows) > 1
        }
    )
    listing_dates = sorted(
        row["observed_dates"]["listing_date_iso"]
        for row in public_nhi_rows
    )
    listing_date_raw_by_iso = {
        row["observed_dates"]["listing_date_iso"]: row["raw_metadata"][
            "listing_date_raw"
        ]
        for row in public_nhi_rows
    }
    raw_hash_pairs = [
        row["source_artifact_locators"]
        for row in public_nhi_rows
    ]
    differing_html_pairs = sum(
        pair["pass_a_artifact_sha256"]
        != pair["pass_b_artifact_sha256"]
        for pair in raw_hash_pairs
    )
    report = {
        "schema": REPORT_SCHEMA,
        "status": (
            "passed_grouped_source_surface_reconciliation_with_collisions"
            if ambiguous_join_keys
            else "passed_source_surface_reconciliation_not_legal_history"
        ),
        "parser": {
            "version": PARSER_VERSION,
            "code_sha256": file_sha256(Path(__file__)),
        },
        "inputs": {
            "nhi_pass_a": {
                "raw_manifest_sha256": file_sha256(
                    nhi_pass_a_dir / "raw-manifest.json"
                ),
                "source_plan_sha256": manifest_a["source_plan_sha256"],
            },
            "nhi_pass_b": {
                "raw_manifest_sha256": file_sha256(
                    nhi_pass_b_dir / "raw-manifest.json"
                ),
                "source_plan_sha256": manifest_b["source_plan_sha256"],
            },
            "fint_2021_through_capture_cut": {
                "raw_manifest_sha256": file_sha256(
                    fint_run_dir / "raw-manifest.json"
                ),
                "source_plan_sha256": fint_manifest["source_plan_sha256"],
                "declared_partition_start": fint_start,
                "declared_partition_end": fint_end,
            },
        },
        "counts": {
            "nhi_listing_detail_records": len(public_nhi_rows),
            "fint_exact_phrase_detail_records": len(public_fint_rows),
            "normalized_document_number_keys": len(all_keys),
            "intersection_keys": len(intersection_rows),
            "nhi_listing_only_keys": len(nhi_only_rows),
            "fint_exact_phrase_only_keys": len(fint_only_rows),
        },
        "nhi_pass_a_b_parity": {
            "status": "passed",
            "comparison_unit": (
                "exact parsed metadata/listing projection keyed by resource_id; "
                "artifact hashes excluded"
            ),
            "parsed_projection_sha256": _projection_fingerprint(
                _stable_nhi_projection(row) for row in pass_a.values()
            ),
            "volatile_html_artifact_pairs": len(raw_hash_pairs),
            "different_html_artifact_hash_pairs": differing_html_pairs,
            "identical_html_artifact_hash_pairs": (
                len(raw_hash_pairs) - differing_html_pairs
            ),
        },
        "listing_detail_field_checks": {
            "document_number_mismatches": 0,
            "document_date_mismatches": 0,
            "subject_whitespace_normalized_mismatches": 0,
            "subject_raw_mismatches": sum(
                not row["listing_detail_observations"]["subject_raw_equal"]
                for row in public_nhi_rows
            ),
            "listing_date_equals_detail_document_date_observations": sum(
                row["listing_detail_observations"][
                    "listing_date_equals_detail_document_date"
                ]
                for row in public_nhi_rows
            ),
            "listing_date_semantic_equivalence_to_effective_date_asserted": False,
        },
        "collision_checks": {
            "nhi_listing": collision_nhi,
            "fint_exact_phrase": collision_fint,
            "ambiguous_join_key_count": len(ambiguous_join_keys),
            "ambiguous_join_keys": ambiguous_join_keys,
            "one_to_one_join_safe": not ambiguous_join_keys,
        },
        "temporal_surface_boundaries": {
            "nhi_listing": {
                "observed_listing_date_start_raw": listing_date_raw_by_iso[
                    listing_dates[0]
                ],
                "observed_listing_date_start_iso": listing_dates[0],
                "observed_listing_date_end_raw": listing_date_raw_by_iso[
                    listing_dates[-1]
                ],
                "observed_listing_date_end_iso": listing_dates[-1],
                "statement": (
                    "The captured NHI listing surface begins at ROC "
                    f"{listing_date_raw_by_iso[listing_dates[0]]}; this is a "
                    "listing boundary, not the beginning of the legal history."
                ),
            },
            "fint_exact_phrase_capture": {
                "declared_partition_start": fint_start,
                "declared_partition_end": fint_end,
                "statement": (
                    "The FINT exact-phrase capture begins in Gregorian 2021 "
                    "(ROC 110), earlier than the observed NHI listing boundary; "
                    "the surfaces therefore have different temporal coverage."
                ),
            },
        },
        "overlap_by_roc_year": _year_overlap(nhi_grouped, fint_grouped),
        "fingerprints": {
            "nhi_parsed_projection_sha256": _projection_fingerprint(
                _stable_nhi_projection(row) for row in pass_a.values()
            ),
            "fint_projection_sha256": _projection_fingerprint(
                public_fint_rows
            ),
            "intersection_rows_sha256": _projection_fingerprint(
                intersection_rows
            ),
            "nhi_listing_only_rows_sha256": _projection_fingerprint(
                nhi_only_rows
            ),
            "fint_exact_phrase_only_rows_sha256": _projection_fingerprint(
                fint_only_rows
            ),
        },
        "intersection_rows": intersection_rows,
        "nhi_listing_only_rows": nhi_only_rows,
        "fint_exact_phrase_only_rows": fint_only_rows,
        "classification_contract": {
            "classification_scope": "source_surface_discrepancy_only",
            "title_phrase_used_as_final_legal_relevance_test": False,
            "official_document_number_used_as_stable_rule_identity": False,
            "legal_effective_date_inferred": False,
            "amendment_event_inferred": False,
            "direct_predecessor_inferred": False,
            "per_clause_history_completeness_inferred": False,
        },
        "limitations": [
            "The NHI listing is unfiltered; FINT is an exact-phrase query.",
            "A source-only row can reflect temporal or indexing coverage, not a missing legal event.",
            "The document-number join does not resolve corrections, reused numbers, clause identity, or lineage.",
            "No listed, document, capture, or file date is treated as a legal effective date.",
            "The receipt preserves raw source metadata but does not inspect attachment semantics.",
        ],
    }
    assert_public_value(report)
    return report


def write_source_universe_reconciliation(
    nhi_pass_a_dir: Path,
    nhi_pass_b_dir: Path,
    fint_run_dir: Path,
    output_path: Path,
    *,
    expected_nhi_detail_count: int = 858,
    expected_fint_detail_count: int = 366,
) -> dict[str, Any]:
    """Atomically write a deterministic receipt, refusing divergent overwrite."""

    report = build_source_universe_reconciliation(
        nhi_pass_a_dir,
        nhi_pass_b_dir,
        fint_run_dir,
        expected_nhi_detail_count=expected_nhi_detail_count,
        expected_fint_detail_count=expected_fint_detail_count,
    )
    payload = canonical_json_bytes(report)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        if output_path.read_bytes() == payload:
            return report
        _fail("reconciliation receipt already exists with different bytes")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        dir=output_path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return report
