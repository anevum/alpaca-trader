"""Point-in-time adapter over the existing ASC-002 deterministic classifier."""
import math

from app.adaptive_policy import fingerprint
from app.market_fabric.contracts import utc
from app.research_agent.nostra_regime import build_market_state, classify_regime, REQUIRED_FEATURES, METHODOLOGY_VERSION


def observe_regime(*, observed_at, feature_as_of, benchmark_returns, candidate_summary,
                   market_quality=None, session_context=None):
    now, as_of = utc(observed_at), utc(feature_as_of)
    if as_of > now:
        raise ValueError("future features prohibited")
    # Inputs retain the research schema; no learned replacement and no API/model calls.
    state = build_market_state(benchmark_returns=benchmark_returns, candidate_summary=candidate_summary,
                               market_quality=market_quality, session_context=session_context)
    missing = [k for k in REQUIRED_FEATURES if state[k] is None or not math.isfinite(float(state[k]))]
    stale = (now-as_of).total_seconds() > 600
    if any(isinstance(v, float) and not math.isfinite(v) for v in state.values()):
        raise ValueError("nonfinite regime feature")
    result = classify_regime(state)
    return {**result, "observed_at": now.isoformat(), "feature_as_of": as_of.isoformat(),
            "methodology_version": METHODOLOGY_VERSION, "feature_fingerprint": fingerprint(state),
            "primary_regime": "UNKNOWN" if missing or stale else result["regime"],
            "missing_features": missing, "stale": stale, "quality_state": "UNAVAILABLE" if missing else "STALE" if stale else "LIVE",
            "execution_authority": False}
