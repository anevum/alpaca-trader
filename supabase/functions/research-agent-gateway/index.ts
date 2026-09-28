import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 1,
  idle_timeout: 20,
  connect_timeout: 10,
});

const GATEWAY_TOKEN_SHA256 =
  "4b0d6d48e9647a8b4e3dc6d18433d1164f32cc14289bf3eef2880c1eefcfb787";
const MAX_BODY_BYTES = 512_000;

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
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function authorized(req: Request): Promise<boolean> {
  const token = req.headers.get("x-anevum-ingest-token")?.trim();
  if (!token || token.length < 32) return false;
  return (await sha256Hex(token)) === GATEWAY_TOKEN_SHA256;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function containsForbiddenReasoning(value: unknown): boolean {
  const forbidden = new Set([
    "chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_trace",
  ]);
  if (Array.isArray(value)) return value.some(containsForbiddenReasoning);
  if (!value || typeof value !== "object") return false;
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (forbidden.has(key.trim().toLowerCase())) return true;
    if (containsForbiddenReasoning(item)) return true;
  }
  return false;
}

async function readEvidence() {
  const query = [
    "select jsonb_build_object(",
    "  'current_strategy', (",
    "    select to_jsonb(s)",
    "    from private.trading_strategy_versions s",
    "    where s.status = 'active'",
    "    order by s.activated_at desc nulls last, s.created_at desc",
    "    limit 1",
    "  ),",
    "  'latest_daily_report', (",
    "    select e.payload",
    "    from private.trading_events e",
    "    where e.event_type = 'research_daily_report'",
    "    order by coalesce(nullif(e.payload->>'generated_at','')::timestamptz, e.occurred_at) desc, e.received_at desc",
    "    limit 1",
    "  ),",
    "  'latest_weekly_report', (",
    "    select w.report_payload",
    "    from private.trading_weekly_reports w",
    "    order by w.period_end desc, w.generated_at desc, w.created_at desc",
    "    limit 1",
    "  ),",
    "  'research_questions', (",
    "    select coalesce(jsonb_agg(to_jsonb(q) order by q.created_at desc), '[]'::jsonb)",
    "    from (",
    "      select distinct on (research_question_id) *",
    "      from private.trading_research_questions",
    "      order by research_question_id, created_at desc",
    "    ) q",
    "  ),",
    "  'experiments', (",
    "    select coalesce(jsonb_agg(to_jsonb(e) order by e.created_at desc), '[]'::jsonb)",
    "    from private.trading_experiments e",
    "  ),",
    "  'research_decisions', (",
    "    select coalesce(jsonb_agg(to_jsonb(d) order by d.decided_at desc), '[]'::jsonb)",
    "    from private.trading_research_decisions d",
    "    where d.status in ('final','superseded')",
    "  ),",
    "  'agent_runs', (",
    "    select coalesce(jsonb_agg(to_jsonb(a) order by a.started_at desc), '[]'::jsonb)",
    "    from (",
    "      select *",
    "      from private.trading_research_agent_runs",
    "      order by started_at desc",
    "      limit 50",
    "    ) a",
    "  ),",
    "  'search_ledger', jsonb_build_object(",
    "    'exposure', (",
    "      select to_jsonb(x)",
    "      from private.rhen_research_search_exposure_v1 x",
    "    ),",
    "    'recent_hypotheses', (",
    "      select coalesce(jsonb_agg(to_jsonb(h) order by h.created_at desc), '[]'::jsonb)",
    "      from (",
    "        select hypothesis_id, hypothesis_key, definition_hash, family_id,",
    "               parent_hypothesis_id, hypothesis_kind, origin_kind,",
    "               search_generation, research_question_id, statement,",
    "               proposal_id, proposal_revision, created_at",
    "        from private.trading_research_hypotheses",
    "        order by created_at desc, hypothesis_id",
    "        limit 75",
    "      ) h",
    "    ),",
    "    'recent_events', (",
    "      select coalesce(jsonb_agg(to_jsonb(se) order by se.event_at desc, se.search_event_sequence desc), '[]'::jsonb)",
    "      from (",
    "        select search_event_sequence, search_event_id, event_key, family_id,",
    "               hypothesis_id, event_type, research_stage, source_kind,",
    "               event_at, prior_hypotheses_examined, data_contaminating,",
    "               corpus_id, global_filtration_id",
    "        from private.trading_research_search_events",
    "        order by event_at desc, search_event_sequence desc",
    "        limit 100",
    "      ) se",
    "    ),",
    "    'multiplicity_state', (",
    "      select to_jsonb(ms)",
    "      from private.rhen_research_multiplicity_state_v1 ms",
    "    ),",
    "    'recent_multiplicity_plans', (",
    "      select coalesce(jsonb_agg(to_jsonb(mp) order by mp.created_at desc, mp.plan_id), '[]'::jsonb)",
    "      from (",
    "        select plan_id, plan_hash, plan_version, proposal_id, proposal_revision,",
    "               proposal_hash, family_id, method, alpha, family_scope, family_size,",
    "               input_type, error_metric, dependence_scope, search_generation,",
    "               policy_status, production_authority, protected_stage_authority,",
    "               created_at",
    "        from private.trading_research_multiplicity_plans",
    "        order by created_at desc, plan_id",
    "        limit 50",
    "      ) mp",
    "    )",
    "  )",
    ") as evidence",
  ].join("\n");

  const rows = await sql.unsafe<{ evidence: Record<string, unknown> }[]>(query);
  const evidence = rows[0]?.evidence;
  if (!evidence || typeof evidence !== "object") {
    throw new Error("canonical_evidence_missing");
  }
  return evidence;
}

async function recordRun(run: Record<string, unknown>) {
  const required = [
    "run_id",
    "run_key",
    "agent_version",
    "source_commit",
    "trigger",
    "started_at",
    "completed_at",
    "input_fingerprint",
    "status",
  ];
  for (const key of required) {
    if (typeof run[key] !== "string" || !(run[key] as string).trim()) {
      throw new Error("invalid_run_record");
    }
  }
  if (!["COMPLETED", "NOOP", "BLOCKED", "FAILED"].includes(String(run.status))) {
    throw new Error("invalid_run_status");
  }
  if (containsForbiddenReasoning(run)) {
    throw new Error("hidden_reasoning_forbidden");
  }

  const query = [
    "insert into private.trading_research_agent_runs (",
    "  run_id, run_key, agent_version, source_commit, trigger, trigger_reference,",
    "  started_at, completed_at, evidence_cutoff, input_artifacts,",
    "  input_fingerprint, proposed_actions, actions_taken, tools_invoked,",
    "  output_artifact, approval_required, authorization_reference, status,",
    "  error_summary, rationale_summary, llm_usage, operator_identity",
    ") values (",
    "  $1::uuid, $2, $3, $4, $5, $6,",
    "  $7::timestamptz, $8::timestamptz, $9::timestamptz, (($10::jsonb #>> '{}')::jsonb),",
    "  $11, (($12::jsonb #>> '{}')::jsonb), (($13::jsonb #>> '{}')::jsonb), (($14::jsonb #>> '{}')::jsonb),",
    "  (($15::jsonb #>> '{}')::jsonb), $16::boolean, $17, $18,",
    "  (($19::jsonb #>> '{}')::jsonb), (($20::jsonb #>> '{}')::jsonb), (($21::jsonb #>> '{}')::jsonb), $22",
    ")",
    "on conflict (run_key) do nothing",
    "returning run_id::text, run_key",
  ].join("\n");

  const parameters = [
    String(run.run_id),
    String(run.run_key),
    String(run.agent_version),
    String(run.source_commit),
    String(run.trigger),
    run.trigger_reference == null ? null : String(run.trigger_reference),
    String(run.started_at),
    String(run.completed_at),
    run.evidence_cutoff == null ? null : String(run.evidence_cutoff),
    JSON.stringify(arrayValue(run.input_artifacts)),
    String(run.input_fingerprint),
    JSON.stringify(arrayValue(run.proposed_actions)),
    JSON.stringify(arrayValue(run.actions_taken)),
    JSON.stringify(arrayValue(run.tools_invoked)),
    run.output_artifact == null ? null : JSON.stringify(run.output_artifact),
    Boolean(run.approval_required),
    run.authorization_reference == null ? null : String(run.authorization_reference),
    String(run.status),
    run.error_summary == null ? null : JSON.stringify(objectValue(run.error_summary)),
    run.rationale_summary == null ? null : JSON.stringify(objectValue(run.rationale_summary)),
    JSON.stringify(objectValue(run.llm_usage)),
    run.operator_identity == null ? null : String(run.operator_identity),
  ];

  const rows = await sql.unsafe<{ run_id: string; run_key: string }[]>(
    query,
    parameters,
  );
  return rows.length === 1
    ? { inserted: true, duplicate: false, run_id: rows[0].run_id }
    : { inserted: false, duplicate: true };
}


async function recordSearchLedger(ledger: Record<string, unknown>) {
  if (containsForbiddenReasoning(ledger)) {
    throw new Error("hidden_reasoning_forbidden");
  }
  if (ledger.ledger_version !== "math001-search-ledger-v1") {
    throw new Error("invalid_search_ledger_version");
  }
  const rows = await sql.unsafe<{ result: Record<string, unknown> }[]>(
    "select private.rhen_research_record_search_ledger($1::jsonb) as result",
    [JSON.stringify(ledger)],
  );
  const result = rows[0]?.result;
  if (!result || typeof result !== "object") {
    throw new Error("search_ledger_write_failed");
  }

  const planRows = await sql.unsafe<{ result: Record<string, unknown> }[]>(
    "select private.rhen_research_record_multiplicity_plan($1::jsonb) as result",
    [JSON.stringify(ledger)],
  );
  const multiplicity = planRows[0]?.result;
  if (!multiplicity || typeof multiplicity !== "object") {
    throw new Error("multiplicity_plan_write_failed");
  }

  return {
    ...result,
    multiplicity_plan: multiplicity,
  };
}

Deno.serve(async (req: Request) => {
  if (!(await authorized(req))) {
    return json(401, { ok: false, error: "unauthorized" });
  }

  try {
    if (req.method === "GET") {
      return json(200, { ok: true, evidence: await readEvidence() });
    }
    if (req.method !== "POST") {
      return json(405, { ok: false, error: "method_not_allowed" });
    }

    const contentLength = Number(req.headers.get("content-length") || "0");
    if (contentLength > MAX_BODY_BYTES) {
      return json(413, { ok: false, error: "payload_too_large" });
    }

    const body = await req.json();
    if (!body || typeof body !== "object") {
      return json(400, { ok: false, error: "invalid_json" });
    }
    const action = String((body as Record<string, unknown>).action || "");
    if (!["record_run", "record_run_and_search_ledger"].includes(action)) {
      return json(400, { ok: false, error: "invalid_action" });
    }
    const run = (body as Record<string, unknown>).run;
    if (!run || typeof run !== "object" || Array.isArray(run)) {
      return json(400, { ok: false, error: "invalid_run_record" });
    }

    const result = await recordRun(run as Record<string, unknown>);
    if (action === "record_run") {
      if (result.duplicate) {
        return json(409, { ok: false, error: "duplicate_run_key" });
      }
      return json(201, { ok: true, ...result });
    }

    const ledger = (body as Record<string, unknown>).search_ledger;
    if (!ledger || typeof ledger !== "object" || Array.isArray(ledger)) {
      return json(400, { ok: false, error: "invalid_search_ledger" });
    }
    const ledgerResult = await recordSearchLedger(
      ledger as Record<string, unknown>,
    );
    return json(201, {
      ok: true,
      ...result,
      search_ledger_recorded: true,
      search_ledger: ledgerResult,
    });
  } catch (error) {
    console.error(
      "research_agent_gateway_failed",
      error instanceof Error ? error.message : "unknown_error",
    );
    return json(500, { ok: false, error: "research_agent_gateway_failed" });
  }
});
