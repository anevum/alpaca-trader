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

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "";

  if (req.method === "GET" && action === "list") {
    return json(200, {
      ok: true,
      tokens: [],
      disabled: true,
      reason: "mobile_live_activity_surface_obsolete",
    });
  }

  if (req.method === "POST" && ["register", "deactivate", "delivery"].includes(action)) {
    return json(410, {
      ok: false,
      disabled: true,
      error: "mobile_live_activity_surface_obsolete",
    });
  }

  if (!["GET", "POST"].includes(req.method)) {
    return json(405, { ok: false, error: "method_not_allowed" });
  }

  return json(400, {
    ok: false,
    disabled: true,
    error: "mobile_live_activity_surface_obsolete",
  });
});
