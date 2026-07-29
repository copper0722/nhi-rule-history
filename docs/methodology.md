# 健保藥品給付條文歷史：公開方法學

狀態：`canonical_method_v4`
更新：2026-07-29

## 一句話

**最新版分章檔負責現行條文；年度整編檔提供逐條 source observations
與消失偵測；條文日期及公文用來解釋觀察到的差異。**

「Git-like」只是一個內部工作比喻：年度檔像定期 snapshot，方便找出兩次
觀察之間哪裡不同。年度檔不是法律上的 commit，兩個相鄰快照也未必是直接
相鄰的法律版本。公開資料一律把 `source observation`、`observed delta`
與 `adjudicated legal version` 分開。

這三種來源不能互相冒充：

| 官方來源 | 資料單位 | 能證明 | 不能單獨證明 |
|---|---|---|---|
| 最新版分章檔 | 現行章節／附表 | 目前官方公布的條文全文 | 每一次歷史修訂 |
| 修訂公文及附件 | 一次發布事件 | 修訂範圍、前後文字、生效日與文號 | 全庫沒有漏件 |
| 84 年原始掃描、96.7–109 年度整編檔 | 官方全文觀察 | 某截點的全體條文 state、跨觀察出現／改文／消失 | 年內精確修訂日、直接法律前版或未觀察到的中間版 |
| 條文內民國年月 | 存活文字的日期註記 | 搜尋與核對 amendment 的強索引 | 已整條刪除的條文或完整版本鏈 |

## 兩條互不阻擋的產品線

```text
Track A：現行條文
最新版分章頁 → 原始附件 → deterministic parse → 單一條文 PG
                                             → JSONL / SQLite / 網站

Track B：逐條歷史
官方全文 → 單一條文 observations → 相鄰 observation delta／消失偵測
       + 條文日期 + 公文／公報／館藏 → transition evidence union
       → proofread／identity-lineage review → PG legal version chain
       → direct-predecessor diff → JSONL / SQLite / 網站
```

Track A 不等待 Track B。只要現行分章來源、條文邊界、原文與更新時間通過
deterministic gate，就可以先發布「現行條文」。官方全文可建立有明確觀察
區間的變更候選；尚未取得事件證據時不得把後一快照日期冒充生效日，不得把
兩個快照間的 diff 冒充一次公告全文，也不得把 observations 自動升格為
direct-predecessor versions。

## Track A：先發布現行條文

唯一現行文字來源是健保署
[最新版藥品給付規定內容（分章節）](https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html)。

每次更新依序：

1. 枚舉頁面明列的全部章節與附表 group。
2. 保存 detail HTML、ODT、DOC、PDF 的 URL、大小、SHA-256 與取得時間。
3. 以 ODT 作主要結構解析；其他格式作原文與缺漏核對。
4. 依官方條號與文件結構切成**單一條文**，整章不能成為一個版本。
5. 將完整原文、條號、章節、來源 locator、官方更新標示寫入 PostgreSQL。
6. 從同一 sealed PG projection 產生網站、JSONL 與 SQLite。
7. 網站標示「現行條文／官方分章頁觀察日期」，沒有已核實歷史的條文不顯示
   虛構沿革。

目前 sealed structural evidence 已可重建 639 條現行條文。整份檔與分章檔
有 33 條全文差異；Copper 已指定分章頁為現行文字正典，因此分章檔投影
優先，差異保留成 conflict observations。這個 current-source policy
不裁決歷史版本，也不允許把較方便解析的整份檔靜默覆蓋分章原文。

## Track B：回頭逐條重建公文歷史

### 來源不是年代二分，而是 evidence union

健保署父層將一個入口標為「修正規定（自103年4月3日以後生效之公告）」，
但 2026-07-29 實測其 target listing 是 859 筆／43 頁，最舊可見列為
111-09-06，且表格含「刊登期限」。因此不能把 103-04-03 當成「此後資料
一定完整存在於目前列表」的分界。

每一個 clause transition 都同時查下列可得路徑，取聯集而非先驗擇一：

- 年度整編快照與最新版分章檔；
- 條文內民國年月；
- NHI RSS、listing、detail、附件及已知 URL；
- MOHW FINT 的日期、文號、條號、成分、商品及前身名稱 targeted queries；
- 行政院公報、檔案館、國圖／臺灣記憶及政府出版品；
- 已取得正式文號的跨入口回查。

FINT 四個關鍵字全空的年度列舉只用於 bounded surface audit，不是逐條重建
的前置作業。任何入口零命中只能得到 `not_found_after_declared_search`，
不能得到「公文不存在」。

### 84–87 年前身名稱與可得性

早期檔案不能只用後來的「藥品給付規定」查：

- FINT 現存的 `健保醫字第84010140號` 詳情頁及附件證明，84-06-20 公告
  `全民健康保險藥品使用規範`，除特別明定者外自 84-07-01 實施；
- 國圖數位典藏目錄的 `D9507418`／`84衛技字第052484號` 記錄一份
  84-07 函轉該規範的公文；
- 同一健保署官方紀錄記載 87-03-04 重新編排並改名為
  `全民健康保險藥品給付規定`，87-04-01 實施。

因此 85 年不是「沒有規範」，而是名稱與後期不同。84 年 25 頁完整官方
掃描已取得、hash 封存並進 PostgreSQL acquisition stage；但它沒有實質
文字層，OCR 仍待逐頁 proofread。精確的 85/1/1 修正文仍未找到：條文句子
為 0 筆，日期 token 的命中亦全數不相關。正確狀態是
`84_baseline_acquired / 85_event_not_found_after_declared_search`，不能因
搜索未命中而宣稱不存在。來源可得性逐件使用：

```text
born_digital_official
digitized_scan
catalog_only
later_official_reproduction
paper_holding_not_digitized
availability_unresolved
```

取得與查詢收據見
[`2026-07-29-fint-84-baseline-acquisition.json`](audits/2026-07-29-fint-84-baseline-acquisition.json)；
版本考據見
[`2026-07-29-early-baseline-forensic-analysis.md`](audits/2026-07-29-early-baseline-forensic-analysis.md)。

早期系譜的 machine-readable claims 與 source-state 限制見
[`2026-07-29-early-rule-source-lineage-check.json`](audits/2026-07-29-early-rule-source-lineage-check.json)。

國史館臺灣文獻館的「臺灣省政府衛生處」全宗目前宣告 12,992 筆，但寬查詢
的可見導覽停在第 10,000 筆；遞增排序的第 9,981–10,000 筆落在 1967 年，
所以不能據此說館藏只到 1967。限定 1995 年只得到一筆不相關卷，精確文號與
完整題名都為 0 筆。這條路徑目前是 `not_found_after_declared_search`，
不是 absence proof；機器可讀紀錄見
[`2026-07-29-taiwan-historica-health-department-search.json`](audits/2026-07-29-taiwan-historica-health-department-search.json)。

### 統一的公文搜尋工作單位

搜尋不是以「所有政府公文」為分母，而是以一個可稽核工作單位進行：

```text
stable clause candidate
× one amendment-date / snapshot-difference candidate
× one declared source cut
```

每個工作單包含：

- 現行條號、歷史條號與名稱別名；
- 條文內全部修訂日期註記及 exact source span；
- 前後年度快照的完整條文與 diff（只作搜尋提示）；
- 成分名、商品名、適應症與高特異詞；
- 搜尋入口、查詢參數、日期窗、分頁結果與執行時間。

### 固定搜尋階梯

每個候選事件依序執行，所有查詢都保存 ledger：

1. **日期＋固定法規詞**：在候選日期前後的窄窗口查
   `藥品給付規定`、`給付規定`、`修訂`、`暫予支付`。
2. **條號／歷史名稱**：查現行及已知舊條號、條文標題與名稱別名。
3. **成分／商品**：查 exact Latin ingredient、brand 與中文特異名稱。
4. **正式文號回查**：一旦任一來源取得文號，以正規化文號回查全部 detail
   與附件。
5. **跨入口補查**：NHI、FINT、公報、檔案館與館藏各自保留 bounded
   search receipt；入口間不互相替代。
6. **年度 state 對帳**：把同一條文的 observations 依 edition time 排列，
   先產生 appearance／text-change／disappearance observations；再用公文、
   日期與 identity evidence 判定可能的 create／amend／delete／restore／
   move 及其 transition time。

零命中只能得到 `not_found_after_declared_search`，不能得到「公文不存在」。
同日多份公文、單一公文修改多條、split／merge／move 或附件 old/new 欄位
省略時，一律進 agentic review。

## 公文 bundle：歷史的 immutable raw

每份公文以正規化文號建立 source bundle，但文號不是條文主鍵。Bundle 至少
包含：

```text
detail HTML
detail metadata（主旨、文號、發文日、公告事項）
該 detail 頁宣告的全部附件
每個 URL、label、byte length、SHA-256、media type
取得時間與查詢 occurrence
```

沒有 ODT、只有 PDF／DOC／ODS／影像、零附件、同文號多頁或附件名稱錯掛都
必須如實保存。`attachment declared`、`bytes fetched`、`relevant to this
transition` 是三個不同欄位。

## Agentic workflow：raw → proofread → transition

公文像 `raw.md`：機器抽出的文字可能有表格錯序、OCR 錯字、全半形與跨頁
問題，不能直接寫入條文歷史。標準流程分成三個不可混淆的產物。

### 1. `source_transcript_raw`

- 由 deterministic parser 產生；
- 保留原格式順序、頁碼／表格／儲存格 locator、原始字元與 extraction issue；
- 不修字、不合併條文、不判斷法律效果。

### 2. `source_transcript_proofread`

- agent 必須閱讀完整 bundle，不只讀搜尋摘要或單一 chunk；
- 逐段對照 ODT／PDF／DOC／ODS／影像，修正抽取錯誤；
- 每個修正保存 raw span、proofread span、reason 與 reviewer state；
- 不得補寫來源沒有的省略文字；
- proofread 的任務是**忠實轉錄**，不是裁決條文身分或生效日。

### 3. `transition_proposal`

另一個明確步驟才判讀：

- 公文影響哪些單一條文；
- create、amend、delete、restore、rename、move、split、merge 或 correction；
- 完整 before／after text；
- 發文、刊登、生效、失效日期的不同角色；
- 直接前版、條號沿革與 exact evidence locators。

單一公文修改多條時，先 deterministic partition 成多個 clause work units；
agent 不得把整份公文當成一條版本。

### 三條時間軸不可合併

每一個來源 observation 與法律版本至少分開保存：

- `source_observed_at`：系統何時抓到這份資料；
- `source_edition_date`：來源自稱是哪一版／哪個截點；
- `legal_effective_from/to`：只有直接官方文字支持時才填的法律生效／失效
  時間。

只有前兩者時，變更時間寫成 `appearance_window`，不從檔名或下載日推算
法律生效日。

### 文字忠實度不可藏在一個 text 欄

同一段至少可保有：

- `exact_source_text`：原始可抽取文字或逐頁校對後的忠實轉錄；
- `comparison_text`：只供 diff/search，依版本化 normalization policy
  忽略空白、單引號、全半形等；
- `ocr_observation`：未校對 OCR，永遠不能成為 exact legal text；
- `display_markdown`：程式化呈現層，不回寫原文。

所有 diff 必須指明使用哪個 comparison policy；exact text、normalized
text、OCR confidence 與 proofread state 不得混成單一「可信度」分數。

### 驗證與 promotion

```text
agent proofread / proposal
  → deterministic schema + span + hash validator
  → independent full-bundle reviewer
  → replay against annual/current anchors
  → PostgreSQL staging
  → independently authorized promotion
```

產生資料的 agent 不能接受自己的結果。任何 exact span 不存在、before/after
不完整、日期角色不明、direct predecessor 不唯一或 replay 不合，都只能輸出
gap，不得靠語言模型補齊。

## PostgreSQL 的最小正規化模型

```text
source_document
source_artifact
source_observation
source_transcript_raw
source_transcript_proofread
proofread_change
clause_observation
observation_delta
clause_identity
clause_designation
clause_lineage_edge
clause_version
clause_version_evidence
rule_transition
transition_document_link
comparison_edge
diff_hunk
history_coverage
notice_search_observation
```

重要約束：

- 先容許 source segment／observation 暫時沒有 canonical identity；完成
  split／merge／move／number-reuse review 後才連到 `clause_identity`；
- 一個 `clause_identity` 一條獨立 legal version chain；
- `clause_lineage_edge` 明載 `continues_as / moves_to / splits_into /
  merges_into / replaced_by / unrelated_number_reuse`；
- 一個公文可連多條 transition，一條 transition 也可有多份公文；
- 歷史 transition 必須至少連到一份已 proofread、已覆核的直接官方
  evidence；可為公文、old/new 對照、累積版、日期註記加完整前後文或官方
  archival snapshot；
- 官方全文先進 `clause_observation`；相鄰 observations 只建立
  `appearance_observed / text_change_observed / disappearance_observed`
  中性 delta，不能先命名 create／amend／delete；
- 只有 identity 與事件角色完成 review 後，observation 才能作
  `clause_version_evidence`；
- 每個 published version 最多一個 direct predecessor；
- diff 只比較 direct predecessor；
- JSONL、SQLite 與網站皆由 sealed PostgreSQL 單向產生。

## 可 deterministic 與必須 agentic 的邊界

| 工作 | 類型 |
|---|---|
| 網頁分頁、detail、附件下載與 hash | deterministic |
| ODT/XML、HTML、可抽取 PDF 的結構解析 | deterministic |
| 日期正規化、查詢展開、候選排序 | deterministic |
| 完整字串相同判定與 direct diff 計算 | deterministic |
| OCR／表格校對、跨格式忠實 proofread | agentic + review |
| 一份公文影響哪些條文 | agentic + deterministic validation |
| split／merge／move／number reuse | agentic + review |
| 生效句 scope 與 direct predecessor | agentic + replay |
| PG promotion、JSONL／SQLite／網站輸出 | deterministic after approval |

## 完整度怎麼計算

逐條文，而不是整章或整個網站計算。PG 保存 completeness vector，不用
單一總分掩蓋不同缺口：

1. 現行全文已由最新版分章檔驗證。
2. `source_coverage`：宣告來源面、日期窗與查詢 receipts 可重播。
3. `observation_coverage`：已取得全文是否完整切成單一條 observations。
4. `marker_disposition`：每個修訂日期註記都有 terminal disposition。
5. `identity_resolution`：same number、move、split、merge、restore 與
   number reuse 已有 lineage disposition。
6. `legal_event_resolution`：每個已發布相鄰法律版本都有完整 before／after
   與 accepted official evidence；只有 appearance window 時如實保留。
7. exact 生效日必須有 direct official locator；只有快照支持時明示 interval
   precision，不冒充 exact date。
8. `replay_coverage`：從最早已知版本重播後，逐年度 anchor 與最新版全文
   全部相符。
9. `diff_coverage`：direct-predecessor diff 可重建，且 exact/comparison
   文字與 normalization policy 都可追溯。

任何一項未通過，該條只能標「現行條文」或「部分歷史已核實」。年度快照數、
附件數、公文搜尋命中數及 agent 完成數，都不能代替逐條完整度。

讀者端只使用三種可驗證語句：

- `官方生效日已核實`；
- `在 A 與 B 兩個官方全文觀察之間出現變化，精確日期未核實`；
- `截至宣告的來源與日期範圍，未找到對應公文`。

不得顯示「首次出現即生效」、「未找到所以不存在」或把 observation delta
寫成「本次修法」。

## 公開輸出

- 現行條文可以先發布。
- 已覆核的歷史逐條追加；未完成條文不偽造空白版本。
- 最新全文置頂，歷史預設只顯示每版相對直接前版的實質 diff。
- PG export 為 normalized JSONL；同一 JSONL 可建 SQLite。
- 公開資料保留官方 URL、文號、來源 hash、proofread/review 狀態與 declared
  cut，讓第三方能重算。
