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
