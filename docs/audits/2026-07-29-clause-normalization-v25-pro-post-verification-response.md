Most material findings from the earlier review are closed. Based on the supplied evidence, the implementation now separates Work, Expression, Manifestation provenance, expression-local nodes, optional persistent node identity, lineage assertions, exact source text, normalized structure, display classification, and activation state. It no longer treats marker paths or load order as persistent legal identity.

The source-span ownership model is methodologically sufficient. Scalar and UTF-8-byte half-open ranges, total ordered primary-leaf coverage, non-overlap, and exact source replay are the correct invariants. The repaired table-cell lookup also closes the concrete integration defect: rendered cell content is resolved through an exact owner key, then checked against both the immutable source block and content.exact_text. This supports reconstruction of:

manifestation text;

expression text;

node-owned text;

source-backed table-cell content.

Containers may remain spanless, and logical carried cells may remain source-spanless, without weakening conservation.

Separating physical cell state from logical grid value is correct. Covered and policy-carried cells may display an inherited logical value while retaining no fabricated physical text or source hash. Requiring a valid origin cell and preserving source ownership only at the origin is the right model for exact reconstruction and responsive row-card rendering. The repaired reader’s rejection of duplicate or missing mappings is an appropriate fail-closed boundary.

A Work-level exact diff remains valid despite 83 unresolved subnode alignments. The exact diff asserts only a transformation between two complete Expression texts. It does not require every subnode to have a verified persistent identity. Unresolved node lineage becomes a blocker only for claims such as “this point moved,” “this subparagraph directly continues,” or node-level semantic alignment. The packet states that those claims are not emitted.

The segment model prevents the ABC → ABCD false-deletion error. An exact segmentation of:
unchanged("ABC")
inserted("D")
reconstructs the complete old and new sides and gives no basis for a deletion hunk. The reported seven-segment sequence—four unchanged segments separated by three insertions—therefore supports 本版新增, provided the stored reconstruction invariants cover both scalar and UTF-8-byte ranges as stated. Labeling the old pane 舊版本, rather than 本版刪除, is semantically correct.

Sealing and recovery are sufficient for this canary. The supplied mutation matrix covers the material paths: direct insert, update, delete, upsert, copy, truncate, and activation-history mutation. The two-transaction deactivation/reactivation drill proves a committed unavailable state was externally observable, followed by restoration of the identical sealed run and byte-identical API payload without reloading. This closes the earlier recovery-evidence concern.

The JSONL and SQLite projection preserve enough logical information for a non-PostgreSQL consumer. Twenty relations, 2,238 canonical rows, per-table count parity, canonical row-fingerprint parity, and a fresh SQLite build from tracked JSONL establish logical portability. SQLite remains a projection rather than an alternate writer. This proves model/data reproducibility; it does not independently prove authenticity of external source artifacts unless the consumer also obtains and verifies those artifacts against the exported hashes and locators.

One Major release blocker remains: the completeness provenance of the future 2026-09-01 Expression is not demonstrated in this packet.
Earlier evidence established that the official amendment attachment was a patch_only source containing omitted Table 2 text. Therefore, the future complete Expression cannot truthfully be source_complete; it must be a verified_composite assembled from the amendment and an independently identified unchanged source component.
The packet reports “2 complete Expressions,” but does not state:

the actual completeness status of each Expression;

the immutable composition-manifest identifier and hash;

which source supplies the unchanged Table 2 body;

the exact component ordering and assembly rule;

a component-level conservation receipt proving that omitted notice text was not represented as physically present;

that the exported API/JSONL/SQLite retain this composite provenance.

Without that proof, a source-exact patch could have been relabeled as a complete official wording, or unchanged text could have been copied into the future Expression without an auditable legal-document composition boundary. That would undermine both the “latest complete clause” reader and the exact diff derived from it.
Falsifiable acceptance test:
current_expression.completeness = source_complete

future_expression.completeness = verified_composite

future_expression.composition_manifest_id IS NOT NULL
composition_manifest_sha256 recomputes exactly

every future-expression scalar and UTF-8 byte is covered exactly once
by an ordered composition component

every component binds:
  source artifact SHA-256
  source block/span
  component role
  exact component text/hash
  assembly ordinal

notice-omitted Table 2 text owns zero notice-source spans

unchanged Table 2 text binds to its actual prior/current official
manifestation and is marked as reused unchanged content

deterministic assembly reproduces:
  complete future-expression text hash
  normalization-run fingerprint
  seven exact diff segments
  exact-diff-run fingerprint

API, canonical JSONL, and SQLite expose:
  verified_composite status
  composition-manifest hash
  component provenance

reader provenance does not call the complete future text
“source-exact from the amendment attachment”
Passing this test does not require re-parsing the notice if the necessary composition rows and receipts already exist; it requires exposing and verifying the evidence that makes the future Expression complete.

Minor improvements — not release blockers

Add a supplementary-plane Unicode and combining-mark fixture to prove scalar, UTF-8-byte, and browser UTF-16 conversions cannot drift.

Add repeated-token cases such as A A → A A A, where several minimal exact alignments are possible, and verify the deterministic tie-break remains stable.

Add a duplicate-identical-table-row plus inserted-row fixture to ensure unresolved row alignment does not cascade into false cell changes.

Record the seven environment-gated skips by stable test name and reason in the release receipt.

Include an explicit SQLite foreign_key_check receipt if it is not already part of the stated integrity gate.

Test screen-reader wording and non-color distinction for inserted-only, removed-only, and unresolved-alignment presentations.

REPAIR
