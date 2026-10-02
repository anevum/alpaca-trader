from datetime import date

from app.research_scheduler import is_last_session_of_week


def test_last_session_of_week_friday_to_monday():
    assert is_last_session_of_week(
        date(2026, 9, 25),
        date(2026, 9, 28),
    )


def test_not_last_session_thursday_to_friday():
    assert not is_last_session_of_week(
        date(2026, 9, 24),
        date(2026, 9, 25),
    )


def test_last_session_when_no_future_session_is_available():
    assert is_last_session_of_week(date(2026, 9, 25), None)


def test_startup_catch_up_backfills_friday_and_weekly_review():
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.research_scheduler import ResearchReportScheduler

    class MarketData:
        async def market_calendar(self, start, end):
            sessions = [
                date(2026, 9, 24),
                date(2026, 9, 25),
                date(2026, 9, 28),
            ]
            return [session for session in sessions if start <= session <= end]

    class Empty:
        pass

    scheduler = ResearchReportScheduler(
        Empty(),
        Empty(),
        MarketData(),
        Empty(),
        Empty(),
    )
    generated = []

    async def daily(session):
        generated.append(("daily", session))
        return {}

    async def weekly(start, end):
        generated.append(("weekly", start, end))
        return {}

    scheduler.generate_daily = daily
    scheduler.generate_weekly = weekly

    asyncio.run(
        scheduler._catch_up_latest_completed(
            datetime(2026, 9, 26, 2, 55, tzinfo=ZoneInfo("America/New_York"))
        )
    )

    assert ("daily", date(2026, 9, 25)) in generated
    assert (
        "weekly",
        date(2026, 9, 21),
        date(2026, 9, 25),
    ) in generated


def test_canonical_weekly_generation_persists_once_with_deterministic_key():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class MarketData:
        async def market_calendar_details(self, start, end):
            return [{"date": date(2026, 9, 25), "open": "09:30", "close": "16:00"}]

    class Sink:
        def __init__(self):
            self.enabled = False
            self.last_error = None
            self.events = []

        def status(self):
            return {}

        async def emit_critical(self, **event):
            self.events.append(event)
            return True

    sink = Sink()
    settings = SimpleNamespace(
        trading_ingest_url="https://foundation.example.test/v1/trading-ingest",
        trading_ingest_token="x" * 32,
        trading_run_id="11111111-1111-1111-1111-111111111111",
        strategy_version_id="LIVE-2026-09-25-003",
    )
    scheduler = ResearchReportScheduler(
        settings,
        SimpleNamespace(),
        MarketData(),
        SimpleNamespace(),
        sink,
    )
    scheduler._canonical_weekly_inputs = lambda start, end: asyncio.sleep(
        0,
        result={
            "daily_reports": [
                {
                    "event_id": "daily-1",
                    "payload": {
                        "session": "2026-09-25",
                        "metrics": {
                            "trade_count": 0,
                            "wins": 0,
                            "losses": 0,
                            "flat": 0,
                            "realized_pnl": "0",
                        },
                        "trades": [],
                    },
                }
            ],
            "earliest_daily_session": "2026-09-25",
            "strategy_versions": [{"version_id": "LIVE-2026-09-25-003"}],
            "runs": [{"run_id": settings.trading_run_id}],
            "runtime_instances": [],
            "account_equity_by_session": [],
            "account_weekly_drawdown": {},
            "orders_by_session": [],
            "fills_by_session": [],
            "candidate_by_session": [],
            "rejection_reasons": [],
            "gate_rates": [],
            "positions": [],
            "incidents": [],
            "operational_by_session": [],
            "forward_outcomes": [],
            "live_offline": [],
            "duplicate_checks": {},
            "canonical_period_summary": {},
            "data_cutoff": "2026-09-26T14:00:00Z",
            "warnings": [],
        },
    )
    scheduler.fetch_weekly_report = lambda end_date=None: asyncio.sleep(0, result=None)

    report = asyncio.run(
        scheduler.generate_weekly(date(2026, 9, 25), date(2026, 9, 25))
    )

    assert report["completeness_state"] == "COMPLETE"
    assert len(sink.events) == 1
    assert sink.events[0]["event_type"] == "research_weekly_report"
    assert sink.events[0]["event_key"].endswith(report["report_key"])
    assert report["live_configuration_changed"] is False


def test_manual_regeneration_rejects_non_session_week_end():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class MarketData:
        async def market_calendar(self, start, end):
            return [date(2026, 9, 25)]

    scheduler = ResearchReportScheduler(
        SimpleNamespace(),
        SimpleNamespace(),
        MarketData(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    try:
        asyncio.run(scheduler.regenerate_weekly(date(2026, 9, 26)))
    except ValueError as exc:
        assert "actual US equity trading session" in str(exc)
    else:
        raise AssertionError("Saturday regeneration should be rejected")


def test_manual_regeneration_uses_actual_trading_week_boundary():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class MarketData:
        async def market_calendar(self, start, end):
            return [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]

    scheduler = ResearchReportScheduler(
        SimpleNamespace(),
        SimpleNamespace(),
        MarketData(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    observed = []

    async def weekly(start, end):
        observed.append((start, end))
        return {"period_start": start.isoformat(), "period_end": end.isoformat()}

    scheduler.generate_weekly = weekly
    report = asyncio.run(scheduler.regenerate_weekly(date(2026, 9, 25)))

    assert observed == [(date(2026, 9, 21), date(2026, 9, 25))]
    assert report["period_end"] == "2026-09-25"


def test_weekly_report_read_uses_durable_report_api():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    scheduler = ResearchReportScheduler(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    calls = []

    async def api_get(**params):
        calls.append(params)
        return {
            "ok": True,
            "report": {
                "report_key": "2026-09-21:2026-09-25:rhen-weekly-v1:test",
                "completeness_state": "PARTIAL",
            },
        }

    scheduler._report_api_get = api_get
    report = asyncio.run(scheduler.fetch_weekly_report(end_date=date(2026, 9, 25)))

    assert calls == [{"latest": "weekly", "week_end": "2026-09-25"}]
    assert report["completeness_state"] == "PARTIAL"


def test_crypto_forward_tick_runs_even_when_equity_reporting_fails():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class State:
        def __init__(self):
            self.events = []

        def record_event(self, **event):
            self.events.append(event)

    state = State()
    scheduler = ResearchReportScheduler(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        state,
        SimpleNamespace(),
    )
    calls = []

    async def crypto_tick(now=None):
        calls.append("crypto")

    async def failing_catch_up(now=None):
        calls.append("equity")
        scheduler.stop_event.set()
        raise TypeError("synthetic equity reporting failure")

    scheduler._crypto_forward_tick = crypto_tick
    scheduler._catch_up_latest_completed = failing_catch_up

    asyncio.run(scheduler._run())

    assert calls == ["crypto", "equity"]
    assert state.events[-1]["kind"] == "research_reporting"
    assert "TypeError" in state.events[-1]["reason"]


def test_crypto_evidence_reader_uses_fast_endpoint_parameter():
    import asyncio
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    scheduler = ResearchReportScheduler(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    calls = []

    async def fake_report_api_get(**params):
        calls.append(params)
        return {"ok": True, "candidates": []}

    scheduler._report_api_get = fake_report_api_get
    result = asyncio.run(
        scheduler._crypto_evidence_api_get(evidence_session="2026-09-29")
    )

    assert result == {"ok": True, "candidates": []}
    assert calls == [{"crypto_evidence_session": "2026-09-29"}]


def test_crypto_forward_tick_executes_with_utc_timestamp():
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class State:
        def __init__(self):
            self.crypto_forward_evidence_state = {}
            self.events = []

        def record_event(self, **event):
            self.events.append(event)

    class Runner:
        def __init__(self):
            self.calls = []

        async def run_recent(self, now):
            self.calls.append(now)
            return SimpleNamespace(errors=0)

    state = State()
    event_sink = SimpleNamespace(enabled=True)
    scheduler = ResearchReportScheduler(
        SimpleNamespace(crypto_lane_enabled=True),
        SimpleNamespace(),
        SimpleNamespace(),
        state,
        event_sink,
    )
    runner = Runner()
    scheduler.crypto_forward_runner = runner
    now = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)

    asyncio.run(scheduler._crypto_forward_tick(now))

    assert runner.calls == [now]
    assert scheduler.last_crypto_forward_at == now
    assert state.events == []


def test_crypto_promotion_tick_fails_closed_from_durable_evidence():
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler

    class State:
        def __init__(self):
            self.crypto_graen_promotion = {
                "status": "GATED",
                "promotion_ready": False,
            }
            self.crypto_graen_evidence = {}
            self.events = []

        def record_event(self, **event):
            self.events.append(event)

    state = State()
    scheduler = ResearchReportScheduler(
        SimpleNamespace(crypto_lane_enabled=True),
        SimpleNamespace(),
        SimpleNamespace(),
        state,
        SimpleNamespace(),
    )
    calls = []

    async def fake_report_api_get(**params):
        calls.append(params)
        return {
            "ok": True,
            "evidence": {
                "resolved_candidate_predictions": 174,
                "paper_round_trips": 0,
                "utc_hours_covered": [23],
                "weekdays_covered": [2],
                "volatility_regimes": ["high"],
                "liquidity_regimes": ["wide"],
                "pairs_covered": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "metrics": {
                    "net_expectancy_after_costs": -0.09,
                    "brier_score": None,
                    "log_loss": None,
                    "calibration_intercept": None,
                    "calibration_slope": None,
                    "discrimination": None,
                    "max_drawdown": 0.0175,
                    "tail_loss": None,
                    "mfe": 0.01,
                    "mae": -0.01,
                    "slippage": 10.0,
                    "spread_sensitivity": 0.07,
                    "regime_stability": None,
                    "time_of_week_stability": None,
                },
                "net_expectancy_positive_after_high_costs": False,
                "walk_forward_passed": False,
                "holdout_passed": False,
                "dependence_adjusted": False,
                "multiplicity_adjusted": False,
                "no_lookahead_verified": False,
            },
        }

    scheduler._report_api_get = fake_report_api_get
    now = datetime(2026, 9, 30, 1, 15, tzinfo=timezone.utc)
    asyncio.run(scheduler._crypto_promotion_tick(now))

    assert calls == [{"crypto_promotion": "1"}]
    assert state.crypto_graen_promotion["promotion_ready"] is False
    assert state.crypto_graen_promotion["status"] == "GATED"
    assert "CANDIDATE_FLOOR" in state.crypto_graen_promotion["reason_codes"]
    assert "NET_EXPECTANCY_POSITIVE_AFTER_HIGH_COSTS" in state.crypto_graen_promotion["reason_codes"]
    assert state.crypto_graen_evidence["resolved_candidate_predictions"] == 174
    assert scheduler.last_crypto_promotion_at == now
    assert state.events[-1]["kind"] == "crypto_promotion"
    assert state.events[-1]["action"] == "blocked"


def test_post_event_generation_uses_purpose_built_foundation_read():
    import inspect
    from app.research_scheduler import ResearchReportScheduler
    source = inspect.getsource(ResearchReportScheduler.generate_post_event_evidence)
    assert "post_event_evidence_session=requested" in source
    assert "evidence_reader=post_event_evidence_reader" in source
