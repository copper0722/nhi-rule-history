# GPT Pro methodology v4 reconciliation

Source audit:
[`2026-07-29-methodology-v4-pro-audit-response.md`](2026-07-29-methodology-v4-pro-audit-response.md)

Initial audit verdict: `REPAIR_THEN_ACCEPT`.
Post-repair re-audit verdict: `ACCEPT`.

Re-audit response:
[`2026-07-29-methodology-v4-pro-reaudit-response.md`](2026-07-29-methodology-v4-pro-reaudit-response.md)

| Pro repair | Disposition in canonical method |
|---|---|
| R1 observation delta ≠ legal predecessor | Accepted. Annual/current files now create `clause_observation` and neutral deltas; only adjudicated versions receive direct-predecessor edges. |
| R2 separate three clocks | Accepted. `source_observed_at`, `source_edition_date`, and `legal_effective_from/to` are distinct; appearance windows do not become exact dates. |
| R3 neutral delta vocabulary | Accepted. Deterministic output is `appearance_observed`, `text_change_observed`, or `disappearance_observed`; create/amend/delete/restore/move require later review. |
| R4 explicit identity/lineage graph | Accepted. Source observations may exist before canonical identity; lineage includes continue, move, split, merge, replace, and unrelated number reuse. |
| R5 source selection/conflict policy | Accepted. Current chapter files remain the owner-selected current-text authority; 33 whole/chapter mismatches remain conflict observations and do not establish history. |
| R6 text/OCR fidelity layers | Accepted. Exact source text, comparison text, OCR observation, and display Markdown are separate; normalization policy is versioned and only affects comparison. |
| R7 per-clause completeness vector | Accepted. Source, observation, marker, identity, event, replay, and diff coverage are reported separately; no total confidence score substitutes for them. |
| R8 reader wording validator | Accepted. Reader output distinguishes verified effective date, bounded observation window, and declared-search not-found. It may not say “not found therefore absent” or call a source delta “this amendment.” |

## New evidence after the audit

The 84-06-20 FINT record and its complete 25-page scan were recovered after
the Pro response. This does not invalidate the repairs:

- the scan is a strong official source observation and closes the 84 baseline
  availability gap;
- it has no substantive text layer, so OCR remains unproofread observation;
- its comparison with the 96 edition is an observed delta, not proof of one
  direct 85-01-01 legal event;
- the exact 85-01-01 amendment remains
  `not_found_after_declared_search`.

## Remaining implementation work

The method is accepted as a publication contract, but the full canonical
history schema, identity adjudication, scan proofreading, replay, completeness
vectors, and reader wording validator are not yet fully implemented. No
complete-history claim is authorized.
