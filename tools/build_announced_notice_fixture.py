#!/usr/bin/env python3
"""Cut a small, checksum-pinned test fixture from an official notice ODT.

The fixture keeps the XML declaration, the root element's namespace
declarations and the exact bytes of ``office:body``.  Automatic styles and
fonts are dropped, so the fixture is a few kilobytes of official text.  The
cut is refused unless the reduced document yields the same project blocks as
the official file when both are identified by the official artifact hash.

With ``--numbering`` the fixture also keeps what automatic list labels depend
on: list styles (without layout properties other than the label position
mode and the text after the label), paragraph styles reduced to their name,
parent, list style and outline level, the outline style, the ODF 1.4 list
formats, and the generator named in ``meta.xml``.  The cut is then also
refused unless every reconstructed label, printed text and LibreOffice export
prefix equals the official file's.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from nhi_rule_history.announced_notice import read_odt_document


_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
_NUMBER = "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"
_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
_LOEXT = "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0"
_KEPT_LEVEL_PROPERTY = f"{{{_TEXT}}}list-level-position-and-space-mode"
_KEPT_ALIGNMENT = f"{{{_TEXT}}}label-followed-by"
_PARAGRAPH_ATTRIBUTES = (
    f"{{{_STYLE}}}name",
    f"{{{_STYLE}}}family",
    f"{{{_STYLE}}}parent-style-name",
    f"{{{_STYLE}}}list-style-name",
    f"{{{_STYLE}}}default-outline-level",
)


def reduced_content(content: bytes) -> bytes:
    root_start = content.index(b"<office:document-content")
    root_end = content.index(b">", root_start) + 1
    body_start = content.index(b"<office:body>")
    body_end = content.index(b"</office:body>") + len(b"</office:body>")
    declaration = content[:root_start]
    return (
        declaration
        + content[root_start:root_end]
        + content[body_start:body_end]
        + b"</office:document-content>\n"
    )


def _prefixes(start_tag: bytes) -> dict[str, str]:
    return {
        uri.decode("utf-8"): prefix.decode("utf-8")
        for prefix, uri in re.findall(rb'xmlns:([\w.-]+)="([^"]+)"', start_tag)
    }


def _serialize(element: ElementTree.Element, prefixes: dict[str, str]) -> str:
    def name(tag: str) -> str:
        uri, local = tag[1:].split("}", 1)
        return f"{prefixes[uri]}:{local}"

    attributes = "".join(
        f' {name(key)}="{_escape(value)}"' for key, value in element.attrib.items()
    )
    children = "".join(_serialize(child, prefixes) for child in element)
    text = _escape(element.text or "")
    if not children and not text:
        return f"<{name(element.tag)}{attributes}/>"
    return f"<{name(element.tag)}{attributes}>{text}{children}</{name(element.tag)}>"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_LEVEL_ATTRIBUTES = frozenset(
    {
        f"{{{_TEXT}}}level",
        f"{{{_TEXT}}}start-value",
        f"{{{_TEXT}}}display-levels",
        f"{{{_STYLE}}}num-format",
        f"{{{_STYLE}}}num-prefix",
        f"{{{_STYLE}}}num-suffix",
        f"{{{_STYLE}}}num-letter-sync",
        f"{{{_STYLE}}}num-list-format",
        f"{{{_STYLE}}}num-list-format-name",
        f"{{{_LOEXT}}}num-list-format",
        f"{{{_LOEXT}}}is-legal",
    }
)


def _numbering_nodes(
    container: ElementTree.Element | None, keep: dict[str, int] | None = None
) -> list[ElementTree.Element]:
    """List, outline, list-format and reduced paragraph styles of a container.

    ``keep`` maps each kept style name to the deepest list level it needs;
    ``None`` keeps everything.  The outline style is always kept.
    """

    kept: list[ElementTree.Element] = []
    if container is None:
        return kept
    for node in container:
        name = node.attrib.get(f"{{{_STYLE}}}name", "")
        if node.tag == f"{{{_TEXT}}}outline-style" or (
            node.tag == f"{{{_TEXT}}}list-style"
            and (keep is None or name in keep)
        ):
            copy = ElementTree.Element(node.tag, dict(node.attrib))
            deepest = 10 if keep is None or node.tag != f"{{{_TEXT}}}list-style" else keep[name]
            for level in node:
                try:
                    number = int(level.attrib.get(f"{{{_TEXT}}}level", "1"))
                except ValueError:
                    number = 1
                if number > deepest:
                    continue
                level_copy = ElementTree.SubElement(
                    copy,
                    level.tag,
                    {
                        key: value
                        for key, value in level.attrib.items()
                        if key in _LEVEL_ATTRIBUTES
                    },
                )
                for properties in level:
                    if properties.tag != f"{{{_STYLE}}}list-level-properties":
                        continue
                    mode = properties.attrib.get(_KEPT_LEVEL_PROPERTY)
                    properties_copy = ElementTree.SubElement(
                        level_copy,
                        properties.tag,
                        {} if mode is None else {_KEPT_LEVEL_PROPERTY: mode},
                    )
                    for alignment in properties:
                        followed = alignment.attrib.get(_KEPT_ALIGNMENT)
                        ElementTree.SubElement(
                            properties_copy,
                            alignment.tag,
                            {} if followed is None else {_KEPT_ALIGNMENT: followed},
                        )
            kept.append(copy)
        elif node.tag == f"{{{_NUMBER}}}num-list-format" and (
            keep is None or name in keep
        ):
            kept.append(node)
        elif (
            node.tag == f"{{{_STYLE}}}style"
            and node.attrib.get(f"{{{_STYLE}}}family") == "paragraph"
            and (keep is None or name in keep)
        ):
            kept.append(
                ElementTree.Element(
                    node.tag,
                    {
                        key: node.attrib[key]
                        for key in _PARAGRAPH_ATTRIBUTES
                        if key in node.attrib
                    },
                )
            )
    return kept


def _referenced_styles(
    body: ElementTree.Element,
    automatic: ElementTree.Element | None,
    common: ElementTree.Element | None,
) -> dict[str, int]:
    """Styles and list formats list labels depend on, with the levels used.

    Inside a list the paragraph style does not affect numbering, so only the
    style chains of paragraphs outside lists are kept, and only when they
    name a list style or an outline level.
    """

    def paragraph_styles(container: ElementTree.Element | None) -> dict:
        return {
            node.attrib.get(f"{{{_STYLE}}}name", ""): node
            for node in (container if container is not None else ())
            if node.tag == f"{{{_STYLE}}}style"
            and node.attrib.get(f"{{{_STYLE}}}family") == "paragraph"
        }

    automatic_paragraphs = paragraph_styles(automatic)
    common_paragraphs = paragraph_styles(common)
    parent: dict[int, ElementTree.Element] = {
        id(child): node for node in body.iter() for child in node
    }
    lists: dict[str, int] = {}
    outside: set[str] = set()
    for node in body.iter():
        depth = 0
        ancestor = parent.get(id(node))
        root_list = None
        while ancestor is not None:
            if ancestor.tag == f"{{{_TEXT}}}list":
                depth += 1
                root_list = ancestor
            ancestor = parent.get(id(ancestor))
        if node.tag == f"{{{_TEXT}}}list":
            name = node.attrib.get(f"{{{_TEXT}}}style-name", "")
            if root_list is not None and not name:
                name = root_list.attrib.get(f"{{{_TEXT}}}style-name", "")
            lists[name] = max(lists.get(name, 0), depth + 1)
        elif node.tag == f"{{{_TEXT}}}list-item":
            name = node.attrib.get(f"{{{_TEXT}}}style-override", "")
            lists[name] = max(lists.get(name, 0), depth)
        elif node.tag == f"{{{_TEXT}}}numbered-paragraph":
            name = node.attrib.get(f"{{{_TEXT}}}style-name", "")
            level = int(node.attrib.get(f"{{{_TEXT}}}level", "1") or "1")
            lists[name] = max(lists.get(name, 0), level)
        elif node.tag in {f"{{{_TEXT}}}p", f"{{{_TEXT}}}h"} and depth == 0:
            outside.add(node.attrib.get(f"{{{_TEXT}}}style-name", ""))
    kept: dict[str, int] = {}
    for name in outside:
        chain: list[str] = []
        style = automatic_paragraphs.get(name)
        if style is None:
            style = common_paragraphs.get(name)
        seen: set[int] = set()
        relevant = False
        while style is not None and id(style) not in seen:
            seen.add(id(style))
            chain.append(style.attrib.get(f"{{{_STYLE}}}name", ""))
            if f"{{{_STYLE}}}list-style-name" in style.attrib or style.attrib.get(
                f"{{{_STYLE}}}default-outline-level"
            ):
                relevant = True
                listed = style.attrib.get(f"{{{_STYLE}}}list-style-name")
                if listed:
                    lists[listed] = max(lists.get(listed, 0), 1)
                break
            style = common_paragraphs.get(
                style.attrib.get(f"{{{_STYLE}}}parent-style-name", "")
            )
        if relevant:
            for member in chain:
                kept[member] = 0
    for name, depth in lists.items():
        kept[name] = max(kept.get(name, 0), depth, 1)
    for container in (automatic, common):
        for node in container if container is not None else ():
            if (
                node.tag == f"{{{_TEXT}}}list-style"
                and node.attrib.get(f"{{{_STYLE}}}name") in lists
            ):
                for level in node:
                    word = level.attrib.get(f"{{{_STYLE}}}num-list-format-name")
                    if word:
                        kept[word] = 0
    kept.pop("", None)
    return kept


def reduced_numbering_parts(
    content: bytes, styles: bytes | None, meta: bytes | None
) -> tuple[bytes, bytes | None, bytes | None]:
    """content.xml, styles.xml and meta.xml cut down to the label inputs."""

    root_start = content.index(b"<office:document-content")
    root_end = content.index(b">", root_start) + 1
    body_start = content.index(b"<office:body>")
    body_end = content.index(b"</office:body>") + len(b"</office:body>")
    prefixes = _prefixes(content[root_start:root_end])
    content_root = ElementTree.fromstring(content)
    automatic = content_root.find(f"{{{_OFFICE}}}automatic-styles")
    styles_root = ElementTree.fromstring(styles) if styles is not None else None
    common = (
        styles_root.find(f"{{{_OFFICE}}}styles") if styles_root is not None else None
    )
    styles_automatic = (
        styles_root.find(f"{{{_OFFICE}}}automatic-styles")
        if styles_root is not None
        else None
    )
    keep = _referenced_styles(
        content_root.find(f"{{{_OFFICE}}}body"), automatic, common
    )
    reduced = (
        content[:root_start]
        + content[root_start:root_end]
        + (
            "<office:automatic-styles>"
            + "".join(
                _serialize(node, prefixes)
                for node in _numbering_nodes(automatic, keep)
            )
            + "</office:automatic-styles>"
        ).encode("utf-8")
        + content[body_start:body_end]
        + b"</office:document-content>\n"
    )
    reduced_styles = None
    if styles is not None:
        start = styles.index(b"<office:document-styles")
        end = styles.index(b">", start) + 1
        style_prefixes = _prefixes(styles[start:end])
        # Word writes its ODF 1.4 list formats among styles.xml's automatic
        # styles; only those are kept from there.
        word_formats = [
            node
            for node in _numbering_nodes(styles_automatic, keep)
            if node.tag == f"{{{_NUMBER}}}num-list-format"
        ]
        reduced_styles = (
            styles[:start]
            + styles[start:end]
            + (
                "<office:automatic-styles>"
                + "".join(_serialize(node, style_prefixes) for node in word_formats)
                + "</office:automatic-styles><office:styles>"
                + "".join(
                    _serialize(node, style_prefixes)
                    for node in _numbering_nodes(common, keep)
                )
                + "</office:styles></office:document-styles>\n"
            ).encode("utf-8")
        )
    reduced_meta = None
    if meta is not None:
        generator = ElementTree.fromstring(meta).find(
            f".//{{{_META}}}generator"
        )
        reduced_meta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-meta xmlns:office="{_OFFICE}" xmlns:meta="{_META}">'
            "<office:meta>"
            + (
                ""
                if generator is None
                else f"<meta:generator>{_escape(generator.text or '')}</meta:generator>"
            )
            + "</office:meta></office:document-meta>\n"
        ).encode("utf-8")
    return reduced, reduced_styles, reduced_meta


# No manifest version: office suites reject a manifest whose version differs
# from the office:version of content.xml, and official files omit it.
_MANIFEST_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<manifest:manifest xmlns:manifest='
    b'"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
    b'<manifest:file-entry manifest:full-path="/" '
    b'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
    b'<manifest:file-entry manifest:full-path="content.xml" '
    b'manifest:media-type="text/xml"/>'
    b"%s"
    b"</manifest:manifest>"
)


def fixture_odt(
    content_xml: bytes,
    styles_xml: bytes | None = None,
    meta_xml: bytes | None = None,
) -> bytes:
    """Rebuild a minimal ODT container around fixture XML parts."""

    extra = b"".join(
        b'<manifest:file-entry manifest:full-path="%s" '
        b'manifest:media-type="text/xml"/>' % name
        for name, part in ((b"styles.xml", styles_xml), (b"meta.xml", meta_xml))
        if part is not None
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            b"application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("content.xml", content_xml, zipfile.ZIP_DEFLATED)
        if styles_xml is not None:
            archive.writestr("styles.xml", styles_xml, zipfile.ZIP_DEFLATED)
        if meta_xml is not None:
            archive.writestr("meta.xml", meta_xml, zipfile.ZIP_DEFLATED)
        archive.writestr(
            "META-INF/manifest.xml", _MANIFEST_XML % extra, zipfile.ZIP_DEFLATED
        )
    return buffer.getvalue()


def _paragraph_signature(document, *, numbering: bool) -> list[tuple]:
    if not numbering:
        return [
            (item.block_id, item.text, item.generated_label)
            for item in document.paragraphs
        ]
    return [
        (
            item.block_id,
            item.text,
            item.generated_label,
            item.numbering_blocked,
            item.printed_text,
            item.export_prefix,
        )
        for item in document.paragraphs
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("odt", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--numbering",
        action="store_true",
        help="keep list styles and the generator; writes .styles.xml and "
        ".meta.xml beside the output",
    )
    args = parser.parse_args()
    payload = args.odt.read_bytes()
    official_sha = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        content = archive.read("content.xml")
        styles = archive.read("styles.xml") if "styles.xml" in names else None
        meta = archive.read("meta.xml") if "meta.xml" in names else None
    parts: dict[str, bytes] = {}
    if args.numbering:
        reduced, reduced_styles, reduced_meta = reduced_numbering_parts(
            content, styles, meta
        )
        fixture_payload = fixture_odt(reduced, reduced_styles, reduced_meta)
        stem = args.output.name.removesuffix(".content.xml")
        if reduced_styles is not None:
            parts[f"{stem}.styles.xml"] = reduced_styles
        if reduced_meta is not None:
            parts[f"{stem}.meta.xml"] = reduced_meta
    else:
        reduced = reduced_content(content)
        fixture_payload = fixture_odt(reduced)
    official = read_odt_document(payload)
    fixture = read_odt_document(fixture_payload, artifact_sha256=official_sha)
    if _paragraph_signature(
        official, numbering=args.numbering
    ) != _paragraph_signature(fixture, numbering=args.numbering):
        raise SystemExit("reduced fixture does not reproduce the official blocks")
    args.output.write_bytes(reduced)
    written = {args.output.name: reduced}
    for name, part in parts.items():
        (args.output.parent / name).write_bytes(part)
        written[name] = part
    print(
        json.dumps(
            {
                "official_artifact_sha256": official_sha,
                "official_byte_size": len(payload),
                "block_count": len(official.paragraphs),
                "labelled_blocks": sum(
                    1 for item in official.paragraphs if item.generated_label
                ),
                "files": {
                    name: {
                        "sha256": hashlib.sha256(part).hexdigest(),
                        "byte_size": len(part),
                    }
                    for name, part in written.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
