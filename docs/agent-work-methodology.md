# 逐條文歷史工作的 agent 方法學

狀態：`canonical_method_v3`

適用範圍：逐條文歷史重建、日期註記裁決、相鄰版本 diff 與完整度認證

取代範圍：v1 `history-gap-work-unit` 中「必須找到 official event」的要求

## 核心裁決

無法證明每一份歷史公告至今仍公開存在、仍被現行搜尋引擎收錄，或一定能由
目前可用的官方查詢介面找到。因此：

- 「找到原始公告」不是 transition 或逐條歷史完成的必要條件；
- 公告是高價值的補強證據與 provenance，不是唯一合法的 evidence basis；
- bounded search 沒找到時只能記
  `notice_not_found_after_bounded_search` 或
  `notice_availability_unknown`，不得記為「公告不存在」；
- 不能因公告缺失，丟掉已由官方 cumulative 版本、old/new 對照表或官方
  版本註記證實的文字變更；
- 也不能因找到了公告，就跳過完整前後文、stable identity、direct
  adjacency 與 anchor replay。

本專案所能認證的是：

`evidence_complete_to_declared_cut_for_enumerated_official_versions`

它表示在明列、可重播的官方來源集合與 declared cut 內，所有觀察到的相鄰
版本差異都已有終結裁決。當 `source_universe_status=open` 時，不得縮寫成
「完整歷史」或暗示網站上所有曾存在的公告都已找到。

## 正確的工作單位

舊 queue 以 `條文 × 日期註記` 為工作單位，適合做 recall checklist，但不適合
直接作法律 transition 的主鍵。v3 的工作單位是：

```text
stable-clause candidate
× one proposed direct-predecessor edge
× one declared source-universe cut
```

日期註記是這個 edge 的 observation/evidence，可以是零筆、一筆或多筆。公告
也只是 edge 的可選 evidence link。每個 work unit 必須 hash-bind：

- declared source set、query profiles、capture cut 與 source-universe status；
- pre/post artifact SHA-256 與 exact source locators；
- pre/post 完整條文候選文字及 normalized hash；
- 關聯日期註記 occurrence IDs；
- 候選公告 IDs（若有），以及 bounded notice-search ledger；
- parser／normalizer／work-contract versions。

既有 3,080 列 v1 queue 保留為不可變 discovery artifact，但標為
`superseded_for_execution`。下一個 agent 不得把其中的
`official_event_identity` 必要欄位當完成 gate；它必須先轉成 v3 edge work
units。

## 可接受的 transition evidence basis

每個 verified transition 至少要有一種直接官方 evidence basis：

| basis | 最低要求 |
|---|---|
| `official_notice_effect` | 公告 detail／附件直接指向該條變更與日期 |
| `official_old_new_comparison` | 官方對照表具完整可定位 old/new sides，省略處不得補猜 |
| `official_cumulative_versions` | 兩個可定序的官方 cumulative snapshots 各有完整條文與 exact locator |
| `official_cumulative_annotation` | 官方條文內的日期註記能證明日期角色，且前後全文另由官方版本支持 |
| `official_archival_snapshot` | 可驗證的官方 archival bytes、版本／as-of 與完整條文 locator |

`official_cumulative_versions` 可證明「兩個官方版本之間文字改變」，但只有在
declared source set 中已證明 direct adjacency，才能把它稱為直接 transition。
若兩 anchor 間仍可能存在未取得的中間版本，結果必須是
`source_gap`，不能把年度 diff 當成一次公告修正。

## Notice linkage 是獨立指標

`notice_linkage_status` 的封閉集合：

- `linked_notice`
- `notice_not_found_after_bounded_search`
- `notice_availability_unknown`
- `notice_not_searched`
- `notice_not_applicable`
- `notice_candidates_ambiguous`

`notice_not_found_after_bounded_search` 必須附完整 search ledger：官方
endpoint、query、同義詞、partition、執行時間、結果數與 locator。這個狀態
只陳述該次限定搜尋的結果，不陳述歷史公告是否曾存在。

公開 coverage 必須分開報：

```text
transition_evidence_coverage
notice_linkage_coverage
source_version_coverage
annotation_terminal_coverage
anchor_replay_coverage
```

不得再以 `0/6,360 連到確定公告事件` 作為完整度的主要分母。正確說法是：
6,360 個有效日期 marker 已抽取；它們在 v3 evidence-basis 契約下尚待日期
角色與 transition adjudication。公告連結率另計。

## Source selection：給付規定 RSS 優先

條文歷史與藥品關聯不需要各自追一套公告宇宙。當給付規定 RSS 捕捉到的同一
官方 bundle 已明列成分／商品與給付條號時，該 bundle 可以同時產生：

- `transition_evidence`：條文修訂前後、日期角色與 scope；
- `drug_rule_link_evidence`：成分／特定商品／強度指向哪些 source
  designations。

此時不必為了證明相同關係，再強制追查一份獨立「藥品公告」。只有以下 gap
才啟動補充來源搜尋：

- RSS bundle 沒有產品、成分或條號的 exact relation；
- 要解析健保品項代碼、ATC、價格有效期或特定強度，而附件不足；
- RSS、IODE、INAE3000、現行條文或其他官方來源互相矛盾；
- 條文生效日、完整 old/new sides 或適用範圍仍不明。

藥品品項／ATC 的主重建來源仍是 IODE snapshot，INAE3000 作 current
freshness。公告中的品牌或成分只證明該 exact source assertion；不能自動
擴張成所有同成分商品、所有強度或整個 ATC class。

具體 canary：
`gov_健保審字第1150055452號` 的 RSS/detail bundle 明列
aumolertinib（Pulmivex）及 gefitinib、erlotinib、afatinib、
osimertinib、dacomitinib，並直接指向 9.138、9.24、9.29、9.45、9.80、
9.83；ODT 對照表標示自 2026-08-01 生效。因此它已足以作這六組
ingredient/product→source-designation linkage evidence，不需另追一份藥品
公告來重複證明相同關係。品項代碼、ATC、價格／支付期間仍須由 ODS／
IODE／INAE3000 evidence 補齊。

## 單一 agent work unit 的標準流程

### 0. 驗證輸入，禁止自行擴張權限

Agent 先重算所有 artifact、row 與 packet hashes。任何 mismatch 都以
`input_tamper_or_drift` 終結。Agent 只能產生 candidate result；不得直接寫
canonical history、改 current、發布或宣稱完成。

### 1. 重建完整前後版

從 source blocks 依條文階層邊界重建完整 pre/post text。保留表格、清單、
段落順序與 exact locators。OCR 或缺損表格只能進
`format_review_required`，不得補寫看似合理的文字。

### 2. 判讀 stable identity

判斷是否為同一條文的 amend/correction/rename/move，或 create/delete/
restore/split/merge/number reuse。只靠同條號不夠。無法唯一判斷時輸出
`identity_ambiguous` 與 competing candidates。

### 3. 證明日期角色

分開保存發文、刊登、生效、失效、as-of 與 fetch dates。accepted
`effective_from` 必須連到一個直接官方 locator 與
`effective_date_basis`。資料庫時間、檔名日期、最大 marker date 或公告
日期都不能代替生效日。

### 4. 證明 direct adjacency

列出 declared source set 中該 identity 的所有可定序版本，確認 pre/post
之間沒有未裁決的 observed version。若 source set 本身有缺件或中間 anchor
不明，輸出 `source_gap`；不得把兩份相隔多年的 cumulative snapshots
直接相連。

### 5. 產生只比較直接前版的 diff

先做結構 mapping，再做文字 diff。新版標新增、舊版標下一版刪除。來源
mapping 不唯一的 hunk 標 `ambiguous`，不顯示精確增刪色塊。完整最新版全文
獨立保存；歷史頁預設只顯示相鄰 diff。

### 6. bounded notice search

若 packet 尚無公告，可做明列範圍的官方來源搜尋並保存 search ledger。
找到候選後仍要讀 exact detail／附件並驗證 scope；找不到則使用
`notice_not_found_after_bounded_search`，不能阻擋已由其他官方 evidence
basis 完成的 transition。

### 7. anchor replay

將 accepted transition 套到 pre snapshot，結果必須等於 post snapshot；
再於下一個 cumulative anchor 比對 rule set 與逐條 hash。任何 mismatch
回到 source/identity/date gate，不能由 agent 改字湊平。

### 8. 輸出 terminal candidate

每個 work unit 只能以以下之一終結：

- `transition_verified_candidate`
- `no_text_change`
- `non_amendment_annotation`
- `identity_ambiguous`
- `date_role_ambiguous`
- `source_gap`
- `format_review_required`
- `notice_candidates_ambiguous`
- `input_tamper_or_drift`

`transition_verified_candidate` 仍須 deterministic validator 與獨立 reviewer
通過，才可 promotion。

## Reviewer 與 promotion gate

先做 10 個具代表性的 pilot，至少涵蓋 amend、create/delete、move/rename、
split/merge、同日多版本、表格與公告找不到但 cumulative evidence 足夠的
案例。Pilot 未通過前不做 3,080 列量產。

每個 candidate 的 reviewer 不得沿用 proposer 的未驗證推論。Reviewer
必須重看：

1. exact source spans 與 full-side reconstruction；
2. identity operation；
3. effective-date basis；
4. adjacency；
5. diff mapping；
6. replay；
7. notice status wording。

Promotion 的 hard gates 是 evidence、identity、adjacency、replay 與
deterministic contract；`linked_notice` 不是 hard gate。任何 agent、模型或
fallback 的同意都不是第二份官方證據。

## 建議的資料庫結構

```text
source_artifact
source_snapshot
source_date_annotation
rule_identity
rule_designation
rule_version
rule_transition
transition_evidence
official_notice
transition_notice_link
comparison_edge
diff_hunk
history_coverage
notice_search_observation
drug_rule_link_evidence
```

`rule_transition` 不應有 mandatory `official_event_id`。官方公告以 nullable
many-to-many `transition_notice_link` 連結；真正必要的是至少一筆 accepted
`transition_evidence`。`transition_evidence.basis` 使用上表封閉 vocabulary，
並保存 artifact、locator、date role、side、SHA-256 與 review state。

PostgreSQL 是 canonical store；公開 JSONL 與 SQLite 由同一批 typed rows
單向生成。SQLite schema 不得把 nullable notice link 重新變成 mandatory
foreign key。

## 對下一個 agent 的分期工作

1. **M1 契約修正**：新增 v3 schema/migration 草案；把 mandatory event FK
   改為 accepted transition evidence，保留舊 stage 不覆寫。
2. **M2 Queue v3**：將 3,080 個 discovery pairs 轉成 direct-edge candidates，
   對缺少 pre/post sides 的列明確產生 `source_gap`。
3. **M3 Pilot 10**：人工可審的完整 packets、candidate results、review
   receipts 與 replay。
4. **M4 Scale**：pilot 通過後才分批處理；每 25 units 做 drift/repetition/
   unresolved-reason audit。
5. **M5 Release**：PG→JSONL→SQLite parity、讀者頁 diff、coverage metrics 與
   scoped claim。

## 暫停與重啟條件

2026-07-28 起，clause-history agent dispatch 依 Copper 指示暫停。deterministic
raw acquisition 可保留，但不得呼叫 Claude 或其他模型整理條文。重新啟動
agent dispatch 必須同時具備：

- Copper 明確指示恢復；
- M1/M2 契約與 validator 已完成；
- 10-unit pilot packet 已凍結；
- runtime 明示 `NHI_RULE_HISTORY_AGENT_DISPATCH_ENABLED=true`；
- candidate-only／no canonical write 保護仍生效。
