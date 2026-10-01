from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg


UTC = timezone.utc


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat()
    return str(value)


def _obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _strategy_name(row: dict[str, Any]) -> str:
    config = _obj(row.get("configuration"))
    return str(
        config.get("strategy_name")
        or config.get("strategy")
        or row.get("strategy_version_id")
        or "unknown"
    )


def _public_event(event_type: str, occurred_at: Any) -> dict[str, Any]:
    mapping = {
        "scan": ("observe", "Scanner cycle evaluated the market universe."),
        "decision_cycle": ("decide", "Decision engine evaluated the market universe."),
        "allocation": ("decide", "Allocation engine evaluated available capacity."),
        "signal": ("decide", "Decision engine evaluated a qualified setup."),
        "order_intent": ("risk", "Risk gate evaluated an execution intent."),
        "execution": ("execute", "Execution subsystem recorded market activity."),
        "broker_order": ("execute", "Broker interface recorded an order lifecycle event."),
        "broker_fill": ("execute", "Broker interface recorded a fill event."),
        "exit": ("execute", "Position lifecycle recorded an exit event."),
        "reconciliation": ("learn", "Broker state and internal ledger were reconciled."),
        "runtime_start": ("system", "Runtime started."),
        "runtime_stop": ("system", "Runtime stopped."),
        "runtime_error": ("warning", "Runtime reported an operational exception."),
    }
    kind, label = mapping.get(
        str(event_type),
        ("system", "System telemetry event recorded."),
    )
    return {
        "at": _iso(occurred_at),
        "type": str(event_type),
        "kind": kind,
        "label": label,
    }


def _research_entry(event_type: str, occurred_at: Any, payload: Any) -> dict[str, Any]:
    body = _obj(payload)
    classification = _obj(body.get("classification"))
    is_daily = event_type == "research_daily_report"
    session = body.get("session")
    week_end = body.get("week_end") or body.get("period_end")
    title = body.get("title")
    if not title:
        title = (
            f"Daily review · {session or 'latest session'}"
            if is_daily
            else f"Weekly review · {week_end or 'latest week'}"
        )
    return {
        "at": _iso(occurred_at),
        "type": event_type,
        "title": str(title),
        "summary": (
            classification.get("reason")
            or body.get("summary")
            or body.get("conclusion")
        ),
        "classification": (
            classification.get("classification")
            or body.get("status")
            or body.get("classification")
        ),
        "focus": body.get("focus") or body.get("next_action"),
        "next_action": body.get("next_offline_research_action") or body.get("next_action"),
        "session": session,
        "week_start": body.get("week_start") or body.get("period_start"),
        "week_end": week_end,
        "warnings": [
            str(value)
            for value in (body.get("data_quality_warnings") or [])
            if isinstance(value, str)
        ][:4],
    }


def _account_performance(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    cur.execute(
        """
        select occurred_at,
               case
                 when payload->>'equity' ~ '^-?[0-9]+([.][0-9]+)?$'
                 then (payload->>'equity')::numeric
                 else null
               end as equity
        from rhen.events
        where event_type='account_snapshot'
          and run_id not like 'foundation-%'
        order by occurred_at asc,event_id asc
        """
    )
    rows = [(row[0], _num(row[1])) for row in cur.fetchall() if _num(row[1]) is not None]
    if not rows:
        return {
            "methodology_version": "PUBLIC-PERFORMANCE-RAILWAY-v1",
            "basis": "canonical_account_snapshot_events",
            "status": "AWAITING_CANONICAL_SAMPLE",
            "sample_state": "AWAITING_LIVE_SAMPLE",
            "tracking_started_at": None,
            "last_observed_at": None,
            "snapshot_count": 0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "account_return_pct": None,
            "realized_return_pct": None,
            "max_drawdown_pct": None,
            "curve": [],
            "limitations": [
                "No canonical account-snapshot sample is available in Railway PostgreSQL yet.",
                "Dollar account values remain private.",
            ],
        }

    baseline = rows[0][1]
    peak = rows[0][1]
    max_drawdown = 0.0
    curve: list[dict[str, Any]] = []
    for stamp, equity in rows:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
        return_pct = ((equity / baseline) - 1.0) * 100.0 if baseline and baseline > 0 else None
        curve.append({"at": _iso(stamp), "return_pct": return_pct})

    if len(curve) > 72:
        step = max(1, len(curve) // 72)
        sampled = curve[::step]
        if sampled[-1] != curve[-1]:
            sampled.append(curve[-1])
        curve = sampled

    latest = rows[-1][1]
    return {
        "methodology_version": "PUBLIC-PERFORMANCE-RAILWAY-v1",
        "basis": "canonical_account_snapshot_events",
        "status": "TRACKING",
        "sample_state": "EARLY_SAMPLE",
        "tracking_started_at": _iso(rows[0][0]),
        "last_observed_at": _iso(rows[-1][0]),
        "snapshot_count": len(rows),
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": None,
        "account_return_pct": ((latest / baseline) - 1.0) * 100.0 if baseline and baseline > 0 else None,
        "realized_return_pct": None,
        "max_drawdown_pct": max_drawdown,
        "curve": curve,
        "limitations": [
            "Normalized account return and drawdown are derived only from canonical account-snapshot events.",
            "Closed-trade and market-lane performance projections are not yet migrated to Foundation v2, so those values are not inferred.",
            "Dollar account values, symbols, prices, quantities, orders, fills, and strategy thresholds remain private.",
        ],
    }


def read_public_feed(database_url: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select run_id,strategy_version_id,asset_class,mode,started_at,ended_at,
                       source_commit,configuration,status
                from rhen.strategy_runs
                where strategy_version_id not like 'FOUNDATION-%'
                  and run_id not like 'foundation-%'
                order by started_at desc,created_at desc
                limit 12
                """
            )
            strategy_cols = [c.name for c in cur.description]
            strategies = [dict(zip(strategy_cols, row)) for row in cur.fetchall()]
            active = strategies[0] if strategies else None

            cur.execute(
                """
                select
                    max(occurred_at) filter (
                        where run_id is null or run_id not like 'foundation-%'
                    ) as latest_event_at,
                    count(*) filter (
                        where occurred_at >= now() - interval '60 minutes'
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as events_60m,
                    count(*) filter (
                        where occurred_at >= now() - interval '10 minutes'
                          and event_type in ('scan','decision_cycle')
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as scan_events_10m,
                    count(distinct symbol) filter (
                        where occurred_at >= now() - interval '10 minutes'
                          and symbol is not null
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as symbols_10m,
                    count(*) filter (
                        where occurred_at >= now() - interval '2 hours'
                          and event_type in ('execution','broker_order','broker_fill')
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as execution_events_2h,
                    count(*) filter (
                        where occurred_at >= now() - interval '2 hours'
                          and event_type='reconciliation'
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as reconciliations_2h,
                    count(*) filter (
                        where occurred_at >= now() - interval '2 hours'
                          and event_type='runtime_error'
                          and (run_id is null or run_id not like 'foundation-%')
                    )::int as errors_2h
                from rhen.events
                """
            )
            summary_row = cur.fetchone()
            summary = dict(zip([c.name for c in cur.description], summary_row))

            cur.execute(
                """
                select date_bin(
                           interval '10 minutes',
                           occurred_at,
                           timestamptz '2001-01-01'
                       ) as bucket,
                       count(*)::int
                from rhen.events
                where occurred_at >= now() - interval '60 minutes'
                  and (run_id is null or run_id not like 'foundation-%')
                group by 1
                order by 1
                """
            )
            activity = [
                {"at": _iso(row[0]), "count": int(row[1])}
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                select event_type,occurred_at
                from rhen.events
                where occurred_at >= now() - interval '2 hours'
                  and event_type in (
                    'scan','decision_cycle','allocation','signal','order_intent',
                    'execution','broker_order','broker_fill','exit',
                    'reconciliation','runtime_error','runtime_start','runtime_stop'
                  )
                  and (run_id is null or run_id not like 'foundation-%')
                order by occurred_at desc,event_id desc
                limit 18
                """
            )
            events = [_public_event(row[0], row[1]) for row in cur.fetchall()]

            cur.execute(
                """
                select occurred_at,payload
                from rhen.events
                where event_type='decision_cycle'
                  and (run_id is null or run_id not like 'foundation-%')
                order by occurred_at desc,event_id desc
                limit 1
                """
            )
            scan_row = cur.fetchone()
            latest_scan = None
            if scan_row:
                body = _obj(scan_row[1])
                latest_scan = {
                    "observed_at": _iso(scan_row[0]),
                    "market_session": body.get("market_session"),
                    "cycle_outcome": body.get("cycle_outcome") or body.get("status"),
                    "data_status": body.get("data_status"),
                    "degraded": bool(body.get("degraded", False)),
                }

            reports: dict[str, dict[str, Any] | None] = {}
            for key, event_type in (
                ("daily", "research_daily_report"),
                ("weekly", "research_weekly_report"),
            ):
                cur.execute(
                    """
                    select occurred_at,payload
                    from rhen.events
                    where event_type=%s
                    order by coalesce(
                        nullif(payload->>'generated_at','')::timestamptz,
                        occurred_at
                    ) desc,ingested_at desc
                    limit 1
                    """,
                    (event_type,),
                )
                row = cur.fetchone()
                reports[key] = (
                    _research_entry(event_type, row[0], row[1])
                    if row
                    else None
                )

            cur.execute(
                """
                select decision_key,status,decided_at,payload
                from rhen.research_decisions
                where lower(status) in ('final','superseded')
                order by decided_at desc
                limit 12
                """
            )
            decisions = []
            for decision_key, status, decided_at, payload in cur.fetchall():
                body = _obj(payload)
                evidence = _obj(body.get("evidence"))
                decisions.append({
                    "at": _iso(decided_at),
                    "decision_key": decision_key,
                    "status": status,
                    "decision_type": body.get("decision_type"),
                    "subject": body.get("subject"),
                    "conclusion": body.get("conclusion"),
                    "methodology_version": body.get("methodology_version"),
                    "families": list(evidence.get("families") or []),
                    "observation_interval": evidence.get("observation_interval"),
                    "primary_forward_horizon_minutes": evidence.get(
                        "primary_forward_horizon_minutes"
                    ),
                    "implemented": evidence.get("implemented") is True,
                    "executed": evidence.get("executed") is True,
                })
            next_direction = next(
                (
                    row
                    for row in decisions
                    if row.get("decision_type") == "next_research_direction"
                ),
                None,
            )

            cur.execute(
                """
                select distinct on (research_question_id)
                    research_question_id,status,question,why_it_matters,
                    sample_size,created_on,created_at
                from rhen.research_questions
                order by research_question_id,created_at desc
                """
            )
            active_questions = [
                {
                    "research_question_id": row[0],
                    "status": row[1],
                    "question": row[2],
                    "why_it_matters": row[3],
                    "sample_size": int(row[4] or 0),
                    "created_on": _iso(row[5]),
                    "created_at": _iso(row[6]),
                }
                for row in cur.fetchall()
                if str(row[1]).upper() in {"OPEN","MONITOR","ACTIVE","READY_FOR_RESEARCH"}
            ][:8]

            cur.execute(
                """
                select
                    case
                      when payload->>'horizon_minutes' ~ '^[0-9]+$'
                      then (payload->>'horizon_minutes')::int
                      else null
                    end as horizon_minutes,
                    coalesce(payload->>'status','unknown') as status,
                    count(*)::int
                from rhen.events
                where event_type='candidate_forward_outcome'
                group by 1,2
                order by 1,2
                """
            )
            outcome_counts = [
                {"horizon_minutes": row[0], "status": row[1], "count": int(row[2])}
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                select coalesce(payload->>'match_state','UNKNOWN'),count(*)::int
                from rhen.events
                where event_type='live_offline_comparison'
                group by 1
                order by 1
                """
            )
            comparison_counts = [
                {"match_state": row[0], "count": int(row[1])}
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                select system_key,health,state,observed_at
                from iren.system_state
                order by updated_at desc
                """
            )
            iren_states = {
                str(row[0]).upper(): {
                    "health": row[1],
                    "state": _obj(row[2]),
                    "observed_at": row[3],
                }
                for row in cur.fetchall()
            }

            cur.execute(
                """
                select heartbeat_at,queue_depth,last_error,active_problem_id
                from graen.runtime_state
                where singleton=true
                """
            )
            graen_row = cur.fetchone()

            cur.execute(
                """
                select max(issued_at),
                       count(*) filter (
                         where issued_at >= now() - interval '24 hours'
                       )::int
                from nostra.forecasts
                """
            )
            nostra_row = cur.fetchone()

            cur.execute(
                """
                select status,completed_at,started_at
                from velum.replays
                order by started_at desc
                limit 1
                """
            )
            velum_row = cur.fetchone()

            performance = _account_performance(cur)

    latest_event_at = summary.get("latest_event_at")
    freshness = (
        max(0.0, (now - latest_event_at.astimezone(UTC)).total_seconds())
        if isinstance(latest_event_at, datetime)
        else None
    )
    live = freshness is not None and freshness < 120.0

    iren = iren_states.get("IREN") or {}
    graen_heartbeat = graen_row[0] if graen_row else None
    graen_age = (
        (now - graen_heartbeat.astimezone(UTC)).total_seconds()
        if isinstance(graen_heartbeat, datetime)
        else None
    )
    graen_running = graen_age is not None and graen_age < 180
    nostra_at = nostra_row[0] if nostra_row else None
    nostra_count = int(nostra_row[1] or 0) if nostra_row else 0
    velum_status = str(velum_row[0]).upper() if velum_row else "IDLE"
    velum_at = (
        velum_row[1] or velum_row[2]
        if velum_row
        else None
    )

    active_strategy = None
    if active:
        active_strategy = {
            "version_id": active.get("strategy_version_id"),
            "strategy_name": _strategy_name(active),
            "environment": active.get("mode"),
            "status": active.get("status"),
            "activated_at": _iso(active.get("started_at")),
        }

    strategy_history = [
        {
            "version_id": row.get("strategy_version_id"),
            "strategy_name": _strategy_name(row),
            "environment": row.get("mode"),
            "status": row.get("status"),
            "activated_at": _iso(row.get("started_at")),
            "retired_at": _iso(row.get("ended_at")),
        }
        for row in strategies
    ]

    journal = [
        {
            "at": row.get("at"),
            "type": row.get("decision_type"),
            "title": row.get("subject") or "Research decision",
            "summary": row.get("conclusion"),
            "classification": row.get("status"),
            "focus": row.get("subject"),
            "next_action": None,
            "session": None,
            "week_start": None,
            "week_end": None,
            "warnings": [],
        }
        for row in decisions[:6]
    ]
    for report in (reports.get("weekly"), reports.get("daily")):
        if report:
            journal.append(report)
    journal.sort(key=lambda row: str(row.get("at") or ""), reverse=True)

    lane_stub = lambda lane: {
        "market_lane": lane,
        "methodology_version": "PUBLIC-MARKET-PERFORMANCE-RAILWAY-v1",
        "basis": "canonical_projection_pending",
        "status": "AWAITING_CANONICAL_PROJECTION",
        "sample_state": "UNAVAILABLE",
        "tracking_started_at": None,
        "tracking_ended_at": None,
        "first_trade_at": None,
        "last_trade_at": None,
        "active_periods": 0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": None,
        "realized_return_pct": None,
        "max_drawdown_pct": None,
        "strategy_version_id": (
            active_strategy.get("version_id") if active_strategy else None
        ),
        "strategy_name": (
            active_strategy.get("strategy_name") if active_strategy else None
        ),
        "strategy_environment": (
            active_strategy.get("environment") if active_strategy else None
        ),
        "strategy_status": (
            active_strategy.get("status") if active_strategy else None
        ),
        "denominator": "canonical_performance_epoch_projection_pending",
        "curve": [],
        "limitations": [
            "Market-lane performance is intentionally unavailable until its canonical Railway projection is migrated and verified.",
            "Replay, simulation, paper, shadow, and development results are excluded from live performance.",
        ],
    }

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "source": "railway_postgresql_canonical",
        "live": live,
        "freshness_seconds": freshness,
        "state": "RUNNING" if live else ("STALE" if latest_event_at else "OFFLINE"),
        "systems": {
            "IREN": {
                "runtime_state": str(iren.get("health") or "UNKNOWN").upper(),
                "health_state": str(iren.get("health") or "UNKNOWN").upper(),
                "tracking_state": "CANONICAL_CONTROL_STATE",
                "observed_at": _iso(iren.get("observed_at")),
                "independent_runtime": True,
                "activity": "Deterministic supervision",
            },
            "RHEN": {
                "runtime_state": "RUNNING" if live else ("STALE" if latest_event_at else "OFFLINE"),
                "health_state": "HEALTHY" if live else "STALE_TELEMETRY",
                "tracking_state": "LIVE_TELEMETRY" if live else "STALE_TELEMETRY",
                "observed_at": _iso(latest_event_at),
                "independent_runtime": True,
                "activity": f"{int(summary.get('events_60m') or 0)} public events / 60m",
            },
            "GRAEN": {
                "runtime_state": "RUNNING" if graen_running else "STALE",
                "health_state": (
                    "DEGRADED"
                    if graen_row and graen_row[2]
                    else ("HEALTHY" if graen_running else "STALE")
                ),
                "tracking_state": "CANONICAL_RUNTIME",
                "observed_at": _iso(graen_heartbeat),
                "independent_runtime": True,
                "activity": (
                    f"{int(graen_row[1] or 0)} queued research problem(s)"
                    if graen_row and int(graen_row[1] or 0) > 0
                    else "Worker active · queue clear"
                ),
            },
            "NOSTRA": {
                "runtime_state": "EMBEDDED" if nostra_count == 0 else "COLLECTING",
                "health_state": "RESEARCH_ONLY",
                "tracking_state": "COLLECTING" if nostra_count else "AWAITING_INDEPENDENT_RUNTIME",
                "observed_at": _iso(nostra_at),
                "independent_runtime": False,
                "activity": (
                    f"{nostra_count} forecasts / 24h"
                    if nostra_count
                    else "Independent forecast runtime not yet activated"
                ),
            },
            "VELUM": {
                "runtime_state": velum_status,
                "health_state": "HEALTHY" if velum_status in {"SUCCEEDED","IDLE"} else velum_status,
                "tracking_state": "READY",
                "observed_at": _iso(velum_at),
                "independent_runtime": True,
                "activity": "Replay runtime",
            },
        },
        "active_strategy": active_strategy,
        "strategy_history": strategy_history,
        "telemetry": {
            "events_60m": int(summary.get("events_60m") or 0),
            "scan_events_10m": int(summary.get("scan_events_10m") or 0),
            "symbols_10m": int(summary.get("symbols_10m") or 0),
            "execution_events_2h": int(summary.get("execution_events_2h") or 0),
            "reconciliations_2h": int(summary.get("reconciliations_2h") or 0),
            "errors_2h": int(summary.get("errors_2h") or 0),
        },
        "activity": activity,
        "events": events,
        "operational": {"latest_scan": latest_scan},
        "research": {
            "current_focus": (
                next_direction.get("subject") if next_direction else None
            ),
            "current_status": (
                next_direction.get("status") if next_direction else None
            ),
            "last_updated_at": (
                decisions[0].get("at")
                if decisions
                else (
                    (reports.get("weekly") or {}).get("at")
                    or (reports.get("daily") or {}).get("at")
                )
            ),
            "next_direction": next_direction,
            "completed_decisions": [
                row
                for row in decisions
                if row.get("decision_type") != "next_research_direction"
            ],
            "active_questions": active_questions,
            "latest_daily": reports.get("daily"),
            "latest_weekly": reports.get("weekly"),
            "latest_weekly_summary": None,
            "evidence": {
                "candidate_forward_outcomes": outcome_counts,
                "live_offline_comparison": comparison_counts,
                "analytics_only": True,
            },
            "limitations": [
                "Foundation v2 exposes only research evidence already present in canonical Railway PostgreSQL.",
                "Legacy Supabase-only analytical projections are not fabricated during migration.",
            ],
            "journal": journal,
        },
        "market_performance": {
            "methodology_version": "PUBLIC-MARKET-PERFORMANCE-RAILWAY-v1",
            "equities": lane_stub("us_equity"),
            "crypto": lane_stub("crypto"),
            "limitations": [
                "Equities and crypto remain separate market lanes.",
                "Canonical market-lane performance projections are being migrated independently from the event store.",
            ],
        },
        "performance": performance,
        "crypto_shadow_validation": None,
        "disclosure": {
            "level": "sanitized",
            "public_fields": [
                "runtime state",
                "active strategy identity",
                "telemetry freshness",
                "aggregate activity",
                "anonymized event classes",
                "sanitized research evidence",
                "normalized account-snapshot percentages",
            ],
            "excluded_fields": [
                "account value",
                "cash",
                "buying power",
                "deposits and withdrawals",
                "symbols",
                "prices",
                "quantities",
                "orders and fills",
                "individual trade records",
                "strategy thresholds and risk parameters",
            ],
        },
    }
