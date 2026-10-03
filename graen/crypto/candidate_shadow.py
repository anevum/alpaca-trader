from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import fmean
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


UTC = timezone.utc
SHADOW_METHODOLOGY_VERSION = "graen-forward-shadow-v1"
SUPPORTED_CANDIDATE_METHODOLOGIES = {
    V9_METHODOLOGY_VERSION,
    V10_METHODOLOGY_VERSION,
    V11_METHODOLOGY_VERSION,
    V12_METHODOLOGY_VERSION,
}
MIN_READY_TRADES = 30
MIN_READY_DAYS = 20
MAX_REVIEW_TRADES = 60
MAX_REVIEW_DAYS = 30
DEPENDENCE_P_MAX = 0.05


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
        checkpoint = payload.get("last_checkpoint")
        self.last_checkpoint = dict(checkpoint) if isinstance(checkpoint, Mapping) else None
        self.last_checkpoint_status = (
            str(payload.get("last_checkpoint_status"))
            if payload.get("last_checkpoint_status")
            else None
        )

    def _checkpoint(self) -> dict[str, Any]:
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
        warmup_hours = max(spec.activity_lookback_hours + 2, 26)
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
        if methodology == V12_METHODOLOGY_VERSION:
            return v12_opportunity_at(series, spec, symbol, stamp)
        if methodology in {V10_METHODOLOGY_VERSION, V11_METHODOLOGY_VERSION}:
            return v10_opportunity_at(series, spec, symbol, stamp)
        raise RuntimeError(
            f"unsupported_shadow_candidate_methodology:{methodology}"
        )

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

        spec = self._active_spec()
        lookback_minutes = max(
            spec.activity_lookback_hours * 60 + 180,
            spec.hold_minutes + 180,
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

        for symbol in V9_UNIVERSE:
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
