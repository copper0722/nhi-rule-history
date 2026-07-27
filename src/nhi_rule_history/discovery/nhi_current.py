"""Deterministic discovery for NHI current cumulative anchor surfaces.

The two NHI pages are acquisition anchors only.  This adapter preserves their
declared file groups, attachment order, labels, and exact URLs.  It does not
interpret page publication/update metadata as a legal effective date.
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


@dataclass(frozen=True)
class NhiAttachment:
    href: str
    title: str
    visible_label: str


@dataclass
class NhiFileGroup:
    source_label: str
    attachments: list[NhiAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class _OpenAnchor:
    href: str
    title: str


class NhiCurrentParser(HTMLParser):
    """Read only the official ``section.fileDownload`` declaration surface."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.groups: list[NhiFileGroup] = []
        self.page_title = ""
        self.file_download_sections = 0
        self._tag_stack: list[str] = []
        self._file_section_depth: int | None = None
        self._title_depth: int | None = None
        self._title_text: list[str] = []
        self._label_depth: int | None = None
        self._label_text: list[str] = []
        self._pending_label: str | None = None
        self._download_list_depth: int | None = None
        self._current_group: NhiFileGroup | None = None
        self._anchor_depth: int | None = None
        self._open_anchor: _OpenAnchor | None = None
        self._anchor_text: list[str] = []
        self.structural_errors: list[str] = []

    @property
    def _depth(self) -> int:
        return len(self._tag_stack)

    @property
    def _inside_file_section(self) -> bool:
        return self._file_section_depth is not None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        self._tag_stack.append(tag)
        depth = self._depth

        if tag == "title" and self._title_depth is None:
            self._title_depth = depth
            self._title_text = []

        if (
            tag == "section"
            and "fileDownload" in classes
            and self._file_section_depth is None
        ):
            self._file_section_depth = depth
            self.file_download_sections += 1
            return

        if not self._inside_file_section:
            return

        if tag == "span" and "fileName" in classes:
            if self._label_depth is not None:
                self.structural_errors.append("nested_file_name")
            if self._pending_label is not None:
                self.structural_errors.append("file_name_without_download_list")
            self._label_depth = depth
            self._label_text = []
            return

        if tag == "ol" and "downloadFiles" in classes:
            if self._download_list_depth is not None:
                self.structural_errors.append("nested_download_list")
                return
            if not self._pending_label:
                self.structural_errors.append("download_list_without_designation")
                return
            self._download_list_depth = depth
            self._current_group = NhiFileGroup(source_label=self._pending_label)
            self._pending_label = None
            return

        if tag == "a" and self._download_list_depth is not None:
            if self._anchor_depth is not None:
                self.structural_errors.append("nested_attachment_anchor")
                return
            href = attributes.get("href")
            if not href:
                self.structural_errors.append("attachment_anchor_without_href")
                return
            self._anchor_depth = depth
            self._open_anchor = _OpenAnchor(
                href=href,
                title=attributes.get("title") or "",
            )
            self._anchor_text = []

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._title_depth is not None:
            self._title_text.append(data)
        if self._label_depth is not None:
            self._label_text.append(data)
        if self._anchor_depth is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        depth = self._depth
        if self._anchor_depth == depth and tag == "a":
            assert self._open_anchor is not None
            if self._current_group is None:
                self.structural_errors.append("attachment_without_group")
            else:
                self._current_group.attachments.append(
                    NhiAttachment(
                        href=self._open_anchor.href,
                        title=_normalized_text(self._open_anchor.title),
                        visible_label=_normalized_text("".join(self._anchor_text)),
                    )
                )
            self._anchor_depth = None
            self._open_anchor = None
            self._anchor_text = []

        if self._label_depth == depth and tag == "span":
            label = _normalized_text("".join(self._label_text))
            if not label:
                self.structural_errors.append("empty_source_designation")
            self._pending_label = label or None
            self._label_depth = None
            self._label_text = []

        if self._download_list_depth == depth and tag == "ol":
            if self._current_group is None:
                self.structural_errors.append("download_list_without_group")
            else:
                self.groups.append(self._current_group)
            self._download_list_depth = None
            self._current_group = None

        if self._file_section_depth == depth and tag == "section":
            if self._pending_label is not None:
                self.structural_errors.append("file_name_without_download_list")
                self._pending_label = None
            if self._download_list_depth is not None:
                self.structural_errors.append("unclosed_download_list")
                self._download_list_depth = None
                self._current_group = None
            self._file_section_depth = None

        if self._title_depth == depth and tag == "title":
            self.page_title = _normalized_text("".join(self._title_text))
            self._title_depth = None
            self._title_text = []

        if not self._tag_stack:
            return
        if self._tag_stack[-1] == tag:
            self._tag_stack.pop()
            return

        # HTML permits several optional end tags and void elements.  Recover
        # the stack without treating unrelated document chrome as a selector
        # failure; ambiguity inside the file declaration surface is recorded
        # explicitly above.
        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index] == tag:
                del self._tag_stack[index:]
                return


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def decode_nhi_html(payload: bytes, content_type: str | None = None) -> str:
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
    raise ContractError("NHI current page has no supported declared encoding")


def parse_nhi_current(
    payload: bytes, content_type: str | None = None
) -> NhiCurrentParser:
    parser = NhiCurrentParser()
    parser.feed(decode_nhi_html(payload, content_type))
    parser.close()
    return parser


def _attachment_identity(url: str) -> str:
    parsed = urlsplit(url)
    pfids = [
        value.strip()
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() == "pfid" and value.strip()
    ]
    if len(set(pfids)) > 1:
        raise ContractError("NHI attachment URL has conflicting PFID values")
    if pfids:
        return f"pfid:{parsed.hostname.casefold()}:{pfids[0]}"
    return f"url:{url}"


class _NhiCurrentAdapter:
    kind = ""
    surface = ""
    page_resource_kind = ""
    attachment_resource_kind = ""

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
        page = urlsplit(page_url)
        target = urlsplit(absolute)
        if target.scheme != "https" or target.hostname != page.hostname:
            raise ContractError(
                "NHI current anchor points to a non-official attachment URL"
            )
        return absolute

    def _resources(
        self,
        context: DiscoveryContext,
        *,
        page_url: str,
        parser: NhiCurrentParser,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if parser.file_download_sections < 1:
            raise ContractError("NHI current page has no fileDownload section")
        if parser.structural_errors:
            raise ContractError(
                "NHI current page structure is ambiguous: "
                + ",".join(sorted(set(parser.structural_errors)))
            )
        if not parser.groups:
            raise ContractError("NHI current page declares zero file groups")

        page_id = stable_id(
            "nhi-current-page",
            context.adapter["id"],
            page_url,
        )
        page_resource = {
            "schema": DISCOVERED_RESOURCE_SCHEMA,
            "resource_id": page_id,
            "adapter_id": context.adapter["id"],
            "resource_kind": self.page_resource_kind,
            "source_url": page_url,
            "discovery_locator": {"surface": self.surface},
            "source_label": parser.page_title or context.adapter["id"],
            "fetch_state": "cached_by_discovery",
        }

        resources: list[dict[str, Any]] = []
        identities: set[str] = set()
        for group_ordinal, group in enumerate(parser.groups, 1):
            if not group.source_label:
                raise ContractError("NHI current file group has no source designation")
            if not group.attachments:
                raise ContractError(
                    "NHI current file group declares zero attachments: "
                    f"{group.source_label}"
                )
            for attachment_ordinal, attachment in enumerate(group.attachments, 1):
                absolute = self._official_url(
                    context,
                    attachment.href,
                    page_url=page_url,
                )
                identity = _attachment_identity(absolute)
                if identity in identities:
                    raise ContractError(
                        "duplicate NHI current attachment stable identity: "
                        f"{identity}"
                    )
                identities.add(identity)
                resources.append(
                    {
                        "schema": DISCOVERED_RESOURCE_SCHEMA,
                        "resource_id": stable_id(
                            "nhi-current-attachment",
                            context.adapter["id"],
                            identity,
                        ),
                        "adapter_id": context.adapter["id"],
                        "resource_kind": self.attachment_resource_kind,
                        "source_url": absolute,
                        "parent_resource_id": page_id,
                        "discovery_locator": {
                            "surface": self.surface,
                            "group_ordinal": group_ordinal,
                            "attachment_ordinal": attachment_ordinal,
                            "source_designation_raw": group.source_label,
                            "attachment_title": attachment.title,
                            "attachment_visible_label": attachment.visible_label,
                            "stable_attachment_identity": identity,
                        },
                        "source_label": group.source_label,
                        "fetch_state": "pending",
                    }
                )
        if not resources:
            raise ContractError("NHI current page declares zero assets")
        return page_resource, resources

    def discover(self, context: DiscoveryContext) -> dict[str, Any]:
        page_url = canonical_url(context.adapter["base_url"])
        observation = context.recorder.observe(
            adapter_id=context.adapter["id"],
            request_url=page_url,
            locator={"surface": self.surface},
        )
        parser = parse_nhi_current(
            observation["payload"],
            observation["headers"].get("content-type"),
        )
        page_resource, attachment_resources = self._resources(
            context,
            page_url=page_url,
            parser=parser,
        )

        # Validate the complete page before appending any resource rows.
        context.recorder.record_resource(page_resource)
        for resource in attachment_resources:
            context.recorder.record_resource(resource)

        asset_count = len(attachment_resources)
        return {
            "adapter_id": context.adapter["id"],
            "kind": self.kind,
            "surface": self.surface,
            "declared_groups": len(parser.groups),
            "declared_assets": asset_count,
            "recorded_assets": asset_count,
            "partitions": [
                {
                    "adapter_id": context.adapter["id"],
                    "partition_id": self.surface,
                    "expected_rows": asset_count,
                    "fetched_rows": asset_count,
                }
            ],
        }


class NhiCurrentWholeAdapter(_NhiCurrentAdapter):
    kind = "nhi_current_whole"
    surface = "current_whole"
    page_resource_kind = "official_current_whole_page"
    attachment_resource_kind = "official_current_whole_attachment"


class NhiCurrentChaptersAdapter(_NhiCurrentAdapter):
    kind = "nhi_chapters"
    surface = "current_chapters"
    page_resource_kind = "official_current_chapters_page"
    attachment_resource_kind = "official_current_chapter_attachment"
