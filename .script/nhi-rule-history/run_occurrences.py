#!/usr/bin/env python3
"""Preferred CLI entrypoint for NHI history occurrence extraction.

The on-disk directory name ``nhi-rule-history`` contains hyphens and is not a
valid Python package import path. Invoke this script (or occurrence_extract.py)
directly:

    python3 run_occurrences.py \\
        --history-dir <path> \\
        --accepted-manifest <path> \\
        --stage-dir <path> \\
        --receipt-dir <path>
"""

from occurrence_extract import main

if __name__ == "__main__":
    raise SystemExit(main())
