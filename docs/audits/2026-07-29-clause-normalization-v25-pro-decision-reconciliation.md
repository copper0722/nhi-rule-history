# GPT Pro pre-deployment architecture reconciliation

## Control record

- Prompt:
  `2026-07-29-clause-normalization-v25-pro-decision-prompt.md`
- Response:
  `2026-07-29-clause-normalization-v25-pro-decision-response.md`
- Prompt SHA-256:
  `74ffed6339548ed5ff5efb9bb49740d514cc7be8be0e272dc025c71fa2619e0f`
- Response SHA-256:
  `5ce55d8a9f6c5f250ec59e75880c2e86cc3dbcd69f092bf746a0b1717473f582`
- Verdict: `REPAIR`
- State at prompt and response:
  - `nhi-rule-history` base:
    `7a352e9c1fb6abc625d0a7283d7ea715c0fe2a84`
  - `copper-panel` base:
    `d884e092bd0e8c694fa5947b3e3ecc5400d4b281`
  - `personal-website-s` base:
    `a2ff58d3726399fbb8b2fc2a38ad50f12645b8d2`
  - staged v25 migration SHA-256:
    `24d1526e95fbb08ddd0e2acb15650a6c3e37a449fcec3b61abfab5fbe111c180`
  - staged loader SHA-256:
    `32e2ab515e36c644a5032aa4e2ec3061f6cb5df52f8292f6011f24895dbcd7b2`
- Drift check: all five fingerprints were unchanged when the response was
  captured. The decision therefore applies to the reviewed staging state.
- Production effect at decision time: none. v25 had not been applied; API and
  subscriber production remained on their prior sealed releases.

## Dispositions

| Pro finding | Disposition | Required implementation |
|---|---|---|
| `work_node_key` conflates persistent Work identity and expression-local structure | `ACCEPT_REPAIR_PENDING` | Add persistent `node_work`, expression-local nodes, append-only identity assertions and many-to-many lineage. Keep `work_node_key` derived only. |
| `identity_status` mixes resolution and basis | `ACCEPT_REPAIR_PENDING` | Split into `identity_resolution_status` and `identity_basis`; only `verified` can drive stable alignment or move labels. |
| one block → one node conservation is invalid | `ACCEPT_REPAIR_PENDING` | Replace component-block ownership with scalar and UTF-8 byte source spans; seal total, non-overlapping primary coverage and exact reconstruction. |
| list items and tables are valid first-class nodes only under admitted boundaries | `ACCEPT_REPAIR_PENDING` | Keep first-class point/table nodes; fall back to source-exact paragraph plus `unresolved_structure` when a marker boundary is not admitted. |
| table `source/covered/implicit_carry/empty` overstates source evidence | `ACCEPT_REPAIR_PENDING` | Split physical and logical states; add explicit repeated/omitted/unresolved; prohibit carried cells from owning source-text hashes. |
| complete-clause reader lacks an explicit completeness contract | `ACCEPT_REPAIR_PENDING` | Add expression completeness, immutable composite manifest and selector gate; patch-only/partial/unresolved never enter complete reader. |
| greatest version/load order does not establish reader state | `ACCEPT_REPAIR_PENDING` | Add explicit effective selector states and append-only activation/deactivation. Prior versions are not physically moved. |
| adjacency must be evidence-backed | `ACCEPT_REPAIR_PENDING` | Add expression relation with `direct_predecessor_verified`, `previous_available_expression_only`, `unresolved`, `conflicted`; gate published direct-predecessor diffs. |
| ignored-change policy is unsafe | `ACCEPT_REPAIR_PENDING` | Preserve every exact difference. Store display classification separately under node-type-specific versioned rules; no global quote/full-width suppression. |
| inline diff needs exact reconstruction | `ACCEPT_REPAIR_PENDING` | Store deterministic `inline_diff_segment` with scalar/byte ranges and both-side replay invariants. |
| table coordinates are not cross-version identity | `ACCEPT_REPAIR_PENDING` | Align by verified Work identity, then unique row signature, then bounded deterministic candidates; otherwise unresolved. |
| unresolved alignment must remain visible | `ACCEPT_REPAIR_PENDING` | Render old-only/new-only or an unresolved notice; never fabricate move/rewrite. |
| agent boundary needs explicit identity/completeness prohibitions | `ACCEPT_DOC_COMPLETE` | Methodology now explicitly forbids an agent from assigning verified Work identity, verified adjacency, verified composite completeness or formatting equivalence. |
| split desktop + unified mobile is appropriate with accessibility requirements | `ACCEPT_REPAIR_PENDING` | Use the same normalized data; add context counts, expand-all, text/icon/style status, keyboard controls, screen-reader labels and stable mobile DOM order. |
| v25 must be shadow, run-scoped, sealed and activation-based | `ACCEPT_REPAIR_PENDING` | Separate normalization and diff runs, bind all fingerprints, fresh recount, append-only activation and non-destructive recovery. |
| portable projection parity is required | `ACCEPT_REPAIR_PENDING` | Add PostgreSQL ↔ JSONL ↔ SQLite logical-row parity receipt before release. |
| mutation and adversarial fixture coverage is incomplete | `ACCEPT_REPAIR_PENDING` | Add child/parent DML, TRUNCATE, upsert, COPY and activation-history mutation probes plus the listed identity/span/table/diff/UI counterexamples. |

No Pro finding was rejected. There are no unresolved `PROBE_REQUIRED`
dispositions. Implementation and post-verification review remain open.

## Release gate

The following remain hard blockers:

1. all `ACCEPT_REPAIR_PENDING` rows become implemented and deterministically
   verified;
2. v25 is applied as an additive shadow migration and loaded without changing
   the existing production reader;
3. fresh PostgreSQL recount, source reconstruction, fingerprints, API/export
   parity, desktop/mobile visual audit and committed deactivate/reactivate drill
   pass;
4. the complete verification packet receives the required GPT Pro
   post-verification audit.

## Implementation verification after the decision

The `ACCEPT_REPAIR_PENDING` items above were implemented as an additive v25
shadow model and verified against production:

- active normalization:
  `16d5abd5-a8aa-5d35-8a4d-3e3edabb7598`;
- active exact diff:
  `cc4acbaf-559d-5148-9773-f5f023e36561`;
- two complete expressions, 478 source blocks, 87 nodes, six tables,
  486 cells, 85 lineage rows and seven exact inline segments;
- 83 subnode lineages remain explicitly `alignment_unresolved`; they do not
  emit fabricated deletion/insertion hunks;
- one Work-level exact expression hunk reconstructs both complete sides.
  Its material segments are insertion-only, so display classification is
  `本版新增`;
- all UPDATE, DELETE, INSERT, UPSERT, COPY, TRUNCATE and control-event mutation
  probes were rejected by PostgreSQL:
  `2026-07-29-clause-normalization-v25-mutation-matrix.json`;
- a committed deactivate was externally observed as API 503; a separately
  committed reactivate restored byte-identical API output:
  `2026-07-29-clause-normalization-v25-two-transaction-drill.json`;
- 20 PostgreSQL relations exported 2,238 canonical JSONL logical rows and
  rebuilt a SQLite projection with exact per-table parity:
  `2026-07-29-clause-document-v25-portable-parity.json`.

Desktop/mobile live reader verification and the required GPT Pro
post-verification decision remain the final gates.
