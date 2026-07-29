#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from nhi_rule_history.current_publication import (
    _source_context,
    material_inventory_report,
    prepare_current_publication,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the current-clause history-gap inventory report"
    )
    parser.add_argument("stage_dir", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(
        (args.stage_dir / "structural-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    with psycopg.connect(args.dsn) as connection:
        acquisition_run_id, source_urls, reconstructed = _source_context(
            connection, str(manifest["parse_run_id"])
        )
    material = prepare_current_publication(
        args.stage_dir,
        source_acquisition_run_id=acquisition_run_id,
        source_urls=source_urls,
        reconstructed_version_counts=reconstructed,
    )
    report = material_inventory_report(material)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output": str(args.output), **report["summary"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
