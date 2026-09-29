from types import SimpleNamespace

from app.pushward_live import PushWardLiveService


async def _snapshot_provider():
    return {}


def _settings(**overrides):
    values = {
        "pushward_api_key": "hlk_" + "a" * 32,
        "pushward_api_url": "https://api.pushward.app",
        "iren_public_feed_url": "https://example.test/feed",
        "pushward_interval_seconds": 300,
        "pushward_heartbeat_seconds": 1800,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pushward_service_configuration_and_slugs():
    service = PushWardLiveService(_settings(), _snapshot_provider)
    status = service.status()
    assert status["configured"] is True
    assert status["activities"] == [
        "rhen-performance",
        "rhen-activity",
        "rhen-status",
    ]


def test_pushward_display_model_is_sanitized_and_compact():
    service = PushWardLiveService(_settings(), _snapshot_provider)
    private = {
        "market": {"is_open": True},
        "bot": {
            "runtime_paused": False,
            "bot_armed": True,
            "last_error": None,
        },
        "positions": [
            {"symbol": "AAA", "side": "long", "unrealized_plpc": "0.0042"},
            {"symbol": "BBB", "side": "long", "unrealized_plpc": "-0.0010"},
        ],
        "open_orders": [
            {"status": "new"},
            {"status": "filled"},
        ],
        "recent_orders": [
            {
                "id": "order-1",
                "symbol": "AAA",
                "side": "buy",
                "status": "filled",
                "filled_at": "2026-09-28T19:35:12.605Z",
            }
        ],
    }
    public = {
        "performance": {"account_return_pct": 1.234},
        "telemetry": {"errors_2h": 0},
        "events": [
            {
                "at": "2026-09-28T19:34:00Z",
                "kind": "learn",
                "label": "Broker state updated.",
            }
        ],
    }

    model = service._display_model(private, public)

    assert model["system_state"] == "RUNNING"
    assert model["market_state"] == "OPEN"
    assert model["return_pct"] == 1.234
    assert model["open_positions"] == 2
    assert model["pending_orders"] == 1
    assert model["errors_2h"] == 0
    assert model["activity_lines"][0]["text"] == "ORDER · BUY AAA · FILLED"
    assert "POSITION · LONG AAA" in [
        line["text"] for line in model["activity_lines"]
    ]

    serialized = str(model)
    assert "qty" not in serialized
    assert "market_value" not in serialized
    assert "filled_avg_price" not in serialized


def test_pushward_display_model_degrades_on_error():
    service = PushWardLiveService(_settings(), _snapshot_provider)
    model = service._display_model(
        {
            "market": {"is_open": False},
            "bot": {
                "runtime_paused": False,
                "bot_armed": True,
                "last_error": "timeout",
            },
            "positions": [],
            "open_orders": [],
            "recent_orders": [],
        },
        {
            "performance": {"account_return_pct": -0.25},
            "telemetry": {"errors_2h": 1},
            "events": [],
        },
    )

    assert model["system_state"] == "DEGRADED"
    assert model["market_state"] == "CLOSED"
    assert model["return_pct"] == -0.25
