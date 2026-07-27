# Canonical promotion independent adversarial review

Date: 2026-07-27  
Scope: uncommitted
`2026-07-28_nhi_rule_history_{canonical,promotion}_v1` migrations  
Live PostgreSQL apply: **not performed**  
Disposition: **blocked for live deployment**

## Test state

The disposable-PostgreSQL migration suite passed 28/28 tests. Independent
adversarial checks nevertheless found five High findings. A green declared
suite therefore does not authorize deployment.

## Blocking findings

1. **Official attachment inventory can omit a PDF by role.** The current
   calculation only counts selected `official_*` roles. A release-linked PDF
   classified as `supporting` was ignored, and a declaration span saying
   `ODT、PDF` was accepted while structured formats asserted ODT-only.
   Promotion must inventory every release-linked official attachment regardless
   of role and derive format availability deterministically.
2. **Ordered event-list equality is not per-rule replay.** Exact count, order,
   event identities and stream fingerprint can pass even when a companion
   rule's verified `after` hash is absent from the post anchor. Replay must walk
   every rule from its pre-anchor hash through each verified before/after edge
   and end at the post-anchor hash. Same-day order needs authoritative evidence,
   not lexical event ID order.
3. **Durability drift is outside the structural seal.** Changing an immutable
   receipt table to `UNLOGGED` survived migration reapplication. The seal must
   assert/fingerprint permanent persistence and other durability-relevant
   relation state.
4. **Owner-role membership is not sealed.** A LOGIN granted membership in the
   owner role survived reapplication and could perform direct canonical DML.
   The owner role must have no members; relevant memberships need explicit
   fingerprinting and capability-role allowlists.
5. **Future publication/document dates can promote.** A case with a past
   effective date but future publication and document dates succeeded. Both
   fields require exact source spans, Taiwan-date gates and documented ordering;
   the effective-date span locator must equal the canonical locator.

## Closed items retained

- Rollback obtains `ACCESS EXCLUSIVE` locks before emptiness checks and fails
  safely when an authenticated writer commits a row.
- Disabled-trigger and RLS drift are detected.
- Producer, reviewer and executor are separate authenticated sessions; executor
  identity is preserved in the immutable receipt.
- Mapping coverage must equal one, `publishable` is unrepresentable, and
  atomic/idempotent paths pass for the reviewed scope.

## External bootstrap boundary

Even after the five SQL findings close, live deployment remains blocked until
an external deterministic gate recomputes attachment inventory, exact spans
and artifact hashes from immutable official bytes; proves source-universe
coverage to the declared cut; and binds canonical anchor fingerprints to those
receipts. PostgreSQL assertions cannot manufacture evidence that acquisition
never collected.

## Severity

- Critical: 0
- High: 5
- Medium: 2

Safe to include in a GPT Pro audit packet: yes, with all five High findings
marked blocking. Safe for live deployment or canonical promotion: no.
