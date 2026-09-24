from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import pstdev
from typing import Any
from zoneinfo import ZoneInfo

from .strategy import Signal

NY = ZoneInfo("America/New_York")
FORWARD_HORIZONS_MINUTES = (1, 5, 15, 30)
MAX_RESEARCH_SAMPLES = 4000


def _d(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=NY)
    return stamp.astimezone(NY)


def _completed_session_bars(
    bars: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    now = now.astimezone(NY)
    completed: list[dict[str, Any]] = []
    for bar in bars:
        stamp = _timestamp(bar.get("t"))
        if stamp is None or stamp.date() != now.date():
            continue
        if stamp + timedelta(minutes=1) > now:
            continue
        completed.append(bar)
    completed.sort(key=lambda bar: _timestamp(bar.get("t")) or now)
    return completed


def _session_vwap(bars: list[dict[str, Any]]) -> Decimal:
    total_volume = Decimal("0")
    weighted = Decimal("0")
    closes: list[Decimal] = []
    for bar in bars:
        close = _d(bar.get("c"))
        closes.append(close)
        volume = _d(bar.get("v"))
        bar_vwap = _d(bar.get("vw") or bar.get("c"))
        if volume > 0:
            total_volume += volume
            weighted += bar_vwap * volume
    if total_volume > 0:
        return weighted / total_volume
    if closes:
        return sum(closes) / Decimal(len(closes))
    return Decimal("0")


def _atr_pct(
    bars: list[dict[str, Any]],
    now: datetime,
    window: int = 14,
) -> Decimal:
    session = _completed_session_bars(bars, now)
    if len(session) < 2:
        return Decimal("0")
    sample = session[-(window + 1):]
    true_ranges: list[Decimal] = []
    for previous, current in zip(sample, sample[1:]):
        high = _d(current.get("h"))
        low = _d(current.get("l"))
        previous_close = _d(previous.get("c"))
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    current_close = _d(sample[-1].get("c"))
    if current_close <= 0 or not true_ranges:
        return Decimal("0")
    atr = sum(true_ranges) / Decimal(len(true_ranges))
    return atr / current_close


def _realized_volatility_pct(
    bars: list[dict[str, Any]],
    now: datetime,
    window: int = 15,
) -> Decimal:
    session = _completed_session_bars(bars, now)
    closes = [_d(bar.get("c")) for bar in session[-(window + 1):]]
    returns: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous > 0:
            returns.append(float((current - previous) / previous))
    if len(returns) < 2:
        return Decimal("0")
    return Decimal(str(pstdev(returns)))


def _market_regime(
    confirmation_bars: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    constructive = 0
    observed = 0

    for symbol, bars in confirmation_bars.items():
        session = _completed_session_bars(bars, now)
        if len(session) < 2:
            details[symbol] = {"constructive": False, "reason": "insufficient bars"}
            continue
        observed += 1
        previous_close = _d(session[-2].get("c"))
        current_close = _d(session[-1].get("c"))
        vwap = _session_vwap(session)
        is_constructive = current_close >= previous_close and current_close >= vwap
        if is_constructive:
            constructive += 1
        details[symbol] = {
            "constructive": is_constructive,
            "current_close": str(current_close),
            "previous_close": str(previous_close),
            "session_vwap": str(vwap),
        }

    if observed == 0:
        label = "unknown"
    elif constructive == observed:
        label = "risk_on"
    elif constructive == 0:
        label = "risk_off"
    else:
        label = "mixed"

    return {
        "label": label,
        "constructive": constructive,
        "observed": observed,
        "details": details,
    }


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def enrich_signal(
    signal: Signal,
    bars: list[dict[str, Any]],
    confirmation_bars: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> Signal:
    metadata = dict(signal.metadata or {})
    atr_pct = _atr_pct(bars, now)
    realized_volatility_pct = _realized_volatility_pct(bars, now)
    regime = _market_regime(confirmation_bars, now)

    momentum = _d(metadata.get("momentum_pct"))
    vwap_edge = _d(metadata.get("vwap_edge_pct"))
    confirmation_passes = int(metadata.get("confirmation_passes", 0) or 0)
    min_confirmations = int(metadata.get("min_confirmations", 1) or 1)

    momentum_component = float(_clamp(momentum / Decimal("0.003"), Decimal("0"), Decimal("1")))
    vwap_component = float(_clamp(vwap_edge / Decimal("0.003"), Decimal("0"), Decimal("1")))
    confirmation_component = min(confirmation_passes / max(min_confirmations, 1), 1.0)

    target_atr = Decimal("0.004")
    if atr_pct <= 0:
        volatility_component = 0.0
    else:
        distance = abs(atr_pct - target_atr) / target_atr
        volatility_component = max(0.0, 1.0 - min(float(distance), 1.0))

    regime_component = {
        "risk_on": 1.0,
        "mixed": 0.5,
        "risk_off": 0.0,
        "unknown": 0.25,
    }.get(str(regime["label"]), 0.25)

    score = round(
        35 * momentum_component
        + 25 * vwap_component
        + 20 * confirmation_component
        + 10 * volatility_component
        + 10 * regime_component,
        2,
    )

    suggested_stop_pct = _clamp(
        atr_pct * Decimal("0.8"),
        Decimal("0.0025"),
        Decimal("0.0080"),
    )
    suggested_target_pct = _clamp(
        suggested_stop_pct * Decimal("1.5"),
        Decimal("0.0035"),
        Decimal("0.0150"),
    )

    metadata["research"] = {
        "score": score,
        "atr_pct": str(atr_pct),
        "realized_volatility_pct": str(realized_volatility_pct),
        "market_regime": regime,
        "suggested_stop_pct": str(suggested_stop_pct),
        "suggested_target_pct": str(suggested_target_pct),
        "behavior_changed": False,
    }
    signal.metadata = metadata
    return signal


def record_scan_samples(
    state: Any,
    scan: dict[str, Any],
    now: datetime,
) -> None:
    for symbol, payload in scan.items():
        metadata = payload.get("metadata") or {}
        research = metadata.get("research") or {}
        bar_time = str(metadata.get("bar_time") or now.isoformat())
        key = f"{symbol.upper()}|{bar_time}"
        if key in state.research_sample_keys:
            continue

        reference_price = payload.get("reference_price") or metadata.get("current_close") or "0"
        sample = {
            "key": key,
            "observed_at": now.astimezone(NY).isoformat(),
            "bar_time": bar_time,
            "symbol": symbol.upper(),
            "action": str(payload.get("action") or ""),
            "reason": str(payload.get("reason") or ""),
            "reference_price": str(reference_price),
            "score": research.get("score"),
            "market_regime": (research.get("market_regime") or {}).get("label"),
            "atr_pct": research.get("atr_pct"),
            "realized_volatility_pct": research.get("realized_volatility_pct"),
            "outcomes": {},
        }
        state.research_samples.insert(0, sample)
        state.research_sample_keys.add(key)

    while len(state.research_samples) > MAX_RESEARCH_SAMPLES:
        removed = state.research_samples.pop()
        state.research_sample_keys.discard(str(removed.get("key") or ""))


def resolve_forward_outcomes(
    state: Any,
    market_bars: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> int:
    now = now.astimezone(NY)
    resolved = 0
    bars_by_symbol: dict[str, list[tuple[datetime, Decimal]]] = {}

    for symbol, bars in market_bars.items():
        completed = _completed_session_bars(bars, now)
        bars_by_symbol[symbol.upper()] = [
            (_timestamp(bar.get("t")), _d(bar.get("c")))
            for bar in completed
            if _timestamp(bar.get("t")) is not None
        ]

    for sample in state.research_samples:
        outcomes = sample.setdefault("outcomes", {})
        if len(outcomes) >= len(FORWARD_HORIZONS_MINUTES):
            continue

        reference_price = _d(sample.get("reference_price"))
        bar_time = _timestamp(sample.get("bar_time"))
        if reference_price <= 0 or bar_time is None:
            continue

        symbol_bars = bars_by_symbol.get(str(sample.get("symbol") or "").upper(), [])
        signal_available_at = bar_time + timedelta(minutes=1)

        for horizon in FORWARD_HORIZONS_MINUTES:
            horizon_key = f"{horizon}m"
            if horizon_key in outcomes:
                continue
            target_bar_time = signal_available_at + timedelta(minutes=horizon)
            if now < target_bar_time + timedelta(minutes=1):
                continue

            target = next(
                (
                    (stamp, close)
                    for stamp, close in symbol_bars
                    if stamp is not None and stamp >= target_bar_time
                ),
                None,
            )
            if target is None:
                continue

            stamp, close = target
            return_pct = (close - reference_price) / reference_price
            outcomes[horizon_key] = {
                "bar_time": stamp.isoformat(),
                "price": str(close),
                "return_pct": str(return_pct),
            }
            resolved += 1

    return resolved


def research_summary(state: Any) -> dict[str, Any]:
    samples = list(state.research_samples)
    horizon_stats: dict[str, Any] = {}

    for horizon in FORWARD_HORIZONS_MINUTES:
        key = f"{horizon}m"
        returns: list[Decimal] = []
        for sample in samples:
            payload = (sample.get("outcomes") or {}).get(key)
            if payload is not None:
                returns.append(_d(payload.get("return_pct")))

        if returns:
            average = sum(returns) / Decimal(len(returns))
            positive = sum(value > 0 for value in returns)
            horizon_stats[key] = {
                "count": len(returns),
                "average_return_pct": str(average),
                "positive_rate": positive / len(returns),
            }
        else:
            horizon_stats[key] = {
                "count": 0,
                "average_return_pct": None,
                "positive_rate": None,
            }

    actions = Counter(str(sample.get("action") or "unknown") for sample in samples)
    regimes = Counter(str(sample.get("market_regime") or "unknown") for sample in samples)
    reasons = Counter(str(sample.get("reason") or "unknown") for sample in samples)

    ranked = sorted(
        (
            sample
            for sample in samples
            if sample.get("score") is not None
        ),
        key=lambda sample: float(sample.get("score") or 0),
        reverse=True,
    )

    return {
        "sample_count": len(samples),
        "forward_horizons_minutes": list(FORWARD_HORIZONS_MINUTES),
        "horizons": horizon_stats,
        "actions": dict(actions),
        "regimes": dict(regimes),
        "top_hold_reasons": reasons.most_common(10),
        "top_recent_scores": ranked[:20],
    }
