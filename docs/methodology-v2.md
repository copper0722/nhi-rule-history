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

## 「第 0 章」是專案導航碼，不是官方章號

健保署原文使用「通則」，沒有把它命名為「第 0 章」或「第 0 節」。既有
`tw_drug.rule_chapters.chapter_id = 0` 是本專案為了排序、網址與資料關聯而
配置的 internal navigation code：

```text
source label = 通則
project navigation code = chapter:00
code origin = project_assigned
reader display label = 通則
```

公開資料與 API 必須同時保留 `source_designation_raw`、`navigation_code`
及 `code_origin`。讀者頁只能顯示「通則」；若提供 `00` 作排序或進階篩選，
必須明示它是本專案編碼，不得寫成健保署的「第 0 章」。這個編碼不能用來
推論法律身分、ATC 階層或條文前後版。

## 日期註記是 completeness checksum，不是歷史全文

條文中的 `(112/6/1、113/6/1)` 等日期是高價值的 source-local amendment
markers。每一個原樣日期都必須先成為 `source_date_annotation`，保留：

- 原始字串與 ROC／Gregorian candidate；
- 它出現在哪一條、段、句及 exact source locator；
- 它標示整條、特定子項或僅是旁註；
- 對應公告／附件是否已找到；
- 是否已有該日期前後的完整全文快照；
- resolution status 與 unresolved reason。

但日期註記本身不能回答「那一版的完整文字是什麼」。它有至少六個限制：

1. 現行全文通常只保留今日仍存在的段落；被整段刪除的文字與日期會一起消失。
2. 同一條中不同子項可能有不同日期，標題的日期集合不是每一行的日期集合。
3. 公告可能用 `略`、合併儲存格或部分修訂，無法從現行全文逆推出舊全文。
4. 條號可能移章、改號、拆分、合併、刪除後恢復或被重用。
5. 日期可能是公告日、文件日、附註日或條件式生效日，必須回到來源判讀角色。
6. 排版或重新載入不能建立新的法律版本，即使資料庫寫入日不同。

因此日期註記的正確用途是三重核對：

```text
annotation recall
  = every accepted source marker was extracted exactly

event resolution
  = every marker maps to an official event/effect or an explicit unresolved gap

snapshot realization
  = every resolved transition has complete before/after snapshots and direct adjacency
```

最後再做 cumulative anchor replay：從較早的已驗證整份檔依生效順序重播所有
event effects，必須逐條得到下一個已驗證整份／分章 anchor 的 exact normalized
hash。單條只有在 annotation、event、snapshot、adjacency、anchor parity 與
source-universe cut 都無缺口時，才能標為 `complete_to_declared_cut`。

### 2026-07-27 舊庫實證

舊 `tw_drug.rule_*` 表是 discovery input，不是 canonical history。實庫稽核
得到：

- 1,548 條 current articles、3,691 個 legacy version rows；
- 3,112 個 version rows 沒有 `source_publication`；
- 78 個 version rows 沒有 effective date；
- 84 組同一條、同一 effective date 的重複 version；
- 980 條 current text 含日期註記，但 980/980 的註記日期集合都與 legacy
  version date set 不一致；
- 1,332 條在不同日期保存完全相同的全文。

Gilteritinib 是明確反例：current text 標示 `112/6/1、113/6/1`，舊 version
rows 卻為 `2023-06-01、2026-05-18、2026-05-21`，缺少 `2024-06-01` 的完整
快照，後兩筆又是相同文字。這證明「有日期」能幫助發現缺口，不能證明舊庫
已有逐條完整歷史。機器可讀結果見
[`audits/2026-07-27-legacy-history-date-annotation-audit.json`](audits/2026-07-27-legacy-history-date-annotation-audit.json)。

同日後續的 deterministic backfill 擴充為同時接受半形與全形斜線，並保存
所有斜線三元組而非先刪除。隔離 stage 對 1,548 條 current text 產生
6,366 個 exact raw candidate rows（983 條至少一筆；6,360 個可正規化為
有效日期，6 個則是待語境判讀的非日期候選）。
980 與 983 的差異不是資料漂移：用同一 sealed rows 唯讀重算，窄格式
`[0-9]{2,3}/[0-9]{1,2}/[0-9]{1,2}` 得 980 條；expanded parser 另外保留
三條來源中斜線前的空白：`3.3.12 = 99/2 /1`、`13.10.5 = 93/8 /1`、
`13.10.6 = 93/8 /1`。因此 6,366／983 是後續 raw extraction checklist
的固定分母，980 只保留為可重現的第一輪窄 regex 結果。機器可讀核對見
[`audits/2026-07-27-date-marker-denominator-reconciliation.json`](audits/2026-07-27-date-marker-denominator-reconciliation.json)。
對六個非日期候選逐筆讀取 exact context 後，全數是 Trelegy
Ellipta `92/55/22`／`184/55/22 mcg` 劑量字串，不是修訂日期；它們留在
raw extraction ledger，但以 `non_date_dosage_strength` 終結，不進公告
解析分母。故 raw pattern denominator 是 6,366，真正 amendment-date
denominator 是 6,360。公開 adjudication receipt 見
[`audits/2026-07-27-invalid-date-candidate-adjudication.json`](audits/2026-07-27-invalid-date-candidate-adjudication.json)。

6,360 個有效 candidate 分布在 323 個不同日期，範圍為
1996-01-01 至 2026-07-01；這讓公告 event ledger 有明確的 date
denominator，但同日仍可能有多個公告／多條 effect，不能只靠日期自動配對。
每列保留原始字串、Unicode code-point offsets、hash 與 source identity，
初態一律是 `unresolved_event`。重播與 fresh-connection row-set fingerprint
已通過，但 0/6,360 有效日期完成公告／transition resolution，因此不改變
`LEGACY_HISTORY_NOT_COMPLETE` 結論。公開 receipt 見
[`audits/2026-07-27-legacy-date-annotation-stage-receipt.json`](audits/2026-07-27-legacy-date-annotation-stage-receipt.json)。

逐條層級的 declared-cut scoreboard 因此是 0/1,548 certified complete：
983 條至少有一個有效日期候選但尚無 event/effect resolution；另外 565 條
未觀察到有效日期候選，也不能在來源宇宙尚未封閉時反推「從未修正」。這是
認證狀態，不是聲稱 1,548 條都必然存在遺漏版本。機器可讀報告見
[`audits/2026-07-27-per-clause-history-completeness-scoreboard.json`](audits/2026-07-27-per-clause-history-completeness-scoreboard.json)。

第一輪實際 event-resolution stage 再將這 6,366 個 raw candidates 與截至
2026-07-27 已進候選庫、均自 2026-08-01 生效的 5 個公告 effect 比對。
resolver 只接受「同一日期、相容條號、兩側 exact locator 都存在，而且公告
不是省略文字或多條合併」的唯一候選。結果為 6,360 `no_match`、6
`invalid`、0 `resolved_candidate`；這是預期的真實零命中，因為 5 個新公告
都晚於 legacy annotation source cut。它證明判定器會拒絕日期不相干的公告，
不代表舊公告來源宇宙已經補齊。公開 receipt 見
[`audits/2026-07-27-event-resolution-stage-receipt.json`](audits/2026-07-27-event-resolution-stage-receipt.json)。

為確認「有日期」究竟能把歷史搜尋縮小到什麼程度，另對 sealed 的
1999–2020 historical ODT stage 做唯讀 preflight。6,360 個有效 marker
聚合成 3,080 個條文×日期組合；1,897 組（61.59%）可在 240 份 ODT 找到
同一正規化日期。排除 70 組 project-assigned `0.x` 通則導航碼後，3,010
組正式數字條號中，只有 826 組（27.44%）能在同一 artifact 同時找到日期
與 exact 條號。另保存 6 個 legacy 及 3 個 ODT 無效日期的 exact locator。

這個 matcher 刻意只回答「哪裡可能有證據」，不回答「改了什麼」：
同一 artifact 出現日期與條號，仍可能是目錄、引用、修訂沿革、跨條表格或
不同 effect；也沒有證明日期是法律生效日、兩段文字直接相鄰，或 stable
identity 沒有 split／merge／move。

第二輪跨格式 preflight 將 ODT、431 PDF、347 OLE 與 147 ODS 的原生 typed
text 合併，仍以相同 3,080／3,010 denominator 重算兩次。原生文字的同日期
候選為 2,034/3,080，較 ODT-only 增加 137；同一 artifact 同時找到日期與
正式條號為 909/3,010，增加 83。13 份 standalone image 與 native zero-text
pages 的 OCR 另列為未人工覆核 lane：155 個日期候選、4 個 joint candidate，
只有 3 個 joint candidate 超出 native lane，且 authoritative text
observation 固定為 0。因此 61.59%、27.44%、2,034 與 909 都不是逐條文
完整率，正式 event resolution 仍是 0/6,360 valid dates；另 6 筆
non-date candidates 已終結分類。

跨格式 preflight 之後，909 個 joint pairs 再以兩份 exact-locator ledgers
與 historical acquisition 的 `resource_artifact_link` 反查 owning official
document number。全數都有至少一個文號候選：490 組唯一、419 組有 2–11 個，
合計涉及 282 個文號。這個 join 只證明「證據 artifact 屬於哪份正式公文」；
唯一 owning document 不證明該日期是生效日、該文號修改此條，或同檔 old/new
文字直接相鄰。實際資料已有明確反例：某些較晚公文附件是 cumulative rule
release，同一附件會同時命中該條在 2001、2004、2007、2011 與 2012 年的
source-local dates；owning document 只說明「誰發布這份累積檔」，不說明
每個舊日期各自屬於哪份原始修正公文。公開 receipt 與 909-row ledger 見
[`audits/2026-07-27-history-marker-document-candidate-preflight.json`](audits/2026-07-27-history-marker-document-candidate-preflight.json)
及
[`audits/2026-07-27-history-marker-document-candidate-evidence.jsonl`](audits/2026-07-27-history-marker-document-candidate-evidence.jsonl)。

任何公開 coverage 數字都必須作為一個完整 metric object 輸出，不得只複製
numerator：

```text
metric_name
numerator
denominator
population_definition
exclusion_reason_set
claim_limit
evidence_receipt
```

例如 `909/3,010` 的 population 是「排除 70 組 project-assigned 通則導航碼
後、可評估正式條號的 clause×date pairs」；`0 unmapped` 只適用這 909 個
已形成 same-artifact joint candidate 的 pairs，不得擴張為全部 3,080 pairs。
同理，19 個 leafmost loci 是 mismatch tree 的表面分類單位，不是
`19/33 resolved`；`0/1,548 certified complete` 也不是 1,548 條皆已證明
缺版。

ODT-only compact receipt 與 14,518 筆 locator ledger 分別見
[`audits/2026-07-27-history-marker-odt-preflight.json`](audits/2026-07-27-history-marker-odt-preflight.json)
及
[`audits/2026-07-27-history-marker-odt-evidence.jsonl`](audits/2026-07-27-history-marker-odt-evidence.jsonl)。
跨格式 compact receipt 與 exact locator ledger 見
[`audits/2026-07-27-history-marker-cross-format-preflight.json`](audits/2026-07-27-history-marker-cross-format-preflight.json)
及
[`audits/2026-07-27-history-marker-cross-format-evidence.jsonl`](audits/2026-07-27-history-marker-cross-format-evidence.jsonl)。
PDF extraction 的 hash-bound receipt 見
[`audits/2026-07-27-pdf-source-extraction-receipt.json`](audits/2026-07-27-pdf-source-extraction-receipt.json)。

147/147 ODS 也已完成 typed extraction。由於 ODS 的 repeated rows/cells
代表約 1.431 兆 logical cells，擷取器保存 1-based logical range、repeat、
span、formula、type、value 與 exact XML-node fingerprint，不把它們展開成
巨量假 rows；實際保存 6,521 個 physical cells，unsupported cells 為 0。
這些 rows 已接入 candidate-only marker matcher，但不因 typed parse 或
同檔命中就具有法律語意。公開 receipt 見
[`audits/2026-07-27-historical-ods-extraction-receipt.json`](audits/2026-07-27-historical-ods-extraction-receipt.json)。

13/13 image artifacts 另完成 deterministic RGB render 與受 macOS sandbox
禁止網路的 Tesseract `chi_tra+eng` OCR；可執行檔、語言模型、argv、環境、
pixel hashes 與 13 個 source locators 都綁入 receipt。13 個 OCR observations
皆非空，共 11,959 characters，但全部仍是未人工核對的 observation，
`needs_visual_review=13`、`human_verified=0`、`authoritative_text=false`。
公開 receipt 見
[`audits/2026-07-27-historical-image-extraction-receipt.json`](audits/2026-07-27-historical-image-extraction-receipt.json)。

347/347 OLE artifacts 另完成完整 CFB directory／stream inventory
（2,153 streams 全讀取並 hash），依實際 container subtype 分成 328 份
Word DOC 與 19 份 Excel XLS。325 份 Word 與 19 份 Excel 取得 typed
paragraph／table／cell output；3 份 Word 沒有可抽取的 OOXML body text，
但經 renderer 定位與人工目視確認共 5 頁皆有影像文字或表格，因此分類為
`needs_image_ocr_or_visual_review`，而不是「空文件」或成功抽完。LibreOffice、
xlrd、pdfinfo、network-denied sandbox、每個 stream 與每個 typed row 都以
版本／SHA 綁定。這仍只關閉 bounded input 的格式擷取分流；不解析法律
日期、事件、stable identity 或相鄰版本。公開 receipt 見
[`audits/2026-07-27-historical-ole-extraction-receipt.json`](audits/2026-07-27-historical-ole-extraction-receipt.json)。

「現在是否完整」另以
[`database/queries/history-completeness-status.sql`](../database/queries/history-completeness-status.sql)
直接查 live stage。查詢在 `READ ONLY` transaction 中執行，對 stage
不存在、全部 unresolved 或 canonical schema 不存在都 fail closed；它不會
因為有漂亮的公開文件就推論實庫已完成。

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
| [NHI 最新整份檔](https://www.nhi.gov.tw/ch/cp-13108-67ddf-2508-1.html) | terminal cumulative anchor | 已程式化擷取；title/update metadata 不是生效日，同 URL 會變動 |
| [NHI 最新分章檔](https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html) | terminal chapter anchors | 已程式化擷取；仍需與同批 whole 做逐條文字一致性檢查 |
| [NHI 法規公告 listing](https://www.nhi.gov.tw/ch/lp-3258-1.html) | listing/detail/attachment observation | 2026-07-27 兩輪完整列出 858 筆、43 頁並抓取 858 details；兩輪 stable projection 均為 2,400 個附件 URL、5 個零附件頁；2,400/2,400 附件已抓取並 sealed 到 PG acquisition stage，最舊只到 ROC 111 |

Listing DOM 在 capture 當日是 `section.list > table.rwdTable`，分頁為
`lp-3258-1.html?pi=N&ps=20`；舊 `div.listTb`／path-page 假設會安靜讀成
0 rows。新 adapter 對六欄 header、1..858 顯示序號、三種原始日期欄位、
43 頁 total declaration 與 exact query contract fail closed，並把 858
rows 全保留後才允許下游分類。兩輪 resource JSONL SHA-256 均為
`059bd09fcd96d91d6478883a643e48c151e218a7623fed90d877e0bf68fb9c01`。
Detail HTML 另做兩次獨立 raw acquisition；所有 858 組 hashes 都因官方
瀏覽計數與 challenge 欄位而不同，但去除 raw-only volatile identity 後，
detail locator／attachment order／label／URL 與零附件集合完全相同。兩輪
各有 2,400 occurrences、2,400 canonical URLs、5 個零附件 details，且各自
對同一 sealed input 的離線重播 byte-identical。公開 cross-pass receipt 見
[`audits/2026-07-27-nhi-listing-detail-expansion-cross-pass-parity.json`](audits/2026-07-27-nhi-listing-detail-expansion-cross-pass-parity.json)。
附件發現後再以 fresh run 抓取 2,400/2,400 official URLs，共 2,396 unique
artifacts、476,139,573 bytes、0 issues；offline verify 後 sealed 到
append-only `tw_drug_history_acq_stage`。公開 receipt 見
[`audits/2026-07-27-nhi-listing-attachment-acquisition-receipt.json`](audits/2026-07-27-nhi-listing-attachment-acquisition-receipt.json)。
這只關閉 bounded listing capture 的附件「發現＋原始 bytes」分母；typed
內容分類、公告 relevance 與法律 effect 仍分開驗收。
這證明 listing index 可重播，不代表 858 個 details／attachments 或法律
event 已完成。公開 receipt 見
[`audits/2026-07-27-nhi-listing-discovery-parity.json`](audits/2026-07-27-nhi-listing-discovery-parity.json)。
| [MOHW 函釋歷史查詢](https://mohwlaw.mohw.gov.tw/FINT/FINTQRY01-1.aspx) | post-109 announcement/detail/attachment candidates | `RowNo` 隨 query 改變；正式文號、detail bytes、PFID 與 artifact hash 必須分存 |

2026-07-27 的 bounded production run 以完全相同 query profile 查詢
`2021-01-01..2026-07-27` 及關鍵詞 `藥品給付規定`，得到：

- 6 個固定年度 partition、366 筆 formal document numbers；
- 兩次獨立列舉都得到 1,719 個相同 resource keys；
- 文書日期從 ROC 110-01-15 到 115-07-15；
- 366/366 detail pages 至少有一個附件；
- 1,353 個 unique PFID：669 PDF、360 ODT、299 ODS、11 XLS、
  11 DOC、2 XLSX、1 DOCX。

這 366 份正式公文也已依同一 deterministic bundle contract materialize：
1,353 attachments、1,719 resources 全部 offline verify，第二次執行為
byte-identical replay。原 acquisition source plan 固定保存為
[`source-plan-post109-exact-phrase.json`](../sources/source-plan-post109-exact-phrase.json)，
不受後來在 working plan 啟用 current-anchor adapters 影響。公開 receipt 見
[`audits/2026-07-27-post109-notice-bundle-materialization-receipt.json`](audits/2026-07-27-post109-notice-bundle-materialization-receipt.json)。

同日另以 current-anchor 專用 source plan 對 NHI 最新整份／分章頁獨立列舉
兩次。兩次均得到 1 個整份 group／3 個附件、93 個分章或附表 group／263
個附件，合計 268 個 resource IDs；key-set SHA-256 完全一致。266 個附件
實抓後形成 267 個 unique artifacts（包含兩個 page bytes）、57,999,120
bytes、0 blocking issue；92/92 ODT 再轉為 44,504 個 structural blocks 與
1,322 個 occurrence candidates，已 sealed 入 PostgreSQL。這關閉的是
「現行官方 anchor 是否確實取得」的 acquisition 缺口，不是 whole↔chapter
逐條 parity，也不是生效日或歷史完整性。公開 receipt 見
[`audits/2026-07-27-current-anchor-capture-receipt.json`](audits/2026-07-27-current-anchor-capture-receipt.json)。

同批資料另做保守的條號／標題 occurrence multiset preflight。正規化僅做
NFKC、移除空白、去掉條號末端標點；不改寫標題內日期、逗號或文字。結果：
整份 662、分章 660，655 個 occurrence 相符，整份獨有 7、分章獨有 5。
差異包括日期標題不一致、刪除標點，以及整份檔仍有而分章檔沒有的
`9.32.1`、`9.32.2`。因此本項狀態是 `mismatch_detected`，不能聲稱同批
whole／chapter 逐條相同。這個 preflight 也沒有比較完整條文 body，後續仍
須以 source locator 重建每條全文、判讀真正的 source-layout 差異，再做
normalized text/hash parity。公開結果見
[`audits/2026-07-27-current-anchor-occurrence-parity-preflight.json`](audits/2026-07-27-current-anchor-occurrence-parity-preflight.json)。

完整條文重建隨後已執行。分析器先驗證 sealed manifest、三個 structural
檔案的 size/hash、parse run、每份 ODT 的單一 resource membership、
連續 `doc_order`、occurrence→block locator/hash，以及所有可能影響文字的
parse issue；再從每個條號 heading 收到下一個同層或祖先 heading，保留
flow、table 與 list blocks。整份與分章各重建 639 條，606 條逐條全文 hash
相同，33 條不同，無 reconstruction blocker，結果為 `parity_failed`。
條文邊界採階層 subtree 語義，因此子條文差異也會讓其父條文 hash 不同；
33 是 mismatch designations，其中 19 個是 leafmost mismatch；這仍不等於
19 個互相獨立的法律修正事件。

19 個 leafmost rows 的 exact character diff 再分為 6 個版本／日期內容、
6 個 list-marker 結構、6 個純標點與 1 個尾端補充表 layout。最後一類來自
分章檔在最後 dotted clause 後附表，現有 subtree boundary 將它掛到
`3.3.31`，不是已證明的條文內容。更重要的是 `8.2.16` 分章檔已有
115/8/1 future-effective 文字，而 capture cut 是 115/7/27；因此較新的
官方檔也不能在生效日前直接成為今日 current。公開分類見
[`audits/2026-07-27-current-anchor-leafmost-diff-classification.json`](audits/2026-07-27-current-anchor-leafmost-diff-classification.json)。

因此 terminal anchor 不是「尚未比較」，而是「已比較並發現 33 個官方來源
差異」；在逐一裁決前不得選任一側寫入 canonical current。完整 source-span
receipts 見
[`audits/2026-07-27-current-anchor-full-clause-parity.json`](audits/2026-07-27-current-anchor-full-clause-parity.json)。

這證明一個可程式重建的 bounded source set；它仍不能證明所有會影響藥品
給付規定的官方函釋都一定含有這個完全相同的字串。後續 source-universe
closure 必須加入明定的查詢 partition/同義 query 與跨來源 discrepancy。

為把「跨來源 discrepancy」變成可驗收資料，本專案已將 NHI 858 筆
listing/detail metadata 與 FINT 2021–capture-cut exact-phrase 366 筆，
按正規化正式文號分組，不強作一對一 join。NHI 形成 847 個鍵，FINT 形成
365 個鍵；交集 217、NHI-only 630、FINT-only 148，union 995。另有 7 個
ambiguous keys（NHI 6、FINT 1），證明正式文號本身也不能直接當 stable
rule identity。NHI 兩次 detail HTML 的 bytes 因 volatile page fields
全數不同，但 parsed metadata projection 完全一致；listing 與 detail 的
文號／公文日期無 mismatch，主旨只有一筆 raw whitespace 差異。

此對帳只建立 source-surface discrepancy inventory。NHI listing 的觀察起點
是 2022-09-06，FINT bounded set 從 2021-01-01 開始；source-only row 可能
來自時間窗或索引差異，不自動等於漏掉法律事件。listing date、document
date、capture date 與 file date 均不得當法律生效日。機器可讀 receipt 見
[`audits/2026-07-27-nhi-fint-source-universe-reconciliation.json`](audits/2026-07-27-nhi-fint-source-universe-reconciliation.json)。

同一 adapter 隨後以另一份公開 source plan 回溯
1996-01-01..2020-12-31，仍只查精確詞 `藥品給付規定`。修正空結果頁的
辨識後，兩輪獨立列舉都得到 942 detail／1,178 attachments／2,120
resources，key-set SHA-256 完全相同；1996–1998 各為 0 筆，第一批命中
始於 1999。Corrected run 全數實抓為 2,120 artifacts／91,694,925 bytes，
240/240 ODT 解析為 13,995 blocks／676 occurrence candidates 並 sealed
入 PostgreSQL。附件另含 431 PDF、347 OLE、147 ODS、9 JPEG、3 GIF、
1 TIFF；這些非 ODT 格式已保留 exact bytes，但尚不能宣稱 effect text
解析完成。公開 receipt 見
[`audits/2026-07-27-historical-events-exact-phrase-capture-receipt.json`](audits/2026-07-27-historical-events-exact-phrase-capture-receipt.json)。

同一 bounded input 隨後依正式文號形成 942 個 deterministic source-local
notice bundles。每份 bundle 綁定 exact detail artifact、全部 child
attachments、官方順序、media type、size、SHA-256、source plan 與 raw
manifest；合計 1,178 個附件、2,120 resources、91,694,925 bytes 全數
offline verify，第二次執行為 byte-identical replay。這個結果特別避免了
「只有 ODT 才算公告」的偏誤：PDF-only、image-only、OLE、ODS、多附件與
零附件形式皆可完整保存。它仍然不證明法律生效日、stable clause identity
或逐條文歷史完整性。公開 receipt 見
[`audits/2026-07-27-historical-notice-bundle-materialization-receipt.json`](audits/2026-07-27-historical-notice-bundle-materialization-receipt.json)。

第一次 raw stage 依錯誤的 `text/html` response header 把 13 份早期
GIF/JPEG/TIFF 附件標成 HTML；magic-byte detector 修正後已建立新的 sealed
run。舊 run 保留為 immutable audit evidence、不得作 release input。這
13 份掃描件必須走 image OCR／visual transcription 並保留 page-level
locator，不能因不是 ODT 而從完整性分母消失。

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

截至 2026-07-27，canonical promotion migration 仍是未套用 live 的草案。
PostgreSQL 內建能力可以核對 PDF bytes 與 ODF ZIP 的外層結構，卻不能獨立
完成所有壓縮 payload 的解壓、CRC 與 XML 完整性驗證；一個外層合法但
`content.xml` 已損壞的 ODT 曾通過兩個 SQL classifier。故目前的 fail-closed
邊界是：任何 release-linked ODT 或 ODS 都只能形成 observation，固定
`promotion_eligible=false`，並以
`blocked_pending_external_archive_integrity_verifier` 阻擋 canonical write。
合法 ODT 也一樣阻擋；不能用「看起來是有效 ZIP」當成功 lane。

PDF 也不能只憑 `%PDF-` magic 認定完整；獨立測試曾讓 51-byte 假檔通過兩個
SQL classifier。故 PDF receipt 也固定 `pdf_integrity_verified=false`、
`promotion_eligible=false`，並以
`blocked_pending_external_pdf_integrity_verifier` 在第一筆 canonical write
前阻擋。現行三個 format policy 均沒有 promotion 正向 lane。未來只有在完成
governed external full-document verifier、可驗簽 receipt 與獨立 replay
contract 後，才可另行設計 promotion；目前 migration 只保存 fail-closed
contract，未核准套用 live。

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
