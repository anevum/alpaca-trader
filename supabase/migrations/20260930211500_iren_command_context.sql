alter table private.iren_commands
  add column if not exists context jsonb not null default '{}'::jsonb
  check (jsonb_typeof(context) = 'object');
