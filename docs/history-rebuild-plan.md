# 逐條文完整歷史重建計畫

狀態：執行中
Declared cut：2026-07-27
Canonical store：PostgreSQL `vault_main`
公開輸出：JSONL、SQLite、checksums；不得由舊 `tw_drug.rule_*` 反向升格

## 現在可以精確回答的分母

現行 1,548 條舊庫文章中，983 條含斜線三元組，共 6,366 個 exact raw
candidates；其中 6,360 個是有效日期，6 個是已裁決的非日期劑量：

第一輪使用「斜線必須緊貼數字」的窄 regex 得到 980 條；expanded
exact-marker parser 另保留 3 條來源原有的斜線旁空白，因此 983 才是固定
checklist 分母。兩者已用同一 sealed PG rows 唯讀重算並逐條列出，見
[`2026-07-27-date-marker-denominator-reconciliation.json`](audits/2026-07-27-date-marker-denominator-reconciliation.json)。

| 時段 | marker occurrences | unique article × date |
|---|---:|---:|
| 1996-01-01..2007-06-01 | 647 | 506 |
| 2007-07-01..2020-12-01 | 3,015 | 1,528 |
| 2021-01-01..2026-07-01 | 2,698 | 1,046 |
| 合計有效日期 | 6,360 | 3,080 |
| 非日期劑量 slash triplets | 6 | 已逐筆裁決為 `92/55/22`／`184/55/22 mcg` |

有效 marker 分布在 323 個不同日期。這個 denominator 能回答「還有多少日期
註記沒有交代」，不能回答「已經有多少舊版全文」。

6,366 是 fail-closed raw pattern denominator；其中 6 筆已確認為 Trelegy
Ellipta 劑量而非日期。有效 amendment-date denominator 是 6,360，目前
event/effect resolution 是 0/6,360。完整歷史未成立。

## 已取得的原始資料

| 來源 | 已取得 | 現階段用途 | 不能推論 |
|---|---|---|---|
| 14 份 96 年 7 月版至 109 年版 ODT | 14/14、49,709,507 bytes | cumulative checkpoints | 檔名／metadata 不是法律生效日；年度 diff 不是公告 |
| FINT 1996–2020 exact phrase | 942 detail、1,178 attachments、91,694,925 bytes | 1999–2020 公告級候選；1996–1998 此查詢為 0 | exact phrase 不等於來源宇宙 |
| FINT 2021–capture cut exact phrase | 366 detail、1,353 attachments、85,642,128 bytes | post-109 公告級候選 | 仍缺同義詞與跨來源 discrepancy |
| NHI current whole/chapter | 268 resources、267 artifacts | terminal cumulative anchor 候選 | 兩個 official surfaces 不一致 |

歷史 exact-phrase 附件包含 240 ODT、431 PDF、347 OLE、147 ODS、9 JPEG、
3 GIF、1 TIFF。ODT 已完成 lossless structural parse；431/431 PDF 亦已
完成 deterministic text＋geometry extraction（845 頁、58,981 words、
0 blocking parse failures），但其中 7 個 zero-word pages 仍須 OCR／
視覺覆核，且表格 row/column semantics 尚未重建。147/147 ODS 已完成
repeat-aware typed cell extraction：6,521 physical cells、0 unsupported
cells；約 1.431 兆 logical cells 以範圍表示而不展開。347/347 OLE 已完成
CFB container／stream inventory：328 份 Word、19 份 Excel 中，325 份
Word 與 19 份 Excel 有 typed output；另 3 份 Word 共 5 頁是有內容的
image-only 頁面，明列待 OCR 或 visual transcription，不把空文字層誤判成
空文件。13/13 standalone images 已完成
deterministic render 與 no-network local OCR，共 11,959 characters；但
13/13 frames 仍待 visual review、0 human-verified，因此 OCR 不是 authoritative
legal text。

以 240 份 ODT 做的第一輪 candidate preflight，以及納入所有可用原生
typed text 的跨格式重算如下：

| 測試 | ODT-only | ODT＋PDF＋OLE＋ODS 原生文字 |
|---|---:|---:|
| 條文×日期可找到同日期 | 1,897 / 3,080 | 2,034 / 3,080（+137） |
| 正式數字條號×日期可在同一 artifact 同時找到日期與條號 | 826 / 3,010 | 909 / 3,010（+83） |
| `0.x` 通則導航碼 | 70 / 3,080 | 只查日期，不評估官方條號 |

同檔出現是搜尋候選，不是法律連結：它尚未證明日期是生效日、條號是同一
stable rule、該公文真的修改此條，也沒有證明 old/new 是直接相鄰版本。
13 image 與 native zero-text pages 的 OCR 另列為非 authoritative lane：
155 個日期候選、4 個 joint candidate，只有 3 個 joint candidate 超出
native lane，0 筆可直接當官方文字。這些數字全是 discovery coverage，
不是歷史完成率。ODT-only 的 14,518 筆 exact-locator evidence 已另存
不可變 JSONL，公開 receipt 見
[`2026-07-27-history-marker-odt-preflight.json`](audits/2026-07-27-history-marker-odt-preflight.json)。
跨格式 receipt 與 exact locator ledger 見
[`2026-07-27-history-marker-cross-format-preflight.json`](audits/2026-07-27-history-marker-cross-format-preflight.json)
與
[`2026-07-27-history-marker-cross-format-evidence.jsonl`](audits/2026-07-27-history-marker-cross-format-evidence.jsonl)。
909 個 native joint candidates 另已全部回溯到 owning official-document
candidates：490 組唯一文號、419 組有 2–11 個文號、0 unmapped，共 282 個
不同文號。這是 review queue，不是法律 effect resolution；公開 receipt 見
[`2026-07-27-history-marker-document-candidate-preflight.json`](audits/2026-07-27-history-marker-document-candidate-preflight.json)。
檔案式 replay 入口為
`python -m nhi_rule_history.history_marker_preflight_cli`；它要求 sealed
annotation run／rows／receipt 與 historical raw／structural stage 全部明列，
輸出 locator ledger 後再寫 compact report，全程不開 canonical PG write
connection。
ODS extraction 的 hash-bound receipt 見
[`2026-07-27-historical-ods-extraction-receipt.json`](audits/2026-07-27-historical-ods-extraction-receipt.json)。
Image render/OCR receipt 見
[`2026-07-27-historical-image-extraction-receipt.json`](audits/2026-07-27-historical-image-extraction-receipt.json)。
OLE CFB／Word／Excel typed extraction receipt 見
[`2026-07-27-historical-ole-extraction-receipt.json`](audits/2026-07-27-historical-ole-extraction-receipt.json)。

1999–2020 的 942 份正式公文也已逐份 materialize 為 source-local bundles：
942 detail、1,178 個明列附件、2,120 resources、91,694,925 bytes 均完成
offline verify，第二次執行為 byte-identical replay。這關閉的是 bounded
exact-phrase input 的「每份公文原始資料封裝」，不是官方來源宇宙、法律
生效日或逐條文歷史。

2021–2026-07-27 的 366 份 bounded exact-phrase 公文亦已完成相同規格：
366 detail、1,353 attachments、1,719 resources 全數 offline verify 且
byte-identical replay。兩個 bounded exact-phrase sets 合計 1,308 個正式
文號皆已有原子 bundle；尚未關閉的是同義 query／跨來源 universe、剩餘
格式 typed extraction／OCR、跨格式 marker matching、event/effect 與
snapshot replay。

Terminal current anchor 已各重建 639 條；606 條相同、33 條不同。因此在
33 個 discrepancy 裁決前，整份檔與分章檔都不能單獨成為 canonical truth。
重建採階層 subtree 條文邊界，子條文差異會向父條文 hash 傳播；33 是
mismatch designations，其中 19 個是 leafmost mismatch；兩者都不直接等同
獨立修正事件數。

19 個 leafmost mismatches 的 exact character diff 已再分成 6 個版本／日期
實質差異、6 個 list-marker 結構差異、6 個純標點與 1 個尾端補充表 layout。
`8.2.16` 的分章檔含 115/8/1 future-effective 文字，顯示「較新公布檔」
不能在生效日前當成 current。這項分類沒有選 canonical side；6 個
version/date rows 仍需正式公告與 effect locator 才能裁決。

## WP1 — 封閉官方來源宇宙

1. NHI `lp-3258` adapter 已兩輪枚舉全部 43 頁／858 rows，listing 階段
   未先用關鍵詞刪除，兩輪 resource-key set 與 JSONL bytes 均相同。858
   個 details 也已各自兩輪抓取；因官方頁會改寫瀏覽計數與 Cloudflare
   challenge 欄位，858/858 paired HTML hashes 都不同，兩份 raw 都保留，
   不錯誤要求跨網路觀察 byte-identical。離線 parser 從兩輪各得到完全相同
   的 2,400 個附件 occurrences／canonical URLs 與 5 個零附件頁；同一 sealed
   input 的重播才要求 byte-identical。2,400/2,400 份附件隨後已抓取，
   形成 2,396 unique artifacts、476,139,573 bytes、0 issues，offline
   verification 後 sealed 到 append-only PG acquisition stage。下一步是
   typed extraction、相關性與跨來源裁決；listing/detail/attachment
   acquisition 仍不能代替 event/effect closure。
   第一輪 NHI↔FINT 正規化文號分組對帳已將差異具體化：NHI 858 rows／
   847 keys，FINT exact phrase 366 rows／365 keys，交集 217、NHI-only
   630、FINT-only 148，union 995；7 個 collision keys 使一對一 join
   不安全。這只是 discrepancy inventory，不能把任一 source-only row
   自動判成漏失或無關。
2. FINT 從 1995-01-01 起按年 partition，因 1996-01-01 生效的事件可能在
   前一年公告。
3. 每個 query 各自形成可重播 source plan，最後以正式文號做 union，
   同時保存所有 query/partition locators：
   - `藥品給付規定`
   - `藥物給付項目及支付標準`
   - `第六編第八十三條`
   - `給付規定修訂對照表`
   - `暫予支付` + `藥品`
   - `停止給付` + `藥品`
   - `取消給付` + `藥品`
   - `更正` + `給付規定`
4. 加入法務部「支付標準第六編／附件六」修正來源。
5. 比對 FINT union、NHI listing、14 個 historical anchors、terminal
   whole/chapter delta；每個 FINT-only、NHI-only、anchor-delta-only row 都
   要有裁決。

Source-universe gate：

```text
pass A resource keys = pass B resource keys
expected pages/rows/details/attachments = fetched
unclassified relevant rows = 0
cross-source discrepancies = 0 or explicitly adjudicated
```

## WP2 — 每份正式公文形成 deterministic corpus bundle

原子單位是一個正式文號，不是一個附件，也不是一個年度 batch。

每個 bundle fingerprint 必須綁定：

- 正式文號與 exact detail artifact；
- 全部 child attachment resource IDs、URLs、labels、ordinals、media types、
  sizes 與 SHA-256；
- source plan、discovery manifest、raw manifest；
- 每個可用 parser 的版本與輸出 hash。

格式路徑：

- ODT：lossless XML blocks/table/list locators；
- ODT + PDF：兩側都解析，effect span 做 parity；
- ODT-only：若官方 manifest 明示沒有 PDF，可合法進 review，不永久阻塞；
- PDF-only：deterministic text/table locator，必要時 OCR；
- GIF/JPEG/TIFF：page/image hash + OCR spans + visual review；
- OLE/DOC/XLS、ODS：typed extractor；不能只留下副檔名。

格式尚未解析的 bundle 狀態是 `needs_format_extraction`，不是「沒有修正」。

## WP3 — Agent proposal lane

Controller 先決定 document identity、attachment inventory、候選日期 locator、
old/new table coordinates 與 dotted designation candidates。Agent 只能提出
綁定 exact source spans 的 proposal。

```text
primary = cm1 Claude
fallback = hm4 Codex, only once after primary timeout/failure/contract failure
```

Fallback 是 availability recovery，不是第二份法律證據。每份公告可 fan-out
為零到多個 effects；多條、`略`、merged cells、跨列、partial patch、
split/merge/move/delete/restore/correction 都留在 review，不硬切成假的
single full replacement。

Batch invariant：

```text
official_documents
= terminal_primary_success
+ terminal_fallback_success
+ terminal_failed
```

每份公文都要有 immutable terminal receipt。

## WP4 — 終結解析 6,366 個 raw candidates

排程可以先按 323 個日期、3,080 個 article-date pairs 聚合；canonical
denominator 永遠保留 6,366 個 occurrence rows。

ODT preflight 可用來排序搜尋工作，但不得自動 promotion：先處理 826 組
同檔候選，再由 typed PDF／OLE／ODS／image extraction 補回 ODT miss；每一
組最後仍須通過下列 event/effect contract。

唯一 resolved link 必須同時具備：

- exact normalized date；
- 日期角色已證明為法律 effective date；
- 相容 designation 與已裁決 stable identity；
- marker exact locator；
- official effective-date exact locator；
- effect scope 涵蓋 marker 所在子項；
- 沒有省略、多條合併、條號重用或同日多 event 歧義。

```text
6366
= verified_effect_link
+ verified_non_effect_annotation
+ invalid_source_date
+ ambiguous
+ unresolved_source_gap
```

Inventory closure 可以保留明列 gap；「完整歷史」要求
`ambiguous = 0` 且 `unresolved_source_gap = 0`。6 個 invalid marker 不得從
分母刪掉。

## WP5 — 14 份 cumulative ODT materialize full clauses

1. 逐版從 structural blocks 產生完整 clause body 與 source span；
2. 另找 edition/as-of evidence，不能使用 filename 或最大 marker 日期代替；
3. 跨版建立 stable identity crosswalk；
4. split、merge、move、delete、restore、number reuse 形成 curation tickets；
5. 年度 anchor delta 只生成「待找公告」ticket，不直接生成 legal event。

## WP6 — Snapshot、direct adjacency、diff 與 replay

1. 從已驗證 cumulative anchor 建 baseline。
2. 依官方 effective date 排 accepted effects；同日無法排序即 fail closed。
3. 每個 operation 具備必要的完整 sides：
   - create：完整 new；
   - amend/correction/rename/move：完整 old + new；
   - delete：完整 old + deletion；
   - restore：完整 new + lineage；
   - split/merge：所有 input/output snapshots。
4. 每個 version 使用半開區間 `[effective_from, effective_until_exclusive)`。
5. 每個相鄰 anchor 重算完整 rule set 與逐條 hash；mismatch 轉成來源搜尋
   ticket，不用 annual diff 捏造公告。
6. Reader diff 只比較直接相鄰版本；非 ambiguous mapping coverage 必須
   100%，不足時顯示歧義而非假 diff。

## 完成 gate

- 6,366/6,366 markers terminal adjudication；
- 每個 accepted effect 都有 official event FK、effective-date locator、
  identity/operation decision 與 operation-required full sides；
- interval overlap = 0；
- unordered same-day effects = 0；
- 每條最多一個 open head；
- 每個版本最多一個 direct predecessor，split/merge 另以明示 edge 表達；
- 所有相鄰 cumulative anchors 的 rule-set/hash parity；
- terminal whole/chapter 未裁決 discrepancy = 0；
- PostgreSQL／JSONL／SQLite PK、FK、rows、logical hashes 一致；
- SQLite `integrity_check` 與 `foreign_key_check` 通過。

## 禁止的捷徑

- marker 日期 = 法律生效日；
- 發文日／刊登日 = 生效日；
- 年度檔名／ODT metadata = anchor date；
- annual cumulative diff = 公告正文；
- 現行全文 + 日期列表 = 歷史全文；
- old/new 對照表 = 已證明 direct predecessor；
- 同日期 = 同 event；
- exact-phrase query = 完整來源宇宙；
- attachment count = 修正 count；
- occurrence candidate = stable rule；
- 後來附件內出現舊日期 = 已取得當年原始公告；
- agent fallback 同意 primary = 獨立證據；
- 把 whole/chapter 差異當排版雜訊。

## 下一個可交付 milestone

1,308 份已取得公文的 deterministic source-local bundles 已完成。下一個
milestone 是補通 PDF/OLE/image typed extraction，讓每份公文都有 terminal
proposal receipt；發布 marker candidate matrix，但仍不寫 canonical。其後
才以 NHI listing 與 widened FINT 關閉 source universe，再按 anchor interval
建完整 snapshots 與 replay。
