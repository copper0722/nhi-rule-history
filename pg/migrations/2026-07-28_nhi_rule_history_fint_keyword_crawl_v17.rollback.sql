BEGIN;

DO $$
BEGIN
  IF to_regclass(
       'nhi_rule_history_edition.fint_crawl_run'
     ) IS NOT NULL
     AND EXISTS (
       SELECT 1
       FROM nhi_rule_history_edition.fint_crawl_run
     ) THEN
    RAISE EXCEPTION
      'refusing FINT rollback while crawl receipts exist';
  END IF;
END;
$$;

DROP TRIGGER IF EXISTS fint_crawl_run_update_guard
  ON nhi_rule_history_edition.fint_crawl_run;
DROP TRIGGER IF EXISTS fint_document_number_identity_update_guard
  ON nhi_rule_history_edition.fint_document_number_identity;

DROP TABLE IF EXISTS nhi_rule_history_edition.fint_crawl_issue;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_attachment_snapshot;
DROP TABLE IF EXISTS
  nhi_rule_history_edition.fint_attachment_declaration;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_query_document_match;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_document_snapshot;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_query_seed;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_query;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_crawl_observation;
DROP TABLE IF EXISTS
  nhi_rule_history_edition.fint_document_number_identity;
DROP TABLE IF EXISTS nhi_rule_history_edition.fint_crawl_run;

DROP FUNCTION IF EXISTS
  nhi_rule_history_edition.guard_fint_crawl_run_update();
DROP FUNCTION IF EXISTS
  nhi_rule_history_edition.reject_fint_crawl_mutation();
DROP FUNCTION IF EXISTS
  nhi_rule_history_edition.guard_fint_crawl_child_insert();

COMMIT;
