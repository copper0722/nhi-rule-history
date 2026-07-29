-- 2026-07-28 — append-only current-text source authority policy
--
-- The NHI chapter download page is the sole current-text authority.  The
-- separate whole-document page remains useful as a non-authoritative quality
-- cross-check, but a mismatch cannot block publication of the chapter-page
-- projection.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-current-source-authority-v16', 0)
);

CREATE TABLE IF NOT EXISTS
  nhi_rule_history_edition.current_source_authority_policy (
    policy_id text PRIMARY KEY,
    policy_version integer NOT NULL UNIQUE CHECK (policy_version > 0),
    authority_page_url text NOT NULL CHECK (authority_page_url ~ '^https://'),
    authority_surface text NOT NULL CHECK (
      authority_surface = 'current_chapters'
    ),
    authority_role text NOT NULL CHECK (
      authority_role = 'sole_current_text_authority'
    ),
    primary_structured_format text NOT NULL CHECK (
      primary_structured_format = 'odt'
    ),
    whole_page_url text NOT NULL CHECK (whole_page_url ~ '^https://'),
    whole_surface text NOT NULL CHECK (whole_surface = 'current_whole'),
    whole_role text NOT NULL CHECK (
      whole_role = 'non_authoritative_quality_crosscheck'
    ),
    whole_mismatch_blocks_current_publication boolean NOT NULL CHECK (
      whole_mismatch_blocks_current_publication = false
    ),
    page_update_label_is_legal_effective_date boolean NOT NULL CHECK (
      page_update_label_is_legal_effective_date = false
    ),
    recorded_on date NOT NULL,
    decision_basis text NOT NULL CHECK (decision_basis <> ''),
    created_at timestamptz NOT NULL DEFAULT current_timestamp
  );

COMMENT ON TABLE
  nhi_rule_history_edition.current_source_authority_policy IS
  'Append-only authority policy for selecting the official current text. '
  'The newest policy_version is active; prior decisions remain auditable.';

CREATE OR REPLACE FUNCTION
  nhi_rule_history_edition.reject_current_source_authority_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'current_source_authority_policy is append-only; insert a new version';
END;
$$;

DROP TRIGGER IF EXISTS current_source_authority_policy_no_update
  ON nhi_rule_history_edition.current_source_authority_policy;
CREATE TRIGGER current_source_authority_policy_no_update
BEFORE UPDATE ON nhi_rule_history_edition.current_source_authority_policy
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_edition.reject_current_source_authority_mutation();

DROP TRIGGER IF EXISTS current_source_authority_policy_no_delete
  ON nhi_rule_history_edition.current_source_authority_policy;
CREATE TRIGGER current_source_authority_policy_no_delete
BEFORE DELETE ON nhi_rule_history_edition.current_source_authority_policy
FOR EACH ROW
EXECUTE FUNCTION
  nhi_rule_history_edition.reject_current_source_authority_mutation();

CREATE OR REPLACE VIEW
  nhi_rule_history_edition.v_current_source_authority AS
SELECT
  policy_id,
  policy_version,
  authority_page_url,
  authority_surface,
  authority_role,
  primary_structured_format,
  whole_page_url,
  whole_surface,
  whole_role,
  whole_mismatch_blocks_current_publication,
  page_update_label_is_legal_effective_date,
  recorded_on,
  decision_basis,
  created_at
FROM nhi_rule_history_edition.current_source_authority_policy
ORDER BY policy_version DESC
LIMIT 1;

INSERT INTO nhi_rule_history_edition.current_source_authority_policy (
  policy_id,
  policy_version,
  authority_page_url,
  authority_surface,
  authority_role,
  primary_structured_format,
  whole_page_url,
  whole_surface,
  whole_role,
  whole_mismatch_blocks_current_publication,
  page_update_label_is_legal_effective_date,
  recorded_on,
  decision_basis
) VALUES (
  'nhi-current-chapter-page-v1',
  1,
  'https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html',
  'current_chapters',
  'sole_current_text_authority',
  'odt',
  'https://www.nhi.gov.tw/ch/cp-13108-67ddf-2508-1.html',
  'current_whole',
  'non_authoritative_quality_crosscheck',
  false,
  false,
  DATE '2026-07-28',
  'The official page is explicitly the latest reimbursement-rule content '
  '(by chapter); project-owner authority decision confirms it as the sole '
  'canonical current-text surface.'
)
ON CONFLICT (policy_id) DO NOTHING;

DO $$
DECLARE
  policy_row nhi_rule_history_edition.current_source_authority_policy%ROWTYPE;
BEGIN
  SELECT *
  INTO STRICT policy_row
  FROM nhi_rule_history_edition.current_source_authority_policy
  WHERE policy_id = 'nhi-current-chapter-page-v1';

  IF policy_row.policy_version <> 1
     OR policy_row.authority_page_url <>
       'https://www.nhi.gov.tw/ch/cp-7593-ad2a9-3397-1.html'
     OR policy_row.authority_role <> 'sole_current_text_authority'
     OR policy_row.whole_role <>
       'non_authoritative_quality_crosscheck'
     OR policy_row.whole_mismatch_blocks_current_publication
     OR policy_row.page_update_label_is_legal_effective_date
  THEN
    RAISE EXCEPTION 'current source authority policy v1 does not match';
  END IF;
END;
$$;

COMMIT;
