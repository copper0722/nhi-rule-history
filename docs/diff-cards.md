# 條文 diff 圖卡

以程式碼 diff 的樣式呈現一次法規異動：紅底為刪改前文字、綠底為修訂後文字，
左側數字為段落序（健保對照表）或項次（法律）。圖卡是 presentation lane，
不寫 PostgreSQL，也不取代條文版本鏈；醫療法等衛生法規與健保給付規定共用
同一套格式與存放位置。

## 本體與輸出

每一組異動是 `publications/diff-cards/<domain>-<date>/`（主線目前只收產生器與測試，各組成品尚未收錄）：

| 路徑 | 內容 |
|---|---|
| `index.html` | 本體（查詢版）：全文、逐段 diff、可展開未修訂段落；手機與桌機皆可讀 |
| `cards/*.html` | 1080×1350 圖卡頁；PNG 由這些頁面截圖產生 |
| `source/` | 官方來源快照（文字 JSON＋原始檔 SHA-256），不含二進位附件 |
| `editorial.json` | 人工撰寫的標題與摘要；條文與 diff 一律由來源重算 |

PNG 是衍生物，輸出到 `build/diff-cards/<id>/png/`，不進 Git。

## 健保藥品給付規定

```sh
python -m tools.diff_cards.build snapshot --out PUB/source/clauses.json BUNDLE_DIR...
python -m tools.diff_cards.build render PUB
```

- 來源是每則公告的 ODT「藥品給付規定修訂對照表」，以 owner 的
  `extract_odt_blocks` 取段落；第 0 列表頭必須是「修訂後給付規定｜原給付規定」，
  否則不收。修訂後欄的標題可印成「建議修訂後給付規定」（2.6.1 公告），型別（給付或附表）仍由兩欄標題共同決定。
  舊欄為「無」即新增條文。
- 比較採 NFKC 並忽略空白與段首條列編號（ODT 自動編號不在擷取文字內），
  所以相容字、全半形與編號差異不標示；顯示文字一律為來源原文。
- 段落先對齊、再以相似度配對修改段，逐字 diff 標出改動；每段只出現一次
  （`verify_block`）。
- 圖卡頁：未修訂段落摺疊、長段只保留段首與改動附近文字（`⋯`），依實測高度分頁；
  全文在 `index.html`。
- 對照表以外的附表（多欄表格）不做 diff，於圖卡註明。

## 法律（條/項/款）

法律異動使用人工審定的 row spec（`editorial.json` 的 `documents[].blocks[].rows`，
`{..}` 標出改動），載入時驗證標記去除後與來源逐字相同、每一項款恰好出現一次：

```sh
python -m tools.diff_cards.build render-law PUB
```

## 行政規則（點/項/款），公報來源

行政規則（注意事項、要點等）的修正以行政院公報「網頁文字版」為來源：新版是修正令所附的修正規定
（其中每一點都是修正點），舊版逐點取自最近一次刊登該點的公報。來源身分以公報印出的字號認定，不看檔名。
主管機關公告頁的摘要不是條文來源；舊文另與主管機關法規系統的現行全文核對，核對結果寫進
`PUB/source/README.md`（工具不代做這一步）：

```sh
python -m tools.diff_cards.build snapshot-gazette --out PUB/source/provisions.json --rule 規則名稱 \
    --new NEW.htm --expect-order "字號" [--expect-date "中華民國…年…月…日"] \
    --old "3,5,7=OLD1.htm" [--old "11=OLD2.htm" ...] [--added "14,15"] \
    [--extra-units FILE] [--attachments FILE]
python -m tools.diff_cards.build render-rule PUB [--png BUILD_DIR]
```

- `--expect-order` 必填，須與 NEW 檔印出的字號完全相同，否則中止並列出兩個值；`--expect-date` 選填，規則相同。
- NEW 的每一點必須由某個 `--old` 對應供應舊文，或列入 `--added`（增訂，舊側為空）。`--old` 點名舊檔沒有的點、
  同一點被兩個對應認領、舊檔就是新檔，一律中止。公報這條線不會產生刪除；刪除點與表格類文件由人工備妥
  （`--extra-units`，格式同快照 `units`，附表可用 `"para_unit": "段"`）、附件清單由 `--attachments`
  （`{id, heading, pages, status}`）提供，兩者驗證格式後原樣併入。
- 解析是確定性的：粗體標題之前的令文不當條文；`第N章` 章名行（章次後接空白，或章次單獨放在編號欄）只記為
  各點的 `chapter_printed`，不比對、不進圖卡，引用章名的條文行（如「第三章所定…」）仍是條文；
  點號（`三、`）不屬於首行文字，款的編號併入顯示文字（`（一）…`）並決定縮排；欄位外有文字、重複點號、
  點之前的段落一律中止。
- 比對、對齊、逐字 diff、每段只出現一次（`verify_block`）與圖卡分頁和健保藥品給付規定相同。
  `項` 單位只對第一層編號、款不編號；其他單位（如 `段`）逐行編號。快照的每個單元必須恰好被一個區塊使用。
- 摘要閘：`render-rule` 先檢查每份文件的摘要，不符即中止並列出全部。`**新詞**` 必須是該文件新增行的原文
  （改寫段須落在標示的改動處）、`~~舊詞~~` 必須是刪除行的原文；摘要中的阿拉伯數字須出現在該文件新增或刪除的行。
  比對方式與全工具一致（NFKC、忽略空白），所以來源用相容字（例如 PDF 文字層的「度」U+FA01）時照常通過。
  條文裡的數字、日期與用語一律放進標記，未標記的文字只作連接與說明。
- 多欄表格與表單不逐字比對，只列清單：附件文件的卡片以群組列出編號、查詢頁逐項列出公報 PDF 頁碼，
  卡片上不摺疊、不省略，列不完就接到下一張；快照的附件必須被附件文件恰好列出一次。
- `editorial.json`：`path_root`、`kicker`、`legend_old`、`legend_new`、`number_note`、`source_lines`、`disclaimer`、
  `cover`、`cover_labels`（單元編號對應封面說明）、`cover_foot`、`index`、`documents`（每份含 `slug`、`title`、
  `summary`、`blocks: [{unit, fn}]`，可加 `notes`、`"elide": false`；或以 `attachments` 取代 `blocks`）。

## 網站 diff 資料線（PostgreSQL）

網站把每一則已公告的藥品給付規定修正，顯示成官方修訂對照表（原給付規定對修訂後給付規定）的
git diff 樣式卡片。公告層（`nhi_rule_history_announced`）只存修訂後欄，原欄不在 PostgreSQL，
所以這條線把每份官方 ODT 對照表切成區塊、算好逐列 diff，存進 `nhi_rule_history_diff_card`
（migration v29）。它是呈現資料，不是條文權威：條文以官方 ODT 與公告層為準，瀏覽器不自己算 diff。

### 結構

| 物件 | 內容 |
|---|---|
| `comparison` | 一份官方 ODT 對照表在一個產生器版本下的比較。鍵是 ODT 位元組的 SHA-256（與公告層的 `source_artifact_sha256` 同值）加 `generator_version`；唯讀（write-once） |
| `comparison_block` | 該比較的區塊，依顯示順序：先是 `clause` 區塊，再是 `attachment` 區塊。第一張表的每一列是 `clause`（通則列依其修正的點各一個）；之後的「修訂後給付規定｜原給付規定」表中，以條號標題開頭的列也是 `clause`（2.6.1 公告的 2.6.2、2.6.3 兩列）；其餘列（多半是修訂後附表）是 `attachment` |
| `v_public_comparison_block` | 讀取契約：每份來源檔取最新的比較（`loaded_at` 再 `generator_version`），一個區塊一列 |

規則：

- 三個物件只有擁有者可讀（與 v28 相同，不對 PUBLIC 授權）；view 是 `security_invoker`、不可更新。
- 兩張表禁止 UPDATE、DELETE、TRUNCATE；一個比較必須與它的全部區塊同一交易提交，否則提交失敗。
- 產生器改版就以新的 `generator_version` 再載入一份，舊份保留；view 只回最新一份。
- 回滾：同名 `.rollback.sql`（刪 view、兩張表、兩個函式、schema；RESTRICT）。表內只有可由 corpus 重建
  的衍生資料。讀回：`database/queries/diff-card-readback-v29.sql`，第 1 至 3 節的 `ok` 欄應為 `t`。

### view 欄位

| 欄位 | 型別 | 意義 |
|---|---|---|
| `source_artifact_sha256` | text | ODT 位元組的 SHA-256（小寫 64 位十六進位） |
| `generator_version` | text | 產生器版本，現為 `nhi-rule-history/diff-cards-pg/1.0.0` |
| `comparison_sha256` | text | 該比較全部區塊的雜湊，見下 |
| `extra_table_count` | integer | ODT 內未比較的多欄表格數 |
| `block_order` | integer | 從 0 起的顯示順序 |
| `block_kind` | text | `clause`（以條號標題開頭的一列：第一張表，或之後的給付規定表）或 `attachment`（之後對照表的其他列，多半是修訂後附表） |
| `clause_id` | text | 條號（如 `9.5`），通則的點為專案編碼（如 `0.4`），附表區塊為附表簡稱（如 `附表十八之五`；沒有標題的表為 `附表（表N）`） |
| `clause_codes` | text[] | 這個區塊服務的條號，見「對應規則」 |
| `path` | text | 卡片檔案路徑，如 `藥品給付規定/9.5`；通則的點為 `藥品給付規定/通則/0.4` |
| `badge` | text | `修訂`；原欄為「無」的新條文是 `新增` |
| `hunk_old`、`hunk_new` | text | 段落範圍，如 `第1～13段`；新條文的舊側是 `（無）` |
| `adds`、`dels` | integer | 新增列數、刪除列數 |
| `old_paragraphs`、`new_paragraphs` | jsonb | 來源段落（逐字）的字串陣列；新條文的 `old_paragraphs` 為空陣列 |
| `rows` | jsonb | diff 列陣列，見下 |

列物件：`{"kind": "ctx"|"del"|"add", "old_no": "3"|"", "new_no": "4"|"", "pair": -1|n,
"indent_em": 0.0, "segments": [{"text": "…", "mark": false}, …]}`。

- `ctx` 是兩側相同的段落（顯示修訂後文字，新舊序號都有）；`del` 只有舊序號，文字是舊段落逐字；
  `add` 只有新序號，文字是新段落逐字。比對忽略空白、全半形與段首條列編號，所以這些差異不產生 `del`／`add`。
- 同一個被改過的段落產生一組 `del`、`add`，兩列的 `pair` 相同；其餘為 -1。
- `segments` 串起來就是該段落全文；`mark` 只會出現在 `del`、`add` 列，標出改動的詞。
  以文字片段取代字元位移，所以不同語言的字串長度算法不會造成偏差。
- 重播檢查（replay invariant）：`ctx` 與 `del` 列的舊序號依序為 1 到 `len(old_paragraphs)`，`ctx` 與
  `add` 列的新序號依序為 1 到 `len(new_paragraphs)`；`del` 的文字等於對應舊段落，`add` 與 `ctx` 的
  文字等於對應新段落；`adds`、`dels` 等於列數。載入器寫入前與讀回後各檢查一次，讀回檔案用 SQL 再檢查一次。
- `comparison_sha256` 是區塊清單（依 `block_order`，每個區塊含 `block_order` 到 `rows` 十三個欄位）的
  canonical JSON（鍵排序、緊縮分隔、UTF-8、結尾一個換行）的 SHA-256；`comparison_block.rows_sha256`
  用同一種寫法對 `rows`。它只取決於 ODT 位元組與產生器版本。

### 對應規則（公告 patch 對應卡片）

1. 公告 patch 的 `source_artifact_sha256` 等於 `v_public_comparison_block.source_artifact_sha256`。
2. 區塊的選擇看 `clause_codes`：patch 的 `clause_code` 屬於區塊的 `clause_codes`。
3. `clause` 區塊的 `clause_codes` 依序是：自己的 `clause_id`；那一列任何一個以條號標題開頭的段落所指的條號
   （判斷用公告層自己的標題文法：NFKC、條號後有句點），先取修訂後段落（依修訂後欄的出現順序），再取只出現在
   原段落的（例如本次修正刪掉的子條文，依原欄順序）；每個條號只出現一次。公告層把一列依標題拆成一條條
   patch，所以區塊服務這一列裡的每一個條號，巢狀的與並列的都算。例：官方對照表的 `9.5` 那一列含 `9.5.1.`
   標題，公告層把它拆成 `9.5` 與 `9.5.1` 兩筆 patch，兩筆都對到同一個區塊，`clause_codes` 為 `9.5` 與
   `9.5.1`；此時 `clause_id` 與 patch 的條號不同，卡片註明「官方對照表此列為 9.5（含 9.5.1）」。並列的例子：
   一份公告的 `9.2` 那一列同時含 `9.26.` 與 `9.69.` 標題，區塊的 `clause_codes` 為 `9.2`、`9.26`、`9.69`。
   段落中間出現的條號、清單項（如 `2.18歲`）不是標題，不列入。之後的給付規定表中以條號標題開頭的列
   （第一個修訂後段落；修訂後欄為「無」即本次刪除時看原欄第一段）同樣是 `clause` 區塊：`clause_id` 是該條號，
   `path` 是 `藥品給付規定/<條號>`，原欄為「無」時 `badge` 為 `新增`，建法與第一張表的列相同。刪除列的修訂後側就是官方表上的那一段「無」（`new_paragraphs` 為 `["無"]`，`badge` 為 `修訂`）。例：2.6.1 公告第一張
   表只有 `2.6.1` 一列，後面一張給付規定表有 `2.6.2`、`2.6.3` 兩列，三者都是 `clause` 區塊。
   通則列（2026-10-19，公告 1150057388）：第一張表中兩欄都以「藥品給付規定通則」標題開頭的一列，**每一個被
   修正的點**各是一個 `clause` 區塊：`clause_id` 與 `clause_codes` 都是該點的專案編碼 `0.N`（N 為頂層序數，
   「四、」為 `0.4`），`path` 是 `藥品給付規定/通則/0.N`，`badge` 為 `修訂`，段落、`hunk_old`／`hunk_new`、
   列都只取該點在兩欄中的段落（從「四、」那一行到下一個標記行之前，逐段比對，`verify_block` 逐點檢查）。
   標題段落與「一、~三、(略)」「五、~十二、(略)」這類只說明省略範圍的段落不屬於任何點，不在任何區塊，卡片上
   不顯示。切點用公告層自己的文法（`comparison_grammar.split_general_principle`），所以卡片服務的條號就是
   公告層投影的條號，區塊依列序排在該列的位置，封面排序鍵讓各點排在條號之前。因為 `clause_id` 屬於
   `clause_codes`，網站匯出的既有檢查不必改。兩欄必須切成相同的點，切不出來的列（缺標題、兩欄的點不同、
   序數讀不出、有一行長得像省略標記卻不是標準寫法、標記被看不見的字元遮住）無法說出卡片屬於哪些 patch，整份比較列為 `failed`，不存一張對不到任何 patch 的卡片。
   原欄為「無」的各種寫法（`無`、`(無)`、`（無）`、`無。`）都是新條文。
4. `attachment` 區塊的 `clause_codes` 是同一份比較所有 `clause` 區塊的 `clause_codes` 的聯集（依區塊順序，
   去重，含巢狀與並列條號），附表隨該公告的每一筆 patch 一起顯示。例：`9.5` 那一列的區塊服務 `9.5` 與
   `9.5.1`，同一份公告的附表區塊也服務 `9.5` 與 `9.5.1`。附表區塊自己那一列裡的標題不另外列入。
5. 公告 patch 文字與區塊段落逐位元組不同（公告層把 ODT 自動編號印成 `1.` 加 Tab，並把巢狀條文拆開），
   比對文字時兩邊都用 `tools.diff_cards.core.key`；patch 文字依空白行切開後，等於區塊 `new_paragraphs`
   的連續一段。

### 載入：`load-pg`

```sh
PYTHONPATH=src python -m tools.diff_cards.build load-pg [--dsn DSN] [--corpus-root DIR] [--dry-run]
```

- 連線字串依序取 `--dsn`、環境變數 `NHI_RULE_HISTORY_DSN`、`service=$PGSERVICE`；NHI 公告 bundle 根目錄取
  `--corpus-root` 或環境變數 `NHI_RULE_HISTORY_CORPUS_ROOT`。沒有就以狀態碼 2 結束。不呼叫任何模型。
- 讀 update queue 內每個曾登錄的 bundle（`announced_release.registered_bundles`）。queue 收據有問題的 bundle
  （`problem`）列在回條，不解析。其餘 bundle 先證明 manifest 是登錄時的那份、來源檔位元組與 manifest 一致，
  再選出公告層會解析的那份 ODT 附件（`announced_notice.parse_notice` 的候選迴圈：ODT 附件中含修訂後／原
  對照表者，必須恰好一份，其 SHA-256 就是公告層記下的 `source_artifact_sha256`）。
- 只共用「選哪份附件」，不要求公告層能完整解析：公告層因 raw.md 收據不符等原因解析不了的舊公告，仍可由 ODT
  本身產生卡片。這些卡片存在庫裡，網站只匯出公告層實際提供的來源檔。
- 用 `sources_nhi.read_table` 與 `build_blocks`（無編輯文字、不排除條文）產生區塊，轉成上述列物件，逐區塊做
  重播檢查，算 `comparison_sha256`。`build_blocks` 把之後各表的每一列都當附表區塊；上一節所說「以條號標題
  開頭的給付規定表列」改為 `clause` 區塊是在 `pg_store` 內分類，不動 `build_blocks`，所以卡片 render 線的輸出不變，
  `clause` 區塊排在 `attachment` 區塊之前。每份比較在一個交易內寫入（先取 advisory lock、再確認尚未存在）；
  已存在的 `(source_artifact_sha256, generator_version)` 不動。
- 寫完用新連線經 `v_public_comparison_block` 讀回，從 PostgreSQL 回來的列重算雜湊並比對：與庫內存的 `comparison_sha256`、
  各區塊的 `rows_sha256`、這次剛建的比較，並重做重播檢查。已存在的比較也一併讀回；若同一版本號下產生器已改變，庫內列與剛建的不同，
  這是讀回不符（須升版本號，唯讀的表無法原地改）。若該來源檔最新的比較屬於別的產生器版本，列為 `superseded`，不算不符。
- 回條（stdout 的 JSON）：`counts`（`registered`、`problem`、`parsed`、`inserted`、`present`、`failed`，
  `--dry-run` 另有 `would_insert`）、`problems` 與 `failed`（每筆 `{work_item_id, error}`）、`store_schema`
  （`present` 或 `missing`）、`comparisons`（每份來源檔一筆，依 `source_artifact_sha256` 排序：
  `work_item_id`、`outcome`（`inserted`、`present`、`would_insert` 或 `failed`），以及各區塊的
  `block_order`、`block_kind`、`clause_id`、`clause_codes`）、`readback`（`checked`、`mismatches`、
  `superseded`）。每個登錄項目是
  `problem`、`parsed`、或解析階段就 `failed` 三者之一（寫入階段才失敗的項目同時計入 `parsed` 與 `failed`）。
  無法成為卡片的 bundle 只列在 `failed`，不影響其他 bundle；
  目前會出現的有：附件清單不完整的公告；通則列切不成點的公告（缺標題、兩欄的點不同、序數讀不出、有一行長得像省略標記卻不是標準寫法、標記被看不見的字元遮住）也會列在這裡。
- 結束狀態：0 表示執行完成（失敗只是列出）；1 表示基礎設施錯誤（連不上、正式執行時沒有 v29 schema）或讀回不符；
  2 表示缺連線字串或 bundle 根目錄。
- `--dry-run`：照常建好並驗證每份比較，但只開唯讀連線，不寫入；schema 尚未套用時回條的 `store_schema`
  為 `missing`，所有 `parsed` 的比較都計入 `would_insert`。正式執行遇到 schema 不存在則以狀態碼 1 結束。
