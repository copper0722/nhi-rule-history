# 缺口清冊

不以主觀百分比表示完成度；每一列以可重現 evidence 關閉。

| ID | 缺口 | 狀態 | 關閉證據 |
|---|---|---|---|
| G-SOURCE-01 | 官方來源宇宙未封閉 | listing_detail_attachment_acquisition_and_grouped_reconciliation_passed_discrepancies_open | NHI listing 43/43 pages、858/858 rows、858/858 detail HTML 兩輪皆通過；2,400/2,400 attachments（476,139,573 bytes、0 issues）已 sealed。NHI 847 文號鍵與 FINT exact-phrase 365 鍵的交集 217、NHI-only 630、FINT-only 148，且有 7 個 collision keys；仍須 typed attachment semantics、同義 query、法務來源及逐筆裁決 |
| G-SOURCE-03 | MOHW FINT exact-query date-window acquisition | source_bundles_passed_bounded | A/B 1,719-key parity、1,719/1,719 fetch、366/366 deterministic notice bundles、offline hash verify 與 byte-identical replay |
| G-SOURCE-04 | MOHW FINT 1996–2020 exact-phrase historical baseline | source_bundles_all_declared_types_extracted_cross_format_candidate_matcher_passed_visual_review_open | A/B 2,120-key parity；942 detail、1,178 attachments 全抓並形成 942 bundles；ODT/PDF/OLE/ODS 原生 typed text matcher 得 2,034/3,080 日期與 909/3,010 joint candidates；3 份 Word／5 頁、13 standalone images 與 7 PDF zero-word pages仍有 OCR／visual review，且 candidate 尚未解析法律 effect |
| G-SOURCE-02 | current whole/chapter 官方內容不一致 | leaf_diffs_classified_canonical_resolution_open | 268 resources、267 unique artifacts、92/92 ODT 已 sealed；各重建 639 條，606 相同／33 不同；19 個 leaf 為 6 version/date、6 list-marker、6 punctuation、1 trailing-layout，仍須以 event/effect 裁決 canonical anchor |
| G-UPDATE-01 | 單一 update orchestration 未完成 | stage_lane_live_canonical_promotion_missing | RSS→完整附件→corpus→primary/fallback→candidate stage 已通過真實排程；尚缺 anchor-gated canonical promotion |
| G-PROMOTION-01 | canonical promotion 尚缺 external full-document verifier 與正向 lane | pro_method_audit_accepted_live_blocked_external_verifier_missing | adversarial review 依序找出來源綁定、時間鏈、MIME self-attestation、假 ODF ZIP、損壞壓縮 payload 與 51-byte 假 PDF 等缺口；最新草案將 release-linked ODT/ODS/PDF 全部設為 observation-only，三個 format policy 均無正向 lane。第八次獨立 disposable-PG gate 與 GPT Pro method audit 均為 C/H/M/L 0/0/1/2、`ACCEPT`；post-audit 271 項 public tests 通過（6 skip）。Medium 正是缺少 governed positive verifier/admission path，故 live 未套用 |
| G-SCHEDULE-01 | recurring RSS/update lane | passed_stage_only | 真實 scheduled poll 與 proposal fires、durable result log、primary timeout／fallback success、`AUTO_PROMOTION_ENABLED=false` |
| G-EVENT-01 | event/effect ledger 未建立 | blocked | 公告、附件新舊欄、correction/supersession |
| G-DATE-01 | 生效日未證據化 | blocked | 每個 accepted date 的 official locator |
| G-ANNOTATION-01 | source-local 日期註記尚未完成 event resolution | raw_6366_adjudicated_909_joint_pairs_document_mapped_resolution_0_of_6360 | 6,366 個 exact slash-triplet candidates 已入 append-only stage；6 筆已確認是 Trelegy 劑量而非日期，有效日期分母 6,360。native cross-format preflight 為 2,034/3,080 同日期、909/3,010 同檔日期＋正式條號候選；909 組皆已追到 owning 文號（490 unique、419 ambiguous），但首輪法律 resolution 仍為 6,360 no-match／6 terminal non-date／0 resolved |
| G-COVERAGE-01 | 尚無逐條完整性 closure | schema_ready_all_blocked | annotation/event/snapshot/adjacency/source-universe/anchor 六項全通過 |
| G-ID-01 | stable identity 未建立 | blocked | UUID、designation、curation、無 cycle |
| G-REPLAY-01 | event replay 未對 anchors | blocked | rule set/text hash parity |
| G-DIFF-01 | adjacent diff 未建立 | blocked | direct edge、100% source mapping |
| G-ATC-01 | ATC linkage 尚未重建 | open | NHI/TFDA source-backed mappings 與 review |
| G-ICD-01 | ICD-11 crosswalk 未獲 WHO agreement | blocked | 書面 agreement 與 release citation |
| G-DATA-01 | normalized clause JSONL 未發布 | blocked | complete rule/version outputs |
| G-SQLITE-01 | v1 SQLite snapshot 已準備；v2 尚缺 | partial | v1 typed parity passed；v2 row parity、integrity、checksum |
| G-API-01 | reader API 未建立 | blocked | accepted read contract 與 canary |
| G-RAW-01 | 14 ODT Release assets | passed | `raw-odt-v1`：14/14 name、size、SHA-256 parity |
| G-RAW-02 | post-109 raw/structural assets | prepared_partial_evidence_bundle_not_published | v2 release manifest、eligibility receipt、zstd decompressed checksum；SQLite/portable contract 仍 open |
| G-COMPLETE-01 | 完整歷史聲明未達 gate | blocked | completion contract 全部通過 |

## 已通過的有限項目

- 14 ODT checksum manifest。
- 14-release source-occurrence staging。
- 9,303／9,303 occurrence round trip。
- 366 detail／1,353 attachment bounded acquisition；雙輪 resource-key parity。
- 366/366 post-109 source-local notice bundles offline verify 與
  byte-identical replay。
- 1999–2020 的 942 detail／1,178 attachment historical exact-phrase
  acquisition；雙輪 2,120-key parity、91,694,925 bytes、240/240 ODT
  structural parse；另完成 942/942 source-local notice bundles 的 offline
  verify 與 byte-identical replay。1996–1998 exact phrase 為 0，不代表
  來源不存在。
- 1,719/1,719 raw linkage；31,377 blocks／1,228 occurrences structural stage。
- 91 項 public tests（5 項環境性 skip），另有 private registrar/migration
  contract tests、sealed DML guard、bounded rollback與 idempotent replay。
- legacy 日期註記稽核已完成；它證明缺口存在，不是完成證據。
- PostgreSQL／SQLite 已能 fail-closed 表達 navigation-code provenance、
  source date annotations 與 per-rule history coverage。
- 連續更新已在真實排程完成完整附件 corpus registration；PDF、ODS、PDF、
  ODT 及八附件公告皆未丟檔。primary failure／timeout 後的一次性 fallback
  亦已實際成功，候選仍停在 `needs_review`。
- NHI 現行整份／分章已獨立列舉兩次並實抓全部附件；raw 與 ODT structural
  stages 已 sealed。完整條文重建各 639 條，606 相同／33 不同；parity
  gate 已執行但失敗，33 個 discrepancy 仍 open。

這些成果只證明 staging 可信，不能替代其餘 legal-history gate。
