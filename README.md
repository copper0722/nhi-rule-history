# 健保藥品給付條文完整歷史

[![status: staging only](https://img.shields.io/badge/status-staging%20only-orange)](STATUS.md)
[![repository: public](https://img.shields.io/badge/repository-public-brightgreen)](https://github.com/copper0722/nhi-rule-history)

這是一個公共資料工程專案：從健保署官方整份檔、公告頁與附件，重建每一條
藥品給付規定的版本、生效時間、來源、前後關係與文字差異，並提供 PostgreSQL、
JSONL 與 SQLite 可攜資料。

## 目前狀態

**尚未完成完整歷史庫。**

現有成果是 14 份歷史 ODT 的受限 staging：

| 項目 | 數量／狀態 |
|---|---:|
| 官方歷史 ODT | 14 份，約 47 MB |
| 結構區塊 | 213,512 |
| 條文編號出現候選 | 9,303 |
| 純文字量 | 約 5.6 MB |
| staging 阻斷錯誤 | 0 |
| 正式法律歷史 | 尚未建立 |
| SQLite release | 尚未產出 |

`9,303` 不是唯一條文數或版本數；目前尚未完成法律生效日、穩定條文身分、
公告事件重播與相鄰版本 diff。

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

現在不會。14 份 ODT 合計約 47 MB，最大單檔約 8 MB；解析出的文字約
5.6 MB。GitHub 一般 Git 單檔上限為 100 MiB，建議 repo 低於 1 GB。

本專案採兩層策略：

1. Git 追蹤 source manifest、normalized JSONL、schema、程式與小型樣本。
2. 不變的官方 DOC／ODT／PDF／ZIP 與 SQLite snapshot 放 GitHub Releases；
   每個 release asset 可到 2 GiB，且不會讓每次 clone 背負全部 binary history。

條文全文可以進 Git；官方二進位原檔則用 Release assets 比較耐久。

## 資料授權

健保署網站允許資料重製、改作、公開傳輸與再授權，但必須註明出處。本 repo
以「資料來源：衛生福利部中央健康保險署」標示，並保留官方 URL 與 hash。

程式採 MIT License；本專案產生的資料採 CC BY 4.0。ATC／DDD 與 ICD-11
內容不自動包含在本專案資料授權內，詳見 [DATA_LICENSE.md](DATA_LICENSE.md)。

## 快速驗證

```bash
python3 -m unittest discover -s .script/nhi-rule-history/tests -p 'test_*.py'
sqlite3 /tmp/nhi-rule-history.db < database/sqlite-schema.sql
```

## 官方來源

- [全民健康保險藥品給付規定歷史檔](https://www.nhi.gov.tw/ch/cp-2192-9951a-2509-1.html)
- [健保署法規公告](https://www.nhi.gov.tw/ch/lp-3258-1.html)
- [健保用藥品項查詢項目檔](https://data.gov.tw/dataset/23715)

本專案與衛生福利部中央健康保險署、WHO 或 WHO Collaborating Centre 無隸屬、
代理或認可關係。
