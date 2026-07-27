# MOHW FINT acquisition manifest — capture cut 2026-07-27

This directory is the public, byte-for-byte metadata projection of one bounded
two-pass acquisition run over the Ministry of Health and Welfare FINT query for
the exact phrase `藥品給付規定`, from 2021-01-01 through 2026-07-27.

It records:

- 366 enumerated and fetched official detail pages;
- 1,353 attachment resources;
- two independent enumeration passes with the same 1,719-key set
  (`a9cef7abddcc7c2301363957bbc054259a0cb7f6dac6d8d52c5c4064b54496a7`);
- 1,719/1,719 resources linked to verified raw bytes;
- 1,712 unique SHA-256 artifacts after byte-level deduplication;
- 85,642,128 unique artifact bytes;
- zero acquisition issues.

Attachment resources by official filename extension:

| Extension | Resources | Unique artifacts |
|---|---:|---:|
| PDF | 669 | 666 |
| ODT | 360 | 358 |
| ODS | 299 | 297 |
| DOC | 11 | 11 |
| XLS | 11 | 11 |
| XLSX | 2 | 2 |
| DOCX | 1 | 1 |

The raw binary blobs are content-addressed by `raw-artifacts.jsonl`. They are
prepared for a GitHub Release rather than committed to Git history. The local
prepared raw bundle is 67,161,191 bytes; the structural blocks compress from
68,320,026 to 3,939,116 bytes. `release-preparation.json` records every asset
checksum and remains `prepared_partial_evidence_bundle_not_published`.
`release-eligibility.json` binds the corrected acquisition/structural PG
fingerprints, capture window, collision receipt, and superseded-run exclusion.
The JSONL
contains no local filesystem paths, credentials, cookies, DSNs, or legal-history
promotion fields.

This is an acquisition/raw evidence dataset. It does **not** establish a legal
effective date, stable rule identity, current version, predecessor/successor
relationship, or diff.
