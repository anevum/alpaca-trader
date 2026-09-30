from datetime import datetime, timezone
from decimal import Decimal

from app.velum_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_run_manifest,
    dataset_fingerprint,
    replay_result_fingerprint,
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


def replay_result():
    return {
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
        "sessions": 1,
        "trades": [
            {
                "symbol": "SPY",
                "entry_at": "2026-09-29T13:31:00+00:00",
                "exit_at": "2026-09-29T13:45:00+00:00",
                "net_pnl": "0.10",
            }
        ],
        "equity_curve": [
            {"at": "2026-09-29T13:31:00+00:00", "equity": "100"},
            {"at": "2026-09-29T13:45:00+00:00", "equity": "100.10"},
        ],
    }


def test_dataset_fingerprint_is_order_independent_and_data_sensitive():
    source = bars()
    reordered = {"QQQ": list(source["QQQ"]), "SPY": list(source["SPY"])}
    assert dataset_fingerprint(source) == dataset_fingerprint(reordered)

    changed = bars()
    changed["SPY"][0]["c"] = 100.6
    assert dataset_fingerprint(source) != dataset_fingerprint(changed)


def test_replay_result_fingerprint_is_deterministic():
    result = replay_result()
    assert replay_result_fingerprint(result) == replay_result_fingerprint(result)

    changed = replay_result()
    changed["trades"][0]["net_pnl"] = "0.11"
    assert replay_result_fingerprint(result) != replay_result_fingerprint(changed)


def test_run_manifest_is_stable_and_reproducible():
    source = bars()
    result = replay_result()
    kwargs = {
        "asset_class": "equity",
        "mode": "REPLAY",
        "methodology_version": "velum-replay-v2",
        "start": datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
        "dataset_hash": dataset_fingerprint(source),
        "coverage": {"SPY": 1, "QQQ": 1},
        "strategy_version_id": "LIVE-2026-09-25-003",
        "strategy": result["strategy"],
        "execution_assumptions": result["assumptions"],
        "random_seed": 20260929,
        "runtime_git_commit": "abc123",
        "result_hash": replay_result_fingerprint(result),
    }
    first = build_run_manifest(**kwargs)
    second = build_run_manifest(**kwargs)

    assert first == second
    assert first["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert first["velum_run_id"].startswith("VELUM-EQUITY-REPLAY-")
    assert first["experiment_fingerprint"].startswith("sha256:")
    assert first["safety"]["broker_orders_possible"] is False


def test_run_id_changes_when_reproducibility_inputs_change():
    source = bars()
    result = replay_result()
    base = build_run_manifest(
        asset_class="crypto",
        mode="REPLAY",
        methodology_version="velum-replay-v2",
        start=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc),
        dataset_hash=dataset_fingerprint(source),
        coverage={"SPY": 1, "QQQ": 1},
        strategy_version_id="CRYPTO-2026-09-29-001",
        strategy=result["strategy"],
        execution_assumptions=result["assumptions"],
        random_seed=2026092900,
        runtime_git_commit="abc123",
        result_hash=replay_result_fingerprint(result),
    )
    changed_execution = dict(result["assumptions"])
    changed_execution["spread_bps"] = "10"
    changed = build_run_manifest(
        asset_class="crypto",
        mode="REPLAY",
        methodology_version="velum-replay-v2",
        start=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc),
        dataset_hash=dataset_fingerprint(source),
        coverage={"SPY": 1, "QQQ": 1},
        strategy_version_id="CRYPTO-2026-09-29-001",
        strategy=result["strategy"],
        execution_assumptions=changed_execution,
        random_seed=2026092900,
        runtime_git_commit="abc123",
        result_hash=replay_result_fingerprint(result),
    )

    assert base["velum_run_id"] != changed["velum_run_id"]
    assert base["experiment_fingerprint"] != changed["experiment_fingerprint"]
