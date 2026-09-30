import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 2,
  idle_timeout: 5,
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
  const tokenHash = await sha256Hex(token);
  const rows = await sql.unsafe<{ token_id: string }[]>(
    "select token_id from private.trading_ingest_tokens where token_hash=$1 and active=true and revoked_at is null limit 1",
    [tokenHash],
  );
  return rows.length === 1;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

async function rpc(name: "claim" | "complete", payload: Record<string, unknown>) {
  const functionName = name === "claim"
    ? "private.anevum_scheduler_claim"
    : "private.anevum_scheduler_complete";
  const rows = await sql.unsafe<{ result: Record<string, unknown> }[]>(
    "select " + functionName + "($1::jsonb) as result",
    [JSON.stringify(payload)],
  );
  if (!rows[0]?.result) throw new Error("scheduler_rpc_empty");
  return rows[0].result;
}

async function listRuns(url: URL) {
  const rawLimit = Number(url.searchParams.get("limit") || "100");
  const limit = Math.max(1, Math.min(250, Number.isFinite(rawLimit) ? rawLimit : 100));
  const workflow = url.searchParams.get("workflow_id")?.trim() || null;
  const query = workflow
    ? "select row_to_json(r) as run from (select run_id,job_key,workflow_id,workflow_version,scheduler_version,scheduled_at,started_at,completed_at,status,trigger_type,attempt,max_attempts,worker_identity,source_commit,input_identity,output_identity,error_classification,error_summary,retry_state,catchup_state,slack_notification_status,details,lease_until from private.anevum_scheduler_runs where workflow_id=$1 order by scheduled_at desc limit $2) r"
    : "select row_to_json(r) as run from (select run_id,job_key,workflow_id,workflow_version,scheduler_version,scheduled_at,started_at,completed_at,status,trigger_type,attempt,max_attempts,worker_identity,source_commit,input_identity,output_identity,error_classification,error_summary,retry_state,catchup_state,slack_notification_status,details,lease_until from private.anevum_scheduler_runs order by scheduled_at desc limit $1) r";
  const rows = workflow
    ? await sql.unsafe<{ run: Record<string, unknown> }[]>(query, [workflow, limit])
    : await sql.unsafe<{ run: Record<string, unknown> }[]>(query, [limit]);
  return rows.map((row) => row.run);
}

Deno.serve(async (req: Request) => {
  if (!(await authorized(req))) {
    return json(401, { ok: false, error: "unauthorized" });
  }

  try {
    if (req.method === "GET") {
      return json(200, {
        ok: true,
        service: "anevum-scheduler-gateway",
        runs: await listRuns(new URL(req.url)),
      });
    }
    if (req.method !== "POST") {
      return json(405, { ok: false, error: "method_not_allowed" });
    }

    const body = objectValue(await req.json());
    const action = String(body.action || "");
    if (action === "claim") {
      return json(200, {
        ok: true,
        ...(await rpc("claim", objectValue(body.job))),
      });
    }
    if (action === "complete") {
      return json(200, {
        ok: true,
        ...(await rpc("complete", body)),
      });
    }
    return json(400, { ok: false, error: "invalid_action" });
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown_error";
    console.error("scheduler_gateway_failed", message);
    return json(500, {
      ok: false,
      error: "scheduler_gateway_failed",
      classification: message,
    });
  }
});
