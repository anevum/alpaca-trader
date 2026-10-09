from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    app: str
    port: int
    critical: bool = False
    market_data_credentials: bool = False
    enabled_env: str | None = None


PROCESSES = (
    ProcessSpec("core", "app.rhen_core.service:app", 8102, critical=True),
    ProcessSpec(
        "execution",
        "app.main:app",
        8101,
        critical=True,
        market_data_credentials=True,
    ),
    ProcessSpec(
        "observer",
        "app.live_observer.service:app",
        8120,
        market_data_credentials=True,
        enabled_env="RHEN_OBSERVER_ENABLED",
    ),
    ProcessSpec(
        "graen",
        "app.graen.service:app",
        8110,
        enabled_env="GRAEN_RUNTIME_ENABLED",
    ),
    ProcessSpec(
        "velum",
        "app.velum_service:app",
        8113,
        market_data_credentials=True,
        enabled_env="VELUM_RUNTIME_ENABLED",
    ),
    ProcessSpec(
        "research-agent",
        "app.research_agent.service:app",
        8114,
        enabled_env="RHEN_RESEARCH_STANDALONE_RUNTIME_ENABLED",
    ),
    ProcessSpec(
        "nostra",
        "app.nostra.service:app",
        8115,
        enabled_env="NOSTRA_STANDALONE_RUNTIME_ENABLED",
    ),
    ProcessSpec("iren", "app.iren.service:app", 8116),
    ProcessSpec(
        "iren-executor",
        "app.iren.executor_service:app",
        8117,
        enabled_env="IREN_EXECUTOR_ENABLED",
    ),
    ProcessSpec(
        "preopen",
        "app.preopen_state.service:app",
        8118,
        market_data_credentials=True,
        enabled_env="PREOPEN_STATE_ENABLED",
    ),
    ProcessSpec("router", "app.rhen_core.router:app", 8080, critical=True),
)

PURE_CORE = {
    "core",
    "graen",
    "research-agent",
    "nostra",
    "iren",
    "iren-executor",
    "router",
}

LOOPBACK = {
    "TRADING_INGEST_URL": "http://127.0.0.1:8102/v1/events",
    "FOUNDATION_INGEST_URL": "http://127.0.0.1:8102/v1/events",
    "FOUNDATION_EVENTS_URL": "http://127.0.0.1:8102/v1/events",
    "GRAEN_GATEWAY_URL": "http://127.0.0.1:8102/v1/graen-gateway",
    "SCHEDULER_GATEWAY_URL": "http://127.0.0.1:8102/v1/scheduler-gateway",
    "RHEN_RESEARCH_GATEWAY_URL": (
        "http://127.0.0.1:8102/v1/research-agent-gateway"
    ),
    "NOSTRA_GATEWAY_URL": "http://127.0.0.1:8102/v1/nostra-gateway",
    "RHEN_TRADER_SCHEDULER_URL": "http://127.0.0.1:8101/v1/scheduler",
    "VELUM_SCHEDULER_URL": "http://127.0.0.1:8113/v1/scheduler",
    "RHEN_RESEARCH_REVIEW_URL": "http://127.0.0.1:8102/v1/research/review",
    "VELUM_SERVICE_URL": "http://127.0.0.1:8113",
    "GRAEN_SERVICE_URL": "http://127.0.0.1:8110",
    "IREN_EXECUTOR_URL": "http://127.0.0.1:8117",
    "IREN_RHEN_HEALTH_URL": "http://127.0.0.1:8101/health",
    "IREN_VELUM_HEALTH_URL": "http://127.0.0.1:8113/health",
    "IREN_PREOPEN_HEALTH_URL": "http://127.0.0.1:8118/health",
    "IREN_GRAEN_HEALTH_URL": "http://127.0.0.1:8110/health",
    "IREN_RESEARCH_AGENT_HEALTH_URL": "http://127.0.0.1:8102/v1/research/health",
    "IREN_EXECUTOR_HEALTH_URL": "http://127.0.0.1:8117/health",
    "IREN_NOSTRA_HEALTH_URL": "http://127.0.0.1:8102/v1/nostra/health",
    "RHEN_CORE_DB_PATH": "/data/rhen-core.db",
    "FOUNDATION_OUTBOX_PATH": "/data/foundation-outbox.jsonl",
    "FOUNDATION_SHADOW_ENABLED": "false",
    "RHEN_CANONICAL_SCHEDULER_ENABLED": "false",
}

DISABLED_EXECUTION = {
    "BOT_ARMED": "false",
    "EXECUTION_ENABLED": "false",
    "LIVE_TRADING": "false",
    "I_ACKNOWLEDGE_LIVE_TRADING": "false",
    "SCAN_ONLY": "true",
}


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _enabled(spec: ProcessSpec) -> bool:
    if not spec.enabled_env:
        return True
    return _truthy(os.getenv(spec.enabled_env), default=False)


def _child_env(spec: ProcessSpec) -> dict[str, str]:
    env = dict(os.environ)
    env.update(LOOPBACK)
    token = (
        env.get("TRADING_INGEST_TOKEN")
        or env.get("FOUNDATION_INGEST_TOKEN")
        or ""
    ).strip()
    if token:
        env["RHEN_CORE_TOKEN"] = token
        env["GRAEN_GATEWAY_TOKEN"] = token
        env["RHEN_RESEARCH_GATEWAY_TOKEN"] = token
        env["NOSTRA_GATEWAY_TOKEN"] = token
        env["FOUNDATION_INGEST_TOKEN"] = token
    research_admin = env.get("RHEN_RESEARCH_ADMIN_TOKEN", "").strip()
    if research_admin:
        env["RHEN_REVIEW_TOKEN"] = research_admin

    if spec.name != "execution":
        env.update(DISABLED_EXECUTION)
    if spec.name in PURE_CORE:
        # Pure control/research modules must not inherit broker or execution
        # administration credentials. Empty means absent to downstream guards;
        # placeholder strings such as "DISABLED" are still truthy secrets.
        env["ALPACA_API_KEY"] = ""
        env["ALPACA_API_SECRET"] = ""
        env["ADMIN_TOKEN"] = ""

    env["PORT"] = str(spec.port)
    env["RHEN_UNIFIED_RUNTIME"] = "true"
    env["RHEN_UNIFIED_ROLE"] = spec.name
    if spec.name == "observer":
        # Observer gets broker read credentials but never execution/control tokens.
        for key in (
            "ADMIN_TOKEN", "RHEN_CORE_TOKEN", "TRADING_INGEST_TOKEN",
            "FOUNDATION_INGEST_TOKEN", "GRAEN_GATEWAY_TOKEN",
            "RHEN_REVIEW_TOKEN", "RHEN_RESEARCH_ADMIN_TOKEN",
            "IREN_EXECUTOR_TOKEN",
        ):
            env[key] = ""
    if spec.name == "velum":
        env["VELUM_AUTORUN"] = "false"
    return env


def _launch(spec: ProcessSpec) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        spec.app,
        "--host",
        "127.0.0.1" if spec.name != "router" else "0.0.0.0",
        "--port",
        str(spec.port),
        "--no-access-log",
    ]
    print(
        {
            "event": "rhen_process_start",
            "name": spec.name,
            "port": spec.port,
            "critical": spec.critical,
        },
        flush=True,
    )
    return subprocess.Popen(command, env=_child_env(spec))


def _wait_tcp_ready(spec: ProcessSpec, timeout_seconds: float | None = None) -> None:
    # Core performs bounded durable maintenance before opening its listener.
    # Hosted volume latency can exceed the ordinary child startup window.
    if timeout_seconds is None:
        timeout_seconds = 120.0 if spec.name == "core" else 15.0
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", spec.port), timeout=0.25):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(
        f"rhen_process_not_ready:{spec.name}:{spec.port}:"
        f"{type(last_error).__name__ if last_error else 'timeout'}"
    )


def _terminate(children: Mapping[str, subprocess.Popen[bytes]]) -> None:
    for process in children.values():
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 10
    for process in children.values():
        remaining = max(0.0, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> None:
    children: dict[str, subprocess.Popen[bytes]] = {}
    specs = {spec.name: spec for spec in PROCESSES if _enabled(spec)}
    stopping = False

    def stop_handler(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    # Start persistence first, then execution/modules, then the public router.
    ordered = sorted(
        specs.values(),
        key=lambda item: (
            0 if item.name == "core" else
            2 if item.name == "router" else
            1
        ),
    )
    for spec in ordered:
        children[spec.name] = _launch(spec)
        # The unified runtime is dependency ordered. Waiting for each local
        # listener prevents Core-dependent services from failing their first
        # readiness/gateway call during rolling deployment.
        _wait_tcp_ready(spec)

    try:
        while not stopping:
            for name, process in list(children.items()):
                code = process.poll()
                if code is None:
                    continue
                spec = specs[name]
                print(
                    {
                        "event": "rhen_process_exit",
                        "name": name,
                        "exit_code": code,
                        "critical": spec.critical,
                    },
                    flush=True,
                )
                if spec.critical:
                    raise RuntimeError(
                        f"critical_rhen_process_exited:{name}:{code}"
                    )
                time.sleep(2)
                children[name] = _launch(spec)
            time.sleep(1)
    finally:
        _terminate(children)


if __name__ == "__main__":
    main()
