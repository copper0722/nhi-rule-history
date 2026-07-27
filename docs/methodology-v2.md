# v2 方法學：從官方原始資料到可稽核的結構化 stage

## 裁決

本專案採用 C+ 雙軌：

- v1 永久凍結為 `bounded_14_historical_odt/source-occurrence`。它保存已封存
  的 14 份年度 ODT、結構區塊及條號樣式候選；不再加入新來源。
- v2 是新的通用 acquisition/raw/structural pipeline。109 年版以後的公告、
  現行整份檔、分章檔及官方歷史查詢都只進 v2。
- v1 與 v2 只能透過相同 artifact SHA-256，或經全量 locator/text
  round-trip 證明的 block crosswalk 連結。

下列層次仍彼此分開：

```text
source plan
  -> discovery observation
  -> discovered resource candidate
  -> fetch attempt
  -> immutable raw artifact
  -> structural parse
  -> event/effect candidate
  -> promoted official event/effect
  -> stable rule identity and canonical history
  -> adjacent diff and reader model
```

目前只允許自動化前五層。來源被成功下載、解析或放入 PostgreSQL，不表示已
確認法律事件、生效日、條文身分、現行狀態或版本先後。

## post-109 的操作邊界

第一個 v2 capture 使用：

```text
artifact_boundary = strictly_after_sealed_109_release_artifact
temporal_query_start = 2021-01-01
overlap_policy = retain every raw date expression even when it refers to ROC 109
```

這是可重現的 acquisition 邊界，不是法律完整性聲明。109 年公告但 110 年
生效、110 年公告追溯到 109 年，或後續發布的勘誤，都必須保留原始日期文字，
不得因 query boundary 被改寫。

## 官方 endpoint plan

| Endpoint | 角色 | 本輪已證明的限制 |
|---|---|---|
| [NHI 歷史整份檔](https://www.nhi.gov.tw/ch/cp-2192-9951a-2509-1.html) | 96 年 7 月至 109 年 cumulative anchors | 只有 v1 的 14 ODT 已封存 |
| [NHI 最新整份檔](https://www.nhi.gov.tw/ch/cp-13108-67ddf-2508-1.html) | terminal cumulative anchor | title/update metadata 不是生效日；同 URL 會變動 |
| [NHI 最新分章檔](https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html) | terminal chapter anchors | 需與同批 whole 做獨立一致性檢查 |
| [NHI 法規公告 listing](https://www.nhi.gov.tw/ch/lp-3258-1.html) | listing/detail/attachment observation | 2026-07-27 顯示 857 筆、43 頁，但最舊只到 ROC 111，不能單獨補齊 post-109 |
| [MOHW 函釋歷史查詢](https://mohwlaw.mohw.gov.tw/FINT/FINTQRY01-1.aspx) | post-109 announcement/detail/attachment candidates | `RowNo` 隨 query 改變；正式文號、detail bytes、PFID 與 artifact hash 必須分存 |

2026-07-27 的 bounded production run 以完全相同 query profile 查詢
`2021-01-01..2026-07-27` 及關鍵詞 `藥品給付規定`，得到：

- 6 個固定年度 partition、366 筆 formal document numbers；
- 兩次獨立列舉都得到 1,719 個相同 resource keys；
- 文書日期從 ROC 110-01-15 到 115-07-15；
- 366/366 detail pages 至少有一個附件；
- 1,353 個 unique PFID：669 PDF、360 ODT、299 ODS、11 XLS、
  11 DOC、2 XLSX、1 DOCX。

這證明一個可程式重建的 bounded source set；它仍不能證明所有會影響藥品
給付規定的官方函釋都一定含有這個完全相同的字串。後續 source-universe
closure 必須加入明定的查詢 partition/同義 query 與跨來源 discrepancy。

## Discover

每一個動態 endpoint 要做兩次完整枚舉：

```text
pass A
  -> enumerate all pages/resources
  -> fetch every expected detail and attachment
pass B
  -> enumerate the same endpoint again
  -> compare the complete unique resource-key set
```

只有下列條件同時成立，才能標示
`ENDPOINT_ENUMERATION_COMPLETE_TO_CAPTURE_CUT=true`：

- source plan bytes 與 query profile 未改變；
- pagination/query partitions 全部走完；
- pass A 與 pass B 的 unique keys 完全相同；
- 沒有未解的 selector、cap、page 或 detail failure。

capture cut 是 `capture_window_started_at..capture_window_ended_at`，不是假裝
官方網站在某一秒靜止不動。

### Resource identity

FINT 的 `RowNo` 只是一輪 query 內的 locator；同一組 366 筆公文在相隔數分鐘
的兩輪查詢中，RowNo 順序確實改變。因此：

- detail resource key = authority + normalized formal document number；
- attachment resource key = authority + canonical attachment URL/PFID；
- RowNo、partition、attachment ordinal 只保留為 discovery locator；
- official label、detail bytes 與 artifact bytes 分別 hash，不塞進 stable
  acquisition key。

第一版錯把 RowNo／parent ordinal 放進 key，兩輪雖同為 1,719 筆，仍有 344
個 key 不一致。雙輪 gate 因此阻止該 run 成為 release input；修正後兩輪
key set 完全一致。這是 acquisition identity 修正，不代表已建立穩定條文
身分。

## Fetch 與 immutable raw

raw artifact 的唯一身分是原始 bytes 的 SHA-256。每次 attempt 另存 request、
redirect、HTTP status、safe response headers、Content-Disposition、工程時間與
failure code。

```text
same URL + same bytes      = same artifact, new observation
same URL + different bytes = new artifact revision observation
```

第二種情況只表示官方 URL 曾提供不同 bytes；不表示後者在法律上取代前者。

MOHW attachment endpoint 實測會把 PDF/ODT bytes 回報成
`Content-Type: text/html`。因此驗證不得只信 HTTP header，必須同時檢查：

- declared filename/extension；
- Content-Disposition；
- magic bytes；
- byte length；
- SHA-256；
- parser capability。

raw store 使用 content-addressed relative locator，不保存本機絕對路徑。
成功 artifact 不會被 resume 重新覆寫；`--refresh` 建立新 acquisition run。

## Parse fidelity

| Fidelity class | 例 | 可做的事 |
|---|---|---|
| `lossless_structural` | 直接解析 ODT XML、HTML DOM | 可產生結構區塊與 occurrence candidates |
| `deterministic_conversion` | DOC 經固定 converter 產生 derivative | 保留 raw + derivative + converter provenance 後才可解析 |
| `text_extraction_only` | PDF text layer | 只能輸出明示低結構 fidelity 的 blocks |
| `visual_or_ocr_derived` | 掃描 PDF/OCR | 不得冒充原生 source text 或可靠 old/new table |

old/new 對照表若有 merged cells、跨列/跨欄、缺頁或 OCR，mapping 必須保留為
candidate；不得自動宣布「新增」或「刪除」。

## PostgreSQL promotion boundary

`tw_drug_history_acq_stage` 只收：

- source plan/run；
- discovery observations/resources；
- fetch attempts；
- content-addressed raw artifacts；
- resource-artifact/url observations；
- acquisition issues。

之後的 structural stage 也只能收 blocks、occurrence candidates 與 parse
issues。日期、事件與 effect 若要建立，必須進另一個尚未開放的 evidence/
promotion layer。下列資料不得由 parser/loader 直接建立：

- legal effective date；
- promoted official event/effect；
- stable rule identity；
- current/active status；
- split/merge/number reuse；
- correction/supersession；
- canonical text version；
- replay/diff。

隔離 schema 是 additive、append-only、run-scoped、fingerprint-bound。rollback
只允許明列物件，禁止 `CASCADE`，最後以 `DROP SCHEMA ... RESTRICT` 封口。

## 可重跑 state machine

```text
planned -> discovering -> discovered -> fetching
-> raw_complete -> raw_verified -> parsing -> parsed
-> stage_validated -> loading -> sealed -> stage_verified
-> exported -> release_prepared -> released
```

只有 `discover`、`fetch`、`release publish` 可連網。`verify-raw`、`parse`、
`stage validate`、`export` 與 parity validation 必須能離線重算。

失敗的 network attempt append 新 attempt；成功 bytes 不覆寫。transform
失敗不得留下可 seal 的 bundle；PG `loading -> failed` 後該 run 永久 terminal，
retry 必須建立新 run。partial export 沒有 final manifest。

## 公開資料契約

公開 source-occurrence/stage export 的共同目標契約是：

- deterministic UTF-8/LF JSONL，依完整 primary key 排序；
- portable SQLite，由相同 rows 單向生成；
- 每表 `row_count`、`primary_key_set_sha256`、
  `typed_row_digest_sha256`；
- PostgreSQL/JSONL/SQLite 三方 logical parity；
- SQLite `integrity_check=ok`、`foreign_key_check` 無 rows；
- checksums、redaction receipt 與該 release family 固定的 non-claim。

SQLite 是公開 projection，不是 canonical store。驗收比較 logical typed-row
digest，不要求不同 SQLite library 建出的 physical file bytes 必然相同。

這個契約目前已對 v1 八張 stage tables 完整實作並產生 publish-ready
JSONL.zst + SQLite；v2 acquisition/structural JSONL 已形成，但 v2 的同型
SQLite exporter 尚未完成，因此不得把 v1 的 parity receipt 延伸聲稱為 v2。

## 固定 non-claim

non-claim 是資料契約欄位，不是每個不同資料層共用一段過度概括的宣傳文字。
每個 release family 固定一個 exact string，同一 family 的 DATASET、manifest、
Release description 與 SQLite metadata（若該版已實作 SQLite）必須逐 byte
一致：

```text
v1 = Bounded source-occurrence staging from 14 historical ODT files; not a complete legal history and not evidence of legal effective dates.

v2 = Source-local structural observation only; not stable rule identity, legal effective date, legal event, current version, predecessor/successor, or diff.
```

兩者都必須另外帶 `legal_history_claim=false`。v2 尚未實作 SQLite，所以不能
把 v1 SQLite 的 non-claim 或 parity receipt 延伸到 v2；發布 v2 SQLite 前，
該 exact v2 string 必須進入 SQLite metadata。

## 授權與容量

健保署及衛福部的政府網站資料開放宣告均允許在著作權保護範圍內，以無償、
非專屬、可再授權方式重製、改作、編輯與公開傳輸，但必須註明出處，且排除
另有特別聲明或第三人權利的素材。release prepare 會保存授權頁 URL 與
attribution；發現特別聲明的 artifact 必須個別阻擋。

大型 JSONL、SQLite 與官方 binary 不反覆提交 Git history；使用 immutable
GitHub Release assets。Git 只保存 code、schema、small fixtures、manifest、
checksums 與 receipts。
