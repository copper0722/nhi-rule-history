# 「完整歷史」完成契約

只有以下 gate 全部通過，才能宣稱「健保藥品給付條文完整歷史已入庫」。

## Source

- 宣告期間的歷史／現行 whole、chapter、listing、detail、attachment 全部進
  manifest。
- expected pages／rows 與 fetched unique rows 相符。
- 每個成功 artifact 有 URL、SHA-256、byte size、MIME 與 locator。
- 403、0 rows、selector miss 為 failed run。

## Text

- publishable snapshot 非空、零截斷。
- 段落、表格、附表順序可回到官方來源。
- 主解析格式與其他官方格式無未裁決重大差異。

## Date

- 發文、刊登、生效、失效、as-of、fetch time 分欄。
- 每個 accepted 生效日有官方 locator。
- 不使用資料庫時間或 filename date 代替法律日期。
- 每個 source-local slash-triplet observation 都有 terminal classification：
  valid amendment-date candidate 必須連到一個具官方 locator 的
  transition evidence basis，或明列 unresolved gap；劑量等非日期
  observation 必須有 exact-context `rejected_non_amendment` evidence。
- 公告連結是獨立 coverage metric，不是 transition completion gate。公告在
  bounded official search 中未找到，只能標
  `notice_not_found_after_bounded_search`／`notice_availability_unknown`，
  不得推論公告不存在。

## Identity

- 永久 rule ID 與 versioned designation 分離。
- publishable cohort unresolved identity conflict 為 0。
- number reuse、split、merge、move、restore、correction 有 evidence-backed
  decision；lineage 無 cycle。

## Replay

- 從 cumulative baseline 重播 accepted transitions，到每個下一個 official
  anchor 時，rule set 與全文 hash 一致。
- 最終 replay 對上當次現行 whole/chapter。
- 同一 input manifest 與程式版本重算 fingerprint 相同。

## Diff

- published version 最多一個 direct predecessor。
- 非 ambiguous hunk 的 source mapping 為 100%。
- 新版顯示新增；舊版顯示下一版刪除。
- 無法可靠比較時顯示 ambiguity，不製造 diff。

## Linkage and portability

- 每個 drug／ATC link 有 source、version、relation、confidence 與 review state。
- 每個 NHI 品項 mapping 可回到 immutable IODE/INAE snapshot、source row、
  raw value、有效期間與 exact rule URL；當月 source row count/hash 與
  normalized projection 相符。
- rule→ATC 是經 resolved product evidence 推導，公開輸出帶 support count；
  不把它誤寫成整個 ATC class 的適用範圍。
- ICD-11 populated crosswalk 只有在 WHO agreement 存在時可發布。
- JSONL、PostgreSQL 與 SQLite row counts、primary keys、foreign keys 相符。
- SQLite 通過 `integrity_check` 與 `foreign_key_check`。

## Release

- 30–50 條分層 canary 涵蓋新增、修改、刪除、恢復、搬移、split／merge、
  correction 與缺件。
- source／dataset／SQLite checksums 公開。
- Critical／High finding 為 0。
- rollback drill 與 reader wording 通過。

當官方來源宇宙仍是 open 時，不得宣稱絕對「完整歷史」。完成前的安全標示是：

`已取得的官方版本與已核實異動`

個別條文逐項通過後，只能依實際 declared cut 標：

`在已枚舉官方版本中，證據完整至 YYYY-MM-DD`

若另有公告 linkage，可附加顯示「已連結公告」，但不能把公告 availability
當作這個標示的必要條件。詳細 agent 契約見
[逐條文歷史工作的 agent 方法學](agent-work-methodology.md)。
