from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import sqrt
from typing import Any

from .config import Settings
from .strategy import Signal


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _clamp(value: Decimal) -> Decimal:
    return min(max(value, Decimal("0")), Decimal("1"))


def _bar_timestamp(bar: dict[str, Any]) -> datetime | None:
    raw = bar.get("t")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp


def _latest_session_bars(
    bars: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    stamped: list[tuple[datetime, dict[str, Any]]] = []
    for bar in bars:
        stamp = _bar_timestamp(bar)
        if stamp is not None:
            stamped.append((stamp, bar))
    if not stamped:
        return []
    stamped.sort(key=lambda item: item[0])
    latest_date = stamped[-1][0].date()
    session = [bar for stamp, bar in stamped if stamp.date() == latest_date]
    return session[-max(limit, 1):]


def _relative_volume_ratio(bars: list[dict[str, Any]]) -> Decimal:
    session = _latest_session_bars(bars, 21)
    if len(session) < 2:
        return Decimal("1")
    latest = d(session[-1].get("v"))
    history = [d(bar.get("v")) for bar in session[:-1] if d(bar.get("v")) > 0]
    if latest <= 0 or not history:
        return Decimal("1")
    average = sum(history, Decimal("0")) / Decimal(len(history))
    return latest / average if average > 0 else Decimal("1")


def _trend_persistence(bars: list[dict[str, Any]]) -> Decimal:
    session = _latest_session_bars(bars, 9)
    closes = [d(bar.get("c")) for bar in session if d(bar.get("c")) > 0]
    if len(closes) < 2:
        return Decimal("0.5")
    positive = sum(
        Decimal("1")
        for previous, current in zip(closes, closes[1:])
        if current > previous
    )
    return positive / Decimal(len(closes) - 1)


def score_opportunity(
    settings: Settings,
    signal: Signal,
    bars: list[dict[str, Any]],
    market_quality: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic 0-100 ranking score from already-observed data.

    This score changes candidate ordering only. It does not bypass strategy,
    market-quality, risk, cash, reconciliation, or execution gates.
    """
    metadata = signal.metadata or {}
    momentum = max(d(metadata.get("momentum_pct")), Decimal("0"))
    vwap_edge = max(d(metadata.get("vwap_edge_pct")), Decimal("0"))

    momentum_scale = max(settings.min_momentum_pct * Decimal("4"), Decimal("0.002"))
    vwap_scale = max(settings.target_pct, Decimal("0.0025"))

    confirmations = metadata.get("confirmations") or {}
    independent = [
        payload
        for symbol, payload in confirmations.items()
        if str(symbol).upper() != signal.symbol.upper()
    ]
    fresh_passes = d(market_quality.get("fresh_confirmation_passes"))
    confirmation_ratio = (
        _clamp(fresh_passes / Decimal(len(independent)))
        if independent
        else Decimal("0")
    )

    spread = max(d(market_quality.get("spread_pct")), Decimal("0"))
    spread_ratio = _clamp(
        Decimal("1") - (spread / settings.max_spread_pct)
    ) if settings.max_spread_pct > 0 else Decimal("0")

    bar_age = max(d(market_quality.get("bar_age_seconds")), Decimal("0"))
    freshness_ratio = _clamp(
        Decimal("1") - (bar_age / Decimal(settings.max_bar_age_seconds))
    )

    relative_volume = _relative_volume_ratio(bars)
    relative_volume_ratio = _clamp(relative_volume / Decimal("2"))
    persistence = _clamp(_trend_persistence(bars))

    components = {
        "momentum": Decimal("30") * _clamp(momentum / momentum_scale),
        "vwap_edge": Decimal("20") * _clamp(vwap_edge / vwap_scale),
        "confirmations": Decimal("15") * confirmation_ratio,
        "spread": Decimal("15") * spread_ratio,
        "freshness": Decimal("5") * freshness_ratio,
        "relative_volume": Decimal("10") * relative_volume_ratio,
        "trend_persistence": Decimal("5") * persistence,
    }
    total = sum(components.values(), Decimal("0"))
    score = round(float(total), 2)
    return {
        "score": score,
        "components": {name: round(float(value), 2) for name, value in components.items()},
        "relative_volume_ratio": round(float(relative_volume), 3),
        "trend_persistence": round(float(persistence), 3),
    }


def _session_close_map(
    bars: list[dict[str, Any]],
    lookback_bars: int,
) -> dict[str, float]:
    session = _latest_session_bars(bars, lookback_bars + 1)
    result: dict[str, float] = {}
    for bar in session:
        stamp = _bar_timestamp(bar)
        close = d(bar.get("c"))
        if stamp is None or close <= 0:
            continue
        result[stamp.isoformat()] = float(close)
    return result


def return_correlation(
    left_bars: list[dict[str, Any]],
    right_bars: list[dict[str, Any]],
    *,
    lookback_bars: int,
    min_observations: int,
) -> tuple[float | None, int]:
    """Pearson correlation of aligned one-minute simple returns."""
    left = _session_close_map(left_bars, lookback_bars)
    right = _session_close_map(right_bars, lookback_bars)
    common = sorted(set(left).intersection(right))
    if len(common) < min_observations + 1:
        return None, max(len(common) - 1, 0)

    common = common[-(lookback_bars + 1):]
    left_returns: list[float] = []
    right_returns: list[float] = []
    for previous, current in zip(common, common[1:]):
        lp, lc = left[previous], left[current]
        rp, rc = right[previous], right[current]
        if lp <= 0 or rp <= 0:
            continue
        left_returns.append((lc - lp) / lp)
        right_returns.append((rc - rp) / rp)

    observations = len(left_returns)
    if observations < min_observations:
        return None, observations

    left_mean = sum(left_returns) / observations
    right_mean = sum(right_returns) / observations
    left_delta = [value - left_mean for value in left_returns]
    right_delta = [value - right_mean for value in right_returns]
    left_var = sum(value * value for value in left_delta)
    right_var = sum(value * value for value in right_delta)
    if left_var <= 0 or right_var <= 0:
        return None, observations

    covariance = sum(
        l_value * r_value
        for l_value, r_value in zip(left_delta, right_delta)
    )
    coefficient = covariance / sqrt(left_var * right_var)
    return max(min(coefficient, 1.0), -1.0), observations


def correlation_checks(
    settings: Settings,
    candidate_symbol: str,
    exposure_symbols: list[str],
    market_bars: dict[str, list[dict[str, Any]]],
) -> tuple[bool, str, list[dict[str, Any]]]:
    candidate = candidate_symbol.upper()
    checks: list[dict[str, Any]] = []
    blocked_peer = ""
    blocked_correlation = -1.0

    for raw_peer in exposure_symbols:
        peer = raw_peer.upper()
        if not peer or peer == candidate:
            continue
        coefficient, observations = return_correlation(
            market_bars.get(candidate, []),
            market_bars.get(peer, []),
            lookback_bars=settings.correlation_lookback_bars,
            min_observations=settings.correlation_min_observations,
        )
        check = {
            "peer": peer,
            "correlation": None if coefficient is None else round(coefficient, 4),
            "observations": observations,
        }
        checks.append(check)
        if (
            coefficient is not None
            and coefficient >= float(settings.max_pairwise_correlation)
            and coefficient > blocked_correlation
        ):
            blocked_peer = peer
            blocked_correlation = coefficient

    if blocked_peer:
        return (
            False,
            f"correlation {blocked_correlation:.2f} with {blocked_peer} exceeds "
            f"{settings.max_pairwise_correlation}",
            checks,
        )
    return True, "correlation checks passed", checks
