import postgres from "npm:postgres@3.4.5";
import { createHandler } from "./handler.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, {
  prepare: false,
  max: 1,
  idle_timeout: 5,
  connect_timeout: 5,
  connection: { statement_timeout: 5000 },
});

Deno.serve(createHandler({
  async authenticate(token) {
    const response = await fetch(Deno.env.get("SUPABASE_URL") + "/auth/v1/user", {
      headers: {
        authorization: "Bearer " + token,
        apikey: Deno.env.get("SUPABASE_ANON_KEY")!,
      },
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401 || response.status === 403) return null;
    if (!response.ok) throw new Error("identity_unavailable");
    return response.json();
  },

  async read() {
    const rows = await sql.begin("read only", async tx =>
      await tx`select revision, state, updated_at from private.iren_control_state where singleton`
    );
    return rows[0] as any || null;
  },

  async readWork() {
    return await sql.begin("read only", async tx => {
      const objectives = await tx`
        select objective_key,parent_key,title,description,status,owner_system,priority,
               dependencies,success_criteria,protected_action,metadata,created_at,updated_at,completed_at
        from private.iren_objectives
        order by priority desc, created_at asc
      `;
      const jobs = await tx`
        select job_id,objective_key,title,owner_system,job_type,status,priority,
               protected_action,requires_human,requested_by,requested_via,
               started_at,completed_at,result,error,metadata,created_at,updated_at
        from private.iren_jobs
        order by created_at desc
        limit 100
      `;
      const commands = await tx`
        select command_id,command_text,source,requested_by,status,response,linked_job_id,
               created_at,updated_at,completed_at
        from private.iren_commands
        order by created_at desc
        limit 50
      `;
      return { objectives, jobs, commands } as any;
    });
  },

  async writeCommand(command, requestedBy) {
    const rows = await sql`
      insert into private.iren_commands (command_text,source,requested_by)
      values (${command},'command',${requestedBy})
      returning command_id,command_text,source,requested_by,status,created_at
    `;
    return rows[0] as any;
  },
}));
