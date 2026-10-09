"""Offline safety/contract tests for the isolated real-time observation child."""
from datetime import datetime, timezone

import pytest

from app.live_observer.service import (
    ReadOnlyObserver, clean_number, feed_window, symbol_scope
)
from app.rhen_core.supervisor import PROCESSES, _child_env


UTC = timezone.utc


def test_observer_is_isolated_in_one_railway_runtime(monkeypatch):
    specs = [item for item in PROCESSES if item.name == "observer"]
    assert len(specs) == 1
    spec = specs[0]
    assert spec.port == 8120
    assert spec.enabled_env == "RHEN_OBSERVER_ENABLED"
    assert spec.market_data_credentials is True
    monkeypatch.setenv("ALPACA_API_KEY", "read-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "read-secret")
    monkeypatch.setenv("ADMIN_TOKEN", "execution-token")
    monkeypatch.setenv("TRADING_INGEST_TOKEN", "ingest-token")
    monkeypatch.setenv("EXECUTION_ENABLED", "true")
    env = _child_env(spec)
    assert env["ALPACA_API_KEY"] == "read-key"
    assert env["EXECUTION_ENABLED"] == "false"
    assert env["LIVE_TRADING"] == "false"
    assert env["BOT_ARMED"] == "false"
    assert env["ADMIN_TOKEN"] == ""
    assert env["TRADING_INGEST_TOKEN"] == ""
    assert env["RHEN_CORE_TOKEN"] == ""
    assert env["GRAEN_GATEWAY_TOKEN"] == ""
    assert env["SCAN_ONLY"] == "true"


def test_bounded_watchlist_and_untrusted_symbols(monkeypatch):
    monkeypatch.setenv(
        "RHEN_OBSERVER_SYMBOLS",
        "SPY,QQQ,SPY,../ENV,IWM,A,B,C,D,E,F,G,HALT",
    )
    selected = symbol_scope()
    assert selected == ("SPY", "QQQ", "IWM", "A", "B", "C", "D", "E")
    assert len(selected) == 8
    assert "../ENV" not in selected


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("2026-10-08T12:00:00+00:00", True),
        ("2026-10-08T21:00:00+00:00", False),
        ("2026-10-09T01:00:00+00:00", False),
        ("2026-10-10T15:00:00+00:00", False),
        ("2026-10-08T23:30:00+00:00", False),
    ],
)
def test_window_never_pretends_market_data_are_live_outside_iex_hours(at, expected):
    assert feed_window(datetime.fromisoformat(at).astimezone(UTC)) is expected


def test_nonfinite_account_values_never_enter_chart():
    for raw in ("NaN", "Infinity", "-Infinity", object(), None):
        assert clean_number(raw) is None
    assert clean_number("14.23") == 14.23


def test_observer_snapshot_is_read_only_and_fails_closed_on_unverified_sip(monkeypatch):
    monkeypatch.setenv("RHEN_OBSERVER_SYMBOLS", "SPY,QQQ")
    monkeypatch.setenv("RHEN_OBSERVER_MARKET_FEED", "iex")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    observed = ReadOnlyObserver()
    snapshot = observed.snapshot()
    assert snapshot["visual_schema"] == "command-visual.v1"
    assert snapshot["system"]["broker_orders_possible"] is False
    assert snapshot["system"]["entry_authority"] is False
    assert snapshot["system"]["market_scope"] == "BOUNDED_OWNER_WATCHLIST"
    assert observed.store.symbols == ("SPY", "QQQ")
    assert observed.manager.store is observed.store

    monkeypatch.setenv("RHEN_OBSERVER_MARKET_FEED", "sip")
    monkeypatch.delenv("RHEN_OBSERVER_SIP_ENTITLED", raising=False)
    with pytest.raises(ValueError, match="entitlement"):
        ReadOnlyObserver()
