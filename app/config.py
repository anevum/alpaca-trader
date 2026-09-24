from datetime import time
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

    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    min_ready_cash: Decimal = Field(default=Decimal("10.00"), alias="MIN_READY_CASH")
    max_order_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_ORDER_NOTIONAL")
    max_position_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_POSITION_NOTIONAL")
    max_daily_orders: int = Field(default=2, alias="MAX_DAILY_ORDERS")
    max_daily_loss: Decimal = Field(default=Decimal("1.00"), alias="MAX_DAILY_LOSS")

    allowed_symbols_raw: str = Field(default="SPY", alias="ALLOWED_SYMBOLS")
    scan_symbols_raw: str = Field(default="", alias="SCAN_SYMBOLS")
    strategy_name: str = Field(default="opening_range_vwap", alias="STRATEGY_NAME")
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

    bar_timeframe: str = Field(default="1Min", alias="BAR_TIMEFRAME")
    lookback_bars: int = Field(default=500, alias="LOOKBACK_BARS")
    lookback_days: int = Field(default=2, alias="LOOKBACK_DAYS")
    data_feed: str = Field(default="iex", alias="DATA_FEED")
    poll_seconds: int = Field(default=15, alias="POLL_SECONDS")

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
        if self.strategy_name != "opening_range_vwap":
            raise ValueError("Only STRATEGY_NAME=opening_range_vwap is supported")
        if self.data_feed not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("DATA_FEED must be iex, sip, or delayed_sip")
        if self.poll_seconds < 15:
            raise ValueError("POLL_SECONDS must be at least 15")
        if self.bar_timeframe != "1Min":
            raise ValueError("BAR_TIMEFRAME must be 1Min for the opening-range strategy")
        if self.lookback_bars < 50:
            raise ValueError("LOOKBACK_BARS must be at least 50")
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
        if not self.scan_symbols:
            raise ValueError("SCAN_SYMBOLS/STRATEGY_SYMBOL cannot both be empty")
        if len(self.scan_symbols) > 30:
            raise ValueError("SCAN_SYMBOLS supports at most 30 symbols")
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
