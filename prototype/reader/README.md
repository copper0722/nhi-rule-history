# Single-clause `通則` reader template

This PG-driven reader uses **one clause as one page and one version chain**.
`通則` is the official source label. `chapter:00` and `0.1`–`0.12` are
project-assigned navigation codes and are never presented as official article
numbers.

The browser first reads
[`data/clauses/index.json`](data/clauses/index.json), then loads exactly one
generated clause projection such as
[`data/clauses/0.4.json`](data/clauses/0.4.json):

- the selected clause's latest full text is shown once at the top;
- every older row shows only the diff to that clause's next distinct text state;
- red means text removed in the next state and green means text added there;
- unchanged annual source observations remain in PostgreSQL but do not create
  duplicate clause versions;
- search covers all 12 current `通則` clauses by medicine, condition, test or
  rule wording;
- the two-column history becomes a stacked layout on mobile.

PostgreSQL `nhi_rule_history_clause` is the sole writable clause-history
authority. `nhi_rule_history_edition` remains the upstream source-edition
container. Reader JSON is disposable and can be regenerated with the public
JSONL and SQLite projections from the same sealed import:

```bash
PYTHONPATH=src python3 tools/rebuild_chapter00_clauses.py \
  --dsn "$DATABASE_URL" \
  --jsonl-dir data/templates/chapter-00-clauses \
  --reader-dir prototype/reader/data/clauses \
  --sqlite-output /tmp/nhi-rule-history-chapter-00-clauses.sqlite
```

The present dataset is complete only for each clause's observations within the
declared set of 15 cumulative source editions. It does not assert that the
official source universe is closed, that edition labels or in-text dates are
legal effective dates, or that adjacent captured text states are direct legal
predecessors.
