#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from app.research_agent.evidence import CanonicalEvidenceReader
from app.research_agent.runner import ResearchAgentRunner


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="RHEN Research Agent v1 deterministic foundation",
    )
    value.add_argument(
        "--evidence-file",
        default=os.environ.get("RHEN_RESEARCH_EVIDENCE_FILE"),
        help="Canonical read-only evidence export JSON",
    )
    value.add_argument("--compact", action="store_true")
    subcommands = value.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status")
    for name in ("daily-review", "weekly-review"):
        child = subcommands.add_parser(name)
        child.add_argument(
            "--dry-run",
            action="store_true",
            help="Required in the foundation implementation",
        )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.evidence_file:
        raise SystemExit(
            "--evidence-file or RHEN_RESEARCH_EVIDENCE_FILE is required; "
            "this foundation configures no database credentials or live service"
        )
    evidence = CanonicalEvidenceReader.from_file(Path(args.evidence_file)).read()
    runner = ResearchAgentRunner(evidence)
    if args.command == "status":
        result = runner.status()
    elif args.command == "daily-review":
        result = runner.daily_review(dry_run=args.dry_run)
    else:
        result = runner.weekly_review(dry_run=args.dry_run)
    print(
        json.dumps(
            result,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
