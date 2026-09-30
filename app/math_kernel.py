from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from math import log, sqrt
from statistics import fmean
from typing import Iterable, Sequence


D = Decimal


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def arithmetic_return(start, end) -> Decimal:
    a, b = _d(start), _d(end)
    if a <= 0:
        return D("0")
    return (b / a) - D("1")


def log_return(start, end) -> float:
    a, b = float(start), float(end)
    if a <= 0 or b <= 0:
        return 0.0
    return log(b / a)


def rolling_return(values: Sequence, periods: int) -> Decimal:
    if periods <= 0 or len(values) <= periods:
        return D("0")
    return arithmetic_return(values[-periods - 1], values[-1])


def rolling_momentum(values: Sequence, periods: int) -> Decimal:
    return rolling_return(values, periods)


def rolling_realized_volatility(values: Sequence, periods: int) -> float:
    if periods <= 1 or len(values) <= periods:
        return 0.0
    window = values[-periods - 1 :]
    returns = [log_return(window[i - 1], window[i]) for i in range(1, len(window))]
    if len(returns) < 2:
        return 0.0
    mean = fmean(returns)
    variance = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    return sqrt(max(variance, 0.0))


def volatility_normalized_momentum(values: Sequence, momentum_periods: int, volatility_periods: int) -> float:
    momentum = float(rolling_momentum(values, momentum_periods))
    vol = rolling_realized_volatility(values, volatility_periods)
    return momentum / vol if vol > 0 else 0.0


def spread_bps(bid, ask) -> Decimal:
    b, a = _d(bid), _d(ask)
    if b <= 0 or a <= 0 or a < b:
        return D("0")
    midpoint = (a + b) / D("2")
    return ((a - b) / midpoint) * D("10000") if midpoint > 0 else D("0")


def normalized_spread(spread, reference_spread) -> Decimal:
    s, r = _d(spread), _d(reference_spread)
    return s / r if r > 0 else D("0")


def drawdown(values: Sequence) -> Decimal:
    if not values:
        return D("0")
    peak = _d(values[0])
    worst = D("0")
    for value in values:
        current = _d(value)
        peak = max(peak, current)
        if peak > 0:
            worst = min(worst, (current / peak) - D("1"))
    return worst


def mfe(reference, highs: Iterable) -> Decimal:
    ref = _d(reference)
    vals = [_d(v) for v in highs]
    return (max(vals) / ref) - D("1") if ref > 0 and vals else D("0")


def mae(reference, lows: Iterable) -> Decimal:
    ref = _d(reference)
    vals = [_d(v) for v in lows]
    return (min(vals) / ref) - D("1") if ref > 0 and vals else D("0")


def forward_return(reference, future) -> Decimal:
    return arithmetic_return(reference, future)


def pearson_correlation(left: Sequence, right: Sequence) -> float:
    n = min(len(left), len(right))
    if n < 2:
        return 0.0
    x = [float(v) for v in left[-n:]]
    y = [float(v) for v in right[-n:]]
    mx, my = fmean(x), fmean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    return num / sqrt(dx * dy) if dx > 0 and dy > 0 else 0.0


def rank_descending(values: Sequence[float]) -> tuple[int, ...]:
    return tuple(sorted(range(len(values)), key=lambda i: (-float(values[i]), i)))


@dataclass(frozen=True, slots=True)
class EvidenceProbability:
    score: float | None
    probability: float | None
    calibration_version: str
    calibrated: bool
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)
