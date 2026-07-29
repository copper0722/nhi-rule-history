# Claude Fable current-publication v18 rereview

Model: `claude-fable-5`
Mode: repo read-only, no web, no subagents
Verdict: `ACCEPT_FOR_LIVE_STAGE`

## blocking findings

None.

Fable confirmed:

- every loader INSERT lists its target columns;
- activation is an append-only event log, identical replay does not append,
  and an older sealed run can be reactivated;
- the loader rereads the active view after commit;
- `whole_split_parity_status` is stored and fingerprint-bound;
- reconstructed versions count only sealed clause imports;
- publication runs can only be inserted as `loading`.

## remaining limitations

- SQL seal checks child counts; Python fresh verification owns the ordered
  row-set and output fingerprints.
- `parity_failed` may be activated. This is intentional here: the official
  chapter ODTs are the sole current-text authority, while the whole ODT is a
  non-authoritative cross-check. The actual status remains visible in PG.
- Concurrent activation can make the loader return `active=false`; callers
  must treat that as a failed activation outcome.
- `DROP SCHEMA CASCADE` rollback is restricted to disposable/recovery use.

The reviewer suggested expecting `parity_passed` in the live check. That single
expectation is not adopted: the sealed source receipt is known to be
`parity_failed` (606 matching, 33 mismatching clauses), and source policy v1
explicitly says whole-file mismatch does not block the chapter projection.
