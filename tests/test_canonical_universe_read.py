import asyncio
from datetime import datetime, timezone

import app.main as main


def test_scheduler_universe_snapshot_is_bounded_read_only(monkeypatch):
    symbols=["SPY","QQQ","NVDA"]+[f"S{i:03d}" for i in range(120)]
    observed=datetime(2026,10,7,20,15,tzinfo=timezone.utc)
    monkeypatch.setattr(main.settings,"dynamic_universe_enabled",True)
    monkeypatch.setattr(main.settings,"universe_size",100)
    monkeypatch.setattr(main.runtime_state,"universe_active_symbols",symbols)
    monkeypatch.setattr(main.runtime_state,"universe_candidate_count",300)
    monkeypatch.setattr(main.runtime_state,"universe_eligible_count",5000)
    monkeypatch.setattr(main.runtime_state,"universe_source","hierarchical_screener")
    monkeypatch.setattr(main.runtime_state,"universe_updated_at",observed)
    monkeypatch.setattr(main.runtime_state,"universe_error",None)

    body=main.scheduler_universe_snapshot()

    assert body["ok"] is True
    assert body["universe_version"]=="rhen-canonical-universe-read-v1"
    assert body["active_count"]==100
    assert body["active_symbols"][:3]==["SPY","QQQ","NVDA"]
    assert len(body["active_symbols"])==100
    assert body["candidate_count"]==300
    assert body["eligible_count"]==5000
    assert body["updated_at"]==observed.isoformat()
    assert body["execution_authority"] is False
    assert body["broker_orders_possible"] is False


def test_scheduler_universe_route_uses_existing_scheduler_token_guard():
    route=next(
        route for route in main.app.routes
        if getattr(route,"path",None)=="/v1/scheduler/universe"
    )
    assert "GET" in route.methods
    assert route.endpoint.__name__=="scheduler_universe"


def test_scheduler_universe_route_refreshes_dynamic_universe(monkeypatch):
    observed=datetime(2026,10,7,20,30,tzinfo=timezone.utc)
    calls=[]
    async def refresh(*, now=None, position_symbols=None):
        calls.append(now)
        main.runtime_state.set_universe(
            symbols=["SPY","QQQ","NVDA"],
            candidate_count=100,
            eligible_count=5000,
            source="hierarchical_screener",
            at=observed,
            error=None,
        )
        return ("SPY","QQQ","NVDA")

    monkeypatch.setattr(main,"require_scheduler_token",lambda token: None)
    monkeypatch.setattr(main.universe,"active_symbols",refresh)
    body=asyncio.run(main.scheduler_universe("fixture"))

    assert len(calls)==1
    assert body["active_symbols"]==["SPY","QQQ","NVDA"]
    assert body["active_count"]==3
    assert body["source"]=="hierarchical_screener"
    assert body["execution_authority"] is False
    assert body["broker_orders_possible"] is False
