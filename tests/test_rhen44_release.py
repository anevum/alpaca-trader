import asyncio
from types import SimpleNamespace

import app.main as main
from app.rhen44_release import release_status


def test_configuration_cannot_grant_promotion_or_broker_authority():
    for market in (False, True):
        for broker in (False, True):
            for command in (False, True):
                config = main.settings.model_copy(update={
                    "rhen_market_stream_enabled": market,
                    "rhen_broker_stream_shadow_enabled": broker,
                    "command_live_stream_enabled": command,
                })
                for observer in (None, SimpleNamespace(broker=SimpleNamespace(state="LIVE"))):
                    status = release_status(config, observer)
                    assert not status["broker_write_authority"]
                    assert not status["promotion_eligible"]
                    assert not status["adaptive_active_available"]
                    assert status["champion_behavior"] == "4.3"
                    assert status["command_stream_available"] == (observer is not None and command)
                    assert status["promotion_blockers"]


def test_health_reports_release_without_creating_observer(monkeypatch):
    monkeypatch.setattr(main, "shadow_fabric", None)
    monkeypatch.setattr(main, "settings", main.settings.model_copy(update={
        "rhen_market_stream_enabled": False,
        "rhen_broker_stream_shadow_enabled": False,
        "command_live_stream_enabled": False,
    }))
    status = asyncio.run(main.health())["rhen44"]
    assert status["state"] == "IMPLEMENTED_GATED"
    assert not any(status["configured"].values())
    assert main.shadow_fabric is None
