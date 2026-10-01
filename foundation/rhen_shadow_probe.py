from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import httpx

from app.persistence import TradingEventSink


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        trading_ingest_url="",
        trading_ingest_token="",
        trading_run_id="foundation-rhen-shadow-probe",
        strategy_version_id="FOUNDATION-RHEN-SHADOW-001",
        trading_run_started_at=None,
        foundation_shadow_enabled=True,
        foundation_outbox_path=os.environ.get(
            "FOUNDATION_OUTBOX_PATH",
            "/data/rhen-foundation-evidence.sqlite3",
        ),
        foundation_ingest_url=os.environ["FOUNDATION_INGEST_URL"],
        foundation_flush_seconds=0.1,
        foundation_batch_size=25,
    )


async def main() -> None:
    sink = TradingEventSink(settings())
    if sink.enabled:
        raise RuntimeError("legacy persistence must be disabled in this probe")
    if not sink.foundation_enabled:
        raise RuntimeError("foundation shadow persistence is not enabled")

    await sink.start()
    try:
        for index in range(1, 4):
            sink.emit(
                event_type="rhen_shadow_probe",
                event_key=f"foundation-rhen-shadow-probe:event:{index}",
                correlation_id="foundation-rhen-shadow-probe",
                payload={
                    "market_lane": "us_equity",
                    "execution_mode": "shadow",
                    "probe_index": index,
                    "broker_orders_possible": False,
                },
            )

        for _ in range(100):
            state = sink.status()["foundation"]
            if state["outbox"]["queued"] == 0 and state["delivered_count"] >= 3:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError(f"RHEN shadow outbox did not drain: {sink.status()}")

        base = settings().foundation_ingest_url.rsplit("/v1/events", 1)[0]
        async with httpx.AsyncClient(timeout=8.0) as http:
            response = await http.get(base + "/status")
            response.raise_for_status()
            remote = response.json()

        print(
            "RHEN_FOUNDATION_SHADOW_PROBE_PASSED",
            {
                "sink": sink.status(),
                "remote": remote,
                "execution_authority": False,
                "broker_credentials_present": False,
            },
            flush=True,
        )
    finally:
        await sink.stop()


if __name__ == "__main__":
    asyncio.run(main())
