"""Bounded command-line research queue; no broker or production writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research_agent.offline_experiment_queue import OfflineExperimentQueue


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persist immutable offline GRAEN experiment proposals."
    )
    parser.add_argument("--db", required=True, help="Path to the isolated research SQLite file.")
    subs = parser.add_subparsers(dest="operation", required=True)

    proposed = subs.add_parser("propose")
    proposed.add_argument("--specs-json", required=True)
    proposed.add_argument("--source-fingerprint", required=True)

    transitioned = subs.add_parser("transition")
    transitioned.add_argument("experiment_id")
    transitioned.add_argument("state")
    transitioned.add_argument("--evidence-fingerprint", required=True)
    transitioned.add_argument("--checks-json")

    listed = subs.add_parser("list")
    listed.add_argument("--limit", type=int, default=100)

    history = subs.add_parser("history")
    history.add_argument("experiment_id")
    args = parser.parse_args()
    queue = OfflineExperimentQueue(args.db)
    if args.operation == "propose":
        specs = json.loads(Path(args.specs_json).read_text(encoding="utf-8"))
        if not isinstance(specs, list) or len(specs) > 20:
            raise ValueError("proposals must be a bounded array of at most 20 experiments")
        result = [
            queue.propose(spec, source_fingerprint=args.source_fingerprint)
            for spec in specs
        ]
    elif args.operation == "transition":
        checks = (
            json.loads(Path(args.checks_json).read_text(encoding="utf-8"))
            if args.checks_json else {}
        )
        if not isinstance(checks, dict):
            raise ValueError("evidence checks must be a JSON object")
        result = queue.transition(
            args.experiment_id, args.state,
            evidence_fingerprint=args.evidence_fingerprint,
            checks=checks,
        )
    elif args.operation == "history":
        result = queue.history(args.experiment_id)
    else:
        result = queue.list(limit=args.limit)
    print(json.dumps(result, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
