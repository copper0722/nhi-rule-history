"""Fail-closed image inventory and bound local OCR for historical attachments.

Every source image is first bound to the sealed acquisition artifact hash,
declared media type, magic bytes, resource identities, and labels.  Pillow
decoding produces deterministic RGB renders and pixel hashes for every
page/frame.  OCR is optional and is only admitted when the executable,
language models, version output, argv template, environment, and a macOS
network-denying sandbox are all fingerprinted.

OCR output remains an unreviewed source-local observation.  It is never legal
text, a rule identity, an amendment event, or evidence of history completeness.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import PIL
    from PIL import (
        GifImagePlugin,
        Image,
        ImageFile,
        JpegImagePlugin,
        TiffImagePlugin,
        features,
    )
except ImportError as exc:  # pragma: no cover - exercised by deployment preflight
    raise RuntimeError("Pillow is required for image extraction") from exc

from nhi_rule_history.contracts import (
    ContractError,
    assert_public_value,
    canonical_json_bytes,
    file_sha256,
    manifest_file_entry,
    resolve_run_relative,
    sha256_bytes,
    write_json,
)
from nhi_rule_history.pg.acquisition import (
    AcquisitionLoadError,
    AcquisitionMaterial,
    validate_acquisition_run,
)


PARSER_VERSION = "nhi-rule-history-image-render-ocr/1.0.0"
MANIFEST_SCHEMA = "nhi-rule-history/image-extraction-manifest/v1"
ARTIFACT_SCHEMA = "nhi-rule-history/image-artifact-observation/v1"
FRAME_SCHEMA = "nhi-rule-history/image-frame-observation/v1"
OCR_SCHEMA = "nhi-rule-history/image-ocr-observation/v1"
PUBLIC_RECEIPT_SCHEMA = (
    "nhi-rule-history/historical-image-extraction-public-receipt/v1"
)
HISTORICAL_CAPTURE_RECEIPT_SCHEMA = (
    "nhi-rule-history/historical-events-exact-phrase-capture-public-receipt/v1"
)
NON_CLAIM = (
    "Source-local image/render/OCR observation only; OCR is unreviewed and "
    "does not establish legal text, a rule identity, an official amendment "
    "event, a legal effective date, or history completeness."
)
IMAGE_MEDIA_TYPES = ("image/gif", "image/jpeg", "image/tiff")
OUTPUT_JSONL_FILES = (
    "image-artifacts.jsonl",
    "image-frames.jsonl",
    "image-ocr.jsonl",
)
OCR_LANGUAGES = ("chi_tra", "eng")
OCR_LANGUAGE_EXPRESSION = "+".join(OCR_LANGUAGES)
OCR_ARGV_TEMPLATE = (
    "{sandbox_executable}",
    "-p",
    "{sandbox_profile}",
    "{tesseract_executable}",
    "{render_png}",
    "stdout",
    "-l",
    OCR_LANGUAGE_EXPRESSION,
    "--oem",
    "1",
    "--psm",
    "6",
)
SANDBOX_PROFILE_TEMPLATE = """(version 1)
(deny default)
(allow process*)
(allow file-read*)
(allow file-write* (subpath "{scratch_dir}"))
(deny network*)
"""
OCR_ENV_TEMPLATE = {
    "LANG": "C",
    "LC_ALL": "C",
    "OMP_THREAD_LIMIT": "1",
    "TESSDATA_PREFIX": "{tessdata_dir}",
    "TMPDIR": "{scratch_dir}",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ImageExtractionError(ContractError):
    """A sealed-input, image-format, render, OCR, or stage verification error."""


@dataclass(frozen=True)
class _OcrRuntime:
    tesseract_path: Path
    sandbox_path: Path
    tessdata_dir: Path
    public_binding: Mapping[str, Any]


def _fail(message: str) -> None:
    raise ImageExtractionError(message)


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source_row_sha256"] = sha256_bytes(
        canonical_json_bytes(result)
    )
    return result


def _row_set_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    values = sorted(str(row["source_row_sha256"]) for row in rows)
    if any(not _SHA256_RE.fullmatch(value) for value in values):
        _fail("image row set contains an invalid source hash")
    return sha256_bytes(
        "".join(f"{value}\n" for value in values).encode("ascii")
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(canonical_json_bytes(dict(row)))
        stream.flush()
        os.fsync(stream.fileno())


def _magic_media_type(payload: bytes) -> str | None:
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(
        (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    ):
        return "image/tiff"
    return None


def _metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("image metadata contains a non-finite float")
        return {"type": "float", "repr": repr(value)}
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "bytes": len(value),
            "sha256": sha256_bytes(value),
            "base64": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, tuple):
        return {
            "type": "tuple",
            "items": [_metadata_value(item) for item in value],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "items": [_metadata_value(item) for item in value],
        }
    if isinstance(value, Mapping):
        return {
            "type": "mapping",
            "items": {
                str(key): _metadata_value(child)
                for key, child in sorted(
                    value.items(), key=lambda item: str(item[0])
                )
            },
        }
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if isinstance(numerator, int) and isinstance(denominator, int):
        return {
            "type": "rational",
            "numerator": numerator,
            "denominator": denominator,
        }
    _fail(
        "image metadata contains an unsupported value type: "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _metadata_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        str(key): _metadata_value(child)
        for key, child in sorted(value.items(), key=lambda item: str(item[0]))
    }


def _pillow_binding() -> dict[str, Any]:
    module_paths = {
        "Image.py": Path(Image.__file__),
        "JpegImagePlugin.py": Path(JpegImagePlugin.__file__),
        "GifImagePlugin.py": Path(GifImagePlugin.__file__),
        "TiffImagePlugin.py": Path(TiffImagePlugin.__file__),
    }
    try:
        import PIL._imaging as imaging_core
    except ImportError as exc:  # pragma: no cover
        raise ImageExtractionError(
            "Pillow imaging core is unavailable"
        ) from exc
    module_paths["_imaging"] = Path(imaging_core.__file__)
    file_bindings = {
        name: {
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for name, path in sorted(module_paths.items())
    }
    codec_versions = {
        feature: features.version(feature)
        for feature in (
            "jpg",
            "jpg_2000",
            "libtiff",
            "webp",
            "zlib",
        )
        if features.check(feature)
    }
    return {
        "pillow_version": PIL.__version__,
        "module_files": file_bindings,
        "codec_versions": codec_versions,
        "render_mode": "RGB",
        "alpha_composite_background": "#ffffff",
        "png_parameters": {
            "format": "PNG",
            "compress_level": 9,
            "optimize": False,
        },
    }


def _command_output(argv: list[str]) -> tuple[bytes, bytes]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            env={"LANG": "C", "LC_ALL": "C"},
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ImageExtractionError(
            "OCR dependency probe failed"
        ) from exc
    if completed.returncode != 0:
        _fail("OCR dependency probe returned non-zero")
    return completed.stdout, completed.stderr


def _discover_ocr_runtime() -> _OcrRuntime:
    tesseract_command = shutil.which("tesseract")
    sandbox_command = shutil.which("sandbox-exec")
    if not tesseract_command or not sandbox_command:
        _fail("bound local OCR requires tesseract and sandbox-exec")
    tesseract_path = Path(tesseract_command).resolve()
    sandbox_path = Path(sandbox_command).resolve()
    if not tesseract_path.is_file() or not sandbox_path.is_file():
        _fail("OCR executable path is not a regular file")
    version_stdout, version_stderr = _command_output(
        [str(tesseract_path), "--version"]
    )
    langs_stdout, langs_stderr = _command_output(
        [str(tesseract_path), "--list-langs"]
    )
    combined_langs = (langs_stdout + langs_stderr).decode(
        "utf-8", errors="strict"
    )
    match = re.search(
        r'List of available languages in "([^"]+)/?"', combined_langs
    )
    if match is None:
        _fail("cannot bind the tesseract language-data directory")
    tessdata_dir = Path(match.group(1)).resolve()
    listed_languages = {
        line.strip()
        for line in combined_langs.splitlines()
        if line.strip() and not line.startswith("List of available")
    }
    if not set(OCR_LANGUAGES).issubset(listed_languages):
        _fail("required OCR languages are unavailable")
    model_bindings: dict[str, dict[str, Any]] = {}
    for language in OCR_LANGUAGES:
        path = tessdata_dir / f"{language}.traineddata"
        if not path.is_file():
            _fail("listed OCR language model file is missing")
        model_bindings[language] = {
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
    public_binding = {
        "status": "eligible_bound_local_no_network",
        "tesseract": {
            "executable_basename": tesseract_path.name,
            "bytes": tesseract_path.stat().st_size,
            "sha256": file_sha256(tesseract_path),
            "version_stdout": version_stdout.decode("utf-8"),
            "version_stdout_sha256": sha256_bytes(version_stdout),
            "version_stderr": version_stderr.decode("utf-8"),
            "version_stderr_sha256": sha256_bytes(version_stderr),
            "list_languages_stdout_sha256": sha256_bytes(langs_stdout),
            "list_languages_stderr_sha256": sha256_bytes(langs_stderr),
        },
        "models": model_bindings,
        "language_expression": OCR_LANGUAGE_EXPRESSION,
        "sandbox": {
            "executable_basename": sandbox_path.name,
            "bytes": sandbox_path.stat().st_size,
            "sha256": file_sha256(sandbox_path),
            "profile_template": SANDBOX_PROFILE_TEMPLATE,
            "profile_template_sha256": sha256_bytes(
                SANDBOX_PROFILE_TEMPLATE.encode("utf-8")
            ),
            "network_rule": "(deny network*)",
        },
        "argv_template": list(OCR_ARGV_TEMPLATE),
        "environment_template": dict(OCR_ENV_TEMPLATE),
    }
    return _OcrRuntime(
        tesseract_path=tesseract_path,
        sandbox_path=sandbox_path,
        tessdata_dir=tessdata_dir,
        public_binding=public_binding,
    )


def _run_ocr(
    *,
    runtime: _OcrRuntime,
    render_path: Path,
    scratch_parent: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="ocr.",
        dir=scratch_parent,
    ) as scratch_name:
        scratch = Path(scratch_name)
        profile = SANDBOX_PROFILE_TEMPLATE.format(
            scratch_dir=str(scratch)
        )
        argv = [
            str(runtime.sandbox_path),
            "-p",
            profile,
            str(runtime.tesseract_path),
            str(render_path),
            "stdout",
            "-l",
            OCR_LANGUAGE_EXPRESSION,
            "--oem",
            "1",
            "--psm",
            "6",
        ]
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_THREAD_LIMIT": "1",
            "TESSDATA_PREFIX": str(runtime.tessdata_dir),
            "TMPDIR": str(scratch),
        }
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                env=environment,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ImageExtractionError("bound local OCR failed") from exc
    if completed.returncode != 0:
        _fail("bound local OCR returned non-zero")
    try:
        text = completed.stdout.decode("utf-8")
        stderr = completed.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImageExtractionError(
            "bound local OCR output is not UTF-8"
        ) from exc
    return {
        "text": text,
        "text_sha256": sha256_bytes(completed.stdout),
        "text_bytes": len(completed.stdout),
        "text_characters": len(text),
        "text_lines": len(text.splitlines()),
        "stderr": stderr,
        "stderr_sha256": sha256_bytes(completed.stderr),
        "stderr_bytes": len(completed.stderr),
        "exit_code": completed.returncode,
    }


def _artifact_sources(
    acquisition: AcquisitionMaterial,
) -> dict[str, list[dict[str, Any]]]:
    resources = {
        row["resource_id"]: row
        for row in acquisition.rows["discovered-resources.jsonl"]
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for link in acquisition.rows["resource-artifact-links.jsonl"]:
        resource = resources.get(link["resource_id"])
        if resource is None:
            _fail("image artifact link references an unknown resource")
        key = (link["artifact_sha256"], link["resource_id"])
        if key in seen:
            continue
        seen.add(key)
        result[link["artifact_sha256"]].append(dict(resource))
    for rows in result.values():
        rows.sort(key=lambda row: row["resource_id"])
    return result


def _frame_kind(media_type: str, frame_count: int) -> str:
    if media_type == "image/tiff":
        return "tiff_page"
    if media_type == "image/gif" and frame_count > 1:
        return "gif_animation_frame"
    return "single_image"


def _render_rgb(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def _pixel_sha256(image: Image.Image) -> str:
    header = canonical_json_bytes(
        {
            "mode": image.mode,
            "width": image.width,
            "height": image.height,
            "byte_order": "Pillow RGB channel order",
        }
    )
    return sha256_bytes(header + image.tobytes())


def _read_and_verify_image(
    *,
    payload: bytes,
    declared_media_type: str,
) -> tuple[str, int]:
    magic = _magic_media_type(payload)
    if magic != declared_media_type:
        _fail("declared image media type differs from magic bytes")
    expected_format = {
        "image/jpeg": "JPEG",
        "image/gif": "GIF",
        "image/tiff": "TIFF",
    }[declared_media_type]
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as probe:
                if probe.format != expected_format:
                    _fail("Pillow format differs from declared image media type")
                frame_count = int(getattr(probe, "n_frames", 1))
                if frame_count < 1:
                    _fail("decoded image has no frames")
                probe.verify()
    except ImageExtractionError:
        raise
    except (
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageExtractionError(
            "image source verification failed"
        ) from exc
    return expected_format, frame_count


def _parse_artifact(
    *,
    extraction_id: str,
    artifact: Mapping[str, Any],
    payload: bytes,
    source_rows: list[Mapping[str, Any]],
    render_root: Path,
    ocr_runtime: _OcrRuntime | None,
    scratch_parent: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    media_type = str(artifact["media_type"])
    source_format, frame_count = _read_and_verify_image(
        payload=payload,
        declared_media_type=media_type,
    )
    resource_ids = sorted(
        {str(row["resource_id"]) for row in source_rows}
    )
    source_labels = sorted(
        {
            str(row["source_label"])
            for row in source_rows
            if row.get("source_label")
        }
    )
    artifact_render_dir = render_root / artifact["artifact_sha256"]
    artifact_render_dir.mkdir(parents=True, exist_ok=False)
    frame_rows: list[dict[str, Any]] = []
    ocr_rows: list[dict[str, Any]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(payload))
        except (OSError, SyntaxError, ValueError) as exc:
            raise ImageExtractionError("image decode failed") from exc
        with image:
            source_metadata = _metadata_mapping(image.info)
            source_mode = image.mode
            source_dimensions = {
                "width": image.width,
                "height": image.height,
            }
            for frame_index in range(frame_count):
                try:
                    image.seek(frame_index)
                    image.load()
                except (EOFError, OSError, ValueError) as exc:
                    raise ImageExtractionError(
                        "image frame decode failed"
                    ) from exc
                source_frame = image.copy()
                rendered = _render_rgb(source_frame)
                pixel_sha256 = _pixel_sha256(rendered)
                render_relative = (
                    f"renders/{artifact['artifact_sha256']}/"
                    f"frame-{frame_index:04d}.png"
                )
                render_path = render_root.parent / render_relative
                rendered.save(
                    render_path,
                    format="PNG",
                    compress_level=9,
                    optimize=False,
                )
                with Image.open(render_path) as render_check:
                    render_check.load()
                    if (
                        render_check.mode != "RGB"
                        or render_check.size != rendered.size
                        or _pixel_sha256(render_check) != pixel_sha256
                    ):
                        _fail("deterministic PNG render verification failed")
                frame_metadata: dict[str, Any] = {
                    "info": _metadata_mapping(image.info),
                }
                if media_type == "image/tiff":
                    frame_metadata["tiff_tags"] = _metadata_mapping(
                        image.tag_v2
                    )
                frame_locator = {
                    "frame_index": frame_index,
                    "frame_count": frame_count,
                    "frame_kind": _frame_kind(media_type, frame_count),
                }
                identity = {
                    "artifact_sha256": artifact["artifact_sha256"],
                    "frame_locator": frame_locator,
                    "rendered_pixel_sha256": pixel_sha256,
                }
                frame_id = sha256_bytes(canonical_json_bytes(identity))
                frame_row = _source_row(
                    {
                        "schema": FRAME_SCHEMA,
                        "extraction_id": extraction_id,
                        "frame_id": frame_id,
                        "artifact_sha256": artifact["artifact_sha256"],
                        "declared_media_type": media_type,
                        "source_format": source_format,
                        "source_resource_ids": resource_ids,
                        "source_labels": source_labels,
                        "locator": frame_locator,
                        "source_frame": {
                            "mode": source_frame.mode,
                            "width": source_frame.width,
                            "height": source_frame.height,
                        },
                        "metadata": frame_metadata,
                        "render": {
                            "mode": "RGB",
                            "width": rendered.width,
                            "height": rendered.height,
                            "pixel_count": (
                                rendered.width * rendered.height
                            ),
                            "rendered_pixel_sha256": pixel_sha256,
                            "png_relative_path": render_relative,
                            "png_bytes": render_path.stat().st_size,
                            "png_sha256": file_sha256(render_path),
                        },
                        "ocr_status": (
                            "unreviewed_candidate_generated"
                            if ocr_runtime is not None
                            else "needs_ocr"
                        ),
                        "needs_visual_review": True,
                        "statement": NON_CLAIM,
                    }
                )
                frame_rows.append(frame_row)
                if ocr_runtime is not None:
                    ocr_result = _run_ocr(
                        runtime=ocr_runtime,
                        render_path=render_path,
                        scratch_parent=scratch_parent,
                    )
                    ocr_identity = {
                        "frame_id": frame_id,
                        "ocr_runtime_fingerprint": (
                            ocr_runtime.public_binding[
                                "runtime_fingerprint"
                            ]
                        ),
                        "text_sha256": ocr_result["text_sha256"],
                    }
                    ocr_rows.append(
                        _source_row(
                            {
                                "schema": OCR_SCHEMA,
                                "extraction_id": extraction_id,
                                "ocr_observation_id": sha256_bytes(
                                    canonical_json_bytes(ocr_identity)
                                ),
                                "artifact_sha256": artifact[
                                    "artifact_sha256"
                                ],
                                "frame_id": frame_id,
                                "frame_locator": frame_locator,
                                "rendered_pixel_sha256": pixel_sha256,
                                "render_png_sha256": frame_row["render"][
                                    "png_sha256"
                                ],
                                "runtime_fingerprint": (
                                    ocr_runtime.public_binding[
                                        "runtime_fingerprint"
                                    ]
                                ),
                                "status": (
                                    "unreviewed_ocr_candidate_"
                                    "needs_visual_review"
                                ),
                                **ocr_result,
                                "statement": NON_CLAIM,
                            }
                        )
                    )
    counts = {
        "frames": len(frame_rows),
        "pages": sum(
            row["locator"]["frame_kind"] == "tiff_page"
            for row in frame_rows
        ),
        "rendered_pixels": sum(
            row["render"]["pixel_count"] for row in frame_rows
        ),
        "ocr_observations": len(ocr_rows),
        "needs_ocr_frames": (
            len(frame_rows) if ocr_runtime is None else 0
        ),
        "needs_visual_review_frames": len(frame_rows),
    }
    artifact_row = _source_row(
        {
            "schema": ARTIFACT_SCHEMA,
            "extraction_id": extraction_id,
            "artifact_sha256": artifact["artifact_sha256"],
            "source_byte_size": artifact["byte_size"],
            "declared_media_type": media_type,
            "magic_media_type": _magic_media_type(payload),
            "source_format": source_format,
            "source_mode": source_mode,
            "source_dimensions": source_dimensions,
            "source_frame_count": frame_count,
            "source_metadata": source_metadata,
            "source_resource_ids": resource_ids,
            "source_labels": source_labels,
            "counts": counts,
            "frame_row_set_fingerprint": _row_set_fingerprint(frame_rows),
            "ocr_row_set_fingerprint": _row_set_fingerprint(ocr_rows),
            "statement": NON_CLAIM,
        }
    )
    return artifact_row, frame_rows, ocr_rows


def _discover_ocr_with_fingerprint() -> _OcrRuntime:
    runtime = _discover_ocr_runtime()
    public = dict(runtime.public_binding)
    public["runtime_fingerprint"] = sha256_bytes(
        canonical_json_bytes(public)
    )
    return _OcrRuntime(
        tesseract_path=runtime.tesseract_path,
        sandbox_path=runtime.sandbox_path,
        tessdata_dir=runtime.tessdata_dir,
        public_binding=public,
    )


def _aggregate_counts(
    artifacts: Iterable[Mapping[str, Any]],
    frames: Iterable[Mapping[str, Any]],
    ocr_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    artifact_rows = list(artifacts)
    frame_rows = list(frames)
    observations = list(ocr_rows)
    media_counts = Counter(
        row["declared_media_type"] for row in artifact_rows
    )
    format_counts = Counter(row["source_format"] for row in artifact_rows)
    frame_kind_counts = Counter(
        row["locator"]["frame_kind"] for row in frame_rows
    )
    return {
        "declared_image_artifacts": len(artifact_rows),
        "parsed_image_artifacts": len(artifact_rows),
        "declared_media_type_counts": dict(sorted(media_counts.items())),
        "decoded_format_counts": dict(sorted(format_counts.items())),
        "frames": len(frame_rows),
        "frame_kind_counts": dict(sorted(frame_kind_counts.items())),
        "rendered_pixels": sum(
            row["render"]["pixel_count"] for row in frame_rows
        ),
        "ocr_observations": len(observations),
        "ocr_nonempty_observations": sum(
            row["text"] != "" for row in observations
        ),
        "ocr_empty_observations": sum(
            row["text"] == "" for row in observations
        ),
        "ocr_text_characters": sum(
            row["text_characters"] for row in observations
        ),
        "ocr_text_bytes": sum(
            row["text_bytes"] for row in observations
        ),
        "needs_ocr_frames": sum(
            row["ocr_status"] == "needs_ocr" for row in frame_rows
        ),
        "needs_visual_review_frames": sum(
            row["needs_visual_review"] for row in frame_rows
        ),
    }


def parse_verified_image_run(
    run_dir: Path,
    stage_dir: Path,
    *,
    expected_media_type_counts: Mapping[str, int],
    ocr_mode: str = "auto",
) -> dict[str, Any]:
    """Exhaustively inventory all sealed images and optionally run bound OCR."""

    if ocr_mode not in {"auto", "disabled", "required"}:
        _fail("ocr_mode must be auto, disabled, or required")
    expected_counts = {
        str(key): value for key, value in expected_media_type_counts.items()
    }
    if set(expected_counts) != set(IMAGE_MEDIA_TYPES) or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for value in expected_counts.values()
    ):
        _fail("expected image media-type denominator is invalid")
    if sum(expected_counts.values()) < 1:
        _fail("expected image denominator is empty")
    run_dir = Path(run_dir)
    stage_dir = Path(stage_dir)
    if stage_dir.exists():
        _fail("image stage directory already exists")
    try:
        acquisition = validate_acquisition_run(run_dir)
    except (AcquisitionLoadError, ContractError, OSError) as exc:
        raise ImageExtractionError(
            "sealed acquisition input failed verification"
        ) from exc
    artifacts = sorted(
        (
            row
            for row in acquisition.rows["raw-artifacts.jsonl"]
            if row.get("media_type") in IMAGE_MEDIA_TYPES
        ),
        key=lambda row: row["artifact_sha256"],
    )
    actual_counts = Counter(row["media_type"] for row in artifacts)
    normalized_actual_counts = {
        media_type: actual_counts.get(media_type, 0)
        for media_type in IMAGE_MEDIA_TYPES
    }
    if normalized_actual_counts != expected_counts:
        _fail("sealed image media-type denominator differs from expectation")
    sources = _artifact_sources(acquisition)
    if any(not sources.get(row["artifact_sha256"]) for row in artifacts):
        _fail("sealed image artifact has no source-resource link")

    pillow_binding = _pillow_binding()
    ocr_runtime: _OcrRuntime | None = None
    ocr_unavailable_reason: str | None = None
    if ocr_mode != "disabled":
        try:
            ocr_runtime = _discover_ocr_with_fingerprint()
        except ImageExtractionError as exc:
            if ocr_mode == "required":
                raise
            ocr_unavailable_reason = str(exc)
    parser_code_sha256 = file_sha256(Path(__file__))
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "parser_version": PARSER_VERSION,
                "parser_code_sha256": parser_code_sha256,
                "pillow_binding": pillow_binding,
                "ocr_mode": ocr_mode,
                "ocr_runtime": (
                    dict(ocr_runtime.public_binding)
                    if ocr_runtime is not None
                    else None
                ),
                "raw_manifest_sha256": acquisition.raw_manifest_sha256,
                "acquisition_run_id": acquisition.run_id,
                "acquisition_sealed_fingerprint": (
                    acquisition.sealed_fingerprint
                ),
                "expected_media_type_counts": dict(
                    sorted(expected_counts.items())
                ),
                "artifact_sha256s": [
                    row["artifact_sha256"] for row in artifacts
                ],
                "non_claim": NON_CLAIM,
            }
        )
    )
    extraction_id = sha256_bytes(
        canonical_json_bytes(
            ["image-extraction", input_fingerprint]
        )
    )

    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{stage_dir.name}.",
            dir=stage_dir.parent,
        )
    )
    try:
        render_root = temporary / "renders"
        render_root.mkdir()
        artifact_rows: list[dict[str, Any]] = []
        frame_rows: list[dict[str, Any]] = []
        ocr_rows: list[dict[str, Any]] = []
        for artifact in artifacts:
            path = resolve_run_relative(
                run_dir, artifact["content_path"]
            )
            payload = path.read_bytes()
            artifact_row, artifact_frames, artifact_ocr = _parse_artifact(
                extraction_id=extraction_id,
                artifact=artifact,
                payload=payload,
                source_rows=sources[artifact["artifact_sha256"]],
                render_root=render_root,
                ocr_runtime=ocr_runtime,
                scratch_parent=temporary,
            )
            artifact_rows.append(artifact_row)
            frame_rows.extend(artifact_frames)
            ocr_rows.extend(artifact_ocr)
        _write_jsonl(
            temporary / "image-artifacts.jsonl", artifact_rows
        )
        _write_jsonl(temporary / "image-frames.jsonl", frame_rows)
        _write_jsonl(temporary / "image-ocr.jsonl", ocr_rows)
        counts = _aggregate_counts(artifact_rows, frame_rows, ocr_rows)
        if counts["parsed_image_artifacts"] != sum(expected_counts.values()):
            _fail("image parser did not exhaust the sealed denominator")
        render_files = [
            {
                "filename": path.relative_to(temporary).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in sorted(
                render_root.rglob("*.png"),
                key=lambda item: item.relative_to(temporary).as_posix(),
            )
        ]
        output_files = [
            manifest_file_entry(temporary / filename)
            for filename in OUTPUT_JSONL_FILES
        ]
        row_sets = {
            "image_artifact": _row_set_fingerprint(artifact_rows),
            "image_frame": _row_set_fingerprint(frame_rows),
            "image_ocr": _row_set_fingerprint(ocr_rows),
        }
        output_fingerprint = sha256_bytes(
            canonical_json_bytes(
                {
                    "counts": counts,
                    "row_set_fingerprints": row_sets,
                    "output_files": output_files,
                    "render_files": render_files,
                }
            )
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "status": "passed",
            "extraction_id": extraction_id,
            "parser_version": PARSER_VERSION,
            "parser_code_sha256": parser_code_sha256,
            "pillow_binding": pillow_binding,
            "ocr_mode": ocr_mode,
            "ocr_runtime": (
                dict(ocr_runtime.public_binding)
                if ocr_runtime is not None
                else {
                    "status": "not_run",
                    "reason": (
                        "explicitly_disabled"
                        if ocr_mode == "disabled"
                        else ocr_unavailable_reason
                    ),
                }
            ),
            "acquisition_run_id": acquisition.run_id,
            "acquisition_sealed_fingerprint": (
                acquisition.sealed_fingerprint
            ),
            "raw_manifest_sha256": acquisition.raw_manifest_sha256,
            "expected_media_type_counts": dict(
                sorted(expected_counts.items())
            ),
            "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "counts": counts,
            "row_set_fingerprints": row_sets,
            "output_files": output_files,
            "render_files": render_files,
            "closure_claims": {
                "sealed_image_denominator_exhausted": (
                    counts["declared_image_artifacts"]
                    == counts["parsed_image_artifacts"]
                    == sum(expected_counts.values())
                ),
                "source_magic_hash_resources_labels_bound": True,
                "all_pages_frames_rendered": True,
                "rendered_pixel_hashes_preserved": True,
                "ocr_text_verified_by_human": False,
                "legal_semantics_inferred": False,
                "history_complete": False,
                "postgresql_written": False,
            },
            "statement": NON_CLAIM,
        }
        write_json(temporary / "image-manifest.json", manifest)
        os.replace(temporary, stage_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def _read_json_object(
    value: Path | Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        try:
            result = json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageExtractionError(
                f"{label} is missing or invalid JSON"
            ) from exc
    if not isinstance(result, dict):
        _fail(f"{label} is not a JSON object")
    return result


def _read_stage_rows(
    path: Path,
    *,
    schema: str,
    extraction_id: str,
    identity_key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise ImageExtractionError(
            "image stage output file is missing"
        ) from exc
    with stream:
        for line in stream:
            if not line.strip():
                _fail("image stage JSONL contains a blank row")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ImageExtractionError(
                    "image stage JSONL is invalid"
                ) from exc
            if (
                not isinstance(row, dict)
                or row.get("schema") != schema
                or row.get("extraction_id") != extraction_id
            ):
                _fail("image stage JSONL row contract mismatch")
            identity = row.get(identity_key)
            if (
                not isinstance(identity, str)
                or not identity
                or identity in identities
            ):
                _fail("image stage row identity is missing or duplicated")
            identities.add(identity)
            clean = dict(row)
            claimed = clean.pop("source_row_sha256", None)
            if claimed != sha256_bytes(canonical_json_bytes(clean)):
                _fail("image stage source row hash mismatch")
            rows.append(row)
    return rows


def verify_image_stage(stage_dir: Path) -> dict[str, Any]:
    """Freshly verify hashes, rows, renders, counts, and fingerprints."""

    stage_dir = Path(stage_dir)
    manifest = _read_json_object(
        stage_dir / "image-manifest.json",
        label="image manifest",
    )
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "passed"
    ):
        _fail("image manifest schema or status mismatch")
    extraction_id = manifest.get("extraction_id")
    if not isinstance(extraction_id, str) or not extraction_id:
        _fail("image manifest extraction_id is missing")
    by_filename = {
        row.get("filename"): row
        for row in manifest.get("output_files", [])
        if isinstance(row, Mapping)
    }
    if set(by_filename) != set(OUTPUT_JSONL_FILES):
        _fail("image manifest output file set mismatch")
    output_files: list[dict[str, Any]] = []
    for filename in OUTPUT_JSONL_FILES:
        path = stage_dir / filename
        entry = by_filename[filename]
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            _fail("image manifested output file changed")
        output_files.append(dict(entry))
    render_files: list[dict[str, Any]] = []
    for entry in manifest.get("render_files", []):
        if not isinstance(entry, Mapping):
            _fail("image render receipt is invalid")
        relative = entry.get("filename")
        if not isinstance(relative, str) or not relative.startswith("renders/"):
            _fail("image render receipt path is invalid")
        path = stage_dir / relative
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            _fail("image rendered PNG changed")
        render_files.append(dict(entry))
    if len({row["filename"] for row in render_files}) != len(render_files):
        _fail("image render receipt repeats a filename")
    artifact_rows = _read_stage_rows(
        stage_dir / "image-artifacts.jsonl",
        schema=ARTIFACT_SCHEMA,
        extraction_id=extraction_id,
        identity_key="artifact_sha256",
    )
    frame_rows = _read_stage_rows(
        stage_dir / "image-frames.jsonl",
        schema=FRAME_SCHEMA,
        extraction_id=extraction_id,
        identity_key="frame_id",
    )
    ocr_rows = _read_stage_rows(
        stage_dir / "image-ocr.jsonl",
        schema=OCR_SCHEMA,
        extraction_id=extraction_id,
        identity_key="ocr_observation_id",
    )
    artifacts = {row["artifact_sha256"]: row for row in artifact_rows}
    frames_by_artifact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    frames = {row["frame_id"]: row for row in frame_rows}
    ocr_by_artifact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    render_receipts = {row["filename"]: row for row in render_files}
    for frame in frame_rows:
        if frame["artifact_sha256"] not in artifacts:
            _fail("image frame references an unknown artifact")
        render = frame["render"]
        receipt = render_receipts.get(render["png_relative_path"])
        if (
            receipt is None
            or receipt["sha256"] != render["png_sha256"]
            or receipt["bytes"] != render["png_bytes"]
        ):
            _fail("image frame render receipt mismatch")
        with Image.open(stage_dir / render["png_relative_path"]) as image:
            image.load()
            if (
                image.mode != "RGB"
                or image.width != render["width"]
                or image.height != render["height"]
                or _pixel_sha256(image)
                != render["rendered_pixel_sha256"]
            ):
                _fail("image rendered pixel hash mismatch")
        frames_by_artifact[frame["artifact_sha256"]].append(frame)
    for observation in ocr_rows:
        frame = frames.get(observation["frame_id"])
        if frame is None or observation["artifact_sha256"] != frame[
            "artifact_sha256"
        ]:
            _fail("OCR observation references an unknown image frame")
        if (
            observation["text_sha256"]
            != sha256_bytes(observation["text"].encode("utf-8"))
            or observation["text_bytes"]
            != len(observation["text"].encode("utf-8"))
            or observation["text_characters"] != len(observation["text"])
        ):
            _fail("OCR text receipt mismatch")
        ocr_by_artifact[observation["artifact_sha256"]].append(
            observation
        )
    for artifact_sha256, artifact in artifacts.items():
        if artifact["frame_row_set_fingerprint"] != _row_set_fingerprint(
            frames_by_artifact[artifact_sha256]
        ):
            _fail("image artifact frame row-set fingerprint mismatch")
        if artifact["ocr_row_set_fingerprint"] != _row_set_fingerprint(
            ocr_by_artifact[artifact_sha256]
        ):
            _fail("image artifact OCR row-set fingerprint mismatch")
    counts = _aggregate_counts(artifact_rows, frame_rows, ocr_rows)
    if manifest.get("counts") != counts:
        _fail("image manifest counts mismatch")
    row_sets = {
        "image_artifact": _row_set_fingerprint(artifact_rows),
        "image_frame": _row_set_fingerprint(frame_rows),
        "image_ocr": _row_set_fingerprint(ocr_rows),
    }
    if manifest.get("row_set_fingerprints") != row_sets:
        _fail("image manifest row-set fingerprints mismatch")
    if len(render_files) != len(frame_rows):
        _fail("image render denominator differs from frame rows")
    parser_code_sha256 = file_sha256(Path(__file__))
    if manifest.get("parser_code_sha256") != parser_code_sha256:
        _fail("image parser code fingerprint mismatch")
    expected_counts = manifest.get("expected_media_type_counts")
    if not isinstance(expected_counts, Mapping):
        _fail("image manifest expected denominator is invalid")
    input_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "parser_version": PARSER_VERSION,
                "parser_code_sha256": parser_code_sha256,
                "pillow_binding": manifest["pillow_binding"],
                "ocr_mode": manifest["ocr_mode"],
                "ocr_runtime": (
                    manifest["ocr_runtime"]
                    if manifest["ocr_runtime"].get("status")
                    == "eligible_bound_local_no_network"
                    else None
                ),
                "raw_manifest_sha256": manifest["raw_manifest_sha256"],
                "acquisition_run_id": manifest["acquisition_run_id"],
                "acquisition_sealed_fingerprint": manifest[
                    "acquisition_sealed_fingerprint"
                ],
                "expected_media_type_counts": dict(
                    sorted(expected_counts.items())
                ),
                "artifact_sha256s": sorted(artifacts),
                "non_claim": NON_CLAIM,
            }
        )
    )
    expected_extraction_id = sha256_bytes(
        canonical_json_bytes(["image-extraction", input_fingerprint])
    )
    if (
        manifest.get("input_fingerprint") != input_fingerprint
        or extraction_id != expected_extraction_id
    ):
        _fail("image manifest input fingerprint mismatch")
    output_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "counts": counts,
                "row_set_fingerprints": row_sets,
                "output_files": output_files,
                "render_files": render_files,
            }
        )
    )
    if manifest.get("output_fingerprint") != output_fingerprint:
        _fail("image manifest output fingerprint mismatch")
    return {
        "manifest": manifest,
        "manifest_sha256": file_sha256(
            stage_dir / "image-manifest.json"
        ),
        "counts": counts,
        "output_files": output_files,
        "render_files": render_files,
        "row_set_fingerprints": row_sets,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
    }


def build_public_image_receipt(
    *,
    stage_dir: Path,
    historical_capture_receipt: Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Build a compact public receipt from a freshly verified image stage."""

    verified = verify_image_stage(stage_dir)
    manifest = verified["manifest"]
    capture = _read_json_object(
        historical_capture_receipt,
        label="historical capture receipt",
    )
    if capture.get("schema") != HISTORICAL_CAPTURE_RECEIPT_SCHEMA:
        _fail("historical capture receipt schema mismatch")
    accepted = capture.get("accepted_acquisition")
    scope = capture.get("scope")
    if not isinstance(accepted, Mapping) or not isinstance(scope, Mapping):
        _fail("historical receipt lacks sealed acquisition binding")
    if (
        accepted.get("run_id") != manifest["acquisition_run_id"]
        or accepted.get("state") != "sealed"
        or accepted.get("sealed_fingerprint")
        != manifest["acquisition_sealed_fingerprint"]
        or accepted.get("raw_manifest_sha256")
        != manifest["raw_manifest_sha256"]
    ):
        _fail("image stage differs from accepted sealed acquisition")
    media_counts = accepted.get("media_type_counts")
    if not isinstance(media_counts, Mapping) or any(
        media_counts.get(media_type)
        != manifest["expected_media_type_counts"][media_type]
        for media_type in IMAGE_MEDIA_TYPES
    ):
        _fail("historical receipt image denominator mismatch")
    counts = manifest["counts"]
    bound_ocr = (
        manifest["ocr_runtime"].get("status")
        == "eligible_bound_local_no_network"
    )
    receipt = {
        "schema": PUBLIC_RECEIPT_SCHEMA,
        "status": (
            "passed_source_render_and_bound_local_ocr"
            if bound_ocr
            else "passed_source_render_inventory_needs_ocr"
        ),
        "scope": {
            key: scope.get(key)
            for key in (
                "query_start",
                "query_end",
                "capture_cut",
                "query",
                "query_mode",
                "source_plan_sha256",
            )
        },
        "sealed_input": {
            "acquisition_run_id": manifest["acquisition_run_id"],
            "acquisition_sealed_fingerprint": manifest[
                "acquisition_sealed_fingerprint"
            ],
            "raw_manifest_sha256": manifest["raw_manifest_sha256"],
            "media_type_counts": manifest[
                "expected_media_type_counts"
            ],
            "image_artifact_denominator": sum(
                manifest["expected_media_type_counts"].values()
            ),
        },
        "render_extraction": {
            "extraction_id": manifest["extraction_id"],
            "parser_version": manifest["parser_version"],
            "parser_code_sha256": manifest["parser_code_sha256"],
            "pillow_binding": manifest["pillow_binding"],
            "input_fingerprint": manifest["input_fingerprint"],
            "output_fingerprint": manifest["output_fingerprint"],
            "manifest_sha256": verified["manifest_sha256"],
            "output_files": verified["output_files"],
            "render_file_count": len(verified["render_files"]),
            "render_file_set_fingerprint": sha256_bytes(
                canonical_json_bytes(verified["render_files"])
            ),
            "row_set_fingerprints": verified[
                "row_set_fingerprints"
            ],
        },
        "ocr": {
            "runtime": manifest["ocr_runtime"],
            "observations": counts["ocr_observations"],
            "nonempty_observations": counts[
                "ocr_nonempty_observations"
            ],
            "empty_observations": counts["ocr_empty_observations"],
            "text_characters": counts["ocr_text_characters"],
            "text_bytes": counts["ocr_text_bytes"],
            "human_verified_observations": 0,
            "status": (
                "unreviewed_candidates_only"
                if bound_ocr
                else "not_run_needs_ocr"
            ),
        },
        "review_denominator": {
            "needs_ocr_frames": counts["needs_ocr_frames"],
            "needs_visual_review_frames": counts[
                "needs_visual_review_frames"
            ],
            "total_frames": counts["frames"],
        },
        "counts": counts,
        "closure": manifest["closure_claims"],
        "claims": {
            "all_sealed_image_artifacts_inventory_complete": True,
            "all_pages_frames_rendered": True,
            "ocr_is_unreviewed_observation_only": True,
            "authoritative_text_extracted": False,
            "legal_rule_identity_resolved": False,
            "legal_effective_date_resolved": False,
            "amendment_effect_resolved": False,
            "history_complete": False,
            "postgresql_written": False,
        },
        "statement": NON_CLAIM,
    }
    assert_public_value(receipt)
    return receipt
