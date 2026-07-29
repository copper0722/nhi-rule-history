# 84–87 年早期規範：Grok／Gemini 原始回報與採納結果

執行日期：2026-07-29
輸入：
[`2026-07-29-early-rule-multimodel-research-prompt.md`](2026-07-29-early-rule-multimodel-research-prompt.md)
重試：
[`2026-07-29-early-rule-grok-retry-prompt.md`](2026-07-29-early-rule-grok-retry-prompt.md)

## Model ledger

| worker | provider / model | harness status | evidentiary disposition |
|---|---|---|---|
| government notice / legacy web | Grok / `grok-4.5` | failed twice | 未完成；不得採納 |
| library / archive forensics | agy / `Gemini 3.1 Pro (High)` | output-contract passed | lead-only；sources array 為空，實質主張須另行驗證 |

## Grok first run — exact answer

> I'll search public catalogs, gazettes, archives, and X for recoverable
> 1995–1998 NHI drug-rule texts and provenance, then classify each hit by source
> state without collapsing categories.Key hits: RootLaw history for
> `藥品使用規範` and an NHI retrospective PDF. Opening those and hunting
> library/gazette records next.

Completion failure:

- 310 characters, required at least 1,800;
- missing all three required sections;
- no direct URL or source object;
- “RootLaw history” is therefore an unverified lead, not a finding.

## Grok bounded retry — exact answer

> I'll open RootLaw and the NHI PDF first, then run exact-title and catalog
> searches for the 1995/1996 sources.

Completion failure:

- 109 characters, required at least 1,200;
- missing all three required sections;
- no opened URL or locator;
- the retry budget is exhausted.

## Gemini run — exact answer

### SOURCE LEDGER

| Date | Title/Name | Issuing body | Document/catalog ID | Source state | Direct URL | Exact locator | What it proves | Limitations |
|---|---|---|---|---|---|---|---|---|
| 1995-07 | (Forwarding Letter for rules) | 臺灣省政府衛生處 (Taiwan Provincial Dept. of Health) | 84衛技字第052484號 / NCL Taiwan Memory D9507418 | 3. a catalog record without accessible full text | https://tm.ncl.edu.tw/search?q=D9507418 | Search query for D9507418 | Proves the administrative distribution of the 1995-06-20 rule text. | **UNVERIFIED LEAD**: The catalog ID `D9507418` does not resolve to a public digital object on the current Taiwan Memory web frontend, yielding no full text. |
| 1995-06-20 (Effective 1995-07-01) | 全民健康保險藥品使用規範 (Later: 全民健康保險藥品給付規定) | 中央健康保險局 (Bureau of NHI) | N/A | 4. a later official transcription or retrospective reproduction | https://www.nhi.gov.tw/ | 健保署「藥品給付規定」專區 (Current rules PDF) | Proves that specific clauses and general principles were enacted/amended on 1996 dates (e.g., annotations `85/1/1`, `85/10/1`). | Only clauses that survived into the digital era are retained. Clauses deleted entirely before ~1999 are completely lost in this format. |

### COVERAGE VERDICT

- **What can now be said about the 1995 baseline and 1996 rule state?**
  The original baseline instrument was established as
  `全民健康保險藥品使用規範` on 1995-06-20 and went into effect on
  1995-07-01. A known forwarding letter from the Taiwan Provincial Department
  of Health (`84衛技字第052484號`) confirms its circulation in July 1995.
  The rules were actively amended during 1996 (ROC year 85) on specific dates,
  notably 1996-01-01 and 1996-10-01. The instrument was subsequently
  reorganized and renamed `全民健康保險藥品給付規定` on 1998-03-04.
- **Is any complete original 1995/1996 text publicly downloadable?**
  No. Despite targeted searches across government gazettes, library catalogs,
  and legacy web archives, no complete born-digital version or digitized scan
  of the 1995/1996 text is publicly downloadable.
- **Which known 1996 amendments or clause families have direct evidence?**
  Direct evidence comes from retrospective reproductions in the current NHI
  rules, which retain historical date annotations. Specifically, clauses
  marked with `85/1/1` and `85/10/1` provide evidence for 1996 rules regarding
  general prescription principles (e.g., matching approved indications and
  reasonable dosages), octreotide (肢端肥大症治療藥物), Salmon calcitonin
  (抑鈣激素製劑), Fluconazole (抗黴菌藥物), and G-CSF
  (白血球生長激素).
- **What remains unknown, especially clauses deleted before later snapshots?**
  The complete holistic text of the 1995 baseline and the exact wording of all
  1996 amendments are unknown. Any clauses, drug categories, or strict usage
  limitations that were enacted in 1995–1996 but deleted in full before the NHI
  started maintaining consolidated digital public snapshots (around 1999)
  leave no trace in current official transcriptions.
- **Is `paper-only` proved, disproved, or unresolved?**
  Unresolved. While public web searches failed to recover the full text and
  only yielded catalog metadata or later transcriptions, this does not
  categorically prove the source is *only* on paper. A digitized scan may exist
  in an intranet, an unindexed legacy database, or a restricted on-site library
  terminal.

### NEXT QUERIES

1. **National Archives Administration (檔案管理局):** Query the official
   government archives database (`https://aa.archives.gov.tw/`) for the exact
   document number `84衛技字第052484號` to locate the physical holding or
   request a digitized reproduction of the Taiwan Provincial Department of
   Health letter and its attached rules.
2. **NCL / Taiwan Memory Reference Request:** Since the catalog ID `D9507418`
   exists but fails to resolve on the public web frontend, submit a direct
   librarian reference request to the National Central Library to access the
   restricted/offline digital object or the physical holding it points to.
3. **MOHW / NHI Freedom of Information Request:** Submit a formal request to the
   NHI for the complete archival copy of the 1995-06-20
   `全民健康保險藥品使用規範` booklet and all corresponding 1996-1998
   amendments.
4. **University Medical Library Catalogs:** Search physical and local digital
   holdings for printed handbook editions published between 1995 and 1998.

The dispatcher returned an empty `sources` array for this answer.

## Independent reconciliation

Accepted after direct verification:

- the later official NHI retrospective publication records the 84-06-20
  establishment, 84-07-01 implementation, 87-03-04 renaming and 87-04-01
  implementation;
- the public National Central Library digital-archives catalog exposes
  `D9507418` and `84衛技字第052484號`;
- later official NHI rule texts preserve some 85-year annotations;
- `paper-only` remains unresolved.

Not accepted from model output alone:

- the categorical statement that no complete original is publicly
  downloadable;
- the named 1996 clause-family list, until each clause gets a direct official
  URL and exact locator;
- “deleted before around 1999” as a proved temporal boundary;
- the unspecified RootLaw lead.

Additional browser-verified evidence is recorded in
[`2026-07-29-taiwan-historica-health-department-search.json`](2026-07-29-taiwan-historica-health-department-search.json).
