"""Deterministic visible-text blocks from captured official detail HTML."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from nhi_rule_history.contracts import sha256_bytes, stable_id


_IGNORED = {"script", "style", "noscript", "svg"}
_VOID_TAGS = {
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
_BOUNDARY_WHITESPACE = "\t\n\r\f\v \u00a0\u3000"


class _VisibleTextParser(HTMLParser):
    def __init__(self, artifact_sha256: str):
        super().__init__(convert_charrefs=True)
        self.artifact_sha256 = artifact_sha256
        self.stack: list[str] = []
        self.blocks: list[dict[str, Any]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.stack.append(tag.lower())

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.stack:
            reverse_index = self.stack[::-1].index(lowered)
            del self.stack[len(self.stack) - reverse_index - 1 :]

    def handle_data(self, data: str) -> None:
        if any(tag in _IGNORED for tag in self.stack):
            return
        text = " ".join(data.split())
        if not text:
            return
        sequence = len(self.blocks)
        locator = {
            "kind": "html_text",
            "text_node_index": sequence,
            "element_path": list(self.stack),
        }
        digest = sha256_bytes(text.encode("utf-8"))
        self.blocks.append(
            {
                "block_id": stable_id(
                    "nhi-update-html-block",
                    self.artifact_sha256,
                    str(sequence),
                    digest,
                ),
                "artifact_sha256": self.artifact_sha256,
                "locator": locator,
                "raw_text": text,
                "raw_text_sha256": digest,
            }
        )


def extract_html_text_blocks(
    payload: bytes, artifact_sha256: str
) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="replace")
    parser = _VisibleTextParser(artifact_sha256)
    parser.feed(text)
    return parser.blocks


class _MetadataFieldsParser(HTMLParser):
    """Capture structural label/value pairs without flattening source text."""

    _BLOCK_TAGS = {"p", "li", "div"}

    def __init__(self, artifact_sha256: str):
        super().__init__(convert_charrefs=True)
        self.artifact_sha256 = artifact_sha256
        self.ignored_depth = 0
        self.dl_stack: list[int] = []
        self.next_dl_id = 0
        self.current_row: list[dict[str, Any]] | None = None
        self.row_depth = 0
        self.current_cell: dict[str, Any] | None = None
        self.nested_cell_depth = 0
        self.block_stack: list[str] = []
        self.segment_parts: list[str] = []
        self.table_rows: list[list[dict[str, Any]]] = []
        self.definition_cells: list[dict[str, Any]] = []
        self.malformed = False

    def _start_cell(
        self,
        tag: str,
        *,
        container: str,
        dl_id: int | None = None,
    ) -> None:
        self.current_cell = {
            "tag": tag,
            "raw_parts": [],
            "visible_blocks": [],
            "container": container,
            "dl_id": dl_id,
        }
        self.block_stack = []
        self.segment_parts = []

    def _flush_segment(self) -> None:
        if self.current_cell is None:
            self.segment_parts = []
            return
        value = "".join(self.segment_parts).strip(_BOUNDARY_WHITESPACE)
        if value:
            self.current_cell["visible_blocks"].append(value)
        self.segment_parts = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        lowered = tag.lower()
        if self.ignored_depth:
            if lowered not in _VOID_TAGS:
                self.ignored_depth += 1
            return
        if lowered in _IGNORED:
            self.ignored_depth = 1
            return
        if lowered == "dl":
            if self.current_cell is not None:
                self.malformed = True
            self.next_dl_id += 1
            self.dl_stack.append(self.next_dl_id)
            return
        if lowered == "tr":
            if self.current_row is None:
                self.current_row = []
                self.row_depth = 1
            else:
                self.row_depth += 1
                self.malformed = True
            return
        if lowered in {"th", "td"} and self.current_row is not None:
            if self.current_cell is None:
                self._start_cell(lowered, container="table")
            else:
                self.nested_cell_depth += 1
                self.malformed = True
            return
        if (
            lowered in {"dt", "dd"}
            and self.current_row is None
            and self.current_cell is None
            and self.dl_stack
        ):
            self._start_cell(
                lowered,
                container="definition",
                dl_id=self.dl_stack[-1],
            )
            return
        if lowered in self._BLOCK_TAGS and self.current_cell is not None:
            self._flush_segment()
            self.block_stack.append(lowered)
            return
        if lowered == "br" and self.current_cell is not None:
            self.current_cell["raw_parts"].append("\n")
            self.segment_parts.append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self.ignored_depth or tag.lower() in _IGNORED:
            return
        if tag.lower() == "br" and self.current_cell is not None:
            self.current_cell["raw_parts"].append("\n")
            self.segment_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth or self.current_cell is None:
            return
        self.current_cell["raw_parts"].append(data)
        self.segment_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self.ignored_depth:
            self.ignored_depth -= 1
            return
        if lowered in self._BLOCK_TAGS and self.current_cell is not None:
            if not self.block_stack or self.block_stack[-1] != lowered:
                self.malformed = True
                return
            self._flush_segment()
            self.block_stack.pop()
            return
        if (
            self.current_cell is not None
            and lowered == self.current_cell["tag"]
        ):
            if self.nested_cell_depth:
                self.nested_cell_depth -= 1
                return
            self._flush_segment()
            cell = {
                "tag": self.current_cell["tag"],
                "raw_text": "".join(self.current_cell["raw_parts"]),
                "visible_blocks": list(
                    self.current_cell["visible_blocks"]
                ),
                "dl_id": self.current_cell["dl_id"],
            }
            if self.current_cell["container"] == "table":
                assert self.current_row is not None
                self.current_row.append(cell)
            else:
                self.definition_cells.append(cell)
            self.current_cell = None
            self.block_stack = []
            self.segment_parts = []
            return
        if lowered == "tr" and self.current_row is not None:
            if self.row_depth > 1:
                self.row_depth -= 1
                return
            if self.current_cell is None and self.current_row:
                self.table_rows.append(self.current_row)
            elif self.current_cell is not None:
                self.malformed = True
            self.current_row = None
            self.row_depth = 0
            return
        if lowered == "dl":
            if not self.dl_stack:
                self.malformed = True
                return
            if (
                self.current_cell is not None
                and self.current_cell["container"] == "definition"
            ):
                self.malformed = True
            self.dl_stack.pop()


def extract_html_metadata_fields(
    payload: bytes, artifact_sha256: str
) -> list[dict[str, Any]]:
    """Return structurally paired official metadata fields.

    Only a two-cell ``th``/``td`` row or an adjacent ``dt``/``dd`` pair is
    admitted. Unknown layouts are omitted so callers fail closed rather than
    infer field boundaries from flattened page text.
    """

    parser = _MetadataFieldsParser(artifact_sha256)
    parser.feed(payload.decode("utf-8", errors="replace"))
    if parser.malformed:
        return []
    fields: list[dict[str, Any]] = []
    for row_index, cells in enumerate(parser.table_rows):
        if (
            len(cells) != 2
            or cells[0]["tag"] != "th"
            or cells[1]["tag"] != "td"
        ):
            continue
        fields.append(
            {
                "label_raw": cells[0]["raw_text"],
                "value_raw": cells[1]["raw_text"],
                "value_blocks": cells[1]["visible_blocks"],
                "locator": {
                    "kind": "html_table_row",
                    "row_index": row_index,
                },
            }
        )
    cells = parser.definition_cells
    for pair_index in range(len(cells) - 1):
        label, value = cells[pair_index : pair_index + 2]
        if (
            label["tag"] != "dt"
            or value["tag"] != "dd"
            or label["dl_id"] != value["dl_id"]
        ):
            continue
        fields.append(
            {
                "label_raw": label["raw_text"],
                "value_raw": value["raw_text"],
                "value_blocks": value["visible_blocks"],
                "locator": {
                    "kind": "html_definition_pair",
                    "pair_index": pair_index,
                },
            }
        )
    return fields
