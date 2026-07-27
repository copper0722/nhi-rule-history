"""Deterministic metadata extraction from an official NHI detail page."""

from __future__ import annotations

import re
from typing import Any

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.update.html import (
    extract_html_metadata_fields,
    extract_html_text_blocks,
)


_REFERENCE_SOURCE_WHITESPACE = r"[\t\n\r\f\v \u00a0\u3000]*"
_REFERENCE_SOURCE_RE = re.compile(
    rf"^{_REFERENCE_SOURCE_WHITESPACE}"
    rf"健保審字第{_REFERENCE_SOURCE_WHITESPACE}"
    rf"[0-9]+{_REFERENCE_SOURCE_WHITESPACE}"
    rf"號?。?{_REFERENCE_SOURCE_WHITESPACE}$"
)
_REFERENCE_COMPACT_RE = re.compile(
    r"^健保審字第(?P<number>[0-9]+)(?P<hao>號)?$"
)
_REFERENCE_WHITESPACE_RE = re.compile(
    r"[\t\n\r\f\v \u00a0\u3000]+"
)
_ROC_DATE_RE = re.compile(r"^[0-9]{3}-[0-9]{2}-[0-9]{2}$")
REFERENCE_NUMBER_NORMALIZATION_RULE = (
    "nhi-reference-number-normalization/1.1.0"
)
REFERENCE_NUMBER_NORMALIZATION_RULE_V12 = (
    "nhi-reference-number-normalization/1.0.0"
)
_REFERENCE_SOURCE_RE_V12 = re.compile(
    r"^健保審字第\s*[0-9]+\s*號?$"
)


def normalize_reference_number(raw: str) -> tuple[str, str]:
    """Return a canonical identifier while preserving how it was derived.

    Some official NHI detail pages omit the terminal ``號``; others append one
    sentence-final ``。`` after the identifier.  The observable source spelling
    is retained separately.  Normalization permits only whitespace removal,
    removal of exactly one terminal full stop, and appending one missing
    terminal ``號``.
    """

    compact = _REFERENCE_WHITESPACE_RE.sub("", raw)
    without_whitespace = compact
    operations: list[str] = []
    if without_whitespace != raw:
        operations.append("whitespace_removed")
    if compact.endswith("。"):
        compact = compact[:-1]
        operations.append("terminal_full_stop_removed")
    matched = _REFERENCE_COMPACT_RE.fullmatch(compact)
    if matched is None:
        raise ContractError("official reference number has an unsupported shape")
    if matched.group("hao") is None:
        operations.append("terminal_hao_appended")
    normalized = f"健保審字第{matched.group('number')}號"
    return normalized, (
        "_and_".join(operations) if operations else "exact"
    )


def _normalize_reference_number_v12(raw: str) -> tuple[str, str]:
    compact = "".join(raw.split())
    matched = _REFERENCE_COMPACT_RE.fullmatch(compact)
    if matched is None:
        raise ContractError(
            "v1.2 official reference number has an unsupported shape"
        )
    operations: list[str] = []
    if compact != raw:
        operations.append("whitespace_removed")
    if matched.group("hao") is None:
        operations.append("terminal_hao_appended")
    normalized = f"健保審字第{matched.group('number')}號"
    return normalized, (
        "_and_".join(operations) if operations else "exact"
    )


def extract_notice_metadata_v12(
    payload: bytes, artifact_sha256: str
) -> dict[str, Any]:
    """Replay the frozen manifest-v1.2 flattened metadata semantics."""

    values = [
        row["raw_text"]
        for row in extract_html_text_blocks(payload, artifact_sha256)
    ]

    def after(label: str, *, pattern: re.Pattern[str] | None = None) -> str:
        matches = []
        for index, value in enumerate(values[:-1]):
            if value != label:
                continue
            candidate = values[index + 1]
            if pattern is None or pattern.fullmatch(candidate):
                matches.append(candidate)
        unique = list(dict.fromkeys(matches))
        if len(unique) != 1:
            raise ContractError(
                f"v1.2 official detail metadata {label!r} "
                "is missing or ambiguous"
            )
        return unique[0]

    reference = after("發文字號", pattern=_REFERENCE_SOURCE_RE_V12)
    normalized, normalization = _normalize_reference_number_v12(reference)
    return {
        "subject_raw": after("主旨"),
        "reference_number_raw": reference,
        "reference_number_normalized": normalized,
        "reference_number_normalization": normalization,
        "reference_number_normalization_rule": (
            REFERENCE_NUMBER_NORMALIZATION_RULE_V12
        ),
        "document_date_roc_raw": after(
            "發文日期",
            pattern=_ROC_DATE_RE,
        ),
        "publication_date_roc_raw": after(
            "發布日期",
            pattern=_ROC_DATE_RE,
        ),
        "update_date_roc_raw": after(
            "更新日期",
            pattern=_ROC_DATE_RE,
        ),
        "announcement_text_raw": after("公告事項"),
    }


def extract_notice_metadata(
    payload: bytes, artifact_sha256: str
) -> dict[str, Any]:
    fields = extract_html_metadata_fields(payload, artifact_sha256)

    def after(
        label: str,
        *,
        pattern: re.Pattern[str] | None = None,
        strip_boundary: bool = False,
    ) -> str:
        matches: list[str] = []
        for field in fields:
            if (
                field["label_raw"].strip(
                    "\t\n\r\f\v \u00a0\u3000"
                )
                != label
            ):
                continue
            candidate = field["value_raw"]
            if strip_boundary:
                candidate = candidate.strip(
                    "\t\n\r\f\v \u00a0\u3000"
                )
            if pattern is None or pattern.fullmatch(candidate):
                matches.append(candidate)
        unique = list(dict.fromkeys(matches))
        if len(unique) != 1:
            raise ContractError(
                f"official detail metadata {label!r} is missing or ambiguous"
            )
        return unique[0]

    def section(label: str) -> str:
        matches: list[str] = []
        for field in fields:
            if (
                field["label_raw"].strip(
                    "\t\n\r\f\v \u00a0\u3000"
                )
                != label
            ):
                continue
            blocks = field["value_blocks"]
            candidate = (
                "\n\n".join(blocks)
                if blocks
                else field["value_raw"]
            )
            if candidate:
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
    document_date = after(
        "發文日期",
        pattern=_ROC_DATE_RE,
        strip_boundary=True,
    )
    publication_date = after(
        "發布日期",
        pattern=_ROC_DATE_RE,
        strip_boundary=True,
    )
    update_date = after(
        "更新日期",
        pattern=_ROC_DATE_RE,
        strip_boundary=True,
    )
    announcement = section("公告事項")
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
