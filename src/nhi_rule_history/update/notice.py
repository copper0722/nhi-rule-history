"""Deterministic metadata extraction from an official NHI detail page."""

from __future__ import annotations

import re
from typing import Any

from nhi_rule_history.contracts import ContractError
from nhi_rule_history.update.html import extract_html_text_blocks


_REFERENCE_RE = re.compile(r"^健保審字第\s*\d+\s*號$")
_ROC_DATE_RE = re.compile(r"^\d{3}-\d{2}-\d{2}$")


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
    reference = after("發文字號", pattern=_REFERENCE_RE)
    document_date = after("發文日期", pattern=_ROC_DATE_RE)
    publication_date = after("發布日期", pattern=_ROC_DATE_RE)
    update_date = after("更新日期", pattern=_ROC_DATE_RE)
    announcement = after("公告事項")
    return {
        "subject_raw": subject,
        "reference_number_raw": reference,
        "document_date_roc_raw": document_date,
        "publication_date_roc_raw": publication_date,
        "update_date_roc_raw": update_date,
        "announcement_text_raw": announcement,
    }
