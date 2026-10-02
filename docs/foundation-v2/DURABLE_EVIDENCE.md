# Durable evidence outbox

Foundation v2 replaces RHEN's bounded in-memory telemetry queue with a persistent SQLite/WAL spool mounted on durable storage.

Properties:
- event-key idempotency;
- FULL synchronous SQLite writes;
- WAL journaling;
- leased batches for safe retries;
- exponential retry backoff;
- events remain on disk after delivery failure or process restart;
- remote Postgres ingest remains idempotent.

Railway PostgreSQL is now the canonical persistence destination. The `FOUNDATION_SHADOW_ENABLED` variable retains its historical name for the durable mirror/outbox transport. It does not make the retired Supabase store canonical. See [current state](CURRENT_STATE.md) for verification limits; preserve existing outbox contents.
