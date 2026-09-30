import { createHandler, project } from "./handler.ts";
const now = Date.parse("2026-09-30T15:00:00Z");
function assert(value: unknown) { if (!value) throw new Error("assertion_failed"); }
function fixture(stamp = "2026-09-30T15:00:00Z") {
  return { revision: 3, updated_at: stamp, state: { observed_at: stamp, state: "HEALTHY",
    topology: { services: [{ service_id: "RHEN", independent_runtime: true, status: "RUNNING" },
      { service_id: "NOSTRA", independent_runtime: false, status: "UNKNOWN" }], dependencies: { broker: { status: "RUNNING" } } },
    incidents: { lost: { status: "OPEN", severity: "warning", reason: "evidence_loss" } } } };
}
for (const [label, stamp] of [["missing", ""], ["stale", "2026-09-30T14:56:59Z"], ["future", "2026-09-30T15:01:00Z"], ["naive", "2026-09-30T15:00:00"], ["malformed", "bad"]]) {
  Deno.test(label + " observations cannot be healthy", () => {
    const row = fixture(stamp);
    const value = project(row, now);
    assert(value.stale && value.action_required && value.topology.services[0].status === "STALE");
    assert(row.state.topology.services[0].status === "RUNNING");
  });
}
Deno.test("fresh canonical state and module distinction", () => {
  const value = project(fixture(), now);
  assert(!value.stale && value.topology.services[1].status === "UNKNOWN" && value.incidents.length === 1);
});
for (const [label, token, user, status] of [
  ["missing token", "", null, 401], ["invalid token", "invalid", null, 401],
  ["nonadmin", "valid", { app_metadata: { role: "member" } }, 403],
  ["user metadata cannot authorize", "valid", { user_metadata: { command_admin: true } }, 403],
  ["administrator", "valid", { app_metadata: { command_admin: true } }, 200],
] as const) {
  Deno.test(label, async () => {
    let reads = 0;
    const handler = createHandler({ authenticate: async () => user as any, read: async () => { reads++; return fixture(); }, now: () => now });
    const response = await handler(new Request("http://test", { headers: token ? { authorization: "Bearer " + token } : {} }));
    assert(response.status === status && reads === (status === 200 ? 1 : 0));
    assert(response.headers.get("cache-control") === "private, no-store");
  });
}
Deno.test("database outage fails closed", async () => {
  const handler = createHandler({ authenticate: async () => ({ app_metadata: { role: "admin" } }),
    read: async () => { throw new Error("secret connection detail"); } });
  const response = await handler(new Request("http://test", { headers: { authorization: "Bearer valid" } }));
  const body = await response.text();
  assert(response.status === 503 && !body.includes("secret"));
});
Deno.test("no mutation verbs", async () => {
  const handler = createHandler({ authenticate: async () => { throw new Error("not called"); }, read: async () => null });
  assert((await handler(new Request("http://test", { method: "POST" }))).status === 405);
});
