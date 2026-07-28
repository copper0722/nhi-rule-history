# 健保藥品給付條文完整歷史

[![status: staging only](https://img.shields.io/badge/status-staging%20only-orange)](STATUS.md)
[![repository: public](https://img.shields.io/badge/repository-public-brightgreen)](https://github.com/copper0722/nhi-rule-history)

這是一個公共資料工程專案：從健保署官方整份檔、公告頁與附件，重建每一條
藥品給付規定的版本、生效時間、來源、前後關係與文字差異。PostgreSQL 是
唯一可寫的結構化權威；GitHub 提供 JSONL 公開交換檔，並可轉成 SQLite
供沒有 PostgreSQL 的使用者使用。

## 目前狀態

**尚未完成完整歷史庫。**

2026-07-28 起，條文整理的 Claude／其他模型派送已依 Copper 指示暫停。
deterministic raw acquisition 可保留；在 v3 transition-evidence schema、
queue converter、validator 與 10-unit pilot 完成前，不重新啟動 agent
量產。

第一個 PG-first 可閱讀模板已完成：官方 `通則` 在宣告的 15 份累積版本
集合中有 15 個完整 snapshot、14 條相鄰版本比較邊與 26 組實質 diff；
最新版全文置頂，歷史列只顯示相較下一份累積版本的變更。JSONL、SQLite 與
前端 JSON 均從同一個 sealed PostgreSQL import 程式化產生。這只證明
**宣告版本集合的序列完整**，不證明官方來源宇宙封閉或法律事件史完整。
詳見 [`通則` 方法學](docs/chapter-00-template.md)與
[reader template](prototype/reader/index.html)。

其餘全庫工作仍分成互不冒充的受限 staging：

| 項目 | v1 年度整份檔 | 歷史公告 exact phrase | post-109 公告 exact phrase |
|---|---:|---:|---:|
| 來源範圍 | 14 份歷史 ODT（96.7–109） | 1996–2020（命中始於 1999） | 2021-01-01–2026-07-27 |
| 官方 detail／附件 | — | 942／1,178 | 366／1,353 |
| 逐正式文號 source bundle | — | 942／942 verified | 366／366 verified |
| 唯一 raw artifacts | 14 | 2,120（91,694,925 bytes） | 1,712（85,642,128 bytes） |
| ODT 資源／唯一 bytes | 14／14 | 240／240 | 360／358 |
| 結構區塊 | 213,512 | 13,995 | 31,377 |
| 條文編號出現候選 | 9,303 | 676 | 1,228 |
| staging 阻斷錯誤 | 0 | 0 | 0 |
| 正式法律歷史 | 尚未建立；僅 `通則` 累積版本模板 | 尚未建立 | 尚未建立 |

`9,303` 與 `1,228` 都不是唯一條文數或版本數。v2 ODT 多為「修訂後／原
給付規定」對照表；兩欄的文字已 lossless 入 stage，但尚未把欄位提升成法律
事件、穩定條文身分或版本先後。目前仍未完成法律生效日、transition 重播與
相鄰版本 diff。

日期註記也不能直接證明歷史完整。2026-07-27 對舊庫的第一輪窄格式稽核
發現 980 條現行文字含連續半形斜線日期，980/980 的日期集合都和 legacy
version dates 不一致。後續 exact-marker parser 允許來源原有的斜線旁空白，
因此再納入 `3.3.12`、`13.10.5`、`13.10.6` 三條的 `99/2 /1`／
`93/8 /1`，最終 raw extraction 分母是 983 條、6,366 個 occurrence；[980→983
分母核對](docs/audits/2026-07-27-date-marker-denominator-reconciliation.json)
已用同一 sealed PG rows 在唯讀交易中重算。日期應作為 completeness
checksum，用來要求每個 marker 都有終結的日期角色／transition 裁決、
前後完整快照與直接相鄰 edge；不能用它逆推出已被刪除的舊文字。公告若能
找到則另作補強 linkage，但不是完成必要條件。另「通則」
是官方名稱，`chapter:00` 只是本專案排序碼，不是官方「第 0 章」。詳見
[v2 方法學](docs/methodology-v2.md)與
[機器可讀稽核](docs/audits/2026-07-27-legacy-history-date-annotation-audit.json)。
完整重建的 source-universe、bundle、marker resolution、snapshot/replay
工作包與硬性分母見[逐條歷史重建計畫](docs/history-rebuild-plan.md)。目前
6,366 個 slash-triplet occurrences 中，6 筆已確認是 Trelegy Ellipta
劑量而非日期；其餘 6,360 個有效日期尚未依 v3 evidence-basis 契約完成
日期角色／transition adjudication；舊 resolver 的 `0/6,360 連到公告`
不是完成 gate，也不能解讀為公告不存在。terminal
current 整份／分章各重建 639 條後，也驗出 33 條全文不同。

日期的確讓缺口容易核對，但目前只能做到 candidate coverage：6,360 個有效
marker 形成 3,080 個條文×日期組合。ODT-only 基線找到 1,897 組同日期，
其中 3,010 組正式數字條號中有 826 組在同一檔案同時找到日期與條號；納入
PDF、OLE 與 ODS 的原生文字後，兩個數字分別增為 2,034（+137）與
909（+83）。這仍未證明日期是法律生效日、該公告修改此條或前後文字直接
相鄰；影像 OCR 另列為非 authoritative candidate，不能算官方文字。
[跨格式機器可讀 preflight](docs/audits/2026-07-27-history-marker-cross-format-preflight.json)
保留了 exact locator 與這個有限結論。

909 組 joint candidates 再沿 immutable artifact→resource→正式文號鏈回溯，
全部都有公文候選：490 組只對到一個文號，419 組對到 2–11 個文號，共涉及
282 個文號。唯一文號仍只是下一輪閱讀佇列，不是已證明的 amendment effect。
完整 3,080 組現已各有一筆可續跑的 v1 discovery 工作單，見
[history gap work queue manifest](docs/audits/2026-07-27-history-gap-work-queue-manifest.json)；
四條優先 lane 僅用於安排來源閱讀，不是法律判定或完整性證書。該 v1
contract 因錯把 official event 設為必要結果，已停止直接執行；下一步是轉成
direct-edge v3 work units 與 10-unit pilot。

ATC linkage 的原始來源也已重新釐清。既有 runtime 的確使用
[`INAE3000` 健保用藥品項網路查詢](https://info.nhi.gov.tw/INAE3000/INAE3000S01)
建立品項連結；可公開重建的主來源則是 IODE resource
`A21030000I-E41001-001`。2026-07-27 實抓為 224,455 列、45,124 個健保
藥品代號、2,244 個 ATC codes，95,520 列帶給付規定文件 URL。repo 現已
提供 content-addressed raw fetcher 與 PostgreSQL／SQLite 對稱的 row-level
source schema。這關閉的是 acquisition／schema 方法缺口；條文到 ATC 的
public mapping 仍須把 508 筆 legacy 未解析條文 link 與 snapshot parity
處理完，不能宣稱 linkage 已完整。

NHI 公告與 FINT 也已先按正規化文號對帳，但不能據此宣稱來源宇宙完整：
NHI 858 筆形成 847 個文號鍵，FINT exact-phrase 366 筆形成 365 個文號鍵；
交集只有 217，NHI-only 630、FINT-only 148，且有 7 個文號碰撞，不能做
一對一 join。這個差異清冊讓後續逐筆裁決可重跑，卻同時證明「公告已抓完」
仍不是目前可說的結論。

實庫狀態不靠人工翻頁判斷；唯讀
[`history-completeness-status.sql`](database/queries/history-completeness-status.sql)
會直接列出 marker、resolution、review queue 與 canonical schema 狀態。
2026-07-28 的 fresh run 是 6,366 unresolved、0 resolved、9/9
needs-review，且 canonical schema 尚不存在。這裡 6,366 是 immutable raw
annotation stage；event resolver 已把其中 6 筆終結判為非日期劑量，
有效 amendment-date denominator 是 6,360。以 declared cut 逐條驗收時，
目前可認證完整的是 **0/1,548 條**；這不表示每一條必然改過，而是 565 條
沒有日期註記者也不能在來源宇宙尚未封閉時反推「從未修正」。詳見
[逐條完整性 scoreboard](docs/audits/2026-07-27-per-clause-history-completeness-scoreboard.json)。

這裡的「canonical schema 尚不存在」專指通過外部文件驗證與法律
transition promotion 的 `nhi_rule_history` schema。`通則` 使用的
`nhi_rule_history_edition` 已在 PostgreSQL 中正規化並封存，但它明確只
承載 source-observed cumulative editions。

## Repo 的重點

- [資料取得與更新 workflow](docs/workflow.md)
- [`通則` PG-first 模板與更新方法](docs/chapter-00-template.md)
- [單頁歷史的讀者體驗契約](docs/reader-experience.md)
- [逐條文歷史工作的 agent 方法學（v3）](docs/agent-work-methodology.md)
- [v2 方法學與日期完整性檢核](docs/methodology-v2.md)
- [逐條歷史重建計畫](docs/history-rebuild-plan.md)
- [日期／條號候選到正式文號的 machine-readable receipt](docs/audits/2026-07-27-history-marker-document-candidate-preflight.json)
- [資料庫結構與 SQLite 轉換](database/README.md)
- [ATC 與 ICD-11 linkage 設計](docs/linkage.md)
- [Agentic 重建與 Grok pilot 心得](docs/agentic-lessons.md)
- [原始資料與容量策略](data/README.md)
- [完成契約](docs/completion-contract.md)
- [進度與缺口](STATUS.md)
- [機器可讀專案狀態](project.yaml)

## 原始資料會不會超過 GitHub 容量？

現在不會。v1 14 份 ODT 約 47 MB；兩個公告 exact-phrase baselines 的
raw artifacts 合計約 169 MiB。單檔沒有超過 GitHub 的 100 MiB Git 上限，
但總量已不適合反覆寫進 Git history，否則每次 clone 都會背負全部 binary。

本專案採兩層策略：

1. Git 追蹤 source manifest、由 sealed PostgreSQL 匯出的 normalized
   JSONL、schema、程式與小型樣本。
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

PYTHONPATH=src python3 tools/rebuild_chapter00.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00 \
  --reader-json prototype/reader/data/chapter-00-reader.json \
  --sqlite-output /tmp/nhi-rule-history-chapter-00.sqlite

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
- [健保用藥品項網路查詢（INAE3000）](https://info.nhi.gov.tw/INAE3000/INAE3000S01)
- [健保用藥品項查詢項目檔（IODE）](https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111)

本專案與衛生福利部中央健康保險署、WHO 或 WHO Collaborating Centre 無隸屬、
代理或認可關係。
