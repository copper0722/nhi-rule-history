"""Deterministic metadata extraction from an official NHI detail page."""

from __future__ import annotations

import re
from typing import Any

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.update.html import extract_html_text_blocks


_REFERENCE_SOURCE_RE = re.compile(r"^健保審字第\s*\d+\s*號?$")
_REFERENCE_COMPACT_RE = re.compile(r"^健保審字第(?P<number>\d+)(?P<hao>號)?$")
_ROC_DATE_RE = re.compile(r"^\d{3}-\d{2}-\d{2}$")
REFERENCE_NUMBER_NORMALIZATION_RULE = (
    "nhi-reference-number-normalization/1.0.0"
)


def normalize_reference_number(raw: str) -> tuple[str, str]:
    """Return a canonical identifier while preserving how it was derived.

    Some official NHI detail pages omit the terminal ``號``.  That observable
    source spelling is retained separately; this function permits only
    whitespace removal and appending that one missing terminal character.
    """

    compact = "".join(raw.split())
    matched = _REFERENCE_COMPACT_RE.fullmatch(compact)
    if matched is None:
        raise ContractError("official reference number has an unsupported shape")
    operations: list[str] = []
    if compact != raw:
        operations.append("whitespace_removed")
    if matched.group("hao") is None:
        operations.append("terminal_hao_appended")
    normalized = f"健保審字第{matched.group('number')}號"
    return normalized, (
        "_and_".join(operations) if operations else "exact"
    )


def extract_notice_metadata(
    payload: bytes, artifact_sha256: str
) -> dict[str, Any]:
    blocks = extract_html_text_blocks(payload, artifact_sha256)
    values = [row["raw_text"] for row in blocks]

    def after(label: str, *, pattern: re.Pattern[str] | None = None) -> str:
        matches: list[str] = []
        for index, value in enumerate(values[:-1]):
            if value != label:
                continue
            candidate = values[index + 1]
            if pattern is None or pattern.fullmatch(candidate):
                matches.append(candidate)
        unique = list(dict.fromkeys(matches))
        if len(unique) != 1:
            raise ContractError(
                f"official detail metadata {label!r} is missing or ambiguous"
            )
        return unique[0]

    subject = after("主旨")
    reference = after("發文字號", pattern=_REFERENCE_SOURCE_RE)
    reference_normalized, reference_normalization = (
        normalize_reference_number(reference)
    )
    document_date = after("發文日期", pattern=_ROC_DATE_RE)
    publication_date = after("發布日期", pattern=_ROC_DATE_RE)
    update_date = after("更新日期", pattern=_ROC_DATE_RE)
    announcement = after("公告事項")
    return {
        "subject_raw": subject,
        "reference_number_raw": reference,
        "reference_number_normalized": reference_normalized,
        "reference_number_normalization": reference_normalization,
        "reference_number_normalization_rule": (
            REFERENCE_NUMBER_NORMALIZATION_RULE
        ),
        "document_date_roc_raw": document_date,
        "publication_date_roc_raw": publication_date,
        "update_date_roc_raw": update_date,
        "announcement_text_raw": announcement,
    }
