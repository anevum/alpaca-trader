from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

EXECUTION_URL = "http://127.0.0.1:8101"
CORE_URL = "http://127.0.0.1:8102"

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

CORE_PREFIXES = (
    "/v1/events",
    "/v1/graen-gateway",
    "/v1/scheduler-gateway",
    "/v1/research-agent-gateway",
    "/v1/nostra-gateway",
    "/v1/maintenance/",
)

app = FastAPI(title="RHEN", version="3.0.0")


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
    async with httpx.AsyncClient(timeout=4.0) as client:
        execution, core, *module_rows = await asyncio.gather(
            _probe(client, EXECUTION_URL + "/health"),
            _probe(client, CORE_URL + "/ready"),
            *(_probe(client, url) for url in MODULES.values()),
        )
    modules = dict(zip(MODULES, module_rows))
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
                name for name, row in modules.items() if not row.get("ok")
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
                target + route,
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
