# RHEN 4.4 architecture

## Objective

RHEN 4.4 creates a real-time, session-aware market-state fabric, projects that canonical state into a truthful Visual Intelligence Layer, and places bounded Adaptive Policy Control above the trading core. The result may react to current evidence faster and more transparently while preserving deterministic execution, explainability, existing hard risk boundaries, and a clean 4.3 rollback path.

## Runtime flow

```text
Alpaca market-data WebSocket
        |
        v
Real-Time Market Fabric
  feed/session router
  stream health/freshness
  per-symbol market state
  incremental features
  rejection/candidate state
        |
        +--> RHEN 4.3 discovery/opportunity engine
        |
        +--> NOSTRA live state vector + regime probabilities
                         |
                         v
                Adaptive Policy Controller
                  |      |       |
                  |      |       +--> Strategy Health / evidence veto
                  |      +----------> session / execution economics
                  +-----------------> account drawdown / risk state
                         |
                         v
                  Effective Policy Snapshot
                         |
                 Capital Governor
                         |
                         v
             existing RHEN qualification
                         |
             existing sizing + risk gates
                         |
                    existing execution
                         |
                         v
                    Alpaca broker
                         |
                 trade_updates WebSocket
                         |
                         v
              canonical trading ledger
                         |
                         +--> IREN/system state
                         |
                         v
                Visual State Projector
                         |
                Command live WebSocket
                         |
          candles/scanner/forecast/replay
```

## Market-data authority

Market data has capability, health, and execution-eligibility states independent of whether a session is technically open.

A session cannot grant live entry unless all are true:

```text
release authority
AND session authority
AND configured feed entitlement
AND actual feed matches expected feed
AND stream healthy
AND source data fresh
AND symbol eligible
AND strategy/policy promoted
AND risk/reconciliation healthy
```

Any unknown/degraded input fails toward less authority.

## Event-driven vs visual cadence

The engine consumes accepted market/broker events immediately. Command receives coalesced deltas at roughly 100-250 ms plus immediate critical execution/risk events. UI cadence never controls trading cadence.


## Visual intelligence authority

The visual subsystem has zero broker-write authority. It consumes canonical state and may not create execution-critical values.

Every dynamic visual is classified as OBSERVED, DERIVED, FORECAST, or OPERATIONAL. Forecasts carry issue time, feature-as-of time, horizon, model/methodology version, expiry, and uncertainty state. Missing data remains visibly missing. Broker execution markers require canonical ledger evidence.

Frontend smoothing may animate between two known rendered states but may not create synthetic quotes, candles, volume, fills, or model forecasts.

## Adaptive-policy authority

### Automatic after profile promotion

The runtime may:

- recompute NOSTRA market state;
- choose among approved policy profiles;
- reduce risk immediately;
- alter strategy thresholds, exit behavior, and capital expression only within the selected profile's approved envelope;
- return to baseline/fallback state when evidence is stale or ambiguous;
- record every policy transition and effective value;
- manage all positions through existing risk/execution machinery.

### Protected / hard-disabled

The runtime may not automatically:

- add a new policy profile;
- widen a parameter range beyond the approved library;
- increase any hard account risk ceiling;
- enable margin/leverage;
- enable shorting;
- enable options broker writes;
- reintroduce crypto;
- change broker-write authority;
- rewrite strategy source code;
- weaken validation methodology;
- promote failed/under-sampled research;
- treat stale/indicative/delayed data as execution-grade by configuration trick.

## Hard ceiling model

Adaptive effective limits remain subordinate to Settings/risk hard limits.

```text
effective_limit <= hard_limit
```

## Research flow

```text
canonical stream + trading evidence
      |
      v
GRAEN / Research Agent
      |
      v
bounded candidate profile / session strategy proposal
      |
      v
VELUM replay + ASC counterfactual tests
      |
      v
next-session fixed-vs-adaptive / session shadow
      |
      v
GRAEN controls + untouched holdout
      |
      v
protected promotion contract
      |
      v
approved policy/session release
```

## Failure behavior

- market stream disconnected/stale -> no new live entries for affected scope;
- feed entitlement/capability mismatch -> fail closed and alert;
- restart -> reconstruct rolling state and remain warming until ready;
- stale NOSTRA state -> `BASELINE_LOCKED` or tighter;
- UNKNOWN/high-uncertainty regime -> no aggressiveness upgrade;
- Strategy Health degraded -> `DEFENSIVE` or `NO_TRADE`;
- evidence integrity unhealthy -> `BASELINE_LOCKED`/`NO_TRADE`;
- reconciliation unsafe -> no new buys;
- controller exception -> exact 4.3 baseline behavior or tighter safety;
- policy library fingerprint mismatch -> adaptive execution disabled;
- invalid effective parameter -> reject policy snapshot and fall back.

## Why this ordering matters

Adaptive control and visual interpretation are only as good as the state they observe. Real-time ingestion, explicit feed capability, persistent warm-start, and rejection telemetry therefore become foundational 4.4 infrastructure rather than a cosmetic Command improvement.
