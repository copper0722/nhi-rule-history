from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nhi_rule_history.contracts import (
    ContractError,
    PLAN_SCHEMA,
    SourcePlan,
    canonical_json_bytes,
    file_sha256,
    relative_blob_path,
    stable_id,
)
from nhi_rule_history.update.historical_bundle import (
    BATCH_SCHEMA,
    BUNDLE_SCHEMA,
    NON_CLAIM_STATEMENT,
    materialize_historical_notice_bundles,
    verify_historical_bundle_batch,
    verify_historical_notice_bundle,
)


ADAPTER_ID = "historical-fixture"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def _source_plan(path: Path) -> Path:
    document = {
        "schema": PLAN_SCHEMA,
        "capture_cut": "2026-07-27",
        "allowed_hosts": ["mohwlaw.mohw.gov.tw"],
        "adapters": [
            {
                "id": ADAPTER_ID,
                "kind": "mohw_fint",
                "enabled": True,
                "base_url": (
                    "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx"
                ),
                "start_date": "1999-01-01",
                "end_date": "2020-12-31",
                "partition_months": 12,
                "queries": [
                    {"id": "exact", "keywords": ["藥品給付規定"]}
                ],
            }
        ],
    }
    path.write_bytes(canonical_json_bytes(document))
    return path


def _resource(
    *,
    resource_id: str,
    kind: str,
    document_number: str,
    source_url: str,
    label: str,
    parent_resource_id: str | None = None,
    ordinal: int | None = None,
) -> dict[str, Any]:
    locator: dict[str, Any] = {
        "query_id": "exact",
        "partition_id": "2000-01-01__2000-12-31",
        "row_number": 1,
    }
    if ordinal is not None:
        locator["attachment_ordinal"] = ordinal
    row: dict[str, Any] = {
        "schema": "nhi-rule-history/discovered-resource/v2",
        "resource_id": resource_id,
        "adapter_id": ADAPTER_ID,
        "resource_kind": kind,
        "source_url": source_url,
        "discovery_locator": locator,
        "source_label": label,
        "official_document_number_raw": document_number,
        "fetch_state": (
            "cached_by_discovery"
            if kind == "official_detail_page"
            else "pending"
        ),
    }
    if parent_resource_id is not None:
        row["parent_resource_id"] = parent_resource_id
    return row


def _add_artifact(
    run_dir: Path,
    *,
    resource: dict[str, Any],
    payload: bytes,
    media_type: str,
    artifacts: list[dict[str, Any]],
    links: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> None:
    digest = _sha(payload)
    relative = relative_blob_path(digest)
    path = run_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
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
    resource_id = resource["resource_id"]
    links.append(
        {
            "schema": "nhi-rule-history/resource-artifact-link/v2",
            "link_id": stable_id("link", resource_id, digest),
            "resource_id": resource_id,
            "artifact_sha256": digest,
            "relation": "retrieved_representation",
            "observed_at": "2026-07-27T00:00:00+00:00",
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


def _reseal_raw_manifest(run_dir: Path) -> None:
    manifest_path = run_dir / "raw-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt_names = [row["filename"] for row in manifest["files"]]
    manifest["files"] = [
        {
            "filename": filename,
            "bytes": (run_dir / filename).stat().st_size,
            "sha256": file_sha256(run_dir / filename),
        }
        for filename in receipt_names
    ]
    resources = [
        json.loads(line)
        for line in (run_dir / "discovered-resources.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    artifacts = [
        json.loads(line)
        for line in (run_dir / "raw-artifacts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    links = [
        json.loads(line)
        for line in (run_dir / "resource-artifact-links.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    manifest["counts"] = {
        "resources": len(resources),
        "artifacts": len(artifacts),
        "resource_artifact_links": len(links),
        "artifact_bytes": sum(row["byte_size"] for row in artifacts),
    }
    manifest_path.write_bytes(canonical_json_bytes(manifest))


def _fixture_run(root: Path) -> tuple[Path, Path, dict[str, bytes]]:
    run_dir = root / "run"
    run_dir.mkdir()
    plan_path = _source_plan(root / "source-plan.json")
    resources: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    payload_by_resource: dict[str, bytes] = {}

    specs = [
        ("健保醫字第00000001號", []),
        (
            "健保醫字第00000002號",
            [
                (
                    "條文.ODT",
                    b"PK\x03\x04odt-fixture",
                    "application/vnd.oasis.opendocument.text",
                ),
                ("對照表.PDF", b"%PDF-1.7\nodt-pdf", "application/pdf"),
            ],
        ),
        (
            "健保醫字第00000003號",
            [("只有PDF.PDF", b"%PDF-1.7\npdf-only", "application/pdf")],
        ),
        (
            "健保醫字第00000004號",
            [("掃描圖.JPG", b"\xff\xd8\xffimage-only", "image/jpeg")],
        ),
        (
            "健保醫字第00000005號",
            [
                (
                    "舊式文件.DOC",
                    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1ole",
                    "application/x-ole-storage",
                ),
                (
                    "表格.ODS",
                    b"PK\x03\x04ods-fixture",
                    "application/vnd.oasis.opendocument.spreadsheet",
                ),
                ("圖示.GIF", b"GIF89aimage", "image/gif"),
            ],
        ),
    ]
    for document_index, (document_number, attachment_specs) in enumerate(
        specs, 1
    ):
        detail_id = _sha(f"detail:{document_number}".encode())
        detail_url = (
            "https://mohwlaw.mohw.gov.tw/FINT/FINTQRY04.aspx?"
            f"RowNo={document_index}"
        )
        detail = _resource(
            resource_id=detail_id,
            kind="official_detail_page",
            document_number=document_number,
            source_url=detail_url,
            label=f"{document_number} detail",
        )
        resources.append(detail)
        detail_payload = (
            f"<html><body>{document_number}</body></html>"
        ).encode()
        payload_by_resource[detail_id] = detail_payload
        _add_artifact(
            run_dir,
            resource=detail,
            payload=detail_payload,
            media_type="text/html",
            artifacts=artifacts,
            links=links,
            attempts=attempts,
            observations=observations,
        )
        for ordinal, (label, payload, media_type) in enumerate(
            attachment_specs, 1
        ):
            resource_id = _sha(
                f"attachment:{document_number}:{ordinal}".encode()
            )
            attachment = _resource(
                resource_id=resource_id,
                kind="official_attachment",
                document_number=document_number,
                source_url=(
                    "https://mohwlaw.mohw.gov.tw/Flaw/GetFile.ashx?"
                    f"PFID={document_index:04d}{ordinal:02d}"
                ),
                label=label,
                parent_resource_id=detail_id,
                ordinal=ordinal,
            )
            resources.append(attachment)
            payload_by_resource[resource_id] = payload
            _add_artifact(
                run_dir,
                resource=attachment,
                payload=payload,
                media_type=media_type,
                artifacts=artifacts,
                links=links,
                attempts=attempts,
                observations=observations,
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
                "source_plan_sha256": SourcePlan.load(plan_path).sha256,
            }
        )
    )
    manifest_files = [
        *rows_by_name,
        "discovery-manifest.json",
    ]
    raw_manifest = {
        "schema": "nhi-rule-history/raw-manifest/v2",
        "status": "success",
        "capture_cut": "2026-07-27",
        "source_plan_schema": PLAN_SCHEMA,
        "source_plan_sha256": SourcePlan.load(plan_path).sha256,
        "counts": {
            "resources": len(resources),
            "artifacts": len(artifacts),
            "resource_artifact_links": len(links),
            "artifact_bytes": sum(row["byte_size"] for row in artifacts),
        },
        "files": [
            {
                "filename": filename,
                "bytes": (run_dir / filename).stat().st_size,
                "sha256": file_sha256(run_dir / filename),
            }
            for filename in manifest_files
        ],
    }
    (run_dir / "raw-manifest.json").write_bytes(
        canonical_json_bytes(raw_manifest)
    )
    return run_dir, plan_path, payload_by_resource


def _output_manifests(output_root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output_root / "bundles").glob("*/manifest.json"))
    ]


class HistoricalBundleTests(unittest.TestCase):
    def test_all_media_and_zero_attachment_notices_materialize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, payloads = _fixture_run(root)
            output = root / "output"
            result = materialize_historical_notice_bundles(
                run_dir,
                source_plan=plan_path,
                output_root=output,
            )
            self.assertFalse(result.replayed)
            self.assertEqual(result.document_count, 5)
            self.assertEqual(result.attachment_count, 7)

            batch = json.loads(
                (output / "batch-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(batch["schema"], BATCH_SCHEMA)
            self.assertEqual(
                batch["denominator"],
                {
                    "official_documents": 5,
                    "detail_resources": 5,
                    "declared_attachments": 7,
                    "materialized_bundles": 5,
                    "materialized_resources": 12,
                    "materialized_artifact_bytes": sum(
                        len(payload) for payload in payloads.values()
                    ),
                    "terminal_status_counts": {
                        "materialized_verified": 5
                    },
                },
            )
            self.assertEqual(
                {row["terminal_status"] for row in batch["documents"]},
                {"materialized_verified"},
            )
            self.assertEqual(batch["non_claim_statement"], NON_CLAIM_STATEMENT)
            self.assertEqual(
                verify_historical_bundle_batch(output)["status"], "passed"
            )

            manifests = _output_manifests(output)
            self.assertTrue(
                all(row["schema"] == BUNDLE_SCHEMA for row in manifests)
            )
            by_document = {
                row["official_document_number_normalized"]: row
                for row in manifests
            }
            self.assertEqual(
                by_document["健保醫字第00000001號"][
                    "declared_attachment_count"
                ],
                0,
            )
            self.assertEqual(
                [
                    row["media_type"]
                    for row in by_document["健保醫字第00000002號"][
                        "attachments"
                    ]
                ],
                [
                    "application/vnd.oasis.opendocument.text",
                    "application/pdf",
                ],
            )
            self.assertEqual(
                [
                    row["media_type"]
                    for row in by_document["健保醫字第00000003號"][
                        "attachments"
                    ]
                ],
                ["application/pdf"],
            )
            self.assertEqual(
                [
                    row["media_type"]
                    for row in by_document["健保醫字第00000004號"][
                        "attachments"
                    ]
                ],
                ["image/jpeg"],
            )
            self.assertEqual(
                [
                    row["media_type"]
                    for row in by_document["健保醫字第00000005號"][
                        "attachments"
                    ]
                ],
                [
                    "application/x-ole-storage",
                    "application/vnd.oasis.opendocument.spreadsheet",
                    "image/gif",
                ],
            )
            self.assertEqual(
                [
                    row["declared_attachment_ordinal"]
                    for row in by_document["健保醫字第00000005號"][
                        "attachments"
                    ]
                ],
                [1, 2, 3],
            )
            for manifest in manifests:
                checked = verify_historical_notice_bundle(
                    output / "bundles" / manifest["bundle_id"]
                )
                self.assertEqual(checked["status"], "passed")

    def test_deterministic_replay_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            output = root / "output"
            first = materialize_historical_notice_bundles(
                run_dir,
                source_plan=plan_path,
                output_root=output,
            )
            before = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            second = materialize_historical_notice_bundles(
                run_dir,
                source_plan=plan_path,
                output_root=output,
            )
            after = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertTrue(second.replayed)
            self.assertEqual(first.batch_id, second.batch_id)
            self.assertEqual(
                first.batch_fingerprint, second.batch_fingerprint
            )
            self.assertEqual(before, after)

    def test_missing_resource_artifact_link_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            link_path = run_dir / "resource-artifact-links.jsonl"
            rows = [
                json.loads(line)
                for line in link_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            _write_jsonl(link_path, rows[:-1])
            _reseal_raw_manifest(run_dir)
            with self.assertRaisesRegex(
                ContractError, "resources without raw artifacts"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_raw_blob_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            artifact = json.loads(
                (run_dir / "raw-artifacts.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            (run_dir / artifact["content_path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(
                ContractError, "raw blob verification failed"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_duplicate_attachment_ordinal_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            resource_path = run_dir / "discovered-resources.jsonl"
            rows = [
                json.loads(line)
                for line in resource_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            target_parent = next(
                row["resource_id"]
                for row in rows
                if row["official_document_number_raw"]
                == "健保醫字第00000002號"
                and row["resource_kind"] == "official_detail_page"
            )
            children = [
                row
                for row in rows
                if row.get("parent_resource_id") == target_parent
            ]
            children[1]["discovery_locator"]["attachment_ordinal"] = 1
            _write_jsonl(resource_path, rows)
            _reseal_raw_manifest(run_dir)
            with self.assertRaisesRegex(
                ContractError, "ordinals are duplicated"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_conflicting_attachment_document_number_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            resource_path = run_dir / "discovered-resources.jsonl"
            rows = [
                json.loads(line)
                for line in resource_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            attachment = next(
                row
                for row in rows
                if row["resource_kind"] == "official_attachment"
            )
            attachment["official_document_number_raw"] = "健保醫字第衝突號"
            _write_jsonl(resource_path, rows)
            _reseal_raw_manifest(run_dir)
            with self.assertRaisesRegex(
                ContractError, "conflicting formal document numbers"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_source_plan_hash_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            changed = json.loads(plan_path.read_text(encoding="utf-8"))
            changed["adapters"][0]["queries"][0]["keywords"] = ["不同查詢"]
            plan_path.write_bytes(canonical_json_bytes(changed))
            with self.assertRaisesRegex(
                ContractError, "not bound to the supplied source plan"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_raw_artifact_path_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            artifact_path = run_dir / "raw-artifacts.jsonl"
            rows = [
                json.loads(line)
                for line in artifact_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            rows[0]["content_path"] = "../outside"
            _write_jsonl(artifact_path, rows)
            _reseal_raw_manifest(run_dir)
            with self.assertRaisesRegex(
                ContractError, "escapes run directory"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )

    def test_resource_reuse_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, plan_path, _payloads = _fixture_run(root)
            resource_path = run_dir / "discovered-resources.jsonl"
            rows = [
                json.loads(line)
                for line in resource_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]
            attachments = [
                row
                for row in rows
                if row["resource_kind"] == "official_attachment"
            ]
            attachments[-1]["resource_id"] = attachments[0]["resource_id"]
            _write_jsonl(resource_path, rows)
            _reseal_raw_manifest(run_dir)
            with self.assertRaisesRegex(
                ContractError, "conflicting duplicate resource_id"
            ):
                materialize_historical_notice_bundles(
                    run_dir,
                    source_plan=plan_path,
                    output_root=root / "output",
                )


if __name__ == "__main__":
    unittest.main()
