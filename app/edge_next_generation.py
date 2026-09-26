from __future__ import annotations

from typing import Any


NEXT_GENERATION_FAMILIES = (
    {
        "name": "residual_relative_strength",
        "class": "cross_sectional",
        "hypothesis": (
            "Trade stock-specific strength only after subtracting broad-market "
            "and sector movement, so the signal targets idiosyncratic continuation "
            "rather than beta."
        ),
        "required_data": [
            "one-minute stock bars",
            "SPY/QQQ/sector ETF one-minute bars",
        ],
        "new_information": (
            "cross-sectional residual return instead of absolute momentum"
        ),
        "falsification": (
            "reject if residual-strength buckets do not show stable forward "
            "expectancy across sectors and cost scenarios"
        ),
    },
    {
        "name": "liquidity_shock_reversion",
        "class": "mean_reversion",
        "hypothesis": (
            "After an abnormal one-minute price/volume shock, test controlled "
            "reversion toward VWAP only when the next bars stop making new lows."
        ),
        "required_data": [
            "one-minute OHLCV",
            "decision-time spread if available",
        ],
        "new_information": (
            "post-shock stabilization and reversion rather than continuation"
        ),
        "falsification": (
            "reject if adverse excursion remains dominant after stabilization"
        ),
    },
    {
        "name": "sector_leader_laggard",
        "class": "relative_value",
        "hypothesis": (
            "Detect when a liquid stock lags an already-moving sector ETF or "
            "peer basket and test whether the lag closes intraday."
        ),
        "required_data": [
            "one-minute constituent bars",
            "sector ETF bars",
            "frozen sector membership map",
        ],
        "new_information": (
            "leader-laggard spread rather than single-symbol trend"
        ),
        "falsification": (
            "reject if lagged residuals do not mean-revert after costs"
        ),
    },
    {
        "name": "opening_gap_structure",
        "class": "auction_structure",
        "hypothesis": (
            "Condition entries on overnight gap direction, first-15-minute "
            "acceptance/rejection, and prior-day range location instead of "
            "intraday momentum alone."
        ),
        "required_data": [
            "daily prior-session bars",
            "one-minute opening bars",
        ],
        "new_information": (
            "overnight-to-open market structure"
        ),
        "falsification": (
            "reject if gap-structure states do not separate forward returns"
        ),
    },
    {
        "name": "multi_timescale_breakout",
        "class": "multi_timescale",
        "hypothesis": (
            "Require alignment between intraday compression and a higher-timeframe "
            "range boundary, testing whether breakouts near meaningful daily "
            "levels behave differently from generic one-minute breakouts."
        ),
        "required_data": [
            "one-minute bars",
            "daily bars",
        ],
        "new_information": (
            "higher-timeframe location plus intraday trigger"
        ),
        "falsification": (
            "reject if higher-timeframe location does not improve net expectancy"
        ),
    },
)


def next_generation_plan(
    research_outcome: dict[str, Any],
) -> dict[str, Any]:
    design_new = bool(research_outcome.get("design_new_family"))
    if not design_new:
        return {
            "triggered": False,
            "reason": "current research still has a surviving validation path",
            "families": [],
        }

    return {
        "triggered": True,
        "reason": str(research_outcome.get("outcome") or "all families rejected"),
        "rules": [
            "Do not change thresholds on a rejected family and rename it.",
            "Each new family must add a genuinely different source of information.",
            "Freeze the hypothesis and data requirements before evaluating holdout data.",
            "Prefer one new information axis at a time so falsification remains interpretable.",
        ],
        "families": [dict(item) for item in NEXT_GENERATION_FAMILIES],
        "priority_order": [
            "residual_relative_strength",
            "liquidity_shock_reversion",
            "opening_gap_structure",
            "sector_leader_laggard",
            "multi_timescale_breakout",
        ],
        "capital_scaling_allowed": False,
    }
