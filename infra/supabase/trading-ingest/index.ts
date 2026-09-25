import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 1,
  idle_timeout: 20,
  connect_timeout: 10,
});

const MAX_EVENTS = 100;
const MAX_BODY_BYTES = 256_000;

type EventInput = {
  event_key: string;
  run_id?: string | null;
  strategy_version_id?: string | null;
  event_type: string;
  occurred_at: string;
  symbol?: string | null;
  correlation_id?: string | null;
  source?: string | null;
  payload?: Record<string, unknown> | null;
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
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

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function eventValidationError(event: unknown): string | null {
  if (!event || typeof event !== "object") return "event must be an object";
  const x = event as Record<string, unknown>;
  if (typeof x.event_key !== "string" || x.event_key.length < 8 || x.event_key.length > 200) {
    return "event_key must be a string between 8 and 200 characters";
  }
  if (typeof x.event_type !== "string" || x.event_type.length < 1 || x.event_type.length > 80) {
    return "event_type must be a string between 1 and 80 characters";
  }
  if (typeof x.occurred_at !== "string" || Number.isNaN(Date.parse(x.occurred_at))) {
    return "occurred_at must be a valid timestamp";
  }
  if (x.run_id != null && (typeof x.run_id !== "string" || !UUID_RE.test(x.run_id))) {
    return "run_id must be a UUID when present";
  }
  if (x.strategy_version_id != null && (typeof x.strategy_version_id !== "string" || x.strategy_version_id.length > 160)) {
    return "strategy_version_id must be a string up to 160 characters";
  }
  if (x.symbol != null && (typeof x.symbol !== "string" || x.symbol.length > 16)) {
    return "symbol must be a string up to 16 characters";
  }
  if (x.correlation_id != null && (typeof x.correlation_id !== "string" || x.correlation_id.length > 200)) {
    return "correlation_id must be a string up to 200 characters";
  }
  if (x.source != null && (typeof x.source !== "string" || x.source.length > 80)) {
    return "source must be a string up to 80 characters";
  }
  if (x.payload != null && (typeof x.payload !== "object" || Array.isArray(x.payload))) {
    return "payload must be an object";
  }
  return null;
}

Deno.serve(async (req) => {
  if (req.method === "GET") return json(200, { ok: true, service: "anevum-trading-ingest" });
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });

  const contentLength = Number(req.headers.get("content-length") || "0");
  if (contentLength > MAX_BODY_BYTES) return json(413, { error: "payload_too_large" });

  const token = req.headers.get("x-anevum-ingest-token")?.trim();
  if (!token || token.length < 32) return json(401, { error: "unauthorized" });

  const tokenHash = await sha256Hex(token);
  const authorized = await sql<{ token_id: string }[]>`
    select token_id
    from private.trading_ingest_tokens
    where token_hash = ${tokenHash}
      and active = true
      and revoked_at is null
    limit 1
  `;
  if (authorized.length !== 1) return json(401, { error: "unauthorized" });

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "invalid_json" });
  }

  const events = Array.isArray((body as any)?.events)
    ? (body as any).events
    : [(body as any)?.event ?? body];

  if (!events.length) {
    return json(400, { error: "invalid_events", reason: "empty_batch" });
  }
  if (events.length > MAX_EVENTS) {
    return json(400, {
      error: "invalid_events",
      reason: "too_many_events",
      received: events.length,
      max_events: MAX_EVENTS,
    });
  }
  for (let index = 0; index < events.length; index += 1) {
    const reason = eventValidationError(events[index]);
    if (reason) {
      const event = events[index] as Record<string, unknown> | undefined;
      return json(400, {
        error: "invalid_event",
        index,
        event_key: event?.event_key ?? null,
        event_type: event?.event_type ?? null,
        reason,
      });
    }
  }

  // Bind every event to the canonical run identity. This prevents a live
  // runtime from silently writing broker activity into a shadow strategy.
  const checkedPairs = new Set<string>();
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index] as EventInput;
    if (!event.run_id) continue;
    const pair = `${event.run_id}:${event.strategy_version_id ?? ""}`;
    if (checkedPairs.has(pair)) continue;
    checkedPairs.add(pair);

    const runs = await sql<{
      run_id: string;
      strategy_version_id: string;
      environment: string;
    }[]>`
      select run_id::text, strategy_version_id, environment
      from private.trading_runs
      where run_id = ${event.run_id}::uuid
      limit 1
    `;
    if (
      runs.length !== 1
      || runs[0].strategy_version_id !== (event.strategy_version_id ?? "")
    ) {
      return json(409, {
        error: "run_identity_mismatch",
        index,
        event_key: event.event_key,
        run_id: event.run_id,
        strategy_version_id: event.strategy_version_id ?? null,
      });
    }
    if (
      runs[0].environment === "live"
      && !(event.strategy_version_id ?? "").toUpperCase().startsWith("LIVE-")
    ) {
      return json(409, {
        error: "live_strategy_identity_invalid",
        index,
        event_key: event.event_key,
        run_id: event.run_id,
        strategy_version_id: event.strategy_version_id ?? null,
      });
    }
  }

  let inserted = 0;
  try {
    await sql.begin(async (tx) => {
      for (const event of events as EventInput[]) {
        const rows = await tx<{ event_id: string }[]>`
          insert into private.trading_events (
            event_key,
            run_id,
            strategy_version_id,
            event_type,
            occurred_at,
            symbol,
            correlation_id,
            source,
            payload
          )
          values (
            ${event.event_key},
            ${event.run_id ?? null},
            ${event.strategy_version_id ?? null},
            ${event.event_type},
            ${event.occurred_at},
            ${event.symbol ?? null},
            ${event.correlation_id ?? null},
            ${event.source ?? "alpaca-trader"},
            ${tx.json(event.payload ?? {})}
          )
          on conflict (event_key) do nothing
          returning event_id
        `;
        inserted += rows.length;
        if (rows.length > 0) {
          await tx`
            select private.project_trading_event(
              ${event.event_type}::text,
              ${event.run_id ?? null}::uuid,
              ${event.strategy_version_id ?? null}::text,
              ${event.occurred_at}::timestamptz,
              ${event.symbol ?? null}::text,
              ${tx.json(event.payload ?? {})}::jsonb
            )
          `;
        }
      }

      await tx`
        update private.trading_ingest_tokens
        set last_used_at = now()
        where token_id = ${authorized[0].token_id}
      `;
    });
  } catch (error) {
    console.error("ingest_failed", error);
    return json(500, { error: "ingest_failed" });
  }

  return json(202, { ok: true, received: events.length, inserted });
});
