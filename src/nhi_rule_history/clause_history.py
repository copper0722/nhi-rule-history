"""Single-clause history derived from sealed official-edition snapshots.

PostgreSQL is the only writable authority. JSONL, SQLite, and browser payloads
are deterministic projections of sealed PostgreSQL rows.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from nhi_rule_history.edition_history import (
    ROC_DATE_RE,
    _canonical_json,
    _comparison_key,
    _inline_segments,
    _json_default,
    _sha256_bytes,
    _sha256_text,
    _stable_id,
    connect,
)


EXTRACTOR_VERSION = "chapter-00-single-clause-extractor/v1"
DIFF_VERSION = "chapter-00-single-clause-diff/v1"
DIFF_PRESENTATION_VERSION = "chapter-00-semantic-diff-presentation/v2"
READER_ENRICHMENT_VERSION = "chapter-00-reader-enrichment/v5"
NHI_DRUG_LOOKUP_URL = "https://info.nhi.gov.tw/INAE3000/INAE3000S01"
ICD11_CODING_TOOL_URL = (
    "https://icd.who.int/ct/icd11_mms/en/2026-01"
)
SINGLE_QUOTES = frozenset({"'", "’", "‘", "＇", "ʼ"})
IGNORED_CHANGE_POLICY = (
    {
        "code": "whitespace",
        "label_zh": "空白字元",
        "rule": "Unicode 空白不計為條文內容增刪。",
    },
    {
        "code": "single_quote",
        "label_zh": "單引號",
        "rule": "半形、全形與彎引號形式的單引號不計為內容增刪。",
    },
    {
        "code": "width_variant",
        "label_zh": "全形／半形",
        "rule": "Unicode NFKC 相等的全形與半形字元視為相同。",
    },
)
EDITION_IMPORT_RUN_ID = uuid.UUID("b1a3aed6-7dff-563a-a1eb-4c5454a960b0")
EDITION_RULE_ID = "rule:general-principles"
CHAPTER_ID = "chapter:general-principles"
CHAPTER_CODE = "chapter:00"
TOP_LEVEL_RE = re.compile(r"^(?P<numeral>[一二三四五六七八九十]+)、\s*(?P<body>.*)")
ROC_DATE_PART_RE = re.compile(
    r"(?P<year>\d{2,3})\s*/\s*(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})"
)

# Agent-curated exact terms for the 0.4 prototype.  Every configured ATC code
# is validated against the live PG reference before it is admitted to the
# project-owned enrichment tables.  Only mappings used by this clause are
# exported; the repository never mirrors the complete ATC index.
CHAPTER_04_DRUG_KEYWORDS: tuple[dict[str, Any], ...] = (
    {"term": "insulin", "type": "ingredient", "atc": ("A10AB01", "A10AB03", "A10AC01", "A10AC03", "A10AD01", "A10AD03", "A10AE01")},
    {"term": "GLP-1受體促效劑", "type": "drug_class", "atc": ("A10BJ",)},
    {"term": "Desferrioxamine", "type": "ingredient", "atc": ("V03AC01",)},
    {"term": "Desferal", "type": "brand", "atc": ("V03AC01",)},
    {"term": "紅血球生成素", "type": "drug_class", "atc": ("B03XA",)},
    {"term": "Eprex", "type": "brand", "atc": ("B03XA01",)},
    {"term": "Recormon", "type": "brand", "atc": ("B03XA01",)},
    {"term": "Aranesp", "type": "brand", "atc": ("B03XA02",)},
    {"term": "Mircera", "type": "brand", "atc": ("B03XA03",)},
    {"term": "α-interferon", "type": "drug_class", "atc": ("L03AB",)},
    {"term": "G-CSF", "type": "drug_class", "atc": ("L03AA",)},
    {"term": "filgrastim", "type": "ingredient", "atc": ("L03AA02",)},
    {"term": "lenograstim", "type": "ingredient", "atc": ("L03AA10",)},
    {"term": "生長激素", "type": "drug_class", "atc": ("H01AC01",)},
    {"term": "human growth hormone", "type": "drug_class", "atc": ("H01AC01",)},
    {"term": "TPN", "type": "abbreviation", "atc": ("B05BA10",)},
    {"term": "octreotide", "type": "ingredient", "atc": ("H01CB02",)},
    {"term": "lanreotide", "type": "ingredient", "atc": ("H01CB03",)},
    {"term": "Sandostatin", "type": "brand", "atc": ("H01CB02",)},
    {"term": "Somatuline", "type": "brand", "atc": ("H01CB03",)},
    {"term": "streptomycin", "type": "ingredient", "atc": ("J01GA01",)},
    {"term": "kanamycin", "type": "ingredient", "atc": ("J01GB04",)},
    {"term": "enviomycin", "type": "ingredient", "atc": ("J04AB06",)},
    {"term": "抗精神病長效針劑", "type": "drug_class", "atc": ("N05A",)},
    {"term": "低分子量肝凝素", "type": "drug_class", "atc": ("B01AB",)},
    {"term": "Apomorphine hydrochloride", "type": "ingredient", "atc": ("N04BC07",)},
    {"term": "apomorphine", "type": "ingredient", "atc": ("N04BC07",)},
    {"term": "Apo-Go Pen", "type": "brand", "atc": ("N04BC07",)},
    {"term": "維生素B12注射劑", "type": "drug_class", "atc": ("B03BA01",)},
    {"term": "aldesleukin", "type": "ingredient", "atc": ("L03AC01",)},
    {"term": "Proleukin Inj", "type": "brand", "atc": ("L03AC01",)},
    {"term": "etanercept", "type": "ingredient", "atc": ("L04AB01",)},
    {"term": "adalimumab", "type": "ingredient", "atc": ("L04AB04",)},
    {"term": "abatacept", "type": "ingredient", "atc": ("L04AA24",)},
    {"term": "tocilizumab", "type": "ingredient", "atc": ("L04AC07",)},
    {"term": "opinercept", "type": "ingredient", "atc": ("L04AB07",)},
    {"term": "certolizumab", "type": "ingredient", "atc": ("L04AB05",)},
    {"term": "brodalumab", "type": "ingredient", "atc": ("L04AC12",)},
    {"term": "teriparatide", "type": "ingredient", "atc": ("H05AA02",)},
    {"term": "interferon beta-1a", "type": "ingredient", "atc": ("L03AB07",)},
    {"term": "interferon beta-1b", "type": "ingredient", "atc": ("L03AB08",)},
    {"term": "glatiramer", "type": "ingredient", "atc": ("L03AX13",)},
    {"term": "Fondaparinux", "type": "ingredient", "atc": ("B01AX05",)},
    {"term": "Arixtra", "type": "brand", "atc": ("B01AX05",)},
    {"term": "morphine", "type": "ingredient", "atc": ("N02AA01",)},
)

CHAPTER_04_DISEASE_TAGS: tuple[dict[str, Any], ...] = (
    {
        "term": "糖尿病",
        "query": "diabetes mellitus",
        "mappings": (("5A14", "candidate", 0.65),),
    },
    {
        "term": "慢性腎臟功能衰竭",
        "query": "chronic kidney disease",
        "mappings": (("GB61", "agent_selected", 0.85),),
    },
    {
        "term": "白血病",
        "query": "leukaemia",
        "mappings": (("2B33.4", "candidate", 0.60),),
    },
    {
        "term": "血友病",
        "query": "haemophilia",
        "mappings": (
            ("3B10.0", "candidate", 0.75),
            ("3B11.0", "candidate", 0.75),
        ),
    },
    {
        "term": "肢端肥大症",
        "query": "acromegaly",
        "mappings": (("5A60.0", "agent_selected", 0.98),),
    },
    {
        "term": "結核病",
        "query": "tuberculosis",
        "mappings": (("1B1Z", "candidate", 0.60),),
    },
    {
        "term": "巴金森氏病",
        "query": "Parkinson disease",
        "mappings": (("8A00.0", "agent_selected", 0.98),),
    },
    {
        "term": "惡性貧血",
        "query": "pernicious anaemia",
        "mappings": (("3A01.30", "agent_selected", 0.98),),
    },
    {
        "term": "pernicious anemia",
        "query": "pernicious anaemia",
        "mappings": (("3A01.30", "agent_selected", 0.98),),
    },
    {
        "term": "維生素B12缺乏",
        "query": "vitamin B12 deficiency",
        "mappings": (("5B5F", "agent_selected", 0.98),),
    },
    {
        "term": "慢性病毒性B型肝炎",
        "query": "chronic hepatitis B",
        "mappings": (("1E51.0", "agent_selected", 0.98),),
    },
    {
        "term": "慢性病毒性C型肝炎",
        "query": "chronic hepatitis C",
        "mappings": (("1E51.1", "agent_selected", 0.98),),
    },
    {
        "term": "類風濕關節炎",
        "query": "rheumatoid arthritis",
        "mappings": (("FA20", "agent_selected", 0.98),),
    },
    {
        "term": "僵直性脊椎炎",
        "query": "axial spondyloarthritis",
        "mappings": (("FA92.0", "candidate", 0.80),),
    },
    {
        "term": "乾癬性周邊關節炎",
        "query": "psoriatic arthritis",
        "mappings": (("FA21", "candidate", 0.85),),
    },
    {
        "term": "乾癬性脊椎病變",
        "query": "psoriatic arthritis",
        "mappings": (("FA21", "candidate", 0.75),),
    },
    {
        "term": "乾癬",
        "query": "psoriasis",
        "mappings": (("EA90", "agent_selected", 0.98),),
    },
    {
        "term": "克隆氏症",
        "query": "Crohn disease",
        "mappings": (("DD70", "agent_selected", 0.98),),
    },
    {
        "term": "靜脈血栓",
        "query": "venous thromboembolism",
        "mappings": (("BD72", "agent_selected", 0.90),),
    },
    {
        "term": "VTE",
        "query": "venous thromboembolism",
        "mappings": (("BD72", "agent_selected", 0.90),),
    },
    {
        "term": "癌症",
        "query": "malignant neoplasm",
        "mappings": (),
    },
)

CHAPTER_04_CONDITION_MARKERS: tuple[tuple[str, str], ...] = (
    ("經事前審查核准後", "prior_authorization"),
    ("需個案事前報准", "prior_authorization"),
    ("需敘明理由", "requirement"),
    ("不得超過", "prohibition"),
    ("不超過", "maximum"),
    ("為限", "maximum"),
    ("方得", "restriction"),
    ("不得", "prohibition"),
    ("且", "logical"),
    ("或", "logical"),
    ("惟", "exception"),
    ("限", "restriction"),
)

DURATION_MARKER_PATTERN = re.compile(
    r"(?<![\d/])(?:"
    r"每(?:小時|天|日|週|周|月|年)"
    r"|(?:\d+|[零〇○一二三四五六七八九十百千兩]+)"
    r"(?:個)?(?:小時|天|日|週|周|月|年)"
    r")"
    r"(?![\d/])"
)

CHAPTER_04_AGENT_SUMMARY = """\
這一條的歷史變動不是反覆重寫全文，而是逐步擴大「哪些注射藥可讓病人攜回」、調整可攜回天數或數量，並增加申報與紀錄要求。

- **98/9–98/11**：紅血球生成素例示加入 Mircera；G-CSF 可攜回天數由三天改為六天；apomorphine 的例示商品名更新。
- **99/11**：apomorphine 每月上限由 15 支提高為 20 支。
- **100/4**：血友病用藥段落加入應依《血液製劑條例》辦理。
- **103/9**：新增 exenatide、liraglutide、teriparatide、interferon beta-1a、interferon beta-1b 與 glatiramer 六項注射劑。
- **108/10–109/12**：增加血友病居家治療紀錄上傳要求，擴大相關藥物；抗精神病長效針劑可攜回期間由一個月延長至三個月；生物製劑的適應症與品項範圍擴大；insulin 條目納入 GLP-1 受體促效劑，原 exenatide、liraglutide 獨立條目改標刪除。
- **111/1–111/3**：靜脈營養輸液條件擴及不需調配的製劑，並新增 fondaparinux。

日期取自新文字中首次出現的條文註記，用來幫助閱讀版本變化；尚未把它認定為經公告證實的法律生效日。"""

CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass(frozen=True)
class SourceEdition:
    version_id: str
    chronology_order: int
    edition_label: str
    official_date_role: str
    official_date_raw_value: str
    official_date_value: date
    official_date_precision: str
    legal_effective_status: str
    official_url: str
    source_page_url: str
    artifact_sha256: str
    blocks: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ClauseObservation:
    ordinal_number: int
    canonical_code: str
    clause_id: str
    edition: SourceEdition
    source_designation_raw: str
    blocks: tuple[dict[str, Any], ...]

    @property
    def raw_text(self) -> str:
        return "\n".join(str(block["raw_text"]) for block in self.blocks)

    @property
    def normalized_text(self) -> str:
        return "\n".join(
            str(block["normalized_text"]) for block in self.blocks
        )

    @property
    def normalized_sha256(self) -> str:
        return _sha256_text(self.normalized_text)

    @property
    def comparison_sha256(self) -> str:
        return _sha256_text(_comparison_key(self.normalized_text))


def _semantic_units(
    text: str | None,
) -> tuple[list[tuple[int, str, str]], list[str]]:
    units: list[tuple[int, str, str]] = []
    classifications: list[str] = []
    for index, char in enumerate(text or ""):
        if char.isspace() or char in SINGLE_QUOTES:
            classifications.append("ignored")
            continue
        classifications.append("pending")
        units.append((index, char, unicodedata.normalize("NFKC", char)))
    return units, classifications


def _group_semantic_segments(
    text: str,
    classifications: Sequence[str],
    *,
    side: str,
) -> list[dict[str, str]]:
    if not text:
        return []
    segments: list[dict[str, str]] = []
    start = 0
    current = classifications[0]
    for index in range(1, len(text)):
        if classifications[index] == current:
            continue
        segments.append(
            {
                "side": side,
                "kind": current,
                "text": text[start:index],
            }
        )
        start = index
        current = classifications[index]
    segments.append(
        {
            "side": side,
            "kind": current,
            "text": text[start:],
        }
    )
    return segments


def semantic_diff_presentation(
    old_text: str | None,
    new_text: str | None,
) -> dict[str, Any]:
    """Classify substantive changes while retaining exact display text."""

    old_value = old_text or ""
    new_value = new_text or ""
    old_units, old_classes = _semantic_units(old_value)
    new_units, new_classes = _semantic_units(new_value)
    ignored_classes: set[str] = set()

    old_whitespace = [char for char in old_value if char.isspace()]
    new_whitespace = [char for char in new_value if char.isspace()]
    if old_whitespace != new_whitespace:
        ignored_classes.add("whitespace")

    old_quotes = [char for char in old_value if char in SINGLE_QUOTES]
    new_quotes = [char for char in new_value if char in SINGLE_QUOTES]
    if old_quotes != new_quotes:
        ignored_classes.add("single_quote")

    matcher = SequenceMatcher(
        None,
        [unit[2] for unit in old_units],
        [unit[2] for unit in new_units],
        autojunk=False,
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for old_unit, new_unit in zip(
                old_units[old_start:old_end],
                new_units[new_start:new_end],
                strict=True,
            ):
                old_index, old_char, old_key = old_unit
                new_index, new_char, new_key = new_unit
                if old_char != new_char and old_key == new_key:
                    old_classes[old_index] = "ignored"
                    new_classes[new_index] = "ignored"
                    ignored_classes.add("width_variant")
                else:
                    old_classes[old_index] = "unchanged"
                    new_classes[new_index] = "unchanged"
            continue
        if tag in {"delete", "replace"}:
            for index, _, _ in old_units[old_start:old_end]:
                old_classes[index] = "removed"
        if tag in {"insert", "replace"}:
            for index, _, _ in new_units[new_start:new_end]:
                new_classes[index] = "added"

    if "pending" in old_classes or "pending" in new_classes:
        raise ValueError("semantic diff left unclassified characters")

    has_removed = "removed" in old_classes
    has_added = "added" in new_classes
    if has_removed and has_added:
        change_kind = "replaced"
        display_note = "下一版改寫"
    elif has_removed:
        change_kind = "removed"
        display_note = "下一版刪除"
    elif has_added:
        change_kind = "added"
        display_note = "下一版新增"
    else:
        change_kind = "format_only"
        display_note = "僅格式差異（已忽略）"

    return {
        "semantic_change_kind": change_kind,
        "display_note": display_note,
        "inline_segments": [
            *_group_semantic_segments(
                old_value,
                old_classes,
                side="old",
            ),
            *_group_semantic_segments(
                new_value,
                new_classes,
                side="new",
            ),
        ],
        "ignored_change_classes": sorted(ignored_classes),
    }


def _chinese_ordinal(value: str) -> int:
    if value == "十":
        return 10
    if value.startswith("十"):
        tail = value[1:]
        if tail not in CHINESE_DIGITS:
            raise ValueError(f"unsupported Chinese ordinal: {value}")
        return 10 + CHINESE_DIGITS[tail]
    if value.endswith("十"):
        head = value[:-1]
        if head not in CHINESE_DIGITS:
            raise ValueError(f"unsupported Chinese ordinal: {value}")
        return CHINESE_DIGITS[head] * 10
    if "十" in value:
        head, tail = value.split("十", 1)
        if head not in CHINESE_DIGITS or tail not in CHINESE_DIGITS:
            raise ValueError(f"unsupported Chinese ordinal: {value}")
        return CHINESE_DIGITS[head] * 10 + CHINESE_DIGITS[tail]
    if value not in CHINESE_DIGITS:
        raise ValueError(f"unsupported Chinese ordinal: {value}")
    return CHINESE_DIGITS[value]


def _source_editions(
    conn: psycopg.Connection[Any],
    *,
    edition_import_run_id: uuid.UUID = EDITION_IMPORT_RUN_ID,
) -> list[SourceEdition]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT state
            FROM nhi_rule_history_edition.import_run
            WHERE run_id = %s
            """,
            (edition_import_run_id,),
        )
        import_row = cur.fetchone()
        if import_row is None or import_row["state"] != "sealed":
            raise ValueError("source-edition import is not sealed")

        cur.execute(
            """
            SELECT
              version.version_id,
              version.chronology_order,
              version.version_label,
              date_fact.date_role AS official_date_role,
              date_fact.raw_value AS official_date_raw_value,
              date_fact.date_value AS official_date_value,
              date_fact.date_precision AS official_date_precision,
              date_fact.legal_effective_status,
              document.official_url,
              document.source_page_url,
              document.artifact_sha256
            FROM nhi_rule_history_edition.rule_version version
            JOIN nhi_rule_history_edition.source_document document
              ON document.document_id = version.primary_document_id
            JOIN nhi_rule_history_edition.rule_version_date date_fact
              ON date_fact.version_id = version.version_id
              AND date_fact.date_role IN (
                'official_edition_label', 'official_update_date'
              )
            WHERE version.rule_id = %s
            ORDER BY version.chronology_order
            """,
            (EDITION_RULE_ID,),
        )
        edition_rows = list(cur.fetchall())
        if len(edition_rows) != 15:
            raise ValueError(
                f"expected 15 source editions, got {len(edition_rows)}"
            )

        version_ids = [str(row["version_id"]) for row in edition_rows]
        cur.execute(
            """
            SELECT
              version_id,
              block_id,
              source_order,
              block_kind,
              structural_path,
              raw_text,
              normalized_text,
              comparison_key,
              raw_sha256,
              source_locator
            FROM nhi_rule_history_edition.rule_block
            WHERE version_id = ANY(%s)
            ORDER BY version_id, source_order
            """,
            (version_ids,),
        )
        blocks_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in cur.fetchall():
            blocks_by_version[str(row["version_id"])].append(dict(row))

    editions: list[SourceEdition] = []
    for expected_order, row in enumerate(edition_rows):
        if int(row["chronology_order"]) != expected_order:
            raise ValueError("source-edition chronology is not contiguous")
        version_id = str(row["version_id"])
        blocks = tuple(blocks_by_version[version_id])
        if not blocks:
            raise ValueError(f"source edition has no blocks: {version_id}")
        editions.append(
            SourceEdition(
                version_id=version_id,
                chronology_order=expected_order,
                edition_label=str(row["version_label"]),
                official_date_role=str(row["official_date_role"]),
                official_date_raw_value=str(row["official_date_raw_value"]),
                official_date_value=row["official_date_value"],
                official_date_precision=str(row["official_date_precision"]),
                legal_effective_status=str(row["legal_effective_status"]),
                official_url=str(row["official_url"]),
                source_page_url=str(row["source_page_url"]),
                artifact_sha256=str(row["artifact_sha256"]),
                blocks=blocks,
            )
        )
    return editions


def _segment_edition(edition: SourceEdition) -> list[ClauseObservation]:
    segments: list[tuple[int, str, list[dict[str, Any]]]] = []
    current_ordinal: int | None = None
    current_designation = ""
    current_blocks: list[dict[str, Any]] = []
    for block in edition.blocks:
        text = str(block["normalized_text"])
        match = TOP_LEVEL_RE.match(text)
        if match:
            if current_ordinal is not None:
                segments.append(
                    (
                        current_ordinal,
                        current_designation,
                        current_blocks,
                    )
                )
            current_ordinal = _chinese_ordinal(match.group("numeral"))
            current_designation = f"{match.group('numeral')}、"
            current_blocks = [block]
        elif current_ordinal is None:
            raise ValueError(
                f"content before first clause in {edition.edition_label}"
            )
        else:
            current_blocks.append(block)
    if current_ordinal is not None:
        segments.append(
            (current_ordinal, current_designation, current_blocks)
        )
    ordinals = [ordinal for ordinal, _, _ in segments]
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError(
            f"non-contiguous top-level clauses in {edition.edition_label}: "
            f"{ordinals}"
        )
    return [
        ClauseObservation(
            ordinal_number=ordinal,
            canonical_code=f"0.{ordinal}",
            clause_id=f"clause:general-principles:0.{ordinal}",
            edition=edition,
            source_designation_raw=designation,
            blocks=tuple(blocks),
        )
        for ordinal, designation, blocks in segments
    ]


def collect_clause_observations(
    conn: psycopg.Connection[Any],
    *,
    edition_import_run_id: uuid.UUID = EDITION_IMPORT_RUN_ID,
) -> tuple[list[SourceEdition], dict[int, list[ClauseObservation]]]:
    editions = _source_editions(
        conn,
        edition_import_run_id=edition_import_run_id,
    )
    observations: dict[int, list[ClauseObservation]] = defaultdict(list)
    for edition in editions:
        for observation in _segment_edition(edition):
            observations[observation.ordinal_number].append(observation)
    ordinals = sorted(observations)
    if ordinals != list(range(1, max(ordinals) + 1)):
        raise ValueError(f"union clause ordinals are not contiguous: {ordinals}")
    if ordinals != list(range(1, 13)):
        raise ValueError(f"expected clauses 0.1 through 0.12, got {ordinals}")
    return editions, observations


def _display_title(observation: ClauseObservation) -> str:
    first_line = str(observation.blocks[0]["normalized_text"])
    match = TOP_LEVEL_RE.match(first_line)
    body = match.group("body") if match else first_line
    title = body.strip().rstrip("：:。；;")
    if "：" in title and title.index("：") <= 36:
        title = title.split("：", 1)[0]
    if ":" in title and title.index(":") <= 36:
        title = title.split(":", 1)[0]
    if len(title) > 48:
        title = f"{title[:47]}…"
    return title


def _observation_id(observation: ClauseObservation) -> str:
    return _stable_id(
        "clause-observation",
        [observation.clause_id, observation.edition.version_id],
    )


def _version_id(
    clause_id: str,
    state_order: int,
    first_observation: ClauseObservation,
) -> str:
    return _stable_id(
        "clause-version",
        {
            "clause_id": clause_id,
            "state_order": state_order,
            "first_source_edition_version_id": (
                first_observation.edition.version_id
            ),
            "normalized_sha256": first_observation.normalized_sha256,
        },
    )


def _block_rows(
    clause_version_id: str,
    observation: ClauseObservation,
) -> list[dict[str, Any]]:
    observation_id = _observation_id(observation)
    rows: list[dict[str, Any]] = []
    for block_order, source_block in enumerate(observation.blocks):
        raw_text = str(source_block["raw_text"])
        block_id = _stable_id(
            "clause-block",
            [
                clause_version_id,
                block_order,
                _sha256_text(raw_text),
            ],
        )
        rows.append(
            {
                "block_id": block_id,
                "clause_version_id": clause_version_id,
                "representative_observation_id": observation_id,
                "block_order": block_order,
                "block_kind": str(source_block["block_kind"]),
                "structural_path": [
                    CHAPTER_CODE,
                    observation.canonical_code,
                ],
                "raw_text": raw_text,
                "normalized_text": str(source_block["normalized_text"]),
                "comparison_key": str(source_block["comparison_key"]),
                "raw_sha256": _sha256_text(raw_text),
                "source_locator": {
                    "source_edition_version_id": observation.edition.version_id,
                    "source_block_id": str(source_block["block_id"]),
                    "source_order": int(source_block["source_order"]),
                    "source_locator": source_block["source_locator"],
                },
            }
        )
    return rows


def _date_rows(
    clause_version_id: str,
    observation: ClauseObservation,
    blocks: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    observation_id = _observation_id(observation)
    for block in blocks:
        raw_text = str(block["raw_text"])
        for match in ROC_DATE_RE.finditer(raw_text):
            raw_value = match.group(0)
            try:
                value = date(
                    int(match.group("year")) + 1911,
                    int(match.group("month")),
                    int(match.group("day")),
                )
                precision = "day"
                status = "candidate_unresolved"
            except ValueError:
                value = None
                precision = "unknown"
                status = "rejected_non_date"
            locator = {
                "block_id": block["block_id"],
                "char_start": match.start(),
                "char_end": match.end(),
                "raw_text_sha256": block["raw_sha256"],
            }
            rows.append(
                {
                    "date_fact_id": _stable_id(
                        "clause-date",
                        [
                            clause_version_id,
                            raw_value,
                            locator,
                        ],
                    ),
                    "clause_version_id": clause_version_id,
                    "representative_observation_id": observation_id,
                    "date_role": "text_amendment_annotation",
                    "raw_value": raw_value,
                    "calendar_system": "ROC",
                    "date_value": value,
                    "date_precision": precision,
                    "basis": (
                        "Source-local annotation inside one clause version. "
                        "It is not a legal effective-date claim until verified."
                    ),
                    "legal_effective_status": status,
                    "source_locator": locator,
                }
            )
    return rows


def _diff_rows(
    *,
    clause_id: str,
    canonical_code: str,
    older_version: dict[str, Any],
    newer_version: dict[str, Any],
    old_blocks: Sequence[dict[str, Any]],
    new_blocks: Sequence[dict[str, Any]],
    older_last_observed_order: int,
    newer_first_observed_order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from difflib import SequenceMatcher

    matcher = SequenceMatcher(
        None,
        [str(row["comparison_key"]) for row in old_blocks],
        [str(row["comparison_key"]) for row in new_blocks],
        autojunk=False,
    )
    hunks: list[dict[str, Any]] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        selected_old = list(old_blocks[old_start:old_end])
        selected_new = list(new_blocks[new_start:new_end])
        old_text = (
            "\n".join(str(row["normalized_text"]) for row in selected_old)
            or None
        )
        new_text = (
            "\n".join(str(row["normalized_text"]) for row in selected_new)
            or None
        )
        change_kind = {
            "delete": "removed",
            "insert": "added",
            "replace": "replaced",
        }[tag]
        hunk_order = len(hunks)
        hunk_payload = {
            "older": older_version["clause_version_id"],
            "newer": newer_version["clause_version_id"],
            "hunk_order": hunk_order,
            "change_kind": change_kind,
            "old_text_sha256": _sha256_text(old_text) if old_text else None,
            "new_text_sha256": _sha256_text(new_text) if new_text else None,
        }
        hunks.append(
            {
                "hunk_id": _stable_id("clause-hunk", hunk_payload),
                "hunk_order": hunk_order,
                "change_kind": change_kind,
                "context_label": canonical_code,
                "old_block_id": (
                    selected_old[0]["block_id"]
                    if len(selected_old) == 1
                    else None
                ),
                "new_block_id": (
                    selected_new[0]["block_id"]
                    if len(selected_new) == 1
                    else None
                ),
                "old_text": old_text,
                "new_text": new_text,
                "old_text_sha256": (
                    _sha256_text(old_text) if old_text else None
                ),
                "new_text_sha256": (
                    _sha256_text(new_text) if new_text else None
                ),
                "inline_segments": _inline_segments(old_text, new_text),
                "display_note": {
                    "added": "本版新增",
                    "removed": "下一版移除",
                    "replaced": "下一版改寫",
                }[change_kind],
            }
        )
    if not hunks:
        raise ValueError(
            f"distinct clause versions produced no diff: {canonical_code}"
        )
    input_payload = {
        "clause_id": clause_id,
        "older_clause_version_id": older_version["clause_version_id"],
        "newer_clause_version_id": newer_version["clause_version_id"],
        "older_normalized_sha256": older_version["normalized_sha256"],
        "newer_normalized_sha256": newer_version["normalized_sha256"],
        "algorithm_version": DIFF_VERSION,
    }
    edge_id = _stable_id("clause-edge", input_payload)
    output_payload = [
        {
            key: hunk[key]
            for key in (
                "hunk_order",
                "change_kind",
                "context_label",
                "old_text_sha256",
                "new_text_sha256",
                "inline_segments",
            )
        }
        for hunk in hunks
    ]
    edge = {
        "edge_id": edge_id,
        "clause_id": clause_id,
        "older_clause_version_id": older_version["clause_version_id"],
        "newer_clause_version_id": newer_version["clause_version_id"],
        "adjacency_basis": (
            "adjacent_distinct_text_state_across_official_editions"
        ),
        "legal_predecessor_status": "not_claimed",
        "crosses_known_gap": True,
        "older_last_observed_order": older_last_observed_order,
        "newer_first_observed_order": newer_first_observed_order,
        "algorithm_version": DIFF_VERSION,
        "input_sha256": _sha256_text(_canonical_json(input_payload)),
        "output_sha256": _sha256_text(_canonical_json(output_payload)),
        "change_hunk_count": len(hunks),
        "status": "verified_source_edition_diff",
    }
    for hunk in hunks:
        hunk["edge_id"] = edge_id
    return edge, hunks


def build_rows(
    editions: Sequence[SourceEdition],
    observations_by_ordinal: dict[int, list[ClauseObservation]],
) -> dict[str, list[dict[str, Any]]]:
    source_editions = [
        {
            "source_edition_version_id": edition.version_id,
            "edition_import_run_id": EDITION_IMPORT_RUN_ID,
            "chronology_order": edition.chronology_order,
            "edition_label": edition.edition_label,
            "official_date_role": edition.official_date_role,
            "official_date_raw_value": edition.official_date_raw_value,
            "official_date_value": edition.official_date_value,
            "official_date_precision": edition.official_date_precision,
            "legal_effective_status": edition.legal_effective_status,
            "official_url": edition.official_url,
            "source_page_url": edition.source_page_url,
            "artifact_sha256": edition.artifact_sha256,
        }
        for edition in editions
    ]
    clauses: list[dict[str, Any]] = []
    versions: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    date_rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    hunks: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    for ordinal in sorted(observations_by_ordinal):
        observations = sorted(
            observations_by_ordinal[ordinal],
            key=lambda item: item.edition.chronology_order,
        )
        clause_id = observations[0].clause_id
        canonical_code = observations[0].canonical_code
        clauses.append(
            {
                "clause_id": clause_id,
                "chapter_id": CHAPTER_ID,
                "canonical_code": canonical_code,
                "ordinal_number": ordinal,
                "code_origin": "project_assigned",
                "identity_basis": (
                    "Project code mapped to a contiguous top-level Chinese "
                    "ordinal with text continuity across the declared official "
                    "edition set."
                ),
                "identity_status": (
                    "verified_within_declared_edition_set"
                ),
            }
        )

        states: list[list[ClauseObservation]] = []
        for observation in observations:
            if (
                not states
                or states[-1][-1].comparison_sha256
                != observation.comparison_sha256
            ):
                states.append([observation])
            else:
                states[-1].append(observation)

        versions_for_clause: list[dict[str, Any]] = []
        blocks_by_version: dict[str, list[dict[str, Any]]] = {}
        state_ranges: dict[str, tuple[int, int]] = {}
        for state_order, state_observations in enumerate(states):
            representative = state_observations[0]
            clause_version_id = _version_id(
                clause_id,
                state_order,
                representative,
            )
            materialized_blocks = _block_rows(
                clause_version_id,
                representative,
            )
            version_row = {
                "clause_version_id": clause_version_id,
                "clause_id": clause_id,
                "state_order": state_order,
                "display_title": _display_title(representative),
                "representative_raw_text": representative.raw_text,
                "normalized_text": representative.normalized_text,
                "structured_json": {
                    "schema": (
                        "nhi-rule-history/single-clause-structured-text/v1"
                    ),
                    "chapter_navigation_code": CHAPTER_CODE,
                    "canonical_code": canonical_code,
                    "source_designation_raw": (
                        representative.source_designation_raw
                    ),
                    "blocks": [
                        {
                            "block_order": block["block_order"],
                            "normalized_text": block["normalized_text"],
                            "source_locator": block["source_locator"],
                        }
                        for block in materialized_blocks
                    ],
                },
                "representative_raw_sha256": _sha256_text(
                    representative.raw_text
                ),
                "normalized_sha256": representative.normalized_sha256,
                "comparison_sha256": representative.comparison_sha256,
                "extractor_version": EXTRACTOR_VERSION,
                "legal_effective_status": "not_claimed",
            }
            versions.append(version_row)
            versions_for_clause.append(version_row)
            blocks_by_version[clause_version_id] = materialized_blocks
            block_rows.extend(materialized_blocks)
            date_rows.extend(
                _date_rows(
                    clause_version_id,
                    representative,
                    materialized_blocks,
                )
            )

            orders: list[int] = []
            for observation in state_observations:
                observation_id = _observation_id(observation)
                orders.append(observation.edition.chronology_order)
                observation_rows.append(
                    {
                        "observation_id": observation_id,
                        "clause_id": clause_id,
                        "clause_version_id": clause_version_id,
                        "source_edition_version_id": (
                            observation.edition.version_id
                        ),
                        "chronology_order": (
                            observation.edition.chronology_order
                        ),
                        "edition_label": observation.edition.edition_label,
                        "source_designation_raw": (
                            observation.source_designation_raw
                        ),
                        "source_order_start": int(
                            observation.blocks[0]["source_order"]
                        ),
                        "source_order_end": int(
                            observation.blocks[-1]["source_order"]
                        ),
                        "raw_text": observation.raw_text,
                        "normalized_text": observation.normalized_text,
                        "raw_sha256": _sha256_text(observation.raw_text),
                        "normalized_sha256": (
                            observation.normalized_sha256
                        ),
                        "source_locator": {
                            "source_edition_version_id": (
                                observation.edition.version_id
                            ),
                            "source_block_ids": [
                                str(block["block_id"])
                                for block in observation.blocks
                            ],
                            "source_orders": [
                                int(block["source_order"])
                                for block in observation.blocks
                            ],
                            "source_locators": [
                                block["source_locator"]
                                for block in observation.blocks
                            ],
                        },
                    }
                )
            state_ranges[clause_version_id] = (min(orders), max(orders))

        for older, newer in zip(
            versions_for_clause,
            versions_for_clause[1:],
        ):
            older_range = state_ranges[older["clause_version_id"]]
            newer_range = state_ranges[newer["clause_version_id"]]
            edge, edge_hunks = _diff_rows(
                clause_id=clause_id,
                canonical_code=canonical_code,
                older_version=older,
                newer_version=newer,
                old_blocks=blocks_by_version[older["clause_version_id"]],
                new_blocks=blocks_by_version[newer["clause_version_id"]],
                older_last_observed_order=older_range[1],
                newer_first_observed_order=newer_range[0],
            )
            edges.append(edge)
            hunks.extend(edge_hunks)

        observed_orders = [
            observation.edition.chronology_order
            for observation in observations
        ]
        presence_contiguous = observed_orders == list(
            range(min(observed_orders), max(observed_orders) + 1)
        )
        coverage.append(
            {
                "assessment_id": _stable_id(
                    "clause-coverage",
                    [
                        clause_id,
                        [edition.version_id for edition in editions],
                    ],
                ),
                "clause_id": clause_id,
                "declared_edition_count": len(editions),
                "observed_edition_count": len(observations),
                "first_observed_order": min(observed_orders),
                "last_observed_order": max(observed_orders),
                "version_state_count": len(states),
                "unique_comparison_text_count": len(
                    {
                        observation.comparison_sha256
                        for observation in observations
                    }
                ),
                "version_edge_count": max(len(states) - 1, 0),
                "observed_presence_contiguous": presence_contiguous,
                "declared_edition_set_complete": True,
                "official_source_universe_closed": False,
                "legal_history_complete": False,
                "status": (
                    "complete_for_declared_source_edition_observations"
                    if presence_contiguous
                    else (
                        "incomplete_declared_source_edition_observations"
                    )
                ),
                "gap_reasons": [
                    {
                        "code": "OFFICIAL_SOURCE_UNIVERSE_OPEN",
                        "meaning": (
                            "The declared cumulative-edition set does not prove "
                            "that every legal amendment event has been found."
                        ),
                    },
                    {
                        "code": "LEGAL_EFFECTIVE_DATES_UNVERIFIED",
                        "meaning": (
                            "Edition labels and in-text date annotations are "
                            "not silently treated as legal effective dates."
                        ),
                    },
                ],
            }
        )

    return {
        "source_edition": source_editions,
        "chapter": [
            {
                "chapter_id": CHAPTER_ID,
                "display_label": "通則",
                "source_designation_raw": "通則",
                "navigation_code": CHAPTER_CODE,
                "navigation_code_origin": "project_assigned",
            }
        ],
        "clause": clauses,
        "clause_version": versions,
        "clause_version_observation": observation_rows,
        "clause_version_block": block_rows,
        "clause_version_date": date_rows,
        "clause_version_edge": edges,
        "clause_diff_hunk": hunks,
        "coverage_assessment": coverage,
    }


def _source_set_sha256(rows: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        "edition_import_run_id": str(EDITION_IMPORT_RUN_ID),
        "source_editions": [
            {
                "version_id": row["source_edition_version_id"],
                "artifact_sha256": row["artifact_sha256"],
            }
            for row in rows["source_edition"]
        ],
        "observations": [
            {
                "clause_id": row["clause_id"],
                "source_edition_version_id": row[
                    "source_edition_version_id"
                ],
                "normalized_sha256": row["normalized_sha256"],
            }
            for row in rows["clause_version_observation"]
        ],
        "extractor_version": EXTRACTOR_VERSION,
        "diff_version": DIFF_VERSION,
    }
    return _sha256_text(_canonical_json(payload))


def _output_sha256(rows: dict[str, list[dict[str, Any]]]) -> str:
    return _sha256_text(
        _canonical_json(
            {
                table: table_rows
                for table, table_rows in sorted(rows.items())
                if table != "source_edition"
            }
        )
    )


def _insert_many(
    cur: psycopg.Cursor[Any],
    table: str,
    rows: Sequence[dict[str, Any]],
    *,
    json_columns: set[str] | None = None,
) -> None:
    if not rows:
        return
    json_columns = json_columns or set()
    columns = list(rows[0])
    if any(list(row) != columns for row in rows):
        raise ValueError(f"inconsistent columns for {table}")
    quoted = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = [
        tuple(
            Jsonb(row[column]) if column in json_columns else row[column]
            for column in columns
        )
        for row in rows
    ]
    cur.executemany(
        f"""
        INSERT INTO nhi_rule_history_clause.{table} ({quoted})
        VALUES ({placeholders})
        ON CONFLICT DO NOTHING
        """,
        values,
    )


def rebuild(
    conn: psycopg.Connection[Any],
    *,
    edition_import_run_id: uuid.UUID = EDITION_IMPORT_RUN_ID,
) -> dict[str, Any]:
    editions, observations = collect_clause_observations(
        conn,
        edition_import_run_id=edition_import_run_id,
    )
    rows = build_rows(editions, observations)
    source_set_sha256 = _source_set_sha256(rows)
    run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nhi-rule-history-clause:{source_set_sha256}",
    )
    output_sha256 = _output_sha256(rows)
    started_at = datetime.now(timezone.utc)
    row_counts = {
        table: len(table_rows)
        for table, table_rows in rows.items()
        if table != "source_edition"
    }

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nhi-rule-history-clause-loader-v1",),
            )
            cur.execute(
                """
                SELECT state, output_sha256, row_counts
                FROM nhi_rule_history_clause.import_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            existing = cur.fetchone()
            if existing is not None:
                if (
                    existing["state"] != "sealed"
                    or existing["output_sha256"] != output_sha256
                    or dict(existing["row_counts"]) != row_counts
                ):
                    raise ValueError(
                        "existing clause import does not match replay"
                    )
                return {
                    "status": "already_sealed",
                    "run_id": str(run_id),
                    "source_set_sha256": source_set_sha256,
                    "output_sha256": output_sha256,
                    "row_counts": row_counts,
                }

            cur.execute(
                """
                INSERT INTO nhi_rule_history_clause.import_run (
                  run_id, edition_import_run_id, source_set_sha256,
                  extractor_version, diff_version, state, started_at
                ) VALUES (%s, %s, %s, %s, %s, 'loading', %s)
                """,
                (
                    run_id,
                    edition_import_run_id,
                    source_set_sha256,
                    EXTRACTOR_VERSION,
                    DIFF_VERSION,
                    started_at,
                ),
            )
            _insert_many(
                cur,
                "chapter",
                [
                    {
                        "first_import_run_id": run_id,
                        **row,
                    }
                    for row in rows["chapter"]
                ],
            )
            _insert_many(
                cur,
                "clause",
                [
                    {
                        "first_import_run_id": run_id,
                        **row,
                    }
                    for row in rows["clause"]
                ],
            )
            _insert_many(
                cur,
                "clause_version",
                [
                    {
                        "first_import_run_id": run_id,
                        **row,
                    }
                    for row in rows["clause_version"]
                ],
                json_columns={"structured_json"},
            )
            _insert_many(
                cur,
                "clause_version_observation",
                [
                    {
                        "first_import_run_id": run_id,
                        **row,
                    }
                    for row in rows["clause_version_observation"]
                ],
                json_columns={"source_locator"},
            )
            _insert_many(
                cur,
                "clause_version_block",
                rows["clause_version_block"],
                json_columns={"structural_path", "source_locator"},
            )
            _insert_many(
                cur,
                "clause_version_date",
                rows["clause_version_date"],
                json_columns={"source_locator"},
            )
            _insert_many(
                cur,
                "clause_version_edge",
                rows["clause_version_edge"],
            )
            _insert_many(
                cur,
                "clause_diff_hunk",
                rows["clause_diff_hunk"],
                json_columns={"inline_segments"},
            )
            _insert_many(
                cur,
                "coverage_assessment",
                [
                    {
                        "import_run_id": run_id,
                        "assessed_at": started_at,
                        **row,
                    }
                    for row in rows["coverage_assessment"]
                ],
                json_columns={"gap_reasons"},
            )

            identity_checks = {
                "clause": ("clause_id", rows["clause"]),
                "clause_version": (
                    "clause_version_id",
                    rows["clause_version"],
                ),
                "clause_version_observation": (
                    "observation_id",
                    rows["clause_version_observation"],
                ),
                "clause_version_edge": (
                    "edge_id",
                    rows["clause_version_edge"],
                ),
                "clause_diff_hunk": (
                    "hunk_id",
                    rows["clause_diff_hunk"],
                ),
            }
            for table, (key, expected_rows) in identity_checks.items():
                identifiers = [row[key] for row in expected_rows]
                cur.execute(
                    f"""
                    SELECT count(*)
                    FROM nhi_rule_history_clause.{table}
                    WHERE {key} = ANY(%s)
                    """,
                    (identifiers,),
                )
                if int(cur.fetchone()["count"]) != len(expected_rows):
                    raise ValueError(f"{table} identity/count parity failed")

            cur.execute(
                """
                UPDATE nhi_rule_history_clause.import_run
                SET state = 'sealed',
                    row_counts = %s,
                    output_sha256 = %s,
                    sealed_at = %s
                WHERE run_id = %s
                  AND state = 'loading'
                """,
                (
                    Jsonb(row_counts),
                    output_sha256,
                    datetime.now(timezone.utc),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("failed to seal clause import")

    return {
        "status": "sealed",
        "run_id": str(run_id),
        "source_set_sha256": source_set_sha256,
        "output_sha256": output_sha256,
        "row_counts": row_counts,
    }


def _base_hunks_for_import(
    conn: psycopg.Connection[Any],
    clause_import_run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT hunk.*
            FROM nhi_rule_history_clause.clause_diff_hunk hunk
            JOIN nhi_rule_history_clause.clause_version_edge edge
              ON edge.edge_id = hunk.edge_id
            JOIN nhi_rule_history_clause.clause clause
              ON clause.clause_id = edge.clause_id
            WHERE clause.first_import_run_id = %s
            ORDER BY edge.clause_id, edge.newer_first_observed_order,
                     hunk.hunk_order
            """,
            (clause_import_run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def rebuild_diff_presentations(
    conn: psycopg.Connection[Any],
    *,
    clause_import_run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    clause_import_run_id = clause_import_run_id or _latest_run_id(conn)
    hunks = _base_hunks_for_import(conn, clause_import_run_id)
    input_payload = {
        "clause_import_run_id": str(clause_import_run_id),
        "algorithm_version": DIFF_PRESENTATION_VERSION,
        "ignored_change_policy": list(IGNORED_CHANGE_POLICY),
        "hunks": [
            {
                "hunk_id": row["hunk_id"],
                "old_text_sha256": row["old_text_sha256"],
                "new_text_sha256": row["new_text_sha256"],
            }
            for row in hunks
        ],
    }
    input_sha256 = _sha256_text(_canonical_json(input_payload))
    run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nhi-rule-history-clause-diff:{input_sha256}",
    )
    presentations = [
        {
            "diff_run_id": run_id,
            "hunk_id": row["hunk_id"],
            **semantic_diff_presentation(
                row["old_text"],
                row["new_text"],
            ),
        }
        for row in hunks
    ]
    output_sha256 = _sha256_text(_canonical_json(presentations))
    started_at = datetime.now(timezone.utc)

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nhi-rule-history-clause-diff-presentation-v2",),
            )
            cur.execute(
                """
                SELECT state, input_sha256, output_sha256, hunk_count
                FROM nhi_rule_history_clause.diff_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            existing = cur.fetchone()
            if existing is not None:
                if (
                    existing["state"] != "sealed"
                    or existing["input_sha256"] != input_sha256
                    or existing["output_sha256"] != output_sha256
                    or int(existing["hunk_count"]) != len(presentations)
                ):
                    raise ValueError(
                        "existing semantic diff run does not match replay"
                    )
                return {
                    "status": "already_sealed",
                    "run_id": str(run_id),
                    "input_sha256": input_sha256,
                    "output_sha256": output_sha256,
                    "hunk_count": len(presentations),
                }

            cur.execute(
                """
                INSERT INTO nhi_rule_history_clause.diff_run (
                  run_id, clause_import_run_id, algorithm_version,
                  ignored_change_policy, state, input_sha256, started_at
                ) VALUES (%s, %s, %s, %s, 'loading', %s, %s)
                """,
                (
                    run_id,
                    clause_import_run_id,
                    DIFF_PRESENTATION_VERSION,
                    Jsonb(list(IGNORED_CHANGE_POLICY)),
                    input_sha256,
                    started_at,
                ),
            )
            _insert_many(
                cur,
                "diff_hunk_presentation",
                presentations,
                json_columns={
                    "inline_segments",
                    "ignored_change_classes",
                },
            )
            cur.execute(
                """
                SELECT count(*)
                FROM nhi_rule_history_clause.diff_hunk_presentation
                WHERE diff_run_id = %s
                """,
                (run_id,),
            )
            if int(cur.fetchone()["count"]) != len(presentations):
                raise ValueError("semantic diff presentation count mismatch")
            cur.execute(
                """
                UPDATE nhi_rule_history_clause.diff_run
                SET state = 'sealed',
                    output_sha256 = %s,
                    hunk_count = %s,
                    sealed_at = %s
                WHERE run_id = %s
                  AND state = 'loading'
                """,
                (
                    output_sha256,
                    len(presentations),
                    datetime.now(timezone.utc),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("failed to seal semantic diff run")

    return {
        "status": "sealed",
        "run_id": str(run_id),
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "hunk_count": len(presentations),
    }


def _normalized_keyword(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", value).strip().casefold(),
    )


def rebuild_reader_enrichment(
    conn: psycopg.Connection[Any],
    *,
    clause_import_run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Seal PG-canonical 0.4 semantic tags and a diff-bound summary."""

    clause_import_run_id = clause_import_run_id or _latest_run_id(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT clause.clause_id, version.normalized_text
            FROM nhi_rule_history_clause.clause clause
            JOIN LATERAL (
              SELECT normalized_text
              FROM nhi_rule_history_clause.clause_version
              WHERE clause_id = clause.clause_id
                AND first_import_run_id = %s
              ORDER BY state_order DESC
              LIMIT 1
            ) version ON true
            WHERE clause.first_import_run_id = %s
              AND clause.canonical_code = '0.4'
            """,
            (clause_import_run_id, clause_import_run_id),
        )
        clause_row = cur.fetchone()
        if clause_row is None:
            raise ValueError("clause 0.4 missing from sealed import")
        clause_id = str(clause_row["clause_id"])
        latest_text = str(clause_row["normalized_text"])
        cur.execute(
            """
            SELECT normalized_text
            FROM nhi_rule_history_clause.clause_version
            WHERE clause_id = %s
              AND first_import_run_id = %s
            ORDER BY state_order
            """,
            (clause_id, clause_import_run_id),
        )
        condition_source_text = "\n".join(
            str(row["normalized_text"]) for row in cur.fetchall()
        )

        cur.execute(
            """
            SELECT run_id, output_sha256, sealed_at
            FROM nhi_rule_history_clause.diff_run
            WHERE clause_import_run_id = %s
              AND state = 'sealed'
            ORDER BY sealed_at DESC, run_id DESC
            LIMIT 1
            """,
            (clause_import_run_id,),
        )
        diff_run = cur.fetchone()
        if diff_run is None:
            raise ValueError("sealed semantic diff run required")
        diff_run_id = diff_run["run_id"]
        source_diff_sha256 = str(diff_run["output_sha256"])
        summary_created_at = diff_run["sealed_at"]

        cur.execute(
            """
            SELECT edge.edge_id
            FROM nhi_rule_history_clause.clause_version_edge edge
            WHERE edge.clause_id = %s
            ORDER BY edge.newer_first_observed_order
            """,
            (clause_id,),
        )
        source_edge_ids = [str(row["edge_id"]) for row in cur.fetchall()]
        if not source_edge_ids:
            raise ValueError("clause 0.4 has no diff edges")

        expected_codes = sorted(
            {
                code
                for keyword in CHAPTER_04_DRUG_KEYWORDS
                for code in keyword["atc"]
            }
        )
        cur.execute(
            """
            SELECT atc_code, max(updated_at) AS source_updated_at
            FROM tw_drug.ref_atc
            WHERE atc_code = ANY(%s)
            GROUP BY atc_code
            ORDER BY atc_code
            """,
            (expected_codes,),
        )
        ref_rows = {
            str(row["atc_code"]): row["source_updated_at"]
            for row in cur.fetchall()
        }
        missing_codes = sorted(set(expected_codes) - set(ref_rows))
        if missing_codes:
            raise ValueError(
                f"ATC codes absent from PG reference: {missing_codes}"
            )

        semantic_tag_rows: list[dict[str, Any]] = []
        atc_rows: list[dict[str, Any]] = []
        for keyword in CHAPTER_04_DRUG_KEYWORDS:
            term = str(keyword["term"])
            normalized = _normalized_keyword(term)
            if normalized not in _normalized_keyword(latest_text):
                raise ValueError(
                    f"configured drug keyword absent from latest 0.4: {term}"
                )
            tag_id = _stable_id(
                "semantic-tag",
                {
                    "clause_id": clause_id,
                    "tag_type": "drug",
                    "normalized_tag": normalized,
                },
            )
            codes = tuple(str(code) for code in keyword["atc"])
            semantic_tag_rows.append(
                {
                    "enrichment_run_id": None,
                    "tag_id": tag_id,
                    "clause_id": clause_id,
                    "tag_text": term,
                    "normalized_tag": normalized,
                    "display_text": term,
                    "tag_type": "drug",
                    "entity_type": keyword["type"],
                    "match_mode": "exact_case_insensitive",
                    "resolution_status": (
                        "multiple_atc" if len(codes) > 1 else "resolved_atc"
                    ),
                    "provenance": {
                        "selection": "agent_curated_exact_term_from_clause_0.4",
                        "atc_validation": "tw_drug.ref_atc_pg_snapshot",
                        "public_scope": "only_codes_used_by_this_clause",
                    },
                }
            )
            for index, code in enumerate(codes):
                cur.execute(
                    """
                    SELECT count(*) AS evidence_count
                    FROM tw_drug.nhi_drugs
                    WHERE atc_code = %s
                      AND (
                        coalesce(name_en, '') ILIKE %s
                        OR coalesce(name_zh, '') ILIKE %s
                      )
                    """,
                    (code, f"%{term}%", f"%{term}%"),
                )
                evidence_count = int(cur.fetchone()["evidence_count"])
                if evidence_count:
                    mapping_basis = "nhi_product_name_atc_match"
                    review_status = "agent_verified"
                elif keyword["type"] in {"drug_class", "abbreviation"}:
                    mapping_basis = "nhi_rule_group_mapping"
                    review_status = "agent_curated"
                else:
                    mapping_basis = (
                        "nhi_atc_reference_plus_clause_context"
                    )
                    review_status = "agent_curated"
                atc_rows.append(
                    {
                        "enrichment_run_id": None,
                        "tag_id": tag_id,
                        "atc_code": code,
                        "is_primary": index == 0,
                        "mapping_basis": mapping_basis,
                        "evidence_count": evidence_count,
                        "source_updated_at": ref_rows[code],
                        "source_url": NHI_DRUG_LOOKUP_URL,
                        "review_status": review_status,
                    }
                )

        expected_icd_codes = sorted(
            {
                str(mapping[0])
                for disease in CHAPTER_04_DISEASE_TAGS
                for mapping in disease["mappings"]
            }
        )
        cur.execute(
            """
            SELECT DISTINCT ON (code)
              code, title, uri, release_id, imported_at
            FROM medical_knowledge.icd11_who
            WHERE code = ANY(%s)
            ORDER BY code, release_year DESC, release_id DESC
            """,
            (expected_icd_codes,),
        )
        icd_by_code = {
            str(row["code"]): dict(row) for row in cur.fetchall()
        }
        missing_icd_codes = sorted(
            set(expected_icd_codes) - set(icd_by_code)
        )
        if missing_icd_codes:
            raise ValueError(
                f"ICD-11 codes absent from private PG: {missing_icd_codes}"
            )

        public_icd_lookup_rows: list[dict[str, Any]] = []
        public_icd_code_rows: list[dict[str, Any]] = []
        private_icd_rows: list[dict[str, Any]] = []
        for disease in CHAPTER_04_DISEASE_TAGS:
            term = str(disease["term"])
            normalized = _normalized_keyword(term)
            if normalized not in _normalized_keyword(latest_text):
                raise ValueError(
                    f"configured disease tag absent from latest 0.4: {term}"
                )
            tag_id = _stable_id(
                "semantic-tag",
                {
                    "clause_id": clause_id,
                    "tag_type": "disease",
                    "normalized_tag": normalized,
                },
            )
            semantic_tag_rows.append(
                {
                    "enrichment_run_id": None,
                    "tag_id": tag_id,
                    "clause_id": clause_id,
                    "tag_text": term,
                    "normalized_tag": normalized,
                    "display_text": term,
                    "tag_type": "disease",
                    "entity_type": (
                        "abbreviation"
                        if term == "VTE"
                        else "disease"
                    ),
                    "match_mode": "exact_case_insensitive",
                    "resolution_status": "official_lookup_only",
                    "provenance": {
                        "selection": (
                            "agent_curated_exact_term_from_clause_0.4"
                        ),
                        "private_icd_content": (
                            "title_uri_and_reference_snapshot_stay_in_pg"
                        ),
                        "public_behavior": (
                            "show_code_relation_without_packaging_icd_content"
                        ),
                    },
                }
            )
            public_icd_lookup_rows.append(
                {
                    "enrichment_run_id": None,
                    "tag_id": tag_id,
                    "lookup_query": disease["query"],
                    "official_lookup_url": ICD11_CODING_TOOL_URL,
                    "icd11_release": "2026-01",
                    "language_tag": "en",
                    "mapping_status": (
                        "official_lookup_only_no_crosswalk"
                    ),
                    "icd11_code": None,
                    "icd11_uri": None,
                    "license_note": (
                        "Only the project-authored term-to-code relation is "
                        "public. ICD-11 titles, URIs, definitions, and the "
                        "reference snapshot remain private PostgreSQL data."
                    ),
                }
            )
            mappings = tuple(disease["mappings"])
            if not mappings:
                private_icd_rows.append(
                    {
                        "enrichment_run_id": None,
                        "tag_id": tag_id,
                        "candidate_rank": 1,
                        "mapping_status": "ambiguous",
                        "icd11_code": None,
                        "icd11_title": None,
                        "icd11_uri": None,
                        "icd11_release": "2024-01",
                        "confidence": 0.0,
                        "rationale": (
                            "The clause term is broader than one defensible "
                            "ICD-11 MMS category."
                        ),
                        "source_table": "medical_knowledge.icd11_who",
                        "verified_at": summary_created_at,
                    }
                )
                continue
            for rank, (code, status, confidence) in enumerate(
                mappings,
                start=1,
            ):
                icd_row = icd_by_code[str(code)]
                public_icd_code_rows.append(
                    {
                        "enrichment_run_id": None,
                        "tag_id": tag_id,
                        "candidate_rank": rank,
                        "icd11_code": code,
                        "mapping_status": status,
                        "confidence": confidence,
                        "display_note": (
                            "Agent-selected relation."
                            if status == "agent_selected"
                            else "Candidate relation; human review pending."
                        ),
                    }
                )
                private_icd_rows.append(
                    {
                        "enrichment_run_id": None,
                        "tag_id": tag_id,
                        "candidate_rank": rank,
                        "mapping_status": status,
                        "icd11_code": code,
                        "icd11_title": icd_row["title"],
                        "icd11_uri": icd_row["uri"],
                        "icd11_release": icd_row["release_id"],
                        "confidence": confidence,
                        "rationale": (
                            "Agentic term-to-code relation checked against "
                            "the private WHO ICD-11 PG snapshot; broad "
                            "source terms remain candidates."
                        ),
                        "source_table": "medical_knowledge.icd11_who",
                        "verified_at": icd_row["imported_at"],
                    }
                )

        condition_rows: list[dict[str, Any]] = []
        normalized_condition_source = _normalized_keyword(
            condition_source_text
        )

        def append_condition(
            marker_text: str,
            semantic_role: str,
            *,
            selection: str,
        ) -> None:
            normalized_marker = _normalized_keyword(marker_text)
            condition_rows.append(
                {
                    "enrichment_run_id": None,
                    "marker_id": _stable_id(
                        "condition-marker",
                        {
                            "clause_id": clause_id,
                            "normalized_marker": normalized_marker,
                            "semantic_role": semantic_role,
                        },
                    ),
                    "clause_id": clause_id,
                    "marker_text": marker_text,
                    "normalized_marker": normalized_marker,
                    "semantic_role": semantic_role,
                    "match_mode": "exact_longest_first",
                    "provenance": {
                        "selection": selection,
                        "rendering": "longest_match_wins",
                    },
                }
            )

        for marker_text, semantic_role in CHAPTER_04_CONDITION_MARKERS:
            normalized_marker = _normalized_keyword(marker_text)
            if normalized_marker not in normalized_condition_source:
                continue
            append_condition(
                marker_text,
                semantic_role,
                selection=(
                    "agent_curated_constraint_lexicon_for_clause_0.4"
                ),
            )
        duration_markers = sorted(
            set(DURATION_MARKER_PATTERN.findall(condition_source_text)),
            key=lambda value: (-len(value), value),
        )
        for marker_text in duration_markers:
            append_condition(
                marker_text,
                "duration",
                selection=(
                    "programmatic_quantity_plus_time_unit_across_clause_history"
                ),
            )

    input_payload = {
        "clause_import_run_id": str(clause_import_run_id),
        "diff_run_id": str(diff_run_id),
        "generator_version": READER_ENRICHMENT_VERSION,
        "drug_keywords": list(CHAPTER_04_DRUG_KEYWORDS),
        "disease_tags": list(CHAPTER_04_DISEASE_TAGS),
        "condition_markers": list(CHAPTER_04_CONDITION_MARKERS),
        "duration_marker_pattern": DURATION_MARKER_PATTERN.pattern,
        "summary_markdown": CHAPTER_04_AGENT_SUMMARY,
    }
    input_sha256 = _sha256_text(_canonical_json(input_payload))
    run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nhi-rule-history-reader-enrichment:{input_sha256}",
    )
    for row in semantic_tag_rows:
        row["enrichment_run_id"] = run_id
    for row in atc_rows:
        row["enrichment_run_id"] = run_id
    for row in public_icd_lookup_rows:
        row["enrichment_run_id"] = run_id
    for row in public_icd_code_rows:
        row["enrichment_run_id"] = run_id
    for row in private_icd_rows:
        row["enrichment_run_id"] = run_id
    for row in condition_rows:
        row["enrichment_run_id"] = run_id
    summary_rows = [
        {
            "enrichment_run_id": run_id,
            "summary_id": _stable_id(
                "agent-summary",
                {
                    "clause_id": clause_id,
                    "source_diff_sha256": source_diff_sha256,
                    "summary_markdown": CHAPTER_04_AGENT_SUMMARY,
                },
            ),
            "clause_id": clause_id,
            "language_tag": "zh-TW",
            "summary_markdown": CHAPTER_04_AGENT_SUMMARY,
            "source_edge_ids": source_edge_ids,
            "source_diff_sha256": source_diff_sha256,
            "generation_method": "pure_agentic_from_structured_diff",
            "generator_id": "openai-codex",
            "review_status": "agent_generated_unreviewed",
            "created_at": summary_created_at,
        }
    ]
    output_payload = {
        "semantic_tags": semantic_tag_rows,
        "tag_atc": atc_rows,
        "public_icd11_lookup": public_icd_lookup_rows,
        "public_icd11_codes": public_icd_code_rows,
        "private_icd11": private_icd_rows,
        "condition_markers": condition_rows,
        "summaries": summary_rows,
    }
    output_sha256 = _sha256_text(_canonical_json(output_payload))
    started_at = datetime.now(timezone.utc)

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nhi-rule-history-clause-reader-enrichment-v3",),
            )
            cur.execute(
                """
                SELECT state, input_sha256, output_sha256,
                       semantic_tag_count, tag_atc_count,
                       tag_icd11_lookup_count, condition_marker_count,
                       summary_count
                FROM nhi_rule_history_clause.reader_enrichment_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            existing = cur.fetchone()
            expected_counts = (
                len(semantic_tag_rows),
                len(atc_rows),
                len(public_icd_lookup_rows),
                len(condition_rows),
                len(summary_rows),
            )
            if existing is not None:
                if (
                    existing["state"] != "sealed"
                    or existing["input_sha256"] != input_sha256
                    or existing["output_sha256"] != output_sha256
                ):
                    raise ValueError(
                        "existing reader enrichment does not match replay"
                    )
                actual_counts = (
                    int(existing["semantic_tag_count"]),
                    int(existing["tag_atc_count"]),
                    int(existing["tag_icd11_lookup_count"]),
                    int(existing["condition_marker_count"]),
                    int(existing["summary_count"]),
                )
                if actual_counts != expected_counts:
                    raise ValueError(
                        "existing reader enrichment count mismatch"
                    )
                return {
                    "status": "already_sealed",
                    "run_id": str(run_id),
                    "input_sha256": input_sha256,
                    "output_sha256": output_sha256,
                    "semantic_tag_count": len(semantic_tag_rows),
                    "tag_atc_count": len(atc_rows),
                    "tag_icd11_lookup_count": len(
                        public_icd_lookup_rows
                    ),
                    "condition_marker_count": len(condition_rows),
                    "summary_count": len(summary_rows),
                }

            cur.execute(
                """
                INSERT INTO
                  nhi_rule_history_clause.reader_enrichment_run (
                    run_id, clause_import_run_id, diff_run_id,
                    generator_version, state, input_sha256, started_at
                  ) VALUES (%s, %s, %s, %s, 'loading', %s, %s)
                """,
                (
                    run_id,
                    clause_import_run_id,
                    diff_run_id,
                    READER_ENRICHMENT_VERSION,
                    input_sha256,
                    started_at,
                ),
            )
            _insert_many(
                cur,
                "clause_semantic_tag",
                semantic_tag_rows,
                json_columns={"provenance"},
            )
            _insert_many(
                cur,
                "clause_semantic_tag_atc",
                atc_rows,
            )
            _insert_many(
                cur,
                "clause_semantic_tag_icd11_lookup",
                public_icd_lookup_rows,
            )
            _insert_many(
                cur,
                "clause_semantic_tag_icd11_code",
                public_icd_code_rows,
            )
            _insert_many(
                cur,
                "clause_semantic_tag_icd11_private",
                private_icd_rows,
            )
            _insert_many(
                cur,
                "clause_condition_marker",
                condition_rows,
                json_columns={"provenance"},
            )
            _insert_many(
                cur,
                "agent_history_summary",
                summary_rows,
                json_columns={"source_edge_ids"},
            )
            cur.execute(
                """
                UPDATE nhi_rule_history_clause.reader_enrichment_run
                SET state = 'sealed',
                    output_sha256 = %s,
                    semantic_tag_count = %s,
                    tag_atc_count = %s,
                    tag_icd11_lookup_count = %s,
                    condition_marker_count = %s,
                    summary_count = %s,
                    sealed_at = %s
                WHERE run_id = %s
                  AND state = 'loading'
                """,
                (
                    output_sha256,
                    len(semantic_tag_rows),
                    len(atc_rows),
                    len(public_icd_lookup_rows),
                    len(condition_rows),
                    len(summary_rows),
                    datetime.now(timezone.utc),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("failed to seal reader enrichment run")

    return {
        "status": "sealed",
        "run_id": str(run_id),
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "semantic_tag_count": len(semantic_tag_rows),
        "tag_atc_count": len(atc_rows),
        "tag_icd11_lookup_count": len(public_icd_lookup_rows),
        "public_icd11_code_count": len(public_icd_code_rows),
        "private_icd11_row_count": len(private_icd_rows),
        "condition_marker_count": len(condition_rows),
        "summary_count": len(summary_rows),
    }


EXPORT_TABLES = (
    "import_run",
    "diff_run",
    "reader_enrichment_run",
    "source_edition",
    "chapter",
    "clause",
    "clause_version",
    "clause_version_observation",
    "clause_version_block",
    "clause_version_date",
    "clause_version_edge",
    "clause_diff_hunk",
    "diff_hunk_presentation",
    "clause_semantic_tag",
    "clause_semantic_tag_atc",
    "clause_semantic_tag_icd11_lookup",
    "clause_semantic_tag_icd11_code",
    "clause_condition_marker",
    "agent_history_summary",
    "coverage_assessment",
)


def _latest_run_id(conn: psycopg.Connection[Any]) -> uuid.UUID:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT run_id
            FROM nhi_rule_history_clause.import_run
            WHERE state = 'sealed'
            ORDER BY sealed_at DESC, run_id
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("no sealed single-clause import")
    return row["run_id"]


def _export_rows(
    conn: psycopg.Connection[Any],
    table: str,
    *,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    if table == "source_edition":
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  version.version_id AS source_edition_version_id,
                  import_run.run_id AS edition_import_run_id,
                  version.chronology_order,
                  version.version_label AS edition_label,
                  date_fact.date_role AS official_date_role,
                  date_fact.raw_value AS official_date_raw_value,
                  date_fact.date_value AS official_date_value,
                  date_fact.date_precision AS official_date_precision,
                  date_fact.legal_effective_status,
                  document.official_url,
                  document.source_page_url,
                  document.artifact_sha256
                FROM nhi_rule_history_edition.rule_version version
                JOIN nhi_rule_history_edition.source_document document
                  ON document.document_id = version.primary_document_id
                JOIN nhi_rule_history_edition.rule_version_date date_fact
                  ON date_fact.version_id = version.version_id
                  AND date_fact.date_role IN (
                    'official_edition_label', 'official_update_date'
                  )
                JOIN nhi_rule_history_clause.import_run clause_run
                  ON clause_run.run_id = %s
                JOIN nhi_rule_history_edition.import_run import_run
                  ON import_run.run_id =
                    clause_run.edition_import_run_id
                WHERE version.rule_id = %s
                ORDER BY version.chronology_order
                """,
                (run_id, EDITION_RULE_ID),
            )
            return [dict(row) for row in cur.fetchall()]

    filters = {
        "import_run": "run_id = %s",
        "diff_run": (
            "run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.diff_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "reader_enrichment_run": (
            "run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "chapter": (
            "chapter_id IN (SELECT chapter_id "
            "FROM nhi_rule_history_clause.clause "
            "WHERE first_import_run_id = %s)"
        ),
        "clause": "first_import_run_id = %s",
        "clause_version": "first_import_run_id = %s",
        "clause_version_observation": "first_import_run_id = %s",
        "clause_version_block": (
            "clause_version_id IN (SELECT clause_version_id "
            "FROM nhi_rule_history_clause.clause_version "
            "WHERE first_import_run_id = %s)"
        ),
        "clause_version_date": (
            "clause_version_id IN (SELECT clause_version_id "
            "FROM nhi_rule_history_clause.clause_version "
            "WHERE first_import_run_id = %s)"
        ),
        "clause_version_edge": (
            "clause_id IN (SELECT clause_id "
            "FROM nhi_rule_history_clause.clause "
            "WHERE first_import_run_id = %s)"
        ),
        "clause_diff_hunk": (
            "edge_id IN (SELECT edge_id "
            "FROM nhi_rule_history_clause.clause_version_edge "
            "WHERE clause_id IN (SELECT clause_id "
            "FROM nhi_rule_history_clause.clause "
            "WHERE first_import_run_id = %s))"
        ),
        "diff_hunk_presentation": (
            "diff_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.diff_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "clause_semantic_tag": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "clause_semantic_tag_atc": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "clause_semantic_tag_icd11_lookup": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "clause_semantic_tag_icd11_code": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "clause_condition_marker": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "agent_history_summary": (
            "enrichment_run_id = (SELECT run_id "
            "FROM nhi_rule_history_clause.reader_enrichment_run "
            "WHERE clause_import_run_id = %s AND state = 'sealed' "
            "ORDER BY sealed_at DESC, run_id DESC LIMIT 1)"
        ),
        "coverage_assessment": "import_run_id = %s",
    }
    order_by = {
        "import_run": "started_at",
        "diff_run": "sealed_at, run_id",
        "reader_enrichment_run": "sealed_at, run_id",
        "chapter": "chapter_id",
        "clause": "ordinal_number",
        "clause_version": "clause_id, state_order",
        "clause_version_observation": "clause_id, chronology_order",
        "clause_version_block": "clause_version_id, block_order",
        "clause_version_date": (
            "clause_version_id, date_value, date_fact_id"
        ),
        "clause_version_edge": (
            "clause_id, newer_first_observed_order"
        ),
        "clause_diff_hunk": "edge_id, hunk_order",
        "diff_hunk_presentation": "diff_run_id, hunk_id",
        "clause_semantic_tag": (
            "enrichment_run_id, clause_id, tag_type, normalized_tag"
        ),
        "clause_semantic_tag_atc": (
            "enrichment_run_id, tag_id, is_primary DESC, atc_code"
        ),
        "clause_semantic_tag_icd11_lookup": (
            "enrichment_run_id, tag_id"
        ),
        "clause_semantic_tag_icd11_code": (
            "enrichment_run_id, tag_id, candidate_rank"
        ),
        "clause_condition_marker": (
            "enrichment_run_id, clause_id, normalized_marker"
        ),
        "agent_history_summary": (
            "enrichment_run_id, clause_id, summary_id"
        ),
        "coverage_assessment": "clause_id",
    }
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT *
            FROM nhi_rule_history_clause.{table}
            WHERE {filters[table]}
            ORDER BY {order_by[table]}
            """,
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def export_jsonl(
    conn: psycopg.Connection[Any],
    *,
    output_dir: Path,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    run_id = run_id or _latest_run_id(conn)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    files: dict[str, dict[str, Any]] = {}
    for table in EXPORT_TABLES:
        rows = _export_rows(conn, table, run_id=run_id)
        path = output_dir / f"{table}.jsonl"
        content = "".join(f"{_canonical_json(row)}\n" for row in rows)
        path.write_text(content, encoding="utf-8")
        counts[table] = len(rows)
        files[path.name] = {
            "rows": len(rows),
            "bytes": path.stat().st_size,
            "sha256": _sha256_bytes(path.read_bytes()),
        }
    manifest = {
        "schema": "nhi-rule-history/clause-export/v1",
        "generated_from": "PostgreSQL nhi_rule_history_clause",
        "run_id": str(run_id),
        "canonical_version_unit": "single_clause",
        "postgresql_is_authority": True,
        "legal_history_complete": False,
        "counts": counts,
        "files": files,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _reader_data(
    conn: psycopg.Connection[Any],
    *,
    run_id: uuid.UUID,
) -> dict[str, list[dict[str, Any]]]:
    return {
        table: _export_rows(conn, table, run_id=run_id)
        for table in EXPORT_TABLES
    }


def reader_projections(
    conn: psycopg.Connection[Any],
    *,
    run_id: uuid.UUID | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    run_id = run_id or _latest_run_id(conn)
    data = _reader_data(conn, run_id=run_id)
    chapter = data["chapter"][0]
    editions = {
        row["source_edition_version_id"]: row
        for row in data["source_edition"]
    }
    clauses = sorted(data["clause"], key=lambda row: row["ordinal_number"])
    versions_by_clause: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for version in data["clause_version"]:
        versions_by_clause[str(version["clause_id"])].append(version)
    observations_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in data["clause_version_observation"]:
        observations_by_version[
            str(observation["clause_version_id"])
        ].append(observation)
    blocks_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in data["clause_version_block"]:
        blocks_by_version[str(block["clause_version_id"])].append(block)
    dates_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for date_row in data["clause_version_date"]:
        dates_by_version[str(date_row["clause_version_id"])].append(date_row)
    edges_by_clause: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in data["clause_version_edge"]:
        edges_by_clause[str(edge["clause_id"])].append(edge)
    hunks_by_edge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for hunk in data["clause_diff_hunk"]:
        hunks_by_edge[str(hunk["edge_id"])].append(hunk)
    if not data["diff_run"]:
        raise ValueError("no sealed semantic diff presentation run")
    current_diff_run = data["diff_run"][-1]
    current_diff_run_id = str(current_diff_run["run_id"])
    presentation_by_hunk = {
        str(row["hunk_id"]): row
        for row in data["diff_hunk_presentation"]
        if str(row["diff_run_id"]) == current_diff_run_id
    }
    if len(presentation_by_hunk) != len(data["clause_diff_hunk"]):
        raise ValueError("semantic diff presentation coverage mismatch")
    diff_policy = {
        "algorithm_version": current_diff_run["algorithm_version"],
        "ignored_change_policy": current_diff_run[
            "ignored_change_policy"
        ],
    }
    if not data["reader_enrichment_run"]:
        raise ValueError("no sealed reader enrichment run")
    current_enrichment_run = data["reader_enrichment_run"][-1]
    current_enrichment_run_id = str(current_enrichment_run["run_id"])
    tags_by_clause: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["clause_semantic_tag"]:
        if str(row["enrichment_run_id"]) == current_enrichment_run_id:
            tags_by_clause[str(row["clause_id"])].append(row)
    atc_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["clause_semantic_tag_atc"]:
        if str(row["enrichment_run_id"]) == current_enrichment_run_id:
            atc_by_tag[str(row["tag_id"])].append(row)
    icd_lookup_by_tag = {
        str(row["tag_id"]): row
        for row in data["clause_semantic_tag_icd11_lookup"]
        if str(row["enrichment_run_id"]) == current_enrichment_run_id
    }
    icd_codes_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["clause_semantic_tag_icd11_code"]:
        if str(row["enrichment_run_id"]) == current_enrichment_run_id:
            icd_codes_by_tag[str(row["tag_id"])].append(row)
    conditions_by_clause: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["clause_condition_marker"]:
        if str(row["enrichment_run_id"]) == current_enrichment_run_id:
            conditions_by_clause[str(row["clause_id"])].append(row)
    summary_by_clause = {
        str(row["clause_id"]): row
        for row in data["agent_history_summary"]
        if str(row["enrichment_run_id"]) == current_enrichment_run_id
    }
    coverage_by_clause = {
        row["clause_id"]: row for row in data["coverage_assessment"]
    }

    pages: dict[str, dict[str, Any]] = {}
    index_rows: list[dict[str, Any]] = []
    for clause in clauses:
        clause_id = str(clause["clause_id"])
        canonical_code = str(clause["canonical_code"])
        versions = sorted(
            versions_by_clause[clause_id],
            key=lambda row: row["state_order"],
        )
        version_by_id = {
            str(version["clause_version_id"]): version
            for version in versions
        }
        latest = versions[-1]

        def observation_summary(version_id: str) -> list[dict[str, Any]]:
            rows = sorted(
                observations_by_version[version_id],
                key=lambda row: row["chronology_order"],
            )
            return [
                {
                    "observation_id": row["observation_id"],
                    "edition_label": row["edition_label"],
                    "chronology_order": row["chronology_order"],
                    "source_designation_raw": row[
                        "source_designation_raw"
                    ],
                    "source": {
                        "official_url": editions[
                            row["source_edition_version_id"]
                        ]["official_url"],
                        "source_page_url": editions[
                            row["source_edition_version_id"]
                        ]["source_page_url"],
                        "artifact_sha256": editions[
                            row["source_edition_version_id"]
                        ]["artifact_sha256"],
                    },
                }
                for row in rows
            ]

        def transition_date_label(
            older_id: str,
            newer_id: str,
        ) -> dict[str, Any]:
            older_dates = {
                str(row["date_value"])
                for row in dates_by_version[older_id]
                if row["date_value"] is not None
            }
            newly_observed = sorted(
                (
                    row
                    for row in dates_by_version[newer_id]
                    if row["date_value"] is not None
                    and str(row["date_value"]) not in older_dates
                ),
                key=lambda row: (
                    str(row["date_value"]),
                    str(row["raw_value"]),
                ),
            )
            if not newly_observed:
                return {
                    "label": None,
                    "basis": "source_edition_fallback",
                    "raw_values": [],
                    "legal_effective_status": None,
                }
            selected = newly_observed[-1]
            match = ROC_DATE_PART_RE.search(str(selected["raw_value"]))
            if match is None:
                label = str(selected["raw_value"])
            else:
                label = (
                    f"{int(match.group('year'))}/"
                    f"{int(match.group('month'))}"
                )
            return {
                "label": label,
                "basis": "new_text_date_annotation",
                "raw_values": [
                    str(row["raw_value"]) for row in newly_observed
                ],
                "legal_effective_status": selected[
                    "legal_effective_status"
                ],
            }

        transitions: list[dict[str, Any]] = []
        for edge in sorted(
            edges_by_clause[clause_id],
            key=lambda row: row["newer_first_observed_order"],
            reverse=True,
        ):
            older_id = str(edge["older_clause_version_id"])
            newer_id = str(edge["newer_clause_version_id"])
            transitions.append(
                {
                    "edge_id": edge["edge_id"],
                    "older": {
                        "clause_version_id": older_id,
                        "state_order": version_by_id[older_id][
                            "state_order"
                        ],
                        "observed_editions": observation_summary(older_id),
                    },
                    "newer": {
                        "clause_version_id": newer_id,
                        "state_order": version_by_id[newer_id][
                            "state_order"
                        ],
                        "observed_editions": observation_summary(newer_id),
                    },
                    "adjacency_basis": edge["adjacency_basis"],
                    "legal_predecessor_status": edge[
                        "legal_predecessor_status"
                    ],
                    "crosses_known_gap": edge["crosses_known_gap"],
                    "display_date": transition_date_label(
                        older_id,
                        newer_id,
                    ),
                    "hunks": [
                        {
                            "hunk_order": hunk["hunk_order"],
                            "source_change_kind": hunk["change_kind"],
                            "change_kind": presentation_by_hunk[
                                str(hunk["hunk_id"])
                            ]["semantic_change_kind"],
                            "context_label": hunk["context_label"],
                            "old_text": hunk["old_text"],
                            "new_text": hunk["new_text"],
                            "inline_segments": presentation_by_hunk[
                                str(hunk["hunk_id"])
                            ]["inline_segments"],
                            "ignored_change_classes": (
                                presentation_by_hunk[str(hunk["hunk_id"])][
                                    "ignored_change_classes"
                                ]
                            ),
                            "display_note": presentation_by_hunk[
                                str(hunk["hunk_id"])
                            ]["display_note"],
                        }
                        for hunk in sorted(
                            hunks_by_edge[str(edge["edge_id"])],
                            key=lambda row: row["hunk_order"],
                        )
                        if presentation_by_hunk[str(hunk["hunk_id"])][
                            "semantic_change_kind"
                        ]
                        != "format_only"
                    ],
                }
            )

        latest_id = str(latest["clause_version_id"])
        latest_observations = observation_summary(latest_id)
        coverage = coverage_by_clause[clause_id]
        semantic_tags = []
        for tag in sorted(
            tags_by_clause[clause_id],
            key=lambda row: (
                -len(str(row["tag_text"])),
                str(row["normalized_tag"]),
            ),
        ):
            tag_id = str(tag["tag_id"])
            tag_payload: dict[str, Any] = {
                "tag_id": tag_id,
                "tag_text": tag["tag_text"],
                "display_text": tag["display_text"],
                "tag_type": tag["tag_type"],
                "entity_type": tag["entity_type"],
                "resolution_status": tag["resolution_status"],
                "internal_url": (
                    f"./tag.html?rule={canonical_code}&tag={tag_id}"
                ),
            }
            if tag["tag_type"] == "drug":
                tag_payload["terminology"] = {
                    "system": "ATC",
                    "codes": [
                        {
                            "code": row["atc_code"],
                            "is_primary": row["is_primary"],
                            "mapping_basis": row["mapping_basis"],
                            "review_status": row["review_status"],
                            "source_url": row["source_url"],
                        }
                        for row in sorted(
                            atc_by_tag[tag_id],
                            key=lambda row: (
                                not bool(row["is_primary"]),
                                str(row["atc_code"]),
                            ),
                        )
                    ],
                }
            else:
                lookup = icd_lookup_by_tag[tag_id]
                tag_payload["terminology"] = {
                    "system": "ICD-11",
                    "public_export": "code_only_no_icd_content",
                    "lookup_query": lookup["lookup_query"],
                    "official_lookup_url": lookup[
                        "official_lookup_url"
                    ],
                    "release": lookup["icd11_release"],
                    "private_pg_mapping": True,
                    "codes": [
                        {
                            "code": row["icd11_code"],
                            "candidate_rank": row["candidate_rank"],
                            "mapping_status": row["mapping_status"],
                            "confidence": float(row["confidence"]),
                            "display_note": row["display_note"],
                        }
                        for row in sorted(
                            icd_codes_by_tag.get(tag_id, []),
                            key=lambda row: int(row["candidate_rank"]),
                        )
                    ],
                }
            semantic_tags.append(tag_payload)
        summary = summary_by_clause.get(clause_id)
        page = {
            "schema": "nhi-rule-history/single-clause-reader/v1",
            "generated_from": "PostgreSQL nhi_rule_history_clause",
            "canonical_version_unit": "single_clause",
            "diff_policy": diff_policy,
            "chapter": {
                "chapter_id": chapter["chapter_id"],
                "display_label": chapter["display_label"],
                "source_designation_raw": chapter[
                    "source_designation_raw"
                ],
                "navigation_code": chapter["navigation_code"],
                "navigation_code_origin": chapter[
                    "navigation_code_origin"
                ],
            },
            "clause": {
                "clause_id": clause_id,
                "canonical_code": canonical_code,
                "code_origin": clause["code_origin"],
                "ordinal_number": clause["ordinal_number"],
                "identity_basis": clause["identity_basis"],
                "identity_status": clause["identity_status"],
                "display_title": latest["display_title"],
            },
            "latest": {
                "clause_version_id": latest_id,
                "state_order": latest["state_order"],
                "display_title": latest["display_title"],
                "full_text_blocks": [
                    {
                        "block_order": block["block_order"],
                        "block_kind": block["block_kind"],
                        "structural_path": block["structural_path"],
                        "render_kind": (
                            "clause_heading"
                            if block["block_order"] == 0
                            else (
                                "subsection"
                                if re.match(
                                    r"^（[一二三四五六七八九十]+）",
                                    str(block["normalized_text"]),
                                )
                                else (
                                    "list_item"
                                    if re.match(
                                        r"^\d+\.",
                                        str(block["normalized_text"]),
                                    )
                                    else "paragraph"
                                )
                            )
                        ),
                        "text": block["normalized_text"],
                    }
                    for block in sorted(
                        blocks_by_version[latest_id],
                        key=lambda row: row["block_order"],
                    )
                ],
                "observed_editions": latest_observations,
                "date_annotations": [
                    {
                        "raw_value": row["raw_value"],
                        "date_value": row["date_value"],
                        "legal_effective_status": row[
                            "legal_effective_status"
                        ],
                    }
                    for row in dates_by_version[latest_id]
                ],
                "legal_effective_status": latest[
                    "legal_effective_status"
                ],
            },
            "semantic_tags": semantic_tags,
            "condition_markers": [
                {
                    "marker_id": row["marker_id"],
                    "marker_text": row["marker_text"],
                    "semantic_role": row["semantic_role"],
                }
                for row in sorted(
                    conditions_by_clause[clause_id],
                    key=lambda row: (
                        -len(str(row["marker_text"])),
                        str(row["normalized_marker"]),
                    ),
                )
            ],
            "agent_history_summary": (
                None
                if summary is None
                else {
                    "summary_markdown": summary["summary_markdown"],
                    "generation_method": summary[
                        "generation_method"
                    ],
                    "generator_id": summary["generator_id"],
                    "review_status": summary["review_status"],
                    "source_diff_sha256": summary[
                        "source_diff_sha256"
                    ],
                }
            ),
            "transitions": transitions,
            "coverage": {
                key: coverage[key]
                for key in (
                    "declared_edition_count",
                    "observed_edition_count",
                    "first_observed_order",
                    "last_observed_order",
                    "version_state_count",
                    "unique_comparison_text_count",
                    "version_edge_count",
                    "observed_presence_contiguous",
                    "declared_edition_set_complete",
                    "official_source_universe_closed",
                    "legal_history_complete",
                    "status",
                    "gap_reasons",
                )
            },
        }
        pages[canonical_code] = page
        all_text = " ".join(
            str(version["normalized_text"]) for version in versions
        )
        index_rows.append(
            {
                "clause_id": clause_id,
                "canonical_code": canonical_code,
                "ordinal_number": clause["ordinal_number"],
                "display_title": latest["display_title"],
                "latest_excerpt": str(latest["normalized_text"])[:160],
                "observed_edition_count": coverage[
                    "observed_edition_count"
                ],
                "version_state_count": coverage["version_state_count"],
                "reader_query": f"?rule={canonical_code}",
                "search_text": _comparison_key(
                    f"{canonical_code} {latest['display_title']} {all_text}"
                ),
            }
        )

    index = {
        "schema": "nhi-rule-history/single-clause-index/v1",
        "generated_from": "PostgreSQL nhi_rule_history_clause",
        "canonical_version_unit": "single_clause",
        "diff_policy": diff_policy,
        "default_clause_code": "0.4",
        "chapter": {
            "display_label": chapter["display_label"],
            "navigation_code": chapter["navigation_code"],
            "navigation_code_origin": chapter["navigation_code_origin"],
        },
        "clauses": index_rows,
    }
    return index, pages


def write_reader_projections(
    conn: psycopg.Connection[Any],
    *,
    output_dir: Path,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    index, pages = reader_projections(conn, run_id=run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "index.json"
    index_path.write_text(
        json.dumps(
            index,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    files: dict[str, dict[str, Any]] = {
        index_path.name: {
            "bytes": index_path.stat().st_size,
            "sha256": _sha256_bytes(index_path.read_bytes()),
        }
    }
    for code, page in pages.items():
        path = output_dir / f"{code}.json"
        path.write_text(
            json.dumps(
                page,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
            + "\n",
            encoding="utf-8",
        )
        files[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256_bytes(path.read_bytes()),
        }
    return {
        "output_dir": output_dir.name,
        "clause_count": len(pages),
        "files": files,
    }


def build_sqlite(
    *,
    jsonl_dir: Path,
    schema_path: Path,
    output: Path,
    force: bool = False,
) -> dict[str, Any]:
    if output.exists():
        if not force:
            raise ValueError(f"output exists: {output}")
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    counts: dict[str, int] = {}
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        for table in EXPORT_TABLES:
            rows = [
                json.loads(line)
                for line in (jsonl_dir / f"{table}.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            columns = {
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            for row in rows:
                unknown = set(row) - columns
                if unknown:
                    raise ValueError(
                        f"{table}: unknown SQLite columns {sorted(unknown)}"
                    )
                keys = sorted(row)
                values: list[Any] = []
                for key in keys:
                    value = row[key]
                    if isinstance(value, bool):
                        value = int(value)
                    elif isinstance(value, (dict, list)):
                        value = _canonical_json(value)
                    values.append(value)
                conn.execute(
                    f"""
                    INSERT INTO "{table}" (
                      {", ".join(f'"{key}"' for key in keys)}
                    ) VALUES ({", ".join("?" for _ in keys)})
                    """,
                    values,
                )
            counts[table] = len(rows)
        conn.commit()
        foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
        if foreign_keys:
            raise ValueError(f"SQLite foreign_key_check: {foreign_keys[:3]}")
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check: {integrity}")
    except Exception:
        conn.close()
        if output.exists():
            output.unlink()
        raise
    finally:
        if output.exists():
            conn.close()
    return {
        "output": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256_bytes(output.read_bytes()),
        "counts": counts,
        "foreign_key_check": "passed",
        "integrity_check": "passed",
    }
