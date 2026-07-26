# Worklog

## 2026-07-26

- 完成官方頁、舊資料庫、既有下載器與來源持有量盤點。
- GPT Pro 接受 evidence-first rebuild architecture；舊 runtime snapshots
  不得直接升格為法律歷史。
- 決定分離 source-occurrence stage、canonical history 與 reader projection。

## 2026-07-27

- 完成 14 份歷史 ODT 的結構解析與 source-occurrence extraction。
- 獨立驗證 69 項測試、9,303／9,303 artifact/block round trip、封存後 DML
  guard、bounded rollback 與 idempotent replay。
- 最終 disposition 為 `ACCEPT_STAGE_ONLY`；法律日期、身分、重播、diff 與
  reader model 保持 blocked。
- Copper 指示建立獨立 project repo，改為 public，定位為公共資料工程。
- 新 repo 將 workflow、raw data、update、ATC／ICD-11 linkage、資料庫結構與
  SQLite portability 列為核心交付。
- 實測 14 份 ODT 合計 49,709,507 bytes，最大單檔 8,164,050 bytes；純文字
  5,902,629 bytes。決定 normalized data 進 Git、官方 binaries 與 SQLite
  snapshots 進 GitHub Releases。
- 公開移植時移除資料庫 host/user 預設值，要求 `--dsn` 或
  `NHI_RULE_HISTORY_DSN`；因此 repo code 的 loader 版號升至 1.0.4，既有
  sealed run 的 provenance 仍如實保留為 1.0.3。
- 將 Grok pilot 與獨立審查所得的失敗模式整理為
  `docs/agentic-lessons.md`，並確認現行 parser/loader 已有相對應回歸測試。
- 公開 repo 驗證通過：70 項 tests（1 項環境性 skip）、14 個 raw checksum、
  SQLite schema/builder integrity，以及 PostgreSQL logical schema 的
  transaction apply/rollback。
- 發布 `raw-odt-v1`：14 個 ODT 與 manifest。GitHub 將中文檔名正規化，
  因此 manifest 同時保存官方 `filename` 與實際 `release_asset_name`；
  14/14 的下載檔名、byte length 與 SHA-256 parity 通過。
