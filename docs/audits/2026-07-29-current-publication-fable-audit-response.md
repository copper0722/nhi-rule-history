# Claude Fable current-publication v18 audit

Model: `claude-fable-5`
Mode: repo read-only, no web, no subagents
Disposition: `ACCEPT_FOR_LIVE_STAGE`

## blocking findings

None.

The reviewer independently checked the 25/10/6-column insert order, JSONB
placeholders, source foreign keys, insert→seal→activate transaction boundary,
idempotent replay, sealed-row guards, fresh fingerprint verification and the
owner-approved expected/missing formula.

## non-blocking findings and disposition

1. INSERT statements relied on positional column order. Repaired by listing
   every target column.
2. A run could not be reactivated after another run became active. Repaired by
   making activation an append-only event log; identical replay does not append
   another activation, but a prior sealed run can be activated again.
3. Whole/split parity status was not in the publication receipt. Repaired by
   storing `whole_split_parity_status`.
4. Reconstructed-version counts did not explicitly filter sealed imports.
   Repaired by joining `nhi_rule_history_clause.import_run` with
   `state='sealed'`.
5. Slash-form ROC dates define the current owner policy; dot-form and Chinese
   dates are outside this metric. This remains an explicit policy boundary.
6. Direct INSERT of a row already marked sealed was possible. Repaired with a
   database insert guard requiring `loading`.
7. Parent clause spans contain child clauses; aggregate expected counts
   therefore describe canonical reader pages, not unique legal events. This is
   retained and documented.
8. Loader coverage was initially mostly disposable integration evidence.
   Focused pure tests remain, plus two complete disposable migration/load
   exercises and the full repository suite.
9. Rollback drops the rebuildable read-model schema. This remains intentional
   and is restricted to disposable/recovery use.

## minimum live verification requested

- Verify sealed structural and acquisition parents plus the active source
  authority policy.
- Load counts `639 / 13,874 / 3,487`.
- Verify inventory `3,512 / 656 / 2,861 / 440 / 199 / 5`.
- Replay and confirm identical run with `already_loaded=true`.
- Reject sealed child mutation and activation of a loading run.
- Verify active run/view and sealed canonical-version denominator.

The repairs above are being sent through a narrow rereview before live apply.
