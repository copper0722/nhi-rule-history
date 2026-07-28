# ATC 與 ICD-11 linkage

條文不是天然等於一個藥，也不是天然等於一個疾病。linkage 必須保留「哪段
文字、哪個產品、哪個成分、哪個版本」的證據，而不能只在條文表上塞一個
`atc_code` 或 `icd_code`。

## 官方品項來源

本專案把兩個健保署介面視為同一來源家族、不同用途：

- [`INAE3000` 健保用藥品項網路查詢](https://info.nhi.gov.tw/INAE3000/INAE3000S01)
  是讀者與高頻現況核對介面。其前端以
  `POST /api/INAE3000/INAE3000S01/SQL0001` 分頁查詢；既有 runtime
  正是從這個 API 保存品項、ATC、`PAY_CODE_LIST` 與 `pdfList`，再建立
  品項到條文的可追溯關聯。
- [`IODE` 健保用藥品項查詢項目檔](https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111)
  是公開、按月更新、可整批重建的主來源。dataset identifier 是
  `A21030000I-E41001`，CSV resource 是 `A21030000I-E41001-001`。

IODE CSV 每列同時保存健保藥品代號、成分、價格有效起迄、ATC 代碼、給付
規定章節、藥品連結與給付規定文件連結。因此它可直接證明：

```text
某個健保藥品品項／價格期間 -> 來源所列 ATC code
某個健保藥品品項／價格期間 -> 來源所列給付規定章節與文件 URL
```

它**不能單獨證明**：

```text
整個 ATC 類別都受該條文規範
給付規定 URL 的檔名日期就是法律生效日
來源所列章節已解析為本專案的穩定 rule identity／精確 snapshot
```

## 給付規定 RSS 也可直接提供藥品關聯

藥品與條文的 relation 不限於 IODE／INAE3000。若給付規定 RSS 所指向的
官方 detail／附件已在同一 source bundle 中明列成分、商品與條號，應直接
保存為 `drug_rule_link_evidence`，不必為了相同 assertion 再強制追查一份
獨立「藥品公告」。

2026 年 `gov_健保審字第1150055452號` 是明確 canary。公告事項與 ODT
對照表直接列出：

| relation subject | source designation |
|---|---|
| aumolertinib（Pulmivex） | 9.138 |
| gefitinib（Iressa） | 9.24 |
| erlotinib（Tarceva） | 9.29 |
| afatinib（Giotrif） | 9.45 |
| osimertinib（Tagrisso） | 9.80 |
| dacomitinib（Vizimpro） | 9.83 |

同一 ODT 標示自 2026-08-01 生效，並保存修訂後／原給付規定兩側。這個
bundle 因而同時是 transition evidence candidate 與
ingredient/product→designation direct evidence。它不必再靠另一份藥品公告
才能建立上述關係。

但來源角色仍須分開：

- 公告／ODT：成分或 named product 與 source designation 的 exact
  assertion、條文 old/new sides、日期文字；
- ODS／IODE／INAE3000：健保品項代碼、特定強度、ATC、價格／支付有效期；
- stable `rule_identity`／`rule_snapshot`：仍須條文 identity 與 replay
  gate。

`drug_rule_link_evidence` 至少保存：

```text
evidence_id
source_uid
artifact_sha256
source_locator
subject_kind              # ingredient | brand | reimbursement_item
subject_value_raw
rule_designation_raw
relation_type             # explicitly_named | item_file_reference
effective_from
assertion_scope
review_state
```

只有當 RSS bundle 缺少 relation、品項／ATC 欄位不足，或官方來源互相矛盾
時，才開補充藥品公告搜尋。不得由「某成分被點名」推論所有品牌、所有強度
或整個 ATC class 都受相同條文規範。

2026-07-27 的 live CSV 有 224,455 列、45,124 個健保藥品代號、2,244 個
ATC codes；95,703 列有給付章節、95,520 列有給付文件 URL。原檔
96,799,113 bytes，SHA-256
`5abfec9bd0afb74f13cabca3402c2d6a0329b3436dd206f9dea83288a8b1d4a2`。
同日 legacy PostgreSQL formulary 有 224,603 列，比該次官方 snapshot 多
148 列；所以舊表只能作 discovery／對帳證據，不能直接冒充可重播的當月
snapshot。完整 receipt 見
[`2026-07-27-inae3000-atc-linkage-audit.json`](audits/2026-07-27-inae3000-atc-linkage-audit.json)。

2026-07-28 再以一列、只讀的 current query 核對上述 API，官方回報
14,066 筆現行品項，回應欄位仍含品項代碼、ATC、給付代碼清單、給付文件
清單與價格有效期。請見
[`2026-07-28-inae3000-live-api-observation.json`](audits/2026-07-28-inae3000-live-api-observation.json)。

## Raw snapshot 與更新

每次更新都保存官方 CSV 原始 bytes，不做 delete-and-reinsert，也不只保留
最新 upsert 結果：

```bash
PYTHONPATH=src python3 tools/fetch_nhi_drug_linkage.py \
  --output-dir build/nhi-drug-linkage
```

輸出包括：

```text
artifacts/<csv-sha256>.csv
manifests/<retrieved-at>-<sha-prefix>.json
```

manifest 保存 dataset/resource ID、來源 URL、擷取時間、HTTP metadata、
byte length、SHA-256、精確欄名、列數、藥品代碼數、ATC 數、給付章節與
連結列數。欄名漂移、零列、缺藥品代碼都 fail closed。raw CSV 與大型
normalized JSONL 應放 GitHub Release assets；Git 只追程式、schema、
manifest、audit 與小型 fixture。

建議排程：

1. 每月以 IODE CSV 形成 immutable public snapshot。
2. 每週以 INAE3000 current API 做 freshness reconciliation，不覆寫月檔。
3. snapshot hash 相同即 idempotent no-op；hash 不同才建立新的
   `dataset_release`、`source_artifact` 與 `linkage_import_run`。
4. 逐列保存 `source_row_number`、`source_record_sha256`、raw JSON、
   有效期間、ATC raw/normalized、章節 raw 與 exact URL。
5. 和前一 snapshot 做新增／移除／修正清冊；不在新檔的舊列保留於歷史，
   不從資料庫刪除。
6. 只有 `nhi_drug_rule_reference` 通過 designation、URL artifact 與
   rule/snapshot evidence 後，才可由 `unresolved_designation` 升格。

## 藥品層

```text
dataset_release + source_artifact
  -> linkage_import_run
  -> nhi_drug_item_observation
       -> drug_concept -> drug_identifier
       -> drug_atc_link
       -> nhi_drug_rule_reference -> rule_identity / rule_snapshot
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

1. 健保署 IODE「健保用藥品項查詢項目檔」本身提供的 ATC code。
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

NHI 直接來源列的 `source_system` 使用 `NHI_IODE_DRUG_ITEMS`，
`source_record_id` 使用 `nhi_drug_item_observation.observation_id`；
`atc_version` 綁定該 source snapshot／resourceModified，而不是假裝已
hydrate WHO 全部 ontology。

### 從品項推導條文 ATC

前端可將已解析到同一 `rule_id` 的產品列聚合成條文 ATC facet，但回傳值要
帶 `supporting_product_count`、source release 與 mapping state。其語意是：

> 有這些官方健保品項列同時指向此條文與此 ATC。

不是：

> 此條文涵蓋該 ATC 類別的所有藥。

未解析的章節 raw／URL 仍可用於 review queue，不能出現在 reader-facing
條文 ATC filter。相同 ATC 的多個品項是多筆 evidence，不應去重後失去來源。

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

因此 code 與 WHO 內容必須分層：

1. **公開 reader prototype**：可顯示專案建立的 code-only 關係、候選／
   已確認狀態與 WHO Coding Tool 連結；不得夾帶 WHO title、URI、definition
   或完整 reference snapshot。
2. **私有 PostgreSQL**：保存候選 title／URI、查核依據與完整參考快照，
   不進 JSONL、SQLite 或 GitHub Pages。
3. **可重用 crosswalk release**：WHO 條款把 mapping/crosswalk 另列為需
   書面 agreement；取得並記錄 agreement 前，不把 prototype code rows
   宣稱為 WHO 授權的正式 crosswalk dataset。

Code 本身不是密碼；這個邊界處理的是授權、內容再散布與關聯的審查狀態，
不是保密。公開候選 code 必須明示 `candidate`，不能冒充 confirmed mapping。

## 搜尋

讀者搜尋不要求背條文號。公開 read model 應索引：

- 健保藥品代碼、TFDA 許可證；
- 商品名、成分名、INN、常見別名；
- ATC code；
- 適應症原文與 normalized text；
- 條文全文；
- 章節與歷史 designation。

搜尋命中後回到 `rule_identity` 頁，再以 `rule_snapshot` 顯示時間序列。
