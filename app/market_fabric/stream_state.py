from __future__ import annotations

from collections import deque
from datetime import datetime

from .contracts import MarketEvent, utc


class MarketStateStore:
    def __init__(self, symbols: tuple[str, ...], *, warm_bars: int = 15, max_bars: int = 120):
        if not 1 <= warm_bars <= max_bars <= 2400:
            raise ValueError("invalid rolling window")
        self.symbols = symbols
        self.warm_bars = warm_bars
        self.max_bars = max_bars
        self.context = None
        self.generation = None
        self.rows = {}
        self.out_of_order = 0
        self.duplicates = 0
        self.dirty = set()
        self.connection = "DISCONNECTED"
        self.subscribed = set()

    def begin(self, generation: str, feed: str, session_id: str):
        if self.context != (feed, session_id):
            self.rows.clear()
        else:
            # Rolling completed bars survive; a new connection must deliver its own
            # fresh quote before becoming evaluable, even if an old quote is young.
            for row in self.rows.values():
                row.pop("quote", None)
                row.pop("trade", None)
                row["timestamps"].pop("quote", None)
                row["timestamps"].pop("trade", None)
        self.context = (feed, session_id)
        self.generation = generation
        self.connection = "WARMING"
        self.subscribed.clear()
        self.dirty.update(self.symbols)

    def apply(self, event: MarketEvent) -> bool:
        if event.symbol not in self.symbols or event.generation != self.generation or (event.feed, event.session_id) != self.context:
            return False
        row = self.rows.setdefault(event.symbol, {"bars": deque(maxlen=self.max_bars), "activity": deque(maxlen=10000), "timestamps": {}})
        key = "bar" if event.kind == "bar_revision" else event.kind
        previous = row["timestamps"].get(key)
        if event.kind == "bar_revision":
            # Provider corrections can revise a known completed bar, never append an unknown old bar.
            for i, bar in enumerate(row["bars"]):
                if bar["timestamp"] == event.source_at.isoformat():
                    row["bars"][i] = self._bar(event)
                    row["feature_revision"] = row.get("feature_revision", 0)+1
                    self.dirty.add(event.symbol)
                    return True
            self.out_of_order += 1
            return False
        if previous is not None and event.source_at <= previous:
            if event.source_at < previous:
                self.out_of_order += 1
            else:
                self.duplicates += 1
            return False
        row["timestamps"][key] = event.source_at
        row[key] = dict(event.payload)
        row["feed"] = event.feed
        row["session"] = event.session
        row["activity"].append(event.received_at)
        if event.kind == "bar":
            row["bars"].append(self._bar(event))
            row["feature_revision"] = row.get("feature_revision", 0)+1
        self.dirty.add(event.symbol)
        return True

    @staticmethod
    def _bar(event):
        return {**event.payload, "symbol": event.symbol, "timestamp": event.source_at.isoformat(),
                "feed": event.feed, "session": event.session, "provenance": "OBSERVED",
                "source": f"ALPACA/{event.feed}", "quality_state": "LIVE"}

    def snapshot(self, symbol: str, now: datetime) -> dict:
        row = self.rows.get(symbol, {})
        activity = row.get("activity")
        if activity is not None:
            while activity and (utc(now)-activity[0]).total_seconds() > 60:
                activity.popleft()
        quote = row.get("quote", {})
        source = row.get("timestamps", {}).get("quote")
        bar_source = row.get("timestamps", {}).get("bar")
        age = (utc(now) - source).total_seconds() * 1000 if source else None
        bar_age = (utc(now) - bar_source).total_seconds() * 1000 if bar_source else None
        bid, ask = quote.get("bid"), quote.get("ask")
        reasons = []
        if self.connection not in {"WARMING", "HEALTHY"}:
            reasons.append("STREAM_DISCONNECTED")
        if symbol not in self.subscribed:
            reasons.append("SUBSCRIPTION_MISSING")
        if age is None or not 0 <= age <= 45000:
            reasons.append("STALE_QUOTE")
        if bar_age is None or not 0 <= bar_age <= 120000:
            reasons.append("STALE_BAR")
        if bid is None or bid <= 0:
            reasons.append("NO_BID")
        if ask is None or ask <= 0:
            reasons.append("NO_ASK")
        valid = bid is not None and ask is not None and 0 < bid < ask
        if bid is not None and ask is not None and bid >= ask:
            reasons.append("LOCKED_OR_CROSSED")
        if len(row.get("bars", [])) < self.warm_bars:
            reasons.append("INSUFFICIENT_OBSERVATIONS")
        recent = list(row.get("bars", []))[-self.warm_bars:]
        if any((utc(b["timestamp"])-utc(a["timestamp"])).total_seconds() > 120 for a,b in zip(recent,recent[1:])):
            reasons.append("DATA_GAP")
        mid = (bid + ask) / 2 if valid else None
        return {"symbol": symbol, "feed": row.get("feed"), "session": row.get("session"),
                "bid": bid, "ask": ask, "mid": mid, "spread_bps": (ask-bid)/mid*10000 if valid else None,
                "quote_source_at": source.isoformat() if source else None, "quote_age_ms": age,
                "bar_age_ms": bar_age, "evaluable": not reasons, "rejection_code": reasons[0] if reasons else None,
                "rejection_codes": reasons, "candidate_state": "BLOCKED" if reasons else "HOLD",
                "quality_state": "STALE" if age is None or age > 45000 or self.connection == "DISCONNECTED" else "WARMING" if reasons else "LIVE",
                "events_per_minute": len(activity) if activity is not None else 0,
                "entry_authority": False}
