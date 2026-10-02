from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

NY = ZoneInfo("America/New_York")


def _as_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _session_bounds(session: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(session)
    start = datetime.combine(day, time.min, tzinfo=NY).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=NY).astimezone(
        timezone.utc
    )
    return start, end


def _event_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "event_id": str(row[0]),
        "event_key": row[1],
        "run_id": row[2],
        "strategy_version_id": row[3],
        "event_type": row[4],
        "occurred_at": row[5].isoformat() if row[5] else None,
        "symbol": row[6],
        "correlation_id": row[7],
        "source": row[8],
        "payload": row[9] or {},
        "ingested_at": row[10].isoformat() if row[10] else None,
    }


def _fetch_events(
    cur: psycopg.Cursor[Any],
    *,
    event_types: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10000,
    ascending: bool = True,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if event_types:
        clauses.append("event_type = any(%s)")
        args.append(event_types)
    if start is not None:
        clauses.append("occurred_at >= %s")
        args.append(start)
    if end is not None:
        clauses.append("occurred_at < %s")
        args.append(end)
    where = " where " + " and ".join(clauses) if clauses else ""
    direction = "asc" if ascending else "desc"
    args.append(limit)
    cur.execute(
        f"""
        select
            event_id, event_key, run_id, strategy_version_id, event_type,
            occurred_at, symbol, correlation_id, source, payload, ingested_at
        from rhen.events
        {where}
        order by occurred_at {direction}, event_id {direction}
        limit %s
        """,
        tuple(args),
    )
    return [_event_dict(row) for row in cur.fetchall()]


def _candidate_identity(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("candidate_key") or "")


def _candidate_is_crypto(candidate: dict[str, Any]) -> bool:
    return (
        str(candidate.get("market_lane") or "").lower() == "crypto"
        or str(candidate.get("strategy_version_id") or "").upper().startswith("CRYPTO-")
        or str((candidate.get("features") or {}).get("market") or "").lower() == "crypto"
        or str((candidate.get("research_attribution") or {}).get("market") or "").lower()
        == "crypto"
    )


def _decision_candidates(
    cur: psycopg.Cursor[Any],
    *,
    start: datetime | None,
    end: datetime | None,
    crypto: bool | None,
    inherit_event_strategy_for_crypto: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["event_type = %s"]
    args: list[Any] = ["decision_cycle"]
    if start is not None:
        clauses.append("occurred_at >= %s")
        args.append(start)
    if end is not None:
        clauses.append("occurred_at < %s")
        args.append(end)
    args.append(20000)

    raw_strategy = (
        "case when candidate_row.candidate ? 'strategy_version_id' "
        "then candidate_row.candidate->>'strategy_version_id' "
        "else decision_events.strategy_version_id end"
        if inherit_event_strategy_for_crypto
        else "candidate_row.candidate->>'strategy_version_id'"
    )
    crypto_expression = f"""(
        lower(coalesce(candidate_row.candidate->>'market_lane', '')) = 'crypto'
        or upper(coalesce({raw_strategy}, '')) like 'CRYPTO-%'
        or lower(coalesce(candidate_row.candidate->'features'->>'market', '')) = 'crypto'
        or lower(coalesce(candidate_row.candidate->'research_attribution'->>'market', '')) = 'crypto'
    )"""
    candidate_filter = ""
    if crypto is True:
        candidate_filter = f" and {crypto_expression}"
    elif crypto is False:
        candidate_filter = f" and not {crypto_expression}"

    cur.execute(
        f"""
        with decision_events as (
            select
                event_id, event_key, run_id, strategy_version_id,
                occurred_at, payload
            from rhen.events
            where {" and ".join(clauses)}
            order by occurred_at asc, event_id asc
            limit %s
        )
        select
            decision_events.event_id,
            decision_events.event_key,
            decision_events.run_id,
            decision_events.strategy_version_id,
            decision_events.occurred_at,
            decision_events.payload->>'cycle_key',
            decision_events.payload->'runtime'->>'runtime_instance_id',
            decision_events.payload->'runtime'->>'deployment_id',
            decision_events.payload->>'data_status',
            decision_events.payload->>'data_feed',
            decision_events.payload->>'bar_interval',
            coalesce(decision_events.payload->'comparison_context', '{{}}'::jsonb),
            candidate_row.candidate,
            candidate_row.candidate_ordinal
        from decision_events
        cross join lateral jsonb_array_elements(
            case
                when jsonb_typeof(decision_events.payload->'candidates') = 'array'
                then decision_events.payload->'candidates'
                else '[]'::jsonb
            end
        ) with ordinality as candidate_row(candidate, candidate_ordinal)
        where jsonb_typeof(candidate_row.candidate) = 'object'
        {candidate_filter}
        order by
            decision_events.occurred_at asc,
            decision_events.event_id asc,
            candidate_row.candidate_ordinal asc
        """,
        tuple(args),
    )

    candidates: list[dict[str, Any]] = []
    for row in cur.fetchall():
        raw = row[12]
        if not isinstance(raw, dict):
            continue
        event_key = row[1]
        run_id = row[2]
        strategy_version_id = row[3]
        occurred_at = row[4].isoformat() if row[4] else None
        cycle_key = str(row[5] or event_key)
        index = max(int(row[13] or 1) - 1, 0)
        candidate = dict(raw)
        candidate.setdefault(
            "candidate_key",
            f"{cycle_key}:{candidate.get('symbol') or index}",
        )
        candidate.setdefault("candidate_id", None)
        candidate.setdefault("scan_cycle_id", cycle_key)
        candidate.setdefault("run_id", run_id)
        candidate.setdefault("strategy_version_id", strategy_version_id)
        candidate.setdefault("observed_at", occurred_at)
        observed = _as_dt(candidate.get("observed_at"))
        candidate.setdefault(
            "session",
            observed.astimezone(NY).date().isoformat() if observed else None,
        )
        candidate["scan_cycle"] = {
            "scan_cycle_id": cycle_key,
            "cycle_key": cycle_key,
            "run_id": run_id,
            "strategy_version_id": strategy_version_id,
            "observed_at": occurred_at,
            "runtime_instance_id": row[6],
            "deployment_id": row[7],
            "data_status": row[8],
            "data_feed": row[9],
            "bar_interval": row[10],
        }
        candidate["decision_cycle_payload"] = {
            "cycle_key": cycle_key,
            "comparison_context": row[11] or {},
        }
        candidates.append(candidate)
    return candidates


def _forward_outcomes(
    cur: psycopg.Cursor[Any],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    candidate_identities: set[str] | None = None,
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    clauses = ["event_type = %s"]
    args: list[Any] = ["candidate_forward_outcome"]
    if start is not None:
        clauses.append("occurred_at >= %s")
        args.append(start)
    if end is not None:
        clauses.append("occurred_at < %s")
        args.append(end)
    if candidate_identities is not None:
        identities = sorted(
            {
                str(identity)
                for identity in candidate_identities
                if str(identity)
            }
        )
        if not identities:
            return {}, []
        clauses.append(
            "coalesce(nullif(payload->>'candidate_id',''), "
            "nullif(payload->>'candidate_key','')) = any(%s)"
        )
        args.append(identities)
    args.append(50000)
    cur.execute(
        f"""
        select
            event_id, event_key, run_id, strategy_version_id, event_type,
            occurred_at, symbol, correlation_id, source, payload, ingested_at
        from rhen.events
        where {" and ".join(clauses)}
        order by occurred_at asc, event_id asc
        limit %s
        """,
        tuple(args),
    )
    events = [_event_dict(row) for row in cur.fetchall()]
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    complete_events: list[dict[str, Any]] = []
    for event in events:
        payload = dict(event["payload"] or {})
        identity = str(payload.get("candidate_id") or payload.get("candidate_key") or "")
        horizon = str(payload.get("horizon_minutes") or "")
        if not identity or not horizon:
            continue
        grouped[identity][horizon] = {
            "status": payload.get("status"),
            "computed_at": payload.get("computed_at") or event["occurred_at"],
            "forward_return": payload.get("forward_return"),
            "max_favorable_return": payload.get("max_favorable_return"),
            "max_adverse_return": payload.get("max_adverse_return"),
            "methodology_version": payload.get("methodology_version"),
        }
        if payload.get("status") == "complete":
            complete_events.append(event)
    return dict(grouped), complete_events


def _attach_outcomes(
    candidates: list[dict[str, Any]],
    outcomes: dict[str, dict[str, dict[str, Any]]],
    *,
    equity_shape: bool = False,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        identity = _candidate_identity(row)
        by_horizon = outcomes.get(identity, {})
        if equity_shape:
            row["outcomes"] = [
                {
                    "horizon_minutes": int(horizon)
                    if str(horizon).isdigit()
                    else horizon,
                    **value,
                }
                for horizon, value in sorted(
                    by_horizon.items(),
                    key=lambda item: int(item[0]) if str(item[0]).isdigit() else 999999,
                )
            ]
        else:
            row["forward_outcomes"] = by_horizon
        output.append(row)
    return output


def _latest_report(
    cur: psycopg.Cursor[Any],
    *,
    event_type: str,
    session_key: str,
    requested_session: str | None = None,
) -> dict[str, Any] | None:
    clauses = ["event_type=%s"]
    args: list[Any] = [event_type]
    if requested_session:
        clauses.append("payload->>%s=%s")
        args.extend([session_key, requested_session])
    cur.execute(
        f"""
        select
            event_id, event_key, run_id, strategy_version_id, event_type,
            occurred_at, symbol, correlation_id, source, payload, ingested_at
        from rhen.events
        where {" and ".join(clauses)}
        order by
            coalesce(
                nullif(payload->>'generated_at','')::timestamptz,
                occurred_at
            ) desc,
            ingested_at desc
        limit 1
        """,
        tuple(args),
    )
    row = cur.fetchone()
    return _event_dict(row) if row else None


def _promotion_evidence(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    candidates = {
        _candidate_identity(candidate): candidate
        for candidate in _decision_candidates(
            cur,
            start=None,
            end=None,
            crypto=True,
            inherit_event_strategy_for_crypto=False,
        )
    }

    outcomes, complete = _forward_outcomes(
        cur,
        candidate_identities=set(candidates),
    )
    resolved_ids = {
        str((event["payload"] or {}).get("candidate_id") or (event["payload"] or {}).get("candidate_key") or "")
        for event in complete
    }
    resolved_ids.discard("")
    resolved_candidates = [
        candidates[identity]
        for identity in resolved_ids
        if identity in candidates
    ]

    hours: set[int] = set()
    weekdays: set[int] = set()
    pairs: set[str] = set()
    volatility_regimes: set[str] = set()
    liquidity_regimes: set[str] = set()
    observed: list[datetime] = []

    for candidate in resolved_candidates:
        stamp = _as_dt(candidate.get("observed_at"))
        if stamp:
            observed.append(stamp)
            hours.add(stamp.hour)
            weekdays.add(stamp.weekday())
        symbol = str(candidate.get("symbol") or "").upper()
        if symbol:
            pairs.add(symbol)
        features = candidate.get("features") or {}
        raw = (features.get("feature_state") or {}).get("raw") or {}
        try:
            volatility = float(raw.get("realized_volatility"))
        except (TypeError, ValueError):
            volatility = None
        try:
            spread_bps = float(raw.get("spread_bps"))
        except (TypeError, ValueError):
            spread_bps = None
        if volatility is not None:
            volatility_regimes.add(
                "low" if volatility < 0.00075 else "mid" if volatility < 0.0015 else "high"
            )
        if spread_bps is not None:
            liquidity_regimes.add(
                "tight" if spread_bps <= 10 else "normal" if spread_bps <= 30 else "wide"
            )

    mfes: list[float] = []
    maes: list[float] = []
    for event in complete:
        payload = event["payload"] or {}
        identity = str(payload.get("candidate_id") or payload.get("candidate_key") or "")
        if identity not in candidates:
            continue
        try:
            mfes.append(float(payload["max_favorable_return"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            maes.append(float(payload["max_adverse_return"]))
        except (KeyError, TypeError, ValueError):
            pass

    metrics = {
        "net_expectancy_after_costs": None,
        "brier_score": None,
        "log_loss": None,
        "calibration_intercept": None,
        "calibration_slope": None,
        "discrimination": None,
        "max_drawdown": None,
        "tail_loss": None,
        "mfe": mean(mfes) if mfes else None,
        "mae": mean(maes) if maes else None,
        "slippage": None,
        "spread_sensitivity": None,
        "regime_stability": None,
        "time_of_week_stability": None,
    }
    return {
        "methodology_version": "foundation-event-derived-crypto-promotion-v1",
        "market_lane": "crypto",
        "resolved_candidate_predictions": len(resolved_ids),
        "paper_round_trips": 0,
        "paper_round_trip_source": "crypto_execution_disabled",
        "utc_hours_covered": sorted(hours),
        "weekdays_covered": sorted(weekdays),
        "volatility_regimes": sorted(volatility_regimes),
        "liquidity_regimes": sorted(liquidity_regimes),
        "pairs_covered": sorted(pairs),
        "coverage_first_observed_at": min(observed).isoformat() if observed else None,
        "coverage_last_observed_at": max(observed).isoformat() if observed else None,
        "metrics": metrics,
        "net_expectancy_positive_after_high_costs": False,
        "walk_forward_passed": False,
        "holdout_passed": False,
        "dependence_adjusted": False,
        "multiplicity_adjusted": False,
        "no_lookahead_verified": False,
        "source": "rhen.events",
        "execution_authority": False,
    }


def _weekly_inputs(
    cur: psycopg.Cursor[Any],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start, _ = _session_bounds(start_date)
    _, end = _session_bounds(end_date)
    events = _fetch_events(cur, start=start, end=end, limit=50000)
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_type[event["event_type"]].append(event)

    daily_reports = by_type.get("research_daily_report", [])
    daily_reports.sort(key=lambda row: str((row["payload"] or {}).get("session") or ""))
    sessions = sorted(
        {
            str((row["payload"] or {}).get("session"))
            for row in daily_reports
            if (row["payload"] or {}).get("session")
        }
    )

    strategy_versions = sorted(
        {
            str(event["strategy_version_id"])
            for event in events
            if event.get("strategy_version_id")
        }
    )
    run_ids = sorted(
        {str(event["run_id"]) for event in events if event.get("run_id")}
    )

    def local_session(event: dict[str, Any]) -> str:
        stamp = _as_dt(event.get("occurred_at"))
        return stamp.astimezone(NY).date().isoformat() if stamp else "unknown"

    def counts(event_type: str, name: str) -> list[dict[str, Any]]:
        grouped: dict[str, int] = defaultdict(int)
        for event in by_type.get(event_type, []):
            grouped[local_session(event)] += 1
        return [{"session": session, name: value} for session, value in sorted(grouped.items())]

    candidate_by_session: dict[str, dict[str, int]] = defaultdict(
        lambda: {"evaluated": 0, "qualified": 0, "rejected": 0}
    )
    for event in by_type.get("decision_cycle", []):
        session = local_session(event)
        for candidate in (event["payload"] or {}).get("candidates") or []:
            if not isinstance(candidate, dict) or _candidate_is_crypto(candidate):
                continue
            candidate_by_session[session]["evaluated"] += 1
            if candidate.get("qualified") is True:
                candidate_by_session[session]["qualified"] += 1
            else:
                candidate_by_session[session]["rejected"] += 1

    equity_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    drawdowns: list[float] = []
    for event in by_type.get("account_snapshot", []):
        session = local_session(event)
        equity_by_session[session].append(event)
        try:
            drawdowns.append(float((event["payload"] or {}).get("drawdown_pct")))
        except (TypeError, ValueError):
            pass
    account_equity_by_session: list[dict[str, Any]] = []
    for session, rows in sorted(equity_by_session.items()):
        rows.sort(key=lambda row: row["occurred_at"] or "")
        account_equity_by_session.append(
            {
                "session": session,
                "starting_equity": (rows[0]["payload"] or {}).get("equity"),
                "ending_equity": (rows[-1]["payload"] or {}).get("equity"),
            }
        )

    outcome_status: dict[str, int] = defaultdict(int)
    for event in by_type.get("candidate_forward_outcome", []):
        status = str((event["payload"] or {}).get("status") or "unknown")
        outcome_status[f"{status}_rows"] += 1

    live_offline: dict[tuple[str, str], int] = defaultdict(int)
    for event in by_type.get("live_offline_comparison", []):
        payload = event["payload"] or {}
        live_offline[
            (
                str(payload.get("session") or local_session(event)),
                str(payload.get("match_state") or "UNKNOWN"),
            )
        ] += 1

    runtime_instances = [
        {
            "run_id": event.get("run_id"),
            "strategy_version_id": event.get("strategy_version_id"),
            "started_at": event.get("occurred_at"),
            "metadata": event.get("payload") or {},
        }
        for event in by_type.get("runtime_start", [])[-10:]
    ]

    incidents = [
        {
            "incident_type": "runtime_error",
            "severity": "error",
            "session": local_session(event),
            "message": (event["payload"] or {}).get("message")
            or (event["payload"] or {}).get("error")
            or "runtime_error",
            "resolved_at": None,
        }
        for event in by_type.get("runtime_error", [])
    ]

    return {
        "daily_reports": daily_reports,
        "earliest_daily_session": sessions[0] if sessions else None,
        "data_cutoff": max(
            (event.get("ingested_at") or "" for event in events),
            default=None,
        ),
        "strategy_versions": [{"version_id": value} for value in strategy_versions],
        "runs": [{"run_id": value} for value in run_ids],
        "runtime_instances": runtime_instances,
        "account_equity_by_session": account_equity_by_session,
        "account_weekly_drawdown": {
            "max_drawdown_pct": max(drawdowns) if drawdowns else None
        },
        "orders_by_session": counts("broker_order", "orders"),
        "order_intents_by_session": [
            {
                "session": row["session"],
                "order_intents": row["order_intents"],
                "entry_intents": sum(
                    1
                    for event in by_type.get("order_intent", [])
                    if local_session(event) == row["session"]
                    and str(((event["payload"] or {}).get("intent") or {}).get("side") or "").lower()
                    == "buy"
                ),
            }
            for row in counts("order_intent", "order_intents")
        ],
        "fills_by_session": counts("broker_fill", "fills"),
        "candidate_by_session": [
            {"session": session, **values}
            for session, values in sorted(candidate_by_session.items())
        ],
        "positions": [],
        "incidents": incidents,
        "forward_outcome_status": dict(outcome_status),
        "live_offline_summary": [
            {"session": session, "match_state": state, "count": count}
            for (session, state), count in sorted(live_offline.items())
        ],
        "duplicate_checks": {},
        "warnings": [
            "Foundation v2 weekly inputs are derived directly from append-only RHEN events; legacy Supabase-only analytical projections are intentionally not recreated."
        ],
    }


def _graen_shadow(
    cur: psycopg.Cursor[Any],
    *,
    candidate_id: str | None,
) -> dict[str, Any]:
    event_types = [
        "graen_candidate_shadow_activation",
        "graen_candidate_shadow_state",
        "graen_candidate_shadow_opportunity",
        "graen_candidate_shadow_entry",
        "graen_candidate_shadow_exit",
        "graen_candidate_shadow_checkpoint",
    ]
    events = _fetch_events(
        cur,
        event_types=event_types,
        limit=500,
        ascending=False,
    )
    if candidate_id:
        events = [
            event
            for event in events
            if str((event["payload"] or {}).get("candidate_id") or "") == candidate_id
        ]
    activation = next(
        (
            event
            for event in events
            if event["event_type"] == "graen_candidate_shadow_activation"
        ),
        None,
    )
    activation_id = str((activation or {}).get("payload", {}).get("activation_id") or "")
    if activation_id:
        events = [
            event
            for event in events
            if event is activation
            or str((event["payload"] or {}).get("activation_id") or "") == activation_id
        ]
    return {
        "ok": True,
        "shadow_methodology_version": "graen-forward-shadow-v1",
        "activation": activation,
        "state": next(
            (event for event in events if event["event_type"] == "graen_candidate_shadow_state"),
            None,
        ),
        "checkpoint": next(
            (event for event in events if event["event_type"] == "graen_candidate_shadow_checkpoint"),
            None,
        ),
        "recent_events": [
            event
            for event in events
            if event["event_type"]
            in {
                "graen_candidate_shadow_opportunity",
                "graen_candidate_shadow_entry",
                "graen_candidate_shadow_exit",
                "graen_candidate_shadow_checkpoint",
            }
        ][:200],
    }


def read_report(database_url: str, params: dict[str, str]) -> dict[str, Any]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            latest = params.get("latest")
            session = params.get("session")
            week_end = params.get("week_end")
            evidence_session = params.get("evidence_session")
            crypto_evidence_session = params.get("crypto_evidence_session")
            crypto_promotion = params.get("crypto_promotion")
            start_date = params.get("start")
            end_date = params.get("end")

            if latest == "graen_shadow":
                return _graen_shadow(
                    cur,
                    candidate_id=params.get("shadow_candidate_id"),
                )

            if _valid_date(start_date) and _valid_date(end_date):
                if str(start_date) > str(end_date):
                    return {"ok": False, "error": "invalid_period"}
                return {
                    "ok": True,
                    "report_version": "rhen-weekly-v1.2",
                    "inputs": _weekly_inputs(
                        cur,
                        start_date=str(start_date),
                        end_date=str(end_date),
                    ),
                }

            if crypto_promotion in {"1", "true", "True"}:
                return {"ok": True, "evidence": _promotion_evidence(cur)}

            if _valid_date(crypto_evidence_session):
                start, end = _session_bounds(str(crypto_evidence_session))
                candidates = _decision_candidates(
                    cur, start=start, end=end, crypto=True
                )
                outcomes, _ = _forward_outcomes(
                    cur,
                    candidate_identities={
                        identity
                        for candidate in candidates
                        if (identity := _candidate_identity(candidate))
                    },
                )
                candidates = _attach_outcomes(candidates, outcomes)
                candidates = [
                    row
                    for row in candidates
                    if sum(
                        1
                        for outcome in (row.get("forward_outcomes") or {}).values()
                        if outcome.get("status") == "complete"
                    )
                    < 7
                ][:5000]
                return {
                    "ok": True,
                    "evidence_version": "rhen-crypto-forward-evidence-v2",
                    "evidence_session": crypto_evidence_session,
                    "candidates": candidates,
                }

            if _valid_date(evidence_session):
                start, end = _session_bounds(str(evidence_session))
                candidates = _decision_candidates(
                    cur, start=start, end=end, crypto=False
                )
                outcomes, _ = _forward_outcomes(
                    cur,
                    candidate_identities={
                        identity
                        for candidate in candidates
                        if (identity := _candidate_identity(candidate))
                    },
                )
                candidates = _attach_outcomes(
                    candidates, outcomes, equity_shape=True
                )
                latest_daily = _latest_report(
                    cur,
                    event_type="research_daily_report",
                    session_key="session",
                    requested_session=str(evidence_session),
                )
                return {
                    "ok": True,
                    "evidence_session": evidence_session,
                    "candidates": candidates[:5000],
                    "post_event": {
                        "source": "rhen.events",
                        "analytics_only": True,
                    },
                    "ads002": {},
                    "ads002_v2": {},
                    "latest_daily_report": latest_daily,
                }

            if latest == "daily":
                if session is not None and not _valid_date(session):
                    return {"ok": False, "error": "invalid_session"}
                row = _latest_report(
                    cur,
                    event_type="research_daily_report",
                    session_key="session",
                    requested_session=session,
                )
                payload = row["payload"] if row else None
                return {
                    "ok": True,
                    "report_version": (payload or {}).get("report_version"),
                    "report": payload,
                }

            if latest == "weekly":
                if week_end is not None and not _valid_date(week_end):
                    return {"ok": False, "error": "invalid_week_end"}
                row = _latest_report(
                    cur,
                    event_type="research_weekly_report",
                    session_key="week_end",
                    requested_session=week_end,
                )
                return {
                    "ok": True,
                    "report_version": "rhen-weekly-v1.2",
                    "report": row["payload"] if row else None,
                }

            if latest == "command":
                daily = _latest_report(
                    cur,
                    event_type="research_daily_report",
                    session_key="session",
                )
                weekly = _latest_report(
                    cur,
                    event_type="research_weekly_report",
                    session_key="week_end",
                )
                recent = _fetch_events(cur, limit=2000, ascending=False)
                latest_runtime = next(
                    (
                        event
                        for event in recent
                        if event["event_type"] == "runtime_start"
                    ),
                    None,
                )
                latest_scan = next(
                    (
                        event
                        for event in recent
                        if event["event_type"] == "decision_cycle"
                    ),
                    None,
                )
                return {
                    "ok": True,
                    "evidence_version": "rhen-command-evidence-v2",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "latest_daily": daily["payload"] if daily else None,
                    "latest_weekly": weekly["payload"] if weekly else None,
                    "research_questions": [],
                    "weekly_decisions": [],
                    "research_decisions": [],
                    "post_event_evidence": {
                        "analytics_only": True,
                        "source": "rhen.events",
                    },
                    "provenance": {
                        "runtime": latest_runtime,
                        "latest_scan_cycle": latest_scan,
                    },
                    "telemetry_health": {
                        "events_observed": len(recent),
                        "latest_event_at": recent[0]["occurred_at"] if recent else None,
                        "canonical_store": "railway_postgresql",
                    },
                }

            return {"ok": False, "error": "invalid_request"}
