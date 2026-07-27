# Data layout

## Git-tracked

```text
data/
  manifests/       official URL, filename, bytes, SHA-256, licence
  normalized/      future compact JSONL dataset releases
  samples/         synthetic or explicitly labelled canaries
```

## GitHub Release assets

```text
official source binaries
portable SQLite snapshots
checksums.sha256
release manifest
```

官方 binary 不在每次更新時重複進 Git history。`filename` 保留官方原名；
`release_asset_name` 記錄 GitHub 實際採用的安全檔名。release asset 的
byte length 與 SHA-256 必須和 Git-tracked manifest 相符。

## Current size

| Layer | Size |
|---|---:|
| 14 official ODTs | 49,709,507 bytes |
| largest ODT | 8,164,050 bytes |
| parsed structural text | 5,902,629 bytes |
| v2 unique raw artifacts | 85,642,128 bytes |
| v2 raw tar.zst (prepared partial evidence) | 67,161,191 bytes |
| v2 structural JSONL | 71,936,797 bytes |
| v2 structural JSONL.zst (3 files) | 4,241,637 bytes |
| v1 SQLite (prepared) | 1,102,200,832 bytes |
| NHI IODE drug-item/ATC CSV (2026-07-27 snapshot) | 96,799,113 bytes |

公開 Git 只追蹤 v2 的 5.2 MB acquisition metadata／manifest。raw binaries、
大型 structural JSONL 與 SQLite 使用 Release assets；上述單一最大檔仍低於
GitHub Release 的 2 GiB asset ceiling。v2 SQLite 尚未生成，不能以 v1
snapshot 代替。

IODE drug-item CSV 每月保留一份 content-addressed raw asset 與 Git-tracked
manifest；不能只把新檔 upsert 到 current table 後丟掉舊 bytes。相同 SHA
不重複上傳，不同 SHA 才建立新 snapshot。row-level normalized projection
可進 JSONL／SQLite Release asset，Git 只保留 audit 與小型 fixture。

## Attribution

每個資料 release 必須包含：

> 資料來源：衛生福利部中央健康保險署

並保留 source page、official URL、下載時間與 content hash。
