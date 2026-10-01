"""CRYPTO-FLOW-PRESSURE research evaluator. Never imports a broker."""
from __future__ import annotations

from datetime import timedelta
from math import isfinite, log1p
from statistics import fmean, pstdev
from types import SimpleNamespace

from graen.engineering import digest, stage_window, stamp
from .activity_shock_v9 import (
    Opportunity, build_series, simulate, summarize,
    development_gate, validation_gate, holdout_gate, verify_stage_corpus,
)

METHODOLOGY_VERSION = "graen-crypto-flow-pressure-v1"


def valid_bar(row):
    try:
        values = [float(row[key]) for key in ("o", "h", "l", "c", "v", "n")]
    except (KeyError, TypeError, ValueError):
        return False
    o, h, l, c, v, n = values
    return (
        all(isfinite(value) for value in values)
        and min(o, h, l, c) > 0 and h > l
        and l <= min(o, c) <= max(o, c) <= h
        and v > 0 and n > 0
    )


def flow_signal(current, history, parameters):
    if len(history) != parameters["lookback_bars"]:
        return None
    if not valid_bar(current) or not all(valid_bar(row) for row in history):
        return None
    features = {}
    for field, name in (("v", "volume_z"), ("n", "trade_count_z")):
        values = [log1p(float(row[field])) for row in history]
        sigma = pstdev(values)
        if sigma <= 1e-12:
            return None
        features[name] = (log1p(float(current[field])) - fmean(values)) / sigma
    baseline = fmean((float(row["h"]) - float(row["l"])) / float(row["o"]) for row in history)
    spread = float(current["h"]) - float(current["l"])
    features["range_ratio"] = (spread / float(current["o"])) / baseline
    features["body_strength"] = (float(current["c"]) - float(current["o"])) / spread
    features["close_location"] = (float(current["c"]) - float(current["l"])) / spread
    if not all(features[key] >= parameters[key] for key in features):
        return None
    return features


def opportunities(series, spec, start, end):
    params = spec["parameters"]
    cooldown = {}
    result = []
    current = start
    while current < end:
        for symbol in spec["universe"]:
            if symbol in cooldown and current < cooldown[symbol]:
                continue
            bars = series.get(symbol, {})
            row = bars.get(current)
            history = [
                bars.get(current - timedelta(minutes=5 * offset))
                for offset in range(params["lookback_bars"], 0, -1)
            ]
            if row is None or any(item is None for item in history):
                continue
            signal = flow_signal(row, history, params)
            if signal is not None:
                result.append(Opportunity(
                    candidate_id=spec["hypothesis_id"], symbol=symbol,
                    opportunity_at=current, hold_minutes=params["hold_minutes"],
                    signal={"family": "bar_flow_pressure_v1", **signal},
                ))
                cooldown[symbol] = current + timedelta(minutes=params["cooldown_minutes"])
        current += timedelta(minutes=5)
    return result


def evaluate_stage(bars, *, spec, stage, predecessor=None):
    start, end = stage_window(spec, stage, predecessor)
    warmup = timedelta(minutes=5 * (spec["parameters"]["lookback_bars"] + 1))
    bounded = {
        symbol: [row for row in bars.get(symbol, []) if start - warmup <= stamp(row["t"]) < end]
        for symbol in spec["universe"]
    }
    verify_stage_corpus(bounded, start=start, end=end)
    series = build_series(bounded, start=start, end=end, warmup_hours=26)
    signals = opportunities(series, spec, start, end)
    scenarios = {}
    for index, cost in enumerate(("low", "base", "high") if stage == "holdout" else ("high",)):
        primary = simulate(series, signals, start=start, end=end, scenario=cost)
        delayed = simulate(series, signals, start=start, end=end, scenario=cost, extra_entry_delay_minutes=5)
        scenarios[cost] = {
            "primary": summarize(primary, start=start, end=end, seed=120000 + index * 2),
            "one_bar_delay": summarize(delayed, start=start, end=end, seed=120001 + index * 2),
        }
    gate_spec = SimpleNamespace(concentration_limit=0.70)
    if stage == "development":
        passed, reasons = development_gate(scenarios["high"])
    elif stage == "validation":
        passed, reasons = validation_gate(gate_spec, scenarios["high"])
    else:
        passed, reasons = holdout_gate(gate_spec, scenarios)
    return {
        "stage": stage, "passed": passed, "reasons": reasons, "spec_hash": digest(spec),
        "epoch": spec["epoch"], "candidate_id": spec["hypothesis_id"],
        "scenarios": scenarios, "research_only": True, "execution_authority": False,
    }
