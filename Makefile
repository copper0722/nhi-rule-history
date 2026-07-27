PYTHON ?= python3
MANIFEST := data/manifests/nhi-history-odt-v1.jsonl

.PHONY: test validate-manifest sqlite-smoke public-tree-check gap-queue

test:
	$(PYTHON) -m unittest discover -s .script/nhi-rule-history/tests -p 'test_*.py'
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py'

validate-manifest:
	$(PYTHON) tools/validate_manifest.py $(MANIFEST)

sqlite-smoke:
	@tmp_db="$$(mktemp -t nhi-rule-history.XXXXXX.sqlite)"; \
	sqlite3 "$$tmp_db" < database/sqlite-schema.sql; \
	test "$$(sqlite3 "$$tmp_db" 'PRAGMA integrity_check;')" = "ok"; \
	rm -f "$$tmp_db"; \
	echo "sqlite schema: ok"

public-tree-check:
	$(PYTHON) tools/check_public_tree.py

gap-queue:
	PYTHONPATH=src $(PYTHON) tools/build_history_gap_work_queue.py \
	  --cross-format-ledger docs/audits/2026-07-27-history-marker-cross-format-evidence.jsonl \
	  --document-candidate-ledger docs/audits/2026-07-27-history-marker-document-candidate-evidence.jsonl \
	  --output docs/audits/2026-07-27-history-gap-work-queue.jsonl \
	  --manifest docs/audits/2026-07-27-history-gap-work-queue-manifest.json \
	  --declared-cut 2026-07-27 \
	  --expected-row-count 3080
