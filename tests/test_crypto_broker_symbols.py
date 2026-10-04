from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.alpaca_client import AlpacaClient, _canonical_crypto_symbols
from app.crypto_execution import CryptoExecutionEngine
from app.risk import validate_crypto_buy


@pytest.mark.parametrize("raw,expected", [
    ({"asset_class": "crypto", "symbol": "BTCUSD"}, "BTC/USD"),
    ({"asset_class": "crypto", "symbol": "ETHUSD"}, "ETH/USD"),
    ({"class": "crypto", "symbol": "USDTUSD"}, "USDT/USD"),
    ({"asset_class": "crypto", "symbol": "BTC/USD"}, "BTC/USD"),
    ({"asset_class": "us_equity", "symbol": "BTCUSD"}, "BTCUSD"),
    ({"symbol": "BTCUSD"}, "BTCUSD"),
    ({"asset_class": "crypto", "symbol": "BTCUSDT"}, "BTCUSDT"),
])
def test_crypto_identity_requires_broker_asset_class(raw, expected):
    original = dict(raw)
    result = _canonical_crypto_symbols(raw)
    assert result["symbol"] == expected
    assert raw == original
    if expected != raw["symbol"]:
        assert result["broker_symbol"] == raw["symbol"]
    assert _canonical_crypto_symbols(result) == result


@pytest.mark.parametrize("payload", [None, {}, {"ok": True}, "unchanged"])
def test_non_asset_responses_are_unchanged(payload):
    assert _canonical_crypto_symbols(payload) == payload


def test_actual_broker_boundary_preserves_position_ownership_and_protection(monkeypatch):
    position = {
        "asset_class": "crypto", "symbol": "BTCUSD", "asset_id": "btc-asset",
        "qty": "0.000118079", "market_value": "10.075398",
        "avg_entry_price": "85323.31",
    }
    buy = {
        "asset_class": "crypto", "symbol": "BTC/USD", "id": "buy-id",
        "client_order_id": "anevum-crypto-btc-usd-buy-test",
        "side": "buy", "status": "filled",
    }
    stop = {
        "asset_class": "crypto", "symbol": "BTCUSD", "id": "stop-id",
        "client_order_id": "anevum-crypto-btc-usd-hardstop-test",
        "side": "sell", "status": "new", "qty": position["qty"],
    }
    equity = {"asset_class": "us_equity", "symbol": "AAPL", "qty": "1"}
    calls = []

    class Http:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/positions"):
                body = [position, equity]
            elif kwargs.get("params", {}).get("status") == "open":
                body = [stop]
            else:
                body = [buy]
            return httpx.Response(200, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "AsyncClient", Http)
    client = AlpacaClient(SimpleNamespace(
        credentials_configured=True, base_url="https://broker.test",
        alpaca_api_key="test-only", alpaca_api_secret="test-only",
    ))

    async def scenario():
        positions = await client.positions()
        orders = await client.recent_orders()
        protection = await client.open_orders()
        crypto = CryptoExecutionEngine._crypto_positions(positions)
        assert len(crypto) == 1
        assert crypto[0]["symbol"] == "BTC/USD"
        assert crypto[0]["broker_symbol"] == "BTCUSD"
        assert crypto[0]["qty"] == position["qty"]
        assert positions[1] == equity
        assert CryptoExecutionEngine._owned_symbols(positions, orders) == {"BTC/USD"}
        assert CryptoExecutionEngine._protective_order(protection, "BTC/USD")["id"] == "stop-id"
        settings = SimpleNamespace(
            crypto_lane_enabled=True, crypto_execution_enabled=True,
            execution_authorized=True,
        )
        result = validate_crypto_buy(
            settings, "BTC/USD", Decimal("5"), {}, positions, 0,
        )
        assert result.allowed is False
        assert result.reason == "BTC/USD crypto position is already open"

    asyncio.run(scenario())
    assert len(calls) == 3
    assert all(method == "GET" for method, _, _ in calls)
    assert position["symbol"] == "BTCUSD"


def test_submission_keeps_outgoing_pair_and_provider_evidence(monkeypatch):
    sent = []

    class Http:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def request(self, method, url, **kwargs):
            sent.append(kwargs["json"])
            return httpx.Response(200, json={
                "asset_class": "crypto", "symbol": "BTCUSD", "id": "buy-1",
                "status": "accepted", "filled_qty": "0",
            }, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "AsyncClient", Http)
    client = AlpacaClient(SimpleNamespace(
        credentials_configured=True, base_url="https://broker.test",
        alpaca_api_key="test-only", alpaca_api_secret="test-only",
    ))
    result = asyncio.run(client.submit_crypto_market_buy(
        symbol="BTC/USD", qty="0.0001", client_order_id="test-id",
    ))
    assert len(sent) == 1
    assert sent[0]["symbol"] == "BTC/USD"
    assert result["symbol"] == "BTC/USD"
    assert result["broker_symbol"] == "BTCUSD"
    assert result["id"] == "buy-1"
    assert result["filled_qty"] == "0"
