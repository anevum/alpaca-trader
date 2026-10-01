from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx


NY = ZoneInfo("America/New_York")


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    token = os.environ["FOUNDATION_INGEST_TOKEN"].strip()
    base = ingest_url.rsplit("/v1/events", 1)[0]
    url = base + "/v1/trading-report-read"
    headers = {"x-anevum-ingest-token": token}
    today = datetime.now(timezone.utc).astimezone(NY).date()
    previous = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())

    async with httpx.AsyncClient(timeout=20.0) as http:
        async def get(**params: str):
            response = await http.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("ok"):
                raise RuntimeError(f"report read contract failed: {params}: {payload}")
            return payload

        promotion = await get(crypto_promotion="1")
        evidence = promotion.get("evidence")
        if not isinstance(evidence, dict):
            raise RuntimeError("crypto promotion evidence missing")
        if evidence.get("execution_authority") is not False:
            raise RuntimeError("crypto promotion evidence must not have execution authority")

        crypto_today = await get(crypto_evidence_session=today.isoformat())
        crypto_previous = await get(crypto_evidence_session=previous.isoformat())
        equity_today = await get(evidence_session=today.isoformat())
        latest_daily = await get(latest="daily")
        latest_weekly = await get(latest="weekly")
        command = await get(latest="command")
        weekly = await get(start=week_start.isoformat(), end=today.isoformat())

    if not isinstance(crypto_today.get("candidates"), list):
        raise RuntimeError("crypto candidates contract invalid")
    if not isinstance(equity_today.get("candidates"), list):
        raise RuntimeError("equity candidates contract invalid")
    if not isinstance(weekly.get("inputs"), dict):
        raise RuntimeError("weekly inputs contract invalid")
    if not isinstance(command.get("telemetry_health"), dict):
        raise RuntimeError("command evidence telemetry contract invalid")

    print(
        "FOUNDATION_REPORT_READ_PROBE_PASSED",
        {
            "crypto_candidates_today": len(crypto_today.get("candidates") or []),
            "crypto_candidates_previous": len(crypto_previous.get("candidates") or []),
            "equity_candidates_today": len(equity_today.get("candidates") or []),
            "promotion_resolved_candidates": int(
                evidence.get("resolved_candidate_predictions") or 0
            ),
            "daily_report_present": isinstance(latest_daily.get("report"), dict),
            "weekly_report_present": isinstance(latest_weekly.get("report"), dict),
            "weekly_daily_reports": len(
                (weekly.get("inputs") or {}).get("daily_reports") or []
            ),
            "canonical_store": (
                command.get("telemetry_health") or {}
            ).get("canonical_store"),
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
