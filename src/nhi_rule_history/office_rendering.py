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
never attaches to a running office suite.  It reports each document as it
finishes; a document that crashes or stalls the office process is left
unrendered, its process group is killed and the other documents get a fresh
process.  Nothing here writes to the documents.
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


CELL_RENDERING_VERSION = "nhi-rule-history/office-cell-rendering/1.1.0"
# com.sun.star.text.LabelFollow, for the label-alignment position mode.
_LABEL_FOLLOWED_BY = {0: "\t", 1: " ", 2: "", 3: "\n"}
# com.sun.star.text.PositionAndSpaceMode.LABEL_ALIGNMENT; in the older
# label-width mode the gap is a position, not a character.
_LABEL_ALIGNMENT = 1
# com.sun.star.style.NumberingType values that draw no text label.
_NUMBERING_BULLET = {6: "bullet", 8: "image"}
# Fields whose text depends on a condition or is hidden.
_HIDING_FIELDS = (
    "com.sun.star.text.TextField.HiddenText",
    "com.sun.star.text.TextField.HiddenParagraph",
    "com.sun.star.text.TextField.ConditionalText",
)
_CONNECT_SECONDS = 60
# The child process runs this file as a script.
_CHILD = Path(__file__).resolve()
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


def _progress(output: Path) -> tuple[dict[str, Any], str | None, int]:
    """Finished documents, the one being loaded, and the line count."""

    finished: dict[str, Any] = {}
    loading: str | None = None
    count = 0
    if not output.is_file():
        return finished, loading, count
    for line in output.read_bytes().split(b"\n"):
        try:
            entry = json.loads(line.decode("utf-8"))
        except ValueError:  # includes UnicodeDecodeError
            continue  # a line cut short by a crash
        count += 1
        if "loading" in entry:
            loading = entry["loading"]
        elif "key" in entry:
            finished[entry["key"]] = entry
            loading = None
    return finished, loading, count


def _attempt(
    python: str,
    binary: str,
    documents: list[tuple[str, str]],
    work: Path,
    attempt: int,
    *,
    deadline: float,
    document_seconds: float,
) -> tuple[dict[str, Any], str | None, bool]:
    """One office process over ``documents``; returns what it finished.

    The process group is killed when no document finishes or starts for
    ``document_seconds``, or at ``deadline``.  The second value names the
    document it was loading when it stopped, if any; the third says whether
    it ended by itself with every document done.
    """

    output = work / f"output-{attempt}.jsonl"
    request = work / f"request-{attempt}.json"
    request.write_text(
        json.dumps(
            {
                "soffice": binary,
                "documents": documents,
                "output": str(output),
                "profile": (work / f"profile-{attempt}").as_uri(),
            }
        ),
        encoding="utf-8",
    )
    # The office process keeps its temporary files under the work directory,
    # so a killed process leaves nothing behind once the batch ends.
    office_tmp = work / f"tmp-{attempt}"
    office_tmp.mkdir()
    process = subprocess.Popen(
        [python, str(_CHILD), str(request)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**_child_env(), "TMPDIR": str(office_tmp)},
        start_new_session=True,
    )
    lines, stalled_since = 0, time.monotonic()
    try:
        while process.poll() is None:
            now = time.monotonic()
            _, _, count = _progress(output)
            if count != lines:
                lines, stalled_since = count, now
            if now >= deadline or now - stalled_since >= document_seconds:
                break
            time.sleep(0.2)
    finally:
        _kill_group(process)
    finished, loading, _ = _progress(output)
    complete = process.returncode == 0 and len(finished) == len(documents)
    return finished, loading, complete


def render_table_cells(
    payloads: Mapping[str, bytes],
    *,
    soffice: str | None = None,
    timeout_seconds: int = 600,
    document_seconds: int = 120,
) -> dict[str, dict[str, Any] | None]:
    """LibreOffice's cell paragraphs of each ODT, or ``None`` per document.

    A rendering is ``{"rendering_version", "tables"}``: one entry per
    top-level body table in document order, each a list of rows, each a list
    of cells by column position; a cell is a list of paragraphs
    ``{"text", "label", "separator", "hidden", "bullet", "transform"}``
    (``separator`` is ``None`` when there is no label or its gap is not a
    character) and ``{"nested_table": true}`` for a table inside the cell, or
    ``None`` when the office suite cannot address it.  ``getString`` returns
    text LibreOffice does not draw and bullets have no label string, so each
    paragraph also reports whether any of it, or its label, is hidden (a
    hidden character attribute, hiding field or hidden or conditional
    section), the bullet or image a list level draws instead of a label, and
    whether a case map (upper, lower, title case or small caps) changes how
    its text or label is drawn.

    A document is ``None`` when LibreOffice or its Python bridge is
    unavailable, or the document does not load, crashes the office process
    or stalls it for ``document_seconds``; the other documents are rendered
    by a fresh office process.  Whatever is unfinished at
    ``timeout_seconds`` is ``None``.
    """

    result: dict[str, dict[str, Any] | None] = {key: None for key in payloads}
    binary = soffice or shutil.which("soffice")
    if not payloads or not binary:
        return result
    python = uno_python(binary)
    if python is None:
        return result
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix="nhi-office-cells-") as scratch:
        work = Path(scratch)
        paths: dict[str, str] = {}
        for index, key in enumerate(sorted(payloads)):
            path = work / f"document-{index:04d}.odt"
            path.write_bytes(payloads[key])
            paths[key] = str(path)
        pending = sorted(payloads)
        attempt = 0
        while pending and time.monotonic() < deadline:
            finished, loading, complete = _attempt(
                python,
                binary,
                [(key, paths[key]) for key in pending],
                work,
                attempt,
                deadline=deadline,
                document_seconds=document_seconds,
            )
            attempt += 1
            for key, entry in finished.items():
                if key in result and isinstance(entry.get("tables"), list):
                    result[key] = {
                        "rendering_version": CELL_RENDERING_VERSION,
                        "tables": entry["tables"],
                    }
            if complete:
                break
            if loading is None and not finished:
                # The office process failed before any document: the
                # renderer itself is unavailable.
                break
            # The document being loaded, if any, stopped the office process;
            # it stays unrendered and the rest get a fresh process.
            pending = [
                key for key in pending if key not in finished and key != loading
            ]
    return result


# ---------------------------------------------------------------------------
# Child process: runs under an interpreter with LibreOffice's ``uno`` bridge.


def _property(name: str, value: Any) -> Any:
    from com.sun.star.beans import PropertyValue

    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def _value(element: Any, name: str) -> Any:
    try:
        return element.getPropertyValue(name)
    except Exception:  # the property does not apply here
        return None


def _level(paragraph: Any) -> dict[str, Any] | None:
    """The list level a counted list paragraph draws, if it is one."""

    rules = _value(paragraph, "NumberingRules")
    if rules is None or not _value(paragraph, "NumberingIsNumber"):
        return None
    level = _value(paragraph, "NumberingLevel")
    if level is None:
        return None
    return {item.Name: item.Value for item in rules.getByIndex(level)}


def _separator(level: dict[str, Any] | None) -> str | None:
    if level is None or level.get("PositionAndSpaceMode") != _LABEL_ALIGNMENT:
        return None
    return _LABEL_FOLLOWED_BY.get(level.get("LabelFollowedBy"))


def _presentation(
    paragraph: Any, level: dict[str, Any] | None, character_styles: Any
) -> dict[str, Any]:
    """What LibreOffice draws differently from the paragraph's string."""

    hidden = bool(_value(paragraph, "CharHidden"))
    transform = bool(_value(paragraph, "CharCaseMap"))
    section = _value(paragraph, "TextSection")
    while section is not None:
        if _value(section, "IsVisible") is False or _value(section, "Condition"):
            hidden = True
        section = _value(section, "ParentSection")
    portions = paragraph.createEnumeration()
    while portions.hasMoreElements():
        portion = portions.nextElement()
        if _value(portion, "CharHidden"):
            hidden = True
        if _value(portion, "CharCaseMap"):
            transform = True
        field = (
            _value(portion, "TextField")
            if _value(portion, "TextPortionType") == "TextField"
            else None
        )
        if field is not None and any(
            field.supportsService(name) for name in _HIDING_FIELDS
        ):
            hidden = True
    bullet = None
    if level is not None:
        bullet = _NUMBERING_BULLET.get(level.get("NumberingType"))
        if bullet == "bullet":
            bullet = level.get("BulletChar") or bullet
        style_name = level.get("CharStyleName")
        if style_name and character_styles.hasByName(style_name):
            style = character_styles.getByName(style_name)
            hidden = hidden or bool(_value(style, "CharHidden"))
            transform = transform or bool(_value(style, "CharCaseMap"))
    return {"hidden": hidden, "bullet": bullet, "transform": transform}


def _paragraphs(text: Any, character_styles: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    enumeration = text.createEnumeration()
    while enumeration.hasMoreElements():
        element = enumeration.nextElement()
        if element.supportsService("com.sun.star.text.TextTable"):
            found.append({"nested_table": True})
            continue
        label = _value(element, "ListLabelString") or ""
        level = _level(element)
        found.append(
            {
                "text": element.getString(),
                "label": label,
                "separator": _separator(level) if label else None,
                **_presentation(element, level, character_styles),
            }
        )
    return found


def _tables(document: Any) -> list[list[list[Any]]]:
    character_styles = document.getStyleFamilies().getByName("CharacterStyles")
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
                cells.append(
                    None
                    if cell is None
                    else _paragraphs(cell.getText(), character_styles)
                )
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
        with open(request["output"], "a", encoding="utf-8") as output:

            def emit(entry: dict[str, Any]) -> None:
                output.write(json.dumps(entry, ensure_ascii=False) + "\n")
                output.flush()

            for key, path in request["documents"]:
                emit({"loading": key})
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
                    emit({"key": key, "error": "the document did not load"})
                    continue
                try:
                    emit({"key": key, "tables": _tables(document)})
                finally:
                    document.close(True)
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
