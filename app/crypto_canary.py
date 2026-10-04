from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .strategy import Signal

UTC = timezone.utc

BTC_CANARY_STRATEGY_VERSION_ID = "BTC-CANARY-001"
BTC_CANARY_SOURCE_CANDIDATE_ID = "V14-R2H-BTC-4H-CONSENSUS-1080-1500"
BTC_CANARY_FAMILY = "btc_4h_momentum_or_sma_consensus_experimental_canary"
BTC_CANARY_SYMBOL = "BTC/USD"
BTC_CANARY_TIMEFRAME = "4Hour"
BTC_CANARY_BAR_HOURS = 4
BTC_CANARY_MOMENTUM_BARS = 180 * 6
BTC_CANARY_SMA_BARS = 250 * 6


@dataclass(frozen=True, slots=True)
class BtcCanaryState:
    available: bool
    desired_long: bool
    signal_bar_at: datetime | None
    signal_close: Decimal
    momentum_return: Decimal | None
    sma: Decimal | None
    completed_bar_count: int
    reason: str

    def metadata(self) -> dict[str, Any]:
        return {
            "experimental_canary": True,
            "execution_class": "EXPERIMENTAL_LIVE",
            "strategy_version_id": BTC_CANARY_STRATEGY_VERSION_ID,
            "strategy_family": BTC_CANARY_FAMILY,
            "source_candidate_id": BTC_CANARY_SOURCE_CANDIDATE_ID,
            "bar_timeframe": BTC_CANARY_TIMEFRAME,
            "signal_bar_at": self.signal_bar_at.isoformat() if self.signal_bar_at else None,
            "signal_close": str(self.signal_close),
            "momentum_lookback_bars": BTC_CANARY_MOMENTUM_BARS,
            "sma_window_bars": BTC_CANARY_SMA_BARS,
            "momentum_return": (
                str(self.momentum_return) if self.momentum_return is not None else None
            ),
            "sma": str(self.sma) if self.sma is not None else None,
            "desired_long": self.desired_long,
            "completed_bar_count": self.completed_bar_count,
            "research_status": "NOT_PROMOTED",
            "independent_validation": False,
        }


def _stamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def completed_4h_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    now_utc = now.astimezone(UTC)
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        raw_stamp = row.get("t", row.get("timestamp"))
        if raw_stamp is None:
            continue
        try:
            stamp = _stamp(raw_stamp)
            close = Decimal(str(row.get("c", row.get("close"))))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if close <= 0:
            continue
        # Alpaca timestamps bars at their start. Never use the currently forming bar.
        if stamp + timedelta(hours=BTC_CANARY_BAR_HOURS) > now_utc:
            continue
        by_stamp[stamp] = {"timestamp": stamp, "close": close}
    return [by_stamp[key] for key in sorted(by_stamp)]


def evaluate_btc_canary_state(
    rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> BtcCanaryState:
    bars = completed_4h_bars(rows, now=now)
    minimum = max(BTC_CANARY_MOMENTUM_BARS + 1, BTC_CANARY_SMA_BARS)
    if len(bars) < minimum:
        return BtcCanaryState(
            available=False,
            desired_long=False,
            signal_bar_at=(bars[-1]["timestamp"] if bars else None),
            signal_close=(bars[-1]["close"] if bars else Decimal("0")),
            momentum_return=None,
            sma=None,
            completed_bar_count=len(bars),
            reason=f"canary warmup incomplete: {len(bars)}/{minimum} completed 4h bars",
        )

    latest = bars[-1]
    signal_close = Decimal(latest["close"])
    anchor_close = Decimal(bars[-1 - BTC_CANARY_MOMENTUM_BARS]["close"])
    momentum_return = signal_close / anchor_close - Decimal("1")
    sma_slice = bars[-BTC_CANARY_SMA_BARS:]
    sma = sum((Decimal(row["close"]) for row in sma_slice), Decimal("0")) / Decimal(
        BTC_CANARY_SMA_BARS
    )
    momentum_positive = momentum_return > 0
    above_sma = signal_close > sma
    desired_long = bool(momentum_positive or above_sma)
    return BtcCanaryState(
        available=True,
        desired_long=desired_long,
        signal_bar_at=latest["timestamp"],
        signal_close=signal_close,
        momentum_return=momentum_return,
        sma=sma,
        completed_bar_count=len(bars),
        reason=(
            "R2H frozen 4h consensus is long"
            if desired_long
            else "R2H frozen 4h consensus is flat"
        ),
    )


def signal_from_canary_state(
    state: BtcCanaryState,
    *,
    reference_price: Decimal,
    order_notional: Decimal,
    stop_pct: Decimal,
) -> Signal:
    metadata = {
        "market": "crypto",
        "market_lane": "crypto",
        "session_model": "24x7",
        **state.metadata(),
    }
    if not state.available:
        return Signal(
            action="hold",
            symbol=BTC_CANARY_SYMBOL,
            reason=state.reason,
            metadata=metadata,
        )
    if not state.desired_long:
        return Signal(
            action="hold",
            symbol=BTC_CANARY_SYMBOL,
            reference_price=reference_price,
            reason=state.reason,
            metadata=metadata,
        )
    if reference_price <= 0:
        return Signal(
            action="hold",
            symbol=BTC_CANARY_SYMBOL,
            reason="canary current reference price unavailable",
            metadata=metadata,
        )

    stop_price = reference_price * (Decimal("1") - stop_pct)
    return Signal(
        action="buy",
        symbol=BTC_CANARY_SYMBOL,
        notional=order_notional,
        reference_price=reference_price,
        stop_price=stop_price,
        take_profit_price=Decimal("0"),
        reason="BTC-CANARY-001 frozen R2H consensus requests long exposure",
        metadata=metadata,
    )
