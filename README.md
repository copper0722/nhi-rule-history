# 健保藥品給付條文完整歷史

[![status: staging only](https://img.shields.io/badge/status-staging%20only-orange)](STATUS.md)
[![repository: public](https://img.shields.io/badge/repository-public-brightgreen)](https://github.com/copper0722/nhi-rule-history)

這是一個公共資料工程專案：從健保署官方整份檔、公告頁與附件，重建每一條
藥品給付規定的版本、生效時間、來源、前後關係與文字差異，並提供 PostgreSQL、
JSONL 與 SQLite 可攜資料。

## 目前狀態

**尚未完成完整歷史庫。**

現有成果分成兩個互不冒充的受限 staging：

| 項目 | v1 年度整份檔 | v2 後續公告／附件 |
|---|---:|---:|
| 來源範圍 | 14 份歷史 ODT（96.7–109） | 2021-01-01–2026-07-27 MOHW FINT bounded query |
| 官方 detail／附件 | — | 366 detail、1,353 附件 |
| 唯一 raw artifacts | 14 | 1,712（85,642,128 bytes） |
| ODT 資源／唯一 bytes | 14／14 | 360／358 |
| 結構區塊 | 213,512 | 31,377 |
| 條文編號出現候選 | 9,303 | 1,228 |
| staging 阻斷錯誤 | 0 | 0 |
| 正式法律歷史 | 尚未建立 | 尚未建立 |

`9,303` 與 `1,228` 都不是唯一條文數或版本數。v2 ODT 多為「修訂後／原
給付規定」對照表；兩欄的文字已 lossless 入 stage，但尚未把欄位提升成法律
事件、穩定條文身分或版本先後。目前仍未完成法律生效日、公告事件重播與
相鄰版本 diff。

## Repo 的重點

- [資料取得與更新 workflow](docs/workflow.md)
- [資料庫結構與 SQLite 轉換](database/README.md)
- [ATC 與 ICD-11 linkage 設計](docs/linkage.md)
- [Agentic 重建與 Grok pilot 心得](docs/agentic-lessons.md)
- [原始資料與容量策略](data/README.md)
- [完成契約](docs/completion-contract.md)
- [進度與缺口](STATUS.md)
- [機器可讀專案狀態](project.yaml)

## 原始資料會不會超過 GitHub 容量？

現在不會。v1 14 份 ODT 約 47 MB；v2 經 byte-level 去重的 raw artifacts
約 82 MiB。單檔沒有超過 GitHub 的 100 MiB Git 上限，但反覆把二進位歷史
塞進 Git 會拖累每次 clone。

本專案採兩層策略：

1. Git 追蹤 source manifest、normalized JSONL、schema、程式與小型樣本。
2. 不變的官方 DOC／ODT／PDF／ODS 與 SQLite snapshot 放 GitHub Releases；
   每個 release asset 可到 2 GiB，且不會讓每次 clone 背負全部 binary history。

條文全文可以公開；大型結構 JSONL、SQLite 與官方二進位原檔則用 Release
assets 比較耐久。v2 的公開 source manifest 已放在
[data/manifests/mohw-fint-2021-2026-v2](data/manifests/mohw-fint-2021-2026-v2/)。
首批 14 份原檔已發布於
[raw-odt-v1](https://github.com/copper0722/nhi-rule-history/releases/tag/raw-odt-v1)。

## 資料授權

健保署網站允許資料重製、改作、公開傳輸與再授權，但必須註明出處。本 repo
以「資料來源：衛生福利部中央健康保險署」標示，並保留官方 URL 與 hash。

程式採 MIT License；本專案產生的資料採 CC BY 4.0。ATC／DDD 與 ICD-11
內容不自動包含在本專案資料授權內，詳見 [DATA_LICENSE.md](DATA_LICENSE.md)。

## 快速驗證

```bash
make test
sqlite3 /tmp/nhi-rule-history.db < database/sqlite-schema.sql

PYTHONPATH=src python3 -m nhi_rule_history.cli discover \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli fetch \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-raw \
  --run-dir build/pass-a
```

`--allow-insecure-tls` 是 2026-07-27 對 MOHW FINT 實跑所需、必須明示的相容
模式；預設仍驗證 TLS。能提供正確 CA bundle 時應改用 `--ca-file`。

## 官方來源

- [全民健康保險藥品給付規定歷史檔](https://www.nhi.gov.tw/ch/cp-2192-9951a-2509-1.html)
- [健保署法規公告](https://www.nhi.gov.tw/ch/lp-3258-1.html)
- [衛福部法規函釋查詢](https://mohwlaw.mohw.gov.tw/FINT/FINTQRY01-1.aspx)
- [健保用藥品項查詢項目檔](https://data.gov.tw/dataset/23715)

本專案與衛生福利部中央健康保險署、WHO 或 WHO Collaborating Centre 無隸屬、
代理或認可關係。
