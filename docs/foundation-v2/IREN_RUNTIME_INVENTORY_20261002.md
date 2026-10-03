# IREN provider inventory recovery — 2026-10-02

Inspected canonical main `c753f551ed83caa5e587a8bf497c898b6440473c` and Railway production configuration before changing code. The executor runs from main and remains alive: /health is 200 while authenticated /v1/evidence/runtime-inventory repeatedly returns 503.

The affected request is GET https://api.github.com/repos/anevum/alpaca-trader/commits/1663c5ab4516df890cdea70929a9d14275bfc7d6/status. Both pinned service contexts (RHEN - rhen-velum and RHEN - rhen-crypto-edge-discovery) use this request. The status records exist when read through the GitHub connector; this does not prove the executor credential has access. The old route suppresses the provider's HTTP cause. Production diagnosis must use the new bounded per-service diagnostics, not infer that the token is valid from a different connection.

Provider HTTP errors, timeouts, invalid responses and missing/ambiguous/invalid deployment statuses now leave the affected service explicitly unverified with no deployment. Sibling records survive. A successful response is shared only within the current observation. Each lookup has a four-second deadline so two failures fit within the controller's twelve-second read timeout.

HTTP 200 means the authenticated inventory envelope was produced. It does not mean the evidence is healthy. The envelope exposes complete plus verified/reason on every service, and warning logs retain the failure and exact request. Missing credentials and disallowed repository configuration also produce explicit unverified records without a request. Authentication remains mandatory. No fallback credential, fabricated deployment identity or stale response is used.

Regression coverage includes partial and total failure, credential/configuration failure, provider 401/403/404/429/503, transport errors, hard deadlines, malformed responses, status ambiguity, log redaction and topology refusal to use unverified evidence. The existing runtime evidence suite is now also named in the explicit IREN CI step. The full staging suite already discovers it.

Before this change, IREN production logs already reported runtime_inventory_complete=true from runtime self-reported identities, while state remained DEGRADED with workflow.rhen.research.daily and workflow.rhen.session_close incidents. Those workflow failures are separate from this provider endpoint defect and must not be cleared or relabeled by this repair.

The first CI run also exposed an existing opportunity-test fixture that used the wall clock: at 00:07 UTC, its eleven synthetic bars spanned midnight and the latest-session filter correctly retained only seven bars (six returns). The fixture now uses a fixed intraday timestamp; all assertions and production opportunity/trading logic are unchanged.

Deployment and post-merge verification results will be recorded in the PR.
