# Foundation v2 current state — 2026-10-04

Status: Runtime cutover complete and operational. Foundation v2 is now locked infrastructure; remaining items are owner-only evidence or non-runtime cleanup.

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


## 2026-10-02 final runtime verification addendum

Foundation report-read reliability was repaired on canonical main through PRs #180-#185:

- PR #180 scopes forward-outcome reads to candidate identities already selected by the request.
- PR #181 adds the partial PostgreSQL candidate-identity index for forward outcomes; migration 0007 is applied and verified.
- PR #182 moves decision-candidate expansion/filtering into PostgreSQL instead of materializing whole decision-cycle payloads in Python.
- PR #183 fixes Psycopg percent-wildcard escaping discovered by the first live #182 probe. The failed build was immediately rolled back to the prior known-good image while the fix passed CI, then the corrected image was redeployed.
- PR #184 moves the existing crypto "< 7 complete horizons" filter and 5,000-candidate cap into PostgreSQL before candidate materialization.
- PR #185 raises only the research report HTTP client timeout from 15 seconds to 30 seconds so bounded cold reads cannot be misclassified as failed evidence runs.

Post-deployment live verification on PR #185 observed:
- crypto promotion: HTTP 200 in 16.287 seconds;
- active prior-session crypto evidence: HTTP 200 in 9.437 seconds;
- current-session crypto evidence: HTTP 200 in 0.089 seconds;
- no client disconnect/499 in the verified post-deployment sample;
- Foundation ingest current memory approximately 0.53 GB. The one-hour maximum still includes the pre-repair high-water period and is not representative of the current process state.

ANEVUM Core (4 services) and RHEN production (9 services) were re-read after deployment: all 13 services were online with zero active Railway warnings/criticals and zero recent failed deployments. The Foundation migrator is restored to its canonical start command `python -m foundation.migrator.run`.

### Remaining sign-off boundaries

These are evidence/authorization boundaries, not runtime cutover defects:

1. Owner-browser confirmation through an authenticated Cloudflare Access session remains required for the final human sign-off of Command/session/topology and a harmless IREN enqueue.
2. Railway variable values remain redacted to the connected OAuth clients. Variable-name inventory shows no Supabase-named runtime variables across the required services, but final zero-dependency proof still requires a value-capable Railway session to inspect generic URL/token destinations.
3. Temporary merged PR branches cannot be deleted through the current GitHub connector. They are non-runtime and do not affect canonical main.
4. The retained Supabase project remains untouched. Deletion is not authorized and is not required for the running Foundation v2 architecture.

Runtime cutover status: **COMPLETE** for canonical GitHub main -> Railway compute -> Railway PostgreSQL with Cloudflare Access at the private edge. Final zero-dependency sign-off remains **PENDING OWNER/VALUE EVIDENCE** only.


## 2026-10-04 closeout

Foundation v2 has completed its engineering closeout. The canonical runtime remains:

GitHub main -> Railway compute -> Foundation -> Railway PostgreSQL.

Cloudflare remains the public/private edge and Access identity layer. Alpaca, Slack and OpenAI remain intentional integrations. Supabase is not part of the canonical operational runtime.

### Live runtime verification

Fresh Railway verification on 2026-10-04 found:

- ANEVUM Core: 5/5 services online, zero active warnings, zero active criticals and zero recent failures across Foundation/PostgreSQL/NOSTRA.
- RHEN production: the current production fleet is online, including alpaca-trader, research agent, pre-open state, research scheduler, VELUM, crypto edge discovery, IREN executor, GRAEN, GRAEN research executor and the BTC paper canary.
- Foundation ingest is actively receiving and returning HTTP 200 for /v1/events, /v1/scheduler-gateway, /v1/graen-gateway, /v1/trading-reconcile, /v1/crypto-promotion-status and /v1/trading-report-read.
- Foundation ingest memory over the latest sampled hour remained approximately 0.46-0.66 GB, with average CPU approximately 11%; the previous multi-gigabyte report-read memory excursion has not recurred in the sampled post-repair window.
- Both ANEVUM Core and RHEN report no actual staged configuration changes. Railway still surfaces stale empty environment-patch workflow records with zero changes; these are non-runtime bookkeeping artifacts and must not be treated as undeployed configuration.

### Runtime dependency audit

A fresh variable-name audit across the active ANEVUM Core and RHEN services found no SUPABASE-named runtime variables.

Railway OAuth still redacts variable values, so this remains a name-and-runtime-behavior audit rather than a cryptographic zero-dependency proof for arbitrary generic URL/token values.

COMMAND_AUTH_MODE remains present on alpaca-trader as an obsolete variable name, while tracked current code contains no COMMAND_AUTH_MODE reference. Because the connected Railway interface does not expose a safe single-variable delete primitive and automated agent removal could not be verified, the variable is left untouched. It is nonfunctional debt, not an active auth path.

### Locked Foundation v2 status

The following are now treated as locked decisions:

1. Foundation v2 is the canonical data/evidence spine.
2. Railway PostgreSQL is the canonical operational store.
3. Supabase fallback/dual-runtime logic must not be restored.
4. Cloudflare Access remains the private edge for Command/private APIs.
5. Foundation is not to be redesigned as part of crypto-strategy iteration.
6. GRAEN, VELUM, NOSTRA, IREN and RHEN should consume Foundation through the current contracts rather than creating new parallel stores.
7. Trading/research strategy changes must not be mixed into Foundation maintenance unless an actual Foundation defect is demonstrated.

### Remaining non-blocking closeout items

These items do not block Foundation v2 operation:

- The ANEVUM Core Railway environment is still named `staging` even though it serves canonical production consumers. Renaming is cosmetic and should be done only through a supported environment-rename path that does not recreate resources or alter environment IDs.
- `COMMAND_AUTH_MODE` should be deleted when a safe value-aware/single-variable delete path is available.
- Owner-browser confirmation of Command/session/topology and a harmless IREN enqueue remains a human acceptance check, not an engineering runtime blocker.
- Railway OAuth value redaction prevents a final exhaustive inspection of arbitrary generic variable values.
- The retained Supabase project remains untouched; deletion is separately authorized work and is not required for Foundation v2.

Foundation v2 engineering status: **CLOSED / OPERATIONAL / LOCKED**.

Development priority should now move above the Foundation layer: GRAEN/VELUM/NOSTRA research quality, crypto strategy validation, paper-canary evidence and eventual RHEN promotion gates.
