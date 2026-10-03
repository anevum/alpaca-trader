from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from app.alpaca_client import AlpacaClient


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


def _settings():
    return SimpleNamespace(
        credentials_configured=True,
        base_url="https://broker.test",
        alpaca_api_key="key",
        alpaca_api_secret="secret",
    )


def test_idempotent_broker_get_retries_transient_read_timeout(monkeypatch):
    attempts = {"count": 0}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise httpx.ReadTimeout(
                    "transient",
                    request=httpx.Request(method, url),
                )
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = AlpacaClient(_settings())
    result = asyncio.run(client._request("GET", "/v2/account"))

    assert result == {"ok": True}
    assert attempts["count"] == 3


def test_non_idempotent_broker_post_is_never_retried(monkeypatch):
    attempts = {"count": 0}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            attempts["count"] += 1
            raise httpx.ReadTimeout(
                "transient",
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = AlpacaClient(_settings())

    try:
        asyncio.run(client._request("POST", "/v2/orders", json={"symbol": "BTC/USD"}))
    except httpx.ReadTimeout:
        pass
    else:
        raise AssertionError("POST timeout must propagate without retry")

    assert attempts["count"] == 1
