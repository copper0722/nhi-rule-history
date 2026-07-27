-- Guarded rollback for the reviewed promotion evidence schema v1.
--
-- Any row in a managed promotion table is durable audit evidence and blocks
-- this rollback.  A canonical promotion receipt is an additional hard stop.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi_rule_history-promotion-v1-global', 0)
);

DO $schema_guard$
DECLARE
  expected_prefix text :=
    'Reviewed evidence and atomic promotion boundary for canonical NHI rule history. managed=nhi_rule_history_promotion/v1';
  existing_comment text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname = 'nhi_rule_history_promotion'
  ) THEN
    RETURN;
  END IF;

  SELECT obj_description(oid, 'pg_namespace')
    INTO existing_comment
  FROM pg_namespace
  WHERE nspname = 'nhi_rule_history_promotion';

  IF existing_comment !~
     (
       '^' || expected_prefix ||
       ' contract_sha256=[0-9a-f]{64}$'
     ) THEN
    RAISE EXCEPTION
      'refusing promotion rollback: managed marker does not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;

END;
$schema_guard$;

LOCK TABLE
  nhi_rule_history_promotion.promotion_case,
  nhi_rule_history_promotion.effect_resolution,
  nhi_rule_history_promotion.effect_resolution_span,
  nhi_rule_history_promotion.anchor_snapshot,
  nhi_rule_history_promotion.anchor_clause,
  nhi_rule_history_promotion.replay_run,
  nhi_rule_history_promotion.replay_rule_result,
  nhi_rule_history_promotion.replay_event,
  nhi_rule_history_promotion.format_parity_receipt,
  nhi_rule_history_promotion.promotion_transition,
  nhi_rule_history.promotion_receipt
IN ACCESS EXCLUSIVE MODE;

DO $canonical_receipt_guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM nhi_rule_history.promotion_receipt
  ) THEN
    RAISE EXCEPTION
      'refusing promotion rollback: canonical promotion receipts exist'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END;
$canonical_receipt_guard$;

DO $nonempty_guard$
DECLARE
  table_name text;
  has_rows boolean;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'promotion_case',
    'effect_resolution',
    'effect_resolution_span',
    'anchor_snapshot',
    'anchor_clause',
    'replay_run',
    'replay_rule_result',
    'replay_event',
    'format_parity_receipt',
    'promotion_transition'
  ]
  LOOP
    EXECUTE format(
      'SELECT EXISTS (SELECT 1 FROM nhi_rule_history_promotion.%I)',
      table_name
    ) INTO has_rows;
    IF has_rows THEN
      RAISE EXCEPTION
        'refusing promotion rollback: managed table % is nonempty',
        table_name
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END LOOP;
END;
$nonempty_guard$;

REVOKE ALL ON FUNCTION
  nhi_rule_history_promotion.promote_case(uuid, text, bigint)
  FROM nhi_rule_history_promotion_executor;
REVOKE ALL ON ALL TABLES IN SCHEMA nhi_rule_history_promotion
  FROM
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
REVOKE ALL ON SCHEMA nhi_rule_history_promotion
  FROM
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
REVOKE ALL ON
  nhi_rule_history_candidate_stage.candidate_proposal,
  nhi_rule_history_candidate_stage.candidate_source_span,
  nhi_rule_history_candidate_stage.current_candidate_state
  FROM
    nhi_rule_history_owner,
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;
REVOKE ALL ON SCHEMA nhi_rule_history_candidate_stage
  FROM
    nhi_rule_history_owner,
    nhi_rule_history_promotion_writer,
    nhi_rule_history_promotion_reviewer,
    nhi_rule_history_promotion_executor;

DROP TABLE IF EXISTS
  nhi_rule_history_promotion.promotion_transition RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.format_parity_receipt RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.replay_rule_result RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.replay_event RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.replay_run RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.anchor_clause RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.anchor_snapshot RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.effect_resolution_span RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.effect_resolution RESTRICT;
DROP TABLE IF EXISTS
  nhi_rule_history_promotion.promotion_case RESTRICT;

DROP FUNCTION IF EXISTS
  nhi_rule_history_promotion.promote_case(uuid, text, bigint) RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_promotion.guard_promotion_transition() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_promotion.guard_evidence_insert_actor() RESTRICT;
DROP FUNCTION IF EXISTS
  nhi_rule_history_promotion.reject_evidence_mutation() RESTRICT;
DROP DOMAIN IF EXISTS
  nhi_rule_history_promotion.sha256_hex RESTRICT;
DROP SCHEMA IF EXISTS nhi_rule_history_promotion RESTRICT;

COMMIT;
