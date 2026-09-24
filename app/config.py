from decimal import Decimal
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")

    # paper | live selects the Alpaca account endpoint.
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    # Execution gates. Paper and live both require EXECUTION_ENABLED + BOT_ARMED.
    # Live additionally requires LIVE_TRADING + the explicit acknowledgement.
    execution_enabled: bool = Field(default=False, alias="EXECUTION_ENABLED")
    live_trading: bool = Field(default=False, alias="LIVE_TRADING")
    acknowledge_live: str = Field(default="NO", alias="I_ACKNOWLEDGE_LIVE_TRADING")
    bot_armed: bool = Field(default=False, alias="BOT_ARMED")

    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    min_ready_cash: Decimal = Field(default=Decimal("10.00"), alias="MIN_READY_CASH")
    max_order_notional: Decimal = Field(default=Decimal("5.00"), alias="MAX_ORDER_NOTIONAL")
    max_position_notional: Decimal = Field(default=Decimal("10.00"), alias="MAX_POSITION_NOTIONAL")
    max_daily_orders: int = Field(default=2, alias="MAX_DAILY_ORDERS")
    max_daily_loss: Decimal = Field(default=Decimal("2.00"), alias="MAX_DAILY_LOSS")

    allowed_symbols_raw: str = Field(default="", alias="ALLOWED_SYMBOLS")
    strategy_name: str = Field(default="sma_cross", alias="STRATEGY_NAME")
    strategy_symbol: str = Field(default="", alias="STRATEGY_SYMBOL")
    order_notional: Decimal = Field(default=Decimal("5.00"), alias="ORDER_NOTIONAL")
    fast_window: int = Field(default=20, alias="FAST_WINDOW")
    slow_window: int = Field(default=50, alias="SLOW_WINDOW")
    bar_timeframe: str = Field(default="5Min", alias="BAR_TIMEFRAME")
    lookback_bars: int = Field(default=120, alias="LOOKBACK_BARS")
    lookback_days: int = Field(default=30, alias="LOOKBACK_DAYS")
    data_feed: str = Field(default="iex", alias="DATA_FEED")

    poll_seconds: int = Field(default=60, alias="POLL_SECONDS")

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
        return {s.strip().upper() for s in self.allowed_symbols_raw.split(",") if s.strip()}

    @property
    def normalized_strategy_symbol(self) -> str:
        return self.strategy_symbol.strip().upper()

    @property
    def credentials_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def paper_execution_authorized(self) -> bool:
        return (
            self.trading_mode == "paper"
            and self.execution_enabled
            and self.bot_armed
        )

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
        if self.strategy_name != "sma_cross":
            raise ValueError("Only STRATEGY_NAME=sma_cross is supported")
        if self.data_feed not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("DATA_FEED must be iex, sip, or delayed_sip")
        if self.poll_seconds < 15:
            raise ValueError("POLL_SECONDS must be at least 15")
        if self.fast_window < 2 or self.slow_window <= self.fast_window:
            raise ValueError("Require 2 <= FAST_WINDOW < SLOW_WINDOW")
        if self.lookback_bars < self.slow_window + 2:
            raise ValueError("LOOKBACK_BARS must exceed SLOW_WINDOW")
        if self.lookback_days < 1:
            raise ValueError("LOOKBACK_DAYS must be positive")
        if self.min_ready_cash < 0:
            raise ValueError("MIN_READY_CASH cannot be negative")
        if self.order_notional <= 0:
            raise ValueError("ORDER_NOTIONAL must be positive")
        if self.order_notional > self.max_order_notional:
            raise ValueError("ORDER_NOTIONAL cannot exceed MAX_ORDER_NOTIONAL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
