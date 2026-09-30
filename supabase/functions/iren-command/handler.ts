type Row = { revision: unknown; state: Record<string, any>; updated_at: unknown };
type Dependencies = {
  authenticate: (token: string) => Promise<{ app_metadata?: Record<string, unknown> } | null>;
  read: () => Promise<Row | null>;
  now?: () => number;
};
const fresh = (stamp: unknown, now: number) => {
  if (typeof stamp !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(stamp)) return false;
  const age = now - Date.parse(stamp);
  return Number.isFinite(age) && age >= 0 && age <= 180000;
};
export function project(row: Row | null, now: number) {
  const state = structuredClone(row?.state || {});
  const stale = !fresh(state.observed_at, now);
  const topology = state.topology;
  if (stale) {
    state.state = "STALE";
    for (const item of topology?.services || []) {
      if (item.independent_runtime) Object.assign(item, { status: "STALE", readiness: false, liveness: null });
    }
    for (const dependency of Object.values(topology?.dependencies || {}) as any[]) dependency.status = "STALE";
  }
  const incidents = Object.entries(state.incidents || {}).filter(([, value]: any) => value.status === "OPEN")
    .map(([key, value]: any) => ({ key, severity: value.severity, reason: value.reason, opened_at: value.opened_at }));
  return { schema_version: "iren_command.v1", revision: row?.revision ?? null,
    observed_at: state.observed_at ?? null, stale, state: state.state || "UNKNOWN",
    topology: topology || null, incidents, scheduler: state.scheduler || null,
    action_required: stale || state.state !== "HEALTHY" || incidents.length > 0,
    configuration_identity: state.configuration_baseline?.fingerprint || null };
}
function response(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: {
    "content-type": "application/json", "cache-control": "private, no-store",
    "x-content-type-options": "nosniff" } });
}
export function createHandler(deps: Dependencies) {
  return async (req: Request) => {
    if (req.method !== "GET") return response(405, { error: "method_not_allowed" });
    const auth = req.headers.get("authorization") || "";
    if (!auth.startsWith("Bearer ") || !auth.slice(7).trim()) return response(401, { error: "unauthorized" });
    try {
      const user = await deps.authenticate(auth.slice(7).trim());
      if (!user) return response(401, { error: "unauthorized" });
      const meta = user.app_metadata || {};
      if (meta.command_admin !== true && !["owner", "founder", "admin", "command_admin"].includes(String(meta.role || "").trim().toLowerCase()))
        return response(403, { error: "forbidden" });
      return response(200, project(await deps.read(), (deps.now || Date.now)()));
    } catch {
      return response(503, { error: "operational_state_unavailable", stale: true, action_required: true });
    }
  };
}
