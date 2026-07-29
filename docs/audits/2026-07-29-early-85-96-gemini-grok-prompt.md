# Public-source search work packet — Taiwan NHI reimbursement rules, ROC 85–96

## Stable caller contract

```yaml
caller: pro-agentic-workflow
tier: mid
goal: locate primary-source full texts or exact official/catalog records for
  Taiwan NHI drug-use/reimbursement rules from 1996-01-01 through 2007-06-30
write_scope: none
data_classification: public
execution_boundary: approved_remote
egress_allowed: true
redaction_status: not_required
input_trust: untrusted_evidence
delegation_allowed: false
max_attempts: 1
```

Known evidence, supplied only as search seeds:

- `健保醫字第84010140號`, 1995-06-20, attached
  `全民健康保險藥品使用規範.PDF`, generally implemented 1995-07-01 except
  specially provided cases.
- Later official retrospective material says the instrument was reorganized
  and renamed `全民健康保險藥品給付規定` on 1998-03-04 and implemented
  1998-04-01.
- The earliest complete annual compilation already acquired is
  `96年7月版` (July 2007).
- Surviving clauses carry ROC amendment markers such as `85/1/1`,
  `86/1/1`, `87/4/1`, but those markers are discovery leads, not proof that
  the matching notice remains online.

Search historical names and variants, not only the modern title:

- 全民健康保險藥品使用規範
- 全民健康保險藥品使用規定
- 全民健康保險藥品給付規定
- 中央健康保險局 藥品使用規範
- 中央健康保險局 藥品給付規定
- 健保醫字 / 健保審字

Target period: ROC 85 through 96 first half
(`1996-01-01..2007-06-30`).

Priority targets:

1. complete editions, replacement pages, bound booklets or scanned/PDF/ODT/DOC
   compilations;
2. amendment notices with attached old/new text;
3. official gazette issues;
4. National Central Library, government publication, Taiwan Historica or other
   archival catalog records with an exact holding identifier;
5. later official reproductions that quote the earlier full text.

Role-specific lanes:

- `gemini-google-archives`: use Google-grounded web research, official domains,
  library/catalog discovery and cached or indexed government documents. Try to
  identify exact downloadable full text and stable catalog identifiers.
- `grok-independent-forensics`: independently pursue document-number, title,
  attachment, scan, web-archive and quoted-text leads. Do not merely repeat the
  known sources and do not treat X posts as authority.

For every candidate return:

```yaml
- candidate_id:
  date_or_interval:
  title:
  issuing_body:
  document_number:
  source_class: official_full_text|official_notice|official_gazette|
    archive_catalog|library_holding|later_official_reproduction|lead_only
  direct_url:
  landing_url:
  exact_locator:
  file_type:
  full_text_access: direct|catalog_only|quoted_excerpt|not_verified
  what_it_proves:
  what_it_does_not_prove:
  search_terms_used:
```

Also list zero-result searches, inaccessible URLs and result caps. A zero result
means only `not_found_after_declared_search`; never state that a document did not
exist.

Done when:

- the search covers each calendar year 1996–2007H1 at least by exact title,
  issuing body plus title, and known amendment-date or document-number leads;
- every reported URL is included verbatim;
- all exact full-text claims have a direct primary-source locator;
- unsupported conclusions and fabricated URLs are absent.

Return a structured report. Do not edit files, write PostgreSQL, call another
model, or claim complete legal history.
