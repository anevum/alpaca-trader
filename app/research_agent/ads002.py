from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import prod
from typing import Any, Mapping, Sequence


METHODOLOGY_VERSION = "ads-shadow-v1"
FORWARD_METHODOLOGY_VERSION = "candidate-forward-v2"
FORWARD_HORIZONS_MINUTES = (1, 3, 5, 10, 15, 30, 60)
PRIMARY_FORWARD_HORIZON_MINUTES = 15

MIN_INDEPENDENT_SESSIONS = 10
MIN_RESEARCH_ELIGIBLE_CANDIDATES = 100
MIN_CLOSED_DIRECT_TRADES = 30
PRE_SAMPLE_CONFIDENCE_CAP = Decimal("49")


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _clamp01(value: Any) -> Decimal:
    return min(max(_d(value), Decimal("0")), Decimal("1"))


def _score(value: Decimal) -> float:
    return round(float(min(max(value, Decimal("0")), Decimal("100"))), 4)


def _component(value: Decimal) -> float:
    return round(float(_clamp01(value)), 6)


def _scale(configuration: Mapping[str, Any], key: str, floor: str, multiplier: str = "1") -> Decimal:
    configured = abs(_d(configuration.get(key)))
    return max(configured * Decimal(multiplier), Decimal(floor))


def score_attention(
    feature_vector: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    momentum_scale = _scale(configuration, "min_momentum_pct", "0.002", "4")
    relative_volume = max(_d(feature_vector.get("relative_volume_ratio"), Decimal("1")), Decimal("0"))
    momentum = abs(_d(feature_vector.get("momentum_pct")))
    persistence = _clamp01(feature_vector.get("trend_persistence", Decimal("0.5")))

    rvol = _clamp01(relative_volume / Decimal("2"))
    velocity = _clamp01(momentum / momentum_scale)
    persistence_strength = _clamp01(Decimal("2") * abs(persistence - Decimal("0.5")))

    total = Decimal("100") * (
        Decimal("0.45") * rvol
        + Decimal("0.35") * velocity
        + Decimal("0.20") * persistence_strength
    )
    return {
        "score": _score(total),
        "components": {
            "relative_volume": _component(rvol),
            "velocity": _component(velocity),
            "trend_persistence_strength": _component(persistence_strength),
        },
    }


def score_qualification(
    feature_vector: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    momentum_scale = _scale(configuration, "min_momentum_pct", "0.002", "4")
    vwap_scale = _scale(configuration, "target_pct", "0.0025")

    momentum = max(_d(feature_vector.get("momentum_pct")), Decimal("0"))
    vwap_edge = max(_d(feature_vector.get("vwap_edge_pct")), Decimal("0"))

    q_momentum = _clamp01(momentum / momentum_scale)
    q_vwap = _clamp01(vwap_edge / vwap_scale)

    independent_count = max(_d(feature_vector.get("independent_confirmation_count")), Decimal("0"))
    if independent_count > 0:
        q_confirm = _clamp01(
            _d(feature_vector.get("fresh_confirmation_passes")) / independent_count
        )
    else:
        q_confirm = Decimal("0.5")

    regime_count = max(_d(feature_vector.get("regime_confirmation_count")), Decimal("0"))
    if regime_count > 0:
        q_regime = _clamp01(
            _d(feature_vector.get("regime_confirmation_passes")) / regime_count
        )
    else:
        q_regime = Decimal("0.5")

    total = Decimal("100") * (
        Decimal("0.35") * q_momentum
        + Decimal("0.25") * q_vwap
        + Decimal("0.25") * q_confirm
        + Decimal("0.15") * q_regime
    )
    return {
        "score": _score(total),
        "components": {
            "positive_momentum": _component(q_momentum),
            "positive_vwap_edge": _component(q_vwap),
            "independent_confirmations": _component(q_confirm),
            "regime_confirmations": _component(q_regime),
        },
    }


def score_timing(
    feature_vector: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    max_spread = max(_d(configuration.get("max_spread_pct")), Decimal("0"))
    max_bar_age = max(_d(configuration.get("max_bar_age_seconds")), Decimal("0"))
    max_extension = max(_d(configuration.get("max_vwap_extension_pct")), Decimal("0"))

    spread = max(_d(feature_vector.get("spread_pct")), Decimal("0"))
    bar_age = max(_d(feature_vector.get("bar_age_seconds")), Decimal("0"))

    if max_spread > 0:
        spread_quality = Decimal("1") - _clamp01(spread / max_spread)
    else:
        spread_quality = Decimal("0")

    if max_bar_age > 0:
        bar_freshness = Decimal("1") - _clamp01(bar_age / max_bar_age)
    else:
        bar_freshness = Decimal("0")

    quote_age_raw = feature_vector.get("quote_age_ms")
    quote_age_imputed = quote_age_raw in (None, "")
    if quote_age_imputed:
        quote_freshness = bar_freshness
    elif max_bar_age > 0:
        quote_freshness = Decimal("1") - _clamp01(
            max(_d(quote_age_raw), Decimal("0")) / (Decimal("1000") * max_bar_age)
        )
    else:
        quote_freshness = Decimal("0")

    vwap_edge = max(_d(feature_vector.get("vwap_edge_pct")), Decimal("0"))
    if max_extension > 0:
        extension_headroom = Decimal("1") - _clamp01(vwap_edge / max_extension)
    else:
        extension_headroom = Decimal("0.5")

    total = Decimal("100") * (
        Decimal("0.35") * spread_quality
        + Decimal("0.25") * bar_freshness
        + Decimal("0.15") * quote_freshness
        + Decimal("0.25") * extension_headroom
    )
    return {
        "score": _score(total),
        "components": {
            "spread_quality": _component(spread_quality),
            "bar_freshness": _component(bar_freshness),
            "quote_freshness": _component(quote_freshness),
            "vwap_extension_headroom": _component(extension_headroom),
        },
        "details": {"quote_age_imputed": quote_age_imputed},
    }


def score_exit_health(
    *,
    realized_return: Any,
    max_favorable_excursion: Any,
    max_adverse_excursion: Any,
) -> dict[str, Any]:
    if max_favorable_excursion in (None, "") or max_adverse_excursion in (None, ""):
        return {
            "score": None,
            "components": {},
            "reason_code": "MISSING_EXCURSION_DATA",
        }

    realized = _d(realized_return)
    mfe = max(_d(max_favorable_excursion), Decimal("0"))
    mae = min(_d(max_adverse_excursion), Decimal("0"))
    eps = Decimal("0.000001")

    outcome_location = _clamp01((realized - mae) / max(mfe - mae, eps))
    if mfe > 0:
        capture = _clamp01(realized / mfe)
    else:
        capture = Decimal("1") if realized >= 0 else Decimal("0")

    loss_containment = Decimal("1") - _clamp01(
        abs(min(realized, Decimal("0"))) / max(abs(mae), eps)
    )

    total = Decimal("100") * (
        Decimal("0.45") * outcome_location
        + Decimal("0.35") * capture
        + Decimal("0.20") * loss_containment
    )
    return {
        "score": _score(total),
        "components": {
            "outcome_location": _component(outcome_location),
            "mfe_capture": _component(capture),
            "loss_containment": _component(loss_containment),
        },
        "reason_code": None,
    }


def pretrade_composite(*, attention: Any, qualification: Any, timing: Any) -> float:
    total = (
        Decimal("0.20") * _d(attention)
        + Decimal("0.50") * _d(qualification)
        + Decimal("0.30") * _d(timing)
    )
    return _score(total)


def _geometric_mean(values: Sequence[Decimal]) -> Decimal:
    clean = [_clamp01(value) for value in values]
    if not clean or any(value <= 0 for value in clean):
        return Decimal("0")
    return Decimal(str(prod(float(value) for value in clean) ** (1 / len(clean))))


def score_confidence(
    *,
    direct_attribution_coverage: Any,
    feature_and_forward_completeness: Any,
    independent_sessions: int,
    research_eligible_candidates: int,
    closed_direct_trades: int,
    primary_effect_sign_consistency: Any = 0,
    median_abs_primary_session_spearman: Any = 0,
    distinct_regime_buckets: int = 0,
) -> dict[str, Any]:
    attribution = _clamp01(direct_attribution_coverage)
    completeness = _clamp01(feature_and_forward_completeness)

    session_ratio = _clamp01(Decimal(independent_sessions) / Decimal(MIN_INDEPENDENT_SESSIONS))
    candidate_ratio = _clamp01(
        Decimal(research_eligible_candidates) / Decimal(MIN_RESEARCH_ELIGIBLE_CANDIDATES)
    )
    trade_ratio = _clamp01(Decimal(closed_direct_trades) / Decimal(MIN_CLOSED_DIRECT_TRADES))
    sample = _geometric_mean((session_ratio, candidate_ratio, trade_ratio))

    if independent_sessions < 2:
        stability = Decimal("0")
    else:
        sign_consistency = _clamp01(primary_effect_sign_consistency)
        effect_persistence = _clamp01(
            abs(_d(median_abs_primary_session_spearman)) / Decimal("0.20")
        )
        stability = (
            Decimal("0.60") * sign_consistency
            + Decimal("0.40") * effect_persistence
        )

    regime = _clamp01(Decimal(distinct_regime_buckets) / Decimal("3"))

    total = Decimal("100") * (
        Decimal("0.30") * attribution
        + Decimal("0.25") * completeness
        + Decimal("0.20") * sample
        + Decimal("0.15") * stability
        + Decimal("0.10") * regime
    )

    minimums_met = (
        independent_sessions >= MIN_INDEPENDENT_SESSIONS
        and research_eligible_candidates >= MIN_RESEARCH_ELIGIBLE_CANDIDATES
        and closed_direct_trades >= MIN_CLOSED_DIRECT_TRADES
    )
    hard_cap_active = not minimums_met
    if hard_cap_active:
        total = min(total, PRE_SAMPLE_CONFIDENCE_CAP)

    missing_requirements: list[str] = []
    if independent_sessions < MIN_INDEPENDENT_SESSIONS:
        missing_requirements.append("MIN_INDEPENDENT_SESSIONS")
    if research_eligible_candidates < MIN_RESEARCH_ELIGIBLE_CANDIDATES:
        missing_requirements.append("MIN_RESEARCH_ELIGIBLE_CANDIDATES")
    if closed_direct_trades < MIN_CLOSED_DIRECT_TRADES:
        missing_requirements.append("MIN_CLOSED_DIRECT_TRADES")

    return {
        "score": _score(total),
        "components": {
            "attribution": _component(attribution),
            "completeness": _component(completeness),
            "sample": _component(sample),
            "stability": _component(stability),
            "regime": _component(regime),
        },
        "hard_cap_active": hard_cap_active,
        "missing_requirements": missing_requirements,
    }


def score_pretrade(
    feature_vector: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    attention = score_attention(feature_vector, configuration)
    qualification = score_qualification(feature_vector, configuration)
    timing = score_timing(feature_vector, configuration)
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "attention": attention,
        "qualification": qualification,
        "timing": timing,
        "pretrade_composite": pretrade_composite(
            attention=attention["score"],
            qualification=qualification["score"],
            timing=timing["score"],
        ),
    }
