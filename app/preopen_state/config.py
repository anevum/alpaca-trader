from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PreOpenSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    data_base_url: str = "https://data.alpaca.markets"
    data_feed: str = "iex"

    preopen_state_enabled: bool = False
    preopen_shadow_only: bool = True
    preopen_target_symbols: str = "SPY,QQQ"
    preopen_proxy_symbols: str = "FEZ,EWJ,EEM,TLT,UUP,USO,GLD,VXX"
    preopen_poll_seconds: int = 20
    preopen_capture_grace_minutes: int = 12
    preopen_model_json: str = ""

    trading_ingest_url: str = ""
    trading_ingest_token: str = ""

    @property
    def target_symbols(self) -> tuple[str, ...]:
        return _symbols(self.preopen_target_symbols)

    @property
    def proxy_symbols(self) -> tuple[str, ...]:
        return _symbols(self.preopen_proxy_symbols)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.target_symbols, *self.proxy_symbols)))

    @property
    def credentials_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def persistence_enabled(self) -> bool:
        return bool(self.trading_ingest_url and self.trading_ingest_token)


def _symbols(raw: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in raw.split(",")
            if item.strip()
        )
    )


@lru_cache
def get_preopen_settings() -> PreOpenSettings:
    return PreOpenSettings()
