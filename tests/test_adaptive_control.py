from app.research_agent.adaptive_control import build_adaptive_control_artifact


def daily_report():
    return {
        "report_key": "d-1",
        "session": "2026-09-29",
        "metrics": {"trade_count": 11, "expectancy": "0.01", "profit_factor": "1.1"},
        "runtime": {
            "reconciliation_safe": True,
            "last_error": None,
            "persistence_error": None,
        },
        "candidate_forward_evidence": {
            "status": {"complete_rows": 100, "incomplete_rows": 0, "error_rows": 0}
        },
        "ads002_v2": {"models": []},
    }


def candidate(session="2026-09-29"):
    return {
        "candidate_id": 1,
        "scan_cycle_id": 1,
        "cycle_key": "c1",
        "session": session,
        "observed_at": session + "T10:00:00-04:00",
        "symbol": "TEST",
        "qualified": False,
        "features": {
            "current_close": "100.2",
            "session_vwap": "100",
            "momentum_pct": "0.001",
            "vwap_edge_pct": "0.002",
            "confirmation_passes": 2,
            "ads002_v2_raw_features": {
                "return_5m": "0.001",
                "fast_slow_spread_pct": "0.001",
                "vwap_edge_pct": "0.002",
                "relative_volume_ratio": "1.2",
                "realized_vol_5m": "0.001",
                "spread_bps": "5",
            },
            "regime_confirmations": {
                "SPY": {"window_return_pct": "0.001"},
                "QQQ": {"window_return_pct": "0.001"},
                "IWM": {"window_return_pct": "0.0005"},
            },
        },
        "checks": {
            "strategy": {
                "fast_above_slow": True,
                "rising": True,
                "momentum_ok": False,
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
                "forward_return": "0.003",
            }
        ],
    }


def test_pipeline_is_research_only_and_safe_by_default():
    artifact = build_adaptive_control_artifact(
        session="2026-09-29",
        source_strategy_version="LIVE-TEST",
        current_configuration={
            "min_momentum_pct": "0.002",
            "min_vwap_edge_pct": "0.001",
            "min_confirmations": "2",
            "max_vwap_extension_pct": "0.008",
        },
        daily_report=daily_report(),
        prior_daily_reports=[],
        candidates=[candidate()],
        counterfactual_lab={
            "rolling_searches": {},
            "proposal_ready_parameters": [],
        },
    )
    assert artifact["read_only"] is True
    assert artifact["execution_authority"] is False
    assert artifact["risk_or_sizing_authority"] is False
    assert artifact["broker_calls"] == 0
    assert artifact["railway_changes"] == 0
    assert artifact["live_configuration_changed"] is False
    assert artifact["promotion_authorized"] is False
    assert artifact["shadow_plan"] is None


def test_pipeline_defensive_on_evidence_integrity_failure():
    report = daily_report()
    report["runtime"]["reconciliation_safe"] = False
    artifact = build_adaptive_control_artifact(
        session="2026-09-29",
        source_strategy_version="LIVE-TEST",
        current_configuration={
            "min_momentum_pct": "0.002",
            "min_vwap_edge_pct": "0.001",
            "min_confirmations": "2",
            "max_vwap_extension_pct": "0.008",
        },
        daily_report=report,
        prior_daily_reports=[],
        candidates=[candidate()],
        counterfactual_lab={"rolling_searches": {}},
    )
    assert artifact["control_state"] == "DEFENSIVE"
    assert artifact["adaptation_proposals"] == {}


def test_pipeline_exposes_nostra_and_graen_without_fabricating_maturity():
    artifact = build_adaptive_control_artifact(
        session="2026-09-29",
        source_strategy_version="LIVE-TEST",
        current_configuration={
            "min_momentum_pct": "0.002",
            "min_vwap_edge_pct": "0.001",
            "min_confirmations": "2",
            "max_vwap_extension_pct": "0.008",
        },
        daily_report=daily_report(),
        prior_daily_reports=[],
        candidates=[candidate()],
        counterfactual_lab={"rolling_searches": {}},
    )
    assert artifact["nostra"]["observations"] == 1
    assert artifact["graen_validation"]["frozen_validation_passed"] is False
    assert artifact["graen_validation"]["walk_forward_passed"] is False
    assert artifact["strategy_family_routing"]["selected_research_family"] == "NO_TRADE"
