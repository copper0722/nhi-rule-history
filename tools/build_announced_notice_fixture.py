#!/usr/bin/env python3
"""Cut a small, checksum-pinned test fixture from an official notice ODT.

The fixture keeps the XML declaration, the root element's namespace
declarations and the exact bytes of ``office:body``.  Automatic styles and
fonts are dropped, so the fixture is a few kilobytes of official text.  The
cut is refused unless the reduced document yields the same project blocks as
the official file when both are identified by the official artifact hash.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

from nhi_rule_history.announced_notice import read_odt_document


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
    b"</manifest:manifest>"
)


def fixture_odt(content_xml: bytes) -> bytes:
    """Rebuild a minimal ODT container around a fixture ``content.xml``."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            b"application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("content.xml", content_xml, zipfile.ZIP_DEFLATED)
        archive.writestr(
            "META-INF/manifest.xml", _MANIFEST_XML, zipfile.ZIP_DEFLATED
        )
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("odt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = args.odt.read_bytes()
    official_sha = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        content = archive.read("content.xml")
    reduced = reduced_content(content)
    official = read_odt_document(payload)
    fixture = read_odt_document(
        fixture_odt(reduced), artifact_sha256=official_sha
    )
    if [
        (item.block_id, item.text, item.generated_label)
        for item in official.paragraphs
    ] != [
        (item.block_id, item.text, item.generated_label)
        for item in fixture.paragraphs
    ]:
        raise SystemExit("reduced fixture does not reproduce the official blocks")
    args.output.write_bytes(reduced)
    print(
        json.dumps(
            {
                "official_artifact_sha256": official_sha,
                "official_byte_size": len(payload),
                "fixture": args.output.name,
                "fixture_sha256": hashlib.sha256(reduced).hexdigest(),
                "fixture_byte_size": len(reduced),
                "block_count": len(official.paragraphs),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
