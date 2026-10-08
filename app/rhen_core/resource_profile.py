from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "rhen_resource_profile.v1"


def profile_interval(raw: str | None) -> int:
    """Opt-in only: clamp nonzero sample periods to 60..3600 seconds."""
    try:
        seconds = int((raw or "0").strip())
    except (TypeError, ValueError):
        return 0
    if seconds <= 0:
        return 0
    return max(60, min(3600, seconds))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def _kib_field(text: str | None, key: str) -> int | None:
    if text is None:
        return None
    for line in text.splitlines():
        field, _, value = line.partition(":")
        if field != key:
            continue
        words = value.strip().split()
        if len(words) == 2 and words[1] == "kB":
            try:
                return int(words[0])
            except ValueError:
                pass
        return None
    return None


def _integer(path: Path) -> int | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _stat(pid: int, proc_root: Path) -> dict[str, int | float] | None:
    raw = _read(proc_root / str(pid) / "stat")
    if not raw:
        return None
    # Field 2 (comm) may contain spaces or parentheses. The final closing
    # parenthesis ends that field. Remaining fields begin at field 3.
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 1 :].split()
    if len(fields) <= 19:
        return None
    try:
        ticks = os.sysconf("SC_CLK_TCK")
        return {
            "start_ticks": int(fields[19]),  # Linux stat field 22
            "cpu_seconds_total": round(
                (int(fields[11]) + int(fields[12])) / ticks, 6
            ),
        }
    except (OSError, ValueError, ZeroDivisionError):
        return None


def _mib_from_kib(value: int | None) -> float | None:
    return round(value / 1024, 3) if value is not None else None


def _mib_from_bytes(value: int | None) -> float | None:
    return round(value / (1024 * 1024), 3) if value is not None else None


def sample(
    children: Mapping[str, Any],
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, Any]:
    """Read only OS resource counters, no environment or broker data.

    Per-process RSS counts shared pages repeatedly. PSS apportions shared
    memory when the kernel permits it. Cgroup memory.current includes shared
    pages, cache and memory not attributed to these children.
    """
    roles: list[dict[str, Any]] = []
    for role, child in sorted(children.items()):
        pid = getattr(child, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            continue
        proc = proc_root / str(pid)
        status = _read(proc / "status")
        if status is None:
            continue
        stat = _stat(pid, proc_root)
        rollup = _read(proc / "smaps_rollup")
        roles.append(
            {
                "role": role,
                "pid": pid,
                "start_ticks": stat["start_ticks"] if stat else None,
                "rss_mib": _mib_from_kib(_kib_field(status, "VmRSS")),
                "peak_rss_mib": _mib_from_kib(_kib_field(status, "VmHWM")),
                "pss_mib": _mib_from_kib(_kib_field(rollup, "Pss")),
                "anonymous_mib": _mib_from_kib(_kib_field(status, "RssAnon")),
                "file_backed_mib": _mib_from_kib(_kib_field(status, "RssFile")),
                "cpu_seconds_total": stat["cpu_seconds_total"] if stat else None,
            }
        )

    def sum_available(key: str) -> float | None:
        values = [float(r[key]) for r in roles if r.get(key) is not None]
        return round(sum(values), 3) if values else None

    cpu_usec: int | None = None
    for line in (_read(cgroup_root / "cpu.stat") or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "usage_usec":
            try:
                cpu_usec = int(parts[1])
            except ValueError:
                pass
            break

    return {
        "event": "rhen_resource_profile",
        "schema_version": SCHEMA_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "process_count": len(roles),
        "roles": roles,
        "sum_rss_mib_not_additive": sum_available("rss_mib"),
        "sum_pss_mib_if_accessible": sum_available("pss_mib"),
        "pss_covered_process_count": sum(
            row["pss_mib"] is not None for row in roles
        ),
        "pss_complete": bool(roles) and all(
            row["pss_mib"] is not None for row in roles
        ),
        "cgroup_memory_mib": _mib_from_bytes(
            _integer(cgroup_root / "memory.current")
        ),
        "cgroup_cpu_usage_usec_total": cpu_usec,
        "broker_data_included": False,
    }


def with_cpu_rates(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Add measured interval vCPU use; skip restarted/reused process IDs."""
    if previous is None or elapsed_seconds <= 0:
        return current

    prior = {
        (row.get("pid"), row.get("start_ticks")): row.get("cpu_seconds_total")
        for row in previous.get("roles", [])
        if row.get("pid") is not None and row.get("start_ticks") is not None
    }
    for row in current.get("roles", []):
        key = (row.get("pid"), row.get("start_ticks"))
        earlier = prior.get(key)
        later = row.get("cpu_seconds_total")
        if earlier is not None and later is not None and later >= earlier:
            row["avg_cpu_cores_in_interval"] = round(
                (later - earlier) / elapsed_seconds, 4
            )

    old = previous.get("cgroup_cpu_usage_usec_total")
    new = current.get("cgroup_cpu_usage_usec_total")
    if old is not None and new is not None and new >= old:
        current["avg_cgroup_cpu_cores_in_interval"] = round(
            (new - old) / 1_000_000 / elapsed_seconds, 4
        )
    return current
