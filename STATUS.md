# 專案進度

最後核對：2026-07-28

## 目前操作狀態

- 條文整理 agent dispatch 已依 Copper 指示暫停：PG proposal task =
  `skipped_gate`、next due = 2099；執行 wrapper 預設
  `NHI_RULE_HISTORY_AGENT_DISPATCH_ENABLED=false`。
- deterministic RSS／raw acquisition 可保留，但不呼叫 Claude 或其他模型
  整理條文。
- canonical 方法已改為 v3 transition-evidence contract；公告 linkage 是
  獨立補強 coverage，不是必要完成 gate。舊 3,080-row v1 queue 保留作
  discovery provenance，不直接執行。

## 已完成

- 公開 GitHub repo 與資料授權邊界。
- 14 份歷史 ODT 的 checksum manifest。
- staging parser、PostgreSQL loader、migration 與 70 項 repo 測試。
- 14 份 ODT、213,512 個結構區塊、9,303 個候選的封存 run。
- v1 八表 deterministic JSONL／SQLite exporter；完整 empirical export
  通過 typed-row parity，release assets 已準備但尚未發布。
- `通則` PG-first 模板已改成正確的單條文版本模型：
  `nhi_rule_history_edition` 只保存 15 份來源版容器；
  `nhi_rule_history_clause` 以 `0.1–0.12` 各自保存版本鏈。sealed import
  含 12 條文、152 筆來源版 observation、29 個不同文字 states、318 個
  blocks、261 個文內日期 facts、17 條 same-clause edges 與 26 組 hunks。
  JSONL、SQLite 與 12 份 reader JSON 均由該 import 重建並通過 count/hash、
  foreign-key、integrity 與 idempotent replay。
  `official_source_universe_closed=false`、`legal_history_complete=false`，
  不改變全庫法律歷史 0/1,548 的結論。
- 第 `0.4` 條 reader enrichment 已在 PostgreSQL 封存：
  semantic diff presentation v2 忽略 Unicode 空白、單引號與全／半形；
  純新增不產生虛構的「下一版刪除」。81 個語意標籤含 61 個藥名／藥物類別
  與 20 個疾病詞，另有 70 筆 ATC 關聯、21 筆 code-only ICD-11 關聯、
  24 個條件詞規則及 1 筆 agent 歷史摘要。疾病 code 分成
  `agent_selected` 與 `candidate`，候選在 UI 明列待人工確認；WHO 標題、
  URI、定義與參考快照不進 Git。
  Reader enrichment v4 新增 `apomorphine → N04BC07` 完整詞索引，並禁止
  在拉丁字詞內以子字串錯配（例如不再把 apomorphine 的 morphine 連到
  N02AA01）。正文只顯示變色且可點擊的藥名／病名；ATC、ICD-11 code 與
  mapping status 必須點進站內 tag 頁才顯示，不在條文旁直接展開。
  條件詞中 `且`、`或`同屬邏輯詞並共用樣式；`需要`因多屬主觀判斷，已從
  強調索引移除。`至多`因幾乎是本條常態也不再強調，改由 PG 程式化保存並
  顯示 `二週`、`六天`、`一個月`、`每週`等 `duration` 標記；`應`也不再
  單獨變色。變更數量 `15支`、`20支`以同 schema 的 `quantity` 角色顯示，
  單字 `限` 遇到複合詞 `上限`時排除。
  Reader enrichment v10 另把上限式中的 `20,000U` 與同句替代值
  `100mcg` 程式化存為 `quantity`；`不超過`維持普通文字。條列段落的負
  `text-indent` 不再被行內語意連結繼承，避免「治療」與「糖尿病」等
  相鄰文字發生字形重疊；正式頁面尚待本輪部署後核對。
  語意連結採 `coding-able` 門檻：有已驗證 ATC／ICD-11 關係才上連結。
  因此 CAPD 維持純文字；透析液、抗生素、抗凝血劑、第八／第九／第十三
  凝血因子、繞徑治療藥物等已補入。過廣且無單一 code 的「癌症」不再產生
  待判讀連結。
- `0.4` 的歷史主標籤已改由「下一版首次新出現的文內日期註記」程式化產生，
  例如 99 年版顯示 `99/11`；同時保留來源版名，並明示日期尚未認定為法律
  生效日。
- GitHub Pages workflow 已建立並成功發布 `prototype/reader`：
  <https://copper0722.github.io/nhi-rule-history/?rule=0.4>。正式 URL 已核對
  HTTP 200、PG 投影載入、`99/11`、agent 摘要、ICD-11 code、純新增 diff
  與零水平溢位；桌面與 390×844 手機 viewport 均已實測。最新正式部署
  commit `4f491ec5` 已確認載入 reader enrichment v4，`apomorphine` 完整
  對應 N04BC07，且 agent summary 內沒有錯誤的 morphine 子字串 tag。
- 長條文導覽已改為響應式 reader controls：桌面同一列左側凍結條號／
  標題／本頁導覽，右側凍結「搜尋通則條文」；手機不占用頂端閱讀空間，
  改用底部浮動島。目錄以側滑抽屜顯示並隨閱讀位置標記目前章節，搜尋則
  展開為底部面板。搜尋結果不再把正文向下推。
- ATC linkage 資料模型，以及 ICD-11 授權 fail-closed 邊界。
- 重新確認 ATC 的官方來源鏈：INAE3000 是既有高頻 current lookup，
  IODE `A21030000I-E41001-001` 是可整批重建的每月 CSV。2026-07-27
  exact raw fetch 為 96,799,113 bytes、224,455 列、45,124 個藥品代號、
  2,244 個 ATC codes、95,520 個給付文件連結；SHA-256
  `5abfec9bd0afb74f13cabca3402c2d6a0329b3436dd206f9dea83288a8b1d4a2`。
- 新增 NHI drug-linkage content-addressed fetcher、欄名漂移／零列
  fail-closed 驗證，以及 PostgreSQL／SQLite 對稱的
  `linkage_import_run`、`nhi_drug_item_observation`、
  `nhi_drug_rule_reference` logical schema。
- 稽核 legacy runtime：45,129 個 current drug rows 均有 ATC；7,498 個
  品項→給付條文 links 中 6,990 列解析到 498 條文章、508 列尚未解析。
  另 2,025 條 article↔ATC mappings 中 1,708 為自動、317 為 curated；
  不能把三類證據混稱官方 direct linkage。
- 2026-07-28 直接對
  `POST /api/INAE3000/INAE3000S01/SQL0001` 做一列只讀核對，官方 current
  API 回報 14,066 筆現行品項，schema 仍含品項代碼、ATC、給付代碼清單、
  給付文件清單與價格有效期。INAE3000 作每週 freshness；IODE 月檔仍是
  immutable clean-room rebuild 基線。
- 最新全套驗證為 70 項 legacy tests（1 skip）與 432 項 public tests
  （425 passed、7 skip；含 `通則` PG/JSONL/SQLite/reader contracts、
  響應式 reader controls 與真實 PostgreSQL linkage transaction test）；
  public-tree、SQLite integrity／foreign-key 與正式 IODE raw fetch smoke
  均通過。
- `raw-odt-v1` GitHub Release：14 個 ODT、49,709,507 bytes，下載檔名、
  size 與 SHA-256 已和 manifest 對照通過。
- v2 程式化 update pipeline：source plan、independent discover A/B、
  `compare-discovery`、resumable fetch、content-addressed raw、offline verify、
  generic ODT structural parse 與 PG loaders。
- MOHW FINT bounded run（2021-01-01..2026-07-27）：366 detail pages、
  1,353 attachments、1,719 resources；A/B resource-key set 完全一致。
- corrected acquisition PG run `51189ce2-ce51-461c-bd96-c59a526f6065`
  已 sealed：1,712 unique artifacts、85,642,128 bytes、0 issues。
- 同一 post-109 bounded set 已依 366 個正式文號形成 366 個 deterministic
  notice bundles；全部 1,353 attachments／1,719 resources 均 offline
  verify，且 byte-identical replay 通過。
- corrected structural PG run `d60dcfb2-2bd1-4c3e-8baf-5ad998b01f54`
  已 sealed：360 ODT resources／358 unique artifacts、31,377 blocks、
  1,228 occurrence candidates、547 nonblocking issues、0 blocking issues。
- 1996–2020 歷史公告精確詞基線已完成兩輪獨立 discovery：1996–1998
  為 0 筆，1999–2020 共 942 detail／1,178 attachments／2,120 resources，
  A/B key set 完全一致。Corrected raw run
  `c94220d4-3d06-4b05-8047-f833e41eebc1` 已抓取 91,694,925 bytes、0
  issues 並 sealed；240/240 ODT 已解析為 13,995 blocks／676 occurrence
  candidates，structural run `c9328039-7093-4f8a-b7eb-84d0ed245760`
  亦已 sealed。
- 同一 bounded 集合已依 942 個正式文號各自形成 deterministic notice
  bundle；全部 1,178 個附件、2,120 個 resources、91,694,925 bytes 均
  offline verify，且重跑為 byte-identical replay。這只關閉
  source-local bundle materialization，不代表來源宇宙、生效日或逐條歷史
  已完整。
- historical bounded set 的 431/431 PDF artifacts 已完成 deterministic
  text＋geometry extraction：845 頁、58,981 words、0 blocking parse
  failures；7 個 zero-word pages 明列 `needs_ocr_or_visual_review`。這關閉
  PDF 檔案級 typed extraction，不代表表格語意、OCR、法律 effect 或逐條
  歷史已完成。
- 147/147 ODS artifacts 亦已完成 deterministic cell extraction：179
  sheets、950 physical rows、6,521 physical cells、0 unsupported cells。
  官方檔以 repeat 壓縮約 1.431 兆 logical cells，parser 只保存 exact
  logical ranges／repeat／span／formula／typed value，不做不可控展開；
  byte-identical replay 已通過。此層尚未接 marker matcher 或法律 semantics。
- 13/13 historical image artifacts（9 JPEG、3 GIF、1 TIFF）已完成
  magic/hash/resource binding、deterministic render 與 sandboxed local
  OCR；13/13 OCR observations 均非空，共 11,959 characters，但全數仍是
  `needs_visual_review`、0 human-verified。OCR 候選不能直接升為官方文字。
- acquisition/structural migrations 均通過 transaction rollback，
  live load 後以 fresh connection 重算 counts 與 row-set fingerprints。
- v2 的 corrected/superseded run、capture window、PG sealed fingerprints
  與 0-collision receipt 已寫入 machine-readable release eligibility gate；
  現有資產只稱 partial evidence bundle，不稱 portable dataset release。
- 「通則」與專案導航碼 `chapter:00` 的 provenance 已分離進 PostgreSQL／
  SQLite schema；讀者顯示不得稱「第 0 章」。
- 完成 legacy history 日期註記稽核：1,548 條 current articles／3,691
  version rows 中，980 條現行文字含日期註記，而 980/980 的日期集合都與
  legacy version dates 不一致。稽核結果已機器可讀保存。
- PostgreSQL／SQLite schema 已加入 source-local 日期註記、公告 effect
  對應，以及逐條文完整性 gate；`complete_to_declared_cut` 會在 annotation、
  transition、direct adjacency、source-universe closure 或 anchor parity
  任一缺口存在時拒絕寫入。
- legacy current text 的日期註記已程式化抽取至隔離、append-only stage：
  1,548 條均有 exact text observation，983 條共 6,366 個原樣 marker；
  6,360 個可形成 ISO calendar candidate，分布於 323 個不同日期
  （1996-01-01..2026-07-01）；其餘 6 個已逐筆確認為 Trelegy Ellipta
  `92/55/22`／`184/55/22 mcg` 劑量，不是日期。raw stage 為保留抽取證據
  仍全數 `unresolved_event`；正式 amendment-date denominator 是 6,360，
  所以這是明確 gap inventory，不是完整歷史。
- 第一輪真實 event-resolution stage 已 sealed：將 6,366 個 raw candidates 與
  5 個 2026-08-01 新公告 effect 依 exact date、條號與 locator 比對，得到
  6,360 `no_match`、6 `invalid`、0 `resolved_candidate`；沒有把不相干的
  新公告誤接成舊沿革，也沒有寫 canonical history。
- 另以 sealed 的 1999–2020 historical ODT stage 做唯讀 candidate preflight：
  6,360 個有效 marker 形成 3,080 個條文×日期組合，其中 1,897 組
  （61.59%）可在 240 份 ODT 找到同日期；3,010 組正式數字條號中，只有
  826 組（27.44%）可在同一 artifact 同時找到日期與條號。70 組 `0.x`
  是「通則」的 project navigation code，只檢查日期，不冒充官方條號。
  這只是 candidate co-occurrence，沒有解析公告、生效日、修正 effect、
  前後版本或 canonical history。
- 同一 denominator 已再由跨格式 matcher 以兩次 byte-identical replay
  重算：納入 ODT、PDF、OLE、ODS 的原生文字後，同日期候選為
  2,034/3,080（較 ODT +137），同一 artifact 的日期＋正式條號候選為
  909/3,010（+83）。未人工覆核 OCR 另列 155 個日期候選與 4 個
  日期＋條號候選；其中只有 3 個 joint candidate 超出 native lane，且
  `authoritative_text=0`，不能升格為官方文字或法律 link。
- 909 個原生文字 joint candidates 已再以 artifact→resource→正式文號的
  sealed acquisition 鏈回溯；0 unmapped，490 組只有一個正式文號候選，
  419 組有 2–11 個候選，共 282 個不同文號。此步只形成 bounded review
  queue，沒有把 unique candidate 自動升格為 amendment effect。
- 3,080 個條文×日期已全數程式化成一對一、可續跑的 v1 source-review
  discovery units，並綁定兩份輸入 ledger 的 SHA-256。優先序為 490
  `unique_document_candidate`、419 `ambiguous_document_candidates`、
  1,125 `native_date_without_joint_document_candidate`、1,046
  `marker_without_native_document_date_match`；合計精確為 3,080。v1
  mandatory official-event gate 已撤回，須先轉成 v3 direct-edge work
  units，再回填 transition evidence、日期角色、stable identity、前後
  exact text、direct adjacency 與 anchor replay；
  `canonical_write_authorized=false`。
- 第一批 5 個 history closure canary 已逐份核對官方 ODT：5/5 都明寫
  `自…生效`、有完整舊／新欄，且新欄與 2026 whole/chapter endpoint
  相等。這只把 source-local 日期角色確認為 5/5；PG event/effect admission
  仍為 0、direct predecessor 與歷史 anchor replay 仍為 0，因此法律
  closure delta 是 0，逐條完整性仍是 0/1,548。
- stage-only continuous updater 曾在真實 PostgreSQL 排程通過 scheduled
  poll 與 proposal fires。來源 bundle／corpus bundle 會保留公告明列的
  全部附件，包括多 PDF、ODS 與 ODT；兩則真實工作均驗到 cm1 Claude
  failure／timeout 後只啟動一次 hm4 Codex fallback，並安全停在
  `staged_needs_review`。`AUTO_PROMOTION_ENABLED=false`，尚未寫 canonical
  history。proposal dispatch 現已停用，不再呼叫 Claude。
- 2026-07-28 最新 fresh live observation：21 個 update work items
  分別為 2 `selected`、9 `staged_needs_review`、2 `failed_terminal`、
  8 `ignored_non_rule`；`corpus_registered` 為 0。
  9/9 candidate proposals 均需人工複核。兩個 terminal works 各自保有
  primary→fallback 的 immutable failure receipt；recovery-v2 additive
  migration 已套入 live，舊 queue／transition／attempt rows 與 fingerprints
  均未改動。兩筆 legacy receipts 已 hash-bound admission 為 2 列 failure
  evidence／4 列 attempt evidence，未偽造 PostgreSQL worker-attempt rows；
  兩筆 generation 2 都以零 model calls、零 execution jobs、零 candidate
  或 bundle claims 結束為 terminal `partition_required`。canonical history
  schema 仍不存在。
- GPT Pro post-audit v2 以 `C/H/M/L=0/0/1/2` 接受 R2 stage-only
  worker/runtime/recovery repair。依其條件，legacy admission 現在明列
  `sha256_hex_v1` attempt identity、immutable JSONL origin、verifier
  contract/code/output schema 與 admission payload hash。尚未關閉的 Medium
  是 live Claude model resolution／zero-custom-context isolation canary；
  在此收據通過前，不執行 cefiderocol official-source canary，也不解除
  repair hold。
- synthetic、stdin-only、零官方來源的 primary Claude isolation canary
  已執行，但在成功 inference 前以 code 1 結束，stdout 0 bytes；因此實際
  resolved model 仍不可觀測，tools／MCP／custom context／plugin execution
  也只能證明設定層隔離，不能證明 event 層為零。狀態是
  `routing-observation-incomplete`；repair hold 保留，official-source
  canary 仍未獲授權。
- 保留上述失敗 receipt 後，以修正過的同一路徑重跑 534-byte synthetic
  canary：13.028 秒成功，selected model 精確解析為第一方
  `claude-sonnet-5`，CLI 另透明回報第一方 Haiku auxiliary；
  `web_search=0`、`web_fetch=0`、permission denial=0。safe mode 明列停用
  CLAUDE.md、skills、plugins、hooks、MCP、commands、agents、styles 與
  workflows；managed settings 僅有 root-owned `autoCompactEnabled`
  boolean，session persistence 關閉。獨立 runtime review C/H/M=`0/0/0`，
  44 項 hostile process／overflow／drift tests 通過，結束後 isolated
  process 與 runtime dir 都是 0。這只解除一次 stage-only official-source
  canary 的 block；repair hold 與 canonical mutation 仍未授權。
- 第一筆指定 work item 的 official-source canary 已開始：
  `cf546edf-7979-5ef8-8dec-7c80e538cd59`（cefiderocol/Fetroja）。6 個
  resources／4 個正式附件已形成 content-addressed source bundle，
  bundle ID
  `4664185ecf5a642776ebe7985f18be516b5910b0d9a71f1a3717c352ae669a44`、
  fingerprint
  `fa5a2c9410735374947a50c56afd992ff9fb5641a03eb8a6611ca633f1fc55ae`。
  流程在模型呼叫前 fail closed：正式頁的發文字號是
  `健保審字第1150055418號。`，且公告事項有兩段；舊 parser 不接受末尾
  全形句號並只保留第一段。work item 安全停在 `acquired`，model calls
  為 0。Additive manifest v1.3／normalization rule 1.1.0 已完成真實
  bundle replay，精確保存 raw 文號、兩段公告與四附件；修補經獨立
  re-review 判定 `SHIP`、C/H/M/L=`0/0/0/0`。live registrar 已升至
  manifest v1.3，corpus filesystem、PG progress 與 active audit 均驗證。
  proposal suitability gate 因同時看見父層 `10.3` 與 leaf `10.3.8`
  而安全停在 `partition_required`，reason=`MULTI_RULE_DOCUMENT`，
  worker calls／attempts 都是 0，沒有 candidate 或 canonical write。
  post-canary audit 為 C/H/M/L=`0/0/1/0`：窄義 parser/runtime repair
  hold 已解除。其後新增 hierarchy-aware suitability v2：只有唯一 leaf、
  其餘候選全為 dot-boundary 祖先，且祖先沒有獨立 comparison row 時才
  collapse；平行 leaves 或祖先獨立成列仍 fail closed。原封存
  cefiderocol packet 的唯讀重算已由 `10.3`＋`10.3.8` 收斂為 leaf
  `10.3.8`，decision=`suitable`；獨立 review 為 `SHIP`、
  C/H/M/L=`0/0/0/2`，兩個 Low 測試缺口均已補強。這只解除狹義父節／子條
  誤判；舊 terminal row／receipt 未改寫，新 recovery generation 尚未建立，
  worker calls 仍為 0，所以仍沒有評估 Claude／Grok 能力。一般多條文
  partition、source-universe 與 legal-history completeness hold 均維持。
- 2026-07-28T04:36:17+08:00 fresh live read-only observation：21 個 work
  items 為 2 `failed_terminal`、8 `ignored_non_rule`、2
  `partition_required`、9 `staged_needs_review`；9/9 candidate proposals
  都是 `needs_review`。cefiderocol 的 `work_generation` 仍為 0 rows，
  canonical legal-history schema 仍不存在。
- 2026-07-27T16:09:28Z 前次 live observation：21 個 update work items
  分別為 3 `selected`、1 `corpus_registered`、8
  `staged_needs_review`、1 `failed_terminal`、8 `ignored_non_rule`；
  8/8 candidate proposals 均需人工複核。該 terminal item 的 primary 與
  fallback 都 timeout，failure receipt 已持久化，沒有自動重試成 canonical
  寫入。Corpus registrar 的 manifest v1.2 與完整函式簽章 hardening 已套用
  並核對 live attributes／ACL。
- 2026-07-27T13:01:41Z 再以 fresh read-only query 核對 live stage：
  immutable raw annotation stage 的 6,366/6,366 rows 仍為
  `unresolved_event`；resolution outcomes 為 6,360 `no_match`、6
  `invalid`、0 `resolved_candidate`。當時 7/7 proposals 均為 `needs_review`，
  canonical history schema 不存在。機器可讀 observation 已封存，避免
  文件進度與實庫狀態互相推論。
- NHI 現行整份／分章 anchor 已由正式 CLI 獨立列舉兩次，均得到 268 個
  resources，key-set 完全一致；266 個附件已全數實抓，267 個 unique
  artifacts／57,999,120 bytes 通過 raw verification。92/92 ODT 已解析為
  44,504 blocks／1,322 occurrence candidates，兩個 PG runs 均 sealed。
- NHI `lp-3258` 法規公告 listing adapter 已依 live DOM／query pagination
  修正並做兩輪全量、未先篩關鍵字的 enumeration：43/43 頁、858/858 rows，
  兩輪 resource JSONL byte-identical，resource-key set 完全一致；858/858
  detail pages 與 2,400/2,400 attachments 亦已抓取並 sealed。NHI 與 FINT
  的 parsed metadata 再按正規化正式文號分組對帳：NHI 858 rows／847 keys，
  FINT exact phrase 366 rows／365 keys，交集 217、NHI-only 630、
  FINT-only 148，union 995；另有 7 個 collision keys，因此不能做一對一
  join。這關閉可重跑的 source-surface discrepancy inventory，不關閉
  relevance、同義 query、法律來源或逐條 event/effect。
- 同批 occurrence header multiset preflight 已完成：整份 662、分章 660，
  其中 655 個相符、整份獨有 7、分章獨有 5。這是明確的 discrepancy
  inventory；只比較條號／標題 occurrence，不是全文逐條 parity，故
  whole↔chapter gate 仍為 open。
- 後續已用 sealed lossless structural rows 重建現行整份／分章各 639 條
  完整條文並逐條比對；606 條全文相同、33 條不同，無結構 blocker。這
  證明目前兩個官方 current surfaces 確實不一致，狀態為 `parity_failed`；
  其中 19 個是 leafmost mismatches，其餘是子條文差異向父層傳播。仍須逐一
  判讀何者為正確／何時生效，不能任選一側當 canonical anchor。
- 19 個 leafmost mismatches 已逐條 exact diff 並暫分為：6 個版本／日期
  實質差異、6 個 list-marker 結構差異、6 個純標點、1 個尾端補充表 layout。
  `8.2.16` 的分章檔包含 115/8/1 future-effective 版本，是不能把「最新
  公布」直接當「今日生效」的實例。分類未選 canonical side，whole↔chapter
  gate 仍 open。
- 已完成 PG-driven 單條文 reader template：`?rule=0.4` 只顯示 `0.4`
  最新全文及其 9 組歷史 diff；`0.2` 的 15 份相同來源觀察則正確折疊為
  1 個文字版本、0 組 diff。全域搜尋從 12 條 index 依藥名／條件選路。
  兩欄桌面、手機堆疊、紅綠文字標籤與官方來源連結均由 sealed PG
  projection 產生，不含手寫條文或 browser-side diff；尚未接付費站正式
  API／route。

## 尚未完成

- 完整官方來源宇宙：MOHW FINT 仍只封閉「精確字串＋日期窗」；NHI current
  whole／chapter 的 acquisition 已關閉；全文 parity 已執行但失敗（639
  條中 33 條不同）；NHI listing 的 858-row index 與 858/858 detail HTML
  皆已兩輪擷取，兩輪各解析出相同的 2,400 個附件 URL 與 5 個零附件頁；
  2,400/2,400 附件已抓取（476,139,573 bytes、2,396 unique artifacts、
  0 issues）並 sealed 到 append-only PG acquisition stage。NHI↔FINT
  文號分組對帳留下 NHI-only 630、FINT-only 148 及 7 個 collision keys；
  相關性、附件語意、同義 query、法務來源與逐筆 discrepancy 裁決仍待
  closure。
  歷史精確詞資料的 431 PDF 已完成 text＋geometry extraction，但 7 個
  zero-word pages 仍需 OCR／視覺覆核；147 ODS 已完成 typed cell
  extraction；13 images 已 render＋OCR 但仍有 13/13 visual review；347/347
  OLE 已完成 CFB inventory 與 Word／Excel typed extraction，344 份有原生
  typed output，另 3 份 Word（5 頁）明列為 image-only visual review。
  PDF／OLE／ODS 的原生文字已接入 date/designation matcher；仍須解析法律
  effect。OCR 只作非 authoritative 搜尋候選。
- 法律生效日與 transition evidence ledger；公告以 optional notice link
  保存。
- 6,360 個有效 source date annotations 的日期角色／transition evidence
  adjudication；
  另 6 個 raw slash-triplet occurrences 已終結判為非日期劑量；
  exact marker backfill 已完成；cross-format preflight 找到 2,034/3,080
  原生文字同日期候選及 909/3,010 同檔日期＋正式條號候選。第一輪舊
  event resolver 的 0/6,360 只表示沒對上那 5 個 future-effective 新公告，
  不能當 v3 completion gate，也不能推論舊公告不存在。
- 穩定條文身分、條號重用、split／merge／move／restore／correction。
- cumulative anchor event replay。
- 全庫經法律證據驗證的 direct-predecessor diff；`通則` 已完成的 17 條
  same-clause source-observed text-state diffs 明確不冒充法律
  direct-predecessor edges。
- ATC raw acquisition 與 portable schema 已可重跑，但 public normalized
  linkage release 尚未產生；legacy formulary 比同日官方 CSV 多 148 列，
  508 個品項→條文 edges 未解析，rule→ATC 仍只允許標示為
  product-evidence-derived，不可宣稱整個 ATC class 受該條文規範。
  INAE3000 current API 是既有 runtime 的 ATC／給付 reference direct
  evidence 與每週 freshness lane；IODE 月檔才是 clean-room immutable
  rebuild baseline。兩者必須保存各自 snapshot／row provenance，不可互相
  覆寫成一張無版本 current table。
- anchor-gated canonical promotion；需讓 future-effective snapshot 先排程，
  到生效日且 replay 通過後才成 current，前一版才轉為 history。
  多輪獨立 disposable-PG review 已依序找出來源綁定、時間鏈、MIME
  self-attestation、假 ODF ZIP、壞 CRC payload 與 51-byte 假 PDF 等
  High。最新 fail-closed 草案把 release-linked ODT、ODS、PDF 全部設為
  observation-only；三個 format policy 均無正向 lane，須等 governed
  external full-document verifier 才能另行開放。42/42 專項及 271 項
  public suite 通過（6 skip）；第八次獨立 disposable-PG review 的
  C/H/M/L 為 0/0/1/2，local Critical／High gate 已清；migration 尚無
  正向 lane，也未套用 live PostgreSQL。
- GPT Pro 已對 exact fingerprinted public-method packet 回覆
  `PRO_METHOD_AUDIT=ACCEPT`，C/H/M/L 同為 0/0/1/2；明確維持
  `LEGAL_HISTORY_COMPLETE=false`、`CANONICAL_LIVE_APPLY=BLOCKED` 與
  `PUBLICATION_AUTHORIZED=false`。唯一 Medium 是尚無 governed positive
  full-document verifier／admission path。兩個 Low 已轉為 reader/API
  regression：`chapter:00` 永不呈現為官方「第 0 章」，每個 coverage
  numerator 必須連同 population、exclusions 與 claim limit。
- 全庫 normalized public clause dataset 與 SQLite snapshot；`通則` 的
  12-clause JSONL 與 SQLite projection 已完成。
- v2 acquisition/structural 的 typed-row JSONL↔SQLite projection。
- v2 clean-room rebuild 與 final-commit code-hash binding。
- 付費站正式真實資料 API／讀者 route；`通則` 靜態 PG projection template
  已完成。

## 目前完整性結論

**逐條文歷史目前不完整。** 日期註記是缺口偵測器，不是舊版全文；只有在
每個 marker 都已抽取並取得終結日期角色／transition 裁決、每個 accepted
transition 都有至少一種直接官方 evidence basis、完整 before/after
snapshot、相鄰 edge，且 cumulative anchor replay 與來源宇宙 cut 都通過
後，單條才可標示
`evidence_complete_to_declared_cut_for_enumerated_official_versions`。
公告 linkage 另計，不是必要 gate。

2026-07-27 的舊契約 scoreboard 為 **0/1,548 條通過完整性 gate**。983 條
至少有一個有效日期候選，但尚未依 v3 契約裁決；其餘 565 條沒有有效日期
候選，也不能因此假定從未修正。

因此可對外說「0/1,548 尚未通過完整性認證」，不可改寫成「已證明 1,548
條都有缺版」。日期讓缺口 audit 變容易，不代表重建工作已完成。

下一步不是派 agent 逐筆量產，而是 M1/M2：建立 v3
`transition_evidence`／optional `transition_notice_link` schema、validator
與 v1→v3 queue converter，再凍結 10 個代表性 pilot packets。Copper 明確
指示恢復 dispatch 且 pilot 通過後，才可分批處理。

注意：第一次 acquisition run `43ecabc6-64c6-4f30-80ec-ecebe25ea361`
在雙輪驗證時發現 RowNo-based resource identity 缺陷。該 immutable run 留在
PG 作 audit evidence，標記為 `superseded_by_methodology`，不作 release input；
正式 stage 是上列 corrected run `51189ce2…`。

逐項關閉證據見 [docs/gap-register.md](docs/gap-register.md)。
