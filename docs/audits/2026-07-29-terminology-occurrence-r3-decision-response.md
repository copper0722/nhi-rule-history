# GPT Pro response — terminology / alias / occurrence R3 decision

1. GO / REPAIR / STOP
DECISION = REPAIR

模型方向正確（normalized concept + alias + occurrence + external code + run 封裝），但在 run immutability、offset 定義、concept identity、scan coverage、activation 機制上仍有 R3 級別缺口，必須修補後才能進 migration。

2. 五表設計是否正確
CORE_MODEL = CORRECT_BUT_INCOMPLETE

五個 domain table 正確，但至少需補三個結構性元件：

(A) tagging_run_block_input（必需）

確保 13,874 blocks「被掃描過」而非只看有 match 的 rows

(tagging_run_id, source_block_id, block_sha256, clause_code, scan_status)

否則無法證明 full scan 完整性。

(B) concept_seed_tag_link（必需）

確保 82/82 conservation 可驗證

(concept_id, legacy_tag_id, source_run_id, mapping_status)

否則「來源 tag → concept」只是語意承諾，非資料約束。

(C) tagging_run_activation（關鍵）

避免修改 sealed run

(tagging_run_id, activated_at, superseded_by)

❗ 禁止在 tagging_run 上 UPDATE active flag

3. v1 alias admission policy
CHOICE = A (source-observed only)

理由：

B/C 仍依賴 model semantic correctness（不可審計）

“collision-free” ≠ “語意唯一”

corpus occurrence ≠ correct meaning

補強 admission 條件（必要）
alias_admitted 必須同時：
- exact source span（有 offset + hash）
- 來自 reviewed seed（82 tags lineage）
- 無 cross-concept collision
- 非 context_required
- span 不屬於 lexical ambiguity（短詞/縮寫需 allowlist）
4. longest-match / no-overlap
RULE = ACCEPT_WITH_STRICT_EXCEPTION
原則

longest match → 正確（避免 “statin” 吃掉 “atorvastatin”）

no-overlap → 正確（reader 可讀性）

唯一例外（但 v1 不啟用）
same-span multi-concept = BLOCKED

即使：

class + ingredient

disease + synonym

👉 v1 一律 fail closed

未來需人工建立：

alias_sense / precedence
5. candidate occurrence 存放
CHOICE = SAME_TABLE + VIEW
clause_occurrence (candidate|admitted|blocked)

view：

admitted_clause_occurrence

理由：

保留 full scan trace（audit 必須）

overlap / conflict 可回溯

避免雙表 drift

6. 最低上線前檢查（release gates）
(A) Deterministic scan
- 13,874 / 13,874 blocks scanned
- 639 clauses covered
- rerun → byte-identical result
(B) Offset correctness（Critical）
raw_text[start:end] == matched_text

且必須定義：

offset_unit = unicode_scalar + utf8_byte

（JS UTF-16 不可直接使用）

(C) Collision / overlap
- 8 known collisions → all blocked
- no overlapping admitted spans
- exclusion constraint (range)
(D) External code integrity
- ATC / ICD / NHI code resolve
- 無 private ICD metadata export
(E) Sealed-run immutability
INSERT/UPDATE/DELETE/TRUNCATE = 全拒
(F) Rollback
- active run pointer 切換
- 不重算舊 run
(G) API post-release checks
- reader 不重新推論
- offsets 與 text 一致
- semantic_tags 不變
- 新欄位僅增加
7. API 相容策略
KEEP semantic_tags (frozen)
ADD terminology_occurrences_v1

不由新系統回寫 semantic_tags

明確 versioned contract

run_id + fingerprint 必須回傳

8. ICD-11 邊界
CONFIRMED (需強化)

公開僅允許：

system = ICD11
code
relation_type

禁止：

title
URI
definition
任何 private snapshot

concept label 必須：

≠ 直接複製 ICD title
結論（最關鍵風險）

這個設計已達到：

GOOD:
- deterministic scan
- provenance preserved
- reader 不依賴 model

但仍有三個會破壞「可審計性」的關鍵缺口：

1. run activation 破壞 immutable（未分離）
2. concept identity 無全域 registry
3. scan denominator（13,874 blocks）未入 DB

修補後即可進入實作。
