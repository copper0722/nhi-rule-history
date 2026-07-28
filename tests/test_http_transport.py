from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.fetch.http import HttpClient


URL = "https://www.nhi.gov.tw/ch/cp-anchor.html"


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


class NhiHttpTransportTests(unittest.TestCase):
    def test_prime_redirect_and_cookie_backed_https_are_both_verified(
        self,
    ) -> None:
        calls: list[list[str]] = []

        def run(argv: list[str], **_: object) -> subprocess.CompletedProcess:
            calls.append(argv)
            if option(argv, "--proto") == "=http":
                self.assertEqual(
                    option(argv, "--url"),
                    "http://www.nhi.gov.tw/ch/cp-anchor.html",
                )
                return subprocess.CompletedProcess(
                    argv, 0, f"301\n{URL}", ""
                )
            Path(option(argv, "--output")).write_bytes(b"official")
            Path(option(argv, "--dump-header")).write_text(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Content-Length: 8\r\n\r\n",
                encoding="iso-8859-1",
            )
            return subprocess.CompletedProcess(
                argv, 0, f"200\n\n{URL}", ""
            )

        with (
            mock.patch(
                "nhi_rule_history.fetch.http.shutil.which",
                return_value="/usr/bin/curl",
            ),
            mock.patch(
                "nhi_rule_history.fetch.http.subprocess.run",
                side_effect=run,
            ),
        ):
            response = HttpClient(("www.nhi.gov.tw",)).get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"official")
        self.assertEqual(
            response.headers["content-type"],
            "text/html; charset=utf-8",
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("--cookie-jar", calls[0])
        self.assertIn("--cookie", calls[1])
        self.assertEqual(option(calls[1], "--url"), URL)

    def test_prime_must_redirect_to_the_exact_requested_https_url(
        self,
    ) -> None:
        with (
            mock.patch(
                "nhi_rule_history.fetch.http.shutil.which",
                return_value="/usr/bin/curl",
            ),
            mock.patch(
                "nhi_rule_history.fetch.http.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    "301\nhttps://www.nhi.gov.tw/ch/different.html",
                    "",
                ),
            ),
        ):
            with self.assertRaisesRegex(
                ContractError, "different HTTPS URL"
            ):
                HttpClient(("www.nhi.gov.tw",)).get(URL)


if __name__ == "__main__":
    unittest.main()
