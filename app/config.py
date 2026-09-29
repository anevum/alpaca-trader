import hashlib
from datetime import datetime, time, timezone
from decimal import Decimal
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .cash_flow import SessionCashFlow


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

    # Optional outbound-only Slack Incoming Webhook for RHEN operational events.
    # Delivery is best-effort and never participates in trading decisions.
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")
    slack_webhook_timeout_seconds: float = Field(
        default=5.0, alias="SLACK_WEBHOOK_TIMEOUT_SECONDS"
    )

    trading_ingest_url: str = Field(default="", alias="TRADING_INGEST_URL")
    trading_ingest_token: str = Field(default="", alias="TRADING_INGEST_TOKEN")

    # PushWard is the active no-Xcode Lock Screen / Live Activity delivery path.
    # Best-effort display only; it never participates in trading decisions.
    pushward_api_key: str = Field(default="", alias="PUSHWARD_API_KEY")
    pushward_api_url: str = Field(
        default="https://api.pushward.app", alias="PUSHWARD_API_URL"
    )
    pushward_interval_seconds: int = Field(
        default=300, alias="PUSHWARD_INTERVAL_SECONDS"
    )
    pushward_heartbeat_seconds: int = Field(
        default=1800, alias="PUSHWARD_HEARTBEAT_SECONDS"
    )

    # IREN native / ActivityKit delivery. All APNs signing values remain server-side.
    iren_mobile_registry_url: str = Field(
        default="https://mfntzxheldzdvlokyntk.supabase.co/functions/v1/iren-mobile-registry",
        alias="IREN_MOBILE_REGISTRY_URL",
    )
    iren_public_feed_url: str = Field(
        default="https://mfntzxheldzdvlokyntk.supabase.co/functions/v1/trading-public-feed",
        alias="IREN_PUBLIC_FEED_URL",
    )
    iren_apns_team_id: str = Field(default="", alias="IREN_APNS_TEAM_ID")
    iren_apns_key_id: str = Field(default="", alias="IREN_APNS_KEY_ID")
    iren_apns_private_key: str = Field(default="", alias="IREN_APNS_PRIVATE_KEY")
    iren_bundle_id: str = Field(default="com.anevum.iren", alias="IREN_BUNDLE_ID")
    iren_mobile_push_interval_seconds: int = Field(
        default=30, alias="IREN_MOBILE_PUSH_INTERVAL_SECONDS"
    )
    iren_mobile_push_heartbeat_seconds: int = Field(
        default=300, alias="IREN_MOBILE_PUSH_HEARTBEAT_SECONDS"
    )
    trading_run_id: str = Field(default="", alias="TRADING_RUN_ID")
    strategy_version_id: str = Field(default="", alias="STRATEGY_VERSION_ID")
    trading_run_started_at_raw: str = Field(default="", alias="TRADING_RUN_STARTED_AT")
    ledger_reconcile_seconds: int = Field(default=60, alias="LEDGER_RECONCILE_SECONDS")

    min_ready_cash: Decimal = Field(default=Decimal("10.00"), alias="MIN_READY_CASH")
    max_order_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_ORDER_NOTIONAL")
    max_position_notional: Decimal = Field(default=Decimal("80.35"), alias="MAX_POSITION_NOTIONAL")
    max_daily_orders: int = Field(default=2, alias="MAX_DAILY_ORDERS")
    max_daily_loss: Decimal = Field(default=Decimal("1.00"), alias="MAX_DAILY_LOSS")
    session_cash_flow_adjustment_raw: str = Field(
        default="", alias="SESSION_CASH_FLOW_ADJUSTMENT"
    )
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
    dynamic_universe_enabled: bool = Field(
        default=False, alias="DYNAMIC_UNIVERSE_ENABLED"
    )
    universe_size: int = Field(default=100, alias="UNIVERSE_SIZE")
    universe_candidate_pool_size: int = Field(
        default=300, alias="UNIVERSE_CANDIDATE_POOL_SIZE"
    )
    universe_refresh_seconds: int = Field(
        default=300, alias="UNIVERSE_REFRESH_SECONDS"
    )
    universe_daily_lookback: int = Field(
        default=5, alias="UNIVERSE_DAILY_LOOKBACK"
    )
    universe_data_batch_size: int = Field(
        default=50, alias="UNIVERSE_DATA_BATCH_SIZE"
    )
    universe_min_price: Decimal = Field(
        default=Decimal("2.00"), alias="UNIVERSE_MIN_PRICE"
    )
    universe_min_avg_volume: Decimal = Field(
        default=Decimal("10000"), alias="UNIVERSE_MIN_AVG_VOLUME"
    )
    universe_min_avg_dollar_volume: Decimal = Field(
        default=Decimal("500000"), alias="UNIVERSE_MIN_AVG_DOLLAR_VOLUME"
    )
    universe_exchanges_raw: str = Field(
        default="NASDAQ,NYSE,ARCA,AMEX,BATS", alias="UNIVERSE_EXCHANGES"
    )
    universe_always_include_raw: str = Field(
        default="SPY,QQQ,SMH", alias="UNIVERSE_ALWAYS_INCLUDE"
    )

    crypto_lane_enabled: bool = Field(default=False, alias="CRYPTO_LANE_ENABLED")
    crypto_execution_enabled: bool = Field(
        default=False, alias="CRYPTO_EXECUTION_ENABLED"
    )
    crypto_location: str = Field(default="us", alias="CRYPTO_LOCATION")
    crypto_universe_size: int = Field(default=12, alias="CRYPTO_UNIVERSE_SIZE")
    crypto_universe_refresh_seconds: int = Field(
        default=300, alias="CRYPTO_UNIVERSE_REFRESH_SECONDS"
    )
    crypto_poll_seconds: int = Field(default=30, alias="CRYPTO_POLL_SECONDS")
    crypto_lookback_minutes: int = Field(
        default=240, alias="CRYPTO_LOOKBACK_MINUTES"
    )
    crypto_quote_currencies_raw: str = Field(
        default="USD", alias="CRYPTO_QUOTE_CURRENCIES"
    )
    crypto_excluded_bases_raw: str = Field(
        default="USDC,USDT,USDG", alias="CRYPTO_EXCLUDED_BASES"
    )
    crypto_always_include_raw: str = Field(
        default="BTC/USD,ETH/USD,SOL/USD", alias="CRYPTO_ALWAYS_INCLUDE"
    )
    crypto_confirmation_symbols_raw: str = Field(
        default="BTC/USD,ETH/USD", alias="CRYPTO_CONFIRMATION_SYMBOLS"
    )
    crypto_strategy_version_id: str = Field(
        default="CRYPTO-2026-09-29-001", alias="CRYPTO_STRATEGY_VERSION_ID"
    )
    crypto_order_notional: Decimal = Field(
        default=Decimal("5.00"), alias="CRYPTO_ORDER_NOTIONAL"
    )
    crypto_max_order_notional: Decimal = Field(
        default=Decimal("5.00"), alias="CRYPTO_MAX_ORDER_NOTIONAL"
    )
    crypto_max_total_position_notional: Decimal = Field(
        default=Decimal("10.00"), alias="CRYPTO_MAX_TOTAL_POSITION_NOTIONAL"
    )
    crypto_max_concurrent_positions: int = Field(
        default=1, alias="CRYPTO_MAX_CONCURRENT_POSITIONS"
    )
    crypto_max_entries_24h: int = Field(
        default=4, alias="CRYPTO_MAX_ENTRIES_24H"
    )
    crypto_max_spread_pct: Decimal = Field(
        default=Decimal("0.005"), alias="CRYPTO_MAX_SPREAD_PCT"
    )
    crypto_stop_pct: Decimal = Field(
        default=Decimal("0.0035"), alias="CRYPTO_STOP_PCT"
    )
    crypto_target_pct: Decimal = Field(
        default=Decimal("0.005"), alias="CRYPTO_TARGET_PCT"
    )
    crypto_stop_limit_buffer_pct: Decimal = Field(
        default=Decimal("0.0025"), alias="CRYPTO_STOP_LIMIT_BUFFER_PCT"
    )
    crypto_max_hold_minutes: int = Field(
        default=60, alias="CRYPTO_MAX_HOLD_MINUTES"
    )
    crypto_reentry_cooldown_minutes: int = Field(
        default=15, alias="CRYPTO_REENTRY_COOLDOWN_MINUTES"
    )

    strategy_name: str = Field(default="opening_range_vwap", alias="STRATEGY_NAME")
    fast_window: int = Field(default=3, alias="FAST_WINDOW")
    slow_window: int = Field(default=8, alias="SLOW_WINDOW")
    min_momentum_pct: Decimal = Field(default=Decimal("0.0005"), alias="MIN_MOMENTUM_PCT")
    min_vwap_edge_pct: Decimal = Field(default=Decimal("0"), alias="MIN_VWAP_EDGE_PCT")
    strategy_symbol: str = Field(default="SPY", alias="STRATEGY_SYMBOL")
    confirmation_symbols_raw: str = Field(default="QQQ,SMH", alias="CONFIRMATION_SYMBOLS")
    min_confirmations: int = Field(default=1, alias="MIN_CONFIRMATIONS")
    regime_window: int = Field(default=5, alias="REGIME_WINDOW")
    regime_min_confirmations: int = Field(default=1, alias="REGIME_MIN_CONFIRMATIONS")
    regime_min_return_pct: Decimal = Field(
        default=Decimal("0"), alias="REGIME_MIN_RETURN_PCT"
    )
    max_vwap_extension_pct: Decimal = Field(
        default=Decimal("0.008"), alias="MAX_VWAP_EXTENSION_PCT"
    )
    loss_streak_limit: int = Field(default=2, alias="LOSS_STREAK_LIMIT")
    loss_streak_cooldown_minutes: int = Field(
        default=10, alias="LOSS_STREAK_COOLDOWN_MINUTES"
    )

    broker_protective_stop_enabled: bool = Field(
        default=False, alias="BROKER_PROTECTIVE_STOP_ENABLED"
    )
    volatility_stop_enabled: bool = Field(
        default=False, alias="VOLATILITY_STOP_ENABLED"
    )
    volatility_stop_multiplier: Decimal = Field(
        default=Decimal("2.0"), alias="VOLATILITY_STOP_MULTIPLIER"
    )
    volatility_stop_lookback_bars: int = Field(
        default=8, alias="VOLATILITY_STOP_LOOKBACK_BARS"
    )
    max_dynamic_stop_pct: Decimal = Field(
        default=Decimal("0.006"), alias="MAX_DYNAMIC_STOP_PCT"
    )
    profit_protect_enabled: bool = Field(
        default=False, alias="PROFIT_PROTECT_ENABLED"
    )
    profit_protect_activation_pct: Decimal = Field(
        default=Decimal("0.001"), alias="PROFIT_PROTECT_ACTIVATION_PCT"
    )
    profit_protect_retain_fraction: Decimal = Field(
        default=Decimal("0.50"), alias="PROFIT_PROTECT_RETAIN_FRACTION"
    )
    profit_protect_min_pct: Decimal = Field(
        default=Decimal("0.0003"), alias="PROFIT_PROTECT_MIN_PCT"
    )
    profit_stop_step_pct: Decimal = Field(
        default=Decimal("0.0002"), alias="PROFIT_STOP_STEP_PCT"
    )
    thesis_exit_enabled: bool = Field(
        default=False, alias="THESIS_EXIT_ENABLED"
    )
    thesis_failure_cycles: int = Field(
        default=2, alias="THESIS_FAILURE_CYCLES"
    )
    thesis_exit_max_return_pct: Decimal = Field(
        default=Decimal("0.0005"), alias="THESIS_EXIT_MAX_RETURN_PCT"
    )

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
    def universe_exchanges(self) -> set[str]:
        return set(parse_csv(self.universe_exchanges_raw))

    @property
    def universe_always_include(self) -> tuple[str, ...]:
        return parse_csv(self.universe_always_include_raw)

    @property
    def crypto_quote_currencies(self) -> set[str]:
        return set(parse_csv(self.crypto_quote_currencies_raw))

    @property
    def crypto_excluded_bases(self) -> set[str]:
        return set(parse_csv(self.crypto_excluded_bases_raw))

    @property
    def crypto_always_include(self) -> tuple[str, ...]:
        return parse_csv(self.crypto_always_include_raw)

    @property
    def crypto_confirmation_symbols(self) -> tuple[str, ...]:
        return parse_csv(self.crypto_confirmation_symbols_raw)

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
    def order_owner_tag(self) -> str:
        source = (self.trading_run_id or self.strategy_version_id or "anevum").encode()
        return hashlib.sha256(source).hexdigest()[:8]

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

    @property
    def session_cash_flow_adjustment(self) -> SessionCashFlow | None:
        return SessionCashFlow.from_json(self.session_cash_flow_adjustment_raw)

    @model_validator(mode="after")
    def validate_settings(self):
        adjustment = self.session_cash_flow_adjustment
        if adjustment is not None and adjustment.run_id != self.trading_run_id:
            raise ValueError("cash-flow adjustment must match TRADING_RUN_ID")
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be paper or live")
        if self.strategy_name not in {"opening_range_vwap", "rolling_momentum_vwap"}:
            raise ValueError("STRATEGY_NAME must be opening_range_vwap or rolling_momentum_vwap")
        if self.data_feed not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("DATA_FEED must be iex, sip, or delayed_sip")
        if self.poll_seconds < 15:
            raise ValueError("POLL_SECONDS must be at least 15")
        if not 0.5 <= self.slack_webhook_timeout_seconds <= 10.0:
            raise ValueError(
                "SLACK_WEBHOOK_TIMEOUT_SECONDS must be between 0.5 and 10 seconds"
            )
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
        if not 10 <= self.universe_size <= 1000:
            raise ValueError("UNIVERSE_SIZE must be between 10 and 1000")
        if not self.universe_size <= self.universe_candidate_pool_size <= 5000:
            raise ValueError(
                "UNIVERSE_CANDIDATE_POOL_SIZE must be between UNIVERSE_SIZE and 5000"
            )
        if not 60 <= self.universe_refresh_seconds <= 3600:
            raise ValueError("UNIVERSE_REFRESH_SECONDS must be between 60 and 3600")
        if not 1 <= self.crypto_universe_size <= 100:
            raise ValueError("CRYPTO_UNIVERSE_SIZE must be between 1 and 100")
        if not 60 <= self.crypto_universe_refresh_seconds <= 3600:
            raise ValueError(
                "CRYPTO_UNIVERSE_REFRESH_SECONDS must be between 60 and 3600"
            )
        if not 15 <= self.crypto_poll_seconds <= 300:
            raise ValueError("CRYPTO_POLL_SECONDS must be between 15 and 300")
        if not 60 <= self.crypto_lookback_minutes <= 1440:
            raise ValueError("CRYPTO_LOOKBACK_MINUTES must be between 60 and 1440")
        if self.crypto_lane_enabled and not self.crypto_quote_currencies:
            raise ValueError("CRYPTO_QUOTE_CURRENCIES cannot be empty")
        if self.crypto_lane_enabled and not self.crypto_confirmation_symbols:
            raise ValueError("CRYPTO_CONFIRMATION_SYMBOLS cannot be empty")
        if self.crypto_order_notional <= 0:
            raise ValueError("CRYPTO_ORDER_NOTIONAL must be positive")
        if self.crypto_max_order_notional < self.crypto_order_notional:
            raise ValueError(
                "CRYPTO_MAX_ORDER_NOTIONAL cannot be below CRYPTO_ORDER_NOTIONAL"
            )
        if self.crypto_max_total_position_notional < self.crypto_order_notional:
            raise ValueError(
                "CRYPTO_MAX_TOTAL_POSITION_NOTIONAL cannot be below CRYPTO_ORDER_NOTIONAL"
            )
        if not 1 <= self.crypto_max_concurrent_positions <= 10:
            raise ValueError("CRYPTO_MAX_CONCURRENT_POSITIONS must be between 1 and 10")
        if not 0 <= self.crypto_max_entries_24h <= 100:
            raise ValueError("CRYPTO_MAX_ENTRIES_24H must be between 0 and 100")
        if not Decimal("0") < self.crypto_max_spread_pct < Decimal("0.10"):
            raise ValueError("CRYPTO_MAX_SPREAD_PCT must be between 0 and 0.10")
        if not Decimal("0") < self.crypto_stop_pct < Decimal("0.20"):
            raise ValueError("CRYPTO_STOP_PCT must be between 0 and 0.20")
        if not Decimal("0") < self.crypto_target_pct < Decimal("0.50"):
            raise ValueError("CRYPTO_TARGET_PCT must be between 0 and 0.50")
        if not Decimal("0") < self.crypto_stop_limit_buffer_pct < Decimal("0.10"):
            raise ValueError(
                "CRYPTO_STOP_LIMIT_BUFFER_PCT must be between 0 and 0.10"
            )
        if not 1 <= self.crypto_max_hold_minutes <= 1440:
            raise ValueError("CRYPTO_MAX_HOLD_MINUTES must be between 1 and 1440")
        if not 0 <= self.crypto_reentry_cooldown_minutes <= 1440:
            raise ValueError(
                "CRYPTO_REENTRY_COOLDOWN_MINUTES must be between 0 and 1440"
            )
        if not 2 <= self.universe_daily_lookback <= 20:
            raise ValueError("UNIVERSE_DAILY_LOOKBACK must be between 2 and 20")
        if not 1 <= self.universe_data_batch_size <= 100:
            raise ValueError("UNIVERSE_DATA_BATCH_SIZE must be between 1 and 100")
        if self.universe_min_price <= 0:
            raise ValueError("UNIVERSE_MIN_PRICE must be positive")
        if self.universe_min_avg_volume < 0:
            raise ValueError("UNIVERSE_MIN_AVG_VOLUME cannot be negative")
        if self.universe_min_avg_dollar_volume < 0:
            raise ValueError("UNIVERSE_MIN_AVG_DOLLAR_VOLUME cannot be negative")
        if self.dynamic_universe_enabled and not self.universe_exchanges:
            raise ValueError("UNIVERSE_EXCHANGES cannot be empty in dynamic mode")
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
        if not self.scan_symbols and not self.dynamic_universe_enabled:
            raise ValueError("SCAN_SYMBOLS/STRATEGY_SYMBOL cannot both be empty")
        if not self.dynamic_universe_enabled:
            missing = [
                symbol for symbol in self.scan_symbols
                if symbol not in self.allowed_symbols
            ]
            if missing:
                raise ValueError(
                    "Every SCAN_SYMBOLS symbol must also be in ALLOWED_SYMBOLS: "
                    + ",".join(missing)
                )
        if not self.confirmation_symbols:
            raise ValueError("CONFIRMATION_SYMBOLS cannot be empty")
        if not 1 <= self.min_confirmations <= len(self.confirmation_symbols):
            raise ValueError("MIN_CONFIRMATIONS must be between 1 and the number of confirmation symbols")
        if not 3 <= self.regime_window <= 30:
            raise ValueError("REGIME_WINDOW must be between 3 and 30")
        if not 1 <= self.regime_min_confirmations <= len(self.confirmation_symbols):
            raise ValueError(
                "REGIME_MIN_CONFIRMATIONS must be between 1 and the number of confirmation symbols"
            )
        if not Decimal("-0.02") <= self.regime_min_return_pct <= Decimal("0.02"):
            raise ValueError("REGIME_MIN_RETURN_PCT must be between -0.02 and 0.02")
        if not Decimal("0") < self.max_vwap_extension_pct < Decimal("0.05"):
            raise ValueError("MAX_VWAP_EXTENSION_PCT must be between 0 and 0.05")
        if not 0 <= self.loss_streak_limit <= 10:
            raise ValueError("LOSS_STREAK_LIMIT must be between 0 and 10")
        if not 0 <= self.loss_streak_cooldown_minutes <= 120:
            raise ValueError("LOSS_STREAK_COOLDOWN_MINUTES must be between 0 and 120")
        if not Decimal("0") < self.volatility_stop_multiplier <= Decimal("10"):
            raise ValueError("VOLATILITY_STOP_MULTIPLIER must be between 0 and 10")
        if not 3 <= self.volatility_stop_lookback_bars <= 60:
            raise ValueError("VOLATILITY_STOP_LOOKBACK_BARS must be between 3 and 60")
        if not Decimal("0") < self.max_dynamic_stop_pct < Decimal("0.05"):
            raise ValueError("MAX_DYNAMIC_STOP_PCT must be between 0 and 0.05")
        if (
            self.volatility_stop_enabled
            and self.max_dynamic_stop_pct < self.stop_pct
        ):
            raise ValueError(
                "MAX_DYNAMIC_STOP_PCT must be >= STOP_PCT when volatility stops are enabled"
            )
        if not Decimal("0") < self.profit_protect_activation_pct < Decimal("0.05"):
            raise ValueError("PROFIT_PROTECT_ACTIVATION_PCT must be between 0 and 0.05")
        if not Decimal("0") < self.profit_protect_retain_fraction <= Decimal("1"):
            raise ValueError("PROFIT_PROTECT_RETAIN_FRACTION must be between 0 and 1")
        if not Decimal("0") <= self.profit_protect_min_pct < self.profit_protect_activation_pct:
            raise ValueError(
                "PROFIT_PROTECT_MIN_PCT must be >= 0 and below PROFIT_PROTECT_ACTIVATION_PCT"
            )
        if not Decimal("0") < self.profit_stop_step_pct < Decimal("0.02"):
            raise ValueError("PROFIT_STOP_STEP_PCT must be between 0 and 0.02")
        if not 1 <= self.thesis_failure_cycles <= 10:
            raise ValueError("THESIS_FAILURE_CYCLES must be between 1 and 10")
        if not Decimal("-0.02") <= self.thesis_exit_max_return_pct <= Decimal("0.05"):
            raise ValueError("THESIS_EXIT_MAX_RETURN_PCT must be between -0.02 and 0.05")
        if not 0 <= self.max_hold_minutes <= 390:
            raise ValueError("MAX_HOLD_MINUTES must be between 0 and 390")
        if not 0 <= self.reentry_cooldown_minutes <= 60:
            raise ValueError("REENTRY_COOLDOWN_MINUTES must be between 0 and 60")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
