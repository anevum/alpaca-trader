create index if not exists iren_commands_linked_job_idx
  on private.iren_commands (linked_job_id)
  where linked_job_id is not null;
