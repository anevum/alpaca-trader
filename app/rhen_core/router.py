from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

EXECUTION_URL = "http://127.0.0.1:8101"
CORE_URL = "http://127.0.0.1:8102"

OPTIONAL_MODULE_ENVS = {
    "iren_executor": "IREN_EXECUTOR_ENABLED",
    "preopen": "PREOPEN_STATE_ENABLED",
    "crypto_research": "CRYPTO_RESEARCH_ENABLED",
}

MODULES = {
    "graen": "http://127.0.0.1:8110/health",
    "graen_research": "http://127.0.0.1:8111/health",
    "crypto_research": "http://127.0.0.1:8112/health",
    "velum": "http://127.0.0.1:8113/health",
    "research_agent": "http://127.0.0.1:8114/health",
    "nostra": "http://127.0.0.1:8115/health",
    "iren": "http://127.0.0.1:8116/health",
    "iren_executor": "http://127.0.0.1:8117/health",
    "preopen": "http://127.0.0.1:8118/health",
}

PUBLIC_MODULE_ROUTES = {
    "/v1/research/readiness/public": "http://127.0.0.1:8114/v1/readiness/public",
    "/v1/research/theory/public": "http://127.0.0.1:8114/v1/theory/public",
}

CORE_PREFIXES = (
    "/v1/internal/crypto-paper-assignment",
    "/v1/btc-discovery/",
    "/v1/events",
    "/v1/trading-report-read",
    "/v1/trading-public-feed",
    "/v1/strategy-pipeline",
    "/v1/trading-reconcile",
    "/v1/graen-gateway",
    "/v1/scheduler-gateway",
    "/v1/research-agent-gateway",
    "/v1/nostra-gateway",
    "/v1/maintenance/",
)

app = FastAPI(title="RHEN", version="3.0.0")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def enabled_modules() -> dict[str, str]:
    return {
        name: url
        for name, url in MODULES.items()
        if name not in OPTIONAL_MODULE_ENVS
        or _truthy(os.getenv(OPTIONAL_MODULE_ENVS[name]))
    }


async def _probe(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        response = await client.get(url)
        body = response.json() if response.content else {}
        return {
            "ok": response.status_code < 400 and bool(body.get("ok", True)),
            "status_code": response.status_code,
            "body": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
        }


@app.get("/health")
async def health() -> JSONResponse:
    active_modules = enabled_modules()
    async with httpx.AsyncClient(timeout=4.0) as client:
        execution, core, *module_rows = await asyncio.gather(
            _probe(client, EXECUTION_URL + "/health"),
            _probe(client, CORE_URL + "/ready"),
            *(_probe(client, url) for url in active_modules.values()),
        )
    modules: dict[str, dict[str, Any]] = {
        name: {
            "ok": True,
            "enabled": False,
            "optional": True,
            "status": "DISABLED",
        }
        for name in OPTIONAL_MODULE_ENVS
        if name not in active_modules
    }
    modules.update(dict(zip(active_modules, module_rows)))
    critical_ok = bool(execution.get("ok") and core.get("ok"))
    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content={
            "ok": critical_ok,
            "system": "RHEN",
            "runtime": "rhen-unified-v3",
            "execution": execution,
            "core": core,
            "modules": modules,
            "module_failures": [
                name
                for name, row in modules.items()
                if row.get("enabled") is not False and not row.get("ok")
            ],
        },
    )


@app.get("/v1/core/status")
async def core_status() -> Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(CORE_URL + "/status")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    route = "/" + path
    public_module_target = PUBLIC_MODULE_ROUTES.get(route)
    if public_module_target:
        target_url = public_module_target
    else:
        target = (
            CORE_URL
            if route == "/core"
            or route.startswith("/core/")
            or route.startswith(CORE_PREFIXES)
            else EXECUTION_URL
        )
        if route == "/core":
            route = "/"
        elif route.startswith("/core/"):
            route = route[len("/core"):]
        target_url = target + route

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.request(
                request.method,
                target_url,
                params=request.query_params,
                headers=headers,
                content=body,
            )
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error": "rhen_internal_route_unavailable",
                    "error_type": type(exc).__name__,
                },
            )
    excluded = {
        "content-length",
        "transfer-encoding",
        "connection",
        "content-encoding",
    }
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in excluded
    }
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )
