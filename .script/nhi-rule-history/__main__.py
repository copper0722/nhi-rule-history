"""This hyphenated directory cannot be imported as a normal Python package.

Package-style module execution (``-m`` with an underscored or hyphenated
package name) is unsupported because the on-disk directory name contains
hyphens.

Truthful CLI entrypoints (run from this directory):

    python3 corpus_profile.py --corpus-root <path> --out-dir <path>
    python3 run_profile.py --corpus-root <path> --out-dir <path>
    python3 occurrence_extract.py --history-dir ... --accepted-manifest ... \\
        --stage-dir ... --receipt-dir ...
    python3 run_occurrences.py --history-dir ... --accepted-manifest ... \\
        --stage-dir ... --receipt-dir ...

``run_profile.py`` / ``run_occurrences.py`` are the preferred thin wrappers.
"""

import sys

print(
    "ERROR: hyphenated directory nhi-rule-history is not a Python package import name.\n"
    "Use: python3 run_profile.py --corpus-root ... --out-dir ...\n"
    " or: python3 corpus_profile.py --corpus-root ... --out-dir ...\n"
    " or: python3 run_occurrences.py --history-dir ... --accepted-manifest ... "
    "--stage-dir ... --receipt-dir ...\n"
    " or: python3 occurrence_extract.py ...",
    file=sys.stderr,
)
raise SystemExit(2)
