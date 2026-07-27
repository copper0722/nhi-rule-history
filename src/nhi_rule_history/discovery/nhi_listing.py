"""Deterministic enumeration of the official NHI amendment listing.

The listing is an acquisition index, not legal-history authority.  This module
preserves every displayed row and its listing locator, then emits only the
official detail-page resources.  Attachment discovery belongs to the later
detail-page expansion step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from nhi_rule_history.contracts import (
    ContractError,
    DISCOVERED_RESOURCE_SCHEMA,
    assert_allowed_url,
    canonical_url,
    stable_id,
)
from nhi_rule_history.discovery.base import DiscoveryContext
from nhi_rule_history.discovery.nhi_current import decode_nhi_html


LISTING_PATH_RE = re.compile(r"^/ch/lp-3258-1\.html$")
DETAIL_PATH_RE = re.compile(
    r"^/ch/cp-[0-9]+-[0-9A-Za-z]+-[0-9]+-[0-9]+\.html$"
)
DATE_RE = re.compile(r"^[0-9]{3,4}\s*[-/.]\s*[0-9]{1,2}\s*[-/.]\s*[0-9]{1,2}$")
TOTAL_RE = re.compile(r"共\s*(?P<total>[0-9][0-9,]*)\s*筆")
PAGE_FRACTION_RE = re.compile(
    r"第?\s*(?P<current>[0-9][0-9,]*)\s*/\s*"
    r"(?P<pages>[0-9][0-9,]*)\s*頁"
)
PAGE_WORD_RE = re.compile(
    r"第\s*(?P<current>[0-9][0-9,]*)\s*頁"
    r".*?共\s*(?P<pages>[0-9][0-9,]*)\s*頁"
)
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _integer(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value.replace(",", ""))
    except ValueError as exc:
        raise ContractError(
            f"NHI listing {field_name} is not an integer"
        ) from exc
    if parsed < 1:
        raise ContractError(f"NHI listing {field_name} must be positive")
    return parsed


@dataclass(frozen=True)
class ListingAnchor:
    href: str
    text: str
    title: str
    rel: str
    classes: tuple[str, ...]


@dataclass(frozen=True)
class ListingCell:
    text: str
    data_title: str
    classes: tuple[str, ...]
    anchors: tuple[ListingAnchor, ...]


@dataclass(frozen=True)
class ListingRow:
    cells: tuple[ListingCell, ...]


@dataclass(frozen=True)
class PaginationAnchor:
    href: str
    text: str
    rel: str
    classes: tuple[str, ...]


@dataclass
class _OpenCell:
    data_title: str
    classes: tuple[str, ...]
    text_parts: list[str] = field(default_factory=list)
    anchors: list[ListingAnchor] = field(default_factory=list)


@dataclass
class _OpenAnchor:
    href: str
    title: str
    rel: str
    classes: tuple[str, ...]
    text_parts: list[str] = field(default_factory=list)


class NhiListingParser(HTMLParser):
    """Parse one ``section.list > table.rwdTable`` and its pagination."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[ListingRow] = []
        self.headers: list[str] = []
        self.pagination_anchors: list[PaginationAnchor] = []
        self.structural_errors: list[str] = []
        self.listing_sections = 0
        self.pagination_sections = 0
        self.declared_total: int | None = None
        self.current_page: int | None = None
        self.declared_pages: int | None = None

        self._tag_stack: list[str] = []
        self._listing_depth: int | None = None
        self._listing_table_depth: int | None = None
        self._thead_depth: int | None = None
        self._header_cell_depth: int | None = None
        self._header_text: list[str] = []
        self._tbody_depth: int | None = None
        self._row_depth: int | None = None
        self._row_cells: list[ListingCell] = []
        self._cell_depth: int | None = None
        self._cell: _OpenCell | None = None
        self._anchor_depth: int | None = None
        self._anchor: _OpenAnchor | None = None

        self._pagination_depth: int | None = None
        self._pagination_page_list_depth: int | None = None
        self._pagination_text: list[str] = []
        self._pagination_attributes: dict[str, str] = {}

    @property
    def _depth(self) -> int:
        return len(self._tag_stack)

    @property
    def _inside_listing(self) -> bool:
        return self._listing_depth is not None

    @property
    def _inside_pagination(self) -> bool:
        return self._pagination_depth is not None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {
            key: value or ""
            for key, value in attrs
        }
        classes = tuple(
            sorted(set(attributes.get("class", "").split()))
        )
        self._tag_stack.append(tag)
        depth = self._depth

        if tag == "section" and "list" in classes:
            self.listing_sections += 1
            if self._listing_depth is not None:
                self.structural_errors.append("nested_listing_surface")
            else:
                self._listing_depth = depth
            return

        if tag == "section" and "pagination" in classes:
            self.pagination_sections += 1
            if self._pagination_depth is not None:
                self.structural_errors.append("nested_pagination_surface")
            else:
                self._pagination_depth = depth
                self._pagination_attributes = attributes
                self._pagination_text = []
            return

        if self._inside_listing:
            if (
                tag == "table"
                and "rwdTable" in classes
                and depth == (self._listing_depth or 0) + 1
            ):
                if self._listing_table_depth is not None:
                    self.structural_errors.append(
                        "nested_listing_table"
                    )
                else:
                    self._listing_table_depth = depth
                return
            if (
                tag == "thead"
                and self._listing_table_depth is not None
            ):
                if self._thead_depth is not None:
                    self.structural_errors.append(
                        "nested_listing_thead"
                    )
                else:
                    self._thead_depth = depth
                return
            if (
                tag == "th"
                and self._thead_depth is not None
            ):
                if self._header_cell_depth is not None:
                    self.structural_errors.append(
                        "nested_listing_header_cell"
                    )
                else:
                    self._header_cell_depth = depth
                    self._header_text = []
                return
            if tag == "tbody":
                if self._listing_table_depth is None:
                    self.structural_errors.append(
                        "listing_tbody_outside_rwd_table"
                    )
                    return
                if self._tbody_depth is not None:
                    self.structural_errors.append("nested_listing_tbody")
                else:
                    self._tbody_depth = depth
                return
            if tag == "tr" and self._tbody_depth is not None:
                if self._row_depth is not None:
                    self.structural_errors.append("nested_listing_row")
                else:
                    self._row_depth = depth
                    self._row_cells = []
                return
            if tag == "td" and self._row_depth is not None:
                if self._cell_depth is not None:
                    self.structural_errors.append("nested_listing_cell")
                else:
                    self._cell_depth = depth
                    self._cell = _OpenCell(
                        data_title=_normalized_text(
                            attributes.get("data-title", "")
                        ),
                        classes=classes,
                    )
                return

        if (
            self._inside_pagination
            and tag == "ul"
            and "page" in classes
        ):
            if self._pagination_page_list_depth is not None:
                self.structural_errors.append(
                    "nested_pagination_page_list"
                )
            else:
                self._pagination_page_list_depth = depth
            return

        if tag == "a" and (
            self._cell_depth is not None
            or self._pagination_page_list_depth is not None
        ):
            if self._anchor_depth is not None:
                self.structural_errors.append("nested_listing_anchor")
                return
            href = attributes.get("href", "").strip()
            if not href:
                self.structural_errors.append("listing_anchor_without_href")
                return
            self._anchor_depth = depth
            self._anchor = _OpenAnchor(
                href=href,
                title=_normalized_text(attributes.get("title", "")),
                rel=_normalized_text(attributes.get("rel", "")).casefold(),
                classes=classes,
            )

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._inside_pagination:
            self._pagination_text.append(data)
        if self._header_cell_depth is not None:
            self._header_text.append(data)
        if self._cell is not None:
            self._cell.text_parts.append(data)
        if self._anchor is not None:
            self._anchor.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        depth = self._depth

        if tag == "th" and self._header_cell_depth == depth:
            self.headers.append(
                _normalized_text("".join(self._header_text))
            )
            self._header_cell_depth = None
            self._header_text = []

        if tag == "a" and self._anchor_depth == depth:
            assert self._anchor is not None
            anchor = ListingAnchor(
                href=self._anchor.href,
                text=_normalized_text("".join(self._anchor.text_parts)),
                title=self._anchor.title,
                rel=self._anchor.rel,
                classes=self._anchor.classes,
            )
            if self._cell is not None:
                self._cell.anchors.append(anchor)
            elif self._inside_pagination:
                self.pagination_anchors.append(
                    PaginationAnchor(
                        href=anchor.href,
                        text=anchor.text,
                        rel=anchor.rel,
                        classes=anchor.classes,
                    )
                )
            else:
                self.structural_errors.append("orphan_listing_anchor")
            self._anchor_depth = None
            self._anchor = None

        if tag == "td" and self._cell_depth == depth:
            if self._cell is None:
                self.structural_errors.append("listing_cell_state_lost")
            else:
                self._row_cells.append(
                    ListingCell(
                        text=_normalized_text(
                            "".join(self._cell.text_parts)
                        ),
                        data_title=self._cell.data_title,
                        classes=self._cell.classes,
                        anchors=tuple(self._cell.anchors),
                    )
                )
            self._cell_depth = None
            self._cell = None

        if tag == "tr" and self._row_depth == depth:
            if not self._row_cells:
                self.structural_errors.append("empty_listing_row")
            else:
                self.rows.append(ListingRow(cells=tuple(self._row_cells)))
            self._row_depth = None
            self._row_cells = []

        if tag == "tbody" and self._tbody_depth == depth:
            self._tbody_depth = None

        if tag == "thead" and self._thead_depth == depth:
            self._thead_depth = None

        if tag == "table" and self._listing_table_depth == depth:
            if self._tbody_depth is not None:
                self.structural_errors.append(
                    "unclosed_listing_tbody"
                )
            self._listing_table_depth = None

        if tag == "section" and self._listing_depth == depth:
            if self._row_depth is not None or self._cell_depth is not None:
                self.structural_errors.append("unclosed_listing_row")
            if self._listing_table_depth is not None:
                self.structural_errors.append(
                    "unclosed_listing_table"
                )
            self._listing_depth = None

        if (
            tag == "ul"
            and self._pagination_page_list_depth == depth
        ):
            self._pagination_page_list_depth = None

        if tag == "section" and self._pagination_depth == depth:
            self._finish_pagination()
            self._pagination_depth = None

        self._pop_stack(tag)

    def _pop_stack(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self._tag_stack:
            return
        if self._tag_stack[-1] == tag:
            self._tag_stack.pop()
            return
        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index] == tag:
                del self._tag_stack[index:]
                return

    def _finish_pagination(self) -> None:
        text = _normalized_text("".join(self._pagination_text))
        total_values: list[int] = []
        current_values: list[int] = []
        page_values: list[int] = []

        if self._pagination_attributes.get("data-total"):
            total_values.append(
                _integer(
                    self._pagination_attributes["data-total"],
                    field_name="declared total",
                )
            )
        if self._pagination_attributes.get("data-current-page"):
            current_values.append(
                _integer(
                    self._pagination_attributes["data-current-page"],
                    field_name="current page",
                )
            )
        if self._pagination_attributes.get("data-total-pages"):
            page_values.append(
                _integer(
                    self._pagination_attributes["data-total-pages"],
                    field_name="declared pages",
                )
            )

        total_match = TOTAL_RE.search(text)
        if total_match:
            total_values.append(
                _integer(
                    total_match.group("total"),
                    field_name="declared total",
                )
            )

        fraction_match = PAGE_FRACTION_RE.search(text)
        word_match = PAGE_WORD_RE.search(text)
        page_match = fraction_match or word_match
        if page_match:
            current_values.append(
                _integer(
                    page_match.group("current"),
                    field_name="current page",
                )
            )
            page_values.append(
                _integer(
                    page_match.group("pages"),
                    field_name="declared pages",
                )
            )

        self.declared_total = self._one_declared_value(
            total_values,
            "declared total",
        )
        self.current_page = self._one_declared_value(
            current_values,
            "current page",
        )
        self.declared_pages = self._one_declared_value(
            page_values,
            "declared pages",
        )

    def _one_declared_value(
        self,
        values: list[int],
        field_name: str,
    ) -> int | None:
        distinct = set(values)
        if len(distinct) > 1:
            self.structural_errors.append(
                f"conflicting_{field_name.replace(' ', '_')}"
            )
            return None
        return next(iter(distinct), None)

    def close(self) -> None:
        super().close()
        if self._listing_depth is not None:
            self.structural_errors.append("unclosed_listing_surface")
        if self._listing_table_depth is not None:
            self.structural_errors.append("unclosed_listing_table")
        if self._header_cell_depth is not None:
            self.structural_errors.append(
                "unclosed_listing_header_cell"
            )
        if self._pagination_depth is not None:
            self.structural_errors.append("unclosed_pagination_surface")
        if self._pagination_page_list_depth is not None:
            self.structural_errors.append(
                "unclosed_pagination_page_list"
            )


def parse_nhi_listing(
    payload: bytes,
    content_type: str | None = None,
) -> NhiListingParser:
    parser = NhiListingParser()
    parser.feed(decode_nhi_html(payload, content_type))
    parser.close()
    return parser


@dataclass(frozen=True)
class _ParsedRow:
    displayed_ordinal: int
    title: str
    document_number_raw: str
    document_date_raw: str
    listing_date_raw: str
    expiry_date_raw: str | None
    detail_url: str
    stable_identity: str
    page_number: int
    row_ordinal: int
    listing_page_url: str


class NhiListingAdapter:
    """Enumerate all ``lp-3258-1.html?pi=N&ps=20`` rows unfiltered."""

    kind = "nhi_3258"
    surface = "nhi_amendment_listing_3258"

    def _official_url(
        self,
        context: DiscoveryContext,
        raw_url: str,
        *,
        page_url: str,
    ) -> str:
        absolute = assert_allowed_url(
            urljoin(page_url, raw_url),
            context.client.allowed_hosts,
        )
        page_parts = urlsplit(page_url)
        target_parts = urlsplit(absolute)
        if (
            target_parts.scheme != "https"
            or target_parts.hostname != page_parts.hostname
        ):
            raise ContractError(
                "NHI listing points outside the official listing origin"
            )
        return canonical_url(absolute)

    def _validate_listing_url(
        self,
        context: DiscoveryContext,
        raw_url: str,
        *,
        page_url: str,
        expected_page: int,
    ) -> str:
        absolute = self._official_url(
            context,
            raw_url,
            page_url=page_url,
        )
        parts = urlsplit(absolute)
        if LISTING_PATH_RE.fullmatch(parts.path) is None:
            raise ContractError(
                "NHI listing pagination URL does not match the expected page"
            )
        query_pairs = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
        if not query_pairs and expected_page == 1:
            return absolute
        if (
            len(query_pairs) != 2
            or dict(query_pairs)
            != {"pi": str(expected_page), "ps": "20"}
            or len(dict(query_pairs)) != len(query_pairs)
        ):
            raise ContractError(
                "NHI listing pagination query does not match pi/ps contract"
            )
        return absolute

    def _detail_url(
        self,
        context: DiscoveryContext,
        raw_url: str,
        *,
        page_url: str,
    ) -> str:
        absolute = self._official_url(
            context,
            raw_url,
            page_url=page_url,
        )
        parts = urlsplit(absolute)
        if not DETAIL_PATH_RE.fullmatch(parts.path) or parts.query:
            raise ContractError(
                "NHI listing row does not point to an official detail page"
            )
        return absolute

    def _validate_parser(
        self,
        parser: NhiListingParser,
        *,
        expected_page: int,
    ) -> None:
        if parser.listing_sections != 1:
            raise ContractError(
                "NHI listing selector drift: expected one section.list"
            )
        if parser.pagination_sections != 1:
            raise ContractError(
                "NHI listing selector drift: expected one pagination surface"
            )
        if parser.structural_errors:
            raise ContractError(
                "NHI listing structure is ambiguous: "
                + ",".join(sorted(set(parser.structural_errors)))
            )
        if not parser.rows:
            raise ContractError(
                f"NHI listing row-count collapse on page {expected_page}"
            )
        if parser.headers != [
            "編號",
            "主旨",
            "發文字號",
            "發文日期",
            "刊登日期",
            "刊登期限",
        ]:
            raise ContractError(
                "NHI listing table-header contract drift"
            )
        if (
            parser.declared_total is None
            or parser.current_page is None
            or parser.declared_pages is None
        ):
            raise ContractError(
                "NHI listing pagination declaration is incomplete"
            )
        if parser.current_page != expected_page:
            raise ContractError(
                "NHI listing returned a different current page than requested"
            )
        if parser.current_page > parser.declared_pages:
            raise ContractError(
                "NHI listing current page exceeds declared last page"
            )

    def _row(
        self,
        context: DiscoveryContext,
        row: ListingRow,
        *,
        page_number: int,
        row_ordinal: int,
        page_url: str,
    ) -> _ParsedRow:
        if len(row.cells) != 6:
            raise ContractError(
                "NHI listing row must contain exactly six cells"
            )
        (
            ordinal_cell,
            title_cell,
            document_number_cell,
            document_date_cell,
            publication_date_cell,
            expiry_date_cell,
        ) = row.cells
        if any(
            cell.anchors
            for cell in (
                ordinal_cell,
                document_number_cell,
                document_date_cell,
                publication_date_cell,
                expiry_date_cell,
            )
        ):
            raise ContractError(
                "NHI listing non-title cell unexpectedly contains a link"
            )
        if len(title_cell.anchors) != 1:
            raise ContractError(
                "NHI listing row must contain exactly one official detail link"
            )
        anchor = title_cell.anchors[0]
        detail_url = self._detail_url(
            context,
            anchor.href,
            page_url=page_url,
        )
        title = _normalized_text(anchor.text)
        if not title:
            raise ContractError("NHI listing row title is empty")
        if title != title_cell.text:
            raise ContractError(
                "NHI listing title cell contains text outside its detail link"
            )
        displayed_ordinal = _integer(
            ordinal_cell.text,
            field_name="displayed row ordinal",
        )
        expected_displayed_ordinal = (
            (page_number - 1) * 20 + row_ordinal
        )
        if displayed_ordinal != expected_displayed_ordinal:
            raise ContractError(
                "NHI listing displayed ordinal does not match page locator"
            )
        document_number_raw = document_number_cell.text
        if not document_number_raw:
            raise ContractError("NHI listing document number is empty")
        required_dates = (
            document_date_cell.text,
            publication_date_cell.text,
        )
        if any(
            DATE_RE.fullmatch(value) is None
            for value in required_dates
        ):
            raise ContractError(
                "NHI listing row contains an unsupported date value"
            )
        expiry_date_raw = expiry_date_cell.text or None
        if (
            expiry_date_raw is not None
            and DATE_RE.fullmatch(expiry_date_raw) is None
        ):
            raise ContractError(
                "NHI listing row contains an unsupported expiry date"
            )
        document_date_raw, listing_date_raw = required_dates
        stable_identity = "url:" + detail_url
        return _ParsedRow(
            displayed_ordinal=displayed_ordinal,
            title=title,
            document_number_raw=document_number_raw,
            document_date_raw=document_date_raw,
            listing_date_raw=listing_date_raw,
            expiry_date_raw=expiry_date_raw,
            detail_url=detail_url,
            stable_identity=stable_identity,
            page_number=page_number,
            row_ordinal=row_ordinal,
            listing_page_url=page_url,
        )

    def _next_page_url(
        self,
        context: DiscoveryContext,
        parser: NhiListingParser,
        *,
        page_url: str,
        page_number: int,
        declared_pages: int,
    ) -> str | None:
        numeric_targets: set[str] = set()
        next_targets: set[str] = set()

        for anchor in parser.pagination_anchors:
            text = _normalized_text(anchor.text)
            if text.replace(",", "").isdigit():
                linked_page = _integer(
                    text,
                    field_name="pagination link",
                )
                if linked_page == page_number + 1:
                    numeric_targets.add(
                        self._validate_listing_url(
                            context,
                            anchor.href,
                            page_url=page_url,
                            expected_page=linked_page,
                        )
                    )

            rel_tokens = set(anchor.rel.split())
            is_next = (
                "next" in rel_tokens
                or text in {"下一頁", "下頁", "Next"}
                or bool(set(anchor.classes).intersection({"next", "nextPage"}))
            )
            if is_next:
                next_targets.add(
                    self._validate_listing_url(
                        context,
                        anchor.href,
                        page_url=page_url,
                        expected_page=page_number + 1,
                    )
                )

        candidates = numeric_targets | next_targets
        if len(candidates) > 1:
            raise ContractError(
                "NHI listing has conflicting next-page URLs"
            )
        if page_number < declared_pages:
            if not candidates:
                raise ContractError(
                    "NHI listing stopped before its declared last page"
                )
            return next(iter(candidates))
        if candidates:
            raise ContractError(
                "NHI listing last page still declares an active next page"
            )
        return None

    def _resource(
        self,
        context: DiscoveryContext,
        row: _ParsedRow,
        occurrences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema": DISCOVERED_RESOURCE_SCHEMA,
            "resource_id": stable_id(
                "nhi-listing-detail",
                context.adapter["id"],
                row.stable_identity,
            ),
            "adapter_id": context.adapter["id"],
            "resource_kind": "official_detail_page",
            "source_url": row.detail_url,
            "discovery_locator": {
                "surface": self.surface,
                "stable_row_identity": row.stable_identity,
                "displayed_ordinal": row.displayed_ordinal,
                "document_number_raw": row.document_number_raw,
                "document_date_raw": row.document_date_raw,
                "listing_date_raw": row.listing_date_raw,
                "expiry_date_raw": row.expiry_date_raw,
                "listing_occurrences": occurrences,
            },
            "source_label": row.title,
            "fetch_state": "pending",
        }

    def discover(self, context: DiscoveryContext) -> dict[str, Any]:
        base_url = canonical_url(context.adapter["base_url"])
        base_parts = urlsplit(base_url)
        if (
            base_parts.scheme != "https"
            or base_parts.hostname != "www.nhi.gov.tw"
            or not LISTING_PATH_RE.fullmatch(base_parts.path)
            or base_parts.query
        ):
            raise ContractError(
                "NHI listing adapter requires the official page-one URL"
            )

        maximum_pages = int(context.adapter.get("max_pages", 10000))
        if maximum_pages < 1:
            raise ContractError("NHI listing max_pages must be positive")

        rows_by_identity: dict[str, _ParsedRow] = {}
        occurrences_by_identity: dict[str, list[dict[str, Any]]] = {}
        seen_page_urls: set[str] = set()
        seen_page_row_sets: dict[tuple[str, ...], int] = {}
        declared_total: int | None = None
        declared_pages: int | None = None
        page_number = 1
        page_url = base_url
        observed_row_occurrences = 0

        while True:
            if page_number > maximum_pages:
                raise ContractError(
                    "NHI listing exceeded the configured page bound"
                )
            if page_url in seen_page_urls:
                raise ContractError("NHI listing pagination cycle detected")
            seen_page_urls.add(page_url)

            observation = context.recorder.observe(
                adapter_id=context.adapter["id"],
                request_url=page_url,
                locator={
                    "surface": self.surface,
                    "page_number": page_number,
                },
            )
            parser = parse_nhi_listing(
                observation["payload"],
                observation["headers"].get("content-type"),
            )
            self._validate_parser(parser, expected_page=page_number)
            assert parser.declared_total is not None
            assert parser.declared_pages is not None

            if declared_total is None:
                declared_total = parser.declared_total
                declared_pages = parser.declared_pages
                if declared_pages > maximum_pages:
                    raise ContractError(
                        "NHI listing declared page count exceeds page bound"
                    )
            elif (
                parser.declared_total != declared_total
                or parser.declared_pages != declared_pages
            ):
                raise ContractError(
                    "NHI listing total/page declarations changed during crawl"
                )
            assert declared_pages is not None

            page_identities: list[str] = []
            for row_ordinal, source_row in enumerate(parser.rows, 1):
                parsed_row = self._row(
                    context,
                    source_row,
                    page_number=page_number,
                    row_ordinal=row_ordinal,
                    page_url=page_url,
                )
                page_identities.append(parsed_row.stable_identity)
                observed_row_occurrences += 1

                previous = rows_by_identity.get(parsed_row.stable_identity)
                if previous is not None and (
                    previous.title != parsed_row.title
                    or previous.listing_date_raw
                    != parsed_row.listing_date_raw
                    or previous.detail_url != parsed_row.detail_url
                ):
                    raise ContractError(
                        "duplicate NHI listing identity has conflicting row data"
                    )

                occurrence = {
                    "page_number": page_number,
                    "row_ordinal": row_ordinal,
                    "listing_page_url": page_url,
                }
                prior_occurrences = occurrences_by_identity.setdefault(
                    parsed_row.stable_identity,
                    [],
                )
                if any(
                    item["page_number"] == page_number
                    for item in prior_occurrences
                ):
                    raise ContractError(
                        "duplicate NHI listing identity occurs twice on one page"
                    )
                prior_occurrences.append(occurrence)
                rows_by_identity.setdefault(
                    parsed_row.stable_identity,
                    parsed_row,
                )

            page_signature = tuple(page_identities)
            if page_signature in seen_page_row_sets:
                raise ContractError(
                    "NHI listing repeated an entire page under a new locator"
                )
            seen_page_row_sets[page_signature] = page_number

            next_url = self._next_page_url(
                context,
                parser,
                page_url=page_url,
                page_number=page_number,
                declared_pages=declared_pages,
            )
            if next_url is None:
                break
            if next_url in seen_page_urls:
                raise ContractError("NHI listing pagination cycle detected")
            page_url = next_url
            page_number += 1

        assert declared_total is not None
        assert declared_pages is not None
        if page_number != declared_pages:
            raise ContractError(
                "NHI listing observed page count differs from declared pages"
            )
        if len(rows_by_identity) != declared_total:
            raise ContractError(
                "NHI listing declared-vs-observed total mismatch: "
                f"declared={declared_total} "
                f"observed_unique={len(rows_by_identity)}"
            )

        # Validate the entire crawl before publishing any resource rows.
        for identity in sorted(rows_by_identity):
            occurrences = sorted(
                occurrences_by_identity[identity],
                key=lambda item: (
                    item["page_number"],
                    item["row_ordinal"],
                ),
            )
            context.recorder.record_resource(
                self._resource(
                    context,
                    rows_by_identity[identity],
                    occurrences,
                )
            )

        return {
            "adapter_id": context.adapter["id"],
            "kind": self.kind,
            "surface": self.surface,
            "declared_pages": declared_pages,
            "observed_pages": page_number,
            "declared_rows": declared_total,
            "observed_row_occurrences": observed_row_occurrences,
            "recorded_detail_resources": len(rows_by_identity),
            "partitions": [
                {
                    "adapter_id": context.adapter["id"],
                    "partition_id": self.surface,
                    "expected_rows": declared_total,
                    "fetched_rows": len(rows_by_identity),
                }
            ],
        }
