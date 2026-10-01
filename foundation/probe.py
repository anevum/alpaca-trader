from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from foundation.outbox import DurableEventOutbox, FoundationShadowSink


def probe_events() -> list[dict]:
    occurred_at = datetime(2026, 10, 1, 13, 20, tzinfo=timezone.utc).isoformat()
    run_id = "foundation-v2-staging-probe"
    return [
        {
            "event_key": f"{run_id}:probe:{index}",
            "run_id": run_id,
            "strategy_version_id": "FOUNDATION-PROBE-001",
            "event_type": "foundation_probe",
            "occurred_at": occurred_at,
            "symbol": None,
            "correlation_id": "foundation-v2-probe",
            "source": "foundation-probe",
            "payload": {
                "market_lane": "us_equity",
                "execution_mode": "shadow",
                "probe_index": index,
                "research_only": True,
                "broker_orders_possible": False,
            },
        }
        for index in range(1, 4)
    ]


async def main() -> None:
    ingest_url = os.environ.get("FOUNDATION_INGEST_URL", "").strip()
    if not ingest_url:
        raise SystemExit("FOUNDATION_INGEST_URL is required")
    outbox_path = Path(
        os.environ.get(
            "FOUNDATION_OUTBOX_PATH",
            "/data/foundation-probe.sqlite3",
        )
    )
    outbox = DurableEventOutbox(outbox_path)
    sink = FoundationShadowSink(outbox=outbox, ingest_url=ingest_url)

    events = probe_events()
    first_enqueued = sink.enqueue_many(events)
    first = await sink.flush_once(limit=10)
    if not first.get("ok"):
        raise RuntimeError(f"first delivery failed: {first}")

    second_enqueued = sink.enqueue_many(events)
    second = await sink.flush_once(limit=10)
    if not second.get("ok"):
        raise RuntimeError(f"duplicate delivery failed: {second}")

    async with httpx.AsyncClient(timeout=8.0) as http:
        status_url = ingest_url.rsplit("/v1/events", 1)[0] + "/status"
        response = await http.get(status_url)
        response.raise_for_status()
        status = response.json()

    if int(status.get("event_count") or 0) < 3:
        raise RuntimeError(f"ingest verification failed: {status}")
    if outbox.stats()["queued"] != 0:
        raise RuntimeError(f"outbox did not drain: {outbox.stats()}")

    print(
        "FOUNDATION_PROBE_PASSED",
        {
            "first_enqueued": first_enqueued,
            "first_remote": first.get("remote"),
            "second_enqueued": second_enqueued,
            "second_remote": second.get("remote"),
            "status": status,
            "outbox": outbox.stats(),
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
