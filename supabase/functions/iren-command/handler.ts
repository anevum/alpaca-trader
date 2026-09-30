type Row = { revision: unknown; state: Record<string, any>; updated_at: unknown };
type User = {
  id?: string;
  email?: string;
  app_metadata?: Record<string, unknown>;
  user_metadata?: Record<string, unknown>;
};
type WorkSnapshot = {
  objectives: Record<string, unknown>[];
  jobs: Record<string, unknown>[];
  commands: Record<string, unknown>[];
};
type Dependencies = {
  authenticate: (token: string) => Promise<User | null>;
  read: () => Promise<Row | null>;
  readWork: () => Promise<WorkSnapshot>;
  writeCommand: (command: string, requestedBy: string) => Promise<Record<string, unknown>>;
  now?: () => number;
};

const fresh = (stamp: unknown, now: number) => {
  if (typeof stamp !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(stamp)) return false;
  const age = now - Date.parse(stamp);
  return Number.isFinite(age) && age >= 0 && age <= 180000;
};

function summary(work: WorkSnapshot) {
  const objectives = work.objectives || [];
  const jobs = work.jobs || [];
  const activeJobs = jobs.filter((row: any) =>
    ["QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"].includes(String(row.status || ""))
  );
  const blockedObjectives = objectives.filter((row: any) => row.status === "BLOCKED");
  const decisions = jobs.filter((row: any) => row.status === "NEEDS_APPROVAL" || row.requires_human === true);
  const complete = objectives.filter((row: any) => row.status === "COMPLETE").length;
  return {
    objective_count: objectives.length,
    objectives_complete: complete,
    active_jobs: activeJobs.length,
    blocked_objectives: blockedObjectives.length,
    requires_human: decisions.length,
  };
}

export function project(row: Row | null, work: WorkSnapshot, now: number) {
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
  return {
    schema_version: "iren_command.v2",
    revision: row?.revision ?? null,
    observed_at: state.observed_at ?? null,
    stale,
    state: state.state || "UNKNOWN",
    topology: topology || null,
    incidents,
    scheduler: state.scheduler || null,
    action_required: stale || state.state !== "HEALTHY" || incidents.length > 0,
    configuration_identity: state.configuration_baseline?.fingerprint || null,
    work: {
      ...summary(work),
      objectives: work.objectives || [],
      jobs: work.jobs || [],
      commands: work.commands || [],
    },
  };
}

function response(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "private, no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function isAdmin(user: User | null) {
  if (!user) return false;
  const meta = user.app_metadata || {};
  return meta.command_admin === true ||
    ["owner", "founder", "admin", "command_admin"].includes(String(meta.role || "").trim().toLowerCase());
}

export function createHandler(deps: Dependencies) {
  return async (req: Request) => {
    if (!["GET", "POST"].includes(req.method)) return response(405, { error: "method_not_allowed" });
    const auth = req.headers.get("authorization") || "";
    if (!auth.startsWith("Bearer ") || !auth.slice(7).trim()) return response(401, { error: "unauthorized" });
    try {
      const user = await deps.authenticate(auth.slice(7).trim());
      if (!user) return response(401, { error: "unauthorized" });
      if (!isAdmin(user)) return response(403, { error: "forbidden" });

      if (req.method === "POST") {
        const body = await req.json().catch(() => ({}));
        const command = String((body as Record<string, unknown>).command || "").trim().slice(0, 4000);
        if (!command) return response(400, { error: "command_required" });
        const requestedBy = String(user.email || user.id || "command-admin").slice(0, 160);
        const created = await deps.writeCommand(command, requestedBy);
        return response(202, {
          schema_version: "iren_command.v2",
          accepted: true,
          command: created,
        });
      }

      const [state, work] = await Promise.all([deps.read(), deps.readWork()]);
      return response(200, project(state, work, (deps.now || Date.now)()));
    } catch {
      return response(503, { error: "operational_state_unavailable", stale: true, action_required: true });
    }
  };
}
