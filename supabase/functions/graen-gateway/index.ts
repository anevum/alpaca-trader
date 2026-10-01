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


    // Research-code handoff uses the existing authenticated GRAEN boundary.
    // Lease + compare-and-swap prevent concurrent workers losing provenance.
    if (action === "research_promotion_claim") {
      const problemId = String(body.problem_id || "");
      const owner = String(body.owner || "");
      if (!/^[0-9a-f-]{36}$/.test(problemId) || !/^[0-9a-f-]{36}$/.test(owner)) {
        throw new Error("invalid_promotion_identity");
      }
      const result = await sql.begin(async tx => {
        const rows = await tx`
          select status,metadata from private.graen_problems
          where problem_id=${problemId}::uuid for update
        `;
        if (!rows[0] || !["WAITING","BLOCKED"].includes(rows[0].status)) return { claimed: false };
        const metadata = objectValue(rows[0].metadata);
        const state = objectValue(metadata.code_promotion);
        const lease = objectValue(metadata.code_promotion_lease);
        if (state.phase === "COMPLETE") return { claimed: false };
        if (lease.until && new Date(String(lease.until)).getTime() > Date.now()) return { claimed: false };
        const revision = Number(metadata.code_promotion_revision || 0);
        const prespec = objectValue(state.prespec || metadata.research_implementation_spec);
        const exposureId = String(prespec.exposure_artifact_id || "");
        let exposure: Record<string, unknown> = {};
        if (/^[0-9a-f-]{36}$/.test(exposureId)) {
          const artifacts = await tx`
            select artifact_id,content from private.graen_artifacts
            where artifact_id=${exposureId}::uuid and problem_id=${problemId}::uuid
              and artifact_type='RESEARCH_CORPUS_EXPOSURE_LEDGER'
          `;
          if (artifacts[0]) exposure = { ...objectValue(artifacts[0].content), artifact_id: artifacts[0].artifact_id };
        }
        const heartbeats = await tx`
          select metadata->'research_executor' as executor from private.graen_runtime_state where singleton
        `;
        const nextMetadata = {
          ...metadata,
          code_promotion_lease: { owner, until: new Date(Date.now() + 300000).toISOString() },
        };
        await tx`update private.graen_problems set metadata=${tx.json(nextMetadata as any)}::jsonb,
          updated_at=now() where problem_id=${problemId}::uuid`;
        return {
          claimed: true, revision, state, prespec, exposure,
          prespec_artifact_id: metadata.code_prespec_artifact_id || null,
          executor_heartbeat: heartbeats[0]?.executor || {},
        };
      });
      return json(200, { ok: true, ...result });
    }

    if (action === "research_promotion_save") {
      const problemId = String(body.problem_id || "");
      const owner = String(body.owner || "");
      const expectedRevision = Number(body.expected_revision);
      const state = objectValue(body.state);
      if (!/^[0-9a-f-]{36}$/.test(problemId) || !/^[0-9a-f-]{36}$/.test(owner)
          || !Number.isSafeInteger(expectedRevision) || JSON.stringify(state).length > 100000) {
        throw new Error("invalid_promotion_state");
      }
      const result = await sql.begin(async tx => {
        const rows = await tx`
          select status,metadata from private.graen_problems
          where problem_id=${problemId}::uuid for update
        `;
        if (!rows[0] || !["WAITING","BLOCKED"].includes(rows[0].status)) throw new Error("promotion_problem_not_idle");
        const metadata = objectValue(rows[0].metadata);
        const previous = objectValue(metadata.code_promotion);
        const lease = objectValue(metadata.code_promotion_lease);
        if (lease.owner !== owner || Number(metadata.code_promotion_revision || 0) !== expectedRevision
          || !(new Date(String(lease.until || "")).getTime() > Date.now())) throw new Error("promotion_lease_or_revision_conflict");
        if (previous.spec_hash && (
          previous.spec_hash !== state.spec_hash ||
          JSON.stringify(previous.prespec) !== JSON.stringify(state.prespec)
        )) throw new Error("immutable_prespec_changed");
        let prespecArtifactId = metadata.code_prespec_artifact_id || null;
        if (state.prespec && !prespecArtifactId) {
          if (state.phase !== "BRANCH" || !/^[a-f0-9]{64}$/.test(String(state.spec_hash))) {
            throw new Error("prespec_must_be_frozen_before_branch");
          }
          const content = { specification: state.prespec, specification_hash: state.spec_hash };
          const hash = await sha256Hex(JSON.stringify(content));
          const key = [problemId, "research-code-prespec", state.spec_hash].join(":");
          const artifacts = await tx`
            insert into private.graen_artifacts (
              problem_id,artifact_key,artifact_type,methodology_version,content_hash,content
            ) values (
              ${problemId}::uuid,${key},'RESEARCH_CODE_FROZEN_PRESPEC',
              'graen.research-code-promotion.v1',${hash},${tx.json(content as any)}::jsonb
            ) on conflict (artifact_key) do update set artifact_key=excluded.artifact_key
            returning artifact_id
          `;
          prespecArtifactId = artifacts[0].artifact_id;
        }
        if (state.phase === "COMPLETE") {
          if (previous.phase !== "RESUME" || state.resume_stage !== "CRYPTO_COMPILED_DEVELOPMENT"
            || !state.merge_sha || !state.deployment_id || !state.executor_heartbeat_at
            || !Array.isArray(state.ci) || state.ci.length === 0 || !prespecArtifactId) {
            throw new Error("verified_deployment_required_before_resume");
          }
          const active = await tx`
            select run_id from private.graen_runs
            where problem_id=${problemId}::uuid and status='RUNNING' limit 1
          `;
          if (active.length) throw new Error("active_research_run_prevents_resume");
        }
        if (JSON.stringify(previous) === JSON.stringify(state)) {
          await tx`update private.graen_problems
            set metadata=jsonb_set(metadata,'{code_promotion_lease}','{}'::jsonb)
            where problem_id=${problemId}::uuid`;
          return { revision: expectedRevision, prespec_artifact_id: prespecArtifactId };
        }
        const revision = expectedRevision + 1;
        const nextMetadata = {
          ...metadata, code_promotion: state, code_promotion_revision: revision,
          code_prespec_artifact_id: prespecArtifactId, code_promotion_lease: {},
          ...(state.phase === "COMPLETE" ? {
            research_stage: "CRYPTO_COMPILED_DEVELOPMENT",
            compiled_specification_hash: state.spec_hash,
          } : {}),
        };
        const event = { revision, ...state, prespec_artifact_id: prespecArtifactId };
        const hash = await sha256Hex(JSON.stringify(event));
        await tx`
          insert into private.graen_artifacts (
            problem_id,artifact_key,artifact_type,methodology_version,content_hash,content
          ) values (
            ${problemId}::uuid,${problemId + ":research-code-event:" + revision},
            'RESEARCH_CODE_PROMOTION_EVENT','graen.research-code-promotion.v1',
            ${hash},${tx.json(event as any)}::jsonb
          )
        `;
        await tx`
          update private.graen_problems
          set metadata=${tx.json(nextMetadata as any)}::jsonb, updated_at=now()
          where problem_id=${problemId}::uuid
        `;
        return { revision, prespec_artifact_id: prespecArtifactId };
      });
      return json(200, { ok: true, ...result });
    }


    if (action === "compiled_stage_evidence") {
      const problemId = String(body.problem_id || "");
      const specHash = String(body.spec_hash || "");
      const stage = String(body.stage || "");
      const epoch = String(body.epoch || "");
      const predecessor = ({ validation: "development", holdout: "validation" } as Record<string,string>)[stage] || "";
      if (!/^[0-9a-f-]{36}$/.test(problemId) || !/^[0-9a-f]{64}$/.test(specHash)
        || !["development","validation","holdout"].includes(stage)) throw new Error("invalid_compiled_stage");
      const artifacts = await sql`
        select artifact_id,content from private.graen_artifacts
        where problem_id=${problemId}::uuid and artifact_type='COMPILED_STAGE_RESULT'
          and content->>'spec_hash'=${specHash} and content->>'epoch'=${epoch}
          and content->>'stage' in (${stage},${predecessor})
        order by created_at asc
      `;
      const current = artifacts.find(row => row.content.stage === stage);
      const prior = artifacts.find(row => row.content.stage === predecessor);
      return json(200, { ok: true,
        current: current ? { ...current.content, artifact_id: current.artifact_id } : null,
        predecessor: prior ? { ...prior.content, artifact_id: prior.artifact_id } : null,
      });
    }

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

    if (action === "queue_research_stage") {
      const problemId = String((body as any).problem_id || "").trim();
      const stage = String((body as any).stage || "").trim().slice(0, 120);
      const metadata = objectValue((body as any).metadata);
      if (!problemId || !stage) throw new Error("invalid_research_stage");
      const active = await sql`
        select count(*)::int as count
        from private.graen_runs
        where problem_id=${problemId}::uuid and status='RUNNING'
      `;
      if (Number(active[0]?.count || 0) > 0) throw new Error("research_problem_run_active");
      const rows = await sql`
        update private.graen_problems
        set status='WAITING',
            completed_at=null,
            metadata=coalesce(metadata,'{}'::jsonb)
              || ${sql.json({ research_stage: stage } as any)}::jsonb
              || ${sql.json(metadata as any)}::jsonb,
            updated_at=now()
        where problem_id=${problemId}::uuid
        returning *
      `;
      if (!rows[0]) throw new Error("graen_problem_not_found");
      return json(200, { ok: true, problem: rows[0] });
    }

    if (action === "claim_research_problem") {
      const workerId = String((body as any).worker_id || "").trim().slice(0, 160);
      const runtimeVersion = String((body as any).runtime_version || "").trim().slice(0, 160);
      const methodologyVersion = String((body as any).methodology_version || "").trim().slice(0, 160);
      const domain = String((body as any).domain || "").trim().slice(0, 80);
      const sourceCommit = String((body as any).source_commit || "").trim().slice(0, 160) || null;
      const deploymentId = String((body as any).deployment_id || "").trim().slice(0, 160) || null;
      if (!workerId || !runtimeVersion || !methodologyVersion || !domain) throw new Error("invalid_research_worker");
      const rows = await sql`
        with candidate as (
          select p.problem_id
          from private.graen_problems p
          where p.status='WAITING'
            and p.domain=${domain}
            and (
              p.metadata->'code_promotion' is null
              or p.metadata->'code_promotion'->>'phase'='COMPLETE'
            )
            and (
              (
                select r.result_summary->>'state'
                from private.graen_runs r
                where r.problem_id=p.problem_id
                order by r.started_at desc
                limit 1
              )='READY_FOR_RESEARCH_EXECUTOR'
              or p.metadata->>'research_stage' in ('CRYPTO_LEADLAG_R2_READY','CRYPTO_V7_BATCH_READY','CRYPTO_AUTONOMOUS_DEVELOPMENT','CRYPTO_AUTONOMOUS_VALIDATION','CRYPTO_AUTONOMOUS_HOLDOUT','CRYPTO_AUTONOMOUS_VELUM_REPLAY','CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT','CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION','CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT','CRYPTO_ACTIVITY_SHOCK_V9_VELUM_REPLAY','CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT','CRYPTO_TREND_PULLBACK_V10_VALIDATION','CRYPTO_TREND_PULLBACK_V10_HOLDOUT','CRYPTO_TREND_PULLBACK_V10_VELUM_REPLAY','CRYPTO_COMPILED_DEVELOPMENT','CRYPTO_COMPILED_VALIDATION','CRYPTO_COMPILED_HOLDOUT')
            )
          order by p.priority desc, p.created_at asc
          limit 1
          for update skip locked
        )
        update private.graen_problems p
        set status='RUNNING', updated_at=now()
        from candidate c
        where p.problem_id=c.problem_id
        returning p.*
      `;
      const problem = rows[0] || null;
      if (!problem) {
        const executorState = {
          worker_id: workerId,
          runtime_version: runtimeVersion,
          methodology_version: methodologyVersion,
          source_commit: sourceCommit,
          deployment_id: deploymentId,
          heartbeat_at: new Date().toISOString(),
          active_problem_id: null,
          last_error: null,
        };
        await sql`
          update private.graen_runtime_state
          set metadata=coalesce(metadata,'{}'::jsonb)
                || jsonb_build_object('research_executor', ${sql.json(executorState as any)}::jsonb),
              updated_at=now()
          where singleton
        `;
        return json(200, { ok: true, problem: null });
      }
      const runRows = await sql`
        insert into private.graen_runs (
          problem_id,worker_id,runtime_version,source_commit,deployment_id,
          methodology_version,status,input_snapshot,model_usage
        ) values (
          ${problem.problem_id},${workerId},${runtimeVersion},${sourceCommit},${deploymentId},
          ${methodologyVersion},'RUNNING',
          ${sql.json({
            problem_key: problem.problem_key,
            domain: problem.domain,
            constraints: problem.constraints,
            success_criteria: problem.success_criteria
          } as any)}::jsonb,
          '{"invoked":false}'::jsonb
        )
        returning *
      `;
      const executorState = {
        worker_id: workerId,
        runtime_version: runtimeVersion,
        methodology_version: methodologyVersion,
        source_commit: sourceCommit,
        deployment_id: deploymentId,
        heartbeat_at: new Date().toISOString(),
        active_problem_id: problem.problem_id,
        last_error: null,
      };
      await sql`
        update private.graen_runtime_state
        set metadata=coalesce(metadata,'{}'::jsonb)
              || jsonb_build_object('research_executor', ${sql.json(executorState as any)}::jsonb),
            updated_at=now()
        where singleton
      `;
      return json(200, { ok: true, problem, run: runRows[0] });
    }

    if (action === "executor_heartbeat") {
      const workerId = String((body as any).worker_id || "").trim().slice(0, 160);
      const runtimeVersion = String((body as any).runtime_version || "").trim().slice(0, 160);
      const methodologyVersion = String((body as any).methodology_version || "").trim().slice(0, 160);
      const sourceCommit = String((body as any).source_commit || "").trim().slice(0, 160) || null;
      const deploymentId = String((body as any).deployment_id || "").trim().slice(0, 160) || null;
      const activeProblem = String((body as any).active_problem_id || "").trim() || null;
      const lastError = String((body as any).last_error || "").trim().slice(0, 1000) || null;
      if (!workerId || !runtimeVersion || !methodologyVersion) throw new Error("invalid_executor_heartbeat");
      const executorState = {
        worker_id: workerId,
        runtime_version: runtimeVersion,
        methodology_version: methodologyVersion,
        source_commit: sourceCommit,
        deployment_id: deploymentId,
        heartbeat_at: new Date().toISOString(),
        active_problem_id: activeProblem,
        last_error: lastError,
      };
      await sql`
        update private.graen_runtime_state
        set metadata=coalesce(metadata,'{}'::jsonb)
              || jsonb_build_object('research_executor', ${sql.json(executorState as any)}::jsonb),
            updated_at=now()
        where singleton
      `;
      return json(200, { ok: true });
    }

    if (action === "shadow_checkpoint") {
      const problemId = String((body as any).problem_id || "").trim();
      const activationId = String((body as any).activation_id || "").trim().slice(0,160);
      const candidateId = String((body as any).candidate_id || "").trim().slice(0,160);
      const shadowStatus = String((body as any).status || "").trim().toUpperCase();
      const evidence = objectValue((body as any).evidence);
      const allowedShadowStatuses = new Set([
        "COLLECTING",
        "READY_FOR_HUMAN_REVIEW",
        "SHADOW_REJECTED",
      ]);
      if (!problemId || !activationId || !candidateId || !allowedShadowStatuses.has(shadowStatus))
        throw new Error("invalid_shadow_checkpoint");

      await sql.begin(async (tx) => {
        const rows = await tx`
          select linked_iren_job_id
          from private.graen_problems
          where problem_id=${problemId}::uuid
          for update
        `;
        if (!rows[0]) throw new Error("graen_problem_not_found");
        const linkedJobId = rows[0].linked_iren_job_id || null;
        const shadowPayload = {
          activation_id: activationId,
          candidate_id: candidateId,
          status: shadowStatus,
          evidence,
          synced_at: new Date().toISOString(),
          execution_authority: false,
          broker_orders_possible: false,
          promotion_authorized: false,
        };
        await tx`
          update private.graen_problems
          set status='WAITING',
              completed_at=null,
              metadata=(coalesce(metadata,'{}'::jsonb) - 'research_stage')
                || jsonb_build_object(
                  'forward_shadow',
                  ${sql.json(shadowPayload as any)}::jsonb
                ),
              updated_at=now()
          where problem_id=${problemId}::uuid
        `;
        if (linkedJobId) {
          await tx`
            update private.iren_jobs
            set status='WAITING',
                requires_human=${shadowStatus === "READY_FOR_HUMAN_REVIEW"},
                completed_at=null,
                result=coalesce(result,'{}'::jsonb)
                  || jsonb_build_object(
                    'current_stage',
                    case
                      when ${shadowStatus}='READY_FOR_HUMAN_REVIEW' then 'FORWARD_SHADOW_READY_FOR_HUMAN_REVIEW'
                      when ${shadowStatus}='SHADOW_REJECTED' then 'FORWARD_SHADOW_REJECTED'
                      else 'FORWARD_SHADOW_RUNNING'
                    end,
                    'forward_shadow',
                    ${sql.json(shadowPayload as any)}::jsonb
                  ),
                updated_at=now()
            where job_id=${linkedJobId}::uuid
          `;
        }
      });
      return json(200, {
        ok: true,
        status: shadowStatus,
        protected_action_required: shadowStatus === "READY_FOR_HUMAN_REVIEW",
      });
    }

    if (action === "block_research_claim") {
      const problemId = String((body as any).problem_id || "").trim();
      const runId = String((body as any).run_id || "").trim();
      const workerId = String((body as any).worker_id || "").trim().slice(0, 160);
      const error = String((body as any).error || "research_claim_failed").trim().slice(0, 1000);
      if (!problemId || !runId || !workerId) throw new Error("invalid_research_claim_block");
      await sql.begin(async (tx) => {
        const runs = await tx`
          select status,worker_id from private.graen_runs
          where run_id=${runId}::uuid and problem_id=${problemId}::uuid
          for update
        `;
        if (!runs[0]) throw new Error("graen_run_not_found");
        if (runs[0].status === 'RUNNING') {
          await tx`
            update private.graen_runs
            set status='BLOCKED',
                result_summary=jsonb_build_object(
                  'state','RESEARCH_EXECUTION_BLOCKED',
                  'decision','REPAIR_REQUIRED',
                  'next_action','RESUME_FROZEN_STAGE_AFTER_REPAIR',
                  'error',${error},
                  'execution_authority',false
                ),
                completed_at=now()
            where run_id=${runId}::uuid and problem_id=${problemId}::uuid
          `;
        }
        await tx`
          update private.graen_problems
          set status='BLOCKED',updated_at=now()
          where problem_id=${problemId}::uuid and status='RUNNING'
        `;
        const executorState = {
          worker_id: workerId,
          heartbeat_at: new Date().toISOString(),
          active_problem_id: null,
          last_error: error,
        };
        await tx`
          update private.graen_runtime_state
          set metadata=coalesce(metadata,'{}'::jsonb)
                || jsonb_build_object('research_executor', ${tx.json(executorState as any)}::jsonb),
              updated_at=now()
          where singleton
        `;
      });
      return json(200, { ok: true, status: "BLOCKED" });
    }

    if (action === "complete_research_problem") {
      const problemId = String((body as any).problem_id || "").trim();
      const runId = String((body as any).run_id || "").trim();
      const workerId = String((body as any).worker_id || "").trim().slice(0, 160);
      const status = safeStatus((body as any).status);
      const resultSummary = objectValue((body as any).result_summary);
      const modelUsage = objectValue((body as any).model_usage);
      if (!problemId || !runId || !workerId || !["WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"].includes(status))
        throw new Error("invalid_research_completion");
      await sql.begin(async (tx) => {
        await tx`
          update private.graen_runs
          set status=${status},
              result_summary=${sql.json(resultSummary as any)}::jsonb,
              model_usage=${sql.json(modelUsage as any)}::jsonb,
              completed_at=now()
          where run_id=${runId}::uuid and problem_id=${problemId}::uuid
        `;
        const linked = await tx`
          select linked_iren_job_id
          from private.graen_problems
          where problem_id=${problemId}::uuid
          for update
        `;
        const linkedJobId = linked[0]?.linked_iren_job_id || null;
        await tx`
          update private.graen_problems
          set status=${status},updated_at=now(),
              completed_at=case when ${["SUCCEEDED","FAILED","CANCELLED"].includes(status)} then now() else null end,
              metadata=coalesce(metadata,'{}'::jsonb) - 'research_stage'
          where problem_id=${problemId}::uuid
        `;
        if (linkedJobId) {
          const terminalSuccess = status === "SUCCEEDED";
          const terminalFailure = status === "FAILED" || status === "CANCELLED";
          const syncPayload = {
            graen_problem_id: problemId,
            graen_run_id: runId,
            graen_result: resultSummary,
            graen_synced_at: new Date().toISOString(),
          };
          const failurePayload = {
            source: "GRAEN",
            graen_problem_id: problemId,
            graen_run_id: runId,
            result: resultSummary,
          };
          await tx`
            update private.iren_jobs
            set status=case
                  when ${terminalSuccess}::boolean and protected_action then 'WAITING'
                  else ${status}::text
                end,
                result=coalesce(result,'{}'::jsonb)
                  || ${sql.json(syncPayload as any)}::jsonb
                  || jsonb_build_object(
                    'graen_protected_completion_blocked',
                    (${terminalSuccess}::boolean and protected_action)
                  ),
                error=case
                  when ${terminalFailure}::boolean
                    then ${sql.json(failurePayload as any)}::jsonb
                  else '{}'::jsonb
                end,
                completed_at=case
                  when ${terminalSuccess}::boolean and not protected_action then now()
                  when ${terminalFailure}::boolean then now()
                  else completed_at
                end,
                updated_at=now()
            where job_id=${linkedJobId}::uuid
          `;
        }
        const completionState = {
          worker_id: workerId,
          heartbeat_at: new Date().toISOString(),
          active_problem_id: null,
          last_completion_at: new Date().toISOString(),
          last_status: status,
          last_error: status === "FAILED"
            ? String((resultSummary as any).error || "research_failed").slice(0,1000)
            : null,
        };
        await tx`
          update private.graen_runtime_state
          set metadata=coalesce(metadata,'{}'::jsonb)
                || jsonb_build_object('research_executor', ${sql.json(completionState as any)}::jsonb),
              updated_at=now()
          where singleton
        `;
      });
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
