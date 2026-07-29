# GPT Pro closure review: v25 composition provenance

This is the single evidence delta requested by your post-verification `REPAIR`
verdict. Please assess only whether the one remaining Major blocker is now
closed. The implementation and active PostgreSQL runs were not replaced or
reparsed.

## Prior audit identity

- Post-verification prompt SHA-256:
  `396a5f89b0de6bdf0e212181a6e73dae6fa59c107cad4215d033edb4193475f4`
- Post-verification response SHA-256:
  `dcd2fa51389fbcafb53740db4d236012a226ee8b68acc2035c0247def13352e9`
- Verdict: `REPAIR`
- Sole Major: completeness provenance for the future 2026-09-01 Expression
  was not demonstrated in the packet.

## What the evidence check found

The data existed in the sealed PG/API/portable projection; the prior packet
failed to expose and recompute it. A new deterministic verifier now consumes
the live read-only API plus the tracked canonical JSONL, rebuilds SQLite from
that JSONL, and fails closed on any mismatch.

The verifier is:

`src/nhi_rule_history/clause_document_composition.py`

The complete machine-readable receipt is:

`docs/audits/2026-07-29-clause-normalization-v25-composition-verification.json`

Receipt SHA-256:

`a686fcee075fb6aed60cc2f8f2c20dd4ee3e8488c1dd665c1e9e99ed47375d2a`

## Falsifiable results

### Expression completeness

- current Expression:
  - ID `c966cd73-2b6f-5d2e-a9d1-abf4b2246c36`
  - `source_complete`
  - source artifact SHA-256
    `3017546fe9b8db045012bb4e1135b31f8f8bc4038f762b73e5addcbe9312f506`
  - exact text SHA-256
    `0fba3dfd13c4d4815067c629bbd466d84e6ad6883746dc6413abdf78e40efd0b`
- future Expression:
  - ID `08491b25-337e-515d-b327-7106c4bfcedb`
  - `verified_composite`
  - exact text SHA-256
    `7f371d5669da201c6dd7733d7883b81a851f961edd339773297ac04bb240e57f`
  - completeness receipt SHA-256
    `7d97721f48cda7f16e0c7162305eca17d0d760fc55ddbd0d31cc4fc99dc98adc`

### Content-addressed composition manifest

- manifest identifier:
  `sha256:b241aea8f2fad47d35a6eaf334ebe7677cafe82ab92d5a5f888ef5655a98e8db`
- stored manifest SHA-256:
  `b241aea8f2fad47d35a6eaf334ebe7677cafe82ab92d5a5f888ef5655a98e8db`
- recomputation from all ordered manifest rows: exact match.
- rule:
  `nhi-rule-history/2.6.1-amendment-plus-inherited-remainder/1.0.0`
- assembly separator: two LF characters between ordered blocks.
- 406 ordered components:
  - blocks 0–335: `amendment_exact`;
  - blocks 336–405: `predecessor_inherited`.
- every component binds:
  - assembly ordinal;
  - component role/lane;
  - source artifact SHA-256;
  - source block ID;
  - source locator;
  - exact text SHA-256;
  - scalar and UTF-8 byte assembly range.
- the 406 range envelopes cover the 9,249-scalar / 13,983-byte future
  Expression exactly once.
- component binding fingerprint:
  `cff582a98f1d7820227a6b871b600e026df2514cb817309ceeb2872a281caa61`.

### Source-span and omitted-remainder proof

- 398 non-empty physical blocks have 398 exact source spans.
- Each non-empty block replays without scalar or UTF-8 byte gap/overlap.
- Eight physically empty table cells own no fabricated span.
- The official amendment has 337 observed components. Component 336 is the
  explicit `(以下略)` marker, source block
  `78d57c1639ebc37637f816a205a93ea884b061fc3608060c9940ffde60a60c79`.
- That omission marker is excluded from the complete future Expression.
- The amendment supplies its Table-2 heading at composed block 335, but zero
  amendment-source spans for the omitted Table-2 body.
- The next 70 blocks bind predecessor publication
  `a707d13a-0b06-5dfe-96b7-6d107ab8793f`, predecessor block orders 2–71 and
  source artifact
  `3017546fe9b8db045012bb4e1135b31f8f8bc4038f762b73e5addcbe9312f506`.
- For every inherited block, source block ID, raw text and raw-text SHA-256
  exactly equal that predecessor block.

### Normalization, diff and portable projections

- deterministic assembly equals both the composed-clause text and v25 future
  Expression text.
- normalization run:
  `16d5abd5-a8aa-5d35-8a4d-3e3edabb7598`;
  API output/sealed fingerprints equal the tracked JSONL run row.
- exact diff run:
  `cc4acbaf-559d-5148-9773-f5f023e36561`;
  one hunk, seven segments; exact old/new replay passed.
- live API payload SHA-256:
  `f14b6e32bb32e887e1fb19c00c7eb678f4f373342ab37fe62a4af585ff69f104`.
- API and canonical JSONL expose completeness, composition-manifest hash and
  all source-block component provenance.
- SQLite was freshly rebuilt from the JSONL:
  - 20 tables;
  - 2,238 logical rows;
  - logical row parity passed;
  - `integrity_check=ok`;
  - `foreign_key_check` returned no rows;
  - SQLite SHA-256
    `87512d7d2bb8c9cd4b8e1a79c8d332490db424f0105b7e49fec0db225a78085a`.

### Reader claim boundary

The reader note says the complete version is assembled from announcement blocks
and an unchanged predecessor remainder. It does not call the complete future
text source-exact from the amendment attachment.

## Regression result

All 495 repository tests were replayed after adding the verifier and explicit
SQLite foreign-key receipt: 488 passed, seven environment-gated tests skipped,
zero failed.

## Closure request

Does this deterministic receipt close your sole Major blocker for the v25
canary and method?

End with exactly one verdict:

- `PASS` — the completeness-provenance Major is closed; or
- `REPAIR` — identify only a still-unmet Critical/Major acceptance condition
  from your prior test and point to the exact failed field or invariant.

Do not promote the previously listed Minor improvements into release blockers.
