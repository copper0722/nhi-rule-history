# GPT Pro methodology v4 audit response

Captured unchanged from the persistent GPT Pro Agentic Workflow conversation on
2026-07-29.

---

DISPOSITION: REPAIR_THEN_ACCEPT

核心方向可接受：官方整編檔作為狀態觀察、公告與附件作為轉換證據、單一條文作為歷史重建的最小內容單位、所有不確定性顯式保留。但目前仍有四個會造成法律語意過度宣稱的核心缺口：

相鄰來源觀察被過早視為相鄰法律版本。

來源出版／觀察日期被用來界定法律生效區間。

穩定條文身分在 create／restore／delete／amend 分類之前尚未成立。

相同文字狀態與同一法律版本期間尚未充分分離。

修正後可進入實作；未修正前，不應建立對外稱為「條文版本鏈」的 canonical reader model。

C/H/M/L = 4/6/4/2

## Critical findings

### C1 — 相鄰 edition observations 不等於相鄰法律版本

年度整編檔只能證明：

在觀察點 O1，來源包含文字 A

在觀察點 O2，來源包含文字 B

不能單憑此證明：

A 的直接 successor 是 B

A 與 B 中間沒有其他法律文字狀態

可能存在：

A → X → Y → B

甚至：

A → X → A → B

而年度檔只留下 A、B。

必要修正

將目前的 adjacent-edition comparison 改名為：

observation_delta

不得直接建立：

predecessor_version_id

successor_version_id

而應先使用：

predecessor_status =

direct_verified

| observation_adjacent_only

| ambiguous

| unknown

只有直接官方 old/new mapping、完整 sides、身分連續性及事件範圍均驗證後，才能建立：

direct_predecessor_verified

公開讀者在未驗證 directness 時只能寫：

與前一個可取得的官方來源觀察相比。

不得寫：

與上一版相比。

### C2 — 來源觀察日期與法律生效時間必須完全分軸

目前提案：

after the prior observation and no later than the next observation

只有在兩個邊界都是明確、可信且同範圍的法律 as-of 狀態時，才可能形成法律時間界限。

若日期只是：

年度檔標示日期；

出版日期；

網頁更新日；

文件上傳日；

資料取得日；

PostgreSQL 載入日；

則最多只能形成：

source_appearance_window

不能形成：

legal_effective_window

必須分開保存

source_edition_label

source_publication_date

source_asserted_as_of_date

source_observed_at

legal_effective_date

legal_effective_window

其中：

source_observed_at 永遠不是法律時間證據；

source_publication_date 不自動代表生效日；

source_asserted_as_of_date 只有在來源明文宣告其 as-of 語意時才可使用；

legal_effective_date 只能來自具有明確 date role、scope 與 locator 的正式證據。

時間狀態 vocabulary

effective_time_status =

exact_effective_date_verified

| bounded_by_verified_as_of_states

| source_appearance_window_only

| effective_time_unknown

| effective_time_conflicted

| relative_date_unresolved

| future_effect_verified_scheduled

只有 bounded_by_verified_as_of_states 才可表達：

(prior_as_of_date, next_as_of_date]

若只是相鄰年度檔，應保存：

earlier_observation_id

later_observation_id

而不是假的日期區間。

### C3 — create_or_restore、delete、amend 均過早假定 stable identity

同一 designation 並不證明同一法律物件；designation 可能：

被移動；

被改號；

被刪除後重用；

被拆分；

被合併；

保留號碼但完全替換主題；

在不同章節重新出現。

因此下列名稱仍過度權威：

create_or_restore_candidate

delete_candidate

amend_candidate

先使用中性 observation vocabulary

appearance_observed

text_change_observed

disappearance_observed

designation_change_observed

structure_change_observed

只有經 identity adjudication 後，才可升格為：

create_verified

restore_verified

amend_verified

delete_verified

move_verified

renumber_verified

split_verified

merge_verified

replacement_verified

number_reuse_verified

Stable identity 的最低不變量

不得只靠 designation 或文字相似度。至少需要一項強證據及完整反例檢查：

官方 old/new 表明確把兩段文字列為同一條規定的修正前後；

官方明文指出改號、移列、拆分、合併或恢復；

同一正式公告中的 event scope 明確連接兩個 designation；

parent chapter、heading、cross-reference 與完整文字 mapping 均一致；

沒有 competing move／split／merge／number-reuse candidate。

文字相似度與 agent 判讀只能產生 review candidate，不能形成 identity。

### C4 — 相同文字內容不等於同一法律版本期間

相同文字可以重複出現：

A → B → A

內容 hash 相同，但前後兩次 A 是不同的法律狀態 episode。

因此不能把所有相同文字合併成一個 canonical version。

必須拆成三層

clause_text_state

= 內容身分；可 content-addressed 重用

clause_state_observation

= 某份來源在某個觀察點出現該文字

clause_state_episode

= 經 adjudication 後，該文字作為某條文法律狀態的一次期間

同一 clause_text_state 可以被多個 clause_state_episode 引用。

建議將「canonical version unit」精確改為：

Canonical legal-version unit 是一個 stable clause identity 的一次 adjudicated state episode；文字內容另以 clause_text_state 去重。

## High findings

### H1 — 33 個 current whole／chapter 差異不能被「owner-selected canon」隱藏

Chapter page 可作專案選定的 reader source，但這只是來源優先政策，不是官方已裁決兩份來源的法律優先順序。

必須保存：

selected_current_source = chapter

selection_policy_id

whole_source_observation_id

chapter_source_observation_id

source_conflict_status

conflict_digest

狀態至少分為：

current_text_status =

chapter_selected_no_known_conflict

| chapter_selected_with_whole_conflict

| current_source_conflict_unresolved

| current_text_not_observed

對 33 個衝突 designation，不得無警示顯示「目前有效條文」。

允許文字：

本頁顯示目前官方章節檔所載文字；同一 capture cut 的整份檔存在差異，尚未由本資料庫裁決為法律優先來源。

### H2 — Notice／old-new table 不會自動證明「精確 transition」

完整 old/new sides 加上官方 locator，仍須分別驗證：

event identity

event scope

clause identity continuity

date role

effective scope

complete-side fidelity

direct predecessor status

correction/supersession status

例如公告日期明確，但沒有生效日，最多只能建立：

official_event_verified

text_transition_supported

effective_time_unknown

不能建立：

exact_effective_transition

建議將一個 transition 分解為三個獨立狀態：

text_transition_status

identity_transition_status

effective_time_status

不要用單一 transition_confidence。

### H3 — Exact text identity 不得使用 normalization hash

必須同時保存：

exact_source_text

exact_source_text_sha256

comparison_text

normalization_profile_version

comparison_text_sha256

clause_text_state 的內容身分應以 exact source text 為準。

Normalization 只能用於：

搜尋；

diff；

whitespace-only 分類；

review routing。

不得因 normalized text 相同，就把下列差異消失：

標點；

數字；

劑量；

比較符號；

table structure；

list numbering；

footnote markers；

全形／半形在藥碼或公式中的差異。

若確定只是格式差異，應另建立：

presentation_equivalence_decision

而不是改寫 exact text identity。

### H4 — OCR／table reconstruction 不能偽裝成 exact source text

Agentic proofread、OCR 或 old/new table reconstruction 可能是必要的，但必須保存 fidelity：

text_fidelity =

native_exact

| deterministic_extraction

| reviewed_reconstruction

| partial_reconstruction

| unavailable

reviewed_reconstruction 可成為經審查的 candidate text，但不得標成 native_exact。

每個 reconstructed span 至少應有：

source_artifact_sha256

page/cell/region locator

raw extracted text

reconstructed text

producer proposal

independent authorization

reconstruction receipt

若 old 或 new 任一 side 仍為 partial，不得升格為完整 amendment。

### H5 — 單條文可作 version unit，但不能成為唯一證據範圍

給付規定的法律意義可能依賴：

章節前言；

共通 eligibility 條件；

table header；

footnote；

跨條文 reference；

同一公告內其他條文；

多條文共同生效條款。

因此需另保留：

source_context_group

official_event

event_scope

shared_condition

cross_clause_dependency

一個公告可連到多個 clause transition，但不得把它複製成彼此無關的多個 event。

單條文 reader 應能回看：

本條文字所屬公告同時影響其他 N 條規定，並含共通條件。

### H6 — Replay parity 不是完整性的證明

即使 reconstructed event chain 與年度/current anchor 完全相符，仍可能漏掉：

A → B → A

因為最終 anchor 仍為 A。

因此：

replay_parity = exact_match

只能宣稱：

已輸入的事件重播後與該觀察點文字相符。

不能宣稱：

該觀察區間的事件完整。

replay_parity 與 source_search_coverage 必須分開。

## Medium findings

### M1 — Inline ROC dates 只能是 date evidence candidates

6,360 個日期 marker 是高價值索引，但不得直接成為：

amendment_date

effective_date

每筆應保存：

raw_date_expression

calendar_system

parsed_date_candidate

date_role_candidate

scope_candidate

source_locator

Date role 至少要區分：

effective_date

publication_date

review_date

expiry_date

clinical_eligibility_date

historical_reference_date

unknown

### M2 — 法律時間排序應為 partial order，不是強制 total order

同一天可能有多份公告、勘誤或不同 scope 的事件。

若來源沒有明示先後或 supersession，應允許：

same_effective_date_unordered

不得以：

document number lexicographic order；

listing row order；

fetch order；

database insert order；

決定法律 predecessor。

### M3 — Notice linkage 可不綁特定 notice table，但 evidence FK 不可缺

「Notice linkage 不是 mandatory foreign key」方向可以保留，因為部分 transition 可能來自 gazette、整編檔或其他正式來源。

但任何 upgraded claim 必須至少有 generic evidence relation：

claim_evidence_link(

claim_id,

source_artifact_id,

locator_id,

evidence_role,

exact_span_sha256

)

不能讓 exact transition 只靠自由文字 provenance。

### M4 — Timeline UI 本身可能製造不存在的時間精度

即使文字警語正確，等距日期軸、連續實線或「上一版／下一版」仍會讓讀者推論：

中間沒有缺失版本；

變更持續至下一日期；

相鄰 entry 是直接 successor。

Interval／observation-only entries 不應使用連續實線時間條。應以：

來源觀察點 A

⋯ 中間變更可能未完整取得

來源觀察點 B

表示。

## Low findings

### L1 — Git-like 只適合作為內部工程比喻

公開資料契約和 reader 不應使用：

commit

parent commit

branch

merge commit

因為 Git commit 是已知完整物件與明確 parent，而本資料的中間法律變更可能缺失。

建議公開名稱使用：

state observation

observation delta

evidence-supported transition

### L2 — 不使用單一 confidence score

「歷史信心 82%」會掩蓋不同缺口。

應採 categorical vector、counts 與 explicit gaps，不產生總分。

## Minimal required repairs

在 REPAIR_THEN_ACCEPT 轉為 ACCEPT 前，最低限度完成以下八項。

### R1 — 拆分 observation、text state、episode、transition

至少建立：

source_edition_observation

source_segment_observation

clause_identity

clause_text_state

clause_state_observation

clause_state_episode

observation_delta

transition_evidence

adjudicated_transition

其中 observation 不自動產生 episode 或 predecessor edge。

### R2 — 建立三軸時間模型

明確區分：

source publication/edition

source asserted-as-of

legal effective time

禁止由 publication、capture 或 next observation 自動產生 legal effective_from/effective_to。

### R3 — 中性化 transition candidate

將：

create_or_restore_candidate

amend_candidate

delete_candidate

改為：

appearance_observed

text_change_observed

disappearance_observed

法律事件類型只在 identity、scope 與 event evidence 通過後升格。

### R4 — 建立 identity／lineage graph

支援：

continues_as

moves_to

renumbered_to

splits_into

merged_from

reuses_designation

identity_unresolved

且允許 one-to-many、many-to-one，不強迫一條線性鏈。

### R5 — 將 current source selection 變成可稽核 policy

33 個 whole/chapter 差異不得被 canonicalization 隱藏。所有 reader/export 必須帶：

current_text_status

selection_policy_id

source_conflict_status

### R6 — Exact text、normalized text、reconstruction fidelity 分離

不得以 normalized hash 作 canonical text-state identity；OCR／agent reconstruction 必須有獨立 fidelity 與 authorization receipt。

### R7 — 實作 completeness vector 與 reader wording validator

不得只在 prose 中提醒。JSONL、SQLite 與 reader projection 都要含相同狀態欄位，並用測試拒絕：

interval 被標成 exact；

observation adjacency 被標成 predecessor；

absence 被標成 deletion；

bounded miss 被標成 no notice exists。

### R8 — 所有 bounded search 產生 machine-readable receipt

至少包括：

search_protocol_version

source_surface

query_terms

historical_name_variant

date_interval

pagination/partition plan

declared result count

retrieved result count

result cap

capture cut

errors

status

receipt_sha256

例如：

1996–1998 只用後期名稱的零結果不得作 absence evidence；

Taiwan Historica 10,000-result cap 必須標 capped_unclosed；

empty-keyword FINT lane 必須標 partial_stopped；

NHI listing 必須標 retention_limited_unclosed。

## Proposed normalized entity vocabulary

| Entity | 精確語意 |
|---|---|
| source_artifact | 官方原始 bytes；content-addressed、immutable |
| source_edition_observation | 某一完整或部分官方整編／章節來源的觀察點 |
| source_segment_observation | 來源內一個 clause-like 結構片段，不主張 stable identity |
| clause_identity | 經 adjudication 的跨來源條文法律物件 |
| identity_assertion | 某 source segment 與 clause identity 的 candidate／verified 關係 |
| designation_assignment | 某 identity 在特定 observation／法律期間使用的條號 |
| clause_text_state | 一份 exact text content state；可被不同 episode 重用 |
| clause_state_observation | 某來源觀察到 identity／segment 使用某 text state |
| clause_state_episode | 經 adjudication 的一次法律狀態期間 |
| observation_delta | 兩個可取得來源觀察之差異，不主張 direct transition |
| transition_evidence | 公告、old/new 表、gazette、明文日期等直接證據 |
| adjudicated_transition | 已確認 event kind、scope、identity relationship 的轉換 |
| effective_time_assertion | exact／bounded／unknown 的法律時間主張及證據 |
| lineage_relation | move／renumber／split／merge／reuse 等身分關係 |
| source_search_run | 一次有明確範圍的來源搜尋 |
| coverage_assessment | 每條文、每時間區間的多維 coverage 狀態 |
| replay_check | 事件重播結果與某來源 observation 的 parity 比較 |

## Proposed normalized status vocabulary

### Observation presence

present_exact

absent_in_declared_complete_scope

partial_or_unreadable

source_conflict

out_of_scope

not_observed

absent_in_declared_complete_scope 不等於 deleted。

### Text fidelity

native_exact

deterministic_extraction

reviewed_reconstruction

partial_reconstruction

unavailable

### Identity

same_identity_verified

distinct_identity_verified

same_identity_candidate

identity_unresolved

split_merge_review

number_reuse_review

### Observation delta

appearance_observed

text_change_observed

disappearance_observed

designation_change_observed

structure_change_observed

### Adjudicated transition

create_verified

restore_verified

amend_verified

delete_verified

move_verified

renumber_verified

split_verified

merge_verified

replacement_verified

number_reuse_verified

### Predecessor

direct_predecessor_verified

observation_adjacent_only

predecessor_ambiguous

predecessor_unknown

### Effective time

exact_effective_date_verified

bounded_by_verified_as_of_states

source_appearance_window_only

effective_time_unknown

effective_time_conflicted

relative_date_unresolved

future_effect_verified_scheduled

### Current text

chapter_selected_no_known_conflict

chapter_selected_with_whole_conflict

current_source_conflict_unresolved

current_text_not_observed

### Search coverage

bounded_search_complete

bounded_search_partial

bounded_not_found

capped_unclosed

source_unavailable

not_searched

## Distinguishing transition types

### Create versus restore

create_verified

至少需要：

官方明文「新增／增訂／訂定」；

event scope 指向該 clause identity；

沒有可支持 prior same identity 的 evidence。

restore_verified

至少需要：

prior same stable identity 已存在；

中間有 verified deletion／cessation episode；

官方明文恢復，或有明確 identity continuity evidence；

已排除 move／renumber／number reuse。

否則一律：

appearance_observed

不得在 create／restore 之間猜測。

### Delete versus move／renumber

absent_in_declared_complete_scope 只證明下一 observation 未見。

要升格 delete_verified，至少需：

官方明文刪除／停止適用／廢止；或

經 adjudication 證明沒有 successor designation、move、merge 或 renumber；

event scope 完整。

如果原條號消失，但相同或相近文字出現在別處：

move_or_renumber_candidate

不得先標 delete。

### Amend versus delete＋create

amend_verified 至少需要：

同一 stable identity；

完整 old/new sides；

直接官方 mapping；

沒有 scope break 或 identity replacement evidence。

同號但主題完全替換，且無明確 continuity，應標：

identity_discontinuity_candidate

文字相似度只能協助排序，不得裁決。

## Per-clause completeness vector

不得產生總分。建議每條文輸出：

```json
{
  "current_text": {
    "status": "chapter_selected_with_whole_conflict",
    "source_cut_id": "…",
    "selected_artifact_sha256": "…",
    "conflicting_artifact_sha256": "…"
  },
  "observed_state_coverage": {
    "present_observation_cuts": ["…"],
    "absent_in_complete_scope_cuts": ["…"],
    "unresolved_cuts": ["…"],
    "out_of_scope_cuts": ["…"]
  },
  "exact_time_coverage": {
    "exact_effective_transitions": 2,
    "bounded_as_of_transitions": 1,
    "source_appearance_only_transitions": 3,
    "unknown_time_transitions": 2
  },
  "identity_continuity": {
    "status": "mixed",
    "verified_links": 3,
    "unresolved_links": 2,
    "split_merge_or_reuse_reviews": 1
  },
  "predecessor_certainty": {
    "direct_verified_edges": 2,
    "observation_adjacent_only_edges": 4,
    "ambiguous_edges": 1
  },
  "deletion_coverage": {
    "direct_delete_evidence": 0,
    "absence_only_observations": 1,
    "move_or_renumber_unresolved": 1
  },
  "notice_linkage": {
    "linked_transitions": 2,
    "bounded_not_found": 1,
    "not_searched": 3
  },
  "source_search_coverage": [
    {
      "lane": "mohw_fint_exact_historical_name",
      "interval": "1995-01-01/1998-12-31",
      "status": "bounded_search_partial",
      "receipt_sha256": "…"
    }
  ],
  "replay_parity": {
    "status": "exact_match",
    "anchor_observation_id": "…",
    "does_not_prove_event_completeness": true
  }
}
```

八個必要維度

current_text

observed_state_coverage

exact_time_coverage

predecessor_certainty

deletion_coverage

notice_linkage

source_search_coverage

replay_parity

另建議增加第九個：

identity_continuity

因為沒有它，其餘七個歷史維度可能被錯誤串到同一條文。

## Should interval candidates appear in the public reader?

結論：可以，但必須作為「來源觀察」，不能作為法律版本

公開條件：

兩端都是可驗證的完整來源 observation；

source scope 相容；

文字可完整讀取；

source order 可確定；

沒有未揭露的 whole/chapter conflict；

entry 使用中性 transition label；

不產生假的 effective_from／effective_to；

明示中間可能有未取得變更。

若任一端是 partial OCR、source conflict 或 scope 不相容，只能顯示 evidence issue，不顯示 interval transition。

## Reader-facing wording contract

### A. Exact effective transition

適用條件：

effective_time_status = exact_effective_date_verified

identity = verified

event scope = verified

text sides = complete

固定格式：

官方明載生效日：2018 年 4 月 1 日。

官方公告及附件明確指出本條文字自該日生效。

可使用：

生效；

修正；

新增；

刪除；

恢復。

但只能使用已 adjudicated 的 event kind。

### B. Verified as-of bounded legal window

只在來源明確宣告兩個 as-of state 時使用：

生效日尚未精確確認。

依兩個具有明確 as-of 語意的完整官方狀態，可確認此文字在 2017 年 12 月 31 日之後、2018 年 12 月 31 日以前已成為適用文字。

不得將 upper bound 顯示成 exact effective date。

### C. Source appearance interval only

年度 edition 的預設用語：

實際生效日未知。

2017 年版仍顯示前一文字，2018 年版已出現此文字。這是兩個官方來源觀察點之間的差異，不是法定生效期間；中間可能存在尚未取得的變更。

標籤：

來源觀察區間

不得標：

生效區間

### D. Exact event date but predecessor uncertain

官方生效日已核實，但版本相鄰關係尚未確認。

目前尚不能證明此文字直接承接頁面上的前一個可見文字狀態。

### E. Absence without deletion proof

下一個完整來源觀察中未見此條號。

目前尚不能判定它是被刪除、改號、移動、合併，或由其他條文取代。

不得使用刪除線加「已刪除」標籤。

### F. Bounded notice miss

在已列明的搜尋範圍內未找到對應公告。

此結果只描述本次查詢的來源、關鍵字、日期與分頁範圍，不代表公告不存在。

### G. Current whole／chapter conflict

現行官方來源不一致。

本頁依既定來源政策顯示章節檔文字；同一 capture cut 的整份檔存在差異。此選擇不表示官方已裁決兩份來源的優先順序。

## Exact terminology prohibited in uncertain entries

除非對應狀態已驗證，不得使用：

上一版

下一版

直接前身

修正於

生效於

刪除於

恢復於

完整沿革

全部公告

完整歷史

目前有效條文

替代詞：

前一個可取得來源觀察

後一個可取得來源觀察

文字出現差異

來源觀察區間

實際生效日未知

身分關係待裁決

## Residual methodological risks even if all deterministic checks pass

即使所有 hash、parser、replay、schema、JSONL／SQLite parity 都通過，以下情況仍可能使 public repo 誤導：

Digital-survival bias

可程式取得的來源不等於歷史來源全集。1995／1996 原文未取得、NHI listing 有 retention／expiry、Taiwan Historica 有 10,000-result cap，均不能靠 deterministic correctness 消除。

Owner-selected current source policy

Chapter file priority 是專案政策，不是法律來源優先順位的官方裁決。

Transient events invisible to anchors

多次變更後回到原文字，年度 replay 仍可完全匹配。

Clause segmentation bias

目前 639-clause segmentation 不一定能無損套用於早期規範；歷史 source segment 必須能在尚無 clause identity 時獨立存在。

Cross-clause legal context loss

單條文頁面可能遮蔽共同前提、表頭、footnote 或同公告其他修正。

Reviewed reconstruction remains reconstruction

獨立 reviewer authorization 降低錯誤，但不能把 OCR／table reconstruction 變成原生 exact source。

Repository naming and README claims

即使資料欄位誠實，repository 名稱、首頁標題或範例若寫「完整歷史庫」，仍會讓使用者忽略 coverage vector。

因此 repo 的允許主張應限於：

本專案建立條文層級的官方來源觀察、文字狀態與部分證據支持的歷史轉換；法律身分、時間、前身關係與來源搜尋完整度均按條文和期間分別標示。

不得宣稱：

已重建完整健保藥品給付規定法律版本歷史。

## Final methodology gate

OBSERVATION_MODEL = ACCEPT_AFTER_REPAIR

CLAUSE_LEVEL_CONTENT_UNIT = ACCEPT

CLAUSE_TEXT_STATE_DEDUP = ACCEPT_IF_EPISODES_REMAIN_DISTINCT

LEGAL_VERSION_ADJACENCY = BLOCKED_UNLESS_DIRECTLY_VERIFIED

OBSERVATION_DATE_AS_EFFECTIVE_DATE = FORBIDDEN

INTERVAL_CANDIDATES_IN_READER = ALLOWED_WITH_SOURCE_OBSERVATION_WORDING

CURRENT_CHAPTER_PUBLICATION = ALLOWED_WITH_SOURCE_POLICY_AND_CONFLICT_STATUS

COMPLETE_HISTORY_CLAIM = FORBIDDEN

最關鍵的修正不是增加更多來源，而是讓資料模型能清楚表達：我們知道某份文字曾出現，但仍不知道它是否是直接法律版本、何時生效、是否曾有中間狀態，以及它是否延續同一條文身分。
