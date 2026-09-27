from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .config import get_settings
from .market_data import MarketDataClient
from .strategy_lab import StrategyTournament


NY = ZoneInfo("America/New_York")


def parse_period(raw: str) -> tuple[date, date]:
    try:
        start_raw, end_raw = raw.split(":", 1)
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError as exc:
        raise ValueError(
            "period must be START:END using YYYY-MM-DD:YYYY-MM-DD"
        ) from exc
    if end < start:
        raise ValueError("period end must be on or after start")
    if (end - start).days + 1 > 21:
        raise ValueError("each period is limited to 21 calendar days")
    return start, end


async def fetch_period(
    market_data: MarketDataClient,
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    start_dt = datetime.combine(
        start,
        time(0, 0),
        tzinfo=NY,
    ).astimezone(timezone.utc)
    end_dt = datetime.combine(
        end + timedelta(days=1),
        time(0, 0),
        tzinfo=NY,
    ).astimezone(timezone.utc)
    return await market_data.historical_bars_many(
        symbols,
        start=start_dt,
        end=end_dt,
    )


def aggregate_periods(
    period_results: list[dict[str, Any]],
    *,
    min_trades: int,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}

    for period in period_results:
        for row in period["leaderboard"]:
            summary = row["summary"]
            name = row["variant"]
            bucket = buckets.setdefault(
                name,
                {
                    "variant": name,
                    "description": row["description"],
                    "parameters": row["parameters"],
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": Decimal("0"),
                    "gross_profit": Decimal("0"),
                    "gross_loss": Decimal("0"),
                    "max_drawdown": Decimal("0"),
                    "max_drawdown_pct": Decimal("0"),
                    "period_returns": [],
                    "positive_periods": 0,
                    "periods": 0,
                },
            )
            bucket["trades"] += int(summary.get("trades") or 0)
            bucket["wins"] += int(summary.get("wins") or 0)
            bucket["losses"] += int(summary.get("losses") or 0)
            bucket["net_pnl"] += Decimal(str(summary.get("net_pnl") or "0"))
            bucket["gross_profit"] += Decimal(
                str(summary.get("gross_profit") or "0")
            )
            bucket["gross_loss"] += Decimal(
                str(summary.get("gross_loss") or "0")
            )
            bucket["max_drawdown"] = max(
                bucket["max_drawdown"],
                Decimal(str(summary.get("max_drawdown") or "0")),
            )
            bucket["max_drawdown_pct"] = max(
                bucket["max_drawdown_pct"],
                Decimal(str(summary.get("max_drawdown_pct") or "0")),
            )
            period_return = Decimal(str(summary.get("return_pct") or "0"))
            bucket["period_returns"].append(period_return)
            bucket["periods"] += 1
            if period_return > 0:
                bucket["positive_periods"] += 1

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        trades = bucket["trades"]
        gross_profit = bucket["gross_profit"]
        gross_loss = bucket["gross_loss"]
        expectancy = (
            bucket["net_pnl"] / Decimal(trades)
            if trades
            else Decimal("0")
        )
        average_return = (
            sum(bucket["period_returns"], Decimal("0"))
            / Decimal(bucket["periods"])
            if bucket["periods"]
            else Decimal("0")
        )
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else None
        )
        eligible = trades >= min_trades
        rows.append(
            {
                "variant": bucket["variant"],
                "description": bucket["description"],
                "parameters": bucket["parameters"],
                "eligible_for_ranking": eligible,
                "sample_note": (
                    ""
                    if eligible
                    else f"sample below minimum: {trades}/{min_trades} trades"
                ),
                "summary": {
                    "trades": trades,
                    "wins": bucket["wins"],
                    "losses": bucket["losses"],
                    "win_rate": round(
                        bucket["wins"] / trades,
                        4,
                    ) if trades else 0.0,
                    "net_pnl": str(bucket["net_pnl"].quantize(Decimal("0.01"))),
                    "gross_profit": str(
                        gross_profit.quantize(Decimal("0.01"))
                    ),
                    "gross_loss": str(
                        gross_loss.quantize(Decimal("0.01"))
                    ),
                    "profit_factor": (
                        None
                        if profit_factor is None
                        else round(float(profit_factor), 4)
                    ),
                    "expectancy_per_trade": str(
                        expectancy.quantize(Decimal("0.01"))
                    ),
                    "average_period_return": str(average_return),
                    "positive_periods": bucket["positive_periods"],
                    "periods": bucket["periods"],
                    "max_drawdown": str(
                        bucket["max_drawdown"].quantize(Decimal("0.01"))
                    ),
                    "max_drawdown_pct": str(bucket["max_drawdown_pct"]),
                },
            }
        )

    def sort_key(row: dict[str, Any]):
        summary = row["summary"]
        expectancy = Decimal(str(summary["expectancy_per_trade"]))
        average_return = Decimal(str(summary["average_period_return"]))
        drawdown = Decimal(str(summary["max_drawdown_pct"]))
        pf_raw = summary["profit_factor"]
        pf = Decimal(str(pf_raw)) if pf_raw is not None else Decimal("999")
        consistency = (
            Decimal(summary["positive_periods"])
            / Decimal(summary["periods"])
            if summary["periods"]
            else Decimal("0")
        )
        return (
            int(row["eligible_for_ranking"]),
            int(expectancy > 0),
            consistency,
            min(pf, Decimal("5")),
            average_return,
            -drawdown,
            summary["trades"],
        )

    rows.sort(key=sort_key, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    market_data = MarketDataClient(settings)
    tournament = StrategyTournament(settings)
    symbols = list(
        dict.fromkeys(
            [*settings.scan_symbols, *settings.confirmation_symbols]
        )
    )

    period_results: list[dict[str, Any]] = []
    for raw in args.period:
        start, end = parse_period(raw)
        bars = await fetch_period(
            market_data,
            symbols,
            start,
            end,
        )
        result = tournament.run_bars(
            bars,
            initial_equity=Decimal(args.initial_equity),
            spread_bps=Decimal(args.spread_bps),
            slippage_bps=Decimal(args.slippage_bps),
            min_trades=args.min_trades,
        )
        period_results.append(
            {
                "range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                **result,
            }
        )

    aggregate = aggregate_periods(
        period_results,
        min_trades=args.min_trades,
    )
    return {
        "aggregate_leaderboard": aggregate,
        "periods": period_results,
        "assumptions": {
            "initial_equity_per_period": args.initial_equity,
            "spread_bps": args.spread_bps,
            "slippage_bps_per_side": args.slippage_bps,
            "min_trades_for_ranking": args.min_trades,
            "live_configuration_changed": False,
            "broker_orders_possible": False,
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run the read-only ANEVUM strategy tournament."
    )
    value.add_argument(
        "--period",
        action="append",
        default=[],
        help="START:END using YYYY-MM-DD dates; repeat for multiple windows.",
    )
    value.add_argument("--initial-equity", default="100")
    value.add_argument("--spread-bps", default="5")
    value.add_argument("--slippage-bps", default="2")
    value.add_argument("--min-trades", type=int, default=20)
    return value


def main() -> None:
    args = parser().parse_args()
    if not args.period:
        args.period = [
            "2026-08-03:2026-08-21",
            "2026-08-24:2026-09-11",
            "2026-09-14:2026-09-23",
        ]
    report = asyncio.run(run(args))
    print("STRATEGY_TOURNAMENT_RESULT")
    print(json.dumps(report, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
