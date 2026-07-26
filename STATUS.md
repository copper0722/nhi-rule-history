# 專案進度

最後核對：2026-07-27

## 已完成

- 公開 GitHub repo 與資料授權邊界。
- 14 份歷史 ODT 的 checksum manifest。
- staging parser、PostgreSQL loader、migration 與 70 項 repo 測試。
- 14 份 ODT、213,512 個結構區塊、9,303 個候選的封存 run。
- PostgreSQL／SQLite 的可攜 logical schema 初版。
- ATC linkage 資料模型，以及 ICD-11 授權 fail-closed 邊界。
- `raw-odt-v1` GitHub Release：14 個 ODT、49,709,507 bytes，下載檔名、
  size 與 SHA-256 已和 manifest 對照通過。

## 尚未完成

- 完整官方來源宇宙，包括現行整份／分章與公告 detail／附件。
- 法律生效日與公告 event/effect ledger。
- 穩定條文身分、條號重用、split／merge／move／restore／correction。
- cumulative anchor event replay。
- 直接相鄰版本 diff。
- normalized public clause dataset 與 SQLite snapshot。
- 真實資料 API／讀者頁。

## 現在的下一步

WP01：把 14 ODT 以外的每個官方來源枚舉、下載、hash、寫入 manifest。
已封存的 14-ODT run 與 `raw-odt-v1` 不修改；新增來源建立新的 manifest、
fingerprint 與 release。

逐項關閉證據見 [docs/gap-register.md](docs/gap-register.md)。
