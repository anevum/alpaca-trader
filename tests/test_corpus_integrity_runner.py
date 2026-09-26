import asyncio
from datetime import datetime, timezone

from app import corpus_integrity_runner as runner


class FakeSettings:
    credentials_configured = True
    bar_timeframe = "1Min"
    data_feed = "iex"
    data_base_url = "https://data.example.test"


class FakeMarketData:
    settings = FakeSettings()
    headers = {"x-test": "1"}

    def _batches(self, symbols):
        return [list(symbols)]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, headers, params):
        self.calls.append((url, headers, params))
        return FakeResponse(self.payloads.pop(0))


def test_paginated_fetch_marks_normal_completion(monkeypatch):
    client = FakeClient(
        [
            {
                "bars": {"AAPL": [{"t": "2026-01-05T14:31:00Z"}]},
                "next_page_token": "page-2",
            },
            {
                "bars": {"AAPL": [{"t": "2026-01-05T14:32:00Z"}]},
                "next_page_token": None,
            },
        ]
    )
    monkeypatch.setattr(runner.httpx, "AsyncClient", lambda timeout: client)

    result = asyncio.run(
        runner._fetch_with_pagination_diagnostics(
            FakeMarketData(),
            ["AAPL"],
            start=datetime(2026, 1, 5, tzinfo=timezone.utc),
            end=datetime(2026, 1, 6, tzinfo=timezone.utc),
        )
    )

    batch = result["pagination"]["batches"][0]
    assert len(result["bars"]["AAPL"]) == 2
    assert batch["pages_fetched"] == 2
    assert batch["pagination_complete"] is True
    assert batch["next_page_token_remaining"] is False
    assert result["pagination"]["pagination_complete"] is True


def test_paginated_fetch_reports_incomplete_at_page_ceiling(monkeypatch):
    client = FakeClient(
        [
            {
                "bars": {"AAPL": [{"t": "2026-01-05T14:31:00Z"}]},
                "next_page_token": "page-2",
            },
            {
                "bars": {"AAPL": [{"t": "2026-01-05T14:32:00Z"}]},
                "next_page_token": "page-3",
            },
        ]
    )
    monkeypatch.setattr(runner.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(runner, "PAGINATION_PAGE_LIMIT", 2)

    result = asyncio.run(
        runner._fetch_with_pagination_diagnostics(
            FakeMarketData(),
            ["AAPL"],
            start=datetime(2026, 1, 5, tzinfo=timezone.utc),
            end=datetime(2026, 1, 6, tzinfo=timezone.utc),
        )
    )

    batch = result["pagination"]["batches"][0]
    assert batch["pages_fetched"] == 2
    assert batch["pagination_complete"] is False
    assert batch["next_page_token_remaining"] is True
    assert result["pagination"]["pagination_complete"] is False
