# Worklog

## 2026-07-28

- Copper 指定健保署「最新版藥品給付規定內容（分章節）」
  `cp-7593-ad2a9-3397-1.html` 為唯一現行正典。方法學因此改為：
  canonical current text 只來自該頁明列的章節／附表 groups，ODT 作主要
  結構解析；最新整份檔只是 non-authoritative quality cross-check。
  先前 639 條 whole/chapter 比對的 606 相同／33 不同與 19 個 leafmost
  分類全部保留為 evidence，但不再阻擋現行條文發布或要求逐條選邊。
  新增 append-only PG authority-policy migration、rollback、公開 JSON
  receipt 與契約測試；頁面 update label 仍不得自動升格為法律生效日。
- 完成 worker contract／recovery v2 的 public implementation 與 private
  runtime hardening。worker 改為只看 source blocks，不再接觸 notice
  metadata；title、date、URL、文號與 stable identity 全由 controller
  獨立綁定。packet 超過結構預算時以可重播 `partition_required` 結束，
  model calls 為 0；general partitioner 仍明列未完成。
- 對兩筆 legacy `failed_terminal` 做 hash-bound recovery bridge：保留原始
  receipt、attempt JSONL、stdout/stderr、64-hex attempt identity、
  primary→fallback lineage、terminal transition／evidence，先收入 append-only
  legacy evidence tables，再允許 generation 2；不偽造 UUID worker-attempt
  rows，也不改寫舊 terminal evidence。
- additive recovery-v2 migration 已套入 live PostgreSQL；套用前後舊 queue
  21 列、transition 88 列、work-item attempts 61 列與 operational worker
  attempts 13 列的 row counts／fingerprints 均不變，canonical history schema
  仍不存在。兩筆 legacy receipts 已各自通過 idempotent admission，共形成
  2 列 legacy failure evidence 與 4 列 attempt evidence。
- 兩筆 generation 2 均由 deterministic preflight 判定
  `partition_required`，worker/model calls、execution jobs、candidate rows、
  source-bundle claims 與 recovery-route attempts 全為 0；沒有自動重試。
  同時修正 transition receipt fingerprint，使其綁定 work item、
  generation 與 transition ID，不再讓不同工作的同型結果碰撞。
- 新增 ODT nested-table document-order regression；修正外層段落在 nested
  table 之後卻被提前輸出的 XML traversal 錯誤。最新驗證為 70 項 legacy
  tests（1 skip）與 335 項 public tests（7 skip），另 private worker
  runtime 9 項與 schedule migration 3 項通過。
- 公開 PR 的 Linux CI 暴露 undeclared runtime dependency：Pillow 未安裝，
  使 image parser 與 cross-format preflight 在 import 階段失敗。
  `pyproject.toml` 現明列 Pillow 與 PostgreSQL loader 使用的
  `psycopg[binary]`，CI 先安裝 package 再測試；全新 virtualenv 已重跑
  70＋335 項 tests、manifest、SQLite 與 public-tree gates 全數通過。
- GPT Pro post-audit v2 以 `ACCEPT`、C/H/M/L=`0/0/1/2` 接受 R2 stage-only
  修復與 bounded activation sequence；未授權 canonical、release 或
  frontend。依 Low findings 再把 legacy attempt 固定標示為
  `sha256_hex_v1`／`immutable_worker_attempt_jsonl`，並將 verifier contract、
  reviewed code identity、output schema 與 canonical admission-payload hash
  持久化。唯一 Medium 是 live Claude model resolution 與 zero custom
  context 尚未由 non-source isolation canary 證明；它阻擋 official-source
  canary 與 repair-hold removal，但不阻擋 commit、additive migration、legacy
  admission 或零呼叫 `partition_required` 分類。
- 執行一次 592-byte synthetic、stdin-only primary Claude isolation
  canary；未讀官方來源、PG 或 corpus 作為 prompt。CLI capability/schema
  checks 通過，但程序 2.245 秒即 code 1、stdout 0 bytes，沒有成功
  inference，故 resolved model identity、event-level zero-tool／MCP／custom
  context 皆未證。另有 5 個 plugin-area 檔案在觀測窗內變動，可能來自
  safe-mode startup 或既存並行程序，不能宣稱 plugin activity 為零。
  收據明列 `routing-observation-incomplete`，repair hold 與
  official-source canary block 均維持。
- 修正第一次 canary 的 Draft 2020-12 transport incompatibility後，primary
  wrapper 改採 bounded FIFO、獨立 process group、前後 managed-settings
  attestation 與 Claude JSON envelope 驗證；精確檢查 resolved
  model／canonical model／provider tuple、token counters、terminal state、
  permission denials 與 server-tool counters。hostile descendants、signal、
  timeout、512 KiB envelope、128 KiB proposal 與 settings drift 都有
  fail-closed regression；獨立 re-review 最終 C/H/M 為 0。
- 第二次 534-byte synthetic、stdin-only canary 在 13.028 秒成功。
  `sonnet` alias 實際解析為 first-party `claude-sonnet-5`；另有明列的
  first-party `claude-haiku-4-5-20251001` auxiliary。web search/fetch 與
  permission denials 都是 0，沒有官方來源、corpus 或 PG prompt/input，
  結束後沒有 isolated orphan 或 runtime dir。故只授權下一個
  cefiderocol stage-only official-source canary；repair hold、canonical
  promotion、release 與 frontend 仍不授權。成功 receipt 另存，不覆寫
  第一次失敗 receipt。
- 依 Copper 指認的官方入口重新核對
  `https://info.nhi.gov.tw/INAE3000/INAE3000S01`。以一列、只讀 current
  query 驗證其 `POST /api/INAE3000/INAE3000S01/SQL0001`，官方回報
  14,066 筆現行品項，回應 schema 仍含品項代碼、ATC、
  `PAY_CODE_LIST`、`pdfList` 與價格有效起迄。新增 hash-bound observation
  receipt，並明確區分每週 INAE freshness 與每月 IODE immutable baseline。
  這也確認既有 ATC linkage 的官方 direct evidence 是
  `品項→ATC` 與 `品項→給付規定 reference`；`條文→ATC` 只能由已解析到
  stable rule 的 supporting product rows 推導並揭露 support count。
- 重審最早五個 history closure canary。四份官方 ODT 對五個條文均明載
  `自…生效`，舊／新 comparison cells 完整，且五個新文字 hash 均等於
  2026 whole/chapter endpoint。故 source-local date role 已定位 5/5；
  但這些 observations 尚未收入 PG event/effect ledger，stable identity、
  direct predecessor、pre/post release anchors、governed ODT verifier
  與 cumulative replay 仍缺，legal closure delta 維持 0，完整性 scoreboard
  維持 0/1,548。

## 2026-07-26

- 完成官方頁、舊資料庫、既有下載器與來源持有量盤點。
- GPT Pro 接受 evidence-first rebuild architecture；舊 runtime snapshots
  不得直接升格為法律歷史。
- 決定分離 source-occurrence stage、canonical history 與 reader projection。

## 2026-07-27

- Copper 補充既有 ATC linkage 原始入口是健保署 INAE3000。重新核對官方
  current lookup、IODE catalog/resource、既有 PostgreSQL 與 Copper Panel
  查詢路徑，確認品項→ATC 與品項→給付章節是官方 direct assertions；
  條文→ATC 則只能是有 supporting product rows 的 derived projection。
- 實抓 IODE `A21030000I-E41001-001`：96,799,113 bytes、224,455 data
  rows、45,124 distinct drug codes、2,244 ATC codes、95,703 rows with
  rule section、95,520 rows with exact rule URL；raw SHA-256
  `5abfec9bd0afb74f13cabca3402c2d6a0329b3436dd206f9dea83288a8b1d4a2`。
- legacy runtime PostgreSQL 稽核為 45,129 `nhi_drugs`、7,498
  INAE item-rule links（508 未解析 article）、2,025 article-ATC mappings
  （1,708 automatic、317 curated）。IODE normalized formulary 比同日官方
  CSV 多 148 列，證明 current upsert 表不能替代 immutable snapshot。
- 新增 content-addressed NHI drug-linkage fetcher與 exact-header／zero-row
  fail-closed tests；PostgreSQL／SQLite logical schema 同步加入
  `linkage_import_run`、`nhi_drug_item_observation`、
  `nhi_drug_rule_reference`，SQLite schema version 升至 3。實跑 fetcher
  重現同一 raw SHA 與全部 aggregate counts。
- 本輪全套驗證：70 legacy tests（1 skip）、313 public tests
  （307 passed、6 skip；含真實 PostgreSQL linkage transaction test）、
  public-tree、compileall、SQLite integrity／foreign-key 與 whitespace
  gates 均通過。
- Corpus registrar manifest v1.2 套用後，再加一層完整 `pg_proc`
  signature hardening；凍結的 v1.2 forward SHA-256 維持
  `68ce1c7177ccaa5dd21c468bf2f6781bdcabdf8205843cb9312edc3dbf9157cd`，
  additive hardening forward 為
  `998e0c6e24ead4caeb41bf6889567860c9f57c9d683ace87b007767b8d44fbc5`。
  獨立 disposable PostgreSQL 審查通過後才套用，live 再核對 body、
  `p_payload`、defaults／SETOF／variadic／modes、SECURITY DEFINER、
  search path、owner 與 exact capability ACL。
- 最新 stage-only update item 已完成官方附件取得與 corpus registration，
  但 primary／fallback 都在來源候選解析 timeout，故以 durable failure
  receipt 結束為 `failed_terminal`，沒有 canonical promotion。Fresh
  read-only check 為 21 work items、8/8 proposals `needs_review`、6,366
  annotations 仍全數 unresolved、canonical history schema 仍不存在。
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
- 核對既有 `tw_drug.rule_chapters` 後，確認 `chapter_id = 0` 是本專案排序
  編碼，官方 source designation 是「通則」。方法學與 reader contract
  改為保存 `source_designation_raw=通則`、
  `navigation_code=chapter:00`、`code_origin=project_assigned`，不得把它
  顯示成官方「第 0 章」。
- 完成 legacy per-clause date-annotation audit：1,548 條 current articles、
  3,691 version rows；78 rows 無 effective date、3,112 rows 無
  source publication、84 組 rule/date duplicate、1,332 條在不同日期保存
  相同全文。980 條 current text 有日期註記，但 980/980 與 legacy version
  date set 不一致。
- Gilteritinib 反例證明日期只能當 completeness checksum：現行文字有
  2023-06-01、2024-06-01，舊 version rows 卻缺 2024 完整快照，另多出兩筆
  2026 載入日的相同全文。
- PostgreSQL 與 portable SQLite logical schema 新增
  `rule_navigation_assignment`、`source_date_annotation`、
  `source_date_annotation_effect`、`rule_history_coverage`。完成狀態只有在
  annotations 全解析、transitions 全驗證、direct edges 齊全、沒有 gap、
  source universe closed 且 cumulative anchor parity 通過時才可寫入。
- 程式化抽取 legacy current text 的 6,366 個 exact slash-triplet candidates，保存
  Unicode offsets、原樣字串、hash 與 source identity；1,548 條 coverage
  rows fresh-connection replay 通過。6,366/6,366 仍為 `unresolved_event`，
  因此只形成 gap inventory。
- Fresh-connection SQL 再核對 6,360 個有效日期 candidate：共有 323 個
  distinct dates，從 1996-01-01 到 2026-07-01；日期 denominator 已明確，
  但同日公告歧義與舊全文 snapshot 仍須另解。
- 連續更新 corpus bundle 升至 manifest 1.1：不再假設只有一份 ODT／PDF，
  而是依公告順序保存每一份 declared attachment，從所有 ODT 產生
  source blocks。cabazitaxel 的 PDF、ODS、PDF、ODT 四附件已在真實 corpus
  與政府文件目錄逐檔驗證。
- 私有 PostgreSQL registrar 升至 v2 並保持 function-only least privilege；
  真實登錄 receipt 將 7 個已驗證檔案分成 5 個 catalogue-registered source
  files 與 2 個 verified-only corpus metadata files。
- PostgreSQL-registered stage-only recurring lane 已通過真實 scheduled poll
  與帶工作的 scheduled proposal fire。cabazitaxel 的 primary contract
  failure，以及 carboplatin／pemetrexed／免疫檢查點抑制劑公告的 primary
  timeout，均只觸發一次 hm4 Codex fallback；兩者皆產生可稽核候選並停在
  `needs_review`，沒有 canonical write。
- scheduled repair 實測另發現 stale claim 未在 repair hold 結束時清除；
  schedule migration 已改為只在非 `claimed`／`in_progress` 狀態清除
  stale lease 欄位並移除 repair-hold marker，重新實火通過。
- 新增 fail-closed event-resolution stage 的 omitted-text gate：官方 effect
  若省略文字或含多條 ambiguity，即使日期相同也不能成為 resolved
  candidate。live migration 重播後，將 annotation run
  `cca6580b-bc23-5b6b-a65b-69ffb1397a09` 的 6,366 個 markers 與 5 個
  2026-08-01 effect 比對並 sealed run
  `8c720061-fce5-54cb-8604-3a1080e790e9`；結果 6,360 no-match、6 invalid、
  0 resolved、0 canonical writes，fresh-connection fingerprint 通過。
- 實作 NHI current whole／chapter deterministic adapters 與
  Cloudflare-compatible ephemeral-cookie transport。正式 CLI A/B 兩輪都列出
  268 resources，resource-key SHA-256
  `ce67b046d91e82bd4248522f4c50fec675c08c43334843b1226592d481b51d05`；
  266 個附件實抓為 267 unique artifacts／57,999,120 bytes，0 issues。
  acquisition run `06fbf976-fa8c-4f7a-b682-c3e94f9bf23e` 與 92/92 ODT
  structural run `baae912e-8d5f-46b0-9efd-77cf4d567428` 都在 live PG sealed；
  後者有 44,504 blocks、1,322 occurrence candidates、0 blocking issues。
- 對 sealed current-anchor occurrence candidates 執行保守 whole／chapter
  header multiset preflight：整份 662、分章 660、matched 655、whole-only
  7、split-only 5。正規化只做 NFKC、空白及條號末端標點；完整條文 body
  尚未比較，因此將 gate 記為
  `occurrence_mismatch_detected_full_clause_parity_open`，沒有把 preflight
  誇稱為逐條全文 parity。
- 進一步以 sealed structural blocks 完整重建現行整份／分章各 639 條；
  驗證 source membership、locator/hash、連續結構順序並保留 table/list
  blocks 後，606 條相同、33 條不同、0 reconstruction blocker。將 gate
  更新為 `full_clause_parity_failed_33_discrepancies_open`；這是官方兩個
  current surfaces 的真實差異。33 個 mismatch 中 19 個是 leafmost，
  其餘為階層傳播；不選邊、不寫 canonical anchor。
- 將 19 個 leafmost mismatches 逐一做 exact character diff：6 個含版本／
  日期實質差異、6 個是可見 list marker 遺失、6 個只有標點、1 個是分章檔
  尾端補充表被現有 subtree boundary 掛到最後一條。特別是 `8.2.16`
  的分章檔已含 115/8/1 future-effective 文字，不能在 2026-07-27 提前
  升為 current。這項分類只縮小裁決範圍，不選 canonical side。
- 新增 1996–2020 FINT historical exact-phrase source plan，並修正空的
  `dat04` 結果表被誤判為一筆 record 的 bug。A/B 兩輪各列出 942 detail、
  1,178 attachments、2,120 resources，key set 完全一致；1996–1998
  為 0 rows。
- 第一個 raw run 發現 13 份 GIF/JPEG/TIFF 掃描附件被官方
  `text/html` header 誤導。新增 image magic-byte detection 後重建
  corrected raw run `c94220d4-3d06-4b05-8047-f833e41eebc1`：
  2,120 artifacts、91,694,925 bytes、0 issues；240/240 ODT 再解析為
  13,995 blocks／676 occurrences。Corrected acquisition/structural runs
  均已 live PG sealed，舊 run 只留 audit evidence。
- 將上述 1999–2020 bounded exact-phrase 集合依正式文號 materialize 為
  942 個 source-local notice bundles；每個 bundle 綁定 detail 與全部 child
  attachments。合計 1,178 attachments、2,120 resources、91,694,925 bytes
  全部 offline verify，第二次執行 byte-identical replay。此層容許
  PDF-only、image-only、OLE、ODS、多附件與零附件公告，不因缺 ODT 而把
  公告誤判為「沒有修正」。batch fingerprint 為
  `0d38ab99acd58e7f6cf87dd1745cc839df2a6051935a1b834db44205ed5127bc`；
  仍不推論生效日、stable identity 或逐條文歷史完整性。
- 從已準備且 checksum 通過的 post-109 raw evidence bundle 重建原 acquisition
  layout，再依同一規格完成 366/366 source-local notice bundles：1,353
  attachments、1,719 resources 全部 offline verify，第二次執行亦為
  byte-identical replay。原始 acquisition 的 1,712 unique artifacts 為
  85,642,128 bytes；source-local bundles 因不同公文可引用相同 bytes，
  materialized byte occurrences 合計 86,038,585。source plan 另以
  `sources/source-plan-post109-exact-phrase.json` 封存原始 hash，避免後來
  current-anchor adapters 改動同名 working plan 而破壞 provenance。
- 將 sealed annotation stage 與 sealed 1999–2020 historical ODT stage 做
  唯讀 marker candidate preflight：6,360 個有效 marker 聚合成 3,080 個
  條文×日期組合；1,897 組（61.59%）在 240 ODT 找到同日期。3,010 組
  正式數字條號中，826 組（27.44%）在同一 artifact 找到日期與 exact
  條號；70 組 `0.x` 通則導航碼明列為不適用官方條號判定。6 個 legacy 與
  3 個 ODT invalid date 均保留 exact locator。14,518-row ledger SHA-256
  `82f1519591c9e8d07be0c7abf8e1760b0886f0721d4d9fb1e5423fbd632dca45`。
  此結果只做候選排序，未寫 PG、未解析 event/effect，逐條文完整性仍否。
- 將上述 preflight 補成 file-based public CLI；replay 必須顯式提供 sealed
  annotation snapshot、兩份 receipts、historical raw 與 structural dirs，
  先原子化寫 exact-locator ledger，再寫與 ledger hash 綁定的 compact
  report。CLI 本身不連 PG，也不具 canonical write path。
- 新增 hash-bound PDF typed extraction lane，historical bounded run 的
  431/431 artifacts 均成功產生 page／flow／block／line／word text 與
  Poppler bbox locators，共 845 頁、58,981 words、0 blocking failures；
  7 個 zero-word pages 逐頁保留 resource ID／source label 並標為
  `needs_ocr_or_visual_review`。同一 lane 也驗證 post-109 的 666/666
  unique PDF artifacts（669 resource bindings、1,666 頁、131,290 words、
  9 zero-word pages）。這是來源擷取，不解析 table semantics 或法律事件。
- 新增 sealed-denominator ODS typed extraction lane：147/147 artifacts、
  179 sheets、950 physical rows、6,521 physical cells 全部解析，0
  unsupported cells，第二次重跑三個 stage files byte-identical。因官方
  ODS 用 repeat 表示約 1.431 兆 logical cells，parser 保存 logical
  row/cell ranges、repeat、span、formula、type/value 與 XML fingerprints，
  不做展開；1,345 physical zero-payload cells 也保留 exact locator。未寫
  PG，未解析日期或法律事件。
- 新增 historical image render/OCR lane：13/13 artifacts（9 JPEG、3 GIF、
  1 TIFF）全數綁定 sealed raw/resource locators，render 44,910,005 pixels；
  Tesseract 5.5.2、`chi_tra+eng` models 與 `(deny network*)` sandbox 全部
  hash-bound。13/13 OCR candidates 非空、共 11,959 characters，兩輪 17
  outputs byte-identical；但 human-verified 仍為 0，13/13 明列 visual review，
  不把 OCR 當官方文字或法律事件。
- 新增 historical OLE typed extraction lane：347/347 CFB artifacts
  （328 Word、19 Excel）全數完成 2,153-stream inventory；325 份 Word 與
  19 份 Excel 取得 typed output，共 28,649 paragraphs、515 tables、
  11,658 Word cells、21 Excel sheets、511 Excel cells。另 3 份 Word 共
  5 頁經 renderer 定位與目視確認為 image-only 內容，明列
  `needs_image_ocr_or_visual_review`。parser／tool／sandbox／stream／row
  receipts 均 hash-bound，未寫 PG、未推論法律事件。
- 2026-07-27T10:50:24Z 直接對 live PostgreSQL 做 read-only state check：
  6,366 annotations 全數 unresolved、6,366 resolution outcomes 中 0
  resolved candidates；update queue 為 acquired 1、ignored 8、selected 5、
  staged-needs-review 7，7 proposals 全數 needs-review，canonical schema
  不存在。結果封存於
  `docs/audits/2026-07-27-live-stage-status-observation.json`。
- 將同一 live check 程式化為
  `database/queries/history-completeness-status.sql`，以 `BEGIN
  TRANSACTION READ ONLY` 與 schema-existence branches 產生 unaligned
  JSON rows；對真實庫重跑得到相同的 6,366 unresolved／0 resolved／7
  needs-review／canonical absent，且 transaction 正常 `COMMIT`。
- 完成 NHI listing detail expansion：兩次獨立取得 858/858 detail HTML，
  雖因瀏覽計數與 Cloudflare challenge 欄位使 858/858 raw hashes 不同，
  兩輪 stable detail／attachment projections 完全相同，各得 2,400 個
  attachment occurrences／URLs 與 5 個零附件頁。每一 sealed input 皆可
  byte-identical 離線重播；另 materialize 2,400-resource 的 fresh fetch
  run，未改寫上游 raw 或 expansion stage。
- 依 cross-pass 完全相同的 2,400 canonical attachment URLs 建立 fresh
  acquisition run；2,400/2,400 network fetch 成功，形成 2,396 unique
  artifacts、476,139,573 bytes、0 issues。offline `verify_raw` 通過後，
  以 UUID run `56416cad-5760-5301-9724-846338b9b8b2` sealed 到
  `tw_drug_history_acq_stage`，fresh-connection counts／row fingerprints
  全部一致；這是 append-only acquisition evidence，不是 canonical history。
- 第三次 canonical promotion 獨立 disposable-PG 複審確認第二輪三個 High
  adversaries 已關閉，但新增 1 個 High：stored MIME 仍可讓真實 PDF bytes
  偽裝成 `application/octet-stream`，規避 ODT/PDF inventory contract。
  Live apply 繼續封鎖，轉交 byte-derived detector receipt 與 Medium temporal
  contract 修補後再重審。
- 釐清第一輪 980 條與 exact-marker stage 983 條的表面矛盾：在同一
  sealed PG rows 中，窄 regex 確為 980；另三條只有 `99/2 /1` 或
  `93/8 /1`，因來源保留第二個斜線前空白而被 expanded parser 正確納入。
  公開逐條 receipt 固定 raw extraction 分母為 983 條／6,366 occurrences。
- 修正 NHI `lp-3258` listing adapter 的 live selector 與分頁 contract：
  實際為 `section.list > table.rwdTable`、`section.pagination` 與
  `lp-3258-1.html?pi=N&ps=20`；六欄中發文、刊登、期限是三個不同日期，
  刊登期限允許官方現有的一筆空值。兩輪未篩關鍵字 enumeration 各取得
  43 pages／858 rows，resources JSONL byte-identical，resource-key set
  SHA-256 `e9c5d3a74f8595b67da35b6f47eb3fb71f2f14de20f65072c5e5f6cccadceb44`。
  這只關閉 listing index；details／attachments 尚未全抓。
- 完成 NHI listing/detail 與 FINT post-109 exact-phrase 的 grouped
  source-surface reconciliation。NHI 858 rows 形成 847 個正規化文號鍵，
  FINT 366 rows 形成 365 鍵；交集 217、NHI-only 630、FINT-only 148、
  union 995。另保留 7 個 collision keys，明確標記 one-to-one join
  不安全。兩次 NHI raw HTML 雖全因 volatile fields 而 hash 不同，parsed
  metadata projection 完全一致；本步只建立 discrepancy inventory，
  不裁決 relevance、法律生效日或 source-universe closure。
- 完成 historical marker 的跨格式 candidate preflight。以 sealed legacy
  annotations 及已驗證 ODT／PDF／OLE／ODS／image extraction stages 兩次
  重播，輸出完全一致。3,080 個條文×日期中，原生 typed text 找到 2,034
  個同日期候選（較 ODT-only +137）；3,010 個正式條號×日期中，同一
  artifact 的 joint candidates 為 909（+83）。OCR 另列且
  authoritative observations 為 0。未寫 PG、未解析 event/effect，
  有效日期的正式 resolution 仍為 0/6,360；6 個非日期劑量另行終結。
- 2026-07-27T12:07:50Z 再從 hmj `vault_main` 執行擴充後的 read-only
  completeness query：immutable raw stage 為 6,366/6,366 annotations
  unresolved；resolution outcomes 是 6,360 no-match、6 invalid、0
  resolved candidates，7/7 proposals needs-review，canonical schema
  仍不存在。新的 append-only observation receipt 與 query hash 已保存；
  本次沒有 PG write。
- 2026-07-27T13:01:41Z 對同一 live PostgreSQL 再執行一次相同 query；
  結果未變：6,360 個有效日期仍全數 `no_match`，6 個非日期劑量為
  `invalid`，0 resolved、7/7 proposals needs-review、canonical schema
  absent。append-only receipt 為
  `docs/audits/2026-07-27-live-stage-status-observation-3.json`；本次同樣只有
  `READ ONLY` transaction。
- 逐筆讀取上述 6 個 invalid candidates 的 exact context、offset、annotation
  ID 與 source hash；全數是 Trelegy Ellipta `92/55/22` 或
  `184/55/22 mcg` 劑量，不是日期。故 6,366 保留為 raw slash-triplet
  extraction denominator，日期／公告解析分母校正為 6,360；六筆以
  `non_date_dosage_strength` 終結，另存 machine-readable adjudication
  receipt。此為唯讀裁決，沒有改寫 append-only PG stage。
- 新增 candidate-only artifact→official-document preflight。它合併 ODT
  same-artifact evidence 與 PDF／OLE／ODS native joint artifacts，再以
  sealed historical `resource_artifact_link` 和 discovered resource
  provenance 回溯正式文號。909/909 joint pairs 均有候選：490 unique、
  419 ambiguous（2–11），共 282 distinct document numbers、0 unmapped；
  exact 909-row ledger 第二次重播 byte-identical。唯一候選仍不推論
  effective date、amendment effect、stable identity 或 predecessor。
- 第五次獨立 disposable-PG promotion 複核在 38/38 作者測試全綠後仍找到
  1 個 High：一個只有 90 bytes、Python `zipfile` 判為 `BadZipFile` 的假
  ZIP，因同時帶 `PK` magic 與 ODT mimetype 字串，被 detector 與 reviewer
  一致誤認為 ODT，最後 promotion 成功且產生 receipt。Role/session
  separation、missing/replaced review、ROC0 與 A→B→C 均通過，但 local
  gate 維持 BLOCKED；修正必須驗證 ZIP central directory 與 ODF 必要
  entries，而非只找 byte substring。
- 第六次獨立 disposable-PG promotion 複核在 39/39 作者測試與外層 ODF
  結構驗證通過後，又用 CRC 已損壞的 deflated `content.xml` 找到 1 個
  High：兩個 SQL classifier 仍接受，promotion 仍產生 canonical receipt。
  因 PostgreSQL 內沒有可獨立驗證全部 ODF payload 的 zlib／XML oracle，
  最小修補改為所有 release-linked ODT/ODS 一律 observation-only；合法
  stored ODT、deflated ODT、Bad-CRC ODT 與 stored ODS 都固定
  `promotion_eligible=false`、exact external-verifier blocker、receipt=0。
  非 archive 正向測試改用最小 `pdf_verified` policy；40/40 專項與 268
  public tests 通過（6 skip）。本輪只改未套用 migration／fixtures，未寫
  live PG；新的獨立重審仍在進行。
- 第七次獨立重審確認上述 Bad-CRC ODT 已無 canonical side effect，但找到
  新 High：只有 `%PDF-` 開頭、欠缺 PDF version、xref、trailer、catalog、
  pages tree 與 EOF 的 51-byte 假檔，仍被兩個 SQL classifier 接受並成功
  promotion。修補後所有 PDF 也固定 `pdf_integrity_verified=false`、
  `promotion_eligible=false`，以
  `blocked_pending_external_pdf_integrity_verifier` 在第一筆 canonical
  write 前阻擋。三個 format policy 現在全無正向 lane；合法 PDF 與 exact
  51-byte adversary 都 receipt=0。42/42 專項與 270 public tests 通過
  （6 skip），live 仍未套用；新的獨立重審正在進行。
- 第八次獨立 disposable-PG review 重播 exact 51-byte 假 PDF、三個
  format policy、Bad-CRC ODT、角色分離、ROC 0 與 multi-event staging；
  42/42 full 與 12/12 bounded checks 通過，三個 policy 的 canonical
  receipt 都是 0。C/H/M/L 為 0/0/1/2；唯一 Medium 是「目前沒有任何
  canonical promotion 正向 lane」，這正是刻意的 fail-closed boundary，
  不能宣稱 promotion operational。Local Critical／High gate 已清，可進
  GPT Pro bounded architecture audit；live apply 仍封鎖。
- 以官方 in-app Browser 將 exact public fact-locked packet 送至既有 GPT
  Pro Agentic Workflow 對話。Prompt source SHA-256
  `5adc2cfee7f598a5bfc6d2010216f3f102e094b52ec5e4a3521b4e3a67ff47a3`，
  UI-normalized text SHA-256
  `f133001f2f2ef55cfe3899c744ce28040577259469736e58ce344662d76b620b`，
  audited-state fingerprint
  `01ca0e0ccd10483744c830e025adb4441f8591ea8c9f2459e16b9c3b036dd29e`。
  Pro 回覆 `PRO_METHOD_AUDIT=ACCEPT`、C/H/M/L 0/0/1/2，同時維持
  legal history incomplete、live apply blocked、publication unauthorized。
  Exact response UTF-8 SHA-256
  `d523b809fcd4d76e36beb6e47c22bfd88e02e00c1719f557f7bf47080ae2cfc9`
  已以 deterministic gzip＋base64 封存，prompt、receipt、summary 與完整
  response provenance 均在 `docs/audits/`。
- Pro 的兩個 Low 已直接形成 public wording regressions：reader contract
  的 API example 明列 `通則`／`chapter:00`／`project_assigned` 且禁止
  `official_chapter_number` 語意；methodology 要求所有公開 coverage
  numerator 同時攜帶 denominator、population definition、exclusions、
  claim limit 與 evidence receipt。這些是 accepted packet 的保守文字補強，
  未開啟 promotion、未改 source counts、未寫 live PG。
- Post-audit Low remediation 後重跑完整 suite：70 項 legacy tests 通過
  （1 skip），271 項 public tests 通過（6 skip）。
- 將 3,080 個條文×日期 pair 全數程式化為一對一、可續跑、hash-bound
  source-review work units。四條 priority lanes 為 490 unique-document、
  419 ambiguous-document、1,125 native-date/no-joint-document、1,046
  marker/no-native-date-match，合計精確為 3,080。Queue 9,547,157 bytes，
  SHA-256
  `947dfd5a8ddbf29633ee9b7b0d945bd17b23f06c90f6a1e60c1af3a9ae606c57`；
  每列明列 event、date role、stable identity、前後 exact text、direct
  adjacency 與 anchor replay 必要回傳，且禁止模型直接 canonical write。
- 修正 continuous updater 兩個真實邊界案例：NHI 文號缺尾端「號」時保存
  raw value 並依版本化規則補成 normalized value；延遲 backlog 的 source
  observation 不再被硬塞進六小時 worker lease。Worker source packet／run
  receipt 現在綁定 exact manifest 與 attempts ledger SHA-256；resource
  observation ≤ manifest seal ≤ earliest worker start，exact 六小時可接受。
  URL predecessor 改由 PG chronological view 依
  `(observed_at,url_observation_id)` 重算，append-time 欄位不再宣稱前後關係。
  隔離 PostgreSQL 已通過 base→patch→idempotent patch、live load/replay、
  delayed t2→t1 chronology、post-worker rejection 與 rollback；完整 suite
  為 70 legacy（1 skip）及 283 public（6 skip）。尚未套用 live migration。
- 2026-07-28 執行第一筆 exact-target official-source canary，work item
  `cf546edf-7979-5ef8-8dec-7c80e538cd59`（cefiderocol/Fetroja）。來源取得
  成功並封存 6 resources／4 attachments；source bundle ID 為
  `4664185ecf5a642776ebe7985f18be516b5910b0d9a71f1a3717c352ae669a44`，
  fingerprint 為
  `fa5a2c9410735374947a50c56afd992ff9fb5641a03eb8a6611ca633f1fc55ae`。
  queue 由 `selected` 進到 `acquired` 後，在 corpus metadata parsing
  fail closed，沒有呼叫 Claude 或 fallback，也沒有寫 proposal／canonical
  history。根因有二：正式文號 cell 是 `健保審字第1150055418號。`；
  `公告事項` 有兩段，第二段才是 cefiderocol 給付規定修訂，但舊 extractor
  只讀第一段。
- 對上述正式 bundle 建立 additive corpus manifest v1.3：v1.2 與
  normalization rule 1.0.0 保持凍結，v1.3 才使用 rule 1.1.0，依固定
  whitespace → 恰一個末尾 U+3002 → 缺 `號` 的順序正規化，同時保存
  exact raw value、reason 與兩段公告。真實 bundle replay 產生
  `gov_健保審字第1150055418號`、8 files、4 attachments，raw hash
  與 manifest 相符。負例涵蓋雙句號、ASCII dot、內嵌／前綴句號與尾註。
  v1.3 parser 失敗時只允許凍結的 v1.2 extractor 定位並驗證既有
  v1.0–v1.2 bundle；沒有既有 target 時仍回傳原始 v1.3 錯誤，不能以
  legacy 語意建立新資料。真實 U+2003 boundary fixture 已驗證舊 bundle
  byte-identical replay，ignored HTML region 內 self-closing 與一般 void
  tag 亦不再破壞 parser depth。獨立 review 再發現 raw 與 manifest hashes
  可協同竄改、manifest symlink 仍會被跟隨；replay 現改由 sealed source
  依 v1.0–v1.3 各自凍結的 renderer 重建 raw 並要求 byte-identical，
  v1.0 ODT／PDF 也綁回 source artifact，target／manifest／payload symlink
  全部拒絕。
  v1.0／v1.1 manifest 與 raw 的 `reference_number` 維持 raw source
  semantics，只有 v1.2／v1.3 才使用 normalized value；合法內部空白的
  v1.1 regression replay 已通過。
  公開完整 suite 為 70 legacy（1 skip）、348 public（7 skip），
  public-tree 與 SQLite smoke 通過；私有 v1.2/v1.3 registrar、forward/
  rollback migration 與 worker runtime 專項測試亦通過。獨立最終
  re-review 為 `SHIP`、C/H/M/L=`0/0/0/0`。live v1.3
  migration 已套用，function hash 為
  `bfaebc312527a2fea2dfe50ceb4698b733fed9d2eb493c6d38302d602a0f9057`；
  live corpus registration 建立
  `gov_健保審字第1150055418號` 的 PG progress 與一筆 active audit。
  proposal suitability gate 觀察到 `10.3`、`10.3.8` 兩個候選而以
  `MULTI_RULE_DOCUMENT` 安全停在 `partition_required`，worker calls、
  worker attempts、candidate 與 canonical writes 全為 0。獨立
  post-canary audit C/H/M/L=`0/0/1/0`，准予解除窄義 parser/runtime
  repair hold；Medium hierarchy-aware partitioner gap、source-universe
  hold 與 legal-history completeness hold 均保留。此次未執行模型，
  不評價 Claude 或 Grok 能力。
- 新增 hierarchy-aware suitability v2，並把 suitability schema 納入
  worker job fingerprint（domain v4）。只有唯一 maximal leaf、所有其他
  designation 都是 dot-boundary 祖先、leaf 位於 top-level comparison
  row，且祖先沒有獨立 comparison row 時才 collapse；平行 leaves 或
  祖先獨立成列仍回到 `partition_required`。原封存 cefiderocol packet
  的唯讀 preflight 現得到 effective designation `10.3.8`、
  decision=`suitable`、reason codes 空；新 fingerprint
  `5cece6ef62529dac09e3aab23747de035daab185512e58e0aac6f9fdb090b2ea`
  與舊 terminal receipt 不同。獨立 review `SHIP`、
  C/H/M/L=`0/0/0/2`，兩個 Low 測試保護缺口已修補；10/10 focused tests
  與完整 350 public（343 passed、7 skip）、70 legacy（1 skip）、
  public-tree、SQLite 均通過。舊 terminal row／receipt 未改寫，尚未建立
  recovery generation 或呼叫 worker，因此模型能力仍未評估；一般
  multi-rule partition、source-universe 與 legal-history completeness
  仍是 open holds。
- 2026-07-28T04:36:17+08:00 以 fresh read-only PG query 核對：21 個
  update work items 為 2 `failed_terminal`、8 `ignored_non_rule`、2
  `partition_required`、9 `staged_needs_review`；9/9 proposals 均為
  `needs_review`。cefiderocol 尚無 `work_generation` row，canonical
  legal-history schema 仍不存在。

## 2026-07-28 — `通則` PG-first reader template

- Copper 明確修正資料邊界：PostgreSQL 是條文、日期角色、版本關係與 diff
  註記的唯一可寫 authority；JSONL 是 GitHub 公開交換媒介，SQLite 是供
  沒有 PostgreSQL 的使用者使用的可攜投影，前端 JSON 也是可拋棄 projection。
  已同步更新 repo Law、資料庫文件與 reader contract，禁止 JSONL／SQLite／
  frontend-only upstream edits。
- 新增 `nhi_rule_history_edition` 正規化 schema、rollback、SQLite 對稱
  schema 與 deterministic importer/exporter。資料來自 sealed 的 14 份年度
  ODT stage，加上 current chapter ODT；current whole ODT 作獨立 cross-check。
  `通則` 由 exact heading 起算，歷史 whole file 在 `第1章`／`第1節` 前
  截止，current chapter file 可至 EOF；raw block、source order 與 locator
  均保留，soft-wrap normalization 不覆蓋 raw text。
- 第一個 live import 發現 edge identity contract 錯誤：只用 old/new content
  hashes 產生 ID，三個相同文字的 edition transitions 發生碰撞，14 條預期
  edges 只留下 11 列。只 rollback 本輪新 schema，未動任何 source stage；
  edge identity 改為同時包含 old/new version IDs，diff version 升為
  `chapter-00-reader-diff/v1.1`，並在 sealing 前新增 exact identity-set 與
  count parity gate。
- clean rebuild 封存為 import
  `b1a3aed6-7dff-563a-a1eb-4c5454a960b0`：
  source-set SHA-256
  `cfe3bfad6f146207d74ca3163f071b9238ed2900c1dd924bb7163a5f06548b57`，
  output SHA-256
  `6a7cb637048c4422dbc9df8462df724cb76c14ad0c479fafff75b724f1303081`。
  實庫為 1 rule、16 source documents、15 versions、16 version-source
  links、523 date facts、743 blocks、14 edges、26 hunks、1 coverage row。
  schema migration 與 importer 均 idempotent replay；第二次 import 回傳
  same sealed run。
- 宣告版本序列已達 15/15 versions 與 14/14 edges；五個版本 transition
  沒有實質文字變化，仍保留並在 UI 顯示「未觀察到實質文字變更」。
  `official_source_universe_closed=false`、`legal_history_complete=false`；
  所有 edge 都是 `adjacent_official_edition`、
  `legal_predecessor_status=not_claimed`、`crosses_known_gap=true`。這不改變
  全庫法律歷史 scoreboard 0/1,548。
- PG 回讀已輸出 10-table JSONL manifest；15/14/26 等各表 counts 與
  per-file SHA-256 固定。JSONL→SQLite replay 通過 exact count parity、
  `foreign_key_check` 與 `integrity_check`。reader JSON 為 87,911 bytes，
  SHA-256
  `1a20778b373e9896688f6f996d1ddcccb1fa9d96496c85c5bc387d4375a62032`。
- reader 已改為 PG projection：最新版全文置頂、歷史只顯示相鄰累積版本
  diff、桌面兩欄、手機堆疊、紅色「前一版移除」、綠色「本版新增」、黃色
  搜尋命中、changed-only filter、zero-change editions、官方來源連結、
  reduced-motion 與 print rules。頁面只顯示官方 `通則`；`chapter:00`
  保留為 project-assigned navigation metadata。
- 本輪未呼叫 Claude、Grok、Gemini 或其他條文整理模型；既有 dispatch
  pause 與 legal canonical promotion hold 均未變更。
- 完整 repo regression 為 70 legacy tests（1 skip）與 415 public tests
  （408 passed、7 skip）；`通則` 專項 13/13 包含於 public suite。

## 2026-07-28 — canonical unit correction: one clause

- Copper rejected the whole-`通則` version unit and confirmed that one
  top-level clause must own one independent history page and version chain.
  The earlier `nhi_rule_history_edition` import is retained unchanged as source
  provenance, but is explicitly reclassified as a source-edition container.
- Added additive PostgreSQL schema `nhi_rule_history_clause` and exact rollback.
  Every source edition is deterministically segmented at top-level Chinese
  ordinals into project codes `0.1–0.12`. Each annual occurrence is retained in
  `clause_version_observation`; consecutive comparison-equivalent observations
  collapse to one `clause_version`.
- Live sealed clause import
  `3873fcbc-a2e1-5ac4-9b2c-64ab3f1da9b9` has source-set SHA-256
  `537a2aaf4e47987d053da35e10c2cfd58e4b36c760c9dc1ba41ca878b6156034`
  and output SHA-256
  `4eb242934844c106d4bc40ae21ab870990530b42b0f609c50b2217d7c55265e8`.
  Counts: 12 clauses, 152 source observations, 29 text states, 318 blocks,
  261 in-text date facts, 17 same-clause edges, 26 hunks and 12 coverage rows.
- Clause `0.2` demonstrates observation/version separation: 15 annual
  observations, one text state and no edge. Clause `0.4` has 15 observations,
  ten text states and nine edges. Every edge stays within one clause and uses
  `legal_predecessor_status=not_claimed`.
- Generated a public clause JSONL manifest, portable SQLite schema and
  `index.json` plus 12 per-clause reader projections. JSONL→SQLite passed exact
  count parity, foreign-key and integrity checks; replay returned the same
  sealed import and byte-identical SQLite output.
- Reworked the reader to `?rule=0.x`: the selected clause's newest full text is
  displayed once; history shows only that clause's stored hunks against its
  next distinct text state. Search spans the 12-clause index and navigates to a
  single-clause page. No browser-side version collapse or diff calculation is
  allowed.
- No Claude, Grok, Gemini or other rule-cleaning model was called. Legal-event
  completeness remains false and the existing dispatch pause remains in force.
- Final regression passed: 70 legacy tests with one environment skip and 423
  public tests with seven skips. Playwright verified `0.4` desktop and mobile,
  global search routing `生物標記` to `0.12`, `0.2` zero-transition behavior,
  external-link safety, and zero horizontal overflow.

## 2026-07-28 — semantic diff, tag rendering and public `0.4` demo

- Added a sealed semantic-diff presentation layer while preserving the exact
  source hunks. Unicode whitespace, single-quote variants and NFKC-equivalent
  width variants are ignored only for change classification. Pure additions
  render only `下一版新增`; no synthetic deleted old side is emitted.
- Added PG-canonical reader enrichment for clause `0.4`: 65 semantic tags,
  50 ATC code relations, 21 code-only ICD-11 relations, 14 longest-first
  condition markers and one agent history summary bound to the diff hash.
  Latest run `76eb6518-7900-5ccd-9994-5b6e85bba1a1` is sealed under
  `chapter-00-reader-enrichment/v3`.
- ICD-11 alphanumeric codes are visible in the reader and tag page. Confirmed
  and candidate mappings are visibly different. WHO titles, URIs, definitions
  and the reference snapshot stay in private PostgreSQL and are not exported.
  A reusable crosswalk release remains a separate licensing gate.
- Date labels are generated from newly appearing in-text date facts. The 99
  edition of `0.4` displays `99/11`, retains the source-edition provenance and
  states that the label is not a verified legal effective date.
- Improved article rendering: top-level rule heading, subsection headings,
  smaller parenthesized dates, semantic drug/disease links and role-colored
  constraint phrases. Replaced the overly broad single-character `需` marker
  with `需要` and `需敘明理由` to avoid highlighting one character inside
  ordinary words.
- Rebuilt JSONL, SQLite and 12 reader projections from PostgreSQL. SQLite
  SHA-256 `35eecae8c91564702745e89d6c648c469a099a1e0b4728cb9780e854f3bf905e`
  passed foreign-key and integrity checks; `0.4.json` SHA-256 is
  `694acad1796844ccc8d8557bbae26e6986b1524fe8235a708b36480da77cbd5e`.
- In-app Browser verified the desktop and 390×844 mobile layouts, direct code
  display, candidate labels, `99/11`, agent summary, pure additions and zero
  horizontal overflow. The full public suite passed 429 tests with seven
  environment skips.
- Added a GitHub Pages Actions workflow publishing only `prototype/reader`.
  The page includes copy-link and GitHub issue feedback actions. No Claude,
  Grok, Gemini or other rule-cleaning model was called.
- PR #1 merged as `864a4516c7543e771fd0c9db77eb85e2b4cd14c1`.
  The first Pages run correctly failed because Pages had never been enabled;
  the repository was then explicitly configured with `build_type=workflow`.
  Rerun `30327622935` deployed successfully. The production URL returned HTTP
  200, served the expected PG projection and passed a final in-app Browser
  check for code display, `99/11`, summary visibility, pure-addition semantics
  and zero horizontal overflow.
- Reworked the long-reader navigation after Copper's UI correction. A second
  frozen dock now keeps clause code/title/navigation in the left column and
  the global clause search in the right column. Copper then clarified that a
  phone should not inherit the tall frozen desktop dock. Following the shared
  webpage mobile-navigation contract, widths at or below 700 px now use a
  bottom floating island: `目錄` opens a slide-in drawer with
  IntersectionObserver scrollspy, and `搜尋` opens the same clause search as a
  bottom sheet. Backdrop, close buttons and Escape all dismiss the active
  panel; reduced-motion users get no drawer transition.
- The first browser pass caught a stale scrollspy state after an anchor jump.
  The final implementation keeps IntersectionObserver and adds a
  requestAnimationFrame-throttled reading-line calculation for scroll,
  resize, hash changes and dynamically rendered long sections. In-app Browser
  verification at 390×844 passed at scroll position 8,000: zero horizontal
  overflow, `history` selected in the drawer, working anchor dismissal, and
  one `0.4` result for `insulin`. Desktop 1280×900 retained the 77 px sticky
  dock below the site header, hid the mobile island and had zero overflow.
  The complete public suite passed 431 tests with seven skips.
- Copper's screenshot exposed two non-intentional inline-tag defects:
  `apomorphine` was split into plain `apo` plus a false `morphine → N02AA01`
  tag, and the flex tag could break between the term and code. Reader
  enrichment v4 was sealed in PostgreSQL as run
  `c3280b44-067f-5624-b661-bc1ec9959335`, adding the exact
  `apomorphine → N04BC07` relation (66 semantic tags, 51 ATC relations).
  The renderer now rejects Latin/Greek subword matches, emits no template
  whitespace around inline links, and keeps each name+code tag atomic on one
  baseline. The regenerated JSONL, reader projection and SQLite snapshot
  passed 22 focused tests, SQLite integrity/foreign-key checks, and a 390×844
  in-app Browser check with zero horizontal overflow.
- A second identical PostgreSQL rebuild returned the same sealed v4 run and
  reproduced byte-identical manifest and `0.4.json` hashes. The SQLite replay
  also reproduced SHA-256
  `e2b97e0744923bfcb5546ea0e907b1ceda9b6030270126637b2024b45292e369`
  with integrity and foreign-key checks passed. A separate 320×800 browser
  pass found zero horizontal overflow; the widest atomic tag remained within
  the article column.
- Independent read-only audit found no Critical, High or Medium issue. It
  confirmed the latest PG run and exported hashes/counts, executed the boundary
  cases directly, and scanned all 315 rendered text blocks: seven correct
  apomorphine matches, one genuine morphine match and zero false morphine
  matches. The audit's regression-test gap was closed with an executable Node
  boundary test and selector-specific CSS assertions. Final public regression:
  432 tests, 425 passed and seven environment skips.
- PR #4 merged as `4f491ec5c2179e158601f6d6f06a82da4bd47b65`;
  Pages workflow `30329354387` deployed successfully. The production JSON
  SHA-256 matched the sealed projection, and a final 390×844 in-app Browser
  check confirmed the atomic `apomorphine / ATC N04BC07` tag, zero false
  morphine tags, active history scrollspy and zero horizontal overflow.
- Copper clarified that the remaining defect was not keyword color or ATC
  value adjudication: the reader should never expose ATC/ICD code badges beside
  drug and disease names before a click. The clause surface now renders only
  the colored linked term; the local tag detail page remains the sole surface
  that displays terminology codes and mapping status. This supersedes the
  earlier name+code inline presentation decision while preserving the PG
  relations and token-boundary correction.
- Copper further refined the condition lexicon: `需要` is intentionally
  excluded because it usually expresses subjective judgment rather than a
  stable reimbursement constraint; `且` and `或` are both stored with the
  shared `logical` semantic role and render with the same visual treatment.
  Because almost every duration in this clause is introduced by `至多`, that
  word is also excluded as low-information. Quantity-plus-time expressions
  such as `二週`, `六天` and `一個月` are instead extracted across all stored
  clause versions into the shared PG `condition_marker` schema with semantic
  role `duration`.
- `應` is likewise excluded as a ubiquitous low-information token. Recurring
  duration syntax is covered explicitly: `每三個月應追蹤一次` highlights only
  `三個月`, while `應每週發藥` highlights only `每週`.
- Applied the additive PG semantic-role migration and sealed reader enrichment
  v5 as run `d283c7d7-4ec3-5132-b066-e9e47dce7c23`: 66 semantic tags, 51 ATC
  relations, 21 public ICD-11 code relations, 21 condition markers and one
  diff-bound summary. The output SHA-256 is
  `389e84f25a2583bb6b58d14bbc1affe0b13060ba12afd9ab6b484bd399b69767`.
  PG then regenerated the public JSONL and all 12 reader pages. The portable
  SQLite projection passed integrity and foreign-key checks with SHA-256
  `0e0f10f70ed4da7949e82a3b6d1d1b7e7335d3beb45f7e09f87811d33cbf3de9`.
- In-app Browser at 390×844 verified the exact scopes: no mark around `需要`,
  `應` or `至多`; only `三個月` is marked in `每三個月應追蹤一次`, and only
  `每週` in `應每週發藥`. Drug/disease links expose no inline ATC/ICD code,
  there is no horizontal overflow, and opening `Mircera` shows ATC `B03XA03`
  on the local tag page.
- Deterministic replay returned the same sealed v5 run and byte-identical JSONL
  and reader projections. The complete public test suite passed: 432 tests,
  seven intentionally skipped.
- Copper removed AI-coded framing from the history summary. The heading is now
  exactly `歷史變更總覽（本節由生成式AI輸出）`; the redundant eyebrow and
  `摘要不取代官方條文` badge are gone, and the opening sentence states the
  observed change pattern directly without `先看懂` or `不是…而是…`.
- Copper identified two remaining value-highlighting gaps. The renderer now
  suppresses the one-character `限` marker inside `上限`. PG enrichment adds
  a `quantity` role for `15支` and `20支`, sharing the value-emphasis style
  with duration markers; the summary wording uses the canonical unspaced count
  forms so `三天`, `六天`, `15支` and `20支` all pass through the same rich
  renderer.
- The summary-only v6 run was sealed as an intermediate receipt and superseded
  before release by reader enrichment v7 run
  `4f2d2eca-d51a-514a-aa7e-34380f72d148`. V7 stores 23 condition markers; its
  output SHA-256 is
  `9f865a24e3e8bb343436bd3d8b71d05b0600e216808325363b1ac6dfcc4b272e`.
  The regenerated portable SQLite projection passed integrity and foreign-key
  checks with SHA-256
  `339159d8a817051a8d0233c72e12f5b4e75412f418d40355e241480e1804d7fa`.
- Copper set `coding-able` as the semantic-link admission threshold. A complete
  scan of the latest 0.4 text and every stored transition hunk added 16
  ATC-addressable terms, including peritoneal dialytics, antibiotics, the
  heparin group, coagulation factors VIII/IX/XIII, bypassing agents, parenteral
  nutrition, interferon classes, antineoplastic agents, exenatide, liraglutide,
  Britaject Pen and the historical `filgrastin` spelling alias. CAPD remains
  plain because no verified intervention terminology is wired into this
  prototype. Broad `癌症` lost its former pending link because no single
  defensible ICD-11 mapping was admitted.
- Reader enrichment v8 sealed as
  `8ce108b2-d745-59a0-a36b-dae86d7f4de1`: 81 semantic tags, 70 ATC relations,
  20 ICD lookup tags, 21 public ICD code relations and 23 condition markers.
  Output SHA-256 is
  `ebeb6ffbf53a26b0af51c9a0db85fdbad0c3edda62caf8a666478ea788d1731d`.
  The regenerated SQLite projection passed integrity and foreign-key checks
  with SHA-256
  `bcd49aae2cb68ee943e94e0f2fb097c76b66f6017829570af68c861af60538a7`.
- The history diff renderer now recognizes semantic terms against the complete
  old/new side before nesting inline additions or deletions. In-app Browser
  proved that `Britaject Pen` and historical `filgrastin` remain one ATC link
  even when only part of the term changed. A 390×844 scan found zero tag-box
  overlaps and zero horizontal overflow; CAPD and `癌症` have zero links, while
  the newly admitted terms are linked. Opening `透析液` displayed ATC `B05D`.
  Deterministic PG replay was byte-identical, and the complete test suite
  passed 432 tests with seven intentional skips.

## 2026-07-28 — hanging-indent overlap and limit-value emphasis

- Browser measurement identified the apparent `治療` / `糖尿病` collision as
  inherited hanging indentation, not an overlapping anchor box. A list
  paragraph's `text-indent: -1.9em` propagated into each inline-block semantic
  link and shifted its glyph content about 26 pixels left. Semantic links now
  reset `text-indent: 0`; the ordinary inter-term spacing remains unchanged.
- Condition extraction now treats numeric value-plus-unit tokens inside a
  `不得超過` or `不超過` expression as `quantity`. The source spelling and
  punctuation are preserved, so `20,000U` and the alternative `100mcg` are
  highlighted while `不超過` itself is not. `方得`, `限`, and `為限` are also
  omitted as low-information terms; the preceding logical structure and the
  actual duration/quantity values carry the emphasis. Matching is
  case-insensitive for Latin units, but rendering preserves the exact source
  text.
- Reader enrichment v9 through v11 were intermediate sealed receipts and were
  superseded before publication. Final v12 run
  `32700d24-3e92-59fd-b5b8-3adb04a9ef85` stores 21 condition markers; its
  output SHA-256 is
  `8c82dd905f72875746d0bb7029641e497a1eef3bed73356c8b8a2f2dd3133bff`.
  The reader `0.4.json` SHA-256 is
  `873855c26ad46587b644ae53d4976d2fc4ed4831247ac1c9d0352e14ae1e3bbb`.
  The portable SQLite projection passed integrity and foreign-key checks with
  SHA-256
  `64645c8534c925554762e63944ebf2cc6d8d6b83656c2e448d1c413f8089bd1d`.
  A deterministic replay returned the same sealed run and byte-identical
  reader and SQLite hashes. Twenty-three focused reader/export tests passed.
- 2026-07-28：依 Copper 指正，將 `coding-able` 從 ATC／ICD-11 擴為可驗證的
  官方編碼系統。Fresh PG query 核對健保署「醫療服務給付項目及支付標準」
  current rows；`CAPD` 建立 4 筆 `NHI_TREATMENT` 關聯，以 `58011C`
  （腹膜透析追蹤處置費－CAPD）為核心，`58009B`、`58010B`、`58012B`
  為直接相關支付碼。來源 dataset identifier 為
  `A21030000I-D20020`，不是 ATC 或 ICD 的替代編碼。
- 同輪將條件呈現由 value-only 升格為 PG-normalized compound expression。
  v13 程式化找出 5 個 unique expressions：
  `不超過20,000U`、`不得超過15支`、`不得超過20支`、
  `一個月為限`、`每三個月應追蹤一次`。每列保存 comparator、numeric
  value、unit、action、action count、parser pattern 與 critical severity；
  reader 先匹配完整 expression，再匹配 atomic markers，避免一句條件被拆成
  多色碎片。
- Live migration
  `2026-07-28_nhi_rule_history_clause_coded_treatment_compound_condition_v13`
  已套用；sealed enrichment run
  `3796f2bf-4848-5e03-8b4e-1ddebf808606`，output SHA-256
  `f9c8efc2ccc3e53a0d41366f80021e42d8dd01c6c96600b18f65347cff917da9`。
  Counts：82 semantic tags、70 ATC、20 ICD lookup、21 public ICD-code rows、
  4 NHI treatment-code rows、21 atomic markers、5 compound expressions、
  1 summary。Reader 0.4 SHA-256
  `1c5e4a5574970ae6dc3e33b6a53d25a3ca27912e61231fcaa1cba5fe0cf991ec`；
  SQLite SHA-256
  `4c483a1d500eb692288bd91b92bf9c6e2f8c7fdd89e8edbddc1c779b1ae864ca`
  並通過 foreign-key／integrity。獨立稽核以 system Python 3.14／SQLite
  3.53.4 重建得到另一個整檔 SHA；逐 byte 比對證實只差 SQLite header
  writer-version。Builder receipt 現明列 Python 3.11.15／SQLite 3.50.4
  與 `byte_reproducibility_scope=same_sqlite_library_version`；兩個 runtime
  的 logical SHA 均為
  `3dfb85c27dd1087c4c19fb2bfacdbc5d616698b2bf86e9d9d53a46374a36f34e`，
  counts、foreign keys 與 integrity 相同。
- Copper 提出的「使用者可設定要顯示的條件與顏色」列入
  `G-UI-SETTINGS-01`。未來設定是 presentation-only；PG 保存的解析、
  severity、條文與 diff 不受使用者偏好影響。
- 獨立稽核另指出 EPO 全句還含 `或100mcg` 這個沿用前述比較子的替代值。
  v13 的完成單位明確縮限為 5 個可直接觀察的連續條件片段，不宣稱已解析
  整個 OR formula；parent expression、ordered alternatives、connector 與
  source spans 列入 `G-COND-ALT-01`。
- 獨立稽核發現 sealed reader enrichment 原本只有應用程式慣例，live PG
  的 parent 與 child tables 仍可被直接修改。新增 v14 additive migration：
  loading run 才能新增 child row，`loading → sealed` 時由 PG 核對七個
  宣告 count，sealed parent 不得再 UPDATE／DELETE，九類 child 不得再
  INSERT／UPDATE／DELETE，parent 與 child TRUNCATE 亦 fail closed。
- v14 專項 disposable PostgreSQL 測試完成 forward、rollback、fresh
  loading→九類 child→sealed、count mismatch rejection，以及 sealed
  parent／child adversarial operations；2/2 tests passed。Live migration
  已套用並建立 21 個 user triggers。可重跑的
  `database/verify-reader-enrichment-immutability.sql` 已證明所有 mutation
  拒絕；`database/verify-reader-enrichment-fresh-seal.sql` 已在真實 schema
  完成 fresh seal 後 rollback，probe rows 為 0。
- Migration 後重播仍回傳既有 sealed v13 run
  `3796f2bf-4848-5e03-8b4e-1ddebf808606`。JSONL、reader `0.4`、SQLite
  的 SHA／logical SHA 及全部 counts 不變；foreign-key 與 integrity
  再次通過。
- 使用者自訂條件顯示與色彩仍列為未來前端設定，不在本輪硬編 UI。規格新增
  `restore_default_palette`；預設是所有已辨識複合條件可見且以 critical
  red 呈現，個人設定只改 presentation。
- Copper 釐清歷史表的閱讀主詞：第一列必須是最新版，每一列都以該列
  「本版」相較「前版」來描述。Reader 顯式依 newer `state_order` 反向排序，
  第一列標示「最新版」；diff labels 改為「本版刪除／本版新增」，來源連結
  改為「前版來源／本版來源」。純新增仍只顯示本版新增，不製造虛構的刪除側。
- 因為 `display_note` 是 PG projection，不只改靜態字串：semantic diff
  presentation 升至 v3 run
  `20046b53-1608-5320-80e1-432c6efb5465`，26 個 presentation hunks 的
  labels 已正規化為本版新增／刪除／改寫。Reader enrichment 隨新的 sealed
  diff 重建為 v14 run `44640535-2f19-51d2-afcf-1572fea9be63`。
- 獨立稽核指出 v14 seal gate 只核對七個既有 parent count，雖然九類 child
  封存後都不可變，仍缺 public ICD-code 與 private ICD mapping 的封存
  分母。v15 migration 已 live 補上 `tag_icd11_code_count=21` 與
  `tag_icd11_private_count=21`，guard 現逐一核對九類 child。
  Disposable migration/rollback、兩類 mismatch rejection、live mutation
  probe、fresh seal rollback 與 deterministic replay 全部再通過。
- 最新 reader `0.4` SHA-256 為
  `65773b51ab5866dfcc2b809e4ad7b898bd8076630fcfc991c994d6b5d62f63d2`；
  SQLite SHA-256 為
  `95097aa5091824519fcc42efb2ba3c269e33315a46e6d3da61a25a4fea5ff2e0`，
  logical SHA 為
  `e230b714a1ec6e128898f9b8dd676997362ad4b9aa1a5d81b464ff4162a14b11`。

## 2026-07-28 — FINT keyword frontier crawler

- 依 Copper 指定，以
  `https://mohwlaw.mohw.gov.tw/FINT/FINTQRY03.aspx` 建立歷史公文研究
  crawler。CAPD canary 查得並封存 6 個正式文號、6 份詳情全文、1 份
  宣告附件與 8 個 raw observations，0 issues；同一文號可被多個 query
  命中，所以資料模型採 document-number grouping＋many-to-many match，
  不以 query row 或單獨文號冒充唯一公文／條文身分；不同詳情保留為不同
  snapshots。
- CAPD canary 同時發現官方資料品質案例：健保藥字第0950070568號的詳情頁
  掛出「食品添加物規格標準.DOC」，下載 bytes 亦為該 OLE Word 文件。
  crawler 原樣保存 source edge，但附件 relevance 與 transition evidence
  必須分層，不得自動升格。
- Python 3.14 因 FINT 官方舊憑證缺少 Subject Key Identifier 而拒絕 TLS。
  新 transport 使用系統 curl 的正常憑證驗證，不使用 insecure；禁止
  redirect、核對 effective URL、限制 response size。失敗的 Python
  transport run 留在 external raw area 作 failure evidence。
- 由 sealed current chapter structural PG run
  `baae912e-8d5f-46b0-9efd-77cf4d567428` 的 661 個不同條文標題，程式化
  產生 1,946 筆 seed provenance、1,446 組 unique queries；固定 baseline
  與 synonym queries 另有明示來源。
- 建立 `fint_keyword_crawl_v17` PG migration 與 verified loader。完整
  detail text 進 PG；raw HTML／附件留 content-addressed store。Disposable
  PostgreSQL 已驗證 forward／rollback、crawler projection、idempotent
  load、seal counts 與 post-seal immutability。正式 PG 等完整 crawl
  manifest 產生後才載入，不把半套 run 標成完成。
- 啟動全期 `藥品給付規定` baseline；FINT 搜尋分母為 1,309。run 位置：
  `external_bundle:fint-crawl-20260728-baseline-v1`。
  執行中狀態不是 sealed receipt。
- 本輪全套回歸：70 項 legacy tests（1 skip）及 453 項 public tests
  （446 passed、7 個環境性 skip）通過。

## 2026-07-28 — FINT unfiltered yearly enumeration pivot

- 直接驗證 `FINTQRY03`：四個 keyword 欄位留空、`valid=3`、
  `type=etype_` 時，`19000101..20260728` 公布 17,497 筆；原
  `00000000..99991231` 亦為 17,497。最末筆 RowNo 17,497 的公文日期是
  民國 43 年（1954），因此 1900 起始不漏掉目前可列舉的已知最早紀錄。
  2025 年分母 608、2026 年 capture-cut 前分母 221，證明年度 partition
  可用。原 1,309 筆精確詞 baseline 在 265 details／999 attachment
  snapshots 時停止；raw 保留但不封存。
- 主策略改為年度空關鍵字全集。新 batch controller 先取 broad total，
  再跑互不重疊 Gregorian-year partitions，最後重取 broad total 與首頁
  fingerprint；年度 match sum、before total、after total 必須完全相等。
  關鍵字 1,446-query frontier 降為 discrepancy／更新補漏。
- Grok `grok-4.5` 以 read-only、no-search、no-subagent 方式獨立審查舊
  v17，結論 `REPAIR_THEN_REAUDIT`。實質 findings：PG 未核對逐 query
  match `1..N`、`input_sha256 UNIQUE` 阻止同 seed 重跑、搜尋 RowNo 未
  防 live reorder、空白附件標籤被漏掉、附件未綁 detail snapshot、
  loader 只核 bytes/count 未核 graph、migration reapply／rollback 與
  TRUNCATE safety 不足。正式 PG 因此維持未套用。
- crawler 升為 `fint-frontier-crawler/2.1.0`：保存所有搜尋結果頁與
  ordered RowNo/result fingerprints，query 完成前逐頁重取核對；detail
  snapshot identity 加入 exact detail URL；match 保存 detail
  observation；空白 attachment label 以 `source_label_missing=true`
  保存。
- 附件正規化拆成 `fint_attachment_declaration` 與
  `fint_attachment_snapshot`。前者綁定 `match_id + snapshot_id` 並保存
  所有官方 anchor；後者只表示實抓 bytes。`all`、`nhi_candidate`、
  `none` 是明示 byte policy，不能拿 bytes coverage 代替 declaration
  coverage 或 relevance。
- v17 staging schema 移除 seed hash 唯一限制；新增 query RowNo unique、
  attachment declaration FK、逐 query contiguous `1..N` seal gate、
  observation URL/kind binding、attachment fetch-state parity、loader
  advisory lock、manifest exact-file-set 與 graph validation。forward 可
  安全 reapply；rollback 在已有 receipt 時拒絕；sealed child 與 parent
  的 TRUNCATE 亦被阻擋。
- 真實 1954 unfiltered canary：1 expected／1 fetched、1 document group、
  1 detail snapshot、2 observations、0 attachment declarations、0 issues；
  manifest SHA-256
  `a17f4e07526bdd110294d039a45a68aa5933f007d855e4fe331c76f2bc65819c`
  已在 disposable PG sealed 為
  `85a4ba45-0f44-8a12-925f-f449dba45bf4`。此 run 是 pre-2.1 canary，
  方法證據保留；2.1 的 2026 年 221-record canary 另行執行中。
- 新增 negative receipts：非連續 RowNo、crawl 途中 search-result drift、
  空白附件 label、query/match parity tamper、同 seed 不同 output 雙 run、
  migration reapply、封存後 INSERT/TRUNCATE、帶 receipt rollback 均
  fail closed。14 項 FINT crawler/PG 專項測試通過；全 repo 回歸待年度
  canary 完成後重跑。
- 2.1 真實 2026 年度 canary 完成：221/221 RowNo details、221 document
  groups、221 snapshots、428 attachment declarations、244 stored
  observations、0 attachment bytes（明示 `none` policy）、0 issues；
  manifest SHA-256
  `496cce733d56323ac5ff6a5720086d50eaf5ddc5cf19e40ec8bdb23ce45a3856`。
  Disposable PG sealed run
  `bfaa5cb5-cb1b-82c1-9cac-5fb74f40023d`，counts 221/428/0。
- 啟動 1900–2026 yearly batch：
  `external_bundle:fint-all-years-20260728-v1`。
  一開始為節省時間複製的 2026 canary seed locator 仍寫 1954 partition；
  已在 batch 到達 2026 前移至
  `preseed-2026-wrong-origin-preserved`，不作 batch input。Crawler 2.1.1
  新增 unfiltered seed locator/date equality gate 與 negative test；正在
  執行的 2.1.0 controller 自行生成正確年度 locator，故既有年度 outputs
  不受此 canary provenance 錯誤影響。
- 新增 post-batch search-index verifier：整批 detail crawl 完成後，重新
  取得每個年度的每一個 FINTQRY03 結果頁，和該年度原始
  `search_index_sha256` 對照；不能只看 broad total／首頁未變。1954–1955
  兩年度 canary 得 1+1=2，broad before/after=2，最終逐年度 index
  verification 全通過；batch manifest SHA-256
  `a10a41ea1ef0062be79e40c26dbf0b49c45b8952137f3ad6da052bc2bb3a434a`，
  verification year-receipts SHA-256
  `798a387c512bd355a8e6511aa80163ee02c606fbf503fea27ec51f667c31e451`。

## 2026-07-29 — 歷史重建方法改為逐條 Git-like state 與 evidence union

- 依 Copper 指示把本輪共識先寫入 durable workflow，避免後續 context
  compression 退回舊方法。Canonical version unit 明定為單一條文；整章、
  年度檔與整份檔只作 source-edition containers。
- 14 份年度整編檔升格為逐條 Git-like state commits：相鄰 editions 比對
  presence、designation、structure 與完整 text hash，建立
  create／amend／delete／restore／move candidates。整條刪除必須靠
  presence→absence 偵測，條文內日期無法補回。
- 條文內民國年月仍是 surviving-text 的強 amendment index；公文、old/new
  附件、公報與館藏用於精確 transition、文號及生效日。找不到公文時保存
  bounded search receipt 與 interval precision，不把後一 snapshot date
  冒充生效日，也不把 diff 冒充一次公告。
- 重新核對 NHI `lp-3258`：父層文字是「自103年4月3日以後生效之公告」，
  但 2026-07-29 target listing 為 859 rows／43 pages，最舊可見
  111-09-06，表格另有刊登期限。故此面只表示目前存活 listing，不是
  2014 年後完整 archive；receipt 已存
  `docs/audits/2026-07-29-nhi-archive-label-surface-check.json`。
- FINT 空關鍵字 17,497-row broad crawl 從主取得策略降為 optional recall
  audit；長跑實際停在 1900–1989，共 90 annual manifests／448 match rows，
  partial raw 保留，未載入正式 PG。主流程改由 snapshot diff、date marker
  與 current-to-history gap 產生 targeted queries。
- 早期名稱系譜加入工作流程：健保署後來官方施政紀實支持 84-06-20 訂定
  `全民健康保險藥品使用規範`、84-07-01 實施、87-03-04 改名、
  87-04-01 實施；國圖 catalog `D9507418`／`84衛技字第052484號` 支持函轉
  metadata。尚未找到可公開下載的 84／85 完整原始全文，狀態是
  `availability_unresolved`，不得寫成 paper-only。
- 依 Copper 提供的國史館臺灣文獻館衛生處全宗網址，以 in-app browser
  核對：全宗宣告 12,992 筆；提供的升冪結果窗為 9,981–10,000，所以畫面
  落在 1967。UI 可見導覽停在 10,000；1995 年切片只有一筆不相關卷
  `061-12707`，精確文號與規範題名皆 0 筆。這是
  `not_found_after_declared_search`，不是 absence proof；receipt 已存
  `docs/audits/2026-07-29-taiwan-historica-health-department-search.json`。
- 依 owner 指示以 model harness 比較 Grok 4.5 與 Gemini 3.1 Pro。Grok
  兩次都只回準備搜尋的進度句，未交 direct URL／locator，兩次均 contract
  failed；依一次重試上限停止。Gemini 完成格式但 sources array 為空，
  且把 search miss 寫成過強的 availability claim，因此只保留 lead-only。
  原始回答、provider status 與 controller reconciliation 已存
  `docs/audits/2026-07-29-early-rule-multimodel-research-results.md`；失敗
  模式已蒸餾進 agent 方法學。

## 2026-07-29 — 找回 84 年原始規範、建立 raw bundle 並進 live PG

- 以後期條文句子查 FINT 並不能找到早期附件內文：
  `本保險處方用藥` 與 `處方合理之含量或規格藥品` 在
  1995-01-01..1997-12-31 都是 0。改用當時正式名稱
  `全民健康保險藥品使用規範` 後得到 2 筆，其中
  `健保醫字第84010140號`（84-06-20）詳情明載附件二及「除有特別明定者
  外，自本（八十四）年七月一日起實施」。
- 官方附件二已完成 durable content-addressed acquisition：
  `external_bundle:fint-84-baseline-20260729-v2`。
  Run 為 1 query、2 documents、2 snapshots、2 matches、2 attachment
  declarations、2 attachment byte snapshots、5 observations、0 issues；
  manifest SHA-256
  `44caa140a4c81b700a9e54265e7f7489a6b818adf38f6bac49fc5080dc43ee57`。
- `全民健康保險藥品使用規範.PDF` 是 25 頁、3,802,900 bytes 的官方掃描，
  SHA-256
  `f773cf6eeb9c413a92fae9bf543c5f1ff161726142fff802848292d00064b4d2`；
  沒有實質文字層。OCR 已做研究性試跑，但錯字明顯，只能是
  unproofread observation，未進 canonical clause text。
- 另封存日期探測
  `external_bundle:fint-85-date-probes-20260729-v1`：
  四種中文日期寫法為 0；`85/1/1` 得 11 筆但逐筆皆與本條修正無關。
  因此 `85/1/1` 精確事件狀態是
  `not_found_after_declared_search`，不是 absence proof。
- Claude Fable `claude-fable-5` 以 repo read-only、no-web、no-subagent
  檢查修後 FINT crawler、loader、migration、rollback 與 tests，裁決
  `ACCEPT_FOR_BOUNDED_LIVE_STAGE`；完整回覆存
  `docs/audits/2026-07-29-fint-fable-live-gate-response.md`。
- 套用 v17 migration 至 `hmj/vault_main` 後，成功載入 84 年 bounded
  bundle，sealed run
  `2fa58923-9a91-8c8a-9a8f-a4ee0010845d`。唯讀 live verification 確認
  state=`sealed`、issue=0、query/document/snapshot/match/declaration/
  attachment=`1/2/2/2/2/2`、RowNo=`1..2`、兩附件均 fetched PDF 且
  output SHA 等於 manifest SHA。對 sealed run 的 UPDATE probe 被
  `sealed FINT crawl runs are immutable` 拒絕。
- 修後 FINT crawler/seed/PG 專項 18/18 tests passed；相同 raw bundle 對
  live PG 重播回 `already_sealed`，run ID 與 counts 不變。
- 84 年掃描第 3 頁直接顯示通則第七條的原始基線；96 年 7 月版同號文字
  已不同並帶 `(85/1/1、86/1/1、94/6/1)`。這證明 84→96 有 observed
  text delta，但仍不能把差異自動切成三個 exact legal transitions。

## 2026-07-29 — GPT Pro 方法學 R1–R8 修補

- 保存 GPT Pro `REPAIR_THEN_ACCEPT` 全文後，已將八項修補寫入
  `docs/methodology.md`、`docs/workflow.md`、
  `docs/history-rebuild-plan.md` 與 `docs/agent-work-methodology.md`：
  source observation 與 legal version 分離、三條時間軸、中性
  appearance/text-change/disappearance vocabulary、identity lineage graph、
  current source conflict policy、exact/comparison/OCR/display text 分層、
  per-clause completeness vector 與 reader wording validator。
- 「Git-like」降為內部工作比喻。年度相鄰來源不再直接建立法律
  predecessor，條號消失不再直接叫 delete；same-number 也不自動成
  stable identity。
- reconciliation 存
  `docs/audits/2026-07-29-methodology-v4-pro-reconciliation.md`。修後摘要已
  送回同一 GPT Pro 對話作窄版 re-audit；最終裁決 `VERDICT: ACCEPT`。
  Pro 另保留非阻擋邊界：文件層一般實施日不可直接下推每條生效日、OCR
  仍是 unproofread observation、84→96 中間事件數與法律時間未知，以及
  JSONL／SQLite／reader 共同狀態與禁語 validator 尚待實作。完整回覆存
  `docs/audits/2026-07-29-methodology-v4-pro-reaudit-response.md`。
- 完成契約與 reader contract 已同步補上 v4 release gate：來源觀察不自動
  升格法律版本、三條時間軸分離、公告層通案日期不自動下推單一條文、相鄰
  快照只用中性 appearance／text-change／disappearance 用語。JSONL、
  SQLite、API 與 reader 必須共用同一 status／reader-wording validator；
  此 validator 尚未實作，故仍不得發布完整法律歷史。
- 2026-07-29：依 owner 決策，現存條文的歷史缺版分母改為最新版全文內
  不重複且有效的民國年月日，最少一版。以 sealed current parse
  `baae912e-8d5f-46b0-9efd-77cf4d567428` 程式化切出 639 條；日期推得
  3,512 個應有版本，既有全文狀態 656，尚缺 2,861 個，分布於 440 條；
  199 條目前不缺，5 條出現 date-count underflow 並保留 discrepancy。
  逐條收據：
  `docs/audits/2026-07-29-current-clause-history-inventory.json`。
- 新增 `nhi_rule_history_publication` v18 immutable PG projection 與 loader：
  639 clauses、13,874 blocks、3,487 clause-date rows。Disposable PG
  forward、load/seal/activate、active views、same-run replay、sealed UPDATE
  rejection、rollback 均通過；2026-07-29 最新 full suite 為 legacy
  70/1 skip、public 465/7 skip（合計 535，527 non-skipped pass）。
  正式 `vault_main` 套用前已送 Claude Fable 只讀獨立 gate。
- Reader transition 由新文字首次新增的 distinct date 數推版本距離：
  一個日期顯示「與上一版本差異」；兩個以上顯示「與舊版本差異」並明列
  中間缺少全文版數。0.4 例如 98 年版跨 2 個、109 年版跨 4 個、最新版
  跨 2 個預期版本。
- Claude Fable 首審與修後窄版複審均回
  `ACCEPT_FOR_LIVE_STAGE`。依其 non-blocking findings，補上 INSERT
  column lists、可逆 activation event log、active target reread、
  parity status receipt、sealed-import denominator 與 direct-sealed-insert
  guard。`parity_failed` 是已知 whole-vs-chapter 33 條差異；依 owner
  source policy，分章 ODT 為唯一現行正典，故入庫揭露而不阻擋。
- 正式 `hmj/vault_main` 已依序套用 v16 authority policy 與 v18
  publication schema，sealed/active run =
  `a707d13a-0b06-5dfe-96b7-6d107ab8793f`。Live counts、inventory 與
  fingerprints 完全等於 disposable receipt；replay 回
  `already_loaded=true` 且 activation 仍 1 列。sealed child UPDATE、
  direct sealed-run INSERT、loading-run activation 三個 adversarial
  probes 均被拒，probe rows 為 0。Live receipt：
  `docs/audits/2026-07-29-current-publication-live-verification.json`。

## 2026-07-29 — 現行條文多表面發布

- `copper-panel` 已從 active sealed PG publication 提供四組 read-only
  contract：latest list/search、single-clause detail、history inventory
  與 reviewed enrichment。Live API 回報 639 條、3,512 個應有版本、
  656 個已重建全文狀態、2,861 個缺少全文狀態；enrichment 僅輸出
  ATC、ICD-11 code 與健保治療碼，不輸出私有 ICD-11 內容。
- `personal-website-s` 已部署
  `https://s.copper0722.com/member/tools/nhi-rules/`。production probe
  證實匿名 page/JSON 均為 303，合法訂閱 session 下 page/JSON 均為
  200；same-origin dataset 含 639 條、82 個 reviewed semantic tags 與
  5 個 compound conditions。
- BOA 端預定只使用 latest-only API。當時直接 `ssh boa` 因使用錯誤的
  預設帳號而被拒，故先將 consumer call 明列為待實機證明；後續已由
  既有跳板與正確主機帳號完成，見下方 live consumer 紀錄。
- GitHub 專案定位固定為公開方法學、schema、crawler/exporter、稽核收據
  與 JSON／SQLite 可攜 release；付費條文 reader 留在 subscriber site，
  GitHub Pages 只保留原型／回饋用途，不作 production content surface。

## 2026-07-29 — BOA latest-only consumer 實機上線

- 釐清先前阻礙不是 BOA 無法連線，而是 `ssh boa` 採用了錯誤預設帳號。
  既有 `hm4 → cm1 → BOA` 跳板可登入，BOA 亦可經 private network 讀取
  `hmj:8710`；未建立任何公開 ingress。
- `boan-emr` PR #7 已 merge，commit
  `e53044b8bdce655adfb6cb7e49d7c1f931100471`。新增 bounded server-side
  client、status/list/detail same-origin proxy、exact v1 contract 與
  sealed publication validation、timeout/cache、mixed-publication rejection
  及五個 focused tests。
- 院內藥品卡仍以 `tw_drug` 唯讀鏡像取得 product→clause linkage，但條文
  全文只取 active sealed latest detail。404 代表條文不在現行分章，不以
  舊文補位；服務失聯時舊鏡像只能以 `local_mirror_fallback`、
  `latest_only=false` 與前端「可能不是現行版本」警示顯示。
- BOA PM2 已保存 private API base environment 並重啟。實機 status 回報
  `nhi-reimbursement-rules/latest/v1`、sealed run
  `a707d13a-0b06-5dfe-96b7-6d107ab8793f`、639 current clauses；CAPD 搜尋
  回 5 條，0.4 detail 回 2,123 字及 26/10/16 版本 inventory。院內 canary
  同時取得 10.1、10.8.2、10.8.2.3 三條現行全文，三者 run ID 與 output
  fingerprint 完全一致。Caddy 路徑與動態 formulary page 亦已核對。
- 公開收據不記錄 BOA 內網位址、登入資訊或院內藥品識別碼：
  `docs/audits/2026-07-29-boan-emr-latest-consumer-live-verification.json`。

## 2026-07-29 — GPT Pro 完成 84 年逐頁校對並載入 source-observation PG

- 保留並完成原 GPT Pro 任務，沒有收回。Pro 視覺檢查官方掃描 25/25 頁，
  交付 `proofread-84.md`、114-row `clauses-84.jsonl` 與 114-item
  `84-to-96-lineage-analysis.md`。交付摘要為 0 unresolved visual
  readings、0 literal deletion placeholders。
- Controller 對 UTF-8、manifest file hash/size、25 個連續 page markers、
  114 個 unique segment IDs、頁面範圍與 lineage coverage 做 fail-closed
  驗證。每個 exact segment text 只經 NFKC、空白／Markdown structure
  removal 與重複公報頁首 removal 後，均能在宣告頁面範圍找到；114/114
  通過。Lineage disposition 為 same-designation 8、renumber/move 86、
  absent-in-96 observation 6、ambiguous 14。
- 新增 v19 `nhi_rule_history_transcript` migration、rollback、loader 與
  tests。資料表正規化保存 run、proofread artifact、page、segment、
  lineage artifact 與 candidate，並外鍵連回 FINT 官方附件 snapshot。
  Loading run 只可封存一次；parent/child 的 sealed mutation 與 TRUNCATE
  都被拒絕。
- Disposable PostgreSQL 已通過 forward、實際 25/114/114 load、same-run
  idempotent replay、sealed UPDATE／DELETE／TRUNCATE rejection 與 rollback。
  正式 `hmj/vault_main` 已載入 sealed run
  `03f3b55e-8a07-5efb-b3ec-f908fbd01575`；fresh query 重算為
  1 proofread artifact／25 pages／114 segments／1 lineage artifact／
  114 candidates，fingerprints 與 sealed receipt 相同。
- Review status 固定為 `agent_proofread_pending_independent_review`。
  `source_observation_only=true`；legal identity、direct predecessor、
  segment-level legal effective date 與 complete-history claims 全為 false。
  因 84 segments 尚未與現存 canonical clause identity adjudicate，現行
  639 條的 expected/reconstructed/missing 仍為 3,512／656／2,861，沒有
  為了看起來進度較快而先扣數。
- Live receipt：
  `docs/audits/2026-07-29-source-transcript-84-live-verification.json`。
- 本輪全套回歸為 70 項 legacy tests（1 skip）與 473 項 public tests
  （466 passed、7 個環境性 skip），合計 543 項、535 項實際通過。
- PR #10 通過兩組 CI 後已 merge，merge commit
  `6f325b1a14d7179fb401dc2bfbdc72aea6bc1ec4`。公開 GitHub Release
  `source-transcript-84-v1` 已發布 proofread、114-row JSONL、
  lineage analysis、manifest 與 checksums 共五個 assets；GitHub 回報的
  asset sizes／SHA-256 全部與來源包相符。

## 2026-07-29 — 最新公告投影、代碼提示窗與自動部署

- `copper-panel` 新增 read-only
  `/api/drugs/reimbursement-rules/notices`，contract =
  `nhi-reimbursement-rules/notices/v1`。只輸出正式健保署 RSS 中已分類為
  給付規定公告的 public-safe 欄位，不洩漏內部 queue state；並明列
  `rss_published_at` 不是法律生效日。完整 API suite 152/152 通過，
  commit `ffcfad6b2f6f` 已部署至 hmj。
- `personal-website-s` build 現在 fail-closed 讀取 latest、history
  summary、enrichments、notices 四個 typed contracts。正式 subscriber
  JSON 含 639 個 current clauses、82 個 reviewed semantic tags、5 個
  compound conditions、14 則最新給付公告；最新公告為健保署
  115-07-28 降血脂藥品支付價格及給付規定修訂。
- 依 Copper 指示，ATC／ICD-11／健保治療碼不再直接出現在條文正文。
  桌機以 hover 或鍵盤 focus 顯示單一 tooltip；手機／粗指標裝置首次
  點按顯示、第二次才跟隨站內連結。七個 insulin ATC 的提示窗經實際
  browser adversarial check 後修成可換行、獨立 code chips，沒有水平
  overflow。網站 135/135 tests、69-page build 通過。
- 付費站 commit
  `2c88acb8d80a05efe5727b571a11e976f290df22` 已部署為 Cloudflare Pages
  production `1aa1de15-899e-4454-8505-97a9be42a01a`。正式站匿名
  page／JSON = 303，合法訂閱 session page／JSON = 200；live subscriber
  JSON SHA-256 =
  `cd3ee1a0ae5159f39df966bdc9251a55737044fa7e30e2e8a7405460585e0031`。
- 新增 deterministic handler
  `nhi-rule-history-subscriber-sync.py`，commit `76a15961`。它在乾淨、
  已推送的 main 上計算四個 PG-backed projection 的 meaningful
  fingerprint；忽略每次 poll 都會變動但不影響頁面的 observation
  timestamp。只有 fingerprint 改變才執行完整 subscriber deploy；
  成功後以短效合法訂閱 session 抓正式 JSON，要求與本次 artifact
  exact SHA-256 相等才更新部署 receipt。
- PG `quality_audit_tasks.id=276`
  `nhi_rule_history_subscriber_projection_sync` 已註冊為 task 260 的
  dependent、hm4 target、15 分鐘 cadence。首輪經正式 runner 執行回
  `up_to_date`，projection fingerprint =
  `6f18ae2c97a8ac2d95ba443e17fdb2807e75afd16fa1edf4fb3fe0fc3c39e326`，
  next due 正常前進。task 261 仍為 `skipped_gate`／2099；本輪沒有啟動
  Claude 條文整理或 canonical history promotion。

## 2026-07-29 — Gemini terminology alias groundwork

- 依 Copper 指示，修正原本把「標籤存在」綁死在「目前條文已出現」的
  錯誤設計。正規化分為三層：PG authoritative terminology masters、
  concept/alias、deterministic clause occurrences。ATC、ICD-11 與健保
  治療／處置給付底表可獨立存在；只有 occurrence 必須實際命中條文。
- Live PG 只讀盤點為 `tw_drug.ref_atc` 6,812 rows、
  `medical_knowledge.icd11_who` 34,663 rows、以及
  `tw_health_open.nhi_payment_standard` 6,151 rows。完整正式字典不交由
  模型重建；ICD-11 title／URI／definition 仍留在私有 PG。
- 透過 model-harness 明確呼叫 `agy / Gemini 3.6 Flash (High)`，以現有
  82 個 reader semantic tags 產生 alias/concept bridge。首次 82-row
  單批回覆 18,238 字但缺 end marker，harness 正確判失敗；保留原始失敗
  後，以 21/21/21/19 個 immutable tag IDs 做一次 partitioned recovery，
  四批全部通過 provider/model 與 begin/end marker contract。
- Controller 對四批 JSONL 做 source identity/code 守恆與 collision
  驗證：79 concepts、371 aliases、336 個 model-proposed auto-match、
  35 個 context-required；82/82 source tag IDs 恰好一次、0 missing、
  0 duplicate、0 unknown/unbacked code。另發現 8 個 normalized alias
  collisions，所以 `auto_match` 尚未獲 production admission。
- 公開 proposal 已寫入
  `data/proposals/gemini-semantic-alias-2026-07-29/`，移除正式 master
  payload 與 private ICD-11 content，只保留候選 concept／alias、
  source-tag references 與 validation receipt。正式 PG 尚未更動。
- 同一案例已回饋到 private `model-harness` skill：新增 expanding-inventory
  預先拆批、missing end marker = structured truncation、source identity
  denominator、master/alias/occurrence 分層及 alias collision gate；完整
  prompt、首輪失敗、四批 recovery、候選與驗證收據均留在
  `skills/model-harness/audit/2026-07-29-gemini-semantic-alias-inventory/`。
