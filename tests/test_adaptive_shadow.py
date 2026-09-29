import pytest

from app.research_agent.adaptive_shadow import (
    aggregate_shadow_validation,
    build_adaptive_shadow_plan,
    evaluate_shadow_session,
    prepare_shadow_rows,
)


BASELINE = {
    "min_momentum_pct": "0.0020",
    "min_vwap_edge_pct": "0.0010",
    "min_confirmations": "2",
    "max_vwap_extension_pct": "0.0080",
}


def proposal(parameter, old, proposed):
    return {
        "proposal_id": f"AP-{parameter}",
        "parameter": parameter,
        "old_value": old,
        "proposed_value": proposed,
        "authorization_required": True,
        "automatic_application_authorized": False,
        "execution_authority": False,
    }


def candidate(session, idx, *, momentum, forward):
    return {
        "candidate_id": idx,
        "candidate_key": f"{session}:{idx}",
        "strategy_version_id": "LIVE-TEST",
        "session": session,
        "symbol": f"T{idx}",
        "features": {
            "current_close": "100.20",
            "session_vwap": "100.00",
            "momentum_pct": str(momentum),
            "vwap_edge_pct": "0.0020",
            "confirmation_passes": 2,
            "checks": {
                "fast_above_slow": True,
                "rising": True,
                "momentum_ok": momentum >= 0.0020,
                "vwap_ok": True,
                "vwap_extension_ok": True,
                "confirmations_ok": True,
                "regime_ok": True,
            },
        },
        "checks": {
            "strategy": {
                "fast_above_slow": True,
                "rising": True,
                "momentum_ok": momentum >= 0.0020,
                "vwap_ok": True,
                "vwap_extension_ok": True,
                "confirmations_ok": True,
                "regime_ok": True,
            }
        },
        "outcomes": [
            {
                "horizon_minutes": 15,
                "status": "complete",
                "forward_return": str(forward),
            }
        ],
    }


def plan(source_session="2026-10-01"):
    return build_adaptive_shadow_plan(
        source_session=source_session,
        source_strategy_version="LIVE-TEST",
        baseline_configuration=BASELINE,
        proposals={
            "min_momentum_pct": proposal(
                "min_momentum_pct",
                "0.0020",
                "0.0018",
            )
        },
    )


def test_shadow_plan_is_bounded_and_has_no_execution_authority():
    result = plan()
    assert result["adaptive_configuration"]["min_momentum_pct"] == "0.0018"
    assert result["baseline_configuration"]["min_momentum_pct"] == "0.0020"
    assert result["execution_authority"] is False
    assert result["live_configuration_changed"] is False
    assert result["effective_from_next_session_only"] is True


def test_shadow_plan_rejects_nonadaptive_parameter():
    result = build_adaptive_shadow_plan(
        source_session="2026-10-01",
        source_strategy_version="LIVE-TEST",
        baseline_configuration=BASELINE,
        proposals={
            "stop_pct": proposal("stop_pct", "0.002", "0.003"),
        },
    )
    assert "stop_pct" in result["rejected_proposals"]
    assert "stop_pct" not in result["adaptive_configuration"]


def test_same_session_adaptive_evaluation_is_forbidden():
    with pytest.raises(ValueError):
        evaluate_shadow_session(
            rows=[],
            plan=plan(),
            evaluation_session="2026-10-01",
        )


def test_adaptive_shadow_values_added_candidates_without_real_trade_claim():
    session = "2026-10-02"
    candidates = []
    for idx in range(20):
        if idx < 10:
            candidates.append(
                candidate(
                    session,
                    idx,
                    momentum=0.0019,
                    forward=0.0040,
                )
            )
        else:
            candidates.append(
                candidate(
                    session,
                    idx,
                    momentum=0.0023,
                    forward=0.0030,
                )
            )
    result = evaluate_shadow_session(
        rows=prepare_shadow_rows(candidates),
        plan=plan(),
        evaluation_session=session,
        round_trip_cost="0.0022",
    )
    assert result["adaptive_selected_candidates"] > result["fixed_selected_candidates"]
    assert float(result["adaptive_minus_fixed_utility"]) > 0
    assert result["screening_only"] is True
    assert result["portfolio_backtest"] is False
    assert result["execution_authority"] is False


def test_cross_session_shadow_validation_requires_mature_repeatability():
    results = []
    for day in range(10):
        source = f"2026-10-{day+1:02d}"
        evaluation = f"2026-10-{day+2:02d}"
        current_plan = plan(source_session=source)
        rows = []
        for idx in range(20):
            if idx < 10:
                rows.append(
                    candidate(
                        evaluation,
                        idx,
                        momentum=0.0019,
                        forward=0.0040,
                    )
                )
            else:
                rows.append(
                    candidate(
                        evaluation,
                        idx,
                        momentum=0.0023,
                        forward=0.0030,
                    )
                )
        results.append(
            evaluate_shadow_session(
                rows=prepare_shadow_rows(rows),
                plan=current_plan,
                evaluation_session=evaluation,
                round_trip_cost="0.0022",
            )
        )

    validation = aggregate_shadow_validation(results)
    assert validation["validation_passed"] is True
    assert validation["independent_sessions"] == 10
    assert validation["complete_candidates"] == 200
    assert validation["differential_decisions"] == 100
    assert float(validation["mean_adaptive_minus_fixed_utility"]) > 0
    assert validation["no_lookahead_enforced"] is True
    assert validation["promotion_authorized"] is False


def test_shadow_validation_rejects_mixed_baselines():
    first = {
        "source_session": "2026-10-01",
        "evaluation_session": "2026-10-02",
        "baseline_fingerprint": "A",
        "complete_candidates": 100,
        "candidate_scope": 100,
        "differential_decisions": 50,
        "forward_coverage": "1",
        "adaptive_minus_fixed_utility": "0.001",
    }
    second = dict(first)
    second["source_session"] = "2026-10-02"
    second["evaluation_session"] = "2026-10-03"
    second["baseline_fingerprint"] = "B"
    result = aggregate_shadow_validation([first, second])
    assert result["rejected_incompatible_results"] == 1
    assert result["validation_passed"] is False
