# RHEN v3 notification and icon language

Status: CANONICAL
Date: 2026-10-05

## Identity hierarchy

ANEVUM is the company identity.

RHEN is the single product and production runtime identity.

The old IREN, GRAEN, VELUM, and NOSTRA names remain accepted only as compatibility keys while the runtime migration is completed. New operator-facing messages should identify the RHEN module instead:

- Execution
- Control
- Research
- Replay
- Forecast
- Core
- Worker
- Command

## Slack notification format

New runtime messages use:

`:rhen: <semantic emoji> *RHEN // <MODULE> // <EVENT>*`

ANEVUM company/release messages retain the `:anevum:` brand prefix.

The semantic vocabulary is intentionally small:

- 🔵 active, online, started, market-session transition
- ✅ healthy, safe, recovered, completed, validated, passed
- ⚠️ warning, degraded, attention required
- 🔴 failed, error, critical
- ⏳ waiting, queued, stale, pending
- 🛡️ risk, protection, blocked, breaker, stop
- ◇ evidence, persistence, telemetry, data
- ↗️ execution
- 🎛️ control
- 🧪 research
- ↺ replay
- 🔭 forecast
- 🗄️ core/store
- ⚙️ worker
- 🖥️ command

State semantics such as active, success, warning, critical, waiting, and risk take precedence over module identity. Otherwise an explicit RHEN module header selects the module marker.

## Retired notification emoji families

Do not add new uses of the old subsystem-specific custom emoji families such as:

- `:iren_*`
- `:graen_*`
- `:nostra_*`
- `:velum_*`
- action-specific RHEN variants such as `:rhen_buy:`, `:rhen_risk:`, and `:rhen_scan:`

Existing prefixed messages remain idempotent during migration so old queued content is not double-branded.

## Visual rule

Identity color and health color are separate concepts.

RHEN and its module icons use the RHEN blue/cyan family. Green, amber, and red are reserved for runtime state and severity, not for distinguishing modules.
