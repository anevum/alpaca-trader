from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.alpaca_client import AlpacaClient
from app.equity_sessions import (
    EquitySession,
    EquitySessionContext,
    EquitySessionResolver,
)
from app.extended_equity import ExtendedEquityEngine


NY = ZoneInfo("America/New_York")


class CalendarMarketData:
    async def market_calendar_details(self, *, start, end):
        rows = [
            {"date": date(2026, 10, 5), "open": "09:30", "close": "16:00"},
            {"date": date(2026, 10, 6), "open": "09:30", "close": "16:00"},
            {"date": date(2026, 10, 7), "open": "09:30", "close": "16:00"},
            {"date": date(2026, 10, 8), "open": "09:30", "close": "16:00"},
            {"date": date(2026, 10, 9), "open": "09:30", "close": "16:00"},
            {"date": date(2026, 10, 12), "open": "09:30", "close": "16:00"},
        ]
        return [row for row in rows if start <= row["date"] <= end]


def classify(stamp):
    return asyncio.run(EquitySessionResolver(CalendarMarketData()).classify(stamp))


def test_session_classifier_covers_alpaca_24x5_handoff():
    assert classify(datetime(2026, 10, 4, 20, 30, tzinfo=NY)).session == EquitySession.OVERNIGHT
    assert classify(datetime(2026, 10, 5, 3, 30, tzinfo=NY)).session == EquitySession.OVERNIGHT
    assert classify(datetime(2026, 10, 5, 5, 0, tzinfo=NY)).session == EquitySession.PREMARKET
    assert classify(datetime(2026, 10, 5, 10, 0, tzinfo=NY)).session == EquitySession.REGULAR
    assert classify(datetime(2026, 10, 5, 17, 0, tzinfo=NY)).session == EquitySession.AFTER_HOURS


def test_session_classifier_closes_friday_weekend_gap():
    result = classify(datetime(2026, 10, 9, 20, 1, tzinfo=NY))
    assert result.session == EquitySession.CLOSED
    assert result.tradable is False


def test_extended_order_adapter_never_uses_market_order(monkeypatch):
    settings = SimpleNamespace(credentials_configured=True)
    client = AlpacaClient(settings)
    captured = {}

    async def request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"id": "synthetic"}

    monkeypatch.setattr(client, "_request", request)
    asyncio.run(
        client.submit_extended_limit_buy(
            symbol="SPY",
            qty="0.1",
            limit_price="101.25",
            client_order_id="anevum-spy-buy-ext-test",
        )
    )

    payload = captured["json"]
    assert payload["type"] == "limit"
    assert payload["time_in_force"] == "day"
    assert payload["extended_hours"] is True
    assert payload["limit_price"] == "101.25"


def settings():
    return SimpleNamespace(
        extended_equity_strategy_version_id="RHEN-EXT-TEST",
        extended_equity_slow_window=8,
        extended_equity_fast_window=3,
        extended_equity_max_bar_age_seconds=120,
        extended_equity_min_price=Decimal("5"),
        extended_equity_min_momentum_pct=Decimal("0.0007"),
        extended_equity_max_vwap_extension_pct=Decimal("0.006"),
        extended_equity_confirmation_symbols=("SPY", "QQQ"),
        min_confirmations=1,
        extended_equity_stop_pct=Decimal("0.004"),
        extended_equity_target_pct=Decimal("0.006"),
        extended_equity_max_quote_age_seconds=45,
        extended_equity_max_spread_pct=Decimal("0.004"),
        overnight_max_spread_pct=Decimal("0.006"),
        extended_equity_data_feed="iex",
        overnight_data_feed="overnight",
        extended_equity_handoff_flat_minutes=5,
        extended_equity_weekend_flat_time=time(19, 45),
        extended_equity_max_hold_minutes=45,
        extended_equity_poll_seconds=30,
        order_owner_tag="fixture",
    )


def bar(minute, close):
    return {
        "t": f"2026-10-06T17:{minute:02d}:00-04:00",
        "o": str(close),
        "h": str(close + Decimal("0.02")),
        "l": str(close - Decimal("0.02")),
        "c": str(close),
        "v": "10000",
        "vw": str(close),
    }


def engine():
    state = SimpleNamespace(
        record_event=lambda **kwargs: None,
        current_correlation_id=None,
    )
    value = ExtendedEquityEngine(
        settings(),
        SimpleNamespace(),
        SimpleNamespace(),
        state,
        ledger=None,
    )
    prices = [
        Decimal("100.00"),
        Decimal("100.02"),
        Decimal("100.04"),
        Decimal("100.06"),
        Decimal("100.08"),
        Decimal("100.10"),
        Decimal("100.13"),
        Decimal("100.17"),
        Decimal("100.22"),
    ]
    value._bars["AAPL"] = [bar(index, price) for index, price in enumerate(prices)]
    value._bars["SPY"] = [bar(index, price) for index, price in enumerate(prices)]
    value._bars["QQQ"] = [bar(index, price) for index, price in enumerate(prices)]
    return value


def after_hours_context():
    return EquitySessionContext(
        session=EquitySession.AFTER_HOURS,
        target_session_date=date(2026, 10, 6),
        starts_at=datetime(2026, 10, 6, 16, 0, tzinfo=NY),
        ends_at=datetime(2026, 10, 6, 20, 0, tzinfo=NY),
        regular_open=datetime(2026, 10, 6, 9, 30, tzinfo=NY),
        regular_close=datetime(2026, 10, 6, 16, 0, tzinfo=NY),
        extended=True,
        tradable=True,
        reason="fixture",
    )


def test_extended_strategy_qualifies_fresh_tight_momentum():
    value = engine()
    now = datetime(2026, 10, 6, 17, 8, 30, tzinfo=NY)
    signal = value._evaluate(
        "AAPL",
        {"bp": "100.21", "ap": "100.23", "t": now.isoformat()},
        after_hours_context(),
        now,
        has_position=False,
        has_open_order=False,
    )
    assert signal.action == "buy"
    assert signal.metadata["execution_order_type"] == "limit"
    assert signal.metadata["extended_hours"] is True
    assert signal.metadata["equity_session"] == "after_hours"


def test_extended_strategy_rejects_stale_quote():
    value = engine()
    now = datetime(2026, 10, 6, 17, 8, 30, tzinfo=NY)
    signal = value._evaluate(
        "AAPL",
        {
            "bp": "100.21",
            "ap": "100.23",
            "t": "2026-10-06T17:06:00-04:00",
        },
        after_hours_context(),
        now,
        has_position=False,
        has_open_order=False,
    )
    assert signal.action == "hold"
    assert "stale" in signal.reason


def test_extended_position_ownership_survives_midnight_until_lane_exit():
    value = engine()
    position = {"symbol": "AAPL", "qty": "0.1"}
    orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "filled_qty": "0.1",
            "client_order_id": "anevum-aapl-buy-ext-fixture-1",
            "filled_at": "2026-10-05T23:55:00-04:00",
        }
    ]
    assert value._position_owned_by_lane(position, orders) is True

    orders.append(
        {
            "symbol": "AAPL",
            "side": "sell",
            "status": "filled",
            "filled_qty": "0.1",
            "client_order_id": "anevum-aapl-sell-ext-fixture-2",
            "filled_at": "2026-10-06T03:10:00-04:00",
        }
    )
    assert value._position_owned_by_lane(position, orders) is False


def test_premarket_handoff_blocks_new_extended_entries():
    value = engine()
    context = EquitySessionContext(
        session=EquitySession.PREMARKET,
        target_session_date=date(2026, 10, 6),
        starts_at=datetime(2026, 10, 6, 4, 0, tzinfo=NY),
        ends_at=datetime(2026, 10, 6, 9, 30, tzinfo=NY),
        regular_open=datetime(2026, 10, 6, 9, 30, tzinfo=NY),
        regular_close=datetime(2026, 10, 6, 16, 0, tzinfo=NY),
        extended=True,
        tradable=True,
        reason="fixture",
    )
    assert (
        value._entry_block_reason(
            context,
            datetime(2026, 10, 6, 9, 27, tzinfo=NY),
        )
        == "regular-session handoff window"
    )


def test_friday_flatten_window_blocks_weekend_carry():
    value = engine()
    context = EquitySessionContext(
        session=EquitySession.AFTER_HOURS,
        target_session_date=date(2026, 10, 9),
        starts_at=datetime(2026, 10, 9, 16, 0, tzinfo=NY),
        ends_at=datetime(2026, 10, 9, 20, 0, tzinfo=NY),
        regular_open=datetime(2026, 10, 9, 9, 30, tzinfo=NY),
        regular_close=datetime(2026, 10, 9, 16, 0, tzinfo=NY),
        extended=True,
        tradable=True,
        reason="fixture",
    )
    assert (
        value._entry_block_reason(
            context,
            datetime(2026, 10, 9, 19, 50, tzinfo=NY),
        )
        == "Friday weekend flatten window"
    )
