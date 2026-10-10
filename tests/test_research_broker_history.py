"""Read-only broker-history pagination and daily reporting completeness."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.alpaca_client import AlpacaClient
from app.research_scheduler import ResearchReportScheduler


NY = ZoneInfo("America/New_York")
START = datetime(2026, 10, 9, 0, 0, tzinfo=NY)
END = START + timedelta(days=1)
SUBMITTED = "2026-10-09T14:30:00Z"


def make_client(monkeypatch, handler):
    client = AlpacaClient(SimpleNamespace())
    async def read(method, path, **kwargs):
        assert method == "GET"
        return await handler(path, kwargs.get("params") or {})
    monkeypatch.setattr(client, "_request", read)
    return client


def order(identity, stamp=SUBMITTED):
    return {"id": identity, "submitted_at": stamp}


def test_research_orders_page_by_id_and_filter_session(monkeypatch):
    calls = []
    async def read(path, params):
        assert path == "/v2/orders"
        calls.append(params)
        if len(calls) == 1:
            return [order(f"o-{i}") for i in range(500)]
        return [order("old", "2026-10-08T16:00:00Z"), order("after", "2026-10-10T14:00:00Z")]
    client = make_client(monkeypatch, read)
    results = asyncio.run(client.research_orders_for_window(
        start_at=START, end_at=END
    ))
    assert len(results) == 500
    assert len(calls) == 2
    assert calls[0]["limit"] == 500
    assert calls[1]["before_order_id"] == "o-499"
    assert "after" not in calls[1] and "until" not in calls[1]


def test_research_orders_fail_closed_at_page_cap_or_duplicate(monkeypatch):
    async def cap(path, params):
        return [order(f"full-{i}") for i in range(500)]
    client = make_client(monkeypatch, cap)
    with pytest.raises(RuntimeError, match="exceeded"):
        asyncio.run(client.research_orders_for_window(
            start_at=START, end_at=END, max_pages=1
        ))

    async def repeating(path, params):
        return [order(f"full-{i}") for i in range(500)]
    client = make_client(monkeypatch, repeating)
    with pytest.raises(RuntimeError, match="repeated"):
        asyncio.run(client.research_orders_for_window(
            start_at=START, end_at=END, max_pages=2
        ))


def test_fill_pagination_uses_activity_cursor_and_never_silently_truncates(monkeypatch):
    calls = []
    async def read(path, params):
        assert path == "/v2/account/activities/FILL"
        calls.append(params)
        if len(calls) == 1:
            return [{"id": f"fill-{i}"} for i in range(100)]
        return [{"id": "fill-101"}]
    client = make_client(monkeypatch, read)
    fills = asyncio.run(client.research_fills_for_session(date="2026-10-09"))
    assert len(fills) == 101
    assert calls[0]["date"] == "2026-10-09"
    assert calls[0]["page_size"] == 100
    assert calls[1]["page_token"] == "fill-99"

    async def saturate(path, params):
        return [{"id": f"fill-{i}"} for i in range(100)]
    client = make_client(monkeypatch, saturate)
    with pytest.raises(RuntimeError, match="exceeded"):
        asyncio.run(client.research_fills_for_session(
            date="2026-10-09", max_pages=1
        ))
    with pytest.raises(ValueError):
        asyncio.run(client.research_fills_for_session(date="not-a-date"))


def test_daily_collect_uses_only_paginated_readers_and_attests_scope():
    calls = []
    class Broker:
        async def research_orders_for_window(self, *, start_at, end_at):
            calls.append(("orders", start_at, end_at))
            return []
        async def research_fills_for_session(self, *, date):
            calls.append(("fills", date))
            return []
        async def positions(self):
            return []
        async def recent_orders(self, **kwargs):
            raise AssertionError("unpaginated legacy request must not run")
        async def fill_activities(self, **kwargs):
            raise AssertionError("unpaginated legacy request must not run")
    class Market:
        async def market_calendar(self, start, end):
            return [date(2026, 10, 9)]
        async def historical_bars_many(self, *args, **kwargs):
            return {}
    scheduler = ResearchReportScheduler(
        SimpleNamespace(order_owner_tag=""),
        Broker(), Market(), SimpleNamespace(decision_history=[]), None
    )
    result = asyncio.run(scheduler._collect(
        date(2026, 10, 9), date(2026, 10, 9)
    ))
    assert result["broker_history"]["pagination"] == "EXHAUSTED_WITHIN_BOUNDS"
    assert result["broker_history"]["order_count"] == 0
    assert result["broker_history"]["fill_count"] == 0
    assert calls[0][1].tzinfo is not None
    assert calls[0][2] > calls[0][1]
    assert calls[1] == ("fills", "2026-10-09")
