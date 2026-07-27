"""Deterministic visible-text blocks from captured official detail HTML."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from nhi_rule_history.contracts import sha256_bytes, stable_id


_IGNORED = {"script", "style", "noscript", "svg"}


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
