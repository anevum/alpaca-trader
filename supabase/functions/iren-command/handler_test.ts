import { createHandler, project } from "./handler.ts";

const now = Date.parse("2026-09-30T15:00:00Z");
function assert(value: unknown) { if (!value) throw new Error("assertion_failed"); }

function fixture(stamp = "2026-09-30T15:00:00Z") {
  return {
    revision: 3,
    updated_at: stamp,
    state: {
      observed_at: stamp,
      state: "HEALTHY",
      topology: {
        services: [
          { service_id: "RHEN", independent_runtime: true, status: "RUNNING" },
          { service_id: "NOSTRA", independent_runtime: false, status: "UNKNOWN" },
        ],
        dependencies: { broker: { status: "RUNNING" } },
      },
      incidents: { lost: { status: "OPEN", severity: "warning", reason: "evidence_loss" } },
    },
  };
}

function work() {
  return {
    objectives: [
      { objective_key: "A", status: "COMPLETE" },
      { objective_key: "B", status: "READY" },
    ],
    jobs: [{ job_id: "1", status: "WAITING", requires_human: false }],
    commands: [{ command_id: "2", status: "SUCCEEDED" }],
  };
}

for (const [label, stamp] of [
  ["missing", ""],
  ["stale", "2026-09-30T14:56:59Z"],
  ["future", "2026-09-30T15:01:00Z"],
  ["naive", "2026-09-30T15:00:00"],
  ["malformed", "bad"],
]) {
  Deno.test(label + " observations cannot be healthy", () => {
    const row = fixture(stamp);
    const value = project(row, work(), now);
    assert(value.stale && value.action_required && value.topology.services[0].status === "STALE");
    assert(row.state.topology.services[0].status === "RUNNING");
  });
}

Deno.test("fresh canonical state includes durable work summary", () => {
  const value = project(fixture(), work(), now);
  assert(!value.stale);
  assert(value.schema_version === "iren_command.v1");
  assert(value.work_schema_version === "iren_work.v1");
  assert(value.work.objective_count === 2);
  assert(value.work.objectives_complete === 1);
  assert(value.work.active_jobs === 1);
});

for (const [label, token, user, status] of [
  ["missing token", "", null, 401],
  ["invalid token", "invalid", null, 401],
  ["nonadmin", "valid", { app_metadata: { role: "member" } }, 403],
  ["user metadata cannot authorize", "valid", { user_metadata: { command_admin: true } }, 403],
  ["administrator", "valid", { app_metadata: { command_admin: true } }, 200],
] as const) {
  Deno.test(label, async () => {
    let reads = 0;
    const handler = createHandler({
      authenticate: async () => user as any,
      read: async () => { reads++; return fixture(); },
      readWork: async () => work(),
      writeCommand: async () => ({}),
      now: () => now,
    });
    const response = await handler(new Request("http://test", {
      headers: token ? { authorization: "Bearer " + token } : {},
    }));
    assert(response.status === status && reads === (status === 200 ? 1 : 0));
    assert(response.headers.get("cache-control") === "private, no-store");
  });
}

Deno.test("administrator can submit command", async () => {
  let captured = "";
  const handler = createHandler({
    authenticate: async () => ({ id: "u1", email: "operator@example.com", app_metadata: { role: "admin" } }),
    read: async () => fixture(),
    readWork: async () => work(),
    writeCommand: async (command, requestedBy) => {
      captured = command + "|" + requestedBy;
      return { command_id: "c1", status: "QUEUED" };
    },
  });
  const response = await handler(new Request("http://test", {
    method: "POST",
    headers: { authorization: "Bearer valid", "content-type": "application/json" },
    body: JSON.stringify({ command: "what's next?" }),
  }));
  const body = await response.json();
  assert(response.status === 202);
  assert(body.accepted === true);
  assert(captured === "what's next?|operator@example.com");
});

Deno.test("empty command rejected", async () => {
  const handler = createHandler({
    authenticate: async () => ({ app_metadata: { role: "admin" } }),
    read: async () => fixture(),
    readWork: async () => work(),
    writeCommand: async () => ({}),
  });
  const response = await handler(new Request("http://test", {
    method: "POST",
    headers: { authorization: "Bearer valid", "content-type": "application/json" },
    body: JSON.stringify({ command: "   " }),
  }));
  assert(response.status === 400);
});

Deno.test("database outage fails closed", async () => {
  const handler = createHandler({
    authenticate: async () => ({ app_metadata: { role: "admin" } }),
    read: async () => { throw new Error("secret connection detail"); },
    readWork: async () => work(),
    writeCommand: async () => ({}),
  });
  const response = await handler(new Request("http://test", {
    headers: { authorization: "Bearer valid" },
  }));
  const body = await response.text();
  assert(response.status === 503 && !body.includes("secret"));
});

Deno.test("unsupported verbs rejected", async () => {
  const handler = createHandler({
    authenticate: async () => { throw new Error("not called"); },
    read: async () => null,
    readWork: async () => work(),
    writeCommand: async () => ({}),
  });
  assert((await handler(new Request("http://test", { method: "DELETE" }))).status === 405);
});
