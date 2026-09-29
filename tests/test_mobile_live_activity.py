from types import SimpleNamespace

from app.mobile_live_activity import MobileLiveActivityService


async def _snapshot_provider():
    return {}


def _settings(**overrides):
    values = {
        "trading_ingest_token": "x" * 40,
        "iren_mobile_registry_url": "https://example.test/registry",
        "iren_public_feed_url": "https://example.test/feed",
        "iren_apns_team_id": "",
        "iren_apns_key_id": "",
        "iren_apns_private_key": "",
        "iren_bundle_id": "com.anevum.iren",
        "iren_mobile_push_interval_seconds": 30,
        "iren_mobile_push_heartbeat_seconds": 300,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_mobile_service_reports_registry_without_apns():
    service = MobileLiveActivityService(_settings(), _snapshot_provider)
    status = service.status()
    assert status["registry_configured"] is True
    assert status["apns_configured"] is False
    assert status["bundle_id"] == "com.anevum.iren"


def test_mobile_service_apns_ready_when_signing_material_exists():
    service = MobileLiveActivityService(
        _settings(
            iren_apns_team_id="TEAM123",
            iren_apns_key_id="KEY123",
            iren_apns_private_key="PRIVATE",
        ),
        _snapshot_provider,
    )
    assert service.apns_configured is True


def test_content_state_matches_activitykit_contract():
    service = MobileLiveActivityService(_settings(), _snapshot_provider)
    private = {
        "market": {"is_open": True},
        "bot": {
            "runtime_paused": False,
            "bot_armed": True,
            "last_error": None,
        },
        "positions": [
            {"symbol": "AAA"},
            {"symbol": "BBB"},
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
        "performance": {
            "account_return_pct": 1.25,
            "max_drawdown_pct": 0.5,
            "curve": [
                {"return_pct": float(index) / 10.0}
                for index in range(40)
            ],
        },
        "telemetry": {"errors_2h": 0},
        "events": [
            {
                "at": "2026-09-28T19:34:00Z",
                "type": "execution",
                "kind": "learn",
                "label": "Broker state updated.",
            }
        ],
    }

    state = service._content_state(private, public)

    assert state["systemState"] == "RUNNING"
    assert state["marketState"] == "OPEN"
    assert state["accountReturnPct"] == 1.25
    assert state["drawdownPct"] == 0.5
    assert len(state["performancePoints"]) == 24
    assert state["openPositions"] == 2
    assert state["pendingOrders"] == 1
    assert state["errors2h"] == 0
    assert state["latestActivity"][0]["kind"] == "ORDER"
    assert state["latestActivity"][0]["isPrivate"] is True
    assert state["latestActivity"][1]["isPrivate"] is False
    assert isinstance(state["updatedAt"], float)


def test_content_state_degrades_on_runtime_error():
    service = MobileLiveActivityService(_settings(), _snapshot_provider)
    state = service._content_state(
        {
            "market": {"is_open": False},
            "bot": {"runtime_paused": False, "bot_armed": True, "last_error": "timeout"},
            "positions": [],
            "open_orders": [],
            "recent_orders": [],
        },
        {
            "performance": {"curve": []},
            "telemetry": {"errors_2h": 1},
            "events": [],
        },
    )
    assert state["systemState"] == "DEGRADED"
    assert state["marketState"] == "CLOSED"
    assert state["performancePoints"] == [0.0]
