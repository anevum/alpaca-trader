from datetime import datetime, time, timezone
from decimal import Decimal
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_hhmm(value: str) -> time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return time(int(hour_text), int(minute_text))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid HH:MM time: {value}") from exc


def parse_csv(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in value.split(","):
        symbol = raw.strip().upper()
        if symbol and symbol not in seen:
            result.append(symbol)
            seen.add(symbol)
    return tuple(result)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")

    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    execution_enabled: bool = Field(default=False, alias="EXECUTION_ENABLED")
    live_trading: bool = Field(default=False, alias="LIVE_TRADING")
    acknowledge_live: str = Field(default="NO", alias="I_ACKNOWLEDGE_LIVE_TRADING")
    bot_armed: bool = Field(default=False, alias="BOT_ARMED")
    scan_only: bool = Field(default=False, alias="SCAN_ONLY")

    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    trading_ingest_url: str = Field(default="", alias="TRADING_INGEST_URL")
    trading_ingest_token: str = Field(default="", alias="TRADING_INGEST_TOKEN")
    trading_run_id: str = Field(default="", alias="TRADING_RUN_ID")
    strategy_version_id: str = Field(default="", alias="STRATEGY_VERSION_ID")
    trading_run_started_at_raw: str = Field(default="", alias="TRADING_RUN_STARTED_AT")
    ledger_reconcile_seconds: int = Field(default=60, alias="LEDGER_RECONCILE_SECONDS")

    min_ready_cash: Decimal = Field(default=Decimal("10.00"), alias="MIN_READY_CASH")
    max_order_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_ORDER_NOTIONAL")
    max_position_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_POSITION_NOTIONAL")
    max_daily_orders: int = Field(default=2, alias="MAX_DAILY_ORDERS")
    max_daily_loss: Decimal = Field(default=Decimal("1.00"), alias="MAX_DAILY_LOSS")
    max_concurrent_positions: int = Field(default=1, alias="MAX_CONCURRENT_POSITIONS")
    max_new_entries_per_cycle: int = Field(default=1, alias="MAX_NEW_ENTRIES_PER_CYCLE")
    max_total_position_notional: Decimal = Field(
        default=Decimal("80.35"), alias="MAX_TOTAL_POSITION_NOTIONAL"
    )

    # Small-capital allocator. Fixed sizing remains available as a fallback.
    sizing_mode: str = Field(default="fixed", alias="SIZING_MODE")
    risk_per_trade_pct: Decimal = Field(
        default=Decimal("0.001"), alias="RISK_PER_TRADE_PCT"
    )
    max_gross_exposure_pct: Decimal = Field(
        default=Decimal("1.0"), alias="MAX_GROSS_EXPOSURE_PCT"
    )
    min_order_notional: Decimal = Field(
        default=Decimal("1.00"), alias="MIN_ORDER_NOTIONAL"
    )

    portfolio_limit_mode: str = Field(default="count", alias="PORTFOLIO_LIMIT_MODE")
    max_position_gross_pct: Decimal = Field(
        default=Decimal("0.25"), alias="MAX_POSITION_GROSS_PCT"
    )
    max_portfolio_stop_risk_pct: Decimal = Field(
        default=Decimal("0.01"), alias="MAX_PORTFOLIO_STOP_RISK_PCT"
    )

    allowed_symbols_raw: str = Field(default="SPY", alias="ALLOWED_SYMBOLS")
    scan_symbols_raw: str = Field(default="", alias="SCAN_SYMBOLS")
    strategy_name: str = Field(default="opening_range_vwap", alias="STRATEGY_NAME")
    fast_window: int = Field(default=3, alias="FAST_WINDOW")
    slow_window: int = Field(default=8, alias="SLOW_WINDOW")
    min_momentum_pct: Decimal = Field(default=Decimal("0.0005"), alias="MIN_MOMENTUM_PCT")
    min_vwap_edge_pct: Decimal = Field(default=Decimal("0"), alias="MIN_VWAP_EDGE_PCT")
    strategy_symbol: str = Field(default="SPY", alias="STRATEGY_SYMBOL")
    confirmation_symbols_raw: str = Field(default="QQQ,SMH", alias="CONFIRMATION_SYMBOLS")
    min_confirmations: int = Field(default=1, alias="MIN_CONFIRMATIONS")

    order_notional: Decimal = Field(default=Decimal("80.00"), alias="ORDER_NOTIONAL")
    opening_range_minutes: int = Field(default=5, alias="OPENING_RANGE_MINUTES")
    max_opening_range_pct: Decimal = Field(
        default=Decimal("0.012"), alias="MAX_OPENING_RANGE_PCT"
    )
    max_breakout_extension_pct: Decimal = Field(
        default=Decimal("0.004"), alias="MAX_BREAKOUT_EXTENSION_PCT"
    )
    stop_pct: Decimal = Field(default=Decimal("0.006"), alias="STOP_PCT")
    target_pct: Decimal = Field(default=Decimal("0.0108"), alias="TARGET_PCT")
    entry_start_raw: str = Field(default="09:35", alias="ENTRY_START")
    entry_cutoff_raw: str = Field(default="11:30", alias="ENTRY_CUTOFF")
    force_flat_time_raw: str = Field(default="15:55", alias="FORCE_FLAT_TIME")
    max_hold_minutes: int = Field(default=0, alias="MAX_HOLD_MINUTES")
    reentry_cooldown_minutes: int = Field(default=0, alias="REENTRY_COOLDOWN_MINUTES")

    bar_timeframe: str = Field(default="1Min", alias="BAR_TIMEFRAME")
    lookback_bars: int = Field(default=500, alias="LOOKBACK_BARS")
    lookback_days: int = Field(default=2, alias="LOOKBACK_DAYS")
    data_feed: str = Field(default="iex", alias="DATA_FEED")
    poll_seconds: int = Field(default=15, alias="POLL_SECONDS")
    max_bar_age_seconds: int = Field(default=90, alias="MAX_BAR_AGE_SECONDS")
    market_data_batch_size: int = Field(default=25, alias="MARKET_DATA_BATCH_SIZE")
    max_spread_pct: Decimal = Field(default=Decimal("0.002"), alias="MAX_SPREAD_PCT")
    min_quality_score: Decimal = Field(default=Decimal("0"), alias="MIN_QUALITY_SCORE")

    max_pairwise_correlation: Decimal = Field(
        default=Decimal("0.85"), alias="MAX_PAIRWISE_CORRELATION"
    )
    correlation_lookback_bars: int = Field(
        default=30, alias="CORRELATION_LOOKBACK_BARS"
    )
    correlation_min_observations: int = Field(
        default=8, alias="CORRELATION_MIN_OBSERVATIONS"
    )

    @property
    def base_url(self) -> str:
        if self.trading_mode == "live":
            return "https://api.alpaca.markets"
        return "https://paper-api.alpaca.markets"

    @property
    def data_base_url(self) -> str:
        return "https://data.alpaca.markets"

    @property
    def allowed_symbols(self) -> set[str]:
        return set(parse_csv(self.allowed_symbols_raw))

    @property
    def normalized_strategy_symbol(self) -> str:
        return self.strategy_symbol.strip().upper()

    @property
    def scan_symbols(self) -> tuple[str, ...]:
        configured = parse_csv(self.scan_symbols_raw)
        if configured:
            return configured
        symbol = self.normalized_strategy_symbol
        return (symbol,) if symbol else ()

    @property
    def confirmation_symbols(self) -> tuple[str, ...]:
        return parse_csv(self.confirmation_symbols_raw)

    @property
    def entry_start(self) -> time:
        return parse_hhmm(self.entry_start_raw)

    @property
    def entry_cutoff(self) -> time:
        return parse_hhmm(self.entry_cutoff_raw)

    @property
    def force_flat_time(self) -> time:
        return parse_hhmm(self.force_flat_time_raw)

    @property
    def credentials_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def persistence_configured(self) -> bool:
        return bool(
            self.trading_ingest_url
            and self.trading_ingest_token
            and self.trading_run_id
            and self.strategy_version_id
        )

    @property
    def trading_run_started_at(self) -> datetime | None:
        raw = self.trading_run_started_at_raw.strip()
        if not raw:
            return None
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("TRADING_RUN_STARTED_AT must include a timezone")
        return stamp.astimezone(timezone.utc)

    @property
    def paper_execution_authorized(self) -> bool:
        return self.trading_mode == "paper" and self.execution_enabled and self.bot_armed

    @property
    def live_execution_authorized(self) -> bool:
        return (
            self.trading_mode == "live"
            and self.execution_enabled
            and self.live_trading
            and self.acknowledge_live == "YES"
            and self.bot_armed
        )

    @property
    def execution_authorized(self) -> bool:
        return self.paper_execution_authorized or self.live_execution_authorized

    @model_validator(mode="after")
    def validate_settings(self):
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be paper or live")
        if self.strategy_name not in {"opening_range_vwap", "rolling_momentum_vwap"}:
            raise ValueError("STRATEGY_NAME must be opening_range_vwap or rolling_momentum_vwap")
        if self.data_feed not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("DATA_FEED must be iex, sip, or delayed_sip")
        if self.poll_seconds < 15:
            raise ValueError("POLL_SECONDS must be at least 15")
        if not 30 <= self.ledger_reconcile_seconds <= 300:
            raise ValueError("LEDGER_RECONCILE_SECONDS must be between 30 and 300")
        if self.persistence_configured:
            if not self.trading_run_started_at_raw.strip():
                raise ValueError(
                    "TRADING_RUN_STARTED_AT is required when persistence is configured"
                )
            try:
                _ = self.trading_run_started_at
            except ValueError as exc:
                raise ValueError(
                    "TRADING_RUN_STARTED_AT must be an ISO-8601 timestamp with timezone"
                ) from exc
        if self.bar_timeframe != "1Min":
            raise ValueError("BAR_TIMEFRAME must be 1Min for the opening-range strategy")
        if self.lookback_bars < 50:
            raise ValueError("LOOKBACK_BARS must be at least 50")
        if not 1 <= self.fast_window < self.slow_window <= 60:
            raise ValueError("FAST_WINDOW and SLOW_WINDOW must satisfy 1 <= FAST_WINDOW < SLOW_WINDOW <= 60")
        if not Decimal("0") <= self.min_momentum_pct < Decimal("0.05"):
            raise ValueError("MIN_MOMENTUM_PCT must be between 0 and 0.05")
        if not Decimal("0") <= self.min_vwap_edge_pct < Decimal("0.05"):
            raise ValueError("MIN_VWAP_EDGE_PCT must be between 0 and 0.05")
        if self.lookback_days < 1:
            raise ValueError("LOOKBACK_DAYS must be positive")
        if not 1 <= self.opening_range_minutes <= 30:
            raise ValueError("OPENING_RANGE_MINUTES must be between 1 and 30")
        if not Decimal("0") < self.max_opening_range_pct < Decimal("0.10"):
            raise ValueError("MAX_OPENING_RANGE_PCT must be between 0 and 0.10")
        if not Decimal("0") < self.max_breakout_extension_pct < Decimal("0.05"):
            raise ValueError("MAX_BREAKOUT_EXTENSION_PCT must be between 0 and 0.05")
        if not Decimal("0") < self.stop_pct < Decimal("0.10"):
            raise ValueError("STOP_PCT must be between 0 and 0.10")
        if not Decimal("0") < self.target_pct < Decimal("0.20"):
            raise ValueError("TARGET_PCT must be between 0 and 0.20")
        if self.entry_start >= self.entry_cutoff:
            raise ValueError("ENTRY_START must be before ENTRY_CUTOFF")
        if self.entry_cutoff >= self.force_flat_time:
            raise ValueError("ENTRY_CUTOFF must be before FORCE_FLAT_TIME")
        if self.min_ready_cash < 0:
            raise ValueError("MIN_READY_CASH cannot be negative")
        if self.order_notional <= 0:
            raise ValueError("ORDER_NOTIONAL must be positive")
        if self.order_notional > self.max_order_notional:
            raise ValueError("ORDER_NOTIONAL cannot exceed MAX_ORDER_NOTIONAL")
        if self.max_position_notional < self.order_notional:
            raise ValueError("MAX_POSITION_NOTIONAL cannot be below ORDER_NOTIONAL")
        if self.max_daily_orders < 0:
            raise ValueError("MAX_DAILY_ORDERS cannot be negative")
        if self.max_daily_loss <= 0:
            raise ValueError("MAX_DAILY_LOSS must be positive")
        if self.portfolio_limit_mode not in {"count", "risk"}:
            raise ValueError("PORTFOLIO_LIMIT_MODE must be count or risk")
        if self.portfolio_limit_mode == "count":
            if not 1 <= self.max_concurrent_positions <= 100:
                raise ValueError("MAX_CONCURRENT_POSITIONS must be between 1 and 100")
            if not 1 <= self.max_new_entries_per_cycle <= self.max_concurrent_positions:
                raise ValueError(
                    "MAX_NEW_ENTRIES_PER_CYCLE must be between 1 and MAX_CONCURRENT_POSITIONS"
                )
        else:
            if self.max_concurrent_positions < 0:
                raise ValueError("MAX_CONCURRENT_POSITIONS cannot be negative")
            if self.max_new_entries_per_cycle < 0:
                raise ValueError("MAX_NEW_ENTRIES_PER_CYCLE cannot be negative")
            if self.sizing_mode != "equity_risk":
                raise ValueError("PORTFOLIO_LIMIT_MODE=risk requires SIZING_MODE=equity_risk")
        if self.max_total_position_notional < self.order_notional:
            raise ValueError(
                "MAX_TOTAL_POSITION_NOTIONAL cannot be below ORDER_NOTIONAL"
            )
        if self.sizing_mode not in {"fixed", "equity_risk"}:
            raise ValueError("SIZING_MODE must be fixed or equity_risk")
        if not Decimal("0") < self.risk_per_trade_pct <= Decimal("0.02"):
            raise ValueError("RISK_PER_TRADE_PCT must be between 0 and 0.02")
        if not Decimal("0") < self.max_gross_exposure_pct <= Decimal("1"):
            raise ValueError("MAX_GROSS_EXPOSURE_PCT must be between 0 and 1")
        if self.min_order_notional <= 0:
            raise ValueError("MIN_ORDER_NOTIONAL must be positive")
        if self.min_order_notional > self.max_order_notional:
            raise ValueError("MIN_ORDER_NOTIONAL cannot exceed MAX_ORDER_NOTIONAL")
        if not Decimal("0") < self.max_position_gross_pct <= Decimal("1"):
            raise ValueError("MAX_POSITION_GROSS_PCT must be between 0 and 1")
        if not Decimal("0") < self.max_portfolio_stop_risk_pct <= Decimal("0.10"):
            raise ValueError("MAX_PORTFOLIO_STOP_RISK_PCT must be between 0 and 0.10")
        if not 30 <= self.max_bar_age_seconds <= 600:
            raise ValueError("MAX_BAR_AGE_SECONDS must be between 30 and 600")
        if not 1 <= self.market_data_batch_size <= 100:
            raise ValueError("MARKET_DATA_BATCH_SIZE must be between 1 and 100")
        if not Decimal("0") < self.max_spread_pct < Decimal("0.05"):
            raise ValueError("MAX_SPREAD_PCT must be between 0 and 0.05")
        if not Decimal("0") <= self.min_quality_score <= Decimal("100"):
            raise ValueError("MIN_QUALITY_SCORE must be between 0 and 100")
        if not Decimal("0") < self.max_pairwise_correlation <= Decimal("1"):
            raise ValueError("MAX_PAIRWISE_CORRELATION must be between 0 and 1")
        if not 8 <= self.correlation_lookback_bars <= 120:
            raise ValueError("CORRELATION_LOOKBACK_BARS must be between 8 and 120")
        if not 3 <= self.correlation_min_observations < self.correlation_lookback_bars:
            raise ValueError(
                "CORRELATION_MIN_OBSERVATIONS must be at least 3 and below CORRELATION_LOOKBACK_BARS"
            )
        if not self.scan_symbols:
            raise ValueError("SCAN_SYMBOLS/STRATEGY_SYMBOL cannot both be empty")
        missing = [symbol for symbol in self.scan_symbols if symbol not in self.allowed_symbols]
        if missing:
            raise ValueError(
                "Every SCAN_SYMBOLS symbol must also be in ALLOWED_SYMBOLS: "
                + ",".join(missing)
            )
        if not self.confirmation_symbols:
            raise ValueError("CONFIRMATION_SYMBOLS cannot be empty")
        if not 1 <= self.min_confirmations <= len(self.confirmation_symbols):
            raise ValueError("MIN_CONFIRMATIONS must be between 1 and the number of confirmation symbols")
        if not 0 <= self.max_hold_minutes <= 390:
            raise ValueError("MAX_HOLD_MINUTES must be between 0 and 390")
        if not 0 <= self.reentry_cooldown_minutes <= 60:
            raise ValueError("REENTRY_COOLDOWN_MINUTES must be between 0 and 60")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
