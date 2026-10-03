from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

import psycopg

from foundation.report_read import _candidate_identity, _decision_candidates
from app.nostra.models import (
    MIN_INDEPENDENT_CYCLES as DRIFT_MIN_INDEPENDENT_CYCLES,
    MODEL_ID as DRIFT_MODEL_ID,
    MODEL_VERSION as DRIFT_MODEL_VERSION,
)


UTC = timezone.utc
FORECAST_HORIZON_MINUTES = 10
BASELINE_MODEL_ID = "zero_return"
SCHEMA_VERSION = "nostra-work-v1"


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _point_in_time_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    identity = _candidate_identity(candidate)
    symbol = str(candidate.get("symbol") or "").strip().upper()
    observed_at = _as_utc(candidate.get("observed_at"))
    features = candidate.get("features")
    if not identity or not symbol or observed_at is None or not isinstance(features, dict):
        return None

    # Decision-cycle candidates are the canonical point-in-time source. Do not
    # pass any post-event material through the forecasting gateway.
    forbidden = {
        "forward_outcomes",
        "outcomes",
        "realized_return",
        "realized_payload",
        "realized_regime",
        "max_favorable_return",
        "max_adverse_return",
    }
    if forbidden.intersection(candidate):
        return None

    scan_cycle = candidate.get("scan_cycle")
    return {
        "candidate_identity": identity,
        "symbol": symbol,
        "market_lane": "crypto",
        "observed_at": observed_at.isoformat(),
        "run_id": candidate.get("run_id"),
        "strategy_version_id": candidate.get("strategy_version_id"),
        "features": features,
        "scan_cycle": dict(scan_cycle) if isinstance(scan_cycle, dict) else {},
        "research_attribution": (
            dict(candidate.get("research_attribution"))
            if isinstance(candidate.get("research_attribution"), dict)
            else {}
        ),
    }


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _baseline_evaluation(
    cur: psycopg.Cursor[Any],
    *,
    current: datetime,
    lookback_hours: int,
    model_id: str = BASELINE_MODEL_ID,
) -> dict[str, Any] | None:
    start = current - timedelta(hours=max(1, lookback_hours))
    cur.execute(
        """
        select
            s.score_id,
            f.model_version,
            f.generated_at,
            o.observed_at,
            f.payload->'forecast_payload'->>'expected_return' as expected_return,
            o.payload->'realized_payload'->>'forward_return' as realized_return,
            s.payload->'metrics'->>'absolute_error' as absolute_error,
            s.payload->'metrics'->>'squared_error' as squared_error,
            s.payload->'baseline_metrics'->>'absolute_error' as baseline_absolute_error,
            s.payload->'baseline_metrics'->>'squared_error' as baseline_squared_error
        from nostra.evidence_scores s
        join nostra.evidence_forecasts f
          on f.forecast_id = s.forecast_id
        join nostra.evidence_outcomes o
          on o.outcome_id = s.outcome_id
        where f.horizon_minutes = %s
          and f.target_kind = 'return'
          and f.model_id = %s
          and o.observed_at >= %s
          and o.observed_at <= %s
        order by o.observed_at asc, s.created_at asc, s.score_id asc
        limit 10000
        """,
        (FORECAST_HORIZON_MINUTES, model_id, start, current),
    )

    observations: list[dict[str, Any]] = []
    for (
        score_id,
        model_version,
        generated_at,
        observed_at,
        expected_return,
        realized_return,
        absolute_error,
        squared_error,
        baseline_absolute_error,
        baseline_squared_error,
    ) in cur.fetchall():
        expected = _finite_float(expected_return)
        realized = _finite_float(realized_return)
        absolute = _finite_float(absolute_error)
        squared = _finite_float(squared_error)
        baseline_absolute = _finite_float(baseline_absolute_error)
        baseline_squared = _finite_float(baseline_squared_error)
        observed = _as_utc(observed_at)
        generated = _as_utc(generated_at)
        if (
            not score_id
            or not model_version
            or expected is None
            or realized is None
            or absolute is None
            or squared is None
            or baseline_absolute is None
            or baseline_squared is None
            or observed is None
            or generated is None
            or observed <= generated
        ):
            continue
        observations.append(
            {
                "score_id": str(score_id),
                "model_version": str(model_version),
                "observed_at": observed,
                "expected_return": expected,
                "realized_return": realized,
                "absolute_error": absolute,
                "squared_error": squared,
                "baseline_absolute_error": baseline_absolute,
                "baseline_squared_error": baseline_squared,
            }
        )

    if not observations:
        return None

    expected_values = [row["expected_return"] for row in observations]
    realized_values = [row["realized_return"] for row in observations]
    absolute_errors = [row["absolute_error"] for row in observations]
    squared_errors = [row["squared_error"] for row in observations]
    baseline_absolute_errors = [row["baseline_absolute_error"] for row in observations]
    baseline_squared_errors = [row["baseline_squared_error"] for row in observations]
    residuals = [
        row["realized_return"] - row["expected_return"]
        for row in observations
    ]

    count = len(observations)
    mean_expected = sum(expected_values) / count
    mean_realized = sum(realized_values) / count
    mean_absolute = sum(absolute_errors) / count
    mean_squared = sum(squared_errors) / count
    baseline_mean_absolute = sum(baseline_absolute_errors) / count
    baseline_mean_squared = sum(baseline_squared_errors) / count
    residual_mean = sum(residuals) / count
    residual_variance = sum(
        (value - residual_mean) ** 2 for value in residuals
    ) / count

    return {
        "schema_version": "nostra-model-evaluation-work-v1",
        "model_id": model_id,
        "model_version": observations[-1]["model_version"],
        "horizon_minutes": FORECAST_HORIZON_MINUTES,
        "target_kind": "return",
        "window_start": observations[0]["observed_at"].isoformat(),
        "window_end": observations[-1]["observed_at"].isoformat(),
        "sample_count": count,
        "through_score_id": observations[-1]["score_id"],
        "metrics": {
            "mean_expected_return": mean_expected,
            "mean_realized_return": mean_realized,
            "bias": mean_expected - mean_realized,
            "mean_absolute_error": mean_absolute,
            "mean_squared_error": mean_squared,
            "root_mean_squared_error": math.sqrt(mean_squared),
            "baseline_mean_absolute_error": baseline_mean_absolute,
            "baseline_mean_squared_error": baseline_mean_squared,
            "mae_skill_vs_zero": (
                1.0 - mean_absolute / baseline_mean_absolute
                if baseline_mean_absolute > 0 else 0.0
            ),
            "mse_skill_vs_zero": (
                1.0 - mean_squared / baseline_mean_squared
                if baseline_mean_squared > 0 else 0.0
            ),
        },
        "calibration": {
            "residual_mean": residual_mean,
            "residual_stddev": math.sqrt(max(0.0, residual_variance)),
            "realized_return_quantiles": {
                "p05": _quantile(realized_values, 0.05),
                "p25": _quantile(realized_values, 0.25),
                "p50": _quantile(realized_values, 0.50),
                "p75": _quantile(realized_values, 0.75),
                "p95": _quantile(realized_values, 0.95),
            },
            "absolute_error_quantiles": {
                "p50": _quantile(absolute_errors, 0.50),
                "p90": _quantile(absolute_errors, 0.90),
                "p95": _quantile(absolute_errors, 0.95),
            },
        },
        "research_only": True,
        "execution_authority": False,
    }


def _drift_training_state(
    cur: psycopg.Cursor[Any],
    *,
    cutoff: datetime,
    lookback_hours: int,
) -> dict[str, Any]:
    start = cutoff - timedelta(hours=max(1, lookback_hours))
    cur.execute(
        """
        select
            s.score_id,
            nullif(sn.payload->'source'->>'scan_cycle_id','') as scan_cycle_id,
            o.observed_at,
            o.payload->'realized_payload'->>'forward_return' as realized_return
        from nostra.evidence_scores s
        join nostra.evidence_forecasts f
          on f.forecast_id = s.forecast_id
        join nostra.evidence_outcomes o
          on o.outcome_id = s.outcome_id
        join nostra.evidence_snapshots sn
          on sn.snapshot_id = f.snapshot_id
        where f.horizon_minutes = %s
          and f.target_kind = 'return'
          and f.model_id = %s
          and o.observed_at >= %s
          and o.observed_at < %s
        order by o.observed_at asc, s.created_at asc, s.score_id asc
        limit 10000
        """,
        (FORECAST_HORIZON_MINUTES, BASELINE_MODEL_ID, start, cutoff),
    )

    by_cycle: dict[str, list[float]] = {}
    observed_times: list[datetime] = []
    through_score_id: str | None = None
    raw_count = 0
    for score_id, cycle_id, observed_at, realized_return in cur.fetchall():
        realized = _finite_float(realized_return)
        observed = _as_utc(observed_at)
        cycle = str(cycle_id or "").strip()
        if not score_id or not cycle or realized is None or observed is None:
            continue
        by_cycle.setdefault(cycle, []).append(realized)
        observed_times.append(observed)
        through_score_id = str(score_id)
        raw_count += 1

    cycle_means = [
        sum(values) / len(values)
        for values in by_cycle.values()
        if values
    ]
    independent_cycles = len(cycle_means)
    mean_cycle_return = (
        sum(cycle_means) / independent_cycles
        if independent_cycles
        else None
    )
    eligible = (
        independent_cycles >= DRIFT_MIN_INDEPENDENT_CYCLES
        and mean_cycle_return is not None
        and through_score_id is not None
    )
    return {
        "schema_version": "nostra-drift-training-state-v1",
        "model_id": DRIFT_MODEL_ID,
        "model_version": DRIFT_MODEL_VERSION,
        "training_cutoff": cutoff.isoformat(),
        "window_start": min(observed_times).isoformat() if observed_times else None,
        "window_end": max(observed_times).isoformat() if observed_times else None,
        "raw_outcome_count": raw_count,
        "independent_cycles": independent_cycles,
        "minimum_independent_cycles": DRIFT_MIN_INDEPENDENT_CYCLES,
        "mean_cycle_return": mean_cycle_return,
        "through_score_id": through_score_id,
        "eligible": eligible,
        "reason": "ready" if eligible else "insufficient_independent_cycles",
        "research_only": True,
        "execution_authority": False,
    }


def read_nostra_work(
    database_url: str,
    *,
    now: datetime | None = None,
    forecast_lookback_minutes: int = 5,
    score_lookback_hours: int = 6,
    evaluation_lookback_hours: int = 24,
    training_lookback_hours: int = 24,
    limit: int = 500,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    forecast_start = current - timedelta(minutes=max(forecast_lookback_minutes, 1))
    score_start = current - timedelta(hours=max(score_lookback_hours, 1))

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            # Foundation Postgres runs in a small shared-memory container.
            # NOSTRA's evidence joins can otherwise trigger parallel workers
            # that request dynamic shared-memory segments and fail the entire
            # read with "No space left on device". Keep this read path
            # deterministic and bounded without changing its evidence window.
            cur.execute("set local max_parallel_workers_per_gather = 0")
            cur.execute("set local work_mem = '4MB'")
            raw_candidates = _decision_candidates(
                cur,
                start=forecast_start,
                end=current + timedelta(seconds=1),
                crypto=True,
                inherit_event_strategy_for_crypto=False,
                result_limit=max(1, min(limit, 2000)),
            )
            point_candidates = [
                candidate
                for raw in raw_candidates
                if (candidate := _point_in_time_candidate(raw)) is not None
            ]

            candidate_times = [
                stamp
                for candidate in point_candidates
                if (stamp := _as_utc(candidate.get("observed_at"))) is not None
            ]
            training_cutoff = (
                min(candidate_times) - timedelta(microseconds=1)
                if candidate_times
                else current
            )
            drift_training_state = _drift_training_state(
                cur,
                cutoff=training_cutoff,
                lookback_hours=training_lookback_hours,
            )
            desired_models = [BASELINE_MODEL_ID]
            if drift_training_state.get("eligible") is True:
                desired_models.append(DRIFT_MODEL_ID)

            cur.execute(
                """
                select
                    payload->'provenance'->>'candidate_identity',
                    model_id
                from nostra.evidence_forecasts
                where generated_at >= %s
                  and horizon_minutes = %s
                  and target_kind = 'return'
                  and model_id = any(%s)
                  and payload->'provenance'->>'candidate_identity' is not null
                """,
                (
                    current - timedelta(days=1),
                    FORECAST_HORIZON_MINUTES,
                    desired_models,
                ),
            )
            existing_by_identity: dict[str, set[str]] = {}
            for identity, model_id in cur.fetchall():
                if not identity or not model_id:
                    continue
                existing_by_identity.setdefault(str(identity), set()).add(str(model_id))

            forecast_candidates: list[dict[str, Any]] = []
            for candidate in point_candidates:
                identity = candidate["candidate_identity"]
                existing = existing_by_identity.get(identity, set())
                missing = [model for model in desired_models if model not in existing]
                if not missing:
                    continue
                forecast_candidates.append({
                    **candidate,
                    "missing_model_ids": missing,
                })

            cur.execute(
                """
                select
                    forecast_id,
                    generated_at,
                    symbol,
                    payload->'provenance'->>'candidate_identity' as candidate_identity,
                    model_id,
                    model_version,
                    payload->'forecast_payload'->>'expected_return' as expected_return
                from nostra.evidence_forecasts
                where generated_at >= %s
                  and horizon_minutes = %s
                  and target_kind = 'return'
                  and payload->'provenance'->>'candidate_identity' is not null
                order by generated_at asc
                limit %s
                """,
                (
                    score_start,
                    FORECAST_HORIZON_MINUTES,
                    max(1, min(limit * 8, 10000)),
                ),
            )
            forecast_rows = []
            for row in cur.fetchall():
                expected_return = _finite_float(row[6])
                if not row[0] or not row[3] or expected_return is None:
                    continue
                forecast_rows.append({
                    "forecast_id": str(row[0]),
                    "generated_at": row[1],
                    "symbol": str(row[2] or ""),
                    "candidate_identity": str(row[3] or ""),
                    "model_id": str(row[4] or ""),
                    "model_version": str(row[5] or ""),
                    "expected_return": expected_return,
                })

            forecast_ids = [row["forecast_id"] for row in forecast_rows]
            scored: set[str] = set()
            if forecast_ids:
                cur.execute(
                    """
                    select distinct forecast_id
                    from nostra.evidence_scores
                    where forecast_id = any(%s)
                    """,
                    (forecast_ids,),
                )
                scored = {str(row[0]) for row in cur.fetchall() if row[0]}

            pending = [row for row in forecast_rows if row["forecast_id"] not in scored]
            identities = sorted({
                row["candidate_identity"]
                for row in pending
                if row["candidate_identity"]
            })
            outcome_by_identity: dict[str, dict[str, Any]] = {}
            if identities:
                cur.execute(
                    """
                    select distinct on (
                        coalesce(
                            nullif(payload->>'candidate_id',''),
                            nullif(payload->>'candidate_key','')
                        )
                    )
                        coalesce(
                            nullif(payload->>'candidate_id',''),
                            nullif(payload->>'candidate_key','')
                        ) as candidate_identity,
                        occurred_at,
                        symbol,
                        payload
                    from rhen.events
                    where event_type = 'candidate_forward_outcome'
                      and payload->>'status' = 'complete'
                      and payload->>'horizon_minutes' = %s
                      and coalesce(
                            nullif(payload->>'candidate_id',''),
                            nullif(payload->>'candidate_key','')
                          ) = any(%s)
                    order by candidate_identity, occurred_at desc, event_id desc
                    """,
                    (str(FORECAST_HORIZON_MINUTES), identities),
                )
                for identity, occurred_at, symbol, payload in cur.fetchall():
                    if not identity or not isinstance(payload, dict):
                        continue
                    outcome_by_identity[str(identity)] = {
                        "occurred_at": occurred_at,
                        "symbol": str(symbol or ""),
                        "payload": payload,
                    }

            score_outcomes: list[dict[str, Any]] = []
            for forecast in pending:
                outcome = outcome_by_identity.get(forecast["candidate_identity"])
                if not outcome:
                    continue
                payload = outcome["payload"]
                observed_at = _as_utc(
                    payload.get("observation_end_at") or outcome["occurred_at"]
                )
                generated_at = _as_utc(forecast["generated_at"])
                realized_return = _finite_float(payload.get("forward_return"))
                if (
                    observed_at is None
                    or generated_at is None
                    or realized_return is None
                    or observed_at <= generated_at
                ):
                    continue
                score_outcomes.append(
                    {
                        "forecast_id": forecast["forecast_id"],
                        "candidate_identity": forecast["candidate_identity"],
                        "symbol": forecast["symbol"] or outcome["symbol"],
                        "model_id": forecast["model_id"],
                        "model_version": forecast["model_version"],
                        "expected_return": forecast["expected_return"],
                        "observed_at": observed_at.isoformat(),
                        "realized_return": realized_return,
                        "max_favorable_return": payload.get("max_favorable_return"),
                        "max_adverse_return": payload.get("max_adverse_return"),
                        "outcome_methodology_version": payload.get("methodology_version"),
                    }
                )

            baseline_evaluation = _baseline_evaluation(
                cur,
                current=current,
                lookback_hours=evaluation_lookback_hours,
                model_id=BASELINE_MODEL_ID,
            )
            drift_evaluation = _baseline_evaluation(
                cur,
                current=current,
                lookback_hours=evaluation_lookback_hours,
                model_id=DRIFT_MODEL_ID,
            )
            model_evaluations = [
                row
                for row in (baseline_evaluation, drift_evaluation)
                if row is not None
            ]

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": current.isoformat(),
        "forecast_horizon_minutes": FORECAST_HORIZON_MINUTES,
        "baseline_model_id": BASELINE_MODEL_ID,
        "research_only": True,
        "execution_authority": False,
        "forecast_candidates": forecast_candidates,
        "score_outcomes": score_outcomes,
        "baseline_evaluation": baseline_evaluation,
        "model_evaluations": model_evaluations,
        "drift_training_state": drift_training_state,
        "counts": {
            "candidate_rows_scanned": len(raw_candidates),
            "forecast_candidates": len(forecast_candidates),
            "pending_score_outcomes": len(score_outcomes),
            "evaluation_models": len(model_evaluations),
            "drift_independent_cycles": int(
                drift_training_state.get("independent_cycles") or 0
            ),
        },
    }
