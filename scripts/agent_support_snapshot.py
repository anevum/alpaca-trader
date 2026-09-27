"""Produce a bounded deterministic snapshot from an authorized evidence bundle.

Usage: python -m scripts.agent_support_snapshot evidence.json
The input adapter must fetch fresh canonical data and Railway observations.
No broker call, LLM, secret output, or production mutation is performed here.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.agent_support.snapshot import build_snapshot


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.agent_support_snapshot evidence.json", file=sys.stderr)
        return 2
    evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(build_snapshot(evidence, now=datetime.now(timezone.utc)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
