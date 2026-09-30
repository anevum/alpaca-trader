import postgres from "npm:postgres@3.4.5";
import { createHandler } from "./handler.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, {
  prepare: false, max: 1, idle_timeout: 5, connect_timeout: 5,
  connection: { statement_timeout: 5000 },
});
Deno.serve(createHandler({
  async authenticate(token) {
    const response = await fetch(Deno.env.get("SUPABASE_URL") + "/auth/v1/user", {
      headers: { authorization: "Bearer " + token, apikey: Deno.env.get("SUPABASE_ANON_KEY")! },
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401 || response.status === 403) return null;
    if (!response.ok) throw new Error("identity_unavailable");
    return response.json();
  },
  async read() {
    // A separate read path: no ingest token, scheduler mutation or RHEN dependency.
    const rows = await sql.begin("read only", async tx =>
      await tx`select revision, state, updated_at from private.iren_control_state where singleton`);
    return rows[0] as any || null;
  },
}));
