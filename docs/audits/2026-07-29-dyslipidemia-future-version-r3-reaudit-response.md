# GPT Pro R3 remediation re-audit response

Captured unchanged from the persistent Agentic Workflow conversation on
2026-07-29.

## Decision

`REPAIR`

Three of the four prior blockers are closed. The packet sufficiently closes:

- effective-date fail-closed behavior through append-only resolution states and
  suppression at `effective_date_reached_unresolved`;
- executable-model coverage through 34/34 predicate provenance, 21/21 branch
  truth-state fixtures, category-priority tests, contradiction handling, and
  all six LDL boundaries;
- deployed privacy through the sentinel canary showing no post-interaction
  requests, URL/storage mutation, telemetry, or session-replay transmission.

The vertical-card semantic parity is also adequately demonstrated.

The production rollback proof is not yet operationally complete. The
`deactivate` event and subsequent `activate` event were committed in one
transaction. Under PostgreSQL transaction visibility, another API/exporter
session cannot observe the intermediate deactivated state. After commit, the
newest visible event is already `activate`. The in-transaction view checks prove
SQL selection semantics, but they do not prove that a committed rollback
actually removes the patch from the authenticated API or paid projection.

## Smallest required repair

Perform a two-commit end-to-end rollback drill.

1. In transaction A, append `deactivate` and commit.
2. From fresh, separate sessions verify:
   - `v_active_run` and `v_public_clause_patch` return zero;
   - the authenticated announced-rule API exposes no active patch or decision
     aid;
   - the subscriber projection/sync consumes that state and no longer serves
     the patch;
   - all sealed rows, counts, hashes, and fingerprints remain unchanged;
   - the committed deactivation receipt remains present.
3. In transaction B, append `activate` and commit.
4. Verify from fresh sessions that:
   - the identical run and sealed fingerprint return without reload;
   - the authenticated JSON/output fingerprint equals the pre-drill value;
   - the paid projection is restored without stale-cache mixing.

No schema redesign, source reparse, model rebuild, or rescan is indicated. The
remaining issue is a bounded recovery-evidence gap. Once the separately
committed deactivate/reactivate drill passes, the supplied architecture and
release evidence support `GO` for the narrowly claimed 2.6.1 future amendment
patch and Table-1 LDL-C threshold helper.
