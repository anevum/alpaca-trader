# Code change map

## `anevum/rhen`

### New/isolated real-time market modules

Prefer a focused package such as `app/market_data/` (adapt names to current main rather than duplicating existing abstractions):

1. `stream_manager.py`
   - Alpaca market WebSocket lifecycle;
   - auth/subscription;
   - reconnect/backoff;
   - entitlement/symbol-capacity validation;
   - feed health.

2. `feed_router.py`
   - map session + data tier to intended feed;
   - verify actual feed/capability;
   - produce fail-closed capability state.

3. `stream_state.py`
   - normalized events;
   - per-symbol atomic market state;
   - source/receive timestamps;
   - out-of-order handling;
   - dirty-symbol tracking.

4. `realtime_features.py`
   - incremental spread/activity/return/forming-bar state;
   - trigger expensive strategy evaluation only when needed.

5. `rejection_engine.py`
   - canonical reason taxonomy;
   - terminal classification per evaluation;
   - minute/session summaries.

6. `stream_recovery.py`
   - REST bootstrap;
   - historical warm-start;
   - restart/reconnect reconstruction;
   - data-gap handling.

Do not create another Railway service.

### Broker update stream

Extend the existing Alpaca broker adapter/execution integration to consume `trade_updates` and reconcile order/fill/cancel/rejection state immediately. Never allow market quote backpressure to drop broker lifecycle events.

### Visual Intelligence modules

Prefer isolated read-only projection modules such as `app/command_visuals/`:

- `visual_projector.py` - canonical state to typed visual deltas;
- `series_buffers.py` - bounded append/patch/reset ring buffers;
- `forecast_projection.py` - versioned NOSTRA forecast contract and expiry;
- `execution_projection.py` - canonical order/fill/position markers;
- `performance_projection.py` - equity/drawdown/exposure/session series;
- `replay_projection.py` - VELUM replay-time visual frames.

These modules have no broker-write imports/authority.

### Adaptive modules

Retain original 4.4 focused modules:

- `app/adaptive_policy.py`
- `app/capital_governor.py`
- `app/nostra/runtime_regime.py`

### Existing files to extend

`app/config.py`
- market-data tier/stream flags;
- Command publish cadence;
- queue/lag thresholds;
- adaptive flags;
- impossible-combination validation;
- protected configuration identity.

Existing extended-equity/session module(s)
- route through canonical SessionContext/feed capability;
- keep session-specific execution authorization independent;
- remove primary dependence on interval polling once equivalence gate passes.

Existing strategy/discovery module(s)
- consume immutable current market snapshot / completed bars;
- preserve 4.3 signal semantics initially;
- attach rejection reasons/evaluable counters.

`app/persistence.py`
- bounded stream health/session summaries;
- completed-bar/warm-start checkpoints;
- rejection summaries;
- adaptive policy/regime observations;
- candidate/order/fill provenance.

`app/execution.py`
- immutable market/policy snapshot per decision;
- stream-sourced freshness validation;
- broker update reconciliation;
- preserve open-position management on controller/stream failure.

`app/risk.py`
- stream/feed health entry veto;
- keep all existing hard limits authoritative;
- never allow adaptive or session code to bypass them.

`app/main.py`
- protected Command live WebSocket;
- REST snapshot remains available;
- expose no secrets/internal prompts.

### Tests to add

- `tests/test_market_stream_manager.py`
- `tests/test_market_feed_router.py`
- `tests/test_market_stream_state.py`
- `tests/test_market_stream_recovery.py`
- `tests/test_rejection_engine.py`
- `tests/test_broker_trade_updates.py`
- `tests/test_command_live_stream.py`
- `tests/test_command_visual_projector.py`
- `tests/test_visual_series_buffers.py`
- `tests/test_forecast_visual_contract.py`
- `tests/test_replay_visual_contract.py`
- original adaptive policy/capital/NOSTRA tests.

## `anevum/anevum-web`

Extend existing Command V4; do not create a parallel dashboard.

Likely additions/changes:

- `src/hooks/useCommandLiveStream.ts` — one protected socket, reconnect, generation/sequence handling;
- `src/lib/command-live-events.ts` — typed snapshot/delta/critical envelopes;
- `src/components/CommandTradingLanes.tsx` — stream/feed/session and current authority;
- `src/components/CommandDiscoveryDeck.tsx` — live scanner + rejection/candidate rates;
- `src/components/CommandResearchLab.tsx` — extended-session and adaptive validation evidence;
- `src/components/CommandReviewDeck.tsx` — promotion blockers/protected actions;
- `src/components/CommandSystemMonitor.tsx` — socket/feed/recovery health;
- graph components — append sparse real points, no fabricated flatlines;
- tests for reconnect/stale/generation handling.


### Frontend Visual Intelligence additions

Suggested components/hooks, adapted to existing code rather than duplicated:

- `src/lib/visual-provenance.ts`
- `src/lib/visual-series.ts`
- `src/components/charts/RealtimeCandlestickChart.tsx`
- `src/components/charts/MiniSparkline.tsx`
- `src/components/charts/ForecastBand.tsx`
- `src/components/charts/ExecutionOverlay.tsx`
- `src/components/charts/FeedHealthRibbon.tsx`
- `src/components/charts/EquityDrawdownChart.tsx`
- `src/components/charts/RegimeProbabilityChart.tsx`
- `src/components/CommandLiveMarket.tsx`
- `src/components/CommandSymbolDetail.tsx`
- `src/components/CommandForecast.tsx`
- `src/components/CommandPerformance.tsx`
- `src/components/CommandReplay.tsx`

Use one charting engine when practical. Do not add multiple redundant chart libraries. All components consume the typed Command visual schema, not raw broker responses.
