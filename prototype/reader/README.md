# `通則` reader template

This is the first PG-driven one-page history template. It displays the official
label `通則`; `chapter:00` is only a project-assigned navigation code.

The page reads the generated
[`data/chapter-00-reader.json`](data/chapter-00-reader.json). It contains no
hand-authored clause text or browser-side diff inference:

- the latest observed official edition is shown in full;
- older rows show only the deterministic diff to the next captured official
  cumulative edition;
- removed and added text use labels as well as red/green styling;
- editions with no substantive text change remain visible;
- search highlighting uses a separate visual channel;
- the two-column timeline becomes a stacked timeline on mobile.

PostgreSQL `nhi_rule_history_edition` is the sole writable authority. The reader
JSON is a disposable projection of one sealed import and can be regenerated in
one command:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00 \
  --reader-json prototype/reader/data/chapter-00-reader.json
```

The present dataset is complete only for its declared set of 15 official
cumulative editions. It does not assert that the official source universe is
closed, that edition labels are legal effective dates, or that adjacent
captured editions are direct legal predecessors.
