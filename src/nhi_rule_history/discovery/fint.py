from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    assert_allowed_url,
    canonical_url,
    stable_id,
)
from nhi_rule_history.discovery.base import DiscoveryContext


ROW_NUMBER_RE = re.compile(r"(?:^|[?&])RowNo=(\d+)(?:&|$)", re.IGNORECASE)
DOCUMENT_NUMBER_RE = re.compile(
    r"發文字號\s*[:：]\s*(?P<number>.*?)\s*發文日期\s*[:：]",
    re.UNICODE,
)


def add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def date_partitions(start: date, end: date, months: int) -> Iterator[tuple[date, date]]:
    if months < 1:
        raise ContractError("partition_months must be positive")
    if start > end:
        raise ContractError("start_date must not be after end_date")
    cursor = start
    while cursor <= end:
        next_start = add_months(cursor.replace(day=1), months)
        partition_end = min(end, date.fromordinal(next_start.toordinal() - 1))
        yield cursor, partition_end
        cursor = date.fromordinal(partition_end.toordinal() + 1)


@dataclass(frozen=True)
class Anchor:
    anchor_id: str | None
    href: str
    text: str


class FintParser(HTMLParser):
    """Extract the record surface and attachment locators without interpretation."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._anchor_id: str | None = None
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._pre_depth = 0
        self._pre_text: list[str] = []
        self.has_record_table = False
        self.body_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "dat04":
            self.has_record_table = True
        if tag == "a":
            self._anchor_id = attributes.get("id")
            self._href = attributes.get("href")
            self._anchor_text = []
        if tag == "pre":
            self._pre_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._anchor_text).split())
            self.anchors.append(Anchor(self._anchor_id, self._href, text))
            self._anchor_id = None
            self._href = None
            self._anchor_text = []
        if tag == "pre" and self._pre_depth:
            self._pre_depth -= 1

    def handle_data(self, data: str) -> None:
        self.body_text.append(data)
        if self._href is not None:
            self._anchor_text.append(data)
        if self._pre_depth:
            self._pre_text.append(data)

    @property
    def record_excerpt(self) -> str:
        return " ".join("".join(self._pre_text).split())[:1000]

    @property
    def normalized_body(self) -> str:
        return " ".join("".join(self.body_text).split())

    def last_row_number(self) -> int | None:
        for anchor in self.anchors:
            if anchor.anchor_id == "hlLast":
                match = ROW_NUMBER_RE.search(anchor.href)
                if match:
                    return int(match.group(1))
        if self.has_record_table:
            return 1
        return None

    def attachment_anchors(self) -> list[Anchor]:
        return [
            anchor
            for anchor in self.anchors
            if "GetFile.ashx" in anchor.href and anchor.text
        ]

    def document_number(self) -> tuple[str, str]:
        match = DOCUMENT_NUMBER_RE.search(self.normalized_body)
        if match is None:
            raise ContractError("FINT detail page has no formal document number")
        raw = " ".join(match.group("number").split())
        normalized = re.sub(r"\s+", "", raw)
        if not normalized:
            raise ContractError("FINT formal document number is empty")
        return raw, normalized


def decode_html(payload: bytes, content_type: str | None = None) -> str:
    candidates: list[str] = []
    if content_type:
        match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.I)
        if match:
            candidates.append(match.group(1))
    head = payload[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset=[\"']?([A-Za-z0-9._-]+)", head, re.I)
    if match:
        candidates.append(match.group(1))
    candidates.extend(("utf-8-sig", "big5"))
    for encoding in candidates:
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ContractError("FINT response has no supported declared encoding")


def parse_fint(payload: bytes, content_type: str | None = None) -> FintParser:
    parser = FintParser()
    parser.feed(decode_html(payload, content_type))
    return parser


class MohwFintAdapter:
    kind = "mohw_fint"

    def _query_url(
        self,
        context: DiscoveryContext,
        start: date,
        end: date,
        keywords: list[str],
        row_number: int,
    ) -> str:
        while len(keywords) < 4:
            keywords.append("")
        params = {
            "starDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
            "no": "",
            "n1": "",
            "n2": "",
            "kt": "",
            "kw": keywords[0],
            "kw2": keywords[1],
            "kw3": keywords[2],
            "kw4": keywords[3],
            "valid": str(context.adapter.get("valid", "3")),
            "type": str(context.adapter.get("record_type", "etype_")),
            "RowNo": str(row_number),
        }
        return canonical_url(f"{context.adapter['base_url']}?{urlencode(params)}")

    def _record_resources(
        self,
        context: DiscoveryContext,
        *,
        query_id: str,
        partition_id: str,
        row_number: int,
        request_url: str,
        parser: FintParser,
    ) -> list[dict[str, Any]]:
        document_number_raw, document_number_normalized = parser.document_number()
        detail_id = stable_id(
            "fint-detail",
            context.adapter["id"],
            document_number_normalized,
        )
        rows: list[dict[str, Any]] = [
            {
                "schema": DISCOVERED_RESOURCE_SCHEMA,
                "resource_id": detail_id,
                "adapter_id": context.adapter["id"],
                "resource_kind": "official_detail_page",
                "source_url": request_url,
                "discovery_locator": {
                    "query_id": query_id,
                    "partition_id": partition_id,
                    "row_number": row_number,
                },
                "source_label": parser.record_excerpt,
                "official_document_number_raw": document_number_raw,
                "fetch_state": "cached_by_discovery",
            }
        ]
        for ordinal, anchor in enumerate(parser.attachment_anchors(), 1):
            absolute = assert_allowed_url(
                urljoin(request_url, anchor.href),
                context.client.allowed_hosts,
            )
            rows.append(
                {
                    "schema": DISCOVERED_RESOURCE_SCHEMA,
                    "resource_id": stable_id(
                        "fint-attachment",
                        context.adapter["id"],
                        absolute,
                    ),
                    "adapter_id": context.adapter["id"],
                    "resource_kind": "official_attachment",
                    "source_url": absolute,
                    "parent_resource_id": detail_id,
                    "discovery_locator": {
                        "query_id": query_id,
                        "partition_id": partition_id,
                        "row_number": row_number,
                        "attachment_ordinal": ordinal,
                    },
                    "source_label": anchor.text,
                    "official_document_number_raw": document_number_raw,
                    "fetch_state": "pending",
                }
            )
        return rows

    def discover(self, context: DiscoveryContext) -> dict[str, Any]:
        start = date.fromisoformat(context.adapter["start_date"])
        end = min(
            date.fromisoformat(context.adapter["end_date"]),
            date.fromisoformat(context.adapter["capture_cut"]),
        )
        months = int(context.adapter.get("partition_months", 12))
        query_specs = context.adapter.get("queries", [])
        if not query_specs:
            raise ContractError("MOHW FINT adapter requires at least one query")

        partition_stats: list[dict[str, Any]] = []
        for start_date, end_date in date_partitions(start, end, months):
            partition_id = f"{start_date.isoformat()}__{end_date.isoformat()}"
            for query in query_specs:
                query_id = query["id"]
                keywords = list(query["keywords"])
                first_url = self._query_url(
                    context, start_date, end_date, keywords.copy(), 1
                )
                first = context.recorder.observe(
                    adapter_id=context.adapter["id"],
                    request_url=first_url,
                    locator={
                        "query_id": query_id,
                        "partition_id": partition_id,
                        "row_number": 1,
                    },
                )
                first_parser = parse_fint(
                    first["payload"], first["headers"].get("content-type")
                )
                expected_rows = first_parser.last_row_number()
                if expected_rows is None:
                    raise ContractError(
                        f"FINT selector miss for query={query_id} partition={partition_id}"
                    )
                fetched_rows = 0
                for row_number in range(1, expected_rows + 1):
                    if row_number == 1:
                        request_url = first_url
                        observation = first
                        parser = first_parser
                    else:
                        request_url = self._query_url(
                            context,
                            start_date,
                            end_date,
                            keywords.copy(),
                            row_number,
                        )
                        observation = context.recorder.observe(
                            adapter_id=context.adapter["id"],
                            request_url=request_url,
                            locator={
                                "query_id": query_id,
                                "partition_id": partition_id,
                                "row_number": row_number,
                            },
                        )
                        parser = parse_fint(
                            observation["payload"],
                            observation["headers"].get("content-type"),
                        )
                    if not parser.has_record_table:
                        raise ContractError(
                            f"FINT missing record row={row_number} "
                            f"query={query_id} partition={partition_id}"
                        )
                    for resource in self._record_resources(
                        context,
                        query_id=query_id,
                        partition_id=partition_id,
                        row_number=row_number,
                        request_url=request_url,
                        parser=parser,
                    ):
                        context.recorder.record_resource(resource)
                    fetched_rows += 1
                if fetched_rows != expected_rows:
                    raise ContractError(
                        f"FINT row parity failed: expected={expected_rows} "
                        f"fetched={fetched_rows}"
                    )
                partition_stats.append(
                    {
                        "adapter_id": context.adapter["id"],
                        "query_id": query_id,
                        "partition_id": partition_id,
                        "expected_rows": expected_rows,
                        "fetched_rows": fetched_rows,
                    }
                )
        return {
            "adapter_id": context.adapter["id"],
            "kind": self.kind,
            "partitions": partition_stats,
        }
