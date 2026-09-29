from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import exp, log
from typing import Any, Mapping, Sequence

METHODOLOGY_VERSION = "ads-shadow-v2"
FEATURE_SCHEMA_VERSION = "ads-features-v2"
NORMALIZATION_VERSION = "hybrid-cross-sectional-v1"

MODEL_ADD = "ads002-add-v2"
MODEL_GEO = "ads002-geo-v2"
MODEL_RANK = "ads002-rank-v2"
ACTIVE_MODELS = (MODEL_ADD, MODEL_GEO, MODEL_RANK)


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp01(value: Any) -> float:
    return min(max(_f(value), 0.0), 1.0)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = exp(max(value, -60.0))
    return z / (1.0 + z)


def _geometric_weighted(values: Sequence[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    result = 0.0
    for value, weight in values:
        value = _clamp01(value)
        if value <= 0:
            return 0.0
        result += (weight / total_weight) * log(value)
    return exp(result)


def _mean(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def percentile_ranks(rows: Sequence[Mapping[str, Any]], key: str) -> list[float | None]:
    values: list[tuple[int, float]] = []
    for index, row in enumerate(rows):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            values.append((index, float(raw)))
        except (TypeError, ValueError):
            continue
    result: list[float | None] = [None] * len(rows)
    if not values:
        return result
    ordered = sorted(values, key=lambda item: item[1])
    n = len(ordered)
    cursor = 0
    while cursor < n:
        end = cursor + 1
        while end < n and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = ((cursor + 1) + end) / 2.0
        percentile = (average_rank - 0.5) / n
        for pos in range(cursor, end):
            result[ordered[pos][0]] = percentile
        cursor = end
    return result


def _hybrid_component(raw: Mapping[str, Any], rank_key: str, z_key: str) -> tuple[float | None, str]:
    rank = raw.get(rank_key)
    z = raw.get(z_key)
    has_rank = rank not in (None, "")
    has_z = z not in (None, "")
    if has_rank and has_z:
        return 0.5 * _sigmoid(_f(z)) + 0.5 * _clamp01(rank), "hybrid"
    if has_rank:
        return _clamp01(rank), "cross_section_only"
    if has_z:
        return _sigmoid(_f(z)), "historical_only"
    return None, "missing"


def score_attention_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    specs = (
        ("rank_log_rvol", "z_log_rvol", 0.25, "relative_volume"),
        ("rank_abs_return_5m", "z_abs_return_5m", 0.25, "movement"),
        ("rank_log_vol_expansion", "z_log_vol_expansion", 0.20, "volatility_expansion"),
        ("rank_log_range_expansion", "z_log_range_expansion", 0.15, "range_expansion"),
        ("rank_log_dollar_volume_5m", "z_log_dollar_volume_5m", 0.15, "dollar_volume"),
    )
    weighted = 0.0
    available_weight = 0.0
    components: dict[str, Any] = {}
    modes: dict[str, str] = {}
    for rank_key, z_key, weight, name in specs:
        value, mode = _hybrid_component(raw, rank_key, z_key)
        modes[name] = mode
        if value is None:
            components[name] = None
            continue
        components[name] = round(value, 6)
        weighted += weight * value
        available_weight += weight
    score = weighted / available_weight if available_weight >= 0.75 else None
    return {
        "score": None if score is None else round(score, 6),
        "components": components,
        "normalization_modes": modes,
        "available_weight": round(available_weight, 4),
    }


def _directional_signal(value: Any, scale: float) -> float | None:
    if value in (None, "") or scale <= 0:
        return None
    return _sigmoid(_f(value) / scale)


def score_qualification_v2(raw: Mapping[str, Any], configuration: Mapping[str, Any]) -> dict[str, Any]:
    momentum_scale = max(abs(_f(configuration.get("min_momentum_pct"))) * 4.0, 0.002)
    vwap_scale = max(abs(_f(configuration.get("target_pct"))), 0.0025)

    trend_values = [
        _directional_signal(raw.get("return_3m"), momentum_scale),
        _directional_signal(raw.get("fast_slow_spread_pct"), momentum_scale),
        _directional_signal(raw.get("vwap_edge_pct"), vwap_scale),
    ]
    persistence = raw.get("trend_persistence")
    if persistence not in (None, ""):
        trend_values.append(_clamp01(persistence))
    trend = _mean([value for value in trend_values if value is not None])

    confirmation_ratio = raw.get("confirmation_ratio")
    regime_ratio = raw.get("regime_ratio")
    confirmation = _mean([
        _clamp01(value)
        for value in (confirmation_ratio, regime_ratio)
        if value not in (None, "")
    ])

    relative_values = [
        raw.get("rank_return_5m"),
        raw.get("rank_relative_volume"),
    ]
    relative = _mean([
        _clamp01(value)
        for value in relative_values
        if value not in (None, "")
    ])

    families = [
        (trend, 0.50, "trend"),
        (confirmation, 0.30, "confirmation"),
        (relative, 0.20, "relative"),
    ]
    available = [(value, weight, name) for value, weight, name in families if value is not None]
    weight_sum = sum(weight for _, weight, _ in available)
    score = None
    if trend is not None and weight_sum >= 0.70:
        score = sum(value * weight for value, weight, _ in available) / weight_sum
    return {
        "score": None if score is None else round(score, 6),
        "families": {
            "trend": None if trend is None else round(trend, 6),
            "confirmation": None if confirmation is None else round(confirmation, 6),
            "relative": None if relative is None else round(relative, 6),
        },
        "family_mode": "equal_weight_within_family_fallback",
        "available_weight": round(weight_sum, 4),
    }


def score_timing_v2(raw: Mapping[str, Any], configuration: Mapping[str, Any]) -> dict[str, Any]:
    spread_bps = raw.get("spread_bps")
    quote_age_ms = raw.get("quote_age_ms")
    bar_age_ms = raw.get("bar_age_ms")
    if spread_bps in (None, "") or bar_age_ms in (None, ""):
        return {"score": None, "reason": "MISSING_MICROSTRUCTURE_CORE"}

    max_spread_pct = max(abs(_f(configuration.get("max_spread_pct"))), 0.0001)
    max_bar_age_ms = max(abs(_f(configuration.get("max_bar_age_seconds"))) * 1000.0, 1000.0)
    tau_spread_bps = max_spread_pct * 10000.0
    tau_quote_ms = max_bar_age_ms
    tau_bar_ms = max_bar_age_ms

    g_spread = exp(-(_f(spread_bps) / tau_spread_bps) ** 2)
    g_bar = exp(-max(_f(bar_age_ms), 0.0) / tau_bar_ms)
    g_quote = (
        exp(-max(_f(quote_age_ms), 0.0) / tau_quote_ms)
        if quote_age_ms not in (None, "")
        else g_bar
    )
    micro = _geometric_weighted(((g_spread, 1.0), (g_quote, 1.0), (g_bar, 1.0)))

    max_extension = max(abs(_f(configuration.get("max_vwap_extension_pct"))), 0.0001)
    extension_ratio = max(_f(raw.get("vwap_edge_pct")), 0.0) / max_extension
    extension = exp(-((extension_ratio - 0.50) ** 2) / (2.0 * 0.35 ** 2))

    momentum_scale = max(abs(_f(configuration.get("min_momentum_pct"))) * 4.0, 0.002)
    momentum_term = _f(raw.get("return_1m")) / momentum_scale
    acceleration_term = _f(raw.get("accel_1m")) / momentum_scale
    impulse = _sigmoid(0.67 * momentum_term + 0.33 * acceleration_term)

    timing = _geometric_weighted(((micro or 0.0, 0.40), (extension, 0.35), (impulse, 0.25)))
    return {
        "score": None if timing is None else round(timing, 6),
        "components": {
            "microstructure": None if micro is None else round(micro, 6),
            "extension": round(extension, 6),
            "impulse": round(impulse, 6),
        },
        "scale_source": "live_configuration_fallback",
    }


def raw_composites(attention: float, qualification: float, timing: float) -> dict[str, float]:
    add = 0.20 * attention + 0.50 * qualification + 0.30 * timing
    geo = _geometric_weighted(((attention, 0.20), (qualification, 0.50), (timing, 0.30)))
    return {
        MODEL_ADD: round(add, 6),
        MODEL_GEO: round(geo or 0.0, 6),
    }


def score_cycle_v2(
    candidates: Sequence[Mapping[str, Any]],
    configuration: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        raw = dict(candidate.get("raw_features") or {})
        raw["symbol"] = candidate.get("symbol")
        rows.append(raw)

    rank_specs = {
        "relative_volume_ratio": "rank_log_rvol",
        "abs_return_5m": "rank_abs_return_5m",
        "volatility_expansion_ratio": "rank_log_vol_expansion",
        "range_expansion_ratio": "rank_log_range_expansion",
        "dollar_volume_5m": "rank_log_dollar_volume_5m",
        "return_5m": "rank_return_5m",
        "relative_volume_ratio_raw": "rank_relative_volume",
    }
    transformed: dict[str, list[float | None]] = {}
    for source, target in rank_specs.items():
        temp: list[dict[str, Any]] = []
        for row in rows:
            value = row.get(source)
            if source in {
                "relative_volume_ratio",
                "volatility_expansion_ratio",
                "range_expansion_ratio",
                "dollar_volume_5m",
            } and value not in (None, ""):
                numeric = max(_f(value), 1e-12)
                temp.append({target: log(numeric)})
            elif source == "abs_return_5m":
                temp.append({target: abs(_f(row.get("return_5m"))) if row.get("return_5m") not in (None, "") else None})
            else:
                temp.append({target: value})
        transformed[target] = percentile_ranks(temp, target)

    for index, row in enumerate(rows):
        for target, ranks in transformed.items():
            row[target] = ranks[index]

    first_pass: list[dict[str, Any]] = []
    for row in rows:
        attention = score_attention_v2(row)
        qualification = score_qualification_v2(row, configuration)
        timing = score_timing_v2(row, configuration)
        complete = all(
            section.get("score") is not None
            for section in (attention, qualification, timing)
        )
        composites = (
            raw_composites(attention["score"], qualification["score"], timing["score"])
            if complete else {}
        )
        first_pass.append({
            "raw_features": row,
            "attention": attention,
            "qualification": qualification,
            "timing": timing,
            "composites": composites,
            "complete_pretrade": complete,
        })

    component_rows = [
        {
            "a": item["attention"].get("score"),
            "q": item["qualification"].get("score"),
            "t": item["timing"].get("score"),
        }
        for item in first_pass
    ]
    rank_a = percentile_ranks(component_rows, "a")
    rank_q = percentile_ranks(component_rows, "q")
    rank_t = percentile_ranks(component_rows, "t")

    output: list[dict[str, Any]] = []
    for index, item in enumerate(first_pass):
        challengers: dict[str, Any] = {}
        if item["complete_pretrade"]:
            challengers[MODEL_ADD] = {"s_raw": item["composites"][MODEL_ADD]}
            challengers[MODEL_GEO] = {"s_raw": item["composites"][MODEL_GEO]}
            if None not in (rank_a[index], rank_q[index], rank_t[index]):
                rank_score = 0.20 * rank_a[index] + 0.50 * rank_q[index] + 0.30 * rank_t[index]
                challengers[MODEL_RANK] = {"s_raw": round(rank_score, 6)}
        output.append({
            "methodology_version": METHODOLOGY_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "research_only": True,
            "execution_authority": False,
            "raw_features": item["raw_features"],
            "attention": item["attention"],
            "qualification": item["qualification"],
            "timing": item["timing"],
            "component_ranks": {
                "attention": rank_a[index],
                "qualification": rank_q[index],
                "timing": rank_t[index],
            },
            "confidence": {
                "score": None,
                "state": "UNFITTED_GLOBAL_CONFIDENCE",
                "reason": "Cross-session evidence and OOD baseline not yet mature.",
            },
            "challengers": challengers,
            "source_completeness": {
                "pretrade_complete": item["complete_pretrade"],
                "historical_time_of_day_baseline_available": any(
                    key.startswith("z_") and value not in (None, "")
                    for key, value in item["raw_features"].items()
                ),
                "relative_family_complete": item["qualification"]["families"].get("relative") is not None,
            },
        })
    return output
