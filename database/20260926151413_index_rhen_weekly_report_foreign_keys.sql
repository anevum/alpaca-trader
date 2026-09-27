
create index if not exists trading_weekly_reports_supersedes_idx
  on private.trading_weekly_reports(supersedes_report_id)
  where supersedes_report_id is not null;

create index if not exists trading_research_questions_experiment_idx
  on private.trading_research_questions(linked_experiment_id)
  where linked_experiment_id is not null;
