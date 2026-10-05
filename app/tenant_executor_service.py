from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

from app.platform_core.broker import TenantAlpacaPaperExecutionClient
from foundation.command_platform import DatabaseEnvelopeSecretResolver
from foundation.tenant_executor_gateway import TenantPaperExecutor


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL_required")
    return value


def _source_commit() -> str:
    value = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        or os.environ.get("SOURCE_COMMIT", "").strip()
    )
    if not value:
        raise RuntimeError("tenant_executor_source_commit_required")
    return value


def _runtime_id() -> str:
    explicit = os.environ.get("TENANT_EXECUTOR_RUNTIME_ID", "").strip()
    if explicit:
        return explicit
    service = os.environ.get("RAILWAY_SERVICE_ID", "").strip()
    replica = os.environ.get("RAILWAY_REPLICA_ID", "").strip()
    if service:
        return "rhen-tenant-paper:" + service + (":" + replica if replica else "")
    return "rhen-tenant-paper:local"


def _log(event: str, **fields) -> None:
    payload = {
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


async def run_cycle() -> list[dict]:
    if not _truthy("TENANT_EXECUTOR_ENABLED"):
        raise RuntimeError("tenant_executor_not_enabled")

    database_url = _database_url()
    source_commit = _source_commit()
    runtime_id = _runtime_id()
    deployment_id = os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip() or None
    limit = max(1, min(int(os.environ.get("TENANT_EXECUTOR_SIGNAL_LIMIT", "25")), 100))

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        def client_factory(account):
            resolver = DatabaseEnvelopeSecretResolver(
                database_url,
                account.tenant_id,
                connection=conn,
            )
            return TenantAlpacaPaperExecutionClient(account, resolver)

        executor = TenantPaperExecutor(
            conn,
            client_factory=client_factory,
            runtime_id=runtime_id,
            source_commit=source_commit,
            deployment_id=deployment_id,
        )
        results = await executor.run_pending(limit=limit)
        _log(
            "tenant_executor_cycle_complete",
            runtime_id=runtime_id,
            source_commit=source_commit,
            signal_count=len(results),
        )
        return results


async def main() -> None:
    interval = max(
        1.0,
        min(float(os.environ.get("TENANT_EXECUTOR_INTERVAL_SECONDS", "5")), 60.0),
    )
    run_once = _truthy("TENANT_EXECUTOR_RUN_ONCE")

    if not _truthy("TENANT_EXECUTOR_ENABLED"):
        _log("tenant_executor_disabled")
        return

    while True:
        try:
            await run_cycle()
        except Exception as exc:
            _log(
                "tenant_executor_cycle_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            if run_once:
                raise
        if run_once:
            return
        await asyncio.sleep(interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _log(
            "tenant_executor_fatal",
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        sys.exit(1)
