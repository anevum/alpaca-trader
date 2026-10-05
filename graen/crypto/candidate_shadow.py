from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite, sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

from app.config import Settings
from app.crypto_layer import CryptoMarketDataClient
from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from graen.crypto.activity_shock_v9 import (
    BAR_MINUTES,
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    UNIVERSE as V9_UNIVERSE,
    _fills as v9_fills,
    _open as v9_open,
    build_series as build_v9_series,
    opportunity_at as v9_opportunity_at,
    spec_from_dict as v9_spec_from_dict,
)
from graen.crypto.trend_pullback_v10 import (
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    opportunity_at as v10_opportunity_at,
    spec_from_dict as v10_spec_from_dict,
)
from graen.crypto.btc_trend_pullback_v11 import (
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    UNIVERSE as V11_UNIVERSE,
    spec_from_dict as v11_spec_from_dict,
)
from graen.crypto.btc_mechanisms_v12 import (
    METHODOLOGY_VERSION as V12_METHODOLOGY_VERSION,
    UNIVERSE as V12_UNIVERSE,
    opportunity_at as v12_opportunity_at,
    spec_from_dict as v12_spec_from_dict,
)
from graen.crypto.btc_hypotheses_v13 import (
    METHODOLOGY_VERSION as V13_METHODOLOGY_VERSION,
    UNIVERSE as V13_UNIVERSE,
    opportunity_at as v13_opportunity_at,
    spec_from_dict as v13_spec_from_dict,
)
from graen.crypto.btc_slow_momentum_v14_r2f import (
    COST_SCENARIOS as V14_R2F_COST_SCENARIOS,
    LOOKBACK_DAYS as V14_R2F_LOOKBACK_DAYS,
    METHODOLOGY_VERSION as V14_R2F_METHODOLOGY_VERSION,
    UNIVERSE as V14_R2F_UNIVERSE,
    spec_from_dict as v14_r2f_spec_from_dict,
)
from graen.crypto.btc_consensus_trend_v14_r2g import (
    COST_SCENARIOS as V14_R2G_COST_SCENARIOS,
    METHODOLOGY_VERSION as V14_R2G_METHODOLOGY_VERSION,
    MOMENTUM_LOOKBACK_DAYS as V14_R2G_MOMENTUM_LOOKBACK_DAYS,
    SMA_WINDOW_DAYS as V14_R2G_SMA_WINDOW_DAYS,
    UNIVERSE as V14_R2G_UNIVERSE,
    spec_from_dict as v14_r2g_spec_from_dict,
)
from graen.crypto.btc_r2h_breakout_v15 import (
    COST_SCENARIOS as V15_COST_SCENARIOS,
    ENTRY_LOOKBACK_BARS as V15_ENTRY_LOOKBACK_BARS,
    EXIT_LOOKBACK_BARS as V15_EXIT_LOOKBACK_BARS,
    HARD_STOP_PCT as V15_HARD_STOP_PCT,
    METHODOLOGY_VERSION as V15_METHODOLOGY_VERSION,
    MOMENTUM_LOOKBACK_BARS as V15_MOMENTUM_LOOKBACK_BARS,
    SMA_WINDOW_BARS as V15_SMA_WINDOW_BARS,
    UNIVERSE as V15_UNIVERSE,
    spec_from_dict as v15_spec_from_dict,
)


UTC = timezone.utc
SHADOW_METHODOLOGY_VERSION = "graen-forward-shadow-v1"
SUPPORTED_CANDIDATE_METHODOLOGIES = {
    V9_METHODOLOGY_VERSION,
    V10_METHODOLOGY_VERSION,
    V11_METHODOLOGY_VERSION,
    V12_METHODOLOGY_VERSION,
    V13_METHODOLOGY_VERSION,
    V14_R2F_METHODOLOGY_VERSION,
    V14_R2G_METHODOLOGY_VERSION,
    V15_METHODOLOGY_VERSION,
}
MIN_READY_TRADES = 30
MIN_READY_DAYS = 20
MAX_REVIEW_TRADES = 60
MAX_REVIEW_DAYS = 30
DEPENDENCE_P_MAX = 0.05

R2F_SHADOW_COST_PER_TURNOVER = float(
    V14_R2F_COST_SCENARIOS["taker_stress_30bp"]
)
R2F_MIN_READY_DAILY_MARKS = 30
R2F_MIN_READY_EXPOSED_DAYS = 10
R2F_MAX_REVIEW_DAILY_MARKS = 120
R2F_MIN_READY_SHARPE = 0.0
R2F_MAX_READY_DRAWDOWN = -0.25

R2G_SHADOW_COST_PER_TURNOVER = float(
    V14_R2G_COST_SCENARIOS["taker_stress_30bp"]
)
R2G_MIN_READY_DAILY_MARKS = 30
R2G_MIN_READY_EXPOSED_DAYS = 10
R2G_MAX_REVIEW_DAILY_MARKS = 120
R2G_MIN_READY_SHARPE = 0.0
R2G_MAX_READY_DRAWDOWN = -0.25

V15_SHADOW_COST_PER_TURNOVER = float(
    V15_COST_SCENARIOS["taker_stress_30bp"]
)
V15_MIN_READY_4H_MARKS = 42
V15_MIN_READY_EXPOSED_BARS = 6
V15_MIN_READY_ENTRIES = 1
V15_MIN_READY_SHARPE = 0.0
V15_MAX_READY_DRAWDOWN = -0.10


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)



def _r2f_completed_daily_rows(
    source: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Never compress missing calendar days into daily momentum/evidence."""
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in source:
        try:
            stamp = _stamp(row.get("t"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("r2f_daily_timestamp_invalid") from exc
        end = stamp + timedelta(days=1)
        if end > now:
            continue  # In-progress bars are not evidence.
        try:
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError) as exc:
            raise ValueError("r2f_daily_close_invalid") from exc
        if not isfinite(close) or close <= 0:
            raise ValueError("r2f_daily_close_invalid")
        previous = by_stamp.get(stamp)
        if previous is not None and previous["close"] != close:
            raise ValueError("r2f_daily_duplicate_conflict")
        by_stamp[stamp] = {"timestamp": stamp, "bar_end": end, "close": close}
    rows = [by_stamp[key] for key in sorted(by_stamp)]
    if len(rows) < V14_R2F_LOOKBACK_DAYS + 2:
        raise ValueError("r2f_daily_history_incomplete")
    if any(
        following["timestamp"] != previous["bar_end"]
        for previous, following in zip(rows, rows[1:])
    ):
        raise ValueError("r2f_daily_calendar_gap")
    if now - rows[-1]["bar_end"] >= timedelta(days=1):
        raise ValueError("r2f_daily_data_stale")
    return rows


def _r2g_completed_daily_rows(
    source: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Require complete daily history for the R2G momentum/SMA comparison."""
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in source:
        try:
            stamp = _stamp(row.get("t"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("r2g_daily_timestamp_invalid") from exc
        end = stamp + timedelta(days=1)
        if end > now:
            continue
        try:
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError) as exc:
            raise ValueError("r2g_daily_close_invalid") from exc
        if not isfinite(close) or close <= 0:
            raise ValueError("r2g_daily_close_invalid")
        previous = by_stamp.get(stamp)
        if previous is not None and previous["close"] != close:
            raise ValueError("r2g_daily_duplicate_conflict")
        by_stamp[stamp] = {"timestamp": stamp, "bar_end": end, "close": close}
    rows = [by_stamp[key] for key in sorted(by_stamp)]
    required = max(
        V14_R2G_MOMENTUM_LOOKBACK_DAYS,
        V14_R2G_SMA_WINDOW_DAYS,
    ) + 2
    if len(rows) < required:
        raise ValueError("r2g_daily_history_incomplete")
    if any(
        following["timestamp"] != previous["bar_end"]
        for previous, following in zip(rows, rows[1:])
    ):
        raise ValueError("r2g_daily_calendar_gap")
    if now - rows[-1]["bar_end"] >= timedelta(days=1):
        raise ValueError("r2g_daily_data_stale")
    return rows



def _v15_completed_4h_rows(
    source: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Require a complete causal 4-hour BTC corpus for V15 shadow evidence."""
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in source:
        try:
            stamp = _stamp(row.get("t"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("v15_4h_timestamp_invalid") from exc
        end = stamp + timedelta(hours=4)
        if end > now:
            continue
        try:
            open_ = float(row.get("o", row.get("open")))
            high = float(row.get("h", row.get("high")))
            low = float(row.get("l", row.get("low")))
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError) as exc:
            raise ValueError("v15_4h_price_invalid") from exc
        if (
            not all(isfinite(value) and value > 0 for value in (open_, high, low, close))
            or high < low
            or high < max(open_, close)
            or low > min(open_, close)
        ):
            raise ValueError("v15_4h_price_invalid")
        normalized = {
            "timestamp": stamp,
            "bar_end": end,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
        previous = by_stamp.get(stamp)
        if previous is not None and previous != normalized:
            raise ValueError("v15_4h_duplicate_conflict")
        by_stamp[stamp] = normalized

    rows = [by_stamp[key] for key in sorted(by_stamp)]
    required = max(V15_MOMENTUM_LOOKBACK_BARS, V15_SMA_WINDOW_BARS) + 2
    if len(rows) < required:
        raise ValueError("v15_4h_history_incomplete")
    if any(
        following["timestamp"] != previous["bar_end"]
        for previous, following in zip(rows, rows[1:])
    ):
        raise ValueError("v15_4h_calendar_gap")
    if now - rows[-1]["bar_end"] >= timedelta(hours=4):
        raise ValueError("v15_4h_data_stale")
    return rows


def _bar_end(row: Mapping[str, Any]) -> datetime:
    return _stamp(row.get("t")) + timedelta(minutes=BAR_MINUTES)


def _latest_common_completed_end(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    symbols: Sequence[str],
    now: datetime,
) -> datetime | None:
    current = now.astimezone(UTC)
    common: set[datetime] | None = None
    for symbol in symbols:
        ends: set[datetime] = set()
        for row in bars_by_symbol.get(symbol, ()):
            try:
                end = _bar_end(row)
            except Exception:
                continue
            if end <= current:
                ends.add(end)
        if not ends:
            return None
        common = ends if common is None else common & ends
        if not common:
            return None
    return max(common) if common else None


@dataclass(slots=True)
class PendingEntry:
    symbol: str
    signal_at: datetime
    entry_bar_end: datetime
    hold_minutes: int
    signal: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal_at": self.signal_at.isoformat(),
            "entry_bar_end": self.entry_bar_end.isoformat(),
            "hold_minutes": self.hold_minutes,
            "signal": dict(self.signal),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PendingEntry":
        return cls(
            symbol=str(payload["symbol"]),
            signal_at=_stamp(payload["signal_at"]),
            entry_bar_end=_stamp(payload["entry_bar_end"]),
            hold_minutes=int(payload["hold_minutes"]),
            signal=dict(payload.get("signal") or {}),
        )


@dataclass(slots=True)
class ShadowPosition:
    symbol: str
    signal_at: datetime
    entry_bar_end: datetime
    entry_reference: float
    exit_bar_end: datetime
    hold_minutes: int
    signal: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal_at": self.signal_at.isoformat(),
            "entry_bar_end": self.entry_bar_end.isoformat(),
            "entry_reference": self.entry_reference,
            "exit_bar_end": self.exit_bar_end.isoformat(),
            "hold_minutes": self.hold_minutes,
            "signal": dict(self.signal),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShadowPosition":
        return cls(
            symbol=str(payload["symbol"]),
            signal_at=_stamp(payload["signal_at"]),
            entry_bar_end=_stamp(payload["entry_bar_end"]),
            entry_reference=float(payload["entry_reference"]),
            exit_bar_end=_stamp(payload["exit_bar_end"]),
            hold_minutes=int(payload["hold_minutes"]),
            signal=dict(payload.get("signal") or {}),
        )


class CandidateForwardShadow:
    """Broker-proof forward observer for GRAEN candidates that passed VELUM."""

    def __init__(self, settings: Settings):
        self.settings = settings.model_copy(
            update={
                "crypto_execution_enabled": False,
                "execution_enabled": False,
                "live_trading": False,
                "bot_armed": False,
                "scan_only": True,
                "market_data_batch_size": max(
                    len(V9_UNIVERSE),
                    int(settings.market_data_batch_size),
                ),
            }
        )
        self.market_data = CryptoMarketDataClient(self.settings)
        self.activation: dict[str, Any] | None = None
        self.pending: dict[str, PendingEntry] = {}
        self.positions: dict[str, ShadowPosition] = {}
        self.suppressed_until: dict[str, datetime] = {}
        self.closed: list[dict[str, Any]] = []
        self.last_processed_bar_end: datetime | None = None
        self.last_error: str | None = None
        self.last_checkpoint: dict[str, Any] | None = None
        self.last_checkpoint_status: str | None = None
        self.opportunity_count = 0
        self.entry_count = 0
        self.exit_count = 0
        self.r2f_daily_marks: list[dict[str, Any]] = []
        self.r2f_shadow_position = 0.0
        self.r2f_baseline_end: datetime | None = None
        self.r2g_daily_marks: list[dict[str, Any]] = []
        self.r2g_shadow_position = 0.0
        self.r2g_baseline_end: datetime | None = None
        self.v15_4h_marks: list[dict[str, Any]] = []
        self.v15_shadow_position = 0.0
        self.v15_entry_price: float | None = None
        self.v15_baseline_end: datetime | None = None

    @property
    def active(self) -> bool:
        return isinstance(self.activation, dict)

    @property
    def broker_orders_possible(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    def _candidate_methodology(self) -> str:
        return str((self.activation or {}).get("candidate_methodology") or "")

    def _candidate_spec(self) -> dict[str, Any]:
        payload = (self.activation or {}).get("candidate_spec")
        return dict(payload) if isinstance(payload, Mapping) else {}

    def _candidate_id(self) -> str:
        return str(self._candidate_spec().get("candidate_id") or "")

    def _activation_id(self) -> str:
        return str((self.activation or {}).get("activation_id") or "")

    def _symbols(self) -> tuple[str, ...]:
        methodology = self._candidate_methodology()
        if methodology == V14_R2F_METHODOLOGY_VERSION:
            return tuple(V14_R2F_UNIVERSE)
        if methodology == V14_R2G_METHODOLOGY_VERSION:
            return tuple(V14_R2G_UNIVERSE)
        if methodology == V15_METHODOLOGY_VERSION:
            return tuple(V15_UNIVERSE)
        if methodology == V13_METHODOLOGY_VERSION:
            return tuple(V13_UNIVERSE)
        if methodology == V12_METHODOLOGY_VERSION:
            return tuple(V12_UNIVERSE)
        if methodology == V11_METHODOLOGY_VERSION:
            return tuple(V11_UNIVERSE)
        if methodology in {V9_METHODOLOGY_VERSION, V10_METHODOLOGY_VERSION}:
            return tuple(V9_UNIVERSE)
        return ()

    def status(self) -> dict[str, Any]:
        return {
            "mode": "forward_shadow",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "active": self.active,
            "activation": dict(self.activation) if self.activation else None,
            "candidate_id": self._candidate_id() or None,
            "candidate_methodology": self._candidate_methodology() or None,
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "last_processed_bar_end": (
                self.last_processed_bar_end.isoformat()
                if self.last_processed_bar_end
                else None
            ),
            "pending": {
                symbol: row.to_dict()
                for symbol, row in sorted(self.pending.items())
            },
            "open_positions": {
                symbol: row.to_dict()
                for symbol, row in sorted(self.positions.items())
            },
            "opportunity_count": self.opportunity_count,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
            "closed_count": len(self.closed),
            "r2f_daily_mark_count": len(self.r2f_daily_marks),
            "r2f_shadow_position": self.r2f_shadow_position,
            "r2f_baseline_end": (
                self.r2f_baseline_end.isoformat()
                if self.r2f_baseline_end
                else None
            ),
            "r2g_daily_mark_count": len(self.r2g_daily_marks),
            "r2g_shadow_position": self.r2g_shadow_position,
            "r2g_baseline_end": (
                self.r2g_baseline_end.isoformat()
                if self.r2g_baseline_end
                else None
            ),
            "v15_4h_mark_count": len(self.v15_4h_marks),
            "v15_shadow_position": self.v15_shadow_position,
            "v15_entry_price": self.v15_entry_price,
            "v15_baseline_end": (
                self.v15_baseline_end.isoformat()
                if self.v15_baseline_end
                else None
            ),
            "last_checkpoint": dict(self.last_checkpoint or {}),
            "last_checkpoint_status": self.last_checkpoint_status,
            "last_error": self.last_error,
        }

    def activate(self, activation: Mapping[str, Any]) -> None:
        methodology = str(activation.get("candidate_methodology") or "")
        if methodology not in SUPPORTED_CANDIDATE_METHODOLOGIES:
            raise ValueError(f"unsupported_shadow_candidate_methodology:{methodology}")
        candidate_spec = activation.get("candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise ValueError("shadow_candidate_spec_missing")
        if methodology == V9_METHODOLOGY_VERSION:
            v9_spec_from_dict(candidate_spec)
        elif methodology == V10_METHODOLOGY_VERSION:
            v10_spec_from_dict(candidate_spec)
        elif methodology == V11_METHODOLOGY_VERSION:
            v11_spec_from_dict(candidate_spec)
        elif methodology == V12_METHODOLOGY_VERSION:
            v12_spec_from_dict(candidate_spec)
        elif methodology == V13_METHODOLOGY_VERSION:
            v13_spec_from_dict(candidate_spec)
        elif methodology == V14_R2F_METHODOLOGY_VERSION:
            v14_r2f_spec_from_dict(candidate_spec)
        elif methodology == V14_R2G_METHODOLOGY_VERSION:
            v14_r2g_spec_from_dict(candidate_spec)
        elif methodology == V15_METHODOLOGY_VERSION:
            v15_spec_from_dict(candidate_spec)

        activation_id = str(activation.get("activation_id") or "")
        if not activation_id:
            raise ValueError("shadow_activation_id_missing")
        if self._activation_id() == activation_id:
            return

        self.activation = dict(activation)
        self.pending.clear()
        self.positions.clear()
        self.suppressed_until.clear()
        self.closed.clear()
        self.last_processed_bar_end = None
        self.last_error = None
        self.last_checkpoint = None
        self.last_checkpoint_status = None
        self.opportunity_count = 0
        self.entry_count = 0
        self.exit_count = 0
        self.r2f_daily_marks.clear()
        self.r2f_shadow_position = 0.0
        self.r2f_baseline_end = None
        self.r2g_daily_marks.clear()
        self.r2g_shadow_position = 0.0
        self.r2g_baseline_end = None
        self.v15_4h_marks.clear()
        self.v15_shadow_position = 0.0
        self.v15_entry_price = None
        self.v15_baseline_end = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "graen.candidate_shadow.state.v1",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "activation": dict(self.activation) if self.activation else None,
            "activation_id": self._activation_id() or None,
            "candidate_id": self._candidate_id() or None,
            "candidate_methodology": self._candidate_methodology() or None,
            "last_processed_bar_end": (
                self.last_processed_bar_end.isoformat()
                if self.last_processed_bar_end
                else None
            ),
            "pending": {
                symbol: row.to_dict()
                for symbol, row in sorted(self.pending.items())
            },
            "positions": {
                symbol: row.to_dict()
                for symbol, row in sorted(self.positions.items())
            },
            "suppressed_until": {
                symbol: value.isoformat()
                for symbol, value in sorted(self.suppressed_until.items())
            },
            "closed": list(self.closed[-200:]),
            "opportunity_count": self.opportunity_count,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
            "r2f_daily_marks": list(self.r2f_daily_marks[-400:]),
            "r2f_shadow_position": self.r2f_shadow_position,
            "r2f_baseline_end": (
                self.r2f_baseline_end.isoformat()
                if self.r2f_baseline_end
                else None
            ),
            "r2g_daily_marks": list(self.r2g_daily_marks[-400:]),
            "r2g_shadow_position": self.r2g_shadow_position,
            "r2g_baseline_end": (
                self.r2g_baseline_end.isoformat()
                if self.r2g_baseline_end
                else None
            ),
            "v15_4h_marks": list(self.v15_4h_marks[-600:]),
            "v15_shadow_position": self.v15_shadow_position,
            "v15_entry_price": self.v15_entry_price,
            "v15_baseline_end": (
                self.v15_baseline_end.isoformat()
                if self.v15_baseline_end
                else None
            ),
            "last_checkpoint": dict(self.last_checkpoint or {}),
            "last_checkpoint_status": self.last_checkpoint_status,
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
        }

    def restore(self, payload: Mapping[str, Any]) -> None:
        activation = payload.get("activation")
        if not isinstance(activation, Mapping):
            return
        self.activate(activation)
        self.last_processed_bar_end = (
            _stamp(payload["last_processed_bar_end"])
            if payload.get("last_processed_bar_end")
            else None
        )
        self.pending = {
            str(symbol): PendingEntry.from_dict(row)
            for symbol, row in dict(payload.get("pending") or {}).items()
            if isinstance(row, Mapping)
        }
        self.positions = {
            str(symbol): ShadowPosition.from_dict(row)
            for symbol, row in dict(payload.get("positions") or {}).items()
            if isinstance(row, Mapping)
        }
        self.suppressed_until = {
            str(symbol): _stamp(value)
            for symbol, value in dict(payload.get("suppressed_until") or {}).items()
        }
        self.closed = [
            dict(row)
            for row in list(payload.get("closed") or [])[-200:]
            if isinstance(row, Mapping)
        ]
        self.opportunity_count = int(payload.get("opportunity_count") or 0)
        self.entry_count = int(payload.get("entry_count") or 0)
        self.exit_count = int(payload.get("exit_count") or 0)
        self.r2f_daily_marks = [
            dict(row)
            for row in list(payload.get("r2f_daily_marks") or [])[-400:]
            if isinstance(row, Mapping)
        ]
        self.r2f_shadow_position = float(
            payload.get("r2f_shadow_position") or 0.0
        )
        self.r2f_baseline_end = (
            _stamp(payload["r2f_baseline_end"])
            if payload.get("r2f_baseline_end")
            else None
        )
        self.r2g_daily_marks = [
            dict(row)
            for row in list(payload.get("r2g_daily_marks") or [])[-400:]
            if isinstance(row, Mapping)
        ]
        self.r2g_shadow_position = float(
            payload.get("r2g_shadow_position") or 0.0
        )
        self.r2g_baseline_end = (
            _stamp(payload["r2g_baseline_end"])
            if payload.get("r2g_baseline_end")
            else None
        )
        self.v15_4h_marks = [
            dict(row)
            for row in list(payload.get("v15_4h_marks") or [])[-600:]
            if isinstance(row, Mapping)
        ]
        self.v15_shadow_position = float(
            payload.get("v15_shadow_position") or 0.0
        )
        self.v15_entry_price = (
            float(payload["v15_entry_price"])
            if payload.get("v15_entry_price") is not None
            else None
        )
        self.v15_baseline_end = (
            _stamp(payload["v15_baseline_end"])
            if payload.get("v15_baseline_end")
            else None
        )
        checkpoint = payload.get("last_checkpoint")
        self.last_checkpoint = dict(checkpoint) if isinstance(checkpoint, Mapping) else None
        self.last_checkpoint_status = (
            str(payload.get("last_checkpoint_status"))
            if payload.get("last_checkpoint_status")
            else None
        )

    @staticmethod
    def _compound_returns(values: Sequence[float]) -> float:
        equity = 1.0
        for value in values:
            equity *= max(1.0 + float(value), 1e-12)
        return equity - 1.0

    @staticmethod
    def _max_drawdown(values: Sequence[float]) -> float:
        equity = 1.0
        peak = 1.0
        worst = 0.0
        for value in values:
            equity *= max(1.0 + float(value), 1e-12)
            peak = max(peak, equity)
            worst = min(worst, equity / peak - 1.0)
        return worst

    def _r2f_checkpoint(self) -> dict[str, Any]:
        marks = list(self.r2f_daily_marks)
        returns = [
            float(row.get("stressed_cost_net_return") or 0.0)
            for row in marks
            if isfinite(float(row.get("stressed_cost_net_return") or 0.0))
        ]
        exposed_days = sum(
            1 for row in marks if float(row.get("position") or 0.0) > 0.0
        )
        turnover_units = sum(
            float(row.get("turnover_units") or 0.0) for row in marks
        )
        total_return = self._compound_returns(returns)
        sigma = pstdev(returns) if len(returns) >= 2 else 0.0
        sharpe = (
            fmean(returns) / sigma * sqrt(365.0)
            if sigma > 1e-15
            else 0.0
        )
        max_drawdown = self._max_drawdown(returns)

        ready = bool(
            len(marks) >= R2F_MIN_READY_DAILY_MARKS
            and exposed_days >= R2F_MIN_READY_EXPOSED_DAYS
            and total_return > 0.0
            and sharpe > R2F_MIN_READY_SHARPE
            and max_drawdown > R2F_MAX_READY_DRAWDOWN
        )
        review_limit_reached = (
            len(marks) >= R2F_MAX_REVIEW_DAILY_MARKS
        )
        status = (
            "READY_FOR_HUMAN_REVIEW"
            if ready
            else "SHADOW_REJECTED"
            if review_limit_reached
            else "COLLECTING"
        )
        activation = self.activation or {}
        return {
            "schema_version": "graen.candidate_shadow.checkpoint.v1",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "activation_id": self._activation_id(),
            "candidate_id": self._candidate_id(),
            "candidate_methodology": self._candidate_methodology(),
            "evidence_phase": str(
                activation.get("evidence_phase") or "FORWARD_SHADOW"
            ),
            "status": status,
            "trade_count": self.entry_count + self.exit_count,
            "independent_day_blocks": len(marks),
            "fresh_daily_mark_count": len(marks),
            "exposed_day_count": exposed_days,
            "turnover_units": turnover_units,
            "cumulative_stressed_cost_return": total_return,
            "annualized_daily_sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "current_shadow_position": self.r2f_shadow_position,
            "fresh_evidence_after": str(
                activation.get("activated_at") or ""
            ),
            "baseline_bar_end": (
                self.r2f_baseline_end.isoformat()
                if self.r2f_baseline_end
                else None
            ),
            "ready_gate": {
                "min_fresh_daily_marks": R2F_MIN_READY_DAILY_MARKS,
                "min_exposed_days": R2F_MIN_READY_EXPOSED_DAYS,
                "cumulative_return_positive": True,
                "annualized_daily_sharpe_gt": R2F_MIN_READY_SHARPE,
                "max_drawdown_gt": R2F_MAX_READY_DRAWDOWN,
            },
            "terminal_rejection_gate": {
                "max_review_daily_marks": R2F_MAX_REVIEW_DAILY_MARKS,
            },
            "adaptive_historical_evidence_counts_as_fresh": False,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    def _r2g_checkpoint(self) -> dict[str, Any]:
        marks = list(self.r2g_daily_marks)
        returns = [
            float(row.get("stressed_cost_net_return") or 0.0)
            for row in marks
            if isfinite(float(row.get("stressed_cost_net_return") or 0.0))
        ]
        exposed_days = sum(
            1 for row in marks if float(row.get("position") or 0.0) > 0.0
        )
        turnover_units = sum(
            float(row.get("turnover_units") or 0.0) for row in marks
        )
        total_return = self._compound_returns(returns)
        sigma = pstdev(returns) if len(returns) >= 2 else 0.0
        sharpe = (
            fmean(returns) / sigma * sqrt(365.0)
            if sigma > 1e-15
            else 0.0
        )
        max_drawdown = self._max_drawdown(returns)
        ready = bool(
            len(marks) >= R2G_MIN_READY_DAILY_MARKS
            and exposed_days >= R2G_MIN_READY_EXPOSED_DAYS
            and total_return > 0.0
            and sharpe > R2G_MIN_READY_SHARPE
            and max_drawdown > R2G_MAX_READY_DRAWDOWN
        )
        review_limit_reached = len(marks) >= R2G_MAX_REVIEW_DAILY_MARKS
        status = (
            "READY_FOR_HUMAN_REVIEW"
            if ready
            else "SHADOW_REJECTED"
            if review_limit_reached
            else "COLLECTING"
        )
        activation = self.activation or {}
        return {
            "schema_version": "graen.candidate_shadow.checkpoint.v1",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "activation_id": self._activation_id(),
            "candidate_id": self._candidate_id(),
            "candidate_methodology": self._candidate_methodology(),
            "evidence_phase": str(
                activation.get("evidence_phase") or "FORWARD_SHADOW"
            ),
            "status": status,
            "trade_count": self.entry_count + self.exit_count,
            "independent_day_blocks": len(marks),
            "fresh_daily_mark_count": len(marks),
            "exposed_day_count": exposed_days,
            "turnover_units": turnover_units,
            "cumulative_stressed_cost_return": total_return,
            "annualized_daily_sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "current_shadow_position": self.r2g_shadow_position,
            "fresh_evidence_after": str(
                activation.get("activated_at") or ""
            ),
            "baseline_bar_end": (
                self.r2g_baseline_end.isoformat()
                if self.r2g_baseline_end
                else None
            ),
            "ready_gate": {
                "min_fresh_daily_marks": R2G_MIN_READY_DAILY_MARKS,
                "min_exposed_days": R2G_MIN_READY_EXPOSED_DAYS,
                "cumulative_return_positive": True,
                "annualized_daily_sharpe_gt": R2G_MIN_READY_SHARPE,
                "max_drawdown_gt": R2G_MAX_READY_DRAWDOWN,
            },
            "terminal_rejection_gate": {
                "max_review_daily_marks": R2G_MAX_REVIEW_DAILY_MARKS,
            },
            "comparison_against": "V14-R2F-BTC-MOM-180D",
            "adaptive_historical_evidence_counts_as_fresh": False,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    def _v15_checkpoint(self) -> dict[str, Any]:
        marks = list(self.v15_4h_marks)
        returns = [
            float(row.get("stressed_cost_net_return") or 0.0)
            for row in marks
            if isfinite(float(row.get("stressed_cost_net_return") or 0.0))
        ]
        exposed_bars = sum(
            1 for row in marks if float(row.get("position") or 0.0) > 0.0
        )
        turnover_units = sum(
            float(row.get("turnover_units") or 0.0) for row in marks
        )
        total_return = self._compound_returns(returns)
        sigma = pstdev(returns) if len(returns) >= 2 else 0.0
        sharpe = (
            fmean(returns) / sigma * sqrt(6.0 * 365.0)
            if sigma > 1e-15
            else 0.0
        )
        max_drawdown = self._max_drawdown(returns)
        ready = bool(
            len(marks) >= V15_MIN_READY_4H_MARKS
            and exposed_bars >= V15_MIN_READY_EXPOSED_BARS
            and self.entry_count >= V15_MIN_READY_ENTRIES
            and total_return > 0.0
            and sharpe > V15_MIN_READY_SHARPE
            and max_drawdown > V15_MAX_READY_DRAWDOWN
        )
        activation = self.activation or {}
        return {
            "schema_version": "graen.candidate_shadow.checkpoint.v1",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "activation_id": self._activation_id(),
            "candidate_id": self._candidate_id(),
            "candidate_methodology": self._candidate_methodology(),
            "evidence_phase": str(
                activation.get("evidence_phase") or "FORWARD_SHADOW"
            ),
            "status": "READY_FOR_HUMAN_REVIEW" if ready else "COLLECTING",
            "trade_count": self.entry_count + self.exit_count,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
            "fresh_4h_mark_count": len(marks),
            "exposed_4h_bar_count": exposed_bars,
            "turnover_units": turnover_units,
            "cumulative_stressed_cost_return": total_return,
            "annualized_4h_sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "current_shadow_position": self.v15_shadow_position,
            "current_entry_price": self.v15_entry_price,
            "fresh_evidence_after": str(
                activation.get("activated_at") or ""
            ),
            "baseline_bar_end": (
                self.v15_baseline_end.isoformat()
                if self.v15_baseline_end
                else None
            ),
            "ready_gate": {
                "min_fresh_4h_marks": V15_MIN_READY_4H_MARKS,
                "min_exposed_4h_bars": V15_MIN_READY_EXPOSED_BARS,
                "min_entries": V15_MIN_READY_ENTRIES,
                "cumulative_return_positive": True,
                "annualized_4h_sharpe_gt": V15_MIN_READY_SHARPE,
                "max_drawdown_gt": V15_MAX_READY_DRAWDOWN,
            },
            "historical_holdout_counts_as_fresh": False,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }


    def _checkpoint(self) -> dict[str, Any]:
        if self._candidate_methodology() == V14_R2F_METHODOLOGY_VERSION:
            return self._r2f_checkpoint()
        if self._candidate_methodology() == V14_R2G_METHODOLOGY_VERSION:
            return self._r2g_checkpoint()
        if self._candidate_methodology() == V15_METHODOLOGY_VERSION:
            return self._v15_checkpoint()
        rows = list(self.closed)
        returns = [
            float(row.get("stressed_cost_net_return") or 0.0)
            for row in rows
            if isfinite(float(row.get("stressed_cost_net_return") or 0.0))
        ]
        day_groups: dict[str, list[float]] = defaultdict(list)
        symbol_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            value = float(row.get("stressed_cost_net_return") or 0.0)
            exit_at = str(row.get("exit_at") or "")
            if exit_at:
                day_groups[exit_at[:10]].append(value)
            symbol = str(row.get("symbol") or "")
            if symbol:
                symbol_counts[symbol] += 1

        daily_means = [
            fmean(day_groups[key])
            for key in sorted(day_groups)
            if day_groups[key]
        ]
        gains = sum(value for value in returns if value > 0)
        losses = -sum(value for value in returns if value < 0)
        profit_factor = gains / losses if losses > 0 else None
        expectancy = fmean(returns) if returns else 0.0
        p_value = (
            float(
                _moving_block_null_pvalue(
                    daily_means,
                    replicates=2000,
                    seed=96001,
                )["p_value"]
            )
            if len(daily_means) >= 2
            else 1.0
        )
        concentration = (
            max(symbol_counts.values()) / len(rows)
            if rows and symbol_counts
            else 0.0
        )

        concentration_limit = float(getattr(self._active_spec(), "concentration_limit", 0.70))
        ready = bool(
            len(rows) >= MIN_READY_TRADES
            and len(daily_means) >= MIN_READY_DAYS
            and expectancy > 0
            and profit_factor is not None
            and profit_factor > 1.0
            and p_value <= DEPENDENCE_P_MAX
            and concentration <= concentration_limit
        )
        review_limit_reached = bool(
            len(rows) >= MAX_REVIEW_TRADES
            or len(daily_means) >= MAX_REVIEW_DAYS
        )
        status = (
            "READY_FOR_HUMAN_REVIEW"
            if ready
            else "SHADOW_REJECTED"
            if review_limit_reached
            else "COLLECTING"
        )
        return {
            "schema_version": "graen.candidate_shadow.checkpoint.v1",
            "shadow_methodology_version": SHADOW_METHODOLOGY_VERSION,
            "activation_id": self._activation_id(),
            "candidate_id": self._candidate_id(),
            "candidate_methodology": self._candidate_methodology(),
            "evidence_phase": str((self.activation or {}).get("evidence_phase") or "FORWARD_SHADOW"),
            "status": status,
            "trade_count": len(rows),
            "independent_day_blocks": len(daily_means),
            "expectancy_per_trade": expectancy,
            "profit_factor": profit_factor,
            "dependence_adjusted_p_value": p_value,
            "symbol_concentration_max_share": concentration,
            "ready_gate": {
                "min_trades": MIN_READY_TRADES,
                "min_independent_days": MIN_READY_DAYS,
                "expectancy_positive": True,
                "profit_factor_min": 1.0,
                "dependence_p_max": DEPENDENCE_P_MAX,
                "symbol_concentration_max_share": concentration_limit,
            },
            "terminal_rejection_gate": {
                "max_review_trades": MAX_REVIEW_TRADES,
                "max_review_days": MAX_REVIEW_DAYS,
            },
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    def _active_spec(self):
        methodology = self._candidate_methodology()
        if methodology == V9_METHODOLOGY_VERSION:
            return v9_spec_from_dict(self._candidate_spec())
        if methodology == V10_METHODOLOGY_VERSION:
            return v10_spec_from_dict(self._candidate_spec())
        if methodology == V11_METHODOLOGY_VERSION:
            return v11_spec_from_dict(self._candidate_spec())
        if methodology == V12_METHODOLOGY_VERSION:
            return v12_spec_from_dict(self._candidate_spec())
        if methodology == V13_METHODOLOGY_VERSION:
            return v13_spec_from_dict(self._candidate_spec())
        if methodology == V14_R2F_METHODOLOGY_VERSION:
            return v14_r2f_spec_from_dict(self._candidate_spec())
        if methodology == V14_R2G_METHODOLOGY_VERSION:
            return v14_r2g_spec_from_dict(self._candidate_spec())
        if methodology == V15_METHODOLOGY_VERSION:
            return v15_spec_from_dict(self._candidate_spec())
        raise RuntimeError(
            f"unsupported_shadow_candidate_methodology:{methodology}"
        )

    def _build_active_series(
        self,
        bars: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        latest_end: datetime,
    ):
        spec = self._active_spec()
        activity_hours = int(getattr(spec, "activity_lookback_hours", 0) or 0)
        slow_minutes = int(getattr(spec, "slow_minutes", 0) or 0)
        fast_minutes = int(getattr(spec, "fast_minutes", 0) or 0)
        warmup_hours = max(
            activity_hours + 2,
            (max(slow_minutes, fast_minutes) + 59) // 60 + 2,
            26,
        )
        return (
            spec,
            build_v9_series(
                bars,
                start=latest_end - timedelta(hours=warmup_hours),
                end=latest_end + timedelta(minutes=spec.hold_minutes + 10),
                warmup_hours=warmup_hours,
            ),
        )

    def _active_opportunity(
        self,
        series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
        spec: Any,
        symbol: str,
        stamp: datetime,
    ):
        methodology = self._candidate_methodology()
        if methodology == V9_METHODOLOGY_VERSION:
            return v9_opportunity_at(series, spec, symbol, stamp)
        if methodology == V13_METHODOLOGY_VERSION:
            return v13_opportunity_at(series, spec, symbol, stamp)
        if methodology == V12_METHODOLOGY_VERSION:
            return v12_opportunity_at(series, spec, symbol, stamp)
        if methodology in {V10_METHODOLOGY_VERSION, V11_METHODOLOGY_VERSION}:
            return v10_opportunity_at(series, spec, symbol, stamp)
        raise RuntimeError(
            f"unsupported_shadow_candidate_methodology:{methodology}"
        )

    async def _cycle_r2f(
        self,
        current: datetime,
    ) -> list[dict[str, Any]]:
        bars_by_symbol = await self.market_data.bars_many(
            ["BTC/USD"],
            timeframe="1Day",
            lookback_minutes=(V14_R2F_LOOKBACK_DAYS + 220) * 1440,
        )
        rows = _r2f_completed_daily_rows(
            bars_by_symbol.get("BTC/USD", ()), now=current
        )

        activation = self.activation or {}
        activated_at = _stamp(
            activation.get("activated_at") or current.isoformat()
        )
        events: list[dict[str, Any]] = []

        if self.last_processed_bar_end is None:
            baseline_index = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if row["bar_end"] > activated_at
                    and index >= V14_R2F_LOOKBACK_DAYS
                ),
                None,
            )
            if baseline_index is None:
                return []
            baseline = rows[baseline_index]
            baseline_end = baseline["bar_end"]
            assert isinstance(baseline_end, datetime)
            self.last_processed_bar_end = baseline_end
            self.r2f_baseline_end = baseline_end
            self.r2f_shadow_position = 0.0
            events.append({
                "event_type": "graen_candidate_shadow_baseline",
                "symbol": "BTC/USD",
                "occurred_at": baseline_end.isoformat(),
                "payload": {
                    "activation_id": self._activation_id(),
                    "candidate_id": self._candidate_id(),
                    "candidate_methodology": V14_R2F_METHODOLOGY_VERSION,
                    "baseline_bar_end": baseline_end.isoformat(),
                    "baseline_close": float(baseline["close"]),
                    "fresh_evidence_counted": False,
                    "reason": (
                        "first completed daily bar after activation is baseline "
                        "only; scoring begins with the following full day"
                    ),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            })

        last_end = self.last_processed_bar_end
        if last_end is None:
            return events

        first_unseen_index = next(
            (index for index, row in enumerate(rows) if row["bar_end"] > last_end),
            None,
        )
        if first_unseen_index is not None:
            if rows[first_unseen_index]["timestamp"] != last_end:
                raise ValueError("r2f_daily_resume_gap")
            if first_unseen_index < V14_R2F_LOOKBACK_DAYS + 1:
                raise ValueError("r2f_daily_resume_warmup_incomplete")

        for index, row in enumerate(rows):
            end = row["bar_end"]
            assert isinstance(end, datetime)
            if end <= last_end:
                continue
            if index < V14_R2F_LOOKBACK_DAYS + 1:
                continue

            prior_close = float(rows[index - 1]["close"])
            anchor_close = float(
                rows[index - 1 - V14_R2F_LOOKBACK_DAYS]["close"]
            )
            if prior_close <= 0 or anchor_close <= 0:
                continue
            trailing_return = prior_close / anchor_close - 1.0
            desired_position = 1.0 if trailing_return > 0.0 else 0.0
            turnover = abs(
                desired_position - self.r2f_shadow_position
            )
            if desired_position > self.r2f_shadow_position:
                self.entry_count += 1
            elif desired_position < self.r2f_shadow_position:
                self.exit_count += 1

            close = float(row["close"])
            asset_return = close / prior_close - 1.0
            gross_return = desired_position * asset_return
            net_return = (
                gross_return
                - turnover * R2F_SHADOW_COST_PER_TURNOVER
            )
            mark = {
                "activation_id": self._activation_id(),
                "candidate_id": self._candidate_id(),
                "candidate_methodology": V14_R2F_METHODOLOGY_VERSION,
                "symbol": "BTC/USD",
                "bar_start": row["timestamp"].isoformat(),
                "bar_end": end.isoformat(),
                "prior_close": prior_close,
                "close": close,
                "lookback_days": V14_R2F_LOOKBACK_DAYS,
                "trailing_momentum_return": trailing_return,
                "position": desired_position,
                "previous_position": self.r2f_shadow_position,
                "turnover_units": turnover,
                "stressed_cost_per_turnover": R2F_SHADOW_COST_PER_TURNOVER,
                "gross_return": gross_return,
                "stressed_cost_net_return": net_return,
                "fresh_evidence": True,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            self.r2f_daily_marks.append(mark)
            self.r2f_daily_marks = self.r2f_daily_marks[-400:]
            self.r2f_shadow_position = desired_position
            self.last_processed_bar_end = end
            last_end = end
            events.append({
                "event_type": "graen_candidate_shadow_daily_mark",
                "symbol": "BTC/USD",
                "occurred_at": end.isoformat(),
                "payload": dict(mark),
            })

        checkpoint = self._r2f_checkpoint()
        self.last_checkpoint_status = str(checkpoint["status"])
        self.last_checkpoint = dict(checkpoint)
        if events:
            events.append({
                "event_type": "graen_candidate_shadow_checkpoint",
                "symbol": "",
                "occurred_at": self.last_processed_bar_end.isoformat(),
                "payload": dict(checkpoint),
            })

        self.last_error = None
        return events


    async def _cycle_r2g(
        self,
        current: datetime,
    ) -> list[dict[str, Any]]:
        minimum_history = max(
            V14_R2G_MOMENTUM_LOOKBACK_DAYS,
            V14_R2G_SMA_WINDOW_DAYS,
        )
        bars_by_symbol = await self.market_data.bars_many(
            ["BTC/USD"],
            timeframe="1Day",
            lookback_minutes=(minimum_history + 220) * 1440,
        )
        rows = _r2g_completed_daily_rows(
            bars_by_symbol.get("BTC/USD", ()), now=current
        )
        activation = self.activation or {}
        activated_at = _stamp(
            activation.get("activated_at") or current.isoformat()
        )
        events: list[dict[str, Any]] = []

        if self.last_processed_bar_end is None:
            baseline_index = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if row["bar_end"] > activated_at
                    and index >= minimum_history
                ),
                None,
            )
            if baseline_index is None:
                return []
            baseline = rows[baseline_index]
            baseline_end = baseline["bar_end"]
            assert isinstance(baseline_end, datetime)
            self.last_processed_bar_end = baseline_end
            self.r2g_baseline_end = baseline_end
            self.r2g_shadow_position = 0.0
            events.append({
                "event_type": "graen_candidate_shadow_baseline",
                "symbol": "BTC/USD",
                "occurred_at": baseline_end.isoformat(),
                "payload": {
                    "activation_id": self._activation_id(),
                    "candidate_id": self._candidate_id(),
                    "candidate_methodology": V14_R2G_METHODOLOGY_VERSION,
                    "baseline_bar_end": baseline_end.isoformat(),
                    "baseline_close": float(baseline["close"]),
                    "fresh_evidence_counted": False,
                    "comparison_against": "V14-R2F-BTC-MOM-180D",
                    "reason": (
                        "first completed daily bar after R2G activation is "
                        "baseline only; comparison scoring begins with the "
                        "following full day"
                    ),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            })

        last_end = self.last_processed_bar_end
        if last_end is None:
            return events
        first_unseen_index = next(
            (index for index, row in enumerate(rows) if row["bar_end"] > last_end),
            None,
        )
        if first_unseen_index is not None:
            if rows[first_unseen_index]["timestamp"] != last_end:
                raise ValueError("r2g_daily_resume_gap")
            if first_unseen_index < minimum_history + 1:
                raise ValueError("r2g_daily_resume_warmup_incomplete")

        for index, row in enumerate(rows):
            end = row["bar_end"]
            assert isinstance(end, datetime)
            if end <= last_end:
                continue
            if index < minimum_history + 1:
                continue

            prior_index = index - 1
            prior_close = float(rows[prior_index]["close"])
            anchor_close = float(
                rows[
                    prior_index - V14_R2G_MOMENTUM_LOOKBACK_DAYS
                ]["close"]
            )
            sma_start = prior_index - V14_R2G_SMA_WINDOW_DAYS + 1
            sma_rows = rows[sma_start : prior_index + 1]
            if len(sma_rows) != V14_R2G_SMA_WINDOW_DAYS:
                raise ValueError("r2g_daily_sma_history_incomplete")
            sma_value = fmean(float(item["close"]) for item in sma_rows)
            trailing_return = prior_close / anchor_close - 1.0
            momentum_positive = trailing_return > 0.0
            above_sma = prior_close > sma_value
            desired_position = 1.0 if (momentum_positive or above_sma) else 0.0
            turnover = abs(
                desired_position - self.r2g_shadow_position
            )
            if desired_position > self.r2g_shadow_position:
                self.entry_count += 1
            elif desired_position < self.r2g_shadow_position:
                self.exit_count += 1

            close = float(row["close"])
            asset_return = close / prior_close - 1.0
            gross_return = desired_position * asset_return
            net_return = (
                gross_return
                - turnover * R2G_SHADOW_COST_PER_TURNOVER
            )
            mark = {
                "activation_id": self._activation_id(),
                "candidate_id": self._candidate_id(),
                "candidate_methodology": V14_R2G_METHODOLOGY_VERSION,
                "symbol": "BTC/USD",
                "bar_start": row["timestamp"].isoformat(),
                "bar_end": end.isoformat(),
                "prior_close": prior_close,
                "close": close,
                "momentum_lookback_days": V14_R2G_MOMENTUM_LOOKBACK_DAYS,
                "sma_window_days": V14_R2G_SMA_WINDOW_DAYS,
                "trailing_momentum_return": trailing_return,
                "sma_value": sma_value,
                "momentum_positive": momentum_positive,
                "above_sma": above_sma,
                "signal_rule": "OR",
                "position": desired_position,
                "previous_position": self.r2g_shadow_position,
                "turnover_units": turnover,
                "stressed_cost_per_turnover": R2G_SHADOW_COST_PER_TURNOVER,
                "gross_return": gross_return,
                "stressed_cost_net_return": net_return,
                "fresh_evidence": True,
                "comparison_against": "V14-R2F-BTC-MOM-180D",
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            self.r2g_daily_marks.append(mark)
            self.r2g_daily_marks = self.r2g_daily_marks[-400:]
            self.r2g_shadow_position = desired_position
            self.last_processed_bar_end = end
            last_end = end
            events.append({
                "event_type": "graen_candidate_shadow_daily_mark",
                "symbol": "BTC/USD",
                "occurred_at": end.isoformat(),
                "payload": dict(mark),
            })

        checkpoint = self._r2g_checkpoint()
        self.last_checkpoint_status = str(checkpoint["status"])
        self.last_checkpoint = dict(checkpoint)
        if events:
            events.append({
                "event_type": "graen_candidate_shadow_checkpoint",
                "symbol": "",
                "occurred_at": self.last_processed_bar_end.isoformat(),
                "payload": dict(checkpoint),
            })
        self.last_error = None
        return events


    async def _cycle_v15(
        self,
        current: datetime,
    ) -> list[dict[str, Any]]:
        minimum_history = max(
            V15_MOMENTUM_LOOKBACK_BARS,
            V15_SMA_WINDOW_BARS,
        )
        bars_by_symbol = await self.market_data.bars_many(
            ["BTC/USD"],
            timeframe="4Hour",
            lookback_minutes=(minimum_history + 220) * 240,
        )
        rows = _v15_completed_4h_rows(
            bars_by_symbol.get("BTC/USD", ()),
            now=current,
        )
        activation = self.activation or {}
        activated_at = _stamp(
            activation.get("activated_at") or current.isoformat()
        )
        events: list[dict[str, Any]] = []

        if self.last_processed_bar_end is None:
            baseline_index = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if row["bar_end"] > activated_at
                    and index >= minimum_history
                ),
                None,
            )
            if baseline_index is None:
                return []
            baseline = rows[baseline_index]
            baseline_end = baseline["bar_end"]
            assert isinstance(baseline_end, datetime)
            self.last_processed_bar_end = baseline_end
            self.v15_baseline_end = baseline_end
            self.v15_shadow_position = 0.0
            self.v15_entry_price = None
            events.append({
                "event_type": "graen_candidate_shadow_baseline",
                "symbol": "BTC/USD",
                "occurred_at": baseline_end.isoformat(),
                "payload": {
                    "activation_id": self._activation_id(),
                    "candidate_id": self._candidate_id(),
                    "candidate_methodology": V15_METHODOLOGY_VERSION,
                    "baseline_bar_end": baseline_end.isoformat(),
                    "baseline_close": float(baseline["close"]),
                    "fresh_evidence_counted": False,
                    "reason": (
                        "first completed 4-hour bar after V15 activation is "
                        "baseline only; shadow starts flat and never backfills "
                        "a pre-activation position"
                    ),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            })

        last_end = self.last_processed_bar_end
        if last_end is None:
            return events
        first_unseen_index = next(
            (index for index, row in enumerate(rows) if row["bar_end"] > last_end),
            None,
        )
        if first_unseen_index is not None:
            if rows[first_unseen_index]["timestamp"] != last_end:
                raise ValueError("v15_4h_resume_gap")
            if first_unseen_index < minimum_history + 1:
                raise ValueError("v15_4h_resume_warmup_incomplete")

        for index, row in enumerate(rows):
            end = row["bar_end"]
            assert isinstance(end, datetime)
            if end <= last_end:
                continue
            if index < minimum_history + 1:
                continue

            signal_index = index - 1
            prior_close = float(rows[signal_index]["close"])
            momentum_anchor = float(
                rows[signal_index - V15_MOMENTUM_LOOKBACK_BARS]["close"]
            )
            sma_start = signal_index - V15_SMA_WINDOW_BARS + 1
            sma_rows = rows[sma_start : signal_index + 1]
            if len(sma_rows) != V15_SMA_WINDOW_BARS:
                raise ValueError("v15_4h_sma_history_incomplete")
            sma_value = fmean(float(item["close"]) for item in sma_rows)
            trailing_return = prior_close / momentum_anchor - 1.0
            momentum_positive = trailing_return > 0.0
            above_sma = prior_close > sma_value
            regime_long = bool(momentum_positive or above_sma)

            entry_channel = rows[
                signal_index - V15_ENTRY_LOOKBACK_BARS : signal_index
            ]
            exit_channel = rows[
                signal_index - V15_EXIT_LOOKBACK_BARS : signal_index
            ]
            if (
                len(entry_channel) != V15_ENTRY_LOOKBACK_BARS
                or len(exit_channel) != V15_EXIT_LOOKBACK_BARS
            ):
                raise ValueError("v15_4h_channel_history_incomplete")
            entry_high = max(float(item["high"]) for item in entry_channel)
            exit_low = min(float(item["low"]) for item in exit_channel)
            breakout = prior_close > entry_high
            breakdown = prior_close < exit_low

            previous_position = self.v15_shadow_position
            previous_entry_price = self.v15_entry_price
            current_open = float(row["open"])
            current_low = float(row["low"])
            current_close = float(row["close"])
            position = previous_position
            entry_price = previous_entry_price
            turnover = 0.0
            net_return = 0.0
            entry_fired = False
            exit_fired = False
            exit_reason: str | None = None
            exit_reference: float | None = None

            if previous_position > 0.0 and (not regime_long or breakdown):
                exit_reference = current_open
                net_return = (
                    exit_reference / prior_close - 1.0
                    - V15_SHADOW_COST_PER_TURNOVER
                )
                position = 0.0
                entry_price = None
                turnover = 1.0
                exit_fired = True
                exit_reason = "regime" if not regime_long else "channel"
                self.exit_count += 1
            elif previous_position <= 0.0 and regime_long and breakout:
                entry_fired = True
                position = 1.0
                entry_price = current_open
                turnover = 1.0
                self.entry_count += 1
                net_return = -V15_SHADOW_COST_PER_TURNOVER
                stop_price = entry_price * (1.0 - V15_HARD_STOP_PCT)
                if current_low <= stop_price:
                    exit_reference = min(stop_price, current_open)
                    net_return += (
                        exit_reference / entry_price - 1.0
                        - V15_SHADOW_COST_PER_TURNOVER
                    )
                    position = 0.0
                    entry_price = None
                    turnover = 2.0
                    exit_fired = True
                    exit_reason = "same_bar_stop"
                    self.exit_count += 1
                else:
                    net_return += current_close / entry_price - 1.0
            elif previous_position > 0.0:
                if previous_entry_price is None:
                    raise ValueError("v15_shadow_entry_price_missing")
                stop_price = previous_entry_price * (1.0 - V15_HARD_STOP_PCT)
                if current_low <= stop_price:
                    exit_reference = min(stop_price, current_open)
                    net_return = (
                        exit_reference / prior_close - 1.0
                        - V15_SHADOW_COST_PER_TURNOVER
                    )
                    position = 0.0
                    entry_price = None
                    turnover = 1.0
                    exit_fired = True
                    exit_reason = "hard_stop"
                    self.exit_count += 1
                else:
                    net_return = current_close / prior_close - 1.0

            mark = {
                "activation_id": self._activation_id(),
                "candidate_id": self._candidate_id(),
                "candidate_methodology": V15_METHODOLOGY_VERSION,
                "symbol": "BTC/USD",
                "bar_start": row["timestamp"].isoformat(),
                "bar_end": end.isoformat(),
                "prior_close": prior_close,
                "open": current_open,
                "low": current_low,
                "close": current_close,
                "momentum_lookback_bars": V15_MOMENTUM_LOOKBACK_BARS,
                "sma_window_bars": V15_SMA_WINDOW_BARS,
                "entry_lookback_bars": V15_ENTRY_LOOKBACK_BARS,
                "exit_lookback_bars": V15_EXIT_LOOKBACK_BARS,
                "trailing_momentum_return": trailing_return,
                "sma_value": sma_value,
                "momentum_positive": momentum_positive,
                "above_sma": above_sma,
                "regime_long": regime_long,
                "entry_channel_high": entry_high,
                "exit_channel_low": exit_low,
                "breakout": breakout,
                "breakdown": breakdown,
                "previous_position": previous_position,
                "position": position,
                "entry_fired": entry_fired,
                "exit_fired": exit_fired,
                "exit_reason": exit_reason,
                "entry_price": entry_price,
                "exit_reference": exit_reference,
                "turnover_units": turnover,
                "stressed_cost_per_turnover": V15_SHADOW_COST_PER_TURNOVER,
                "stressed_cost_net_return": net_return,
                "fresh_evidence": True,
                "execution_model": "prior_completed_signal_next_4h_open",
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            self.v15_4h_marks.append(mark)
            self.v15_4h_marks = self.v15_4h_marks[-600:]
            self.v15_shadow_position = position
            self.v15_entry_price = entry_price
            self.last_processed_bar_end = end
            last_end = end

            events.append({
                "event_type": "graen_candidate_shadow_4h_mark",
                "symbol": "BTC/USD",
                "occurred_at": end.isoformat(),
                "payload": dict(mark),
            })
            if entry_fired:
                events.append({
                    "event_type": "graen_candidate_shadow_entry",
                    "symbol": "BTC/USD",
                    "occurred_at": row["timestamp"].isoformat(),
                    "payload": {
                        **dict(mark),
                        "entry_reference": current_open,
                        "mode": "forward_shadow",
                    },
                })
            if exit_fired:
                events.append({
                    "event_type": "graen_candidate_shadow_exit",
                    "symbol": "BTC/USD",
                    "occurred_at": row["timestamp"].isoformat(),
                    "payload": {
                        **dict(mark),
                        "mode": "forward_shadow",
                    },
                })

        checkpoint = self._v15_checkpoint()
        self.last_checkpoint_status = str(checkpoint["status"])
        self.last_checkpoint = dict(checkpoint)
        if events:
            events.append({
                "event_type": "graen_candidate_shadow_checkpoint",
                "symbol": "",
                "occurred_at": self.last_processed_bar_end.isoformat(),
                "payload": dict(checkpoint),
            })
        self.last_error = None
        return events


    async def cycle(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not self.active:
            return []
        current = (now or datetime.now(UTC)).astimezone(UTC)
        methodology = self._candidate_methodology()
        if methodology not in SUPPORTED_CANDIDATE_METHODOLOGIES:
            raise RuntimeError(
                f"unsupported_shadow_candidate_methodology:{methodology}"
            )
        if methodology == V14_R2F_METHODOLOGY_VERSION:
            return await self._cycle_r2f(current)
        if methodology == V14_R2G_METHODOLOGY_VERSION:
            return await self._cycle_r2g(current)
        if methodology == V15_METHODOLOGY_VERSION:
            return await self._cycle_v15(current)

        spec = self._active_spec()
        activity_minutes = int(getattr(spec, "activity_lookback_hours", 0) or 0) * 60
        slow_minutes = int(getattr(spec, "slow_minutes", 0) or 0)
        fast_minutes = int(getattr(spec, "fast_minutes", 0) or 0)
        lookback_minutes = max(
            activity_minutes + 180,
            slow_minutes + 180,
            fast_minutes + 180,
            spec.hold_minutes + 180,
            26 * 60,
        )
        symbols = self._symbols()
        bars = await self.market_data.bars_many(
            list(symbols),
            timeframe="5Min",
            lookback_minutes=lookback_minutes,
        )
        latest_end = _latest_common_completed_end(
            bars,
            symbols=symbols,
            now=current,
        )
        if latest_end is None or latest_end == self.last_processed_bar_end:
            return []

        spec, series = self._build_active_series(bars, latest_end=latest_end)
        events: list[dict[str, Any]] = []

        for symbol, position in list(self.positions.items()):
            if latest_end < position.exit_bar_end:
                continue
            exit_reference = v9_open(
                series,
                symbol,
                position.exit_bar_end,
            )
            if exit_reference is None:
                continue
            entry_fill, exit_fill = v9_fills(
                symbol,
                position.entry_reference,
                exit_reference,
                "high",
            )
            net_return = exit_fill / entry_fill - 1.0
            closed = {
                "activation_id": self._activation_id(),
                "candidate_id": self._candidate_id(),
                "candidate_methodology": methodology,
                "symbol": symbol,
                "signal_at": position.signal_at.isoformat(),
                "entry_at": position.entry_bar_end.isoformat(),
                "exit_at": position.exit_bar_end.isoformat(),
                "entry_reference": position.entry_reference,
                "exit_reference": exit_reference,
                "stressed_cost_net_return": net_return,
                "hold_minutes": position.hold_minutes,
                "signal": dict(position.signal),
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            self.closed.append(closed)
            self.closed = self.closed[-200:]
            self.exit_count += 1
            del self.positions[symbol]
            events.append({
                "event_type": "graen_candidate_shadow_exit",
                "symbol": symbol,
                "occurred_at": position.exit_bar_end.isoformat(),
                "payload": closed,
            })

        for symbol, pending in list(self.pending.items()):
            if latest_end < pending.entry_bar_end:
                continue
            entry_reference = v9_open(
                series,
                symbol,
                pending.entry_bar_end,
            )
            if entry_reference is None:
                continue
            exit_bar_end = (
                pending.signal_at
                + timedelta(minutes=pending.hold_minutes + BAR_MINUTES)
            )
            position = ShadowPosition(
                symbol=symbol,
                signal_at=pending.signal_at,
                entry_bar_end=pending.entry_bar_end,
                entry_reference=entry_reference,
                exit_bar_end=exit_bar_end,
                hold_minutes=pending.hold_minutes,
                signal=dict(pending.signal),
            )
            self.positions[symbol] = position
            del self.pending[symbol]
            self.entry_count += 1
            events.append({
                "event_type": "graen_candidate_shadow_entry",
                "symbol": symbol,
                "occurred_at": pending.entry_bar_end.isoformat(),
                "payload": {
                    "activation_id": self._activation_id(),
                    "candidate_id": self._candidate_id(),
                    "candidate_methodology": methodology,
                    **position.to_dict(),
                    "mode": "forward_shadow",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            })

        for symbol in symbols:
            if symbol in self.pending or symbol in self.positions:
                continue
            if self.suppressed_until.get(
                symbol,
                datetime.min.replace(tzinfo=UTC),
            ) > latest_end:
                continue
            opportunity = self._active_opportunity(
                series,
                spec,
                symbol,
                latest_end,
            )
            if opportunity is None:
                continue
            pending = PendingEntry(
                symbol=symbol,
                signal_at=opportunity.opportunity_at,
                entry_bar_end=opportunity.opportunity_at
                + timedelta(minutes=BAR_MINUTES),
                hold_minutes=opportunity.hold_minutes,
                signal=dict(opportunity.signal),
            )
            self.pending[symbol] = pending
            self.suppressed_until[symbol] = (
                opportunity.opportunity_at
                + timedelta(minutes=spec.cooldown_minutes)
            )
            self.opportunity_count += 1
            events.append({
                "event_type": "graen_candidate_shadow_opportunity",
                "symbol": symbol,
                "occurred_at": opportunity.opportunity_at.isoformat(),
                "payload": {
                    "activation_id": self._activation_id(),
                    "candidate_id": self._candidate_id(),
                    "candidate_methodology": methodology,
                    "opportunity": opportunity.to_dict(),
                    "mode": "forward_shadow",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            })

        self.last_processed_bar_end = latest_end
        checkpoint = self._checkpoint()
        if checkpoint["status"] != self.last_checkpoint_status:
            self.last_checkpoint_status = str(checkpoint["status"])
            self.last_checkpoint = dict(checkpoint)
            events.append({
                "event_type": "graen_candidate_shadow_checkpoint",
                "symbol": "",
                "occurred_at": latest_end.isoformat(),
                "payload": dict(checkpoint),
            })
        else:
            self.last_checkpoint = dict(checkpoint)

        self.last_error = None
        return events
