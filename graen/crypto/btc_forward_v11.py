from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any, Mapping, Sequence

from .activity_shock_v9 import (
    BAR_MINUTES,
    build_series,
    simulate,
    summarize,
)
from .trend_pullback_v10 import (
    Opportunity,
    TrendPullbackSpec,
    opportunity_at as v10_opportunity_at,
)


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-btc-forward-v11"
FAMILY = "btc_activity_confirmed_trend_pullback_recovery"
UNIVERSE = ("BTC/USD",)
DELAY_ROBUSTNESS_MINUTES = 5

# Historical windows are deliberately DEVELOPMENT evidence only. They were
# touched by earlier GRAEN campaigns and have no independent confirmation
# authority in v11.
WALK_FORWARD_WINDOWS = (
    {
        "name": "WF-2024-A",
        "start": datetime(2024, 1, 1, tzinfo=UTC),
        "end": datetime(2024, 5, 1, tzinfo=UTC),
    },
    {
        "name": "WF-2025-A",
        "start": datetime(2025, 5, 1, tzinfo=UTC),
        "end": datetime(2025, 9, 1, tzinfo=UTC),
    },
    {
        "name": "WF-2026-A",
        "start": datetime(2026, 2, 1, tzinfo=UTC),
        "end": datetime(2026, 6, 1, tzinfo=UTC),
    },
)


def candidate_specs() -> tuple[TrendPullbackSpec, ...]:
    return (
        TrendPullbackSpec(
            "V11-BTC-TPR-60-10-A",
            60, 10, 8, 0.0040, 1.00, 0.50, 0.65, 30, 45, 1.0,
        ),
        TrendPullbackSpec(
            "V11-BTC-TPR-120-15-A",
            120, 15, 12, 0.0060, 1.00, 0.50, 0.65, 45, 60, 1.0,
        ),
        TrendPullbackSpec(
            "V11-BTC-TPR-120-30-B",
            120, 30, 12, 0.0080, 1.25, 0.75, 0.70, 60, 90, 1.0,
        ),
        TrendPullbackSpec(
            "V11-BTC-TPR-240-15-A",
            240, 15, 24, 0.0100, 1.00, 0.50, 0.65, 60, 90, 1.0,
        ),
    )


def spec_from_dict(payload: Mapping[str, Any]) -> TrendPullbackSpec:
    return TrendPullbackSpec(**dict(payload))


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: TrendPullbackSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    if symbol not in UNIVERSE:
        return None
    row = v10_opportunity_at(series, spec, symbol, stamp)
    if row is None:
        return None
    signal = dict(row.signal)
    signal["family"] = FAMILY
    return Opportunity(
        candidate_id=row.candidate_id,
        symbol=row.symbol,
        opportunity_at=row.opportunity_at,
        hold_minutes=row.hold_minutes,
        signal=signal,
    )


def _grid(start: datetime, end: datetime):
    current = start.astimezone(UTC)
    limit = end.astimezone(UTC)
    while current < limit:
        yield current
        current += timedelta(minutes=BAR_MINUTES)


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: TrendPullbackSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    cooldown: dict[str, datetime] = {}
    for stamp in _grid(start, end):
        for symbol in UNIVERSE:
            if cooldown.get(symbol, datetime.min.replace(tzinfo=UTC)) > stamp:
                continue
            row = opportunity_at(series, spec, symbol, stamp)
            if row is None:
                continue
            output.append(row)
            cooldown[symbol] = stamp + timedelta(minutes=spec.cooldown_minutes)
    return output


def verify_stage_corpus(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    min_fraction: float = 0.80,
) -> dict[str, Any]:
    expected = max(int((end - start).total_seconds() // (BAR_MINUTES * 60)), 1)
    count = 0
    for row in bars_by_symbol.get("BTC/USD", ()):
        raw = str(row.get("t") or "")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        stamp = stamp.astimezone(UTC)
        if start <= stamp < end:
            count += 1
    fraction = count / expected
    report = {
        "expected_bars": expected,
        "count": count,
        "fraction": fraction,
        "minimum_fraction": min_fraction,
        "passed": fraction >= min_fraction,
        "symbol": "BTC/USD",
    }
    if not report["passed"]:
        raise ValueError("v11_btc_stage_corpus_incomplete")
    return report


def evaluate_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: TrendPullbackSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    btc_only = {"BTC/USD": list(bars_by_symbol.get("BTC/USD", ()))}
    warmup_hours = max(spec.activity_lookback_hours + 2, 26)
    series = build_series(
        btc_only,
        start=start,
        end=end,
        warmup_hours=warmup_hours,
    )
    opportunities = collect_opportunities(series, spec, start=start, end=end)
    primary = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )
    return {
        "candidate": spec.to_dict(),
        "family": FAMILY,
        "universe": list(UNIVERSE),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": summarize(delayed, start=start, end=end, seed=seed + 1),
        "opportunity_sample": [row.to_dict() for row in opportunities[:5]],
    }


def development_window_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 15:
        reasons.append("window_trade_count_below_15")
    if int(primary["independent_day_blocks"]) < 10:
        reasons.append("window_independent_days_below_10")
    if float(primary["trades_per_day"]) < 0.15:
        reasons.append("window_frequency_below_0.15_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("window_expectancy_nonpositive")
    pf = primary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("window_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("window_delay_expectancy_nonpositive")
    return not reasons, reasons


def evaluate_walk_forward(
    bars_by_window: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
) -> dict[str, Any]:
    windows = {row["name"]: row for row in WALK_FORWARD_WINDOWS}
    results: dict[str, Any] = {}
    survivors: list[dict[str, Any]] = []
    for spec_index, spec in enumerate(candidate_specs()):
        window_results: dict[str, Any] = {}
        pass_count = 0
        total_trades = 0
        total_days = 0
        score = 0.0
        for window_index, name in enumerate(windows):
            contract = windows[name]
            evaluation = evaluate_candidate(
                bars_by_window[name],
                spec=spec,
                start=contract["start"],
                end=contract["end"],
                scenario="high",
                seed=111000 + spec_index * 100 + window_index * 10,
            )
            passed, reasons = development_window_gate(evaluation)
            primary = evaluation["primary"]
            total_trades += int(primary["trade_count"])
            total_days += int(primary["independent_day_blocks"])
            if passed:
                pass_count += 1
                score += float(primary["expectancy_per_trade"]) * sqrt(
                    max(int(primary["trade_count"]), 1)
                )
            window_results[name] = {
                "passed": passed,
                "reasons": reasons,
                "result": evaluation,
            }
        robust = bool(
            pass_count >= 2
            and total_trades >= 60
            and total_days >= 30
            and score > 0
        )
        results[spec.candidate_id] = {
            "candidate_spec": spec.to_dict(),
            "window_results": window_results,
            "passed_windows": pass_count,
            "window_count": len(windows),
            "total_trades": total_trades,
            "total_independent_days": total_days,
            "selection_score": score,
            "robust_development_pass": robust,
        }
        if robust:
            survivors.append({
                "candidate_id": spec.candidate_id,
                "selection_score": score,
                "passed_windows": pass_count,
                "total_trades": total_trades,
            })

    selected = (
        max(
            survivors,
            key=lambda row: (
                row["passed_windows"],
                row["selection_score"],
                row["total_trades"],
                row["candidate_id"],
            ),
        )
        if survivors
        else None
    )
    selected_id = str(selected["candidate_id"]) if selected else None
    selected_spec = next(
        (spec for spec in candidate_specs() if spec.candidate_id == selected_id),
        None,
    )
    return {
        "stage": "DEVELOPMENT_WALK_FORWARD",
        "methodology_version": METHODOLOGY_VERSION,
        "family": FAMILY,
        "universe": list(UNIVERSE),
        "window_count": len(windows),
        "windows": [
            {
                "name": row["name"],
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "evidence_role": "PREVIOUSLY_INSPECTED_DEVELOPMENT_ONLY",
            }
            for row in WALK_FORWARD_WINDOWS
        ],
        "candidate_count": len(candidate_specs()),
        "results": results,
        "survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": selected_spec.to_dict() if selected_spec else None,
        "independent_confirmatory_evidence": False,
        "historical_promotion_authority": False,
        "next_confirmation": "NATIVE_FORWARD_SHADOW",
    }
