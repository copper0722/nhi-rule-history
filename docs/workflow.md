# 資料取得與更新 workflow

這個 workflow 的目標不是「抓到一份最新檔」，而是讓任何人能回答：

1. 這份資料從哪個官方頁面取得？
2. 當時官方頁面列出了多少 release、公告與附件？
3. 哪些 bytes 被解析？
4. 如何從原始檔重建條文、版本、事件與 diff？
5. 更新後哪些資料改變，哪些只是格式變動？

## 截至 2026-07-29 的重建共識

這一節是後續 agent 的工作起點。除非有新的官方證據推翻，不能再把流程
改回「先找到所有公文，才開始建立歷史」，也不能把年度檔降格成只供參考
的附件。

### 1. Version unit 是單一條文

- 整部、整章、年度 ODT 都是 `source edition`，不是 canonical version。
- 每一個 top-level 條文各自有永久 `rule_identity`、版本鏈與 direct
  predecessor。
- 條號只是某一段時間的 designation。改號、移動、拆分、合併、刪除後
  復活與條號重用都不能只靠字串相等自動裁決。
- 前端每個條文一頁：最新版全文置頂；歷史列只顯示該版相對直接前版的
  diff。

### 2. 年度快照是 source observations；Git-like 只作工作比喻

年度快照的角色不是提供精確修法日期，而是證明某個時間點整部規定的可見
狀態。對每一個條文，依 edition time 排列所有 observation：

```text
official source editions
  -> clause observations
  -> adjacent observed states
  -> appearance / text-change / disappearance observations
  -> notice and date-marker reconciliation
  -> stable identity and lineage resolution
  -> accepted clause versions and direct edges
```

- 同一條文跨兩個快照全文相同：是兩筆 observations、同一個 text state，
  不虛構新版本。
- 前一快照存在、後一快照消失：建立 `disappearance_observed`。這能找回
  日期 marker 無法顯示的整條消失，但在 identity review 前不能先叫刪除。
- 前一快照不存在、後一快照出現：建立 `appearance_observed`；之後再判斷
  create、restore、move、split 或只是較早來源缺漏。
- 兩個相鄰快照文字不同：建立 `text_change_observed`。deterministic diff
  只描述兩個 observations 的差異，不冒充 direct legal predecessor diff。
- 快照間隔內找不到公文時，仍保存版本與
  `effective_time_precision=interval`；區間起訖是前一 observation 之後到
  後一 observation 當日。不能把公文缺口誤寫成「沒有變更」，也不能把
  後一快照日期冒充生效日。

### 3. 現存條文以民國年月作版本缺口 denominator

- 原文括號內的 `85/1/1`、`103/9/1` 等日期逐一保存 exact span、來源
  locator 與正規化候選日。
- 對每一個**現存條文**，專案 owner 指定
  `expected_version_count=max(1, count(distinct valid ROC dates))`；
  已重建全文狀態至少含目前版本，再加上已接受的歷史全文狀態。
- `missing_version_count=max(0, expected_version_count -
  reconstructed_version_count)`。這可以立即回答「現存條文尚缺多少歷史
  全文版本」，並用來排重建優先順序。
- 已重建全文狀態多於日期推得數量時不產生負缺口，另標
  `annotation_count_underflows_reconstructed_evidence`，保留方法上的
  discrepancy。
- 它們無法發現已整條刪除、刪除後未復活、舊條號被移除，或新版本未保留
  舊日期的情形。因此這個 denominator 只回答現存條文的缺版 inventory，
  不代表所有曾存在條文的 complete-history denominator。
- 日期註記、年度快照、公告／附件三者互相校驗；任何單一路徑都不宣稱
  已封閉公告或歷史條文宇宙。

### 4. 公文是 transition 的精確證據，不是開始建版本鏈的先決條件

- 公文 detail 與 old/new 對照附件可提供正式文號、公告日、明示生效日、
  受影響條文及 before/after，找到時用來把 interval transition 升格成
  exact transition。
- 公文的 raw text 與附件先保存；版面、表格、略字、刪除線或 OCR 需要
  agent 轉為 proofread evidence。agent 不得直接寫 canonical history；
  其 proposal 經 deterministic hash/locator 驗證後才可進 PG。
- 找不到公文時保留 bounded search receipt、查過的名稱／日期／條號／
  藥名與官方入口；`notice_not_found` 只描述搜尋結果，不證明公文不存在。

### 5. 來源策略是多路證據取聯集

主要證據路徑如下：

1. 最新分章官方檔：現行條文正典，可先獨立發布。
2. 14 份年度 ODT：逐條 state anchors 與刪除偵測。
3. 現行條文的民國年月：存活文字 amendment index。
4. NHI 公告／RSS／detail／附件：較新 transition 的原始事件證據。
5. MOHW FINT：依日期、文號、條號、藥名與前身名稱做 targeted notice
   retrieval，亦補 NHI 現行 listing 已下架的公文。
6. 行政院公報、總統府公報、國圖／臺灣記憶、政府出版品及其他正式館藏：
   補早期與 born-paper sources。

NHI 的父層選單雖寫「修正規定（自103年4月3日以後生效之公告）」，但
2026-07-29 實測 target listing 共 859 筆、43 頁，最舊可見列為
111-09-06，且表格有「刊登期限」。因此該 listing 不能作
2014-04-03 以後的封閉歷史分母；它只是目前可列舉的存活公告表面。

FINT 四個關鍵字全空的 17,497 筆全庫列舉是可選的研究／漏失稽核，不是
主要重建前置條件。主流程從快照 diff、日期 marker 與 current-to-history
gap 產生 targeted queries；只有當要估計某個查詢面的漏失率時，才啟動
bounded broad crawl。

### 6. 早期名稱必須納入 identity lineage

健保開辦初期的同一規範系譜不是一開始就叫「藥品給付規定」：

- 84-06-20：官方後來回顧為「全民健康保險藥品使用規範」；
- 84-07-01：國圖數位典藏記錄一份函轉該規範並自此日實施的公文；
- 87-03-04：官方回顧為重新編排並改名「全民健康保險藥品給付規定」，
  自 87-04-01 實施。

因此以後期名稱查 85–86 年得 0 筆，不得解讀成當時沒有給付條件。早期
source state 分開標示：

```text
born_digital_official
digitized_scan
catalog_only
later_official_reproduction
paper_holding_not_digitized
availability_unresolved
```

2026-07-29 已由 FINT 取得 `健保醫字第84010140號` 及其 25 頁官方附件
`全民健康保險藥品使用規範.PDF`，並封存 raw bundle 與載入 append-only
PostgreSQL acquisition stage。84 年全文不再是可得性 gap；未完成的是掃描
逐頁 proofread，以及 `85/1/1` 精確修正文的定位。句子查詢為 0、日期變體
亦未找到相關結果，只能寫 `not_found_after_declared_search`。取得收據見
[`2026-07-29-fint-84-baseline-acquisition.json`](audits/2026-07-29-fint-84-baseline-acquisition.json)。
早期證據分類與 claim limits 另見
[`2026-07-29-early-rule-source-lineage-check.json`](audits/2026-07-29-early-rule-source-lineage-check.json)。

國史館臺灣文獻館「臺灣省政府衛生處」全宗是另一條搜尋線，但也不能用
寬查詢畫面直接判斷年代範圍。2026-07-29 的全宗查詢宣告 12,992 筆；使用者
提供的遞增排序視窗是第 9,981–10,000 筆，因此畫面停在 1967 年。現行 UI
只顯示到第 10,000 筆，直接請求更後範圍沒有結果；限定 1995 年只回一筆
不相關卷，精確查 `84衛技字第052484號` 與規範全名皆為 0。這只形成
bounded search receipt：目標未在目前索引命中，不證明館藏或紙本不存在。
後續查此站必須以年代切片、文號、件名分別查，不能翻一個 10,000 筆寬表。

## 資料層

```text
current official split files
annual snapshots + inline date markers
official notices + gazettes + archival holdings
  -> immutable source artifacts + manifests
  -> structural parse + per-clause observations
  -> evidence-union transition candidates
  -> PostgreSQL stable identity + accepted clause versions
  -> PostgreSQL adjacent comparisons + diffs
  -> sealed PostgreSQL import
       -> normalized JSONL public interchange
       -> SQLite portable release
       -> API / reader projections
```

每一層都保留輸入 hash、程式版本、輸出 fingerprint 與 issue ledger。下游不得
跳過上游 gate。PostgreSQL 是唯一可寫 authority；JSONL、SQLite 與前端不得
回寫或各自維護第二份條文。

## WP01：來源枚舉與取得

### FINT 全庫研究 crawler

FINT 的主要角色是依 clause evidence 產生的日期、正式文號、條號、藥名、
適應症及規範前身名稱做 targeted retrieval。每次 query 都保存完整結果頁，
再逐一取得 `FINTQRY04 RowNo=1..N` 的詳情內文；同一 query 的 expected
match 數必須和 fetched rows 相等。

四個關鍵字欄位留空時會回傳廣泛公文表面，可按西元年度做 bounded
enumeration，供 query recall 稽核或找無法預期的名稱變體。這條 broad
lane 不再是逐條歷史的主流程，也不必在 current rules 或 snapshot-derived
history 發布前完成。

同一公文被不同 query 命中時，PG 保存不同 RowNo occurrences 與 detail
snapshots；正式文號只作 grouping key。附件 declaration 必須綁到宣告它的
detail snapshot；附件 bytes 依 `all`、`nhi_candidate` 或 `none` 另報
coverage，不把「看見連結」和「已抓 bytes／已判相關」混為一談。

流程、TLS 相容策略、CAPD canary 的錯掛附件案例、PG schema 與 frontier
closure 定義見
[FINT 歷史公文研究 crawler](fint-keyword-crawler.md)。

### 1. 枚舉

每次 update 先讀 [sources/sources.yaml](../sources/sources.yaml)，完整枚舉：

- 歷史整份檔頁面上的所有格式與版本；
- 現行整份檔及分章／附表；
- NHI 公告 listing 的全部頁數與 rows；
- 每一個公告 detail page；
- 每個 detail page 的 DOC／ODT／PDF／ZIP 附件；
- 官方歷史查詢與母法附件六交叉來源。

listing 階段不得先用藥名或關鍵字丟資料。先 archive 完整 listing，再分類是否
影響藥品給付規定。

### 2. 下載

每個 artifact 建立：

```text
source_id
source_page_url
official_url
official_label
filename
media_type
byte_length
sha256
fetched_at
fetch_transport
supersedes_sha256
licence
```

相同 URL 回傳不同 bytes 時建立新 revision，不覆寫舊檔。403、selector miss
或 0 rows 是 failed update，不能解釋成「沒有更新」。

### 3. 保存

- manifest 與 normalized text 進 Git。
- 官方 binary 以 checksum-addressed GitHub Release assets 保存。
- manifest 必須可在下載 release assets 後逐檔重算 SHA-256。
- 同一 source bundle 的所有官方格式都保存；parser 可選主格式，但 PDF／DOC
  仍作交叉驗證。

目前 14 份 ODT 的 manifest：
[data/manifests/nhi-history-odt-v1.jsonl](../data/manifests/nhi-history-odt-v1.jsonl)。

歷史公告 acquisition 完成後，另以正式文號為原子單位建立 deterministic
source-local bundle。每份 bundle 都必須同時綁定 detail page 與該頁明列的
全部附件；沒有 ODT、只有 PDF／影像／OLE、或零附件都不能從分母消失：

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli historical-bundles \
  --run-dir build/historical-raw-run \
  --source-plan sources/source-plan-historical-events-exact-phrase.json \
  --output-root build/historical-notice-bundles
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-historical-bundles \
  --output-root build/historical-notice-bundles
```

這個步驟只證明 bounded input 內每份公文的原始 bytes 都已封裝與可重播；
不解讀生效日、條文身分或修正內容。

## WP02：結構解析

已實作的 ODT stage parser 會保存：

- 段落、標題、表格、列、儲存格、covered／empty cell；
- 原文字、normalized search text、hash 與長度；
- XML element index、repeat instance 與完整 locator；
- dotted-designation occurrence candidate；
- deterministic issue ledger。

它不推論法律生效日、不以條號建立 canonical identity，也不計算跨版 diff。

## WP03：Transition evidence union 與公告連結

一個條文 transition 與公告是否仍可取得分開，但 evidence strength 必須
明示：

- `rule_transition`：對某一條文的 create、amend、delete、restore、
  rename、move、split、merge 或 correction；
- `transition_evidence`：官方 cumulative version、old/new 對照表、條文
  日期註記、archival snapshot 或公告 effect 的 exact locator；
- `official_notice` 與 `transition_notice_link`：找到時保存 exact
  transition provenance；沒有 notice 的 snapshot-derived transition 可
  存在，但只能是 interval precision，不能假裝 exact effective event。

每個 accepted effective date 必須指回官方 locator。公告列出的日期、附件
正文日期與實際生效日分欄保存。不能證明所有歷史公告都仍存在或可由現行
查詢找到，因此 notice linkage 是獨立 coverage，不是完成 gate。

### 日期註記 ledger

每一條／子項原文中的 ROC 日期註記全部列舉，不先去重成「條文日期」：

1. exact source span → `source_date_annotation`；
2. deterministic ROC conversion 只產生 ISO candidate；calendar-invalid
   slash triplets 必須讀 exact context，將劑量等非日期 observation 明確
   `rejected_non_amendment`，不可永遠留在日期分母；
3. 先由同檔日期＋條號 locator 沿 artifact→resource→parent detail 回查
   正式文號；unique owning document 仍只是 review candidate；
4. 有公告候選時，讀 detail 的生效句與 old/new 附件，建立 evidence
   proposal；
5. 找不到公告時記
   `notice_not_found_after_bounded_search`／`notice_availability_unknown`，
   不刪 marker，也不推論公告不存在；
6. 以其他官方 evidence basis 足以證明 transition 時，可在沒有公告連結下
   完成；但仍須完整 before/after、stable identity、direct adjacency 與
   anchor replay；
7. 每一條分開公開 `annotation_terminal_coverage`、
   `transition_evidence_coverage`、`notice_linkage_coverage` 與 gap reasons。

日期集合是 completeness checksum。它不能取代歷史附件、被刪文字或
cumulative anchor replay。

Legacy current-text 的第一輪 gap inventory 可用公開 CLI 重建：

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli load-annotation-stage \
  --input-jsonl legacy-article-observations.jsonl \
  --dsn "$NHI_RULE_HISTORY_DSN"
```

每個 JSONL row 必須包含 `article_id`、`article_num`、exact `full_text` 與明示
的 `source_identity` object。這個命令只寫 isolated append-only stage；所有
marker 的舊 v1 初態都是 `unresolved_event`；該欄名只代表尚未裁決。v3
工作包會將它轉為 evidence-basis candidate，不會推定公告、建立快照或寫入
canonical history。

## WP04：條文身分與版本

- `rule_identity` 使用永久 ID。
- 官方「通則」以 project navigation code `chapter:00` 排序；`00` 必須標記
  `project_assigned`，讀者顯示仍為「通則」，不稱「第 0 章」。
- 整份章節或年度檔是 source-edition container，不是條文版本。先以
  top-level ordinal 切成 `0.1–0.12`，再讓每個單一條文各自建立
  observation、text state、edge 與 diff。
- 連續來源版本的同一條若 comparison text 相同，只建立一個
  `clause_version`；每份來源仍各保留一筆
  `clause_version_observation`。文字曾改變後又恢復時，恢復後是新的 state。
- `rule_designation` 保存條號、標題與有效區間。
- 相同條號不保證同一條文。
- split／merge／number reuse／restore／correction 必須有 curation decision。
- 每個 `rule_snapshot` 保存完整文字、結構、來源 locator 與 evidence。
- 日期／條號同檔 preflight 只建立 evidence candidate；不得據此自動建立
  `official_event_effect`、選 canonical side 或把前一版移入 history。

## WP05：藥品／ATC linkage

1. 從 NHI IODE `A21030000I-E41001-001` 取得整批 CSV；保存 exact bytes、
   retrieval time、HTTP metadata、SHA-256 與 source manifest。
2. 將每列載入 `nhi_drug_item_observation`；原始健保代碼、價格有效期間、
   ATC、給付章節與 exact URL 一律保留，不能只做 current-state upsert。
3. 品項到 ATC 是官方來源 assertion；品項到給付章節／URL 也是官方來源
   assertion。章節到 canonical `rule_identity`／`rule_snapshot` 仍須獨立
   resolution。
4. 給付規定 RSS/detail/attachment 若直接同時列出成分／商品與條號，另建立
   `drug_rule_link_evidence`；相同關係不必再強制追一份獨立藥品公告。只有
   relation、品項／ATC／強度或跨來源 discrepancy 未解時才補充搜尋。
5. 條文到 ATC 只由已解析產品列推導，保存 support count 與 source release；
   不把它宣稱成整個 ATC class 的適用範圍。
6. 每月 IODE snapshot 是可重建基線；INAE3000
   `POST /api/INAE3000/INAE3000S01/SQL0001` 作每週 freshness
   reconciliation，不覆寫或刪除舊 snapshot。
7. PostgreSQL 與 SQLite 使用相同
   `linkage_import_run`／`nhi_drug_item_observation`／
   `nhi_drug_rule_reference`／`drug_rule_link_evidence` logical contract。

實作與 live audit 見 [ATC 與 ICD-11 linkage](linkage.md)。

## WP06：逐條 Git-like 重播與 diff

1. 以最早可驗證 snapshot 建立每條 clause 的第一個 observed state；這
   不自動代表法律初始版本。
2. 逐 edition 比對同一 stable identity 的 presence、designation、structure
   與全文 hash，產生 create／amend／delete／restore／move candidates。
3. 以條內日期與公文 old/new effect 將 candidate 的 interval date 升格為
   exact date；若不一致則保留 conflict，不覆寫證據。
4. 依 accepted exact 或 interval chronology 重播；到每一個 cumulative
   snapshot 時，比對 rule set、designation 與全文 hash。
5. 差異未解時，只阻擋受影響條文與時間區間；不阻擋已驗證的其他條文。
6. 每個版本只和直接前版比較。純新增只顯示「本版新增」；純刪除只顯示
   「本版刪除」，不製造不存在的另一側變更。
7. 同一列若新文字只新增一個有效日期，顯示「與上一版本差異」；若新增
   兩個以上有效日期，表示兩個已重建全文間跨過至少一個預期版本，顯示
   「與舊版本差異」並列出中間缺少的全文版本數。
8. 公開資料同時揭露 date precision、evidence basis、source coverage 與
   unresolved gaps，讓 interval history 可以先使用而不冒充 exact history。

## WP06A：現行 639 條 publication projection

正式網站與 API 不直接讀 ODT 或 Git JSON。更新流程將 sealed current
structural parse 單向載入 PostgreSQL
`nhi_rule_history_publication`：

```text
official chapter ODTs
  -> sealed structural blocks
  -> one current_clause row per clause
  -> current_clause_block / current_clause_date
  -> expected / reconstructed / missing version inventory
  -> sealed publication_run
  -> publication_activation
  -> read-only API and site projections
```

2026-07-29 的固定輸入為 parse run
`baae912e-8d5f-46b0-9efd-77cf4d567428`。逐條 inventory 收據見
[`2026-07-29-current-clause-history-inventory.json`](audits/2026-07-29-current-clause-history-inventory.json)。
本輪統計為 639 條、3,512 個應有版本、656 個已重建全文狀態、2,861 個
缺少全文狀態；440 條有缺版，199 條目前不缺，5 條的日期計數低於既有
全文證據而另列 discrepancy。

同一個 active sealed projection 的發布邊界如下：

1. `copper-panel`／`hmj:8710` 是內部唯讀 API provider，提供 latest
   list/search、單條 detail、history inventory 與 reviewed enrichment。
2. 付費站在 build 時讀取 typed contracts，產生受訂閱 gateway 保護的
   same-origin JSON 與 reader；不讓瀏覽器直接連 tailnet API。
3. `boan-emr` 只讀 latest contract，不把歷史重建、agent summary 或私有
   ICD-11 內容帶入臨床端。瀏覽器只呼叫 BOA same-origin proxy；BOA
   server-side client 驗證 exact v1 contract、sealed state、run ID 與
   output fingerprint，同一藥品卡不得混用兩個 publication receipts。
   本地 `tw_drug` 只提供藥品到條文號的連結；upstream 404 不復活舊條文，
   upstream failure 才可顯示明確標示 `latest_only=false` 的舊鏡像。
   2026-07-29 已從 BOA 實機通過 status、list、detail 與 formulary
   projection，收據見
   [`2026-07-29-boan-emr-latest-consumer-live-verification.json`](audits/2026-07-29-boan-emr-latest-consumer-live-verification.json)。
4. GitHub 保存可重現 workflow、schema、程式、公開稽核收據及可攜
   JSON／SQLite release。它不是付費 reader 的 production host。

## WP06B：掃描原始檔的 proofread source-observation layer

掃描／OCR 的忠實轉錄 proofread 與現行條文的排版 proofread 是不同資料層。
後者不得改動任何來源字元；先證明版本化 parser 已保留官方 artifact 可得
的全部結構，再使用既有 ODT table/list locator。Parser 漏結構時屬
`source_structure_loss`，禁止開 presentation run；只有官方來源本身不足、
且純版面分組可逐字等價時，才允許 agent 提案。完整 schema、逐字等價
invariant、seal gate 與 API fallback 見
[`presentation-proofread.md`](presentation-proofread.md)。

掃描 PDF 沒有可靠文字層時，模型校對不能直接寫入 canonical legal
version。輸入包固定包含官方附件 identity、逐頁 transcript、source-local
segments 與跨版本 lineage candidates：

```text
official attachment snapshot in FINT PG
  -> full-page visual proofread
  -> exact source-page markers
  -> source-local segment JSONL
  -> normalized-containment-in-declared-page-span validator
  -> unadjudicated lineage candidate parser
  -> immutable sealed transcript_run
  -> independent review
  -> identity / legal-version adjudication (separate future gate)
```

Loader 對每個 segment 只允許 Unicode NFKC、空白與 Markdown 結構符號的
比較正規化，並移除 proofread 中重複的公報頁首；不得模糊比對、同義改寫
或讓模型自行補字。任一 normalized transcript span 無法在宣告頁面範圍找到，整包拒絕
入庫。Lineage candidates 必須一對一覆蓋所有 segment IDs，disposition
count 也必須等於 manifest。

PG schema `nhi_rule_history_transcript` 把 transcript、25 個 source pages、
114 個 segments、analysis 與 114 個 candidates 分表保存。父 run 只能
`loading → sealed`；sealed 後 parent/children 的 INSERT、UPDATE、DELETE
及 TRUNCATE 均 fail closed。外鍵指回 FINT 內的官方 attachment snapshot。

84 年第一批 live run
`03f3b55e-8a07-5efb-b3ec-f908fbd01575` 的 review status 是
`agent_proofread_pending_independent_review`。即使 114/114 declared-page
normalized-containment checks 全通過，它仍只代表「來源上看到什麼」：

- 不建立 stable legal identity；
- 不宣告 96 年條文是 direct predecessor／successor；
- 不把文件一般實施日下推為每個 segment 的精確生效日；
- 不宣告完整歷史；
- 不先扣減現行條文的 2,861 個缺版。

只有 independent review 與 identity/legal-version adjudication 均通過的
segment，才可在另一個 promotion transaction 影響 canonical version
inventory。

## WP07：公開 release

每個資料 release 至少包含：

```text
manifest.json
dataset_release.jsonl
source_artifact.jsonl
official_event.jsonl
official_event_effect.jsonl
rule_identity.jsonl
rule_designation.jsonl
rule_snapshot.jsonl
snapshot_evidence.jsonl
comparison_edge.jsonl
diff_hunk.jsonl
drug_concept.jsonl
linkage_import_run.jsonl
nhi_drug_item_observation.jsonl
nhi_drug_rule_reference.jsonl
drug_atc_link.jsonl
indication.jsonl
rule_indication_link.jsonl
nhi-rule-history.sqlite
checksums.sha256
```

SQLite 由同一批 JSONL 建立，不是另一份手工維護資料。

## 更新操作

目前每一層都有獨立、fail-closed 的 CLI；尚未把它們包成一個會自動跨 gate
的 `update --all`：

```bash
make test

PYTHONPATH=src python3 -m nhi_rule_history.cli discover \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli discover \
  --plan sources/source-plan-v2.json --run-dir build/pass-b \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli compare-discovery \
  --pass-a build/pass-a --pass-b build/pass-b \
  --output build/pass-a/discovery-parity.json
PYTHONPATH=src python3 -m nhi_rule_history.cli fetch \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-raw \
  --run-dir build/pass-a
PYTHONPATH=src python3 -m nhi_rule_history.cli parse-odt \
  --run-dir build/pass-a --stage-dir build/structural-RUN-UUID \
  --parse-run-id RUN-UUID
PYTHONPATH=src python3 -m nhi_rule_history.cli release-v2 \
  --run-dir build/pass-a --stage-dir build/structural-RUN-UUID \
  --source-plan sources/source-plan-v2.json \
  --eligibility-receipt \
    data/manifests/mohw-fint-2021-2026-v2/release-eligibility.json \
  --output-dir build/v2-evidence-release

PYTHONPATH=src python3 tools/build_current_history_inventory.py \
  build/baae912e-8d5f-46b0-9efd-77cf4d567428 \
  --dsn "$DATABASE_URL" \
  --output docs/audits/current-clause-history-inventory.json

PYTHONPATH=src python3 tools/load_current_publication.py \
  build/baae912e-8d5f-46b0-9efd-77cf4d567428 \
  --dsn "$DATABASE_URL"

PYTHONPATH=src python3 tools/load_source_transcript.py \
  external_bundle:gpt-pro-20260729-v1 \
  --dsn "$DATABASE_URL"
```

上例的 `external_bundle:` 是公開文件中的 logical locator；實際執行時傳入
本機 formal bundle 目錄。Loader 不接受 public repo 裡未經 manifest
hash/size 驗證的散落文字。

取得 NHI 藥品／ATC／給付章節 raw snapshot：

```bash
PYTHONPATH=src python3 tools/fetch_nhi_drug_linkage.py \
  --output-dir build/nhi-drug-linkage
```

NHI IODE fetcher 預設且實跑皆使用 TLS 驗證；2026-07-27 live smoke 不需要
任何 insecure 例外。若 operator 因可重現的相容性需求明示
`--allow-insecure-tls`，工具會在 manifest 記錄該 transport；目前工具本身
不強制隔離，因此 operator 必須指定獨立的 `--output-dir`，且在以正常 TLS
或可驗證 CA bundle 重新取得並核對前，不得交給下游 loader 或 release。
工具不寫 PG，先產生 content-addressed raw 與 manifest，再由受控 loader
進 staging。

上方 FINT `discover`／`fetch` 命令中的 TLS 例外則只重現 2026-07-27 FINT
endpoint 與本機 Python trust store 的相容需求，必須由 operator 明示。
預設仍 fail closed；有可驗證 CA bundle 時改用 `--ca-file`。

套用 repo migration 後，`load-acquisition` 與 `load-structural` 各自會先完整
validate、在單一 transaction 寫入並 seal，再用新連線重算 count 與 row-set
fingerprint。`release-v2` 只在本地準備 raw tar.zst 與 structural JSONL.zst，
沒有發布網路路徑。

入口完成前，不建立假裝自動化的 schedule。每一個未實作步驟留在
[gap-register](gap-register.md)。

## Pull request gate

每次更新 PR 必須附：

- 前後 source manifest diff；
- 新增／改 bytes／消失的官方 URL；
- expected/fetched pages、rows、details、attachments；
- parser、event、identity、replay、diff issue counts；
- output fingerprint；
- SQLite `integrity_check` 與 row-count parity；
- 授權或第三方資料變更。

來源刪除或回溯修改不能直接從 history 消失；以 supersession 與 finding 表示。
