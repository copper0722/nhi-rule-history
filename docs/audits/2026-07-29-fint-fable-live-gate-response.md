I've completed the review of all seven files against the prior Grok findings and the bounded-run scope. Here is the independent gate result.

**1. VERDICT: ACCEPT_FOR_BOUNDED_LIVE_STAGE**

**2. 阻斷性發現:無。** Grok 各關鍵項均已三層落實:
- 1..N 齊備性:loader `src/nhi_rule_history/pg/fint_crawl.py:191-196`、約束 `...v17.sql:259` UNIQUE(run_id,query_id,row_number)、封存觸發器 `...v17.sql:457-481`。
- 重複執行:run_id 由 manifest_sha256 派生(`fint_crawl.py:308`),同 run 重載回 `already_sealed`(`fint_crawl.py:353-365`),同前沿不同產出為新 run(測試 `tests/test_fint_crawl_pg.py:99-173`),`input_sha256` 已無 UNIQUE。
- RowNo 漂移:完成前全頁重抓並比對指紋(`fint_keyword_crawler.py:868-889`),loader 以原始 bytes 重新解析並重驗每頁與 `search_index_sha256`(`fint_crawl.py:219-249`)。
- 宣告-詳情繫結:複合外鍵 `v17.sql:303-307` 及觸發器繫結檢查 `v17.sql:517-548`。
- 圖/雜湊:manifest 檔集與雜湊全驗(`fint_crawl.py:69-85`),原始 blob 逐一 verify。
- 重套/回滾:有測試(`test_fint_crawl_pg.py:215-223`);回滾在有收據時拒絕(`rollback.sql:8-14`)。
- 封存不可變:update/delete/truncate 觸發器並經負向測試(`test_fint_crawl_pg.py:175-213`)。
- 零筆與成功查詢共存:query_id 由 casefold 關鍵詞+日期+valid+type 構成(`fint_keyword_crawler.py:766-774`),兩探測 id 必不同;expected_rows=0 時 loader 空集合檢查(L195)與觸發器 `expected_rows>0` 護欄(`v17.sql:474-477`)均通過;文件身份僅源於詳情頁發文字號,查詢詞不參與身份,無身份污染。

**3. 非阻斷限制:**
- `rollback.sql:8-14` 空表檢查與 DROP 間有競態:READ COMMITTED 下看不到未提交載入,DROP 等鎖後仍會刪除剛封存的證據;勿與載入並行,建議先 LOCK TABLE 再檢查;回滾檔未設 lock_timeout。
- `fint_crawl.py:132-139` 僅對 observation 重算 stable_id;snapshot/match/declaration/attachment 的 id 及 attachment 與其 observation 的 content_sha256 相等性未重驗,完整性以 manifest 為信任根。
- `fint_crawl.py:501-507` record_text 只驗 blob 雜湊,未由詳情 HTML 重新導出比對(原始 HTML 已留存,可事後重導)。
- `v17.sql:553-609` insert guard 不涵蓋 `fint_document_number_identity`(無 run_id),平時可被插入無來源身份列;載入端碰撞複查(`fint_crawl.py:483-499`)會失敗關閉,但表面可被污染。
- `v17.sql:398` DELETE 時引用 NEW.state 以 "record new is not assigned" 例外擋下——失敗關閉但訊息不明。
- `fint_keyword_crawler.py:105-121` 恆送 `no=""`,字號僅能作 kw 關鍵詞;若來源對 0 筆不渲染 table#dat02,`result_count()`(L230-238)將 fail-closed 而非記 0。
- `v17.sql` 全用 CREATE TABLE IF NOT EXISTS:若 vault_main 有舊草稿殘留會沿用舊表形,套用前須確認 schema 無 fint_* 殘留。
- `v17.sql:184` byte_size>0:空 body 成功回應不可載入(本次不受影響)。

**4. 載入後最小驗證(vault_main):**
```sql
SELECT state,issue_count,query_count,document_count,snapshot_count,
match_count,attachment_declaration_count,attachment_count,output_sha256
FROM nhi_rule_history_edition.fint_crawl_run;
-- 期望:sealed,0,1,2,2,2,2,2,且 output_sha256=本地 manifest 雜湊
SELECT query_id,expected_rows,fetched_rows
FROM nhi_rule_history_edition.fint_query;  -- 2,2
SELECT count(*),min(row_number),max(row_number)
FROM nhi_rule_history_edition.fint_query_document_match GROUP BY query_id;
SELECT d.fetch_state,(a.attachment_snapshot_id IS NOT NULL) AS has_bytes,
a.detected_media_type
FROM nhi_rule_history_edition.fint_attachment_declaration d
LEFT JOIN nhi_rule_history_edition.fint_attachment_snapshot a
USING (run_id,attachment_declaration_id);  -- 均 fetched/true,含 PDF
SELECT normalized_document_number,first_observed_raw_document_number
FROM nhi_rule_history_edition.fint_document_number_identity;  -- 含84-06-20字號
UPDATE nhi_rule_history_edition.fint_crawl_run SET sealed_at=now();
-- 必須被拒:sealed 不可變
```
