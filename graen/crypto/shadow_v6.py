from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.crypto_layer import CryptoMarketDataClient
from app.config import Settings
from .research_v6 import (
    CONTEXT_UNIVERSE,
    EXECUTION_UNIVERSE,
    Opportunity,
    _fill_prices,
    _open,
    build_series,
    opportunity_state,
    reclaim_at,
)


LOOKBACK_MINUTES = 600
BAR_MINUTES = 5
HOLD_MINUTES = 120


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bar_end(row: Mapping[str, Any]) -> datetime:
    return _stamp(row.get("t")) + timedelta(minutes=BAR_MINUTES)


def _latest_common_completed_end(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    now: datetime,
) -> datetime | None:
    now = now.astimezone(timezone.utc)
    common: set[datetime] | None = None
    for symbol in CONTEXT_UNIVERSE:
        ends = {
            _bar_end(row)
            for row in bars_by_symbol.get(symbol, ())
            if _bar_end(row) <= now
        }
        if not ends:
            return None
        common = ends if common is None else common & ends
        if not common:
            return None
    return max(common) if common else None


@dataclass(slots=True)
class ShadowPosition:
    symbol: str
    opportunity_at: datetime
    confirmation_at: datetime
    entry_reference: float
    exit_at: datetime
    snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "opportunity_at": self.opportunity_at.isoformat(),
            "confirmation_at": self.confirmation_at.isoformat(),
            "entry_reference": self.entry_reference,
            "exit_at": self.exit_at.isoformat(),
            "snapshot_id": self.snapshot_id,
        }


class CryptoResidualReclaimShadow:
    """Broker-proof live shadow evaluator for CRYPTO-RESIDUAL-RECLAIM-001."""

    def __init__(self, settings: Settings):
        self.settings = settings.model_copy(
            update={
                "crypto_execution_enabled": False,
                "market_data_batch_size": len(CONTEXT_UNIVERSE),
            }
        )
        self.market_data = CryptoMarketDataClient(self.settings)
        self.pending: dict[str, Opportunity] = {}
        self.positions: dict[str, ShadowPosition] = {}
        self.suppressed_until: dict[str, datetime] = {}
        self.closed: list[dict[str, Any]] = []
        self.last_processed_bar_end: datetime | None = None
        self.last_error: str | None = None
        self.last_cycle: dict[str, Any] | None = None
        self.opportunity_count = 0
        self.entry_count = 0
        self.exit_count = 0

    @property
    def broker_orders_possible(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "mode": "shadow",
            "strategy_version_id": "CRYPTO-RESIDUAL-RECLAIM-001",
            "execution_authority": False,
            "broker_orders_possible": False,
            "last_processed_bar_end": (
                self.last_processed_bar_end.isoformat()
                if self.last_processed_bar_end else None
            ),
            "pending": {
                symbol: opportunity.to_dict()
                for symbol, opportunity in sorted(self.pending.items())
            },
            "open_positions": {
                symbol: position.to_dict()
                for symbol, position in sorted(self.positions.items())
            },
            "closed_count": len(self.closed),
            "opportunity_count": self.opportunity_count,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
            "last_error": self.last_error,
            "last_cycle": self.last_cycle,
        }

    async def cycle(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            bars = await self.market_data.bars_many(
                list(CONTEXT_UNIVERSE),
                timeframe="5Min",
                lookback_minutes=LOOKBACK_MINUTES,
            )
            latest_end = _latest_common_completed_end(bars, now=current)
            if latest_end is None:
                self.last_cycle = {
                    "action": "hold",
                    "reason": "no common completed 5-minute crypto bar",
                }
                return []
            if latest_end == self.last_processed_bar_end:
                return []

            series = build_series(
                bars,
                start=latest_end - timedelta(minutes=60),
                end=latest_end + timedelta(minutes=HOLD_MINUTES + BAR_MINUTES),
            )
            events: list[dict[str, Any]] = []

            for symbol, position in list(self.positions.items()):
                if latest_end < position.exit_at + timedelta(minutes=BAR_MINUTES):
                    continue
                exit_reference = _open(
                    series,
                    symbol,
                    position.exit_at + timedelta(minutes=BAR_MINUTES),
                )
                if exit_reference is None:
                    continue
                entry_fill, exit_fill = _fill_prices(
                    symbol,
                    position.entry_reference,
                    exit_reference,
                    "high",
                )
                net_return = exit_fill / entry_fill - 1.0
                event = {
                    "event_type": "crypto_shadow_v6_exit",
                    "symbol": symbol,
                    "occurred_at": position.exit_at.isoformat(),
                    "payload": {
                        "strategy_version_id": "CRYPTO-RESIDUAL-RECLAIM-001",
                        "mode": "shadow",
                        "entry_reference": position.entry_reference,
                        "exit_reference": exit_reference,
                        "stressed_cost_net_return": net_return,
                        "hold_minutes": HOLD_MINUTES,
                        "snapshot_id": position.snapshot_id,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                }
                events.append(event)
                self.closed.append(event)
                self.closed = self.closed[-100:]
                self.exit_count += 1
                del self.positions[symbol]

            for symbol, opportunity in list(self.pending.items()):
                confirmation = reclaim_at(series, opportunity)
                if confirmation is None:
                    if latest_end > opportunity.opportunity_at + timedelta(minutes=20):
                        events.append({
                            "event_type": "crypto_shadow_v6_expired",
                            "symbol": symbol,
                            "occurred_at": latest_end.isoformat(),
                            "payload": {
                                "opportunity_at": opportunity.opportunity_at.isoformat(),
                                "snapshot_id": opportunity.snapshot["snapshot_id"],
                                "execution_authority": False,
                                "broker_orders_possible": False,
                            },
                        })
                        del self.pending[symbol]
                    continue

                fill_bar_end = confirmation + timedelta(minutes=BAR_MINUTES)
                if latest_end < fill_bar_end:
                    continue
                entry_reference = _open(series, symbol, fill_bar_end)
                if entry_reference is None:
                    continue

                position = ShadowPosition(
                    symbol=symbol,
                    opportunity_at=opportunity.opportunity_at,
                    confirmation_at=confirmation,
                    entry_reference=entry_reference,
                    exit_at=confirmation + timedelta(minutes=HOLD_MINUTES),
                    snapshot_id=str(opportunity.snapshot["snapshot_id"]),
                )
                self.positions[symbol] = position
                del self.pending[symbol]
                self.entry_count += 1
                events.append({
                    "event_type": "crypto_shadow_v6_entry",
                    "symbol": symbol,
                    "occurred_at": confirmation.isoformat(),
                    "payload": {
                        **position.to_dict(),
                        "shock_residual_15": opportunity.residual_15,
                        "shock_threshold": opportunity.threshold,
                        "shock_multiple": opportunity.shock_multiple,
                        "nostra_snapshot": opportunity.snapshot,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                })

            for symbol in EXECUTION_UNIVERSE:
                if symbol in self.positions or symbol in self.pending:
                    continue
                if self.suppressed_until.get(
                    symbol,
                    datetime.min.replace(tzinfo=timezone.utc),
                ) > latest_end:
                    continue
                opportunity = opportunity_state(series, symbol, latest_end)
                if opportunity is None:
                    continue
                self.pending[symbol] = opportunity
                self.suppressed_until[symbol] = (
                    opportunity.opportunity_at + timedelta(minutes=15)
                )
                self.opportunity_count += 1
                events.append({
                    "event_type": "crypto_shadow_v6_opportunity",
                    "symbol": symbol,
                    "occurred_at": latest_end.isoformat(),
                    "payload": {
                        "opportunity": opportunity.to_dict(),
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                })

            self.last_processed_bar_end = latest_end
            self.last_error = None
            self.last_cycle = {
                "action": "processed",
                "bar_end": latest_end.isoformat(),
                "events": len(events),
                "pending": len(self.pending),
                "open_positions": len(self.positions),
                "closed_count": len(self.closed),
            }
            return events
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_cycle = {
                "action": "error",
                "reason": self.last_error,
            }
            raise
