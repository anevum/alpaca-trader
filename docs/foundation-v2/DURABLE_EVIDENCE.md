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

During migration this sink is shadow-only. Existing Supabase persistence remains authoritative for live RHEN until parity and reconciliation gates pass.
