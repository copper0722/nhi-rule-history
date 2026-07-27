from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from nhi_rule_history.parsers.image import (
    IMAGE_MEDIA_TYPES,
    ImageExtractionError,
    _OcrRuntime,
    build_public_image_receipt,
    parse_verified_image_run,
    verify_image_stage,
)


def _image_bytes(
    image_format: str,
    *,
    frames: int = 1,
) -> bytes:
    images = [
        Image.new(
            "RGB",
            (8 + index, 6 + index),
            (40 + index * 50, 70, 120),
        )
        for index in range(frames)
    ]
    buffer = io.BytesIO()
    options = {}
    if image_format == "JPEG":
        options = {"quality": 90, "dpi": (72, 72)}
    if frames > 1:
        images[0].save(
            buffer,
            format=image_format,
            save_all=True,
            append_images=images[1:],
            duration=100,
            loop=0,
            **options,
        )
    else:
        images[0].save(buffer, format=image_format, **options)
    return buffer.getvalue()


def _fixture(
    root: Path,
    specs: list[tuple[str, bytes]],
    *,
    linked: bool = True,
) -> tuple[Path, SimpleNamespace, dict[str, int]]:
    run_dir = root / "run"
    artifacts = []
    resources = []
    links = []
    counts = {media_type: 0 for media_type in IMAGE_MEDIA_TYPES}
    for index, (media_type, payload) in enumerate(specs):
        digest = f"{index + 1:064x}"
        relative = f"raw/sha256/{digest[:2]}/{digest}"
        path = run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        artifacts.append(
            {
                "artifact_sha256": digest,
                "byte_size": len(payload),
                "content_path": relative,
                "media_type": media_type,
            }
        )
        resource_id = f"resource-{index}"
        resources.append(
            {
                "resource_id": resource_id,
                "source_label": f"attachment-{index}",
            }
        )
        if linked:
            links.append(
                {
                    "resource_id": resource_id,
                    "artifact_sha256": digest,
                }
            )
        counts[media_type] += 1
    acquisition = SimpleNamespace(
        run_id="11111111-1111-4111-8111-111111111111",
        raw_manifest_sha256="a" * 64,
        sealed_fingerprint="b" * 64,
        rows={
            "raw-artifacts.jsonl": tuple(artifacts),
            "discovered-resources.jsonl": tuple(resources),
            "resource-artifact-links.jsonl": tuple(links),
        },
    )
    return run_dir, acquisition, counts


def _capture_receipt(
    acquisition: SimpleNamespace,
    counts: dict[str, int],
) -> dict:
    return {
        "schema": (
            "nhi-rule-history/"
            "historical-events-exact-phrase-capture-public-receipt/v1"
        ),
        "scope": {
            "query_start": "1996-01-01",
            "query_end": "2020-12-31",
            "capture_cut": "2026-07-27",
            "query": "藥品給付規定",
            "query_mode": "exact_phrase_bounded_baseline",
            "source_plan_sha256": "c" * 64,
        },
        "accepted_acquisition": {
            "run_id": acquisition.run_id,
            "state": "sealed",
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "sealed_fingerprint": acquisition.sealed_fingerprint,
            "media_type_counts": counts,
        },
    }


class ImageParserTests(unittest.TestCase):
    def test_jpeg_inventory_preserves_metadata_dimensions_and_pixels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.image."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                manifest = parse_verified_image_run(
                    run_dir,
                    stage,
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )
            artifact = json.loads(
                (stage / "image-artifacts.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            frame = json.loads(
                (stage / "image-frames.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(artifact["magic_media_type"], "image/jpeg")
            self.assertEqual(artifact["source_format"], "JPEG")
            self.assertEqual(
                artifact["source_dimensions"],
                {"width": 8, "height": 6},
            )
            self.assertIn("jfif", artifact["source_metadata"])
            self.assertEqual(frame["locator"]["frame_count"], 1)
            self.assertEqual(frame["locator"]["frame_kind"], "single_image")
            self.assertEqual(frame["render"]["mode"], "RGB")
            self.assertEqual(frame["render"]["pixel_count"], 48)
            self.assertEqual(
                len(frame["render"]["rendered_pixel_sha256"]), 64
            )
            self.assertEqual(frame["ocr_status"], "needs_ocr")
            self.assertTrue(frame["needs_visual_review"])
            self.assertEqual(manifest["counts"]["needs_ocr_frames"], 1)
            self.assertEqual(
                manifest["counts"]["needs_visual_review_frames"], 1
            )
            self.assertFalse(
                manifest["closure_claims"]["legal_semantics_inferred"]
            )

    def test_gif_frames_and_tiff_pages_are_exhaustive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [
                    ("image/gif", _image_bytes("GIF", frames=2)),
                    ("image/tiff", _image_bytes("TIFF", frames=2)),
                ],
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.image."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                manifest = parse_verified_image_run(
                    run_dir,
                    stage,
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )
            self.assertEqual(manifest["counts"]["frames"], 4)
            self.assertEqual(
                manifest["counts"]["frame_kind_counts"],
                {
                    "gif_animation_frame": 2,
                    "tiff_page": 2,
                },
            )
            self.assertEqual(len(manifest["render_files"]), 4)
            verify_image_stage(stage)

    def test_output_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
            )
            first = root / "first"
            second = root / "second"
            with mock.patch(
                "nhi_rule_history.parsers.image."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                first_manifest = parse_verified_image_run(
                    run_dir,
                    first,
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )
                second_manifest = parse_verified_image_run(
                    run_dir,
                    second,
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )
            self.assertEqual(first_manifest, second_manifest)
            for relative in (
                "image-artifacts.jsonl",
                "image-frames.jsonl",
                "image-ocr.jsonl",
                "image-manifest.json",
            ):
                self.assertEqual(
                    (first / relative).read_bytes(),
                    (second / relative).read_bytes(),
                )
            render = first_manifest["render_files"][0]["filename"]
            self.assertEqual(
                (first / render).read_bytes(),
                (second / render).read_bytes(),
            )

    def test_bound_ocr_output_is_unreviewed_and_review_remains_open(
        self,
    ) -> None:
        runtime = _OcrRuntime(
            tesseract_path=Path("/fixture/tesseract"),
            sandbox_path=Path("/fixture/sandbox-exec"),
            tessdata_dir=Path("/fixture/tessdata"),
            public_binding={
                "status": "eligible_bound_local_no_network",
                "runtime_fingerprint": "d" * 64,
            },
        )
        ocr_result = {
            "text": "測試 OCR\n",
            "text_sha256": hashlib.sha256(
                "測試 OCR\n".encode("utf-8")
            ).hexdigest(),
            "text_bytes": len("測試 OCR\n".encode("utf-8")),
            "text_characters": len("測試 OCR\n"),
            "text_lines": 1,
            "stderr": "",
            "stderr_sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934"
                "ca495991b7852b855"
            ),
            "stderr_bytes": 0,
            "exit_code": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
            )
            stage = root / "stage"
            with (
                mock.patch(
                    "nhi_rule_history.parsers.image."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                mock.patch(
                    "nhi_rule_history.parsers.image."
                    "_discover_ocr_with_fingerprint",
                    return_value=runtime,
                ),
                mock.patch(
                    "nhi_rule_history.parsers.image._run_ocr",
                    return_value=ocr_result,
                ),
            ):
                manifest = parse_verified_image_run(
                    run_dir,
                    stage,
                    expected_media_type_counts=counts,
                    ocr_mode="required",
                )
            observation = json.loads(
                (stage / "image-ocr.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(observation["text"], "測試 OCR\n")
            self.assertEqual(
                observation["status"],
                "unreviewed_ocr_candidate_needs_visual_review",
            )
            self.assertEqual(manifest["counts"]["needs_ocr_frames"], 0)
            self.assertEqual(
                manifest["counts"]["needs_visual_review_frames"], 1
            )
            verify_image_stage(stage)

    def test_public_receipt_is_compact_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
            )
            stage = root / "stage"
            with mock.patch(
                "nhi_rule_history.parsers.image."
                "validate_acquisition_run",
                return_value=acquisition,
            ):
                parse_verified_image_run(
                    run_dir,
                    stage,
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )
            receipt = build_public_image_receipt(
                stage_dir=stage,
                historical_capture_receipt=_capture_receipt(
                    acquisition, counts
                ),
            )
            self.assertEqual(
                receipt["sealed_input"]["image_artifact_denominator"], 1
            )
            self.assertEqual(
                receipt["review_denominator"]["needs_ocr_frames"], 1
            )
            self.assertEqual(
                receipt["status"],
                "passed_source_render_inventory_needs_ocr",
            )
            self.assertEqual(
                receipt["ocr"]["status"], "not_run_needs_ocr"
            )
            self.assertFalse(receipt["claims"]["history_complete"])
            render = (
                stage
                / json.loads(
                    (stage / "image-frames.jsonl").read_text(
                        encoding="utf-8"
                    )
                )["render"]["png_relative_path"]
            )
            render.write_bytes(render.read_bytes() + b"x")
            with self.assertRaisesRegex(
                ImageExtractionError, "rendered PNG changed"
            ):
                build_public_image_receipt(
                    stage_dir=stage,
                    historical_capture_receipt=_capture_receipt(
                        acquisition, counts
                    ),
                )

    def test_denominator_and_source_link_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
            )
            wrong = dict(counts)
            wrong["image/jpeg"] = 2
            with (
                mock.patch(
                    "nhi_rule_history.parsers.image."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                self.assertRaisesRegex(
                    ImageExtractionError, "denominator"
                ),
            ):
                parse_verified_image_run(
                    run_dir,
                    root / "stage",
                    expected_media_type_counts=wrong,
                    ocr_mode="disabled",
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, acquisition, counts = _fixture(
                root,
                [("image/jpeg", _image_bytes("JPEG"))],
                linked=False,
            )
            with (
                mock.patch(
                    "nhi_rule_history.parsers.image."
                    "validate_acquisition_run",
                    return_value=acquisition,
                ),
                self.assertRaisesRegex(
                    ImageExtractionError, "source-resource link"
                ),
            ):
                parse_verified_image_run(
                    run_dir,
                    root / "stage",
                    expected_media_type_counts=counts,
                    ocr_mode="disabled",
                )

    def test_magic_format_mismatch_and_corruption_fail_closed(self) -> None:
        cases = (
            (
                "format mismatch",
                "image/jpeg",
                _image_bytes("GIF"),
            ),
            (
                "corrupt",
                "image/jpeg",
                b"\xff\xd8\xffnot-a-jpeg",
            ),
        )
        for label, media_type, payload in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    run_dir, acquisition, counts = _fixture(
                        root, [(media_type, payload)]
                    )
                    stage = root / "stage"
                    with (
                        mock.patch(
                            "nhi_rule_history.parsers.image."
                            "validate_acquisition_run",
                            return_value=acquisition,
                        ),
                        self.assertRaises(ImageExtractionError),
                    ):
                        parse_verified_image_run(
                            run_dir,
                            stage,
                            expected_media_type_counts=counts,
                            ocr_mode="disabled",
                        )
                    self.assertFalse(stage.exists())


if __name__ == "__main__":
    unittest.main()
