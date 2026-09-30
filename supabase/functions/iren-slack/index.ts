import postgres from "npm:postgres@3.4.5";

const db = Deno.env.get("SUPABASE_DB_URL");
if (!db) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(db, {
  prepare: false,
  max: 1,
  idle_timeout: 5,
  connect_timeout: 5,
  connection: { statement_timeout: 5000 },
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

function timingSafeEqual(a: string, b: string) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmacHex(secret: string, value: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function verifySlack(req: Request, body: string) {
  const secret = Deno.env.get("SLACK_SIGNING_SECRET")?.trim() || "";
  if (!secret) return { ok: false, status: 503, error: "slack_signing_secret_not_configured" };
  const timestamp = req.headers.get("x-slack-request-timestamp") || "";
  const signature = req.headers.get("x-slack-signature") || "";
  const seconds = Number(timestamp);
  if (!Number.isFinite(seconds) || Math.abs(Date.now() / 1000 - seconds) > 300) {
    return { ok: false, status: 401, error: "stale_request" };
  }
  const expected = "v0=" + await hmacHex(secret, "v0:" + timestamp + ":" + body);
  if (!timingSafeEqual(expected, signature)) {
    return { ok: false, status: 401, error: "invalid_signature" };
  }
  return { ok: true, status: 200, error: null };
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });

  const raw = await req.text();
  const verified = await verifySlack(req, raw);
  if (!verified.ok) return json(verified.status, { error: verified.error });

  const type = req.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const payload = JSON.parse(raw || "{}");
    if (payload.type === "url_verification" && typeof payload.challenge === "string") {
      return json(200, { challenge: payload.challenge });
    }
    return json(400, { error: "unsupported_slack_event" });
  }

  const form = new URLSearchParams(raw);
  const expectedTeam = Deno.env.get("SLACK_TEAM_ID")?.trim() || "";
  const teamId = form.get("team_id") || "";
  if (expectedTeam && teamId !== expectedTeam) return json(403, { error: "workspace_mismatch" });

  const userId = form.get("user_id") || "";
  const channelId = form.get("channel_id") || "";
  const responseUrl = form.get("response_url") || "";
  const slashCommand = form.get("command") || "/iren";
  const commandText = (form.get("text") || "").trim() || "status";

  if (!userId || !channelId || !responseUrl.startsWith("https://hooks.slack.com/commands/")) {
    return json(400, { error: "invalid_slack_command" });
  }

  const context = {
    team_id: teamId,
    channel_id: channelId,
    user_id: userId,
    response_url: responseUrl,
    slash_command: slashCommand,
  };

  const rows = await sql`
    insert into private.iren_commands (command_text, source, requested_by, context)
    values (${commandText}, 'slack', ${"slack:" + userId}, ${sql.json(context)}::jsonb)
    returning command_id, status, created_at
  `;

  return json(200, {
    response_type: "ephemeral",
    text: "IREN accepted: " + commandText,
    command_id: rows[0].command_id,
  });
});
