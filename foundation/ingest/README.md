# Foundation evidence ingest

Private staging service for ANEVUM Foundation v2.

Purpose:
- accept RHEN evidence in the existing event envelope;
- preserve idempotency with `event_key`;
- store canonical payload hashes;
- write append-only evidence to `rhen.events`;
- create a minimal `rhen.strategy_runs` record when an event references a run that has not yet been registered.

This service is intentionally private-only during staging. It must not receive a public Railway domain until authentication, rate limiting, and production cutover rules are explicitly added.

It is a shadow migration target, not yet the live trading persistence authority.


## Authentication boundary

While the service has no public domain, staging may omit `FOUNDATION_INGEST_TOKEN`.
Before any public domain is attached, set a strong `FOUNDATION_INGEST_TOKEN`; event
writes and `/status` then require the same value in the
`x-anevum-foundation-token` header. Railway health endpoints `/live` and
`/ready` remain unauthenticated so platform health checks continue to work.
