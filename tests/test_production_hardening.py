import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.research_agent import gateway, service
from app.slack_notifier import SlackNotifier
from scripts.research_cron import due, successful


def test_scheduler_tolerates_late_start_and_daylight_saving():
    assert due(datetime(2026, 9, 28, 20, 14, tzinfo=timezone.utc))
    assert not due(datetime(2026, 9, 28, 21, 14, tzinfo=timezone.utc))
    assert due(datetime(2026, 12, 1, 21, 14, tzinfo=timezone.utc))
    assert not due(datetime(2026, 12, 1, 20, 14, tzinfo=timezone.utc))
    assert not due(datetime(2026, 10, 3, 20, 14, tzinfo=timezone.utc))


def test_http_200_is_not_proof_of_research_completion():
    assert successful({"status": "NOOP", "persisted": True})
    assert successful({"status": "NOOP", "reason": "duplicate_run_key"})
    assert not successful({"status": "FAILED", "persisted": True})
    assert not successful({"status": "COMPLETED", "persisted": False})


def test_gateway_retries_transient_read_failure_without_repeating_writes(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        if len(calls) < 3:
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(200, json={"ok": True, "evidence": {}})
    original = httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    async def no_sleep(_): pass
    monkeypatch.setattr(gateway.asyncio, "sleep", no_sleep)
    assert asyncio.run(gateway.ResearchGateway("https://fixture.invalid", "synthetic").fetch_document()) == {}
    assert calls == ["GET"] * 3


def test_gateway_does_not_retry_invalid_credentials(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(401)
    original = httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(gateway.ResearchGatewayError):
        asyncio.run(gateway.ResearchGateway("https://fixture.invalid", "synthetic").fetch_document())
    assert calls == ["GET"]


def test_concurrent_reviews_cannot_both_enter_model_or_persistence(monkeypatch):
    async def scenario():
        monkeypatch.setattr(service, "review_lock", asyncio.Lock())
        monkeypatch.setattr(service, "_require_operator", lambda _: None)
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def work(*args):
            calls.append(1); started.set(); await release.wait()
            return {"status": "NOOP", "persisted": False}
        monkeypatch.setattr(service, "_review", work)
        first = asyncio.create_task(service.review(service.ReviewRequest(), "test"))
        await started.wait()
        with pytest.raises(service.HTTPException) as exc:
            await service.review(service.ReviewRequest(), "test")
        assert exc.value.status_code == 409
        release.set(); await first
        assert len(calls) == 1
    asyncio.run(scenario())


def test_reconciliation_recovery_and_second_failure_are_not_suppressed():
    notifier = SlackNotifier(SimpleNamespace(slack_webhook_url="https://fixture.invalid"))
    for action in ("safe", "error", "safe", "error"):
        notifier.record_event({"kind": "reconciliation", "action": action, "message": action})
    assert notifier._queue.qsize() == 4


def test_deployment_provenance_takes_precedence_over_old_manual_pin(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("RHEN_RESEARCH_SOURCE_COMMIT", "b" * 40)
    assert service._source_commit() == "a" * 40


def test_staging_rejects_network_when_active():
    import os, socket
    if os.environ.get("RHEN_STAGING") == "1":
        with pytest.raises(RuntimeError, match="staging forbids network"):
            socket.create_connection(("example.invalid", 443))


def test_preopen_does_not_mark_rejected_persistence_as_complete():
    from app.preopen_state.service import _confirmed
    with pytest.raises(RuntimeError, match="persistence_unconfirmed"):
        _confirmed({"ok": False, "skipped": True})


def test_scheduler_docker_image_contains_its_entrypoint():
    from pathlib import Path
    assert "COPY scripts ./scripts" in Path("Dockerfile").read_text()


def test_research_configuration_failure_returns_503(monkeypatch):
    monkeypatch.setattr(service, "_health", lambda: {"ok": False})
    with pytest.raises(service.HTTPException) as exc:
        asyncio.run(service.health())
    assert exc.value.status_code == 503


def test_daily_report_requires_durable_acknowledgement(monkeypatch):
    from datetime import date
    from app import research_scheduler as module
    async def account(): return {}
    async def reject(**kwargs): return False
    async def collect(*args):
        return {"metrics": {}, "funnel": {}, "data_quality_warnings": [],
                "positions": [], "trades": [], "reconstruction": {}}
    async def canonical(*args): return {}
    scheduler = module.ResearchReportScheduler(SimpleNamespace(), SimpleNamespace(account=account),
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(emit_critical=reject))
    monkeypatch.setattr(scheduler, "_collect", collect)
    monkeypatch.setattr(scheduler, "_daily_post_event_inputs", canonical)
    monkeypatch.setattr(scheduler, "_runtime_snapshot", lambda: {})
    monkeypatch.setattr(module, "classify_daily", lambda *args: {"classification": "KEEP"})
    monkeypatch.setattr(module, "next_research_action", lambda *args: "observe")
    with pytest.raises(RuntimeError, match="durably persisted"):
        asyncio.run(scheduler.generate_daily(date(2026, 9, 28)))
    assert scheduler.last_daily_report is None
