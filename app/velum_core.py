from __future__ import annotations

import random
from typing import Any

from .replay import d


def bootstrap_trade_distribution(
    trades: list[dict[str, Any]],
    *,
    paths: int,
    seed: int,
) -> dict[str, Any]:
    """Deterministic bootstrap of replay trade P&L; descriptive, not predictive."""
    if paths < 1:
        raise ValueError("paths must be positive")

    pnls = [float(d(row.get("net_pnl"))) for row in trades]
    if not pnls:
        return {
            "paths": paths,
            "trade_count": 0,
            "median_net_pnl": 0.0,
            "p05_net_pnl": 0.0,
            "p95_net_pnl": 0.0,
            "positive_path_fraction": 0.0,
            "method": "bootstrap-realized-trade-pnl",
            "forecast": False,
        }

    rng = random.Random(seed)
    totals = sorted(
        sum(rng.choice(pnls) for _ in range(len(pnls)))
        for _ in range(paths)
    )

    def quantile(q: float) -> float:
        if len(totals) == 1:
            return totals[0]
        index = int(round((len(totals) - 1) * q))
        return totals[max(0, min(index, len(totals) - 1))]

    return {
        "paths": paths,
        "trade_count": len(pnls),
        "median_net_pnl": round(quantile(0.50), 6),
        "p05_net_pnl": round(quantile(0.05), 6),
        "p95_net_pnl": round(quantile(0.95), 6),
        "positive_path_fraction": round(
            sum(value > 0 for value in totals) / len(totals),
            6,
        ),
        "method": "bootstrap-realized-trade-pnl",
        "forecast": False,
    }
