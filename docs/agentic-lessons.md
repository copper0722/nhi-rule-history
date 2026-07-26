# Agentic 重建：Grok pilot 與驗收心得

這個專案把 LLM agent 當作可加速大量整理與實作的工作者，不把模型輸出本身
當作資料正確性的證據。Grok pilot 的價值，在於快速完成 14 份 ODT 的 XML
結構盤點、候選條號抽取、資料清理與 staging loader；真正能接受進資料庫的
版本，則來自可重跑輸出、獨立審查與逐項 gate。

## 適合交給 agent 的 groundwork

- 列舉 ODT 的段落、表格、frame、list 與 XML producer 差異；
- 產生 deterministic parser、manifest、issue ledger 與測試樣本；
- 將重複、疑似條號與解析異常保留成候選，不急著決定法律身分；
- 依明確 schema 實作批次載入、fingerprint 與回滾工具；
- 對大量資料做 checksum、row-count 與 locator round trip。

## Pilot 揭露的典型失敗

第一版看似能完成大量抽取，獨立審查仍找到高風險問題：

- frame／text box 內的巢狀段落可能被併入外層，失去獨立 locator；
- 空白、covered、row/column-spanned cell 可能只被計數，沒有可重建位置；
- `0.2 mL`、`0.5 mg` 等數值可能被 dotted-number regex 誤判為條號；
- CLI 的測試 bypass 若暴露給 production，可能把全文寫到錯誤目錄；
- receipt 若用 denylist 過濾，schema 演進後可能洩漏未列入的原文欄位；
- 只算 `.odt` 可能忽略來源目錄裡多出的 artifact；
- PostgreSQL code 可能在 mock 測試通過，卻使用真實 driver 不支援的介面；
- validate 與 apply 間若未重算 bytes，會有同 row count 但內容已改的 TOCTOU；
- 「sealed」若只是一個狀態欄位，沒有 trigger/privilege，實際上仍可被改寫；
- fingerprint 若沒有綁定 migration/schema，不能證明同一份資料契約；
- rollback 若用廣域 `DROP ... CASCADE`，可能傷及 schema 外依賴。

## 已固化的改進

現行 stage parser/loader 將上述問題轉成程式約束與回歸測試：

- 巢狀 `p/h` 各自成 block，外層文字不吸收內層條號；
- empty／covered cells 形成 `empty_table_cell` block，locator 保存 repeat 與
  row/column span；
- 數值加單位由分類器拒絕，不進 occurrence candidate；
- production CLI 永不開放 unrestricted stage；
- tracked receipts 使用 top-level 與 nested key allowlist；
- manifest、artifact set、bytes、row count、stream fingerprint 在 apply 時重驗；
- psycopg3 路徑以 cursor 執行批次寫入，並有無 `Connection.executemany` 的測試；
- sealed child rows 由 trigger 與權限保持 append-only；
- code/schema fingerprint、composite artifact/block FK、共同 advisory lock 與
  bounded rollback 納入 migration/loader 契約。

## 可重用的指揮原則

1. 給 agent 一個狹窄、可列舉、可 checksum 的工作包。
2. producer 不得自行宣告 promotion；由另一個 agent 或測試者做 adversarial
   audit。
3. 要求「真實檔案反例」而不只 synthetic happy path。
4. 每個 audit finding 都要變成 regression test、schema constraint 或明確
   gap；不能只寫在聊天紀錄。
5. 模型可提出候選 identity、日期、linkage、diff，但 official locator 與
   deterministic replay 才能升格為公開資料。
6. 每次更新保留輸入、程式、schema、輸出四層 fingerprint，避免「同數量」
   被誤認為「同內容」。

這套分工讓 Grok 這類 agent 的速度用在 groundwork，同時把法律歷史的最終
聲明鎖在可驗證 evidence，而不是鎖在模型自信。
