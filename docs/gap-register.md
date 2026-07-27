# 缺口清冊

不以主觀百分比表示完成度；每一列以可重現 evidence 關閉。

| ID | 缺口 | 狀態 | 關閉證據 |
|---|---|---|---|
| G-SOURCE-01 | 官方來源宇宙未封閉 | open | 所有 release/listing/detail/attachment manifest 與 hash |
| G-SOURCE-03 | MOHW FINT exact-query date-window acquisition | passed_bounded | A/B 1,719-key parity、1,719/1,719 fetch、offline hash verify |
| G-SOURCE-02 | current whole/chapter parity 未證明 | open | 同批 live bytes 與逐條 parity |
| G-UPDATE-01 | 單一 update orchestration 未完成 | partial | discover／compare／fetch／verify／parse／load 各步可重跑；尚缺一鍵 state machine |
| G-EVENT-01 | event/effect ledger 未建立 | blocked | 公告、附件新舊欄、correction/supersession |
| G-DATE-01 | 生效日未證據化 | blocked | 每個 accepted date 的 official locator |
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
- 1,719/1,719 raw linkage；31,377 blocks／1,228 occurrences structural stage。
- 70 項 legacy tests 加新 package/PG tests、sealed DML guard、bounded rollback、
  idempotent replay。

這些成果只證明 staging 可信，不能替代其餘 legal-history gate。
