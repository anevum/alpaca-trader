from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
import sys

from .config import get_settings
from .velum_service import VelumRuntime


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run one bounded VELUM equity replay and exit."
    )
    value.add_argument(
        "--session",
        required=True,
        help="Completed U.S. equity session in YYYY-MM-DD form.",
    )
    return value


async def run(session: date) -> dict:
    runtime = VelumRuntime(get_settings())
    return await runtime.run_equity_session(session)


def main() -> int:
    args = parser().parse_args()
    try:
        session = date.fromisoformat(args.session)
        result = asyncio.run(run(session))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "broker_orders_possible": False,
                    "execution_authority": False,
                },
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
