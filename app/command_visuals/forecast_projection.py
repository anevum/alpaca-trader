from datetime import timedelta

from app.market_fabric.contracts import utc, number


def valid_forecast(forecast, now):
    required = ("forecast_id", "symbol", "issued_at", "feature_as_of", "horizon_seconds", "expires_at", "model_version", "methodology_version", "central_path", "quality_state")
    if any(not forecast.get(k) for k in required):
        raise ValueError("forecast lineage incomplete")
    issue, feature, expiry = (utc(forecast[k]) for k in ("issued_at", "feature_as_of", "expires_at"))
    horizon = forecast["horizon_seconds"]
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0 or feature > issue or issue > utc(now) or not issue < expiry <= issue+timedelta(seconds=horizon):
        raise ValueError("forecast point-in-time/horizon violation")
    if utc(now) >= expiry:
        return None
    if forecast["quality_state"] != "LIVE":
        return None
    central = forecast["central_path"]
    timestamps = [utc(p["timestamp"]) for p in central]
    if timestamps != sorted(set(timestamps)) or any(t < issue or t > expiry for t in timestamps):
        raise ValueError("forecast path outside horizon")
    for p in central:
        number(p["value"])
    lower, upper = forecast.get("lower_path"), forecast.get("upper_path")
    if (lower is None) != (upper is None):
        raise ValueError("incomplete uncertainty band")
    if lower is not None:
        if [utc(p["timestamp"]) for p in lower] != timestamps or [utc(p["timestamp"]) for p in upper] != timestamps:
            raise ValueError("uncertainty timestamp mismatch")
        if any(not number(l["value"]) <= number(c["value"]) <= number(u["value"]) for l,c,u in zip(lower,central,upper)):
            raise ValueError("invalid uncertainty ordering")
    confidence = forecast.get("confidence_level")
    if confidence is not None and not 0 < number(confidence) < 1:
        raise ValueError("invalid confidence level")
    return {**forecast, "provenance": "FORECAST", "source": "NOSTRA", "uncertainty_state": "AVAILABLE" if lower is not None else "UNAVAILABLE"}


def project_nostra_forecast(record, reference, now):
    """Project an existing canonical return forecast onto its observed reference.

    Only start and terminal expected values are drawn; the connecting line is a
    visual interpolation, not a new model of intra-horizon market motion. Bands
    are absent unless the canonical record provides return bounds.
    """
    if record.get("execution_authority") is not False or record.get("research_only") is not True or record.get("target_kind") != "return":
        raise ValueError("canonical research return forecast required")
    if record.get("authority_state") not in {"NORMAL", "LOW_SUPPORT"}:
        return None
    if reference.get("provenance") != "OBSERVED" or not reference.get("source") or reference.get("quality_state") != "LIVE":
        raise ValueError("observed forecast reference required")
    if reference.get("symbol") != record.get("symbol") or reference.get("snapshot_id") != record.get("snapshot_id"):
        raise ValueError("forecast reference lineage mismatch")
    issue, feature = utc(record["generated_at"]), utc(record["as_of_timestamp"])
    if utc(reference["timestamp"]) > feature or feature-utc(reference["timestamp"]) > timedelta(seconds=120):
        raise ValueError("stale or future forecast reference")
    horizon = record["horizon_minutes"]
    if type(horizon) is not int or not 1 <= horizon <= 1440:
        raise ValueError("invalid forecast horizon")
    expiry = issue+timedelta(minutes=horizon)
    base = number(reference["value"],positive=True)
    terminal = base*(1+number(record["forecast_payload"]["expected_return"]))
    number(terminal,positive=True)
    def path(end):
        return [{"timestamp":issue.isoformat(),"value":base},{"timestamp":expiry.isoformat(),"value":end}]
    uncertainty = record.get("uncertainty") or {}
    lower, upper = uncertainty.get("lower_return"), uncertainty.get("upper_return")
    if (lower is None) != (upper is None):
        raise ValueError("canonical uncertainty bounds incomplete")
    result = {"forecast_id":record["forecast_id"],"symbol":record["symbol"],"issued_at":issue.isoformat(),
        "feature_as_of":feature.isoformat(),"horizon_seconds":horizon*60,"expires_at":expiry.isoformat(),
        "model_version":record["model_version"],"methodology_version":record["methodology_version"],
        "projection_methodology_version":"nostra-observed-reference-terminal-v1","snapshot_id":record["snapshot_id"],
        "central_path":path(terminal),"lower_path":path(base*(1+number(lower))) if lower is not None else None,
        "upper_path":path(base*(1+number(upper))) if upper is not None else None,
        "confidence_level":uncertainty.get("confidence_level"),"quality_state":"LIVE",
        "authority_state":record.get("authority_state"),"entry_authority":False,
        "interpolation":"VISUAL_LINEAR_ENDPOINT_CONNECTION", "reference_source":reference["source"]}
    return valid_forecast(result,now)
