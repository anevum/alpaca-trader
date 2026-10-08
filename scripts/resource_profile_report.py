from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from app.rhen_core.resource_profile import SCHEMA_VERSION


# Fixed supervisor roles only: report input is exported log data, not a
# trusted source for arbitrary labels or secrets masquerading as process names.
ALLOWED_ROLES = frozenset({
    "core", "execution", "iren", "router", "graen", "velum",
    "research-agent", "nostra", "iren-executor", "preopen",
})


def _number(value: Any) -> float | None:
    # Booleans and nonfinite/negative metrics are invalid measurements.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _nearest_rank(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, math.ceil(len(ordered) * percentile / 100) - 1)
    return round(ordered[position], 3)


def _stats(values: list[float]) -> dict[str, int | float | None]:
    return {
        "sample_count": len(values),
        "mean": round(mean(values), 3) if values else None,
        "median": round(median(values), 3) if values else None,
        "p95_nearest_rank": _nearest_rank(values, 95),
        "max": round(max(values), 3) if values else None,
    }


def _extract(line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(row, dict):
        return None

    # Railway JSON log exports sometimes wrap the actual event in message.
    if row.get("event") != "rhen_resource_profile" and isinstance(
        row.get("message"), str
    ):
        try:
            row = json.loads(row["message"])
        except ValueError:
            return None

    if not isinstance(row, dict):
        return None
    if row.get("event") != "rhen_resource_profile":
        return None
    if row.get("schema_version") != SCHEMA_VERSION:
        return None
    if row.get("broker_data_included") is not False:
        return None
    return row


def summarize(lines: Iterable[str]) -> dict[str, Any]:
    """Report only safe resource totals; never echo arbitrary source log text."""
    records = [record for line in lines if (record := _extract(line))]
    by_role: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"pss_mib": [], "rss_mib": [], "cpu_cores": []}
    )
    cgroup_memory: list[float] = []
    cgroup_cpu: list[float] = []
    complete_pss_samples = 0

    for record in records:
        current_mem = _number(record.get("cgroup_memory_mib"))
        if current_mem is not None:
            cgroup_memory.append(current_mem)
        current_cpu = _number(record.get("avg_cgroup_cpu_cores_in_interval"))
        if current_cpu is not None:
            cgroup_cpu.append(current_cpu)
        if record.get("pss_complete") is True:
            complete_pss_samples += 1

        for role in record.get("roles") or []:
            if not isinstance(role, dict):
                continue
            name = role.get("role")
            if name not in ALLOWED_ROLES:
                continue
            for source, target in (
                ("pss_mib", "pss_mib"),
                ("rss_mib", "rss_mib"),
                ("avg_cpu_cores_in_interval", "cpu_cores"),
            ):
                reading = _number(role.get(source))
                if reading is not None:
                    by_role[name][target].append(reading)

    return {
        "schema_version": "rhen_resource_profile_report.v1",
        "valid_sample_count": len(records),
        "complete_pss_sample_count": complete_pss_samples,
        "cgroup_memory_mib": _stats(cgroup_memory),
        "cgroup_avg_cpu_cores": _stats(cgroup_cpu),
        "roles": {
            role: {
                "pss_mib": _stats(values["pss_mib"]),
                "rss_mib": _stats(values["rss_mib"]),
                "avg_cpu_cores": _stats(values["cpu_cores"]),
            }
            for role, values in sorted(by_role.items())
        },
        "limitations": [
            "PSS may be unavailable or partial; check complete_pss_sample_count.",
            "RSS is not additive because shared pages are counted repeatedly.",
            "Cgroup memory includes caches and runtime not directly attributable to child roles.",
            "This is a sample-based resource summary, not a Railway invoice or live-trading performance report.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize sanitized RHEN resource-profiler JSONL logs."
    )
    parser.add_argument("path", type=Path, help="Local JSONL log export")
    args = parser.parse_args()
    with args.path.open(encoding="utf-8", errors="replace") as stream:
        result = summarize(stream)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
