"""Safety and transport regressions for aggregate-only canonical RHEN SSE."""
import asyncio
import json

import pytest

from app.rhen_core.public_events import PublicEventBus


def projection(observed_at="2026-10-08T15:00:00Z"):
    return {
        "ok": True,
        "source": "rhen-core-sqlite",
        "generated_at": observed_at,
        "disclosure": {"level": "aggregate_only"},
        "events": [{"label": "Scanner observed universe", "at": observed_at}],
    }


def test_persisted_events_notify_clients_without_polling():
    async def run():
        bus = PublicEventBus()
        bus.start()
        first = bus.subscribe()
        second = bus.subscribe()
        calls = []
        def read():
            calls.append(True)
            return projection()
        initial = await bus.snapshot(read)
        assert initial["ok"] is True
        assert len(calls) == 1
        bus.signal()
        bus.signal()
        await asyncio.sleep(.01)
        # Slow clients get newest revision, not an unbounded backlog.
        assert first.qsize() == second.qsize() == 1
        assert first.get_nowait() == second.get_nowait() == bus.revision
        after_event = await bus.snapshot(read)
        await bus.snapshot(read)
        assert after_event == initial
        assert len(calls) == 2  # refreshed after persisted event
        bus.unsubscribe(first)
        bus.unsubscribe(second)
        bus.stop()
    asyncio.run(run())


def test_public_stream_rejects_private_and_nonfinite_payloads():
    safe = projection()
    frame = PublicEventBus.frame(safe)
    assert frame.startswith("event: snapshot\ndata:")
    assert frame.endswith("\n\n")
    assert json.loads(frame.partition("\ndata:")[2]) == safe
    with pytest.raises(ValueError, match="private"):
        PublicEventBus.frame({**safe, "disclosure": {"level": "private"}})
    with pytest.raises(ValueError):
        PublicEventBus.frame({**safe, "ok": False})
    with pytest.raises(ValueError):
        PublicEventBus.frame({**safe, "equity": float("nan")})


def test_client_cap_and_wrong_projection_fail_closed():
    async def run():
        bus = PublicEventBus()
        bus.start()
        queues = [bus.subscribe() for _ in range(bus.MAX_CLIENTS)]
        with pytest.raises(RuntimeError, match="capacity"):
            bus.subscribe()
        with pytest.raises(ValueError, match="unsafe"):
            await bus.snapshot(lambda: {
                "ok": True, "source": "rhen-core-sqlite",
                "disclosure": {"level": "private"},
            })
        for q in queues:
            bus.unsubscribe(q)
        bus.stop()
    asyncio.run(run())


def test_read_only_stream_url_is_explicitly_routed():
    from app.rhen_core.router import app
    path = "/v1/trading-public-events"
    assert any(
        getattr(route, "path", None) == path
        and "GET" in (getattr(route, "methods", set()) or set())
        for route in app.routes
    )
