from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Iterable

from .replay import BPS, d, stamp


DEFAULT_STOPS = (
    Decimal("0.0020"),
    Decimal("0.0025"),
    Decimal("0.0030"),
    Decimal("0.0035"),
    Decimal("0.0040"),
    Decimal("0.0050"),
)
DEFAULT_TARGETS = (
    Decimal("0.0020"),
    Decimal("0.0025"),
    Decimal("0.0030"),
    Decimal("0.0040"),
    Decimal("0.0050"),
    Decimal("0.0060"),
)
DEFAULT_HORIZONS = (5, 10, 15)


def _sell_fill(
    reference: Decimal,
    *,
    spread_bps: Decimal,
    slippage_bps: Decimal,
) -> Decimal:
    half_spread = spread_bps / Decimal("2") / BPS
    slippage = slippage_bps / BPS
    return reference * (Decimal("1") - half_spread - slippage)


def simulate_surface_exit(
    *,
    future_bars: list[dict[str, Any]],
    entry_price: Decimal,
    stop_pct: Decimal,
    target_pct: Decimal,
    horizon: int,
    spread_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, Any] | None:
    """Simulate simple stop/target/time geometry with conservative bar ordering."""
    if entry_price <= 0:
        return None
    if stop_pct <= 0 or target_pct <= 0:
        raise ValueError("stop_pct and target_pct must be positive")
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    sample = future_bars[:horizon]
    if not sample:
        return None

    stop_price = entry_price * (Decimal("1") - stop_pct)
    target_price = entry_price * (Decimal("1") + target_pct)
    exit_reference: Decimal | None = None
    exit_reason = "time"

    for bar in sample:
        low = d(bar.get("l"))
        high = d(bar.get("h"))
        if low > 0 and low <= stop_price:
            exit_reference = stop_price
            exit_reason = "stop"
            break
        if high > 0 and high >= target_price:
            exit_reference = target_price
            exit_reason = "target"
            break

    if exit_reference is None:
        exit_reference = d(sample[-1].get("c"))
        if exit_reference <= 0:
            return None

    exit_fill = _sell_fill(
        exit_reference,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
    )
    net_return = (exit_fill / entry_price) - Decimal("1")
    return {
        "exit_reason": exit_reason,
        "exit_reference": str(exit_reference),
        "exit_fill": str(exit_fill),
        "net_return": str(net_return),
    }


def _future_bars(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    symbol = str(row.get("symbol") or "").upper()
    raw_time = str(row.get("decision_bar_time") or "")
    if not symbol or not raw_time:
        return []
    decision_time = stamp({"t": raw_time})
    future = [
        bar
        for bar in bars_by_symbol.get(symbol, [])
        if stamp(bar) > decision_time
        and stamp(bar).date() == decision_time.date()
    ]
    future.sort(key=stamp)
    return future


def _cell_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [d(item.get("net_return")) for item in results]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    reasons = Counter(str(item.get("exit_reason") or "") for item in results)
    return {
        "n": len(results),
        "mean_return": str(
            sum(returns, Decimal("0")) / Decimal(len(returns))
            if returns else Decimal("0")
        ),
        "win_rate": len(wins) / len(results) if results else 0.0,
        "profit_factor": (
            float(gross_profit / gross_loss)
            if gross_loss > 0 else None
        ),
        "gross_positive_return": str(gross_profit),
        "gross_negative_return": str(gross_loss),
        "exit_reasons": dict(reasons),
    }


def run_exit_surface(
    observations: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    stop_grid: Iterable[Decimal] = DEFAULT_STOPS,
    target_grid: Iterable[Decimal] = DEFAULT_TARGETS,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    spread_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
) -> dict[str, Any]:
    """Test whether simple exit geometry rescues the current qualified signals.

    Observations are quality-eligible production-style BUY opportunities. This
    analysis intentionally ignores portfolio capacity and repeated-signal
    suppression so it measures raw entry/exit geometry rather than allocation.
    """
    eligible = [
        row for row in observations
        if row.get("quality_allowed")
    ]
    cells: list[dict[str, Any]] = []

    for horizon in horizons:
        for stop_pct in stop_grid:
            for target_pct in target_grid:
                results: list[dict[str, Any]] = []
                for row in eligible:
                    entry_price = d(row.get("simulated_entry_price"))
                    simulated = simulate_surface_exit(
                        future_bars=_future_bars(bars_by_symbol, row),
                        entry_price=entry_price,
                        stop_pct=Decimal(stop_pct),
                        target_pct=Decimal(target_pct),
                        horizon=int(horizon),
                        spread_bps=spread_bps,
                        slippage_bps=slippage_bps,
                    )
                    if simulated is not None:
                        results.append(simulated)

                cells.append(
                    {
                        "horizon_minutes": int(horizon),
                        "stop_pct": str(stop_pct),
                        "target_pct": str(target_pct),
                        "summary": _cell_summary(results),
                    }
                )

    cells.sort(
        key=lambda cell: (
            d(cell["summary"].get("mean_return")),
            d(cell["summary"].get("profit_factor")),
            int(cell["summary"].get("n") or 0),
        ),
        reverse=True,
    )
    return {
        "status": "research_only",
        "eligible_observations": len(eligible),
        "spread_bps": str(spread_bps),
        "slippage_bps_per_side": str(slippage_bps),
        "stop_first_same_bar": True,
        "cells": cells,
        "best_cell": cells[0] if cells else None,
        "promotion_authorized": False,
        "scalable_capital_merge_allowed": False,
        "notes": [
            "This surface measures raw entry/exit geometry, not portfolio execution.",
            "Repeated adjacent BUY opportunities are intentionally retained.",
            "A positive surface cell is hypothesis evidence only; it cannot authorize live changes.",
        ],
    }


def consistent_surface_cells(
    period_surfaces: list[dict[str, Any]],
    *,
    minimum_periods: int = 2,
) -> dict[str, Any]:
    """Return cells with positive mean return in every supplied period."""
    if len(period_surfaces) < minimum_periods:
        return {
            "status": "insufficient_periods",
            "positive_in_all_periods": [],
        }

    buckets: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for period in period_surfaces:
        for cell in period.get("cells") or []:
            key = (
                int(cell["horizon_minutes"]),
                str(cell["stop_pct"]),
                str(cell["target_pct"]),
            )
            buckets.setdefault(key, []).append(cell)

    passing: list[dict[str, Any]] = []
    for key, cells in buckets.items():
        if len(cells) != len(period_surfaces):
            continue
        means = [
            d(cell["summary"].get("mean_return"))
            for cell in cells
        ]
        if not all(value > 0 for value in means):
            continue
        passing.append(
            {
                "horizon_minutes": key[0],
                "stop_pct": key[1],
                "target_pct": key[2],
                "minimum_period_mean_return": str(min(means)),
                "average_period_mean_return": str(
                    sum(means, Decimal("0")) / Decimal(len(means))
                ),
                "periods": [
                    cell["summary"]
                    for cell in cells
                ],
            }
        )

    passing.sort(
        key=lambda item: (
            d(item["minimum_period_mean_return"]),
            d(item["average_period_mean_return"]),
        ),
        reverse=True,
    )
    return {
        "status": "research_only",
        "period_count": len(period_surfaces),
        "positive_in_all_periods": passing,
        "current_signal_family_rescued_by_simple_exit_geometry": bool(passing),
        "promotion_authorized": False,
    }
