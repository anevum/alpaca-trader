"""RHEN 4.4 observation contracts. This package has no execution authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from types import MappingProxyType
from typing import Mapping


def utc(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("authoritative timestamp requires timezone")
    return result.astimezone(timezone.utc)


def number(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a market value")
    result = float(value)
    if not isfinite(result) or (positive and result <= 0):
        raise ValueError("invalid market value")
    return result


@dataclass(frozen=True)
class MarketEvent:
    generation: str
    sequence: int
    feed: str
    session: str
    session_id: str
    symbol: str
    kind: str
    source_at: datetime
    received_at: datetime
    payload: Mapping

    def __post_init__(self):
        object.__setattr__(self, "source_at", utc(self.source_at))
        object.__setattr__(self, "received_at", utc(self.received_at))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if self.source_at > self.received_at:
            raise ValueError("future market observation")


def normalize(raw: dict, *, generation: str, sequence: int, feed: str,
              session: str, session_id: str, received_at: datetime) -> MarketEvent:
    kind = {"q": "quote", "t": "trade", "b": "bar", "u": "bar_revision", "s": "status"}.get(raw.get("T"))
    if not kind or not raw.get("S"):
        raise ValueError("unsupported market event")
    if kind == "quote":
        payload = {"bid": number(raw["bp"]), "ask": number(raw["ap"]),
                   "bid_size": number(raw.get("bs", 0)), "ask_size": number(raw.get("as", 0))}
    elif kind in {"bar", "bar_revision"}:
        payload = {name: number(raw[key], positive=True) for name, key in
                   (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
        if not payload["low"] <= min(payload["open"], payload["close"]) <= max(payload["open"], payload["close"]) <= payload["high"]:
            raise ValueError("invalid OHLC range")
        payload.update(volume=number(raw["v"]) if raw.get("v") is not None else None, complete=True)
        if payload["volume"] is not None and payload["volume"] < 0:
            raise ValueError("negative volume")
        if raw.get("vw") is not None:
            payload["vwap"] = number(raw["vw"], positive=True)
    elif kind == "trade":
        payload = {"price": number(raw["p"], positive=True), "size": number(raw["s"]), "trade_id": raw.get("i"),
                   "quality": "DELAYED" if feed == "overnight" else "LIVE"}
    else:
        payload = {"status_code": raw.get("sc"), "reason": raw.get("sm")}
    return MarketEvent(generation, sequence, feed, session, session_id, str(raw["S"]).upper(), kind,
                       utc(raw["t"]), utc(received_at), payload)
