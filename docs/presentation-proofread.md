# Presentation-only proofread layer

Status: accepted design; PostgreSQL migration and activation workflow pending.

## Why this is a separate layer

The project has two different proofread problems and must not combine them:

| Layer | May correct source characters? | Purpose |
|---|---:|---|
| `nhi_rule_history_transcript` | May correct extracted/OCR transcript characters to match the immutable visual source, after full visual review | Faithfully transcribe scans, OCR, image-only tables and extraction errors |
| `nhi_rule_history_presentation` | No | Re-express an already canonical current text as headings, lists, tables and paragraphs |

A presentation revision is not another legal-text version. It cannot change a
letter, digit, unit, date, punctuation mark, quote form, full-/half-width form,
or whitespace character in the canonical source stream.

## Triage before agent work

Every reader defect first receives one of four dispositions:

1. `renderer_bug`: PostgreSQL already has sufficient structure but the API or
   browser drops or misrenders it. Fix the deterministic renderer.
2. `source_structure_loss`: the source parser failed to retain structure that
   is present in the official artifact. Re-run a versioned parser; do not hand
   edit the text.
3. `presentation_only_proofread`: source character order is authoritative, but
   a human/agent must propose block boundaries, hierarchy or layout.
4. `source_transcript_proofread`: OCR or visual-source characters themselves
   require full-source transcription and independent review.

Existing source locators always win over an agent proposal. ODT table
coordinates, row/column spans and list metadata must be projected directly
when they are sufficient.

A presentation run is admitted only with
`triage_disposition=presentation_only_proofread` and a deterministic
`source_structure_sufficiency_receipt` proving that the versioned parser has
retained all structure available in the official artifact. A
`source_structure_loss` disposition blocks presentation work and returns the
artifact to parser development. An agent may not manufacture an official list
marker, item path, start value or continuation that the artifact/parser did not
preserve.

## PostgreSQL model

The planned schema is `nhi_rule_history_presentation`:

```text
presentation_run
clause_revision
presentation_block
block_source_span
review
activation
```

Minimum responsibilities:

- `presentation_run`: base publication run, policy and renderer versions,
  producer identity/tool/model, reason, input/output receipt and state.
- `clause_revision`: clause identity, base clause/block hashes, source/display
  stream hashes, superseded revision and equivalence decision.
- `presentation_block`: stable order, parent, typed kind and level. Typed
  metadata covers list style/depth and table/row/cell coordinates.
- `block_source_span`: maps every presentation leaf to an exact source block
  and zero-based Unicode code-point interval.
- `review`: independent reviewer, decision, findings and reviewed revision
  hash.
- `activation`: append-only per-clause selection of one sealed revision.

`clause_revision(publication_run_id, clause_code)` has a composite foreign key
to the current publication clause. `block_source_span` has a composite foreign
key to
`current_clause_block(run_id, clause_code, block_order)`.
`block_source_span` must reference the source tuple
`(publication_run_id, clause_code, source_block_order)`, not
`source_block_id` alone.

The migration must enforce producer/reviewer separation and the
sealed+approved+equivalent activation rule with cross-table triggers; a prose
rule or single-table `CHECK` is not sufficient.

## Strict character-equivalence invariant

The base stream is built from `current_clause_block.raw_text` in block order.
It does not use `current_clause.raw_text`, because the latter inserts projection
line feeds between blocks.

The display stream is the ordered concatenation of all referenced source
spans. DOM/CSS chrome—bullets, table borders, disclosure icons and headings—is
not part of that stream.

Every reader-visible canonical text segment is rebuilt from those source
substrings at seal, export and request time. An agent may submit typed layout
metadata, never free-form display text. If the database materializes display
text for performance, a trigger/loader must prove it equals the span
concatenation. Generated bullets and layout chrome are explicitly
`generated_noncontent` and are excluded from search, diff, copy and canonical
streams.

Both streams may undergo only:

1. UTF-8 decoding;
2. CRLF or CR normalization to LF;
3. Unicode NFC normalization.

They must then be equal code point for code point and have the same SHA-256.
NFKC, case folding, whitespace deletion, quote/punctuation deletion,
full-/half-width conversion and reordering are forbidden.

Every source code point must be covered exactly once, monotonically, without a
gap or overlap. An empty source block may have one `0..0` span and exist only
as a structural empty cell.

## Seal and activation gates

A revision cannot be sealed or activated when any of these is true:

- character equivalence fails or span coverage is not exactly 100%;
- producer and reviewer are the same actor, or review is not approved;
- any member of the activation key is stale:
  `base_publication_run_id + clause_code + base_raw_text_sha256 +
  ordered_block_receipt_sha256`;
- table coordinates overlap or spans/repeats/nesting remain ambiguous;
- list structure or marker continuation is guessed without source evidence;
- a blocking parser issue is still open;
- golden renderer, accessibility or DOM-stream tests fail.

When any activation-key field changes, including an ordered block/locator
receipt change with identical visible text, the API must ignore the old
presentation revision and return `raw_fallback`. It must never silently reuse a
layout from another legal-text version or parser structure.

## API projection

The typed rendering response adds:

```json
{
  "presentation": {
    "mode": "sealed_layout",
    "revision_id": "…",
    "base_publication_run_id": "…",
    "base_text_sha256": "…",
    "ordered_block_receipt_sha256": "…",
    "equivalence_policy": "strict-utf8-lf-nfc/v1",
    "review_status": "approved",
    "sealed_at": "…"
  },
  "blocks": [
    {
      "kind": "table_cell",
      "level": 0,
      "source_spans": [],
      "table": {
        "table_index": 0,
        "row_index": 1,
        "cell_index": 2,
        "rowspan": 1,
        "colspan": 1
      }
    }
  ]
}
```

The browser renders typed blocks only. It does not infer table or legal-list
structure from text. If no sealed presentation revision exists, the API
returns `presentation.mode=raw_fallback` and preserves the canonical text.

## Adversarial release fixtures

At minimum, every release must include:

- `0.4`: semantic links, compound conditions and date annotations;
- `2.6.1`: multi-column table with row spans;
- `8.2.4`: nested clause/list structure;
- a clause containing tabs and inline breaks;
- a clause containing an empty covered table cell;
- a mutation suite proving that any changed character, digit, punctuation,
  quote or width form fails equivalence, while block-boundary split/merge and
  typed kind/indent/table-wrapper changes pass only when the ordered
  source-span sequence is unchanged.
