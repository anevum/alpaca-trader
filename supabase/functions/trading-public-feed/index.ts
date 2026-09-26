import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 3,
  idle_timeout: 20,
  connect_timeout: 10,
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
    ] = await Promise.all([
      sql.unsafe("select version_id, strategy_name, environment, status, activated_at from private.trading_strategy_versions where environment = 'live' and status = 'active' order by activated_at desc limit 1"),
      sql.unsafe("select version_id, strategy_name, environment, status, activated_at, retired_at from private.trading_strategy_versions where environment = 'live' order by coalesce(activated_at,created_at) desc limit 12"),
      sql.unsafe("select max(occurred_at) as latest_event_at, count(*) filter (where occurred_at >= now() - interval '60 minutes') as events_60m, count(*) filter (where event_type = 'scan' and occurred_at >= now() - interval '10 minutes') as scan_events_10m, count(distinct symbol) filter (where event_type = 'scan' and occurred_at >= now() - interval '10 minutes') as symbols_10m, count(*) filter (where event_type in ('execution','broker_order','broker_fill') and occurred_at >= now() - interval '2 hours') as execution_events_2h, count(*) filter (where event_type = 'reconciliation' and occurred_at >= now() - interval '2 hours') as reconciliations_2h, count(*) filter (where event_type = 'runtime_error' and occurred_at >= now() - interval '2 hours') as errors_2h from private.trading_events where occurred_at >= now() - interval '2 hours'"),
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
    const terminalDecisions =
      decisions.filter((row) => row.decision_type === "terminal_rejection");
    const weekly = weeklyRows[0] ?? null;
    const missingSessions = weekly ? safeStringArray(weekly.missing_sessions) : [];
    const includedSessions = weekly ? safeStringArray(weekly.included_sessions) : [];

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
        completed_decisions: terminalDecisions,
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
        ]
      }
    });
  } catch (error) {
    console.error("public_feed_failed", error);
    return json(500, { ok: false, error: "public_feed_failed" });
  }
});
