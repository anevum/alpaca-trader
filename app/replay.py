from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .market_data import MarketDataClient
from .opportunity import correlation_checks, score_opportunity
from .sizing import calculate_entry_notional, effective_gross_limit
from .strategy import RollingMomentumVwapStrategy, Signal


NY = ZoneInfo("America/New_York")
BPS = Decimal("10000")
QTY_STEP = Decimal("0.000000001")
CENT = Decimal("0.01")


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def stamp(bar: dict[str, Any]) -> datetime:
    raw = str(bar["t"])
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY)
    return parsed.astimezone(NY)


def price(bar: dict[str, Any], key: str = "c") -> Decimal:
    return d(bar.get(key))


def fractional_qty(notional: Decimal, fill_price: Decimal) -> Decimal:
    if fill_price <= 0:
        return Decimal("0")
    return (notional / fill_price).quantize(QTY_STEP, rounding=ROUND_DOWN)


@dataclass
class ReplayPosition:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_reference: Decimal
    entry_at: datetime
    notional: Decimal
    quality_score: float
    risk_stop_pct: Decimal = Decimal("0")
    peak_return_pct: Decimal = Decimal("0")
    protected_floor_pct: Decimal | None = None
    profit_protection_active: bool = False
    thesis_failure_count: int = 0
    last_thesis_failure_bar_time: str | None = None
    target_reached: bool = False

    def market_value(self, mark: Decimal) -> Decimal:
        return self.qty * mark


class ReplayEngine:
    """Deterministic, broker-isolated replay of the production long-only logic."""

    def __init__(self, settings: Settings, strategy: Any):
        self.settings = settings
        self.strategy = strategy

    @staticmethod
    def _session_bar_map(
        bars_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> dict[date, dict[str, list[dict[str, Any]]]]:
        sessions: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                bar_stamp = stamp(bar)
                if time(9, 30) <= bar_stamp.time() < time(16, 0):
                    sessions[bar_stamp.date()][symbol.upper()].append(bar)
        for symbols in sessions.values():
            for symbol in symbols:
                symbols[symbol].sort(key=stamp)
        return {day: dict(symbols) for day, symbols in sessions.items()}

    @staticmethod
    def _marks(
        visible: dict[str, list[dict[str, Any]]],
        positions: dict[str, ReplayPosition],
    ) -> dict[str, Decimal]:
        output: dict[str, Decimal] = {}
        for symbol in positions:
            bars = visible.get(symbol, [])
            if bars:
                output[symbol] = price(bars[-1])
            else:
                output[symbol] = positions[symbol].entry_price
        return output

    @staticmethod
    def _account(
        cash: Decimal,
        prior_close_equity: Decimal,
        positions: dict[str, ReplayPosition],
        marks: dict[str, Decimal],
    ) -> dict[str, Any]:
        equity = cash + sum(
            (
                position.market_value(marks.get(symbol, position.entry_price))
                for symbol, position in positions.items()
            ),
            Decimal("0"),
        )
        return {
            "cash": str(cash),
            "equity": str(equity),
            "last_equity": str(prior_close_equity),
            "account_blocked": False,
            "trading_blocked": False,
        }

    @staticmethod
    def _position_payloads(
        positions: dict[str, ReplayPosition],
        marks: dict[str, Decimal],
    ) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbol,
                "qty": str(position.qty),
                "market_value": str(
                    position.market_value(marks.get(symbol, position.entry_price))
                ),
            }
            for symbol, position in positions.items()
        ]

    def _replay_risk(
        self,
        symbol: str,
        notional: Decimal,
        account: dict[str, Any],
        position_payloads: list[dict[str, Any]],
        entry_orders_today: int,
        *,
        active_entry_universe: set[str] | None = None,
    ) -> tuple[bool, str]:
        if self.settings.dynamic_universe_enabled:
            if active_entry_universe is None:
                return False, "as-of dynamic entry universe is unavailable"
            if symbol.upper() not in active_entry_universe:
                return False, "symbol is not in as-of active dynamic universe"
        if symbol.upper() not in self.settings.allowed_symbols:
            return False, "symbol is not allowlisted"
        if not self.settings.dynamic_universe_enabled and symbol.upper() not in self.settings.scan_symbols:
            return False, "symbol is not in scan universe"
        if any(
            str(item.get("symbol", "")).upper() == symbol.upper()
            and d(item.get("qty")) > 0
            for item in position_payloads
        ):
            return False, "position is already open"
        if (
            self.settings.portfolio_limit_mode == "count"
            and len([item for item in position_payloads if d(item.get("qty")) > 0])
            >= self.settings.max_concurrent_positions
        ):
            return False, "maximum concurrent-position limit reached"
        if notional <= 0:
            return False, "order notional must be positive"
        if notional > self.settings.max_order_notional:
            return False, "order exceeds MAX_ORDER_NOTIONAL"
        if notional > self.settings.max_position_notional:
            return False, "order exceeds MAX_POSITION_NOTIONAL"

        current_exposure = sum(
            (abs(d(item.get("market_value"))) for item in position_payloads),
            Decimal("0"),
        )
        if current_exposure + notional > self.settings.max_total_position_notional:
            return False, "order exceeds MAX_TOTAL_POSITION_NOTIONAL"
        if self.settings.sizing_mode == "equity_risk":
            gross_limit = effective_gross_limit(self.settings, account)
            if gross_limit <= 0 or current_exposure + notional > gross_limit:
                return False, "order exceeds MAX_GROSS_EXPOSURE_PCT"
        if (
            self.settings.portfolio_limit_mode == "count"
            and entry_orders_today >= self.settings.max_daily_orders
        ):
            return False, "daily entry-order limit reached"
        if d(account.get("cash")) < notional:
            return False, "insufficient cash"
        last_equity = d(account.get("last_equity"))
        equity = d(account.get("equity"))
        if last_equity > 0 and (last_equity - equity) >= self.settings.max_daily_loss:
            return False, "daily loss circuit breaker is active"
        return True, "risk checks passed"

    @staticmethod
    def _quality_signal_rank(signal: Signal) -> tuple[Decimal, Decimal, Decimal, int]:
        metadata = signal.metadata or {}
        return (
            d(metadata.get("quality_score")),
            d(metadata.get("momentum_pct")),
            d(metadata.get("vwap_edge_pct")),
            int(metadata.get("confirmation_passes", 0) or 0),
        )

    def _historical_market_quality(
        self,
        signal: Signal,
        visible: dict[str, list[dict[str, Any]]],
        now: datetime,
        spread_pct: Decimal,
    ) -> tuple[bool, str, dict[str, Any]]:
        if spread_pct > self.settings.max_spread_pct:
            return False, "assumed spread exceeds MAX_SPREAD_PCT", {
                "spread_pct": str(spread_pct)
            }

        bars = visible.get(signal.symbol, [])
        if not bars:
            return False, "no candidate bars", {}

        latest_stamp = stamp(bars[-1])
        age_seconds = max(
            (now - (latest_stamp + timedelta(minutes=1))).total_seconds(),
            0,
        )
        if age_seconds > self.settings.max_bar_age_seconds:
            return False, "candidate bar is stale", {
                "bar_age_seconds": round(age_seconds, 3),
                "spread_pct": str(spread_pct),
            }

        confirmations = (signal.metadata or {}).get("confirmations") or {}
        fresh_passes = 0
        for symbol, payload in confirmations.items():
            normalized = str(symbol).upper()
            if normalized == signal.symbol.upper() or not bool((payload or {}).get("ok")):
                continue
            confirmation = visible.get(normalized, [])
            if not confirmation:
                continue
            confirmation_age = max(
                (
                    now
                    - (stamp(confirmation[-1]) + timedelta(minutes=1))
                ).total_seconds(),
                0,
            )
            if confirmation_age <= self.settings.max_bar_age_seconds:
                fresh_passes += 1

        details = {
            "bar_age_seconds": round(age_seconds, 3),
            "spread_pct": str(spread_pct),
            "fresh_confirmation_passes": fresh_passes,
        }
        if fresh_passes < self.settings.min_confirmations:
            return False, "not enough fresh market confirmations passed", details
        return True, "historical market-quality assumptions passed", details

    @staticmethod
    def _fill(reference: Decimal, side: str, spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
        half_spread = spread_bps / Decimal("2") / BPS
        slippage = slippage_bps / BPS
        adjustment = half_spread + slippage
        if side == "buy":
            return reference * (Decimal("1") + adjustment)
        return reference * (Decimal("1") - adjustment)

    def _rolling_strategy(self) -> RollingMomentumVwapStrategy | None:
        """Research wrappers expose their underlying production strategy."""
        underlying = getattr(self.strategy, "production_strategy", self.strategy)
        return underlying if isinstance(underlying, RollingMomentumVwapStrategy) else None

    def _exit_decision(
        self,
        position: ReplayPosition,
        bar: dict[str, Any],
        now: datetime,
        *,
        visible: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[str, Decimal] | None:
        """Approximate completed-bar exits; never infer broker fills from bars.

        Existing (previous-bar) stops are evaluated before updating the peak to
        avoid pretending a newly raised intrabar stop was already resting.
        """
        low = price(bar, "l")
        high = price(bar, "h")
        close = price(bar, "c")
        opened = price(bar, "o")
        risk_stop = position.risk_stop_pct or self.settings.stop_pct
        hard_stop = position.entry_price * (Decimal("1") - risk_stop)
        target = position.entry_price * (Decimal("1") + self.settings.target_pct)
        protected = (
            position.entry_price * (Decimal("1") + position.protected_floor_pct)
            if position.profit_protection_active
            and position.protected_floor_pct is not None
            else None
        )
        stop = max(hard_stop, protected) if protected is not None else hard_stop

        # Live prioritizes the scheduled forced flatten before ordinary exits.
        if now.time() >= self.settings.force_flat_time:
            return "eod", close
        if low > 0 and low <= stop:
            # Stop-first, including gap-through at the next bar open.
            reference = min(stop, opened) if opened > 0 else stop
            return ("protect" if protected is not None and protected > hard_stop else "stop", reference)

        rolling = self._rolling_strategy()
        if rolling is None:
            if high > 0 and high >= target:
                return "target", target
            if self.settings.max_hold_minutes > 0:
                elapsed = (now - position.entry_at).total_seconds() / 60
                if elapsed >= self.settings.max_hold_minutes:
                    return "time", close
        else:
            # Live rolling momentum uses the target to arm protection, not to
            # force take-profit, and deliberately bypasses MAX_HOLD_MINUTES.
            if high >= target:
                position.target_reached = True
            if (
                self.settings.thesis_exit_enabled
                and visible is not None
            ):
                health = rolling.position_health(
                    bars=visible.get(position.symbol, []),
                    confirmation_bars={
                        key: visible.get(key, [])
                        for key in self.settings.confirmation_symbols
                    },
                    symbol=position.symbol,
                    now=now,
                )
                current_return = (
                    (close - position.entry_price) / position.entry_price
                    if position.entry_price > 0 else Decimal("0")
                )
                failed = (
                    health.get("strong_failure") is True
                    and current_return <= self.settings.thesis_exit_max_return_pct
                )
                bar_time = str(health.get("bar_time") or "")
                if failed and bar_time and bar_time != position.last_thesis_failure_bar_time:
                    position.thesis_failure_count += 1
                    position.last_thesis_failure_bar_time = bar_time
                elif not failed:
                    position.thesis_failure_count = 0
                    position.last_thesis_failure_bar_time = None
                if failed and position.thesis_failure_count >= self.settings.thesis_failure_cycles:
                    return "thesis", close

            if high > 0:
                observed_peak = (high - position.entry_price) / position.entry_price
                position.peak_return_pct = max(position.peak_return_pct, observed_peak)
            if self.settings.profit_protect_enabled:
                activation = max(
                    self.settings.profit_protect_activation_pct,
                    self.settings.target_pct,
                )
                if position.peak_return_pct >= activation:
                    floor = max(
                        self.settings.profit_protect_min_pct,
                        position.peak_return_pct * self.settings.profit_protect_retain_fraction,
                    )
                    position.protected_floor_pct = max(
                        position.protected_floor_pct
                        if position.protected_floor_pct is not None
                        else floor,
                        floor,
                    )
                    position.profit_protection_active = True

        return None

    @staticmethod
    def _summarize(
        trades: list[dict[str, Any]],
        equity_curve: list[dict[str, Any]],
        initial_equity: Decimal,
        ending_equity: Decimal,
        counters: Counter,
    ) -> dict[str, Any]:
        pnls = [d(trade["net_pnl"]) for trade in trades]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        gross_profit = sum(wins, Decimal("0"))
        gross_loss = abs(sum(losses, Decimal("0")))
        peak = initial_equity
        max_drawdown = Decimal("0")
        for point in equity_curve:
            equity = d(point["equity"])
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)

        by_symbol: dict[str, dict[str, Any]] = {}
        grouped: dict[str, list[Decimal]] = defaultdict(list)
        for trade in trades:
            grouped[str(trade["symbol"])].append(d(trade["net_pnl"]))
        for symbol, values in grouped.items():
            symbol_wins = sum(value > 0 for value in values)
            by_symbol[symbol] = {
                "trades": len(values),
                "wins": symbol_wins,
                "win_rate": round(symbol_wins / len(values), 4),
                "net_pnl": str(sum(values, Decimal("0")).quantize(CENT)),
                "average_pnl": str(
                    (sum(values, Decimal("0")) / Decimal(len(values))).quantize(CENT)
                ),
            }

        return {
            "initial_equity": str(initial_equity.quantize(CENT)),
            "ending_equity": str(ending_equity.quantize(CENT)),
            "net_pnl": str((ending_equity - initial_equity).quantize(CENT)),
            "return_pct": round(
                float((ending_equity - initial_equity) / initial_equity)
                if initial_equity > 0 else 0.0,
                6,
            ),
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
            "gross_profit": str(gross_profit.quantize(CENT)),
            "gross_loss": str(gross_loss.quantize(CENT)),
            "profit_factor": (
                round(float(gross_profit / gross_loss), 4)
                if gross_loss > 0
                else None
            ),
            "expectancy_per_trade": str(
                (
                    sum(pnls, Decimal("0")) / Decimal(len(pnls))
                    if pnls else Decimal("0")
                ).quantize(CENT)
            ),
            "max_drawdown": str(max_drawdown.quantize(CENT)),
            "max_drawdown_pct": round(
                float(max_drawdown / initial_equity) if initial_equity > 0 else 0.0,
                6,
            ),
            "research_rule_blocks": counters["research_rule_blocks"],
            "signals_scored": counters["signals_scored"],
            "quality_blocks": counters["quality_blocks"],
            "universe_snapshot_missing_cycles": counters["universe_snapshot_missing_cycles"],
            "signals_qualified": counters["signals_qualified"],
            "entries": counters["entries"],
            "correlation_blocks": counters["correlation_blocks"],
            "risk_blocks": counters["risk_blocks"],
            "exit_reasons": dict(Counter(str(trade["exit_reason"]) for trade in trades)),
            "by_symbol": by_symbol,
        }

    def run(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        initial_equity: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        universe_snapshots: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if spread_bps < 0 or slippage_bps < 0:
            raise ValueError("spread_bps and slippage_bps cannot be negative")

        # Time-indexed universe snapshots are mandatory for dynamic replay.
        # The current production universe cannot be reconstructed by reading
        # today's universe into earlier history.
        asof_universe: list[tuple[datetime, set[str]]] = []
        if self.settings.dynamic_universe_enabled:
            if not universe_snapshots:
                raise ValueError(
                    "dynamic universe replay requires time-indexed as-of universe_snapshots"
                )
            for raw_time, symbols in universe_snapshots.items():
                asof = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                if asof.tzinfo is None:
                    raise ValueError("universe snapshot timestamps must be timezone-aware")
                if not isinstance(symbols, (list, tuple)):
                    raise ValueError("universe snapshot symbols must be lists")
                asof_universe.append((
                    asof.astimezone(NY),
                    {str(symbol).upper() for symbol in symbols if str(symbol).strip()},
                ))
            asof_universe.sort(key=lambda row: row[0])
        sessions = self._session_bar_map(bars_by_symbol)
        if not sessions:
            raise ValueError("no regular-session historical bars were returned")

        cash = initial_equity
        prior_close_equity = initial_equity
        positions: dict[str, ReplayPosition] = {}
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        counters: Counter = Counter()
        spread_pct = spread_bps / BPS

        for session_day in sorted(sessions):
            session = sessions[session_day]
            snapshots_for_day = [
                (at, symbols) for at, symbols in asof_universe
                if at.date() == session_day
            ]
            scanned = (
                set().union(*(symbols for _, symbols in snapshots_for_day))
                if self.settings.dynamic_universe_enabled
                else set(self.settings.scan_symbols)
            )
            if self.settings.dynamic_universe_enabled and not snapshots_for_day:
                raise ValueError(
                    f"missing as-of dynamic universe snapshots for {session_day}"
                )
            missing_symbols = scanned.difference(session)
            if self.settings.dynamic_universe_enabled and missing_symbols:
                raise ValueError(
                    "dynamic replay missing bar histories for: "
                    + ",".join(sorted(missing_symbols))
                )
            timeline = sorted(
                {
                    stamp(bar)
                    for symbol in scanned
                    for bar in session.get(symbol, [])
                }
            )
            if not timeline:
                continue

            visible: dict[str, list[dict[str, Any]]] = {
                symbol: [] for symbol in scanned | set(self.settings.confirmation_symbols)
            }
            index: dict[str, int] = {symbol: 0 for symbol in visible}
            last_exit: dict[str, datetime] = {}
            entries_today = 0

            for bar_time in timeline:
                now = bar_time + timedelta(minutes=1)
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
                    decision = self._exit_decision(
                        position, current[-1], now, visible=visible
                    )
                    if decision is None:
                        continue
                    exit_reason, exit_reference = decision
                    exit_fill = self._fill(exit_reference, "sell", spread_bps, slippage_bps)
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
                            if cost_basis > 0 else 0.0,
                            "quality_score": position.quality_score,
                            "exit_reason": exit_reason,
                        }
                    )
                    del positions[symbol]
                    last_exit[symbol] = now
                    exited_this_cycle = True

                marks = self._marks(visible, positions)
                account = self._account(cash, prior_close_equity, positions, marks)
                equity_curve.append(
                    {
                        "at": now.isoformat(),
                        "equity": str(d(account["equity"])),
                    }
                )

                # Production manages exits before considering any new entries.
                if exited_this_cycle:
                    continue
                if not (self.settings.entry_start <= now.time() <= self.settings.entry_cutoff):
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
                active_universe = (
                    next(
                        (symbols for at, symbols in reversed(snapshots_for_day) if at <= now),
                        set(),
                    )
                    if self.settings.dynamic_universe_enabled
                    else set(self.settings.scan_symbols)
                )
                if self.settings.dynamic_universe_enabled and not active_universe:
                    counters["universe_snapshot_missing_cycles"] += 1
                for symbol in sorted(active_universe):
                    signal = self.strategy.evaluate(
                        bars=visible.get(symbol, []),
                        confirmation_bars=confirmation_bars,
                        symbol=symbol,
                        has_position=symbol in positions,
                        order_notional=self.settings.order_notional,
                        now=now,
                    )
                    if signal.action != "buy":
                        if (signal.metadata or {}).get("research_rule_rejected") is True:
                            counters["research_rule_blocks"] += 1
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
                    signal.metadata["relative_volume_ratio"] = ranking["relative_volume_ratio"]
                    signal.metadata["trend_persistence"] = ranking["trend_persistence"]
                    counters["signals_scored"] += 1
                    if Decimal(str(ranking["score"])) < self.settings.min_quality_score:
                        counters["quality_blocks"] += 1
                        continue
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
                    if latest_exit is not None and self.settings.reentry_cooldown_minutes > 0:
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
                    account = self._account(cash, prior_close_equity, positions, marks)
                    payloads = self._position_payloads(positions, marks)
                    notional = calculate_entry_notional(
                        self.settings,
                        account,
                        payloads,
                    )
                    risk_allowed, _ = self._replay_risk(
                        symbol,
                        notional,
                        account,
                        payloads,
                        entries_today,
                        active_entry_universe=active_universe,
                    )
                    if not risk_allowed:
                        counters["risk_blocks"] += 1
                        continue

                    reference = signal.reference_price
                    if reference <= 0:
                        continue
                    entry_fill = self._fill(reference, "buy", spread_bps, slippage_bps)
                    # Match live execution: quantity is derived from the signal
                    # reference price, while the simulated fill determines cash cost.
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
                        risk_stop_pct=d(
                            (signal.metadata or {}).get("effective_stop_pct")
                            or self.settings.stop_pct
                        ),
                    )
                    entries_today += 1
                    planned += 1
                    counters["entries"] += 1

            # Fail-safe end-of-session flatten for incomplete historical sessions.
            if positions:
                final_time = timeline[-1] + timedelta(minutes=1)
                for symbol in list(positions):
                    current = visible.get(symbol, [])
                    if not current:
                        continue
                    position = positions[symbol]
                    exit_reference = price(current[-1])
                    exit_fill = self._fill(exit_reference, "sell", spread_bps, slippage_bps)
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
                            if cost_basis > 0 else 0.0,
                            "quality_score": position.quality_score,
                            "exit_reason": "session_end",
                        }
                    )
                    del positions[symbol]

            prior_close_equity = cash
            equity_curve.append(
                {
                    "at": datetime.combine(
                        session_day,
                        time(16, 0),
                        tzinfo=NY,
                    ).isoformat(),
                    "equity": str(cash),
                }
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
                "same_strategy_logic": False,
                "broker_orders_possible": False,
                "live_execution_parity": "UNVERIFIED",
                "research_only": True,
                "production_promotion_authorized": False,
                "dynamic_universe_asof_required": self.settings.dynamic_universe_enabled,
                "dynamic_universe_asof_supplied": bool(asof_universe),
                "universe_snapshot_fingerprint": (
                    sha256(
                        json.dumps(
                            [
                                [at.isoformat(), sorted(symbols)]
                                for at, symbols in asof_universe
                            ],
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    if asof_universe else None
                ),
                "protection_model": "prior_completed_bar_ratchet",
                "thesis_model": "distinct_completed_bar_approximation",
                "fidelity_limitations": [
                    "No historical broker-order, fill, or standing-stop reconstruction.",
                    "Bar-only simulation has uncertain intrabar ordering and stop fills.",
                    "Historical quote spreads, live account/risk and scan selection can differ.",
                    "Replay is an offline approximation, not a production parity certificate.",
                ],
            },
            "strategy": {
                "name": self.settings.strategy_name,
                "scan_symbols": list(self.settings.scan_symbols),
                "fast_window": self.settings.fast_window,
                "slow_window": self.settings.slow_window,
                "stop_pct": str(self.settings.stop_pct),
                "target_pct": str(self.settings.target_pct),
                "max_hold_minutes": self.settings.max_hold_minutes,
                "max_concurrent_positions": self.settings.max_concurrent_positions,
                "max_new_entries_per_cycle": self.settings.max_new_entries_per_cycle,
                "max_daily_orders": self.settings.max_daily_orders,
                "max_daily_loss": str(self.settings.max_daily_loss),
                "max_pairwise_correlation": str(self.settings.max_pairwise_correlation),
                "sizing_mode": self.settings.sizing_mode,
                "risk_per_trade_pct": str(self.settings.risk_per_trade_pct),
                "max_gross_exposure_pct": str(self.settings.max_gross_exposure_pct),
            },
            "sessions": len(sessions),
            "trades": trades,
            "equity_curve": equity_curve,
        }


class ReplayLab:
    def __init__(
        self,
        settings: Settings,
        market_data: MarketDataClient,
        strategy: Any,
    ):
        self.settings = settings
        self.market_data = market_data
        self.engine = ReplayEngine(settings, strategy)

    @staticmethod
    def _parse_date(raw: str) -> date:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("replay dates must use YYYY-MM-DD") from exc

    async def run(
        self,
        *,
        start: str,
        end: str,
        initial_equity: Decimal = Decimal("100"),
        spread_bps: Decimal = Decimal("5"),
        slippage_bps: Decimal = Decimal("2"),
    ) -> dict[str, Any]:
        start_day = self._parse_date(start)
        end_day = self._parse_date(end)
        if end_day < start_day:
            raise ValueError("end date must be on or after start date")
        calendar_days = (end_day - start_day).days + 1
        if calendar_days > 21:
            raise ValueError("replay range is limited to 21 calendar days per run")

        start_dt = datetime.combine(start_day, time(0, 0), tzinfo=NY).astimezone(timezone.utc)
        end_dt = datetime.combine(
            end_day + timedelta(days=1),
            time(0, 0),
            tzinfo=NY,
        ).astimezone(timezone.utc)

        symbols = list(
            dict.fromkeys(
                [*self.settings.scan_symbols, *self.settings.confirmation_symbols]
            )
        )
        bars = await self.market_data.historical_bars_many(
            symbols,
            start=start_dt,
            end=end_dt,
        )
        result = self.engine.run(
            bars,
            initial_equity=initial_equity,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
        )
        result["range"] = {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
        }
        return result
