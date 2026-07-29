# GPT Pro work packet — 84 年《全民健康保險藥品使用規範》

This is a public Taiwanese government document. The attached PDF is the exact
25-page official scan:

- title: `全民健康保險藥品使用規範`
- source document: `健保醫字第84010140號`
- document date: ROC 84-06-20
- PDF SHA-256:
  `f773cf6eeb9c413a92fae9bf543c5f1ff161726142fff802848292d00064b4d2`
- the scan has no substantive native text layer

The second attachment is the official `96年7月版` ODT, SHA-256
`9d6212ca37442d5c07e6df2e601e8d5ac5c4a772c4e09ae985a005178205f9b7`.
It is supplied only as a later source observation for numbering comparison.

Please read all 25 scan pages visually. OCR may be used as a draft, but every
character, punctuation mark, Latin term, number, unit, heading and list level
must be checked against the page image. Do not silently repair source wording.
When a character genuinely cannot be resolved, retain a visible marker such as
`⟦unclear:p12:line7⟧`; never guess.

Produce three downloadable UTF-8 files rather than placing the full
transcription in the chat:

1. `proofread-84.md`
   - complete faithful transcription in page order;
   - insert `<!-- source_page: N -->` at every page boundary;
   - preserve section headings, numbered items, tables/lists and continuation
     across pages;
   - no summary and no omitted repeated text.

2. `clauses-84.jsonl`
   - one JSON object per top-level substantive source rule;
   - this is source segmentation, not a legal identity decision;
   - required fields:
     `source_segment_id`, `source_page_start`, `source_page_end`,
     `section_path`, `designation_raw`, `heading_raw`, `exact_text`,
     `substructure`, `literal_deleted_marker`, `uncertainties`;
   - `source_segment_id` is only a local 84-scan locator such as
     `84:p04:antimicrobial:1`; do not reuse a 96/current rule ID;
   - keep subitems inside their owning top-level rule while recording their
     raw labels in `substructure`;
   - if the scan literally prints `已刪除`, `刪除`, `停止適用` or an equivalent
     placeholder, preserve the exact words and set
     `literal_deleted_marker=true`; absence from the later file is not such a
     marker.

3. `84-to-96-lineage-analysis.md`
   - inventory the designation systems visibly used in the 84 scan;
   - compare every 84 source segment with the supplied 96 official ODT;
   - use only these candidate dispositions:
     `same_designation_text_continuity_candidate`,
     `renumber_or_move_candidate`, `absent_in_96_observation`,
     `new_in_96_observation`, `ambiguous`;
   - cite exact 84 page(s), raw 84 designation and the 96 designation/text
     span supporting each comparison;
   - explicitly answer:
     a. Does the 84 scan already use the later dotted decimal code system?
     b. Is there direct source evidence that a wholesale or partial recoding
        happened before the 96 edition?
     c. Does the 84 scan contain literal deletion placeholders?
     d. Which apparent 84→96 matches are strong enough only for deterministic
        candidate generation, and which require identity adjudication?

Hard limits:

- A source observation is not automatically a legal version.
- Same number does not prove stable identity; changed number does not prove
  deletion.
- The document-level general implementation date must not be assigned to each
  clause without clause-level support.
- Do not infer the number or dates of unobserved intermediate amendments.
- Do not claim complete history.

Finish with a concise chat summary reporting:

- pages visually reviewed;
- number of source segments;
- number of unresolved visual readings;
- count of literal deletion placeholders;
- a one-sentence answer on whether the 84→96 evidence supports a recoding
  hypothesis.
