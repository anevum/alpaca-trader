"""Offline-only scorecard of frozen, independent rule-set tournaments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research_agent.rule_set_scorecard import paired_rule_set_scorecard


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare paired, session-bounded RHEN research tournaments."
    )
    parser.add_argument("--sessions-json", required=True)
    parser.add_argument("--output", help="Optional local JSON output path")
    args = parser.parse_args()
    source = Path(args.sessions_json)
    if not source.is_file() or source.stat().st_size > 10_000_000:
        parser.error("offline tournament input unavailable or exceeds 10MB")
    try:
        sessions = json.loads(source.read_text(encoding="utf-8"))
        scorecard = paired_rule_set_scorecard(sessions)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))
    payload = json.dumps(scorecard, sort_keys=True, indent=2)
    if args.output:
        destination = Path(args.output)
        if destination.resolve() == source.resolve():
            parser.error("scorecard output cannot overwrite source evidence")
        destination.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
