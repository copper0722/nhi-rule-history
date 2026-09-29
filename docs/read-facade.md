# nhi_rules：健保給付條文唯讀 schema 地圖

**結論：13 個唯讀 view，疊在既有 store 上；不搬資料、不新增寫入者、不是權威來源。**
先查 `release_state`，再引用任何數字。

```sql
select * from nhi_rules.release_state;                       -- 每一層現在讀哪一批 run
select view_name, answers, reads_schemas from nhi_rules.view_catalog;  -- 這張地圖本身
```

排序用 `order by sort_key`。全部 view 只讀、不可寫、不對 PUBLIC 開放。

## 四個問題，四組 view

| 問題 | view | 回答什麼 | 讀哪個 store |
|---|---|---|---|
| 條文怎麼寫？ | `current_clause` | 每條條文（publication 封存時的分章檔內容）：全文、標題、來源檔、雜湊 | `nhi_rule_history_publication` |
| | `current_clause_block` | 同一批條文拆成段落／清單／表格儲存格 | 同上 |
| | `current_clause_date` | 條文內出現的民國日期（文中標註） | 同上 |
| | `current_source_file` | 現行條文出自哪些官方分章檔、各供應幾條 | 同上 |
| 公告了什麼、何時生效？ | `announced_patch` | 已公告修正：條號、生效日、逐字修正文、公告字號、目前狀態（讀取當下算出，生效日當天會變） | `nhi_rule_history_announced`（使用中的 release）＋現行條文 |
| | `announced_composed_clause` | 修正併入現行條文後的完整條文（已審者） | `nhi_rule_history_announced` |
| | `announced_notice_effect` | 每則公告改了哪些條、是否已投影成 patch | `nhi_rule_history_announced` |
| 通則歷來怎麼改？ | `general_principle_version` | 通則每條的版本鏈：各文字狀態、出現在哪些版本 | `nhi_rule_history_clause` |
| | `general_principle_change` | 相鄰版本之間改了什麼（逐段新舊文） | 同上 |
| | `general_principle_edition` | 版本鏈用到的官方版本與來源檔雜湊 | `nhi_rule_history_edition` |
| 公告從哪來？ | `notice` | 官方 RSS 給付規定公告，及是否已進 announced release | `nhi_rule_history_update_queue`、`_update_ops`、`_announced` |
| 現在讀哪一批？ | `release_state` | 四層各自讀的 run、封存／最後觀察時間、數量 | 上列各 store |
| 這是什麼？ | `view_catalog` | 每個 view 的用途與實際讀取的 relation（取自資料庫依賴，不會與定義脫節） | 系統目錄 |

## 現場可示範的三個查詢

```sql
-- 1. 2.6.1 現在怎麼寫、出自哪個官方檔
select clause_code, display_title, source_label, source_url
from nhi_rules.current_clause where clause_code = '2.6.1';

-- 2. 10/1 生效的公告
select clause_code, effective_from, display_lifecycle, notice_reference
from nhi_rules.announced_patch
where effective_from = date '2026-10-01' order by sort_key;

-- 3. 通則第 4 條歷來的文字狀態
select version_no, first_seen_edition, last_seen_edition, left(version_text, 24) as head
from nhi_rules.general_principle_version where clause_code = '0.4' order by version_no;
```

## 它不能回答什麼

- **版本鏈只有通則（0.1–0.12）。** 第 1–15 章沒有歷史，因為條文歷史庫還沒建到那裡。
- **文中日期不是法定生效日。** `current_clause_date` 與 `text_annotation_dates` 是條文自己標的日期；
  通則版本的 `legal_effective_status` 一律 `not_claimed`。
- **`current_clause` 是 `release_state` 那一批封存的分章檔內容。** 官方之後發布的分章檔要重新載入才會進來；
  已公告或已生效、但還沒併入分章檔的修正，只出現在 `announced_patch`。
- **`announced_patch` 只讀使用中的 release。** 尚未投影的公告效果在 `announced_notice_effect`
  以 `projection_status` 標出。
- **沒有藥品品項、ATC、ICD 連結。** 那些在原 store，不在這一層。
- **不是權威來源。** 要改資料，走原 store 的 loader 與 migration。

## 維護

- Migration：`pg/migrations/2026-09-29_nhi_rule_history_read_facade_v28.sql`；回滾：同名 `.rollback.sql`
  （只刪 view 與 schema，RESTRICT，不動任何資料）。
- 讀回：`database/queries/read-facade-readback-v28.sql`（全部 `ok` 欄應為 `t`）。
- 通則版本鏈的 `change_kind` 是讀者看到的分類（最新封存的 diff run）；`source_change_kind` 是 store 當初記的分類，兩者可以不同。
- 每個 view 依賴它所選的欄位；日後要刪改來源 view 的欄位，先跑回滾。
