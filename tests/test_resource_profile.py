from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rhen_core.resource_profile import (
    SCHEMA_VERSION,
    profile_interval,
    sample,
    with_cpu_rates,
)


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (None, 0),
        ("", 0),
        ("0", 0),
        ("-30", 0),
        ("nonsense", 0),
        ("1", 60),
        ("30", 60),
        ("60", 60),
        ("300", 300),
        ("99999", 3600),
    ],
)
def test_resource_profile_opt_in_bounds(input_value, expected):
    assert profile_interval(input_value) == expected


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stat(pid: int, *, start: int = 200, user: int = 100, system: int = 50) -> str:
    # Linux /proc/PID/stat: field 2 is parenthesized and may contain spaces.
    # The first list entry below is field 3.
    fields = ["S"] + ["0"] * 20
    fields[11] = str(user)
    fields[12] = str(system)
    fields[19] = str(start)
    return f"{pid} (uvicorn (Core) service) " + " ".join(fields) + "\n"


def test_kernel_only_profile_cgroup_memory_pss_cpu_and_role_order(
    tmp_path, monkeypatch
):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _write(
        proc / "100" / "status",
        "Name:\tpython\nVmRSS:\t204800 kB\nVmHWM:\t256000 kB\n"
        "RssAnon:\t143360 kB\nRssFile:\t61440 kB\n",
    )
    _write(proc / "100" / "smaps_rollup", "Pss:\t153600 kB\n")
    _write(proc / "100" / "stat", _stat(100))
    _write(
        proc / "200" / "status",
        "VmRSS:\t102400 kB\nVmHWM:\t110592 kB\n",
    )
    _write(proc / "200" / "stat", _stat(200, start=201, user=400, system=0))
    _write(cg / "memory.current", str(400 * 1024 * 1024))
    _write(cg / "cpu.stat", "usage_usec 50000000\n")
    monkeypatch.setattr(os, "sysconf", lambda _: 100)

    # Sorted output is stable despite insertion order.
    result = sample(
        {
            "router": SimpleNamespace(pid=200),
            "core": SimpleNamespace(pid=100),
            "dead": SimpleNamespace(pid=999),
        },
        proc_root=proc,
        cgroup_root=cg,
    )
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["event"] == "rhen_resource_profile"
    assert result["broker_data_included"] is False
    assert result["process_count"] == 2
    assert [row["role"] for row in result["roles"]] == ["core", "router"]
    assert result["roles"][0]["rss_mib"] == 200
    assert result["roles"][0]["pss_mib"] == 150
    assert result["roles"][0]["peak_rss_mib"] == 250
    assert result["roles"][0]["anonymous_mib"] == 140
    assert result["roles"][0]["file_backed_mib"] == 60
    assert result["roles"][0]["cpu_seconds_total"] == 1.5
    assert result["roles"][0]["start_ticks"] == 200
    assert result["roles"][1]["pss_mib"] is None
    assert result["sum_rss_mib_not_additive"] == 300
    assert result["sum_pss_mib_if_accessible"] == 150
    assert result["cgroup_memory_mib"] == 400
    assert result["cgroup_cpu_usage_usec_total"] == 50000000
    encoded = json.dumps(result)
    assert "uvicorn (Core)" not in encoded
    assert "ALPACA_API_KEY" not in encoded


def test_missing_kernel_counters_fail_open(tmp_path):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _write(proc / "100" / "status", "VmRSS:\tbroken kB\n")
    _write(proc / "100" / "stat", "invalid")
    _write(cg / "cpu.stat", "usage_usec invalid\n")
    result = sample(
        {"core": SimpleNamespace(pid=100), "missing": SimpleNamespace(pid=101)},
        proc_root=proc,
        cgroup_root=cg,
    )
    assert result["process_count"] == 1
    assert result["roles"][0]["rss_mib"] is None
    assert result["roles"][0]["cpu_seconds_total"] is None
    assert result["sum_rss_mib_not_additive"] is None
    assert result["sum_pss_mib_if_accessible"] is None
    assert result["cgroup_memory_mib"] is None
    assert result["cgroup_cpu_usage_usec_total"] is None


def test_resource_rates_ignore_restarted_pid_and_use_monotonic_elapsed():
    previous = {
        "roles": [
            {"role": "core", "pid": 100, "start_ticks": 11, "cpu_seconds_total": 5},
            {"role": "execution", "pid": 101, "start_ticks": 22, "cpu_seconds_total": 4},
        ],
        "cgroup_cpu_usage_usec_total": 2_000_000,
    }
    current = {
        "roles": [
            {"role": "core", "pid": 100, "start_ticks": 11, "cpu_seconds_total": 8},
            # PID reused after a restart: do not invent negative or positive CPU.
            {"role": "execution", "pid": 101, "start_ticks": 23, "cpu_seconds_total": 1},
        ],
        "cgroup_cpu_usage_usec_total": 7_000_000,
    }
    measured = with_cpu_rates(current, previous, 10.0)
    assert measured["roles"][0]["avg_cpu_cores_in_interval"] == 0.3
    assert "avg_cpu_cores_in_interval" not in measured["roles"][1]
    assert measured["avg_cgroup_cpu_cores_in_interval"] == 0.5


def test_first_profile_has_no_cpu_rates():
    current = {
        "roles": [{"pid": 100, "start_ticks": 100, "cpu_seconds_total": 8}],
        "cgroup_cpu_usage_usec_total": 7_000_000,
    }
    assert with_cpu_rates(current, None, 60) is current
    assert "avg_cgroup_cpu_cores_in_interval" not in current
    assert with_cpu_rates(current, current, 0) is current


def test_resource_profiler_not_enabled_in_live_supervisor_by_default(monkeypatch):
    from app.rhen_core.resource_profile import profile_interval

    monkeypatch.delenv("RHEN_RESOURCE_PROFILE_INTERVAL_SECONDS", raising=False)
    assert profile_interval(os.getenv("RHEN_RESOURCE_PROFILE_INTERVAL_SECONDS")) == 0
