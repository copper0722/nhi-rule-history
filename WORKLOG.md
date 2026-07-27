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
- GPT Pro 以 C+ bounded dual-track 補完方法學：v1 凍結；v2 僅自動化
  acquisition/raw/structural，event/effect、legal dates、identity、replay、
  diff 與 frontend cutover 保持 blocked。
- 實作 v2 source plan 與 MOHW FINT adapter。第一次 live run 完成 366 detail、
  1,353 attachments 與 1,719/1,719 raw fetch，但第二次獨立 enumeration
  發現 344 個 resource IDs 不一致。
- 根因為 FINT RowNo 在兩輪相同 query 間重排。resource identity 改為正式
  發文字號與 canonical PFID URL；corrected A/B 兩輪 1,719-key set 完全一致，
  key-set SHA-256 為
  `a9cef7abddcc7c2301363957bbc054259a0cb7f6dac6d8d52c5c4064b54496a7`。
  舊 sealed run `43ecabc6…` 保留 immutable audit evidence，不作 release input。
- Corrected acquisition run `51189ce2-ce51-461c-bd96-c59a526f6065`
  取得 1,712 unique artifacts／85,642,128 bytes，0 issues；PG sealed
  fingerprint `49d42031a5402ebd11efece60196804a654fc1d218ce41f82d2000de0702ed2e`。
- generic ODT parser 用 magic-first 避免官方錯誤 `text/html` header，並補上
  真實 2023 ODT 的 `text:list-header`。360 ODT resources／358 unique bytes
  全部達成 structural text coverage。
- Structural run `d60dcfb2-2bd1-4c3e-8baf-5ad998b01f54` 產生 31,377 blocks、
  1,228 occurrence candidates、547 nonblocking issues、0 blocking issues；
  PG sealed fingerprint
  `6ff571a5551753dbb94483675b43c6d89bf6e6baadc7a3deaa4d3109414cc2d2`。
- v1 八表 empirical export 通過 JSONL/SQLite typed parity；logical digest
  `eff96dccb7c7f1bcfffeaebe5c499fed6a0b225e49512ff8fcebf26dfaaaecd3`，
  SQLite 1,102,200,832 bytes。v1 release assets 準備完成、未發布。
- v2 raw tar.zst（67,161,191 bytes）與三份 structural JSONL.zst 準備完成；
  decompressed checksum 全數通過，狀態為
  `prepared_partial_evidence_bundle_not_published`。v2 SQLite 尚未完成，
  所以不得稱 portable dataset release-ready。
- GPT Pro 事後審查要求把 corrected/superseded run、resource-key collision、
  source-plan/capture window 與 PG sealed fingerprints 變成 machine-readable
  gate。新增 `release-eligibility.json` 並令 `release-v2` fail closed；corrected
  run 可進 bounded evidence bundle，RowNo 版 run 永久排除。
- 修正後全套驗證為 70 項 legacy tests（1 skip）＋25 項 v2/package/PG/export
  tests，另通過 public-tree、JSON/YAML、manifest、SQLite schema、compileall、
  zstd 與 whitespace gates。
- 同一 GPT Pro 對話複審後最終 disposition 為
  `PRO_FINAL=ACCEPT_COMMIT_PUSH`；完整回應 SHA-256
  `350f4ded0e53b550f4689acc1d3051deffa9ada999f5e70a724bb32d48ddb42c`。
  此 gate 只允許 branch commit/push/draft PR，不授權任何 release asset。
