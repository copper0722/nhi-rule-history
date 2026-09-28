"""ODF list-label reconstruction against pinned LibreOffice behaviour.

Each synthetic document isolates one numbering rule.  The expected text
export prefixes were taken from LibreOffice's plain-text export of the same
documents; ``LibreOfficeCrossCheckTest`` re-renders them when LibreOffice is
installed and requires the reconstruction to match line for line.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from nhi_rule_history.announced_notice import _paragraph_text
from nhi_rule_history.odf_list_numbering import format_number, resolve_list_labels
from nhi_rule_history.odf_list_numbering import ListNumberingUnsupported
from tools.build_announced_notice_fixture import fixture_odt


_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:loext="urn:org:documentfoundation:names:experimental:office:'
    'xmlns:loext:1.0"'
)
MSO = "MicrosoftOffice/15.0 MicrosoftWord"
OTHER = "LibreOffice/7.6"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def level(
    n: int,
    fmt: str = "1",
    prefix: str | None = None,
    suffix: str | None = ".",
    *,
    start: int | None = None,
    followed: str = "listtab",
    extra: str = "",
    properties: bool = True,
) -> str:
    attributes = f'text:level="{n}" style:num-format="{fmt}"'
    if prefix is not None:
        attributes += f' style:num-prefix="{prefix}"'
    if suffix is not None:
        attributes += f' style:num-suffix="{suffix}"'
    if start is not None:
        attributes += f' text:start-value="{start}"'
    inner = (
        '<style:list-level-properties text:list-level-position-and-space-mode='
        f'"label-alignment"><style:list-level-label-alignment '
        f'text:label-followed-by="{followed}"/></style:list-level-properties>'
        if properties
        else ""
    )
    return (
        f"<text:list-level-style-number {attributes} {extra}>{inner}"
        "</text:list-level-style-number>"
    )


def list_style(name: str, *levels: str, attributes: str = "") -> str:
    return (
        f'<text:list-style style:name="{name}" {attributes}>'
        + "".join(levels)
        + "</text:list-style>"
    )


def lst(style: str | None, *items: str, attributes: str = "") -> str:
    name = f' text:style-name="{style}"' if style else ""
    return f"<text:list{name} {attributes}>" + "".join(items) + "</text:list>"


def item(*content: str, attributes: str = "") -> str:
    return f"<text:list-item {attributes}>" + "".join(content) + "</text:list-item>"


def header(*content: str) -> str:
    return "<text:list-header>" + "".join(content) + "</text:list-header>"


def para(text: str, style: str | None = None) -> str:
    name = f' text:style-name="{style}"' if style else ""
    return f"<text:p{name}>{text}</text:p>"


def document(
    body: str,
    *,
    automatic: str = "",
    common: str = "",
    generator: str = MSO,
) -> tuple[bytes, bytes, bytes]:
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_NS}>'
        f"<office:automatic-styles>{automatic}</office:automatic-styles>"
        f"<office:body><office:text>{body}</office:text></office:body>"
        "</office:document-content>"
    ).encode("utf-8")
    styles = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-styles {_NS}>'
        f"<office:styles>{common}</office:styles></office:document-styles>"
    ).encode("utf-8")
    meta = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-meta {_NS}>'
        f"<office:meta><meta:generator>{generator}</meta:generator>"
        "</office:meta></office:document-meta>"
    ).encode("utf-8")
    return content, styles, meta


A = list_style("A", level(1, "1", None, "."), level(2, "1", "(", ")"), level(3, "I"))
B = list_style("B", level(1, "i"), level(2, "a", None, ")"))

# name -> (document, {paragraph: expected}); an expected value is the
# LibreOffice export prefix, or "!" plus the unsupported reason.
CASES: dict[str, tuple[tuple[bytes, bytes, bytes], dict[str, str]]] = {
    "items_continuations_and_nesting": (
        document(
            lst("A", item(para("P01")), item(para("P02"), para("P03")),
              item(para("P04"), lst(None, item(para("P05")), item(para("P06")))), item(para("P07"))),
            automatic=A,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "       ",
         "P04": "    3. ", "P05": "        (1) ", "P06": "        (2) ",
         "P07": "    4. "},
    ),
    "list_header_is_not_counted": (
        document(lst("A", header(para("P01")), item(para("P02")), item(para("P03"))), automatic=A),
        {"P01": "       ", "P02": "    1. ", "P03": "    2. "},
    ),
    "mso_continues_last_list_of_the_style": (
        document(
            lst("A", item(para("P01")), item(para("P02")),
              attributes='text:continue-numbering="true"')
            + lst("B", item(para("P03")), attributes='text:continue-numbering="true"')
            + lst("A", item(para("P04")), attributes='text:continue-numbering="true"'),
            automatic=A + B,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "    i. ", "P04": "    3. "},
    ),
    "other_generators_continue_only_the_preceding_list": (
        document(
            lst("A", item(para("P01")), item(para("P02")),
              attributes='text:continue-numbering="true"')
            + lst("B", item(para("P03")), attributes='text:continue-numbering="true"')
            + lst("A", item(para("P04")), attributes='text:continue-numbering="true"'),
            automatic=A + B,
            generator=OTHER,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "    i. ", "P04": "    1. "},
    ),
    "continue_numbering_absent_or_false_restarts": (
        document(
            lst("A", item(para("P01")), item(para("P02")))
            + lst("A", item(para("P03")), attributes='text:continue-numbering="true"')
            + lst("A", item(para("P04")))
            + lst("A", item(para("P05")), attributes='text:continue-numbering="false"'),
            automatic=A,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "    3. ", "P04": "    1. ",
         "P05": "    1. "},
    ),
    "continue_list_by_xml_id": (
        document(
            lst("A", item(para("P01")), item(para("P02")), attributes='xml:id="list1"')
            + lst("B", item(para("P03")))
            + lst("A", item(para("P04")), attributes='text:continue-list="list1"'),
            automatic=A + B,
            generator=OTHER,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "    i. ", "P04": "    3. "},
    ),
    "level_and_item_start_values": (
        document(
            lst("S", item(para("P01")), item(para("P02")),
              item(para("P03"), attributes='text:start-value="9"'), item(para("P04")),
              item(para("P05"), lst(None, item(para("P06")),
                            item(para("P07"), attributes='text:start-value="7"'),
                            item(para("P08"))))),
            automatic=list_style(
                "S", level(1, "1", None, ".", start=5),
                level(2, "1", "(", ")", start=3),
            ),
        ),
        {"P01": "    5. ", "P02": "    6. ", "P03": "    9. ", "P04": "    10. ",
         "P05": "    11. ", "P06": "        (3) ", "P07": "        (7) ",
         "P08": "        (8) "},
    ),
    "uncounted_parent_continues_previous_subtree": (
        document(
            lst("A", item(para("P01"), lst(None, item(para("P02")), item(para("P03")))),
              header(para("P04"), lst(None, item(para("P05"))))),
            automatic=A,
        ),
        {"P01": "    1. ", "P02": "        (1) ", "P03": "        (2) ",
         "P04": "       ", "P05": "        (3) "},
    ),
    "display_levels_and_list_format_strings": (
        document(
            lst("D", item(para("P01"), lst(None, item(para("P02"), lst(None, item(para("P03")),
                                                      item(para("P04")))),
                                   item(para("P05")))),
              item(para("P06"), lst(None, item(para("P07")))))
            + lst("F", item(para("P08"), lst(None, item(para("P09")))), item(para("P10"))),
            automatic=list_style(
                "D", level(1), level(2, extra='text:display-levels="2"'),
                level(3, "a", "[", "]", extra='text:display-levels="3"'),
            )
            + list_style(
                "F", level(1, extra='loext:num-list-format="%1%)"'),
                level(2, "i", extra='style:num-list-format="%1%-%2%"'),
            ),
            generator=OTHER,
        ),
        {"P01": "    1. ", "P02": "        1.1. ", "P03": "            [1.1.a] ",
         "P04": "            [1.1.b] ", "P05": "        1.2. ", "P06": "    2. ",
         "P07": "        2.1. ", "P08": "    1) ", "P09": "        1-i ",
         "P10": "    2) "},
    ),
    "label_followed_by_does_not_change_the_export": (
        document(
            lst("G", item(para("P01"), lst(None, item(para("P02")))), item(para("P03"))),
            automatic=list_style(
                "G", level(1, followed="nothing"), level(2, followed="space"),
            ),
        ),
        {"P01": "    1. ", "P02": "        1. ", "P03": "    2. "},
    ),
    "paragraph_style_list_is_ignored_inside_a_list": (
        document(
            lst("A", item(para("P01", "PB")), item(para("P02")), item(para("P03", "PE"))),
            automatic=A + B
            + '<style:style style:name="PB" style:family="paragraph" '
            'style:list-style-name="B"/>'
            + '<style:style style:name="PE" style:family="paragraph" '
            'style:list-style-name=""/>',
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "    3. "},
    ),
    "phantom_levels_count_in_libreoffice_only": (
        document(
            lst("A", item(lst(None, item(lst(None, item(para("P01")))))),
              item(lst(None, item(para("P02")))), item(para("P03")), item(para("P04"))),
            automatic=A,
        ),
        {"P01": "            I. ", "P02": "!list_numbering_models_disagree",
         "P03": "!list_numbering_models_disagree",
         "P04": "!list_numbering_models_disagree"},
    ),
    "second_sub_list_restart_is_libreoffice_only": (
        document(
            lst("A", item(para("P01"), lst(None, item(para("P02")), item(para("P03"))), para("P04"),
                     lst(None, item(para("P05")))),
              item(para("P06"), lst(None, item(para("P07"))),
                lst(None, item(para("P08")), attributes='text:continue-numbering="true"'))),
            automatic=A,
        ),
        {"P01": "    1. ", "P02": "        (1) ", "P03": "        (2) ",
         "P04": "       ", "P05": "!list_numbering_models_disagree",
         "P06": "    2. ", "P07": "        (1) ", "P08": "        (2) "},
    ),
    "fail_closed_features": (
        document(
            para("P01", "PL")
            + lst("BU", item(para("P02")))
            + lst("W", item(para("P03")))
            + lst("NN", item(para("P04")))
            + lst("CN", item(para("P05")))
            + lst("A", item(para("P06"), lst("B", item(para("P07")))))
            + lst("A", item('<text:h text:outline-level="2">P08</text:h>'))
            + lst("A", item(para("P09"), attributes='text:style-override="B"'))
            + lst(None, item(para("P10"))),
            automatic=A + B
            + '<style:style style:name="PL" style:family="paragraph" '
            'style:list-style-name="A"/>'
            + '<text:list-style style:name="BU"><text:list-level-style-bullet '
            'text:level="1" text:bullet-char="●"><style:list-level-properties '
            'text:list-level-position-and-space-mode="label-alignment"/>'
            "</text:list-level-style-bullet></text:list-style>"
            + list_style("W", level(1, properties=False))
            + list_style("NN", level(1, "", "第", "條"))
            + list_style("CN", level(1), attributes='text:consecutive-numbering="true"'),
        ),
        {"P01": "!paragraph_style_numbering_outside_list",
         "P02": "!bullet_label", "P03": "!label_width_and_position_mode",
         "P04": "!number_none_with_affixes", "P05": "!consecutive_numbering",
         "P06": "    1. ", "P07": "!nested_list_style_differs",
         "P08": "!heading_in_list", "P09": "!list_item_style_override",
         "P10": "!list_style_missing"},
    ),
    "numbered_paragraphs_with_a_list_id": (
        document(
            '<text:numbered-paragraph text:list-id="x1" text:level="1" '
            'text:style-name="A"><text:p>P01</text:p></text:numbered-paragraph>'
            '<text:numbered-paragraph text:list-id="x1" text:level="1" '
            'text:style-name="A"><text:p>P02</text:p></text:numbered-paragraph>'
            '<text:numbered-paragraph text:list-id="x1" text:level="2" '
            'text:style-name="A"><text:p>P03</text:p></text:numbered-paragraph>'
            '<text:numbered-paragraph text:list-id="x1" text:level="1" '
            'text:style-name="A" text:start-value="5"><text:p>P04</text:p>'
            "</text:numbered-paragraph>"
            '<text:numbered-paragraph text:level="1" text:style-name="A">'
            "<text:p>P05</text:p></text:numbered-paragraph>",
            automatic=A,
        ),
        {"P01": "    1. ", "P02": "    2. ", "P03": "        (1) ",
         "P04": "    5. ",
         "P05": "!numbered_paragraph_without_list_id_or_style"},
    ),
    "word_list_format_must_agree": (
        document(
            lst("WA", item(para("P01"))) + lst("WB", item(para("P02"))),
            automatic=list_style(
                "WA", level(1, extra='style:num-list-format-name="NLF0"'),
            )
            + list_style(
                "WB", level(1, extra='style:num-list-format-name="NLF1"'),
            )
            + '<number:num-list-format style:name="NLF0"><number:num-list-label '
            'text:level="1"/><number:text>%1%.</number:text>'
            "</number:num-list-format>"
            + '<number:num-list-format style:name="NLF1"><number:num-list-label '
            'text:level="1"/><number:text>%1%、</number:text>'
            "</number:num-list-format>",
        ),
        {"P01": "    1. ", "P02": "!word_list_format_differs"},
    ),
}


def _labels(parts: tuple[bytes, bytes, bytes]) -> dict[str, tuple[str, object]]:
    content, styles, meta = (ElementTree.fromstring(part) for part in parts)
    labels = resolve_list_labels(content, styles, meta)
    found: dict[str, tuple[str, object]] = {}
    for element in content.find(f".//{{{_OFFICE}}}text").iter():
        if element.tag not in {f"{{{_TEXT}}}p", f"{{{_TEXT}}}h"}:
            continue
        text = _paragraph_text(element, render=True)
        found[text] = ("plain", None) if id(element) not in labels else (
            labels[id(element)].status, labels[id(element)]
        )
    return found


def _expected(label: tuple[str, object]) -> str | None:
    status, value = label
    if status == "plain":
        return ""
    if status == "unsupported":
        return "!" + value.reason
    return value.export_prefix


class ListLabelRulesTest(unittest.TestCase):
    def test_cases_match_pinned_libreoffice_prefixes(self) -> None:
        for name, (parts, expected) in CASES.items():
            found = _labels(parts)
            for paragraph, prefix in expected.items():
                with self.subTest(case=name, paragraph=paragraph):
                    self.assertEqual(_expected(found[paragraph]), prefix)

    def test_printed_prefix_uses_the_label_separator(self) -> None:
        found = _labels(CASES["label_followed_by_does_not_change_the_export"][0])
        self.assertEqual(found["P01"][1].printed_prefix, "1.")
        self.assertEqual(found["P02"][1].printed_prefix, "1. ")
        tab = _labels(CASES["items_continuations_and_nesting"][0])
        self.assertEqual(tab["P01"][1].printed_prefix, "1.\t")
        self.assertEqual(tab["P03"][1].status, "unlabelled")
        self.assertEqual(tab["P03"][1].printed_prefix, "")

    def test_number_formats(self) -> None:
        cases = {
            ("1", 0): "0", ("1", 12): "12", ("a", 1): "a", ("a", 26): "z",
            ("A", 3): "C", ("i", 4): "iv", ("I", 1994): "MCMXCIV",
            ("甲, 乙, 丙, ...", 10): "癸", ("子, 丑, 寅, ...", 12): "亥",
            ("壹, 貳, 參, ...", 9): "玖", ("一, 二, 三, ...", 3): "三",
        }
        for (fmt, value), text in cases.items():
            with self.subTest(fmt=fmt, value=value):
                self.assertEqual(
                    format_number(value, fmt, letter_sync=False, word_compatible=True),
                    text,
                )
        # LibreOffice continues unsynchronized letters aa, ab; Word repeats.
        self.assertEqual(
            format_number(28, "a", letter_sync=False, word_compatible=False), "ab"
        )
        self.assertEqual(
            format_number(28, "A", letter_sync=True, word_compatible=True), "BB"
        )
        for fmt, value in (
            ("a", 27),
            ("甲, 乙, 丙, ...", 11),
            ("壹, 貳, 參, ...", 10),
            ("一, 十, 一百(繁), ...", 1),
            ("１, ２, ３, ...", 1),
            ("①, ②, ③, ...", 1),
            ("i", 0),
        ):
            with self.subTest(fmt=fmt, value=value):
                with self.assertRaises(ListNumberingUnsupported):
                    format_number(value, fmt, letter_sync=False, word_compatible=True)


def _render_many(documents: dict[str, bytes]) -> dict[str, str]:
    """LibreOffice plain-text export of several documents in one run."""

    with tempfile.TemporaryDirectory(
        prefix="nhi-list-lo-", dir=os.environ.get("TMPDIR")
    ) as scratch:
        work = Path(scratch)
        paths = []
        for name, payload in documents.items():
            path = work / f"{name}.odt"
            path.write_bytes(payload)
            paths.append(str(path))
        subprocess.run(
            [
                shutil.which("soffice"),
                f"-env:UserInstallation={(work / 'profile').as_uri()}",
                "--headless",
                "--norestore",
                "--convert-to",
                "txt:Text (encoded):UTF8",
                "--outdir",
                str(work),
                *paths,
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
        return {
            name: (work / f"{name}.txt").read_text(encoding="utf-8-sig")
            for name in documents
        }


@unittest.skipUnless(shutil.which("soffice"), "LibreOffice is unavailable")
class LibreOfficeCrossCheckTest(unittest.TestCase):
    """Every reproduced prefix equals the independent LibreOffice export."""

    def test_reproduced_prefixes_equal_libreoffice(self) -> None:
        rendered = _render_many(
            {name: fixture_odt(*parts) for name, (parts, _) in CASES.items()}
        )
        for name, (parts, expected) in CASES.items():
            lines = {}
            for line in rendered[name].split("\n"):
                match = re.search(r"P\d\d", line)
                if match:
                    lines[match.group(0)] = line[: match.start()]
            found = _labels(parts)
            for paragraph, pinned in expected.items():
                with self.subTest(case=name, paragraph=paragraph):
                    if pinned.startswith("!"):
                        continue
                    self.assertEqual(found[paragraph][1].export_prefix if
                                     found[paragraph][0] != "plain" else "",
                                     lines[paragraph])
            # Confusable negative: a shifted label is not what LibreOffice drew.
            labelled = [
                value for status, value in found.values() if status == "labelled"
            ]
            if labelled:
                first = labelled[0]
                self.assertNotIn(
                    first.export_prefix.replace(first.label, first.label + "0"),
                    set(lines.values()),
                )


if __name__ == "__main__":
    unittest.main()
