# RHEN Agent Support Layer v1 activation

The deterministic support service uses the existing canonical telemetry and
report tables through `agent-support-gateway`. That Edge Function returns only
bounded release identity, report coverage and freshness, telemetry cutoff,
duplicate counts and open support alerts. Its read token cannot write. A
different write token accepts only validated OPEN/RESOLVED support-alert
actions and invokes `private.rhen_agent_support_apply_actions`; it has no
trading table mutation route. Neither token is a trading-ingest token.

The service reads the Research Agent's existing sanitized public readiness
and the pre-open service's health/status. It uses the pinned XNYS exchange
calendar for sessions since the first canonical daily report. It preserves
canonical blocker, limitation, monitor and waiting-requirement distinctions.
The current `WAITING` state is a warning, not a critical alert.

Service variables (names only):

- `RHEN_SUPPORT_GATEWAY_URL`
- `RHEN_SUPPORT_READ_TOKEN`
- `RHEN_SUPPORT_WRITE_TOKEN`
- `RHEN_SUPPORT_OPERATOR_TOKEN`
- `RHEN_SUPPORT_RESEARCH_URL`
- `RHEN_SUPPORT_PREOPEN_URL`
- `RHEN_SUPPORT_SCHEDULE_ENABLED` (initially `false`)

The operator-authenticated `POST /v1/run` accepts a fresh, bounded Railway
status/config observation for a manual dry run or controlled persisted run.
The observation is validated against the maintained service ID, source,
start-command and scheduler cron map. Domains and historic service labels do
not assign roles. No shadow/comparison service is inferred.

Unattended hourly checks require `RHEN_SUPPORT_OBSERVER_URL` and
`RHEN_SUPPORT_OBSERVER_READ_TOKEN` to point to a separately scoped, read-only
Railway observation service. The support service refuses to enable scheduling
without that boundary. A Railway project deployment token, trading ingestion
token, database URL, broker credential or model key must not be supplied to
the support service.

Use the manual sequence: dry run without persistence; inspect proposed
transitions; run one persisted evaluation; verify production support-alert
rows; repeat for stable keys and timestamps; test resolution against a
simulated cleared condition without changing canonical evidence. Enable the
hourly schedule only after the read-only observer and those checks are
verified. Coordinator and Verifier remain contract-only.
