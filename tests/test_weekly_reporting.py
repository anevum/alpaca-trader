from copy import deepcopy
from datetime import date, datetime, timezone

from app.weekly_reporting import REPORT_VERSION, build_weekly_report


def trade(
    symbol="SPY",
    pnl="-0.10",
    *,
    mfe="0.20",
    mae="-0.30",
    return_pct="-0.10",
    exit_reason="thesis_failure",
):
    return {
        "symbol": symbol,
        "realized_pnl": pnl,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "return_pct": return_pct,
        "hold_minutes": "8",
        "exit_reason": exit_reason,
    }


def daily(event_id, session, pnl, trades, strategy="LIVE-2026-09-25-003"):
    wins = sum(1 for row in trades if float(row["realized_pnl"]) > 0)
    losses = sum(1 for row in trades if float(row["realized_pnl"]) < 0)
    return {
        "event_id": event_id,
        "occurred_at": f"{session}T20:20:00+00:00",
        "strategy_version_id": strategy,
        "payload": {
            "report_type": "daily",
            "session": session,
            "strategy_version_id": strategy,
            "metrics": {
                "trade_count": len(trades),
                "wins": wins,
                "losses": losses,
                "flat": len(trades) - wins - losses,
                "realized_pnl": pnl,
            },
            "trades": trades,
        },
    }


def base_inputs(daily_reports):
    return {
        "daily_reports": daily_reports,
        "earliest_daily_session": (
            min(row["payload"]["session"] for row in daily_reports)
            if daily_reports else None
        ),
        "strategy_versions": [
            {
                "version_id": "LIVE-2026-09-25-003",
                "strategy_name": "rolling_momentum_vwap",
            }
        ],
        "runs": [{"run_id": "11111111-1111-1111-1111-111111111111"}],
        "runtime_instances": [
            {
                "runtime_instance_id": "runtime-1",
                "deployment_id": "deploy-1",
                "git_commit": "abc123",
            }
        ],
        "account_equity_by_session": [
            {
                "session": row["payload"]["session"],
                "starting_equity": "100",
                "ending_equity": "99.5",
            }
            for row in daily_reports
        ],
        "account_weekly_drawdown": {"max_drawdown_pct": "-0.01"},
        "orders_by_session": [
            {"session": row["payload"]["session"], "orders": 2}
            for row in daily_reports
        ],
        "fills_by_session": [
            {"session": row["payload"]["session"], "fills": 2, "entry_fills": 1}
            for row in daily_reports
        ],
        "candidate_by_session": [
            {
                "session": row["payload"]["session"],
                "evaluated": 4,
                "qualified": 1,
                "rejected": 3,
                "signals": 1,
                "partial_backfill": 0,
            }
            for row in daily_reports
        ],
        "rejection_reasons": [],
        "gate_rates": [],
        "positions": [],
        "incidents": [],
        "operational_by_session": [],
        "forward_outcomes": [],
        "live_offline": [],
        "duplicate_checks": {
            "scan_cycle_key": 0,
            "candidate_cycle_symbol": 0,
            "decision_event_key": 0,
        },
        "canonical_period_summary": {
            "execution_quality": {
                "orders": 2 * len(daily_reports),
                "with_fill_slippage": 0,
                "avg_reference_to_fill_bps": None,
            }
        },
        "data_cutoff": "2026-09-26T20:30:00+00:00",
        "warnings": [],
    }


def calendar(*rows):
    return [
        {"date": date.fromisoformat(session), "open": opened, "close": closed}
        for session, opened, closed in rows
    ]


def provenance():
    return {
        "report_version": REPORT_VERSION,
        "generator": "alpaca-trader",
        "git_commit": "abc123",
    }


def build(inputs, cal, start, end):
    return build_weekly_report(
        inputs,
        cal,
        period_start=date.fromisoformat(start),
        period_end=date.fromisoformat(end),
        generation_provenance=provenance(),
        generated_at=datetime(2026, 9, 26, 20, 30, tzinfo=timezone.utc),
    )


def test_complete_week_reconciles_exactly_to_daily_reports():
    reports = [
        daily("d1", "2026-09-24", "-0.10", [trade()]),
        daily("d2", "2026-09-25", "0.25", [trade(pnl="0.25", return_pct="0.25")]),
    ]
    inputs = base_inputs(reports)
    report = build(
        inputs,
        calendar(
            ("2026-09-24", "09:30", "16:00"),
            ("2026-09-25", "09:30", "16:00"),
        ),
        "2026-09-24",
        "2026-09-25",
    )

    assert report["completeness_state"] == "COMPLETE"
    assert report["included_daily_report_ids"] == ["d1", "d2"]
    assert report["metrics"]["weekly_realized_pnl"] == "0.15"
    assert report["metrics"]["completed_trades"] == 2
    assert report["metrics"]["wins"] == 1
    assert report["metrics"]["losses"] == 1
    assert report["metrics"]["best_session"]["session"] == "2026-09-25"
    assert report["metrics"]["worst_session"]["session"] == "2026-09-24"


def test_partial_historical_week_is_not_presented_as_complete():
    reports = [daily("d5", "2026-09-25", "-0.36", [trade()])]
    inputs = base_inputs(reports)
    cal = calendar(
        ("2026-09-21", "09:30", "16:00"),
        ("2026-09-22", "09:30", "16:00"),
        ("2026-09-23", "09:30", "16:00"),
        ("2026-09-24", "09:30", "16:00"),
        ("2026-09-25", "09:30", "16:00"),
    )
    report = build(inputs, cal, "2026-09-21", "2026-09-25")

    assert report["completeness_state"] == "PARTIAL"
    assert report["included_trading_sessions"] == ["2026-09-25"]
    assert report["missing_trading_sessions"] == [
        "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"
    ]
    assert report["classification"]["classification"] == "INSUFFICIENT_CROSS_SESSION_SAMPLE"
    assert any("PARTIAL" in warning for warning in report["warnings"])


def test_missing_daily_after_reporting_started_is_incomplete():
    reports = [
        daily("d1", "2026-09-21", "0", []),
        daily("d3", "2026-09-23", "0", []),
    ]
    inputs = base_inputs(reports)
    cal = calendar(
        ("2026-09-21", "09:30", "16:00"),
        ("2026-09-22", "09:30", "16:00"),
        ("2026-09-23", "09:30", "16:00"),
    )
    report = build(inputs, cal, "2026-09-21", "2026-09-23")

    assert report["completeness_state"] == "INCOMPLETE"
    assert report["missing_trading_sessions"] == ["2026-09-22"]
    assert report["weekly_assessment"]["operating_state"] == "INVESTIGATE_DATA_COMPLETENESS"


def test_holiday_shortened_session_is_explicit():
    reports = [daily("d1", "2026-11-27", "0", [])]
    inputs = base_inputs(reports)
    report = build(
        inputs,
        calendar(("2026-11-27", "09:30", "13:00")),
        "2026-11-27",
        "2026-11-27",
    )

    assert report["completeness_state"] == "COMPLETE"
    assert report["shortened_sessions"] == [
        {"session": "2026-11-27", "open": "09:30", "close": "13:00"}
    ]


def test_multiple_strategy_versions_are_separated_not_blended():
    reports = [
        daily("d1", "2026-09-24", "-0.10", [trade()]),
        daily("d2", "2026-09-25", "0.20", [trade(pnl="0.20")], strategy="LIVE-ALT"),
    ]
    inputs = base_inputs(reports)
    inputs["strategy_versions"] = [
        {"version_id": "LIVE-2026-09-25-003"},
        {"version_id": "LIVE-ALT"},
    ]
    inputs["positions"] = [
        {
            "strategy_version_id": "LIVE-2026-09-25-003",
            "status": "closed",
            "realized_pnl": "-0.10",
        },
        {
            "strategy_version_id": "LIVE-ALT",
            "status": "closed",
            "realized_pnl": "0.20",
        },
    ]
    report = build(
        inputs,
        calendar(
            ("2026-09-24", "09:30", "16:00"),
            ("2026-09-25", "09:30", "16:00"),
        ),
        "2026-09-24",
        "2026-09-25",
    )

    assert set(report["strategy_versions"]) == {"LIVE-2026-09-25-003", "LIVE-ALT"}
    assert report["metrics"]["by_strategy_version"]["LIVE-2026-09-25-003"]["realized_pnl"] == "-0.10"
    assert report["metrics"]["by_strategy_version"]["LIVE-ALT"]["realized_pnl"] == "0.20"
    assert any("Multiple strategy versions" in warning for warning in report["warnings"])


def test_zero_trade_session_is_preserved_in_daily_dispersion():
    reports = [
        daily("d1", "2026-09-24", "0", []),
        daily("d2", "2026-09-25", "-0.10", [trade()]),
    ]
    report = build(
        base_inputs(reports),
        calendar(
            ("2026-09-24", "09:30", "16:00"),
            ("2026-09-25", "09:30", "16:00"),
        ),
        "2026-09-24",
        "2026-09-25",
    )
    assert report["metrics"]["completed_trades"] == 1
    assert report["metrics"]["daily_dispersion"][0] == {
        "session": "2026-09-24",
        "realized_pnl": "0",
    }


def test_research_question_created_without_launching_experiment():
    reports = [
        daily(
            "d1",
            "2026-09-25",
            "-0.30",
            [trade(symbol="A"), trade(symbol="B"), trade(symbol="C")],
        )
    ]
    report = build(
        base_inputs(reports),
        calendar(("2026-09-25", "09:30", "16:00")),
        "2026-09-25",
        "2026-09-25",
    )

    rq = next(
        row for row in report["research_questions"]
        if row["research_question_id"] == "RQ-THESIS-EXIT-ENTRY-QUALITY"
    )
    assert rq["status"] == "MONITOR"
    assert rq["linked_experiment"] is None
    assert report["research_gate"]["residual_downshock_rebound_v2_1_started"] is False


def test_reconciliation_incidents_are_operational_not_strategy_findings():
    reports = [daily("d1", "2026-09-25", "-0.10", [trade()])]
    inputs = base_inputs(reports)
    inputs["incidents"] = [
        {
            "session": "2026-09-25",
            "incident_type": "reconciliation_mismatch",
            "severity": "critical",
            "message": "mismatch",
            "resolved_at": "2026-09-25T18:00:00Z",
        },
        {
            "session": "2026-09-25",
            "incident_type": "reconciliation_mismatch",
            "severity": "critical",
            "message": "mismatch",
            "resolved_at": "2026-09-25T19:00:00Z",
        },
    ]
    report = build(
        inputs,
        calendar(("2026-09-25", "09:30", "16:00")),
        "2026-09-25",
        "2026-09-25",
    )

    assert report["operational_health"]["incident_count"] == 2
    assert any(
        row["likely_type"] == "operational"
        for row in report["evidence_stability"]["repeated_patterns"]
    )
    assert all(
        decision["production_behavior_changed"] is False
        for decision in report["weekly_decisions"]
    )


def test_report_identity_is_deterministic_and_regeneration_is_versioned_by_sources():
    reports = [daily("d1", "2026-09-25", "-0.10", [trade()])]
    inputs = base_inputs(reports)
    cal = calendar(("2026-09-25", "09:30", "16:00"))
    first = build(inputs, cal, "2026-09-25", "2026-09-25")
    second = build(deepcopy(inputs), cal, "2026-09-25", "2026-09-25")

    assert first["report_key"] == second["report_key"]
    assert first["source_fingerprint"] == second["source_fingerprint"]

    changed = deepcopy(inputs)
    changed["daily_reports"][0]["event_id"] = "d1-regenerated"
    third = build(changed, cal, "2026-09-25", "2026-09-25")
    assert third["report_key"] != first["report_key"]


def test_provenance_and_locked_research_decisions_are_retained():
    reports = [daily("d1", "2026-09-25", "-0.10", [trade()])]
    inputs = base_inputs(reports)
    report = build(
        inputs,
        calendar(("2026-09-25", "09:30", "16:00")),
        "2026-09-25",
        "2026-09-25",
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["runtime_provenance"][0]["deployment_id"] == "deploy-1"
    locked = report["evidence_stability"]["locked_decisions_not_open_for_retuning"]
    assert len([row for row in locked if "family" in row]) == 5
    assert any(row.get("research_direction") == "Residual Downshock Rebound v2.1" for row in locked)


def test_candidate_counterfactuals_remain_post_event_only():
    reports = [daily("d1", "2026-09-25", "0", [])]
    inputs = base_inputs(reports)
    inputs["candidate_by_session"][0].update(
        {"evaluated": 10, "qualified": 2, "rejected": 8, "signals": 2}
    )
    inputs["forward_outcomes"] = [
        {
            "session": "2026-09-25",
            "complete": 3,
            "rejected_favorable": 1,
            "rejected_failed": 2,
            "qualified_succeeded": 0,
            "qualified_failed": 0,
        }
    ]
    report = build(
        inputs,
        calendar(("2026-09-25", "09:30", "16:00")),
        "2026-09-25",
        "2026-09-25",
    )
    assert report["candidate_analysis"]["post_event_only"] is True
    assert report["candidate_analysis"]["forward_outcomes_complete"] == 3
    assert report["live_configuration_changed"] is False
