# Worklog

## 2026-07-28

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
