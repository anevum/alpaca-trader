"""Read-only intraday preview using the existing RHEN market-data adapter."""
from datetime import datetime, timezone
import os

from .btc_day_strategy import BtcDayTradeStrategy


class BtcDayPreview:
    def __init__(self, data, settings):
        self.data, self.settings = data, settings
        self.strategy = BtcDayTradeStrategy()
        self.snapshot = {"status": "DISABLED", "strategy_version_id": self.strategy.strategy_version_id,
                         "broker_writes_allowed": False, "live_execution_authorized": False}

    async def observe(self):
        if os.getenv("BTC_DAY_PREVIEW_ENABLED", "false").lower() != "true": return
        now = datetime.now(timezone.utc)
        try:
            bars = await self.data.bars_many(["BTC/USD"], timeframe="15Min", lookback_minutes=4320)
            quotes = await self.data.latest_quotes(["BTC/USD"])
            now = datetime.now(timezone.utc)
            signal = self.strategy.evaluate(bars=bars.get("BTC/USD", []), confirmation_bars={}, symbol="BTC/USD",
                has_position=False, order_notional=self.settings.crypto_order_notional, now=now)
            if signal.action == "buy": signal = self.strategy.friction_check(signal, quotes.get("BTC/USD", {}), self.settings, now)
            self.snapshot = {"status": "UNVALIDATED_PREVIEW", "observed_at": now.isoformat(),
                "strategy_version_id": self.strategy.strategy_version_id, "parameter_fingerprint": self.strategy.parameter_fingerprint,
                "action": signal.action, "reason": signal.reason, "metadata": signal.metadata,
                "stop_pct": str(self.strategy.hard_stop_pct), "target_pct": str(self.strategy.take_profit_pct),
                "max_hold_minutes": self.strategy.max_hold_minutes, "live_execution_authorized": False,
                "account_assumption": "flat_hypothesis_only",
                "cost_assumptions": {"minimum_round_trip_fee_pct": "0.005", "minimum_slippage_pct": "0.001"},
                "broker_writes_allowed": False, "activation_blockers": ["independent_replay_and_forward_paper_not_passed", "explicit_live_release_not_approved"]}
        except Exception as exc:
            self.snapshot = {"status": "DATA_UNAVAILABLE", "error": type(exc).__name__,
                "observed_at": now.isoformat(), "strategy_version_id": self.strategy.strategy_version_id,
                "broker_writes_allowed": False, "live_execution_authorized": False}
