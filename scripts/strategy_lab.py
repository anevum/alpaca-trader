from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.market_data import MarketDataClient
from app.strategy_lab import StrategyTournament


NY = ZoneInfo("America/New_York")


def parse_period(raw: str) -> tuple[date, date]:
    try:
        start_raw, end_raw = raw.split(":", 1)
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "period must be START:END using YYYY-MM-DD:YYYY-MM-DD"
        ) from exc
    if end < start:
        raise argparse.ArgumentTypeError("period end must be on or after start")
    if (end - start).days + 1 > 21:
        raise argparse.ArgumentTypeError("each period is limited to 21 calendar days")
    return start, end


async def fetch_period(market_data, symbols, start, end):
    start_dt = datetime.combine(start, time(0, 0), tzinfo=NY).astimezone(timezone.utc)
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


def aggregate(period_results, min_trades):
    variants = {}
    for period in period_results:
        for row in period["leaderboard"]:
            bucket = variants.setdefault(
                row["variant"],
                {
                    "variant": row["variant"],
                    "description": row["description"],
                    "parameters": row["parameters"],
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": Decimal("0"),
                    "gross_profit": Decimal("0"),
                    "gross_loss": Decimal("0"),
                    "max_drawdown": Decimal("0"),
                    "period_returns": [],
                    "periods": 0,
                    "positive_periods": 0,
                },
            )
            summary = row["summary"]
            bucket["trades"] += int(summary.get("trades") or 0)
            bucket["wins"] += int(summary.get("wins") or 0)
            bucket["losses"] += int(summary.get("losses") or 0)
            bucket["net_pnl"] += Decimal(str(summary.get("net_pnl") or "0"))
            bucket["gross_profit"] += Decimal(str(summary.get("gross_profit") or "0"))
            bucket["gross_loss"] += Decimal(str(summary.get("gross_loss") or "0"))
            bucket["max_drawdown"] = max(
                bucket["max_drawdown"],
                Decimal(str(summary.get("max_drawdown") or "0")),
            )
            period_return = Decimal(str(summary.get("return_pct") or "0"))
            bucket["period_returns"].append(period_return)
            bucket["periods"] += 1
            if period_return > 0:
                bucket["positive_periods"] += 1

    rows = []
    for bucket in variants.values():
        trades = bucket["trades"]
        gross_loss = bucket["gross_loss"]
        gross_profit = bucket["gross_profit"]
        expectancy = (
            bucket["net_pnl"] / Decimal(trades)
            if trades
            else Decimal("0")
        )
        profit_factor = (
            float(gross_profit / gross_loss)
            if gross_loss > 0
            else None
        )
        average_period_return = (
            sum(bucket["period_returns"], Decimal("0")) / Decimal(bucket["periods"])
            if bucket["periods"]
            else Decimal("0")
        )
        row = {
            "variant": bucket["variant"],
            "description": bucket["description"],
            "parameters": bucket["parameters"],
            "summary": {
                "trades": trades,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "win_rate": round(bucket["wins"] / trades, 4) if trades else 0.0,
                "net_pnl": str(bucket["net_pnl"].quantize(Decimal("0.01"))),
                "gross_profit": str(gross_profit.quantize(Decimal("0.01"))),
                "gross_loss": str(gross_loss.quantize(Decimal("0.01"))),
                "profit_factor": None if profit_factor is None else round(profit_factor, 4),
                "expectancy_per_trade": str(expectancy.quantize(Decimal("0.01"))),
                "return_pct": str(average_period_return),
                "max_drawdown": str(bucket["max_drawdown"].quantize(Decimal("0.01"))),
                "max_drawdown_pct": "0",
                "positive_periods": bucket["positive_periods"],
                "periods": bucket["periods"],
            },
        }
        row["eligible_for_ranking"] = trades >= min_trades
        row["sample_note"] = (
            ""
            if trades >= min_trades
            else f"sample below minimum: {trades}/{min_trades} trades"
        )
        rows.append(row)

    def key(row):
        summary = row["summary"]
        pf = summary["profit_factor"] if summary["profit_factor"] is not None else 999.0
        expectancy = Decimal(str(summary["expectancy_per_trade"]))
        avg_return = Decimal(str(summary["return_pct"]))
        consistency = (
            summary["positive_periods"] / summary["periods"]
            if summary["periods"]
            else 0
        )
        return (
            int(row["eligible_for_ranking"]),
            int(expectancy > 0),
            consistency,
            min(pf, 5.0),
            avg_return,
            summary["trades"],
        )

    rows.sort(key=key, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def write_csv(path, rows):
    fields = [
        "rank", "variant", "eligible_for_ranking", "trades", "wins", "losses",
        "win_rate", "net_pnl", "profit_factor", "expectancy_per_trade",
        "average_period_return", "positive_periods", "periods", "max_drawdown",
        "sample_note",
    ]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            summary = row["summary"]
            writer.writerow({
                "rank": row["rank"],
                "variant": row["variant"],
                "eligible_for_ranking": row["eligible_for_ranking"],
                "trades": summary["trades"],
                "wins": summary["wins"],
                "losses": summary["losses"],
                "win_rate": summary["win_rate"],
                "net_pnl": summary["net_pnl"],
                "profit_factor": summary["profit_factor"],
                "expectancy_per_trade": summary["expectancy_per_trade"],
                "average_period_return": summary["return_pct"],
                "positive_periods": summary.get("positive_periods"),
                "periods": summary.get("periods"),
                "max_drawdown": summary["max_drawdown"],
                "sample_note": row["sample_note"],
            })


async def run(args):
    settings = get_settings()
    market_data = MarketDataClient(settings)
    tournament = StrategyTournament(settings)
    symbols = list(
        dict.fromkeys([*settings.scan_symbols, *settings.confirmation_symbols])
    )

    period_results = []
    for start, end in args.period:
        bars = await fetch_period(market_data, symbols, start, end)
        result = tournament.run_bars(
            bars,
            initial_equity=Decimal(args.initial_equity),
            spread_bps=Decimal(args.spread_bps),
            slippage_bps=Decimal(args.slippage_bps),
            min_trades=args.min_trades,
        )
        period_results.append({
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            **result,
        })

    leaderboard = aggregate(period_results, args.min_trades)
    report = {
        "periods": period_results,
        "aggregate_leaderboard": leaderboard,
        "assumptions": {
            "initial_equity_per_period": args.initial_equity,
            "spread_bps": args.spread_bps,
            "slippage_bps_per_side": args.slippage_bps,
            "min_trades_for_ranking": args.min_trades,
            "live_configuration_changed": False,
        },
    }

    print(json.dumps({
        "aggregate_leaderboard": leaderboard,
        "assumptions": report["assumptions"],
    }, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.csv:
        write_csv(args.csv, leaderboard)


def main():
    parser = argparse.ArgumentParser(
        description="Run the ANEVUM historical strategy tournament."
    )
    parser.add_argument(
        "--period",
        action="append",
        type=parse_period,
        required=True,
        help="Repeatable START:END period, max 21 calendar days each.",
    )
    parser.add_argument("--initial-equity", default="100")
    parser.add_argument("--spread-bps", default="5")
    parser.add_argument("--slippage-bps", default="2")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--output", default="strategy_lab.json")
    parser.add_argument("--csv", default="strategy_leaderboard.csv")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
