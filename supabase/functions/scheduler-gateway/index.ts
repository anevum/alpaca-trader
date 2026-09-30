import postgres from "npm:postgres@3.4.5";

const connectionString = Deno.env.get("SUPABASE_DB_URL");
if (!connectionString) throw new Error("SUPABASE_DB_URL is not configured");

const sql = postgres(connectionString, {
  prepare: false,
  max: 2,
  idle_timeout: 5,
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
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function authorized(req: Request): Promise<boolean> {
  const token = req.headers.get("x-anevum-ingest-token")?.trim();
  if (!token || token.length < 32) return false;
  const tokenHash = await sha256Hex(token);
  const rows = await sql.unsafe<{ token_id: string }[]>(
    "select token_id from private.trading_ingest_tokens where token_hash=$1 and active=true and revoked_at is null limit 1",
    [tokenHash],
  );
  return rows.length === 1;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

async function rpc(name: "claim" | "complete", payload: any) {
  if (name === "claim") {
    const required = [
      "job_key",
      "workflow_id",
      "workflow_version",
      "scheduler_version",
      "scheduled_at",
      "trigger_type",
    ];
    const missing = required.filter((key) => !String(payload[key] ?? "").trim());
    if (missing.length > 0) {
      console.error(
        "scheduler_gateway_invalid_claim_shape",
        JSON.stringify({ missing, keys: Object.keys(payload).sort() }),
      );
      throw new Error("invalid_claim_shape:" + missing.join(","));
    }
    const rows = await sql<{ result: Record<string, unknown> }[]>`
      select private.anevum_scheduler_claim(${sql.json(payload)}::jsonb) as result
    `;
    if (!rows[0]?.result) throw new Error("scheduler_rpc_empty");
    return rows[0].result;
  }

  const rows = await sql<{ result: Record<string, unknown> }[]>`
    select private.anevum_scheduler_complete(${sql.json(payload)}::jsonb) as result
  `;
  if (!rows[0]?.result) throw new Error("scheduler_rpc_empty");
  return rows[0].result;
}

async function listRuns(url: URL) {
  const rawLimit = Number(url.searchParams.get("limit") || "100");
  const limit = Math.max(1, Math.min(250, Number.isFinite(rawLimit) ? rawLimit : 100));
  const workflow = url.searchParams.get("workflow_id")?.trim() || null;
  const query = workflow
    ? "select row_to_json(r) as run from (select run_id,job_key,workflow_id,workflow_version,scheduler_version,scheduled_at,started_at,completed_at,status,trigger_type,attempt,max_attempts,worker_identity,source_commit,input_identity,output_identity,error_classification,error_summary,retry_state,catchup_state,slack_notification_status,details,lease_until from private.anevum_scheduler_runs where workflow_id=$1 order by scheduled_at desc limit $2) r"
    : "select row_to_json(r) as run from (select run_id,job_key,workflow_id,workflow_version,scheduler_version,scheduled_at,started_at,completed_at,status,trigger_type,attempt,max_attempts,worker_identity,source_commit,input_identity,output_identity,error_classification,error_summary,retry_state,catchup_state,slack_notification_status,details,lease_until from private.anevum_scheduler_runs order by scheduled_at desc limit $1) r";
  const rows = workflow
    ? await sql.unsafe<{ run: Record<string, unknown> }[]>(query, [workflow, limit])
    : await sql.unsafe<{ run: Record<string, unknown> }[]>(query, [limit]);
  return rows.map((row) => row.run);
}

async function irenAction(action: string, body: Record<string, unknown>) {
  if (action === "iren_read") {
    const rows = await sql`select revision, state, updated_at from private.iren_control_state where singleton`;
    const events = await sql`select event, created_at, delivery_status, attempts from private.iren_control_events order by created_at desc limit 50`;
    return { ...rows[0], events };
  }
  if (action === "iren_commit") {
    const rows = await sql`select private.iren_control_commit(${sql.json(body as any)}::jsonb) as result`;
    return rows[0].result;
  }
  if (action === "iren_notifications_claim") {
    const owner = String(body.owner || "");
    if (!/^[a-f0-9-]{36}$/.test(owner)) throw new Error("invalid_iren_owner");
    const rows = await sql`
      with candidates as (
        select event_key from private.iren_control_events
        where delivery_status not like 'delivered:%' and attempts < 3
          and (lease_until is null or lease_until < now())
        order by created_at limit 10 for update skip locked
      ) update private.iren_control_events e set owner=${owner},
          lease_until=now()+interval '120 seconds', attempts=attempts+1
        from candidates c where e.event_key=c.event_key returning e.event
    `;
    return { events: rows.map((row) => row.event) };
  }
  if (action === "iren_notification_complete") {
    const result = String(body.delivery_status || "").slice(0, 200);
    const rows = await sql`update private.iren_control_events
      set delivery_status=${result}, lease_until=null, owner=null
      where event_key=${String(body.event_key)} and owner=${String(body.owner)}
        and lease_until > now() returning event_key`;
    return { updated: rows.length === 1 };
  }
  if (action === "iren_work_snapshot") {
    const objectives = await sql`
      select objective_key,parent_key,title,description,status,owner_system,priority,
             dependencies,success_criteria,protected_action,metadata,created_at,updated_at,completed_at
      from private.iren_objectives
      order by priority desc, created_at asc
    `;
    const jobs = await sql`
      select job_id,objective_key,title,instructions,owner_system,job_type,status,priority,
             protected_action,requires_human,requested_by,requested_via,claimed_by,lease_until,
             started_at,completed_at,result,error,metadata,created_at,updated_at
      from private.iren_jobs
      order by created_at desc
      limit 200
    `;
    const commands = await sql`
      select command_id,command_text,source,requested_by,status,response,linked_job_id,
             created_at,updated_at,completed_at
      from private.iren_commands
      order by created_at desc
      limit 100
    `;
    return { objectives, jobs, commands };
  }
  if (action === "iren_command_create") {
    const commandText = String(body.command_text || "").trim().slice(0, 4000);
    const source = String(body.source || "iren").trim().slice(0, 40);
    const requestedBy = String(body.requested_by || "").trim().slice(0, 160) || null;
    const context = objectValue(body.context);
    if (!commandText) throw new Error("invalid_iren_command");
    const rows = await sql`
      insert into private.iren_commands (command_text,source,requested_by,context)
      values (${commandText},${source},${requestedBy},${sql.json(context as any)}::jsonb)
      returning command_id,command_text,source,requested_by,status,created_at
    `;
    return { command: rows[0] };
  }
  if (action === "iren_commands_claim") {
    const owner = String(body.owner || "").trim().slice(0, 120);
    const rawLimit = Number(body.limit || 5);
    const limit = Math.max(1, Math.min(20, Number.isFinite(rawLimit) ? rawLimit : 5));
    if (!owner) throw new Error("invalid_iren_command_owner");
    const rows = await sql`
      with candidates as (
        select command_id from private.iren_commands
        where (status='QUEUED' or (status='PROCESSING' and lease_until < now()))
        order by created_at asc
        limit ${limit}
        for update skip locked
      )
      update private.iren_commands c
      set status='PROCESSING', owner=${owner}, lease_until=now()+interval '120 seconds',
          updated_at=now()
      from candidates x
      where c.command_id=x.command_id
      returning c.command_id,c.command_text,c.source,c.requested_by,c.status,c.context,c.created_at
    `;
    return { commands: rows };
  }
  if (action === "iren_command_complete") {
    const commandId = String(body.command_id || "").trim();
    const status = String(body.status || "").trim().toUpperCase();
    const response = objectValue(body.response);
    const linked = String(body.linked_job_id || "").trim() || null;
    if (!/^[a-f0-9-]{36}$/i.test(commandId) || !["SUCCEEDED","FAILED","CANCELLED"].includes(status))
      throw new Error("invalid_iren_command_completion");
    const rows = linked
      ? await sql`
          update private.iren_commands
          set status=${status}, response=${sql.json(response as any)}::jsonb,
              linked_job_id=${linked}::uuid, completed_at=now(), updated_at=now(),
              lease_until=null, owner=null
          where command_id=${commandId}::uuid
          returning command_id,status,response,linked_job_id,completed_at
        `
      : await sql`
          update private.iren_commands
          set status=${status}, response=${sql.json(response as any)}::jsonb,
              completed_at=now(), updated_at=now(), lease_until=null, owner=null
          where command_id=${commandId}::uuid
          returning command_id,status,response,linked_job_id,completed_at
        `;
    if (!rows[0]) throw new Error("iren_command_not_found");
    return { command: rows[0] };
  }
  if (action === "iren_job_create") {
    const job = objectValue(body.job);
    const title = String(job.title || "").trim().slice(0, 240);
    const instructions = String(job.instructions || "").trim().slice(0, 12000);
    const status = String(job.status || "QUEUED").trim().toUpperCase();
    const objectiveKey = String(job.objective_key || "").trim() || null;
    if (!title || !["QUEUED","WAITING","BLOCKED","NEEDS_APPROVAL"].includes(status))
      throw new Error("invalid_iren_job");
    const rows = await sql`
      insert into private.iren_jobs (
        objective_key,title,instructions,owner_system,job_type,status,priority,
        protected_action,requires_human,requested_by,requested_via,metadata
      ) values (
        ${objectiveKey},${title},${instructions},
        ${String(job.owner_system || "IREN").slice(0,80)},
        ${String(job.job_type || "AGENT_WORK").slice(0,80)},
        ${status},${Number(job.priority || 0)},
        ${Boolean(job.protected_action)},${Boolean(job.requires_human)},
        ${String(job.requested_by || "").slice(0,160) || null},
        ${String(job.requested_via || "iren").slice(0,40)},
        ${sql.json(objectValue(job.metadata) as any)}::jsonb
      )
      returning *
    `;
    await sql`
      insert into private.iren_job_events (job_id,event_type,event)
      values (${rows[0].job_id},'CREATED',${sql.json({source:"iren_work_engine"} as any)}::jsonb)
    `;
    return { job: rows[0] };
  }
  if (action === "iren_jobs_claim") {
    const owner = String(body.owner || "").trim().slice(0, 120);
    const rawLimit = Number(body.limit || 3);
    const limit = Math.max(1, Math.min(10, Number.isFinite(rawLimit) ? rawLimit : 3));
    if (!owner) throw new Error("invalid_iren_job_owner");
    const rows = await sql`
      with candidates as (
        select job_id from private.iren_jobs
        where status='QUEUED' or (status='RUNNING' and lease_until < now())
        order by priority desc, created_at asc
        limit ${limit}
        for update skip locked
      )
      update private.iren_jobs j
      set status='RUNNING', claimed_by=${owner}, lease_until=now()+interval '300 seconds',
          started_at=coalesce(started_at,now()), updated_at=now()
      from candidates x
      where j.job_id=x.job_id
      returning j.*
    `;
    return { jobs: rows };
  }
  if (action === "iren_job_update") {
    const jobId = String(body.job_id || "").trim();
    const status = String(body.status || "").trim().toUpperCase();
    if (!/^[a-f0-9-]{36}$/i.test(jobId) ||
        !["QUEUED","RUNNING","WAITING","BLOCKED","NEEDS_APPROVAL","SUCCEEDED","FAILED","CANCELLED"].includes(status))
      throw new Error("invalid_iren_job_update");
    const result = objectValue(body.result);
    const error = objectValue(body.error);
    const terminal = ["SUCCEEDED","FAILED","CANCELLED"].includes(status);
    const rows = await sql`
      update private.iren_jobs
      set status=${status}, result=${sql.json(result as any)}::jsonb, error=${sql.json(error as any)}::jsonb,
          completed_at=case when ${terminal} then now() else completed_at end,
          lease_until=null, claimed_by=null, updated_at=now()
      where job_id=${jobId}::uuid
      returning *
    `;
    if (!rows[0]) throw new Error("iren_job_not_found");
    await sql`
      insert into private.iren_job_events (job_id,event_type,event)
      values (${jobId}::uuid,${status},${sql.json({result,error} as any)}::jsonb)
    `;
    return { job: rows[0] };
  }
  if (action === "iren_objective_update") {
    const objectiveKey = String(body.objective_key || "").trim();
    const status = String(body.status || "").trim().toUpperCase();
    if (!objectiveKey || !["LOCKED","ACTIVE","BLOCKED","READY","COMPLETE","FUTURE","OBSOLETE"].includes(status))
      throw new Error("invalid_iren_objective_update");
    const rows = await sql`
      update private.iren_objectives
      set status=${status}, updated_at=now(),
          completed_at=case when ${status === "COMPLETE"} then now() else completed_at end
      where objective_key=${objectiveKey}
      returning *
    `;
    if (!rows[0]) throw new Error("iren_objective_not_found");
    return { objective: rows[0] };
  }
  throw new Error("invalid_iren_action");
}

Deno.serve(async (req: Request) => {
  if (!(await authorized(req))) {
    return json(401, { ok: false, error: "unauthorized" });
  }

  try {
    if (req.method === "GET") {
      return json(200, {
        ok: true,
        service: "anevum-scheduler-gateway",
        runs: await listRuns(new URL(req.url)),
      });
    }
    if (req.method !== "POST") {
      return json(405, { ok: false, error: "method_not_allowed" });
    }

    const body = objectValue(await req.json());
    const action = String(body.action || "");
    if (["iren_read", "iren_commit", "iren_notifications_claim", "iren_notification_complete", "iren_work_snapshot", "iren_command_create", "iren_commands_claim", "iren_command_complete", "iren_job_create", "iren_jobs_claim", "iren_job_update", "iren_objective_update"].includes(action)) {
      return json(200, { ok: true, ...(await irenAction(action, body)) });
    }
    if (action === "claim") {
      return json(200, {
        ok: true,
        ...(await rpc("claim", objectValue(body.job))),
      });
    }
    if (action === "complete") {
      return json(200, {
        ok: true,
        ...(await rpc("complete", body)),
      });
    }
    return json(400, { ok: false, error: "invalid_action" });
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown_error";
    console.error("scheduler_gateway_failed", message);
    return json(500, {
      ok: false,
      error: "scheduler_gateway_failed",
      classification: message,
    });
  }
});