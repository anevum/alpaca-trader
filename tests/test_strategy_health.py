from app.research_agent.strategy_health import (
    ADAPT,
    DEFENSIVE,
    NORMAL,
    RESEARCH,
    compute_strategy_health,
)


def base_daily():
    return {
        "report_key": "2026-09-29:rhen-daily-v1.2:test",
        "session": "2026-09-29",
        "metrics": {
            "trade_count": 11,
            "expectancy": "0.01",
            "profit_factor": "1.1",
            "win_rate": "0.55",
        },
        "runtime": {
            "reconciliation_safe": True,
            "last_error": None,
            "persistence_error": None,
        },
        "candidate_forward_evidence": {
            "status": {
                "complete_rows": 300,
                "incomplete_rows": 25,
                "error_rows": 0,
            }
        },
        "ads002_v2": {
            "models": [
                {
                    "model_key": "ads002-geo-v2",
                    "session_spearman_15m": "0.10",
                    "confidence": {"score": "0.20", "minimums_met": False},
                }
            ]
        },
    }


def test_small_sample_remains_normal_collecting_not_forced_change():
    result = compute_strategy_health(daily_report=base_daily())
    assert result["control_state"] == NORMAL
    assert result["dimensions"]["edge"]["status"] == "COLLECTING"
    assert result["read_only"] is True
    assert result["execution_authority"] is False
    assert result["live_configuration_changed"] is False


def test_runtime_integrity_failure_is_defensive():
    daily = base_daily()
    daily["runtime"]["reconciliation_safe"] = False
    result = compute_strategy_health(daily_report=daily)
    assert result["control_state"] == DEFENSIVE
    assert result["dimensions"]["evidence_integrity"]["status"] == "BLOCKED"


def test_mature_negative_edge_escalates_to_research():
    weekly = {
        "period_end": "2026-10-09",
        "metrics": {
            "completed_trades": 48,
            "expectancy": "-0.15",
            "profit_factor": "0.70",
            "win_rate": "0.35",
        },
    }
    result = compute_strategy_health(
        daily_report=base_daily(),
        weekly_report=weekly,
    )
    assert result["control_state"] == RESEARCH
    assert result["dimensions"]["edge"]["status"] == "DEGRADED"


def test_regime_uncertainty_can_request_bounded_adapt_state():
    nostra = {
        "regime": "TREND_DECAY",
        "confidence": "0.50",
        "unknown_probability": "0.10",
        "market_familiarity": "0.55",
    }
    result = compute_strategy_health(
        daily_report=base_daily(),
        nostra_state=nostra,
    )
    assert result["control_state"] == ADAPT
    assert result["dimensions"]["regime"]["status"] == "WATCH"
    assert result["dimensions"]["distribution"]["status"] == "WATCH"


def test_low_market_familiarity_escalates_to_research_not_auto_adapt():
    nostra = {
        "regime": "UNKNOWN",
        "confidence": "0.30",
        "unknown_probability": "0.60",
        "market_familiarity": "0.20",
    }
    result = compute_strategy_health(
        daily_report=base_daily(),
        nostra_state=nostra,
    )
    assert result["control_state"] == RESEARCH
    assert result["dimensions"]["distribution"]["status"] == "DEGRADED"


def test_persistent_parameter_boundary_pressure_escalates_to_research():
    pressure = {
        "parameters": [
            {"parameter": "min_momentum_pct", "boundary_fraction": "0.75"},
            {"parameter": "min_vwap_edge_pct", "boundary_fraction": "0.10"},
        ]
    }
    result = compute_strategy_health(
        daily_report=base_daily(),
        parameter_pressure=pressure,
    )
    assert result["control_state"] == RESEARCH
    assert result["dimensions"]["parameter_pressure"]["status"] == "DEGRADED"


def test_mature_challenger_pressure_is_research_only():
    daily = base_daily()
    daily["ads002_v2"]["models"] = [
        {
            "model_key": "ads002-geo-v2",
            "session_spearman_15m": "0.22",
            "confidence": {"score": "0.82", "minimums_met": True},
        }
    ]
    result = compute_strategy_health(daily_report=daily)
    assert result["control_state"] == RESEARCH
    assert result["dimensions"]["challenger_pressure"]["status"] == "WATCH"
    assert result["promotion_authorized"] is False
