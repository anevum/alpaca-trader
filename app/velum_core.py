from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import random
from collections.abc import Mapping
from typing import Any

from .crypto_layer import CryptoRollingMomentumStrategy
from .opportunity import correlation_checks, score_opportunity
from .replay import BPS, ReplayEngine, ReplayPosition, d, fractional_qty, price, stamp
from .sizing import calculate_entry_notional
from .strategy import Signal


CostInput = Decimal | Mapping[str, Decimal | str | int | float]


def _cost_bps(value: CostInput, symbol: str) -> Decimal:
    if isinstance(value, Mapping):
        normalized = symbol.upper()
        raw = value.get(normalized)
        if raw is None:
            raw = value.get("*")
        if raw is None:
            raise ValueError(f"missing replay cost for symbol: {normalized}")
        result = d(raw)
    else:
        result = d(value)
    if result < 0:
        raise ValueError("replay costs cannot be negative")
    return result


def _cost_payload(value: CostInput) -> str | dict[str, str]:
    if isinstance(value, Mapping):
        return {
            str(symbol).upper(): str(d(amount))
            for symbol, amount in sorted(value.items())
        }
    return str(d(value))


class ContinuousReplayEngine(ReplayEngine):
    """Broker-isolated replay engine for VELUM continuously traded assets.

    It reuses production scoring, correlation, sizing, and risk mathematics but
    never imports or calls the broker client or execution engine.
    """

    def run_btc_direct(self, rows, *, start, end, candidate, prepared_signals=None):
        from .velum_btc import run_direct
        return run_direct(self, rows, start=start, end=end, candidate=candidate,
                          prepared_signals=prepared_signals)

    def _exit_decision(
        self,
        position: ReplayPosition,
        bar: dict[str, Any],
        now: datetime,
    ) -> tuple[str, Decimal] | None:
        low = price(bar, "l")
        high = price(bar, "h")
        close = price(bar, "c")
        stop = position.entry_price * (Decimal("1") - self.settings.stop_pct)
        target = position.entry_price * (Decimal("1") + self.settings.target_pct)

        # Historical one-minute bars do not reveal intrabar ordering.
        if low > 0 and low <= stop:
            return "stop", stop
        if high > 0 and high >= target:
            return "target", target
        if self.settings.max_hold_minutes > 0:
            elapsed = (now - position.entry_at).total_seconds() / 60
            if elapsed >= self.settings.max_hold_minutes:
                return "time", close
        return None

    @staticmethod
    def _append_until(
        source: list[dict[str, Any]],
        visible: list[dict[str, Any]],
        cursor: int,
        bar_time: datetime,
    ) -> int:
        while cursor < len(source) and stamp(source[cursor]) <= bar_time:
            visible.append(source[cursor])
            cursor += 1
        return cursor

    def _trim_lookback(
        self,
        visible: dict[str, list[dict[str, Any]]],
        now: datetime,
    ) -> None:
        minutes = max(int(getattr(self.settings, "crypto_lookback_minutes", 240)), 2)
        cutoff = now - timedelta(minutes=minutes)
        for symbol, bars in visible.items():
            first = 0
            while first < len(bars) and stamp(bars[first]) < cutoff:
                first += 1
            if first:
                visible[symbol] = bars[first:]

    def run(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        initial_equity: Decimal,
        spread_bps: CostInput,
        slippage_bps: CostInput,
    ) -> dict[str, Any]:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if isinstance(spread_bps, Mapping):
            for symbol in self.settings.scan_symbols:
                _cost_bps(spread_bps, symbol)
        elif d(spread_bps) < 0:
            raise ValueError("spread_bps cannot be negative")
        if isinstance(slippage_bps, Mapping):
            for symbol in self.settings.scan_symbols:
                _cost_bps(slippage_bps, symbol)
        elif d(slippage_bps) < 0:
            raise ValueError("slippage_bps cannot be negative")

        normalized = {
            symbol.upper(): sorted(list(bars), key=stamp)
            for symbol, bars in bars_by_symbol.items()
        }
        timeline = sorted(
            {
                stamp(bar)
                for symbol in self.settings.scan_symbols
                for bar in normalized.get(symbol.upper(), [])
            }
        )
        if not timeline:
            raise ValueError("no continuous historical bars were returned")

        tracked = set(self.settings.scan_symbols) | set(self.settings.confirmation_symbols)
        visible: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in tracked}
        index = {symbol: 0 for symbol in tracked}
        positions: dict[str, ReplayPosition] = {}
        last_exit: dict[str, datetime] = {}
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        counters: Counter = Counter()
        cash = initial_equity
        prior_close_equity = initial_equity
        current_day = None
        entries_today = 0
        final_time: datetime | None = None

        for bar_time in timeline:
            now = bar_time + timedelta(minutes=1)
            final_time = now

            if current_day is not None and now.date() != current_day:
                marks = self._marks(visible, positions)
                prior_close_equity = d(
                    self._account(cash, prior_close_equity, positions, marks)["equity"]
                )
                entries_today = 0
            current_day = now.date()

            for symbol in tracked:
                source = normalized.get(symbol, [])
                index[symbol] = self._append_until(
                    source,
                    visible[symbol],
                    index[symbol],
                    bar_time,
                )
            self._trim_lookback(visible, now)

            exited_this_cycle = False
            for symbol in list(positions):
                current = visible.get(symbol, [])
                if not current or stamp(current[-1]) != bar_time:
                    continue
                position = positions[symbol]
                decision = self._exit_decision(position, current[-1], now)
                if decision is None:
                    continue
                exit_reason, exit_reference = decision
                symbol_spread_bps = _cost_bps(spread_bps, symbol)
                symbol_slippage_bps = _cost_bps(slippage_bps, symbol)
                exit_fill = self._fill(
                    exit_reference,
                    "sell",
                    symbol_spread_bps,
                    symbol_slippage_bps,
                )
                proceeds = position.qty * exit_fill
                cost_basis = position.qty * position.entry_price
                net_pnl = proceeds - cost_basis
                cash += proceeds
                trades.append(
                    {
                        "symbol": symbol,
                        "entry_at": position.entry_at.isoformat(),
                        "exit_at": now.isoformat(),
                        "entry_price": str(position.entry_price),
                        "exit_price": str(exit_fill),
                        "qty": str(position.qty),
                        "entry_notional": str(cost_basis),
                        "net_pnl": str(net_pnl),
                        "return_pct": round(float(net_pnl / cost_basis), 6)
                        if cost_basis > 0
                        else 0.0,
                        "quality_score": position.quality_score,
                        "exit_reason": exit_reason,
                    }
                )
                del positions[symbol]
                last_exit[symbol] = now
                exited_this_cycle = True

            marks = self._marks(visible, positions)
            account = self._account(cash, prior_close_equity, positions, marks)
            equity_curve.append({"at": now.isoformat(), "equity": str(d(account["equity"]))})

            if exited_this_cycle:
                continue
            if (
                self.settings.portfolio_limit_mode == "count"
                and entries_today >= self.settings.max_daily_orders
            ):
                continue

            confirmation_bars = {
                symbol: visible.get(symbol, [])
                for symbol in self.settings.confirmation_symbols
            }
            buy_signals: list[Signal] = []
            for symbol in self.settings.scan_symbols:
                signal = self.strategy.evaluate(
                    bars=visible.get(symbol, []),
                    confirmation_bars=confirmation_bars,
                    symbol=symbol,
                    has_position=symbol in positions,
                    order_notional=self.settings.order_notional,
                    now=now,
                )
                if signal.action != "buy":
                    continue

                symbol_spread_bps = _cost_bps(spread_bps, signal.symbol)
                allowed, _, quality = self._historical_market_quality(
                    signal,
                    visible,
                    now,
                    symbol_spread_bps / BPS,
                )
                if not allowed:
                    continue

                ranking = score_opportunity(
                    self.settings,
                    signal,
                    visible.get(symbol, []),
                    quality,
                )
                signal.metadata = dict(signal.metadata or {})
                signal.metadata["market_quality"] = quality
                signal.metadata["quality_score"] = ranking["score"]
                signal.metadata["quality_components"] = ranking["components"]
                signal.metadata["relative_volume_ratio"] = ranking["relative_volume_ratio"]
                signal.metadata["trend_persistence"] = ranking["trend_persistence"]
                buy_signals.append(signal)
                counters["signals_qualified"] += 1

            ranked = sorted(buy_signals, key=self._quality_signal_rank, reverse=True)
            planned = 0
            for signal in ranked:
                if (
                    self.settings.portfolio_limit_mode == "count"
                    and planned >= self.settings.max_new_entries_per_cycle
                ):
                    break
                if (
                    self.settings.portfolio_limit_mode == "count"
                    and len(positions) >= self.settings.max_concurrent_positions
                ):
                    break

                symbol = signal.symbol.upper()
                if symbol in positions:
                    continue
                latest_exit = last_exit.get(symbol)
                if latest_exit is not None and self.settings.reentry_cooldown_minutes > 0:
                    if (
                        now - latest_exit
                    ).total_seconds() / 60 < self.settings.reentry_cooldown_minutes:
                        continue

                allowed, _, _ = correlation_checks(
                    self.settings,
                    symbol,
                    list(positions),
                    visible,
                )
                if not allowed:
                    counters["correlation_blocks"] += 1
                    continue

                marks = self._marks(visible, positions)
                account = self._account(cash, prior_close_equity, positions, marks)
                payloads = self._position_payloads(positions, marks)
                effective_stop = d((signal.metadata or {}).get("effective_stop_pct"))
                notional = calculate_entry_notional(
                    self.settings,
                    account,
                    payloads,
                    stop_pct_override=effective_stop if effective_stop > 0 else None,
                )
                risk_allowed, _ = self._replay_risk(
                    symbol,
                    notional,
                    account,
                    payloads,
                    entries_today,
                )
                if not risk_allowed:
                    counters["risk_blocks"] += 1
                    continue

                reference = signal.reference_price
                if reference <= 0:
                    continue
                symbol_spread_bps = _cost_bps(spread_bps, symbol)
                symbol_slippage_bps = _cost_bps(slippage_bps, symbol)
                entry_fill = self._fill(
                    reference,
                    "buy",
                    symbol_spread_bps,
                    symbol_slippage_bps,
                )
                qty = fractional_qty(notional, reference)
                if qty <= 0:
                    continue
                cost = qty * entry_fill
                if cost > cash:
                    continue

                cash -= cost
                positions[symbol] = ReplayPosition(
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_fill,
                    entry_reference=reference,
                    entry_at=now,
                    notional=cost,
                    quality_score=float((signal.metadata or {}).get("quality_score", 0) or 0),
                )
                entries_today += 1
                planned += 1
                counters["entries"] += 1

        if final_time is not None:
            for symbol in list(positions):
                current = visible.get(symbol, [])
                if not current:
                    continue
                position = positions[symbol]
                symbol_spread_bps = _cost_bps(spread_bps, symbol)
                symbol_slippage_bps = _cost_bps(slippage_bps, symbol)
                exit_fill = self._fill(
                    price(current[-1]),
                    "sell",
                    symbol_spread_bps,
                    symbol_slippage_bps,
                )
                proceeds = position.qty * exit_fill
                cost_basis = position.qty * position.entry_price
                net_pnl = proceeds - cost_basis
                cash += proceeds
                trades.append(
                    {
                        "symbol": symbol,
                        "entry_at": position.entry_at.isoformat(),
                        "exit_at": final_time.isoformat(),
                        "entry_price": str(position.entry_price),
                        "exit_price": str(exit_fill),
                        "qty": str(position.qty),
                        "entry_notional": str(cost_basis),
                        "net_pnl": str(net_pnl),
                        "return_pct": round(float(net_pnl / cost_basis), 6)
                        if cost_basis > 0
                        else 0.0,
                        "quality_score": position.quality_score,
                        "exit_reason": "window_end",
                    }
                )
                del positions[symbol]
            equity_curve.append({"at": final_time.isoformat(), "equity": str(cash)})

        summary = self._summarize(
            trades,
            equity_curve,
            initial_equity,
            cash,
            counters,
        )
        return {
            "summary": summary,
            "assumptions": {
                "spread_bps": _cost_payload(spread_bps),
                "slippage_bps_per_side": _cost_payload(slippage_bps),
                "commission_per_order": "0",
                "intrabar_stop_target_policy": "stop_first",
                "historical_quotes_available": False,
                "same_crypto_signal_logic": isinstance(
                    self.strategy, CryptoRollingMomentumStrategy
                ),
                "broker_orders_possible": False,
                "continuous_market": True,
                "positions_carry_across_calendar_boundaries": True,
                "lookback_minutes": int(
                    getattr(self.settings, "crypto_lookback_minutes", 240)
                ),
            },
            "strategy": {
                "name": type(self.strategy).__name__,
                "scan_symbols": list(self.settings.scan_symbols),
                "confirmation_symbols": list(self.settings.confirmation_symbols),
                "fast_window": self.settings.fast_window,
                "slow_window": self.settings.slow_window,
                "stop_pct": str(self.settings.stop_pct),
                "target_pct": str(self.settings.target_pct),
                "max_hold_minutes": self.settings.max_hold_minutes,
                "sizing_mode": self.settings.sizing_mode,
                "portfolio_limit_mode": self.settings.portfolio_limit_mode,
                "risk_per_trade_pct": str(self.settings.risk_per_trade_pct),
                "max_gross_exposure_pct": str(self.settings.max_gross_exposure_pct),
            },
            "sessions": len({point["at"][:10] for point in equity_curve}),
            "trades": trades,
            "equity_curve": equity_curve,
        }


def bootstrap_trade_distribution(
    trades: list[dict[str, Any]],
    *,
    paths: int,
    seed: int,
) -> dict[str, Any]:
    """Deterministic bootstrap of replay trade P&L; descriptive, not predictive."""
    if paths < 1:
        raise ValueError("paths must be positive")

    pnls = [float(d(row.get("net_pnl"))) for row in trades]
    if not pnls:
        return {
            "paths": paths,
            "trade_count": 0,
            "median_net_pnl": 0.0,
            "p05_net_pnl": 0.0,
            "p95_net_pnl": 0.0,
            "positive_path_fraction": 0.0,
            "method": "bootstrap-realized-trade-pnl",
            "forecast": False,
        }

    rng = random.Random(seed)
    totals = sorted(
        sum(rng.choice(pnls) for _ in range(len(pnls)))
        for _ in range(paths)
    )

    def quantile(q: float) -> float:
        if len(totals) == 1:
            return totals[0]
        index = int(round((len(totals) - 1) * q))
        return totals[max(0, min(index, len(totals) - 1))]

    return {
        "paths": paths,
        "trade_count": len(pnls),
        "median_net_pnl": round(quantile(0.50), 6),
        "p05_net_pnl": round(quantile(0.05), 6),
        "p95_net_pnl": round(quantile(0.95), 6),
        "positive_path_fraction": round(
            sum(value > 0 for value in totals) / len(totals),
            6,
        ),
        "method": "bootstrap-realized-trade-pnl",
        "forecast": False,
    }
