from __future__ import annotations

import csv
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history.nhi_drug_linkage import (
    DOWNLOAD_URL,
    EXPECTED_COLUMNS,
    LinkageSnapshotError,
    acquire_snapshot,
    inspect_csv,
)


class NhiDrugLinkageSnapshotTests(unittest.TestCase):
    class _Response(io.BytesIO):
        status = 200

        def __init__(
            self,
            payload: bytes,
            *,
            final_url: str = DOWNLOAD_URL,
        ) -> None:
            super().__init__(payload)
            self._final_url = final_url
            self.headers = {
                "Content-Type": "application/csv",
                "Content-Length": str(len(payload)),
                "Content-Disposition": "attachment; filename=items.csv",
            }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self.close()

        def geturl(self) -> str:
            return self._final_url

    def _write_rows(
        self,
        path: Path,
        rows: list[dict[str, str]],
        *,
        columns: tuple[str, ...] = EXPECTED_COLUMNS,
    ) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _row(self, **overrides: str) -> dict[str, str]:
        row = {column: "" for column in EXPECTED_COLUMNS}
        row.update(
            {
                "藥品代號": "AC58256100",
                "ATC代碼": "N06AB05",
                "給付規定章節": "1.2.1.",
                "給付規定章節連結": (
                    "https://info.nhi.gov.tw/api/INAE3000/"
                    "INAE3000S01/getPDF?DurgFileName=1.2.1._20180301_000.pdf"
                ),
            }
        )
        row.update(overrides)
        return row

    def _payload(self, rows: list[dict[str, str]]) -> bytes:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=EXPECTED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")

    def test_inspection_counts_direct_source_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "items.csv"
            self._write_rows(
                path,
                [
                    self._row(),
                    self._row(
                        藥品代號="AB12345678",
                        ATC代碼="C10AA05",
                        給付規定章節="",
                        給付規定章節連結="",
                    ),
                    self._row(
                        藥品代號="AC58256100",
                        ATC代碼="N06AB05",
                    ),
                ],
            )
            result = inspect_csv(path)

        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["distinct_drug_codes"], 2)
        self.assertEqual(result["rows_with_atc"], 3)
        self.assertEqual(result["distinct_atc_codes"], 2)
        self.assertEqual(result["rows_with_rule_section"], 2)
        self.assertEqual(result["rows_with_rule_url"], 2)

    def test_header_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "items.csv"
            drifted = EXPECTED_COLUMNS[:-1] + ("給付規定網址",)
            self._write_rows(path, [], columns=drifted)
            with self.assertRaisesRegex(LinkageSnapshotError, "header drift"):
                inspect_csv(path)

    def test_truncated_row_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "items.csv"
            path.write_text(
                ",".join(EXPECTED_COLUMNS) + "\n,AC58256100\n",
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(LinkageSnapshotError, "truncated"):
                inspect_csv(path)

    def test_unterminated_quoted_field_fails_closed(self) -> None:
        payload = self._payload(
            [self._row(給付規定章節連結="unterminated,field")]
        )
        self.assertTrue(payload.rstrip().endswith(b'"'))
        malformed = payload.rstrip()[:-1]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "items.csv"
            path.write_bytes(malformed)
            with self.assertRaisesRegex(LinkageSnapshotError, "malformed"):
                inspect_csv(path)

    def test_empty_or_missing_drug_code_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            empty_path = Path(temp) / "empty.csv"
            self._write_rows(empty_path, [])
            with self.assertRaisesRegex(LinkageSnapshotError, "zero data rows"):
                inspect_csv(empty_path)

            missing_path = Path(temp) / "missing.csv"
            self._write_rows(missing_path, [self._row(藥品代號="")])
            with self.assertRaisesRegex(
                LinkageSnapshotError,
                "without NHI drug code",
            ):
                inspect_csv(missing_path)

    def test_acquisition_is_content_addressed_and_idempotent(self) -> None:
        payload = self._payload([self._row()])
        retrieved_at = dt.datetime(2026, 7, 27, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch(
                "nhi_rule_history.nhi_drug_linkage.urllib.request.urlopen",
                side_effect=lambda *args, **kwargs: self._Response(payload),
            ):
                first = acquire_snapshot(
                    output_dir=Path(temp),
                    retrieved_at=retrieved_at,
                )
                second = acquire_snapshot(
                    output_dir=Path(temp),
                    retrieved_at=retrieved_at,
                )
            manifest = json.loads(
                Path(first["manifest_path"]).read_text(encoding="utf-8")
            )

        self.assertEqual(first, second)
        self.assertEqual(manifest["download_url"], DOWNLOAD_URL)
        self.assertEqual(
            manifest["transport"]["tls_verification"],
            "system_default",
        )
        self.assertEqual(manifest["inspection"]["row_count"], 1)
        self.assertEqual(manifest["legal_semantics"]["product_to_atc"], (
            "official_source_assertion"
        ))

    def test_nonofficial_url_is_rejected_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            urls = (
                "https://example.invalid/items.csv",
                (
                    "https://user@info.nhi.gov.tw/api/iode0000s01/"
                    f"Dataset?rId={DOWNLOAD_URL.rsplit('=', 1)[-1]}"
                ),
                DOWNLOAD_URL + "&extra=1",
                DOWNLOAD_URL + "#fragment",
                DOWNLOAD_URL.replace("info.nhi.gov.tw", "info.nhi.gov.tw:444"),
            )
            for url in urls:
                with self.subTest(url=url):
                    with self.assertRaisesRegex(
                        LinkageSnapshotError,
                        "declared official",
                    ):
                        acquire_snapshot(output_dir=Path(temp), url=url)

    def test_foreign_redirect_target_is_rejected(self) -> None:
        payload = self._payload([self._row()])
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch(
                "nhi_rule_history.nhi_drug_linkage.urllib.request.urlopen",
                return_value=self._Response(
                    payload,
                    final_url="https://example.invalid/items.csv",
                ),
            ):
                with self.assertRaisesRegex(
                    LinkageSnapshotError,
                    "declared official",
                ):
                    acquire_snapshot(output_dir=Path(temp))

    def test_existing_artifact_hash_drift_is_rejected(self) -> None:
        payload = self._payload([self._row()])
        retrieved_at = dt.datetime(2026, 7, 27, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with mock.patch(
                "nhi_rule_history.nhi_drug_linkage.urllib.request.urlopen",
                side_effect=lambda *args, **kwargs: self._Response(payload),
            ):
                first = acquire_snapshot(
                    output_dir=output,
                    retrieved_at=retrieved_at,
                )
                artifact = Path(first["artifact_path"])
                corrupted = bytearray(artifact.read_bytes())
                corrupted[-1] = (corrupted[-1] + 1) % 256
                artifact.write_bytes(corrupted)
                with self.assertRaisesRegex(
                    LinkageSnapshotError,
                    "does not match its identity",
                ):
                    acquire_snapshot(
                        output_dir=output,
                        retrieved_at=retrieved_at,
                    )


if __name__ == "__main__":
    unittest.main()
