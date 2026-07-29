# Clause normalization v25 post-verification reconciliation

## Audit chain

| Artifact | SHA-256 | Result |
|---|---|---|
| `2026-07-29-clause-normalization-v25-pro-post-verification-prompt.md` | `396a5f89b0de6bdf0e212181a6e73dae6fa59c107cad4215d033edb4193475f4` | Full production packet |
| `2026-07-29-clause-normalization-v25-pro-post-verification-response.md` | `dcd2fa51389fbcafb53740db4d236012a226ee8b68acc2035c0247def13352e9` | `REPAIR`, one Major |
| `2026-07-29-clause-normalization-v25-pro-composition-delta-prompt.md` | `ffa044c39a34569b3e745736a23f7a7a2a83983e2f6f1c04b03cc86e1cdd2322` | Single permitted evidence delta |
| `2026-07-29-clause-normalization-v25-pro-composition-delta-response.md` | `69fcd532eee33089b6220f09d63482a3b6e28276b4e4620978c34ee5fb7c5008` | `PASS` |

The same persistent GPT Pro conversation reviewed both packets. The second
request was limited to the sole Major from the first response.

## Major disposition

The first audit accepted the Work／Expression／source-span／table-state／exact
diff／sealing／portable-projection design and identified one missing proof:
the packet did not demonstrate how the 2026-09-01 future Expression became a
complete `verified_composite` when the amendment attachment ended Table 2 with
`(以下略)`.

Disposition: `ACCEPT_REPAIR_COMPLETE`.

A deterministic verifier was added:

`src/nhi_rule_history/clause_document_composition.py`

Its sealed evidence receipt is:

`2026-07-29-clause-normalization-v25-composition-verification.json`

The verifier proved:

- current Expression = `source_complete`;
- future Expression = `verified_composite`;
- content-addressed manifest ID/hash recomputes from all 406 components;
- component envelopes cover all 9,249 Unicode scalars and 13,983 UTF-8 bytes
  exactly once under the versioned two-LF assembly rule;
- all non-empty physical blocks replay their source spans exactly;
- the amendment `(以下略)` marker remains source evidence but is excluded from
  the complete Expression;
- the omitted Table-2 body owns zero amendment-source spans;
- the 70 inherited blocks exactly bind predecessor blocks 2–71 and the prior
  official source artifact;
- deterministic assembly reproduces the complete future text and seven-segment
  exact diff;
- API, canonical JSONL and freshly rebuilt SQLite preserve the same
  completeness, manifest and component provenance;
- SQLite `integrity_check` and `foreign_key_check` pass;
- the reader describes a composite and never calls the complete future text
  source-exact from the amendment attachment.

GPT Pro then stated: “The sole Major blocker is closed” and returned `PASS`.
No Critical or Major item remains open for this v25 canary and method.

## Non-blocking follow-up

The first response listed these Minors:

- supplementary-plane／combining-mark offset fixture;
- repeated-token deterministic diff tie-break fixture;
- duplicate-identical table-row alignment fixture;
- stable names and reasons for the seven environment-gated skips;
- explicit SQLite foreign-key receipt;
- screen-reader and non-color UI cases.

This closeout added the supplementary-plane／combining-mark regression and the
explicit SQLite foreign-key receipt. The other Minors remain ordinary
regression-hardening backlog; they do not reopen the release gate.

## Scope boundary

`PASS` applies to the v25 2.6.1 production canary and the normalization/diff
method. It does not mean the complete historical reimbursement-rule corpus has
been reconstructed. Historical coverage gaps remain governed by
`docs/gap-register.md` and `project.yaml`.
