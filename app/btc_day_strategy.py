"""Intraday BTC design. Signal generation never grants broker authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from types import MappingProxyType

from .btc_direct_strategy import BtcDirectSwingStrategy
from .strategy import Signal


class BtcDayTradeStrategy:
    preview_only = True
    strategy_version_id = "RHEN-BTC-DAY-004"
    strategy_family = "btc_fee_aware_intraday_breakout"
    timeframe = regime_timeframe = "15Min"
    signal_timeframe = "15Min"
    required_history_minutes = regime_history_minutes = 3 * 24 * 60
    hard_stop_pct = Decimal("0.0125")
    take_profit_pct = Decimal("0.018")
    max_hold_minutes = 360
    manages_position_exits = True
    # These are frozen hypotheses, not claims of measured expectancy.
    parameters = {"interval_minutes": 15, "fast_ema": 8, "medium_ema": 21,
                  "slow_ema": 64, "breakout_bars": 16, "atr_bars": 14,
                  "warmup_bars": 96, "hard_stop_pct": "0.0125",
                  "take_profit_pct": "0.018", "max_hold_minutes": 360,
                  "minimum_round_trip_fee_pct": "0.005",
                  "minimum_slippage_pct": "0.001", "minimum_net_margin_pct": "0.003"}
    parameter_fingerprint = sha256(json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    parameters = MappingProxyType(parameters)

    @staticmethod
    def completed(bars, now):
        current = now.astimezone(timezone.utc)
        rows = {}
        for row in bars:
            try:
                stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("timezone_required")
                stamp = stamp.astimezone(timezone.utc)
                if stamp + timedelta(minutes=15) > current:
                    continue
                if stamp.minute % 15 or stamp.second or stamp.microsecond or stamp in rows:
                    raise ValueError("invalid_or_duplicate_15min_bar")
                values = {key: Decimal(str(row[key])) for key in ("o", "h", "l", "c")}
                if not all(v.is_finite() and v > 0 for v in values.values()) or values["h"] < max(values["o"], values["c"]) or values["l"] > min(values["o"], values["c"]):
                    raise ValueError("invalid_15min_ohlc")
                rows[stamp] = {**values, "t": stamp}
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                raise ValueError("intraday_input_invalid") from exc
        ordered = [rows[t] for t in sorted(rows)][-288:]
        if ordered:
            # No entry from stale data or a discontinuous indicator history.
            if current - (ordered[-1]["t"] + timedelta(minutes=15)) >= timedelta(minutes=15):
                raise ValueError("intraday_completed_bar_stale")
            tail = ordered[-96:]
            if any(b["t"] - a["t"] != timedelta(minutes=15) for a, b in zip(tail, tail[1:])):
                raise ValueError("intraday_warmup_gap")
        return ordered

    def evaluate(self, *, bars, confirmation_bars, symbol, has_position, order_notional, now=None):
        now = now or datetime.now(timezone.utc)
        meta = {"market": "crypto", "strategy_version_id": self.strategy_version_id,
                "strategy_family": self.strategy_family, "parameter_fingerprint": self.parameter_fingerprint,
                "timeframe": self.timeframe, "validation_state": "UNVALIDATED_DESIGN",
                "live_execution_authorized": False, "broker_writes_allowed": False}
        def hold(reason):
            return Signal(action="hold", symbol=symbol, reason=reason, metadata=meta)
        if symbol != "BTC/USD": return hold("intraday strategy is BTC/USD only")
        if has_position: return hold("existing BTC position; manage exits before new entry")
        try:
            rows = self.completed(bars, now)
        except ValueError as exc:
            return hold(str(exc))
        if len(rows) < 96: return hold(f"intraday warmup {len(rows)}/96 completed 15-minute bars")
        last = rows[-1]
        closes = [r["c"] for r in rows]
        fast, medium, slow = (BtcDirectSwingStrategy._ema(closes, n) for n in (8, 21, 64))
        prior_high = max(r["h"] for r in rows[-17:-1])
        ranges = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(rows[-15:-1], rows[-14:])]
        atr_pct = sum(ranges) / Decimal(len(ranges)) / last["c"]
        recent_range = (max(r["h"] for r in rows[-24:]) - min(r["l"] for r in rows[-24:])) / last["c"]
        meta["state"] = {"bar_time": last["t"].isoformat(), "close": str(last["c"]),
            "fast_ema": str(fast), "medium_ema": str(medium), "slow_ema": str(slow),
            "prior_4h_high": str(prior_high), "atr_pct": str(atr_pct),
            "observed_6h_range_pct": str(recent_range)}
        meta["opportunity_range_pct"] = str(recent_range)
        if not fast > medium > slow: return hold("intraday trend alignment absent")
        if last["c"] <= prior_high or last["c"] <= last["o"]: return hold("completed intraday breakout absent")
        if (last["c"] - prior_high) / last["c"] > atr_pct: return hold("intraday breakout already extended; no chase")
        if recent_range < self.take_profit_pct: return hold("observed six-hour range below gross target")
        return Signal(action="buy", symbol=symbol, notional=order_notional, reference_price=last["c"],
            stop_price=last["c"] * (1 - self.hard_stop_pct), take_profit_price=last["c"] * (1 + self.take_profit_pct),
            reason="completed 15-minute trend breakout with intraday movement budget", metadata=meta)

    def position_exit_reason(self, bars, regime_bars, *, now):
        try:
            rows = self.completed(bars, now)
        except ValueError:
            return None  # Never trade on invalid signal data; broker/software stops still apply.
        if len(rows) >= 21:
            closes = [r["c"] for r in rows]
            if BtcDirectSwingStrategy._ema(closes, 8) <= BtcDirectSwingStrategy._ema(closes, 21):
                return "intraday trend reversal"
        return None

    @staticmethod
    def friction_check(signal, quote, settings, now):
        """Use ask-priced entries and taker fees even for a marketable limit order."""
        meta = dict(signal.metadata)
        try:
            bid, ask = Decimal(str(quote["bp"])), Decimal(str(quote["ap"]))
            stamp = datetime.fromisoformat(str(quote["t"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None or not all(x.is_finite() and x > 0 for x in (bid, ask)) or ask < bid:
                raise ValueError("invalid_quote")
            age = (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            if age < -5 or age > settings.crypto_max_quote_age_seconds:
                raise ValueError("stale_or_future_quote")
            spread = (ask - bid) / ((ask + bid) / 2)
            fee = max(Decimal("0.005"), settings.crypto_estimated_round_trip_fee_pct)
            slippage = max(Decimal("0.001"), settings.crypto_estimated_round_trip_slippage_pct)
            margin = max(Decimal("0.003"), settings.crypto_min_net_edge_pct)
            target = signal.take_profit_price / ask - 1 if signal.take_profit_price > 0 else Decimal("0")
            depth = Decimal(str(quote.get("as", "0")))
            if not depth.is_finite() or depth < signal.notional / ask or spread > settings.crypto_max_spread_pct:
                raise ValueError("intraday_spread_or_depth_unavailable")
            cost = fee + slippage + spread
            meta["cost_model"] = {"round_trip_fee_pct": str(fee), "slippage_pct": str(slippage),
                "spread_pct": str(spread), "gross_target_from_ask_pct": str(target),
                "net_target_margin_pct": str(target - cost), "required_net_margin_pct": str(margin),
                "basis": "movement_budget_only_not_measured_expectancy"}
            if target <= cost + margin:
                raise ValueError("intraday_target_does_not_clear_friction")
            signal.reference_price = ask
            signal.stop_price = ask * (1 - BtcDayTradeStrategy.hard_stop_pct)
            signal.take_profit_price = ask * (1 + BtcDayTradeStrategy.take_profit_pct)
            signal.metadata = meta
            return signal
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            return Signal(action="hold", symbol=signal.symbol, reason=str(exc), metadata=meta)
