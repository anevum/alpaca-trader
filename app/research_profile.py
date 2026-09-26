from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from .config import Settings


STRATEGY_005_PROFILE_ID = "strategy005-entry-study-v1"

STRATEGY_005_ENTRY_MANIFEST: dict[str, Any] = {
    "profile_id": STRATEGY_005_PROFILE_ID,
    "scope": "entry_quality_research",
    "strategy_name": "rolling_momentum_vwap",
    "scan_symbols": ["AAPL", "MSFT", "SMCI", "TQQQ", "SOXL"],
    "confirmation_symbols": ["QQQ", "SMH"],
    "regime_reference_symbols": ["SPY", "QQQ", "SMH"],
    "dynamic_universe_enabled": False,
    "fast_window": 3,
    "slow_window": 8,
    "min_momentum_pct": "0.0005",
    "min_vwap_edge_pct": "0",
    "min_confirmations": 1,
    "regime_window": 5,
    "regime_min_confirmations": 1,
    "regime_min_return_pct": "0",
    "max_vwap_extension_pct": "0.008",
    "stop_pct": "0.0035",
    "target_pct": "0.005",
    "entry_start": "09:31",
    "entry_cutoff": "15:30",
    "max_bar_age_seconds": 90,
    "max_spread_pct": "0.002",
    "min_quality_score": "80",
    "bar_timeframe": "1Min",
    "data_feed": "iex",
    "assumed_spread_bps": "5",
    "assumed_slippage_bps_per_side": "2",
    "notes": [
        "This profile is for entry-quality research, not a claim of exact historical production replay.",
        "QQQ/SMH are entry confirmations; SPY/QQQ/SMH are separate read-only regime references.",
        "Portfolio allocation and production exit-engine state are intentionally outside this profile.",
    ],
}


def strategy_005_profile_manifest() -> dict[str, Any]:
    return json.loads(json.dumps(STRATEGY_005_ENTRY_MANIFEST))


def strategy_005_profile_hash() -> str:
    encoded = json.dumps(
        STRATEGY_005_ENTRY_MANIFEST,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_strategy_005_entry_profile(base: Settings) -> Settings:
    """Return an immutable-entry-study configuration independent of ambient env.

    Only settings used by signal eligibility, opportunity scoring, market
    quality, and forward stop/target labels are locked here. Portfolio sizing
    and live exit-engine settings are not represented as production parity.
    """
    return base.model_copy(
        update={
            "strategy_name": "rolling_momentum_vwap",
            "allowed_symbols_raw": "AAPL,MSFT,SMCI,TQQQ,SOXL",
            "scan_symbols_raw": "AAPL,MSFT,SMCI,TQQQ,SOXL",
            "dynamic_universe_enabled": False,
            "confirmation_symbols_raw": "QQQ,SMH",
            "fast_window": 3,
            "slow_window": 8,
            "min_momentum_pct": Decimal("0.0005"),
            "min_vwap_edge_pct": Decimal("0"),
            "min_confirmations": 1,
            "regime_window": 5,
            "regime_min_confirmations": 1,
            "regime_min_return_pct": Decimal("0"),
            "max_vwap_extension_pct": Decimal("0.008"),
            "stop_pct": Decimal("0.0035"),
            "target_pct": Decimal("0.005"),
            "entry_start_raw": "09:31",
            "entry_cutoff_raw": "15:30",
            "max_bar_age_seconds": 90,
            "max_spread_pct": Decimal("0.002"),
            "min_quality_score": Decimal("80"),
            "bar_timeframe": "1Min",
            "data_feed": "iex",
        }
    )
