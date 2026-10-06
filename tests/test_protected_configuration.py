from decimal import Decimal

from app.protected_configuration import (
    build_protected_configuration,
    diff_snapshots,
)


def snapshot(*, max_positions=2, symbols=("SPY", "QQQ"), commit="a" * 40):
    return build_protected_configuration(
        comparison={
            "universe": {"symbols": symbols},
            "entry": {"min_momentum_pct": Decimal("0.0010")},
        },
        protected={
            "portfolio": {"max_concurrent_positions": max_positions},
            "asset_authority": {
                "long_us_equities_etfs": True,
                "crypto": False,
                "options": False,
                "short_equities": False,
                "leverage_expansion": False,
            },
        },
        generated_at="2026-10-06T19:00:00+00:00",
        runtime_commit=commit,
        deployment_id="deployment-a",
        strategy_version_id="LIVE-2026-09-25-003",
    )


def test_provenance_does_not_change_fingerprint():
    first = snapshot(commit="a" * 40)
    second = build_protected_configuration(
        comparison=first["comparison"],
        protected=first["protected"],
        generated_at="2026-10-07T19:00:00+00:00",
        runtime_commit="b" * 40,
        deployment_id="deployment-b",
        strategy_version_id="LIVE-2026-09-25-003",
    )
    assert first["fingerprint"] == second["fingerprint"]


def test_decimal_and_setlike_symbol_order_are_deterministic():
    first = snapshot(symbols=("SPY", "QQQ"))
    second = build_protected_configuration(
        comparison={
            "universe": {"symbols": ("QQQ", "SPY")},
            "entry": {"min_momentum_pct": Decimal("0.001")},
        },
        protected=first["protected"],
        generated_at="2026-10-06T19:00:00+00:00",
        runtime_commit="a" * 40,
        deployment_id="deployment-a",
        strategy_version_id="LIVE-2026-09-25-003",
    )
    assert first["fingerprint"] == second["fingerprint"]


def test_field_level_diff_is_stable_and_classified():
    before = snapshot(max_positions=2)
    after = snapshot(max_positions=3)
    drift = diff_snapshots(before, after)

    assert drift["comparison_completeness"] == "full"
    assert drift["changed_count"] == 1
    assert drift["changes"] == [
        {
            "path": "protected.portfolio.max_concurrent_positions",
            "operation": "replace",
            "classification": "portfolio_risk",
            "severity": "protected",
            "before": 2,
            "after": 3,
        }
    ]
