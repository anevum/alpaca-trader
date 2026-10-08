from __future__ import annotations

import json

from scripts.resource_profile_report import summarize


def _event(
    *,
    memory: float = 400,
    cgroup_cpu: float | None = 0.5,
    pss: float = 150,
    core_cpu: float = 0.2,
) -> dict:
    value = {
        "event": "rhen_resource_profile",
        "schema_version": "rhen_resource_profile.v1",
        "broker_data_included": False,
        "observed_at": "2026-10-08T14:00:00+00:00",
        "cgroup_memory_mib": memory,
        "avg_cgroup_cpu_cores_in_interval": cgroup_cpu,
        "pss_complete": True,
        "roles": [
            {
                "role": "core",
                "pss_mib": pss,
                "rss_mib": pss + 30,
                "avg_cpu_cores_in_interval": core_cpu,
            }
        ],
    }
    return value


def test_profile_report_handles_raw_and_railway_wrapped_jsonl():
    first = _event(memory=400, cgroup_cpu=0.2, pss=140)
    second = _event(memory=600, cgroup_cpu=0.4, pss=160)
    third = _event(memory=500, cgroup_cpu=0.3, pss=150)
    lines = [
        json.dumps(first),
        json.dumps({"message": json.dumps(second)}),
        json.dumps(third),
        "not json",
        json.dumps({"event": "broker_order", "payload": {"secret": "do-not-echo"}}),
    ]
    result = summarize(lines)
    assert result["valid_sample_count"] == 3
    assert result["complete_pss_sample_count"] == 3
    assert result["cgroup_memory_mib"]["median"] == 500
    assert result["cgroup_memory_mib"]["mean"] == 500
    assert result["cgroup_memory_mib"]["p95_nearest_rank"] == 600
    assert result["cgroup_avg_cpu_cores"]["mean"] == 0.3
    assert result["roles"]["core"]["pss_mib"]["median"] == 150
    assert result["roles"]["core"]["avg_cpu_cores"]["p95_nearest_rank"] == 0.2
    assert "do-not-echo" not in json.dumps(result)


def test_profile_report_filters_invalid_or_untrusted_events():
    bad = _event()
    bad["broker_data_included"] = True
    wrong_schema = _event()
    wrong_schema["schema_version"] = "arbitrary"
    invalid = _event()
    invalid["cgroup_memory_mib"] = -2
    invalid["avg_cgroup_cpu_cores_in_interval"] = float("inf")
    invalid["roles"][0]["role"] = "x" * 60
    valid = _event(memory=100)
    valid["pss_complete"] = False
    lines = [
        json.dumps(bad),
        json.dumps(wrong_schema),
        json.dumps(invalid),
        json.dumps(valid),
        json.dumps({"message": "not json", "secret": "never-return"}),
    ]
    result = summarize(lines)
    assert result["valid_sample_count"] == 2
    # Missing/invalid cgroup counters do not imply unavailable PSS.
    # The other valid record advertises a partial PSS sample.
    assert result["complete_pss_sample_count"] == 1
    assert result["cgroup_memory_mib"]["sample_count"] == 1
    assert result["cgroup_memory_mib"]["median"] == 100
    assert set(result["roles"]) == {"core"}
    assert "never-return" not in json.dumps(result)


def test_profile_report_empty_input_has_no_fabricated_metrics():
    result = summarize(["", "broken", '{"message":"nope"}'])
    assert result["valid_sample_count"] == 0
    assert result["roles"] == {}
    assert result["cgroup_memory_mib"]["median"] is None
    assert result["cgroup_avg_cpu_cores"]["p95_nearest_rank"] is None
