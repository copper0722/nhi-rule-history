"""Deterministic announced-text overlay from an official amendment notice.

An NHI drug-rule amendment notice ships an official comparison table
(``修訂對照表``) whose ``修訂後`` column states the announced text of each
amended clause.  This module turns one registered corpus source bundle into
that announced text, exactly as printed, with source spans, hashes and the
effective date stated in the attachment.  It makes no model call.

The result is an announced/current-text overlay with provenance.  It is not
canonical legal history: a comparison table is not a stable rule-identity
decision, its old/new columns do not prove direct predecessor adjacency, and
the revised column usually elides unchanged paragraphs.  Every clause is
therefore emitted as ``patch_only`` text; nothing here composes, promotes or
closes a clause version.

Parsing fails closed.  An unknown table grammar, an ambiguous or missing
effective date, a designation mismatch between the two columns, an unsupported
ODF feature, or any disagreement between this module's own XML traversal and
the project ODT block parser raises :class:`AnnouncedNoticeError`.

Automatic list labels (``1.``, ``(5)``) are not character data.  They are
reconstructed by :mod:`nhi_rule_history.odf_list_numbering` and printed
before the paragraph text followed by the label's tab, space or nothing; a
clause with a list feature that module does not reproduce is held back.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import re
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree

from nhi_rule_history.contracts import canonical_json_bytes
from nhi_rule_history.current_publication import semantic_comparison_text
from nhi_rule_history.odf_list_numbering import (
    LIST_LABEL_RULE_VERSION,
    ListLabel,
    resolve_list_labels,
)
from nhi_rule_history.pg.common import PgLoadError, object_fingerprint
from nhi_rule_history.update.odt import inspect_odt_document


PARSER_VERSION = "nhi-rule-history/announced-notice-parser/1.1.0"
TEXT_RULE_VERSION = (
    "nhi-rule-history/odt-paragraph-text-with-whitespace-elements/1.0.0"
)
PATCH_TEXT_JOIN = "\n\n"
CORPUS_BUNDLE_SCHEMA = "nhi-rule-history/corpus-source-bundle/v1"
ODT_MEDIA_TYPE = "application/vnd.oasis.opendocument.text"
OFFICIAL_URL_PREFIX = "https://www.nhi.gov.tw/ch/cp-"

_UUID_NAMESPACE = uuid.UUID("5b0c7d7e-2f55-4b6f-a2c4-6f1d8e0b9a31")

_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TAG_P = f"{{{_TEXT}}}p"
_TAG_H = f"{{{_TEXT}}}h"
_TAG_S = f"{{{_TEXT}}}s"
_TAG_TAB = f"{{{_TEXT}}}tab"
_TAG_LINE_BREAK = f"{{{_TEXT}}}line-break"
_TAG_NOTE = f"{{{_TEXT}}}note"
_TAG_TRACKED_CHANGES = f"{{{_TEXT}}}tracked-changes"
_TAG_ANNOTATION = f"{{{_OFFICE}}}annotation"
_TAG_TABLE = f"{{{_TABLE}}}table"
_TAG_ROW = f"{{{_TABLE}}}table-row"
_TAG_CELL = f"{{{_TABLE}}}table-cell"
_TAG_COVERED_CELL = f"{{{_TABLE}}}covered-table-cell"
_ATTR_SPACE_COUNT = f"{{{_TEXT}}}c"
_ATTR_COLUMNS_SPANNED = f"{{{_TABLE}}}number-columns-spanned"
_ATTR_ROWS_SPANNED = f"{{{_TABLE}}}number-rows-spanned"
_ATTR_COLUMNS_REPEATED = f"{{{_TABLE}}}number-columns-repeated"
_ATTR_ROWS_REPEATED = f"{{{_TABLE}}}number-rows-repeated"

# Column headers observed in official comparison tables.  Anything else is
# not a comparison table and is never guessed into one.  NHI announcements
# sometimes keep the drafting label ``建議修訂後`` on the revised column; it is
# announced text only inside an announcement (公告), never in a pre-announcement
# (預告) or any other notice.
_REVISED_HEADER_RE = re.compile(
    r"^(?P<prefix>建議)?修[訂正]後(?P<kind>給付規定|附表規定)$"
)
_ORIGINAL_HEADER_RE = re.compile(r"^原(?P<kind>給付規定|附表規定)$")
# Grammar matching runs on NFKC text: official files mix full-width forms
# and CJK compatibility ideographs (for example U+F98E for 年).  The stored
# source text is never normalized.
_EFFECTIVE_DATE_RE = re.compile(
    r"^\(\s*自\s*(?P<year>[0-9]{2,3})\s*年\s*(?P<month>[0-9]{1,2})\s*月"
    r"\s*(?P<day>[0-9]{1,2})\s*日\s*(?:起\s*)?生效\s*\)$"
)
_EFFECTIVE_WORD_RE = re.compile(r"生效")
# A clause designation is a dotted numeric code terminated by its own full
# stop, e.g. ``2.1.4.2.Rivaroxaban``.  ``2.18歲`` is a list item, not 2.18.
_CLAUSE_HEADING_RE = re.compile(
    r"^\s*(?P<code>[1-9][0-9]*(?:\.[0-9]+)+)\.(?![0-9])"
)
# Anything that may designate a clause or a general-rule item without the
# strict heading form: a dotted number after optional marks (``9.140 Bar``,
# ``9.57:Bar``, ``◎9.141.``, ``0.5.``) or an item ordinal (``五、``).  A
# row holding one never continues the previous clause.
_LOOSE_DESIGNATION_RE = re.compile(
    r"^[^0-9A-Za-z\u3400-\u9fff]*"
    r"(?:[0-9]+(?:\.[0-9]+)+|[一二三四五六七八九十百]+、)"
)
_APPENDIX_DESIGNATION_RE = re.compile(
    r"附表[一二三四五六七八九十百零〇]+(?:之[一二三四五六七八九十]+)?"
)
_NONE_CELL_RE = re.compile(r"^\(?\s*無\s*\)?[。.]?$")
# Omitted-text markers used by official tables: (略), (以下略), (餘略),
# 以下略, and a trailing 略 after a boundary character (checked on NFKC).
_OMISSION_RE = re.compile(
    r"\(\s*(?:以下|其餘|餘)?\s*略\s*\)"
    r"|(?:以下|其餘|餘)略"
    r"|(?:^|[\s:,、。)\]】~])略\s*[。.]?\s*$"
)
_UNSUPPORTED_CELL_TAGS = frozenset({_TAG_NOTE, _TAG_ANNOTATION})
# Manifest rows of these roles, and raw.md, are source files: the parser
# reads them or they identify the notice, so their size and SHA-256 are always
# checked and a registration proof never undoes them.  Any other role is a
# derived text layer (proofread.md and later layers) that the parser never
# reads.
SOURCE_FILE_ROLES = frozenset(
    {
        "declared_attachment",
        "deterministic_extraction",
        "detail_page",
        "rss_observation",
        "comparison_odt",
        "comparison_pdf",
    }
)
# Corpus bookkeeping may add or advance these ``extraction_status`` keys in
# manifest.json after registration.  Each maps to the values the key may have
# held at registration (``None``: absent).
_BOOKKEEPING_REVERSIONS: Mapping[str, tuple[str | None, ...]] = {
    "mineru": (None,),
    "proofread": ("not_started",),
}
# Top-level keys that the proofread lane adds after registration.  The parser
# reads neither; the effective date comes from the attachment.
_DERIVED_MANIFEST_KEYS = ("effective_date", "proofread_method")
_MAX_DERIVED_ROWS = 8


class AnnouncedNoticeError(PgLoadError):
    """An official notice violated a closed parsing or provenance contract."""


def stable_uuid(label: str, value: object) -> str:
    material = json.dumps(
        [label, value], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(_UUID_NAMESPACE, material))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def roc_date(year: str | int, month: str | int, day: str | int) -> date:
    """Return the Gregorian date for an ROC calendar date."""

    roc_year = int(year)
    if roc_year < 1:
        raise AnnouncedNoticeError("ROC year must be positive")
    try:
        return date(roc_year + 1911, int(month), int(day))
    except ValueError as exc:
        raise AnnouncedNoticeError("ROC date is not a calendar date") from exc


def grammar_text(text: str) -> str:
    """NFKC projection used only for grammar matching, never for storage."""

    return unicodedata.normalize("NFKC", text)


def parse_effective_statement(text: str) -> date | None:
    """Parse one stand-alone ``（自115年10月1日生效）`` statement."""

    match = _EFFECTIVE_DATE_RE.fullmatch(grammar_text(text).strip())
    if not match:
        return None
    return roc_date(match["year"], match["month"], match["day"])


def clause_heading_code(text: str) -> str | None:
    match = _CLAUSE_HEADING_RE.match(grammar_text(text))
    return match["code"] if match else None


def is_omission_marker(text: str) -> bool:
    return bool(_OMISSION_RE.search(grammar_text(text)))


# ---------------------------------------------------------------------------
# Corpus bundle


@dataclass(frozen=True)
class NoticeAttachment:
    declared_sequence: int
    file_name: str
    media_type: str
    sha256: str
    byte_size: int
    path: Path


@dataclass(frozen=True)
class NoticeBundle:
    bundle_dir: Path
    source_uid: str
    reference_number: str
    title: str
    official_url: str
    published_on: str
    manifest_sha256: str
    attachments: tuple[NoticeAttachment, ...]
    announcement_items: tuple[str, ...]
    raw_md_blocks: Mapping[str, tuple[tuple[str, str], ...]]
    manifest_identity: Mapping[str, Any] | None = None


def is_source_file_row(row: Mapping[str, Any]) -> bool:
    """Whether a manifest file row is pinned source rather than derived."""

    return (
        row.get("role") in SOURCE_FILE_ROLES
        or row.get("file_name") == "raw.md"
    )


def registered_manifest_identity(
    manifest_bytes: bytes, registered_sha256: str
) -> dict[str, Any]:
    """Prove that manifest.json is the registered one, up to later lanes.

    Corpus registration records the SHA-256 of the canonical manifest bytes.
    Later corpus lanes may re-serialize manifest.json, add or advance the
    ``extraction_status`` keys in ``_BOOKKEEPING_REVERSIONS``, add the
    top-level keys in ``_DERIVED_MANIFEST_KEYS`` and add rows for derived
    text layers.  The registered manifest is rebuilt by undoing only such
    changes and must hash to the recorded digest.  That proves every source
    row (declared attachments, raw.md, the source pages), its size and hash,
    and every identity field to be exactly the registered one; the source
    bytes are then checked against those rows.  Anything else raises.
    """

    current = hashlib.sha256(manifest_bytes).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", registered_sha256 or ""):
        raise AnnouncedNoticeError("registration receipt has no manifest digest")
    receipt = {
        "registered_manifest_sha256": registered_sha256,
        "current_manifest_sha256": current,
    }
    if current == registered_sha256:
        return {"method": "identical_bytes", **receipt}
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError as exc:
        raise AnnouncedNoticeError("corpus manifest is not JSON") from exc
    if not isinstance(manifest, dict):
        raise AnnouncedNoticeError(
            "corpus manifest differs from its registration receipt"
        )
    status = manifest.get("extraction_status")
    files = manifest.get("files")
    files = files if isinstance(files, list) else []
    derived = [
        index
        for index, row in enumerate(files)
        if isinstance(row, dict) and not is_source_file_row(row)
    ]
    if len(derived) > _MAX_DERIVED_ROWS:
        raise AnnouncedNoticeError(
            "corpus manifest has too many derived rows to prove its "
            "registration"
        )
    # One option list per undoable change: keep it (None) or undo it.
    options: list[list[tuple[str, Any, Any] | None]] = []
    if isinstance(status, dict):
        for key, values in _BOOKKEEPING_REVERSIONS.items():
            if key in status:
                options.append(
                    [None]
                    + [
                        ("extraction_status", key, value)
                        for value in values
                        if value != status[key]
                    ]
                )
    for key in _DERIVED_MANIFEST_KEYS:
        if key in manifest:
            options.append([None, ("derived_key", key, None)])
    for index in derived:
        options.append([None, ("derived_row", index, None)])
    for combination in itertools.product(*options):
        undone = [change for change in combination if change is not None]
        candidate = json.loads(json.dumps(manifest))
        for kind, key, value in undone:
            if kind == "extraction_status":
                if value is None:
                    del candidate["extraction_status"][key]
                else:
                    candidate["extraction_status"][key] = value
            elif kind == "derived_key":
                del candidate[key]
        rows = {key for kind, key, _ in undone if kind == "derived_row"}
        if rows:
            candidate["files"] = [
                row for index, row in enumerate(files) if index not in rows
            ]
        rebuilt = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
        if rebuilt != registered_sha256:
            continue
        reverted = {
            key: {"registered": value, "current": status[key]}
            for kind, key, value in undone
            if kind == "extraction_status"
        }
        derived = [
            {"change": kind, "key": key}
            if kind == "derived_key"
            else {
                "change": kind,
                "file_name": files[key].get("file_name"),
                "role": files[key].get("role"),
            }
            for kind, key, _ in undone
            if kind != "extraction_status"
        ]
        if derived:
            method = "canonical_bytes_with_derived_layers_undone"
        elif reverted:
            method = "canonical_bytes_with_bookkeeping_reverted"
        else:
            method = "canonical_bytes"
        return {
            "method": method,
            **receipt,
            "reverted_extraction_status": reverted,
            **({"undone_derived_layers": derived} if derived else {}),
        }
    raise AnnouncedNoticeError(
        "corpus manifest differs from its registration receipt beyond "
        "extraction-status bookkeeping and derived text layers"
    )


def _raw_md_sections(raw_md: str) -> tuple[tuple[str, ...], dict[str, list]]:
    """Return the 公告事項 items and the ODT source-block receipts."""

    items: list[str] = []
    section: str | None = None
    blocks: dict[str, list[tuple[str, str]]] = {}
    for line in raw_md.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section == "公告事項" and line.strip():
            items.append(line.strip())
        if line.startswith("<!-- source-block ") and line.endswith(" -->"):
            payload = json.loads(line[len("<!-- source-block "):-len(" -->")])
            blocks.setdefault(str(payload["attachment_file_name"]), []).append(
                (str(payload["block_id"]), str(payload["raw_text_sha256"]))
            )
    return tuple(items), blocks


def read_notice_bundle(
    bundle_dir: Path, *, registered_manifest_sha256: str | None = None
) -> NoticeBundle:
    """Read one registered corpus bundle and verify its file inventory.

    With ``registered_manifest_sha256`` (the queue's registration receipt)
    the manifest must be proven identical to the registered one, and the
    notice is identified by the registered digest.
    """

    bundle_dir = Path(bundle_dir)
    real_root = bundle_dir.resolve(strict=True)

    def inside(path: Path) -> bool:
        # Migrated bundles keep some payload behind relative in-bundle links.
        resolved = path.resolve(strict=True)
        return resolved.is_file() and (
            resolved == real_root or real_root in resolved.parents
        )

    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file() or not inside(manifest_path):
        raise AnnouncedNoticeError("corpus bundle manifest is missing")
    manifest_bytes = manifest_path.read_bytes()
    identity = (
        None
        if registered_manifest_sha256 is None
        else registered_manifest_identity(
            manifest_bytes, registered_manifest_sha256
        )
    )
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != CORPUS_BUNDLE_SCHEMA:
        raise AnnouncedNoticeError("corpus bundle schema is unsupported")
    official_url = str(manifest.get("canonical_url") or "")
    if not official_url.startswith(OFFICIAL_URL_PREFIX):
        raise AnnouncedNoticeError("notice URL is not an official NHI page")
    attachments: list[NoticeAttachment] = []
    names: set[str] = set()
    for row in manifest.get("files") or ():
        name = str(row.get("file_name") or "")
        if not name or Path(name).name != name or name in names:
            raise AnnouncedNoticeError("corpus bundle file row is invalid")
        names.add(name)
        if not is_source_file_row(row):
            # A derived text layer is never read here, so later corpus lanes
            # may rewrite it without changing the source identity.
            continue
        path = bundle_dir / name
        if not path.is_file() or not inside(path):
            raise AnnouncedNoticeError(f"corpus bundle file is missing: {name}")
        if path.stat().st_size != int(row.get("byte_size", -1)):
            raise AnnouncedNoticeError(f"corpus bundle size mismatch: {name}")
        if _sha256_file(path) != row.get("sha256"):
            raise AnnouncedNoticeError(f"corpus bundle hash mismatch: {name}")
        if row.get("role") == "declared_attachment":
            attachments.append(
                NoticeAttachment(
                    declared_sequence=int(row["declared_sequence"]),
                    file_name=name,
                    media_type=str(row.get("media_type") or ""),
                    sha256=str(row["sha256"]),
                    byte_size=int(row["byte_size"]),
                    path=path,
                )
            )
    if "raw.md" not in names:
        raise AnnouncedNoticeError("corpus bundle raw.md is missing")
    attachments.sort(key=lambda item: item.declared_sequence)
    if [item.declared_sequence for item in attachments] != list(
        range(len(attachments))
    ):
        raise AnnouncedNoticeError("declared attachment sequence has gaps")
    if len(attachments) != int(manifest.get("declared_attachment_count", -1)):
        raise AnnouncedNoticeError("declared attachment inventory is partial")
    raw_md = (bundle_dir / "raw.md").read_text(encoding="utf-8")
    items, raw_blocks = _raw_md_sections(raw_md)
    published_on = str(manifest.get("publish_date") or "")
    try:
        date.fromisoformat(published_on)
    except ValueError as exc:
        raise AnnouncedNoticeError("notice publication date is invalid") from exc
    reference = str(manifest.get("ref_number") or "")
    if not re.fullmatch(r"健保審字第[0-9]+號", reference):
        raise AnnouncedNoticeError("notice reference number is invalid")
    return NoticeBundle(
        bundle_dir=bundle_dir,
        source_uid=str(manifest.get("source_uid") or ""),
        reference_number=reference,
        title=str(manifest.get("title_zh") or ""),
        official_url=official_url,
        published_on=published_on,
        manifest_sha256=(
            hashlib.sha256(manifest_bytes).hexdigest()
            if identity is None
            else identity["registered_manifest_sha256"]
        ),
        attachments=tuple(attachments),
        announcement_items=items,
        raw_md_blocks={
            name: tuple(values) for name, values in raw_blocks.items()
        },
        manifest_identity=identity,
    )


# ---------------------------------------------------------------------------
# ODT traversal


@dataclass(frozen=True)
class OdtParagraph:
    """One project ODT block with its whitespace-faithful text and cell."""

    document_order: int
    block_id: str
    locator: Mapping[str, Any]
    block_text: str
    block_text_sha256: str
    text: str
    top_table_index: int | None
    top_row_index: int | None
    top_cell_index: int | None
    nested: bool
    numbering: ListLabel | None = None

    @property
    def generated_label(self) -> str | None:
        """The automatic list label drawn before the text, if any."""

        if self.numbering is None or self.numbering.status != "labelled":
            return None
        return self.numbering.label

    @property
    def numbering_blocked(self) -> str | None:
        """Why list numbering here cannot be reproduced, if it cannot."""

        if self.numbering is None or self.numbering.status != "unsupported":
            return None
        return self.numbering.reason or "unsupported"

    @property
    def printed_text(self) -> str:
        """The text as printed: generated label, its separator, then text."""

        if self.numbering is None:
            return self.text
        return self.numbering.printed_prefix + self.text

    @property
    def export_prefix(self) -> str:
        """What LibreOffice's text export writes before ``text``."""

        return "" if self.numbering is None else self.numbering.export_prefix


@dataclass(frozen=True)
class OdtCell:
    table_index: int
    row_index: int
    cell_index: int
    covered: bool
    column_span: int
    row_span: int


@dataclass(frozen=True)
class OdtDocument:
    artifact_sha256: str
    paragraphs: tuple[OdtParagraph, ...]
    top_tables: Mapping[int, tuple[tuple[OdtCell, ...], ...]]
    structural_facts: Mapping[str, Any]
    has_tracked_changes: bool
    unsupported_cell_features: frozenset[tuple[int, int, int]]


def _paragraph_text(element: ElementTree.Element, *, render: bool) -> str:
    """Own text of a paragraph, excluding nested paragraphs and tables.

    With ``render=False`` this reproduces the project block contract, which
    keeps character data only.  With ``render=True`` the ODF whitespace
    elements are rendered: ``text:s`` as spaces, ``text:tab`` as a tab and
    ``text:line-break`` as a newline, which is what the document displays.
    """

    parts: list[str] = []

    def visit(parent: ElementTree.Element) -> None:
        if parent.text:
            parts.append(parent.text)
        for child in parent:
            if render and child.tag == _TAG_S:
                parts.append(" " * int(child.attrib.get(_ATTR_SPACE_COUNT, "1")))
            elif render and child.tag == _TAG_TAB:
                parts.append("\t")
            elif render and child.tag == _TAG_LINE_BREAK:
                parts.append("\n")
            elif isinstance(child.tag, str) and child.tag not in {
                _TAG_P,
                _TAG_H,
                _TAG_TABLE,
            }:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return "".join(parts)


def _positive(element: ElementTree.Element, attribute: str) -> int:
    raw = element.attrib.get(attribute, "1")
    try:
        value = int(raw)
    except ValueError as exc:
        raise AnnouncedNoticeError("ODT span attribute is invalid") from exc
    if value < 1:
        raise AnnouncedNoticeError("ODT span attribute is invalid")
    return value


def read_odt_document(
    payload: bytes, *, artifact_sha256: str | None = None
) -> OdtDocument:
    """Traverse an ODT independently and align it to the project blocks.

    ``artifact_sha256`` defaults to the payload hash.  Block identities are
    derived from it, so a reduced test fixture can reproduce the official
    block ids by naming the official artifact it was cut from.
    """

    artifact_sha256 = artifact_sha256 or hashlib.sha256(payload).hexdigest()
    inspected = inspect_odt_document(payload, artifact_sha256)
    blocks = inspected["blocks"]
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            content = archive.read("content.xml")
            styles = archive.read("styles.xml") if "styles.xml" in names else None
            meta = archive.read("meta.xml") if "meta.xml" in names else None
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise AnnouncedNoticeError("ODT container is malformed") from exc
    root = ElementTree.fromstring(content)
    numbering = resolve_list_labels(
        root,
        None if styles is None else ElementTree.fromstring(styles),
        None if meta is None else ElementTree.fromstring(meta),
    )
    body = root.find(f".//{{{_OFFICE}}}text")
    if body is None:
        raise AnnouncedNoticeError("ODT office:text is missing")
    parent: dict[int, ElementTree.Element] = {
        id(child): node for node in body.iter() for child in node
    }
    tables = [node for node in body.iter() if node.tag == _TAG_TABLE]
    table_index = {id(node): index for index, node in enumerate(tables)}

    def ancestors(node: ElementTree.Element) -> Iterable[ElementTree.Element]:
        current = parent.get(id(node))
        while current is not None:
            yield current
            current = parent.get(id(current))

    def owned_rows(table: ElementTree.Element) -> list[ElementTree.Element]:
        rows: list[ElementTree.Element] = []

        def visit(node: ElementTree.Element) -> None:
            for child in node:
                if child.tag == _TAG_TABLE:
                    continue
                if child.tag == _TAG_ROW:
                    rows.append(child)
                    continue
                visit(child)

        visit(table)
        return rows

    top_tables: dict[int, tuple[tuple[OdtCell, ...], ...]] = {}
    cell_position: dict[int, tuple[int, int, int]] = {}
    for table in tables:
        index = table_index[id(table)]
        is_top = not any(node.tag == _TAG_TABLE for node in ancestors(table))
        grid: list[tuple[OdtCell, ...]] = []
        for row_number, row in enumerate(owned_rows(table)):
            if _positive(row, _ATTR_ROWS_REPEATED) > 1:
                raise AnnouncedNoticeError("ODT repeated table rows are unsupported")
            cells = [
                child
                for child in row
                if child.tag in {_TAG_CELL, _TAG_COVERED_CELL}
            ]
            row_cells: list[OdtCell] = []
            for cell_number, cell in enumerate(cells):
                if _positive(cell, _ATTR_COLUMNS_REPEATED) > 1:
                    raise AnnouncedNoticeError(
                        "ODT repeated table cells are unsupported"
                    )
                cell_position[id(cell)] = (index, row_number, cell_number)
                row_cells.append(
                    OdtCell(
                        table_index=index,
                        row_index=row_number,
                        cell_index=cell_number,
                        covered=cell.tag == _TAG_COVERED_CELL,
                        column_span=_positive(cell, _ATTR_COLUMNS_SPANNED),
                        row_span=_positive(cell, _ATTR_ROWS_SPANNED),
                    )
                )
            grid.append(tuple(row_cells))
        if is_top:
            top_tables[index] = tuple(grid)

    unsupported: set[tuple[int, int, int]] = set()
    for node in body.iter():
        if node.tag not in _UNSUPPORTED_CELL_TAGS:
            continue
        for ancestor in ancestors(node):
            if id(ancestor) in cell_position:
                position = cell_position[id(ancestor)]
                if position[0] in top_tables:
                    unsupported.add(position)

    elements = [
        node
        for node in body.iter()
        if node.tag in {_TAG_P, _TAG_H} and _paragraph_text(node, render=False)
    ]
    if len(elements) != len(blocks):
        raise AnnouncedNoticeError(
            "independent ODT traversal disagrees with the block parser"
        )
    paragraphs: list[OdtParagraph] = []
    for element, block in zip(elements, blocks):
        block_text = str(block["raw_text"])
        if _paragraph_text(element, render=False) != block_text:
            raise AnnouncedNoticeError(
                "independent ODT paragraph text disagrees with its block"
            )
        top_position: tuple[int, int, int] | None = None
        nested = False
        for ancestor in ancestors(element):
            if id(ancestor) in cell_position:
                position = cell_position[id(ancestor)]
                if position[0] in top_tables:
                    top_position = position
                else:
                    nested = True
        locator = block["locator"]
        if top_position is not None and not nested:
            if (
                locator.get("table_index"),
                locator.get("row_index"),
                locator.get("cell_index"),
            ) != top_position:
                raise AnnouncedNoticeError(
                    "independent ODT cell attribution disagrees with its block"
                )
        paragraphs.append(
            OdtParagraph(
                document_order=int(locator["document_order"]),
                block_id=str(block["block_id"]),
                locator=dict(locator),
                block_text=block_text,
                block_text_sha256=str(block["raw_text_sha256"]),
                text=_paragraph_text(element, render=True),
                top_table_index=top_position[0] if top_position else None,
                top_row_index=top_position[1] if top_position else None,
                top_cell_index=top_position[2] if top_position else None,
                nested=nested,
                numbering=numbering.get(id(element)),
            )
        )
    return OdtDocument(
        artifact_sha256=artifact_sha256,
        paragraphs=tuple(paragraphs),
        top_tables=top_tables,
        structural_facts=dict(inspected["structural_facts"]),
        has_tracked_changes=any(
            node.tag == _TAG_TRACKED_CHANGES for node in body.iter()
        ),
        unsupported_cell_features=frozenset(unsupported),
    )


# ---------------------------------------------------------------------------
# Comparison-table grammar


@dataclass(frozen=True)
class ComparisonTable:
    table_index: int
    kind: str
    revised_header: str
    original_header: str
    revised_column: int
    original_column: int
    effective_on: date
    effective_statement: OdtParagraph
    title_paragraph: OdtParagraph | None


@dataclass(frozen=True)
class AnnouncedClause:
    clause_code: str
    effective_on: str
    table_index: int
    rows: tuple[tuple[int, int], ...]
    revised: tuple[OdtParagraph, ...]
    original: tuple[OdtParagraph, ...]
    original_is_none: bool
    continued: bool

    @property
    def patch_text(self) -> str:
        return PATCH_TEXT_JOIN.join(item.printed_text for item in self.revised)

    @property
    def original_text(self) -> str:
        return PATCH_TEXT_JOIN.join(item.printed_text for item in self.original)

    @property
    def omission_orders(self) -> tuple[int, ...]:
        return tuple(
            item.document_order
            for item in self.revised
            if is_omission_marker(item.printed_text)
        )

    @property
    def generated_label_orders(self) -> tuple[int, ...]:
        return tuple(
            item.document_order for item in self.revised if item.generated_label
        )

    @property
    def numbering_blocked_reasons(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.numbering_blocked
                    for item in self.revised
                    if item.numbering_blocked
                }
            )
        )

    @property
    def requires_rendering_check(self) -> bool:
        """Whether list numbering must be confirmed by an office rendering."""

        return any(item.numbering is not None for item in self.revised)

    @property
    def blocked_reason(self) -> str | None:
        """Why this clause's printed text cannot be reproduced exactly.

        A table nested in the revised column would lose its rows and columns
        as paragraph text, and the office rendering check cannot see it, so
        such a clause is always held back.
        """

        if any(item.nested for item in self.revised):
            return "nested_table"
        if self.numbering_blocked_reasons:
            return "unsupported_list_numbering"
        return None

    def component_manifest(self) -> list[dict[str, Any]]:
        """One entry per revised block, with its exact span in patch_text.

        Spans are half-open ``[start, end)`` in Unicode scalars and in UTF-8
        bytes; the gaps between spans are exactly ``PATCH_TEXT_JOIN``.
        """

        manifest: list[dict[str, Any]] = []
        scalar = 0
        byte = 0
        separator_bytes = len(PATCH_TEXT_JOIN.encode("utf-8"))
        for index, item in enumerate(self.revised):
            if index:
                scalar += len(PATCH_TEXT_JOIN)
                byte += separator_bytes
            text = item.printed_text
            length = len(text)
            byte_length = len(text.encode("utf-8"))
            manifest.append(
                {
                    "source_block_id": item.block_id,
                    "source_locator": dict(item.locator),
                    "raw_text_sha256": item.block_text_sha256,
                    "rendered_text_sha256": sha256_text(text),
                    "patch_text_scalar_span": [scalar, scalar + length],
                    "patch_text_utf8_span": [byte, byte + byte_length],
                    "top_level_cell": [
                        item.top_table_index,
                        item.top_row_index,
                        item.top_cell_index,
                    ],
                    "nested_table_paragraph": item.nested,
                    "omission_marker": is_omission_marker(text),
                    "generated_label": _label_manifest(item),
                }
            )
            scalar += length
            byte += byte_length
        return manifest


def _label_manifest(item: OdtParagraph) -> dict[str, Any] | None:
    """The generated prefix of one block, or ``None`` when it has none."""

    if item.generated_label is None or item.numbering is None:
        return None
    prefix = item.numbering.printed_prefix
    return {
        "label": item.generated_label,
        "separator": item.numbering.separator,
        "prefix_scalar_length": len(prefix),
        "prefix_utf8_length": len(prefix.encode("utf-8")),
        "rule_version": LIST_LABEL_RULE_VERSION,
        "evidence": dict(item.numbering.evidence),
    }


@dataclass(frozen=True)
class NonClauseEffect:
    key: str
    effect_type: str
    designation: str | None
    scope_note: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class ParsedNotice:
    bundle: NoticeBundle
    attachment: NoticeAttachment
    document: OdtDocument
    tables: tuple[ComparisonTable, ...]
    clauses: tuple[AnnouncedClause, ...]
    other_effects: tuple[NonClauseEffect, ...]
    effective_on: str
    ignored_tables: tuple[Mapping[str, Any], ...] = field(default=())

    @property
    def notice_id(self) -> str:
        return stable_uuid(
            "notice",
            [self.bundle.reference_number, self.attachment.sha256],
        )


def _cell_text(items: Sequence[OdtParagraph]) -> str:
    return "".join(item.printed_text for item in items)


def _header_key(items: Sequence[OdtParagraph]) -> str:
    return re.sub(r"\s+", "", grammar_text(_cell_text(items)))


def _cell_items(
    paragraphs: Sequence[OdtParagraph],
    table_index: int,
    row_index: int,
    cell_index: int,
) -> tuple[OdtParagraph, ...]:
    return tuple(
        item
        for item in paragraphs
        if item.top_table_index == table_index
        and item.top_row_index == row_index
        and item.top_cell_index == cell_index
    )


def _segments(
    items: Sequence[OdtParagraph],
) -> list[tuple[str | None, list[OdtParagraph]]]:
    """Split one cell at clause-heading paragraphs, keeping document order."""

    segments: list[tuple[str | None, list[OdtParagraph]]] = []
    for item in items:
        if item.numbering is not None and not item.nested and (
            clause_heading_code(item.text)
            or clause_heading_code(item.printed_text)
        ):
            raise AnnouncedNoticeError(
                "a list-numbered paragraph reads as a clause designation"
            )
        code = None if item.nested else clause_heading_code(item.text)
        if code is not None:
            segments.append((code, [item]))
        elif segments:
            segments[-1][1].append(item)
        else:
            segments.append((None, [item]))
    return segments


def _comparison_tables(document: OdtDocument) -> tuple[
    list[tuple[int, dict[str, Any]]], list[Mapping[str, Any]]
]:
    found: list[tuple[int, dict[str, Any]]] = []
    ignored: list[Mapping[str, Any]] = []
    for table_index, grid in sorted(document.top_tables.items()):
        if not grid:
            continue
        header_cells = grid[0]
        headers = [
            _header_key(
                _cell_items(
                    document.paragraphs, table_index, 0, cell.cell_index
                )
            )
            for cell in header_cells
        ]
        revised = [
            index
            for index, text in enumerate(headers)
            if _REVISED_HEADER_RE.fullmatch(text)
        ]
        original = [
            index
            for index, text in enumerate(headers)
            if _ORIGINAL_HEADER_RE.fullmatch(text)
        ]
        if not revised and not original:
            ignored.append({"table_index": table_index, "headers": headers})
            continue
        if len(header_cells) != 2 or len(revised) != 1 or len(original) != 1:
            raise AnnouncedNoticeError(
                f"comparison table {table_index} header grammar is unsupported"
            )
        revised_kind = _REVISED_HEADER_RE.fullmatch(headers[revised[0]])["kind"]
        original_kind = _ORIGINAL_HEADER_RE.fullmatch(headers[original[0]])[
            "kind"
        ]
        if revised_kind != original_kind:
            raise AnnouncedNoticeError(
                f"comparison table {table_index} column kinds differ"
            )
        for row in grid[1:]:
            if len(row) != 2 or any(
                cell.covered or cell.column_span != 1 or cell.row_span != 1
                for cell in row
            ):
                raise AnnouncedNoticeError(
                    f"comparison table {table_index} has merged or extra cells"
                )
        found.append(
            (
                table_index,
                {
                    "kind": "appendix" if revised_kind == "附表規定" else "clause",
                    "revised_header": headers[revised[0]],
                    "original_header": headers[original[0]],
                    "revised_column": revised[0],
                    "original_column": original[0],
                },
            )
        )
    return found, ignored


def _first_order(document: OdtDocument, table_index: int) -> int:
    orders = [
        item.document_order
        for item in document.paragraphs
        if item.top_table_index == table_index
    ]
    if not orders:
        raise AnnouncedNoticeError(f"comparison table {table_index} is empty")
    return min(orders)


def parse_notice(bundle: NoticeBundle) -> ParsedNotice:
    """Parse the official comparison table of one notice bundle."""

    candidates: list[tuple[NoticeAttachment, OdtDocument]] = []
    for attachment in bundle.attachments:
        if attachment.media_type != ODT_MEDIA_TYPE:
            continue
        payload = attachment.path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != attachment.sha256:
            raise AnnouncedNoticeError("ODT bytes changed after verification")
        document = read_odt_document(payload, artifact_sha256=attachment.sha256)
        if _comparison_tables(document)[0]:
            candidates.append((attachment, document))
    if not candidates:
        raise AnnouncedNoticeError("notice has no official comparison table")
    if len(candidates) != 1:
        raise AnnouncedNoticeError(
            "comparison tables span several ODT attachments"
        )
    attachment, document = candidates[0]
    return parse_comparison_document(bundle, attachment, document)


def _continuation_refusal(
    previous: Mapping[str, Any] | None,
    row: tuple[int, int],
    revised_segments: Sequence[tuple[str | None, Sequence[OdtParagraph]]],
    original_segments: Sequence[tuple[str | None, Sequence[OdtParagraph]]],
    *,
    original_none: bool,
) -> str | None:
    """Why a row without a designation may not continue the previous clause.

    Attributing a row to the clause above is an inference, so it needs
    positive evidence: that clause ends on the row just above in the same
    table, each column holds exactly one undesignated segment, the original
    column does not mark a new clause (``無``), and no paragraph in either
    column reads like a clause or general-rule designation.
    """

    if previous is None:
        return "no clause precedes it"
    table_index, row_index = row
    if tuple(previous["rows"][-1]) != (table_index, row_index - 1):
        return "the clause above does not end on the previous row"
    if (
        len(revised_segments) != 1
        or len(original_segments) != 1
        or original_segments[0][0] is not None
    ):
        return "a column is empty or holds a designation"
    if original_none:
        return "the original column marks a new clause"
    for _, items in (*revised_segments, *original_segments):
        for item in items:
            if any(
                _LOOSE_DESIGNATION_RE.match(grammar_text(text))
                for text in (item.text, item.printed_text)
            ):
                return "a paragraph reads like a designation"
    return None


def parse_comparison_document(
    bundle: NoticeBundle,
    attachment: NoticeAttachment,
    document: OdtDocument,
) -> ParsedNotice:
    """Parse the comparison tables of one verified ODT attachment."""

    if document.artifact_sha256 != attachment.sha256:
        raise AnnouncedNoticeError("ODT identity differs from its attachment")
    raw_receipts = bundle.raw_md_blocks.get(attachment.file_name)
    own_receipts = tuple(
        (item.block_id, item.block_text_sha256) for item in document.paragraphs
    )
    if raw_receipts is None:
        raise AnnouncedNoticeError(
            "raw.md has no source-block receipts for the comparison attachment"
        )
    if tuple(raw_receipts) != own_receipts:
        raise AnnouncedNoticeError(
            "ODT blocks differ from the registered raw.md source blocks"
        )
    table_specs, ignored = _comparison_tables(document)
    if not table_specs:
        raise AnnouncedNoticeError("attachment has no official comparison table")
    title = grammar_text(bundle.title).strip()
    if "預告" in title:
        raise AnnouncedNoticeError(
            "the notice is a pre-announcement (預告), not announced text"
        )
    for table_index, spec in table_specs:
        if spec["revised_header"].startswith("建議") and not title.startswith(
            "公告"
        ):
            raise AnnouncedNoticeError(
                f"comparison table {table_index} is a proposal (建議修訂後) "
                "outside an announcement"
            )
    if document.has_tracked_changes:
        raise AnnouncedNoticeError("ODT contains tracked changes")

    flow = [item for item in document.paragraphs if item.top_table_index is None]
    statements = [
        (item, parse_effective_statement(item.text))
        for item in flow
        if _EFFECTIVE_WORD_RE.search(item.text)
    ]
    if any(item.numbering is not None for item, _ in statements):
        raise AnnouncedNoticeError(
            "an effective-date statement carries list numbering"
        )
    unparsed = [item for item, value in statements if value is None]
    if unparsed:
        raise AnnouncedNoticeError(
            "attachment states an effective date outside the known grammar"
        )
    dated = [(item, value) for item, value in statements if value is not None]
    if not dated:
        raise AnnouncedNoticeError("attachment states no effective date")

    tables: list[ComparisonTable] = []
    for table_index, spec in table_specs:
        start = _first_order(document, table_index)
        preceding = [
            (item, value) for item, value in dated if item.document_order < start
        ]
        if preceding:
            statement, effective_on = preceding[-1]
        elif len({value for _, value in dated}) == 1:
            statement, effective_on = dated[0]
        else:
            raise AnnouncedNoticeError(
                f"comparison table {table_index} has no governing effective date"
            )
        titles = [item for item in flow if item.document_order < start]
        tables.append(
            ComparisonTable(
                table_index=table_index,
                kind=spec["kind"],
                revised_header=spec["revised_header"],
                original_header=spec["original_header"],
                revised_column=spec["revised_column"],
                original_column=spec["original_column"],
                effective_on=effective_on,
                effective_statement=statement,
                title_paragraph=titles[-1] if titles else None,
            )
        )
    effective_dates = {table.effective_on for table in tables}
    if len(effective_dates) != 1:
        raise AnnouncedNoticeError(
            "one notice states several effective dates"
        )

    clauses: list[dict[str, Any]] = []
    appendices: dict[str, dict[str, Any]] = {}
    for table in tables:
        grid = document.top_tables[table.table_index]
        for row_index in range(1, len(grid)):
            cells_with_features = {
                (table.table_index, row_index, table.revised_column),
                (table.table_index, row_index, table.original_column),
            }
            if cells_with_features & document.unsupported_cell_features:
                raise AnnouncedNoticeError(
                    "comparison cell contains notes or annotations"
                )
            revised_items = _cell_items(
                document.paragraphs,
                table.table_index,
                row_index,
                table.revised_column,
            )
            original_items = _cell_items(
                document.paragraphs,
                table.table_index,
                row_index,
                table.original_column,
            )
            if not revised_items:
                raise AnnouncedNoticeError(
                    f"comparison row {table.table_index}/{row_index} "
                    "has an empty revised column"
                )
            if table.kind == "appendix":
                designation = None
                if table.title_paragraph is not None:
                    match = _APPENDIX_DESIGNATION_RE.search(
                        grammar_text(table.title_paragraph.text)
                    )
                    designation = match.group(0) if match else None
                if designation is None:
                    raise AnnouncedNoticeError(
                        "appendix comparison table has no designation"
                    )
                title_text = table.title_paragraph.text.strip()
                appendix = appendices.setdefault(
                    designation,
                    {"title": title_text, "rows": [], "block_ids": [], "texts": []},
                )
                appendix["rows"].append([table.table_index, row_index])
                appendix["block_ids"].extend(item.block_id for item in revised_items)
                appendix["texts"].extend(item.text for item in revised_items)
                continue
            revised_segments = _segments(revised_items)
            original_segments = _segments(original_items)
            original_none = (
                len(original_items) == 1
                and _NONE_CELL_RE.fullmatch(
                    grammar_text(original_items[0].printed_text).strip()
                )
                is not None
            )
            revised_codes = [code for code, _ in revised_segments]
            original_codes = [code for code, _ in original_segments]
            if revised_codes[0] is None:
                refusal = _continuation_refusal(
                    clauses[-1] if clauses else None,
                    (table.table_index, row_index),
                    revised_segments,
                    original_segments,
                    original_none=original_none,
                )
                if refusal is not None:
                    raise AnnouncedNoticeError(
                        f"comparison row {table.table_index}/{row_index} "
                        f"does not start with a clause designation ({refusal})"
                    )
                previous = clauses[-1]
                previous["revised"].extend(revised_segments[0][1])
                previous["original"].extend(original_segments[0][1])
                previous["rows"].append((table.table_index, row_index))
                previous["continued"] = True
                continue
            if not original_none and original_codes != revised_codes:
                raise AnnouncedNoticeError(
                    f"comparison row {table.table_index}/{row_index} "
                    "designations differ between columns"
                )
            original_by_code = (
                {}
                if original_none
                else {code: items for code, items in original_segments}
            )
            for code, items in revised_segments:
                clauses.append(
                    {
                        "clause_code": code,
                        "table": table,
                        "rows": [(table.table_index, row_index)],
                        "revised": list(items),
                        "original": list(original_by_code.get(code, ())),
                        "original_is_none": original_none,
                        "continued": False,
                    }
                )
    codes = [row["clause_code"] for row in clauses]
    if len(codes) != len(set(codes)):
        raise AnnouncedNoticeError("a clause designation repeats in one notice")
    effective_on = next(iter(effective_dates)).isoformat()
    announced = tuple(
        AnnouncedClause(
            clause_code=row["clause_code"],
            effective_on=effective_on,
            table_index=row["table"].table_index,
            rows=tuple(row["rows"]),
            revised=tuple(row["revised"]),
            original=tuple(row["original"]),
            original_is_none=row["original_is_none"],
            continued=row["continued"],
        )
        for row in clauses
    )
    other = [
        NonClauseEffect(
            key=f"appendix:{designation}",
            effect_type="clause_amendment",
            designation=designation,
            scope_note=(
                f"{appendix['title']}: appendix-table amendment has no dotted "
                "clause designation; its revised column stays in the sealed "
                "notice source and is not projected as clause text"
            ),
            evidence={
                "rows": appendix["rows"],
                "revised_block_ids": appendix["block_ids"],
                "revised_text_sha256": sha256_text(
                    PATCH_TEXT_JOIN.join(appendix["texts"])
                ),
            },
        )
        for designation, appendix in appendices.items()
    ]
    other.extend(_announcement_effects(bundle, attachment))
    return ParsedNotice(
        bundle=bundle,
        attachment=attachment,
        document=document,
        tables=tuple(tables),
        clauses=announced,
        other_effects=tuple(other),
        effective_on=effective_on,
        ignored_tables=tuple(ignored),
    )


def _announcement_effects(
    bundle: NoticeBundle, comparison: NoticeAttachment
) -> list[NonClauseEffect]:
    """Name notice effects that the clause-text overlay does not project."""

    effects: list[NonClauseEffect] = []
    listing = [
        item
        for item in bundle.announcement_items
        if "藥品已收載項目異動明細表" in item or "暫予支付" in item
    ]
    if listing or "暫予支付" in bundle.title or "支付價格" in bundle.title:
        effects.append(
            NonClauseEffect(
                key="reimbursed_item_change",
                effect_type="reimbursed_item_change",
                designation=None,
                scope_note=(
                    "listed-item and payment-price changes remain outside "
                    "the clause-text overlay"
                ),
                evidence={"announcement_items": listing},
            )
        )
    return effects


# ---------------------------------------------------------------------------
# Deterministic comparisons for the verification report


def old_column_comparison(
    clause: AnnouncedClause, served_text: str | None
) -> dict[str, Any]:
    """Compare the official old column with the currently served text."""

    if served_text is None:
        return {
            "served_clause_present": False,
            "old_equals_served_exact": False,
            "old_equals_served_semantic": False,
            "old_paragraphs_found_in_served": None,
        }
    old_text = "\n".join(item.printed_text for item in clause.original)
    served_semantic = semantic_comparison_text(served_text)
    substantive = [
        item
        for item in clause.original
        if not is_omission_marker(item.printed_text)
    ]
    found = sum(
        1
        for item in substantive
        if semantic_comparison_text(item.printed_text) in served_semantic
    )
    return {
        "served_clause_present": True,
        "old_equals_served_exact": old_text == served_text,
        "old_equals_served_semantic": (
            semantic_comparison_text(old_text) == served_semantic
        ),
        "old_paragraphs_found_in_served": [found, len(substantive)],
    }


def revised_paragraphs_in_served(
    clause: AnnouncedClause, served_text: str | None
) -> list[int] | None:
    if served_text is None:
        return None
    served_semantic = semantic_comparison_text(served_text)
    substantive = [
        item
        for item in clause.revised
        if not is_omission_marker(item.printed_text)
    ]
    return [
        sum(
            1
            for item in substantive
            if semantic_comparison_text(item.printed_text) in served_semantic
        ),
        len(substantive),
    ]


def cell_rendering_check(
    notice: ParsedNotice,
    clause: AnnouncedClause,
    rendering: Mapping[str, Any] | None,
) -> str:
    """Check a clause against LibreOffice's rendering of its own revised cells.

    ``rendering`` is the notice attachment's entry from
    :func:`nhi_rule_history.office_rendering.render_table_cells`.  Each
    revised cell the clause spans is aligned paragraph by paragraph with the
    cell LibreOffice renders at the same top-level table, row and column;
    blank unlabelled paragraphs and nested tables are left out on both sides.
    Every paragraph of the cell must have the same text, and each of the
    clause's own paragraphs must carry the same list label followed by the
    same separator (tab, space, nothing or line break).  A label that only
    matches another cell, or a gap LibreOffice draws differently, is a
    mismatch.
    """

    if rendering is None:
        return "unavailable"
    if any(item.nested for item in clause.revised):
        return "not_applicable_nested_table"
    tables = rendering.get("tables")
    top = sorted(notice.document.top_tables)
    if not isinstance(tables, list) or len(tables) != len(top):
        return "mismatch"
    column = next(
        table.revised_column
        for table in notice.tables
        if table.table_index == clause.table_index
    )
    own = {item.document_order for item in clause.revised}
    for table_index, row_index in clause.rows:
        try:
            cell = tables[top.index(table_index)][row_index][column]
        except (IndexError, TypeError, ValueError):
            return "mismatch"
        if not isinstance(cell, list):
            return "mismatch"
        ours = [
            item
            for item in _cell_items(
                notice.document.paragraphs, table_index, row_index, column
            )
            if not item.nested and (item.text.strip() or item.generated_label)
        ]
        drawn = [
            paragraph
            for paragraph in cell
            if isinstance(paragraph, Mapping)
            and not paragraph.get("nested_table")
            and (str(paragraph.get("text") or "").strip() or paragraph.get("label"))
        ]
        if len(ours) != len(drawn):
            return "mismatch"
        for item, paragraph in zip(ours, drawn):
            if item.text != paragraph.get("text"):
                return "mismatch"
            if item.document_order not in own:
                continue
            label = item.generated_label or ""
            if label != (paragraph.get("label") or ""):
                return "mismatch"
            if label and item.numbering.separator != paragraph.get("separator"):
                return "mismatch"
    return "exact"


def projection_block_reason(
    clause: AnnouncedClause, rendering_check: str | None
) -> str | None:
    """Why a clause must stay a pending effect instead of a patch, if so.

    A clause whose paragraphs carry list numbering is projected only when
    LibreOffice's rendering of its own revised cells shows exactly the
    reconstructed labels and separators (:func:`cell_rendering_check`).
    """

    if clause.blocked_reason:
        return clause.blocked_reason
    if rendering_check == "mismatch":
        return "official_rendering_mismatch"
    if clause.requires_rendering_check and rendering_check != "exact":
        return "official_rendering_unverified"
    return None


def verification_row(
    notice: ParsedNotice,
    clause: AnnouncedClause,
    *,
    served_text: str | None,
    rendering: Mapping[str, Any] | None,
) -> dict[str, Any]:
    whitespace_blocks = sum(
        1 for item in clause.revised if item.text != item.block_text
    )
    rendering_check = cell_rendering_check(notice, clause, rendering)
    return {
        "reference_number": notice.bundle.reference_number,
        "clause_code": clause.clause_code,
        "effective_on": clause.effective_on,
        "effective_statement": notice.tables[0].effective_statement.text
        if notice.tables
        else None,
        "revised_block_count": len(clause.revised),
        "revised_text_sha256": sha256_text(clause.patch_text),
        "revised_equals_official_rendering": rendering_check,
        "revised_blocks_with_whitespace_elements": whitespace_blocks,
        "revised_blocks_with_generated_labels": len(
            clause.generated_label_orders
        ),
        "list_numbering_unsupported": list(clause.numbering_blocked_reasons),
        "blocked_reason": clause.blocked_reason,
        "projection_block_reason": projection_block_reason(
            clause, rendering_check
        ),
        "omitted_text_present": bool(clause.omission_orders),
        "continued_across_rows": clause.continued,
        "original_is_none": clause.original_is_none,
        **old_column_comparison(clause, served_text),
        "revised_paragraphs_found_in_served": revised_paragraphs_in_served(
            clause, served_text
        ),
    }
