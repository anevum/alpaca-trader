"""Research-only paired leaderboard: chronology, multiplicity and immutable inputs."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.research_agent.rule_set_scorecard import paired_rule_set_scorecard


def tournament(serial: int):
    control = "0.001"
    volume = str(serial / 1000)
    trend = str(-serial / 2000)
    return {
        "methodology": "rhen-rule-set-lab-v1",
        "research_only": True,
        "automatic_promotion_authorized": False,
        "manifest": {
            "frozen_settings": {
                "strategy_name": "rolling_momentum_vwap",
                "quality_score": 80,
            },
            "rules": [
                {"name": "production", "entry_rules": []},
                {"name": "volume", "entry_rules": [{"indicator": "relative_volume"}]},
                {"name": "trend", "entry_rules": [{"indicator": "trend_persistence"}]},
            ],
            "initial_equity": "100",
            "spread_bps": "5",
            "slippage_bps_per_side": "2",
            "data_fingerprint": f"{serial:064x}",
        },
        "results": [
            {"name": "production", "summary": {"net_pnl": control},
             "net_pnl_delta_to_control": "0",
             "validated_alpha": False, "promotion_authorized": False},
            {"name": "volume", "summary": {"net_pnl": volume},
             "net_pnl_delta_to_control": str(serial / 1000 - 0.001),
             "validated_alpha": False, "promotion_authorized": False},
            {"name": "trend", "summary": {"net_pnl": trend},
             "net_pnl_delta_to_control": str(-serial / 2000 - 0.001),
             "validated_alpha": False, "promotion_authorized": False},
        ],
    }


def runs():
    records = []
    day = date(2026, 9, 21)
    for i in range(1, 10):
        phase = "DEVELOPMENT" if i <= 3 else ("WALK_FORWARD" if i <= 6 else "HOLDOUT")
        records.append({
            "session": day.isoformat(),
            "phase": phase,
            "source_fingerprint": f"{i + 100:064x}",
            "market_regime": "LOW_VOL",
            "tournament": tournament(i),
        })
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return records


def test_scorecard_respects_session_pairing_and_untouched_holdout_boundary():
    result = paired_rule_set_scorecard(runs())
    assert result["status"] == "DESCRIPTIVE_RESEARCH_ONLY_NOT_VALIDATED"
    assert result["session_count"] == 9
    assert result["comparison_count"] == 2
    assert result["holdout_is_untouched_verified"] is False
    assert result["broker_fill_and_stop_parity_verified"] is False
    assert result["live_changes_authorized"] is False
    assert result["ranking_basis"] == "WALK_FORWARD_SESSION_MEAN_AFTER_FRICTION"
    assert result["leaderboard"][0]["name"] == "volume"
    validation = result["leaderboard"][0]["by_phase"]["WALK_FORWARD"]
    assert validation["independent_sessions"] == 3
    assert validation["positive_sessions"] == 3
    assert validation["bonferroni_sign_test_p"] == "0.5"
    assert result["leaderboard"][0]["eligible_for_live_promotion"] is False
    assert result["scorecard_fingerprint"] == paired_rule_set_scorecard(runs())["scorecard_fingerprint"]


def test_scorecard_rejects_duplicate_sessions_and_fingerprints():
    records = runs()
    records[-1]["session"] = records[0]["session"]
    with pytest.raises(ValueError, match="duplicate independent"):
        paired_rule_set_scorecard(records)
    records = runs()
    records[-1]["source_fingerprint"] = records[0]["source_fingerprint"]
    with pytest.raises(ValueError, match="duplicated immutable"):
        paired_rule_set_scorecard(records)
    records = runs()
    records[-1]["tournament"]["manifest"]["data_fingerprint"] = records[0]["tournament"]["manifest"]["data_fingerprint"]
    with pytest.raises(ValueError, match="duplicated independently"):
        paired_rule_set_scorecard(records)


def test_scorecard_fails_closed_on_strategy_changes_and_holdout_leak():
    records = runs()
    records[6]["tournament"]["manifest"]["frozen_settings"]["quality_score"] = 85
    with pytest.raises(ValueError, match="settings drifted"):
        paired_rule_set_scorecard(records)
    records = runs()
    records[3]["phase"] = "HOLDOUT"
    with pytest.raises(ValueError, match="chronology"):
        paired_rule_set_scorecard(records)
    records = runs()
    records[3]["tournament"]["results"][1]["net_pnl_delta_to_control"] = "999"
    with pytest.raises(ValueError, match="does not reconcile"):
        paired_rule_set_scorecard(records)


def test_scorecard_refuses_authorized_live_changes():
    records = runs()
    records[0]["tournament"]["automatic_promotion_authorized"] = True
    with pytest.raises(ValueError, match="authorized-to-trade"):
        paired_rule_set_scorecard(records)
    records = runs()
    records[0]["tournament"]["results"][1]["promotion_authorized"] = True
    with pytest.raises(ValueError, match="promotion-authorized"):
        paired_rule_set_scorecard(records)

def test_weekend_dates_are_not_accidentally_treated_as_independent_sessions():
    records = runs()
    records[2]["session"] = "2026-09-26"
    with pytest.raises(ValueError, match="weekends"):
        paired_rule_set_scorecard(records)
