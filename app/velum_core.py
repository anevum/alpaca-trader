from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import random
from typing import Any
from zoneinfo import ZoneInfo

from .opportunity import correlation_checks, score_opportunity
from .replay import (
    BPS,
    ReplayEngine,
    ReplayPosition,
    d,
    fractional_qty,
    price,
    stamp,
)
from .sizing import calculate_entry_notional
from .strategy import NY, RollingMomentumVwapStrategy, Signal


class ContinuousRollingMomentumVwapStrategy(RollingMomentumVwapStrategy):
    """Rolling-momentum/VWAP strategy adapted only for VELUM 24/7 replay.

    The live equity strategy remains untouched. This subclass removes equity
    weekday/session gates and defines a replay session as an America/New_York
    calendar day so crypto can be evaluated continuously with the same rolling
    signal mathematics, confirmations, regime checks, and stop model.
    """

    def _completed_session_bars(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = now.astimezone(NY)
        today = now.date()
        completed: list[dict[str, Any]] = []
        for bar in bars:
            bar_stamp = self._timestamp(bar)
            if bar_stamp.date() != today:
                continue
            if bar_stamp + timedelta(minutes=1) > now:
                continue
            completed.append(bar)
        completed.sort(key=self._timestamp)
        return completed

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
        now: datetime | None = None,
    ) -> Signal:
        now = (now or datetime.now(NY)).astimezone(NY)
        symbol = symbol.upper()

        if has_position:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="position already open; replay exits manage risk",
            )

        session = self._completed_session_bars(bars, now)
        if len(session) < self.slow_window + 1:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="not enough completed bars for rolling signal",
            )

        closes = [self._d(bar["c"]) for bar in session]
        current_close = closes[-1]
        previous_close = closes[-2]
        fast_average = self._mean(closes[-self.fast_window:])
        slow_average = self._mean(closes[-self.slow_window:])
        session_vwap = self._vwap(session)

        momentum_anchor = closes[-(self.fast_window + 1)]
        momentum_pct = (
            (current_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        vwap_edge_pct = (
            (current_close - session_vwap) / session_vwap
            if session_vwap > 0
            else Decimal("0")
        )

        fast_above_slow = fast_average > slow_average
        rising = current_close > previous_close
        momentum_ok = momentum_pct >= self.min_momentum_pct
        vwap_ok = current_close > session_vwap and vwap_edge_pct >= self.min_vwap_edge_pct
        vwap_extension_ok = vwap_edge_pct <= self.max_vwap_extension_pct

        metadata: dict[str, Any] = {
            "bar_time": self._timestamp(session[-1]).isoformat(),
            "current_close": str(current_close),
            "previous_close": str(previous_close),
            "fast_average": str(fast_average),
            "slow_average": str(slow_average),
            "session_vwap": str(session_vwap),
            "momentum_pct": str(momentum_pct),
            "vwap_edge_pct": str(vwap_edge_pct),
            "checks": {
                "fast_above_slow": fast_above_slow,
                "rising": rising,
                "momentum_ok": momentum_ok,
                "vwap_ok": vwap_ok,
                "vwap_extension_ok": vwap_extension_ok,
                "confirmations_ok": False,
                "regime_ok": False,
            },
            "confirmations": {},
            "regime_confirmations": {},
            "max_vwap_extension_pct": str(self.max_vwap_extension_pct),
            "continuous_session": True,
        }

        confirmation_passes = 0
        regime_passes = 0
        independent_confirmations = 0
        for confirmation_symbol in self.confirmation_symbols:
            confirmation_symbol = confirmation_symbol.upper()
            if confirmation_symbol == symbol:
                metadata["confirmations"][confirmation_symbol] = {
                    "ok": True,
                    "reason": "candidate symbol; self-confirmation skipped",
                }
                continue
            independent_confirmations += 1
            ok, reason, details = self._confirmation_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            metadata["confirmations"][confirmation_symbol] = {
                "ok": ok,
                "reason": reason,
                **details,
            }
            if ok:
                confirmation_passes += 1

            regime_ok, regime_reason, regime_details = self._regime_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            metadata["regime_confirmations"][confirmation_symbol] = {
                "ok": regime_ok,
                "reason": regime_reason,
                **regime_details,
            }
            if regime_ok:
                regime_passes += 1

        confirmations_ok = (
            independent_confirmations >= self.min_confirmations
            and confirmation_passes >= self.min_confirmations
        )
        regime_ok = (
            independent_confirmations >= self.regime_min_confirmations
            and regime_passes >= self.regime_min_confirmations
        )
        metadata["checks"]["confirmations_ok"] = confirmations_ok
        metadata["checks"]["regime_ok"] = regime_ok
        metadata["confirmation_passes"] = confirmation_passes
        metadata["min_confirmations"] = self.min_confirmations
        metadata["regime_passes"] = regime_passes
        metadata["regime_min_confirmations"] = self.regime_min_confirmations
        metadata["regime_window"] = self.regime_window
        metadata["regime_min_return_pct"] = str(self.regime_min_return_pct)

        if not fast_above_slow:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="fast trend is not above slow trend",
                metadata=metadata,
            )
        if not rising:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="latest completed bar is not rising",
                metadata=metadata,
            )
        if not momentum_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="short-term momentum is below threshold",
                metadata=metadata,
            )
        if not vwap_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="price does not have required VWAP edge",
                metadata=metadata,
            )
        if not vwap_extension_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="price is too extended above session VWAP",
                metadata=metadata,
            )
        if not confirmations_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="not enough market confirmations passed",
                metadata=metadata,
            )
        if not regime_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="market regime is not constructive",
                metadata=metadata,
            )

        effective_stop_pct, stop_model = self._effective_stop_pct(session)
        stop_price = self._price(
            current_close * (Decimal("1") - effective_stop_pct)
        )
        take_profit_price = self._price(
            current_close * (Decimal("1") + self.target_pct)
        )
        metadata["effective_stop_pct"] = str(effective_stop_pct)
        metadata["stop_model"] = stop_model
        metadata["stop_price"] = str(stop_price)
        metadata["take_profit_price"] = str(take_profit_price)

        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=current_close,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            reason="continuous rolling momentum above VWAP with constructive regime",
            metadata=metadata,
        )


class ContinuousReplayEngine(ReplayEngine):
    """Broker-isolated VELUM engine for continuously traded assets."""

    @staticmethod
    def _session_bar_map(
        bars_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> dict[date, dict[str, list[dict[str, Any]]]]:
        sessions: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                sessions[stamp(bar).date()][symbol.upper()].append(bar)
        for symbols in sessions.values():
            for symbol in symbols:
                symbols[symbol].sort(key=stamp)
        return {day: dict(symbols) for day, symbols in sessions.items()}

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

        stop_hit = low > 0 and low <= stop
        target_hit = high > 0 and high >= target
        if stop_hit:
            return "stop", stop
        if target_hit:
            return "target", target
        if self.settings.max_hold_minutes > 0:
            elapsed = (now - position.entry_at).total_seconds() / 60
            if elapsed >= self.settings.max_hold_minutes:
                return "time", close
        return None

    def run(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        initial_equity: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal,
    ) -> dict[str, Any]:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if spread_bps < 0 or slippage_bps < 0:
            raise ValueError("spread_bps and slippage_bps cannot be negative")

        sessions = self._session_bar_map(bars_by_symbol)
        if not sessions:
            raise ValueError("no continuous historical bars were returned")

        cash = initial_equity
        prior_close_equity = initial_equity
        positions: dict[str, ReplayPosition] = {}
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        counters: Counter = Counter()
        spread_pct = spread_bps / BPS
        last_exit: dict[str, datetime] = {}
        final_visible: dict[str, list[dict[str, Any]]] = {}
        final_time: datetime | None = None

        for session_day in sorted(sessions):
            session = sessions[session_day]
            timeline = sorted(
                {
                    stamp(bar)
                    for symbol in self.settings.scan_symbols
                    for bar in session.get(symbol, [])
                }
            )
            if not timeline:
                continue

            visible: dict[str, list[dict[str, Any]]] = {
                symbol: []
                for symbol in set(self.settings.scan_symbols)
                | set(self.settings.confirmation_symbols)
            }
            index: dict[str, int] = {symbol: 0 for symbol in visible}
            entries_today = 0

            for bar_time in timeline:
                now = bar_time + timedelta(minutes=1)
                final_time = now
                for symbol in visible:
                    source = session.get(symbol, [])
                    cursor = index[symbol]
                    while cursor < len(source) and stamp(source[cursor]) <= bar_time:
                        visible[symbol].append(source[cursor])
                        cursor += 1
                    index[symbol] = cursor

                marks = self._marks(visible, positions)
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
                    exit_fill = self._fill(
                        exit_reference,
                        "sell",
                        spread_bps,
                        slippage_bps,
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
                account = self._account(
                    cash,
                    prior_close_equity,
                    positions,
                    marks,
                )
                equity_curve.append(
                    {
                        "at": now.isoformat(),
                        "equity": str(d(account["equity"])),
                    }
                )

                if exited_this_cycle:
                    continue
                if (
                    self.settings.portfolio_limit_mode == "count"
                    and entries_today >= self.settings.max_daily_orders
                ):
                    continue

                buy_signals: list[Signal] = []
                confirmation_bars = {
                    symbol: visible.get(symbol, [])
                    for symbol in self.settings.confirmation_symbols
                }
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

                    allowed, _, quality = self._historical_market_quality(
                        signal,
                        visible,
                        now,
                        spread_pct,
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
                    signal.metadata["relative_volume_ratio"] = ranking[
                        "relative_volume_ratio"
                    ]
                    signal.metadata["trend_persistence"] = ranking[
                        "trend_persistence"
                    ]
                    buy_signals.append(signal)
                    counters["signals_qualified"] += 1

                ranked = sorted(
                    buy_signals,
                    key=self._quality_signal_rank,
                    reverse=True,
                )
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
                    if (
                        latest_exit is not None
                        and self.settings.reentry_cooldown_minutes > 0
                    ):
                        elapsed = (now - latest_exit).total_seconds() / 60
                        if elapsed < self.settings.reentry_cooldown_minutes:
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
                    account = self._account(
                        cash,
                        prior_close_equity,
                        positions,
                        marks,
                    )
                    payloads = self._position_payloads(positions, marks)
                    stop_override = d(
                        (signal.metadata or {}).get("effective_stop_pct")
                    )
                    notional = calculate_entry_notional(
                        self.settings,
                        account,
                        payloads,
                        stop_pct_override=stop_override
                        if stop_override > 0
                        else None,
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
                    entry_fill = self._fill(
                        reference,
                        "buy",
                        spread_bps,
                        slippage_bps,
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
                        quality_score=float(
                            (signal.metadata or {}).get("quality_score", 0) or 0
                        ),
                    )
                    entries_today += 1
                    planned += 1
                    counters["entries"] += 1

            marks = self._marks(visible, positions)
            account = self._account(cash, prior_close_equity, positions, marks)
            prior_close_equity = d(account["equity"])
            equity_curve.append(
                {
                    "at": (
                        timeline[-1] + timedelta(minutes=1)
                    ).isoformat(),
                    "equity": str(prior_close_equity),
                }
            )
            final_visible = visible

        if positions and final_time is not None:
            for symbol in list(positions):
                current = final_visible.get(symbol, [])
                if not current:
                    continue
                position = positions[symbol]
                exit_reference = price(current[-1])
                exit_fill = self._fill(
                    exit_reference,
                    "sell",
                    spread_bps,
                    slippage_bps,
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

        if final_time is not None:
            equity_curve.append(
                {"at": final_time.isoformat(), "equity": str(cash)}
            )

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
                "spread_bps": str(spread_bps),
                "slippage_bps_per_side": str(slippage_bps),
                "commission_per_order": "0",
                "intrabar_stop_target_policy": "stop_first",
                "historical_quotes_available": False,
                "same_rolling_signal_math": True,
                "broker_orders_possible": False,
                "continuous_market": True,
                "session_boundary": "America/New_York calendar day",
                "positions_carry_across_session_boundaries": True,
            },
            "strategy": {
                "name": "continuous_" + self.settings.strategy_name,
                "scan_symbols": list(self.settings.scan_symbols),
                "fast_window": self.settings.fast_window,
                "slow_window": self.settings.slow_window,
                "stop_pct": str(self.settings.stop_pct),
                "target_pct": str(self.settings.target_pct),
                "max_hold_minutes": self.settings.max_hold_minutes,
                "max_pairwise_correlation": str(
                    self.settings.max_pairwise_correlation
                ),
                "sizing_mode": self.settings.sizing_mode,
                "portfolio_limit_mode": self.settings.portfolio_limit_mode,
                "risk_per_trade_pct": str(self.settings.risk_per_trade_pct),
                "max_gross_exposure_pct": str(
                    self.settings.max_gross_exposure_pct
                ),
            },
            "sessions": len(sessions),
            "trades": trades,
            "equity_curve": equity_curve,
        }


def bootstrap_trade_distribution(
    trades: list[dict[str, Any]],
    *,
    paths: int,
    seed: int,
) -> dict[str, Any]:
    """Deterministic non-parametric resampling of realized replay trade P&L."""
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
    totals: list[float] = []
    for _ in range(paths):
        totals.append(sum(rng.choice(pnls) for _ in range(len(pnls))))
    totals.sort()

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
