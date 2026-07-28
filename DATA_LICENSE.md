# Data licence and attribution

## Project-produced data

Unless a file says otherwise, normalized data and project-produced metadata in
`data/` are licensed under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

Required attribution:

> Data source: National Health Insurance Administration, Ministry of Health and
> Welfare, Taiwan. Normalization and provenance metadata: nhi-rule-history
> contributors.

This is compatible with the attribution requirement in Taiwan's Government
Open Data Licence, Version 1.0.

## Official NHI and MOHW source material

Official source files remain attributable to the National Health Insurance
Administration or the Ministry of Health and Welfare. Their website open-data
declarations permit reproduction, adaptation, public transmission, and
sublicensing with attribution within the copyright-protected scope. Official
logos, trademarks, third-party material, and any specially restricted item are
not relicensed by this repository; an artifact carrying a special restriction
is excluded from a public binary release until separately adjudicated.

References:

- [NHI open-data declaration](https://www.nhi.gov.tw/ch/cp-4379-5d212-3280-1.html)
- [MOHW open-data declaration](https://www.mohw.gov.tw/cp-81-155-1.html)
- [Taiwan Government Open Data Licence, Version 1.0](https://data.gov.tw/license)

The project preserves official bytes unchanged, stores checksums and source
URLs, and does not imply NHI endorsement.

## ATC/DDD

The complete ATC/DDD index and guideline text are not included in the CC BY
data grant. The WHO Collaborating Centre requires attribution, prohibits
commercial copying/distribution, and prohibits changing or manipulating the
material.

Reference: [ATC/DDD copyright and disclaimer](https://atcddd.fhi.no/copyright_disclaimer/).

Public linkage rows may contain:

- an ATC code already supplied in NHI or TFDA open data;
- the NHI/TFDA record that supplied the code;
- mapping method, confidence, review state, and version;
- a link to the official ATC/DDD index.

They do not redistribute the complete ATC names, hierarchy, DDD values, or
guideline text.

## ICD-11

ICD-11 is licensed by WHO under CC BY-ND 3.0 IGO. WHO's terms state that
mapping or producing crosswalks between other classifications or terminologies
and ICD-11 requires a separate written agreement.

The public reader prototype may display an individual alphanumeric ICD-11 code
beside a project-authored disease tag, together with an explicit
`agent_selected` or `candidate` review state. These code-only relations are
not passwords or secret data, but they are excluded from this repository's
CC BY 4.0 data grant and are not represented as a WHO-authorized reusable
crosswalk dataset.

The repository does not publish WHO titles, URIs, definitions, hierarchy,
indexes, or a reference snapshot. Those fields remain private PostgreSQL
data. Because WHO separately regulates mappings and crosswalks, a stable
reusable NHI-to-ICD-11 mapping release remains blocked until the required
agreement is documented.

References:

- [ICD-11 API licence](https://icd.who.int/docs/icd-api/license/)
- [ICD-11 licensing terms (PDF)](https://icd.who.int/en/docs/ICD11-license.pdf)
