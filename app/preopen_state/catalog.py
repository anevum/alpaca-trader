from __future__ import annotations

from .models import SignalSource, SourceTier


TARGET_SOURCES: tuple[SignalSource, ...] = (
    SignalSource(
        key="spy_cash_proxy",
        label="S&P 500 ETF state",
        tier=SourceTier.DIRECT,
        symbol="SPY",
        measures="U.S. equity index ETF premarket price discovery",
        caveat="SPY is not ES futures and must never be labeled as futures data.",
    ),
    SignalSource(
        key="qqq_cash_proxy",
        label="Nasdaq-100 ETF state",
        tier=SourceTier.DIRECT,
        symbol="QQQ",
        measures="U.S. growth/index ETF premarket price discovery",
        caveat="QQQ is not NQ futures and must never be labeled as futures data.",
    ),
)

PROXY_SOURCES: tuple[SignalSource, ...] = (
    SignalSource(
        key="europe_equity_proxy",
        label="Europe equity proxy",
        tier=SourceTier.PROXY,
        symbol="FEZ",
        measures="U.S.-listed eurozone equity ETF premarket state",
        caveat="Not a substitute for synchronized STOXX/DAX/FTSE cash or futures data.",
    ),
    SignalSource(
        key="japan_equity_proxy",
        label="Japan equity proxy",
        tier=SourceTier.PROXY,
        symbol="EWJ",
        measures="U.S.-listed Japan equity ETF premarket state",
        caveat="Not a substitute for Nikkei/TOPIX session data.",
    ),
    SignalSource(
        key="em_equity_proxy",
        label="Emerging-market equity proxy",
        tier=SourceTier.PROXY,
        symbol="EEM",
        measures="U.S.-listed emerging-market ETF premarket state",
        caveat="Aggregates regions and may be stale in thin premarket trading.",
    ),
    SignalSource(
        key="rates_proxy",
        label="Long-duration Treasury proxy",
        tier=SourceTier.PROXY,
        symbol="TLT",
        measures="Long-duration Treasury ETF price response",
        caveat="Not a direct 2Y/10Y yield or Treasury-futures observation.",
    ),
    SignalSource(
        key="usd_proxy",
        label="U.S. dollar proxy",
        tier=SourceTier.PROXY,
        symbol="UUP",
        measures="Dollar-index ETF price response",
        caveat="Not direct DXY or synchronized spot-FX data.",
    ),
    SignalSource(
        key="oil_proxy",
        label="Crude-oil proxy",
        tier=SourceTier.PROXY,
        symbol="USO",
        measures="U.S.-listed crude-oil fund premarket state",
        caveat="Not direct WTI futures and contains fund/roll effects.",
    ),
    SignalSource(
        key="gold_proxy",
        label="Gold proxy",
        tier=SourceTier.PROXY,
        symbol="GLD",
        measures="U.S.-listed gold ETF premarket state",
        caveat="Not direct COMEX gold futures.",
    ),
    SignalSource(
        key="volatility_proxy",
        label="Volatility-product proxy",
        tier=SourceTier.PROXY,
        symbol="VXX",
        measures="Short-term VIX-futures ETN price response",
        caveat="Not VIX spot and not the VX term structure.",
    ),
)

INSTITUTIONAL_SOURCES: tuple[SignalSource, ...] = (
    SignalSource(
        key="es_futures",
        label="E-mini S&P 500 futures",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Overnight return, 5/15/30/60-minute momentum, depth",
        provider_requirement="CME futures market-data source",
    ),
    SignalSource(
        key="nq_futures",
        label="E-mini Nasdaq-100 futures",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Overnight return, 5/15/30/60-minute momentum, depth",
        provider_requirement="CME futures market-data source",
    ),
    SignalSource(
        key="global_cash_futures",
        label="Asia and Europe synchronized indexes/futures",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Local-session return and lead-lag state",
        provider_requirement="Point-in-time international exchange data",
    ),
    SignalSource(
        key="treasury_yields",
        label="U.S. Treasury yields",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="2Y/10Y yield changes and curve state",
        provider_requirement="Point-in-time Treasury/yield data",
    ),
    SignalSource(
        key="vix_vx_curve",
        label="VIX and VIX-futures term structure",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Volatility level, change, curve shape",
        provider_requirement="Cboe VIX/VX data",
    ),
    SignalSource(
        key="nasdaq_noii",
        label="Nasdaq opening imbalance / NOII",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Paired shares, imbalance, indicative clearing price and trajectory",
        provider_requirement="Nasdaq NOII/TotalView",
    ),
    SignalSource(
        key="nyse_opening_imbalance",
        label="NYSE opening imbalance",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Opening imbalance, paired quantity, indicative clearing price",
        provider_requirement="NYSE auction/imbalance feed",
    ),
    SignalSource(
        key="order_flow_depth",
        label="Signed order-flow imbalance and depth",
        tier=SourceTier.UNAVAILABLE,
        symbol=None,
        measures="Adds, cancels, aggressive flow, spread, queue depth",
        provider_requirement="Full-depth/order-event market data",
    ),
)


def source_catalog() -> tuple[SignalSource, ...]:
    return (*TARGET_SOURCES, *PROXY_SOURCES, *INSTITUTIONAL_SOURCES)


def symbol_tier(symbol: str, targets: tuple[str, ...]) -> SourceTier:
    return SourceTier.DIRECT if symbol.upper() in set(targets) else SourceTier.PROXY
