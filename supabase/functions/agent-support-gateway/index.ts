import postgres from "npm:postgres@3.4.5";

// These are hashes of dedicated, independent support-only bearer tokens.
// The raw tokens are supplied only to the support service.
const READ_HASH = "f0330fbad9d266efd4f94171beb5d0734469252ae8b45757f6717b3821543572";
const WRITE_HASH = "1d9f8fdd0932b051cbbbd0ae382ba4c5a855a728b1c088b771f1a8fd67cf832f";
const connection = Deno.env.get("SUPABASE_DB_URL");
if (!connection) throw new Error("SUPABASE_DB_URL is not configured");
const sql = postgres(connection, { prepare: false, max: 1, connect_timeout: 10 });

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store",
      "x-content-type-options": "nosniff" },
  });
}

async function allowed(req: Request, expected: string): Promise<boolean> {
  const token = req.headers.get("x-rhen-support-token") ?? "";
  if (token.length < 64 || token.length > 128) return false;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  const hash = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, "0")).join("");
  return hash === expected;
}

function day(offset: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

async function boundedEvidence() {
  const start = day(-10), end = day(1);
  const [runtime, scan, telemetry, daily, weekly, period, earliest] = await Promise.all([
    sql`select service_id, service_name, deployment_id, git_commit, strategy_version_id
        from private.trading_runtime_instances order by started_at desc limit 1`,
    sql`select scan_cycle_id from private.trading_scan_cycles
        order by observed_at desc limit 1`,
    sql`select max(received_at) as latest_received_at
        from private.trading_events`,
    sql`select event_id::text, occurred_at,
          jsonb_build_object('session',payload->>'session',
            'strategy_version_id',payload->>'strategy_version_id',
            'generated_at',payload->>'generated_at') as payload
        from private.trading_events
        where event_type='research_daily_report'
          and occurred_at >= ${start}::date and occurred_at < ${end}::date
        order by occurred_at desc limit 12`,
    sql`select jsonb_build_object(
          'report_key',report_payload->>'report_key',
          'report_version',report_payload->>'report_version',
          'week_end',report_payload->>'week_end',
          'generated_at',report_payload->>'generated_at',
          'completeness_state',report_payload->>'completeness_state',
          'expected_trading_sessions',report_payload->'expected_trading_sessions',
          'included_trading_sessions',report_payload->'included_trading_sessions') as report
        from private.trading_weekly_reports
        order by period_end desc,generated_at desc limit 1`,
    sql`select private.rhen_weekly_report_inputs(${start}::date,${end}::date)
        as inputs`,
    sql`select min(nullif(payload->>'session','')::date)::text as session
        from private.trading_events where event_type='research_daily_report'`,
  ]);
  const inputs = period[0]?.inputs ?? {};
  return {
    evidence_version: "rhen-support-evidence-v1",
    generated_at: new Date().toISOString(),
    command_evidence: {
      evidence_version: "rhen-command-evidence-v1",
      provenance: { runtime: runtime[0] ?? null, latest_scan_cycle: scan[0] ?? null },
      telemetry_health: telemetry[0] ?? null,
      latest_weekly: weekly[0]?.report ?? null,
    },
    period_inputs: {
      strategy_versions: (inputs.strategy_versions ?? []).map((row: Record<string, unknown>) =>
        ({ version_id: row.version_id, strategy_name: row.strategy_name })),
      daily_reports: daily,
      earliest_daily_session: earliest[0]?.session ?? null,
      duplicate_checks: inputs.duplicate_checks ?? {},
    },
  };
}

async function openAlerts() {
  return await sql`select alert_key, reason_code, severity, source_component,
      evidence_reference, first_observed_at, last_observed_at
    from private.rhen_agent_support_alerts where state='OPEN'
    order by alert_key limit 200`;
}

const sources = new Set(["railway", "research_agent", "runtime", "telemetry",
  "preopen", "reports", "research", "context"]);
function validAction(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const key = row.alert_key;
  if (typeof key !== "string" || key.length < 3 || key.length > 500) return false;
  if (row.state === "RESOLVED") {
    return Object.keys(row).every(k => ["alert_key", "state", "resolved_at", "last_observed_at"].includes(k))
      && typeof row.resolved_at === "string" && !Number.isNaN(Date.parse(row.resolved_at));
  }
  if (row.state !== "OPEN") return false;
  const source = row.source_component, code = row.reason_code, ref = row.evidence_reference;
  return Object.keys(row).every(k => ["alert_key", "state", "reason_code", "severity",
    "source_component", "evidence_reference", "first_observed_at", "last_observed_at"].includes(k))
    && typeof source === "string" && sources.has(source)
    && typeof code === "string" && /^[A-Z][A-Z0-9_]{2,79}$/.test(code)
    && typeof ref === "string" && ref.length <= 300
    && key === `${source}:${code}:${ref}`
    && ["warning", "critical"].includes(String(row.severity))
    && typeof row.first_observed_at === "string"
    && typeof row.last_observed_at === "string"
    && !Number.isNaN(Date.parse(row.first_observed_at))
    && !Number.isNaN(Date.parse(row.last_observed_at))
    && Date.parse(row.first_observed_at) <= Date.parse(row.last_observed_at);
}

Deno.serve(async (req: Request) => {
  const view = new URL(req.url).searchParams.get("view");
  const read = req.method === "GET" && ["evidence", "alerts"].includes(view ?? "");
  const write = req.method === "POST" && view === "alerts";
  if (!read && !write) return response(405, { ok: false, error: "unsupported_operation" });
  if (!(await allowed(req, read ? READ_HASH : WRITE_HASH))) {
    return response(401, { ok: false, error: "unauthorized" });
  }
  try {
    if (read) return response(200, { ok: true,
      data: view === "evidence" ? await boundedEvidence() : await openAlerts() });
    if (Number(req.headers.get("content-length") || "0") > 40_000) {
      return response(413, { ok: false, error: "payload_too_large" });
    }
    const actions = await req.json();
    if (!Array.isArray(actions) || actions.length > 50 || !actions.every(validAction)) {
      return response(422, { ok: false, error: "invalid_actions" });
    }
    const result = await sql`select private.rhen_agent_support_apply_actions(
      ${sql.json(actions)}) as applied`;
    return response(200, { ok: true, applied: result[0]?.applied ?? 0 });
  } catch (error) {
    console.error("agent_support_gateway_failed", error instanceof Error ? error.message : "unknown");
    return response(503, { ok: false, error: "support_gateway_unavailable" });
  }
});
