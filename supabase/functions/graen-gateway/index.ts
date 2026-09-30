import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, {
  prepare: false,
  max: 1,
  idle_timeout: 5,
  connect_timeout: 5,
  connection: { statement_timeout: 5000 },
});

const TOKEN_SHA256 = "c7f42e3a58b0b812ab6095a27a29d5c603f0f135d9fd1f7ecf0b9ac68c627e9e";

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

async function sha256Hex(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function authorized(req: Request) {
  const token = req.headers.get("x-graen-gateway-token")?.trim() || "";
  if (token.length < 32) return false;
  return (await sha256Hex(token)) === TOKEN_SHA256;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function safeStatus(value: unknown) {
  const status = String(value || "").trim().toUpperCase();
  const allowed = new Set(["QUEUED","RUNNING","WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"]);
  if (!allowed.has(status)) throw new Error("invalid_status");
  return status;
}

async function snapshot() {
  const problems = await sql`
    select problem_id,problem_key,title,statement,domain,status,priority,source,requested_by,
           linked_iren_job_id,constraints,success_criteria,metadata,created_at,updated_at,
           started_at,completed_at
    from private.graen_problems
    order by
      case status when 'RUNNING' then 0 when 'QUEUED' then 1 when 'WAITING' then 2 else 3 end,
      priority desc, created_at asc
    limit 200
  `;
  const runs = await sql`
    select run_id,problem_id,worker_id,runtime_version,source_commit,deployment_id,
           methodology_version,status,input_snapshot,result_summary,model_usage,
           started_at,completed_at,created_at
    from private.graen_runs
    order by started_at desc
    limit 100
  `;
  const artifacts = await sql`
    select artifact_id,problem_id,run_id,artifact_key,artifact_type,methodology_version,
           source_commit,content_hash,content,created_at
    from private.graen_artifacts
    order by created_at desc
    limit 100
  `;
  const state = await sql`
    select singleton,worker_id,runtime_version,source_commit,deployment_id,started_at,
           heartbeat_at,last_claim_at,last_completion_at,active_problem_id,queue_depth,
           last_error,metadata,updated_at
    from private.graen_runtime_state
    where singleton
  `;
  return { problems, runs, artifacts, runtime_state: state[0] || null };
}

Deno.serve(async (req: Request) => {
  if (!(await authorized(req))) return json(401, { ok: false, error: "unauthorized" });

  try {
    if (req.method === "GET") return json(200, { ok: true, ...(await snapshot()) });
    if (req.method !== "POST") return json(405, { ok: false, error: "method_not_allowed" });

    const body = await req.json();
    const action = String((body as Record<string, unknown>).action || "");

    if (action === "create_problem") {
      const title = String((body as any).title || "").trim().slice(0, 240);
      const statement = String((body as any).statement || "").trim().slice(0, 12000);
      const domain = String((body as any).domain || "GENERAL_RESEARCH").trim().slice(0, 80);
      const priority = Math.max(0, Math.min(100, Number((body as any).priority || 50)));
      const source = String((body as any).source || "IREN").trim().slice(0, 80);
      const requestedBy = String((body as any).requested_by || "").trim().slice(0, 160) || null;
      const linkedJob = String((body as any).linked_iren_job_id || "").trim() || null;
      const constraints = objectValue((body as any).constraints);
      const successCriteria = objectValue((body as any).success_criteria);
      const metadata = objectValue((body as any).metadata);
      if (!title || !statement) throw new Error("invalid_problem");
      const keyMaterial = [title, statement, domain, linkedJob || ""].join("\n");
      const problemKey = await sha256Hex(keyMaterial);
      const rows = await sql`
        insert into private.graen_problems (
          problem_key,title,statement,domain,status,priority,source,requested_by,
          linked_iren_job_id,constraints,success_criteria,metadata
        ) values (
          ${problemKey},${title},${statement},${domain},'QUEUED',${priority},${source},${requestedBy},
          ${linkedJob},${sql.json(constraints as any)}::jsonb,
          ${sql.json(successCriteria as any)}::jsonb,${sql.json(metadata as any)}::jsonb
        )
        on conflict (problem_key) do update set updated_at=now()
        returning *
      `;
      return json(201, { ok: true, problem: rows[0] });
    }

    if (action === "claim_problem") {
      const workerId = String((body as any).worker_id || "").trim().slice(0, 160);
      const runtimeVersion = String((body as any).runtime_version || "").trim().slice(0, 160);
      const sourceCommit = String((body as any).source_commit || "").trim().slice(0, 160) || null;
      const deploymentId = String((body as any).deployment_id || "").trim().slice(0, 160) || null;
      if (!workerId || !runtimeVersion) throw new Error("invalid_worker");
      const rows = await sql`
        with candidate as (
          select problem_id
          from private.graen_problems
          where status='QUEUED'
          order by priority desc, created_at asc
          limit 1
          for update skip locked
        )
        update private.graen_problems p
        set status='RUNNING', started_at=coalesce(started_at,now()), updated_at=now()
        from candidate c
        where p.problem_id=c.problem_id
        returning p.*
      `;
      const problem = rows[0] || null;
      await sql`
        update private.graen_runtime_state
        set worker_id=${workerId},runtime_version=${runtimeVersion},source_commit=${sourceCommit},
            deployment_id=${deploymentId},heartbeat_at=now(),last_claim_at=case when ${Boolean(problem)} then now() else last_claim_at end,
            active_problem_id=${problem ? problem.problem_id : null},
            queue_depth=(select count(*) from private.graen_problems where status='QUEUED'),
            last_error=null,updated_at=now()
        where singleton
      `;
      if (!problem) return json(200, { ok: true, problem: null });
      const runRows = await sql`
        insert into private.graen_runs (
          problem_id,worker_id,runtime_version,source_commit,deployment_id,status,input_snapshot
        ) values (
          ${problem.problem_id},${workerId},${runtimeVersion},${sourceCommit},${deploymentId},
          'RUNNING',${sql.json({ problem_key: problem.problem_key, domain: problem.domain } as any)}::jsonb
        )
        returning *
      `;
      return json(200, { ok: true, problem, run: runRows[0] });
    }

    if (action === "heartbeat") {
      const workerId = String((body as any).worker_id || "").trim().slice(0,160);
      const runtimeVersion = String((body as any).runtime_version || "").trim().slice(0,160);
      const sourceCommit = String((body as any).source_commit || "").trim().slice(0,160) || null;
      const deploymentId = String((body as any).deployment_id || "").trim().slice(0,160) || null;
      const activeProblem = String((body as any).active_problem_id || "").trim() || null;
      const lastError = String((body as any).last_error || "").trim().slice(0,1000) || null;
      await sql`
        update private.graen_runtime_state
        set worker_id=${workerId},runtime_version=${runtimeVersion},source_commit=${sourceCommit},
            deployment_id=${deploymentId},heartbeat_at=now(),active_problem_id=${activeProblem},
            queue_depth=(select count(*) from private.graen_problems where status='QUEUED'),
            last_error=${lastError},updated_at=now()
        where singleton
      `;
      return json(200, { ok: true });
    }

    if (action === "complete_problem") {
      const problemId = String((body as any).problem_id || "").trim();
      const runId = String((body as any).run_id || "").trim();
      const status = safeStatus((body as any).status);
      const resultSummary = objectValue((body as any).result_summary);
      const modelUsage = objectValue((body as any).model_usage);
      if (!problemId || !runId || !["WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"].includes(status))
        throw new Error("invalid_completion");
      await sql.begin(async (tx) => {
        await tx`
          update private.graen_runs
          set status=${status},result_summary=${sql.json(resultSummary as any)}::jsonb,
              model_usage=${sql.json(modelUsage as any)}::jsonb,
              completed_at=case when ${["SUCCEEDED","FAILED","CANCELLED"].includes(status)} then now() else completed_at end
          where run_id=${runId}::uuid and problem_id=${problemId}::uuid
        `;
        await tx`
          update private.graen_problems
          set status=${status},updated_at=now(),
              completed_at=case when ${["SUCCEEDED","FAILED","CANCELLED"].includes(status)} then now() else completed_at end
          where problem_id=${problemId}::uuid
        `;
        await tx`
          update private.graen_runtime_state
          set active_problem_id=null,last_completion_at=now(),heartbeat_at=now(),
              queue_depth=(select count(*) from private.graen_problems where status='QUEUED'),
              last_error=case when ${status}='FAILED' then ${String((resultSummary as any).error || "research_failed").slice(0,1000)} else null end,
              updated_at=now()
          where singleton
        `;
      });
      return json(200, { ok: true });
    }

    if (action === "record_artifact") {
      const problemId = String((body as any).problem_id || "").trim();
      const runId = String((body as any).run_id || "").trim() || null;
      const artifactType = String((body as any).artifact_type || "").trim().slice(0,100);
      const methodologyVersion = String((body as any).methodology_version || "").trim().slice(0,160) || null;
      const sourceCommit = String((body as any).source_commit || "").trim().slice(0,160) || null;
      const content = objectValue((body as any).content);
      if (!problemId || !artifactType) throw new Error("invalid_artifact");
      const canonical = JSON.stringify(content);
      const hash = await sha256Hex(canonical);
      const artifactKey = [problemId, runId || "none", artifactType, hash].join(":");
      const rows = await sql`
        insert into private.graen_artifacts (
          problem_id,run_id,artifact_key,artifact_type,methodology_version,source_commit,
          content_hash,content
        ) values (
          ${problemId}::uuid,${runId},${artifactKey},${artifactType},
          ${methodologyVersion},${sourceCommit},${hash},${sql.json(content as any)}::jsonb
        )
        on conflict (artifact_key) do nothing
        returning *
      `;
      return json(201, { ok: true, inserted: Boolean(rows[0]), artifact: rows[0] || null, content_hash: hash });
    }

    return json(400, { ok: false, error: "invalid_action" });
  } catch (error) {
    console.error("graen_gateway_failed", error instanceof Error ? error.message : "unknown");
    return json(500, { ok: false, error: "graen_gateway_failed" });
  }
});
