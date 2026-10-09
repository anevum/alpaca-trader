"""Run the frozen, offline, observed-fill screening from private JSON.

This command NEVER fetches account records, modifies strategy, or places
orders. Input must be owner-controlled, local and private. Output aggregates
statistics and deliberately omits per-fill prices and account identifiers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os

from app.research_agent.observed_fill_screen import screen_observed_trades


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only frozen entry-gate screen over actual fill evidence."
    )
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--output", help="Optional private aggregate scorecard JSON path")
    args = parser.parse_args()
    src = Path(args.evidence_json)
    if not src.is_file() or not 0 < src.stat().st_size <= 5_000_000:
        parser.error("input evidence absent or outside bounded size")
    try:
        evidence = json.loads(src.read_text(encoding="utf-8"))
        result = screen_observed_trades(evidence)
        output = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            dest = Path(args.output)
            if dest.resolve() == src.resolve():
                parser.error("private source evidence may not be overwritten")
            # No unintentional sensitive-file overwrite / insecure default mode.
            fd = os.open(
                dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(output)
        print(output)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
