# Foundation migration runner

This is a staging-first, one-shot migration verifier for ANEVUM Foundation v2.

It reads `DATABASE_URL`, applies every `db/migrations/*.sql` file in lexical order, records the SHA-256 of each applied migration in `anevum.schema_migrations`, and refuses to continue if an already-applied migration file has changed.

It then verifies the six canonical schemas and the five-system registry.

The runner is not a production daemon. It exists so schema application is reproducible and traceable during the migration.
