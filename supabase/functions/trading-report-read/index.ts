import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 8,
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
  const cryptoEvidenceSession = url.searchParams.get("crypto_evidence_session");
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


    if (validDate(cryptoEvidenceSession)) {
      const candidateRows = await sql<{
        candidate: Record<string, unknown>;
        forward_outcomes: Record<string, unknown>;
      }[]>`
        with bounds as (
          select
            (${cryptoEvidenceSession}::date::timestamp at time zone 'America/New_York') as starts_at,
            ((${cryptoEvidenceSession}::date + 1)::timestamp at time zone 'America/New_York') as ends_at
        )
        select
          jsonb_strip_nulls(jsonb_build_object(
            'candidate_id', c.candidate_id,
            'candidate_key', c.candidate_key,
            'scan_cycle_id', c.scan_cycle_id,
            'run_id', c.run_id,
            'strategy_version_id', c.strategy_version_id,
            'strategy_family', c.strategy_family,
            'market_lane', c.market_lane,
            'model_version', c.model_version,
            'calibration_version', c.calibration_version,
            'regime_version', c.regime_version,
            'execution_adapter_version', c.execution_adapter_version,
            'symbol', c.symbol,
            'observed_at', c.observed_at,
            'decision_reference_price', c.decision_reference_price,
            'qualified', c.qualified,
            'action', c.action,
            'reason', c.reason,
            'data_feed', c.data_feed,
            'bar_interval', c.bar_interval,
            'features', jsonb_strip_nulls(jsonb_build_object(
              'market', 'crypto',
              'market_lane', 'crypto',
              'bar_time', c.features->'bar_time'
            ))
          )) as candidate,
          outcomes.rows as forward_outcomes
        from private.trading_candidate_evaluations c
        cross join bounds b
        left join lateral (
          select
            coalesce(
              jsonb_object_agg(
                fo.horizon_minutes::text,
                jsonb_build_object(
                  'status', fo.status,
                  'computed_at', fo.computed_at,
                  'forward_return', fo.forward_return,
                  'max_favorable_return', fo.max_favorable_return,
                  'max_adverse_return', fo.max_adverse_return,
                  'methodology_version', fo.methodology_version
                )
                order by fo.horizon_minutes
              ),
              '{}'::jsonb
            ) as rows,
            count(*) filter (where fo.status='complete')::int as complete_count
          from private.trading_candidate_forward_outcomes fo
          where fo.candidate_id=c.candidate_id
            and fo.methodology_version='candidate-forward-crypto-v1'
        ) outcomes on true
        where c.observed_at >= b.starts_at
          and c.observed_at < b.ends_at
          and (
            c.market_lane='crypto'
            or c.strategy_version_id like 'CRYPTO-%'
            or c.features->>'market'='crypto'
          )
          and coalesce(outcomes.complete_count,0) < 7
        order by c.observed_at,c.symbol,c.candidate_id
        limit 5000
      `;

      return json(200, {
        ok: true,
        evidence_version: "rhen-crypto-forward-evidence-v1",
        evidence_session: cryptoEvidenceSession,
        candidates: candidateRows.map((row) => ({
          ...(row.candidate ?? {}),
          forward_outcomes: row.forward_outcomes ?? {},
        })),
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
        ads002_v2_scores: Record<string, unknown>;
      }[]>`
        with bounds as (
          select
            (${evidenceSession}::date::timestamp at time zone 'America/New_York') as starts_at,
            ((${evidenceSession}::date + 1)::timestamp at time zone 'America/New_York') as ends_at
        ),
        session_candidates as materialized (
          select
            c.candidate_id,
            c.signal_id,
            c.scan_cycle_id,
            c.observed_at,
            c.symbol,
            sc.cycle_key,
            jsonb_build_object(
              'candidate_id', c.candidate_id,
              'scan_cycle_id', c.scan_cycle_id,
              'run_id', c.run_id,
              'strategy_version_id', c.strategy_version_id,
              'candidate_key', c.candidate_key,
              'symbol', c.symbol,
              'session', (c.observed_at at time zone 'America/New_York')::date,
              'observed_at', c.observed_at,
              'decision_reference_price', c.decision_reference_price,
              'qualified', c.qualified,
              'action', c.action,
              'reason', c.reason,
              'rejection_reason_codes', c.rejection_reason_codes,
              'features', c.features,
              'checks', c.checks,
              'final_decision', c.final_decision,
              'data_feed', c.data_feed,
              'bar_interval', c.bar_interval,
              'quote', jsonb_strip_nulls(jsonb_build_object(
                'bid', c.decision_bid,
                'ask', c.decision_ask,
                'midpoint', c.decision_midpoint,
                'spread_pct', c.observed_spread,
                'observed_at', c.quote_observed_at
              ))
            ) as candidate,
            row_number() over (
              partition by c.scan_cycle_id
              order by c.observed_at,c.symbol,c.candidate_id
            ) as cycle_row_number
          from private.trading_candidate_evaluations c
          join private.trading_scan_cycles sc using(scan_cycle_id)
          cross join bounds b
          where c.observed_at >= b.starts_at
            and c.observed_at < b.ends_at
            and not (
              coalesce(c.market_lane,'')='crypto'
              or c.strategy_version_id like 'CRYPTO-%'
              or c.features->>'market'='crypto'
            )
        ),
        cycle_rows as materialized (
          select distinct on (c.scan_cycle_id)
            c.scan_cycle_id,
            c.cycle_key,
            jsonb_strip_nulls(jsonb_build_object(
              'scan_cycle_id', sc.scan_cycle_id,
              'cycle_key', sc.cycle_key,
              'run_id', sc.run_id,
              'strategy_version_id', sc.strategy_version_id,
              'observed_at', sc.observed_at,
              'runtime_instance_id', sc.runtime_instance_id,
              'deployment_id', sc.deployment_id,
              'data_status', sc.data_status,
              'data_feed', sc.data_feed,
              'bar_interval', sc.bar_interval
            )) as scan_cycle
          from session_candidates c
          join private.trading_scan_cycles sc
            on sc.scan_cycle_id=c.scan_cycle_id
          order by c.scan_cycle_id
        ),
        decision_events as (
          select distinct on (cr.scan_cycle_id)
            cr.scan_cycle_id,
            jsonb_build_object(
              'cycle_key', e.payload->'cycle_key',
              'comparison_context', e.payload->'comparison_context'
            ) as payload
          from cycle_rows cr
          join private.trading_events e
            on e.event_type='decision_cycle'
           and e.payload->>'cycle_key'=cr.cycle_key
          cross join bounds b
          where e.occurred_at >= b.starts_at - interval '5 minutes'
            and e.occurred_at < b.ends_at + interval '5 minutes'
          order by cr.scan_cycle_id,e.received_at desc
        ),
        signal_ids as (
          select distinct signal_id
          from session_candidates
          where signal_id is not null
        ),
        intent_rows as (
          select distinct on (i.signal_id)
            i.signal_id,
            i.intent_id
          from private.trading_order_intents i
          join signal_ids s using(signal_id)
          order by i.signal_id,i.intended_at desc
        ),
        order_rows as (
          select distinct on (o.order_intent_id)
            o.order_intent_id,
            o.broker_order_id,
            o.status
          from private.trading_orders o
          join intent_rows i on i.intent_id=o.order_intent_id
          order by o.order_intent_id,coalesce(o.submitted_at,o.updated_at) desc
        )
        select
          c.candidate,
          case
            when c.cycle_row_number=1 then cr.scan_cycle
            else '{}'::jsonb
          end as scan_cycle,
          case
            when c.cycle_row_number=1 then de.payload
            else null
          end as decision_cycle_payload,
          coalesce(outcomes.rows,'[]'::jsonb) as outcomes,
          c.signal_id::text as signal_id,
          intent.intent_id::text as intent_id,
          ord.broker_order_id,
          ord.status as order_status,
          case
            when ads.candidate_id is null then null
            else jsonb_build_object(
              'attention_score', ads.attention_score,
              'qualification_score', ads.qualification_score,
              'timing_score', ads.timing_score,
              'pretrade_composite', ads.pretrade_composite,
              'source_completeness', ads.source_completeness,
              'methodology_version', ads.methodology_version
            )
          end as ads002_score,
          ads_v2.scores as ads002_v2_scores
        from session_candidates c
        left join cycle_rows cr
          on cr.scan_cycle_id=c.scan_cycle_id
        left join decision_events de
          on de.scan_cycle_id=c.scan_cycle_id
        left join intent_rows intent
          on intent.signal_id=c.signal_id
        left join order_rows ord
          on ord.order_intent_id=intent.intent_id
        left join lateral (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'horizon_minutes', fo.horizon_minutes,
                'status', fo.status,
                'forward_return', fo.forward_return,
                'max_favorable_return', fo.max_favorable_return,
                'max_adverse_return', fo.max_adverse_return,
                'methodology_version', fo.methodology_version
              )
              order by fo.horizon_minutes
            ),
            '[]'::jsonb
          ) as rows
          from private.trading_candidate_forward_outcomes fo
          where fo.candidate_id=c.candidate_id
            and fo.methodology_version='candidate-forward-v2'
        ) outcomes on true
        left join private.trading_ads_shadow_scores ads
          on ads.candidate_id=c.candidate_id
         and ads.methodology_version='ads-shadow-v1'
        left join lateral (
          select coalesce(
            jsonb_object_agg(
              s.model_key,
              jsonb_build_object(
                'attention_score', s.attention_score,
                'qualification_score', s.qualification_score,
                'timing_score', s.timing_score,
                'raw_score', s.raw_score,
                'confidence_score', s.confidence_score,
                'effective_score', s.effective_score,
                'source_completeness', s.source_completeness,
                'methodology_version', s.methodology_version
              )
              order by s.model_key
            ),
            '{}'::jsonb
          ) as scores
          from private.trading_ads_challenger_scores s
          where s.candidate_id=c.candidate_id
            and s.methodology_version='ads-shadow-v2'
        ) ads_v2 on true
        order by c.observed_at,c.symbol
      `

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

      const adsV2Rows = await sql<{ inputs: Record<string, unknown> }[]>`
        select private.rhen_ads002_v2_daily_inputs(
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
          ads002_v2_scores: row.ads002_v2_scores ?? {},
          submitted: Boolean(row.intent_id),
        })),
        post_event: postRows[0]?.inputs ?? {},
        ads002: adsRows[0]?.inputs ?? {},
        ads002_v2: adsV2Rows[0]?.inputs ?? {},
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
