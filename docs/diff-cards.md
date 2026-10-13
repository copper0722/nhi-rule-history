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
  否則不收。舊欄為「無」即新增條文。
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
