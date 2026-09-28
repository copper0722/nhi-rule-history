"""LibreOffice's own reading of the paragraphs in an ODT's table cells.

The official-rendering gate of the announced overlay compares each clause
with the rendering of its own revised cell.  A plain-text export cannot serve
that purpose: it prints the whole document, so a paragraph can be found in
another cell, and it prints every list label followed by one space whatever
gap the document draws.  This module opens the document in LibreOffice and
reads, through UNO, each paragraph of each cell of each top-level table: its
text, the list label LibreOffice draws before it (``ListLabelString``) and
what follows that label (the list level's ``LabelFollowedBy``: tab, space,
nothing or a line break).

The UNO client runs in a child process with an interpreter that can import
``uno``, against a private office process with a throwaway profile, so it
never attaches to a running office suite; its whole process group is killed
when it overruns the time limit.  Nothing here writes to the documents.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


CELL_RENDERING_VERSION = "nhi-rule-history/office-cell-rendering/1.0.0"
# com.sun.star.text.LabelFollow, for the label-alignment position mode.
_LABEL_FOLLOWED_BY = {0: "\t", 1: " ", 2: "", 3: "\n"}
# com.sun.star.text.PositionAndSpaceMode.LABEL_ALIGNMENT; in the older
# label-width mode the gap is a position, not a character.
_LABEL_ALIGNMENT = 1
_CONNECT_SECONDS = 60
UNO_PYTHON_ENV = "NHI_RULE_HISTORY_UNO_PYTHON"


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(name, None)
    return env


@functools.lru_cache(maxsize=None)
def uno_python(soffice: str) -> str | None:
    """An interpreter that can import LibreOffice's ``uno`` bridge, if any.

    Tried in order: ``NHI_RULE_HISTORY_UNO_PYTHON``, the Python bundled with
    the office suite, this interpreter, and ``python3`` on the path.
    """

    candidates = (
        os.environ.get(UNO_PYTHON_ENV),
        str(Path(os.path.realpath(soffice)).with_name("python")),
        sys.executable,
        shutil.which("python3"),
    )
    for candidate in candidates:
        if not candidate or not os.access(candidate, os.X_OK):
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "import uno"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=_child_env(),
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()


def render_table_cells(
    payloads: Mapping[str, bytes],
    *,
    soffice: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, dict[str, Any] | None]:
    """LibreOffice's cell paragraphs of each ODT, or ``None`` per document.

    A rendering is ``{"rendering_version", "tables"}``: one entry per
    top-level body table in document order, each a list of rows, each a list
    of cells by column position; a cell is a list of paragraphs
    ``{"text", "label", "separator"}`` (``separator`` is ``None`` when there
    is no label or its gap is not a character) and ``{"nested_table": true}``
    for a table inside the cell, or ``None`` when the office suite cannot
    address it.  A document is ``None`` when LibreOffice or its Python bridge
    is unavailable, fails, or overruns ``timeout_seconds``.
    """

    result: dict[str, dict[str, Any] | None] = {key: None for key in payloads}
    binary = soffice or shutil.which("soffice")
    if not payloads or not binary:
        return result
    python = uno_python(binary)
    if python is None:
        return result
    with tempfile.TemporaryDirectory(prefix="nhi-office-cells-") as scratch:
        work = Path(scratch)
        documents: dict[str, str] = {}
        for index, key in enumerate(sorted(payloads)):
            path = work / f"document-{index:04d}.odt"
            path.write_bytes(payloads[key])
            documents[key] = str(path)
        output = work / "output.json"
        request = work / "request.json"
        request.write_text(
            json.dumps(
                {
                    "soffice": binary,
                    "documents": documents,
                    "output": str(output),
                    "profile": (work / "profile").as_uri(),
                }
            ),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [python, str(Path(__file__).resolve()), str(request)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            pass
        finally:
            _kill_group(process)
        if process.returncode != 0 or not output.is_file():
            return result
        rendered = json.loads(output.read_text(encoding="utf-8"))
    for key, value in rendered.get("documents", {}).items():
        if key in result and isinstance(value, dict) and isinstance(
            value.get("tables"), list
        ):
            result[key] = {
                "rendering_version": CELL_RENDERING_VERSION,
                "tables": value["tables"],
            }
    return result


# ---------------------------------------------------------------------------
# Child process: runs under an interpreter with LibreOffice's ``uno`` bridge.


def _property(name: str, value: Any) -> Any:
    from com.sun.star.beans import PropertyValue

    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def _separator(paragraph: Any) -> str | None:
    rules = paragraph.getPropertyValue("NumberingRules")
    if rules is None:
        return None
    level = paragraph.getPropertyValue("NumberingLevel")
    values = {item.Name: item.Value for item in rules.getByIndex(level)}
    if values.get("PositionAndSpaceMode") != _LABEL_ALIGNMENT:
        return None
    return _LABEL_FOLLOWED_BY.get(values.get("LabelFollowedBy"))


def _paragraphs(text: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    enumeration = text.createEnumeration()
    while enumeration.hasMoreElements():
        element = enumeration.nextElement()
        if element.supportsService("com.sun.star.text.TextTable"):
            found.append({"nested_table": True})
            continue
        label = element.getPropertyValue("ListLabelString") or ""
        found.append(
            {
                "text": element.getString(),
                "label": label,
                "separator": _separator(element) if label else None,
            }
        )
    return found


def _tables(document: Any) -> list[list[list[Any]]]:
    tables: list[list[list[Any]]] = []
    enumeration = document.getText().createEnumeration()
    while enumeration.hasMoreElements():
        element = enumeration.nextElement()
        if not element.supportsService("com.sun.star.text.TextTable"):
            continue
        grid: list[list[Any]] = []
        for row in range(element.getRows().getCount()):
            cells: list[Any] = []
            for column in range(element.getColumns().getCount()):
                try:
                    cell = element.getCellByPosition(column, row)
                except Exception:  # an irregular table has no such cell
                    cell = None
                cells.append(None if cell is None else _paragraphs(cell.getText()))
            grid.append(cells)
        tables.append(grid)
    return tables


def _child(request_path: str) -> int:
    import uno
    from com.sun.star.connection import NoConnectException

    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    pipe = f"nhi_office_cells_{uuid.uuid4().hex}"
    office = subprocess.Popen(
        [
            request["soffice"],
            f"-env:UserInstallation={request['profile']}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nologo",
            "--nodefault",
            f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    desktop = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local
        )
        deadline = time.monotonic() + _CONNECT_SECONDS
        while True:
            try:
                context = resolver.resolve(
                    f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext"
                )
                break
            except NoConnectException:
                if time.monotonic() > deadline or office.poll() is not None:
                    return 2
                time.sleep(0.2)
        desktop = context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context
        )
        rendered: dict[str, Any] = {}
        for key, path in sorted(request["documents"].items()):
            document = desktop.loadComponentFromURL(
                Path(path).as_uri(),
                "_blank",
                0,
                (
                    _property("Hidden", True),
                    _property("ReadOnly", True),
                    _property("MacroExecutionMode", 0),
                    _property("UpdateDocMode", 0),
                ),
            )
            if document is None:
                rendered[key] = {"error": "the document did not load"}
                continue
            try:
                rendered[key] = {"tables": _tables(document)}
            finally:
                document.close(True)
        Path(request["output"]).write_text(
            json.dumps({"documents": rendered}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 0
    finally:
        if desktop is not None:
            try:
                desktop.terminate()
            except Exception:  # the bridge closes as the office exits
                pass
        try:
            office.wait(timeout=30)
        except subprocess.TimeoutExpired:
            office.kill()
            office.wait()


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1]))
