from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

METHODOLOGY_VERSION = "asc-strategy-health-v1"

HEALTHY = "HEALTHY"
WATCH = "WATCH"
DEGRADED = "DEGRADED"
BLOCKED = "BLOCKED"
COLLECTING = "COLLECTING"

NORMAL = "NORMAL"
ADAPT = "ADAPT"
RESEARCH = "RESEARCH"
DEFENSIVE = "DEFENSIVE"


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return value


def _dimension(
    status: str,
    *reason_codes: str,
    metrics: Mapping[str, Any] | None = None,
    observations: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason_codes": [code for code in reason_codes if code],
        "metrics": _json_safe(dict(metrics or {})),
        "observations": observations,
    }


def _metrics(report: Mapping[str, Any] | None) -> Mapping[str, Any]:
    item = _m(report)
    metrics = item.get("metrics")
    if isinstance(metrics, Mapping):
        return metrics
    performance = item.get("performance")
    return performance if isinstance(performance, Mapping) else {}


def _runtime(report: Mapping[str, Any] | None) -> Mapping[str, Any]:
    item = _m(report)
    runtime = item.get("runtime")
    if isinstance(runtime, Mapping):
        return runtime
    operational = item.get("operational_health")
    return operational if isinstance(operational, Mapping) else {}


def _forward_status(report: Mapping[str, Any] | None) -> Mapping[str, Any]:
    item = _m(report)
    candidate = _m(item.get("candidate_forward_evidence"))
    status = candidate.get("status")
    if isinstance(status, Mapping):
        return status
    analysis = _m(item.get("candidate_analysis"))
    status = analysis.get("forward_outcome_status")
    return status if isinstance(status, Mapping) else {}


def _evidence_integrity(report: Mapping[str, Any] | None) -> dict[str, Any]:
    item = _m(report)
    runtime = _runtime(item)
    forward = _forward_status(item)
    errors = int(_d(forward.get("error_rows")))
    incomplete = int(_d(forward.get("incomplete_rows")))
    complete = int(_d(forward.get("complete_rows")))
    total = complete + incomplete + errors
    coverage = Decimal(complete) / Decimal(total) if total > 0 else None

    reason_codes: list[str] = []
    blocked = False

    if runtime.get("reconciliation_safe") is False:
        blocked = True
        reason_codes.append("RECONCILIATION_UNSAFE")
    if runtime.get("last_error"):
        blocked = True
        reason_codes.append("RUNTIME_ERROR")
    if runtime.get("persistence_error"):
        blocked = True
        reason_codes.append("PERSISTENCE_ERROR")
    if errors:
        blocked = True
        reason_codes.append("FORWARD_OUTCOME_ERRORS")

    completeness = str(item.get("completeness_state") or "").upper()
    if completeness == "INCOMPLETE":
        blocked = True
        reason_codes.append("INCOMPLETE_CANONICAL_REPORT")

    if blocked:
        return _dimension(
            BLOCKED,
            *reason_codes,
            metrics={
                "forward_complete_rows": complete,
                "forward_incomplete_rows": incomplete,
                "forward_error_rows": errors,
                "forward_completion_ratio": coverage,
            },
            observations=total or None,
        )

    if total == 0:
        return _dimension(
            COLLECTING,
            "FORWARD_EVIDENCE_NOT_YET_AVAILABLE",
            metrics={"forward_completion_ratio": None},
        )

    reason = (
        "CANONICAL_EVIDENCE_USABLE_WITH_KNOWN_HORIZON_LIMITATIONS"
        if incomplete
        else "CANONICAL_EVIDENCE_USABLE"
    )
    return _dimension(
        HEALTHY,
        reason,
        metrics={
            "forward_complete_rows": complete,
            "forward_incomplete_rows": incomplete,
            "forward_error_rows": errors,
            "forward_completion_ratio": coverage,
        },
        observations=total,
    )


def _edge_health(
    daily_report: Mapping[str, Any] | None,
    weekly_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    daily = _metrics(daily_report)
    weekly = _metrics(weekly_report)

    daily_trades = int(_d(daily.get("trade_count") or daily.get("completed_trades")))
    weekly_trades = int(_d(weekly.get("completed_trades") or weekly.get("trade_count")))
    total_trades = weekly_trades or daily_trades

    expectancy = weekly.get("expectancy")
    profit_factor = weekly.get("profit_factor")
    if expectancy in (None, ""):
        expectancy = daily.get("expectancy")
    if profit_factor in (None, ""):
        profit_factor = daily.get("profit_factor")

    metrics = {
        "daily_trades": daily_trades,
        "rolling_trades": total_trades,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "win_rate": weekly.get("win_rate", daily.get("win_rate")),
    }

    if total_trades < 20:
        return _dimension(
            COLLECTING,
            "INSUFFICIENT_REALIZED_SAMPLE",
            metrics=metrics,
            observations=total_trades,
        )

    ev = _d(expectancy)
    pf = _d(profit_factor) if profit_factor not in (None, "") else None
    if ev < 0 and pf is not None and pf < Decimal("0.85"):
        return _dimension(
            DEGRADED,
            "NEGATIVE_EXPECTANCY_AND_WEAK_PROFIT_FACTOR",
            metrics=metrics,
            observations=total_trades,
        )
    if ev < 0 or (pf is not None and pf < Decimal("1")):
        return _dimension(
            WATCH,
            "REALIZED_EDGE_WEAKENING",
            metrics=metrics,
            observations=total_trades,
        )
    return _dimension(
        HEALTHY,
        "REALIZED_EDGE_WITHIN_OBSERVED_RANGE",
        metrics=metrics,
        observations=total_trades,
    )


def _execution_health(report: Mapping[str, Any] | None) -> dict[str, Any]:
    runtime = _runtime(report)
    reason_codes: list[str] = []
    if runtime.get("reconciliation_safe") is False:
        reason_codes.append("RECONCILIATION_UNSAFE")
    if runtime.get("last_error"):
        reason_codes.append("RUNTIME_ERROR")
    if runtime.get("persistence_error"):
        reason_codes.append("PERSISTENCE_ERROR")
    if reason_codes:
        return _dimension(
            DEGRADED,
            *reason_codes,
            metrics={
                "reconciliation_safe": runtime.get("reconciliation_safe"),
                "last_error": runtime.get("last_error"),
                "persistence_error": runtime.get("persistence_error"),
            },
        )

    execution_quality = _m(
        _m(_m(report).get("cross_day_trade_quality")).get("execution_shortfall")
    )
    if not runtime and not execution_quality:
        return _dimension(COLLECTING, "EXECUTION_HEALTH_INPUTS_NOT_AVAILABLE")

    return _dimension(
        HEALTHY,
        "NO_EXPLICIT_EXECUTION_DEFECT",
        metrics={
            "reconciliation_safe": runtime.get("reconciliation_safe"),
            "execution_shortfall": execution_quality,
        },
    )


def _regime_health(nostra_state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = _m(nostra_state)
    if not state:
        return _dimension(COLLECTING, "NOSTRA_REGIME_MODEL_NOT_CONNECTED")

    unknown = _d(state.get("unknown_probability"))
    confidence = _d(state.get("confidence"))
    familiarity = state.get("market_familiarity")
    familiarity_d = _d(familiarity) if familiarity not in (None, "") else None
    metrics = {
        "regime": state.get("regime"),
        "confidence": confidence,
        "unknown_probability": unknown,
        "market_familiarity": familiarity_d,
    }

    if familiarity_d is not None and familiarity_d < Decimal("0.35"):
        return _dimension(DEGRADED, "LOW_MARKET_FAMILIARITY", metrics=metrics)
    if unknown >= Decimal("0.40"):
        return _dimension(DEGRADED, "UNKNOWN_REGIME_DOMINANT", metrics=metrics)
    if (
        familiarity_d is not None
        and familiarity_d < Decimal("0.60")
    ) or confidence < Decimal("0.55"):
        return _dimension(WATCH, "REGIME_UNCERTAINTY_ELEVATED", metrics=metrics)
    return _dimension(HEALTHY, "REGIME_STATE_CONFIDENT", metrics=metrics)


def _calibration_health(nostra_state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = _m(nostra_state)
    calibration = _m(state.get("calibration"))
    if not calibration:
        return _dimension(COLLECTING, "NOSTRA_CALIBRATION_NOT_AVAILABLE")

    observations = int(_d(calibration.get("observations")))
    brier_skill = (
        _d(calibration.get("brier_skill_score"))
        if calibration.get("brier_skill_score") not in (None, "")
        else None
    )
    metrics = {
        "observations": observations,
        "multiclass_brier": calibration.get("multiclass_brier"),
        "base_rate_brier": calibration.get("base_rate_brier"),
        "brier_skill_score": calibration.get("brier_skill_score"),
        "log_loss": calibration.get("log_loss"),
        "base_rate_log_loss": calibration.get("base_rate_log_loss"),
        "log_loss_skill_score": calibration.get("log_loss_skill_score"),
        "top1_accuracy": calibration.get("top1_accuracy"),
        "majority_class_accuracy": calibration.get("majority_class_accuracy"),
        "brier_score": calibration.get("brier_score"),
        "calibration_slope": calibration.get("calibration_slope"),
    }
    if observations < 100:
        return _dimension(
            COLLECTING,
            "CALIBRATION_SAMPLE_IMMATURE",
            metrics=metrics,
            observations=observations,
        )

    # NOSTRA transition forecasts are multiclass. Compare their probability
    # quality against an empirical base-rate forecast instead of inventing a
    # scalar calibration slope.
    if brier_skill is not None:
        if brier_skill < Decimal("-0.10"):
            return _dimension(
                DEGRADED,
                "FORECAST_CALIBRATION_SKILL_DEGRADED",
                metrics=metrics,
                observations=observations,
            )
        if brier_skill <= Decimal("0"):
            return _dimension(
                WATCH,
                "FORECAST_CALIBRATION_NO_SKILL",
                metrics=metrics,
                observations=observations,
            )
        return _dimension(
            HEALTHY,
            "FORECAST_CALIBRATION_POSITIVE_SKILL",
            metrics=metrics,
            observations=observations,
        )

    # Preserve support for later scalar probabilistic models that expose a
    # conventional calibration slope.
    slope = calibration.get("calibration_slope")
    if slope in (None, ""):
        return _dimension(
            COLLECTING,
            "CALIBRATION_METRIC_NOT_AVAILABLE",
            metrics=metrics,
            observations=observations,
        )
    slope_d = _d(slope, Decimal("1"))
    if slope_d < Decimal("0.6") or slope_d > Decimal("1.4"):
        return _dimension(
            DEGRADED,
            "FORECAST_CALIBRATION_DRIFT",
            metrics=metrics,
            observations=observations,
        )
    if slope_d < Decimal("0.8") or slope_d > Decimal("1.2"):
        return _dimension(
            WATCH,
            "FORECAST_CALIBRATION_WATCH",
            metrics=metrics,
            observations=observations,
        )
    return _dimension(
        HEALTHY,
        "FORECAST_CALIBRATION_STABLE",
        metrics=metrics,
        observations=observations,
    )


def _distribution_health(nostra_state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = _m(nostra_state)
    familiarity = state.get("market_familiarity")
    if familiarity in (None, ""):
        return _dimension(COLLECTING, "MARKET_FAMILIARITY_NOT_AVAILABLE")
    value = _d(familiarity)
    metrics = {"market_familiarity": value}
    if value < Decimal("0.35"):
        return _dimension(
            DEGRADED,
            "OUT_OF_DISTRIBUTION_MARKET_STATE",
            metrics=metrics,
        )
    if value < Decimal("0.60"):
        return _dimension(WATCH, "MARKET_DISTRIBUTION_DRIFT", metrics=metrics)
    return _dimension(HEALTHY, "MARKET_DISTRIBUTION_FAMILIAR", metrics=metrics)


def _parameter_health(parameter_pressure: Mapping[str, Any] | None) -> dict[str, Any]:
    pressure = _m(parameter_pressure)
    rows = _rows(pressure.get("parameters"))
    if not rows:
        return _dimension(COLLECTING, "PARAMETER_PRESSURE_NOT_AVAILABLE")

    mature_rows = [
        row
        for row in rows
        if int(_d(row.get("observations"))) >= 5
    ]
    if not mature_rows:
        return _dimension(
            COLLECTING,
            "PARAMETER_PRESSURE_SAMPLE_IMMATURE",
            metrics={
                "parameter_count": len(rows),
                "mature_parameter_count": 0,
                "max_observations": max(
                    (int(_d(row.get("observations"))) for row in rows),
                    default=0,
                ),
            },
        )

    strongest = Decimal("0")
    boundary_parameters: list[str] = []
    for row in mature_rows:
        fraction = _d(row.get("boundary_fraction"))
        if fraction > strongest:
            strongest = fraction
        if fraction >= Decimal("0.60"):
            boundary_parameters.append(str(row.get("parameter") or "unknown"))

    metrics = {
        "max_boundary_fraction": strongest,
        "boundary_parameters": boundary_parameters,
        "parameter_count": len(rows),
        "mature_parameter_count": len(mature_rows),
    }
    if strongest >= Decimal("0.60"):
        return _dimension(
            DEGRADED,
            "PERSISTENT_PARAMETER_BOUNDARY_PRESSURE",
            metrics=metrics,
            observations=len(rows),
        )
    if strongest >= Decimal("0.30"):
        return _dimension(
            WATCH,
            "PARAMETER_BOUNDARY_PRESSURE",
            metrics=metrics,
            observations=len(rows),
        )
    return _dimension(
        HEALTHY,
        "PARAMETERS_WITHIN_ADAPTIVE_ENVELOPE",
        metrics=metrics,
        observations=len(rows),
    )


def _challenger_health(report: Mapping[str, Any] | None) -> dict[str, Any]:
    ads = _m(_m(report).get("ads002_v2"))
    models = _rows(ads.get("models"))
    if not models:
        return _dimension(COLLECTING, "ADS002_V2_EVIDENCE_NOT_AVAILABLE")

    mature: list[Mapping[str, Any]] = []
    strongest_confidence = Decimal("0")
    strongest_model = None
    strongest_rho = None
    for model in models:
        confidence = _m(model.get("confidence"))
        score = _d(confidence.get("score"))
        if score > strongest_confidence:
            strongest_confidence = score
            strongest_model = model.get("model_key")
            strongest_rho = model.get("session_spearman_15m")
        if confidence.get("minimums_met") is True:
            mature.append(model)

    metrics = {
        "active_model_count": len(models),
        "mature_model_count": len(mature),
        "strongest_confidence": strongest_confidence,
        "strongest_model": strongest_model,
        "strongest_session_spearman_15m": strongest_rho,
    }
    if not mature:
        return _dimension(
            COLLECTING,
            "CHALLENGER_MINIMUM_EVIDENCE_NOT_MET",
            metrics=metrics,
            observations=len(models),
        )

    positive = [
        model
        for model in mature
        if _d(model.get("session_spearman_15m")) > 0
        and _d(_m(model.get("confidence")).get("score")) >= Decimal("0.70")
    ]
    if positive:
        return _dimension(
            WATCH,
            "VALIDATED_CHALLENGER_PRESSURE",
            metrics=metrics,
            observations=len(models),
        )
    return _dimension(
        HEALTHY,
        "NO_VALIDATED_CHALLENGER_PRESSURE",
        metrics=metrics,
        observations=len(models),
    )


def _control_state(
    dimensions: Mapping[str, Mapping[str, Any]],
) -> tuple[str, list[str]]:
    evidence = str(_m(dimensions.get("evidence_integrity")).get("status") or "")
    if evidence == BLOCKED:
        return DEFENSIVE, ["EVIDENCE_INTEGRITY_BLOCKER"]

    degraded = [
        name
        for name, value in dimensions.items()
        if str(_m(value).get("status") or "") == DEGRADED
    ]
    watched = [
        name
        for name, value in dimensions.items()
        if str(_m(value).get("status") or "") == WATCH
    ]

    if "execution" in degraded:
        return DEFENSIVE, ["DEGRADED_EXECUTION"]
    if any(
        name in degraded
        for name in (
            "edge",
            "parameter_pressure",
            "distribution",
            "regime",
            "calibration",
        )
    ):
        return RESEARCH, [f"DEGRADED_{name.upper()}" for name in degraded]
    if any(
        name in watched
        for name in ("evidence_integrity", "calibration", "challenger_pressure")
    ):
        return RESEARCH, [f"WATCH_{name.upper()}" for name in watched]
    adaptive_watch = [
        name
        for name in watched
        if name in {"edge", "regime", "distribution", "parameter_pressure"}
    ]
    if adaptive_watch:
        return ADAPT, [f"WATCH_{name.upper()}" for name in adaptive_watch]
    return NORMAL, ["NO_ESCALATION_TRIGGER"]


def compute_strategy_health(
    *,
    daily_report: Mapping[str, Any] | None,
    weekly_report: Mapping[str, Any] | None = None,
    nostra_state: Mapping[str, Any] | None = None,
    parameter_pressure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a read-only ASC strategy-health snapshot.

    This module has no execution, broker, sizing, or configuration mutation
    authority. It only classifies evidence already present in canonical reports
    and optional NOSTRA/parameter-pressure inputs.
    """

    dimensions = {
        "edge": _edge_health(daily_report, weekly_report),
        "regime": _regime_health(nostra_state),
        "calibration": _calibration_health(nostra_state),
        "execution": _execution_health(daily_report or weekly_report),
        "distribution": _distribution_health(nostra_state),
        "parameter_pressure": _parameter_health(parameter_pressure),
        "challenger_pressure": _challenger_health(daily_report),
        "evidence_integrity": _evidence_integrity(daily_report or weekly_report),
    }
    state, reasons = _control_state(dimensions)
    source = _m(daily_report) or _m(weekly_report)

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "source_report_key": source.get("report_key"),
        "source_session": source.get("session") or source.get("period_end"),
        "control_state": state,
        "control_reason_codes": reasons,
        "dimensions": dimensions,
        "read_only": True,
        "execution_authority": False,
        "risk_or_sizing_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
