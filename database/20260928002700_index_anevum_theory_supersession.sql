-- Cover the self-referential supersession foreign key for versioned theory artifacts.
create index anevum_theory_artifacts_supersedes_idx
  on private.anevum_theory_artifacts(supersedes_artifact_id)
  where supersedes_artifact_id is not null;
