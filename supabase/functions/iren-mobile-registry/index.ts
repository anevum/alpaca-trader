import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 2,
  idle_timeout: 10,
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
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function authorized(req: Request): Promise<boolean> {
  const token = req.headers.get("x-anevum-ingest-token")?.trim();
  if (!token || token.length < 32) return false;
  const tokenHash = await sha256Hex(token);
  const rows = await sql.unsafe(
    "select token_id from private.trading_ingest_tokens where token_hash = $1 and active = true and revoked_at is null limit 1",
    [tokenHash],
  );
  return rows.length === 1;
}

function validHexToken(value: string): boolean {
  return value.length >= 32 && value.length <= 256 && /^[0-9a-f]+$/.test(value);
}

Deno.serve(async (req) => {
  if (!(await authorized(req))) return json(401, { ok: false, error: "unauthorized" });

  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "";

  try {
    if (req.method === "GET" && action === "list") {
      const rows = await sql.unsafe(
        "select activity_id, push_token, apns_environment, bundle_id, platform, surface, active, registered_at, updated_at, last_push_at, last_apns_status, last_apns_id, last_error from private.iren_live_activity_tokens where active = true order by updated_at desc limit 16"
      );
      return json(200, { ok: true, tokens: rows });
    }

    if (req.method !== "POST") {
      return json(405, { ok: false, error: "method_not_allowed" });
    }

    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch {
      return json(400, { ok: false, error: "invalid_json" });
    }

    if (action === "register") {
      const activityId = String(body.activity_id || "").trim();
      const pushToken = String(body.push_token || "").trim().toLowerCase();
      const environment = String(body.apns_environment || "production").trim().toLowerCase();
      const bundleId = String(body.bundle_id || "com.anevum.iren").trim();

      if (!activityId || activityId.length > 160 || !validHexToken(pushToken)) {
        return json(422, { ok: false, error: "invalid_registration" });
      }
      if (!["sandbox", "production"].includes(environment)) {
        return json(422, { ok: false, error: "invalid_apns_environment" });
      }

      await sql.unsafe(
        "insert into private.iren_live_activity_tokens (activity_id,push_token,apns_environment,bundle_id,platform,surface,active,registered_at,updated_at,last_error,last_apns_status,last_apns_id) values ($1,$2,$3,$4,'ios','rhen_live_activity',true,now(),now(),null,null,null) on conflict (activity_id) do update set push_token=excluded.push_token, apns_environment=excluded.apns_environment, bundle_id=excluded.bundle_id, active=true, updated_at=now(), last_error=null",
        [activityId, pushToken, environment, bundleId]
      );
      return json(200, { ok: true, registered: true, activity_id: activityId });
    }

    if (action === "deactivate") {
      const activityId = String(body.activity_id || "").trim();
      if (!activityId) return json(422, { ok: false, error: "activity_id_required" });
      await sql.unsafe(
        "update private.iren_live_activity_tokens set active=false,updated_at=now() where activity_id=$1",
        [activityId]
      );
      return json(200, { ok: true, deactivated: true });
    }

    if (action === "delivery") {
      const activityId = String(body.activity_id || "").trim();
      const event = String(body.event || "update").trim();
      const status = Number(body.apns_status || 0);
      const delivered = body.delivered === true;
      const apnsId = body.apns_id == null ? null : String(body.apns_id);
      const reason = body.response_reason == null ? null : String(body.response_reason).slice(0, 500);
      const fingerprint = body.payload_fingerprint == null ? null : String(body.payload_fingerprint).slice(0, 128);
      const durationMs = Number(body.duration_ms || 0);

      if (!activityId || !["update", "end"].includes(event)) {
        return json(422, { ok: false, error: "invalid_delivery" });
      }

      await sql.begin(async (tx) => {
        await tx.unsafe(
          "insert into private.iren_live_activity_deliveries (activity_id,event,payload_fingerprint,apns_environment,apns_status,apns_id,response_reason,delivered,duration_ms,completed_at) select $1,$2,$3,t.apns_environment,$4,$5,$6,$7,$8,now() from private.iren_live_activity_tokens t where t.activity_id=$1",
          [activityId,event,fingerprint,status,apnsId,reason,delivered,durationMs]
        );
        await tx.unsafe(
          "update private.iren_live_activity_tokens set last_push_at=now(),last_apns_status=$2,last_apns_id=$3,last_error=$4,active=case when $2 in (400,404,410) then false else active end,updated_at=now() where activity_id=$1",
          [activityId,status,apnsId,delivered ? null : reason]
        );
      });
      return json(200, { ok: true, recorded: true });
    }

    return json(400, { ok: false, error: "invalid_action" });
  } catch (error) {
    console.error("iren_mobile_registry_failed", error);
    return json(500, { ok: false, error: "registry_failed" });
  }
});
