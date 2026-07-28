from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import uuid
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
from nhi_rule_history.parsers import pdf as pdf_parser
from nhi_rule_history.parsers.pdf import (
    BLOCKING_PARSE_FAILURE,
    IMAGE_ONLY_NEEDS_OCR,
    TEXT_EXTRACTED,
    parse_verified_pdf_run,
)


POPPLER_AVAILABLE = bool(shutil.which("pdfinfo") and shutil.which("pdftotext"))


def _pdf(objects: list[bytes]) -> bytes:
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def _text_pdf(text: str = "Revision 110/1/1") -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii")
    return _pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            ),
            (
                f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
                + stream
                + b"\nendstream"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
    )


def _image_only_pdf() -> bytes:
    content = b"q 100 0 0 100 10 10 cm /Im0 Do Q"
    image = b"00>"
    return _pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                b"/Resources << /XObject << /Im0 5 0 R >> >> "
                b"/Contents 4 0 R >>"
            ),
            (
                f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
                + content
                + b"\nendstream"
            ),
            (
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8 "
                b"/Filter /ASCIIHexDecode /Length 3 >>\nstream\n"
                + image
                + b"\nendstream"
            ),
        ]
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def _raw_run(
    root: Path,
    specifications: list[tuple[str, bytes, str]],
) -> tuple[Path, str]:
    run_dir = root / "raw-run"
    run_dir.mkdir()
    resources: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    for ordinal, (label, payload, media_type) in enumerate(specifications, 1):
        resource_id = sha256_bytes(f"resource:{ordinal}:{label}".encode())
        digest = sha256_bytes(payload)
        relative = relative_blob_path(digest)
        blob_path = run_dir / relative
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(payload)
        resource = {
            "schema": "nhi-rule-history/discovered-resource/v2",
            "resource_id": resource_id,
            "adapter_id": "fixture",
            "resource_kind": "official_attachment",
            "source_url": f"https://example.invalid/{ordinal}",
            "discovery_locator": {
                "attachment_ordinal": ordinal,
                "attachment_visible_label": label,
            },
            "source_label": label,
            "fetch_state": "pending",
        }
        resources.append(resource)
        if not any(row["artifact_sha256"] == digest for row in artifacts):
            artifacts.append(
                {
                    "schema": "nhi-rule-history/raw-artifact/v2",
                    "artifact_sha256": digest,
                    "byte_size": len(payload),
                    "content_path": relative,
                    "media_type": media_type,
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
                "response_headers": {"content-type": media_type},
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
                "source_plan_sha256": "1" * 64,
            }
        )
    )
    manifested_names = [*rows_by_name, "discovery-manifest.json"]
    raw_manifest = {
        "schema": "nhi-rule-history/raw-manifest/v2",
        "status": "success",
        "capture_cut": "2026-07-27",
        "source_plan_schema": "nhi-rule-history/source-plan/v2",
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
            for filename in manifested_names
        ],
    }
    manifest_path = run_dir / "raw-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(raw_manifest))
    return run_dir, file_sha256(manifest_path)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


@unittest.skipUnless(POPPLER_AVAILABLE, "local Poppler tools are required")
class PDFParserIntegrationTests(unittest.TestCase):
    def test_text_and_image_pdf_are_exhaustively_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [
                    ("修訂對照表.PDF", _text_pdf(), "application/pdf"),
                    ("掃描附件.pdf", _image_only_pdf(), "application/pdf"),
                ],
            )
            parse_run_id = str(uuid.uuid4())
            stage = root / "stage"
            manifest = parse_verified_pdf_run(
                run_dir,
                stage,
                parse_run_id=parse_run_id,
                expected_raw_manifest_sha256=raw_sha,
            )
            self.assertEqual(manifest["status"], "passed")
            self.assertEqual(manifest["raw_manifest_sha256"], raw_sha)
            self.assertEqual(
                manifest["counts"],
                {
                    "declared_pdf_resources": 2,
                    "declared_pdf_artifacts": 2,
                    "text_extracted_artifacts": 1,
                    "image_only_needs_ocr_artifacts": 1,
                    "blocking_parse_failure_artifacts": 0,
                    "pages": 2,
                    "pages_without_words": 1,
                    "flows": 1,
                    "blocks": 1,
                    "lines": 1,
                    "words": 2,
                    "blocking_issues": 0,
                },
            )
            artifacts = _read_jsonl(stage / "pdf-artifacts.jsonl")
            self.assertEqual(
                {row["classification"] for row in artifacts},
                {TEXT_EXTRACTED, IMAGE_ONLY_NEEDS_OCR},
            )
            labels = {
                binding["source_label"]
                for row in artifacts
                for binding in row["resource_bindings"]
            }
            self.assertEqual(labels, {"修訂對照表.PDF", "掃描附件.pdf"})
            pages = _read_jsonl(stage / "pdf-pages.jsonl")
            text_page = next(page for page in pages if page["text"])
            self.assertEqual(text_page["text"], "Revision110/1/1")
            word = text_page["flows"][0]["blocks"][0]["lines"][0]["words"][0]
            self.assertEqual(word["text"], "Revision")
            self.assertEqual(
                set(word["bbox"]),
                {"x_min", "y_min", "x_max", "y_max"},
            )
            self.assertFalse(manifest["closure_claims"]["legal_dates_interpreted"])
            self.assertFalse(manifest["closure_claims"]["history_complete"])
            self.assertRegex(manifest["tools"]["pdfinfo"]["version"], r"pdfinfo version")
            self.assertRegex(
                manifest["tools"]["pdftotext"]["version"],
                r"pdftotext version",
            )
            self.assertRegex(manifest["output_fingerprint"], r"^[0-9a-f]{64}$")

            replay_stage = root / "replay-stage"
            replay = parse_verified_pdf_run(
                run_dir,
                replay_stage,
                parse_run_id=parse_run_id,
                expected_raw_manifest_sha256=raw_sha,
            )
            self.assertEqual(
                manifest["output_fingerprint"],
                replay["output_fingerprint"],
            )
            for filename in pdf_parser.PDF_STAGE_FILES:
                self.assertEqual(
                    (stage / filename).read_bytes(),
                    (replay_stage / filename).read_bytes(),
                )

    def test_magic_type_mismatch_is_recorded_then_batch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [("偽裝附件.PDF", b"not-a-pdf", "application/pdf")],
            )
            stage = root / "stage"
            with self.assertRaisesRegex(ContractError, "1 blocking issue"):
                parse_verified_pdf_run(
                    run_dir,
                    stage,
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256=raw_sha,
                )
            manifest = json.loads(
                (stage / "pdf-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["counts"]["declared_pdf_artifacts"], 1)
            self.assertEqual(
                manifest["counts"]["blocking_parse_failure_artifacts"], 1
            )
            artifact = _read_jsonl(stage / "pdf-artifacts.jsonl")[0]
            self.assertEqual(artifact["classification"], BLOCKING_PARSE_FAILURE)
            issue = _read_jsonl(stage / "pdf-issues.jsonl")[0]
            self.assertEqual(
                issue["issue_code"], "declared_pdf_magic_type_mismatch"
            )

    def test_unexpected_parser_exception_is_a_blocking_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [("附件.PDF", _text_pdf(), "application/pdf")],
            )
            stage = root / "stage"
            with mock.patch.object(
                pdf_parser,
                "_extract_layout",
                side_effect=RuntimeError("private path must not leak"),
            ):
                with self.assertRaisesRegex(ContractError, "1 blocking issue"):
                    parse_verified_pdf_run(
                        run_dir,
                        stage,
                        parse_run_id=str(uuid.uuid4()),
                        expected_raw_manifest_sha256=raw_sha,
                    )
            issue = _read_jsonl(stage / "pdf-issues.jsonl")[0]
            self.assertEqual(
                issue["issue_code"], "unexpected_pdf_parser_failure"
            )
            self.assertNotIn("private path", json.dumps(issue))


class PDFParserContractTests(unittest.TestCase):
    def test_expected_manifest_hash_is_required_and_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, raw_sha = _raw_run(
                root,
                [("附件.PDF", _text_pdf(), "application/pdf")],
            )
            self.assertNotEqual(raw_sha, "0" * 64)
            with self.assertRaisesRegex(ContractError, "sealed hash"):
                parse_verified_pdf_run(
                    run_dir,
                    root / "stage",
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256="0" * 64,
                )

    def test_manifest_path_escape_fails_before_raw_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, _ = _raw_run(
                root,
                [("附件.PDF", _text_pdf(), "application/pdf")],
            )
            path = run_dir / "raw-manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["files"][0]["filename"] = "../escape.jsonl"
            path.write_bytes(canonical_json_bytes(manifest))
            with self.assertRaisesRegex(ContractError, "escapes run directory"):
                parse_verified_pdf_run(
                    run_dir,
                    root / "stage",
                    parse_run_id=str(uuid.uuid4()),
                    expected_raw_manifest_sha256=file_sha256(path),
                )

    def test_malformed_xhtml_and_page_count_mismatch_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            pdf_parser._ArtifactFailure,
            "malformed_poppler_xhtml",
        ):
            pdf_parser._parse_poppler_xhtml(
                b"<html><body><doc><page></doc></body></html>",
                artifact_sha256="a" * 64,
                expected_page_count=1,
            )
        valid_empty_page = b"""\
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
<page width="200.000000" height="200.000000"></page>
</doc></body></html>"""
        with self.assertRaisesRegex(
            pdf_parser._ArtifactFailure,
            "page_count_mismatch",
        ):
            pdf_parser._parse_poppler_xhtml(
                valid_empty_page,
                artifact_sha256="a" * 64,
                expected_page_count=2,
            )

    def test_subprocess_invocation_explicitly_disables_shell(self) -> None:
        completed = subprocess_result = mock.Mock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        with mock.patch.object(
            pdf_parser.subprocess,
            "run",
            return_value=subprocess_result,
        ) as run:
            self.assertIs(
                pdf_parser._run_tool(
                    ["/usr/bin/example", "--version"],
                    timeout_seconds=1,
                ),
                completed,
            )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/example", "--version"],
        )


if __name__ == "__main__":
    unittest.main()
