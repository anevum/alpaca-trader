from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .math_kernel import forward_return, mae, mfe


METHODOLOGY_VERSION = "candidate-forward-crypto-v1"
HORIZONS_MINUTES = (1, 3, 5, 10, 15, 30, 60)

# Forward evidence is research-only and shares RHEN's telemetry transport with
# operational events. Keep each catch-up pass bounded so a historical backlog
# can never starve current runtime telemetry.
MAX_EMITTED_OUTCOMES_PER_RUN = 500


def _d(value: Any) -> Decimal | None:
    try:
        if value in (None, ""):
            return None
        return Decimal(str(value))
    except Exception:
        return None


def _stamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _bar_start(bar: dict[str, Any]) -> datetime | None:
    stamp = _stamp(bar.get("t"))
    return stamp.astimezone(timezone.utc) if stamp else None


def _reference_start(candidate: dict[str, Any]) -> datetime | None:
    features = candidate.get("features") or {}
    stamp = _stamp(features.get("bar_time"))
    if stamp:
        return stamp.astimezone(timezone.utc)
    observed = _stamp(candidate.get("observed_at"))
    return observed.astimezone(timezone.utc) - timedelta(minutes=1) if observed else None


def calculate_continuous_forward_outcome(
    candidate: dict[str, Any],
    bars: list[dict[str, Any]],
    *,
    horizon_minutes: int,
    now: datetime,
) -> dict[str, Any] | None:
    reference = _d(candidate.get("decision_reference_price"))
    start = _reference_start(candidate)
    if reference is None or reference <= 0 or start is None:
        return {
            "status": "error",
            "candidate_id": candidate.get("candidate_id"),
            "candidate_key": candidate.get("candidate_key"),
            "horizon_minutes": horizon_minutes,
            "reference_price": str(reference) if reference else None,
            "observation_end_at": None,
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": candidate.get("data_feed") or "alpaca-crypto",
            "bar_interval": candidate.get("bar_interval") or "1Min",
            "methodology_version": METHODOLOGY_VERSION,
            "details": {"reason": "invalid_reference_state", "continuous_market": True},
        }

    effective = start + timedelta(minutes=1)
    target = effective + timedelta(minutes=horizon_minutes)
    current = now.astimezone(timezone.utc)
    if current < target:
        return None

    window: list[tuple[datetime, dict[str, Any]]] = []
    terminal: dict[str, Any] | None = None
    for bar in bars:
        bar_start = _bar_start(bar)
        if bar_start is None:
            continue
        end = bar_start + timedelta(minutes=1)
        if end <= effective or end > target:
            continue
        window.append((end, bar))
        if end == target:
            terminal = bar

    if terminal is None:
        return {
            "status": "insufficient_future_data",
            "candidate_id": candidate.get("candidate_id"),
            "candidate_key": candidate.get("candidate_key"),
            "horizon_minutes": horizon_minutes,
            "reference_price": str(reference),
            "observation_end_at": target.isoformat(),
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": candidate.get("data_feed") or "alpaca-crypto",
            "bar_interval": candidate.get("bar_interval") or "1Min",
            "methodology_version": METHODOLOGY_VERSION,
            "details": {
                "reason": "exact_horizon_terminal_bar_unavailable",
                "continuous_market": True,
                "observed_bar_count": len(window),
            },
        }

    future = _d(terminal.get("c"))
    highs = [_d(row.get("h")) for _, row in window]
    lows = [_d(row.get("l")) for _, row in window]
    good_highs = [x for x in highs if x is not None and x > 0]
    good_lows = [x for x in lows if x is not None and x > 0]
    if future is None or future <= 0 or not good_highs or not good_lows:
        status = "error"
        fr = fav = adv = None
    else:
        status = "complete"
        fr = forward_return(reference, future)
        fav = mfe(reference, good_highs)
        adv = mae(reference, good_lows)

    return {
        "status": status,
        "candidate_id": candidate.get("candidate_id"),
        "candidate_key": candidate.get("candidate_key"),
        "horizon_minutes": horizon_minutes,
        "reference_price": str(reference),
        "observation_end_at": target.isoformat(),
        "forward_price": str(future) if future is not None else None,
        "forward_return": str(fr) if fr is not None else None,
        "max_favorable_return": str(fav) if fav is not None else None,
        "max_adverse_return": str(adv) if adv is not None else None,
        "provider": candidate.get("data_feed") or "alpaca-crypto",
        "bar_interval": candidate.get("bar_interval") or "1Min",
        "methodology_version": METHODOLOGY_VERSION,
        "market_lane": "crypto",
        "strategy_family": candidate.get("strategy_family"),
        "strategy_version_id": candidate.get("strategy_version_id"),
        "model_version": candidate.get("model_version"),
        "calibration_version": candidate.get("calibration_version"),
        "regime_version": candidate.get("regime_version"),
        "execution_adapter_version": candidate.get("execution_adapter_version"),
        "details": {
            "continuous_market": True,
            "reference_effective_at": effective.isoformat(),
            "requested_end_at": target.isoformat(),
            "observed_bar_count": len(window),
            "analytics_only": True,
        },
    }


@dataclass(slots=True)
class CryptoForwardEvidenceSummary:
    candidates: int = 0
    emitted: int = 0
    complete: int = 0
    incomplete: int = 0
    deferred: int = 0
    errors: int = 0


class CryptoForwardEvidenceRunner:
    def __init__(self, *, market_data: Any, event_sink: Any, evidence_reader: Any, state: Any):
        self.market_data = market_data
        self.event_sink = event_sink
        self.evidence_reader = evidence_reader
        self.state = state

    @staticmethod
    def _is_crypto(candidate: dict[str, Any]) -> bool:
        return (
            str(candidate.get("market_lane") or "").lower() == "crypto"
            or str((candidate.get("research_attribution") or {}).get("market") or "").lower() == "crypto"
            or str((candidate.get("features") or {}).get("market") or "").lower() == "crypto"
            or str(candidate.get("strategy_version_id") or "").upper().startswith("CRYPTO-")
        )

    async def run_recent(self, now: datetime | None = None) -> CryptoForwardEvidenceSummary:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        dates = {current.date(), (current - timedelta(days=1)).date()}
        rows: dict[Any, dict[str, Any]] = {}
        for day in sorted(dates):
            source = await self.evidence_reader(evidence_session=day.isoformat())
            for candidate in source.get("candidates") or []:
                if isinstance(candidate, dict) and self._is_crypto(candidate):
                    key = candidate.get("candidate_id") or candidate.get("candidate_key")
                    if key not in (None, ""):
                        rows[key] = dict(candidate)

        summary = CryptoForwardEvidenceSummary(candidates=len(rows))
        if not rows:
            self.state.crypto_forward_evidence_state = {
                "status": "healthy",
                "candidate_count": 0,
                "last_run_at": current.isoformat(),
            }
            return summary

        starts = [x for x in (_reference_start(row) for row in rows.values()) if x]
        symbols = sorted({str(row.get("symbol") or "").upper() for row in rows.values() if row.get("symbol")})
        if not starts or not symbols:
            return summary
        bars = await self.market_data.historical_bars_many(
            symbols,
            start=min(starts),
            end=current + timedelta(minutes=1),
        )

        for candidate in rows.values():
            completed = candidate.get("forward_outcomes") or {}
            symbol = str(candidate.get("symbol") or "").upper()
            for horizon in HORIZONS_MINUTES:
                prior = completed.get(str(horizon)) if isinstance(completed, dict) else None
                if isinstance(prior, dict) and prior.get("status") == "complete":
                    continue
                outcome = calculate_continuous_forward_outcome(
                    candidate,
                    bars.get(symbol, []),
                    horizon_minutes=horizon,
                    now=current,
                )
                if outcome is None:
                    continue

                # Incomplete/error states are diagnostic observations, not
                # finalized forward evidence. Re-emitting them every minute
                # previously flooded the shared telemetry queue with tens of
                # thousands of idempotent duplicates. Recompute them on the
                # next pass instead and persist only complete outcomes.
                if outcome["status"] == "insufficient_future_data":
                    summary.incomplete += 1
                    continue
                if outcome["status"] != "complete":
                    summary.errors += 1
                    continue

                summary.complete += 1
                if summary.emitted >= MAX_EMITTED_OUTCOMES_PER_RUN:
                    summary.deferred += 1
                    continue

                outcome["computed_at"] = current.isoformat()
                identity = candidate.get("candidate_id") or candidate.get("candidate_key")
                self.event_sink.emit(
                    event_type="candidate_forward_outcome",
                    event_key=f"candidate-forward-crypto:{identity}:{horizon}:{METHODOLOGY_VERSION}:complete",
                    occurred_at=current.isoformat(),
                    symbol=symbol,
                    payload=outcome,
                )
                summary.emitted += 1

        queue = getattr(self.event_sink, "queue", None)
        if queue is not None:
            await queue.join()
        self.state.crypto_forward_evidence_state = {
            "status": "healthy" if summary.errors == 0 else "degraded",
            "candidate_count": summary.candidates,
            "emitted": summary.emitted,
            "complete": summary.complete,
            "incomplete": summary.incomplete,
            "deferred": summary.deferred,
            "errors": summary.errors,
            "last_run_at": current.isoformat(),
        }
        return summary
