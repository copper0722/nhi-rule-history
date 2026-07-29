#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nhi_rule_history.contracts import ContractError, canonical_json_bytes
from nhi_rule_history.discovery.fint_seed import Heading, seeds_from_headings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic FINT keyword seeds from tab-separated "
            "canonical clause headings on stdin: designation<TAB>raw_text."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    headings: list[Heading] = []
    for line_number, line in enumerate(sys.stdin, 1):
        line = line.rstrip("\n")
        if not line:
            continue
        if "\t" not in line:
            raise ContractError(
                f"stdin:{line_number}: expected designation<TAB>raw_text"
            )
        designation, raw_text = line.split("\t", 1)
        if not designation or not raw_text:
            raise ContractError(
                f"stdin:{line_number}: designation and raw_text are required"
            )
        headings.append(Heading(designation, raw_text))

    rows = []
    for seed in seeds_from_headings(headings):
        rows.append(
            {
                "keywords": list(seed.keywords),
                "origin_kind": seed.origin_kind,
                "origin_locator": seed.origin_locator,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("wb") as stream:
        for row in rows:
            stream.write(canonical_json_bytes(row))
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
