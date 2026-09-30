import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 1,
  idle_timeout: 5,
  connect_timeout: 5,
  connection: { statement_timeout: 10000 },
});

const corsHeaders = {
  "access-control-allow-origin": "https://anevum.com",
  "access-control-allow-methods": "GET, OPTIONS",
  "access-control-allow-headers": "content-type",
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=4, s-maxage=4, stale-while-revalidate=8",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
    },
  });
}

function numberOrZero(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function safeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => item !== null && item !== undefined)
    .map((item) => {
      if (item instanceof Date) return item.toISOString().slice(0, 10);
      const raw = String(item);
      const parsed = new Date(raw);
      if (!Number.isNaN(parsed.getTime()) && /^\d{4}-\d{2}-\d{2}/.test(raw)) {
        return parsed.toISOString().slice(0, 10);
      }
      return raw;
    });
}

function publicResearchEvent(row: Record<string, unknown>) {
  const payload = objectValue(row.payload);
  const classification = objectValue(payload.classification);
  const type = String(row.event_type || "research_checkpoint");
  const session = optionalString(payload.session);
  const weekStart = optionalString(payload.week_start);
  const weekEnd = optionalString(payload.week_end);
  const classificationName =
    optionalString(classification.classification) ||
    optionalString(payload.status) ||
    optionalString(payload.classification);
  const reason =
    optionalString(classification.reason) ||
    optionalString(payload.summary) ||
    optionalString(payload.conclusion);
  const nextAction =
    optionalString(payload.next_offline_research_action) ||
    optionalString(payload.next_action) ||
    optionalString(payload.focus);

  const fallbackTitle =
    type === "research_daily_report" ? `Daily review · ${session || "latest session"}` :
    type === "research_weekly_report" ? `Weekly review · ${weekEnd || "latest week"}` :
    "Research checkpoint";

  const warnings = Array.isArray(payload.data_quality_warnings)
    ? payload.data_quality_warnings
        .filter((item) => typeof item === "string")
        .filter((item) =>
          /candidate horizons are incomplete|unreconstructable|missing canonical daily reports|historical candidate/i.test(String(item))
        )
        .slice(0, 4)
    : [];

  const ads002 = objectValue(payload.ads002);
  const adsReadiness = objectValue(ads002.readiness);
  const adsConfidence = objectValue(ads002.confidence);
  const adsAttribution = objectValue(ads002.attribution);
  const publicAds002 = Object.keys(ads002).length
    ? {
        methodology_version: optionalString(ads002.methodology_version),
        readiness_state: optionalString(adsReadiness.state),
        data_valid: adsReadiness.data_valid === true,
        stable: adsReadiness.stable === true,
        confidence_score: Number.isFinite(Number(adsConfidence.score))
          ? Number(adsConfidence.score)
          : null,
        executable_signals: Number.isFinite(Number(adsAttribution.executable_signals))
          ? Number(adsAttribution.executable_signals)
          : null,
        direct_signals: Number.isFinite(Number(adsAttribution.direct_signals))
          ? Number(adsAttribution.direct_signals)
          : null,
        unlinked_signals: Number.isFinite(Number(adsAttribution.unlinked_signals))
          ? Number(adsAttribution.unlinked_signals)
          : null,
        direct_coverage: Number.isFinite(Number(adsAttribution.direct_coverage))
          ? Number(adsAttribution.direct_coverage)
          : null,
        reason_codes: safeStringArray(adsReadiness.reason_codes),
        promotion_authorized: ads002.promotion_authorized === true,
        live_configuration_changed: ads002.live_configuration_changed === true,
      }
    : null;

  return {
    at: row.occurred_at ? String(row.occurred_at) : null,
    type,
    title: optionalString(payload.title) || fallbackTitle,
    summary: reason,
    classification: classificationName,
    focus: optionalString(payload.focus) || nextAction,
    next_action: nextAction,
    session,
    week_start: weekStart,
    week_end: weekEnd,
    warnings,
    ads002: publicAds002,
  };
}

function publicDecision(row: Record<string, unknown>) {
  const evidence = objectValue(row.evidence);
  return {
    at: row.decided_at ? String(row.decided_at) : null,
    decision_key: optionalString(row.decision_key),
    status: optionalString(row.status),
    decision_type: optionalString(row.decision_type),
    subject: optionalString(row.subject),
    conclusion: optionalString(row.conclusion),
    methodology_version: optionalString(row.methodology_version),
    families: safeStringArray(evidence.families),
    observation_interval: optionalString(evidence.observation_interval),
    primary_forward_horizon_minutes:
      Number.isFinite(Number(evidence.primary_forward_horizon_minutes))
        ? Number(evidence.primary_forward_horizon_minutes)
        : null,
    implemented: evidence.implemented === true,
    executed: evidence.executed === true,
  };
}

function publicEvent(eventType: string | null, occurredAt: string | null) {
  const type = String(eventType || "system");
  const map: Record<string, { kind: string; label: string }> = {
    scan: { kind: "observe", label: "Scanner cycle evaluated the market universe." },
    allocation: { kind: "decide", label: "Allocation engine evaluated available capacity." },
    signal: { kind: "decide", label: "Decision engine evaluated a qualified setup." },
    order_intent: { kind: "risk", label: "Risk gate evaluated an execution intent." },
    execution: { kind: "execute", label: "Execution subsystem recorded market activity." },
    broker_order: { kind: "execute", label: "Broker interface recorded an order lifecycle event." },
    broker_fill: { kind: "execute", label: "Broker interface recorded a completed fill event." },
    exit: { kind: "execute", label: "Position lifecycle recorded an exit event." },
    reconciliation: { kind: "learn", label: "Broker state and internal ledger were reconciled." },
    runtime_start: { kind: "system", label: "Runtime started." },
    runtime_stop: { kind: "system", label: "Runtime stopped." },
    runtime_error: { kind: "warning", label: "Runtime reported an operational exception." },
  };
  const safe = map[type] || { kind: "system", label: "System telemetry event recorded." };
  return { at: occurredAt, type, kind: safe.kind, label: safe.label };
}

function dateString(value: unknown): string | null {
  if (value instanceof Date) return value.toISOString();
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function latestTimestamp(...values: unknown[]): string | null {
  const clean = values
    .map((value) => dateString(value))
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => Date.parse(b) - Date.parse(a));
  return clean[0] || null;
}

function serviceRows(controlState: Record<string, unknown>) {
  const topology = objectValue(controlState.topology);
  if (!Array.isArray(topology.services)) return [] as Record<string, unknown>[];
  return topology.services
    .map((value) => objectValue(value))
    .filter((value) => Object.keys(value).length > 0);
}

function normalizedState(value: unknown, fallback = "UNKNOWN") {
  return optionalString(value)?.toUpperCase() || fallback;
}

function publicSystemStates(
  row: Record<string, unknown>,
  live: boolean,
  latestEventAt: string | null,
  events60m: number,
) {
  const controlState = objectValue(row.iren_control_state);
  const services = serviceRows(controlState);
  const service = (id: string) =>
    services.find((value) => String(value.service_id || "").toUpperCase() === id) || {};

  const iren = service("IREN");
  const rhen = service("RHEN");
  const graen = service("GRAEN");
  const nostra = service("NOSTRA");
  const velum = service("VELUM");
  const graenRuntime = objectValue(row.graen_runtime);

  const controlObservedAt =
    dateString(controlState.observed_at) ||
    dateString(row.iren_updated_at);
  const graenObservedAt =
    dateString(graenRuntime.heartbeat_at) ||
    dateString(graen.last_heartbeat_at) ||
    dateString(graen.observed_at);
  const nostraObservedAt = latestTimestamp(
    row.nostra_latest_forecast_at,
    row.nostra_latest_snapshot_at,
  );

  const rhenProbeState = normalizedState(rhen.status);
  const rhenHealth =
    live && rhenProbeState === "OFFLINE"
      ? "OBSERVABILITY_DEGRADED"
      : rhenProbeState;

  const queueDepth = numberOrZero(graenRuntime.queue_depth);
  const graenState =
    normalizedState(graen.status) !== "UNKNOWN"
      ? normalizedState(graen.status)
      : graenObservedAt && Date.now() - Date.parse(graenObservedAt) < 180_000
        ? "RUNNING"
        : "STALE";
  const graenHealth = optionalString(graenRuntime.last_error)
    ? "DEGRADED"
    : graenState === "RUNNING"
      ? "HEALTHY"
      : graenState;

  const nostraForecasts24h = numberOrZero(row.nostra_forecasts_24h);
  const nostraSnapshots24h = numberOrZero(row.nostra_snapshots_24h);
  const nostraRecords24h = nostraForecasts24h + nostraSnapshots24h;
  const nostraIndependent = nostra.independent_runtime === true;

  return {
    IREN: {
      runtime_state: normalizedState(iren.status),
      health_state: normalizedState(controlState.state),
      tracking_state: "CANONICAL_CONTROL_STATE",
      observed_at: dateString(iren.last_heartbeat_at) || controlObservedAt,
      independent_runtime: iren.independent_runtime === true,
      activity: optionalString(iren.current_activity) || "Deterministic supervision",
    },
    RHEN: {
      runtime_state: live ? "RUNNING" : normalizedState(rhen.status, latestEventAt ? "STALE" : "OFFLINE"),
      health_state: rhenHealth,
      tracking_state: live ? "LIVE_TELEMETRY" : "STALE_TELEMETRY",
      observed_at: latestEventAt || dateString(rhen.observed_at),
      independent_runtime: true,
      activity: `${events60m} public events / 60m`,
    },
    GRAEN: {
      runtime_state: graenState,
      health_state: graenHealth,
      tracking_state: "CANONICAL_RUNTIME",
      observed_at: graenObservedAt,
      independent_runtime: true,
      activity: queueDepth > 0 ? `${queueDepth} queued research problem${queueDepth === 1 ? "" : "s"}` : "Worker active · queue clear",
    },
    NOSTRA: {
      runtime_state: nostraIndependent ? normalizedState(nostra.status) : "EMBEDDED",
      health_state: nostraIndependent ? normalizedState(nostra.status) : "RESEARCH_ONLY",
      tracking_state: nostraRecords24h > 0 ? "COLLECTING" : "AWAITING_INDEPENDENT_RUNTIME",
      observed_at: nostraObservedAt || dateString(nostra.observed_at),
      independent_runtime: nostraIndependent,
      activity: nostraRecords24h > 0
        ? `${nostraForecasts24h} forecasts / 24h · ${nostraSnapshots24h} snapshots / 24h`
        : "Independent forecast runtime not yet activated",
    },
    VELUM: {
      runtime_state: normalizedState(velum.status, "UNKNOWN"),
      health_state: velum.liveness === true ? "HEALTHY" : normalizedState(velum.status),
      tracking_state: velum.liveness === true ? "READY" : "UNAVAILABLE",
      observed_at: dateString(velum.last_heartbeat_at) || dateString(velum.observed_at),
      independent_runtime: velum.independent_runtime === true,
      activity: optionalString(velum.current_activity) || (normalizedState(velum.status) === "IDLE" ? "Replay worker ready" : "Replay runtime"),
    },
  };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "GET") return json(405, { ok: false, error: "method_not_allowed" });

  try {
    const [
      strategyRows,
      strategyHistoryRows,
      summaryRows,
      recentRows,
      bucketRows,
      dailyRows,
      weeklyEventRows,
      decisionRows,
      weeklyRows,
      questionRows,
      outcomeRows,
      comparisonRows,
      scanRows,
      systemStateRows,
    ] = await Promise.all([
      sql.unsafe("select version_id, strategy_name, environment, status, activated_at from private.trading_strategy_versions where environment = 'live' and status = 'active' order by activated_at desc limit 1"),
      sql.unsafe("select version_id, strategy_name, environment, status, activated_at, retired_at from private.trading_strategy_versions where environment = 'live' order by coalesce(activated_at,created_at) desc limit 12"),
      sql.unsafe("select max(occurred_at) as latest_event_at, count(*) filter (where occurred_at >= now() - interval '60 minutes') as events_60m, count(*) filter (where event_type = 'scan' and occurred_at >= now() - interval '10 minutes') as scan_events_10m, count(distinct symbol) filter (where event_type = 'scan' and occurred_at >= now() - interval '10 minutes') as symbols_10m, count(*) filter (where event_type in ('execution','broker_order','broker_fill') and occurred_at >= now() - interval '2 hours') as execution_events_2h, count(*) filter (where event_type = 'reconciliation' and occurred_at >= now() - interval '2 hours') as reconciliations_2h, count(*) filter (where event_type = 'runtime_error' and occurred_at >= now() - interval '2 hours') as errors_2h, (select count(*)::int from private.trading_positions where status = 'closed') as closed_trades, (select count(*)::int from private.trading_positions where status = 'closed' and coalesce(net_pnl, realized_pnl, 0) > 0) as wins, (select count(*)::int from private.trading_positions where status = 'closed' and coalesce(net_pnl, realized_pnl, 0) <= 0) as losses, (select min(opened_at) from private.trading_positions where status = 'closed') as first_trade_at, (select max(closed_at) from private.trading_positions where status = 'closed') as last_trade_at from private.trading_events where occurred_at >= now() - interval '2 hours'"),
      sql.unsafe("select event_type, occurred_at from private.trading_events where occurred_at >= now() - interval '2 hours' and event_type in ('scan','allocation','signal','order_intent','execution','broker_order','broker_fill','exit','reconciliation','runtime_error','runtime_start','runtime_stop') order by occurred_at desc limit 18"),
      sql.unsafe("select date_bin(interval '10 minutes', occurred_at, timestamptz '2001-01-01') as bucket, count(*) as event_count from private.trading_events where occurred_at >= now() - interval '60 minutes' group by 1 order by 1 asc"),
      sql.unsafe("select event_type, occurred_at, payload from private.trading_events where event_type='research_daily_report' order by coalesce(nullif(payload->>'generated_at','')::timestamptz,occurred_at) desc,received_at desc limit 1"),
      sql.unsafe("select event_type, occurred_at, payload from private.trading_events where event_type='research_weekly_report' order by coalesce(nullif(payload->>'generated_at','')::timestamptz,occurred_at) desc,received_at desc limit 1"),
      sql.unsafe("select decision_key,status,decision_type,subject,conclusion,methodology_version,evidence,decided_at from private.trading_research_decisions order by decided_at desc limit 12"),
      sql.unsafe("select report_version,period_start,period_end,completeness_state,expected_sessions,included_sessions,missing_sessions,generated_at from private.trading_weekly_reports order by period_end desc,generated_at desc,created_at desc limit 1"),
      sql.unsafe("select * from (select distinct on (research_question_id) research_question_id,status,question,why_it_matters,sample_size,created_on,created_at from private.trading_research_questions order by research_question_id,created_at desc) q where status in ('OPEN','MONITOR','ACTIVE') order by created_at desc limit 8"),
      sql.unsafe("select horizon_minutes,status,count(*)::int as count from private.trading_candidate_forward_outcomes group by horizon_minutes,status order by horizon_minutes,status"),
      sql.unsafe("select coalesce(nullif(payload->>'match_state',''),'UNKNOWN') as match_state,count(*)::int as count from private.trading_events where event_type='live_offline_comparison' group by 1 order by 1"),
      sql.unsafe("select observed_at,market_session,cycle_outcome,data_status,degraded from private.trading_scan_cycles order by observed_at desc limit 1"),
      sql.unsafe(`select
        (select state from private.iren_control_state where singleton = true limit 1) as iren_control_state,
        (select updated_at from private.iren_control_state where singleton = true limit 1) as iren_updated_at,
        (select jsonb_build_object(
          'heartbeat_at', heartbeat_at,
          'queue_depth', queue_depth,
          'last_error', last_error
        ) from private.graen_runtime_state where singleton = true limit 1) as graen_runtime,
        (select max(generated_at) from private.nostra_forecasts) as nostra_latest_forecast_at,
        (select count(*)::int from private.nostra_forecasts where generated_at >= now() - interval '24 hours') as nostra_forecasts_24h,
        (select max(as_of_timestamp) from private.nostra_snapshots) as nostra_latest_snapshot_at,
        (select count(*)::int from private.nostra_snapshots where created_at >= now() - interval '24 hours') as nostra_snapshots_24h`),
    ]);

    const [performanceRows, performanceCurveRows] = await Promise.all([
      sql.unsafe(`with active_epoch as (
          select epoch_id, baseline_snapshot_id, started_at, ended_at, reason
          from private.trading_performance_epochs
          order by started_at desc
          limit 1
        ),
        baseline as (
          select
            e.epoch_id,
            e.started_at as tracking_started_at,
            e.ended_at as tracking_ended_at,
            e.reason as baseline_reason,
            p.equity::numeric as first_equity
          from active_epoch e
          join public.trading_public_equity p
            on p.snapshot_id = e.baseline_snapshot_id
        ),
        epoch_equity as (
          select p.observed_at, p.equity::numeric
          from public.trading_public_equity p
          cross join baseline b
          where p.observed_at >= b.tracking_started_at
            and p.observed_at <= coalesce(b.tracking_ended_at, now())
        ),
        drawdowns as (
          select
            observed_at,
            equity,
            max(equity) over (
              order by observed_at
              rows between unbounded preceding and current row
            ) as peak_equity
          from epoch_equity
        ),
        equity as (
          select
            b.tracking_started_at,
            b.baseline_reason,
            b.first_equity,
            max(e.observed_at) as last_observed_at,
            (array_agg(e.equity order by e.observed_at desc))[1]::numeric as latest_equity,
            count(e.*)::int as snapshot_count,
            coalesce(max(
              case when d.peak_equity > 0
                then (d.peak_equity - d.equity) / d.peak_equity
                else 0 end
            ), 0)::numeric as max_drawdown_fraction
          from baseline b
          left join epoch_equity e on true
          left join drawdowns d on d.observed_at = e.observed_at
          group by b.tracking_started_at, b.tracking_ended_at, b.baseline_reason, b.first_equity
        ),
        trades as (
          select
            count(*)::int as closed_trades,
            count(*) filter (where coalesce(t.net_pnl, t.realized_pnl, 0) > 0)::int as wins,
            count(*) filter (where coalesce(t.net_pnl, t.realized_pnl, 0) < 0)::int as losses,
            count(distinct (t.closed_at at time zone 'America/New_York')::date)::int as trading_sessions,
            min(t.opened_at) as first_trade_at,
            max(t.closed_at) as last_trade_at
          from public.trading_public_trades t
          join public.trading_public_strategies s on s.version_id = t.strategy_version_id
          where s.environment = 'live'
        ),
        epoch_trades as (
          select
            coalesce(sum(coalesce(t.net_pnl, t.realized_pnl, 0)), 0)::numeric as net_realized_pnl
          from public.trading_public_trades t
          join public.trading_public_strategies s on s.version_id = t.strategy_version_id
          cross join baseline b
          where s.environment = 'live'
            and t.closed_at >= b.tracking_started_at
            and t.closed_at <= coalesce(b.tracking_ended_at, now())
        )
        select
          equity.tracking_started_at,
          equity.last_observed_at,
          equity.first_equity,
          equity.latest_equity,
          equity.max_drawdown_fraction,
          equity.snapshot_count,
          equity.baseline_reason,
          trades.closed_trades,
          trades.wins,
          trades.losses,
          trades.trading_sessions,
          trades.first_trade_at,
          trades.last_trade_at,
          case
            when equity.first_equity > 0 and equity.latest_equity is not null
              then ((equity.latest_equity / equity.first_equity) - 1) * 100
            else null
          end as account_return_pct,
          case
            when equity.first_equity > 0
              then (epoch_trades.net_realized_pnl / equity.first_equity) * 100
            else null
          end as realized_return_pct
        from equity cross join trades cross join epoch_trades`),
      sql.unsafe(`with active_epoch as (
          select baseline_snapshot_id, started_at, ended_at
          from private.trading_performance_epochs
          order by started_at desc
          limit 1
        ),
        baseline as (
          select e.started_at, e.ended_at, p.equity::numeric as first_equity
          from active_epoch e
          join public.trading_public_equity p
            on p.snapshot_id = e.baseline_snapshot_id
        ),
        ordered as (
          select
            p.observed_at,
            p.equity,
            row_number() over (order by p.observed_at) as rn,
            count(*) over () as n
          from public.trading_public_equity p
          cross join baseline b
          where p.observed_at >= b.started_at
            and p.observed_at <= coalesce(b.ended_at, now())
        ),
        sampled as (
          select *
          from ordered
          where rn = 1
             or rn = n
             or mod(rn - 1, greatest(1, ceil(n / 72.0)::int)) = 0
        )
        select
          sampled.observed_at as at,
          case
            when baseline.first_equity > 0
              then ((sampled.equity / baseline.first_equity) - 1) * 100
            else null
          end as return_pct
        from sampled cross join baseline
        order by sampled.observed_at asc`),
    ]);


    const [marketPerformanceRows, marketPerformanceCurveRows, marketStrategyRows] = await Promise.all([
      sql.unsafe(`with current_epoch as (
          select baseline_snapshot_id, started_at, ended_at, reason
          from private.trading_performance_epochs
          order by started_at desc
          limit 1
        ),
        baseline as (
          select
            e.started_at,
            e.ended_at,
            e.reason,
            p.equity::numeric as first_equity
          from current_epoch e
          join public.trading_public_equity p
            on p.snapshot_id = e.baseline_snapshot_id
        ),
        position_lanes as (
          select
            p.position_id,
            p.strategy_version_id,
            p.opened_at,
            p.closed_at,
            coalesce(p.realized_pnl, 0)::numeric as pnl,
            coalesce(
              o.market_lane,
              i.market_lane,
              sg.market_lane,
              case when p.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end
            ) as lane
          from private.trading_positions p
          join private.trading_strategy_versions sv
            on sv.version_id = p.strategy_version_id
           and sv.environment = 'live'
          left join private.trading_orders o
            on o.broker_order_id = p.payload->>'entry_order_id'
          left join private.trading_order_intents i
            on i.intent_id = o.order_intent_id
          left join private.trading_signals sg
            on sg.signal_id = i.signal_id
          where p.status = 'closed'
            and p.closed_at is not null
        ),
        epoch_trades as (
          select pl.*
          from position_lanes pl
          cross join baseline b
          where pl.closed_at >= b.started_at
            and pl.closed_at <= coalesce(b.ended_at, now())
        ),
        cumulative as (
          select
            lane,
            closed_at,
            sum(pnl) over (
              partition by lane
              order by closed_at
              rows between unbounded preceding and current row
            ) as cumulative_pnl
          from epoch_trades
        ),
        peaks as (
          select
            lane,
            closed_at,
            cumulative_pnl,
            max(cumulative_pnl) over (
              partition by lane
              order by closed_at
              rows between unbounded preceding and current row
            ) as peak_pnl
          from cumulative
        ),
        drawdowns as (
          select
            lane,
            max(greatest(peak_pnl - cumulative_pnl, 0))::numeric as max_realized_drawdown
          from peaks
          group by lane
        )
        select
          pl.lane,
          count(*)::int as closed_trades,
          count(*) filter (where pl.pnl > 0)::int as wins,
          count(*) filter (where pl.pnl < 0)::int as losses,
          count(distinct (pl.closed_at at time zone 'America/New_York')::date)::int as active_periods,
          min(pl.opened_at) as first_trade_at,
          max(pl.closed_at) as last_trade_at,
          (array_agg(pl.strategy_version_id order by pl.closed_at desc))[1] as latest_trade_strategy_version,
          case
            when b.first_equity > 0
              then coalesce(sum(pl.pnl) filter (
                where pl.closed_at >= b.started_at
                  and pl.closed_at <= coalesce(b.ended_at, now())
              ), 0) / b.first_equity * 100
            else null
          end as realized_return_pct,
          case
            when b.first_equity > 0
              then coalesce(d.max_realized_drawdown, 0) / b.first_equity * 100
            else null
          end as max_drawdown_pct,
          b.started_at as tracking_started_at,
          b.ended_at as tracking_ended_at,
          b.reason as baseline_reason
        from position_lanes pl
        cross join baseline b
        left join drawdowns d on d.lane = pl.lane
        group by pl.lane, b.first_equity, b.started_at, b.ended_at, b.reason, d.max_realized_drawdown
        order by pl.lane`),
      sql.unsafe(`with current_epoch as (
          select baseline_snapshot_id, started_at, ended_at
          from private.trading_performance_epochs
          order by started_at desc
          limit 1
        ),
        baseline as (
          select e.started_at, e.ended_at, p.equity::numeric as first_equity
          from current_epoch e
          join public.trading_public_equity p
            on p.snapshot_id = e.baseline_snapshot_id
        ),
        live_trades as (
          select
            p.closed_at,
            coalesce(p.realized_pnl, 0)::numeric as pnl,
            coalesce(
              o.market_lane,
              i.market_lane,
              sg.market_lane,
              case when p.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end
            ) as lane
          from private.trading_positions p
          join private.trading_strategy_versions sv
            on sv.version_id = p.strategy_version_id
           and sv.environment = 'live'
          left join private.trading_orders o
            on o.broker_order_id = p.payload->>'entry_order_id'
          left join private.trading_order_intents i
            on i.intent_id = o.order_intent_id
          left join private.trading_signals sg
            on sg.signal_id = i.signal_id
          cross join baseline b
          where p.status = 'closed'
            and p.closed_at is not null
            and p.closed_at >= b.started_at
            and p.closed_at <= coalesce(b.ended_at, now())
        ),
        lanes as (
          select distinct lane from live_trades
        ),
        cumulative as (
          select
            lane,
            closed_at,
            sum(pnl) over (
              partition by lane
              order by closed_at
              rows between unbounded preceding and current row
            ) as cumulative_pnl
          from live_trades
        )
        select lane, at, return_pct
        from (
          select
            lanes.lane,
            b.started_at as at,
            0::numeric as return_pct
          from lanes cross join baseline b
          union all
          select
            c.lane,
            c.closed_at as at,
            case when b.first_equity > 0
              then c.cumulative_pnl / b.first_equity * 100
              else null
            end as return_pct
          from cumulative c cross join baseline b
        ) q
        order by lane, at`),
      sql.unsafe(`select distinct on (lane)
          lane, version_id, strategy_name, environment, status, activated_at, created_at
        from (
          select
            case when version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end as lane,
            version_id, strategy_name, environment, status, activated_at, created_at
          from private.trading_strategy_versions
          where environment = 'live'
             or version_id like 'CRYPTO-%'
        ) versions
        order by lane, coalesce(activated_at, created_at) desc`),
    ]);

    const strategy = strategyRows[0] ?? null;
    const summary = summaryRows[0] ?? {};
    const latestEventAt = summary.latest_event_at ? String(summary.latest_event_at) : null;
    const freshnessSeconds = latestEventAt
      ? Math.max(0, (Date.now() - Date.parse(latestEventAt)) / 1000)
      : null;
    const live = freshnessSeconds != null && freshnessSeconds < 120;

    const latestDaily = dailyRows[0] ? publicResearchEvent(dailyRows[0]) : null;
    const latestWeekly = weeklyEventRows[0] ? publicResearchEvent(weeklyEventRows[0]) : null;
    const decisions = decisionRows.map((row) => publicDecision(row));
    const nextDirection =
      decisions.find((row) => row.decision_type === "next_research_direction") || null;
    const completedDecisions =
      decisions.filter((row) => row.decision_type !== "next_research_direction");
    const weekly = weeklyRows[0] ?? null;
    const systems = publicSystemStates(
      objectValue(systemStateRows[0]),
      live,
      latestEventAt,
      numberOrZero(summary.events_60m),
    );
    const missingSessions = weekly ? safeStringArray(weekly.missing_sessions) : [];
    const includedSessions = weekly ? safeStringArray(weekly.included_sessions) : [];

    const performance = performanceRows[0] ?? {};
    const closedTrades = numberOrZero(performance.closed_trades);
    const wins = numberOrZero(performance.wins);
    const losses = numberOrZero(performance.losses);
    const tradingSessions = numberOrZero(performance.trading_sessions);
    const baselineReset = String(performance.baseline_reason || "") === "external_cash_flow";
    const sampleState =
      closedTrades >= 100 && tradingSessions >= 20
        ? "LONGER_HISTORY"
        : closedTrades >= 50 && tradingSessions >= 10
          ? "BUILDING_HISTORY"
          : "EARLY_SAMPLE";
    const accountReturnPct =
      performance.account_return_pct == null ? null : Number(performance.account_return_pct);
    const realizedReturnPct =
      performance.realized_return_pct == null ? null : Number(performance.realized_return_pct);
    const maxDrawdownPct =
      performance.max_drawdown_fraction == null
        ? null
        : Number(performance.max_drawdown_fraction) * 100;
    const winRatePct = closedTrades > 0 ? (wins / closedTrades) * 100 : null;
    const publicCurve = performanceCurveRows
      .filter((row) => row.return_pct != null)
      .map((row) => ({ at: row.at, return_pct: Number(row.return_pct) }));


    const marketStrategy = (lane: string) =>
      marketStrategyRows.find((row) => String(row.lane) === lane) ?? null;
    const marketStats = (lane: string) =>
      marketPerformanceRows.find((row) => String(row.lane) === lane) ?? null;
    const publicMarketPerformance = (lane: "us_equity" | "crypto") => {
      const row = marketStats(lane);
      const strategyRow = marketStrategy(lane);
      const closed = numberOrZero(row?.closed_trades);
      const winsLane = numberOrZero(row?.wins);
      const lossesLane = numberOrZero(row?.losses);
      const activePeriods = numberOrZero(row?.active_periods);
      const sampleState =
        closed === 0 ? "AWAITING_LIVE_SAMPLE" :
        closed >= 100 && activePeriods >= 20 ? "LONGER_HISTORY" :
        closed >= 50 && activePeriods >= 10 ? "BUILDING_HISTORY" :
        "EARLY_SAMPLE";
      const curve = marketPerformanceCurveRows
        .filter((point) => String(point.lane) === lane && point.return_pct != null)
        .map((point) => ({ at: point.at, return_pct: Number(point.return_pct) }));
      return {
        market_lane: lane,
        methodology_version: "PUBLIC-MARKET-PERFORMANCE-v1",
        basis: "market_lane_realized_live_trade_performance_over_current_performance_epoch_baseline",
        status: closed > 0 ? "TRACKING" : "AWAITING_LIVE_SAMPLE",
        sample_state: sampleState,
        tracking_started_at: row?.tracking_started_at || null,
        tracking_ended_at: row?.tracking_ended_at || null,
        first_trade_at: row?.first_trade_at || null,
        last_trade_at: row?.last_trade_at || null,
        active_periods: activePeriods,
        closed_trades: closed,
        wins: winsLane,
        losses: lossesLane,
        win_rate_pct: closed > 0 ? (winsLane / closed) * 100 : null,
        realized_return_pct:
          closed > 0 && row?.realized_return_pct != null
            ? Number(row.realized_return_pct)
            : null,
        max_drawdown_pct:
          closed > 0 && row?.max_drawdown_pct != null
            ? Number(row.max_drawdown_pct)
            : null,
        strategy_version_id:
          row?.latest_trade_strategy_version || strategyRow?.version_id || null,
        strategy_name: strategyRow?.strategy_name || null,
        strategy_environment: strategyRow?.environment || null,
        strategy_status: strategyRow?.status || null,
        denominator: "current_performance_epoch_baseline_equity",
        curve,
        limitations: [
          "This lane record includes closed live trades only; research, paper, replay, and simulation are excluded.",
          "Lane realized return is cumulative lane realized P&L within the current performance epoch divided by the shared account epoch baseline. It is not a separately funded account-equity curve.",
          "Dollar values, symbols, prices, quantities, orders, fills, and execution-sensitive parameters remain private.",
        ],
      };
    };
    const equityMarketPerformance = publicMarketPerformance("us_equity");
    const cryptoMarketPerformance = publicMarketPerformance("crypto");

    const limitations = [
      ...(missingSessions.length
        ? [`Canonical weekly reporting is ${String(weekly?.completeness_state || "PARTIAL")}; ${missingSessions.length} expected session(s) are missing a canonical daily report.`]
        : []),
      "Historical candidate telemetry is partially backfilled, so some rejected opportunities cannot be reconstructed.",
      "Some historical live-vs-offline decisions are unreconstructable where exact decision-time inputs were not retained.",
    ];

    const journal = [
      ...decisions.slice(0, 6).map((row) => ({
        at: row.at,
        type: row.decision_type,
        title: row.subject || "Research decision",
        summary: row.conclusion,
        classification: row.status,
        focus: row.subject,
        next_action: null,
        session: null,
        week_start: null,
        week_end: null,
        warnings: [],
      })),
      ...(latestWeekly ? [latestWeekly] : []),
      ...(latestDaily ? [latestDaily] : []),
    ].sort((a, b) => Date.parse(String(b.at || "")) - Date.parse(String(a.at || "")));

    return json(200, {
      ok: true,
      generated_at: new Date().toISOString(),
      source: "anevum_public_telemetry",
      live,
      freshness_seconds: freshnessSeconds,
      state: live ? "RUNNING" : latestEventAt ? "STALE" : "OFFLINE",
      systems,
      active_strategy: strategy
        ? {
            version_id: strategy.version_id,
            strategy_name: strategy.strategy_name,
            environment: strategy.environment,
            status: strategy.status,
            activated_at: strategy.activated_at,
          }
        : null,
      strategy_history: strategyHistoryRows,
      telemetry: {
        events_60m: numberOrZero(summary.events_60m),
        scan_events_10m: numberOrZero(summary.scan_events_10m),
        symbols_10m: numberOrZero(summary.symbols_10m),
        execution_events_2h: numberOrZero(summary.execution_events_2h),
        reconciliations_2h: numberOrZero(summary.reconciliations_2h),
        errors_2h: numberOrZero(summary.errors_2h),
      },
      activity: bucketRows.map((row) => ({
        at: row.bucket,
        count: numberOrZero(row.event_count),
      })),
      events: recentRows.map((row) => publicEvent(row.event_type ?? null, row.occurred_at ?? null)),
      operational: {
        latest_scan: scanRows[0] ?? null,
      },
      research: {
        current_focus: nextDirection?.subject || null,
        current_status: nextDirection?.status || null,
        last_updated_at: decisions[0]?.at || latestWeekly?.at || latestDaily?.at || null,
        next_direction: nextDirection,
        completed_decisions: completedDecisions,
        active_questions: questionRows,
        latest_daily: latestDaily,
        latest_weekly: latestWeekly,
        latest_weekly_summary: weekly
          ? {
              report_version: weekly.report_version,
              period_start: weekly.period_start,
              period_end: weekly.period_end,
              completeness_state: weekly.completeness_state,
              expected_session_count: safeStringArray(weekly.expected_sessions).length,
              included_session_count: includedSessions.length,
              missing_session_count: missingSessions.length,
              included_sessions: includedSessions,
              missing_sessions: missingSessions,
              generated_at: weekly.generated_at,
            }
          : null,
        evidence: {
          candidate_forward_outcomes: outcomeRows,
          live_offline_comparison: comparisonRows,
          analytics_only: true,
        },
        limitations,
        journal,
      },
      market_performance: {
        methodology_version: "PUBLIC-MARKET-PERFORMANCE-v1",
        equities: equityMarketPerformance,
        crypto: cryptoMarketPerformance,
        limitations: [
          "Equities and crypto are reported as separate market lanes and are never combined into one curve.",
          "Market-lane curves are realized live-trade performance normalized to the shared current performance-epoch baseline; they are not separately funded account-equity curves.",
          "Replay, simulation, paper, shadow, and development results are excluded from live market performance."
        ]
      },

      performance: {
        methodology_version: "PUBLIC-PERFORMANCE-v2",
        basis: "broker_derived_live_ledger_with_cash_flow_epochs",
        status: "TRACKING",
        sample_state: sampleState,
        tracking_started_at: performance.tracking_started_at || null,
        last_observed_at: performance.last_observed_at || null,
        first_trade_at: performance.first_trade_at || null,
        last_trade_at: performance.last_trade_at || null,
        snapshot_count: numberOrZero(performance.snapshot_count),
        trading_sessions: tradingSessions,
        closed_trades: closedTrades,
        wins,
        losses,
        win_rate_pct: winRatePct,
        account_return_pct: accountReturnPct,
        realized_return_pct: realizedReturnPct,
        max_drawdown_pct: maxDrawdownPct,
        external_cash_flows_present: baselineReset,
        baseline_reason: performance.baseline_reason || null,
        baseline_reset: baselineReset,
        curve: publicCurve,
        limitations: [
          "This is a short live sample and is not evidence of future performance.",
          "Dollar account values, symbols, prices, quantities, orders, fills, and individual trade records are excluded from the public feed.",
          baselineReset
            ? "An external cash-flow boundary created a new performance epoch; current normalized return and drawdown start from the first post-flow account snapshot while prior history remains preserved."
            : "Normalized return uses the active performance epoch baseline.",
        ],
      },
      disclosure: {
        level: "sanitized",
        public_fields: [
          "runtime state",
          "active strategy identity",
          "telemetry freshness",
          "aggregate activity",
          "anonymized event classes",
          "sanitized research decisions",
          "sanitized evidence availability",
          "sanitized strategy history",
          "sanitized per-system runtime and tracking state",
          "normalized live performance percentages",
          "normalized public performance curve",
          "separate normalized equities and crypto live-trade performance",
        ],
        excluded_fields: [
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
        ]
      }
    });
  } catch (error) {
    console.error("public_feed_failed", error);
    return json(500, { ok: false, error: "public_feed_failed" });
  }
});
