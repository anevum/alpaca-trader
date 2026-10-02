# Foundation v2 current state — 2026-10-02

Status: Access-only code deployed; final end-to-end sign-off remains open.

## Canonical runtime

GitHub main -> Railway compute -> Railway PostgreSQL.
Cloudflare provides the public edge and Access identity. Alpaca, Slack and OpenAI remain intentional integrations.
The ANEVUM Core environment is still named `staging`; its Foundation endpoint serves the production consumers. This name does not designate another canonical store.
Public trading telemetry remains `OFFLINE_BY_DESIGN`.

Backend PRs #170, #172, #174–178 and website PRs #104–105, #107–108 already merged before this verification session.
Current backend auth code always verifies Cloudflare Access; `COMMAND_AUTH_MODE` is no longer read. Do not restore the obsolete Supabase/dual fallback.
All nine RHEN services and Foundation ingest, migrator and evidence probe have source branch `main` and successful live deployments. Crypto-edge and VELUM retain older main commit pins; source inspection of current main alone does not certify those older images.

## Access read-back

- Application: `ea5ecc36-b828-4030-aaf7-c2a7799f6085` (ANEVUM Command, self_hosted)
- AUD: `3fabe3c703b4cc972136d2292fda39783c11903c3c6cf5ac0e42c4738f2ee800`
- Issuer/team domain: `https://wispy-tooth-095a.cloudflareaccess.com`
- Exact destinations: `anevum.com/command*`, `anevum.com/api/command*`
- Owner policy: `266c244d-1d51-4da8-8a46-4996d4517190`, allow, precedence 1
- Include: exact email `devon@anevum.com`; exclude/require empty; no domain-wide allow, bypass or service-auth policy.

The initial read-back found a previously attached reusable staff policy. The application association was removed; the reusable account policy was preserved and read back unchanged. Only the owner policy now applies.
API contract was checked against Cloudflare's OpenAPI-generated TypeScript SDK, blob `f6e2720eb3a3a09589fc9a6188a7a675549f6d11`: public destinations use `{type:"public",uri:"hostname/path"}`; application updates accept policy ID/precedence links.

[Policy correction](https://github.com/anevum/anevum-web/actions/runs/36944754450) succeeded before that run's Python-client public probe returned 403. No Access or WAF bypass was introduced. Subsequent standard curl probes passed.
[Complete edge and web contract verification](https://github.com/anevum/anevum-web/actions/runs/36947417138) passed:
- homepage, RHEN and IREN public pages: 200;
- Command, topology and private API paths: redirect to the expected Access domain;
- direct Railway RHEN session/status and Foundation IREN GET/POST without credentials: 401;
- public telemetry: 503 with OFFLINE_BY_DESIGN;
- 13 web IREN/topology contract tests passed.
[Backend Access/IREN verification](https://github.com/anevum/alpaca-trader/actions/runs/36947423834): 6 tests passed; no legacy runtime endpoint matches.

## Dependency inventory and classification

| Reference | Classification | Disposition |
| --- | --- | --- |
| Backend app/, graen/, foundation/ executable endpoint configuration | ACTIVE | No Supabase URL, key environment reference or /functions/v1 endpoint found in tracked source |
| Railway CF_ACCESS_* and COMMAND_ACCESS_EMAILS | ACTIVE | Names present on RHEN and Foundation; Worker values match read-back |
| Railway TRADING_INGEST_URL, SCHEDULER_GATEWAY_URL, GRAEN_GATEWAY_URL, RHEN_RESEARCH_GATEWAY_URL | ACTIVE | Keep; current traffic reaches Foundation; names alone are not obsolete |
| Railway COMMAND_AUTH_MODE | MIGRATED/OBSOLETE | Still present on alpaca-trader; current code ignores it; no supported variable-delete tool available in this session |
| Supabase-named variables in RHEN and ANEVUM Core | MIGRATED/OBSOLETE | None found across all 13 services, including PostgreSQL |
| Backend README and shadow-era Foundation runbooks | MIGRATED/OBSOLETE | Current docs corrected; original parity runbook explicitly marked historical |
| Test scheduler /functions/v1 URL | MIGRATED/OBSOLETE | Replaced with Railway-shaped /v1 fixture |
| foundation/public_feed.py and report_read.py Supabase mentions | archival | Explanatory limitations: missing historical analytical projections are not fabricated |
| foundation/public_feed_probe.py supabase_required:false | archival | Negative verification assertion, no connection |
| database/, db/migrations/, docs/archive/, research development-state JSON | archival | Preserve provenance, applied migration hashes and frozen research; do not rewrite |
| Web worker/browser auth and IREN proxy | ACTIVE | Access assertions and Railway endpoints only; no browser Supabase bearer required |
| Web site/ and native/iren-ios/ | archival | Retained old client source, outside production Vite entrypoint; not valid Foundation v2 clients |
| Web product/architecture labels and README | MIGRATED/OBSOLETE | Correction tracked in paired website cleanup |
| Web founder history, release artifacts and legacy-auth rejection tests | archival | Preserve historical record and negative tests |

Railway OAuth withholds all variable values. This inventory certifies names and observed destinations, not an exhaustive scan of generic variable values. Do not claim final zero-dependency proof from variable names alone. RHEN Archive has not been modified or certified as required production runtime.

## Runtime observations

Sampled deploy and DNS logs beginning 2026-10-02 00:03 UTC across required services show Foundation/PostgreSQL traffic and no Supabase matches. Some streams hit the 200-row limit; absence in a bounded sample is not proof for all traffic.
Observed destinations include Foundation, postgres.railway.internal, internal Railway services, Alpaca, Slack and the Access issuer.
RHEN private status/evidence/daily-report reads and Foundation IREN reads returned 200. Weekly RHEN report read returned 404; historical reports must not be invented.

Infrastructure issue under investigation: Foundation trading-report-read requests sometimes reach Railway's 15-second client deadline (499), while the server subsequently logs 200. Foundation memory rose from about 0.6 GB to 5.4 GB in the sampled hour. `python -m foundation.report_diagnostics` provides read-only aggregate storage diagnostics without logging payloads or credentials. Do not bypass this issue by weakening validation or changing evidence selection.

## Open gates

1. Owner browser confirmation of Command and /api/command/session without Supabase credentials.
2. Owner IREN status/topology and safe command enqueue (no broker action, research run or paid model invocation).
3. Resolve and reverify Foundation report-read timeouts.
4. Inspect actual Railway variable values through an authorized value-capable session; remove only proven-unused variables.
5. Finish cleanup of temporary ops branches/workflows after evidence is recorded.

The Supabase project remains untouched. Deletion requires explicit authorization after final zero-dependency verification; this document is not such authorization.
