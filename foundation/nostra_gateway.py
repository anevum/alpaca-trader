from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from foundation.report_read import _candidate_identity, _decision_candidates


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


def read_nostra_work(
    database_url: str,
    *,
    now: datetime | None = None,
    forecast_lookback_minutes: int = 5,
    score_lookback_hours: int = 6,
    limit: int = 500,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    forecast_start = current - timedelta(minutes=max(forecast_lookback_minutes, 1))
    score_start = current - timedelta(hours=max(score_lookback_hours, 1))

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            raw_candidates = _decision_candidates(
                cur,
                start=forecast_start,
                end=current + timedelta(seconds=1),
                crypto=True,
                inherit_event_strategy_for_crypto=False,
                result_limit=max(1, min(limit, 2000)),
            )

            cur.execute(
                """
                select distinct payload->'provenance'->>'candidate_identity'
                from nostra.evidence_forecasts
                where generated_at >= %s
                  and horizon_minutes = %s
                  and target_kind = 'return'
                  and model_id = %s
                  and payload->'provenance'->>'candidate_identity' is not null
                """,
                (current - timedelta(days=1), FORECAST_HORIZON_MINUTES, BASELINE_MODEL_ID),
            )
            already_forecast = {str(row[0]) for row in cur.fetchall() if row[0]}

            forecast_candidates: list[dict[str, Any]] = []
            for raw in raw_candidates:
                candidate = _point_in_time_candidate(raw)
                if candidate is None:
                    continue
                if candidate["candidate_identity"] in already_forecast:
                    continue
                forecast_candidates.append(candidate)

            cur.execute(
                """
                select
                    forecast_id,
                    generated_at,
                    symbol,
                    payload->'provenance'->>'candidate_identity' as candidate_identity
                from nostra.evidence_forecasts
                where generated_at >= %s
                  and horizon_minutes = %s
                  and target_kind = 'return'
                  and model_id = %s
                  and payload->'provenance'->>'candidate_identity' is not null
                order by generated_at asc
                limit %s
                """,
                (score_start, FORECAST_HORIZON_MINUTES, BASELINE_MODEL_ID, max(1, min(limit * 4, 5000))),
            )
            forecast_rows = [
                {
                    "forecast_id": str(row[0]),
                    "generated_at": row[1],
                    "symbol": str(row[2] or ""),
                    "candidate_identity": str(row[3] or ""),
                }
                for row in cur.fetchall()
                if row[0] and row[3]
            ]

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
            identities = sorted({row["candidate_identity"] for row in pending if row["candidate_identity"]})
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
                observed_at = _as_utc(payload.get("observation_end_at") or outcome["occurred_at"])
                generated_at = _as_utc(forecast["generated_at"])
                try:
                    realized_return = float(payload.get("forward_return"))
                except (TypeError, ValueError):
                    continue
                if observed_at is None or generated_at is None or observed_at <= generated_at:
                    continue
                score_outcomes.append(
                    {
                        "forecast_id": forecast["forecast_id"],
                        "candidate_identity": forecast["candidate_identity"],
                        "symbol": forecast["symbol"] or outcome["symbol"],
                        "observed_at": observed_at.isoformat(),
                        "realized_return": realized_return,
                        "max_favorable_return": payload.get("max_favorable_return"),
                        "max_adverse_return": payload.get("max_adverse_return"),
                        "outcome_methodology_version": payload.get("methodology_version"),
                    }
                )

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
        "counts": {
            "candidate_rows_scanned": len(raw_candidates),
            "forecast_candidates": len(forecast_candidates),
            "pending_score_outcomes": len(score_outcomes),
        },
    }
