# ATC 與 ICD-11 linkage

條文不是天然等於一個藥，也不是天然等於一個疾病。linkage 必須保留「哪段
文字、哪個產品、哪個成分、哪個版本」的證據，而不能只在條文表上塞一個
`atc_code` 或 `icd_code`。

## 藥品層

```text
rule_snapshot
  -> rule_drug_link
  -> drug_concept
  -> drug_identifier
  -> drug_atc_link
```

### `drug_concept`

表示：

- 單一成分；
- 複方；
- 特定劑型／途徑；
- 特定強度；
- 商品或健保品項；
- 藥理／治療群組。

不同 object 不強迫合併。ATC 可能依途徑、強度或主用途不同，因此
`drug_atc_link` 是多對多且 versioned。

### ATC 來源順序

1. 健保署「健保用藥品項查詢項目檔」本身提供的 ATC code。
2. TFDA 許可證與其 ATC 欄位。
3. WHO ATC/DDD 官方 index 的人工核對。
4. 名稱／INN 自動候選，只能是 `candidate`。

每筆 mapping 保存：

```text
drug_concept_id
atc_code
atc_version
relation_type
source_system
source_record_id
source_url
source_text
is_primary
confidence
review_status
reviewed_at
```

repo 不鏡像完整 ATC 名稱、階層與 DDD。對外可用 ATC code 搜尋並連回官方
index；需要完整詞庫的使用者自行依其授權 hydrate。

## 適應症層

```text
rule_snapshot
  -> rule_indication_link
  -> indication
  -> external_concept_link
```

`indication` 保存官方條文原始 span、normalized text、包含／排除條件與
source offsets。原文仍是權威；外部分類只是額外索引。

## ICD-11

WHO ICD-11 條款允許在軟體內使用 classification，但要求保留 code、title、
URI 與 attribution；更重要的是，WHO 明定「其他分類／術語與 ICD-11 的
mapping 或 crosswalk」需要另外書面同意。

因此分三階段：

1. **現在公開**：linkage schema、條文 indication spans、WHO API client、
   空白／synthetic example。
2. **內部研究**：候選 URI 與人工審查可在不發布的 `licensed/` projection
   進行。
3. **取得 WHO agreement 後**：才發布 populated ICD-11 linkage rows，並在
   release manifest 記錄 agreement/version/citation。

沒有 agreement 時，CI 必須拒絕任何 `system='ICD11'` 且
`publication_status='publishable'` 的 populated row。

## 搜尋

讀者搜尋不要求背條文號。公開 read model 應索引：

- 健保藥品代碼、TFDA 許可證；
- 商品名、成分名、INN、常見別名；
- ATC code；
- 適應症原文與 normalized text；
- 條文全文；
- 章節與歷史 designation。

搜尋命中後回到 `rule_identity` 頁，再以 `rule_snapshot` 顯示時間序列。
