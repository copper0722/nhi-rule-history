from __future__ import annotations

import json
import struct
import tempfile
import unittest
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from nhi_rule_history.contracts import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    relative_blob_path,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.parsers import ole as ole_parser
from nhi_rule_history.parsers.ole import (
    CFB_MAGIC,
    ENDOFCHAIN,
    EXCEL_TYPED_EXTRACTED,
    FATSECT,
    FREESECT,
    NOSTREAM,
    WORD_TYPED_EXTRACTED,
    _CFBContainer,
    _CFBError,
    _parse_docx,
    parse_verified_ole_run,
)


def _directory_entry(
    name: str,
    object_type: int,
    *,
    left_id: int = NOSTREAM,
    right_id: int = NOSTREAM,
    child_id: int = NOSTREAM,
    start_sector: int = ENDOFCHAIN,
    stream_size: int = 0,
) -> bytes:
    encoded = name.encode("utf-16le") + b"\x00\x00"
    if len(encoded) > 64:
        raise ValueError("fixture directory name is too long")
    row = bytearray(128)
    row[: len(encoded)] = encoded
    struct.pack_into("<H", row, 64, len(encoded))
    row[66] = object_type
    row[67] = 1
    struct.pack_into("<III", row, 68, left_id, right_id, child_id)
    struct.pack_into("<I", row, 116, start_sector)
    struct.pack_into("<Q", row, 120, stream_size)
    return bytes(row)


def _header(
    *,
    fat_sector_id: int,
    first_directory_sector: int = 0,
    first_minifat_sector: int = ENDOFCHAIN,
    number_of_minifat_sectors: int = 0,
) -> bytes:
    header = bytearray(512)
    header[:8] = CFB_MAGIC
    struct.pack_into("<HHHH", header, 24, 0x003E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, first_directory_sector)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, first_minifat_sector)
    struct.pack_into("<I", header, 64, number_of_minifat_sectors)
    struct.pack_into("<I", header, 68, ENDOFCHAIN)
    struct.pack_into("<I", header, 72, 0)
    difat = [fat_sector_id, *([FREESECT] * 108)]
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header)


def _fat_sector(values: dict[int, int]) -> bytes:
    fat = [FREESECT] * 128
    for index, value in values.items():
        fat[index] = value
    return struct.pack("<128I", *fat)


def _standard_stream_cfb(stream_name: str) -> bytes:
    stream = (f"{stream_name}-fixture".encode("ascii") * 400)[:4096]
    stream = stream.ljust(4096, b"\x00")
    directory = (
        _directory_entry("Root Entry", 5, child_id=1)
        + _directory_entry(
            stream_name,
            2,
            start_sector=1,
            stream_size=len(stream),
        )
    ).ljust(512, b"\x00")
    sectors = [directory]
    sectors.extend(stream[index : index + 512] for index in range(0, 4096, 512))
    fat_sector_id = len(sectors)
    fat_values = {0: ENDOFCHAIN, fat_sector_id: FATSECT}
    for sector_id in range(1, 9):
        fat_values[sector_id] = (
            sector_id + 1 if sector_id < 8 else ENDOFCHAIN
        )
    sectors.append(_fat_sector(fat_values))
    return _header(fat_sector_id=fat_sector_id) + b"".join(sectors)


def _mini_stream_cfb(stream_name: str) -> bytes:
    stream = b"mini-workbook"
    directory = (
        _directory_entry(
            "Root Entry",
            5,
            child_id=1,
            start_sector=1,
            stream_size=64,
        )
        + _directory_entry(
            stream_name,
            2,
            start_sector=0,
            stream_size=len(stream),
        )
    ).ljust(512, b"\x00")
    mini_stream = stream.ljust(512, b"\x00")
    minifat = struct.pack("<I", ENDOFCHAIN).ljust(512, b"\xff")
    fat = _fat_sector(
        {
            0: ENDOFCHAIN,
            1: ENDOFCHAIN,
            2: ENDOFCHAIN,
            3: FATSECT,
        }
    )
    return (
        _header(
            fat_sector_id=3,
            first_minifat_sector=2,
            number_of_minifat_sectors=1,
        )
        + directory
        + mini_stream
        + minifat
        + fat
    )


def _docx_fixture() -> bytes:
    document = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def _raw_run(
    root: Path,
    specifications: list[tuple[str, bytes]],
) -> tuple[Path, str]:
    run_dir = root / "raw-run"
    run_dir.mkdir()
    resources: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    for ordinal, (label, payload) in enumerate(specifications, 1):
        resource_id = sha256_bytes(f"resource:{ordinal}:{label}".encode())
        digest = sha256_bytes(payload)
        relative = relative_blob_path(digest)
        blob = run_dir / relative
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(payload)
        resource = {
            "schema": "nhi-rule-history/discovered-resource/v2",
            "resource_id": resource_id,
            "adapter_id": "fixture",
            "resource_kind": "official_attachment",
            "source_url": f"https://example.invalid/{ordinal}",
            "discovery_locator": {"attachment_ordinal": ordinal},
            "source_label": label,
            "fetch_state": "pending",
        }
        resources.append(resource)
        artifacts.append(
            {
                "schema": "nhi-rule-history/raw-artifact/v2",
                "artifact_sha256": digest,
                "byte_size": len(payload),
                "content_path": relative,
                "media_type": "application/x-ole-storage",
                "first_observed_at": "2026-07-27T00:00:00+00:00",
            }
        )
        links.append(
            {
                "schema": "nhi-rule-history/resource-artifact-link/v2",
                "link_id": stable_id("link", resource_id, digest),
                "resource_id": resource_id,
                "artifact_sha256": digest,
                "relation": "retrieved_representation",
                "observed_at": "2026-07-27T00:00:01+00:00",
            }
        )
        attempts.append(
            {
                "schema": "nhi-rule-history/fetch-attempt/v2",
                "attempt_id": stable_id("attempt", resource_id, digest),
                "resource_id": resource_id,
                "source_url": resource["source_url"],
                "started_at": "2026-07-27T00:00:00+00:00",
                "completed_at": "2026-07-27T00:00:01+00:00",
                "status": "success",
                "acquisition_mode": "network",
                "http_status": 200,
                "final_url": resource["source_url"],
                "response_headers": {
                    "content-type": "application/x-ole-storage"
                },
                "artifact_sha256": digest,
                "byte_size": len(payload),
            }
        )
        observations.append(
            {
                "schema": "nhi-rule-history/artifact-url-observation/v2",
                "url_observation_id": stable_id(
                    "observation", resource_id, digest
                ),
                "resource_id": resource_id,
                "source_url": resource["source_url"],
                "artifact_sha256": digest,
                "relation_to_previous": "first_observation",
                "observed_at": "2026-07-27T00:00:01+00:00",
            }
        )
    rows_by_name = {
        "discovery-observations.jsonl": [],
        "discovered-resources.jsonl": resources,
        "fetch-attempts.jsonl": attempts,
        "raw-artifacts.jsonl": artifacts,
        "resource-artifact-links.jsonl": links,
        "artifact-url-observations.jsonl": observations,
        "issues.jsonl": [],
    }
    for filename, rows in rows_by_name.items():
        _write_jsonl(run_dir / filename, rows)
    (run_dir / "discovery-manifest.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "nhi-rule-history/discovery-manifest/v2",
                "status": "success",
            }
        )
    )
    names = [*rows_by_name, "discovery-manifest.json"]
    manifest = {
        "schema": "nhi-rule-history/raw-manifest/v2",
        "status": "success",
        "source_plan_sha256": "1" * 64,
        "counts": {
            "resources": len(resources),
            "artifacts": len(artifacts),
            "resource_artifact_links": len(links),
            "artifact_bytes": sum(int(row["byte_size"]) for row in artifacts),
        },
        "files": [
            {
                "filename": filename,
                "bytes": (run_dir / filename).stat().st_size,
                "sha256": file_sha256(run_dir / filename),
            }
            for filename in names
        ],
    }
    manifest_path = run_dir / "raw-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    return run_dir, file_sha256(manifest_path)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class CFBContractTests(unittest.TestCase):
    def test_standard_word_stream_inventory_and_type(self) -> None:
        container = _CFBContainer(_standard_stream_cfb("WordDocument"))
        self.assertEqual(container.primary_office_type(), "word_doc")
        self.assertEqual(len(container.stream_entries), 1)
        bound = container.stream_entries[0]
        self.assertEqual(bound.path, ("WordDocument",))
        self.assertEqual(len(container.read_stream(bound)), 4096)

    def test_ministream_workbook_inventory_and_type(self) -> None:
        container = _CFBContainer(_mini_stream_cfb("Workbook"))
        self.assertEqual(container.primary_office_type(), "excel_xls")
        self.assertEqual(
            container.read_stream(container.stream_entries[0]),
            b"mini-workbook",
        )

    def test_magic_and_directory_cycle_fail_closed(self) -> None:
        with self.assertRaisesRegex(_CFBError, "ole_magic_mismatch"):
            _CFBContainer(b"not an ole file")
        payload = bytearray(_standard_stream_cfb("WordDocument"))
        directory_offset = 512
        struct.pack_into("<I", payload, directory_offset + 128 + 68, 1)
        with self.assertRaisesRegex(
            _CFBError,
            "directory_tree_cycle_or_duplicate",
        ):
            _CFBContainer(bytes(payload))


class WordNormalizationTests(unittest.TestCase):
    def test_docx_paragraph_table_and_cell_locators(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.docx"
            path.write_bytes(_docx_fixture())
            result = _parse_docx(
                path,
                artifact_sha256="a" * 64,
                parse_run_id=str(uuid.uuid4()),
            )
        self.assertEqual(len(result["paragraphs"]), 3)
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(len(result["cells"]), 2)
        self.assertEqual(result["paragraphs"][0]["text"], "First paragraph")
        self.assertEqual(result["tables"][0]["text"], "Cell A\tCell B")
        self.assertEqual(
            result["cells"][1]["locator"],
            {
                "artifact_sha256": "a" * 64,
                "table_ordinal": 1,
                "row_index": 1,
                "cell_index": 2,
            },
        )

    def test_malformed_docx_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.docx"
            path.write_bytes(b"not a zip")
            with self.assertRaisesRegex(
                ole_parser._TypedExtractionError,
                "needs_malformed_docx_review",
            ):
                _parse_docx(
                    path,
                    artifact_sha256="a" * 64,
                    parse_run_id=str(uuid.uuid4()),
                )

    def test_pdfinfo_page_count_is_strict(self) -> None:
        self.assertEqual(
            ole_parser._parse_pdfinfo_page_count(b"Title: fixture\nPages: 3\n"),
            3,
        )
        with self.assertRaisesRegex(
            ole_parser._TypedExtractionError,
            "needs_word_visual_page_count_review",
        ):
            ole_parser._parse_pdfinfo_page_count(b"Title: fixture\n")


class OLEParserIntegrationTests(unittest.TestCase):
    def test_word_and_excel_denominator_is_exhaustive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [
                    ("source.DOC", _standard_stream_cfb("WordDocument")),
                    ("sheet.XLS", _mini_stream_cfb("Workbook")),
                ],
            )
            paragraph = {
                "schema": ole_parser.OLE_WORD_PARAGRAPH_SCHEMA,
                "parse_run_id": "replaced",
                "artifact_sha256": "replaced",
                "paragraph_ordinal": 1,
                "container_path": [],
                "block_index": 1,
                "text": "Word text",
                "explicit_page_breaks": 0,
                "locator": {},
                "statement": ole_parser.NON_CLAIM,
            }

            def fake_word(
                blob_path: Path,
                *,
                artifact_sha256: str,
                parse_run_id: str,
                **_: object,
            ) -> dict[str, object]:
                row = dict(paragraph)
                row["parse_run_id"] = parse_run_id
                row["artifact_sha256"] = artifact_sha256
                row["locator"] = {
                    "artifact_sha256": artifact_sha256,
                    "paragraph_ordinal": 1,
                }
                return {
                    "paragraphs": [row],
                    "tables": [],
                    "cells": [],
                    "text": "Word text",
                    "text_sha256": sha256_bytes(b"Word text"),
                    "document_xml_sha256": "2" * 64,
                    "drawing_count": 0,
                    "body_block_count": 1,
                    "conversion_receipt": {},
                }

            def fake_excel(
                payload: bytes,
                *,
                artifact_sha256: str,
                parse_run_id: str,
                **_: object,
            ) -> dict[str, object]:
                sheet = {
                    "schema": ole_parser.OLE_EXCEL_SHEET_SCHEMA,
                    "parse_run_id": parse_run_id,
                    "artifact_sha256": artifact_sha256,
                    "sheet_index": 1,
                    "sheet_name": "Sheet1",
                    "visibility": 0,
                    "row_count": 1,
                    "column_count": 1,
                    "merged_ranges": [],
                    "locator": {
                        "artifact_sha256": artifact_sha256,
                        "sheet_index": 1,
                    },
                    "statement": ole_parser.NON_CLAIM,
                }
                cell = {
                    "schema": ole_parser.OLE_EXCEL_CELL_SCHEMA,
                    "parse_run_id": parse_run_id,
                    "artifact_sha256": artifact_sha256,
                    "sheet_index": 1,
                    "sheet_name": "Sheet1",
                    "row_index": 1,
                    "column_index": 1,
                    "cell_type": "text",
                    "value": "Cell",
                    "error_text": None,
                    "date_system": None,
                    "locator": {
                        "artifact_sha256": artifact_sha256,
                        "sheet_index": 1,
                        "row_index": 1,
                        "column_index": 1,
                    },
                    "statement": ole_parser.NON_CLAIM,
                }
                return {
                    "sheets": [sheet],
                    "cells": [cell],
                    "text": "Cell",
                    "text_sha256": sha256_bytes(b"Cell"),
                    "date_system": 0,
                }

            stage = root / "stage"
            with (
                mock.patch.object(
                    ole_parser,
                    "_resolve_soffice",
                    return_value=Path("/usr/bin/true"),
                ),
                mock.patch.object(
                    ole_parser,
                    "_soffice_receipt",
                    return_value={"available": True, "version": "fixture"},
                ),
                mock.patch.object(
                    ole_parser,
                    "_xlrd_receipt",
                    return_value=(object(), {"available": True, "version": "fixture"}),
                ),
                mock.patch.object(ole_parser, "_extract_word", side_effect=fake_word),
                mock.patch.object(
                    ole_parser,
                    "_extract_excel",
                    side_effect=fake_excel,
                ),
            ):
                manifest = parse_verified_ole_run(
                    run_dir,
                    stage,
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256=raw_sha,
                )
            self.assertEqual(manifest["status"], "passed")
            self.assertEqual(manifest["counts"]["declared_ole_artifacts"], 2)
            self.assertEqual(manifest["counts"]["typed_extracted_artifacts"], 2)
            self.assertEqual(
                manifest["classification_counts"],
                {
                    EXCEL_TYPED_EXTRACTED: 1,
                    WORD_TYPED_EXTRACTED: 1,
                },
            )
            artifacts = _read_jsonl(stage / "ole-artifacts.jsonl")
            self.assertEqual(
                {row["primary_office_type"] for row in artifacts},
                {"word_doc", "excel_xls"},
            )
            streams = _read_jsonl(stage / "ole-streams.jsonl")
            self.assertEqual(
                {row["stream_name"] for row in streams},
                {"WordDocument", "Workbook"},
            )
            self.assertEqual(len(_read_jsonl(stage / "ole-word-paragraphs.jsonl")), 1)
            self.assertEqual(len(_read_jsonl(stage / "ole-excel-cells.jsonl")), 1)

    def test_image_only_word_retains_visual_page_locators(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [("image-form.DOC", _standard_stream_cfb("WordDocument"))],
            )

            def fake_word(
                blob_path: Path,
                *,
                artifact_sha256: str,
                parse_run_id: str,
                **_: object,
            ) -> dict[str, object]:
                return {
                    "paragraphs": [],
                    "tables": [],
                    "cells": [],
                    "text": "",
                    "text_sha256": sha256_bytes(b""),
                    "document_xml_sha256": "2" * 64,
                    "drawing_count": 1,
                    "body_block_count": 1,
                    "conversion_receipt": {},
                    "visual_page_inventory": {
                        "page_count": 2,
                        "rendered_pdf_sha256": "3" * 64,
                        "conversion_receipt": {},
                        "page_count_receipt": {},
                    },
                }

            stage = root / "stage"
            with (
                mock.patch.object(
                    ole_parser,
                    "_resolve_soffice",
                    return_value=Path("/usr/bin/true"),
                ),
                mock.patch.object(
                    ole_parser,
                    "_resolve_pdfinfo",
                    return_value=Path("/usr/bin/true"),
                ),
                mock.patch.object(
                    ole_parser,
                    "_soffice_receipt",
                    return_value={"available": True, "version": "fixture"},
                ),
                mock.patch.object(
                    ole_parser,
                    "_pdfinfo_receipt",
                    return_value={"available": True, "version": "fixture"},
                ),
                mock.patch.object(
                    ole_parser,
                    "_xlrd_receipt",
                    return_value=(None, {"available": False}),
                ),
                mock.patch.object(ole_parser, "_extract_word", side_effect=fake_word),
            ):
                manifest = parse_verified_ole_run(
                    run_dir,
                    stage,
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256=raw_sha,
                )
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(
                manifest["classification_counts"],
                {"needs_image_ocr_or_visual_review": 1},
            )
            self.assertEqual(manifest["counts"]["word_visual_pages"], 2)
            pages = _read_jsonl(stage / "ole-word-pages.jsonl")
            self.assertEqual(
                [row["locator"]["page_number"] for row in pages],
                [1, 2],
            )
            self.assertEqual(
                {row["content_status"] for row in pages},
                {"needs_image_ocr_or_visual_review"},
            )

    def test_corrupt_magic_is_exact_needs_review_not_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [("broken.DOC", b"not an ole container")],
            )
            stage = root / "stage"
            with (
                mock.patch.object(
                    ole_parser,
                    "_resolve_soffice",
                    return_value=None,
                ),
                mock.patch.object(
                    ole_parser,
                    "_xlrd_receipt",
                    return_value=(None, {"available": False}),
                ),
            ):
                manifest = parse_verified_ole_run(
                    run_dir,
                    stage,
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256=raw_sha,
                )
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["counts"]["declared_ole_artifacts"], 1)
            self.assertEqual(manifest["counts"]["needs_review_artifacts"], 1)
            artifact = _read_jsonl(stage / "ole-artifacts.jsonl")[0]
            self.assertEqual(
                artifact["classification"], "needs_corrupt_or_non_cfb_review"
            )
            self.assertEqual(artifact["issue_codes"], ["ole_magic_mismatch"])
            issue = _read_jsonl(stage / "ole-issues.jsonl")[0]
            self.assertEqual(issue["message_parameters"]["source_labels"], ["broken.DOC"])

    def test_manifest_hash_tampering_fails_before_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, _ = _raw_run(
                root,
                [("source.DOC", _standard_stream_cfb("WordDocument"))],
            )
            with self.assertRaisesRegex(ContractError, "sealed hash"):
                parse_verified_ole_run(
                    run_dir,
                    root / "stage",
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256="0" * 64,
                )

    def test_subprocess_call_disables_shell(self) -> None:
        completed = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(
            ole_parser.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = ole_parser._run(
                ["/usr/bin/example", "--version"],
                timeout_seconds=1,
                environment={"PATH": "/usr/bin"},
            )
        self.assertIs(result, completed)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/example", "--version"],
        )


if __name__ == "__main__":
    unittest.main()
