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
| PostgreSQL staging physical size | 約 735 MB |

PG physical size 包含 213,512 rows、JSON locator、indexes 與 page overhead，不是
公開 export 的預期大小。normalized JSONL 與 SQLite 會以 logical tables 輸出。

## Attribution

每個資料 release 必須包含：

> 資料來源：衛生福利部中央健康保險署

並保留 source page、official URL、下載時間與 content hash。
