from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from .market_data import MarketDataClient


NY = ZoneInfo("America/New_York")
PREMARKET_OPEN = time(4, 0)
EXTENDED_CLOSE = time(20, 0)


class EquitySession(StrEnum):
    CLOSED = "closed"
    OVERNIGHT = "overnight"
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"


@dataclass(frozen=True)
class EquitySessionContext:
    session: EquitySession
    target_session_date: date | None
    starts_at: datetime | None
    ends_at: datetime | None
    regular_open: datetime | None
    regular_close: datetime | None
    extended: bool
    tradable: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "session": self.session.value,
            "target_session_date": (
                self.target_session_date.isoformat()
                if self.target_session_date is not None
                else None
            ),
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "regular_open": self.regular_open.isoformat() if self.regular_open else None,
            "regular_close": self.regular_close.isoformat() if self.regular_close else None,
            "extended": self.extended,
            "tradable": self.tradable,
            "reason": self.reason,
        }


def _parse_market_time(raw: object, default: time) -> time:
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        hour_text, minute_text = text.split(":", 1)
        return time(int(hour_text), int(minute_text))
    except (TypeError, ValueError):
        return default


class EquitySessionResolver:
    """Classify the current U.S. equity session using Alpaca's trading calendar.

    RHEN deliberately treats a regular session and an extended session as
    different execution regimes. Overnight is only considered tradable when the
    next published regular session is the next calendar day. This conservative
    rule naturally closes the lane for the Friday night/weekend gap and avoids
    guessing across exchange holidays.
    """

    def __init__(self, market_data: MarketDataClient):
        self.market_data = market_data
        self._calendar_cache: dict[date, dict] = {}
        self._cache_loaded_through: date | None = None

    async def _ensure_calendar(self, now_date: date) -> None:
        required_end = now_date + timedelta(days=8)
        if (
            self._cache_loaded_through is not None
            and self._cache_loaded_through >= required_end
            and any(day >= now_date for day in self._calendar_cache)
        ):
            return

        rows = await self.market_data.market_calendar_details(
            start=now_date - timedelta(days=1),
            end=required_end,
        )
        for row in rows:
            session_date = row.get("date")
            if isinstance(session_date, date):
                self._calendar_cache[session_date] = dict(row)
        self._cache_loaded_through = required_end

    @staticmethod
    def _closed(reason: str) -> EquitySessionContext:
        return EquitySessionContext(
            session=EquitySession.CLOSED,
            target_session_date=None,
            starts_at=None,
            ends_at=None,
            regular_open=None,
            regular_close=None,
            extended=False,
            tradable=False,
            reason=reason,
        )

    def _bounds(self, row: dict) -> tuple[datetime, datetime]:
        session_date = row["date"]
        open_time = _parse_market_time(row.get("open"), time(9, 30))
        close_time = _parse_market_time(row.get("close"), time(16, 0))
        return (
            datetime.combine(session_date, open_time, tzinfo=NY),
            datetime.combine(session_date, close_time, tzinfo=NY),
        )

    async def classify(
        self,
        now: datetime | None = None,
    ) -> EquitySessionContext:
        current = (now or datetime.now(NY)).astimezone(NY)
        await self._ensure_calendar(current.date())

        today_row = self._calendar_cache.get(current.date())
        if today_row is not None:
            regular_open, regular_close = self._bounds(today_row)
            premarket_open = datetime.combine(
                current.date(),
                PREMARKET_OPEN,
                tzinfo=NY,
            )
            extended_close = datetime.combine(
                current.date(),
                EXTENDED_CLOSE,
                tzinfo=NY,
            )

            if current < premarket_open:
                prior_start = datetime.combine(
                    current.date() - timedelta(days=1),
                    EXTENDED_CLOSE,
                    tzinfo=NY,
                )
                return EquitySessionContext(
                    session=EquitySession.OVERNIGHT,
                    target_session_date=current.date(),
                    starts_at=prior_start,
                    ends_at=premarket_open,
                    regular_open=regular_open,
                    regular_close=regular_close,
                    extended=True,
                    tradable=True,
                    reason="overnight session before published regular trading day",
                )

            if current < regular_open:
                return EquitySessionContext(
                    session=EquitySession.PREMARKET,
                    target_session_date=current.date(),
                    starts_at=premarket_open,
                    ends_at=regular_open,
                    regular_open=regular_open,
                    regular_close=regular_close,
                    extended=True,
                    tradable=True,
                    reason="premarket session before published regular open",
                )

            if current < regular_close:
                return EquitySessionContext(
                    session=EquitySession.REGULAR,
                    target_session_date=current.date(),
                    starts_at=regular_open,
                    ends_at=regular_close,
                    regular_open=regular_open,
                    regular_close=regular_close,
                    extended=False,
                    tradable=True,
                    reason="published regular trading session",
                )

            if current < extended_close:
                return EquitySessionContext(
                    session=EquitySession.AFTER_HOURS,
                    target_session_date=current.date(),
                    starts_at=regular_close,
                    ends_at=extended_close,
                    regular_open=regular_open,
                    regular_close=regular_close,
                    extended=True,
                    tradable=True,
                    reason="after-hours session after published regular close",
                )

        # 20:00 ET onward belongs to the next published trading day only when
        # that day is tomorrow. This closes Friday night and holiday gaps.
        future_days = sorted(
            day for day in self._calendar_cache if day > current.date()
        )
        if current.time() >= EXTENDED_CLOSE and future_days:
            target = future_days[0]
            if target == current.date() + timedelta(days=1):
                target_row = self._calendar_cache[target]
                regular_open, regular_close = self._bounds(target_row)
                start = datetime.combine(
                    current.date(),
                    EXTENDED_CLOSE,
                    tzinfo=NY,
                )
                end = datetime.combine(target, PREMARKET_OPEN, tzinfo=NY)
                return EquitySessionContext(
                    session=EquitySession.OVERNIGHT,
                    target_session_date=target,
                    starts_at=start,
                    ends_at=end,
                    regular_open=regular_open,
                    regular_close=regular_close,
                    extended=True,
                    tradable=True,
                    reason="overnight session leading into next published trading day",
                )

        return self._closed("outside Alpaca 24/5 equity trading window")
