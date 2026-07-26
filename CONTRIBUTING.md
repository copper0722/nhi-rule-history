# Contributing

歡迎回報遺漏的官方來源、解析反例、日期證據、條文身分判斷與 linkage 問題。

## 最有價值的回報

- 健保署官方 listing、detail page 或附件 URL；
- 同一 URL 曾回傳不同 bytes 的證據；
- parser 未保留的段落、表格、frame、註腳或儲存格；
- 條號重用、split、merge、move、restore 或 correction 的官方證據；
- 生效日與公告日不相同的官方 locator；
- NHI／TFDA 藥品代碼與 ATC code 的可追溯 mapping；
- SQLite/PostgreSQL schema 無法表達的真實資料反例。

## Pull request 要求

1. 不直接覆寫既有 source artifact；新增 revision、hash 與 supersession。
2. 不用檔案修改時間、下載時間或資料庫時間推論法律生效日。
3. 新增 parser 行為時附真實結構的最小化 regression fixture。
4. 新增 normalized data 時附來源 URL、locator、checksum 與輸出 fingerprint。
5. 執行：

   ```bash
   make test
   make sqlite-smoke
   ```

6. 不提交帳密、內部 host/path、未授權 terminology、完整 ATC/DDD index 或
   未獲 WHO 書面同意的 NHI-to-ICD-11 crosswalk。

不確定的資料請標成 candidate 或 issue；不要為了讓 pipeline 通過而補猜答案。
