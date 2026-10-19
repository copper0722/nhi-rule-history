-- 2026-10-19 — git-diff cards for announced amendment notices (v29).
--
-- The website shows each announced drug-rule amendment as a code-diff style
-- card of the official 修訂對照表 (原給付規定 against 修訂後給付規定).  The
-- announced layer keeps only the revised column, so the old column is not in
-- PostgreSQL.  This schema stores the card data: one write-once comparison per
-- official 修訂對照表 (identified by the SHA-256 of its ODT bytes) and generator
-- version, with one block per clause row and one per revised 附表 table.  Rows
-- are built from the registered corpus bundles by the project's own loader
-- (python -m tools.diff_cards.build load-pg); no model is involved.
--
-- What it is not: not an authority for rule text (the official ODT and the
-- announced layer stay authoritative), not a version chain, and not a claim
-- about legal effective dates.  It changes no loader, lane, hold or consumer;
-- nothing reads it until a consumer is pointed at the view.
--
-- Access follows v28: the schema is closed to PUBLIC and nothing is granted, so
-- only the owner (the role the reader API runs as) can read.  The view is
-- security_invoker and not updatable.  The comparison tables are write-once:
-- UPDATE, DELETE and TRUNCATE are refused, and a comparison commits only with
-- all of its blocks.  A change of generator version loads a new comparison
-- beside the old one; the view serves the newest per source file.
--
-- Rollback: 2026-10-19_nhi_rule_history_diff_card_v29.rollback.sql drops the
-- view, the two tables, the two guard functions and the schema (RESTRICT, no
-- CASCADE).  The tables hold derived rows only; running the loader again
-- refills them from the corpus.
-- Readback: database/queries/diff-card-readback-v29.sql.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT pg_advisory_xact_lock(
  hashtextextended('nhi-rule-history-diff-card-v29', 0)
);

CREATE SCHEMA nhi_rule_history_diff_card;
REVOKE ALL ON SCHEMA nhi_rule_history_diff_card FROM PUBLIC;
COMMENT ON SCHEMA nhi_rule_history_diff_card IS
  'Write-once code-diff card data for announced NHI drug-rule amendment notices: the official 修訂對照表 (原給付規定 against 修訂後給付規定) cut into per-clause blocks with exact paragraphs and row-by-row diff. Presentation data derived from the official ODT, not a rule-text authority; read it through v_public_comparison_block.';

-- ---------------------------------------------------------------------------
-- One comparison per official 修訂對照表 and generator version
-- ---------------------------------------------------------------------------

CREATE TABLE nhi_rule_history_diff_card.comparison (
  comparison_id uuid PRIMARY KEY,
  source_artifact_sha256 text NOT NULL
    CHECK (source_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  generator_version text NOT NULL CHECK (generator_version <> ''),
  comparison_sha256 text NOT NULL
    CHECK (comparison_sha256 ~ '^[0-9a-f]{64}$'),
  block_count integer NOT NULL CHECK (block_count >= 1),
  extra_table_count integer NOT NULL CHECK (extra_table_count >= 0),
  work_item_id uuid NOT NULL,
  corpus_manifest_sha256 text NOT NULL
    CHECK (corpus_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  loaded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_artifact_sha256, generator_version)
);

COMMENT ON TABLE nhi_rule_history_diff_card.comparison IS
  'One official 修訂對照表 as compared by one generator version. Write-once; a new generator version adds a second row for the same source file.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.source_artifact_sha256 IS
  'SHA-256 of the notice''s official comparison-table ODT bytes: the same value the announced layer records as source_artifact_sha256.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.generator_version IS
  'The loader code that produced the blocks. Rows of one version are never rewritten; a changed generator loads under a new version.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.comparison_sha256 IS
  'SHA-256 of the canonical JSON (sorted keys, compact separators, UTF-8, one trailing newline) of the list of this comparison''s blocks in block_order, each with the thirteen view columns block_order to rows. A pure function of the ODT bytes and the generator version.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.block_count IS
  'How many blocks the comparison holds; checked at commit against comparison_block.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.extra_table_count IS
  'Multi-column tables of the ODT that are not 原／修訂後 comparisons and are therefore not compared.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.work_item_id IS
  'Update-queue work item whose registered bundle supplied the ODT. A plain value, not a foreign key: the queue schema is rolled back independently.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.corpus_manifest_sha256 IS
  'Registered manifest digest of that bundle (the proof the ODT bytes are the registered ones).';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison.loaded_at IS
  'When the loader committed the comparison; the view serves the newest comparison of each source file.';

-- ---------------------------------------------------------------------------
-- Blocks: one per clause row of the first table, one per revised 附表 table
-- ---------------------------------------------------------------------------

CREATE TABLE nhi_rule_history_diff_card.comparison_block (
  comparison_id uuid NOT NULL
    REFERENCES nhi_rule_history_diff_card.comparison (comparison_id),
  block_order integer NOT NULL CHECK (block_order >= 0),
  block_kind text NOT NULL CHECK (block_kind IN ('clause', 'attachment')),
  clause_id text NOT NULL CHECK (clause_id <> ''),
  clause_codes text[] NOT NULL CHECK (cardinality(clause_codes) >= 1),
  path text NOT NULL CHECK (path <> ''),
  badge text NOT NULL CHECK (badge IN ('修訂', '新增')),
  hunk_old text NOT NULL CHECK (hunk_old <> ''),
  hunk_new text NOT NULL CHECK (hunk_new <> ''),
  adds integer NOT NULL CHECK (adds >= 0),
  dels integer NOT NULL CHECK (dels >= 0),
  old_paragraphs jsonb NOT NULL
    CHECK (jsonb_typeof(old_paragraphs) = 'array'),
  new_paragraphs jsonb NOT NULL
    CHECK (jsonb_typeof(new_paragraphs) = 'array'
           AND jsonb_array_length(new_paragraphs) >= 1),
  rows jsonb NOT NULL
    CHECK (jsonb_typeof(rows) = 'array' AND jsonb_array_length(rows) >= 1),
  rows_sha256 text NOT NULL CHECK (rows_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (comparison_id, block_order)
);

COMMENT ON TABLE nhi_rule_history_diff_card.comparison_block IS
  'The blocks of a comparison in display order. Write-once, and committed only together with every other block of the comparison.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.block_kind IS
  'clause = a row of the first comparison table; attachment = a revised 附表 table (原／修訂後 附表規定).';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.clause_id IS
  'The clause code of the row (for example 9.5), or the short title of the revised 附表 for an attachment.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.clause_codes IS
  'Clause codes this block serves. Clause block: its own clause_id plus every clause heading nested under it in the revised paragraphs (the row of 9.5 serves 9.5 and 9.5.1, which the announced layer splits into two patches). Attachment block: the clause_id of every clause block of the same comparison.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.path IS
  'Display path of the card file, for example 藥品給付規定/9.5.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.badge IS
  '新增 = the original column reads 無 (a new clause); 修訂 otherwise.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.hunk_old IS
  'Paragraph range of the original column, for example 第1～13段, or （無） when the clause is new.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.hunk_new IS
  'Paragraph range of the revised column, for example 第1～19段.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.old_paragraphs IS
  'Source paragraphs of the original column, verbatim, as a JSON array of strings; empty for a new clause.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.new_paragraphs IS
  'Source paragraphs of the revised column, verbatim, as a JSON array of strings.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.rows IS
  'The diff as ordered row objects: kind ctx, del or add; old_no and new_no as paragraph numbers or empty strings; pair (-1 or the id shared by the del and add rows of one edited paragraph); indent_em; segments, a list of text pieces whose concatenation is the paragraph, with mark true on the pieces that changed. Replay rule: old numbers over ctx and del rows run 1..n of old_paragraphs in order, new numbers over ctx and add rows run 1..m of new_paragraphs in order, a del row''s text is its old paragraph and an add or ctx row''s text is its new paragraph.';
COMMENT ON COLUMN nhi_rule_history_diff_card.comparison_block.rows_sha256 IS
  'SHA-256 of the canonical JSON of rows (same canonical form as comparison.comparison_sha256).';

-- ---------------------------------------------------------------------------
-- Write-once guards (the idiom of the sealed tables, v14)
-- ---------------------------------------------------------------------------

CREATE FUNCTION nhi_rule_history_diff_card.reject_write_once_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'diff card tables are write-once: % on %.% is refused',
    TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

CREATE FUNCTION nhi_rule_history_diff_card.check_comparison_complete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected integer;
  actual integer;
  first_order integer;
  last_order integer;
BEGIN
  SELECT c.block_count
  INTO expected
  FROM nhi_rule_history_diff_card.comparison AS c
  WHERE c.comparison_id = NEW.comparison_id;

  SELECT count(*), min(b.block_order), max(b.block_order)
  INTO actual, first_order, last_order
  FROM nhi_rule_history_diff_card.comparison_block AS b
  WHERE b.comparison_id = NEW.comparison_id;

  IF expected IS NULL
     OR actual <> expected
     OR first_order IS DISTINCT FROM 0
     OR last_order IS DISTINCT FROM expected - 1 THEN
    RAISE EXCEPTION
      'diff card comparison % must commit with all of its blocks: block_count %, found % (orders % to %)',
      NEW.comparison_id, expected, actual, first_order, last_order;
  END IF;
  RETURN NULL;
END;
$$;

CREATE TRIGGER comparison_write_once
BEFORE UPDATE OR DELETE ON nhi_rule_history_diff_card.comparison
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_diff_card.reject_write_once_change();

CREATE TRIGGER comparison_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_diff_card.comparison
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_diff_card.reject_write_once_change();

CREATE TRIGGER comparison_block_write_once
BEFORE UPDATE OR DELETE ON nhi_rule_history_diff_card.comparison_block
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_diff_card.reject_write_once_change();

CREATE TRIGGER comparison_block_truncate_guard
BEFORE TRUNCATE ON nhi_rule_history_diff_card.comparison_block
FOR EACH STATEMENT
EXECUTE FUNCTION nhi_rule_history_diff_card.reject_write_once_change();

CREATE CONSTRAINT TRIGGER comparison_complete
AFTER INSERT ON nhi_rule_history_diff_card.comparison
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_diff_card.check_comparison_complete();

CREATE CONSTRAINT TRIGGER comparison_block_complete
AFTER INSERT ON nhi_rule_history_diff_card.comparison_block
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION nhi_rule_history_diff_card.check_comparison_complete();

-- ---------------------------------------------------------------------------
-- The reader contract: every block of the newest comparison of each source file
-- ---------------------------------------------------------------------------

CREATE VIEW nhi_rule_history_diff_card.v_public_comparison_block
WITH (security_invoker = true) AS
SELECT
  c.source_artifact_sha256,
  c.generator_version,
  c.comparison_sha256,
  c.extra_table_count,
  b.block_order,
  b.block_kind,
  b.clause_id,
  b.clause_codes,
  b.path,
  b.badge,
  b.hunk_old,
  b.hunk_new,
  b.adds,
  b.dels,
  b.old_paragraphs,
  b.new_paragraphs,
  b.rows
FROM (
  SELECT DISTINCT ON (k.source_artifact_sha256)
    k.comparison_id,
    k.source_artifact_sha256,
    k.generator_version,
    k.comparison_sha256,
    k.extra_table_count
  FROM nhi_rule_history_diff_card.comparison AS k
  ORDER BY k.source_artifact_sha256, k.loaded_at DESC, k.generator_version DESC
) AS c
JOIN nhi_rule_history_diff_card.comparison_block AS b
  ON b.comparison_id = c.comparison_id;

COMMENT ON VIEW nhi_rule_history_diff_card.v_public_comparison_block IS
  'The card data readers use: one row per block of the newest comparison (loaded_at, then generator_version) of each official 修訂對照表 file, with the exact paragraphs and the row-by-row diff. Join to an announced patch by source_artifact_sha256 and clause_codes.';

COMMIT;
