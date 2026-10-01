# Foundation v2 migration map

This file tracks legacy infrastructure as migration source material. It is not permission to delete anything.

## Legacy Railway project: RHEN

| Legacy service | Target owner | Target disposition |
| --- | --- | --- |
| alpaca-trader | RHEN | Refactor into services/rhen; preserve execution/risk behavior |
| rhen-velum | VELUM | Move to services/velum |
| rhen-research-agent | GRAEN / IREN | Split research generation from orchestration |
| rhen-preopen-state | RHEN | Convert from permanent service to RHEN-owned scheduled job |
| rhen-research-scheduler | IREN | Replace with canonical IREN scheduler |
| rhen-crypto-edge-discovery | GRAEN | Fold into GRAEN research runtime |
| iren-executor | IREN | Merge into explicit IREN control/executor boundary |
| graen | GRAEN | Preserve as GRAEN runtime |
| graen-research-executor | GRAEN | Consolidate after behavior parity is proven |

## Legacy Supabase responsibilities

| Responsibility | Foundation v2 destination |
| --- | --- |
| trading-ingest | RHEN API/direct private PostgreSQL write path |
| trading-reconcile | RHEN reconciliation worker |
| trading-public-feed | Cloudflare public API -> read-only ANEVUM/RHEN API |
| trading-report-read | IREN/RHEN report API |
| scheduler-gateway | IREN scheduler + PostgreSQL |
| iren-command | IREN API |
| iren-slack | IREN notification adapter / Slack webhook |
| graen-gateway | GRAEN API on Railway private network |
| research-agent-gateway | GRAEN/IREN API |
| agent-support-gateway | shared internal API only if still required |
| command-operator | IREN protected Command API |
| Supabase Auth | Cloudflare Access for private Command |
| iren-mobile-registry | OBSOLETE; do not migrate |
| release-delivery-test | re-evaluate; no automatic migration |

## Migration rule

Do not delete or disable a legacy path until its replacement has:
- staging verification;
- durable persistence verification;
- parity/reconciliation evidence;
- rollback procedure;
- explicit production cutover record.
