# GPT Pro audit request — NHI rule-history methodology v4

Please act as an adversarial methodology reviewer. This is a public-data
research pipeline, not a request to infer missing legal text. Give concrete
corrections to the plan; do not rewrite the whole document.

## Objective

Reconstruct Taiwan NHI drug-reimbursement-rule history in PostgreSQL with a
single clause as the canonical version unit, then export normalized JSONL,
SQLite, and a reader page. The current official chapter files can publish
current text immediately; historical confidence is reported per clause and
time interval.

## Verified facts

1. Fourteen annual consolidated ODT files cover 2007-07 through 2020. They are
   complete source editions at observation points, not event logs.
2. Current official chapter files can deterministically reconstruct 639
   clauses. Current whole-file and chapter-file surfaces differ on 33 clause
   designations; the chapter page is the owner-selected current-text canon.
3. Current texts contain 6,360 valid ROC-date marker occurrences across 3,080
   clause-date pairs. These markers are strong indexes for surviving text but
   cannot reveal clauses deleted in full.
4. Bounded MOHW FINT acquisitions contain 942 documents for 1999–2020 and 366
   for 2021–2026. An exact query using the later name returns zero for
   1996–1998, but the instrument then had an earlier name.
5. NHI's parent navigation labels one listing “effective after 2014-04-03.”
   On 2026-07-29 the target listing exposed 859 rows / 43 pages; its oldest
   visible row was 2022-09-06 and the table included a publication-expiry
   field. It cannot serve as a closed post-2014 archive.
6. A later official NHI publication records:
   - 1995-06-20: `全民健康保險藥品使用規範` established;
   - 1995-07-01: generally implemented;
   - 1998-03-04: reorganized/renamed `全民健康保險藥品給付規定`;
   - 1998-04-01: renamed rules implemented.
7. A public National Central Library catalog record `D9507418`, document
   `84衛技字第052484號`, records the 1995 forwarding letter and earlier rule
   name, but no complete original 1995/1996 text has yet been recovered.
   “Paper-only” remains unresolved.
8. A Taiwan Historica health-department-fonds query declares 12,992 records,
   but visible broad-result navigation stops at result 10,000 (1967 in
   ascending order). A 1995 date slice returned one irrelevant volume; exact
   document-number/title searches returned zero. These are bounded misses, not
   absence proof.
9. An optional empty-keyword FINT surface query declares 17,497 rows. A
   year-partition crawl was stopped after 1900–1989 (90 manifests, 448 rows).
   It is now a recall-audit lane, not the main reconstruction prerequisite.

## Proposed method

- Canonical version unit: one stable clause identity, not chapter or edition.
- Annual/current editions become Git-like state observations.
- Adjacent editions are compared for presence, designation, structure and full
  text:
  - absent→present: `create_or_restore_candidate`;
  - present→changed: `amend_candidate`;
  - present→absent: `delete_candidate`;
  - designation/structure change: move/split/merge review.
- Identical text across editions produces repeated observations of one text
  state, not false versions.
- Without direct effective-date evidence, a candidate retains interval
  precision: after the prior observation and no later than the next
  observation. The later snapshot date is never presented as the legal
  effective date.
- Inline dates, NHI notices/RSS/attachments, targeted FINT queries, official
  gazettes, archives, libraries and government publications form an evidence
  union.
- A notice or old/new comparison can upgrade an interval transition to an
  exact transition when it provides a direct official locator and complete
  sides. Notice linkage is reported separately and is not a mandatory foreign
  key.
- `notice_not_found_after_bounded_search` describes only the declared search.
- Agentic work is limited to proofread/OCR/table reconstruction, stable-identity
  ambiguities, split/merge/move/number reuse, date-role interpretation and
  complete-side reconstruction. Deterministic code owns fetch, hashes,
  structure, exact equality, candidates, diff, replay, validation and export.
- Producer agents cannot accept their own proposals. Canonical promotion is
  independently authorized after exact-span/hash validation and replay.

## Questions

1. Does the Git-like observation model overclaim a legal “version” when an
   unknown number of intermediate changes may exist? Propose precise entity and
   status names that preserve usability without overclaiming.
2. What is the correct interval representation and ordering rule when annual
   edition dates themselves may be publication/as-of observations rather than
   effective dates?
3. What additional evidence or invariants are required to distinguish create
   from restore, delete from move/renumber, and amendment from delete+create?
4. Should an interval candidate appear in the public reader? If yes, give the
   exact user-facing confidence/wording rules.
5. Propose a per-clause completeness vector that separates:
   current-text certainty, observed-state coverage, exact-time coverage,
   predecessor certainty, deletion coverage, notice linkage, source-search
   coverage and replay parity.
6. Identify any residual methodological error that would make this public repo
   misleading even if every deterministic check passes.

Return:

- `DISPOSITION`: ACCEPT / REPAIR_THEN_ACCEPT / REJECT;
- findings ranked Critical / High / Medium / Low;
- a minimal set of required repairs;
- a proposed normalized entity/status vocabulary;
- a reader-facing wording contract for exact versus interval history.
