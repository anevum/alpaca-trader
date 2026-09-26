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
