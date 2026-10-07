# RHEN 4.4 Extended 24/5 Runtime

## Objective

Treat U.S. equity trading as four materially different regimes instead of one generic "extended" session:

```text
PREMARKET    04:00-09:30 ET
REGULAR      09:30-16:00 ET
AFTER_HOURS  16:00-20:00 ET
OVERNIGHT    20:00-04:00 ET
```

All four share one RHEN risk/ledger/authority system, but feed capability, execution eligibility, thresholds, signal families, and promotion state are session-specific.

## Capability matrix

### Current/free-resource mode

| Window ET | Data path | Real-time quality | RHEN 4.4 default authority |
|---|---|---|---|
| 04:00-08:00 | No complete execution-grade free equities feed | insufficient for full-market live execution | research/blocked |
| 08:00-09:30 | IEX WebSocket | real-time single venue | observe/shadow until validated |
| 09:30-16:00 | IEX WebSocket | real-time single venue | preserve existing 4.3 live authority |
| 16:00-17:00 | IEX WebSocket | real-time single venue | observe/shadow until validated |
| 17:00-20:00 | IEX no longer supplies fresh session data | structurally stale | blocked |
| 20:00-04:00 | Alpaca `overnight` | real-time indicative quotes; delayed trade information | observe/shadow only |

The runtime must express these states honestly. A session can be "market open" while RHEN is `DATA_CAPABILITY_BLOCKED` for live entry.

### Future paid-data mode

| Window ET | Feed | Intended capability |
|---|---|---|
| 04:00-20:00 | SIP | consolidated real-time U.S. equity market |
| 20:00-04:00 | BOATS | real-time overnight market data |

Paid capability is an adapter/entitlement change, not a second trading architecture.

## SessionRouter contract

The router produces an immutable `SessionContext`:

```text
session
session_started_at
session_ends_at
market_calendar_status
configured_feed
actual_feed
feed_capability
execution_policy
strategy_namespace
risk_namespace
universe_namespace
```

`execution_policy` is one of:

- `LIVE_ALLOWED_BY_RELEASE`;
- `LIMITED_LIVE_GATED`;
- `SHADOW_ONLY`;
- `RESEARCH_ONLY`;
- `DATA_CAPABILITY_BLOCKED`;
- `MARKET_CLOSED`.

The router does not grant execution by itself. Final authority is the intersection of session policy, release gate, data health, asset eligibility, account/risk state, and operator-protected configuration.

## Universe

Keep the 24-symbol configured universe as the initial research/streaming universe. Do not equate configured with executable.

Pipeline:

```text
configured
 -> session eligible
 -> feed-covered
 -> fresh/evaluable
 -> liquid/spread acceptable
 -> signal qualified
 -> risk qualified
 -> executable
```

Persist counts at every stage.

## Overnight-specific rules

Overnight is not "after-hours continued."

Required behavior:

- validate `overnight_tradable` for each asset before the session;
- refresh eligibility near the broker's recommended pre-session sync window;
- honor `overnight_halted`;
- limit orders only;
- treat indicative quote provenance explicitly;
- never substitute delayed trades as if they were real-time trade confirmation;
- use session-specific liquidity/spread thresholds;
- bootstrap enough historical/rolling state after restart before evaluation;
- remain `OBSERVING/SHADOW` in 4.4 until promotion evidence passes.

## Premarket / after-hours rules

With Basic data, live extended execution is only even eligible where IEX is supplying fresh events. That capability does not by itself authorize trading.

RHEN must automatically block entry when:

- feed does not cover the current window;
- quote/bar age exceeds gate;
- feed has stopped updating;
- actual feed differs from expected capability;
- subscription coverage is incomplete;
- required asset/session eligibility is unknown.

## Strategy namespaces

Initial release retains the 4.3 production strategy as the only live champion. Extended sessions get separate namespaces for research and shadow evidence:

```text
regular/<strategy_version>
premarket/<candidate_version>
after_hours/<candidate_version>
overnight/<candidate_version>
```

No result from one session automatically promotes another.

## Data-tier upgrade economics

Algo Trader Plus is currently a $99/month capability upgrade. 4.4 must not depend on purchasing it. The package is explicitly designed so the paid upgrade changes feed entitlement and validation scope rather than architecture.

Before paying for the feed, RHEN should establish from free/shadow evidence that:

- the target session produces enough economically viable opportunities;
- candidate frequency is not simply zero after valid evaluation;
- spread/slippage assumptions leave positive expected net edge;
- the projected opportunity value plausibly exceeds subscription cost and additional risk.
