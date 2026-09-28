import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 1,
  idle_timeout: 20,
  connect_timeout: 10,
});

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function validDate(value: string | null): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value));
}

async function authorized(req: Request): Promise<boolean> {
  const token = req.headers.get("x-anevum-ingest-token")?.trim();
  if (!token || token.length < 32) return false;
  const tokenHash = await sha256Hex(token);
  const rows = await sql<{ token_id: string }[]>`
    select token_id
    from private.trading_ingest_tokens
    where token_hash = ${tokenHash}
      and active = true
      and revoked_at is null
    limit 1
  `;
  return rows.length === 1;
}

Deno.serve(async (req) => {
  if (req.method !== "GET") return json(405, { ok: false, error: "method_not_allowed" });
  if (!(await authorized(req))) return json(401, { ok: false, error: "unauthorized" });

  const url = new URL(req.url);
  const start = url.searchParams.get("start");
  const end = url.searchParams.get("end");
  const latest = url.searchParams.get("latest");
  const weekEnd = url.searchParams.get("week_end");
  const evidenceSession = url.searchParams.get("evidence_session");
  const session = url.searchParams.get("session");

  try {
    if (validDate(start) && validDate(end)) {
      if (start > end) return json(422, { ok: false, error: "invalid_period" });
      const rows = await sql<{
        base_inputs: Record<string, unknown>;
        post_event_inputs: Record<string, unknown>;
      }[]>`
        select
          private.rhen_weekly_report_inputs(${start}::date, ${end}::date) as base_inputs,
          private.rhen_post_event_evidence_inputs(${start}::date, ${end}::date) as post_event_inputs
      `;
      return json(200, {
        ok: true,
        report_version: "rhen-weekly-v1.2",
        inputs: {
          ...(rows[0]?.base_inputs ?? {}),
          ...(rows[0]?.post_event_inputs ?? {}),
        },
      });
    }


    if (validDate(evidenceSession)) {
      const candidateRows = await sql<{
        candidate: Record<string, unknown>;
        scan_cycle: Record<string, unknown>;
        decision_cycle_payload: Record<string, unknown> | null;
        outcomes: unknown[];
        signal_id: string | null;
        intent_id: string | null;
        broker_order_id: string | null;
        order_status: string | null;
        ads002_score: Record<string, unknown> | null;
      }[]>`
        select
          to_jsonb(c) as candidate,
          to_jsonb(sc) as scan_cycle,
          de.payload as decision_cycle_payload,
          coalesce(fo.outcomes,'[]'::jsonb) as outcomes,
          sig.signal_id::text as signal_id,
          intent.intent_id::text as intent_id,
          ord.broker_order_id,
          ord.status as order_status,
          ads.score_payload as ads002_score
        from private.trading_candidate_evaluations c
        join private.trading_scan_cycles sc using(scan_cycle_id)
        left join lateral (
          select e.payload
          from private.trading_events e
          where e.event_type='decision_cycle'
            and e.payload->>'cycle_key'=sc.cycle_key
          order by e.received_at desc
          limit 1
        ) de on true
        left join lateral (
          select jsonb_agg(to_jsonb(o) order by o.horizon_minutes) as outcomes
          from private.trading_candidate_forward_outcomes o
          where o.candidate_id=c.candidate_id
        ) fo on true
        left join lateral (
          select s.signal_id
          from private.trading_signals s
          where s.candidate_id=c.candidate_id
             or (c.signal_id is not null and s.signal_id=c.signal_id)
          order by s.signal_at desc
          limit 1
        ) sig on true
        left join lateral (
          select i.intent_id
          from private.trading_order_intents i
          where i.signal_id=sig.signal_id
          order by i.intended_at desc
          limit 1
        ) intent on true
        left join lateral (
          select o.broker_order_id,o.status
          from private.trading_orders o
          where o.order_intent_id=intent.intent_id
          order by coalesce(o.submitted_at,o.updated_at) desc
          limit 1
        ) ord on true
        left join lateral (
          select jsonb_build_object(
            'attention_score', s.attention_score,
            'qualification_score', s.qualification_score,
            'timing_score', s.timing_score,
            'pretrade_composite', s.pretrade_composite,
            'source_completeness', s.source_completeness,
            'methodology_version', s.methodology_version
          ) as score_payload
          from private.trading_ads_shadow_scores s
          where s.candidate_id=c.candidate_id
            and s.methodology_version='ads-shadow-v1'
          limit 1
        ) ads on true
        where (c.observed_at at time zone 'America/New_York')::date=${evidenceSession}::date
        order by c.observed_at,c.symbol
      `;

      const postRows = await sql<{ inputs: Record<string, unknown> }[]>`
        select private.rhen_post_event_evidence_inputs(
          ${evidenceSession}::date,
          ${evidenceSession}::date
        ) as inputs
      `;

      const adsRows = await sql<{ inputs: Record<string, unknown> }[]>`
        select private.rhen_ads002_daily_inputs(
          ${evidenceSession}::date
        ) as inputs
      `;

      const dailyRows = await sql<{
        event_id: string;
        occurred_at: string;
        payload: Record<string, unknown>;
      }[]>`
        select event_id::text,occurred_at::text,payload
        from private.trading_events
        where event_type='research_daily_report'
          and nullif(payload->>'session','')::date=${evidenceSession}::date
        order by coalesce(
          nullif(payload->>'generated_at','')::timestamptz,
          occurred_at
        ) desc,received_at desc
        limit 1
      `;

      return json(200, {
        ok: true,
        evidence_session: evidenceSession,
        candidates: candidateRows.map((row) => ({
          ...(row.candidate ?? {}),
          scan_cycle: row.scan_cycle ?? {},
          decision_cycle_payload: row.decision_cycle_payload,
          outcomes: row.outcomes ?? [],
          signal_id: row.signal_id,
          intent_id: row.intent_id,
          broker_order_id: row.broker_order_id,
          order_status: row.order_status,
          ads002_score: row.ads002_score,
          submitted: Boolean(row.intent_id),
        })),
        post_event: postRows[0]?.inputs ?? {},
        ads002: adsRows[0]?.inputs ?? {},
        latest_daily_report: dailyRows[0] ?? null,
      });
    }

    if (latest === "daily") {
      if (session != null && !validDate(session)) {
        return json(422, { ok: false, error: "invalid_session" });
      }
      const rows = session
        ? await sql<{ payload: Record<string, unknown> }[]>`
            select payload
            from private.trading_events
            where event_type='research_daily_report'
              and nullif(payload->>'session','')::date=${session}::date
            order by coalesce(
              nullif(payload->>'generated_at','')::timestamptz,
              occurred_at
            ) desc, received_at desc
            limit 1
          `
        : await sql<{ payload: Record<string, unknown> }[]>`
            select payload
            from private.trading_events
            where event_type='research_daily_report'
            order by coalesce(
              nullif(payload->>'generated_at','')::timestamptz,
              occurred_at
            ) desc, received_at desc
            limit 1
          `;
      return json(200, {
        ok: true,
        report_version: rows[0]?.payload?.report_version ?? null,
        report: rows[0]?.payload ?? null,
      });
    }

    if (latest === "command") {
      const [
        dailyRows,
        weeklyRows,
        questionRows,
        weeklyDecisionRows,
        researchDecisionRows,
        outcomeRows,
        comparisonRows,
        runtimeRows,
        scanRows,
        healthRows,
      ] = await Promise.all([
        sql<{ payload: Record<string, unknown> }[]>`
          select payload
          from private.trading_events
          where event_type='research_daily_report'
          order by coalesce(
            nullif(payload->>'generated_at','')::timestamptz,
            occurred_at
          ) desc, received_at desc
          limit 1
        `,
        sql<{ report_payload: Record<string, unknown> }[]>`
          select report_payload
          from private.trading_weekly_reports
          order by period_end desc, generated_at desc, created_at desc
          limit 1
        `,
        sql<Record<string, unknown>[]>`
          select *
          from (
            select distinct on (research_question_id)
              research_question_id, created_on, evidence_summary, sample_size,
              question, why_it_matters, required_data, status,
              linked_experiment_id, created_at
            from private.trading_research_questions
            order by research_question_id, created_at desc
          ) q
          order by created_at desc
          limit 24
        `,
        sql<Record<string, unknown>[]>`
          select *
          from (
            select distinct on (decision_key)
              decision_key, evidence, interpretation, decision, scope,
              production_behavior_changed, decided_at, created_at
            from private.trading_weekly_decisions
            order by decision_key, decided_at desc, created_at desc
          ) d
          order by decided_at desc
          limit 24
        `,
        sql<Record<string, unknown>[]>`
          select decision_key, decided_at, status, decision_type, subject,
                 conclusion, methodology_version, evidence, code_commit,
                 deployment_id, created_at
          from private.trading_research_decisions
          order by decided_at desc
          limit 24
        `,
        sql<Record<string, unknown>[]>`
          select horizon_minutes, status, count(*)::int as count
          from private.trading_candidate_forward_outcomes
          group by horizon_minutes, status
          order by horizon_minutes, status
        `,
        sql<Record<string, unknown>[]>`
          select
            coalesce(nullif(payload->>'session',''),'unknown') as session,
            coalesce(nullif(payload->>'match_state',''),'UNKNOWN') as match_state,
            count(*)::int as count
          from private.trading_events
          where event_type='live_offline_comparison'
          group by 1,2
          order by 1 desc,2
        `,
        sql<Record<string, unknown>[]>`
          select runtime_instance_id, run_id::text, strategy_version_id,
                 deployment_id, build_id, git_commit, repository, branch,
                 service_id, service_name, environment_id, environment_name,
                 system_version, started_at, stopped_at, metadata
          from private.trading_runtime_instances
          order by started_at desc
          limit 1
        `,
        sql<Record<string, unknown>[]>`
          select scan_cycle_id, run_id::text, observed_at, strategy_version_id,
                 runtime_instance_id, deployment_id, git_commit, candidate_count,
                 qualified_count, rejected_count, cycle_outcome, data_status,
                 degraded, error_text, execution_mode, market_session,
                 universe_version, data_source, data_feed, bar_interval,
                 methodology_version, strategy_family, build_id
          from private.trading_scan_cycles
          order by observed_at desc
          limit 1
        `,
        sql<Record<string, unknown>[]>`
          select
            count(*) filter (where occurred_at >= now() - interval '24 hours')::int as events_24h,
            count(*) filter (where event_type='runtime_error' and occurred_at >= now() - interval '24 hours')::int as runtime_errors_24h,
            count(*) filter (where event_type='scan' and occurred_at >= now() - interval '24 hours')::int as scan_events_24h,
            max(occurred_at) as latest_event_at,
            max(received_at) as latest_received_at
          from private.trading_events
        `,
      ]);

      return json(200, {
        ok: true,
        evidence_version: "rhen-command-evidence-v1",
        generated_at: new Date().toISOString(),
        latest_daily: dailyRows[0]?.payload ?? null,
        latest_weekly: weeklyRows[0]?.report_payload ?? null,
        research_questions: questionRows,
        weekly_decisions: weeklyDecisionRows,
        research_decisions: researchDecisionRows,
        post_event_evidence: {
          forward_outcomes: outcomeRows,
          live_offline: comparisonRows,
          analytics_only: true,
        },
        provenance: {
          runtime: runtimeRows[0] ?? null,
          latest_scan_cycle: scanRows[0] ?? null,
        },
        telemetry_health: healthRows[0] ?? null,
      });
    }

    if (latest === "weekly") {
      let rows: { report_payload: Record<string, unknown> }[];
      if (weekEnd != null) {
        if (!validDate(weekEnd)) return json(422, { ok: false, error: "invalid_week_end" });
        rows = await sql<{ report_payload: Record<string, unknown> }[]>`
          select report_payload
          from private.trading_weekly_reports
          where period_end = ${weekEnd}::date
          order by generated_at desc, created_at desc
          limit 1
        `;
      } else {
        rows = await sql<{ report_payload: Record<string, unknown> }[]>`
          select report_payload
          from private.trading_weekly_reports
          order by period_end desc, generated_at desc, created_at desc
          limit 1
        `;
      }
      return json(200, {
        ok: true,
        report_version: "rhen-weekly-v1.2",
        report: rows[0]?.report_payload ?? null,
      });
    }

    return json(400, { ok: false, error: "invalid_request" });
  } catch (error) {
    console.error("trading_report_read_failed", error);
    return json(500, { ok: false, error: "trading_report_read_failed" });
  }
});
