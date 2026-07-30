# 條文正規化與版本比較：完整實作流程

這份文件只回答一件事：一份新的健保給付公告出現後，系統如何在不改寫
官方文字的前提下，變成 PostgreSQL 裡可查、可比較、可自動上網的條文。

## 先看懂三種工作

本文用三個標籤：

- **D — Deterministic（機械式）**：同一輸入必須得到同一輸出；可用程式、
  雜湊、資料庫限制與測試重播。
- **A — Agentic audit / presentation（代理稽核／閱讀編排）**：模型找
  異常、反例與新格式，提出「應增加哪一條機械規則」。當 Copper 明確核准
  某一複雜版本退出通用 renderer 時，模型也可撰寫來源綁定的閱讀順序與
  結構解說；模型仍不能改官方文字、表格、代碼、門檻或 exact diff。
- **H — Human gate（人工裁決）**：只處理來源互相矛盾、條文身分不明或
  會改變法律意義的決定。

正常更新應幾乎全部走 **D**。**A** 和 **H** 是例外處理，不是每次公告的
必經人工編輯。Agentic reader profile 是 presentation lane，不會成為另一個
條文 Expression。

## 五條不變原則

1. **官方原文永遠保留。** 正規化資料是原文的可重播投影，不取代原文。
2. **一個版本的正典單位是單一條文。** 整章只是來源文件與發行包。
3. **只有已證實相鄰的版本才稱「與上一版本差異」。** 若中間是否還有版本
   尚未證實，只能標示「與舊版本差異」，並清楚寫出它比較的是前一個
   *目前可取得* 的版本。
4. **前端不猜。** 條列階層、表格、合併格、重複值與 diff 都由 PG 提供。
5. **模型不能補資料。** 遇到無法機械判斷的結構，保存
   `unresolved_structure`，不能猜一個看起來合理的答案。

## 借用既有標準，不自創法律文件語言

資料模型採 **Akoma Ntoso-inspired relational projection**。意思是借用
OASIS 法律文件標準的概念與名稱，但以 PostgreSQL 關聯表實作；本專案不會
假稱輸出已完整符合 Akoma Ntoso XML。

對應方式：

| 本專案 | Akoma Ntoso 概念 | 用途 |
|---|---|---|
| 條文跨版本的長期身分 | Work | 「2.6.1」這一條本身 |
| 某日生效的完整條文 | Expression | 例如 115/9/1 版 2.6.1 |
| 官方 ODT／PDF／網頁附件 | Manifestation | 文字從哪個具體檔案來 |
| 條文、段落、項目、子項 | clause / paragraph / point / subparagraph | 保存條列樹 |
| 表格 | table | 獨立結構元件 |
| 變更日期與公告事件 | lifecycle / temporalData | 版本何時、由何公告造成 |

參考：

- [OASIS Akoma Ntoso 1.0](https://www.oasis-open.org/standard/akn-v1-0/)
- [Akoma Ntoso 結構詞彙](https://docs.oasis-open.org/legaldocml/akn-core/v1.0/akn-core-v1.0-part1-vocabulary.html)
- [Akoma Ntoso 識別命名](https://docs.oasis-open.org/legaldocml/akn-nc/v1.0/csd02/akn-nc-v1.0-csd02.html)

## PostgreSQL 中實際保存什麼

### 1. 來源層：官方檔案到底寫了什麼

- 官方網址、文號、公告日、生效日。
- 原始附件 bytes 與 SHA-256。
- 每一個 source block 的原文、原始順序、來源頁／表／列／格位置。
- ODT、PDF、年度快照或前一版各自的來源 lane。

這一層不得因排版優化而改動。

### 2. 版本層：Work、Expression 與完整性

- `clause_work`：跨版本的條文身分。
- `clause_expression`：單一條文的一個完整版本。
- `expression_completeness`：
  `source_complete`、`verified_composite`、`patch_only`、`partial` 或
  `unresolved`。
- `effective_from`、`effective_until`。
- 造成版本的公告事件與附件。
- 完整原文、block manifest、雜湊與封存收據。

只有 `source_complete` 或 `verified_composite` 可以進入「最新條文」。
`verified_composite` 必須有不可變的 composition manifest，逐段指明來源、
組合規則與審查收據。`patch_only` 只能待在公告異動區，不能因為解析成功就
冒充完整條文。

Composition manifest 採內容定址：`sha256:<manifest_sha256>` 本身就是
manifest identifier。每個 component 都要保存 assembly ordinal、來源
artifact SHA-256、source block／locator、角色、逐字文字雜湊，以及組合後
的 scalar／UTF-8 byte 範圍。分隔字元也是版本化 assembly rule 的一部分，
不能藏在 renderer。完整性 gate 必須重新計算 manifest hash、完整文字 hash
與所有 range；只保存一個「已核准」布林值不算證明。

版本先後另存 `clause_expression_relation`：

- `direct_predecessor_verified`：已由正式來源證實法律上直接相鄰；
- `previous_available_expression_only`：只是目前找得到的前一版；
- `unresolved`；
- `conflicted`。

不得由檔案年份、匯入順序、文字相似或條文號相同，自動推定
`direct_predecessor_verified`。

### 3. 文件樹：條文內的十點就是十個節點

每個版本有一棵文件樹。節點種類至少包括：

- 條文標題；
- 小標；
- 一般段落；
- 項目；
- 子項目；
- 表格；
- 附註。

每個節點保存：

- Expression 內的 `node_id`；
- `parent_node_id` 與階層深度；
- 原始編號 `marker_raw`，例如 `1.`、`（一）`、`一、`；
- 編號格式 `marker_scheme`；
- 可確定時的數字序位；
- 去除編號後的內容與雜湊；
- 指回原始 block 內 scalar 與 UTF-8 byte 範圍的精確 span；
- 可選的跨版本 `node_work` 身分；
- `node_identity_assertion`，分開保存：
  - `identity_resolution_status`：
    `unassigned`、`candidate`、`verified`、`version_local`、`conflicted`；
  - `identity_basis`：正式來源對應、精確前版對應、marker path、結構角色
    或經審查裁決；
- 多對多 `node_lineage_relation`，可表達延續、移動、拆分、合併、取代與
  編號重用。

因此，一條文若有 10 點，不是存成一大段再交給 CSS 猜，而是 10 個可搜尋、
可定位、可逐點比較的 `point`。原始未切割全文仍完整保留。

`work_node_key` 可以保留為方便閱讀的衍生鍵，但不能當成正式身分。像
`2.6.1/（一）/1.` 會因插入、改號、移動、拆分或合併而改變。只有
`identity_resolution_status=verified` 才能驅動「移動」標籤或穩定跨版本
對齊；相似度只能排出待審候選。

source mapping 不是「一個 block 對一個 node」。正式規則是：

- 每個來源字元由一個 primary leaf span 擁有，不能遺漏或重複；
- container 可以沒有自己的來源文字；
- 一個 node 可以吃多個有序 span；
- 一個 block 可以切成多個不重疊 span；
- table cell 的文字由 cell-content layer 擁有，不再同時算到 table node；
- 依來源順序重組所有 primary span，必須 byte-for-byte 還原單條文原文。

### 4. 表格：附屬於條文，但本身是正式元件

每張表保存：

- 表格角色與 renderer profile；
- 列數、欄數、標題列數；
- 完整矩形 cell grid；
- 每格的原始段落與條列標記；
- 結構雜湊。

每格分開保存「來源實體狀態」與「網格邏輯值」，不能混成一個狀態：

| `physical_state` | 意義 |
|---|---|
| `present_text` | 官方來源真的有這個格與文字 |
| `present_empty` | 官方來源明示一個空格 |
| `explicit_covered` | 官方來源明示被 rowspan／colspan origin 覆蓋 |
| `source_repeated` | ODT 明示 repeated-cell，而非本系統自行補值 |
| `physically_omitted` | 來源中沒有這個實體格 |
| `unresolved` | 來源格狀態無法機械判定 |

| `logical_value_state` | 意義 |
|---|---|
| `own_source_value` | 值由本格來源文字提供 |
| `covered_from_origin` | 值來自正式 span origin |
| `policy_carried_from_origin` | 值依版本化 table-role 規則延續 |
| `none` | 沒有邏輯值 |
| `unresolved` | 邏輯值無法機械判定 |

`covered_from_origin` 與 `policy_carried_from_origin` 都要指向同一表格內幾何
有效的 source origin；禁止 origin chain 與 cycle。carry 格沒有自己的來源
文字雜湊，只能有另外計算的 `logical_value_hash`，並要保存 policy version
與 receipt。重建 source-exact 原文時，一律忽略 carry 的顯示值。

`has_table` 不另行手填；它由該版本的 table node 數量計算。

## 新公告上線的完整流程

### Default renderer 與 opt-out renderer 的分界

完成正規化與 exact diff 後，預設由通用 renderer 直接上線，這是 **D**。
只有在資料正確但閱讀任務仍要求讀者同時跨越多張表、多個代碼集合與多層
條件時，才開 opt-out：

1. **D**：計算完整文字、表格、健保碼集合、decision model 與 exact diff。
2. **A**：以同一版資料撰寫閱讀路徑與結構層級的變更摘要。
3. **H**：Copper 核准該版採 `agentic_specialized`。
4. **D**：loader 驗證 source version／全文 hash／diff fingerprint，
   封存 profile 並透過 control event 啟用。
5. **D**：API 只在所有 source bindings 相等時回傳 profile；不相等時
   fail closed，前端使用通用 renderer。

Profile 不得包含自行抄寫的門檻表、藥品清單或官方全文。專屬模板應從
canonical `decision_model`、`reimbursement_code_links` 與 normalized tables
即時渲染數值；profile 只保存人類閱讀順序、標題、說明與 source-role
references。正式原始附件文字與完整合成條文仍放在頁尾供逐字核對。

### Step 0 — 發現公告

**類型：D**

1. 定時讀取健保署公告清單、RSS 與已知現行規定入口。
2. 依正式網址、文號與附件 identity 去重。
3. 若同文號出現新 bytes、撤回、更正或競爭附件，標為衝突，不直接覆蓋。

輸出：一筆待取得的 official event。

失敗方式：來源無法讀取時保留 acquisition gap；網站繼續供應上次已封存版。

### Step 1 — 取得原始資料

**類型：D**

1. 下載公告頁與附件。
2. 驗證 MIME、magic bytes、大小與 SHA-256。
3. 保存下載時間、官方 URL、檔名與 attachment edge。
4. 再跑一次時，必須得到相同 bundle 或明確偵測來源改變；同一 URL 若
   bytes 改變，必須形成新 manifestation 事件，不能原地覆蓋。

輸出：不可變 source bundle。

### Step 2 — 解析來源 block

**類型：D**

1. ODT 依 XML document order 走訪。
2. PDF 只在有可驗證文字層或已校對 proofread 層時進入正式解析。
3. 每一段、表格儲存格與頁面註記都保存 locator。
4. 驗證「來源可見文字剛好一次」：不能漏、不能重複。

輸出：ordered source blocks。

不支援的新格式不由模型直接轉成正式條文，而是進 anomaly queue。

### Step 3 — 找出公告影響哪些條文

**正常路徑：D；歧義路徑：A → H**

可機械判斷時：

- 公告附件明列條文號碼；
- 變更前後欄位有明確 heading；
- 每段可以精確綁到 source locator。

輸出：`official_event → clause_effect`。

若同一附件包含拆分、合併、移動或不明舊編碼，agent 只能提出 lineage
candidate，最後由人工裁決是否為同一條。

### Step 4 — 組成單一條文的完整新版本

**類型：D**

1. 若公告提供完整新文，直接使用。
2. 若公告只列修改部分與「以下略」，只能依已核准的 composition rule，
   把公告新文與直接前一版未變 remainder 合成。
3. 每個 block 標明來自 `amendment_exact` 或
   `predecessor_inherited`。
4. 不得把合成版宣稱為「全部來自單一附件」。

5. 設定 `expression_completeness`。只有來源全文是
   `source_complete`，或合成 manifest 完整且經准入為
   `verified_composite`，才可輸出至完整條文 reader。
6. 若公告以「以下略」省略未變部分，省略 marker 必須保留在公告觀察層，
   但不得成為完整 Expression 的文字。被省略的部分必須逐 block 回指前版
   artifact、block ID、locator 與文字雜湊；公告來源對該 remainder 的
   source-span count 必須是 0。
7. 對外發布前，從 read-only API、canonical JSONL 與由 JSONL 新建的
   SQLite 重新執行同一份 composition verification。三者的完整性狀態、
   manifest hash 與 component provenance 任一不一致即停止發布。

輸出：一個帶完整性狀態的 `clause_expression` 與完整 provenance manifest。

本專案的可重跑 gate：

```bash
python -m nhi_rule_history.clause_document_composition \
  --api-json exported-api.json \
  --jsonl-dir data/releases/clause-document-v25-2.6.1 \
  --sqlite-output /tmp/clause-document.sqlite \
  --receipt composition-verification.json
```

這是 deterministic verification，不是 agent 裁決。Agent 可以質疑
composition rule，但不能直接把不通過的資料改標成 `verified_composite`。

### Step 5 — 建立條文文件樹

**類型：D**

1. 先辨識 clause root。
2. 依 admitted marker grammar 辨識 `1.`、`（一）`、`一、` 等。
3. 建立 parent、depth、sibling ordinal、marker、content 與 source span
   mapping；同一 parent 下 sibling ordinal 必須唯一。
4. 表格成為 table node。
5. 無法確定的 nesting 寫成 `unresolved_structure`。

輸出：Akoma Ntoso-inspired node tree。

守恆：所有來源 scalar 由 primary leaf spans 完整、不重疊地涵蓋；所有
primary spans 依序重組後，必須 byte-for-byte 等於單條文原文。

### Step 6 — 正規化表格

**類型：D**

1. 依來源 row／cell coordinate 建立表格。
2. 先分辨 `present_text`、`present_empty`、`explicit_covered`、
   `source_repeated`、`physically_omitted`。
3. 再依 table-role 專屬規則計算 logical value；carry 規則必須保存版本與
   receipt。
4. 生成完整矩形 grid 與 cell origin。
5. 表格內每個段落仍保留原始 block 與編號資訊。

輸出：table、row、cell、cell-block rows。

禁止：前端看到空格後自行「往上補值」。

### Step 7 — 對齊直接前一版的節點

**精確對齊：D；不確定 lineage：A → H**

對齊優先序：

0. 先確認 expression relation 是否為
   `direct_predecessor_verified`；
1. 已封存且 `verified` 的 persistent Work node identity；
2. 同一 table role 下唯一且精確的 row signature；
3. 經版本化規則限制的 bounded candidate alignment；
4. 其餘標為 `alignment_unresolved`。

如果 `1.` 變成 `2.`，不能只因文字相似就靜默認定為同一節點。模型可提出
候選，但 PG 必須保留 identity assertion 與裁決來源。若一列插入表格頂端，
也不能只按 coordinate 讓後面每列都變成「改寫」；coordinate 是來源位置，
不是跨版本身分。

輸出：old node ↔ new node alignment。

### Step 8 — 產生 diff

**類型：D**

比較分成兩條互不混淆的軌道：

1. **版本全文 exact diff**：只要兩側都是完整 Expression，先以已驗證的
   同一條文 Work 身分比較完整舊文與完整新文。這一層不依賴子節點是否
   對齊，因此一定可重播，也不會把一批對齊未解的子段落偽裝成一批刪除
   與新增。
2. **節點 lineage 與結構 diff**：另行保存節點延續、增、刪、移動、
   拆分、合併及表格列／欄／格差異。只有已驗證或唯一機械對齊的節點才
   能產生節點級 hunk；其餘保留 `alignment_unresolved`。

PG 同時保存兩層：

1. **exact diff**：所有來源差異都保留，任何空白、引號、全半型、標點、
   數字、比較符號、單位或代碼差異都不能消失；
2. **display classification**：只在 node-type 專屬、版本化且可重播的規則
   下，把某個差異分類為排版變化。被折疊的差異仍可展開、可計數。

如果 ABC 變 ABCD，exact hunk 的兩側仍是完整版本，但變動 segment 只有
`inserted=D`，顯示分類就是「本版新增」；不能製造「舊版刪除 ABC」。

PG 保存：

- old/new node 與 block ranges；
- old/new text 與 hash；
- change kind；
- inline segments；
- exact `inline_diff_segment`；
- display classification 與 suppressed count；
- algorithm、tokenizer、tie-break、Unicode profile、normalization policy、
  implementation version；
- expression relation 與證據 receipt。

每個 diff 必須雙向重建：

- old-side unchanged + deleted/replaced = exact old text；
- new-side unchanged + inserted/replaced = exact new text；
- scalar range 與 UTF-8 byte range 都可重播。

若 adjacency 未解，不能稱為「與上一版本差異」。若子節點 alignment
未解，不得硬湊成節點級「改寫」或「移動」；PG 保留 old-only／new-only
lineage，reader 顯示未解數量，但版本全文 exact diff 仍可在 Work 層安全
呈現。
前端只顯示 PG 結果，不再計算一次 diff。

Git 的 Myers／patience／histogram 可作為全文或已對齊節點內的序列工具；
它們不能自行證明法律文件子節點的跨版本身分：
[Git diff algorithms](https://git-scm.com/docs/diff-algorithm-option.html)。

### Step 9 — 封存 PG release

**類型：D**

normalization run 與 diff run 分開封存。每一個 run 都綁定 source publication、
parser、規則、migration、input 與 output fingerprint。loading → sealed 前
必須全部通過：

- 一個 expression 正好一個 clause root；
- parent／child 屬於同一 expression，無 cycle；
- child depth = parent depth + 1，sibling ordinal 唯一；
- primary source spans 完整、無重疊，原文可精確重建；
- 表格為完整矩形；
- physical／logical state 與 origin 幾何有效；
- policy carry 未被偽裝成實體來源文字；
- 可被 reader 選取的完整 expression 沒有 unresolved primary structure；
- `direct_predecessor` diff 有 verified relation；
- exact diff 可重建兩側；
- 原文、content、cell 與 receipts 的 hash 可重播；
- expected counts 與實際 rows 相同；
- sealed rows 不可修改。

失敗時整個 transaction 回滾，不會留下半版條文。

### Step 10 — 切換新舊版本

**類型：D**

1. normalization／diff run 各自以 append-only activation event 啟用。
2. 目前生效完整條文維持第一順位；未生效完整新文顯示在公告區。
3. 生效日到達且 resolution gate 通過後，前一版成為 prior effective；沒有
   文字搬移、覆寫或刪除。
4. 生效日到達仍須通過 resolution gate；若有更正、撤回、來源衝突或監測
   缺口，fail closed，不自動稱為現行有效版。

輸出：`current_effective_complete`、`future_announced_complete`、
`prior_effective`、`conflicted`、`unresolved` 的可查詢狀態。

### Step 11 — API 與 GitHub 資料輸出

**類型：D**

API 直接輸出：

- 最新完整條文；
- ordered nodes；
- normalized tables；
- terminology occurrences；
- reimbursement-code links；
- verified direct-predecessor diff，或帶警語的 previous-available diff；
- history metadata。

GitHub JSON／JSONL 與 SQLite 是 PG 的唯讀可攜投影。匯出器要驗證 counts、
hashes、node/block 守恆與 table grid，不能成為第二套可寫正典。

### Step 12 — 網頁渲染

**類型：D**

最新版：

- 顯示完整條文；
- 依 node kind 決定 heading、paragraph、list 或 table；
- 關鍵字、藥品、疾病與條件提示只使用已封存 occurrence；
- 表格 renderer 由 table profile 決定。

歷史：

- 預設收合；
- 桌機預設「左右比較」：左舊、右新，同一 node／cell 對位；
- 手機預設「單欄比較」：刪除與新增上下排列；
- 可切換左右／單欄；
- 未變內容折疊，只留必要上下文，可展開。
- 折疊處明示省略多少 context，並提供「全部展開」；
- 新增／刪除同時使用文字、圖示與樣式，不能只靠顏色；
- toggle 與展開控制可用鍵盤操作，並有 screen-reader label；
- 手機 DOM 順序固定舊 context 在前、新 context 在後，不靠 CSS 視覺重排；
- adjacency／alignment 未解警語在左右與單欄兩種模式都不能消失。

這借用 GitHub 同時提供 split／unified 的方法，而不是照搬程式碼的行號語意：
[GitHub diff view](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request)。

### Step 13 — 對抗式稽核

**類型：A，但不寫 canonical rows**

Agent 每次抽查：

- 是否有 source block 未顯示或重複；
- 編號是否被錯切；
- 子項是否掛錯 parent；
- 表格是否錯 carry、錯 rowspan 或漏註腳；
- diff 是否把純新增說成刪除；
- 左右欄是否錯位；
- 手機是否產生水平捲動；
- hover／tag 是否遮住原文。

Agent 的合法輸出只有：

1. anomaly report；
2. 最小反例；
3. 建議的新 deterministic rule；
4. 建議新增的 regression fixture。

修正方式是更新版本化規則、重跑全部資料、通過守恆與回歸測試。禁止只手改
某一筆 PG row 讓畫面看起來正常。

Agent 也不得直接指定：

- `node_work_id`；
- `relation_status=direct_predecessor_verified`；
- `expression_completeness=verified_composite`；
- formatting-equivalence。

這些狀態只能來自已准入的 deterministic rule，或獨立人工授權後的完整重播。

### Step 14 — 發布

**類型：D verification + H release gate**

發布前確認：

- additive shadow migration 與兩次 commit 的 deactivate／activate drill；
- fresh PG replay；
- API contract test；
- composition manifest、omitted-remainder 與跨投影 provenance gate；
- JSON／SQLite parity；
- SQLite `integrity_check` 與 `foreign_key_check`；
- 桌機與手機 visual audit；
- live authenticated page；
- GPT Pro 架構與 post-verification audit 的 blocking findings 全部結案。

人類只決定是否接受發布；不逐段搬運條文。

## 哪些情況才會進 agentic queue

| 情況 | 系統先做什麼 | Agent 能做什麼 | 誰能裁決 |
|---|---|---|---|
| 新的編號格式 | 保存原文並標 unresolved | 提出 grammar 與 fixture | 規則審查者 |
| 表格欄位省略方式未知 | 不 carry | 判斷可能的表格模式 | 規則審查者 |
| 條文改號／拆分／合併 | 建 lineage candidates | 整理證據與反例 | 人類 |
| PDF 只有掃描影像 | 保存 artifact | 協助 OCR／校對候選 | 校對者 |
| 官方來源互相衝突 | fail closed | 整理衝突來源 | 人類 |
| 畫面不好讀 | 保留原資料 | 找出 renderer 問題 | 前端規則審查者 |

## 更新速度的實際期待

對「已支援格式」的新公告，Step 0–12 都是機械式，理論上可在取得官方附件
後數分鐘內完成；速度主要受官方網站與部署時間影響。

對「未支援格式」，raw acquisition 仍立即完成，但 structured promotion
停在 anomaly queue。這比讓模型臨場自由整理慢一點，卻能避免正式條文被
無聲改寫。

## 完成的定義

不是「網頁看起來有內容」，而是以下狀態同時成立：

- 官方來源已保存且 hash 可驗證；
- PG 有完整單條文版本；
- 原文 blocks、文件 nodes、表格 cells 完整守恆；
- 新舊版有 direct-predecessor 關係；
- diff 可重播；
- exact diff 與 display classification 分層；
- API 與 GitHub／SQLite 投影一致；
- 桌機、手機都能讀；
- agentic findings 已轉成規則、駁回或由人類裁決；
- live 頁面與 PG 指向同一個 sealed fingerprint。
