# 專案進度

最後核對：2026-07-27

## 已完成

- 公開 GitHub repo 與資料授權邊界。
- 14 份歷史 ODT 的 checksum manifest。
- staging parser、PostgreSQL loader、migration 與 70 項 repo 測試。
- 14 份 ODT、213,512 個結構區塊、9,303 個候選的封存 run。
- v1 八表 deterministic JSONL／SQLite exporter；完整 empirical export
  通過 typed-row parity，release assets 已準備但尚未發布。
- ATC linkage 資料模型，以及 ICD-11 授權 fail-closed 邊界。
- `raw-odt-v1` GitHub Release：14 個 ODT、49,709,507 bytes，下載檔名、
  size 與 SHA-256 已和 manifest 對照通過。
- v2 程式化 update pipeline：source plan、independent discover A/B、
  `compare-discovery`、resumable fetch、content-addressed raw、offline verify、
  generic ODT structural parse 與 PG loaders。
- MOHW FINT bounded run（2021-01-01..2026-07-27）：366 detail pages、
  1,353 attachments、1,719 resources；A/B resource-key set 完全一致。
- corrected acquisition PG run `51189ce2-ce51-461c-bd96-c59a526f6065`
  已 sealed：1,712 unique artifacts、85,642,128 bytes、0 issues。
- corrected structural PG run `d60dcfb2-2bd1-4c3e-8baf-5ad998b01f54`
  已 sealed：360 ODT resources／358 unique artifacts、31,377 blocks、
  1,228 occurrence candidates、547 nonblocking issues、0 blocking issues。
- acquisition/structural migrations 均通過 transaction rollback，
  live load 後以 fresh connection 重算 counts 與 row-set fingerprints。
- v2 的 corrected/superseded run、capture window、PG sealed fingerprints
  與 0-collision receipt 已寫入 machine-readable release eligibility gate；
  現有資產只稱 partial evidence bundle，不稱 portable dataset release。

## 尚未完成

- 完整官方來源宇宙：本輪只封閉「精確字串＋日期窗」的 MOHW FINT query；
  NHI current whole／chapter、NHI listing 與同義 query 仍待 discrepancy closure。
- 法律生效日與公告 event/effect ledger。
- 穩定條文身分、條號重用、split／merge／move／restore／correction。
- cumulative anchor event replay。
- 直接相鄰版本 diff。
- normalized public clause dataset 與 SQLite snapshot。
- v2 acquisition/structural 的 typed-row JSONL↔SQLite projection。
- v2 clean-room rebuild 與 final-commit code-hash binding。
- 真實資料 API／讀者頁。

## 現在的下一步

WP03：以 NHI 最新整份／分章為 cumulative anchors，對 366 份公告 detail 與
附件建立 evidence-backed event/effect candidates；先做 whole↔chapter parity
與跨來源 discrepancy，不直接從日期字串推論法律生效日。

注意：第一次 acquisition run `43ecabc6-64c6-4f30-80ec-ecebe25ea361`
在雙輪驗證時發現 RowNo-based resource identity 缺陷。該 immutable run 留在
PG 作 audit evidence，標記為 `superseded_by_methodology`，不作 release input；
正式 stage 是上列 corrected run `51189ce2…`。

逐項關閉證據見 [docs/gap-register.md](docs/gap-register.md)。
