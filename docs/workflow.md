# 資料取得與更新 workflow

這個 workflow 的目標不是「抓到一份最新檔」，而是讓任何人能回答：

1. 這份資料從哪個官方頁面取得？
2. 當時官方頁面列出了多少 release、公告與附件？
3. 哪些 bytes 被解析？
4. 如何從原始檔重建條文、版本、事件與 diff？
5. 更新後哪些資料改變，哪些只是格式變動？

## 資料層

```text
official pages
  -> immutable source artifacts + manifest
  -> structural parse + source occurrences
  -> transition evidence ledger
  -> optional official-notice linkage
  -> stable rule identity + version snapshots
  -> adjacent comparisons + diffs
  -> normalized JSONL
  -> PostgreSQL build / SQLite portable release / API
```

每一層都保留輸入 hash、程式版本、輸出 fingerprint 與 issue ledger。下游不得
跳過上游 gate。

## WP01：來源枚舉與取得

### 1. 枚舉

每次 update 先讀 [sources/sources.yaml](../sources/sources.yaml)，完整枚舉：

- 歷史整份檔頁面上的所有格式與版本；
- 現行整份檔及分章／附表；
- NHI 公告 listing 的全部頁數與 rows；
- 每一個公告 detail page；
- 每個 detail page 的 DOC／ODT／PDF／ZIP 附件；
- 官方歷史查詢與母法附件六交叉來源。

listing 階段不得先用藥名或關鍵字丟資料。先 archive 完整 listing，再分類是否
影響藥品給付規定。

### 2. 下載

每個 artifact 建立：

```text
source_id
source_page_url
official_url
official_label
filename
media_type
byte_length
sha256
fetched_at
fetch_transport
supersedes_sha256
licence
```

相同 URL 回傳不同 bytes 時建立新 revision，不覆寫舊檔。403、selector miss
或 0 rows 是 failed update，不能解釋成「沒有更新」。

### 3. 保存

- manifest 與 normalized text 進 Git。
- 官方 binary 以 checksum-addressed GitHub Release assets 保存。
- manifest 必須可在下載 release assets 後逐檔重算 SHA-256。
- 同一 source bundle 的所有官方格式都保存；parser 可選主格式，但 PDF／DOC
  仍作交叉驗證。

目前 14 份 ODT 的 manifest：
[data/manifests/nhi-history-odt-v1.jsonl](../data/manifests/nhi-history-odt-v1.jsonl)。

歷史公告 acquisition 完成後，另以正式文號為原子單位建立 deterministic
source-local bundle。每份 bundle 都必須同時綁定 detail page 與該頁明列的
全部附件；沒有 ODT、只有 PDF／影像／OLE、或零附件都不能從分母消失：

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli historical-bundles \
  --run-dir build/historical-raw-run \
  --source-plan sources/source-plan-historical-events-exact-phrase.json \
  --output-root build/historical-notice-bundles
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-historical-bundles \
  --output-root build/historical-notice-bundles
```

這個步驟只證明 bounded input 內每份公文的原始 bytes 都已封裝與可重播；
不解讀生效日、條文身分或修正內容。

## WP02：結構解析

已實作的 ODT stage parser 會保存：

- 段落、標題、表格、列、儲存格、covered／empty cell；
- 原文字、normalized search text、hash 與長度；
- XML element index、repeat instance 與完整 locator；
- dotted-designation occurrence candidate；
- deterministic issue ledger。

它不推論法律生效日、不以條號建立 canonical identity，也不計算跨版 diff。

## WP03：Transition evidence 與可選公告連結

一個條文 transition 與公告是否仍可取得分開：

- `rule_transition`：對某一條文的 create、amend、delete、restore、
  rename、move、split、merge 或 correction；
- `transition_evidence`：官方 cumulative version、old/new 對照表、條文
  日期註記、archival snapshot 或公告 effect 的 exact locator；
- `official_notice` 與 `transition_notice_link`：找到時保存的補強 provenance，
  不是 mandatory transition foreign key。

每個 accepted effective date 必須指回官方 locator。公告列出的日期、附件
正文日期與實際生效日分欄保存。不能證明所有歷史公告都仍存在或可由現行
查詢找到，因此 notice linkage 是獨立 coverage，不是完成 gate。

### 日期註記 ledger

每一條／子項原文中的 ROC 日期註記全部列舉，不先去重成「條文日期」：

1. exact source span → `source_date_annotation`；
2. deterministic ROC conversion 只產生 ISO candidate；calendar-invalid
   slash triplets 必須讀 exact context，將劑量等非日期 observation 明確
   `rejected_non_amendment`，不可永遠留在日期分母；
3. 先由同檔日期＋條號 locator 沿 artifact→resource→parent detail 回查
   正式文號；unique owning document 仍只是 review candidate；
4. 有公告候選時，讀 detail 的生效句與 old/new 附件，建立 evidence
   proposal；
5. 找不到公告時記
   `notice_not_found_after_bounded_search`／`notice_availability_unknown`，
   不刪 marker，也不推論公告不存在；
6. 以其他官方 evidence basis 足以證明 transition 時，可在沒有公告連結下
   完成；但仍須完整 before/after、stable identity、direct adjacency 與
   anchor replay；
7. 每一條分開公開 `annotation_terminal_coverage`、
   `transition_evidence_coverage`、`notice_linkage_coverage` 與 gap reasons。

日期集合是 completeness checksum。它不能取代歷史附件、被刪文字或
cumulative anchor replay。

Legacy current-text 的第一輪 gap inventory 可用公開 CLI 重建：

```bash
PYTHONPATH=src python3 -m nhi_rule_history.cli load-annotation-stage \
  --input-jsonl legacy-article-observations.jsonl \
  --dsn "$NHI_RULE_HISTORY_DSN"
```

每個 JSONL row 必須包含 `article_id`、`article_num`、exact `full_text` 與明示
的 `source_identity` object。這個命令只寫 isolated append-only stage；所有
marker 的舊 v1 初態都是 `unresolved_event`；該欄名只代表尚未裁決。v3
工作包會將它轉為 evidence-basis candidate，不會推定公告、建立快照或寫入
canonical history。

## WP04：條文身分與版本

- `rule_identity` 使用永久 ID。
- 官方「通則」以 project navigation code `chapter:00` 排序；`00` 必須標記
  `project_assigned`，讀者顯示仍為「通則」，不稱「第 0 章」。
- `rule_designation` 保存條號、標題與有效區間。
- 相同條號不保證同一條文。
- split／merge／number reuse／restore／correction 必須有 curation decision。
- 每個 `rule_snapshot` 保存完整文字、結構、來源 locator 與 evidence。
- 日期／條號同檔 preflight 只建立 evidence candidate；不得據此自動建立
  `official_event_effect`、選 canonical side 或把前一版移入 history。

## WP05：藥品／ATC linkage

1. 從 NHI IODE `A21030000I-E41001-001` 取得整批 CSV；保存 exact bytes、
   retrieval time、HTTP metadata、SHA-256 與 source manifest。
2. 將每列載入 `nhi_drug_item_observation`；原始健保代碼、價格有效期間、
   ATC、給付章節與 exact URL 一律保留，不能只做 current-state upsert。
3. 品項到 ATC 是官方來源 assertion；品項到給付章節／URL 也是官方來源
   assertion。章節到 canonical `rule_identity`／`rule_snapshot` 仍須獨立
   resolution。
4. 給付規定 RSS/detail/attachment 若直接同時列出成分／商品與條號，另建立
   `drug_rule_link_evidence`；相同關係不必再強制追一份獨立藥品公告。只有
   relation、品項／ATC／強度或跨來源 discrepancy 未解時才補充搜尋。
5. 條文到 ATC 只由已解析產品列推導，保存 support count 與 source release；
   不把它宣稱成整個 ATC class 的適用範圍。
6. 每月 IODE snapshot 是可重建基線；INAE3000
   `POST /api/INAE3000/INAE3000S01/SQL0001` 作每週 freshness
   reconciliation，不覆寫或刪除舊 snapshot。
7. PostgreSQL 與 SQLite 使用相同
   `linkage_import_run`／`nhi_drug_item_observation`／
   `nhi_drug_rule_reference`／`drug_rule_link_evidence` logical contract。

實作與 live audit 見 [ATC 與 ICD-11 linkage](linkage.md)。

## WP06：重播與 diff

1. 從 verified cumulative full release 建 baseline。
2. 依官方 effective date 重播 event effects。
3. 到下一個 cumulative release 時，比對 rule set 與全文 hash。
4. 差異未解時，只阻擋受影響條文與時間區間。
5. 每個版本只和直接前版比較。
6. 同一 edge 在新版顯示「本版新增」，在舊版顯示「下一版刪除」。

## WP07：公開 release

每個資料 release 至少包含：

```text
manifest.json
dataset_release.jsonl
source_artifact.jsonl
official_event.jsonl
official_event_effect.jsonl
rule_identity.jsonl
rule_designation.jsonl
rule_snapshot.jsonl
snapshot_evidence.jsonl
comparison_edge.jsonl
diff_hunk.jsonl
drug_concept.jsonl
linkage_import_run.jsonl
nhi_drug_item_observation.jsonl
nhi_drug_rule_reference.jsonl
drug_atc_link.jsonl
indication.jsonl
rule_indication_link.jsonl
nhi-rule-history.sqlite
checksums.sha256
```

SQLite 由同一批 JSONL 建立，不是另一份手工維護資料。

## 更新操作

目前每一層都有獨立、fail-closed 的 CLI；尚未把它們包成一個會自動跨 gate
的 `update --all`：

```bash
make test

PYTHONPATH=src python3 -m nhi_rule_history.cli discover \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli discover \
  --plan sources/source-plan-v2.json --run-dir build/pass-b \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli compare-discovery \
  --pass-a build/pass-a --pass-b build/pass-b \
  --output build/pass-a/discovery-parity.json
PYTHONPATH=src python3 -m nhi_rule_history.cli fetch \
  --plan sources/source-plan-v2.json --run-dir build/pass-a \
  --allow-insecure-tls
PYTHONPATH=src python3 -m nhi_rule_history.cli verify-raw \
  --run-dir build/pass-a
PYTHONPATH=src python3 -m nhi_rule_history.cli parse-odt \
  --run-dir build/pass-a --stage-dir build/structural-RUN-UUID \
  --parse-run-id RUN-UUID
PYTHONPATH=src python3 -m nhi_rule_history.cli release-v2 \
  --run-dir build/pass-a --stage-dir build/structural-RUN-UUID \
  --source-plan sources/source-plan-v2.json \
  --eligibility-receipt \
    data/manifests/mohw-fint-2021-2026-v2/release-eligibility.json \
  --output-dir build/v2-evidence-release
```

取得 NHI 藥品／ATC／給付章節 raw snapshot：

```bash
PYTHONPATH=src python3 tools/fetch_nhi_drug_linkage.py \
  --output-dir build/nhi-drug-linkage
```

NHI IODE fetcher 預設且實跑皆使用 TLS 驗證；2026-07-27 live smoke 不需要
任何 insecure 例外。若 operator 因可重現的相容性需求明示
`--allow-insecure-tls`，工具會在 manifest 記錄該 transport；目前工具本身
不強制隔離，因此 operator 必須指定獨立的 `--output-dir`，且在以正常 TLS
或可驗證 CA bundle 重新取得並核對前，不得交給下游 loader 或 release。
工具不寫 PG，先產生 content-addressed raw 與 manifest，再由受控 loader
進 staging。

上方 FINT `discover`／`fetch` 命令中的 TLS 例外則只重現 2026-07-27 FINT
endpoint 與本機 Python trust store 的相容需求，必須由 operator 明示。
預設仍 fail closed；有可驗證 CA bundle 時改用 `--ca-file`。

套用 repo migration 後，`load-acquisition` 與 `load-structural` 各自會先完整
validate、在單一 transaction 寫入並 seal，再用新連線重算 count 與 row-set
fingerprint。`release-v2` 只在本地準備 raw tar.zst 與 structural JSONL.zst，
沒有發布網路路徑。

入口完成前，不建立假裝自動化的 schedule。每一個未實作步驟留在
[gap-register](gap-register.md)。

## Pull request gate

每次更新 PR 必須附：

- 前後 source manifest diff；
- 新增／改 bytes／消失的官方 URL；
- expected/fetched pages、rows、details、attachments；
- parser、event、identity、replay、diff issue counts；
- output fingerprint；
- SQLite `integrity_check` 與 row-count parity；
- 授權或第三方資料變更。

來源刪除或回溯修改不能直接從 history 消失；以 supersession 與 finding 表示。
