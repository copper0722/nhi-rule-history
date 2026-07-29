from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from nhi_rule_history.discovery.fint_keyword_crawler import Seed


DESIGNATION_PREFIX_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s*")
DATE_GROUP_RE = re.compile(
    r"[\(（][^()（）]{0,160}\d{2,3}\s*/\s*\d{1,2}"
    r"(?:\s*/\s*\d{1,2})?[^()（）]{0,160}[\)）]"
)
LATIN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+)*)"
    r"(?![A-Za-z0-9])"
)
CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]{4,40}")

LATIN_STOPWORDS = {
    "acid",
    "acetate",
    "agent",
    "agents",
    "alpha",
    "amino",
    "blood",
    "calcium",
    "capsule",
    "capsules",
    "citrate",
    "coated",
    "complex",
    "cream",
    "direct",
    "drug",
    "drugs",
    "drops",
    "elixir",
    "extended",
    "extract",
    "factor",
    "failure",
    "general",
    "gelatin",
    "group",
    "hcl",
    "human",
    "immune",
    "implant",
    "injection",
    "injections",
    "iodine",
    "local",
    "maleate",
    "muscle",
    "oral",
    "other",
    "patch",
    "plasma",
    "powder",
    "release",
    "relief",
    "solution",
    "sustained",
    "tablet",
    "tablets",
    "therapy",
    "treatment",
    "used",
    "with",
}
CJK_STOPWORDS = {
    "全民健康保險藥品給付規定",
    "藥品給付規定",
    "治療藥品",
    "口服製劑",
    "注射製劑",
    "注射劑型",
    "一般錠劑膠囊劑",
    "藥品使用原則",
}
BASELINE_SEEDS = (
    Seed(
        "藥品給付規定",
        "manual_source_universe_baseline",
        "methodology/fint-baseline",
    ),
    Seed(
        "全民健康保險藥品給付規定",
        "manual_source_universe_baseline",
        "methodology/fint-full-title",
    ),
    Seed(
        "藥品使用原則",
        "manual_source_universe_synonym",
        "methodology/fint-synonym-usage-principle",
    ),
    Seed(
        "藥品使用規定",
        "manual_source_universe_synonym",
        "methodology/fint-synonym-usage-rule",
    ),
)


@dataclass(frozen=True)
class Heading:
    designation: str
    raw_text: str


def _surfaces(raw_text: str) -> tuple[str, str]:
    surface = DESIGNATION_PREFIX_RE.sub("", raw_text, count=1)
    surface = DATE_GROUP_RE.sub("", surface)
    surface = " ".join(surface.split())
    title_surface = surface
    for separator in ("：", ":"):
        if separator in title_surface:
            title_surface = title_surface.split(separator, 1)[0]
    return surface, title_surface


def extract_terms(raw_text: str) -> tuple[str, ...]:
    """Extract high-specificity drug, brand, acronym, and class terms."""

    latin_surface, cjk_surface = _surfaces(raw_text)
    ordered: list[str] = []
    seen: set[str] = set()

    for match in LATIN_TOKEN_RE.finditer(latin_surface):
        term = match.group(1)
        folded = term.casefold()
        is_acronym = term.upper() == term and len(term) >= 3
        if folded in LATIN_STOPWORDS:
            continue
        if not is_acronym and len(term) < 5:
            continue
        if folded in seen:
            continue
        ordered.append(term)
        seen.add(folded)

    for match in CJK_CHUNK_RE.finditer(cjk_surface):
        term = match.group(0)
        if term.startswith(("之", "及", "與", "或", "如")):
            continue
        if term in CJK_STOPWORDS:
            continue
        if any(stop in term for stop in ("藥品給付規定", "限用於")):
            continue
        folded = term.casefold()
        if folded in seen:
            continue
        ordered.append(term)
        seen.add(folded)

    return tuple(ordered)


def seeds_from_headings(headings: Iterable[Heading]) -> list[Seed]:
    seeds = list(BASELINE_SEEDS)
    seen_heading: set[tuple[str, str]] = set()
    for heading in headings:
        heading_key = (heading.designation, heading.raw_text)
        if heading_key in seen_heading:
            continue
        seen_heading.add(heading_key)
        for term in extract_terms(heading.raw_text):
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9+-]*", term):
                additional: tuple[str, ...] = ()
            else:
                additional = ("藥品給付規定",)
            seeds.append(
                Seed(
                    term,
                    "current_canonical_clause_heading",
                    f"clause/{heading.designation}",
                    additional,
                )
            )
    return seeds
