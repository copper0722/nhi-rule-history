# 健保藥品給付條文完整歷史

[![status: staging only](https://img.shields.io/badge/status-staging%20only-orange)](STATUS.md)
[![repository: public](https://img.shields.io/badge/repository-public-brightgreen)](https://github.com/copper0722/nhi-rule-history)

**[試用第 0.4 條單頁歷史 prototype](https://copper0722.github.io/nhi-rule-history/?rule=0.4)**
— 最新全文置頂，往下只看每次實質文字變更；頁面內可直接回報 GitHub issue。

這是一個公共資料工程專案：從健保署官方整份檔、公告頁與附件，重建每一條
藥品給付規定的版本、生效時間、來源、前後關係與文字差異。PostgreSQL 是
唯一可寫的結構化權威；GitHub 提供 JSONL 公開交換檔，並可轉成 SQLite
供沒有 PostgreSQL 的使用者使用。

## 目前狀態

**尚未完成完整歷史庫。**

2026-07-28 起，條文整理的 Claude／其他模型派送已依 Copper 指示暫停。
deterministic raw acquisition 與 bounded public-source research可保留；
在 transition-evidence schema、queue converter、validator 與 10-unit pilot
完成前，不重新啟動 agent 量產。

2026-07-29 的方法修正是：**年度整編檔是逐條 source observations**。
「Git-like」只作內部比對譬喻；先記錄 appearance／text-change／
disappearance，再由條文身分、民國年月及公文判斷新增、改寫、移動、刪除
或恢復。找不到公文時保留明示觀察區間與來源缺口，不把快照日期冒充生效日，
也不把兩個 snapshots 冒充直接相鄰法律版本。

第一個 PG-first 可閱讀模板已完成：15 份官方 `通則` 累積版本先作為來源
容器，再確定性切成 12 個專案條文 `0.1–0.12`。**一個條文才是一條獨立
version chain**：目前共有 152 筆來源觀察、29 個不同文字狀態、17 條
單條文相鄰文字邊與 26 組 diff。最新版只顯示所選條文全文；歷史列只顯示
該條相較下一個文字狀態的變更。JSONL、SQLite 與每條文前端 JSON 均從同一
個 sealed PostgreSQL import 程式化產生。這只證明
**宣告來源版本集合內的條文觀察與文字狀態完整**，不證明官方來源宇宙封閉
或法律事件史完整。
詳見 [`通則` 方法學](docs/chapter-00-template.md)與
[reader template](prototype/reader/index.html)。

條件式閱讀層已正規化成 PostgreSQL 的 concept／alias／occurrence schema，
不再由前端手工逐條加標籤。第一個 sealed run 以既有 82 個 reviewed tags
建立 79 concepts、371 aliases 與 92 public-code links，再以
longest-match、token boundary、精確 Unicode／UTF-8 offsets 與 no-overlap
規則掃描現行 639 條、13,874 個 source blocks。共保存 1,916 occurrences：
1,294 admitted、192 candidate、430 blocked；無命中的 block 也有 scan
receipt。0.4 的糖尿病、insulin、GLP-1、CAPD、透析液等可由同一 API 自動
渲染；正文只顯示名稱，ATC、ICD-11 與健保治療支付碼留在 hover／鍵盤
focus／手機點按提示窗。ICD-11 公開投影只含 code，WHO title、URI、
definition 與 reference snapshot 仍留在私有 PostgreSQL。詳見
[資料授權邊界](DATA_LICENSE.md)。

這個 run 已完整掃描既定 publication，但詞彙分母仍只是 82-tag reviewed
seed，**不能宣稱全書術語已涵蓋**。例如 2.6.3 的 ezetimibe、statin、
gemfibrozil 與高膽固醇血症目前仍是公開 negative canary。新增詞彙須先進
concept／alias review，再建立新的 immutable run；不在 HTML 手工補字。

條文與藥品的連結也已新增「健保給付代碼」維度。ATC 保留作藥理分類與
搜尋，不再被當成唯一的法律適用 binding。第一個 production canary 是
115/9/1 生效的 2.6.1：609 個 10 碼健保給付代碼直接連到完整條文版本與
適用分支，其中 116 個表二例外由公告逐碼明列，493 個表一品項由當期 C10
母表扣除例外集合確定性產生。名稱與 ATC 可協助搜尋，但前端及 API 的分支
判斷以版本化健保給付代碼連結為準。

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
[公開方法學](docs/methodology.md)與
[機器可讀稽核](docs/audits/2026-07-27-legacy-history-date-annotation-audit.json)。
完整重建的 source-universe、bundle、marker resolution、snapshot/replay
工作包與硬性分母見[逐條歷史重建計畫](docs/history-rebuild-plan.md)。目前
6,366 個 slash-triplet occurrences 中，6 筆已確認是 Trelegy Ellipta
劑量而非日期；其餘 6,360 個有效日期尚未依 evidence-basis 契約完成
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

NHI 公告與 FINT 也已先按正規化文號對帳，但不能據此宣稱法律歷史完整：
NHI 858 筆形成 847 個文號鍵，FINT exact-phrase 366 筆形成 365 個文號鍵；
交集只有 217，NHI-only 630、FINT-only 148，且有 7 個文號碰撞，不能做
一對一 join。2026-07-28 另確認 FINT 可用空關鍵字逐年度列舉；當時
`1900-01-01..2026-07-28` 的官方分母為 17,497。年度廣抓在完成
1900–1989 共 90 個 partitions／448 rows 後停止，保留作可選 recall audit；
主流程改由快照差異與日期 marker 產生 targeted queries。

NHI 父層雖將 `lp-3258` 標成「自103年4月3日以後生效之公告」，2026-07-29
實測 target listing 只有 859 rows／43 頁，最舊可見為 111-09-06，且有
「刊登期限」。因此它不是 2014 年後的完整檔案庫。84–87 年也必須用前身
`全民健康保險藥品使用規範` 搜尋。這個歷史名稱已在 FINT 找到
`健保醫字第84010140號` 及 25 頁完整掃描附件；raw bundle 與 live PG
sealed run 已完成。仍未找到的是精確的 85/1/1 修正文：條文句子查詢為 0，
日期 token 的命中都不相關。這只能寫「在宣告查詢範圍內未找到」，不能寫成
公文不存在。

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
transition promotion 的 `nhi_rule_history` schema。`通則` 的
`nhi_rule_history_edition` 只承載 source-observed cumulative editions；
`nhi_rule_history_clause` 則承載每個單一條文的 source-observed 文字版本與
diff。兩者都不冒充已驗證法律事件史。

## Repo 的重點

- [資料取得與更新 workflow](docs/workflow.md)
- [FINT 歷史公文研究 crawler](docs/fint-keyword-crawler.md)
- [`通則` PG-first 模板與更新方法](docs/chapter-00-template.md)
- [單頁歷史的讀者體驗契約](docs/reader-experience.md)
- [逐條文歷史工作的 agent 方法學（v3）](docs/agent-work-methodology.md)
- [v2 方法學與日期完整性檢核](docs/methodology-v2.md)
- [逐條歷史重建計畫](docs/history-rebuild-plan.md)
- [日期／條號候選到正式文號的 machine-readable receipt](docs/audits/2026-07-27-history-marker-document-candidate-preflight.json)
- [資料庫結構與 SQLite 轉換](database/README.md)
- [健保給付代碼、ATC 與 ICD-11 linkage 設計](docs/linkage.md)
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

PYTHONPATH=src python3 tools/rebuild_chapter00_clauses.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00-clauses \
  --reader-dir prototype/reader/data/clauses \
  --sqlite-output /tmp/nhi-rule-history-chapter-00-clauses.sqlite

PYTHONPATH=src python3 tools/crawl_fint_years.py \
  --batch-dir /path/outside/git/fint-all-years \
  --start-year 1900 \
  --capture-cut 2026-07-28 \
  --attachment-policy none
```

新 FINT crawler 不關閉 TLS。Python 3.14 會因官方舊憑證缺少 Subject Key
Identifier 而拒絕該鏈；crawler 改用系統 `curl` 的驗證 trust stack，並
禁止 redirect、核對 effective URL、限制檔案大小。舊版
`--allow-insecure-tls` acquisition 只保留為歷史 receipt，不再是新跑法。

## 官方來源

- [全民健康保險藥品給付規定歷史檔](https://www.nhi.gov.tw/ch/cp-2192-9951a-2509-1.html)
- [健保署法規公告](https://www.nhi.gov.tw/ch/lp-3258-1.html)
- [衛福部法規函釋查詢](https://mohwlaw.mohw.gov.tw/FINT/FINTQRY01-1.aspx)
- [健保用藥品項網路查詢（INAE3000）](https://info.nhi.gov.tw/INAE3000/INAE3000S01)
- [健保用藥品項查詢項目檔（IODE）](https://info.nhi.gov.tw/IODE0000/IODE0000S09?id=111)

本專案與衛生福利部中央健康保險署、WHO 或 WHO Collaborating Centre 無隸屬、
代理或認可關係。
