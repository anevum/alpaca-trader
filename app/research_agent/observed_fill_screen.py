"""Broker-isolated *observed-entry* counterfactual screen.

Reads only locally supplied, previously reconstructed execution records and
completed-bar features. Applies the exact frozen research agenda slate, not
LLM-proposed live settings. It is NOT a full strategy replay: removing an entry
does not reconstruct subsequent portfolio allocation or changing exits.
No broker, runtime, network, database, order or automatic promotion imports.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any

from .rule_set_agenda import CHALLENGERS

SCHEMA_VERSION = "anevum-rhen-email-fill-observed-trade-screen-v1"
SCREEN_VERSION = "rhen-observed-entry-screen-v1"
MAX_TRADES = 2000
MIN_COMPLETED_BARS = 8
MAX_PNL_DISCREPANCY = Decimal("0.00000001")


def _decimal(value: Any, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite Decimal")
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return result


def _stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [row["_pnl"] for row in trades]
    positives = sum((v for v in pnls if v > 0), Decimal("0"))
    negatives = sum((v for v in pnls if v < 0), Decimal("0"))
    return {
        "count": len(pnls),
        "winners": sum(v > 0 for v in pnls),
        "losers": sum(v < 0 for v in pnls),
        "gross_profit": str(positives),
        "gross_loss": str(negatives),
        "net_pnl_from_original_exits": str(positives + negatives),
        "profit_factor_from_original_exits": (
            str(positives / -negatives) if negatives else None
        ),
    }


def _rule_passes(trade: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> bool:
    features = {
        "relative_volume": trade["_relative_volume"],
        "trend_persistence": trade["_trend_persistence"],
    }
    return all(
        (
            rule["indicator"] in features
            and features[rule["indicator"]] >= _decimal(rule["threshold"], "threshold")
            and trade["_completed_bars"] >= int(rule["min_bars"])
        )
        for rule in rules
    )


def screen_observed_trades(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve actual exits; compare only whether original entries are retained.

    Returned package is an aggregate, never echoes email identifiers, account
    metadata or per-trade execution details from the private input.
    """
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be a mapping")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unrecognized observed-entry evidence schema")
    source = evidence.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("broker and market feature provenance is missing")
    if "Alpaca" not in str(source.get("broker_fills") or ""):
        raise ValueError("broker source evidence identity unverified")
    if "IEX" not in str(source.get("market_features") or ""):
        raise ValueError("expected IEX completed-bar observation provenance is missing")
    trades = evidence.get("trades")
    if (
        not isinstance(trades, list)
        or not 1 <= len(trades) <= MAX_TRADES
    ):
        raise ValueError("require bounded nonempty observed trade rows")
    observations: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str, str]] = set()
    for record in trades:
        if not isinstance(record, Mapping):
            raise ValueError("observed trade must be a mapping")
        session = str(record.get("session") or "")
        try:
            if date.fromisoformat(session).isoformat() != session:
                raise ValueError("noncanonical date")
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid entry session") from exc
        symbol = str(record.get("symbol") or "").strip()
        entry_at = str(record.get("entry_et") or "")
        exit_at = str(record.get("exit_et") or "")
        try:
            in_time = time.fromisoformat(entry_at)
            out_time = time.fromisoformat(exit_at)
            if in_time.isoformat(timespec="minutes") != entry_at:
                raise ValueError("minute accuracy required")
            if out_time.isoformat(timespec="minutes") != exit_at:
                raise ValueError("minute accuracy required")
        except (TypeError, ValueError) as exc:
            raise ValueError("entry and exit must use strict HH:MM eastern time") from exc
        if out_time < in_time:
            raise ValueError("overnight trades need separate session reconstruction")
        identity = (session, symbol, entry_at, exit_at)
        if not symbol or identity in identities:
            raise ValueError("missing or duplicate independent round-trip identity")
        identities.add(identity)
        quantity = _decimal(record.get("quantity"), "quantity")
        entry = _decimal(record.get("entry_price"), "entry_price")
        exit_price = _decimal(record.get("exit_price"), "exit_price")
        pnl = _decimal(record.get("gross_realized_pnl"), "gross_realized_pnl")
        if min(quantity, entry, exit_price) <= 0:
            raise ValueError("fills must have positive quantity and prices")
        if abs(pnl - quantity * (exit_price - entry)) > MAX_PNL_DISCREPANCY:
            raise ValueError("fill P/L does not reconcile to executed prices")
        # These values are not reconstructed from the input's asserted
        # quality labels; callers must supply actual past-only computed bars.
        rel_volume = _decimal(record.get("relative_volume_21bars"), "relative_volume")
        trend = _decimal(
            record.get("trend_persistence_last_9_closes"), "trend_persistence"
        )
        try:
            completed = int(record.get("completed_bars_available"))
        except (TypeError, ValueError) as exc:
            raise ValueError("completed bar count missing") from exc
        if completed < MIN_COMPLETED_BARS or rel_volume < 0 or not 0 <= trend <= 1:
            raise ValueError("missing or inconsistent completed-bar features")
        observations.append({
            "_pnl": pnl, "_relative_volume": rel_volume,
            "_trend_persistence": trend,
            "_completed_bars": completed, "session": session,
        })

    dates = sorted({row["session"] for row in observations})
    base = _stats(observations)
    slate = [{"name": "production", "entry_rules": []}, *CHALLENGERS]
    decisions = []
    for item in slate:
        accept = [
            row for row in observations
            if _rule_passes(row, item["entry_rules"])
        ]
        reject = [
            row for row in observations
            if not _rule_passes(row, item["entry_rules"])
        ]
        retained = _stats(accept)
        excluded = _stats(reject)
        delta = (
            Decimal(retained["net_pnl_from_original_exits"])
            - Decimal(base["net_pnl_from_original_exits"])
        )
        decisions.append({
            "name": item["name"],
            "original_exits_retained": retained,
            "observed_entries_filtered_out": excluded,
            "foregone_historical_winners": excluded["winners"],
            "delta_to_unfiltered_original_pnl": str(delta),
            "by_session": {
                session: {
                    "original_exits_retained": _stats(
                        [row for row in accept if row["session"] == session]
                    ),
                    "observed_entries_filtered_out": _stats(
                        [row for row in reject if row["session"] == session]
                    ),
                }
                for session in dates
            },
        })

    material = {
        "version": SCREEN_VERSION,
        "control_strategy": str(source.get("strategy_version_id") or ""),
        "trades": [
            {
                "session": r.get("session"), "symbol": r.get("symbol"),
                "entry_et": r.get("entry_et"), "exit_et": r.get("exit_et"),
                "qty": r.get("quantity"), "entry": r.get("entry_price"),
                "exit": r.get("exit_price"), "rv": r.get("relative_volume_21bars"),
                "trend": r.get("trend_persistence_last_9_closes"),
            }
            for r in trades
        ],
    }
    return {
        "schema_version": SCREEN_VERSION,
        "source_fingerprint": sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "study_kind": "OBSERVED_FILLED_ENTRIES_ONLY",
        "control": "production",
        "independent_session_count": len(dates),
        "sessions": dates,
        "original_filled_trade_count": len(observations),
        "original_net_pnl": base["net_pnl_from_original_exits"],
        "compared_challengers": len(CHALLENGERS),
        "screen_results": decisions,
        "completed_bar_features_from_original_source_not_reconstructed": True,
        "full_candidate_universe_included": False,
        "same_strategy_execution_path_replayed": False,
        "dynamic_cash_positions_replayed": False,
        "independent_holdout_untouched": False,
        "experimental_significance_established": False,
        "broker_activity_api_complete": False,
        "research_only": True,
        "new_live_entries_authorized": False,
        "automatic_promotion_authorized": False,
        "execution_authority": False,
        "decision": "EXPLORATORY_OBSERVED_ENTRIES_NO_WINNER_VALIDATED",
    }
