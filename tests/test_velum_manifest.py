from datetime import datetime, timezone

from app.velum_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_run_manifest,
    dataset_fingerprint,
    evidence_fingerprint,
)


def bars():
    return {
        "SPY": [
            {
                "t": "2026-09-29T13:30:00+00:00",
                "o": 100,
                "h": 101,
                "l": 99,
                "c": 100.5,
                "v": 1000,
            }
        ],
        "QQQ": [
            {
                "t": "2026-09-29T13:30:00+00:00",
                "o": 50,
                "h": 51,
                "l": 49,
                "c": 50.25,
                "v": 2000,
            }
        ],
    }


def evidence():
    return {
        "system": "VELUM",
        "methodology_version": "velum-replay-v2",
        "asset_class": "equity",
        "baseline": {
            "summary": {"trades": 1, "return_pct": 0.001},
            "assumptions": {
                "spread_bps": "5",
                "slippage_bps_per_side": "2",
                "broker_orders_possible": False,
            },
            "strategy": {
                "name": "rolling_momentum_vwap",
                "fast_window": 3,
                "slow_window": 8,
            },
        },
        "stress": {
            "summary": {"trades": 1, "return_pct": 0.0005},
            "assumptions": {
                "spread_bps": "10",
                "slippage_bps_per_side": "4",
            },
        },
        "bootstrap": {"paths": 500, "positive_path_fraction": 0.6},
    }


def manifest_kwargs(evidence_hash: str):
    payload = evidence()
    return {
        "asset_class": "equity",
        "mode": "REPLAY",
        "methodology_version": "velum-replay-v2",
        "start": datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
        "dataset_hash": dataset_fingerprint(bars()),
        "coverage": {"SPY": 1, "QQQ": 1},
        "strategy_version_id": "LIVE-2026-09-25-003",
        "strategy": payload["baseline"]["strategy"],
        "execution_assumptions": payload["baseline"]["assumptions"],
        "random_seed": 20260929,
        "runtime_git_commit": "abc123",
        "evidence_hash": evidence_hash,
    }


def test_dataset_fingerprint_is_order_independent_and_data_sensitive():
    source = bars()
    reordered = {"QQQ": list(source["QQQ"]), "SPY": list(source["SPY"])}
    assert dataset_fingerprint(source) == dataset_fingerprint(reordered)

    lower_case = {"spy": list(source["SPY"]), "qqq": list(source["QQQ"])}
    assert dataset_fingerprint(source) == dataset_fingerprint(lower_case)

    changed = bars()
    changed["SPY"][0]["c"] = 100.6
    assert dataset_fingerprint(source) != dataset_fingerprint(changed)


def test_evidence_fingerprint_covers_stress_and_diagnostics():
    payload = evidence()
    assert evidence_fingerprint(payload) == evidence_fingerprint(payload)

    changed = evidence()
    changed["stress"]["summary"]["return_pct"] = -0.001
    assert evidence_fingerprint(payload) != evidence_fingerprint(changed)

    changed = evidence()
    changed["bootstrap"]["paths"] = 1000
    assert evidence_fingerprint(payload) != evidence_fingerprint(changed)


def test_run_manifest_is_stable_and_reproducible():
    payload_hash = evidence_fingerprint(evidence())
    first = build_run_manifest(**manifest_kwargs(payload_hash))
    second = build_run_manifest(**manifest_kwargs(payload_hash))

    assert first == second
    assert first["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert first["velum_run_id"].startswith("VELUM-EQUITY-REPLAY-")
    assert first["experiment_fingerprint"].startswith("sha256:")
    assert first["evidence_fingerprint"] == payload_hash
    assert first["run_fingerprint"].startswith("sha256:")
    assert first["safety"]["broker_orders_possible"] is False


def test_experiment_and_run_fingerprints_have_distinct_meanings():
    base_evidence = evidence()
    changed_evidence = evidence()
    changed_evidence["stress"]["summary"]["return_pct"] = -0.001

    base = build_run_manifest(
        **manifest_kwargs(evidence_fingerprint(base_evidence))
    )
    changed = build_run_manifest(
        **manifest_kwargs(evidence_fingerprint(changed_evidence))
    )

    assert base["experiment_fingerprint"] == changed["experiment_fingerprint"]
    assert base["evidence_fingerprint"] != changed["evidence_fingerprint"]
    assert base["run_fingerprint"] != changed["run_fingerprint"]
    assert base["velum_run_id"] != changed["velum_run_id"]


def test_experiment_fingerprint_changes_when_inputs_change():
    payload_hash = evidence_fingerprint(evidence())
    base_kwargs = manifest_kwargs(payload_hash)
    base = build_run_manifest(**base_kwargs)

    changed_execution = dict(base_kwargs["execution_assumptions"])
    changed_execution["spread_bps"] = "10"
    changed_kwargs = dict(base_kwargs)
    changed_kwargs["execution_assumptions"] = changed_execution
    changed = build_run_manifest(**changed_kwargs)

    assert base["experiment_fingerprint"] != changed["experiment_fingerprint"]
    assert base["run_fingerprint"] != changed["run_fingerprint"]
    assert base["velum_run_id"] != changed["velum_run_id"]
