"""LibreOffice cell rendering: one bad document leaves the others rendered."""

from __future__ import annotations

import os
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from nhi_rule_history import office_rendering
from nhi_rule_history.office_rendering import render_table_cells, uno_python
from tests.test_announced_notice import _comparison
from tools.build_announced_notice_fixture import fixture_odt


def _office_available() -> bool:
    binary = shutil.which("soffice")
    return bool(binary and uno_python(binary))


# The real child, except that loading the document whose file name contains
# the marker in NHI_TEST_BAD_DOCUMENT hangs or kills the child.
_WRAPPER = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {src!r})
    from nhi_rule_history import office_rendering as office
    original = office._tables
    def tables(document):
        marker, mode = os.environ["NHI_TEST_BAD_DOCUMENT"].split(":")
        if marker in document.getURL():
            if mode == "hang":
                time.sleep(600)
            os._exit(9)
        return original(document)
    office._tables = tables
    sys.exit(office._child(sys.argv[1]))
    """
)


@unittest.skipUnless(_office_available(), "LibreOffice/UNO is unavailable")
class DocumentIsolationTest(unittest.TestCase):
    """2026-09-28 finding LOW: one crash or hang lost every rendering."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.wrapper = Path(self.scratch.name) / "child.py"
        self.wrapper.write_text(
            _WRAPPER.format(src=str(Path(office_rendering.__file__).parents[1])),
            encoding="utf-8",
        )
        self.payloads = {
            key: fixture_odt(
                _comparison([([f"9.{index}.Foo：(115/10/1)", "限用於"], ["無"])])
            )
            for index, key in enumerate(("a", "b", "c"), start=2)
        }

    def tearDown(self) -> None:
        self.scratch.cleanup()

    def _render(self, mode: str) -> tuple[dict, float]:
        # Documents are written in key order: b is document-0001.
        env = {"NHI_TEST_BAD_DOCUMENT": f"document-0001:{mode}"}
        start = time.monotonic()
        with mock.patch.object(office_rendering, "_CHILD", self.wrapper), \
                mock.patch.dict(os.environ, env):
            result = render_table_cells(self.payloads, document_seconds=5)
        return result, time.monotonic() - start

    def test_a_hanging_document_is_the_only_one_lost(self) -> None:
        result, seconds = self._render("hang")
        self.assertEqual(
            {key: value is not None for key, value in result.items()},
            {"a": True, "b": False, "c": True},
        )
        self.assertLess(seconds, 60)

    def test_a_crashing_document_is_the_only_one_lost(self) -> None:
        result, _ = self._render("crash")
        self.assertEqual(
            {key: value is not None for key, value in result.items()},
            {"a": True, "b": False, "c": True},
        )

    def test_every_document_renders_without_a_bad_one(self) -> None:
        # Confusable negative: the wrapper itself changes nothing.
        with mock.patch.object(office_rendering, "_CHILD", self.wrapper), \
                mock.patch.dict(os.environ, {"NHI_TEST_BAD_DOCUMENT": "none:hang"}):
            result = render_table_cells(self.payloads, document_seconds=5)
        self.assertEqual(sorted(key for key, value in result.items() if value), ["a", "b", "c"])
        self.assertEqual(result, render_table_cells(self.payloads))

    def test_a_renderer_that_never_starts_renders_nothing(self) -> None:
        result = render_table_cells(self.payloads, soffice="/bin/false")
        self.assertEqual(result, {"a": None, "b": None, "c": None})


if __name__ == "__main__":
    unittest.main()
