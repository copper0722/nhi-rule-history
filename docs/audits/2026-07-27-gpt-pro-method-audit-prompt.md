# GPT Pro bounded methodology audit — exact prompt

Audit the current public-method packet for the NHI drug reimbursement rule
history project. This is a post-verification architecture/methodology audit,
not authorization to apply migrations, mutate live PostgreSQL, publish a
dataset, or claim the history is complete.

## Decision to audit

The project now says:

1. The official source label is `通則`. `chapter:00` is a project-assigned
   navigation/sort/API code only (`code_origin=project_assigned`), never an
   official “第 0 章”. Public/API data preserve `source_designation_raw`,
   `navigation_code`, `code_origin`, and reader display label.
2. At declared cut 2026-07-27, **0/1,548 clauses are certified
   complete_to_declared_cut**.
3. Raw parsing found 6,366 slash-triplet occurrences in 983 clauses. Exact
   context adjudication shows 6 are Trelegy Ellipta dose strings
   (`92/55/22` or `184/55/22 mcg`), not dates. The amendment-date denominator
   is therefore 6,360 valid occurrences, 323 unique dates, and 3,080
   clause×date pairs. The 565 clauses without a valid marker are not presumed
   unchanged while the official source universe remains open.
4. Fresh read-only live PostgreSQL observation at 2026-07-27T13:01:41Z:
   6,366 immutable raw annotations remain `unresolved_event`; resolver outcomes
   are 6,360 `no_match`, 6 terminal `invalid`/non-date, 0
   `resolved_candidate`; 7/7 new-clause proposals remain `needs_review`;
   canonical history schema is absent.
5. Candidate search only: native ODT/PDF/OLE/ODS text locates a same-date
   candidate for 2,034/3,080 clause×date pairs and same-artifact
   date+official-designation candidates for 909/3,010 evaluable pairs.
   Artifact→resource→official-document tracing gives 490 unique owning-document
   candidates and 419 ambiguous candidates (2–11), 0 unmapped. This is a
   reading queue, not a legal event/effect link. A cumulative attachment may
   carry many older dates.
6. Official-source surfaces are not closed: NHI listing has 858 detail rows /
   847 normalized document-number keys; bounded FINT exact-phrase discovery
   has 366 / 365; intersection 217, NHI-only 630, FINT-only 148, union 995,
   with 7 collision keys.
7. Current whole-file and chapter anchors each reconstruct 639 designations:
   606 equal and 33 different. Nineteen leafmost differences are classified
   (6 version/date, 6 list marker, 6 punctuation, 1 trailing layout), but no
   canonical side has been selected.
8. The continuous updater is stage-only. It preserves the full official
   detail/attachment bundle, calls the primary worker and only one fallback
   after recorded primary failure, then stops at `needs_review`.
   `AUTO_PROMOTION_ENABLED=false`.
9. Local canonical-promotion review found and remediated successive integrity
   flaws. The exact current packet deliberately has **no positive promotion
   lane**: all release-linked ODT/ODS/PDF observations have
   `promotion_eligible=false`; archive/PDF integrity blockers fire before the
   first canonical write. A governed external full-document verifier,
   independently replayable signed receipt, and separately reviewed admission
   path are still absent. Therefore live apply remains blocked.
10. Verification on the exact packet: legacy suite 70 pass / 1 skip; public
    suite 270 pass / 6 skip; independent disposable-PostgreSQL review
    C/H/M/L = 0/0/1/2. The Medium is precisely the absence of a positive
    promotion lane, not evidence that promotion is operational.

## Audit questions

Return one bounded decision:

`PRO_METHOD_AUDIT=ACCEPT | REVISE | BLOCK`

Then answer:

1. Is `0/1,548 certified complete` the honest completeness statement given
   these denominators, including the 565 no-marker clauses? Distinguish
   “not certified complete” from “proven to have a missing version”.
2. Is the `通則` / project-assigned `chapter:00` provenance contract
   methodologically sufficient? Give exact wording changes if not.
3. Do the date annotations make incompleteness easy to *audit* without making
   history easy to *reconstruct*? Define the minimum per-clause closure
   evidence: event/effect, before/after snapshots, stable identity,
   predecessor edge, source-universe cut, and cumulative-anchor replay.
4. Are the candidate coverage and source-surface numbers labelled honestly,
   or does any denominator/claim still invite a false completeness inference?
5. Is “all attachment formats observation-only until an external
   full-document verifier exists” the correct fail-closed boundary? State the
   minimum verifier receipt/admission contract, but do not authorize live
   apply.
6. Identify any Critical/High methodology defect in the public wording or
   state model. Report `C/H/M/L` counts and list exact blocking fixes. If there
   is no Critical/High, say so explicitly.
7. Give the shortest reader-facing answer to Copper’s question:
   “目前逐條文歷史完整了嗎？條文變更會加註日期，應很好確認才對。”

Do not infer access to local files. Audit only the fact-locked packet above and
the prior conversation context. Do not introduce new source counts or claim
that any candidate is a verified legal amendment.

## Exact public state fingerprint

`01ca0e0ccd10483744c830e025adb4441f8591ea8c9f2459e16b9c3b036dd29e`

This is SHA-256 of the ordered `sha256  repo-relative-path` list for the
methodology, scoreboard, adjudication, candidate-search receipts, source
reconciliation, current-anchor parity, live observation, gap register,
independent review, and the two unapplied migration drafts.
