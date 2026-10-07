"""Capability routing over the existing calendar-aware EquitySessionContext."""
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.equity_sessions import EquitySessionContext

NY = ZoneInfo("America/New_York")
ENDPOINTS = {"iex": "v2/iex", "sip": "v2/sip", "overnight": "v1beta1/overnight", "boats": "v1beta1/boats"}


@dataclass(frozen=True)
class FeedRoute:
    session: str
    session_id: str
    expected_feed: str | None
    capability: str
    execution_policy: str
    reason: str
    # Routing establishes observation capability only. No promotion in this slice.
    entry_authority: bool = False


def route_feed(context: EquitySessionContext, now: datetime, *, tier: str = "basic",
               plus_verified: bool = False) -> FeedRoute:
    if tier not in {"basic", "plus"}:
        raise ValueError("unknown data tier")
    session = context.session.value.upper()
    sid = f"{context.target_session_date}/{session}"
    if not context.tradable or context.starts_at is None or context.ends_at is None or not context.starts_at <= now < context.ends_at:
        return FeedRoute(session, sid, None, "MARKET_CLOSED", "MARKET_CLOSED", context.reason)
    if tier == "plus" and not plus_verified:
        return FeedRoute(session, sid, None, "ENTITLEMENT_UNVERIFIED", "DATA_CAPABILITY_BLOCKED", "Plus entitlement is not verified")
    feed = ("boats" if tier == "plus" else "overnight") if session == "OVERNIGHT" else ("sip" if tier == "plus" else "iex")
    clock = now.astimezone(NY).time().replace(tzinfo=None)
    if feed == "iex" and not time(8) <= clock < time(17):
        return FeedRoute(session, sid, None, "FEED_UNAVAILABLE", "DATA_CAPABILITY_BLOCKED", "Basic IEX coverage gap")
    capability = "INDICATIVE_QUOTES_DELAYED_TRADES" if feed == "overnight" else "REALTIME_SINGLE_VENUE" if feed == "iex" else "REALTIME"
    return FeedRoute(session, sid, feed, capability, "SHADOW_ONLY", "4.4 observation; 4.3 remains authoritative")


def validate_symbols(symbols: tuple[str, ...], *, cap: int = 30) -> None:
    if not symbols or len(set(symbols)) != len(symbols) or len(symbols) > min(cap, 30):
        raise ValueError("invalid or over-capacity observation universe")
    if any(not s or "/" in s or len(s) > 12 for s in symbols):
        raise ValueError("observation scope must be equity symbols")
