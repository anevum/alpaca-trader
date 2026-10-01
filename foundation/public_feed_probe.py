from __future__ import annotations

import asyncio
import os

import httpx


FORBIDDEN_KEYS = {
    "symbol",
    "symbols",
    "price",
    "qty",
    "quantity",
    "cash",
    "buying_power",
    "account_value",
    "equity",
    "order_id",
    "client_order_id",
}


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    base = ingest_url.rsplit("/v1/events", 1)[0]
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(base + "/v1/trading-public-feed")
        response.raise_for_status()
        body = response.json()

    if body.get("ok") is not True:
        raise RuntimeError("public feed did not report ok")
    if body.get("source") != "railway_postgresql_canonical":
        raise RuntimeError(f"wrong public feed source: {body.get('source')}")

    active = body.get("active_strategy") or {}
    version = str(active.get("version_id") or "")
    if version.startswith("FOUNDATION-"):
        raise RuntimeError("verification strategy leaked into public feed")

    leaked = sorted(
        {
            key.casefold()
            for key in walk(body)
            if key.casefold() in FORBIDDEN_KEYS
        }
    )
    if leaked:
        raise RuntimeError(f"private disclosure keys leaked: {leaked}")

    performance = body.get("market_performance") or {}
    for lane in ("equities", "crypto"):
        lane_body = performance.get(lane) or {}
        if lane_body.get("status") != "AWAITING_CANONICAL_PROJECTION":
            raise RuntimeError(f"{lane} projection was inferred before migration")

    print(
        "FOUNDATION_PUBLIC_FEED_PROBE_PASSED",
        {
            "source": body.get("source"),
            "state": body.get("state"),
            "strategy_version": version or None,
            "events_60m": (body.get("telemetry") or {}).get("events_60m"),
            "private_keys_exposed": False,
            "market_lane_projection_inferred": False,
            "supabase_required": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
