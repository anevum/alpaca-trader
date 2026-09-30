create table if not exists private.iren_settings (
  singleton boolean primary key default true check (singleton),
  autopilot_enabled boolean not null default false,
  autopilot_max_jobs_per_day integer not null default 3
    check (autopilot_max_jobs_per_day between 1 and 12),
  model_execution_authorized boolean not null default false,
  updated_by text,
  updated_at timestamptz not null default now()
);

alter table private.iren_settings enable row level security;
revoke all on table private.iren_settings from public, anon, authenticated;

insert into private.iren_settings (
  singleton, autopilot_enabled, autopilot_max_jobs_per_day,
  model_execution_authorized, updated_by
) values (true, false, 3, false, 'migration')
on conflict (singleton) do nothing;
