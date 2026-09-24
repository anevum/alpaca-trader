from decimal import Decimal
from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")

    # Selects which Alpaca account the monitor reads. Live monitoring is allowed
    # without enabling order execution.
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    # Reserved execution guards. No order-placement code exists in this bootstrap.
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
    poll_seconds: int = Field(default=15, alias="POLL_SECONDS")

    @property
    def base_url(self) -> str:
        if self.trading_mode == "live":
            return "https://api.alpaca.markets"
        return "https://paper-api.alpaca.markets"

    @property
    def allowed_symbols(self) -> set[str]:
        return {s.strip().upper() for s in self.allowed_symbols_raw.split(",") if s.strip()}

    @property
    def credentials_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def live_execution_authorized(self) -> bool:
        return (
            self.trading_mode == "live"
            and self.live_trading
            and self.acknowledge_live == "YES"
            and self.bot_armed
        )

    @model_validator(mode="after")
    def validate_settings(self):
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be paper or live")
        if self.poll_seconds < 5:
            raise ValueError("POLL_SECONDS must be at least 5")
        if self.min_ready_cash < 0:
            raise ValueError("MIN_READY_CASH cannot be negative")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
