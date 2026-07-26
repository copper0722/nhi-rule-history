#!/usr/bin/env python3
"""Preferred CLI entrypoint for the NHI corpus profiler.

The on-disk directory name ``nhi-rule-history`` contains hyphens and is not a
valid Python package import path. Invoke this script (or corpus_profile.py)
directly:

    python3 run_profile.py --corpus-root <path> --out-dir <path>
"""

from corpus_profile import main

if __name__ == "__main__":
    raise SystemExit(main())
