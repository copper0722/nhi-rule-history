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
  -> official event/effect ledger
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

## WP02：結構解析

已實作的 ODT stage parser 會保存：

- 段落、標題、表格、列、儲存格、covered／empty cell；
- 原文字、normalized search text、hash 與長度；
- XML element index、repeat instance 與完整 locator；
- dotted-designation occurrence candidate；
- deterministic issue ledger。

它不推論法律生效日、不以條號建立 canonical identity，也不計算跨版 diff。

## WP03：公告事件

一則公告與其對條文的影響分開：

- `official_event`：發文字號、主旨、發文／刊登／生效資訊及附件。
- `official_event_effect`：對某一條文的 create、amend、delete、restore、
  rename、move、split、merge 或 correction。

每個 accepted effective date 必須指回官方 locator。公告列出的日期、附件正文
日期與實際生效日分欄保存。

## WP04：條文身分與版本

- `rule_identity` 使用永久 ID。
- `rule_designation` 保存條號、標題與有效區間。
- 相同條號不保證同一條文。
- split／merge／number reuse／restore／correction 必須有 curation decision。
- 每個 `rule_snapshot` 保存完整文字、結構、來源 locator 與 evidence。

## WP05：重播與 diff

1. 從 verified cumulative full release 建 baseline。
2. 依官方 effective date 重播 event effects。
3. 到下一個 cumulative release 時，比對 rule set 與全文 hash。
4. 差異未解時，只阻擋受影響條文與時間區間。
5. 每個版本只和直接前版比較。
6. 同一 edge 在新版顯示「本版新增」，在舊版顯示「下一版刪除」。

## WP06：公開 release

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
drug_atc_link.jsonl
indication.jsonl
rule_indication_link.jsonl
nhi-rule-history.sqlite
checksums.sha256
```

SQLite 由同一批 JSONL 建立，不是另一份手工維護資料。

## 更新操作

現階段：

```bash
make test
make validate-manifest
make sqlite-smoke
```

尚待實作的單一入口：

```bash
python -m nhi_rule_history update --source all --out build/run-YYYYMMDD
python -m nhi_rule_history verify --run build/run-YYYYMMDD
python -m nhi_rule_history release --run build/run-YYYYMMDD
```

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
