"""String-level grammar of an official comparison table (修訂對照表) cell.

The announced overlay (:mod:`nhi_rule_history.announced_notice`) and the
comparison-card generator read the same two cell shapes and must agree on
them, so the grammar lives here, without any database or document
dependency:

* a cell that only says ``無`` marks a new clause (in the original column) or
  a deleted one (in the revised column);
* a general-principle (通則) row opens each column with the chapter heading
  and lists the principle points by their top-level CJK ordinal
  (``四、注射藥品之使用原則：``).  A run of points the table leaves out is one
  paragraph naming the range and the omission (``一、~三、(略)``): an
  omission marker, never a point.  A line that only looks like one
  (``五至十二、(略)``) is never absorbed into the point above it: the cut
  refuses it.

Grammar matching runs on NFKC text, so full-width forms (``（無）``,
``（略）``, ``～``) read as their ASCII counterparts.  Stored text is never
normalized.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass

GENERAL_PRINCIPLE_RULE_VERSION = "nhi-rule-history/general-principle-points/1.0.0"
GENERAL_PRINCIPLE_LABEL = "通則"
GENERAL_PRINCIPLE_HEADING = "藥品給付規定通則"

_NONE_CELL_RE = re.compile(r"^\(?\s*無\s*\)?[。.]?$")
_ORDINAL = "[一二三四五六七八九十]+"
_MARKER_RE = re.compile(rf"^\s*(?P<ordinal>{_ORDINAL})、(?P<rest>.*)\Z", re.DOTALL)
_RANGE_RE = re.compile(
    rf"^\s*[~〜]\s*(?P<last>{_ORDINAL})、(?P<tail>.*)\Z", re.DOTALL
)
_OMISSION_TAIL_RE = re.compile(
    r"^\s*(?:\(\s*(?:以下|其餘|餘)?\s*略\s*\)|(?:以下|其餘|餘)?略)\s*[。.]?\s*\Z"
)
# What a marker line that is neither a recognised range nor a single omitted point must not show: another
# ordinal and 、 inside it, an omission at its end (a bare 略 only after a boundary, so 策略 is a word), or
# a dash, tilde or connector symbol right after the marker.  Any of them is a range written with a connector
# this grammar does not read (∼ ﹏ － — 至 ...), and reading it as a point would invent point N.
_INNER_MARKER_RE = re.compile(rf"{_ORDINAL}、")
_OMISSION_END_RE = re.compile(
    r"(?:\(\s*(?:以下|其餘|餘)?\s*略\s*\)|(?:^|[\s:,、。)\]】~])(?:以下|其餘|餘)?略)\s*[。.]?\s*\Z"
)
_CONNECTOR_CATEGORIES = frozenset({"Pd", "Sm", "Pc"})
# The head of a line that is not a point marker but starts like a top-level ordinal: a bare ordinal (also after
# 第) that is not followed by 、, or an ordinal in brackets.  NFKC folds （五） to (五).
_BARE_HEAD_RE = re.compile(rf"^\s*第?\s*{_ORDINAL}")
_BRACKET_HEAD_RE = re.compile(rf"^\s*[(\[【〔]\s*(?P<ordinal>{_ORDINAL})\s*[)\]】〕]")
_CJK_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def grammar_text(text: str) -> str:
    """NFKC projection used only for grammar matching, never for storage."""

    return unicodedata.normalize("NFKC", text)


def is_none_cell(text: str) -> bool:
    """Whether one paragraph reads ``無``, ``(無)`` or ``（無）`` (full-width)."""

    return _NONE_CELL_RE.fullmatch(grammar_text(text).strip()) is not None


def is_general_principle_heading(text: str) -> bool:
    """Whether ``text`` is the 通則 chapter heading, ignoring width and spacing."""

    return re.sub(r"\s+", "", grammar_text(text)) == GENERAL_PRINCIPLE_HEADING


def cjk_ordinal(text: str) -> int | None:
    """The value of a CJK numeral from 一 to 九十九, or ``None`` if it is none.

    Only the plain positional forms are read (``四``, ``十``, ``十一``,
    ``二十``, ``二十一``); anything else, such as ``十十`` or ``一二``, is
    unreadable and the caller fails closed.
    """

    if text in _CJK_DIGITS:
        return _CJK_DIGITS[text]
    if text == "十":
        return 10
    if text.startswith("十"):
        tail = _CJK_DIGITS.get(text[1:])
        return None if tail is None else 10 + tail
    head, ten, tail = text.partition("十")
    if not ten or head not in _CJK_DIGITS:
        return None
    if not tail:
        return _CJK_DIGITS[head] * 10
    if tail not in _CJK_DIGITS:
        return None
    return _CJK_DIGITS[head] * 10 + _CJK_DIGITS[tail]


@dataclass(frozen=True)
class GeneralPrincipleSplit:
    """One 通則 column cut into points, or the reason it could not be.

    ``points`` are ``(ordinal, start, end)``: the point's paragraphs are
    ``texts[start:end]`` of the cell, its marker paragraph first.  ``omissions``
    are ``(index, first, last)``: paragraphs that only name a range of points
    the table leaves out.  Neither the heading paragraph nor an omission
    marker belongs to a point.
    """

    points: tuple[tuple[int, int, int], ...] = ()
    omissions: tuple[tuple[int, int, int], ...] = ()
    refusal: str | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        """The project-assigned clause codes (``0.4``) of the points."""

        return tuple(f"0.{ordinal}" for ordinal, _, _ in self.points)


def read_point_marker(text: str) -> tuple[str, int, int] | str | None:
    """Read one paragraph as a 通則 point marker.

    ``None``: not a marker.  A string: it starts like a marker but cannot be
    read, the named reason.  Otherwise ``(kind, first, last)`` with kind
    ``point`` or ``omission``.  A marker followed by ``~`` is a range and must
    be exactly ``first、~last、`` plus an omission; a marker followed by
    nothing but an omission is a single omitted point; anything else after a
    marker is the point's own heading text, unless it shows what only a range
    shows (another ordinal and 、, an omission at its end, or a dash, tilde
    or connector symbol straight after the marker): a range written with a
    connector this grammar does not read is ``range_marker_unreadable``, never
    a point.
    """

    match = _MARKER_RE.match(grammar_text(text))
    if match is None:
        return None
    first = cjk_ordinal(match["ordinal"])
    if first is None:
        return "ordinal_unreadable"
    rest = match["rest"]
    if re.match(r"\s*[~〜]", rest):
        span = _RANGE_RE.match(rest)
        if span is None:
            return "range_marker_unreadable"
        last = cjk_ordinal(span["last"])
        if last is None:
            return "ordinal_unreadable"
        if last < first:
            return "range_marker_reversed"
        if _OMISSION_TAIL_RE.match(span["tail"]) is None:
            return "range_marker_is_not_an_omission"
        return ("omission", first, last)
    if _OMISSION_TAIL_RE.match(rest):
        return ("omission", first, first)
    lead = rest.lstrip()[:1]
    if (
        _INNER_MARKER_RE.search(rest)
        or _OMISSION_END_RE.search(rest)
        or (lead and unicodedata.category(lead) in _CONNECTOR_CATEGORIES)
    ):
        return "range_marker_unreadable"
    return ("point", first, first)


def reads_like_an_omission_marker(text: str, latest: int) -> bool:
    """Whether a paragraph that is not a point marker reads like an omission marker for top-level points.

    A line :func:`read_point_marker` reads (a point, an omission, or a marker it names unreadable) is that
    function's, never a lookalike.

    ``latest`` is the last top-level ordinal the cut has seen, so ``latest + 1`` is the next point the column
    could name.  Such a line ends in an omission and starts like a top-level ordinal without being the
    recognised marker (``五至十二、(略)``, ``五~十二、(略)``, ``第五、~十二、(略)``): the cut cannot say which
    points it omits, and the paragraph must not run on as the text of the point above it.  A line that starts
    with an ordinal in brackets is how this document numbers sub-items (the official row carries
    ``（三）~（四）(略)`` inside point 四): it reads like an omission marker only when its first ordinal is the
    next top-level ordinal, because it is then equally the omitted points that follow and the point's own
    sub-item omission.  A bracketed range that starts anywhere else is sub-item text.
    """

    line = grammar_text(text)
    if _OMISSION_END_RE.search(line) is None or read_point_marker(text) is not None:
        return False
    if _BARE_HEAD_RE.match(line):
        return True
    head = _BRACKET_HEAD_RE.match(line)
    return head is not None and cjk_ordinal(head["ordinal"]) == latest + 1


def split_general_principle(
    texts: Sequence[str], *, plain: Collection[int] = frozenset()
) -> GeneralPrincipleSplit:
    """Cut the paragraphs of one 通則 column into its points.

    ``texts[0]`` must be the chapter heading.  A paragraph starting with a
    top-level ordinal and ``、`` opens a point (``四、注射藥品之使用原則：``)
    that runs to the next marker paragraph; every paragraph in between,
    sub-items and ``(略)`` lines included, is the point's own text.  A marker
    paragraph that only names an omitted range (``一、~三、(略)``,
    ``五、~十二、(略)``) or a single omitted point (``十、(略)``) is an
    omission marker.  ``plain`` lists the indexes of paragraphs that are never
    markers (those inside a nested table).

    The cut fails closed, with a named reason, when text stands outside any
    point, when an ordinal or range cannot be read, when ordinals do not
    strictly increase (a repeated point, or a sub-list that starts over), when
    a paragraph only reads like an omission marker
    (:func:`reads_like_an_omission_marker`; ``omission_marker_lookalike``), or
    when the column holds no point at all.  Nothing is joined, skipped or
    inferred.
    """

    if not texts or not is_general_principle_heading(texts[0]):
        return GeneralPrincipleSplit(refusal="heading_missing")
    points: list[tuple[int, int, int]] = []
    omissions: list[tuple[int, int, int]] = []
    open_point: tuple[int, int] | None = None
    latest = 0
    for index in range(1, len(texts)):
        token = None if index in plain else read_point_marker(texts[index])
        if isinstance(token, str):
            return GeneralPrincipleSplit(refusal=token)
        if token is None:
            if open_point is None and grammar_text(texts[index]).strip():
                return GeneralPrincipleSplit(refusal="text_outside_a_point")
            if index not in plain and reads_like_an_omission_marker(texts[index], latest):
                return GeneralPrincipleSplit(refusal="omission_marker_lookalike")
            continue
        kind, first, last = token
        if first <= latest:
            return GeneralPrincipleSplit(refusal="ordinals_not_increasing")
        latest = last
        if open_point is not None:
            points.append((open_point[0], open_point[1], index))
            open_point = None
        if kind == "point":
            open_point = (first, index)
        else:
            omissions.append((index, first, last))
    if open_point is not None:
        points.append((open_point[0], open_point[1], len(texts)))
    if not points:
        return GeneralPrincipleSplit(refusal="no_point")
    return GeneralPrincipleSplit(tuple(points), tuple(omissions))
