# FINT 歷史公文研究 crawler

## 目的與界線

衛福部「法規函釋查詢系統」的 `FINTQRY03.aspx` 是查詢入口，
`FINTQRY04.aspx` 是逐筆詳情頁。實測確認：關鍵字四欄全部留空、有效狀態
選「現行＋廢止」時，`FINTQRY03` 會回傳一個可列舉的官方分母；2026-07-28
的 `1900-01-01..2026-07-28` capture 為 17,497 筆。年度日期分割亦可用，
例如 2025 年 608 筆、2026 年 capture-cut 前 221 筆。

因此主取得路徑不是猜藥名，而是：

1. 以西元年度建立互不重疊的空關鍵字 partitions；
2. 保存每一頁搜尋結果，驗證 `RowNo` 恰為連續 `1..N`；
3. 抓取每一個 `FINTQRY04` 詳情頁與完整公文文字；
4. 保存每個附件宣告 edge；只對 deterministic NHI candidate 或指定
   canary 下載附件 bytes；
5. 年度 partitions 的 match 總和必須等於同一 capture interval 的空
   關鍵字總分母，且 crawl 前後的總數與首頁 fingerprint 不變。

這可證明「在指定 capture interval、指定 valid/type 條件下，當時官方
仍可列舉的 FINT records 已逐筆取得」。它仍不能證明已刪除、撤下或從未
被 FINT 建索引的公告一定存在或一定能找到；因此公告 linkage 不能成為
逐條歷史完成的必要外鍵。條文內日期、官方累積版本、old/new 對照表與
exact source span 仍可獨立成為 transition evidence。

## 查詢前線

第一層是無關鍵字的年度全集，seed 只有日期 partition identity：

```json
{"keywords":[],"origin_kind":"fint_unfiltered_year_partition","origin_locator":"20250101__20251231"}
```

第二層才是關鍵字補漏與更新診斷。它不是 completeness 的主分母，而是用來
找出 FINT 搜尋索引、NHI listing、現行條文詞彙與既有公文之間的
discrepancy。關鍵字不是人工維護的一串熱門藥名，而是由 PostgreSQL 中
現行唯一正典的分章條文標題程式化產生：

- Latin 成分名、商品名與高特異縮寫：單獨查詢；
- 中文疾病／藥物類別：與「藥品給付規定」組成 AND query；
- 固定來源宇宙 baseline：
  `藥品給付規定`、`全民健康保險藥品給付規定`、
  `藥品使用原則`、`藥品使用規定`；
- 後續 frontier：只從已判為相關的官方公文標題與條文標題，使用同一版
  deterministic extractor 產生新詞。

每筆 seed 保存 `origin_kind` 與 `origin_locator`。同一查詢可由多個條文
產生；PG 保留所有來源關聯，但只執行一次查詢。

目前生成方式：

```bash
ssh hmj \
  "PGOPTIONS='-c default_transaction_read_only=on' \
   psql -d vault_main -X -qAt -F '\t' -c \
   \"SELECT DISTINCT designation_text,
       replace(replace(raw_text, E'\\n', ' '), E'\\t', ' ')
     FROM tw_drug_history_structural_stage.occurrence_candidate
     WHERE parse_run_id='<sealed-current-chapter-parse-run>'
     ORDER BY designation_text, 2\"" |
  PYTHONPATH=src python3 tools/build_fint_keyword_seeds.py \
    --output sources/fint-keyword-seeds-v1.jsonl
```

產物必須把 query 去重後再報數；seed rows 與 unique queries 是兩個分母。

## deterministic acquisition

```text
date partition or keyword seed
  -> every FINTQRY03 result page + exact result count N
  -> exact ordered RowNo 1..N + result-row fingerprint
  -> FINTQRY04 RowNo 1..N
  -> normalized formal-number grouping key
  -> complete detail-table text snapshot
  -> every attachment declaration bound to that exact detail snapshot
  -> policy-selected attachment bytes
  -> content-addressed raw bytes + SHA-256
  -> query↔document many-to-many edge
```

傳輸規則：

- 單執行緒，預設兩次官方請求至少相隔 0.8 秒；
- TLS 驗證維持開啟；
- 此舊站的憑證鏈會被 Python 3.14 以缺少 Subject Key Identifier 拒絕，
  因此使用系統 `curl` 的驗證 trust stack，不使用 `--insecure`；
- 禁止 redirect，實際 URL 必須等於要求 URL；
- 每一回應限制大小，保存安全 response headers、byte count、SHA-256；
- 相同 URL 已有完整且通過 hash 的 raw artifact 時直接續跑；
- 每個 query 結束前再次取得所有搜尋結果頁；任一結果列 fingerprint 改變
  即 fail closed，不把跨時點混合的 RowNo 序列封存；
- selector miss、結果數超過安全上限、`RowNo` 缺頁、下載失敗或 hash
  不合均 fail closed。

執行：

```bash
PYTHONPATH=src python3 tools/crawl_fint_keywords.py \
  --seeds sources/fint-unfiltered-canary.jsonl \
  --run-dir /path/outside/git/fint-crawl-run \
  --start-date 19540101 \
  --end-date 19541231 \
  --attachment-policy none

PYTHONPATH=src python3 tools/crawl_fint_years.py \
  --batch-dir /path/outside/git/fint-all-years \
  --start-year 1900 \
  --capture-cut 2026-07-28 \
  --attachment-policy none

PYTHONPATH=src python3 tools/verify_fint_yearly_batch.py \
  --batch-dir /path/outside/git/fint-all-years \
  --receipt-dir /path/outside/git/fint-all-years-verification
```

最後一個 verification pass 會重新取得每一年度的所有搜尋結果頁，確認
整批跑完後仍和 detail enumeration 使用的 index fingerprint 完全相同；
避免只因 broad total 未變，就漏掉中間年度的替換或移動。

raw binary／HTML 不進 Git history。Git 追蹤 seed、程式、manifest、PG 匯出
JSONL 與小型 sample；完整 raw bundle 以 content-addressed GitHub Release
asset 發布。

## 公文與附件不是同一個真實性層

`FINTQRY04` 詳情頁內文是公文快照；附件是該頁「宣告」的關聯。網站可能把
不相關或錯名附件掛在公文下。crawler 必須原樣保存，但不得自動把附件升格
為該條文的 old/new evidence。

因此分成：

1. `fint_attachment_declaration`：官方詳情頁宣告的 source edge，必須綁
   定 `match_id + snapshot_id`，即使連結沒有顯示名稱也不能丟棄；
2. `fint_attachment_snapshot`：政策選中後實抓的 raw bytes；
3. `attachment_relevance_assessment`：後續 deterministic／agentic 判定，
   包含檔案真實格式、標題、內文、與公文主旨的一致性；
4. `transition_evidence`：只有通過 relevance 與 exact locator gate 才能
   連到單一條文版本。

例如 CAPD canary 的 95 年公文，FINT 頁面實際掛出
「食品添加物規格標準.DOC」。raw bytes 是真正的 OLE Word 檔，但標題與
公文主題不一致；應保存並標記疑義，不能靜默丟棄或當成給付條文。

## PostgreSQL 正規化

Migration：
`2026-07-28_nhi_rule_history_fint_keyword_crawl_v17.sql`

主要表：

| 表 | 單位 |
|---|---|
| `fint_crawl_run` | 一次完整、可封存的日期／關鍵字 query frontier |
| `fint_query_seed` | query 與條文／方法學來源 |
| `fint_query` | 0–4 個 AND keywords、完整搜尋頁索引與結果分母 |
| `fint_document_number_identity` | 正規化正式文號分組；不宣稱一個文號永遠只有一筆紀錄 |
| `fint_crawl_observation` | 搜尋頁、詳情頁、附件的 raw receipt |
| `fint_document_snapshot` | 詳情頁完整文字與時間化 snapshot |
| `fint_query_document_match` | query RowNo occurrence↔detail snapshot；逐 query 唯一且連續 |
| `fint_attachment_declaration` | 指定 detail snapshot 宣告的附件 edge |
| `fint_attachment_snapshot` | 已下載附件 bytes 與 raw fingerprint |
| `fint_crawl_issue` | 阻擋完成的 acquisition issue |

loader 要求 manifest 的檔名集合完全相等、逐檔重算 hashes、重建所有
query→RowNo→snapshot→attachment 關係，並由 PG 再核對 child counts、
逐 query `1..N`、detail observation URL/kind、attachment fetch-state parity，
才允許 `loading → sealed`。相同 seed frontier 可在不同時間重跑；相同
manifest 重播 idempotent。sealed run 與 child rows 的
INSERT／UPDATE／DELETE／TRUNCATE 均被拒絕。

```bash
NHI_RULE_HISTORY_PG_DSN='<private DSN>' \
  PYTHONPATH=src python3 tools/load_fint_crawl_pg.py \
  --run-dir /path/outside/git/fint-crawl-run
```

JSONL 是 PG 的公開交換投影；沒有 PG 的使用者可以再轉 SQLite。JSONL、
SQLite 與網站都不是第二份可寫主庫。

## 如何連回單一條文

關鍵詞命中只產生 discovery edge，不是條文裁決。連結順序：

1. 正式文號建立分組鍵，完整詳情文字各自建立 record snapshot；
2. 詳情或附件解析出 exact 條號、藥名、日期、old/new 欄位與 source span；
3. 以現行／歷史條文 stable identity 候選比對；
4. 一對一且 before/after 可重播者可 deterministic proposal；
5. 多條、split／merge／move、附件疑義或只剩 OCR 時進 agentic review；
6. accepted transition 必須保留 direct predecessor、完整兩版文字與 evidence
   locator；只命中藥名不等於「這份公文修改這一條」。

## 「全抓」的可稽核完成條件

年度全集同時公布：

- `query_row_parity`：每個 query 的 `fetched_rows = expected_rows`；
- `search_index_stability`：每一個搜尋結果頁在 detail crawl 前後
  fingerprint 不變；
- `year_partition_parity`：所有不重疊年度 match 總和等於空關鍵字 broad
  query 分母；
- `detail_raw_coverage`：每個 RowNo 都有 detail raw SHA-256 與完整文字；
- `attachment_declaration_coverage`：每個官方附件 anchor 都有 declaration；
- `attachment_byte_coverage`：另依 `all`、`nhi_candidate` 或 `none` 明報，
  不和 declaration coverage 混稱。

通過後可稱「指定期間、當時官方可列舉的 FINT surface 完整」。不能寫成
「所有歷史上曾存在的公告已證明完整」。

## 更新

FINT 頁面自稱每月更新。建議每月：

1. 已封閉歷史年度可抽查；重新跑當年度空關鍵字 partition；
2. 重算 broad total 與所有年度分母；新文號或 detail hash 只追加；
3. 重跑關鍵字 discrepancy frontier；
4. 相同正式文號如出現新 detail text hash，新增 snapshot、不覆寫；
5. 新文號進入 clause-link work queue；
6. 公布新增、消失、內容改變、附件改變與 selector／transport issues；
7. 只有完整 sealed run 才能進 public JSONL／SQLite release。
