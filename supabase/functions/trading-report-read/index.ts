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

  try {
    if (validDate(start) && validDate(end)) {
      if (start > end) return json(422, { ok: false, error: "invalid_period" });
      const rows = await sql<{ inputs: Record<string, unknown> }[]>`
        select private.rhen_weekly_report_inputs(${start}::date, ${end}::date) as inputs
      `;
      return json(200, {
        ok: true,
        report_version: "rhen-weekly-v1.1",
        inputs: rows[0]?.inputs ?? {},
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
        report_version: "rhen-weekly-v1.1",
        report: rows[0]?.report_payload ?? null,
      });
    }

    return json(400, { ok: false, error: "invalid_request" });
  } catch (error) {
    console.error("trading_report_read_failed", error);
    return json(500, { ok: false, error: "trading_report_read_failed" });
  }
});
