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
