CREATE VIRTUAL TABLE rule_search_fts USING fts5(
    document_id UNINDEXED,
    title,
    designation,
    drug_names,
    atc_codes,
    indication_text,
    rule_text,
    tokenize = 'unicode61 remove_diacritics 0'
);

INSERT INTO rule_search_fts(
    document_id,
    title,
    designation,
    drug_names,
    atc_codes,
    indication_text,
    rule_text
)
SELECT
    document_id,
    coalesce(title, ''),
    coalesce(designation, ''),
    coalesce(drug_names, ''),
    coalesce(atc_codes, ''),
    coalesce(indication_text, ''),
    rule_text
FROM search_document
ORDER BY document_id;
