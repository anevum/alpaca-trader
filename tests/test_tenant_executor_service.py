from pathlib import Path

import pytest

from app import tenant_executor_service


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app" / "tenant_executor_service.py").read_text().lower()


def test_tenant_executor_service_is_explicitly_enabled_and_paper_only(monkeypatch):
    monkeypatch.delenv("TENANT_EXECUTOR_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="not_enabled"):
        import asyncio
        asyncio.run(tenant_executor_service.run_cycle())

    assert "tenantalpacapaperexecutionclient" in SOURCE
    assert "tenantalpacareadclient" not in SOURCE
    assert "api.alpaca.markets" not in SOURCE


def test_tenant_executor_service_requires_source_revision(monkeypatch):
    monkeypatch.setenv("TENANT_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    with pytest.raises(RuntimeError, match="source_commit_required"):
        import asyncio
        asyncio.run(tenant_executor_service.run_cycle())


def test_service_logs_never_include_secret_environment_names():
    assert "command_paper_envelope_key_b64" not in SOURCE
    assert "alpaca_oauth" not in SOURCE
    assert "authorization" not in SOURCE
